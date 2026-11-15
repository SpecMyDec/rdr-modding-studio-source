"""Do the event and layout parsers agree with the raw files?

Checks against numbers read out of the files by hand, so a regression in
levelmeta.py shows up as a mismatch rather than a plausible-looking answer.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdrstudio.game import Game                      # noqa: E402
from rdrstudio.level import Level                    # noqa: E402
from rdrstudio import levelmeta as lm                # noqa: E402
from rdrstudio import config                         # noqa: E402

# The PRISTINE disc. This is a read-only structural check, so it must not
# baseline on the working copy -- that makes its result depend on whatever
# the last write left behind. Was a hardcoded `D:\...\RDR_MODDED.iso`,
# which also meant this only ran under Windows python. REQ-035.
DISC = Path(config.VANILLA_ISO)


def main():
    game = Game(DISC, writable=False)
    ok = True

    def check(what, got, want):
        nonlocal ok
        good = got == want
        ok &= good
        print("  %-46s %-22s %s"
              % (what, repr(got)[:22], "ok" if good else "EXPECTED %r" % (want,)))

    print("\nTOMB -- area transitions (.sub)")
    tr = lm.transitions(game, "LVLS_SNG/TOMB.DAT")
    check("sub-levels found", len(tr), 6)
    first = tr[0]
    check("first loads", first["level"], "lvl_cloShop")
    check("shown to the player as", first["display"], "Tailor's Shop")
    check("master level name", first["master"], "Tombstone")
    check("door model", first["door_model"], "drclo_lod_01")
    check("door animation", first["door_anim"], "drclo_open")
    check("entry position", [round(v, 3) for v in first["enter"]],
          [-0.4, 0.0, 7.4])
    named = [t["display"] for t in tr]
    print("     all six: %s" % ", ".join(named))

    print("\nTRAIN -- cutscene cameras (.cam)")
    cams = lm.cameras(game, "LVLS_SNG/TRAIN.DAT")
    tgs = [c for c in cams if c["cutscene"]]
    check("shots in duelview_lvl_train.cam + swatcameras", len(cams) >= 11, True)
    # 10, not 9: the probe's output was truncated mid-list when I first counted.
    check("TGS_ cutscene shots", len(tgs), 10)
    p1 = next(c for c in cams if c["name"] == "TGS_train_puppet1")
    check("puppet1 eye x", round(p1["eye"][0], 3), 4.202)
    check("puppet1 lens", round(p1["lens"], 2), 0.7)
    check("PrincessCam flagged parked",
          next(c for c in cams if c["name"] == "PrincessCam")["parked"], True)

    print("\nTRAIN -- sun direction (.shadows)")
    s = lm.sun(game, "LVLS_SNG/TRAIN.DAT")
    check("kind", s["kind"], "directional")
    check("DirLight", [round(v, 3) for v in s["dir"]], [0.689, -0.543, 0.48])
    print("     (interiors differ: lvl_bar.shadows is SimpleShadows, no sun)")
    s2 = lm.sun(game, "LVLS_SNG/TOMB.DAT")
    check("TOMB shadows kind", s2["kind"] in ("simple", "directional"), True)

    print("\nTOMB -- glows (.glows)")
    # TOMB.DAT holds five .glows, one per interior plus the town, so the
    # archive-wide count is 12. My first expectation of 1 was lvl_tomb.glows
    # alone -- the parser was right and the test was reading the wrong scope.
    g = lm.glows(game, "LVLS_SNG/TOMB.DAT")
    check("glows across all 5 .glows files", len(g), 12)
    town = [x for x in g if x["source"] == "lvl_tomb.glows"]
    check("in lvl_tomb.glows itself", len(town), 1)
    check("texture", town[0]["texture"], "sun01")
    check("scale", round(town[0]["scale"], 1), 145.8)
    check("position", [round(v, 1) for v in town[0]["pos"]], [-29.9, 27.5, 37.3])

    print("\nTRAIN -- level flags (.flags)")
    fl = lm.flags(game, "LVLS_SNG/TRAIN.DAT")
    names = {f["name"] for f in fl}
    check("flags parsed", len(fl) >= 40, True)
    check("Train_Started present", "Train_Started" in names, True)
    check("TGS_Bandit_Start_Puppet present",
          "TGS_Bandit_Start_Puppet" in names, True)
    check("every flag has no position",
          all("pos" not in f for f in fl), True)

    print("\nTRAIN -- AI spawn points (.aisp)")
    sp = lm.spawn_points(game, "LVLS_SNG/TRAIN.DAT")
    pts = [p for grp in sp for p in grp["points"]]
    check("groups", len(sp) >= 20, True)
    check("points", len(pts) >= 100, True)
    kinds = {}
    for p in pts:
        kinds[p["kind"]] = kinds.get(p["kind"], 0) + 1
    print("     owners: %s" % kinds)
    win = next(g for g in sp if g["group"] == "Window_Spawn")
    check("Window_Spawn points", len(win["points"]), 4)
    check("owned by module", win["points"][0]["owner"], "lvl_train_armouryB")

    print("\nTRAIN -- consist (.trn)")
    c = lm.consist(game, "LVLS_SNG/TRAIN.DAT")
    check("trains", len(c), 2)
    check("train 1 cars (the handcar)", len(c[0]["cars"]), 1)
    check("train 2 cars", len(c[1]["cars"]), 9)
    check("train 2 leads with", c[1]["cars"][0]["name"], "lvl_train_steamerBad")
    check("armouryC resource (1-based)",
          next(x["resource"] for x in c[1]["cars"]
               if x["name"] == "lvl_train_armouryC"), 210)
    print("     train 2 is %.1f m long, cars: %s"
          % (c[1]["length"],
             " ".join(x["name"].split("_")[-1] for x in c[1]["cars"])))

    print("\nTRAIN -- terrain tiles (.til)")
    t = lm.tiles(game, "LVLS_SNG/TRAIN.DAT")
    check("tile files", len(t), 1)
    check("palette size", len(t[0]["palette"]), 14)
    check("sets", t[0]["sets"], 2)
    check("tiles per set", t[0]["per_set"], 8)
    check("random tail", t[0]["random_tail"], 2)
    check("sequence length", len(t[0]["sequence"]), 16)

    print("\nTRAIN -- speed (.znv)")
    v = lm.train_speed(game, "LVLS_SNG/TRAIN.DAT")
    check("velocity m/s", round(v["velocity"], 1), 7.0)

    print("\nTRAIN -- derived layout")
    lvl = Level(game, "LVLS_SNG/TRAIN.DAT")
    fr = lm.frames(game, "LVLS_SNG/TRAIN.DAT", lvl)
    tilef = [f for f in fr if f["kind"] == "tile"]
    carf = [f for f in fr if f["kind"] == "train_car"]
    check("tile frames", len(tilef), 16)
    check("car frames", len(carf), 10)
    span = max((f["offset"][0] + f["length"]) for f in tilef) if tilef else 0
    print("     track laid out to %.1f m; train is %.1f m"
          % (span, max((f["offset"][0] for f in carf), default=0)))
    check("track is longer than one tile", span > 100, True)
    # 16 slots: 14 ordinary tiles at 60.00 m plus the seam tile trn50 at
    # 120.00 m, and trn50 appears in both sets. 14*60 + 2*120 = 1,080.
    check("track length", round(span), 1080)
    rr = [f for f in tilef if f["runtime_random"]]
    check("slots inside the Random tail", len(rr), 4)
    seam = [f for f in tilef if abs(f["z_shift"]) > 1]
    check("seam tiles that move the track sideways", len(seam), 2)
    print("     seam: %s, %.1f m, track steps z by %+.2f m"
          % (seam[0]["module"], seam[0]["length"], seam[0]["z_shift"]))

    print("\nTRAINGOO -- the canyon that opens into desert")
    lvl3 = Level(game, "LVLS_SNG/TRAINGOO.DAT")
    fr3 = lm.frames(game, "LVLS_SNG/TRAINGOO.DAT", lvl3)
    t3 = [f for f in fr3 if f["kind"] == "tile"]
    span3 = max((f["offset"][0] + f["length"]) for f in t3) if t3 else 0
    check("tile slots", len(t3), 24)
    print("     laid out to %.1f m across %d tile slots" % (span3, len(t3)))
    check("longer than TRAIN", span3 > span, True)

    print("\nTOMB -- a normal level has no module hierarchy")
    lvl2 = Level(game, "LVLS_SNG/TOMB.DAT")
    check("frames", len(lm.frames(game, "LVLS_SNG/TOMB.DAT", lvl2)), 0)

    print("\n%s" % ("ALL CHECKS PASSED" if ok else "SOMETHING FAILED"))
    game.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
