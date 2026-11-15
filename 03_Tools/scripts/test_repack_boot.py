"""Does repacking a Dave archive actually brick the game?

The toolkit inherited a rule -- never repack, it causes a permanent
black-screen freeze -- from a single earlier attempt that was never verified.
It is the reason the tool can edit content but never ADD any, so it is worth
testing rather than obeying.

`probe_repack.py` already established the cheap half: 66 of 66 archives --
the whole disc -- rebuild byte-identically from parsed structure, so the
format is fully understood and the old failure was a bug rather than a law.

This is the expensive half. It writes a REAL repack -- a different byte
layout, not a reproduction -- into a throwaway ISO and boots it.

What "repack" means here: every file is moved to be tightly packed at its
natural alignment, reclaiming the slack that sits between files. The archive
keeps its total size (it is a fixed-size file inside the ISO and the ISO has
no inter-file gaps), so the reclaimed space collects at the END, which is
exactly where new content would go.

    python scripts/test_repack_boot.py            repack + boot
    python scripts/test_repack_boot.py --no-boot  repack and verify only

NEVER touches 01_Vanilla or 02_Modded/RDR_MODDED.iso.
"""
import argparse
import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rdrstudio.game import Game                      # noqa: E402
from rdrstudio.dave import SECTOR                     # noqa: E402
from rdrstudio.config import ROOT, VANILLA_ISO   # noqa: E402

TEST_ISO = Path(ROOT) / "02_Modded" / "RDR_REPACKTEST.iso"

# MEASURED, not assumed. An earlier version of this script said "every
# original offset observed is sector aligned" and packed at 2048 -- which made
# the archive BIGGER than the original, because it is not true. The real
# alignment histogram for FRONT.DAT is 236 offsets at 2048 but 38 at 32, 22 at
# 64, 12 at 128 and 6 at 256; BANKS.DAT goes down to 16. The packer aligned to
# a sector when that happened to be free and to 16 bytes otherwise, so 16 is
# the alignment the game demonstrably accepts.
ALIGN = 16


def _original_alignment(off):
    """The largest power-of-two boundary this file already sat on, capped at a
    sector.

    This exists because of a hypothesis that turned out to be WRONG, and it is
    kept because the disproof is worth having in the code.

    The idea: STREAMS.DAT has **all 8,361 of its files on a 2048-byte
    sector**, which looks designed rather than incidental (SWAT.DAT is 37%,
    FRONT.DAT 74%). Streamed audio is DMA'd off the disc in whole sectors, so
    the obvious conclusion was that flat 16-byte packing must break it, and
    that this was why repacking everything froze the game.

    The test says otherwise. Repacking STREAMS.DAT alone at flat 16 bytes,
    destroying every one of those 8,361 sector alignments, plays perfectly --
    and the archive that actually freezes the game, SWAT.DAT, is the *least*
    sector-aligned of the four. Alignment is not the mechanism.

    It is also useless as a safety measure: preserving alignment reclaims
    0-1 KB per archive instead of 200 KB - 13 MB, because the slack IS the
    alignment padding. Preserve everything and you have repacked nothing.
    """
    if off == 0:
        return SECTOR
    a = 1
    while a < SECTOR and off % (a * 2) == 0:
        a *= 2
    return max(a, ALIGN)


