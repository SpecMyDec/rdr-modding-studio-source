#!/usr/bin/env python3
"""A custom character mesh, written and read back through the OTHER reader.

    python3 scripts/test_skinwrite.py        (reads 01_Vanilla, writes nothing)

What is checked (offline; NOTHING here proves the console draws it):
  * a shipped character part, exported to an OBJ in bind-pose world space and
    imported again, re-reads (vif.py, an independent code path from the writer)
    with the same triangles, the same bone per vertex and positions within the
    quantiser step;
  * the secondary (blend) influences survive: every vertex keeps its bone list;
  * a half-scale copy and a brand-new box (different vertex count) import, fit,
    verify as a bundle (check_bundle: same tag sequence, other chunks
    byte-identical) and bind to the bone nearest each vertex;
  * refusals: rig mismatch, non-model chunk, empty OBJ, UV overflow.
"""
import math
import os
import sys
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from rdrstudio import dave, skeleton as SK, skinwrite as W   # noqa: E402
from rdrstudio.iso9660 import Iso9660                        # noqa: E402
from rdrstudio.rsc import MAGIC, Rsc, check_bundle           # noqa: E402

ISO = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..',
                   '01_Vanilla', 'RDR_VANILLA.iso')
FAILURES = []


def check(ok, what, detail=""):
    print(("  ok   " if ok else "  FAIL ") + what
          + (("  -- " + str(detail)) if detail and not ok else ""))
    if not ok:
        FAILURES.append(what)
    return ok


def load(iso, stem):
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b'Dave', b'DAVE'):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        names = {e.name.lower(): e for e in d.entries}
        e = names.get(stem + '_lods.rsc')
        sk = names.get(stem + '.skel')
        if not e or not sk:
            continue
        raw = d.backing.read_at(e.offset, e.size_comp)
        b = zlib.decompress(raw, -15) if e.compressed else raw
        if b[:4] != MAGIC:
            continue
        bones = SK.parse(d.read(sk))[2]
        w = SK.world_positions(bones)
        return b, [w[x.name] for x in bones], e.name
    raise SystemExit("no %s on the disc" % stem)


def obj_of(m, bw, scale=1.0):
    out = []
    for v, b in zip(m["verts"], m["bones"]):
        p = bw[b]
        out.append("v %.6f %.6f %.6f" % tuple((v[k] + p[k]) * scale for k in range(3)))
    for u, t in m["uvs"]:
        out.append("vt %.6f %.6f" % (u, 1.0 - t))
    out.append("usemtl body")
    for a, b, c in m["tris"]:
        out.append("f %d/%d %d/%d %d/%d" % (a + 1, a + 1, b + 1, b + 1, c + 1, c + 1))
    return "\n".join(out) + "\n"


def tri_list(m, bw):
    out = []
    for t in m["tris"]:
        pts = [tuple(m["verts"][i][k] + bw[m["bones"][i]][k] for k in range(3)) for i in t]
        bs = tuple(sorted((m["bones"][i], round(m["weights"][i][0][1], 2)
                           if m["weights"][i] else 1.0) for i in t))
        out.append((tuple(sum(p[k] for p in pts) / 3 for k in range(3)), bs))
    return out


def match(a, b, tol):
    """Greedy centroid match, grid-bucketed. -> unmatched count."""
    cell = 0.05
    grid = {}
    for i, (c, bs) in enumerate(b):
        grid.setdefault(tuple(int(math.floor(x / cell)) for x in c), []).append(i)
    used = set()
    miss = 0
    for c, bs in a:
        k = tuple(int(math.floor(x / cell)) for x in c)
        hit = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for i in grid.get((k[0] + dx, k[1] + dy, k[2] + dz), ()):
                        if i in used:
                            continue
                        if b[i][1] == bs and max(abs(c[j] - b[i][0][j]) for j in range(3)) < tol:
                            hit = i
                            break
                    if hit is not None:
                        break
                if hit is not None:
                    break
            if hit is not None:
                break
        if hit is None:
            miss += 1
        else:
            used.add(hit)
    return miss


def box_obj(c, h):
    x, y, z = c
    P = [(x - h, y - h, z - h), (x + h, y - h, z - h), (x + h, y + h, z - h),
         (x - h, y + h, z - h), (x - h, y - h, z + h), (x + h, y - h, z + h),
         (x + h, y + h, z + h), (x - h, y + h, z + h)]
    F = [(0, 1, 2, 3), (5, 4, 7, 6), (4, 0, 3, 7), (1, 5, 6, 2), (3, 2, 6, 7), (4, 5, 1, 0)]
    out = ["v %.5f %.5f %.5f" % p for p in P] + ["vt 0 0", "vt 1 0", "vt 1 1", "vt 0 1"]
    for f in F:
        out.append("f " + " ".join("%d/%d" % (i + 1, k + 1) for k, i in enumerate(f)))
    return "\n".join(out) + "\n"


