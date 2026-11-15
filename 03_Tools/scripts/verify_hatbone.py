"""[M5] The hat goes where the character wears it, or nowhere.

The tester: *"Animation player automatically places hats on characters their
`head` bone, some characters wear their hats to their `hat01` bone (their
back), and some don't use their hats. Option to toggle Hat Bone and toggle
show/hide hat would be nice."*

All three are true, and the disc answers all three itself. Every character's
`.swt` carries `Hat` (0/1), `HatBone` and `HatOffset`, so the control is an
OVERRIDE on a default that is finally right rather than a knob on a guess.

What is asserted here, all of it against the real bundles on the working ISO:

  1. THE DISC'S ANSWER IS USABLE. Every `HatBone` named by a character whose
     skeleton is on the disc exists in that character's own rig. If that ever
     stops being true the default has to grow a fallback, so it is measured
     rather than believed -- and the four bones in use are named, because
     "head or hat01" is not the whole set.
  2. THE TRANSFORM FOLLOWS. For every character whose hat does NOT belong on
     `head`, the merged mesh is built both ways and the hat's vertices must
     move by exactly the vector between the two bones -- while the body and
     the face must not move at all. That last half is the point: the old code
     hung the face and the hat on one bone, so moving the hat by moving
     `head` would have dragged the face with it.
  3. VISIBILITY FOLLOWS. A character whose `.swt` says `Hat: 0` and whose
     bundle nonetheless carries a hat mesh -- there are five -- must come
     back with exactly the hat's vertices missing and the hat absent from
     `parts`, and the show override must bring it back.
  4. THE OVERRIDE WINS, both ways, including over the disc. That is what
     makes it a control rather than a display.
  5. THE DEFAULT HAS NOT DRIFTED. `hatbone.default_bone_index` is a second
     copy of `studio.head_bone_index`; they must agree on every character rig
     on the disc, or a hat lands in one place in the still preview and
     another in the animated one.
  6. A BONE THAT IS NOT THERE IS REPORTED, not quietly swapped for another.

Nothing here writes to the disc.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdrstudio.game import Game                       # noqa: E402
from rdrstudio import config                          # noqa: E402
from rdrstudio import hatbone as HB                   # noqa: E402
from rdrstudio import skeleton as SK                  # noqa: E402
from rdrstudio import studio as ST                    # noqa: E402
from rdrstudio.rsc import Rsc                         # noqa: E402

# Derived, not a literal from the machine that wrote this -- config.py is
# emphatic about why, and a hardcoded `...` here made this
# script unrunnable anywhere else. Opened read-only either way.
#
# Vanilla rather than the working ISO. What this checks is structural -- which
# bone a character's `.rsc` names as its hat mount -- so it should be read off
# the stock disc and not off whatever the last live test left behind. It also
# makes the script safe to run while someone else is editing the working copy.
DISC = Path(config.VANILLA_ISO)
ARC = "SWAT.DAT"


def dist(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


class Character:
    """One bundle, with everything the viewer would load for it."""

    __slots__ = ("stem", "spec", "bones", "world", "bundle", "picks", "dave")


def load(game, dave, stem):
    """-> Character, or None when this bundle cannot be posed at all."""
    try:
        bundle = Rsc(dave.read(stem + "_lods.rsc"), stem + "_lods.rsc")
    except Exception:
        return None
    try:
        _v, _d, bones, _r = SK.parse(dave.read(stem + ".skel"))
    except Exception:
        return None
    if not bones:
        return None
    picks = ST.character_parts(bundle, dave, stem, 0)
    if not picks:
        return None
    c = Character()
    c.stem = stem
    c.dave = dave
    c.bundle = bundle
    c.bones = bones
    c.world = SK.world_positions(bones)
    c.picks = list(picks)
    c.spec = HB.spec_for(dave, stem)
    return c


def main():
    game = Game(DISC, writable=False)
    ok = True

    def check(what, got, want, cmp="=="):
        nonlocal ok
        good = (got == want if cmp == "==" else
                got >= want if cmp == ">=" else got <= want)
        ok &= good
        print("  %-56s %-12s %s"
              % (what, str(got)[:12],
                 "ok" if good else "FAIL (want %s %s)" % (cmp, want)))

    dave = game.archive(ARC)
    stems = sorted({e.name[:-9] for e in game.toc(ARC)
                    if e.name.endswith("_lods.rsc")})
    print("%s: %d character bundles" % (ARC, len(stems)))

    # ------------------------------------------------------------------ 1
    # Every bundle that has a hat mesh, classified by what the disc says.
    print("\nwhat the disc says about the hat:")
    chars = {}
    with_hat = []
    named = {}
    bone_ok = bone_missing = 0
    drift = 0
    for stem in stems:
        c = load(game, dave, stem)
        if c is None:
            continue
        chars[stem] = c
        # 5 -- the two copies of the default rule must agree.
        if (HB.default_bone_index(c.bones, c.world)
                == ST.head_bone_index(c.bones, c.world)):
            drift += 0
        else:
            drift += 1
        if c.spec.bone:
            named.setdefault(c.spec.bone, []).append(stem)
            if HB._find(c.bones, c.spec.bone) is not None:
                bone_ok += 1
            else:
                bone_missing += 1
        if any(HB.is_hat(n) for _i, n in c.picks):
            with_hat.append(stem)

    print("  bundles that load with a rig : %d" % len(chars))
    print("  bundles carrying a hat mesh  : %d" % len(with_hat))
    for b in sorted(named, key=lambda k: -len(named[k])):
        print("    HatBone %-10s %4d   %s"
              % (b, len(named[b]), ", ".join(sorted(named[b])[:4])))
    check("characters load with a rig", len(chars) >= 40, True, ">=")
    check("bundles with a hat mesh", len(with_hat) >= 20, True, ">=")
    check("the disc names more than head/hat01", len(named) >= 3, True, ">=")
    check("every named HatBone exists in that rig", bone_missing, 0)
    check("  ...and there were some to check", bone_ok >= 20, True, ">=")
    # 5
    check("the default rule has not drifted from studio's", drift, 0)

    # What is wrong TODAY, which is what this milestone is for.
    wrong_bone = [s for s in with_hat
                  if chars[s].spec.bone and chars[s].spec.bone.lower()
                  != HB.DEFAULT_BONE]
    wrong_vis = [s for s in with_hat if chars[s].spec.wears is False]
    print("\n  hats the viewer places wrongly today:")
    print("    on the wrong bone : %d  %s" % (len(wrong_bone), wrong_bone))
    print("    drawn at all      : %d  %s" % (len(wrong_vis), wrong_vis))
    check("there are wrong-bone characters to fix", len(wrong_bone) >= 1,
          True, ">=")
    check("there are no-hat characters to fix", len(wrong_vis) >= 1,
          True, ">=")

    # ------------------------------------------------------------------ 2
    # The rendered transform follows the mount.
    print("\nthe hat moves, and nothing else does:")
    moved = face_still = body_still = 0
    for stem in wrong_bone:
        c = chars[stem]
        bworld = [c.world[b.name] for b in c.bones]
        # show=True on BOTH sides. `twg` is on this list and the no-hat list
        # at once -- the disc puts its hat on spine_02 and also says it does
        # not wear one -- so without pinning visibility the two builds would
        # differ by the hat's existence and this section would be measuring
        # the wrong thing. Visibility gets its own section below.
        old = HB.mounts(c.picks, c.bones, c.world, None, show=True)
        new = HB.mounts(c.picks, c.bones, c.world, c.spec, show=True)
        a = HB.merge_with_mounts(c.bundle, c.picks, bworld, old)
        b = HB.merge_with_mounts(c.bundle, c.picks, bworld, new)
        if not a or not b or len(a["verts"]) != len(b["verts"]):
            check("  %s builds both ways" % stem, False, True)
            continue
        hat = next(m for m in new.values() if m.kind == "hat")
        want = dist(hat.position, old[hat.part].position)
        # Walk the parts in order and slice the vertex ranges, so "the hat
        # moved" is asserted about the hat's own vertices rather than about
        # some vertex somewhere.
        at = 0
        hat_delta = other_delta = 0.0
        for p in a["parts"]:
            n = p["verts"]
            d = max((dist(a["verts"][i], b["verts"][i])
                     for i in range(at, at + n)), default=0.0)
            if HB.is_hat(p["name"]):
                hat_delta = max(hat_delta, d)
            else:
                other_delta = max(other_delta, d)
            at += n
        print("    %-8s %-9s -> %-9s hat moved %.3f (bones %.3f apart), "
              "rest moved %.4f"
              % (stem, old[hat.part].bone, hat.bone, hat_delta, want,
                 other_delta))
        moved += abs(hat_delta - want) < 1e-4 and want > 1e-3
        face_still += other_delta < 1e-6
        body_still += other_delta < 1e-6
    check("the hat's vertices move by the bone-to-bone distance",
          moved, len(wrong_bone))
    check("the face and body do not move at all", face_still, len(wrong_bone))

    # ------------------------------------------------------------------ 3
    # Visibility.
    print("\nthe hat disappears when the character wears none:")
    hidden = restored = 0
    for stem in wrong_vis:
        c = chars[stem]
        bworld = [c.world[b.name] for b in c.bones]
        shown = HB.merge_with_mounts(
            c.bundle, c.picks, bworld,
            HB.mounts(c.picks, c.bones, c.world, c.spec, show=True))
        asis = HB.merge_with_mounts(
            c.bundle, c.picks, bworld,
            HB.mounts(c.picks, c.bones, c.world, c.spec))
        hat_part = next(p for p in shown["parts"] if HB.is_hat(p["name"]))
        gone = (len(asis["verts"]) == len(shown["verts"]) - hat_part["verts"]
                and not any(HB.is_hat(p["name"]) for p in asis["parts"])
                and len(asis["tris"]) == len(shown["tris"]) - hat_part["tris"])
        print("    %-8s .swt says Hat: 0 -> %d verts, was %d (hat is %d)"
              % (stem, len(asis["verts"]), len(shown["verts"]),
                 hat_part["verts"]))
        hidden += gone
        restored += any(HB.is_hat(p["name"]) for p in shown["parts"])
    check("the hat's vertices and triangles are gone", hidden, len(wrong_vis))
    check("and the show override brings it back", restored, len(wrong_vis))

    # ------------------------------------------------------------------ 4
    # The override wins, over the default AND over the disc.
    print("\nthe override wins:")
    subject = wrong_bone[0]
    c = chars[subject]
    bworld = [c.world[b.name] for b in c.bones]
    forced = HB.mounts(c.picks, c.bones, c.world, c.spec,
                       bone=HB.DEFAULT_BONE)
    hat = next(m for m in forced.values() if m.kind == "hat")
    check("  %s: forcing head beats the .swt's %s"
          % (subject, c.spec.bone), hat.bone.lower(), HB.DEFAULT_BONE)

    # ...and the other direction, on a character the disc puts on `head`.
    plain = next((s for s in with_hat
                  if chars[s].spec.bone
                  and chars[s].spec.bone.lower() == HB.DEFAULT_BONE
                  and len(chars[s].bones) > 2), None)
    check("  found a head-mounted character to move", plain is not None, True)
    if plain:
        c2 = chars[plain]
        bw2 = [c2.world[b.name] for b in c2.bones]
        head_i = HB.default_bone_index(c2.bones, c2.world)
        # Any bone that is not the head and is somewhere else in space.
        other = next((b.name for i, b in enumerate(c2.bones)
                      if i != head_i
                      and dist(c2.world[b.name],
                               c2.world[c2.bones[head_i].name]) > 0.05), None)
        m0 = HB.mounts(c2.picks, c2.bones, c2.world, c2.spec)
        m1 = HB.mounts(c2.picks, c2.bones, c2.world, c2.spec, bone=other)
        g0 = HB.merge_with_mounts(c2.bundle, c2.picks, bw2, m0)
        g1 = HB.merge_with_mounts(c2.bundle, c2.picks, bw2, m1)
        h = next(m for m in m1.values() if m.kind == "hat")
        at = 0
        seen = 0.0
        for p in g0["parts"]:
            n = p["verts"]
            if HB.is_hat(p["name"]):
                seen = max(dist(g0["verts"][i], g1["verts"][i])
                           for i in range(at, at + n))
            at += n
        want = dist(h.position, m0[h.part].position)
        print("    %-8s head -> %-10s hat moved %.3f (bones %.3f apart)"
              % (plain, other, seen, want))
        check("  the forced bone moves the hat there", abs(seen - want) < 1e-4,
              True)

    # ------------------------------------------------------------------ 6
    # A bone that is not in the rig is reported, not substituted.
    print("\nan override naming a bone the rig does not have:")
    i, nm, why = HB.resolve_bone(c.bones, c.world, c.spec,
                                 override="not_a_bone_on_this_rig")
    check("  no index is invented", i is None, True)
    check("  the name asked for is echoed", nm, "not_a_bone_on_this_rig")
    check("  and it says why", "no bone called" in why, True)

    # A .swt naming a bone THIS rig lacks falls back, and says so -- the rig
    # is a parameter in this app, so the pairing is not guaranteed even
    # though every shipped pairing checks out.
    thin = HB.HatSpec(wears=True, bone="no_such_bone", source="swt")
    i2, nm2, why2 = HB.resolve_bone(c.bones, c.world, thin)
    check("  a .swt bone missing from the rig falls back", i2 is not None, True)
    check("  and names both bones in the reason",
          "no_such_bone" in why2 and nm2 is not None, True)

    print("\n%s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
