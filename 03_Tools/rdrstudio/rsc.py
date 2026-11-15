"""RSC0 resource bundles -- the container holding a level's real geometry.

    "RSC0"  u32 count  u32 header_size
    count x (char[4] tag, u32 chunk_end)

Tags seen on this disc:
    BND0  collision bound   (DECODED -- see BoundMesh)
    MOD0  render model
    MIP0 / PAL0 / TEX0      texture mip data / palette / header
    PMT0  named collision-surface material -- NOT a palette (see below)
    SAN0 / SKE0             animation / skeleton
    NTG0  nav grid

The stored u32 is each chunk's END, not its start -- chunk i spans
[previous end, offs[i]), the first starting at `header_size`, and the final
entry equals the file length exactly (623/623 files with a table). Reading them
as starts shifts every tag by one. The note where `Rsc.__init__` reads the
table records what that mis-read looked like; measured by
`research/probe_rsc.py`, independently reproduced by rdr-fmt (373/373 after
dedup), filed as REQ-055.

`resourceIndex` in a .bnds/.znes node indexes this table **1-based**, 0 meaning
none (18,539/18,541), so node -> chunk is `table[resourceIndex - 1]`.

BND0 layout:

    0x00  u32   magic 0x0FFFFF00
    0x08  3f    aabb min
    0x14  3f    aabb max
    0x20  3f    centre          (exactly the midpoint of min/max)
    0x38  f32   bounding radius (exactly half the min->max diagonal)
    0x4C  u32   vertex count
    0x50  u32   face count
    0x68  u32   vertex data offset
    0x6C  u32   face data offset
    0x70+       further offsets, and past the face table an AABB tree the
                engine uses for broadphase. Not needed to draw the surface.

    vertices: 3 floats each
    faces:    32 bytes each ->  3f face normal (unit),
                               f32 SURFACE AREA of the polygon,
                               4 x u16 vertex index,
                               4 x u16 pad, always 0xFFFF

The float at +12 is the polygon's AREA, not a plane distance. It was read as a
distance for a long time and never used, so nothing depended on the mistake --
but it is the single most useful field in the chunk, because it lets any
decode be checked against a number the file states about itself. It is only
375 of 313,169 times equal to n.v, which is what a plane distance would be:
`lvl_mans.rsc` chunk 63 is one flat 1.5135 x 2.5192 quad whose four corners
all sit at n.v = 18.0250, and the field reads 3.8117 -- the area.

More precisely it is the sum of the FAN TRIANGLES, not the flat-polygon area.
A quad here is not always planar (TRAIN chunk 424 face 3 has a corner 0.50 m
out of plane), and for those the file states the two-triangle total. Measured
across all 35 LVLS_SNG levels with the index rule below: summing the fan,
313,169 of 313,169 faces reproduce their own stated area, worst error 0.0030%.
Taking Newell's planar area instead gives 313,164 and a worst error of 0.8221%
-- so the five "known residuals" were the check's arithmetic, not the data's.
Summing the fan is also the stricter test, because it measures the triangles
that are actually emitted below rather than the polygon they came from.

Only FOUR of the eight u16 slots are vertex indices. Slots 4..7 are 0xFFFF in
every one of those 313,169 faces, so no face is ever more than a quad; the
shape split is 221,914 triangles to 91,255 quads.

A triangle is written with 0 in slot 3. That is a pad, not vertex 0 -- see
`bound` for why this has to be read positionally.
"""

import struct

MAGIC = b"RSC0"

# First u32 of a primitive (vertex+face) collision bound: bytes 00 ff ff 0f.
BND_MAGIC = 0x0FFFFF00


class Chunk:
    __slots__ = ("index", "tag", "offset", "size")

    def __init__(self, index, tag, offset, size):
        self.index = index
        self.tag = tag
        self.offset = offset
        self.size = size

    def __repr__(self):
        return "<Chunk %d %s @0x%X %d>" % (self.index, self.tag, self.offset, self.size)


class BoundMesh:
    """Decoded collision geometry: world-space vertices + triangles."""

    __slots__ = ("index", "verts", "tris", "aabb_min", "aabb_max", "radius",
                 "nfaces")

    def __init__(self, index, verts, tris, aabb_min, aabb_max, radius, nfaces):
        self.index = index
        self.verts = verts        # [(x, y, z), ...]
        self.tris = tris          # [(a, b, c), ...] indices into verts
        self.aabb_min = aabb_min
        self.aabb_max = aabb_max
        self.radius = radius
        self.nfaces = nfaces

    def to_json(self):
        return dict(index=self.index, verts=self.verts, tris=self.tris,
                    min=self.aabb_min, max=self.aabb_max, faces=self.nfaces)


