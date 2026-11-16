"""Red Dead Revolver resource bundles: .xrsc (Xbox) with their .xrnt name tables, as the game loads them (the
bundle reader sub_00083E00, the chunk fix-ups MOD0 sub_00184230, TEX0 sub_0017FB80, MIP0 sub_00187710,
PAL0 sub_00185B10).

    .xrsc: "RSC0", u32 chunk count, u32 header size (0x80), then (tag, end offset) per chunk; a chunk starts where the
           one before it ends, the first at the header's end
    .xrnt: "RNT0", u32 count, 16 bytes, u32 names offset, then (tag, chunk number) per name, then the names

Chunks are the original tool's memory saved as it was (MSVC debug heap fill 0xFD / 0xDD shows between objects), with
every pointer an offset from the chunk's start.

MIP0  Xbox D3DTexture (Common 0x00040001 at +0x6C, its Format at +0x78, e.g. 0x08810B29 = P8 256x256 one level),
      then the pixels from +0x80, swizzled (Z order)
PAL0  Xbox D3DPalette, then 256 BGRA colors from +0x40
TEX0  128 bytes: the texture's name at +0x24
MOD0  a mesh:
      +0x00 u8 material count, +0x02 u16 0x7530 (0x4E20 once loaded)
      +0x04 materials (pointers), +0x08 batches (a pointer per material), +0x0C the D3DVertexBuffer
      +0x10 vertex shader (-13 = the game's skinned one) and +0x14 u16 vertex format, u16 stride
      +0x18 the u16 index buffer
      material: +0 texture, +4 texture (each a name hash, or bundle slot << 14 | chunk number), +8 its colors
      batch: u16 packets, u16 vertices, +4 packet records
      packet record, 20 bytes: u16 flag (0 mostly, 1 on ~8%: nothing in the data predicts it; 0 draws), u16 format,
                               u16 stride, u16 primitive (6 strip), u16 vertices, u16 indices,
                               u16 first vertex, u16 first index, +0x10 its bones
      bones: u8 count, then the bone numbers, padded with 0xFD to a multiple of 16 bytes
      vertex, skinned (format 0x111A, 44 bytes): position, 4 bytes 16 + 3 x matrix slot (the vertex shader's
              registers, three a bone), weights w0 w1 (w2 = 1 - w0 - w1), normal, uv
      indices: each packet's strips joined into one, strips stitched by repeating an index at each join
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

HEADER = 0x80
STRIP = 6
BONE_REGISTER = 16  # the vertex shader's first bone register, three registers a bone


@dataclass
class Chunk:
    tag: str
    start: int
    end: int
    data: bytes


def read_chunks(data: bytes) -> list[Chunk]:
    if data[:4] != b'RSC0':
        raise ValueError('not an RSC0 bundle')
    n, hs = struct.unpack_from('<II', data, 4)
    out, start = [], hs
    for i in range(n):
        tag = data[12 + i * 8:16 + i * 8].decode('latin-1')
        end = struct.unpack_from('<I', data, 16 + i * 8)[0]
        out.append(Chunk(tag, start, end, data[start:end]))
        start = end
    return out


def read_names(data: bytes) -> list[tuple[str, int, str]]:
    """(tag, chunk number, name) per entry of a .xrnt: "RNT0", u32 count, 16 bytes, then 12 bytes an entry (u32 name
    offset, tag, u32 chunk number counted from 1), then the names. A material's texture is a chunk number too."""
    if data[:4] != b'RNT0':
        raise ValueError('not an RNT0 name table')
    n = struct.unpack_from('<I', data, 4)[0]
    out = []
    for i in range(n):
        name_at, tag, num = struct.unpack_from('<I4sI', data, 0x18 + 12 * i)
        e = data.index(b'\0', name_at)
        out.append((tag.decode('latin-1'), num, data[name_at:e].decode('latin-1')))
    return out


@dataclass
class Packet:
    format: int
    stride: int
    primitive: int
    vertices: int
    indices: int
    first_vertex: int
    first_index: int
    bones: list[int] = field(default_factory=list)


@dataclass
class Vertex:
    position: tuple
    normal: tuple
    uv: tuple
    slots: tuple = (0, 0, 0, 0)  # matrix slots in its packet
    weights: tuple = (1.0, 0.0, 0.0)
    color: tuple | None = None  # rgba 0..1, level meshes' baked lighting
    uv2: tuple | None = None


@dataclass
class Mesh:
    flags: int
    format: int
    stride: int
    shader: int
    materials: list = field(default_factory=list)  # (texture ref, texture ref, color block offset)
    batches: list = field(default_factory=list)  # per material, its packets
    vertices: list = field(default_factory=list)
    indices: list = field(default_factory=list)
    header_floats: tuple = ()
    colors: list = field(default_factory=list)  # per material: (diffuse, specular) rgba


