#!/usr/bin/env python3
"""Music Recognition (like the Shazam button in the macOS Control Center).

Two parts:
- Background worker: run directly (python3 music_recognition.py --listen), it listens to
  what the computer is playing with SongRec, adds the song to the history and shows a
  notification. It is a separate process, so it keeps listening after the Control Center
  closes, as on macOS.
- UI: the wide capsule in the bottom bar (MusicCapsule) and the history view (MusicView).
  Both watch a small state file written by the worker.
"""
import gi
import os
import sys
import json
import time
import math
import shutil
import signal
import subprocess
import urllib.request
import urllib.parse
import shlex
import glob

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gdk, Gio, GdkPixbuf, Pango

if __name__ == "__main__":   # worker started as a script: make the package importable
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from cupertinocontrolcenter.paths import STATE_DIR, CACHE_DIR
from cupertinocontrolcenter.i18n import _, ngettext

SONGREC_FLATPAK = "re.fossplant.songrec"
LISTEN_TIMEOUT = 25          # s; "not found" after this (one request records ~12 s of audio)
RESULT_SHOW_SECS = 8         # how long a result stays in the capsule
NOT_FOUND_SHOW_SECS = 4
HISTORY_MAX = 100
VIEW_MAX_ROWS = 20
COVER_PX = 49           # same cover size as the media tile (49, radius 3)
COVER_RADIUS = 3

CONFIG_DIR = STATE_DIR
HISTORY_PATH = os.path.join(CONFIG_DIR, "music_history.json")
COVER_DIR = os.path.join(CACHE_DIR, "music-covers")
STATE_PATH = os.path.join(GLib.get_user_runtime_dir(), "cupertino-music-recognition.json")


# ─────────────────────────────────────────────────────────────
# Shared: state and history files
# ─────────────────────────────────────────────────────────────
def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)        # never leave a half-written file


def _read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def read_state():
    st = _read_json(STATE_PATH, {"state": "idle"})
    # Don't stay stuck on "listening" if the worker died
    if st.get("state") == "listening":
        pid = st.get("pid", 0)
        alive = False
        try:
            os.kill(pid, 0)
            alive = True
        except Exception:
            pass
        if not alive or time.time() - st.get("time", 0) > LISTEN_TIMEOUT + 15:
            st = {"state": "idle"}
    return st


def load_history():
    h = _read_json(HISTORY_PATH, [])
    return h if isinstance(h, list) else []


def clear_history():
    _write_json(HISTORY_PATH, [])


def is_listening():
    return read_state().get("state") == "listening"


def start_listening():
    if is_listening():
        return
    try:
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "--listen"],
                         start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"Could not start Music Recognition: {e}")


def stop_listening():
    st = read_state()
    if st.get("state") == "listening":
        try:
            # The worker has its own session; killing the group stops SongRec too
            os.killpg(st.get("pid", 0), signal.SIGTERM)
        except Exception:
            pass
    _write_json(STATE_PATH, {"state": "idle", "time": time.time()})


