#!/usr/bin/env python3
"""Character display lists tile 100% -- the "41 failures" were a trailer.

    python3 scripts/test_vifwalk_chars.py

a9689d0 reported the VIF walker tiling 172 / 213 character parts and blamed
"VIFcodes 0x0B/0x0C". Those are not commands. They are the byte values 11 and
12 in an ascending index list that follows the LAST packet of a rigid
(bone-count 0) model -- the walker ran off the end of the packet's FLUSH into
data that is not a VIF stream. `vifwalk.walk_packet` ends a packet where the
hardware does, at the FLUSH, and `vifwalk.trailer` recognises what follows.

Asserted over every `*_lods.rsc` on the disc (reads 01_Vanilla, writes nothing):

  * every part's packet tiles with no gap or overlap, and re-emitting the
    tokens reproduces its bytes exactly;
  * what is left after the packet is either nothing or a trailer that opens
    with the four-pointer table, and only the LAST part of a bone-count-0
    model has one -- no mid-packet FLUSH exists, which is what makes
    "stop at the FLUSH" a rule instead of a guess.

What this does NOT do: write a skinned mesh. See CAPABILITIES.md.
"""
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from rdrstudio import dave, vifwalk                # noqa: E402
from rdrstudio.iso9660 import Iso9660              # noqa: E402
from rdrstudio.rsc import MAGIC, Rsc               # noqa: E402

ISO = os.path.join(os.path.dirname(__file__), '..', '..',
                   '01_Vanilla', 'RDR_VANILLA.iso')
FAILURES = []


def check(ok, what, detail=""):
    print(("  ok   " if ok else "  FAIL ") + what
          + (("  -- " + detail) if detail and not ok else ""))
    if not ok:
        FAILURES.append(what)
    return ok


def bundles(iso):
    seen = set()
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b'Dave', b'DAVE'):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            if not e.name.lower().endswith('_lods.rsc'):
                continue
            raw = d.backing.read_at(e.offset, e.size_comp)
            try:
                b = zlib.decompress(raw, -15) if e.compressed else raw
            except zlib.error:
                continue
            if b[:4] != MAGIC or struct.unpack_from("<I", b, 4)[0] == 0:
                continue
            h = zlib.crc32(b)
            if h in seen:
                continue
            seen.add(h)
            yield e.name, b


def main():
    iso = Iso9660(ISO)
    parts = tiled = ident = 0
    tr_total = tr_last = tr_rigged = tr_bad = 0
    old_ok = 0
    try:
        for name, b in bundles(iso):
            try:
                r = Rsc(b, name)
            except ValueError:
                continue
            for ch in r.chunks:
                if ch.tag != "MOD0":
                    continue
                ps = r.parts(ch.index)
                if not ps:
                    continue
                raw = r.raw(ch.index)
                for i, p in enumerate(ps):
                    parts += 1
                    try:
                        vifwalk.walk(raw, p["offset"], p["end"])
                        old_ok += 1
                    except vifwalk.Unknown:
                        pass
                    try:
                        toks, pe = vifwalk.walk_packet(raw, p["offset"], p["end"])
                    except (vifwalk.Unknown, ValueError):
                        continue
                    cov = sum(4 + t.data_len for t in toks)
                    pos = p["offset"]
                    gaps = 0
                    for t in toks:
                        gaps += t.offset != pos
                        pos = t.end
                    zeros = raw[toks[-1].end:pe].count(0) == pe - toks[-1].end
                    tiled += (gaps == 0 and zeros
                              and cov + (pe - toks[-1].end) == pe - p["offset"])
                    out = b"".join(vifwalk.emit(t, raw[t.data_start:t.end])
                                   for t in toks) + raw[toks[-1].end:pe]
                    ident += out == raw[p["offset"]:pe]
                    try:
                        tr = vifwalk.trailer(raw, pe, p["end"])
                    except ValueError:
                        tr_bad += 1
                        continue
                    if tr:
                        tr_total += 1
                        tr_last += i == len(ps) - 1
                        tr_rigged += bool(raw[1])
    finally:
        iso.close()

    print("%d character parts" % parts)
    check(parts > 600, "walked %d parts in unique *_lods.rsc bundles" % parts)
    print("  (the old walk() tiled %d of them)" % old_ok)
    check(tiled == parts, "walk_packet tiles %d / %d" % (tiled, parts))
    check(ident == parts, "re-emitted packets byte-identical %d / %d"
                          % (ident, parts))
    check(parts - old_ok == tr_total,
          "the %d parts the old walk() could not tile are exactly the %d with "
          "a trailer" % (parts - old_ok, tr_total))
    check(tr_bad == 0, "no unrecognised remainder after a FLUSH (%d)" % tr_bad)
    check(tr_total > 50 and tr_total == tr_last,
          "%d trailers, every one after a model's LAST part" % tr_total)
    check(tr_rigged == 0,
          "no trailer on a rigged (bone-count > 0) model (%d)" % tr_rigged)
    print("\n%s" % ("FAIL: " + "; ".join(FAILURES) if FAILURES else "PASS"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
