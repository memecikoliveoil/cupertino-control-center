import gi
import subprocess
import threading
import os
import re
import time
import json
import hashlib
import urllib.request
import urllib.parse
from urllib.parse import urlparse, unquote

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gdk, GdkPixbuf, Pango, Gio
from cupertinocontrolcenter.i18n import _
from cupertinocontrolcenter.spawn import spawn
from cupertinocontrolcenter.appinfo import DesktopAppInfo

try:
    from pydbus import SessionBus
    DBUS_SUPPORT = True
except Exception:
    DBUS_SUPPORT = False


NO_MEDIA = _("No Media Playing")
UNKNOWN_ARTIST = _("Unknown Artist")
# With no player: shows "Music" (one line) with a music icon, as on macOS; clicking the cover
# or play starts IDLE_APP (with IDLE_COMMAND as a fallback). Set them to your music app.
IDLE_TITLE = _("Music")
IDLE_APP = "org.gnome.Lollypop.desktop"
IDLE_COMMAND = ["lollypop"]
IDLE_ICONS = ["multimedia-audio-player", "audio-x-generic"]   # the theme's music icon


def launch_idle_player():
    """Starts the music app (by its .desktop file, so distro and Flatpak installs both work)."""
    try:
        info = DesktopAppInfo.new(IDLE_APP)
        if info is not None and info.launch([], None):
            return True
    except Exception:
        pass
    try:
        spawn(IDLE_COMMAND, stdout=DEVNULL, stderr=DEVNULL, start_new_session=True)
        return True
    except Exception as e:
        print(f"Could not start the music app: {e}")
        return False

MPRIS_PREFIX = "org.mpris.MediaPlayer2."
MPRIS_PATH = "/org/mpris/MediaPlayer2"

# MPRIS names that belong to browsers. ("instance" is not a key: Celluloid's name contains it,
# io.github.celluloid_player.Celluloid.instance-1.)
BROWSER_KEYS = ("brave", "librewolf", "firefox", "chromium")
SHORTWAVE_KEYS = ("shortwave", "de.haeckerfelix")

# Chromium app ids of these web apps (the same on every install)
PWA_APP_IDS = {
    "telegram": "majiogicmcnmdhhlgmkahaleckhjbmlk",
    "youtube": "agimnkijcaahngcdmfeangaknmldooml",
    "netflix": "eppojlglocelodeimnohnlnionkobfln",
}
SOURCE_ICONS = {
    "youtube-music": ["youtube-music", "youtube"],
    "youtube": ["youtube"],
    "telegram": ["telegram-desktop", "telegram"],
    "netflix": ["netflix", "netflix-desktop", "multimedia-video-player"],
}
WINDOW_TERMS = {
    "youtube-music": "YouTube Music",
    "youtube": "YouTube",
    "telegram": "Telegram",
    "netflix": "Netflix",
}

DEVNULL = subprocess.DEVNULL


# ─────────────────────────────────────────────────────────────
# Shared helpers (media_view.py imports these too)
# ─────────────────────────────────────────────────────────────

