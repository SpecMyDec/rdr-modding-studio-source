#!/usr/bin/env python3
"""Research: what the skinned character batch header words mean.

    python3 research/probe_skinned_header.py [--csv out.csv]

Reads 01_Vanilla only. For every batch of every rigged (bone-count > 0) MOD0 in
every unique *_lods.rsc it records the header qword (the V4_32 num=1 UNPACK at
the batch base) and the batch's measurable features, then reports which header
fields are determined by which features.
"""
import collections, struct, sys, zlib, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from rdrstudio import dave, vifwalk
from rdrstudio.iso9660 import Iso9660
from rdrstudio.rsc import MAGIC, Rsc
ISO = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '01_Vanilla', 'RDR_VANILLA.iso')


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
                r = Rsc(b, e.name)
            except ValueError:
                continue
            yield e.name, r


def batches(raw, p):
    """Yield per-batch dicts for one part packet."""
    toks, pe = vifwalk.walk_packet(raw, p['offset'], p['end'])
    cur = None
    for t in toks:
        if t.name == 'STCYCL' and cur is None:
            cur = dict(unp=[], start=t.offset)
        if cur is None:
            continue
        if t.is_unpack:
            cur['unp'].append(t)
        if t.name == 'MSCAL' and t.imm == 0 and any(u.addr_ok for u in []) :
            pass
        if t.name == 'ITOP':
            cur['itop'] = t.imm
        if t.name == 'MSCAL' and 'itop' in cur:
            cur['end'] = t.end
            yield cur
            cur = None


def main():
    csv = None
    if '--csv' in sys.argv:
        csv = open(sys.argv[sys.argv.index('--csv') + 1], 'w')
    iso = Iso9660(ISO)
    rows = []
    try:
        for name, r in models(iso):
            for ch in r.chunks:
                if ch.tag != 'MOD0':
                    continue
                ps = r.parts(ch.index)
                if not ps:
                    continue
                raw = r.raw(ch.index)
                bones = raw[1]
                if not bones:
                    continue
                for pi, p in enumerate(ps):
                    nb = 0
                    for b in batches(raw, p):
                        unp = b['unp']
                        hdr = None
                        feats = dict(model=name, chunk=ch.index, bones=bones, part=pi)
                        for u in unp:
                            addr = u.imm & 0x3ff
                            if u.vn == 3 and u.vl == 0 and u.num == 1 and hdr is None and addr == b['itop']:
                                hdr = struct.unpack_from('<4I', raw, u.data_start)
                                feats['gif'] = struct.unpack_from('<4I', raw, u.data_start + 0) if False else None
                        # header is first V4_32 num=1 at itop; GIF tag is the next
                        v4 = [u for u in unp if u.vn == 3 and u.vl == 0 and u.num == 1 and (u.imm & 0x3ff) in (b['itop'], b['itop'] + 1)]
                        if len(v4) < 2:
                            continue
                        hdr = struct.unpack_from('<4I', raw, v4[0].data_start)
                        gif = struct.unpack_from('<4I', raw, v4[1].data_start)
                        pos = [u for u in unp if u.vn == 3 and u.vl == 1]
                        wide = [u for u in unp if u.vn == 3 and u.vl == 0 and u.num > 1]
                        if not pos and wide:
                            pos = wide[:1]
                            wide = wide[1:]
                        if not pos:
                            continue
                        n = pos[0].num
                        extra = sum(u.num for u in wide)
                        rows.append(dict(model=name, chunk=ch.index, bones=bones, part=pi,
                                         w0=hdr[0], w1=hdr[1], first=hdr[2] & 0xffff, A=hdr[2] >> 16,
                                         B=hdr[3] & 0xffff, C=hdr[3] >> 16, N=n, extra=extra,
                                         nlook=gif[0] & 0x7fff, gif1=gif[1], gif2=gif[2],
                                         wide=int(pos[0].vl == 0), nb=nb,
                                         nun=len(unp), size=b['end'] - b['start'], itop=b['itop']))
                        nb += 1
    finally:
        iso.close()
    keys = list(rows[0].keys())
    if csv:
        csv.write(','.join(keys) + '\n')
        for r in rows:
            csv.write(','.join(str(r[k]) for k in keys) + '\n')
        csv.close()
    print('%d batches in %d models' % (len(rows), len({r['model'] + str(r['chunk']) for r in rows})))
    C = collections.Counter
    for k in ('w0', 'first', 'gif1', 'gif2'):
        print(k, C(r[k] for r in rows).most_common(12))
    print('A == N+26:', sum(r['A'] == r['N'] + 26 for r in rows), '/', len(rows))
    print('nloop == N:', sum(r['nlook'] == r['N'] for r in rows), '/', len(rows))
    print('w1 values:', C(r['w1'] for r in rows).most_common(35))
    return rows


if __name__ == '__main__':
    main()
