"""PS2 bundles (.rsc): the same RSC0 container and .rnt names as the Xbox's (rsc.read_chunks / read_names), with PS2
chunk contents.

MOD0 (a mesh): a 0x74 byte header, then VIF packets for VU1, one batch of up to ~60 vertices after another:
    header   +0x00 u8 1, u8 bone count (0: a plain or attached mesh), u16; ...
             +0x68 u16 qwords?, u16 vertex count; +0x74 u32 batch count (a VIF NOP's immediate)
    VIF      UNPACK V4-32 to 0x29: the bounds (?, ?, ?, radius) for the whole mesh, then MSCAL
    a batch  STCYCL 3,1 (three qwords a vertex: st, normal, position), then
             V4-32 x2 at the batch's base: its header and the GIF tag (NLOOP = vertices, PRIM)
             V2-16 st        STROW 2048.0: st = (row + u16) - 2048 - 8
             V3-8  normal    STROW 65536.0 (w 65537.0): n = (row + u8) - 65537.0, so (u8 - 128) / 128
             V4-16 position  STROW 1024.0: p = (row + u16) - bias, the bias a V3-32 at 0x28 (1028.0 ...);
                             w: bit 15 skips the triangle (a strip starts again, the GS ADC bit), bits 0-14 the
                             bone's matrix in VU memory: bone = (w - 210) / 4
             ITOP, MSCAL 0
    Each vertex follows one bone (no blending on PS2).
    Level meshes (lvl_<name>.rsc) have no normals: their batches carry st, a V4-8 color (the baked light, 0x80 =
    1.0) and V4-32 float positions (bias 0), head (0, 0, (n + 26) << 16 | 0x142, ...), in the rigid buffers.
MIP0 / PAL0: an 8 bit texture (256 colors) after a 0x60 byte header; the palette's 256 colors RGBA with alpha
    0x80 as opaque, in the GS's CSM1 order (each 32 colors: 8-15 and 16-23 swapped).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

HEADER = 0x74
BONE_BASE = 210  # VU address of bone 0's matrix
BONE_STRIDE = 4
UV_BIAS = 8.0


def _f(bits: int) -> float:
    return struct.unpack('<f', struct.pack('<I', bits & 0xFFFFFFFF))[0]


VN = 'S V2 V3 V4'.split()
VL = [32, 16, 8, 5]


def vif(data: bytes, start: int = HEADER):
    """The VIF commands from start: (offset, command name, num, imm, payload dict)."""
    o = start
    out = []
    while o + 4 <= len(data):
        w = struct.unpack_from('<I', data, o)[0]
        cmd, num, imm = (w >> 24) & 0x7F, (w >> 16) & 0xFF, w & 0xFFFF
        rec = {'at': o, 'cmd': cmd, 'num': num, 'imm': imm}
        o += 4
        if 0x60 <= cmd <= 0x7F:
            vn, vl = (cmd >> 2) & 3, cmd & 3
            per = 2 if vl == 3 else (VL[vl] // 8) * (vn + 1)
            size = (per * num + 3) & ~3
            rec.update(kind=f'{VN[vn]}-{VL[vl]}', addr=imm & 0x3FF, usn=(imm >> 14) & 1, data=data[o:o + size])
            o += size
        elif cmd == 0x20:
            rec.update(kind='STMASK', mask=struct.unpack_from('<I', data, o)[0])
            o += 4
        elif cmd in (0x30, 0x31):
            rec.update(kind='STROW' if cmd == 0x30 else 'STCOL', row=struct.unpack_from('<4I', data, o))
            o += 16
        elif cmd == 0x05:
            rec.update(kind='STMOD')
        elif cmd == 0x01:
            rec.update(kind='STCYCL')
        elif cmd == 0x14:
            rec.update(kind='MSCAL')
        elif cmd in (0x50, 0x51):
            n = (imm or 0x10000) * 16
            rec.update(kind='DIRECT', data=data[o:o + n])
            o += n
        else:
            rec.update(kind=f'{cmd:02X}')
        out.append(rec)
    return out


@dataclass
class Vertex:
    position: tuple
    normal: tuple
    uv: tuple
    bone: int
    skip: bool  # the GS draws no triangle ending here: a strip starts again
    color: tuple = None  # level meshes: the baked light, rgba (0x80 = 1.0)


@dataclass
class Batch:
    material: int
    vertices: list = field(default_factory=list)


@dataclass
class Material:
    texture: int  # its TEX0 chunk number, 0 for none
    color: tuple = (1.0, 1.0, 1.0, 1.0)


@dataclass
class Mesh:
    bones: int
    materials: list = field(default_factory=list)
    batches: list = field(default_factory=list)
    bounds: tuple = ()
    vertex_count: int = 0  # the descriptors' total
    lit: bool = False  # a level mesh: vertex colors, no normals


def read_mod0(c: bytes) -> Mesh:
    """A PS2 MOD0: u8 material count, u8 bone count, u16; u32 0x10; u32 offset of the draw list; u32; then from 0x10
    an offset per material to its record (u32 TEX0 chunk number, u32 offset of its color: 4 floats), and at the
    draw list an offset per material to its descriptor (u32 offset of its VIF stream, u16 size, u16 vertex count).
    Each stream: a VIF NOP whose immediate counts its batches, then the batches (see the module)."""
    nmat, bones = c[0], c[1]
    draw_list = struct.unpack_from('<I', c, 0x08)[0]
    mesh = Mesh(bones)
    starts = []
    for k in range(nmat):
        rec = struct.unpack_from('<I', c, 0x10 + 4 * k)[0]
        tex, color_at = struct.unpack_from('<II', c, rec)
        color = struct.unpack_from('<4f', c, color_at) if 0 < color_at and color_at + 16 <= len(c) else \
            (1.0, 1.0, 1.0, 1.0)
        mesh.materials.append(Material(tex, color))
        desc = struct.unpack_from('<I', c, draw_list + 4 * k)[0]
        vif_at, _size, verts = struct.unpack_from('<IHH', c, desc)
        starts.append(vif_at)
        mesh.vertex_count += verts
    for k, start in enumerate(starts):
        end = min((e for e in starts if e > start), default=len(c))
        _walk(mesh, k, c[:end], start)
    return mesh


def _walk(mesh, material, c, start):
    row, bias = (0, 0, 0, 0), (1028.0, 1028.0, 1028.0)
    pending = {}
    for r in vif(c, start):
        kind = r['kind']
        if kind == 'STROW':
            row = r['row']
        elif kind == 'V4-32' and r['addr'] == 0x29:
            mesh.bounds = struct.unpack('<4f', r['data'][:16])
        elif kind == 'V3-32' and r['addr'] == 0x28:
            bias = struct.unpack('<3f', r['data'][:12])
            if 'pos' in pending:
                _finish(mesh, material, pending, bias)
            pending = {}
        elif kind == 'V2-16' and r['num'] > 1:
            pending['st'] = [tuple(_f(row[k] + v) - _f(row[k]) - UV_BIAS for k, v in enumerate(p))
                             for p in struct.iter_unpack('<2H', r['data'][:4 * r['num']])]
        elif kind == 'V4-8' and r['num'] > 1:  # a level mesh's colors
            pending['c'] = [tuple(x / 128.0 for x in p) for p in struct.iter_unpack('<4B', r['data'][:4 * r['num']])]
        elif kind == 'V3-8' and r['num'] > 1:
            pending['n'] = [tuple(_f(row[k] + v) - _f(row[3]) for k, v in enumerate(p))
                            for p in struct.iter_unpack('<3B', r['data'][:3 * r['num']])]
        elif kind == 'V4-16' and r['num'] > 1:
            pending['pos'] = [(tuple(_f(row[k] + p[k]) for k in range(3)), p[3])
                              for p in struct.iter_unpack('<4H', r['data'][:8 * r['num']])]
        elif kind == 'V4-32' and r['num'] > 1:  # a mesh too big for 16 bit positions: floats, w the same flags
            pending['pos'] = [((x, y, z), w) for x, y, z, w in
                              (struct.unpack_from('<3fI', r['data'], 16 * i) for i in range(r['num']))]


def _finish(mesh, material, pending, bias):
    b = Batch(material)
    sts, ns, cs = pending.get('st', []), pending.get('n', []), pending.get('c', [])
    if cs and not ns:
        mesh.lit = True
    for i, (p, w) in enumerate(pending['pos']):
        pos = tuple(p[k] - bias[k] for k in range(3))
        bone = ((w & 0x7FFF) - BONE_BASE) // BONE_STRIDE if mesh.bones else 0
        b.vertices.append(Vertex(pos, ns[i] if i < len(ns) else None,
                                 sts[i] if i < len(sts) else (0.0, 0.0), bone, bool(w & 0x8000),
                                 cs[i] if i < len(cs) else None))
    mesh.batches.append(b)


def strips(batch: Batch) -> list:
    """The batch's strips: lists of vertex indices, each strip starting at a pair of skipping vertices."""
    out, cur = [], []
    vs = batch.vertices
    for i, v in enumerate(vs):
        if v.skip and (i + 1 < len(vs) and vs[i + 1].skip) and not (cur and len(cur) == 1 and vs[cur[0]].skip):
            if len(cur) >= 3:
                out.append(cur)
            cur = []
        cur.append(i)
    if len(cur) >= 3:
        out.append(cur)
    return out


