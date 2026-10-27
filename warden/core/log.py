"""Logging system with native Python rotation.

Per-job logs in ~/.local/share/warden/logs/<slug>.log
Rotation: 7 daily + 4 weekly, gzip-compressed when rotated.
"""

from __future__ import annotations

import gzip
import logging
import os
import shutil
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from warden.core.config import LOGS_DIR

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"

# Rotation policy
KEEP_DAILY = 7
KEEP_WEEKLY = 4
MAX_BYTES = 5 * 1024 * 1024  # 5 MB before rotating

# Per-module loggers cache
_loggers: dict[str, logging.Logger] = {}


class GzipRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler that gzips rotated files."""

    def __init__(self, filename: str, *, mode: str = "a", **kwargs):
        super().__init__(filename, mode=mode, **kwargs)

    def doRollover(self) -> None:  # noqa: N802 — logging API
        if self.stream:
            self.stream.close()
            self.stream = None
        if os.path.exists(self.baseFilename):
            # Move current to .1 then gzip
            rotated = self.baseFilename + ".1"
            if os.path.exists(rotated):
                os.remove(rotated)
            os.rename(self.baseFilename, rotated)
            self._gzip_file(rotated)
            self._shift_rotated_files()
        if not self.delay:
            self.stream = self._open()

    def _gzip_file(self, path: str) -> None:
        gz_path = path + ".gz"
        with open(path, "rb") as f_in:
            with gzip.open(gz_path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
        os.remove(path)

    def _shift_rotated_files(self) -> None:
        # Keep up to KEEP_DAILY .gz files
        base = self.baseFilename
        gz_files = sorted(
            [f for f in os.listdir(os.path.dirname(base) or ".")
             if f.startswith(os.path.basename(base) + ".") and f.endswith(".gz")]
        )
        # Sort by index descending and prune
        def _idx(name: str) -> int:
            try:
                return int(name.rsplit(".", 2)[-2])
            except (ValueError, IndexError):
                return 0
        gz_files.sort(key=_idx, reverse=True)
        for f in gz_files[KEEP_DAILY - 1:]:
            try:
                os.remove(os.path.join(os.path.dirname(base), f))
            except OSError:
                pass


def get_logger(job_slug: str, *, verbose: bool = False) -> logging.Logger:
    """Get or create a logger for a specific job."""
    if job_slug in _loggers:
        logger = _loggers[job_slug]
        logger.setLevel(logging.DEBUG if verbose else logging.INFO)
        return logger

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"{job_slug}.log"

    logger = logging.getLogger(f"warden.{job_slug}")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False

    # File handler with rotation
    file_handler = GzipRotatingFileHandler(
        str(log_path), maxBytes=MAX_BYTES, backupCount=KEEP_DAILY, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    logger.addHandler(file_handler)

    # Stderr handler (for systemd journal capture)
    stderr_handler = logging.StreamHandler()
    stderr_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    stderr_handler.setLevel(logging.WARNING)  # only warnings+ to stderr
    logger.addHandler(stderr_handler)

    _loggers[job_slug] = logger
    return logger


def close_logger(job_slug: str) -> None:
    """Close all handlers for a logger (used at end of run)."""
    logger = _loggers.pop(job_slug, None)
    if logger is None:
        return
    for handler in list(logger.handlers):
        try:
            handler.close()
        except Exception:
            pass
        logger.removeHandler(handler)


def log_file_path(job_slug: str) -> Path:
    """Return the path to a job's main log file."""
    return LOGS_DIR / f"{job_slug}.log"


def tail_log(job_slug: str, lines: int = 50) -> str:
    """Return the last N lines of a job's log."""
    path = log_file_path(job_slug)
    if not path.exists():
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            all_lines = f.readlines()
        return "".join(all_lines[-lines:])
    except OSError:
        return ""


def prune_old_logs() -> int:
    """Remove log files older than 30 days. Returns count of deleted files."""
    if not LOGS_DIR.exists():
        return 0
    cutoff = datetime.now() - timedelta(days=30)
    deleted = 0
    for entry in LOGS_DIR.iterdir():
        if not entry.is_file():
            continue
        try:
            mtime = datetime.fromtimestamp(entry.stat().st_mtime)
            if mtime < cutoff:
                entry.unlink()
                deleted += 1
        except OSError:
            pass
    return deleted
