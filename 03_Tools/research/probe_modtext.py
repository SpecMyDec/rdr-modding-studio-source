#!/usr/bin/env python3
"""M15 prerequisite: `.mod` has a HUMAN-READABLE dialect, and 251 files use it.

M15 is the `.mod` importer for weapon models.  `.mod` was PARTIAL with 8,600
entries (3,008 distinct names) behind `rsc.py` / `vif.py`, which read the
binary PS2 display lists.

There are **four container versions**, and they are not one format:

    version: 2.12   2,635 files   binary
    version: 1.10     251 files   **plain ASCII, and complete**
    version: 2.10      62 files   binary
    version: 2.00      60 files   binary

The 251 are an OBJ-like text mesh: a header that declares every count, then
one record per line.  **A model in this game can be read, diffed and written
in a text editor**, which is a different proposition for M15/M18 than emitting
VIF display lists.

    version: 1.10
    verts: 942      normals: 1185   colors: 1       tex1s: 741
    tex2s: 0        tangents: 0     materials: 1    adjuncts: 1383
    primitives: 1848                matrices: 52    reskins: 3

    v  0.000000 -0.023228  0.120881      -- vertex
    n  ...                               -- normal
    c  ...                               -- colour
    t1 ...                               -- texcoord set 1
    mtl <name> { packets: 7  primitives: 188  textures: 0
                 illum: diffuse  ambient: ...  diffuse: ...  specular: ... }
    packet 52 13 1 { adj  0  0  0  0 -1  0
                     ... }                -- 52 `adj` lines
    reskin 43 2 0.50 -0.003122 0.636476 0.003841

What is verified here, and what is not, is kept apart deliberately -- three of
the eleven header counts do NOT match any rule I tested and are reported open
rather than guessed at.

Read-only.  Takes no lock.
"""
import argparse, collections, json, os, re, sys, zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from rdrstudio.iso9660 import Iso9660
from rdrstudio import dave

ISO = os.path.join(os.path.dirname(__file__), '..', '..',
                   '01_Vanilla', 'RDR_VANILLA.iso')

# header key -> the line tag it counts.  Verified 251/251 each.
VERIFIED = {'verts': 'v', 'normals': 'n', 'colors': 'c', 'tex1s': 't1',
            'tex2s': 't2', 'tangents': 'tan', 'materials': 'mtl'}
# header keys that do NOT equal a simple count -- see `open_questions`.
OPEN = ('adjuncts', 'matrices', 'reskins')


def mods(iso):
    seen = set()
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b'Dave', b'DAVE'):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            if not e.name.lower().endswith('.mod') or e.name in seen:
                continue
            seen.add(e.name)
            raw = d.backing.read_at(e.offset, e.size_comp)
            try:
                b = zlib.decompress(raw, -15) if e.compressed else raw
            except zlib.error:
                continue
            yield f.path, e.name, b


def scan(text):
    """Header scalars, top-level tag counts, mtl `packets:`, packet blocks."""
    hdr, tags = {}, collections.Counter()
    mtl_packets, packets = [], []
    cur = None
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        w = s.split()
        if w[0] == 'packet' and s.endswith('{'):
            cur = ([int(x) for x in w[1:-1]], collections.Counter())
            continue
        if s == '}' and cur is not None:
            packets.append(cur); cur = None
            continue
        if cur is not None:
            cur[1][w[0]] += 1
            continue
        if w[0].endswith(':') and len(w) > 1:
            key = w[0][:-1]
            try:
                val = int(w[1])
            except ValueError:
                continue
            if key == 'packets':
                mtl_packets.append(val)
            else:
                hdr.setdefault(key, val)
            continue
        tags[w[0]] += 1
    return hdr, tags, mtl_packets, packets


