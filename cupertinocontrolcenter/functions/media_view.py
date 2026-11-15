import gi
import subprocess
import threading
import os
import time
from urllib.parse import urlparse, unquote

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gdk, GdkPixbuf, Pango

from cupertinocontrolcenter.widgets.media_box  import (
    NO_MEDIA, UNKNOWN_ARTIST, IDLE_TITLE, IDLE_ICONS,
    get_selector, detect_web_source, icon_candidates,
    is_browser_player, is_shortwave_player, is_gecko_player,
    itunes_cover_async, download_art, fit_cover_pixbuf, round_cover_bg,
    make_media_button, GLYPH_SIZES,
    radio_display, mpris_can, send_media_command,
    media_texts, tooltip_when_cut,
    BADGE_OVER, badge_pixbuf, wrap_with_badge, player_desktop_id, app_icon_pixbuf,
    youtube_preview_filter,
)
from cupertinocontrolcenter.i18n import _

# Every MPRIS player is shown, except playerctl's own proxy (playerctld)
IGNORED_PLAYERS = ("playerctld",)
# Placeholder card while Shortwave is open but not on MPRIS (stopped). Its own id, so it
# doesn't clash with the real player (de.haeckerfelix.Shortwave).
SHORTWAVE_GHOST = "__shortwave_ghost__"
SHORTWAVE_BUS = "org.mpris.MediaPlayer2.de.haeckerfelix.Shortwave"
MPRIS_METHODS = {"play-pause": "PlayPause", "next": "Next", "previous": "Previous"}
META_FMT = "\t".join(["{{status}}", "{{title}}", "{{artist}}", "{{mpris:length}}",
                      "{{mpris:artUrl}}", "{{position}}", "{{xesam:url}}"])
POLL_INTERVAL = 0.75
CARD_COVER = 57          # cover size; Big Sur's ratio gives 63, but 57 balances 10pt text better.
                         # Odd, so the 27 px icon inside lands on whole pixels (see CARD_ICON)
VIEW_TOP_GAP = 14        # view top -> cover top
# cover bottom -> top of the progress bar (on macOS ~89% of the gap above, 28 / 25 px)
VIEW_COVER_BAR_GAP = 12
CARD_COVER_RADIUS = 3   # measured on macOS: ~5% of the side
# App icon shown when there is no artwork (browser, Shortwave...), same ratio as the media
# tile (23 in 49 -> 27 in 57). Cover and icon must be both odd or both even, or the icon
# lands on half pixels and blurs ((57-27)/2 = 15).
CARD_ICON = 27
CARD_BADGE = 22          # app badge, ~38% of the cover (Big Sur); overhangs by BADGE_OVER


