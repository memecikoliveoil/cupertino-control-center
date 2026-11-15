import gi
import os
import time
import signal
import ctypes
import threading
import subprocess
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gio, Gdk, GdkPixbuf, Pango
from cupertinocontrolcenter.functions.brightness_device import BrightnessInformation
from cupertinocontrolcenter.i18n import _

# ─────────────────────────────────────────────────────────────
# Round button next to the slider
# ─────────────────────────────────────────────────────────────


class SideIcon(Gtk.DrawingArea):
    """The button's PNG icon, drawn without scaling or recoloring.
    Light mode: an opaque grey disc is put under the PNG (using the PNG's own circle as the
    mask) and the PNG is drawn on top unchanged. Dark mode: the -dark PNG as it is."""
    SIZE = 24
    LIGHT_GREY, HOVER_GREY = 176, 160   # resulting disc grey (0-255), normal and hovered

    def __init__(self, kind, dark_png):
        super().__init__()
        self.kind = kind
        self.dark_png = dark_png
        self.light_png = dark_png.replace("-dark", "")   # e.g. icons/control-sound.png
        self._cache = {}
        self.dark = False
        self.hover = False
        self.set_size_request(self.SIZE, self.SIZE)
        self.set_valign(Gtk.Align.CENTER)
        self.connect("draw", self._draw)

    def set_state(self, dark=None, hover=None):
        if dark is not None:
            self.dark = dark
        if hover is not None:
            self.hover = hover
        self.queue_draw()

    def _light_pixbuf(self, target):
        """Composites the PNG over a grey disc once (24x24, about 1 ms)."""
        pb = GdkPixbuf.Pixbuf.new_from_file(self.light_png)
        if not pb.get_has_alpha():
            return pb
        w, h, rs, n = pb.get_width(), pb.get_height(), pb.get_rowstride(), pb.get_n_channels()
        src = pb.get_pixels()
        # The circle's own colour/opacity in the PNG (a point near the top, outside the glyph)
        i0 = 2 * rs + (w // 2) * n
        c_alpha = max(src[i0 + 3], 1) / 255
        c_val = src[i0]
        grey = max(0.0, min(255.0, (target - c_val * c_alpha) / (1 - c_alpha))) if c_alpha < 1 else target
        out = bytearray(w * h * 4)
        for y in range(h):
            for x in range(w):
                i, o = y * rs + x * n, (y * w + x) * 4
                a = src[i + 3] / 255
                m = min(1.0, a / c_alpha)          # grey layer: opaque inside, soft edge
                oa = a + m * (1 - a)
                if oa <= 0:
                    continue
                for k in range(3):
                    out[o + k] = int(round((src[i + k] * a + grey * m * (1 - a)) / oa))
                out[o + 3] = int(round(oa * 255))
        return GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(bytes(out)),
                                               GdkPixbuf.Colorspace.RGB, True, 8, w, h, w * 4)

    def _pixbuf(self):
        key = "dark" if self.dark else ("hover" if self.hover else "light")
        if key not in self._cache:
            try:
                if key == "dark":
                    self._cache[key] = GdkPixbuf.Pixbuf.new_from_file(self.dark_png)
                else:
                    self._cache[key] = self._light_pixbuf(self.HOVER_GREY if key == "hover" else self.LIGHT_GREY)
            except Exception as e:
                print(f"[SideIcon] could not load the icon ({key}): {e}")
                self._cache[key] = None
        return self._cache[key]

    def _draw(self, w, cr):
        pb = self._pixbuf()
        if pb is None:
            return True
        # whole pixels only: half-pixel offsets blur the PNG
        x = (self.get_allocated_width() - pb.get_width()) // 2
        y = (self.get_allocated_height() - pb.get_height()) // 2
        Gdk.cairo_set_source_pixbuf(cr, pb, x, y)
        cr.paint()
        return True


