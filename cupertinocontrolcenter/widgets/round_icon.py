"""A white glyph in a circle, as in the macOS Control Center lists (Wi-Fi, Bluetooth, Sound,
Screen Mirroring).

Connected/selected: blue circle (#007AFF); otherwise grey (#808080). The glyph is white, the
same in light and dark mode (same colours as the main view's buttons).

The Wi-Fi glyph is drawn in code, or taken from icons/wifi.png if that file exists; the
others are vector paths stored in this file (sources and licenses below).

Usage:  RoundIcon("headphones", active=True)
Kinds:  wifi (level 1-3), link, laptop, speaker, headphones, earbuds, microphone, monitor, tv,
        phone, keyboard, mouse, gamepad, airdrop (unknown kinds: icons/bluetooth.png),
        "png:<path>" (white glyph from a PNG), "img:<path>" (coloured PNG as it is)
"""
import math
import os

import cairo
import gi

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GdkPixbuf

BLUE = (0.0, 0.478, 1.0, 1.0)
GREY = (0.502, 0.502, 0.502, 1.0)   # #808080: inactive circle, same grey as the main view's buttons
WHITE = (1.0, 1.0, 1.0)
DIM = 0.5   # opacity of unlit Wi-Fi arcs on weak signal (0.3 is barely visible on blue)
PI = math.pi


def rrect(cr, x, y, w, h, r):
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -PI / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, PI / 2)
    cr.arc(x + r, y + h - r, r, PI / 2, PI)
    cr.arc(x + r, y + r, r, PI, 1.5 * PI)
    cr.close_path()


# ─────────────────────────────────────────────────────────────
# Glyphs from three openly licensed sets, converted once from SVG to M/L/C/Z commands (no
# files are read at runtime). Each entry: (viewBox, size ratio in the circle, path).
#   Google Material Symbols (Rounded, 500): laptop (laptop_chromebook), headphones, microphone
#       Apache License 2.0, Copyright Google LLC (licenses/Apache-2.0.txt)
#   Microsoft Fluent UI System Icons: phone, speaker (speaker_2)
#       MIT License, Copyright (c) 2020 Microsoft Corporation (licenses/MIT-Fluent.txt)
#   Phosphor Icons: gamepad
#       MIT License, Copyright (c) 2023 Phosphor Icons (licenses/MIT-Phosphor.txt)
# Keyboard, mouse, link, airdrop and monitor/TV are drawn by this project (CUSTOM below).
# Glyphs with a screen (laptop, TV, phone, monitor) use outlined versions: filled ones look
# too white inside the circle.
# ─────────────────────────────────────────────────────────────

