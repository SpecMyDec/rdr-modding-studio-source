"""Writing Red Dead Revolver .xrsc bundles and .xrnt name tables (the layouts are in rsc.py)."""
from __future__ import annotations

import struct
from collections import defaultdict

from . import mod as modfmt
from .rsc import BONE_REGISTER, HEADER, STRIP

MAX_BONES = 58  # a packet's bones: three vertex shader registers each from register 16 (shipped packets use 25)
# vertex formats (Xbox FVF codes) with their stride and vertex shader:
SKINNED = 0x111A  # position, 2 weights, 4 bone bytes, normal, uv: 44 bytes, the game's blending shader -13
RIGID = 0x1116    # position, 4 bone bytes (one bone), normal, uv: 36 bytes, shader -14
PLAIN = 0x112     # position, normal, uv: 32 bytes; the loader picks its shader by the format
LIT = 0x142       # levels: position, color (baked light), uv: 24 bytes
LIT2 = 0x242      # levels: position, color, uv, uv2 (a second texture): 32 bytes
FORMATS = {SKINNED: (44, -13), RIGID: (36, -14), PLAIN: (32, PLAIN), LIT: (24, LIT), LIT2: (32, LIT2)}
ALIGN = 0x80  # chunks end on 0x80 (the game loads bundles 0x80 aligned)


def swizzle(width: int, height: int):
    """Where each row-major pixel goes in the Xbox's swizzled (Z order) layout, for power-of-two sides."""
    import numpy as np

    def spread(v, bits):
        r = np.zeros_like(v)
        for b in range(bits):
            r |= ((v >> b) & 1) << (2 * b)
        return r
    small = min(width, height).bit_length() - 1
    xs, ys = np.arange(width), np.arange(height)
    # the shorter side's bits interleave with the longer's; the longer side's high bits go on top
    sx = spread(xs & ((1 << small) - 1), small) | ((xs >> small) << (2 * small) if width > height else 0)
    sy = (spread(ys & ((1 << small) - 1), small) << 1) | ((ys >> small) << (2 * small) if height > width else 0)
    return (sy[:, None] | sx[None, :]).ravel()


def texture_chunks(name: str, rgba, pal_num: int, mip_num: int):
    """MIP0, PAL0, TEX0 for an RGBA image (rows in the texture file's order): 256 colors, swizzled, one level, as
    most shipped character textures are."""
    import numpy as np
    from . import tex
    h, w = rgba.shape[:2]
    if w < 1 or h < 1:
        raise ValueError(f'texture {name} has no picture (0 x 0): its image file is missing, did not load, or the '
                         'image was made in Blender and never saved. Open or save the image, then export again')
    if w & (w - 1) or h & (h - 1) or w > 4096 or h > 4096:
        raise ValueError(f'texture {name} must have power of two sides, {w} x {h} has not')
    pal, ix = tex.quantize(np.ascontiguousarray(rgba, np.uint8), 256)
    pixels = np.empty(w * h, np.uint8)
    pixels[swizzle(w, h)] = ix.ravel()
    lw, lh = w.bit_length() - 1, h.bit_length() - 1
    mip = bytearray(0x80)
    struct.pack_into('<III', mip, 0, 1, 0, 0x6C)
    mip[0x0C], mip[0x0D] = 5, 0  # 5, levels - 1
    struct.pack_into('<HH', mip, 0x0E, w, h)
    fmt = 0x29 | (0x0B << 8) | (1 << 16) | (lw << 20) | (lh << 24)  # P8, one level, the sides' powers of two
    struct.pack_into('<IIII', mip, 0x6C, 0x00040001, 0x80, 0, fmt)  # D3DTexture: common, data, lock, format
    mip += pixels.tobytes()
    palc = bytearray(0x40)
    struct.pack_into('<HHII', palc, 0, 1, 256, 0x00030001, 0)  # count; D3DPalette: common, data
    struct.pack_into('<I', palc, 0x10, 0x40)
    palc += pal[:, [2, 1, 0, 3]].tobytes() + bytes(0x40)
    t = bytearray(0x80)
    struct.pack_into('<IIIHHHHIIII', t, 0, 0, 0, 0x24, w, h, 0, 6, pal_num, mip_num, 1, 0)
    raw = name.encode('latin-1')[:0x80 - 0x24 - 1]
    t[0x24:0x24 + len(raw)] = raw
    return [('MIP0', bytes(mip)), ('PAL0', bytes(palc)), ('TEX0', bytes(t))]


