"""End-to-end proof that an edit reaches the ISO and nothing else moves.

Drives the running Studio over HTTP:
  1. load a level, pick an enemy
  2. dry-run a move -> must report it fits
  3. apply it
  4. reload and confirm the new position came back from the ISO
  5. confirm the vanilla ISO is untouched and only ONE file differs
  6. put the disc back EXACTLY as it was found, mods and all

**This test used to fail, and destroy data, whenever the file it edits was
already modified.** Both faults are worth naming because the comment above
them claimed the opposite -- that baselining meant it "never reports someone
else's legitimate edit as a failure":

  * the changed-file check subtracted the baseline from the diff and then
    demanded exactly one NEW entry. That protects every file EXCEPT the one
    this test edits: if the subject file was already modified, this test's own
    edit adds no new entry, so the count came out 0 and the check failed.
  * the revert called `revert_file`, which restores from VANILLA. The test
    then asserted both that the position matched the BASELINE and that the
    file was clean against vanilla -- two conditions that cannot both hold
    unless the baseline was vanilla to begin with. So it failed, and on the
    way it overwrote the pre-existing edit with vanilla bytes.

Measured on the 2026-09-18 06:00 gate: the working disc carried an edit to
`lvl_mans_var_1.enm` putting Griffin#1 at x=2.446219. This test reverted it to
vanilla's 0.446219, reported FAILED, and the edit was gone. The next run, on
the now-clean disc, passed -- which is how a data-destroying bug reads as a
flake.

Both are fixed by checkpointing instead of reverting: `checkpoint` stores
every file that differs from vanilla and `checkpoint_restore` puts them all
back byte-for-byte, so the disc ends where it started whatever state it was
in. That also preserves the property the old revert was reaching for -- the
restore is byte-exact, so it cannot leave a re-serialised float behind.
"""

import json
import os
import sys
import urllib.request

BASE = "http://127.0.0.1:8731/api/"
ARCHIVE = "LVLS_SNG/MANS.DAT"


# 900, matching what run_tests.py already uses for its own checkpoint
# (bank_checkpoint, timeout=900). MEASURED by rdr-3d, 2026-09-18, and the
# numbers are the argument:
#
#     273s   /api/diff on a freshly copied 2.34 GB image, nothing warm
#     212s   first call after a write invalidates it
#      80s   fully warm, same image, same process
#
# `checkpoint()` is built on `diff()` -- its own docstring says so -- so a
# restore point costs whatever a cold diff costs. The old ceilings were 120s
# on get() and 300s on post(), and the 120s is the thinner of the two: the
# BASELINE call at the top of main() is a get("diff"), so on a cold disc this
# test died before it ever reached the checkpoint that makes it safe.
#
# In normal operation the disc is warm -- thirty-odd offline sweeps run first
# -- and the real margin was ~220s, not the ~27s a worst-case-against-300
# reading suggests. **That warmth is a dependency on run ORDER and it is
# invisible in this file**, which is the actual reason to raise the ceiling
# rather than rely on it: first run after a reboot, after Restore Vanilla, or
# against a freshly copied disc is the cold case, and those are exactly the
# runs where a lost restore point matters most.
#
# A client timeout does NOT cancel the server's work: the Studio keeps
# computing, everything behind it queues on the one lock, and the symptom
# surfaces as the NEXT row stalling rather than this one failing. So the cost
# of a ceiling that is too low is paid by somebody else's test.
TIMEOUT = 900


def get(route):
    with urllib.request.urlopen(BASE + route, timeout=TIMEOUT) as r:
        return json.load(r)


