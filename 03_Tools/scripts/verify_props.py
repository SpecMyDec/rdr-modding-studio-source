"""The generic text-format parser reads the disc and rewrites it byte-exact.

Three properties matter, and the first one is the one that was missing:

  1. Structure -- the block walk must end with an empty stack on a file whose
     braces balance, and no field it OFFERS FOR EDITING may hold an unclosed
     `{`. This is new on 2026-09-14 and it is the property whose absence let
     C-033 / REQ-017 live here undetected: `props` used to require a brace to
     own its line, so `Node 0 {` was recorded as an editable field named
     `Node` with the value `0 \t{` and pushed no block. Editing it deleted the
     brace. 270,750 such fields across 11,555 files, and every check below
     passed the whole time.
  2. Coverage -- how many of the disc's text files yield fields.
  3. Fidelity -- `apply(data, {})` must return the input unchanged, and
     writing a field back with its own value must also return the input
     unchanged. These files sit in fixed-size archive slots, so a parser
     that silently reflows whitespace or renormalises numbers would grow a
     file past its slot and corrupt the disc.

Fidelity and structure are checked on every file sampled, not a sub-sample.

WHY "UNCLOSED", NOT "CONTAINS A BRACE". A value may legitimately hold a
balanced `{ ... }` -- `.nav` writes `Links 12 { 52 51 ... }`, `.chase` writes
`SideOffsets: { 4.0 5.5 7.0 }`, one `.enm` field is `0 { }`. 139 fields on the
disc are of that shape and they are fine: the braces are part of the value and
are replaced with it. What is never fine is a field whose value opens a brace
it does not close, because that brace belongs to a block.
"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rdrstudio import Game, props  # noqa: E402
from rdrstudio.config import VANILLA_ISO  # noqa: E402

# Handled by their own parsers; not this one's job.
SKIP = {"rsc", "xrsc", "tex", "mod", "modb", "anim", "stm", "ipu", "pss",
        "pck", "bin", "ico", "psi", "rec", "sys", "hf", "bd", "xls", "pup",
        "fonttex", "iani", "strtbl", "hd", "actb", "stateb"}
MAX_PER_EXT = 60


def main():
    game = Game(VANILLA_ISO)
    seen = Counter()
    files = fielded = 0
    empty = []
    roundtrip_fail = []
    rewrite_fail = []
    drifted = []
    trapped = []
    total_fields = 0

    for arc in game.archives():
        try:
            dv = game.archive(arc)
        except Exception:
            continue
        for e in dv:
            ext = (e.ext or "").lower()
            if ext in SKIP or seen[ext] >= MAX_PER_EXT:
                continue
            try:
                raw = dv.read(e)
            except Exception:
                continue
            if not props.is_textual(raw):
                continue
            seen[ext] += 1
            files += 1

            fs, leftover = props.parse(raw, with_depth=True)
            total_fields += len(fs)

            # 1. STRUCTURE. Only meaningful where the file itself balances --
            # three `x`-prefixed `.duel` files really are unbalanced on the
            # disc, and a parser is not wrong to end deep on those.
            opens = closes = 0
            for ln in raw.split(b"\n"):
                st = ln.strip()
                if st.startswith(b";"):
                    continue
                body = st.split(b";", 1)[0]
                opens += body.count(b"{")
                closes += body.count(b"}")
            if opens == closes and leftover and len(drifted) < 10:
                drifted.append((arc, e.name, leftover))
            for f in fs:
                if f.raw.count("{") > f.raw.count("}"):
                    if len(trapped) < 10:
                        trapped.append((arc, e.name, f.path, f.raw[:40]))
                    break
            if fs:
                fielded += 1
            elif len(empty) < 10:
                empty.append((arc, e.name))

            # identity: no edits must change nothing
            if props.apply(raw, {}) != raw:
                if len(roundtrip_fail) < 10:
                    roundtrip_fail.append((arc, e.name))
                continue
            # rewriting every field with its own text must change nothing
            if fs:
                same = {}
                for f in fs:
                    same.setdefault(f.path, f.raw)
                try:
                    if props.apply(raw, same) != raw:
                        if len(rewrite_fail) < 10:
                            rewrite_fail.append((arc, e.name))
                except Exception as ex:
                    if len(rewrite_fail) < 10:
                        rewrite_fail.append((arc, e.name, str(ex)))
        game._cache.pop(arc.upper(), None)

    print("text files parsed  : %d across %d extensions" % (files, len(seen)))
    print("yielded fields     : %d  (%.1f%%)"
          % (fielded, 100.0 * fielded / files if files else 0))
    print("fields total       : %d" % total_fields)
    print("block stack drift  : %d file(s)" % len(drifted))
    print("fields holding an unclosed brace: %d file(s)" % len(trapped))
    for d in drifted[:5]:
        print("   drifted        : %s / %s  (%d block(s) never closed)" % d)
    for t in trapped[:5]:
        print("   trap field     : %s / %s  %s = %r" % t)
    print("identity rewrite   : %d failure(s)" % len(roundtrip_fail))
    print("self-value rewrite : %d failure(s)" % len(rewrite_fail))
    for r in roundtrip_fail[:5]:
        print("   identity broke : %s / %s" % r[:2])
    for r in rewrite_fail[:5]:
        print("   rewrite broke  : %s / %s" % (r[0], r[1]))
    if empty:
        print("\nparsed but no fields found (first few):")
        for a, n in empty[:6]:
            print("   %s / %s" % (a, n))

    print("\ntop extensions by files sampled")
    for ext, n in seen.most_common(14):
        print("   %-14s %4d" % (ext, n))

    ok = (not roundtrip_fail and not rewrite_fail and not drifted
          and not trapped and files > 1000 and fielded > 0.9 * files)
    print("\n%s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
