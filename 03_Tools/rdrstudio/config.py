"""Where things are.

Nothing here is allowed to be a literal path from the machine that built the
app -- not in the code, and not in this docstring either, because a docstring
is compiled into the module and ships with it.

This module is frozen into the shipped exe, and it used to open by assigning
two absolute paths: the project root on a particular drive, and an emulator
directory beside it. That put one developer's drive layout, project folder
naming and emulator location inside every stranger's download. server.py
overrides these
at startup so a real install writes under %LOCALAPPDATA% -- but they stayed as
the fallback, so if that override ever failed or was bypassed, a stranger's
install would quietly aim at a D: drive that is not theirs.

Everything is now derived:

  ROOT   from this file's own location, so a checkout works wherever it sits.
         Frozen it lands inside the bundle, which is harmless because the
         shell has already replaced these values by the time anything reads
         them.
  PCSX2  from the environment, then from the settings file the app itself
         writes when the user picks their emulator -- the one place that
         actually knows. No guessing at install locations.

Override either with RDR_ROOT / RDR_PCSX2_EXE when driving the CLI tools.
"""

import json
import os

# 03_Tools/rdrstudio/config.py -> 03_Tools/rdrstudio -> 03_Tools -> the project
_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("RDR_ROOT") or os.path.dirname(os.path.dirname(_HERE))

VANILLA_ISO = os.path.join(ROOT, "01_Vanilla", "RDR_VANILLA.iso")
MODDED_ISO = os.path.join(ROOT, "02_Modded", "RDR_MODDED.iso")
EXTRACTED = os.path.join(ROOT, "04_Extracted")
BACKUPS = os.path.join(ROOT, "06_Backups")
MODS = os.path.join(ROOT, "02_Modded", "mods")
# Patches and ISO copies made by Export. The desktop app points this at
# Documents\RDR Mods\exports (rdrshell/server.py).
EXPORTS = os.path.join(ROOT, "02_Modded", "exports")


def _settings_file():
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        # Windows always sets LOCALAPPDATA, so this branch is for the CLI
        # tools and the test scripts, which also run from WSL. The old
        # fallback was `os.path.expanduser(r"~\AppData\Local")`, and
        # expanduser only expands a leading `~` followed by a SLASH or the
        # end of the string -- given a backslashed name it returns it
        # untouched. So off Windows this handed back the literal relative
        # path `~\AppData\Local/RDR Modding Studio/settings.json` and would
        # have created a directory named `~\AppData\Local` in whatever the
        # working directory happened to be.
        base = os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(base, "RDR Modding Studio", "settings.json")


def _saved_pcsx2():
    """The emulator the user actually chose, from the app's own settings."""
    try:
        with open(_settings_file(), encoding="utf-8") as fh:
            return (json.load(fh).get("pcsx2_exe") or "").strip()
    except Exception:                                          # noqa: BLE001
        return ""


PCSX2_EXE = os.environ.get("RDR_PCSX2_EXE") or _saved_pcsx2()
PCSX2_DIR = os.path.dirname(PCSX2_EXE) if PCSX2_EXE else ""


def emulog():
    """PCSX2's log. Portable builds keep it beside the exe; installed ones use
    Documents. With no emulator configured there is nothing to point at, and
    saying so beats inventing a path that will never exist."""
    candidates = []
    if PCSX2_DIR:
        candidates.append(os.path.join(PCSX2_DIR, "logs", "emulog.txt"))
    # Explicit join, not expanduser(r"~\Documents\..."): on POSIX expanduser
    # expands `~` only before a `/` or the end of the string, so the backslash
    # form comes back unchanged and RELATIVE. Identical result on Windows.
    candidates.append(os.path.join(os.path.expanduser("~"), "Documents",
                                   "PCSX2", "logs", "emulog.txt"))
    for p in candidates:
        if os.path.exists(p):
            return p
    return candidates[0]


def sstates():
    r"""PCSX2's savestate folder, resolved the same way as emulog().

    Portable builds keep it beside the exe, installed ones use Documents.
    With no emulator configured there is nothing to point at, and saying so
    beats inventing a path that will never exist.

    Spelled as an explicit join rather than `expanduser(r"~\Documents\...")`.
    On POSIX `expanduser` expands `~` only before a `/` or the end of the
    string, so the backslash form is returned UNCHANGED and comes back
    relative. emulog() above was written that way and returned
    '~\\Documents\\PCSX2\\logs\\emulog.txt', isabs False, until it was fixed
    alongside this; `_settings_file()` had always had it right. The fourth
    and last instance, rdrshell/paths.py, was closed the same day -- REQ-072.
    """
    candidates = []
    if PCSX2_DIR:
        candidates.append(os.path.join(PCSX2_DIR, "sstates"))
    candidates.append(os.path.join(os.path.expanduser("~"), "Documents",
                                   "PCSX2", "sstates"))
    for p in candidates:
        if os.path.isdir(p):
            return p
    return candidates[0]


# The Studio's port, overridable so a run can be isolated onto a port of its own
# instead of competing for the shared one. Default unchanged, so nothing moves
# unless RDR_PORT is deliberately set.
#
# **THIS DOES NOT MOVE THE WHOLE STACK, AND THAT MATTERS.** `run_tests.py` keeps
# its OWN `PORT = 8731` (line 30) and does not read this. So with RDR_PORT set,
# the Studio binds the new port while the suite still looks at 8731, decides
# nothing is listening, and starts a SECOND Studio there -- and then reports
# about an instance you did not mean to test. That is the same wrong-instance
# family as the wrong-disc Studio rdr-app closed in 1c87eed. Until run_tests.py
# reads this constant, RDR_PORT is safe for driving a Studio by hand and is NOT
# safe for a suite run.
#
# Also still literal, and unaffected: studio.py `serve(port=8731)`'s default
# (RDR_Studio.py passes --port, so this only bites a direct caller), test.sh's
# messages, and Launch Studio.bat's printed URL.
PORT = int(os.environ.get("RDR_PORT") or 8731)