class MediaProgress(Gtk.DrawingArea):
    """macOS-style progress bar (sizes and colours from scale.mac-pure-bar-progress in
    application.css). Everything is snapped to whole pixels so edges stay sharp."""

    # Measured on Big Sur (588 px panel -> 370 px window, x0.63): bar 6 px -> 4, knob 3x18 ->
    # 2x11, knob-to-bar gap 2 px -> 1. Knob/bar ratio ~3, as on macOS.
    TRACK_H = 4
    TRACK_R = 2
    KNOB_W = 2
    KNOB_H = 10     # even, so it overhangs the bar equally above and below (3/3)
    GAP = 1         # gap between knob and bar (whole pixels)

    def __init__(self, on_press, on_release):
        super().__init__()
        self.adj = Gtk.Adjustment(value=0, lower=0, upper=100, step_increment=1)
        self.adj.connect("value-changed", lambda *_: self.queue_draw())
        self.adj.connect("changed", lambda *_: self.queue_draw())
        self._on_press, self._on_release = on_press, on_release
        self._drag = False
        self.set_hexpand(True)
        self.set_size_request(-1, self.KNOB_H)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                        | Gdk.EventMask.BUTTON1_MOTION_MASK)
        self.connect("draw", self._draw)
        self.connect("button-press-event", self._press)
        self.connect("motion-notify-event", self._motion)
        self.connect("button-release-event", self._release)

    def get_value(self):
        return self.adj.get_value()

    def _x_to_value(self, x):
        w = max(1, self.get_allocated_width() - self.KNOB_W)
        frac = min(1.0, max(0.0, (x - self.KNOB_W / 2) / w))
        return self.adj.get_lower() + frac * (self.adj.get_upper() - self.adj.get_lower())

    def _press(self, w, e):
        if e.button != 1 or not self.get_sensitive():
            return False
        self._drag = True
        self._on_press()
        self.adj.set_value(self._x_to_value(e.x))
        return True

    def _motion(self, w, e):
        if self._drag:
            self.adj.set_value(self._x_to_value(e.x))
        return self._drag

    def _release(self, w, e):
        if not self._drag:
            return False
        self._drag = False
        self.adj.set_value(self._x_to_value(e.x))
        self._on_release(self.adj.get_value())
        return True

    def _colors(self):
        top = self.get_toplevel()
        dark = top is not None and top.get_style_context().has_class("dark-mode")
        # Measured on Big Sur (light): filled part ~88% black, remaining part ~25% black
        if dark:
            return (1, 1, 1, 1.0), (1, 1, 1, 1.0), (1, 1, 1, 0.22)    # filled / knob / remaining
        return (0, 0, 0, 0.88), (0, 0, 0, 0.88), (0, 0, 0, 0.22)

    def _bar(self, cr, x0, x1, y, round_left=True, round_right=True):
        """Bar from x0 to x1. Only the outer ends are rounded; the ends facing the knob are
        cut square."""
        if x1 - x0 < 1:
            return
        h = self.TRACK_H
        rl = min(self.TRACK_R, (x1 - x0) / 2) if round_left else 0
        rr = min(self.TRACK_R, (x1 - x0) / 2) if round_right else 0
        cr.new_sub_path()
        if rr:
            cr.arc(x1 - rr, y + rr, rr, -1.5708, 0)
            cr.arc(x1 - rr, y + h - rr, rr, 0, 1.5708)
        else:
            cr.move_to(x1, y)
            cr.line_to(x1, y + h)
        if rl:
            cr.arc(x0 + rl, y + h - rl, rl, 1.5708, 3.1416)
            cr.arc(x0 + rl, y + rl, rl, 3.1416, 4.7124)
        else:
            cr.line_to(x0, y + h)
            cr.line_to(x0, y)
        cr.close_path()
        cr.fill()

    def _draw(self, w, cr):
        W, H = self.get_allocated_width(), self.get_allocated_height()
        fill, knob, rest = self._colors()
        track_y = (H - self.TRACK_H) // 2
        lo, hi = self.adj.get_lower(), self.adj.get_upper()
        frac = (self.adj.get_value() - lo) / (hi - lo) if hi > lo else 0.0

        if not self.get_sensitive():  # live stream without a length: dim bar only
            cr.set_source_rgba(*rest)
            self._bar(cr, 0, W, track_y)
            return True

        kx = round(frac * (W - self.KNOB_W))          # knob's left edge, whole pixel
        cr.set_source_rgba(*rest)
        self._bar(cr, kx + self.KNOB_W + self.GAP, W, track_y, round_left=False)
        cr.set_source_rgba(*fill)
        self._bar(cr, 0, kx - self.GAP, track_y, round_right=False)
        cr.set_source_rgba(*knob)
        # Knob: a bar with round ends, as on macOS (radius = half the width)
        ky, r = (H - self.KNOB_H) // 2, self.KNOB_W / 2
        cr.new_sub_path()
        cr.arc(kx + r, ky + r, r, 3.1416, 0)
        cr.arc(kx + r, ky + self.KNOB_H - r, r, 0, 3.1416)
        cr.close_path()
        cr.fill()
        return True


# Text styles for the media view, at priority 900 (above application.css at 800, which gives
# every label 2 px top padding). Sizes match the main view (10pt / 8pt); to change them, add
# font-size to the rules below, e.g. label.media-view-title { font-size: 11pt; }
_VIEW_CSS = Gtk.CssProvider()
_VIEW_CSS.load_from_data(b"""
label.media-time { padding-top: 0px; margin-top: 1px; }   /* bar bottom to digits 7px, knob bottom to digits 4px (Big Sur 11px / 5px x 0.63) */
label.media-view-title { }
/* Elapsed time in primary colour, same as the filled part of the bar (macOS: left time dark /
   white, right time secondary grey). Remaining time keeps .general_desc grey. */
.light-mode label.media-time-elapsed { color: rgba(0, 0, 0, 0.88); }
.dark-mode label.media-time-elapsed { color: rgba(255, 255, 255, 1.0); }
label.media-view-artist { }
""")
_VIEW_CSS_PRIORITY = 900


def _view_css(label, cls):
    label.get_style_context().add_class(cls)
    label.get_style_context().add_provider(_VIEW_CSS, _VIEW_CSS_PRIORITY)


