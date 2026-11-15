"""The second u32 of a part record is a material-block offset.

It spent a long time labelled `flags` because it looks like a flag word: it
takes only 32, 48, 64 ... 192, i.e. 16*k for k=2..12, contiguous, no gaps. It
is a byte offset within the same chunk; the values are multiples of 16 only
because the blocks sit at a stride of 80 from a 16-aligned base.

The decisive test is deliberately ONE test, not a pile of restatements. An
earlier write-up claimed "four independent checks at 100.00% over 23,042
records", and an adversarial re-derivation showed that was inflated: the
field takes exactly two values disc-wide, so 98% of that denominator is
degenerate, and three of the four checks were implied by the fourth.

What actually carries the weight is a 2x2 with zero off-diagonal:

    container has 1 distinct value  <->  (inner[0] - material_base) == 68
    container has 2 distinct values <->  (inner[0] - material_base) == 148

`inner[0]` is a quantity the field never touches, so "the field takes a
second value 80 higher" and "the region is 80 bytes longer" are independent
measurements. Their perfect agreement cannot be an artifact of the reading.

The float-range check is the semantic backstop: an offset off by one block
lands on inner-record integers and fails it immediately.
"""
import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rdrstudio import Game, rsc  # noqa: E402
from rdrstudio.config import VANILLA_ISO  # noqa: E402

BLOCK = 68        # payload bytes
STRIDE = 80       # 68 padded to a 16-byte boundary


def main():
    game = Game(VANILLA_ISO)
    grid = Counter()          # (distinct values, region length) -> count
    parts = floats_ok = tail_zero = 0
    containers = 0
    deltas = Counter()
    problems = []

    for arc in game.archives():
        try:
            dv = game.archive(arc)
        except Exception:
            continue
        for e in dv:
            if e.ext != "rsc":
                continue
            try:
                r = rsc.Rsc(dv.read(e), e.name)
            except Exception:
                continue
            for c in r.chunks:
                if c.tag != "MOD0":
                    continue
                p = r.parts(c.index)
                if not p:
                    continue
                raw = r.raw(c.index)
                n = raw[0]
                data_start = struct.unpack_from("<I", raw, 8)[0]
                base = data_start + 4 * n
                if base + 4 > len(raw):
                    continue
                inner0 = struct.unpack_from("<I", raw, data_start)[0]
                region = inner0 - base
                offs = {rec["material"] for rec in p}
                grid[(len(offs), region)] += 1
                containers += 1
                for o in offs:
                    deltas[o - base] += 1

                for rec in p:
                    parts += 1
                    o = rec["material"]
                    if o + BLOCK > len(raw):
                        problems.append((e.name, c.index, "block runs past end"))
                        continue
                    vals = struct.unpack_from("<16f", raw, o)
                    if all(0.0 <= v <= 1.0 for v in vals):
                        floats_ok += 1
                    else:
                        problems.append((e.name, c.index, "floats out of [0,1]"))
                    if struct.unpack_from("<I", raw, o + 64)[0] == 0:
                        tail_zero += 1
        game._cache.pop(arc.upper(), None)

    print("containers        : %d" % containers)
    print("part records      : %d" % parts)
    print("16 floats in [0,1]: %d / %d" % (floats_ok, parts))
    print("u32 at +64 is zero: %d / %d" % (tail_zero, parts))
    print("offsets seen, as a delta from material_base: %s"
          % dict(deltas.most_common(6)))

    print("\nthe decisive 2x2 (distinct offsets in a container x region length)")
    on = grid.get((1, BLOCK), 0) + grid.get((2, BLOCK + STRIDE), 0)
    off = sum(v for k, v in grid.items()
              if k not in ((1, BLOCK), (2, BLOCK + STRIDE)))
    for k in sorted(grid):
        mark = "  <- expected" if k in ((1, BLOCK), (2, BLOCK + STRIDE)) else \
               "  <- OFF-DIAGONAL"
        print("   %d distinct value(s), region %4d bytes : %6d%s"
              % (k[0], k[1], grid[k], mark))
    print("\non-diagonal       : %d" % on)
    print("off-diagonal      : %d  (must be 0)" % off)

    for p in problems[:8]:
        print("   %s chunk %d: %s" % p)

    ok = (containers > 1000 and off == 0 and not problems
          and floats_ok == parts and tail_zero == parts)
    print("\n%s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
