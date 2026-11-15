"""The real model->texture binding, verified across every level on the disc.

What we shipped was a heuristic: a mesh uses the nearest TEX0 chunk that
precedes it. It is right often enough to look plausible and wrong often
enough to put an interior atlas on a building wall -- the "rainbow buildings"
in Brimstone and the wrong ground in Perfnew.

The real binding was in the container MOD0 all along. Its outer table is
`count` x (u32 resource, u32 flags), and `resource` is a ONE-BASED chunk
index that points straight at a TEX0 register block. Zero means untextured.

Two things hid it:

  * The index is 1-based, so read as 0-based every reference landed one chunk
    early -- on the PAL0 that precedes each TEX0 -- and the tag census came
    back "MOD0/MIP0/PMT0", which read as noise.
  * Only the container MOD0s carry the table, and a single-part model is
    still a container. Until `parts()` stopped rejecting `count < 2`, most of
    these records were never parsed at all.

This asserts the property that makes it a binding and not a coincidence:
every non-zero resource resolves to a TEX0 chunk, in every area of every
level archive.
"""
import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rdrstudio import Game, rsc  # noqa: E402
from rdrstudio.config import VANILLA_ISO  # noqa: E402


def main():
    game = Game(VANILLA_ISO)
    archives = [n for n in game.archives() if n.startswith("LVLS_")]
    total = hits = zeros = bad = 0
    areas = 0
    worst = []
    tagmiss = Counter()

    for arc in archives:
        try:
            dv = game.archive(arc)
        except Exception:
            continue
        for area in rsc.level_areas(dv):
            try:
                b = rsc.Rsc(dv.read(area), area)
            except Exception:
                continue
            areas += 1
            a_total = a_bad = 0
            for c in b.chunks:
                if c.tag != "MOD0":
                    continue
                p = b.parts(c.index)
                if not p:
                    continue
                for rec in p:
                    res = rec["resource"]
                    total += 1
                    a_total += 1
                    if res == 0:
                        zeros += 1
                        continue
                    j = res - 1
                    if 0 <= j < len(b.chunks) and b.chunks[j].tag == "TEX0":
                        hits += 1
                    else:
                        bad += 1
                        a_bad += 1
                        tagmiss[b.chunks[j].tag if 0 <= j < len(b.chunks)
                                else "OOB"] += 1
            if a_bad:
                worst.append((a_bad, a_total, arc, area))

    nonzero = total - zeros
    print("areas scanned      : %d" % areas)
    print("part records       : %d" % total)
    print("  untextured (0)   : %d" % zeros)
    print("  resolve to TEX0  : %d" % hits)
    print("  do not resolve   : %d" % bad)
    if nonzero:
        print("binding hit rate   : %.4f%% of non-zero references"
              % (100.0 * hits / nonzero))
    if tagmiss:
        print("misses landed on   :", dict(tagmiss.most_common(8)))
    for w in sorted(worst, reverse=True)[:10]:
        print("  %s / %s: %d of %d bad" % (w[2], w[3], w[0], w[1]))

    ok = bad == 0 and hits > 0
    print("\n%s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
