"""Handing the disc to PCSX2 and getting it back.

This covers the oldest recurring bug in the project — "I press Play and the
emulator says permission denied" — and three separate bugs found while fixing
it. All four are regression-tested here because every one of them was invisible
under the old behaviour.

  1. **The Studio must not reclaim the disc while the emulator is starting.**
     The original code released the handle and let `ensure_open` take it back
     after a 3-second throttle. PCSX2 takes longer than that to reach the CDVD,
     so the Studio won the race every time — and the 4-second disc-state poll
     triggered it with no user action at all.

  2. **"Looks free" is not "finished with".** The first fix reclaimed as soon
     as the file was openable. Straight after a handover it IS openable,
     because the emulator has not got to it yet, so that reclaimed in two
     seconds and fixed nothing. The real test needs a grace window plus
     whether the emulator was ever actually seen holding it.

  3. **Browsing must keep working during a handover.** The handover closed
     the pristine disc as well, which nothing needed, breaking every read.

  4. **Cached Levels hold the closed handle.** A `Level` built before the
     handover keeps a Dave bound to that file; reading a stale one raises
     "short read ... got 0", which names nothing useful. `hand_over` has to
     drop the level cache.

Bugs 3 and 4 existed before any of this and were simply unreachable: the old
reclaim happened before anything could read.
"""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8731/api/"
LEVEL = "LVLS_SNG/TOMB.DAT"
WATCH = 12               # seconds to watch the disc stay handed over


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=300) as fh:
        return json.load(fh)


def post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as fh:
        return json.load(fh)


def main():
    ok = True
    start_dirty = len(get("diff"))
    print("disc dirty before : %d" % start_dirty)

    # Warm the level cache so bug 4 is actually reachable.
    warm = get("level?archive=%s" % LEVEL)
    print("cached level      : %s, %d nodes" % (warm["name"], len(warm["nodes"])))

    post("release", {})
    st = get("disc_state")
    print("\nafter release     : handed_over=%s studio_holds=%s free=%s"
          % (st["handed_over"], st["studio_holds"], st["free_for_emulator"]))
    ok &= st["handed_over"] and not st["studio_holds"]

    # 1 + 2: the disc must STAY free while nothing has taken it, even under
    # the polling the UI does. This is the whole bug.
    print("\nwatching %ds under the UI's own polling:" % WATCH)
    stolen = None
    read_fail = 0
    for i in range(WATCH):
        time.sleep(1)
        d = get("disc_state")
        try:
            # 3 + 4: reads must keep working, including of the cached level.
            get("level?archive=%s" % LEVEL)
            get("levels")
        except Exception as ex:
            read_fail += 1
            if read_fail == 1:
                print("    read failed: %s" % str(ex)[:70])
        if d["studio_holds"] and stolen is None:
            stolen = i + 1
    print("  reclaimed early : %s"
          % ("no" if stolen is None else "YES at t+%ds" % stolen))
    print("  read failures   : %d / %d" % (read_fail, WATCH))
    ok &= stolen is None
    ok &= read_fail == 0

    # An explicit edit is a request for the disc and must override the grace
    # window — otherwise the tool would feel broken for 90 seconds.
    print("\nan edit should reclaim the disc immediately:")
    strings = get("strings?q=story_profile")
    row = next((r for r in strings["strings"]
                if r["name"] == "story_profile"), None)
    if row is None:
        print("  (story_profile missing; skipping the write half)")
    else:
        same = post("string_set", dict(name="story_profile", text=row["text"],
                                       slot=0, copies=row["copies"],
                                       dry=True))
        held = get("disc_state")["studio_holds"]
        print("  dry-run write ok=%s, studio_holds now %s" % (same["ok"], held))
        ok &= held

    final = len(get("diff"))
    print("\ndisc dirty after  : %d  (expect %d)" % (final, start_dirty))
    ok &= final == start_dirty

    print("\n%s" % ("ALL CHECKS PASSED" if ok else "SOMETHING FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
