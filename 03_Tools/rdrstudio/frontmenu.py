"""The Mod List screen: a front-end page built from data, at build time.

Owned by lane rdr-menu. Plain functions, no UI, no routes -- rdr-app wires
those. See `05_Docs/FRONTMENU-FEASIBILITY.md` for how the front end is put
together and which parts of it are executable code.

THE FINDING THIS MODULE RESTS ON. A screen is openable only if its name is in
the front end's registered-page list: `0x0013bba8` walks that list comparing
names and returns NULL for anything else, and `0x0013bb70` then quietly does
nothing. But the list is NOT only built from the executable. Two call sites
register pages, and they land on the same function, `0x001186d8`:

    front-end init  0x001389c8   512x256   14 names compiled in
    cache loader    0x00119400   640x448   ONE LINE PER LINE of a text file

The second reads `frontendcache.swui` -- a newline-separated list of screen
names, in the archive, as data. It is not theory: `gamepicture` and
`gamesound` are reachable Options screens on the shipped disc, they are in
that file, and they are in NEITHER of the executable's 14 registrations.

So a brand-new screen costs zero executable bytes. It needs three data edits,
which is what `install` does:

    1. a new `modlist.swui` member in FRONT.DAT      (Dave.add)
    2. `modlist` appended to `frontendcache.swui`    (Dave.patch)
    3. a row on the story book that links to it      (Dave.patch)

WHAT THE BOOT SETTLED, 2026-09-18. Registration is NOT interactivity, and that
distinction cost a disc. `frontendcache.swui` really does register and load a
new screen -- it rendered its three entries correctly on real hardware. But
there is a **screen-class factory** at `0x00118b78`..`0x001191f0`: a strcmp
chain on the screen NAME, each arm allocating a different C++ object with its
own vtable (`gamesound` -> new(0x47c), `gamepicture` -> new(0x4a8), ...). The
default arm at `0x001191dc` allocates `new(0x44c)` -- the BASE size -- and
stores no vtable.

**So a screen the executable has never heard of gets the plain base page: it
draws, and it handles nothing.** rdr-play pressed all twelve pad buttons on it
and measured 0.00 frame delta on every one, with the emulator live at 30fps.
A new screen is not a screen, it is a soft-lock.

Interactivity needs a class, a class needs an executable patch, and
`rdrstudio/elfpack.py` is unpack-only -- no packer exists for the aPLib +
XOR-obfuscated `SLUS_205.00`. So this is not expensive, it is unavailable.

`install()` therefore writes the mod names onto a screen that ALREADY HAS a
class -- `mainoptions`, the Options page -- where they inherit working input
handling for free. `install_screen()` keeps the old route because the finding
is worth reproducing, and refuses unless called with
`i_know_this_soft_locks=True`.

THE MANIFEST IS THE SCREEN. There is no runtime manifest parser and writing
one would mean patching the executable. `<TEXT>` is static in the file and
`<READOUT>` is filled in by code, so the only zero-code way to show a name the
game has never heard of is to have the BUILD write it into the page. That is
what `screen()` is: the mod list rendered to `.swui` source. The disc is
rebuilt per mod set anyway, so there is nothing to parse at runtime.

Budget, measured on the USA disc: FRONT.DAT's largest free run is 2,035 bytes
and that is what a new member's STORED (deflated) payload must fit in. This
text deflates around 3:1, and `fits()` answers exactly rather than guessing.
"""

import re

# KEEP THE IMPORTS AT `re` AND NOTHING ELSE. `studio.py` does
# `from . import frontmenu` at module scope, so the blast radius of this file
# is "the Studio starts", not "a cosmetic list works". The try/except around
# the call site cannot help an import. (rdr-app, on review of REQ-083.)

SCREEN_NAME = "modlist"
SCREEN_FILE = "modlist.swui"
CACHE_FILE = "frontendcache.swui"
ARCHIVE = "FRONT.DAT"
BOOK_SCREEN = "storyselect.swui"
OPTIONS_SCREEN = "mainoptions.swui"

# The story-book row that opens it. Anchored on the BOUNTY HUNTER row so the
# insert lands under the stock five at their own 20px pitch.
BOOK_ANCHOR = (b"<BUTTON:40,190,0:story_bhunter:CALLB:0,0:640,448:0:-1:BHUNTER>"
               b"<BASECOLOR:0.296,0,0,1>\r\n")
BOOK_ROW = (b"<BUTTON:40,210,0:Mod List:" + SCREEN_NAME.encode() +
            b":0,0:640,448:1:-1:0><BASECOLOR:0.296,0,0,1>\r\n")

# The SECOND door, and it is a diagnostic as much as a feature.
#
# The story-book row is where the developer asked for the entry, but `storyselect` is
# a book page with its own class, and whether that class honours a link-type
# BUTTON (as opposed to its five CALLB rows) is the one thing the executable
# would not tell me. `mainoptions` is a full-screen page that demonstrably
# does -- its own two rows are links, and they work on the shipped disc.
#
# Putting the same destination behind both means ONE boot separates "links do
# not work from a book page" from "the screen is not registered at all",
# instead of leaving the two confounded.
OPTIONS_ANCHOR = (b"<BUTTON:200,213,0:Sound Options:gamesound:0,0:640,448:1:"
                  b"-1:0>\r\n")
