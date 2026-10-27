# 🔐 Warden

> *Guardian of your files — local and Google Drive backup for Debian*

**Warden** is a backup system for Debian that lets you back up any folder to **internal disk**, **external disk**, or **Google Drive** — with each destination managed independently. Backups can be **manual** (one-click GUI or CLI) or **automatic** (systemd timers).

Each task is a self-contained JSON config file. Each destination inside a task has its own mode, retention policy, and schedule. Nothing is hidden behind magic — everything is auditable plain JSON.

---

## ✨ Features

- 🎯 **Per-folder, per-destination control** — every backup task is independent; every destination inside a task is independent
- 💾 **Three destination types** — internal disk, external disk (auto-mount-aware), Google Drive (via rclone)
- 🔁 **Four backup modes per destination**:
  - **Mirror** — destination always equals source (`rsync --delete` / `rclone sync`)
  - **Dated snapshots** — `rsync --link-dest` hardlinks for near-zero space overhead; `rclone --backup-dir` for Drive
  - **Compressed archive** — `tar.gz` dated file
  - **Mirror + snapshots** — fast mirror plus historical snapshots side by side
- 🗂️ **Retention per destination** — Grandfather-Father-Son (7 daily / 4 weekly / 6 monthly), last-N, or unlimited
- ⏰ **systemd timers** — one `.timer` + `.service` per automatic task, with `Persistent=true` for missed runs
- 📢 **Always-on notifications** — every backup run sends a Telegram message (✅ success / ⚠️ partial / ❌ failure)
- 📜 **Built-in log system** — per-job logs with native Python rotation, plus `history.json` and `state.json`
- 🛡️ **Mount safety** — external disk checked via `findmnt` before any local backup; if not mounted, destination is SKIPPED (no writing to an empty mountpoint)
- 🔒 **flock per destination** — concurrent tasks targeting the same disk run in series, never in parallel
- 🎨 **GTK3 GUI** — Vorta-inspired layout (sidebar + tabbed main + bottom bar), with light/dark/system theme and custom font
- ⌨️ **Full CLI** — `warden list / run / status / restore / install-timers` for headless and scripted use
- 🔓 **Runs as user** — no root, no sudo; everything in `~/.config/warden/` and `~/.local/share/warden/`

---

## 📐 Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    GUI (GTK3, user-facing)                   │
│  warden-gui → warden.gui.main → ZBackupApp (Gtk.Window)     │
│  Layout: [ Sidebar A ] [ Tabs B ] [ Bottom bar C ]          │
│  Imports warden.core (never the other way around)           │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                       CLI (headless)                         │
│  warden → warden.cli.main → argparse subcommands            │
│  Used by systemd timers — never loads GTK                   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                       Core (no GTK)                          │
│  config.py    jobs.py       backup.py     rsync_backend.py  │
│  rclone_backend.py  retention.py  locks.py  mounts.py       │
│  log.py  history.py  notify.py  systemd.py                  │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
                    ┌─────────────────────┐
                    │  rsync · rclone     │
                    │  systemd · flock    │
                    │  findmnt · tar      │
                    └─────────────────────┘
```

**Golden rule:** `core/` never imports GTK. This guarantees systemd timers run headless without loading graphical libraries.

---

## 📂 Project structure

```
warden/
├── pyproject.toml              # metadata + entry points (warden, warden-gui)
├── README.md
├── LICENSE
├── warden/                     # Python package
│   ├── __init__.py
│   ├── core/                   # headless logic
│   │   ├── __init__.py
│   │   ├── config.py           # JSON load/save, schema, validation, chmod 0o600
│   │   ├── jobs.py             # discover jobs in jobs.d/, enable/disable
│   │   ├── backup.py           # orchestrate backup (calls rsync/rclone)
│   │   ├── rsync_backend.py    # mirror, snapshots, archive, mirror+snapshots
│   │   ├── rclone_backend.py   # mirror, snapshots (--backup-dir)
│   │   ├── retention.py        # GFS, last-N, unlimited
│   │   ├── locks.py            # flock per destination, 30 min wait
│   │   ├── mounts.py           # findmnt, mount verification
│   │   ├── log.py              # logger with native Python rotation
│   │   ├── history.py          # history.json + state.json per job
│   │   ├── notify.py           # Telegram via urllib (no extra deps)
│   │   └── systemd.py          # generate .service/.timer, daemon-reload
│   ├── cli/
│   │   ├── __init__.py
│   │   └── __main__.py         # warden list/run/status/restore/install-timers
│   └── gui/
│       ├── __init__.py
│       ├── main.py             # ZBackupApp (Gtk.Window, A/B/C layout)
│       ├── css.py              # CSS_DARK, CSS_LIGHT, apply_style()
│       ├── dialogs.py          # DialogoDestino, DialogoSettings, DialogoAbout
│       └── widgets.py          # reusable components
├── data/
│   ├── warden-gui.desktop      # menu entry
│   └── icons/
└── scripts/
    └── install.sh              # symlink to ~/.local/bin, install deps
