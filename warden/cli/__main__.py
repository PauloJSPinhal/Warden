"""warden command-line interface.

Subcommands:
  list             List all jobs
  run <job>        Run a backup job
  status [job]     Show status
  restore          Restore a snapshot
  install-timers   (Re)generate and enable systemd timers
  check            Validate configuration files
  test-telegram    Send a test Telegram message
  test-rclone      Test rclone connection
  version          Show version info

This module NEVER imports GTK — it must run in headless environments
(systemd timers, SSH sessions, etc.).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from warden import __version__
from warden.core import (
    load_config,
    list_jobs,
    load_job,
    save_job,
    delete_job,
    rename_job,
)
from warden.core.backup import run_backup, restore_snapshot, BackupResult
from warden.core.config import load_config as _load_config
from warden.core.jobs import validate_job, find_job_path, export_job, import_job
from warden.core.history import get_job_history, get_all_history, load_state
from warden.core.log import tail_log, log_file_path
from warden.core.notify import send_test_message
from warden.core.rclone_backend import test_connection
from warden.core.systemd import (
    install_all_timers,
    list_all_timers,
    get_timer_status,
    generate_unit_files,
    enable_timer,
)


# ---------------------------------------------------------------- helpers
def _print_kv(key: str, value, *, indent: int = 0) -> None:
    pad = "  " * indent
    print(f"{pad}{key}: {value}")


def _human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    for unit in ("KB", "MB", "GB", "TB"):
        n /= 1024
        if n < 1024:
            return f"{n:.1f} {unit}"
    return f"{n:.1f} PB"


def _human_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    m = int(seconds // 60)
    s = int(seconds % 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h = int(m // 60)
    m = m % 60
    return f"{h}h{m:02d}m"


# ---------------------------------------------------------------- commands
def cmd_list(args: argparse.Namespace) -> int:
    """List all jobs."""
    jobs = list_jobs()
    if not jobs:
        print("No jobs configured. Run 'warden-gui' to create one, or use")
        print("'warden import <file.json>' to import a job config.")
        return 0
    print(f"{'NAME':<25} {'ENABLED':<10} {'SOURCE'}")
    print("-" * 80)
    for slug, enabled in jobs:
        job = load_job(slug)
        if job is None:
            print(f"{slug:<25} {'?':<10} (invalid config)")
            continue
        en = "yes" if enabled else "no"
        src = job.source or "(not set)"
        if len(src) > 40:
            src = "..." + src[-37:]
        print(f"{slug:<25} {en:<10} {src}")
    print(f"\nTotal: {len(jobs)} job(s)")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Run a backup job."""
    job = load_job(args.job)
    if job is None:
        print(f"Error: job '{args.job}' not found", file=sys.stderr)
        return 1

    config = load_config()
    only = args.only.split(",") if args.only else None

    if only:
        # Validate that the requested destinations exist
        valid_ids = {d.id for d in job.destinations}
        invalid = [d for d in only if d not in valid_ids]
        if invalid:
            print(
                f"Error: unknown destination(s): {', '.join(invalid)}",
                file=sys.stderr,
            )
            print(f"Available: {', '.join(valid_ids)}", file=sys.stderr)
            return 1

    print(f"Starting backup: {job.name}")
    print(f"Source: {job.source}")
    print(f"Destinations: {', '.join(d.id for d in job.destinations)}")
    if only:
        print(f"Filtered to: {', '.join(only)}")
    print()

    result = run_backup(job, config, only_destinations=only, verbose=args.verbose)

    # Print summary
    print("\n" + "=" * 60)
    print(f"Backup {result.status.upper()}: {result.job}")
    print(f"Duration: {_human_duration(result.duration_seconds)}")
    print()
    for dr in result.destinations:
        status_icon = {
            "ok": "✓", "skipped": "⏭", "failed": "✗"
        }.get(dr.status, "?")
        size_str = (
            f", {_human_size(dr.size_bytes)}" if dr.size_bytes else ""
        )
        line = f"  {status_icon} {dr.destination_id} — {dr.status}{size_str}"
        if dr.error:
            line += f" ({dr.error[:80]})"
        print(line)

    if result.status == "failure":
        return 2
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Show status of jobs."""
    if args.job:
        return _status_one(args.job, args.log)
    return _status_all()


def _status_all() -> int:
    jobs = list_jobs()
    if not jobs:
        print("No jobs configured.")
        return 0
    print(f"{'JOB':<20} {'LAST RUN':<22} {'STATUS':<10} {'NEXT RUN'}")
    print("-" * 80)
    for slug, _ in jobs:
        state = load_state(slug)
        last = state.last_run or "never"
        if state.last_run:
            try:
                dt = datetime.fromisoformat(state.last_run)
                last = dt.strftime("%Y-%m-%d %H:%M")
            except ValueError:
                pass
        status = state.last_status or "-"
        # Get next run from systemd
        timer_status = get_timer_status(slug)
        next_run = timer_status.get("next_trigger", "-") if timer_status["enabled"] else "-"
        print(f"{slug:<20} {last:<22} {status:<10} {next_run}")
    return 0


def _status_one(slug: str, show_log: bool) -> int:
    job = load_job(slug)
    if job is None:
        print(f"Error: job '{slug}' not found", file=sys.stderr)
        return 1

    print(f"Job: {job.name}")
    print(f"Enabled: {'yes' if find_job_path(slug) and find_job_path(slug).suffix == '.json' else 'no'}")
    print(f"Source: {job.source}")
    print(f"Excludes: {', '.join(job.excludes) or '(none)'}")
    print()
    print("Destinations:")
    for d in job.destinations:
        print(f"  - {d.id} ({d.type.value}, {d.mode.value})")
        if d.type.value == "local":
            print(f"    path: {d.path}")
            if d.mountpoint:
                print(f"    mountpoint: {d.mountpoint}")
        else:
            print(f"    remote: {d.remote}")
        print(f"    retention: {d.retention.mode.value}")
    print()
    print("Schedule:")
    if job.schedule_enabled:
        print(f"  enabled: yes")
        print(f"  on_calendar: {job.schedule_on_calendar}")
        print(f"  persistent: {job.schedule_persistent}")
        timer_status = get_timer_status(slug)
        print(f"  systemd_enabled: {timer_status['enabled']}")
        print(f"  systemd_active: {timer_status['active']}")
        if timer_status.get("next_trigger"):
            print(f"  next_run: {timer_status['next_trigger']}")
    else:
        print("  enabled: no")

    print()
    state = load_state(slug)
    print("Last run:")
    if state.last_run:
        print(f"  at: {state.last_run}")
        print(f"  status: {state.last_status}")
        if state.last_duration_seconds is not None:
            print(f"  duration: {_human_duration(state.last_duration_seconds)}")
        if state.destinations_state:
            print("  destinations:")
            for dest_id, st in state.destinations_state.items():
                print(f"    {dest_id}: {st}")
    else:
        print("  (never run)")

    if show_log:
        print()
        print(f"Last 50 lines of log ({log_file_path(slug)}):")
        print("-" * 60)
        print(tail_log(slug, lines=50))

    # Recent history
    print()
    print("Recent history (last 5):")
    history = get_job_history(slug, limit=5)
    if not history:
        print("  (no history)")
    else:
        for r in history:
            dt = r.finished_at
            try:
                dt = datetime.fromisoformat(r.finished_at).strftime("%Y-%m-%d %H:%M")
            except ValueError:
                pass
            ok = len(r.destinations_ok)
            sk = len(r.destinations_skipped)
            fa = len(r.destinations_failed)
            print(
                f"  {dt} — {r.status} "
                f"(ok={ok}, skipped={sk}, failed={fa}, "
                f"duration={_human_duration(r.duration_seconds)})"
            )

    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    """Restore a snapshot."""
    job = load_job(args.job)
    if job is None:
        print(f"Error: job '{args.job}' not found", file=sys.stderr)
        return 1

    # Find destination
    dest = None
    for d in job.destinations:
        if d.id == args.destination:
            dest = d
            break
    if dest is None:
        print(
            f"Error: destination '{args.destination}' not found in job "
            f"'{args.job}'",
            file=sys.stderr,
        )
        print(f"Available: {', '.join(d.id for d in job.destinations)}")
        return 1

    target = args.to or f"{job.source}_restored"
    print(f"Restoring snapshot: {args.snapshot}")
    print(f"  from: {dest.id} ({dest.type.value})")
    print(f"  to:   {target}")
    print()

    config = load_config()
    ok, msg = restore_snapshot(job, args.destination, args.snapshot, target, config)
    if ok:
        print(f"✓ {msg}")
        return 0
    print(f"✗ {msg}", file=sys.stderr)
    return 1


def cmd_install_timers(args: argparse.Namespace) -> int:
    """(Re)generate and enable systemd timers."""
    if args.job:
        job = load_job(args.job)
        if job is None:
            print(f"Error: job '{args.job}' not found", file=sys.stderr)
            return 1
        print(f"Generating units for job: {args.job}")
        generate_unit_files(job)
        if job.schedule_enabled:
            ok, msg = enable_timer(args.job)
            print(f"  timer enable: {'OK' if ok else 'FAILED: ' + msg}")
        else:
            print("  (schedule disabled — only service file generated)")
        return 0

    print("Regenerating systemd units for all jobs...")
    results = install_all_timers()
    if not results:
        print("No scheduled jobs found.")
        return 0
    print()
    for slug, (ok, msg) in results.items():
        status = "✓" if ok else "✗"
        print(f"  {status} {slug}: {msg}")
    print()
    print("Active warden timers:")
    timers = list_all_timers()
    if not timers:
        print("  (none)")
    else:
        for t in timers:
            print(f"  {t['unit']}: next={t['next']}, last={t['last']}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Validate all job configs."""
    jobs = list_jobs()
    if not jobs:
        print("No jobs configured.")
        return 0
    has_errors = False
    for slug, _ in jobs:
        job = load_job(slug)
        if job is None:
            print(f"✗ {slug}: invalid config (cannot load)")
            has_errors = True
            continue
        errors = validate_job(job)
        if errors:
            has_errors = True
            print(f"✗ {slug}:")
            for e in errors:
                print(f"    - {e}")
        else:
            print(f"✓ {slug}: OK ({len(job.destinations)} destination(s))")
    return 1 if has_errors else 0


