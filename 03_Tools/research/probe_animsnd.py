#!/usr/bin/env python3
"""`.animsnd` -- the sound track of an animation, and it checks against it.

5,797 entries, 2,420 distinct, and the largest un-attacked PARTIAL after the
FX family.  Layout is three parallel arrays under one count:

    NumKeyframes 8
    animsnd {
      Frame  3 27 29 39 41 59 102 105
      Value  1.0 -2.0 1.0 -2.0 3.0 3.0 -1.0 3.0
      Value2 9.0  2.0 10.0 2.0 91.0 91.0 0.0 81.0
    }

A `.animsnd` is named for the `.anim` it belongs to, so two things are
checkable from outside the file:

  * the name resolves to an animation on the disc;
  * **`max(Frame)` does not exceed that animation's own frame count**, read
    from the `.anim` header at +0x04.

The second is the one worth having: the keyframe numbers are indices into a
clip the parser read separately, so the two files corroborate each other.

Two traps this probe is built around, both of which bit on the first run:

  * 68 files look like they are "missing an array".  They are
    `NumKeyframes 0` with an empty block -- correct, not malformed.  Counting
    them as failures gave 2352/2420 instead of 2420/2420.
  * comparing against the FIRST `.anim` found for a stem would be a sampling
    guess.  It is not one here, and the probe proves it rather than assuming:
    **no stem on the disc carries two different frame counts.**

Read-only.  Takes no lock.
"""
import argparse, collections, json, os, re, struct, sys, zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from rdrstudio.iso9660 import Iso9660
from rdrstudio import dave

ISO = os.path.join(os.path.dirname(__file__), '..', '..',
                   '01_Vanilla', 'RDR_VANILLA.iso')

ARRAYS = ('Frame', 'Value', 'Value2')


def collect(iso):
    snd, frames = {}, collections.defaultdict(set)
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b'Dave', b'DAVE'):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            base = e.name.rsplit('/', 1)[-1]
            ext = base.rsplit('.', 1)[-1].lower()
            stem = base.rsplit('.', 1)[0].lower()
            if ext == 'animsnd':
                if base in snd:
                    continue
                raw = d.backing.read_at(e.offset, e.size_comp)
                try:
                    snd[base] = (zlib.decompress(raw, -15) if e.compressed
                                 else raw)
                except zlib.error:
                    pass
            elif ext == 'anim':
                raw = d.backing.read_at(e.offset, min(e.size_comp, 4096))
                try:
                    h = (zlib.decompressobj(-15).decompress(raw, 16)
                         if e.compressed else raw[:16])
                except zlib.error:
                    continue
                if len(h) >= 8:
                    frames[stem].add(struct.unpack_from('<I', h, 4)[0] & 0xffff)
    return snd, frames


def parse(text):
    m = re.search(r'NumKeyframes\s+(\d+)', text)
    if not m:
        return None, {}
    n = int(m.group(1))
    out = {}
    for k in ARRAYS:
        mm = re.search(r'^\s*%s\s+(.*)$' % k, text, re.M)
        out[k] = mm.group(1).split() if mm else []
    return n, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    iso = Iso9660(ISO)
    snd, frames = collect(iso)
    iso.close()

    ambiguous = {k: sorted(v) for k, v in frames.items() if len(v) > 1}

    shape_ok = shape_bad = 0
    empties = 0
    bad_shape = []
    resolved = unresolved = 0
    unres = []
    fr_ok = fr_bad = 0
    overruns = []
    val = collections.Counter()

    for name, b in sorted(snd.items()):
        text = b.decode('latin-1')
        n, arrs = parse(text)
        if n is None:
            shape_bad += 1
            bad_shape.append((name, 'no NumKeyframes'))
            continue
        if n == 0:
            empties += 1
        if all(len(arrs[k]) == n for k in ARRAYS):
            shape_ok += 1
        else:
            shape_bad += 1
            if len(bad_shape) < 5:
                bad_shape.append((name, {k: len(arrs[k]) for k in ARRAYS}))
        for v in arrs['Value']:
            val[int(float(v))] += 1

        stem = name.rsplit('.', 1)[0].lower()
        if stem in frames:
            resolved += 1
            if arrs['Frame']:
                mx = max(int(float(x)) for x in arrs['Frame'])
                limit = max(frames[stem])
                if mx <= limit:
                    fr_ok += 1
                else:
                    fr_bad += 1
                    if len(overruns) < 6:
                        overruns.append((name, mx, limit))
        else:
            unresolved += 1
            if len(unres) < 6:
                unres.append(name)

    total = len(snd)
    res = dict(files=total,
               shape=[shape_ok, shape_bad], empty_files=empties,
               bad_shape=bad_shape,
               name_resolves=[resolved, total],
               unresolved_examples=unres,
               frame_within_anim=[fr_ok, fr_bad], overruns=overruns,
               ambiguous_anim_stems=ambiguous,
               value_histogram=dict(val.most_common(10)))
    if args.json:
        print(json.dumps(res, indent=1)); return

    print('.animsnd distinct files: %d' % total)
    print('  NumKeyframes == len(Frame) == len(Value) == len(Value2):'
          '  %d ok, %d bad' % (shape_ok, shape_bad))
    print('    (%d of those declare 0 and carry an empty block -- correct,'
          ' not malformed)' % empties)
    for x in bad_shape[:5]:
        print('      %s' % (x,))
    print()
    print('  name resolves to a .anim: %d / %d  (%.1f%%)'
          % (resolved, total, 100 * resolved / total))
    print('    unresolved e.g. %s' % unres[:4])
    print()
    print('  CONTROL: .anim stems carrying more than one frame count: %d'
          % len(ambiguous))
    print('    (so comparing against any one of them is safe, not a guess)')
    print('  max(Frame) <= the animation frame count: %d ok, %d over'
          % (fr_ok, fr_bad))
    for n, mx, lim in overruns:
        print('      %-30s max frame %3d, anim has %3d' % (n, mx, lim))
    print()
    print('  `Value` histogram (the event type): %s'
          % dict(val.most_common(8)))


if __name__ == '__main__':
    main()
