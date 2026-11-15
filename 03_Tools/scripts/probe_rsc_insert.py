#!/usr/bin/env python3
"""What an RSC0 chunk-insert has to preserve. M18's measurement, read-only.

    python3 scripts/probe_rsc_insert.py

REQ-055 settled the two facts needed to *read* the container: the table stores
chunk ENDS, not starts, and `resourceIndex` is 1-based. Neither says what a
WRITE has to keep true. Inserting a chunk grows the table by 8 bytes, and the
table lives inside `header_size` -- so the first question is what is in the
space between the end of the table and `header_size`, because the new entry is
about to land on it.

Assuming that space is dead is the kind of guess that corrupts files that are
not the 515 shaped like the one you tested. This probe asks the disc instead.

Measured here, each one a thing the inserter is allowed to rely on only if it
comes back clean:

  1. header slack      -- is (12 + 8*count .. header_size) all zero bytes?
  2. room to append    -- does header_size already fit one MORE entry, or does
                          appending force header_size to grow and every chunk
                          to move?
  3. header_size       -- its distribution, because it is NOT constant
  4. end == file size  -- does the last table entry equal the file length?
  5. alignment         -- are chunk ends 16-byte aligned, and is the file?
  6. payload slack     -- is there padding INSIDE a chunk's recorded extent,
                          i.e. does a chunk's real content stop before its end?

Read-only. Takes no lock. Reads `01_Vanilla/RDR_VANILLA.iso` and writes
nothing.
"""
import collections
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from rdrstudio import dave                        # noqa: E402
from rdrstudio.iso9660 import Iso9660             # noqa: E402

ISO = os.path.join(os.path.dirname(__file__), '..', '..',
                   '01_Vanilla', 'RDR_VANILLA.iso')

MAGIC = b'RSC0'


def collect(iso):
    """name -> bytes for every distinct .rsc on the disc."""
    out = {}
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b'Dave', b'DAVE'):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            if not e.name.lower().endswith('.rsc') or e.name in out:
                continue
            raw = d.backing.read_at(e.offset, e.size_comp)
            try:
                b = zlib.decompress(raw, -15) if e.compressed else raw
            except zlib.error:
                continue
            if b[:4] == MAGIC:
                out[e.name] = b
    return out


def main():
    iso = Iso9660(ISO)
    try:
        files = collect(iso)
    finally:
        iso.close()

    n = len(files)
    hsizes = collections.Counter()
    slack_zero = slack_total = 0
    slack_dirty = []
    room = no_room = 0
    end_is_len = align_ends = align_len = 0
    tabled = 0
    payload_slack = collections.Counter()
    trailing = []

    for name, b in sorted(files.items()):
        count, hsize = struct.unpack_from('<II', b, 4)
        hsizes[hsize] += 1
        if count == 0:
            continue
        tabled += 1
        ents = [struct.unpack_from('<4sI', b, 12 + 8 * i) for i in range(count)]
        offs = [o for _, o in ents]

        # 1. header slack
        table_end = 12 + 8 * count
        slack = b[table_end:hsize]
        slack_total += 1
        if slack.count(0) == len(slack):
            slack_zero += 1
        else:
            slack_dirty.append((name, table_end, hsize,
                                slack[:16].hex()))

        # 2. room for one more 8-byte entry without moving any chunk
        if table_end + 8 <= hsize:
            room += 1
        else:
            no_room += 1

        # 4 / 5
        if offs[-1] == len(b):
            end_is_len += 1
        if all(o % 16 == 0 for o in offs):
            align_ends += 1
        if len(b) % 16 == 0:
            align_len += 1

        # 6. padding inside a chunk's recorded extent
        starts = [hsize] + offs[:-1]
        for (tag, end), start in zip(ents, starts):
            body = b[start:end]
            stripped = len(body.rstrip(b'\x00'))
            pad = len(body) - stripped
            payload_slack[min(pad, 16)] += 1
        last_tag, last_end = ents[-1]
        if last_end < len(b):
            trailing.append((name, len(b) - last_end))

    print("RSC0 files on the disc (distinct by name): %d, with a table: %d"
          % (n, tabled))
    print()
    print("1. header slack (12+8*count .. header_size) is ALL ZERO: %d / %d"
          % (slack_zero, slack_total))
    for row in slack_dirty[:10]:
        print("     dirty: %s  table_end=%d header_size=%d  %s" % row)
    print()
    print("2. header_size already fits one MORE entry: %d / %d"
          % (room, tabled))
    print("   appending would force the header to grow:  %d / %d"
          % (no_room, tabled))
    print()
    print("3. header_size distribution:")
    for hs, c in sorted(hsizes.items()):
        print("     %6d  x %d" % (hs, c))
    print()
    print("4. last table entry == file length:   %d / %d" % (end_is_len, tabled))
    print("5. every end 16-byte aligned:         %d / %d" % (align_ends, tabled))
    print("   file length 16-byte aligned:       %d / %d" % (align_len, tabled))
    if trailing:
        print("   files with bytes past the last end: %d (e.g. %s +%d)"
              % (len(trailing), trailing[0][0], trailing[0][1]))
    print()
    print("6. trailing zero bytes inside a chunk's extent (capped at 16):")
    for pad in sorted(payload_slack):
        print("     %2d%s  x %d" % (pad, "+" if pad == 16 else " ",
                                    payload_slack[pad]))


if __name__ == '__main__':
    main()
