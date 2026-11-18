import gi
import subprocess
import threading
import time
import os

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gdk, Pango
from cupertinocontrolcenter.widgets.round_icon import RoundIcon
from cupertinocontrolcenter.paths import STATE_DIR
from cupertinocontrolcenter.i18n import _
from cupertinocontrolcenter.spawn import spawn

# "Devices": paired devices (a fixed list, as in the macOS Control Center).
# "Nearby Devices": new, unpaired devices. Expands and collapses like "Other Networks" in
# Wi-Fi; it only scans while expanded, and new finds are added below without reordering.
# Clicking one pairs and connects it, no Bluetooth manager needed.
NEARBY_STATE = os.path.join(STATE_DIR, "bt_nearby_expanded")


class BluetoothManager(Gtk.Box):
    _icon_cache = {}   # mac -> icon type reported by BlueZ ("audio-headset", "input-mouse"...)

    @property
    def _bt_icons(self):
        return BluetoothManager._icon_cache

    def __init__(self, app_instance):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        
        self.app = app_instance
        
        self.set_margin_start(13); self.set_margin_end(13)   # content and separators 15 px from the window edge
        self.set_margin_top(10); self.set_margin_bottom(6)
        self.get_style_context().remove_class("styledwidgetbox")

        # --- Header: title (click to go back) and switch ---
        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        
        bt_title_eb = Gtk.EventBox()
        bt_title_eb.set_visible_window(False)
        bt_title_eb.set_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        
        bt_title = Gtk.Label()
        bt_title.set_markup("<span weight='bold' size='medium'>Bluetooth</span>")
        bt_title.set_halign(Gtk.Align.START)
        bt_title.set_margin_start(2)        
        bt_title_eb.add(bt_title)
        
        bt_title_eb.connect("button-press-event", lambda w, e: [
            self.app.view_stack.set_visible_child_name("main_view") if (self.app and hasattr(self.app, 'view_stack')) else None
        ] if e.button == 1 else False)

        self.bluetooth_switch = Gtk.Switch()
        self.bluetooth_switch.set_halign(Gtk.Align.END)
        self.bluetooth_switch.set_margin_end(1)
        
        try:
            if self.app and hasattr(self.app, 'connections_box') and self.app.connections_box.bluetooth:
                self.bluetooth_switch.set_active(self.app.connections_box.bluetooth.get_power())
            else:
                self.bluetooth_switch.set_active(True)
        except:
            self.bluetooth_switch.set_active(True)
        
        self.bluetooth_switch.connect("notify::active", self._on_bt_switch_changed)

        top_row.pack_start(bt_title_eb, True, True, 0)
        top_row.pack_end(self.bluetooth_switch, False, False, 1)
        self.pack_start(top_row, False, False, 4)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.set_margin_top(0); sep.set_margin_bottom(2)
        sep.set_margin_start(2); sep.set_margin_end(2)   # 16 px from the window edge, same as in the list
        self.pack_start(sep, False, False, 0)

        # --- Device list ---
        scroll_window = Gtk.ScrolledWindow()
        scroll_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll_window.set_shadow_type(Gtk.ShadowType.NONE)
        scroll_window.set_size_request(260, -1) 
        scroll_window.set_propagate_natural_height(True)
        scroll_window.set_max_content_height(380)

        self.bluetooth_list_box = Gtk.ListBox()
        self.bluetooth_list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.bluetooth_list_box.get_style_context().add_class("transparent-listbox")
        
        scroll_window.add(self.bluetooth_list_box)
        self.pack_start(scroll_window, True, True, 0)

        self._last_devices_key = None   # last drawn list (not rebuilt if unchanged)
        self._sync_block_until = 0      # after a switch click, don't sync the switch with the system
        self._anim_until = 0            # don't touch the list while the switch animates

        # Is the view on screen? Scanning only runs while it is; kept as a flag so threads
        # don't have to ask GTK
        self._nearby = []               # [(name, mac, False)] unpaired devices seen nearby
        self._nearby_order = {}         # mac -> order first seen (keeps the list stable)
        self._discovering = False
        self._last_paired_list = []
        try:
            with open(NEARBY_STATE) as f:
                self._nearby_expanded = f.read().strip() == "true"
        except Exception:
            self._nearby_expanded = False

        self._view_visible = False
        self.connect("map", lambda *_: setattr(self, "_view_visible", True))
        self.connect("unmap", lambda *_: setattr(self, "_view_visible", False))

    def _on_bt_switch_changed(self, switch, pspec):
        """Turns Bluetooth on or off from the switch."""
        if getattr(switch, '_updating_from_system', False):
            return
        # Switching Bluetooth takes 1-2 s. A refresh in between would see the old state and
        # flip the switch back, so don't sync the switch with the system for 3 s.
        now = time.monotonic()
        self._sync_block_until = now + 3.0
        # Rebuilding the list (and resizing the window) during the switch animation makes it
        # stutter, so hold the list for that moment.
        self._anim_until = now + 0.3
        active = switch.get_active()
        if self.app and hasattr(self.app, 'connections_box') and self.app.connections_box.bluetooth:
            def worker():
                try:
                    self.app.connections_box.bluetooth.toggle(active)
                    if active:
                        self.scan_bluetooth_devices()
                    GLib.timeout_add(1000, lambda: threading.Thread(
                        target=self.app.connections_box._background_refresh_worker, 
                        daemon=True
                    ).start() or False)
                except: 
                    pass
            threading.Thread(target=worker, daemon=True).start()

    def scan_bluetooth_devices(self):
        """Starts the scan/refresh loop while the view is open. Opening the view never turns
        Bluetooth on; only the switch does (as on macOS)."""
        if getattr(self, "_discovery_running", False):
            return   # already running
        # Called when the view opens (or by the switch); "map" may arrive a moment later, so
        # count as visible now; "unmap" clears it.
        self._view_visible = True
        try:
            if self.app and hasattr(self.app, 'connections_box') and hasattr(self.app.connections_box, '_background_refresh_worker'):
                threading.Thread(target=self.app.connections_box._background_refresh_worker, daemon=True).start()

            def hardware_trigger_worker():
                """Scan + list: one D-Bus call to BlueZ returns all devices (name, connected),
                and the list is updated directly."""
                self._discovery_running = True
                adapter = None
                try:
                    from pydbus import SystemBus
                    bus = SystemBus()
                    adapter = bus.get("org.bluez", "/org/bluez/hci0")
                    manager = bus.get("org.bluez", "/")
                    # Know the paired devices right away, so unpaired ones in the list from the
                    # Connections tile are filtered out from the start
                    self._paired = self._paired_macs(manager)

                    # Just turned on by the switch: the adapter needs 1-2 s to power up
                    for _ in range(25):
                        if bool(adapter.Powered):
                            break
                        time.sleep(0.2)
                    else:
                        return   # Bluetooth off: no scan


                    last = None
                    while self._view_visible:
                        try:
                            if not bool(adapter.Powered):
                                break
                        except Exception:
                            break
                        # Scan only while "Nearby Devices" is expanded
                        if self._nearby_expanded and not self._discovering:
                            try:
                                adapter.StartDiscovery()
                            except Exception as dbus_err:
                                if "InProgress" not in str(dbus_err):
                                    print(f"Could not start Bluetooth discovery: {dbus_err}")
                            self._discovering = True
                        elif not self._nearby_expanded and self._discovering:
                            try:
                                adapter.StopDiscovery()
                            except Exception:
                                pass
                            self._discovering = False

                        self._paired = self._paired_macs(manager)
                        devices = self._read_bluez_devices(manager)
                        nearby = self._read_nearby(manager) if self._nearby_expanded else []
                        if (devices, nearby) != last:
                            last = (devices, nearby)
                            def apply(d=devices, n=nearby):
                                self._nearby = n
                                self._populate_bluetooth_devices(d)
                                return False
                            GLib.idle_add(apply)
                        time.sleep(1.5)

                except Exception as ex:
                    print(f"Bluetooth scan error: {ex}")
                finally:
                    # Stop scanning when the view closes (scanning drains the battery; macOS also
                    # scans only while the menu is open)
                    try:
                        if adapter is not None and self._discovering:
                            adapter.StopDiscovery()
                        self._discovering = False
                    except Exception:
                        pass
                    self._discovery_running = False

            threading.Thread(target=hardware_trigger_worker, daemon=True).start()
            
        except Exception as e:
            print(f"Bluetooth view error: {e}")

    @classmethod
    def _paired_macs(cls, manager):
        return {str(i["org.bluez.Device1"].get("Address", "")).upper()
                for i in manager.GetManagedObjects().values()
                if "org.bluez.Device1" in i and (i["org.bluez.Device1"].get("Paired")
                                                 or i["org.bluez.Device1"].get("Connected"))}

    @staticmethod
    def _read_bluez_devices(manager):
        """[(name, mac, connected), ...], connected first, then by name."""
        out = []
        for path, ifaces in manager.GetManagedObjects().items():
            dev = ifaces.get("org.bluez.Device1")
            if not dev:
                continue
            if not (dev.get("Paired") or dev.get("Connected")):
                continue
            mac = str(dev.get("Address", ""))
            name = str(dev.get("Alias") or dev.get("Name") or "").strip()
            # Devices without a name show their address (AA-BB-CC...): skip them
            if not name or name.replace("-", ":").upper() == mac.upper():
                continue
            out.append((name, mac, bool(dev.get("Connected", False))))
            if dev.get("Icon"):
                BluetoothManager._icon_cache[mac.upper()] = str(dev.get("Icon"))
        return sorted(out, key=lambda d: (not d[2], d[0].lower()))

    def _read_nearby(self, manager):
        """Unpaired devices with a name, seen in the current scan (they have an RSSI), in the
        order first seen: new ones go to the bottom, nothing moves."""
        out = []
        for path, ifaces in manager.GetManagedObjects().items():
            dev = ifaces.get("org.bluez.Device1")
            if not dev or dev.get("Paired") or dev.get("Connected") or "RSSI" not in dev:
                continue
            mac = str(dev.get("Address", ""))
            name = str(dev.get("Alias") or dev.get("Name") or "").strip()
            if not name or name.replace("-", ":").upper() == mac.upper():
                continue
            if mac not in self._nearby_order:
                self._nearby_order[mac] = len(self._nearby_order)
            if dev.get("Icon"):
                BluetoothManager._icon_cache[mac.upper()] = str(dev.get("Icon"))
            out.append((name, mac, False))
        return sorted(out, key=lambda d: self._nearby_order[d[1]])

    def _on_nearby_clicked(self, widget, event):
        if event.type != Gdk.EventType.BUTTON_PRESS or event.button != 1:
            return event.button == 1
        self._nearby_expanded = not self._nearby_expanded
        try:
            os.makedirs(os.path.dirname(NEARBY_STATE), exist_ok=True)
            with open(NEARBY_STATE, "w") as f:
                f.write("true" if self._nearby_expanded else "false")
        except Exception:
            pass
        if not self._nearby_expanded:
            self._nearby = []
        self._last_devices_key = None   # force a rebuild
        self._populate_bluetooth_devices(self._last_paired_list)
        return True

    def _populate_bluetooth_devices(self, devices):
        """Rebuilds the list: Devices, Nearby Devices and "Bluetooth Settings…"."""
        now = time.monotonic()
        if now < self._anim_until:
            # The switch is still animating: draw afterwards
            GLib.timeout_add(int((self._anim_until - now) * 1000) + 20,
                             lambda d=devices: self._populate_bluetooth_devices(d) or False)
            return False

        # Sync the switch with the system, unless the user just clicked it
        if now >= self._sync_block_until and self.app and hasattr(self.app, 'connections_box') \
                and self.app.connections_box.bluetooth:
            try:
                system_bt_power = bool(self.app.connections_box.bluetooth.get_power())
                if self.bluetooth_switch.get_active() != system_bt_power:
                    self.bluetooth_switch._updating_from_system = True
                    self.bluetooth_switch.set_active(system_bt_power)
                    self.bluetooth_switch._updating_from_system = False
            except Exception:
                pass

        # Don't rebuild an unchanged list (the Connections tile sends it every 5 s; rebuilding
        # makes the hover and the window flicker). Two sources may send it in different
        # orders, so sort it first. The tile's list includes unpaired devices: drop them.
        paired = getattr(self, "_paired", None)
        if paired is not None:
            devices = [d for d in (devices or []) if isinstance(d, (list, tuple)) and len(d) >= 2
                       and str(d[1]).upper() in paired]
        try:
            devices = sorted(devices or [], key=lambda d: (not bool(d[2]), str(d[0]).lower()))
        except Exception:
            devices = list(devices or [])
        self._last_paired_list = devices
        bt_on = self.bluetooth_switch.get_active()
        key = (tuple(tuple(d) if isinstance(d, (list, tuple)) else d for d in devices),
               tuple(self._nearby), self._nearby_expanded, bt_on)
        if key == self._last_devices_key and self.bluetooth_list_box.get_children():
            self.bluetooth_list_box._prefill = False
            return False
        self._last_devices_key = key

        self.bluetooth_list_box._prefill = False  # real data arrived (main.py marks its placeholder fill)
        for child in self.bluetooth_list_box.get_children():
            self.bluetooth_list_box.remove(child)

        valid_devices = []
        if devices:
            for dev in devices:
                try:
                    if isinstance(dev, (list, tuple)):
                        if len(dev) >= 3:
                            if ":" in str(dev[0]) and len(str(dev[0])) >= 17:
                                d_mac, d_name, d_conn = str(dev[0]).strip(), str(dev[1]).strip(), bool(dev[2])
                            else:
                                d_name, d_mac, d_conn = str(dev[0]).strip(), str(dev[1]).strip(), bool(dev[2])
                        elif len(dev) == 2:
                            if ":" in str(dev[1]):
                                d_name, d_mac, d_conn = str(dev[0]).strip(), str(dev[1]).strip(), False
                            else:
                                d_name, d_mac, d_conn = str(dev[0]).strip(), str(dev[0]).strip(), bool(dev[1])
                        else:
                            d_name, d_mac, d_conn = str(dev[0]).strip(), str(dev[0]).strip(), False
                    else:
                        d_name, d_mac, d_conn = str(dev).strip(), str(dev).strip(), False
                    
                    if not d_name or "devicemenu" in d_name.lower():
                        continue
                        
                    valid_devices.append((d_name, d_mac, d_conn))
                except:
                    continue


        def add_category_header(text):
            row_shell = Gtk.ListBoxRow()
            row_shell.set_can_focus(False); row_shell.set_selectable(False); row_shell.set_sensitive(False)
            row_shell.get_style_context().add_class("mac-category-title-row")
            
            title_lbl = Gtk.Label()
            title_lbl.set_text(text)
            title_lbl.get_style_context().add_class("mac-section-title")   # font in CSS
            # Same height as the "Nearby Devices" header
            title_lbl.set_halign(Gtk.Align.START); title_lbl.set_margin_top(0); title_lbl.set_margin_bottom(4); title_lbl.set_margin_start(0)
            row_shell.add(title_lbl)
            self.bluetooth_list_box.add(row_shell)

        def add_perfect_slider_sep():
            sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            sep.set_margin_top(0); sep.set_margin_bottom(0)
            sep.set_margin_start(0); sep.set_margin_end(0)
            sep.get_style_context().add_class("tight-bottom")  # only the settings row below
            
            row_sep_shell = Gtk.ListBoxRow()
            row_sep_shell.set_can_focus(False); row_sep_shell.set_selectable(False); row_sep_shell.set_sensitive(False)
            row_sep_shell.set_margin_start(0); row_sep_shell.set_margin_end(0)
                        
            row_sep_shell.add(sep)
            self.bluetooth_list_box.add(row_sep_shell)

        def append_row_to_list(name, mac, connected, is_custom_settings=False):
            row_shell = Gtk.ListBoxRow()
            row_shell.get_style_context().add_class("mac-wifi-row")
            row_shell.set_margin_start(0); row_shell.set_margin_end(0)
            
            if is_custom_settings:
                row_shell.set_margin_top(0); row_shell.set_margin_bottom(0)
                row_shell.get_style_context().add_class("mac-bottom-settings-row")
            else:
                row_shell.set_margin_top(0); row_shell.set_margin_bottom(0)
            
            row_eb = Gtk.EventBox()
            row_eb.set_visible_window(False)
            
            row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            row_box.set_margin_top(1); row_box.set_margin_bottom(1); row_box.set_margin_start(0); row_box.set_margin_end(0)
            if is_custom_settings:
                row_box.set_margin_top(3); row_box.set_margin_bottom(3)
            
            if is_custom_settings:
                lbl = Gtk.Label()
                lbl.set_markup(GLib.markup_escape_text(name))
                lbl.set_halign(Gtk.Align.START)
                                
                row_box.pack_start(lbl, True, True, 0)
                
                def launch_manager(w, e):
                    if e.button == 1:
                        c_env = os.environ.copy()
                        if "DISPLAY" not in c_env: 
                            c_env["DISPLAY"] = ":0.0"
                        try:
                            spawn(["blueman-manager"], env=c_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            if self.app and hasattr(self.app, 'window') and self.app.window: 
                                self.app.window.hide()
                        except: 
                            pass
                    return True
                row_eb.connect("button-press-event", launch_manager)
            else:
                # Blue circle when connected, grey otherwise (no checkmark, as on macOS)
                icon = RoundIcon(self._device_kind(name, mac), active=connected)
                
                lbl = Gtk.Label()
                lbl.set_markup(GLib.markup_escape_text(name))
                lbl.set_halign(Gtk.Align.START)
                lbl.set_ellipsize(Pango.EllipsizeMode.END)
                lbl.set_max_width_chars(30)
                
                row_box.pack_start(icon, False, False, 0)
                row_box.pack_start(lbl, True, True, 0)
                
                row_eb.connect("button-press-event", lambda w, e, m=mac, n=name: self._on_bluetooth_selected(w, e, str(m), str(n)))
                
            row_eb.add(row_box)
            row_shell.add(row_eb)
            self.bluetooth_list_box.add(row_shell)

        # Build. As on macOS: no "nothing found" text; without devices (or with Bluetooth
        # off) only the settings row remains
        def add_nearby_header():
            """Like "Other Networks" in Wi-Fi: click to expand, hover effect, grey text."""
            row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
            row_box.set_margin_top(1); row_box.set_margin_bottom(1)
            lbl = Gtk.Label()
            lbl.set_text(_("Nearby Devices"))
            lbl.get_style_context().add_class("mac-section-title")
            lbl.set_halign(Gtk.Align.START); lbl.set_margin_start(0)
            row_box.pack_start(lbl, True, True, 0)
            arrow = Gtk.Label()
            arrow.set_markup(f"<span size='medium'>{'⌃' if self._nearby_expanded else '›'}</span>")
            arrow.set_margin_end(5)
            row_box.pack_end(arrow, False, False, 0)
            if self._nearby_expanded:   # scanning indicator
                sp = Gtk.Spinner(); sp.set_size_request(12, 12); sp.set_valign(Gtk.Align.CENTER)
                sp.set_margin_end(6); sp.start()
                row_box.pack_end(sp, False, False, 0)
            eb = Gtk.EventBox(); eb.set_visible_window(False)
            eb.add(row_box)
            eb.connect("button-press-event", self._on_nearby_clicked)
            row_shell = Gtk.ListBoxRow()
            row_shell.set_can_focus(False); row_shell.set_selectable(False); row_shell.set_activatable(False)
            row_shell.get_style_context().add_class("mac-wifi-row")
            row_shell.get_style_context().add_class("mac-category-toggle-row")
            row_shell.add(eb)
            self.bluetooth_list_box.add(row_shell)

        def add_plain_sep():
            sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            row = Gtk.ListBoxRow()
            row.set_can_focus(False); row.set_selectable(False); row.set_sensitive(False)
            row.get_style_context().add_class("styled-separator-row")
            row.add(sep)
            self.bluetooth_list_box.add(row)

        if valid_devices:
            add_category_header(_("Devices"))
            for name, mac, conn in valid_devices:
                append_row_to_list(name, mac, conn, is_custom_settings=False)
            if bt_on:
                add_plain_sep()

        if bt_on:
            add_nearby_header()
            if self._nearby_expanded:
                for name, mac, conn in self._nearby:
                    append_row_to_list(name, mac, conn, is_custom_settings=False)

        if valid_devices or bt_on:
            add_perfect_slider_sep()
        append_row_to_list(_("Bluetooth Settings…"), None, False, is_custom_settings=True)

        self.bluetooth_list_box.show_all()
        
        if self.app and hasattr(self.app, '_force_single_resize'):
            GLib.idle_add(lambda: self.app._force_single_resize() or False)
        return False

    # Device type reported by BlueZ -> glyph in the circle
    BLUEZ_KINDS = {
        "audio-headset": "headphones", "audio-headphones": "headphones", "audio-card": "speaker",
        "input-mouse": "mouse", "input-keyboard": "keyboard", "input-gaming": "gamepad",
        "phone": "phone", "computer": "laptop", "video-display": "tv", "input-tablet": "mouse",
    }

    def _device_kind(self, name, mac):
        """The type the device reports (headset, mouse, phone...) is more reliable than its
        name. Headsets with "buds"/"pods" in the name get the earbuds glyph. Without a type,
        guess from the name."""
        lower = str(name).lower()
        earbuds = any(x in lower for x in ("buds", "pods", "earbud"))
        kind = self.BLUEZ_KINDS.get(getattr(self, "_bt_icons", {}).get(str(mac).upper(), ""))
        if kind == "headphones" and earbuds:
            return "earbuds"
        if kind:
            return kind
        if "tv" in lower.replace("[", " ").replace("]", " ").split():
            return "tv"
        if earbuds:
            return "earbuds"
        if any(x in lower for x in ("audio", "headphone", "headset", "scale", "portal", "beoplay")):
            return "headphones"
        if any(x in lower for x in ("speaker", "soundbar", "jbl", "boom")):
            return "speaker"
        if any(x in lower for x in ("mouse", "lightspeed", "pop", "mx master")):
            return "mouse"
        if any(x in lower for x in ("keyboard", "keychron")):
            return "keyboard"
        if any(x in lower for x in ("controller", "shock", "sn30", "xbox", "gamepad")):
            return "gamepad"
        return "bluetooth"

    def _on_bluetooth_selected(self, widget, event, mac, name):
        """Connects, or disconnects, a device; pairs and trusts it first if needed."""
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
                        pair_proc = subprocess.run(["bluetoothctl", "pair", mac], capture_output=True, text=True, timeout=15)
                        time.sleep(0.3)
                        
                        subprocess.run(["bluetoothctl", "trust", mac], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        time.sleep(0.2)
                        
                        if not ("Pairing successful" in pair_proc.stdout or pair_proc.returncode == 0):
                            print(f"Pairing {name} may have failed; trying to connect anyway")

                    subprocess.run(["bluetoothctl", "connect", mac], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    
                if self.app and hasattr(self.app, 'connections_box') and hasattr(self.app.connections_box, '_background_refresh_worker'):
                    GLib.timeout_add(500, lambda: threading.Thread(
                        target=self.app.connections_box._background_refresh_worker,
                        daemon=True
                    ).start() or False)
                    
            except subprocess.TimeoutExpired:
                print(f"Pairing {mac} timed out (is the device in pairing mode?)")
            except Exception as e:
                print(f"Could not connect/disconnect {name}: {e}")
                
        threading.Thread(target=worker, daemon=True).start()
        return True