def post(route, body):
    req = urllib.request.Request(
        BASE + route, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.load(r)


def main():
    ok = True
    # The working ISO may already carry real mods. Baseline first and compare
    # deltas, so this test never assumes a pristine disc -- and never reports
    # someone else's legitimate edit as a failure.
    print("baselining existing modifications...")
    baseline = {(x["archive"], x["name"]) for x in get("diff")}
    print("already modified: %d file(s)" % len(baseline))
    for a, n in sorted(baseline):
        print("   (pre-existing) %s :: %s" % (a, n))

    # Take the restore point BEFORE touching anything. A checkpoint stores the
    # bytes of every file that differs from vanilla, so restoring it later puts
    # the disc back exactly as found -- including edits this test did not make
    # and must not destroy. On a clean disc it stores nothing and the restore
    # reverts our one edit, which is the old behaviour exactly.
    try:
        cp = post("checkpoint", dict(label="auto_before_roundtrip"))
    except Exception as ex:                                    # noqa: BLE001
        # Nothing has been written at this point -- the restore point is taken
        # before the first edit -- so failing here costs the run and not the
        # disc. Said explicitly rather than left to a traceback, because a
        # traceback here and a traceback after the edit mean opposite things.
        print("   REFUSING: the restore point could not be taken (%s: %s)."
              % (type(ex).__name__, ex))
        print("   Nothing has been written; the disc is as it was found.")
        return 1
    cp_path = cp.get("path")
    print("restore point    : %s (%d file(s))"
          % (os.path.basename(cp_path or "<none>"), len(baseline)))
    if not cp_path:
        print("   REFUSING: no restore point, so a failure could not be undone")
        return 1
    print()

    lvl = get("level?archive=" + ARCHIVE.replace("/", "%2F") + "&reload=1")
    enemy = next(n for n in lvl["nodes"] if n["kind"] == "enemy")
    uid, before = enemy["uid"], list(enemy["pos"])
    target = [round(before[0] + 3.5, 6), before[1], round(before[2] - 2.25, 6)]
    print("enemy      : %s (uid %d)" % (enemy["name"], uid))
    print("source     : %s" % enemy["source"])
    print("before     : %s" % before)
    print("target     : %s" % target)

    dry = post("move", dict(archive=ARCHIVE, changes=[dict(uid=uid, pos=target)], dry=True))
    rep = list(dry["report"].values())[0]
    print("dry run    : stored %d / slot %d -> %s"
          % (rep["stored"], rep["slot"], "FITS" if rep["fits"] else "TOO BIG"))
    if not rep["fits"]:
        return 1

    res = post("move", dict(archive=ARCHIVE, changes=[dict(uid=uid, pos=target)]))
    print("applied    : %s  %s" % (res.get("applied"), res.get("written")))
    if not res.get("applied"):
        return 1

    lvl2 = get("level?archive=" + ARCHIVE.replace("/", "%2F") + "&reload=1")
    after = next(n for n in lvl2["nodes"] if n["uid"] == uid)
    print("read back  : %s" % after["pos"])
    moved = all(abs(a - b) < 1e-4 for a, b in zip(after["pos"], target))
    print("MATCH      : %s" % ("YES" if moved else "NO"))
    ok &= moved

    print("node count : %d -> %d %s"
          % (len(lvl["nodes"]), len(lvl2["nodes"]),
             "(unchanged)" if len(lvl["nodes"]) == len(lvl2["nodes"]) else "(CHANGED!)"))
    ok &= len(lvl["nodes"]) == len(lvl2["nodes"])

    print("\ndiffing working ISO against vanilla (reads both discs)...")
    d = get("diff")
    now = {(x["archive"], x["name"]) for x in d}
    for x in d:
        if (x["archive"], x["name"]) not in baseline:
            print("   %-22s %-32s %d -> %d bytes"
                  % (x["archive"], x["name"], x["vanilla_size"], x["size"]))

    # Assert the SET, not a count of new entries. After this edit the modified
    # set must be exactly what it was plus our subject file -- and our subject
    # file may well have been in it already, which is the case the old count
    # could not express. Anything else means we moved something we did not
    # mean to.
    want = baseline | {(ARCHIVE, enemy["source"])}
    only_one = now == want
    print("this test changed exactly one file, the expected one : %s"
          % ("YES" if only_one else "NO"))
    if not only_one:
        for k in sorted(now - want):
            print("   UNEXPECTED  %s :: %s" % k)
        for k in sorted(want - now):
            print("   MISSING     %s :: %s" % k)
    ok &= only_one

    # Put the disc back BYTE-exactly, whatever state it was found in. This
    # restores the checkpoint rather than reverting to vanilla: writing the
    # original numbers back would re-serialise the float and leave a byte
    # difference, and reverting to vanilla would silently discard a
    # pre-existing edit -- which is exactly what it used to do.
    try:
        rr = post("checkpoint_restore", dict(path=cp_path))
    except Exception as ex:                                    # noqa: BLE001
        # THIS one is the dangerous end, and it is the reason the ceiling was
        # raised rather than the checkpoint end. By here the edit HAS been
        # applied, so a failure leaves the disc dirty -- and a client timeout
        # does not cancel the server's work, so the Studio may well finish the
        # restore after this process has given up. Neither "it is dirty" nor
        # "it is clean" can be asserted from here, so say exactly that and
        # name the file a human needs.
        print("   RESTORE DID NOT COMPLETE: %s: %s" % (type(ex).__name__, ex))
        print("   THE DISC MAY BE DIRTY. This test HAS applied its edit and "
              "cannot confirm it was undone.")
        print("   A client timeout does not cancel the Studio's work, so the "
              "restore may yet finish on its own.")
        print("   Restore by hand if it did not:  %s" % cp_path)
        return 1
    if not rr.get("ok"):
        print("   RESTORE REPORTED FAILURE: %s" % rr.get("failed"))
    ok &= bool(rr.get("ok"))
    lvl3 = get("level?archive=" + ARCHIVE.replace("/", "%2F") + "&reload=1")
    restored = next(n for n in lvl3["nodes"] if n["uid"] == uid)
    same = all(abs(a - b) < 1e-4 for a, b in zip(restored["pos"], before))

    # "Clean" means back to the BASELINE, not back to vanilla. On a clean disc
    # those are the same thing; on a dirty one only this is correct.
    after_set = {(x["archive"], x["name"]) for x in get("diff")}
    clean = after_set == baseline
    print("restored   : %s -> %s" % (restored["pos"], "OK" if same else "FAILED"))
    print("disc back to baseline (%d modified file(s))      : %s"
          % (len(baseline), "YES" if clean else "NO"))
    if not clean:
        for k in sorted(after_set - baseline):
            print("   LEFT BEHIND %s :: %s" % k)
        for k in sorted(baseline - after_set):
            print("   DESTROYED   %s :: %s" % k)
    ok &= same and clean

    print("\n%s" % ("ALL CHECKS PASSED" if ok else "SOMETHING FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
