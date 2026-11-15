import gi
import os
import glob
import time
import subprocess
import threading

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gio, Gdk
from cupertinocontrolcenter.i18n import _


def _find_kbd_device():
    for path in sorted(glob.glob("/sys/class/leds/*kbd_backlight*")):
        return os.path.basename(path)
    return None


# Keyboard backlight LED, e.g. "smc::kbd_backlight" on MacBooks. None if there is none;
# main.py then hides the tile. Set a name here to pick a different LED.
KBD_DEVICE = _find_kbd_device()


class KbdBrightnessBox(Gtk.Box):
    """Keyboard Brightness tile on the main view; opens the Keyboard Brightness view."""
    def __init__(self):
        super().__init__()
        
        self.set_hexpand(False)
        self.set_vexpand(False)
        
        main_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        
        open_button = Gtk.Button()
        open_button.set_always_show_image(True)
        open_button.connect("clicked", self._expand_brightness_page)
        open_button.get_style_context().add_class("widgetbutton")
        open_button.set_size_request(-1, -1)
        
        # Icon follows light/dark mode (apply_dynamic_icon_theme)
        self.kbdbrightness_icon = Gtk.Image()
        self.kbdbrightness_icon.set_halign(Gtk.Align.CENTER)
        self.kbdbrightness_icon.set_margin_start(6)   # measured: centers the icon in the tile

        # One label with a line break (two labels add extra spacing)
        # Translators: tile title, split over two lines with \n
        kbd_label = Gtk.Label(label=_("Keyboard\nBrightness"))
        kbd_label.get_style_context().add_class("general_widget_title")
        kbd_label.set_halign(Gtk.Align.CENTER)
        kbd_label.set_justify(Gtk.Justification.CENTER)
        
        grid = Gtk.Grid()
        grid.set_column_homogeneous(True)
        grid.set_halign(Gtk.Align.CENTER)
        grid.set_valign(Gtk.Align.CENTER)
        
        grid.set_margin_start(0)
        grid.set_margin_end(0)
        grid.set_row_spacing(0)
        # Lines up the text with the Screen Mirroring tile next to it: in a centered box,
        # 2 px at the top moves the content down by 1 px
        grid.set_margin_top(2)
        
        grid.attach(self.kbdbrightness_icon, 0, 0, 1, 1)
        grid.attach(kbd_label, 0, 1, 1, 1)
        grid.show_all()
        open_button.add(grid)
        
        main_box.pack_start(open_button, False, False, 0)
        self.pack_start(main_box, False, False, 0)
        
        self.get_style_context().add_class("styledwidgetbox")
        self.get_style_context().add_class("widgetbutton-container")
        
        self.apply_dynamic_icon_theme()  # right away, so the tile doesn't change size after opening
        
    def apply_dynamic_icon_theme(self):
        try:
            settings = Gio.Settings.new("org.gnome.desktop.interface")
            is_dark = "dark" in settings.get_string("color-scheme").lower()
            
            if is_dark:
                self.kbdbrightness_icon.set_from_file("icons/kbdbrightness-dark.png")
            else:
                self.kbdbrightness_icon.set_from_file("icons/kbdbrightness.png")
        except Exception as e:
            self.kbdbrightness_icon.set_from_file("icons/kbdbrightness.png")
            print(f"Could not pick the keyboard icon: {e}")
        return False
    
    def _expand_brightness_page(self, button):
        self.apply_dynamic_icon_theme()
        app = Gtk.Application.get_default()
        if app and hasattr(app, 'view_stack'):
            kbd_view = app.view_stack.get_child_by_name("kbd_brightness_view")
            if kbd_view and hasattr(kbd_view, 'refresh_kbd_brightness_from_system'):
                kbd_view.refresh_kbd_brightness_from_system()
            app.view_stack.set_visible_child_name("kbd_brightness_view")

