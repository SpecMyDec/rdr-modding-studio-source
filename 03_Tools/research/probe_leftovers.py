#!/usr/bin/env python3
"""Developer leftovers and cut content on the retail disc.

Re-derives GET /api/hidden's numbers read-only from the ISO rather than
trusting the route, then characterises what it found:

  * the .lvl manifests in SWAT.DAT that no shipped level owns, and how much
    of each one's asset list actually survives on the disc;
  * the .enm enemy rosters for level stems that never shipped;
  * name-pattern leftovers ("copy of", "-borked", "before russ ...").

The route calls a manifest `loadable` when ONE of its referenced assets is
present.  That is a floor, not a verdict, so this probe reports the present
fraction per manifest and grades on it.

Read-only.  Takes no lock.
"""
import argparse, json, os, re, sys, zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from rdrstudio.iso9660 import Iso9660
from rdrstudio import dave

ISO = os.path.join(os.path.dirname(__file__), '..', '..',
                   '01_Vanilla', 'RDR_VANILLA.iso')


def archives(iso):
    for f in iso.files():
        if f.size < 16:
            continue
        if iso.read_at(f.offset, 4) in (b'Dave', b'DAVE'):
            yield f.path


def open_dave(iso, path):
    return dave.Dave(dave.IsoBacking(iso, path), label=path)


def read(d, e):
    raw = d.backing.read_at(e.offset, e.size_comp)
    if e.compressed:
        try:
            return zlib.decompress(raw, -15)
        except zlib.error:
            return b''
    return raw



# ---------------------------------------------------------------- traces ----
# Task 4: human traces.  Build paths, debug option tables, source filenames,
# placeholder text and the names of actual people, in the ELF and in every
# archive entry that is mostly printable.

ELF = os.path.join(os.path.dirname(__file__), '..', '..',
                   '07_Research', 'SLUS_205.00.unpacked.bin')

TRACE_PATS = {
    'dos_path': re.compile(rb'[A-Za-z]:[\\/][\w\-. \\/]{4,60}'),
    'unix_path': re.compile(rb'(?:[a-z]:)?/[\w\-.]+/[\w\-./]{6,60}'),
    'source_file': re.compile(rb'[\w\-]{2,40}\.(?:cpp|h|c|inl)\b'),
}

# Named after people.  `livery_truss` matches /russ/ and is NOT one of them --
# the probe reports it so the false positive stays visible rather than being
# quietly filtered into a cleaner-looking answer.
HUMAN_NAME = re.compile(r'russ|jason|before |deleteme|dontuse|\bjunk\b|'
                        r'\bbak\b|borked|arsbak|crap|\bshit\b', re.I)


def trace_elf():
    """Strings in the main executable that came off a developer's machine."""
    out = {}
    with open(ELF, 'rb') as fh:
        b = fh.read()
    for k, p in TRACE_PATS.items():
        out[k] = sorted(set(m.group().decode('latin-1') for m in p.finditer(b)))
    # The debug/command-line option table is one contiguous run of short
    # C strings; anchor on the asset root that sits inside it.
    i = b.find(b's:/swat/assets/current')
    opts = []
    if i >= 0:
        lo = b.rfind(b'\x00\x00', 0, i - 400) + 1
        for m in re.finditer(rb'[\x20-\x7e]{4,}', b[max(0, lo):i + 40]):
            opts.append(m.group().decode('latin-1'))
    out['debug_options'] = opts
    return out


def trace_archives(iso, arcs):
    """The same hunt across every entry on the disc."""
    names, temp_rows = [], []
    for a in arcs:
        d = open_dave(iso, a)
        for e in d.entries:
            if HUMAN_NAME.search(e.name):
                names.append(dict(archive=a, name=e.name, size=e.size_full))
            if e.name == 'swat.txt before russ broke stuff':
                t = read(d, e).decode('utf-16-le', 'replace')
                for r in (x.split('\t') for x in t.splitlines() if x.strip()):
                    if r[0].startswith('TEMP_'):
                        temp_rows.append((r[0], r[3] if len(r) > 3 else ''))
    return names, temp_rows


