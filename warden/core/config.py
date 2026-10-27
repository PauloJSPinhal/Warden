"""Configuration management — global config and per-job JSON files.

All config files use JSON with chmod 0o600 (may contain secrets like
Telegram bot tokens or rclone Service Account paths).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------- paths
CONFIG_DIR = Path.home() / ".config" / "warden"
CONFIG_FILE = CONFIG_DIR / "config.json"
JOBS_DIR = CONFIG_DIR / "jobs.d"
DATA_DIR = Path.home() / ".local" / "share" / "warden"
LOGS_DIR = DATA_DIR / "logs"
HISTORY_FILE = DATA_DIR / "history.json"
STATE_DIR = DATA_DIR / "state"
LOCKS_DIR = CONFIG_DIR / "locks"


# ---------------------------------------------------------------- enums
class BackupMode(str, Enum):
    MIRROR = "mirror"
    SNAPSHOT = "snapshot"
    ARCHIVE = "archive"
    MIRROR_SNAPSHOT = "mirror_snapshot"


class DestinationType(str, Enum):
    LOCAL = "local"
    RCLONE = "rclone"


class RetentionMode(str, Enum):
    NONE = "none"
    LAST_N = "last_n"
    GFS = "gfs"  # Grandfather-Father-Son


# ---------------------------------------------------------------- dataclasses
@dataclass
class RetentionPolicy:
    mode: RetentionMode = RetentionMode.NONE
    n: int = 10  # for last_n
    daily: int = 7  # for gfs
    weekly: int = 4  # for gfs
    monthly: int = 6  # for gfs

    @classmethod
    def from_dict(cls, data: dict | None) -> "RetentionPolicy":
        if not data:
            return cls()
        try:
            mode = RetentionMode(data.get("mode", "none"))
        except ValueError:
            mode = RetentionMode.NONE
        return cls(
            mode=mode,
            n=int(data.get("n", 10)),
            daily=int(data.get("daily", 7)),
            weekly=int(data.get("weekly", 4)),
            monthly=int(data.get("monthly", 6)),
        )

    def to_dict(self) -> dict:
        return {
            "mode": self.mode.value,
            "n": self.n,
            "daily": self.daily,
            "weekly": self.weekly,
            "monthly": self.monthly,
        }


@dataclass
class Destination:
    id: str
    type: DestinationType
    path: str = ""  # for local
    mountpoint: str = ""  # for local — checked with findmnt
    remote: str = ""  # for rclone (e.g. "gdrive:backup/documentos")
    mode: BackupMode = BackupMode.MIRROR
    retention: RetentionPolicy = field(default_factory=RetentionPolicy)

    @classmethod
    def from_dict(cls, data: dict) -> "Destination":
        try:
            dtype = DestinationType(data.get("type", "local"))
        except ValueError:
            raise ValueError(f"Invalid destination type: {data.get('type')}")
        try:
            mode = BackupMode(data.get("mode", "mirror"))
        except ValueError:
            raise ValueError(f"Invalid backup mode: {data.get('mode')}")
        return cls(
            id=data.get("id", ""),
            type=dtype,
            path=data.get("path", ""),
            mountpoint=data.get("mountpoint", ""),
            remote=data.get("remote", ""),
            mode=mode,
            retention=RetentionPolicy.from_dict(data.get("retention")),
        )

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "type": self.type.value,
            "mode": self.mode.value,
            "retention": self.retention.to_dict(),
        }
        if self.type == DestinationType.LOCAL:
            d["path"] = self.path
            d["mountpoint"] = self.mountpoint
        else:
            d["remote"] = self.remote
        return d

    def display(self) -> str:
        if self.type == DestinationType.LOCAL:
            return self.path or self.id
        return self.remote or self.id


@dataclass
class JobConfig:
    name: str
    enabled: bool = True
    source: str = ""
    excludes: list[str] = field(default_factory=list)
    destinations: list[Destination] = field(default_factory=list)
    schedule_enabled: bool = False
    schedule_on_calendar: str = "*-*-* 03:00:00"
    schedule_persistent: bool = True

    @classmethod
    def from_dict(cls, data: dict) -> "JobConfig":
        try:
            destinations = [
                Destination.from_dict(d) for d in data.get("destinations", [])
            ]
        except ValueError as e:
            raise ValueError(f"Invalid destination in job '{data.get('name')}': {e}")
        return cls(
            name=data.get("name", ""),
            enabled=bool(data.get("enabled", True)),
            source=data.get("source", ""),
            excludes=list(data.get("excludes", [])),
            destinations=destinations,
            schedule_enabled=bool(data.get("schedule", {}).get("enabled", False)),
            schedule_on_calendar=data.get("schedule", {}).get(
                "on_calendar", "*-*-* 03:00:00"
            ),
            schedule_persistent=bool(
                data.get("schedule", {}).get("persistent", True)
            ),
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "source": self.source,
            "excludes": self.excludes,
            "destinations": [d.to_dict() for d in self.destinations],
            "schedule": {
                "enabled": self.schedule_enabled,
                "on_calendar": self.schedule_on_calendar,
                "persistent": self.schedule_persistent,
            },
        }


@dataclass
class RcloneConfig:
    binary: str = "/usr/bin/rclone"
    default_remote: str = "gdrive"
    sa_key_path: str = ""

    @classmethod
    def from_dict(cls, data: dict | None) -> "RcloneConfig":
        if not data:
            return cls()
        return cls(
            binary=data.get("binary", "/usr/bin/rclone"),
            default_remote=data.get("default_remote", "gdrive"),
            sa_key_path=data.get("sa_key_path", ""),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TelegramConfig:
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""
    notify_on_success: bool = True
    notify_on_partial: bool = True
    notify_on_failure: bool = True

    @classmethod
    def from_dict(cls, data: dict | None) -> "TelegramConfig":
        if not data:
            return cls()
        return cls(
            enabled=bool(data.get("enabled", False)),
            bot_token=data.get("bot_token", ""),
            chat_id=data.get("chat_id", ""),
            notify_on_success=bool(data.get("notify_on_success", True)),
            notify_on_partial=bool(data.get("notify_on_partial", True)),
            notify_on_failure=bool(data.get("notify_on_failure", True)),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BehaviorConfig:
    verify_mount_before_local: bool = True
    flock_per_destination: bool = True
    flock_timeout_minutes: int = 30
    include_size_in_log: bool = True
    autostart_gui: bool = False

    @classmethod
    def from_dict(cls, data: dict | None) -> "BehaviorConfig":
        if not data:
            return cls()
        return cls(
            verify_mount_before_local=bool(
                data.get("verify_mount_before_local", True)
            ),
            flock_per_destination=bool(data.get("flock_per_destination", True)),
            flock_timeout_minutes=int(data.get("flock_timeout_minutes", 30)),
            include_size_in_log=bool(data.get("include_size_in_log", True)),
            autostart_gui=bool(data.get("autostart_gui", False)),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Config:
    version: int = 1
    theme: str = "sistema"  # black / white / sistema
    font: str = ""
    rclone: RcloneConfig = field(default_factory=RcloneConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    behavior: BehaviorConfig = field(default_factory=BehaviorConfig)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "theme": self.theme,
            "font": self.font,
            "rclone": self.rclone.to_dict(),
            "telegram": self.telegram.to_dict(),
            "behavior": self.behavior.to_dict(),
        }


# ---------------------------------------------------------------- defaults
DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "theme": "sistema",
    "font": "",
    "rclone": {
        "binary": "/usr/bin/rclone",
        "default_remote": "gdrive",
        "sa_key_path": "",
    },
    "telegram": {
        "enabled": False,
        "bot_token": "",
        "chat_id": "",
        "notify_on_success": True,
        "notify_on_partial": True,
        "notify_on_failure": True,
    },
    "behavior": {
        "verify_mount_before_local": True,
        "flock_per_destination": True,
        "flock_timeout_minutes": 30,
        "include_size_in_log": True,
        "autostart_gui": False,
    },
}


# ---------------------------------------------------------------- I/O
def _ensure_dirs() -> None:
    """Create all required directories with secure permissions."""
    for d in (CONFIG_DIR, JOBS_DIR, DATA_DIR, LOGS_DIR, STATE_DIR, LOCKS_DIR):
        d.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass


def _write_json(path: Path, data: Any, *, secret: bool = False) -> None:
    """Write JSON with optional chmod 0o600 for files containing secrets."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)
    if secret:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def load_config() -> Config:
    """Load global config, returning defaults if missing."""
    _ensure_dirs()
    if not CONFIG_FILE.exists():
        _write_json(CONFIG_FILE, DEFAULT_CONFIG, secret=True)
        return Config()
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        # Corrupted config — back it up and start fresh
        backup = CONFIG_FILE.with_suffix(".json.bak")
        try:
            os.replace(CONFIG_FILE, backup)
        except OSError:
            pass
        _write_json(CONFIG_FILE, DEFAULT_CONFIG, secret=True)
        return Config()

    return Config(
        version=int(data.get("version", 1)),
        theme=data.get("theme", "sistema"),
        font=data.get("font", ""),
        rclone=RcloneConfig.from_dict(data.get("rclone")),
        telegram=TelegramConfig.from_dict(data.get("telegram")),
        behavior=BehaviorConfig.from_dict(data.get("behavior")),
    )


def save_config(config: Config) -> None:
    """Persist global config with chmod 0o600."""
    _ensure_dirs()
    _write_json(CONFIG_FILE, config.to_dict(), secret=True)
