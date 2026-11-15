"""probe_fonttex.py -- `.fonttex` is a glyph atlas index. 243 files, no parser.

The `.xls` design spreadsheets name a font per string (`berrylium`, `arial14`,
`courier18`), and these are those fonts' glyph tables:

    0x00  f32  line scale (1.02 on every file)
    0x04  u32  x5 header fields  (size, ?, ?, first codepoint, ?)
    0x18  u32  x5 zero
    0x2c  u32  glyph count N
    0x30  N x {
              u32  codepoint
              u8   x, y          position in the atlas texture
              u8   w, h         size in pixels
              s16  y_offset     baseline bearing, always negative
          }
          u32  range count R
          R  x u32  end index of each contiguous codepoint run, last == N

VARIANT. 242 of the 243 files carry scale 1.02 and the layout above. The one
exception, `default.fonttex`, carries scale **1.00**, starts its glyphs at 0x14
instead of 0x30, and its records are not a fixed 10 bytes -- 43 parse cleanly
and then `M` takes 11, with an extra byte between the glyph box and the
bearing. Its trailing range table is valid and agrees (1 range, end 91 == the
declared count), so the file is intact and it is the record shape that differs.
It is reported as VARIANT rather than FAIL, and it is the one `.fonttex` this
probe does not claim.

DUPLICATES are real. `instructions2.fonttex` and `instructions2_glw.fonttex`
list codepoint 191 twice, at different atlas positions with identical size.
That is in the shipped data, so the ordering check allows equal codepoints and
counts them rather than refusing the file.

Two mistakes are worth recording because both parsed without complaining:

  * reading x as a **u16** instead of two u8s. Glyph 93 of arial14 then has
    x=25922, which no atlas has -- but the early glyphs are at x=0,2,8,19 and
    look perfectly sane, so the file appears to decode until you reach the
    second row of the texture.
  * assuming the file ends at the last glyph. The trailing range table is 4 to
    28 bytes and its final entry equals N, which is the check that catches it.

    python3 03_Tools/research/probe_fonttex.py [--iso PATH] [--dump NAME]
"""

import argparse
import collections
import json
import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from rdrstudio.iso9660 import Iso9660          # noqa: E402
from rdrstudio import dave                     # noqa: E402

REPO = os.path.dirname(os.path.dirname(HERE))
VANILLA = os.path.join(REPO, "01_Vanilla", "RDR_VANILLA.iso")
HEADER = 0x30
REC = 10


class BadFont(Exception):
    pass


class Variant(Exception):
    """A real .fonttex this probe's layout does not cover -- not a failure."""


def parse(data):
    if len(data) < HEADER + REC:
        raise BadFont("shorter than one glyph")
    scale, = struct.unpack_from("<f", data, 0)
    if abs(scale - 1.02) > 0.005:
        raise Variant("scale %.4f, not the 1.02 layout" % scale)
    count, = struct.unpack_from("<I", data, 0x2C)
    if not 0 < count < 4096:
        raise BadFont("implausible glyph count %d" % count)
    end = HEADER + count * REC
    if end + 4 > len(data):
        raise BadFont("glyph table overruns the file")

    glyphs = []
    prev_cp = -1
    dupes = 0
    for i in range(count):
        cp, x, y, w, h, yoff = struct.unpack_from("<IBBBBh", data, HEADER + i * REC)
        if cp < prev_cp:
            raise BadFont("codepoints descend at glyph %d (%d after %d)"
                          % (i, cp, prev_cp))
        if cp == prev_cp:
            dupes += 1
        prev_cp = cp
        glyphs.append((cp, x, y, w, h, yoff))

    nranges, = struct.unpack_from("<I", data, end)
    if not 0 < nranges < 64:
        raise BadFont("implausible range count %d" % nranges)
    want = end + 4 + nranges * 4
    if want != len(data):
        raise BadFont("size %d != header+glyphs+ranges %d" % (len(data), want))
    ranges = list(struct.unpack_from("<%dI" % nranges, data, end + 4))
    if ranges[-1] != count:
        raise BadFont("last range end %d != glyph count %d" % (ranges[-1], count))
    if ranges != sorted(ranges):
        raise BadFont("range ends not ascending: %r" % ranges)
    return {"scale": scale, "count": count, "glyphs": glyphs,
            "ranges": ranges, "duplicate_codepoints": dupes}


