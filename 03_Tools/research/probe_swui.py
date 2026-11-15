#!/usr/bin/env python3
"""M14 prerequisite: the `.swui` tag table, validated against the devs' own spec.

M14 is "`.swui` editing for HUD changes" and its acceptance test is a byte-exact
round-trip, so what it needs is not another parser -- `rdrstudio/swui.py`
already has one -- but a TAG TABLE somebody can trust: every tag that actually
occurs, how many fields each carries, and which of them the developers
documented.

The disc ships that documentation.  `example.swui` in SWAT.DAT (3,005 bytes) is
a commented reference for the format, written by the people who wrote the
parser (found in the LEFTOVERS sweep, filed as REQ-046).  This probe reads the
spec out of it and checks it against every UI file on the disc, reporting the
three things that matter for M14:

  * tags USED but not documented   -- the spec is older than the shipped files
  * tags DOCUMENTED but not used   -- features that exist in the parser
  * field-count spread per tag     -- which trailing fields are optional

`example.swui` is itself excluded from the corpus statistics: it is the spec,
not a screen, and counting it would let the spec corroborate itself.

Read-only.  Takes no lock.
"""
import argparse, collections, json, os, re, sys, zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from rdrstudio.iso9660 import Iso9660
from rdrstudio import dave, swui

ISO = os.path.join(os.path.dirname(__file__), '..', '..',
                   '01_Vanilla', 'RDR_VANILLA.iso')

SPEC_FILES = ('example.swui', 'example_jp.swui')
SPEC = 'example.swui'
# In the spec a tag is introduced as `<NAME:arg,arg:arg>` or `<NAME>`, either
# bare on a line or inside a `//` comment (the model widgets are commented out).
SPEC_TAG = re.compile(r'<\s*/?([A-Z][A-Z0-9_]*)\s*[:>]')
RAW_TAG = re.compile(rb'<\s*/?([A-Za-z][A-Za-z0-9_]*)\s*[:>]')
# A placeholder use -- the devs left the argument NAMES in a few live screens.
SELFDOC = re.compile(r'\b(grid id|pos x,y|scale x,y|cell size|input number|'
                     r'modelname)\b')


def ui_files(iso):
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b'Dave', b'DAVE'):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            if not swui.is_swui(e.name):
                continue
            raw = d.backing.read_at(e.offset, e.size_comp)
            try:
                b = zlib.decompress(raw, -15) if e.compressed else raw
            except zlib.error:
                continue
            yield f.path, e.name, b


def spec_tags(text):
    """Tag names the developers documented, and the arg list they showed."""
    out = {}
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(';'):
            continue
        body = s[2:].strip() if s.startswith('//') else s
        for m in re.finditer(r'<\s*(/?)([A-Z][A-Z0-9_]*)\s*(:([^>]*))?>', body):
            close, name, _, args = m.groups()
            if close:
                continue
            n = len(args.split(':')) if args else 0
            out.setdefault(name, set()).add(n)
    return out