ICONS = {
    "laptop": ([0.0, -960.0, 960.0, 960.0], 0.66, "M 31.43 -154.5 C 22.53 -154.5 15.07 -157.51 9.04 -163.53 C 3.01 -169.56 0 -177.04 0 -185.97 C 0 -194.9 2.98 -202.37 8.94 -208.37 C 14.89 -214.37 22.39 -217.37 31.43 -217.37 L 77.37 -217.37 L 77.37 -777.85 C 77.37 -796.82 84.04 -812.95 97.39 -826.26 C 110.73 -839.57 126.77 -846.22 145.5 -846.22 L 814.5 -846.22 C 833.23 -846.22 849.27 -839.57 862.61 -826.26 C 875.96 -812.95 882.63 -796.82 882.63 -777.85 L 882.63 -217.37 L 928.57 -217.37 C 937.47 -217.37 944.93 -214.31 950.96 -208.19 C 956.99 -202.08 960 -194.55 960 -185.62 C 960 -176.69 957.02 -169.27 951.06 -163.36 C 945.11 -157.45 937.6 -154.5 928.55 -154.5 L 31.43 -154.5 Z M 416.59 -217.37 L 545.41 -217.37 C 551.14 -217.37 556 -219.37 560 -223.37 C 564 -227.37 566 -232.37 566 -238.37 C 566 -244.37 564 -249.37 560 -253.37 C 556 -257.37 551.14 -259.37 545.41 -259.37 L 416.59 -259.37 C 410.86 -259.37 406 -257.37 402 -253.37 C 398 -249.37 396 -244.37 396 -238.37 C 396 -232.37 398 -227.37 402 -223.37 C 406 -219.37 410.86 -217.37 416.59 -217.37 Z M 149.5 -322 L 818.5 -322 L 818.5 -777.85 L 149.5 -777.85 L 149.5 -322 Z M 484 -550.04 Z"),   # material laptop_chromebook
    "speaker": ([0.0, 0.0, 24.0, 24.0], 0.62, "M 15 4.25 L 15 19.75 C 15 19.94 14.95 20.13 14.87 20.3 C 14.78 20.47 14.66 20.62 14.51 20.74 C 14.35 20.86 14.18 20.94 13.99 20.97 C 13.8 21.01 13.6 21 13.42 20.95 C 13.23 20.9 13.06 20.81 12.92 20.68 L 8.42 16.68 C 8.35 16.62 8.27 16.58 8.19 16.55 C 8.11 16.52 8.02 16.5 7.93 16.5 L 4.25 16.5 C 3.01 16.5 2 15.49 2 14.25 L 2 9.75 C 2 8.5 3 7.5 4.25 7.5 L 7.93 7.5 C 8.12 7.5 8.28 7.44 8.43 7.31 L 12.92 3.31 C 13.72 2.6 15 3.17 15 4.25 M 19 5.9 C 19.1 5.82 19.23 5.77 19.36 5.76 C 19.49 5.74 19.62 5.76 19.74 5.81 C 19.86 5.87 19.96 5.95 20.04 6.05 C 20.89 7.19 21.48 8.5 21.78 9.89 C 22.08 11.28 22.08 12.72 21.78 14.11 C 21.48 15.5 20.89 16.81 20.04 17.95 C 19.96 18.06 19.86 18.14 19.73 18.19 C 19.61 18.24 19.48 18.26 19.35 18.24 C 19.22 18.23 19.1 18.18 18.99 18.1 C 18.88 18.02 18.8 17.92 18.75 17.79 C 18.7 17.67 18.68 17.54 18.7 17.41 C 18.71 17.28 18.76 17.16 18.84 17.05 C 19.56 16.08 20.06 14.97 20.31 13.79 C 20.57 12.61 20.57 11.39 20.31 10.21 C 20.06 9.03 19.56 7.92 18.84 6.95 C 18.76 6.84 18.71 6.72 18.7 6.59 C 18.68 6.46 18.7 6.33 18.75 6.21 C 18.8 6.08 18.88 5.98 18.99 5.9 M 17.13 8.37 C 17.22 8.32 17.31 8.29 17.41 8.28 C 17.51 8.27 17.61 8.28 17.7 8.31 C 17.8 8.34 17.89 8.38 17.96 8.44 C 18.04 8.51 18.1 8.58 18.15 8.67 C 18.7 9.69 18.99 10.84 18.99 12 C 18.99 13.16 18.7 14.31 18.15 15.33 C 18.09 15.45 17.99 15.54 17.88 15.61 C 17.77 15.68 17.64 15.72 17.51 15.72 C 17.38 15.72 17.25 15.69 17.13 15.63 C 17.01 15.57 16.92 15.47 16.85 15.36 C 16.78 15.25 16.74 15.12 16.74 14.99 C 16.74 14.86 16.77 14.73 16.83 14.61 C 17.26 13.81 17.49 12.91 17.49 12 C 17.49 11.09 17.26 10.19 16.83 9.39 C 16.78 9.3 16.75 9.21 16.74 9.11 C 16.73 9.01 16.74 8.91 16.77 8.82 C 16.8 8.72 16.84 8.63 16.9 8.56 C 16.97 8.48 17.04 8.42 17.13 8.37"),   # fluent speaker_2
    "phone": ([0.0, 0.0, 24.0, 24.0], 0.66, "M 15.75 2 C 16.99 2 18 3 18 4.25 L 18 19.75 C 18 20.99 17 22 15.75 22 L 8.25 22 C 7.01 22 6 21 6 19.75 L 6 4.25 C 6 3.01 7 2 8.25 2 Z M 15.75 3.5 L 8.25 3.5 C 8.12 3.5 7.99 3.53 7.88 3.6 C 7.76 3.67 7.67 3.76 7.6 3.88 C 7.53 3.99 7.5 4.12 7.5 4.25 L 7.5 19.75 C 7.5 20.16 7.84 20.5 8.25 20.5 L 15.75 20.5 C 16.16 20.5 16.5 20.16 16.5 19.75 L 16.5 4.25 C 16.5 4.12 16.47 3.99 16.4 3.88 C 16.33 3.76 16.24 3.67 16.12 3.6 C 16.01 3.53 15.88 3.5 15.75 3.5 M 13.25 17.5 C 13.38 17.5 13.51 17.53 13.62 17.6 C 13.74 17.67 13.83 17.76 13.9 17.88 C 13.97 17.99 14 18.12 14 18.25 C 14 18.38 13.97 18.51 13.9 18.62 C 13.83 18.74 13.74 18.83 13.62 18.9 C 13.51 18.97 13.38 19 13.25 19 L 10.75 19 C 10.62 19 10.49 18.97 10.38 18.9 C 10.26 18.83 10.17 18.74 10.1 18.62 C 10.03 18.51 10 18.38 10 18.25 C 10 18.12 10.03 17.99 10.1 17.88 C 10.17 17.76 10.26 17.67 10.38 17.6 C 10.49 17.53 10.62 17.5 10.75 17.5 Z"),   # fluent
    "headphones": ([0.0, -960.0, 960.0, 960.0], 0.66, "M 264.9 -114.5 L 182.1 -114.5 C 163.8 -114.5 147.8 -121.3 134.3 -134.8 C 120.8 -148.3 114 -164.2 114 -182.6 L 114 -480 C 114 -530.8 123.6 -578.4 142.9 -622.8 C 162.1 -667.2 188.2 -705.8 221.2 -738.8 C 254.2 -771.8 292.8 -797.9 337.2 -817.2 C 381.6 -836.6 429.2 -846.2 480 -846.2 C 530.8 -846.2 578.4 -836.6 622.8 -817.2 C 667.2 -797.9 705.8 -771.8 738.8 -738.8 C 771.8 -705.8 797.9 -667.2 817.2 -622.8 C 836.6 -578.4 846.2 -530.8 846.2 -480 L 846.2 -182.6 C 846.2 -164.2 839.4 -148.3 825.8 -134.8 C 812.2 -121.3 796.2 -114.5 777.9 -114.5 L 695.4 -114.5 C 676.3 -114.5 660.1 -121.1 646.9 -134.4 C 633.6 -147.6 627 -163.7 627 -182.6 L 627 -353.4 C 627 -372.3 633.6 -388.4 646.9 -401.6 C 660.1 -414.9 676.3 -421.5 695.4 -421.5 L 777.9 -421.5 L 777.9 -480 C 777.9 -562.7 748.9 -633 691 -691 C 633 -748.9 562.7 -777.9 480 -777.9 C 397.3 -777.9 327 -748.9 269.1 -691 C 211.1 -633 182.2 -562.7 182.2 -480 L 182.2 -421.5 L 264.9 -421.5 C 283.8 -421.5 299.9 -414.9 313.1 -401.6 C 326.4 -388.4 333 -372.3 333 -353.4 L 333 -182.6 C 333 -163.7 326.4 -147.7 313.1 -134.4 C 299.9 -121.1 283.8 -114.5 264.9 -114.5 Z"),
    "microphone": ([0.0, -960.0, 960.0, 960.0], 0.62, "M 403.22 -447.7 C 382.61 -469.59 372.3 -496.21 372.3 -527.57 L 372.3 -777.37 C 372.3 -806.9 382.75 -832.07 403.66 -852.87 C 424.56 -873.67 449.99 -884.07 479.94 -884.07 C 509.89 -884.07 535.38 -873.67 556.4 -852.87 C 577.42 -832.07 587.93 -806.9 587.93 -777.37 L 587.93 -527.57 C 587.93 -496.21 577.59 -469.59 556.9 -447.7 C 536.21 -425.81 510.58 -414.87 480 -414.87 C 449.42 -414.87 423.83 -425.81 403.22 -447.7 Z M 447.37 -141.87 L 447.37 -245.72 C 381.62 -253.05 324.72 -279.52 276.68 -325.13 C 228.65 -370.74 200.72 -427 192.91 -493.93 C 191.42 -503.14 193.77 -511.05 199.98 -517.66 C 206.19 -524.27 213.99 -527.57 223.4 -527.57 C 232.8 -527.57 240.65 -524.29 246.96 -517.73 C 253.26 -511.17 257.24 -503.16 258.89 -493.7 C 267.37 -439.22 292.79 -394.77 335.16 -360.36 C 377.52 -325.95 425.68 -308.74 479.65 -308.74 C 534.52 -308.74 583.03 -325.95 625.17 -360.36 C 667.32 -394.77 692.71 -439.22 701.35 -493.7 C 702.84 -503.16 706.8 -511.17 713.22 -517.73 C 719.64 -524.29 727.52 -527.57 736.87 -527.57 C 746.22 -527.57 753.92 -524.23 759.99 -517.54 C 766.06 -510.85 768.42 -502.91 767.09 -493.7 C 759.12 -427.59 731.32 -371.57 683.7 -325.63 C 636.07 -279.69 579.05 -253.05 512.63 -245.72 L 512.63 -141.87 C 512.63 -132.57 509.46 -124.77 503.12 -118.46 C 496.78 -112.15 489.01 -109 479.82 -109 C 470.63 -109 462.93 -112.15 456.71 -118.46 C 450.48 -124.77 447.37 -132.57 447.37 -141.87 Z"),   # material
    "gamepad": ([0.0, 0.0, 256.0, 256.0], 0.66, "M 247.44 173.75 C 247.44 173.7 247.44 173.66 247.44 173.61 L 231.05 89.44 C 231.05 89.38 231.05 89.32 231.05 89.26 C 229.37 80.1 225.59 71.45 220 64.01 C 214.4 56.56 207.16 50.51 198.83 46.35 C 190.5 42.18 181.31 40.01 172 40 L 83.89 40 C 74.55 40 65.34 42.18 57 46.37 C 48.65 50.56 41.4 56.64 35.83 64.14 C 30.25 71.63 26.51 80.32 24.89 89.52 L 8.58 173.61 C 8.58 173.66 8.58 173.7 8.58 173.75 C 7.52 179.76 8.01 185.95 10 191.72 C 11.98 197.49 15.41 202.66 19.94 206.75 C 24.48 210.83 29.98 213.7 35.93 215.07 C 41.88 216.44 48.08 216.28 53.95 214.6 C 59.82 212.92 65.16 209.77 69.48 205.46 L 69.83 205.09 L 109.52 160 L 146.52 160 L 186.23 205.09 C 186.34 205.22 186.46 205.34 186.58 205.46 C 189.92 208.79 193.89 211.44 198.25 213.25 C 202.61 215.06 207.28 215.99 212 216 C 217.25 216 222.45 214.84 227.21 212.62 C 231.97 210.4 236.18 207.16 239.56 203.13 C 242.94 199.11 245.39 194.39 246.75 189.31 C 248.11 184.24 248.34 178.92 247.43 173.75 Z M 104 112 L 96 112 L 96 120 C 96 121.4 95.63 122.78 94.93 124 C 94.23 125.22 93.22 126.23 92 126.93 C 90.78 127.63 89.4 128 88 128 C 86.6 128 85.22 127.63 84 126.93 C 82.78 126.23 81.77 125.22 81.07 124 C 80.37 122.78 80 121.4 80 120 L 80 112 L 72 112 C 70.6 112 69.22 111.63 68 110.93 C 66.78 110.23 65.77 109.22 65.07 108 C 64.37 106.78 64 105.4 64 104 C 64 102.6 64.37 101.22 65.07 100 C 65.77 98.78 66.78 97.77 68 97.07 C 69.22 96.37 70.6 96 72 96 L 80 96 L 80 88 C 80 86.6 80.37 85.22 81.07 84 C 81.77 82.78 82.78 81.77 84 81.07 C 85.22 80.37 86.6 80 88 80 C 89.4 80 90.78 80.37 92 81.07 C 93.22 81.77 94.23 82.78 94.93 84 C 95.63 85.22 96 86.6 96 88 L 96 96 L 104 96 C 105.4 96 106.78 96.37 108 97.07 C 109.22 97.77 110.23 98.78 110.93 100 C 111.63 101.22 112 102.6 112 104 C 112 105.4 111.63 106.78 110.93 108 C 110.23 109.22 109.22 110.23 108 110.93 C 106.78 111.63 105.4 112 104 112 Z M 144 104 C 144 102.6 144.37 101.22 145.07 100 C 145.77 98.78 146.78 97.77 148 97.07 C 149.22 96.37 150.6 96 152 96 L 176 96 C 177.4 96 178.78 96.37 180 97.07 C 181.22 97.77 182.23 98.78 182.93 100 C 183.63 101.22 184 102.6 184 104 C 184 105.4 183.63 106.78 182.93 108 C 182.23 109.22 181.22 110.23 180 110.93 C 178.78 111.63 177.4 112 176 112 L 152 112 C 150.6 112 149.22 111.63 148 110.93 C 146.78 110.23 145.77 109.22 145.07 108 C 144.37 106.78 144 105.4 144 104 Z M 228.37 191.47 C 226.87 193.63 224.96 195.47 222.75 196.88 C 220.53 198.29 218.06 199.25 215.47 199.7 C 212.34 200.25 209.12 200.04 206.09 199.11 C 203.05 198.17 200.28 196.53 198 194.31 L 167.8 160 L 172 160 C 182.21 160 192.26 157.4 201.18 152.43 C 210.11 147.46 217.62 140.3 223 131.62 L 231.74 176.62 C 232.19 179.2 232.12 181.83 231.54 184.38 C 230.96 186.93 229.88 189.34 228.37 191.47 Z"),   # phosphor
}
ICONS["earbuds"] = ICONS["headphones"]
_parsed = {}


