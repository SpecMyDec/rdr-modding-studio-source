"""Structural probe for the .mod model container.

Strategy: models are mostly float vertex data. Classify every 4-byte word as
"plausible float" (finite, |v| < 4096, or exactly 0) vs "small int", then look
for long contiguous runs of floats -- those are the vertex/normal/UV blocks.
Then test whether any u32 in the header equals runlength/stride for a common
stride, which identifies the count fields.
"""

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rdrstudio import Game  # noqa: E402
from rdrstudio import config                         # noqa: E402

HDR = 14  # "version: 2.12\0"


def is_float(w):
    (f,) = struct.unpack("<f", w)
    if f != f or f in (float("inf"), float("-inf")):
        return False
    a = abs(f)
    return f == 0.0 or (1e-4 < a < 4096.0)


def runs(data, start):
    """Contiguous runs of plausible floats: [(offset, count)]."""
    out = []
    i = start
    n = len(data) - 3
    while i < n:
        if is_float(data[i:i + 4]):
            j = i
            while j < n and is_float(data[j:j + 4]):
                j += 4
            cnt = (j - i) // 4
            if cnt >= 9:
                out.append((i, cnt))
            i = j
        else:
            i += 4
    return out


def probe(name, data):
    print("=" * 70)
    print("%s  %d bytes" % (name, len(data)))
    hdr = struct.unpack_from("<11I", data, HDR)
    print("  header u32 x11 :", hdr)
    body = HDR + 44
    print("  body starts    : 0x%X" % body)

    rr = runs(data, body)
    rr.sort(key=lambda r: -r[1])
    print("  float runs (top 6, by length):")
    for off, cnt in rr[:6]:
        vals = struct.unpack_from("<%df" % min(cnt, 9), data, off)
        print("     @0x%-6X %5d floats  first9=%s"
              % (off, cnt, " ".join("%.3f" % v for v in vals)))
        for stride in (3, 4, 6, 8, 12, 16):
            if cnt % stride == 0:
                k = cnt // stride
                if k in hdr:
                    print("        -> %d floats / stride %d = %d  MATCHES header field %d"
                          % (cnt, stride, k, hdr.index(k)))
    return hdr, rr


if __name__ == "__main__":
    # config, not a `D:\` literal -- see REQ-059.
    iso = sys.argv[1] if len(sys.argv) > 1 else config.VANILLA_ISO
    g = Game(iso)
    d = g.archive("LVLS_SNG/MANS.DAT")
    targets = [e for e in d if e.ext == "mod"]
    targets.sort(key=lambda e: e.size_full)
    for e in targets[:3] + targets[len(targets) // 2:len(targets) // 2 + 2]:
        probe(e.name, d.read(e))
