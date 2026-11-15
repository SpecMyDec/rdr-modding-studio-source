"""Undo/redo and mod save/load.

Checks:
  1. three edits, then undo x3 -> disc is byte-identical to vanilla again
  2. redo x3                   -> the edits come back exactly
  3. save mod, restore-to-vanilla per file, load mod -> edits reproduced
  4. undo after a NEW edit discards the redo stack (standard semantics)
"""

import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8731/api/"
ARC = "LVLS_SNG/MANS.DAT"


def get(route):
    with urllib.request.urlopen(BASE + route, timeout=300) as r:
        return json.load(r)


def post(route, body):
    req = urllib.request.Request(
        BASE + route, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


def load():
    return get("level?archive=" + ARC.replace("/", "%2F") + "&reload=1")


def pos_of(uid):
    return next(n["pos"] for n in load()["nodes"] if n["uid"] == uid)


def checks():
    """The four checks. The disc is clean when this is entered."""
    ok = True
    lvl = load()
    enemies = [n for n in lvl["nodes"] if n["kind"] == "enemy"][:3]
    originals = {n["uid"]: list(n["pos"]) for n in enemies}
    print("moving %d enemies" % len(enemies))

    for i, n in enumerate(enemies):
        tgt = [round(n["pos"][0] + 2.0 + i, 6), n["pos"][1], n["pos"][2]]
        post("move", dict(archive=ARC, changes=[dict(uid=n["uid"], pos=tgt)]))
    moved = {u: pos_of(u) for u in originals}
    changed = sum(1 for u in originals if moved[u] != originals[u])
    print("  positions changed : %d/%d" % (changed, len(originals)))
    ok &= changed == len(originals)

    h = get("history")
    print("  journal entries   : %d  (can_undo=%s)" % (len(h["entries"]), h["can_undo"]))
    ok &= h["can_undo"]

    print("\nundo x3")
    for _ in range(3):
        r = post("undo", {})
        print("  undid: %s" % r["label"])
    d = get("diff")
    print("  diff vs vanilla   : %d file(s) %s" % (len(d), "CLEAN" if not d else d))
    ok &= not d

    print("\nredo x3")
    for _ in range(3):
        r = post("redo", {})
        print("  redid: %s" % r["label"])
    back = {u: pos_of(u) for u in originals}
    same = all(back[u] == moved[u] for u in originals)
    print("  positions restored: %s" % ("YES" if same else "NO"))
    ok &= same

    print("\nsave mod")
    m = post("mod_save", dict(name="journal test", notes="automated"))
    print("  wrote %s (%d file(s))" % (m["path"], m["files"]))

    print("\nrevert every touched file, then re-apply the mod")
    for x in get("diff"):
        post("revert_file", {"archive": x["archive"], "name": x["name"]})
    print("  after revert      : %d diff(s)" % len(get("diff")))
    r = post("mod_load", dict(path=m["path"]))
    print("  applied %d file(s), %d failed" % (len(r["applied"]), len(r["failed"])))
    after_mod = {u: pos_of(u) for u in originals}
    reproduced = all(after_mod[u] == moved[u] for u in originals)
    print("  mod reproduced edits: %s" % ("YES" if reproduced else "NO"))
    ok &= reproduced and not r["failed"]

    print("\nredo stack is discarded by a new edit")
    post("undo", {})
    u = enemies[0]["uid"]
    post("move", dict(archive=ARC, changes=[dict(uid=u, pos=originals[u])]))
    h = get("history")
    print("  can_redo after new edit: %s (expect False)" % h["can_redo"])
    ok &= not h["can_redo"]

    print("\ncleanup")
    for x in get("diff"):
        post("revert_file", {"archive": x["archive"], "name": x["name"]})
    print("  final diff        : %d" % len(get("diff")))
    ok &= not get("diff")

    return ok


def hand_back(saved, banked):
    """Give the user their disc back, and PROVE it is back.

    The restore call's own answer does not settle it: it reports what it
    applied, not what the disc now holds, and a restore that applies nothing
    still answers ok. So the disc is re-read and compared against the state
    this test found it in.
    """
    if not saved or not saved.get("path"):
        return True
    path = saved["path"]
    print("\nhanding your disc back")
    try:
        r = post("checkpoint_restore", {"path": path})
    except Exception as ex:                                   # noqa: BLE001
        print("  RESTORE DID NOT COMPLETE: %s: %s" % (type(ex).__name__, ex))
        print("  the %d file(s) this test reverted are NOT back on the disc."
              % len(banked))
        print("  every one of them is recoverable from:")
        print("    %s" % path)
        return False
    print("  restore reports: %d applied, %d failed"
          % (r.get("applied", 0), len(r.get("failed") or [])))
    now = {(x["archive"], x["name"]): x["size"] for x in get("diff")}
    if now == banked:
        print("  disc matches the state it was found in: %d file(s)"
              % len(banked))
        return True
    print("  HAND-BACK FAILED: the disc is NOT how this test found it.")
    for a, f in sorted(set(banked) - set(now)):
        print("    missing     %s :: %s" % (a, f))
    for a, f in sorted(set(now) - set(banked)):
        print("    unexpected  %s :: %s" % (a, f))
    for k in sorted(set(now) & set(banked)):
        if now[k] != banked[k]:
            print("    wrong size  %s :: %s  %d bytes, expected %d"
                  % (k[0], k[1], now[k], banked[k]))
    print("  every banked file is recoverable from:")
    print("    %s" % path)
    return False


def main():
    # This test needs a clean baseline to reason about its own diffs -- but
    # the working ISO belongs to the user, and wiping it destroyed real work
    # once: a 512-file preset they had applied, gone in a test run they never
    # asked for. Bank the current state first and hand it back at the end.
    #
    # The hand-back used to be the last statement of the checks, which made it
    # two things it could not afford to be: skippable, because anything that
    # raised above it jumped straight over it, and unasserted, because nothing
    # ever looked at whether it worked. On 2026-09-19 the same 75 files were
    # banked at 13:04, reverted, and never came back -- and this test reported
    # PASS, because `ok` had already been decided above. See REQ-086.
    saved = None
    banked = {}
    d = get("diff")
    if d:
        try:
            saved = post("checkpoint", {"label": "auto: before test_journal"})
            print("disc is not clean; banked %d file(s) first" % len(d))
            print("  %s" % saved.get("path", "?"))
        except Exception as ex:                               # noqa: BLE001
            # EXIT 2, not 1. CLAUDE.md reserves 2 for "it could not start,
            # nothing was run", and that is exactly this: the checks have not
            # run, the disc has not been touched, and no verdict about the
            # journal exists. A 1 here reads as "the journal is broken" to
            # anyone scanning the tail of a log -- a refusal wearing a
            # failure's exit code. run_tests reports both as FAIL either way;
            # the distinction is for the person reading afterwards, which is
            # the only place it was ever going to matter.
            #
            # Spotted by rdr-re while repairing the hand-back (REQ-086) and
            # left for me, since the row is mine.
            print("could not bank a checkpoint (%s) -- refusing to wipe the "
                  "working ISO. Restore vanilla yourself if you want this "
                  "test to run." % ex)
            print("Nothing has been run.")
            return 2
        banked = {(x["archive"], x["name"]): x["size"] for x in d}
        for x in d:
            post("revert_file", {"archive": x["archive"], "name": x["name"]})

    # finally, not a trailing statement: the disc goes back whatever the
    # checks did, including raise.
    ok = False
    try:
        ok = checks()
    finally:
        returned = hand_back(saved, banked)

    ok = bool(ok) and returned
    print("\n%s" % ("ALL CHECKS PASSED" if ok else "SOMETHING FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
