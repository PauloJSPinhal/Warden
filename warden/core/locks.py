"""Per-destination file locking via flock.

Prevents two concurrent backups from writing to the same destination
simultaneously, which would cause I/O contention and possibly corruption.
"""

from __future__ import annotations

import errno
import fcntl
import os
import time
from pathlib import Path

from warden.core.config import LOCKS_DIR


class LockTimeoutError(Exception):
    """Raised when a lock cannot be acquired within the timeout."""


class DestinationLock:
    """Context manager that holds a flock on a destination's lockfile.

    Usage:
        with DestinationLock("gdrive-backup-docs", timeout_minutes=30) as lock:
            # exclusive access to destination
            ...

    The lock is automatically released on context exit.
    """

    def __init__(self, dest_id: str, *, timeout_minutes: int = 30):
        self.dest_id = dest_id
        self.timeout_seconds = timeout_minutes * 60
        self._fd: int | None = None
        self._path: Path = LOCKS_DIR / f"{dest_id}.lock"

    def acquire(self) -> "DestinationLock":
        LOCKS_DIR.mkdir(parents=True, exist_ok=True)
        # Open with O_CREAT | O_RDWR; the lock is on the fd, not the file content
        fd = os.open(str(self._path), os.O_CREAT | os.O_RDWR, 0o600)
        start = time.monotonic()
        while True:
            try:
                # LOCK_EX = exclusive, LOCK_NB = non-blocking (we poll)
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._fd = fd
                # Write our PID for debugging
                try:
                    os.ftruncate(fd, 0)
                    os.write(fd, f"{os.getpid()}\n".encode())
                except OSError:
                    pass
                return self
            except BlockingIOError:
                # Lock is held by another process — wait and retry
                elapsed = time.monotonic() - start
                if elapsed >= self.timeout_seconds:
                    os.close(fd)
                    raise LockTimeoutError(
                        f"Could not acquire lock for destination "
                        f"'{self.dest_id}' after {self.timeout_seconds}s"
                    )
                time.sleep(2)
            except OSError as e:
                os.close(fd)
                if e.errno == errno.EWOULDBLOCK:
                    raise LockTimeoutError(
                        f"Lock would block for '{self.dest_id}'"
                    ) from e
                raise

    def release(self) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

    def __enter__(self) -> "DestinationLock":
        return self.acquire()

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()


def lock_for(dest_id: str, *, timeout_minutes: int = 30) -> DestinationLock:
    """Convenience factory."""
    return DestinationLock(dest_id, timeout_minutes=timeout_minutes)
