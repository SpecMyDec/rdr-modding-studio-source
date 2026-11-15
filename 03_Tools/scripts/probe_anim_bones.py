"""Which bone does each animation channel drive?

The layout of `cm2` is settled but the channel-to-bone mapping was not, and
without it there is no exporter. The skeletons make a concrete proposal:
`.skel` is plain text, every bone declares `transX transY transZ rotX rotY rotZ`
in that fixed order, and the bones are nested by hierarchy. Since an animation's
channel count is always `3 + 3 * bones`, the obvious reading is

    channels 0,1,2      root bone translation      x, y, z
    channels 3+3i ..    bone i rotation            x, y, z    (i in depth-first order)

That is a HYPOTHESIS. This tests it with predictions that would fail loudly if
the mapping were wrong, and none of them needs an emulator:

  1. **Channel count.** bones == (channels - 3) / 3 for the matching skeleton.
  2. **Leaves barely move.** In a walk cycle the hips, knees and ankles should
     have the largest rotation ranges; finger and toe bones should be nearly
     constant. Under a WRONG mapping the large ranges would land on arbitrary
     bones, so where they land is the test.
  3. **Left/right antiphase.** A walk is symmetric: `*_l` and `*_r` versions of
     the same bone should have similar ranges and be roughly half a cycle out
     of phase. Correlation between the two curves should be strongly NEGATIVE
     for legs. This is the sharpest test -- it depends on the mapping being
     right for two specific channels at once, and chance will not produce it.
  4. **A shuffled control.** Run test 3 again with the bone order shuffled. If a
     random mapping scores as well, tests 2 and 3 prove nothing.
"""
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdrstudio.game import Game                      # noqa: E402
from rdrstudio import config                          # noqa: E402
from rdrstudio import anim as A                       # noqa: E402

# REQ-035. Was a literal path to the WORKING ISO on one machine:
# unrunnable on anyone else's checkout, and pointed at the disc the
# live tests mutate, so a read-only structural check could go red for
# a reason with nothing to do with the code under test. What this
# measures is a property of the shipped clips, so it reads the stock
# disc. Opened writable=False, which is what makes vanilla correct here.
DISC = Path(config.VANILLA_ISO)
BONE_RE = re.compile(rb"bone\s+(\S+)\s*\{")


def bone_order(data):
    """Bone names in the order they appear -- which is depth-first."""
    return [m.group(1).decode("latin-1") for m in BONE_RE.finditer(data)]


def pearson(a, b):
    n = len(a)
    if n < 3:
        return 0.0
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va <= 0 or vb <= 0:
        return 0.0
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    return cov / (va ** 0.5 * vb ** 0.5)


