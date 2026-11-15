"""Disassemble the unpacker stub at the head of SLUS_205.00.

`probe_elf_packed.py` established that the shipped executable is compressed:
444 of 447 4 KB blocks sit above 7.8 bits/byte, and not one of the seven format
magics the game demonstrably reads (`DAVE`, `MOD0`, `TEX0`, `cm2`, ...) appears
anywhere in it. Only the first 4 KB decodes as MIPS. That 4 KB is the unpacker.

If the scheme is a simple LZ -- and at 4 KB it cannot be much else -- then
unpacking the ELF offline becomes possible, and the cm2 decoder becomes
readable. So it is worth actually reading these instructions rather than
guessing.

Enough of MIPS R5900 to follow a decompressor: loads, stores, shifts, masks,
arithmetic, branches and jumps. Anything unrecognised prints as a raw word so
nothing is silently skipped.
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdrstudio.iso9660 import Iso9660                 # noqa: E402
from rdrstudio.config import VANILLA_ISO             # noqa: E402

# The STOCK disc, not the working one: a read-only structural probe must
# not depend on what the last test left behind (REQ-035). The repoint was
# measured output-neutral at the time it was made -- see REQ-074.
DISC = Path(VANILLA_ISO)
ELF = "SLUS_205.00"

REG = ["zero", "at", "v0", "v1", "a0", "a1", "a2", "a3",
       "t0", "t1", "t2", "t3", "t4", "t5", "t6", "t7",
       "s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7",
       "t8", "t9", "k0", "k1", "gp", "sp", "fp", "ra"]

# opcode -> (mnemonic, form)
#   I  = rt, rs, imm      L = rt, imm(rs)       B = rs, rt, target
#   BZ = rs, target       J = target            U = rt, imm
IOPS = {
    0x04: ("beq", "B"),   0x05: ("bne", "B"),
    0x06: ("blez", "BZ"), 0x07: ("bgtz", "BZ"),
    0x08: ("addi", "I"),  0x09: ("addiu", "I"),
    0x0A: ("slti", "I"),  0x0B: ("sltiu", "I"),
    0x0C: ("andi", "I"),  0x0D: ("ori", "I"),
    0x0E: ("xori", "I"),  0x0F: ("lui", "U"),
    0x18: ("daddi", "I"), 0x19: ("daddiu", "I"),
    0x20: ("lb", "L"),    0x21: ("lh", "L"),
    0x22: ("lwl", "L"),   0x23: ("lw", "L"),
    0x24: ("lbu", "L"),   0x25: ("lhu", "L"),
    0x26: ("lwr", "L"),   0x27: ("lwu", "L"),
    0x28: ("sb", "L"),    0x29: ("sh", "L"),
    0x2A: ("swl", "L"),   0x2B: ("sw", "L"),
    0x2E: ("swr", "L"),   0x37: ("ld", "L"),
    0x3F: ("sd", "L"),    0x02: ("j", "J"), 0x03: ("jal", "J"),
}
SPEC = {
    0x00: ("sll", "SH"),  0x02: ("srl", "SH"),  0x03: ("sra", "SH"),
    0x04: ("sllv", "R"),  0x06: ("srlv", "R"),  0x07: ("srav", "R"),
    0x08: ("jr", "JR"),   0x09: ("jalr", "JR"),
    0x0C: ("syscall", "N"),
    0x10: ("mfhi", "D"),  0x12: ("mflo", "D"),
    0x18: ("mult", "M"),  0x19: ("multu", "M"),
    0x1A: ("div", "M"),   0x1B: ("divu", "M"),
    0x20: ("add", "R"),   0x21: ("addu", "R"),
    0x22: ("sub", "R"),   0x23: ("subu", "R"),
    0x24: ("and", "R"),   0x25: ("or", "R"),
    0x26: ("xor", "R"),   0x27: ("nor", "R"),
    0x2A: ("slt", "R"),   0x2B: ("sltu", "R"),
    0x38: ("dsll", "SH"), 0x3A: ("dsrl", "SH"), 0x3B: ("dsra", "SH"),
    0x3C: ("dsll32", "SH"), 0x3E: ("dsrl32", "SH"), 0x3F: ("dsra32", "SH"),
}


def dis(word, pc):
    op = (word >> 26) & 0x3F
    rs, rt, rd = (word >> 21) & 31, (word >> 16) & 31, (word >> 11) & 31
    sa, fn = (word >> 6) & 31, word & 0x3F
    imm = word & 0xFFFF
    simm = imm - 0x10000 if imm & 0x8000 else imm
    if word == 0:
        return "nop"
    if op == 0:
        if fn not in SPEC:
            return ".word 0x%08X" % word
        m, form = SPEC[fn]
        if form == "SH":
            return "%-8s %s, %s, %d" % (m, REG[rd], REG[rt], sa)
        if form == "R":
            return "%-8s %s, %s, %s" % (m, REG[rd], REG[rs], REG[rt])
        if form == "JR":
            return "%-8s %s" % (m, REG[rs])
        if form == "M":
            return "%-8s %s, %s" % (m, REG[rs], REG[rt])
        if form == "D":
            return "%-8s %s" % (m, REG[rd])
        return m
    if op == 0x01:          # REGIMM
        which = {0x00: "bltz", 0x01: "bgez", 0x10: "bltzal",
                 0x11: "bgezal"}.get(rt)
        if which:
            return "%-8s %s, 0x%08X" % (which, REG[rs], pc + 4 + simm * 4)
        return ".word 0x%08X" % word
    if op not in IOPS:
        return ".word 0x%08X" % word
    m, form = IOPS[op]
    if form == "I":
        return "%-8s %s, %s, %d" % (m, REG[rt], REG[rs], simm)
    if form == "U":
        return "%-8s %s, 0x%04X" % (m, REG[rt], imm)
    if form == "L":
        return "%-8s %s, %d(%s)" % (m, REG[rt], simm, REG[rs])
    if form == "B":
        return "%-8s %s, %s, 0x%08X" % (m, REG[rs], REG[rt], pc + 4 + simm * 4)
    if form == "BZ":
        return "%-8s %s, 0x%08X" % (m, REG[rs], pc + 4 + simm * 4)
    if form == "J":
        return "%-8s 0x%08X" % (m, ((pc + 4) & 0xF0000000) | ((word & 0x3FFFFFF) << 2))
    return m


def main():
    iso = Iso9660(DISC)
    e = iso.get(ELF)
    data = iso.read_at(e.offset, e.size)
    e_entry, e_phoff = struct.unpack_from("<II", data, 24)
    p_offset, p_vaddr = struct.unpack_from("<II", data, e_phoff + 4)
    print("entry 0x%08X, segment file 0x%X -> vaddr 0x%08X"
          % (e_entry, p_offset, p_vaddr))
    start = p_offset + (e_entry - p_vaddr)
    print("entry is at file offset 0x%X\n" % start)

    # Print until the first jr/j that is not followed by more code, or 0xE00
    # bytes -- whichever comes first. 4 KB is the whole stub.
    LIMIT = 0x900
    print("=" * 72)
    print("unpacker stub")
    print("=" * 72)
    for i in range(0, LIMIT, 4):
        off = start + i
        if off + 4 > len(data):
            break
        w = struct.unpack_from("<I", data, off)[0]
        pc = e_entry + i
        print("  0x%08X  %08X   %s" % (pc, w, dis(w, pc)))

    iso.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
