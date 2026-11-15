"""A complete VIF1 packet walker: every byte accounted for, or it says so.

`vif.py` extracts geometry. It looks for the UNPACKs it needs and is free to
skip anything it does not recognise, which is exactly right for reading. It is
not enough for WRITING: to emit a display list with a different vertex count I
have to know what every other byte in the packet is for, not just the three
streams carrying vertices.

So this walks the whole packet and returns a token per command, with its
payload span. The oracle is simple and unforgiving: the tokens must tile the
packet with no gap and no overlap. A gap means something is being read as data
that is really a command, or the reverse -- and a writer built on a grammar
with holes in it produces a packet the VU will walk off the end of.

VIFcode, one 32-bit little-endian word:

    bits  0-15  IMMEDIATE
    bits 16-23  NUM
    bits 24-31  CMD

Payload sizes:
  * UNPACK (0x60..0x7F) carries NUM elements of (vn+1) components at vl bits,
    rounded up to a whole number of 32-bit words. Under STCYCL with
    cl < wl the write pattern skips, but the DATA is still NUM elements --
    the cycle governs where they land in VU memory, not how many bytes travel.
  * STMASK (0x20) carries exactly one word.
  * STROW / STCOL (0x30 / 0x31) carry exactly four words.
  * MPG (0x4A) carries NUM*2 words. No level packet on this disc uses it.
  * Everything else carries nothing.

`NUM == 0` means 256 for UNPACK and MPG. That is the hardware's rule and it
bites: a 256-element unpack read as zero-length silently truncates a batch.
"""

import struct

UNPACK_LO = 0x60
UNPACK_HI = 0x7F

VN_COUNT = {0: 1, 1: 2, 2: 3, 3: 4}
VL_BITS = {0: 32, 1: 16, 2: 8, 3: 5}

NAMES = {
    0x00: "NOP", 0x01: "STCYCL", 0x02: "OFFSET", 0x03: "BASE",
    0x04: "ITOP", 0x05: "STMOD", 0x06: "MSKPATH3", 0x07: "MARK",
    0x10: "FLUSHE", 0x11: "FLUSH", 0x13: "FLUSHA",
    0x14: "MSCAL", 0x15: "MSCALF", 0x17: "MSCNT",
    0x20: "STMASK", 0x30: "STROW", 0x31: "STCOL", 0x4A: "MPG",
}


class Token:
    __slots__ = ("offset", "cmd", "num", "imm", "name",
                 "data_start", "data_len", "vn", "vl", "masked")

    def __init__(self, offset, cmd, num, imm, name,
                 data_start, data_len, vn=None, vl=None, masked=False):
        self.offset = offset
        self.cmd = cmd
        self.num = num
        self.imm = imm
        self.name = name
        self.data_start = data_start
        self.data_len = data_len
        self.vn = vn
        self.vl = vl
        self.masked = masked

    @property
    def end(self):
        return self.data_start + self.data_len

    @property
    def is_unpack(self):
        return UNPACK_LO <= self.cmd <= UNPACK_HI

    def __repr__(self):
        if self.is_unpack:
            return ("<%s V%d_%d%s num=%d addr=%d @%d+%d>"
                    % (self.name, VN_COUNT[self.vn], VL_BITS[self.vl],
                       "m" if self.masked else "", self.num, self.imm,
                       self.offset, self.data_len))
        return "<%s imm=%d @%d>" % (self.name, self.imm, self.offset)


class Unknown(Exception):
    """A command this grammar cannot size. Never guess a payload length --
    guessing wrong desynchronises everything after it and the walk still
    'succeeds', just describing a packet that does not exist."""


def unpack_bytes(num, vn, vl):
    """Payload bytes for an UNPACK, rounded up to whole 32-bit words."""
    n = 256 if num == 0 else num
    bits = VN_COUNT[vn] * VL_BITS[vl] * n
    return (bits + 31) // 32 * 4


def walk(data, start, end):
    """Every VIF command in [start, end). Raises Unknown on anything unsized."""
    out = []
    off = start
    while off + 4 <= end:
        word = struct.unpack_from("<I", data, off)[0]
        cmd = (word >> 24) & 0xFF
        num = (word >> 16) & 0xFF
        imm = word & 0xFFFF
        base = cmd & 0x7F              # bit 7 is the interrupt flag
        ds = off + 4

        if UNPACK_LO <= base <= UNPACK_HI:
            vn = (base >> 2) & 3
            vl = base & 3
            masked = bool((base >> 4) & 1)
            n = unpack_bytes(num, vn, vl)
            tok = Token(off, base, num, imm, "UNPACK", ds, n, vn, vl, masked)
        elif base == 0x20:
            tok = Token(off, base, num, imm, "STMASK", ds, 4)
        elif base in (0x30, 0x31):
            tok = Token(off, base, num, imm, NAMES[base], ds, 16)
        elif base == 0x4A:
            n = (256 if num == 0 else num) * 8
            tok = Token(off, base, num, imm, "MPG", ds, n)
        elif base in NAMES:
            tok = Token(off, base, num, imm, NAMES[base], ds, 0)
        else:
            raise Unknown("unknown VIFcode 0x%02X at %d" % (base, off))

        if tok.end > end:
            raise Unknown("%s at %d claims %d payload bytes, past the packet "
                          "end (%d > %d)"
                          % (tok.name, off, tok.data_len, tok.end, end))
        out.append(tok)
        off = tok.end
    return out


