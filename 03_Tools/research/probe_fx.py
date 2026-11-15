#!/usr/bin/env python3
"""The particle/FX family, and the reference chain that makes it moddable.

`.swptx` 12,944 entries / 788 distinct, `.swptxemitter` 4,436 / 368,
`.arfx` 3,538 / 61, `.fx` 2,286 / 197.  The biggest block left PARTIAL, and
the one that decides how the game LOOKS -- muzzle flashes, blood, dust, fire,
explosions.

They mis-read for a reason none of the earlier text formats had: each file is
a braced block in the ordinary `Key value` grammar followed by a TRAILING
section in a different one -- `Key= value`, with `=`, the same spelling `.rain`
uses -- carrying counted keyframe records:

    swptx {                        <- ordinary grammar, props.py reads this
      NumPoints 400
      TextureName firefx4
      ...
    }
                                   <- and then, outside the braces:
    Keyframes= 5
    Time=  0.000000
    Size=  0.096999 0.093999
    Color= 0.000000 0.000000 0.460990 0.400000

So a reader that stops at the closing brace gets the system's parameters and
none of its animation.  That is the missing coverage.

**The chain this probe exists to establish:**

    .swptxemitter  --names-->  .swptx  --TextureName-->  .tex

An emitter lists the particle systems it spawns BY NAME, and each system names
its texture.  If both hops resolve on the disc, then "make this explosion throw
different particles" and "retexture this effect" are name edits, not binary
surgery.

Read-only.  Takes no lock.
"""
import argparse, collections, json, os, re, sys, zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from rdrstudio.iso9660 import Iso9660
from rdrstudio import dave

ISO = os.path.join(os.path.dirname(__file__), '..', '..',
                   '01_Vanilla', 'RDR_VANILLA.iso')

EXTS = ('fx', 'arfx', 'swptx', 'swptxemitter')
KV = re.compile(r'^([A-Za-z_][\w]*)=\s*(.*)$')


def files(iso):
    seen = set()
    disc = collections.defaultdict(set)
    blobs = collections.defaultdict(dict)
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b'Dave', b'DAVE'):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            base = e.name.rsplit('/', 1)[-1]
            ext = base.rsplit('.', 1)[-1].lower()
            disc[ext].add(base.rsplit('.', 1)[0].lower())
            if ext not in EXTS or base in seen:
                continue
            seen.add(base)
            raw = d.backing.read_at(e.offset, e.size_comp)
            try:
                b = zlib.decompress(raw, -15) if e.compressed else raw
            except zlib.error:
                continue
            blobs[ext][base] = b
    return blobs, disc


def tail_of(text):
    """Everything after the last closing brace -- the `Key= value` section."""
    i = text.rfind('}')
    return text[i + 1:] if i >= 0 else text


def keyframe_checks(text, out, tag):
    """`Keyframes= N` counts the `Time=` records that follow it."""
    lines = [l.strip() for l in tail_of(text).splitlines() if l.strip()]
    pending = None
    seen = 0
    for l in lines:
        m = KV.match(l)
        if not m:
            if pending is not None:
                out['%s: Keyframes= N == Time= records' % tag][
                    pending == seen] += 1
                pending, seen = None, 0
            continue
        k, v = m.group(1), m.group(2).split()
        if k == 'Keyframes':
            if pending is not None:
                out['%s: Keyframes= N == Time= records' % tag][
                    pending == seen] += 1
            pending, seen = int(float(v[0])), 0
        elif k == 'Time' and pending is not None:
            seen += 1
        elif k == 'TuneFrames':
            # NOT one count: the line is a SEQUENCE of `N v1..vN` groups that
            # must exactly consume it.  Reading it as a single count scored
            # 0/788 -- a wrong rule, not a broken format.
            i, good = 0, True
            while i < len(v):
                try:
                    c = int(float(v[i]))
                except ValueError:
                    good = False
                    break
                i += 1 + c
                if i > len(v):
                    good = False
                    break
            out['%s: TuneFrames= groups consume the line' % tag][
                good and i == len(v)] += 1
    if pending is not None:
        out['%s: Keyframes= N == Time= records' % tag][pending == seen] += 1


