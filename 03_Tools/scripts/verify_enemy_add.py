"""[M11] Adding an enemy to a `.enm` by chunk copy, with a unique name.

The tester: *"copy an existing data chunk, paste it, give it a unique name."*
M11's criterion: add a new enemy by chunk copy, assert the file re-parses with
N+1 enemies **and a unique name**.

    python3 scripts/verify_enemy_add.py

Read-only: everything is spliced and re-parsed IN MEMORY off `01_Vanilla`, so
this needs no Studio, writes no ISO and takes no lock.

WHY THIS EXISTS WHEN `verify_enemy_edit.py` ALREADY PLACES AN ENEMY. That test
proves the N+1 half well -- "SHER70 gains exactly one enemy", the new block
carrying the Type/Table/Name it was asked for, its position and angle, and all
11 required fields. What nothing asserted anywhere was **the unique name**,
which is the half the tester actually asked for and the half with a real failure
mode: two enemies sharing a Name in one file.

THE THREE CASES, because "unique" means different things in each:

  1. **No name given.** The name is generated from the template's Type and must
     not collide with any Name already in the file.
  2. **A name given that is already taken.** It must NOT be written as-is.
     Measured: `BlueBeard2` comes back `BlueBeard2_copy`, and exactly one block
     still carries `BlueBeard2`.
  3. **The cost of uniquifying is not free**, and this is the part worth
     pinning. `scene.unique_name` follows the game's own `_NN` counter when the
     base ends in one, and falls back to appending `_copy` when it does not --
     `BlueBeard2` has no underscore before its digit, so it takes the longer
     path. Its own docstring records why that matters: these blocks live in
     fixed-size slots and a placement has been refused for overrunning by a
     single byte. So the test asserts the name is unique AND reports what the
     uniquifying cost in bytes, rather than treating the name as free text.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _HERE]
sys.path.insert(0, os.path.dirname(_HERE))

from rdrstudio import config, scene                          # noqa: E402
from rdrstudio import enemyfields as EF                      # noqa: E402
from rdrstudio.game import Game                              # noqa: E402
from rdrstudio.level import Level                            # noqa: E402

ARCHIVE = "LVLS_SNG/SHER70.DAT"
FAILURES = []


def check(cond, label, detail=""):
    print("  [%s] %-58s %s" % ("PASS" if cond else "FAIL", label, detail))
    if not cond:
        FAILURES.append(label)
    return cond


def main():
    g = Game(config.VANILLA_ISO, writable=False)
    print("disc: %s  (read-only, nothing is written)" % config.VANILLA_ISO)

    lvl = Level(g, ARCHIVE)
    nodes = lvl.enemy_nodes()
    template = EF.smallest_block(nodes)
    check(template is not None, "there is a block to clone the shape of")
    if template is None:
        return 1
    target = template.source
    data = lvl._read(target)
    before_names = scene.existing_enemy_names(data)
    before = scene.parse_enemies(data, target)
    print("\n%s :: %s -- %d enemies, %d distinct names"
          % (ARCHIVE, target, len(before), len(before_names)))
    check(len(before_names) == len(before),
          "the stock file has no duplicate names to begin with",
          "%d names / %d blocks" % (len(before_names), len(before)))

    print("\n1. a chunk copy with NO name given:")
    edits, auto, _ = lvl.place_enemy(target, template, [1.0, 2.0, 3.0], 0.0,
                                     {}, None)
    out = scene.splice(data, edits[target])
    after = scene.parse_enemies(out, target)
    check(len(after) == len(before) + 1, "the file re-parses with N+1 enemies",
          "%d -> %d" % (len(before), len(after)))
    check(auto not in before_names, "the generated name is unique", repr(auto))
    names_after = scene.existing_enemy_names(out)
    check(len(names_after) == len(after),
          "every block in the result still has a distinct name",
          "%d names / %d blocks" % (len(names_after), len(after)))
    check(after[-1].props.get("Name") == auto,
          "the new block carries that name", repr(after[-1].props.get("Name")))

    print("\n2. a chunk copy asking for a name that is ALREADY TAKEN:")
    taken = sorted(before_names)[0]
    lvl2 = Level(g, ARCHIVE)
    edits2, got, _ = lvl2.place_enemy(target, template, [1.0, 2.0, 3.0], 0.0,
                                      {}, taken)
    out2 = scene.splice(lvl2._read(target), edits2[target])
    after2 = scene.parse_enemies(out2, target)
    check(len(after2) == len(before) + 1, "still N+1 enemies",
          "%d -> %d" % (len(before), len(after2)))
    check(got != taken, "the requested name was NOT written as-is",
          "asked %r, got %r" % (taken, got))
    still = [n for n in after2 if n.props.get("Name") == taken]
    check(len(still) == 1,
          "exactly one block still carries the original name",
          "%d block(s)" % len(still))
    check(len(scene.existing_enemy_names(out2)) == len(after2),
          "no two blocks share a name after the add")

    print("\n3. what the uniquifying cost, in bytes:")
    # Not decoration. These blocks sit in fixed-size slots and
    # scene.unique_name's own docstring records a placement refused for
    # overrunning by one byte, so the length of the name it picks is a real
    # constraint and not a cosmetic choice.
    cost = len(got) - len(taken)
    print("     %r -> %r  (+%d bytes)" % (taken, got, cost))
    check(cost >= 0, "the unique name is not shorter than what was asked for")
    m_auto = len(auto) - len(str(template.props.get("Type") or ""))
    print("     the generated name %r follows the game's own _NN convention"
          % auto if auto[-3:-2] == "_" or auto[-3:].startswith("_")
          else "     generated %r" % auto)
    print("     (unique_name uses _NN when the base ends in one, and falls "
          "back to\n      _copy when it does not -- %r has no underscore "
          "before its digit)" % taken)

    print("\nSUMMARY")
    print("  [%s] an enemy is added by chunk copy, and its name is unique"
          % ("FAIL" if FAILURES else "PASS"))
    if FAILURES:
        print("  failed: %s" % ", ".join(FAILURES))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
