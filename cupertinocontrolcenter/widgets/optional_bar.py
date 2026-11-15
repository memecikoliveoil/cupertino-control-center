import gi
import os
import json
import subprocess
import cairo
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gdk
from cupertinocontrolcenter.functions.airplane_mode import AirPlaneInformation
from cupertinocontrolcenter.functions.battery_box import BatteryBox, get_battery, _load_lp
from cupertinocontrolcenter.functions.music_recognition import MusicCapsule, MusicView
from cupertinocontrolcenter.paths import STATE_DIR
from cupertinocontrolcenter.i18n import _

# Bottom bar settings, written by settings_gui.py (keep the defaults in sync with it)
SETTINGS_PATH = os.path.join(STATE_DIR, "settings.json")
DEFAULT_SETTINGS = {
    "show_battery": True,
    "show_music": False,
    "tile_settings": True,
    "tile_screenshot": True,
    "tile_lock": True,
    "tile_power": True,
}


def load_settings():
    s = dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_PATH) as f:
            raw = json.load(f)
        # Older versions stored a single choice ("wide_slot")
        if "wide_slot" in raw and "show_battery" not in raw:
            raw["show_battery"] = raw["wide_slot"] == "battery"
            raw["show_music"] = raw["wide_slot"] == "music"
        s.update({k: v for k, v in raw.items() if k in DEFAULT_SETTINGS})
    except Exception:
        pass
    return s


SETTINGS = load_settings()
# When Battery and Music Recognition are both on:
#  - if the right-hand buttons (Screenshot / Lock / Power) are all off, Music Recognition
#    takes the right half: one row, battery left, music right
#  - if any of them is on, there is no room: Music Recognition moves to a second row
_BOTH = SETTINGS["show_battery"] and SETTINGS["show_music"]
_RIGHT_TILES = any(SETTINGS[k] for k in ("tile_screenshot", "tile_lock", "tile_power"))
MUSIC_ON_RIGHT = _BOTH and not _RIGHT_TILES
MUSIC_ON_SECOND_ROW = _BOTH and _RIGHT_TILES
# Used by main.py to tell whether the bottom bar has anything in it:
#   SHOW_BATTERY_CAPSULE  a wide capsule (Battery or Music Recognition) is shown
#   SHOW_4_WIDGETS        at least one of the small buttons is shown
SHOW_BATTERY_CAPSULE = SETTINGS["show_battery"] or SETTINGS["show_music"]
SHOW_4_WIDGETS = any(SETTINGS[k] for k in ("tile_settings", "tile_screenshot", "tile_lock", "tile_power"))

# Icon size in the small buttons. Symbolic icons are drawn on a 16 px grid, so 16 and 32 are
# sharp on 1x screens; sizes in between put lines on half pixels and look blurry.
BUTTON_ICON_PX = 16
BAR_GAP = 10                 # gap between tiles, same as between the main view's modules
BAR_HEIGHT = 46
# Main view height: 354 without the bottom bar, 410 with one row, 466 with two
# (each extra row = BAR_HEIGHT + BAR_GAP)
if not SHOW_BATTERY_CAPSULE and not SHOW_4_WIDGETS:
    WINDOW_HEIGHT = 354
else:
    WINDOW_HEIGHT = 410 + (BAR_HEIGHT + BAR_GAP if MUSIC_ON_SECOND_ROW else 0)
BATTERY_GLYPH_W = 25         # battery drawing: body + terminal nub
BATTERY_GLYPH_H = 12


