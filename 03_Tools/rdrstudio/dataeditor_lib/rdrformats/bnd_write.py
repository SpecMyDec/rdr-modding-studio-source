"""Writing BND0 collision (the format: bnd.py), the .bnds files that place it, and the .lvl Bounds list."""
from __future__ import annotations

import itertools
import struct

from .bnd import MAGIC

LEAF_FACES = 15  # a node with more faces is split ...
MIN_SPLIT = 0.5  # ... while wider than this (metres), as the shipped bounds are
MAX_DEPTH = 12
LIGHT_LEAF = (32, 1.0)  # leaf_faces, min_split for big new collision: half the bytes of the default (a level's)
BOX_PAD = 0.0001
MARGIN = 0.001  # faces this near a node count as in it (float rounding must not drop one)


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(u, w):
    return (u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0])


def face_overlaps(p, lo, hi) -> bool:
    """Whether a flat convex face (points) touches a box: their boxes overlap and its plane passes through the box
    (a little generous: an extra face in a leaf costs a test, a missing one lets things through)."""
    for a in range(3):
        if max(q[a] for q in p) < lo[a] or min(q[a] for q in p) > hi[a]:
            return False
    n = _cross(_sub(p[1], p[0]), _sub(p[2], p[0]))
    d = sum(n[k] * p[0][k] for k in range(3))
    c = [(lo[k] + hi[k]) / 2 for k in range(3)]
    e = [(hi[k] - lo[k]) / 2 for k in range(3)]
    r = sum(abs(n[k]) * e[k] for k in range(3))
    return abs(sum(n[k] * c[k] for k in range(3)) - d) <= r * 1.0001 + 1e-7


def faces_from_polygons(vertices: list, polygons: list, material: int = 0, tol: float = 1e-3) -> list:
    """Collision faces from polygons (vertex index lists, counter-clockwise from the side they face): flat ones of 3
    or 4 corners kept, anything else (bent quads, n-gons) cut into a fan of triangles; faces of no size dropped.
    Returns [(corners, material)]."""
    out = []
    for poly in polygons:
        poly = [i for k, i in enumerate(poly) if i != poly[k - 1]]
        if len(poly) < 3:
            continue
        p = [vertices[i] for i in poly]
        n = _cross(_sub(p[1], p[0]), _sub(p[2], p[0]))
        ln = sum(x * x for x in n) ** 0.5
        flat = ln > 1e-12 and all(abs(sum(n[k] * (q[k] - p[0][k]) for k in range(3))) / ln < tol for q in p[3:])
        convex = True
        if len(poly) == 4 and flat:
            # a quad turning the same way at every corner (a concave one is a bad plane to collide with: two
            # triangles instead)
            turns = [sum(n[k] * t for k, t in enumerate(_cross(_sub(p[(j + 1) % 4], p[j]),
                                                                _sub(p[(j + 2) % 4], p[(j + 1) % 4]))))
                     for j in range(4)]
            convex = min(turns) > -1e-12 or max(turns) < 1e-12
            if not convex:
                # two triangles from the corner that turns the other way (a fan from any other cuts outside it)
                odd = turns.index(min(turns)) if sum(1 for t in turns if t < 0) == 1 else turns.index(max(turns))
                r = (odd + 1) % 4
                q = poly[r:] + poly[:r]
                out.append(([q[0], q[1], q[2]], material))
                out.append(([q[0], q[2], q[3]], material))
                continue
        if len(poly) <= 4 and flat and convex:
            out.append((list(poly), material))
        else:
            for k in range(1, len(poly) - 1):
                out.append(([poly[0], poly[k], poly[k + 1]], material))
    good = []
    for cs, m in out:
        p = [vertices[i] for i in cs]
        if sum(x * x for x in _cross(_sub(p[1], p[0]), _sub(p[2], p[0]))) > 4e-8:  # 1 cm2: less has no direction
            good.append((cs, m))
    return good


