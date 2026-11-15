"""The .csv reader and writer, checked on every one of the 1,022 files.

Four properties, in the order they matter:

  1. **Fidelity.** `apply(data, {})` must return the input unchanged, and
     rewriting every cell with its own value must ALSO return it unchanged.
     These files sit in fixed-size archive slots -- 28 of them have zero
     spare bytes -- so a writer that renormalised quoting or dropped the
     trailing commas the disc pads rows with would grow a file past its slot.
  2. **Isolation.** One edit changes one cell. Every other cell in the file
     must read back exactly as it did.
  3. **Header placement.** The header is found by shape, not by index. The
     `snd_<num>` family is the evidence: 253 of 256 put it on line 3, and the
     three that do not are the whole reason a constant would be wrong.
  4. **Agreement with the stdlib.** Every non-blank row must match
     `csv.reader` cell for cell. The two DO differ on a blank line -- the
     stdlib yields `[]`, this yields `['']` -- because a row that can be
     addressed is worth more here than one that vanishes.

Fidelity and isolation are checked on every file, not a sample.
"""
import csv as stdcsv
import io
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rdrstudio import Game  # noqa: E402
from rdrstudio import csvfile, sheets  # noqa: E402
from rdrstudio.api import stored_size  # noqa: E402
from rdrstudio.config import VANILLA_ISO  # noqa: E402


def main():
    game = Game(VANILLA_ISO)
    index = game.build_index()
    names = sorted(n for n in index if n.endswith(".csv"))
    swat = game.archive("SWAT.DAT")

    identity = []
    isolation = []
    disagree = []
    blank_rows = 0
    cells = 0
    headers = Counter()
    snd_headers = Counter()
    sections = Counter()
    random.seed(1)

    for name in names:
        data = swat.read(name)
        sheet = csvfile.parse(data)
        sections[len(sheet.sections)] += 1
        headers[sheet.header_row()] += 1
        if name.startswith("snd_") and not name.startswith("snd_cfg"):
            snd_headers[sheet.header_row()] += 1

        # 1. fidelity
        same = {}
        for row in sheet.rows:
            for ci, cell in enumerate(row.cells):
                same["%d.%d" % (row.index, ci)] = cell.text
        cells += len(same)
        try:
            if csvfile.apply(data, {}) != data or csvfile.apply(data, same) != data:
                identity.append(name)
        except Exception as ex:
            identity.append("%s (%s)" % (name, ex))

        # 2. isolation -- one real edit, everything else untouched
        pick = [(r.index, ci) for r in sheet.rows if r.kind in ("data", "header")
                for ci, c in enumerate(r.cells) if c.text.strip()]
        if pick:
            r, c = random.choice(pick)
            old = sheet.cell(r, c).text
            new = "9999" if csvfile._NUM.match(old.strip()) else old + "_x"
            try:
                back = csvfile.parse(csvfile.apply(data, {"%d.%d" % (r, c): new}))
                if back.cell(r, c).text != new:
                    isolation.append((name, r, c, "did not store the value"))
                else:
                    for row in sheet.rows:
                        for ci, cell in enumerate(row.cells):
                            if (row.index, ci) == (r, c):
                                continue
                            if back.cell(row.index, ci).text != cell.text:
                                isolation.append((name, row.index, ci, "collateral"))
                                break
                        else:
                            continue
                        break
            except Exception as ex:
                isolation.append((name, r, c, str(ex)))

        # 4. stdlib agreement, row by row
        mine = [[c.text for c in r.cells] for r in sheet.rows]
        try:
            ref = list(stdcsv.reader(io.StringIO(data.decode("latin-1"),
                                                 newline="")))
        except Exception:
            # One file carries a NUL byte and the stdlib refuses it outright.
            continue
        for a, b in zip(ref, mine):
            if a == b:
                continue
            if a == [] and b == [""]:
                blank_rows += 1
            else:
                disagree.append((name, a[:3], b[:3]))

    print("csv files          : %d, %d copies, %d cells"
          % (len(names), sum(len(index[n]) for n in names), cells))
    print("identity rewrite   : %d failure(s) %s"
          % (len(identity), identity[:4]))
    print("one-edit isolation : %d failure(s) %s"
          % (len(isolation), isolation[:4]))
    print("vs csv.reader      : %d real disagreement(s), %d blank rows "
          "represented differently" % (len(disagree), blank_rows))
    for d in disagree[:4]:
        print("   %s  stdlib %s  ours %s" % d)
    print("sections per file  : %s" % dict(sorted(sections.items())))
    print("first header row   : %s"
          % dict(sorted(headers.items(), key=lambda k: (k[0] is None, k[0]))))
    print("snd_<num> header   : %s  (expect {1: 2, 2: 253, 3: 1})"
          % dict(sorted(snd_headers.items())))

    # The disc's only level -> music link.
    smap = sheets.sound_map(game)
    music = sum(1 for lv in smap["levels"] if lv["music"]["present"])
    streams = sum(1 for lv in smap["levels"] if lv["streams"]["present"])
    sfx = sum(1 for lv in smap["levels"] if lv["sfx"]["present"])
    print("sound map          : %d levels, %d with music, %d streams, %d sfx"
          % (smap["total"], music, streams, sfx))

    rost = sheets.roster(game)
    print("mp roster          : %d characters, %d resolve through swat.strtbl"
          % (rost["total"], rost["resolved"]))

    # THE SURPRISE, kept as a regression: the slot is checked after DEFLATE,
    # so on a file with no headroom a SAME-LENGTH edit can overflow while a
    # LONGER one fits. Any future "same width always fits" shortcut breaks
    # here rather than on someone's disc.
    woods = "snd_cfg_lvl_woods.csv"
    e = swat.find(woods)
    d = swat.read(woods)
    same_len = stored_size(e, csvfile.apply(d, {"1.1": "66"}))
    longer = stored_size(e, csvfile.apply(d, {"1.1": "665"}))
    print("deflate check      : %s slot %d; same-length edit stores %d (%s), "
          "one byte longer stores %d (%s)"
          % (woods, e.slot, same_len, "fits" if same_len <= e.slot else "OVER",
             longer, "fits" if longer <= e.slot else "OVER"))

    ok = (not identity and not isolation and not disagree
          and len(names) > 1000
          and dict(snd_headers) == {1: 2, 2: 253, 3: 1}
          and rost["resolved"] == rost["total"] == 48
          and same_len > e.slot >= longer)
    print("\n%s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
