"""Translations.

Strings in the code are English and wrapped in _(). Translations live in po/<language>.po
(e.g. po/tr.po) and are read directly at startup, so no build step is needed.

The language follows the system (LANGUAGE / LC_ALL / LC_MESSAGES / LANG). To force one,
set "language" in functions/states/settings.json (e.g. "tr", "en"), or start the app with
LANGUAGE=en.

To add a language: copy po/cupertino-control-center.pot to po/<code>.po and fill in the
msgstr lines. After adding new _() strings to the code, regenerate the template and merge
it into the existing translations (see README, "Translations").
"""
import ast
import gettext
import json
import os

from cupertinocontrolcenter.paths import PO_DIR, STATE_DIR

DOMAIN = "cupertino-control-center"
SOURCE_LANGUAGE = "en"


def _parse_po(path):
    entries, cur, last = [], {}, None

    def flush():
        if "msgid" in cur:
            entries.append(dict(cur))
        cur.clear()

    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith('"'):
                cur[last] += ast.literal_eval(line)
                continue
            key, _, value = line.partition(" ")
            if key in ("msgctxt", "msgid") and any(k.startswith("msgstr") for k in cur):
                flush()
            cur[key] = ast.literal_eval(value)
            last = key
    flush()
    return entries


class PoTranslations(gettext.NullTranslations):
    def __init__(self, path):
        super().__init__()
        self._catalog = {}
        self._plural = lambda n: int(n != 1)
        for e in _parse_po(path):
            ctx, msgid = e.get("msgctxt"), e["msgid"]
            if msgid == "" and ctx is None:
                for line in e.get("msgstr", "").splitlines():
                    if line.lower().startswith("plural-forms:") and "plural=" in line:
                        expr = line.split("plural=", 1)[1].strip().rstrip(";")
                        try:
                            self._plural = gettext.c2py(expr)
                        except Exception:
                            pass
                continue
            if "msgid_plural" in e:
                forms = [e[k] for k in sorted(k for k in e if k.startswith("msgstr["))]
                if all(forms):
                    self._catalog[(ctx, msgid)] = forms
            elif e.get("msgstr"):
                self._catalog[(ctx, msgid)] = e["msgstr"]

    def gettext(self, message):
        return self._catalog.get((None, message), message)

    def pgettext(self, context, message):
        return self._catalog.get((context, message), message)

    def ngettext(self, singular, plural, n):
        forms = self._catalog.get((None, singular))
        if isinstance(forms, list):
            i = self._plural(n)
            return forms[i] if i < len(forms) else forms[-1]
        return singular if n == 1 else plural


def _wanted_languages():
    try:
        with open(os.path.join(STATE_DIR, "settings.json")) as f:
            lang = json.load(f).get("language", "auto")
        if lang and lang != "auto":
            return [lang]
    except Exception:
        pass
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        val = os.environ.get(var)
        if val:
            return [v for v in val.split(":") if v]
    return []


def _load():
    for lang in _wanted_languages():
        base = lang.split(".")[0].split("@")[0]
        if base in ("C", "POSIX"):
            break
        for code in (base, base.split("_")[0]):
            if code.split("_")[0] == SOURCE_LANGUAGE:
                return gettext.NullTranslations()
            path = os.path.join(PO_DIR, code + ".po")
            if os.path.exists(path):
                try:
                    return PoTranslations(path)
                except Exception as e:
                    print(f"Could not read {path}: {e}")
    return gettext.NullTranslations()


translations = _load()
_ = translations.gettext
ngettext = translations.ngettext
pgettext = translations.pgettext