def run_playerctl(*args, timeout=2):
    """Runs playerctl; returns stdout on success, otherwise None."""
    try:
        r = subprocess.run(["playerctl", *args], capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None
    return r.stdout if r.returncode == 0 else None


def is_browser_player(name):
    n = str(name or "").lower()
    return any(k in n for k in BROWSER_KEYS)


def is_gecko_player(name):
    n = str(name or "").lower()
    return "librewolf" in n or "firefox" in n


def is_shortwave_player(name):
    n = str(name or "").lower()
    return any(k in n for k in SHORTWAVE_KEYS)


def detect_web_source(player_name, url=""):
    """Which site a browser player is playing from. None for non-browser players."""
    if not is_browser_player(player_name):
        return None
    u = (url or "").lower()
    if "music.youtube" in u:
        return "youtube-music"
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    if "telegram.org" in u:
        return "telegram"
    if "netflix.com" in u:
        return "netflix"
    if u:
        return None
    # No URL: look at the window list; don't guess if several web apps are open
    try:
        dump = subprocess.run(["wmctrl", "-l", "-x"], capture_output=True,
                              text=True, timeout=2).stdout.lower()
    except Exception:
        return None
    found = [k for k, app_id in PWA_APP_IDS.items() if app_id in dump]
    return found[0] if len(found) == 1 else None


def media_texts(player, title, artist, url=""):
    """(title, second line, search) in one place, so the tile and the view show the same text.
    Without an artist the second line stays empty and the title is centered alone (no
    "Netflix / Netflix" or "Unknown Artist"). Radio titles "Artist - Song" are split."""
    title, artist, search = title or "", artist or "", None
    if is_shortwave_player(player):
        if title:
            title, artist, search = radio_display(title, artist)
        elif artist:
            # The station sends no song info: station name as the title
            title, artist = artist, _("Shortwave Radio")
        else:
            title, artist = _("Stopped Radio Stream"), _("Shortwave Radio")
    if not title and (url or "").startswith("file://"):
        # Players without a title (e.g. a video in Celluloid): the file name without extension
        title = os.path.splitext(os.path.basename(unquote(urlparse(url).path)))[0]
    return title or NO_MEDIA, artist, search


# ── App badge ──────────────────────────────────────────────
# Big Sur shows the playing app's icon at the bottom right of real artwork, only in the
# media view (media_view), not in the small tile. Measured (61 px cover): badge 23 px (~38%),
# overhanging ~3 px right and bottom. Browsers have no real artwork (the site icon is shown
# instead), so they get no badge.
BADGE_OVER = 3
# Fallbacks for players that don't report their .desktop file
BADGE_APPS = (
    (("lollypop",), "org.gnome.Lollypop"),
    (SHORTWAVE_KEYS, "de.haeckerfelix.Shortwave"),
)
_badge_cache = {}
_desktop_ids = {}
_app_icon_cache = {}


def player_desktop_id(player):
    """The player's .desktop name (MPRIS DesktopEntry, e.g. io.github.celluloid_player.Celluloid).
    Asked once per player, then cached. Call from the worker thread."""
    if not player or player.startswith(("%", "__")):
        return None
    if player in _desktop_ids:
        return _desktop_ids[player]
    app = None
    try:
        r = subprocess.run(["dbus-send", "--session", "--print-reply", f"--dest={MPRIS_PREFIX}{player}",
                            MPRIS_PATH, "org.freedesktop.DBus.Properties.Get",
                            "string:org.mpris.MediaPlayer2", "string:DesktopEntry"],
                           capture_output=True, text=True, timeout=1)
        m = re.search(r'string "([^"]+)"', r.stdout or "")
        app = m.group(1) if m else None
    except Exception:
        app = None
    if not app:
        n = player.lower()
        app = next((a for keys, a in BADGE_APPS if any(k in n for k in keys)), None)
    if app and app.endswith(".desktop"):
        app = app[:-8]
    _desktop_ids[player] = app
    return app


def _app_gicon(app_id):
    if not app_id:
        return None
    try:
        info = DesktopAppInfo.new(app_id + ".desktop")
        return info.get_icon() if info is not None else None
    except Exception:
        return None


def app_icon_pixbuf(app_id, size):
    """App icon shown in place of missing artwork (like the TV / Safari cards on macOS)."""
    key = (app_id, size)
    if key not in _app_icon_cache:
        pb = None
        icon = _app_gicon(app_id)
        if icon is not None:
            try:
                info = Gtk.IconTheme.get_default().lookup_by_gicon(icon, size, Gtk.IconLookupFlags.FORCE_SIZE)
                pb = info.load_icon() if info else None
            except Exception:
                pb = None
        _app_icon_cache[key] = pb
    return _app_icon_cache[key]


def _trim_alpha(pb):
    """Trims transparent edges, keeping the image square."""
    if not pb.get_has_alpha():
        return pb
    w, h, n, rs = pb.get_width(), pb.get_height(), pb.get_n_channels(), pb.get_rowstride()
    px = pb.get_pixels()
    xs, ys = [], []
    for y in range(h):
        row = y * rs
        for x in range(w):
            if px[row + x * n + 3] > 16:
                xs.append(x); ys.append(y)
    if not xs:
        return pb
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    side = max(x1 - x0, y1 - y0) + 1
    cx, cy = (x0 + x1 + 1) / 2, (y0 + y1 + 1) / 2
    sx = int(max(0, min(w - side, round(cx - side / 2))))
    sy = int(max(0, min(h - side, round(cy - side / 2))))
    return pb.new_subpixbuf(sx, sy, min(side, w), min(side, h))


def badge_pixbuf(app_id, size):
    """The player's badge icon (Icon= from its .desktop file) or None."""
    if not app_id:
        return None
    key = (app_id, size)
    if key not in _badge_cache:
        pb = None
        try:
            icon = _app_gicon(app_id)
            # Draw large, trim the transparent margin, then scale down: theme icons have padding
            # (WhiteSur ~12%), which would make the badge look smaller than on macOS
            big = size * 4
            info = Gtk.IconTheme.get_default().lookup_by_gicon(icon, big, Gtk.IconLookupFlags.FORCE_SIZE)
            pb = info.load_icon() if info else None
            if pb is not None:
                pb = _trim_alpha(pb).scale_simple(size, size, GdkPixbuf.InterpType.HYPER)
        except Exception:
            pb = None
        _badge_cache[key] = pb
    return _badge_cache[key]


class CoverBadge(Gtk.DrawingArea):
    """The badge on the cover's corner. Clicks pass through to the cover."""

    def __init__(self, size):
        super().__init__()
        self.pb = None
        self.set_size_request(size, size)
        self.set_halign(Gtk.Align.END)
        self.set_valign(Gtk.Align.END)
        self.set_no_show_all(True)
        self.connect("draw", self._draw)

    def set_pixbuf(self, pb):
        self.pb = pb
        self.set_visible(pb is not None)
        self.queue_draw()

    def _draw(self, w, cr):
        if self.pb is not None:
            Gdk.cairo_set_source_pixbuf(cr, self.pb, 0, 0)
            cr.paint()
        return True


def wrap_with_badge(cover, size, margin_top=0):
    """Puts the cover and its badge in an Overlay, with BADGE_OVER of room right and below for
    the overhang; callers subtract it again in their layout."""
    cover.set_margin_end(BADGE_OVER)
    cover.set_margin_bottom(BADGE_OVER)
    if margin_top:
        cover.set_margin_top(margin_top)
    badge = CoverBadge(size)
    ov = Gtk.Overlay()
    ov.add(cover)
    ov.add_overlay(badge)
    ov.set_overlay_pass_through(badge, True)
    return ov, badge


def tooltip_when_cut(label):
    """Tooltip with the full text, only when the label is ellipsized."""
    def query(lbl, x, y, keyboard, tooltip):
        layout = lbl.get_layout()
        if layout is not None and layout.is_ellipsized():
            tooltip.set_text(lbl.get_text())
            return True
        return False
    label.set_has_tooltip(True)
    label.connect("query-tooltip", query)


# ── YouTube hover-preview filter (browsers) ────────────────
# Hovering a YouTube thumbnail plays a preview, which the browser announces as playing media.
# During a preview the page URL doesn't change and the tab title keeps the real video's name.
# So: same URL, new title that's in no window title while the old one is, means a preview,
# and the real video keeps being shown.
_gecko_accepted = {}
_win_titles = {"t": 0.0, "titles": []}


def _window_titles():
    now = time.monotonic()
    if now - _win_titles["t"] > 1.0:          # 1 s cache shared by the tile and the view
        try:
            out = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True, timeout=1).stdout
        except Exception:
            out = ""
        _win_titles["titles"] = [l.split(None, 3)[3] if len(l.split(None, 3)) > 3 else ""
                                 for l in out.splitlines()]
        _win_titles["t"] = now
    return _win_titles["titles"]


def youtube_preview_filter(player, title, artist, url):
    """(title, artist, is_preview) for browsers. LibreWolf sends the page URL, which must stay
    the same; Brave doesn't, so only the window title is checked (it's the active tab's, i.e.
    the real video's). Switching to a new video changes the tab title, so it's accepted."""
    u = url or ""
    if not is_browser_player(player) or not title:
        return title, artist, False
    gecko = is_gecko_player(player)
    if gecko and "youtube.com/watch" not in u:
        return title, artist, False
    last = _gecko_accepted.get(player)
    if last and (last["url"] == u or not gecko) and last["title"] != title:
        wins = _window_titles()
        if not any(title in w for w in wins) and any(last["title"] in w for w in wins):
            return last["title"], last["artist"], True
    _gecko_accepted[player] = {"url": u, "title": title, "artist": artist}
    return title, artist, False


def icon_candidates(player_name, source=None):
    names = list(SOURCE_ICONS.get(source, []))
    if is_gecko_player(player_name):
        names += ["librewolf", "firefox"]
    elif is_browser_player(player_name):
        names += ["brave-browser", "brave"]
    return names


