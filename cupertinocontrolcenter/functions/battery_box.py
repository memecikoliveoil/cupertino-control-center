import gi
import subprocess
import os
import threading
import glob
import re
import psutil

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gdk, Gio, GdkPixbuf, Pango
from cupertinocontrolcenter.paths import STATE_DIR
from cupertinocontrolcenter.i18n import _

os.makedirs(STATE_DIR, exist_ok=True)
LOW_POWER_FILE = os.path.join(STATE_DIR, "low_power_mode")   # Low Power Mode state + settings to restore

# "Using Significant Energy" list
ENERGY_TICK = 3        # s between scans (only while the Battery view is open)
ENERGY_CPU = 15.0      # % CPU of all of an app's processes together (one core = 100%)
ENERGY_TICKS = 3       # an app is listed after this many scans in a row above the threshold (~9 s)
ENERGY_MAX_APPS = 3

# Names and icons for apps whose process names aren't helpful:
# (text in the process name, shown name, icon). Order matters: the first match wins.
# Apps not listed here get their name and icon from their .desktop file.
APP_MAP = (
    ("zapzap", "ZapZap", "zapzap"),
    ("qtwebengineproc", "ZapZap", "zapzap"),
    ("librewolf", "LibreWolf", "librewolf"),
    ("brave", "Brave Browser", "brave-browser"),
    ("chrome", "Google Chrome", "google-chrome"),
    ("firefox", "Firefox", "firefox"),
    ("discord", "Discord", "discord"),
    ("taskmanager", _("Task Manager"), "task-manager"),
    ("lollypop", _("Music"), "music-app"),
)
# Parts of the system, not apps: never listed (exact name)
IGNORED_PROCS = {
    "xorg", "xwayland", "systemd", "pulseaudio", "pipewire", "pipewire-pulse", "wireplumber",
    "xfwm4", "xfce4-panel", "xfdesktop", "xfsettingsd", "xfce4-session", "picom", "plank",
    "upowerd", "networkmanager", "bluetoothd", "dbus-daemon", "gammastep", "ps", "top", "htop",
}
# Firefox/LibreWolf content processes: named after their role; resolved through the parent
HELPER_PROCS = ("web content", "isolated web co", "webextensions", "rdd process",
                "socket process", "utility process", "privileged cont", "file:// content")

STATE_CHARGING, STATE_DISCHARGING, STATE_EMPTY, STATE_FULL, STATE_PENDING_CHARGE, STATE_PENDING_DISCHARGE = 1, 2, 3, 4, 5, 6


# ─────────────────────────────────────────────────────────────
# Battery state from UPower over D-Bus, updated by its change signals. The bottom-bar capsule
# and this view listen to the same object.
# ─────────────────────────────────────────────────────────────

