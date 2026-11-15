"""Find the animation decoder in the UNPACKED executable.

`rdrstudio/elfpack.py` now undoes the aPLib+XOR packing, so the game's own code
is readable for the first time: 4.5 MB of MIPS at 0x00100000, with .rodata
around 0x00520000 in address terms. That makes the cm2 decoder reachable by
static analysis, which it was not while the shipped file was packed.

Strategy, cheapest first:

  1. Find the `cm2` / `cmp` strings in .rodata and note their addresses.
  2. Find every instruction that materialises one of those addresses -- MIPS
     builds a 32-bit address with `lui reg, hi` followed by an
     `addiu/ori reg, reg, lo`, so searching for the hi/lo pair locates the
     references.
  3. Also search for the magic as a 32-bit COMPARISON constant: a loader that
     checks `*(u32*)hdr == 'cm2\\0'` needs 0x00326D63 built the same way.
  4. Dump the code around each reference so the loader is readable.
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rdrstudio.iso9660 import Iso9660                 # noqa: E402
from rdrstudio import config                          # noqa: E402
from rdrstudio import elfpack                         # noqa: E402
import dis_elf_stub as D                              # noqa: E402

# REQ-035/036. Was a literal path to the WORKING ISO on one machine:
# unrunnable on anyone else's checkout, and pointed at the disc the live
# tests mutate. What this examines is a property of the SHIPPED data, so
# it reads the stock disc. Read-only throughout, which is what makes
# vanilla the right answer here rather than merely the tidier one.
DISC = Path(config.VANILLA_ISO)


def refs_to(img, base, target):
    """Every `lui`/`addiu|ori` pair in the image that builds `target`.

    MIPS sign-extends the `addiu` immediate, so the high half the assembler
    emits is `(target >> 16) + (1 if (target & 0x8000) else 0)`.
    """
    lo = target & 0xFFFF
    hi = ((target >> 16) + (1 if lo & 0x8000 else 0)) & 0xFFFF
    slo = lo - 0x10000 if lo & 0x8000 else lo
    out = []
    for off in range(0, len(img) - 8, 4):
        w = struct.unpack_from("<I", img, off)[0]
        if ((w >> 26) & 0x3F) != 0x0F:          # lui
            continue
        if (w & 0xFFFF) != hi:
            continue
        reg = (w >> 16) & 0x1F
        # The low half is usually the next instruction, but the compiler may
        # schedule one or two instructions in between.
        for k in range(1, 6):
            if off + k * 4 + 4 > len(img):
                break
            w2 = struct.unpack_from("<I", img, off + k * 4)[0]
            op2 = (w2 >> 26) & 0x3F
            if op2 not in (0x09, 0x0D):          # addiu / ori
                continue
            if ((w2 >> 21) & 0x1F) != reg:
                continue
            imm2 = w2 & 0xFFFF
            simm2 = imm2 - 0x10000 if imm2 & 0x8000 else imm2
            if (op2 == 0x09 and simm2 == slo) or (op2 == 0x0D and imm2 == lo):
                out.append((off, off + k * 4))
                break
    return out


def dump(img, base, off, n=40, label=""):
    print("\n  ---- %s  vaddr 0x%08X (image +0x%06X)" % (label, base + off, off))
    for i in range(-4, n):
        o = off + i * 4
        if o < 0 or o + 4 > len(img):
            continue
        w = struct.unpack_from("<I", img, o)[0]
        pc = base + o
        mark = "->" if i == 0 else "  "
        print("   %s 0x%08X  %08X  %s" % (mark, pc, w, D.dis(w, pc)))


def main():
    iso = Iso9660(DISC)
    e = iso.get("SLUS_205.00")
    img, base = elfpack.unpack_elf(iso.read_at(e.offset, e.size))
    print("unpacked image: %d bytes at 0x%08X" % (len(img), base))

    # ------------------------------------------------- 1. the strings/magics
    print("\n" + "=" * 70)
    print("occurrences of the animation magics in the unpacked image")
    print("=" * 70)
    spots = []
    for needle in (b"cm2\x00", b"cmp\x00", b"cm2", b"cmp"):
        pos = -1
        hits = []
        while len(hits) < 12:
            pos = img.find(needle, pos + 1)
            if pos < 0:
                break
            hits.append(pos)
        print("  %-9r %d hit(s)" % (needle, len(hits)))
        for h in hits:
            ctx = img[max(0, h - 24):h + 24]
            print("     +0x%06X vaddr 0x%08X   %r"
                  % (h, base + h, ctx))
            spots.append(h)

    # ---------------------------------------- 2. references to those strings
    print("\n" + "=" * 70)
    print("code that references the magic string's ADDRESS")
    print("=" * 70)
    for h in sorted(set(spots)):
        target = base + h
        rs = refs_to(img, base, target)
        print("  string at 0x%08X: %d reference(s)" % (target, len(rs)))
        for luioff, addioff in rs[:3]:
            dump(img, base, luioff, 28, "reference to 0x%08X" % target)

    # -------------------------------- 3. the magic as a comparison constant
    print("\n" + "=" * 70)
    print("the magic as the 32-bit constant 0x00326D63 ('cm2\\0')")
    print("=" * 70)
    for name, val in (("cm2\\0", 0x00326D63), ("cmp\\0", 0x00706D63)):
        rs = refs_to(img, base, val)
        print("  %s = 0x%08X : %d site(s) building it" % (name, val, len(rs)))
        for luioff, addioff in rs[:4]:
            dump(img, base, luioff, 34, "compares against %s" % name)

    iso.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