def open_url(url):
    if url:
        subprocess.Popen(["xdg-open", url], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _ytm_pwa_command():
    """Launch command of an installed YouTube Music web app (from its .desktop file), or None.
    Chromium-based browsers open web apps with '--app-id=...'."""
    for path in glob.glob(os.path.expanduser("~/.local/share/applications/*.desktop")):
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except Exception:
            continue
        name = exec_line = None
        for line in text.splitlines():
            if line.startswith("Name=") and name is None:
                name = line[5:].strip()
            elif line.startswith("Exec=") and exec_line is None:
                exec_line = line[5:].strip()
        if name == "YouTube Music" and exec_line and "--app-id=" in exec_line:
            # drop field codes such as %U
            return [a for a in shlex.split(exec_line) if not (len(a) == 2 and a.startswith("%"))]
    return None


def open_in_youtube_music(query):
    """Searches YouTube Music for the song: in the installed web app if there is one,
    otherwise in the browser."""
    url = "https://music.youtube.com/search?q=" + urllib.parse.quote_plus(query)
    cmd = _ytm_pwa_command()
    if cmd:
        # Chromium option that opens a web app at a given URL (used by its shortcut menu)
        cmd = cmd + [f"--app-launch-url-for-shortcuts-menu-item={url}"]
        try:
            subprocess.Popen(cmd, start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except Exception:
            pass
    open_url(url)


def _fmt_ago(ts):
    d = time.time() - ts
    if d < 60:
        return _("Now")
    if d < 3600:
        n = int(d // 60)
        return ngettext("%d min ago", "%d min ago", n) % n
    if d < 86400:
        n = int(d // 3600)
        return ngettext("%d hour ago", "%d hours ago", n) % n     # macOS: "21 hours ago"
    # Translators: date format for older entries (strftime)
    return time.strftime(_("%m/%d/%Y"), time.localtime(ts))


# ─────────────────────────────────────────────────────────────
# Background worker
# ─────────────────────────────────────────────────────────────
def _songrec_cmd():
    if shutil.which("songrec"):
        return ["songrec"]
    if shutil.which("flatpak"):
        try:
            r = subprocess.run(["flatpak", "info", SONGREC_FLATPAK],
                               capture_output=True, timeout=5)
            if r.returncode == 0:
                return ["flatpak", "run", f"--command=songrec", SONGREC_FLATPAK]
        except Exception:
            pass
    return None


def _monitor_source():
    """Monitor source of the current default output, i.e. whatever the computer is playing.
    Follows output changes (e.g. headphones plugged in)."""
    try:
        sink = subprocess.run(["pactl", "get-default-sink"], capture_output=True,
                              text=True, timeout=3).stdout.strip()
        if sink:
            return sink + ".monitor"
    except Exception:
        pass
    return None


def _notify(title, body, icon, url=None, query=None):
    # As on macOS: the result shows in the capsule/list and as a notification
    base = ["notify-send", "-a", _("Music Recognition"), "-i", icon or "audio-x-generic", title]
    if body:
        base.append(body)
    if not url:
        subprocess.run(base, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    # Notification with actions: notify-send waits and prints the chosen action
    try:
        # xfce4-notifyd shows "default" as a button too; clicking the notification = Open in Shazam
        actions = ["-A", "default=" + _("Open in Shazam")]
        if query:
            actions += ["-A", "ytm=" + _("Search in YouTube Music")]
        r = subprocess.run(base[:1] + actions + base[1:],
                           capture_output=True, text=True, timeout=600)
        if r.returncode == 0:
            chosen = r.stdout.strip()
            if chosen == "default":
                open_url(url)
            elif chosen == "ytm":
                open_in_youtube_music(query)
            return
    except subprocess.TimeoutExpired:
        return
    except Exception:
        pass
    # Older notify-send without actions: plain notification
    subprocess.run(base, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _save_cover(key, url):
    if not url:
        return ""
    os.makedirs(COVER_DIR, exist_ok=True)
    path = os.path.join(COVER_DIR, f"{key}.jpg")
    if os.path.exists(path):
        return path
    try:
        with urllib.request.urlopen(url, timeout=8) as r, open(path, "wb") as f:
            f.write(r.read())
        return path
    except Exception:
        return ""


def run_worker():
    me = os.getpid()
    proc = None

    def on_term(*a):
        if proc and proc.poll() is None:
            proc.kill()
        sys.exit(0)
    signal.signal(signal.SIGTERM, on_term)

    _write_json(STATE_PATH, {"state": "listening", "pid": me, "time": time.time()})

    cmd = _songrec_cmd()
    if not cmd:
        _write_json(STATE_PATH, {"state": "error", "time": time.time(),
                                 "message": _("SongRec not found")})
        _notify(_("Music Recognition"), _("SongRec is not installed."), "dialog-warning")
        return

    src = _monitor_source()
    args = cmd + ["recognize", "-j"] + (["-d", src] if src else [])
    track = None
    try:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        out, _ = proc.communicate(timeout=LISTEN_TIMEOUT)
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    track = json.loads(line).get("track")
                except Exception:
                    track = None
                if track:
                    break
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    except Exception as e:
        print(f"SongRec error: {e}")

    # Stopped by the user meanwhile (the state file isn't ours any more): exit quietly
    cur = _read_json(STATE_PATH, {})
    if cur.get("pid") != me:
        return

    if not track or not track.get("title"):
        _write_json(STATE_PATH, {"state": "notfound", "time": time.time()})
        _notify(_("No Match Found"), _("No match was found for this song."), "audio-x-generic")
        return

    key = str(track.get("key") or int(time.time()))
    cover_url = (track.get("images") or {}).get("coverart", "")
    entry = {
        "key": key,
        "title": track.get("title", ""),
        "artist": track.get("subtitle", ""),
        "url": track.get("url", ""),
        "cover_url": cover_url,
        "cover": _save_cover(key, cover_url),
        "time": time.time(),
    }
    hist = [h for h in load_history() if h.get("key") != key]   # a repeat moves to the top
    hist.insert(0, entry)
    _write_json(HISTORY_PATH, hist[:HISTORY_MAX])
    _write_json(STATE_PATH, dict(entry, state="found"))
    _notify(entry["title"], entry["artist"], entry["cover"] or "audio-x-generic", entry["url"],
            query=f"{entry['title']} {entry['artist']}".strip())


# ─────────────────────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────────────────────
class _StateWatcher:
    """Reports changes to the state and history files (written by the worker process)."""
    _inst = None

    @classmethod
    def get(cls):
        if cls._inst is None:
            cls._inst = cls()
        return cls._inst

    def __init__(self):
        self._listeners = []
        self._pending = None
        self._mons = []
        for p in (STATE_PATH, HISTORY_PATH):
            try:
                os.makedirs(os.path.dirname(p), exist_ok=True)
                m = Gio.File.new_for_path(p).monitor_file(Gio.FileMonitorFlags.WATCH_MOVES, None)
                m.connect("changed", self._on_changed)
                self._mons.append(m)
            except Exception as e:
                print(f"Could not watch the Music Recognition files: {e}")

    def add(self, cb):
        self._listeners.append(cb)

    def _on_changed(self, *a):
        if self._pending is None:
            self._pending = GLib.timeout_add(80, self._fire)

    def _fire(self):
        self._pending = None
        for cb in self._listeners:
            cb()
        return False


class WaveGlyph(Gtk.DrawingArea):
    """Sound wave glyph (5 rounded bars) that moves while listening.
    Colour from CSS like the battery glyph (.opt-battery-glyph); with round_btn=True it is a
    round button: #808080 when idle, #007AFF while listening, white wave (as the toggles)."""

    BARS = (0.42, 0.78, 1.0, 0.66, 0.36)

    def __init__(self, round_btn=False, size=24):
        super().__init__()
        self.round_btn = round_btn
        self.size = size
        self.animating = False
        self._t0 = 0
        self._timer = None
        if round_btn:
            self.set_size_request(size, size)
        else:
            self.set_size_request(18, 16)
            self.get_style_context().add_class("opt-battery-glyph")
        self.set_valign(Gtk.Align.CENTER)
        self.connect("draw", self._draw)
        self.connect("unmap", lambda *a: self._stop_timer())
        self.connect("map", lambda *a: self.set_animating(self.animating))

    def set_animating(self, on):
        self.animating = on
        if on and self.get_mapped() and self._timer is None:
            self._t0 = time.monotonic()
            self._timer = GLib.timeout_add(50, self._tick)
        elif not on:
            self._stop_timer()
        self.queue_draw()

    def _stop_timer(self):
        if self._timer is not None:
            GLib.source_remove(self._timer)
            self._timer = None

    def _tick(self):
        self.queue_draw()
        return True

    def _draw(self, widget, cr):
        fg = self.get_style_context().get_color(self.get_state_flags())
        W, H = self.get_allocated_width(), self.get_allocated_height()
        if self.round_btn:
            # Blue when on, grey when off, like the Low Power / Airplane Mode buttons
            cr.arc(W / 2, H / 2, self.size / 2, 0, 2 * math.pi)
            if self.animating:
                cr.set_source_rgba(0.0, 122 / 255, 1.0, 1)        # #007AFF
            else:
                cr.set_source_rgba(128 / 255, 128 / 255, 128 / 255, 1)   # #808080
            cr.fill()
            bar_w, gap, max_h = 2, 2, round(self.size / 2)
            col = (1, 1, 1, 1)
        else:
            bar_w, gap, max_h = 2, 2, H
            col = (fg.red, fg.green, fg.blue, 1)
        n = len(self.BARS)
        total = n * bar_w + (n - 1) * gap
        x0 = round((W - total) / 2)
        t = time.monotonic() - self._t0
        cr.set_source_rgba(*col)
        for i, base in enumerate(self.BARS):
            if self.animating:
                k = 0.3 + 0.7 * abs(math.sin(t * 3.2 + i * 0.9))
            else:
                k = base
            h = max(bar_w, round(max_h * k))
            x = x0 + i * (bar_w + gap)
            y = (H - h) / 2
            r = bar_w / 2
            cr.new_sub_path()
            cr.arc(x + r, y + r, r, math.pi, 0)
            cr.arc(x + r, y + h - r, r, 0, math.pi)
            cr.close_path()
            cr.fill()
        return True


class _ShrinkableBox(Gtk.EventBox):
    """Sits in a slot whose width is tied to a neighbour (SizeGroup). Reports a small natural
    width so a long song title doesn't widen the Connections tile above; the text is
    ellipsized in the space it gets."""

    def do_get_preferred_width(self):
        return 40, 40


class MusicCapsule(_ShrinkableBox):
    """The wide capsule in the bottom bar. As on macOS Sonoma: a round icon on the left and
    two lines of text ("Music / Recognition") next to it. While listening the circle turns
    blue and the wave moves; a result shows title / artist.
    Click the circle: start / stop listening. Click the text (or right-click): history."""

    def __init__(self):
        super().__init__()
        self.set_visible_window(True)
        self.get_style_context().add_class("opt-battery-box")
        self.set_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.set_hexpand(True)

        # Spacing 0: spacing would also be added next to the empty centering boxes
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        box.set_margin_top(6); box.set_margin_bottom(6)
        box.set_margin_start(11); box.set_margin_end(8)
        self.glyph = WaveGlyph(round_btn=True)
        # Circle-to-text gap, about half the diameter as on macOS
        self.glyph.set_margin_end(10)
        self.line1 = Gtk.Label(xalign=0)
        self.line2 = Gtk.Label(xalign=0)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        texts.set_valign(Gtk.Align.CENTER)
        for l in (self.line1, self.line2):
            l.get_style_context().add_class("opt-flat-text")
            l.set_ellipsize(Pango.EllipsizeMode.END)
            texts.pack_start(l, False, False, 0)
        self.line2.set_no_show_all(True)
        # Left-aligned as on macOS. The empty box on the right takes the extra space; when
        # the text doesn't fit, that box shrinks first, then the text is ellipsized.
        box.pack_start(self.glyph, False, False, 0)
        box.pack_start(texts, False, True, 0)
        box.pack_start(Gtk.Box(), True, True, 0)
        self.add(box)

        self._shown = None
        self._back_timer = None
        self.connect("button-press-event", self._on_click)
        self.connect("map", lambda *a: self.refresh())
        _StateWatcher.get().add(self.refresh)
        self.refresh()

    def _set_lines(self, first, second=None, animating=False, small=None):
        self.glyph.set_animating(animating)
        esc = GLib.markup_escape_text
        # Same size and weight as the two-line titles of the Keyboard Brightness / Screen
        # Mirroring tiles. Single-line status texts ("No Match") are one step smaller so they
        # fit the ~110 px capsule; "Listening" fits at normal size.
        if small is None:
            small = not second
        size = " size='small'" if small else ""
        self.line1.set_markup(f"<span weight='medium'{size}>{esc(first)}</span>")
        if second:
            self.line2.set_markup(f"<span weight='medium'>{esc(second)}</span>")
            self.line2.show()
        else:
            self.line2.hide()

    def refresh(self):
        st = read_state()
        s = st.get("state", "idle")
        age = time.time() - st.get("time", 0)
        if self._back_timer:
            GLib.source_remove(self._back_timer)
            self._back_timer = None

        if s == "listening":
            self._shown = "listening"
            self._set_lines(_("Listening"), None, animating=True, small=False)
        elif s == "found" and age < RESULT_SHOW_SECS:
            self._shown = "found"
            self._set_lines(st.get("title", ""), st.get("artist", ""))
            self._back_timer = GLib.timeout_add(int((RESULT_SHOW_SECS - age) * 1000) + 50, self._expire)
        elif s in ("notfound", "error") and age < NOT_FOUND_SHOW_SECS:
            self._shown = s
            self._set_lines(_("No Match") if s == "notfound" else st.get("message", _("Error")))
            self._back_timer = GLib.timeout_add(int((NOT_FOUND_SHOW_SECS - age) * 1000) + 50, self._expire)
        else:
            self._shown = "idle"
            # Translators: "Music Recognition" split over two lines
            self._set_lines(_("Music"), _("Recognition"))

    def _expire(self):
        self._back_timer = None
        self.refresh()
        return False

    def _on_click(self, widget, event):
        if event.button == 3:
            _show_history()
            return True
        if event.button != 1:
            return False
        # Circle: listen / stop. The rest of the capsule (text): history.
        # 6 px of slack around the circle, since it's a small target
        gx = self.glyph.translate_coordinates(self, 0, 0)
        on_circle = gx is not None and gx[0] - 6 <= event.x <= gx[0] + self.glyph.size + 6
        if not on_circle:
            _show_history()
        elif self._shown == "listening":
            stop_listening()
        else:
            start_listening()
            # Show it right away, before the worker writes the state file
            self._shown = "listening"
            self._set_lines(_("Listening"), None, animating=True, small=False)
        return True


def _show_history():
    app = Gtk.Application.get_default()
    if app and hasattr(app, "view_stack") and app.view_stack.get_child_by_name("music_view"):
        app.view_stack.set_visible_child_name("music_view")


class _Cover(Gtk.DrawingArea):
    """Small cover with slightly rounded corners (same ratio as in the media tile)."""

    def __init__(self, path):
        super().__init__()
        self.set_size_request(COVER_PX, COVER_PX)
        self.set_valign(Gtk.Align.CENTER)
        self.pb = None
        if path and os.path.exists(path):
            try:
                self.pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, COVER_PX, COVER_PX, False)
            except Exception:
                self.pb = None
        self.connect("draw", self._draw)

    def _draw(self, w, cr):
        s, r = COVER_PX, COVER_RADIUS
        cr.new_sub_path()
        cr.arc(s - r, r, r, -math.pi / 2, 0)
        cr.arc(s - r, s - r, r, 0, math.pi / 2)
        cr.arc(r, s - r, r, math.pi / 2, math.pi)
        cr.arc(r, r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()
        if self.pb:
            cr.clip()
            Gdk.cairo_set_source_pixbuf(cr, self.pb, 0, 0)
            cr.paint()
        else:
            fg = self.get_style_context().get_color(self.get_state_flags())
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.12)
            cr.fill()
        return True


class MusicView(Gtk.Box):
    """History view, like the Music Recognition menu on macOS: a header with the listen
    button, then recently recognized songs (cover, title, artist, time)."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.set_margin_start(13); self.set_margin_end(13)   # content and separators 15 px from the window edge
        self.set_margin_top(14); self.set_margin_bottom(7)

        # Header as on macOS: round icon + "Music Recognition" / "Start Listening".
        # Click the circle: listen / stop. Click the text: back to the main view.
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        head.set_margin_start(2)
        icon_eb = Gtk.EventBox()
        icon_eb.set_visible_window(False)
        # The circle is a drawing area with its own window; an invisible EventBox's input
        # layer sits below it by default and never gets the click, so put it above.
        icon_eb.set_above_child(True)
        self.listen_icon = WaveGlyph(round_btn=True, size=28)
        icon_eb.add(self.listen_icon)
        icon_eb.connect("button-press-event", self._on_listen)
        text_eb = Gtk.EventBox()
        text_eb.set_visible_window(False)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        texts.set_valign(Gtk.Align.CENTER)
        t = Gtk.Label(xalign=0)
        t.set_markup("<span weight='bold' size='medium'>%s</span>"
                     % GLib.markup_escape_text(_("Music Recognition")))
        self.listen_lbl = Gtk.Label(xalign=0)
        self.listen_lbl.get_style_context().add_class("mac-secondary-text")
        texts.pack_start(t, False, False, 0)
        texts.pack_start(self.listen_lbl, False, False, 0)
        text_eb.add(texts)
        text_eb.connect("button-press-event", lambda w, e: self._go_back() if e.button == 1 else False)
        head.pack_start(icon_eb, False, False, 0)
        head.pack_start(text_eb, True, True, 0)
        self.pack_start(head, False, False, 0)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.set_margin_top(8); sep.set_margin_start(2); sep.set_margin_end(2)   # 16 px from the edge
        self.pack_start(sep, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_shadow_type(Gtk.ShadowType.NONE)
        scroll.set_size_request(260, -1)
        scroll.set_propagate_natural_height(True)
        scroll.set_max_content_height(450)
        self.list_box = Gtk.ListBox()
        self.list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.list_box.get_style_context().add_class("transparent-listbox")
        scroll.add(self.list_box)
        self.pack_start(scroll, True, True, 0)

        self._hist_anchor = len(self.list_box.get_children())
        self._hist_rows = []
        # No "Clear History" here (one click, can't be undone); it's in the Settings window,
        # behind a confirmation (macOS doesn't have it in this menu either)

        self._hist_sig = None
        _StateWatcher.get().add(self._refresh)
        self.connect("map", lambda *a: self._refresh())
        self._refresh()

    # ── rows ──
    def _add_separator_row(self, tight=False, at=None, inset=0):
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.set_margin_start(inset)
        if tight:
            sep.get_style_context().add_class("tight-bottom")
        row = Gtk.ListBoxRow()
        row.set_sensitive(False); row.set_can_focus(False); row.set_selectable(False)
        row.get_style_context().add_class("styled-separator-row")
        row.add(sep)
        if at is None:
            self.list_box.add(row)
        else:
            self.list_box.insert(row, at)
        return row

    def _add_settings_row(self, text, handler):
        row = Gtk.ListBoxRow()
        row.get_style_context().add_class("mac-wifi-row")
        row.get_style_context().add_class("mac-bottom-settings-row")
        row.set_margin_start(2)
        eb = Gtk.EventBox(); eb.set_visible_window(False)
        lbl = Gtk.Label(label=text, xalign=0)
        lbl.set_margin_top(4); lbl.set_margin_bottom(4)
        eb.add(lbl)
        eb.connect("button-press-event", lambda w, e: handler() if e.button == 1 else False)
        row.add(eb)
        self.list_box.add(row)
        return row

    def _title_row(self, text, secondary, at):
        lbl = Gtk.Label(xalign=0)
        if secondary:
            # Empty-history text: same style as the grey artist text, with more room
            lbl.set_text(text)
            lbl.get_style_context().add_class("general_desc")
            lbl.set_margin_top(8); lbl.set_margin_bottom(4)
        else:
            lbl.set_text(text)
            lbl.get_style_context().add_class("mac-section-title")   # font in CSS
            lbl.set_margin_top(2); lbl.set_margin_bottom(2)
        lbl.set_margin_start(1)
        row = Gtk.ListBoxRow()
        row.set_sensitive(secondary); row.set_can_focus(False)
        row.set_selectable(False); row.set_activatable(False)
        row.get_style_context().add_class("mac-category-title-row")
        row.add(lbl)
        self.list_box.insert(row, at)
        return row

    def _song_row(self, h, at, first):
        row = Gtk.ListBoxRow()
        row.get_style_context().add_class("mac-wifi-row")
        row.set_margin_top(0 if first else 2)
        eb = Gtk.EventBox(); eb.set_visible_window(False)
        # Same layout as the media tile: 49 px cover, 11 px gap, bold title (.general_title)
        # and the grey artist below (.general_desc)
        hb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=11)
        hb.set_margin_top(4); hb.set_margin_bottom(4)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        texts.set_valign(Gtk.Align.CENTER)
        t = Gtk.Label(label=h.get("title", ""), xalign=0)
        t.set_ellipsize(Pango.EllipsizeMode.END)
        t.get_style_context().add_class("general_title")
        a = Gtk.Label(label=h.get("artist", ""), xalign=0)
        a.set_ellipsize(Pango.EllipsizeMode.END)
        a.get_style_context().add_class("general_desc")
        texts.pack_start(t, False, False, 0)
        texts.pack_start(a, False, False, 0)
        when = Gtk.Label(label=_fmt_ago(h.get("time", 0)))
        when.get_style_context().add_class("general_desc")
        when.set_valign(Gtk.Align.CENTER)
        hb.pack_start(_Cover(h.get("cover", "")), False, False, 0)
        hb.pack_start(texts, True, True, 0)
        hb.pack_end(when, False, False, 0)
        eb.add(hb)
        url = h.get("url", "")
        eb.connect("button-press-event", lambda w, e: self._open(url) if e.button == 1 else False)
        row.add(eb)
        self.list_box.insert(row, at)
        return row

    # ── updating ──
    def _refresh(self):
        listening = is_listening()
        self.listen_icon.set_animating(listening)
        self.listen_lbl.set_text(_("Listening…") if listening else _("Start Listening"))

        hist = load_history()[:VIEW_MAX_ROWS]
        # Include the minute in the signature so "x min ago" texts refresh
        sig = (tuple(h.get("key") for h in hist), int(time.time() // 60))
        if sig == self._hist_sig:
            return
        self._hist_sig = sig
        for r in self._hist_rows:
            self.list_box.remove(r)
        self._hist_rows = []
        at = self._hist_anchor
        if hist:
            # As on macOS: no header, thin lines between songs
            for i, h in enumerate(hist):
                if i:
                    # As on macOS the line starts where the text starts, not under the cover
                    # (cover 49 + gap 11)
                    self._hist_rows.append(self._add_separator_row(at=at, inset=COVER_PX + 11)); at += 1
                self._hist_rows.append(self._song_row(h, at, i == 0)); at += 1
        else:
            self._hist_rows.append(self._title_row(_("No songs recognized yet"), True, at))
        for r in self._hist_rows:
            r.show_all()
        app = Gtk.Application.get_default()
        if self.get_mapped() and app and hasattr(app, "_force_single_resize"):
            GLib.idle_add(app._force_single_resize)

    # ── clicks ──
    def _on_listen(self, w, e):
        if e.button != 1:
            return False
        if is_listening():
            stop_listening()
        else:
            start_listening()
            self.listen_icon.set_animating(True)
            self.listen_lbl.set_text(_("Listening…"))
        return True

    def _open(self, url):
        open_url(url)
        app = Gtk.Application.get_default()
        if app and hasattr(app, "close_cc"):
            app.close_cc()
        return True

    def _go_back(self):
        app = Gtk.Application.get_default()
        if app and hasattr(app, "view_stack"):
            app.view_stack.set_visible_child_name("main_view")
        return True


if __name__ == "__main__":
    if "--listen" in sys.argv:
        run_worker()
