"""Skinned (character) display lists: a decoder AND an encoder for one batch.

`vif.py` reads skinned geometry; `vifwalk.py` tiles any packet. This module
is what a WRITER needs: every field of a skinned batch, including the four
header words nobody could explain until 2026-11-05, and the rules for the
words that are not data. See 05_Docs/SKINNED-HEADER-DECODE.md for the evidence.

A rigged-model batch (cl=3/wl=1: each vertex is three VU quadwords apart) is:

    STMASK 40404040 | STMOD 0 | STCYCL 0x103
    UNPACK V4_32 x1  @T       header qword  (6, nrec, 0x1116|A<<16, B|C<<16)
    UNPACK V4_32 x1  @T+1     GIF tag       (0x8000|N, 0x30024004, 0x512, N)
    STMASK 50505050 | STROW uv-row | STMOD 1 | UNPACK V2_16 xN @T+2 | STMOD 0
    STMASK 40404040 x2 | STROW nrm-row | STMOD 1 | UNPACK V3_8 xN @T+3 | STMOD 0
    STROW pos-row | STMOD 1 | UNPACK V4_16 xN @T+4 | STMOD 0
    UNPACK V3_32 x1  @0x28    bias = pos-row + 0x8000 (float bit pattern)
    [STCYCL 0x101 | UNPACK V4_32 x2*nrec @X]     secondary influences
    ITOP T | MSCAL 0 | NOP x((-pos) mod 16 / 4)

where T = 0xD2 + 4*bones + k*S, k cycling 0,1,2 and S = (1024 - base)//3.
"""
import math
import struct

from . import vifwalk

MASK_PLAIN = 0x40404040
MASK_UV = 0x50505050
UV_ROW = (0x45000000, 0x45000000, 0x45009000, 0x45008000)
NRM_ROW = (0x47800000, 0x47800000, 0x47800000, 0x47800080)
POS_ROW_NARROW = (0x44800000, 0x44800000, 0x44800000, 0)    # +-4 m, ulp 1/8192
POS_ROW_WIDE = (0x46000000, 0x46000000, 0x46000000, 0)      # +-32 m, ulp 1/1024
GIF_W1, GIF_W2 = 0x30024004, 0x512
HDR_W0, HDR_FIRST = 6, 0x1116
BONE_BASE = 0xD2


class Unrecognised(ValueError):
    pass


def base_addr(bones):
    return BONE_BASE + 4 * bones


def stride(bones):
    return (1024 - base_addr(bones)) // 3


def c_word(n):
    """Header word C: 36 + ceil(7N/4). Exact on all 15,813 vanilla batches."""
    return 36 + (7 * n + 3) // 4


def header_words(n, nrec, size_words):
    return (HDR_W0, nrec, HDR_FIRST | ((n + 26) << 16),
            size_words | (c_word(n) << 16))


def _vc(imm, num, cmd):
    return struct.pack("<I", (imm & 0xFFFF) | ((num & 0xFF) << 16) | (cmd << 24))


def _pad16(n_bytes):
    return b"\x00" * ((-n_bytes) % 16)


def _u(raw, t):
    return raw[t.data_start:t.end]


def split_packet(raw, start, end):
    """-> (prologue bytes, [batch token lists], epilogue bytes, tokens)."""
    toks, pe = vifwalk.walk_packet(raw, start, end)
    starts = [i for i in range(len(toks) - 2)
              if toks[i].name == "STMASK" and toks[i + 1].name == "STMOD"
              and toks[i + 2].name == "STCYCL" and toks[i + 2].imm == 0x103]
    if not starts:
        raise Unrecognised("no batch")
    # epilogue = trailing STCYCL + FLUSH (+ zero pad to 16)
    last = len(toks)
    if toks[-1].name == "FLUSH" and toks[-2].name == "STCYCL":
        last = len(toks) - 2
    pro = raw[start:toks[starts[0]].offset]
    batches = []
    for k, s in enumerate(starts):
        e = starts[k + 1] if k + 1 < len(starts) else last
        batches.append(toks[s:e])
    epi = raw[toks[last].offset:pe] if last < len(toks) else b""
    return pro, batches, epi, toks, pe


def decode_batch(raw, tl):
    """Token list of ONE batch -> field dict. Raises Unrecognised on any
    deviation from the template above (a wide V4_32 position batch is
    recognised too, as pos_wide=True)."""
    names = [t.name for t in tl]
    un = [t for t in tl if t.is_unpack]
    if len(un) < 6:
        raise Unrecognised("too few unpacks")
    hdr, gif, uv, nrm, pos = un[:5]
    rest = un[5:]
    d = {}
    d["itop"] = tl[[i for i, t in enumerate(tl) if t.name == "ITOP"][0]].imm
    d["hdr"] = struct.unpack("<4I", _u(raw, hdr))
    d["gif"] = struct.unpack("<4I", _u(raw, gif))
    n = uv.num
    if not (nrm.num == pos.num == n):
        raise Unrecognised("stream counts differ")
    d["n"] = n
    d["pos_wide"] = pos.vl == 0
    d["uv"] = [struct.unpack_from("<HH", _u(raw, uv), 4 * i) for i in range(n)]
    nb = _u(raw, nrm)
    d["nrm"] = [tuple(nb[3 * i:3 * i + 3]) for i in range(n)] \
        if len(nb) >= 3 * n else None
    d["nrm_raw"] = nb
    d["pos_raw"] = _u(raw, pos)
    rows = [struct.unpack("<4I", _u(raw, t)) for t in tl if t.name == "STROW"]
    d["rows"] = rows
    bias = [t for t in rest if t.vn == 2 and t.vl == 0]
    d["bias"] = struct.unpack("<3I", _u(raw, bias[0])) if bias else None
    ex = [t for t in rest if t.vn == 3 and t.vl == 0 and t.num > 1]
    d["extra_addr"] = ex[0].imm if ex else None
    d["extra_raw"] = _u(raw, ex[0]) if ex else b""
    d["nrec"] = len(d["extra_raw"]) // 32
    d["pos_row"] = rows[2] if len(rows) > 2 else None
    pad = 0
    for t in reversed(tl):
        if t.name != "NOP":
            break
        pad += 1
    d["pad"] = pad
    return d


