import gi
import subprocess
import os
import threading
import re

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gdk, GdkPixbuf, Pango
from cupertinocontrolcenter.functions.bluetooth_information import BluetoothInformation
from cupertinocontrolcenter.functions.network_information import NetworkInformation
from cupertinocontrolcenter.widgets.round_icon import RoundIcon, DARK_GREY
from cupertinocontrolcenter.functions.airdrop import BACKEND_COMMANDS
from cupertinocontrolcenter.paths import STATE_DIR
from cupertinocontrolcenter.i18n import _

# Small fix for the labels and icons of this tile only (application.css is loaded at
# priority 800, this at 900 on top of it)
_ROW_CSS = Gtk.CssProvider()
_ROW_CSS.load_from_data(b"label { padding-top: 2px; } image { margin-top: 2px; }")


def _local_css(widget):
    widget.get_style_context().add_provider(_ROW_CSS, 900)


class ConnectionsBox(Gtk.Box):
    ROW_SPACING = 8   # space between the Wi-Fi / Bluetooth / AirDrop rows

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.get_style_context().add_class("styledwidgetbox")

        # Rows are centered vertically, so the space above and below stays equal when the
        # right column makes the tile taller or shorter
        self.rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=self.ROW_SPACING)
        self.rows.set_valign(Gtk.Align.CENTER)
        self.pack_start(self.rows, True, True, 0)
        
        self.set_hexpand(False)
        self.set_vexpand(False)
        self.set_size_request(171, -1)   # tile width

        self.network_info = None
        self.bluetooth = None
        self.airdrop_active = False
        
        # Bluetooth devices found by the last refresh, for the Bluetooth view
        self.bluetooth_devices_list = []

        self._create_row("wifi")
        self._create_row("bluetooth")
        self._create_row("airdrop")
        self._set_initial_icons()

        threading.Thread(target=self._async_loader, daemon=True).start()
        GLib.timeout_add_seconds(5, self._trigger_async_update)

        self._bt_toggle_in_progress = False

        app = Gtk.Application.get_default()
        if app:
            app.bluetooth_page_instance = self


    def _create_row(self, type):
        row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        row_box.set_hexpand(True)
        
        eb = Gtk.EventBox()
        eb.set_margin_start(1)
        icon_container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        icon_container.set_valign(Gtk.Align.CENTER)
        
        # Circle and glyph drawn in code (round_icon.py), as in the Wi-Fi and Bluetooth lists:
        # blue when on, grey when off. Clicking the icon toggles; clicking the text opens the view.
        kind = {"wifi": "wifi", "bluetooth": "png:icons/bluetooth.png", "airdrop": "airdrop"}[type]
        icon = RoundIcon(kind, off_color=DARK_GREY)
        icon.set_margin_top(2)   # lines up with the text
        icon_container.pack_start(icon, False, False, 0)
        eb.add(icon_container)
 
        text_eb = Gtk.EventBox()
        text_eb.set_hexpand(True)
        
        text_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        text_vbox.set_hexpand(True)
        text_vbox.set_valign(Gtk.Align.CENTER)  # the two text lines centered on the icon
        
        title_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        title_hbox.set_hexpand(True)
        
        title = Gtk.Label(xalign=0)
        desc = Gtk.Label(xalign=0)
        
        title.set_hexpand(True)
        title.set_max_width_chars(14)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        
        desc.set_hexpand(True)
        desc.set_max_width_chars(14)
        desc.set_ellipsize(Pango.EllipsizeMode.END)
        
        _local_css(title)
        _local_css(desc)
        title.get_style_context().add_class("general_title")
        desc.get_style_context().add_class("general_desc")
        desc.get_style_context().add_class("conn-desc")  # sits close to the title (negative margin in CSS)
        
        # "›" shown on hover
        arrow_label = Gtk.Label()
        arrow_label.set_markup("<span size='large' weight='bold'>›</span>")
        arrow_label.set_halign(Gtk.Align.END)
        arrow_label.set_valign(Gtk.Align.CENTER)
        arrow_label.set_no_show_all(True)
        arrow_label.hide()
        arrow_label.get_style_context().add_class("menu_arrow_hint")
        
        # As an overlay, so the large arrow doesn't change the row height when it appears
        title_overlay = Gtk.Overlay()
        title_overlay.add(title)
        title_overlay.add_overlay(arrow_label)
        title_overlay.set_overlay_pass_through(arrow_label, True)
        title_hbox.pack_start(title_overlay, True, True, 0)
        
        text_vbox.pack_start(title_hbox, False, False, 0)
        text_vbox.pack_start(desc, False, False, 0)
        text_eb.add(text_vbox)
        
        # Gap between icon and text (measured)
        eb.set_margin_end(5)
        row_box.pack_start(eb, False, False, 3)
        row_box.pack_start(text_eb, True, True, 0) 
        self.rows.pack_start(row_box, False, False, 0)

        text_eb.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        text_eb.connect("enter-notify-event", lambda w, e: arrow_label.show())
        text_eb.connect("leave-notify-event", lambda w, e: arrow_label.hide())

        if type == "wifi":
            self.network_icon, self.network_device, self.network_name = icon, title, desc
            self.network_device.set_text("Wi-Fi")
            eb.connect("button-press-event", self._toggle_network_power)
            text_eb.connect("button-press-event", self._expand_network_menu)
            
        elif type == "bluetooth":
            self.bluetooth_icon, self.bluetooth_device, self.bluetooth_name = icon, title, desc
            self.bluetooth_device.set_text("Bluetooth")
            eb.connect("button-press-event", self._toggle_bluetooth_power)
            text_eb.connect("button-press-event", self._expand_bluetooth_menu)

        elif type == "airdrop":
            self.airdrop_icon, self.airdrop_device, self.airdrop_name = icon, title, desc
            self.airdrop_device.set_text("AirDrop")
            eb.connect("button-press-event", self._toggle_airdrop_click)
            text_eb.connect("button-press-event", self._expand_airdrop_menu)

    def _set_initial_icons(self):
        self.network_icon.set_state(active=False)
        self.network_name.set_text(_("Loading…"))
        self.bluetooth_icon.set_state(active=False)
        self.bluetooth_name.set_text(_("Loading…"))
        self._update_airdrop_ui()

    def _async_loader(self):
        try:
            self.network_info = NetworkInformation()
            self.bluetooth = BluetoothInformation()
            self._background_refresh_worker()
        except: pass

    def _trigger_async_update(self):
        threading.Thread(target=self._background_refresh_worker, daemon=True).start()
        return True

    def _background_refresh_worker(self):
        """Refreshes Wi-Fi, Bluetooth and AirDrop. AirDrop follows the running processes, so
        starting or quitting Valent/LocalSend elsewhere is picked up too."""
        import subprocess, re, os

        # Is a sharing app running?
        is_valent_alive = subprocess.run(["pgrep", "-f", "ca.andyholmes.Valent"], capture_output=True).returncode == 0 or \
                           subprocess.run(["pgrep", "-f", "valent"], capture_output=True).returncode == 0
        
        is_localsend_alive = subprocess.run(["pgrep", "-f", "localsend_app"], capture_output=True).returncode == 0 or \
                             subprocess.run(["pgrep", "-f", "localsend"], capture_output=True).returncode == 0
        
        actual_process_state = (is_valent_alive or is_localsend_alive)

        states_dir = self._get_states_dir()
        toggle_file = os.path.join(states_dir, "airdrop_toggle_state")
        selection_file = os.path.join(states_dir, "airdrop_selection")

        if actual_process_state:
            self.airdrop_active = True
            
            current_detected_app = "valent" if is_valent_alive else "localsend"
            
            try:
                with open(toggle_file, "w") as f: f.write("true")
                with open(selection_file, "w") as f: f.write(current_detected_app)
            except: pass
        else:
            self.airdrop_active = False
            try:
                with open(toggle_file, "w") as f: f.write("false")
            except: pass

        if not self.network_info: 
            GLib.idle_add(self._update_airdrop_ui)
            return
            
        try:
            self.network_info.refresh()
            wifi_ssid = self.network_info.get_name()
            # Internet reachable? (NetworkManager: full / limited / portal / none)
            try:
                conn = subprocess.run(["nmcli", "networking", "connectivity"], capture_output=True,
                                      text=True, timeout=3).stdout.strip()
            except Exception:
                conn = ""
            self._no_internet = conn in ("none", "limited", "portal")
            
            GLib.idle_add(self._update_airdrop_ui)
            
            bt_pwr = False
            if hasattr(self, 'bluetooth') and self.bluetooth:
                try:
                    self.bluetooth.refresh()
                    bt_pwr = self.bluetooth.get_power()
                except: pass
            else:
                pwr_check = subprocess.run(["bluetoothctl", "show"], capture_output=True, text=True)
                bt_pwr = "Powered: yes" in pwr_check.stdout

            label_status_text = _("Off")
            scanned_devices = []

            if bt_pwr:
                label_status_text = _("On")
                
                try:
                    devices_out = subprocess.check_output(["bluetoothctl", "devices"], text=True, stderr=subprocess.DEVNULL)
                    lines = devices_out.strip().split('\n')
                except:
                    lines = []

                connected_macs = set()
                connected_names = []
                
                for line in lines:
                    if not line.startswith("Device "): 
                        continue
                    parts = line.split(" ", 2)
                    if len(parts) >= 3:
                        mac_addr = parts[1].strip()
                        dev_name = parts[2].strip()
                        
                        if not dev_name or dev_name.replace(":", "-").lower() == mac_addr.replace(":", "-").lower():
                            continue
                            
                        is_connected = False
                        try:
                            info_out = subprocess.check_output(["bluetoothctl", "info", mac_addr], text=True, stderr=subprocess.DEVNULL)
                            if "Connected: yes" in info_out:
                                is_connected = True
                                connected_names.append(dev_name)
                        except: pass
                            
                        scanned_devices.append((dev_name, mac_addr, is_connected))

                if connected_names:
                    label_status_text = ", ".join(connected_names)
                
                self.bluetooth_devices_list = scanned_devices
            else:
                self.bluetooth_devices_list = []

            # Send the list to the Bluetooth view even when it's empty, so the view doesn't
            # wait for it before resizing
            if True:
                try:
                    app = Gtk.Application.get_default()
                    if app and hasattr(app, 'bluetooth_details_view') and app.bluetooth_details_view:
                        if hasattr(app.bluetooth_details_view, '_populate_bluetooth_devices'):
                            GLib.idle_add(lambda: app.bluetooth_details_view._populate_bluetooth_devices(scanned_devices) or False)
                except Exception as route_err:
                    print(f"Could not update the Bluetooth view: {route_err}")

            GLib.idle_add(self._apply_ui, wifi_ssid, bt_pwr, label_status_text)
        except Exception as e:
            GLib.idle_add(self._update_airdrop_ui)

    def _apply_ui(self, wifi_ssid, bt_pwr, label_status_text):
        is_wifi_disabled = not self.network_info.is_wifi_enabled()
        self.network_icon.set_state(active=not is_wifi_disabled,
                                    no_internet=(not is_wifi_disabled and getattr(self, "_no_internet", False)))
        self.network_name.set_text(str(wifi_ssid))
        self.network_name.set_tooltip_text(str(wifi_ssid))
        
        self.bluetooth_icon.set_state(active=bool(bt_pwr))
        self.bluetooth_name.set_text(str(label_status_text))
        self.bluetooth_name.set_tooltip_text(str(label_status_text))
        
        self._update_airdrop_ui()
        return False

    def _update_airdrop_ui(self):
        """Updates the AirDrop row from the saved state."""
        import os
        states_dir = self._get_states_dir()
        toggle_file = os.path.join(states_dir, "airdrop_toggle_state")
        
        if os.path.exists(toggle_file):
            try:
                with open(toggle_file, "r") as f:
                    self.airdrop_active = f.read().strip() == "true"
            except: pass

        self.airdrop_icon.set_state(active=self.airdrop_active)

        if self.airdrop_active:
            current_choice = self._get_last_airdrop_selection()
            display_name = "Valent" if current_choice == "valent" else "LocalSend"
            self.airdrop_name.set_text(display_name)
        else:
            self.airdrop_name.set_text(_("Off"))

    def _get_states_dir(self):
        import os
        if not os.path.exists(STATE_DIR):
            os.makedirs(STATE_DIR, exist_ok=True)
        return STATE_DIR

    def _get_last_airdrop_selection(self):
        """The app chosen in the AirDrop view ("valent" or "localsend")."""
        import os
        states_dir = self._get_states_dir()
        selection_file = os.path.join(states_dir, "airdrop_selection")
        if os.path.exists(selection_file):
            try:
                with open(selection_file, "r") as f: 
                    return f.read().strip()
            except: pass
        return "valent"

    def _write_airdrop_toggle_state(self, is_active):
        import os
        states_dir = self._get_states_dir()
        toggle_file = os.path.join(states_dir, "airdrop_toggle_state")
        try:
            with open(toggle_file, "w") as f:
                f.write("true" if is_active else "false")
        except: pass

    def _toggle_airdrop_click(self, w, event):
        if event.button == 1:
            self.airdrop_active = not self.airdrop_active
            
            self._write_airdrop_toggle_state(self.airdrop_active)
            self._update_airdrop_ui()
            
            if self.airdrop_active:
                last_app = self._get_last_airdrop_selection()
                self._launch_backend_async(BACKEND_COMMANDS.get(last_app, BACKEND_COMMANDS["localsend"]))
            else:
                self._kill_all_airdrop_backends()
                
            return True
        return False

    def _launch_backend_async(self, app_name):
        try:
            GLib.spawn_command_line_async(app_name)
        except Exception as e:
            print(f"Could not start {app_name}: {e}")

    def _kill_all_airdrop_backends(self):
        def killer_worker():
            try:
                subprocess.run(["pkill", "-9", "-f", "Valent"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run(["pkill", "-9", "-f", "valent"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run(["pkill", "-9", "-f", "localsend_app"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run(["pkill", "-9", "-f", "localsend"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:
                print(f"Could not stop the AirDrop apps: {e}")
                
        threading.Thread(target=killer_worker, daemon=True).start()

    def _toggle_network_power(self, w, event):
        if event.button == 1 and self.network_info:
            wifi_active = self.network_info.is_wifi_enabled()
            self.network_icon.set_state(active=not wifi_active, no_internet=False)
            self.network_name.set_text(_("Switching…"))
            def worker():
                self.network_info.toggle_wifi(not wifi_active)
                GLib.timeout_add(1000, lambda: threading.Thread(target=self._background_refresh_worker, daemon=True).start() or False)
            threading.Thread(target=worker, daemon=True).start()
            return True
        return False

    def _expand_network_menu(self, w, event):
        if event.button == 1:
            app = Gtk.Application.get_default()
            if hasattr(app, 'view_stack') and app.view_stack: 
                app.view_stack.set_visible_child_name("wifi_view")
                # main.py animates the window to the view's size
            return True
        return False

    def _toggle_bluetooth_power(self, w, event):
        if event.button == 1 and self.bluetooth:
            if getattr(self, '_bt_toggle_in_progress', False):
                return True
                
            current_status = self.bluetooth_name.get_text()
            target_state = current_status in (_("Off"), _("Switching…"))
            
            self._bt_toggle_in_progress = True
            self.bluetooth_icon.set_state(active=target_state)
            self.bluetooth_name.set_text(_("Switching…"))
            
            def worker():
                import subprocess, threading
                try:
                    subprocess.run(["bluetoothctl", "scan", "off"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    
                    self.bluetooth.toggle(target_state)
                except Exception as e:
                    print(f"Could not toggle Bluetooth: {e}")
                finally:
                    self._bt_toggle_in_progress = False
                    
                    if target_state:
                        app = Gtk.Application.get_default()
                        if app and hasattr(app, '_scan_bluetooth_devices'):
                            GLib.idle_add(app._scan_bluetooth_devices)
                        elif app and hasattr(app, 'window') and hasattr(app.window, '_scan_bluetooth_devices'):
                            GLib.idle_add(app.window._scan_bluetooth_devices)
                    
                    GLib.timeout_add(200, lambda: threading.Thread(target=self._background_refresh_worker, daemon=True).start() or False)
                    
            threading.Thread(target=worker, daemon=True).start()
            return True
        return False

    def _expand_bluetooth_menu(self, w, event):
        if event.button == 1:
            app = Gtk.Application.get_default()
            if hasattr(app, 'view_stack') and app.view_stack:
                app.view_stack.set_visible_child_name("bluetooth_view")
                
                # Fill the Bluetooth view with the devices found by the last refresh
                bt_view_widget = app.view_stack.get_child_by_name("bluetooth_view")
                if bt_view_widget:
                    self.bluetooth_popover = bt_view_widget
                    
                    if not hasattr(self.bluetooth_popover, '_populate_bluetooth_devices') and hasattr(bt_view_widget, 'page'):
                        self.bluetooth_popover = bt_view_widget.page
                    elif not hasattr(self.bluetooth_popover, '_populate_bluetooth_devices') and hasattr(bt_view_widget, 'main_box'):
                        self.bluetooth_popover = bt_view_widget.main_box

                if hasattr(self, 'bluetooth_popover') and hasattr(self.bluetooth_popover, '_populate_bluetooth_devices'):
                    if hasattr(self, 'bluetooth_devices_list') and self.bluetooth_devices_list:
                        self.bluetooth_popover._populate_bluetooth_devices(self.bluetooth_devices_list)
                else:
                    import threading
                    threading.Thread(target=self._background_refresh_worker, daemon=True).start()

                # main.py animates the window to the view's size
            return True
        return False

    def _expand_airdrop_menu(self, w, event):
        if event.button == 1:
            app = Gtk.Application.get_default()
            if hasattr(app, 'view_stack') and app.view_stack: 
                if hasattr(app, 'airdrop_page'): 
                    app.airdrop_page.refresh_selection_ui()
                app.view_stack.set_visible_child_name("airdrop_view")
                
                if app.view_stack.get_visible_child():
                    app.view_stack.get_visible_child().show_all()
            return True
        return False

    @staticmethod
    def _static_bluetooth_click(instance, widget, event, mac, name):
        if event.button != 1: 
            return False
        if not mac or mac == "None" or ":" not in mac:
            return False
            

        def worker():
            import subprocess, time
            try:
                info_out = subprocess.check_output(["bluetoothctl", "info", mac], text=True, stderr=subprocess.DEVNULL)
                
                if "Connected: yes" in info_out:
                    subprocess.run(["bluetoothctl", "disconnect", mac], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    if "Paired: yes" not in info_out:
                        # Not paired yet: pair and trust first
                        pair_proc = subprocess.run(["bluetoothctl", "pair", mac], capture_output=True, text=True, timeout=15)
                        time.sleep(0.3)
                        subprocess.run(["bluetoothctl", "trust", mac], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        time.sleep(0.2)
                    
                    subprocess.run(["bluetoothctl", "connect", mac], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                # Refresh the tile afterwards
                import threading
                from gi.repository import GLib
                GLib.timeout_add(500, lambda: threading.Thread(
                    target=instance._background_refresh_worker, 
                    daemon=True
                ).start() or False)
                    
            except Exception as e:
                print(f"Could not connect/disconnect {name}: {e}")
                
        import threading
        threading.Thread(target=worker, daemon=True).start()
        return True