class BatteryGlyph(Gtk.DrawingArea):
    """Battery drawing as in the macOS menu bar: the body fills with the charge, with a bolt
    while charging. (Theme battery icons fit a 16x16 square and look small and thin.)
    The colour comes from CSS (.opt-battery-glyph), so it follows light/dark mode.
    In Low Power Mode the fill is yellow, as on macOS. macOS doesn't turn it red on low
    charge (only with Low Power + Increase Contrast), so neither does this."""

    def __init__(self):
        super().__init__()
        self.percent = 0
        self.charging = False
        self.low_power = False
        self.set_size_request(BATTERY_GLYPH_W, BATTERY_GLYPH_H)
        self.set_valign(Gtk.Align.CENTER)
        self.get_style_context().add_class("opt-battery-glyph")
        self.connect("draw", self._draw)

    def update(self, percent, charging, low_power):
        if (percent, charging, low_power) != (self.percent, self.charging, self.low_power):
            self.percent, self.charging, self.low_power = percent, charging, low_power
            self.queue_draw()

    @staticmethod
    def _rrect(cr, x, y, w, h, r):
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -1.5708, 0)
        cr.arc(x + w - r, y + h - r, r, 0, 1.5708)
        cr.arc(x + r, y + h - r, r, 1.5708, 3.1416)
        cr.arc(x + r, y + r, r, 3.1416, 4.7124)
        cr.close_path()

    def _draw(self, widget, cr):
        fg = self.get_style_context().get_color(self.get_state_flags())
        W, H = BATTERY_GLYPH_W, BATTERY_GLYPH_H
        nub = 2
        bw = W - nub - 1            # body width
        cr.push_group()

        # Body outline (semi-transparent, as on macOS) and the nub
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.45)
        cr.set_line_width(1)
        self._rrect(cr, 0.5, 0.5, bw - 1, H - 1, 3)
        cr.stroke()
        cr.rectangle(bw + 0.5, H / 2 - 2, nub, 4)
        cr.fill()

        # Fill
        if self.low_power:
            cr.set_source_rgba(1.0, 0.80, 0.0, 1)          # yellow
        else:
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 1)
        inner_w = bw - 4
        # Whole pixels, so the fill's edge stays sharp
        fill_w = max(2, round(inner_w * max(0, min(100, self.percent)) / 100.0))
        self._rrect(cr, 2, 2, fill_w, H - 4, 1.5)
        cr.fill()

        if self.charging:
            # Bolt: clear a halo around it first so it separates from the fill
            cx = bw / 2
            pts = [(cx + 1.2, 0.5), (cx - 3.2, H / 2 + 0.6), (cx - 0.2, H / 2 + 0.6),
                   (cx - 1.2, H - 0.5), (cx + 3.2, H / 2 - 0.6), (cx + 0.2, H / 2 - 0.6)]
            cr.move_to(*pts[0])
            for p in pts[1:]:
                cr.line_to(*p)
            cr.close_path()
            path = cr.copy_path()
            cr.set_operator(cairo.OPERATOR_CLEAR)
            cr.set_line_width(2)
            cr.stroke()
            cr.set_operator(cairo.OPERATOR_OVER)
            cr.append_path(path)
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 1)
            cr.fill()

        cr.pop_group_to_source()
        cr.paint()
        return True


