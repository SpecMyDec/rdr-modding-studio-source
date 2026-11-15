"""[M12] A `.csv` cell edit, and the name the GAME shows changing with it.

M12's criterion: *edit a cell, write, re-read exactly, and assert the
game-side string table sees the change.*

    python3 scripts/verify_csv_edit.py

Read-only: everything is applied and re-parsed IN MEMORY off `01_Vanilla`.
No Studio, no ISO write, no lock.

WHY THIS EXISTS WHEN `verify_csv.py` ALREADY CHECKS 1,022 FILES. That test
proves the reader and writer are honest -- fidelity, isolation, header
placement by shape, agreement with the stdlib -- on every file. All of it is
about the FILE. What it does not do is follow an edit out of the file and into
what the game would display, which is the half M12 actually asks for and the
only half that can tell a correct writer from a useful one.

THE CHAIN, which is the disc's own and not a guess. `mpcharbounty.csv` holds
character codes like `mjck`. The string table does not carry the leading `m`,
so the game resolves `mjck` -> `jck` -> "Jack Swift". Measured by
`sheets.roster`: 48 of 48 resolve with the `m` stripped and 0 of 48 resolve
with it left on. Edit the code cell and the name the game shows changes with
it -- that is the assertion, and it is end to end rather than a round trip.

THE SLOT, NOT THE LENGTH. `verify_csv.py` records the surprise that makes this
necessary: the slot is checked AFTER deflate, so on a file with no headroom a
SAME-LENGTH edit can overflow while a LONGER one fits. 28 of the 1,022 files
have zero spare bytes. So this asserts the stored size against the slot and
never the character count.
"""

import os
import sys
import zlib

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _HERE]
sys.path.insert(0, os.path.dirname(_HERE))

from rdrstudio import config, csvfile, sheets                # noqa: E402
from rdrstudio.game import Game                              # noqa: E402

SHEET = "mpcharbounty.csv"
FAILURES = []


def check(cond, label, detail=""):
    print("  [%s] %-56s %s" % ("PASS" if cond else "FAIL", label, detail))
    if not cond:
        FAILURES.append(label)
    return cond


def cells(doc):
    """Every cell, addressed, so isolation can be compared cell for cell."""
    out = {}
    for i, row in enumerate(doc.rows):
        for j, c in enumerate(row.cells):
            out["%d.%d" % (i, j)] = c.text
    return out


def main():
    g = Game(config.VANILLA_ISO, writable=False)
    print("disc: %s  (read-only, nothing is written)" % config.VANILLA_ISO)
    swat = g.archive("SWAT.DAT")

    print("\n1. the stock chain, csv code -> string table -> shown name:")
    rost = sheets.roster(g)
    check(rost["total"] == 48, "48 multiplayer characters", rost["total"])
    check(rost["resolved"] == rost["total"],
          "every one resolves through swat.strtbl",
          "%d of %d" % (rost["resolved"], rost["total"]))
    named = [c for c in rost["characters"] if c.get("name")]
    check(len(named) >= 2, "at least two carry a shown name", len(named))
    if len(named) < 2:
        return 1
    src, dst = named[0], named[1]
    print("     %-6s -> %r" % (src["code"], src["name"]))
    print("     %-6s -> %r" % (dst["code"], dst["name"]))
    check(src["name"] != dst["name"],
          "the two show different names, so a swap is observable")

    data = swat.read(SHEET)
    doc = csvfile.parse(data)
    before = cells(doc)
    addr = None
    for i, row in enumerate(doc.rows):
        if row.kind == "data" and row.cells \
                and row.cells[0].text.strip() == src["code"]:
            addr = "%d.0" % i
            break
    check(addr is not None, "found the cell holding %s" % src["code"], addr)
    if addr is None:
        return 1

    print("\n2. edit one cell, and re-read it exactly:")
    out = csvfile.set_fields(data, {addr: dst["code"]})
    doc2 = csvfile.parse(out)
    after = cells(doc2)
    check(after.get(addr, "").strip() == dst["code"],
          "the edited cell reads back as the new value",
          "%s = %r" % (addr, after.get(addr)))
    moved = [k for k in before if before[k] != after.get(k)]
    check(moved == [addr], "exactly one cell changed", moved[:4])
    check(len(after) == len(before),
          "no row or column was added or lost",
          "%d cells" % len(after))

    print("\n3. the slot, measured after DEFLATE and never by length:")
    e = swat.find(SHEET)
    co = zlib.compressobj(9, 8, -15)
    stored = len(co.compress(out) + co.flush()) if e.compressed else len(out)
    check(stored <= e.slot, "the edited sheet still fits its archive slot",
          "%d of %d stored (%d spare)" % (stored, e.slot, e.slot - stored))
    if len(out) == len(data) and stored != e.size_comp:
        print("     same length (%d) but stored moved %d -> %d -- which is why"
              % (len(out), e.size_comp, stored))
        print("     this is asserted against the slot and not the character "
              "count")

    print("\n4. THE GAME-SIDE STRING TABLE SEES IT:")
    # Resolved exactly the way sheets.roster does it, because that rule is the
    # disc's: strip the leading `m`, then look the stem up in swat.strtbl.
    names = sheets._display_names(swat)
    shown_before = names.get(sheets._stem(src["code"]))
    shown_after = names.get(sheets._stem(after[addr].strip()))
    check(shown_before == src["name"],
          "before: that roster row shows %r" % shown_before)
    check(shown_after == dst["name"],
          "after:  the same row now shows %r" % shown_after)
    check(shown_after != shown_before,
          "the name the game would display actually changed",
          "%r -> %r" % (shown_before, shown_after))
    # And the rule itself, re-measured rather than trusted: the `m` matters.
    check(names.get(after[addr].strip()) is None,
          "the code does NOT resolve with its leading m left on",
          repr(after[addr].strip()))

    print("\nSUMMARY")
    print("  [%s] a csv cell edit reaches the name the game shows"
          % ("FAIL" if FAILURES else "PASS"))
    if FAILURES:
        print("  failed: %s" % ", ".join(FAILURES))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