def emitter_systems(text):
    """`NumSystems= N`, then N blocks each opening with a bare system NAME."""
    lines = [l.strip() for l in tail_of(text).splitlines() if l.strip()]
    declared, names = None, []
    for l in lines:
        m = KV.match(l)
        if m:
            if m.group(1) == 'NumSystems':
                declared = int(float(m.group(2).split()[0]))
            continue
        names.append(l)
    return declared, names


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    iso = Iso9660(ISO)
    blobs, disc = files(iso)
    iso.close()

    checks = collections.defaultdict(lambda: collections.Counter())
    sys_ok = sys_bad = 0
    ref_hit = ref_miss = 0
    missing = collections.Counter()
    tex_hit = tex_miss = 0
    tex_missing = collections.Counter()
    anywhere = {}

    swptx_names = {n.rsplit('.', 1)[0].lower() for n in blobs['swptx']}

    for ext in EXTS:
        for name, b in blobs[ext].items():
            text = b.decode('latin-1')
            keyframe_checks(text, checks, ext)
            if ext == 'swptx':
                m = re.search(r'^\s*TextureName\s+(\S+)', text, re.M)
                if m:
                    t = m.group(1).lower()
                    if t in disc['tex'] or t == 'notexture':
                        tex_hit += 1
                    else:
                        tex_miss += 1
                        tex_missing[t] += 1
                        anywhere[t] = sorted(
                            e for e, names_ in disc.items() if t in names_)
            elif ext == 'swptxemitter':
                declared, names = emitter_systems(text)
                if declared is not None:
                    if declared == len(names):
                        sys_ok += 1
                    else:
                        sys_bad += 1
                for n in names:
                    key = n.lower()
                    # Match the name AS WRITTEN first.  `Explosion_wood_PTX-MOD`
                    # is itself a `.swptx` basename -- stripping the suffix
                    # first turned 71 real hits into "unresolved", which was my
                    # normalisation, not a dangling reference.
                    # Some emitters write the system name WITH its extension.
                    if key.endswith('.swptx'):
                        key = key[:-len('.swptx')]
                    if key not in swptx_names:
                        key = re.sub(r'[-_]ptx-mod$', '', key)
                    if key in swptx_names:
                        ref_hit += 1
                    else:
                        ref_miss += 1
                        missing[n] += 1

    res = dict(counts={e: len(blobs[e]) for e in EXTS},
               checks={k: {'ok': v[True], 'bad': v[False]}
                       for k, v in checks.items()},
               NumSystems=[sys_ok, sys_bad],
               emitter_to_swptx=[ref_hit, ref_miss],
               unresolved_systems=dict(missing.most_common(10)),
               swptx_to_tex=[tex_hit, tex_miss],
               unresolved_textures=dict(tex_missing.most_common(10)),
               unresolved_texture_extensions={k: v for k, v in anywhere.items()})
    if args.json:
        print(json.dumps(res, indent=1)); return

    print('distinct files: %s' % {e: len(blobs[e]) for e in EXTS})
    print()
    print('%-46s %6s %6s' % ('counted-record invariant', 'ok', 'bad'))
    for k in sorted(checks):
        print('%-46s %6d %6d' % (k, checks[k][True], checks[k][False]))
    print('%-46s %6d %6d' % ('swptxemitter: NumSystems= N == named blocks',
                             sys_ok, sys_bad))
    print()
    print('THE REFERENCE CHAIN')
    tot = ref_hit + ref_miss
    print('  .swptxemitter -> .swptx by name : %d / %d  (%.1f%%)'
          % (ref_hit, tot, 100 * ref_hit / tot if tot else 0))
    if missing:
        print('     unresolved: %s' % dict(missing.most_common(6)))
    tot2 = tex_hit + tex_miss
    print('  .swptx -> .tex via TextureName  : %d / %d  (%.1f%%)'
          % (tex_hit, tot2, 100 * tex_hit / tot2 if tot2 else 0))
    if tex_missing:
        print('     unresolved: %s' % dict(tex_missing.most_common(6)))
        nowhere = [k for k, v in anywhere.items() if not v]
        print('     of those, %d/%d appear under NO extension anywhere on the'
              ' disc' % (len(nowhere), len(anywhere)))
        print('     -- dangling references: %s' % sorted(nowhere)[:8])


if __name__ == '__main__':
    main()