def repack_archive(dv, raw, preserve_alignment=False, keep_first=0):
    """Rebuild the archive with files tightly packed. Returns (bytes, stats).

    Identical files share one offset in the original (the packer deduplicated
    them), so that sharing is preserved -- breaking it would inflate the
    archive and defeat the point.

    `preserve_alignment` keeps each file on the boundary it already sat on.
    It defaults OFF because it was tried as a fix and is not one: see
    `_original_alignment`. Flat 16-byte packing is verified working on
    FRONT.DAT, STREAMS.DAT, BANKS.DAT and PUPSHOW.DAT.

    `keep_first` leaves the first N data blocks exactly where they are and
    packs only what follows. This exists to bisect WITHIN an archive:
    SWAT.DAT's 47,101 blocks cannot all be repacked without freezing the game,
    and binary-searching N finds the block whose movement is what breaks it.
    N=0 is a full repack; N >= the block count is a no-op.
    """
    # Longest span stored at each offset, computed once. This used to be a
    # `max(... for e in dv.entries if e.offset == off)` inside the loop, which
    # is O(files x blocks) -- fine for FRONT.DAT's 317 and about 2.2 billion
    # comparisons for SWAT.DAT's 47,115.
    span = {}
    for e in dv.entries:
        n = e.size_comp or e.size_full
        if n > span.get(e.offset, 0):
            span[e.offset] = n

    order = sorted(span)
    remap = {}
    cur = order[0]
    for i, off in enumerate(order):
        n = span[off]
        if i < keep_first:
            # Untouched. `cur` must still advance past it, or the first moved
            # block would be written on top of a block that never moved.
            remap[off] = off
            cur = max(cur, off + (n + ALIGN - 1) // ALIGN * ALIGN)
            continue
        a = _original_alignment(off) if preserve_alignment else ALIGN
        cur = (cur + a - 1) // a * a
        remap[off] = cur
        cur += (n + ALIGN - 1) // ALIGN * ALIGN

    reclaimed = dv.size - cur
    if cur > len(raw):
        raise ValueError(
            "repacked layout needs %d bytes but the archive is %d -- refusing"
            % (cur, len(raw)))
    out = bytearray(len(raw))
    out[0:SECTOR + dv.info_size + dv.name_size] = \
        raw[0:SECTOR + dv.info_size + dv.name_size]

    for e in dv.entries:
        struct.pack_into("<IIII", out, SECTOR + e.index * 16,
                         e.name_offset, remap[e.offset],
                         e.size_full, e.size_comp)
    for off in order:
        n = span[off]
        dst = remap[off]
        # A slice assignment past the end EXTENDS a bytearray rather than
        # failing, which is how the first attempt silently produced an archive
        # 132 KB larger than the one it was replacing.
        if dst + n > len(out):
            raise ValueError("file at %d would run past the archive end" % dst)
        out[dst:dst + n] = raw[off:off + n]

    return bytes(out), dict(reclaimed=reclaimed, moved=sum(
        1 for o in order if remap[o] != o), blocks=len(order))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", default="FRONT.DAT")
    ap.add_argument("--no-boot", action="store_true")
    ap.add_argument("--seconds", type=float, default=70.0)
    args = ap.parse_args()

    if not TEST_ISO.exists():
        print("no test ISO at %s -- copy the vanilla one there first" % TEST_ISO)
        return 1

    game = Game(TEST_ISO, writable=True)
    dv = game.archive(args.archive)
    raw = dv.backing.read_at(0, dv.size)
    print("%s: %d files, %d bytes" % (args.archive, dv.count, dv.size))

    packed, st = repack_archive(dv, raw)
    print("  files moved      : %d of %d data blocks" % (st["moved"], st["blocks"]))
    print("  slack reclaimed  : %d bytes (%.2f MB)"
          % (st["reclaimed"], st["reclaimed"] / 1048576.0))
    print("  archive size     : unchanged (%d)" % len(packed))
    if len(packed) != dv.size:
        print("  REFUSING: repack changed the archive size")
        return 1
    if st["moved"] == 0:
        print("  nothing moved -- this archive is already tight; pick another")
        return 1

    dv.backing.write_at(0, packed)
    dv.backing.flush()
    game.close()
    print("  written to the TEST iso")

    # Re-open and prove the repacked archive still reads correctly.
    g2 = Game(TEST_ISO, writable=False)
    d2 = g2.archive(args.archive)
    bad = 0
    checked = 0
    src = Game(Path(VANILLA_ISO), writable=False)
    for e in d2:
        try:
            a = src.read(args.archive, e.name)
            b = g2.read(args.archive, e.name)
        except Exception:
            bad += 1
            continue
        checked += 1
        if a != b:
            bad += 1
    print("\n  re-read after repack: %d files, %d wrong" % (checked, bad))
    src.close()
    g2.close()
    if bad:
        print("  REPACK IS BROKEN -- not booting it")
        return 1
    print("  every file still reads back byte-identically")

    if args.no_boot:
        return 0

    print("\nbooting the repacked ISO ...")
    r = subprocess.run([sys.executable, str(HERE / "play_test.py"),
                        "--seconds", str(args.seconds), "--save-state",
                        "--iso", str(TEST_ISO)], cwd=str(HERE.parent))
    print("\nplay_test exit: %s" % r.returncode)
    print("Look at the screenshots: a black screen throughout means the old")
    print("rule was right; gameplay means it was not.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
