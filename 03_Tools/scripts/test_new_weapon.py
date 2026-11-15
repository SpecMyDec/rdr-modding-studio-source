"""Can the disc hold a 56th weapon, or only 55 with one of them rewritten?

The research answer first, because it decided what got built. Three addresses
in the unpacked executable (`py -3 rdr.py unpack-elf`, then `scripts/dis.py`):

    0x003813A8  WeaponIndexByName(name) -- for i in 0..*(int*)0x0079FD80-1,
                strcmp(*(char**)0x0079FD84 + i*1040, name); return i, else -1.
                The TypeName is the first field of each 1040-byte record.
    0x0039E4DC  the .ars arsenal loader calls exactly that function once per
                listed name. The 100 arsenal files hold NAMES; the indices
                only exist after load.
    0x003825E8  the rack is malloc(Num_GenericWeapon * 1040 + 16), read from
                the file's own header. No compiled-in ceiling of 55.

Lookup is BY NAME and the array is sized from the file, so a genuinely new
weapon is possible -- this is NOT the "clone over one you do not use" case.
The clone is APPENDED, so all 55 existing load-order indices are unmoved.

Three stages:

  1. OFFLINE, in memory: clone Old_Pistol, prove the 55 originals are
     byte-identical and unmoved, prove the clone is the source but for its
     TypeName, prove it is editable through the existing `guns.set_fields`,
     and cost every one of the 62 copies against its own slot. Then
     `arsenal_add` against all 100 `.ars` files.
  2. ON THE THROWAWAY DISC: run the real route (routes2.post_weapon_clone)
     dry, then for real, through a real Studio -- swat.guns x62, two arsenals
     and the shop row -- and read all of it back off the disc afterwards.
  3. BOOT, 185 s, judged by MOTION (3+ distinct late frames = plays).
     The intro runs ~100 s; anything shorter proves nothing.

Writes ONLY to 02_Modded/RDR_REPACKTEST.iso. The Studio's journal autosave is
redirected to a temp directory first, so 02_Modded/mods is untouched.

    python scripts/test_new_weapon.py --offline    stage 1 only (fast, safe)
    python scripts/test_new_weapon.py              all three
"""
import argparse
import glob
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rdrstudio.config import ROOT, VANILLA_ISO   # noqa: E402

VANILLA = Path(VANILLA_ISO)
TEST_ISO = Path(ROOT) / "02_Modded" / "RDR_REPACKTEST.iso"
SHOTS = Path(ROOT) / "07_Research" / "play_test"

NAME = "Peacekeeper"
SOURCE = "Old_Pistol"
ARSENALS = ["allweapons.ars", "yrd.ars"]


def offline():
    from rdrstudio import api, guns
    from rdrstudio.game import Game

    g = Game(VANILLA)
    raw = g.read("SWAT.DAT", "swat.guns")
    before = guns.parse(raw)
    new = guns.clone(raw, SOURCE, NAME)
    after = guns.parse(new)

    assert len(after) == len(before) + 1, "wrong weapon count"
    assert after[-1].name == NAME, "the clone is not last"
    for a, b in zip(before, after):
        assert a.name == b.name, "index %d moved: %s -> %s" % (a.index, a.name, b.name)
        assert raw[a.span[0]:a.span[1]] == new[b.span[0]:b.span[1]], \
            "%s changed" % a.name
    print("clone: %d -> %d weapons, all %d originals byte-identical and at the "
          "same index" % (len(before), len(after), len(before)))

    src = guns.find(raw, SOURCE)
    dst = guns.find(new, NAME)
    body_src = raw[src.span[0]:src.span[1]].replace(
        b"TypeName " + SOURCE.encode(), b"TypeName " + NAME.encode())
    assert body_src == new[dst.span[0]:dst.span[1]], "clone body differs"
    assert b"Num_GenericWeapon\t%d " % len(after) in new, "count not bumped"
    print("clone body identical to %s except TypeName; header now %d"
          % (SOURCE, len(after)))

    # The whole point of a real entry: the shipped editor can address it.
    edited = guns.set_fields(new, NAME, {"Damage": 9.5, "FullLoad": 12})
    w = guns.find(edited, NAME)
    assert w.fields["Damage"]["value"] == 9.5 and w.fields["FullLoad"]["value"] == 12
    assert (guns.find(edited, SOURCE).fields["Damage"]["value"]
            == guns.find(raw, SOURCE).fields["Damage"]["value"]), "source drifted"
    print("the new weapon is editable by name through guns.set_fields, and "
          "%s is untouched" % SOURCE)

    arcs = g.locate("swat.guns")
    worst = None
    for a in arcs:
        e = g.archive(a).find("swat.guns")
        stored = api.stored_size(e, new)
        assert stored <= e.slot, "%s: %d > %d" % (a, stored, e.slot)
        if worst is None or e.slot - stored < worst[1]:
            worst = (a, e.slot - stored, stored, e.slot)
    print("slot: %d copies, worst is %s at %d of %d with %d bytes spare"
          % (len(arcs), worst[0], worst[2], worst[3], worst[1]))

    d = g.archive("SWAT.DAT")
    fits = bad = 0
    for e in d:
        if e.ext != "ars":
            continue
        a0 = d.read(e)
        a1 = guns.arsenal_add(a0, NAME)
        rows = guns.arsenal(a1)
        n0 = len(guns.arsenal(a0))
        if (len(rows) != n0 + 1 or rows[-1]["name"] != NAME
                or int(a1.split()[1]) != len(rows)
                or guns.arsenal_add(a1, NAME) != a1):
            bad += 1
            print("  BAD %s" % e.name)
        if api.stored_size(e, a1) <= e.slot:
            fits += 1
    assert not bad, "%d arsenals mis-written" % bad
    print("arsenals: 100 parse and re-serialise correctly, %d have room for "
          "'%s' (%d chars)" % (fits, NAME, len(NAME)))
    g.close()
    return True