def fit_cover_pixbuf(path, size):
    """Fits an image into a size x size square, keeping its aspect ratio. Square (or nearly)
    covers are scaled directly; for others, like radio logos, the empty sides are filled with
    the image's corner colour so the logo blends in (neutral grey if the corners are clear)."""
    src = GdkPixbuf.Pixbuf.new_from_file(path)
    w, h = src.get_width(), src.get_height()
    if w <= 0 or h <= 0:
        return None
    if abs(w - h) <= max(w, h) * 0.03:
        return src.scale_simple(size, size, GdkPixbuf.InterpType.HYPER)

    scale = size / max(w, h)
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    scaled = src.scale_simple(nw, nh, GdkPixbuf.InterpType.HYPER)

    fill = 0xE6E6E6FF
    try:
        px = src.get_pixels()
        n, rs = src.get_n_channels(), src.get_rowstride()
        corners = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]
        cols = []
        for x, y in corners:
            i = y * rs + x * n
            a = px[i + 3] if src.get_has_alpha() else 255
            if a < 128:
                cols = None
                break
            cols.append((px[i], px[i + 1], px[i + 2]))
        if cols:
            r = sum(c[0] for c in cols) // 4
            g = sum(c[1] for c in cols) // 4
            b = sum(c[2] for c in cols) // 4
            fill = (r << 24) | (g << 16) | (b << 8) | 0xFF
    except Exception:
        pass

    canvas = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, size, size)
    canvas.fill(fill)
    ox, oy = (size - nw) // 2, (size - nh) // 2
    scaled.composite(canvas, ox, oy, nw, nh, ox, oy, 1.0, 1.0, GdkPixbuf.InterpType.NEAREST, 255)
    return canvas


def round_cover_bg(widget, radius):
    """Clips the cover box's CSS background (.modern-media-cover) to the cover's rounding,
    so square corners don't show behind light covers."""
    prov = Gtk.CssProvider()
    prov.load_from_data(f".modern-media-cover {{ border-radius: {radius}px; }}".encode())
    widget.get_style_context().add_provider(prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 100)


def download_art(url, timeout=5):
    """Downloads the artwork to a per-URL file in /tmp (only once)."""
    if not url:
        return None
    dest = os.path.join("/tmp", "cupertino_art_" + hashlib.md5(url.encode()).hexdigest())
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return dest
    try:
        r = subprocess.run(
            ["curl", "-s", "-L", "--max-time", str(timeout), "-A", "Mozilla/5.0", "-o", dest, url],
            stdout=DEVNULL, stderr=DEVNULL, timeout=timeout + 1,
        )
    except Exception:
        return None
    if r.returncode == 0 and os.path.exists(dest) and os.path.getsize(dest) > 0:
        return dest
    try:
        os.remove(dest)
    except Exception:
        pass
    return None


_itunes_cache = {}
_itunes_waiters = {}   # key -> waiting callbacks (the tile and the view may ask at the same time)
_itunes_lock = threading.Lock()


def split_radio_title(title, station=""):
    """Radios send "Artist - Song" as the title and the station as the artist. Splits it into
    (song, artist), or (None, None)."""
    t = (title or "").strip()
    for sep in (" - ", " – ", " — "):
        if sep in t:
            singer, song = (x.strip() for x in t.split(sep, 1))
            if singer and song and singer.lower() != (station or "").strip().lower():
                return song, singer
    return None, None


def radio_display(title, station):
    """(title, second line) to show, and (artist, song) for the artwork lookup."""
    song, singer = split_radio_title(title, station)
    if not song:
        return title, station, None
    sub = f"{singer} – {station}" if station else singer   # en dash, as on macOS ("Artist – Album")
    return song, sub, (singer, song)


def itunes_cover_async(artist, title, on_ready=None):
    """Looks up artwork on iTunes for "artist + song". Returns the cached path right away if
    there is one; otherwise searches in the background and calls on_ready(path) (from the
    worker thread). Several callers may wait for the same song."""
    if not artist or not title:
        return None
    key = f"{artist}|{title}".lower()
    with _itunes_lock:
        if key in _itunes_cache:
            return _itunes_cache[key]
        waiters = _itunes_waiters.setdefault(key, [])
        if on_ready:
            waiters.append(on_ready)
        if len(waiters) > (1 if on_ready else 0) or key + "#run" in _itunes_waiters:
            return None  # already searching
        _itunes_waiters[key + "#run"] = True

    def worker():
        path = None
        try:
            query = urllib.parse.quote_plus(f"{artist} {title}".strip())
            api = f"https://itunes.apple.com/search?term={query}&media=music&entity=song&limit=1"
            req = urllib.request.Request(api, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode())
            results = data.get("results") or []
            art = results[0].get("artworkUrl100", "") if results else ""
            if art:
                # 100 px is too blurry; ask for the large version
                path = download_art(art.replace("100x100bb", "600x600bb")) or download_art(art)
        except Exception as e:
            print(f"[iTunes] could not get artwork: {e}")
        with _itunes_lock:
            _itunes_cache[key] = path
            callbacks = _itunes_waiters.pop(key, [])
            _itunes_waiters.pop(key + "#run", None)
        if path:
            for cb in callbacks:
                try:
                    cb(path)
                except Exception:
                    pass

    threading.Thread(target=worker, daemon=True).start()
    return None


_caps_cache = {}


def _mpris_can_raw(player, prop):
    """The real CanGoNext/CanGoPrevious value, uncached."""
    try:
        r = subprocess.run(["dbus-send", "--session", "--print-reply", f"--dest={MPRIS_PREFIX}{player}",
                            MPRIS_PATH, "org.freedesktop.DBus.Properties.Get",
                            "string:org.mpris.MediaPlayer2.Player", f"string:{prop}"],
                           capture_output=True, text=True, timeout=1)
        if r.returncode == 0:
            return "boolean false" not in r.stdout
    except Exception:
        pass
    return True


def mpris_can(player, prop):
    """CanGoNext / CanGoPrevious, so unsupported buttons look dimmed. Cached for 5 s; True if
    it can't be read (don't disable buttons needlessly).

    Not for browsers (Brave and its web apps): Chromium reports whatever media buttons the page
    registered, which is unreliable, so their buttons stay enabled."""
    if is_browser_player(player):
        return True
    key, now = (player, prop), time.monotonic()
    hit = _caps_cache.get(key)
    if hit and now - hit[1] < 5:
        return hit[0]
    val = _mpris_can_raw(player, prop)
    _caps_cache[key] = (val, now)
    return val