class Rsc:
    def __init__(self, data, label=""):
        self.data = data
        self.label = label
        if data[:4] != MAGIC:
            raise ValueError("%s: not an RSC0 bundle (%r)" % (label, data[:4]))
        count, self.header_size = struct.unpack_from("<II", data, 4)
        self.chunks = []
        raw = []
        for i in range(count):
            off = 12 + i * 8
            if off + 8 > len(data):
                break
            tag = data[off:off + 4].decode("latin-1", "replace")
            end = struct.unpack_from("<I", data, off + 4)[0]
            raw.append((tag, end))

        # The stored value is each chunk's END, not its start: the final entry
        # equals the file size exactly, and a single-chunk bundle
        # (lvl_mans_nav.rsc) only makes sense this way. Reading them as starts
        # shifts every tag by one -- which is what made a palette look like it
        # was tagged MIP0.
        for i, (tag, end) in enumerate(raw):
            start = self.header_size if i == 0 else raw[i - 1][1]
            self.chunks.append(Chunk(i, tag, start, max(0, end - start)))

    def __len__(self):
        return len(self.chunks)

    def tally(self):
        out = {}
        for c in self.chunks:
            out[c.tag] = out.get(c.tag, 0) + 1
        return out

    def raw(self, index):
        c = self.chunks[index]
        return self.data[c.offset:c.offset + c.size]

    # ------------------------------------------------------------------ BND0

    def bound(self, index):
        """Decode a BND0 chunk. Returns None if it isn't the primitive form.

        There is no second form. Measured over every archive on the disc, all
        3,815 BND0 chunks in 63 archives start with BND_MAGIC; none of them
        starts `01 00 01 00 10 ...`, and none is shorter than the 0x90 header.

        This docstring used to say ~6% were a model-derived form needing the
        .mod format finished first. That 6% was the chunk table being read
        wrong -- offsets are each chunk's END, not its start, so every tag was
        shifted by one and one BND0 in sixteen was really pointing at the
        following MOD0. Fixing the table fixed the 6%; the note outlived it.

        The magic check stays anyway. Rejecting on magic rather than on
        downstream sanity checks keeps a format we have not seen from
        silently yielding plausible garbage.
        """
        c = self.chunks[index]
        if c.tag != "BND0" or c.size < 0x90:
            return None
        b = self.raw(index)
        if struct.unpack_from("<I", b, 0)[0] != BND_MAGIC:
            return None

        aabb_min = struct.unpack_from("<3f", b, 0x08)
        aabb_max = struct.unpack_from("<3f", b, 0x14)
        radius = struct.unpack_from("<f", b, 0x38)[0]
        nverts, nfaces = struct.unpack_from("<II", b, 0x4C)
        voff, foff = struct.unpack_from("<II", b, 0x68)

        if not (0 < nverts < 100000 and 0 <= nfaces < 100000):
            return None
        if voff + nverts * 12 > len(b) or foff + nfaces * 32 > len(b):
            return None

        verts = [struct.unpack_from("<3f", b, voff + i * 12) for i in range(nverts)]

        tris = []
        for i in range(nfaces):
            o = foff + i * 32
            idx = struct.unpack_from("<4H", b, o + 16)
            # A face is a triangle or a quad, and the count is not stored: a
            # triangle pads slot 3 with 0. So slots 0..2 are ALWAYS vertices --
            # including when one of them is 0, which happens 3,849 times -- and
            # only slot 3 is conditional.
            #
            # Scanning all eight slots and stopping at 0xFFFF, which is what
            # this did before, therefore read every triangle as a quad whose
            # fourth corner is vertex 0 -- a vertex on the far side of the
            # mesh. Fanning that quad gives the REAL triangle plus one long
            # thin one stretching back to the mesh origin, so the damage is
            # exactly one spurious triangle per triangle face: 221,914 of the
            # 626,338 triangles this used to emit, 35.4%. Every one of the
            # 404,424 triangles it now emits was already in the old set --
            # the old rule added to the truth, it did not replace it, which
            # is why collision drew as a shredded mess laid over real
            # surfaces rather than as nothing recognisable.
            #
            # 70.9% is a different number: the share of FACES that are
            # triangles and so were misread. It is not the share of bad
            # triangles and should not be quoted as one.
            #
            # The arbiter is the stated area at +12, not a guess: under this
            # rule 313,169 of 313,169 faces disc-wide come out to the area the
            # file itself records, worst error 0.0030%, and every single face
            # is wound to agree with its own stored normal. Under the old rule
            # 92,608 did -- 29.57%, which is just the quads, the only faces it
            # happened to read correctly.
            poly = list(idx[:3])
            if idx[3] not in (0, 0xFFFF):
                poly.append(idx[3])
            if any(v >= nverts for v in poly):   # corrupt or a different form
                continue
            # Fan-triangulate: 1 triangle for a tri, 2 for a quad.
            for k in range(1, len(poly) - 1):
                tris.append((poly[0], poly[k], poly[k + 1]))

        return BoundMesh(index, verts, tris, list(aabb_min), list(aabb_max),
                         radius, nfaces)

    def bounds(self):
        """Every BND0 chunk, decoded. index -> BoundMesh."""
        out = {}
        for c in self.chunks:
            if c.tag != "BND0":
                continue
            m = self.bound(c.index)
            if m is not None:
                out[c.index] = m
        return out

    # ------------------------------------------------------------ textures

    # A TEX0 needs 0x8C bytes to state its size and 0xB0 to also carry a
    # name. Both forms exist on the disc.
    TEX0_SIZED, TEX0_NAMED = 0x8C, 0xB0

    def texture_info(self, index):
        """Name and dimensions from a TEX0 register chunk.

        Layout found by inspection:
            0x88  u16 width, u16 height
            0xA0  NUL-terminated texture name

        This used to require 0xB0 bytes -- the space the NAME needs -- and so
        returned None for a shorter TEX0 whose dimensions were sitting right
        there at 0x88. `texture_table` drops whatever this rejects, so those
        textures were invisible to every caller, and 13 character surfaces
        bound to one of them: a viewer asking "what goes on this part" got
        nothing back.

        There are **73 of them on the disc** (REQ-029), every one with
        plausible dimensions -- 256x256, 256x32, 128x128, 64x64 and other
        power-of-two pairs. They are 160 bytes: past the size and short of
        the name. So the size is the requirement and the name is the extra,
        which is what these two thresholds say.

        Verified additive before it was changed: across all 1,139 bundles,
        admitting them adds 73 table entries and shifts the position of
        **zero** existing ones. That mattered because `tex_slot_map` maps a
        binding reference to a POSITION in `texture_table()`, so a shift
        would have silently rebound textures that were reading correctly.
        """
        c = self.chunks[index]
        if c.tag != "TEX0" or c.size < self.TEX0_SIZED:
            return None
        b = self.raw(index)
        w, h = struct.unpack_from("<HH", b, 0x88)
        name = (b[0xA0:0xC0].split(b"\x00")[0].decode("latin-1", "replace")
                if c.size >= self.TEX0_NAMED else "")
        return dict(index=index, name=name, width=w, height=h)

    def tex_sources(self, index):
        """A TEX0's own (pal_chunk, mip_chunk), read from the chunk itself.

        At 0x90 a TEX0 stores two ONE-BASED chunk indices: its palette and
        its pixels, in that order. Same one-based convention as the `.rnt`
        manifest, the per-surface texture binding, and `.znes` resourceIndex.

        Returns None when the field is absent or does not resolve to the
        right pair of tags, so the caller can fall back to disc order.
        """
        c = self.chunks[index]
        if c.tag != "TEX0" or c.size < 0x98:
            return None
        pal_i, mip_i = struct.unpack_from("<II", self.raw(index), 0x90)
        pal_i, mip_i = pal_i - 1, mip_i - 1
        n = len(self.chunks)
        if not (0 <= pal_i < n and 0 <= mip_i < n):
            return None
        pal, mip = self.chunks[pal_i], self.chunks[mip_i]
        if mip.tag != "MIP0" or pal.tag != "PAL0":
            return None
        return pal, mip

    def texture_table(self):
        """Every texture as (tex0_index, info, mip_chunk, pal_chunk).

        The pairing is read out of the TEX0 itself -- see tex_sources. The
        fallback below is the old rule: on disc the order is MIP0, PAL0,
        TEX0, so a TEX0's pixels and palette are the chunks just before it.
        That guess happens to be right for all 4,093 TEX0 chunks in the level
        bundles, but it is a guess, and a texture wearing a neighbour's
        palette is exactly the failure that looks like rainbow garbage.
        """
        out = []
        for i, c in enumerate(self.chunks):
            if c.tag != "TEX0":
                continue
            info = self.texture_info(c.index)
            if info is None:
                continue
            stored = self.tex_sources(c.index)
            if stored is not None:
                out.append((c.index, info, stored[1], stored[0]))
                continue
            mip = pal = None
            for prev in self.chunks[max(0, i - 4):i]:
                if prev.tag == "MIP0":
                    mip = prev
                elif prev.tag == "PAL0":
                    pal = prev
            if mip is not None and pal is not None:
                out.append((c.index, info, mip, pal))
        return out

    def tex_slot_map(self):
        """resource reference -> position in texture_table().

        This is the real model->texture binding, and it is exact.

        Every MOD0 in a level is a container whose outer table is
        `count` x (u32 resource, u32 flags), one record per sub-mesh. That
        `resource` is a ONE-BASED chunk index pointing straight at a TEX0
        register block; 0 means the surface is untextured and draws with
        vertex colour only.

        It stayed hidden because of the off-by-one. Read as a 0-based index
        every reference lands one chunk early -- on the PAL0 that precedes
        each TEX0, since disc order is MIP0, PAL0, TEX0 -- so a tag census of
        the targets came back "MOD0/MIP0/PMT0" and read as noise. Corrected,
        it resolves to a TEX0 for 15,730 of 15,730 non-zero references across
        all 97 level areas on the disc, with 1,444 explicit zeroes.

        What this replaces was a heuristic -- "use the nearest TEX0 that
        precedes the model" -- which is per-model rather than per-surface and
        so could only ever put one texture on a building. That is what made
        Brimstone's storefronts rainbow and Perfnew's ground wrong.
        """
        return {t[0] + 1: n for n, t in enumerate(self.texture_table())}

    def model_texture_map(self):
        """MOD0 chunk index -> position in texture_table().

        Superseded by tex_slot_map() for anything with a part table; kept for
        the handful of callers that want one texture per model, and as the
        fallback for a MOD0 that is not a container.

        A model uses the nearest TEX0 that precedes it. Verified semantically:
        `mans_whitedoor` lands on the door zones, `mwater` on the water,
        `mvegalpha` on the grass, `mans_banner` on the gazebo banner.
        """
        order = {t[0]: n for n, t in enumerate(self.texture_table())}
        out = {}
        current = None
        for c in self.chunks:
            if c.tag == "TEX0" and c.index in order:
                current = order[c.index]
            elif c.tag == "MOD0" and current is not None:
                out[c.index] = current
        return out

    # ------------------------------------------------------- render meshes

    def parts(self, index):
        """Sub-mesh directory of a multi-part MOD0 chunk, or None.

        The biggest MOD0 in a level is usually not one display list but a
        container of many:

            u16 count, u16 version(1), u32 header_size(16), u32 data_start
            count x u32  ->  8-byte (resource_chunk, flags) records
            at data_start: count x u32 -> 8-byte (offset, index_count,
            vertex_count) records, each offset a self-contained packet

        The vertex_count field is what confirms the read: it matches the
        decoded vertex total exactly for every part.
        """
        raw = self.raw(index)
        if len(raw) < 32:
            return None
        # count is u8 and the byte after it is the BONE COUNT, not the high
        # half of a u16. Reading the pair as one u16 made every character
        # model look like it had 13,313 parts, so `parts()` rejected all 620
        # of them. The bone byte matches the matching .skel exactly -- mrd 52,
        # srd 59 -- and is 0 for level containers.
        count, bone_count = raw[0], raw[1]
        version = struct.unpack_from("<H", raw, 2)[0]
        header_size, data_start = struct.unpack_from("<II", raw, 4)
        if (version != 1 or header_size != 16 or count < 1 or count > 4096
                or not (header_size <= data_start < len(raw))):
            return None
        if header_size + count * 4 > len(raw):
            return None
        # The outer table must be self-describing: `count` ascending u32
        # offsets whose records sit between the table and data_start.
        table = struct.unpack_from("<%dI" % count, raw, header_size)
        if table[0] != header_size + count * 4:
            return None
        if any(b - a != 8 for a, b in zip(table, table[1:])):
            return None
        if table[-1] + 8 > data_start:
            return None

        inner_off = data_start
        if inner_off + count * 4 > len(raw):
            return None
        inner = struct.unpack_from("<%dI" % count, raw, inner_off)
        if any(b <= a for a, b in zip(inner, inner[1:])):
            return None
        if inner[-1] + 8 > len(raw):
            return None

        out = []
        for i, rec in enumerate(inner):
            offset, index_count, vertex_count = struct.unpack_from(
                "<IHH", raw, rec)
            if not (0 < offset < len(raw)):
                return None
            end = inner and (struct.unpack_from("<I", raw, inner[i + 1])[0]
                             if i + 1 < count else len(raw))
            # The second u32 of a part record is a BYTE OFFSET, inside this
            # same chunk, to that part's 68-byte material block. It is not a
            # flag word -- it only ever takes multiples of 16 because the
            # blocks sit at a stride of 80 from a 16-aligned base, and that
            # regularity is what made it look like a bitfield for so long.
            #
            # The blocks live AFTER the inner pointer table (at
            # data_start + 4*count), which is why looking for a gap in the
            # header found nothing: there is no gap, the materials are
            # further down.
            #
            # Layout, 68 bytes:
            #   0x00  4f  base colour RGBA -- white on 22,356 of 23,042
            #             records, 0.8 grey on 569, transparent black on 117
            #   0x10  4f  zero except on 117 records
            #   0x20  4f  zero on every record on the disc
            #   0x30  4f  usually (0,0,0,1)
            #   0x40  u32 zero on every record on the disc
            resource, material = struct.unpack_from("<II", raw, table[i])
            colour = None
            if 0 < material and material + 68 <= len(raw):
                vals = struct.unpack_from("<16f", raw, material)
                # Refuse anything that is not plausibly a colour block, so a
                # future format variant degrades to "no tint" rather than to
                # nonsense multiplied over the whole surface.
                if all(0.0 <= v <= 1.0 for v in vals):
                    colour = list(vals[:4])
            out.append(dict(offset=offset, end=end, verts=vertex_count,
                            indices=index_count, resource=resource,
                            material=material, colour=colour,
                            flags=material))
        return out

    def model(self, index):
        """Decode a MOD0/TEX0 chunk's VIF display list into a mesh.

        These chunks are PS2 DMA packets, not vertex buffers -- see vif.py.
        Returns None when the packet does not yield plausible geometry, which
        is preferable to emitting garbage.
        """
        from . import vif
        c = self.chunks[index]
        if c.tag != "MOD0" or c.size < 64:
            return None
        raw = self.raw(index)

        parts = self.parts(index)
        if parts:
            return _merge_parts(vif, raw, parts, raw[1])
        return vif.extract_best(raw)

    def bone_count(self, index):
        """Bones in the skeleton this MOD0 is rigged to; 0 if it is rigid.

        Byte 1 of the chunk, and it equals the paired .skel's NumBones
        exactly -- mrd 52, srd 59 -- while level containers carry 0.
        """
        c = self.chunks[index]
        if c.tag != "MOD0" or c.size < 32:
            return 0
        return self.raw(index)[1]

    def models(self):
        """Every decodable render mesh. index -> mesh dict."""
        out = {}
        for c in self.chunks:
            if c.tag != "MOD0":
                continue
            try:
                m = self.model(c.index)
            except Exception:
                continue
            if m and m["tris"]:
                out[c.index] = m
        return out