class BatteryModel:
    def __init__(self):
        self.percent = None
        self.on_battery = True
        self.state = 0
        self.time_to_empty = 0
        self.time_to_full = 0
        self._listeners = []
        self._dev = None
        self._root = None
        try:
            self._root = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE, None,
                "org.freedesktop.UPower", "/org/freedesktop/UPower", "org.freedesktop.UPower", None)
            # DisplayDevice: UPower's combined battery for UIs (also used by the panel icon)
            self._dev = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE, None,
                "org.freedesktop.UPower", "/org/freedesktop/UPower/devices/DisplayDevice",
                "org.freedesktop.UPower.Device", None)
            self._dev.connect("g-properties-changed", lambda *a: self._read())
            self._root.connect("g-properties-changed", lambda *a: self._read())
        except Exception as e:
            print(f"UPower not available over D-Bus, using psutil: {e}")
            self._dev = None
            GLib.timeout_add_seconds(10, self._read)
        self._read()

    @staticmethod
    def _prop(proxy, name, default):
        try:
            v = proxy.get_cached_property(name)
            return v.unpack() if v is not None else default
        except Exception:
            return default

    def _read(self):
        if self._dev is not None:
            self.percent = int(round(self._prop(self._dev, "Percentage", 0)))
            self.state = int(self._prop(self._dev, "State", 0))
            self.time_to_empty = int(self._prop(self._dev, "TimeToEmpty", 0))
            self.time_to_full = int(self._prop(self._dev, "TimeToFull", 0))
            self.on_battery = bool(self._prop(self._root, "OnBattery", True)) if self._root else \
                self.state in (STATE_DISCHARGING, STATE_EMPTY, STATE_PENDING_DISCHARGE)
        else:
            try:
                b = psutil.sensors_battery()
                if b:
                    self.percent = int(round(b.percent))
                    self.on_battery = not b.power_plugged
                    self.state = STATE_DISCHARGING if self.on_battery else (
                        STATE_FULL if self.percent >= 99 else STATE_CHARGING)
                    self.time_to_empty = b.secsleft if (self.on_battery and b.secsleft and b.secsleft > 0) else 0
                    self.time_to_full = 0
            except Exception:
                pass
        for cb in list(self._listeners):
            try:
                cb(self)
            except Exception as e:
                print(f"Battery listener error: {e}")
        return True   # keep polling in psutil mode

    @property
    def charging(self):
        return self.state == STATE_CHARGING

    def add_listener(self, cb):
        self._listeners.append(cb)
        cb(self)


_MODEL = None


def get_battery():
    global _MODEL
    if _MODEL is None:
        _MODEL = BatteryModel()
    return _MODEL


def _fmt_duration(secs):
    m = max(1, int(round(secs / 60)))
    h, m = divmod(m, 60)
    if h and m:
        return _("%d h %d min") % (h, m)
    return _("%d h") % h if h else _("%d min") % m


# ─────────────────────────────────────────────────────────────
# Low Power Mode and Airplane Mode
# ─────────────────────────────────────────────────────────────

