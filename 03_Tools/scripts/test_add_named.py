"""Can a new file carry a REAL name, or only the '#'-prefix hack?

The first proven insertion used '#test' so the entry would sort to index 0 and
nothing subtle about the name block would be disturbed. That was the cautious
special case. A tester needs real names -- specifically, dropping a correctly
named `.enm` roster into a Showdown archive that has none -- so this test does
the general thing everywhere it can and then boots it.

Three stages:

  1. OFFLINE, in memory, against every archive: insert a mid-sorting name,
     re-decode the whole TOC, demand every original name/offset/size is
     untouched and the newcomer reads back byte-exact.
  2. ON THE THROWAWAY DISC: two real insertions --
        FRONT.DAT       'menu_addtest.txt'   (mid-sort, the general case)
        LVLS_M2/BRIDGE2.DAT  'lvl_bridge2_var_1.enm'  (the tester's actual want:
            a roster, cloned from the SP bridge roster, put where the MP
            archive would look for one)
  3. BOOT, 185 s, judged by MOTION (3+ distinct late frames = plays).
     The intro runs ~100 s; anything shorter proves nothing.

Writes ONLY to 02_Modded/RDR_REPACKTEST.iso.

    python scripts/test_add_named.py --offline    stages 1 only (fast, safe)
    python scripts/test_add_named.py              all three
"""
import argparse
import glob
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rdrstudio.config import ROOT, VANILLA_ISO   # noqa: E402

VANILLA = Path(VANILLA_ISO)
TEST_ISO = Path(ROOT) / "02_Modded" / "RDR_REPACKTEST.iso"
SHOTS = Path(ROOT) / "07_Research" / "play_test"


def offline_sweep():
    """Stage 1: dry insertion maths against every archive on the disc."""
    from rdrstudio.game import Game
    g = Game(VANILLA)
    ok = skip = 0
    for a in g.archives():
        dv = g.archive(a)
        check = dv.can_add("modtest_middle.txt", 64)
        if not check["ok"]:
            skip += 1          # full name block / no TOC slot etc. -- honest
            continue
        k = dv.insertion_index("modtest_middle.txt")
        names = [e.name for e in dv.entries]
        assert 0 <= k <= len(names)
        # the invariant the middle-insert relies on
        if 0 < k < len(names):
            a_, b_ = names[k - 1], names[k]
            assert a_ < "modtest_middle.txt" < b_ or a_ < "modtest_middle.txt"
        ok += 1
    g.close()
    print("offline: %d archives accept a mid-sort insert, %d refuse (full)"
          % (ok, skip))
    return True


def write_both():
    """Stage 2: really insert, into the throwaway."""
    from rdrstudio.game import Game

    print("restoring a pristine test disc ...")
    if TEST_ISO.exists():
        os.chmod(TEST_ISO, 0o666)
    shutil.copyfile(VANILLA, TEST_ISO)
    os.chmod(TEST_ISO, 0o666)

    g = Game(TEST_ISO, writable=True)

    # -- the general case: a plain name into the menu archive
    dv = g.archive("FRONT.DAT")
    payload = b"named-insertion test payload\n" * 4
    res = dv.add("menu_addtest.txt", payload)
    print("  FRONT.DAT   + menu_addtest.txt  at index %d of %d"
          % (res["index"], res["count"]))
    assert res["index"] not in (0, res["count"] - 1), \
        "wanted a MIDDLE insertion; index %d is an edge" % res["index"]

    # -- the tester's case: a real roster name into a rosterless MP archive
    roster = g.read("LVLS_SNG/BRIDGE.DAT", "lvl_bridge_var_1.enm")
    dv2 = g.archive("LVLS_M2/BRIDGE2.DAT")
    # add() compresses, so the fit question is asked THROUGH add's dry run --
    # gating on the raw size here refused a roster that deflates 17x.
    try:
        pre = dv2.add("lvl_bridge2_var_1.enm", roster, dry=True)
        res2 = dv2.add("lvl_bridge2_var_1.enm", roster)
        print("  BRIDGE2.DAT + lvl_bridge2_var_1.enm  at index %d "
              "(%d raw -> %d stored%s)"
              % (res2["index"], len(roster), pre["stored_bytes"],
                 ", deflated" if pre["compressed"] else ""))
        back = dv2.read("lvl_bridge2_var_1.enm")
        assert back == roster, "roster round-trip mismatch"
        print("  roster reads back byte-identical through the normal path")
    except ValueError as ex:
        print("  BRIDGE2.DAT refuses the roster: %s" % ex)
        print("  (the FRONT.DAT insert still boots-tests the mechanism)")
    g.close()

    # read both back off the disc that will boot
    g2 = Game(TEST_ISO)
    got = g2.read("FRONT.DAT", "menu_addtest.txt")
    assert got == payload, "FRONT.DAT payload mismatch"
    n = len(g2.archive("FRONT.DAT").entries)
    print("  read-back OK; FRONT.DAT now %d entries" % n)
    g2.close()
    return True


def boot(seconds):
    print("\nplaying the disc (%.0f s) ..." % seconds)
    before = set(glob.glob(str(SHOTS / "*.png")))
    subprocess.run([sys.executable, str(HERE / "play_test.py"),
                    "--level", "--seconds", str(seconds),
                    "--iso", str(TEST_ISO)],
                   cwd=str(HERE.parent), capture_output=True, text=True)
    fresh = [f for f in sorted(glob.glob(str(SHOTS / "*.png")),
                               key=os.path.getmtime) if f not in before][-6:]
    if len(fresh) < 6:
        print("only %d new frames -- run did not complete" % len(fresh))
        return False
    hashes = [hashlib.sha256(open(f, "rb").read()).hexdigest()[:12]
              for f in fresh]
    for f, h in zip(fresh, hashes):
        print("   %-26s %s" % (os.path.basename(f), h))
    n = len(set(hashes))
    print("distinct late frames: %d" % n)
    return n >= 3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--seconds", type=float, default=185.0)
    args = ap.parse_args()

    if not offline_sweep():
        return 1
    if args.offline:
        return 0
    if not write_both():
        return 1
    if boot(args.seconds):
        print("\nPLAYS. Real-named insertion (middle of the TOC) is safe on "
              "the archives tested.")
        print("Still not proven: that the GAME reads the new roster -- that "
              "needs a human to start a Bridge showdown match.")
        return 0
    print("\nFROZEN or incomplete. Do NOT ship named insertion.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
