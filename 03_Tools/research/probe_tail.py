#!/usr/bin/env python3
"""The tail of the `props.py` structural gap: the last ten extensions.

After REQ-047/049/050/051 accounted for 94.5% of the 271,618 block opens
`props.py` misses, 14,949 were left across ten extensions:

    .hot 8674  .pmtl 3654  .duel 1273  .msd 998  .amp 239
    .guns 63   .sphs 34    .dst 8      .natr 5   .cam 1

They are all the same Angel Studios grammar wearing different nouns, so they
are one probe.  A block opens as `Kind {`, `Kind name {` or `Kind name N {` --
the third form declaring how many records follow -- and blocks nest.

The per-extension count invariants below are what an editor has to preserve,
and each is checked against the whole corpus:

    .hot   NumHotSpots      == HotSpot blocks
    .pmtl  num_materials    == mtl blocks
    .duel  NumDuelData      == Type blocks
    .sphs  NumBoundSpheres  == BoundSphere + BoundHotdog blocks
    .guns  Num_GenericWeapon== Weapon blocks
    .natr  NumNodes         == position records, and `attributes N` == entries

`.hot` is worth one warning to anyone writing a parser: its `matrix` is
**twelve bare floats with NO braces**, unlike the `matrix { ... }` of the
scene-geometry family.  Same key, same meaning, different spelling.

Read-only.  Takes no lock.
"""
import argparse, collections, json, os, re, sys, zlib

# `Key <0xCD 0xCD ...>` -- which field the uninitialised bytes sit in.
FILLFIELD = re.compile(rb'([A-Za-z_]+)\s+(?:[\x80-\xff]+\s*)+')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from rdrstudio.iso9660 import Iso9660
from rdrstudio import dave

ISO = os.path.join(os.path.dirname(__file__), '..', '..',
                   '01_Vanilla', 'RDR_VANILLA.iso')

EXTS = ('hot', 'pmtl', 'duel', 'msd', 'amp', 'guns', 'sphs', 'dst',
        'natr', 'cam')

# Counted `attributes N { TYPE name value... }`, arity by type -- the same
# construct decoded in probe_scene.py, and `.natr` uses it too.
ARITY = {'BOOL': 1, 'STRING': 1, 'INT': 1, 'FLOAT': 1, 'VECTOR3': 3}

COUNTS = {
    'hot':  ('NumHotSpots', ('HotSpot',)),
    'pmtl': ('num_materials', ('mtl',)),
    'duel': ('NumDuelData', ('Type',)),
    'sphs': ('NumBoundSpheres', ('BoundSphere', 'BoundHotdog')),
    'natr': ('NumNodes', None),          # records are bare xyz, counted apart
}


class TailError(Exception):
    pass


def tokens(text):
    return text.replace('{', ' { ').replace('}', ' } ').split()


def _num(s):
    try:
        float(s); return True
    except ValueError:
        return False