```

---

## 📋 Configuration

### Global config — `~/.config/warden/config.json`

```json
{
  "version": 1,
  "theme": "sistema",
  "font": "Inter 11",
  "rclone": {
    "binary": "/usr/bin/rclone",
    "default_remote": "gdrive",
    "sa_key_path": "~/.config/warden/gdrive-sa.json"
  },
  "telegram": {
    "enabled": true,
    "bot_token": "123456:ABC-DEF...",
    "chat_id": "987654321",
    "notify_on_success": true,
    "notify_on_partial": true,
    "notify_on_failure": true
  },
  "behavior": {
    "verify_mount_before_local": true,
    "flock_per_destination": true,
    "flock_timeout_minutes": 30,
    "include_size_in_log": true,
    "autostart_gui": false
  }
}
```

Permissions: `chmod 0o600` (contains the Telegram bot token).

### Per-job config — `~/.config/warden/jobs.d/<name>.json`

```json
{
  "name": "documentos",
  "enabled": true,
  "source": "/home/user/Documentos",
  "excludes": [
    "*.tmp",
    "node_modules/",
    ".git/objects/"
  ],
  "destinations": [
    {
      "id": "local-interno",
      "type": "local",
      "path": "/mnt/interno/backup/documentos",
      "mountpoint": "/mnt/interno",
      "mode": "mirror",
      "retention": { "mode": "none" }
    },
    {
      "id": "local-externo",
      "type": "local",
      "path": "/mnt/externo/backup/documentos",
      "mountpoint": "/mnt/externo",
      "mode": "snapshot",
      "retention": { "mode": "gfs", "daily": 7, "weekly": 4, "monthly": 6 }
    },
    {
      "id": "gdrive",
      "type": "rclone",
      "remote": "gdrive:backup/documentos",
      "mode": "snapshot",
      "retention": { "mode": "last_n", "n": 10 }
    }
  ],
  "schedule": {
    "enabled": true,
    "on_calendar": "*-*-* 03:00:00",
    "persistent": true
  }
}
```

To **disable** a task without deleting it: rename `<name>.json` → `<name>.disabled` (systemd-style drop-in).

---

## 🖥️ GUI layout

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Warden                                                          ─ □ ✕   │
├──────────────┬──────────────────────────────────────────────────────────┤
│              │  [ Destinos ] [ Origem ] [ Agendamento ] [ Histórico ]  │
│  Tarefas:    ├──────────────────────────────────────────────────────────┤
│              │                                                          │
│ ▸ documentos │   ┌──────────────────────────────────────────────────┐   │
�   ✓ há 2h    │   │ ID            Tipo     Modo       Retenção  Estado│   │
│ ▸ fotos      │   ├──────────────────────────────────────────────────┤   │
│   ⚠ parcial  │   │ local-interno local    mirror     —         ✓    │   │
│ ▸ projetos   │   │ local-externo local    snapshot   GFS 7/4/6 ⚠    │   │
│   ○ nunca    │   │ gdrive        rclone   snapshot   últimos 10 ✓   │   │
│              │   └──────────────────────────────────────────────────┘   │
│              │                                                          │
│              │   [ + Adicionar ]  [ ✎ Editar ]  [ − Eliminar ]          │
│              │                                                          │
│              │                                                          │
│              │                                                          │
│              │                                                          │
│ [ + ] [ − ] [✎] [⬇] [⬆]                                                 │
├──────────────┴──────────────────────────────────────────────────────────┤
│ [ ⚙ Configurações ]                       [ Cancelar ] [ ▶ Iniciar Backup ]│
└─────────────────────────────────────────────────────────────────────────┘
```

