"""Does an edit made by this tool actually reach the RUNNING GAME?

Everything else in the suite checks the *files*. That is a real gap: a format
can be byte-perfect on disc and still be understood wrongly, and a write can
land in the archive the tool thinks is canonical while the game loads a
different copy. This is the only check that closes the loop.

    edit -> boot the disc in PCSX2 -> save state -> find it in PS2 RAM -> undo

It found a genuine mistake the first time it ran. A string was edited in
FRONT.DAT, the write verified correct on disc, and the running game still
showed the old text -- because that string exists in **65 archives** and only
one had been changed. No file-level test could have caught that, because every
file involved was exactly what the tool intended it to be.

    python scripts/verify_ingame.py            edit, boot, check, undo
    python scripts/verify_ingame.py --keep     leave the marker applied

Needs PCSX2 with `SavestateCompressionType = 0` (otherwise save states are
zstd and unreadable), and it takes over the screen and keyboard for ~90s
because it drives the emulator by sending keystrokes.
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from rdrstudio.config import sstates   # noqa: E402


API = "http://127.0.0.1:8731/api/"
STATES = sstates()
ENTRY = "Aim_You_Win"          # a short front-end string, safe to borrow
MARKER = "MOD TEST OK"


def api(route, **kw):
    import urllib.parse
    u = API + route + "?" + urllib.parse.urlencode(kw)
    return json.load(urllib.request.urlopen(u, timeout=1800))


def post(route, body):
    req = urllib.request.Request(
        API + route, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=1800))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true",
                    help="leave the marker in the ISO instead of undoing it")
    ap.add_argument("--seconds", type=float, default=75.0)
    args = ap.parse_args()
    ok = True

    def check(what, good, detail=""):
        nonlocal ok
        ok &= bool(good)
        print("  %-52s %s%s" % (what, "ok" if good else "FAIL",
                                ("  " + detail) if detail else ""))

    # ---- 1. find every copy. Scoping this query to one archive is exactly
    # the mistake that made the first run fail, so it is deliberately global.
    row = None
    d = api("strings", q=ENTRY)
    for r in d["strings"]:
        if r["name"] == ENTRY:
            row = r
    if not row:
        print("could not find the string %r to borrow" % ENTRY)
        return 1
    original = row["text"]
    print("%s: %d copies across %d strtbl files, currently %r\n"
          % (ENTRY, len(row["copies"]), d["files"], original))
    check("the string exists in many archives, not one",
          len(row["copies"]) > 1, "%d copies" % len(row["copies"]))

    # ---- 2. write the marker into all of them
    body = {"name": ENTRY, "text": MARKER, "copies": row["copies"],
            "label": "in-game verification marker"}
    dry = post("string_set", dict(body, dry=True))
    check("dry run fits every copy", dry.get("ok") and not dry.get("refused"),
          "%d would apply" % dry.get("would_apply", 0))
    if not dry.get("ok"):
        return 1
    applied = post("string_set", body)
    check("marker written to every copy", not applied.get("refused"),
          "%d file(s)" % applied.get("applied", 0))

    # ---- 3. boot it
    try:
        urllib.request.urlopen(urllib.request.Request(
            API + "release", data=b"{}",
            headers={"Content-Type": "application/json"}), timeout=60)
    except Exception:
        pass          # not fatal; the disc may not be held open
    for p in glob.glob(os.path.join(STATES, "*.p2s")):
        try:
            os.remove(p)
        except OSError:
            pass
    print("\nlaunching PCSX2 -- do not touch the keyboard for ~%ds\n"
          % int(args.seconds))
    subprocess.run([sys.executable, str(HERE / "play_test.py"),
                    "--seconds", str(args.seconds), "--save-state"],
                   cwd=str(HERE.parent))

    # ---- 4. look for it in PS2 RAM
    states = sorted(glob.glob(os.path.join(STATES, "*.p2s")),
                    key=os.path.getmtime)
    check("a save state was produced", bool(states))
    if states:
        z = zipfile.ZipFile(states[-1])
        try:
            ee = z.read("eeMemory.bin")
        except KeyError:
            print("  save state has no eeMemory.bin -- is it zstd-compressed? "
                  "set SavestateCompressionType = 0")
            ee = b""
        found = ee.count(MARKER.encode("utf-16-le"))
        stale = ee.count(original.encode("utf-16-le")) if original else 0
        check("the marker is in the running game's RAM", found > 0,
              "x%d" % found)
        # Not a failure by itself: other entries can share the old text.
        print("     (the old text still appears %d time(s); other entries can "
              "carry it)" % stale)

    # ---- 5. put it back
    if not args.keep:
        r = post("undo", {})
        check("the edit undoes cleanly", r.get("ok"),
              "%s file(s)" % r.get("count"))

    print("\n%s" % ("ALL CHECKS PASSED" if ok else "SOMETHING FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
