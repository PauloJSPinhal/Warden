"""History and state persistence.

- history.json: append-only list of last N runs per job
- state/<job>.state.json: current state (last run, next scheduled)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Any

from warden.core.config import HISTORY_FILE, STATE_DIR

MAX_HISTORY_PER_JOB = 100


# ---------------------------------------------------------------- history
@dataclass
class RunRecord:
    """A single backup execution record."""

    job: str
    started_at: str  # ISO 8601
    finished_at: str
    duration_seconds: float
    status: str  # success / partial / failure
    destinations_ok: list[str] = field(default_factory=list)
    destinations_skipped: list[str] = field(default_factory=list)
    destinations_failed: list[str] = field(default_factory=list)
    total_size_bytes: int = 0
    total_files: int = 0
    error_message: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _load_history() -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return []


def _save_history(records: list[dict]) -> None:
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = HISTORY_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
    os.replace(tmp, HISTORY_FILE)
    try:
        os.chmod(HISTORY_FILE, 0o600)
    except OSError:
        pass


def append_run(record: RunRecord) -> None:
    """Append a run record and prune to MAX_HISTORY_PER_JOB per job."""
    records = _load_history()
    records.append(record.to_dict())

    # Group by job and prune
    by_job: dict[str, list[dict]] = {}
    for r in records:
        by_job.setdefault(r.get("job", ""), []).append(r)
    pruned: list[dict] = []
    for job, runs in by_job.items():
        runs_sorted = sorted(runs, key=lambda x: x.get("finished_at", ""),
                             reverse=True)[:MAX_HISTORY_PER_JOB]
        pruned.extend(runs_sorted)

    _save_history(pruned)


def get_job_history(job_slug: str, limit: int = 50) -> list[RunRecord]:
    """Get run history for a specific job, most recent first."""
    records = _load_history()
    job_records = [r for r in records if r.get("job") == job_slug]
    job_records.sort(key=lambda x: x.get("finished_at", ""), reverse=True)
    return [RunRecord(**r) for r in job_records[:limit]]


def get_all_history(limit: int = 100) -> list[RunRecord]:
    """Get all run records, most recent first."""
    records = _load_history()
    records.sort(key=lambda x: x.get("finished_at", ""), reverse=True)
    return [RunRecord(**r) for r in records[:limit]]


# ---------------------------------------------------------------- state
@dataclass
class JobState:
    """Current state of a job."""

    job: str
    last_run: str | None = None  # ISO 8601
    last_status: str | None = None  # success / partial / failure
    last_duration_seconds: float | None = None
    next_run: str | None = None  # ISO 8601 (from systemd timer)
    destinations_state: dict[str, str] = field(default_factory=dict)
    # ^ map of destination_id -> "ok" / "skipped" / "failed"

    def to_dict(self) -> dict:
        return asdict(self)


def state_path(job_slug: str) -> Path:
    return STATE_DIR / f"{job_slug}.state.json"


def load_state(job_slug: str) -> JobState:
    path = state_path(job_slug)
    if not path.exists():
        return JobState(job=job_slug)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return JobState(**data)
    except (json.JSONDecodeError, OSError, TypeError):
        return JobState(job=job_slug)


def save_state(state: JobState) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = state_path(state.job)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state.to_dict(), f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def update_state_after_run(
    job_slug: str,
    finished_at: datetime,
    status: str,
    duration_seconds: float,
    destinations_state: dict[str, str],
) -> None:
    """Update the state file after a run completes."""
    state = load_state(job_slug)
    state.last_run = finished_at.isoformat()
    state.last_status = status
    state.last_duration_seconds = duration_seconds
    state.destinations_state = destinations_state
    save_state(state)
