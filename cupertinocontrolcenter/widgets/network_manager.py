import gi
import subprocess
import os
import json
import threading

gi.require_version('Gtk', '3.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk, GLib, Gdk, Pango, Gio, PangoCairo
from cupertinocontrolcenter.widgets.round_icon import RoundIcon
from cupertinocontrolcenter.paths import STATE_DIR
from cupertinocontrolcenter.i18n import _

NM = "org.freedesktop.NetworkManager"
NM_PATH = "/org/freedesktop/NetworkManager"

# Phone status (battery, 4G/5G, signal) through Valent (KDE Connect). macOS gets this from an
# iPhone via Continuity; on Android the KDE Connect app sends the same information.
VALENT = "ca.andyholmes.Valent"
VALENT_DEVICES = "/ca/andyholmes/Valent/Device"
# Networks recognized as phone hotspots (remembered after the first connection)
HOTSPOT_STATE = os.path.join(STATE_DIR, "hotspots.json")
# KDE Connect network type names -> short names as on macOS
NET_TYPES = {"5G": "5G", "NR": "5G", "LTE": "4G", "HSPA": "3G", "UMTS": "3G", "CDMA2000": "3G",
             "EDGE": "E", "GPRS": "G", "GSM": "2G", "CDMA": "2G", "iDEN": "2G"}


def _rrect(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -1.5708, 0)
    cr.arc(x + w - r, y + h - r, r, 0, 1.5708)
    cr.arc(x + r, y + h - r, r, 1.5708, 3.1416)
    cr.arc(x + r, y + r, r, 3.1416, 4.7124)
    cr.close_path()


class PhoneStatus(Gtk.DrawingArea):
    """Right side of a Personal Hotspot row, as on macOS: signal bars, 4G/5G, battery. All
    three are drawn in one area so they line up and share a colour (the theme's text colour)."""
    BAR_W, BAR_GAP, GAP = 3.0, 1.5, 5
    BATT_W = 23

    def __init__(self, bars=None, net=None, battery=None):
        super().__init__()
        self.bars, self.net, self.battery = bars, net, battery
        self.set_valign(Gtk.Align.FILL)
        self.connect("draw", self._draw)
        self.set_size_request(self._width(), 12)

    def _layout(self):
        lay = self.create_pango_layout(self.net or "4G")
        desc = self.get_style_context().get_font(Gtk.StateFlags.NORMAL).copy()
        desc.set_weight(Pango.Weight.MEDIUM)      # "4G" is medium weight on macOS
        lay.set_font_description(desc)
        return lay

    def _width(self):
        w, parts = 0, []
        if self.bars is not None:
            parts.append(4 * self.BAR_W + 3 * self.BAR_GAP)
        if self.net:
            parts.append(self._layout().get_pixel_extents()[1].width)
        if self.battery is not None:
            parts.append(self.BATT_W)
        return int(sum(parts) + self.GAP * max(0, len(parts) - 1)) + 1

    def _draw(self, w, cr):
        c = self.get_style_context().get_color(self.get_state_flags())
        rgb, ink_a = (c.red, c.green, c.blue), c.alpha * 0.72
        lay = self._layout()
        ink, logical = lay.get_pixel_extents()
        h = self.get_allocated_height()
        ltop = round((h - logical.height) / 2)          # text box, centered
        top, cap = ltop + ink.y, ink.height            # top and height of the capitals
        x = 0.0

        if self.bars is not None:
            for i in range(4):
                bh = cap * (0.45 + 0.55 * i / 3)       # tallest = cap height
                cr.set_source_rgba(*rgb, ink_a if i < self.bars else ink_a * 0.35)
                _rrect(cr, x, top + cap - bh, self.BAR_W, bh, self.BAR_W / 2)
                cr.fill()
                x += self.BAR_W + self.BAR_GAP
            x += self.GAP - self.BAR_GAP

        if self.net:
            cr.set_source_rgba(*rgb, ink_a)
            cr.move_to(x - ink.x, ltop)
            PangoCairo.show_layout(cr, lay)
            x += ink.width + self.GAP

        if self.battery is not None:
            pct, charging = self.battery
            bh = cap + 2                                # battery slightly taller than the text, same center
            y = top + cap / 2 - bh / 2
            body = self.BATT_W - 2.5
            cr.set_line_width(1)
            cr.set_source_rgba(*rgb, c.alpha * 0.4)
            _rrect(cr, x + 0.5, y + 0.5, body - 1, bh - 1, 3)
            cr.stroke()
            _rrect(cr, x + body + 0.5, y + bh / 2 - 2, 1.8, 4, 0.9)
            cr.fill()
            if charging:
                cr.set_source_rgba(0.204, 0.780, 0.349, 1)     # green while charging
            elif pct <= 20:
                cr.set_source_rgba(1.0, 0.231, 0.188, 1)       # red when low
            else:
                cr.set_source_rgba(*rgb, ink_a)
            inner = body - 4
            _rrect(cr, x + 2, y + 2, max(2.0, inner * min(pct, 100) / 100), bh - 4, 1.6)
            cr.fill()
        return False


class NetworkManager(Gtk.Box):
    def __init__(self, app_instance):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        
        self.app = app_instance
        
        self.set_margin_start(13); self.set_margin_end(13)   # content and separators 15 px from the window edge
        self.set_margin_top(13); self.set_margin_bottom(6)
        self.get_style_context().remove_class("styledwidgetbox")

        # --- Header: title (click to go back) and switch ---
        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        
        wifi_title_eb = Gtk.EventBox()
        wifi_title_eb.set_visible_window(False)
        wifi_title_eb.set_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        
        wifi_title = Gtk.Label()
        wifi_title.set_markup("<span weight='bold' size='medium'>Wi-Fi</span>")
        wifi_title.set_halign(Gtk.Align.START)
        wifi_title.set_margin_start(2)        
        wifi_title_eb.add(wifi_title)
        
        wifi_title_eb.connect("button-press-event", lambda w, e: [
            self.app.view_stack.set_visible_child_name("main_view") if (self.app and hasattr(self.app, 'view_stack')) else None
        ] if e.button == 1 else False)

        self.wifi_switch = Gtk.Switch()
        self.wifi_switch.set_halign(Gtk.Align.END)
        self.wifi_switch.set_margin_end(1)
        
        try:
            if self.app and hasattr(self.app, 'connections_box') and self.app.connections_box.network_info:
                self.wifi_switch.set_active(self.app.connections_box.network_info.is_wifi_enabled())
            else:
                self.wifi_switch.set_active(True)
        except:
            self.wifi_switch.set_active(True)
        
        self.wifi_switch.connect("state-set", self._on_wifi_switch_toggled)

        top_row.pack_start(wifi_title_eb, True, True, 0)
        top_row.pack_end(self.wifi_switch, False, False, 0)
        self.pack_start(top_row, False, False, 0)

        slider_sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        slider_sep.set_margin_top(1); slider_sep.set_margin_bottom(0)
        slider_sep.set_margin_start(2); slider_sep.set_margin_end(2)   # 16 px from the window edge, same as in the list
        self.pack_start(slider_sep, False, False, 0)

        # --- Network list ---
        scroll_window = Gtk.ScrolledWindow()
        scroll_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll_window.set_shadow_type(Gtk.ShadowType.NONE)

        scroll_window.set_size_request(260, -1) 
        scroll_window.set_propagate_natural_height(True)
        scroll_window.set_min_content_height(0) 
        scroll_window.set_max_content_height(600)

        self.wifi_list_box = Gtk.ListBox()
        self.wifi_list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.wifi_list_box.get_style_context().add_class("transparent-listbox")
        
        scroll_window.add(self.wifi_list_box)
        self.pack_start(scroll_window, True, True, 0)

        # "Other Networks" toggle: connected once, always uses the latest network list
        self._wifi_networks = []
        self._other_rows = []        # rows under "Other Networks" (hidden when collapsed)
        self._other_arrow = None
        self._other_visible = True
        self._last_shown = None      # last drawn list (not rebuilt if unchanged)
        self._scanning = False

        self._other_spinner = None
        self._scan_in_progress = False
        self._refresh_pending = None
        self._saved_ssids = None     # real SSIDs of saved networks (from D-Bus)
        self._hotspots = self._load_hotspots()   # {ssid: {"kind": "phone"/"iphone", "device": valent_id}}
        self._phones = {}            # phones connected to Valent (battery, network type, signal)

        # Same approach as nm-applet: listen to NetworkManager over D-Bus for networks coming
        # and going, scans finishing and Wi-Fi turning on/off. No polling.
        self._bus = None
        self._wdev = None
        try:
            self._nm_setup()
        except Exception as e:
            print(f"Could not connect to NetworkManager over D-Bus, using nmcli: {e}")
            self._bus = None

        # Fallback without D-Bus: poll with nmcli every 4 s
        GLib.timeout_add_seconds(4, self._live_refresh)

    # ── NetworkManager D-Bus ────────────────────────────────
    def _nm_call(self, path, iface, method, args=None):
        res = self._bus.call_sync(NM, path, iface, method, args, None,
                                  Gio.DBusCallFlags.NONE, 3000, None)
        return res.unpack() if res is not None else ()

    def _nm_props(self, path, iface):
        return self._nm_call(path, "org.freedesktop.DBus.Properties", "GetAll",
                             GLib.Variant("(s)", (iface,)))[0]

    def _nm_setup(self):
        self._bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        for dev in self._nm_call(NM_PATH, NM, "GetDevices")[0]:
            if self._nm_props(dev, NM + ".Device").get("DeviceType") == 2:   # 2 = Wi-Fi
                self._wdev = dev
                break
        if not self._wdev:
            raise RuntimeError("No Wi-Fi device found")

        sub = self._bus.signal_subscribe
        flags = Gio.DBusSignalFlags.NONE
        # Network list changed
        sub(NM, NM + ".Device.Wireless", "AccessPointAdded", self._wdev, None, flags,
            lambda *a: self._schedule_refresh())
        sub(NM, NM + ".Device.Wireless", "AccessPointRemoved", self._wdev, None, flags,
            lambda *a: self._schedule_refresh())
        # Scan finished / active network changed
        sub(NM, "org.freedesktop.DBus.Properties", "PropertiesChanged", self._wdev, None, flags,
            self._on_wdev_props)
        # Connection states (to catch a wrong password right away)
        sub(NM, NM + ".Device", "StateChanged", self._wdev, None, flags, self._on_wdev_state)
        # Wi-Fi turned on / off
        sub(NM, "org.freedesktop.DBus.Properties", "PropertiesChanged", NM_PATH, None, flags,
            self._on_nm_props)
        # Saved network added / removed
        for sig in ("NewConnection", "ConnectionRemoved"):
            sub(NM, NM + ".Settings", sig, NM_PATH + "/Settings", None, flags,
                lambda *a: self._invalidate_saved())

    def _on_wdev_props(self, conn, sender, path, iface, signal, params):
        changed = params.unpack()[1]
        if "LastScan" in changed:
            self._scan_in_progress = False
            self._update_spinner()
        if {"LastScan", "ActiveAccessPoint", "AccessPoints", "State"} & set(changed):
            self._schedule_refresh()

    def _on_nm_props(self, conn, sender, path, iface, signal, params):
        changed = params.unpack()[1]
        if "WirelessEnabled" in changed:
            self._wifi_on = bool(changed["WirelessEnabled"])
            self._sync_switch()
            self._schedule_refresh()
        if "Connectivity" in changed:   # internet came / went
            self._schedule_refresh()

    def _invalidate_saved(self):
        self._saved_ssids = None
        self._schedule_refresh()

    def _view_visible(self):
        try:
            return self.app.view_stack.get_visible_child_name() == "wifi_view"
        except Exception:
            return False

    def _schedule_refresh(self):
        """A scan sends a burst of signals; read once, 300 ms later."""
        if not self._view_visible():
            return
        if self._refresh_pending:
            GLib.source_remove(self._refresh_pending)
        def run():
            self._refresh_pending = None
            threading.Thread(target=self._dbus_refresh_worker, daemon=True).start()
            return False
        self._refresh_pending = GLib.timeout_add(300, run)

    def _dbus_refresh_worker(self):
        try:
            nets = self._read_networks_dbus()
            GLib.idle_add(self._populate_if_changed, nets)
        except Exception as e:
            print(f"Could not read Wi-Fi networks: {e}")

    def _read_saved_ssids(self):
        ssids = set()
        for c in self._nm_call(NM_PATH + "/Settings", NM + ".Settings", "ListConnections")[0]:
            try:
                st = self._nm_call(c, NM + ".Settings.Connection", "GetSettings")[0]
                raw = st.get("802-11-wireless", {}).get("ssid")
                if raw:
                    ssids.add(bytes(raw).decode("utf-8", "replace"))
            except Exception:
                continue
        return ssids

    def _read_networks_dbus(self):
        """[(ssid, signal, '*' or '', secured)], connected first, then strongest first.
        The lock icon comes from the network's real security flags."""
        nm = self._nm_props(NM_PATH, NM)
        self._wifi_on = bool(nm.get("WirelessEnabled", True))
        # NetworkManager connectivity: 4 = full, 3 = limited (no internet), 2 = captive portal
        # (hotel/cafe), 1 = none (no IP), 0 = unknown (checking disabled)
        self._connectivity = int(nm.get("Connectivity", 0))
        if self._saved_ssids is None:
            self._saved_ssids = self._read_saved_ssids()
        self._saved_connections = sorted(self._saved_ssids)

        wl = self._nm_props(self._wdev, NM + ".Device.Wireless")
        active_path = wl.get("ActiveAccessPoint", "/")
        best = {}
        active_ssid = ""
        for ap in wl.get("AccessPoints", []):
            try:
                p = self._nm_props(ap, NM + ".AccessPoint")
            except Exception:
                continue   # may have disappeared during the scan
            ssid = bytes(p.get("Ssid", b"")).decode("utf-8", "replace").strip()
            if not ssid:
                continue
            strength = int(p.get("Strength", 0))
            secured = bool(p.get("WpaFlags", 0) or p.get("RsnFlags", 0) or (p.get("Flags", 0) & 0x1))
            in_use = "*" if ap == active_path else ""
            if in_use:
                active_ssid = ssid
            old = best.get(ssid)
            if old is None or in_use or (not old[2] and strength > old[1]):
                best[ssid] = (ssid, strength, in_use, secured)
        self._active_ssid = active_ssid
        self._phones = self._read_phones()
        self._detect_hotspot(active_ssid)
        return sorted(best.values(), key=lambda n: (n[2] != "*", -n[1], n[0].lower()))

    # ── Personal Hotspot (phone hotspot) ────────────────────
    def _load_hotspots(self):
        try:
            with open(HOTSPOT_STATE) as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def _save_hotspots(self):
        try:
            os.makedirs(os.path.dirname(HOTSPOT_STATE), exist_ok=True)
            with open(HOTSPOT_STATE, "w") as f:
                json.dump(self._hotspots, f, ensure_ascii=False, indent=1)
        except Exception as e:
            print(f"Could not save the hotspot list: {e}")

    def _detect_hotspot(self, ssid):
        """Is the connected network a phone's hotspot?
        Android hotspots announce a metered connection; NetworkManager keeps it as Metered
        (1 = yes, 3 = guessed yes). iPhones always hand out 172.20.10.x addresses. A network
        recognized once is remembered and shows under "Personal Hotspot" when not connected."""
        if not ssid:
            return
        try:
            dev = self._nm_props(self._wdev, NM + ".Device")
        except Exception:
            return
        iphone = False
        ip4 = dev.get("Ip4Config", "/")
        if ip4 and ip4 != "/":
            try:
                for a in self._nm_props(ip4, NM + ".IP4Config").get("AddressData", []):
                    if str(a.get("address", "")).startswith("172.20.10."):
                        iphone = True
            except Exception:
                pass
        if not (iphone or int(dev.get("Metered", 0)) in (1, 3)):
            return
        old = self._hotspots.get(ssid, {})
        new = dict(old, kind="iphone" if iphone else "phone")
        if not iphone:
            # With exactly one phone connected to Valent, the hotspot is that phone's
            live = [d for d, p in self._phones.items() if p.get("connected")]
            if len(live) == 1:
                new["device"] = live[0]
        if new != old:
            self._hotspots[ssid] = new
            self._save_hotspots()

    def _read_phones(self):
        """Phones known to Valent: {id: {name, connected, battery: (percent, charging),
        net: (4G/5G..., 0-4 bars)}}. Empty if Valent isn't running (we don't start it)."""
        phones = {}
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

            def call(dest, path, iface, method, args=None):
                return bus.call_sync(dest, path, iface, method, args, None,
                                     Gio.DBusCallFlags.NO_AUTO_START, 800, None).unpack()

            if not call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                        "NameHasOwner", GLib.Variant("(s)", (VALENT,)))[0]:
                return phones
            xml = call(VALENT, VALENT_DEVICES, "org.freedesktop.DBus.Introspectable", "Introspect")[0]
            for child in Gio.DBusNodeInfo.new_for_xml(xml).nodes:
                path = f"{VALENT_DEVICES}/{child.path}"
                try:
                    props = call(VALENT, path, "org.freedesktop.DBus.Properties", "GetAll",
                                 GLib.Variant("(s)", (VALENT + ".Device",)))[0]
                    actions = call(VALENT, path, "org.gtk.Actions", "DescribeAll")[0]
                except Exception:
                    continue
                # State: 1 = connected, 2 = paired (3 = both)
                info = {"name": str(props.get("Name", "")),
                        "connected": bool(int(props.get("State", 0)) & 1),
                        "battery": None, "net": None}
                bat = actions.get("battery.state")
                if bat and bat[2]:
                    b = bat[2][0]
                    if b.get("is-present", True) and "percentage" in b:
                        info["battery"] = (int(round(float(b["percentage"]))), bool(b.get("charging", False)))
                con = actions.get("connectivity_report.state")
                if con and con[2]:
                    best = None
                    # Dual-SIM phones report each SIM: pick a known type with the strongest signal
                    for sim in (con[2][0].get("signal-strengths") or {}).values():
                        kind = NET_TYPES.get(str(sim.get("network-type", "")))
                        bars = max(0, min(4, int(sim.get("signal-strength", 0))))
                        if best is None or (kind is not None, bars) > (best[0] is not None, best[1]):
                            best = (kind, bars)
                    info["net"] = best
                phones[str(props.get("Id") or child.path)] = info
        except Exception as e:
            print(f"Could not read Valent: {e}")
        return phones

    def _request_scan(self):
        """Requests a scan, as nm-applet does when its menu opens. NetworkManager refuses if it
        scanned recently; fine, its current list is shown anyway."""
        try:
            self._nm_call(self._wdev, NM + ".Device.Wireless", "RequestScan",
                          GLib.Variant("(a{sv})", ({},)))
            self._scan_in_progress = True
            GLib.idle_add(self._update_spinner)
            def give_up():
                if self._scan_in_progress:
                    self._scan_in_progress = False
                    self._update_spinner()
                return False
            GLib.timeout_add_seconds(8, give_up)
        except Exception:
            pass

    def _update_spinner(self):
        sp = self._other_spinner
        if sp is not None:
            if self._scan_in_progress:
                sp.show(); sp.start()
            else:
                sp.stop(); sp.hide()
        return False

    def _live_refresh(self):
        try:
            visible = self.app.view_stack.get_visible_child_name() == "wifi_view"
        except Exception:
            visible = False
        if visible and not self._scanning and self._bus is None:   # only without D-Bus
            threading.Thread(target=self._live_refresh_worker, daemon=True).start()
        return True

    def _live_refresh_worker(self):
        self._scanning = True
        try:
            self._fetch_saved_and_active()
            # --rescan auto: NetworkManager rescans if the last scan is older than 30 s
            nets = self._read_networks(rescan="auto")
            GLib.idle_add(self._populate_if_changed, nets)
        finally:
            self._scanning = False

    def _password_box_open(self):
        return any(getattr(r, "password_box_active", False) for r in self.wifi_list_box.get_children())

    def _populate_if_changed(self, networks):
        key = self._shown_key(networks)
        if key == self._last_shown:
            self._sync_switch()
            return False
        if self._password_box_open():   # don't rebuild the list while a password is typed
            return False
        return self._populate_wifi_networks(networks)

    def _shown_key(self, networks):
        """Same list? A change in phone battery/signal also counts as a change."""
        return (tuple(networks), tuple(getattr(self, "_saved_connections", [])),
                getattr(self, "_active_ssid", ""), getattr(self, "_connectivity", 0),
                repr((sorted(self._hotspots.items()), sorted(self._phones.items()))))

    def _sync_switch(self):
        """Syncs the switch with the system, only if different and without toggling Wi-Fi."""
        on = getattr(self, "_wifi_on", None)   # read in the background
        if on is None:
            return
        if self.wifi_switch.get_active() != on:
            self.wifi_switch.handler_block_by_func(self._on_wifi_switch_toggled)
            self.wifi_switch.set_active(on)
            self.wifi_switch.handler_unblock_by_func(self._on_wifi_switch_toggled)

    _OTHER_NETS_STATE = os.path.expanduser("~/.config/cupertino-control-center/cupertinocontrolcenter/functions/states/other_nets_expanded")

    def _get_other_expanded(self):
        try:
            with open(self._OTHER_NETS_STATE, "r") as f:
                return f.read().strip() == "true"
        except Exception:
            return True

    def _on_other_nets_clicked(self, widget, event):
        """Click on the "Other Networks" row (a direct click handler; row-activated could be
        lost while a scan rebuilt the list)."""
        if event.type != Gdk.EventType.BUTTON_PRESS or event.button != 1:
            return event.button == 1   # the second event of a double click must not undo it
        # The list is not rebuilt: the rows are already there and are only shown or hidden
        expanded = not self._other_visible
        self._set_other_visible(expanded)
        try:
            os.makedirs(os.path.dirname(self._OTHER_NETS_STATE), exist_ok=True)
            with open(self._OTHER_NETS_STATE, "w") as f:
                f.write("true" if expanded else "false")
        except Exception as e:
            print(f"Could not save the Other Networks state: {e}")
        if self.app and hasattr(self.app, '_force_single_resize'):
            GLib.idle_add(lambda: self.app._force_single_resize() or False)
        return True

    def _set_other_visible(self, visible):
        self._other_visible = visible
        for r in self._other_rows:
            r.set_visible(visible)
        if self._other_arrow is not None:
            self._other_arrow.set_markup(f"<span size='medium'>{'⌃' if visible else '›'}</span>")

    def _on_wifi_switch_toggled(self, switch, state):
        """Turns Wi-Fi on/off from the switch, in the background."""
        def worker():
            try:
                if self.app and hasattr(self.app, 'connections_box') and self.app.connections_box and self.app.connections_box.network_info:
                    self.app.connections_box.network_info.toggle_wifi(state)
                    
                    GLib.timeout_add(1000, lambda: threading.Thread(
                        target=self.app.connections_box._background_refresh_worker, 
                        daemon=True
                    ).start() or False)
                    # Networks appear within seconds after turning on; refresh now
                    GLib.timeout_add(1500, lambda: self._live_refresh() and False)
            except Exception as e:
                print(f"Could not toggle Wi-Fi: {e}")

        threading.Thread(target=worker, daemon=True).start()
        return False

    def scan_wifi_networks(self):
        """Called when the view opens."""
        threading.Thread(target=self._scan_wifi_networks_worker, daemon=True).start()

    def _scan_wifi_networks_worker(self):
        # 1) NetworkManager's last scan arrives at once (~50 ms), so the window opens at the
        #    right size. 2) Then a fresh scan, which updates the list if it changed.
        if self._bus is not None:
            # Show the current list now and request a fresh scan; new networks arrive through
            # NetworkManager's signals
            try:
                self._saved_ssids = None   # re-read saved networks every time the view opens
                nets = self._read_networks_dbus()
                GLib.idle_add(self._populate_wifi_networks, nets)
            except Exception as e:
                print(f"Could not read Wi-Fi networks: {e}")
                GLib.idle_add(self._populate_wifi_networks, [])
            self._request_scan()
            return
        self._scanning = True
        try:
            self._fetch_saved_and_active()
            first = self._read_networks(rescan="no")
            GLib.idle_add(self._populate_wifi_networks, first)
            fresh = self._read_networks(rescan="auto")
            self._fetch_saved_and_active()
            GLib.idle_add(self._populate_if_changed, fresh)
        finally:
            self._scanning = False

    def _fetch_saved_and_active(self):
        """Saved connections and the active network, read in the background."""
        saved, active = [], ""
        try:
            out = subprocess.check_output(["nmcli", "-t", "-f", "NAME", "connection", "show"],
                                          text=True, stderr=subprocess.DEVNULL, timeout=3)
            saved = [l.strip() for l in out.splitlines() if l.strip()]
        except Exception:
            pass
        try:
            active = subprocess.check_output(["iwgetid", "-r"], text=True,
                                             stderr=subprocess.DEVNULL, timeout=2).strip()
        except Exception:
            pass
        self._saved_connections, self._active_ssid = saved, active
        try:
            out = subprocess.check_output(["nmcli", "radio", "wifi"], text=True,
                                          stderr=subprocess.DEVNULL, timeout=2).strip()
            self._wifi_on = (out == "enabled")
        except Exception:
            pass

    def _read_networks(self, rescan="auto"):
        try:
            cmd = f"nmcli -t -f IN-USE,SSID,SIGNAL device wifi list --rescan {rescan}"
            output = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL, timeout=15)
            networks = []
            seen_ssids = set()
            
            for line in output.strip().split("\n"):
                if not line or ":" not in line: continue
                parts = line.split(":")
                if len(parts) < 3: continue
                
                in_use = parts[0].strip()
                ssid = parts[1]
                
                if not ssid or ssid in seen_ssids: continue 
                try: signal = int(parts[2])
                except: signal = 50
                
                seen_ssids.add(ssid)
                networks.append((ssid, signal, in_use))
            return networks
        except Exception:
            return []

    def _populate_wifi_networks(self, networks):
        self.wifi_list_box._prefill = False  # real data arrived (main.py marks its placeholder fill)
        self._wifi_networks = networks       # the Other Networks toggle uses this latest list
        for child in self.wifi_list_box.get_children():
            self.wifi_list_box.remove(child)
        self._other_rows = []
        self._other_arrow = None
        self._other_spinner = None
            

        self._last_shown = self._shown_key(networks)
        # Only touch the switch when it differs, without emitting its signal
        self._sync_switch()

        # Values read in the background (no commands on the UI thread). The active network
        # is also marked with '*' in the list.
        saved_connections = getattr(self, "_saved_connections", [])
        active_ssid = getattr(self, "_active_ssid", "")
        if not active_ssid:
            for item in networks:
                if isinstance(item, (list, tuple)) and len(item) >= 3 and str(item[2]) == "*":
                    active_ssid = str(item[0]); break

        hotspot_nets = []   # "Personal Hotspot", at the top as on macOS
        known_nets = [] 
        other_nets = []

        for item in networks:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                ssid = str(item[0])
                try: signal = int(item[1])
                except: signal = 50
                in_use = str(item[2]) if len(item) >= 3 else ""
                secured = item[3] if len(item) >= 4 else None   # None: unknown (nmcli fallback)
            else:
                continue
                
            if not ssid or ssid.strip() == "": continue
            
            if ssid in self._hotspots:
                hotspot_nets.append((ssid, signal, in_use, secured))
            elif ssid.strip() == active_ssid.strip() or ssid in saved_connections:
                known_nets.append((ssid, signal, in_use, secured))
            else:
                other_nets.append((ssid, signal, in_use, secured))

        is_expanded = self._get_other_expanded()

        def add_category_header(text, has_arrow=False):
            row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
            row_box.set_hexpand(True)
            title_lbl = Gtk.Label()
            title_lbl.set_text(text)
            title_lbl.get_style_context().add_class("mac-section-title")   # font in CSS
            title_lbl.set_halign(Gtk.Align.START)
            title_lbl.set_margin_top(0); title_lbl.set_margin_bottom(0); title_lbl.set_margin_start(0)   # lines up with "Wi-Fi" and the circles (15 px)
            row_box.pack_start(title_lbl, True, True, 0)
            
            if has_arrow:
                arrow_lbl = Gtk.Label()
                arrow_text = "⌃" if is_expanded else "›"
                arrow_lbl.set_markup(f"<span size='medium'>{arrow_text}</span>")
                arrow_lbl.set_halign(Gtk.Align.END)
                arrow_lbl.set_margin_top(0); arrow_lbl.set_margin_bottom(0); arrow_lbl.set_margin_end(5)
                row_box.pack_end(arrow_lbl, False, False, 0)
                self._other_arrow = arrow_lbl
                # Small spinner next to the header while scanning, as on macOS
                spinner = Gtk.Spinner()
                spinner.set_size_request(12, 12)
                spinner.set_valign(Gtk.Align.CENTER)
                spinner.set_margin_end(6)
                spinner.set_no_show_all(True)
                row_box.pack_end(spinner, False, False, 0)
                self._other_spinner = spinner
                
            row_shell = Gtk.ListBoxRow()
            row_shell.set_hexpand(True)
            row_shell.set_can_focus(False)
            row_shell.set_selectable(False)
            row_shell.set_activatable(False)

            if has_arrow:
                # "Other Networks" is a clickable row with a hover effect; its text keeps the
                # header grey (CSS: mac-category-toggle-row)
                eb = Gtk.EventBox()
                eb.set_visible_window(False)
                eb.add(row_box)
                eb.connect("button-press-event", self._on_other_nets_clicked)
                row_shell.add(eb)
                row_shell.get_style_context().add_class("mac-wifi-row")
                row_shell.get_style_context().add_class("mac-category-toggle-row")
                row_box.set_margin_top(1); row_box.set_margin_bottom(1)
            else:
                # "Known Networks": header only, no click or hover. Same 2/2 padding as "Other
                # Networks", so the space below both headers is equal.
                row_box.set_margin_top(0); row_box.set_margin_bottom(3)
                row_shell.add(row_box)
                row_shell.set_sensitive(False)
                row_shell.get_style_context().add_class("mac-category-title-row")

            self.wifi_list_box.add(row_shell)

        def add_pruist_separator(tight=False):
            """Full-width separator row."""
            sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            if tight:  # separator above the bottom settings row
                sep.get_style_context().add_class("tight-bottom")
            sep.set_margin_top(0); sep.set_margin_bottom(0); sep.set_margin_start(0); sep.set_margin_end(0)
            
            row_shell = Gtk.ListBoxRow()
            row_shell.set_sensitive(False); row_shell.set_can_focus(False); row_shell.set_selectable(False)
            row_shell.set_margin_start(0)
            row_shell.set_margin_end(0)
            row_shell.get_style_context().add_class("styled-separator-row")
            
            row_shell.add(sep)
            self.wifi_list_box.add(row_shell)

        def append_row_to_list(ssid, signal, in_use, secured=None, is_custom_other=False, is_custom_settings=False,
                               hotspot=None):
            row_shell = Gtk.ListBoxRow()
            row_shell.set_hexpand(True)
            row_shell.get_style_context().add_class("mac-wifi-row")  # rounded hover
            row_shell.set_margin_start(0); row_shell.set_margin_end(0)
            row_shell.set_margin_top(0); row_shell.set_margin_bottom(1)
            
            row_eb = Gtk.EventBox()
            row_eb.set_visible_window(False)
            
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            if is_custom_settings:
                row.set_margin_top(5); row.set_margin_bottom(3)
                row_shell.set_margin_top(0); row_shell.set_margin_bottom(0)
                row_shell.get_style_context().add_class("mac-bottom-settings-row")
            else:
                row.set_margin_top(0); row.set_margin_bottom(1)
                
            row.set_margin_start(0); row.set_margin_end(0)
            # The row sits in a vertical box, so the password field opens below the network
            row_v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            row_v.pack_start(row, False, False, 0)
            row_eb.add(row_v)
            row_shell.add(row_eb)
            
            if is_custom_other or is_custom_settings:
                lbl = Gtk.Label()
                lbl.set_markup(GLib.markup_escape_text(ssid))
                lbl.set_halign(Gtk.Align.START)
                if is_custom_settings:
                    lbl.set_margin_start(0)
                row.pack_start(lbl, True, True, 0)
                
                if is_custom_other:
                    row_eb.connect("button-press-event", lambda w, e: [GLib.spawn_command_line_async("nm-connection-editor --create &")] if e.button == 1 else False)
                elif is_custom_settings:
                    row_eb.connect("button-press-event", lambda w, e: [
                        GLib.spawn_command_line_async("nm-connection-editor &"),
                        self.app.window.hide() if (self.app and self.app.window) else None
                    ] if e.button == 1 else False)
            else:
                # Glyph in a circle as on macOS: blue when connected, grey otherwise.
                # Phone hotspots get the link glyph instead of Wi-Fi.
                level = 1 if signal < 30 else (2 if signal < 60 else 3)
                connected = ssid.strip() == active_ssid.strip() or in_use == "*"
                icon = RoundIcon("link" if hotspot is not None else "wifi", active=connected, level=level)
                # Connected without internet (no IP, limited, captive portal): Wi-Fi glyph with
                # an exclamation mark, as on macOS
                if connected and hotspot is None and getattr(self, "_connectivity", 0) in (1, 2, 3):
                    icon.set_state(no_internet=True)
                    
                lbl = Gtk.Label()
                lbl.set_markup(GLib.markup_escape_text(ssid))
                lbl.set_halign(Gtk.Align.START)
                lbl.set_ellipsize(Pango.EllipsizeMode.END)
                lbl.set_max_width_chars(22)
                
                row.pack_start(icon, False, False, 0)
                row.pack_start(lbl, True, True, 0)
                
                # No checkmark on the connected network (the blue circle is enough). The lock
                # shows on the connected network too, but not on hotspots, as on macOS.
                if hotspot is None:
                    lower_ssid = ssid.lower()
                    if secured is None:   # no security info: guess from the name
                        secured = "free" not in lower_ssid and "public" not in lower_ssid and "acik" not in lower_ssid
                    if secured:
                        padlock_icon = Gtk.Image.new_from_icon_name("changes-prevent-symbolic", Gtk.IconSize.MENU)
                        padlock_icon.set_opacity(0.6)
                        row.pack_end(padlock_icon, False, False, 2)

                # Phone signal, 4G/5G and battery (with Valent; not available for iPhones)
                phone = self._phones.get((hotspot or {}).get("device"))
                if phone and phone.get("connected") and (phone.get("net") or phone.get("battery")):
                    net = phone.get("net") or (None, None)
                    row.pack_end(PhoneStatus(bars=net[1], net=net[0], battery=phone.get("battery")),
                                 False, False, 2)
                        
                row_eb.connect("button-press-event", self._on_wifi_selected, ssid)
                
            self.wifi_list_box.add(row_shell)
            return row_shell

        if not networks:
            # No networks (or Wi-Fi off): just the settings row, as on macOS
            append_row_to_list(_("Network Settings…"), 0, "", is_custom_settings=True)
            self.wifi_list_box.show_all()
            if self.app and hasattr(self.app, '_force_single_resize'):
                GLib.idle_add(lambda: self.app._force_single_resize() or False)
            return False

        if hotspot_nets:
            add_category_header(_("Personal Hotspot"), has_arrow=False)
            for s, sig, iu, sec in hotspot_nets:
                append_row_to_list(s, sig, iu, sec, hotspot=self._hotspots.get(s, {}))
            add_pruist_separator()

        if known_nets:
            add_category_header(_("Known Networks"), has_arrow=False)
            for s, sig, iu, sec in known_nets:
                append_row_to_list(s, sig, iu, sec)
            add_pruist_separator()

        # "Other Networks" is always the same expandable row, also while scanning before any
        # network is found; networks are added below it
        add_category_header(_("Other Networks"), has_arrow=True)
        for s, sig, iu, sec in other_nets:
            self._other_rows.append(append_row_to_list(s, sig, iu, sec))
        self._other_rows.append(append_row_to_list(_("Other…"), 0, "", is_custom_other=True))

        add_pruist_separator(tight=True)
        append_row_to_list(_("Network Settings…"), 0, "", is_custom_settings=True)
        
        self.wifi_list_box.show_all()
        if self._other_rows:
            self._set_other_visible(is_expanded)   # after show_all: hide if collapsed
        self._update_spinner()
        
        if self.app and hasattr(self.app, '_force_single_resize'):
            GLib.idle_add(lambda: self.app._force_single_resize() or False)
        return False

    def _on_wifi_selected(self, widget, event, ssid):
        """Connects in the background: saved and open networks directly, others ask for a password."""
        if event.button != 1: return False

        cmd_check = 'nmcli -t -f NAME connection show'
        saved_connections = []
        try:
            output = subprocess.check_output(cmd_check, shell=True, text=True, stderr=subprocess.DEVNULL)
            saved_connections = [line.strip() for line in output.splitlines() if line.strip()]
        except: 
            pass

        # Saved network? Also check the real SSIDs from D-Bus: a connection's name can differ
        # from its SSID (e.g. "Home Wi-Fi 1")
        if ssid in (getattr(self, "_saved_ssids", None) or ()) and ssid not in saved_connections:
            saved_connections.append(ssid)
            by_ssid = True
        else:
            by_ssid = False

        if ssid in saved_connections:
            if self.app and hasattr(self.app, 'connections_box') and hasattr(self.app.connections_box, 'network_name'):
                self.app.connections_box.network_name.set_text(_("Connecting…"))
                
            def direct_connect_worker():
                try:
                    # by_ssid: the connection has another name; NetworkManager finds it by SSID
                    cmd_up = ["nmcli", "device", "wifi", "connect", ssid] if by_ssid else ["nmcli", "connection", "up", ssid]
                    result = subprocess.run(cmd_up, capture_output=True, text=True, timeout=12)
                    if result.returncode == 0:
                        GLib.idle_add(self._close_after_connect)
                    else:
                        print("Could not bring up the saved profile; asking for the password")
                        GLib.idle_add(lambda: self._show_password_box_inside_popover(widget, ssid))
                except Exception as e:
                    print(f"Connection error: {e}")
                    GLib.idle_add(lambda: self._show_password_box_inside_popover(widget, ssid))

            import threading
            threading.Thread(target=direct_connect_worker, daemon=True).start()
            return True

        # Open network: connect directly, no password field
        secured = None
        for n in self._wifi_networks or []:
            if isinstance(n, (list, tuple)) and n and n[0] == ssid and len(n) >= 4:
                secured = n[3]
                break
        if secured is False:
            if self.app and hasattr(self.app, 'connections_box') and hasattr(self.app.connections_box, 'network_name'):
                self.app.connections_box.network_name.set_text(_("Connecting…"))
            def open_worker():
                r = subprocess.run(["nmcli", "device", "wifi", "connect", ssid],
                                   capture_output=True, text=True, timeout=20)
                if r.returncode == 0:
                    GLib.idle_add(self._close_after_connect)
                else:
                    print(f"Could not connect to the open network: {r.stderr.strip()}")
            threading.Thread(target=open_worker, daemon=True).start()
            return True

        self._show_password_box_inside_popover(widget, ssid)
        return True

    def _close_after_connect(self):
        if self.app and hasattr(self.app, "close_cc"):
            self.app.close_cc()
        elif self.app and hasattr(self.app, 'view_stack'):
            self.app.view_stack.set_visible_child_name("main_view")
        return False

    def _show_password_box_inside_popover(self, widget, ssid):
        """Opens the password field below the network row."""
        row_shell = widget.get_parent()
        
        for child in self.wifi_list_box.get_children():
            if child != row_shell and hasattr(child, "password_box_active") and child.password_box_active:
                inner_box = child.get_child().get_child()
                if hasattr(child, "password_box") and child.password_box in inner_box.get_children():
                    inner_box.remove(child.password_box)
                child.password_box_active = False
                child.get_style_context().remove_class("mac-row-expanded")

        if hasattr(row_shell, "password_box_active") and row_shell.password_box_active:
            row_shell.get_child().get_child().remove(row_shell.password_box)
            row_shell.password_box_active = False
            row_shell.get_style_context().remove_class("mac-row-expanded")
            row_shell.show_all()
            if self.app and hasattr(self.app, '_force_single_resize'):
                GLib.idle_add(self.app._force_single_resize)
            return True

        inner_box = widget.get_child()   # the row's vertical box
        pass_container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        pass_container.set_margin_top(4); pass_container.set_margin_bottom(4)
        # Full row width, from the icon to the right edge
        pass_container.set_margin_start(0); pass_container.set_margin_end(2)
        
        password_entry = Gtk.Entry()
        password_entry.set_placeholder_text(_("Password"))
        password_entry.set_visibility(False)
        password_entry.set_hexpand(True)
        password_entry.set_max_width_chars(20)
        password_entry.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, "go-next-symbolic")
        # Eye icon on the left: show/hide the password (like macOS's "Show password")
        password_entry.set_icon_from_icon_name(Gtk.EntryIconPosition.PRIMARY, "view-reveal-symbolic")
        password_entry.set_icon_tooltip_text(Gtk.EntryIconPosition.PRIMARY, _("Show password"))
        
        password_entry.connect("activate", lambda e: self._connect_to_wifi(None, ssid, password_entry, pass_container))
        def on_icon(entry, pos, ev):
            if pos == Gtk.EntryIconPosition.PRIMARY:
                show = not entry.get_visibility()
                entry.set_visibility(show)
                entry.set_icon_from_icon_name(Gtk.EntryIconPosition.PRIMARY,
                                              "view-conceal-symbolic" if show else "view-reveal-symbolic")
            else:
                self._connect_to_wifi(None, ssid, entry, pass_container)
        password_entry.connect("icon-press", on_icon)
        
        pass_container.pack_start(password_entry, True, True, 0)
        inner_box.pack_start(pass_container, False, False, 0)
        
        row_shell.password_box = pass_container
        row_shell.password_box_active = True
        # Keep the row highlighted while the field is open, so name and field read as one block
        row_shell.get_style_context().add_class("mac-row-expanded")
        row_shell.show_all()
        password_entry.grab_focus()   # type right away
        
        if self.app and hasattr(self.app, '_force_single_resize'):
            GLib.idle_add(self.app._force_single_resize)
        return True

    def _connect_to_wifi(self, dummy, ssid, entry_widget, container):
        password = entry_widget.get_text()
        if not password:
            return
        entry_widget.set_sensitive(False)
        entry_widget.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, "view-refresh-symbolic")

        # Run as an argument list, not through a shell, so " $ ` \ in passwords are safe
        try:
            proc = subprocess.Popen(["nmcli", "device", "wifi", "connect", ssid, "password", password],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            self._connection_result(False, ssid, container, entry_widget)
            return
        # Pending attempt: with a wrong password NetworkManager goes to NEED_AUTH; that is caught
        # over D-Bus right away and the attempt is ended (_on_wdev_state)
        self._pending_connect = {"ssid": ssid, "proc": proc, "container": container,
                                 "entry": entry_widget, "done": False}
        pending = self._pending_connect

        def waiter():
            try:
                rc = proc.wait(timeout=40)
            except Exception:
                proc.kill(); rc = 1
            GLib.idle_add(lambda: self._finish_connect(pending, rc == 0) or False)

        threading.Thread(target=waiter, daemon=True).start()

    def _on_wdev_state(self, conn, sender, path, iface, signal, params):
        new_state = params.unpack()[0]
        p = getattr(self, "_pending_connect", None)
        if p and not p["done"] and new_state == 60:   # 60 = NEED_AUTH: password rejected
            # NetworkManager would now ask the secret agent (nm-applet) and its "Authentication
            # required" dialog would open. Cancel the attempt instead: deleting the profile
            # drops the pending request, so no dialog appears.
            try:
                p["proc"].terminate()
            except Exception:
                pass
            self._finish_connect(p, False)

    def _finish_connect(self, p, ok):
        if p["done"]:
            return
        p["done"] = True
        if not ok:
            # nmcli saves a profile with the wrong password even when it fails; delete it, or the
            # network would show under "Known Networks" and retry the wrong password
            ssid = p["ssid"]
            threading.Thread(target=lambda: subprocess.run(
                ["nmcli", "connection", "delete", "id", ssid],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL), daemon=True).start()
        self._connection_result(ok, p["ssid"], p["container"], p["entry"])

    def _connection_result(self, success, ssid, container, entry_widget):
        """Updates the UI when the nmcli connection attempt finishes."""
        if success:
            # Password accepted
            row_shell = container.get_parent().get_parent().get_parent()
            container.get_parent().remove(container)
            
            if hasattr(row_shell, "password_box_active"):
                row_shell.password_box_active = False
                row_shell.get_style_context().remove_class("mac-row-expanded")
                
            # As on macOS: the Control Center closes once connected
            self._close_after_connect()
        else:
            # Wrong password:
            entry_widget.set_sensitive(True)
            entry_widget.set_text("")
            entry_widget.set_placeholder_text(_("Wrong password. Try again"))
            entry_widget.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, "dialog-warning-symbolic")
