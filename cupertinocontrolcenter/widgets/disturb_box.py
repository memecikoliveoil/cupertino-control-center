import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk
import subprocess
from cupertinocontrolcenter.i18n import _

class DoNotDisturbInformation:
    def __init__(self):
        self.state = self._get_state()

    def _get_state(self):
        try:
            result = subprocess.run(
                ["xfconf-query", "-c", "xfce4-notifyd", "-p", "/do-not-disturb"],
                capture_output=True, text=True
            )
            return result.stdout.strip() == "true"
        except Exception: return False

    def get_state(self): return self.state

    def toggle(self):
        new_state = not self.state
        subprocess.run([
            "xfconf-query", "-c", "xfce4-notifyd", "-p", "/do-not-disturb", "-s",
            "true" if new_state else "false", "--create"
        ])
        self.state = new_state

class DisturbBox(Gtk.Box):
    def __init__(self):
        super().__init__()
        self.dnd = DoNotDisturbInformation()
        self.get_style_context().add_class("styledwidgetbox")

        self.set_hexpand(True)
        self.set_vexpand(True)

        h_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        
        # Icon (click toggles Do Not Disturb in xfce4-notifyd)
        self.disturb_icon = Gtk.Image()
        self.disturb_eventbox = Gtk.EventBox()
        self.disturb_eventbox.add(self.disturb_icon)
        self.disturb_eventbox.connect("button-press-event", self._toggle_dnd)

        self.disturb_eventbox.set_margin_start(5)

        v_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        v_box.set_margin_start(5)
        
        # One label with a line break (two labels would each get CSS top padding)
        # Translators: tile title, split over two lines with \n
        label = Gtk.Label(label=_("Do Not\nDisturb"))
        label.get_style_context().add_class("general_title")
        label.set_xalign(0)
        label.set_justify(Gtk.Justification.LEFT)
        v_box.set_valign(Gtk.Align.CENTER)
        v_box.pack_start(label, False, False, 0)

        h_box.pack_start(self.disturb_eventbox, False, False, 0)
        h_box.pack_start(v_box, True, True, 0)

        self.pack_start(h_box, True, True, 0)
        self._update_ui()

    def _update_ui(self):
        icon_path = "icons/disturb.png" if self.dnd.get_state() else "icons/disturb-d.png"
        self.disturb_icon.set_from_file(icon_path)

    def _toggle_dnd(self, widget, event):
        if event.button == 1:
            self.dnd.toggle()
            self._update_ui()
            return True
        return False
