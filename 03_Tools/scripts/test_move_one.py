"""Move exactly ONE file inside an archive, and see whether the game notices.

The bisect narrows SWAT.DAT to a single block index, but "keeping N blocks
plays and keeping N-1 freezes" is not quite the same statement as "moving
block N-1 breaks the game". `keep_first=N-1` moves that block AND every block
above it, so the bisect isolates a boundary, not a cause.

This closes that gap by changing one thing. It relocates a single file into
the slack that already sits in front of it -- everything else on the disc,
including every other file in the same archive, stays exactly where it is. If
the game then freezes, the conclusion is exact and single-variable.

    python scripts/test_move_one.py --archive SWAT.DAT --block 46955
    python scripts/test_move_one.py --archive SWAT.DAT --name logo01.pck
    python scripts/test_move_one.py --archive SWAT.DAT --block 46955 --no-boot

Where the file goes: the packer left padding between consecutive blocks, so
the target is the first 16-byte boundary after the previous block ends. That
is inside the archive, needs no space to be created, and moves the file by a
small, known number of bytes. The old copy is left in place as stale bytes --
which is deliberate. If the game reads through the TOC it will never see them;
if it freezes anyway, something is reaching the data by a route other than the
TOC, which is the whole question.

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
    ap.add_argument("--block", type=int, default=-1,
                    help="data-block index, in ascending-offset order")
    ap.add_argument("--name", default="", help="or name the file directly")
    ap.add_argument("--no-boot", action="store_true")
    ap.add_argument("--seconds", type=float, default=185.0)
    ap.add_argument("--restore", action="store_true", default=True)
    args = ap.parse_args()

    if args.block < 0 and not args.name:
        print("give --block or --name")
        return 2

    if args.restore:
        if TEST_ISO.exists():
            os.chmod(TEST_ISO, 0o666)
        print("restoring a pristine test ISO ...")
        shutil.copyfile(VANILLA, TEST_ISO)
        os.chmod(TEST_ISO, 0o666)

    game = Game(TEST_ISO, writable=True)
    dv = game.archive(args.archive)

    span = {}
    for e in dv.entries:
        n = e.size_comp or e.size_full
        if n > span.get(e.offset, 0):
            span[e.offset] = n
    order = sorted(span)

    if args.name:
        hit = next((e for e in dv.entries
                    if e.name.lower() == args.name.lower()), None)
        if not hit:
            print("no file named %r in %s" % (args.name, args.archive))
            return 1
        idx = order.index(hit.offset)
    else:
        idx = args.block
        if not 0 <= idx < len(order):
            print("block %d out of range (0..%d)" % (idx, len(order) - 1))
            return 1

    off = order[idx]
    size = span[off]
    sharers = [e.name for e in dv.entries if e.offset == off]
    print("\nblock %d: offset 0x%08X, %d bytes, named by %s"
          % (idx, off, size, sharers))

    if idx == 0:
        print("block 0 has nothing in front of it to move into")
        return 1
    prev = order[idx - 1]
    prev_end = prev + span[prev]
    dst = (prev_end + ALIGN - 1) // ALIGN * ALIGN
    if dst >= off:
        print("no slack in front of this block (previous ends 0x%08X, this "
              "starts 0x%08X) -- nothing to move it into" % (prev_end, off))
        return 1
    moved_by = off - dst
    print("moving it back %d bytes, to 0x%08X" % (moved_by, dst))
    print("  (previous block ends 0x%08X)" % prev_end)

    raw = dv.backing.read_at(0, dv.size)
    out = bytearray(raw)
    out[dst:dst + size] = raw[off:off + size]
    # Every TOC entry pointing at this block follows it. Files that merely
    # shared the offset are the same bytes, so they move together or the
    # archive would need two copies.
    touched = 0
    for e in dv.entries:
        if e.offset == off:
            struct.pack_into("<IIII", out, SECTOR + e.index * 16,
                             e.name_offset, dst, e.size_full, e.size_comp)
            touched += 1
    print("  %d TOC entries repointed; %d of %d blocks unmoved"
          % (touched, len(order) - 1, len(order)))

    if len(out) != dv.size:
        print("REFUSING: size changed")
        return 1
    dv.backing.write_at(0, bytes(out))
    game.close()

    g2 = Game(TEST_ISO, writable=False)
    src = Game(VANILLA, writable=False)
    bad = 0
    for e in g2.archive(args.archive):
        try:
            if src.read(args.archive, e.name) != g2.read(args.archive, e.name):
                bad += 1
        except Exception:
            bad += 1
    src.close()
    g2.close()
    print("  read-back: %d files wrong" % bad)
    if bad:
        print("  the move itself is broken -- not booting")
        return 1

    if args.no_boot:
        return 0

    print("\nplaying ...")
    subprocess.run([sys.executable, str(HERE / "play_test.py"),
                    "--level", "--seconds", str(args.seconds),
                    "--iso", str(TEST_ISO)], cwd=str(HERE.parent))
    print("\nOne file moved %d bytes. If the six late screenshots are "
          "byte-identical,\nthat single move is the whole cause." % moved_by)
    return 0


if __name__ == "__main__":
    sys.exit(main())
