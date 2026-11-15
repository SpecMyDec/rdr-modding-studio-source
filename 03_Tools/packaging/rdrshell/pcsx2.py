"""Find PCSX2, so Play and Boot test are not dead on arrival.

The Studio can launch the working disc in an emulator and can run a timed boot
test against it. Both need the path to pcsx2-qt.exe (or the older pcsx2.exe).
Nothing in the tool ever asks for it, so on a fresh install both buttons are
permanently disabled and the status bar says "PCSX2 missing" with no way to
fix it from inside the app. This module is the fix: guess first, ask second,
and let the user skip entirely.

Guessing is cheap -- it is a handful of os.path.isfile calls over the places
Windows installers actually use -- so it costs nothing to try on every first
run and on every launch where the stored path has gone stale.
"""

import glob
import os

# Newest first: pcsx2-qt.exe is the current binary, pcsx2x64*.exe the 1.6-era
# ones. A portable install is just a folder, which is why the Steam and common
# hand-unzipped locations are searched too.
NAMES = ("pcsx2-qt.exe", "pcsx2x64-avx2.exe", "pcsx2x64.exe", "pcsx2.exe")


def _dirs():
    env = os.environ
    out = []
    for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA", "APPDATA"):
        base = env.get(var)
        if base:
            out += [os.path.join(base, "PCSX2"),
                    os.path.join(base, "PCSX2-Qt"),
                    os.path.join(base, "Programs", "PCSX2")]
    home = os.path.expanduser("~")
    out += [os.path.join(home, "PCSX2"),
            os.path.join(home, "Desktop", "PCSX2"),
            os.path.join(home, "Downloads", "PCSX2"),
            os.path.join(home, "Documents", "PCSX2")]
    for drive in ("C:", "D:", "E:"):
        out += [drive + r"\PCSX2",
                drive + r"\Emulators\PCSX2",
                drive + r"\Games\PCSX2",
                drive + r"\Program Files\PCSX2",
                drive + r"\SteamLibrary\steamapps\common\PCSX2"]
    return out


def find():
    """The first plausible pcsx2 executable, or "". Never raises."""
    for d in _dirs():
        try:
            if not os.path.isdir(d):
                continue
            for name in NAMES:
                p = os.path.join(d, name)
                if os.path.isfile(p):
                    return p
            # A versioned unzip: PCSX2\pcsx2-v2.0.0-windows-x64\pcsx2-qt.exe
            for sub in sorted(glob.glob(os.path.join(d, "pcsx2*")), reverse=True):
                for name in NAMES:
                    p = os.path.join(sub, name)
                    if os.path.isfile(p):
                        return p
        except OSError:
            continue
    return ""


def describe(path):
    if not path:
        return {"path": "", "ok": False,
                "message": "PCSX2 not found. Play and Boot test stay switched "
                           "off until you point at it."}
    if not os.path.isfile(path):
        return {"path": path, "ok": False,
                "message": "That file is not there any more."}
    return {"path": path, "ok": True,
            "message": "%s -- Play and Boot test will work."
                       % os.path.basename(path)}


if __name__ == "__main__":
    import json
    print(json.dumps(describe(find()), indent=2))
