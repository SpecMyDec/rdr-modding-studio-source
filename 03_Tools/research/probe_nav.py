#!/usr/bin/env python3
"""`.nav` -- the AI navigation graph, decoded and checked against geometry.

289 files.  It is the single largest structural blind spot on the disc:
71,769 of the 271,618 block opens `props.py` misses are in `.nav` (26%), and
it holds 75 of the 139 remaining trap fields and all 83 files that drifted on
round-trip.  Fixing it is worth more than any other text format left.

Layout:

    type: a
    NumNodes  N
    NumLinks  L
    Node i { LocalPos x y z ; Links <ids...> ; FirstLinkRef r ; NumLinks n }
    Link j { Length f ; Nodes a b ; Flags n }
    Tracks T { Links c { ids... } ... }      -- T entries
    ForeLinks F { ids... }
    BackLinks B { ids... }
    LinkRefs { ids... }                      -- no count

Note `NumLinks` means two different things: at top level it is the number of
`Link` blocks, inside a `Node` it is the degree of that node.  Same key, two
scopes -- which is part of why a flat reader mis-parses this format.

The claim that makes the decode more than a shape: **`Link.Length` is the
Euclidean distance between the `LocalPos` of the two nodes it joins.**  That is
checked at float precision against the node table, so the graph's geometry and
its topology corroborate each other from inside the same file.

Read-only.  Takes no lock.
"""
import argparse, collections, json, math, os, sys, zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from rdrstudio.iso9660 import Iso9660
from rdrstudio import dave

ISO = os.path.join(os.path.dirname(__file__), '..', '..',
                   '01_Vanilla', 'RDR_VANILLA.iso')


class NavError(Exception):
    pass


def tokens(text):
    """Braces are TOKENS.  Treating them as line markers is what produced 83
    false `.nav` failures in the first text-grammar pass."""
    return text.replace('{', ' { ').replace('}', ' } ').split()


def parse(text):
    tk = tokens(text)
    i = 0
    out = {'nodes': [], 'links': [], 'tracks': [], 'bnodes': [],
           'scalars': {}}

    def block():
        """Read a `{ ... }` run of tokens, returning the flat token list."""
        nonlocal i
        if tk[i] != '{':
            raise NavError('expected { at %d, got %r' % (i, tk[i]))
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
            raise NavError('unclosed {')
        body = tk[start:i]
        i += 1
        return body

    def kv(body):
        """Parse a Node/Link body: `key v v v` until the next known key."""
        d, j = {}, 0
        while j < len(body):
            k = body[j].rstrip(':')
            j += 1
            vals = []
            while j < len(body) and _isnum(body[j]):
                vals.append(body[j]); j += 1
            d[k] = vals
        return d

    while i < len(tk):
        t = tk[i]
        if t in ('Node', 'Link'):
            i += 1
            idx = int(tk[i]); i += 1
            d = kv(block())
            (out['nodes'] if t == 'Node' else out['links']).append((idx, d))
        elif t == 'Tracks':
            i += 1
            n = int(tk[i]); i += 1
            body = block()
            # each entry is `Links <count> { ids }`
            j, entries = 0, []
            while j < len(body):
                if body[j] != 'Links':
                    raise NavError('Tracks: expected Links, got %r' % body[j])
                c = int(body[j + 1]); j += 2
                if body[j] != '{':
                    raise NavError('Tracks: expected {')
                j += 1
                ids = []
                while body[j] != '}':
                    ids.append(int(body[j])); j += 1
                j += 1
                if len(ids) != c:
                    raise NavError('Tracks/Links declared %d found %d'
                                   % (c, len(ids)))
                entries.append(ids)
            if len(entries) != n:
                raise NavError('Tracks declared %d found %d' % (n, len(entries)))
            out['tracks'] = entries
        elif t in ('ForeLinks', 'BackLinks'):
            i += 1
            n = int(tk[i]); i += 1
            ids = [int(x) for x in block()]
            if len(ids) != n:
                raise NavError('%s declared %d found %d' % (t, n, len(ids)))
            out[t] = ids
        elif t == 'LinkRefs':
            i += 1
            out['LinkRefs'] = [int(x) for x in block()]
        elif t == 'node':
            # Dialect B -- the older format.  Links are embedded per node,
            # each as `target distance`, and there is no separate Link table.
            i += 1
            idx = int(tk[i]); i += 1
            body = block()
            d, j = {}, 0
            while j < len(body):
                k = body[j]; j += 1
                if j < len(body) and body[j] == '{':
                    j += 1
                    vals = []
                    while body[j] != '}':
                        vals.append(body[j]); j += 1
                    j += 1
                    d[k] = vals
                elif k in ('links', 'navtable'):
                    c = int(body[j]); j += 1
                    if body[j] != '{':
                        raise NavError('%s: expected {' % k)
                    j += 1
                    vals = []
                    while body[j] != '}':
                        vals.append(body[j]); j += 1
                    j += 1
                    if len(vals) != 2 * c:
                        raise NavError('%s declared %d pairs, found %d values'
                                       % (k, c, len(vals)))
                    d[k] = vals
                else:
                    vals = []
                    while j < len(body) and _isnum(body[j]):
                        vals.append(body[j]); j += 1
                    d[k] = vals
            out['bnodes'].append((idx, d))
        elif t.rstrip(':') in ('type', 'NumNodes', 'NumLinks', 'NumOfNodes'):
            k = t.rstrip(':'); i += 1
            out['scalars'][k] = tk[i]; i += 1
        else:
            i += 1
    return out