OPTIONS_ROW = (b"<BUTTON:200,266,0:Mod List:" + SCREEN_NAME.encode() +
               b":0,0:640,448:1:-1:0>\r\n")

# Layout, copied from `mainoptions.swui` because that is a full-screen
# front-end page that demonstrably works: same texture phase, same background,
# same title position, same instruction row. Deviating from a page that ships
# is how you find out the hard way which of these tags are load-bearing.
# ---------------------------------------------------------------------------
# THE DEAD ROUTE'S LAYOUT. These belong to `screen()` -- the standalone
# `modlist.swui` page, which SOFT-LOCKS and is never shipped. They are NOT the
# live budget. The page that ships is the block on `mainoptions`, and its
# constants are INLINE_* further down, capped at THREE names.
#
# Why the two differ, since the numbers look contradictory: a full-screen page
# of its own has 130..280 to itself and fits six rows. The live block is a
# guest on the Options page and has to clear SOUND OPTIONS above it and the
# Select/Back prompts below, which rdr-play's row-band scan measured at three.
#
# Kept only so C-039 can be reproduced. Do not tune them.
DEAD_TITLE_Y = 71
DEAD_FIRST_Y = 130
DEAD_PITCH_Y = 30
DEAD_BACK_Y = 300
DEAD_MAX_ROWS = 6
# ---------------------------------------------------------------------------

# `<TAG:field:field>` is the grammar (see swui.py). A name carrying any of
# these would not be a name any more, it would be syntax.
_UNSAFE = re.compile(r"[<>:;\r\n\t]")
NAME_LIMIT = 28


def clean(name):
    """Make one mod name safe to sit inside a tag field, and short enough.

    Not cosmetic. `<` `>` `:` end fields and tags, `;` starts a comment to end
    of line, and a newline would split the tag across two lines -- any of them
    turns the rest of the screen into something else. Truncation is by glyph
    budget: the list font is drawn centred at SCALE 1.195 and much past 28
    characters runs off the paper.
    """
    text = _UNSAFE.sub(" ", str(name)).strip()
    text = re.sub(r"\s+", " ", text)
    if not text:
        text = "(unnamed)"
    if len(text) > NAME_LIMIT:
        # "..." and not an ellipsis glyph: the label goes through the
        # game's own font, and `regulatorcondensed` is a 2004 bitmap
        # font with no reason to carry U+2026.
        text = text[:NAME_LIMIT - 3].rstrip() + "..."
    return text


def screen(names, title="Mod List", empty="No mods on this disc"):
    """Render the Mod List page to `.swui` source bytes.

    `names` is whatever the build knows about the mods on this disc. Anything
    past DEAD_MAX_ROWS is summarised on the last line rather than dropped,
    a list that silently ends is a list that lies.
    """
    rows = [clean(n) for n in names]
    overflow = 0
    if len(rows) > DEAD_MAX_ROWS:
        overflow = len(rows) - (DEAD_MAX_ROWS - 1)
        rows = rows[:DEAD_MAX_ROWS - 1] + ["and %d more" % overflow]
    if not rows:
        rows = [clean(empty)]

    out: list[str] = []
    add = out.append
    add("<TEXPHASE:FRONTEND1>")
    add("<IPU_BACKGROUND:0,0,0:0,0:options_bg_10k>")
    add("")
    add("; title")
    add("<FONT:regulatorcondensed:SCALE:2,2:CENTER>")
    add("<COLOR:1,1,1,1><COLORC:1,1,1,1>")
    add("<TEXT:200,%d,0:%s><BASECOLOR:0.289,0.041,0,1>" % (DEAD_TITLE_Y,
                                                           clean(title)))
    add("")
    add("; the mods on this disc -- written here by the build, not read at")
    add("; runtime. see 03_Tools/rdrstudio/frontmenu.py")
    add("<FONT:regulatorcondensed:SCALE:1.195,1.195:CENTER>")
    add("<COLOR:0.702,0.702,0.702,1><COLORC:0.771,0.771,0.771,0.500>")
    for i, row in enumerate(rows):
        add("<TEXT:200,%d,0:%s>" % (DEAD_FIRST_Y + i * DEAD_PITCH_Y, row))
    add("")
    add("; a way out that does not depend on the page class handling Back")
    add("<BUTTON:200,%d,0:Back:CLOSEUI:0,0:640,448:1:-1:0>" % DEAD_BACK_Y)
    add("")
    add("; instructions")
    add("<FONT:berrylium>")
    add("<TEXT:196,343,0:BUTTON_BACK>")
    add("<FONT:berrylium>")
    add("<TEXT:232,343,0:Back>")
    add("")
    return "\r\n".join(out).encode("latin-1", "replace")


def cache_with(data, name=SCREEN_NAME):
    """Append a screen name to a `*cache.swui` list, once.

    The loader reads this file with '\\n' as the delimiter (`0x002603e0`,
    255-byte lines), and the shipped file ends WITHOUT a trailing newline, so
    the separator goes in front.
    """
    want = name.encode("latin-1")
    lines = [ln.strip() for ln in data.replace(b"\r\n", b"\n").split(b"\n")]
    if want in [ln for ln in lines if ln]:
        return data
    return data.rstrip(b"\r\n") + b"\r\n" + want


