#!/usr/bin/env python3
"""The level scene-geometry family: `.bxes` `.lnes` `.znes` `.xznes` `.bnds`.

These five are one grammar and one attack.  Together they are 4,090 files and
**130,183 of the 178,689 block opens `props.py` still misses (73%)** once
`.nav` and `.gait` are accounted for.  They are also exactly the sections a
`.lvl` manifest lists -- Bounds / Zones / Lines / Boxes -- so this is the
geometry a level is assembled from.

Shared shape:

    Num<Kind>  N                     -- NumBoxes / NumLineNodes / NumInstNodes
    <Kind> <name> {                  -- `Box` or `node`, N of them
        matrix { 4 rows x 3 floats } -- 3x3 basis + a translation row
        attributes A { A entries }   -- `TYPE name value`, counted
        ...per-extension fields
    }

Per extension: `.bxes` adds width/height/depth; `.lnes` adds
`Vertices V { V rows x 3 }`; `.znes`/`.xznes` add numLods and
resourceIndex/resourceIndexXbox; `.bnds` adds the resource indices and carries
the collision flags in its attributes.

The claim this probe exists to settle: **a `.bnds` node's NAME encodes its own
collision flags.**  `bound_bound_grp_fffffffffftf_01` has twelve f/t characters
and the node has twelve `BoundFlag*` booleans; if character i is `t` exactly
when flag i is true, the name is a redundant encoding of the attributes and can
be checked against them.

Read-only.  Takes no lock.
"""
import argparse, collections, json, os, re, sys, zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from rdrstudio.iso9660 import Iso9660
from rdrstudio import dave

ISO = os.path.join(os.path.dirname(__file__), '..', '..',
                   '01_Vanilla', 'RDR_VANILLA.iso')

EXTS = ('bxes', 'lnes', 'znes', 'xznes', 'bnds')

# `Name N {` -- declares a count AND opens a block.  The count is the number of
# RECORDS, and each record's width is asserted separately.
COUNTED = {'attributes': None, 'Vertices': 3}

# An attribute is `TYPE name value...` and the number of values depends on the
# TYPE -- VECTOR3 carries three.  Assuming a fixed three tokens per attribute
# cost 30 files on the first run.  Unknown types raise rather than being
# guessed at, so the set below is complete or the probe says so.
ARITY = {'BOOL': 1, 'STRING': 1, 'INT': 1, 'FLOAT': 1, 'VECTOR3': 3}
UNCOUNTED = {'matrix': 3}

FLAGRE = re.compile(r'_([ft]{2,})_\d+$')


class SceneError(Exception):
    pass


def tokens(text):
    return text.replace('{', ' { ').replace('}', ' } ').split()


def _num(s):
    try:
        float(s); return True
    except ValueError:
        return False


def parse(text):
    tk = tokens(text)
    i, out = 0, {'header': {}, 'nodes': []}

    def grab_block():
        nonlocal i
        if tk[i] != '{':
            raise SceneError('expected { got %r' % tk[i])
        i += 1
        depth, start = 1, i
        while i < len(tk) and depth:
            if tk[i] == '{':
                depth += 1
            elif tk[i] == '}':
                depth -= 1
                if not depth:
                    break
            i += 1
        if depth:
            raise SceneError('unclosed {')
        body = tk[start:i]
        i += 1
        return body

    def body_fields(body):
        d, j = {}, 0
        while j < len(body):
            k = body[j]; j += 1
            if k in UNCOUNTED and j < len(body) and body[j] == '{':
                j += 1
                rows, cur = [], []
                while body[j] != '}':
                    cur.append(float(body[j])); j += 1
                    if len(cur) == UNCOUNTED[k]:
                        rows.append(cur); cur = []
                j += 1
                if cur:
                    raise SceneError('%s: ragged rows' % k)
                d[k] = rows
            elif k in COUNTED and j < len(body) and _num(body[j]):
                n = int(body[j]); j += 1
                if body[j] != '{':
                    raise SceneError('%s: expected {' % k)
                j += 1
                inner = []
                while body[j] != '}':
                    inner.append(body[j]); j += 1
                j += 1
                w = COUNTED[k]
                if w:
                    if len(inner) != n * w:
                        raise SceneError('%s declared %d rows of %d, found %d'
                                         % (k, n, w, len(inner)))
                    d[k] = [inner[x:x + w] for x in range(0, len(inner), w)]
                else:
                    # attributes: `TYPE name value...`, arity set by TYPE
                    attrs, x = [], 0
                    while x < len(inner):
                        ty = inner[x]
                        if ty not in ARITY:
                            raise SceneError('unknown attribute type %r' % ty)
                        w = ARITY[ty]
                        if x + 2 + w > len(inner) + 1:
                            raise SceneError('%s: truncated' % ty)
                        attrs.append((ty, inner[x + 1],
                                      tuple(inner[x + 2:x + 2 + w])))
                        x += 2 + w
                    if len(attrs) != n:
                        raise SceneError('attributes declared %d, found %d'
                                         % (n, len(attrs)))
                    d[k] = attrs
            else:
                vals = []
                while j < len(body) and _num(body[j]):
                    vals.append(body[j]); j += 1
                d[k] = vals
        return d

    while i < len(tk):
        t = tk[i]
        if t in ('Box', 'node'):
            i += 1
            name = tk[i]; i += 1
            out['nodes'].append((name, body_fields(grab_block())))
        elif t.startswith('Num'):
            k = t; i += 1
            out['header'][k] = int(tk[i]); i += 1
        else:
            i += 1
    return out