def cmd_test_telegram(args: argparse.Namespace) -> int:
    """Send a test Telegram message."""
    config = load_config()
    if not config.telegram.enabled:
        print("Telegram is not enabled in config.")
        return 1
    print("Sending test message...")
    ok, msg = send_test_message(config.telegram)
    if ok:
        print(f"✓ {msg}")
        return 0
    print(f"✗ {msg}", file=sys.stderr)
    return 1


def cmd_test_rclone(args: argparse.Namespace) -> int:
    """Test rclone connection."""
    config = load_config()
    remote = args.remote or f"{config.rclone.default_remote}:"
    print(f"Testing rclone connection to: {remote}")
    ok, msg = test_connection(config.rclone, remote)
    if ok:
        print(f"✓ {msg}")
        return 0
    print(f"✗ {msg}", file=sys.stderr)
    return 1


def cmd_export(args: argparse.Namespace) -> int:
    """Export a job to a JSON file."""
    dest = Path(args.output)
    ok = export_job(args.job, dest)
    if ok:
        print(f"✓ Exported '{args.job}' to {dest}")
        return 0
    print(f"✗ Failed to export '{args.job}'", file=sys.stderr)
    return 1


def cmd_import(args: argparse.Namespace) -> int:
    """Import a job from a JSON file."""
    src = Path(args.file)
    slug = import_job(src, new_slug=args.name)
    if slug:
        print(f"✓ Imported as: {slug}")
        return 0
    print(f"✗ Failed to import {src}", file=sys.stderr)
    return 1