def fits(archive, payload_len):
    """Would a new member of this stored size fit? Ask, don't assume."""
    return archive.can_add(SCREEN_FILE, payload_len)


def plan(archive, names):
    """What `install` would do, without doing it. API-ready, no side effects."""
    body = screen(names)
    import zlib
    z = zlib.compressobj(9, zlib.DEFLATED, -15)
    stored = z.compress(body) + z.flush()
    check = archive.can_add(SCREEN_FILE, len(stored))
    cache = archive.find(CACHE_FILE)
    book = archive.find(BOOK_SCREEN)
    return {
        "screen_bytes": len(body),
        "stored_bytes": len(stored),
        "can_add": check["ok"],
        "reasons": check["reasons"],
        "payload_room": check["payload_room"],
        "cache_headroom": cache.headroom,
        "book_headroom": book.headroom,
        "rows": len(names),
    }


# Any row this module has ever written, whatever it pointed at. Used to
# supersede an earlier build's row instead of stacking a second one beside it
# -- two rows both reading "Mod List" is not an upgrade, it is a bug you can
# see from the sofa.
_OURS = re.compile(rb"^<BUTTON:[0-9,.]+:Mod List:[^\r\n]*\r\n", re.M)


def _add_row(archive, filename, anchor, row, label, steps):
    """Put exactly one Mod List row into a screen. Replaces any earlier one."""
    entry = archive.find(filename)
    cur = archive.read(entry)
    want = _OURS.sub(b"", cur)
    if cur == want and row in cur:
        steps.append((label, filename, 0, 0))
        return
    if want.count(anchor) != 1:
        raise ValueError(
            "%s is not the file this row was written against: the anchor "
            "matched %d times, expected 1" % (filename, want.count(anchor)))
    new = want.replace(anchor, anchor + row)
    if new == cur:
        steps.append((label, filename, 0, 0))
        return
    wrote, slot = archive.patch(entry, new)
    steps.append((label, filename, wrote, slot))


# The inline block, written onto `mainoptions` between two marker comments so
# a rebuild replaces it rather than stacking another copy underneath.
MARK_OPEN = b"; rdr-menu: mods on this disc -- written by the build\r\n"
# The authoritative list, as a comment the game's parser skips (`;` runs to end
# of line -- see swui.py). THE DISC IS ITS OWN REGISTRY.
#
# rdr-app looked for a "what mods are on this disc" structure and there is none:
# /api/mods is the shelf not the disc, /api/diff is file-level and unnamed, and
# the session journal is reset by a restart. Rather than invent one, this reads
# back what the page already says. The page IS the record -- it is the whole
# point of the feature -- so accumulating a second mod onto a disc means reading
# this line, appending, and rewriting.
#
# It is separate from the visible rows because those are LOSSY: past three names
# the display collapses to "and N more", and a list that cannot round-trip its
# own contents would silently drop a mod on the next write.
MARK_NAMES = b"; rdr-menu: names="
MARK_END = b"; rdr-menu: end\r\n"
_BLOCK = re.compile(re.escape(MARK_OPEN) + b".*?" + re.escape(MARK_END),
                    re.S)
_NAMES = re.compile(re.escape(MARK_NAMES) + b"([^\r\n]*)")

# `|` separates the record, `<` `>` would end a tag, and a newline would end the
# comment and turn the rest into markup. 64 is generous next to the 28 the
# visible row allows -- the record keeps the real name even when the row cannot.
_RECORD_UNSAFE = re.compile(r"[|<>\r\n\t]")
RECORD_LIMIT = 64

# LAYOUT: 250 / 272 / 290 / 308, three names. Do not "improve" this without a
# boot -- it was improved once and the improvement was a regression.
#
# Every number here is bracketed by something rdr-play SAW on a television,
# and the brackets are worth more than the values. Row-band scans, same disc
# path, same capture:
#
#                    gap to SOUND OPTIONS above   names resolvable   gap to prompts
#   head 250, +18px            20px                    yes                29px
#   head 240, +16px             4px  (touching)        NO  (one band)     51px
#
# So:
#   * heading at 250 is SAFE, heading at 240 is NOT -- descenders of SOUND
#     OPTIONS meet the cap-height of the heading. The real floor is somewhere
#     in 240..250 and nobody has looked for it.
#   * 18px between rows is resolvable; 16px is not -- at 16 the first two mod
#     names scanned as a SINGLE 37px band. The real limit is in 16..18.
#   * y=308 as the last row leaves 29px to the prompts. That is observed-safe,
#     NOT the collision point: no build has ever put a row below it, so the
#     true limit is under 308 and unknown.
#
# THE CONSEQUENCE, which is the actual finding: there is no room for a fourth
# row between SOUND OPTIONS and the prompts at this font size. It does not come
# from moving things around -- 250 with 18px spacing puts a fourth name at 326,
# and tightening to fit overruns the top instead. **A fourth mod is the trigger
# to re-lay the page, not to squeeze it.** Hence INLINE_MAX = 3 and an overflow
# row, rather than a cap that quietly drops names.
INLINE_HEAD_Y = 250
INLINE_FIRST_Y = 272
INLINE_PITCH = 18
INLINE_MAX = 3          # 272, 290, 308 -- all three seen good on a TV


