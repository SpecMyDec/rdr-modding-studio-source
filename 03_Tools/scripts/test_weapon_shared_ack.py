#!/usr/bin/env python3
"""A weapon model shared by many weapons can be imported over -- knowingly.

    python3 scripts/test_weapon_shared_ack.py

Why this exists: `modimport.plan` refused every shared model, and 9 of the 14
weapon models are shared (ptl_lod_01 by 11 weapons, rfl_lod_01 by 11, ...), so
the importer could only ever reach `bow_lod_01` -- Sacred_Bow. The cause is NOT
that those 9 are binary `version: 2.12` files: the plan never reads the file it
replaces, and the game's own dispatch (0x3B6A50) hands `version: 1.10` to the
text reader whatever extension-matched file it finds. The cause is the guard,
and the guard has a reason: overwriting `ptl_lod_01.mod` re-models eleven
weapons at once. `allow_shared=True` is the user saying they know.

Library-only: opens 01_Vanilla read-only, writes nothing, no Studio. Proves the
plan logic. It does NOT prove the game loads a text 1.10 `.mod` where a binary
2.12 one used to be -- no one has booted that.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from rdrstudio import config, modimport            # noqa: E402
from rdrstudio.game import Game                    # noqa: E402

FAILURES = []


def check(ok, what, detail=""):
    print(("  ok   " if ok else "  FAIL ") + what
          + (("  -- " + str(detail)) if detail and not ok else ""))
    if not ok:
        FAILURES.append(what)


# A single triangle: the smallest thing that is a model.
OBJ = "v 0 0 0\nv 0.1 0 0\nv 0 0.1 0\nf 1 2 3\n"


def main():
    g = Game(config.VANILLA_ISO, writable=False)
    try:
        plan, prob, data = modimport.plan(g, OBJ, "ptl_lod_01")
        check(prob and prob["code"] == "not_editable",
              "shared model refused by default", prob and prob["code"])
        check(len(plan["shared_by"]) == 11 and not plan["shared_acknowledged"],
              "refusal names all %d weapons" % len(plan["shared_by"]))

        plan, prob, data = modimport.plan(g, OBJ, "ptl_lod_01",
                                          allow_shared=True)
        check(prob is None, "allowed once acknowledged", prob)
        check(plan["shared_acknowledged"] and len(plan["shared_by"]) == 11,
              "plan records who changes: %s" % ", ".join(plan["shared_by"][:3]))
        check(plan["fits"] and plan["writes"][0]["stored"]
              <= plan["writes"][0]["slot"], "and it still checks the slot",
              plan["writes"][0])

        seen = set()
        for stem in ("ptl_lod_01", "rfl_lod_01", "shotgun01", "pt2_lod_01",
                     "bay_lod_01", "gat_lod_01", "bow_lod_01"):
            plan, prob, data = modimport.plan(g, OBJ, stem, allow_shared=True)
            seen.add(stem if prob is None else None)
        seen.discard(None)
        check(len(seen) >= 6, "%d of 7 sampled models plannable" % len(seen), seen)

        # Loader evidence (STATIC -- nobody has booted it): the game reads the
        # first 14 bytes, and strncmp()s "version: 2.1" (0x3B6A50, UNKNOWNS.md
        # 2255-2270). 2.1x and 2.00 pick the binary reader; 1.10 and anything
        # else fall to the TEXT reader. So the header, not the file, decides.
        d = g.archive(modimport.WEAPON_ARCHIVE)
        orig = d.read(d.find("bay_lod_01.mod"))
        plan, prob, data = modimport.plan(g, OBJ, "bay_lod_01", allow_shared=True)
        check(orig.startswith(b"version: 2.1"),
              "bay_lod_01 is a BINARY 2.1x file on the vanilla disc", orig[:14])
        check(data.startswith(b"version: 1.10") and
              not data.startswith(b"version: 2.1") and
              not data.startswith(b"version: 2.00"),
              "the replacement carries a header that routes to the text reader",
              data[:14])
        check(data[:14].split(b"\n")[0] == b"version: 1.10" and
              data.count(b"\0") == 0,
              "and is plain text (no NULs)")

        plan, prob, data = modimport.plan(g, OBJ, "bow_lod_01")
        check(prob is None and not plan["shared_acknowledged"],
              "an unshared model is unaffected")

        # a refusal that is about the SLOT is still a refusal
        big = "".join("v %d 0 0\nv %d 1 0\nv %d 0 1\nf %d %d %d\n"
                      % (i, i, i, 3 * i + 1, 3 * i + 2, 3 * i + 3)
                      for i in range(800))
        plan, prob, data = modimport.plan(g, big, "ptl_lod_01",
                                          allow_shared=True)
        check(prob and prob["code"] == "slot_overflow",
              "allow_shared does not skip the slot check",
              prob and prob["code"])
    finally:
        g.close()
    print("\n%s" % ("FAIL: " + "; ".join(FAILURES) if FAILURES else "PASS"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
