r"""[M10] Rebind an enemy's `.stateb` and prove it on the bytes.

M10 gives `.tbl` / `.stateb` their own tab, and its accept criterion is the
one thing a tab cannot fake: *rebind an enemy's `.stateb` through the tab's
own API and assert the on-disc bytes and a re-read both agree.*

THE CHAIN, and every link is checked here rather than assumed:

    an enemy in a `.enm`   ->  its `Table` field names a stem
    <stem>.tbl             ->  binds behaviour slots to state machines
    <slot>: <name>         ->  `name` is a `.stateb` in the SAME archive

So "rebinding an enemy's `.stateb`" means changing one slot's name token in
the `.tbl` that the enemy names. The enemy file itself is not touched, and
this test asserts that too -- an edit that rewrote the `.enm` would also look
like a successful rebind from the outside.

WHAT IT GOES THROUGH. `behaviour.state_palette`, `behaviour.resolve_state` and
`behaviour.set_states` -- the same three the tab's routes call
(`get_behaviour_table`, `post_behaviour_table_set`). Not a byte poke: a slot
this API cannot reach has to fail here rather than pass on a write nobody
could have made through the app. The HTTP layer above them is rdr-app's and
is covered by their live row.

WHY A NAME AND NOT A NUMBER. A `.tbl` binding is a FILENAME the engine looks
up, so the legal values are exactly the `.stateb` stems in that archive. That
is why the API resolves against a palette and refuses anything else, and why
two of the checks below are refusals: a binding that names no file is not a
misbehaving enemy, it is an enemy that does nothing.

THE LENGTH TRAP. These are text files in compressed archive slots, and a state
name is not a fixed-width field: rebinding `Barfight_Shop_start` to a longer
stem makes the file longer. Worse, a SAME-length rebind can still change the
STORED size, because the entry is deflated and different text compresses
differently -- that is C-030, found the same way. So the stored size is
reported on every case here rather than assumed to be stable, and the slot is
checked against the value the archive actually wrote.

    python3 03_Tools/scripts/verify_stateb_rebind.py [iso]

Read-only: opens the pristine disc, extracts ONE archive to a temp directory,
does every write on that copy and deletes it. No ISO is written, so this needs
no suite lock.
"""

import os
import re
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

# A level archive on purpose: every one of the 570 `.tbl` in the 35 level
# archives resolves completely, so a miss here is this code and never the
# disc. (The 281 shipped bindings that name no file are all in SWAT.DAT.)
ARCHIVE = "LVLS_SNG/BARFIGHT.DAT"

_TABLE_FIELD = re.compile(rb"(?m)^[ \t]*Table[ \t]+(\S+)")

FAILED = []


def check(label, ok, detail=""):
    print("     %-56s %s%s"
          % (label, "ok" if ok else "FAIL", ("  " + detail) if detail else ""))
    if not ok:
        FAILED.append(label)
    return ok


def find_enemy_table(dave):
    """(enemy file, table filename) for the first enemy naming a real table."""
    for e in sorted(dave, key=lambda x: x.name):
        if e.ext != "enm":
            continue
        try:
            blob = dave.read(e)
        except Exception:
            continue
        for m in _TABLE_FIELD.finditer(blob):
            stem = m.group(1).decode("latin-1")
            want = (stem + ".tbl").lower()
            hit = next((x for x in dave if x.name.lower() == want), None)
            if hit is not None:
                return e.name, hit.name
    return None, None


