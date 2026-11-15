#!/usr/bin/env python3
"""Does the game DRAW a custom level model? Boot it and look.

    python3 scripts/test_custom_model_boot.py            patch + boot + shots
    python3 scripts/test_custom_model_boot.py --control  boot the unpatched disc
    python3 scripts/test_custom_model_boot.py --no-boot  patch and verify only

The offline test (test_levelvif.py) proves the bytes are the shipped grammar
and read back; it cannot say whether the PS2's VU1 program accepts them. This
can, by eye: it replaces the first mission's (lvl_perfnew) biggest rigid model
with an OBJ that is impossible to miss -- a hollow, high-contrast box around
the player's spawn point, vertex count unlike the original -- then loads the
banked save state at the mission menu, starts the mission and photographs it.

JUDGED BY EYE AS WELL AS BY MOTION. A frame that is only "not black" proves
nothing, so `--control` takes the same shots on the unpatched disc and the
reader (a human, or a model looking at the PNGs) compares them.

Writes ONLY to 02_Modded/RDR_CUSTOMMODEL.iso, restored from vanilla first.
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

from rdrstudio import config as cfg                     # noqa: E402
from rdrstudio import levelvif                          # noqa: E402
from rdrstudio.config import ROOT, VANILLA_ISO         # noqa: E402

TEST_ISO = Path(ROOT) / "02_Modded" / "RDR_CUSTOMMODEL.iso"
SHOTS = Path(ROOT) / "07_Research" / "play_test"
ARCHIVE_GUESS = ("LVLS_SNG/PERFNEW.DAT", "LVLS_M1/PERFNEW.DAT")


def box_obj(c, half, inside=True, outside=True, lo=None):
    """A box around `c` with a checker-ish UV so it is unmistakable."""
    x, y, z = c
    h = half
    P = [(x - h, y - h, z - h), (x + h, y - h, z - h), (x + h, y + h, z - h),
         (x - h, y + h, z - h), (x - h, y - h, z + h), (x + h, y - h, z + h),
         (x + h, y + h, z + h), (x - h, y + h, z + h)]
    F = [(0, 1, 2, 3), (5, 4, 7, 6), (4, 0, 3, 7), (1, 5, 6, 2),
         (3, 2, 6, 7), (4, 5, 1, 0)]
    out = ["o custombox"]
    for p in P:
        out.append("v %.5f %.5f %.5f" % p)
    out += ["vt 0 0", "vt 4 0", "vt 4 4", "vt 0 4"]
    for f in F:
        a, b, c_, d = [i + 1 for i in f]
        if outside:
            out.append("f %d/1 %d/2 %d/3" % (a, b, c_))
            out.append("f %d/1 %d/3 %d/4" % (a, c_, d))
        if inside:
            out.append("f %d/1 %d/3 %d/2" % (a, c_, b))
            out.append("f %d/1 %d/4 %d/3" % (a, d, c_))
    return "\n".join(out) + "\n"


def late_frames(before):
    fs = sorted(glob.glob(str(SHOTS / "*.png")), key=os.path.getmtime)
    return [f for f in fs if f not in before]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-boot", action="store_true")
    ap.add_argument("--control", action="store_true")
    ap.add_argument("--chunks", default="",
                    help="comma list of MOD0 chunks to replace (default: the "
                         "biggest rigid one)")
    ap.add_argument("--all-rigid", action="store_true",
                    help="replace EVERY rigid MOD0 chunk with the box: if the "
                         "level still looks vanilla, nothing written is drawn")
    ap.add_argument("--all-levels", action="store_true",
                    help="do --all-rigid in EVERY level archive that fits, at "
                         "its own spawn, so whichever mission the menu flow "
                         "lands in shows the box (the 11/5 boot tests loaded "
                         "a different mission than PERFNEW)")
    ap.add_argument("--half", type=float, default=6.0)
    ap.add_argument("--seconds", type=float, default=330.0)
    args = ap.parse_args()

    print("restoring a pristine test disc ...")
    if TEST_ISO.exists():
        os.chmod(TEST_ISO, 0o666)
    shutil.copyfile(VANILLA_ISO, TEST_ISO)
    os.chmod(TEST_ISO, 0o666)

    from rdrstudio.game import Game
    from rdrstudio.level import Level

    game = Game(TEST_ISO, writable=True)
    arc = next((a for a in ARCHIVE_GUESS
                if a.split("/")[1] in {n.upper() for n in game.archives()}
                or a in game.archives()), None)
    if arc is None:
        cands = [a for a in game.archives() if "PERFNEW" in a.upper()]
        arc = cands[0] if cands else None
    if args.all_levels:
        done = []
        for a in game.archives():
            if "LVLS" not in a.upper():
                continue
            try:
                lv = Level(game, a)
                sps = [n for n in lv.nodes if n.kind == "spawn"]
                if not sps:
                    print("  %s: no spawn, skipped" % a)
                    continue
                spp = [float(v.value) for v in sps[0].pos]
                rs = lv._load_rsc()
                ids = [c.index for c in rs.chunks if c.tag == "MOD0"
                       and not rs.raw(c.index)[1] and rs.parts(c.index)]
                out = game.archive(a).read(lv.rsc_name)
                ob = box_obj(spp, args.half)
                for ci in ids:
                    out, _ = levelvif.import_obj(out, ci, ob, label=lv.rsc_name)
                fit = game.archive(a).would_fit(lv.rsc_name, out)
                if not fit["fits"]:
                    print("  %s: %d chunks, does NOT fit" % (a, len(ids)))
                    continue
                game.archive(a).patch(lv.rsc_name, out)
                done.append(a)
                print("  %s: %d chunks -> box at %s  OK" % (
                    a, len(ids), [round(v, 1) for v in spp]))
            except Exception as e:                     # noqa: BLE001
                print("  %s: skipped (%s: %s)" % (a, type(e).__name__, e))
        game.close()
        print("patched %d level archives" % len(done))
        return 0 if done else 1
    print("  archive:", arc)
    lvl = Level(game, arc)
    spawns = [n for n in lvl.nodes if n.kind == "spawn"]
    sp = [float(v.value) for v in spawns[0].pos]
    print("  player spawn:", [round(v, 2) for v in sp])
    rsc = lvl._load_rsc()
    # the biggest rigid container is the one whose slot has the most room
    best = None
    for c in rsc.chunks:
        if c.tag == "MOD0" and not rsc.raw(c.index)[1] and rsc.parts(c.index):
            if best is None or c.size > best.size:
                best = c
    print("  replacing chunk %d (%d bytes, %d parts)"
          % (best.index, best.size, len(rsc.parts(best.index))))
    cur = game.archive(arc).read(lvl.rsc_name)
    obj = box_obj(sp, args.half)
    if not args.control:
        want = [int(x) for x in args.chunks.split(",") if x] or [best.index]
        if args.all_rigid:
            want = [c.index for c in rsc.chunks if c.tag == "MOD0"
                    and not rsc.raw(c.index)[1] and rsc.parts(c.index)]
            print("  --all-rigid: %d chunks" % len(want))
        out = cur
        for ci in want:
            out, rep = levelvif.import_obj(out, ci, obj, label=lvl.rsc_name)
            print("  chunk %d: %s" % (ci, {k: rep[k] for k in (
                "vertices", "triangles", "parts", "old_bytes", "new_bytes")}))
        fit = game.archive(arc).would_fit(lvl.rsc_name, out)
        print("  fits:", fit["fits"], "stored", fit["stored"], "slot", fit["slot"])
        if not fit["fits"]:
            print("  does not fit -- nothing to boot")
            game.close()
            return 1
        game.archive(arc).patch(lvl.rsc_name, out)
    game.close()

    if args.no_boot:
        return 0

    print("\nplaying the disc ...")
    before = set(glob.glob(str(SHOTS / "*.png")))
    subprocess.run([sys.executable, str(HERE / "play_test.py"),
                    "--level", "--skip-intro", "--seconds", str(args.seconds),
                    "--iso", str(TEST_ISO)],
                   cwd=str(HERE.parent), capture_output=True, text=True)
    fs = late_frames(before)
    print("%d screenshots" % len(fs))
    for f in fs:
        print("  ", os.path.basename(f),
              hashlib.sha256(open(f, "rb").read()).hexdigest()[:12])
    return 0 if fs else 1


if __name__ == "__main__":
    sys.exit(main())
