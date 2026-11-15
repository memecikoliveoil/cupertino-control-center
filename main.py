#!/usr/bin/env python3
import sys, os, gi, threading, select, subprocess, time, warnings
# Process start time, used by the panel-button toggle check (_closed_just_now). Keep it first.
_STARTED = time.time()

# Icons and the stylesheet are loaded with paths relative to the project folder.
os.chdir(os.path.dirname(os.path.abspath(__file__)))

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gio, Gdk, Pango

from cupertinocontrolcenter.widgets.connection_box import ConnectionsBox
from cupertinocontrolcenter.widgets.disturb_box import DisturbBox
from cupertinocontrolcenter.widgets.keyboard_brightness import KbdBrightnessBox, KeyboardBrightnessView, KBD_DEVICE
from cupertinocontrolcenter.widgets.screen_mirroring import ScreenMirroringBox
from cupertinocontrolcenter.widgets.display_level import DisplayLevelBox
from cupertinocontrolcenter.widgets.sound_box import SoundLevelBox
from cupertinocontrolcenter.widgets.media_box import MediaControlBox
from cupertinocontrolcenter.widgets.network_manager import NetworkManager
from cupertinocontrolcenter.widgets.bluetooth_manager import BluetoothManager
from cupertinocontrolcenter.widgets.optional_bar import OptionalBar
from cupertinocontrolcenter.functions.battery_box import BatteryBox
from cupertinocontrolcenter.functions.display_view import DisplayView
from cupertinocontrolcenter.functions.sound_view import SoundView
from cupertinocontrolcenter.functions.media_view import MediaView
from cupertinocontrolcenter.paths import PACKAGE_DIR
from cupertinocontrolcenter.i18n import _

import importlib.util

# Optional pages, loaded from their file paths. If one fails to load (e.g. a missing
# dependency), only that page is replaced by an empty box; the rest of the app still starts.
airdrop_path = os.path.join(PACKAGE_DIR, "functions", "airdrop.py")
sm_path = os.path.join(PACKAGE_DIR, "functions", "sm_view.py")

try:
    spec = importlib.util.spec_from_file_location("airdrop_module", airdrop_path)
    airdrop_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(airdrop_module)
    AirDrop = airdrop_module.AirDropView
except Exception as e:
    print(f"Could not load the AirDrop page: {e}")
    AirDrop = lambda **kwargs: Gtk.Box()

try:
    spec_sm = importlib.util.spec_from_file_location("sm_module", sm_path)
    sm_module = importlib.util.module_from_spec(spec_sm)
    spec_sm.loader.exec_module(sm_module)
    ScreenMirroring = sm_module.SMView
except Exception as e:
    print(f"Could not load the Screen Mirroring page: {e}")
    ScreenMirroring= lambda **kwargs: Gtk.Box()

# Window name and class. Window-manager or compositor rules (picom, skippy-xd, ...) that
# match this window should use this name.
APP_NAME = _("Control Center")
GLib.set_prgname(APP_NAME)
GLib.set_application_name(APP_NAME)
Gdk.set_program_class(APP_NAME)

# Where the window opens, on the primary monitor, measured from the usable screen area
# (the screen minus panels, so it adapts to panel size and position):
#   WINDOW_RIGHT_OFFSET  distance from the area's right edge to the window's left edge (px)
#   WINDOW_TOP_GAP       gap between the top of the area (below a top panel) and the window
WINDOW_RIGHT_OFFSET = 425
WINDOW_TOP_GAP = 4
# Width of the main view (px).
MAIN_WIDTH = 370

# ------------------------------------------------------------------