class OptionalBar(Gtk.Box):
    def __init__(self):
        # Vertical: row 1 [left half | spacer | right half], row 2 if needed [Music Recognition]
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=BAR_GAP)
        self.set_hexpand(True)

        self.airplane = AirPlaneInformation()

        # =========================================================================
        # Same grid as the row above:
        #   [ Battery capsule | Settings ]      = width of the Connections tile
        #   [ Screenshot | Lock | Power ]       = width of the right column
        # Gaps match the other modules (10). Widths are tied to the tiles above through
        # align_with(), not fixed numbers, so they stay aligned with any font.
        # =========================================================================
        self.battery_eb = Gtk.EventBox()
        self.battery_eb.set_visible_window(True)
        self.battery_eb.get_style_context().add_class("opt-battery-box")
        self.battery_eb.set_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.battery_eb.set_hexpand(True)

        battery_container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        battery_container.set_halign(Gtk.Align.CENTER)
        battery_container.set_margin_top(8); battery_container.set_margin_bottom(8)

        self.battery_glyph = BatteryGlyph()
        # Translators: battery percentage while unknown; use your language's percent style
        self.battery_label = Gtk.Label(label=_("--%"))
        self.battery_label.get_style_context().add_class("opt-flat-text")
        self.battery_label.set_valign(Gtk.Align.CENTER)

        battery_container.pack_start(self.battery_glyph, False, False, 0)
        battery_container.pack_start(self.battery_label, False, False, 0)
        self.battery_eb.add(battery_container)
        self.battery_eb.connect("button-press-event", self._on_battery_click)

        # main.py adds this as "battery_view"
        self.battery_inner_box = BatteryBox(
            airplane_instance=self.airplane,
            app_instance=Gtk.Application.get_default()
        )

        # Music Recognition: in the battery's place when Battery is off, otherwise on the
        # right or on a second row (see above); its history is "music_view"
        self.music_eb = MusicCapsule()
        self.music_view = MusicView()

        self.settings_eb = self._make_button("preferences-system-symbolic", ["xfce4-settings-manager"])
        self.screenshot_eb = self._make_button("accessories-screenshot-symbolic", ["xfce4-screenshooter"])
        self.lock_eb = self._make_button("changes-prevent-symbolic", ["xflock4"])
        self.power_eb = self._make_button("system-shutdown-symbolic", ["xfce4-session-logout"])

        self.left_half = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=BAR_GAP)
        self.left_half.set_size_request(-1, BAR_HEIGHT)
        self.left_half.pack_start(self.battery_eb, True, True, 0)
        if not MUSIC_ON_SECOND_ROW and not MUSIC_ON_RIGHT:
            self.left_half.pack_start(self.music_eb, True, True, 0)
        self.left_half.pack_start(self.settings_eb, False, False, 0)

        self.right_half = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=BAR_GAP)
        self.right_half.set_homogeneous(True)
        self.right_half.set_size_request(-1, BAR_HEIGHT)
        for b in (self.screenshot_eb, self.lock_eb, self.power_eb):
            b.set_hexpand(True)
            self.right_half.pack_start(b, True, True, 0)
        if MUSIC_ON_RIGHT:
            # Right-hand buttons off: Music Recognition fills the right column
            self.right_half.pack_start(self.music_eb, True, True, 0)
        # The Settings button (left half) is as wide as one of the three on the right. The
        # width comes from the right column (align_with), so it works even if they're hidden.
        self._settings_w = None

        # Keep the halves at their requested width; extra space goes to the spacer
        self.left_half.set_hexpand(False)
        self.right_half.set_hexpand(False)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        self.row1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.row1.pack_start(self.left_half, False, False, 0)
        self.row1.pack_start(spacer, True, True, 0)
        self.row1.pack_start(self.right_half, False, False, 0)
        self.pack_start(self.row1, False, False, 0)

        # Row 2: Music Recognition, as wide as the left half, aligned left
        self.row2_left = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.row2_left.set_size_request(-1, BAR_HEIGHT)
        self.row2_left.set_hexpand(False)
        self.row2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.row2.pack_start(self.row2_left, False, False, 0)
        self.row2.pack_start(Gtk.Box(), True, True, 0)
        if MUSIC_ON_SECOND_ROW:
            self.row2_left.pack_start(self.music_eb, True, True, 0)
        self.pack_start(self.row2, False, False, 0)

        # Battery updates come from UPower signals
        get_battery().add_listener(self._on_battery)
        self.show_all()

    def align_with(self, left_widget, right_widget):
        """Called by main.py: the left half gets the Connections tile's width, the right half
        the right column's. (A SizeGroup equalizes requested widths; the halves are narrower
        than those tiles, so the row above keeps its width.)"""
        self._sg_left = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        self._sg_left.add_widget(self.left_half)
        self._sg_left.add_widget(self.row2_left)
        self._sg_left.add_widget(left_widget)
        self._sg_right = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        self._sg_right.add_widget(self.right_half)
        self._sg_right.add_widget(right_widget)
        right_widget.connect("size-allocate", self._sync_settings_width)

    def _sync_settings_width(self, box, alloc):
        if getattr(self, "_settings_alone", False):
            return
        w = max(1, (alloc.width - 2 * BAR_GAP) // 3)
        if w != self._settings_w:
            self._settings_w = w
            GLib.idle_add(lambda: self.settings_eb.set_size_request(w, -1) or False)

    def _make_button(self, icon_name, cmd):
        eb = Gtk.EventBox()
        eb.set_visible_window(True)
        eb.get_style_context().add_class("opt-curved-btn")
        eb.set_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        icon = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.MENU)
        icon.set_pixel_size(BUTTON_ICON_PX)
        icon.set_halign(Gtk.Align.CENTER); icon.set_valign(Gtk.Align.CENTER)
        eb.add(icon)
        eb.connect("button-press-event", lambda w, e: self._run_cmd(e, cmd))
        return eb

    def _on_battery(self, m):
        if m.percent is None:
            self.battery_label.set_markup("<span weight='bold' size='medium'>%s</span>"
                                          % GLib.markup_escape_text(_("--%")))
            return
        # Translators: battery percentage; use your language's percent style (e.g. "%%%d")
        text = _("%d%%") % m.percent
        self.battery_label.set_markup("<span weight='bold' size='medium'>%s</span>"
                                      % GLib.markup_escape_text(text))
        self.battery_glyph.update(m.percent, m.charging, _load_lp()["on"])

    def _on_battery_click(self, widget, event):
        if event.button == 1:
            app = Gtk.Application.get_default()
            if app and hasattr(app, 'view_stack'):
                app.view_stack.set_visible_child_name("battery_view")
            return True
        return False

    def do_show_all(self):
        Gtk.Box.do_show_all(self)
        S = SETTINGS

        def off(w):
            w.set_no_show_all(True)
            w.hide()

        wide_in_row1 = S["show_battery"] or S["show_music"]
        if not S["show_battery"]:
            off(self.battery_eb)
        if not S["show_music"]:
            off(self.music_eb)
        if not MUSIC_ON_SECOND_ROW:
            off(self.row2)
        if not S["tile_settings"]:
            off(self.settings_eb)
        elif not wide_in_row1:
            # Alone in the left half: fill it
            self.settings_eb.set_hexpand(True)
            self.left_half.child_set_property(self.settings_eb, "expand", True)
            self._settings_alone = True
        if not wide_in_row1 and not S["tile_settings"]:
            off(self.left_half)

        # Right-hand buttons: the remaining ones share a hidden button's space equally
        for key, w in (("tile_screenshot", self.screenshot_eb), ("tile_lock", self.lock_eb),
                       ("tile_power", self.power_eb)):
            if not S[key]:
                off(w)
        if not (S["tile_screenshot"] or S["tile_lock"] or S["tile_power"]) and not MUSIC_ON_RIGHT:
            off(self.right_half)

        if not SHOW_BATTERY_CAPSULE and not SHOW_4_WIDGETS:
            off(self)

    def _run_cmd(self, event, cmd):
        if event.button != 1:
            return False
        try:
            # In its own session, so it survives the Control Center closing
            subprocess.Popen(cmd, start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            print(f"Could not start {cmd[0]}: {e}")
        app = Gtk.Application.get_default()
        if app and hasattr(app, "close_cc"):
            app.close_cc()
        elif app and getattr(app, "window", None):
            app.window.hide()
        return True
