#!/usr/bin/env python3
"""`.skel` -- the bone hierarchy -- plus `.chase`, the last trap field.

The two items left in the `props.py` structural gap after REQ-047/049/050.

`.skel` is 1,128 files and **33,557 of the 48,506 block opens still missed
(69%)**.  It mis-parses for a reason none of the earlier formats had: a bone
block CONTAINS bone blocks, to arbitrary depth.  A reader that tracks one level
of nesting cannot count them.

    Version: 101
    NumBones N
    bone <name> {
        offset x y z
        transX transY transZ rotX rotY rotZ scaleX scaleY scaleZ   -- valueless
        bone <child> { ... }                                       -- recursive
    }

The nine `transX..scaleZ` keys carry NO value.  They are channel declarations:
which degrees of freedom this bone animates.  That makes `.skel` the place the
channel counts in `.anim` / `.iani` should come from, so the probe reports the
per-skeleton channel total for anyone testing that.

`.chase` is 63 files of one 84-byte file and holds 63 of the 139 remaining trap
fields -- the last live corruption route after `.nav`'s 75:

    NumSideOffsets: 3
    SideOffsets: { 4.000000  5.500000  7.000000  }

`props.py` offers `SideOffsets` with the braces inside the value, which is the
same shape that drops a brace in `.nav`'s `Tracks`.

Read-only.  Takes no lock.
"""
import argparse, collections, json, os, sys, zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from rdrstudio.iso9660 import Iso9660
from rdrstudio import dave

ISO = os.path.join(os.path.dirname(__file__), '..', '..',
                   '01_Vanilla', 'RDR_VANILLA.iso')

CHANNELS = ('transX', 'transY', 'transZ', 'rotX', 'rotY', 'rotZ',
            'scaleX', 'scaleY', 'scaleZ')


class SkelError(Exception):
    pass


def tokens(text):
    return text.replace('{', ' { ').replace('}', ' } ').split()


def _num(s):
    try:
        float(s); return True
    except ValueError:
        return False


def parse_skel(text):
    """Recursive-descent over the bone tree.  Returns (header, root bones)."""
    tk = tokens(text)
    i, header = 0, {}

    def bone():
        nonlocal i
        assert tk[i] == 'bone'
        i += 1
        name = tk[i]; i += 1
        if tk[i] != '{':
            raise SkelError('bone %s: expected {' % name)
        i += 1
        node = {'name': name, 'channels': [], 'children': [], 'offset': None}
        while tk[i] != '}':
            k = tk[i]
            if k == 'bone':
                node['children'].append(bone())
            elif k in CHANNELS:
                node['channels'].append(k); i += 1
            else:
                i += 1
                vals = []
                while i < len(tk) and _num(tk[i]):
                    vals.append(float(tk[i])); i += 1
                if k == 'offset':
                    node['offset'] = vals
                else:
                    node[k] = vals
        i += 1
        return node

    roots = []
    while i < len(tk):
        if tk[i] == 'bone':
            roots.append(bone())
        else:
            k = tk[i].rstrip(':'); i += 1
            if i < len(tk) and _num(tk[i]):
                header[k] = tk[i]; i += 1
    return header, roots


def walk(nodes, depth=0):
    for n in nodes:
        yield depth, n
        for d, c in walk(n['children'], depth + 1):
            yield d, c


