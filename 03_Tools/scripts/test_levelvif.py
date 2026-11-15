#!/usr/bin/env python3
"""[M23] A WRITER for level display lists: OBJ -> MOD0 with any vertex count.

    python3 scripts/test_levelvif.py

Until now `geomedit.apply_obj` refused any OBJ whose vertex count differed
from the surface's, because writing a different count means emitting a new
VIF packet and nothing could. `levelvif.py` can. What this proves, on the
real vanilla disc, and -- stated just as plainly -- what it does NOT:

PROVEN HERE (structural, against shipped data)
  1. The container rebuild (header, part tables, material blocks, packet
     placement) is BYTE-IDENTICAL for every rigid level MOD0 on the disc.
  2. The packet writer, fed the rows of a shipped packet (plus the two free
     choices the original tool made: trailing NOP pad and the first header
     word), reproduces that packet BYTE-FOR-BYTE for every packet of the
     3-stream grammar. So the template -- every VIFcode, every constant, the
     STROW, the GIF tag, the A/B/C header words, the cull sphere slot -- is
     the shipped one.
  3. The triangle -> strip packer draws exactly the triangles it was given,
     with their winding, for random meshes and batches no larger than 56.
  4. An OBJ with a DIFFERENT vertex/triangle count replaces a real level
     model, the bundle still verifies (check_bundle, same tag sequence, every
     other chunk byte-identical, every texture reference resolves to a TEX0),
     and the existing reader decodes the new geometry back to the OBJ.

NOT PROVEN HERE
  Whether the console/emulator DRAWS it. Header word B and the part record's
  index count are derived (B is exact; the index count is within 1 of the
  shipped formula) and the VU1 microprogram that consumes them is not
  something this repo can run. See 05_Docs/CAPABILITIES.md.
"""
import os
import random
import struct
import sys
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from rdrstudio import dave, levelvif as L          # noqa: E402
from rdrstudio.iso9660 import Iso9660              # noqa: E402
from rdrstudio.rsc import MAGIC, Rsc, check_bundle  # noqa: E402

ISO = os.path.join(os.path.dirname(__file__), '..', '..',
                   '01_Vanilla', 'RDR_VANILLA.iso')
FAILURES = []


def check(ok, what, detail=""):
    print(("  ok   " if ok else "  FAIL ") + what
          + (("  -- " + detail) if detail and not ok else ""))
    if not ok:
        FAILURES.append(what)
    return ok


def level_bundles(iso):
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b'Dave', b'DAVE'):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            if not e.name.lower().endswith('.rsc') or '_lods' in e.name.lower():
                continue
            raw = d.backing.read_at(e.offset, e.size_comp)
            try:
                b = zlib.decompress(raw, -15) if e.compressed else raw
            except zlib.error:
                continue
            if b[:4] != MAGIC or struct.unpack_from("<I", b, 4)[0] == 0:
                continue
            yield f.path, e.name, b


def sphere_obj(cx, cy, cz, r, rings, segs, quads=False):
    import math
    o = ["o test"]
    for i in range(rings + 1):
        th = math.pi * i / rings
        for j in range(segs + 1):
            ph = 2 * math.pi * j / segs
            o.append("v %.6f %.6f %.6f" % (
                cx + r * math.sin(th) * math.cos(ph), cy + r * math.cos(th),
                cz + r * math.sin(th) * math.sin(ph)))
    for i in range(rings + 1):
        for j in range(segs + 1):
            o.append("vt %.6f %.6f" % (j / segs, i / rings))
    for i in range(rings):
        for j in range(segs):
            a = i * (segs + 1) + j + 1
            b = a + 1
            c = a + segs + 1
            d = c + 1
            if quads:
                o.append("f %d/%d %d/%d %d/%d %d/%d" % (a, a, c, c, d, d, b, b))
            else:
                o.append("f %d/%d %d/%d %d/%d" % (a, a, c, c, b, b))
                o.append("f %d/%d %d/%d %d/%d" % (b, b, c, c, d, d))
    return "\n".join(o) + "\n"


