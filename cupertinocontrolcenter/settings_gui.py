#!/usr/bin/env python3
import gi
import os
import sys
import json
import glob
import argparse
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, Gdk, GLib

# Run as a script (python3 settings_gui.py): make the package importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cupertinocontrolcenter.paths import STATE_DIR, CACHE_DIR, PO_DIR
from cupertinocontrolcenter.i18n import _, ngettext

# Settings are stored here; optional_bar.py reads them every time the Control Center opens.
# Keys not shown in this window (e.g. Dark Mode theme names) can be added by hand and are kept.
SETTINGS_PATH = os.path.join(STATE_DIR, "settings.json")
DEFAULT_SETTINGS = {
    "show_battery": True,
    "show_music": False,
    "tile_settings": True,
    "tile_screenshot": True,
    "tile_lock": True,
    "tile_power": True,
    "language": "auto",
}

# Music Recognition history and covers (same places as music_recognition.py)
HISTORY_PATH = os.path.join(STATE_DIR, "music_history.json")
COVER_DIR = os.path.join(CACHE_DIR, "music-covers")

# Language names shown in the Language list, in their own language. Languages with a
# po/<code>.po file appear automatically; add a name here to show it nicely.
LANGUAGE_NAMES = {"en": "English", "tr": "Türkçe"}

# Same measurements as XFCE settings dialogs: bold section titles, rows indented 12 px,
# 12 px page padding
SECTION_INDENT = 12
PAGE_PADDING = 12


