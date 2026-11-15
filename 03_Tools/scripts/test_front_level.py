"""[M17] FRONT.DAT opens in the level editor, and an edit round-trips.

The tester M17: *"`Front.dat` in the Level Editor (main menu editing)"*.

The main menu is a level. FRONT.DAT holds one `.lvl`, a `.bnds`, three `.znes`,
three `.lnes`, 19 `.mod` and an `.rsc` -- the 3D scene behind the title screen.
`Level(game, "FRONT.DAT")` has parsed all of it since the first commit: 29
nodes, 25 zones, geometry, no errors.

So nothing had to be taught to read it. What kept it out of the editor is one
line: it is not under `LVLS_*`, so `list_levels` never returned it, and the
picker is built from that list. The fix adds it to the CATALOGUE as its own
mode and leaves `list_levels` alone -- section 2 is what holds that line,
because the same list also drives the campaign/Showdown mission mapping, the
disc-wide asset sweep and the link index, and a main-menu entry in any of those
would be wrong.

  1. IT IS REACHABLE FROM THE PICKER the editor actually reads, as its own
     mode, exactly once.
  2. AND `list_levels` STILL DOES NOT RETURN IT -- the ripple guard. Widening
     that function is the obvious fix and the wrong one.
  3. IT OPENS AS A LEVEL: nodes, geometry, no parse errors, and the nodes
     carry the source files they came from.
  4. AN EDIT ROUND-TRIPS OFF THE DISC: a bound moves, the edit is written to
     the working ISO through the editor's own apply path, re-read from a FRESH
     handle at the new position, and restored byte-exactly.

Sections 1-3 read `01_Vanilla` and write nothing. Section 4 writes the working
ISO and restores it in a `finally`, with its own writable handle, then re-reads
to prove the restore landed.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdrstudio import modes                                   # noqa: E402
from rdrstudio.config import MODDED_ISO, VANILLA_ISO          # noqa: E402
from rdrstudio.game import Game                               # noqa: E402
from rdrstudio.level import Level, list_levels                # noqa: E402

ARCHIVE = "FRONT.DAT"
DELTA = (10.0, 2.0, -5.0)
EPS = 1e-3

ok = True
OFFLINE = "--offline" in sys.argv


def check(what, got, want):
    global ok
    good = got == want
    ok &= bool(good)
    print("  %-58s %-14s %s"
          % (what, str(got)[:14], "ok" if good else "FAIL (want %s)" % (want,)))


def main():
    van = Game(VANILLA_ISO)

    print("1. the editor's own picker lists it")
    cat = modes.catalog(van)
    rows = [e for e in cat if e["archive"] == ARCHIVE]
    check("FRONT.DAT appears in the level catalogue", len(rows), 1)
    if not rows:
        print("\nFAIL")
        return 1
    row = rows[0]
    check("...under its own mode", row["mode"], modes.FRONTEND)
    check("...with a label the picker can head a group with",
          row["mode_label"], modes.MODE_LABELS[modes.FRONTEND])
    check("...carrying no mission rows", row["missions"], [])

    print("\n2. and list_levels still does not return it")
    levels = list_levels(van)
    check("list_levels excludes FRONT.DAT", ARCHIVE in levels, False)
    # The catalogue is the level list plus exactly one entry. If this drifts,
    # something widened list_levels after all.
    check("the catalogue is list_levels + exactly one",
          len(cat) - len(levels), 1)

    print("\n3. it opens as a level")
    lvl = Level(van, ARCHIVE)
    check("no parse errors", lvl.errors, [])
    nodes = lvl.nodes_json()
    check("it has nodes", len(nodes) > 0, True)
    check("every node names the file it came from",
          all(n.get("source") for n in nodes), True)
    geo = lvl.geometry()
    check("it has geometry the viewer can draw", "meshes" in geo, True)
    bounds = [n for n in nodes if n["kind"] == "bound"]
    check("it has at least one bound to move", len(bounds) > 0, True)
    if not bounds:
        print("\nFAIL")
        return 1
    print("     %d nodes, %s" % (len(nodes), lvl.stats()))

    if OFFLINE:
        print("\n%s" % ("PARTIAL -- section 4 not run" if ok else "FAIL"))
        return 0 if ok else 1

    print("\n4. an edit round-trips off the disc")
    work = Game(MODDED_ISO, writable=True)
    wlvl = Level(work, ARCHIVE)
    target = next(n for n in wlvl.nodes_json() if n["kind"] == "bound")
    uid, src = target["uid"], target["source"]
    before = list(target["pos"])
    want = [before[i] + DELTA[i] for i in range(3)]
    print("     moving %s (%s) by %s" % (target["name"], src, DELTA))

    wdv = work.archive(ARCHIVE)
    original = wdv.read(src)

    edits = wlvl.move(uid, tuple(want))
    check("the move produces an edit to the node's own file",
          list(edits), [src])
    rep = wlvl.preview(edits)
    check("the edited file still fits its slot", rep[src]["fits"], True)
    print("     %d -> %d bytes, slot %d, headroom %d"
          % (rep[src]["bytes_before"], rep[src]["bytes_after"],
             rep[src]["slot"], rep[src]["headroom"]))
    if not rep[src]["fits"]:
        print("\nFAIL")
        return 1

    restored = False
    try:
        res = wlvl.apply(edits)
        check("the edit applied", res.get("applied"), True)

        # A FRESH handle: re-reading through the one that wrote would prove
        # only that the object in memory remembers what it was told.
        fresh = Game(MODDED_ISO)
        on_disc = fresh.archive(ARCHIVE).read(src)
        check("the bytes on disc actually changed", on_disc != original, True)

        back = Level(fresh, ARCHIVE)
        got = next(n for n in back.nodes_json() if n["uid"] == uid)
        check("it re-opens as the same node", got["name"], target["name"])
        moved = max(abs(got["pos"][i] - want[i]) for i in range(3)) < EPS
        check("...at the position it was moved to", moved, True)
        print("     %s -> %s (wanted %s)" % (before, got["pos"], want))
        # "Nothing changed" is a failure signal, not a pass: if the position
        # never moved, every check above could still be green on a file that
        # was rewritten identically.
        check("...which is not where it started",
              max(abs(got["pos"][i] - before[i]) for i in range(3)) > EPS, True)
    finally:
        # The revert needs its OWN writable handle, and it needs to run even
        # when the body threw -- the run that dies on the way to its own
        # restore is exactly the one that leaves the disc dirty for the next
        # lane.
        wdv.patch(src, original)
        restored = Game(MODDED_ISO).archive(ARCHIVE).read(src) == original

    print("\n   restore:")
    check("the working ISO is back to the bytes it started with",
          restored, True)

    print("\n%s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
