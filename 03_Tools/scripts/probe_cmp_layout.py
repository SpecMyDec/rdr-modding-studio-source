"""Does the layout in `readData` (0x002D35F0) explain the cmp files exactly?

The reader consumes the stream in this order:

    tracks x 12 bytes   -- u32, u32, u16, u16 (the last read into scratch)
    2 bytes             -- nchan
    nchan * keys * 4    -- and `mult a0, a0, v0` is the R5900 three-operand
                           form (rd = a0, encoding 0x00822018), so this is
                           nchan*keys floats, RAW

`keys` and `tracks` come from an already-parsed header object, not from the
stream, so they are taken from the file's own 17-byte header here. Half rate
(bit 3) halves the key count the same way cm2 does.

If that is right the size equation closes with nothing left over, on every
file. It is checked that way rather than approximately, because a layout that
is nearly right still explains most files.

The consequence, if it holds: cmp samples are **plain float32**. There is no
adaptive bit-packing to decode -- the thing this task was named after does not
exist.
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdrstudio.game import Game                      # noqa: E402
from rdrstudio import config                          # noqa: E402

# REQ-035/036. Was a literal path to the WORKING ISO on one machine:
# unrunnable on anyone else's checkout, and pointed at the disc the live
# tests mutate. What this examines is a property of the SHIPPED data, so
# it reads the stock disc. Read-only throughout, which is what makes
# vanilla the right answer here rather than merely the tidier one.
DISC = Path(config.VANILLA_ISO)
HDR = 17


def collect(game):
    out, seen = [], set()
    for arc in game.archives():
        try:
            toc = game.toc(arc)
        except Exception:
            continue
        for e in toc:
            if not e.name.lower().endswith(".anim") or e.name in seen:
                continue
            try:
                d = game.read(arc, e.name)
            except Exception:
                continue
            if d[:4] == b"cmp\x00":
                seen.add(e.name)
                out.append((arc, e.name, d))
    return out


def main():
    game = Game(DISC, writable=False)
    files = collect(game)
    print("%d cmp clips\n" % len(files))
    ok = bad = 0
    halves = 0
    widths = {}
    examples = []
    for arc, name, d in files:
        frames = struct.unpack_from("<I", d, 4)[0]
        tracks = struct.unpack_from("<I", d, 8)[0]
        flags = d[16]
        keys = frames
        if flags & 0x08:
            keys = frames // 2 + 1
            halves += 1
        off = HDR + tracks * 12
        if off + 2 > len(d):
            bad += 1
            continue
        nchan = struct.unpack_from("<H", d, off)[0]
        end = off + 2 + nchan * keys * 4
        if end == len(d):
            ok += 1
            if len(examples) < 4:
                examples.append((name, frames, keys, tracks, nchan, flags))
            # the per-track byte the code keeps (low byte of the u16 at +8)
            for i in range(min(tracks, 400)):
                b = d[HDR + i * 12 + 8]
                widths[b] = widths.get(b, 0) + 1
        else:
            bad += 1
            if bad <= 3:
                print("  MISMATCH %-28s size %d, predicted %d (off by %d)"
                      % (name, len(d), end, len(d) - end))

    print("exact: %d/%d     mismatched: %d" % (ok, len(files), bad))
    print("half-rate clips: %d\n" % halves)
    for name, frames, keys, tracks, nchan, flags in examples:
        print("  %-30s frames=%-5d keys=%-5d tracks=%-4d nchan=%-4d "
              "flags=0x%02X" % (name, frames, keys, tracks, nchan, flags))
    print("\nthe per-track byte the reader stores (record[+8] low byte):")
    for b in sorted(widths)[:16]:
        print("   value %-4d x%d" % (b, widths[b]))

    game.close()
    return 0 if ok == len(files) else 1


if __name__ == "__main__":
    sys.exit(main())
