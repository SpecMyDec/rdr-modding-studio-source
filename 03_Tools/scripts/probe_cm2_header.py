"""Is the cm2 header 20 bytes with a u32 at +0x10, or 18 bytes with two u8s?

Read as a u32, the field at +0x10 takes values like 8, 9, 256, 264, 1033, 2568,
13833 ... and also 4084410377, 2787137032, 949470217. A field cannot plausibly
be both a tiny flag word and a number near 2^32.

But every one of the small values has the same shape in hex -- 0x0008, 0x0009,
0x0100, 0x0108, 0x0409, 0x0A08, 0x3609 -- i.e. low nibble in {0,1,8,9} and bits
4..7 clear. That is what you see when a u8 flags byte is followed by a u8
counter and the two bytes above it are actually the start of the payload.

So: header is 18 bytes, `+0x10 = u8`, `+0x11 = u8`. If true, `data[0x10]` should
take only a handful of values across the whole disc, and the earlier "float-
looking u32" disappears because those bytes were never part of the header.

Also measures bits-per-sample grouped by `data[0x10]`, because if that byte is
a bit-width or a mode selector the groups should separate cleanly.
"""
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdrstudio.game import Game                      # noqa: E402
from rdrstudio import config                          # noqa: E402

# REQ-035/036. Was a literal path to the WORKING ISO on one machine:
# unrunnable on anyone else's checkout, and pointed at the disc the live
# tests mutate. What this examines is a property of the SHIPPED data, so
# it reads the stock disc. Read-only throughout, which is what makes
# vanilla the right answer here rather than merely the tidier one.
DISC = Path(config.VANILLA_ISO)
LIMIT = 4000


def main():
    game = Game(DISC, writable=False)
    print("collecting distinct .anim files (target %d)..." % LIMIT)
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
                data = game.read(arc, e.name)
            except Exception:
                continue
            if data[:4] not in (b"cm2\x00", b"cmp\x00") or len(data) < 24:
                continue
            frames, channels, f0c = struct.unpack_from("<III", data, 4)
            if not channels or not frames:
                continue
            rows.append(dict(name=e.name, data=data, size=len(data),
                             frames=frames, ch=channels, f0c=f0c,
                             b10=data[0x10], b11=data[0x11],
                             b12=data[0x12], b13=data[0x13]))
            if len(rows) >= LIMIT:
                break
        if len(rows) >= LIMIT:
            break
    print("%d files\n" % len(rows))

    print("=" * 70)
    print("byte at +0x10")
    print("=" * 70)
    c = Counter(r["b10"] for r in rows)
    for v, n in c.most_common(20):
        print("  0x%02X (%3d)  %5d files  %5.1f%%   bits set: %s"
              % (v, v, n, 100.0 * n / len(rows), bin(v)[2:].rjust(8, "0")))
    print("  DISTINCT VALUES: %d" % len(c))

    print("\n" + "=" * 70)
    print("byte at +0x11")
    print("=" * 70)
    c11 = Counter(r["b11"] for r in rows)
    print("  distinct: %d, range %d..%d"
          % (len(c11), min(c11), max(c11)))
    for v, n in c11.most_common(12):
        print("    0x%02X (%3d)  %5d files" % (v, v, n))

    print("\n" + "=" * 70)
    print("bytes at +0x12 / +0x13 -- header, or first payload bytes?")
    print("=" * 70)
    z12 = sum(1 for r in rows if r["b12"] == 0)
    z13 = sum(1 for r in rows if r["b13"] == 0)
    print("  +0x12 == 0 in %d/%d (%.1f%%)" % (z12, len(rows), 100.0*z12/len(rows)))
    print("  +0x13 == 0 in %d/%d (%.1f%%)" % (z13, len(rows), 100.0*z13/len(rows)))
    print("  (if these were header padding they would be 0 everywhere)")

    print("\n" + "=" * 70)
    print("does +0x11 predict anything? (vs channels, bones, frames)")
    print("=" * 70)
    for label, fn in (("channels", lambda r: r["ch"]),
                      ("bones", lambda r: r["ch"] // 3),
                      ("frames", lambda r: r["frames"])):
        eq = sum(1 for r in rows if r["b11"] == fn(r))
        print("  +0x11 == %-9s : %d/%d" % (label, eq, len(rows)))
    # +0x11 vs size
    print("\n  +0x11 vs payload size, sampled:")
    for r in sorted(rows, key=lambda x: x["b11"])[:4] + \
             sorted(rows, key=lambda x: -x["b11"])[:4]:
        print("    b11=%-4d ch=%-4d frames=%-5d size=%-6d %s"
              % (r["b11"], r["ch"], r["frames"], r["size"], r["name"][:40]))

    print("\n" + "=" * 70)
    print("bits per sample, grouped by +0x10")
    print("=" * 70)
    byb = defaultdict(list)
    for r in rows:
        for hdr in (18,):
            pay = r["size"] - hdr
            if pay <= 0:
                continue
            bps = pay * 8.0 / (r["frames"] * r["ch"])
            byb[r["b10"]].append(bps)
    for v in sorted(byb, key=lambda k: -len(byb[k]))[:10]:
        vals = sorted(byb[v])
        n = len(vals)
        print("  +0x10=0x%02X n=%-5d  bits/sample  min %.2f  p25 %.2f  med %.2f  "
              "p75 %.2f  max %.2f"
              % (v, n, vals[0], vals[n // 4], vals[n // 2],
                 vals[3 * n // 4], vals[-1]))

    game.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
