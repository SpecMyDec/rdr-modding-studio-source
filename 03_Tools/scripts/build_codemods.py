#!/usr/bin/env python3
"""Build a code-modded copy of a disc from the command line (the Code Mods tab does the same).

    py -3 scripts/build_codemods.py <source.iso> <NEW dest.iso> [noclip,freeze,camlock]
    py -3 scripts/build_codemods.py --check <iso>

The source is only read; the destination must not exist. Credit for the hooks: community reverse-engineering research (see codemods.py).
"""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from rdrstudio import codemods as C

def main():
    a = sys.argv[1:]
    if a and a[0] == '--check':
        print(C.iso_state(a[1])); return 0
    if len(a) < 2:
        print(__doc__); return 2
    feats = a[2].split(',') if len(a) > 2 else C.KEYS
    t0 = time.time()
    res = C.patch_iso(a[0], a[1], feats, progress=lambda m: print('[%4ds] %s' % (time.time() - t0, m), flush=True))
    print('DONE %s' % res)
    return 0

if __name__ == '__main__':
    sys.exit(main())