def triangles(batch: Batch, offsets=None) -> list:
    """The batch's triangles (vertex indices in it). Each strip alternates its winding, but strips start either way
    round (the GS culls nothing, so the game never kept them one way): each strip is turned to face the way most of
    its vertex normals do. offsets: bone positions added to skinned positions (bone relative) for that test."""
    import numpy as np
    vs = batch.vertices
    pos = np.array([v.position for v in vs], np.float64)
    if offsets is not None:
        pos = pos + np.array([offsets[v.bone] if v.bone < len(offsets) else (0.0, 0.0, 0.0) for v in vs])
    nrm = np.array([v.normal or (0.0, 0.0, 0.0) for v in vs], np.float64)
    out = []
    for st in strips(batch):
        tris, score = [], 0.0
        for k in range(2, len(st)):
            i = st[k]
            if vs[i].skip:
                continue
            a, b, c = st[k - 2], st[k - 1], i
            t = (a, b, c) if k % 2 == 0 else (b, a, c)
            n = np.cross(pos[t[1]] - pos[t[0]], pos[t[2]] - pos[t[0]])
            score += float(n @ nrm[list(t)].sum(0))
            tris.append(t)
        if score < 0:
            tris = [(b, a, c) for a, b, c in tris]
        out += tris
    return out