def write(vertices: list, faces: list, materials=(1,), leaf_faces: int = LEAF_FACES, min_split: float = MIN_SPLIT) -> bytes:
    """A BND0 from vertices [(x, y, z) game space, world] and faces [(corner indices: 3 or 4, flat and convex,
    the normal by the first three's right-hand rule; material index into materials)]. materials: PMT0 chunk
    numbers (1: default). leaf_faces / min_split: a node is split while it holds more faces and is wider (bigger
    leaves: a smaller bound -- LIGHT_LEAF -- the shipped ones hold up to 193 faces a leaf)."""
    nv, nf, nm = len(vertices), len(faces), len(materials)
    if not nv or not nf:
        raise ValueError('a bound needs vertices and faces')
    lo = [min(v[a] for v in vertices) - BOX_PAD for a in range(3)]  # the shipped boxes are 0.1 mm wider
    hi = [max(v[a] for v in vertices) + BOX_PAD for a in range(3)]
    centre = [(lo[a] + hi[a]) / 2 for a in range(3)]
    size = [hi[a] - lo[a] for a in range(3)]
    vlo, vhi = [x + BOX_PAD for x in lo], [x - BOX_PAD for x in hi]  # radius and reach: of the vertices' own box
    radius = 0.5 * sum((vhi[a] - vlo[a]) ** 2 for a in range(3)) ** 0.5
    far = max(sum(c * c for c in corner) ** 0.5 for corner in itertools.product(*zip(vlo, vhi)))
    eps = 0.035 * min(size)
    vo = 0x90
    fo = (vo + 12 * nv + 15) & ~15
    mo = fo + 32 * nf
    root_at = mo + 4 * nm

    side = max(size) or 1.0
    rlo = [centre[a] - side / 2 for a in range(3)]
    rhi = [centre[a] + side / 2 for a in range(3)]
    pts = [[vertices[i] for i in cs] for cs, _m in faces]

    def node_bytes(nlo, nhi, kids, leaf):
        return struct.pack('<6f2I', *nlo, *nhi, kids, leaf)

    def build(nlo, nhi, inside, at, depth):
        """The node's contents (a block of 8 nodes and theirs, depth first, or a leaf) written from offset at:
        (bytes, block offset, leaf offset)."""
        if not inside:
            return b'', 0, 0
        if len(inside) > leaf_faces and nhi[0] - nlo[0] > min_split and depth < MAX_DEPTH:
            mid = [(nlo[a] + nhi[a]) / 2 for a in range(3)]
            boxes = []
            for k in range(8):  # x fastest, then y, then z
                clo = [mid[a] if (k >> a) & 1 else nlo[a] for a in range(3)]
                chi = [nhi[a] if (k >> a) & 1 else mid[a] for a in range(3)]
                glo, ghi = [x - MARGIN for x in clo], [x + MARGIN for x in chi]
                boxes.append((clo, chi, [f for f in inside if face_overlaps(pts[f], glo, ghi)]))
            if any(len(fs) < len(inside) for _l, _h, fs in boxes):
                body = bytearray(32 * 8)
                cur = at + 32 * 8
                for k, (clo, chi, fs) in enumerate(boxes):
                    sub, kids, leaf = build(clo, chi, fs, cur, depth + 1)
                    body[32 * k:32 * k + 32] = node_bytes(clo, chi, kids, leaf)
                    body += sub
                    cur += len(sub)
                return bytes(body), at, 0
        vs = [q for f in inside for q in pts[f]]
        llo = [min(q[a] for q in vs) for a in range(3)]
        lhi = [max(q[a] for q in vs) for a in range(3)]
        rec = bytearray(struct.pack('<6fH', *llo, *lhi, len(inside)) + struct.pack(f'<{len(inside)}H', *inside))
        while len(rec) % 4:
            rec += b'\xcd'
        return bytes(rec), 0, at

    sub, kids, leaf = build(rlo, rhi, list(range(nf)), root_at + 32, 0)
    tree = node_bytes(rlo, rhi, kids, leaf) + sub
    bits_at = root_at + len(tree)

    head = bytearray(0x90)
    struct.pack_into('<IBBBB', head, 0, MAGIC, 8, nm, 1, 0)
    struct.pack_into('<9f', head, 0x08, *lo, *hi, *centre)
    struct.pack_into('<4f', head, 0x38, radius, far, eps, 0.3 * eps)
    struct.pack_into('<IIIiIiI', head, 0x4C, nv, nf, 0, -1, 0, -1, 0)
    struct.pack_into('<IIIiIIIIII', head, 0x68, vo, fo, mo, -1, 0, root_at, bits_at, 0, 0, 2)
    body = bytearray(head)
    for v in vertices:
        body += struct.pack('<3f', *v)
    body += bytes(fo - len(body))
    for cs, m in faces:
        p = [vertices[i] for i in cs]
        n = _cross(_sub(p[1], p[0]), _sub(p[2], p[0]))
        ln = sum(x * x for x in n) ** 0.5 or 1.0
        area = sum(sum(x * x for x in _cross(_sub(p[k], p[0]), _sub(p[k + 1], p[0]))) ** 0.5
                   for k in range(1, len(p) - 1)) / 2
        abits = (struct.unpack('<I', struct.pack('<f', area))[0] & ~0xFF) | (m & 0xFF)
        # four corners always, as the shipped faces: a triangle repeats corner 0 in the fourth slot ([a, b, c, 0]);
        # a quad that ends on point 0 is turned round (same face) so it is not read as a triangle. Written
        # [a, b, c, 0xFFFF] the game read a fourth corner at point 65535 -- past the end -- and flung the player
        # (2026-11-18)
        cs = list(cs)[:4]
        if len(cs) == 4 and cs[3] == 0:
            cs = cs[3:] + cs[:3]
        if len(cs) == 3:
            cs = cs + [0]
        slots = cs + [0xFFFF] * (8 - len(cs))
        body += struct.pack('<3fI8H', *(x / ln for x in n), abits, *slots)
    body += struct.pack(f'<{nm}I', *materials)
    body += tree + bytes(4 * ((nf + 31) // 32))
    return bytes(body)


SOLID = {'BoundFlagMover': True, 'BoundFlagCamera': True, 'BoundFlagTarget': True, 'BoundFlagShoot': True,
         'BoundFlagSneak': True, 'BoundFlagEnemy': True, 'BoundFlagHorse': True, 'BoundFlagPlayer': False,
         'BoundFlagDoor': False, 'BoundFlagCamClip': False, 'BoundFlagWater': False, 'BoundFlagPlummet': False}
# what the shipped levels' fully solid collision has (bound_grp_tttttttfffff): everything that moves stops on it,
# bullets and the camera too


def bnds_file(nodes: list) -> str:
    """A bound file (.bnds): nodes [(name, chunk number, {flag: bool})], at the identity (bounds are world space)."""
    out = [f'NumInstNodes {len(nodes)} ', '']
    for name, num, flags in nodes:
        out += [f'node {name} {{', f'\tresourceIndex {num} ', f'\tresourceIndexXbox {num} ', '\tmatrix', '\t{',
                '\t\t1.000000 \t0.000000 \t0.000000 ', '\t\t0.000000 \t1.000000 \t0.000000 ',
                '\t\t0.000000 \t0.000000 \t1.000000 ', '\t\t0.000000 \t0.000000 \t0.000000 ', '\t}',
                f'\tattributes {len(flags)} {{']
        out += [f'\t\tBOOL\t{k}\t{"true" if v else "false"}' for k, v in flags.items()]
        out += ['\t}', '}']
    return '\r\n'.join(out) + '\r\n'


def add_bounds(lvl_text: str, bnds_name: str) -> str:
    """A .lvl with one more bound file in its Bounds list."""
    import re
    if bnds_name.lower() in lvl_text.lower():
        return lvl_text
    nl = '\r\n' if '\r\n' in lvl_text else '\n'
    m = re.search(r'Bounds\s*\{', lvl_text)
    if not m:
        return f'Bounds {nl}{{ {nl}{bnds_name} {nl}}} {nl}' + lvl_text
    return lvl_text[:m.end()] + nl + bnds_name + ' ' + lvl_text[m.end():]


def audit(data: bytes) -> list:
    """Problems in a BND0's octree: every face touching a leaf node's box must be in that leaf; boxes must hold
    their faces. Returns messages (empty: fine)."""
    from . import bnd
    b = bnd.read(data)
    t = bnd.read_tree(data)
    pts = [[b.vertices[i] for i in cs] for cs, _m in b.faces]
    problems = []
    covered = set()

    def walk(n):
        if n['leaf']:
            _lo, _hi, fs, _at = n['leaf']
            covered.update(fs)
            want = {f for f in range(len(pts)) if face_overlaps(pts[f], n['min'], n['max'])}
            if want - set(fs):
                problems.append(f'leaf at {n["at"]:#x} misses faces {sorted(want - set(fs))[:6]}')
        elif not n['children']:
            want = [f for f in range(len(pts)) if face_overlaps(pts[f], n['min'], n['max'])]
            if want:
                problems.append(f'empty node at {n["at"]:#x} has faces {want[:6]}')
        for k in n['children'] or []:
            walk(k)
    walk(t)
    if len(covered) != len(pts):
        problems.append(f'{len(pts) - len(covered)} faces in no leaf')
    return problems
