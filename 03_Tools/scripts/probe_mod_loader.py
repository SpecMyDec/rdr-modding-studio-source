"""Find the code that READS a loose .mod file.

Guessing a layout from header statistics is how you end up with a decoder that
works on the four files you looked at. The executable is unpacked now, and
that is what cracked cm2: the format is written down in the code that parses
it.

Every loose .mod opens with the ASCII string "version: 2.12", so the parser
almost certainly compares against a literal like that. This locates such
strings in the image, finds the instruction pairs that materialise their
addresses (MIPS builds a 32-bit constant from `lui` + `addiu`, so an address
appears as two halves and never as one searchable word), and prints the code
around each reference.
"""
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdrstudio.iso9660 import Iso9660                 # noqa: E402
from rdrstudio import elfpack                         # noqa: E402
from rdrstudio.config import VANILLA_ISO             # noqa: E402

# The STOCK disc, not the working one: a read-only structural probe must
# not depend on what the last test left behind (REQ-035). The repoint was
# measured output-neutral at the time it was made -- see REQ-074.
DISC = Path(VANILLA_ISO)


def strings_at(img, pattern, base):
    """Every occurrence of a regex in the image, with its virtual address."""
    out = []
    for m in re.finditer(pattern, img):
        s = img[m.start():m.start() + 64].split(b"\x00")[0]
        out.append((base + m.start(), m.start(), s.decode("latin-1")))
    return out


def lui_addiu_refs(img, base, target):
    """Where is `target` built as a 32-bit constant?

    MIPS cannot load a 32-bit immediate in one instruction, so an address is
    assembled as `lui rX, hi` then `addiu rX, rX, lo` -- where `hi` carries a
    +1 correction whenever the low half is negative as a signed 16-bit value.
    Searching for the whole address as a word finds nothing; this finds the
    halves.
    """
    lo = target & 0xFFFF
    hi = (target >> 16) & 0xFFFF
    if lo & 0x8000:
        hi = (hi + 1) & 0xFFFF
    hits = []
    n = len(img) // 4
    words = struct.unpack("<%dI" % n, img[:n * 4])
    for i, w in enumerate(words):
        # lui rt, imm  ->  0x3C00_0000 | rt<<16 | imm
        if (w >> 26) != 0x0F:
            continue
        if (w & 0xFFFF) != hi:
            continue
        rt = (w >> 16) & 0x1F
        # the matching addiu usually follows within a few instructions
        for j in range(i + 1, min(i + 8, n)):
            v = words[j]
            op = v >> 26
            if op == 0x09 and ((v >> 21) & 0x1F) == rt and (v & 0xFFFF) == lo:
                hits.append((base + i * 4, base + j * 4))
                break
            # ori also builds constants, with no sign correction
            if op == 0x0D and ((v >> 21) & 0x1F) == rt \
                    and (v & 0xFFFF) == (target & 0xFFFF):
                hits.append((base + i * 4, base + j * 4))
                break
    return hits


def main():
    iso = Iso9660(DISC)
    e = iso.get("SLUS_205.00")
    packed = iso.read_at(e.lba * 2048, e.size)
    img, base = elfpack.unpack_elf(packed)
    print("unpacked %d bytes, loaded at 0x%08X\n" % (len(img), base))

    # ---- 1. the version strings themselves
    print("=" * 66)
    print("strings that look like a format version")
    print("=" * 66)
    found = strings_at(img, rb"version: ?[0-9]", base)
    for va, off, s in found[:40]:
        print("  0x%08X  %r" % (va, s))
    print("  (%d total)\n" % len(found))

    # Also the printf-style ones, which name the fields around them.
    for pat, label in ((rb"version: ?%", "format string"),
                       (rb"\.mod\x00", '".mod" literal'),
                       (rb"%s\.mod", '".mod" template')):
        hits = strings_at(img, pat, base)
        if hits:
            print("%s:" % label)
            for va, off, s in hits[:12]:
                print("  0x%08X  %r" % (va, s))
            print()

    # ---- 2. who references them
    print("=" * 66)
    print("code that builds the address of each version string")
    print("=" * 66)
    for va, off, s in found[:12]:
        refs = lui_addiu_refs(img, base, va)
        print("\n  %r  @ 0x%08X  --  %d reference(s)" % (s, va, len(refs)))
        for lui_va, add_va in refs[:6]:
            print("     built at 0x%08X .. 0x%08X" % (lui_va, add_va))

    # ---- 3. nearby strings, which usually name the fields
    print("\n" + "=" * 66)
    print("other strings in the same neighbourhood (these tend to be the")
    print("field names the parser is looking for)")
    print("=" * 66)
    for va, off, s in found:
        print("\n---- around %r @ 0x%08X ----" % (s, va))
        lo = max(0, off - 1400)
        hi = min(len(img), off + 1400)
        toks = [m.group().decode("latin-1")
                for m in re.finditer(rb"[ -~]{3,}", img[lo:hi])]
        print("  " + " | ".join(toks))


if __name__ == "__main__":
    main()