# The IN-GAME Options page (`gameoptions.swui`) is a different file from the
# front end's `mainoptions.swui`, and it is the one a player already in a game
# reaches: Start -> Options. The developer looked there and found nothing, which is
# how this page came to be patched at all.
#
# ITS LAYOUT IS THE FRONT-END LAYOUT, DELIBERATELY. The list sits at exactly
# the rows the front-end page uses -- heading 250, names 272/290/308 -- so
# every clearance already measured on a television transfers unchanged instead
# of being re-derived. Only the stock buttons move, from a 57px pitch to 40px,
# far enough up that the page below them is the page that already works.
#
# The first attempt did the opposite: it kept the buttons near stock and pushed
# the list down to 255/273/291/309. rdr-play measured the result per column --
# 96 columns of contact between "Sound Options" and the heading, tightest gap
# 0 px. Ink-band profiles of both captures, in these units:
#
#     front end   button 202..234   heading 242..258   gap 8   CLEAR
#     in game     button 222..254   heading 247..263   gap -8  TOUCHING
#
# A button at y occupies roughly y-11..y+21; a list row at y occupies y-8..y+8.
# Those two ranges are what the numbers below are chosen against.
INGAME_SCREEN = "gameoptions.swui"
# Matched by ROLE, not by position. The first version replaced one exact
# stock block, so re-laying a disc that had already been patched silently did
# nothing -- the buttons stayed where the previous attempt had put them while
# the list moved, which is worse than either layout on its own. Rewriting the
# y of whichever rows are there makes the edit idempotent across re-lays.
INGAME_BUTTON_ROWS = ("Player Options", "Picture Options", "Sound Options")
INGAME_BUTTON_Y = (133, 173, 213)
_INGAME_BTN = re.compile(
    rb"<BUTTON:200,\d+(?:\.\d+)?,0:(Player Options|Picture Options|"
    rb"Sound Options):")


def _move_ingame_buttons(data):
    """Put the three stock rows on INGAME_BUTTON_Y, wherever they are now."""
    want = dict(zip(INGAME_BUTTON_ROWS, INGAME_BUTTON_Y))

    def sub(m):
        label = m.group(1).decode("latin-1")
        return b"<BUTTON:200,%d,0:%s:" % (want[label], m.group(1))
    out, n = _INGAME_BTN.subn(sub, data)
    if n != 3:
        raise ValueError("expected 3 stock Options rows, matched %d" % n)
    return out
# The one number here with no precedent: the title's ink ends at 115 and the
# first button now starts at 122, a 7px gap. Gaps of 5 and 8 are both measured
# clear elsewhere on these pages, so 7 sits between two known-good values --
# but it has not itself been seen, and it is the thing to check on a boot.
INGAME_TITLE_GAP = 7


def inline_block(names, heading="Mods on this disc",
                 head_y=None, first_y=None):
    """The mod list as plain <TEXT> rows, for a screen that already works.

    Colours are byte-for-byte the ones `mainoptions.swui` already has in
    force at this point in the file, so the block leaves no state behind for
    the instruction row that follows it.
    """
    rows = [clean(n) for n in names]
    if len(rows) > INLINE_MAX:
        rows = rows[:INLINE_MAX - 1] + ["and %d more" % (
            len(rows) - (INLINE_MAX - 1))]
    if not rows:
        rows = ["(none)"]
    record = "|".join(record_name(n) for n in names)
    hy = INLINE_HEAD_Y if head_y is None else head_y
    fy = INLINE_FIRST_Y if first_y is None else first_y
    out = [MARK_OPEN.decode("latin-1").rstrip("\r\n"),
           MARK_NAMES.decode("latin-1") + record,
           "<FONT:berrylium:SCALE:0.750,0.750:CENTER>",
           "<COLOR:0.702,0.702,0.702,1><COLORC:0.771,0.771,0.771,0.500>",
           "<TEXT:200,%d,0:%s>" % (hy, clean(heading))]
    for i, row in enumerate(rows):
        out.append("<TEXT:200,%d,0:%s>" % (fy + i * INLINE_PITCH, row))
    out.append(MARK_END.decode("latin-1").rstrip("\r\n"))
    return ("\r\n".join(out) + "\r\n").encode("latin-1", "replace")


def record_name(name):
    """Sanitise a name for the machine-readable record line."""
    text = _RECORD_UNSAFE.sub(" ", str(name)).strip()
    text = re.sub(r"\s+", " ", text) or "(unnamed)"
    return text[:RECORD_LIMIT].strip()


