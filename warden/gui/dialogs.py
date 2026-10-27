"""Dialog windows for the Warden GUI.

- DialogoDestino: edit a single destination
- DialogoSettings: app settings (3 tabs: Aparência, Funcionamento, Sobre)
- DialogoAdicionarJob: create a new job
- DialogoLog: view a job's log
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from warden import __version__
from warden.core.config import (
    BackupMode,
    Destination,
    DestinationType,
    RetentionPolicy,
    RetentionMode,
    Config,
)
from warden.core.notify import send_test_message
from warden.core.rclone_backend import test_connection
from warden.core.mounts import list_mounts
from warden.gui.widgets import (
    create_label,
    create_entry,
    create_button,
    create_frame,
    info_dialog,
    warning_dialog,
    error_dialog,
)

try:
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Pango", "1.0")
    from gi.repository import Gtk, Gdk, Pango, GLib
except (ImportError, ValueError):
    Gtk = None
    Gdk = None
    Pango = None
    GLib = None


# ============================================================
# Destination editor
# ============================================================
class DialogoDestino(Gtk.Dialog):
    """Dialog to add/edit a destination."""

    def __init__(self, parent: Any, dest: Destination | None = None):
        super().__init__(
            title="Editar destino" if dest else "Adicionar destino",
            transient_for=parent, modal=True,
        )
        self.set_default_size(550, 480)
        self.add_buttons(
            "_Cancelar", Gtk.ResponseType.CANCEL,
            "_Guardar", Gtk.ResponseType.OK,
        )
        self.set_border_width(10)

        dest = dest or Destination(id="", type=DestinationType.LOCAL)

        grid = Gtk.Grid()
        grid.set_column_spacing(10)
        grid.set_row_spacing(10)
        self.get_content_area().pack_start(grid, True, True, 0)

        # Row 0: ID
        grid.attach(create_label("ID:"), 0, 0, 1, 1)
        self.entry_id = create_entry(dest.id, placeholder="local-interno")
        grid.attach(self.entry_id, 1, 0, 1, 1)

        # Row 1: Type
        grid.attach(create_label("Tipo:"), 0, 1, 1, 1)
        self.combo_type = Gtk.ComboBoxText()
        self.combo_type.append("local", "Disco local (interno/externo)")
        self.combo_type.append("rclone", "rclone (Google Drive / cloud)")
        self.combo_type.set_active_id(dest.type.value)
        self.combo_type.connect("changed", self._on_type_changed)
        grid.attach(self.combo_type, 1, 1, 1, 1)

        # Row 2: Path (local) / Remote (rclone)
        self.label_path = create_label("Caminho:")
        grid.attach(self.label_path, 0, 2, 1, 1)
        self.box_path = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.entry_path = create_entry(dest.path if dest.type == DestinationType.LOCAL
                                       else dest.remote,
                                       placeholder="/mnt/externo/backup/documentos")
        self.box_path.pack_start(self.entry_path, True, True, 0)
        self.button_browse = create_button(label="Procurar…", tooltip="Escolher pasta")
        self.button_browse.connect("clicked", self._on_browse)
        self.box_path.pack_start(self.button_browse, False, False, 0)
        grid.attach(self.box_path, 1, 2, 1, 1)

        # Row 3: Mountpoint (local only)
        self.label_mountpoint = create_label("Mountpoint:")
        grid.attach(self.label_mountpoint, 0, 3, 1, 1)
        self.box_mount = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.entry_mountpoint = create_entry(dest.mountpoint,
                                             placeholder="/mnt/externo")
        self.box_mount.pack_start(self.entry_mountpoint, True, True, 0)
        self.combo_mounts = Gtk.ComboBoxText()
        self.combo_mounts.append("", "(escolher da lista)")
        for m in list_mounts():
            self.combo_mounts.append(m["target"], m["target"])
        self.combo_mounts.set_active_id(dest.mountpoint)
        self.combo_mounts.connect("changed", self._on_mount_selected)
        self.box_mount.pack_start(self.combo_mounts, False, False, 0)
        grid.attach(self.box_mount, 1, 3, 1, 1)

        # Row 4: Mode
        grid.attach(create_label("Modo:"), 0, 4, 1, 1)
        self.combo_mode = Gtk.ComboBoxText()
        self.combo_mode.append("mirror", "Espelho (destino = origem)")
        self.combo_mode.append("snapshot", "Snapshots datados (hardlinks)")
        self.combo_mode.append("archive", "Arquivo comprimido (.tar.gz)")
        self.combo_mode.append("mirror_snapshot", "Espelho + snapshots")
        self.combo_mode.set_active_id(dest.mode.value)
        grid.attach(self.combo_mode, 1, 4, 1, 1)

        # Row 5: Retention
        grid.attach(create_label("Retenção:"), 0, 5, 1, 1)
        self.combo_retention = Gtk.ComboBoxText()
        self.combo_retention.append("none", "Sem limite (manter tudo)")
        self.combo_retention.append("last_n", "Últimos N")
        self.combo_retention.append("gfs", "GFS (diários/semanais/mensais)")
        self.combo_retention.set_active_id(dest.retention.mode.value)
        self.combo_retention.connect("changed", self._on_retention_changed)
        grid.attach(self.combo_retention, 1, 5, 1, 1)

        # Row 6: Retention params
        self.box_retention_params = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=10
        )
        grid.attach(self.box_retention_params, 1, 6, 1, 1)

        # N (for last_n)
        self.label_n = create_label("N:")
        self.spin_n = Gtk.SpinButton.new_with_range(1, 999, 1)
        self.spin_n.set_value(dest.retention.n)
        self.box_retention_params.pack_start(self.label_n, False, False, 0)
        self.box_retention_params.pack_start(self.spin_n, False, False, 0)

        # GFS params
        self.label_daily = create_label("Diários:")
        self.spin_daily = Gtk.SpinButton.new_with_range(1, 365, 1)
        self.spin_daily.set_value(dest.retention.daily)
        self.box_retention_params.pack_start(self.label_daily, False, False, 0)
        self.box_retention_params.pack_start(self.spin_daily, False, False, 0)

        self.label_weekly = create_label("Semanais:")
        self.spin_weekly = Gtk.SpinButton.new_with_range(1, 52, 1)
        self.spin_weekly.set_value(dest.retention.weekly)
        self.box_retention_params.pack_start(self.label_weekly, False, False, 0)
        self.box_retention_params.pack_start(self.spin_weekly, False, False, 0)

        self.label_monthly = create_label("Mensais:")
        self.spin_monthly = Gtk.SpinButton.new_with_range(1, 24, 1)
        self.spin_monthly.set_value(dest.retention.monthly)
        self.box_retention_params.pack_start(self.label_monthly, False, False, 0)
        self.box_retention_params.pack_start(self.spin_monthly, False, False, 0)

        self._on_type_changed(self.combo_type)
        self._on_retention_changed(self.combo_retention)
        self.show_all()

    def _on_type_changed(self, combo: Gtk.ComboBoxText) -> None:
        is_local = combo.get_active_id() == "local"
        if is_local:
            self.label_path.set_text("Caminho:")
            self.entry_path.set_placeholder_text("/mnt/externo/backup/documentos")
            self.button_browse.set_sensitive(True)
            self.label_mountpoint.show()
            self.box_mount.show()
        else:
            self.label_path.set_text("Remote:")
            self.entry_path.set_placeholder_text("gdrive:backup/documentos")
            self.button_browse.set_sensitive(False)
            self.label_mountpoint.hide()
            self.box_mount.hide()

    def _on_retention_changed(self, combo: Gtk.ComboBoxText) -> None:
        mode = combo.get_active_id()
        # Hide all
        for w in (self.label_n, self.spin_n,
                  self.label_daily, self.spin_daily,
                  self.label_weekly, self.spin_weekly,
                  self.label_monthly, self.spin_monthly):
            w.hide()
        if mode == "last_n":
            for w in (self.label_n, self.spin_n):
                w.show()
        elif mode == "gfs":
            for w in (self.label_daily, self.spin_daily,
                      self.label_weekly, self.spin_weekly,
                      self.label_monthly, self.spin_monthly):
                w.show()

    def _on_mount_selected(self, combo: Gtk.ComboBoxText) -> None:
        sel = combo.get_active_id()
        if sel:
            self.entry_mountpoint.set_text(sel)

    def _on_browse(self, _button: Gtk.Button) -> None:
        dialog = Gtk.FileChooserDialog(
            title="Escolher pasta de destino", transient_for=self, modal=True,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        dialog.add_buttons(
            "_Cancelar", Gtk.ResponseType.CANCEL,
            "_Selecionar", Gtk.ResponseType.OK,
        )
        if dialog.run() == Gtk.ResponseType.OK:
            path = dialog.get_filename()
            self.entry_path.set_text(path)
            # Auto-fill mountpoint if path starts with /mnt or /media
            if path.startswith(("/mnt/", "/media/")):
                parts = path.split("/")
                if len(parts) >= 3:
                    self.entry_mountpoint.set_text("/" + parts[1] + "/" + parts[2])
        dialog.destroy()

    def get_destination(self) -> Destination:
        """Return a Destination built from the dialog fields."""
        type_id = self.combo_type.get_active_id()
        retention_mode = RetentionMode(self.combo_retention.get_active_id())
        retention = RetentionPolicy(
            mode=retention_mode,
            n=self.spin_n.get_value_as_int(),
            daily=self.spin_daily.get_value_as_int(),
            weekly=self.spin_weekly.get_value_as_int(),
            monthly=self.spin_monthly.get_value_as_int(),
        )
        dest = Destination(
            id=self.entry_id.get_text().strip(),
            type=DestinationType(type_id),
            mode=BackupMode(self.combo_mode.get_active_id()),
            retention=retention,
        )
        if dest.type == DestinationType.LOCAL:
            dest.path = self.entry_path.get_text().strip()
            dest.mountpoint = self.entry_mountpoint.get_text().strip()
        else:
            dest.remote = self.entry_path.get_text().strip()
        return dest


# ============================================================
# Add new job dialog
# ============================================================
class DialogoAdicionarJob(Gtk.Dialog):
    """Dialog to create a new job (asks for name + source)."""

    def __init__(self, parent: Any):
        super().__init__(
            title="Nova tarefa de backup",
            transient_for=parent, modal=True,
        )
        self.set_default_size(500, 200)
        self.add_buttons(
            "_Cancelar", Gtk.ResponseType.CANCEL,
            "_Criar", Gtk.ResponseType.OK,
        )
        self.set_border_width(10)

        grid = Gtk.Grid()
        grid.set_column_spacing(10)
        grid.set_row_spacing(10)
        self.get_content_area().pack_start(grid, True, True, 0)

        grid.attach(create_label("Nome:"), 0, 0, 1, 1)
        self.entry_name = create_entry(placeholder="documentos")
        grid.attach(self.entry_name, 1, 0, 1, 1)

        grid.attach(create_label("Pasta origem:"), 0, 1, 1, 1)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.entry_source = create_entry(placeholder="/home/user/Documentos")
        box.pack_start(self.entry_source, True, True, 0)
        button = create_button(label="Procurar…")
        button.connect("clicked", self._on_browse)
        box.pack_start(button, False, False, 0)
        grid.attach(box, 1, 1, 1, 1)

        note = create_label(
            "O nome deve ter apenas letras minúsculas, números, hífens e underscores.",
            dim=True,
        )
        grid.attach(note, 0, 2, 2, 1)
        self.show_all()

    def _on_browse(self, _button: Gtk.Button) -> None:
        dialog = Gtk.FileChooserDialog(
            title="Escolher pasta de origem", transient_for=self, modal=True,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        dialog.add_buttons(
            "_Cancelar", Gtk.ResponseType.CANCEL,
            "_Selecionar", Gtk.ResponseType.OK,
        )
        if dialog.run() == Gtk.ResponseType.OK:
            self.entry_source.set_text(dialog.get_filename())
        dialog.destroy()

    def get_name(self) -> str:
        return self.entry_name.get_text().strip()

    def get_source(self) -> str:
        return self.entry_source.get_text().strip()


# ============================================================
# Rename job dialog
# ============================================================
class DialogoRenomear(Gtk.Dialog):
    """Dialog to rename a job."""

    def __init__(self, parent: Any, current_name: str):
        super().__init__(
            title="Renomear tarefa",
            transient_for=parent, modal=True,
        )
        self.set_default_size(400, 150)
        self.add_buttons(
            "_Cancelar", Gtk.ResponseType.CANCEL,
            "_Renomear", Gtk.ResponseType.OK,
        )
        self.set_border_width(10)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.get_content_area().pack_start(box, True, True, 0)

        box.pack_start(create_label("Novo nome:"), False, False, 0)
        self.entry = create_entry(current_name)
        box.pack_start(self.entry, False, False, 0)

        note = create_label(
            "Apenas letras minúsculas, números, hífens e underscores.",
            dim=True,
        )
        box.pack_start(note, False, False, 0)
        self.show_all()

    def get_name(self) -> str:
        return self.entry.get_text().strip()


# ============================================================
# Settings dialog (3 tabs)
# ============================================================
class DialogoSettings(Gtk.Dialog):
    """Application settings — 3 tabs: Aparência, Funcionamento, Sobre."""

    def __init__(self, parent: Any, config: Config):
        super().__init__(
            title="Configurações", transient_for=parent, modal=True,
        )
        self.set_default_size(640, 560)
        self.add_buttons(
            "_Cancelar", Gtk.ResponseType.CANCEL,
            "_Guardar", Gtk.ResponseType.OK,
        )
        self.set_border_width(10)
        self.config = config

        notebook = Gtk.Notebook()
        self.get_content_area().pack_start(notebook, True, True, 0)

        self._build_aparencia_tab(notebook)
        self._build_funcionamento_tab(notebook)
        self._build_sobre_tab(notebook)

        self.show_all()

    # ---------- Aparência ----------
    def _build_aparencia_tab(self, notebook: Gtk.Notebook) -> None:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        vbox.set_border_width(12)
        notebook.append_page(vbox, Gtk.Label(label="Aparência"))

        frame = create_frame("Tema")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        inner.set_border_width(10)
        frame.add(inner)
        vbox.pack_start(frame, False, False, 0)

        self.radio_dark = Gtk.RadioButton.new_with_label(None, "Escuro")
        self.radio_light = Gtk.RadioButton.new_with_label_from_widget(
            self.radio_dark, "Claro"
        )
        self.radio_system = Gtk.RadioButton.new_with_label_from_widget(
            self.radio_dark, "Sistema (seguir GTK)"
        )
        theme = self.config.theme
        if theme == "black":
            self.radio_dark.set_active(True)
        elif theme == "white":
            self.radio_light.set_active(True)
        else:
            self.radio_system.set_active(True)
        for r in (self.radio_dark, self.radio_light, self.radio_system):
            inner.pack_start(r, False, False, 0)

        frame = create_frame("Tipo de letra")
        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        inner.set_border_width(10)
        frame.add(inner)
        vbox.pack_start(frame, False, False, 0)

        inner.pack_start(create_label("Fonte:"), False, False, 0)
        self.font_button = Gtk.FontButton()
        if self.config.font:
            self.font_button.set_font(self.config.font)
        inner.pack_start(self.font_button, True, True, 0)

    # ---------- Funcionamento ----------
    def _build_funcionamento_tab(self, notebook: Gtk.Notebook) -> None:
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        vbox.set_border_width(12)
        scrolled.add(vbox)
        notebook.append_page(scrolled, Gtk.Label(label="Funcionamento"))

        # --- Discos ---
        frame = create_frame("Discos")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        inner.set_border_width(10)
        frame.add(inner)
        vbox.pack_start(frame, False, False, 0)
        self.check_verify_mount = Gtk.CheckButton(
            label="Verificar mount antes de backup local (recomendado)"
        )
        self.check_verify_mount.set_active(
            self.config.behavior.verify_mount_before_local
        )
        inner.pack_start(self.check_verify_mount, False, False, 0)

        # --- Paralelismo ---
        frame = create_frame("Paralelismo")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        inner.set_border_width(10)
        frame.add(inner)
        vbox.pack_start(frame, False, False, 0)
        self.check_flock = Gtk.CheckButton(
            label="flock por destino (série) — evita contenção de I/O"
        )
        self.check_flock.set_active(self.config.behavior.flock_per_destination)
        inner.pack_start(self.check_flock, False, False, 0)
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        hbox.pack_start(create_label("Timeout (minutos):"), False, False, 0)
        self.spin_flock_timeout = Gtk.SpinButton.new_with_range(1, 240, 5)
        self.spin_flock_timeout.set_value(
            self.config.behavior.flock_timeout_minutes
        )
        hbox.pack_start(self.spin_flock_timeout, False, False, 0)
        inner.pack_start(hbox, False, False, 0)

        # --- Logs ---
        frame = create_frame("Logs")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        inner.set_border_width(10)
        frame.add(inner)
        vbox.pack_start(frame, False, False, 0)
        self.check_size_log = Gtk.CheckButton(
            label="Incluir tamanho total no log e notificações"
        )
        self.check_size_log.set_active(self.config.behavior.include_size_in_log)
        inner.pack_start(self.check_size_log, False, False, 0)

        # --- Arranque ---
        frame = create_frame("Arranque")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        inner.set_border_width(10)
        frame.add(inner)
        vbox.pack_start(frame, False, False, 0)
        self.check_autostart = Gtk.CheckButton(
            label="Iniciar warden-gui com o sistema"
        )
        self.check_autostart.set_active(self.config.behavior.autostart_gui)
        inner.pack_start(self.check_autostart, False, False, 0)

        # --- rclone ---
        frame = create_frame("rclone")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        inner.set_border_width(10)
        frame.add(inner)
        vbox.pack_start(frame, False, False, 0)

        grid = Gtk.Grid()
        grid.set_column_spacing(10)
        grid.set_row_spacing(6)
        inner.pack_start(grid, False, False, 0)

        grid.attach(create_label("Binário:"), 0, 0, 1, 1)
        self.entry_rclone_bin = create_entry(self.config.rclone.binary,
                                             placeholder="/usr/bin/rclone")
        grid.attach(self.entry_rclone_bin, 1, 0, 1, 1)

        grid.attach(create_label("Remote padrão:"), 0, 1, 1, 1)
        self.entry_rclone_remote = create_entry(
            self.config.rclone.default_remote, placeholder="gdrive"
        )
        grid.attach(self.entry_rclone_remote, 1, 1, 1, 1)

        grid.attach(create_label("SA JSON:"), 0, 2, 1, 1)
        self.entry_rclone_sa = create_entry(
            self.config.rclone.sa_key_path,
            placeholder="~/.config/warden/gdrive-sa.json",
        )
        grid.attach(self.entry_rclone_sa, 1, 2, 1, 1)

        button_test = create_button(label="Testar ligação")
        button_test.connect("clicked", self._on_test_rclone)
        inner.pack_start(button_test, False, False, 0)

        # --- Telegram ---
        frame = create_frame("Telegram")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        inner.set_border_width(10)
        frame.add(inner)
        vbox.pack_start(frame, False, False, 0)

        self.check_telegram = Gtk.CheckButton(label="Ativar notificações Telegram")
        self.check_telegram.set_active(self.config.telegram.enabled)
        inner.pack_start(self.check_telegram, False, False, 0)

        grid = Gtk.Grid()
        grid.set_column_spacing(10)
        grid.set_row_spacing(6)
        inner.pack_start(grid, False, False, 0)

        grid.attach(create_label("Bot token:"), 0, 0, 1, 1)
        self.entry_tg_token = create_entry(
            self.config.telegram.bot_token,
            placeholder="123456:ABC-DEF...", visibility=False,
        )
        grid.attach(self.entry_tg_token, 1, 0, 1, 1)

        grid.attach(create_label("Chat ID:"), 0, 1, 1, 1)
        self.entry_tg_chat = create_entry(
            self.config.telegram.chat_id, placeholder="987654321"
        )
        grid.attach(self.entry_tg_chat, 1, 1, 1, 1)

        # Notification preferences
        self.check_tg_success = Gtk.CheckButton(label="Notificar em sucessos")
        self.check_tg_success.set_active(self.config.telegram.notify_on_success)
        inner.pack_start(self.check_tg_success, False, False, 0)
        self.check_tg_partial = Gtk.CheckButton(label="Notificar em parciais")
        self.check_tg_partial.set_active(self.config.telegram.notify_on_partial)
        inner.pack_start(self.check_tg_partial, False, False, 0)
        self.check_tg_failure = Gtk.CheckButton(label="Notificar em falhas")
        self.check_tg_failure.set_active(self.config.telegram.notify_on_failure)
        inner.pack_start(self.check_tg_failure, False, False, 0)

        button_test_tg = create_button(label="Testar mensagem")
        button_test_tg.connect("clicked", self._on_test_telegram)
        inner.pack_start(button_test_tg, False, False, 0)

    # ---------- Sobre ----------
    def _build_sobre_tab(self, notebook: Gtk.Notebook) -> None:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        vbox.set_border_width(20)
        notebook.append_page(vbox, Gtk.Label(label="Sobre"))

        title = Gtk.Label()
        title.set_markup(
            f"<span size='xx-large' weight='bold'>🔐 Warden</span>\n"
            f"<span size='large' color='#666666'>v{__version__}</span>"
        )
        title.set_justify(Gtk.Justification.CENTER)
        vbox.pack_start(title, False, False, 0)

        desc = Gtk.Label(
            label="Guardian of your files — local and Google Drive backup for Debian."
        )
        desc.set_justify(Gtk.Justification.CENTER)
        desc.set_line_wrap(True)
        vbox.pack_start(desc, False, False, 0)

        vbox.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL),
                        False, False, 0)

        # Usage instructions
        frame = create_frame("Como usar")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        inner.set_border_width(10)
        frame.add(inner)
        vbox.pack_start(frame, False, False, 0)

        instructions = [
            "1. Na barra lateral, clique em + para adicionar uma tarefa.",
            "2. Em 'Destinos', adicione os destinos (interno, externo, Drive).",
            "3. Em 'Origem', defina a pasta e os padrões de exclusão.",
            "4. Em 'Agendamento', ative o agendamento (systemd timer).",
            "5. Clique em 'Iniciar Backup' para uma execução manual.",
            "6. Clique em 'Reinstalar timers' para ativar os systemd timers.",
            "",
            "CLI: warden list / run / status / restore / install-timers",
            "Logs: ~/.local/share/warden/logs/",
            "Config: ~/.config/warden/",
        ]
        for line in instructions:
            inner.pack_start(create_label(line), False, False, 0)

        # Author
        vbox.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL),
                        False, False, 0)
        author = Gtk.Label()
        author.set_markup(
            "<span color='#666666'>License: MIT</span>\n"
            "<span color='#666666'>https://github.com/warden-backup/warden</span>"
        )
        author.set_justify(Gtk.Justification.CENTER)
        vbox.pack_start(author, False, False, 0)

    # ---------- Actions ----------
    def _on_test_rclone(self, _button: Gtk.Button) -> None:
        from warden.core.config import RcloneConfig
        rc = RcloneConfig(
            binary=self.entry_rclone_bin.get_text().strip() or "/usr/bin/rclone",
            default_remote=self.entry_rclone_remote.get_text().strip() or "gdrive",
            sa_key_path=self.entry_rclone_sa.get_text().strip(),
        )
        remote = f"{rc.default_remote}:"
        ok, msg = test_connection(rc, remote)
        if ok:
            info_dialog(self, "rclone", msg)
        else:
            error_dialog(self, "rclone", msg)

    def _on_test_telegram(self, _button: Gtk.Button) -> None:
        from warden.core.config import TelegramConfig
        tg = TelegramConfig(
            enabled=True,
            bot_token=self.entry_tg_token.get_text().strip(),
            chat_id=self.entry_tg_chat.get_text().strip(),
        )
        if not tg.bot_token or not tg.chat_id:
            warning_dialog(self, "Telegram", "Bot token e Chat ID são obrigatórios.")
            return
        ok, msg = send_test_message(tg)
        if ok:
            info_dialog(self, "Telegram", "Mensagem enviada com sucesso.")
        else:
            error_dialog(self, "Telegram", msg)

    def get_config(self) -> Config:
        """Return updated config from the dialog."""
        config = self.config
        # Theme
        if self.radio_dark.get_active():
            config.theme = "black"
        elif self.radio_light.get_active():
            config.theme = "white"
        else:
            config.theme = "sistema"
        config.font = self.font_button.get_font()

        # Behavior
        config.behavior.verify_mount_before_local = self.check_verify_mount.get_active()
        config.behavior.flock_per_destination = self.check_flock.get_active()
        config.behavior.flock_timeout_minutes = self.spin_flock_timeout.get_value_as_int()
        config.behavior.include_size_in_log = self.check_size_log.get_active()
        config.behavior.autostart_gui = self.check_autostart.get_active()

        # rclone
        config.rclone.binary = self.entry_rclone_bin.get_text().strip() or "/usr/bin/rclone"
        config.rclone.default_remote = self.entry_rclone_remote.get_text().strip() or "gdrive"
        config.rclone.sa_key_path = self.entry_rclone_sa.get_text().strip()

        # Telegram
        config.telegram.enabled = self.check_telegram.get_active()
        config.telegram.bot_token = self.entry_tg_token.get_text().strip()
        config.telegram.chat_id = self.entry_tg_chat.get_text().strip()
        config.telegram.notify_on_success = self.check_tg_success.get_active()
        config.telegram.notify_on_partial = self.check_tg_partial.get_active()
        config.telegram.notify_on_failure = self.check_tg_failure.get_active()

        return config
