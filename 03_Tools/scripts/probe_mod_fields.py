"""Who reads `verts:`, `normals:`, `primitives:` and the rest?

The version dispatcher at 0x003B6A40 only picks a reader. The layout lives in
whatever consumes the field-name strings, so this cross-references each of
them and prints the code around the hits.

The names are also the honest way to check the header reading: an eleven-count
header fitted the *shapes* of the first eleven words but not the file sizes,
so either the order differs from the string table's or some of those words are
not counts at all. Rather than fit harder, this looks at what the code does.
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

WANT = [b"verts:", b"normals:", b"colors:", b"tex1s:", b"tex2s:",
        b"tangents:", b"materials:", b"adjuncts:", b"primitives:",
        b"matrices:", b"reskins:", b"packets:", b"textures:"]


def find_string(img, s):
    """Every NUL-terminated occurrence of exactly this string."""
    out = []
    for m in re.finditer(re.escape(s) + rb"\x00", img):
        out.append(m.start())
    return out


def const_refs(words, base, target):
    """lui/addiu or lui/ori pairs that build `target`."""
    lo = target & 0xFFFF
    hi = ((target >> 16) + (1 if lo & 0x8000 else 0)) & 0xFFFF
    hits = []
    for i, w in enumerate(words):
        if (w >> 26) != 0x0F or (w & 0xFFFF) != hi:
            continue
        rt = (w >> 16) & 0x1F
        for j in range(i + 1, min(i + 10, len(words))):
            v = words[j]
            op = v >> 26
            if op == 0x09 and ((v >> 21) & 0x1F) == rt and (v & 0xFFFF) == lo:
                hits.append(base + j * 4)
                break
            if op == 0x0D and ((v >> 21) & 0x1F) == rt \
                    and (v & 0xFFFF) == (target & 0xFFFF):
                hits.append(base + j * 4)
                break
    return hits


def main():
    iso = Iso9660(DISC)
    e = iso.get("SLUS_205.00")
    img, base = elfpack.unpack_elf(iso.read_at(e.lba * 2048, e.size))
    n = len(img) // 4
    words = struct.unpack("<%dI" % n, img[:n * 4])
    print("image %d bytes @ 0x%08X\n" % (len(img), base))

    allrefs = {}
    for s in WANT:
        offs = find_string(img, s)
        print("%-13s %d copy(ies):" % (s.decode(), len(offs)), end="")
        hits = []
        for o in offs:
            va = base + o
            r = const_refs(words, base, va)
            hits += r
            print("  0x%08X->%d" % (va, len(r)), end="")
        print()
        allrefs[s.decode()] = sorted(hits)

    # Where do the references cluster? A parser reads them all in one place.
    print("\nreference sites, sorted -- a run of nearby addresses is the "
          "function that walks the whole header:")
    flat = sorted((a, k) for k, v in allrefs.items() for a in v)
    prev = None
    for a, k in flat:
        gap = "" if prev is None else "  (+0x%X)" % (a - prev)
        print("  0x%08X  %-12s%s" % (a, k, gap))
        prev = a


if __name__ == "__main__":
    main()