class PlayerSelector:
    """Which player to show: the one that most recently started playing > the last one that
    played (sticky) > paused > stopped. Pausing keeps the focus; another player only takes
    over when it starts playing.

    Position isn't used: Chromium only updates it on events, so a playing video would look
    stuck. PlaybackStatus is reliable."""

    def __init__(self):
        self._lock = threading.RLock()
        self._bus = None
        self._last_status = {}    # player -> last seen status
        self._playing_since = {}  # player -> when it started playing
        self._sticky = None

    def _get_bus(self):
        if self._bus is None:
            self._bus = SessionBus()
        return self._bus

    def reset_bus(self):
        with self._lock:
            self._bus = None

    def proxy(self, short_name):
        if not DBUS_SUPPORT:
            return None
        with self._lock:
            bus = self._get_bus()
        return bus.get(MPRIS_PREFIX + short_name, MPRIS_PATH)

    def set_focus(self, short_name):
        """Makes a player sticky when the user picks it."""
        with self._lock:
            if short_name and short_name != "%any":
                self._sticky = short_name

    def _collect_states(self):
        """Reads every player's status with one playerctl call. (pydbus can fail on Chromium
        and silently drop it from the list; media_view uses playerctl too, so both agree.)"""
        out = run_playerctl("-a", "status", "--format", "{{playerInstance}}\t{{lc(status)}}")
        if out is None:
            return []
        states = []
        for line in out.splitlines():
            name, _, status = line.partition("\t")
            name, status = name.strip(), status.strip()
            if not name or not status or name.startswith("playerctld"):
                continue
            states.append((name, status))
        return states

    def pick(self):
        with self._lock:
            try:
                states = self._collect_states()
            except Exception as e:
                print(f"[PlayerSelector] scan error: {e}")
                return self._sticky

            if not states:
                self._sticky = None
                self._last_status.clear()
                self._playing_since.clear()
                return None

            now = time.monotonic()
            playing, paused, stopped = [], [], []
            for short, status in states:
                if status == "playing":
                    if self._last_status.get(short) != "playing":
                        self._playing_since[short] = now  # just started
                    playing.append(short)
                else:
                    self._playing_since.pop(short, None)
                    (paused if status == "paused" else stopped).append(short)
                self._last_status[short] = status

            alive = {s for s, _ in states}
            for d in (self._last_status, self._playing_since):
                for k in [k for k in d if k not in alive]:
                    del d[k]
            if self._sticky not in alive:
                self._sticky = None

            if playing:
                self._sticky = max(playing, key=lambda p: self._playing_since.get(p, 0))
                return self._sticky
            if self._sticky is not None:
                return self._sticky
            for group in (paused, stopped):
                if group:
                    self._sticky = group[0]
                    return self._sticky
            return None


# ─────────────────────────────────────────────────────────────
# Media button glyphs (drawn with cairo, independent of the icon theme)
# ─────────────────────────────────────────────────────────────

# Sizes: h = play/pause height, skip = next/previous height, hit = clickable size, gap = space
# between buttons (px). Measured on Big Sur screenshots and scaled to this window:
#   tile:        pause 22 px / next 17 px (Connections tile 258 px wide) -> 15 / 12
#   media view:  pause 36 px / prev-next 23 px (panel 587 px wide)       -> 23 / 15
#   small cards: play 19 px / next 14 px (panel 437 px wide)             -> 15 / 12
GLYPH_SIZES = {
    "box":  {"h": 15, "skip": 12, "hit": 28, "gap": 0},   # media tile on the main view
    "big":  {"h": 23, "skip": 15, "hit": 42, "gap": 14},  # large card in the media view
    "mini": {"h": 15, "skip": 12, "hit": 28, "gap": 0},   # small cards in the media view
}


class MediaGlyph(Gtk.DrawingArea):
    """play / pause / next / prev glyph."""

    def __init__(self, kind, height):
        super().__init__()
        self.kind = kind
        self.h = height
        self.set_size_request(int(height * 1.75) + 1, height + 2)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)
        self.connect("draw", self._on_draw)

    def set_kind(self, kind):
        if kind != self.kind:
            self.kind = kind
            self.queue_draw()

    @staticmethod
    def _round_poly(cr, pts, r):
        """Polygon with softened corners: at each corner the edges stop r short and the corner
        is turned with a curve."""
        n = len(pts)

        def toward(a, b, dist):
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = (dx * dx + dy * dy) ** 0.5 or 1.0
            d = min(dist, length / 2)
            return a[0] + dx / length * d, a[1] + dy / length * d

        for i in range(n):
            prev, v, nxt = pts[i - 1], pts[i], pts[(i + 1) % n]
            a, b = toward(v, prev, r), toward(v, nxt, r)
            if i == 0:
                cr.move_to(*a)
            else:
                cr.line_to(*a)
            cr.curve_to(v[0], v[1], v[0], v[1], b[0], b[1])
        cr.close_path()

    @staticmethod
    def _rrect(cr, x, y, w, h, r):
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -1.5708, 0)
        cr.arc(x + w - r, y + h - r, r, 0, 1.5708)
        cr.arc(x + r, y + h - r, r, 1.5708, 3.1416)
        cr.arc(x + r, y + r, r, 3.1416, 4.7124)
        cr.close_path()

    def _on_draw(self, widget, cr):
        alloc = self.get_allocation()
        flags = self.get_state_flags()
        disabled = bool(int(flags) & int(Gtk.StateFlags.INSENSITIVE))
        c = self.get_style_context().get_color(Gtk.StateFlags(int(flags) & ~int(Gtk.StateFlags.INSENSITIVE)))
        # As on macOS: dark grey, not black (~80% black in light mode); disabled = half of
        # that. One fill, no outline, so edges don't look different from the inside.
        top = self.get_toplevel()
        dark = top is not None and top.get_style_context().has_class("dark-mode")
        if getattr(self, "secondary", False):
            # Buttons in the media view: secondary grey as on macOS (same as
            # .mac-secondary-text); disabled = half of that
            r, g, b = (1.0, 1.0, 1.0) if dark else (0.0, 0.0, 0.0)
            alpha = (0.55 if dark else 0.5) * (0.5 if disabled else 1.0)
        elif dark:
            # Dark mode: white, disabled ~55% white, as on macOS
            r, g, b = 1.0, 1.0, 1.0
            alpha = 0.55 if disabled else 1.0
        else:
            # Light mode: dark grey (~80% black), disabled = half of that
            r, g, b = c.red, c.green, c.blue
            alpha = c.alpha * 0.8 * (0.5 if disabled else 1.0)
        cr.set_source_rgba(r, g, b, alpha)

        h = self.h
        cx, top = alloc.width / 2, (alloc.height - h) / 2

        if self.kind == "pause":
            # Two bars with a gap (width/height ~0.84)
            bw, gap = h * 0.32, h * 0.20
            x0 = cx - (2 * bw + gap) / 2
            r = bw * 0.32
            self._rrect(cr, x0, top, bw, h, r)
            self._rrect(cr, x0 + bw + gap, top, bw, h, r)
            cr.fill()
            return True

        r = h * 0.2    # corner softness
        if self.kind == "play":
            w = h * 0.9
            x0 = cx - w / 2 + h * 0.06   # slightly right: optical centering
            self._round_poly(cr, [(x0, top), (x0 + w, top + h / 2), (x0, top + h)], r)
        else:  # next / prev: two touching triangles (total width ~1.75 x height)
            w = h * 0.87
            x0 = cx - w
            for k in (0, 1):
                xa = x0 + k * w
                if self.kind == "next":
                    pts = [(xa, top), (xa + w, top + h / 2), (xa, top + h)]
                else:
                    pts = [(xa + w, top), (xa, top + h / 2), (xa + w, top + h)]
                self._round_poly(cr, pts, r)
        cr.fill()
        return True