# --------------------------------------------------------------------- write

ALIGN = 16


def _align_up(n, a=ALIGN):
    return (n + a - 1) // a * a


def check_bundle(data, label=""):
    """Every structural invariant an insert relies on. -> list of problems.

    Empty list means the bundle is one this module knows how to write into.
    Each of these was measured over the 623 `.rsc` files on the disc that carry
    a table (`scripts/probe_rsc_insert.py`), and each came back 623/623 -- so a
    file that fails one is outside what was measured, and `append_chunk`
    refuses it rather than guessing.
    """
    p = []
    if data[:4] != MAGIC:
        return ["%s: not an RSC0 bundle (%r)" % (label or "bundle", data[:4])]
    if len(data) < 12:
        return ["%s: %d bytes is too short for a header" % (label, len(data))]
    count, hsize = struct.unpack_from("<II", data, 4)
    if count == 0:
        return ["%s: no chunk table" % label]
    table_end = 12 + 8 * count
    if table_end > hsize:
        p.append("table of %d entries needs %d bytes but header_size is %d"
                 % (count, table_end, hsize))
    if hsize > len(data):
        p.append("header_size %d is past the end of a %d-byte file"
                 % (hsize, len(data)))
    if hsize % ALIGN:
        p.append("header_size %d is not %d-byte aligned" % (hsize, ALIGN))
    if p:
        return ["%s: %s" % (label or "bundle", x) for x in p]

    ends = [struct.unpack_from("<I", data, 12 + 8 * i + 4)[0]
            for i in range(count)]
    if ends != sorted(ends):
        p.append("chunk ends are not non-decreasing")
    if any(e % ALIGN for e in ends):
        p.append("not every chunk end is %d-byte aligned" % ALIGN)
    if ends[-1] != len(data):
        p.append("last chunk end is %d but the file is %d bytes -- the table "
                 "stores ENDS, and the final one is the file length"
                 % (ends[-1], len(data)))
    starts = [hsize] + ends[:-1]
    if any(e < s for s, e in zip(starts, ends)):
        p.append("at least one chunk has a negative size")
    if data[table_end:hsize].count(0) != hsize - table_end:
        p.append("the space between the table and header_size is not all "
                 "zero, so a new entry cannot be written into it")
    return ["%s: %s" % (label or "bundle", x) for x in p]