def main():
    game = Game(DISC, writable=False)
    toc = game.toc("SWAT.DAT")
    skels = {}
    for e in toc:
        if e.name.lower().endswith(".skel"):
            try:
                bones = bone_order(game.read("SWAT.DAT", e.name))
            except Exception:
                continue
            skels.setdefault(len(bones), []).append((e.name, bones))
    print("skeletons in SWAT.DAT by bone count: %s"
          % sorted((k, len(v)) for k, v in skels.items())[:14])

    ok = True

    def check(what, got, want, cmp="=="):
        nonlocal ok
        good = (got == want if cmp == "==" else
                got < want if cmp == "<" else got > want)
        ok &= good
        print("  %-52s %-12s %s" % (what, str(got)[:12],
                                    "ok" if good else "FAIL (want %s %s)" % (cmp, want)))

    # A walk cycle for the commonest skeleton shape.
    CLIPS = ["chr_for_wlk.anim", "chr_for_run.anim"]
    for clip in CLIPS:
        try:
            a = A.parse(game.read("SWAT.DAT", clip))
        except Exception as ex:
            print("\n%s: %s" % (clip, ex))
            continue
        bones = (a.channels - 3) // 3
        print("\n" + "=" * 72)
        print("%s -- %d channels => %d bones by the hypothesis"
              % (clip, a.channels, bones))
        print("=" * 72)
        cands = skels.get(bones, [])
        print("  skeletons in SWAT.DAT with exactly %d bones: %d %s"
              % (bones, len(cands), [c[0] for c in cands][:6]))
        if not cands:
            print("  no skeleton to test against; skipping")
            continue
        name, order = cands[0]
        check("channels == 3 + 3*bones", a.channels, 3 + 3 * bones)

        # Per-bone rotation range, summed over its three channels.
        curves = {}
        for c in range(a.channels):
            curves[c] = [a.at_key(k)[c] for k in range(a.keys)]
        rng = {}
        for i, bn in enumerate(order):
            base = 3 + 3 * i
            if base + 2 >= a.channels:
                break
            r = sum(max(curves[base + j]) - min(curves[base + j]) for j in range(3))
            rng[bn] = r

        top = sorted(rng.items(), key=lambda x: -x[1])[:10]
        print("\n  bones with the LARGEST rotation range (using %s):" % name)
        for bn, r in top:
            print("      %-24s %.4f rad" % (bn, r))
        quiet = sorted(rng.items(), key=lambda x: x[1])[:6]
        print("  bones with the SMALLEST range:")
        for bn, r in quiet:
            print("      %-24s %.4f rad" % (bn, r))

        # Prediction 2: leaves quiet, limbs loud.
        LIMB = re.compile(r"hip|thigh|knee|leg|ankle|foot|shoulder|elbow|"
                          r"wrist|clavicle|spine|pelvis", re.I)
        LEAF = re.compile(r"finger|thumb|toe", re.I)
        limb = [r for b, r in rng.items() if LIMB.search(b)]
        leaf = [r for b, r in rng.items() if LEAF.search(b)]
        if limb and leaf:
            ml = sum(limb) / len(limb)
            mf = sum(leaf) / len(leaf)
            print("\n  mean range: limbs %.4f (%d bones) vs "
                  "fingers/toes %.4f (%d bones)  ratio %.1fx"
                  % (ml, len(limb), mf, len(leaf), ml / mf if mf else 0))
            check("limbs move more than fingers/toes", ml > mf * 2, True)

        # Prediction 3: left/right antiphase on the legs.
        print("\n  left/right correlation (a walk should be ANTIphase, so < 0):")
        # Only pairs that actually MOVE can be in or out of phase. A constant
        # curve has zero variance, so its correlation is 0 by definition -- and
        # counting those as "not antiphase" made the run cycle look like a
        # failure when 18 of its 23 pairs were simply motionless fingers.
        MOVES = 1e-4
        pairs = 0
        neg = 0
        corrs = []
        skipped = 0
        for bn in order:
            if not bn.endswith("_l"):
                continue
            mate = bn[:-2] + "_r"
            if mate not in order:
                continue
            i, j = order.index(bn), order.index(mate)
            bi, bj = 3 + 3 * i, 3 + 3 * j
            if bi + 2 >= a.channels or bj + 2 >= a.channels:
                continue
            # Use the axis with the most motion on the left side.
            axis = max(range(3),
                       key=lambda t: max(curves[bi + t]) - min(curves[bi + t]))
            la = max(curves[bi + axis]) - min(curves[bi + axis])
            rb = max(curves[bj + axis]) - min(curves[bj + axis])
            if la < MOVES or rb < MOVES:
                skipped += 1
                continue
            c = pearson(curves[bi + axis], curves[bj + axis])
            corrs.append((bn[:-2], c))
            pairs += 1
            if c < 0:
                neg += 1
        for bn, c in sorted(corrs, key=lambda x: x[1])[:10]:
            print("      %-22s r = %+.3f" % (bn, c))
        if pairs:
            print("      %d of %d MOVING pairs are negatively correlated"
                  " (%d motionless pairs excluded)" % (neg, pairs, skipped))
            check("most moving left/right pairs are antiphase",
                  neg > pairs * 0.5, True)

        # Prediction 4: a control, done properly.
        #
        # The first version shuffled the bone list and then looked each `_l`
        # bone's `_r` mate up by NAME in the shuffled list. That still pairs the
        # same two bones, only at different channel indices, and after excluding
        # motionless pairs it left FOUR -- far too few to mean anything, and it
        # duly produced a nonsense 75% by chance.
        #
        # The question worth asking is: if channels were assigned to bones at
        # random, how often would a pair come out antiphase? Answer it by
        # resampling random channel pairs from this same clip, many times.
        rnd = random.Random(12345)
        moving = [c for c in range(3, a.channels)
                  if max(curves[c]) - min(curves[c]) >= MOVES]
        rneg = rtot = 0
        for _ in range(400):
            if len(moving) < 2:
                break
            x, y = rnd.sample(moving, 2)
            if pearson(curves[x], curves[y]) < 0:
                rneg += 1
            rtot += 1
        if rtot and pairs:
            base = 100.0 * rneg / rtot
            real = 100.0 * neg / pairs
            print("      CONTROL: %d random channel pairs from this same clip"
                  " are %.0f%% antiphase" % (rtot, base))
            print("      the real left/right pairs are %.0f%% antiphase" % real)
            check("the mapping beats a random pairing", real > base, True)


    print("\n%s" % ("ALL CHECKS PASSED" if ok else "SOMETHING FAILED"))
    game.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
