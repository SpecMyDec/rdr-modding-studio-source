"""Is SLUS_205.00 packed?

The animation magic `cm2` appears NOWHERE in the 1.8 MB executable -- not as a
string, not as the u32 0x00326D63, not as a lui/ori immediate pair. The game
demonstrably reads files that start with those bytes, so the comparison exists;
it just is not present in the file as shipped. Meanwhile the bytes around the
few incidental 0x6D63 matches look like noise, and there is a small
shift/mask/load routine sitting right at the ELF entry point.

All of that points one way: the executable is compressed and unpacks itself at
boot. This measures it instead of assuming it.

Three tests, each of which a normal MIPS binary fails and a packed one passes:

  1. **Entropy per 4 KB.** Real MIPS code sits around 5.0-6.5 bits/byte because
     every instruction is 4 bytes with a heavily skewed opcode field. Packed
     data sits above 7.8.
  2. **The low-byte-of-instruction test.** In real MIPS little-endian code the
     HIGH byte of each word carries the opcode and is very unevenly
     distributed. If byte 3 of every word is uniform, it is not code.
  3. **Printable-string density.** A 1.8 MB game executable normally carries
     thousands of readable strings -- filenames, format names, assert text.
     Near-zero readable strings means they are packed too.
"""
import math
import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdrstudio.iso9660 import Iso9660                 # noqa: E402
from rdrstudio.config import VANILLA_ISO             # noqa: E402

# The STOCK disc, not the working one: a read-only structural probe must
# not depend on what the last test left behind (REQ-035). The repoint was
# measured output-neutral at the time it was made -- see REQ-074.
DISC = Path(VANILLA_ISO)
ELF = "SLUS_205.00"
BLOCK = 4096


def entropy(buf):
    if not buf:
        return 0.0
    c = Counter(buf)
    n = float(len(buf))
    return -sum((v / n) * math.log2(v / n) for v in c.values())


def main():
    iso = Iso9660(DISC)
    e = iso.get(ELF)
    data = iso.read_at(e.offset, e.size)
    print("%s: %d bytes (%.2f MB)" % (ELF, len(data), len(data) / 1e6))

    # ------------------------------------------------------------ 1. entropy
    print("\n" + "=" * 70)
    print("entropy per 4 KB block  (MIPS code ~5.0-6.5, packed >7.8)")
    print("=" * 70)
    ents = []
    for off in range(0, len(data), BLOCK):
        ents.append((off, entropy(data[off:off + BLOCK])))
    lowish = [o for o, v in ents if v < 6.5]
    high = [o for o, v in ents if v > 7.8]
    print("  %d blocks total" % len(ents))
    print("  below 6.5 bits/byte (code-like) : %d  (%.1f%%)"
          % (len(lowish), 100.0 * len(lowish) / len(ents)))
    print("  above 7.8 bits/byte (packed)    : %d  (%.1f%%)"
          % (len(high), 100.0 * len(high) / len(ents)))
    print("\n  first 12 blocks:")
    for off, v in ents[:12]:
        print("    0x%06X  %.2f  %s" % (off, v, "#" * int(v * 4)))
    print("  ...")
    print("  last 6 blocks:")
    for off, v in ents[-6:]:
        print("    0x%06X  %.2f  %s" % (off, v, "#" * int(v * 4)))
    # Where does the code-like region END? That is the unpacker's size.
    run = 0
    for off, v in ents:
        if v < 6.5:
            run = off + BLOCK
        else:
            break
    print("\n  contiguous code-like region from 0x000000 to 0x%06X (%d bytes)"
          % (run, run))

    # -------------------------------------------- 2. opcode byte distribution
    print("\n" + "=" * 70)
    print("high byte of each 32-bit word (the MIPS opcode field lives here)")
    print("=" * 70)
    for label, lo, hi in (("entry region", 0x1000, 0x3000),
                          ("middle", len(data) // 2, len(data) // 2 + 0x2000),
                          ("end", len(data) - 0x2000, len(data))):
        c = Counter()
        for off in range(lo, min(hi, len(data)) - 4, 4):
            c[data[off + 3]] += 1
        tot = sum(c.values()) or 1
        top = c.most_common(4)
        ent = -sum((v / tot) * math.log2(v / tot) for v in c.values())
        print("  %-13s distinct high bytes %3d, entropy %.2f bits, top: %s"
              % (label, len(c), ent,
                 ", ".join("0x%02X=%.0f%%" % (k, 100.0 * v / tot) for k, v in top)))
    print("\n  (real MIPS: a handful of high bytes dominate, entropy ~3-4 bits.")
    print("   uniform 8 bits means the words are not instructions.)")

    # --------------------------------------------------- 3. printable strings
    print("\n" + "=" * 70)
    print("readable strings")
    print("=" * 70)
    runs = []
    cur = []
    for b in data:
        if 32 <= b < 127:
            cur.append(b)
        else:
            if len(cur) >= 8:
                runs.append(bytes(cur))
            cur = []
    print("  ASCII runs of 8+ chars: %d" % len(runs))
    if runs:
        print("  longest 12:")
        for s in sorted(runs, key=len, reverse=True)[:12]:
            print("    %r" % s[:70])
    known = [b"anim", b"skel", b".rsc", b"DAVE", b"cm2", b"TEX0", b"MOD0"]
    print("\n  format names present as plain text:")
    for k in known:
        print("    %-6s %d occurrence(s)" % (k.decode(), data.count(k)))

    print("\n" + "=" * 70)
    print("verdict")
    print("=" * 70)
    # Counting ASCII runs was the wrong test and it gave the wrong answer on
    # the first run: random bytes throw up plenty of accidental 8-character
    # printable stretches, so 669 "strings" that are all gibberish is evidence
    # FOR packing, not against it. What matters is whether any of them are
    # meaningful, and the sharpest form of that is whether the format magics
    # the game provably reads are present at all.
    magics_present = sum(1 for k in known if data.count(k))
    real_words = sum(1 for s in runs
                     if b"_" in s or s.lower().count(b"e") >= 2)
    print("  code-like 4 KB blocks      : %d of %d" % (len(lowish), len(ents)))
    print("  packed-looking blocks      : %d of %d" % (len(high), len(ents)))
    print("  format magics found        : %d of %d" % (magics_present, len(known)))
    print("  ASCII runs that look like words: %d of %d" % (real_words, len(runs)))
    packed = (len(high) > len(ents) * 0.5) and magics_present == 0
    if packed:
        print("  PACKED. The shipped executable is compressed; only a small")
        print("  unpacker at the entry point is plain code. The cm2 decoder is")
        print("  in there, but not readable without undoing the packing, so")
        print("  static analysis of the shipped file cannot reach it.")
        print("\n  What that leaves for cracking cm2:")
        print("    * a PCSX2 save state -- eeMemory.bin holds the UNPACKED code")
        print("      as well as live decoded bone matrices in vu1Memory.bin.")
        print("      That is the practical route, and it needs an uncompressed")
        print("      state (SavestateCompressionType = 0, already set).")
    else:
        print("  NOT packed -- plain code. The magic's absence needs another")
        print("  explanation.")

    iso.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
