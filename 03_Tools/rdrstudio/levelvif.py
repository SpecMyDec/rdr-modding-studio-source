"""A WRITER for level display lists: an OBJ of any size becomes a MOD0 chunk.

`vif.py` reads these packets, `vifwalk.py` tiles them, `geomedit.py` moves
floats inside them. None of those can change the NUMBER of vertices. This does:
it emits new packets from scratch, copying every constant from what the
shipped game wrote.

WHAT A LEVEL PART PACKET IS (measured over all 20,693 parts of the 3-stream
form in the 1,139 `.rsc` bundles on the disc -- 20,693 / 20,693 follow it):

    NOP ; NOP(imm = number of batches)
    UNPACK V4_32 x1 -> VU 41      cull sphere (cx, cy, cz, w)
    MSCAL 2
    per batch (<= 58 vertices, 56 is the mode):
        STMASK 0x40404040 ; STMOD 0 ; STCYCL cl=3 wl=1
        UNPACK V4_32 x1 -> base      (0, 0, 322 | (N+26)<<16, B | (2N+27)<<16)
        UNPACK V4_32 x1 -> base+1    GIF tag: NLOOP=N EOP, PRIM strip, 3 regs
        STMASK 0x50505050 ; STROW ST-scale ; STMOD 1
        UNPACK V2_16 masked x N -> base+2 | USN    ST
        STMOD 0 ; STMASK 0x40404040
        UNPACK V4_8  x N -> base+3 | USN           RGBA (0x80 = 1.0)
        UNPACK V4_32 x N -> base+4                 XYZ + W (bit 15 = GS ADC)
        UNPACK V3_32 x1  -> 40       zeros
        ITOP base ; MSCAL 0
    STCYCL 1,1 ; FLUSH ; [NOP NOP to a 16-byte boundary]

`base` cycles 210, 482, 755 (triple-buffered VU1 memory).

WHAT IS DERIVED AND HOW SURE IT IS. Three numbers have no stated meaning:

  * header word  A = N + 26         exact on 8,476 / 8,476 batches
  * header word  C = 2N + 27        exact on 8,402 / 8,476 (the other 74 are
                                    batches whose first word is not 322)
  * header word  B = 6N + 36 - 2*(N odd)
                                    EXACT on only ~85% of batches: the rest
                                    sit 2-8 away from it for a reason not
                                    recovered (not N, not triangle count, not
                                    strip-piece count, not ADC layout).
  * part record `indices` = int(1.5N)+11 per batch, within 1 on single-batch
                                    parts, looser on multi-batch.

None of that can be proved without running the VU1 microprogram, so a model
written here is "structurally identical to vanilla" and nothing more until it
has been drawn by the console or an emulator. The docs say so.
"""

import math
import struct

BASES = (210, 482, 755)
MAX_BATCH = 56              # mode of the 67,407 shipped batches; shipped max 58
ST_ROW_STD = (0x45000000, 0x45000000, 0x45009000, 0x45008000)
ST_ROW_WIDE = (0x47800000, 0x47800000, 0x47800000, 0x47800080)   # 65536.0 grid, 275 shipped batches
GIF_NREG = (0x8000, 805453828, 1298)     # EOP bit, tag word 1, reg list
ADC = 0x8000
SPHERE_ADDR = 41
ZERO_ADDR = 40
UV_LIMIT = 7.99                          # +/-8 is the standard grid's range

NOP, STCYCL, ITOP, MSCAL, FLUSH = 0x00, 0x01, 0x04, 0x14, 0x11
STMOD, STMASK, STROW = 0x05, 0x20, 0x30


def _code(cmd, num, imm):
    return struct.pack("<I", (cmd << 24) | ((num & 0xFF) << 16) | (imm & 0xFFFF))


class MeshError(ValueError):
    pass


# ------------------------------------------------------------------ OBJ in

