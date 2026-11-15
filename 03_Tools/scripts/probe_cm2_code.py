"""Find the cm2 decoder inside SLUS_205.00 and read what it does.

Statistics over the files are exhausted, and the save-state route (VU1
quadword 210 holds live posed bone matrices) needs a fresh uncompressed state
that only the developer can make. What is available right now is the decoder itself:
1.8 MB of MIPS R5900 in `SLUS_205.00`.

This does three things, in order of cost:

  1. Locate every occurrence of the literal `cm2` and `cmp` magic in the ELF.
     The comparison against the magic sits in, or right next to, the loader.
  2. Around each hit, look for the MIPS immediate patterns a bit-reader
     produces: `andi` with a mask of 2^n-1, `sll`/`srl`/`sra` with small shift
     amounts, and `lui 0x...` loading a float scale. A quantised-curve decoder
     is dense in exactly those.
  3. Look for a candidate TABLE: a run of small monotone-ish bytes or halfwords
     in .data, which is what a per-symbol bit-width or Huffman length table
     looks like.

MIPS is little-endian here and every instruction is 4 bytes, so decoding just
the opcode field is cheap and needs no disassembler dependency.
"""
import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdrstudio.iso9660 import Iso9660                 # noqa: E402
from rdrstudio import config                          # noqa: E402

# REQ-035/036. Was a literal path to the WORKING ISO on one machine:
# unrunnable on anyone else's checkout, and pointed at the disc the live
# tests mutate. What this examines is a property of the SHIPPED data, so
# it reads the stock disc. Read-only throughout, which is what makes
# vanilla the right answer here rather than merely the tidier one.
DISC = Path(config.VANILLA_ISO)
ELF = "SLUS_205.00"

# MIPS I/II opcode field (bits 31..26) for the instructions a bit-reader uses.
OP = {0x0C: "andi", 0x0D: "ori", 0x0F: "lui", 0x24: "lbu", 0x25: "lhu",
      0x23: "lw", 0x21: "lh", 0x20: "lb", 0x09: "addiu", 0x0A: "slti",
      0x0B: "sltiu", 0x0E: "xori", 0x2B: "sw", 0x29: "sh", 0x28: "sb"}
SPECIAL = {0x00: "sll", 0x02: "srl", 0x03: "sra", 0x04: "sllv",
           0x06: "srlv", 0x07: "srav", 0x24: "and", 0x25: "or",
           0x18: "mult", 0x19: "multu"}


def decode(word):
    """Just enough MIPS to recognise a bit-twiddling window."""
    op = (word >> 26) & 0x3F
    rs = (word >> 21) & 0x1F
    rt = (word >> 16) & 0x1F
    rd = (word >> 11) & 0x1F
    sa = (word >> 6) & 0x1F
    fn = word & 0x3F
    imm = word & 0xFFFF
    simm = imm - 0x10000 if imm & 0x8000 else imm
    if op == 0:
        name = SPECIAL.get(fn)
        if name in ("sll", "srl", "sra"):
            return "%s r%d, r%d, %d" % (name, rd, rt, sa), name, sa
        if name:
            return "%s r%d, r%d, r%d" % (name, rd, rs, rt), name, None
        return None, None, None
    name = OP.get(op)
    if not name:
        return None, None, None
    return "%s r%d, r%d, 0x%X (%d)" % (name, rt, rs, imm, simm), name, imm