def append_chunk(data, tag, payload, label=""):
    """Append `payload` as a new chunk. -> (new bundle bytes, chunk index).

    The returned index is 0-based, as `Rsc.chunks` is; the `resourceIndex` a
    `.bnds`/`.znes` node needs for it is **index + 1**, because that field is
    1-based with 0 meaning none.

    **Appending is the only position this writes, and that is deliberate.**
    An insert anywhere else renumbers every later chunk, and `resourceIndex`
    references live in OTHER files -- the scene `.bnds`/`.znes`/`.xznes` in
    whatever archives happen to point at this bundle. Nothing inside this
    module can find them, so a mid-file insert would silently repoint every
    later reference by one. Appending leaves indices 1..count untouched and
    gives the new chunk a fresh one, which is why it needs no rewrite anywhere
    else.

    **What appending does decide is the texture binding.** `model_texture_map`
    binds a MOD0 to the nearest TEX0 *preceding* it, so a MOD0 appended at the
    end inherits the LAST TEX0 in the bundle. Existing bindings do not move --
    every existing chunk keeps its position and its order -- but the caller
    does not get to choose the new model's texture by appending. Putting it
    after a particular TEX0 is a mid-file insert, with everything above.

    Two things are kept true here because the disc keeps them true, 623/623:
    the payload is zero-padded to a %d-byte boundary so every chunk end stays
    aligned, and the new last end equals the new file length exactly.

    `header_size` is NOT constant -- 22 distinct values on the disc, 128 on 515
    files but also 64 on 30, and up to 6656. When the table has no room for
    another 8-byte entry (4 of 623 files), the header grows to the next
    aligned size and every chunk moves with it, which is why every stored end
    is rewritten rather than only the tail.
    """ % ALIGN
    problems = check_bundle(data, label)
    if problems:
        raise ValueError("cannot append to this bundle -- " +
                         "; ".join(problems))

    if isinstance(tag, str):
        tag = tag.encode("latin-1")
    if len(tag) != 4:
        raise ValueError("a chunk tag is 4 bytes, got %r" % (tag,))
    payload = bytes(payload)
    if not payload:
        raise ValueError("refusing to append a zero-length chunk: every chunk "
                         "on the disc has positive size")

    count, hsize = struct.unpack_from("<II", data, 4)
    ends = [struct.unpack_from("<I", data, 12 + 8 * i + 4)[0]
            for i in range(count)]
    tags = [data[12 + 8 * i:12 + 8 * i + 4] for i in range(count)]

    new_count = count + 1
    new_hsize = max(hsize, _align_up(12 + 8 * new_count))
    delta = new_hsize - hsize                 # bytes every chunk shifts by

    body = data[hsize:] + payload + b"\x00" * (
        _align_up(len(payload)) - len(payload))

    out = bytearray()
    out += MAGIC
    out += struct.pack("<II", new_count, new_hsize)
    for t, e in zip(tags, ends):
        out += t + struct.pack("<I", e + delta)
    out += tag + struct.pack("<I", new_hsize + len(body))
    out += b"\x00" * (new_hsize - len(out))
    out += body

    result = bytes(out)
    # Belt and braces: the thing we just wrote has to pass the same check the
    # input did. A writer that can emit a file its own reader rejects is worse
    # than no writer.
    problems = check_bundle(result, label)
    if problems:
        raise AssertionError("append_chunk produced a bundle that does not "
                             "verify -- " + "; ".join(problems))
    return result, count


