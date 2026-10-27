"""Backup orchestration — the main entry point for running a backup job.

This is called by both the CLI (for systemd timers) and the GUI.
"""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from warden.core.config import (
    Config,
    Destination,
    DestinationType,
    JobConfig,
)
from warden.core.history import RunRecord, append_run, update_state_after_run
from warden.core.locks import DestinationLock, LockTimeoutError
from warden.core.log import get_logger, close_logger
from warden.core.mounts import is_mounted
from warden.core.notify import send_notification
from warden.core.rclone_backend import (
    run_remote_backup,
    apply_retention_remote,
    RcloneError,
)
from warden.core.rsync_backend import (
    run_local_backup,
    apply_retention_local,
    RsyncError,
)
from warden.core.retention import RetentionMode


# ---------------------------------------------------------------- result
@dataclass
class DestinationResult:
    destination_id: str
    status: str  # "ok" / "skipped" / "failed"
    error: str = ""
    files: int = 0
    size_bytes: int = 0
    duration_seconds: float = 0.0


@dataclass
class BackupResult:
    job: str
    started_at: datetime
    finished_at: datetime
    duration_seconds: float
    status: str  # "success" / "partial" / "failure"
    destinations: list[DestinationResult] = field(default_factory=list)
    error: str = ""

    @property
    def destinations_ok(self) -> list[str]:
        return [d.destination_id for d in self.destinations if d.status == "ok"]

    @property
    def destinations_skipped(self) -> list[str]:
        return [d.destination_id for d in self.destinations if d.status == "skipped"]

    @property
    def destinations_failed(self) -> list[str]:
        return [d.destination_id for d in self.destinations if d.status == "failed"]

    @property
    def total_size_bytes(self) -> int:
        return sum(d.size_bytes for d in self.destinations)

    @property
    def total_files(self) -> int:
        return sum(d.files for d in self.destinations)


# ---------------------------------------------------------------- helpers
def _human_size(n: int) -> str:
    """Convert bytes to human-readable string."""
    if n < 1024:
        return f"{n} B"
    for unit in ("KB", "MB", "GB", "TB"):
        n /= 1024
        if n < 1024:
            return f"{n:.1f} {unit}"
    return f"{n:.1f} PB"


