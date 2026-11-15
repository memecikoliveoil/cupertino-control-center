"""Built-in Dark Mode switch, used by the Display view when no theme script is installed.

Switches the GTK theme, the window border theme (xfwm4) and the icon theme to their light or
dark variants, and sets org.gnome.desktop.interface color-scheme, which libadwaita apps and
this Control Center follow.

Variants are found by name, and used only if that theme is installed:
  dark:  "<name>-Dark" or "<name>-dark"            (Adwaita -> Adwaita-dark)
  light: "<name>", "<name>-Light" or "<name>-light" (WhiteSur-Dark -> WhiteSur-Light)
A "-dark"/"-light" part in the middle of a name is swapped too (WhiteSur-Dark-solid).

To choose the themes yourself, set any of these in functions/states/settings.json:
  "light_gtk_theme", "dark_gtk_theme", "light_wm_theme", "dark_wm_theme",
  "light_icon_theme", "dark_icon_theme"
"""
import json
import os
import re
import shutil
import subprocess

from cupertinocontrolcenter.paths import STATE_DIR

_HOME = os.path.expanduser("~")
THEME_DIRS = [os.path.join(_HOME, ".themes"), os.path.join(_HOME, ".local/share/themes"),
              "/usr/local/share/themes", "/usr/share/themes"]
ICON_DIRS = [os.path.join(_HOME, ".icons"), os.path.join(_HOME, ".local/share/icons"),
             "/usr/local/share/icons", "/usr/share/icons"]
BUILTIN_GTK = {"Adwaita", "Adwaita-dark", "HighContrast", "HighContrastInverse"}

# settings key, xfconf channel, xfconf property, gsettings key, search dirs, file that
# proves the theme has this part
TARGETS = (
    ("gtk_theme", "xsettings", "/Net/ThemeName", "gtk-theme", THEME_DIRS, "gtk-3.0"),
    ("wm_theme", "xfwm4", "/general/theme", None, THEME_DIRS, "xfwm4"),
    ("icon_theme", "xsettings", "/Net/IconThemeName", "icon-theme", ICON_DIRS, "index.theme"),
)
GSETTINGS_SCHEMA = "org.gnome.desktop.interface"


def available():
    return bool(shutil.which("xfconf-query") or shutil.which("gsettings"))


def _run(args):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=3)
    except Exception:
        return None


def _get(channel, prop, gkey):
    if shutil.which("xfconf-query"):
        r = _run(["xfconf-query", "-c", channel, "-p", prop])
        if r and r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    if gkey and shutil.which("gsettings"):
        r = _run(["gsettings", "get", GSETTINGS_SCHEMA, gkey])
        if r and r.returncode == 0:
            return r.stdout.strip().strip("'")
    return None


def _set(channel, prop, gkey, value):
    if shutil.which("xfconf-query"):
        _run(["xfconf-query", "-c", channel, "-p", prop, "-s", value])
    if gkey and shutil.which("gsettings"):
        _run(["gsettings", "set", GSETTINGS_SCHEMA, gkey, value])


def _installed(name, dirs, marker):
    if marker == "gtk-3.0" and name in BUILTIN_GTK:
        return True
    return any(os.path.exists(os.path.join(d, name, marker)) for d in dirs)


def _candidates(name, dark):
    m = re.search(r"-(dark|light)(?=-|$)", name, re.I)
    if m:
        head, tail = name[:m.start()], name[m.end():]
    else:
        head, tail = name, ""
    if dark:
        return [f"{head}-Dark{tail}", f"{head}-dark{tail}"]
    return [f"{head}{tail}", f"{head}-Light{tail}", f"{head}-light{tail}"]


def _variant(name, dark, dirs, marker):
    for c in _candidates(name, dark):
        if c == name or _installed(c, dirs, marker):
            return c
    return None


def _overrides():
    try:
        with open(os.path.join(STATE_DIR, "settings.json")) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def is_dark():
    gtk = _get("xsettings", "/Net/ThemeName", "gtk-theme")
    if gtk:
        return bool(re.search(r"dark", gtk, re.I))
    r = _run(["gsettings", "get", GSETTINGS_SCHEMA, "color-scheme"]) if shutil.which("gsettings") else None
    return bool(r and "dark" in r.stdout)


def set_dark(dark):
    overrides = _overrides()
    mode = "dark" if dark else "light"
    for key, channel, prop, gkey, dirs, marker in TARGETS:
        current = _get(channel, prop, gkey)
        target = overrides.get(f"{mode}_{key}") or (current and _variant(current, dark, dirs, marker))
        if target and target != current:
            _set(channel, prop, gkey, target)
    if shutil.which("gsettings"):
        _run(["gsettings", "set", GSETTINGS_SCHEMA, "color-scheme",
              "prefer-dark" if dark else "default"])
