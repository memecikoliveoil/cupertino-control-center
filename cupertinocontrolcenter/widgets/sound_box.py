import gi
import os
import signal
import ctypes
import threading
import subprocess
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gio, Gdk, GdkPixbuf
from cupertinocontrolcenter.functions.sound_device import SoundInformation
from cupertinocontrolcenter.i18n import _

# ─────────────────────────────────────────────────────────────
# Round button next to the slider (same as in display_level.py)
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


class SoundLevelBox(Gtk.Box):
    def __init__(self):
        super().__init__()
        self.set_hexpand(True)
        self.set_size_request(-1, -1) 
        
        self.sound = SoundInformation()
        self.get_style_context().add_class("styledwidgetbox")
        self.last_non_zero_volume = 50 

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        # The slider and title line up with the icons in the Connections tile, as on macOS
        content_box.set_margin_start(4)
        content_box.set_margin_end(6)
        content_box.set_hexpand(True)

        # Title: shows a "›" on hover; clicking opens the Sound view
        title_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        title_hbox.set_hexpand(True)
        
        title_eb = Gtk.EventBox()
        title_eb.set_visible_window(False)
        title_eb.set_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        
        title_inner_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        
        display_label = Gtk.Label(label=_("Sound"), xalign=0)
        display_label.get_style_context().add_class("general_title")
        display_label.set_margin_start(2)
        
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
        
        adjustment = Gtk.Adjustment(value=self.sound.get_volume(), lower=0, upper=100, step_increment=1)
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
        self.icon_click_eb.set_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        
        self.internal_icon = Gtk.Image()
        self.internal_icon.get_style_context().add_class("mac-slider-embedded-icon")        
        self.icon_click_eb.set_halign(Gtk.Align.START)
        self.icon_click_eb.set_valign(Gtk.Align.CENTER)
        self.icon_click_eb.set_margin_start(6)
        self.icon_click_eb.set_margin_bottom(1)              
        
        # The icon is added to the overlay directly, not inside an EventBox, with pass-through:
        # clicks on it reach the slider. An EventBox swallows the click even with pass-through.
        # A click exactly on the icon toggles mute (_on_slider_press_icon).
        self.internal_icon.set_halign(Gtk.Align.START)
        self.internal_icon.set_valign(Gtk.Align.CENTER)
        self.internal_icon.set_margin_start(6)
        self.internal_icon.set_margin_bottom(1)
        slider_overlay.add_overlay(self.internal_icon)
        slider_overlay.set_overlay_pass_through(self.internal_icon, True)
        self.slider.connect("button-press-event", self._on_slider_press_icon)

        # Button: opens the sound settings (pavucontrol)
        sound_btn = Gtk.Button()
        sound_btn.get_style_context().add_class("sliderbutton")
        sound_btn.get_style_context().add_class("level-side-button")
        sound_btn.set_relief(Gtk.ReliefStyle.NONE)
        sound_btn.connect("enter-notify-event", lambda *_: self._set_side_icon(hover=True))
        sound_btn.connect("leave-notify-event", lambda *_: self._set_side_icon(hover=False))
        self.sound_icon = SideIcon("airplay", "icons/control-sound-dark.png")
        sound_btn.add(self.sound_icon)
        sound_btn.connect("clicked", self.do_open_sound_app)

        slider_row.pack_start(slider_overlay, True, True, 0)
        slider_row.pack_start(sound_btn, False, False, 0)

        # Title padding 1 (slider is 22 px tall), so the tile and window keep their height
        content_box.pack_start(title_hbox, False, False, 1)
        content_box.pack_start(slider_row, True, False, 2)

        self.pack_start(content_box, True, True, 0) 

        initial_vol = self.sound.get_volume()
        if initial_vol > 0: self.last_non_zero_volume = initial_vol
            
        self._update_internal_icon_instantly(initial_vol)
        # Load the icon right away (loading it later makes the tile jump); it follows
        # color-scheme changes by itself
        self._iface_settings = Gio.Settings.new("org.gnome.desktop.interface")
        self._iface_settings.connect("changed::color-scheme", lambda *_: self.apply_dynamic_icon_theme())
        self.apply_dynamic_icon_theme()
        # Instant updates for volume keys and other programs: PulseAudio/PipeWire reports
        # changes through `pactl subscribe` (no CPU while waiting). The 3 s poll is a fallback.
        self._ev_pending = None
        threading.Thread(target=self._watch_volume_events, daemon=True).start()
        GLib.timeout_add(3000, self.refresh_slider)

    def _on_title_clicked(self, widget, event):
        """Opens the Sound view."""
        if event.button == 1:
            try:
                app = Gtk.Application.get_default()
                if app and hasattr(app, 'view_stack') and app.view_stack:
                    app.view_stack.set_visible_child_name("sound_view")
                    sound_page = app.view_stack.get_child_by_name("sound_view")
                    if sound_page and hasattr(sound_page, 'refresh_audio_devices'):
                        sound_page.refresh_audio_devices()
            except Exception as e:
                print(f"Could not open the Sound view: {e}")
            return True
        return False

    def _on_embedded_icon_pressed(self, widget, event):
        if event.button == 1:
            curr_val = int(self.slider.get_value())
            if curr_val > 0:
                self.last_non_zero_volume = curr_val
                self.slider.set_value(0)
            else:
                self.slider.set_value(self.last_non_zero_volume)
            return True
        return False

    def _set_side_icon(self, hover=False):
        self.sound_icon.set_state(hover=hover)
        return False

    def apply_dynamic_icon_theme(self):
        try:
            dark = "dark" in self._iface_settings.get_string("color-scheme").lower()
        except Exception:
            dark = False
        self.sound_icon.set_state(dark=dark, hover=False)
        return False

    def _update_internal_icon_instantly(self, value):
        if value == 0: icon_name = "audio-volume-muted-symbolic"
        elif value <= 33: icon_name = "audio-volume-low-symbolic"
        elif value <= 66: icon_name = "audio-volume-medium-symbolic"
        else: icon_name = "audio-volume-high-symbolic"
        self.internal_icon.set_from_icon_name(icon_name, Gtk.IconSize.MENU)

    def on_slider_changed(self, slider):
        value = int(slider.get_value())
        self.sound.set_volume(value)
        self._update_internal_icon_instantly(value)
        if value > 0: self.last_non_zero_volume = value

        try:
            app = Gtk.Application.get_default()
            if app and hasattr(app, 'view_stack') and app.view_stack:
                sound_view = app.view_stack.get_child_by_name("sound_view")
                if sound_view and hasattr(sound_view, 'sub_scale') and sound_view.sub_scale:
                    if int(sound_view.sub_scale.get_value()) != value:
                        sound_view.sub_scale.disconnect_by_func(sound_view._on_sub_volume_changed)
                        sound_view.sub_scale.set_value(value)
                        sound_view.sub_scale.connect("value-changed", sound_view._on_sub_volume_changed)
        except: pass

    def refresh_slider(self):
        """Shows the system volume on the sliders. While muted the slider shows 0 with the
        muted icon (as on macOS); the real level is kept and comes back on unmute. The
        handler is blocked while setting the value, so it isn't written back to the system."""
        self.sound.refresh()
        vol = self.sound.get_volume()
        muted = getattr(self.sound, "muted", False)
        shown = 0 if muted else vol
        if int(self.slider.get_value()) != shown:
            self.slider.handler_block_by_func(self.on_slider_changed)
            self.slider.set_value(shown)
            self.slider.handler_unblock_by_func(self.on_slider_changed)
        self._update_internal_icon_instantly(shown)
        if vol > 0: self.last_non_zero_volume = vol

        try:
            app = Gtk.Application.get_default()
            if app and hasattr(app, 'view_stack') and app.view_stack:
                sound_view = app.view_stack.get_child_by_name("sound_view")
                if sound_view and hasattr(sound_view, 'sub_scale') and sound_view.sub_scale:
                    if int(sound_view.sub_scale.get_value()) != shown:
                        sound_view.sub_scale.disconnect_by_func(sound_view._on_sub_volume_changed)
                        sound_view.sub_scale.set_value(shown)
                        sound_view.sub_scale.connect("value-changed", sound_view._on_sub_volume_changed)
        except: pass

        return True

    def _on_slider_press_icon(self, scale, event):
        """A click exactly on the icon toggles mute, like the mute key: the slider shows 0, and
        another click restores the level. Anywhere else, even right next to the icon, is a
        normal slider click, so low levels stay reachable."""
        if event.button != 1:
            return False
        r = scale.translate_coordinates(self.internal_icon, int(event.x), int(event.y))
        a = self.internal_icon.get_allocation()
        # Hit area: the icon minus 4 px on the right, where the image is wider than the
        # glyph (tune the number to taste)
        if not r or not (0 <= r[0] < a.width - 4 and 0 <= r[1] < a.height):
            return False
        subprocess.Popen(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        GLib.timeout_add(80, self._after_mute_toggle)
        return True

    def _after_mute_toggle(self):
        self.refresh_slider()
        return False

    def _watch_volume_events(self):
        """pactl subscribe prints a line on every volume/mute change. It exits with us."""
        def die_with_parent():
            try:
                ctypes.CDLL("libc.so.6").prctl(1, signal.SIGTERM)   # PR_SET_PDEATHSIG
            except Exception:
                pass
        try:
            env = dict(os.environ, LC_ALL="C")
            proc = subprocess.Popen(["pactl", "subscribe"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    text=True, env=env, preexec_fn=die_with_parent)
            for line in proc.stdout:
                l = line.lower()
                if "'change'" in l and (" sink " in l or " server" in l):
                    GLib.idle_add(self._schedule_volume_refresh)
        except Exception as e:
            print(f"Could not listen for volume changes: {e}")

    def _schedule_volume_refresh(self):
        # Holding a key sends a burst of events; refresh once per 60 ms
        if self._ev_pending:
            return False
        def run():
            self._ev_pending = None
            self.refresh_slider()
            return False
        self._ev_pending = GLib.timeout_add(60, run)
        return False

    def do_open_sound_app(self, widget):
        """Opens pavucontrol."""
        try: GLib.spawn_command_line_async("pavucontrol &")
        except: pass