"""Does moving a building AND its collision shell together survive a load?

Tonight added linked moves: drag a building in the editor and the shell you
bump into goes with it. That is a new write path -- two nodes in (possibly)
two different files, edited in one apply -- and nothing offline can tell you
whether the level still loads afterwards. Only the game can.

    python scripts/test_linked_move_boot.py
    python scripts/test_linked_move_boot.py --no-boot    write and verify only

JUDGED BY MOTION, not by screenshot size, for the same reason
test_place_enemy_boot.py is: a broken disc here shows a perfectly good title
screen that has stopped updating. Six late frames, hashed; 3+ distinct means
the game is alive, 1 means it is not. Vanilla scores 3-5.

Writes ONLY to 02_Modded/RDR_REPACKTEST.iso, restored from vanilla first.
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

ARCHIVE = "LVLS_SNG/SHER70.DAT"
DELTA = (1.5, 0.0, -1.5)


def late_frames(before):
    fs = sorted(glob.glob(str(SHOTS / "*.png")), key=os.path.getmtime)
    fresh = [f for f in fs if f not in before]
    return fresh[-6:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-boot", action="store_true")
    ap.add_argument("--seconds", type=float, default=185.0)
    args = ap.parse_args()

    print("restoring a pristine test disc ...")
    if TEST_ISO.exists():
        os.chmod(TEST_ISO, 0o666)
    shutil.copyfile(VANILLA, TEST_ISO)
    os.chmod(TEST_ISO, 0o666)

    from rdrstudio.game import Game
    from rdrstudio.level import Level

    game = Game(TEST_ISO, writable=True)
    lvl = Level(game, ARCHIVE)

    link = lvl.links()
    pairs = [(a, b) for a, b in link.items()
             if lvl.by_uid(a).kind == "zone"]
    if not pairs:
        print("  %s has no linked building/collision pair" % ARCHIVE)
        return 1
    print("  %d linked pairs in this level" % len(pairs))

    zuid, buid = pairs[0]
    z, b = lvl.by_uid(zuid), lvl.by_uid(buid)
    zpos = [float(v.value) for v in z.pos]
    bpos = [float(v.value) for v in b.pos]
    print("  moving %r (%s) with %r (%s)"
          % (z.name, z.source, b.name, b.source))
    print("     building  at %s" % ([round(v, 3) for v in zpos],))
    print("     collision at %s" % ([round(v, 3) for v in bpos],))

    znew = [zpos[i] + DELTA[i] for i in range(3)]
    bnew = [bpos[i] + DELTA[i] for i in range(3)]
    edits = lvl.merge(lvl.move(zuid, znew), lvl.move(buid, bnew))
    print("  one apply touching %d file(s): %s"
          % (len(edits), ", ".join(sorted(edits))))

    report = lvl.preview(edits)
    rows = report.values() if isinstance(report, dict) else report
    fits = all(r.get("fits", True) for r in rows)
    print("  preview fits=%s" % fits)
    if not fits:
        for k, v in (report.items() if isinstance(report, dict) else []):
            print("     %-30s %s" % (k, v))
        print("  it does not fit -- nothing to boot")
        game.close()
        return 1

    res = lvl.apply(edits, dry=False)
    print("  applied: %s"
          % (res.get("applied") if isinstance(res, dict) else res))
    game.close()

    # Read both back off the disc that will actually be booted. A linked move
    # that only lands on one of the two is the exact failure this is for.
    g2 = Game(TEST_ISO, writable=False)
    l2 = Level(g2, ARCHIVE)
    z2, b2 = l2.by_uid(zuid), l2.by_uid(buid)
    g2.close()

    def near(got, want):
        return all(abs(float(got[i].value) - want[i]) < 0.01 for i in range(3))

    okz, okb = near(z2.pos, znew), near(b2.pos, bnew)
    print("  building  reads back %s -> %s"
          % ([round(float(v.value), 3) for v in z2.pos], okz))
    print("  collision reads back %s -> %s"
          % ([round(float(v.value), 3) for v in b2.pos], okb))
    if not (okz and okb):
        print("  the linked write did not land on both -- nothing to boot")
        return 1

    if args.no_boot:
        return 0

    print("\nplaying the disc ...")
    before = set(glob.glob(str(SHOTS / "*.png")))
    subprocess.run([sys.executable, str(HERE / "play_test.py"),
                    "--level", "--seconds", str(args.seconds),
                    "--iso", str(TEST_ISO)],
                   cwd=str(HERE.parent), capture_output=True, text=True)

    fs = late_frames(before)
    if len(fs) < 6:
        print("only %d new screenshots -- the run did not complete" % len(fs))
        return 1
    hs = []
    for f in fs:
        h = hashlib.sha256(open(f, "rb").read()).hexdigest()[:12]
        hs.append(h)
        print("   %-26s %s" % (os.path.basename(f), h))
    n = len(set(hs))
    print("\ndistinct late frames: %d" % n)
    if n >= 3:
        print("PLAYS. Moving a building with its collision does not stop the "
              "level loading.")
        return 0
    print("FROZEN. The linked move breaks the game -- do NOT ship it.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
