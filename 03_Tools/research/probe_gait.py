#!/usr/bin/env python3
"""`.gait` -- the locomotion parameter file, decoded across the whole disc.

5,159 entries on the disc and `props.py` reads 14.9% of the keys, because the
generic reader handles `key value` lines and stops at the counted float
matrices.  This probe parses the whole file, asserts every declared count
against what follows it, round-trips byte-exactly, and then tests what the
matrix columns actually MEAN against the header scalars that sit above them.

Structure (all of it plain ASCII, Angel Studios `key value` grammar):

    Axis IdealSpeed StrideLength MaxPeriod VelScale SlewVelScale
    AngVelScale DistComfort2 AngComfort      -- 9 header scalars
    NumSteps: N
    StepData {                               -- N of these
      Stance: x y z
      Trigger: f
      DutyFactor: f
      NumFrames: F { F rows x 6 floats }
      NumStandFrames: S { S rows x 2 floats }
    }

Read-only.  Takes no lock.
"""
import argparse, collections, json, math, os, random, re, sys, zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from rdrstudio.iso9660 import Iso9660
from rdrstudio import dave

ISO = os.path.join(os.path.dirname(__file__), '..', '..',
                   '01_Vanilla', 'RDR_VANILLA.iso')

SCALARS = ('Axis', 'IdealSpeed', 'StrideLength', 'MaxPeriod', 'VelScale',
           'SlewVelScale', 'AngVelScale', 'AngComfort', 'DistComfort2')

# `NumFrames: 20 {` declares a count AND opens a block.  The count is the
# number of ROWS that follow, not the number of numbers -- getting that
# backwards is the same mistake that produced 34,942 false `Num*` mismatches
# in probe_textgrammar.py, so the width is asserted separately per row.
COUNTED = {'NumFrames': 6, 'NumStandFrames': 2}


class GaitError(Exception):
    pass


def tokens(text):
    """Braces are TOKENS, not line markers -- the lesson from the .nav pass."""
    return text.replace('{', ' { ').replace('}', ' } ').split()


def parse(text):
    """Parse one .gait into a dict.  Raises GaitError on any mismatch."""
    tk = tokens(text)
    i, out = 0, {'steps': []}

    def want(x):
        nonlocal i
        if i >= len(tk) or tk[i] != x:
            raise GaitError('expected %r at token %d, got %r'
                            % (x, i, tk[i] if i < len(tk) else '<eof>'))
        i += 1

    while i < len(tk):
        key = tk[i].rstrip(':')
        if key == 'StepData':
            i += 1
            want('{')
            step = {}
            while tk[i] != '}':
                k = tk[i].rstrip(':')
                i += 1
                if k in COUNTED:
                    n = int(tk[i]); i += 1
                    want('{')
                    width = COUNTED[k]
                    rows = []
                    while tk[i] != '}':
                        row = tk[i:i + width]
                        if len(row) < width:
                            raise GaitError('%s: short row' % k)
                        rows.append([float(v) for v in row])
                        i += width
                    want('}')
                    if len(rows) != n:
                        raise GaitError('%s declared %d, found %d'
                                        % (k, n, len(rows)))
                    step[k] = rows
                elif k == 'Stance':
                    step[k] = [float(tk[i + j]) for j in range(3)]; i += 3
                else:
                    step[k] = float(tk[i]); i += 1
            want('}')
            out['steps'].append(step)
        else:
            i += 1
            out[key] = float(tk[i]); i += 1

    n = int(out.get('NumSteps', -1))
    if n != len(out['steps']):
        raise GaitError('NumSteps declared %d, found %d StepData blocks'
                        % (n, len(out['steps'])))
    return out


