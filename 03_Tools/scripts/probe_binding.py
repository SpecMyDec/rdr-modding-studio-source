"""What actually binds a texture to a mesh?

Two candidates were ruled out already:

  * VIF DIRECT packets -- there are none. The display lists carry no GS
    register writes at all, so the engine sets the texture, not the packet.
  * "nearest preceding TEX0 chunk" -- what we ship. It is a guess, and it is
    what puts an interior atlas on a building wall.

That leaves the container MOD0's outer table, `count` x (u32 resource,
u32 flags). Those u32s were dismissed once because they land on MOD0/MIP0/
PMT0 tags rather than TEX0. But chunk order on disc is MIP0, PAL0, TEX0 --
so a reference to a texture's *pixels* is a reference two chunks before its
TEX0. This checks every fixed offset, and also whether the u32 is an index
at all versus a byte offset or an id.
"""
import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rdrstudio import Game, rsc  # noqa: E402
from rdrstudio.config import VANILLA_ISO  # noqa: E402

LEVELS = [("LVLS_SNG/PERFNEW.DAT", "lvl_perfnew.rsc"),
          ("LVLS_SNG/TOMB.DAT", "lvl_tomb.rsc"),
          ("LVLS_SNG/SHER70.DAT", "lvl_sher70.rsc")]


def part_records(b, index):
    """(resource, flags) pairs from a container MOD0's outer table."""
    raw = b.raw(index)
    if len(raw) < 32:
        return None
    count, bone_count = raw[0], raw[1]
    version = struct.unpack_from("<H", raw, 2)[0]
    header_size, data_start = struct.unpack_from("<II", raw, 4)
    if (version != 1 or header_size != 16 or count < 1 or count > 4096
            or not (header_size <= data_start < len(raw))):
        return None
    if header_size + count * 4 > len(raw):
        return None
    table = struct.unpack_from("<%dI" % count, raw, header_size)
    if table[0] != header_size + count * 4:
        return None
    if any(bb - a != 8 for a, bb in zip(table, table[1:])):
        return None
    out = []
    for off in table:
        if off + 8 > len(raw):
            return None
        out.append(struct.unpack_from("<II", raw, off))
    return out


def main():
    game = Game(VANILLA_ISO)
    for arc, area in LEVELS:
        blob = game.archive(arc).read(area)
        b = rsc.Rsc(blob)
        ntag = len(b.chunks)
        print("\n=== %s (%d chunks) ===" % (area, ntag))
        print("tally:", dict(sorted(b.tally().items(), key=lambda kv: -kv[1])))

        refs = []
        flagvals = Counter()
        containers = 0
        for c in b.chunks:
            if c.tag != "MOD0":
                continue
            recs = part_records(b, c.index)
            if not recs:
                continue
            containers += 1
            for res, flg in recs:
                refs.append(res)
                flagvals[flg] += 1
        print("containers: %d   part records: %d" % (containers, len(refs)))
        if not refs:
            continue
        print("flags seen:", dict(flagvals.most_common(8)))
        print("resource range: %d..%d (chunks 0..%d)"
              % (min(refs), max(refs), ntag - 1))

        # If the u32 is a chunk index, which tag does it land on -- and does
        # a fixed offset make it land on TEX0 every time?
        for delta in (0, 1, 2, 3, -1, -2):
            tags = Counter()
            for r in refs:
                j = r + delta
                tags[b.chunks[j].tag if 0 <= j < ntag else "OOB"] += 1
            top = ", ".join("%s=%d" % kv for kv in tags.most_common(5))
            hit = 100.0 * tags.get("TEX0", 0) / len(refs)
            print("  delta %+d -> %-52s TEX0 %.1f%%" % (delta, top, hit))


if __name__ == "__main__":
    main()
