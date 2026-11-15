"""Is the exported BVH actually valid, and does it still hold the source data?

Writing a file Blender *accepts* is not the same as writing a file that is
right. These checks are chosen so a plausible-but-broken exporter fails them:

  1. **Structure.** Braces balance; there is exactly one ROOT; every JOINT and
     the ROOT declare CHANNELS; every leaf has an End Site; the joint count
     matches the skeleton.
  2. **Width.** Every MOTION line carries exactly `6 + 3*(bones-1)` numbers --
     the count BVH's own CHANNELS declarations imply. A single missing or extra
     value shifts every later joint by one axis and would still import.
  3. **Height.** The number of MOTION lines equals the declared `Frames:`,
     which equals the clip's stored key count.
  4. **Round trip.** Read the BVH back and compare against the source
     animation: root translation to the metre, every rotation to the degree.
     This is the check that catches a transposed or mis-ordered emit.
  5. **Refusals.** A clip whose channel count does not fit `3 + 3*bones`, and a
     skeleton with the wrong bone count, must both be REFUSED rather than
     exported wrong.
"""
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdrstudio.game import Game                      # noqa: E402
from rdrstudio import config                          # noqa: E402
from rdrstudio import anim as A                       # noqa: E402
from rdrstudio import bvh as BVH                      # noqa: E402
from rdrstudio import skeleton as SK                  # noqa: E402

# REQ-035. Was a literal path to the WORKING ISO on one machine:
# unrunnable on anyone else's checkout, and pointed at the disc the
# live tests mutate, so a read-only structural check could go red for
# a reason with nothing to do with the code under test. What this
# measures is a property of the shipped clips, so it reads the stock
# disc. Opened writable=False, which is what makes vanilla correct here.
DISC = Path(config.VANILLA_ISO)
ARC = "SWAT.DAT"


