import os

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(PACKAGE_DIR)

ICONS_DIR = os.path.join(APP_DIR, "icons")
PO_DIR = os.path.join(APP_DIR, "po")

# Per-user state (settings, music history, saved hotspots, expanded/collapsed sections).
# Lives next to the code so a single folder holds everything; it is git-ignored.
STATE_DIR = os.path.join(PACKAGE_DIR, "functions", "states")

CACHE_DIR = os.path.join(os.path.expanduser("~/.cache"), "cupertino-control-center")