def read_names(archive, screen=None):
    """CAUTION: this reads ONE page. Prefer `read_names_disc(game)`.

    On a row build the names are not on the Options page at all -- they are on
    the page the row opens -- so this returns `[]` for a disc that plainly
    carries them. rdr-fmt hit exactly that on RDR_CROWD_060_ROW3 and was right
    to ask rather than report it: an empty list here means "not on the page you
    asked about", never "this disc has no mod list".
    """

    """What this disc already says is on it. [] if the page was never written.

    Reads the record line, not the visible rows -- see MARK_NAMES. This is the
    answer to "what mods are on this disc" for everything this lane needs, and
    it is answerable because the feature itself wrote it down.
    """
    screen = OPTIONS_SCREEN if screen is None else screen
    try:
        cur = archive.read(archive.find(screen))
    except Exception:                                         # noqa: BLE001
        # A level archive has gameoptions.swui and no mainoptions.swui, and
        # vice versa. Absence is an ordinary answer here, not a failure.
        return []
    m = _NAMES.search(cur)
    if m:
        return [n for n in m.group(1).decode("latin-1").split("|") if n]

    # A page written before the record line existed. Fall back to the visible
    # rows, which is LOSSY past the display cap -- but silently returning []
    # here would make the next add_mod() wipe a list instead of extending it,
    # and losing a name quietly is the worse failure.
    if MARK_OPEN not in cur:
        return []
    block = _BLOCK.search(cur)
    if not block:
        return []
    rows = re.findall(rb"<TEXT:200,\d+,0:([^>]*)>", block.group(0))
    out = []
    for r in rows:
        text = r.decode("latin-1")
        if text in ("Mods on this disc", "(none)") or text.startswith("and "):
            continue
        out.append(text)
    return out


def add_mod(archive, name, dry=False):
    """Append one mod to what the disc advertises, keeping what was there.

    This is the call for a build path: the Studio knows the name of the mod it
    just applied, and nothing else has to remember the rest. Adding a name that
    is already listed is a no-op rather than a duplicate row.
    """
    have = read_names(archive)
    want = record_name(name)
    if want in have:
        return have if dry else install(archive, have)
    names = have + [want]
    return names if dry else install(archive, names)


def _write(archive, filename, new, label, steps):
    """Patch a member only if its bytes actually change. Reports what it did."""
    entry = archive.find(filename)
    cur = archive.read(entry)
    if new == cur:
        steps.append((label, filename, 0, 0))
        return
    wrote, slot = archive.patch(entry, new)
    steps.append((label, filename, wrote, slot))


def remove_screen_route(archive, steps=None):
    """Undo the screen route: unregister it and drop both link rows.

    What this is for: a disc built with `install_screen` SOFT-LOCKS anyone who
    opens the page. This takes the trap out without a recopy -- a few hundred
    bytes inside FRONT.DAT. The orphaned `modlist.swui` member is left in the
    archive, unreferenced and unreachable: `Dave` has no remove, and a member
    nothing names costs a gap it was already occupying.
    """
    steps = [] if steps is None else steps
    cache = archive.find(CACHE_FILE)
    cur = archive.read(cache)
    keep = [ln for ln in cur.replace(b"\r\n", b"\n").split(b"\n")
            if ln.strip() and ln.strip() != SCREEN_NAME.encode()]
    _write(archive, CACHE_FILE, b"\r\n".join(keep), "unregistered", steps)
    for fn in (BOOK_SCREEN, OPTIONS_SCREEN):
        entry = archive.find(fn)
        _write(archive, fn, _OURS.sub(b"", archive.read(entry)),
               "unlinked", steps)
    return steps


def install_disc(game, names, dry=False):
    """Put the mod list on EVERY copy of the Options page on a disc.

    **`mainoptions.swui` exists twice** -- once in `FRONT.DAT` and once in
    `SWAT.DAT` -- and patching only the first is why a disc could carry the
    block and still show a stock Options page. Which copy the engine resolves
    depends on the archive set open at the time, so the only safe answer is
    both. `patch_everywhere` is this codebase's idiom for exactly that, and it
    reports the copies it could not fit rather than aborting.

    Returns (applied, skipped) from `Game.patch_everywhere`, plus the FRONT.DAT
    housekeeping `install()` does (unregistering the dead screen route).
    """
    block = inline_block(names)

    def edit(cur):
        if _BLOCK.search(cur):
            return _BLOCK.sub(block, cur)
        if cur.count(OPTIONS_ANCHOR) != 1:
            raise ValueError("anchor matched %d times, expected 1"
                             % cur.count(OPTIONS_ANCHOR))
        return cur.replace(OPTIONS_ANCHOR, OPTIONS_ANCHOR + b"\r\n" + block)

    applied, skipped = game.patch_everywhere(OPTIONS_SCREEN, edit, dry=dry)
    if not dry:
        # The dead screen route only ever went into FRONT.DAT.
        try:
            remove_screen_route(game.archive(ARCHIVE))
        except Exception as ex:                               # noqa: BLE001
            print("  could not clear the old screen route: %s" % ex)
    return applied, skipped


def install_ingame(game, names, dry=False):
    """Put the mod list on the IN-GAME Options page too, in every archive.

    Why this exists: `gameoptions.swui` is what Start -> Options shows during
    play, it is a different file from the front end's `mainoptions.swui`, and
    it is where a player who is already in a game will look. A mod list you
    have to know the route to is one nobody finds.

    The buttons move; the list does not. See the comment on INGAME_BUTTONS_NEW.

    It lives in 62 archives. One of them, `LVLS_SNG/BARFIGHT.DAT`, has a
    384-byte slot against the ~475 this needs, so it is expected to be SKIPPED
    -- a partial application, which `patch_everywhere` reports rather than
    hides. Barfight keeps the stock page; every other level gets the list.
    """
    # No overrides: the list sits exactly where the front-end page puts it.
    block = inline_block(names)

    def edit(cur):
        cur = _move_ingame_buttons(cur)
        if _BLOCK.search(cur):
            return _BLOCK.sub(block, cur)
        anchor = b"<BUTTON:200,%d,0:Sound Options:" % INGAME_BUTTON_Y[2]
        i = cur.find(anchor)
        if i < 0:
            raise ValueError("Sound Options row not found after the move")
        eol = cur.find(b"\r\n", i) + 2
        return cur[:eol] + b"\r\n" + block + cur[eol:]

    return game.patch_everywhere(INGAME_SCREEN, edit, dry=dry)


