"""probe_inventory.py -- walk the whole disc and count every distinct file type.

Read-only. Opens the VANILLA ISO (never the working copy), walks the ISO9660
tree, opens every Dave/DAVE archive it finds, and tallies every entry by
extension: count, size range, how many are compressed, and the set of leading
magic bytes actually observed.

    python3 03_Tools/research/probe_inventory.py [--iso PATH] [--json OUT]

Output is a JSON document on --json plus a human table on stdout. Nothing is
written to the ISO, so this needs no lock.
"""

import argparse
import collections
import json
import os
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))          # 03_Tools
from rdrstudio.iso9660 import Iso9660              # noqa: E402
from rdrstudio import dave                         # noqa: E402

REPO = os.path.dirname(os.path.dirname(HERE))
VANILLA = os.path.join(REPO, "01_Vanilla", "RDR_VANILLA.iso")


def printable(b):
    return "".join(chr(c) if 32 <= c < 127 else "." for c in b)


def head_of(entry, backing, n):
    """First n bytes of an entry's DECOMPRESSED payload.

    Everything in a Dave archive may be raw-deflate compressed, so reading the
    stored bytes gives you the deflate header, not the format's magic. We pull
    a small compressed prefix and inflate just that -- a partial stream is
    expected to end early, which decompressobj tolerates.
    """
    want = entry.size_comp if not entry.compressed else min(entry.size_comp, 4096)
    raw = backing.read_at(entry.offset, min(want, max(n, 256)) if not entry.compressed else want)
    if not entry.compressed:
        return raw[:n]
    try:
        return zlib.decompressobj(-15).decompress(raw, n)[:n]
    except zlib.error:
        return b""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso", default=VANILLA)
    ap.add_argument("--json", default=os.path.join(REPO, "07_Research", "inventory.json"))
    ap.add_argument("--magic-bytes", type=int, default=8)
    args = ap.parse_args()

    iso = Iso9660(args.iso)                        # read-only
    report = {
        "iso": args.iso,
        "volume_id": iso.volume_id,
        "total_sectors": iso.total_sectors,
        "loose_files": [],
        "archives": {},
        "by_ext": {},
    }

    ext_stat = collections.defaultdict(lambda: {
        "count": 0, "min": None, "max": None, "total": 0,
        "compressed": 0, "magics": collections.Counter(),
        "archives": set(), "loose": 0,
    })

    def tally(ext, size, comp, magic, where, loose=False):
        s = ext_stat[ext]
        s["count"] += 1
        s["total"] += size
        s["min"] = size if s["min"] is None else min(s["min"], size)
        s["max"] = size if s["max"] is None else max(s["max"], size)
        if comp:
            s["compressed"] += 1
        s["magics"][magic] += 1
        s["archives"].add(where)
        if loose:
            s["loose"] += 1

    for f in iso.files():
        path = f.path.lstrip("/")
        ext = path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else "(none)"
        head = iso.read_at(f.offset, min(args.magic_bytes, f.size)) if f.size else b""
        report["loose_files"].append(
            {"path": path, "size": f.size, "lba": f.lba, "magic": printable(head)}
        )
        tally("::loose::" + ext, f.size, False, printable(head), "(iso root)", loose=True)

        if head[:4] in (b"Dave", b"DAVE"):
            try:
                d = dave.Dave(dave.IsoBacking(iso, f.path), label=path)
            except Exception as exc:                      # noqa: BLE001
                report["archives"][path] = {"error": str(exc)}
                continue
            arch = {
                "magic": d.magic, "count": d.count, "size": d.size,
                "info_size": d.info_size, "name_size": d.name_size,
                "ext_counts": collections.Counter(),
            }
            for e in d.entries:
                ext2 = e.ext or "(none)"
                arch["ext_counts"][ext2] += 1
                try:
                    m = printable(head_of(e, d.backing, args.magic_bytes))
                except Exception:                          # noqa: BLE001
                    m = "<unreadable>"
                tally(ext2, e.size_full, e.compressed, m, path)
            arch["ext_counts"] = dict(arch["ext_counts"])
            report["archives"][path] = arch

    for ext, s in ext_stat.items():
        report["by_ext"][ext] = {
            "count": s["count"], "min": s["min"], "max": s["max"],
            "total": s["total"], "compressed": s["compressed"],
            "loose": s["loose"], "archive_count": len(s["archives"]),
            "magics": s["magics"].most_common(8),
        }

    with open(args.json, "w") as fh:
        json.dump(report, fh, indent=1)

    rows = sorted(report["by_ext"].items(), key=lambda kv: -kv[1]["count"])
    print("%-14s %7s %10s %10s %6s  %s" % ("EXT", "COUNT", "MIN", "MAX", "COMP", "TOP MAGIC"))
    for ext, s in rows:
        top = s["magics"][0][0] if s["magics"] else ""
        print("%-14s %7d %10s %10s %6d  %r" % (
            ext, s["count"], s["min"], s["max"], s["compressed"], top))
    print("\narchives: %d   loose files: %d   -> %s"
          % (len(report["archives"]), len(report["loose_files"]), args.json))
    iso.close()


if __name__ == "__main__":
    main()
