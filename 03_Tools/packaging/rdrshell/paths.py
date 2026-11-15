"""Where a downloaded app is allowed to put things.

A shipped application must not write next to its own .exe -- that may be
Program Files, or a read-only folder, or a network share. Windows has one
right answer for each kind of file and this module is the only place that
answer is written down.

    %LOCALAPPDATA%\\RDR Modding Studio\\      settings, logs, the working ISO
    %USERPROFILE%\\Documents\\RDR Mods\\      mods, checkpoints, exports

The split is deliberate. LOCALAPPDATA holds machine state: big, rebuildable,
not worth backing up, not worth syncing (the working ISO is 2.2 GB and would
choke OneDrive). Documents holds the things the developer *made*: mod files and
checkpoints are small, portable and exactly what someone wants to find,
back up and send to a friend.

Everything here can be overridden by settings.json, so a user with a small C:
can move the working ISO onto D: without editing code.
"""

import json
import os
import sys

APP_NAME = "RDR Modding Studio"
DOCS_NAME = "RDR Mods"
SETTINGS_VERSION = 1


# ----------------------------------------------------------------- base dirs

def _local_appdata():
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        # Explicit join, NOT expanduser(r"~\AppData\Local"). On POSIX
        # expanduser expands a leading `~` only before a `/` or the end of the
        # string; given a backslashed name it returns it untouched. So this
        # handed back the relative string `~\AppData\Local/RDR Modding Studio`
        # and ensure_dirs() then created a literal directory named
        # `~\AppData\Local` in whatever the working directory happened to be.
        # Three of those were made and removed on 2026-09-18, and .gitignore's
        # catch-all keeps them out of `git status`, so nothing reports them.
        #
        # Identical result on Windows, where this branch is unreachable
        # anyway: %LOCALAPPDATA% is always set there. Which is also why the
        # suite cannot exercise this line in either direction -- the fallback
        # runs only off Windows, so a green run says nothing about it. Shown
        # by hand instead: `env -u LOCALAPPDATA python3 packaging/test_shell.py`
        # went 20/21 before this and 21/21 after.
        #
        # REQ-072, and the last of four instances of one defect.
        # config._settings_file() always had this form and its comment
        # predicted this exact junk directory; emulog() and sstates() were
        # corrected in d08418f.
        base = os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(base, APP_NAME)


def _documents():
    """The real Documents folder, which is NOT always ~\\Documents.

    OneDrive redirects it, and so does any corporate profile. The registry
    holds the truth; the expanduser guess is only the fallback.
    """
    try:
        import winreg
        key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            val, _ = winreg.QueryValueEx(k, "Personal")
            return os.path.join(os.path.expandvars(val), DOCS_NAME)
    except Exception:                                      # noqa: BLE001
        return os.path.join(os.path.expanduser("~"), "Documents", DOCS_NAME)


APP_DIR = _local_appdata()
SETTINGS_FILE = os.path.join(APP_DIR, "settings.json")
LOG_FILE = os.path.join(APP_DIR, "studio.log")


# ------------------------------------------------------------------ bundling

def resource_dir():
    """Where the read-only shipped files live (web/, docs, reference data).

    Frozen: PyInstaller's extraction dir. Source: the 03_Tools folder.
    """
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def frozen():
    return bool(getattr(sys, "frozen", False))


# ------------------------------------------------------------------ settings

DEFAULTS = {
    "version": SETTINGS_VERSION,
    "vanilla_iso": "",        # the user's own disc image. Never shipped.
    "working_iso": "",        # our writable copy of it
    "mods_dir": "",           # defaults to Documents\RDR Mods\mods
    "checkpoints_dir": "",    # defaults to Documents\RDR Mods\checkpoints
    "exports_dir": "",        # defaults to Documents\RDR Mods\exports
    "pcsx2_exe": "",          # optional; Play/Boot test are disabled without it
    "window": {"width": 1600, "height": 1000, "maximized": False},
    "accepted_disclaimer": False,
}


def load():
    s = dict(DEFAULTS)
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as fh:
            on_disk = json.load(fh)
        if isinstance(on_disk, dict):
            s.update(on_disk)
    except FileNotFoundError:
        pass
    except Exception:                                      # noqa: BLE001
        # A corrupt settings file must not brick the app. Keep the bad one
        # beside the good one so a support question can still be answered.
        try:
            os.replace(SETTINGS_FILE, SETTINGS_FILE + ".broken")
        except OSError:
            pass
    return _fill_defaults(s)


def _fill_defaults(s):
    docs = _documents()
    if not s.get("working_iso"):
        s["working_iso"] = os.path.join(APP_DIR, "working", "RDR_WORKING.iso")
    for key, sub in (("mods_dir", "mods"),
                     ("checkpoints_dir", "checkpoints"),
                     ("exports_dir", "exports")):
        if not s.get(key):
            s[key] = os.path.join(docs, sub)
    return s


def save(s):
    os.makedirs(APP_DIR, exist_ok=True)
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(s, fh, indent=2)
    os.replace(tmp, SETTINGS_FILE)          # atomic; never a half-written file
    return SETTINGS_FILE


def ensure_dirs(s):
    for d in (APP_DIR,
              os.path.dirname(s["working_iso"]),
              s["mods_dir"], s["checkpoints_dir"], s["exports_dir"]):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass


def configured(s):
    """True when the app has everything it needs to open the Studio."""
    return (bool(s.get("vanilla_iso")) and os.path.isfile(s["vanilla_iso"])
            and bool(s.get("working_iso")) and os.path.isfile(s["working_iso"])
            and s.get("accepted_disclaimer") is True)


def describe():
    return {
        "app_dir": APP_DIR,
        "settings": SETTINGS_FILE,
        "documents": _documents(),
        "resources": resource_dir(),
        "frozen": frozen(),
    }


if __name__ == "__main__":
    print(json.dumps(describe(), indent=2))
    print()
    print(json.dumps(load(), indent=2))
