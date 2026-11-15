"""probe_xls_strtbl.py -- the spreadsheets are the missing symbol table for `.strtbl`.

`.strtbl` addresses each record by a PJW/ELF hash of its identifier
(`rdrstudio/strtbl.py:name_hash`), and each file carries its OWN key table of
plaintext identifiers, so `StringTable.name_of()` already names most records.
The first cut of this probe assumed the names were missing entirely. They are
not, and the claim had to be narrowed to the one that is actually open:

    how many records can the tool NOT name today, and does hashing the
    identifier column of the 38 `.xls` design spreadsheets fill that gap?

A record goes unnamed when its id is not in its own file's key table -- a
string shared from somewhere else, or one whose key was dropped when the table
was built. The spreadsheets are a second, independent source of identifiers,
so the measurement is: unnamed records before, unnamed records after.

    python3 03_Tools/research/probe_xls_strtbl.py [--iso PATH] [--out MAP.csv]
"""

import argparse
import collections
import json
import os
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
from rdrstudio.iso9660 import Iso9660          # noqa: E402
from rdrstudio import dave, strtbl             # noqa: E402
from probe_xls import ole_streams, parse_workbook, BadXLS   # noqa: E402

REPO = os.path.dirname(os.path.dirname(HERE))
VANILLA = os.path.join(REPO, "01_Vanilla", "RDR_VANILLA.iso")


def inflate(backing, e):
    raw = backing.read_at(e.offset, e.size_comp)
    return raw if not e.compressed else zlib.decompress(raw, -15)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso", default=VANILLA)
    ap.add_argument("--out", default="")
    ap.add_argument("--json", default=os.path.join(REPO, "07_Research", "xls_strtbl.json"))
    args = ap.parse_args()

    iso = Iso9660(args.iso)

    names = {}          # hash -> identifier
    dup = 0
    n_xls = 0
    sheet_rows = 0
    tables = 0
    ids = collections.Counter()
    named_today = collections.Counter()
    unnamed_today = collections.Counter()
    sample = []

    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b"Dave", b"DAVE"):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            if e.ext not in ("xls", "strtbl"):
                continue
            try:
                blob = inflate(d.backing, e)
            except zlib.error:
                continue

            if e.ext == "xls":
                try:
                    streams = ole_streams(blob)
                    wb = streams.get("Workbook") or streams.get("Book")
                    if wb is None:
                        continue
                    _sheets, _sst, cells, _bad = parse_workbook(wb)
                except (BadXLS, Exception):        # noqa: BLE001
                    continue
                n_xls += 1
                for (r, c), v in cells.items():
                    if c != 0 or r == 0 or not isinstance(v, str) or not v:
                        continue
                    sheet_rows += 1
                    h = strtbl.name_hash(v)
                    if h in names and names[h] != v:
                        dup += 1
                    names[h] = v
            else:
                try:
                    st = strtbl.parse(blob)
                except Exception:                  # noqa: BLE001
                    continue
                tables += 1
                for _slot, recs in st.blocks.items():
                    for rec in recs:
                        ids[rec.id] += 1
                        if st.name_of(rec) is not None:
                            named_today[rec.id] += 1
                        else:
                            unnamed_today[rec.id] += 1
                            if len(sample) < 12 and rec.text:
                                sample.append((rec.id, rec.text))
    iso.close()

    all_records = sum(ids.values())
    unnamed = sum(unnamed_today.values())
    gained_ids = [i for i in unnamed_today if i in names]
    gained = sum(unnamed_today[i] for i in gained_ids)
    unmatched_names = [n for h, n in names.items() if h not in ids]
    confirms = sum(1 for i in named_today if i in names)

    report = {
        "workbooks": n_xls, "identifier_cells": sheet_rows,
        "distinct_names": len(names), "hash_collisions_seen": dup,
        "strtbl_files": tables, "distinct_ids": len(ids),
        "record_instances": all_records,
        "unnamed_by_tool": unnamed,
        "unnamed_distinct": len(unnamed_today),
        "recovered_by_spreadsheets": gained,
        "recovered_distinct": len(gained_ids),
        "ids_the_sheets_confirm": confirms,
        "names_with_no_record": len(unmatched_names),
        "examples": [{"id": i, "name": names[i]} for i in gained_ids[:20]],
    }
    json.dump(report, open(args.json, "w"), indent=1)

    print("%d workbooks -> %d distinct identifiers (%d identifier cells, %d collisions)"
          % (n_xls, len(names), sheet_rows, dup))
    print("%d .strtbl files -> %d distinct record ids, %d record instances\n"
          % (tables, len(ids), all_records))
    print("records the tool cannot name today : %d of %d  (%.1f%%), %d distinct ids"
          % (unnamed, all_records, 100.0 * unnamed / max(all_records, 1),
             len(unnamed_today)))
    print("of those, named by the spreadsheets : %d  (%d distinct ids)"
          % (gained, len(gained_ids)))
    print("ids the sheets independently CONFIRM: %d" % confirms)
    print("spreadsheet names with no record on disc: %d" % len(unmatched_names))
    if gained_ids:
        print("\nnewly resolved:")
        for i in gained_ids[:12]:
            print("   0x%08x  %s" % (i, names[i]))
    else:
        print("\nNo record gains a name it did not already have.")
        print("Unnamed examples, for whoever wants to chase them:")
        for i, t in sample[:6]:
            print("   0x%08x  %r" % (i, t[:44]))

    if args.out:
        with open(args.out, "w") as fh:
            fh.write("# .strtbl record id -> identifier, recovered by hashing the\n"
                     "# identifier column of the 38 .xls design spreadsheets with\n"
                     "# rdrstudio/strtbl.py:name_hash (PJW/ELF).\n"
                     "# Regenerate:\n"
                     "#   python3 03_Tools/research/probe_xls_strtbl.py --out %s\n"
                     "id,identifier\n" % os.path.basename(args.out))
            for h in sorted(names):
                if h in ids:
                    fh.write("0x%08x,%s\n" % (h, names[h]))
        print("\n-> %s" % args.out)
    print("-> %s" % args.json)


if __name__ == "__main__":
    main()
