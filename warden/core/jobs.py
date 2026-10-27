"""Job discovery and manipulation.

Each job is a JSON file in ~/.config/warden/jobs.d/<name>.json.
Disabled jobs have extension .disabled (systemd drop-in style).
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from warden.core.config import (
    JOBS_DIR,
    JobConfig,
    _write_json,
)

# Slug pattern: lowercase, digits, hyphens, underscores
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def slugify(name: str) -> str:
    """Convert a display name to a valid job slug.

    'Meus Documentos' -> 'meus_documentos'
    'Backup Fotos 2024!' -> 'backup_fotos_2024'
    """
    import unicodedata

    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-zA-Z0-9_-]+", "_", s).strip("_").lower()
    if not s:
        s = "job"
    if not s[0].isalnum():
        s = "job_" + s
    return s


def is_valid_slug(name: str) -> bool:
    return bool(SLUG_RE.match(name))


def list_jobs() -> list[tuple[str, bool]]:
    """List all jobs as (slug, enabled) tuples, sorted by slug."""
    if not JOBS_DIR.exists():
        return []
    jobs = []
    for entry in sorted(JOBS_DIR.iterdir()):
        if entry.suffix == ".json":
            jobs.append((entry.stem, True))
        elif entry.suffix == ".disabled":
            jobs.append((entry.stem, False))
    return jobs


def job_path(slug: str, enabled: bool = True) -> Path:
    suffix = ".json" if enabled else ".disabled"
    return JOBS_DIR / f"{slug}{suffix}"


def find_job_path(slug: str) -> Path | None:
    """Find a job path regardless of enabled/disabled state."""
    for p in (job_path(slug, enabled=True), job_path(slug, enabled=False)):
        if p.exists():
            return p
    return None


def is_job_enabled(slug: str) -> bool:
    return job_path(slug, enabled=True).exists()


def load_job(slug: str) -> JobConfig | None:
    """Load a job by slug. Returns None if not found."""
    path = find_job_path(slug)
    if path is None:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return JobConfig.from_dict(data)
    except (json.JSONDecodeError, OSError, ValueError):
        return None


def save_job(job: JobConfig, *, enabled: bool | None = None) -> None:
    """Save a job. If enabled is None, preserves current enabled state."""
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    if enabled is None:
        enabled = is_job_enabled(job.name) if find_job_path(job.name) else True
    # Remove both possible variants first to avoid leftovers
    for p in (job_path(job.name, True), job_path(job.name, False)):
        if p.exists():
            p.unlink()
    path = job_path(job.name, enabled=enabled)
    _write_json(path, job.to_dict(), secret=False)


def delete_job(slug: str) -> bool:
    """Delete a job file. Returns True if deleted."""
    path = find_job_path(slug)
    if path is None:
        return False
    path.unlink()
    return True


def rename_job(old_slug: str, new_slug: str) -> bool:
    """Rename a job. Returns True on success."""
    if not is_valid_slug(new_slug):
        return False
    old_path = find_job_path(old_slug)
    if old_path is None:
        return False
    if find_job_path(new_slug) is not None:
        return False  # target exists
    new_path = old_path.with_name(f"{new_slug}{old_path.suffix}")
    old_path.rename(new_path)
    # Also rename the job's name field inside the JSON
    job = load_job(new_slug)
    if job:
        job.name = new_slug
        save_job(job)
    return True


def enable_job(slug: str) -> bool:
    """Re-enable a disabled job. Returns True if state changed."""
    disabled = job_path(slug, enabled=False)
    enabled = job_path(slug, enabled=True)
    if not disabled.exists():
        return False
    disabled.rename(enabled)
    return True


def disable_job(slug: str) -> bool:
    """Disable a job (rename .json -> .disabled). Returns True if changed."""
    disabled = job_path(slug, enabled=False)
    enabled = job_path(slug, enabled=True)
    if not enabled.exists():
        return False
    enabled.rename(disabled)
    return True


def export_job(slug: str, dest_path: Path) -> bool:
    """Copy a job JSON to dest_path (for export). Returns True on success."""
    src = find_job_path(slug)
    if src is None:
        return False
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest_path)
    return True


def import_job(src_path: Path, new_slug: str | None = None) -> str | None:
    """Import a job JSON from src_path. Returns the slug or None on failure."""
    src_path = Path(src_path)
    if not src_path.exists():
        return None
    try:
        with open(src_path, encoding="utf-8") as f:
            data = json.load(f)
        job = JobConfig.from_dict(data)
    except (json.JSONDecodeError, OSError, ValueError):
        return None

    slug = new_slug or slugify(job.name or src_path.stem)
    if not is_valid_slug(slug):
        return None
    if find_job_path(slug) is not None:
        # Generate a unique slug
        i = 2
        while find_job_path(f"{slug}_{i}") is not None:
            i += 1
        slug = f"{slug}_{i}"

    job.name = slug
    save_job(job, enabled=True)
    return slug


def validate_job(job: JobConfig) -> list[str]:
    """Return a list of validation errors. Empty list = valid."""
    errors = []
    if not job.name:
        errors.append("Job name is empty")
    elif not is_valid_slug(job.name):
        errors.append(f"Invalid job name: '{job.name}'")
    if not job.source:
        errors.append("Source path is empty")
    elif not Path(job.source).is_dir():
        errors.append(f"Source path does not exist or is not a directory: {job.source}")
    if not job.destinations:
        errors.append("No destinations defined")
    for i, dest in enumerate(job.destinations):
        if not dest.id:
            errors.append(f"Destination #{i}: missing id")
        if dest.type.value == "local" and not dest.path:
            errors.append(f"Destination '{dest.id}': missing path")
        if dest.type.value == "rclone" and not dest.remote:
            errors.append(f"Destination '{dest.id}': missing remote")
    return errors