def main():
    iso = sys.argv[1] if len(sys.argv) > 1 else config.VANILLA_ISO
    print("disc: %s  (read-only)" % iso)
    print("archive: %s, extracted to a temp copy -- no ISO is written\n"
          % ARCHIVE)

    game = Game(iso, writable=False)
    blob = game.iso.read_file(ARCHIVE)
    game.close()

    tmp = tempfile.mkdtemp(prefix="rdr-stateb-")
    try:
        path = os.path.join(tmp, os.path.basename(ARCHIVE))
        with open(path, "wb") as fh:
            fh.write(blob)

        # ------------------------------------------------- 1. the chain
        print("1. the enemy -> .tbl -> .stateb chain")
        dave = Dave(FileBacking(path, writable=False), label=ARCHIVE)
        enemy, table = find_enemy_table(dave)
        check("an enemy names a .tbl that exists in this archive",
              enemy is not None and table is not None,
              "%s -> %s" % (enemy, table))
        if enemy is None:
            dave.backing.close()
            return 1

        data = dave.read(table)
        enemy_before = dave.read(enemy)
        doc = B.table_doc(dave, table, ARCHIVE)
        palette = doc["palette"]
        slots = doc["states"]
        check("the table binds at least one slot", bool(slots),
              "%d slot(s)" % len(slots))
        check("every binding resolves to a .stateb here",
              all(r["resolves"] for r in slots),
              "%d of %d" % (sum(1 for r in slots if r["resolves"]), len(slots)))
        check("the palette is the archive's own .stateb stems",
              len(palette) > 1, "%d stems" % len(palette))

        # `table_doc` rows are the UI's view and carry no byte spans; the
        # spans live on `states()` rows. Take the row from there so the
        # untouched-bytes assertion below has a token to bound.
        spanned = {r["path"]: r for r in B.states(data)}
        target = dict(slots[0], **spanned[slots[0]["path"]])
        was = target["value"]
        # Same length keeps the FILE length identical, which is what lets the
        # untouched-bytes assertion below be exact rather than approximate.
        new = next((p for p in palette
                    if len(p) == len(was) and p.lower() != was.lower()), None)
        check("a different .stateb of the same name length exists",
              new is not None, "%s -> %s" % (was, new))
        if new is None:
            dave.backing.close()
            return 1
        entry = dave.find(table)
        stored_before, slot = entry.size_comp, entry.slot
        raw_before = dave.raw(entry)
        dave.backing.close()

        # ------------------------------------------------- 2. the rebind
        print("\n2. rebind %s: %s -> %s" % (target["path"], was, new))
        dave = Dave(FileBacking(path, writable=True), label=ARCHIVE)
        resolved = B.resolve_state(palette, new)
        check("the API resolves the new name to the palette's spelling",
              resolved == new, repr(resolved))
        out = B.set_states(data, {target["path"]: resolved})
        check("the rewrite keeps the file length (same-length name)",
              len(out) == len(data), "%d -> %d" % (len(data), len(out)))
        dave.patch(table, out)
        dave.backing.close()

        # Re-open from the bytes on disk. Nothing carries over in memory.
        again = Dave(FileBacking(path, writable=False), label=ARCHIVE)
        entry2 = again.find(table)
        raw_after = again.raw(entry2)
        read_back = again.read(entry2)
        enemy_after = again.read(enemy)

        print("\n3. the on-disc bytes, and the re-read")
        check("the archive's STORED bytes for the entry changed",
              raw_after != raw_before,
              "stored %d -> %d of a %d-byte slot"
              % (stored_before, entry2.size_comp, slot))
        check("the entry still fits its slot", entry2.size_comp <= slot,
              "%d <= %d" % (entry2.size_comp, slot))
        check("the file read back is what was written", read_back == out)

        after = {r["path"]: r["value"] for r in B.states(read_back)}
        check("a re-read returns the NEW state name",
              after.get(target["path"]) == new,
              "%s = %r" % (target["path"], after.get(target["path"])))

        before_map = {r["path"]: r["value"] for r in slots}
        moved = [p for p in before_map
                 if before_map[p] != after.get(p)]
        check("exactly one binding changed", moved == [target["path"]],
              "changed: %s" % moved)

        doc2 = B.table_doc(again, table, ARCHIVE)
        row = next(r for r in doc2["states"] if r["path"] == target["path"])
        check("the new binding resolves to a real .stateb", row["resolves"],
              row["file"])

        # Only the name token may move. Everything else -- key spelling,
        # indentation, a trailing `//comment`, every other line -- is bytes.
        spans = [i for i in range(len(data)) if data[i] != read_back[i]]
        inside = all(target["start"] <= i < target["end"] for i in spans) \
            if spans else False
        check("every changed byte is inside the name token's span",
              inside, "%d byte(s) changed, token is [%d,%d)"
              % (len(spans), target["start"], target["end"]))

        check("the ENEMY file was not touched", enemy_after == enemy_before,
              "%s is byte-identical" % enemy)
        again.backing.close()

        # ------------------------------------------------- 4. refusals
        print("\n4. the API refuses what the engine could not look up")
        check("a stem that is in no .stateb here does not resolve",
              B.resolve_state(palette, "no_such_state_zzz") is None)

        bad = None
        try:
            B.set_states(data, {target["path"]: "has a space"})
        except B.BadState as ex:
            bad = ex.code
        check("an illegal name is refused, not written", bad == "unknown_value",
              repr(bad))

        missing = None
        try:
            B.set_states(data, {"NoSuchState": "whatever"})
        except B.BadState as ex:
            missing = ex.code
        check("a slot this table does not bind is refused",
              missing == "not_found", repr(missing))

        # ------------------------------------------------- 5. controls
        print("\n5. controls -- the assertions can fail")
        same = B.set_states(data, {target["path"]: was})
        check("re-saving the SAME name is a zero-byte edit", same == data)

        # The check that matters is "a re-read returns the new name". Prove it
        # can fail: a writer that leaves the file alone must not satisfy it.
        untouched = {r["path"]: r["value"] for r in B.states(data)}
        check("...and that check FAILS for a write that did nothing",
              untouched.get(target["path"]) != new,
              "still %r" % untouched.get(target["path"]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 68)
    print("SUMMARY")
    print("=" * 68)
    if FAILED:
        for f in FAILED:
            print("  [FAIL] %s" % f)
        print("\n%d check(s) failed." % len(FAILED))
        return 1
    print("  An enemy's behaviour table was rebound to a different .stateb")
    print("  through the same API the tab's routes use. The archive's stored")
    print("  bytes changed, the entry still fits its slot, a re-read returns")
    print("  the new name, it resolves to a real file, every other binding and")
    print("  every byte outside the name token is identical, and the .enm")
    print("  itself was never written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
