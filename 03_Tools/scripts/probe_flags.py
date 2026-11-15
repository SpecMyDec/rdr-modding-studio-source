"""What is the second u32 in a container MOD0's part record?

The outer table is `count` x (u32 resource, u32 flags). `resource` turned out
to be the texture binding -- a one-based TEX0 chunk index. `flags` is still
unread, and it is the last unknown field in the level render path.

Observed values are all multiples of 16: 32, 48, 64, 80, 96, 112, 128, 144,
160, 192. That regularity has to mean something. Candidates:

  * a render-state bitfield -- alpha blend, two-sided, no-cull, fade
  * a byte size (a VU1 buffer, a vertex stride)
  * a sort key / draw layer

The level artists labelled their own zones: `zone_alpha_01_adobeGlass_01`,
`zone_fade_adobePostsNS_01`, `zone_bldgFrontNS_01`. If `flags` is render
state, it should track those prefixes. That is the test.
"""
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rdrstudio import Game, rsc, scene  # noqa: E402
from rdrstudio.config import VANILLA_ISO  # noqa: E402
from rdrstudio.level import Level  # noqa: E402

LEVELS = [("LVLS_SNG/TOMB.DAT", "lvl_tomb.rsc"),
          ("LVLS_SNG/PERFNEW.DAT", "lvl_perfnew.rsc"),
          ("LVLS_SNG/SHER70.DAT", "lvl_sher70.rsc"),
          ("LVLS_SNG/MANS.DAT", "lvl_mans.rsc"),
          ("LVLS_SNG/WOODS.DAT", "lvl_woods.rsc")]


def main():
    game = Game(VANILLA_ISO)

    allvals = Counter()
    by_tag = defaultdict(Counter)     # name marker -> flag histogram
    by_bit = defaultdict(Counter)     # bit -> marker histogram
    vsize = defaultdict(list)         # flag -> vertex counts

    for arc, area in LEVELS:
        try:
            lvl = Level(game, arc, area=area)
            r = lvl._load_rsc()
        except Exception as e:
            print("%-22s ERR %s" % (area, e))
            continue
        if r is None:
            continue
        names = {}
        for n in lvl.nodes:
            ci = scene.chunk_of(n)
            if ci is not None:
                names.setdefault(ci, n.name)

        for c in r.chunks:
            if c.tag != "MOD0":
                continue
            parts = r.parts(c.index)
            if not parts:
                continue
            nm = (names.get(c.index) or "").lower()
            marker = ("alpha" if "alpha" in nm else
                      "fade" if "fade" in nm else
                      "water" if "water" in nm else
                      "veg" if ("veg" in nm or "tree" in nm or "bush" in nm)
                      else "opaque")
            for rec in parts:
                f = rec["flags"] if "flags" in rec else None
                if f is None:
                    continue
                allvals[f] += 1
                by_tag[marker][f] += 1
                vsize[f].append(rec["verts"])
                for b in range(16):
                    if f & (1 << b):
                        by_bit[b][marker] += 1

    if not allvals:
        print("no flags captured -- parts() does not expose the field yet")
        return 1

    print("flag value histogram (all levels)")
    for v, n in sorted(allvals.items()):
        vs = vsize[v]
        print("  0x%04X %6d  (%5d)  verts avg %6.1f  max %d"
              % (v, v, n, sum(vs) / len(vs), max(vs)))

    print("\nflag by artist's own name marker")
    markers = sorted(by_tag)
    print("        " + "".join("%9s" % m for m in markers))
    for v in sorted(allvals):
        row = "".join("%9d" % by_tag[m].get(v, 0) for m in markers)
        print("  0x%04X%s" % (v, row))

    print("\nwhich markers set each bit")
    for b in sorted(by_bit):
        tot = sum(by_bit[b].values())
        top = ", ".join("%s=%d" % kv for kv in by_bit[b].most_common(5))
        print("  bit %-2d (0x%04X)  %6d  %s" % (b, 1 << b, tot, top))
    return 0


if __name__ == "__main__":
    sys.exit(main())
