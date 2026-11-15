r"""Renaming a `.tbl` or a `.stateb`: the archive invariant, and two reference graphs.

M10 asks for browse, swap, rename, edit and add on `.tbl` / `.stateb`. Five of
those existed. **Rename did not**, for either kind, and it is the only one of
the six that is not a content edit: it changes a FILENAME, which is both an
archive-structure edit and a referential one.

THE TWO VERBS DO NOT SHARE A POLICY, and that is measured rather than assumed:

    rename a .tbl      1,258 of 3,275 are named by an enemy, and some are also
                       named INLINE in a .stateb token stream, which cannot be
                       rewritten safely -> those are REFUSED, and the 2,017
                       orphans are what stays renameable.
    rename a .stateb   1,315 of 1,315 on a level are named by a .tbl binding
                       and NONE is named in a token stream -> refusing over
                       referrers would refuse every one, so the bindings are
                       REWRITTEN instead. 91.7% of same-length renames go
                       through; the other 8.3% are blocked by slot pressure in
                       the tables that name them.

THREE HAZARDS, all measured on the pristine disc, all asserted here.

1. THE TOC IS BINARY-SEARCHED, so it must stay sorted. `SLUS_205.00` resolves
   a file in an archive by bsearch over the 16-byte TOC records with a
   case-insensitive compare (`0x002CB908` -> `0x0025EEB8`, comparator
   `0x002CA9D8`; three callers, only one of them audio, so it is the generic
   lookup). All 66 archives are sorted under it -- 66 of 66. An entry renamed
   in place would leave the array unsorted, and bsearch on an unsorted array
   does not merely lose the moved entry, it can miss arbitrary others. So
   `Dave.rename` MOVES the record to the new name's sorted position.

2. NAMES SHARE PREFIXES WITH THE ENTRY BEFORE THEM. A stored name may open
   with "copy the first L characters of the previous name", and **2,928 of the
   3,275 `.tbl` entries (89.4%) are followed by a name that does**. Moving a
   record changes somebody's predecessor at both ends, so any neighbour that
   was deduping is rewritten as a literal first.

3. A `.tbl` IS REFERRED TO BY NAME. Two classes, and they are not equally
   tractable: `.enm` `Table` fields are text and safe to rewrite (1,258 of
   3,275 tables have at least one; `actor` in BARFIGHT.DAT is named by 2 files
   and **20 enemies**), while `.stateb` / `.actb` name tables inline in a
   token stream (rdr-re's opcode 1003, C-036). The second kind is refused
   rather than half-rewritten.

There is no `remove()` in this format, so the old name's bytes stay as dead
space and renames are finite: `can_rename` reports the name block's spare.
And a rename cannot be undone by the journal, which rewrites file CONTENT into
a slot and cannot put a name back -- the same property clone has.

    python3 03_Tools/scripts/verify_table_rename.py [iso]

Read-only: the pristine disc is opened read-only and archives are extracted to
a temp directory. No ISO is written, so this needs no suite lock.
"""

import os
import shutil
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _HERE]
sys.path.insert(0, os.path.dirname(_HERE))

from rdrstudio import behaviour as B                            # noqa: E402
from rdrstudio import config                                    # noqa: E402
from rdrstudio.dave import Dave, FileBacking                    # noqa: E402
from rdrstudio.game import Game                                 # noqa: E402

FREE = "LVLS_SNG/BARFIGHT.DAT"      # has an orphan table and .enm referrers
LOCKED = "LVLS_SNG/FORT.DAT"        # `actor` is named inline by 3 .stateb

FAILED = []


def check(label, ok, detail=""):
    print("     %-58s %s%s"
          % (label, "ok" if ok else "FAIL", ("  " + detail) if detail else ""))
    if not ok:
        FAILED.append(label)
    return ok


def sorted_toc(dave):
    keys = [dave._sort_key(e.name) for e in dave.entries]
    return all(keys[i] <= keys[i + 1] for i in range(len(keys) - 1))