class Application(Gtk.Application):
    def __init__(self, *args, **kwargs):
        super().__init__(
            *args, 
            application_id="com.github.memecikoliveoil.cupertino-control-center", 
            flags=Gio.ApplicationFlags.FLAGS_NONE, 
            **kwargs
        )
        self.window = None

    def setup_css(self):
        css_path = os.path.join(PACKAGE_DIR, "application.css")
        provider = Gtk.CssProvider()
        if os.path.exists(css_path):
            provider.load_from_path(css_path)
            Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, 800)

    def apply_theme(self, *args):
        """Runs at startup and whenever org.gnome.desktop.interface color-scheme changes;
        switches the window between the light-mode and dark-mode CSS classes."""
        try:
            if not hasattr(self, "_iface_settings"):
                # Keep a reference, or the object is garbage-collected and the signal never
                # arrives. Connect before reading: GSettings only notifies about keys that
                # have been read.
                self._iface_settings = Gio.Settings.new("org.gnome.desktop.interface")
                self._iface_settings.connect("changed::color-scheme", self.apply_theme)
            is_dark = "dark" in self._iface_settings.get_string("color-scheme").lower()
            ctx = self.window.get_style_context()
            ctx.add_class("dark-mode" if is_dark else "light-mode")
            ctx.remove_class("light-mode" if is_dark else "dark-mode")

            # Tiles with PNG icons (Keyboard Brightness, Screen Mirroring) pick their
            # light/dark icon again
            for w in getattr(self, "_theme_widgets", []):
                if hasattr(w, "apply_dynamic_icon_theme"):
                    w.apply_dynamic_icon_theme()
            # Theme button icon in the Display view
            if getattr(self, "display_details_view", None):
                self.display_details_view.refresh_states()
        except Exception as e:
            print(f"Could not apply the theme: {e}")

    # =========================================================================
    # Closing behaviour (as on macOS)
    #  - Click outside the window: closes
    #  - Esc: from a detail view goes back to the main view; on the main view closes
    #  - Clicking the panel launcher again: closes (toggle)
    #  - Switching to another window with Alt+Tab: closes
    # =========================================================================
    _CLOSED_STAMP = os.path.join(GLib.get_user_runtime_dir(), "cupertino-cc-closed")

    def close_cc(self):
        """Single exit point: writes a timestamp (see _closed_just_now), hides the window and
        quits the application."""
        if getattr(self, "_closing", False):
            return False
        self._closing = True
        try:
            with open(self._CLOSED_STAMP, "w") as f:
                f.write(str(time.time()))
        except Exception:
            pass
        # No fade-out: picom recomputes the background blur on every frame, which makes a
        # fade stutter on slower GPUs.
        if self.window:
            self.window.hide()
        self.quit()
        return False

    def _closed_just_now(self):
        """Clicking the panel launcher while the window is open first takes focus away
        (which closes us), then the panel starts a new instance, which would reopen the
        window. An instance started within 0.6 s of a close treats the click as "close"
        and exits without opening."""
        try:
            with open(self._CLOSED_STAMP) as f:
                closed_at = float(f.read().strip())
            # The close may be recorded slightly after the new instance started, so the
            # window works in both directions
            return abs(_STARTED - closed_at) < 0.6
        except Exception:
            return False

    def _we_are_still_active(self):
        """Is this window still the active window according to the window manager?
        A keyboard shortcut makes XFCE grab the keyboard for a moment; GTK reports that as a
        focus loss, but the active window does not change. A real switch to another window
        does change it."""
        try:
            gi.require_version("GdkX11", "3.0")
            from gi.repository import GdkX11  # noqa: F401
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)   # no GTK3 replacement on X11
                active = Gdk.Screen.get_default().get_active_window()
            ours = self.window.get_window() if self.window else None
            return bool(active and ours and active.get_xid() == ours.get_xid())
        except Exception:
            return False

    def _on_focus_out(self, widget, event):
        """Keyboard switches to another window (Alt+Tab etc.). Mouse clicks outside are
        handled by _click_watch_tick. After focus leaves, the window manager is polled and
        the window closes as soon as another window is active. For momentary grabs such as
        keyboard shortcuts, focus comes back (focus-in) and nothing happens."""
        if getattr(self, "_suppress_focus_exit", False) or getattr(self, "_focus_poll", None):
            return False
        def poll():
            if getattr(self, "_suppress_focus_exit", False) or not self.window \
                    or self.window.is_active():
                self._focus_poll = None      # focus came back, or the media view holds it
                return False
            if not self._we_are_still_active():
                self._focus_poll = None
                self.close_cc()
                return False
            return True                      # Alt+Tab switcher still open: keep waiting
        self._focus_poll = GLib.timeout_add(30, poll)
        return False

    def _on_focus_in(self, widget, event):
        if getattr(self, "_focus_poll", None):
            GLib.source_remove(self._focus_poll)
            self._focus_poll = None
        return False

    # ── Detecting clicks outside the window ─────────────────
    # The pointer is NOT grabbed, so hover effects, dock zoom etc. keep working in other
    # programs. While the window is open, the pointer position and button state are read
    # every 30 ms (one cheap X request). When a new click starts outside the window we
    # close, and the click still reaches whatever was clicked.
    _MOUSE_BUTTONS = (Gdk.ModifierType.BUTTON1_MASK | Gdk.ModifierType.BUTTON2_MASK
                      | Gdk.ModifierType.BUTTON3_MASK)

    def _start_click_watch(self):
        if getattr(self, "_click_watch", None):
            return
        self._was_pressed = True   # ignore the click that opened the window if still held
        self._click_watch = GLib.timeout_add(30, self._click_watch_tick)

    def _click_watch_tick(self):
        win = self.window
        if not win or not win.get_visible() or getattr(self, "_closing", False):
            self._click_watch = None
            return False
        gdkwin = win.get_window()
        try:
            pointer = Gdk.Display.get_default().get_default_seat().get_pointer()
            _, x, y, mask = Gdk.get_default_root_window().get_device_position(pointer)
        except Exception:
            return True
        pressed = bool(mask & self._MOUSE_BUTTONS)
        if pressed and not self._was_pressed and gdkwin is not None:
            # A new click started: is it outside the window?
            _, ox, oy = gdkwin.get_origin()
            w, h = gdkwin.get_width(), gdkwin.get_height()
            if not (ox <= x < ox + w and oy <= y < oy + h):
                self._click_watch = None
                self.close_cc()
                return False
        # Only the "released -> pressed" transition counts, so drags that start inside and
        # leave the window (e.g. a slider) do not close it.
        self._was_pressed = pressed
        return True

    def _place_window(self):
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        area = monitor.get_workarea()
        self.window.move(area.x + area.width - WINDOW_RIGHT_OFFSET, area.y + WINDOW_TOP_GAP)

    def _on_map(self, widget, event):
        self._start_click_watch()
        return False

    # ── Scrolling on sliders ────────────────────────────────
    # Two-finger scroll up/right raises the value, down/left lowers it. Touchpads follow the
    # finger smoothly; a mouse wheel moves 5% per notch. Set SCROLL_INVERT = True to flip.
    SCROLL_STEP = 5        # one mouse-wheel notch (%)
    SCROLL_TOUCH = 4       # touchpad sensitivity (higher = faster)
    SCROLL_INVERT = False
    SCROLL_FPS_MS = 33     # apply at most ~30 times per second so pactl/brightnessctl keep up

    def _enable_slider_scroll(self, root):
        stack = [root]
        while stack:
            w = stack.pop()
            if isinstance(w, Gtk.Scale) and not getattr(w, "_scroll_hooked", False):
                w._scroll_hooked = True
                w._scroll_tick = None
                w.add_events(Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK)
                w.connect("scroll-event", self._on_slider_scroll)
            if isinstance(w, Gtk.Container):
                stack.extend(w.get_children())

    def _on_slider_scroll(self, scale, event):
        ok, dx, dy = event.get_scroll_deltas()
        if ok:   # touchpad: proportional to the movement
            delta = (-dy if abs(dy) >= abs(dx) else dx) * self.SCROLL_TOUCH
        else:    # mouse wheel: fixed steps
            d = event.direction
            if d in (Gdk.ScrollDirection.UP, Gdk.ScrollDirection.RIGHT):
                delta = self.SCROLL_STEP
            elif d in (Gdk.ScrollDirection.DOWN, Gdk.ScrollDirection.LEFT):
                delta = -self.SCROLL_STEP
            else:
                return True
        if self.SCROLL_INVERT:
            delta = -delta
        adj = scale.get_adjustment()
        base = scale._scroll_target if scale._scroll_tick else adj.get_value()
        scale._scroll_target = max(adj.get_lower(), min(adj.get_upper(), base + delta))
        if not scale._scroll_tick:
            # Deltas accumulate and are applied once per SCROLL_FPS_MS
            scale._scroll_tick = GLib.timeout_add(self.SCROLL_FPS_MS, self._apply_slider_scroll, scale)
        return True   # block GTK's own slider scrolling (slow, and in the other direction)

    def _apply_slider_scroll(self, scale):
        scale._scroll_tick = None
        scale.set_value(scale._scroll_target)
        return False

    def _on_key_press(self, widget, event):
        if event.keyval == Gdk.KEY_Escape:
            if self.view_stack.get_visible_child_name() != "main_view":
                self.view_stack.set_visible_child_name("main_view")
            else:
                self.close_cc()
            return True
        return False

    def _on_window_hidden(self, widget):
        # Some rows only hide the window when they open a settings app. A hidden instance
        # would keep scanning in the background, so quit if it stays hidden.
        def quit_if_still_hidden():
            if getattr(self, "_suppress_focus_exit", False):
                return False   # media cover click: media_box handles closing itself
            if self.window and not self.window.get_visible() and not getattr(self, "_closing", False):
                self.close_cc()
            return False
        GLib.timeout_add(1500, quit_if_still_hidden)

    ANIM_DURATION = 0.26  # resize animation (s); slightly longer than the 0.2 s crossfade

    def animate_window_bounds(self, target_width, target_height):
        """Time-based ease-out resize: the first frame is immediate, then it slows to a stop."""
        if not self.window:
            return

        # Cancel a running animation; the new one continues from the current size
        if getattr(self, "_anim_id", None):
            GLib.source_remove(self._anim_id)
            self._anim_id = None

        self.window.set_resizable(True)
        start_w = self.window.get_allocated_width()
        start_h = self.window.get_allocated_height()
        t0 = time.monotonic()

        def apply(w, h):
            self.window.set_size_request(w, h)
            self.window.resize(w, h)

        def finish():
            self._anim_id = None
            from cupertinocontrolcenter.widgets.optional_bar import WINDOW_HEIGHT
            dyn_h = WINDOW_HEIGHT
            # Lock the size only on the main view; detail views must be able to resize
            # when their content changes.
            if target_height == dyn_h and self.view_stack.get_visible_child_name() == "main_view":
                self.window.set_resizable(False)

        if (start_w, start_h) == (target_width, target_height):
            apply(target_width, target_height)
            finish()
            return

        def step():
            if not self.window:
                self._anim_id = None
                return False
            p = min(1.0, (time.monotonic() - t0) / self.ANIM_DURATION)
            e = 1 - (1 - p) ** 3  # ease-out cubic
            apply(round(start_w + (target_width - start_w) * e),
                  round(start_h + (target_height - start_h) * e))
            if p >= 1.0:
                finish()
                return False
            return True

        step()  # first frame right away
        self._anim_id = GLib.timeout_add(16, step)

    EXPAND_VIEWS = ("wifi_view", "bluetooth_view", "battery_view", "airdrop_view", "sm_view",
                    "kbd_brightness_view", "display_view", "sound_view", "media_view", "music_view")

    def _target_size_for(self, name, child):
        """Window size for a detail view: as large as its content, within the limits below
        (width, height caps in px)."""
        min_req, nat_req = child.get_preferred_size()

        if name in ("display_view", "sound_view"):
            cap = 280 if name == "display_view" else 320
            w = max(276, min_req.width)
            h = min(nat_req.height, cap)
        elif name == "media_view":
            # The view's natural height, margins included
            w = 370
            h = max(120, min(nat_req.height + 4, 640))
        elif name in ("airdrop_view", "kbd_brightness_view"):
            w = max(260, nat_req.width + 16)
            h = min(nat_req.height, 230)
        else:  # wifi_view, bluetooth_view, battery_view, sm_view, music_view
            cap = 450 if name == "sm_view" else 600
            w = max(260, nat_req.width + 16)
            h = max(60, min(nat_req.height, cap))

        # Never target less than the content's minimum, or the animation stalls
        return max(w, min_req.width), max(h, min_req.height)

    def _fit_visible_view(self):
        self._fit_idle = None
        if not self.window or not hasattr(self, 'view_stack'):
            return False
        stack = self.view_stack
        name = stack.get_visible_child_name()
        child = stack.get_visible_child()
        if name not in self.EXPAND_VIEWS or child is None:
            return False
        # While the Wi-Fi/Bluetooth list is still loading, don't shrink to the empty list;
        # resize once when the results arrive.
        if getattr(self, "_await_view", None) == name:
            if self._list_is_placeholder(name):
                return False
            self._cancel_await()
        w, h = self._target_size_for(name, child)
        self.animate_window_bounds(w, h)
        return False

    def _request_fit(self):
        """Collapses several resize requests in the same main-loop iteration into one."""
        if getattr(self, "_fit_idle", None):
            return
        self._fit_idle = GLib.idle_add(self._fit_visible_view)

    def _list_is_placeholder(self, name):
        if name == "wifi_view":
            lb = getattr(getattr(self, "wifi_details_view", None), "wifi_list_box", None)
        else:
            lb = getattr(getattr(self, "bluetooth_details_view", None), "bluetooth_list_box", None)
        # Still showing the empty placeholder list, not real scan results
        return lb is None or not lb.get_children() or getattr(lb, "_prefill", False)

    def _begin_await(self, name):
        self._cancel_await()
        self._await_view = name
        # If the scan never returns, settle on whatever is there after 1.5 s
        self._await_timeout = GLib.timeout_add(1500, self._await_expired)

    def _await_expired(self):
        self._await_timeout = None
        self._await_view = None
        self._request_fit()
        return False

    def _cancel_await(self):
        if getattr(self, "_await_timeout", None):
            GLib.source_remove(self._await_timeout)
        self._await_timeout = None
        self._await_view = None

    def _force_single_resize(self):
        """Called by detail views when their content changes."""
        self._request_fit()
        return False

    def _on_stack_changed(self, stack, pspec):
        current_view = stack.get_visible_child_name()
        if not current_view:
            return

        if self.window:
            ctx = self.window.get_style_context()

            if current_view in self.EXPAND_VIEWS:
                ctx.add_class("expand-mode")
                
                if current_view == "wifi_view" and hasattr(self, 'wifi_details_view'):
                    if hasattr(self.wifi_details_view, 'wifi_list_box') and not self.wifi_details_view.wifi_list_box.get_children():
                        if hasattr(self.wifi_details_view, '_populate_wifi_networks'):
                            self.wifi_details_view._populate_wifi_networks([])
                            self.wifi_details_view.wifi_list_box._prefill = True                            
                elif current_view == "bluetooth_view" and hasattr(self, 'bluetooth_details_view'):
                    if hasattr(self.bluetooth_details_view, 'bluetooth_list_box') and not self.bluetooth_details_view.bluetooth_list_box.get_children():
                        if hasattr(self.bluetooth_details_view, '_populate_bluetooth_devices'):
                            self.bluetooth_details_view._populate_bluetooth_devices([])
                            self.bluetooth_details_view.bluetooth_list_box._prefill = True
                elif current_view == "media_view" and hasattr(self, 'media_details_view') and self.media_details_view:
                    self.media_details_view.is_active = True
                    self.media_details_view._invalidate()


                child_widget = stack.get_visible_child()
                if child_widget:
                    child_widget.show_all()
                if current_view in ("wifi_view", "bluetooth_view") and self._list_is_placeholder(current_view):
                    self._begin_await(current_view)
                else:
                    self._request_fit()
            else:
                self._cancel_await()
                if hasattr(self, 'media_details_view') and self.media_details_view:
                    self.media_details_view.is_active = False
                ctx.remove_class("expand-mode")
                # Back to the main view; its height depends on the bottom bar settings
                from cupertinocontrolcenter.widgets.optional_bar import WINDOW_HEIGHT
                dyn_h = WINDOW_HEIGHT
                self.animate_window_bounds(MAIN_WIDTH, dyn_h)

        if pspec is not None:
            if current_view == "wifi_view":
                if hasattr(self, 'wifi_details_view') and hasattr(self.wifi_details_view, 'scan_wifi_networks'):
                    threading.Thread(target=self.wifi_details_view.scan_wifi_networks, daemon=True).start()
            elif current_view == "bluetooth_view":
                if hasattr(self, 'bluetooth_details_view') and hasattr(self.bluetooth_details_view, 'scan_bluetooth_devices'):
                    threading.Thread(target=self.bluetooth_details_view.scan_bluetooth_devices, daemon=True).start()

    def _on_bluetooth_selected(self, widget, event, mac, name):
        """Forwards Bluetooth device clicks from connection_box.py to the Bluetooth view."""
        if hasattr(self, 'bluetooth_details_view') and hasattr(self.bluetooth_details_view, '_on_bluetooth_selected'):
            return self.bluetooth_details_view._on_bluetooth_selected(widget, event, mac, name)
        return False

    def do_activate(self):
        from cupertinocontrolcenter.widgets.optional_bar import WINDOW_HEIGHT
        dyn_height = WINDOW_HEIGHT

        if self.window and self.window.get_visible():
            # Launcher clicked while open (as on macOS Sonoma): go back from a detail view,
            # or close on the main view. Same as Esc.
            if self.view_stack.get_visible_child_name() != "main_view":
                self.view_stack.set_visible_child_name("main_view")
            else:
                self.close_cc()
            return

        if self.window: 
            if hasattr(self, 'view_stack'):
                self.view_stack.set_visible_child_name("main_view")
                
            from cupertinocontrolcenter.widgets.optional_bar import WINDOW_HEIGHT
            dyn_height = WINDOW_HEIGHT
                
            self.window.set_size_request(-1, -1)
            self.window.present()
            self._place_window()
            return self.window.present()

        if self._closed_just_now():
            # This launch is the tail of a "close" click on the launcher: don't open
            self.quit()
            return

        self.window = Gtk.ApplicationWindow(application=self, decorated=False)
        self.window.set_title(APP_NAME)
        self.window.set_role(APP_NAME)
        Gtk.Settings.get_default().set_property("gtk-overlay-scrolling", True)
        self.window.set_visual(self.window.get_screen().get_rgba_visual())
        self.window.get_style_context().add_class("mainwindow")
        
        self.window.set_type_hint(Gdk.WindowTypeHint.POPUP_MENU)
        # A system panel, not an app window: keep it out of the dock, taskbar, Alt+Tab and
        # window overviews.
        self.window.set_skip_taskbar_hint(True)
        self.window.set_skip_pager_hint(True)
        self.window.set_position(Gtk.WindowPosition.NONE)       
        self.window.set_resizable(True)                        

        self.setup_css()

        self.view_stack = Gtk.Stack()        
        self.view_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.view_stack.set_transition_duration(200)        
        self.view_stack.set_homogeneous(False)
        self.view_stack.set_interpolate_size(True)

        # Main view tiles
        self.connections_box = ConnectionsBox()
        disturb, kbd = DisturbBox(), KbdBrightnessBox()
        if not KBD_DEVICE:
            kbd.set_no_show_all(True)   # no keyboard backlight: hide the tile
        wifi_disp, disp, sound = ScreenMirroringBox(), DisplayLevelBox(), SoundLevelBox()
        opt_bar = OptionalBar()
        media_box = MediaControlBox() 
        self.airplane = opt_bar.airplane
        
        main_layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        main_layout.set_margin_start(10); main_layout.set_margin_end(10)
        main_layout.set_margin_top(10); main_layout.set_margin_bottom(10)

        # Grid layout: row1 = [connections | (Do Not Disturb / (Keyboard, Mirroring))]
        row1 = Gtk.Box(spacing=10)
        right_v = Gtk.Box(orientation=1, spacing=10)
        row_sub = Gtk.Box(spacing=10)
        
        row_sub.pack_start(kbd, False, False, 0); row_sub.pack_start(wifi_disp, False, False, 0)
        right_v.pack_start(disturb, True, True, 0); right_v.pack_end(row_sub, False, False, 0)
        row1.pack_start(self.connections_box, False, False, 0); row1.pack_start(right_v, False, False, 0)
        # Bottom bar columns line up with the grid: [battery + settings] under the
        # connections tile, [3 buttons] under the right column
        opt_bar.align_with(self.connections_box, right_v)

        from cupertinocontrolcenter.widgets.optional_bar import SHOW_BATTERY_CAPSULE, SHOW_4_WIDGETS
        row1_padding = 2 if (not SHOW_BATTERY_CAPSULE and not SHOW_4_WIDGETS) else 0
        disp_padding = 2 if (not SHOW_BATTERY_CAPSULE and not SHOW_4_WIDGETS) else 0
        sound_padding = 2 if (not SHOW_BATTERY_CAPSULE and not SHOW_4_WIDGETS) else 0
        media_padding = 2 if (not SHOW_BATTERY_CAPSULE and not SHOW_4_WIDGETS) else 0

        main_layout.pack_start(row1, False, False, row1_padding)
        main_layout.pack_start(disp, False, False, disp_padding)
        main_layout.pack_start(sound, False, False, sound_padding)
        main_layout.pack_start(media_box, False, False, media_padding)
        main_layout.pack_start(opt_bar, False, False, 0)

        # Detail views
        self.wifi_details_view = NetworkManager(app_instance=self)
        self.bluetooth_details_view = BluetoothManager(app_instance=self)        
        self.battery_view = opt_bar.battery_inner_box
        self.kbd_brightness_view = KeyboardBrightnessView(app_instance=self)
        self.display_details_view = DisplayView()
        self.sound_details_view = SoundView() 
        self.media_details_view = MediaView()

        # Views are switched by name through the stack
        self.view_stack.add_named(main_layout, "main_view")
        self.view_stack.add_named(self.wifi_details_view, "wifi_view")
        self.view_stack.add_named(self.bluetooth_details_view, "bluetooth_view")
        self.view_stack.add_named(self.battery_view, "battery_view")
        self.view_stack.add_named(opt_bar.music_view, "music_view")   # Music Recognition history
        self.view_stack.add_named(self.kbd_brightness_view, "kbd_brightness_view")
        self.view_stack.add_named(self.display_details_view, "display_view")
        self.view_stack.add_named(self.sound_details_view, "sound_view")
        self.view_stack.add_named(self.media_details_view, "media_view")

        self.screen_mirroring_page = ScreenMirroring()
        self.view_stack.add_named(self.screen_mirroring_page, "sm_view") 
        
        self.airdrop_page = AirDrop(main_connection_box=self.connections_box)
        self.view_stack.add_named(self.airdrop_page, "airdrop_view")

        # Other modules reach these through Gtk.Application.get_default()
        app = Gtk.Application.get_default()
        app.airdrop_page = self.airdrop_page
        app.kbd_brightness_view = self.kbd_brightness_view

        self.view_stack.set_visible_child_name("main_view")
        self.window.add(self.view_stack)
        self.view_stack.connect("notify::visible-child-name", self._on_stack_changed)

        for b in [main_layout, row1, right_v, row_sub, self.view_stack]: b.show()
        opt_bar.show_all()
        media_box.show_all() 
        
        for w in [self.connections_box, disturb, kbd, wifi_disp, disp, sound]:
            w.get_style_context().add_class("styledwidgetbox")
            w.show_all()

        self._theme_widgets = [kbd, wifi_disp]
        self.apply_theme()
        
        if hasattr(self, 'view_stack'):
            self.view_stack.set_visible_child_name("main_view")
        
        # Main view spacing: tighter when the bottom bar is hidden
        from cupertinocontrolcenter.widgets.optional_bar import SHOW_BATTERY_CAPSULE, SHOW_4_WIDGETS
        
        if not SHOW_BATTERY_CAPSULE and not SHOW_4_WIDGETS:
            main_layout.set_spacing(6)            
            main_layout.set_margin_top(8)
            main_layout.set_margin_bottom(8)            
            dyn_height = 354
        else:
            main_layout.set_spacing(10)
            main_layout.set_margin_top(10)
            main_layout.set_margin_bottom(10)
            from cupertinocontrolcenter.widgets.optional_bar import WINDOW_HEIGHT
            dyn_height = WINDOW_HEIGHT   # see optional_bar.py
            
        self.window.set_size_request(-1, -1)
        self._enable_slider_scroll(self.window)
        self.window.show_all()
        
        self._place_window()

        # Closing: see "Closing behaviour" above
        self.window.connect("focus-out-event", self._on_focus_out)
        self.window.connect("focus-in-event", self._on_focus_in)
        self.window.connect("key-press-event", self._on_key_press)
        self.window.connect("hide", self._on_window_hidden)
        self.window.connect("map-event", self._on_map)

if __name__ == "__main__":
    import sys
    Application().run(sys.argv)