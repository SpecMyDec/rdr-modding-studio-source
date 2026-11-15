"""The whole spatial index of a collision shell (BND0): read it, rebuild it.

`boundmove` moves a shell rigidly by transforming the boxes it finds. It only
ever saw the FIRST block of the index. The index is an OCTREE:

    +0x7C -> root node (32 bytes: box min[3] max[3], child u32, faces u32)
    child -> a block of EIGHT nodes (bit0 = +x half, bit1 = +y, bit2 = +z)
    faces -> a leaf list: tight box (6 floats = the union of the listed faces'
             own boxes), u16 count, count x u16 face numbers ascending,
             padded to 4 bytes with 0xCD
    layout: depth first -- a block of eight, then for each of the eight in
            order either its leaf list or its own block (and everything under
            it) before the next sibling.
    root box = a CUBE: centre of the header AABB, side = its longest edge.
    a node is split when more than 15 faces touch it, it is above depth 8
    and it is wider than 0.5 m.
    +0x40 = 0.035 x the AABB's shortest edge, +0x44 = 0.3 x that.
    +0x80 -> one bit per face (4 bytes per 32 faces), then pad to 16.

Every line above is measured over every BND0 on the disc by
`research/probe_bnd_octree.py`; the numbers are in 05_Docs/DEEPDIVE-2026-11-11.md.

What this module is for: moving SOME of a shell's vertices (one object inside
a merged `bound_grp` shell) or all of them, and writing back a shell whose
index is true for where the triangles now are. Vertex and face counts and
order never change, so the per-face tables (materials at +0x70, bits at +0x80)
carry over untouched.
"""
import math
import struct

MAGIC = 0x0FFFFF00
LEAF_MAX = 15
DEPTH_MAX = 8
SPLIT_MIN = 0.5      # a node this narrow or narrower is never split
NODE = 32


class TreeError(ValueError):
    pass


def _poly(idx4):
    p = list(idx4[:3])
    if idx4[3] not in (0, 0xFFFF):
        p.append(idx4[3])
    return p


def parse(raw):
    """-> dict(verts, faces, root, nodes, lists, offsets...). Raises TreeError."""
    if len(raw) < 0x90 or struct.unpack_from("<I", raw, 0)[0] != MAGIC:
        raise TreeError("not a collision shell")
    nv, nf = struct.unpack_from("<2I", raw, 0x4C)
    voff, foff, moff = struct.unpack_from("<3I", raw, 0x68)
    noff, toff = struct.unpack_from("<2I", raw, 0x7C)
    if not (voff + 12 * nv <= foff and foff + 32 * nf <= moff <= noff
            and noff + NODE <= toff <= len(raw)):
        raise TreeError("offsets out of order")
    verts = [struct.unpack_from("<3f", raw, voff + 12 * i) for i in range(nv)]
    faces = [_poly(struct.unpack_from("<4H", raw, foff + 32 * i + 16))
             for i in range(nf)]
    for f in faces:
        if any(i >= nv for i in f):
            raise TreeError("face names a vertex past the end")
    count = [0]

    def rd(o, depth):
        if o + NODE > toff or depth > 16:
            raise TreeError("index runs out of the chunk")
        count[0] += 1
        box = struct.unpack_from("<6f", raw, o)
        ch, fp = struct.unpack_from("<2I", raw, o + 24)
        n = dict(o=o, box=box, depth=depth, kids=[], faces=None, tight=None)
        if fp:
            if fp + 26 > toff:
                raise TreeError("leaf list runs out of the chunk")
            c = struct.unpack_from("<H", raw, fp + 24)[0]
            n["tight"] = struct.unpack_from("<6f", raw, fp)
            n["faces"] = list(struct.unpack_from("<%dH" % c, raw, fp + 26))
        if ch:
            n["kids"] = [rd(ch + NODE * k, depth + 1) for k in range(8)]
        return n

    root = rd(noff, 0)
    return dict(nv=nv, nf=nf, verts=verts, faces=faces, root=root,
                node_count=count[0], voff=voff, foff=foff, moff=moff,
                noff=noff, toff=toff)


# ------------------------------------------------------------ geometry tests

def _fbox(vs):
    return ([min(v[i] for v in vs) for i in range(3)],
            [max(v[i] for v in vs) for i in range(3)])


def _tri_box(c, h, t):
    """Triangle `t` (3 points) against the box centre c, half-size h (SAT)."""
    v = [[p[i] - c[i] for i in range(3)] for p in t]
    for i in range(3):
        if min(v[0][i], v[1][i], v[2][i]) > h[i] or \
           max(v[0][i], v[1][i], v[2][i]) < -h[i]:
            return False
    e = [[v[(k + 1) % 3][i] - v[k][i] for i in range(3)] for k in range(3)]
    for k in range(3):
        ex, ey, ez = e[k]
        for ax in ((0.0, -ez, ey), (ez, 0.0, -ex), (-ey, ex, 0.0)):
            p = [ax[0] * q[0] + ax[1] * q[1] + ax[2] * q[2] for q in v]
            r = h[0] * abs(ax[0]) + h[1] * abs(ax[1]) + h[2] * abs(ax[2])
            if min(p) > r or max(p) < -r:
                return False
    n = (e[0][1] * e[1][2] - e[0][2] * e[1][1],
         e[0][2] * e[1][0] - e[0][0] * e[1][2],
         e[0][0] * e[1][1] - e[0][1] * e[1][0])
    d = n[0] * v[0][0] + n[1] * v[0][1] + n[2] * v[0][2]
    r = h[0] * abs(n[0]) + h[1] * abs(n[1]) + h[2] * abs(n[2])
    return abs(d) <= r


