"""Core backup logic — headless, no GTK imports allowed.

This package can be imported by the CLI (for systemd timers) without
any graphical dependencies. The GUI imports from here, never the reverse.
"""

from warden.core.config import (
    Config,
    JobConfig,
    Destination,
    RetentionPolicy,
    BackupMode,
    DestinationType,
    load_config,
    save_config,
    CONFIG_DIR,
    CONFIG_FILE,
    JOBS_DIR,
)
from warden.core.jobs import (
    list_jobs,
    load_job,
    save_job,
    delete_job,
    rename_job,
    enable_job,
    disable_job,
    is_job_enabled,
    import_job,
    export_job,
)

__all__ = [
    "Config",
    "JobConfig",
    "Destination",
    "RetentionPolicy",
    "BackupMode",
    "DestinationType",
    "load_config",
    "save_config",
    "CONFIG_DIR",
    "CONFIG_FILE",
    "JOBS_DIR",
    "list_jobs",
    "load_job",
    "save_job",
    "delete_job",
    "rename_job",
    "enable_job",
    "disable_job",
    "is_job_enabled",
    "import_job",
    "export_job",
]
