"""`resourceIndex` in a .znes scene node is ONE-BASED, like every other
resource reference in this engine.

A scene node is an instance: a name, a transform, and a `resourceIndex`
saying which chunk of the area's RSC0 bundle to draw. We read that as a
0-based chunk index, and it very nearly works -- most of the time it lands
on a MOD0 anyway, because models are the commonest chunk and they cluster.
That is exactly what makes it dangerous: it looks right.

Read 0-based, a fifth of the references land on a MIP0, a PAL0, a SAN0 --
chunk kinds that cannot be an instance of anything. Read 1-based, every
single one lands on a MOD0 or a BND0, which are the only two things a scene
node can point at.

The consequence in the editor was quiet: `by_res` matched each mesh to the
node one chunk off, so objects wore their neighbour's name, and the asset
catalogue's "is this chunk real geometry?" filter was testing the wrong
chunk. Nothing crashed; the labels were simply wrong.

This is the third 1-based reference found in the format, after the `.rnt`
manifest and the container MOD0's texture binding. It is the convention.
"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rdrstudio import Game, rsc  # noqa: E402
from rdrstudio.config import VANILLA_ISO  # noqa: E402

# The only chunk kinds a scene node can be an instance of.
INSTANCEABLE = {"MOD0", "BND0"}


def main():
    game = Game(VANILLA_ISO)
    from rdrstudio.level import Level

    tally = {0: Counter(), -1: Counter()}
    areas = total = 0
    for arc in game.archives():
        if not arc.startswith("LVLS_"):
            continue
        try:
            dv = game.archive(arc)
        except Exception:
            continue
        for area in rsc.level_areas(dv):
            try:
                lvl = Level(game, arc, area=area)
                r = lvl._load_rsc()
            except Exception:
                continue
            if r is None:
                continue
            areas += 1
            n = len(r.chunks)
            for node in lvl.nodes:
                ri = node.props.get("resourceIndex")
                if ri is None:
                    continue
                total += 1
                for d in (0, -1):
                    j = int(ri) + d
                    tally[d][r.chunks[j].tag if 0 <= j < n else "OOB"] += 1

    print("areas: %d   scene nodes with a resourceIndex: %d\n" % (areas, total))
    ok = True
    for d in (0, -1):
        c = tally[d]
        good = sum(v for k, v in c.items() if k in INSTANCEABLE)
        pct = 100.0 * good / total if total else 0
        print("read as %s (delta %+d)" % ("0-based" if d == 0 else "1-based", d))
        print("   %s" % dict(c.most_common(8)))
        print("   lands on MOD0/BND0: %d / %d  (%.2f%%)\n" % (good, total, pct))
        if d == -1 and pct < 100.0:
            ok = False
        if d == 0 and pct >= 100.0:
            ok = False       # would mean there is nothing to fix

    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