def _ops(kind):
    if kind not in _parsed:
        out, tok = [], ICONS[kind][2].split()
        i = 0
        while i < len(tok):
            c = tok[i]
            n = {"M": 2, "L": 2, "C": 6, "Z": 0}[c]
            out.append((c, [float(v) for v in tok[i + 1:i + 1 + n]]))
            i += 1 + n
        _parsed[kind] = out
    return _parsed[kind]


# Screen area of glyphs that have one, in the glyph's viewBox units: x, y, w, h, radius
TINT_RECTS = {"phone": (7.5, 3.5, 9.0, 17.0, 1.3),
              "laptop": (150, -778, 668, 560, 18)}


# Vertical optical centering (px). The headphones are measured-centered, but their weight
# (the ear cups) sits low, so they look too low without this.
NUDGE_Y = {"headphones": -0.7}


def draw_icon(cr, kind):
    (vx, vy, vw, vh), ratio, _ = ICONS[kind]
    px = 24 * ratio
    cr.save()
    cr.translate(12 - px / 2, 12 - px / 2 + NUDGE_Y.get(kind, 0))
    cr.scale(px / vw, px / vh)
    cr.translate(-vx, -vy)
    if kind in TINT_RECTS:
        src = cr.get_source()
        cr.set_source_rgba(1, 1, 1, SCREEN_TINT)
        rrect(cr, *TINT_RECTS[kind])
        cr.fill()
        cr.set_source(src)
    cr.new_path()
    for c, v in _ops(kind):
        if c == "M":
            cr.move_to(*v)
        elif c == "L":
            cr.line_to(*v)
        elif c == "C":
            cr.curve_to(*v)
        else:
            cr.close_path()
    cr.fill()
    cr.restore()


