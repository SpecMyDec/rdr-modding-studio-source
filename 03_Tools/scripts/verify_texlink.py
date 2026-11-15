"""A TEX0 chunk names its own pixels and palette. It does not have to be
guessed from disc order.

`texture_table()` pairs each TEX0 with "the MIP0 and PAL0 in the four chunks
before it", because on disc the order is MIP0, PAL0, TEX0. That holds for
most bundles and quietly fails where it does not -- and a texture wearing
another texture's palette is exactly the "rainbow" failure mode.

The link is stored. A TEX0 chunk is 192 bytes and mostly zero at rest (the
zeroed words are runtime pointers the engine fills in on load), but three
fields survive on disc:

    0x84  u32  offset of the name within the chunk (always 0xA0)
    0x88  u16 width, u16 height
    0x90  u32, u32  ONE-BASED chunk indices of this texture's pixels and
                    palette -- the same one-based convention as the `.rnt`
                    manifest, the per-surface texture binding and the .znes
                    resourceIndex

This checks that both indices resolve, that one is a MIP0 and the other a
PAL0/PMT0, and that they agree with what the disc-order guess produced.
"""
import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rdrstudio import Game, rsc  # noqa: E402
from rdrstudio.config import VANILLA_ISO  # noqa: E402

PIXELS = {"MIP0"}
PALETTE = {"PAL0", "PMT0"}


def main():
    game = Game(VANILLA_ISO)
    total = resolved = agreed = 0
    disagreed = []
    shapes = Counter()

    for arc in game.archives():
        if not arc.startswith("LVLS_"):
            continue
        try:
            dv = game.archive(arc)
        except Exception:
            continue
        for area in rsc.level_areas(dv):
            try:
                r = rsc.Rsc(dv.read(area), area)
            except Exception:
                continue
            guess = {t[0]: (t[2].index, t[3].index) for t in r.texture_table()}
            n = len(r.chunks)
            for c in r.chunks:
                if c.tag != "TEX0" or c.size < 0xA0:
                    continue
                b = r.raw(c.index)
                a, bb = struct.unpack_from("<II", b, 0x90)
                total += 1
                ia, ib = a - 1, bb - 1
                tags = []
                for j in (ia, ib):
                    tags.append(r.chunks[j].tag if 0 <= j < n else "OOB")
                shapes[tuple(tags)] += 1
                pix = pal = None
                for j, tag in zip((ia, ib), tags):
                    if tag in PIXELS:
                        pix = j
                    elif tag in PALETTE:
                        pal = j
                if pix is None or pal is None:
                    continue
                resolved += 1
                g = guess.get(c.index)
                if g is None:
                    continue
                if g == (pix, pal):
                    agreed += 1
                elif len(disagreed) < 12:
                    disagreed.append((arc, area, c.index, g, (pix, pal)))

    print("TEX0 chunks examined : %d" % total)
    print("both indices resolve : %d  (%.2f%%)"
          % (resolved, 100.0 * resolved / total if total else 0))
    print("agree with disc-order guess : %d" % agreed)
    print("\nwhat the two indices point at")
    for k, v in shapes.most_common(8):
        print("   %-16s %6d" % ("+".join(k), v))
    if disagreed:
        print("\nwhere the stored link and the guess differ")
        for d in disagreed:
            print("   %s/%s chunk %d: guess %s  stored %s" % d)
    ok = total and resolved == total
    print("\n%s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