def cmd_version(args: argparse.Namespace) -> int:
    """Show version info."""
    print(f"warden {__version__}")
    print("Guardian of your files — local and Google Drive backup for Debian")
    print("https://github.com/warden-backup/warden")
    return 0


# ---------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="warden",
        description="Warden — guardian of your files. "
                    "Local and Google Drive backup for Debian.",
    )
    parser.add_argument(
        "--version", action="store_true", help="show version and exit"
    )
    sub = parser.add_subparsers(dest="command", help="command to run")

    # list
    p = sub.add_parser("list", help="list all jobs")
    p.set_defaults(func=cmd_list)

    # run
    p = sub.add_parser("run", help="run a backup job")
    p.add_argument("job", help="job slug")
    p.add_argument(
        "--only", help="comma-separated destination IDs to run (default: all)"
    )
    p.add_argument(
        "-v", "--verbose", action="store_true", help="verbose (debug) logging"
    )
    p.set_defaults(func=cmd_run)

    # status
    p = sub.add_parser("status", help="show status of jobs")
    p.add_argument("job", nargs="?", help="job slug (omit for all)")
    p.add_argument("--log", action="store_true", help="show last 50 log lines")
    p.set_defaults(func=cmd_status)

    # restore
    p = sub.add_parser("restore", help="restore a snapshot")
    p.add_argument("job", help="job slug")
    p.add_argument("destination", help="destination ID")
    p.add_argument("snapshot", help="snapshot name (e.g. 2026-10-27T03-00)")
    p.add_argument("--to", help="target directory (default: <source>_restored)")
    p.set_defaults(func=cmd_restore)

    # install-timers
    p = sub.add_parser("install-timers", help="(re)generate and enable systemd timers")
    p.add_argument("job", nargs="?", help="job slug (omit for all)")
    p.set_defaults(func=cmd_install_timers)

    # check
    p = sub.add_parser("check", help="validate all job configs")
    p.set_defaults(func=cmd_check)

    # test-telegram
    p = sub.add_parser("test-telegram", help="send a test Telegram message")
    p.set_defaults(func=cmd_test_telegram)

    # test-rclone
    p = sub.add_parser("test-rclone", help="test rclone connection")
    p.add_argument(
        "--remote", help="remote name (default: from config, e.g. 'gdrive:')"
    )
    p.set_defaults(func=cmd_test_rclone)

    # export
    p = sub.add_parser("export", help="export a job config to JSON")
    p.add_argument("job", help="job slug")
    p.add_argument("output", help="output file path")
    p.set_defaults(func=cmd_export)

    # import
    p = sub.add_parser("import", help="import a job config from JSON")
    p.add_argument("file", help="JSON file to import")
    p.add_argument("--name", help="new job slug (default: from file)")
    p.set_defaults(func=cmd_import)

    # version
    p = sub.add_parser("version", help="show version")
    p.set_defaults(func=cmd_version)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        return cmd_version(args)

    if not args.command:
        parser.print_help()
        return 0

    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
