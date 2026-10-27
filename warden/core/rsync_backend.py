"""rsync backend — local destinations.

Modes:
  - mirror:           rsync --delete (destination always equals source)
  - snapshot:         rsync --link-dest=<latest> (hardlinks for unchanged files)
  - archive:          tar -czf <date>.tar.gz
  - mirror_snapshot:  keep current/ mirror + snapshots/<date>/ history
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from warden.core.config import BackupMode, Destination, RetentionPolicy
from warden.core.retention import (
    snapshot_name,
    prune_local_snapshots,
)


class RsyncError(Exception):
    pass


def _build_exclude_args(excludes: list[str]) -> list[str]:
    """Convert exclude patterns to rsync --exclude args."""
    args = []
    for pattern in excludes:
        args.extend(["--exclude", pattern])
    return args


def _run_rsync(args: list[str], logger=None) -> tuple[int, str, str]:
    """Run rsync and capture output. Returns (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            ["rsync"] + args,
            capture_output=True,
            text=True,
            timeout=None,  # No timeout — backups can take hours
            check=False,
        )
        return result.returncode, result.stdout, result.stderr
    except FileNotFoundError as e:
        raise RsyncError("rsync binary not found") from e
    except subprocess.TimeoutExpired as e:
        raise RsyncError("rsync timed out") from e


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _dir_size(path: Path) -> tuple[int, int]:
    """Return (total_bytes, file_count) of a directory tree."""
    total = 0
    count = 0
    if not path.exists():
        return 0, 0
    for entry in path.rglob("*"):
        if entry.is_file():
            try:
                total += entry.stat().st_size
                count += 1
            except OSError:
                pass
    return total, count


# ---------------------------------------------------------------- mirror
def do_mirror(
    source: str,
    dest_path: str,
    excludes: list[str],
    logger=None,
) -> dict:
    """Mirror mode: rsync --delete. Destination equals source after.

    Returns: {"files": int, "size_bytes": int, "rsync_output": str}
    """
    src = str(source).rstrip("/") + "/"
    dst = Path(dest_path)
    _ensure_dir(dst)

    args = [
        "-a",  # archive mode
        "--delete",
        "--delete-excluded",
        "--stats",
        "--human-readable",
    ]
    args += _build_exclude_args(excludes)
    args += [str(src), str(dst) + "/"]

    if logger:
        logger.info(f"rsync mirror: {src} -> {dst}")
    rc, stdout, stderr = _run_rsync(args, logger)
    if rc not in (0, 23, 24):  # 23=partial transfer, 24=vanished source files
        msg = f"rsync failed (rc={rc}): {stderr.strip()[:500]}"
        if logger:
            logger.error(msg)
        raise RsyncError(msg)
    if logger and stderr:
        logger.debug(f"rsync stderr: {stderr[:500]}")

    size, count = _dir_size(dst)
    return {
        "files": count,
        "size_bytes": size,
        "rsync_output": stdout,
    }


# ---------------------------------------------------------------- snapshot
def do_snapshot(
    source: str,
    dest_path: str,
    excludes: list[str],
    logger=None,
) -> dict:
    """Snapshot mode: dated directory with hardlinks to previous snapshot.

    Layout:
      <dest_path>/
        2026-10-27T03-00/
        2026-10-26T03-00/
        ...

    Uses rsync --link-dest=<previous> so unchanged files are hardlinks
    (zero extra space).
    """
    src = str(source).rstrip("/") + "/"
    dst_base = Path(dest_path)
    _ensure_dir(dst_base)

    # Find latest existing snapshot
    existing = sorted(
        [p for p in dst_base.iterdir() if p.is_dir() and p.name[0].isdigit()],
        key=lambda p: p.name,
    )
    link_dest = existing[-1] if existing else None

    snap_name = snapshot_name()
    snap_path = dst_base / snap_name
    if snap_path.exists():
        # Same-minute collision — append a counter
        i = 2
        while (dst_base / f"{snap_name}-{i:02d}").exists():
            i += 1
        snap_name = f"{snap_name}-{i:02d}"
        snap_path = dst_base / snap_name

    args = [
        "-a",
        "--stats",
        "--human-readable",
    ]
    if link_dest is not None:
        args.extend(["--link-dest", str(link_dest)])
    args += _build_exclude_args(excludes)
    args += [str(src), str(snap_path) + "/"]

    if logger:
        logger.info(f"rsync snapshot: {src} -> {snap_path}")
        if link_dest:
            logger.info(f"link-dest: {link_dest}")
    rc, stdout, stderr = _run_rsync(args, logger)
    if rc not in (0, 23, 24):
        msg = f"rsync snapshot failed (rc={rc}): {stderr.strip()[:500]}"
        if logger:
            logger.error(msg)
        raise RsyncError(msg)

    size, count = _dir_size(snap_path)
    return {
        "files": count,
        "size_bytes": size,
        "snapshot_name": snap_name,
        "rsync_output": stdout,
    }