def g_keyboard(cr):
    # Keyboard: filled rounded body, two rows of evenly spaced keys and a space bar,
    # cut out of the body (the circle's colour shows through).
    x0, y0, w, h = 4.2, 7.0, 15.6, 10.0
    rrect(cr, x0, y0, w, h, 2.3)
    pad, key, n = 1.6, 1.6, 5
    left, right = x0 + pad, x0 + w - pad
    gap = (right - left - n * key) / (n - 1)
    rows = (y0 + 1.5, y0 + 1.5 + key + 1.0, y0 + 1.5 + 2 * (key + 1.0))
    for y in rows[:2]:
        for i in range(n):
            rrect(cr, left + i * (key + gap), y, key, key, 0.4)
    y = rows[2]
    rrect(cr, left + key + gap, y, right - left - 2 * (key + gap), key, 0.6)   # space bar
    cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
    cr.fill()
    cr.set_fill_rule(cairo.FILL_RULE_WINDING)


def g_mouse(cr):
    # Mouse: one filled capsule (no button split, like Apple mice), with a small wheel slot
    # so it doesn't read as a plain oval
    rrect(cr, 8.4, 5.0, 7.2, 14.0, 3.6)
    rrect(cr, 11.3, 7.4, 1.4, 2.8, 0.7)
    cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
    cr.fill()
    cr.set_fill_rule(cairo.FILL_RULE_WINDING)