def to_model(mesh: Mesh, texture_names: dict, bone_positions=None):
    """The mesh as a model (mod.Model, 2.12 shapes): a packet a batch, its bones as the packet's matrices, its
    strip as triangles. texture_names: TEX0 chunk number -> name."""
    from . import mod
    m = mod.Model(version='2.12')
    m.uv2s, m.colors = [(0.0, 0.0)], [(1.0, 1.0, 1.0, 1.0)]
    pos, nrm, uvs, cols = {}, {}, {}, {(1.0, 1.0, 1.0, 1.0): 0}
    offs = bone_positions if mesh.bones else None
    tris_of = [triangles(b, offs) for b in mesh.batches]
    normals = None
    if any(v.normal is None for b in mesh.batches for v in b.vertices):
        tris_of = orient(mesh, tris_of)
        normals = smooth_normals(mesh, tris_of)

    def pool(d, lst, v):
        k = tuple(round(x, 6) for x in v)
        if k not in d:
            d[k] = len(lst)
            lst.append(k)
        return d[k]
    mats = []
    for mt in mesh.materials:
        name = texture_names.get(mt.texture) if mt.texture else None
        mat = mod.Material(name=name or 'untextured', textures=[name] if name else [])
        mat.diffuse = tuple(mt.color)
        mats.append(mat)
    for bi, b in enumerate(mesh.batches):
        used = sorted({v.bone for v in b.vertices}) if mesh.bones else [0]
        slot = {bone: i for i, bone in enumerate(used)}
        pk = mod.Packet(matrices=used)
        for vi, v in enumerate(b.vertices):
            n = v.normal if v.normal is not None else normals[bi][vi]
            c = pool(cols, m.colors, v.color) if v.color is not None else 0
            pk.adjuncts.append(mod.Adjunct(pool(pos, m.positions, v.position), pool(nrm, m.normals, n), c,
                                           pool(uvs, m.uv1s, v.uv), 0, slot.get(v.bone, 0)))
        pk.primitives = [mod.Primitive(mod.TRIANGLES, [i for t in tris_of[bi] for i in t])]
        mats[b.material].packets.append(pk)
    m.materials = [mt for mt in mats if mt.packets]
    m.header_totals = (sum(len(p.adjuncts) for p in m.packets()), 0, max(mesh.bones, 1), 0)
    return m


def _key(v):
    return tuple(round(x, 4) for x in v.position)


def orient(mesh: Mesh, tris_of: list) -> list:
    """Triangles of a mesh without normals (levels), turned to agree with their neighbours: its strips start either
    way round (the GS culls nothing), so each triangle is matched to the ones sharing its edges (by place), and
    each connected piece then faces up (game +y) more than down."""
    from collections import deque
    tris = [(bi, tuple(t)) for bi, ts in enumerate(tris_of) for t in ts]
    keys = [[_key(mesh.batches[bi].vertices[i]) for i in t] for bi, t in tris]
    edges = {}
    for ti, ks in enumerate(keys):
        for e in range(3):
            a, c = ks[e], ks[(e + 1) % 3]
            if a != c:
                edges.setdefault(frozenset((a, c)), []).append((ti, a))
    flip = [None] * len(tris)
    for start in range(len(tris)):
        if flip[start] is not None:
            continue
        flip[start] = False
        comp, todo = [start], deque([start])
        while todo:
            ti = todo.popleft()
            ks = keys[ti]
            for e in range(3):
                a, c = ks[e], ks[(e + 1) % 3]
                if a == c:
                    continue
                shared = edges.get(frozenset((a, c)), ())
                if len(shared) != 2:
                    continue  # an open or non-manifold edge says nothing
                for tj, a2 in shared:
                    if tj == ti or flip[tj] is not None:
                        continue
                    # neighbours agree when they run the shared edge opposite ways
                    flip[tj] = (a2 == a) != flip[ti]
                    comp.append(tj)
                    todo.append(tj)
        up = 0.0
        for ti in comp:
            bi, t = tris[ti]
            p = [mesh.batches[bi].vertices[i].position for i in t]
            ny = (p[1][2] - p[0][2]) * (p[2][0] - p[0][0]) - (p[1][0] - p[0][0]) * (p[2][2] - p[0][2])
            up += -ny if flip[ti] else ny
        if up < 0:
            for ti in comp:
                flip[ti] = not flip[ti]
    out = [[] for _ in mesh.batches]
    for (bi, t), f in zip(tris, flip):
        out[bi].append((t[1], t[0], t[2]) if f else t)
    return out


def smooth_normals(mesh: Mesh, tris_of: list) -> list:
    """Per batch, per vertex: the sum of its triangles' normals, shared by vertices at the same place."""
    acc = {}
    for b, ts in zip(mesh.batches, tris_of):
        for t in ts:
            p = [b.vertices[i].position for i in t]
            u = [p[1][k] - p[0][k] for k in range(3)]
            w = [p[2][k] - p[0][k] for k in range(3)]
            n = (u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0])
            for i in t:
                s = acc.setdefault(_key(b.vertices[i]), [0.0, 0.0, 0.0])
                for k in range(3):
                    s[k] += n[k]
    out = []
    for b in mesh.batches:
        row = []
        for v in b.vertices:
            n = acc.get(_key(v), [0.0, 1.0, 0.0])
            ln = sum(x * x for x in n) ** 0.5
            row.append(tuple(x / ln for x in n) if ln > 1e-12 else (0.0, 1.0, 0.0))
        out.append(row)
    return out