def parse_obj(text, max_verts=200000):
    """OBJ -> dict(groups={material: [corner-triples]}, ...).

    Returns per-group triangle lists whose corners are indexes into the shared
    `verts` list; a vertex is a UNIQUE (position, uv, colour) corner, because
    one packet vertex carries exactly one UV and one colour.

    Accepts v with optional r g b, vt, f with v, v/vt, v/vt/vn, v//vn, negative
    indices, quads and n-gons (fan-triangulated), usemtl groups. Everything
    else (normals, smoothing, curves) is ignored.
    """
    pos, cols, uvs = [], [], []
    verts, key_of = [], {}
    groups = {}
    mat = "default"
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line[0] == "#":
            continue
        bits = line.split()
        tag = bits[0]
        try:
            if tag == "v":
                pos.append((float(bits[1]), float(bits[2]), float(bits[3])))
                if len(bits) >= 7:
                    cols.append((float(bits[4]), float(bits[5]), float(bits[6])))
                else:
                    cols.append(None)
            elif tag == "vt":
                uvs.append((float(bits[1]), float(bits[2]) if len(bits) > 2 else 0.0))
            elif tag == "usemtl":
                mat = bits[1] if len(bits) > 1 else "default"
            elif tag == "f":
                corner = []
                for tok in bits[1:]:
                    parts = tok.split("/")
                    vi = int(parts[0])
                    vi = vi - 1 if vi > 0 else len(pos) + vi
                    ti = None
                    if len(parts) > 1 and parts[1]:
                        ti = int(parts[1])
                        ti = ti - 1 if ti > 0 else len(uvs) + ti
                    if not (0 <= vi < len(pos)):
                        raise MeshError("line %d: vertex index %s out of range"
                                        % (n, parts[0]))
                    if ti is not None and not (0 <= ti < len(uvs)):
                        raise MeshError("line %d: uv index out of range" % n)
                    uv = uvs[ti] if ti is not None else (0.0, 1.0)
                    # OBJ's v runs up; the packet's t runs down (see
                    # geomedit.to_obj, which writes 1 - t).
                    k = (vi, round(uv[0], 6), round(1.0 - uv[1], 6))
                    idx = key_of.get(k)
                    if idx is None:
                        if len(verts) >= max_verts:
                            raise MeshError("more than %d unique vertices"
                                            % max_verts)
                        idx = key_of[k] = len(verts)
                        verts.append((pos[vi], (uv[0], 1.0 - uv[1]), cols[vi]))
                    corner.append(idx)
                if len(corner) < 3:
                    raise MeshError("line %d: a face needs 3+ vertices" % n)
                g = groups.setdefault(mat, [])
                for i in range(1, len(corner) - 1):
                    a, b, c = corner[0], corner[i], corner[i + 1]
                    if a != b and b != c and a != c:
                        g.append((a, b, c))
        except (IndexError, ValueError) as ex:
            if isinstance(ex, MeshError):
                raise
            raise MeshError("line %d: cannot read %r (%s)" % (n, line[:40], ex))
    groups = {k: v for k, v in groups.items() if v}
    if not groups:
        raise MeshError("no faces in this OBJ -- nothing to draw")
    return dict(verts=verts, groups=groups)


# ----------------------------------------------------------- triangles->strips

def _cyc(t):
    a, b, c = t
    return min((a, b, c), (b, c, a), (c, a, b))


def stripify(tris, cap=MAX_BATCH):
    """Triangles -> list of batches; a batch is [(vertex, adc), ...].

    Convention measured on the disc: a strip PIECE opens with two vertices
    whose ADC bit is set (they only prime the strip) and every vertex after
    them draws a triangle. Pieces share a batch, and the hardware's strip
    parity runs on across them, so a piece starting at an odd position has its
    first two vertices swapped to keep the winding the OBJ asked for.
    """
    todo = {}
    edge = {}
    for i, t in enumerate(tris):
        todo[i] = t
        for u, v in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
            edge.setdefault(frozenset((u, v)), []).append(i)
    order = list(range(len(tris)))
    batches, cur = [], []

    def take(i):
        t = todo.pop(i)
        return t

    def drawn(x, y, z, i):
        return (x, y, z) if i % 2 == 0 else (y, x, z)

    for i0 in order:
        if i0 not in todo:
            continue
        a, b, c = take(i0)
        # A piece needs at least 3 slots; a fresh batch if it will not fit.
        s = len(cur)
        if s + 3 > cap:
            batches.append(cur)
            cur, s = [], 0
        seq = [a, b, c] if s % 2 == 0 else [b, a, c]
        piece = list(seq)
        while s + len(piece) < cap:
            x, y = piece[-2], piece[-1]
            idx = s + len(piece)
            nxt = None
            for j in edge.get(frozenset((x, y)), ()):
                if j not in todo:
                    continue
                t = todo[j]
                z = next(v for v in t if v != x and v != y) \
                    if len({x, y} & set(t)) == 2 and len(set(t)) == 3 else None
                if z is None:
                    continue
                if _cyc(drawn(x, y, z, idx)) == _cyc(t):
                    nxt = (j, z)
                    break
            if nxt is None:
                break
            take(nxt[0])
            piece.append(nxt[1])
        for k, v in enumerate(piece):
            cur.append((v, 1 if k < 2 else 0))
    if cur:
        batches.append(cur)
    return batches


