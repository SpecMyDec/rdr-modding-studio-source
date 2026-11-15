"""Behaviour-table parse coverage across the disc.

Offline. Fails if coverage regresses below what was reached when .actb /
.stateb / .acttbl were decoded, and checks the two properties that make the
parser trustworthy:

  * it never mistakes a run of ASCII for an integer opcode (the
    `bountyhuntermode` -> "opcode 2037673589" trap)
  * set_float / set_int are length-preserving, so an edit always fits its slot
"""

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rdrstudio import Game, actb          # noqa: E402
from rdrstudio import config as cfg        # noqa: E402

MIN_PARSE_RATE = 0.95
EXTS = ("actb", "stateb", "acttbl")


def main():
    iso = sys.argv[1] if len(sys.argv) > 1 else cfg.VANILLA_ISO
    g = Game(iso)

    total = parsed = headerless = errors = 0
    ascii_as_int = 0
    names = floats = ints = 0
    sample = None

    for arc in g.archives():
        try:
            d = g.archive(arc)
        except Exception:
            continue
        for e in d:
            if e.ext not in EXTS:
                continue
            total += 1
            try:
                data = d.read(e)
            except Exception:
                errors += 1
                continue
            if not actb.is_actb(data):
                headerless += 1
                continue
            try:
                _, _, toks = actb.parse(data, limit=4000)
            except Exception:
                errors += 1
                continue
            parsed += 1
            # Pick a sample that actually exercises BOTH editors.
            if sample is None and any(t.kind == "float" for t in toks) \
                    and any(t.kind == "int" for t in toks):
                sample = (arc, e.name, data)
            for t in toks:
                if t.kind == "name":
                    names += 1
                elif t.kind == "float":
                    floats += 1
                elif t.kind == "int":
                    ints += 1
                    if all(32 <= c < 127 for c in struct.pack("<I", t.value)):
                        ascii_as_int += 1
        g._cache.pop(arc.upper(), None)

    rate = parsed / max(1, total)
    print("behaviour files : %d" % total)
    print("  parsed        : %d (%.1f%%)" % (parsed, 100 * rate))
    print("  no header     : %d  (dev junk)" % headerless)
    print("  errors        : %d" % errors)
    print("  tokens        : %d names, %d floats, %d ints" % (names, floats, ints))
    print("  ASCII typed as int : %d  (must be 0 -- the resync trap)" % ascii_as_int)

    ok = True
    if rate < MIN_PARSE_RATE:
        print("\nREGRESSION: parse rate %.1f%% < %.0f%%" % (100 * rate, 100 * MIN_PARSE_RATE))
        ok = False
    if errors:
        print("\nREGRESSION: %d parse errors (expected 0)" % errors)
        ok = False
    if ascii_as_int:
        print("\nREGRESSION: %d words of ASCII reported as integer opcodes"
              % ascii_as_int)
        ok = False

    # Edits must not change length -- that is what keeps them slot-safe.
    if sample:
        arc, name, data = sample
        _, _, toks = actb.parse(data, limit=4000)
        f = next((t for t in toks if t.kind == "float"), None)
        i = next((t for t in toks if t.kind == "int"), None)
        if f:
            out = actb.set_float(data, f.offset, 1.25)
            same = len(out) == len(data)
            back = actb.parse(out, limit=4000)[2]
            landed = any(t.offset == f.offset and abs(t.value - 1.25) < 1e-6
                         for t in back if t.kind == "float")
            print("\nset_float on %s :: %s -> same length %s, value landed %s"
                  % (arc, name, same, landed))
            ok &= same and landed
        if i:
            out = actb.set_int(data, i.offset, 4242)
            print("set_int   -> same length %s" % (len(out) == len(data)))
            ok &= len(out) == len(data)

    print("\n%s" % ("ALL CHECKS PASSED" if ok else "SOMETHING FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
