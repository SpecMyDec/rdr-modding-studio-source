"""Build a .mod / .xmod from triangles, outside Blender.

Each triangle is three corners in the game's space (Y up), front face counter clockwise as the game draws it (the
winding rdrformats.mod.Primitive.triangles gives). A corner carries its position, normal, color, uv1, uv2, the bone
it rides and up to two more it blends toward (the format's reskins): blend ((bone, r1), (bone, r2)) moves the corner
r1 of the way from its bone to the first, then r2 of the way to the second, so the bones' shares are
(1 - r1)(1 - r2), r1 (1 - r2) and r2 (see weights_to_blend). A reskin stores its weight and the corner's place from that
bone in the bind pose (model space position less the bone's: all 841 of aka's agree).

Packets are filled to the limits every shipped 2.12 model keeps (58 adjuncts, 26 matrices: measured over 2,615 Xbox
models, the format itself allows 256), so a packet never asks the runtime for more than the original exporter did.
Each packet's triangles are joined into strips.

PS2 .mod characters keep their positions relative to their bone (the bind pose has no rotation, so relative is the
bone's summed offset subtracted); Xbox .xmod ones are in model space. bone_relative chooses.
"""
from __future__ import annotations

import struct
from collections import defaultdict
from dataclasses import dataclass, field

from . import mod

MAX_ADJUNCTS = 58
MAX_MATRICES = 26
MAX_STRIP = 255  # 2.12's u8 count
WHITE = (1.0, 1.0, 1.0, 1.0)


@dataclass
class Corner:
    position: tuple
    normal: tuple
    color: tuple = WHITE
    uv1: tuple = (0.0, 0.0)
    uv2: tuple | None = None
    bone: int = 0
    blend: tuple = ()  # ((bone, factor), ...), at most two, in the order they apply


def weights_to_blend(weights: dict, most: int = 3, least: float = 0.01):
    """Bone weights {bone: weight} as (bone, blend): the strongest bone, then up to two more in the order they apply
    (the weakest last), weights under least dropped and the rest made to sum to 1."""
    ws = sorted(((w, b) for b, w in weights.items() if w >= least), reverse=True)[:most]
    total = sum(w for w, _ in ws)
    if not ws or total <= 0:
        return 0, ()
    ws = [(w / total, b) for w, b in ws]
    if len(ws) == 1:
        return ws[0][1], ()
    if len(ws) == 2:
        return ws[0][1], ((ws[1][1], round(ws[1][0], 5)),)
    (w0, b0), (w1, b1), (w2, b2) = ws
    return b0, ((b1, round(w1 / (1.0 - w2), 5)), (b2, round(w2, 5)))


def blend_to_weights(bone: int, blend: tuple) -> dict:
    """The bones' shares from a corner's bone and blend."""
    out = {bone: 1.0}
    for b, r in blend:
        for k in out:
            out[k] *= 1.0 - r
        out[b] = out.get(b, 0.0) + r
    return out


@dataclass
class Triangle:
    material: int
    corners: tuple  # three Corner


@dataclass
class BuildOptions:
    version: str = '2.12'
    bone_relative: bool = False  # PS2 .mod characters
    bone_positions: list = field(default_factory=list)  # model space, by bone index, for bone_relative
    bone_count: int | None = None  # the skeleton's, for the header; None for the highest bone used + 1
    max_adjuncts: int = MAX_ADJUNCTS
    max_matrices: int = MAX_MATRICES
    strips: bool = True  # False writes every triangle as its own primitive


class _Pool:
    """Distinct values in first use order."""

    def __init__(self):
        self.items, self.index = [], {}

    def add(self, v) -> int:
        i = self.index.get(v)
        if i is None:
            i = self.index[v] = len(self.items)
            self.items.append(v)
        return i


def _q(v, places=6):
    return tuple(round(float(x), places) for x in v)


