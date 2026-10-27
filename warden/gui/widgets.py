"""Reusable GTK widgets for the Warden GUI."""

from __future__ import annotations

from typing import Any

try:
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Pango", "1.0")
    from gi.repository import Gtk, Gdk, GdkPixbuf, Pango, GLib
    GTK_AVAILABLE = True
except (ImportError, ValueError):
    GTK_AVAILABLE = False
    Gtk = None
    Gdk = None
    GdkPixbuf = None
    Pango = None
    GLib = None


def human_size(n: int) -> str:
    """Convert bytes to human-readable string."""
    if not n:
        return "0 B"
    if n < 1024:
        return f"{n} B"
    f = float(n)
    for unit in ("KB", "MB", "GB", "TB"):
        f /= 1024
        if f < 1024:
            return f"{f:.1f} {unit}"
    return f"{f:.1f} PB"


def human_duration(seconds: float) -> str:
    """Convert seconds to friendly duration string."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    m = int(seconds // 60)
    s = int(seconds % 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h = int(m // 60)
    m = m % 60
    return f"{h}h{m:02d}m"


def status_icon(status: str | None) -> str:
    """Return a status icon character."""
    return {
        "success": "✓",
        "ok": "✓",
        "partial": "⚠",
        "skipped": "⏭",
        "failure": "✗",
        "failed": "✗",
        None: "○",
        "": "○",
    }.get(status, "○")


def status_class(status: str | None) -> str:
    """Return the CSS class for a status."""
    if status in ("success", "ok"):
        return "status-ok"
    if status in ("partial", "skipped"):
        return "status-warn"
    if status in ("failure", "failed"):
        return "status-error"
    return "status-pending"


def create_label(text: str = "", *, markup: bool = False, xalign: float = 0.0,
                 dim: bool = False) -> "Gtk.Label":
    """Create a label with common defaults."""
    label = Gtk.Label()
    if markup:
        label.set_markup(text)
    else:
        label.set_text(text)
    label.set_xalign(xalign)
    label.set_halign(Gtk.Align.START)
    if dim:
        label.get_style_context().add_class("dim-label")
    return label


def create_button(label: str = "", icon: str = "", tooltip: str = "",
                  style: str = "") -> "Gtk.Button":
    """Create a button. If icon is given, uses an icon + tooltip."""
    if icon:
        button = Gtk.Button.new_from_icon_name(icon, Gtk.IconSize.BUTTON)
        if label:
            button.set_label(label)
    else:
        button = Gtk.Button(label=label)
    if tooltip:
        button.set_tooltip_text(tooltip)
    if style:
        button.get_style_context().add_class(style)
    return button


def create_entry(text: str = "", placeholder: str = "",
                 visibility: bool = True) -> "Gtk.Entry":
    """Create a text entry."""
    entry = Gtk.Entry()
    entry.set_text(text)
    if placeholder:
        entry.set_placeholder_text(placeholder)
    if not visibility:
        entry.set_visibility(False)
    entry.set_hexpand(True)
    return entry


def create_frame(label: str) -> "Gtk.Frame":
    """Create a frame with a label."""
    frame = Gtk.Frame(label=label)
    frame.set_shadow_type(Gtk.ShadowType.ETCHED_IN)
    return frame


def info_dialog(parent: Any, title: str, message: str) -> None:
    """Show an info dialog."""
    dialog = Gtk.MessageDialog(
        transient_for=parent, modal=True,
        message_type=Gtk.MessageType.INFO,
        buttons=Gtk.ButtonsType.OK,
        text=title,
    )
    dialog.format_secondary_text(message)
    dialog.run()
    dialog.destroy()


def warning_dialog(parent: Any, title: str, message: str) -> None:
    """Show a warning dialog."""
    dialog = Gtk.MessageDialog(
        transient_for=parent, modal=True,
        message_type=Gtk.MessageType.WARNING,
        buttons=Gtk.ButtonsType.OK,
        text=title,
    )
    dialog.format_secondary_text(message)
    dialog.run()
    dialog.destroy()


def error_dialog(parent: Any, title: str, message: str) -> None:
    """Show an error dialog."""
    dialog = Gtk.MessageDialog(
        transient_for=parent, modal=True,
        message_type=Gtk.MessageType.ERROR,
        buttons=Gtk.ButtonsType.OK,
        text=title,
    )
    dialog.format_secondary_text(message)
    dialog.run()
    dialog.destroy()


def confirm_dialog(parent: Any, title: str, message: str) -> bool:
    """Show a yes/no confirmation dialog. Returns True if user confirms."""
    dialog = Gtk.MessageDialog(
        transient_for=parent, modal=True,
        message_type=Gtk.MessageType.QUESTION,
        buttons=Gtk.ButtonsType.YES_NO,
        text=title,
    )
    dialog.format_secondary_text(message)
    response = dialog.run()
    dialog.destroy()
    return response == Gtk.ResponseType.YES


def text_viewer_dialog(parent: Any, title: str, text: str,
                       *, monospace: bool = True) -> None:
    """Show a read-only text viewer dialog (for logs, prompts, etc.)."""
    dialog = Gtk.Dialog(
        title=title, transient_for=parent, modal=True,
        default_width=800, default_height=600,
    )
    dialog.add_buttons("_Fechar", Gtk.ResponseType.CLOSE)
    dialog.set_border_width(10)

    content = dialog.get_content_area()
    scrolled = Gtk.ScrolledWindow()
    scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    textview = Gtk.TextView()
    textview.set_editable(False)
    textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    if monospace:
        textview.override_font(
            Pango.FontDescription.from_string("Monospace 10")
        )
    textview.get_buffer().set_text(text)
    scrolled.add(textview)
    content.pack_start(scrolled, True, True, 0)
    dialog.show_all()
    dialog.run()
    dialog.destroy()