def gaits(iso):
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b'Dave', b'DAVE'):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            if not e.name.lower().endswith('.gait'):
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
    ap.add_argument('--limit', type=int, default=0)
    args = ap.parse_args()

    iso = Iso9660(ISO)
    ok = fail = 0
    errs = collections.Counter()
    keys = collections.Counter()
    widths = collections.Counter()
    # semantic tests -- see the docstring of report() for what each one is
    # testing and what its control is.
    stand_zero = stand_all = 0
    names = set()
    rad = collections.Counter()      # cols within [-pi, pi]
    half = collections.Counter()     # cols within [-pi/2, pi/2]
    colmax = collections.defaultdict(float)
    coltot = 0
    axis_ok = axis_n = 0
    dom = collections.Counter()      # which column actually dominates
    axis_pairs = []
    sign_ok = sign_n = 0
    travel = []
    still = 0
    PI = math.pi + 1e-4

    for arc, name, b in gaits(iso):
        if args.limit and ok + fail >= args.limit:
            break
        names.add(name)
        try:
            text = b.decode('ascii')
        except UnicodeDecodeError:
            fail += 1; errs['not ascii'] += 1; continue
        try:
            g = parse(text)
        except (GaitError, ValueError, IndexError) as ex:
            fail += 1; errs[str(ex)[:60]] += 1; continue
        ok += 1
        for k in g:
            if k != 'steps':
                keys[k] += 1
        ax = int(g.get('Axis', -1))
        sl = g.get('StrideLength', 0.0)
        for st in g['steps']:
            for k in st:
                keys[k] += 1
            fr = st.get('NumFrames') or []
            if fr:
                widths[len(fr[0])] += 1
                coltot += 1
                for c in range(6):
                    col = [r[c] for r in fr]
                    if all(abs(v) <= PI for v in col):
                        rad[c] += 1
                    if all(abs(v) <= PI / 2 for v in col):
                        half[c] += 1
                    colmax[c] = max(colmax[c], max(abs(v) for v in col))
                # Which column carries the translation, and does `Axis` say so?
                n0 = abs(fr[-1][0] - fr[0][0])
                n2 = abs(fr[-1][2] - fr[0][2])
                if max(n0, n2) < 0.05:
                    still += 1
                else:
                    pick = 0 if n0 > n2 else 2
                    dom[pick] += 1
                    axis_n += 1
                    axis_pairs.append((ax, pick))
                    if pick == (0 if ax == 0 else 2):
                        axis_ok += 1
                    sel = fr[-1][0 if ax == 0 else 2] - fr[0][0 if ax == 0 else 2]
                    travel.append(abs(sel))
                    if abs(sel) > 0.05 and abs(sl) > 1e-6:
                        sign_n += 1
                        if (sel > 0) == (sl > 0):
                            sign_ok += 1
            sf = st.get('NumStandFrames') or []
            if sf:
                stand_all += 1
                if all(abs(v) < 1e-9 for r in sf for v in r):
                    stand_zero += 1

    iso.close()
    total = ok + fail

    # CONTROLS for the Axis claim.  97% means nothing until you know what a
    # constant answer scores: 66% of files say Axis=1 and 64% of step blocks
    # are col2-dominant, so "always col2" is the baseline to beat, and a
    # shuffled Axis column is the null.
    base = max(dom.values()) / axis_n if axis_n else 0.0
    rnd = random.Random(0)
    shuffled = [a for a, _ in axis_pairs]
    rnd.shuffle(shuffled)
    null = (sum(1 for a, (_, pk) in zip(shuffled, axis_pairs)
                if pk == (0 if a == 0 else 2)) / axis_n) if axis_n else 0.0

    travel.sort()
    res = dict(files=total, parsed=ok, failed=fail,
               distinct_names=len(names),
               errors=dict(errs.most_common(10)),
               frame_widths=dict(widths),
               standframes_all_zero=[stand_zero, stand_all],
               within_pi={c: [rad[c], coltot] for c in range(6)},
               within_half_pi={c: [half[c], coltot] for c in range(6)},
               col_max={c: round(colmax[c], 4) for c in range(6)},
               axis_selects_travel=[axis_ok, axis_n],
               axis_baseline_constant=round(base, 4),
               axis_null_shuffled=round(null, 4),
               sign_matches_stridelength=[sign_ok, sign_n],
               no_translation_blocks=still,
               travel_median=round(travel[len(travel) // 2], 4) if travel else None,
               keys=dict(keys.most_common()))

    if args.json:
        print(json.dumps(res, indent=1))
        return

    print('.gait files %d   parsed %d   failed %d   distinct names %d'
          % (total, ok, fail, len(names)))
    if errs:
        print('failures:')
        for e, c in errs.most_common(10):
            print('   %5d  %s' % (c, e))
    print('frame row widths: %s' % dict(widths))
    print('NumStandFrames all zero: %d / %d' % (stand_zero, stand_all))

    print()
    print('COLUMN TYPE -- angles are bounded, positions are not')
    print('%-6s %16s %16s %12s' % ('', 'within +-pi', 'within +-pi/2', 'max |v|'))
    for c in range(6):
        print('  col%d %10d/%-5d %10d/%-5d %12.4f'
              % (c, rad[c], coltot, half[c], coltot, colmax[c]))

    print()
    print('AXIS selects the translation column (0 -> col0, 1 -> col2)')
    print('  Axis-as-selector      %5d / %-5d = %5.1f%%'
          % (axis_ok, axis_n, 100 * axis_ok / axis_n))
    print('  CONTROL best constant                   %5.1f%%' % (100 * base))
    print('  CONTROL shuffled Axis                   %5.1f%%' % (100 * null))
    print('  (%d step blocks excluded: no translation on either axis)' % still)
    print('  |travel| on the selected column, median %.3f' % travel[len(travel) // 2])

    print()
    print('REJECTED: sign(travel) == sign(StrideLength)  %d / %d = %.1f%%'
          ' -- near chance, not a rule'
          % (sign_ok, sign_n, 100 * sign_ok / sign_n))
    print('\nkeys seen:')
    for k, c in keys.most_common():
        print('   %-16s %6d' % (k, c))


if __name__ == '__main__':
    main()
