"""Moving a collision shell so it still collides where you put it.

M16. a tester: *"you can remove collision from a model by moving its shell, but
the shell cannot be moved elsewhere."* Both halves of that are true today, and
the second is a consequence of the first rather than a separate problem.

**Why moving a shell removed collision.** A BND0 records where it is in four
places, and `geomedit.translate_bound` updates two of them:

    verts at +0x68        the triangles                     moved
    +0x08 / +0x14         AABB min / max                    moved
    +0x20                 the AABB centre, stored again     LEFT BEHIND
    +0x3C                 distance from the WORLD ORIGIN
                          to the farthest AABB corner       LEFT BEHIND
    segment B             a node tree of world-space boxes  LEFT BEHIND

The node tree is the one that matters. It is the shell's own spatial index, in
world space, and leaving it behind means the broadphase still has boxes at the
old location pointing at triangles that are no longer inside them, and nothing
at all at the new one. The shell then collides in neither place -- which is
exactly what "moving it removes collision" describes.

None of the above is inferred from the code. `+0x20`, `+0x38` and `+0x3C` were
each confirmed against every BND0 on the disc (2,870 of 2,870). The tree was
found by asking the GAME: the disc places the same shell at different positions
in several levels, so grouping every BND0 by a translation-invariant signature
of its vertices turns up relocated duplicates, and a float that differs by
exactly the placement delta is a world coordinate. Over 8 such pairs, 1,751
floats in the tail move and **0 in the face records do** -- faces carry a normal
and an area, no position, which is why they are not touched here either.

**The node layout**, verified on all 2,870 chunks (28,766 nodes, no failures):

    +0x7C  -> first node
    node   = 32 bytes: AABB min[3], max[3] (24 bytes)
                       child  (u32, absolute byte offset, 0 = leaf)
                       faces  (u32, absolute byte offset, 0 = none)
    the array ends at the first face list any node points at.

Node 0 is the root; its box covers the chunk's own AABB on 2,870 of 2,870 (it
is padded outward, which is why containment must be tested against the ROOT and
not against the AABB -- testing against the AABB rejects 2,085 of them). Every
node's box lies inside the root's.

**What is deliberately NOT recomputed.** Boxes are transformed, never rebuilt
from the vertices they cover. The stored boxes are wider than their contents --
the root is padded, and `geomedit` already records the same thing about the
header AABB -- so rebuilding them would quietly change the volumes the engine
culls and traverses against. Transforming preserves whatever margin the
original author chose. For a translation that is exact; for a rotation it is
conservative in the right direction (the box can only grow).

`+0x38` (the radius) is half the AABB's diagonal, so it is translation
invariant and is scaled rather than re-derived. `+0x3C` is the only field that
is a function of the world origin, so it cannot be transformed -- but it is
adjusted by how much it CHANGED rather than replaced by a fresh computation.
The disc's stored values agree with the derivation to about 2e-4 and not
exactly, so recomputing would edit every shell merely passed through. Move
nothing and the change is zero and the author's bytes survive: an identity
move here is byte-exact, which is the property that lets this be run over a
whole disc safely.
"""

import math
import struct

BND_MIN, BND_MAX, BND_CENTRE = 0x08, 0x14, 0x20
BND_RADIUS, BND_ORIGIN_DIST = 0x38, 0x3C
BND_COUNTS, BND_VOFF = 0x4C, 0x68
BND_NODES = 0x7C

NODE = 32
NODE_CHILD, NODE_FACES = 24, 28


class NotEditable(Exception):
    pass


def _f3(raw, off):
    return struct.unpack_from("<3f", raw, off)


def _corners(lo, hi):
    return [(lo[0] if a else hi[0], lo[1] if b else hi[1],
             lo[2] if c else hi[2])
            for a in (0, 1) for b in (0, 1) for c in (0, 1)]


def _origin_dist(lo, hi):
    """Distance from the world origin to the box's farthest corner."""
    return max(math.sqrt(sum(c[i] ** 2 for i in range(3)))
               for c in _corners(lo, hi))