def build(triangles: list[Triangle], materials: list[mod.Material], opts: BuildOptions = BuildOptions()) -> mod.Model:
    if opts.version not in mod.VERSIONS:
        raise mod.FormatError(f'unsupported version {opts.version}')
    m = mod.Model(version=opts.version)
    positions, normals, colors, uv1s, uv2s = _Pool(), _Pool(), _Pool(), _Pool(), _Pool()
    any_uv2 = any(c.uv2 is not None for t in triangles for c in t.corners)
    by_material = defaultdict(list)
    for t in triangles:
        if not 0 <= t.material < len(materials):
            raise mod.FormatError(f'triangle material {t.material} has no material')
        by_material[t.material].append(t)

    world = {}  # key: model space position, for the reskins' offsets

    def corner_key(c: Corner):
        p = c.position
        if opts.bone_relative:
            if not 0 <= c.bone < len(opts.bone_positions):
                raise mod.FormatError(f'bone {c.bone} has no position for a bone relative model')
            b = opts.bone_positions[c.bone]
            p = (p[0] - b[0], p[1] - b[1], p[2] - b[2])
        blend = tuple((b, round(float(r), 5)) for b, r in c.blend[:2] if b != c.bone and r > 0)
        key = (positions.add(_q(p)), normals.add(_q(c.normal)), colors.add(_q(c.color)), uv1s.add(_q(c.uv1)),
               uv2s.add(_q(c.uv2 if c.uv2 is not None else (0.0, 0.0))), c.bone, blend)
        world.setdefault(key, c.position)
        return key

    out_materials = []
    for mi, src in enumerate(materials):
        mat = mod.Material(name=src.name, illum=src.illum, ambient=src.ambient, diffuse=src.diffuse,
                           specular=src.specular, textures=list(src.textures), params=list(src.params))
        tris = [tuple(corner_key(c) for c in t.corners) for t in by_material.get(mi, [])]
        tris = [t for t in tris if len(set(t)) == 3]  # a corner twice draws nothing
        # neighbours by bone set, so a packet's matrices stay few
        tris.sort(key=lambda t: tuple(sorted(_bones(t))))
        mat.packets = _packets(tris, opts, world)
        out_materials.append(mat)
    m.materials = out_materials
    m.positions, m.normals, m.colors, m.uv1s = positions.items, normals.items, colors.items, uv1s.items
    m.uv2s = uv2s.items if any_uv2 else uv2s.items[:1] or [(0.0, 0.0)]
    if not m.colors:
        m.colors = [WHITE]
    if not m.uv1s:
        m.uv1s = [(0.0, 0.0)]
    m.header_totals = None
    m.trailer = None
    totals = mod.derived_totals(m, opts.bone_count)
    m.header_totals = totals
    if m.wide:
        m.trailer = mod.derived_trailer(m, opts.bone_count)
    return m


def _bones(t: tuple) -> set:
    """The bones a triangle's corners ride and blend toward."""
    return {k[5] for k in t} | {b for k in t for b, _ in k[6]}


def _packets(tris: list[tuple], opts: BuildOptions, world: dict) -> list[mod.Packet]:
    packets = []
    pending: list[tuple] = []
    adj: dict = {}
    mtx: dict = {}

    def close():
        if not pending:
            return
        pk = mod.Packet()
        pk.matrices = [b for b, _ in sorted(mtx.items(), key=lambda kv: kv[1])]
        slot_of = mtx
        pk.adjuncts = [None] * len(adj)
        rec = '<' + ('II' if opts.version in ('2.00', '2.10') else 'HH') + '4f'
        for key, i in adj.items():
            pk.adjuncts[i] = mod.Adjunct(key[0], key[1], key[2], key[3], key[4], slot_of[key[5]])
        for key, i in sorted(adj.items(), key=lambda kv: kv[1]):
            p = world[key]
            for b, r in key[6]:
                bp = opts.bone_positions[b] if b < len(opts.bone_positions) else (0.0, 0.0, 0.0)
                pk.reskins.append(struct.pack(rec, i, slot_of[b], r, p[0] - bp[0], p[1] - bp[1], p[2] - bp[2]))
        local = [tuple(adj[k] for k in t) for t in pending]
        pk.primitives = _strips(local) if opts.strips else [mod.Primitive(mod.TRIANGLES, list(t)) for t in local]
        packets.append(pk)

    for t in tris:
        new_adj = {k for k in t if k not in adj}
        new_mtx = {b for b in _bones(t) if b not in mtx}
        if pending and (len(adj) + len(new_adj) > opts.max_adjuncts or len(mtx) + len(new_mtx) > opts.max_matrices):
            close()
            pending, adj, mtx = [], {}, {}
            new_adj = set(t)
            new_mtx = _bones(t)
        for k in t:
            if k not in adj:
                adj[k] = len(adj)
        for b in sorted(_bones(t), key=lambda b: (b not in {k[5] for k in t}, b)):
            if b not in mtx:
                mtx[b] = len(mtx)
        pending.append(t)
    close()
    return packets