def weapon_dialects(iso):
    """Which dialect do the WEAPON models use?  M15's actual question.

    `.guns` names every weapon's `ModelName`, so the answer comes from the
    game's own data rather than from guessing which files look like guns.
    Filed first without this check, which was a premise I should have tested
    before writing the request.
    """
    guns, ver = [], {}
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b'Dave', b'DAVE'):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            n = e.name.lower()
            if n.endswith('.guns'):
                raw = d.backing.read_at(e.offset, e.size_comp)
                try:
                    guns.append(zlib.decompress(raw, -15) if e.compressed
                                else raw)
                except zlib.error:
                    pass
            elif n.endswith('.mod') and n not in ver:
                raw = d.backing.read_at(e.offset, min(e.size_comp, 4096))
                try:
                    h = (zlib.decompressobj(-15).decompress(raw, 32)
                         if e.compressed else raw[:32])
                except zlib.error:
                    continue
                m = re.match(rb'version: ([0-9.]+)', h)
                ver[n] = m.group(1).decode() if m else '?'

    pairs = set()
    for b in guns:
        cur = None
        for line in b.decode('latin-1').splitlines():
            w = line.strip().split()
            if not w:
                continue
            if w[0] == 'TypeName':
                cur = w[1]
            elif w[0] == 'ModelName' and cur and \
                    w[1].lower() not in ('<none>', 'none'):
                pairs.add((cur, w[1].lower()))

    types = collections.Counter()
    models = collections.defaultdict(set)
    for tn, mn in pairs:
        v = ver.get(mn + '.mod', '?')
        key = 'text 1.10' if v.startswith('1.1') else 'binary ' + v
        types[key] += 1
        models[key].add(mn)
    return types, {k: sorted(v) for k, v in models.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    iso = Iso9660(ISO)
    wtypes, wmodels = weapon_dialects(iso)
    versions = collections.Counter()
    text_files = []
    for arc, name, b in mods(iso):
        m = re.match(rb'version: ([0-9.]+)', b)
        v = m.group(1).decode() if m else '?'
        versions[v] += 1
        if v.startswith('1.1'):
            text_files.append((name, b))
    iso.close()

    checks = collections.defaultdict(lambda: [0, 0])
    pk_ok = pk_n = 0
    mtl_ok = mtl_n = 0
    ascii_ok = 0
    openq = collections.defaultdict(lambda: [0, 0])

    for name, b in text_files:
        if all(c < 128 for c in b):
            ascii_ok += 1
        hdr, tags, mtl_packets, packets = scan(b.decode('ascii', 'replace'))

        for key, tag in VERIFIED.items():
            if key in hdr:
                checks[key][0 if hdr[key] == tags.get(tag, 0) else 1] += 1

        # `packet N ... {` -- N is the number of `adj` lines it contains
        for head, inner in packets:
            pk_n += 1
            if head and head[0] == inner.get('adj', 0):
                pk_ok += 1

        # every material declares how many packets it owns
        if mtl_packets:
            mtl_n += 1
            if sum(mtl_packets) == len(packets):
                mtl_ok += 1

        # the three that do not resolve -- record the rules that FAILED
        # count these over the WHOLE file, inside packets included -- the
        # top-level count is 0 for `mtx` and reports nothing useful.
        adj_idx = set()
        allmtx = allreskin = 0
        for line in b.decode('ascii', 'replace').splitlines():
            w = line.strip().split()
            if not w:
                continue
            if w[0] == 'adj' and len(w) > 1:
                adj_idx.add(int(w[1]))
            elif w[0] == 'mtx':
                allmtx += 1
            elif w[0] == 'reskin':
                allreskin += 1
        if 'adjuncts' in hdr:
            openq['adjuncts == distinct adj index'][
                0 if hdr['adjuncts'] == len(adj_idx) else 1] += 1
            openq['adjuncts == max adj index + 1'][
                0 if hdr['adjuncts'] == (max(adj_idx) + 1 if adj_idx else 0)
                else 1] += 1
        if 'matrices' in hdr:
            openq['matrices == all `mtx` lines'][
                0 if hdr['matrices'] == allmtx else 1] += 1
        if 'reskins' in hdr:
            openq['reskins == all `reskin` lines'][
                0 if hdr['reskins'] == allreskin else 1] += 1

    res = dict(versions=dict(versions.most_common()),
               text_files=len(text_files), pure_ascii=ascii_ok,
               verified={k: {'ok': v[0], 'bad': v[1]}
                         for k, v in checks.items()},
               packet_adj=[pk_ok, pk_n], mtl_packets=[mtl_ok, mtl_n],
               open_questions={k: {'ok': v[0], 'bad': v[1]}
                               for k, v in openq.items()},
               weapon_types=dict(wtypes), weapon_models=wmodels)
    if args.json:
        print(json.dumps(res, indent=1)); return

    print('.mod container versions (%d distinct names):' % sum(versions.values()))
    for v, c in versions.most_common():
        kind = 'PLAIN ASCII' if v.startswith('1.1') else 'binary'
        print('   version %-6s %5d   %s' % (v, c, kind))
    print()
    tot = sum(wtypes.values())
    print('WEAPON MODELS (from `.guns`, %d TypeNames with a model):' % tot)
    for k, v in wtypes.most_common():
        print('   %-14s %3d types (%4.1f%%)  %d distinct models: %s'
              % (k, v, 100 * v / tot, len(wmodels[k]), ', '.join(wmodels[k])))
    print()
    print('the text dialect: %d files, pure ASCII on %d'
          % (len(text_files), ascii_ok))
    print()
    print('HEADER COUNTS THAT VERIFY  %-14s %6s %6s' % ('', 'ok', 'bad'))
    for k in VERIFIED:
        if checks[k][0] or checks[k][1]:
            print('   %-36s %6d %6d' % ('%s == `%s` lines' % (k, VERIFIED[k]),
                                        checks[k][0], checks[k][1]))
    print('   %-36s %6d %6d' % ('packet N == its own `adj` lines', pk_ok,
                                pk_n - pk_ok))
    print('   %-36s %6d %6d' % ("sum(mtl `packets:`) == packet blocks",
                                mtl_ok, mtl_n - mtl_ok))
    print()
    print('STILL OPEN -- rules tested and REJECTED, so nobody retries them')
    for k in sorted(openq):
        v = openq[k]
        print('   %-36s %6d ok %6d bad' % (k, v[0], v[1]))
    print('   `adjuncts`, `matrices` and `reskins` are declared in every')
    print('   header and match none of the above. Their records live inside')
    print('   `packet`/`mtl` blocks and are shared between them, so the count')
    print('   is of something other than lines. NOT guessed at here.')


if __name__ == '__main__':
    main()
