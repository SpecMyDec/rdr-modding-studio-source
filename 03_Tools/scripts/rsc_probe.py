"""Decode the RSC0 resource bundle.

Header seen in ani_lods.rsc:
    "RSC0"  u32 count=8  u32 0x80
    then count x (char[4] tag, u32 value)   MIP0 PAL0 TEX0 MOD0 MOD0 MOD0 MPH0 MOD0

The last value equals the file size exactly, which says the table is a list of
chunk boundaries. This script tests that reading and prints what lands at each
boundary, so the interpretation can be confirmed rather than assumed.
"""

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rdrstudio import Game          # noqa: E402
from rdrstudio import config as cfg  # noqa: E402


def hexdump(data, base, n=64):
    out = []
    for i in range(0, min(n, len(data)), 16):
        chunk = data[i:i + 16]
        asc = "".join(chr(c) if 32 <= c < 127 else "." for c in chunk)
        out.append("    %06X  %-47s  %s" % (base + i, chunk.hex(" "), asc))
    return "\n".join(out)


def parse(name, data):
    print("=" * 74)
    print("%s   %d bytes" % (name, len(data)))
    magic = data[:4].decode("latin-1")
    count, hdr_size = struct.unpack_from("<II", data, 4)
    print("  magic=%s count=%d field2=0x%X" % (magic, count, hdr_size))
    print("\n  raw header:")
    print(hexdump(data, 0, 0x90))

    print("\n  table entries:")
    entries = []
    for i in range(count):
        off = 12 + i * 8
        if off + 8 > len(data):
            break
        tag = data[off:off + 4].decode("latin-1", "replace")
        val = struct.unpack_from("<I", data, off + 4)[0]
        entries.append((tag, val))
        flag = ""
        if val == len(data):
            flag = "   <-- equals file size"
        elif val > len(data):
            flag = "   <-- PAST EOF"
        print("    %2d  %-6s 0x%08X (%d)%s" % (i, tag, val, val, flag))

    # If these are file offsets, each should land on something recognisable.
    print("\n  bytes at each offset (are these chunk starts?):")
    for i, (tag, val) in enumerate(entries):
        if val >= len(data):
            print("    %2d %-6s  past EOF, skipped" % (i, tag))
            continue
        peek = data[val:val + 16]
        asc = "".join(chr(c) if 32 <= c < 127 else "." for c in peek)
        print("    %2d %-6s @0x%06X  %-47s  %s" % (i, tag, val, peek.hex(" "), asc))
    return entries


def main():
    iso = sys.argv[1] if len(sys.argv) > 1 else cfg.VANILLA_ISO
    g = Game(iso)
    d = g.archive("LVLS_SNG/MANS.DAT")
    targets = sys.argv[2:] or ["lvl_mans_nav.rsc", "ani_lods.rsc", "lvl_mans.rsc"]
    for t in targets:
        try:
            parse(t, d.read(t))
            print()
        except Exception as ex:
            print("%s: %s: %s" % (t, type(ex).__name__, ex))


if __name__ == "__main__":
    main()
