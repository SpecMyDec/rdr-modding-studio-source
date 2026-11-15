"""Put a sixth row -- "Mod List" -- on Red Dead Revolver's story-book menu.

    py -3 03_Tools/frontmenu/build_modlist_row.py <disc.iso> ["Mod name" ...]

**The disc is required.** There is no default target: this tool writes
multi-GB discs and makes one from the vanilla master when the path does not
exist, so a no-argument run that picks its own target is how 2.3 GB gets
written to a disc nobody meant to touch. Omit the NAMES and it rebuilds with
the ones `02_Modded/modlist-names.txt` records for that disc; if there is no
record it refuses rather than substituting a default list, because the names
it would substitute are not the names on the disc we ship. **This tool also
refuses to write `RDR_MODDED.iso`**: that is the disc the developer play-tests on, and
`05_Docs/MENU-BRIEF.md` says to leave it alone. Pass another path if you mean
another disc; the refusal is deliberate and there is no flag to defeat it.

Because the target is this lane's own file and not the shared working ISO,
the build needs no suite lock -- no other lane can be inside it. Point this
at a shared disc and that stops being true; take the lock yourself if you do.

Writes the mod names onto BOTH Options pages as static <TEXT> rows -- the
build IS the manifest, there is nothing to parse at runtime -- and undoes the
older "screen of its own" route if a previous build left one on the disc.

**Both pages, because there are two.** `mainoptions.swui` is the front end's
(desk -> Options); `gameoptions.swui` is the in-game one (Start -> Options)
and lives in 62 archives. The developer looked at the in-game page, found nothing,
and reported the feature broken -- it was on the other page. A mod list you
have to know the route to is one nobody finds.

**Why not a screen of its own.** It was built that way first, and the boot test
on 2026-09-18 showed it renders perfectly and then handles no input at all:
the executable's screen-class factory at `0x00118b78` gives a name it does not
recognise the base page class, with no vtable and no behaviour. Twelve pad
buttons, 0.00 frame delta each, emulator live at 30fps. It soft-locks whoever
opens it. `rdrstudio/frontmenu.py` carries the addresses.

`mainoptions` is a name the executable DOES know, so it has a class, so the
pad works there and Cancel already leaves. Riding on it costs nothing and
inherits all of that.

Nothing is renamed and nothing moves. `verify_modlist_row.py` asserts both,
plus that the soft-lock is not on the disc, rather than trusting any of it.
"""

import os
import shutil
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "03_Tools"))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rdrstudio.game import Game                               # noqa: E402
from rdrstudio import frontmenu                               # noqa: E402
import manifest                                               # noqa: E402

VANILLA = os.path.join(ROOT, "01_Vanilla", "RDR_VANILLA.iso")
FORBIDDEN = ("rdr_modded.iso",)

# THERE IS NO DEFAULT MOD SET. Deleted 2026-09-19, not corrected.
#
# It held this lane's own three names and claimed to be "kept in step with the
# same list in verify_modlist_row.py" -- a contract that had already stopped
# existing when the gate started reading names from the disc, so the comment
# was instructing the next person to maintain a link that was not there.
#
# The list was also wrong by then. rdr-fmt built the shipped disc with their
# names ("60 Scattered Enemies"), and a no-names rebuild here would have put
# "Crowd Density 060 Scatter" back silently. Names now come from the command
# line or from `modlist-names.txt`, which records what the disc in front of
# you was actually built with. See CHECKS-THAT-LIE 21.