# ---------------------------------------------------------------------------
# THE REAL SELECTABLE ROW, via an already-classed screen name.
#
# A brand-new screen name cannot work: the factory at 0x00118b78 strcmps the
# name and gives anything it does not recognise the BASE page class, which
# renders and handles no input -- that is C-039 and it soft-locks.
#
# But 25 names DO have their own class, and `videooptions` is one of them
# (arm at 0x00118e74, `new(0x454)` -- 8 bytes larger than the base 0x44c, so a
# real subclass) while being registered NOWHERE: absent from
# `frontendcache.swui`, from `singleplayercache.swui`, and from the executable's
# 14 front-end registrations. `videooptions.swui` is already in FRONT.DAT. It is
# a working class with nothing wired to it.
#
# So: register the name in data, put the list on its page, and link a real
# BUTTON row to it. Zero executable bytes, and the row inherits a class that
# already drives the pad.
#
# ITS THREE STOCK BUTTONS ARE KEPT ON PURPOSE. Removing a widget a class looks
# up by name is how you get a NULL dereference -- the class stores widget
# pointers and writes through them (`sb flag,240(widget)`). The page is
# retitled and the list added below; the video-mode rows stay. That is a wart,
# and it is the honest trade against a crash on someone else's boot.
# The in-game screen registry is a DIFFERENT FILE from the front end's. A row
# registered only in `frontendcache.swui` is invisible to the pause menu, and
# OpenScreen there returns NULL -- the row would draw and do nothing.
INGAME_CACHE = "singleplayercache.swui"
# The row on the pause-menu page. Its buttons sit at 133/173/213, so 253 is the
# next slot at the same 40px pitch: band 242..274, clear of the 335 prompts.
INGAME_ROW = (b"<BUTTON:200,253,0:Mod List:videooptions:0,0:640,448:1:-1:0>"
              b"\r\n")
ROW_SCREEN = "videooptions"
ROW_FILE = "videooptions.swui"
ROW_TITLE_OLD = b"<TEXT:200,70,0:COLOR:1,1,1,1:Choose Video Mode>"
ROW_TITLE_NEW = b"<TEXT:200,70,0:COLOR:1,1,1,1:Mod List>"
ROW_ANCHOR = b"<BUTTON:80,200,0:Use NTSC 60Hz:CALLB:0,0,640,448,0:-1:Set_NTSC>"
# A real BUTTON row on the Options page, pointing at the classed name.
ROW_BUTTON = (b"<BUTTON:200,266,0:Mod List:" + ROW_SCREEN.encode() +
              b":0,0:640,448:1:-1:0>\r\n")