class ControlCenterSettings:
    """Opens in two ways:
    - From the XFCE Settings Manager: with --socket-id, the content is embedded in the
      manager's window (the manager adds Help / All Settings / Close). This needs
      X-XfcePluggable=true in the .desktop file.
    - On its own: the same content in its own window with an XFCE-style Close button."""

    def __init__(self, socket_id=0):
        self.settings = self._load()
        self._toplevel = None

        notebook = self._build_notebook()

        if socket_id:
            plug = Gtk.Plug.new(socket_id)
            plug.connect("delete-event", Gtk.main_quit)
            notebook.set_border_width(6)
            plug.add(notebook)
            self._toplevel = plug
            plug.show_all()
            Gdk.notify_startup_complete()
        else:
            win = Gtk.Window(title=_("Control Center Settings"))
            win.set_type_hint(Gdk.WindowTypeHint.DIALOG)
            win.set_icon_name("preferences-system")
            win.set_default_size(440, -1)
            win.set_resizable(False)
            win.set_position(Gtk.WindowPosition.CENTER)
            win.connect("destroy", Gtk.main_quit)
            self._toplevel = win

            outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            notebook.set_margin_top(12); notebook.set_margin_start(12); notebook.set_margin_end(12)
            outer.pack_start(notebook, True, True, 0)

            # Close button at the bottom right, as in other XFCE dialogs. Changes are saved
            # immediately, so there is no Save button.
            bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            bar.set_border_width(12)
            close_btn = Gtk.Button.new_with_mnemonic(_("_Close"))
            close_btn.set_image(Gtk.Image.new_from_icon_name("window-close-symbolic", Gtk.IconSize.BUTTON))
            close_btn.set_always_show_image(True)
            close_btn.connect("clicked", lambda w: win.destroy())
            bar.pack_end(close_btn, False, False, 0)
            outer.pack_start(bar, False, False, 0)

            win.add(outer)
            win.show_all()
            close_btn.grab_focus()

    # ------------------------------------------------------------------ content
    def _build_notebook(self):
        notebook = Gtk.Notebook()

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        page.set_border_width(PAGE_PADDING)

        # One group per module, as on macOS; rows inside a group are divided by thin lines
        rows = self._add_section(page, _("Battery"), first=True)
        self._add_switch_row(rows, _("Show in Control Center"),
                             _("Battery percentage; click for the battery menu"), "show_battery")

        rows = self._add_section(page, _("Music Recognition"))
        self._add_switch_row(rows, _("Show in Control Center"),
                             _("Circle: identifies the playing song · Text: history"), "show_music")
        rows.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)
        # Clearing the history can't be undone, so it lives here, behind a confirmation,
        # not in the Control Center menu
        self._add_history_row(rows)

        rows = self._add_section(page, _("Quick Buttons"))
        for i, (key, text, desc) in enumerate((
                ("tile_settings", _("Settings"), _("Settings Manager")),
                ("tile_screenshot", _("Screenshot"), None),
                ("tile_lock", _("Lock"), _("Lock the screen")),
                ("tile_power", _("Power"), _("Log out, restart, shut down")))):
            if i:
                rows.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)
            self._add_switch_row(rows, text, desc, key)

        rows = self._add_section(page, _("Language"))
        self._add_language_row(rows)

        note = Gtk.Label(xalign=0)
        note.set_markup("<small>%s</small>" % GLib.markup_escape_text(_(
            "When Battery and Music Recognition are both on, they sit side by side. If "
            "Screenshot, Lock or Power is also on, there is no room left, so Music Recognition "
            "moves to a second row and the Control Center gets taller. Buttons next to a "
            "hidden one fill its place. Changes appear the next time the Control Center opens.")))
        note.get_style_context().add_class("dim-label")
        note.set_line_wrap(True)
        note.set_max_width_chars(50)
        note.set_margin_top(12)
        page.pack_start(note, False, False, 0)

        notebook.append_page(page, Gtk.Label.new_with_mnemonic(_("_General")))
        return notebook

    def _add_section(self, page, title, first=False):
        header = Gtk.Label(xalign=0)
        header.set_markup(f"<b>{GLib.markup_escape_text(title)}</b>")
        if not first:
            header.set_margin_top(12)   # space between groups
        page.pack_start(header, False, False, 0)

        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        rows.set_margin_start(SECTION_INDENT)
        page.pack_start(rows, False, False, 0)
        return rows

    def _text_block(self, label_text, desc_text):
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        texts.set_valign(Gtk.Align.CENTER)
        lbl = Gtk.Label(label=label_text, xalign=0)
        texts.pack_start(lbl, False, False, 0)
        if desc_text:
            desc = Gtk.Label(xalign=0)
            desc.set_markup(f"<small>{GLib.markup_escape_text(desc_text)}</small>")
            desc.get_style_context().add_class("dim-label")
            desc.set_line_wrap(True)
            desc.set_max_width_chars(40)
            texts.pack_start(desc, False, False, 0)
        return texts

    def _add_switch_row(self, container, label_text, desc_text, config_key):
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        sw = Gtk.Switch()
        sw.set_active(bool(self.settings[config_key]))
        sw.set_valign(Gtk.Align.CENTER)
        sw.connect("state-set", self._on_switch_toggled, config_key)
        hbox.pack_start(self._text_block(label_text, desc_text), True, True, 0)
        hbox.pack_end(sw, False, False, 0)
        container.pack_start(hbox, False, False, 0)

    def _add_language_row(self, container):
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        combo = Gtk.ComboBoxText()
        combo.set_valign(Gtk.Align.CENTER)
        combo.append("auto", _("System default"))
        codes = {"en"} | {os.path.splitext(os.path.basename(p))[0]
                          for p in glob.glob(os.path.join(PO_DIR, "*.po"))}
        for code in sorted(codes, key=lambda c: LANGUAGE_NAMES.get(c, c).lower()):
            combo.append(code, LANGUAGE_NAMES.get(code, code))
        if not combo.set_active_id(self.settings.get("language") or "auto"):
            combo.set_active_id("auto")
        combo.connect("changed", lambda c: self._set("language", c.get_active_id()))
        hbox.pack_start(self._text_block(_("Language"), _("Applies the next time it opens")), True, True, 0)
        hbox.pack_end(combo, False, False, 0)
        container.pack_start(hbox, False, False, 0)

    def _add_history_row(self, container):
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self._hist_texts = self._text_block(_("Recognition history"), self._history_desc())
        btn = Gtk.Button(label=_("Clear History…"))
        btn.set_valign(Gtk.Align.CENTER)
        btn.set_sensitive(self._history_count() > 0)
        btn.connect("clicked", self._on_clear_history)
        self._hist_btn = btn
        hbox.pack_start(self._hist_texts, True, True, 0)
        hbox.pack_end(btn, False, False, 0)
        container.pack_start(hbox, False, False, 0)

    @staticmethod
    def _history_count():
        try:
            with open(HISTORY_PATH) as f:
                h = json.load(f)
            return len(h) if isinstance(h, list) else 0
        except Exception:
            return 0

    def _history_desc(self):
        n = self._history_count()
        return ngettext("%d song saved", "%d songs saved", n) % n if n else _("History is empty")

    def _on_clear_history(self, btn):
        n = self._history_count()
        dlg = Gtk.MessageDialog(transient_for=self._toplevel if isinstance(self._toplevel, Gtk.Window) else None,
                                modal=True, message_type=Gtk.MessageType.QUESTION,
                                buttons=Gtk.ButtonsType.NONE,
                                text=_("Clear the Music Recognition history?"))
        dlg.format_secondary_text(ngettext(
            "%d recognized song and its cover will be deleted. This can't be undone.",
            "%d recognized songs and their covers will be deleted. This can't be undone.", n) % n)
        dlg.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        dlg.add_button(_("Delete"), Gtk.ResponseType.ACCEPT)
        dlg.set_default_response(Gtk.ResponseType.CANCEL)
        resp = dlg.run()
        dlg.destroy()
        if resp != Gtk.ResponseType.ACCEPT:
            return
        try:
            tmp = HISTORY_PATH + ".tmp"
            with open(tmp, "w") as f:
                json.dump([], f)
            os.replace(tmp, HISTORY_PATH)
            for c in glob.glob(os.path.join(COVER_DIR, "*")):
                os.remove(c)
        except Exception as e:
            print(f"Could not clear the history: {e}")
        desc = self._hist_texts.get_children()[-1]
        desc.set_markup(f"<small>{GLib.markup_escape_text(self._history_desc())}</small>")
        self._hist_btn.set_sensitive(False)

    # ------------------------------------------------------------------ storage
    def _load(self):
        s = dict(DEFAULT_SETTINGS)
        self._other_keys = {}
        try:
            with open(SETTINGS_PATH) as f:
                raw = json.load(f)
            # Older versions stored a single choice ("wide_slot")
            if "wide_slot" in raw and "show_battery" not in raw:
                raw["show_battery"] = raw["wide_slot"] == "battery"
                raw["show_music"] = raw["wide_slot"] == "music"
            s.update({k: v for k, v in raw.items() if k in DEFAULT_SETTINGS})
            self._other_keys = {k: v for k, v in raw.items()
                                if k not in DEFAULT_SETTINGS and k != "wide_slot"}
        except Exception:
            pass
        return s

    def _set(self, key, value):
        self.settings[key] = value
        try:
            os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
            tmp = SETTINGS_PATH + ".tmp"
            with open(tmp, "w") as f:
                json.dump({**self._other_keys, **self.settings}, f, indent=2)
            os.replace(tmp, SETTINGS_PATH)     # never leave a half-written file
        except Exception as e:
            print(f"Could not save the settings: {e}")

    def _on_switch_toggled(self, switch, state, config_key):
        self._set(config_key, bool(state))
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    # The XFCE Settings Manager passes --socket-id=NNN
    parser.add_argument("--socket-id", type=int, default=0)
    args, _unknown = parser.parse_known_args()
    ControlCenterSettings(args.socket_id)
    Gtk.main()
