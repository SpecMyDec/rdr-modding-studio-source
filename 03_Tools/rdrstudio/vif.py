"""PS2 VIF display-list decoding.

Renderable geometry on this disc is not a vertex buffer -- it is a DMA packet
for the PS2's VIF1 unit, the same bytes the hardware consumed in 2004. Vertex
data arrives as UNPACK commands that write into VU1 memory.

VIFcode is one 32-bit little-endian word:
    bits  0-15  IMMEDIATE   (for UNPACK: the destination VU address)
    bits 16-23  NUM         (element count)
    bits 24-31  CMD

UNPACK commands are 0x60..0x7F:
    CMD = 0x60 | (m << 4) | (vn << 2) | vl
    vn  0=S 1=V2 2=V3 3=V4          how many components
    vl  0=32bit 1=16bit 2=8bit 3=V4-5   component width

So 0x6C = V4_32 (4 floats, 16 bytes each) and 0x6E = V4_8 (4 bytes, RGBA).

Positions arrive as V4_32 where W is not a float but flags; bit 15 is the GS
ADC bit, which suppresses the triangle ending on that vertex. That is how one
long triangle strip encodes many disjoint pieces.
"""

import struct

# VIFcodes that carry no inline payload.
_NO_DATA = {0x00, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
            0x10, 0x11, 0x13, 0x14, 0x15, 0x17}

VN_COUNT = {0: 1, 1: 2, 2: 3, 3: 4}
VL_BITS = {0: 32, 1: 16, 2: 8, 3: 5}

ADC_BIT = 0x8000


BONE_BASE = 210          # 0xD2: VU1 quadword holding bone matrix 0
BONE_STRIDE = 4          # one 4x4 matrix per bone
BIAS_ADDR = 0x28         # V3_32 the VU subtracts from every skinned position


def _ulp(bits):
    """The float in `bits`, and the distance to the next float above it."""
    a = struct.unpack("<f", struct.pack("<I", bits))[0]
    b = struct.unpack("<f", struct.pack("<I", bits + 1))[0]
    return a, b - a


class Unpack:
    __slots__ = ("offset", "addr", "num", "vn", "vl", "masked", "data", "cmd",
                 "row", "mode", "usn", "cl", "wl", "row_off")

    def __init__(self, offset, addr, num, vn, vl, masked, cmd, data,
                 row=(0, 0, 0, 0), mode=0, usn=False, cl=1, wl=1,
                 row_off=None):
        self.offset = offset
        self.addr = addr
        self.num = num
        self.vn = vn
        self.vl = vl
        self.masked = masked
        self.cmd = cmd
        self.data = data
        # VIF unit state in force when this UNPACK ran. For 32-bit streams it
        # is decoration; for the 16-bit skinned position stream it IS the
        # format. STMOD 1 makes the hardware ADD the ROW register to every
        # element, and ROW holds a float -- so adding an integer advances it
        # that many ULPs. The scale therefore lives in a register, not in a
        # constant anyone gets to choose.
        self.row = row        # 4 raw u32 words of the ROW register
        self.row_off = row_off   # byte offset of those words
        self.mode = mode      # STMOD: 0 none, 1 offset (add ROW), 2 difference
        self.usn = usn        # UNPACK imm bit 14: unsigned
        self.cl = cl
        self.wl = wl

    @property
    def kind(self):
        return "V%d_%d" % (VN_COUNT[self.vn], VL_BITS[self.vl])

    def floats(self):
        """V4_32 / V3_32 payload as tuples of floats."""
        if VL_BITS[self.vl] != 32:
            return []
        n = VN_COUNT[self.vn]
        out = []
        for i in range(self.num):
            o = i * n * 4
            if o + n * 4 > len(self.data):
                break
            out.append(struct.unpack_from("<%df" % n, self.data, o))
        return out

    def raw_words(self):
        n = VN_COUNT[self.vn]
        out = []
        for i in range(self.num):
            o = i * n * 4
            if o + n * 4 > len(self.data):
                break
            out.append(struct.unpack_from("<%dI" % n, self.data, o))
        return out

    def bytes4(self):
        """Colour payload as (r, g, b, a) tuples.

        Handles both V4_8 (one byte per channel) and V4_5, the packed
        RGBA5551 form some meshes use instead.
        """
        if self.vl == 3:  # V4-5: 16 bits, RGBA5551
            out = []
            for i in range(self.num):
                o = i * 2
                if o + 2 > len(self.data):
                    break
                v = self.data[o] | (self.data[o + 1] << 8)
                out.append((((v) & 0x1F) << 3, ((v >> 5) & 0x1F) << 3,
                            ((v >> 10) & 0x1F) << 3, 0x80 if v & 0x8000 else 0))
            return out
        if VL_BITS[self.vl] != 8:
            return []
        n = VN_COUNT[self.vn]
        return [tuple(self.data[i * n:i * n + n]) for i in range(self.num)
                if (i + 1) * n <= len(self.data)]

    def __repr__(self):
        return "<Unpack %s num=%d addr=0x%X @0x%X>" % (
            self.kind, self.num, self.addr, self.offset)


