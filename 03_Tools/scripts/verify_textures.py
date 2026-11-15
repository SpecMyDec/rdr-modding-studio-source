"""Texture and render-geometry decode coverage across every level.

Offline (no Studio needed). Fails if coverage regresses below the levels
reached when these formats were decoded, so a future change to the VIF or
texture path cannot quietly break them.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rdrstudio import Game                      # noqa: E402
from rdrstudio import config as cfg              # noqa: E402
from rdrstudio import texture as T               # noqa: E402
from rdrstudio.level import list_levels          # noqa: E402
from rdrstudio.rsc import Rsc, find_rsc_names    # noqa: E402

MIN_TEXTURE_RATE = 0.99
MIN_RENDER_RATE = 0.97
MIN_UV_RATE = 0.95


def main():
    iso = sys.argv[1] if len(sys.argv) > 1 else cfg.VANILLA_ISO
    g = Game(iso)
    levels = [a for a in list_levels(g) if a.startswith("LVLS_SNG")]

    tex_ok = tex_n = 0
    mod_ok = mod_n = 0
    uv_ok = uv_n = 0
    tris = 0

    print("%-14s %9s %14s %9s" % ("level", "textures", "render chunks", "tris"))
    print("-" * 52)
    for arc in levels:
        d = g.archive(arc)
        name = arc.rsplit("/", 1)[-1][:-4]
        rsc = None
        for n in find_rsc_names(d, name):
            try:
                r = Rsc(d.read(n), n)
            except Exception:
                continue
            if r.tally().get("MIP0") or r.tally().get("MOD0"):
                rsc = r
                break
        if rsc is None:
            continue

        table = rsc.texture_table()
        t_ok = 0
        for _, info, mip, pal in table:
            try:
                if T.decode(rsc.raw(mip.index), T.parse_palette(rsc.raw(pal.index))):
                    t_ok += 1
            except Exception:
                pass

        m_ok = m_n = 0
        l_tris = 0
        for c in rsc.chunks:
            if c.tag != "MOD0":
                continue
            m_n += 1
            try:
                mesh = rsc.model(c.index)
            except Exception:
                mesh = None
            if mesh and mesh["tris"]:
                m_ok += 1
                l_tris += len(mesh["tris"])
                uv_n += 1
                if len(mesh.get("uvs") or []) == len(mesh["verts"]):
                    uv_ok += 1

        tex_ok += t_ok
        tex_n += len(table)
        mod_ok += m_ok
        mod_n += m_n
        tris += l_tris
        print("%-14s %4d/%-4d %7d/%-6d %9d" % (name, t_ok, len(table), m_ok, m_n, l_tris))
        g._cache.pop(arc.upper(), None)

    print("-" * 52)
    trate = tex_ok / max(1, tex_n)
    rrate = mod_ok / max(1, mod_n)
    urate = uv_ok / max(1, uv_n)
    print("textures        : %d / %d  (%.1f%%)" % (tex_ok, tex_n, 100 * trate))
    print("render chunks   : %d / %d  (%.1f%%)" % (mod_ok, mod_n, 100 * rrate))
    print("meshes with UVs : %d / %d  (%.1f%%)" % (uv_ok, uv_n, 100 * urate))
    print("render triangles: %d" % tris)

    bad = []
    if trate < MIN_TEXTURE_RATE:
        bad.append("textures %.1f%% < %.0f%%" % (100 * trate, 100 * MIN_TEXTURE_RATE))
    if rrate < MIN_RENDER_RATE:
        bad.append("render %.1f%% < %.0f%%" % (100 * rrate, 100 * MIN_RENDER_RATE))
    if urate < MIN_UV_RATE:
        bad.append("UVs %.1f%% < %.0f%%" % (100 * urate, 100 * MIN_UV_RATE))
    if bad:
        print("\nREGRESSION: " + "; ".join(bad))
        return 1
    print("\nall coverage thresholds met")
    return 0


if __name__ == "__main__":
    sys.exit(main())