def coverage(data, start, end):
    """(tokens, covered_bytes, total_bytes). Tiling is the whole oracle: a
    grammar that leaves bytes unexplained cannot be used to write."""
    toks = walk(data, start, end)
    covered = sum(4 + t.data_len for t in toks)
    return toks, covered, end - start


def emit(tok, payload):
    """One VIFcode word plus its payload, back to bytes."""
    word = (tok.cmd << 24) | ((tok.num & 0xFF) << 16) | (tok.imm & 0xFFFF)
    out = struct.pack("<I", word) + payload
    if len(payload) != tok.data_len:
        raise ValueError("payload is %d bytes, the code says %d"
                         % (len(payload), tok.data_len))
    return out


def rebuild(data, start, end):
    """Re-emit the packet from its own tokens. Must be byte-identical.

    This is the encoder's oracle. If walking and re-emitting does not
    reproduce the original exactly, the grammar is wrong somewhere and
    anything built on it is guesswork.
    """
    toks = walk(data, start, end)
    out = bytearray()
    for t in toks:
        out += emit(t, data[t.data_start:t.end])
    return bytes(out)


# ---------------------------------------------------------------- trailers
#
# 2026-11-05: the 41 "failures" the walker reported on character parts were
# never VIF commands at all. A part's byte range runs to the NEXT part's
# offset, or to the end of the chunk for the last one, and on a rigged model
# the last part is followed by a TRAILER that is not a VIF stream:
#
#     u32 x4   [t+4, t+16, a, b]          offsets, relative to the chunk
#     bytes    index lists, 0,1,2,... with gaps   (three lists, 56-60 long)
#
# The walker read that as commands, hit bytes like 0x0B / 0x0C ("VIFcodes"
# that are really the byte values 11 and 12 in an ascending index list), and
# gave up. A packet ENDS at its FLUSH: STCYCL 257 ; FLUSH ; NOP padding to 16
# bytes. Walking to there tiles 1,919 / 1,919 character parts across every
# `*_lods.rsc` on the disc; 346 of them (the last part of a model) have a
# trailer after it, and all 346 open with that four-pointer table. Mid parts
# end exactly at their padding (99 / 99), which is what makes "stop at the
# FLUSH" a rule and not a guess: if a FLUSH ever occurred mid-packet, some mid
# part would have bytes left over, and none does.

FLUSH_CMD = 0x11


def walk_packet(data, start, end):
    """Tokens of ONE packet and where it ends: (tokens, packet_end).

    Stops after the first FLUSH and any zero padding words after it. Whatever
    remains in [packet_end, end) is not VIF data -- see `trailer`. Raises
    Unknown on an unsizable command before the FLUSH, and ValueError if no
    FLUSH is found (a packet that never ends is not one this can describe).
    """
    out = []
    off = start
    while off + 4 <= end:
        word = struct.unpack_from("<I", data, off)[0]
        cmd = (word >> 24) & 0xFF
        num = (word >> 16) & 0xFF
        imm = word & 0xFFFF
        base = cmd & 0x7F
        ds = off + 4
        if UNPACK_LO <= base <= UNPACK_HI:
            vn, vl = (base >> 2) & 3, base & 3
            tok = Token(off, base, num, imm, "UNPACK", ds,
                        unpack_bytes(num, vn, vl), vn, vl,
                        bool((base >> 4) & 1))
        elif base == 0x20:
            tok = Token(off, base, num, imm, "STMASK", ds, 4)
        elif base in (0x30, 0x31):
            tok = Token(off, base, num, imm, NAMES[base], ds, 16)
        elif base == 0x4A:
            tok = Token(off, base, num, imm, "MPG", ds,
                        (256 if num == 0 else num) * 8)
        elif base in NAMES:
            tok = Token(off, base, num, imm, NAMES[base], ds, 0)
        else:
            raise Unknown("unknown VIFcode 0x%02X at %d" % (base, off))
        if tok.end > end:
            raise Unknown("%s at %d claims %d payload bytes, past the packet "
                          "end (%d > %d)"
                          % (tok.name, off, tok.data_len, tok.end, end))
        out.append(tok)
        off = tok.end
        if base == FLUSH_CMD:
            while off + 4 <= end and struct.unpack_from("<I", data, off)[0] == 0:
                off += 4
            return out, off
    raise ValueError("no FLUSH before %d -- not a complete packet" % end)


def trailer(data, packet_end, end):
    """The non-VIF trailer after a rigged model's last packet, or None.

    -> dict(start, end, pointers=[...], lists=[(start, end), ...]). Pointers
    are offsets into the same chunk; the first addresses the second word of
    the table itself. Raises ValueError if bytes remain that do not open with
    that table, since the layout is only known from the disc and an
    unrecognised remainder must not be silently carried.
    """
    if packet_end >= end:
        return None
    if end - packet_end < 16:
        raise ValueError("%d stray bytes after the packet" % (end - packet_end))
    p = struct.unpack_from("<4I", data, packet_end)
    t = packet_end
    if not (p[0] == t + 4 and p[1] == t + 16 and t + 16 < p[2] < p[3] < end):
        raise ValueError("trailer at %d does not open with the 4-pointer table"
                         % t)
    return dict(start=t, end=end, pointers=list(p),
                lists=[(p[1], p[2]), (p[2], p[3]), (p[3], end)])