class KeyboardBrightnessView(Gtk.Box):
    def __init__(self, app_instance=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        
        self.app = app_instance if app_instance else Gtk.Application.get_default()
        
        self.set_hexpand(True)
        self.set_vexpand(False)
        self.set_valign(Gtk.Align.START)
        
        self.set_margin_start(7); self.set_margin_end(7)   # content and separators 15 px from the window edge
        self.set_margin_top(10); self.set_margin_bottom(6)
        self.get_style_context().add_class("styledwidgetbox")
        self.get_style_context().add_class("kbd-view")
        
        # Level to restore when the icon is clicked again after turning the light off
        self.last_non_zero_brightness = 50

        # --- Header: clicking the title goes back ---
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        
        title_eb = Gtk.EventBox()
        title_eb.set_visible_window(False)
        title_eb.set_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        
        title_lbl = Gtk.Label()
        title_lbl.set_markup("<span weight='bold' size='medium'>%s</span>"
                             % GLib.markup_escape_text(_("Keyboard Brightness")))
        title_lbl.set_halign(Gtk.Align.START)
        title_lbl.set_margin_start(2)
        title_eb.add(title_lbl)
        
        title_eb.connect("button-press-event", lambda w, e: [
            self._go_back()
        ] if e.button == 1 else False)
        
        header_box.pack_start(title_eb, True, True, 0)
        self.pack_start(header_box, False, False, 0)

        # --- Slider with the icon drawn on top ---
        slider_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        slider_row.set_hexpand(True)
        slider_row.set_margin_top(10); slider_row.set_margin_bottom(0)

        self.kbd_slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.kbd_slider.set_draw_value(False)
        self.kbd_slider.set_hexpand(True)
        self.kbd_slider.set_size_request(-1, -1)
        self.kbd_slider.get_style_context().add_class("widgetslider")
        self.kbd_slider.connect("value-changed", self._on_kbd_slider_changed)

        slider_overlay = Gtk.Overlay()
        slider_overlay.add(self.kbd_slider)

        self.internal_icon = Gtk.Image()
        self.internal_icon.get_style_context().add_class("mac-slider-embedded-icon")
        # The icon is added to the overlay directly with pass-through (an EventBox would
        # swallow clicks). A click exactly on the icon toggles the light; see
        # _on_embedded_icon_pressed.
        self.internal_icon.set_halign(Gtk.Align.START)
        self.internal_icon.set_valign(Gtk.Align.CENTER)
        self.internal_icon.set_margin_start(6)
        self.internal_icon.set_margin_bottom(1)
        slider_overlay.add_overlay(self.internal_icon)
        slider_overlay.set_overlay_pass_through(self.internal_icon, True)
        self.kbd_slider.connect("button-press-event", self._on_embedded_icon_pressed)

        slider_row.pack_start(slider_overlay, True, True, 0)
        self.pack_start(slider_row, False, False, 0)
        
        sep1 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep1.set_margin_top(0); sep1.set_margin_bottom(0)
        sep1.set_margin_start(2); sep1.set_margin_end(2)   # 16 px from the window edge
        sep1.get_style_context().add_class("tight-bottom")
        self.pack_start(sep1, False, False, 0)
        
        # --- "Keyboard Settings…" row ---
        settings_eb = Gtk.EventBox()
        settings_eb.set_visible_window(False)
        settings_eb.set_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        
        settings_lbl = Gtk.Label()
        settings_lbl.set_markup("<span size='medium'>%s</span>"
                                % GLib.markup_escape_text(_("Keyboard Settings…")))
        settings_lbl.set_halign(Gtk.Align.START)
        settings_lbl.set_margin_start(1)
        settings_lbl.set_margin_top(3)
        settings_lbl.set_margin_bottom(3)
        settings_eb.add(settings_lbl)
        
        settings_eb.connect("button-press-event", lambda w, e: [
            GLib.spawn_command_line_async("xfce4-keyboard-settings &")
        ] if e.button == 1 else False)
        
        # In a list row, like the other views, so it gets the hover effect
        pref_list = Gtk.ListBox()
        pref_list.set_selection_mode(Gtk.SelectionMode.NONE)
        pref_list.get_style_context().add_class("transparent-listbox")
        pref_item = Gtk.ListBoxRow()
        pref_item.get_style_context().add_class("mac-wifi-row")
        pref_item.get_style_context().add_class("mac-bottom-settings-row")
        pref_item.add(settings_eb)
        pref_list.add(pref_item)
        self.pack_start(pref_list, False, False, 0)

        self._kbd_max = 255          # replaced by the real maximum on the first read
        self._kbd_settled = None     # last real (raw) level shown
        self._kbd_set_time = 0       # when the slider last set a level
        self.refresh_kbd_brightness_from_system()
        self.show_all()
        # Changes from the brightness keys or the system (e.g. ambient light): while the view
        # is open, the kernel's file is read every 0.25 s (cheap, no process started)
        GLib.timeout_add(250, self._poll_kbd_level)
        # When the power manager applies the brightness keys through UPower, UPower signals
        # the change: update right away (the polling above stays as a fallback)
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            for sig in ("BrightnessChanged", "BrightnessChangedWithSource"):
                bus.signal_subscribe("org.freedesktop.UPower", "org.freedesktop.UPower.KbdBacklight", sig,
                                     "/org/freedesktop/UPower/KbdBacklight", None,
                                     Gio.DBusSignalFlags.NONE,
                                     lambda *a: GLib.timeout_add(30, lambda: self._poll_kbd_level() and False))
        except Exception as e:
            print(f"Could not listen for keyboard backlight changes: {e}")

    def _on_embedded_icon_pressed(self, scale, event):
        """A click exactly on the icon turns the light off, or back on at the previous level.
        Anywhere else (even right next to the icon) is a normal slider click."""
        if event.button != 1:
            return False
        r = scale.translate_coordinates(self.internal_icon, int(event.x), int(event.y))
        a = self.internal_icon.get_allocation()
        # Hit area: the icon minus 1 px on the right (tune the number to taste)
        if not r or not (0 <= r[0] < a.width - 1 and 0 <= r[1] < a.height):
            return False
        curr_val = int(self.kbd_slider.get_value())
        if curr_val > 0:
            self.last_non_zero_brightness = curr_val
            self.kbd_slider.set_value(0)
        else:
            self.kbd_slider.set_value(self.last_non_zero_brightness)
        return True

    def _go_back(self):
        app = self.app if self.app else Gtk.Application.get_default()
        if app and hasattr(app, 'view_stack'):
            app.view_stack.set_visible_child_name("main_view")

    def _update_internal_icon_instantly(self, value):
        """Off / low / medium / high icon for the level."""
        if value == 0:
            icon_name = "keyboard-brightness-off-symbolic" 
        elif value <= 33:
            icon_name = "keyboard-brightness-medium-symbolic"
        elif value <= 66:
            icon_name = "keyboard-brightness-symbolic"
        else:
            icon_name = "keyboard-brightness-high-symbolic"
            
        self.internal_icon.set_from_icon_name(icon_name, Gtk.IconSize.MENU)

    def refresh_kbd_brightness_from_system(self):
        """Reads the real level at startup and whenever the view opens."""
        if not KBD_DEVICE:
            return
        def worker():
            try:
                current = int(subprocess.check_output(["brightnessctl", "--device", KBD_DEVICE, "get"], text=True).strip())
                max_val = int(subprocess.check_output(["brightnessctl", "--device", KBD_DEVICE, "max"], text=True).strip())
                self._kbd_max = max(1, max_val)
                self._kbd_settled = current
                percent = int(round(current * 100 / self._kbd_max))   # round(): truncating is off by one
                
                def update_slider():
                    self.kbd_slider.handler_block_by_func(self._on_kbd_slider_changed)
                    self.kbd_slider.set_value(percent)
                    self.kbd_slider.handler_unblock_by_func(self._on_kbd_slider_changed)
                    self._update_internal_icon_instantly(percent)
                    if percent > 0:
                        self.last_non_zero_brightness = percent
                GLib.idle_add(update_slider)
            except Exception as e:
                print(f"Could not read the keyboard backlight: {e}")
                GLib.idle_add(lambda: [self.kbd_slider.set_value(25), self._update_internal_icon_instantly(25)])
                
        threading.Thread(target=worker, daemon=True).start()

    def _on_kbd_slider_changed(self, scale):
        """Converts the percentage to the device's raw level and sets it in the background."""
        val = int(scale.get_value())
        self._update_internal_icon_instantly(val)
        self._kbd_set_time = time.monotonic()
        if val > 0:
            self.last_non_zero_brightness = val
            
        def apply():
            try:
                raw_val = int(round(val * self._kbd_max / 100))
                self._kbd_expected = raw_val   # compared with the next read
                self._kbd_after_set = True
                subprocess.run(["brightnessctl", "--device", KBD_DEVICE, "set", str(raw_val)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except: 
                pass
        threading.Thread(target=apply, daemon=True).start()
    KBD_FILE = f"/sys/class/leds/{KBD_DEVICE}/brightness" if KBD_DEVICE else None

    def _poll_kbd_level(self):
        """While the view is open, moves the slider to the real level."""
        try:
            app = self.app if self.app else Gtk.Application.get_default()
            if app.view_stack.get_visible_child_name() != "kbd_brightness_view":
                return True
        except Exception:
            return True
        if time.monotonic() - self._kbd_set_time < 0.3:
            return True              # just set by the slider; the write may not be done yet
        try:
            with open(self.KBD_FILE) as f:
                raw = int(f.read().strip())
        except Exception:
            return True
        if getattr(self, "_kbd_after_set", False):
            self._kbd_after_set = False
            if raw == getattr(self, "_kbd_expected", None):
                # Exactly what we wrote: leave the knob where the user put it
                self._kbd_settled = raw
                return True
            # Different: a brightness key was pressed in between; follow the real level
        if raw == self._kbd_settled:
            return True              # no change
        self._kbd_settled = raw
        percent = int(round(raw * 100 / max(1, self._kbd_max)))
        if int(self.kbd_slider.get_value()) != percent:
            self.kbd_slider.handler_block_by_func(self._on_kbd_slider_changed)
            self.kbd_slider.set_value(percent)
            self.kbd_slider.handler_unblock_by_func(self._on_kbd_slider_changed)
        self._update_internal_icon_instantly(percent)
        if percent > 0:
            self.last_non_zero_brightness = percent
        return True
