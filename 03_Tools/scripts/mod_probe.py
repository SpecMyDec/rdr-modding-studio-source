"""Validate the .mod vertex-block hypothesis, then dump what follows.

HYPOTHESIS (from hud_arrow_lod_01.mod, 4 verts / 2 tris):
    0x00  "version: 2.12\0"          14 bytes
    0x0E  11 x u32 header
    0x3A  positions  h[0] x vec3
          normals    h[1] x vec3
          colors     h[2] x vec4
          uvs        h[3] x vec2
          <-- float block ends here, a material name string starts

ORACLE: if the hypothesis is right, the computed end offset lands exactly on
a printable ASCII string in EVERY model on the disc. That is a very strong
signal -- a wrong stride would land mid-float almost every time.
"""

import collections
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rdrstudio import Game          # noqa: E402
from rdrstudio import config as cfg  # noqa: E402

HDR = 14
BODY = HDR + 44


def vertex_block_end(h):
    """Byte offset just past the float attribute block."""
    return BODY + (h[0] * 3 + h[1] * 3 + h[2] * 4 + h[3] * 2) * 4


def looks_like_name(data, off, n=24):
    """Printable, lowercase-ish run at off -> the material/object name."""
    if off >= len(data):
        return None
    out = []
    for i in range(off, min(off + n, len(data))):
        c = data[i]
        if 32 <= c < 127:
            out.append(chr(c))
        else:
            break
    s = "".join(out)
    return s if len(s) >= 3 else None


def main():
    iso = sys.argv[1] if len(sys.argv) > 1 else cfg.VANILLA_ISO
    g = Game(iso)

    archives = ["SWAT.DAT"] + [a for a in g.archives() if a.startswith("LVLS_SNG")]
    ok = bad = 0
    fails = []
    versions = collections.Counter()
    h45 = collections.Counter()
    samples = []

    for arc in archives:
        d = g.archive(arc)
        for e in d:
            if e.ext not in ("mod", "modb"):
                continue
            try:
                data = d.read(e)
            except Exception:
                continue
            if not data.startswith(b"version:"):
                fails.append((arc, e.name, "no version tag"))
                bad += 1
                continue
            versions[data[:13].decode("latin-1", "replace")] += 1
            if len(data) < BODY:
                continue
            h = struct.unpack_from("<11I", data, HDR)
            h45[(h[4], h[5])] += 1

            end = vertex_block_end(h)
            name = looks_like_name(data, end)
            if name:
                ok += 1
                if len(samples) < 6 and h[0] > 8:
                    samples.append((arc, e.name, h, end, name, data))
            else:
                bad += 1
                if len(fails) < 25:
                    ctx = data[max(0, end - 8):end + 16].hex(" ") if end < len(data) else "past EOF"
                    fails.append((arc, e.name, "end=0x%X of %d  ctx=%s" % (end, len(data), ctx)))
        g._cache.pop(arc.upper(), None)

    total = ok + bad
    print("=" * 72)
    print("models tested      : %d" % total)
    print("vertex block ends on a name string : %d (%.2f%%)"
          % (ok, 100.0 * ok / max(1, total)))
    print("mismatches         : %d" % bad)
    print("\nversion tags: %s" % dict(versions))
    print("header[4],[5] values seen: %s" % dict(h45.most_common(8)))

    if fails:
        print("\nfirst mismatches:")
        for a, n, m in fails[:20]:
            print("   %-22s %-30s %s" % (a, n, m))

    print("\n" + "=" * 72)
    print("TAIL DUMPS (what follows the float block)")
    for arc, name, h, end, sname, data in samples:
        print("\n--- %s :: %s" % (arc, name))
        print("    header=%s  len=%d  block_end=0x%X  name=%r"
              % (list(h), len(data), end, sname))
        tail = data[end:]
        print("    tail is %d bytes (%.1f%% of file)" % (len(tail), 100.0 * len(tail) / len(data)))
        for i in range(0, min(len(tail), 160), 16):
            chunk = tail[i:i + 16]
            asc = "".join(chr(c) if 32 <= c < 127 else "." for c in chunk)
            print("    %04X  %-47s  %s" % (end + i, chunk.hex(" "), asc))


if __name__ == "__main__":
    main()
