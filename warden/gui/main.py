"""Main Warden GTK3 application window.

Layout (Vorta-inspired):
  +--------------------------------------------------------+
  |  Window title bar                                       |
  +------------+-------------------------------------------+
  |  Sidebar A |  Notebook B (tabs)                       |
  |  (jobs)    |                                           |
  |  [+] [-]   |                                           |
  |  [rename]  |                                           |
  |  [import]  |                                           |
  |  [export]  |                                           |
  +------------+-------------------------------------------+
  |  [Settings]              [Cancel] [Start Backup]      |  C
  +--------------------------------------------------------+
"""

from __future__ import annotations

import json
import os
import queue
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from warden import __version__
from warden.core import (
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
    load_config,
    save_config,
)
from warden.core.backup import run_backup, BackupResult
from warden.core.config import (
    BackupMode,
    Config,
    Destination,
    DestinationType,
    JobConfig,
)
from warden.core.history import get_job_history, load_state
from warden.core.jobs import find_job_path, slugify, validate_job
from warden.core.log import tail_log, log_file_path
from warden.core.systemd import (
    generate_unit_files,
    enable_timer,
    disable_timer,
    daemon_reload,
    get_timer_status,
    install_all_timers,
)
from warden.gui.css import apply_theme
from warden.gui.dialogs import (
    DialogoDestino,
    DialogoAdicionarJob,
    DialogoRenomear,
    DialogoSettings,
)
from warden.gui.widgets import (
    create_label,
    create_button,
    create_entry,
    create_frame,
    info_dialog,
    warning_dialog,
    error_dialog,
    confirm_dialog,
    text_viewer_dialog,
    human_size,
    human_duration,
    status_icon,
    status_class,
    GTK_AVAILABLE,
)

# Try to import GTK; if it fails, this module is importable but not runnable
try:
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Pango", "1.0")
    from gi.repository import Gtk, Gdk, Pango, GLib
except (ImportError, ValueError):
    if not GTK_AVAILABLE:
        Gtk = None
        Gdk = None
        Pango = None
        GLib = None