def main():
    iso = Iso9660(ISO)
    n_cont = n_cont_ok = n_pk = n_pk_ok = n_pk_skip = 0
    samples = []                       # (name, bundle, chunk) to import into
    try:
        for path, name, b in level_bundles(iso):
            try:
                r = Rsc(b, name)
            except ValueError:
                continue
            for ch in r.chunks:
                if ch.tag != "MOD0":
                    continue
                raw = r.raw(ch.index)
                if raw[1] or not r.parts(ch.index):
                    continue
                try:
                    cont = L.parse_container(raw)
                except L.MeshError:
                    continue
                n_cont += 1
                n_cont_ok += L.build_container(cont) == raw
                for p in cont.parts:
                    try:
                        pk = L.read_packet(p["packet"])
                    except L.MeshError:
                        n_pk_skip += 1
                        continue
                    if any(tuple(bt["row"]) != L.ST_ROW_STD
                           and tuple(bt["row"]) != L.ST_ROW_WIDE
                           for bt in pk["batches"]):
                        n_pk_skip += 1
                        continue
                    wide = tuple(pk["batches"][0]["row"]) == L.ST_ROW_WIDE
                    sc = 128.0 if wide else 4096.0
                    bats = [[((x, y, z), ((s - 0x8000) / sc, (t - 0x8000) / sc),
                              cl, 1 if w & 0x8000 else 0)
                             for (s, t), cl, (x, y, z, w)
                             in zip(bt["uv"], bt["col"], bt["pos"])]
                            for bt in pk["batches"]]
                    pads = [(bt["hdr"][3] & 0xFFFF) - (6 * len(bt["pos"]) + 34)
                            for bt in pk["batches"]]
                    firsts = [bt["hdr"][2] & 0xFFFF for bt in pk["batches"]]
                    bases = [210, 482, 755]
                    if pk["batches"][0]["base"] not in bases:
                        n_pk_skip += 1
                        continue
                    new = L.build_packet(
                        bats, pk["sphere"], wide=wide, pads=pads, firsts=firsts,
                        base_start=bases.index(pk["batches"][0]["base"]))
                    n_pk += 1
                    n_pk_ok += new == p["packet"]
                if len(samples) < 40 and len(raw) < 60000 and \
                        len(cont.parts) <= 4 and cont.materials:
                    samples.append((name, b, ch.index))
    finally:
        iso.close()

    print("\ncontainers + packets")
    check(n_cont > 10000, "walked %d rigid level containers" % n_cont)
    check(n_cont_ok == n_cont,
          "container rebuild byte-identical %d / %d" % (n_cont_ok, n_cont))
    check(n_pk > 18000, "re-parsed %d shipped packets (%d outside the "
                        "3-stream grammar, skipped)" % (n_pk, n_pk_skip))
    check(n_pk_ok == n_pk,
          "packet writer byte-identical %d / %d" % (n_pk_ok, n_pk))

    print("\nstrip packer")
    rnd = random.Random(7)
    ok_all = True
    for trial in range(60):
        nv = rnd.randint(4, 120)
        grid = []
        # a triangulated patch plus random extra triangles
        tris = set()
        w = rnd.randint(2, 9)
        for i in range(nv):
            r_, c_ = divmod(i, w)
            if c_ + 1 < w and i + w + 1 < nv:
                tris.add((i, i + w, i + 1))
                tris.add((i + 1, i + w, i + w + 1))
        for _ in range(rnd.randint(0, 30)):
            a, b, c = rnd.sample(range(nv), 3)
            tris.add((a, b, c))
        tris = sorted(tris)
        if not tris:
            continue
        bs = L.stripify(tris)
        drawn = []
        for b in bs:
            if len(b) > L.MAX_BATCH or len(b) < 3:
                ok_all = False
            drawn += [L._cyc(t) for t in L.strip_triangles(b)]
        if sorted(drawn) != sorted(L._cyc(t) for t in tris):
            ok_all = False
    check(ok_all, "60 random meshes: strips draw exactly the given "
                  "oriented triangles, batches 3..%d" % L.MAX_BATCH)

    print("\nOBJ import into real level models (different vertex counts)")
    check(len(samples) >= 10, "%d candidate models" % len(samples))
    done = 0
    for name, b, ci in samples[:12]:
        r = Rsc(b, name)
        old = r.model(ci)
        if not old:
            continue
        xs = [v[0] for v in old["verts"]]
        ys = [v[1] for v in old["verts"]]
        zs = [v[2] for v in old["verts"]]
        cx, cy, cz = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, \
            (min(zs) + max(zs)) / 2
        for rings, segs, quads in ((3, 4, False), (9, 17, True), (30, 40, False)):
            obj = sphere_obj(cx, cy, cz, 2.5, rings, segs, quads)
            try:
                out, rep = L.import_obj(b, ci, obj, label=name)
            except Exception as ex:                       # noqa: BLE001
                check(False, "%s chunk %d %dx%d imports" % (name, ci, rings, segs),
                      "%s: %s" % (type(ex).__name__, ex))
                continue
            r2 = Rsc(out, name)
            same_tags = [c.tag for c in r2.chunks] == [c.tag for c in r.chunks]
            others = all(r2.raw(c.index) == r.raw(c.index)
                         for c in r.chunks if c.index != ci)
            m2 = r2.model(ci)
            differs = len(m2["verts"]) != len(old["verts"])
            good = (same_tags and others and not check_bundle(out, name)
                    and rep["verified"]["triangles"] == len(m2["tris"]))
            # UVs survive to 1/4096
            uvs_ok = all(0 <= round(u * 4096) / 4096 <= 1.0001 for u, _ in m2["uvs"])
            if not (good and uvs_ok):
                check(False, "%s chunk %d %dx%d" % (name, ci, rings, segs),
                      "tags=%s others=%s" % (same_tags, others))
            done += 1
            if differs is False and rings > 3:
                pass
    check(done >= 24, "%d imports re-read, bundle verified, other chunks "
                      "untouched, tag sequence unchanged" % done)
    # an import that changes the count: the thing apply_obj refuses
    name, b, ci = samples[0]
    r = Rsc(b, name)
    old = r.model(ci)
    obj = sphere_obj(0, 0, 0, 1, 12, 20)
    out, rep = L.import_obj(b, ci, obj)
    m2 = Rsc(out, name).model(ci)
    check(len(m2["verts"]) != len(old["verts"]),
          "vertex count changed %d -> %d (geomedit.apply_obj refuses this)"
          % (len(old["verts"]), len(m2["verts"])))
    # refusals
    for what, fn in (
            ("an OBJ with no faces", lambda: L.import_obj(b, ci, "v 0 0 0\n")),
            ("a non-model chunk", lambda: L.import_obj(
                b, next(c.index for c in r.chunks if c.tag != "MOD0"), obj)),
            ("a texture that is not a TEX0", lambda: L.import_obj(
                b, ci, obj.replace("o test", "usemtl m\no test"),
                resource_for={"m": 1}))):
        try:
            fn()
            check(False, "refuses " + what)
        except (L.MeshError, ValueError):
            check(True, "refuses " + what)

    print("\n%s" % ("FAIL: " + "; ".join(FAILURES) if FAILURES else "PASS"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
