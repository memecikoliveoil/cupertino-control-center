"""DesktopAppInfo (reading installed apps' .desktop files).

Newer PyGObject marks Gio.DesktopAppInfo as deprecated in favour of GioUnix.DesktopAppInfo.
On GLib 2.80 (Xubuntu 24.04), though, taking the class from GioUnix first makes some of its
methods fail (get_startup_wm_class, for one). So it is still taken from Gio, with that
warning silenced, and from GioUnix only if a future PyGObject drops it from Gio."""
import warnings
import gi
from gi.repository import Gio

try:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        DesktopAppInfo = Gio.DesktopAppInfo
except AttributeError:
    gi.require_version("GioUnix", "2.0")
    from gi.repository import GioUnix
    DesktopAppInfo = GioUnix.DesktopAppInfo
