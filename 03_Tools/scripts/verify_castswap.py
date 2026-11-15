"""[M9] A character's model is swapped in ONE level and not in the others.

The tester: *"quick option to copy data from one [.rsc] to override another...
automatically change their .cr FileNameRender, to the new model.. I do this
since facial animations sync up if they are all changed using only the .cr"* —
and in his own bold, **"Important that it gives the option to do it per
level"**.

That emphasis is the milestone. A `.cr` is not one file: it is copied into
every archive that needs the character, and the archives are the levels.
**110 of 181 creatures live in more than one** -- and `srd.cr`, Red himself,
is in **57**. So "swap this character's model" without a level choice silently
rewrites the character in every level they appear in, which is the one outcome
he called out, and for the player that is fifty-seven levels from one button.

What is asserted here:

  1. PER-LEVEL IS A REAL DISTINCTION, not a UI nicety — measured on the disc,
     and the subject of this test appears in five levels with four of them
     able to take the swap.
  2. THE CHOSEN LEVEL CHANGED. Its `.cr` re-reads off the disc with
     `FileNameRender` AND `FileNameSkel` pointing at the new model. Both,
     because the disc never splits them (181 of 181).
  3. `FileNameAnim` DID NOT CHANGE. A swap repoints the model, not the
     animation set — if this ever moves, the bone-count argument that makes
     the swap safe stops holding.
  4. EVERY OTHER LEVEL IS BYTE-IDENTICAL. This is the half a caller takes on
     trust and the half the milestone is actually about, so it is compared
     byte for byte rather than by re-reading the field.
  5. THE ISO STILL VERIFIES. The edited archive re-opens from a fresh handle
     and every file in it still reads, at the same count and the same total
     size — an edit that fits its slot but corrupts the directory would pass
     every check above and fail this one.
  6. A CHOICE THAT MEANS NOTHING IS REFUSED: no levels, an unrelated level,
     or a target the level cannot resolve.

Sections 1 and 6 read `01_Vanilla` and write nothing. Sections 2-5 write the
working ISO and restore it in a `finally`, verifying the restoration.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdrstudio import Game, castswap                     # noqa: E402
from rdrstudio import modelswap as M                      # noqa: E402
from rdrstudio.config import MODDED_ISO, VANILLA_ISO      # noqa: E402

SUBJECT = "amg.cr"
TARGET = "gwk"

ok = True
OFFLINE = "--offline" in sys.argv


def check(what, got, want, cmp="=="):
    global ok
    good = (got == want if cmp == "==" else
            got >= want if cmp == ">=" else got <= want)
    ok &= bool(good)
    print("  %-58s %-12s %s"
          % (what, str(got)[:12],
             "ok" if good else "FAIL (want %s %s)" % (cmp, want)))


def main():
    van = Game(VANILLA_ISO)
    f = M.facts(van)

    # ---------------------------------------------------------------- 1
    print("1. a character's .cr lives in many levels at once:")
    spread = {}
    for cr in f.stock:
        spread[cr] = len(f.archives_for(cr))
    many = sum(1 for n in spread.values() if n > 1)
    print("    %d creatures; %d of them appear in more than one level, "
          "up to %d" % (len(spread), many, max(spread.values())))
    check("per-level is a real distinction, not a UI nicety",
          many >= 50, True, ">=")

    levels = castswap.levels_for(van, SUBJECT, TARGET)
    writable = [r["archive"] for r in levels if r["holds_target"]]
    print("    %s is in %d levels; %d can take %s:"
          % (SUBJECT, len(levels), len(writable), TARGET))
    for r in levels:
        print("      %-28s %s" % (r["archive"],
                                  "can take it" if r["holds_target"]
                                  else r["reason"]))
    check("the subject spans several levels", len(levels) >= 3, True, ">=")
    check("and more than one of them could take the swap",
          len(writable) >= 2, True, ">=")

    # ---------------------------------------------------------------- 6
    print("\n6. a choice that means nothing is refused:")
    for what, arcs in (("no level chosen", []),
                       ("a level the character is not in",
                        ["LVLS_M2/INT2.DAT"])):
        try:
            castswap.plan(van, SUBJECT, TARGET, arcs)
            got = "no refusal"
        except (castswap.SwapRefused, KeyError) as ex:
            got = "refused"
            print("      %s -> %s" % (what, str(ex)[:70]))
        check("  %s" % what, got, "refused")
    nowhere = [r["archive"] for r in levels if not r["holds_target"]]
    if nowhere:
        try:
            castswap.plan(van, SUBJECT, TARGET, nowhere[:1])
            got = "no refusal"
        except castswap.SwapRefused as ex:
            got = "refused"
            print("      a level that cannot resolve the target -> %s"
                  % str(ex)[:70])
        check("  a level that cannot resolve the target", got, "refused")

    if OFFLINE:
        print("\n2-5. SKIPPED (--offline): the disc write did not run.")
        print("\n%s" % ("PARTIAL -- sections 2-5 not run" if ok else "FAIL"))
        return 0 if ok else 1

    # ---------------------------------------------------------------- 2-5
    print("\n2. the swap is written to ONE level:")
    work = Game(MODDED_ISO, writable=True)
    wlevels = castswap.levels_for(work, SUBJECT, TARGET)
    wwritable = [r["archive"] for r in wlevels if r["holds_target"]]
    chosen = wwritable[0]
    others = [r["archive"] for r in wlevels if r["archive"] != chosen]
    print("    chosen: %s" % chosen)
    print("    left alone: %s" % ", ".join(a.split("/")[-1] for a in others))

    before_names = castswap.names_in(work, SUBJECT, chosen)
    before_other = {a: work.archive(a).read(SUBJECT) for a in others}
    inventory = _inventory(work, chosen)
    original = work.archive(chosen).read(SUBJECT)

    rows, skipped = castswap.plan(work, SUBJECT, TARGET, [chosen])
    check("exactly one level is planned", len(rows), 1)
    check("and it is the one chosen", rows[0]["archive"], chosen)

    restored = False
    try:
        castswap.apply(work, rows)

        fresh = Game(MODDED_ISO)
        after = castswap.names_in(fresh, SUBJECT, chosen)
        print("    %s in %s: FileNameRender %s -> %s"
              % (SUBJECT, chosen.split("/")[-1],
                 before_names.get("FileNameRender"),
                 after.get("FileNameRender")))
        check("the field was readable before the swap",
              bool(before_names.get("FileNameRender")), True)
        check("FileNameRender re-reads as the new model",
              after.get("FileNameRender"), TARGET)
        check("FileNameSkel followed it", after.get("FileNameSkel"), TARGET)
        check("and it actually changed",
              after.get("FileNameRender") != before_names.get(
                  "FileNameRender"), True)

        # ------------------------------------------------------------ 3
        print("\n3. the animation set was NOT repointed:")
        # Asserted as a real value, not just as equal: comparing two
        # missing fields is a check that passes because nothing was read.
        check("FileNameAnim was readable at all",
              bool(before_names.get("FileNameAnim")), True)
        check("FileNameAnim is unchanged",
              after.get("FileNameAnim"), before_names.get("FileNameAnim"))

        # ------------------------------------------------------------ 4
        print("\n4. every other level is byte-identical:")
        same = 0
        for a in others:
            if fresh.archive(a).read(SUBJECT) == before_other[a]:
                same += 1
            else:
                print("      CHANGED: %s" % a)
        print("    %d other level(s) compared" % len(others))
        check("none of them was touched", same, len(others))

        # ------------------------------------------------------------ 5
        print("\n5. the ISO still verifies:")
        now = _inventory(fresh, chosen)
        print("    %s: %d files, %d bytes before -> %d files, %d bytes after"
              % (chosen.split("/")[-1], inventory[0], inventory[1],
                 now[0], now[1]))
        check("the edited archive still lists every file", now[0],
              inventory[0])
        check("and every one of them still reads", now[2], 0)
        check("its total size is unchanged", now[1], inventory[1])
    finally:
        work.archive(chosen).patch(SUBJECT, original)
        restored = (Game(MODDED_ISO).archive(chosen).read(SUBJECT)
                    == original)
    print("\n   restore:")
    check("the working ISO is back to the bytes it started with",
          restored, True)

    print("\n%s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def _inventory(game, archive):
    """(file count, total bytes, unreadable) for one archive, read fresh.

    A swap that fits its slot but scrambled the directory would pass every
    field check and only show up here.
    """
    dave = game.archive(archive)
    count = total = bad = 0
    for e in game.toc(archive):
        count += 1
        try:
            total += len(dave.read(e.name))
        except Exception:
            bad += 1
    return count, total, bad


if __name__ == "__main__":
    sys.exit(main())