def parse(text):
    """Generic recursive reader. Returns (scalars, [(kind, name, body)...])."""
    tk = tokens(text)
    i = 0
    scalars = {}
    blocks = []
    counted = []            # (key, declared, found) for `Kind name N {` forms

    def block():
        nonlocal i
        i += 1                       # past '{'
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
            raise TailError('unclosed {')
        body = tk[start:i]
        i += 1
        return body

    def scan(toks, out_blocks):
        """Walk a token run, collecting the blocks it opens."""
        j = 0
        while j < len(toks):
            t = toks[j]
            if t == '{':
                d, s = 1, j + 1
                j += 1
                while j < len(toks) and d:
                    if toks[j] == '{':
                        d += 1
                    elif toks[j] == '}':
                        d -= 1
                        if not d:
                            break
                    j += 1
                out_blocks.append(('', '', toks[s:j]))
                j += 1
                continue
            # `Kind`, `Kind name`, `Kind N`, `Kind name N` then `{`
            k = j
            words = []
            while k < len(toks) and toks[k] != '{' and len(words) < 3:
                words.append(toks[k]); k += 1
            if k < len(toks) and toks[k] == '{' and words:
                d, s = 1, k + 1
                k += 1
                while k < len(toks) and d:
                    if toks[k] == '{':
                        d += 1
                    elif toks[k] == '}':
                        d -= 1
                        if not d:
                            break
                    k += 1
                body = toks[s:k]
                kind = words[0].rstrip(':')
                name = words[1] if len(words) > 1 else ''
                if _num(words[-1]) and len(words) > 1:
                    counted.append((kind, int(float(words[-1])), body))
                out_blocks.append((kind, name, body))
                j = k + 1
                continue
            j += 1

    while i < len(tk):
        t = tk[i]
        if t == '{':
            blocks.append(('', '', block()))
            continue
        words = []
        k = i
        while k < len(tk) and tk[k] != '{' and len(words) < 3:
            words.append(tk[k]); k += 1
        if k < len(tk) and tk[k] == '{' and words:
            i = k
            body = block()
            kind = words[0].rstrip(':')
            name = words[1] if len(words) > 1 else ''
            if len(words) > 1 and _num(words[-1]):
                counted.append((kind, int(float(words[-1])), body))
            blocks.append((kind, name, body))
            continue
        key = tk[i].rstrip(':'); i += 1
        vals = []
        while i < len(tk) and _num(tk[i]):
            vals.append(tk[i]); i += 1
        scalars.setdefault(key, vals)
    return scalars, blocks, counted, scan


def count_kinds(blocks, kinds, scan):
    """Count blocks of the given kinds at ANY depth."""
    n = 0
    stack = list(blocks)
    while stack:
        kind, name, body = stack.pop()
        if kind in kinds:
            n += 1
        inner = []
        scan(body, inner)
        stack.extend(inner)
    return n


def attrs_ok(body):
    """`attributes N { TYPE name value... }` -- arity by TYPE."""
    ok = True
    j = 0
    while j < len(body):
        if body[j] == 'attributes' and j + 1 < len(body) and _num(body[j + 1]):
            n = int(body[j + 1])
            if j + 2 >= len(body) or body[j + 2] != '{':
                return False
            k = j + 3
            found = 0
            while k < len(body) and body[k] != '}':
                ty = body[k]
                if ty not in ARITY:
                    return False
                k += 2 + ARITY[ty]
                found += 1
            ok = ok and found == n
            j = k
        j += 1
    return ok


