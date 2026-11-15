"""Which archive does repacking actually break?

Repacking FRONT.DAT is safe -- verified: the ISO boots, reaches the title,
and the menu responds exactly as the pristine disc does. Repacking all 65
archives is NOT safe: the game draws the title logo and then freezes before
the "Press START button" prompt ever appears. Both runs pass every offline
check (every file reads back byte-identically), so the difference is only
visible by running the game.

This automates the search. For each candidate set it restores a pristine test
ISO, repacks just that set, plays the disc, and decides from the screenshots.

**The decision rule, and why it is this one.** The obvious test -- "is the
screen black" -- is useless here, because the failure is not a black screen;
it is a perfectly good title screen that never changes. So the measure is
MOTION: hash the six screenshots taken after the intro ends and count the
distinct ones. A working disc is being driven through its menu and yields
three or more. A frozen one yields exactly one, byte-for-byte, over 76
seconds. Vanilla and a FRONT.DAT-only repack both score 3; the all-archive
repack scores 1.

    python scripts/bisect_repack.py STREAMS.DAT
    python scripts/bisect_repack.py BANKS.DAT SWAT.DAT
    python scripts/bisect_repack.py --each BANKS.DAT STREAMS.DAT SWAT.DAT

Only ever writes to 02_Modded/RDR_REPACKTEST.iso.
"""
import argparse
import glob
import hashlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rdrstudio.config import ROOT, VANILLA_ISO   # noqa: E402

VANILLA = Path(VANILLA_ISO)
TEST_ISO = Path(ROOT) / "02_Modded" / "RDR_REPACKTEST.iso"
SHOTS = Path(ROOT) / "07_Research" / "play_test"
LATE = 6          # screenshots taken after the intro ends
PASS_AT = 3       # distinct frames a working disc reaches


def restore():
    if TEST_ISO.exists():
        os.chmod(TEST_ISO, 0o666)
    shutil.copyfile(VANILLA, TEST_ISO)
    os.chmod(TEST_ISO, 0o666)


def newest_shots(n):
    fs = sorted(glob.glob(str(SHOTS / "*.png")), key=os.path.getmtime)
    return fs[-n:]


def run_case(names, seconds, keep_first=0):
    print("\n" + "=" * 68)
    print("CASE: %s%s" % (", ".join(names) if names else "(vanilla control)",
                          "  keep_first=%d" % keep_first if keep_first else ""))
    print("=" * 68)
    sys.stdout.flush()
    restore()
    if names:
        r = subprocess.run(
            [sys.executable, str(HERE / "test_repack_all.py"),
             "--archives", ",".join(names), "--no-boot",
             "--keep-first", str(keep_first)],
            cwd=str(HERE.parent), capture_output=True, text=True)
        tail = [l for l in r.stdout.splitlines() if l.strip()][-3:]
        for l in tail:
            print("   " + l)
        if r.returncode:
            print("   repack/verify FAILED -- not playing this case")
            return None

    before = set(newest_shots(400))
    subprocess.run([sys.executable, str(HERE / "play_test.py"),
                    "--level", "--seconds", str(seconds),
                    "--iso", str(TEST_ISO)],
                   cwd=str(HERE.parent), capture_output=True, text=True)
    fresh = [f for f in newest_shots(20) if f not in before]
    late = fresh[-LATE:]
    if len(late) < LATE:
        print("   only %d new screenshots -- the run did not complete"
              % len(late))
        return None
    hs = [hashlib.sha256(open(f, "rb").read()).hexdigest()[:12] for f in late]
    for f, h in zip(late, hs):
        print("   %-28s %s" % (os.path.basename(f), h))
    n = len(set(hs))
    ok = n >= PASS_AT
    print("   distinct late frames: %d  ->  %s"
          % (n, "OK, the menu advances" if ok else "FROZEN"))
    sys.stdout.flush()
    return ok


def bisect_within(archive, seconds, hi_bound=0):
    """Binary-search the block whose movement breaks the game.

    keep_first=0 repacks everything (known to freeze SWAT.DAT); keep_first=N
    moves nothing. Somewhere between is the largest N that still plays, and
    the block at that index is the one to look at.

    Invariant checked rather than assumed: the two endpoints are run first. If
    they do not come out FROZEN and ok respectively, the search is meaningless
    and it says so instead of returning a number.
    """
    sys.path.insert(0, str(HERE.parent))
    from rdrstudio.game import Game               # noqa: E402
    g = Game(VANILLA, writable=False)
    total = len({e.offset for e in g.archive(archive).entries})
    g.close()
    top = hi_bound or total
    print("\n%s has %d data blocks; searching [0, %d)" % (archive, total, top))

    lo_full = run_case([archive], seconds, keep_first=0)
    if lo_full is not False:
        print("keep_first=0 did not freeze -- nothing to bisect")
        return None
    hi_none = run_case([archive], seconds, keep_first=top)
    if hi_none is not True:
        print("keep_first=%d ALSO fails. If that was the whole archive, the "
              "freeze is not about moving files at all and the bisect would be "
              "nonsense. If it was a sub-range, it just means another "
              "unmovable block sits below %d -- narrow further." % (top, top))
        return None

    lo, hi = 0, top            # lo freezes, hi plays
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if run_case([archive], seconds, keep_first=mid):
            hi = mid
        else:
            lo = mid
        print("   >>> bracket now [%d frozen .. %d ok]" % (lo, hi))
        sys.stdout.flush()
    # Be careful about what this number means. keep_first=K moves every block
    # with index >= K, so the run freezes exactly when SOME unmovable block
    # sits at index >= K. The search therefore converges on max(unmovable) --
    # the HIGHEST-indexed block that cannot be moved, not the first and not
    # the only one. There may be more below it, and finding them means
    # re-running the search on the range [0, lo).
    print("\nBOUNDARY: keeping %d blocks plays, keeping %d freezes.\n"
          "So block index %d cannot be moved. That is the HIGHEST such block;\n"
          "others may exist below it -- re-run the search over [0, %d) to find\n"
          "out, and only a clean run over that range means it is the only one."
          % (hi, lo, lo, lo))
    return lo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("archives", nargs="*")
    ap.add_argument("--each", action="store_true",
                    help="test every named archive on its own, one case each")
    ap.add_argument("--control", action="store_true",
                    help="also run a pristine disc, to check the rule itself")
    ap.add_argument("--within", default="",
                    help="binary-search WITHIN this archive for the block "
                         "whose movement breaks the game")
    ap.add_argument("--upto", type=int, default=0,
                    help="with --within, search only blocks [0, UPTO). Use "
                         "this to look for FURTHER unmovable blocks below one "
                         "already found -- the search returns the highest, "
                         "not the only one.")
    ap.add_argument("--seconds", type=float, default=185.0)
    args = ap.parse_args()

    if args.within:
        bisect_within(args.within, args.seconds, hi_bound=args.upto)
        return 0

    cases = ([[a] for a in args.archives] if args.each
             else [args.archives] if args.archives else [])
    if args.control:
        cases.insert(0, [])

    results = []
    for names in cases:
        results.append((names, run_case(names, args.seconds)))

    print("\n" + "=" * 68)
    print("SUMMARY")
    for names, ok in results:
        print("  %-46s %s" % (", ".join(names) or "(vanilla control)",
                              {True: "ok", False: "FROZEN",
                               None: "inconclusive"}[ok]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
