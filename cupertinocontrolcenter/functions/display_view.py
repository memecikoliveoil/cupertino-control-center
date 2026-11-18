import gi
import subprocess
import os
import re
import time
import threading
import shutil
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gdk, Pango, GdkPixbuf
from cupertinocontrolcenter.i18n import _
from cupertinocontrolcenter.functions import dark_mode
from cupertinocontrolcenter.spawn import spawn

# First button. By default it is a Dark Mode switch (see dark_mode.py).
# Optionally it can drive your own theme script instead: if THEME_SCRIPT exists, the button
# becomes "Theme" and cycles through auto, sunrise, day, sunset, night. The script is called
# as `python3 THEME_SCRIPT <mode>` and reports its state in THEME_CACHE_DIR:
#   last_mode        "<theme>_<wallpaper>", e.g. "night_sunset"
#   override_active  the forced mode; no file means automatic
THEME_SCRIPT = os.path.expanduser("~/.local/bin/auto_theme.py")
THEME_CACHE_DIR = os.path.expanduser("~/.cache/theme-switcher")


def _markup(text, attrs=""):
    return f"<span{' ' + attrs if attrs else ''}>{GLib.markup_escape_text(text)}</span>"


class DisplayView(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.app = Gtk.Application.get_default()
        self.last_non_zero_volume = 50

        # Order the Theme button cycles through; must match THEME_SCRIPT's modes
        self.theme_modes = ["auto", "sunrise", "day", "sunset", "night"]
        self._theme_ui_mode = None   # mode shown during rapid clicks, before the script runs
        self._theme_timer = None
        self._dark_target = None     # Dark Mode state shown while a switch is pending
        self._dark_timer = None

        # Outer margins: content and separators sit 15 px from the window edge
        self.set_margin_start(7); self.set_margin_end(7)
        self.set_margin_top(8); self.set_margin_bottom(6)
        self.get_style_context().remove_class("styledwidgetbox")

        # --- Header: clicking the title goes back to the main view ---
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        header.set_margin_start(6); header.set_margin_end(0)
        header.set_margin_bottom(3); header.set_margin_top(6)
        h_eb = Gtk.EventBox(); h_eb.set_visible_window(False)
        lbl_h = Gtk.Label()
        lbl_h.set_markup(_markup(_("Display"), "weight='bold' size='medium'"))
        lbl_h.set_halign(Gtk.Align.START); lbl_h.set_margin_start(2)
        h_eb.add(lbl_h)
        h_eb.connect("button-press-event", lambda w, e: self._go_back() if e.button == 1 else False)
        header.pack_start(h_eb, True, True, 0)
        self.pack_start(header, False, False, 0)

        # --- Brightness slider (full width) ---
        slider_outer_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        slider_outer_box.set_margin_bottom(2)
        slider_outer_box.set_margin_top(3)
        slider_outer_box.set_margin_start(6); slider_outer_box.set_margin_end(6)

        current_bright = 50   # fallback if brightness can't be read
        try:
            bright_out = subprocess.check_output(["brightnessctl", "g"], text=True, stderr=subprocess.DEVNULL).strip()
            max_bright = subprocess.check_output(["brightnessctl", "m"], text=True, stderr=subprocess.DEVNULL).strip()
            if bright_out and max_bright:
                current_bright = int(round(int(bright_out) * 100 / int(max_bright)))   # same rounding as the main view
        except:
            try:
                if self.app and hasattr(self.app, 'view_stack') and self.app.view_stack:
                    main_view = self.app.view_stack.get_child_by_name("main_view")
                    for child in main_view.get_children():
                        if "DisplayLevelBox" in type(child).__name__:
                            current_bright = int(child.slider.get_value())
            except:
                pass

        # Same range as the main view slider: 0 = backlight off
        self.adj = Gtk.Adjustment(value=current_bright, lower=0, upper=100, step_increment=5)
        self.sub_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.adj)
        self.sub_scale.set_draw_value(False)
        self.sub_scale.set_hexpand(True)
        self.sub_scale.set_size_request(-1, -1)
        self.sub_scale.get_style_context().add_class("widgetslider")
        self.sub_scale.connect("value-changed", self._on_sub_brightness_changed)

        slider_overlay = Gtk.Overlay()
        slider_overlay.add(self.sub_scale)

        self.icon_overlay_eb = Gtk.EventBox()
        self.icon_overlay_eb.set_visible_window(False)

        self.internal_icon = Gtk.Image()
        self.internal_icon.get_style_context().add_class("mac-slider-embedded-icon")
        self._update_internal_icon(current_bright)
        # Separate handler for the icon: the main view slider temporarily blocks the
        # brightness handler above when it syncs this slider, and the icon must still update.
        self.sub_scale.connect("value-changed", lambda s: self._update_internal_icon(int(s.get_value())))

        self.icon_overlay_eb.set_halign(Gtk.Align.START)
        self.icon_overlay_eb.set_valign(Gtk.Align.CENTER)
        self.icon_overlay_eb.set_margin_start(6)
        self.icon_overlay_eb.set_margin_bottom(1)

        # The icon is added to the overlay directly, not inside an EventBox, with pass-through:
        # clicks on it reach the slider. An EventBox swallows the click even with pass-through.
        self.internal_icon.set_halign(Gtk.Align.START)
        self.internal_icon.set_valign(Gtk.Align.CENTER)
        self.internal_icon.set_margin_start(6)
        self.internal_icon.set_margin_bottom(1)
        slider_overlay.add_overlay(self.internal_icon)
        slider_overlay.set_overlay_pass_through(self.internal_icon, True)

        slider_outer_box.pack_start(slider_overlay, True, True, 0)
        self.pack_start(slider_outer_box, False, False, 0)

        # --- Buttons: Theme, Night Shift, Presentation Mode ---
        grid = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        grid.get_style_context().add_class("styledwidgetbox")
        # Equal-width columns, so the buttons are evenly spaced and centered as a group
        grid.set_homogeneous(True)
        # styledwidgetbox padding (6 px) + 2 px = the slider's edges, so the columns split
        # exactly the slider's width
        grid.set_margin_start(2); grid.set_margin_end(2); grid.set_margin_bottom(0)

        # A. Theme script or Dark Mode
        self._theme_script = os.path.exists(THEME_SCRIPT)
        t_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        t_box.set_margin_top(3); t_box.set_margin_bottom(0)
        t_box.set_margin_start(0); t_box.set_margin_end(0)
        t_eb = Gtk.EventBox(); t_eb.set_visible_window(False)

        self.t_icon = Gtk.Image()
        self.t_icon.set_halign(Gtk.Align.CENTER)

        t_lbl_title = Gtk.Label()
        t_lbl_title.set_markup(_markup(_("Theme") if self._theme_script else _("Dark Mode"),
                                       "weight='bold' size='small'"))
        self.t_lbl_status = Gtk.Label()
        self.t_lbl_status.get_style_context().add_class("general_desc")
        t_handler = self._on_theme_clicked if self._theme_script else self._on_dark_mode_clicked
        t_box.pack_start(self._circle_button(self.t_icon, t_handler, self.ICON_NUDGE['theme']), False, False, 0)
        t_box.pack_start(t_lbl_title, False, False, 0)
        t_box.pack_start(self.t_lbl_status, False, False, 0)
        t_eb.add(t_box)

        # B. Night Shift
        ns_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        ns_box.set_margin_top(3); ns_box.set_margin_bottom(0)
        ns_box.set_margin_start(0); ns_box.set_margin_end(0)
        ns_eb = Gtk.EventBox(); ns_eb.set_visible_window(False)

        self.ns_icon = Gtk.Image()
        self.ns_icon.set_halign(Gtk.Align.CENTER)

        ns_lbl_title = Gtk.Label()
        ns_lbl_title.set_markup(_markup(_("Night Shift"), "weight='bold' size='small'"))
        self.ns_lbl_status = Gtk.Label()
        self.ns_lbl_status.get_style_context().add_class("general_desc")
        ns_box.pack_start(self._circle_button(self.ns_icon, self._on_gammastep_clicked, self.ICON_NUDGE['nightshift']), False, False, 0)
        ns_box.pack_start(ns_lbl_title, False, False, 0)
        ns_box.pack_start(self.ns_lbl_status, False, False, 0)
        ns_eb.add(ns_box)

        # C. Presentation Mode (xfce4-power-manager)
        pm_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        pm_box.set_margin_top(3); pm_box.set_margin_bottom(0)
        pm_box.set_margin_start(0); pm_box.set_margin_end(0)
        pm_eb = Gtk.EventBox(); pm_eb.set_visible_window(False)

        self.pm_icon = Gtk.Image()
        self.pm_icon.set_halign(Gtk.Align.CENTER)

        pm_lbl_title = Gtk.Label()
        pm_lbl_title.set_markup(_markup(_("Presentation"), "weight='bold' size='small'"))
        self.pm_lbl_status = Gtk.Label()
        self.pm_lbl_status.get_style_context().add_class("general_desc")
        self.pm_lbl_status.set_margin_start(1)
        pm_box.pack_start(self._circle_button(self.pm_icon, self._on_presentation_mode_clicked, self.ICON_NUDGE['presentation']), False, False, 0)
        pm_box.pack_start(pm_lbl_title, False, False, 0)
        pm_box.pack_start(self.pm_lbl_status, False, False, 0)
        pm_eb.add(pm_box)

        # Buttons whose tool is missing are left out; the others share the row
        if self._theme_script or dark_mode.available():
            grid.pack_start(t_eb, True, True, 0)
        if shutil.which("gammastep"):
            grid.pack_start(ns_eb, True, True, 0)
        if shutil.which("xfconf-query"):
            grid.pack_start(pm_eb, True, True, 0)
        self.pack_start(grid, False, False, 0)

        slider_sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        slider_sep.set_margin_top(0); slider_sep.set_margin_bottom(0)
        slider_sep.set_margin_start(8); slider_sep.set_margin_end(8)   # 16 px from the window edge
        slider_sep.get_style_context().add_class("tight-bottom")
        self.pack_start(slider_sep, False, False, 0)

        # --- "Display Settings…" row ---
        pref_row = Gtk.ListBox(); pref_row.set_margin_start(6); pref_row.set_margin_end(0)
        pref_row.set_selection_mode(Gtk.SelectionMode.NONE)
        pref_row.get_style_context().add_class("transparent-listbox")

        p_item = Gtk.ListBoxRow()
        p_item.get_style_context().add_class("mac-wifi-row")
        p_item.get_style_context().add_class("row-hover-effect")
        p_item.get_style_context().add_class("mac-bottom-settings-row")

        p_eb = Gtk.EventBox(); p_eb.set_visible_window(False)
        p_lbl = Gtk.Label(label=_("Display Settings…"), xalign=0)
        p_lbl.set_margin_start(0); p_lbl.set_margin_top(4); p_lbl.set_margin_bottom(4)
        p_eb.add(p_lbl); p_item.add(p_eb); pref_row.add(p_item)
        p_eb.connect("button-press-event", lambda w, e: spawn([self.DISPLAY_SETTINGS_CMD]) if e.button == 1 else False)

        self.pack_start(pref_row, False, False, 0)

        self._ns_state = None          # last known Night Shift state
        self._ns_busy_until = 0        # colour transition after our own click; don't measure
        self.refresh_states()
        self.show_all()

        # Pick up changes made elsewhere (e.g. gammastep-indicator): while this view is
        # open, check the real state every 3 s
        GLib.timeout_add_seconds(3, self._ns_periodic)

    DISPLAY_SETTINGS_CMD = "xfce4-display-settings"
    CIRCLE = 34   # button circle diameter (px)
    # Per-button horizontal fine-tuning (px of left margin); the circle moves right by half
    # this value. It compensates for circles that are not centered inside their PNG. Measure
    # from a screenshot: the circle's center should line up with its label's center.
    ICON_NUDGE = {"theme": 6, "nightshift": 5, "presentation": 7}
    ICON_GAP = 5  # space between the circle and its title

    def _circle_button(self, icon, handler, nudge=0):
        """Only the circle itself is clickable, not the labels or the empty corners."""
        icon.set_halign(Gtk.Align.CENTER)
        eb = Gtk.EventBox()
        eb.set_visible_window(False)
        eb.set_halign(Gtk.Align.CENTER)
        eb.set_margin_start(nudge)
        eb.set_margin_bottom(self.ICON_GAP)
        eb.add(icon)

        def on_press(w, event):
            a = w.get_allocation()
            r = self.CIRCLE / 2
            if (event.x - a.width / 2) ** 2 + (event.y - a.height / 2) ** 2 > r * r:
                return False   # outside the circle
            return handler(w, event)
        eb.connect("button-press-event", on_press)
        return eb

    def _update_internal_icon(self, value):
        """Same thresholds as the main view: low / medium / high sun."""
        if value <= 33: icon_name = "display-brightness-low-symbolic"
        elif value <= 66: icon_name = "display-brightness-medium-symbolic"
        else: icon_name = "display-brightness-high-symbolic"
        self.internal_icon.set_from_icon_name(icon_name, Gtk.IconSize.MENU)

    def _on_sub_brightness_changed(self, scale):
        val = int(self.adj.get_value())
        # Brightness is set through the main view slider, so both follow the same level rules
        try:
            if self.app and hasattr(self.app, 'view_stack') and self.app.view_stack:
                main_view = self.app.view_stack.get_child_by_name("main_view")
                for child in main_view.get_children():
                    if "DisplayLevelBox" in type(child).__name__:
                        if int(child.slider.get_value()) != val:
                            child.slider.set_value(val)
        except Exception as ex:
            print(f"Could not sync the main view slider: {ex}")

    THEME_LABELS = {"auto": _("Automatic"), "sunrise": _("Sunrise"), "day": _("Day"),
                    "sunset": _("Sunset"), "night": _("Night")}
    THEME_IS_DARK = {"sunrise": True, "day": False, "sunset": True, "night": True}  # as in THEME_SCRIPT
    THEME_APPLY_DELAY = 700  # ms after the last click before the theme is applied

    def refresh_states(self):
        try:
            if self.app and hasattr(self.app, 'view_stack') and self.app.view_stack:
                main_view = self.app.view_stack.get_child_by_name("main_view")
                for child in main_view.get_children():
                    if "DisplayLevelBox" in type(child).__name__:
                        main_val = int(child.slider.get_value())
                        if int(self.sub_scale.get_value()) != main_val:
                            self.sub_scale.set_value(main_val)
        except: pass

        if self._theme_script:
            self._refresh_theme_script_state()
        else:
            dark = self._dark_target if self._dark_target is not None else dark_mode.is_dark()
            self.t_lbl_status.set_markup(_markup(_("On") if dark else _("Off")))
            self._set_theme_icon(dark)

        # Night Shift: show the last known state now, check the real state in the background
        if self._ns_state is not None:
            self._set_ns_ui(self._ns_state)
        threading.Thread(target=self._ns_detect, daemon=True).start()

        # Presentation Mode
        is_pm_on = False
        try:
            pm_out = subprocess.check_output(["xfconf-query", "-c", "xfce4-power-manager", "-p", "/xfce4-power-manager/presentation-mode"], text=True).strip()
            is_pm_on = (pm_out.lower() == "true")
        except:
            pass

        self.pm_lbl_status.set_markup(_markup(_("On") if is_pm_on else _("Off")))
        pm_img = "icons/presentation.png" if is_pm_on else "icons/presentation-d.png"
        try:
            pb_p = GdkPixbuf.Pixbuf.new_from_file(pm_img).scale_simple(34, 34, GdkPixbuf.InterpType.BILINEAR)
            self.pm_icon.set_from_pixbuf(pb_p)
        except Exception:
            self.pm_icon.set_from_file(pm_img)

        if self.app and hasattr(self.app, '_force_single_resize'):
            GLib.idle_add(self.app._force_single_resize)

    def _refresh_theme_script_state(self):
        last_mode_file = os.path.join(THEME_CACHE_DIR, "last_mode")
        override_file = os.path.join(THEME_CACHE_DIR, "override_active")

        current_theme, current_wp = "day", "day"
        if os.path.exists(last_mode_file):
            try:
                with open(last_mode_file, "r") as f:
                    raw = f.read().strip()
                    if "_" in raw: current_theme, current_wp = raw.split("_", 1)
            except: pass

        status_text = self.THEME_LABELS[current_wp if current_wp in self.THEME_IS_DARK else "day"]

        if os.path.exists(override_file):
            try:
                with open(override_file, "r") as f:
                    m = f.read().strip()
                    if m in self.THEME_IS_DARK:
                        status_text = self.THEME_LABELS[m]
            except: pass
        else:
            status_text = self.THEME_LABELS["auto"]

        self.t_lbl_status.set_markup(_markup(status_text))
        t_img = "icons/theme-dark.png" if current_theme == "night" else "icons/theme-light.png"
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file(t_img).scale_simple(34, 34, GdkPixbuf.InterpType.BILINEAR)
            self.t_icon.set_from_pixbuf(pb)
        except: self.t_icon.set_from_file(t_img)

    DARK_APPLY_DELAY = 300  # ms after the last click before the theme is switched

    def _on_dark_mode_clicked(self, widget, event):
        """The label and icon change right away; the theme is switched once the clicks stop."""
        if event.button != 1:
            return False
        current = self._dark_target if self._dark_target is not None else dark_mode.is_dark()
        self._dark_target = not current
        self.t_lbl_status.set_markup(_markup(_("On") if self._dark_target else _("Off")))
        self._set_theme_icon(self._dark_target)
        if self._dark_timer:
            GLib.source_remove(self._dark_timer)
        self._dark_timer = GLib.timeout_add(self.DARK_APPLY_DELAY, self._apply_dark_mode)
        return True

    def _apply_dark_mode(self):
        self._dark_timer = None
        dark = self._dark_target

        def work():
            dark_mode.set_dark(dark)
            GLib.idle_add(self._dark_mode_applied)
        threading.Thread(target=work, daemon=True).start()
        return False

    def _dark_mode_applied(self):
        if self._dark_timer is None:     # no new click in the meantime
            self._dark_target = None
            self.refresh_states()
        return False

    def _on_theme_clicked(self, widget, event):
        """A click only updates the label and icon. The theme itself is applied once, 700 ms
        after the clicks stop, so stepping through several modes runs THEME_SCRIPT only once."""
        if event.button != 1:
            return False
        override_file = os.path.join(THEME_CACHE_DIR, "override_active")

        current = self._theme_ui_mode
        if current is None:
            current = "auto"
            try:
                with open(override_file, "r") as f:
                    current = f.read().strip() or "auto"
            except Exception:
                pass
        if current not in self.theme_modes:
            current = "auto"
        target = self.theme_modes[(self.theme_modes.index(current) + 1) % len(self.theme_modes)]
        self._theme_ui_mode = target

        self.t_lbl_status.set_markup(_markup(self.THEME_LABELS[target]))
        if target in self.THEME_IS_DARK:
            self._set_theme_icon(self.THEME_IS_DARK[target])

        if self._theme_timer:
            GLib.source_remove(self._theme_timer)
        self._theme_timer = GLib.timeout_add(self.THEME_APPLY_DELAY, self._apply_theme_mode)
        return True

    def _apply_theme_mode(self):
        self._theme_timer = None
        target = self._theme_ui_mode
        override_file = os.path.join(THEME_CACHE_DIR, "override_active")
        args = ["/usr/bin/python3", THEME_SCRIPT]
        try:
            if target == "auto":
                if os.path.exists(override_file):
                    os.remove(override_file)
                args.append("auto")
            else:
                os.makedirs(os.path.dirname(override_file), exist_ok=True)
                with open(override_file, "w") as f:
                    f.write(target)
                args.append(target)
        except Exception as e:
            print(f"Could not write the theme override file: {e}")

        try:
            proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # Re-read the state when the script exits. proc is handed over so it stays alive
            # until then (letting go of a running process makes Python warn)
            GLib.child_watch_add(GLib.PRIORITY_DEFAULT, proc.pid, self._on_theme_script_done, proc)
        except Exception as e:
            print(f"Could not start {THEME_SCRIPT}: {e}")
            self._theme_ui_mode = None
        return False

    def _on_theme_script_done(self, pid, status, proc):
        proc.returncode = status          # GLib has already collected it; Python mustn't wait again
        if self._theme_timer is None:     # no new click in the meantime
            self._theme_ui_mode = None
            self.refresh_states()

    def _set_theme_icon(self, dark):
        t_img = "icons/theme-dark.png" if dark else "icons/theme-light.png"
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file(t_img).scale_simple(34, 34, GdkPixbuf.InterpType.BILINEAR)
            self.t_icon.set_from_pixbuf(pb)
        except Exception:
            self.t_icon.set_from_file(t_img)

    # ── Night Shift (gammastep) ─────────────────────────────
    # Paused state is remembered in this file (shared with THEME_SCRIPT)
    NS_LOCK = os.path.join(THEME_CACHE_DIR, "ns_pause_lock")
    NS_FADE_S = 4.0   # gammastep fades colours when toggled; don't measure during the fade

    @staticmethod
    def _gs_running():
        # -x: exact name, so gammastep-indicator doesn't match
        return subprocess.run(["pgrep", "-x", "gammastep"], capture_output=True).returncode == 0

    @staticmethod
    def _screen_tinted():
        """Is the screen actually tinted right now? xrandr shows the display's colour ramp as
        'Gamma'; it differs from 1.0:1.0:1.0 while gammastep is active. This is the real
        state, whoever toggled it (indicator, shortcut, this view)."""
        try:
            out = subprocess.check_output(["xrandr", "--verbose", "--current"], text=True,
                                          stderr=subprocess.DEVNULL, timeout=3)
        except Exception:
            return None
        for m in re.finditer(r"Gamma:\s*([\d.]+):([\d.]+):([\d.]+)", out):
            if any(abs(float(v) - 1.0) > 0.03 for v in m.groups()):
                return True
        return False

    @staticmethod
    def _night_expected():
        """Should the screen be tinted now, according to gammastep's settings? None if unknown."""
        try:
            out = subprocess.check_output(["gammastep", "-p"], text=True,
                                          stderr=subprocess.STDOUT, timeout=3)
            m = re.search(r"(\d{4,5})\s*K", out)
            return int(m.group(1)) < 6300 if m else None
        except Exception:
            return None

    def _ns_detect(self):
        if time.monotonic() < self._ns_busy_until:
            return
        if not self._gs_running():
            state = False
        else:
            tinted = self._screen_tinted()
            if tinted:
                state = True
            elif tinted is False and self._night_expected():
                # Night time but not tinted: someone (e.g. the indicator) paused it
                state = False
            else:
                # Daytime: on or off looks the same, so trust the saved state
                state = not os.path.exists(self.NS_LOCK)
        self._ns_save(state)
        GLib.idle_add(self._set_ns_ui, state)

    def _ns_save(self, state):
        """Keep the lock file in line with the real state (also for changes made elsewhere)."""
        try:
            if state and os.path.exists(self.NS_LOCK):
                os.remove(self.NS_LOCK)
            elif not state and not os.path.exists(self.NS_LOCK):
                os.makedirs(os.path.dirname(self.NS_LOCK), exist_ok=True)
                with open(self.NS_LOCK, "w") as f:
                    f.write("paused")
        except Exception:
            pass

    def _set_ns_ui(self, state):
        self._ns_state = state
        self.ns_lbl_status.set_markup(_markup(_("On") if state else _("Off")))
        ns_img = "icons/nightshift.png" if state else "icons/nightshift-d.png"
        try:
            pb_n = GdkPixbuf.Pixbuf.new_from_file(ns_img).scale_simple(34, 34, GdkPixbuf.InterpType.BILINEAR)
            self.ns_icon.set_from_pixbuf(pb_n)
        except Exception:
            self.ns_icon.set_from_file(ns_img)
        return False

    def _ns_periodic(self):
        try:
            visible = self.app.view_stack.get_visible_child_name() == "display_view"
        except Exception:
            visible = False
        if visible:
            threading.Thread(target=self._ns_detect, daemon=True).start()
        return True

    def _on_gammastep_clicked(self, widget, event):
        """Same as gammastep-indicator: send SIGUSR1 and let gammastep fade the colours."""
        if event.button != 1:
            return False
        try:
            current = self._ns_state if self._ns_state is not None else not os.path.exists(self.NS_LOCK)
            if not self._gs_running():
                # Not running: start the indicator if installed, otherwise gammastep itself
                cmd = ["gammastep-indicator"] if shutil.which("gammastep-indicator") else ["gammastep"]
                spawn(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                      start_new_session=True)
                new_state = True
            else:
                subprocess.run(["pkill", "-USR1", "-x", "gammastep"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                new_state = not current
            self._ns_save(new_state)
            self._set_ns_ui(new_state)
            self._ns_busy_until = time.monotonic() + self.NS_FADE_S
            # Verify the real state once the fade is over
            GLib.timeout_add(int(self.NS_FADE_S * 1000) + 300,
                             lambda: threading.Thread(target=self._ns_detect, daemon=True).start() or False)
        except Exception as ex:
            print(f"Could not toggle Night Shift: {ex}")
        return True

    def _on_presentation_mode_clicked(self, widget, event):
        if event.button == 1:
            try:
                pm_out = subprocess.check_output(["xfconf-query", "-c", "xfce4-power-manager", "-p", "/xfce4-power-manager/presentation-mode"], text=True).strip()
                target_state = "false" if pm_out.lower() == "true" else "true"
                subprocess.run(["xfconf-query", "-c", "xfce4-power-manager", "-p", "/xfce4-power-manager/presentation-mode", "-s", target_state])
            except:
                subprocess.run(["xfconf-query", "-c", "xfce4-power-manager", "-p", "/xfce4-power-manager/presentation-mode", "-n", "-t", "bool", "-s", "true"])

            GLib.timeout_add(150, lambda: self.refresh_states() or False)
            return True
        return False

    def _go_back(self):
        if self.app and hasattr(self.app, 'view_stack') and self.app.view_stack:
            self.app.view_stack.set_visible_child_name("main_view")
            if hasattr(self.app, '_force_single_resize'):
                GLib.idle_add(self.app._force_single_resize)
