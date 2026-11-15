#!/usr/bin/env python3
"""`.acttbl` -- the weighted action table, and 47 behaviours that were cut.

832 entries, 536 distinct.  Not blocked by REQ-021: that closed the MIPS
disassembly of the behaviour opcode dispatch, which is a different question
from what this file does.  `.acttbl` is plain text and says what it is.

    columns 6
    0.2  0.2  0.2  0.1  0.1  0.2            <- weights, one per column
    Goto_Balc4  Goto_Balc6  ... Goto_Balc12  <- row 1
    Goto_Balc4  Goto_Balc6  ... Goto_Balc12  <- row 2
    ... 16 rows

A weighted random-selection table: pick a column by weight, and the row gives
the `.actb` behaviour to run.  What the 16 rows index is not settled here.

    columns N == weight count                  536 / 536
    every row has N entries                    536 / 536
    weights sum to 1.0                         533 / 536
    16 rows                                    523 / 536
    action names resolve to a .actb        33,706 / 34,308  (98.2%)

The last line is the interesting one: **47 distinct behaviour names have no
`.actb` on the disc**, and they are not scattered -- 12 of 536 files hold all
of them.  See `cut_behaviours()`.

Read-only.  Takes no lock.
"""
import argparse, collections, json, os, sys, zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from rdrstudio.iso9660 import Iso9660
from rdrstudio import dave

ISO = os.path.join(os.path.dirname(__file__), '..', '..',
                   '01_Vanilla', 'RDR_VANILLA.iso')


def gather(iso):
    tbl, actb = {}, set()
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b'Dave', b'DAVE'):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            n = e.name.rsplit('/', 1)[-1]
            ext = n.rsplit('.', 1)[-1].lower()
            if ext == 'actb':
                actb.add(n.rsplit('.', 1)[0].lower())
            elif ext == 'acttbl' and n not in tbl:
                raw = d.backing.read_at(e.offset, e.size_comp)
                try:
                    tbl[n] = (zlib.decompress(raw, -15) if e.compressed
                              else raw)
                except zlib.error:
                    pass
    return tbl, actb


def parse(text):
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines or not lines[0].lower().startswith('columns'):
        return None
    cols = int(lines[0].split()[1])
    weights = lines[1].split() if len(lines) > 1 else []
    rows = [l.split() for l in lines[2:]]
    return cols, weights, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    iso = Iso9660(ISO)
    tbl, actb = gather(iso)
    iso.close()

    c = collections.defaultdict(lambda: [0, 0])
    rowcount = collections.Counter()
    wbad = []
    hit = miss = 0
    unres = collections.Counter()
    where = collections.defaultdict(set)

    for name, b in sorted(tbl.items()):
        got = parse(b.decode('latin-1'))
        if got is None:
            c['has a `columns` header'][1] += 1
            continue
        c['has a `columns` header'][0] += 1
        cols, weights, rows = got
        c['columns N == weight count'][0 if len(weights) == cols else 1] += 1
        c['every row has N entries'][
            0 if all(len(r) == cols for r in rows) else 1] += 1
        try:
            s = sum(float(x) for x in weights)
            ok = abs(s - 1.0) < 1e-3
            c['weights sum to 1.0'][0 if ok else 1] += 1
            if not ok:
                wbad.append((name, cols, round(s, 4)))
        except ValueError:
            c['weights sum to 1.0'][1] += 1
        rowcount[len(rows)] += 1
        for r in rows:
            for a in r:
                if a.lower() in actb:
                    hit += 1
                else:
                    miss += 1
                    unres[a] += 1
                    where[a].add(name)

    files_hit = sorted(set().union(*where.values())) if where else []
    res = dict(files=len(tbl),
               invariants={k: {'ok': v[0], 'bad': v[1]} for k, v in c.items()},
               row_counts=dict(rowcount.most_common(6)),
               weight_outliers=wbad,
               refs=[hit, hit + miss],
               cut_behaviours=len(unres),
               cut_files=files_hit,
               cut_names=sorted(unres))
    if args.json:
        print(json.dumps(res, indent=1)); return

    print('.acttbl distinct: %d' % len(tbl))
    print('%-34s %6s %6s' % ('invariant', 'ok', 'bad'))
    for k in sorted(c):
        print('%-34s %6d %6d' % (k, c[k][0], c[k][1]))
    print('row counts: %s' % dict(rowcount.most_common(6)))
    print('weight-sum outliers: %s' % wbad)
    print()
    print('action names -> a .actb file: %d / %d  (%.1f%%)'
          % (hit, hit + miss, 100 * hit / (hit + miss) if hit + miss else 0))
    print('CUT BEHAVIOURS: %d distinct names with no .actb, in %d of %d files'
          % (len(unres), len(files_hit), len(tbl)))
    for fn in files_hit:
        names = sorted(a for a in unres if fn in where[a])
        print('   %-38s %d: %s' % (fn, len(names), ', '.join(names[:4])))


if __name__ == '__main__':
    main()
