#!/usr/bin/env python3
"""`.rnt` -- the chunk-name manifest. Its layout is FIXED, not variable.

`.rnt` gives the chunks in a `.rsc` their artist names, which is what stops a
tool handing you a disembodied head when you ask for "LOD 2".  1,226 entries,
769 distinct.

`rsc.py`'s `parse_rnt` reads it with a deliberately tolerant scan, and says
why: *"the entry field order varies within the file"*.  **It does not.**
Measured across every `.rnt` on the disc:

    0x00  "RNT0"
    0x04  u32 count
    0x08  16 bytes, zero                                   769 / 769
    0x18  count x { u32 name_offset, char[4] tag, u32 index }
          name block at exactly 0x18 + 12*count            607 / 607
          every name_offset lands on a string start        769 / 769

(The other 162 files declare `count == 0` and carry no entries at all -- an
empty manifest, not a mismatch.  Reading them as mismatches is what made my
own first run report 607/769 instead of 607/607.)

**What the tolerance costs.** The scan anchors on `rsc.py`'s `RNT_TAGS`, which
is missing two tags that occur in real entries -- `GAI0` (457 of them) and
`FNT0` (2).  So:

    entries by the fixed layout : 6,181
    entries parse_rnt returns   : 5,719   (92.5%)

459 entries carry a tag the set does not list.  A fixed-offset reader needs no
tag list at all and returns all 6,181.

Read-only.  Takes no lock.
"""
import argparse, collections, json, os, struct, sys, zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from rdrstudio.iso9660 import Iso9660
from rdrstudio import dave, rsc

ISO = os.path.join(os.path.dirname(__file__), '..', '..',
                   '01_Vanilla', 'RDR_VANILLA.iso')

HDR = 0x18          # magic + count + 16 zero bytes
ENT = 12            # name_offset, tag, index


def rnts(iso):
    seen = set()
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b'Dave', b'DAVE'):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            if not e.name.lower().endswith('.rnt') or e.name in seen:
                continue
            seen.add(e.name)
            raw = d.backing.read_at(e.offset, e.size_comp)
            try:
                b = zlib.decompress(raw, -15) if e.compressed else raw
            except zlib.error:
                continue
            yield f.path, e.name, b


def entries(b):
    """The fixed layout. Returns (count, [(name_offset, tag, index)])."""
    cnt = struct.unpack_from('<I', b, 4)[0]
    out = []
    for i in range(cnt):
        o = HDR + ENT * i
        if o + ENT > len(b):
            break
        out.append(struct.unpack_from('<I4sI', b, o))
    return cnt, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    iso = Iso9660(ISO)
    files = empty = 0
    pad_ok = names_ok = starts_ok = 0
    nonempty = 0
    tags = collections.Counter()
    unknown = collections.Counter()
    fixed_total = parser_total = 0
    short_files = []

    for arc, name, b in rnts(iso):
        if b[:4] != b'RNT0':
            continue
        files += 1
        cnt, ents = entries(b)
        if bytes(b[8:HDR]) == b'\x00' * (HDR - 8):
            pad_ok += 1
        for _, t, _ in ents:
            tag = t.decode('latin-1', 'replace')
            tags[tag] += 1
            if t not in rsc.RNT_TAGS:
                unknown[tag] += 1
        if cnt == 0:
            empty += 1
            continue
        if len(ents) != cnt:
            continue
        nonempty += 1
        offs = [o for o, _, _ in ents]
        if min(offs) == HDR + ENT * cnt:
            names_ok += 1
        if all(o == HDR + ENT * cnt or (0 < o < len(b) and b[o - 1] == 0)
               for o in offs):
            starts_ok += 1
        fixed_total += len(ents)
        got = rsc.parse_rnt(b)
        parser_total += len(got)
        if len(got) < len(ents):
            short_files.append((name, len(ents), len(got)))

    iso.close()
    res = dict(files=files, empty=empty, nonempty=nonempty,
               pad_zero=pad_ok, name_block_exact=names_ok,
               offsets_at_string_start=starts_ok,
               fixed_entries=fixed_total, parse_rnt_entries=parser_total,
               tags=dict(tags.most_common()),
               tags_missing_from_RNT_TAGS=dict(unknown),
               files_parser_returns_fewer=short_files[:8])
    if args.json:
        print(json.dumps(res, indent=1)); return

    print('.rnt distinct files: %d   (%d declare count == 0)' % (files, empty))
    print()
    print('THE LAYOUT IS FIXED')
    print('   16 bytes at +8 are zero             %d / %d' % (pad_ok, files))
    print('   name block at 0x18 + 12*count       %d / %d' % (names_ok, nonempty))
    print('   every name_offset at a string start %d / %d'
          % (starts_ok, nonempty))
    print()
    print('WHAT THE TOLERANT SCAN COSTS')
    print('   entries by the fixed layout : %d' % fixed_total)
    print('   entries parse_rnt returns   : %d  (%.1f%%)'
          % (parser_total, 100 * parser_total / fixed_total
             if fixed_total else 0))
    print('   tags in real entries but absent from rsc.RNT_TAGS: %s'
          % dict(unknown))
    for n, a, g in short_files[:6]:
        print('      %-24s has %3d entries, parser returns %3d' % (n, a, g))
    print()
    print('tags seen: %s' % dict(tags.most_common(10)))


if __name__ == '__main__':
    main()