def parse(data, start=0, end=None):
    """Walk a VIF packet and return every UNPACK found.

    Unknown or malformed codes stop the walk rather than guessing -- a
    desynced parse would emit convincing garbage.
    """
    if end is None:
        end = len(data)
    out = []
    i = start
    # Unit state carried onto each UNPACK. STROW and STMOD used to be skipped
    # as payload-only codes; the skinned position format is unreadable
    # without them.
    row = (0, 0, 0, 0)
    row_off = None
    mode = 0
    cl = wl = 1
    while i + 4 <= end:
        imm, num, cmd = struct.unpack_from("<HBB", data, i)
        i += 4

        if cmd == 0x05:            # STMOD -- must precede the _NO_DATA test
            mode = imm & 3
            continue
        if cmd in _NO_DATA:
            continue
        if cmd == 0x01:            # STCYCL
            cl, wl = imm & 0xFF, (imm >> 8) & 0xFF
            continue
        if cmd == 0x20:            # STMASK  + 1 word
            i += 4
            continue
        if cmd == 0x30:            # STROW + 4 words
            if i + 16 <= end:
                row = struct.unpack_from("<4I", data, i)
                # Where those words live, so a writer can change the grid.
                # A skinned position's precision and range are set entirely by
                # this register, so rewriting it is how you widen the window a
                # character's vertices have to move in.
                row_off = i
            i += 16
            continue
        if cmd == 0x31:            # STCOL + 4 words
            i += 16
            continue
        if cmd == 0x4A:            # MPG: microprogram, num*8 bytes
            i += (num or 256) * 8
            continue
        if cmd in (0x50, 0x51):    # DIRECT / DIRECTHL: imm * 16 bytes
            i += (imm or 65536) * 16
            continue

        if 0x60 <= cmd <= 0x7F:
            vl = cmd & 0x03
            vn = (cmd >> 2) & 0x03
            masked = bool(cmd & 0x10)
            count = num or 256
            bits = VL_BITS[vl]
            comps = VN_COUNT[vn]
            if vl == 3:            # V4-5: 16 bits per element regardless
                size = 2 * count
            else:
                size = comps * (bits // 8) * count
            if i + size > end:
                break
            out.append(Unpack(i - 4, imm & 0x3FF, count, vn, vl, masked, cmd,
                              data[i:i + size], row=row, mode=mode,
                              row_off=row_off,
                              usn=bool(imm & 0x4000), cl=cl, wl=wl))
            # A VIF packet is a stream of 32-bit words, so an UNPACK payload
            # that is not a whole number of words is padded up to one. A
            # V3_8 batch of 6 occupies 20 bytes, not 18 -- advancing by the
            # unpadded length desyncs the walk on the very next code and
            # throws away the rest of the model.
            i += (size + 3) & ~3
            continue

        # Anything else means we have lost sync.
        break
    return out


def strip_to_triangles(vertices, adc):
    """Triangle strip with GS ADC breaks -> triangle index list.

    A set ADC bit means "do not draw the triangle ending here", which is how
    separate surfaces share one strip.
    """
    tris = []
    for i in range(2, len(vertices)):
        if adc[i]:
            continue
        # Strips alternate winding.
        if i % 2 == 0:
            tris.append((i - 2, i - 1, i))
        else:
            tris.append((i - 1, i - 2, i))
    return tris


def _extract_at(data, start, end, world_limit):
    """Best-effort mesh from a VIF packet.

    A batch uploads three streams to consecutive VU addresses:
        A-2  V2_16  UVs
        A-1  V4_8   vertex colours
        A    V4_32  positions

    VU1 memory is multi-buffered, so a packet cycles through several base
    addresses (0xD6, 0x1E6, 0x2F7 ...). Recognising a batch by that *relative*
    layout rather than by one fixed address is what makes all of them come
    through -- keying on a single "best" address silently drops two thirds of
    the geometry in a triple-buffered packet.
    """
    unpacks = parse(data, start, end)
    if not unpacks:
        return None

    # Walk in packet order. UVs and colours are uploaded immediately before
    # the positions they belong to, so match them per batch; matching against
    # the mesh's TOTAL vertex count only works for single-batch meshes.
    verts, adc, tris, colors, uvs = [], [], [], [], []
    # Byte offset of each vertex's X float within `data`, parallel to `verts`.
    # This is what makes level geometry editable: a position is four floats in
    # the packet, so moving a wall is an in-place patch of the same length --
    # no re-serialising a VIF stream, and it always fits its archive slot.
    voff = []
    addrs = set()
    pending_uv = pending_col = None

    for u in unpacks:
        if u.kind == "V2_16":
            pending_uv = u
            continue
        # Colours appear in four encodings across the disc: one byte per
        # channel with or without alpha (V4_8 / V3_8), and packed RGBA5551
        # likewise (V4_5 / V3_5). The batch layout is identical in all cases.
        if u.kind in ("V4_8", "V3_8", "V4_5", "V3_5"):
            pending_col = u
            continue
        if u.kind != "V4_32":
            continue

        # A position stream is identified by its neighbours, not its address.
        is_positions = (
            pending_col is not None and pending_col.addr == u.addr - 1
            and pending_col.num == u.num
            and pending_uv is not None and pending_uv.addr == u.addr - 2
            and pending_uv.num == u.num)
        if not is_positions:
            continue
        addrs.add(u.addr)

        base = len(verts)
        words = u.raw_words()
        chunk_v, chunk_adc = [], []
        for i, v in enumerate(u.floats()):
            chunk_v.append((v[0], v[1], v[2]))
            w = words[i][3] if i < len(words) else 0
            chunk_adc.append(bool(w & ADC_BIT))
            # `u.offset` is the VIFcode; the payload starts one word later.
            voff.append(u.offset + 4 + i * 16)
        # Each UNPACK is its own strip; do not bridge across them.
        for a, b, c in strip_to_triangles(chunk_v, chunk_adc):
            tris.append((base + a, base + b, base + c))
        verts.extend(chunk_v)
        adc.extend(chunk_adc)

        n = len(chunk_v)
        # UVs are unsigned 16-bit biased by 0x8000 with 4096 = 1.0. A clean
        # quad decodes to exactly (0,0) (0,1) (1,0) (1,1) this way; reading
        # them as plain signed fixed point puts everything at the s16 extremes.
        for i in range(n):
            s, t = struct.unpack_from("<HH", pending_uv.data, i * 4)
            uvs.append(((s - 0x8000) / 4096.0, (t - 0x8000) / 4096.0))
        cols = pending_col.bytes4()[:n]
        # V3 forms carry no alpha; pad so downstream indexing stays uniform.
        colors.extend(c if len(c) == 4 else (c + (128,))[:4] for c in cols)
        if len(cols) < n:
            colors.extend([(128, 128, 128, 128)] * (n - len(cols)))

        pending_uv = pending_col = None

    # Gate on plausibility. A desynced parse produces float garbage many
    # orders of magnitude outside the world, so require that nearly every
    # vertex lands in a believable range before trusting the mesh at all.
    if not verts:
        return None
    good = sum(1 for v in verts if all(abs(c) < world_limit for c in v))
    if good < len(verts) * 0.98:
        return None

    return dict(verts=verts, tris=tris, colors=colors[:len(verts)],
                uvs=uvs, addr=sorted(addrs), unpacks=len(unpacks), start=start,
                voff=voff)


def _extract_skinned_at(data, start, end, world_limit):
    """Mesh from a SKINNED VIF packet -- the five-stream character form.

    Five streams instead of three, and the positions are 16-bit fixed point
    whose scale lives in the VIF ROW register rather than in the file:

        A-2  V4_32 num=1   VU loop control
        A-1  V4_32 num=1   GS GIFtag (PRIM = TRIANGLE STRIP, NLOOP = N)
        A    V2_16 num=N   UVs
        A+1  V3_8  num=N   unit normals (NOT colours -- there is no alpha)
        A+2  V4_16 num=N   positions, bone-local, W = ADC bit | bone slot
        0x28 V3_32 num=1   per-batch de-quantisation bias, closes the batch
             V4_32 even    optional secondary skin influences

    Recognised by RELATIVE address only: a batch base is 0xD2 + 4*bone_count
    and is multi-buffered, so no absolute address is stable across models
    (a 52-bone character starts at 0x1A2, a 5-bone prop at 0xEA).
    """
    unpacks = parse(data, start, end)
    if not unpacks:
        return None

    verts, tris, colors, uvs = [], [], [], []
    normals, bones, weights = [], [], []
    # Per-vertex byte offset of the position, and the quantisation in force
    # for it. A skinned position is not a float: it is a u16 code read as
    #     value[k] = row[k] + code * ulp[k] - bias[k]
    # so writing one back means inverting that, which needs the row, the ulp
    # step and the batch bias -- none of which are recoverable from the mesh
    # afterwards. Recording them here is what makes custom characters
    # possible at all.
    voff, quant = [], []
    addrs = set()
    batches = []                 # (first_vertex, pos_addr, count)
    tails = []                   # (unpack, len(batches) when seen)
    state = {"uv": None, "col": None, "pos": None}

    def flush(bias):
        uv, col, pos = state["uv"], state["col"], state["pos"]
        state["uv"] = state["col"] = state["pos"] = None
        if pos is None or uv is None or col is None:
            return
        if not (col.addr == uv.addr + 1 and pos.addr == uv.addr + 2):
            return
        if not (uv.num == col.num == pos.num):
            return
        # Some skinned batches send positions as full V4_32 floats instead of
        # quantised V4_16. That layout is byte-identical to the three-stream
        # LEVEL batch, so it is accepted only when the batch was closed by its
        # own bias quadword at BIAS_ADDR -- the level form has no such
        # quadword, which is what keeps the two apart.
        wide = pos.kind == "V4_32"
        rowf = step = None
        if wide:
            if bias is None:
                return
        else:
            # A 16-bit stream is only a position if the hardware was in offset
            # mode against an unsigned unpack. Anything else is another format.
            if not pos.usn or pos.mode != 1:
                return
            rowf, step = [], []
            for k in range(3):
                f, u = _ulp(pos.row[k])
                rowf.append(f)
                step.append(u)
            if rowf[0] <= 0.0:
                return
            if bias is None:
                bias = [rowf[k] + 0x8000 * step[k] for k in range(3)]

        n = pos.num
        base = len(verts)
        chunk_v, chunk_adc = [], []
        for i in range(n):
            if wide:
                x, y, z = struct.unpack_from("<3f", pos.data, i * 16)
                w = struct.unpack_from("<I", pos.data, i * 16 + 12)[0]
                chunk_v.append((x, y, z))
                voff.append(pos.offset + 4 + i * 16)
                quant.append(None)          # already floats
            else:
                x, y, z, w = struct.unpack_from("<4H", pos.data, i * 8)
                chunk_v.append(tuple(rowf[k] + c * step[k] - bias[k]
                                     for k, c in enumerate((x, y, z))))
                # `pos.offset` is the VIFcode; the payload starts a word later.
                voff.append(pos.offset + 4 + i * 8)
                quant.append((tuple(rowf), tuple(step), tuple(bias)))
            chunk_adc.append(bool(w & ADC_BIT))
            slot = w & 0x7FFF
            # slot 0 means the sub-mesh is rigid: no bone, no influences.
            bones.append((slot - BONE_BASE) // BONE_STRIDE
                         if slot >= BONE_BASE else None)
        for a, b, c in strip_to_triangles(chunk_v, chunk_adc):
            tris.append((base + a, base + b, base + c))
        verts.extend(chunk_v)
        addrs.add(pos.addr)

        for i in range(n):
            s, t = struct.unpack_from("<HH", uv.data, i * 4)
            uvs.append(((s - 0x8000) / 4096.0, (t - 0x8000) / 4096.0))
        for c in col.bytes4()[:n]:
            normals.append(tuple(v / 128.0 - 1.0 for v in c[:3]))
        while len(normals) < len(verts):
            normals.append((0.0, 0.0, 0.0))
        cols = col.bytes4()[:n]
        colors.extend(c if len(c) == 4 else (c + (128,))[:4] for c in cols)
        if len(cols) < n:
            colors.extend([(128, 128, 128, 128)] * (n - len(cols)))
        weights.extend([] for _ in range(n))
        batches.append((base, pos.addr, n))

    for u in unpacks:
        if u.kind == "V2_16":
            if state["pos"] is not None:
                flush(None)
            state["uv"] = u
        elif u.kind in ("V3_8", "V4_8", "V4_5", "V3_5"):
            state["col"] = u
        elif u.kind == "V4_16":
            state["pos"] = u
        elif (u.kind == "V4_32" and u.num > 1 and state["uv"] is not None
              and state["pos"] is None
              and u.addr == state["uv"].addr + 2
              and u.num == state["uv"].num):
            state["pos"] = u
        elif u.kind == "V3_32" and u.num == 1 and u.addr == BIAS_ADDR:
            flush(list(struct.unpack_from("<3f", u.data, 0)))
        elif u.kind == "V4_32" and u.num >= 2 and u.num % 2 == 0:
            tails.append((u, len(batches)))
    if state["pos"] is not None:
        flush(None)

    if not verts:
        return None

    # Secondary influences: 2 quadwords per record,
    #   qw0 = (ox, oy, oz, weight) in the extra bone's local frame
    #   qw1 = (dest vertex VU addr, bone matrix VU addr, pad, pad)
    # The block trails the batch it belongs to, but some batches have none,
    # so resolve by address rather than by position.
    for tail, seen in tails:
        for cand in (seen - 1, seen):
            if not (0 <= cand < len(batches)):
                continue
            base, paddr, n = batches[cand]
            recs, ok = [], True
            for i in range(0, tail.num - 1, 2):
                wt = struct.unpack_from("<f", tail.data, i * 16 + 12)[0]
                dst, badr = struct.unpack_from("<2I", tail.data, (i + 1) * 16)
                d = dst - paddr
                if (d < 0 or d % 3 or d // 3 >= n or badr < BONE_BASE
                        or (badr - BONE_BASE) % BONE_STRIDE):
                    ok = False
                    break
                recs.append((base + d // 3,
                             (badr - BONE_BASE) // BONE_STRIDE, wt))
            if ok and recs:
                for vi, bi, wt in recs:
                    weights[vi].append((bi, wt))
                break

    for i, extra in enumerate(weights):
        if bones[i] is not None:
            extra.insert(0, (bones[i], 1.0 - sum(w for _, w in extra)))

    good = sum(1 for v in verts if all(abs(c) < world_limit for c in v))
    if good < len(verts) * 0.98:
        return None

    return dict(verts=verts, tris=tris, colors=colors[:len(verts)], uvs=uvs,
                addr=sorted(addrs), unpacks=len(unpacks), start=start,
                skinned=True, normals=normals[:len(verts)], bones=bones,
                weights=weights, batches=len(batches),
                voff=voff, quant=quant)


def quantise(value, q):
    """Invert the skinned position encoding: float -> the u16 code.

    Decoding is `value = row + code*ulp - bias`, so the code is
    `(value + bias - row) / ulp` rounded. Clamped to the u16 range, which is
    also the model's representable extent -- a character's positions are
    bone-local and the range is sized to the character, so a vertex cannot be
    pushed arbitrarily far without saturating.
    """
    rowf, step, bias = q
    out = []
    for k in range(3):
        if step[k] == 0:
            out.append(0)
            continue
        c = int(round((value[k] + bias[k] - rowf[k]) / step[k]))
        out.append(max(0, min(0xFFFF, c)))
    return out


def dequantise(code, q):
    """The float a u16 code decodes to. The exact inverse of quantise()."""
    rowf, step, bias = q
    return tuple(rowf[k] + code[k] * step[k] - bias[k] for k in range(3))


def to_bind_pose(mesh, bone_world):
    """Bone-local skinned verts -> bind-pose world space.

    `bone_world` is skeleton.world_positions() in .skel FILE ORDER. The bind
    pose is translation-only, so this is an add, not a matrix multiply.
    """
    if not mesh.get("skinned"):
        return mesh["verts"]
    out = []
    for v, b in zip(mesh["verts"], mesh["bones"]):
        if b is None or b >= len(bone_world):
            out.append(v)
        else:
            p = bone_world[b]
            out.append((v[0] + p[0], v[1] + p[1], v[2] + p[2]))
    return out


def extract_best(data, limit=384, world_limit=4096.0):
    """Find the packet start and mesh together.

    Chunk headers vary in size, so rather than assume one, try every 4-byte
    aligned offset and keep whichever yields the most plausible geometry.
    Scoring on the mesh itself (not on UNPACK count) is what matters: a
    slightly-wrong offset still decodes into UNPACKs, just into nonsense.

    The three-stream level form is tried first and wins outright when it
    finds anything -- that is what protects the existing 100% level coverage
    from the looser skinned matcher.
    """
    best, best_score = None, 0
    for off in range(0, min(limit, max(0, len(data) - 4)), 4):
        try:
            m = _extract_at(data, off, None, world_limit)
        except Exception:
            continue
        if not m or not m["tris"]:
            continue
        score = len(m["verts"]) + len(m["tris"])
        if score > best_score:
            best, best_score = m, score
    if best is not None:
        return best
    for off in range(0, min(limit, max(0, len(data) - 4)), 4):
        try:
            m = _extract_skinned_at(data, off, None, world_limit)
        except Exception:
            continue
        if not m or not m["tris"]:
            continue
        score = len(m["verts"]) + len(m["tris"])
        if score > best_score:
            best, best_score = m, score
    return best


def extract_mesh(data, start=0, end=None, world_limit=4096.0, rigged=False):
    """Decode a mesh from a known packet offset (see extract_best).

    `rigged` comes from the container's own bone-count byte. When set, the
    skinned grammar is tried FIRST -- a skinned batch that sends positions as
    V4_32 is byte-identical to the three-stream level batch, so the level
    reader would claim it and return a mesh with no bones, leaving the model
    collapsed in bone-local space (ji2_lods came out 0.67 m instead of 1.68).
    With rigged=False this is exactly the previous behaviour.
    """
    if rigged:
        m = _extract_skinned_at(data, start, end, world_limit)
        if m is not None and m["tris"]:
            return m
    m = _extract_at(data, start, end, world_limit)
    if m is not None and m["tris"]:
        return m
    return _extract_skinned_at(data, start, end, world_limit) or m
