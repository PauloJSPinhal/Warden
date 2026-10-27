"""Retention policy enforcement.

Prunes old backups according to the policy configured per destination.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

from warden.core.config import RetentionPolicy, RetentionMode


# Snapshot directory name pattern: ISO 8601 (basic) — sortable
# Example: 2026-10-27T03-00
SNAPSHOT_PATTERN = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})(?:-(\d{2}))?$"
)


def snapshot_name(dt: datetime | None = None) -> str:
    """Generate a snapshot directory name for a datetime (default: now)."""
    dt = dt or datetime.now()
    return dt.strftime("%Y-%m-%dT%H-%M")


def parse_snapshot_name(name: str) -> datetime | None:
    """Parse a snapshot directory name back to datetime. None if invalid."""
    m = SNAPSHOT_PATTERN.match(name)
    if not m:
        return None
    year, month, day, hour, minute = (int(x) for x in m.groups()[:5])
    second = int(m.group(5)) if m.group(5) else 0
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def list_snapshots(base_dir: Path) -> list[tuple[str, datetime]]:
    """List snapshots in a directory, sorted oldest first."""
    if not base_dir.exists():
        return []
    snapshots = []
    for entry in base_dir.iterdir():
        if not entry.is_dir():
            continue
        dt = parse_snapshot_name(entry.name)
        if dt is not None:
            snapshots.append((entry.name, dt))
    snapshots.sort(key=lambda x: x[1])
    return snapshots


def _select_gfs_keep(
    snapshots: list[tuple[str, datetime]],
    daily: int,
    weekly: int,
    monthly: int,
) -> set[str]:
    """Apply GFS retention: keep last N daily, N weekly, N monthly.

    Weekly = first snapshot of each ISO week (Monday-Sunday).
    Monthly = first snapshot of each calendar month.
    """
    if not snapshots:
        return set()
    now = datetime.now()
    keep: set[str] = set()

    # Daily: most recent N days with snapshots
    by_day: dict[str, tuple[str, datetime]] = {}
    for name, dt in snapshots:
        key = dt.strftime("%Y-%m-%d")
        if key not in by_day or dt > by_day[key][1]:
            by_day[key] = (name, dt)
    daily_sorted = sorted(by_day.values(), key=lambda x: x[1], reverse=True)
    for name, _ in daily_sorted[:daily]:
        keep.add(name)

    # Weekly: most recent N ISO weeks with snapshots
    by_week: dict[str, tuple[str, datetime]] = {}
    for name, dt in snapshots:
        iso = dt.isocalendar()
        key = f"{iso.year}-W{iso.week:02d}"
        if key not in by_week or dt > by_week[key][1]:
            by_week[key] = (name, dt)
    weekly_sorted = sorted(by_week.values(), key=lambda x: x[1], reverse=True)
    for name, _ in weekly_sorted[:weekly]:
        keep.add(name)

    # Monthly: most recent N calendar months with snapshots
    by_month: dict[str, tuple[str, datetime]] = {}
    for name, dt in snapshots:
        key = dt.strftime("%Y-%m")
        if key not in by_month or dt > by_month[key][1]:
            by_month[key] = (name, dt)
    monthly_sorted = sorted(by_month.values(), key=lambda x: x[1], reverse=True)
    for name, _ in monthly_sorted[:monthly]:
        keep.add(name)

    return keep


def select_to_prune(
    snapshots: list[tuple[str, datetime]],
    policy: RetentionPolicy,
) -> list[str]:
    """Given snapshots (oldest first) and a policy, return names to delete."""
    if not snapshots:
        return []
    if policy.mode == RetentionMode.NONE:
        return []
    if policy.mode == RetentionMode.LAST_N:
        # Keep last N, prune the rest
        keep_count = max(1, policy.n)
        if len(snapshots) <= keep_count:
            return []
        return [name for name, _ in snapshots[:-keep_count]]
    if policy.mode == RetentionMode.GFS:
        keep = _select_gfs_keep(
            snapshots, policy.daily, policy.weekly, policy.monthly
        )
        return [name for name, _ in snapshots if name not in keep]
    return []


def prune_local_snapshots(base_dir: Path, policy: RetentionPolicy) -> list[str]:
    """Prune local snapshot directories according to policy.

    Returns the list of pruned snapshot names.
    """
    snapshots = list_snapshots(base_dir)
    to_prune = select_to_prune(snapshots, policy)
    pruned = []
    for name in to_prune:
        path = base_dir / name
        try:
            if path.exists():
                import shutil
                shutil.rmtree(path)
                pruned.append(name)
        except OSError:
            pass
    return pruned