def inflate(backing, e):
    raw = backing.read_at(e.offset, e.size_comp)
    return raw if not e.compressed else zlib.decompress(raw, -15)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso", default=VANILLA)
    ap.add_argument("--dump", default="")
    ap.add_argument("--json", default=os.path.join(REPO, "07_Research", "fonttex.json"))
    args = ap.parse_args()

    iso = Iso9660(args.iso)
    ok, fails, rows, variants = 0, [], [], []
    total = 0
    seen_fonts = collections.Counter()
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b"Dave", b"DAVE"):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            if e.ext != "fonttex":
                continue
            total += 1
            base = e.name.rsplit("/", 1)[-1]
            try:
                blob = inflate(d.backing, e)
                info = parse(blob)
            except Variant as ex:
                variants.append({"name": base, "why": str(ex), "size": len(blob)})
                continue
            except (BadFont, zlib.error, struct.error) as ex:
                fails.append({"name": base, "why": str(ex)})
                continue
            ok += 1
            seen_fonts[base] += 1
            g = info["glyphs"]
            rows.append({
                "name": base, "size": len(blob), "scale": info["scale"],
                "glyphs": info["count"], "ranges": len(info["ranges"]),
                "cp_lo": g[0][0], "cp_hi": g[-1][0],
                "max_x": max(q[1] + q[3] for q in g),
                "max_y": max(q[2] + q[4] for q in g),
                "yoff": (min(q[5] for q in g), max(q[5] for q in g)),
                "duplicate_codepoints": info["duplicate_codepoints"],
            })
            if args.dump and base == args.dump:
                for cp, x, y, w, h, yo in g[:20]:
                    print("  U+%04X %-3s x=%-3d y=%-3d %2dx%-2d yoff=%d"
                          % (cp, repr(chr(cp))[1:-1] if 32 <= cp < 127 else "",
                             x, y, w, h, yo))
                print("  ranges:", info["ranges"], "\n")
    iso.close()

    json.dump({"files": total, "parsed": ok, "failed": len(fails),
               "variants": variants, "fails": fails[:20], "rows": rows},
              open(args.json, "w"), indent=1)

    print("%d .fonttex files; %d parse to EOF exactly, %d fail, %d other-layout variant(s)"
          % (total, ok, len(fails), len(variants)))
    for x in fails[:10]:
        print("   FAIL    %-26s %s" % (x["name"], x["why"]))
    for x in variants:
        print("   VARIANT %-26s %s (%d bytes, not claimed)"
              % (x["name"], x["why"], x["size"]))
    dupes = sum(r["duplicate_codepoints"] for r in rows)
    if dupes:
        print("   %d duplicate codepoint(s) in the shipped data, in %d file(s)"
              % (dupes, sum(1 for r in rows if r["duplicate_codepoints"])))
    if rows:
        print("\n%d distinct font names; atlas extent x<=%d y<=%d across the corpus"
              % (len(seen_fonts),
                 max(r["max_x"] for r in rows), max(r["max_y"] for r in rows)))
        print("glyph counts %d..%d, codepoints U+%04X..U+%04X"
              % (min(r["glyphs"] for r in rows), max(r["glyphs"] for r in rows),
                 min(r["cp_lo"] for r in rows), max(r["cp_hi"] for r in rows)))
        print("\n%-26s %6s %7s %7s %9s %s" % (
            "FONT", "BYTES", "GLYPHS", "RANGES", "ATLAS", "CODEPOINTS"))
        for r in sorted(rows, key=lambda r: -r["glyphs"])[:10]:
            print("%-26s %6d %7d %7d %4dx%-4d U+%04X..U+%04X" % (
                r["name"], r["size"], r["glyphs"], r["ranges"],
                r["max_x"], r["max_y"], r["cp_lo"], r["cp_hi"]))
    print("-> %s" % args.json)


if __name__ == "__main__":
    main()
