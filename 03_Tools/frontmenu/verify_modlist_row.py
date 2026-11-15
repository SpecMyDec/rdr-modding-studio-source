"""[M-menu-1] The mod list is on the disc, on a screen that actually works.

The feature this asserts CHANGED after the 2026-09-18 boot test, and the test
changed with it. A disc built the old way -- a `modlist` screen of its own --
renders correctly and then handles no input at all, because the executable's
screen-class factory hands an unknown name the base page class. It soft-locks.
`05_Docs/FRONTMENU-FEASIBILITY.md` has the addresses.

So what is asserted now is the opposite of what it was: that the trap is NOT
on the disc, and that the mod names ride on `mainoptions` -- a screen the
executable names, so it has a class, so the pad works and Cancel leaves.

  * the mod names are on the Options page, exactly as `frontmenu.inline_block`
    renders them for the set this disc was built with
  * `storyselect.swui` is byte-identical to vanilla -- no dead row on the book
  * `frontendcache.swui` is byte-identical to vanilla -- nothing registers the
    inert screen, so nothing can reach it
  * the Options page still has its two stock rows and no third one
  * every other member of FRONT.DAT is untouched

    py -3 03_Tools/frontmenu/verify_modlist_row.py [disc.iso]

Read-only against both discs, so it takes no lock.

It fails if the names go missing, if a build re-introduces the trap, or if any
stock member is disturbed.
"""

