#!/usr/bin/env python3
"""Prove the whole non-GUI half of the desktop app, with numbers.

Everything the shell does except draw a window:

    1  resolve the per-user folders
    2  verify a candidate ISO through all three fast tiers
    3  make the working copy (skipped if one is already there)
    4  patch the toolkit's config to the per-user folders
    5  start the Studio in-process on an OS-chosen port
    6  hit the real API and the real front-end files over that port
    7  shut down cleanly

Writes only inside packaging/testdata. Reads the vanilla ISO. Never touches
01_Vanilla or 02_Modded for writing, and never binds 8731.

ONE AT A TIME, and enforced rather than promised. Two concurrent runs would
open the same testdata/RDR_WORKING.iso writable from two processes, which is
the mechanism behind REQ-077: each holds its own buffered handle, a seek
inside an existing buffer is served without going back to the OS, and nothing
arbitrates -- the Studio's own lock is a threading.RLock. Worse than the
races, step 3 REUSES the working copy when it exists and matches VANILLA's
size, and the toolkit is slot-safe, so an in-place edit never changes the
size: that gate passes on every disc there is, mid-edit or not. It cannot
detect the one state that makes reuse unsafe.

Until now the only thing stopping the overlap was the suite lock, which is a
convention, and which a suite started any other way never takes. Every other
promise in this file is asserted -- the port is not 8731, the copy is
writable, the size matches vanilla -- so this one is too, by an exclusive
create below.

    python packaging/test_shell.py [--fresh]
"""

import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, TOOLS)

from rdrshell import paths, verify                        # noqa: E402
from rdrshell.server import StudioServer                  # noqa: E402
# The vanilla ISO comes from the toolkit's own config, which derives it from
# the repo layout or $RDR_ROOT. This was a literal `...` path:
# one machine's drive letter and folder naming, which made the file unrunnable
# anywhere else and could not be right on a second machine. Same defect
# REQ-059 swept out of three registered rows, and the one `machine_gate`
# exists to stop reaching a stranger. `config` is imported by step 4 anyway.
from rdrstudio.config import VANILLA_ISO as VANILLA       # noqa: E402

TESTDATA = os.path.join(HERE, "testdata")
WORKING = os.path.join(TESTDATA, "RDR_WORKING.iso")

T = {}
FAILED = []
LOCK = os.path.join(TESTDATA, "RDR_WORKING.iso.lock")
# Only the process that WON the create may remove the file. Without this a
# refused second run would delete the holder's lock on its way out and hand
# the ISO to a third -- the guard undoing itself, which is the shape this
# whole file has been chasing today.
_LOCK_IS_OURS = False


def take_lock():
    """Exclusive create beside the ISO, or refuse. Returns nothing.

    O_CREAT|O_EXCL is atomic on both platforms, so there is no check-then-act
    window of the kind that cost REQ-077 its premise -- the create either wins
    or raises, and the winner is the only process that proceeds.

    A crashed run leaves the file behind, so it carries the pid and a
    timestamp and the refusal prints how to clear it. That is deliberately a
    human decision: this process cannot tell a live holder from a dead one
    across platforms (the pid may belong to the Windows side), and guessing
    wrong deletes a live run's lock.
    """
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            with open(LOCK, encoding="utf-8") as fh:
                held = fh.read().strip()
        except OSError:
            held = "(unreadable)"
        print("  %s is already locked: %s" % (WORKING, held))
        print("  Another run of this file has the working copy open. Two would")
        print("  share one writable ISO and neither would see the other's")
        print("  writes -- see the header. Wait for it, or if nothing is")
        print("  running, delete %s" % LOCK)
        print("Nothing has been run.")
        sys.exit(2)
    global _LOCK_IS_OURS
    with os.fdopen(fd, "w") as fh:
        fh.write("pid %d, started %s\n"
                 % (os.getpid(), time.strftime("%Y-%m-%d %H:%M:%S")))
    _LOCK_IS_OURS = True