def _read_file(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return None


def _write_sys(path, value):
    """Writing to /sys needs root; returns False without permission."""
    try:
        with open(path, "w") as f:
            f.write(value)
        return True
    except Exception:
        return False


def _rfkill_states():
    """From /sys/class/rfkill (no root needed): {'wlan': [soft_blocked...], 'bluetooth': [...]}"""
    out = {"wlan": [], "bluetooth": []}
    for dev in glob.glob("/sys/class/rfkill/rfkill*"):
        t = _read_file(os.path.join(dev, "type"))
        if t in out:
            out[t].append(_read_file(os.path.join(dev, "soft")) == "1")
    return out


def airplane_is_on():
    st = _rfkill_states()
    radios = st["wlan"] + st["bluetooth"]
    return bool(radios) and all(radios)


def _load_lp():
    raw = _read_file(LOW_POWER_FILE)
    if not raw:
        return {"on": False}
    d = {}
    for line in raw.splitlines():
        k, _, v = line.partition("=")
        d[k] = v
    d["on"] = d.get("on") == "1"
    return d


def _save_lp(d):
    try:
        with open(LOW_POWER_FILE, "w") as f:
            for k, v in d.items():
                f.write(f"{k}={'1' if v is True else '0' if v is False else v}\n")
    except Exception as e:
        print(f"Could not save the Low Power Mode state: {e}")


TURBO_FILE = "/sys/devices/system/cpu/intel_pstate/no_turbo"
GOV_GLOB = "/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor"
XF_BLANK = ["xfconf-query", "-c", "xfce4-power-manager", "-p", "/xfce4-power-manager/blank-on-battery"]


CPU_HELPER = "/usr/local/libexec/cupertino-cpu-policy"


def _tlp_governor():
    """The governor TLP wants for the current power source (/etc/tlp.conf, /etc/tlp.d/*.conf).
    Only used without the helper; the helper does the same itself."""
    on_ac = False
    for ps in glob.glob("/sys/class/power_supply/*"):
        if _read_file(os.path.join(ps, "type")) == "Mains" and _read_file(os.path.join(ps, "online")) == "1":
            on_ac = True
    key = "CPU_SCALING_GOVERNOR_ON_AC" if on_ac else "CPU_SCALING_GOVERNOR_ON_BAT"
    value = None
    for path in ["/etc/tlp.conf"] + sorted(glob.glob("/etc/tlp.d/*.conf")):   # later files override earlier ones
        for line in (_read_file(path) or "").splitlines():
            line = line.strip()
            if line.startswith(key + "="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
    return value


def apply_cpu_policy(low_power, airplane):
    """Applies the CPU policy for both modes together:
    Low Power or Airplane Mode on: powersave, turbo off. Both off: whatever TLP wants.

    The safe way: a small root-owned helper (/usr/local/libexec/cupertino-cpu-policy) run
    with pkexec. Its polkit policy lets the active local user run it without a password; it
    only accepts 'lowpower' and 'normal' (see system/ and the README).
    Without the helper, it tries writing the files directly (fails without permission)."""
    mode = "lowpower" if (low_power or airplane) else "normal"
    if os.path.exists(CPU_HELPER):
        try:
            r = subprocess.run(["pkexec", CPU_HELPER, mode], capture_output=True, text=True, timeout=15)
            if r.returncode != 0:
                print(f"The CPU helper failed ({r.returncode}): {r.stderr.strip()}")
        except Exception as e:
            print(f"Could not run the CPU helper: {e}")
        return

    if mode == "lowpower":
        gov, no_turbo = "powersave", "1"
    else:
        avail = (_read_file("/sys/devices/system/cpu/cpu0/cpufreq/scaling_available_governors") or "").split()
        gov = _tlp_governor()
        if not gov or (avail and gov not in avail):
            gov = "schedutil" if "schedutil" in avail else ("powersave" if "powersave" in avail else None)
        no_turbo = "0"
    ok = _write_sys(TURBO_FILE, no_turbo) if os.path.exists(TURBO_FILE) else True
    if gov:
        for g in glob.glob(GOV_GLOB):
            ok = _write_sys(g, gov) and ok
    if not ok:
        print(f"Could not set the CPU policy (governor={gov}, no_turbo={no_turbo}): "
              f"no permission and {CPU_HELPER} is not installed.")


def set_low_power(enable):
    """Turning on saves the brightness and display dimming settings; turning off restores
    them. The CPU part is apply_cpu_policy."""
    if enable:
        prev = {"on": True}
        try:
            prev["brightness"] = subprocess.run(["brightnessctl", "get"], capture_output=True,
                                                text=True, timeout=2).stdout.strip()
        except Exception:
            pass
        try:
            prev["blank"] = subprocess.run(XF_BLANK, capture_output=True, text=True, timeout=2).stdout.strip()
        except Exception:
            pass
        _save_lp(prev)

        subprocess.run(["brightnessctl", "set", "20%"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(XF_BLANK + ["-s", "1"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        prev = _load_lp()
        _save_lp({"on": False})
        if prev.get("brightness", "").isdigit():
            subprocess.run(["brightnessctl", "set", prev["brightness"]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if prev.get("blank", "").isdigit():
            subprocess.run(XF_BLANK + ["-s", prev["blank"]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    apply_cpu_policy(enable, airplane_is_on())


# ─────────────────────────────────────────────────────────────
# Apps using significant energy
# ─────────────────────────────────────────────────────────────

_LAUNCHERS = {"flatpak", "env", "sh", "bash", "python", "python3", "snap", "gtk-launch", "exo-open", "xdg-open"}
_DESKTOP_INDEX = None


def _desktop_index():
    """{key: (name, icon)} from the installed apps' .desktop entries. Keys: app id (e.g.
    com.rtosta.zapzap and its last part, zapzap), StartupWMClass, executable name."""
    global _DESKTOP_INDEX
    if _DESKTOP_INDEX is not None:
        return _DESKTOP_INDEX
    idx = {}
    try:
        infos = [i for i in Gio.AppInfo.get_all() if isinstance(i, Gio.DesktopAppInfo)]
    except Exception:
        infos = []
    infos.sort(key=lambda i: not i.should_show())   # apps shown in menus first
    for info in infos:
        name = info.get_display_name() or info.get_name()
        icon = info.get_icon()
        if not name or icon is None:
            continue
        app_id = (info.get_id() or "").lower()
        if app_id.endswith(".desktop"):
            app_id = app_id[:-8]
        keys = [app_id, app_id.split(".")[-1]]
        wm = info.get_startup_wm_class()
        if wm:
            keys.append(wm.lower())
        exe = os.path.basename(info.get_executable() or "").lower()
        if exe and exe not in _LAUNCHERS:
            keys.append(exe)
        for k in keys:
            if k:
                idx.setdefault(k, (name, icon))
    _DESKTOP_INDEX = idx
    return idx


def _flatpak_id(pid):
    """Flatpak app id of a process (its name is often useless, e.g. QtWebEngineProcess)."""
    try:
        with open(f"/proc/{pid}/cgroup") as f:
            m = re.search(r"app-flatpak-([A-Za-z0-9_.\-]+?)-\d+\.scope", f.read())
        return m.group(1).lower() if m else None
    except Exception:
        return None


class EnergyWatcher:
    """Current CPU use per process with psutil (between two measurements; 'ps pcpu' would
    give the average since the process started)."""

    def __init__(self):
        self._procs = {}
        self._streak = {}
        self._own = os.getpid()
        self._uid = os.getuid()

    def _app_for(self, p):
        try:
            name = (p.name() or "").lower()
        except Exception:
            return None
        if name in IGNORED_PROCS or p.pid == self._own:
            return None
        # Only the user's apps, as on macOS: no root/system services (packagekitd, udisksd...)
        try:
            if p.uids().real != self._uid:
                return None
        except Exception:
            return None
        # Wrapper scripts: "baobab" runs "/usr/bin/baobab.real"; use the name without ".real"
        if name.endswith(".real"):
            name = name[:-5]
        cand = [name]
        if name.startswith("python"):
            try:
                cmd = p.cmdline()
                # "python3 /usr/bin/lollypop" -> lollypop; without a script ("python3 -c ...") "python"
                script = None
                args = cmd[1:]
                for i, c in enumerate(args):
                    if c == "-c":
                        break                       # inline code: no script name
                    if c == "-m":
                        script = args[i + 1] if i + 1 < len(args) else None
                        break
                    if not c.startswith("-"):
                        script = c
                        break
                cand = [os.path.basename(script).lower().removesuffix(".py")] if script else ["python"]
            except Exception:
                return None
            if cand[0] == "main":   # the Control Center itself
                return None
        if name.startswith(HELPER_PROCS):
            try:
                par = p.parent()
                cand = [(par.name() or "").lower()] if par else cand
            except Exception:
                pass
        idx = _desktop_index()
        fid = _flatpak_id(p.pid)
        if fid:
            cand = [fid, fid.split(".")[-1]] + cand
        for c in cand:
            for key, label, icon in APP_MAP:
                if key in c:
                    # Name from APP_MAP; icon from the installed app if there is one
                    found = idx.get(c) or idx.get(key)
                    return label, (found[1] if found else icon)
            if c in idx:
                return idx[c]
        c = cand[0]
        if not c or c.startswith(HELPER_PROCS) or c in _LAUNCHERS:
            return None
        return c.capitalize(), c

    def sample(self):
        """[(name, icon)] of apps above the threshold for ENERGY_TICKS scans, highest first."""
        totals = {}
        alive = set()
        for p in psutil.process_iter():
            pid = p.pid
            alive.add(pid)
            proc = self._procs.get(pid)
            if proc is None:
                proc = self._procs[pid] = p
                try:
                    proc.cpu_percent(None)   # the first reading is 0; meaningful from the next scan
                except Exception:
                    pass
                continue
            try:
                cpu = proc.cpu_percent(None)
            except Exception:
                continue
            if cpu < 1.0:
                continue
            app = self._app_for(proc)
            if app:
                totals[app] = totals.get(app, 0.0) + cpu
        for pid in list(self._procs):
            if pid not in alive:
                del self._procs[pid]

        hot = {a for a, c in totals.items() if c >= ENERGY_CPU}
        self._streak = {a: self._streak.get(a, 0) + 1 for a in hot}
        result = [a for a in sorted(hot, key=lambda a: -totals[a]) if self._streak[a] >= ENERGY_TICKS]
        return result[:ENERGY_MAX_APPS]

    def reset(self):
        self._streak.clear()


# ─────────────────────────────────────────────────────────────
# Battery view (opened from the capsule in the bottom bar)
# ─────────────────────────────────────────────────────────────

_ROUND_CACHE = {}


class BatteryBox(Gtk.Box):
    def __init__(self, airplane_instance, app_instance):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)

        self.airplane = airplane_instance
        self.app = app_instance
        self.lp_active = _load_lp()["on"]
        self.ap_active = airplane_is_on()
        self._energy = EnergyWatcher()
        self._energy_apps = []
        self._energy_timer = None
        self._energy_busy = False
        self._model = None

        # Same margins as the AirDrop, Wi-Fi and Bluetooth views
        self.set_margin_start(13); self.set_margin_end(13)   # content and separators 15 px from the window edge
        self.set_margin_top(14); self.set_margin_bottom(7)

        # --- Header: "Battery" with the percentage on the right ---
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        title_eb = Gtk.EventBox()
        title_eb.set_visible_window(False)
        title_lbl = Gtk.Label()
        title_lbl.set_markup("<span weight='bold' size='medium'>%s</span>"
                             % GLib.markup_escape_text(_("Battery")))
        title_lbl.set_halign(Gtk.Align.START)
        title_lbl.set_margin_start(2)
        title_eb.add(title_lbl)
        title_eb.connect("button-press-event", lambda w, e: self._go_back() if e.button == 1 else False)

        self.percent_lbl = Gtk.Label()
        self.percent_lbl.set_halign(Gtk.Align.END)
        self.percent_lbl.set_margin_end(1)

        header_box.pack_start(title_eb, True, True, 0)
        header_box.pack_end(self.percent_lbl, False, False, 0)
        self.pack_start(header_box, False, False, 0)

        # --- Power source and time remaining (grey secondary text, .mac-secondary-text) ---
        self.power_source_lbl = Gtk.Label(xalign=0)
        self.time_status_lbl = Gtk.Label(xalign=0)
        self.time_status_lbl.set_no_show_all(True)
        for lbl in (self.power_source_lbl, self.time_status_lbl):
            lbl.get_style_context().add_class("mac-secondary-text")
            lbl.set_margin_start(2)
            self.pack_start(lbl, False, False, 0)
        self.power_source_lbl.set_margin_top(2)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.set_margin_top(2); sep.set_margin_bottom(0)
        sep.set_margin_start(2); sep.set_margin_end(2)   # 16 px from the window edge
        self.pack_start(sep, False, False, 0)

        # --- List: rows are built once, then only their contents change (rebuilding every
        # few seconds makes the hover flicker and can swallow clicks) ---
        scroll_window = Gtk.ScrolledWindow()
        scroll_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll_window.set_shadow_type(Gtk.ShadowType.NONE)
        scroll_window.set_size_request(260, -1)
        scroll_window.set_propagate_natural_height(True)
        scroll_window.set_max_content_height(450)

        self.list_box = Gtk.ListBox()
        self.list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.list_box.get_style_context().add_class("transparent-listbox")
        scroll_window.add(self.list_box)
        self.pack_start(scroll_window, True, True, 0)

        self._add_title_row(_("Power Modes"))
        self.lp_icon, self.lp_check = self._add_toggle_row(_("Low Power Mode"), self._on_lp_toggle, first=True)
        self.ap_icon, self.ap_check = self._add_toggle_row(_("Airplane Mode"), self._on_ap_toggle)
        self._add_separator_row()
        # The energy section starts here; only this part is rebuilt when it changes
        self._energy_rows = []
        self._energy_anchor = len(self.list_box.get_children())
        self._add_separator_row(tight=True)
        self._add_settings_row(_("Battery Settings…"), "xfce4-power-manager-settings")

        self._render_modes()
        self._render_energy([])

        self.connect("map", self._on_map)
        self.connect("unmap", self._on_unmap)
        get_battery().add_listener(self._on_battery)

    # ── Row builders (same measurements as Wi-Fi / Bluetooth / AirDrop) ──

    def _add_title_row(self, text, secondary=False, at=None):
        row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        row_box.set_margin_top(2); row_box.set_margin_bottom(2)
        lbl = Gtk.Label(xalign=0)
        if secondary:
            # "No Apps Using Significant Energy": grey info text, not a header
            lbl.set_text(text)
            lbl.get_style_context().add_class("mac-secondary-text")
        else:
            lbl.set_text(text)
            lbl.get_style_context().add_class("mac-section-title")   # font in CSS
        lbl.set_margin_start(1)
        row_box.pack_start(lbl, True, True, 0)
        row = Gtk.ListBoxRow()
        # Headers are insensitive as in Wi-Fi; the info text stays sensitive (the theme would
        # dim it twice)
        row.set_sensitive(secondary); row.set_can_focus(False); row.set_selectable(False); row.set_activatable(False)
        row.get_style_context().add_class("mac-category-title-row")
        row.add(row_box)
        self._insert(row, at)
        return row

    def _add_toggle_row(self, text, handler, first=False):
        row = Gtk.ListBoxRow()
        row.get_style_context().add_class("mac-wifi-row")
        row.get_style_context().add_class("mac-airdrop-row")   # checkmark on the right, as in AirDrop
        row.set_margin_start(0); row.set_margin_end(0)
        row.set_margin_top(0 if first else 2); row.set_margin_bottom(0)
        eb = Gtk.EventBox(); eb.set_visible_window(False)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        box.set_margin_top(2); box.set_margin_bottom(2)
        icon = Gtk.Image()
        lbl = Gtk.Label(label=text, xalign=0)
        lbl.set_valign(Gtk.Align.CENTER)
        check = Gtk.Image.new_from_icon_name("object-select-symbolic", Gtk.IconSize.MENU)
        check.set_no_show_all(True)
        box.pack_start(icon, False, False, 0)
        box.pack_start(lbl, True, True, 0)
        box.pack_end(check, False, False, 0)
        eb.add(box)
        eb.connect("button-press-event", handler)
        row.add(eb)
        self.list_box.add(row)
        return icon, check

    def _add_app_row(self, name, icon_name, at, first):
        row = Gtk.ListBoxRow()
        row.get_style_context().add_class("mac-wifi-row")
        row.set_margin_top(0 if first else 2)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_top(2); box.set_margin_bottom(2)
        img = Gtk.Image()
        if isinstance(icon_name, Gio.Icon):
            img.set_from_gicon(icon_name, Gtk.IconSize.MENU)
        else:
            theme = Gtk.IconTheme.get_default()
            img.set_from_icon_name(icon_name if theme.has_icon(icon_name) else "application-x-executable",
                                   Gtk.IconSize.MENU)
        # Small app icon (16) as on macOS, centered in a 24 px slot so the text lines up with
        # the other rows. 16 is a native size, so icons stay sharp.
        img.set_pixel_size(16)
        img.set_size_request(24, -1)
        lbl = Gtk.Label(label=name, xalign=0)
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        box.pack_start(img, False, False, 0)
        box.pack_start(lbl, True, True, 0)
        row.add(box)
        self._insert(row, at)
        return row

    def _add_separator_row(self, tight=False):
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        if tight:
            sep.get_style_context().add_class("tight-bottom")
        row = Gtk.ListBoxRow()
        row.set_sensitive(False); row.set_can_focus(False); row.set_selectable(False)
        row.get_style_context().add_class("styled-separator-row")
        row.add(sep)
        self.list_box.add(row)

    def _add_settings_row(self, text, cmd):
        row = Gtk.ListBoxRow()
        row.get_style_context().add_class("mac-wifi-row")
        row.get_style_context().add_class("mac-bottom-settings-row")
        row.set_margin_start(1); row.set_margin_end(0)   # lines up with the text above
        eb = Gtk.EventBox(); eb.set_visible_window(False)
        lbl = Gtk.Label(label=text, xalign=0)
        lbl.set_margin_top(4); lbl.set_margin_bottom(4)
        eb.add(lbl)
        eb.connect("button-press-event", lambda w, e: self._launch(cmd) if e.button == 1 else False)
        row.add(eb)
        self.list_box.add(row)

    def _insert(self, row, at):
        if at is None:
            self.list_box.add(row)
        else:
            self.list_box.insert(row, at)

    # ── Updates ──

    def _on_battery(self, m):
        if m.percent is None:
            self.percent_lbl.set_text(_("--%"))
            return
        # Normal weight, as in the macOS header
        self.percent_lbl.set_markup("<span size='medium'>%s</span>"
                                    % GLib.markup_escape_text(_("%d%%") % m.percent))
        self.power_source_lbl.set_text(_("Power Source: Battery") if m.on_battery
                                       else _("Power Source: Power Adapter"))
        # macOS hides the time remaining on battery; shown here, like the XFCE power plugin
        if m.on_battery:
            hint = (_("%s remaining") % _fmt_duration(m.time_to_empty) if m.time_to_empty > 0
                    else _("Calculating time remaining…"))
        elif m.state == STATE_FULL or m.percent >= 99:
            hint = _("Battery Is Charged")
        elif m.state == STATE_CHARGING:
            hint = (_("%s until full") % _fmt_duration(m.time_to_full) if m.time_to_full > 0
                    else _("Charging"))
        else:
            hint = _("Not Charging")
        if hint:
            self.time_status_lbl.set_text(hint)
        self.time_status_lbl.set_visible(bool(hint))
        if self.get_mapped() and self.app and hasattr(self.app, "_force_single_resize"):
            GLib.idle_add(self.app._force_single_resize)

    def _set_round_icon(self, img, on, name):
        path = f"icons/{name}.png" if on else f"icons/{name}-d.png"
        try:
            pb = _ROUND_CACHE.get(path)
            if pb is None:
                # Scale the large PNG to 24 in one high-quality step (bilinear blurs the edges)
                pb = _ROUND_CACHE[path] = GdkPixbuf.Pixbuf.new_from_file(path).scale_simple(
                    24, 24, GdkPixbuf.InterpType.HYPER)
            img.set_from_pixbuf(pb)
        except Exception:
            img.set_from_icon_name("airplane-mode-symbolic" if name == "airplane" else "battery-symbolic",
                                   Gtk.IconSize.LARGE_TOOLBAR)

    def _render_modes(self):
        self._set_round_icon(self.lp_icon, self.lp_active, "lowpower")
        self._set_round_icon(self.ap_icon, self.ap_active, "airplane")
        self.lp_check.set_visible(self.lp_active)
        self.ap_check.set_visible(self.ap_active)
        return False

    def _render_energy(self, apps):
        if apps == self._energy_apps and self._energy_rows:
            return False
        self._energy_apps = apps
        for r in self._energy_rows:
            self.list_box.remove(r)
        self._energy_rows = []
        at = self._energy_anchor
        if apps:
            self._energy_rows.append(self._add_title_row(_("Using Significant Energy"), at=at))
            for i, (name, icon) in enumerate(apps):
                self._energy_rows.append(self._add_app_row(name, icon, at + 1 + i, first=(i == 0)))
        else:
            self._energy_rows.append(self._add_title_row(_("No Apps Using Significant Energy"), secondary=True, at=at))
        for r in self._energy_rows:
            r.show_all()
        if self.app and hasattr(self.app, "_force_single_resize") and self.get_mapped():
            GLib.idle_add(self.app._force_single_resize)
        return False

    # ── Work done while visible ──

    def _on_map(self, *a):
        # Airplane Mode may have changed elsewhere (nm-applet, Fn key): re-read on every open
        self.ap_active = airplane_is_on()
        self._render_modes()
        if self._energy_timer is None:
            self._energy_timer = GLib.timeout_add_seconds(ENERGY_TICK, self._energy_tick)
            self._energy_tick()

    def _on_unmap(self, *a):
        if self._energy_timer is not None:
            GLib.source_remove(self._energy_timer)
            self._energy_timer = None

    def _energy_tick(self):
        # macOS shows the list on power too
        if self._energy_busy:
            return True
        self._energy_busy = True

        def work():
            try:
                apps = self._energy.sample()
            except Exception as e:
                print(f"Energy scan error: {e}")
                apps = self._energy_apps
            self._energy_busy = False
            GLib.idle_add(self._render_energy, apps)
        threading.Thread(target=work, daemon=True).start()
        return True

    # ── Clicks ──

    def _go_back(self):
        if self.app and hasattr(self.app, "view_stack"):
            self.app.view_stack.set_visible_child_name("main_view")
        return True

    def _launch(self, cmd):
        try:
            subprocess.Popen([cmd], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            print(f"Could not start {cmd}: {e}")
        if self.app and hasattr(self.app, "close_cc"):
            self.app.close_cc()
        return True

    def _on_lp_toggle(self, widget, event):
        if event.button != 1:
            return False
        self.lp_active = not self.lp_active
        self._render_modes()   # show right away; the hardware part runs in the background
        def work(on):
            set_low_power(on)
            GLib.idle_add(lambda: get_battery()._read() and False)   # battery glyph turns yellow / back
        threading.Thread(target=work, args=(self.lp_active,), daemon=True).start()
        return True

    def _on_ap_toggle(self, widget, event):
        if event.button != 1:
            return False
        target_state = not self.ap_active
        self.ap_active = target_state
        if self.airplane is not None:
            try:
                self.airplane.state = target_state
            except Exception:
                pass
        self._render_modes()
        airplane = self.airplane

        def hardware_worker():
            import time
            try:
                if target_state:
                    res_wifi = subprocess.run(["nmcli", "radio", "wifi"], capture_output=True, text=True, timeout=2)
                    st = _rfkill_states()
                    if airplane is not None:
                        airplane.wifi_was_enabled = "enabled" in res_wifi.stdout.lower() or "açık" in res_wifi.stdout.lower()
                        airplane.bluetooth_was_enabled = any(not b for b in st["bluetooth"])

                    subprocess.run(["rfkill", "block", "all"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    apply_cpu_policy(_load_lp()["on"], True)
                else:
                    subprocess.run(["rfkill", "unblock", "all"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    apply_cpu_policy(_load_lp()["on"], False)
                    wifi_on = getattr(airplane, "wifi_was_enabled", True)
                    bt_on = getattr(airplane, "bluetooth_was_enabled", True)
                    subprocess.Popen(["nmcli", "radio", "wifi", "on" if wifi_on else "off"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    subprocess.Popen(["rfkill", "unblock" if bt_on else "block", "bluetooth"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as hardware_ex:
                print(f"Airplane Mode error: {hardware_ex}")
            # Read the real state, so the checkmark is right even if a command failed
            time.sleep(1.0)
            real = airplane_is_on()

            def sync():
                if real != self.ap_active:
                    self.ap_active = real
                    self._render_modes()
                return False
            GLib.idle_add(sync)

        threading.Thread(target=hardware_worker, daemon=True).start()
        return True