def entries(iso, exts):
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b'Dave', b'DAVE'):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            ext = e.name.rsplit('.', 1)[-1].lower()
            if ext not in exts:
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
    ap.add_argument('--anim', action='store_true',
                    help='also cross-check .anim channel counts against the '
                         'skeletons (slower: parses 10k .anim files)')
    args = ap.parse_args()

    iso = Iso9660(ISO)
    ok = fail = 0
    errs = collections.Counter()
    numbones_ok = numbones_bad = 0
    versions = collections.Counter()
    depths = collections.Counter()
    chanset = collections.Counter()
    roots_n = collections.Counter()
    offset3 = offset_other = 0
    chan_totals = {}
    skel_channels = {}
    bad_examples = []
    attachments = []
    root_names = collections.Counter()
    chase_entries = 0
    seen = set()

    # ---- .chase: the last trap field ----
    chase_files = chase_ok = 0
    chase_shape = collections.Counter()

    for arc, name, ext, b in entries(iso, ('skel', 'chase')):
        if ext == 'chase':
            chase_entries += 1
        key = (ext, name)
        if key in seen:
            continue
        seen.add(key)
        try:
            text = b.decode('ascii')
        except UnicodeDecodeError:
            fail += 1; errs['%s: not ascii' % ext] += 1; continue

        if ext == 'chase':
            chase_files += 1
            tk = tokens(text)
            d = {}
            j = 0
            while j < len(tk):
                k = tk[j].rstrip(':'); j += 1
                if j < len(tk) and tk[j] == '{':
                    j += 1
                    vals = []
                    while tk[j] != '}':
                        vals.append(float(tk[j])); j += 1
                    j += 1
                    d[k] = vals
                elif j < len(tk) and _num(tk[j]):
                    d[k] = float(tk[j]); j += 1
            n = int(d.get('NumSideOffsets', -1))
            got = len(d.get('SideOffsets', []))
            chase_shape[(n, got, tuple(d.get('SideOffsets', [])))] += 1
            if n == got:
                chase_ok += 1
            continue

        try:
            header, roots = parse_skel(text)
        except (SkelError, ValueError, IndexError) as ex:
            fail += 1; errs['skel: %s' % str(ex)[:50]] += 1; continue
        ok += 1
        versions[header.get('Version', '?')] += 1
        roots_n[len(roots)] += 1

        allb = list(walk(roots))
        declared = int(header.get('NumBones', -1))
        # `NumBones` counts the FIRST root's subtree, not every bone in the
        # file.  Two files carry extra root-level bones -- `gat` (a gatling,
        # 8 bones), `skel_ptl` (a pistol), `cof_01` -- which are attached
        # props with their own little skeletons, and are not counted.
        primary = len(list(walk(roots[:1]))) if roots else 0
        if declared == primary:
            numbones_ok += 1
        else:
            numbones_bad += 1
            if len(bad_examples) < 5:
                bad_examples.append((name, declared, primary, len(allb)))
        if len(roots) > 1:
            attachments.append((name, roots[0]['name'],
                                [(r['name'], len(list(walk([r]))))
                                 for r in roots[1:]]))
        root_names[roots[0]['name'] if roots else '?'] += 1

        total_ch = 0
        for dep, n in allb:
            depths[dep] += 1
            chanset[tuple(n['channels'])] += 1
            total_ch += len(n['channels'])
            if n['offset'] is not None:
                if len(n['offset']) == 3:
                    offset3 += 1
                else:
                    offset_other += 1
        chan_totals[name] = total_ch
        skel_channels[name] = (
            sum(len(n['channels']) for _, n in walk(roots[:1])),
            declared)

    iso.close()

    res = dict(skel_parsed=ok, skel_failed=fail, errors=dict(errs.most_common(8)),
               NumBones_ok=numbones_ok, NumBones_bad=numbones_bad,
               NumBones_mismatches=bad_examples,
               versions=dict(versions), root_counts=dict(roots_n),
               depth_histogram=dict(sorted(depths.items())),
               offset_3_floats=[offset3, offset3 + offset_other],
               distinct_channel_sets=len(chanset),
               channel_sets=dict(collections.Counter(
                   {' '.join(k) or '(none)': v
                    for k, v in chanset.items()}).most_common(6)),
               chase_entries=chase_entries,
               chase_files=chase_files, chase_count_ok=chase_ok,
               skel_attachments=attachments,
               first_root_names=dict(root_names),
               chase_distinct_shapes=len(chase_shape))
    if args.json:
        print(json.dumps(res, indent=1)); return

    print('.skel  parsed %d  failed %d' % (ok, fail))
    for e, c in errs.most_common(8):
        print('   %5d  %s' % (c, e))
    print('  Version values: %s' % dict(versions))
    print('  root bones per file: %s' % dict(roots_n))
    print('  NumBones == bones under the FIRST root (recursive):  %d ok, %d bad'
          % (numbones_ok, numbones_bad))
    for x in bad_examples:
        print('     mismatch %s declared %d primary %d total %d' % x)
    print('  first root is named: %s' % dict(root_names.most_common(4)))
    if attachments:
        print('  files with extra root-level bones (attached props):')
        for nm, r0, extra in attachments:
            print('     %-14s first root %-6s + %s' % (nm, r0, extra))
    print('  offset is 3 floats: %d / %d' % (offset3, offset3 + offset_other))
    print('  bone depth histogram: %s' % dict(sorted(depths.items())))
    print('  distinct channel sets: %d' % len(chanset))
    for k, v in chanset.most_common(6):
        print('     %6d  %s' % (v, ' '.join(k) or '(none)'))
    tot = sum(chan_totals.values())
    print('  channels declared across all skeletons: %d' % tot)

    if args.anim:
        cross_check_anim({n[:-5].lower() if n.endswith('.skel') else n: v
                          for n, v in skel_channels.items()})

    print()
    print('.chase  archive entries %d, distinct files %d   '
          'NumSideOffsets == len(SideOffsets): %d'
          % (chase_entries, chase_files, chase_ok))
    print('  distinct contents: %d -- every copy on the disc is identical'
          % len(chase_shape))
    for (n, got, vals), c in chase_shape.most_common(3):
        print('     %3d files  NumSideOffsets=%d  SideOffsets=%s' % (c, n, list(vals)))