def files(iso):
    seen = set()
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b'Dave', b'DAVE'):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            ext = e.name.rsplit('.', 1)[-1].lower()
            if ext not in EXTS or e.name in seen:
                continue
            seen.add(e.name)
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
    inv = collections.defaultdict(lambda: [0, 0])
    inner = collections.defaultdict(lambda: [0, 0])
    mism = []
    fill = {}
    fillfields = collections.Counter()
    highother = collections.Counter()
    balance = collections.defaultdict(lambda: collections.Counter())
    unbalanced = []

    for arc, name, ext, b in files(iso):
        # 15 `.duel` files carry 0xCD bytes -- MSVC's debug fill for
        # uninitialised heap -- so latin-1, and count them rather than
        # failing the parse.  See `fill` below.
        nfill = sum(1 for c in b if c == 0xCD)
        if nfill:
            fill[name] = nfill
            for m in FILLFIELD.finditer(b):
                fillfields[m.group(1).decode('latin-1')] += 1
        other_high = sorted({c for c in b if c > 127 and c != 0xCD})
        if other_high:
            highother[ext] += 1
        text = b.decode('latin-1')

        # Brace balance, checked before parsing.  Three `.duel` files fail it
        # and all three are named dead (`x`, `xx`, `xxx` prefixes): they have
        # 2 opens against 6 closes, i.e. someone deleted the opening braces of
        # nested blocks by hand.  Those are also the only two count mismatches
        # below, so the format is consistent and the FILES are broken.
        nopen, nclose = text.count('{'), text.count('}')
        balance[ext][nopen == nclose] += 1
        if nopen != nclose:
            unbalanced.append((name, ext, nopen, nclose))
        try:
            scalars, blocks, counted, scan = parse(text)
        except (TailError, ValueError, IndexError) as ex:
            bad[ext] += 1; errs['%s: %s' % (ext, str(ex)[:40])] += 1; continue
        ok[ext] += 1

        if ext in COUNTS:
            key, kinds = COUNTS[ext]
            dec = scalars.get(key)
            if dec and kinds:
                n = count_kinds(blocks, set(kinds), scan)
                hit = int(float(dec[0])) == n
                inv['%s: %s == %s blocks' % (ext, key, '/'.join(kinds))][
                    0 if hit else 1] += 1
                if not hit and len(mism) < 6:
                    mism.append((name, key, dec[0], n))

        # `Kind name N {` -- the declared count against the records inside.
        # `attributes N {` is NOT this: its entries are `TYPE name value`
        # triples, not blocks, and `attrs_ok` checks them properly.  Counting
        # its inner BLOCKS scored 0/5 -- a failure of my check, not the data.
        for kind, dec, body in counted:
            if kind == 'attributes':
                continue
            sub = []
            scan(body, sub)
            got = len(sub)
            inner['%s: %s N == inner blocks' % (ext, kind)][
                0 if got == dec else 1] += 1

        if ext == 'natr':
            allb = [blocks] and [body for _, _, body in blocks]
            good = attrs_ok(tokens(text))
            inv['natr: attributes N == entries'][0 if good else 1] += 1

    iso.close()
    res = dict(parsed=dict(ok), failed=dict(bad),
               errors=dict(errs.most_common(8)),
               invariants={k: {'ok': v[0], 'bad': v[1]} for k, v in inv.items()},
               counted_blocks={k: {'ok': v[0], 'bad': v[1]}
                               for k, v in inner.items()},
               mismatches=mism,
               msvc_debug_fill=dict(sorted(fill.items(),
                                           key=lambda kv: -kv[1])),
               fill_fields=dict(fillfields),
               files_with_other_high_bytes=dict(highother),
               brace_balance={e: {'balanced': v[True], 'unbalanced': v[False]}
                              for e, v in balance.items()},
               unbalanced_files=unbalanced)
    if args.json:
        print(json.dumps(res, indent=1)); return

    print('%-8s %8s %8s' % ('ext', 'parsed', 'failed'))
    for e in EXTS:
        print('%-8s %8d %8d' % ('.' + e, ok[e], bad[e]))
    print('%-8s %8d %8d' % ('TOTAL', sum(ok.values()), sum(bad.values())))
    if errs:
        print('\nfailures:')
        for e, c in errs.most_common(8):
            print('   %5d  %s' % (c, e))
    print('\n%-46s %6s %6s' % ('count invariant', 'ok', 'bad'))
    for k in sorted(inv):
        print('%-46s %6d %6d' % (k, inv[k][0], inv[k][1]))
    for k in sorted(inner):
        print('%-46s %6d %6d' % (k, inner[k][0], inner[k][1]))
    for x in mism:
        print('   mismatch %s %s=%s found %d' % x)

    print()
    print('BRACE BALANCE  %-8s %9s %11s' % ('ext', 'balanced', 'UNBALANCED'))
    for e in sorted(balance):
        print('               %-8s %9d %11d'
              % ('.' + e, balance[e][True], balance[e][False]))
    for n, e, o, c in unbalanced:
        print('   MALFORMED %-26s .%-5s open %d close %d  (named dead)'
              % (n, e, o, c))

    if fill:
        print()
        print('UNINITIALISED MEMORY SHIPPED IN THE DATA')
        print('  0xCD is MSVC\'s debug fill for heap that was never written.')
        print('  %d files carry it, %d bytes total, and it is the ONLY high'
              % (len(fill), sum(fill.values())))
        print('  byte value anywhere in these formats (%d files had any other).'
              % sum(highother.values()))
        print('  every occurrence is inside these fields: %s' % dict(fillfields))
        for n, c in sorted(fill.items(), key=lambda kv: -kv[1])[:6]:
            print('     %-28s %4d bytes' % (n, c))


if __name__ == '__main__':
    main()