def check_jack_splice(temp_rows):
    """The protagonist rename, as a reproducible assertion.

    GHOSTINTRO is the original synopsis and names the hero `Union Jack`.
    The other three are copies whose opening was rewritten for `Red` and
    whose remaining rows were never touched -- so they still say `Jack`,
    and the splice left the sentence ungrammatical: one row ends
    `indebted to` and the next begins `position as the star`.
    """
    groups = {}
    for k, s in temp_rows:
        groups.setdefault(k[len('TEMP_JASON_'):].rsplit('_', 1)[0], []).append(s)
    res = {}
    for g, rows in groups.items():
        joined = ' '.join(x.strip('"') for x in rows)
        res[g] = dict(
            names_red='our hero Red' in joined,
            names_union_jack='Union Jack' in joined,
            jack_mentions=joined.count('Jack'),
            broken_splice='indebted to position as the star' in
                          re.sub(r'\s+', ' ', joined))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    iso = Iso9660(ISO)
    arcs = list(archives(iso))

    # Every basename on the whole disc, not just SWAT.DAT.  A manifest's refs
    # may legitimately live in the level archive rather than beside it, and
    # scoring against SWAT alone would under-count a shipped level.
    disc_names, per_arc = set(), {}
    for a in arcs:
        d = open_dave(iso, a)
        ns = {e.name.rsplit('/', 1)[-1].lower() for e in d.entries}
        per_arc[a] = ns
        disc_names |= ns

    # Shipped level stems: one .lvl per level archive under LVLS_*.
    shipped = set()
    for a in arcs:
        stem = a.rsplit('/', 1)[-1]
        if stem.lower().endswith('.dat') and a.upper().startswith('LVLS'):
            shipped.add(stem[:-4].lower())

    swat = open_dave(iso, 'SWAT.DAT')
    swat_names = {e.name.lower() for e in swat.entries}

    manifests = []
    for e in swat.entries:
        if not e.name.lower().endswith('.lvl'):
            continue
        stem = e.name[:-4].replace('lvl_', '').lower()
        text = read(swat, e).decode('latin-1', 'replace')
        refs = [t.strip() for t in
                text.replace('{', ' ').replace('}', ' ').split() if '.' in t]
        refs = [r for r in refs if re.match(r'^[\w\-. ()]+\.\w{1,6}$', r)]
        here = sum(1 for r in refs if r.lower() in swat_names)
        anywhere = sum(1 for r in refs if r.lower() in disc_names)
        manifests.append(dict(
            manifest=e.name, stem=stem, size=e.size_full, refs=len(refs),
            present_swat=here, present_disc=anywhere,
            frac=round(anywhere / len(refs), 3) if refs else 0.0,
            missing=[r for r in refs if r.lower() not in disc_names][:12],
            shipped=stem in shipped or stem[:8] in shipped))

    unshipped = [m for m in manifests if not m['shipped']]

    rosters = []
    for e in swat.entries:
        n = e.name.lower()
        if not n.endswith('.enm'):
            continue
        m = re.match(r'(?:copy of )?lvl_([a-z0-9_\-. ]+?)(?:_var_\d+)?\.enm$', n)
        if not m:
            continue
        st = m.group(1)
        base = re.sub(r'\d+$', '', st)
        if st in shipped or base in shipped or st[:8] in shipped:
            continue
        rosters.append(dict(file=e.name, stem=st, size=e.size_full))

    curios = sorted(n for n in swat_names
                    if 'konami' in n or n.startswith('copy of ')
                    or 'borked' in n or 'demo' in n)

    out = dict(archives=len(arcs), shipped_level_stems=len(shipped),
               manifests=manifests, unshipped=len(unshipped),
               rosters=rosters, curiosities=curios)

    tr = trace_elf()
    names, temp_rows = trace_archives(iso, arcs)
    splice = check_jack_splice(temp_rows)
    out.update(traces=tr, human_named=names, jack_splice=splice)
    iso.close()

    if args.json:
        print(json.dumps(out, indent=1))
        return

    print('archives %d   shipped level stems %d' % (len(arcs), len(shipped)))
    print('manifests %d   unshipped %d   rosters %d   curiosities %d'
          % (len(manifests), len(unshipped), len(rosters), len(curios)))
    print()
    print('%-38s %5s %5s %6s  %s' % ('MANIFEST', 'REFS', 'HAVE', 'FRAC', 'GRADE'))
    for m in sorted(unshipped, key=lambda m: -m['frac']):
        grade = ('COMPLETE' if m['frac'] == 1.0 else
                 'MOSTLY' if m['frac'] >= 0.8 else
                 'PARTIAL' if m['frac'] > 0.0 else 'EMPTY')
        print('%-38s %5d %5d %6.1f%%  %s' % (m['manifest'], m['refs'],
                                             m['present_disc'],
                                             100 * m['frac'], grade))

    print()
    print('=== ELF: paths off the build machine ===')
    for k in ('dos_path', 'unix_path', 'source_file'):
        for s in tr[k]:
            print('  %-12s %s' % (k, s))
    print('  debug option table: %d entries, %s ...'
          % (len(tr['debug_options']), ', '.join(tr['debug_options'][:6])))

    print()
    print('=== entries named by a human: %d ===' % len(names))
    for h in sorted(names, key=lambda h: h['name'])[:40]:
        print('  %-22s %-46s %8d' % (h['archive'], h['name'][:46], h['size']))

    print()
    print('=== the protagonist rename (TEMP_JASON_* intro rows) ===')
    for g, r in sorted(splice.items()):
        print('  %-16s Red=%-5s UnionJack=%-5s Jack x%-2d broken_splice=%s'
              % (g, r['names_red'], r['names_union_jack'],
                 r['jack_mentions'], r['broken_splice']))


if __name__ == '__main__':
    main()