def make_media_button(kind, size_key, on_click):
    """Frameless media button with a fixed hit area. Returns (button, glyph)."""
    spec = GLYPH_SIZES[size_key]
    glyph = MediaGlyph(kind, spec["h"] if kind in ("play", "pause") else spec.get("skip", spec["h"]))
    btn = Gtk.Button()
    btn.set_relief(Gtk.ReliefStyle.NONE)
    btn.add(glyph)
    # The hit area is the glyph plus 4 px on the sides and 6 px above/below; the rest of the
    # "hit" square is non-clickable margin, so the layout doesn't change
    gw, gh = glyph.get_size_request()
    hw = min(spec["hit"], gw + 8)
    hh = min(spec["hit"], gh + 12)
    hw += (spec["hit"] - hw) % 2; hh += (spec["hit"] - hh) % 2   # equal margins, so the glyph stays on whole pixels
    btn.set_size_request(hw, hh)
    btn.set_margin_start((spec["hit"] - hw) // 2); btn.set_margin_end(spec["hit"] - hw - (spec["hit"] - hw) // 2)
    btn.set_margin_top((spec["hit"] - hh) // 2); btn.set_margin_bottom(spec["hit"] - hh - (spec["hit"] - hh) // 2)
    btn.set_valign(Gtk.Align.CENTER)
    btn.get_style_context().add_class("media-glyph-button")
    # No theme padding (CSS: button.media-glyph-button.box-glyph), so the spacing doesn't
    # depend on the theme
    btn.get_style_context().add_class("box-glyph")
    btn.connect("clicked", lambda _b: on_click())
    return btn, glyph


class _WindowTools:
    """Finding and raising windows (wmctrl + xdotool + D-Bus)."""

    @staticmethod
    def _run(args, timeout=2):
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
            return r.stdout if r.returncode == 0 else None
        except Exception:
            return None

    def _mpris_raise(self, player):
        self._run(["dbus-send", "--session", "--type=method_call", f"--dest={MPRIS_PREFIX}{player}",
                   MPRIS_PATH, "org.mpris.MediaPlayer2.Raise"])

    def _player_pid(self, player):
        out = self._run(["dbus-send", "--session", "--print-reply", "--dest=org.freedesktop.DBus",
                         "/org/freedesktop/DBus", "org.freedesktop.DBus.GetConnectionUnixProcessID",
                         f"string:{MPRIS_PREFIX}{player}"])
        m = re.search(r"uint32 (\d+)", out or "")
        return int(m.group(1)) if m else None

    def _list_windows(self):
        wins = []
        for line in (self._run(["wmctrl", "-lpx"]) or "").splitlines():
            parts = line.split(None, 5)
            if len(parts) < 5:
                continue
            wins.append({
                "id": int(parts[0], 16),
                "pid": int(parts[2]) if parts[2].isdigit() else -1,
                "cls": parts[3].lower(),
                "title": parts[5] if len(parts) > 5 else "",
            })
        return wins

    def _active_wid(self):
        out = self._run(["xdotool", "getactivewindow"])
        try:
            return int(out.strip())
        except Exception:
            return None

    def _pick_window(self, player, media_title, source):
        wins = self._list_windows()

        # Brave web app: window class crx_<app id>, an exact match whatever the title
        app_id = PWA_APP_IDS.get(source)
        if app_id:
            for w in wins:
                if f"crx_{app_id}" in w["cls"]:
                    return w

        pid = self._player_pid(player)
        pool = [w for w in wins if pid and w["pid"] == pid]
        if not pool:  # e.g. Flatpak apps (Shortwave), where the PID doesn't match
            key = "shortwave" if is_shortwave_player(player) else player.split(".")[0].lower()
            pool = [w for w in wins if key in w["cls"]]
        t = (media_title or "").lower()[:25]
        if t:
            for w in pool:
                if t in w["title"].lower():
                    return w
        if is_browser_player(player) and not app_id:
            normal = [w for w in pool if "crx_" not in w["cls"]]  # no title match: prefer a normal window
            pool = normal or pool
        return pool[0] if pool else None

    def send_key_to(self, player, media_title, source, keys):
        """Sends a keyboard shortcut to the player's window, then gives focus back. Chromium
        ignores synthetic keys sent to background windows, so it's raised for a moment."""
        target = self._pick_window(player, media_title, source)
        if not target:
            return False
        # Not a web app: the title must match, so the key doesn't go to the wrong tab
        t = (media_title or "").lower()[:25]
        if "crx_" not in target["cls"] and not (t and t in target["title"].lower()):
            return False

        app = Gtk.Application.get_default()
        if app is not None:
            app._suppress_focus_exit = True  # don't close the Control Center when focus moves
        prev = self._active_wid()
        try:
            if not self._activate(target["id"]):
                return False
            self._run(["xdotool", "key", "--clearmodifiers", keys])
            time.sleep(0.05)
            if prev and prev != target["id"]:
                self._run(["xdotool", "windowactivate", str(prev)])
            return True
        finally:
            if app is not None:
                GLib.timeout_add(400, lambda: setattr(app, "_suppress_focus_exit", False) or False)


    def _activate(self, wid):
        for _ in range(3):
            self._run(["xdotool", "windowactivate", "--sync", str(wid)], timeout=1.5)
            if self._active_wid() == wid:
                return True
            self._run(["wmctrl", "-i", "-a", hex(wid)])
            time.sleep(0.1)
            if self._active_wid() == wid:
                return True
        return False



_wt = _WindowTools()

# YouTube's and YouTube Music's own shortcuts (on YouTube, previous only works in playlists)
YT_KEYS = {"next": "shift+n", "previous": "shift+p"}


def send_media_command(player, cmd):
    """Sends a command to a player. When YouTube hasn't registered "next" with the browser
    (CanGoNext=false, so MPRIS and the media keys don't work), the site's own shortcut is
    sent to the window. Call from the worker thread."""
    if not player or player == "%any":
        run_playerctl(cmd)
        return
    if cmd in YT_KEYS and is_browser_player(player):
        prop = "CanGoNext" if cmd == "next" else "CanGoPrevious"
        if not _mpris_can_raw(player, prop):
            out = run_playerctl(f"--player={player}", "metadata", "--format", "{{xesam:url}}\t{{title}}") or ""
            url, _, title = out.rstrip("\n").partition("\t")
            source = detect_web_source(player, url)
            if source in ("youtube", "youtube-music") and _wt.send_key_to(player, title, source, YT_KEYS[cmd]):
                return
    run_playerctl(f"--player={player}", cmd)


_selector = PlayerSelector()


def get_selector():
    return _selector


# ─────────────────────────────────────────────────────────────
# Media tile
# ─────────────────────────────────────────────────────────────

class MediaControlBox(Gtk.Box, _WindowTools):
    # Measured on Big Sur: the cover is ~68% of the tile height, its left edge lines up with the Wi-Fi icon
    COVER_SIZE = 49
    ICON_SIZE = 23
    COVER_RADIUS = 3   # measured on macOS: ~5 px on a ~98 px cover (~5%)
    META_FMT = "\t".join(["{{status}}", "{{title}}", "{{artist}}", "{{mpris:artUrl}}", "{{xesam:url}}"])

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.set_hexpand(True)
        self.set_size_request(-1, 65)
        self.get_style_context().add_class("styledwidgetbox")

        self.is_running = True
        self.active_player = "%any"
        self.current_pixbuf = None

        self._last_sig = None
        self._snap = None          # what the tile shows now (media_view uses it when opening)
        self._last_cover_key = None
        self._cover_token = 0
        self._harvesting = False
        self._harvest_lock = threading.Lock()

        # ALBUM COVER / THUMBNAIL
        self.cover_container = Gtk.EventBox()
        self.cover_container.set_size_request(self.COVER_SIZE, self.COVER_SIZE)
        self.cover_container.set_hexpand(False)
        self.cover_container.set_vexpand(False)
        self.cover_container.set_margin_start(2)   # 6 (tile) + 4 (packing) = the Wi-Fi icon's x
        self.cover_container.set_halign(Gtk.Align.START)
        self.cover_container.set_valign(Gtk.Align.CENTER)
        self.cover_container.get_style_context().add_class("modern-media-cover")
        round_cover_bg(self.cover_container, self.COVER_RADIUS)

        self.cover_canvas = Gtk.DrawingArea()
        self.cover_canvas.set_size_request(self.COVER_SIZE, self.COVER_SIZE)
        self.cover_canvas.connect("draw", self._on_canvas_draw)
        self.cover_container.add(self.cover_canvas)

        # TEXT
        info_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        info_vbox.set_hexpand(True)
        info_vbox.set_valign(Gtk.Align.CENTER)

        self.title_label = Gtk.Label(xalign=0)
        self.title_label.get_style_context().add_class("general_title")
        self.title_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.title_label.set_max_width_chars(14)

        self.artist_label = Gtk.Label(xalign=0)
        self.artist_label.get_style_context().add_class("general_desc")
        self.artist_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.artist_label.set_max_width_chars(16)
        # Second line hidden when empty, so the title is centered alone
        self.artist_label.set_no_show_all(True)

        tooltip_when_cut(self.title_label)
        tooltip_when_cut(self.artist_label)

        info_vbox.pack_start(self.title_label, False, False, 0)
        info_vbox.pack_start(self.artist_label, False, False, 0)

        # BUTTONS
        controls_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=GLYPH_SIZES["box"]["gap"])
        controls_hbox.set_valign(Gtk.Align.CENTER)
        controls_hbox.set_hexpand(False)
        controls_hbox.set_halign(Gtk.Align.END)

        self.play_btn, self.play_icon = make_media_button("play", "box", lambda: self._send_player_cmd("play-pause"))
        self.next_btn, _ = make_media_button("next", "box", lambda: self._send_player_cmd("next"))
        next_btn = self.next_btn

        controls_hbox.pack_start(self.play_btn, False, False, 0)
        controls_hbox.pack_start(next_btn, False, False, 5)

        info_click_area = Gtk.EventBox()
        info_click_area.add(info_vbox)

        self.cover_container.connect("button-press-event", self._on_cover_clicked)
        info_click_area.connect("button-press-event", self._on_text_clicked)

        self.pack_start(self.cover_container, False, False, 4)
        self.pack_start(info_click_area, True, True, 3)
        # Theme padding is zeroed in CSS (box-glyph); the next glyph sits 14 px from the tile
        # edge, lined up with the circles of the Sound/Display buttons
        for _b in (self.play_btn, next_btn):
            _b.get_style_context().add_class("box-glyph")
        self.pack_start(controls_hbox, False, False, 1)

        self.connect("destroy", self._on_destroy)

        self.title_label.set_text(IDLE_TITLE)
        self.artist_label.set_text("")
        self._load_fallback_pixbuf()

        # First load right away, without blocking the UI
        self._polling_worker()
        # Players announce changes (pause, new track...) on D-Bus; the tile listens and checks
        # right away, using no CPU in between. Polling is only a fallback for players that
        # don't announce: every 5 s with D-Bus, every 2 s without.
        self._mpris_sub = None
        self._mpris_pending = False
        if DBUS_SUPPORT:
            try:
                self._mpris_sub = SessionBus().subscribe(
                    iface="org.freedesktop.DBus.Properties", signal="PropertiesChanged",
                    object=MPRIS_PATH, signal_fired=self._on_mpris_changed)
            except Exception as e:
                print(f"[MediaControlBox] can't listen for MPRIS signals: {e}")
        GLib.timeout_add_seconds(5 if self._mpris_sub else 2, self._polling_worker)
        self.show_all()

    # ── Polling ──────────────────────────────────────────────

    def _polling_worker(self):
        if not self.is_running:
            return False
        with self._harvest_lock:
            if self._harvesting:
                return True  # the previous round is still running
            self._harvesting = True
        threading.Thread(target=self._harvest_guarded, daemon=True).start()
        return True

    def _harvest_guarded(self):
        try:
            self._async_metadata_harvester()
        except Exception as e:
            print(f"[MediaControlBox] poll error: {e}")
        finally:
            with self._harvest_lock:
                self._harvesting = False

    def _refresh_soon(self, delay_ms=350):
        def tick():
            self._polling_worker()
            return False
        GLib.timeout_add(delay_ms, tick)
        return False

    def _find_best_active_player_dbus(self):
        return get_selector().pick()

    def _get_player_metadata_instant(self, short_name):
        """{'status','title','artist','art_url','url'} or None. title/artist may be empty;
        defaults are applied in the UI."""
        sel = get_selector()
        try:
            obj = sel.proxy(short_name)
            if obj is not None:
                status = str(obj.PlaybackStatus).lower()
                md = obj.Metadata or {}
                artists = md.get("xesam:artist") or []
                if isinstance(artists, str):
                    artists = [artists]
                return {
                    "status": status,
                    "title": str(md.get("xesam:title") or ""),
                    "artist": str(artists[0]) if artists else "",
                    "art_url": str(md.get("mpris:artUrl") or ""),
                    "url": str(md.get("xesam:url") or ""),
                }
        except Exception:
            sel.reset_bus()

        out = run_playerctl(f"--player={short_name}", "metadata", "--format", self.META_FMT)
        if out is None:
            status = (run_playerctl(f"--player={short_name}", "status") or "").strip().lower()
            if not status:
                return None
            return {"status": status, "title": "", "artist": "", "art_url": "", "url": ""}
        parts = out.rstrip("\n").split("\t") + [""] * 5
        return {
            "status": parts[0].strip().lower() or "stopped",
            "title": parts[1],
            "artist": parts[2],
            "art_url": parts[3],
            "url": parts[4],
        }

    def _async_metadata_harvester(self):
        app = Gtk.Application.get_default()
        if app is not None and time.time() < getattr(app, "_global_lock_timeout", 0):
            return

        chosen = self._find_best_active_player_dbus()
        meta = self._get_player_metadata_instant(chosen) if chosen else None

        if not meta:
            if self._last_sig != "empty":
                self._last_sig = "empty"
                self._last_cover_key = None
                GLib.idle_add(self._apply_empty_ui)
            return

        # Don't let a YouTube hover preview replace the real video
        meta["title"], meta["artist"], _ = youtube_preview_filter(
            chosen, meta["title"], meta["artist"], meta["url"])
        # Same text rules as media_view (media_texts)
        meta["title"], meta["artist"], meta["search"] = media_texts(
            chosen, meta["title"], meta["artist"], meta["url"])
        meta["can_next"] = mpris_can(chosen, "CanGoNext")
        meta["app_id"] = player_desktop_id(chosen)

        sig = (chosen, meta["status"], meta["title"], meta["artist"], meta["art_url"], meta["can_next"])
        if sig == self._last_sig:
            return
        self._last_sig = sig

        has_cover = meta["status"] in ("playing", "paused")
        cover_key = (chosen, meta["art_url"], meta["title"], meta["artist"], has_cover)
        cover_changed = cover_key != self._last_cover_key
        self._last_cover_key = cover_key

        GLib.idle_add(self._apply_media_ui, chosen, meta, cover_changed)

    # ── UI updates (main thread) ────────────────────────────

    def _apply_empty_ui(self):
        self.active_player = "%any"
        self._snap = None
        self.title_label.set_text(IDLE_TITLE)
        self.artist_label.set_text("")
        self.artist_label.set_visible(False)
        self.play_icon.set_kind("play")
        self.next_btn.set_sensitive(False)   # as on macOS: next is dimmed without media
        self._cover_token += 1
        self._set_cover_icon(self._cover_token, IDLE_ICONS)
        return False

    def _apply_media_ui(self, chosen, meta, cover_changed):
        self.active_player = chosen
        title = meta["title"] or NO_MEDIA
        artist = meta["artist"]

        prev = self._snap or {}
        self._snap = {"player": chosen, "status": meta["status"], "title": title, "artist": artist,
                      "cover_path": prev.get("cover_path") if (not cover_changed and prev.get("player") == chosen) else None}

        # Tooltip with the full name only when ellipsized (tooltip_when_cut)
        self.title_label.set_text(title)
        self.artist_label.set_text(artist)
        self.artist_label.set_visible(bool(artist))

        self.play_icon.set_kind("pause" if meta["status"] == "playing" else "play")
        self.next_btn.set_sensitive(meta.get("can_next", True))

        if not cover_changed:
            return False

        self._cover_token += 1
        token = self._cover_token
        if meta["status"] in ("playing", "paused"):
            threading.Thread(target=self._resolve_cover, args=(token, chosen, dict(meta)), daemon=True).start()
        elif is_shortwave_player(chosen):
            # Stopped radio: the Shortwave icon instead of the generic music icon
            self._set_cover_icon(token, ["de.haeckerfelix.Shortwave", "shortwave"])
        else:
            self._load_fallback_pixbuf()
        return False

    # ── Artwork ─────────────────────────────────────────────
    # Network/file work in the thread, GTK/icon work on the main thread.
    # token: a stale request must not overwrite the current player's cover.

    def _resolve_cover(self, token, player, meta):
        try:
            if is_browser_player(player):
                source = detect_web_source(player, meta["url"])
                if is_gecko_player(player):
                    # Browser tab: the browser's own icon (Icon= in its .desktop), like the
                    # Safari card on macOS
                    GLib.idle_add(self._set_cover_app, token, meta.get("app_id"), icon_candidates(player))
                else:
                    # Brave web apps (YouTube/Netflix/Telegram): the site's icon
                    GLib.idle_add(self._set_cover_icon, token, icon_candidates(player, source))
                return

            if meta.get("search"):
                found = itunes_cover_async(
                    *meta["search"],
                    on_ready=lambda p: GLib.idle_add(self._set_cover_file, token, p),
                )
                if found:
                    GLib.idle_add(self._set_cover_file, token, found)
                    return
                # Lookup running / nothing found: station logo for now; the callback above replaces it

            art_url = meta["art_url"]
            path = None
            if art_url.startswith("file://"):
                p = unquote(urlparse(art_url).path)
                if os.path.exists(p):
                    path = p
            elif art_url.startswith(("http://", "https://")):
                path = download_art(art_url)

            if path:
                GLib.idle_add(self._set_cover_file, token, path)
                return

            if is_shortwave_player(player):
                GLib.idle_add(self._set_cover_icon, token, ["de.haeckerfelix.Shortwave", "shortwave"])
                return

            # No artwork (e.g. a video in Celluloid): the player's own icon
            GLib.idle_add(self._set_cover_app, token, meta.get("app_id"))
        except Exception as e:
            print(f"[MediaControlBox] artwork error: {e}")
            GLib.idle_add(self._set_cover_fallback, token)

    def _set_cover_icon(self, token, names, size=None):
        if token != self._cover_token:
            return False
        size = size or self.ICON_SIZE
        theme = Gtk.IconTheme.get_default()
        for name in names:
            try:
                if not theme.has_icon(name):
                    continue
                info = theme.lookup_icon(name, size, 0)
                if info:
                    self.current_pixbuf = info.load_icon().scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)
                    if self._snap:
                        self._snap["cover_path"] = None
                    self.cover_canvas.queue_draw()
                    return False
            except Exception:
                continue
        self._load_fallback_pixbuf()
        return False

    def _set_cover_app(self, token, app_id, fallback_names=None):
        if token != self._cover_token:
            return False
        pb = app_icon_pixbuf(app_id, self.ICON_SIZE)
        if pb is None:
            if fallback_names:
                return self._set_cover_icon(token, fallback_names)
            self._load_fallback_pixbuf()
            return False
        self.current_pixbuf = pb
        if self._snap:
            self._snap["cover_path"] = None
        self.cover_canvas.queue_draw()
        return False

    def _set_cover_file(self, token, path):
        if token != self._cover_token:
            return False
        try:
            pb = fit_cover_pixbuf(path, self.COVER_SIZE)
            if pb is None:
                raise ValueError("empty image")
            self.current_pixbuf = pb
            if self._snap:
                self._snap["cover_path"] = path
            self.cover_canvas.queue_draw()
        except Exception:
            self._load_fallback_pixbuf()
        return False

    def _set_cover_fallback(self, token):
        if token == self._cover_token:
            self._load_fallback_pixbuf()
        return False

    def _load_fallback_pixbuf(self):
        theme = Gtk.IconTheme.get_default()
        try:
            info = theme.lookup_icon("multimedia-audio-player", 23, 0)
            self.current_pixbuf = info.load_icon().scale_simple(23, 23, GdkPixbuf.InterpType.BILINEAR) if info else None
        except Exception:
            self.current_pixbuf = None
        if getattr(self, "_snap", None):
            self._snap["cover_path"] = None
        if getattr(self, "cover_canvas", None):
            self.cover_canvas.queue_draw()
        return False

    def _on_canvas_draw(self, widget, cr):
        if not self.current_pixbuf:
            return True
        size = self.COVER_SIZE
        radius = self.COVER_RADIUS
        cr.new_sub_path()
        cr.arc(size - radius, radius, radius, -1.5708, 0)
        cr.arc(size - radius, size - radius, radius, 0, 1.5708)
        cr.arc(radius, size - radius, radius, 1.5708, 3.1416)
        cr.arc(radius, radius, radius, 3.1416, 4.7124)
        cr.close_path()
        cr.clip()
        w = self.current_pixbuf.get_width()
        h = self.current_pixbuf.get_height()
        Gdk.cairo_set_source_pixbuf(cr, self.current_pixbuf, (size - w) / 2, (size - h) / 2)
        cr.paint()
        return True

    # ── Controls ────────────────────────────────────────────

    def _send_player_cmd(self, cmd):
        player = self.active_player
        args = [f"--player={player}"] if player and player != "%any" else []
        if args:
            get_selector().set_focus(player)

        def worker():
            send_media_command(player if args else None, cmd)
            GLib.idle_add(self._refresh_soon)

        threading.Thread(target=worker, daemon=True).start()

    def _on_text_clicked(self, widget, event):
        if event.type == Gdk.EventType.BUTTON_PRESS and event.button == 1:
            try:
                app = Gtk.Application.get_default()
                if app and hasattr(app, 'view_stack') and app.view_stack:
                    app.view_stack.set_visible_child_name("media_view")
            except Exception as e:
                print(f"Could not open the media view: {e}")
            return True
        return False

    def _on_cover_clicked(self, widget, event):
        if event is None or event.type != Gdk.EventType.BUTTON_PRESS or event.button != 1:
            return False

        player = self.active_player
        if not player or player == "%any":
            player = get_selector().pick()
        if not player:
            # No player: start the music app, as on macOS
            launch_idle_player()
            GLib.idle_add(self._clean_and_quit)
            return True

        self.active_player = player
        title = self.title_label.get_text()
        threading.Thread(target=self._focus_player_window, args=(player, title), daemon=True).start()
        return True

    # ── Focusing the player's window (thread) ───────────────
    # 1) MPRIS Raise (raises the right tab in Brave, the window in most players)
    # 2) Find the window by identity (web app class / PID), not by title
    # 3) Check it really became active, otherwise retry
    # 4) Only then close the Control Center

    def _hide_app_window(self):
        app = Gtk.Application.get_default()
        if app is not None and getattr(app, "window", None):
            GLib.idle_add(app.window.hide)

    def _focus_player_window(self, player, title):
        app = Gtk.Application.get_default()
        if app is not None:
            app._suppress_focus_exit = True
        try:
            # media_view's placeholder Shortwave card
            mpris = "de.haeckerfelix.Shortwave" if player.startswith("__") else player
            meta = self._get_player_metadata_instant(mpris) or {}
            source = detect_web_source(mpris, meta.get("url", ""))
            media_title = meta.get("title") or ("" if title == NO_MEDIA else title)

            self._mpris_raise(mpris)
            self._hide_app_window()

            # Raise alone was enough (usually)
            target = None
            for _ in range(4):
                time.sleep(0.1)
                target = self._pick_window(mpris, media_title, source)
                if target and self._active_wid() == target["id"]:
                    return

            if target and self._activate(target["id"]):
                return

            if target is None and is_shortwave_player(mpris):
                # No window at all (minimized to the tray): launch the app
                spawn(["flatpak", "run", "de.haeckerfelix.Shortwave"], stdout=DEVNULL, stderr=DEVNULL)
            elif target is None:
                print(f"[MediaControlBox] no window found for {mpris}")
        except Exception as e:
            print(f"Media error: {e}")
        finally:
            GLib.idle_add(self._clean_and_quit)

    # ── Lifecycle ───────────────────────────────────────────

    def _clean_and_quit(self):
        self.is_running = False
        toplevel = self.get_toplevel()
        if toplevel and hasattr(toplevel, "destroy"):
            toplevel.destroy()
        return False

    def _on_mpris_changed(self, *args):
        # Merge announcements arriving within 250 ms into one check
        if not self.is_running or self._mpris_pending:
            return
        self._mpris_pending = True

        def go():
            self._mpris_pending = False
            self._polling_worker()
            return False
        GLib.timeout_add(250, go)

    def _on_destroy(self, widget):
        self.is_running = False
        if getattr(self, "_mpris_sub", None) is not None:
            try:
                self._mpris_sub.unsubscribe()
            except Exception:
                pass
            self._mpris_sub = None
