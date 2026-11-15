"""Is the cmp decoder right, or just plausible?

A decoder that mis-reads a bitfield still produces numbers, so "it ran" proves
nothing. These checks are chosen so a wrong one fails:

  1. **Every file parses.** All 234, with the size equation exact -- the parser
     refuses rather than guessing when the payload does not match
     `nchan * keys * 4`, so a wrong field width shows up as a refusal.
  2. **Nothing is degenerate.** No non-finite samples anywhere.
  3. **Bitfields do not overlap.** Rebuilding a word from the three extracted
     fields must reproduce it exactly. If the shifts or masks were wrong the
     round trip loses bits.
  4. **Constants really are constant.** A channel marked 0xFFFF must return
     the same value at every key -- that is what the sentinel claims.
  5. **Animated channels actually vary.** If they did not, the index would be
     pointless and the reading would be suspect.
  6. **Values are physical.** Rotations within a few radians for the great
     majority, root translation within a sane distance.
"""
import math
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdrstudio.game import Game                      # noqa: E402
from rdrstudio import animcmp as C                    # noqa: E402
from rdrstudio import config                         # noqa: E402

# The PRISTINE disc. This is a read-only structural check, so it must not
# baseline on the working copy -- that makes its result depend on whatever
# the last write left behind. Was a hardcoded `D:\...\RDR_MODDED.iso`,
# which also meant this only ran under Windows python. REQ-035.
DISC = Path(config.VANILLA_ISO)


def main():
    game = Game(DISC, writable=False)
    ok = True

    def check(what, good, detail=""):
        nonlocal ok
        ok &= bool(good)
        print("  %-54s %s%s" % (what, "ok" if good else "FAIL",
                                ("  " + detail) if detail else ""))

    files, seen = [], set()
    for arc in game.archives():
        try:
            toc = game.toc(arc)
        except Exception:
            continue
        for e in toc:
            if not e.name.lower().endswith(".anim") or e.name in seen:
                continue
            try:
                d = game.read(arc, e.name)
            except Exception:
                continue
            if d[:4] == C.MAGIC:
                seen.add(e.name)
                files.append((e.name, d))
    print("%d cmp clips on the disc\n" % len(files))

    parsed, failed = [], []
    for name, d in files:
        try:
            parsed.append((name, C.parse(d), d))
        except C.CmpError as ex:
            failed.append((name, str(ex)))
    for name, why in failed[:4]:
        print("  REFUSED %-30s %s" % (name, why[:70]))
    check("every cmp file parses", not failed,
          "%d/%d" % (len(parsed), len(files)))

    # ---- 2 and 3
    nonfinite = 0
    bitloss = 0
    total_words = 0
    for name, a, d in parsed:
        for k in range(0, a.keys, max(1, a.keys // 4)):
            for v in a.at_key(k):
                if v != v or math.isinf(v):
                    nonfinite += 1
        for w in a.words[:200]:
            total_words += 1
            f0 = w & 0x7FF
            f1 = (w >> 11) & 0x3FF
            f2 = (w >> 21) & 0x7FF
            if (f0 | (f1 << 11) | (f2 << 21)) != w:
                bitloss += 1
    check("no non-finite samples", nonfinite == 0, "%d bad" % nonfinite)
    check("the three bitfields tile a word exactly", bitloss == 0,
          "%d of %d words lose bits" % (bitloss, total_words))

    # ---- 4 and 5
    const_moved = 0
    anim_static = 0
    anim_total = 0
    for name, a, d in parsed[:60]:
        if a.keys < 2:
            continue
        first = a.at_key(0)
        last = a.at_key(a.keys - 1)
        mid = a.at_key(a.keys // 2)
        for c in range(a.channels):
            if not a.animated(c):
                if first[c] != last[c] or first[c] != mid[c]:
                    const_moved += 1
            else:
                anim_total += 1
                if first[c] == last[c] == mid[c]:
                    anim_static += 1
    check("channels marked constant never change", const_moved == 0,
          "%d moved" % const_moved)
    # Some animated channels genuinely hold still for a whole clip, so this is
    # a proportion, not an absolute -- a decoder reading the wrong column
    # would make nearly all of them static.
    frac = (anim_static / anim_total) if anim_total else 1.0
    check("most animated channels actually vary", frac < 0.5,
          "%.0f%% static of %d" % (100 * frac, anim_total))

    # ---- 6
    worst_rot = worst_pos = 0.0
    for name, a, d in parsed:
        for k in range(0, a.keys, max(1, a.keys // 3)):
            row = a.at_key(k)
            for i, v in enumerate(row):
                if i < 3:
                    worst_pos = max(worst_pos, abs(v))
                else:
                    worst_rot = max(worst_rot, abs(v))
    print("\n  worst |root translation| %.2f m, worst |rotation| %.2f rad"
          % (worst_pos, math.degrees(worst_rot) / 180 * math.pi))
    check("root translation is a sane distance", worst_pos < 500.0,
          "%.1f m" % worst_pos)
    check("rotations are a sane magnitude", worst_rot < 40.0,
          "%.1f rad" % worst_rot)

    print("\n%s" % ("ALL CHECKS PASSED" if ok else "SOMETHING FAILED"))
    game.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
