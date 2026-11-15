"""Disassemble the unpacked game executable.

    python scripts/probe_dis.py 0x002D2EB0            # a function, until `jr ra`
    python scripts/probe_dis.py 0x002D2EB0 --count 200
    python scripts/probe_dis.py 0x002D2EB0 --data 64  # as words/floats instead
    python scripts/probe_dis.py --string 0x005241E4   # read a C string
    python scripts/probe_dis.py --xref 0x002D2EB0     # who calls this?

The image comes from `rdr.py unpack-elf`; if it has not been written yet this
unpacks on the fly. Addresses are console addresses -- the image is based at
0x00100000, so offset N in the file is address 0x00100000 + N.

MIPS R5900 (the PS2's EE) is MIPS III plus multimedia instructions. This covers
the integer, branch, load/store, FPU-load and COP1 forms a compiler emits;
anything else prints as `.word`, so nothing is silently misread.

Named `probe_dis.py` and not `dis.py` (REQ-031). Python puts a script's own
directory first on `sys.path`, so a file called `dis.py` in `scripts/` shadowed
the STDLIB `dis` for everything run out of there -- and `inspect`, `traceback`,
`dataclasses` and `argparse` all import it. It cost this project twice before
it was renamed: several scripts still carry hand-written `sys.path` surgery to
work around it, and numpy failed to import inside the studio, silently turning
off every fast path with no symptom except encoding taking 6.7s instead of
0.20s (see `ipuenc.NUMPY_UNAVAILABLE`).
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = 0x00100000
CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "..", "07_Research", "SLUS_205.00.unpacked.bin")

REG = ["zero", "at", "v0", "v1", "a0", "a1", "a2", "a3",
       "t0", "t1", "t2", "t3", "t4", "t5", "t6", "t7",
       "s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7",
       "t8", "t9", "k0", "k1", "gp", "sp", "fp", "ra"]

IOPS = {
    0x02: ("j", "J"),      0x03: ("jal", "J"),
    0x04: ("beq", "B"),    0x05: ("bne", "B"),
    0x06: ("blez", "BZ"),  0x07: ("bgtz", "BZ"),
    0x08: ("addi", "I"),   0x09: ("addiu", "I"),
    0x0A: ("slti", "I"),   0x0B: ("sltiu", "I"),
    0x0C: ("andi", "IU"),  0x0D: ("ori", "IU"),
    0x0E: ("xori", "IU"),  0x0F: ("lui", "U"),
    0x14: ("beql", "B"),   0x15: ("bnel", "B"),
    0x16: ("blezl", "BZ"), 0x17: ("bgtzl", "BZ"),
    0x18: ("daddi", "I"),  0x19: ("daddiu", "I"),
    0x1A: ("ldl", "L"),    0x1B: ("ldr", "L"),
    0x20: ("lb", "L"),     0x21: ("lh", "L"),
    0x22: ("lwl", "L"),    0x23: ("lw", "L"),
    0x24: ("lbu", "L"),    0x25: ("lhu", "L"),
    0x26: ("lwr", "L"),    0x27: ("lwu", "L"),
    0x28: ("sb", "L"),     0x29: ("sh", "L"),
    0x2A: ("swl", "L"),    0x2B: ("sw", "L"),
    0x2C: ("sdl", "L"),    0x2D: ("sdr", "L"),
    0x2E: ("swr", "L"),
    0x31: ("lwc1", "F"),   0x35: ("ldc1", "F"),
    0x37: ("ld", "L"),     0x39: ("swc1", "F"),
    0x3D: ("sdc1", "F"),   0x3F: ("sd", "L"),
    0x1E: ("lq", "L"),     0x1F: ("sq", "L"),
}
SPEC = {
    0x00: ("sll", "SH"),   0x02: ("srl", "SH"),   0x03: ("sra", "SH"),
    0x04: ("sllv", "RV"),  0x06: ("srlv", "RV"),  0x07: ("srav", "RV"),
    0x08: ("jr", "JR"),    0x09: ("jalr", "JR"),
    0x0A: ("movz", "R"),   0x0B: ("movn", "R"),
    0x0C: ("syscall", "N"), 0x0D: ("break", "N"),
    0x0F: ("sync", "N"),
    0x10: ("mfhi", "D"),   0x11: ("mthi", "S"),
    0x12: ("mflo", "D"),   0x13: ("mtlo", "S"),
    0x14: ("dsllv", "RV"), 0x16: ("dsrlv", "RV"), 0x17: ("dsrav", "RV"),
    0x18: ("mult", "M"),   0x19: ("multu", "M"),
    0x1A: ("div", "M"),    0x1B: ("divu", "M"),
    0x20: ("add", "R"),    0x21: ("addu", "R"),
    0x22: ("sub", "R"),    0x23: ("subu", "R"),
    0x24: ("and", "R"),    0x25: ("or", "R"),
    0x26: ("xor", "R"),    0x27: ("nor", "R"),
    0x2A: ("slt", "R"),    0x2B: ("sltu", "R"),
    0x2C: ("dadd", "R"),   0x2D: ("daddu", "R"),
    0x2E: ("dsub", "R"),   0x2F: ("dsubu", "R"),
    0x38: ("dsll", "SH"),  0x3A: ("dsrl", "SH"),  0x3B: ("dsra", "SH"),
    0x3C: ("dsll32", "SH"), 0x3E: ("dsrl32", "SH"), 0x3F: ("dsra32", "SH"),
}
REGIMM = {0x00: "bltz", 0x01: "bgez", 0x02: "bltzl", 0x03: "bgezl",
          0x10: "bltzal", 0x11: "bgezal"}
# COP1 (FPU) single-precision, which a curve decoder uses heavily.
COP1S = {0x00: "add.s", 0x01: "sub.s", 0x02: "mul.s", 0x03: "div.s",
         0x04: "sqrt.s", 0x05: "abs.s", 0x06: "mov.s", 0x07: "neg.s",
         0x18: "adda.s", 0x1C: "madd.s", 0x24: "cvt.w.s",
         0x28: "max.s", 0x29: "min.s",
         0x30: "c.f.s", 0x32: "c.eq.s", 0x34: "c.lt.s", 0x36: "c.le.s"}


def dis(w, pc):
    if w == 0:
        return "nop"
    op = (w >> 26) & 0x3F
    rs, rt, rd = (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31
    sa, fn = (w >> 6) & 31, w & 0x3F
    imm = w & 0xFFFF
    simm = imm - 0x10000 if imm & 0x8000 else imm

    if op == 0:
        if fn not in SPEC:
            return ".word 0x%08X" % w
        m, form = SPEC[fn]
        if form == "SH":
            return "%-8s %s, %s, %d" % (m, REG[rd], REG[rt], sa)
        if form == "RV":
            return "%-8s %s, %s, %s" % (m, REG[rd], REG[rt], REG[rs])
        if form == "R":
            return "%-8s %s, %s, %s" % (m, REG[rd], REG[rs], REG[rt])
        if form == "JR":
            return "%-8s %s" % (m, REG[rs])
        if form == "M":
            return "%-8s %s, %s" % (m, REG[rs], REG[rt])
        if form == "D":
            return "%-8s %s" % (m, REG[rd])
        if form == "S":
            return "%-8s %s" % (m, REG[rs])
        return m
    if op == 0x01:
        which = REGIMM.get(rt)
        return ("%-8s %s, 0x%08X" % (which, REG[rs], pc + 4 + simm * 4)
                if which else ".word 0x%08X" % w)
    if op == 0x11:                      # COP1
        if rs == 0x00:
            return "mfc1     %s, f%d" % (REG[rt], rd)
        if rs == 0x04:
            return "mtc1     %s, f%d" % (REG[rt], rd)
        if rs == 0x08:
            t = "bc1t" if (rt & 1) else "bc1f"
            return "%-8s 0x%08X" % (t, pc + 4 + simm * 4)
        if rs == 0x10:
            m = COP1S.get(fn)
            if m and m.startswith("c."):
                return "%-8s f%d, f%d" % (m, (w >> 11) & 31, (w >> 16) & 31)
            if m:
                return "%-8s f%d, f%d, f%d" % (m, (w >> 6) & 31,
                                               (w >> 11) & 31, (w >> 16) & 31)
        if rs == 0x14:
            return "cvt.s.w  f%d, f%d" % ((w >> 6) & 31, (w >> 11) & 31)
        return ".word 0x%08X  (cop1)" % w
    if op not in IOPS:
        return ".word 0x%08X" % w
    m, form = IOPS[op]
    if form == "I":
        return "%-8s %s, %s, %d" % (m, REG[rt], REG[rs], simm)
    if form == "IU":
        return "%-8s %s, %s, 0x%X" % (m, REG[rt], REG[rs], imm)
    if form == "U":
        return "%-8s %s, 0x%04X" % (m, REG[rt], imm)
    if form == "L":
        return "%-8s %s, %d(%s)" % (m, REG[rt], simm, REG[rs])
    if form == "F":
        return "%-8s f%d, %d(%s)" % (m, rt, simm, REG[rs])
    if form == "B":
        return "%-8s %s, %s, 0x%08X" % (m, REG[rs], REG[rt], pc + 4 + simm * 4)
    if form == "BZ":
        return "%-8s %s, 0x%08X" % (m, REG[rs], pc + 4 + simm * 4)
    if form == "J":
        return "%-8s 0x%08X" % (m, ((pc + 4) & 0xF0000000) | ((w & 0x3FFFFFF) << 2))
    return m


def load_image():
    if os.path.exists(CACHE):
        with open(CACHE, "rb") as fh:
            return fh.read()
    from rdrstudio.iso9660 import Iso9660
    from rdrstudio import elfpack
    from rdrstudio import config as cfg
    iso = Iso9660(getattr(cfg, "MODDED_ISO", None) or cfg.VANILLA_ISO)
    try:
        e = iso.get("SLUS_205.00")
        img, _ = elfpack.unpack_elf(iso.read_at(e.offset, e.size))
    finally:
        iso.close()
    return img


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("addr", nargs="?", help="address, e.g. 0x002D2EB0")
    ap.add_argument("--count", type=int, default=0,
                    help="instructions to print (0 = until jr ra)")
    ap.add_argument("--data", type=int, default=0,
                    help="print N words as hex/int/float instead of code")
    ap.add_argument("--string", help="read a NUL-terminated string at an address")
    ap.add_argument("--xref", help="find calls/references to an address")
    args = ap.parse_args()

    img = load_image()
    top = BASE + len(img)

    def at(addr):
        return addr - BASE

    if args.string:
        a = int(args.string, 16)
        o = at(a)
        end = img.find(b"\x00", o)
        print("0x%08X: %r" % (a, img[o:end if end >= 0 else o + 80]))
        return 0

    if args.xref:
        target = int(args.xref, 16)
        print("references to 0x%08X:" % target)
        # jal / j encode the target directly
        want = (target >> 2) & 0x3FFFFFF
        n = 0
        for off in range(0, len(img) - 4, 4):
            w = struct.unpack_from("<I", img, off)[0]
            op = (w >> 26) & 0x3F
            if op in (0x02, 0x03) and (w & 0x3FFFFFF) == want:
                print("  0x%08X  %s" % (BASE + off, dis(w, BASE + off)))
                n += 1
            elif w == target:
                print("  0x%08X  DATA word (vtable slot or pointer)" % (BASE + off))
                n += 1
            if n >= 60:
                break
        if not n:
            print("  none")
        return 0

    if not args.addr:
        ap.print_help()
        return 1
    a = int(args.addr, 16)
    if not (BASE <= a < top):
        print("0x%08X is outside the image (0x%08X..0x%08X)" % (a, BASE, top))
        return 1
    o = at(a)

    if args.data:
        for i in range(args.data):
            p = o + i * 4
            if p + 4 > len(img):
                break
            w = struct.unpack_from("<I", img, p)[0]
            f = struct.unpack_from("<f", img, p)[0]
            fs = "%g" % f if 1e-8 < abs(f) < 1e9 else ""
            print("  0x%08X  %08X  %11d  %s" % (a + i * 4, w,
                                                struct.unpack_from("<i", img, p)[0], fs))
        return 0

    limit = args.count or 4000
    print("==== 0x%08X" % a)
    seen_jr = False
    for i in range(limit):
        p = o + i * 4
        if p + 4 > len(img):
            break
        w = struct.unpack_from("<I", img, p)[0]
        pc = a + i * 4
        print("  0x%08X  %08X  %s" % (pc, w, dis(w, pc)))
        if not args.count:
            if seen_jr:
                break                      # printed the delay slot, stop
            if w == 0x03E00008:
                seen_jr = True
    return 0


if __name__ == "__main__":
    sys.exit(main())
