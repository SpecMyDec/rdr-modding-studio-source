"""`props` addresses one subscript per key. Should it address many?

`.msd` -- the MoveSetData file, one per character, and the last of M7's nine
formats with no support at all -- is an ordinary `props` tree except for one
thing: it writes doubly-subscripted keys.

    DuelReactTimeScale[0][0]  1.000000

`props._KEY` allows exactly one `[...]` group, so a line like that matches
nothing and is skipped whole. It is not mis-parsed and it is not mis-attributed
-- it is invisible, which is the failure mode this project keeps finding.

The question this probe answers is not "does widening it work" but **"does
widening it CHANGE anything that already parses"**, because `props` is the
backbone of 20,490 text files and a shifted path would break every caller that
keys on one. So it parses every text file on the disc twice, once with each
pattern, and compares (path, start) sets:

    LOST or MOVED   a field the old pattern found that the new one does not,
                    or found at a different offset. Any of these and the
                    change is unsafe.
    GAINED          a field only the new pattern finds. These are the point.

Measured 2026-09-14 on `01_Vanilla`: 20,490 distinct text files, **0 lost or
moved**, 185 files gained -- every `.msd` on the disc (+4,368 fields) plus
`swat.guns` (+40).

Read-only. Never opens the working ISO, never needs the suite lock.

    python3 03_Tools/scripts/probe_props_subscript.py
"""

import collections
import os
import re
import sys

# `scripts/` holds a `dis.py`, and Python puts a script's own directory first
# on sys.path -- which shadows the stdlib `dis` that `inspect` (and so
# `argparse`) imports on 3.13+. Drop this directory and add `03_Tools`.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _HERE]
sys.path.insert(0, os.path.dirname(_HERE))

from rdrstudio import config, props
from rdrstudio.game import Game

# The only difference is the quantifier on the subscript group: ? -> *
WIDENED = re.compile(
    rb"^([ \t]*)([A-Za-z_][A-Za-z0-9_]*"
    rb"(?:[./][A-Za-z0-9_]+)*"
    rb"(?:\[[A-Za-z0-9_]+\])*"
    rb"(?:[./][A-Za-z0-9_]+)*)"
    rb"[ \t]*(?:[:=][ \t]*|[ \t]+)"
    rb"([^\r\n;]*?)[ \t]*(?=[\r\n;]|$)",
    re.M)


def scan(data, pattern):
    """Every field's (path, value offset), under `pattern`."""
    original = props._KEY
    props._KEY = pattern
    try:
        return {(f.path, f.start) for f in props.parse(data)}
    finally:
        props._KEY = original


def main():
    iso = sys.argv[1] if len(sys.argv) > 1 else config.VANILLA_ISO
    game = Game(iso, writable=False)
    print("disc: %s  (read-only)\n" % iso)

    files = gained = broke = 0
    by_ext = collections.Counter()
    examples = []
    breakages = []
    # The disc holds the same file in many archives; (name, size) is enough to
    # scan each distinct one once and keeps this to about a minute.
    seen = set()
    for arc in game.archives():
        dave = game.archive(arc)
        for entry in dave:
            key = (entry.name, entry.size_full)
            if key in seen:
                continue
            seen.add(key)
            try:
                data = dave.read(entry)
            except Exception:                                  # noqa: BLE001
                continue
            if not props.is_textual(data):
                continue
            files += 1
            try:
                old = scan(data, props._KEY)
                new = scan(data, WIDENED)
            except Exception:                                  # noqa: BLE001
                continue
            lost = old - new
            gain = new - old
            if lost:
                broke += 1
                if len(breakages) < 5:
                    breakages.append((arc, entry.name, sorted(lost)[:3]))
            if gain:
                gained += 1
                by_ext[entry.ext] += len(gain)
                if len(examples) < 3:
                    examples.append((entry.name, sorted(gain)[:2]))

    print("distinct text files scanned : %d" % files)
    print("fields LOST or MOVED        : %d file(s)" % broke)
    for row in breakages:
        print("   UNSAFE %s :: %s -- %s" % row)
    print("files that GAIN fields      : %d" % gained)
    for ext, n in by_ext.most_common():
        print("   %-10s +%d" % (ext, n))
    print("total fields gained         : %d" % sum(by_ext.values()))
    for name, paths in examples:
        print("   e.g. %s -> %s" % (name, ", ".join(p for p, _o in paths)))

    if broke:
        print("\nVERDICT: widening is NOT safe -- it moves fields that already "
              "parse.")
        return 1
    print("\nVERDICT: widening is purely additive. Nothing that parses today "
          "parses differently.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
