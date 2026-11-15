"""probe_coverage.py -- which extensions does the tool actually touch today?

For every extension found by probe_inventory.py, grep 03_Tools/rdrstudio/ for
the extension as a quoted literal (".foo", "foo") and report which modules
mention it. A mention is NOT proof of a parser -- this only narrows the list a
human has to read. The DECODED/PARTIAL/UNKNOWN verdict in UNKNOWNS.md is set by
reading the module, never by this script alone.

    python3 03_Tools/research/probe_coverage.py
"""

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
STUDIO = os.path.join(REPO, "03_Tools", "rdrstudio")
INV = os.path.join(REPO, "07_Research", "inventory.json")


def load_sources():
    src = {}
    for name in sorted(os.listdir(STUDIO)):
        if name.endswith(".py"):
            with open(os.path.join(STUDIO, name), encoding="utf-8", errors="replace") as fh:
                src[name] = fh.read()
    return src


def main():
    inv = json.load(open(INV))
    src = load_sources()
    rows = []
    for ext, s in inv["by_ext"].items():
        if ext.startswith("::loose::"):
            label, e = "loose:" + ext[9:], ext[9:]
        else:
            label, e = ext, ext
        if e in ("(none)", ""):
            continue
        pat = re.compile(r"""['"]\.?%s['"]|\.%s\b""" % (re.escape(e), re.escape(e)), re.I)
        hits = sorted(m for m, text in src.items() if pat.search(text))
        rows.append((s["count"], label, hits))
    rows.sort(key=lambda r: -r[0])

    untouched = [r for r in rows if not r[2]]
    print("%-16s %7s  %s" % ("EXT", "COUNT", "MODULES MENTIONING IT"))
    for count, label, hits in rows:
        print("%-16s %7d  %s" % (label, count, ", ".join(hits) if hits else "-- NONE --"))
    print("\n%d extensions, %d with no mention anywhere in rdrstudio/"
          % (len(rows), len(untouched)))
    print("no-mention entries cover %d files"
          % sum(c for c, _, h in rows if not h))


if __name__ == "__main__":
    main()