**A — Sidebar (left):** list of tasks with status icon (✓ ⚠ ✗ ○) and last-run info. Buttons at bottom: `+` Adicionar, `−` Eliminar, `✎` Renomear, `⬇` Exportar, `⬆` Importar.

**B — Main area (right):** tabbed details for the selected task.
- **Destinos** — list of destinations, add/edit/remove
- **Origem** — source folder + exclude patterns
- **Agendamento** — enable/disable, OnCalendar schedule, reinstall timer button
- **Histórico** — past runs + snapshots, with "view log" toggle

**C — Bottom bar (full width):**
- Left: `⚙ Configurações` (opens dialog with Aparência / Funcionamento / Sobre)
- Right: `Cancelar` (active only during run) + `▶ Iniciar Backup` (suggested-action blue)

### Settings dialog — 3 tabs

| Tab | Content |
|---|---|
| **Aparência** | Radio: Black / White / Sistema · `FontButton` |
| **Funcionamento** | Frames: Notificações, Discos, Paralelismo, Arranque, rclone, Telegram (with "Testar ligação" / "Testar mensagem") |
| **Sobre** | Version, description, author, license, docs link |

---

## ⌨️ CLI

```bash
# List all tasks (enabled and disabled)
warden list

# Run a task now (manual trigger — same code path as systemd timer)
warden run documentos

# Run only specific destinations of a task
warden run documentos --only local-interno,gdrive

# Show status of all tasks (last run, next scheduled, state per destination)
warden status

# Show status of one task with detailed log tail
warden status documentos --log

# Restore a snapshot
warden restore documentos 2026-10-27T03-00 --to ~/Documentos-restored

# (Re)generate systemd user units for all enabled scheduled tasks
warden install-timers

# (Re)generate systemd unit for one task
warden install-timers documentos

# Validate config files without running anything
warden check
```

All commands work headless — no GTK loaded. `warden run` is what systemd timers call.

---

## ⏰ systemd integration

For each task with `schedule.enabled: true`, Warden generates:

`~/.config/systemd/user/warden-<job>.service`
```ini
[Unit]
Description=Warden backup: <job>

[Service]
Type=oneshot
ExecStart=/usr/bin/warden run <job>
```

`~/.config/systemd/user/warden-<job>.timer`
```ini
[Unit]
Description=Warden schedule: <job>

[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

Then runs `systemctl --user daemon-reload && systemctl --user enable --now warden-<job>.timer`.

Logs appear in `journalctl --user -u warden-<job>` **and** in `~/.local/share/warden/logs/<job>.log`.

---

## 📜 Log system

```
~/.local/share/warden/
├── logs/
│   ├── documentos.log                  # main log, rotated
│   ├── documentos.local-interno.2026-10-27T03-00.log   # rsync verbose
│   └── ...
├── history.json                        # last 100 runs per job
└── state/
    └── documentos.state.json           # current state, last run, next run
