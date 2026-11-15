#!/usr/bin/env python3
"""M18 prerequisite: the RSC0 container, and the two things M18 must verify.

M18 is "add NEW models to a `.rsc`", and its acceptance test asserts that
*"the resource index and the model->texture binding still verify"*.  Adding a
chunk to a container you have mis-modelled corrupts every later reference, so
both halves want settling against the disc before that test is written.

`rsc.py` already decodes the container and BND0.  This probe checks the two
structural facts an inserter depends on, and corrects both of them.

**1. The table stores chunk ENDS, not starts.**  `rsc.py`'s docstring calls
the field `file_offset`, which reads as a start.  It is not:

    chunk i spans [previous end, offs[i]),  the first starting at header_size
    offs[-1] == the file length

623 / 623 files are consistent with that and every chunk has positive size.
Under a "starts" reading the last entry looks like a zero-length chunk sitting
at EOF, and 623 of 627 files look like they have an offset past the end -- which
is what my own first run reported.

**Why it matters for M18:** inserting a chunk is not appending an entry. Every
subsequent END offset moves, so the whole tail of the table is rewritten.

**2. `resourceIndex` is 1-BASED, and 0 means "none".**  A `.bnds`/`.znes`/
`.xznes` node's `resourceIndex` indexes this table:

    1 <= v <= count : 18,539 of 18,541   (100.0%)
    v == 0          : 2
    out of range    : 0

Read as 0-based, 59 scene files appear to reference one chunk past the end of
their own `.rsc` -- every one of them `max == count` exactly, which is the
signature of an off-by-one rather than 59 broken files.

Read-only.  Takes no lock.
"""
import argparse, collections, json, os, re, struct, sys, zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from rdrstudio.iso9660 import Iso9660
from rdrstudio import dave

ISO = os.path.join(os.path.dirname(__file__), '..', '..',
                   '01_Vanilla', 'RDR_VANILLA.iso')

MAGIC = b'RSC0'
SCENE = ('znes', 'xznes', 'bnds')


def walk(iso):
    rsc, scene = {}, collections.defaultdict(list)
    caps = collections.defaultdict(int)
    seen = set()
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b'Dave', b'DAVE'):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            ext = e.name.rsplit('.', 1)[-1].lower()
            if ext not in ('rsc',) + SCENE:
                continue
            raw = d.backing.read_at(e.offset, e.size_comp)
            try:
                b = zlib.decompress(raw, -15) if e.compressed else raw
            except zlib.error:
                continue
            if ext == 'rsc':
                if b[:4] != MAGIC:
                    continue
                cnt = struct.unpack_from('<I', b, 4)[0]
                caps[f.path] = max(caps[f.path], cnt)
                if e.name not in seen:
                    seen.add(e.name)
                    rsc[e.name] = b
            else:
                key = 'resourceIndexXbox' if ext == 'xznes' else 'resourceIndex'
                v = [int(m) for m in
                     re.findall(r'^\s*%s\s+(\d+)' % key,
                                b.decode('latin-1'), re.M)]
                if v:
                    scene[f.path].append((e.name, v))
    return rsc, scene, caps


def table(b):
    cnt, hsize = struct.unpack_from('<II', b, 4)
    ents = [struct.unpack_from('<4sI', b, 12 + 8 * i) for i in range(cnt)]
    return cnt, hsize, ents


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    iso = Iso9660(ISO)
    rsc, scene, caps = walk(iso)
    iso.close()

    tags = collections.Counter()
    ends_ok = ends_bad = 0
    pos_ok = 0
    mono = align = 0
    fits = 0
    zero = 0
    hsizes = collections.Counter()
    bad = []

    for name, b in sorted(rsc.items()):
        cnt, hsize, ents = table(b)
        hsizes[hsize] += 1
        if cnt == 0:
            zero += 1
            continue
        offs = [o for _, o in ents]
        for t, _ in ents:
            tags[t.decode('latin-1')] += 1
        if offs == sorted(offs):
            mono += 1
        if all(o % 16 == 0 for o in offs):
            align += 1
        if 12 + 8 * cnt <= hsize:
            fits += 1
        prev, good, allpos = hsize, True, True
        for o in offs:
            if o < prev:
                good = False
            if o - prev <= 0:
                allpos = False
            prev = o
        if good and offs[-1] == len(b):
            ends_ok += 1
        else:
            ends_bad += 1
            if len(bad) < 5:
                bad.append((name, hsize, offs[:3], offs[-1], len(b)))
        if allpos:
            pos_ok += 1

    # resourceIndex, 1-based against the archive's largest RSC0 count
    z = one = out = 0
    over0 = []
    for arc, items in scene.items():
        cap = caps.get(arc, 0)
        if not cap:
            continue
        for nm, vals in items:
            for v in vals:
                if v == 0:
                    z += 1
                elif 1 <= v <= cap:
                    one += 1
                else:
                    out += 1
            if max(vals) == cap and len(over0) < 6:
                over0.append((arc, nm, max(vals), cap))
    tot = z + one + out

    res = dict(rsc_files=len(rsc), zero_count=zero,
               header_sizes=dict(hsizes.most_common(6)),
               table_fits_header=fits,
               offsets_monotonic=mono, offsets_16_aligned=align,
               ends_model=[ends_ok, ends_bad], all_chunks_positive=pos_ok,
               bad=bad, tags=dict(tags.most_common(16)),
               resourceIndex={'zero': z, 'in_1..count': one,
                              'out_of_range': out, 'total': tot},
               would_overrun_if_0_based=over0)
    if args.json:
        print(json.dumps(res, indent=1)); return

    n = len(rsc) - zero
    print('.rsc distinct: %d  (count==0 on %d)' % (len(rsc), zero))
    print('header_size values: %s' % dict(hsizes.most_common(5)))
    print()
    print('CONTAINER INVARIANTS')
    print('   offsets non-decreasing            %d / %d' % (mono, n))
    print('   offsets 16-byte aligned           %d / %d' % (align, n))
    print('   table fits inside header_size     %d / %d' % (fits, n))
    print('   ENDS model consistent, last==EOF  %d / %d' % (ends_ok, n))
    print('   every chunk has positive size     %d / %d' % (pos_ok, n))
    for x in bad:
        print('      %s' % (x,))
    print()
    print('RESOURCE INDEX -- 1-based, 0 means none')
    print('   1 <= v <= count : %6d / %d  (%.1f%%)'
          % (one, tot, 100 * one / tot if tot else 0))
    print('   v == 0          : %6d' % z)
    print('   out of range    : %6d' % out)
    print('   read as 0-based, these would look like overruns'
          ' (max == count exactly):')
    for a, nm, mx, cap in over0[:4]:
        print('      %-20s %-30s max %d, count %d' % (a, nm, mx, cap))
    print()
    print('chunk tags: %s' % dict(tags.most_common(14)))


if __name__ == '__main__':
    main()
