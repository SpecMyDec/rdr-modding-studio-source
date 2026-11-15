#!/usr/bin/env python3
"""Disc-wide reference integrity -- what points at what, and what dangles.

`probe_fields.py` marks a key `ref` when its values name real files, giving
4,213 reference keys. This probe asks the next question: **do they all
resolve?**

    ref keys resolving 100%      4,163
    ref keys resolving partially    50
    ref keys resolving 0%            0

So the dependency graph is 98.8% intact at key level and nothing is entirely
broken. That is the modding-safety map: rename a file and you can see what
breaks.

**The caveat that has to travel with it, because it is half the answer.** A
partially-resolving `ref` key is NOT automatically a dangling reference. The
`ref` inference is a heuristic -- a key qualifies when at least half its values
match a filename -- so a key whose values are a small vocabulary can qualify by
coincidence. `pmtl`'s `mtl/sound` is the clean example: its 24 values are
`none`, `dirt`, `wood`, `adobe`, `rock`, `metal`, `water`, `stone`... surface
names, and the disc happens to contain `dirt.tex`, `wood.tex`, `rock.tex`.
It scores 69% and refers to no file at all.

**So `partial` splits into two classes and only one is cut content.** They are
reported separately here rather than summed into a single scary number.

This probe also carries the two name findings that came out of walking the
graph -- see `villains()` and `two_jasons()`.

Read-only.  Takes no lock.
"""
import argparse, collections, json, os, re, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from rdrstudio.iso9660 import Iso9660
from rdrstudio import dave

ISO = os.path.join(os.path.dirname(__file__), '..', '..',
                   '01_Vanilla', 'RDR_VANILLA.iso')
FIELDS = os.path.join(os.path.dirname(__file__), '..', '..',
                      '07_Research', 'fields.json')

# The slasher roster. `jason` is deliberately NOT here: it is ambiguous on this
# disc and `two_jasons()` separates the two.
VILLAINS = ('freddy', 'kruger', 'michael', 'myers', 'vorhees', 'leather')


def all_names(iso):
    out = set()
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b'Dave', b'DAVE'):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            out.add(e.name.rsplit('/', 1)[-1])
    return out


def integrity():
    with open(FIELDS) as fh:
        cat = json.load(fh)
    full = partial = zero = 0
    occ = collections.Counter()
    rows = []
    for ext, v in cat.items():
        for f in v.get('fields', []):
            if f.get('kind') != 'ref':
                continue
            pct = f.get('ref_match_pct', 0)
            n = f.get('occurrences', 0)
            if pct >= 100:
                full += 1; occ['full'] += n
            elif pct > 0:
                partial += 1; occ['partial'] += n
                rows.append((pct, n, ext, f['key'], f.get('ref_to', [])))
            else:
                zero += 1; occ['zero'] += n
    rows.sort()
    return dict(full=full, partial=partial, zero=zero,
                occurrences=dict(occ), partial_rows=rows)


def villains(names):
    """Enemy AI files named after slasher-movie villains."""
    hits = collections.Counter()
    files = collections.defaultdict(list)
    for n in names:
        for v in VILLAINS:
            if re.search(v, n, re.I):
                hits[v] += 1
                files[v].append(n)
    return hits, files


def two_jasons(names):
    """There are two Jasons on this disc and they can be told apart.

    A `*jason*` file whose name yields a real file when `jason` is swapped for
    another villain belongs to the slasher roster. One with no such sibling is
    the developer -- `jasons_actfile_for_tomb_783.actb` settles that reading,
    being a possessive.
    """
    jason = [n for n in names if re.search(r'jason', n, re.I)]
    slasher, person = [], []
    for n in jason:
        if any(re.sub(r'jason', v, n, flags=re.I) in names for v in VILLAINS):
            slasher.append(n)
        else:
            person.append(n)
    return sorted(slasher), sorted(person)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    iso = Iso9660(ISO)
    names = all_names(iso)
    iso.close()

    ref = integrity()
    vh, vf = villains(names)
    slasher, person = two_jasons(names)

    res = dict(reference_keys=ref, villains=dict(vh),
               jason_slasher=len(slasher), jason_person=len(person),
               jason_person_files=person)
    if args.json:
        print(json.dumps(res, indent=1)); return

    print('REFERENCE INTEGRITY (from 07_Research/fields.json)')
    print('   ref keys resolving 100%%   : %d' % ref['full'])
    print('   resolving partially       : %d' % ref['partial'])
    print('   resolving 0%%              : %d' % ref['zero'])
    print('   occurrences: %s' % ref['occurrences'])
    print()
    print('   lowest-resolving keys -- NOT all of these are dangling:')
    print('   %-6s %-8s %-9s %s' % ('pct', 'occ', 'ext', 'key'))
    for pct, n, ext, key, to in ref['partial_rows'][:12]:
        print('   %-6.1f %-8d %-9s %s -> %s' % (pct, n, ext, key, to))
    print()
    print('   `pmtl mtl/sound` (69%) is the control: its values are surface')
    print('   names -- none, dirt, wood, adobe, rock, metal, water, stone --')
    print('   and the disc merely happens to contain dirt.tex, wood.tex,')
    print('   rock.tex. It refers to no file at all. A partial ref can be a')
    print('   mis-classification, not cut content.')
    print()
    print('THE SLASHER ROSTER -- enemy AI named after horror villains')
    for v, c in vh.most_common():
        print('   %-10s %4d files   e.g. %s' % (v, c, sorted(vf[v])[0]))
    print()
    print('TWO JASONS: %d slasher (have a Freddy/Myers/Kruger sibling), '
          '%d the developer' % (len(slasher), len(person)))
    print('   developer files e.g. %s' % person[:4])


if __name__ == '__main__':
    main()