class MediaView(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.app = Gtk.Application.get_default()

        self.set_margin_start(13); self.set_margin_end(13)   # content and separators 15 px from the window edge
        self.get_style_context().remove_class("styledwidgetbox")

        self.is_active = True
        self._user_is_dragging = False
        self._stop = threading.Event()
        self._wake = threading.Event()  # interrupts the wait for an immediate refresh

        self._structure_sig = None   # the UI is only rebuilt when the card structure changes
        self._cards = []             # cards on screen, in order; updated in place
        self._order = None           # player order of the cards; rebuilt when it changes
        self._pixbuf_cache = {}
        self._last_valid_data = []

        self.header_eb = Gtk.EventBox()
        self.header_eb.set_visible_window(False)
        self.header_eb.set_events(Gdk.EventMask.BUTTON_PRESS_MASK)

        self.main_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        # Top gap as in the other views (AirDrop, Wi-Fi): 14
        self.main_container.set_margin_top(VIEW_TOP_GAP); self.main_container.set_margin_bottom(12)
        self.header_eb.add(self.main_container)
        self.pack_start(self.header_eb, True, True, 0)

        self.header_eb.connect("button-press-event", self._on_background_press)
        self.connect("destroy", lambda *_: (self._stop.set(), self._wake.set()))

        threading.Thread(target=self._poll_loop, daemon=True).start()
        self.show_all()

    # main.py sets is_active = True when the view opens. In the background this view doesn't
    # poll the players (CPU), so on opening its cards are from the last time it was open.
    # Before the first draw, cards are corrected from what the media tile already knows
    # (no new queries); the next normal poll refreshes the rest.
    @property
    def is_active(self):
        return getattr(self, "_is_active", False)

    @is_active.setter
    def is_active(self, value):
        was = getattr(self, "_is_active", False)
        self._is_active = value
        if value and not was:
            try:
                self._prime_from_box()
            except Exception:
                pass

    def _prime_from_box(self):
        if not self._cards:
            return
        box = self._find_media_box()
        snap = getattr(box, "_snap", None) if box is not None else None
        if not snap:
            return
        for card in self._cards:
            d = card["data"]
            if d.get("player") != snap["player"]:
                continue
            keys = ("status", "title", "artist", "cover_path")
            if all(d.get(k) == snap[k] for k in keys):
                return
            nd = dict(d)
            nd.update({k: snap[k] for k in keys})
            self._update_card(card, nd)
            return

    def _on_background_press(self, widget, event):
        """Back to the main view only when the strip above/below the cards is clicked."""
        if event.button != 1:
            return False
        eb = self.header_eb.get_allocation()
        mc = self.main_container.get_allocation()
        top = mc.y - eb.y
        if event.y < top or event.y > top + mc.height:
            self._go_back()
            return True
        return False

    def _go_back(self):
        if self.app and hasattr(self.app, 'view_stack') and self.app.view_stack:
            self.app.view_stack.set_visible_child_name("main_view")

    UNKNOWN_TIME = "--:--"   # unknown length (live stream, radio), as on macOS

    def _format_time(self, seconds, total=0):
        """As on macOS (checked on Sonoma): under an hour 01:54; an hour or more 01:43:58,
        10:54:34, 100:54:48 (at least two hour digits). Each time is formatted by its own
        length: minute 9 of a 2-hour video is 09:46. (total is unused, kept for old callers.)"""
        if seconds is None or seconds < 0:
            seconds = 0
        s = int(seconds)
        if s >= 3600:
            return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"
        return f"{s // 60:02d}:{s % 60:02d}"

    def _send_player_cmd(self, player, cmd):
        if player not in ("%any", SHORTWAVE_GHOST):
            get_selector().set_focus(player)

        def worker():
            if player == SHORTWAVE_GHOST:
                # Not visible to playerctl: send the command to Shortwave's D-Bus name directly
                subprocess.run(["dbus-send", "--session", "--type=method_call", f"--dest={SHORTWAVE_BUS}",
                                "/org/mpris/MediaPlayer2", f"org.mpris.MediaPlayer2.Player.{MPRIS_METHODS.get(cmd, 'PlayPause')}"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                send_media_command(player, cmd)
            self._invalidate()  # refresh the cards right away

        threading.Thread(target=worker, daemon=True).start()

    def _invalidate(self, *_):
        """Rebuild the cards on the next poll, and start that poll right away."""
        self._structure_sig = None
        self._wake.set()

    # ── Polling (background thread) ─────────────────────────

    def _poll_loop(self):
        while not self._stop.is_set():
            if self.is_active:
                try:
                    self._poll_once()
                except Exception as e:
                    print(f"Media polling error: {e}")
            self._wake.wait(POLL_INTERVAL)
            self._wake.clear()

    def _list_players(self):
        try:
            out = subprocess.run(["playerctl", "-l"], capture_output=True, text=True, timeout=2).stdout
        except Exception:
            return []
        return [p.strip() for p in out.splitlines()
                if p.strip() and not p.strip().lower().startswith(IGNORED_PLAYERS)]

    def _read_player(self, p):
        """One playerctl call per player."""
        try:
            r = subprocess.run(["playerctl", f"--player={p}", "metadata", "--format", META_FMT],
                               capture_output=True, text=True, timeout=2)
        except Exception:
            return None

        if r.returncode == 0:
            parts = r.stdout.rstrip("\n").split("\t") + [""] * 7
            status, title, artist, length, art_url, pos, url = parts[:7]
        else:
            # The player may be alive without metadata; ask for its status separately
            try:
                s = subprocess.run(["playerctl", f"--player={p}", "status"],
                                   capture_output=True, text=True, timeout=2)
            except Exception:
                return None
            if s.returncode != 0 or not s.stdout.strip():
                return None  # leave out players that don't answer
            status, title, artist, length, art_url, pos, url = s.stdout.strip(), "", "", "", "", "", ""

        status = status.strip().lower()
        if not status:
            return None

        try:
            total_sec = int(length) // 1_000_000
        except ValueError:
            total_sec = 0
        try:
            current_sec = int(pos) // 1_000_000
        except ValueError:
            current_sec = 0
        if is_browser_player(p):
            # YouTube hover preview: keep the real video's title, length and position (the last
            # known values; _gecko_fix below advances the position)
            title, artist, preview = youtube_preview_filter(p, title, artist, url)
            if preview:
                total_sec, current_sec = 0, 0
            if preview or is_gecko_player(p):
                total_sec, current_sec = self._gecko_fix(p, title, status, total_sec, current_sec)

        # Same text rules as the media tile (media_box.media_texts)
        title, artist, search = media_texts(p, title, artist, url)
        return {
            "player": p,
            "status": status,
            "title": title,
            "artist": artist,
            "search": search,
            "can_next": mpris_can(p, "CanGoNext"),
            "can_prev": mpris_can(p, "CanGoPrevious"),
            "current_sec": current_sec,
            "total_sec": total_sec,
            "art_url": art_url,
            "url": url,
            "source": detect_web_source(p, url),
            "app_id": player_desktop_id(p),
        }

    def _gecko_fix(self, p, title, status, total, cur):
        """LibreWolf/Firefox sometimes report length 0 or position 0 for a while, which would
        reset the bar. For the same video, keep the last known length, and while playing
        advance the position by the elapsed time, until the browser sends real values."""
        mem = getattr(self, "_gecko_mem", None)
        if mem is None:
            mem = self._gecko_mem = {}
        now = time.monotonic()
        last = mem.get(p)
        same = last is not None and last["title"] == title
        if total <= 0 and same:
            total = last["total"]
        if cur <= 0 and same and last["cur"] > 0:
            cur = last["cur"] + (int(now - last["t"]) if status == "playing" else 0)
            if total > 0:
                cur = min(cur, total)
            # advance from the last real reading, not from the last estimate
            mem[p] = {"title": title, "total": total, "cur": last["cur"], "t": last["t"]}
            return total, cur
        mem[p] = {"title": title, "total": total, "cur": cur, "t": now}
        return total, cur

    def _shortwave_running(self):
        try:
            return subprocess.run(["pgrep", "-f", "de.haeckerfelix.Shortwave"],
                                  capture_output=True, timeout=2).returncode == 0
        except Exception:
            return False

    def _resolve_cover_path(self, d):
        """Network/file work, runs in the thread. None if there is no image."""
        player = d["player"]
        if player == SHORTWAVE_GHOST or is_browser_player(player):
            return None
        if d.get("search"):
            # Radio: artwork of the current song first; _invalidate refreshes when the lookup ends
            found = itunes_cover_async(*d["search"], on_ready=self._invalidate)
            if found:
                return found
        art = d["art_url"]
        if art.startswith("file://"):
            p = unquote(urlparse(art).path)
            return p if os.path.exists(p) else None
        if art.startswith(("http://", "https://")):
            return download_art(art)
        return None

    def _poll_once(self):
        data = [d for d in (self._read_player(p) for p in self._list_players()) if d]

        if self._shortwave_running() and not any(is_shortwave_player(d["player"]) for d in data):
            data.append({
                "player": SHORTWAVE_GHOST, "status": "stopped",
                "title": _("Stopped Radio Stream"), "artist": _("Shortwave Radio"),
                "search": None, "can_next": True, "can_prev": True,
                "current_sec": 0, "total_sec": 0,
                "art_url": "", "url": "", "source": None,
            })

        leader = get_selector().pick()
        data.sort(key=lambda d: 0 if d["player"] == leader else 1)

        for d in data:
            d["cover_path"] = self._resolve_cover_path(d)

        sig = tuple((d["player"], d["status"], d["title"], d["artist"], d["total_sec"],
                     d["art_url"], d["source"], d["cover_path"], d["can_next"], d["can_prev"]) for d in data)

        self._last_valid_data = data
        if sig != self._structure_sig:
            self._structure_sig = sig
            GLib.idle_add(self._rebuild_media_ui, data)
        else:
            GLib.idle_add(self._update_positions, data)

    # ── UI (main thread) ────────────────────────────────────

    def _update_positions(self, data):
        if self._user_is_dragging or not self._cards:
            return False
        card = self._cards[0]
        for d in data:
            if d["player"] == card["data"]["player"] and card.get("adj") and card["slider"].get_sensitive():
                card["adj"].set_value(min(d["current_sec"], card["adj"].get_upper()))
                card["lbl_c"].set_text(self._format_time(d["current_sec"], card["adj"].get_upper()))
        return False

    def _rebuild_media_ui(self, player_data_list):
        """Updates the cards in place while the player order is the same; rebuilds only when the
        order or count changes. (In GTK3 a new button under the pointer ignores the first click
        until the pointer leaves and re-enters, so rebuilding on every change needed double
        clicks and flickered.)"""
        if self._user_is_dragging:
            self._structure_sig = None  # try again after the drag
            return False

        if not player_data_list:
            player_data_list = [{
                "player": "%any", "status": "stopped",
                "title": IDLE_TITLE, "artist": "",
                "current_sec": 0, "total_sec": 0,
                "art_url": "", "url": "", "source": None, "cover_path": None,
            }]

        order = tuple(d["player"] for d in player_data_list)
        if order == self._order and len(self._cards) == len(player_data_list):
            for card, d in zip(self._cards, player_data_list):
                self._update_card(card, d)
            return False

        # Order changed: rebuild
        self._order = order
        self._cards = []
        for child in self.main_container.get_children():
            child.destroy()
        for idx, d in enumerate(player_data_list):
            card = self._build_big_card() if idx == 0 else self._build_mini_card()
            self._update_card(card, d)
            self._cards.append(card)
            self.main_container.pack_start(card["root"], False, False, 0)
            if idx < len(player_data_list) - 1:
                sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
                sep.set_margin_start(5); sep.set_margin_end(5)
                # Equal visible space above and below the line (~14 px), as in macOS lists.
                # Below the large card it's roomier: on macOS the buttons-to-line gap is ~1.7x
                # that of small cards (33 vs 19 px); the buttons' hit area leaves 13, +13 = 26.
                # Below small cards 11: the theme adds ~3 px around separators, 11 + 3 = 14.
                # The 3 px badge overhang under small covers is not added to the line.
                sep.set_margin_top(13 if idx == 0 else 11 - BADGE_OVER)
                sep.set_margin_bottom(11)
                self.main_container.pack_start(sep, False, False, 0)
        # If the last card is small, its badge overhang is taken off the bottom margin
        self.main_container.set_margin_bottom(12 - BADGE_OVER if len(player_data_list) > 1 else 12)
        self.main_container.show_all()

        if self.app and hasattr(self.app, '_force_single_resize'):
            self.app._force_single_resize()
        return False

    # ── Card parts ──────────────────────────────────────────

    def _button(self, kind, size_key, cmd, card):
        # Button colour as in the media tile (media_box.MediaGlyph: white in dark mode,
        # 80% black in light mode)
        return make_media_button(kind, size_key, lambda: self._send_player_cmd(card["data"]["player"], cmd))

    def _build_common(self, card, text_spacing):
        cover_eb = Gtk.EventBox()
        cover_eb.set_size_request(CARD_COVER, CARD_COVER)
        cover_eb.get_style_context().add_class("modern-media-cover")
        round_cover_bg(cover_eb, CARD_COVER_RADIUS)
        canvas = Gtk.DrawingArea()
        canvas.set_size_request(CARD_COVER, CARD_COVER)
        canvas.connect("draw", lambda w, cr: self._draw_cairo_cover(cr, card.get("pb"), size=CARD_COVER))
        cover_eb.add(canvas)
        cover_eb.connect("button-press-event", lambda w, e: self._on_cover_clicked(e, card["data"]))
        cover_ov, badge = wrap_with_badge(cover_eb, CARD_BADGE)

        text_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=text_spacing)
        text_vbox.set_valign(Gtk.Align.CENTER); text_vbox.set_hexpand(True)
        text_vbox.set_margin_bottom(BADGE_OVER)   # badge overhang below the cover: keeps the text centered on it
        lbl_t = Gtk.Label(xalign=0); lbl_t.get_style_context().add_class("general_title")
        lbl_t.set_ellipsize(Pango.EllipsizeMode.END); lbl_t.set_max_width_chars(20)
        # As in the Big Sur menu: a long title wraps to a second line, then ellipsizes
        # (the small media tile stays on one line, as on macOS)
        lbl_t.set_line_wrap(True); lbl_t.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        lbl_t.set_lines(2)
        lbl_a = Gtk.Label(xalign=0); lbl_a.get_style_context().add_class("general_desc")
        lbl_a.set_ellipsize(Pango.EllipsizeMode.END); lbl_a.set_max_width_chars(24)
        _view_css(lbl_t, "media-view-title")
        _view_css(lbl_a, "media-view-artist")
        tooltip_when_cut(lbl_t)
        tooltip_when_cut(lbl_a)
        lbl_a.set_no_show_all(True)   # hidden when empty, so the title alone is centered
        text_vbox.pack_start(lbl_t, False, False, 0)
        text_vbox.pack_start(lbl_a, False, False, 0)

        card.update({"canvas": canvas, "lbl_t": lbl_t, "lbl_a": lbl_a, "badge": badge})
        return cover_ov, text_vbox

    def _build_big_card(self):
        card = {"big": True, "data": {}}
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        cover_eb, text_vbox = self._build_common(card, 0)

        meta_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=13 - BADGE_OVER)   # cover-to-text 13 (Big Sur 22 px -> 13), minus the badge overhang
        # Cover bottom -> bar top = VIEW_COVER_BAR_GAP. The bar is drawn centered in the knob's
        # height; that offset is subtracted here.
        track_offset = (MediaProgress.KNOB_H - MediaProgress.TRACK_H) // 2
        meta_box.set_margin_bottom(max(0, VIEW_COVER_BAR_GAP - track_offset - BADGE_OVER)); meta_box.set_margin_start(3)
        meta_box.set_margin_end(3)   # text ends where the progress bar ends, as on macOS
        meta_box.pack_start(cover_eb, False, False, 0)
        meta_box.pack_start(text_vbox, True, True, 0)
        vbox.pack_start(meta_box, False, False, 0)

        progress_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        progress_box.set_margin_start(3); progress_box.set_margin_end(3)
        slider = MediaProgress(
            on_press=lambda: setattr(self, "_user_is_dragging", True),
            on_release=lambda v: self._on_slider_released(card["data"]["player"], v),
        )
        adj = slider.adj

        time_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        lbl_c = Gtk.Label(label="00:00", xalign=0); lbl_c.get_style_context().add_class("general_desc")
        lbl_e = Gtk.Label(label="00:00", xalign=0); lbl_e.get_style_context().add_class("general_desc")
        # application.css gives labels 2 px top padding; move the times closer to the bar
        for _l in (lbl_c, lbl_e):
            _view_css(_l, "media-time")
        # Elapsed time in the primary colour (as the filled bar), remaining grey, as on macOS
        _view_css(lbl_c, "media-time-elapsed")

        adj.connect("value-changed", lambda a: lbl_c.set_text(self._format_time(a.get_value(), a.get_upper())) if self._user_is_dragging else None)

        time_hbox.pack_start(lbl_c, False, False, 0)
        time_hbox.pack_end(lbl_e, False, False, 0)
        progress_box.pack_start(slider, True, True, 0)
        progress_box.pack_start(time_hbox, False, False, 0)
        vbox.pack_start(progress_box, False, False, 0)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=GLYPH_SIZES["big"]["gap"])
        controls.set_halign(Gtk.Align.CENTER)
        btn_p, _ = self._button("prev", "big", "previous", card)
        btn_pl, play_img = self._button("play", "big", "play-pause", card)
        btn_n, _ = self._button("next", "big", "next", card)
        for b in (btn_p, btn_pl, btn_n):
            controls.pack_start(b, False, False, 0)
        vbox.pack_start(controls, False, False, 0)

        card.update({"root": vbox, "adj": adj, "slider": slider, "lbl_c": lbl_c, "lbl_e": lbl_e,
                     "play_img": play_img, "btns": [btn_p, btn_pl, btn_n],
                     "btn_prev": btn_p, "btn_next": btn_n})
        return card

    def _build_mini_card(self):
        card = {"big": False, "data": {}}
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=13 - BADGE_OVER)   # same cover-to-text gap as the large card
        hbox.set_margin_start(3); hbox.set_margin_end(0)   # 3: cover lines up with the large card's cover
        cover_eb, text_vbox = self._build_common(card, 1)

        # Measured on macOS (x0.846): ~15 px visible gap between play and next, and next ends
        # ~6 px inside the end of the bar
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=GLYPH_SIZES["mini"]["gap"] + 3)
        buttons.set_valign(Gtk.Align.CENTER)
        buttons.set_margin_end(6)
        buttons.set_margin_bottom(BADGE_OVER)   # centered on the cover
        btn_pl, play_img = self._button("play", "mini", "play-pause", card)
        btn_n, _ = self._button("next", "mini", "next", card)
        buttons.pack_start(btn_pl, False, False, 0)
        buttons.pack_start(btn_n, False, False, 0)

        hbox.pack_start(cover_eb, False, False, 0)
        hbox.pack_start(text_vbox, True, True, 0)
        hbox.pack_end(buttons, False, False, 0)
        card.update({"root": hbox, "play_img": play_img, "btns": [btn_pl, btn_n], "btn_next": btn_n})
        return card

    def _update_card(self, card, d):
        old = card["data"]
        card["data"] = d

        if old.get("title") != d["title"]:
            card["lbl_t"].set_markup(f"<b>{GLib.markup_escape_text(d['title'])}</b>")
        if old.get("artist") != d["artist"]:
            card["lbl_a"].set_text(d["artist"])
        card["lbl_a"].set_visible(bool(d["artist"]))

        cover_key = (d["player"], d.get("cover_path"), d.get("source"))
        if card.get("cover_key") != cover_key:
            card["cover_key"] = cover_key
            card["pb"] = self._pixbuf_for(d)
            # Badge only over real artwork (as on macOS), not over an app icon
            real_cover = bool(d.get("cover_path")) and card["pb"] is not None and \
                card["pb"] is self._pixbuf_cache.get(("file", d["cover_path"], CARD_COVER))
            card["badge"].set_pixbuf(badge_pixbuf(d.get("app_id"), CARD_BADGE) if real_cover else None)
            card["canvas"].queue_draw()

        card["play_img"].set_kind("pause" if d["status"] == "playing" else "play")

        alive = d["player"] != "%any"
        for b in card["btns"]:
            b.set_sensitive(alive)
        # No player: play stays enabled (starts the music app, as on macOS), next/previous dimmed
        card["btns"][1 if card["big"] else 0].set_sensitive(True)
        # Dimmed when the player doesn't support it (e.g. no station skipping in Shortwave)
        card["btn_next"].set_sensitive(alive and d.get("can_next", True))
        if card.get("btn_prev"):
            card["btn_prev"].set_sensitive(alive and d.get("can_prev", True))

        if card["big"]:
            has_len = d["total_sec"] > 0 and d["status"] != "stopped"
            card["slider"].set_sensitive(has_len)
            card["adj"].set_upper(d["total_sec"] if d["total_sec"] > 0 else 100)
            card["adj"].set_value(min(d["current_sec"], card["adj"].get_upper()) if has_len else 0)
            if d["total_sec"] > 0:
                card["lbl_c"].set_text(self._format_time(d["current_sec"] if has_len else 0))
                card["lbl_e"].set_text(self._format_time(d["total_sec"]))
            else:
                # Unknown length (radio, live stream, not reported): --:--, as on macOS
                card["lbl_c"].set_text(self.UNKNOWN_TIME)
                card["lbl_e"].set_text(self.UNKNOWN_TIME)

    # ── Cover/icon (main thread, no subprocesses) ───────────

    def _cache_put(self, key, pb):
        if len(self._pixbuf_cache) > 60:
            self._pixbuf_cache.clear()
        self._pixbuf_cache[key] = pb
        return pb

    def _load_file_pixbuf(self, path, size):
        key = ("file", path, size)
        if key in self._pixbuf_cache:
            return self._pixbuf_cache[key]
        try:
            pb = fit_cover_pixbuf(path, size)
        except Exception:
            return None
        if pb is None:
            return None
        return self._cache_put(key, pb)

    def _load_icon(self, names, size):
        key = ("icon", tuple(names), size)
        if key in self._pixbuf_cache:
            return self._pixbuf_cache[key]
        theme = Gtk.IconTheme.get_default()
        for name in names:
            try:
                if not theme.has_icon(name):
                    continue
                info = theme.lookup_icon(name, size, 0)
                if info:
                    pb = info.load_icon().scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)
                    return self._cache_put(key, pb)
            except Exception:
                continue
        return None

    def _pixbuf_for(self, data):
        player = data["player"]
        if player == "%any":
            pb = self._load_icon(IDLE_ICONS, CARD_ICON)
            if pb:
                return pb
        if data.get("cover_path"):
            pb = self._load_file_pixbuf(data["cover_path"], CARD_COVER)
            if pb:
                return pb
        if is_browser_player(player):
            if is_gecko_player(player):
                # Browser tab: the browser's own icon, like the Safari card on macOS
                pb = app_icon_pixbuf(data.get("app_id"), CARD_ICON) or \
                    self._load_icon(icon_candidates(player), CARD_ICON)
            else:
                pb = self._load_icon(icon_candidates(player, data.get("source")), CARD_ICON)
            if pb:
                return pb
        if is_shortwave_player(player):
            pb = self._load_icon(["de.haeckerfelix.Shortwave", "shortwave"], CARD_ICON)
            if pb:
                return pb
        # No artwork (e.g. a video in Celluloid): the player's own icon, like the TV card on macOS
        pb = app_icon_pixbuf(data.get("app_id"), CARD_ICON)
        if pb:
            return pb
        return self._load_icon(["multimedia-audio-player"], CARD_ICON)

    def _draw_cairo_cover(self, cr, pixbuf, size=CARD_COVER):
        if not pixbuf:
            return True
        radius = CARD_COVER_RADIUS
        cr.new_sub_path()
        cr.arc(size - radius, radius, radius, -1.5708, 0)
        cr.arc(size - radius, size - radius, radius, 0, 1.5708)
        cr.arc(radius, size - radius, radius, 1.5708, 3.1416)
        cr.arc(radius, radius, radius, 3.1416, 4.7124)
        cr.close_path()
        cr.clip()
        w = pixbuf.get_width(); h = pixbuf.get_height()
        Gdk.cairo_set_source_pixbuf(cr, pixbuf, (size - w) / 2, (size - h) / 2)
        cr.paint()
        return True

    # ── Slider ──────────────────────────────────────────────

    def _on_slider_pressed(self, widget, event):
        self._user_is_dragging = True

    def _on_slider_released(self, player, val):

        if self.app:
            self.app._global_lock_player = player
            self.app._global_lock_target_time = float(val)
            self.app._global_lock_timeout = time.time() + 2.5

        def seek_worker():
            try:
                subprocess.run(["playerctl", f"--player={player}", "position", str(int(val))],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass
            self._user_is_dragging = False

        threading.Thread(target=seek_worker, daemon=True).start()
        return False

    # ── Cover click -> same focusing logic as the media tile ─

    def _find_media_box(self):
        try:
            root = self.app.view_stack.get_child_by_name("main_view")
        except Exception:
            return None
        stack = [root]
        while stack:
            w = stack.pop()
            if w is None:
                continue
            if type(w).__name__ == "MediaControlBox":
                return w
            if isinstance(w, Gtk.Container):
                stack.extend(w.get_children())
        return None

    def _on_cover_clicked(self, event, data):
        # Also without a player: the tile's handler starts the music app
        if event.type != Gdk.EventType.BUTTON_PRESS or event.button != 1:
            return False
        if data["player"] != SHORTWAVE_GHOST:
            get_selector().set_focus(data["player"])
        box = self._find_media_box()
        if box is not None:
            box.active_player = data["player"]
            box._on_cover_clicked(None, event)
        return True