def _isnum(s):
    try:
        float(s)
        return True
    except ValueError:
        return False


def navs(iso):
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b'Dave', b'DAVE'):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            if not e.name.lower().endswith('.nav'):
                continue
            raw = d.backing.read_at(e.offset, e.size_comp)
            try:
                b = zlib.decompress(raw, -15) if e.compressed else raw
            except zlib.error:
                continue
            yield f.path, e.name, b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    iso = Iso9660(ISO)
    ok = fail = 0
    errs = collections.Counter()
    checks = collections.Counter()
    worst = 0.0
    worst_where = None
    stale = collections.defaultdict(lambda: [0, 0, 0.0])
    lengths = 0
    seen = set()

    for arc, name, b in navs(iso):
        if name in seen:
            continue
        seen.add(name)
        try:
            text = b.decode('ascii')
        except UnicodeDecodeError:
            fail += 1; errs['not ascii'] += 1; continue
        try:
            g = parse(text)
        except (NavError, ValueError, IndexError) as ex:
            fail += 1; errs[str(ex)[:60]] += 1; continue
        ok += 1

        nodes, links = g['nodes'], g['links']
        sc = g['scalars']

        def check(tag, cond):
            checks[tag + ('_ok' if cond else '_BAD')] += 1

        if 'NumOfNodes' in sc:
            # ---- dialect B: older format, links embedded per node ----
            checks['dialect_B'] += 1
            bn = g['bnodes']
            check('B_NumOfNodes', int(sc['NumOfNodes']) == len(bn))
            bpos = {ix: [float(v) for v in d['pos']]
                    for ix, d in bn if len(d.get('pos', [])) == 3}
            sym = {}
            for ix, d in bn:
                lk = d.get('links', [])
                for k in range(0, len(lk), 2):
                    tgt, dist = int(lk[k]), float(lk[k + 1])
                    sym[(ix, tgt)] = dist
                    if ix in bpos and tgt in bpos:
                        got = math.dist(bpos[ix], bpos[tgt])
                        e = abs(got - dist) / max(dist, 1e-6)
                        checks['B_dist_ok' if e < 2e-5 else 'B_dist_BAD'] += 1
                        st = stale[name]
                        st[1] += 1
                        st[2] = max(st[2], e)
                        if e >= 2e-5:
                            st[0] += 1
            for (a2, b2), dv in sym.items():
                if (b2, a2) in sym:
                    checks['B_symmetric_ok' if abs(sym[(b2, a2)] - dv) < 1e-6
                           else 'B_symmetric_BAD'] += 1
                else:
                    checks['B_symmetric_MISSING'] += 1
            continue

        checks['dialect_A'] += 1
        check('NumNodes', int(sc.get('NumNodes', -1)) == len(nodes))
        check('NumLinks_top', int(sc.get('NumLinks', -1)) == len(links))

        # per-node degree == length of the Links list
        deg_ok = all(len(d.get('Links', [])) == int(d['NumLinks'][0])
                     for _, d in nodes if 'NumLinks' in d)
        check('node_degree', deg_ok)

        # FirstLinkRef is the running prefix sum of the degrees
        run, pref_ok = 0, True
        for _, d in nodes:
            if 'FirstLinkRef' not in d or 'NumLinks' not in d:
                pref_ok = False; break
            if int(d['FirstLinkRef'][0]) != run:
                pref_ok = False; break
            run += int(d['NumLinks'][0])
        check('FirstLinkRef_prefix_sum', pref_ok)

        # LinkRefs holds exactly that many entries
        if 'LinkRefs' in g:
            check('LinkRefs_total', len(g['LinkRefs']) == run)

        # THE GEOMETRIC CHECK: a link's Length is the distance between the
        # LocalPos of the two nodes it names.
        pos = {}
        for idx, d in nodes:
            if 'LocalPos' in d and len(d['LocalPos']) == 3:
                pos[idx] = [float(v) for v in d['LocalPos']]
        for idx, d in links:
            if 'Nodes' not in d or 'Length' not in d:
                continue
            na, nb = (int(v) for v in d['Nodes'][:2])
            if na not in pos or nb not in pos:
                continue
            want = float(d['Length'][0])
            got = math.dist(pos[na], pos[nb])
            lengths += 1
            err = abs(got - want) / max(want, 1e-6)
            if err > worst:
                worst, worst_where = err, (name, idx, want, got)
            # float32 storage: a relative tolerance below ~1.2e-7 per
            # operand cannot be met, and short links amplify it.  2e-5 is
            # loose enough for float32 and still 4 orders tighter than any
            # real mismatch would be.
            checks['Length_ok' if err < 2e-5 else 'Length_BAD'] += 1

    iso.close()
    res = dict(files=ok + fail, parsed=ok, failed=fail,
               errors=dict(errs.most_common(10)),
               checks=dict(checks), links_checked=lengths,
               worst_length_rel_err=worst,
               worst_where=worst_where,
               dialect_b_stale={n: dict(stale_links=v[0], links=v[1],
                                        worst=v[2]) for n, v in stale.items()})
    if args.json:
        print(json.dumps(res, indent=1, default=str)); return

    print('.nav files %d   parsed %d   failed %d' % (ok + fail, ok, fail))
    for e, c in errs.most_common(10):
        print('   %5d  %s' % (c, e))
    print()
    print('%-28s %8s %8s' % ('invariant', 'holds', 'fails'))
    print('  dialect A files %d   dialect B files %d'
          % (checks.get('dialect_A', 0), checks.get('dialect_B', 0)))
    for tag in ('NumNodes', 'NumLinks_top', 'node_degree',
                'FirstLinkRef_prefix_sum', 'LinkRefs_total', 'Length',
                'B_NumOfNodes', 'B_dist', 'B_symmetric'):
        print('  %-26s %8d %8d'
              % (tag, checks.get(tag + '_ok', 0), checks.get(tag + '_BAD', 0)))
    print()
    print('Link.Length == |LocalPos[a] - LocalPos[b]|  over %d links' % lengths)
    print('  worst relative error %.3e  %s' % (worst, worst_where))

    if stale:
        print()
        print('dialect B: cached link distance vs the node positions')
        print('  %-32s %5s %6s %10s  %s'
              % ('file', 'stale', 'links', 'worst', 'verdict'))
        for n in sorted(stale, key=lambda n: -stale[n][0]):
            bad, tot, mx = stale[n]
            print('  %-32s %5d %6d %10.2e  %s'
                  % (n, bad, tot, mx, 'STALE' if bad else 'consistent'))


if __name__ == '__main__':
    main()