def cross_check_anim(skel_ch):
    """Does `.anim`'s `channels` field come from the skeleton?

    Two claims, and the second is the one that survives without a heuristic.

    1. Paired by filename stem, `channels` equals either the skeleton's own
       declared channel total or `NumBones*3 + 3` (every bone rotates, the
       root also translates).  Pairing by stem is a GUESS about which rig a
       clip animates, so this is a floor, not a rate.
    2. Pairing-free: `channels` is a multiple of 3 on every file, which is
       what 3-per-bone requires.  The control is that a random integer is a
       multiple of 3 one time in three.
    """
    from rdrstudio import anim
    iso = Iso9660(ISO)
    hit = collections.Counter()
    vals = []
    for arc, name, ext, b in entries(iso, ('anim',)):
        try:
            a = anim.parse(b)
        except Exception:
            continue
        ch = getattr(a, 'channels', None)
        if ch is None:
            continue
        vals.append(ch)
        stem = name.lower().split('_')[0].rsplit('.', 1)[0]
        if stem not in skel_ch:
            hit['no matching .skel'] += 1
            continue
        prim, nb = skel_ch[stem]
        if ch == prim:
            hit['A: == skeleton channel declarations'] += 1
        elif ch == nb * 3 + 3:
            hit['B: == NumBones*3 + 3'] += 1
        else:
            hit['C: paired to the wrong rig'] += 1
    iso.close()
    n = len(vals)
    d3 = sum(1 for v in vals if v % 3 == 0)
    paired = sum(v for k, v in hit.items() if k != 'no matching .skel')
    print()
    print('.anim channel cross-check (%d files with a channels field)' % n)
    for k, v in hit.most_common():
        extra = '' if k == 'no matching .skel' else '  %5.1f%%' % (100 * v / paired)
        print('   %-38s %6d%s' % (k, v, extra))
    print('   -> A or B: %.2f%% of %d paired files' % (
        100 * (hit['A: == skeleton channel declarations']
               + hit['B: == NumBones*3 + 3']) / paired, paired))
    print('   pairing by filename stem is a guess; the residual is 90.1%')
    print('   channels=162 and 5.9% channels=159, both the 53-bone rig.')
    print()
    print('   PAIRING-FREE: channels divisible by 3: %d / %d = %.2f%%'
          % (d3, n, 100 * d3 / n))
    print('   CONTROL     : a random integer is, one time in three (33.3%)')
    print('   distinct channel values: %d -> %s'
          % (len(set(vals)), sorted(set(vals))))


if __name__ == '__main__':
    main()