def read_mod0(c: bytes) -> Mesh:
    nmat, flags, magic = struct.unpack_from('<BBH', c, 0)
    mats_at, batches_at, vb_at, shader, fmt, stride, ib_at = struct.unpack_from('<IIIiHHI', c, 4)
    mesh = Mesh(flags, fmt, stride, shader, header_floats=struct.unpack_from('<4f', c, 0x1C))
    for i in range(nmat):
        m = struct.unpack_from('<I', c, mats_at + 4 * i)[0]
        mesh.materials.append(struct.unpack_from('<III', c, m))
        col_at = mesh.materials[-1][2]
        if col_at and col_at + 68 <= len(c):
            f = struct.unpack_from('<17f', c, col_at)  # D3DMATERIAL8: diffuse, ambient, specular, emissive, power
            mesh.colors.append((f[0:4], f[8:12]))
        else:
            mesh.colors.append(((1.0, 1.0, 1.0, 1.0), (0.0, 0.0, 0.0, 1.0)))
        b = struct.unpack_from('<I', c, batches_at + 4 * i)[0]
        count, _total, recs = struct.unpack_from('<HHI', c, b)
        pks = []
        for k in range(count):
            flag, f, s, prim, nv, ni, fv, fi, bones_at = struct.unpack_from('<HHHH4HI', c, recs + 20 * k)
            nb = c[bones_at] if bones_at else 0  # plain packets: none
            pks.append(Packet(f, s, prim, nv, ni, fv, fi, list(c[bones_at + 1:bones_at + 1 + nb])))
        mesh.batches.append(pks)
    data_at = struct.unpack_from('<I', c, vb_at + 4)[0]
    nverts = sum(p.vertices for b in mesh.batches for p in b)
    for v in range(nverts):
        o = data_at + v * stride
        pos = struct.unpack_from('<3f', c, o)
        if fmt == 0x111A:
            regs = c[o + 12:o + 16]
            w0, w1 = struct.unpack_from('<2f', c, o + 16)
            nrm = struct.unpack_from('<3f', c, o + 24)
            uv = struct.unpack_from('<2f', c, o + 36)
            slots = tuple((r - BONE_REGISTER) // 3 for r in regs)
            mesh.vertices.append(Vertex(pos, nrm, uv, slots, (w0, w1, 1.0 - w0 - w1)))
        elif fmt == 0x1116:  # one bone: position, its 4 bone bytes, normal, uv
            slots = tuple((r - BONE_REGISTER) // 3 for r in c[o + 12:o + 16])
            nrm = struct.unpack_from('<3f', c, o + 16)
            uv = struct.unpack_from('<2f', c, o + 28)
            mesh.vertices.append(Vertex(pos, nrm, uv, slots, (0.0, 0.0, 1.0)))
        elif fmt in (0x142, 0x242):  # levels: position, color (D3DCOLOR, bytes b g r a), uv (, uv2); no normal
            b, g, r, a = c[o + 12:o + 16]
            uv = struct.unpack_from('<2f', c, o + 16)
            uv2 = struct.unpack_from('<2f', c, o + 24) if fmt == 0x242 else None
            mesh.vertices.append(Vertex(pos, None, uv, color=(r / 255, g / 255, b / 255, a / 255), uv2=uv2))
        elif fmt == 0x212:  # faces: position, normal, uv, uv2
            nrm = struct.unpack_from('<3f', c, o + 12)
            uv, uv2 = struct.unpack_from('<2f', c, o + 24), struct.unpack_from('<2f', c, o + 32)
            mesh.vertices.append(Vertex(pos, nrm, uv, uv2=uv2))
        elif fmt == 0x112:
            nrm = struct.unpack_from('<3f', c, o + 12)
            uv = struct.unpack_from('<2f', c, o + 24)
            mesh.vertices.append(Vertex(pos, nrm, uv))
        else:
            raise ValueError(f'vertex format {fmt:#x} (stride {stride}) is not read yet')
    nidx = sum(p.indices for b in mesh.batches for p in b)
    mesh.indices = list(struct.unpack_from(f'<{nidx}H', c, ib_at))
    return mesh


def strip_triangles(ix):
    """A joined strip's triangles, every other one turned, the degenerate joins dropped."""
    out = []
    for i in range(len(ix) - 2):
        a, b, cc = ix[i], ix[i + 1], ix[i + 2]
        if a == b or b == cc or a == cc:
            continue
        out.append((b, a, cc) if i % 2 else (a, b, cc))
    return out


# ------------------------------------------------------------------------------------------------------------------
# back to models and pictures, for importing a bundle

XBOX_P8, XBOX_DXT1, XBOX_DXT3, XBOX_DXT5 = 0x0B, 0x0C, 0x0E, 0x0F


def _swizzle_order(w, h):
    from .rscwrite import swizzle
    return swizzle(w, h)


def read_texture(chunks: list, tex_num: int):
    """(name, RGBA rows in the texture files' order, as tex.load gives) for a TEX0 chunk number; None when its picture
    is in a format this does not read."""
    import numpy as np
    from . import tex
    t = chunks[tex_num - 1].data
    name = t[0x24:t.index(b'\0', 0x24)].decode('latin-1') if b'\0' in t[0x24:] else ''
    pal_num, mip_num = struct.unpack_from('<II', t, 0x14)
    if not 0 < mip_num <= len(chunks):
        return name, None
    mip = chunks[mip_num - 1].data
    fmt = struct.unpack_from('<I', mip, 0x78)[0]
    kind, w, h = (fmt >> 8) & 0xFF, 1 << ((fmt >> 20) & 15), 1 << ((fmt >> 24) & 15)
    data = mip[0x80:]
    if kind == XBOX_P8 and 0 < pal_num <= len(chunks):
        pal = np.frombuffer(chunks[pal_num - 1].data[0x40:0x440], np.uint8).reshape(256, 4)[:, [2, 1, 0, 3]]
        ix = np.frombuffer(data[:w * h], np.uint8)[_swizzle_order(w, h)].reshape(h, w)
        return name, pal[ix]
    dxt = {XBOX_DXT1: tex.DXT1, XBOX_DXT3: tex.DXT3, XBOX_DXT5: tex.DXT5}.get(kind)
    if dxt:
        return name, tex._decode_dxt(data[:tex.level_size(dxt, w, h)], dxt, w, h)
    return name, None


def _face_normals(mesh: Mesh) -> list:
    """Smooth normals for meshes stored without any (levels): each vertex the sum of its triangles' normals, shared
    across vertices at the same place."""
    acc = {}
    keys = [tuple(round(x, 4) for x in v.position) for v in mesh.vertices]
    for b in mesh.batches:
        for p in b:
            for t in strip_triangles(mesh.indices[p.first_index:p.first_index + p.indices]):
                a, bb, cc = (mesh.vertices[i].position for i in t)
                u = [bb[k] - a[k] for k in range(3)]
                w = [cc[k] - a[k] for k in range(3)]
                n = (u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0])
                for i in t:
                    s = acc.setdefault(keys[i], [0.0, 0.0, 0.0])
                    for k in range(3):
                        s[k] += n[k]
    out = []
    for k in keys:
        n = acc.get(k, [0.0, 1.0, 0.0])
        ln = sum(x * x for x in n) ** 0.5 or 1.0
        out.append(tuple(x / ln for x in n))
    return out


def mod0_to_model(mesh: Mesh, texture_names: dict):
    """A compiled mesh as a model (mod.Model, 2.12 shapes): its packets, strips and bones as they are, the blend
    weights back as reskins (offsets left 0: the game's compiled meshes do not keep them). texture_names: TEX0 chunk
    number -> name for the materials."""
    from . import mod
    m = mod.Model(version='2.12')
    pos, nrm, uvs, uv2s, cols = {}, {}, {}, {}, {}
    m.uv2s, m.colors = [], []
    normals = None
    if mesh.vertices and mesh.vertices[0].normal is None:
        normals = _face_normals(mesh)

    def pool(d, lst, v):
        k = tuple(round(x, 6) for x in v)
        if k not in d:
            d[k] = len(lst)
            lst.append(k)
        return d[k]
    nbones = 0
    for mi, (pks, (ref, _r2, _c)) in enumerate(zip(mesh.batches, mesh.materials)):
        name = texture_names.get(ref & 0x3FFF) if ref and not ref & 0xFFFF0000 else None
        second = texture_names.get(_r2 & 0x3FFF) if _r2 and not _r2 & 0xFFFF0000 else None
        mat = mod.Material(name=name or 'untextured',
                           textures=([name] if name else []) + ([second] if second and name else []))
        if mi < len(mesh.colors):
            mat.diffuse, mat.specular = mesh.colors[mi]
        for p in pks:
            pk = mod.Packet(matrices=list(p.bones) or [0])
            for k in range(p.vertices):
                vi = p.first_vertex + k
                v = mesh.vertices[vi]
                a = mod.Adjunct(pool(pos, m.positions, v.position),
                                pool(nrm, m.normals, v.normal if v.normal is not None else normals[vi]),
                                pool(cols, m.colors, v.color or (1.0, 1.0, 1.0, 1.0)),
                                pool(uvs, m.uv1s, v.uv), pool(uv2s, m.uv2s, v.uv2 or (0.0, 0.0)), 0)
                if mesh.format in (0x111A, 0x1116) and p.bones:
                    a.matrix = min(v.slots[0], len(p.bones) - 1)
                if mesh.format == 0x111A:
                    w0, w1 = v.weights[0], v.weights[1]  # shares of the slots in bytes 1 and 2
                    r2 = w1
                    r1 = w0 / (1.0 - r2) if r2 < 1.0 else 0.0
                    for slot, r in ((v.slots[1], r1), (v.slots[2], r2)):
                        if r > 1e-5 and slot != v.slots[0] and slot < len(p.bones):
                            pk.reskins.append(struct.pack('<HH4f', k, slot, r, 0.0, 0.0, 0.0))
                pk.adjuncts.append(a)
            local = [i - p.first_vertex for i in mesh.indices[p.first_index:p.first_index + p.indices]]
            pk.primitives = [mod.Primitive(mod.STRIP, local)]
            nbones = max(nbones, max(p.bones, default=0) + 1)
            mat.packets.append(pk)
        m.materials.append(mat)
    m.header_totals = (sum(len(p.adjuncts) for p in m.packets()), 0, nbones if mesh.format != 0x112 else 1, 0)
    return m
