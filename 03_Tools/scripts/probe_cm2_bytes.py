"""Stare at the smallest cm2 animations byte by byte.

Everything eliminated so far (fixed-rate, per-channel offset tables, plain
deflate) was eliminated statistically, across thousands of files. That was the
right first move and it is exhausted. What is left is to read the smallest
files literally, because the smallest file constrains the grammar hardest:
`ptl_red_ptl_run_rgt_hls.anim` is 42 bytes for 55 frames and 6 channels, so
after a 20-byte header there are 22 bytes to describe six curves. Any theory
has to survive that.

Prints, for the N smallest cm2 files on the disc:
  * the header fields
  * the payload as bytes, as int16 LE, and as int16 BE
  * a per-nibble and per-byte histogram of the payload

and then looks for the structural tells worth checking by eye:
  * does byte 0 of the payload equal the channel count, or a keyframe count?
  * do the first `channels` bytes look like per-channel counts that sum to
    something consistent with the remaining bytes?
"""
import struct
import sys
from collections import Counter
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
SHOW = 8            # how many of the smallest files to print in full
HDR = 20


def header(data):
    magic = data[:4]
    frames, channels, f0c, f10 = struct.unpack_from("<IIII", data, 4)
    return magic, frames, channels, f0c, f10


def main():
    game = Game(DISC, writable=False)
    print("indexing .anim files...")
    hits = []
    for arc in game.archives():
        try:
            toc = game.toc(arc)
        except Exception:
            continue
        for e in toc:
            if not e.name.lower().endswith(".anim"):
                continue
            hits.append((e.size_full, arc, e.name))
    hits.sort()
    print("%d .anim entries; smallest %d bytes, largest %d"
          % (len(hits), hits[0][0], hits[-1][0]))

    # Dedupe by (name) so the same animation in 30 archives is printed once.
    seen = set()
    picks = []
    for size, arc, name in hits:
        if name in seen:
            continue
        seen.add(name)
        try:
            data = game.read(arc, name)
        except Exception:
            continue
        if data[:4] not in (b"cm2\x00", b"cmp\x00"):
            continue
        picks.append((arc, name, data))
        if len(picks) >= SHOW:
            break

    for arc, name, data in picks:
        magic, frames, channels, f0c, f10 = header(data)
        body = data[HDR:]
        print("\n" + "=" * 72)
        print("%s   (%s)" % (name, arc))
        print("=" * 72)
        print("  magic %r  frames %d  channels %d (%d bones)  +0x0C %d  +0x10 %d"
              % (magic, frames, channels, channels // 3, f0c, f10))
        print("  total %d bytes, payload %d bytes after a %d-byte header"
              % (len(data), len(body), HDR))
        if channels:
            print("  payload / channels = %.3f bytes per channel"
                  % (len(body) / channels))
            print("  payload / (frames*channels) = %.4f bytes per sample"
                  % (len(body) / (frames * channels)))

        print("\n  raw header : %s" % data[:HDR].hex(" "))
        print("  raw payload: %s" % body.hex(" "))

        print("\n  payload as bytes  : %s"
              % " ".join("%3d" % b for b in body))
        print("  payload as int8   : %s"
              % " ".join("%4d" % struct.unpack_from("<b", body, i)[0]
                         for i in range(len(body))))
        n16 = len(body) // 2
        if n16:
            le = struct.unpack_from("<%dh" % n16, body, 0)
            be = struct.unpack_from(">%dh" % n16, body, 0)
            print("  payload as i16 LE : %s" % " ".join("%6d" % v for v in le))
            print("  payload as i16 BE : %s" % " ".join("%6d" % v for v in be))
            print("  as i16 LE / 32768 : %s"
                  % " ".join("%6.3f" % (v / 32768.0) for v in le))

        # Structural tells, checked out loud rather than assumed.
        print("\n  tells:")
        print("    payload[0] = %d   (channels=%d, frames=%d)"
              % (body[0] if body else -1, channels, frames))
        if len(body) >= channels:
            head = list(body[:channels])
            print("    first `channels` bytes: %s  sum=%d"
                  % (head, sum(head)))
            rest = len(body) - channels
            print("      -> %d bytes left; sum*2=%d  sum*4=%d"
                  % (rest, sum(head) * 2, sum(head) * 4))
        # A keyframe count as a u16/u32 anywhere in the header?
        for off in (0x0C, 0x10):
            v = struct.unpack_from("<I", data, off)[0]
            if 0 < v <= frames:
                print("    header +0x%02X = %d, which is <= frames (%d)"
                      % (off, v, frames))

    # Across many files: what does +0x10 correlate with?
    print("\n" + "=" * 72)
    print("header +0x10 across the 400 smallest distinct cm2 files")
    print("=" * 72)
    rows = []
    seen = set()
    for size, arc, name in hits:
        if name in seen:
            continue
        seen.add(name)
        try:
            data = game.read(arc, name)
        except Exception:
            continue
        if data[:4] not in (b"cm2\x00", b"cmp\x00"):
            continue
        magic, frames, channels, f0c, f10 = header(data)
        rows.append((len(data), frames, channels, f0c, f10))
        if len(rows) >= 400:
            break

    c0c = Counter(r[3] for r in rows)
    print("  +0x0C values: %s" % c0c.most_common(6))
    c10 = Counter(r[4] for r in rows)
    print("  +0x10 values: %s" % c10.most_common(10))
    # Does +0x10 relate to payload size or channels?
    eq_ch = sum(1 for r in rows if r[4] == r[2])
    eq_bo = sum(1 for r in rows if r[4] == r[2] // 3)
    eq_fr = sum(1 for r in rows if r[4] == r[1])
    print("  +0x10 == channels : %d / %d" % (eq_ch, len(rows)))
    print("  +0x10 == bones    : %d / %d" % (eq_bo, len(rows)))
    print("  +0x10 == frames   : %d / %d" % (eq_fr, len(rows)))
    hdr20 = sum(1 for r in rows if (r[0] - 20) % 2 == 0)
    print("  payload after 20-byte header is even-length: %d / %d"
          % (hdr20, len(rows)))

    game.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
