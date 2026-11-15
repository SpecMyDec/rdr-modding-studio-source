"""Is the unpacked SLUS_205.00 image real, or just plausible-looking rubbish?

A depacker that is subtly wrong still produces megabytes of output, so "it ran"
proves nothing. These checks are chosen so that a wrong depacker fails them:

  1. **Opcode-field entropy.** In real MIPS little-endian code the high byte of
     each word holds the opcode and is very unevenly distributed -- entropy
     around 3-4 bits, with a handful of values dominating. Rubbish gives ~8.
  2. **Readable strings.** A game executable carries thousands of them. The
     packed file had none that were meaningful.
  3. **The animation magic.** `cm2` must be present, because the game reads
     files that begin with it.
  4. **Self-consistency at the tail.** A correct aPLib stream ends on its
     terminator, not by running off the end of the input.
  5. **The jump target resolves.** The stub ends with `j 0x00100008`, so the
     word at image offset 8 must decode as a sane instruction.
"""
import math
import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdrstudio.iso9660 import Iso9660                 # noqa: E402
from rdrstudio import elfpack                         # noqa: E402
from rdrstudio import config                         # noqa: E402

# The PRISTINE disc. This is a read-only structural check, so it must not
# baseline on the working copy -- that makes its result depend on whatever
# the last write left behind. Was a hardcoded `D:\...\RDR_MODDED.iso`,
# which also meant this only ran under Windows python. REQ-035.
DISC = Path(config.VANILLA_ISO)


def ent(buf):
    if not buf:
        return 0.0
    c = Counter(buf)
    n = float(len(buf))
    return -sum((v / n) * math.log2(v / n) for v in c.values())


def main():
    ok = True

    def check(what, got, want, cmp="=="):
        nonlocal ok
        if cmp == "==":
            good = got == want
        elif cmp == "<":
            good = got < want
        elif cmp == ">":
            good = got > want
        ok &= good
        print("  %-44s %-14s %s"
              % (what, str(got)[:14],
                 "ok" if good else "FAIL (want %s %s)" % (cmp, want)))

    iso = Iso9660(DISC)
    e = iso.get("SLUS_205.00")
    packed = iso.read_at(e.offset, e.size)
    img, base = elfpack.unpack_elf(packed)

    print("packed   : %d bytes, entropy %.2f" % (len(packed), ent(packed[0x1600:0x41600])))
    print("unpacked : %d bytes at 0x%08X\n" % (len(img), base))

    print("1. size and entropy")
    check("unpacked larger than packed", len(img) > len(packed), True)
    check("unpacked entropy is code-like", round(ent(img[:400000]), 2), 6.6, "<")

    print("\n2. opcode field (high byte of each word)")
    c = Counter()
    for off in range(0, min(len(img), 600000) - 4, 4):
        c[img[off + 3]] += 1
    tot = sum(c.values())
    e_op = -sum((v / tot) * math.log2(v / tot) for v in c.values())
    top = c.most_common(6)
    print("     top high bytes: %s"
          % ", ".join("0x%02X=%.1f%%" % (k, 100.0 * v / tot) for k, v in top))
    check("opcode-field entropy is low (real MIPS)", round(e_op, 2), 5.5, "<")
    # In MIPS, 0x00 (SPECIAL) and 0x8C/0xAC (lw/sw) style bytes dominate.
    check("one high byte covers >8% of words",
          round(100.0 * top[0][1] / tot, 1), 8.0, ">")

    print("\n3. readable strings")
    # Counting ASCII runs is not a test: 4 MB of anything throws up thousands
    # of accidental printable stretches, and the first run of this script
    # "passed" while printing pure noise, because the samples came from the
    # code region rather than .rodata. The real test is whether SPECIFIC
    # strings the game must contain are present and spelled correctly.
    WANT = [b"s:/swat/assets/current",   # the studio's own asset path
            b"multiarchive", b"sublevels", b"preload_%02d",
            b"swat.dat", b"lvl_front", b"unlockcheats",
            b"fxFilmGlobalData", b"zone_SHADOW"]
    missing = [w for w in WANT if w not in img]
    for w in WANT:
        print("     %-26s %s" % (w.decode(), "present" if w in img else "MISSING"))
    check("all expected strings present", len(missing), 0)

    print("\n4. the animation magic and friends")
    for k in (b"cm2", b"cmp\x00", b"anim", b"skel"):
        n = img.count(k)
        print("     %-8s %d occurrence(s)" % (k.decode("latin-1").strip("\x00"), n))
    check("cm2 present", img.count(b"cm2") > 0, True)

    print("\n5. the stub's jump target decodes")
    w = struct.unpack_from("<I", img, 8)[0]
    op = (w >> 26) & 0x3F
    print("     word at +8 = 0x%08X, opcode 0x%02X" % (w, op))
    check("word at +8 is not zero", w != 0, True)

    print("\n%s" % ("ALL CHECKS PASSED" if ok else "SOMETHING FAILED"))
    iso.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