def main():
    iso = Iso9660(DISC)
    entry = iso.get(ELF)
    data = iso.read_at(entry.offset, entry.size)
    print("%s: %d bytes" % (ELF, len(data)))

    # ELF program headers give the virtual address, which is what makes an
    # offset in this file comparable to an address in a disassembler.
    if data[:4] == b"\x7fELF":
        e_entry, e_phoff = struct.unpack_from("<II", data, 24)
        e_phentsize, e_phnum = struct.unpack_from("<HH", data, 42)
        print("  ELF entry 0x%08X, %d program headers" % (e_entry, e_phnum))
        segs = []
        for i in range(e_phnum):
            off = e_phoff + i * e_phentsize
            p_type, p_offset, p_vaddr, _pa, p_filesz, p_memsz = \
                struct.unpack_from("<IIIIII", data, off)
            if p_type == 1:
                segs.append((p_offset, p_vaddr, p_filesz))
                print("    LOAD file 0x%06X -> vaddr 0x%08X  size 0x%X"
                      % (p_offset, p_vaddr, p_filesz))
    else:
        segs = [(0, 0, len(data))]
        print("  not an ELF header; treating as flat")

    def vaddr(fileoff):
        for o, v, sz in segs:
            if o <= fileoff < o + sz:
                return v + (fileoff - o)
        return None

    # ---------------------------------------------------------------- magics
    print("\n" + "=" * 70)
    print("occurrences of the animation magics")
    print("=" * 70)
    for magic in (b"cm2\x00", b"cmp\x00", b"cm2", b"CM2"):
        hits = []
        pos = -1
        while True:
            pos = data.find(magic, pos + 1)
            if pos < 0:
                break
            hits.append(pos)
            if len(hits) > 40:
                break
        print("  %-8r %d hit(s): %s"
              % (magic, len(hits),
                 " ".join("0x%06X" % h for h in hits[:12])))
        for h in hits[:6]:
            va = vaddr(h)
            ctx = data[max(0, h - 16):h + 16]
            print("      @0x%06X (vaddr %s)  %s"
                  % (h, "0x%08X" % va if va else "?", ctx.hex(" ")))
            # A magic used in a comparison is often materialised as
            # lui/ori of the two halves rather than stored as a string.

    # "cm2\0" little-endian is the u32 0x00326D63. With no copy of the string
    # in the file, the check has to be against that constant -- either sitting
    # in a literal pool, or built as lui 0x0032 / ori 0x6D63.
    print("\n" + "=" * 70)
    print("the magic as a 32-bit constant 0x00326D63")
    print("=" * 70)
    for name, needle in (("cm2 as u32 LE", b"\x63\x6d\x32\x00"),
                         ("cmp as u32 LE", b"\x63\x6d\x70\x00"),
                         ("cm2 as u32 BE", b"\x00\x32\x6d\x63")):
        pos, hits = -1, []
        while len(hits) < 20:
            pos = data.find(needle, pos + 1)
            if pos < 0:
                break
            hits.append(pos)
        print("  %-14s %d hit(s): %s"
              % (name, len(hits), " ".join("0x%06X" % h for h in hits[:10])))

    print("\n  halves of the magic as instruction immediates:")
    # 0x6D63 is the low half; 0x0032 is the high half but is also an extremely
    # common small constant, so it is only interesting NEXT TO the low half.
    lows = []
    for off in range(0, len(data) - 4, 4):
        w = struct.unpack_from("<I", data, off)[0]
        if (w & 0xFFFF) != 0x6D63:
            continue
        txt, nm, _ = decode(w)
        lows.append((off, txt or "op 0x%02X" % ((w >> 26) & 0x3F)))
    print("    immediate 0x6D63 ('mc') appears %d time(s)" % len(lows))
    for off, txt in lows[:12]:
        va = vaddr(off)
        print("      0x%06X (vaddr %s)  %s   window: %s"
              % (off, "0x%08X" % va if va else "?", txt,
                 data[off - 8:off + 12].hex(" ")))

    # -------------------------------------------------- bit-reader signatures
    print("\n" + "=" * 70)
    print("densest windows of shift/mask instructions (bit-reader candidates)")
    print("=" * 70)
    # Score each 64-instruction window by how many shift/and/andi it holds.
    WIN = 64
    scores = []
    kinds = []
    for off in range(0, len(data) - 4, 4):
        w = struct.unpack_from("<I", data, off)[0]
        # A zero word decodes as `sll r0, r0, 0` -- the canonical MIPS NOP. A
        # block of zeros therefore scored a perfect 64/64 and won every time,
        # which is how the first run "found" a bit-reader in the BSS padding.
        if w == 0:
            kinds.append(None)
            continue
        _txt, name, _ = decode(w)
        kinds.append(name)
    for i in range(0, len(kinds) - WIN, WIN // 2):
        win = kinds[i:i + WIN]
        n = sum(1 for k in win
                if k in ("srl", "sll", "sra", "srlv", "sllv", "srav",
                         "and", "andi"))
        # Require the window to be mostly real instructions, so a sparse
        # scattering inside a data table cannot outscore actual code.
        decoded = sum(1 for k in win if k)
        if decoded < WIN * 0.7:
            continue
        scores.append((n, i * 4))
    scores.sort(reverse=True)
    for n, off in scores[:6]:
        va = vaddr(off)
        print("\n  %d/%d shift+mask ops at 0x%06X (vaddr %s)"
              % (n, WIN, off, "0x%08X" % va if va else "?"))
        for j in range(0, WIN):
            w = struct.unpack_from("<I", data, off + j * 4)[0]
            txt, name, _ = decode(w)
            if name in ("srl", "sll", "sra", "srlv", "sllv", "srav",
                        "and", "andi", "lbu", "lhu", "lw"):
                print("      +0x%03X  %s" % (j * 4, txt))
            if j > 40:
                break

    # ------------------------------------------------------- mask immediates
    print("\n" + "=" * 70)
    print("andi masks that are 2^n - 1 (a field width being extracted)")
    print("=" * 70)
    masks = Counter()
    for off in range(0, len(data) - 4, 4):
        w = struct.unpack_from("<I", data, off)[0]
        if ((w >> 26) & 0x3F) != 0x0C:      # andi
            continue
        imm = w & 0xFFFF
        if imm and (imm & (imm + 1)) == 0:  # 1,3,7,15,31,...
            masks[imm] += 1
    for m, n in sorted(masks.items()):
        print("  0x%04X (%5d = %2d bits)  %d site(s)"
              % (m, m, m.bit_length(), n))

    iso.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