def main():
    iso = Iso9660(ISO)
    try:
        b, bw, name = load(iso, "srd")
    finally:
        iso.close()
    r = Rsc(b, name)
    chunk = 4
    m = r.model(chunk)
    print("srd chunk %d: %d verts, %d tris, %d bones in the skeleton"
          % (chunk, len(m["verts"]), len(m["tris"]), len(bw)))
    multi = sum(1 for w in m["weights"] if len(w) > 1)
    print("  %d vertices carry blend influences" % multi)

    # 1. identity-ish round trip
    out, rep = W.import_obj(b, chunk, obj_of(m, bw), bw, name)
    m2 = Rsc(out, name).model(chunk)
    check(m2 is not None and m2.get("skinned"), "re-reads as a skinned model")
    check(len(m2["tris"]) == len(m["tris"]),
          "same triangle count (%d)" % len(m["tris"]), len(m2["tris"]))
    miss = match(tri_list(m, bw), tri_list(m2, bw), 0.0035)
    check(miss == 0, "every triangle re-reads with the same bones and "
          "positions to the quantiser step", "%d unmatched" % miss)
    multi2 = sum(1 for w in m2["weights"] if len(w) > 1)
    check(multi2 > 0 and abs(multi2 - multi) <= multi * 0.5 + 5,
          "blend influences survive (%d -> %d vertices)" % (multi, multi2))
    check(not check_bundle(out, name), "bundle still verifies (check_bundle)")
    r1, r2 = Rsc(b, name), Rsc(out, name)
    others = all(r1.raw(i) == r2.raw(i) for i in range(len(r1.chunks)) if i != chunk)
    check(others and [c.tag for c in r1.chunks] == [c.tag for c in r2.chunks],
          "every other chunk byte-identical, same tag sequence")
    print("  report:", {k: rep[k] for k in ("parts", "batches", "vertices",
          "secondary_influences", "old_bytes", "new_bytes", "skin_distance_max")})

    # 2. half-scale copy
    out, rep = W.import_obj(b, chunk, obj_of(m, bw, 0.5), bw, name)
    m3 = Rsc(out, name).model(chunk)
    check(len(m3["tris"]) == len(m["tris"]) and not check_bundle(out, name),
          "half-scale copy imports and verifies")

    # 3. brand-new box (12 triangles, 8 corners x uv) near a real bone
    j = m["bones"][0]
    c = bw[j]
    out, rep = W.import_obj(b, chunk, box_obj((c[0], c[1] + 0.05, c[2]), 0.08), bw, name)
    m4 = Rsc(out, name).model(chunk)
    check(m4 and len(m4["tris"]) == 12, "a new 12-triangle box replaces the model",
          m4 and len(m4["tris"]))
    check(not check_bundle(out, name), "box bundle verifies")
    near = set(m4["bones"])
    check(len(near) >= 1 and all(0 <= x < len(bw) for x in near),
          "box vertices are bound to real bones %s" % sorted(near))

    # 4. refusals
    def refuses(what, f, word):
        try:
            f()
        except W.SkinError as ex:
            check(word in str(ex), what + " -> " + str(ex)[:70], str(ex))
        except Exception as ex:                          # noqa: BLE001
            check(False, what, repr(ex))
        else:
            check(False, what + " was NOT refused")

    refuses("rig mismatch", lambda: W.import_obj(b, chunk, obj_of(m, bw), bw[:-1], name), "rig mismatch")
    tex = next(i for i, ch in enumerate(r.chunks) if ch.tag != "MOD0")
    refuses("non-model chunk", lambda: W.import_obj(b, tex, obj_of(m, bw), bw, name), "not a model")
    refuses("empty OBJ", lambda: W.import_obj(b, chunk, "v 0 0 0\n", bw, name), "no faces")
    bad = "v 0 0 0\nv 1 0 0\nv 0 1 0\nvt 50 0\nvt 0 0\nvt 0 1\nf 1/1 2/2 3/3\n"
    refuses("UV overflow", lambda: W.import_obj(b, chunk, bad, bw, name), "UV")
    print("\n%s" % ("FAIL: " + "; ".join(FAILURES) if FAILURES else "PASS"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
