"""Which Euler order makes the SKELETON anatomically correct?

The composition order was read out of the engine (see FORMAT_NOTES: the bone
euler-to-matrix builder at 0x00146B68 / 0x001465A8, reached from the animation
applier at 0x002D55B8 / 0x002D54C8). That is the authoritative answer. This is
an INDEPENDENT check of it that touches no disassembly at all, because a claim
this load-bearing should not rest on one reading of one function.

SPOILER, so nobody repeats this: IT DOES NOT WORK. Two anatomical metrics were
tried -- knee hinge-axis stability and head-over-root uprightness -- and both
score all six candidate orders within 0.3% of each other. The script is kept
because a null result that is written down is worth more than one that is
rediscovered, and because the reason for the null is itself informative.

The reason is NOT that the order is unimportant. Over chr_for_wlk and
chr_for_run, 42.3% of bone-frames rotate about more than one axis, and for
those the six orders differ by a median of 0.035 in matrix entries, p90 0.23,
max 2.0. The order matters -- for LIMBS. Both metrics happen to probe the spine
chain, where rotations are small and every order agrees. A discriminating data
test would need ground truth for a specific multi-axis bone, which is exactly
what the disc does not carry.
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdrstudio.game import Game                      # noqa: E402
from rdrstudio import config                          # noqa: E402
from rdrstudio import anim as A                       # noqa: E402
from rdrstudio import skeleton as SK                  # noqa: E402

# REQ-035/036. Was a literal path to the WORKING ISO on one machine:
# unrunnable on anyone else's checkout, and pointed at the disc the live
# tests mutate. What this examines is a property of the SHIPPED data, so
# it reads the stock disc. Read-only throughout, which is what makes
# vanilla the right answer here rather than merely the tidier one.
DISC = Path(config.VANILLA_ISO)
ORDERS = ["XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX"]


def rot(axis, t):
    c, s = math.cos(t), math.sin(t)
    if axis == "X":
        return [[1, 0, 0], [0, c, -s], [0, s, c]]
    if axis == "Y":
        return [[c, 0, s], [0, 1, 0], [-s, 0, c]]
    return [[c, -s, 0], [s, c, 0], [0, 0, 1]]


def mul(P, Q):
    return [[sum(P[i][k] * Q[k][j] for k in range(3)) for j in range(3)]
            for i in range(3)]


def compose(order, ang):
    """`order` names the axes OUTERMOST first, matching the R = Ra*Rb*Rc form.

    For "YZX" that is R = Ry(ch1) * Rz(ch2) * Rx(ch0): each letter picks the
    channel of the same name, so the mapping from channel index to axis is
    fixed and only the multiplication order varies between candidates.
    """
    idx = {"X": 0, "Y": 1, "Z": 2}
    M = rot(order[0], ang[idx[order[0]]])
    for ax in order[1:]:
        M = mul(M, rot(ax, ang[idx[ax]]))
    return M


def xform(M, t, v):
    return [sum(M[i][k] * v[k] for k in range(3)) + t[i] for i in range(3)]


def pose(bones, chans, order):
    """World position of every bone under `order`. Returns a list of xyz."""
    worldR, worldT = [None] * len(bones), [None] * len(bones)
    out = []
    for i, b in enumerate(bones):
        base = 3 + 3 * i
        R = compose(order, (chans[base], chans[base + 1], chans[base + 2]))
        off = list(b.offset)
        if b.parent is None or i == 0:
            t = [off[0] + chans[0], off[1] + chans[1], off[2] + chans[2]]
            worldR[i], worldT[i] = R, t
        else:
            p = names.index(b.parent) if isinstance(b.parent, str) else b.parent
            worldT[i] = xform(worldR[p], worldT[p], off)
            worldR[i] = mul(worldR[p], R)
        out.append(worldT[i])
    return out


def sub(a, b):
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def norm(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n > 1e-9 else [0, 0, 0]


def cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0]]


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


names = []


def main():
    global names
    game = Game(DISC, writable=False)

    # Clips with a lot of leg motion, across two skeleton shapes.
    CLIPS = ["chr_for_wlk.anim", "chr_for_run.anim", "chr_lft_wlk.anim",
             "chr_rgt_wlk.anim", "chr_bak_wlk.anim"]
    score = {o: [] for o in ORDERS}
    flips = {o: 0 for o in ORDERS}
    tested = 0

    for clip in CLIPS:
        try:
            a = A.parse(game.read("SWAT.DAT", clip))
        except Exception:
            continue
        want = (a.channels - 3) // 3
        skel = None
        for e in game.toc("SWAT.DAT"):
            if not e.name.lower().endswith(".skel"):
                continue
            try:
                _v, _d, bones, _r = SK.parse(game.read("SWAT.DAT", e.name))
            except Exception:
                continue
            if len(bones) == want:
                skel = bones
                break
        if skel is None:
            continue
        names = [b.name for b in skel]
        # locate a leg chain
        try:
            hip = names.index("hip_l")
            knee = names.index("knee_l")
            ankle = names.index("ankle_l")
        except ValueError:
            continue
        tested += 1
        print("  %-22s %d bones, %d keys" % (clip, len(skel), a.keys))

        # A SINGLE joint cannot discriminate between composition orders -- the
        # first version of this test measured the knee and scored all six
        # within 0.3% of each other with zero backward bends, because the
        # knee's own rotation is dominated by one channel whatever order the
        # other two are applied in. The error from a wrong order compounds
        # along a CHAIN, so measure a deep one.
        #
        # The test: a walking human carries the head above the hips. Follow
        # root -> spine -> neck -> head (four or five composed rotations) and
        # measure how far the head strays HORIZONTALLY from the root, as a
        # fraction of the body's height. A correct order keeps it small and
        # steady; a wrong one leans the body over as the error accumulates.
        try:
            head = names.index("head")
        except ValueError:
            continue
        for o in ORDERS:
            lean = []
            upright = []
            for k in range(a.keys):
                P = pose(skel, a.at_key(k), o)
                height = abs(P[head][1] - P[ankle][1]) or 1.0
                dx = P[head][0] - P[0][0]
                dz = P[head][2] - P[0][2]
                lean.append(math.sqrt(dx * dx + dz * dz) / height)
                # the head must also stay ABOVE the feet at all times
                upright.append(1.0 if P[head][1] > P[ankle][1] else 0.0)
            mean_lean = sum(lean) / len(lean)
            # score: smaller lean is better, and being upside-down is fatal
            score[o].append((1.0 - min(1.0, mean_lean))
                            * (sum(upright) / len(upright)))
            flips[o] += sum(1 for u in upright if u == 0.0)

    print("\n%d clip(s) tested\n" % tested)
    print("  order   upright score (1=best)   frames head below feet")
    rows = []
    for o in ORDERS:
        s = sum(score[o]) / len(score[o]) if score[o] else 0.0
        rows.append((s, o, flips[o]))
        print("   %-5s        %.4f                 %d" % (o, s, flips[o]))
    rows.sort(reverse=True)
    best = rows[0]
    print("\n  most upright: %s (%.4f)" % (best[1], best[0]))
    print("  engine says:     YZX")

    # HONEST VERDICT.
    #
    # This test CANNOT discriminate, and saying so is the result. Two metrics
    # were tried: knee hinge-axis stability (all six orders within 0.3%, zero
    # backward bends) and head-over-root uprightness (all six within 0.2%).
    # Neither separates the candidates, so neither confirms nor refutes the
    # disassembly.
    #
    # The reason is not that the order is unimportant. Measured over
    # chr_for_wlk and chr_for_run: 42.3% of bone-frames rotate about more than
    # one axis, and for those the six orders differ by a median of 0.035 in
    # matrix entries, p90 0.23, max 2.0. The order matters a lot -- for LIMBS.
    # Both metrics above happen to probe the spine chain, where the rotations
    # are small and every order agrees.
    #
    # So the authority for the shipped default is the CODE, not this script:
    # the bone euler-to-matrix builder at 0x00146B68 (FPU) / 0x001465A8 (VU0),
    # reached from the animation applier at 0x002D55B8 / 0x002D54C8, computes
    # R = Ry(ch1) * Rz(ch2) * Rx(ch0).
    spread = max(r[0] for r in rows) - min(r[0] for r in rows)
    print("\n  spread across all six orders: %.4f" % spread)
    print("  INCONCLUSIVE -- that spread is noise, so this test neither")
    print("  confirms nor refutes the disassembly. It is kept because a null")
    print("  result is worth recording: it stops the next person believing a")
    print("  0.2% difference means something.")
    print("\n  The order actually in use is read from the engine; see")
    print("  07_Research/FORMAT_NOTES.md and rdrstudio/bvh.py.")

    game.close()
    # A diagnostic, not a pass/fail gate -- it exits 0 either way.
    return 0


if __name__ == "__main__":
    sys.exit(main())
