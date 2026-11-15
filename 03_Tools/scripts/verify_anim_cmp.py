"""The `cmp` codec -- CONTAINER decoded, sample bit-packing still open.

234 `.anim` files carry the `cmp\\0` magic. Its readData is at 0x002D35F0 (the
first slot of the cmp vtable at 0x005078CC). Read off the instruction stream,
the container is:

    +0x00  the same 17-byte header as cm2 (magic, frames, channels, unknown
           f32, flags). K = (flags & 8) ? frames/2 + 1 : frames, as everywhere.
    +0x11  channels x 12-byte per-channel records. The loop at 0x002D36A0
           reads, per channel, 4 + 4 + 2 + 2 bytes:
             f32  scale   (0x002D36B0, read into the stack slot's +0)
             f32  bias    (0x002D36C8, +4)
             s16  width   (0x002D36DC, +8)  -- the sampler at 0x002D37B8 reads
                          the low byte of this back as a per-channel value
             s16  (2 more bytes, 0x002D36EC, consumed into scratch)
    then   s16  a global count (0x002D3708)
    then   the packed sample block.

The scale is telling: it is 0.0005 = 1/2000 on the channels checked, the same
default cm2 uses for its translation group, so `value = raw * scale + bias` is
almost certainly the dequantiser -- an adaptive per-channel scale AND bias,
where cm2 had a scale per GROUP and no bias. That makes `cmp` the more
sophisticated of the two codecs, not a simpler one.

WHAT IS VERIFIED HERE, and it is only the container: the whole file is accounted
for by

    size = 17 + 12*channels + 2 + K * bytesPerKey

with `bytesPerKey` a per-file integer -- exact on all 234 files. bytesPerKey
varies from file to file (4..80+ for the same 159 channels across different
clips), which is the signature of a per-channel adaptive BIT width: a frame is
the channels bit-packed at their individual widths, rounded up to bytes.

WHAT IS NOT DONE: where the per-channel bit widths live and how the frame is
unpacked. The obvious candidate -- sum(record[+8]) / 8 == bytesPerKey -- does
NOT hold (0 of 234), so the width is not a raw byte in the record at +8, and
the packing needs the sampler at 0x002D33C8/0x002D3470 for the cmp class traced
properly rather than guessed. Until then this codec decodes NOTHING; it only
knows the file is well-formed. The tool must keep refusing to decode a cmp file,
not pretend.
"""
import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdrstudio.game import Game                      # noqa: E402
from rdrstudio import config                          # noqa: E402

# REQ-035. Was a literal path to the WORKING ISO on one machine:
# unrunnable on anyone else's checkout, and pointed at the disc the
# live tests mutate, so a read-only structural check could go red for
# a reason with nothing to do with the code under test. What this
# measures is a property of the shipped clips, so it reads the stock
# disc. Opened writable=False, which is what makes vanilla correct here.
DISC = Path(config.VANILLA_ISO)


def main():
    game = Game(DISC, writable=False)
    ok = True

    def check(what, got, want, cmp="=="):
        nonlocal ok
        good = (got == want if cmp == "==" else got > want if cmp == ">" else got < want)
        ok &= good
        print("  %-52s %-12s %s"
              % (what, str(got)[:12], "ok" if good else "FAIL (want %s %s)" % (cmp, want)))

    rows = []
    seen = set()
    for arc in game.archives():
        try:
            toc = game.toc(arc)
        except Exception:
            continue
        for e in toc:
            n = e.name.lower()
            if not n.endswith(".anim") or n in seen:
                continue
            seen.add(n)
            try:
                d = game.read(arc, e.name)
            except Exception:
                continue
            if len(d) < 20 or d[:4] != b"cmp\x00":
                continue
            fr, ch = struct.unpack_from("<II", d, 4)
            rows.append((n, d, len(d), fr & 0xFFFF, ch & 0xFFFF, d[0x10]))

    print("%d cmp files\n" % len(rows))
    check("found the cmp set", len(rows), 234)

    # The container size formula must account for every byte.
    clean = 0
    bpk_by_ch = {}
    for n, d, size, fr, ch, fl in rows:
        keys = (fr // 2 + 1) if (fl & 8) else fr
        rem = size - 17 - 12 * ch - 2
        if keys and rem > 0 and rem % keys == 0:
            clean += 1
            bpk_by_ch.setdefault(ch, set()).add(rem // keys)
    print("\ncontainer: size == 17 + 12*channels + 2 + keys*bytesPerKey")
    check("files accounted for exactly", clean, len(rows))

    # bytesPerKey is per-FILE, not a function of channels -- which is what
    # says the widths are adaptive rather than fixed.
    varying = sum(1 for ch, s in bpk_by_ch.items() if len(s) > 1)
    print("\n  channel counts where bytesPerKey varies between files: %d of %d"
          % (varying, len(bpk_by_ch)))
    check("bytesPerKey is adaptive (varies per file)", varying, 0, ">")

    # The scale field really is a small positive dequantiser on real channels.
    scales = []
    for n, d, size, fr, ch, fl in rows[:60]:
        for c in range(min(ch, 8)):
            s = struct.unpack_from("<f", d, 17 + 12 * c)[0]
            if 1e-6 < abs(s) < 1.0:
                scales.append(s)
    scales.sort()
    if scales:
        print("\n  per-channel scale field: %d plausible, median %.6g"
              % (len(scales), scales[len(scales) // 2]))
        check("scales look like a dequantiser (0 < s < 1)", len(scales), 50, ">")

    print("\n  channel counts present: %s"
          % sorted(Counter(r[4] for r in rows)))

    print("\n  NOTE: this verifies the CONTAINER only. The sample bit-packing")
    print("  is not decoded, so no cmp file is turned into curves yet -- the")
    print("  tool refuses them, correctly. See task list / FORMAT_NOTES.md.")

    print("\n%s" % ("ALL CHECKS PASSED" if ok else "SOMETHING FAILED"))
    game.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