def nodes(raw):
    """Byte offsets of every node in the shell's spatial index.

    The array runs from `+0x7C` to the first face list a node points at. The
    count is not stored anywhere -- this is how the engine's own pointers
    delimit it, and it parses all 2,870 BND0 chunks on the disc.
    """
    if len(raw) < 0x90:
        raise NotEditable("too short to be a BND0")
    base = struct.unpack_from("<I", raw, BND_NODES)[0]
    if not (0 < base < len(raw)) or base + NODE > len(raw):
        raise NotEditable("this BND0 names no node array")
    root = struct.unpack_from("<6f", raw, base)
    if any(v != v for v in root) or not all(root[i] <= root[3 + i]
                                            for i in range(3)):
        raise NotEditable("the root node is not a box")
    eps = [max(1e-3, (root[3 + i] - root[i]) * 1e-3) for i in range(3)]

    out = []
    limit = len(raw)
    i = 0
    while base + i * NODE + NODE <= limit:
        o = base + i * NODE
        box = struct.unpack_from("<6f", raw, o)
        if any(v != v for v in box):
            raise NotEditable("node %d has a NaN in its box" % i)
        if not all(box[j] <= box[3 + j] + 1e-3 for j in range(3)):
            raise NotEditable("node %d has min > max" % i)
        if not all(box[j] >= root[j] - eps[j]
                   and box[3 + j] <= root[3 + j] + eps[j] for j in range(3)):
            raise NotEditable("node %d is not inside the root box" % i)
        faces = struct.unpack_from("<I", raw, o + NODE_FACES)[0]
        if faces and base < faces < limit:
            limit = faces
        out.append(o)
        i += 1
    if not out:
        raise NotEditable("no nodes parsed")
    return out


def transform(raw, fn, radius_scale=1.0):
    """Move a whole collision shell. Returns new bytes, the same length.

    `fn(x, y, z) -> (x, y, z)` is applied to the vertices and, corner by
    corner, to every stored box -- the header AABB and every node in the
    index. The chunk keeps its length by construction, so it still fits the
    archive slot it came out of.
    """
    offs = nodes(raw)                     # raises before anything is written
    nverts = struct.unpack_from("<I", raw, BND_COUNTS)[0]
    voff = struct.unpack_from("<I", raw, BND_VOFF)[0]
    if voff + nverts * 12 > len(raw):
        raise NotEditable("vertex list runs past the end of the chunk")

    out = bytearray(raw)

    for i in range(nverts):
        o = voff + i * 12
        struct.pack_into("<3f", out, o, *[float(v) for v in fn(*_f3(raw, o))])

    def rebox(lo, hi):
        moved = [fn(*c) for c in _corners(lo, hi)]
        return ([min(m[i] for m in moved) for i in range(3)],
                [max(m[i] for m in moved) for i in range(3)])

    lo, hi = rebox(_f3(raw, BND_MIN), _f3(raw, BND_MAX))
    struct.pack_into("<3f", out, BND_MIN, *[float(v) for v in lo])
    struct.pack_into("<3f", out, BND_MAX, *[float(v) for v in hi])

    # The centre is stored, not derived, and it is the field whose staleness
    # is invisible: nothing about the file looks wrong afterwards.
    #
    # TRANSFORMED as a point, not recomputed from the new box. Both give the
    # same answer -- the corner set is symmetric about the centre, so
    # re-bounding a transformed box lands on the transformed centre -- but
    # only the transform leaves an unmoved shell's own bytes alone. A field
    # recomputed from scratch differs from the author's in the last bits, and
    # an identity move that rewrites bytes is not an identity move.
    struct.pack_into("<3f", out, BND_CENTRE,
                     *[float(v) for v in fn(*_f3(raw, BND_CENTRE))])

    if radius_scale != 1.0:
        r = struct.unpack_from("<f", raw, BND_RADIUS)[0]
        struct.pack_into("<f", out, BND_RADIUS, float(r * radius_scale))

    # This one genuinely cannot be transformed -- it measures distance from
    # the world origin, so a moved shell's old value describes a place it no
    # longer is. It is therefore adjusted by how much it CHANGED rather than
    # replaced by a fresh computation: the disc's stored values agree with the
    # derivation to about 2e-4 but not exactly, and overwriting with our own
    # arithmetic would edit every shell we merely looked at. Move nothing and
    # the delta is zero and the author's bytes survive untouched.
    was = _origin_dist(_f3(raw, BND_MIN), _f3(raw, BND_MAX))
    now = _origin_dist(lo, hi)
    if now != was:
        stored = struct.unpack_from("<f", raw, BND_ORIGIN_DIST)[0]
        struct.pack_into("<f", out, BND_ORIGIN_DIST, float(stored + now - was))

    for o in offs:
        box = struct.unpack_from("<6f", raw, o)
        nlo, nhi = rebox(box[:3], box[3:])
        struct.pack_into("<6f", out, o,
                         *[float(v) for v in list(nlo) + list(nhi)])

    return bytes(out)