def join_strips(strips: list) -> list:
    """One strip from many: a join repeats the last index and the next strip's first, and the first once more when
    the next strip would otherwise start on the wrong turn. strips: (indices, turned), turned meaning the strip's first
    triangle is read with its first two corners swapped (the .xmod's type 2)."""
    out: list = []
    for ix, turned in strips:
        if not ix:
            continue
        if out:
            out += [out[-1], ix[0]]
        # the strip's first triangle starts at len(out); the joined strip turns every odd position
        if (len(out) % 2 == 1) != turned:
            out.append(ix[0])
        out += ix
    return out


def strip_primitives(prims):
    """A packet's primitives as strips (the Xbox mesh is all strips): a triangle list -- a model read from a PS2
    bundle has them -- made into strips. Read as one strip it joined corners of different triangles: long spikes
    across every PS2 level converted for the Xbox (2026-11-17)."""
    out = []
    for prim in prims:
        if prim.type == modfmt.TRIANGLES:
            from . import builder
            tris = [tuple(t) for t in prim.triangles() if len(set(t)) == 3]
            if tris:
                out += builder._strips(tris)
        else:
            out.append(prim)
    return out


def mod0(m, texture_ref, level=False, keep_uv2=True) -> bytes:
    """A MOD0 from a model. texture_ref(name) gives a material's TEX0 chunk
    number (0 for none). Skinned when the model has more than one bone; weights come from its reskins (the second
    blends over the first: w0 = r1 (1 - r2), w1 = r2, the rest the adjunct's own bone). level: a level mesh, lit by
    its vertex colors (no normals), with a second texture on its second uvs when a material names two."""
    nbones = max([m.header_totals[2] if m.header_totals else 0] +
                 [max(pk.matrices, default=-1) + 1 for pk in m.packets()])
    skinned = nbones > 1 and not level
    blended = skinned and any(pk.reskins for pk in m.packets())
    if level:
        # a second uv set only for a mesh that had one in the level (0x242) or draws a second texture; a new
        # mesh's second uv map (common in models from elsewhere) would cost 8 bytes a vertex for nothing
        has_uv2 = keep_uv2 and len({tuple(round(x, 6) for x in uv) for uv in m.uv2s}) > 1
        fmt = LIT2 if has_uv2 or any(len(mat.textures) > 1 for mat in m.materials) else LIT
    else:
        fmt = SKINNED if blended else RIGID if skinned else PLAIN
    stride, shader = FORMATS[fmt]
    pks_by_mat = [list(mat.packets) for mat in m.materials]
    for pks in pks_by_mat:
        for pk in pks:
            if len(pk.matrices) > MAX_BONES:
                raise ValueError(f'a packet has {len(pk.matrices)} bones; bundles take {MAX_BONES}')
    nmat = len(m.materials)
    out = bytearray(0x2C)
    mats_at = len(out)
    out += bytes(4 * nmat)
    mat_at = []
    for i, mat in enumerate(m.materials):
        struct.pack_into('<I', out, mats_at + 4 * i, len(out))
        mat_at.append(len(out))
        ref = texture_ref(mat.textures[0]) if mat.textures else 0
        ref2 = texture_ref(mat.textures[1]) if fmt == LIT2 and len(mat.textures) > 1 else 0
        out += struct.pack('<III', ref, ref2, 0)
    for i, mat in enumerate(m.materials):
        struct.pack_into('<I', out, mat_at[i] + 8, len(out))
        d = (tuple(mat.diffuse) + (1.0,) * 4)[:4]
        s = (tuple(mat.specular) + (1.0,) * 4)[:4]
        out += struct.pack('<17f', *d, 0, 0, 0, 0, *s, 0, 0, 0, 0, 0.0)  # D3DMATERIAL8, ambient 0 as shipped
    batches_at = len(out)
    out += bytes(4 * nmat)
    batch_at = []
    for i, pks in enumerate(pks_by_mat):
        struct.pack_into('<I', out, batches_at + 4 * i, len(out))
        batch_at.append(len(out))
        out += struct.pack('<HHI', len(pks), sum(len(pk.adjuncts) for pk in pks) & 0xFFFF, 0)
    vertices, indices = bytearray(), []
    first_v = first_i = 0
    bone_lists = []
    idx = '<HH' if not m.wide else '<II'
    wat = 4 if not m.wide else 8
    for i, pks in enumerate(pks_by_mat):
        struct.pack_into('<I', out, batch_at[i] + 4, len(out))
        for pk in pks:
            rsk = defaultdict(list)
            for r in pk.reskins:
                a, b = struct.unpack_from(idx, r)
                rsk[a].append((b, struct.unpack_from('<f', r, wat)[0]))
            for k, a in enumerate(pk.adjuncts):
                p = m.positions[a.position]
                n = m.normals[a.normal]
                uv = m.uv1s[a.uv1] if a.uv1 < len(m.uv1s) else (0.0, 0.0)
                if fmt in (LIT, LIT2):
                    col = m.colors[a.color] if a.color < len(m.colors) else (1.0, 1.0, 1.0, 1.0)
                    r, g, b, al = (max(0, min(255, int(round(x * 255)))) for x in (tuple(col) + (1.0,))[:4])
                    vertices += struct.pack('<3f', *p) + bytes([b, g, r, al]) + struct.pack('<2f', *uv)
                    if fmt == LIT2:
                        vertices += struct.pack('<2f', *(m.uv2s[a.uv2] if a.uv2 < len(m.uv2s) else (0.0, 0.0)))
                elif fmt == RIGID:
                    vertices += struct.pack('<3f', *p) + bytes([BONE_REGISTER + 3 * a.matrix] * 4) + struct.pack(
                        '<3f2f', *n, *uv)
                elif skinned:
                    extra = rsk[k][:2]
                    s0 = a.matrix
                    s1 = extra[0][0] if extra else s0
                    s2 = extra[1][0] if len(extra) > 1 else s1
                    r1 = extra[0][1] if extra else 0.0
                    r2 = extra[1][1] if len(extra) > 1 else 0.0
                    w0, w1 = (r1 * (1 - r2), r2) if extra else (1.0, 0.0)
                    regs = bytes([BONE_REGISTER + 3 * s0, BONE_REGISTER + 3 * s1, BONE_REGISTER + 3 * s2,
                                  BONE_REGISTER])
                    vertices += struct.pack('<3f', *p) + regs + struct.pack('<2f3f2f', w0, w1, *n, *uv)
                else:
                    vertices += struct.pack('<3f3f2f', *p, *n, *uv)
            joined = join_strips([([first_v + i for i in prim.indices], prim.type == modfmt.STRIP_FLIPPED)
                                  for prim in strip_primitives(pk.primitives)])
            if fmt in (SKINNED, RIGID):  # plain and level packets have no bones: a null list, as shipped
                bone_lists.append((len(out), pk.matrices))
            out += struct.pack('<HHHH4HI', 0, fmt, stride, STRIP, len(pk.adjuncts), len(joined), first_v, first_i, 0)
            indices += joined
            first_v += len(pk.adjuncts)
            first_i += len(joined)
    if first_v > 0xFFFF:
        raise ValueError('a mesh holds 65535 vertices at most')
    for rec_at, bones in bone_lists:
        struct.pack_into('<I', out, rec_at + 16, len(out))
        lst = bytes([len(bones)] + list(bones))
        out += lst + b'\xfd' * (-len(lst) % 16)  # count and bones, in 16 byte steps as shipped
    vb_at = len(out)
    out += struct.pack('<III', 0x00000001, vb_at + 12, 0)  # D3DVertexBuffer: common, data, lock
    out += vertices
    ib_at = len(out)
    out += struct.pack(f'<{len(indices)}H', *indices)
    # the bounding sphere: the box's centre and its farthest point (the shipped radius is a little tighter)
    pts = [m.positions[a.position] for pks in pks_by_mat for pk in pks for a in pk.adjuncts]
    lo = [min(q[k] for q in pts) for k in range(3)]
    hi = [max(q[k] for q in pts) for k in range(3)]
    ctr = [(lo[k] + hi[k]) / 2 for k in range(3)]
    rad = max(sum((q[k] - ctr[k]) ** 2 for k in range(3)) for q in pts) ** 0.5
    # +1: the skeleton's bone count for skinned meshes (as shipped), 0 otherwise
    struct.pack_into('<BBHIIIiHHI4f', out, 0, nmat, min(nbones, 255) if skinned else 0, 0x7530, mats_at, batches_at,
                     vb_at, shader, fmt, stride, ib_at, *ctr, rad)
    return bytes(out)