def _same_turn(a: tuple, b: tuple) -> bool:
    """b is a with its corners rotated, the same facing."""
    return b in (a, (a[1], a[2], a[0]), (a[2], a[0], a[1]))


def _strips(tris: list[tuple]) -> list[mod.Primitive]:
    """Greedy strips over a packet's triangles (local adjunct indices, front counter clockwise). Every triangle a
    strip makes is checked against the one it stands for, so the facing never changes."""
    by_edge = defaultdict(list)
    for i, t in enumerate(tris):
        for e in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
            by_edge[frozenset(e)].append(i)
    used = [False] * len(tris)
    prims = []
    for start, t in enumerate(tris):
        if used[start]:
            continue
        best = None
        for rot in ((t[0], t[1], t[2]), (t[1], t[2], t[0]), (t[2], t[0], t[1])):
            strip, taken = list(rot), {start}
            while len(strip) < MAX_STRIP:
                a, b = strip[-2], strip[-1]
                nxt = None
                for j in by_edge[frozenset((a, b))]:
                    if used[j] or j in taken:
                        continue
                    c = next(v for v in tris[j] if v not in (a, b))
                    k = len(strip) - 2  # the triangle this index would make
                    made = (a, b, c) if k % 2 == 0 else (b, a, c)
                    if _same_turn(tris[j], made):
                        nxt = (j, c)
                        break
                if nxt is None:
                    break
                taken.add(nxt[0])
                strip.append(nxt[1])
            if best is None or len(taken) > len(best[1]):
                best = (strip, taken)
        strip, taken = best
        for j in taken:
            used[j] = True
        prims.append(mod.Primitive(mod.STRIP, strip))
    return prims


def triangles_of(m: mod.Model, bone_positions: list | None = None) -> tuple[list[Triangle], list[mod.Material]]:
    """A model's triangles back as corners in model space (adding bone positions to a bone relative model)."""
    out = []
    rec = '<' + ('II' if m.wide else 'HH') + 'f'
    for mi, mat in enumerate(m.materials):
        for pk in mat.packets:
            blends = defaultdict(list)
            for r in pk.reskins:
                k, s, w = struct.unpack_from(rec, r)
                if s < len(pk.matrices):
                    blends[k].append((pk.matrices[s], w))
            for prim in pk.primitives:
                for tri in prim.triangles():
                    corners = []
                    for i in tri:
                        a = pk.adjuncts[i]
                        bone = pk.matrices[a.matrix] if a.matrix < len(pk.matrices) else 0
                        p = m.positions[a.position]
                        if bone_positions is not None and bone < len(bone_positions):
                            b = bone_positions[bone]
                            p = (p[0] + b[0], p[1] + b[1], p[2] + b[2])
                        uv2 = m.uv2s[a.uv2] if a.uv2 < len(m.uv2s) else None
                        corners.append(Corner(p, m.normals[a.normal], m.colors[a.color] if a.color < len(m.colors)
                                              else WHITE, m.uv1s[a.uv1] if a.uv1 < len(m.uv1s) else (0.0, 0.0), uv2,
                                              bone, tuple(blends.get(i, ())[:2])))
                    out.append(Triangle(mi, tuple(corners)))
    return out, m.materials
