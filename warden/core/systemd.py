"""systemd user unit generation and management.

Generates one .service + .timer per scheduled job in
~/.config/systemd/user/warden-<job>.{service,timer}
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from warden.core.config import JobConfig
from warden.core.jobs import list_jobs, load_job

SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"


SERVICE_TEMPLATE = """[Unit]
Description=Warden backup: {job_name}
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart={warden_bin} run {job_name}
Nice=10
IOSchedulingClass=best-effort
IOSchedulingPriority=7
"""


TIMER_TEMPLATE = """[Unit]
Description=Warden schedule: {job_name}
Requires=warden-{job_name}.service

[Timer]
OnCalendar={on_calendar}
Persistent={persistent}
Unit=warden-{job_name}.service

[Install]
WantedBy=timers.target
"""


def _warden_binary() -> str:
    """Find the warden binary path."""
    for candidate in (
        shutil.which("warden"),
        "/usr/local/bin/warden",
        "/usr/bin/warden",
        str(Path.home() / ".local" / "bin" / "warden"),
    ):
        if candidate and Path(candidate).exists():
            return candidate
    return "warden"  # fall back to PATH lookup


def _ensure_systemd_dir() -> None:
    SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)


def generate_unit_files(job: JobConfig) -> tuple[Path, Path | None]:
    """Generate .service (and .timer if scheduled) for a job.

    Returns (service_path, timer_path_or_None).
    """
    _ensure_systemd_dir()
    binary = _warden_binary()
    service_path = SYSTEMD_USER_DIR / f"warden-{job.name}.service"
    service_path.write_text(
        SERVICE_TEMPLATE.format(job_name=job.name, warden_bin=binary),
        encoding="utf-8",
    )

    timer_path = None
    if job.schedule_enabled:
        timer_path = SYSTEMD_USER_DIR / f"warden-{job.name}.timer"
        timer_path.write_text(
            TIMER_TEMPLATE.format(
                job_name=job.name,
                on_calendar=job.schedule_on_calendar,
                persistent="true" if job.schedule_persistent else "false",
            ),
            encoding="utf-8",
        )
    else:
        # Remove timer file if it exists (job was unscheduled)
        old_timer = SYSTEMD_USER_DIR / f"warden-{job.name}.timer"
        if old_timer.exists():
            old_timer.unlink()

    return service_path, timer_path


def remove_unit_files(job_name: str) -> None:
    """Remove .service and .timer for a job."""
    for suffix in (".service", ".timer"):
        path = SYSTEMD_USER_DIR / f"warden-{job_name}{suffix}"
        if path.exists():
            # Stop/disable first if active
            subprocess.run(
                ["systemctl", "--user", "disable", "--now", path.name],
                capture_output=True, check=False,
            )
            path.unlink()


def daemon_reload() -> tuple[bool, str]:
    """Reload systemd user units. Returns (success, message)."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            return False, f"daemon-reload failed: {result.stderr.strip()[:200]}"
        return True, "OK"
    except FileNotFoundError:
        return False, "systemctl not found — is systemd available?"


def enable_timer(job_name: str) -> tuple[bool, str]:
    """Enable and start a job's timer. Returns (success, message)."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", "enable", "--now", f"warden-{job_name}.timer"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            return False, result.stderr.strip()[:200]
        return True, "OK"
    except FileNotFoundError:
        return False, "systemctl not found"


def disable_timer(job_name: str) -> tuple[bool, str]:
    """Disable and stop a job's timer. Returns (success, message)."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", "disable", "--now",
             f"warden-{job_name}.timer"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            # Non-fatal: timer may not exist
            return True, "OK (already disabled)"
        return True, "OK"
    except FileNotFoundError:
        return False, "systemctl not found"


def get_timer_status(job_name: str) -> dict:
    """Get the status of a job's timer.

    Returns dict with keys: enabled, active, next_trigger, last_trigger.
    """
    result = {
        "enabled": False,
        "active": False,
        "next_trigger": "",
        "last_trigger": "",
    }
    try:
        # Check if timer is enabled
        r = subprocess.run(
            ["systemctl", "--user", "is-enabled", f"warden-{job_name}.timer"],
            capture_output=True, text=True, check=False,
        )
        result["enabled"] = r.returncode == 0 and r.stdout.strip() == "enabled"

        # Check if active
        r = subprocess.run(
            ["systemctl", "--user", "is-active", f"warden-{job_name}.timer"],
            capture_output=True, text=True, check=False,
        )
        result["active"] = r.returncode == 0 and r.stdout.strip() == "active"

        # Get next trigger
        r = subprocess.run(
            ["systemctl", "--user", "list-timers",
             f"warden-{job_name}.timer", "--no-pager", "--no-legend"],
            capture_output=True, text=True, check=False,
        )
        if r.returncode == 0 and r.stdout.strip():
            parts = r.stdout.strip().split()
            if len(parts) >= 5:
                result["next_trigger"] = " ".join(parts[:3])
                result["last_trigger"] = " ".join(parts[3:6])
    except (FileNotFoundError, OSError):
        pass
    return result


def install_all_timers() -> dict:
    """(Re)generate and enable timers for all scheduled jobs.

    Returns dict mapping job_name -> (success, message).
    """
    results: dict[str, tuple[bool, str]] = {}

    # 1. Generate units for all scheduled jobs
    for slug, _enabled in list_jobs():
        job = load_job(slug)
        if job is None:
            continue
        if job.schedule_enabled:
            generate_unit_files(job)
            ok, msg = enable_timer(slug)
            results[slug] = (ok, msg)
        else:
            # Make sure timer is disabled/removed if schedule was turned off
            disable_timer(slug)
            # Still write the service file in case of manual run
            generate_unit_files(job)

    # 2. daemon-reload
    daemon_reload()

    return results


def list_all_timers() -> list[dict]:
    """List all warden timers via systemctl --user list-timers."""
    try:
        r = subprocess.run(
            ["systemctl", "--user", "list-timers", "warden-*",
             "--no-pager", "--no-legend", "--all"],
            capture_output=True, text=True, check=False,
        )
        if r.returncode != 0:
            return []
        timers = []
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) < 5:
                continue
            # Format: NEXT LEFT LAST UNIT ACTIVATES
            timers.append({
                "next": " ".join(parts[:2]),
                "last": " ".join(parts[2:4]) if len(parts) > 2 else "",
                "unit": parts[4] if len(parts) > 4 else "",
            })
        return timers
    except (FileNotFoundError, OSError):
        return []