def texture_of(chunks: list, tex_num: int):
    """(name, RGBA rows) of a PS2 TEX0 chunk (1-based): +0x88 u16 width, height; +0x90 its PAL0 chunk number,
    +0x94 its MIP0's; +0xA0 its name."""
    t = chunks[tex_num - 1].data
    pal_num, mip_num = struct.unpack_from('<II', t, 0x90)
    raw = t[0xA0:]
    name = raw[:raw.index(b'\0')].decode('latin-1') if b'\0' in raw else raw.decode('latin-1')
    if not (0 < mip_num <= len(chunks) and 0 < pal_num <= len(chunks)):
        raise ValueError(f'TEX0 {tex_num} names chunks {mip_num} and {pal_num}, not in the bundle')
    _w, _h, rgba = read_texture(chunks[mip_num - 1].data, chunks[pal_num - 1].data)
    return name, rgba


def csm1(i: int) -> int:
    """A palette index in the GS's CSM1 order: in each 32, colors 8-15 and 16-23 change places."""
    return (i & ~0x18) | ((i & 0x08) << 1) | ((i & 0x10) >> 1)


def unswizzle8_index(w: int, h: int):
    """For each pixel (row major) where it is in the GS's 8 bit swizzled layout (PSMT8 written as 32 bit)."""
    import numpy as np
    y, x = np.mgrid[0:h, 0:w]
    block = (y & ~0xF) * w + (x & ~0xF) * 2
    swap = (((y + 2) >> 2) & 1) * 4
    py = (((y & ~3) >> 1) + (y & 1)) & 7
    column = py * w * 2 + ((x + swap) & 7) * 4
    byte = ((y >> 1) & 1) + ((x >> 2) & 2)
    return (block + column + byte).ravel()


