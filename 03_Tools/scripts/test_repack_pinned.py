"""Repack an archive, but keep chosen files on a sector boundary.

The bisect reduced SWAT.DAT's freeze to a single file: moving `logo01.pck`
243,504 bytes) back by 624 bytes freezes the game at the title screen, with
47,100 of 47,101 other blocks untouched and every file still reading back
byte-identically. The freeze hash is the same one the full 65-archive repack
produced, so that one move accounts for the whole failure.

The 624 bytes matter in a specific way: `logo01.pck` sat at 0x0A6C8000, which
is exactly sector 85,392, and the move put it at 0x0A6C7D90, which is not a
sector boundary at all. So the hypothesis is that this file must start on a
2048-byte sector -- presumably because it is DMA'd off the disc rather than
read through the normal file path.

This is a narrower claim than the one tested earlier and rejected. Repacking
STREAMS.DAT flat at 16 bytes destroys all 8,361 of its sector alignments and
plays fine, which shows sector alignment is not required in GENERAL. It does
not show that no individual file requires it, and that over-generalisation is
what this script exists to correct.

The test: repack everything flat at 16 bytes -- the layout that freezes -- but
force the pinned files onto sector boundaries. If the game then plays, the
mechanism is alignment, the fix is cheap (a couple of KB of padding), and
SWAT.DAT's 13.5 MB becomes reclaimable after all.

    python scripts/test_repack_pinned.py                     pin logo01.pck
    python scripts/test_repack_pinned.py --pin-ext pck       pin every .pck
    python scripts/test_repack_pinned.py --no-boot

Writes ONLY to 02_Modded/RDR_REPACKTEST.iso.
"""
import argparse
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rdrstudio.game import Game                      # noqa: E402
from rdrstudio.dave import SECTOR                     # noqa: E402
from rdrstudio.config import ROOT, VANILLA_ISO   # noqa: E402

VANILLA = Path(VANILLA_ISO)
TEST_ISO = Path(ROOT) / "02_Modded" / "RDR_REPACKTEST.iso"
ALIGN = 16


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", default="SWAT.DAT")
    ap.add_argument("--pin", default="logo01.pck",
                    help="comma-separated file names to keep sector-aligned")
    ap.add_argument("--pin-ext", default="",
                    help="also pin every file with these extensions")
    ap.add_argument("--no-boot", action="store_true")
    ap.add_argument("--seconds", type=float, default=185.0)
    args = ap.parse_args()

    if TEST_ISO.exists():
        os.chmod(TEST_ISO, 0o666)
    print("restoring a pristine test ISO ...")
    shutil.copyfile(VANILLA, TEST_ISO)
    os.chmod(TEST_ISO, 0o666)

    pin_names = {x.strip().lower() for x in args.pin.split(",") if x.strip()}
    pin_exts = {x.strip().lower().lstrip(".")
                for x in args.pin_ext.split(",") if x.strip()}

    game = Game(TEST_ISO, writable=True)
    dv = game.archive(args.archive)
    raw = dv.backing.read_at(0, dv.size)

    span, pinned_offsets = {}, set()
    for e in dv.entries:
        n = e.size_comp or e.size_full
        if n > span.get(e.offset, 0):
            span[e.offset] = n
        if e.name.lower() in pin_names or e.ext.lower() in pin_exts:
            # Only meaningful if it WAS sector-aligned; pinning a file that
            # never was would be inventing a constraint.
            if e.offset % SECTOR == 0:
                pinned_offsets.add(e.offset)

    order = sorted(span)
    print("%s: %d blocks, %d pinned to sector boundaries"
          % (args.archive, len(order), len(pinned_offsets)))
    if not pinned_offsets:
        print("nothing matched --pin/--pin-ext among sector-aligned files")
        return 1

    remap, cur = {}, order[0]
    for off in order:
        a = SECTOR if off in pinned_offsets else ALIGN
        cur = (cur + a - 1) // a * a
        remap[off] = cur
        cur += (span[off] + ALIGN - 1) // ALIGN * ALIGN

    reclaimed = dv.size - cur
    if cur > len(raw):
        print("layout needs %d bytes, archive is %d -- refusing" % (cur, len(raw)))
        return 1
    moved = sum(1 for o in order if remap[o] != o)
    print("  moved %d of %d blocks, reclaimed %d bytes (%.2f MB)"
          % (moved, len(order), reclaimed, reclaimed / 1048576.0))
    for o in sorted(pinned_offsets):
        nm = next(e.name for e in dv.entries if e.offset == o)
        print("  pinned %-20s 0x%08X -> 0x%08X  (aligned=%s)"
              % (nm, o, remap[o], remap[o] % SECTOR == 0))

    out = bytearray(len(raw))
    hdr = SECTOR + dv.info_size + dv.name_size
    out[0:hdr] = raw[0:hdr]
    for e in dv.entries:
        struct.pack_into("<IIII", out, SECTOR + e.index * 16,
                         e.name_offset, remap[e.offset],
                         e.size_full, e.size_comp)
    for off in order:
        n = span[off]
        dst = remap[off]
        if dst + n > len(out):
            print("REFUSING: block would run past the archive end")
            return 1
        out[dst:dst + n] = raw[off:off + n]

    if len(out) != dv.size:
        print("REFUSING: size changed")
        return 1
    dv.backing.write_at(0, bytes(out))
    game.close()

    g2 = Game(TEST_ISO, writable=False)
    src = Game(VANILLA, writable=False)
    bad = sum(1 for e in g2.archive(args.archive)
              if src.read(args.archive, e.name) != g2.read(args.archive, e.name))
    src.close()
    g2.close()
    print("  read-back: %d of %d files wrong" % (bad, dv.count))
    if bad:
        return 1

    if args.no_boot:
        return 0
    print("\nplaying ...")
    subprocess.run([sys.executable, str(HERE / "play_test.py"),
                    "--level", "--seconds", str(args.seconds),
                    "--iso", str(TEST_ISO)], cwd=str(HERE.parent))
    print("\nIf this PLAYS, sector alignment was the mechanism and %.1f MB of "
          "SWAT.DAT\nis reclaimable. If it still freezes, alignment is not it "
          "and the cause is\nsomething else about where that file sits."
          % (reclaimed / 1048576.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
