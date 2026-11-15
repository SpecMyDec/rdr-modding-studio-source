"""Decode collision geometry for every level and report coverage.

Two oracles, because the first one on its own is nearly worthless:

  AABB -- the decoded vertices reproduce the header's own bounding box. This
  only ever tested the VERTEX LIST. It passed 1,501/1,501 for months while
  221,914 of the 626,338 emitted triangles -- a third of them -- were
  nonsense, because a wrong face index still points at a vertex that is
  inside the box.

  AREA -- every face records its own surface area at +12. That is a statement
  about the POLYGON, so it catches a wrong index, a wrong vertex count and a
  wrong winding, none of which the AABB can see. This is the real test; the
  AABB is kept only because it is a cheap independent check on the vertices.

Both are reported as worst-case error, not as a pass count against a loose
tolerance, so a test that starts passing by accident is visible.
"""

import math
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rdrstudio import Game                    # noqa: E402
from rdrstudio import config as cfg            # noqa: E402
from rdrstudio.level import Level, list_levels  # noqa: E402
from rdrstudio.rsc import Rsc, find_rsc_names   # noqa: E402


def aabb_matches(mesh, tol=0.02):
    """Do the decoded vertices reproduce the header's own AABB?"""
    if not mesh.verts:
        return False
    lo = [min(v[i] for v in mesh.verts) for i in range(3)]
    hi = [max(v[i] for v in mesh.verts) for i in range(3)]
    for i in range(3):
        span = max(1.0, mesh.aabb_max[i] - mesh.aabb_min[i])
        if abs(lo[i] - mesh.aabb_min[i]) > tol * span:
            return False
        if abs(hi[i] - mesh.aabb_max[i]) > tol * span:
            return False
    return True


# One tolerance, named once, so the printed count and the number quoted in
# rsc.py's docstring cannot drift apart. 0.1%, which is 33x the worst real
# residual (0.003%), so a regression has nowhere to hide.
AREA_TOL = 0.001


def _tri_area(a, b, c):
    u = [b[k] - a[k] for k in range(3)]
    w = [c[k] - a[k] for k in range(3)]
    cr = [u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2],
          u[0] * w[1] - u[1] * w[0]]
    return 0.5 * math.sqrt(sum(q * q for q in cr))


def area_check(rsc, index, tol=AREA_TOL):
    """(faces, faces whose area matches the file, worst relative error).

    Rebuilds each polygon from the same rule `Rsc.bound` uses, fans it into
    triangles exactly as `Rsc.bound` does, and compares the SUM OF THOSE
    TRIANGLES' areas with the area the chunk states for that face. A face
    read with the wrong indices cannot land on the right area by chance --
    under the old index rule only 29.6% of faces did.

    Summing the fan rather than taking Newell's planar area matters, and it
    is not a rounding preference. A quad in this format is not always flat --
    `TRAIN` chunk 424 face 3 has a corner 0.50 m out of plane -- and for
    those the file states the two-triangle area, not the planar one. Measured
    over all 313,169 faces: fan sum matches 313,169 with a worst error of
    0.0030%, Newell matches 313,164 with a worst error of 0.8221%. So the
    five "known residuals" were the oracle's arithmetic, not the data's, and
    the fan sum is also the stricter test because it checks the triangles
    that are actually handed to the viewport.
    """
    c = rsc.chunks[index]
    b = rsc.raw(index)
    nverts, nfaces = struct.unpack_from("<II", b, 0x4C)
    voff, foff = struct.unpack_from("<II", b, 0x68)
    if voff + nverts * 12 > len(b) or foff + nfaces * 32 > len(b):
        return 0, 0, float("inf")
    verts = [struct.unpack_from("<3f", b, voff + i * 12) for i in range(nverts)]
    total = ok = 0
    worst = 0.0
    for i in range(nfaces):
        o = foff + i * 32
        stated = struct.unpack_from("<f", b, o + 12)[0]
        idx = struct.unpack_from("<4H", b, o + 16)
        poly = list(idx[:3])
        if idx[3] not in (0, 0xFFFF):
            poly.append(idx[3])
        if any(v >= nverts for v in poly):
            continue
        # Same fan `Rsc.bound` emits: (0, k, k+1).
        got = sum(_tri_area(verts[poly[0]], verts[poly[k]], verts[poly[k + 1]])
                  for k in range(1, len(poly) - 1))
        rel = abs(got - stated) / max(stated, 1e-6)
        total += 1
        worst = max(worst, rel)
        if rel <= tol:
            ok += 1
    return total, ok, worst


def main():
    iso = sys.argv[1] if len(sys.argv) > 1 else cfg.VANILLA_ISO
    g = Game(iso)
    levels = [a for a in list_levels(g) if a.startswith("LVLS_SNG")]

    tot_bnd = tot_ok = tot_aabb = tot_tris = tot_verts = 0
    tot_faces = tot_area_ok = 0
    worst_area = 0.0
    print("%-14s %7s %7s %8s %9s %9s %9s %9s" %
          ("level", "BND0", "decoded", "aabb-ok", "verts", "tris",
           "faces", "area-ok"))
    print("-" * 82)

    for arc in levels:
        d = g.archive(arc)
        name = arc.rsplit("/", 1)[-1][:-4]
        rsc = None
        for n in find_rsc_names(d, name):
            try:
                r = Rsc(d.read(n), n)
            except Exception:
                continue
            if r.tally().get("BND0"):
                rsc = r
                break
        if rsc is None:
            print("%-14s %7s" % (name, "none"))
            continue

        nbnd = rsc.tally().get("BND0", 0)
        meshes = rsc.bounds()
        good = sum(1 for m in meshes.values() if aabb_matches(m))
        v = sum(len(m.verts) for m in meshes.values())
        t = sum(len(m.tris) for m in meshes.values())
        nf = na = 0
        for i in meshes:
            f, a, w = area_check(rsc, i)
            nf += f
            na += a
            worst_area = max(worst_area, w)
        tot_bnd += nbnd
        tot_ok += len(meshes)
        tot_aabb += good
        tot_verts += v
        tot_tris += t
        tot_faces += nf
        tot_area_ok += na
        print("%-14s %7d %7d %8d %9d %9d %9d %9d"
              % (name, nbnd, len(meshes), good, v, t, nf, na))
        g._cache.pop(arc.upper(), None)

    print("-" * 82)
    print("%-14s %7d %7d %8d %9d %9d %9d %9d" %
          ("TOTAL", tot_bnd, tot_ok, tot_aabb, tot_verts, tot_tris,
           tot_faces, tot_area_ok))
    print("\ndecoded  : %d / %d  (%.1f%%)" % (tot_ok, tot_bnd, 100.0 * tot_ok / max(1, tot_bnd)))
    print("aabb-ok  : %d / %d  (%.1f%%)  <- vertices reproduce the header AABB"
          % (tot_aabb, tot_ok, 100.0 * tot_aabb / max(1, tot_ok)))
    # State the tolerance next to the count. Without it the same run reads as
    # 313,169/313,169 at 1% and 313,164/313,169 at 0.1%, and both numbers have
    # been quoted as if they were the same measurement.
    print("area-ok  : %d / %d  (%.4f%%) at %.2f%% tolerance  <- each polygon "
          "reproduces the area the file states for it"
          % (tot_area_ok, tot_faces,
             100.0 * tot_area_ok / max(1, tot_faces), 100.0 * AREA_TOL))
    print("worst relative area error: %.4f%%" % (100.0 * worst_area))


if __name__ == "__main__":
    main()