def emit_batch(d, base_offset=0):  # base_offset kept for API symmetry
    """Field dict -> bytes. `d` needs: itop, n, uv, nrm_raw, pos_raw, pos_row
    (4 words), extra_raw/extra_addr, size_words (filled in by the caller)."""
    out = bytearray()
    t, n = d["itop"], d["n"]
    pos_row = d.get("pos_row", POS_ROW_NARROW)
    # bias = the quantiser's centre: the row's float bit pattern + 0x8000,
    # shifted per axis (vanilla shifts it freely, e.g. +0x200 on Y).
    bias = d.get("bias") or tuple((pos_row[k] + 0x8000) & 0xFFFFFFFF
                                  for k in range(3))
    nrec = len(d.get("extra_raw", b"")) // 32
    out += _vc(0, 0, 0x20) + struct.pack("<I", MASK_PLAIN)
    out += _vc(0, 0, 0x05) + _vc(0x103, 0, 0x01)
    sizew = d.get("size_words")
    hdr = header_words(n, nrec, sizew if sizew is not None else 0)
    out += _vc(t, 1, 0x6C) + struct.pack("<4I", *hdr)
    out += _vc(t + 1, 1, 0x6C) + struct.pack("<4I", 0x8000 | n, GIF_W1, GIF_W2, n)
    out += _vc(0, 0, 0x20) + struct.pack("<I", MASK_UV)
    out += _vc(0, 0, 0x30) + struct.pack("<4I", *UV_ROW)
    out += _vc(1, 0, 0x05)
    uvb = b"".join(struct.pack("<HH", s, tt) for s, tt in d["uv"])
    out += _vc(0x4000 | (t + 2), n, 0x75) + uvb + _pad4(len(uvb))
    out += _vc(0, 0, 0x05)
    out += _vc(0, 0, 0x20) + struct.pack("<I", MASK_PLAIN)
    out += _vc(0, 0, 0x20) + struct.pack("<I", MASK_PLAIN)
    out += _vc(0, 0, 0x30) + struct.pack("<4I", *NRM_ROW)
    out += _vc(1, 0, 0x05)
    out += _vc(0x4000 | (t + 3), n, 0x7A) + d["nrm_raw"]
    out += _vc(0, 0, 0x05)
    out += _vc(0, 0, 0x30) + struct.pack("<4I", *pos_row)
    out += _vc(1, 0, 0x05)
    out += _vc(0x4000 | (t + 4), n, 0x6D) + d["pos_raw"]
    out += _vc(0, 0, 0x05)
    out += _vc(0x28, 1, 0x68) + struct.pack("<3I", *bias)
    if nrec:
        out += _vc(0x101, 0, 0x01)
        out += _vc(d["extra_addr"], nrec * 2, 0x6C) + d["extra_raw"]
    out += _vc(t, 0, 0x04) + _vc(0, 0, 0x14)
    # NOP pad: the default rule makes the batch a whole number of qwords
    # (14,961 + 144 of the 15,361 narrow vanilla batches). The other 256 are
    # all the FIRST batch of a part, 2 words longer: a free choice of the
    # original tool, so it is carried in `pad` and honoured when present.
    pad = d.get("pad")
    if pad is None:
        pad = ((-len(out)) % 16) // 4
    out += b"\x00\x00\x00\x00" * pad
    return bytes(out)


def _pad4(n):
    return b"\x00" * ((-n) % 4)


def encode_batch(d, base_offset=0):
    """Two-pass emit: the header's B word is the batch's own size in words,
    NOP pad included, so it is measured after the first pass."""
    first = emit_batch(dict(d, size_words=0), base_offset)
    return emit_batch(dict(d, size_words=len(first) // 4), base_offset)


# ------------------------------------------------------------- container
# The MOD0 container of a rigged model is the level container (levelvif) with
# raw[1] = bone count; the rest of the layout is identical.

class Container:
    __slots__ = ("bones", "parts", "materials", "tail")

    def __init__(self, bones, parts, materials, tail=b""):
        self.bones = bones
        self.parts = parts
        self.materials = materials
        self.tail = tail


def parse_container(raw):
    if len(raw) < 32 or raw[1] == 0:
        raise Unrecognised("not a rigged container")
    n = raw[0]
    ver = struct.unpack_from("<H", raw, 2)[0]
    hs, ds = struct.unpack_from("<II", raw, 4)
    if ver != 1 or hs != 16 or n < 1:
        raise Unrecognised("unrecognised MOD0 header")
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
    return Container(raw[1], parts, mats)


def build_container(c):
    n = len(c.parts)
    if not (1 <= n <= 255):
        raise Unrecognised("a container holds 1..255 parts, got %d" % n)
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
    out += struct.pack("<BBHII", n, c.bones, 1, 16, ds) + b"\x00" * 4
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
