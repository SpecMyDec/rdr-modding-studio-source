"""`flags` is 16*k for k in 2..12. What is k?

The value is not a bitfield -- it takes every multiple of 16 from 0x20 to
0xC0 with no gaps, which no flag word does. A contiguous small range scaled
by 16 looks like a size or a count of quadwords. This measures it against
what the packet actually contains: bytes, vertices, UNPACK streams, and the
vertex formats used.
"""
import struct
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rdrstudio import Game, rsc, vif  # noqa: E402
from rdrstudio.config import VANILLA_ISO  # noqa: E402

LEVELS = [("LVLS_SNG/TOMB.DAT", "lvl_tomb.rsc"),
          ("LVLS_SNG/PERFNEW.DAT", "lvl_perfnew.rsc"),
          ("LVLS_SNG/SHER70.DAT", "lvl_sher70.rsc"),
          ("LVLS_SNG/WOODS.DAT", "lvl_woods.rsc")]


def main():
    game = Game(VANILLA_ISO)
    rows = defaultdict(list)
    kinds = defaultdict(lambda: defaultdict(int))

    for arc, area in LEVELS:
        try:
            r = rsc.Rsc(game.archive(arc).read(area), area)
        except Exception as e:
            print("%-20s ERR %s" % (area, e))
            continue
        for c in r.chunks:
            if c.tag != "MOD0":
                continue
            parts = r.parts(c.index)
            if not parts:
                continue
            raw = r.raw(c.index)
            for p in parts:
                f = p["flags"]
                nbytes = (p["end"] or len(raw)) - p["offset"]
                ups = vif.parse(raw[p["offset"]:p["end"] or len(raw)])
                unp = [u for u in ups if getattr(u, "kind", None)]
                sig = tuple(sorted({u.kind for u in unp}))
                kinds[f][sig] += 1
                rows[f].append((nbytes, p["verts"], p["indices"], len(unp)))

    print("flag   n     bytes/vert   verts/unpack  unpacks  bytes")
    for f in sorted(rows):
        v = rows[f]
        bp = sum(b / max(1, w) for b, w, _, _ in v) / len(v)
        up = sum(u for _, _, _, u in v) / len(v)
        by = sum(b for b, _, _, _ in v) / len(v)
        print("  %3d %5d  %10.2f  %13.2f  %7.2f  %7.0f"
              % (f, len(v), bp, f / 16.0, up, by))

    print("\nunpack-format signature per flag value")
    for f in sorted(kinds):
        tot = sum(kinds[f].values())
        top = sorted(kinds[f].items(), key=lambda kv: -kv[1])[:3]
        print("  %3d (k=%2d) n=%-5d %s"
              % (f, f // 16, tot,
                 "  ".join("%s x%d" % ("+".join(s), n) for s, n in top)))

    # Is k the number of UNPACK streams in the packet?
    print("\nk vs unpack count")
    for f in sorted(rows):
        ups = [u for _, _, _, u in rows[f]]
        exact = sum(1 for u in ups if u == f // 16)
        print("  k=%-3d unpacks: min %d max %d  exact-match %d/%d (%.0f%%)"
              % (f // 16, min(ups), max(ups), exact, len(ups),
                 100.0 * exact / len(ups)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
