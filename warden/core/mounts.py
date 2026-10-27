"""Mount verification via findmnt (no extra dependencies).

Used to check if an external disk is mounted before running local backups.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def findmnt_available() -> bool:
    """Check if findmnt binary exists."""
    return shutil.which("findmnt") is not None


def is_mounted(mountpoint: str) -> bool:
    """Check if the given path is a mountpoint (something is mounted there).

    Uses `findmnt --noheadings --output TARGET <path>` which exits 0 if mounted.
    Falls back to os.path.ismount() if findmnt is unavailable.
    """
    if not mountpoint:
        return True  # No mountpoint specified — assume always available
    path = Path(mountpoint)
    if not path.exists():
        return False
    if not findmnt_available():
        try:
            return path.is_mount()
        except OSError:
            return False
    try:
        result = subprocess.run(
            ["findmnt", "--noheadings", "--output", "TARGET", str(path)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        # Fall back to Python
        try:
            return path.is_mount()
        except OSError:
            return False


def list_mounts() -> list[dict[str, str]]:
    """List all current mounts as dicts with keys: target, source, fstype.

    Useful for the GUI to let the user pick a mountpoint from a dropdown.
    """
    if not findmnt_available():
        return []
    try:
        result = subprocess.run(
            ["findmnt", "--noheadings", "--output",
             "TARGET,SOURCE,FSTYPE", "--list"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode != 0:
            return []
        mounts = []
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 3:
                mounts.append({
                    "target": parts[0],
                    "source": parts[1],
                    "fstype": parts[2],
                })
        return mounts
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []
