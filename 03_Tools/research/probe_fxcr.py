#!/usr/bin/env python3
"""`.fxcr` binds effects to SKELETON BONES, and `.reigns` / `.crk` / `.film`.

A cluster of mid-size formats, taken together. The one worth the probe is
`.fxcr` -- a character-effect binder that names the bones an effect attaches
to, which makes it checkable against the `.skel` decode:

    fxcr { Duration 10.0  Loop 0 }
    NumPtxSystems= 0
    NumEmitters= 4
    burning_01                                   <- the effect
    NumEvents= 1
    0.000000 1                                   <- time, flag
    clavicle_l,shoulder_l,elbow_l,wrist_l,neck_01   <- BONES it burns on
    Offset= 0.0 0.0 0.0
    ... x NumEmitters

So "set this character on fire along the left arm" is a bone list in a text
file. The bone names come from `.skel`, which this lane decoded earlier, so
the two formats check each other.

`.reigns` is horse-rein physics and indexes its own mass list
(`LeftHand`/`RightHand`/`Saddle` into `NumMasses`), which is the same
shape of check.

Read-only.  Takes no lock.
"""
import argparse, collections, json, os, re, sys, zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))
import probe_skel as SK
from rdrstudio.iso9660 import Iso9660
from rdrstudio import dave

ISO = SK.ISO
BONELIST = re.compile(r'^[A-Za-z][\w]*(?:,[A-Za-z][\w]*)*$')


def gather(iso):
    out = collections.defaultdict(dict)
    bones, effects = set(), set()
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b'Dave', b'DAVE'):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            n = e.name.rsplit('/', 1)[-1]
            ext = n.rsplit('.', 1)[-1].lower()
            stem = n.rsplit('.', 1)[0].lower()
            if ext in ('swptx', 'swptxemitter', 'ptx'):
                effects.add(stem)
                continue
            if ext not in ('fxcr', 'reigns', 'crk', 'film', 'skel'):
                continue
            raw = d.backing.read_at(e.offset, e.size_comp)
            try:
                b = zlib.decompress(raw, -15) if e.compressed else raw
            except zlib.error:
                continue
            if ext == 'skel':
                try:
                    _, roots = SK.parse_skel(b.decode('ascii'))
                    for _, nd in SK.walk(roots):
                        bones.add(nd['name'].lower())
                except Exception:
                    pass
            else:
                out[ext].setdefault(n, b)
    return out, bones, effects


def parse_fxcr(text):
    """Returns (declared emitters, [(effect, nevents, events, bones)])."""
    lines = [l.rstrip() for l in text.splitlines()]
    i = 0
    declared = None
    while i < len(lines):
        m = re.match(r'\s*NumEmitters=\s*(\d+)', lines[i])
        i += 1
        if m:
            declared = int(m.group(1))
            break
    blocks = []
    while i < len(lines):
        name = lines[i].strip()
        if not name:
            i += 1
            continue
        i += 1
        m = re.match(r'\s*NumEvents=\s*(\d+)', lines[i]) if i < len(lines) else None
        if not m:
            continue
        nev = int(m.group(1)); i += 1
        events = []
        while i < len(lines) and re.match(r'^\s*[-\d.]+\s+[-\d]+\s*$', lines[i]):
            events.append(lines[i].strip()); i += 1
        bl = []
        if i < len(lines) and BONELIST.match(lines[i].strip()):
            bl = lines[i].strip().split(','); i += 1
        while i < len(lines) and lines[i].strip().startswith('Offset='):
            i += 1
        blocks.append((name, nev, events, bl))
    return declared, blocks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    iso = Iso9660(ISO)
    data, bones, effects = gather(iso)
    iso.close()

    em_ok = em_bad = 0
    ev_ok = ev_bad = 0
    b_hit = b_miss = 0
    e_hit = e_miss = 0
    bad_bones = collections.Counter()
    bad_eff = collections.Counter()

    for name, blob in sorted(data['fxcr'].items()):
        declared, blocks = parse_fxcr(blob.decode('latin-1'))
        if declared is not None:
            if declared == len(blocks):
                em_ok += 1
            else:
                em_bad += 1
        for eff, nev, events, bl in blocks:
            if nev == len(events):
                ev_ok += 1
            else:
                ev_bad += 1
            for bn in bl:
                if bn.lower() in bones:
                    b_hit += 1
                else:
                    b_miss += 1; bad_bones[bn] += 1
            if eff.lower() in effects:
                e_hit += 1
            else:
                e_miss += 1; bad_eff[eff] += 1

    r_ok = r_bad = 0
    r_bad_ex = []
    for name, blob in sorted(data['reigns'].items()):
        t = blob.decode('latin-1')
        kv = dict(re.findall(r'^([A-Za-z]+)=\s*([-\d.]+)', t, re.M))
        if 'NumMasses' not in kv:
            continue
        nm = int(float(kv['NumMasses']))
        for k in ('LeftHand', 'RightHand', 'Saddle'):
            if k in kv:
                v = int(float(kv[k]))
                if 0 <= v < nm:
                    r_ok += 1
                else:
                    r_bad += 1
                    if len(r_bad_ex) < 4:
                        r_bad_ex.append((name, k, v, nm))

    res = dict(fxcr_files=len(data['fxcr']),
               NumEmitters=[em_ok, em_bad], NumEvents=[ev_ok, ev_bad],
               bones=[b_hit, b_miss], unresolved_bones=dict(bad_bones.most_common(8)),
               effects=[e_hit, e_miss], unresolved_effects=dict(bad_eff.most_common(8)),
               distinct_skel_bones=len(bones),
               reigns_index=[r_ok, r_bad], reigns_bad=r_bad_ex)
    if args.json:
        print(json.dumps(res, indent=1)); return

    print('.fxcr files: %d   (%d distinct bone names across all .skel)'
          % (len(data['fxcr']), len(bones)))
    print('   NumEmitters= N == emitter blocks  %d ok, %d bad' % (em_ok, em_bad))
    print('   NumEvents=  N == event lines      %d ok, %d bad' % (ev_ok, ev_bad))
    print()
    print('   CROSS-FORMAT: bone names resolve against .skel  %d / %d  (%.1f%%)'
          % (b_hit, b_hit + b_miss,
             100 * b_hit / (b_hit + b_miss) if b_hit + b_miss else 0))
    if bad_bones:
        print('      unresolved: %s' % dict(bad_bones.most_common(6)))
    print('   effect names resolve to .swptx/.swptxemitter/.ptx  %d / %d (%.1f%%)'
          % (e_hit, e_hit + e_miss,
             100 * e_hit / (e_hit + e_miss) if e_hit + e_miss else 0))
    if bad_eff:
        print('      unresolved: %s' % dict(bad_eff.most_common(6)))
    print()
    print('.reigns  hand/saddle index < NumMasses: %d ok, %d out of range'
          % (r_ok, r_bad))
    for x in r_bad_ex:
        print('      %s' % (x,))


if __name__ == '__main__':
    main()
