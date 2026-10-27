"""CSS themes for the Warden GUI.

Three themes: black (dark), white (light), sistema (follow system).
Plus an optional custom font.
"""

from __future__ import annotations

CSS_DARK = """
window, dialog, frame, box, grid, flowbox, popover, menu {
    background-color: #1e1e1e;
    color: #eeeeee;
}
label { color: #eeeeee; }
.dim-label { color: #888888; }
button {
    background-color: #333333;
    color: #eeeeee;
    border: 1px solid #444444;
    border-radius: 4px;
    padding: 4px 10px;
}
button:hover { background-color: #3a3a3a; }
button:disabled { color: #666666; background-color: #2a2a2a; }
button.suggested-action {
    background-color: #3a6ea5;
    color: #ffffff;
    font-weight: bold;
}
button.suggested-action:hover { background-color: #4a7eb5; }
button.destructive-action {
    background-color: #a53a3a;
    color: #ffffff;
}
entry, textview, textview text, treeview, list, listbox, listboxrow,
.view, scrollbar, viewport, scrolledwindow {
    background-color: #262626;
    color: #eeeeee;
}
entry { border: 1px solid #444444; border-radius: 3px; padding: 4px; }
treeview:selected, listboxrow:selected {
    background-color: #3a6ea5;
    color: #ffffff;
}
progressbar trough { background-color: #333333; }
progressbar progress { background-color: #3a6ea5; }
frame > border { color: #444444; }
notebook tab { background-color: #2a2a2a; color: #aaaaaa; padding: 6px 12px; }
notebook tab:checked { background-color: #1e1e1e; color: #eeeeee; }
spinbutton { background-color: #262626; }
checkbutton { color: #eeeeee; }
radiobutton { color: #eeeeee; }
separator { background-color: #444444; }
"""

CSS_LIGHT = """
window, dialog, frame, box, grid, flowbox, popover, menu {
    background-color: #ffffff;
    color: #111111;
}
label { color: #111111; }
.dim-label { color: #666666; }
button {
    background-color: #f5f5f5;
    color: #111111;
    border: 1px solid #cccccc;
    border-radius: 4px;
    padding: 4px 10px;
}
button:hover { background-color: #e8e8e8; }
button:disabled { color: #999999; background-color: #f0f0f0; }
button.suggested-action {
    background-color: #3a6ea5;
    color: #ffffff;
    font-weight: bold;
}
button.suggested-action:hover { background-color: #4a7eb5; }
button.destructive-action {
    background-color: #a53a3a;
    color: #ffffff;
}
entry, textview, textview text, treeview, list, listbox, listboxrow,
.view, scrollbar, viewport, scrolledwindow {
    background-color: #ffffff;
    color: #111111;
}
entry { border: 1px solid #cccccc; border-radius: 3px; padding: 4px; }
treeview:selected, listboxrow:selected {
    background-color: #3a6ea5;
    color: #ffffff;
}
progressbar trough { background-color: #dddddd; }
progressbar progress { background-color: #3a6ea5; }
frame > border { color: #cccccc; }
notebook tab { background-color: #e8e8e8; color: #666666; padding: 6px 12px; }
notebook tab:checked { background-color: #ffffff; color: #111111; }
"""

# Common styles applied regardless of theme
CSS_COMMON = """
.list-row {
    padding: 8px 12px;
    border-bottom: 1px solid rgba(128, 128, 128, 0.2);
}
.status-ok { color: #4caf50; }
.status-warn { color: #ff9800; }
.status-error { color: #f44336; }
.status-pending { color: #888888; }
"""


def apply_theme(theme: str = "sistema", font: str = "") -> None:
    """Apply the selected theme and font to the GTK application.

    Args:
        theme: "black" / "white" / "sistema"
        font: Pango font string (e.g. "Inter 11") or empty for default
    """
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gdk, Gtk
    except (ImportError, ValueError):
        return  # GUI not available

    display = Gdk.Display.get_default()
    if display is None:
        return
    screen = display.get_default_screen()

    # Remove any previous provider we set
    if hasattr(apply_theme, "_provider") and apply_theme._provider is not None:
        Gtk.StyleContext.remove_provider_for_screen(screen, apply_theme._provider)

    css = CSS_COMMON
    if theme == "black":
        css += CSS_DARK
    elif theme == "white":
        css += CSS_LIGHT
    # For "sistema", only apply CSS_COMMON and let the system theme handle the rest

    # Font override
    if font:
        try:
            from gi.repository import Pango
            desc = Pango.FontDescription.from_string(font)
            family = desc.get_family().replace('"', "") if desc.get_family() else "Sans"
            size_pt = desc.get_size() / Pango.SCALE
            css += f'\n* {{ font-family: "{family}"; font-size: {size_pt:.1f}pt; }}'
        except Exception:
            pass

    if css:
        provider = Gtk.CssProvider()
        try:
            provider.load_from_data(css.encode("utf-8"))
            Gtk.StyleContext.add_provider_for_screen(
                screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
            apply_theme._provider = provider
        except Exception:
            apply_theme._provider = None

    # For "black"/"white" themes, also tell GTK to use the dark variant
    settings = Gtk.Settings.get_default()
    if settings:
        settings.set_property(
            "gtk-application-prefer-dark-theme", theme == "black"
        )
