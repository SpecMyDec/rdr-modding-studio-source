#!/usr/bin/env python3
"""Skinned character display lists: the header words, decoded, and a batch
encoder that reproduces the vanilla disc byte-for-byte.

    python3 scripts/test_skinvif.py          (reads 01_Vanilla, writes nothing)

For every rigged (bone-count > 0) MOD0 in every unique *_lods.rsc:

  1. each narrow batch decodes to fields and `skinvif.encode_batch` rebuilds it
     BYTE-IDENTICALLY -- header qword (6, nrec, 0x1116|(N+26)<<16,
     B|(36+ceil(7N/4))<<16) and GIF tag included, all derived by rule, not
     carried;
  2. the secondary-influence block sits at itop + 176 - 2*nrec;
  3. the VU buffer base cycles base, base+S, base+2S with S=(1024-base)//3;
  4. the rigged container rebuilds byte-identically;
  5. whole part packets rebuild identically from prologue + encoded batches +
     epilogue (the 452 wide-position batches are copied through, not encoded).
Evidence write-up: 05_Docs/SKINNED-HEADER-DECODE.md.
"""
import collections
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from rdrstudio import dave, skinvif as S                       # noqa: E402
from rdrstudio.iso9660 import Iso9660                          # noqa: E402
from rdrstudio.rsc import MAGIC, Rsc                           # noqa: E402

ISO = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..',
                   '01_Vanilla', 'RDR_VANILLA.iso')
FAILURES = []


def check(ok, what, detail=""):
    print(("  ok   " if ok else "  FAIL ") + what
          + (("  -- " + str(detail)) if detail and not ok else ""))
    if not ok:
        FAILURES.append(what)
    return ok


def models(iso):
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
            if b[:4] != MAGIC:
                continue
            h = zlib.crc32(b)
            if h in seen:
                continue
            seen.add(h)
            try:
                yield e.name, Rsc(b, e.name)
            except ValueError:
                continue


def main():
    iso = Iso9660(ISO)
    nb = ident = wide = 0
    hdr_ok = extra_ok = itop_ok = 0
    cont = cont_ok = 0
    parts = parts_ok = 0
    bad = collections.Counter()
    try:
        for name, r in models(iso):
            for ch in r.chunks:
                if ch.tag != "MOD0" or not r.parts(ch.index):
                    continue
                raw = r.raw(ch.index)
                if not raw[1]:
                    continue
                bones = raw[1]
                cont += 1
                try:
                    cont_ok += S.build_container(S.parse_container(raw)) == raw
                except S.Unrecognised:
                    pass
                for p in r.parts(ch.index):
                    parts += 1
                    try:
                        pro, bl, epi, toks, pe = S.split_packet(
                            raw, p["offset"], p["end"])
                    except S.Unrecognised:
                        bad["split"] += 1
                        continue
                    rebuilt = bytearray(pro)
                    for k, tl in enumerate(bl):
                        nb += 1
                        orig = raw[tl[0].offset:tl[-1].end]
                        d = S.decode_batch(raw, tl)
                        want_itop = S.base_addr(bones) + (k % 3) * S.stride(bones)
                        itop_ok += d["itop"] == want_itop
                        if d["pos_wide"]:
                            wide += 1
                            rebuilt += orig
                            continue
                        out = S.encode_batch(d, tl[0].offset)
                        ident += out == orig
                        rebuilt += out
                        hdr_ok += (d["hdr"] == S.header_words(
                            d["n"], d["nrec"], len(orig) // 4))
                        if d["nrec"]:
                            extra_ok += (d["extra_addr"]
                                         == d["itop"] + 176 - 2 * d["nrec"])
                        else:
                            extra_ok += 1
                    rebuilt += epi
                    parts_ok += bytes(rebuilt) == raw[p["offset"]:pe]
    finally:
        iso.close()
    narrow = nb - wide
    print("%d batches (%d narrow, %d wide), %d parts, %d containers"
          % (nb, narrow, wide, parts, cont))
    check(nb > 15000, "walked %d batches" % nb)
    check(ident == narrow, "narrow batches rebuilt byte-identical %d / %d"
          % (ident, narrow))
    check(hdr_ok == narrow, "header words follow the rule %d / %d"
          % (hdr_ok, narrow))
    check(extra_ok == narrow, "influence block at itop+176-2*nrec %d / %d"
          % (extra_ok, narrow))
    check(itop_ok == nb, "VU buffer base cycles base, +S, +2S %d / %d"
          % (itop_ok, nb))
    check(cont_ok == cont, "rigged containers rebuilt byte-identical %d / %d"
          % (cont_ok, cont))
    check(parts_ok == parts, "whole part packets rebuilt byte-identical "
          "%d / %d" % (parts_ok, parts))
    check(not bad, "no unsplittable packet", dict(bad))
    print("\n%s" % ("FAIL: " + "; ".join(FAILURES) if FAILURES else "PASS"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