def write_bundle(chunks: list, align: int = ALIGN) -> bytes:
    """An .xrsc from (tag, data) chunks, each padded to end on 0x80 (align: the PS2's .rsc pad to 16)."""
    head = -(-(12 + 8 * len(chunks)) // ALIGN) * ALIGN  # the table, rounded up to 0x80 (shipped: 0x80, 0x100)
    out = bytearray(head)
    out[:4] = b'RSC0'
    struct.pack_into('<II', out, 4, len(chunks), head)
    for i, (tag, data) in enumerate(chunks):
        out += data
        out += bytes(-len(out) % align)
        out[12 + 8 * i:16 + 8 * i] = tag.encode('latin-1')
        struct.pack_into('<I', out, 16 + 8 * i, len(out))
    return bytes(out)


def write_names(entries: list) -> bytes:
    """An .xrnt from (tag, chunk number, name) entries, sorted by name: the game finds a name by binary search
    (sub_00084140 over the table, comparing names, then tags), as the shipped tables are sorted (ani, ani_face,
    ani_lod_01, ...)."""
    entries = sorted(entries, key=lambda e: (e[2].lower(), e[0]))
    head = bytearray(b'RNT0' + struct.pack('<I', len(entries)) + bytes(16))
    names = bytearray()
    base = 0x18 + 12 * len(entries)
    for tag, num, name in entries:
        head += struct.pack('<I4sI', base + len(names), tag.encode('latin-1'), num)
        names += name.encode('latin-1') + b'\0'
    return bytes(head + names)


def build(textures: list, meshes: list) -> tuple:
    """(.xrsc, .xrnt) from textures [(name, rgba)] and meshes [(name, model)]: the textures first, each MIP0 PAL0
    TEX0, then a MOD0 a mesh, each named in the .xrnt."""
    chunks, tex_num = [], {}
    for name, rgba in textures:
        n = len(chunks)
        chunks += texture_chunks(name, rgba, pal_num=n + 2, mip_num=n + 1)
        tex_num[name.lower()] = n + 3
    entries = []
    for name, m in meshes:
        chunks.append(('MOD0', mod0(m, lambda t: tex_num.get(t.lower(), 0))))
        entries.append(('MOD0', len(chunks), name))
    return write_bundle(chunks), write_names(entries)


def empty_mod0() -> bytes:
    """A mesh that draws nothing (one material, one packet of one degenerate triangle): stands in for a level mesh
    taken out, so the chunk numbers the level's zone files use stay where they are."""
    m = modfmt.Model(version='2.12')
    m.positions, m.normals, m.uv1s, m.colors, m.uv2s = [(0.0, 0.0, 0.0)], [(0.0, 1.0, 0.0)], [(0.0, 0.0)], \
        [(0.0, 0.0, 0.0, 0.0)], [(0.0, 0.0)]
    pk = modfmt.Packet(adjuncts=[modfmt.Adjunct(0, 0, 0, 0, 0, 0)] * 3,
                       primitives=[modfmt.Primitive(modfmt.STRIP, [0, 1, 2])], matrices=[0])
    m.materials = [modfmt.Material(name='removed', packets=[pk])]
    m.header_totals = (3, 0, 1, 1)
    return mod0(m, lambda t: 0, level=True)


def build_level(base: list, replace_meshes: dict, new_meshes: list, new_textures: list, replace_textures: dict,
                texture_numbers: dict, remove: set = frozenset()) -> tuple:
    """A level bundle from the one it was imported from (rsc.Chunk list): every chunk keeps its number, as the
    level's zone (.xznes) and bound (.bnds) files name them.
        replace_meshes   {MOD0 chunk number: model}, compiled in place
        remove           MOD0 chunk numbers to empty (meshes taken out)
        replace_textures {TEX0 chunk number: rgba}: its MIP0 and PAL0 rewritten in place
        new_textures     [(name, rgba)], added after the last chunk
        new_meshes       [model], added after them
        texture_numbers  {texture name (lower case): TEX0 chunk number} of the textures already in the bundle
    Returns (bundle, the new meshes' chunk numbers)."""
    chunks = [(c.tag, c.data) for c in base]
    numbers = dict(texture_numbers)
    for num, rgba in replace_textures.items():
        t = base[num - 1].data
        pal_num, mip_num = struct.unpack_from('<II', t, 0x14)
        name = t[0x24:t.index(b'\0', 0x24)].decode('latin-1') if b'\0' in t[0x24:] else ''
        mip, pal, tex0 = texture_chunks(name, rgba, pal_num, mip_num)
        chunks[mip_num - 1], chunks[pal_num - 1], chunks[num - 1] = mip, pal, tex0
    for name, rgba in new_textures:
        n = len(chunks)
        chunks += texture_chunks(name, rgba, pal_num=n + 2, mip_num=n + 1)
        numbers[name.lower()] = n + 3

    def ref(t):
        return numbers.get(t.lower(), 0)
    for num, m in replace_meshes.items():
        chunks[num - 1] = ('MOD0', mod0(m, ref, level=True))
    for num in remove:
        if num not in replace_meshes:
            chunks[num - 1] = ('MOD0', empty_mod0())
    added = []
    for m in new_meshes:
        chunks.append(('MOD0', mod0(m, ref, level=True, keep_uv2=False)))
        added.append(len(chunks))
    return write_bundle(chunks), added


def zone_file(nodes: list, ps2: bool = False) -> str:
    """A zone file placing meshes: nodes [(name, chunk number)], each where its mesh is (world space). Xbox
    (.xznes): the number in resourceIndexXbox; PS2 (.znes): in resourceIndex."""
    out = [f'NumInstNodes {len(nodes)} ', '']
    for name, num in nodes:
        out += [f'node {name} {{', '\tnumLods 1 ', f'\tresourceIndex {num if ps2 else 0} ',
                f'\tresourceIndexXbox {0 if ps2 else num} ', '\tmatrix',
                '\t{', '\t\t1.000000 \t0.000000 \t0.000000 ', '\t\t0.000000 \t1.000000 \t0.000000 ',
                '\t\t0.000000 \t0.000000 \t1.000000 ', '\t\t0.000000 \t0.000000 \t0.000000 ', '\t}',
                '\tattributes 0 {', '\t}', '}']
    return '\r\n'.join(out) + '\r\n'


def add_to_list(lvl_text: str, section: str, name: str) -> str:
    """A .lvl with one more file in one of its lists (Bounds, Zones, Lines, Boxes)."""
    import re
    if re.search(r'(?im)^\s*' + re.escape(name) + r'\s*$', lvl_text):
        return lvl_text
    nl = '\r\n' if '\r\n' in lvl_text else '\n'
    m = re.search(section + r'\s*\{', lvl_text)
    if not m:
        return lvl_text + f'{section} {nl}{{ {nl}{name} {nl}}} {nl}'
    return lvl_text[:m.end()] + nl + name + ' ' + lvl_text[m.end():]


def set_list(lvl_text: str, section: str, names: list) -> str:
    """A .lvl with one of its lists (Lines, Boxes ...) holding exactly these names."""
    import re
    nl = '\r\n' if '\r\n' in lvl_text else '\n'
    body = nl.join(n + ' ' for n in names)
    m = re.search(section + r'\s*\{(.*?)\}', lvl_text, re.S)
    block = f'{section} {nl}{{ {nl}' + (body + nl if names else '') + '} '
    if not m:
        return lvl_text + block + nl
    return lvl_text[:m.start()] + block + lvl_text[m.end():]


def add_zone(lvl_text: str, zone_name: str) -> str:
    """A level's .lvl with one more zone file in its Zones list (named .znes, as the list names them)."""
    import re
    if zone_name.lower() in lvl_text.lower():
        return lvl_text
    m = re.search(r'(Zones\s*\{)', lvl_text)
    if not m:
        raise ValueError('the .lvl has no Zones list')
    nl = '\r\n' if '\r\n' in lvl_text else '\n'
    return lvl_text[:m.end()] + nl + zone_name + ' ' + lvl_text[m.end():]