# ---------------------------------------------------------------- archive
def do_archive(
    source: str,
    dest_path: str,
    excludes: list[str],
    logger=None,
) -> dict:
    """Archive mode: tar -czf <date>.tar.gz of the source."""
    dst = Path(dest_path)
    _ensure_dir(dst)

    arch_name = f"{snapshot_name()}.tar.gz"
    arch_path = dst / arch_name
    if arch_path.exists():
        i = 2
        while (dst / f"{snapshot_name()}-{i:02d}.tar.gz").exists():
            i += 1
        arch_name = f"{snapshot_name()}-{i:02d}.tar.gz"
        arch_path = dst / arch_name

    # Build tar command
    src_path = Path(source)
    tar_args = ["tar", "-czf", str(arch_path), "-C", str(src_path.parent)]
    for pattern in excludes:
        tar_args.extend(["--exclude", pattern])
    tar_args.append(src_path.name)

    if logger:
        logger.info(f"tar archive: {source} -> {arch_path}")
    try:
        result = subprocess.run(
            tar_args, capture_output=True, text=True, timeout=None, check=False
        )
    except FileNotFoundError as e:
        raise RsyncError("tar binary not found") from e

    if result.returncode != 0:
        msg = f"tar failed (rc={result.returncode}): {result.stderr.strip()[:500]}"
        if logger:
            logger.error(msg)
        raise RsyncError(msg)

    size = arch_path.stat().st_size if arch_path.exists() else 0
    return {
        "files": 0,  # tar doesn't report easily
        "size_bytes": size,
        "archive_name": arch_name,
        "rsync_output": result.stdout,
    }


# ---------------------------------------------------------------- mirror+snapshot
def do_mirror_snapshot(
    source: str,
    dest_path: str,
    excludes: list[str],
    logger=None,
) -> dict:
    """Combined: maintain current/ mirror + snapshots/<date>/ history.

    Layout:
      <dest_path>/
        current/        <- always equals source (rsync --delete)
        snapshots/
          2026-10-27T03-00/  <- hardlink copy of previous current/
    """
    dst = Path(dest_path)
    _ensure_dir(dst)
    current = dst / "current"
    snapshots_dir = dst / "snapshots"
    _ensure_dir(snapshots_dir)

    # 1. Before updating current/, snapshot the existing current/ if it exists
    snap_name = snapshot_name()
    snap_path = snapshots_dir / snap_name
    if snap_path.exists():
        i = 2
        while (snapshots_dir / f"{snap_name}-{i:02d}").exists():
            i += 1
        snap_name = f"{snap_name}-{i:02d}"
        snap_path = snapshots_dir / snap_name

    if current.exists() and any(current.iterdir()):
        if logger:
            logger.info(f"snapshotting current state -> {snap_path}")
        # Hardlink copy of current/ to snapshots/<date>/
        cp_args = ["cp", "-al", str(current), str(snap_path)]
        try:
            result = subprocess.run(
                cp_args, capture_output=True, text=True, timeout=None, check=False
            )
            if result.returncode != 0 and logger:
                logger.warning(
                    f"snapshot copy returned {result.returncode}: "
                    f"{result.stderr.strip()[:200]}"
                )
        except FileNotFoundError as e:
            raise RsyncError("cp binary not found") from e

    # 2. Now mirror source -> current/
    mirror_result = do_mirror(source, str(current), excludes, logger)

    size, count = _dir_size(current)
    return {
        "files": count,
        "size_bytes": size,
        "snapshot_name": snap_name,
        "rsync_output": mirror_result["rsync_output"],
    }


# ---------------------------------------------------------------- retention
def apply_retention_local(
    dest_path: str,
    mode: BackupMode,
    policy: RetentionPolicy,
    logger=None,
) -> list[str]:
    """Apply retention policy to a local destination.

    Returns list of pruned snapshot/archive names.
    """
    if policy.mode.value == "none":
        return []

    base = Path(dest_path)
    if mode == BackupMode.SNAPSHOT:
        return prune_local_snapshots(base, policy)
    if mode == BackupMode.MIRROR_SNAPSHOT:
        return prune_local_snapshots(base / "snapshots", policy)
    if mode == BackupMode.ARCHIVE:
        # Prune old .tar.gz files
        from warden.core.retention import list_snapshots, select_to_prune
        archives = []
        if base.exists():
            for entry in base.iterdir():
                if entry.is_file() and entry.name.endswith(".tar.gz"):
                    stem = entry.name[:-7]
                    from warden.core.retention import parse_snapshot_name
                    dt = parse_snapshot_name(stem)
                    if dt:
                        archives.append((entry.name, dt))
        archives.sort(key=lambda x: x[1])
        to_prune = select_to_prune(archives, policy)
        pruned = []
        for name in to_prune:
            try:
                (base / name).unlink()
                pruned.append(name)
            except OSError:
                pass
        return pruned
    # MIRROR: no snapshots to prune
    return []


# ---------------------------------------------------------------- dispatcher
def run_local_backup(
    destination: Destination,
    source: str,
    excludes: list[str],
    logger=None,
) -> dict:
    """Dispatch to the right mode for a local destination."""
    mode = destination.mode
    if mode == BackupMode.MIRROR:
        result = do_mirror(source, destination.path, excludes, logger)
    elif mode == BackupMode.SNAPSHOT:
        result = do_snapshot(source, destination.path, excludes, logger)
    elif mode == BackupMode.ARCHIVE:
        result = do_archive(source, destination.path, excludes, logger)
    elif mode == BackupMode.MIRROR_SNAPSHOT:
        result = do_mirror_snapshot(source, destination.path, excludes, logger)
    else:
        raise RsyncError(f"Unknown backup mode: {mode}")
    return result


def list_local_snapshots(destination: Destination) -> list[str]:
    """List available snapshots/archives for restore UI."""
    from warden.core.retention import list_snapshots
    base = Path(destination.path)
    if destination.mode == BackupMode.SNAPSHOT:
        return [name for name, _ in list_snapshots(base)]
    if destination.mode == BackupMode.MIRROR_SNAPSHOT:
        return [name for name, _ in list_snapshots(base / "snapshots")]
    if destination.mode == BackupMode.ARCHIVE:
        if not base.exists():
            return []
        return sorted(
            entry.name[:-7]
            for entry in base.iterdir()
            if entry.is_file() and entry.name.endswith(".tar.gz")
        )
    # MIRROR: no snapshots
    return []