def replace_chunk(data, index, payload, label=""):
    """The bundle with chunk `index` swapped for `payload`, at any length.

    Chunk NUMBERS do not move -- same tags, same order -- so every
    `resourceIndex` in a `.bnds`/`.znes`, every texture reference in a MOD0
    part table and every entry of the `.rnt` manifest still means what it
    meant. Only the stored chunk ENDS from `index` on shift, by the (16-byte
    aligned) size change; the table stores ends, not starts, which is what
    makes a resize a one-column rewrite. Contrast `append_chunk`, which
    exists for the case where a new NUMBER is wanted.
    """
    problems = check_bundle(data, label)
    if problems:
        raise ValueError("cannot rewrite this bundle -- " + "; ".join(problems))
    count, hsize = struct.unpack_from("<II", data, 4)
    if not (0 <= index < count):
        raise ValueError("chunk %d is not in a bundle of %d" % (index, count))
    payload = bytes(payload)
    if not payload:
        raise ValueError("refusing a zero-length chunk")
    ends = [struct.unpack_from("<I", data, 12 + 8 * i + 4)[0]
            for i in range(count)]
    starts = [hsize] + ends[:-1]
    padded = payload + b"\x00" * (_align_up(len(payload)) - len(payload))
    delta = len(padded) - (ends[index] - starts[index])
    out = bytearray(data[:hsize])
    for i in range(index, count):
        struct.pack_into("<I", out, 12 + 8 * i + 4, ends[i] + delta)
    out += data[hsize:starts[index]] + padded + data[ends[index]:]
    result = bytes(out)
    problems = check_bundle(result, label)
    if problems:
        raise AssertionError("replace_chunk produced a bundle that does not "
                             "verify -- " + "; ".join(problems))
    return result


