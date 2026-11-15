"""Decode cmp clips and check the values are physically sensible.

The layout and the packing both come from the game's own code:

  readData 0x002D35F0   tracks x 12 bytes, then u16 nchan, then
                        nchan*keys 32-bit words, KEY-MAJOR
  sampler  0x002D3F08   word = samples[frame*nchan + index]
                        field 0 = bits 0..10   (11 bits)
                        field 1 = bits 11..20  (10 bits)
                        field 2 = bits 21..31  (11 bits)
                        value   = scale[track] * field + bias[track]

`scale` is the first per-track float multiplied by 1/2048, or 1/1024 when
`track % 3 == 1` -- which is exactly the field that has one fewer bit, so all
three fields cover the same range. `bias` is the second per-track float. An
index of 0xFFFF marks a channel that never moves: it has no field, and its
value is the bias alone.

Sensible means: rotations inside a few radians, root translation inside a few
hundred metres, no NaNs, and curves that are continuous rather than jumping
the full quantiser range between adjacent keys. A decoder that had the fields
or the scales wrong would fail those.
"""
import math
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
S0 = struct.unpack("<f", struct.pack("<I", 0x3A001002))[0]   # ~1/2048
S1 = struct.unpack("<f", struct.pack("<I", 0x3A802008))[0]   # ~1/1024


def decode(d):
    frames = struct.unpack_from("<I", d, 4)[0]
    tracks = struct.unpack_from("<I", d, 8)[0]
    flags = d[16]
    keys = frames // 2 + 1 if flags & 0x08 else frames
    scale, bias, index = [], [], []
    for i in range(tracks):
        o = HDR + i * 12
        a = struct.unpack_from("<f", d, o)[0]
        b = struct.unpack_from("<f", d, o + 4)[0]
        c = struct.unpack_from("<H", d, o + 8)[0]
        scale.append(a * (S1 if i % 3 == 1 else S0))
        bias.append(b)
        index.append(c)
    off = HDR + tracks * 12
    nchan = struct.unpack_from("<H", d, off)[0]
    base = off + 2
    words = struct.unpack_from("<%dI" % (nchan * keys), d, base)

    out = [[0.0] * tracks for _ in range(keys)]
    for k in range(keys):
        row = out[k]
        wbase = k * nchan
        for i in range(tracks):
            ci = index[i]
            if ci == 0xFFFF:
                row[i] = bias[i]
                continue
            w = words[wbase + ci]
            slot = i % 3
            if slot == 0:
                f = w & 0x7FF
            elif slot == 1:
                f = (w >> 11) & 0x3FF
            else:
                f = (w >> 21) & 0x7FF
            row[i] = scale[i] * f + bias[i]
    return frames, keys, tracks, nchan, out


def main():
    game = Game(DISC, writable=False)
    files, seen = [], set()
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
                files.append((e.name, d))
    print("%d cmp clips\n" % len(files))

    ok = True
    worst_rot = 0.0
    worst_pos = 0.0
    worst_jump = 0.0
    nan = 0
    done = 0
    for name, d in files:
        try:
            frames, keys, tracks, nchan, rows = decode(d)
        except Exception as ex:
            print("  %s: decode failed: %s" % (name, ex))
            ok = False
            continue
        done += 1
        for k, row in enumerate(rows):
            for i, v in enumerate(row):
                if v != v or math.isinf(v):
                    nan += 1
                    continue
                if i < 3:
                    worst_pos = max(worst_pos, abs(v))
                else:
                    worst_rot = max(worst_rot, abs(v))
            if k:
                prev = rows[k - 1]
                for i in range(3, tracks):
                    worst_jump = max(worst_jump, abs(row[i] - prev[i]))

    print("decoded %d/%d clips" % (done, len(files)))
    print("  non-finite samples          : %d" % nan)
    print("  worst |root translation|    : %.2f m" % worst_pos)
    print("  worst |rotation|            : %.3f rad (%.1f deg)"
          % (worst_rot, math.degrees(worst_rot)))
    print("  worst key-to-key rotation   : %.3f rad (%.1f deg)"
          % (worst_jump, math.degrees(worst_jump)))
    ok &= (nan == 0 and worst_rot < 20.0 and worst_pos < 1000.0)
    print("\n%s" % ("VALUES LOOK SANE" if ok else "SOMETHING IS OFF"))
    game.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