def translate(raw, dx, dy, dz):
    return transform(raw, lambda x, y, z: (x + dx, y + dy, z + dz))


def scale(raw, sx, sy=None, sz=None, about=None):
    sy = sx if sy is None else sy
    sz = sx if sz is None else sz
    lo = _f3(raw, BND_MIN)
    hi = _f3(raw, BND_MAX)
    cx, cy, cz = about or tuple((a + b) / 2.0 for a, b in zip(lo, hi))
    return transform(raw, lambda x, y, z: (cx + (x - cx) * sx,
                                           cy + (y - cy) * sy,
                                           cz + (z - cz) * sz),
                     max(abs(sx), abs(sy), abs(sz)))


def rotate_y(raw, radians, about=None):
    cs, sn = math.cos(radians), math.sin(radians)
    lo = _f3(raw, BND_MIN)
    hi = _f3(raw, BND_MAX)
    cx, _cy, cz = about or tuple((a + b) / 2.0 for a, b in zip(lo, hi))
    return transform(raw, lambda x, y, z: (cx + (x - cx) * cs + (z - cz) * sn,
                                           y,
                                           cz - (x - cx) * sn + (z - cz) * cs))


def audit(raw):
    """Is this shell internally consistent about where it is? -> dict.

    Every check here is one the disc passes on all 2,870 stock shells, so a
    False after an edit is the edit's fault. This is what tells the difference
    between a shell that moved and a shell that merely had its triangles
    moved -- which is the bug M16 exists to fix, and which nothing about the
    file's size or its re-read can reveal.
    """
    lo, hi = list(_f3(raw, BND_MIN)), list(_f3(raw, BND_MAX))
    ctr = _f3(raw, BND_CENTRE)
    nverts = struct.unpack_from("<I", raw, BND_COUNTS)[0]
    voff = struct.unpack_from("<I", raw, BND_VOFF)[0]
    verts = [_f3(raw, voff + i * 12) for i in range(nverts)]
    vlo = [min(v[i] for v in verts) for i in range(3)]
    vhi = [max(v[i] for v in verts) for i in range(3)]
    tol = max(1e-3, max(hi[i] - lo[i] for i in range(3)) * 1e-4)

    offs = nodes(raw)
    root = struct.unpack_from("<6f", raw, offs[0])
    inside = 0
    for o in offs:
        box = struct.unpack_from("<6f", raw, o)
        if all(box[j] >= root[j] - tol and box[3 + j] <= root[3 + j] + tol
               for j in range(3)):
            inside += 1

    far = _origin_dist(lo, hi)
    return dict(
        nodes=len(offs),
        centre_matches=max(abs(ctr[i] - (lo[i] + hi[i]) / 2.0)
                           for i in range(3)) <= tol,
        origin_dist_matches=abs(
            struct.unpack_from("<f", raw, BND_ORIGIN_DIST)[0] - far) <= max(
                1e-2, far * 1e-4),
        verts_inside_aabb=all(vlo[i] >= lo[i] - tol and vhi[i] <= hi[i] + tol
                              for i in range(3)),
        root_covers_verts=all(root[i] <= vlo[i] + tol
                              and root[3 + i] >= vhi[i] - tol
                              for i in range(3)),
        nodes_inside_root=inside == len(offs),
    )