def _mod0_refs(payload):
    """(outer table offsets, 1-based texture refs) of a container MOD0.

    Returns (None, None) when the payload is not the container form -- the
    same shape test `Rsc.parts` makes, kept here because a writer must not
    depend on a decode succeeding.
    """
    if len(payload) < 32:
        return None, None
    count = payload[0]
    version = struct.unpack_from("<H", payload, 2)[0]
    header_size, data_start = struct.unpack_from("<II", payload, 4)
    if (version != 1 or header_size != 16 or count < 1 or count > 4096
            or not (header_size <= data_start < len(payload))):
        return None, None
    if header_size + count * 4 > len(payload):
        return None, None
    table = struct.unpack_from("<%dI" % count, payload, header_size)
    if table[0] != header_size + count * 4:
        return None, None
    if any(b - a != 8 for a, b in zip(table, table[1:])):
        return None, None
    if table[-1] + 8 > len(payload):
        return None, None
    refs = [struct.unpack_from("<I", payload, off)[0] for off in table]
    return list(table), refs


def add_model(dst, src, src_index, label="", allow_rig_mismatch=False):
    """Copy the MOD0 at `src_index` of bundle `src` into `dst`, textures and
    all. -> (new dst bytes, report dict).

    This is M18's "add a NEW model": the destination gains a model it did not
    have, and every byte of geometry is a display list the console already
    renders. **There is no VIF writer in this codebase** -- `geomedit.py` is
    built specifically to avoid needing one -- so synthesising a display list
    from an OBJ is a separate and much larger job, and one that could only be
    settled in an emulator rather than structurally. Copying a known-good
    packet is what can be verified from here.

    What makes this more than an append: a container MOD0's per-surface
    texture reference is a ONE-BASED chunk index, and it indexes the bundle it
    came from. Copied across bundles unchanged, every reference points at
    whatever chunk happens to sit at that index in the destination -- which
    resolves to *something* and renders as the wrong texture rather than as an
    error. So each referenced TEX0 is copied too, with the MIP0 and PAL0 it
    names at 0x90, and all three sets of indices are rewritten:

        MIP0, PAL0 appended  ->  the copied TEX0's 0x90 pair repointed
        TEX0 appended        ->  the copied MOD0's part refs repointed
        MOD0 appended last   ->  so it also follows its own TEX0 in disc
                                 order, which keeps the nearest-preceding-TEX0
                                 fallback in `model_texture_map` right too

    Copying **within one bundle** (`src == dst`) needs none of that: the refs
    already point at chunks that exist, so the textures are not duplicated and
    the references are left exactly as they are.

    Refuses rather than guesses when a reference does not resolve to a TEX0,
    or when a TEX0 does not carry its own palette/pixel pair -- in both cases
    the safe thing is unknown, and writing anyway produces a file that looks
    fine and draws wrong.

    **The skeleton is the same trap one level up, and it cannot be fixed from
    here.** Byte 1 of a container MOD0 is the bone count of the skeleton it is
    rigged to, and that skeleton is an EXTERNAL `.skel` chosen per character --
    nothing in the bundle names it. A rigged mesh copied into a character with
    a different skeleton keeps bone indices that mean different joints, which
    animates wrong rather than failing. So a rigged source is refused unless
    the destination already carries a model with the same bone count; that is
    a necessary check and not a sufficient one -- two 53-bone skeletons are not
    necessarily the same skeleton, which is the same "several skeletons share
    one bone count" problem `bvh.candidate_skeletons` exists for. Pass
    `allow_rig_mismatch=True` to override it deliberately, having checked.
    Rigid models (bone count 0) are unaffected.
    """
    sr = Rsc(src, label=label or "source")
    if not (0 <= src_index < len(sr.chunks)):
        raise ValueError("chunk %d is not in a bundle of %d"
                         % (src_index, len(sr.chunks)))
    if sr.chunks[src_index].tag != "MOD0":
        raise ValueError("chunk %d is %s, not a MOD0"
                         % (src_index, sr.chunks[src_index].tag))

    payload = bytearray(sr.raw(src_index))
    table, refs = _mod0_refs(payload)
    same_bundle = bytes(src) == bytes(dst)

    bones = sr.bone_count(src_index)
    if bones and not same_bundle and not allow_rig_mismatch:
        dr = Rsc(dst, label=label or "destination")
        have = {dr.bone_count(c.index) for c in dr.chunks if c.tag == "MOD0"}
        if bones not in have:
            raise ValueError(
                "the source model is rigged to a %d-bone skeleton and the "
                "destination carries no model with that bone count (%s) -- "
                "its bone indices would mean different joints. Pass "
                "allow_rig_mismatch=True if you have checked the skeletons "
                "themselves" % (bones, sorted(have) or "none"))

    out = dst
    report = {"textures": [], "parts": 0 if refs is None else len(refs),
              "same_bundle": same_bundle, "container": refs is not None,
              "bones": bones}

    if refs is not None and not same_bundle:
        remap = {}
        for r in sorted({r for r in refs if r}):
            if not (1 <= r <= len(sr.chunks)):
                raise ValueError(
                    "a part references chunk %d, outside the source's %d"
                    % (r, len(sr.chunks)))
            tex = sr.chunks[r - 1]
            if tex.tag != "TEX0":
                raise ValueError(
                    "a part references chunk %d which is %s, not a TEX0 -- "
                    "refusing to copy a binding this module cannot follow"
                    % (r - 1, tex.tag))
            pair = sr.tex_sources(r - 1)
            if pair is None:
                raise ValueError(
                    "TEX0 chunk %d does not carry its own palette/pixel pair "
                    "at 0x90, so its MIP0 and PAL0 cannot be identified "
                    "without guessing disc order" % (r - 1))
            pal, mip = pair
            out, mi = append_chunk(out, "MIP0", sr.raw(mip.index), label=label)
            out, pi = append_chunk(out, "PAL0", sr.raw(pal.index), label=label)
            tex_payload = bytearray(sr.raw(r - 1))
            struct.pack_into("<II", tex_payload, 0x90, pi + 1, mi + 1)
            out, ti = append_chunk(out, "TEX0", bytes(tex_payload), label=label)
            remap[r] = ti + 1
            report["textures"].append(
                {"source": r - 1, "tex0": ti, "mip0": mi, "pal0": pi})
        for off, r in zip(table, refs):
            if r:
                struct.pack_into("<I", payload, off, remap[r])

    out, model_index = append_chunk(out, "MOD0", bytes(payload), label=label)
    report["model"] = model_index
    report["resource_index"] = model_index + 1
    return out, report