class WardenApp(Gtk.Window):
    """Main Warden application window."""

    def __init__(self):
        super().__init__(title="Warden")
        self.set_default_size(980, 700)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_border_width(8)

        # State
        self.config = load_config()
        self.current_job_slug: str | None = None
        self.events: queue.Queue = queue.Queue()
        self.running = False

        # Apply theme
        apply_theme(self.config.theme, self.config.font)

        # Build UI
        self._build_ui()

        # Connect signals
        self.connect("delete-event", self._on_close)

        # Start event polling
        GLib.timeout_add(100, self._poll_events)

        # Initial load
        self._refresh_jobs_list()

    # ============================================================
    # UI construction
    # ============================================================
    def _build_ui(self) -> None:
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.add(main_box)

        # Header
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        title = Gtk.Label()
        title.set_markup(
            "<span size='large' weight='bold'>🔐 Warden</span>"
            " <span color='#888888' size='small'>— guardian of your files</span>"
        )
        title.set_xalign(0)
        header.pack_start(title, True, True, 0)
        main_box.pack_start(header, False, False, 0)

        # Horizontal paned: A (sidebar) | B (main)
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_position(240)
        main_box.pack_start(paned, True, True, 0)

        paned.pack1(self._build_sidebar(), resize=False, shrink=False)
        paned.pack2(self._build_main_area(), resize=True, shrink=False)

        # Bottom bar C
        main_box.pack_start(self._build_bottom_bar(), False, False, 0)

    # ---------- Sidebar A ----------
    def _build_sidebar(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        # Header
        header = Gtk.Label()
        header.set_markup("<b>Tarefas</b>")
        header.set_xalign(0)
        vbox.pack_start(header, False, False, 0)

        # Jobs list
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.jobs_listbox = Gtk.ListBox()
        self.jobs_listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.jobs_listbox.connect("row-selected", self._on_job_selected)
        scrolled.add(self.jobs_listbox)
        vbox.pack_start(scrolled, True, True, 0)

        # Buttons at bottom of sidebar
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        for icon, tooltip, callback in (
            ("list-add", "Adicionar tarefa", self._on_add_job),
            ("list-remove", "Eliminar tarefa", self._on_delete_job),
            ("edit-rename", "Renomear tarefa", self._on_rename_job),
            ("go-down", "Exportar tarefa", self._on_export_job),
            ("go-up", "Importar tarefa", self._on_import_job),
        ):
            btn = Gtk.Button.new_from_icon_name(icon, Gtk.IconSize.BUTTON)
            btn.set_tooltip_text(tooltip)
            btn.connect("clicked", callback)
            button_box.pack_start(btn, True, True, 0)
        vbox.pack_start(button_box, False, False, 0)

        return vbox

    # ---------- Main area B ----------
    def _build_main_area(self) -> Gtk.Widget:
        self.notebook = Gtk.Notebook()

        # Tab 1: Destinos
        self.notebook.append_page(
            self._build_destinos_tab(), Gtk.Label(label="Destinos")
        )
        # Tab 2: Origem
        self.notebook.append_page(
            self._build_origem_tab(), Gtk.Label(label="Origem")
        )
        # Tab 3: Agendamento
        self.notebook.append_page(
            self._build_agendamento_tab(), Gtk.Label(label="Agendamento")
        )
        # Tab 4: Histórico
        self.notebook.append_page(
            self._build_historico_tab(), Gtk.Label(label="Histórico")
        )

        return self.notebook

    def _build_destinos_tab(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vbox.set_border_width(10)

        # TreeView of destinations
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.dest_model = Gtk.ListStore(str, str, str, str, str)
        # Columns: ID, Type, Mode, Retention, Status
        self.dest_treeview = Gtk.TreeView(model=self.dest_model)
        for i, title in enumerate(
            ("ID", "Tipo", "Modo", "Retenção", "Estado")
        ):
            renderer = Gtk.CellRendererText()
            col = Gtk.TreeViewColumn(title, renderer, text=i)
            col.set_resizable(True)
            if i == 0:
                col.set_min_width(150)
            self.dest_treeview.append_column(col)
        scrolled.add(self.dest_treeview)
        scrolled.set_min_content_height(220)
        vbox.pack_start(scrolled, True, True, 0)

        # Buttons
        bbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.btn_add_dest = create_button(label="Adicionar")
        self.btn_add_dest.connect("clicked", self._on_add_dest)
        bbox.pack_start(self.btn_add_dest, False, False, 0)
        self.btn_edit_dest = create_button(label="Editar")
        self.btn_edit_dest.connect("clicked", self._on_edit_dest)
        bbox.pack_start(self.btn_edit_dest, False, False, 0)
        self.btn_del_dest = create_button(label="Eliminar")
        self.btn_del_dest.connect("clicked", self._on_del_dest)
        bbox.pack_start(self.btn_del_dest, False, False, 0)
        vbox.pack_start(bbox, False, False, 0)

        return vbox

    def _build_origem_tab(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vbox.set_border_width(10)

        grid = Gtk.Grid()
        grid.set_column_spacing(10)
        grid.set_row_spacing(10)
        vbox.pack_start(grid, False, False, 0)

        grid.attach(create_label("Pasta origem:"), 0, 0, 1, 1)
        self.entry_source = create_entry(placeholder="/home/user/Documentos")
        grid.attach(self.entry_source, 1, 0, 1, 1)
        btn = create_button(label="Procurar…")
        btn.connect("clicked", self._on_browse_source)
        grid.attach(btn, 2, 0, 1, 1)

        grid.attach(create_label("Exclusões:"), 0, 1, 1, 1)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_min_content_height(180)
        self.excludes_buffer = Gtk.TextBuffer()
        textview = Gtk.TextView(buffer=self.excludes_buffer)
        textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        scrolled.add(textview)
        grid.attach(scrolled, 1, 1, 2, 1)

        note = create_label(
            "Um padrão por linha. Exemplos: *.tmp, node_modules/, .git/objects/",
            dim=True,
        )
        grid.attach(note, 0, 2, 3, 1)

        # Save button
        bbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn = create_button(label="Guardar origem")
        btn.connect("clicked", self._on_save_source)
        bbox.pack_start(btn, False, False, 0)
        vbox.pack_start(bbox, False, False, 0)

        return vbox

    def _build_agendamento_tab(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vbox.set_border_width(10)

        frame = create_frame("Agendamento (systemd timer)")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        inner.set_border_width(10)
        frame.add(inner)
        vbox.pack_start(frame, False, False, 0)

        self.check_schedule_enabled = Gtk.CheckButton(label="Ativar agendamento")
        inner.pack_start(self.check_schedule_enabled, False, False, 0)

        grid = Gtk.Grid()
        grid.set_column_spacing(10)
        grid.set_row_spacing(6)
        inner.pack_start(grid, False, False, 0)

        grid.attach(create_label("OnCalendar:"), 0, 0, 1, 1)
        self.entry_on_calendar = create_entry(
            "*-*-* 03:00:00", placeholder="*-*-* 03:00:00"
        )
        grid.attach(self.entry_on_calendar, 1, 0, 1, 1)

        grid.attach(create_label(""), 0, 1, 1, 1)
        note = create_label(
            "Formato systemd: *-*-* 03:00:00 (diário às 3h), "
            "Mon *-*-* 03:00:00 (segundas), etc.",
            dim=True,
        )
        grid.attach(note, 1, 1, 1, 1)

        self.check_persistent = Gtk.CheckButton(
            label="Persistent (corrige backups perdidos quando a máquina estava off)"
        )
        self.check_persistent.set_active(True)
        inner.pack_start(self.check_persistent, False, False, 0)

        # Timer status
        frame = create_frame("Estado do timer systemd")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        inner.set_border_width(10)
        frame.add(inner)
        vbox.pack_start(frame, False, False, 0)

        self.label_timer_status = create_label("(sem tarefa selecionada)", dim=True)
        inner.pack_start(self.label_timer_status, False, False, 0)

        bbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn = create_button(label="Guardar e reinstalar timer")
        btn.connect("clicked", self._on_save_schedule)
        bbox.pack_start(btn, False, False, 0)
        btn = create_button(label="Reinstalar todos os timers")
        btn.connect("clicked", self._on_reinstall_all_timers)
        bbox.pack_start(btn, False, False, 0)
        inner.pack_start(bbox, False, False, 0)

        return vbox

    def _build_historico_tab(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vbox.set_border_width(10)

        # History TreeView
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.history_model = Gtk.ListStore(str, str, str, str, str)
        # Columns: Date, Status, OK/Skip/Fail, Size, Duration
        self.history_treeview = Gtk.TreeView(model=self.history_model)
        for i, title in enumerate(
            ("Data", "Estado", "OK/Skip/Fail", "Tamanho", "Duração")
        ):
            renderer = Gtk.CellRendererText()
            col = Gtk.TreeViewColumn(title, renderer, text=i)
            col.set_resizable(True)
            self.history_treeview.append_column(col)
        scrolled.add(self.history_treeview)
        vbox.pack_start(scrolled, True, True, 0)

        # Log viewer button
        bbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn = create_button(label="Ver log")
        btn.connect("clicked", self._on_view_log)
        bbox.pack_start(btn, False, False, 0)
        vbox.pack_start(bbox, False, False, 0)

        return vbox

    # ---------- Bottom bar C ----------
    def _build_bottom_bar(self) -> Gtk.Widget:
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        # Left: Settings button (aligned with sidebar A)
        self.btn_settings = create_button(label="⚙ Configurações")
        self.btn_settings.connect("clicked", self._on_settings)
        hbox.pack_start(self.btn_settings, False, False, 0)

        # Center: progress bar (visible during run)
        self.progress = Gtk.ProgressBar()
        self.progress.set_show_text(True)
        self.progress.set_no_show_all(True)
        hbox.pack_start(self.progress, True, True, 0)

        # Right: Cancel + Start
        right_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.btn_cancel = create_button(label="Cancelar")
        self.btn_cancel.set_sensitive(False)
        self.btn_cancel.connect("clicked", self._on_cancel)
        right_box.pack_start(self.btn_cancel, False, False, 0)

        self.btn_start = create_button(label="▶ Iniciar Backup",
                                       style="suggested-action")
        self.btn_start.connect("clicked", self._on_start_backup)
        right_box.pack_start(self.btn_start, False, False, 0)
        hbox.pack_end(right_box, False, False, 0)

        return hbox

    # ============================================================
    # Jobs list
    # ============================================================
    def _refresh_jobs_list(self) -> None:
        """Refresh the sidebar jobs list."""
        for row in list(self.jobs_listbox.get_children()):
            self.jobs_listbox.remove(row)

        jobs = list_jobs()
        if not jobs:
            row = Gtk.ListBoxRow()
            row.set_sensitive(False)
            label = create_label("(sem tarefas)", dim=True)
            row.add(label)
            self.jobs_listbox.add(row)
        else:
            for slug, enabled in jobs:
                row = self._create_job_row(slug, enabled)
                self.jobs_listbox.add(row)
        self.jobs_listbox.show_all()

        # Re-select current job if still present
        if self.current_job_slug:
            for i, row in enumerate(self.jobs_listbox.get_children()):
                if row.get_data("slug") == self.current_job_slug:
                    self.jobs_listbox.select_row(row)
                    return
        # Otherwise clear main area
        self.current_job_slug = None
        self._clear_main_area()

    def _create_job_row(self, slug: str, enabled: bool) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.set_data("slug", slug)
        row.set_data("enabled", enabled)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(8)
        box.set_margin_end(8)

        # Job name + status icon
        name_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        state = load_state(slug)
        icon = status_icon(state.last_status)
        icon_label = Gtk.Label(label=icon)
        icon_label.get_style_context().add_class(status_class(state.last_status))
        name_box.pack_start(icon_label, False, False, 0)

        name_label = Gtk.Label(label=slug)
        name_label.set_xalign(0)
        name_label.set_halign(Gtk.Align.START)
        if not enabled:
            name_label.set_markup(f"<i>{slug}</i>")
        name_box.pack_start(name_label, True, True, 0)

        box.pack_start(name_box, False, False, 0)

        # Last run info
        last = ""
        if state.last_run:
            try:
                dt = datetime.fromisoformat(state.last_run)
                last = dt.strftime("%Y-%m-%d %H:%M")
            except ValueError:
                last = state.last_run
        else:
            last = "nunca"
        sub_label = create_label(last, dim=True)
        sub_label.get_style_context().add_class("dim-label")
        box.pack_start(sub_label, False, False, 0)

        row.add(box)
        return row

    def _on_job_selected(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        if row is None:
            self.current_job_slug = None
            self._clear_main_area()
            return
        self.current_job_slug = row.get_data("slug")
        self._load_job_to_ui()

    # ============================================================
    # Load job to UI / save UI to job
    # ============================================================
    def _clear_main_area(self) -> None:
        self.dest_model.clear()
        self.entry_source.set_text("")
        self.excludes_buffer.set_text("")
        self.check_schedule_enabled.set_active(False)
        self.entry_on_calendar.set_text("*-*-* 03:00:00")
        self.check_persistent.set_active(True)
        self.history_model.clear()
        self.label_timer_status.set_text("(sem tarefa selecionada)")
        self.btn_start.set_sensitive(False)

    def _load_job_to_ui(self) -> None:
        """Load the currently-selected job into all tabs."""
        if not self.current_job_slug:
            self._clear_main_area()
            return
        job = load_job(self.current_job_slug)
        if job is None:
            self._clear_main_area()
            return

        # Destinos tab
        self.dest_model.clear()
        state = load_state(job.name)
        for d in job.destinations:
            dest_state = state.destinations_state.get(d.id, "-")
            status_text = {
                "ok": "✓ ok",
                "skipped": "⏭ skipped",
                "failed": "✗ failed",
            }.get(dest_state, "—")
            self.dest_model.append([
                d.id,
                d.type.value,
                d.mode.value,
                d.retention.mode.value,
                status_text,
            ])

        # Origem tab
        self.entry_source.set_text(job.source)
        self.excludes_buffer.set_text("\n".join(job.excludes))

        # Agendamento tab
        self.check_schedule_enabled.set_active(job.schedule_enabled)
        self.entry_on_calendar.set_text(job.schedule_on_calendar)
        self.check_persistent.set_active(job.schedule_persistent)
        # Timer status
        timer_status = get_timer_status(job.name)
        status_text = (
            f"Timer systemd: {'enabled' if timer_status['enabled'] else 'disabled'}, "
            f"{'active' if timer_status['active'] else 'inactive'}\n"
            f"Próxima execução: {timer_status.get('next_trigger', '-')}\n"
            f"Última execução: {timer_status.get('last_trigger', '-')}"
        )
        self.label_timer_status.set_text(status_text)

        # Histórico tab
        self._refresh_history()

        # Enable Start button
        self.btn_start.set_sensitive(not self.running)

    def _refresh_history(self) -> None:
        self.history_model.clear()
        if not self.current_job_slug:
            return
        history = get_job_history(self.current_job_slug, limit=50)
        for r in history:
            try:
                dt = datetime.fromisoformat(r.finished_at)
                date_str = dt.strftime("%Y-%m-%d %H:%M")
            except ValueError:
                date_str = r.finished_at
            ok = len(r.destinations_ok)
            sk = len(r.destinations_skipped)
            fa = len(r.destinations_failed)
            self.history_model.append([
                date_str,
                r.status,
                f"{ok}/{sk}/{fa}",
                human_size(r.total_size_bytes),
                human_duration(r.duration_seconds),
            ])

    def _get_job_from_ui(self) -> JobConfig | None:
        """Build a JobConfig from the UI fields."""
        if not self.current_job_slug:
            return None
        existing = load_job(self.current_job_slug)
        if existing is None:
            return None

        # Update from UI
        existing.source = self.entry_source.get_text().strip()
        excludes_text = self.excludes_buffer.get_text(
            *self.excludes_buffer.get_bounds(), include_hidden_chars=False
        )
        existing.excludes = [
            line.strip() for line in excludes_text.splitlines() if line.strip()
        ]
        existing.schedule_enabled = self.check_schedule_enabled.get_active()
        existing.schedule_on_calendar = self.entry_on_calendar.get_text().strip()
        existing.schedule_persistent = self.check_persistent.get_active()
        return existing

    # ============================================================
    # Sidebar actions
    # ============================================================
    def _on_add_job(self, _button) -> None:
        dialog = DialogoAdicionarJob(self)
        if dialog.run() == Gtk.ResponseType.OK:
            name = dialog.get_name()
            source = dialog.get_source()
            dialog.destroy()
            if not name:
                warning_dialog(self, "Nome obrigatório",
                               "Indique um nome para a tarefa.")
                return
            slug = slugify(name)
            from warden.core.jobs import is_valid_slug, find_job_path
            if not is_valid_slug(slug):
                error_dialog(self, "Nome inválido",
                             f"Nome inválido após normalização: '{slug}'")
                return
            if find_job_path(slug) is not None:
                error_dialog(self, "Já existe",
                             f"Já existe uma tarefa com o nome '{slug}'.")
                return
            job = JobConfig(name=slug, source=source, enabled=True)
            save_job(job)
            self.current_job_slug = slug
            self._refresh_jobs_list()
        else:
            dialog.destroy()

    def _on_delete_job(self, _button) -> None:
        if not self.current_job_slug:
            return
        if not confirm_dialog(
            self, "Eliminar tarefa",
            f"Eliminar a tarefa '{self.current_job_slug}'?\n"
            "A configuração será removida (os backups existentes não)."
        ):
            return
        # Disable timer first
        disable_timer(self.current_job_slug)
        from warden.core.systemd import remove_unit_files
        remove_unit_files(self.current_job_slug)
        delete_job(self.current_job_slug)
        self.current_job_slug = None
        self._refresh_jobs_list()

    def _on_rename_job(self, _button) -> None:
        if not self.current_job_slug:
            return
        dialog = DialogoRenomear(self, self.current_job_slug)
        if dialog.run() == Gtk.ResponseType.OK:
            new_name = dialog.get_name()
            dialog.destroy()
            if not new_name:
                return
            from warden.core.jobs import is_valid_slug
            if not is_valid_slug(new_name):
                error_dialog(self, "Nome inválido",
                             "Use apenas minúsculas, números, hífens e underscores.")
                return
            old = self.current_job_slug
            if rename_job(old, new_name):
                # Also rename systemd units
                from warden.core.systemd import remove_unit_files
                remove_unit_files(old)
                self.current_job_slug = new_name
                job = load_job(new_name)
                if job and job.schedule_enabled:
                    generate_unit_files(job)
                    enable_timer(new_name)
                self._refresh_jobs_list()
            else:
                error_dialog(self, "Erro",
                             f"Não foi possível renomear para '{new_name}'.")
        else:
            dialog.destroy()

    def _on_export_job(self, _button) -> None:
        if not self.current_job_slug:
            return
        dialog = Gtk.FileChooserDialog(
            title="Exportar tarefa", transient_for=self, modal=True,
            action=Gtk.FileChooserAction.SAVE,
        )
        dialog.add_buttons(
            "_Cancelar", Gtk.ResponseType.CANCEL,
            "_Guardar", Gtk.ResponseType.OK,
        )
        dialog.set_current_name(f"{self.current_job_slug}.json")
        dialog.set_do_overwrite_confirmation(True)
        if dialog.run() == Gtk.ResponseType.OK:
            path = Path(dialog.get_filename())
            dialog.destroy()
            if export_job(self.current_job_slug, path):
                info_dialog(self, "Exportar",
                            f"Tarefa exportada para:\n{path}")
            else:
                error_dialog(self, "Erro", "Falha ao exportar a tarefa.")
        else:
            dialog.destroy()

    def _on_import_job(self, _button) -> None:
        dialog = Gtk.FileChooserDialog(
            title="Importar tarefa", transient_for=self, modal=True,
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.add_buttons(
            "_Cancelar", Gtk.ResponseType.CANCEL,
            "_Importar", Gtk.ResponseType.OK,
        )
        filt = Gtk.FileFilter()
        filt.set_name("JSON")
        filt.add_pattern("*.json")
        dialog.add_filter(filt)
        if dialog.run() == Gtk.ResponseType.OK:
            path = Path(dialog.get_filename())
            dialog.destroy()
            slug = import_job(path)
            if slug:
                self.current_job_slug = slug
                self._refresh_jobs_list()
                info_dialog(self, "Importar", f"Tarefa importada como: {slug}")
            else:
                error_dialog(self, "Erro", "Falha ao importar a tarefa.")
        else:
            dialog.destroy()

    # ============================================================
    # Destinos tab actions
    # ============================================================
    def _get_selected_dest_index(self) -> int | None:
        sel = self.dest_treeview.get_selection()
        model, iter_ = sel.get_selected()
        if iter_ is None:
            return None
        return model.get_path(iter_).get_indices()[0]

    def _on_add_dest(self, _button) -> None:
        if not self.current_job_slug:
            return
        job = load_job(self.current_job_slug)
        if job is None:
            return
        dialog = DialogoDestino(self)
        if dialog.run() == Gtk.ResponseType.OK:
            dest = dialog.get_destination()
            dialog.destroy()
            if not dest.id:
                warning_dialog(self, "ID obrigatório", "Indique um ID para o destino.")
                return
            if any(d.id == dest.id for d in job.destinations):
                warning_dialog(self, "ID duplicado",
                               f"Já existe um destino com ID '{dest.id}'.")
                return
            job.destinations.append(dest)
            save_job(job)
            self._load_job_to_ui()
        else:
            dialog.destroy()

    def _on_edit_dest(self, _button) -> None:
        if not self.current_job_slug:
            return
        idx = self._get_selected_dest_index()
        if idx is None:
            warning_dialog(self, "Seleção", "Selecione um destino para editar.")
            return
        job = load_job(self.current_job_slug)
        if job is None:
            return
        dialog = DialogoDestino(self, job.destinations[idx])
        if dialog.run() == Gtk.ResponseType.OK:
            new_dest = dialog.get_destination()
            dialog.destroy()
            # Check ID uniqueness (excluding self)
            for i, d in enumerate(job.destinations):
                if i != idx and d.id == new_dest.id:
                    warning_dialog(self, "ID duplicado",
                                   f"Já existe um destino com ID '{new_dest.id}'.")
                    return
            job.destinations[idx] = new_dest
            save_job(job)
            self._load_job_to_ui()
        else:
            dialog.destroy()

    def _on_del_dest(self, _button) -> None:
        if not self.current_job_slug:
            return
        idx = self._get_selected_dest_index()
        if idx is None:
            warning_dialog(self, "Seleção", "Selecione um destino para eliminar.")
            return
        job = load_job(self.current_job_slug)
        if job is None:
            return
        dest_id = job.destinations[idx].id
        if not confirm_dialog(
            self, "Eliminar destino",
            f"Eliminar o destino '{dest_id}' da tarefa?"
        ):
            return
        del job.destinations[idx]
        save_job(job)
        self._load_job_to_ui()

    # ============================================================
    # Origem tab
    # ============================================================
    def _on_browse_source(self, _button) -> None:
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

    def _on_save_source(self, _button) -> None:
        if not self.current_job_slug:
            return
        job = self._get_job_from_ui()
        if job is None:
            return
        if not job.source:
            warning_dialog(self, "Origem obrigatória",
                           "Indique a pasta de origem.")
            return
        if not Path(job.source).is_dir():
            if not confirm_dialog(
                self, "Pasta inexistente",
                f"A pasta '{job.source}' não existe. Guardar à mesma?"
            ):
                return
        save_job(job)
        info_dialog(self, "Guardado", "Origem e exclusões guardadas.")

    # ============================================================
    # Agendamento tab
    # ============================================================
    def _on_save_schedule(self, _button) -> None:
        if not self.current_job_slug:
            return
        job = self._get_job_from_ui()
        if job is None:
            return
        save_job(job)
        # Generate unit files
        generate_unit_files(job)
        if job.schedule_enabled:
            ok, msg = enable_timer(job.name)
            if ok:
                info_dialog(self, "Timer",
                            f"Timer para '{job.name}' ativado.")
            else:
                error_dialog(self, "Timer", f"Falha ao ativar timer: {msg}")
        else:
            disable_timer(job.name)
            info_dialog(self, "Timer",
                        f"Timer para '{job.name}' desativado.")
        daemon_reload()
        self._load_job_to_ui()

    def _on_reinstall_all_timers(self, _button) -> None:
        if not confirm_dialog(
            self, "Reinstalar timers",
            "Reinstalar todos os systemd timers?\n"
            "Isto vai (re)gerar ficheiros .service e .timer para todas as tarefas."
        ):
            return
        results = install_all_timers()
        if not results:
            info_dialog(self, "Timers", "Nenhuma tarefa agendada encontrada.")
            return
        msg_lines = []
        for slug, (ok, m) in results.items():
            msg_lines.append(f"{'✓' if ok else '✗'} {slug}: {m}")
        info_dialog(self, "Timers reinstalados", "\n".join(msg_lines))
        self._load_job_to_ui()

    # ============================================================
    # Histórico tab
    # ============================================================
    def _on_view_log(self, _button) -> None:
        if not self.current_job_slug:
            return
        log_text = tail_log(self.current_job_slug, lines=200)
        if not log_text:
            log_text = "(log vazio)"
        text_viewer_dialog(
            self, f"Log: {self.current_job_slug}", log_text, monospace=True
        )

    # ============================================================
    # Bottom bar actions
    # ============================================================
    def _on_settings(self, _button) -> None:
        dialog = DialogoSettings(self, self.config)
        if dialog.run() == Gtk.ResponseType.OK:
            self.config = dialog.get_config()
            save_config(self.config)
            apply_theme(self.config.theme, self.config.font)
            # Handle autostart
            self._update_autostart()
            info_dialog(self, "Configurações", "Configurações guardadas.")
        dialog.destroy()

    def _update_autostart(self) -> None:
        autostart_file = Path.home() / ".config" / "autostart" / "warden-gui.desktop"
        if self.config.behavior.autostart_gui:
            autostart_file.parent.mkdir(parents=True, exist_ok=True)
            autostart_file.write_text(
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Name=Warden\n"
                "Comment=Guardian of your files\n"
                "Exec=warden-gui\n"
                "Icon=warden\n"
                "Terminal=false\n"
                "X-GNOME-Autostart-enabled=true\n",
                encoding="utf-8",
            )
        else:
            if autostart_file.exists():
                autostart_file.unlink()

    def _on_start_backup(self, _button) -> None:
        if not self.current_job_slug:
            return
        if self.running:
            return
        job = self._get_job_from_ui()
        if job is None:
            return
        # Validate
        errors = validate_job(job)
        if errors:
            error_dialog(self, "Configuração inválida",
                         "Erros encontrados:\n" + "\n".join(f"• {e}" for e in errors))
            return
        # Save before running
        save_job(job)

        self.running = True
        self.btn_start.set_sensitive(False)
        self.btn_cancel.set_sensitive(True)
        self.progress.set_no_show_all(False)
        self.progress.set_fraction(0)
        self.progress.set_text("A iniciar…")

        # Run in background thread
        def worker():
            try:
                result = run_backup(job, self.config)
                self.events.put(("done", result))
            except Exception as e:
                self.events.put(("error", str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_cancel(self, _button) -> None:
        # Note: we can't really kill a running rsync/rclone easily
        warning_dialog(
            self, "Cancelar",
            "O backup atual não pode ser interrompido a meio. "
            "Aguarde que termine."
        )

    # ============================================================
    # Event polling (called by GLib.timeout_add)
    # ============================================================
    def _poll_events(self) -> bool:
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "done":
                    self._on_backup_done(event[1])
                elif event[0] == "error":
                    self._on_backup_error(event[1])
        except queue.Empty:
            pass
        return True

    def _on_backup_done(self, result: BackupResult) -> None:
        self.running = False
        self.btn_start.set_sensitive(True)
        self.btn_cancel.set_sensitive(False)
        self.progress.set_no_show_all(True)
        self.progress.set_fraction(1.0)

        # Build summary message
        ok = len(result.destinations_ok)
        sk = len(result.destinations_skipped)
        fa = len(result.destinations_failed)
        msg = (
            f"Backup {result.status} — duração: "
            f"{human_duration(result.duration_seconds)}\n"
            f"OK: {ok} • Skipped: {sk} • Failed: {fa}\n"
            f"Tamanho: {human_size(result.total_size_bytes)}\n"
        )
        if sk > 0:
            skipped_info = []
            for r in result.destinations:
                if r.status == "skipped":
                    skipped_info.append(f"  • {r.destination_id}: {r.error}")
            msg += "\nDestinos saltados:\n" + "\n".join(skipped_info)
        if fa > 0:
            failed_info = []
            for r in result.destinations:
                if r.status == "failed":
                    failed_info.append(f"  • {r.destination_id}: {r.error}")
            msg += "\nDestinos falhados:\n" + "\n".join(failed_info)

        if result.status == "success":
            info_dialog(self, "Backup concluído", msg)
        elif result.status == "partial":
            warning_dialog(self, "Backup parcial", msg)
        else:
            error_dialog(self, "Backup falhou", msg)

        # Refresh UI
        self._refresh_jobs_list()
        self._load_job_to_ui()

    def _on_backup_error(self, error_msg: str) -> None:
        self.running = False
        self.btn_start.set_sensitive(True)
        self.btn_cancel.set_sensitive(False)
        self.progress.set_no_show_all(True)
        error_dialog(self, "Erro", f"Erro inesperado durante o backup:\n{error_msg}")
        self._refresh_jobs_list()

    # ============================================================
    # Close
    # ============================================================
    def _on_close(self, _widget, _event) -> bool:
        if self.running:
            if not confirm_dialog(
                self, "Backup em curso",
                "Está a decorrer um backup. Sair à mesma?"
            ):
                return True  # prevent close
        Gtk.main_quit()
        return False


# ============================================================
# Entry point
# ============================================================
def main() -> int:
    """Launch the Warden GUI."""
    if not GTK_AVAILABLE:
        print(
            "Error: GTK3 is not available. Install with:\n"
            "  sudo apt install python3-gi gir1.2-gtk-3.0",
            flush=True,
        )
        return 1
    app = WardenApp()
    app.show_all()
    Gtk.main()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
