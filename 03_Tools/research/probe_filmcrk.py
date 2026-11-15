#!/usr/bin/env python3
"""`.film`, `.crk`, `.ptx` -- the invariants I deferred, now measured.

I read these four formats and then wrote in UNKNOWNS.md that they stay PARTIAL
because *"I looked at it is not a decode"*.  This is the follow-through.  Three
earn DECODED; `.cm` does not and stays where it is.

    .crk   State_N numbered 1..N with no gaps            259 / 259
    .ptx   StartTextureTile <= EndTextureTile            118 / 118
    .ptx   EndTextureTile <= TilesX * TilesY             118 / 118
    .ptx   TextureName -> a .tex on the disc             118 / 118
    .film  DustMinCount <= DustMaxCount                   52 /  52
    .film  fxFilmPresetNames values -> a .film file      346 / 346

`.cm` (camera tuning) carries no count and no cross-reference I could find --
it is a fixed set of named blocks (`General`, `Follow`, `Riser`, `Swing`,
`TurnZones`, `Gatling_Cam`, `Roam_Cam`, `HorseRiser`) with float leaves.
Nothing to assert, so nothing is claimed.

**Three rules in this file were wrong on the first pass and every one of them
failed in a way that named the fix**, which is the only reason they were
caught:

  * `EndTextureTile < TilesX*TilesY` failed on 12 files, all with
    `End == TilesX*TilesY` exactly -- the bound is INCLUSIVE.  Identical
    signature to `resourceIndex` being 1-based in probe_rsc.py.
  * `fxFilmPresetNames` "values" scored 346/692 because I matched every word
    in the block, half of which are the KEYS.
  * then 0/346, because the values are QUOTED and I compared `"duelslow"`
    against `duelslow`.

Read-only.  Takes no lock.
"""
import argparse, collections, json, os, re, sys, zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from rdrstudio.iso9660 import Iso9660
from rdrstudio import dave

ISO = os.path.join(os.path.dirname(__file__), '..', '..',
                   '01_Vanilla', 'RDR_VANILLA.iso')


def num(text, key):
    m = re.search(r'^\s*%s\s+([-\d.]+)' % key, text, re.M)
    return int(float(m.group(1))) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    iso = Iso9660(ISO)
    blobs = collections.defaultdict(dict)
    tex, films = set(), set()
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b'Dave', b'DAVE'):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            n = e.name.rsplit('/', 1)[-1]
            ext = n.rsplit('.', 1)[-1].lower()
            stem = n.rsplit('.', 1)[0].lower()
            if ext == 'tex':
                tex.add(stem); continue
            if ext == 'film':
                films.add(stem)
            if ext not in ('film', 'crk', 'ptx') or n in blobs[ext]:
                continue
            raw = d.backing.read_at(e.offset, e.size_comp)
            try:
                blobs[ext][n] = (zlib.decompress(raw, -15) if e.compressed
                                 else raw)
            except zlib.error:
                pass
    iso.close()

    c = collections.defaultdict(lambda: [0, 0])
    bad = collections.defaultdict(list)

    for n, b in blobs['crk'].items():
        ids = sorted(int(m) for m in
                     re.findall(r'^\s*State_(\d+)\s*\{',
                                b.decode('latin-1'), re.M))
        hit = ids == list(range(1, len(ids) + 1))
        c['crk: State_N numbered 1..N'][0 if hit else 1] += 1
        if not hit and len(bad['crk']) < 3:
            bad['crk'].append((n, ids))

    for n, b in blobs['ptx'].items():
        t = b.decode('latin-1')
        tx, ty = num(t, 'NumTextureTilesX'), num(t, 'NumTextureTilesY')
        s0, e0 = num(t, 'StartTextureTile'), num(t, 'EndTextureTile')
        if None not in (s0, e0):
            c['ptx: StartTextureTile <= EndTextureTile'][
                0 if s0 <= e0 else 1] += 1
        if None not in (tx, ty, e0):
            c['ptx: EndTextureTile <= TilesX*TilesY'][
                0 if e0 <= tx * ty else 1] += 1
        m = re.search(r'^\s*TextureName\s+(\S+)', t, re.M)
        if m:
            v = m.group(1).strip('"').lower()
            c['ptx: TextureName -> .tex'][
                0 if (v in tex or v == 'notexture') else 1] += 1

    for n, b in blobs['film'].items():
        t = b.decode('latin-1')
        mn, mx = num(t, 'DustMinCount'), num(t, 'DustMaxCount')
        if None not in (mn, mx):
            c['film: DustMinCount <= DustMaxCount'][0 if mn <= mx else 1] += 1
        m = re.search(r'fxFilmPresetNames\s*\{(.*?)\}', t, re.S)
        if m:
            for line in m.group(1).splitlines():
                w = line.split()
                if len(w) >= 2:
                    v = w[1].strip('"').lower()
                    c['film: preset value -> a .film file'][
                        0 if v in films else 1] += 1

    res = {k: {'ok': v[0], 'bad': v[1]} for k, v in c.items()}
    if args.json:
        print(json.dumps(dict(invariants=res,
                              files={k: len(v) for k, v in blobs.items()}),
                         indent=1))
        return

    print('files: %s' % {k: len(v) for k, v in blobs.items()})
    print()
    print('%-44s %6s %6s' % ('invariant', 'ok', 'bad'))
    for k in sorted(c):
        print('%-44s %6d %6d' % (k, c[k][0], c[k][1]))
    for ext, rows in bad.items():
        for r in rows:
            print('   %s %s' % (ext, r))
    print()
    print('`.cm` carries no count and no cross-reference I could find, so it')
    print('stays PARTIAL. Nothing to assert, nothing claimed.')


if __name__ == '__main__':
    main()