def copy_of(game, archive, tmp):
    path = os.path.join(tmp, archive.replace("/", "_"))
    with open(path, "wb") as fh:
        fh.write(game.iso.read_file(archive))
    return path


def main():
    iso = sys.argv[1] if len(sys.argv) > 1 else config.VANILLA_ISO
    print("disc: %s  (read-only)\n" % iso)
    game = Game(iso, writable=False)
    tmp = tempfile.mkdtemp(prefix="rdr-rename-")
    try:
        # ------------------------------------------------ 1. an orphan rename
        print("1. rename a table nothing refers to")
        path = copy_of(game, FREE, tmp)
        dave = Dave(FileBacking(path, writable=True), label=FREE)
        before = [e.name for e in dave.entries]
        check("the stock TOC is sorted under the engine's collation",
              sorted_toc(dave), "%d entries" % len(before))

        orphan = None
        for e in [x for x in dave if x.ext == "tbl"]:
            refs = B.table_referrers(dave, e.name[:-4])
            if not refs["enm"] and not refs["locked"]:
                orphan = e.name
                break
        check("an unreferenced .tbl exists to rename", orphan is not None,
              str(orphan))
        if orphan is None:
            return 1

        entry = dave.find(orphan)
        payload, offset = dave.read(entry), entry.offset
        plan = B.check_rename_table(dave, orphan, "zz_renamed_table")
        check("the plan allows it", plan["ok"], str(plan["reasons"])[:60])
        check("the plan says it cannot be undone", plan["undoable"] is False)
        check("the plan reports the name block's spare",
              plan["spare_name_bytes"] > 0,
              "%d bytes" % plan["spare_name_bytes"])

        res = dave.rename(orphan, "zz_renamed_table.tbl")
        dave.backing.close()

        again = Dave(FileBacking(path, writable=False), label=FREE)
        after = [e.name for e in again.entries]
        check("the entry count is unchanged", len(after) == len(before),
              "%d -> %d" % (len(before), len(after)))
        check("the old name is gone", orphan not in after)
        check("the new name is present", "zz_renamed_table.tbl" in after)
        moved = again.find("zz_renamed_table.tbl")
        check("the file's payload is byte-identical",
              again.read(moved) == payload, "%d bytes" % len(payload))
        check("not one byte of file data moved", moved.offset == offset,
              "offset 0x%X" % offset)
        check("every OTHER name is unchanged",
              sorted(n for n in before if n != orphan)
              == sorted(n for n in after if n != "zz_renamed_table.tbl"))
        check("the TOC is STILL sorted (the game bsearches it)",
              sorted_toc(again),
              "record moved %d -> %d, %d literal name(s) rewritten"
              % (res["index_from"], res["index_to"], res["literals"]))
        again.backing.close()

        # ------------------------------------------- 2. the reference graph
        print("\n2. a table enemies point at")
        dave = Dave(FileBacking(copy_of(game, FREE, tmp), writable=False),
                    label=FREE)
        refs = B.table_referrers(dave, "actor")
        check("the .enm referrers are found", len(refs["enm"]) >= 2,
              "%d file(s), %d enem(ies)" % (len(refs["enm"]), refs["enemies"]))
        check("files and enemies are reported separately",
              refs["enemies"] > len(refs["enm"]),
              "%d files carry %d enemies" % (len(refs["enm"]), refs["enemies"]))

        enm = refs["enm"][0]
        data = dave.read(enm)
        out = B.set_table_field(data, "actor", "zz_actor")
        check("repointing an enemy's Table changes the bytes", out != data)
        rr = B.table_referrers.__self__ if False else None      # noqa: F841
        import re as _re
        hits = _re.findall(rb"(?m)^[ \t]*Table[ \t]+(\S+)", out)
        check("every `Table actor` in that file now reads zz_actor",
              all(h.decode("latin-1").lower() != "actor" for h in hits),
              "%d Table line(s)" % len(hits))
        check("the file grew by exactly the name's extra characters",
              len(out) - len(data) == 3 * sum(
                  1 for h in _re.findall(rb"(?m)^[ \t]*Table[ \t]+(\S+)", data)
                  if h.decode("latin-1").lower() == "actor"),
              "%d -> %d bytes" % (len(data), len(out)))
        dave.backing.close()

        # ------------------------------------------------ 3. the refusals
        print("\n3. what it refuses, and it has to refuse these")
        dave = Dave(FileBacking(copy_of(game, LOCKED, tmp), writable=False),
                    label=LOCKED)
        locked = B.check_rename_table(dave, "actor", "zz_actor")
        check("a table named inline by a .stateb is REFUSED",
              not locked["ok"] and locked["locked_by"],
              "%d state machine(s): %s"
              % (len(locked["locked_by"]), locked["locked_by"][:2]))
        check("the refusal says why, in words", bool(locked["reasons"]),
              locked["reasons"][0][:70] if locked["reasons"] else "")

        # Each refusal must be refused for ITS OWN reason. Picking any table
        # here would have passed both checks below on the `locked` reason
        # above -- a test that goes green for the wrong cause, which is the
        # same trap the [M7] span bound fell into. So: a table with no
        # referrers at all, and the reason text is asserted, not just `ok`.
        free = None
        for e in [x for x in dave if x.ext == "tbl"]:
            r = B.table_referrers(dave, e.name[:-4])
            if not r["enm"] and not r["locked"]:
                free = e.name
                break
        check("an unreferenced table exists here to test refusals with",
              free is not None, str(free))

        other = next(e.name[:-4] for e in dave
                     if e.ext == "tbl" and e.name != free)
        clash = B.check_rename_table(dave, free, other)
        why = " ".join(clash["reasons"]).lower()
        check("renaming onto a name already here is refused",
              not clash["ok"] and "already in this archive" in why,
              clash["reasons"][0][:60] if clash["reasons"] else "")

        # NOT an alphabet question -- the 6-bit alphabet has a space at index
        # 1 and `Dave.can_rename` accepts `has a space.tbl` correctly, because
        # the format really can spell it. What cannot survive is the
        # REFERENCE: `Table <name>` is read to the first whitespace. This
        # assertion was written the wrong way round first and passed for the
        # wrong reason, which is why each refusal below names its own cause.
        bad = B.check_rename_table(dave, free, "has a space")
        why = " ".join(bad["reasons"]).lower()
        check("a name an enemy's `Table` field could not carry is refused",
              not bad["ok"] and "first space" in why,
              bad["reasons"][0][:70] if bad["reasons"] else "")
        check("...and the archive itself would have taken it",
              dave.can_rename(free, "has a space.tbl")["ok"],
              "the space is index 1 of the 49-character alphabet")
        dave.backing.close()

        # -------------------------------------- 4. renaming a .stateb
        #
        # The mirror verb, and its policy is the OPPOSITE of the table's, on
        # measurement rather than symmetry: every level `.stateb` is named by
        # at least one `.tbl` binding (1,315 of 1,315) and NONE is named
        # inside a token stream, so refusing over referrers would refuse the
        # lot. They are rewritten instead -- `.tbl` bindings are plain text
        # with a known span.
        print("\n4. rename a .stateb, rewriting the tables that name it")
        path = copy_of(game, FREE, tmp)
        dave = Dave(FileBacking(path, writable=True), label=FREE)

        target = newname = None
        for e in [x for x in dave if x.ext == "stateb"]:
            stem = e.name[:-7]
            cand = "zz" + stem[2:]
            plan = B.check_rename_state(dave, stem, cand)
            if plan["ok"] and plan["rewrites_needed"] >= 1:
                target, newname = stem, cand
                break
        check("a renameable .stateb with referrers exists", target is not None,
              "%s -> %s" % (target, newname))
        if target is None:
            return 1

        plan = B.check_rename_state(dave, target, newname)
        check("the plan names the tables it must rewrite",
              plan["rewrites_needed"] >= 1,
              "%d table(s), %d binding(s)"
              % (plan["rewrites_needed"], plan["bindings"]))
        check("every rewrite is costed against its SLOT, not its length",
              all("stored" in w and "slot" in w for w in plan["rewrites"])
              and all(w["fits"] for w in plan["rewrites"]))
        check("a state rename cannot be undone either",
              plan["undoable"] is False)

        payload = dave.read(target + ".stateb")
        for row in plan["rewrites"]:
            data = dave.read(row["file"])
            dave.patch(row["file"], B.repoint_states(data, target, newname))
        dave.rename(target + ".stateb", newname + ".stateb")
        dave.backing.close()

        again = Dave(FileBacking(path, writable=False), label=FREE)
        check("the state file carries its new name",
              newname + ".stateb" in [e.name for e in again.entries])
        check("its bytes are untouched",
              again.read(newname + ".stateb") == payload,
              "%d bytes" % len(payload))
        check("the TOC is still sorted after a .stateb rename",
              sorted_toc(again))
        left = []
        for row in plan["rewrites"]:
            vals = [st["value"].lower() for st in B.states(again.read(row["file"]))]
            if target in vals:
                left.append(row["file"])
        check("no table still names the OLD state", not left, str(left[:3]))
        pointed = sum(1 for row in plan["rewrites"]
                      for st in B.states(again.read(row["file"]))
                      if st["value"] == newname)
        check("every binding now names the new state",
              pointed == plan["bindings"],
              "%d of %d" % (pointed, plan["bindings"]))
        doc = B.table_doc(again, plan["rewrites"][0]["file"], FREE)
        check("the rebound tables still resolve against the palette",
              all(r["resolves"] for r in doc["states"]),
              "%s" % doc["rewrites"] if False else doc["file"])
        again.backing.close()

        print("\n5. a state rename the disc will not take")
        dave = Dave(FileBacking(copy_of(game, FREE, tmp), writable=False),
                    label=FREE)
        stuck = None
        for e in [x for x in dave if x.ext == "stateb"]:
            stem = e.name[:-7]
            p2 = B.check_rename_state(dave, stem, "zz" + stem[2:])
            if not p2["ok"] and p2["blocked_by_slot"]:
                stuck = (stem, p2)
                break
        # 8.3% of level .stateb are in this position -- a SAME-LENGTH rename
        # that still overflows, because the referring table is deflated and
        # the new text compresses worse. C-030, and the reason the plan costs
        # stored bytes rather than file length.
        check("a same-length rename blocked by a referrer's slot is refused",
              stuck is not None,
              "%s: %s" % (stuck[0], stuck[1]["blocked_by_slot"][:2])
              if stuck else "none found in this archive")
        if stuck:
            w = next(x for x in stuck[1]["rewrites"] if not x["fits"])
            check("...and the file LENGTH did not change, only the stored size",
                  w["bytes_after"] == w["bytes_before"],
                  "%d bytes, stored %d of slot %d"
                  % (w["bytes_after"], w["stored"], w["slot"]))
        dave.backing.close()

        # ------------------------------------------------ 6. the control
        print("\n6. control -- the sortedness check can fail")
        dave = Dave(FileBacking(copy_of(game, FREE, tmp), writable=False),
                    label=FREE)
        names = [e.name for e in dave.entries]
        swapped = names[:]
        swapped[0], swapped[-1] = swapped[-1], swapped[0]
        keys = [dave._sort_key(n) for n in swapped]
        check("a TOC with two names swapped reads as unsorted",
              any(keys[i] > keys[i + 1] for i in range(len(keys) - 1)))
        dave.backing.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        game.close()

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    if FAILED:
        for f in FAILED:
            print("  [FAIL] %s" % f)
        print("\n%d check(s) failed." % len(FAILED))
        return 1
    print("  A .tbl and a .stateb were each renamed with no file data moved,")
    print("  the TOC left sorted for the engine's bsearch, and every other")
    print("  name byte-identical. A table named inside a .stateb token stream")
    print("  is refused; a state's .tbl bindings are rewritten instead, and a")
    print("  rename whose referrer has no slot headroom is refused BEFORE the")
    print("  write -- costed on stored bytes, which a same-length rename can")
    print("  still change.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
