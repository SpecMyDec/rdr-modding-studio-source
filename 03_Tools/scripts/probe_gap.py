"""Is there an unread table between a container MOD0's header and its data?

Known layout:

    0x00  u8  count
    0x01  u8  bone_count
    0x02  u16 version (1)
    0x04  u32 header_size (16)
    0x08  u32 data_start
    0x10  count x u32   offsets to 8-byte (resource, flags) records
    ...   count x 8B    the records themselves
    ???                 <- anything here has never been read
    data_start          count x u32 offsets to (offset, u16 idx, u16 verts)

`flags` is 16*k for k in 2..12 and matches nothing about the geometry. That
is the shape of a byte offset into a table of 16-byte records -- so look for
a table to point into.
"""
import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rdrstudio import Game, rsc  # noqa: E402
from rdrstudio.config import VANILLA_ISO  # noqa: E402

LEVELS = [("LVLS_SNG/TOMB.DAT", "lvl_tomb.rsc"),
          ("LVLS_SNG/PERFNEW.DAT", "lvl_perfnew.rsc"),
          ("LVLS_SNG/WOODS.DAT", "lvl_woods.rsc")]


def main():
    game = Game(VANILLA_ISO)
    gaps = Counter()
    shown = 0
    for arc, area in LEVELS:
        try:
            r = rsc.Rsc(game.archive(arc).read(area), area)
        except Exception:
            continue
        for c in r.chunks:
            if c.tag != "MOD0":
                continue
            parts = r.parts(c.index)
            if not parts:
                continue
            raw = r.raw(c.index)
            count = raw[0]
            header_size, data_start = struct.unpack_from("<II", raw, 4)
            end_of_records = header_size + count * 4 + count * 8
            gap = data_start - end_of_records
            gaps[gap] += 1
            if gap > 0 and shown < 6:
                shown += 1
                fl = sorted({p["flags"] for p in parts})
                print("%s chunk %d: count=%d gap=%d bytes  flags=%s"
                      % (area, c.index, count, gap, fl))
                blob = raw[end_of_records:data_start]
                for off in range(0, min(len(blob), 96), 16):
                    q = struct.unpack_from("<4I", blob, off) \
                        if off + 16 <= len(blob) else ()
                    fq = struct.unpack_from("<4f", blob, off) \
                        if off + 16 <= len(blob) else ()
                    print("    +0x%02X  %s   %s"
                          % (off,
                             " ".join("%08X" % x for x in q),
                             " ".join("%9.3f" % x for x in fq)))
    print("\ngap size histogram (bytes between records and data_start)")
    for g, n in sorted(gaps.items()):
        print("  %6d bytes : %5d containers%s"
              % (g, n, "   <- multiple of 16" if g and g % 16 == 0 else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