def _touches(lo, hi, pts, eps):
    c = [(lo[i] + hi[i]) / 2.0 for i in range(3)]
    h = [(hi[i] - lo[i]) / 2.0 + eps for i in range(3)]
    for k in range(1, len(pts) - 1):
        if _tri_box(c, h, (pts[0], pts[k], pts[k + 1])):
            return True
    return False


def _f32(x):
    return struct.unpack("<f", struct.pack("<f", x))[0]


def build_tree(verts, faces, lo, hi):
    """-> root node dict for the shell whose header AABB is lo..hi."""
    # The cube is as wide as the VERTICES' longest edge (the header box is
    # that plus 1e-4 a side) and sits on the header box's centre. Single
    # precision at every step, as the shipped builder did it.
    vlo, vhi = _fbox(verts)
    half = _f32(max(_f32(vhi[i] - vlo[i]) for i in range(3)) * 0.5)
    cen = [_f32(_f32(hi[i] + lo[i]) * 0.5) for i in range(3)]
    rlo = [_f32(cen[i] - half) for i in range(3)]
    rhi = [_f32(cen[i] + half) for i in range(3)]
    side = 2.0 * half
    eps = max(1e-5, side * 1e-6)
    pts = [[verts[i] for i in f] for f in faces]
    boxes = [_fbox(p) for p in pts]

    def make(blo, bhi, cand, depth):
        mine = []
        for f in cand:
            flo, fhi = boxes[f]
            if any(flo[i] > bhi[i] + eps or fhi[i] < blo[i] - eps for i in range(3)):
                continue
            if _touches(blo, bhi, pts[f], eps):
                mine.append(f)
        n = dict(box=tuple(blo) + tuple(bhi), depth=depth, kids=[], faces=None,
                 tight=None)
        if len(mine) > LEAF_MAX and depth < DEPTH_MAX \
                and bhi[0] - blo[0] > SPLIT_MIN:
            mid = [_f32(_f32(blo[i] + bhi[i]) * 0.5) for i in range(3)]
            for k in range(8):
                clo = [mid[i] if (k >> i) & 1 else blo[i] for i in range(3)]
                chi = [bhi[i] if (k >> i) & 1 else mid[i] for i in range(3)]
                n["kids"].append(make(clo, chi, mine, depth + 1))
        elif mine:
            n["faces"] = mine
            tl = [min(boxes[f][0][i] for f in mine) for i in range(3)]
            th = [max(boxes[f][1][i] for f in mine) for i in range(3)]
            n["tight"] = tuple(tl) + tuple(th)
        return n

    return make(rlo, rhi, list(range(len(faces))), 0)


def _emit_tree(root, base):
    """Serialise depth-first. -> bytes that start at file offset `base`."""
    out = bytearray(NODE)

    def put_node(at, n, ch, fp):
        struct.pack_into("<6f2I", out, at, *(list(n["box"]) + [ch, fp]))

    def put_list(n):
        fp = base + len(out)
        b = struct.pack("<6fH", *(list(n["tight"]) + [len(n["faces"])]))
        b += struct.pack("<%dH" % len(n["faces"]), *n["faces"])
        if len(b) % 4:
            b += b"\xcd" * (4 - len(b) % 4)
        out.extend(b)
        return fp

    def body(n, at):
        if n["kids"]:
            blk = len(out)
            out.extend(bytes(NODE * 8))
            put_node(at, n, base + blk, 0)
            for k, kid in enumerate(n["kids"]):
                body(kid, blk + NODE * k)
        elif n["faces"]:
            put_node(at, n, 0, put_list(n))
        else:
            put_node(at, n, 0, 0)

    body(root, 0)
    return bytes(out)


def _normal_area(pts):
    nx = ny = nz = 0.0
    area = 0.0
    a = pts[0]
    for k in range(1, len(pts) - 1):
        b, c = pts[k], pts[k + 1]
        u = [b[i] - a[i] for i in range(3)]
        w = [c[i] - a[i] for i in range(3)]
        cx = (u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2],
              u[0] * w[1] - u[1] * w[0])
        nx += cx[0]; ny += cx[1]; nz += cx[2]
        area += 0.5 * math.sqrt(cx[0] ** 2 + cx[1] ** 2 + cx[2] ** 2)
    ln = math.sqrt(nx * nx + ny * ny + nz * nz)
    if ln < 1e-12:
        return None, area
    return (nx / ln, ny / ln, nz / ln), area