def _studio():
    """A real Studio on the throwaway disc, with its autosave sent elsewhere.

    `Studio.open` writes `_session.rdrmod.json` into cfg.MODS, which lives in
    02_Modded. That directory is read-only for this work, so the constant is
    repointed at a temp dir before the object exists.
    """
    from rdrstudio import config as cfg
    from rdrstudio.studio import Studio

    cfg.MODS = tempfile.mkdtemp(prefix="rdr_newgun_")
    return Studio(str(VANILLA), str(TEST_ISO))


def write_it():
    from rdrstudio import guns, routes2

    print("restoring a pristine test disc ...")
    if TEST_ISO.exists():
        os.chmod(TEST_ISO, 0o666)
    shutil.copyfile(VANILLA, TEST_ISO)
    os.chmod(TEST_ISO, 0o666)

    s = _studio()
    body = dict(source=SOURCE, name=NAME, arsenals=ARSENALS, shop=True)

    dry = routes2.post_weapon_clone(None, s, dict(body, dry=True))
    assert dry["ok"], dry
    plan = dry["plan"]
    print("dry run: %d writes, fits=%s, %d undo step"
          % (len(plan["writes"]), plan["fits"], plan["undo"]["steps"]))
    for w in plan["writes"]:
        if w["file"] != "swat.guns":
            print("   %-24s %-18s %d of %d, %d spare"
                  % (w["archive"], w["file"], w["stored"], w["slot"], w["headroom"]))
    g = [w for w in plan["writes"] if w["file"] == "swat.guns"]
    print("   swat.guns x%d          %d -> %d bytes, %d of %d stored, %d spare"
          % (len(g), g[0]["bytes_before"], g[0]["bytes_after"],
             g[0]["stored"], g[0]["slot"], g[0]["headroom"]))

    res = routes2.post_weapon_clone(None, s, dict(body, dry=False))
    assert res["ok"] and res["applied"], res
    print("applied as journal group %s" % res["group"])
    s.close()

    # -- read it all back off the disc that will boot, through the normal path
    from rdrstudio.game import Game
    g2 = Game(TEST_ISO)
    misses = 0
    for arc in g2.locate("swat.guns"):
        ws = guns.parse(g2.read(arc, "swat.guns"))
        if len(ws) != 56 or ws[-1].name != NAME:
            misses += 1
    print("read-back: %d of %d copies of swat.guns hold 56 weapons ending in %s"
          % (len(g2.locate("swat.guns")) - misses, len(g2.locate("swat.guns")), NAME))
    assert not misses
    for a in ARSENALS:
        names = [r["name"] for r in guns.arsenal(g2.read("SWAT.DAT", a))]
        assert NAME in names, a
        print("read-back: %-16s %s" % (a, ", ".join(names)))
    shop = g2.read("SWAT.DAT", "invmasterlist.csv")
    assert (NAME + ",weapon").encode() in shop
    print("read-back: invmasterlist.csv sells %s" % NAME)

    # And the clone is addressable by name through the shipped weapon editor.
    probe = guns.set_fields(g2.read("SWAT.DAT", "swat.guns"), NAME, {"Damage": 9.5})
    assert guns.find(probe, NAME).fields["Damage"]["value"] == 9.5
    print("read-back: the new weapon resolves by name in guns.set_fields")
    g2.close()
    return True


def boot(seconds):
    print("\nplaying the disc (%.0f s) ..." % seconds)
    before = set(glob.glob(str(SHOTS / "*.png")))
    subprocess.run([sys.executable, str(HERE / "play_test.py"),
                    "--watch", "12", "--seconds", str(seconds),
                    "--iso", str(TEST_ISO)],
                   cwd=str(HERE.parent), capture_output=True, text=True)
    fresh = [f for f in sorted(glob.glob(str(SHOTS / "*.png")),
                               key=os.path.getmtime) if f not in before][-6:]
    if len(fresh) < 6:
        print("only %d new frames -- run did not complete" % len(fresh))
        return False
    hashes = [hashlib.sha256(open(f, "rb").read()).hexdigest()[:12] for f in fresh]
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

    if not offline():
        return 1
    if args.offline:
        return 0
    if not write_it():
        return 1
    if boot(args.seconds):
        print("\nPLAYS with 56 weapons in the rack.")
        print("Still not proven: that you can HOLD it -- that needs a human to "
              "start a mission and open the weapon wheel.")
        return 0
    print("\nFROZEN or incomplete. Do NOT ship the new-weapon route.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
