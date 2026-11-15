"""What is the shape of a cm2 animation?

11,138 `.anim` files. Known already: magic `cm2 ` or `cmp`, a frame count and a
channel count in the header, and channels == bones * 3 (verified against the
paired `.skel`). The curve data itself is unread.

This does not try to crack the encoding. It answers the container questions
first, because guessing at bit layouts before knowing whether the file is
keyframed or fixed-rate is how you burn a day:

  * exact header fields, by looking at the smallest files where there is
    almost nothing but header
  * is there a per-channel offset or size table after the header?
  * fixed-rate or keyframed? -- test whether payload size is linear in
    frames * channels. A tight fit means every channel is sampled every
    frame; a poor fit means keyframes.
  * how much entropy is in the payload? Under ~7 bits/byte it is structured
    data you can read, not a compressed stream.
"""
import math
import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rdrstudio import Game  # noqa: E402
from rdrstudio.config import VANILLA_ISO  # noqa: E402


def entropy(b):
    if not b:
        return 0.0
    c = Counter(b)
    n = len(b)
    return -sum((v / n) * math.log2(v / n) for v in c.values())


def main():
    game = Game(VANILLA_ISO)
    files = []
    seen = set()
    for arc in game.archives():
        try:
            dv = game.archive(arc)
        except Exception:
            continue
        for e in dv:
            if e.ext != "anim" or e.name in seen:
                continue
            seen.add(e.name)
            try:
                files.append((e.name, dv.read(e)))
            except Exception:
                pass
        game._cache.pop(arc.upper(), None)

    print("distinct .anim files: %d" % len(files))
    magics = Counter(b[:4] for _, b in files)
    print("magics: %s" % {k.decode("latin-1", "replace"): v
                          for k, v in magics.most_common()})

    # ---- header field census over the first 16 u32s -------------------
    print("\nheader words, over all files (distinct values / min / max)")
    ncols = 16
    cols = [[] for _ in range(ncols)]
    for _, b in files:
        for i in range(ncols):
            if 4 + i * 4 + 4 <= len(b):
                cols[i].append(struct.unpack_from("<I", b, 4 + i * 4)[0])
    for i, col in enumerate(cols):
        if not col:
            continue
        u = len(set(col))
        f = [struct.unpack("<f", struct.pack("<I", v))[0] for v in col[:400]]
        plaus = sum(1 for x in f if 0.0 < abs(x) < 10000.0)
        print("  +0x%02X  u32 distinct %-6d min %-10d max %-12d  "
              "looks-float %d%%"
              % (4 + i * 4, u, min(col), max(col), 100 * plaus // max(1, len(f))))

    # ---- the smallest files: nearly all header -----------------------
    files.sort(key=lambda t: len(t[1]))
    print("\nsmallest files (hex of the first 96 bytes)")
    for name, b in files[:4]:
        print("\n  %s  (%d bytes)" % (name, len(b)))
        for o in range(0, min(96, len(b)), 16):
            row = b[o:o + 16]
            print("    +%03X  %-47s  %s"
                  % (o, row.hex(" "),
                     "".join(chr(c) if 32 <= c < 127 else "." for c in row)))

    # ---- fixed-rate or keyframed? -----------------------------------
    # Take the two most plausible header slots as frames and channels and see
    # which pair makes payload size linear.
    print("\nfixed-rate test: is payload size linear in frames * channels?")
    best = None
    for fi in range(ncols):
        for ci in range(ncols):
            if fi == ci:
                continue
            pts = []
            for _, b in files:
                if 4 + max(fi, ci) * 4 + 4 > len(b):
                    continue
                fr = struct.unpack_from("<I", b, 4 + fi * 4)[0]
                ch = struct.unpack_from("<I", b, 4 + ci * 4)[0]
                if not (0 < fr < 20000 and 0 < ch < 4000):
                    continue
                pts.append((fr * ch, len(b)))
            if len(pts) < 500:
                continue
            n = len(pts)
            sx = sum(p[0] for p in pts); sy = sum(p[1] for p in pts)
            sxx = sum(p[0] * p[0] for p in pts)
            syy = sum(p[1] * p[1] for p in pts)
            sxy = sum(p[0] * p[1] for p in pts)
            den = math.sqrt(max(1e-9, (n * sxx - sx * sx) * (n * syy - sy * sy)))
            r = (n * sxy - sx * sy) / den
            slope = (n * sxy - sx * sy) / max(1e-9, (n * sxx - sx * sx))
            if best is None or abs(r) > abs(best[0]):
                best = (r, fi, ci, slope, n)
    if best:
        r, fi, ci, slope, n = best
        print("  best pair: frames @+0x%02X, channels @+0x%02X" %
              (4 + fi * 4, 4 + ci * 4))
        print("  r = %.4f over %d files, slope = %.4f bytes per frame-channel"
              % (r, n, slope))
        print("  => %s" % ("FIXED RATE, about %.1f bits per sample"
                           % (slope * 8) if abs(r) > 0.97
                           else "NOT a clean fixed rate -- likely keyframed"))

    # ---- entropy -----------------------------------------------------
    print("\npayload entropy (bits/byte), skipping a 64-byte header guess")
    ents = []
    for name, b in files:
        if len(b) > 256:
            ents.append(entropy(b[64:]))
    ents.sort()
    print("  n=%d  min %.2f  p10 %.2f  median %.2f  p90 %.2f  max %.2f"
          % (len(ents), ents[0], ents[len(ents) // 10],
             ents[len(ents) // 2], ents[9 * len(ents) // 10], ents[-1]))
    print("  => %s" % ("structured, readable" if ents[len(ents) // 2] < 7.0
                       else "densely packed or compressed"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
