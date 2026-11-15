"""Which of a bone's three angles is the MIDDLE one of the Euler decomposition?

The channel-to-bone mapping is settled: bone i owns channels 3+3i, 3+3i+1,
3+3i+2. What is not settled is the order the engine composes them in, and the
exporter currently has to ask.

There is a purely data-driven test, and it needs no emulator. Every Euler
decomposition of a rotation matrix produces a MIDDLE angle from an `asin` (or
`acos`), so it is mathematically confined to a 180-degree range -- conventionally
[-90, +90]. The outer two come from `atan2` and use the full [-180, +180].

So: measure the extremes of each of the three positions across every animation
on the disc. If exactly one position is capped near 90 degrees while the other
two reach 180, that position is the middle axis of the decomposition, and the
six candidate orders collapse to two.

This CAN fail to be conclusive -- if the encoder clamped, or if the data simply
never exercises the full range, all three will look bounded or all three
unbounded. That is a real outcome and the script says so rather than forcing an
answer.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdrstudio.game import Game                      # noqa: E402
from rdrstudio import config                          # noqa: E402
from rdrstudio import anim as A                       # noqa: E402

# REQ-035/036. Was a literal path to the WORKING ISO on one machine:
# unrunnable on anyone else's checkout, and pointed at the disc the live
# tests mutate. What this examines is a property of the SHIPPED data, so
# it reads the stock disc. Read-only throughout, which is what makes
# vanilla the right answer here rather than merely the tidier one.
DISC = Path(config.VANILLA_ISO)
LIMIT = 1200
RAD90 = 1.5707963267948966
RAD180 = 3.141592653589793


def main():
    game = Game(DISC, writable=False)
    seen = set()
    # Per position within a bone's triple: the largest |angle| ever seen, and
    # how many samples exceeded 90 degrees.
    peak = [0.0, 0.0, 0.0]
    over90 = [0, 0, 0]
    over170 = [0, 0, 0]
    total = [0, 0, 0]
    files = 0

    for arc in game.archives():
        try:
            toc = game.toc(arc)
        except Exception:
            continue
        for e in toc:
            n = e.name.lower()
            if not n.endswith(".anim") or n in seen:
                continue
            seen.add(n)
            try:
                a = A.parse(game.read(arc, e.name))
            except Exception:
                continue
            bones = (a.channels - 3) // 3
            if a.channels != 3 + 3 * bones or bones < 1:
                continue
            files += 1
            for k in range(a.keys):
                p = a.at_key(k)
                for b in range(bones):
                    base = 3 + 3 * b
                    for j in range(3):
                        v = abs(p[base + j])
                        total[j] += 1
                        if v > peak[j]:
                            peak[j] = v
                        if v > RAD90 + 1e-6:
                            over90[j] += 1
                        if v > RAD180 * 0.944:      # beyond 170 degrees
                            over170[j] += 1
            if files >= LIMIT:
                break
        if files >= LIMIT:
            break

    deg = 180.0 / RAD180
    print("%d animations, %s samples per position\n"
          % (files, "{:,}".format(total[0])))
    print("  position   peak |angle|   over 90deg        over 170deg")
    for j in range(3):
        print("     %d       %8.3f deg   %9d (%5.2f%%)  %8d (%5.3f%%)"
              % (j, peak[j] * deg, over90[j], 100.0 * over90[j] / (total[j] or 1),
                 over170[j], 100.0 * over170[j] / (total[j] or 1)))

    bounded = [j for j in range(3) if peak[j] * deg < 91.0]
    unbounded = [j for j in range(3) if peak[j] * deg > 170.0]

    print("\n  positions capped at ~90 deg : %s" % (bounded or "none"))
    print("  positions reaching >170 deg : %s" % (unbounded or "none"))

    ok = False
    if len(bounded) == 1 and len(unbounded) == 2:
        mid = bounded[0]
        print("\n  => position %d is the MIDDLE axis of the decomposition." % mid)
        axis = "XYZ"[mid]
        others = [c for c in "XYZ" if c != axis]
        cands = ["%s%s%s" % (others[0], axis, others[1]),
                 "%s%s%s" % (others[1], axis, others[0])]
        print("     That narrows six candidate orders to two: %s or %s."
              % (cands[0], cands[1]))
        print("     Which of those two is right still needs the engine or the")
        print("     emulator -- this test fixes the middle axis, not the")
        print("     handedness of the outer pair.")
        ok = True
    else:
        print("\n  => NOT CONCLUSIVE. The middle angle of an Euler")
        print("     decomposition must be confined to +/-90 degrees, and that")
        print("     is not what this data shows. Possible reasons, none of")
        print("     which this script can distinguish:")
        print("       * the channels are not a classical Euler triple at all")
        print("         (they could be a scaled axis-angle or a quaternion's")
        print("          imaginary part, both of which are common in PS2-era")
        print("          engines and would use the full range on all three);")
        print("       * the encoder emitted an unwrapped/continuous angle")
        print("         rather than the principal value, which the modular")
        print("         unwrap in sampleLerp at 0x002D3504 hints it might;")
        print("       * the quantiser rail at +/-32768 maps to +/-pi and the")
        print("         extremes are simply clamped values, not real angles.")
        print("     Do NOT pick an order on the strength of this.")

    game.close()
    # Being inconclusive is a legitimate result, not a test failure -- the
    # script's job is to report what the data supports.
    print("\n%s" % ("MIDDLE AXIS IDENTIFIED" if ok else "INCONCLUSIVE"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
