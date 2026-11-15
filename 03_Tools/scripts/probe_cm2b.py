"""cm2, second pass: header size, per-channel table, and the tail.

From the first pass:
    +0x00  char[4]  "cm2\0" (3,771 files) or "cmp\0" (234)
    +0x04  u32      frames    (1..1101)
    +0x08  u32      channels  (6..219, ALWAYS divisible by 3 = bones*3)
    payload entropy is 7.16 bits/byte and size is NOT linear in
    frames*channels -- so keyframed and bit-packed, not fixed-rate.

Open questions this answers:
  1. Where does the header end and the payload start?
  2. Is there a per-channel table (offsets, bit widths, or key counts)?
     A 42-byte file with 6 channels leaves almost no room, which is a
     strong constraint.
  3. What are the trailing bytes? Several small files end in a word whose
     high byte is 0x46/0x47 -- the exponent range of a plausible float.
  4. Do the `.animsnd` keyframe lists (plain text, e.g. "Frame 1 18 61 83")
     line up with the header's frame count? That would confirm the field.
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rdrstudio import Game, props  # noqa: E402
from rdrstudio.config import VANILLA_ISO  # noqa: E402


def main():
    game = Game(VANILLA_ISO)
    anims, snds = {}, {}
    for arc in game.archives():
        try:
            dv = game.archive(arc)
        except Exception:
            continue
        for e in dv:
            if e.ext == "anim" and e.name not in anims:
                try:
                    anims[e.name] = dv.read(e)
                except Exception:
                    pass
            elif e.ext == "animsnd" and e.name not in snds:
                try:
                    snds[e.name] = dv.read(e)
                except Exception:
                    pass
        game._cache.pop(arc.upper(), None)

    good = {n: b for n, b in anims.items() if b[:4] in (b"cm2\0", b"cmp\0")}
    print("cm2/cmp files: %d   .animsnd files: %d" % (len(good), len(snds)))

    # ---- 3. the tail word -------------------------------------------------
    print("\ntail: is the LAST u32 a plausible float?")
    tail_ok = tail_n = 0
    vals = []
    for b in good.values():
        if len(b) < 8:
            continue
        tail_n += 1
        v = struct.unpack_from("<f", b, len(b) - 4)[0]
        vals.append(v)
        if 0.0 < v < 1e6:
            tail_ok += 1
    vals.sort()
    print("  plausible positive float: %d / %d (%.1f%%)"
          % (tail_ok, tail_n, 100.0 * tail_ok / tail_n))
    print("  median %.1f   p10 %.1f   p90 %.1f"
          % (vals[len(vals) // 2], vals[len(vals) // 10],
             vals[9 * len(vals) // 10]))

    # ---- 2. per-channel table --------------------------------------------
    # If a table of `channels` entries sits right after a fixed header, then
    # header + channels*width must fit inside every file. Anything that fails
    # even once is not the layout.
    print("\nper-channel table: which (header, entry width) survives every file?")
    for hdr in (0x0C, 0x10, 0x14, 0x18, 0x1C, 0x20):
        for width in (1, 2, 4):
            fits = mono = tot = 0
            for b in good.values():
                ch = struct.unpack_from("<I", b, 8)[0]
                if not (0 < ch < 4000):
                    continue
                tot += 1
                if hdr + ch * width > len(b):
                    continue
                fits += 1
                fmt = {1: "B", 2: "H", 4: "I"}[width]
                t = struct.unpack_from("<%d%s" % (ch, fmt), b, hdr)
                if all(y >= x for x, y in zip(t, t[1:])):
                    mono += 1
            if tot:
                print("  header 0x%02X, %d-byte entries: fits %5d/%5d (%5.1f%%)"
                      "   non-decreasing %5d (%5.1f%% of those)"
                      % (hdr, width, fits, tot, 100.0 * fits / tot,
                         mono, 100.0 * mono / max(1, fits)))

    # ---- 4. .animsnd cross-reference -------------------------------------
    print("\n.animsnd cross-reference (its 'Frame' list vs the header)")
    pairs = frame_ok = 0
    examples = []
    for name, blob in snds.items():
        cand = name.rsplit(".", 1)[0] + ".anim"
        if cand not in good:
            continue
        frames = None
        for f in props.parse(blob):
            if f.key == "Frame":
                try:
                    frames = [int(x) for x in f.parts]
                except ValueError:
                    frames = None
                break
        if not frames:
            continue
        pairs += 1
        b = good[cand]
        hdr_frames = struct.unpack_from("<I", b, 4)[0]
        if max(frames) <= hdr_frames:
            frame_ok += 1
        if len(examples) < 8:
            examples.append((cand[:34], hdr_frames,
                             struct.unpack_from("<I", b, 8)[0],
                             len(frames), max(frames), len(b)))
    print("  paired .anim/.animsnd: %d" % pairs)
    if pairs:
        print("  max keyframe <= header frames: %d / %d (%.1f%%)"
              % (frame_ok, pairs, 100.0 * frame_ok / pairs))
    for e in examples:
        print("    %-34s frames=%-5d chan=%-4d keys=%-3d last=%-5d bytes=%d" % e)

    # ---- 1. bytes per sample --------------------------------------------
    print("\nbytes per (frame x channel)")
    ratios = []
    for b in good.values():
        fr = struct.unpack_from("<I", b, 4)[0]
        ch = struct.unpack_from("<I", b, 8)[0]
        if 0 < fr < 20000 and 0 < ch < 4000:
            ratios.append((len(b) - 0x14) / float(fr * ch))
    ratios.sort()
    print("  n=%d  min %.4f  p10 %.4f  median %.4f  p90 %.4f  max %.4f"
          % (len(ratios), ratios[0], ratios[len(ratios) // 10],
             ratios[len(ratios) // 2], ratios[9 * len(ratios) // 10],
             ratios[-1]))
    print("  a fixed-rate int16 stream would sit at exactly 2.0.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