def main():
    if len(sys.argv) < 2:
        print("name the disc to build: build_modlist_row.py <disc.iso> "
              "[\"Mod name\" ...]")
        print("there is no default -- this writes 2.3 GB and will make the "
              "disc if it does not exist.")
        print("Nothing has been run.")
        return 2
    disc = sys.argv[1]
    base = os.path.basename(disc).lower()

    if base in FORBIDDEN:
        print("refusing to write %s -- that is the developer's play-test disc." % base)
        print("Nothing has been run.")
        return 2
    if "RDR-Play" in os.path.abspath(disc).replace("\\", "/"):
        print("refusing to write inside the play-test tree.")
        print("Nothing has been run.")
        return 2
    if not os.path.exists(VANILLA):
        print("no vanilla master at %s" % VANILLA)
        print("Nothing has been run.")
        return 2

    # The NAMES, with the same refusal as the target. Given on the command
    # line, or taken from the record of what this disc was last built with --
    # never from a default list. The default list used to be this lane's own
    # three names, which stopped matching the shipped disc the moment rdr-fmt
    # built the merged one with theirs; a no-names rebuild silently swapped
    # them, and the gate read the new names off the disc and called them
    # expected. CHECKS-THAT-LIE 21.
    mods = sys.argv[2:]
    if not mods:
        ok, record, msg = manifest.read(ROOT, disc)
        if not ok:
            print("no names given, and %s" % msg)
            print("Nothing has been run.")
            return 2
        mods = record[1]
        print("names : %d from %s (the last build's record)"
              % (len(mods), manifest.NAME))

    if not os.path.exists(disc):
        print("making %s from the vanilla master (2.3 GB, takes a few "
              "minutes)" % os.path.basename(disc))
        shutil.copyfile(VANILLA, disc)
        os.chmod(disc, 0o644)

    # Verify the TARGET, not the environment. A quiet machine guarantees
    # nothing; what this disc holds at write time does.
    g = Game(disc, writable=True)
    try:
        arc = g.archive(frontmenu.ARCHIVE)
        print("disc  : %s" % disc)

        block = frontmenu.inline_block(mods)
        print("block : %d bytes of <TEXT> for %d mods, onto %s"
              % (len(block), len(mods), frontmenu.OPTIONS_SCREEN))
        for m in mods:
            print("        %s" % frontmenu.clean(m))

        # install_disc, NOT install. `install` patches FRONT.DAT only, and
        # `mainoptions.swui` exists TWICE -- the SWAT.DAT copy was silently
        # left stock on every disc built with this script, which is how
        # RDR_CROWD_060_ALL shipped with a half-patched front end.
        applied, skipped = frontmenu.install_disc(g, mods)
        for arc_name, a, b in applied:
            print("  %-11s %-22s %d bytes into a %d-byte slot"
                  % ("front-end", arc_name, a, b))
        for arc_name, why in skipped:
            print("  %-11s %-22s %s" % ("SKIPPED", arc_name, why[:60]))

        # The in-game page, in every archive that holds one.
        applied, skipped = frontmenu.install_ingame(g, mods)
        print("  %-11s %-22s %d archive(s)"
              % ("in-game", frontmenu.INGAME_SCREEN, len(applied)))
        for arc_name, why in skipped:
            # Expected on BARFIGHT: a 384-byte slot against the ~473 needed.
            # Reported, never hidden -- that level keeps the stock page.
            print("  %-11s %-22s %s" % ("skipped", arc_name, why.split(".")[0]))
    finally:
        g.close()

    # Tell the gate which disc to check, so the [M23] row does not depend on a
    # filename somebody has to remember to chase. rdr-app flagged that hazard
    # when they registered the row, and the name had already churned once the
    # same day.
    pointer = os.path.join(ROOT, "02_Modded", "modlist-disc.txt")
    try:
        if os.path.dirname(os.path.abspath(disc)) == os.path.dirname(pointer):
            with open(pointer, "w") as fh:
                fh.write(os.path.basename(disc) + "\n")
            print("gate  : %s now points at this disc"
                  % os.path.basename(pointer))
        # And WHAT it should carry. Written after the disc, from the same list,
        # so the gate has an expectation the disc cannot supply.
        rec = manifest.write(ROOT, disc, mods)
        print("record: %s holds the %d name(s) this build wrote"
              % (os.path.basename(rec), len(mods)))
    except Exception as ex:                                   # noqa: BLE001
        print("  could not update %s: %s" % (pointer, ex))

    print("\nbuilt. now prove it:")
    print("  py -3 03_Tools/frontmenu/verify_modlist_row.py %s"
          % os.path.basename(disc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