def _human_duration(seconds: float) -> str:
    """Convert seconds to a friendly duration string."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s"
    hours = int(minutes // 60)
    mins = minutes % 60
    return f"{hours}h{mins:02d}m"


# ---------------------------------------------------------------- main
def run_backup(
    job: JobConfig,
    config: Config,
    *,
    only_destinations: list[str] | None = None,
    verbose: bool = False,
) -> BackupResult:
    """Run a backup job.

    Args:
        job: The job configuration
        config: Global app configuration
        only_destinations: Optional list of destination IDs to run
            (None = all destinations)
        verbose: If True, debug-level logging

    Returns:
        BackupResult with per-destination status.
    """
    started_at = datetime.now()
    logger = get_logger(job.name, verbose=verbose)
    logger.info(f"=== Backup start: {job.name} ===")
    logger.info(f"Source: {job.source}")
    logger.info(
        f"Destinations: {len(job.destinations)} "
        f"({', '.join(d.id for d in job.destinations)})"
    )

    results: list[DestinationResult] = []
    destinations_to_run = job.destinations
    if only_destinations:
        destinations_to_run = [
            d for d in job.destinations if d.id in only_destinations
        ]
        logger.info(f"Filtered to destinations: {[d.id for d in destinations_to_run]}")

    for dest in destinations_to_run:
        result = _run_destination(dest, job, config, logger)
        results.append(result)

    finished_at = datetime.now()
    duration = (finished_at - started_at).total_seconds()

    # Determine overall status
    has_failed = any(r.status == "failed" for r in results)
    has_skipped = any(r.status == "skipped" for r in results)
    has_ok = any(r.status == "ok" for r in results)
    if has_failed or not has_ok:
        status = "failure"
    elif has_skipped:
        status = "partial"
    else:
        status = "success"

    logger.info(
        f"=== Backup end: {job.name} — {status} "
        f"(ok={len([r for r in results if r.status == 'ok'])}, "
        f"skipped={len([r for r in results if r.status == 'skipped'])}, "
        f"failed={len([r for r in results if r.status == 'failed'])}, "
        f"duration={_human_duration(duration)}) ==="
    )

    # Build summary for notification
    summary_parts = []
    if results:
        ok = len([r for r in results if r.status == "ok"])
        skipped = len([r for r in results if r.status == "skipped"])
        failed = len([r for r in results if r.status == "failed"])
        summary_parts.append(
            f"{ok} OK, {skipped} skipped, {failed} failed"
        )
    if config.behavior.include_size_in_log:
        summary_parts.append(f"{_human_size(sum(r.size_bytes for r in results))}")
    summary_parts.append(_human_duration(duration))
    summary = " • ".join(summary_parts)

    # Build details for skipped/failed
    details_lines = []
    for r in results:
        if r.status == "skipped":
            details_lines.append(f"⏭ {r.destination_id}: skipped")
            if r.error:
                details_lines.append(f"   reason: {r.error}")
        elif r.status == "failed":
            details_lines.append(f"❌ {r.destination_id}: failed")
            if r.error:
                details_lines.append(f"   error: {r.error}")
    details = "\n".join(details_lines)

    # Send Telegram notification
    try:
        send_notification(
            config.telegram, job.name, status, summary, details
        )
    except Exception as e:
        logger.warning(f"Telegram notification failed: {e}")

    # Record to history and state
    try:
        record = RunRecord(
            job=job.name,
            started_at=started_at.isoformat(),
            finished_at=finished_at.isoformat(),
            duration_seconds=duration,
            status=status,
            destinations_ok=[r.destination_id for r in results if r.status == "ok"],
            destinations_skipped=[
                r.destination_id for r in results if r.status == "skipped"
            ],
            destinations_failed=[
                r.destination_id for r in results if r.status == "failed"
            ],
            total_size_bytes=sum(r.size_bytes for r in results),
            total_files=sum(r.files for r in results),
            error_message=next(
                (r.error for r in results if r.status == "failed"), ""
            ),
        )
        append_run(record)
        dests_state = {r.destination_id: r.status for r in results}
        update_state_after_run(
            job.name, finished_at, status, duration, dests_state
        )
    except Exception as e:
        logger.warning(f"Failed to record history/state: {e}")

    close_logger(job.name)

    return BackupResult(
        job=job.name,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=duration,
        status=status,
        destinations=results,
        error=next((r.error for r in results if r.status == "failed"), ""),
    )


def _run_destination(
    dest: Destination,
    job: JobConfig,
    config: Config,
    logger,
) -> DestinationResult:
    """Run a single destination. Acquires lock, checks mount, runs backup."""
    dest_start = time.monotonic()
    logger.info(f"--- Destination: {dest.id} ({dest.type.value}, {dest.mode.value}) ---")

    # 1. Mount check (local destinations only)
    if dest.type == DestinationType.LOCAL and dest.mountpoint:
        if config.behavior.verify_mount_before_local and not is_mounted(dest.mountpoint):
            logger.warning(
                f"{dest.id}: mountpoint {dest.mountpoint} not mounted — skipping"
            )
            return DestinationResult(
                destination_id=dest.id,
                status="skipped",
                error=f"mountpoint {dest.mountpoint} not mounted",
                duration_seconds=time.monotonic() - dest_start,
            )

    # 2. Acquire lock
    if config.behavior.flock_per_destination:
        try:
            lock = DestinationLock(
                dest.id,
                timeout_minutes=config.behavior.flock_timeout_minutes,
            )
            lock.acquire()
        except LockTimeoutError as e:
            logger.error(f"{dest.id}: lock timeout — {e}")
            return DestinationResult(
                destination_id=dest.id,
                status="failed",
                error=f"lock timeout: {e}",
                duration_seconds=time.monotonic() - dest_start,
            )
    else:
        lock = None

    # 3. Run backup (inside the lock)
    try:
        if dest.type == DestinationType.LOCAL:
            result_data = run_local_backup(dest, job.source, job.excludes, logger)
        else:  # rclone
            result_data = run_remote_backup(
                dest, job.source, job.excludes, config.rclone, logger
            )

        # 4. Apply retention
        try:
            if dest.type == DestinationType.LOCAL:
                pruned = apply_retention_local(
                    dest.path, dest.mode, dest.retention, logger
                )
            else:
                pruned = apply_retention_remote(
                    dest.remote, dest.mode, dest.retention, config.rclone, logger
                )
            if pruned and logger:
                logger.info(f"{dest.id}: pruned {len(pruned)} old snapshots")
        except Exception as e:
            if logger:
                logger.warning(f"{dest.id}: retention failed: {e}")

        duration = time.monotonic() - dest_start
        logger.info(
            f"{dest.id}: OK — "
            f"files={result_data.get('files', 0)}, "
            f"size={_human_size(result_data.get('size_bytes', 0))}, "
            f"duration={_human_duration(duration)}"
        )
        return DestinationResult(
            destination_id=dest.id,
            status="ok",
            files=result_data.get("files", 0),
            size_bytes=result_data.get("size_bytes", 0),
            duration_seconds=duration,
        )

    except (RsyncError, RcloneError) as e:
        duration = time.monotonic() - dest_start
        logger.error(f"{dest.id}: FAILED — {e}")
        return DestinationResult(
            destination_id=dest.id,
            status="failed",
            error=str(e),
            duration_seconds=duration,
        )
    except Exception as e:
        duration = time.monotonic() - dest_start
        logger.error(f"{dest.id}: UNEXPECTED ERROR — {e}")
        logger.debug(traceback.format_exc())
        return DestinationResult(
            destination_id=dest.id,
            status="failed",
            error=f"unexpected: {e}",
            duration_seconds=duration,
        )
    finally:
        if lock is not None:
            lock.release()


# ---------------------------------------------------------------- restore
def restore_snapshot(
    job: JobConfig,
    destination_id: str,
    snapshot_name: str,
    target_path: str,
    config: Config,
) -> tuple[bool, str]:
    """Restore a snapshot to target_path.

    Returns (success, message).
    """
    dest = next((d for d in job.destinations if d.id == destination_id), None)
    if dest is None:
        return False, f"Destination '{destination_id}' not found in job '{job.name}'"

    target = Path(target_path)
    target.mkdir(parents=True, exist_ok=True)

    if dest.type == DestinationType.LOCAL:
        return _restore_local(dest, snapshot_name, target)
    else:
        return _restore_remote(dest, snapshot_name, target, config)


def _restore_local(
    dest: Destination, snapshot_name: str, target: Path
) -> tuple[bool, str]:
    """Restore a local snapshot."""
    from warden.core.config import BackupMode

    base = Path(dest.path)
    src_path = None

    if dest.mode == BackupMode.SNAPSHOT:
        src_path = base / snapshot_name
    elif dest.mode == BackupMode.MIRROR_SNAPSHOT:
        src_path = base / "snapshots" / snapshot_name
    elif dest.mode == BackupMode.ARCHIVE:
        arch_path = base / f"{snapshot_name}.tar.gz"
        if not arch_path.exists():
            return False, f"Archive not found: {arch_path}"
        import subprocess
        result = subprocess.run(
            ["tar", "-xzf", str(arch_path), "-C", str(target)],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            return False, f"tar extract failed: {result.stderr.strip()[:200]}"
        return True, f"Extracted {arch_path.name} to {target}"
    elif dest.mode == BackupMode.MIRROR:
        return False, "Mirror mode has no snapshots to restore"
    else:
        return False, f"Unsupported mode for restore: {dest.mode}"

    if not src_path or not src_path.exists():
        return False, f"Snapshot not found: {src_path}"

    # Use rsync to copy the snapshot to the target
    import subprocess
    args = [
        "rsync", "-a", "--stats",
        f"{src_path}/", f"{target}/",
    ]
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode not in (0, 23, 24):
        return False, f"rsync restore failed: {result.stderr.strip()[:200]}"
    return True, f"Restored {src_path.name} to {target}"


def _restore_remote(
    dest: Destination, snapshot_name: str, target: Path, config: Config
) -> tuple[bool, str]:
    """Restore a remote snapshot via rclone."""
    from warden.core.config import BackupMode
    from warden.core.rclone_backend import _run_rclone, _normalize_remote

    remote = _normalize_remote(dest.remote)
    if dest.mode == BackupMode.SNAPSHOT:
        src_remote = f"{remote}_snapshots/{snapshot_name}"
    elif dest.mode == BackupMode.MIRROR_SNAPSHOT:
        src_remote = f"{remote}/snapshots/{snapshot_name}"
    elif dest.mode == BackupMode.ARCHIVE:
        # Download tar.gz then extract
        arch_remote = f"{remote}/{snapshot_name}.tar.gz"
        local_arch = target / f"{snapshot_name}.tar.gz"
        rc, _, stderr = _run_rclone(
            ["copyto", arch_remote, str(local_arch)], config.rclone, timeout=600
        )
        if rc != 0:
            return False, f"rclone download failed: {stderr[:200]}"
        import subprocess
        result = subprocess.run(
            ["tar", "-xzf", str(local_arch), "-C", str(target)],
            capture_output=True, text=True, check=False,
        )
        local_arch.unlink(missing_ok=True)
        if result.returncode != 0:
            return False, f"tar extract failed: {result.stderr.strip()[:200]}"
        return True, f"Restored {snapshot_name} to {target}"
    else:
        return False, f"Cannot restore from mode: {dest.mode}"

    rc, _, stderr = _run_rclone(
        ["copy", src_remote, str(target) + "/", "--transfers=4"],
        config.rclone, timeout=600,
    )
    if rc != 0:
        return False, f"rclone restore failed: {stderr[:200]}"
    return True, f"Restored {snapshot_name} to {target}"