def g_link(cr):
    # Personal hotspot: two interlocking links, offset diagonally so it doesn't look like the
    # usual web "link" icon
    cr.set_line_width(1.5)
    w, h = 9.0, 5.4
    rrect(cr, 12 - w + 1.7, 12 - h / 2 - 1.3, w, h, h / 2)
    cr.stroke()
    rrect(cr, 12 - 1.7, 12 - h / 2 + 1.3, w, h, h / 2)
    cr.stroke()


# Monitor/TV glyph proportions: fills ~65% of a 24 px circle, 1.35 px strokes, centered.
SW = 1.35


def _frame(cr, x0, y0, x1, y1, r):
    """Frame whose outer edge is (x0,y0)-(x1,y1); the stroke grows inwards."""
    h = SW / 2
    rrect(cr, x0 + h, y0 + h, x1 - x0 - SW, y1 - y0 - SW, max(0.1, r - h))
    cr.set_line_width(SW)
    cr.stroke()


# Faint white inside screens, so they read as a screen rather than an empty frame
SCREEN_TINT = 0.16


def _screen_tint(cr, x0, y0, x1, y1, r):
    src = cr.get_source()
    cr.set_source_rgba(1, 1, 1, SCREEN_TINT)
    rrect(cr, x0 + SW, y0 + SW, x1 - x0 - 2 * SW, y1 - y0 - 2 * SW, max(0.1, r - SW))
    cr.fill()
    cr.set_source(src)


def g_display(cr):
    # Monitor / TV: landscape frame with a separate stand below (~60% of the width),
    # slightly taller so it doesn't look squat, and a little low for optical centering.
    _screen_tint(cr, 4.35, 5.9, 19.65, 16.6, 1.0)
    _frame(cr, 4.35, 5.9, 19.65, 16.6, 1.0)
    rrect(cr, 7.4, 17.5, 9.2, 1.35, 0.65)
    cr.fill()


def g_airdrop(cr):
    # AirDrop: small ring in the middle, two open rings around it and a thin tower below,
    # drawn for 24 px (thick strokes, wide gaps). Outer width matches the Wi-Fi glyph (~16 px).
    cx, cy = 12.0, 12.3                # ring center, optically centered
    gap = 0.5                          # half-angle of the opening at the bottom (radians)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_width(1.35)
    cr.new_sub_path()
    cr.arc(cx, cy, 2.0, 0, 2 * PI)     # small center ring
    cr.stroke()
    cr.set_line_width(1.45)
    for r in (4.9, 7.6):
        cr.new_sub_path()
        cr.arc(cx, cy, r, PI / 2 + gap, PI / 2 - gap + 2 * PI)
        cr.stroke()
    # Tower: thin triangle widening downwards from the center ring
    cr.move_to(cx, cy + 2.2)
    cr.line_to(cx + 1.9, cy + 7.8)
    cr.line_to(cx - 1.9, cy + 7.8)
    cr.close_path()
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_line_width(0.7)
    cr.fill_preserve()
    cr.stroke()


CUSTOM = {"keyboard": g_keyboard, "mouse": g_mouse, "link": g_link, "airdrop": g_airdrop,
          "monitor": g_display, "tv": g_display}