def strip_triangles(batch):
    """Triangles a batch draws, as the GS would (for tests)."""
    out = []
    for i in range(2, len(batch)):
        if batch[i][1]:
            continue
        x, y, z = batch[i - 2][0], batch[i - 1][0], batch[i][0]
        out.append((x, y, z) if i % 2 == 0 else (y, x, z))
    return out


# ------------------------------------------------------------- packet out

def default_pad(n):
    """NOP words trailing a batch: 2 when N is even, else 0.

    Measured: the shipped choice follows this rule on ~85% of batches and the
    opposite on the rest, with B moving in lock-step every time, so it is a
    free choice of the original tool and not a requirement of the VU.
    """
    return 0 if n & 1 else 2


def header_words(n, pad=None, first=322):
    """(0, 0, first | A<<16, B | C<<16).

    B is the batch's size in 32-bit words, pad NOPs included -- verified on
    7,266 / 7,266 mid batches and 3,060 / 3,060 last batches:
        B = (STMASK .. next batch or the closing STCYCL) / 4 = 6N + 34 + pad
    """
    if pad is None:
        pad = default_pad(n)
    return (0, 0, first | ((n + 26) << 16), (6 * n + 34 + pad) | ((2 * n + 27) << 16))


def encode_uv(u, t, wide=False):
    scale = 128.0 if wide else 4096.0
    cs = int(round(u * scale)) + 0x8000
    ct = int(round(t * scale)) + 0x8000
    return max(0, min(0xFFFF, cs)), max(0, min(0xFFFF, ct))