```

**Main log format:**
```
2026-10-27T03:00:12+01:00 [INFO] documentos: início (3 destinos)
2026-10-27T03:00:13+01:00 [INFO] documentos/local-interno: rsync iniciado
2026-10-27T03:02:45+01:00 [INFO] documentos/local-interno: 12450 ficheiros, 2.3 GB
2026-10-27T03:02:46+01:00 [WARN] documentos/local-externo: skipped (disco não montado)
2026-10-27T03:04:25+01:00 [INFO] documentos/gdrive: rclone sync concluído
2026-10-27T03:04:26+01:00 [INFO] documentos: concluído em 4m14s
```

**Rotation (native Python, no logrotate dependency):**
- 7 daily files + 4 weekly files
- gzip-compressed when rotated
- per-job, independent

---

## 📲 Telegram notifications

Every backup run sends one message. Format:

```
✅ warden • documentos
3 destinos OK, 0 skipped
2.3 GB • 4m12s
```

```
⚠️ warden • documentos
2 OK, 1 skipped
local-externo: disco não montado
1.8 GB • 3m45s
```

```
❌ warden • documentos
erro: rclone timeout
ver log: ~/.local/share/warden/logs/documentos.log
```

If Telegram is not configured, `notify()` is a no-op (never fails the backup). If the send fails (no network), the failure is logged but does not fail the backup.

---

## 🔑 Google Drive setup (rclone + Service Account)

1. **Create a Google Cloud project** → enable Google Drive API
2. **Create a Service Account** → generate a JSON key → save as `~/.config/warden/gdrive-sa.json` (chmod 0o600)
3. **Create a folder in Drive** (e.g. `backup/`) → share it with the SA email (`xxx@yyy.iam.gserviceaccount.com`)
4. **Configure rclone remote** in `~/.config/rclone/rclone.conf`:
   ```ini
   [gdrive]
   type = drive
   scope = drive
   service_account_file = /home/user/.config/warden/gdrive-sa.json
   team_drive =
   ```
5. **Test** in the GUI: Settings → Funcionamento → rclone → "Testar ligação" (runs `rclone lsd gdrive:`)

No browser OAuth, no token expiry. The Service Account is the identity that accesses the Drive folder — your personal Google account just shares the folder with it.

---

## 📦 Installation

### Dependencies (Debian)

```bash
sudo apt install python3 python3-gi gir1.2-gtk-3.0 rclone rsync tar
```

### Install Warden

```bash
git clone https://github.com/<user>/warden.git
cd warden
pip install --user .
```

Or with `pipx` (recommended for isolated install):

```bash
pipx install .
```

This installs two commands: `warden` (CLI) and `warden-gui` (GTK).

### First run

```bash
# 1. Open the GUI
warden-gui

# 2. Settings → Funcionamento → configure rclone + Telegram

# 3. Sidebar → + to add your first task

# 4. For each task, set Destinos / Origem / Agendamento

# 5. (Re)generate systemd timers
warden install-timers

# 6. Verify
systemctl --user list-timers | grep warden
```

---

## 🧱 Design decisions

| Decision | Choice | Rationale |
|---|---|---|
| Stack | Python + rclone | Structured config, rich logs, no heavy runtime |
| Config | JSON drop-in per job | Each task portable, auditable, enable/disable by rename |
| Backup modes | 4 modes per destination | Mirror / snapshots / archive / mirror+snapshots |
| Retention | Per destination (GFS / last-N / none) | Different destinations have different capacities |
| Scheduling | systemd user timers | Debian standard, `journalctl`, `Persistent=true` |
| Notifications | Log file (always) + Telegram (always) | Silence in backups is dangerous |
| GUI | GTK3, Vorta-inspired | Native Debian look, theme control, drop-in install |
| Drive auth | rclone + Service Account | No browser OAuth, no token expiry, headless-friendly |
| Privileges | User only, no root | systemd user units, all in `~/.` |
| External disk | `findmnt` check before backup | Avoids writing to empty mountpoint (bug #1 in disk backups) |
| Concurrency | `flock` per destination | Parallel tasks on same disk = I/O contention |
| Encryption | None | Backups directly browsable in Drive web |
| Restore | CLI only | Keeps GUI focused on config & execution |
| Catch-up | systemd default | Only the most recent missed run executes |

---

## 🗺️ Roadmap

- [ ] v0.1 — Core backup (rsync mirror + snapshots), CLI, systemd timers
- [ ] v0.2 — rclone backend (Drive), Telegram notifications
- [ ] v0.3 — GTK3 GUI (sidebar + tabs + settings dialog)
- [ ] v0.4 — Compressed archive mode, GFS retention, history view
- [ ] v0.5 — Restore CLI, log viewer in GUI
- [ ] v1.0 — Documentation, Debian package, first stable release
- [ ] Future — Borg backend, S3 backend, restore GUI, multi-language

---

## 🤝 Contributing

PRs welcome. Please open an issue first to discuss the change.

Areas needing help:
- Testing on different Debian versions (stable / testing / sid)
- Translations (pt_BR, es, fr, de)
- Icon design
- Debian packaging (`.deb`)

---

## 📄 License

MIT — see [LICENSE](LICENSE).

---

**Warden** — *guardian of your files.*
