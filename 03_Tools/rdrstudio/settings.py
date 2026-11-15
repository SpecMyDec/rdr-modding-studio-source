"""The preferences the app remembers between launches.

A tester on v1.2.0: *"Being able to set up and save the Theme, View Settings,
and Game Lighting settings instead of needing to set them each time you
relaunch the app."*

Three groups, and they were in three different states before this module:

  * **Theme** was remembered, in `localStorage` under `rdr.look`.
  * **View settings** were remembered in six places and forgotten in four --
    `worldMode`, `renderMode`, `linkCollision` and `trackLayout` were plain
    module-scope variables that went back to their defaults on every reload.
  * **Game lighting** was not remembered at all. Its three checkboxes are
    declared `checked` in `index.html` and nothing ever read them back, so
    every launch turned all three on again.

`localStorage` is also the wrong home even where it worked. It belongs to one
browser profile on one machine: clearing site data, opening the app in a
different browser, or -- for the packaged build -- a webview that starts with
a fresh profile all lose the lot, and none of it is visible to the app
itself. So the answer moves to the file the app already owns: the same
`settings.json` under %LOCALAPPDATA% that holds the emulator path.

Two rules this module keeps, both of them things a test can check:

  1. **A key nobody declared is not stored.** `DEFAULTS` is the whole
     vocabulary. An unknown key is dropped and named in the reply rather than
     written, so a buggy or hostile client cannot grow this file without
     bound, and a typo in the front end shows up as "ignored" instead of as a
     setting that silently never comes back.

  2. **Writing preferences never loses the emulator path.** `pcsx2_exe` is
     written by a different part of the app and read by `config.py` at import
     time. Saving is a read-modify-write of the whole document with an atomic
     replace, so a preference write cannot truncate the file or drop a key it
     does not know about at the top level.

`load()` always returns a complete dict -- every key in `DEFAULTS` present,
with the default filled in for anything missing or unreadable. A corrupt or
absent settings file is a fresh install, not an error.
"""

import json
import os
import tempfile

from . import config

# Every preference the app remembers, with the value it has when nobody has
# chosen one. These defaults are the SAME values the front end declares, and
# `verify_settings.py` asserts that rather than trusting the comment.
DEFAULTS = {
    # --- Theme (Appearance) ------------------------------------------------
    # The id is validated as a bounded string rather than against a list:
    # the themes live in `web/nav.js`, which already falls back with
    # `THEMES[themeId] || THEMES.workbench`, and a list here would be a
    # second copy to drift.
    "theme": "workbench",
    "accent": "#5b9dd9",

    # --- View settings -----------------------------------------------------
    "linkCollision": False,
    "allowGrow": False,
    "navMode": "orbit",
    "gridOn": False,
    "xray": False,
    "helpersFirst": False,
    "worldMode": "off",
    "renderMode": "textured",
    "trackLayout": False,
    "hiddenBoxTypes": [],
    "outMode": "file",

    # --- Game lighting -----------------------------------------------------
    "envTint": True,
    "bakedLight": True,
    "secondPass": True,
}

# The settings whose values are a fixed set. The front end switches on these
# by string and has no fallback for an unknown one, so a bad value would be a
# dead control rather than a wrong one.
CHOICES = {
    "navMode": ("orbit", "fly"),
    "worldMode": ("solid", "wire", "off"),
    "renderMode": ("textured", "vertexcolor", "plain", "off"),
    "outMode": ("file", "type", "frame"),
}

# Bounds. Nothing here is a security boundary -- the server is bound to
# loopback -- but a settings file is the one thing that survives a restart,
# so a value that breaks the app must not be able to persist and break it
# again on every launch after that.
_MAX_STR = 64
_MAX_LIST = 256

# Where the preferences live inside settings.json. Nested under one key so
# `pcsx2_exe` and anything else at the top level stays clearly not-a-preference.
_SECTION = "ui"


def path():
    """The settings file. Same one `config.py` reads the emulator path from."""
    return config._settings_file()


def _read_document():
    """The whole settings.json as a dict, or {} if it is missing or corrupt."""
    try:
        with open(path(), encoding="utf-8") as fh:
            doc = json.load(fh)
        return doc if isinstance(doc, dict) else {}
    except Exception:                                          # noqa: BLE001
        return {}


def _clean(key, value):
    """`value` coerced to what `key` is declared as, or None to reject it.

    Rejection is deliberate rather than best-effort coercion: storing the
    string "false" in a boolean would come back as True on the next launch,
    which is exactly the class of bug this module exists to stop.
    """
    default = DEFAULTS[key]

    if isinstance(default, bool):
        # Checked before int: bool IS an int in Python, and JSON booleans
        # arrive as bools, so this ordering matters.
        return value if isinstance(value, bool) else None

    if isinstance(default, list):
        if not isinstance(value, list) or len(value) > _MAX_LIST:
            return None
        out = []
        for item in value:
            if not isinstance(item, str) or len(item) > _MAX_STR:
                return None
            out.append(item)
        # Order is not meaningful (the front end holds it in a Set) and a
        # stable order makes the file diffable and the test deterministic.
        return sorted(set(out))

    if isinstance(default, str):
        if not isinstance(value, str) or len(value) > _MAX_STR:
            return None
        if key in CHOICES:
            return value if value in CHOICES[key] else None
        return value

    return None


def load():
    """Every declared preference, defaults filled in for whatever is missing."""
    saved = _read_document().get(_SECTION)
    out = dict(DEFAULTS)
    if isinstance(saved, dict):
        for key, value in saved.items():
            if key not in DEFAULTS:
                continue
            cleaned = _clean(key, value)
            if cleaned is not None:
                out[key] = cleaned
    # The list is mutable and shared with DEFAULTS until something replaces
    # it; copy so a caller cannot edit the module's own default in place.
    out["hiddenBoxTypes"] = list(out["hiddenBoxTypes"])
    return out


def save(changes):
    """Merge `changes` into the stored preferences. Returns what happened.

        {"settings": {...everything, after the merge...},
         "written":  ["xray", ...],      # keys whose stored value changed
         "ignored":  ["nonsense", ...]}  # unknown keys and rejected values

    Merge rather than replace, so a caller that knows about one setting
    cannot drop the others -- which is what a front end saving a single
    checkbox does.
    """
    if not isinstance(changes, dict):
        raise ValueError("settings must be an object")

    current = load()
    merged = dict(current)
    ignored = []
    for key, value in changes.items():
        if key not in DEFAULTS:
            ignored.append(key)
            continue
        cleaned = _clean(key, value)
        if cleaned is None:
            ignored.append(key)
            continue
        merged[key] = cleaned

    written = [k for k in DEFAULTS if merged[k] != current[k]]

    if written:
        doc = _read_document()
        # Store only what differs from the default. The file then says what
        # the user actually chose, and a later change to a default reaches
        # everyone who never touched that control.
        doc[_SECTION] = {k: v for k, v in merged.items() if v != DEFAULTS[k]}
        _write_document(doc)

    return {"settings": merged, "written": written, "ignored": ignored}


def reset():
    """Forget every preference. The emulator path and anything else stays."""
    doc = _read_document()
    doc.pop(_SECTION, None)
    _write_document(doc)
    return {"settings": load(), "written": [], "ignored": []}


def _write_document(doc):
    """Replace settings.json atomically.

    A half-written settings file is read back as a fresh install, which would
    silently lose the emulator path as well as every preference. Write beside
    the target and rename, so a reader sees either the old file or the new
    one.
    """
    target = path()
    os.makedirs(os.path.dirname(target), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(target), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