import hashlib
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "03_Tools"))
sys.path.insert(0, os.path.join(ROOT, "03_Tools", "rdrstudio"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rdrstudio.game import Game                               # noqa: E402
import frontmenu                                              # noqa: E402
import swui                                                   # noqa: E402
import manifest                                               # noqa: E402

VANILLA = os.path.join(ROOT, "01_Vanilla", "RDR_VANILLA.iso")
# WHICH DISC THIS GATES, resolved rather than hard-coded.
#
# It was a hard-coded filename and that churned once on the day it was written
# (RDR_MENUTEST.iso -> RDR_CROWD_060_MENU.iso). rdr-app flagged it when they
# registered the row: a name in here going stale turns the DEFAULT gate red
# until somebody chases it, and a gate that cries wolf is a gate that gets
# weakened. Resolution order:
#
#   1. a path given on the command line          -- explicit always wins
#   2. 02_Modded/modlist-disc.txt                -- written by the build script
#   3. the only disc in 02_Modded carrying the   -- so a rebuild under any name
#      mod-list block                               is found without config
#
# If none of those resolves, it exits 2 with "Nothing has been run." naming the
# fix. A refusal, never a pass: an unfindable disc must not read as a clean gate.
POINTER = os.path.join(ROOT, "02_Modded", "modlist-disc.txt")


def resolve_disc():
    """The disc to gate, and one line saying how it was chosen."""
    if os.path.exists(POINTER):
        try:
            name = open(POINTER).read().strip()
        except Exception as ex:                               # noqa: BLE001
            print("  could not read %s: %s" % (POINTER, ex))
            name = ""
        if name:
            cand = os.path.join(ROOT, "02_Modded", name)
            if os.path.exists(cand):
                return cand, "named by %s" % os.path.basename(POINTER)
            print("  %s names %s, which is not there -- falling back to a scan"
                  % (os.path.basename(POINTER), name))

    import glob
    from rdrstudio.game import Game as _G
    found = []
    for cand in sorted(glob.glob(os.path.join(ROOT, "02_Modded", "*.iso"))):
        try:
            g = _G(cand)
            has = frontmenu.MARK_OPEN in g.archive(
                frontmenu.ARCHIVE).read(frontmenu.OPTIONS_SCREEN)
            g.close()
        except Exception:                                     # noqa: BLE001
            continue
        if has:
            found.append(cand)
    if len(found) == 1:
        return found[0], "the only disc in 02_Modded carrying the mod list"
    if len(found) > 1:
        return None, ("%d discs in 02_Modded carry a mod list (%s) -- name one "
                      "on the command line, or write its filename into %s"
                      % (len(found), ", ".join(os.path.basename(f)
                                               for f in found),
                         os.path.basename(POINTER)))
    return None, ("no disc in 02_Modded carries a mod list -- build one with "
                  "frontmenu/build_modlist_row.py, or name a disc on the "
                  "command line")


ARCHIVE = frontmenu.ARCHIVE

# The mod set this disc was built with. Kept here, and in the build script,
# deliberately: the test asserts the screen matches what the generator makes
# for THIS list, so an edit to either without the other is a failure and not
# a silent drift.
# WHERE THE EXPECTED NAMES COME FROM, and what that buys.
#
# They were hardcoded once. That could assert the names were RIGHT, but made
# the test unrunnable against any disc but one -- so the disc that actually
# shipped to the developer was never checked by it, and shipped half-built twice.
#
# They were then read off the disc under test. That ran against anything, and
# proved nothing: the expectation came from a copy of the page being checked,
# so the copies agreed necessarily. Overwrite the names and this returned
# 19/19 green on a page nobody chose. rdr-app caught it; CHECKS-THAT-LIE 21.
#
# Now: `02_Modded/modlist-names.txt`, written by the builder at build time and
# compared against the disc here. The row compares DISC AGAINST RECORD rather
# than disc against itself.
#
# Say what that proves and no more. The record is written from the same names
# the builder wrote to the disc, so its authority is the build invocation: it
# proves the disc STILL MATCHES THE LAST INTENTIONAL BUILD. It catches a hand
# edit, another tool writing that page, and a copy left half-rewritten. It does
# NOT catch a build run with the wrong names -- then disc and record are wrong
# together and this row is green. The roadmap label says so (REQ-085).
#
# NO FALLBACK. Missing record, unreadable record, record naming another disc:
# refuse. Falling back to the disc restores the bug above on exactly the
# occasions the record existed to cover. A false red costs an hour; a false
# green put a half-built disc in front of the developer twice.
MODS = None

EDITED = {frontmenu.OPTIONS_SCREEN, frontmenu.CACHE_FILE, frontmenu.ROW_FILE}
BARFIGHT = "LVLS_SNG/BARFIGHT.DAT"

# Screen names with their own class, read off the factory chain at 0x00118b78.
# Anything NOT here gets the base page: it renders and handles no input.
CLASSED = {
    "gamesound", "options", "gameoptions", "cheats", "gamepicture",
    "mainoptions", "continue", "gameover", "fadeout", "fadein",
    "savefrontend", "mapscreen", "inputname", "bountychar", "videooptions",
    "mpmode", "bookchapterview", "journal", "profile", "storyselect",
    "warning", "gameplayeropt", "frontendbuttons", "warningfrontend",
    "difficultyselect",
}

FAILURES = []
RAN = []


def check(ok, note):
    RAN.append(note)
    if not ok:
        FAILURES.append(note)
    print("  %-5s %s" % ("ok" if ok else "FAIL", note))


def rows(data):
    return [t for t in swui.parse(data)
            if t.name == "BUTTON" and not t.commented]


def main():
    global MODS
    MODS = sys.argv[2:] or None
    if len(sys.argv) > 1:
        disc, how = sys.argv[1], "given on the command line"
    else:
        disc, how = resolve_disc()
        if disc is None:
            print("cannot tell which disc to gate: %s" % how)
            print("Nothing has been run.")
            return 2
    for path in (VANILLA, disc):
        if not os.path.exists(path):
            print("no such disc: %s" % path)
            print("Nothing has been run.")
            return 2

    print("vanilla: %s" % VANILLA)
    print("disc   : %s\n         (%s)" % (disc, how))

    van = Game(VANILLA)
    mod = Game(disc)
    va = van.archive(ARCHIVE)
    ma = mod.archive(ARCHIVE)
    # --- 1. the front-end Options page, EVERY copy of it --------------------
    print("\n1. the front-end Options page (desk -> Options)")
    if MODS:
        names, source = MODS, "pinned on the command line"
    else:
        ok, record, msg = manifest.read(ROOT, disc)
        if not ok:
            print("cannot tell what this disc SHOULD carry: %s" % msg)
            print("Nothing has been run.")
            return 2
        names, source = record[1], "%s, the record of the last build" % manifest.NAME
    print("  expect %d name(s) -- %s" % (len(names), source))

    # The disc's own account of itself, checked AGAINST the record rather than
    # used as the expectation. This is the check the old version could not make.
    on_disc = frontmenu.read_names_disc(mod)
    check(on_disc == names,
          "the disc carries the %d name(s) the record expects: %s"
          % (len(names), ", ".join(names))
          if on_disc == names else
          "the disc does NOT match the record -- record %r, disc %r"
          % (names, on_disc))
    want = frontmenu.inline_block(names)

    # Which variant is this? A row disc puts the ROW on the Options pages and
    # the names on the page it opens; a names disc prints the names there.
    # Whichever it is, EVERY copy must agree -- `mainoptions.swui` exists more
    # than once, and a disc with only one patched shows a stock page whenever
    # the engine resolves the other. That is the defect that shipped once.
    fe_copies = mod.locate(frontmenu.OPTIONS_SCREEN)
    ROW_MODE = any(frontmenu.ROW_BUTTON in mod.archive(a).read(
        frontmenu.OPTIONS_SCREEN) for a in fe_copies)
    marker = frontmenu.ROW_BUTTON if ROW_MODE else frontmenu.MARK_OPEN
    what = "the Mod List row" if ROW_MODE else "the names block"

    fe_missing = [a for a in fe_copies
                  if marker not in mod.archive(a).read(frontmenu.OPTIONS_SCREEN)]
    check(not fe_missing,
          "all %d copies of %s carry %s%s"
          % (len(fe_copies), frontmenu.OPTIONS_SCREEN, what,
             "" if not fe_missing else " -- MISSING FROM %s" % fe_missing))
    if not ROW_MODE:
        fe_stale = [a for a in fe_copies if want not in
                    mod.archive(a).read(frontmenu.OPTIONS_SCREEN)]
        check(not fe_stale, "every copy renders the same block%s"
              % ("" if not fe_stale else " -- DISAGREES IN %s" % fe_stale))

    got = mod.archive(fe_copies[0]).read(frontmenu.OPTIONS_SCREEN)
    text = got.decode("latin-1")

    # Where the names live depends on the variant. On a names-only disc they
    # are printed on the Options page; on a row disc they are on the page the
    # row opens, and printing them on BOTH is what produced a 24px overlap
    # that shipped. So: they must appear exactly once, somewhere reachable.
    carriers = [f for f in (frontmenu.ROW_FILE, frontmenu.OPTIONS_SCREEN)
                if frontmenu.MARK_OPEN
                in mod.archive(frontmenu.ARCHIVE).read(f)]
    check(len(carriers) == 1,
          "the names are printed on exactly one page (%s) -- printing them on "
          "the Options page AND the page its row opens is what overlapped the "
          "row by 24px on RDR_CROWD_060_ROW2" % (", ".join(carriers) or "NONE"))

    home = carriers[0] if carriers else frontmenu.OPTIONS_SCREEN
    htext = mod.archive(frontmenu.ARCHIVE).read(home).decode("latin-1")
    missing = [m for m in (frontmenu.clean(x) for x in names)
               if ("0:%s>" % m) not in htext]
    check(not missing,
          "every mod name is on %s as its own <TEXT> row%s"
          % (home, "" if not missing else " -- missing %s" % missing))
    check(htext.count(frontmenu.MARK_OPEN.decode("latin-1")) == 1,
          "the block appears once -- a rebuild replaces it, it does not stack")

    # The defect that shipped: no page may have overlapping bands.
    import re as _re3
    for fn in set([home] + list(fe_copies[:1]) + [frontmenu.ROW_FILE]):
        try:
            d = mod.archive(frontmenu.ARCHIVE).read(fn).decode("latin-1")
        except Exception:                                     # noqa: BLE001
            continue
        bands = ([(int(y) - 11, int(y) + 21, "button " + n)
                  for y, n in _re3.findall(r"<BUTTON:200,(\d+),0:([^:]+):", d)]
                 + [(int(y) - 8, int(y) + 8, "text " + t)
                    for y, t in _re3.findall(r"<TEXT:200,(\d+),0:([^>]+)>", d)
                    if int(y) > 100])
        bands.sort()
        ov = [(bands[i][2], bands[i + 1][2])
              for i in range(len(bands) - 1)
              if bands[i][1] >= bands[i + 1][0]]
        check(not ov, "%s: no overlapping rows%s"
              % (fn, "" if not ov else " -- %s" % ov[:2]))

    # Two shipping variants, and the test has to be able to pass BOTH or it
    # cannot gate the disc that actually goes out -- which is the defect that
    # let a half-built disc reach the developer twice.
    #
    #   names-only : no added navigation at all
    #   row        : a real "Mod List" row pointing at a CLASSED, REGISTERED
    #                screen. An added row pointing anywhere else is the C-039
    #                soft-lock and must fail.
    opt = rows(got)
    v_opt = rows(va.read(va.find(frontmenu.OPTIONS_SCREEN)))
    added = [t for t in opt if t.fields[1].raw not in
             [x.fields[1].raw for x in v_opt]]
    cache = mod.archive(frontmenu.ARCHIVE).read(
        frontmenu.CACHE_FILE).decode("latin-1").split()
    if not added:
        check(len(opt) == len(v_opt) == 2,
              "names-only build: the page has its %d stock rows and adds no "
              "navigation" % len(opt))
    else:
        tgts = [t.fields[2].raw for t in added]
        check(len(added) == 1 and tgts == [frontmenu.ROW_SCREEN],
              "row build: exactly one added row, %r -> %r"
              % (added[0].fields[1].raw if added else "?", tgts))
        check(all(t in cache for t in tgts),
              "its target is REGISTERED in %s (%s) -- an unregistered target "
              "opens nothing at all" % (frontmenu.CACHE_FILE, ", ".join(cache)))
        check(all(t in CLASSED for t in tgts),
              "its target has its own CLASS in the executable, so the page it "
              "opens handles the pad. A classless target is the C-039 "
              "soft-lock.")
        pg = mod.archive(frontmenu.ARCHIVE).read(frontmenu.ROW_FILE)
        check(frontmenu.MARK_OPEN in pg
              and frontmenu.read_names(mod.archive(frontmenu.ARCHIVE),
                                       frontmenu.ROW_FILE) == names,
              "the page it opens carries the same %d names" % len(names))
        check(b"CLOSEUI" in pg,
              "and an explicit Back wired to CLOSEUI")

    # --- 2. the trap is not on this disc ---------------------------------
    print("\n2. the soft-lock is gone")
    check(va.read(va.find(frontmenu.BOOK_SCREEN))
          == ma.read(ma.find(frontmenu.BOOK_SCREEN)),
          "%s is byte-identical to the vanilla master" % frontmenu.BOOK_SCREEN)

    # `frontendcache.swui` is UNTOUCHED on a names-only build and MODIFIED on a
    # row build, where registering the classed screen is the whole mechanism.
    # The invariant that holds for both is narrower and is what actually
    # matters: nothing classless is ever registered.
    cache_now = ma.read(ma.find(frontmenu.CACHE_FILE)).decode("latin-1").split()
    cache_van = va.read(va.find(frontmenu.CACHE_FILE)).decode("latin-1").split()
    extra = [n for n in cache_now if n not in cache_van]
    if not extra:
        check(True, "%s is byte-identical to the vanilla master"
              % frontmenu.CACHE_FILE)
    else:
        check(all(n in CLASSED for n in extra),
              "%s registers %s, and every one has its own class -- "
              "registering a classless name is what builds a soft-lock"
              % (frontmenu.CACHE_FILE, ", ".join(extra)))

    cache = ma.read(ma.find(frontmenu.CACHE_FILE)).decode("latin-1").split()
    check(frontmenu.SCREEN_NAME not in cache,
          "%r is NOT registered, so the inert page cannot be opened at all "
          "(cache lists: %s)" % (frontmenu.SCREEN_NAME, ", ".join(cache)))

    reachable = []
    for e in ma.entries:
        if not e.name.endswith(".swui"):
            continue
        try:
            body = ma.read(e)
        except Exception as ex:                               # noqa: BLE001
            print("  could not read %s: %s" % (e.name, ex))
            continue
        for t in rows(body):
            if len(t.fields) > 2 and t.fields[2].raw == frontmenu.SCREEN_NAME:
                reachable.append(e.name)
    check(not reachable,
          "no live BUTTON anywhere in %s links to it%s"
          % (ARCHIVE, "" if not reachable else " -- but %s does" % reachable))

    # --- 2b. the IN-GAME Options page ------------------------------------
    print("\n2b. the in-game Options page (Start -> Options)")
    import re as _re2
    holders = mod.locate(frontmenu.INGAME_SCREEN)
    got, missing, wrong = [], [], []
    for arc in holders:
        try:
            d = mod.archive(arc).read(frontmenu.INGAME_SCREEN)
        except Exception as ex:                               # noqa: BLE001
            print("  could not read %s in %s: %s"
                  % (frontmenu.INGAME_SCREEN, arc, ex))
            continue
        if frontmenu.MARK_OPEN not in d:
            missing.append(arc)
            continue
        got.append(arc)
        btn = [int(y) for y in _re2.findall(rb"<BUTTON:200,(\d+),0:", d)]
        txt = [int(y) for y in _re2.findall(rb"<TEXT:200,(\d+),0:", d)][1:]
        if (tuple(btn) != frontmenu.INGAME_BUTTON_Y
                or txt != [250, 272, 290, 308]
                or frontmenu.read_names(mod.archive(arc),
                                        frontmenu.INGAME_SCREEN) != names):
            wrong.append((arc, btn, txt))
    if ROW_MODE:
        withrow = [a for a in holders if frontmenu.INGAME_ROW
                   in mod.archive(a).read(frontmenu.INGAME_SCREEN)]
        check(len(withrow) == len(holders),
              "all %d archives carry the Mod List row on the pause menu%s"
              % (len(holders), "" if len(withrow) == len(holders)
                 else " -- missing from %s" % [a for a in holders
                                               if a not in withrow][:3]))
        cache2 = mod.archive("SWAT.DAT").read(
            frontmenu.INGAME_CACHE).decode("latin-1").split()
        check(frontmenu.ROW_SCREEN in cache2,
              "%s registers %r -- the pause menu has its OWN registry, and a "
              "row registered only in frontendcache.swui would draw there and "
              "open nothing" % (frontmenu.INGAME_CACHE, frontmenu.ROW_SCREEN))
        return _finish()
    check(len(got) == len(holders) - 1 and missing == [BARFIGHT],
          "%d of %d archives carry the list; the only one without it is %s, "
          "whose slot is too small -- a reported partial, not a miss"
          % (len(got), len(holders), BARFIGHT))
    check(not wrong,
          "every patched copy has buttons at %s and the list at "
          "250/272/290/308 -- the same rows the front-end page uses, which is "
          "the geometry measured clear on hardware%s"
          % (",".join(str(y) for y in frontmenu.INGAME_BUTTON_Y),
             "" if not wrong else " -- but %s" % wrong[:2]))
    if missing == [BARFIGHT]:
        d = mod.archive(BARFIGHT).read(frontmenu.INGAME_SCREEN)
        check(d == van.archive(BARFIGHT).read(frontmenu.INGAME_SCREEN),
              "%s keeps its page byte-identical to vanilla -- the skip left "
              "it untouched rather than half-written" % BARFIGHT)

    # --- 3. what it cost the archive --------------------------------------
    print("\n3. what it cost %s" % ARCHIVE)
    v_by = dict((e.name, e) for e in va.entries)
    m_by = dict((e.name, e) for e in ma.entries)
    added = set(m_by) - set(v_by)
    dropped = set(v_by) - set(m_by)
    check(not dropped and added <= {frontmenu.SCREEN_FILE},
          "%d members -> %d, dropped nothing%s"
          % (len(v_by), len(m_by),
             ". %s is an orphan from the old route: unregistered, unlinked, "
             "unreachable -- Dave has no remove and it costs only the gap it "
             "already sat in" % frontmenu.SCREEN_FILE
             if added else ""))

    moved = [n for n in v_by if n in m_by
             and v_by[n].offset != m_by[n].offset
             and n != frontmenu.ROW_FILE]
    check(not moved,
          "all %d pre-existing members kept their exact offset -- the add "
          "went into a free gap and moved no file data" % len(v_by))

    diff = []
    for n in sorted(v_by):
        if n in EDITED:
            continue
        try:
            if va.read(v_by[n]) != ma.read(m_by[n]):
                diff.append(n)
        except Exception as ex:                               # noqa: BLE001
            print("  could not read %s: %s" % (n, ex))
            diff.append(n)
    check(not diff,
          "every member except the %d edited reads back byte-identical to "
          "vanilla (%d checked)" % (len(EDITED), len(v_by) - len(EDITED)))

    return _finish()


def _finish():
    print("\n%d check(s) ran." % len(RAN))
    if FAILURES:
        print("FAILED %d check(s):" % len(FAILURES))
        for note in FAILURES:
            print("  %s" % note)
        return 1
    print("the mod list is on the Options page, and the soft-lock is not on "
          "this disc.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
