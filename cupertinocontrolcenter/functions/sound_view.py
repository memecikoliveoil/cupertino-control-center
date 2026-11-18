import gi
import subprocess
import os
import re
import json
import threading
import signal
import ctypes
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gdk, Gio, Pango, GdkPixbuf
from cupertinocontrolcenter.widgets.round_icon import RoundIcon
from cupertinocontrolcenter.paths import STATE_DIR
from cupertinocontrolcenter.i18n import _
from cupertinocontrolcenter.spawn import spawn

class SoundView(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.app = Gtk.Application.get_default()
        self.last_non_zero_volume = 50

        self.set_margin_start(7); self.set_margin_end(7)   # content and separators 15 px from the window edge
        self.set_margin_top(8); self.set_margin_bottom(6)
        self.get_style_context().remove_class("styledwidgetbox")
        
        # --- Header: clicking the title goes back ---
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        header.set_margin_start(6); header.set_margin_end(0)   # same x as the Display and Wi-Fi titles (15 px)
        header.set_margin_bottom(6); header.set_margin_top(6)
        h_eb = Gtk.EventBox(); h_eb.set_visible_window(False)
        lbl_h = Gtk.Label()
        lbl_h.set_markup("<span weight='bold' size='medium'>%s</span>" % GLib.markup_escape_text(_("Sound")))
        lbl_h.set_halign(Gtk.Align.START); lbl_h.set_margin_start(2)
        h_eb.add(lbl_h); h_eb.connect("button-press-event", lambda w, e: self._go_back() if e.button == 1 else False)
        header.pack_start(h_eb, True, True, 0)
        self.pack_start(header, False, False, 0)

        # --- Volume slider (full width) ---
        slider_outer_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        slider_outer_box.set_margin_bottom(2)
        slider_outer_box.set_margin_top(0)
        slider_outer_box.set_margin_start(6); slider_outer_box.set_margin_end(6)   # same as the Display slider
        
        current_vol = 50   # fallback if the volume can't be read
        try:
            vol_out = subprocess.check_output("pactl get-sink-volume @DEFAULT_SINK@", shell=True, text=True, stderr=subprocess.DEVNULL)
            vol_match = re.search(r'(\d+)%', vol_out)
            if vol_match:
                current_vol = int(vol_match.group(1))
            # Muted: show 0, like the main view ("evet" = localized "yes")
            mute_out = subprocess.check_output(["pactl", "get-sink-mute", "@DEFAULT_SINK@"], text=True,
                                               stderr=subprocess.DEVNULL, timeout=2).strip()
            if mute_out and mute_out.split()[-1].lower() in ("yes", "evet", "true", "1"):
                current_vol = 0
        except:
            try:
                if self.app and hasattr(self.app, 'view_stack') and self.app.view_stack:
                    main_view = self.app.view_stack.get_child_by_name("main_view")
                    for child in main_view.get_children():
                        if "SoundLevelBox" in type(child).__name__:
                            current_vol = int(child.slider.get_value())
            except:
                pass

        if current_vol > 0: 
            self.last_non_zero_volume = current_vol

        self.adj = Gtk.Adjustment(value=current_vol, lower=0, upper=100, step_increment=1)
        self.sub_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.adj)
        self.sub_scale.set_draw_value(False)
        self.sub_scale.set_hexpand(True)
        self.sub_scale.set_size_request(-1, -1)
        self.sub_scale.get_style_context().add_class("widgetslider")
        self.sub_scale.connect("value-changed", self._on_sub_volume_changed)
        # Separate handler for the icon: the main view's Sound tile temporarily disconnects the
        # volume handler above when it syncs this slider, and the icon must still update.
        self.sub_scale.connect("value-changed", lambda s: self._update_internal_icon_instantly(int(s.get_value())))

        slider_overlay = Gtk.Overlay()
        slider_overlay.add(self.sub_scale)
        
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
        self.sub_scale.connect("button-press-event", self._on_slider_press_icon)
        slider_outer_box.pack_start(slider_overlay, True, True, 0)
        self.pack_start(slider_outer_box, False, False, 0)

        slider_sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        slider_sep.set_margin_top(0); slider_sep.set_margin_bottom(2)
        slider_sep.set_margin_start(8); slider_sep.set_margin_end(8)   # 16 px from the window edge
        self.pack_start(slider_sep, False, False, 0)

        # Output and input devices
        self.list_box = Gtk.ListBox()
        self.list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.list_box.get_style_context().add_class("transparent-listbox")
        self.list_box.set_margin_start(0); self.list_box.set_margin_end(0) 
        self.list_box.set_activate_on_single_click(False)
        self.pack_start(self.list_box, False, False, 0)

        slider_sep2 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        slider_sep2.set_margin_top(0); slider_sep2.set_margin_bottom(0)
        slider_sep2.set_margin_start(8); slider_sep2.set_margin_end(8)
        slider_sep2.get_style_context().add_class("tight-bottom")
        self.pack_start(slider_sep2, False, False, 0)

        # --- "Sound Settings…" row ---
        pref_row = Gtk.ListBox()
        pref_row.set_margin_start(6); pref_row.set_margin_end(0)
        pref_row.set_selection_mode(Gtk.SelectionMode.NONE)
        pref_row.get_style_context().add_class("transparent-listbox")
        
        p_item = Gtk.ListBoxRow()
        p_item.get_style_context().add_class("mac-wifi-row")
        p_item.get_style_context().add_class("row-hover-effect")
        p_item.get_style_context().add_class("mac-bottom-settings-row")
        
        p_eb = Gtk.EventBox(); p_eb.set_visible_window(False)
        p_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        p_box.set_margin_top(3); p_box.set_margin_bottom(3)  # room around the text for the hover box
        p_box.set_margin_start(0); p_box.set_margin_end(0)
        
        p_lbl = Gtk.Label(label=_("Sound Settings…"), xalign=0)
        p_lbl.set_margin_start(0); p_lbl.set_margin_top(0); p_lbl.set_margin_bottom(0)
        
        p_box.pack_start(p_lbl, True, True, 0)
        p_eb.add(p_box); p_item.add(p_eb); pref_row.add(p_item)
        p_eb.connect("button-press-event", lambda w, e: [
            spawn(["pavucontrol"]),
            self.app.window.hide() if (self.app and hasattr(self.app, 'window') and self.app.window) else None
        ] if e.button == 1 else False)
        
        self.pack_start(pref_row, False, False, 0)
        
        self._update_internal_icon_instantly(current_vol)
        self._last_devices = None
        self._refresh_pending = None
        self._name_overrides = self._load_name_overrides()
        self.refresh_audio_devices()
        self.show_all()

        # Refresh the list when headphones / USB / Bluetooth devices come and go
        threading.Thread(target=self._watch_audio_events, daemon=True).start()

    def _on_sub_volume_changed(self, scale):
        val = int(self.adj.get_value())
        # The volume is set through the main view's slider (which also unmutes), so both
        # sliders behave the same
        self._update_internal_icon_instantly(val)
        if val > 0: self.last_non_zero_volume = val
        
        try:
            if self.app and hasattr(self.app, 'view_stack') and self.app.view_stack:
                main_view = self.app.view_stack.get_child_by_name("main_view")
                for child in main_view.get_children():
                    if "SoundLevelBox" in type(child).__name__:
                        if int(child.slider.get_value()) != val:
                            child.slider.set_value(val)
                        return
        except: pass
        # Fallback if the main view's tile isn't there
        spawn(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{val}%"],
              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _on_embedded_icon_pressed(self, widget, event):
        if event.button == 1:
            curr_val = int(self.sub_scale.get_value())
            if curr_val > 0:
                self.last_non_zero_volume = curr_val
                self.sub_scale.set_value(0)
            else:
                self.sub_scale.set_value(self.last_non_zero_volume)
            return True
        return False

    def _on_slider_press_icon(self, scale, event):
        """A click exactly on the icon toggles mute, like the mute key: the slider shows 0, and
        another click restores the level. Anywhere else is a normal slider click."""
        if event.button != 1:
            return False
        r = scale.translate_coordinates(self.internal_icon, int(event.x), int(event.y))
        a = self.internal_icon.get_allocation()
        # Hit area: the icon plus 2 px on the right (tune the number to taste)
        if not r or not (0 <= r[0] < a.width + 2 and 0 <= r[1] < a.height):
            return False
        spawn(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"],
              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        GLib.timeout_add(80, self._after_mute_toggle)
        return True

    def _after_mute_toggle(self):
        # The main view's Sound tile reads the mute state and syncs both sliders
        try:
            main_view = self.app.view_stack.get_child_by_name("main_view")
            for child in main_view.get_children():
                if "SoundLevelBox" in type(child).__name__:
                    child.refresh_slider()
        except Exception:
            pass
        return False

    def _update_internal_icon_instantly(self, value):
        if value == 0: icon_name = "audio-volume-muted-symbolic"
        elif value <= 33: icon_name = "audio-volume-low-symbolic"
        elif value <= 66: icon_name = "audio-volume-medium-symbolic"
        else: icon_name = "audio-volume-high-symbolic"
        self.internal_icon.set_from_icon_name(icon_name, Gtk.IconSize.MENU)

    def refresh_audio_devices(self, force=True):
        outputs = self._parse_pipewire_hardware(is_sink=True)
        inputs = self._parse_pipewire_hardware(is_sink=False)
        batteries = dict(getattr(self, "_bt_battery", {}))
        if not force and (outputs, inputs, batteries) == self._last_devices:
            return False   # no change: don't rebuild (keeps the hover from flickering)
        self._last_devices = (outputs, inputs, batteries)

        for child in self.list_box.get_children():
            self.list_box.remove(child)

        # Output
        o_row = Gtk.ListBoxRow()
        o_row.set_sensitive(False)
        o_row.get_style_context().add_class("mac-category-title-row")
        o_lbl = Gtk.Label()
        o_lbl.set_text(_("Output"))
        o_lbl.get_style_context().add_class("mac-section-title")   # font in CSS
        o_lbl.set_halign(Gtk.Align.START); o_lbl.set_margin_start(6); o_lbl.set_margin_bottom(3)   # like "Known Networks" in Wi-Fi
        o_row.add(o_lbl); self.list_box.add(o_row)

        for i, (dev_type, dev_name, dev_desc, is_active, extra_id) in enumerate(outputs):
            icon_n = self.TYPE_ICONS.get(dev_type, "speaker")
            is_last = (i == len(outputs) - 1)
            self._create_device_row(dev_type, dev_name, dev_desc, icon_n, is_active, extra_id, is_sink=True, is_last=is_last)

        # Input: only shown when there is more than one microphone to choose from
        if len(inputs) > 1:
            sep_mid = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            sep_mid.set_margin_top(0); sep_mid.set_margin_bottom(0)
            sep_mid.set_margin_start(6); sep_mid.set_margin_end(6)   # the list row adds 2 px: 16 px in total
            sep_row_mid = Gtk.ListBoxRow(); sep_row_mid.set_sensitive(False)
            sep_row_mid.add(sep_mid); self.list_box.add(sep_row_mid)

            i_row = Gtk.ListBoxRow()
            i_row.set_sensitive(False)
            i_row.get_style_context().add_class("mac-category-title-row")
            i_lbl = Gtk.Label()
            i_lbl.set_text(_("Input"))
            i_lbl.get_style_context().add_class("mac-section-title")
            i_lbl.set_halign(Gtk.Align.START); i_lbl.set_margin_start(6); i_lbl.set_margin_top(2); i_lbl.set_margin_bottom(3)
            i_row.add(i_lbl); self.list_box.add(i_row)

            for j, (dev_type, dev_name, dev_desc, is_active, extra_id) in enumerate(inputs):
                icon_n = self.TYPE_ICONS.get(dev_type, "microphone")
                is_last_input = (j == len(inputs) - 1)
                self._create_device_row(dev_type, dev_name, dev_desc, icon_n, is_active, extra_id, is_sink=False, is_last=is_last_input)
        
        self.list_box.show_all()
        
        if self.app and hasattr(self.app, '_force_single_resize'):
            GLib.idle_add(self.app._force_single_resize)
        return False

    def _create_device_row(self, dev_type, dev_name, dev_desc, icon_name, is_active, extra_id, is_sink, is_last=False):
        row = Gtk.ListBoxRow()
        row.get_style_context().add_class("mac-wifi-row")
        row.get_style_context().add_class("row-hover-effect")
        
        row.set_margin_top(0)
        row.set_margin_bottom(1 if is_last else 5)
        
        eb = Gtk.EventBox(); eb.set_visible_window(False)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_margin_top(0); box.set_margin_bottom(0); box.set_margin_start(6)   # circles line up with the titles (15 px), as in Wi-Fi
        
        # As on macOS: the selected device has a blue circle, the others grey (no checkmark)
        icon = RoundIcon(icon_name, active=is_active)
        
        lbl = Gtk.Label(xalign=0)
        lbl.set_markup(f"<span>{dev_desc}</span>")
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        
        box.pack_start(icon, False, False, 0)
        box.pack_start(lbl, True, True, 0)
        # Battery percentage of Bluetooth headphones, right of the name, as on macOS
        batt = getattr(self, "_bt_battery", {}).get(dev_name) if is_sink else None
        if batt is not None:
            b_lbl = Gtk.Label(label=_("%d%%") % batt)
            b_lbl.get_style_context().add_class("mac-secondary-text")
            box.pack_end(b_lbl, False, False, 5)
            
        eb.connect("button-press-event", lambda w, e: self._on_hardware_selected(e, dev_type, dev_name, extra_id, is_sink))
        eb.add(box); row.add(eb); self.list_box.add(row)

    # ── Device names and icons ──────────────────────────────
    # Custom names for audio devices go in functions/states/settings.json, e.g.
    #   "sound_device_names": [
    #     {"match": ["beoplay", "portal"], "name": "B&O Beoplay Portal", "type": "headphone"}
    #   ]
    # A device whose system name or description contains any "match" word is shown as
    # "name"; "type" (optional, default "headphone") is a key of TYPE_ICONS below.
    INTERNAL_NAME = _("Internal Audio")
    # Device type -> glyph in the circle (round_icon.py). The built-in sound card (speakers
    # and microphone) gets a laptop, like "Internal Speakers" on macOS.
    TYPE_ICONS = {
        "internal": "laptop",
        "speaker": "speaker",
        "headphone": "headphones",
        "earbuds": "earbuds",
        "microphone": "microphone",
        "display": "monitor",
    }
    # Technical suffixes the system adds to device names (not shown)
    _SUFFIXES = re.compile(
        r"\s*(Analog|Digital|Pro|Multichannel)?\s*(Stereo|Mono|Surround [\d.]+|Input|Output)"
        r"(\s*\(.*?\))?\s*$|\s*(Headset Head Unit|Handsfree|High Fidelity Playback)\s*$", re.I)

    @staticmethod
    def _load_name_overrides():
        try:
            with open(os.path.join(STATE_DIR, "settings.json")) as f:
                entries = json.load(f).get("sound_device_names", [])
            return [([k.lower() for k in e["match"]], e["name"], e.get("type", "headphone"))
                    for e in entries if e.get("match") and e.get("name")]
        except Exception:
            return []

    @staticmethod
    def _pactl_json(*args):
        """pactl's JSON output, which is the same in every system language."""
        out = subprocess.check_output(["pactl", "-f", "json", *args], text=True,
                                      stderr=subprocess.DEVNULL, timeout=3)
        return json.loads(out) if out.strip() else []

    def _classify(self, dev, is_sink, mic_cards=()):
        """Returns (type, display name), from what the device reports about itself: bus
        (pci/usb/bluetooth), form factor (headphone/speaker...) and active port."""
        props = dev.get("properties", {}) or {}
        name = dev.get("name", "")
        desc = dev.get("description", "") or props.get("device.description", "") or name
        bus = (props.get("device.bus") or "").lower()
        form = (props.get("device.form_factor") or "").lower()
        port = (dev.get("active_port") or "").lower()
        blob = f"{name} {desc} {props.get('device.product.name', '')}".lower()

        # 1) Custom names first
        for keys, label, kind in self._name_overrides:
            if any(k in blob for k in keys):
                return (kind, GLib.markup_escape_text(label))   # labels are markup

        # 2) HDMI / DisplayPort (display or TV audio)
        if "hdmi" in name.lower() or "hdmi" in port or "iec958" in name.lower() and "hdmi" in desc.lower():
            return ("display", GLib.markup_escape_text(_("External Display (%s)") % "HDMI"))

        # 3) Built-in card (pci): with headphones plugged in, "Headphones" as on macOS
        if bus == "pci" or "alsa_output.pci" in name or "alsa_input.pci" in name:
            if is_sink and "headphone" in port:
                return ("headphone", GLib.markup_escape_text(_("Headphones")))
            if not is_sink and ("headset" in port or "headphone" in port):
                return ("headphone", GLib.markup_escape_text(_("Headphone Microphone")))
            return ("internal", GLib.markup_escape_text(self.INTERNAL_NAME))

        # 4) Bluetooth / USB / other: the device's own name without technical suffixes
        clean = self._SUFFIXES.sub("", desc).strip() or desc
        # A USB card that also has a microphone is a headset (Audeze Maxwell, HyperX,
        # SteelSeries wireless receivers...; most only call themselves a sound card).
        # Same rule for inputs: a headset's microphone gets the headphones glyph, so it
        # stands apart from the built-in microphone.
        is_usb_headset = bus == "usb" and props.get("device.name") in mic_cards
        if any(k in blob for k in ("earbud", "buds", "pods")):
            kind = "earbuds"      # in-ear (Galaxy Buds, AirPods...)
        elif is_usb_headset or form in ("headphone", "headset", "hands-free", "handset") or bus == "bluetooth" \
                or any(k in blob for k in ("headphone", "headset")):
            kind = "headphone"
        elif form in ("speaker", "car", "hifi", "portable"):
            kind = "speaker"
        else:
            kind = "speaker" if is_sink else "microphone"
        return (kind, GLib.markup_escape_text(clean))

    @staticmethod
    def _bluetooth_battery(dev):
        """Battery percentage of a Bluetooth device (org.bluez.Battery1 in BlueZ), or None.
        (Headsets with a USB receiver report their battery to their own apps, not BlueZ.)"""
        props = dev.get("properties", {}) or {}
        if (props.get("device.bus") or "").lower() != "bluetooth" and not dev.get("name", "").startswith("bluez_"):
            return None
        mac = (props.get("api.bluez5.address") or props.get("device.string") or "").upper()
        if not re.fullmatch(r"([0-9A-F]{2}:){5}[0-9A-F]{2}", mac):
            m = re.search(r"([0-9A-F]{2}_){5}[0-9A-F]{2}", dev.get("name", "").upper())
            if not m:
                return None
            mac = m.group(0).replace("_", ":")
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            res = bus.call_sync("org.bluez", "/", "org.freedesktop.DBus.ObjectManager",
                                "GetManagedObjects", None, None, Gio.DBusCallFlags.NONE, 500, None)
            for path, ifaces in res.unpack()[0].items():
                d = ifaces.get("org.bluez.Device1")
                if d and str(d.get("Address", "")).upper() == mac:
                    b = ifaces.get("org.bluez.Battery1")
                    return int(b["Percentage"]) if b and "Percentage" in b else None
        except Exception:
            pass
        return None

    def _parse_pipewire_hardware(self, is_sink=True):
        pool = []
        try:
            info = self._pactl_json("info")
            default = info.get("default_sink_name" if is_sink else "default_source_name", "")
            devices = self._pactl_json("list", "sinks" if is_sink else "sources")
            # Cards on the other side: for outputs, cards with a microphone; for inputs, cards
            # with an output. A USB card with both is a headset.
            mic_cards = set()
            for other in self._pactl_json("list", "sources" if is_sink else "sinks"):
                if not other.get("name", "").endswith(".monitor"):
                    mic_cards.add((other.get("properties") or {}).get("device.name"))
            mic_cards.discard(None)
            if is_sink:
                self._bt_battery = {}
            for dev in devices:
                name = dev.get("name", "")
                if is_sink:
                    pct = self._bluetooth_battery(dev)
                    if pct is not None:
                        self._bt_battery[name] = pct
                if not is_sink and (name.endswith(".monitor") or dev.get("monitor_of_sink") not in (None, "", "n/a")):
                    continue   # monitor sources aren't real microphones
                kind, label = self._classify(dev, is_sink, mic_cards)
                # Only the default device is marked active
                pool.append((kind, name, label, name == default, "card_level"))
        except Exception as ex:
            print(f"Could not list audio devices: {ex}")

        if not pool:
            pool.append(("internal", "default", GLib.markup_escape_text(self.INTERNAL_NAME), True, "card_level"))
        return pool

    def _watch_audio_events(self):
        """pactl subscribe reports devices being added/removed and default changes.
        PR_SET_PDEATHSIG makes pactl exit together with the Control Center."""
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
                if (("'new'" in l or "'remove'" in l) and (" sink " in l or " source " in l or " card " in l)) \
                        or ("'change'" in l and (" server" in l or " card " in l)):
                    GLib.idle_add(self._schedule_refresh)
        except Exception as e:
            print(f"Could not listen for audio device changes: {e}")

    def _schedule_refresh(self):
        # Plugging a device in sends several events; refresh once, 300 ms later
        if self._refresh_pending:
            GLib.source_remove(self._refresh_pending)
        def run():
            self._refresh_pending = None
            self.refresh_audio_devices(force=False)
            return False
        self._refresh_pending = GLib.timeout_add(300, run)
        return False

    def _on_hardware_selected(self, event, dev_type, sys_name, extra_id, is_sink):
        if event.button == 1:
            try:
                cmd = "set-default-sink" if is_sink else "set-default-source"
                subprocess.run(["pactl", cmd, sys_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
                # Move playing streams to the new device. "short" output is the same in
                # every language; the first column is the stream id.
                inputs_out = subprocess.check_output(["pactl", "list", "short", "sink-inputs" if is_sink else "source-outputs"], text=True, stderr=subprocess.DEVNULL)
                input_ids = [l.split()[0] for l in inputs_out.splitlines() if l.strip()]
                for inp_id in input_ids:
                    move_cmd = "move-sink-input" if is_sink else "move-source-output"
                    subprocess.run(["pactl", move_cmd, inp_id, sys_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as ex:
                print(f"Could not switch the audio device: {ex}")
                
            GLib.timeout_add(150, lambda: self.refresh_audio_devices() or False)
            return True
        return False

    def _on_open_pavucontrol(self, widget, event):
        if event.button == 1:
            spawn(["pavucontrol"])
            app = Gtk.Application.get_default()
            if app and hasattr(app, 'window') and app.window: app.window.hide()
            return True
        return False

    def _go_back(self):
        if self.app and hasattr(self.app, 'view_stack') and self.app.view_stack:
            self.app.view_stack.set_visible_child_name("main_view")
            if hasattr(self.app, '_force_single_resize'):
                GLib.idle_add(self.app._force_single_resize)