def _merge_parts(vif, raw, parts, bone_count=0):
    """Decode every sub-packet of a container MOD0 into one mesh.

    Each part carries its own resource reference, kept per-vertex so a
    container still shows the right texture on each surface.
    """
    verts, tris, colors, uvs, part_of = [], [], [], [], []
    normals, bones, weights = [], [], []
    ranges, voff, quant = [], [], []
    addrs = set()
    skinned = False
    for p in parts:
        # The part table states the packet start outright, so decode there
        # rather than rescanning for it. Byte-identical to the old scan on
        # every level sub-packet, and it is the only way the skinned form is
        # found at all: its grammar rejects a misaligned start, so a scan
        # simply returns nothing.
        m = vif.extract_mesh(raw, p["offset"], p["end"],
                             rigged=bone_count > 0)
        if not m:
            continue
        base = len(verts)
        tri_base = len(tris)
        verts.extend(m["verts"])
        tris.extend((base + a, base + b, base + c) for a, b, c in m["tris"])
        # Each part draws with its own texture, so record where its triangles
        # land. This is what lets one building carry a wall texture and a roof
        # texture instead of whichever one happened to win.
        ranges.append((p["resource"], tri_base, len(m["tris"]),
                       p.get("colour")))
        # Offsets are relative to the whole chunk already -- extract_mesh is
        # handed the chunk and a span, not a slice -- so they need no rebasing.
        # A skinned part sends quantised positions and yields none; pad with
        # None so `voff` stays index-parallel with `verts` either way.
        vo = m.get("voff")
        voff.extend(vo if vo and len(vo) == len(m["verts"])
                    else [None] * len(m["verts"]))
        # Skinned parts also carry the quantisation their positions were
        # encoded against, which is what makes them writable. A rigid part
        # has none, and pads with None so the list stays index-parallel.
        qz = m.get("quant")
        quant.extend(qz if qz and len(qz) == len(m["verts"])
                     else [None] * len(m["verts"]))
        colors.extend(m["colors"])
        uvs.extend(m["uvs"])
        part_of.extend([p["resource"]] * len(m["verts"]))
        addrs.update(m["addr"])
        if m.get("skinned"):
            skinned = True
            normals.extend(m["normals"])
            bones.extend(m["bones"])
            weights.extend(m["weights"])
        else:
            normals.extend([(0.0, 0.0, 0.0)] * len(m["verts"]))
            bones.extend([None] * len(m["verts"]))
            weights.extend([] for _ in m["verts"])
    if not verts:
        return None
    out = dict(verts=verts, tris=tris, colors=colors[:len(verts)], uvs=uvs,
               addr=sorted(addrs), unpacks=len(parts), start=0,
               parts=len(parts), part_resource=part_of,
               part_ranges=ranges, voff=voff, quant=quant)
    if skinned:
        out.update(skinned=True, normals=normals, bones=bones,
                   weights=weights, bone_count=bone_count)
    return out


