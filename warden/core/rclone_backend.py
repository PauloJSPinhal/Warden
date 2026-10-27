"""rclone backend — Google Drive (and other cloud) destinations.

Uses rclone binary; configured via ~/.config/rclone/rclone.conf.
For Service Account auth, the SA JSON path is in the global config.

Modes (rclone-side):
  - mirror:           rclone sync --drive-use-trash=false
  - snapshot:         rclone sync --backup-dir=snapshots/<date>
  - archive:          upload tar.gz from rsync_backend
  - mirror_snapshot:  keep current/ + snapshots/<date>/ via --backup-dir
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from warden.core.config import BackupMode, Destination, RcloneConfig, RetentionPolicy
from warden.core.retention import snapshot_name, parse_snapshot_name
from warden.core.rsync_backend import do_archive


class RcloneError(Exception):
    pass


def _rclone_binary(config: RcloneConfig) -> str:
    binary = config.binary or "rclone"
    if not shutil.which(binary):
        raise RcloneError(f"rclone binary not found: {binary}")
    return binary


def _run_rclone(
    args: list[str],
    config: RcloneConfig,
    logger=None,
    *,
    timeout: int | None = None,
) -> tuple[int, str, str]:
    """Run rclone and capture output. Returns (returncode, stdout, stderr)."""
    binary = _rclone_binary(config)
    # Add Service Account flag if configured
    env = None
    full_args = [binary, "--stats=10s", "--stats-one-line"] + args
    try:
        result = subprocess.run(
            full_args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return result.returncode, result.stdout, result.stderr
    except FileNotFoundError as e:
        raise RcloneError(f"rclone binary not found: {binary}") from e
    except subprocess.TimeoutExpired as e:
        raise RcloneError("rclone timed out") from e


def _normalize_remote(remote: str) -> str:
    """Ensure remote ends with no trailing slash and has colon."""
    remote = remote.rstrip("/")
    if ":" not in remote:
        remote = remote + ":"
    return remote


# ---------------------------------------------------------------- mirror
def do_mirror(
    source: str,
    remote: str,
    excludes: list[str],
    rclone_config: RcloneConfig,
    logger=None,
) -> dict:
    """rclone sync source -> remote."""
    remote = _normalize_remote(remote)
    args = ["sync", source, remote, "--transfers=4", "--checkers=8"]
    for pattern in excludes:
        args.extend(["--exclude", pattern])

    if logger:
        logger.info(f"rclone sync: {source} -> {remote}")
    rc, stdout, stderr = _run_rclone(args, rclone_config, logger)
    if rc != 0:
        msg = f"rclone sync failed (rc={rc}): {stderr.strip()[:500]}"
        if logger:
            logger.error(msg)
        raise RcloneError(msg)

    return {
        "files": 0,
        "size_bytes": 0,
        "rsync_output": stdout + stderr,
    }


# ---------------------------------------------------------------- snapshot
def do_snapshot(
    source: str,
    remote: str,
    excludes: list[str],
    rclone_config: RcloneConfig,
    logger=None,
) -> dict:
    """rclone sync with --backup-dir for dated snapshots.

    Layout on remote:
      gdrive:backup/documentos/                 <- current (always equals source)
      gdrive:backup/documentos_snapshots/
        2026-10-27T03-00/                       <- previous versions
    """
    remote = _normalize_remote(remote)
    snap_name = snapshot_name()
    backup_dir = f"{remote}_snapshots/{snap_name}"

    args = [
        "sync", source, remote,
        "--backup-dir", backup_dir,
        "--transfers=4", "--checkers=8",
    ]
    for pattern in excludes:
        args.extend(["--exclude", pattern])

    if logger:
        logger.info(f"rclone snapshot: {source} -> {remote}")
        logger.info(f"backup-dir: {backup_dir}")
    rc, stdout, stderr = _run_rclone(args, rclone_config, logger)
    if rc != 0:
        msg = f"rclone snapshot failed (rc={rc}): {stderr.strip()[:500]}"
        if logger:
            logger.error(msg)
        raise RcloneError(msg)

    return {
        "files": 0,
        "size_bytes": 0,
        "snapshot_name": snap_name,
        "rsync_output": stdout + stderr,
    }


# ---------------------------------------------------------------- archive
def do_archive_upload(
    source: str,
    remote: str,
    excludes: list[str],
    rclone_config: RcloneConfig,
    logger=None,
) -> dict:
    """Create a local tar.gz, then rclone copy it to remote."""
    # 1. Create tar.gz in a temp dir
    import tempfile
    with tempfile.TemporaryDirectory(prefix="warden_archive_") as tmpdir:
        arch_result = do_archive(source, tmpdir, excludes, logger)
        arch_path = Path(tmpdir) / arch_result["archive_name"]

        # 2. Upload to remote
        remote = _normalize_remote(remote)
        args = [
            "copyto", str(arch_path), f"{remote}/{arch_result['archive_name']}",
            "--transfers=4",
        ]
        if logger:
            logger.info(f"rclone copy archive -> {remote}")
        rc, stdout, stderr = _run_rclone(args, rclone_config, logger)
        if rc != 0:
            msg = f"rclone copy archive failed (rc={rc}): {stderr.strip()[:500]}"
            if logger:
                logger.error(msg)
            raise RcloneError(msg)

    return {
        "files": 0,
        "size_bytes": arch_result["size_bytes"],
        "archive_name": arch_result["archive_name"],
        "rsync_output": stdout + stderr,
    }


# ---------------------------------------------------------------- mirror+snapshot
def do_mirror_snapshot(
    source: str,
    remote: str,
    excludes: list[str],
    rclone_config: RcloneConfig,
    logger=None,
) -> dict:
    """Combined: current/ mirror + snapshots/<date>/ history on remote.

    Same as snapshot mode but uses a 'current' subfolder on the remote.
    """
    remote = _normalize_remote(remote)
    current_remote = f"{remote}/current"
    snap_name = snapshot_name()
    backup_dir = f"{remote}/snapshots/{snap_name}"

    args = [
        "sync", source, current_remote,
        "--backup-dir", backup_dir,
        "--transfers=4", "--checkers=8",
    ]
    for pattern in excludes:
        args.extend(["--exclude", pattern])

    if logger:
        logger.info(f"rclone mirror+snapshot: {source} -> {current_remote}")
        logger.info(f"backup-dir: {backup_dir}")
    rc, stdout, stderr = _run_rclone(args, rclone_config, logger)
    if rc != 0:
        msg = (
            f"rclone mirror+snapshot failed (rc={rc}): {stderr.strip()[:500]}"
        )
        if logger:
            logger.error(msg)
        raise RcloneError(msg)

    return {
        "files": 0,
        "size_bytes": 0,
        "snapshot_name": snap_name,
        "rsync_output": stdout + stderr,
    }


# ---------------------------------------------------------------- retention
def apply_retention_remote(
    remote: str,
    mode: BackupMode,
    policy: RetentionPolicy,
    rclone_config: RcloneConfig,
    logger=None,
) -> list[str]:
    """Apply retention on the remote."""
    if policy.mode.value == "none":
        return []
    remote = _normalize_remote(remote)

    # Determine the snapshots base on the remote
    if mode == BackupMode.SNAPSHOT:
        snaps_remote = f"{remote}_snapshots"
    elif mode == BackupMode.MIRROR_SNAPSHOT:
        snaps_remote = f"{remote}/snapshots"
    elif mode == BackupMode.ARCHIVE:
        # List tar.gz files on remote and prune old ones
        args = ["lsl", remote, "--max-depth=1"]
        rc, stdout, stderr = _run_rclone(args, rclone_config, logger, timeout=60)
        if rc != 0:
            return []
        archives = []
        for line in stdout.splitlines():
            parts = line.split(maxsplit=5)
            if len(parts) >= 6 and parts[-1].endswith(".tar.gz"):
                name = parts[-1][:-7]
                dt = parse_snapshot_name(name)
                if dt:
                    archives.append((parts[-1], dt))
        archives.sort(key=lambda x: x[1])
        from warden.core.retention import select_to_prune
        to_prune = select_to_prune(archives, policy)
        pruned = []
        for fname in to_prune:
            file_remote = f"{remote}/{fname}"
            rc, _, _ = _run_rclone(
                ["deletefile", file_remote], rclone_config, logger, timeout=60
            )
            if rc == 0:
                pruned.append(fname)
        return pruned

    # For snapshot/mirror_snapshot: list subdirs on remote and prune old
    args = ["lsd", snaps_remote, "--max-depth=1"]
    rc, stdout, stderr = _run_rclone(args, rclone_config, logger, timeout=60)
    if rc != 0:
        if logger:
            logger.warning(f"could not list snapshots on {snaps_remote}: {stderr[:100]}")
        return []

    snaps = []
    for line in stdout.splitlines():
        parts = line.split()
        if parts:
            name = parts[-1]
            dt = parse_snapshot_name(name)
            if dt:
                snaps.append((name, dt))
    snaps.sort(key=lambda x: x[1])

    from warden.core.retention import select_to_prune
    to_prune = select_to_prune(snaps, policy)
    pruned = []
    for name in to_prune:
        path_remote = f"{snaps_remote}/{name}"
        rc, _, _ = _run_rclone(
            ["purge", path_remote], rclone_config, logger, timeout=300
        )
        if rc == 0:
            pruned.append(name)
    return pruned


# ---------------------------------------------------------------- dispatcher
def run_remote_backup(
    destination: Destination,
    source: str,
    excludes: list[str],
    rclone_config: RcloneConfig,
    logger=None,
) -> dict:
    """Dispatch to the right mode for an rclone destination."""
    mode = destination.mode
    if mode == BackupMode.MIRROR:
        return do_mirror(source, destination.remote, excludes, rclone_config, logger)
    if mode == BackupMode.SNAPSHOT:
        return do_snapshot(source, destination.remote, excludes, rclone_config, logger)
    if mode == BackupMode.ARCHIVE:
        return do_archive_upload(
            source, destination.remote, excludes, rclone_config, logger
        )
    if mode == BackupMode.MIRROR_SNAPSHOT:
        return do_mirror_snapshot(
            source, destination.remote, excludes, rclone_config, logger
        )
    raise RcloneError(f"Unknown backup mode: {mode}")


def list_remote_snapshots(
    destination: Destination,
    rclone_config: RcloneConfig,
    logger=None,
) -> list[str]:
    """List snapshot names on the remote."""
    remote = _normalize_remote(destination.remote)
    if destination.mode == BackupMode.SNAPSHOT:
        snaps_remote = f"{remote}_snapshots"
    elif destination.mode == BackupMode.MIRROR_SNAPSHOT:
        snaps_remote = f"{remote}/snapshots"
    elif destination.mode == BackupMode.ARCHIVE:
        args = ["lsl", remote, "--max-depth=1"]
        rc, stdout, _ = _run_rclone(args, rclone_config, logger, timeout=60)
        if rc != 0:
            return []
        names = []
        for line in stdout.splitlines():
            parts = line.split(maxsplit=5)
            if len(parts) >= 6 and parts[-1].endswith(".tar.gz"):
                names.append(parts[-1][:-7])
        return sorted(names, reverse=True)
    else:
        return []

    args = ["lsd", snaps_remote, "--max-depth=1"]
    rc, stdout, _ = _run_rclone(args, rclone_config, logger, timeout=60)
    if rc != 0:
        return []
    names = []
    for line in stdout.splitlines():
        parts = line.split()
        if parts:
            names.append(parts[-1])
    return sorted(names, reverse=True)


def test_connection(rclone_config: RcloneConfig, remote: str = "gdrive:") -> tuple[bool, str]:
    """Test rclone connection. Returns (success, message)."""
    try:
        rc, stdout, stderr = _run_rclone(
            ["listremotes"], rclone_config, timeout=15
        )
        if rc != 0:
            return False, f"rclone listremotes failed: {stderr.strip()[:200]}"
        remotes = [r.strip() for r in stdout.splitlines() if r.strip()]
        if not any(remote.split(":")[0] == r.split(":")[0] for r in remotes):
            return False, (
                f"Remote '{remote}' not configured in rclone. "
                f"Available: {', '.join(remotes) or 'none'}"
            )
        # Try listing the remote root
        rc, stdout, stderr = _run_rclone(
            ["lsd", remote], rclone_config, timeout=30
        )
        if rc != 0:
            return False, f"Cannot access {remote}: {stderr.strip()[:200]}"
        return True, f"OK — {remote} accessible"
    except RcloneError as e:
        return False, str(e)
