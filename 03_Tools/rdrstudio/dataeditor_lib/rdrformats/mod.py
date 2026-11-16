"""Angel Studios / Rockstar San Diego binary models: Red Dead Revolver .mod (PS2) and .xmod (Xbox), versions 2.00,
2.10 and 2.12. The two platforms share the layout; only what the files name (textures) differs.

Layout, little endian:
    "version: 2.xx\\0"
    counts, u32 each: positions, normals, colors, uv1s, uv2s, tangents, materials, adjuncts, primitives, matrices,
                      reskins (not in 2.00)
    positions  xyz f32      normals  xyz f32      colors  rgba f32      uv1s / uv2s  uv f32      tangents  xyz f32
    materials:
        name, space terminated
        u32 packets, u32 primitives, u32 textures, u32 illum
        ambient, diffuse, specular: rgba f32
        texture names, space terminated
        2.10, 2.12: u32 parameter count, then each: u32 type, name (space terminated), value (type 2 = f32)
    per material, its packets:
        packet header: adjuncts, primitives, matrices, reskins (u32 in 2.00/2.10, u16 in 2.12)
        adjuncts: position, normal, color, uv1, uv2, matrix indices (u32 in 2.00/2.10, u16 in 2.12)
        reskins: two indices (u32 / u16) then 16 bytes, kept raw
        primitives: type (0 triangles, 1 strip, 2 strip flipped), count (types 1, 2; triangles are 3), indices into the
                    packet's adjuncts; all u32 in 2.00/2.10, u8 in 2.12
        matrices: indices into the skeleton's bones (u32 / u8)
    2.00, 2.10 trailer: " mtxv " then a u32 per matrix, " mtxn " likewise (positions and normals each matrix takes)

A packet's adjuncts and matrices are local to it: 2.12's u8 indices cap a packet at 256 adjuncts and matrices.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

VERSIONS = ('2.00', '2.10', '2.12')
TRIANGLES, STRIP, STRIP_FLIPPED = 0, 1, 2
PARAM_FLOAT = 2


@dataclass
class Adjunct:
    position: int
    normal: int
    color: int
    uv1: int
    uv2: int
    matrix: int

    def tuple(self):
        return (self.position, self.normal, self.color, self.uv1, self.uv2, self.matrix)


@dataclass
class Primitive:
    type: int
    indices: list[int]

    def triangles(self) -> list[tuple[int, int, int]]:
        """The primitive as triangles, in the winding the game draws."""
        ix = self.indices
        if self.type == TRIANGLES:
            return [tuple(ix[i:i + 3]) for i in range(0, len(ix) - 2, 3)]
        out = []
        for i in range(len(ix) - 2):
            a, b, c = ix[i], ix[i + 1], ix[i + 2]
            if a == b or b == c or a == c:
                continue  # degenerate joins between strips
            flip = (i % 2 == 1) != (self.type == STRIP_FLIPPED)
            out.append((b, a, c) if flip else (a, b, c))
        return out


@dataclass
class Packet:
    adjuncts: list[Adjunct] = field(default_factory=list)
    primitives: list[Primitive] = field(default_factory=list)
    matrices: list[int] = field(default_factory=list)
    reskins: list[bytes] = field(default_factory=list)  # raw, as read


@dataclass
class Param:
    type: int
    name: str
    value: object


@dataclass
class Material:
    name: str
    illum: int = 1
    ambient: tuple = (1.0, 1.0, 1.0, 1.0)
    diffuse: tuple = (1.0, 1.0, 1.0, 1.0)
    specular: tuple = (0.0, 0.0, 0.0, 1.0)
    textures: list[str] = field(default_factory=list)
    params: list[Param] = field(default_factory=list)
    packets: list[Packet] = field(default_factory=list)
    # the material's primitive count as read (the triangles it draws, see derived_material_count); None to derive it
    declared_primitives: int | None = None


@dataclass
class Model:
    version: str = '2.12'
    positions: list[tuple] = field(default_factory=list)
    normals: list[tuple] = field(default_factory=list)
    colors: list[tuple] = field(default_factory=list)
    uv1s: list[tuple] = field(default_factory=list)
    uv2s: list[tuple] = field(default_factory=list)
    tangents: list[tuple] = field(default_factory=list)
    materials: list[Material] = field(default_factory=list)
    # 2.00 / 2.10: per tag ("mtxv", "mtxn"), a count per matrix; None to derive them on write
    trailer: list[tuple[str, list[int]]] | None = None
    # the header's adjunct, primitive, matrix and reskin totals as read; None to derive them on write
    header_totals: tuple | None = None

    @property
    def wide(self):
        return self.version in ('2.00', '2.10')

    def packets(self):
        for m in self.materials:
            yield from m.packets


class FormatError(ValueError):
    pass


class _Reader:
    def __init__(self, data: bytes):
        self.d, self.p = data, 0

    def take(self, fmt: str):
        fmt = '<' + fmt
        v = struct.unpack_from(fmt, self.d, self.p)
        self.p += struct.calcsize(fmt)
        return v

    def one(self, fmt: str):
        return self.take(fmt)[0]

    def name(self) -> str:
        end = self.d.index(b' ', self.p)
        s = self.d[self.p:end].decode('latin-1')
        self.p = end + 1
        return s

    def vecs(self, n: int, k: int):
        return [self.take(f'{k}f') for _ in range(n)]


TEXT_VERSIONS = ('1.09', '1.10')
ILLUM = {'diffuse': 1, 'emit': 2}  # binary files only show 1; emit's number is a guess
_PRIMS = {'tri': TRIANGLES, 'str': STRIP, 'stp': STRIP_FLIPPED}


def read_text(data: bytes) -> Model:
    """The text models (1.09, 1.10; the layout Dummiesman's importer reads): counts as "name: n" lines, then "v", "n",
    "c", "t1", "t2" lines, "mtl name { ... }" blocks, then per material its "packet adjuncts primitives matrices
    [reskins] {" blocks of "adj", "reskin adjunct slot weight x y z", "tri"/"str"/"stp" and "mtx" lines; last
    "mtxv"/"mtxn". Kept in the binary 2.12 shapes (reskins packed as u16 u16 then four f32; a missing index -1 becomes
    0 as binary files store it)."""
    def num(x):
        return float(x.rstrip('fF'))  # a few files write C style 1.0f
    lines = [ln.split() for ln in data.decode('latin-1').splitlines()]
    lines = [t for t in lines if t]
    version = lines[0][1] if lines[0][0] == 'version:' else ''
    if version not in TEXT_VERSIONS:
        raise FormatError(f'unsupported text model version {version!r}')
    m = Model(version='2.12')
    totals = {}
    mats_packets = []
    i = 1
    vec = {'v': m.positions, 'n': m.normals, 'c': m.colors, 't1': m.uv1s, 't2': m.uv2s}
    while i < len(lines):
        t = lines[i]
        k = t[0]
        if k.endswith(':') and len(t) == 2 and not mats_packets:
            totals[k[:-1]] = int(t[1])
        elif k in vec:
            vec[k].append(tuple(num(x) for x in t[1:]))
        elif k == 'mtl':
            mat = Material(name=t[1])
            i += 1
            while lines[i][0] != '}':
                f, rest = lines[i][0], lines[i][1:]
                if f == 'packets:':
                    mats_packets.append([mat, int(rest[0])])
                elif f == 'primitives:':
                    mat.declared_primitives = int(rest[0])
                elif f == 'illum:':
                    mat.illum = ILLUM.get(rest[0], 1)
                elif f in ('ambient:', 'diffuse:', 'specular:'):
                    setattr(mat, f[:-1], (tuple(num(x) for x in rest) + (1.0,))[:4])
                elif f == 'texture:':
                    mat.textures.append(' '.join(rest[1:]).strip('"'))
                elif f == 'float' and rest and rest[0].endswith(':'):
                    mat.params.append(Param(PARAM_FLOAT, rest[0][:-1], num(rest[1])))
                i += 1
            m.materials.append(mat)
        elif k == 'packet':
            mat = next(mp for mp in mats_packets if mp[1] > 0)
            mp_left = mat
            pk = Packet()
            i += 1
            while lines[i][0] != '}':
                f, rest = lines[i][0], lines[i][1:]
                if f == 'adj':
                    pk.adjuncts.append(Adjunct(*(max(int(x), 0) for x in rest[:6])))
                elif f == 'reskin':
                    pk.reskins.append(struct.pack('<HH4f', int(rest[0]), int(rest[1]), *map(num, rest[2:6])))
                elif f in _PRIMS:
                    ix = [int(x) for x in rest]
                    pk.primitives.append(Primitive(_PRIMS[f], ix if f == 'tri' else ix[1:]))
                elif f == 'mtx':
                    pk.matrices = [int(x) for x in rest]
                i += 1
            mp_left[0].packets.append(pk)
            mp_left[1] -= 1
        elif k in ('mtxv', 'mtxn'):
            pass  # derived again on write
        i += 1
    m.header_totals = (totals.get('adjuncts', 0), totals.get('primitives', 0), totals.get('matrices', 0),
                       totals.get('reskins', 0))
    return m


def read(data: bytes) -> Model:
    if data.startswith(b'version: 1.'):
        return read_text(data)
    end = data.index(b'\0')
    header = data[:end].decode('latin-1')
    if not header.startswith('version: '):
        raise FormatError('not a binary model')
    version = header[len('version: '):]
    if version not in VERSIONS:
        raise FormatError(f'unsupported version {version}')
    r = _Reader(data)
    r.p = end + 1
    m = Model(version=version)
    nv, nn, nc, nt1, nt2, ntan, nmat, nadj, nprim, nmtx = r.take('10I')
    nres = r.one('I') if version != '2.00' else 0
    m.header_totals = (nadj, nprim, nmtx, nres)
    m.positions, m.normals = r.vecs(nv, 3), r.vecs(nn, 3)
    m.colors = r.vecs(nc, 4)
    m.uv1s, m.uv2s = r.vecs(nt1, 2), r.vecs(nt2, 2)
    m.tangents = r.vecs(ntan, 3)
    counts = []
    for _ in range(nmat):
        mat = Material(name=r.name())
        packets, prims, ntex, mat.illum = r.take('4I')
        counts.append((packets, prims))
        mat.declared_primitives = prims
        mat.ambient, mat.diffuse, mat.specular = r.take('4f'), r.take('4f'), r.take('4f')
        mat.textures = [r.name() for _ in range(ntex)]
        if version != '2.00':
            for _ in range(r.one('I')):
                ptype = r.one('I')
                pname = r.name()
                if ptype != PARAM_FLOAT:
                    raise FormatError(f'material parameter type {ptype} at {r.p:#x}')
                mat.params.append(Param(ptype, pname, r.one('f')))
        m.materials.append(mat)
    idx = 'I' if m.wide else 'H'
    num = 'I' if m.wide else 'B'
    try:
        for mat, (packets, _) in zip(m.materials, counts):
            for _ in range(packets):
                na, np_, nm, nr = r.take('4' + idx)
                pk = Packet()
                pk.adjuncts = [Adjunct(*r.take('6' + idx)) for _ in range(na)]
                rsize = struct.calcsize('<2' + idx) + 16
                for _ in range(nr):
                    if r.p + rsize > len(data):
                        raise struct.error('cut short')
                    pk.reskins.append(data[r.p:r.p + rsize])
                    r.p += rsize
                for _ in range(np_):
                    t = r.one(num)
                    n = 3 if t == TRIANGLES else r.one(num)
                    pk.primitives.append(Primitive(t, list(r.take(f'{n}{num}'))))
                pk.matrices = list(r.take(f'{nm}{num}'))
                mat.packets.append(pk)
        if m.wide:
            m.trailer = []
            while r.p < len(data) and data[r.p] == 0x20:
                r.p += 1
                tag = r.name()
                m.trailer.append((tag, list(r.take(f'{nmtx}I'))))
    except struct.error:
        # a file cut short (a few shipped ones are): what was read is kept, the packet being read when it ended is not
        m.truncated = True
        return m
    if r.p != len(data):
        raise FormatError(f'{len(data) - r.p} bytes left over at {r.p:#x}')
    return m


def _check_name(s: str):
    if ' ' in s or '\0' in s:
        raise FormatError(f'name {s!r} has a space or null, which ends a name in this format')
    return s.encode('latin-1') + b' '


def derived_totals(m: Model, bone_count: int | None = None) -> tuple:
    """The header's totals as the shipped files have them (measured over the samples, tests/counts_hypotheses.py):
    adjuncts = distinct corners (position, normal, color, uvs) before packets split them, ~80% exact and the rest within
    a few; primitives = triangles drawn, 99.6%; matrices = the skeleton's bone count (highest bone used + 1 when not
    given, 98%); the fifth (2.10 / 2.12, "reskins") is not the packets' reskin count: the samples have 3 on skinned
    characters and 1 on everything else, and two ship 0xCCCCCCCC (memory the original exporter never set), so the game
    does not read it; this writes the same convention."""
    pks = list(m.packets())
    corners = {(a.position, a.normal, a.color, a.uv1, a.uv2) for p in pks for a in p.adjuncts}
    tris = sum(len(prim.triangles()) for p in pks for prim in p.primitives)
    bones = {b for p in pks for b in p.matrices}
    nmtx = bone_count if bone_count is not None else (max(bones) + 1 if bones else 0)
    return (len(corners), tris, nmtx, 3 if nmtx > 1 else 1)


def write(m: Model, keep_header_totals: bool = True) -> bytes:
    if m.version not in VERSIONS:
        raise FormatError(f'unsupported version {m.version}')
    wide = m.wide
    idx = 'I' if wide else 'H'
    num = 'I' if wide else 'B'
    limit = 0xFFFFFFFF if wide else 0xFF
    out = bytearray(f'version: {m.version}'.encode() + b'\0')
    totals = m.header_totals if keep_header_totals and m.header_totals else derived_totals(m)
    nmtx = totals[2]
    head = [len(m.positions), len(m.normals), len(m.colors), len(m.uv1s), len(m.uv2s), len(m.tangents),
            len(m.materials), *totals[:3]]
    out += struct.pack('<10I', *head)
    if m.version != '2.00':
        out += struct.pack('<I', totals[3])
    for vecs, k in ((m.positions, 3), (m.normals, 3), (m.colors, 4), (m.uv1s, 2), (m.uv2s, 2), (m.tangents, 3)):
        for v in vecs:
            out += struct.pack(f'<{k}f', *v)
    for mat in m.materials:
        out += _check_name(mat.name)
        keep = keep_header_totals and mat.declared_primitives is not None
        prims = mat.declared_primitives if keep else derived_material_count(mat)
        out += struct.pack('<4I', len(mat.packets), prims, len(mat.textures), mat.illum)
        for c in (mat.ambient, mat.diffuse, mat.specular):
            out += struct.pack('<4f', *c)
        for t in mat.textures:
            out += _check_name(t)
        if m.version != '2.00':
            out += struct.pack('<I', len(mat.params))
            for prm in mat.params:
                out += struct.pack('<I', prm.type) + _check_name(prm.name) + struct.pack('<f', prm.value)
    for mat in m.materials:
        for pk in mat.packets:
            if not wide and (len(pk.adjuncts) > 256 or len(pk.matrices) > 256):
                raise FormatError('a 2.12 packet holds at most 256 adjuncts and 256 matrices')
            out += struct.pack('<4' + idx, len(pk.adjuncts), len(pk.primitives), len(pk.matrices), len(pk.reskins))
            for a in pk.adjuncts:
                out += struct.pack('<6' + idx, *a.tuple())
            for raw in pk.reskins:
                out += raw
            for prim in pk.primitives:
                if prim.type == TRIANGLES and len(prim.indices) != 3:
                    raise FormatError('a triangle primitive has exactly 3 indices')
                if any(i > limit for i in prim.indices):
                    raise FormatError('a primitive index is past the packet limit')
                out += struct.pack('<' + num, prim.type)
                if prim.type != TRIANGLES:
                    if len(prim.indices) > limit:
                        raise FormatError('a 2.12 strip holds at most 255 indices')
                    out += struct.pack('<' + num, len(prim.indices))
                out += struct.pack(f'<{len(prim.indices)}{num}', *prim.indices)
            out += struct.pack(f'<{len(pk.matrices)}{num}', *pk.matrices)
    if wide:
        trailer = m.trailer if m.trailer is not None else derived_trailer(m)
        for tag, counts in trailer:
            if len(counts) != nmtx:
                raise FormatError(f'trailer {tag} needs a count per matrix ({nmtx})')
            out += b' ' + _check_name(tag) + struct.pack(f'<{len(counts)}I', *counts)
    return bytes(out)


def derived_material_count(mat: Material) -> int:
    """A material's primitive count: the triangles its packets draw (a guess until checked)."""
    return sum(len(p.triangles()) for pk in mat.packets for p in pk.primitives)


def derived_trailer(m: Model, bone_count: int | None = None) -> list:
    """mtxv / mtxn: the positions and normals each bone takes, bone by bone (100% of the 2.00 / 2.10 samples)."""
    nmtx = derived_totals(m, bone_count)[2]
    pos = [set() for _ in range(nmtx)]
    nrm = [set() for _ in range(nmtx)]
    for pk in m.packets():
        for a in pk.adjuncts:
            if a.matrix < len(pk.matrices) and pk.matrices[a.matrix] < nmtx:
                pos[pk.matrices[a.matrix]].add(a.position)
                nrm[pk.matrices[a.matrix]].add(a.normal)
    return [('mtxv', [len(s) for s in pos]), ('mtxn', [len(s) for s in nrm])]


def load(path) -> Model:
    with open(path, 'rb') as f:
        return read(f.read())


def save(m: Model, path, keep_header_totals: bool = True):
    with open(path, 'wb') as f:
        f.write(write(m, keep_header_totals))