def unswizzle4_index(w: int, h: int):
    """For each pixel (row major) its byte in the GS's 4 bit swizzled layout (PSMT4 written as 32 bit), and
    whether it is that byte's high nibble."""
    import numpy as np
    y, x = np.mgrid[0:h, 0:w]
    pages_h, pages_v = (w + 127) // 128, (h + 127) // 128
    page = ((y & ~0x7F) // 128) * pages_h + (x & ~0x7F) // 128
    page_loc = (page // pages_v) * 32 * h * 2 + (page % pages_v) * 64 * 4
    lx, ly = x & 0x7F, y & 0x7F
    block = ((lx & ~0x1F) >> 1) * h + (ly & ~0xF) * 2
    swap = (((y + 2) >> 2) & 1) * 4
    py = (((y & ~3) >> 1) + (y & 1)) & 7
    column = py * h * 2 + ((x + swap) & 7) * 4
    byte = (x >> 3) & 3
    return (page_loc + block + column + byte).ravel(), ((y >> 1) & 1).ravel().astype(bool)


def texture_info(mip: bytes) -> dict:
    """The MIP0 header's fields: +0x28 pixel format (5: 8 bit, 256 colors; 6: 4 bit, 16 colors), +0x29 extra mip
    levels, +0x2A u16 (unknown), +0x2C width, +0x2E height."""
    psm, extra, unk, w, h = struct.unpack_from('<BBHHH', mip, 0x28)
    # +0x30: how the pixels went to the GS: 1, as 32 bit (so they are swizzled), else the pixel format (linear)
    return {'psm': psm, 'bits': 4 if psm == 6 else 8, 'levels': extra + 1, 'unknown': unk, 'width': w, 'height': h,
            'swizzled': mip[0x30] == 1}


def read_texture(mip: bytes, pal: bytes, swizzled=None):
    """(width, height, RGBA rows) of a PS2 texture's first level: the pixels after MIP0's 0x60 byte header, PAL0's
    colors after its 16 byte header (256, or 16 for a 4 bit texture), alpha 0x80 opaque."""
    import numpy as np
    info = texture_info(mip)
    w, h = info['width'], info['height']
    swizzled = info['swizzled'] if swizzled is None else swizzled
    raw = np.frombuffer(mip[0x60:], np.uint8)
    if info['bits'] == 4:
        if swizzled:
            at, high = unswizzle4_index(w, h)
            b = raw[at]
            pixels = np.where(high, b >> 4, b & 15).astype(np.uint8)
        else:
            packed = raw[:w * h // 2]
            pixels = np.empty(w * h, np.uint8)
            pixels[0::2], pixels[1::2] = packed & 15, packed >> 4
        colors = np.frombuffer(pal[16:16 + 64], np.uint8).reshape(16, 4).copy()
    else:
        pixels = raw[:w * h]
        if swizzled:
            pixels = pixels[unswizzle8_index(w, h)]
        colors = np.frombuffer(pal[16:16 + 1024], np.uint8).reshape(256, 4).copy()
        colors = colors[[csm1(i) for i in range(256)]]
    colors[:, 3] = np.minimum(colors[:, 3].astype(np.int32) * 255 // 128, 255).astype(np.uint8)
    return w, h, colors[pixels].reshape(h, w, 4)


# ---------------------------------------------------------------------------------------------------------------
# writing

RIGID_BASES = (210, 482, 755)  # VU1 buffers of a mesh without bones
MAX_SKINNED, MAX_RIGID = 56, 70  # vertices a batch (the shipped meshes' most; a skinned batch's blends sit at +174)
PS2_ALIGN = 16  # the shipped .rsc chunks end on 16 bytes
MAX_LEVEL = 58  # vertices a level batch (the shipped levels' most)
POS_RANGE = 4.0 - 1.0 / 8192  # 16 bit positions reach the center +-4


def vu_bases(bones: int) -> tuple:
    """The three VU1 buffers the batches take turns in: above the bone matrices (4 qwords each from 210)."""
    if not bones:
        return RIGID_BASES
    b0 = BONE_BASE + BONE_STRIDE * bones
    step = (1024 - b0) // 3
    return (b0, b0 + step, b0 + 2 * step)


class _Vif:
    def __init__(self):
        self.b = bytearray()

    def cmd(self, c, num=0, imm=0):
        self.b += struct.pack('<I', (c << 24) | (num << 16) | (imm & 0xFFFF))

    def data(self, raw: bytes):
        self.b += raw
        self.b += bytes(-len(raw) % 4)

    def mask(self, m):
        self.cmd(0x20)
        self.b += struct.pack('<I', m)

    def row(self, *r):
        self.cmd(0x30)
        self.b += struct.pack('<4I', *r)

    def pad(self, mod, rem=0):
        while len(self.b) % mod != rem:
            self.cmd(0x00)


def _u16(v):
    return max(0, min(0xFFFF, int(round(v))))


def encode_batch(v: _Vif, verts: list, base: int, skinned: bool, center, floats: bool):
    """One batch: verts [(position (bone relative when skinned), normal, uv, bone, skip)]."""
    n = len(verts)
    v.mask(0x40404040)
    v.cmd(0x05, imm=0)
    v.cmd(0x01, imm=0x0103)  # STCYCL 3,1: st, normal, position a vertex
    w2 = ((n + 26) << 16) | (0x1116 if skinned else 0x0112)  # words 2 and 3: not read by the VU program
    w3 = ((2 * n + 23) << 16 | (4 * n + 40)) if skinned else ((36 + (7 * n + 3) // 4) << 16 | (4 * n + 52))
    v.cmd(0x6C, 1, base)
    v.data(struct.pack('<4I', 6 if skinned else 2, 0, w2, w3))  # flags (4: skinned), no blends
    v.cmd(0x6C, 1, base + 1)
    v.data(struct.pack('<4I', 0x8000 | n, 0x30024004, 0x512, n))  # the GIF tag: n vertices, ST RGBAQ XYZ2
    v.mask(0x50505050)
    v.row(0x45000000, 0x45000000, 0x45009000, 0x45008000)
    v.cmd(0x05, imm=1)
    v.cmd(0x75, n, 0x4000 | (base + 2))
    v.data(b''.join(struct.pack('<2H', _u16((uv[0] + UV_BIAS) * 4096), _u16((uv[1] + UV_BIAS) * 4096))
                    for _p, _n, uv, _b, _s in verts))
    v.cmd(0x05, imm=0)
    v.mask(0x40404040)
    v.mask(0x40404040)
    v.row(0x47800000, 0x47800000, 0x47800000, 0x47800080)
    v.cmd(0x05, imm=1)
    v.cmd(0x7A, n, 0x4000 | (base + 3))
    v.data(b''.join(bytes(max(0, min(255, int(round((c + 1.0) * 128)))) for c in nrm) for _p, nrm, _u, _b, _s in verts))
    v.cmd(0x05, imm=0)

    def flag(bone, skip):
        return (0x8000 if skip else 0) | ((BONE_BASE + BONE_STRIDE * bone) if skinned else 0)
    if floats:
        v.cmd(0x6C, n, base + 4)
        v.data(b''.join(struct.pack('<3fI', p[0], p[1], p[2], flag(b, s)) for p, _n, _u, b, s in verts))
        bias = (0.0, 0.0, 0.0)
    else:
        v.row(0x44800000, 0x44800000, 0x44800000, 0)
        v.cmd(0x05, imm=1)
        v.cmd(0x6D, n, 0x4000 | (base + 4))
        v.data(b''.join(struct.pack('<4H', *(_u16((p[k] - center[k] + 4.0) * 8192) for k in range(3)), flag(b, s))
                        for p, _n, _u, b, s in verts))
        v.cmd(0x05, imm=0)
        bias = tuple(1028.0 - c for c in center)
    v.cmd(0x68, 1, 0x28)
    v.data(struct.pack('<3f', *bias))
    v.cmd(0x04, imm=base)  # ITOP
    v.cmd(0x14, imm=0)  # MSCAL 0: the batch program
    v.pad(16, 8)


def encode_level_batch(v: _Vif, verts: list, base: int):
    """One level batch, as the shipped levels have them: verts [(position (world), color rgba (1.0 = 0x80), uv,
    skip)]; three qwords a vertex: st, color, float position."""
    n = len(verts)
    start = len(v.b)
    v.mask(0x40404040)
    v.cmd(0x05, imm=0)
    v.cmd(0x01, imm=0x0103)
    v.cmd(0x6C, 1, base)
    # flags 0 (unlit by normals: drawn with the vertex colors), no blends; words 2 and 3 as shipped (not read)
    v.data(struct.pack('<4I', 0, 0, ((n + 26) << 16) | 0x142, ((2 * n + 27) << 16) | (6 * n + 36)))
    v.cmd(0x6C, 1, base + 1)
    v.data(struct.pack('<4I', 0x8000 | n, 0x30024004, 0x512, n))
    v.mask(0x50505050)
    v.row(0x45000000, 0x45000000, 0x45009000, 0x45008000)
    v.cmd(0x05, imm=1)
    v.cmd(0x75, n, 0x4000 | (base + 2))
    v.data(b''.join(struct.pack('<2H', _u16((uv[0] + UV_BIAS) * 4096), _u16((uv[1] + UV_BIAS) * 4096))
                    for _p, _c, uv, _s in verts))
    v.cmd(0x05, imm=0)
    v.mask(0x40404040)
    v.cmd(0x6E, n, 0x4000 | (base + 3))
    v.data(b''.join(bytes(max(0, min(255, int(round(x * 128)))) for x in (tuple(c) + (1.0,) * 4)[:4])
                    for _p, c, _u, _s in verts))
    v.cmd(0x6C, n, base + 4)
    v.data(b''.join(struct.pack('<3fI', p[0], p[1], p[2], 0x8000 if s else 0) for p, _c, _u, s in verts))
    v.cmd(0x68, 1, 0x28)
    v.data(struct.pack('<3f', 0.0, 0.0, 0.0))
    v.cmd(0x04, imm=base)  # ITOP
    v.cmd(0x14, imm=0)  # MSCAL 0
    # the shipped levels pad each batch to end 8 bytes past 16 (as the stream is laid out for the DMA) and state its
    # REAL length in word 3 of its first qword -- the game walks a mesh's batches by that length (0x3B05D8: the next
    # batch's STMASK must be there). Writing a fixed 6n + 36 instead left even-sized batches 8 bytes short (the game
    # hung reading vertices), and padding every batch to 6n + 36 to match put odd-sized ones 8 bytes out of step
    # (the screen went black); both found on Coliseum, 2026-11-16
    v.pad(16, 8)
    struct.pack_into('<H', v.b, start + 32, (len(v.b) - start) // 4)


def _strip_batches(strips: list, limit: int) -> list:
    """Strips (vertex lists) packed into batches of at most limit vertices; a strip longer than a batch goes on in
    the next with its last two vertices again. Returns [[strip, ...], ...]."""
    batches, cur, used = [], [], 0
    for s in strips:
        s = list(s)
        while s:
            room = limit - used
            if len(s) <= room:
                cur.append(s)
                used += len(s)
                s = []
            elif room >= 3 and len(s) > limit:
                cur.append(s[:room])
                s = s[room - 2:]
                batches.append(cur)
                cur, used = [], 0
            else:
                batches.append(cur)
                cur, used = [], 0
    if cur:
        batches.append(cur)
    return [b for b in batches if b]


def fit_uvs(verts: list, tris: list) -> tuple:
    """The PS2 keeps texture coordinates in 16 bits, -8 to 8 (UV_BIAS): a triangle reaching past them (a texture
    repeated far, common in models from elsewhere) was clamped and smeared across it. Such a triangle gets its own
    corners moved by whole repeats (the same picture: textures wrap) to sit round 0. -> (verts, tris)."""
    lim = UV_BIAS - 1e-3
    if all(-lim <= c <= lim for v in verts for c in v[2]):
        return verts, tris
    verts = list(verts)
    made, out = {}, []
    for t in tris:
        uvs = [verts[i][2] for i in t]
        if all(-lim <= c <= lim for uv in uvs for c in uv):
            out.append(t)
            continue
        shift = tuple(float(round(sum(uv[a] for uv in uvs) / 3)) for a in range(2))
        nt = []
        for i in t:
            key = (i, shift)
            if key not in made:
                v = verts[i]
                made[key] = len(verts)
                verts.append((v[0], v[1], (v[2][0] - shift[0], v[2][1] - shift[1])) + tuple(v[3:]))
            nt.append(made[key])
        out.append(tuple(nt))
    return verts, out


def mod0(materials: list, bones: int, level: bool = False, floats: bool = False, diffuse=None) -> bytes:
    """A PS2 MOD0. materials: [(TEX0 chunk number or 0, vertices, triangles)], vertices [(position (relative to its
    bone when bones), normal, uv, bone)], triangles [(i, j, k)] into them. bones: the skeleton's bone count (0: a
    rigid or attached mesh). level: a level mesh, lit by its vertex colors: each vertex's normal slot holds its
    color (rgba, 1.0 = full) instead."""
    from . import builder
    skinned = bones > 0 and not level
    limit = MAX_LEVEL if level else MAX_SKINNED if skinned else MAX_RIGID
    bases = vu_bases(0 if level else bones)
    allp = [vv[0] for _t, vs, _tr in materials for vv in vs] or [(0.0, 0.0, 0.0)]
    lo = [min(p[k] for p in allp) for k in range(3)]
    hi = [max(p[k] for p in allp) for k in range(3)]
    center = tuple(round((lo[k] + hi[k]) / 2 * 16) / 16 for k in range(3))
    # float positions when they reach past 16 bits' range -- or asked for (a level's animated object: world space)
    floats = floats or any(abs(p[k] - center[k]) >= POS_RANGE for p in allp for k in range(3))
    radius = max(sum((p[k] - center[k]) ** 2 for k in range(3)) ** 0.5 for p in allp)
    streams, counts = [], []
    for tex, verts, tris in materials:
        tris = [tuple(t) for t in tris if len(set(t)) == 3]  # a triangle on two corners draws nothing
        verts, tris = fit_uvs(verts, tris)
        strips = [list(pr.indices) for pr in builder._strips(tris)]
        batches = _strip_batches(strips, limit)
        v = _Vif()
        v.cmd(0x00)
        v.cmd(0x00, imm=len(batches))
        v.cmd(0x6C, 1, 0x29)
        v.data(struct.pack('<4f', center[0], center[1], center[2], radius))
        v.cmd(0x14, imm=2)  # MSCAL 2: the mesh's setup
        nverts = 0
        for bi, batch in enumerate(batches):
            out = []
            for s in batch:
                for k, idx in enumerate(s):
                    p, nrm, uv, bone = verts[idx]
                    out.append((p, nrm, uv, k < 2) if level else (p, nrm, uv, bone, k < 2))
            nverts += len(out)
            if level:
                encode_level_batch(v, out, bases[bi % 3])
            else:
                encode_batch(v, out, bases[bi % 3], skinned, center, floats)
        v.cmd(0x01, imm=0x0101)
        v.cmd(0x11)  # FLUSH
        v.pad(16)
        streams.append(bytes(v.b))
        counts.append(nverts)
    nm = len(materials)
    rec_at = 0x10 + 4 * nm
    draw_at = rec_at + 8 * nm
    color_at = draw_at + 4 * nm
    cols = [tuple(diffuse[k]) if diffuse else (1.0, 1.0, 1.0, 1.0) for k in range(nm)]
    uniq = list(dict.fromkeys(cols))  # a colour block each (0x50 apart), the materials pointing at theirs
    desc_at = color_at + 0x50 * (len(uniq) - 1) + 64 + 4
    start = (desc_at + 8 * nm + 15) & ~15
    head = bytearray(start)
    struct.pack_into('<BBHIII', head, 0, nm, 0 if level else min(bones, 255), 1, 0x10, draw_at, 0)
    offs = []
    at = start
    for s in streams:
        offs.append(at)
        at += len(s)
    for k, (tex, _v, _t) in enumerate(materials):
        struct.pack_into('<I', head, 0x10 + 4 * k, rec_at + 8 * k)
        struct.pack_into('<II', head, rec_at + 8 * k, tex, color_at + 0x50 * uniq.index(cols[k]))
        struct.pack_into('<I', head, draw_at + 4 * k, desc_at + 8 * k)
        struct.pack_into('<IHH', head, desc_at + 8 * k, offs[k], len(streams[k]) // 16, counts[k])
    for j, c in enumerate(uniq):
        struct.pack_into('<4f', head, color_at + 0x50 * j, *c)
        struct.pack_into('<f', head, color_at + 0x50 * j + 60, 1.0)
    return bytes(head) + b''.join(streams)


def texture_chunks(name: str, rgba, pal_num: int, mip_num: int, level: bool = False):
    """MIP0, PAL0, TEX0 for an RGBA image (rows top first): 256 colors, swizzled (as most shipped character
    textures), one level. Sides must be powers of two, at least 16 x 16 for the swizzle."""
    import numpy as np
    from . import tex
    h, w = rgba.shape[:2]
    if w & (w - 1) or h & (h - 1) or w < 16 or h < 16:
        raise ValueError(f'texture {name}: sides must be powers of two of at least 16, {w} x {h} is not')
    pal, ix = tex.quantize(np.ascontiguousarray(rgba, np.uint8), 256)
    pixels = np.empty(w * h, np.uint8)
    pixels[unswizzle8_index(w, h)] = np.asarray(ix, np.uint8).ravel()
    mip = bytearray(0x60)
    struct.pack_into('<III', mip, 0, 1, 0, 0x60)
    struct.pack_into('<BBHHH', mip, 0x28, 5, 0, w * h // 256, w, h)
    struct.pack_into('<BBH', mip, 0x30, 1, 1, h // 2)
    struct.pack_into('<I', mip, 0x48, w * h // 256)
    struct.pack_into('<I', mip, 0x58, max(1, w // 64))
    colors = np.ascontiguousarray(pal, np.uint8).reshape(-1, 4)[:256].copy()
    if len(colors) < 256:
        colors = np.vstack([colors, np.zeros((256 - len(colors), 4), np.uint8)])
    colors[:, 3] = (colors[:, 3].astype(np.int32) * 128 + 127) // 255
    stored = colors[[csm1(i) for i in range(256)]]  # the swap is its own inverse
    palc = bytes.fromhex('01000001010000000400000000000000') + stored.tobytes()
    t = bytearray(0xC0)
    struct.pack_into('<IHHHHIII', t, 0x84, 0xA0, w, h, 0, 6, pal_num, mip_num, 1)
    if level:  # every shipped level texture has it (character textures: 0); without it they draw as noise
        struct.pack_into('<I', t, 0x70, 0x00010000)
    raw = name.encode('latin-1')[:0xC0 - 0xA0 - 1]
    t[0xA0:0xA0 + len(raw)] = raw
    return [('MIP0', bytes(mip) + pixels.tobytes()), ('PAL0', palc), ('TEX0', bytes(t))]


def model_input(m, tex_num: dict, level: bool = False):
    """A built model (mod.Model; skinned positions relative to their bones, as the PS2 .mod has them) as mod0's
    input: (materials, bones). tex_num: texture name (lower case) -> TEX0 chunk number. level: each vertex carries
    its color (the baked light) where the normal goes."""
    from . import builder
    tris, mats = builder.triangles_of(m)
    per = {}
    for t in tris:
        verts, index, out = per.setdefault(t.material, ([], {}, []))
        tri = []
        for c in t.corners:
            second = tuple(c.color) if level else tuple(c.normal)
            key = (tuple(round(x, 6) for x in c.position), tuple(round(x, 4) for x in second),
                   tuple(round(x, 6) for x in c.uv1), c.bone)
            if key not in index:
                index[key] = len(verts)
                verts.append((c.position, second, c.uv1, c.bone))
            tri.append(index[key])
        out.append(tuple(tri))
    materials = []
    for k in sorted(per):
        mat = mats[k]
        tex = tex_num.get(mat.textures[0].lower(), 0) if mat.textures else 0
        verts, _index, out = per[k]
        materials.append((tex, verts, out))
    nb = m.header_totals[2] if m.header_totals else 1
    return materials, (nb if nb > 1 else 0)


def build(textures: list, meshes: list, max_side: int = 256) -> tuple:
    """(.rsc, .rnt) from textures [(name, rgba)] and meshes [(name, model)]: the textures first (each MIP0 PAL0 TEX0,
    at most max_side a side: the PS2 has 4 MB of video memory), then a MOD0 a mesh; the .rnt names both."""
    from .rscwrite import write_bundle, write_names
    try:
        from .. import texfix
        fit = lambda img: texfix.power_of_two(img, max_side)
    except ImportError:  # the formats alone (tests): textures as given
        fit = lambda img: img
    chunks, tex_num, entries = [], {}, []
    for name, rgba in textures:
        n = len(chunks)
        chunks += texture_chunks(name, fit(rgba), pal_num=n + 2, mip_num=n + 1)
        tex_num[name.lower()] = n + 3
        entries.append(('TEX0', n + 3, name))
    for name, m in meshes:
        materials, bones = model_input(m, tex_num)
        chunks.append(('MOD0', mod0(materials, bones)))
        entries.append(('MOD0', len(chunks), name))
    return write_bundle(chunks), write_names(entries)


# ---------------------------------------------------------------------------------------------------------------
# levels (lvl_<name>.rsc): rebuilt keeping every chunk's number, as the zone (.znes) and bound (.bnds) files name
# them by number

def empty_mod0() -> bytes:
    """A level mesh that draws nothing (one material, no batches): stands in for a mesh taken out."""
    zero = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 0.0), (0.0, 0.0), 0)
    return mod0([(0, [zero, zero, zero], [(0, 1, 2)])], 0, level=True)  # one triangle of no size


def build_level(base: list, replace_meshes: dict, new_meshes: list, new_textures: list, replace_textures: dict,
                texture_numbers: dict, remove=frozenset(), max_side: int = 256) -> tuple:
    """A PS2 level bundle from the one it was imported from (rsc.Chunk list), as rscwrite.build_level does for the
    Xbox: models are mod.Model (world space, colors = the baked light). Returns (bundle, the new meshes' chunk
    numbers)."""
    from .rscwrite import write_bundle
    try:
        from .. import texfix
        fit = lambda img: texfix.power_of_two(img, max_side)
    except ImportError:
        fit = lambda img: img
    chunks = [(c.tag, c.data) for c in base]
    numbers = dict(texture_numbers)
    for num, rgba in replace_textures.items():
        t = base[num - 1].data
        pal_num, mip_num = struct.unpack_from('<II', t, 0x90)
        raw = t[0xA0:]
        name = raw[:raw.index(b'\0')].decode('latin-1') if b'\0' in raw else ''
        mip, pal, tex0 = texture_chunks(name, fit(rgba), pal_num, mip_num, level=True)
        chunks[mip_num - 1], chunks[pal_num - 1], chunks[num - 1] = mip, pal, tex0
    for name, rgba in new_textures:
        n = len(chunks)
        chunks += texture_chunks(name, fit(rgba), pal_num=n + 2, mip_num=n + 1, level=True)
        numbers[name.lower()] = n + 3
    for num, m in replace_meshes.items():
        chunks[num - 1] = ('MOD0', mod0(model_input(m, numbers, level=True)[0], 0, level=True))
    for num in remove:
        if num not in replace_meshes:
            chunks[num - 1] = ('MOD0', empty_mod0())
    added = []
    for m in new_meshes:
        chunks.append(('MOD0', mod0(model_input(m, numbers, level=True)[0], 0, level=True)))
        added.append(len(chunks))
    return write_bundle(chunks, PS2_ALIGN), added
