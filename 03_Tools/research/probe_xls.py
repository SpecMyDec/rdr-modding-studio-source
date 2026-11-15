"""probe_xls.py -- the 38 `.xls` on the disc are real Excel workbooks.

`D0 CF 11 E0 A1 B1 1A E1` on all 38 files: OLE2 / CFBF, the Microsoft compound
file. Inside each is a `Workbook` stream of BIFF8 records. These are the
DESIGN SPREADSHEETS -- shipped, by accident or convenience, on the retail disc.

What they contain is the authoring source for the game's string tables:

    identifier | font | language | string | scale x | scale y | offset x |
    offset y | flags

`swat.xls` alone holds 10,775 cells. That is the same data `.strtbl` carries in
binary, plus the per-string font, scale, offset and flag columns.

Everything here is stdlib. `olefile`/`xlrd` are not installed and adding a
dependency to read 38 files is the wrong trade, so the CFBF container and the
BIFF8 records are parsed directly.

THE FIDDLY PART, and the reason an earlier cut of this probe returned '?' for
854 strings: BIFF8's shared string table (SST) is split across CONTINUE
records, and a string may straddle the boundary. The first byte of each
CONTINUE is a fresh encoding flag for the remainder of that string -- so a
16-bit string can continue as 8-bit and vice versa. Concatenating the payloads
and parsing straight through gets the first few hundred strings right and then
walks off the end, which looks like a corrupt file and is not.

    python3 03_Tools/research/probe_xls.py [--iso PATH] [--dump NAME.xls]
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
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ENDOFCHAIN = 0xFFFFFFFE
FREESECT = 0xFFFFFFFF


class BadXLS(Exception):
    pass


# ------------------------------------------------------------------ CFBF

def ole_streams(d):
    if d[:8] != OLE_MAGIC:
        raise BadXLS("not an OLE2 compound file")
    ssz = 1 << struct.unpack_from("<H", d, 30)[0]
    msz = 1 << struct.unpack_from("<H", d, 32)[0]
    nfat = struct.unpack_from("<I", d, 44)[0]
    dir_start = struct.unpack_from("<I", d, 48)[0]
    cutoff = struct.unpack_from("<I", d, 56)[0]
    mini_start = struct.unpack_from("<I", d, 60)[0]
    difat_start = struct.unpack_from("<I", d, 68)[0]
    ndifat = struct.unpack_from("<I", d, 72)[0]

    def sector(n):
        off = 512 + n * ssz
        return d[off:off + ssz]

    # DIFAT: 109 entries in the header, the rest chained through its own sectors
    difat = [struct.unpack_from("<I", d, 76 + i * 4)[0] for i in range(109)]
    nxt = difat_start
    for _ in range(ndifat):
        if nxt >= 0xFFFFFFF0:
            break
        blk = sector(nxt)
        difat += list(struct.unpack("<%dI" % (ssz // 4 - 1), blk[:-4]))
        nxt, = struct.unpack_from("<I", blk, ssz - 4)
    difat = [x for x in difat if x < 0xFFFFFFF0][:nfat]

    fat = []
    for s in difat:
        fat += list(struct.unpack("<%dI" % (ssz // 4), sector(s)))

    def chain(start, table):
        out, n, seen = [], start, set()
        while n < 0xFFFFFFF0:
            if n in seen or n >= len(table):
                break
            seen.add(n)
            out.append(n)
            n = table[n]
        return out

    dir_data = b"".join(sector(s) for s in chain(dir_start, fat))

    entries = []
    for i in range(len(dir_data) // 128):
        e = dir_data[i * 128:(i + 1) * 128]
        nlen = struct.unpack_from("<H", e, 64)[0]
        if nlen < 2:
            continue
        name = e[:nlen - 2].decode("utf-16le", "replace")
        entries.append((name, e[66],
                        struct.unpack_from("<I", e, 116)[0],
                        struct.unpack_from("<Q", e, 120)[0]))

    # The mini stream lives in the root entry and is indexed by its own FAT.
    mini_fat, mini_data = [], b""
    root = next((x for x in entries if x[1] == 5), None)
    if root and mini_start < 0xFFFFFFF0:
        mini_fat = []
        for s in chain(mini_start, fat):
            mini_fat += list(struct.unpack("<%dI" % (ssz // 4), sector(s)))
        mini_data = b"".join(sector(s) for s in chain(root[2], fat))

    out = {}
    for name, typ, start, size in entries:
        if typ != 2:
            continue
        if size < cutoff and mini_data:
            blob = b"".join(mini_data[n * msz:(n + 1) * msz]
                            for n in chain(start, mini_fat))
        else:
            blob = b"".join(sector(s) for s in chain(start, fat))
        out[name] = blob[:size]
    return out


# ------------------------------------------------------------------ BIFF8

def records(stream):
    out, p = [], 0
    while p + 4 <= len(stream):
        rid, ln = struct.unpack_from("<HH", stream, p)
        p += 4
        out.append((rid, stream[p:p + ln]))
        p += ln
    return out


def read_sst(recs, i):
    """Shared strings, honouring the CONTINUE encoding-flag reset."""
    parts = [recs[i][1]]
    for rid, data in recs[i + 1:]:
        if rid != 0x003C:            # CONTINUE
            break
        parts.append(data)
    buf = b"".join(parts)
    # byte offset where each continuation begins, so a straddling string can
    # pick up the fresh flag byte that sits there
    bounds = set()
    at = 0
    for k, part in enumerate(parts):
        if k:
            bounds.add(at)
        at += len(part)

    total, uniq = struct.unpack_from("<II", buf, 0)
    p = 8
    strings = []
    for _ in range(uniq):
        if p + 3 > len(buf):
            break
        nchar, = struct.unpack_from("<H", buf, p)
        flags = buf[p + 2]
        p += 3
        rich = ext = 0
        if flags & 0x08:
            rich, = struct.unpack_from("<H", buf, p); p += 2
        if flags & 0x04:
            ext, = struct.unpack_from("<I", buf, p); p += 4
        wide = flags & 0x01
        chars = []
        left = nchar
        while left > 0:
            if p in bounds:                  # a CONTINUE starts here
                wide = buf[p] & 0x01
                p += 1
            take = left
            if wide:
                nxt = min((b for b in bounds if b > p), default=len(buf))
                take = min(left, (nxt - p) // 2)
                chars.append(buf[p:p + take * 2].decode("utf-16le", "replace"))
                p += take * 2
            else:
                nxt = min((b for b in bounds if b > p), default=len(buf))
                take = min(left, nxt - p)
                chars.append(buf[p:p + take].decode("latin-1"))
                p += take
            if take <= 0:
                break
            left -= take
        p += rich * 4 + ext
        strings.append("".join(chars))
    return strings, total, uniq


def rk_value(v):
    is_int = v & 2
    div100 = v & 1
    v &= 0xFFFFFFFC
    if is_int:
        n = struct.unpack("<i", struct.pack("<I", v))[0] >> 2
    else:
        n = struct.unpack("<d", struct.pack("<Q", v << 32))[0]
    return n / 100.0 if div100 else n


def short_string(b, off):
    ln = b[off]
    flags = b[off + 1]
    if flags & 1:
        return b[off + 2:off + 2 + ln * 2].decode("utf-16le", "replace")
    return b[off + 2:off + 2 + ln].decode("latin-1")


def parse_workbook(wb):
    recs = records(wb)
    sst, sheets = [], []
    for i, (rid, data) in enumerate(recs):
        if rid == 0x00FC and not sst:
            sst, _t, _u = read_sst(recs, i)
        elif rid == 0x0085:                 # BOUNDSHEET
            sheets.append(short_string(data, 6))
    cells = {}
    unresolved = 0
    for rid, data in recs:
        try:
            if rid == 0x00FD:               # LABELSST
                r, c, _xf, ix = struct.unpack_from("<HHHI", data, 0)
                if ix < len(sst):
                    cells[(r, c)] = sst[ix]
                else:
                    unresolved += 1
            elif rid == 0x0203:             # NUMBER
                r, c, _xf = struct.unpack_from("<HHH", data, 0)
                cells[(r, c)], = struct.unpack_from("<d", data, 6)
            elif rid == 0x027E:             # RK
                r, c, _xf, v = struct.unpack_from("<HHHI", data, 0)
                cells[(r, c)] = rk_value(v)
            elif rid == 0x00BD:             # MULRK
                r, c0 = struct.unpack_from("<HH", data, 0)
                for k in range((len(data) - 6) // 6):
                    _xf, v = struct.unpack_from("<HI", data, 4 + k * 6)
                    cells[(r, c0 + k)] = rk_value(v)
            elif rid == 0x0204:             # LABEL
                r, c, _xf = struct.unpack_from("<HHH", data, 0)
                cells[(r, c)] = short_string(data, 6)
        except struct.error:
            unresolved += 1
    return sheets, sst, cells, unresolved


def inflate(backing, e):
    raw = backing.read_at(e.offset, e.size_comp)
    return raw if not e.compressed else zlib.decompress(raw, -15)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso", default=VANILLA)
    ap.add_argument("--dump", default="", help="print the grid of this workbook")
    ap.add_argument("--rows", type=int, default=12)
    ap.add_argument("--json", default=os.path.join(REPO, "07_Research", "xls.json"))
    args = ap.parse_args()

    iso = Iso9660(args.iso)
    rows, fails = [], []
    total = 0
    headers = collections.Counter()
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b"Dave", b"DAVE"):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            if e.ext != "xls":
                continue
            total += 1
            base = e.name.rsplit("/", 1)[-1]
            try:
                blob = inflate(d.backing, e)
                streams = ole_streams(blob)
                wb = streams.get("Workbook") or streams.get("Book")
                if wb is None:
                    raise BadXLS("no Workbook stream, streams=%s" % list(streams))
                sheets, sst, cells, unresolved = parse_workbook(wb)
            except (BadXLS, zlib.error, struct.error) as ex:
                fails.append({"name": base, "why": str(ex)})
                continue
            hdr = tuple(str(cells.get((0, c), "")) for c in range(9))
            if any(hdr):
                headers[hdr] += 1
            rows.append({"name": base, "archive": f.path, "size": len(blob),
                         "streams": sorted(streams), "sheets": sheets,
                         "sst": len(sst), "cells": len(cells),
                         "unresolved": unresolved,
                         "max_row": max((r for r, _ in cells), default=-1) + 1,
                         "max_col": max((c for _, c in cells), default=-1) + 1})
            if args.dump and base == args.dump:
                ordered = sorted({r for r, _ in cells})
                for r in ordered[:args.rows]:
                    cs = sorted(c for rr, c in cells if rr == r)
                    print("row%-4d %s" % (r, " | ".join(
                        str(cells[(r, c)])[:22] for c in cs)))
                print()
    iso.close()

    json.dump({"files": total, "parsed": len(rows), "failed": len(fails),
               "fails": fails, "rows": rows}, open(args.json, "w"), indent=1)

    bad = sum(r["unresolved"] for r in rows)
    print("%d .xls files; %d parse as OLE2 + BIFF8, %d fail" % (total, len(rows), len(fails)))
    for x in fails:
        print("   FAIL %-24s %s" % (x["name"], x["why"]))
    print("unresolved cells across the whole corpus: %d" % bad)
    print("\ncolumn headers in use (row 0):")
    for hdr, c in headers.most_common(4):
        print("   x%-3d %s" % (c, " | ".join(h for h in hdr if h)))
    print("\n%-24s %8s %7s %7s %6s  %s" % (
        "FILE", "BYTES", "STRINGS", "CELLS", "ROWSxC", "SHEETS"))
    for r in sorted(rows, key=lambda r: -r["cells"])[:10]:
        print("%-24s %8d %7d %7d %4dx%-3d %s" % (
            r["name"], r["size"], r["sst"], r["cells"],
            r["max_row"], r["max_col"], ",".join(r["sheets"])))
    print("\ntotal cells recovered: %d" % sum(r["cells"] for r in rows))
    print("-> %s" % args.json)


if __name__ == "__main__":
    main()