class DisplayLevelBox(Gtk.Box):
    def __init__(self):
        super().__init__()
        self.set_hexpand(True)
        self.set_size_request(-1, -1)
        self.brightness = BrightnessInformation()
        self.get_style_context().add_class("styledwidgetbox")

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        # The slider and title line up with the icons in the Connections tile, as on macOS:
        # 6 (tile padding) + 2 + the slider's own 2 px = the Wi-Fi icon's x
        content_box.set_margin_start(4); content_box.set_margin_end(6)
        content_box.set_hexpand(False)

        # Title: shows a "›" on hover; clicking opens the Display view
        title_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        title_hbox.set_hexpand(True)
        
        title_eb = Gtk.EventBox()
        title_eb.set_visible_window(False)
        title_eb.set_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        
        title_inner_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        
        display_label = Gtk.Label(label=_("Display"), xalign=0)
        display_label.get_style_context().add_class("general_title")
        display_label.set_margin_start(2)   # with the text's own side bearing, lines up with the slider
        
        self.arrow_hint = Gtk.Label()
        self.arrow_hint.set_markup("<span size='large' weight='bold'>›</span>")
        self.arrow_hint.set_halign(Gtk.Align.END)
        self.arrow_hint.set_margin_end(0) 
        self.arrow_hint.set_no_show_all(True)
        self.arrow_hint.hide()
        self.arrow_hint.get_style_context().add_class("menu_arrow_hint")
        
        title_inner_box.pack_start(display_label, True, True, 0)
        title_inner_box.pack_end(self.arrow_hint, False, False, 0)
        title_eb.add(title_inner_box)
        
        title_eb.connect("enter-notify-event", lambda w, e: self.arrow_hint.show())
        title_eb.connect("leave-notify-event", lambda w, e: self.arrow_hint.hide())
        title_eb.connect("button-press-event", self._on_title_clicked)
        
        title_hbox.pack_start(title_eb, True, True, 0)

        # Slider and button. Both gaps measure 12 (slider end → button, button → tile edge),
        # but the slider's round end makes the left gap look wider; 9 is a 1 px optical fix.
        slider_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        slider_row.set_hexpand(True)
        
        initial_bright = self.brightness.get_current_brightness()
        # 0 = backlight off; anything above is at least one hardware level
        # (see brightness_device.set_brightness)
        adjustment = Gtk.Adjustment(value=initial_bright, lower=0, upper=100, step_increment=1)
        self.slider = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adjustment)
        self.slider.set_draw_value(False)
        self.slider.set_hexpand(True)
        self.slider.set_size_request(-1, -1)
        self.slider.get_style_context().add_class("widgetslider")
        self.slider.connect("value-changed", self.on_slider_changed)

        slider_overlay = Gtk.Overlay()
        slider_overlay.add(self.slider)
        
        self.icon_click_eb = Gtk.EventBox()
        self.icon_click_eb.set_visible_window(False)
        self.icon_click_eb.set_sensitive(False)
        
        self.internal_icon = Gtk.Image()
        self.internal_icon.get_style_context().add_class("mac-slider-embedded-icon")
        
        self.icon_click_eb.set_halign(Gtk.Align.START)
        self.icon_click_eb.set_valign(Gtk.Align.CENTER)
        self.icon_click_eb.set_margin_start(6)
        self.icon_click_eb.set_margin_bottom(1)
        
        # The icon is added to the overlay directly, not inside an EventBox, with pass-through:
        # clicks on it reach the slider. An EventBox swallows the click even with pass-through.
        self.internal_icon.set_halign(Gtk.Align.START)
        self.internal_icon.set_valign(Gtk.Align.CENTER)
        self.internal_icon.set_margin_start(6)
        self.internal_icon.set_margin_bottom(1)
        slider_overlay.add_overlay(self.internal_icon)
        slider_overlay.set_overlay_pass_through(self.internal_icon, True)

        # Button: opens the display settings
        display_btn = Gtk.Button()
        display_btn.get_style_context().add_class("sliderbutton")
        display_btn.get_style_context().add_class("level-side-button")
        display_btn.set_relief(Gtk.ReliefStyle.NONE)
        display_btn.connect("enter-notify-event", lambda *_: self._set_side_icon(hover=True))
        display_btn.connect("leave-notify-event", lambda *_: self._set_side_icon(hover=False))
        self.display_icon = SideIcon("display", "icons/control-display-dark.png")
        display_btn.add(self.display_icon)
        display_btn.connect("clicked", self.do_open_display_app)

        slider_row.pack_start(slider_overlay, True, True, 0)
        slider_row.pack_start(display_btn, False, False, 0)

        # Title padding 1 (slider is 22 px tall), so the tile and window keep their height
        content_box.pack_start(title_hbox, False, False, 1)
        content_box.pack_start(slider_row, True, False, 2)

        self.pack_start(content_box, True, True, 0)

        self._update_internal_icon_instantly(initial_bright)
        # Load the icon right away (loading it later makes the tile jump); it follows
        # color-scheme changes by itself
        self._iface_settings = Gio.Settings.new("org.gnome.desktop.interface")
        self._iface_settings.connect("changed::color-scheme", lambda *_: self.apply_dynamic_icon_theme())
        self.apply_dynamic_icon_theme()
        # Brightness-key changes: read the kernel's file every 0.25 s (cheap); without it,
        # run brightnessctl once a second
        GLib.timeout_add(250 if self.brightness.sysfs_path() else 1000, self.refresh_slider)
        # Instant updates: the kernel emits a udev "change" event on every brightness change
        # (keys included). udevadm waits for it without using CPU; the polling above is a
        # fallback.
        self._ev_pending = None
        threading.Thread(target=self._watch_backlight_events, daemon=True).start()

    def _on_title_clicked(self, widget, event):
        """Opens the Display view."""
        if event.button == 1:
            try:
                app = Gtk.Application.get_default()
                if app and hasattr(app, 'view_stack') and app.view_stack:
                    app.view_stack.set_visible_child_name("display_view")
                    display_page = app.view_stack.get_child_by_name("display_view")
                    if display_page and hasattr(display_page, 'refresh_states'):
                        display_page.refresh_states()
            except Exception as e:
                print(f"Could not open the Display view: {e}")
            return True
        return False

    def _set_side_icon(self, hover=False):
        self.display_icon.set_state(hover=hover)
        return False

    def apply_dynamic_icon_theme(self):
        try:
            dark = "dark" in self._iface_settings.get_string("color-scheme").lower()
        except Exception:
            dark = False
        self.display_icon.set_state(dark=dark, hover=False)
        return False

    def _update_internal_icon_instantly(self, value):
        if value <= 33: icon_name = "display-brightness-low-symbolic"
        elif value <= 66: icon_name = "display-brightness-medium-symbolic"
        else: icon_name = "display-brightness-high-symbolic"
        self.internal_icon.set_from_icon_name(icon_name, Gtk.IconSize.MENU)

    def on_slider_changed(self, slider):
        value = int(slider.get_value())
        self._last_set = value          # lets refresh_slider tell rounding from real changes
        self._last_set_time = time.monotonic()
        self.brightness.set_brightness(value)
        self._update_internal_icon_instantly(value)
        self._sync_view(value)

    def _sync_view(self, value):
        """Syncs the Display view's slider without triggering its brightness handler."""
        try:
            app = Gtk.Application.get_default()
            if app and hasattr(app, 'view_stack') and app.view_stack:
                display_view = app.view_stack.get_child_by_name("display_view")
                if display_view and hasattr(display_view, 'sub_scale') and display_view.sub_scale:
                    if int(display_view.sub_scale.get_value()) != value:
                        display_view.sub_scale.disconnect_by_func(display_view._on_sub_brightness_changed)
                        display_view.sub_scale.set_value(value)
                        display_view.sub_scale.connect("value-changed", display_view._on_sub_brightness_changed)
        except Exception as ex:
            print(f"Could not sync the Display view slider: {ex}")

    def refresh_slider(self):
        """Moves the sliders when brightness changes elsewhere (keys, power manager).
        Panels have few hardware levels (e.g. 15), so setting 50% may read back as 47% or 53%.
        Writing that back to the slider would make the knob jump, and would set brightness
        again through the slider's handler. So the first read after our own change, if it is
        exactly the level we wrote, is remembered as "our setting" and the knob stays. Any
        later read that differs from it is an outside change: the knob follows, with the
        handler blocked."""
        if time.monotonic() - getattr(self, "_last_set_time", 0) < 0.3:
            return True                 # brightnessctl just ran; let it finish
        current = self.brightness.read_fast()
        if getattr(self, "_last_set", None) is not None:
            self._last_set = None
            if self.brightness.current == getattr(self.brightness, "last_raw", None):
                self._settled = current  # exactly the level we wrote: keep the knob
                return True
            # Different: a brightness key was pressed in between; follow the real level
        if current == getattr(self, "_settled", None):
            return True                 # no change
        self._settled = current
        if int(self.slider.get_value()) != current:
            self.slider.handler_block_by_func(self.on_slider_changed)
            self.slider.set_value(current)
            self.slider.handler_unblock_by_func(self.on_slider_changed)
            self._sync_view(current)
        self._update_internal_icon_instantly(current)
        return True

    def _watch_backlight_events(self):
        def die_with_parent():
            try:
                ctypes.CDLL("libc.so.6").prctl(1, signal.SIGTERM)   # PR_SET_PDEATHSIG: exit with us
            except Exception:
                pass
        try:
            proc = subprocess.Popen(["udevadm", "monitor", "--kernel", "--subsystem-match=backlight"],
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                    env=dict(os.environ, LC_ALL="C"), preexec_fn=die_with_parent)
            for line in proc.stdout:
                if " change " in line:
                    GLib.idle_add(self._schedule_backlight_refresh)
        except Exception as e:
            print(f"Could not listen for brightness changes: {e}")

    def _schedule_backlight_refresh(self):
        if self._ev_pending:
            return False
        # If the slider just set a level, wait until that short window is over
        wait = 0.3 - (time.monotonic() - getattr(self, "_last_set_time", 0))
        def run():
            self._ev_pending = None
            self.refresh_slider()
            return False
        self._ev_pending = GLib.timeout_add(max(20, int(wait * 1000) + 20), run)
        return False

    def do_open_display_app(self, widget):
        """Opens the XFCE display settings."""
        try: GLib.spawn_command_line_async("xfce4-display-settings &")
        except: pass