RNT_TAGS = {b"MOD0", b"TEX0", b"MPH0", b"MIP0", b"PAL0", b"PMT0",
            b"SKE0", b"SAN0", b"BND0", b"NTG0", b"RSV0"}


def parse_rnt(data):
    """The `.rnt` manifest: chunk index -> the artist's name for that chunk.

    Without it the chunks in a character bundle are anonymous, and treating
    them as LODs by ordinal is simply wrong -- `mrd_lods.rsc` holds
    `mrd_lod_01`, `mrd_lod_02`, `mrd_face` and `hat_mrd_lod_01`, i.e. two body
    LODs plus a face and a hat. Asking it for "LOD 2" hands you a
    disembodied head.

        "RNT0"  u32 count
        entries of (u32 name_offset, char[4] tag, u32 resource_index)
        then a block of NUL-terminated names

    The stored resource index is **1-based**: `mrd_lod_01` says 4 and is
    chunk 3. Parsing is deliberately tolerant -- anchor on the tag, take the
    u32 after it as the index, and accept whichever adjacent u32 lands on a
    real name start -- because the entry field order varies within the file.
    """
    if len(data) < 12 or data[:4] != b"RNT0":
        return {}
    last = 0
    for i in range(len(data) - 4):
        if data[i:i + 4] in RNT_TAGS:
            last = i + 4
    names = {}
    i = last
    while i < len(data):
        j = data.find(b"\x00", i)
        if j < 0:
            break
        if j > i:
            names[i] = data[i:j].decode("latin-1")
        i = j + 1

    out = {}
    for i in range(len(data) - 8):
        if data[i:i + 4] not in RNT_TAGS:
            continue
        index = struct.unpack_from("<I", data, i + 4)[0]
        name = None
        for cand in (i - 4, i + 8):
            if 0 <= cand <= len(data) - 4:
                v = struct.unpack_from("<I", data, cand)[0]
                if v in names:
                    name = names[v]
                    break
        if name and index:
            out[index - 1] = name          # 1-based on disc
    return out


def area_stem(rsc_name):
    """'lvl_gunshop.rsc' -> 'gunshop'. The playable area's own name."""
    n = rsc_name.rsplit(".", 1)[0]
    return n[4:] if n.startswith("lvl_") else n


def level_areas(dave):
    """Every playable area bundle in an archive, largest first.

    An archive is not one level. TOMB4 carries the whole of Brimstone plus
    six interiors (bank, bar, clothes shop, general store, gunshop, sheriff)
    as seven sibling bundles, and picking only one of them hid the rest.
    `_nav`/`_view` are the nav grid and visibility set, not geometry, and
    `*_lods.rsc` bundles are characters and props rather than world.
    """
    out = []
    for e in dave:
        if e.ext != "rsc" or not e.name.startswith("lvl_"):
            continue
        if e.name.endswith(("_nav.rsc", "_view.rsc")):
            continue
        out.append(e)
    out.sort(key=lambda e: -e.size_full)
    return [e.name for e in out]


def _match_score(stem, base):
    """How well an area stem answers to an archive's base name."""
    if stem == base:
        return 400
    # Archive names are capped at 8 characters with the mission Variation
    # appended, so TOMB4 is lvl_tomb variation 4 and ROYFINA7 is a truncated
    # lvl_royfinal. Matching on the letters alone is what connects them.
    trimmed = base.rstrip("0123456789")
    if stem == trimmed:
        return 300
    if trimmed and (stem.startswith(trimmed) or trimmed.startswith(stem)):
        return 200
    if base in stem or stem in base:
        return 100
    return 0


def find_level_areas(dave, level_name):
    """Area bundles in an archive, best match for `level_name` first.

    Ranked rather than filtered: a plain substring test never connected
    TOMB4 to lvl_tomb, so the search fell through to the first bundle with
    any geometry at all -- a character LOD model standing in for the world.
    """
    base = level_name.lower().rsplit(".", 1)[0]
    areas = level_areas(dave)
    return sorted(areas, key=lambda n: (-_match_score(area_stem(n), base),
                                        areas.index(n)))


def find_rsc_names(dave, level_name):
    """Every candidate geometry bundle, best level match first."""
    areas = find_level_areas(dave, level_name)
    seen = set(areas)
    rest = [e.name for e in dave if e.ext == "rsc" and e.name not in seen
            and not e.name.endswith(("_nav.rsc", "_view.rsc"))]
    return areas + rest