def install_row(game, names, dry=False, strip_stock=False,
                ingame=False, i_know_this_hangs=False):
    """**PROVEN TO HANG THE CONSOLE. Refuses unless you ask for it by name.**

    `videooptions` has a class, which is what made it look like a free host for
    a selectable row. It is not free -- it is a video-mode confirmation screen,
    and its constructor stores a **ten-second countdown**:

        0x00118e9c  lui  at,0x4120      ; 0x41200000 = 10.0
        0x00118eb4  swc1 f0,1100(s0)

    Ten seconds after the page opens, with no input, the timer fires and the
    game hangs: black screen, frame delta 0.000, every pad button dead, 31
    threads burning CPU with zero disc reads. Measured by rdr-play on
    `RDR_CROWD_060_ROW4`, and confirmed independently by the reviewer waiting fifteen
    seconds on the same build after it had already been reported working.

    **It was reported working twice** -- once by me, once by the reviewer -- because
    opening a page, seeing it and backing out takes about three seconds. That
    is `05_Docs/CHECKS-THAT-LIE.md` entry 20, presence mistaken for function,
    committed twice against the same page by the person who wrote the entry.

    Kept so the finding can be reproduced. **Use `install_disc` + `install_ingame`
    instead** -- names printed on both Options pages, no navigation added, and
    nothing that can trap a player.
    """
    if not i_know_this_hangs:
        raise ValueError(
            "install_row() builds a disc whose Mod List page HANGS the console "
            "ten seconds after it opens -- a countdown compiled into the "
            "videooptions class at 0x00118e9c, measured twice on hardware. "
            "There is no data fix. Use install_disc()+install_ingame() for the "
            "shipping route; pass i_know_this_hangs=True only to reproduce it.")
    """A genuinely selectable "Mod List" row, with no executable patch.

    Three data edits, and `install_disc` should have been run first so the
    Options page itself already carries the names as a fallback:

      1. `videooptions.swui` -- retitled, list added, explicit CLOSEUI back
      2. `frontendcache.swui` -- registers the name so OpenScreen can find it
      3. `mainoptions.swui` (every copy) -- the row that links to it

    Returns a list of (what, where, wrote, slot). Raises rather than half-doing
    it if any anchor is missing.
    """
    steps = []
    block = inline_block(names, head_y=250, first_y=272)

    # 1. the page itself
    entry = game.archive(ARCHIVE).find(ROW_FILE)
    cur = game.archive(ARCHIVE).read(entry)
    body = cur.replace(ROW_TITLE_OLD, ROW_TITLE_NEW)

    # strip_stock=True SOFT-LOCKS THE GAME. Measured by rdr-play on
    # RDR_CROWD_060_ROW2: selecting the front-end Mod List row draws the page
    # over the top of Options, goes black, and spins -- frame delta 0.000 over
    # 18s, every pad button pressed, no way out. 31 threads, 18.8% CPU, zero
    # disc reads: a hang inside the game, not a crash.
    #
    # The disc with the SAME row and the buttons LEFT IN (RDR_CROWD_060_ROW)
    # opened that page and backed out cleanly. The three commented buttons are
    # the only difference between them.
    #
    # So this is the NULL the comment below always warned about, and it is now
    # observed rather than hypothesised. **Do not pass strip_stock=True.** It
    # is kept only so the finding can be reproduced.
    if strip_stock:
        # Comment the three stock rows out rather than delete them: the file
        # keeps a record of what was there, and a commented tag is not a
        # widget, so the page renders clean.
        #
        # WHY THIS IS WORTH THE RISK. Two of them are `Set_PAL` / `Set_NTSC`
        # callbacks that can change the console's video mode -- a real hazard
        # on a page a player is now invited into. The third links to
        # `ntsctest`, which has NO class: it is harmless only because it is
        # unregistered, and it would be a C-039 soft-lock the moment anyone
        # registered that name.
        #
        # THE RISK IT CARRIES. This class stores widget pointers and writes
        # through them. If it looks one of these up by name, it gets NULL and
        # the game may fault. Nothing in the disassembly says it does, but
        # nothing says it does not either -- only a boot settles it, and the
        # un-stripped disc stays the fallback.
        n = 0
        out = []
        for line in body.split(b"\r\n"):
            if (line.startswith(b"<BUTTON:80,")
                    and (b":ntsctest:" in line or b":Set_PAL>" in line
                         or b":Set_NTSC>" in line)):
                out.append(b";" + line)
                n += 1
            else:
                out.append(line)
        if n != 3:
            raise ValueError("expected 3 stock video rows, commented %d" % n)
        body = b"\r\n".join(out)
    if _BLOCK.search(body):
        body = _BLOCK.sub(block, body)
    else:
        if body.count(ROW_ANCHOR) != 1:
            raise ValueError("%s: stock NTSC row matched %d times, expected 1"
                             % (ROW_FILE, body.count(ROW_ANCHOR)))
        eol = body.find(b"\r\n", body.find(ROW_ANCHOR)) + 2
        back = (b"<BUTTON:200,340,0:Back:CLOSEUI:0,0:640,448:1:-1:0>\r\n")
        body = body[:eol] + b"\r\n" + block + back + body[eol:]
    if not dry:
        # grow=True: this file's slot is 224 bytes and the page is now far
        # larger, so it relocates into FRONT.DAT's free gap. Every other
        # member keeps its offset; `verify` asserts that.
        wrote, slot = game.archive(ARCHIVE).patch(entry, body, grow=True)
        steps.append(("page", ROW_FILE, wrote, slot))

    # 2. register the name, or OpenScreen returns NULL and the row does nothing
    cache = game.archive(ARCHIVE).find(CACHE_FILE)
    cur = game.archive(ARCHIVE).read(cache)
    reg = cache_with(cur, ROW_SCREEN)
    if not dry and reg != cur:
        wrote, slot = game.archive(ARCHIVE).patch(cache, reg)
        steps.append(("register", CACHE_FILE, wrote, slot))

    # 3. the row, on every copy of the Options page
    # 3. the row, on every copy of the front-end Options page.
    #
    # THE PRINTED NAMES COME OFF ANY PAGE THAT GAINS THE ROW. They were always
    # redundant once the row leads to them, and leaving both is not merely
    # untidy: the row's band is 255..287 and the names occupy 242..316, a 24px
    # overlap that shipped to the developer's picker because I checked that the row
    # WORKED and never band-checked the page it sat on.
    def edit(d):
        d = _BLOCK.sub(b"", d)
        if ROW_BUTTON in d:
            return d
        if d.count(OPTIONS_ANCHOR) != 1:
            raise ValueError("Sound Options row matched %d times, expected 1"
                             % d.count(OPTIONS_ANCHOR))
        return d.replace(OPTIONS_ANCHOR, OPTIONS_ANCHOR + ROW_BUTTON)
    if not dry:
        applied, skipped = game.patch_everywhere(OPTIONS_SCREEN, edit)
        for arc, w, sl in applied:
            steps.append(("row", arc, w, sl))
        for arc, why in skipped:
            steps.append(("SKIPPED", arc, 0, 0))
            print("  could not add the row to %s: %s" % (arc, why))

    # 4. the pause menu: register the name in ITS registry, then the row.
    if not dry and ingame:
        cache2 = game.archive("SWAT.DAT").find(INGAME_CACHE)
        cur2 = game.archive("SWAT.DAT").read(cache2)
        reg2 = cache_with(cur2, ROW_SCREEN)
        if reg2 != cur2:
            w, sl = game.archive("SWAT.DAT").patch(cache2, reg2)
            steps.append(("register", INGAME_CACHE, w, sl))

        def edit_ig(d):
            d = _BLOCK.sub(b"", d)
            if INGAME_ROW in d:
                return d
            d = _move_ingame_buttons(d)
            anchor = b"<BUTTON:200,%d,0:Sound Options:" % INGAME_BUTTON_Y[2]
            i = d.find(anchor)
            if i < 0:
                raise ValueError("Sound Options row not found")
            eol = d.find(b"\r\n", i) + 2
            return d[:eol] + INGAME_ROW + d[eol:]
        ap2, sk2 = game.patch_everywhere(INGAME_SCREEN, edit_ig)
        steps.append(("row", "%s x%d" % (INGAME_SCREEN, len(ap2)), 0, 0))
        for arc, why in sk2:
            print("  could not add the pause row to %s: %s" % (arc, why))
    return steps