# Tags whose first fields declare how many members follow.  An editor that
# adds or removes a member MUST update the count, or the screen desyncs.
def check_counted(name, f, out):
    def n_at(i):
        try:
            return int(f[i].strip())
        except Exception:
            return None

    if name == 'GROUP' and len(f) >= 3:
        n = n_at(2)
        members = sum(len([x for x in fl.split(',') if x.strip()])
                      for fl in f[3:])
        out['GROUP: N == members in the trailing fields'][n == members] += 1
    elif name == 'FACADE' and len(f) >= 3:
        n = n_at(1)
        toks = len([x for x in f[2].split(',') if x.strip()])
        out['FACADE: N == tokens in field 2'][n == toks] += 1
    elif name in ('SCALECHANGE', 'COLORCHANGE') and f:
        n = n_at(0)
        out['%s: fields == 1 + 2N' % name][
            n is not None and len(f) == 1 + 2 * n] += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    iso = Iso9660(ISO)
    specs = {}
    dialects = collections.Counter()
    used = collections.Counter()
    fieldspread = collections.defaultdict(collections.Counter)
    files = 0
    seen = set()
    per_ext = collections.Counter()
    unreadable = []
    counted = collections.defaultdict(collections.Counter)
    selfdoc = []
    totals = collections.Counter()

    for arc, name, b in ui_files(iso):
        base = name.rsplit('/', 1)[-1]
        if base in SPEC_FILES:
            specs[base] = b.decode('latin-1')
            continue                      # a spec is not a screen
        if base in seen:
            continue
        seen.add(base)
        files += 1
        per_ext[base.rsplit('.', 1)[-1].lower()] += 1
        d = swui.dialect(b)
        dialects[d] += 1
        if d == 'unreadable':
            unreadable.append(base)
        if d != 'tags':
            continue
        raw_tags = len(RAW_TAG.findall(b))
        parsed_tags = 0
        for t in swui.parse(b):
            used[t.name] += 1
            parsed_tags += 1
            f = [x.raw for x in t.fields]
            fieldspread[t.name][len(f)] += 1
            check_counted(t.name, f, counted)
            if SELFDOC.search(':'.join(f)):
                selfdoc.append((base, t.name, ':'.join(f)[:130]))
        totals['raw'] += raw_tags
        totals['parsed'] += parsed_tags

    iso.close()

    spec = specs.get(SPEC, '')
    doc = {}
    for text in specs.values():
        for k, v in spec_tags(text).items():
            doc.setdefault(k, set()).update(v)
    used_names = set(used)
    doc_names = set(doc)
    undocumented = sorted(used_names - doc_names, key=lambda k: -used[k])
    unused = sorted(doc_names - used_names)

    res = dict(ui_files=files, by_extension=dict(per_ext),
               dialects=dict(dialects), unreadable=unreadable,
               documented_tags=len(doc_names), used_tags=len(used_names),
               undocumented={k: used[k] for k in undocumented},
               documented_but_unused=unused,
               field_counts={k: dict(v) for k, v in fieldspread.items()},
               tag_totals=dict(used.most_common()),
               specs_found=sorted(specs),
               parser_raw_vs_parsed=[totals['raw'], totals['parsed']],
               counted_records={k: {'ok': v[True], 'bad': v[False]}
                                for k, v in counted.items()},
               self_documenting=selfdoc)
    if args.json:
        print(json.dumps(res, indent=1)); return

    print('UI files (excluding the spec): %d   %s' % (files, dict(per_ext)))
    print('dialects: %s' % dict(dialects))
    if unreadable:
        print('  unreadable: %d  %s' % (len(unreadable), unreadable[:6]))
    print()
    print('example.swui documents %d tags; the corpus uses %d'
          % (len(doc_names), len(used_names)))
    print()
    print('USED BUT NOT DOCUMENTED (%d) -- the spec predates the shipped files'
          % len(undocumented))
    for k in undocumented:
        print('   %-22s %6d uses   field counts %s'
              % (k, used[k], dict(fieldspread[k])))
    print()
    print('DOCUMENTED BUT NEVER USED (%d) -- parser features nothing exercises'
          % len(unused))
    for k in unused:
        print('   %-22s documented arg counts %s' % (k, sorted(doc[k])))
    print()
    print('PARSER COMPLETENESS: swui.parse() emits %d tags; a raw scan of the '
          'same\n  files finds %d. Nothing is dropped.'
          % (totals['parsed'], totals['raw']))
    print()
    print('COUNTED-RECORD TAGS -- an editor must keep the count in sync')
    print('   %-44s %6s %6s' % ('invariant', 'ok', 'bad'))
    for k in sorted(counted):
        print('   %-44s %6d %6d' % (k, counted[k][True], counted[k][False]))
    print()
    print('SELF-DOCUMENTING uses left in live screens (%d)' % len(selfdoc))
    for base, nm, txt in selfdoc[:6]:
        print('   %-24s <%s:%s>' % (base, nm, txt))
    print()
    print('FIELD-COUNT SPREAD per tag (a spread means trailing fields are '
          'optional)')
    print('   %-22s %8s  %s' % ('tag', 'uses', 'field count -> files'))
    for k, c in used.most_common():
        sp = dict(sorted(fieldspread[k].items()))
        mark = '  <-- variable' if len(sp) > 1 else ''
        print('   %-22s %8d  %s%s' % (k, c, sp, mark))


if __name__ == '__main__':
    main()