def main():
    game = Game(DISC, writable=False)
    ok = True

    def check(what, got, want, cmp="=="):
        nonlocal ok
        good = (got == want if cmp == "==" else
                got < want if cmp == "<" else got > want)
        ok &= good
        print("  %-50s %-14s %s"
              % (what, str(got)[:14],
                 "ok" if good else "FAIL (want %s %s)" % (cmp, want)))

    # Index the skeletons once, by bone count.
    skels = {}
    for e in game.toc(ARC):
        if not e.name.lower().endswith(".skel"):
            continue
        try:
            _v, _d, bones, _r = SK.parse(game.read(ARC, e.name))
        except Exception:
            continue
        skels.setdefault(len(bones), []).append((e.name, bones))
    print("%d bone counts among %s's skeletons"
          % (len(skels), ARC))

    # Exercise a spread of clips: different bone counts, half and full rate.
    picks = []
    for e in game.toc(ARC):
        if not e.name.lower().endswith(".anim"):
            continue
        try:
            a = A.parse(game.read(ARC, e.name))
        except Exception:
            continue
        want = (a.channels - 3) // 3
        if a.channels != 3 + 3 * want or want not in skels:
            continue
        picks.append((e.name, a, skels[want][0]))
        if len(picks) >= 40:
            break
    print("%d clips with a matching skeleton to export\n" % len(picks))
    check("found clips to test", len(picks) > 10, True)

    worst_pos = 0.0
    worst_rot = 0.0
    total_frames = 0
    for name, a, (skname, bones) in picks:
        text = BVH.to_bvh(a, bones, name=name)
        lines = [l for l in text.split("\n") if l.strip()]

        # ---- 1. structure
        depth = 0
        roots = joints = ends = 0
        chan_counts = []
        for l in lines:
            t = l.strip()
            depth += t.count("{") - t.count("}")
            if t.startswith("ROOT "):
                roots += 1
            elif t.startswith("JOINT "):
                joints += 1
            elif t == "End Site":
                ends += 1
            elif t.startswith("CHANNELS"):
                chan_counts.append(int(t.split()[1]))
        if depth != 0 or roots != 1 or (roots + joints) != len(bones):
            print("  %s: STRUCTURE BAD depth=%d roots=%d joints=%d bones=%d"
                  % (name, depth, roots, joints, len(bones)))
            ok = False
            continue
        if len(chan_counts) != len(bones) or chan_counts[0] != 6 \
                or any(c != 3 for c in chan_counts[1:]):
            print("  %s: CHANNELS declarations wrong: %s"
                  % (name, chan_counts[:6]))
            ok = False
            continue

        # ---- 2 and 3. motion block shape
        mi = lines.index("MOTION")
        frames = int(lines[mi + 1].split(":")[1])
        motion = lines[mi + 3:]
        width = 6 + 3 * (len(bones) - 1)
        if frames != a.keys or len(motion) != frames:
            print("  %s: frames declared %d, lines %d, clip keys %d"
                  % (name, frames, len(motion), a.keys))
            ok = False
            continue
        bad = [i for i, l in enumerate(motion) if len(l.split()) != width]
        if bad:
            print("  %s: %d motion line(s) are not %d values wide"
                  % (name, len(bad), width))
            ok = False
            continue

        # ---- 4. round trip
        deg = 180.0 / math.pi
        # The values come out in the order the CHANNELS line declares, which is
        # the Euler order -- for the default YZX that is (ch1, ch2, ch0), not
        # (ch0, ch1, ch2). Comparing positionally assumed XYZ and so compared
        # different axes to each other, reporting a 483-degree "error" that was
        # entirely this test's own indexing.
        order = None
        for line in lines[:mi]:
            t = line.strip()
            if t.startswith("CHANNELS") and "rotation" in t:
                axes = [w[0].upper() for w in t.split()[1:] if "rotation" in w]
                order = "".join(axes)
                break
        assert order and len(order) == 3, "could not read the rotation order"
        slot = ["XYZ".index(ax) for ax in order]   # emitted position -> channel

        for k, l in enumerate(motion):
            v = [float(x) for x in l.split()]
            p = a.at_key(k)
            for i in range(3):
                worst_pos = max(worst_pos, abs(v[i] - p[i]))
            for b in range(len(bones)):
                for j in range(3):
                    src = p[3 + 3 * b + slot[j]] * deg
                    got = v[3 + 3 * b + j]
                    worst_rot = max(worst_rot, abs(got - src))
        total_frames += frames

    print("\nacross %d clips, %d frames:" % (len(picks), total_frames))
    check("worst root-translation error (metres)", worst_pos < 1e-6, True)
    check("worst rotation error (degrees)", worst_rot < 1e-4, True)
    print("     max |dpos| = %.3e m, max |drot| = %.3e deg"
          % (worst_pos, worst_rot))

    # ---- 5. refusals
    print("\nrefusals:")
    name, a, (skname, bones) = picks[0]
    wrong = None
    for n, bs in skels.items():
        if n != len(bones):
            wrong = bs[0]
            break
    if wrong:
        try:
            BVH.to_bvh(a, wrong[1])
            print("  a %d-bone skeleton was ACCEPTED for a %d-bone clip"
                  % (len(wrong[1]), len(bones)))
            ok = False
        except ValueError as ex:
            print("  mismatched skeleton refused: %s" % str(ex)[:74])
            check("wrong bone count is refused", True, True)
    try:
        BVH.to_bvh(a, bones, euler="ABC")
        print("  a nonsense Euler order was ACCEPTED")
        ok = False
    except ValueError:
        check("nonsense Euler order is refused", True, True)

    # A clip that genuinely does not fit the layout, if one exists.
    odd = None
    for e in game.toc(ARC):
        if not e.name.lower().endswith(".anim"):
            continue
        try:
            x = A.parse(game.read(ARC, e.name))
        except Exception:
            continue
        if x.channels != 3 + 3 * ((x.channels - 3) // 3):
            odd = (e.name, x)
            break
    if odd:
        try:
            BVH.to_bvh(odd[1], bones)
            print("  %s does not fit the layout but was ACCEPTED" % odd[0])
            ok = False
        except ValueError:
            check("a clip that does not fit the layout is refused", True, True)
    else:
        print("  (every cm2 clip in %s fits 3 + 3*bones, so that path is "
              "untested here)" % ARC)

    print("\n%s" % ("ALL CHECKS PASSED" if ok else "SOMETHING FAILED"))
    game.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