def read_names_disc(game):
    """The names as the disc advertises them, from any page that carries them.

    Checks both Options pages: the front end's and the in-game one. They are
    different files in different archives and either may be the only one
    patched, so asking only one of them can report an empty list for a disc
    that plainly shows names.
    """
    for screen in (ROW_FILE, OPTIONS_SCREEN, INGAME_SCREEN):
        for arc in game.locate(screen):
            names = read_names(game.archive(arc), screen)
            if names:
                return names
    return []


def install(archive, names, dry=False):
    """Put the mod names where they will actually work.

    Writes them as <TEXT> onto `mainoptions` -- a screen with a class, so the
    pad already works there and Cancel already leaves. Also undoes the screen
    route if a previous build left one, because that route traps the player.
    """
    if dry:
        return plan(archive, names)
    steps: list = []
    remove_screen_route(archive, steps)
    entry = archive.find(OPTIONS_SCREEN)
    cur = archive.read(entry)
    block = inline_block(names)
    if _BLOCK.search(cur):
        new = _BLOCK.sub(block, cur)
    else:
        if cur.count(OPTIONS_ANCHOR) != 1:
            raise ValueError(
                "%s is not the file this block was written against: the "
                "anchor matched %d times, expected 1"
                % (OPTIONS_SCREEN, cur.count(OPTIONS_ANCHOR)))
        new = cur.replace(OPTIONS_ANCHOR, OPTIONS_ANCHOR + b"\r\n" + block)
    _write(archive, OPTIONS_SCREEN, new, "listed", steps)
    return steps


def install_screen(archive, names, dry=False, options_door=True,
                   i_know_this_soft_locks=False):
    """The screen route. **PROVEN TO SOFT-LOCK -- do not ship a disc with it.**

    Refuses unless `i_know_this_soft_locks=True`. The module docstring claimed
    that refusal before it existed, which is the worst kind of wrong comment --
    a safety claim a reader would rely on. rdr-app's review of REQ-083 is what
    made me go and look.

    Kept because the finding is worth being able to reproduce: the screen
    registers, loads and renders, and then handles no input at all, because
    the executable's screen-class factory gives an unknown name the base page.
    See the module docstring. `install()` is the one to call.

    `archive` is an open `Dave` for FRONT.DAT on a WRITABLE disc. The caller
    owns the disc and the lock: this touches nothing else and takes nothing.

    Idempotent -- run it again with a different `names` and the screen is
    rewritten in place, the cache line is not doubled, and the book row is
    left alone because it is already there.
    """
    if not i_know_this_soft_locks:
        raise ValueError(
            "install_screen() builds the standalone modlist.swui page, which "
            "renders correctly and then handles NO pad input at all -- the "
            "executable gives an unrecognised screen name the base page class "
            "(0x001191dc). A disc built with it traps the player. Use "
            "install() for the shipping route; pass "
            "i_know_this_soft_locks=True only to reproduce C-039.")
    body = screen(names)
    steps = []

    if dry:
        return plan(archive, names)

    # 1. the screen itself -- added if new, patched in place if it is already
    #    there, so a rebuild with a different mod set costs nothing new.
    existing = None
    try:
        existing = archive.find(SCREEN_FILE)
    except Exception:                                         # noqa: BLE001
        existing = None
    if existing is None:
        res = archive.add(SCREEN_FILE, body)
        steps.append(("added", SCREEN_FILE, res["offset"], len(body)))
    else:
        wrote, slot = archive.patch(existing, body)
        steps.append(("patched", SCREEN_FILE, wrote, slot))

    # 2. register the name, so the front end can find the page at all
    cache = archive.find(CACHE_FILE)
    cur = archive.read(cache)
    new = cache_with(cur)
    if new != cur:
        wrote, slot = archive.patch(cache, new)
        steps.append(("registered", CACHE_FILE, wrote, slot))
    else:
        steps.append(("registered", CACHE_FILE, 0, 0))

    # 3. the row on the story book -- where the brief asks for it
    _add_row(archive, BOOK_SCREEN, BOOK_ANCHOR, BOOK_ROW, "linked", steps)

    # 4. the same destination under Options, as the control. See the comment
    #    on OPTIONS_ANCHOR: this is what makes one boot diagnostic.
    if options_door:
        _add_row(archive, OPTIONS_SCREEN, OPTIONS_ANCHOR, OPTIONS_ROW,
                 "linked", steps)

    return steps
