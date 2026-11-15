import gi
import subprocess
import threading
import re
import os
import glob
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gdk, Pango
from cupertinocontrolcenter.widgets.round_icon import RoundIcon
from cupertinocontrolcenter.i18n import _

class SMView(Gtk.Box):
    def __init__(self, app_instance=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        
        self.app = app_instance if app_instance else Gtk.Application.get_default()
        self.detected_displays = []
        
        self.set_margin_start(7); self.set_margin_end(7)   # content and separators 15 px from the window edge
        self.set_margin_top(7); self.set_margin_bottom(0)
        self.get_style_context().add_class("styledwidgetbox")

        # --- Header: clicking the title goes back ---
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        
        title_eb = Gtk.EventBox()
        title_eb.set_visible_window(False)
        title_eb.set_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        
        title_lbl = Gtk.Label()
        title_lbl.set_markup("<span weight='bold' size='medium'>%s</span>"
                             % GLib.markup_escape_text(_("Screen Mirroring")))
        title_lbl.set_halign(Gtk.Align.START)
        title_lbl.set_margin_start(2)
        title_eb.add(title_lbl)
        
        title_eb.connect("button-press-event", lambda w, e: [
            self._go_back()
        ] if e.button == 1 else False)
        
        header_box.pack_start(title_eb, True, True, 0)
        self.pack_start(header_box, False, False, 0)
        
        sep1 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep1.set_margin_top(5); sep1.set_margin_bottom(0)
        sep1.set_margin_start(2); sep1.set_margin_end(2)   # 16 px from the window edge, as in Wi-Fi
        self.pack_start(sep1, False, False, 0)

        # --- Display list ---
        scroll_window = Gtk.ScrolledWindow()
        scroll_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll_window.set_shadow_type(Gtk.ShadowType.NONE)
        scroll_window.set_size_request(260, -1)
        scroll_window.set_propagate_natural_height(True)
        scroll_window.set_max_content_height(450)

        self.display_list_box = Gtk.ListBox()
        self.display_list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.display_list_box.get_style_context().add_class("transparent-listbox")
        
        scroll_window.add(self.display_list_box)
        self.pack_start(scroll_window, True, True, 0)

        # The first scan runs in the background (xrandr probing can take half a second);
        # draw the empty list now so the settings row shows right away
        self._populate_displays()
        self._was_visible = False
        threading.Thread(target=self._scan_displays_worker, args=(True,), daemon=True).start()
        
        GLib.timeout_add_seconds(3, self._trigger_async_display_scan)
        self.show_all()

    def _trigger_async_display_scan(self):
        visible = bool(self.app and hasattr(self.app, 'view_stack')
                       and self.app.view_stack.get_visible_child_name() == "sm_view")
        if visible:
            # Probe outputs (--query) when the view opens; while it stays open only read the
            # current state (--current). Probing every 3 s can make an external display
            # flicker on some Intel setups.
            probe = not self._was_visible
            threading.Thread(target=self._scan_displays_worker, args=(probe,), daemon=True).start()
        self._was_visible = visible
        return True

    # ── Helpers ─────────────────────────────────────────────

    @staticmethod
    def _is_internal(port):
        return port.upper().startswith(("EDP", "LVDS", "DSI"))

    @staticmethod
    def _is_tv(name):
        """Does the EDID name belong to a TV? ("LG TV", "SONY TV", "TCL", "Roku"...)
        Brands that also make monitors (Samsung, LG) count as monitors unless "TV" is in the name."""
        n = re.sub(r"[^a-z0-9]+", " ", name.lower()).split()
        return "tv" in n or any(b in n for b in ("roku", "bravia", "tcl", "hisense", "vizio"))

    @staticmethod
    def _edid_name(port):
        """The display's own name ("SAMSUNG", "DELL U2414H"...) from its EDID, or None.
        xrandr names outputs DP1 / DP-1 / HDMI-1 depending on the driver; sysfs uses DP-1, HDMI-A-1."""
        def norm(n):
            return n.upper().replace("-", "").replace("HDMIA", "HDMI")
        target = norm(port)
        for path in glob.glob("/sys/class/drm/card*-*"):
            conn = os.path.basename(path).split("-", 1)[1]
            if norm(conn) != target:
                continue
            try:
                with open(os.path.join(path, "edid"), "rb") as f:
                    edid = f.read()
            except Exception:
                return None
            for off in (54, 72, 90, 108):   # the four 18-byte descriptors
                d = edid[off:off + 18]
                if len(d) == 18 and d[0] == 0 and d[1] == 0 and d[3] == 0xFC:
                    name = d[5:18].decode("ascii", "ignore").split("\n")[0].strip()
                    return name or None
            return None
        return None

    def _scan_displays_worker(self, probe=False):
        try:
            flag = "--query" if probe else "--current"
            out = subprocess.check_output(["xrandr", flag], text=True, stderr=subprocess.DEVNULL, timeout=5)
            geo_re = re.compile(r"\b(\d+)x(\d+)\+\d+\+\d+")

            connected = []   # (port, line)
            for line in out.splitlines():
                if " connected" in line and not line.startswith(" "):
                    connected.append((line.split()[0], line))

            # Internal display: eDP/LVDS/DSI; otherwise the primary one; otherwise the first
            internal, internal_line = None, ""
            for port, line in connected:
                if self._is_internal(port):
                    internal, internal_line = port, line; break
            if internal is None:
                for port, line in connected:
                    if " primary" in line:
                        internal, internal_line = port, line; break
            if internal is None and connected:
                internal, internal_line = connected[0]

            m = geo_re.search(internal_line)
            internal_res = f"{m.group(1)}x{m.group(2)}" if m else None

            displays = []
            for port, line in connected:
                if port == internal:
                    continue
                # An active output shows its geometry on its own line (e.g. 1920x1080+0+0)
                active = bool(geo_re.search(line))
                name = self._edid_name(port) or _("External Display (%s)") % port.replace("-", " ")
                displays.append({"port": port, "name": name, "active": active,
                                 "internal": internal, "internal_res": internal_res})

            if self.detected_displays != displays or not hasattr(self, '_first_scan_done'):
                self._first_scan_done = True
                self.detected_displays = displays
                GLib.idle_add(self._populate_displays)
        except Exception as e:
            print(f"Could not scan displays: {e}")

    def _add_settings_row(self):
        """"Display Settings…" at the bottom, as on macOS (same row as in the Display view)."""
        row_shell = Gtk.ListBoxRow()
        row_shell.get_style_context().add_class("mac-wifi-row")
        row_shell.get_style_context().add_class("mac-bottom-settings-row")
        row_shell.set_margin_start(0); row_shell.set_margin_end(0)
        row_shell.set_margin_top(0); row_shell.set_margin_bottom(0)

        eb = Gtk.EventBox(); eb.set_visible_window(False)
        lbl = Gtk.Label(label=_("Display Settings…"), xalign=0)
        lbl.set_margin_start(0); lbl.set_margin_top(4); lbl.set_margin_bottom(4)
        eb.add(lbl)
        eb.connect("button-press-event", lambda w, e: subprocess.Popen(["xfce4-display-settings"]) if e.button == 1 else False)
        row_shell.add(eb)
        self.display_list_box.add(row_shell)

    def _add_separator_row(self):
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.get_style_context().add_class("tight-bottom")
        sep.set_margin_start(2); sep.set_margin_end(2)   # the list row adds 2 px: 16 px in total
        row = Gtk.ListBoxRow()
        row.set_sensitive(False); row.set_can_focus(False); row.set_selectable(False)
        row.get_style_context().add_class("styled-separator-row")
        row.add(sep)
        self.display_list_box.add(row)

    def _populate_displays(self):
        for child in self.display_list_box.get_children():
            self.display_list_box.remove(child)
            
        # Nothing connected: just the settings row, no "not found" text (as on macOS)
        if not self.detected_displays:
            self._add_settings_row()
            self.display_list_box.show_all()
            
            if self.app and hasattr(self.app, '_force_single_resize'):
                GLib.idle_add(self.app._force_single_resize)
            return False

        for disp in self.detected_displays:
            # Same measurements as the Wi-Fi and Bluetooth rows
            row_shell = Gtk.ListBoxRow()
            row_shell.get_style_context().add_class("mac-wifi-row")
            row_shell.set_margin_start(2); row_shell.set_margin_end(0)
            row_shell.set_margin_top(3); row_shell.set_margin_bottom(1)
            
            row_eb = Gtk.EventBox()
            row_eb.set_visible_window(False)
            row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            row_box.set_margin_top(1); row_box.set_margin_bottom(1)
            
            # TV or monitor glyph; blue while mirroring
            img = RoundIcon("tv" if self._is_tv(disp["name"]) else "monitor", active=disp["active"])
            lbl = Gtk.Label(label=disp["name"], xalign=0)
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
            
            row_box.pack_start(img, False, False, 0)
            row_box.pack_start(lbl, True, True, 0)
            
            row_eb.add(row_box)
            row_eb.connect("button-press-event", self._on_display_selected, disp)
            row_shell.add(row_eb)
            self.display_list_box.add(row_shell)

        self._add_separator_row()
        self._add_settings_row()
        self.display_list_box.show_all()
        if self.app and hasattr(self.app, '_force_single_resize'):
            GLib.idle_add(self.app._force_single_resize)
        return False

    def _on_display_selected(self, widget, event, disp):
        if event.button != 1:
            return False

        def run(args):
            r = subprocess.run(args, capture_output=True, text=True)
            if r.returncode != 0:
                print(f"xrandr failed ({' '.join(args)}): {r.stderr.strip()}")
            return r.returncode == 0

        def toggle_worker():
            ext, internal = disp["port"], disp["internal"]
            try:
                if disp["active"]:
                    run(["xrandr", "--output", ext, "--off", "--output", internal, "--auto"])
                else:
                    base = ["xrandr", "--output", ext, "--auto", "--same-as", internal]
                    res = disp.get("internal_res")
                    # Full-screen mirroring as on macOS: the external display scales the
                    # internal display's picture to its own resolution. With --same-as alone and
                    # different resolutions, the picture sits in the top-left corner.
                    if not (res and run(base + ["--scale-from", res])):
                        run(base)   # scaling not supported: plain mirroring
                self._scan_displays_worker(False)
            except Exception as ex:
                print(f"Could not toggle mirroring: {ex}")

        threading.Thread(target=toggle_worker, daemon=True).start()
        return True

    def _go_back(self):
        if self.app and hasattr(self.app, 'view_stack') and self.app.view_stack:
            self.app.view_stack.set_visible_child_name("main_view")
            if hasattr(self.app, '_force_single_resize'):
                GLib.idle_add(self.app._force_single_resize)
