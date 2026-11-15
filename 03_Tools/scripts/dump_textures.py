"""Decode every texture in a level (or the whole disc) to PNG.

    python scripts/dump_textures.py                    all single-player levels
    python scripts/dump_textures.py LVLS_SNG/MANS.DAT  just one

Writes to 07_Research/tex/<level>/. Pure stdlib -- the PNG writer is in
rdrstudio/texture.py.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rdrstudio import Game                     # noqa: E402
from rdrstudio import config as cfg             # noqa: E402
from rdrstudio import texture as T              # noqa: E402
from rdrstudio.level import list_levels         # noqa: E402
from rdrstudio.rsc import Rsc, find_rsc_names   # noqa: E402

OUT = os.path.join(cfg.ROOT, "07_Research", "tex")


def do_level(g, arc):
    d = g.archive(arc)
    name = arc.rsplit("/", 1)[-1][:-4]
    rsc = None
    for n in find_rsc_names(d, name):
        try:
            r = Rsc(d.read(n), n)
        except Exception:
            continue
        if r.tally().get("MIP0"):
            rsc = r
            break
    if rsc is None:
        return 0, 0

    dest = os.path.join(OUT, name)
    os.makedirs(dest, exist_ok=True)
    pairs = T.textures_in(rsc)
    ok = 0
    for i, (mip, pal) in enumerate(pairs):
        try:
            res = T.decode(rsc.raw(mip.index), T.parse_palette(rsc.raw(pal.index)))
        except Exception:
            res = None
        if not res:
            continue
        w, h, rgba = res
        T.write_png(os.path.join(dest, "%03d_%dx%d.png" % (i, w, h)), w, h, rgba)
        ok += 1
    return ok, len(pairs)


def main():
    g = Game(sys.argv[2] if len(sys.argv) > 2 else cfg.VANILLA_ISO)
    targets = ([sys.argv[1]] if len(sys.argv) > 1
               else [a for a in list_levels(g) if a.startswith("LVLS_SNG")])
    tot_ok = tot = 0
    for arc in targets:
        ok, n = do_level(g, arc)
        tot_ok += ok
        tot += n
        print("  %-24s %3d / %-3d textures" % (arc.rsplit("/", 1)[-1], ok, n))
        g._cache.pop(arc.upper(), None)
    print("\n%d / %d decoded -> %s" % (tot_ok, tot, OUT))


if __name__ == "__main__":
    main()
