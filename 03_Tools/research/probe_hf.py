"""probe_hf.py -- `.hf` is a terrain heightfield, and the size law proves it.

67 files, 1,788 B to 262,168 B, and nothing in `03_Tools/rdrstudio/` parsed
them. The header reads:

    u32  width
    u32  height
    f32  min_x, min_z, max_x, max_z      world extent of the patch
    f32  height[width * height]          row-major

The claim is falsifiable in one line and that is the point of it:

    file size == 24 + width * height * 4

A wrong field order still satisfies a size check, so the probe also asserts
that every height is a finite float in a sane world range, and that the
declared extent is non-degenerate (max > min on both axes). A heightfield whose
grid decodes as NaN is not a heightfield.

    python3 03_Tools/research/probe_hf.py [--iso PATH] [--csv OUT.csv]
"""

import argparse
import collections
import json
import math
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
HEADER = 24


class BadHF(Exception):
    pass


def parse(data):
    if len(data) < HEADER:
        raise BadHF("shorter than the header")
    w, h = struct.unpack_from("<II", data, 0)
    if not (0 < w <= 4096 and 0 < h <= 4096):
        raise BadHF("implausible grid %dx%d" % (w, h))
    want = HEADER + w * h * 4
    if len(data) != want:
        raise BadHF("size %d != 24 + %d*%d*4 = %d" % (len(data), w, h, want))
    min_x, min_z, max_x, max_z = struct.unpack_from("<4f", data, 8)
    if not (max_x > min_x and max_z > min_z):
        raise BadHF("degenerate extent x[%g,%g] z[%g,%g]"
                    % (min_x, max_x, min_z, max_z))
    heights = struct.unpack_from("<%df" % (w * h), data, HEADER)
    for v in heights:
        if math.isnan(v) or math.isinf(v) or abs(v) > 1e6:
            raise BadHF("height out of range: %r" % v)
    return {"w": w, "h": h, "extent": (min_x, min_z, max_x, max_z),
            "min_height": min(heights), "max_height": max(heights)}


def inflate(backing, e):
    raw = backing.read_at(e.offset, e.size_comp)
    return raw if not e.compressed else zlib.decompress(raw, -15)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso", default=VANILLA)
    ap.add_argument("--csv", default="")
    ap.add_argument("--json", default=os.path.join(REPO, "07_Research", "hf.json"))
    args = ap.parse_args()

    iso = Iso9660(args.iso)
    ok, fails, rows = 0, [], []
    grids = collections.Counter()
    total = 0
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b"Dave", b"DAVE"):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            if e.ext != "hf":
                continue
            total += 1
            try:
                blob = inflate(d.backing, e)
            except zlib.error:
                fails.append({"archive": f.path, "name": e.name, "why": "inflate"})
                continue
            try:
                info = parse(blob)
            except BadHF as ex:
                fails.append({"archive": f.path, "name": e.name,
                              "why": str(ex), "size": len(blob)})
                continue
            ok += 1
            grids["%dx%d" % (info["w"], info["h"])] += 1
            rows.append(dict(archive=f.path, name=e.name, size=len(blob), **info))
    iso.close()

    json.dump({"files": total, "parsed": ok, "failed": len(fails),
               "fails": fails[:20], "grids": grids.most_common(),
               "rows": rows}, open(args.json, "w"), indent=1)

    print("%d .hf files; %d satisfy  size == 24 + w*h*4  with a sane grid, %d fail"
          % (total, ok, len(fails)))
    for x in fails[:10]:
        print("   FAIL %-28s %s" % (x["name"], x["why"]))
    print("\ngrid sizes in use:")
    for g, c in grids.most_common(12):
        print("   %-12s %d" % (g, c))
    if rows:
        lo = min(r["min_height"] for r in rows)
        hi = max(r["max_height"] for r in rows)
        print("\nheight range across the corpus: %.2f .. %.2f" % (lo, hi))
        print("\n%-28s %-10s %-28s %s" % ("FILE", "GRID", "EXTENT x/z", "HEIGHT"))
        for r in sorted(rows, key=lambda r: -r["size"])[:8]:
            ex = r["extent"]
            print("%-28s %-10s [%7.1f,%7.1f]/[%7.1f,%7.1f] %6.1f..%.1f" % (
                r["name"][:28], "%dx%d" % (r["w"], r["h"]),
                ex[0], ex[2], ex[1], ex[3], r["min_height"], r["max_height"]))
    if args.csv and rows:
        with open(args.csv, "w") as fh:
            fh.write("archive,name,w,h,min_x,min_z,max_x,max_z,min_height,max_height\n")
            for r in rows:
                fh.write("%s,%s,%d,%d,%g,%g,%g,%g,%g,%g\n" % (
                    r["archive"], r["name"], r["w"], r["h"], *r["extent"],
                    r["min_height"], r["max_height"]))
        print("\n-> %s" % args.csv)
    print("-> %s" % args.json)


if __name__ == "__main__":
    main()
