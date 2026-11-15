"""Unpack SLUS_205.00.

The shipped executable is compressed: 444 of its 447 4 KB blocks sit above 7.8
bits/byte and not one of the seven format magics the game provably reads
(`DAVE`, `MOD0`, `TEX0`, `cm2`, ...) appears anywhere in it. Only the first
4 KB is real MIPS, and that 4 KB is a self-extractor. This undoes it, which is
what makes the game's own decoders readable -- above all the animation decoder,
the one format the toolkit still cannot read.

The stub, disassembled by `scripts/dis_elf_stub.py`, does this:

    lui   a0, 0x0080 ; addiu a0, a0, 1536   -> source  = 0x00800600
    lui   a1, 0x0010                        -> dest    = 0x00100000
    jal   unpack
    j     0x00100008                        -> unpacked entry point

and `unpack` is **aPLib** with one twist: the packed bytes are obfuscated. Every
byte the depacker consumes -- both bitstream refills and whole-byte reads --
is XORed with a one-byte key that starts at 1 and increments per byte consumed:

    lbu  v0, 0(v1)      ; b = *src
    addiu v1, v1, 1     ; src++
    xor  v0, v0, a1     ; b ^= key
    addiu a0, a1, 1     ; key++

The very first output byte is copied straight from the stream BEFORE the key is
initialised, so it is not XORed. That asymmetry is in the stub too:
`lbu v1, 0(a0)` happens before `sb v0, 6(sp)` sets the key to 1.

The four aPLib cases, each identified at a concrete address in the stub:

    0        literal byte                            (0x00800590)
    10       gamma offset + gamma length match       (0x00800274)
    110      7-bit offset, length 2 or 3             (0x00800208)
    111      4-bit offset, length 1, 0 => write NUL  (0x0080016C)

There is no separate length field in the last two cases, and offset 0 in the
110 case terminates -- `beq a3, zero, 0x0080026C` leads to the exit path.
"""

SRC_VADDR = 0x00800600      # where the packed data starts, as an address
DST_VADDR = 0x00100000      # where the unpacked image belongs
SEG_VADDR = 0x00800000      # vaddr of the ELF's single LOAD segment


class _Bits:
    """The stub's bit reader, XOR layer included.

    `bits_left` starts at 0 and is decremented before every use; when the
    decrement underflows past zero the next byte is pulled in and the counter
    is reset to 7. That is eight bits per byte with the first read fetching.
    """

    __slots__ = ("buf", "pos", "bitbuf", "bits_left", "key")

    def __init__(self, buf, pos):
        self.buf = buf
        self.pos = pos
        self.bitbuf = 0
        self.bits_left = 0
        self.key = 1

    def byte(self):
        b = self.buf[self.pos] ^ (self.key & 0xFF)
        self.pos += 1
        self.key = (self.key + 1) & 0xFF
        return b

    def bit(self):
        self.bits_left = (self.bits_left - 1) & 0xFF
        if self.bits_left == 0xFF:
            self.bitbuf = self.byte()
            self.bits_left = 7
        b = (self.bitbuf >> 7) & 1
        self.bitbuf = (self.bitbuf << 1) & 0xFF
        return b

    def gamma(self):
        """aPLib's interlaced Elias gamma: value starts at 1, then (bit, more)."""
        v = 1
        while True:
            v = (v << 1) + self.bit()
            if not self.bit():
                return v


def depack(packed, limit=None):
    """aPLib-with-XOR, as the stub implements it. Returns the unpacked bytes."""
    if not packed:
        return b""
    out = bytearray()
    out.append(packed[0])                 # first byte is literal and NOT XORed
    bs = _Bits(packed, 1)

    last_offset = 0
    lwm = 0                               # "last was match"
    while True:
        if limit is not None and len(out) >= limit:
            break
        if bs.pos > len(packed):
            break
        if not bs.bit():
            out.append(bs.byte())
            lwm = 0
            continue
        if not bs.bit():
            # 10 -- gamma offset (in 256-byte blocks) then gamma length.
            #
            # The two subtractions live in the delay slots of a `bnel` and a
            # `bne` at 0x0080033C / 0x00800348. `bnel` is branch-LIKELY: its
            # delay slot runs only when the branch is taken. Reading it as an
            # ordinary branch inverts both cases, which is what made the first
            # version of this desync after 1,883 bytes.
            offset = bs.gamma()
            if lwm == 0 and offset == 2:
                # Reuse the previous offset; the length is a bare gamma with no
                # fixups (0x00800414-0x0080043C, copying from `t0 - t4`).
                offset = last_offset
                length = bs.gamma()
            else:
                offset -= 3 if lwm == 0 else 2
                offset = (offset << 8) + bs.byte()
                length = bs.gamma()
                if offset >= 32000:
                    length += 1
                if offset >= 1280:
                    length += 1
                if offset < 128:
                    length += 2
                last_offset = offset
            for _ in range(length):
                out.append(out[-offset] if 0 < offset <= len(out) else 0)
            lwm = 1
            continue
        if not bs.bit():
            # 110 -- one byte: 7-bit offset and a 1-bit length selector
            b = bs.byte()
            length = 2 + (b & 1)
            offset = b >> 1
            if offset == 0:
                break                     # the terminator
            for _ in range(length):
                out.append(out[-offset] if offset <= len(out) else 0)
            last_offset = offset
            lwm = 1
            continue
        # 111 -- four raw bits of offset, one byte copied; zero writes a NUL
        offset = 0
        for _ in range(4):
            offset = (offset << 1) + bs.bit()
        if offset == 0:
            out.append(0)
        else:
            out.append(out[-offset] if offset <= len(out) else 0)
        lwm = 0

    return bytes(out)


def unpack_elf(elf_bytes):
    """Take SLUS_205.00 as shipped, return (image, base_vaddr).

    `image` is the unpacked code and data as the console would see it at
    `base_vaddr`, so an offset in it maps to an address by simple addition.
    """
    import struct
    if elf_bytes[:4] != b"\x7fELF":
        raise ValueError("not an ELF")
    e_phoff = struct.unpack_from("<I", elf_bytes, 28)[0]
    p_offset, p_vaddr = struct.unpack_from("<II", elf_bytes, e_phoff + 4)
    # The packed blob starts at SRC_VADDR, which lives inside that segment.
    start = p_offset + (SRC_VADDR - p_vaddr)
    return depack(elf_bytes[start:]), DST_VADDR
