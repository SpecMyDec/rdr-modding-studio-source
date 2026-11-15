"""Weapon editing across every copy of swat.guns on the disc.

Most gameplay files exist in SWAT.DAT *and* every level archive, and the level
copy wins at runtime -- so a weapon mod that only touches SWAT.DAT appears to
do nothing in game. This proves all copies get patched, and that undo walks
every one of them back.
"""

import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8731/api/"
WEAPON = "Old_Pistol"


# 900s, not 600s. The precondition check below calls get("diff"), which is the
# call that costs 273s on a cold 2.34 GB image and 212s on the first call after
# a write -- rdr-3d measured it. A 12:29 run of this row died on exactly that
# call at 618.7s, after its writes had landed and before its cleanup ran, which
# is how 62 modified swat.guns were left on the disc. Same ceiling c2a7537 gave
# test_roundtrip, for the same reason.
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

    # REFUSE on a dirty disc. Do not clean it.
    #
    # This used to be:
    #
    #     for x in get("diff"):
    #         post("revert_file", {"archive": x["archive"], "name": x["name"]})
    #
    # which reverted EVERY modified file on the working ISO to vanilla --
    # not just swat.guns -- unconditionally, before the first assertion, on a
    # registered LIVE row that runs on every full suite. Whatever was on the
    # disc when a gate started was gone by the third line of this file: a
    # lane's experiment, a half-finished edit, or the developer's own work. On
    # 2026-09-18 the disc carried 149 modified entries he had confirmed as
    # intentional ("the 41 are intentional, leave them") and that composition
    # -- swat.guns and swat.strtbl across LVLS_M2/M4, lvl_front_ground.bnds,
    # lvl_int2.rsc -- is exactly what this loop takes.
    #
    # The damage is worse than debris left behind, because it happens in SETUP:
    # the run that destroys the disc is the run that then reports green, so
    # there is nothing afterwards to find.
    #
    # What the test actually needs is not a clean disc but to KNOW the disc is
    # clean -- its "diff after undo" and "stats restored" assertions are only
    # meaningful against a known baseline. Refusing gives it that and never
    # writes. An unsuitable state must not be silently normalised into a
    # suitable one; see CLAUDE.md on the exit-code split, and REQ-076 for the
    # same shape in test_roundtrip.
    # CONTENT-dirty, not stored-byte-dirty, and the difference is not cosmetic.
    # Measured 2026-09-18: the working ISO had 149 files whose STORED bytes
    # differ from vanilla and 0 whose CONTENT differs. A file that is patched
    # and later reverted comes back content-identical but re-deflated, and
    # never byte-matches vanilla's original stored form again. That residue is
    # permanent and cannot be cleaned. get("diff") decompresses and compares
    # content, which is why it answers 0 and this refusal passes.
    #
    # DO NOT "optimise" this into a size or stored-byte comparison to avoid
    # decompressing 66 archives. The fast check would refuse forever on a
    # perfectly clean disc. The slow check is the correct one.
    #
    # And a timeout is NOT a clean disc. get("diff") is the call that died at
    # 618.7s on 2026-09-18; if it cannot answer, the precondition is
    # UNESTABLISHED, which must refuse rather than proceed -- otherwise the
    # slow path silently green-lights the destructive one.
    try:
        dirty = get("diff")
    except Exception as ex:                                    # noqa: BLE001
        print("REFUSED: could not establish whether the disc is clean.")
        print("   %s: %s" % (type(ex).__name__, str(ex)[:120]))
        print("\nNothing has been run and nothing has been written.")
        print("An undetermined answer is not a clean disc. If this was a")
        print("timeout, the first diff on a cold image costs ~273s -- re-run")
        print("once the page cache is warm.")
        return 2

    if dirty:
        print("REFUSED: the working ISO carries %d modified file(s)." % len(dirty))
        for x in dirty[:20]:
            print("   %s :: %s" % (x["archive"], x["name"]))
        if len(dirty) > 20:
            print("   ... and %d more" % (len(dirty) - 20))
        print("\nNothing has been run and nothing has been written.")
        print("This test needs a known-clean baseline. Restore or checkpoint")
        print("the disc deliberately, then re-run -- it will not do it for you.")
        return 2

    # How deep the journal already is. Undoing past this point would walk back
    # edits belonging to other tests -- or to the user.
    floor = len(get("history")["entries"])

    info = get("guns")
    copies = info["copies"]
    print("swat.guns copies on disc : %d" % copies)

    w = get("gun?name=" + WEAPON)
    before = {k: w["weapon"]["fields"][k] for k in ("Damage", "FullLoad", "Range")}
    print("%s stock             : %s" % (WEAPON, before))

    changes = {"Damage": 100.0, "FullLoad": 99, "Range": 500.0}
    dry = post("gun_save", dict(name=WEAPON, changes=changes, dry=True))
    print("dry run                  : %d fit, %d too big"
          % (len(dry["applied"]), len(dry["failed"])))
    for f in dry["failed"][:5]:
        print("   %s: %s" % (f["archive"], f["error"]))
    ok &= not dry["failed"]

    res = post("gun_save", dict(name=WEAPON, changes=changes))
    print("applied                  : %d archive(s), %d failed"
          % (len(res["applied"]), len(res["failed"])))
    ok &= len(res["applied"]) == copies and not res["failed"]

    w2 = get("gun?name=" + WEAPON)
    after = {k: w2["weapon"]["fields"][k] for k in changes}
    print("read back                : %s" % after)
    ok &= all(abs(after[k] - v) < 1e-6 for k, v in changes.items())

    d = get("diff")
    print("files differing          : %d (expect %d)" % (len(d), copies))
    ok &= len(d) == copies

    # Every write went through the journal, so undo must walk all of them
    # back. Drive the loop off the DISC, not off the history length:
    # `history` is a windowed, group-collapsed view (`entries[-200:]` then
    # merged), so its row count can fall as history grows and makes a
    # useless stopping condition.
    h = get("history")
    print("journal rows             : %d (windowed view; was %d before)"
          % (len(h["entries"]), floor))
    print("undoing this test's writes...")
    n = 0
    while n <= copies + 5:
        if not [x for x in get("diff") if x["name"] == "swat.guns"]:
            break
        if not get("history")["can_undo"]:
            break
        post("undo", {})
        n += 1
    print("undone                   : %d" % n)

    # Judge only the files THIS test touched. Asserting on the whole disc made
    # the verdict depend on whatever ran earlier, so one unrelated leftover
    # was reported here as a weapon-editor failure.
    all_diff = get("diff")
    d2 = [x for x in all_diff if x["name"] == "swat.guns"]
    others = [x for x in all_diff if x["name"] != "swat.guns"]
    print("diff after undo          : %d swat.guns %s"
          % (len(d2), "CLEAN" if not d2 else ""))
    if others:
        print("  note: %d unrelated file(s) were already modified (%s) -- "
              "not this test's doing"
              % (len(others), ", ".join(sorted({x["name"] for x in others})[:4])))
    ok &= not d2

    w3 = get("gun?name=" + WEAPON)
    restored = {k: w3["weapon"]["fields"][k] for k in before}
    print("stats restored           : %s %s"
          % (restored, "OK" if restored == before else "MISMATCH"))
    ok &= restored == before

    print("\n%s" % ("ALL CHECKS PASSED" if ok else "SOMETHING FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