def rebuild(raw, new_verts=None, keep_normals=False):
    """The same shell with `new_verts` ({index: (x, y, z)} or a full list) and
    an index rebuilt for them. Vertex/face counts and order are unchanged.

    The header box, centre, radius, origin distance and margins are derived
    from the new vertices only when a vertex actually moved; faces touched by
    a moved vertex get their normal and area recomputed (`keep_normals` keeps
    the stored normal, for a pure translation). With nothing moved, everything
    before the index is returned byte for byte.
    """
    p = parse(raw)
    verts = list(p["verts"])
    moved = set()
    if new_verts is not None:
        items = (new_verts.items() if isinstance(new_verts, dict)
                 else enumerate(new_verts))
        for i, v in items:
            v = tuple(_f32(float(c)) for c in v)
            if v != verts[i]:
                verts[i] = v
                moved.add(i)
    out = bytearray(raw[:p["noff"]])
    lo = list(struct.unpack_from("<3f", raw, 0x08))
    hi = list(struct.unpack_from("<3f", raw, 0x14))
    if moved:
        for i in moved:
            struct.pack_into("<3f", out, p["voff"] + 12 * i, *verts[i])
        nlo, nhi = _fbox(verts)
        olo, ohi = _fbox(p["verts"])
        # keep whatever margin the stored box had over its own vertices
        lo = [nlo[i] + (lo[i] - olo[i]) for i in range(3)]
        hi = [nhi[i] + (hi[i] - ohi[i]) for i in range(3)]
        struct.pack_into("<3f", out, 0x08, *lo)
        struct.pack_into("<3f", out, 0x14, *hi)
        lo = list(struct.unpack_from("<3f", out, 0x08))
        hi = list(struct.unpack_from("<3f", out, 0x14))
        struct.pack_into("<3f", out, 0x20, *[(lo[i] + hi[i]) / 2.0 for i in range(3)])
        struct.pack_into("<f", out, 0x38, 0.5 * math.sqrt(
            sum((hi[i] - lo[i]) ** 2 for i in range(3))))
        struct.pack_into("<f", out, 0x3C, max(
            math.sqrt(sum(c * c for c in (x, y, z)))
            for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])))
        m = 0.035 * min(hi[i] - lo[i] for i in range(3))
        struct.pack_into("<2f", out, 0x40, m, 0.3 * m)
        for fi, f in enumerate(p["faces"]):
            if not moved.intersection(f):
                continue
            n, area = _normal_area([verts[i] for i in f])
            o = p["foff"] + 32 * fi
            if n is not None and not keep_normals:
                struct.pack_into("<3f", out, o, *n)
            struct.pack_into("<f", out, o + 12, area)
    tree = build_tree(verts, p["faces"], lo, hi)
    tb = _emit_tree(tree, p["noff"])
    out.extend(tb)
    struct.pack_into("<I", out, 0x80, len(out))
    bits = raw[p["toff"]:p["toff"] + 4 * ((p["nf"] + 31) // 32)]
    out.extend(bits)
    if len(out) % 16:
        out.extend(bytes(16 - len(out) % 16))
    return bytes(out)


def audit(raw):
    """Is this shell's index true for its own triangles? -> dict of counts.

    `missing` = (leaf, face) pairs where a face reaches into a leaf's box but
    the leaf does not list it: a place the engine would not test that face.
    That is the number that must be 0 after any edit."""
    p = parse(raw)
    V = p["verts"]
    pts = [[V[i] for i in f] for f in p["faces"]]
    boxes = [_fbox(q) for q in pts]
    res = dict(nodes=p["node_count"], leaves=0, missing=0, extra=0,
               tight_bad=0, faces_listed=0)
    listed = set()

    def walk(n, cand):
        blo, bhi = n["box"][:3], n["box"][3:]
        shrink = 1e-4 * max(1.0, bhi[0] - blo[0])
        mine_strict, mine_loose = [], []
        for f in cand:
            flo, fhi = boxes[f]
            if any(flo[i] > bhi[i] + shrink or fhi[i] < blo[i] - shrink for i in range(3)):
                continue
            if _touches(blo, bhi, pts[f], shrink):
                mine_loose.append(f)
                if _touches(blo, bhi, pts[f], -shrink):
                    mine_strict.append(f)
        if n["kids"]:
            for k in n["kids"]:
                walk(k, mine_loose)
            return
        res["leaves"] += 1
        have = set(n["faces"] or [])
        listed.update(have)
        res["missing"] += len(set(mine_strict) - have)
        res["extra"] += len(have - set(mine_loose))
        if have:
            tl = [min(boxes[f][0][i] for f in have) for i in range(3)]
            th = [max(boxes[f][1][i] for f in have) for i in range(3)]
            t = n["tight"]
            if any(abs(t[i] - tl[i]) > 1e-3 or abs(t[3 + i] - th[i]) > 1e-3 for i in range(3)):
                res["tight_bad"] += 1

    walk(p["root"], list(range(p["nf"])))
    res["faces_listed"] = len(listed)
    res["faces"] = p["nf"]
    return res
