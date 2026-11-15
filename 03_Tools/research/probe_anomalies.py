#!/usr/bin/env python3
"""Do the files that break parsers have names that warn you?

Every decode this lane has done threw off a handful of files that did not fit.
Listed together they look like a coincidence:

    3 `.duel`   unbalanced braces, 2 opens against 6 closes
    2 `.mod`    version string separated by CR, not LF
    1 `.anim`   matches no codec, payload no smoother than a shuffle
    1 `.rec`    size not a multiple of 16 where every sibling is
    4 `.nav`    cached link distances stale against their own node positions
    3 `.swui`   unreadable by any of the three dialects

They are not a coincidence.  This probe scores each anomalous file against the
LEFTOVERS name patterns -- `crap`, `shit`, `x`/`xx`/`xxx` dead prefixes,
`old`/`bak`/`borked`/`deleteme`, `wip` -- and compares that rate against the
base rate of such names across all 127,365 archive entries.

If the two rates were similar the pattern would be nothing.  They are not
similar, and the practical consequence is in `verdict()`: **a tool that hits a
malformed file on this disc should suspect the file before it suspects its own
parser.**

Every anomaly listed here was found by an earlier probe in this directory and
is named in `05_Docs/RESEARCH/UNKNOWNS.md`; this probe re-derives the name
statistics rather than re-deriving the anomalies.

Read-only.  Takes no lock.
"""
import argparse, collections, json, os, re, sys, zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from rdrstudio.iso9660 import Iso9660
from rdrstudio import dave

ISO = os.path.join(os.path.dirname(__file__), '..', '..',
                   '01_Vanilla', 'RDR_VANILLA.iso')

# The LEFTOVERS name vocabulary.  `livery_truss` matches /russ/ and is not a
# leftover; the patterns below are anchored to avoid that class of hit.
JUNK = re.compile(
    r'(^|[_\-. ])(crap\d*|shit\w*|junk|dontuse|deleteme|tempdeleteme|wip|'
    r'borked|bak|old\d*|fortold|copy of|copy \(\d+\) of)([_\-. ]|$)|'
    r'^x{1,3}[_\-]|^x(?=lvl)|before |\.arsbak$', re.I)

# The anomalies, each with the probe that found it.
ANOMALIES = [
    ('xlvl_cover3.duel',        'probe_tail',     'braces 2 open / 6 close'),
    ('xx_lvl_cover3.duel',      'probe_tail',     'braces 2 open / 6 close'),
    ('xxx_lvl_cover3.duel',     'probe_tail',     'braces 2 open / 6 close'),
    ('crap.mod',                'probe_modtext',  'version string CR not LF'),
    ('shitgood.mod',            'probe_modtext',  'version string CR not LF'),
    ('red_shit.anim',           'probe_animraw',  'matches no codec'),
    ('shit.rec',                'probe_unknown7', 'size not a multiple of 16'),
    ('lvl_fortold.nav',         'probe_nav',      '72 stale link distances'),
    ('lvl_perfect.nav',         'probe_nav',      '4 stale link distances'),
    ('old_lvl_cover.nav',       'probe_nav',      '2 stale link distances'),
    ('old2_lvl_cover.nav',      'probe_nav',      '2 stale link distances'),
    ('blank.swui',              'probe_swui',     'no dialect reads it'),
    ('blank_jp.swui',           'probe_swui',     'no dialect reads it'),
    ('wip.swui',                'probe_swui',     'no dialect reads it'),
    # Counterexamples, included ON PURPOSE -- anomalies with innocent names.
    ('lvl_perfnew_hf.rain',     'probe_unknown7', 'a heightfield, misfiled'),
    ('krd_lod_01.mod',          'rdr-fmt REQ-045', '0xCCCCCCCC stub'),
    ('sncb_lod_01.mod',         'rdr-fmt REQ-045', '0xCCCCCCCC stub'),
]


def base_rate(iso):
    """How many of the disc's archive entries carry a leftover name."""
    total = junk = 0
    names = set()
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b'Dave', b'DAVE'):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            total += 1
            names.add(e.name)
            if JUNK.search(e.name):
                junk += 1
    return total, junk, names


def verdict(hit, n, rate):
    lift = (hit / n) / rate if rate and n else 0
    return ('%d of %d anomalous files carry a leftover name (%.0f%%), against '
            'a %.2f%% base rate across the disc -- %.0fx.'
            % (hit, n, 100 * hit / n, 100 * rate, lift))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    iso = Iso9660(ISO)
    total, junk, names = base_rate(iso)
    iso.close()
    rate = junk / total

    rows = []
    for name, probe, why in ANOMALIES:
        rows.append(dict(name=name, probe=probe, why=why,
                         junk_name=bool(JUNK.search(name)),
                         on_disc=name in names))
    hit = sum(1 for r in rows if r['junk_name'])

    res = dict(entries=total, junk_named=junk, base_rate=rate,
               anomalies=rows, junk_anomalies=hit,
               verdict=verdict(hit, len(rows), rate))
    if args.json:
        print(json.dumps(res, indent=1)); return

    print('disc-wide: %d archive entries, %d carry a leftover name (%.2f%%)'
          % (total, junk, 100 * rate))
    print()
    print('%-24s %-16s %-30s %s' % ('anomalous file', 'found by', 'what is wrong',
                                    'leftover name?'))
    for r in rows:
        mark = 'YES' if r['junk_name'] else 'no'
        miss = '' if r['on_disc'] else '   (NOT FOUND ON DISC)'
        print('%-24s %-16s %-30s %s%s'
              % (r['name'], r['probe'], r['why'], mark, miss))
    print()
    print(verdict(hit, len(rows), rate))
    print()
    miss = len(rows) - hit
    print('The %d `no` rows are in the list deliberately. A pattern quoted'
          % miss)
    print('without its exceptions is advocacy: the two 0xCCCCCCCC stubs, the')
    print('misfiled heightfield and both `blank` screens are broken with')
    print('perfectly ordinary names.')
    print()
    print('`lvl_perfect.nav` is the honest near-miss: it WAS superseded, by')
    print('`lvl_perfnew`, so it is a leftover in fact -- but nothing in its')
    print('name says so, and the pattern set does not get credit for it.')
    print()
    print('Practical consequence: on THIS disc, a tool that hits a malformed')
    print('file should check the name before it rewrites the parser.')


if __name__ == '__main__':
    main()