def drop_lock():
    try:
        os.remove(LOCK)
    except OSError:
        pass


def step(n, label):
    print("\n[%s] %s" % (n, label))


def check(ok, label, detail=""):
    print("   %s  %s%s" % ("PASS" if ok else "FAIL", label,
                           ("  -- " + detail) if detail else ""))
    if not ok:
        FAILED.append(label)
    return ok


def main():
    fresh = "--fresh" in sys.argv
    os.makedirs(TESTDATA, exist_ok=True)
    # Before step 3 reuses or rewrites the working copy, and before the Studio
    # opens it writable.
    take_lock()

    # ------------------------------------------------------------------- 1
    step(1, "per-user folders")
    d = paths.describe()
    for k, v in d.items():
        print("   %-10s %s" % (k, v))
    # One question split three ways, and the split is the point. The old pair
    # was `isabs` plus `"AppData" in app_dir`, and that substring test passed
    # on every value it existed to reject -- measured, not reasoned:
    #
    #   LOCALAPPDATA                       isabs  "AppData" in   what it is
    #   (unset -- the real one)            True   True           correct
    #   ~\AppData\Local                     False  True           relative (REQ-072)
    #   /tmp/x/myAppDataBackup             True   True           not under AppData
    #   /tmp/Program Files/AppData/Local   True   True           under Program Files
    #
    # Only `isabs` ever failed anything. The label said "not Program Files"
    # and nothing tested Program Files at all -- a guard that names a thing it
    # does not do, which is worse than no guard, because the label is what
    # gets read and trusted.
    app = str(d["app_dir"])
    # Split on BOTH separators. The value is Windows-shaped on Windows and
    # POSIX-shaped here, and REQ-072's broken value carried one of each in the
    # same string. (ntpath.normpath first would also work; this needs no
    # import and does not depend on which platform's rules normpath applies.)
    parts = [x for x in app.replace("\\", "/").split("/") if x]
    check(os.path.isabs(app), "app dir is absolute")
    check("AppData" in parts,
          "AppData is a path COMPONENT, not just text somewhere in the path")
    # Deliberately NOT also requiring a `Local` component. %LOCALAPPDATA% ends
    # in AppData\Local on every normal install, but redirected profiles exist,
    # and a false red in the gate costs more than that extra clause buys.
    check(not any(x.lower().startswith("program files") for x in parts),
          "app dir is not under Program Files")

    # ------------------------------------------------------------------- 2
    step(2, "verify the ISO")
    t = time.perf_counter()
    q = verify.quick(VANILLA)
    T["verify_quick"] = round(time.perf_counter() - t, 4)
    check(q["verdict"] == "likely", "tier 1-2 accepts the real disc",
          "%.1f ms" % (T["verify_quick"] * 1000))

    t = time.perf_counter()
    ident = verify.identify(VANILLA)
    T["verify_identify"] = round(time.perf_counter() - t, 3)
    check(ident["verdict"] == "likely", "tier 3 identifies the game",
          "%s, %.2f s" % (ident.get("boot"), T["verify_identify"]))
    check(ident.get("swat_entries") == 47115, "SWAT.DAT entry count is the tested one")

    bad = verify.quick(os.path.join(TOOLS, "RDR_Studio.py"))
    check(bad["verdict"] == "reject", "a .py file is refused")

    # ------------------------------------------------------------------- 3
    step(3, "working copy")
    if fresh and os.path.exists(WORKING):
        os.remove(WORKING)
    if os.path.exists(WORKING) and os.path.getsize(WORKING) == os.path.getsize(VANILLA):
        print("   (reusing the copy already in testdata)")
    else:
        t = time.perf_counter()
        with open(VANILLA, "rb") as fi, open(WORKING + ".part", "wb") as fo:
            while True:
                buf = fi.read(8 << 20)
                if not buf:
                    break
                fo.write(buf)
        os.replace(WORKING + ".part", WORKING)
        T["copy_seconds"] = round(time.perf_counter() - t, 1)
        print("   copied 2.18 GB in %.1f s" % T["copy_seconds"])
    check(os.path.getsize(WORKING) == os.path.getsize(VANILLA),
          "working copy is byte-count identical")
    check(os.access(WORKING, os.W_OK), "working copy is writable")

    # ------------------------------------------------------------------- 4/5
    step(4, "start the Studio in-process, patched to those folders")
    s = dict(paths.DEFAULTS)
    s.update({
        "vanilla_iso": VANILLA,
        "working_iso": WORKING,
        "mods_dir": os.path.join(TESTDATA, "mods"),
        "checkpoints_dir": os.path.join(TESTDATA, "checkpoints"),
        "exports_dir": os.path.join(TESTDATA, "exports"),
        "accepted_disclaimer": True,
    })
    paths.ensure_dirs(s)

    srv = StudioServer()
    t = time.perf_counter()
    url = srv.start(s, TOOLS)
    waited = srv.wait_ready()
    T["server_start"] = round(time.perf_counter() - t, 3)
    print("   %s  (ready after %.3f s, socket wait %.3f s)"
          % (url, T["server_start"], waited))
    check(srv.port != 8731, "did NOT take port 8731",
          "chose %d, so a Studio already running is untouched" % srv.port)

    from rdrstudio import config as cfg
    check(cfg.MODS == s["mods_dir"], "config.MODS was repointed", cfg.MODS)
    check(cfg.BACKUPS == s["checkpoints_dir"], "config.BACKUPS was repointed")

    from rdrstudio import studio as sm
    check(os.path.isdir(sm.WEB_ROOT), "WEB_ROOT resolves", sm.WEB_ROOT)

    # ------------------------------------------------------------------- 6
    step(5, "the API and front end really answer on that port")

    def get(path):
        t0 = time.perf_counter()
        with urllib.request.urlopen(url + path, timeout=30) as r:
            body = r.read()
        return body, r.status, round(time.perf_counter() - t0, 3)

    body, code, ms = get("api/config")
    cfgj = json.loads(body)
    check(code == 200, "GET api/config", "%.0f ms" % (ms * 1000))
    check(str(cfgj.get("modded", "")).endswith("RDR_WORKING.iso"),
          "the API reports the per-user working ISO, not the repo folder",
          str(cfgj.get("modded")))

    body, code, ms = get("api/archives")
    arcs = json.loads(body)
    n = len(arcs.get("archives", arcs) if isinstance(arcs, dict) else arcs)
    check(code == 200 and isinstance(n, int) and n >= 66, "GET api/archives lists the game's 66 archives (or more)",
          "%.0f ms" % (ms * 1000))

    body, code, ms = get("api/levels")
    lv = json.loads(body)
    n = len(lv.get("levels", lv) if isinstance(lv, dict) else lv)
    check(code == 200 and n > 30, "GET api/levels", "%d levels, %.0f ms" % (n, ms * 1000))
    T["api_levels_ms"] = round(ms * 1000)

    for f, minkb in (("", 40), ("app.js", 300), ("three.module.js", 1000)):
        body, code, ms = get(f)
        kb = len(body) / 1024.0
        check(code == 200 and kb > minkb,
              "GET /%s" % (f or "index.html"), "%.0f KB in %.0f ms" % (kb, ms * 1000))

    # ------------------------------------------------------------------- 7
    step(6, "shut down")
    srv.stop()
    check(True, "stopped without error")

    print("\ntimings: %s" % json.dumps(T))
    if FAILED:
        print("\n%d FAILED: %s" % (len(FAILED), "; ".join(FAILED)))
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        # Also on the exit(2) refusal path: that raises SystemExit from inside
        # take_lock() BEFORE the file is written, so the holder's lock is not
        # ours to remove -- but drop_lock() would remove it anyway. Guarded by
        # only dropping a lock this process wrote.
        if _LOCK_IS_OURS:
            drop_lock()