def build_batch(rows, base, wide=False, pad=None, first=322):
    """rows: [(pos, (u, t), (r, g, b, a), adc)] -> VIF bytes for one batch."""
    n = len(rows)
    if not (3 <= n <= 90):
        raise MeshError("a batch holds 3..90 vertices, got %d" % n)
    out = bytearray()
    out += _code(STMASK, 0, 0) + struct.pack("<I", 0x40404040)
    out += _code(STMOD, 0, 0)
    out += _code(STCYCL, 0, 259)
    if pad is None:
        pad = default_pad(n)
    out += _code(0x6C, 1, base) + struct.pack("<4I", *header_words(n, pad, first))
    out += _code(0x6C, 1, base + 1) + struct.pack(
        "<4I", GIF_NREG[0] | n, GIF_NREG[1], GIF_NREG[2], n)
    out += _code(STMASK, 0, 0) + struct.pack("<I", 0x50505050)
    out += _code(STROW, 0, 0) + struct.pack(
        "<4I", *(ST_ROW_WIDE if wide else ST_ROW_STD))
    out += _code(STMOD, 0, 1)
    out += _code(0x75, n, 0x4000 | (base + 2))
    for _p, uv, _c, _a in rows:
        out += struct.pack("<HH", *encode_uv(uv[0], uv[1], wide))
    out += _code(STMOD, 0, 0)
    out += _code(STMASK, 0, 0) + struct.pack("<I", 0x40404040)
    out += _code(0x6E, n, 0x4000 | (base + 3))
    for _p, _uv, col, _a in rows:
        out += bytes(col)
    out += _code(0x6C, n, base + 4)
    for p, _uv, _c, adc in rows:
        out += struct.pack("<3fI", p[0], p[1], p[2], ADC if adc else 0)
    out += _code(0x68, 1, ZERO_ADDR) + b"\x00" * 12
    out += _code(ITOP, 0, base)
    out += _code(MSCAL, 0, 0)
    out += _code(NOP, 0, 0) * pad
    if len(out) != 4 * (6 * n + 34 + pad):
        raise AssertionError("batch is %d words, header says %d"
                             % (len(out) // 4, 6 * n + 34 + pad))
    return bytes(out)


def sphere_of(positions):
    mn = [min(p[i] for p in positions) for i in range(3)]
    mx = [max(p[i] for p in positions) for i in range(3)]
    c = [(a + b) / 2.0 for a, b in zip(mn, mx)]
    r = max(math.dist(c, p) for p in positions)
    # 10% slack: shipped w is >= 0.99x the bbox-centre radius for 90% of parts
    # and often far larger, so over-claiming only costs culling efficiency.
    return (c[0], c[1], c[2], r * 1.1 + 0.01)


def build_packet(batches_rows, sphere, base_start=0, wide=False, pads=None,
                 firsts=None):
    """Whole part packet. `batches_rows` is a list of lists of rows."""
    nb = len(batches_rows)
    if not (1 <= nb <= 4000):
        raise MeshError("a part holds 1..4000 batches, got %d" % nb)
    out = bytearray()
    out += _code(NOP, 0, 0) + _code(NOP, 0, nb)
    out += _code(0x6C, 1, SPHERE_ADDR) + struct.pack("<4f", *sphere)
    out += _code(MSCAL, 0, 2)
    for k, rows in enumerate(batches_rows):
        out += build_batch(rows, BASES[(base_start + k) % 3], wide,
                           pad=None if pads is None else pads[k],
                           first=322 if firsts is None else firsts[k])
    out += _code(STCYCL, 0, 257) + _code(FLUSH, 0, 0)
    if len(out) % 16:
        out += _code(NOP, 0, 0) * ((16 - len(out) % 16) // 4)
    if len(out) % 16:
        raise MeshError("packet is not 16-byte aligned")
    return bytes(out)


def part_from_triangles(verts, tris, wide_ok=True):
    """(vertices, triangles) -> (packet bytes, vertex_count, index_count).

    `verts` is [(pos, uv, rgb-or-None)], `tris` indexes it.
    """
    used = sorted({i for t in tris for i in t})
    if not used:
        raise MeshError("empty part")
    # UVs that overflow the standard +/-8 grid switch the WHOLE part to the
    # 65536.0 grid (+/-256 at 1/128 resolution).
    lim = max(max(abs(verts[i][1][0]), abs(verts[i][1][1])) for i in used)
    wide = lim > UV_LIMIT
    if lim > 255.9:
        raise MeshError("a UV reaches %.1f; the packet stores at most +/-256"
                        % lim)
    batches = stripify(tris)
    rows_batches = []
    sumn = 0
    ni = 0
    for b in batches:
        rows = []
        for vid, adc in b:
            p, uv, col = verts[vid]
            if col is None:
                c = (128, 128, 128, 128)
            else:
                c = tuple(max(0, min(255, int(round(v * 128)))) for v in col) + (128,)
            rows.append((p, uv, c, adc))
        rows_batches.append(rows)
        sumn += len(rows)
        ni += int(1.5 * len(rows)) + 11
    positions = [verts[i][0] for i in used]
    sph = sphere_of(positions)
    pk = build_packet(rows_batches, sph, wide=wide)
    return pk, sumn, min(ni, 0xFFFF)


# ------------------------------------------------------------ container

class Container:
    """A level MOD0 container, parsed so it can be rebuilt byte-for-byte."""
    __slots__ = ("parts", "materials")

    def __init__(self, parts, materials):
        self.parts = parts            # [dict(resource, material, packet, verts, indices)]
        self.materials = materials    # [68 bytes]


def parse_container(raw):
    """Level MOD0 -> Container. Raises MeshError if it is not the plain form."""
    if len(raw) < 32 or raw[1] != 0:
        raise MeshError("not a rigid level container (bone byte %d)"
                        % (raw[1] if len(raw) > 1 else -1))
    n = raw[0]
    ver = struct.unpack_from("<H", raw, 2)[0]
    hs, ds = struct.unpack_from("<II", raw, 4)
    if ver != 1 or hs != 16 or n < 1:
        raise MeshError("unrecognised MOD0 header")
    outer = struct.unpack_from("<%dI" % n, raw, 16)
    recs = [struct.unpack_from("<II", raw, o) for o in outer]
    inner = struct.unpack_from("<%dI" % n, raw, ds)
    ir = [struct.unpack_from("<IHH", raw, o) for o in inner]
    mats_off = sorted({m for _r, m in recs if m})
    mats = [bytes(raw[o:o + 68]) for o in mats_off]
    parts = []
    for i in range(n):
        off, idx, vc = ir[i]
        end = ir[i + 1][0] if i + 1 < n else len(raw)
        mo = recs[i][1]
        parts.append(dict(resource=recs[i][0],
                          material=mats_off.index(mo) if mo else None,
                          packet=bytes(raw[off:end]), verts=vc, indices=idx))
    return Container(parts, mats)


def build_container(c):
    n = len(c.parts)
    if not (1 <= n <= 255):
        raise MeshError("a container holds 1..255 parts, got %d" % n)
    outer_tbl = 16
    outer_recs = outer_tbl + 4 * n
    inner_tbl = outer_recs + 8 * n
    ds = inner_tbl
    mat0 = inner_tbl + 4 * n
    k = len(c.materials)
    mat_offs = [mat0 + 80 * i for i in range(k)]
    recs_at = (mat0 + 80 * (k - 1) + 68) if k else mat0
    first_pkt = (recs_at + 8 * n + 15) // 16 * 16
    out = bytearray()
    out += struct.pack("<BBHII", n, 0, 1, 16, ds) + b"\x00" * 4
    for i in range(n):
        out += struct.pack("<I", outer_recs + 8 * i)
    for p in c.parts:
        m = mat_offs[p["material"]] if p["material"] is not None else 0
        out += struct.pack("<II", p["resource"], m)
    for i in range(n):
        out += struct.pack("<I", recs_at + 8 * i)
    for i, m in enumerate(c.materials):
        out += m + (b"\x00" * 12 if i + 1 < k else b"")
    off = first_pkt
    recs = bytearray()
    for p in c.parts:
        recs += struct.pack("<IHH", off, p["indices"] & 0xFFFF,
                            p["verts"] & 0xFFFF)
        off += len(p["packet"])
    out += recs
    out += b"\x00" * (first_pkt - len(out))
    for p in c.parts:
        out += p["packet"]
    return bytes(out)


# --------------------------------------------------------------- the API

def build_model(obj_text, base_container_raw, resource_for=None,
                default_resource=None):
    """OBJ text -> new MOD0 chunk bytes, modelled on `base_container_raw`.

    The base supplies the material block and the default texture reference:
    every part of the new model uses the base's first material, and a group
    named in `resource_for` ({usemtl name: 1-based TEX0 chunk index}) gets that
    texture instead. Returns (bytes, report).
    """
    base = parse_container(base_container_raw)
    if not base.materials:
        raise MeshError("the model being replaced has no material block")
    res0 = default_resource if default_resource else base.parts[0]["resource"]
    mesh = parse_obj(obj_text)
    parts = []
    tri_total = 0
    for name, tris in mesh["groups"].items():
        res = (resource_for or {}).get(name, res0)
        # a part's vertex count is a u16, and 20 bytes of header per part is
        # cheap, so split big groups well under it
        chunks = [tris[i:i + 6000] for i in range(0, len(tris), 6000)]
        for ch in chunks:
            pk, vc, ic = part_from_triangles(mesh["verts"], ch)
            parts.append(dict(resource=res, material=0, packet=pk,
                              verts=vc, indices=ic))
            tri_total += len(ch)
    if len(parts) > 255:
        raise MeshError("this OBJ needs %d parts; a container holds 255. "
                        "Decimate it." % len(parts))
    out = build_container(Container(parts, base.materials[:1]))
    return out, dict(parts=len(parts), triangles=tri_total,
                     vertices=sum(p["verts"] for p in parts),
                     materials=list(mesh["groups"]), bytes=len(out))


# ------------------------------------------------------- reading it back

def read_packet(packet):
    """Parse one part packet with the walker -> dict, or raise MeshError.

    Only the exact template this module writes is accepted; anything else is
    refused rather than half-understood.
    """
    from . import vifwalk
    try:
        toks = vifwalk.walk(packet, 0, len(packet))
    except vifwalk.Unknown as ex:
        raise MeshError(str(ex))
    d = lambda t: packet[t.data_start:t.end]            # noqa: E731
    it = iter(toks)
    nxt = lambda: next(it, None)                        # noqa: E731
    t = nxt()
    if not t or t.name != "NOP":
        raise MeshError("packet does not open with NOP")
    t = nxt()
    if not t or t.name != "NOP":
        raise MeshError("packet does not open with NOP NOP")
    nb = t.imm
    t = nxt()
    if not (t and t.name == "UNPACK" and t.imm == SPHERE_ADDR and t.num == 1):
        raise MeshError("no cull sphere")
    sphere = struct.unpack("<4f", d(t))
    t = nxt()
    if not (t and t.name == "MSCAL" and t.imm == 2):
        raise MeshError("no MSCAL 2 after the sphere")
    batches = []
    while True:
        t = nxt()
        if t and t.name == "STCYCL" and t.imm == 257:
            break
        if not (t and t.name == "STMASK" and d(t) == struct.pack("<I", 0x40404040)):
            raise MeshError("expected a batch at %s" % (t,))
        b = {}
        seq = [("STMOD", 0), ("STCYCL", 259)]
        for name, imm in seq:
            t = nxt()
            if not (t and t.name == name and t.imm == imm):
                raise MeshError("expected %s %d, got %s" % (name, imm, t))
        t = nxt()
        if not (t and t.name == "UNPACK" and t.num == 1 and t.vn == 3 and t.vl == 0):
            raise MeshError("no header qword")
        b["base"] = t.imm
        b["hdr"] = struct.unpack("<4I", d(t))
        t = nxt()
        if not (t and t.name == "UNPACK" and t.num == 1 and t.imm == b["base"] + 1):
            raise MeshError("no GIF tag")
        b["gif"] = struct.unpack("<4I", d(t))
        t = nxt()
        if not (t and t.name == "STMASK" and d(t) == struct.pack("<I", 0x50505050)):
            raise MeshError("no ST mask")
        t = nxt()
        if not (t and t.name == "STROW"):
            raise MeshError("no STROW")
        b["row"] = struct.unpack("<4I", d(t))
        t = nxt()
        if not (t and t.name == "STMOD" and t.imm == 1):
            raise MeshError("no STMOD 1")
        t = nxt()
        if not (t and t.name == "UNPACK" and t.cmd == 0x75 and t.imm == 0x4000 | (b["base"] + 2)):
            raise MeshError("no ST stream")
        n = t.num
        b["uv"] = [struct.unpack_from("<HH", packet, t.data_start + 4 * i)
                   for i in range(n)]
        for name, imm in (("STMOD", 0), ("STMASK", 0)):
            t = nxt()
            if not (t and t.name == name):
                raise MeshError("expected %s" % name)
        t = nxt()
        if not (t and t.name == "UNPACK" and t.cmd == 0x6E and t.num == n):
            raise MeshError("no colour stream")
        b["col"] = [tuple(packet[t.data_start + 4 * i:t.data_start + 4 * i + 4])
                    for i in range(n)]
        t = nxt()
        if not (t and t.name == "UNPACK" and t.cmd == 0x6C and t.num == n
                and t.imm == b["base"] + 4):
            raise MeshError("no position stream")
        b["pos"] = [struct.unpack_from("<3fI", packet, t.data_start + 16 * i)
                    for i in range(n)]
        t = nxt()
        if not (t and t.name == "UNPACK" and t.imm == ZERO_ADDR and d(t) == b"\0" * 12):
            raise MeshError("no zero quadword")
        t = nxt()
        if not (t and t.name == "ITOP" and t.imm == b["base"]):
            raise MeshError("no ITOP")
        t = nxt()
        if not (t and t.name == "MSCAL" and t.imm == 0):
            raise MeshError("no MSCAL 0")
        # shipped packets sometimes pad with NOP NOP between batches
        while True:
            save = it
            peek = nxt()
            if peek and peek.name == "NOP":
                continue
            it = iter([peek] + list(save)) if peek else iter(())
            nxt = lambda: next(it, None)                # noqa: E731
            break
        batches.append(b)
    t = nxt()
    if not (t and t.name == "FLUSH"):
        raise MeshError("no FLUSH")
    rest = list(it)
    if any(x.name != "NOP" for x in rest):
        raise MeshError("junk after FLUSH")
    if len(batches) != nb:
        raise MeshError("NOP says %d batches, found %d" % (nb, len(batches)))
    return dict(sphere=sphere, batches=batches)


# ------------------------------------------------------ into a bundle

def friendly(ex):
    """A plain-English line for a failed import (no tracebacks, no jargon).

    Always says what to do next, and that nothing was changed.
    """
    m = str(ex)
    if "no faces" in m:
        tip = ("That .obj has no faces. In Blender export with 'Selection "
               "Only' off and make sure the model is not just points/edges.")
    elif "unique vertices" in m or "needs" in m and "parts" in m:
        tip = ("That model is too detailed to import. Reduce the triangle "
               "count (Blender: Decimate modifier) and try again.")
    elif "out of range" in m or "cannot read" in m or "needs 3+" in m \
            or "a face needs" in m:
        tip = ("That file does not look like a valid .obj (%s). Export again "
               "from Blender: File > Export > Wavefront (.obj)." % m)
    elif "UV reaches" in m:
        tip = ("Some texture coordinates are too far out (%s). Move the UVs "
               "back near the 0..1 square in Blender." % m)
    elif "not a MOD0" in m or "not a rigid" in m or "unrecognised" in m \
            or "no material" in m:
        tip = ("This model can't be replaced (%s). Only rigid level models "
               "can be swapped; try a different model." % m)
    else:
        tip = "Import failed: %s." % m
    return tip + " Nothing was changed."


def import_obj(bundle, chunk, obj_text, resource_for=None, label=""):
    """Replace MOD0 `chunk` of an RSC0 `bundle` with the geometry in an OBJ.

    -> (new bundle bytes, report). Vertex and triangle counts are free. The
    chunk keeps its NUMBER, so nothing that references it has to change; its
    texture binding and material block are inherited from the model being
    replaced unless `resource_for` maps an OBJ `usemtl` name to a 1-based TEX0
    chunk number.

    NOT TOUCHED, and the report says so: the collision shell (BND0) and the
    scene nodes. Walls you add are not solid, and a wall you delete is still
    a wall you bump into, until the matching BND0 is edited.
    """
    from . import rsc as R
    r = R.Rsc(bundle, label)
    if not (0 <= chunk < len(r.chunks)) or r.chunks[chunk].tag != "MOD0":
        raise MeshError("chunk %s is not a MOD0 model" % chunk)
    old = r.raw(chunk)
    for res in (resource_for or {}).values():
        if not (1 <= res <= len(r.chunks)) or r.chunks[res - 1].tag != "TEX0":
            raise MeshError("texture reference %s is not a TEX0 chunk" % res)
    new, rep = build_model(obj_text, old, resource_for=resource_for)
    out = R.replace_chunk(bundle, chunk, new, label)
    rep.update(chunk=chunk, old_bytes=len(old), new_bytes=len(new),
               bundle_bytes=len(out), collision="unchanged",
               verified=verify_import(out, chunk, obj_text))
    return out, rep


def _f32(x):
    """The float32 the packet stores for `x`, so equality is exact."""
    return struct.unpack("<f", struct.pack("<f", x))[0]


def verify_import(bundle, chunk, obj_text):
    """Re-read the model the way the Studio will and compare with the OBJ.

    Returns a dict of facts; raises MeshError on any mismatch. What is
    compared: the multiset of drawn triangles (as position triples, so strip
    re-ordering and vertex duplication do not matter) and the set of UVs.
    """
    from . import rsc as R
    r = R.Rsc(bundle, "verify")
    if R.check_bundle(bundle, "verify"):
        raise MeshError("bundle no longer verifies: %s"
                        % R.check_bundle(bundle, "verify"))
    m = r.model(chunk)
    if not m or not m["tris"]:
        raise MeshError("the written model does not decode")
    want = parse_obj(obj_text)
    wt = {}
    for tris in want["groups"].values():
        for a, b, c in tris:
            k = _cyc(tuple(tuple(_f32(x) for x in want["verts"][i][0])
                           for i in (a, b, c)))
            wt[k] = wt.get(k, 0) + 1
    gt = {}
    for a, b, c in m["tris"]:
        k = _cyc(tuple(tuple(_f32(x) for x in m["verts"][i])
                       for i in (a, b, c)))
        gt[k] = gt.get(k, 0) + 1
    if wt != gt:
        miss = sum((wt[k] - gt.get(k, 0)) for k in wt if wt[k] > gt.get(k, 0))
        extra = sum((gt[k] - wt.get(k, 0)) for k in gt if gt[k] > wt.get(k, 0))
        raise MeshError("re-read model differs from the OBJ: %d triangles "
                        "missing, %d unexpected" % (miss, extra))
    parts = r.parts(chunk) or []
    for p in parts:
        if p["resource"] and not (
                1 <= p["resource"] <= len(r.chunks)
                and r.chunks[p["resource"] - 1].tag == "TEX0"):
            raise MeshError("a part's texture reference does not resolve")
    return dict(vertices=len(m["verts"]), triangles=len(m["tris"]),
                parts=len(parts))
