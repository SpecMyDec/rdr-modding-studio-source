"""Does a placed enemy actually survive the game loading the level?

`place_enemy` is the headline capability of the rework and `features.json`
marks it `untested`, honestly: "the plan fits and reads back; no boot test
past ~100s has run yet". It is nevertheless set to ship in the public build.

That combination is exactly the mistake this project already made once. The
repack work was called proven on the strength of a 70-second boot test, and
the opening cinematic runs to about 100 seconds -- so the test stopped before
the game had finished starting up, and every offline check passed on a disc
that froze. `scripts/bisect_repack.py` has the whole story.

So this writes a real enemy into a throwaway disc and plays it long enough to
mean something.

    python scripts/test_place_enemy_boot.py
    python scripts/test_place_enemy_boot.py --no-boot     write and verify only

JUDGED BY MOTION, not by screenshot size. A frozen disc here is not a black
screen; it is a perfectly good title screen that stops updating. Six frames
after the intro, hashed: 3+ distinct means the game is running, 1 means it
is dead. Vanilla scores 3-5.

Writes ONLY to 02_Modded/RDR_REPACKTEST.iso.
"""
import argparse
import hashlib
import glob
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
# Driven through the library, NOT the running server, on purpose. The dev
# server writes to 02_Modded/RDR_MODDED.iso -- the developer's working disc, with his
# edits on it. A test that boots a throwaway must also WRITE to the throwaway,
# or it proves nothing about the disc it played and quietly damages the one it
# did not.

# SHER70 is mission 3, the level already edited and booted before, and its
# roster is local rather than borrowed from SWAT.DAT -- so this exercises the
# ordinary path.
ARCHIVE = "LVLS_SNG/SHER70.DAT"


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
    from rdrstudio import cast as C

    game = Game(TEST_ISO, writable=True)
    lvl = Level(game, ARCHIVE)

    # Clone the shape of an enemy the level already has. place_enemy does this
    # deliberately -- the whitespace, field order and Spawn sub-block are what
    # the game's own exporter wrote, and generating them fresh is a way to be
    # subtly wrong.
    templates = lvl.enemy_nodes()
    if not templates:
        print("  %s has no enemy to clone -- cannot place" % ARCHIVE)
        return 1
    template = min(templates, key=lambda n: n.span[1] - n.span[0])
    target = template.source

    palette = C.cast(game, ARCHIVE)
    used = [c for c in palette["characters"] if c.get("used")]
    ch = (used or palette["characters"])[0]
    print("  cloning %r from %s" % (template.name, target))
    print("  new enemy is character %r (%d already in this level)"
          % (ch["id"], ch.get("used", 0)))

    edits, new_name, block_bytes = lvl.place_enemy(
        target, template, [24.4, 1.5, -15.8], 0.0,
        {"Type": ch["id"]}, "boottest_01")

    report = lvl.preview(edits)
    fits = all(r.get("fits", True) for r in
               (report.values() if isinstance(report, dict) else report))
    print("  block is %d bytes; preview fits=%s" % (block_bytes, fits))
    try:
        for k, v in (report.items() if isinstance(report, dict) else []):
            print("     %-30s %s" % (k, v))
    except Exception:
        print("     report: %r" % (report,))
    if not fits:
        print("  it does not fit -- nothing to boot")
        game.close()
        return 1

    res = lvl.apply(edits, dry=False)
    print("  applied: %s" % (res.get("applied") if isinstance(res, dict) else res))
    game.close()

    # Read it back off the disc that will actually be booted.
    g2 = Game(TEST_ISO, writable=False)
    data = g2.read(ARCHIVE, target)
    g2.close()
    ok = b"boottest_01" in data
    print("  reads back from the test disc: %s" % ok)
    if not ok:
        print("  the write did not land -- nothing to boot")
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
        print("PLAYS. A placed enemy does not stop the level loading.")
        print("features.json can move place_enemy from untested to measured.")
        return 0
    print("FROZEN. The placement breaks the game -- do NOT ship it.")
    return 1


if __name__ == "__main__":

    sys.exit(main())
