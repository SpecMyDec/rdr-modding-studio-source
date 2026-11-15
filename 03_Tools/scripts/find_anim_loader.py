"""Walk from the `cm2` extension string to the code that decodes the format.

The unpacked image holds `cm3\\0 cm2\\0 cmp\\0 anim\\0` adjacently in .rodata,
and each is returned by a two-instruction accessor of the shape

    lui   v0, 0x0052
    jr    ra
    addiu v0, v0, <low half>

which is what a C++ `virtual const char* extension()` compiles to. So the
format is handled by a class, and that accessor's address will appear inside
the class's vtable. Everything else in that vtable is the rest of the loader --
including whatever reads the frames.

Steps:
  1. Find every such accessor and which string it returns.
  2. Find data words equal to each accessor's address: those are vtable slots.
  3. Print the vtable around the hit, resolving each slot to a function.
  4. Disassemble the most promising sibling -- the one that is largest and
     contains shifts/masks, i.e. the decompressor.
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


def main():
    iso = Iso9660(DISC)
    e = iso.get("SLUS_205.00")
    img, base = elfpack.unpack_elf(iso.read_at(e.offset, e.size))
    lo, hi = base, base + len(img)
    print("image 0x%08X .. 0x%08X" % (lo, hi))

    # ------------------------------------------------ 1. the extension strings
    tbl = img.find(b"cm3\x00cm2\x00cmp\x00anim\x00")
    if tbl < 0:
        print("extension string block not found")
        return 1
    strs = {}
    for i, name in enumerate(("cm3", "cm2", "cmp", "anim")):
        strs[name] = base + tbl + sum(len(n) + 1 for n in
                                      ("cm3", "cm2", "cmp", "anim")[:i])
    print("\nextension strings:")
    for k, v in strs.items():
        print("  %-5s 0x%08X" % (k, v))

    # ------------------------------------------------------- 2. the accessors
    print("\n" + "=" * 70)
    print("accessors: lui/jr/addiu returning one of those strings")
    print("=" * 70)
    accessors = {}
    for off in range(0, len(img) - 12, 4):
        w0 = struct.unpack_from("<I", img, off)[0]
        w1 = struct.unpack_from("<I", img, off + 4)[0]
        w2 = struct.unpack_from("<I", img, off + 8)[0]
        if ((w0 >> 26) & 0x3F) != 0x0F:            # lui
            continue
        if w1 != 0x03E00008:                        # jr ra
            continue
        if ((w2 >> 26) & 0x3F) != 0x09:            # addiu (in the delay slot)
            continue
        himm = w0 & 0xFFFF
        limm = w2 & 0xFFFF
        slimm = limm - 0x10000 if limm & 0x8000 else limm
        addr = (himm << 16) + slimm
        for name, sa in strs.items():
            if addr == sa:
                accessors.setdefault(name, []).append(base + off)
    for name in ("cm3", "cm2", "cmp", "anim"):
        for a in accessors.get(name, []):
            print("  %-5s -> accessor at 0x%08X" % (name, a))
    if not accessors:
        print("  none found")

    # -------------------------------------------------------- 3. the vtables
    print("\n" + "=" * 70)
    print("vtable slots pointing at those accessors")
    print("=" * 70)
    words = struct.unpack_from("<%dI" % (len(img) // 4), img, 0)

    def fname(a):
        return "0x%08X" % a if lo <= a < hi else "-"

    for name in ("cm3", "cm2", "cmp", "anim"):
        for acc in accessors.get(name, []):
            for i, w in enumerate(words):
                if w != acc:
                    continue
                va = base + i * 4
                print("\n  %s accessor referenced from 0x%08X:" % (name, va))
                for j in range(max(0, i - 6), min(len(words), i + 10)):
                    w2 = words[j]
                    mark = "->" if j == i else "  "
                    print("    %s 0x%08X  %s" % (mark, base + j * 4, fname(w2)))

    # ------------------------------------------- 4. the biggest shifty sibling
    print("\n" + "=" * 70)
    print("the loader's siblings, ranked by shift/mask density")
    print("=" * 70)
    # Collect candidate function addresses from every vtable that held a cm2
    # or anim accessor, then score each.
    cands = set()
    for name in ("cm2", "anim", "cmp", "cm3"):
        for acc in accessors.get(name, []):
            for i, w in enumerate(words):
                if w != acc:
                    continue
                for j in range(max(0, i - 8), min(len(words), i + 12)):
                    a = words[j]
                    if lo <= a < hi and (a & 3) == 0:
                        cands.add(a)
    print("  %d candidate functions from the vtables" % len(cands))
    scored = []
    for a in sorted(cands):
        off = a - base
        n = 0
        size = 0
        for k in range(0, 400):
            o = off + k * 4
            if o + 4 > len(img):
                break
            w = struct.unpack_from("<I", img, o)[0]
            size = k * 4
            if w == 0x03E00008 and k > 2:          # jr ra -- end of function
                break
            opc = (w >> 26) & 0x3F
            fn = w & 0x3F
            if opc == 0x0C or (opc == 0 and fn in (0x00, 0x02, 0x03, 0x04,
                                                   0x06, 0x07, 0x24, 0x25)):
                n += 1
        scored.append((n, size, a))
    scored.sort(reverse=True)
    for n, size, a in scored[:6]:
        print("\n  0x%08X  %d shift/mask ops in %d bytes" % (a, n, size))
        off = a - base
        for k in range(0, min(52, size // 4 + 2)):
            o = off + k * 4
            if o + 4 > len(img):
                break
            w = struct.unpack_from("<I", img, o)[0]
            print("      0x%08X  %08X  %s" % (a + k * 4, w, D.dis(w, a + k * 4)))

    iso.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