def g_wifi_drawn(cr, cut, level, fg):
    # Fallback Wi-Fi (when icons/wifi.png can't be read): a wedge and two arcs
    cx, cy = 12, 12 + 5.7
    half = math.radians(48)
    a0, a1 = -PI / 2 - half, -PI / 2 + half
    for i, (r0, r1) in enumerate(((0, 3.4), (5.2, 7.5), (9.1, 11.3))):
        cr.set_source_rgba(*fg, 1.0 if i < level else DIM)
        if r0 == 0:
            cr.move_to(cx, cy)
            cr.arc(cx, cy, r1, a0, a1)
            cr.close_path()
            cr.fill()
        else:
            rm, hw = (r0 + r1) / 2, (r1 - r0) / 2
            cr.set_line_width(2 * hw)
            cr.arc(cx, cy, rm, a0 + hw / rm, a1 - hw / rm)
            cr.stroke()


# ─────────────────────────────────────────────────────────────
# Wi-Fi: three layers (dot, middle arc, outer arc) cut from icons/wifi.png
# ─────────────────────────────────────────────────────────────

WIFI_PNG = "icons/wifi.png"
_wifi_cache = None


def _wifi_layers(size):
    """Turns the white glyph in wifi.png (on a blue circle) into a mask and splits it into
    three parts by distance from the tip, so it can be drawn in any colour and the outer
    arcs can be dimmed on a weak signal."""
    global _wifi_cache
    if _wifi_cache is not None:
        return _wifi_cache
    _wifi_cache = False
    if not os.path.exists(WIFI_PNG):
        return _wifi_cache       # no PNG: the drawn glyph (g_wifi_drawn) is used
    try:
        pb = GdkPixbuf.Pixbuf.new_from_file(WIFI_PNG)
        if pb.get_width() != size or pb.get_height() != size:
            pb = pb.scale_simple(size, size, GdkPixbuf.InterpType.HYPER)
        w, h, rs, n = pb.get_width(), pb.get_height(), pb.get_rowstride(), pb.get_n_channels()
        px = pb.get_pixels()
        ring = px[(h // 2) * rs + 1 * n]   # the circle's red channel (~0 on blue)
        pts = []
        for y in range(h):
            for x in range(w):
                i = y * rs + x * n
                a = (px[i + 3] / 255 if n == 4 else 1.0) * \
                    max(0.0, min(1.0, (px[i] - ring) / max(1, 255 - ring)))
                if a > 0.02:
                    pts.append((x, y, a))
        if not pts:
            return False
        bottom = max(y for x, y, a in pts if a > 0.5)
        row = [(x, a) for x, y, a in pts if y == bottom]
        tx = sum(x * a for x, a in row) / sum(a for x, a in row) + 0.5
        ty = bottom + 1.0

        def dist(x, y):
            return math.hypot(x + 0.5 - tx, y + 0.5 - ty)

        dmax = max(dist(x, y) for x, y, a in pts)
        hist = {}
        for x, y, a in pts:
            b = round(dist(x, y) * 2) / 2
            hist[b] = hist.get(b, 0) + a

        def valley(lo, hi):
            cand = [k / 2 for k in range(int(lo * 2), int(hi * 2) + 1)]
            return min(cand, key=lambda d: hist.get(d, 0))

        t1 = valley(dmax * 0.25, dmax * 0.55)
        t2 = valley(t1 + 1.5, dmax * 0.9)
        stride = cairo.ImageSurface.format_stride_for_width(cairo.FORMAT_A8, w)
        layers = []
        for lo, hi in ((-1, t1), (t1, t2), (t2, 1e9)):
            buf = bytearray(stride * h)
            for x, y, a in pts:
                if lo < dist(x, y) <= hi:
                    buf[y * stride + x] = int(round(a * 255))
            layers.append(cairo.ImageSurface.create_for_data(buf, cairo.FORMAT_A8, w, h, stride))
        _wifi_cache = layers
    except Exception as e:
        print(f"Could not read the Wi-Fi icon, using the drawn one: {e}")
    return _wifi_cache


BT_PNG = "icons/bluetooth.png"   # the main view's Bluetooth icon
RED = (0.894, 0.0, 0.063, 1.0)        # the red of icons/presentation.png
DARK_GREY = (0.502, 0.502, 0.502, 1.0)  # grey of inactive buttons (main view, Display view)
_png_cache = {}


def _png_glyph(path, boost=1.0, center=True):
    """Turns the white glyph of a PNG (on a coloured circle) into a mask, so it can be drawn
    in any circle colour and size. Kept at the file's resolution (scaling large PNGs down
    first makes jagged edges).
    boost: pushes semi-transparent strokes towards white; 1 = unchanged.
    center: moves the glyph to the exact center (some files are off by half a pixel).
    Returns (mask, width, height, dx, dy)."""
    key = (path, boost, center)
    if key in _png_cache:
        return _png_cache[key]
    _png_cache[key] = None
    try:
        pb = GdkPixbuf.Pixbuf.new_from_file(path)
        w, h, rs, n = pb.get_width(), pb.get_height(), pb.get_rowstride(), pb.get_n_channels()
        px = pb.get_pixels()
        # Circle colour: a point near the top center. The best channel to separate the glyph
        # is the circle's darkest one (red on blue, green on red); white has 255 there.
        i0 = max(1, h // 10) * rs + (w // 2) * n
        ring = px[i0:i0 + 3]
        ch = min(range(3), key=lambda c: ring[c])
        rc = ring[ch]
        stride = cairo.ImageSurface.format_stride_for_width(cairo.FORMAT_A8, w)
        buf = bytearray(stride * h)
        x0, y0, x1, y1 = w, h, -1, -1
        for y in range(h):
            for x in range(w):
                i = y * rs + x * n
                a = (px[i + 3] / 255 if n == 4 else 1.0) * \
                    min(1.0, max(0.0, (px[i + ch] - rc) / max(1, 255 - rc)) * boost)
                v = int(round(a * 255))
                buf[y * stride + x] = v
                if v > 60:
                    x0, y0, x1, y1 = min(x0, x), min(y0, y), max(x1, x), max(y1, y)
        dx = dy = 0
        if center and x1 >= 0:
            dx = w / 2 - (x0 + x1 + 1) / 2
            dy = h / 2 - (y0 + y1 + 1) / 2
        surf = cairo.ImageSurface.create_for_data(buf, cairo.FORMAT_A8, w, h, stride)
        _png_cache[key] = (surf, w, h, dx, dy)
    except Exception as e:
        print(f"Could not read {path}: {e}")
    return _png_cache[key]


def _draw_png_glyph(cr, path, s, scale=1.0, boost=1.0, center=True):
    g = _png_glyph(path, boost, center)
    if not g:
        return
    surf, w, h, dx, dy = g
    cr.save()
    # scale: glyph size in the circle (1 = as in the file), scaled around the center
    cr.translate(s / 2, s / 2)
    cr.scale(scale, scale)
    cr.translate(-s / 2, -s / 2)
    cr.scale(s / w, s / h)
    pat = cairo.SurfacePattern(surf)
    pat.set_filter(cairo.FILTER_BEST)
    m = cairo.Matrix()
    m.translate(-dx, -dy)
    pat.set_matrix(m)
    cr.mask(pat)
    cr.restore()


_img_cache = {}


def _img_pair(path, off_rgb, off_ref=None):
    """Uses a coloured PNG as it is for "on" and makes a grey version for "off": the circle
    turns grey, and every tone between the circle and white moves to the same place between
    grey and white. If off_ref is given (a grey drawing of the same size), the chart colours
    come from it. Returns (on surface, off surface, width, height)."""
    key = (path, off_rgb, off_ref)
    if key in _img_cache:
        return _img_cache[key]
    _img_cache[key] = None
    try:
        pb = GdkPixbuf.Pixbuf.new_from_file(path)
        if not pb.get_has_alpha():
            pb = pb.add_alpha(False, 0, 0, 0)
        w, h, rs = pb.get_width(), pb.get_height(), pb.get_rowstride()
        px = pb.get_pixels()
        ref = None
        if off_ref:
            try:
                rp = GdkPixbuf.Pixbuf.new_from_file(off_ref)
                if not rp.get_has_alpha():
                    rp = rp.add_alpha(False, 0, 0, 0)
                if (rp.get_width(), rp.get_height()) == (w, h):
                    ref, rrs = rp.get_pixels(), rp.get_rowstride()
            except Exception:
                ref = None
        i0 = max(1, h // 10) * rs + (w // 2) * 4
        ring = px[i0:i0 + 3]
        ch = min(range(3), key=lambda c: ring[c])      # green channel on a red circle
        rc = ring[ch]
        # The chart: pure white pixels (the frame is half-tone); its surroundings are kept
        white = [(x, y) for y in range(h) for x in range(w)
                 if px[y * rs + x * 4 + 3] > 200 and min(px[y * rs + x * 4:y * rs + x * 4 + 3]) > 235]
        if white:
            cx = sum(p[0] for p in white) / len(white)
            cy = sum(p[1] for p in white) / len(white)
            rad = max(math.hypot(x - cx, y - cy) for x, y in white) + 2.0
        else:
            cx = cy = rad = -1
        stride = cairo.ImageSurface.format_stride_for_width(cairo.FORMAT_ARGB32, w)
        on, off = bytearray(stride * h), bytearray(stride * h)
        for y in range(h):
            for x in range(w):
                i = y * rs + x * 4
                r, g, b, a = px[i], px[i + 1], px[i + 2], px[i + 3]
                o = y * stride + x * 4
                # cairo: premultiplied, B G R A in memory
                on[o:o + 4] = bytes((b * a // 255, g * a // 255, r * a // 255, a))
                t = max(0.0, min(1.0, ((r, g, b)[ch] - rc) / max(1, 255 - rc)))
                # Map each tone between the circle colour and white to the same place between
                # grey and white
                nr, ng, nb = (int(round(c * (1 - t) + 255 * t)) for c in off_rgb)
                off[o:o + 4] = bytes((nb * a // 255, ng * a // 255, nr * a // 255, a))
        fmt = cairo.FORMAT_ARGB32
        _img_cache[key] = (cairo.ImageSurface.create_for_data(on, fmt, w, h, stride),
                           cairo.ImageSurface.create_for_data(off, fmt, w, h, stride), w, h)
    except Exception as e:
        print(f"Could not read {path}: {e}")
    return _img_cache[key]


def _draw_no_internet(cr, layers, ring):
    """Connected without internet: full white Wi-Fi with an exclamation mark through it."""
    for surf in layers:
        cr.set_source_rgba(*WHITE, 1.0)
        cr.mask_surface(surf, 0, 0)
    # Cut the arcs around the mark (in the circle colour) so it stands out
    cr.set_source_rgba(*ring)
    rrect(cr, 10.0, 4.4, 4.0, 10.0, 2.0)
    cr.fill()
    cr.arc(12, 16.6, 2.2, 0, 2 * PI)
    cr.fill()
    cr.set_source_rgba(*WHITE, 1.0)
    rrect(cr, 11.05, 5.2, 1.9, 8.4, 0.95)
    cr.fill()
    cr.arc(12, 16.6, 1.15, 0, 2 * PI)
    cr.fill()


class RoundIcon(Gtk.DrawingArea):
    """kind: see the module docstring.
    size: circle diameter (24 in lists). on_color / off_color: circle colour when active /
    inactive. no_internet: Wi-Fi connected without internet (exclamation mark)."""
    SIZE = 24

    def __init__(self, kind, active=False, level=3, size=24, on_color=BLUE, off_color=GREY,
                 png_scale=1.0, png_boost=1.0, png_center=True, img_off_ref=None):
        super().__init__()
        self.kind, self.active, self.level = kind, active, level
        self.size, self.on_color, self.off_color = size, on_color, off_color
        self.png_opts = (png_scale, png_boost, png_center)
        self.img_off_ref = img_off_ref
        self.no_internet = False
        # No own GdkWindow: in GTK3 a DrawingArea's window swallows clicks (a Gtk.Image has
        # none). This way clicks reach the EventBox around it (toggles, list rows).
        self.set_has_window(False)
        self.set_size_request(size, size)
        self.set_valign(Gtk.Align.CENTER)
        self.connect("draw", self._draw)

    def set_active(self, active):
        if active != self.active:
            self.active = active
            self.queue_draw()

    def set_state(self, active=None, level=None, no_internet=None):
        changed = False
        for name, val in (("active", active), ("level", level), ("no_internet", no_internet)):
            if val is not None and getattr(self, name) != val:
                setattr(self, name, val)
                changed = True
        if changed:
            self.queue_draw()

    def _draw(self, w, cr):
        s = self.size
        a = self.get_allocation()
        cr.translate((a.width - s) / 2, (a.height - s) / 2)
        ring = self.on_color if self.active else self.off_color
        cr.set_source_rgba(*ring)
        cr.arc(s / 2, s / 2, s / 2, 0, 2 * PI)
        cr.fill()
        cr.set_source_rgba(*WHITE, 1.0)
        if self.kind.startswith("img:"):
            # Coloured PNG as it is, clipped slightly inside the circle drawn above (smooth edge)
            off = tuple(int(round(c * 255)) for c in self.off_color[:3])
            pair = _img_pair(self.kind[4:], off, self.img_off_ref)
            if pair:
                on_s, off_s, iw, ih = pair
                cr.save()
                cr.arc(s / 2, s / 2, s / 2 - 0.8, 0, 2 * PI)
                cr.clip()
                cr.scale(s / iw, s / ih)
                pat = cairo.SurfacePattern(on_s if self.active else off_s)
                pat.set_filter(cairo.FILTER_BEST)
                cr.set_source(pat)
                cr.paint()
                cr.restore()
            return False
        if self.kind.startswith("png:"):
            _draw_png_glyph(cr, self.kind[4:], s, *self.png_opts)
            return False
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.scale(s / 24.0, s / 24.0)
        if self.kind == "wifi":
            layers = _wifi_layers(24)
            if layers and self.no_internet:
                _draw_no_internet(cr, layers, ring)
            elif layers:
                for i, surf in enumerate(layers):
                    cr.set_source_rgba(*WHITE, 1.0 if i < self.level else DIM)
                    cr.mask_surface(surf, 0, 0)
            else:
                g_wifi_drawn(cr, ring, self.level, WHITE)
            return False
        if self.kind in CUSTOM:
            CUSTOM[self.kind](cr)
        elif self.kind in ICONS:
            draw_icon(cr, self.kind)
        else:
            # Unknown Bluetooth device type: the main view's Bluetooth glyph
            cr.scale(24.0 / s, 24.0 / s)
            _draw_png_glyph(cr, BT_PNG, s)
        return False