def scene_files(iso):
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b'Dave', b'DAVE'):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            ext = e.name.rsplit('.', 1)[-1].lower()
            if ext not in EXTS:
                continue
            raw = d.backing.read_at(e.offset, e.size_comp)
            try:
                b = zlib.decompress(raw, -15) if e.compressed else raw
            except zlib.error:
                continue
            yield f.path, e.name, ext, b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    iso = Iso9660(ISO)
    ok = collections.Counter(); bad = collections.Counter()
    errs = collections.Counter()
    hdr_ok = collections.Counter(); hdr_bad = collections.Counter()
    mat = collections.Counter()
    attr_types = collections.Counter()
    flag_ok = flag_bad = flag_files = 0
    flag_examples = []
    flag_patterns = collections.Counter()
    bnds_nodes = 0
    seen = set()

    for arc, name, ext, b in scene_files(iso):
        if name in seen:
            continue
        seen.add(name)
        try:
            text = b.decode('ascii')
        except UnicodeDecodeError:
            bad[ext] += 1; errs['%s: not ascii' % ext] += 1; continue
        try:
            g = parse(text)
        except (SceneError, ValueError, IndexError) as ex:
            bad[ext] += 1; errs['%s: %s' % (ext, str(ex)[:50])] += 1; continue
        ok[ext] += 1

        # the Num* header counts the node blocks
        hv = list(g['header'].values())
        if hv and hv[0] == len(g['nodes']):
            hdr_ok[ext] += 1
        else:
            hdr_bad[ext] += 1

        for nm, d in g['nodes']:
            if ext == 'bnds':
                bnds_nodes += 1
            if 'matrix' in d:
                mat['%dx3' % len(d['matrix'])] += 1
            for tri in d.get('attributes', []):
                attr_types[tri[0]] += 1

            # does the name encode the BOOL flags?
            m = FLAGRE.search(nm)
            bools = [t for t in d.get('attributes', []) if t[0] == 'BOOL']
            if m and bools and len(m.group(1)) == len(bools):
                flag_files += 1
                want = ''.join('t' if t[2][0] == 'true' else 'f'
                               for t in bools)
                flag_patterns[m.group(1)] += 1
                if want == m.group(1):
                    flag_ok += 1
                else:
                    flag_bad += 1
                    if len(flag_examples) < 5:
                        flag_examples.append((nm, m.group(1), want))

    iso.close()
    res = dict(parsed=dict(ok), failed=dict(bad),
               errors=dict(errs.most_common(10)),
               header_matches=dict(hdr_ok), header_mismatch=dict(hdr_bad),
               matrix_shapes=dict(mat), attribute_types=dict(attr_types),
               name_encodes_flags=[flag_ok, flag_files],
               bnds_nodes=bnds_nodes,
               flag_distinct_patterns=len(flag_patterns),
               flag_all_false_share=(
                   sum(v for k, v in flag_patterns.items() if set(k) == {'f'})
                   / flag_files if flag_files else 0),
               name_flag_mismatches=flag_examples)
    if args.json:
        print(json.dumps(res, indent=1)); return

    print('%-8s %8s %8s %10s %10s' % ('ext', 'parsed', 'failed',
                                      'Num* ok', 'Num* bad'))
    for e in EXTS:
        print('%-8s %8d %8d %10d %10d'
              % ('.' + e, ok[e], bad[e], hdr_ok[e], hdr_bad[e]))
    print('%-8s %8d %8d %10d %10d'
          % ('TOTAL', sum(ok.values()), sum(bad.values()),
             sum(hdr_ok.values()), sum(hdr_bad.values())))
    if errs:
        print('\nfailures:')
        for e, c in errs.most_common(10):
            print('   %5d  %s' % (c, e))
    print('\nmatrix shapes: %s' % dict(mat))
    print('attribute types: %s' % dict(attr_types.most_common(8)))
    allf = sum(v for k, v in flag_patterns.items() if set(k) == {'f'})
    print('\nNAME ENCODES ITS OWN FLAGS: %d / %d nodes  (%.2f%%)'
          % (flag_ok, flag_files,
             100 * flag_ok / flag_files if flag_files else 0))
    print('  scope   : %d of %d .bnds nodes carry a flag-shaped name (%.1f%%)'
          % (flag_files, bnds_nodes,
             100 * flag_files / bnds_nodes if bnds_nodes else 0))
    print('  CONTROL : %d distinct 12-bit patterns; all-false is %d nodes'
          ' (%.1f%%), so a match is not free'
          % (len(flag_patterns), allf,
             100 * allf / flag_files if flag_files else 0))
    for x in flag_examples:
        print('   mismatch name=%s  name_says=%s  attrs_say=%s' % x)


if __name__ == '__main__':
    main()
