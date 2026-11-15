"""What an enemy is allowed to BE, in one archive.

A level's roster names two independent things and the UI has always shown
both as bare text boxes:

    Type    which creature -- model, skeleton, animation set   (`<id>.cr`)
    Table   which behaviour -- nine bound AI states            (`<id>.tbl`)

They are orthogonal. `actor` drives 42 different creatures; `ncb` is driven by
115 different tables. Any control that presents "the character" as one field
is wrong about the data, so this module returns two lists and never merges
them.

Both palettes are exactly knowable, which is the reason a picker is possible
at all: the legal `Type` values for an archive are precisely the stems of the
`.cr` files inside it, and every `Table` value used on the disc resolves to a
lowercase `.tbl` inside the archive that uses it. Nothing has to be guessed,
so nothing here is inferred.

A `.cr` file names its own render model and skeleton:

    FileName {
        FileNameAnim    red
        FileNameRender  cls
        FileNameSkel    cls
    }

`FileNameRender` is a working model-swap knob -- 59 of the 181 `.cr` files on
the disc point at another creature's model. Those are reported as `borrowed`
rather than hidden, because the game itself proves the trick works.
"""

import re

_FILENAME_BLOCK = re.compile(rb"FileName\s*\{(.*?)\}", re.S)
# Case-insensitive, because the disc spells the same key both ways: 2,927
# state lines are all lower case (`dontmovestate` 800, `specialstate` 797,
# `endstate` 664, `partnerstate` 660) and 35 more are mixed. The exact match
# silently dropped every one of them from the Cast palette -- behaviour.py's
# module docstring counts the loss at 3,034 lines.
_STATE_LINE = re.compile(rb"(?m)^[ \t]*(\w+State)[ \t]*:?[ \t]*(\S+)", re.I)

STATE_ORDER = ("StartState", "AttackState", "DodgeState", "SearchState",
               "PartnerState", "EndState", "SpecialState", "DontMoveState",
               "UniversalState")


def _names(dave):
    return {e.name for e in dave}


def _stems(dave, ext):
    return sorted(e.name[:-(len(ext) + 1)] for e in dave if e.ext == ext)


def _field(body, key):
    m = re.search(rb"\b" + key + rb"\b[ \t]+(\S+)", body)
    return m.group(1).decode("latin-1") if m else None


def read_creature(data):
    """`FileNameAnim` / `FileNameRender` / `FileNameSkel` from a `.cr`."""
    m = _FILENAME_BLOCK.search(data)
    body = m.group(1) if m else data[:2048]
    return dict(anim=_field(body, b"FileNameAnim"),
                render=_field(body, b"FileNameRender"),
                skel=_field(body, b"FileNameSkel"))


def read_table(data):
    """The nine `State: value` bindings from a `.tbl`, in file order.

    Keys are canonicalised onto STATE_ORDER's spelling so `dontmovestate`
    lands in the same slot as `DontMoveState` -- the file's own casing is a
    spelling, not a different binding. A key STATE_ORDER does not name
    (`DeadState`, `AlwaysState`) keeps the file's spelling.
    """
    canon = {s.lower(): s for s in STATE_ORDER}
    states = {}
    for m in _STATE_LINE.finditer(data):
        key = m.group(1).decode("latin-1")
        states[canon.get(key.lower(), key)] = m.group(2).decode("latin-1")
    return states


def _label_for(stem, archive_stem):
    """`sher70_sheriff` in SHER70 -> `sheriff`. The level prefix is noise."""
    low = stem.lower()
    for prefix in (archive_stem + "_", "lvl_" + archive_stem + "_"):
        if prefix and low.startswith(prefix):
            return stem[len(prefix):]
    return stem


def _enm_names(dave):
    return [e.name for e in dave if e.ext == "enm"]


def used_counts(dave, enm_files):
    """How many enemies in this archive's own rosters use each Type / Table.

    Case is preserved as written, because `Table` is written back verbatim and
    the file is the authority on its own spelling -- but the counts are keyed
    lowercase, because the same table appears as `Sher70_sheriff` in one block
    and `SHER70_Sheriff` in another.
    """
    types: dict[str, int] = {}
    tables: dict[str, int] = {}
    spelling: dict[str, str] = {}
    for fn in enm_files:
        try:
            data = dave.read(fn)
        except Exception:
            continue
        for m in re.finditer(rb"(?m)^[ \t]*Type[ \t]+(\S+)", data):
            k = m.group(1).decode("latin-1").lower()
            types[k] = types.get(k, 0) + 1
        for m in re.finditer(rb"(?m)^[ \t]*Table[ \t]+(\S+)", data):
            raw = m.group(1).decode("latin-1")
            k = raw.lower()
            tables[k] = tables.get(k, 0) + 1
            spelling.setdefault(k, raw)
    return types, tables, spelling


def drops(game):
    """The 55 `TypeName` entries in `swat.guns`, as PowerUp kind 9 values.

    `PowerUp 9 Six_Shooter` gives the enemy a weapon to drop; `PowerUp 0
    0.000000` gives nothing. Kind 9 is the only kind whose name has a knowable
    legal set, so it is the only one offered as a list.
    """
    from . import guns
    try:
        data = game.read("SWAT.DAT", "swat.guns")
    except Exception:
        return []
    return [dict(id=w["name"], kind=9) for w in guns.summary(data)]


def _swat_roster_for(game, archive):
    """Does SWAT.DAT hold this level's roster? (the 26 Showdown archives)

    Matched on the archive's OWN `.lvl` manifest names, exactly. Matching
    loosely was tried and is worse than useless here: trimming the trailing
    digit off `BRIDGE2` finds `lvl_bridge_var_1.enm`, which is the
    single-player Bridge roster and has nothing to do with the Showdown
    arena. A roster attributed to the wrong level is a worse answer than no
    roster, because it would be offered as editable.

    Measured: SWAT.DAT holds 307 `.enm` files, and none of them is named for
    any of the 26 archives that carry no roster of their own.
    """
    try:
        dave = game.archive(archive)
        swat = game.archive("SWAT.DAT")
    except Exception:
        return []
    stems = {e.name[:-4].lower() for e in dave if e.ext == "lvl"}
    stems.add("lvl_" + archive.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower())
    hits = []
    for e in swat:
        if e.ext != "enm":
            continue
        n = e.name.lower()
        for st in stems:
            if n == st + ".enm" or n.startswith(st + "_var"):
                hits.append(e.name)
                break
    return sorted(set(hits))


def cast(game, archive):
    """The whole legal palette for one archive.

    Raises KeyError if the archive is not on the disc; the route turns that
    into a `not_found` refusal.
    """
    dave = game.archive(archive)
    names = _names(dave)
    archive_stem = archive.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()

    enm_files = _enm_names(dave)
    roster_source = "local"
    roster_note = ""
    if not enm_files:
        swat = _swat_roster_for(game, archive)
        if swat:
            roster_source = "swat"
            roster_note = ("This level's roster lives in SWAT.DAT, not in this "
                           "archive, so enemies cannot be placed here yet.")
        else:
            roster_source = "none"
            roster_note = ("This level carries no enemy roster of its own, and "
                           "SWAT.DAT holds none named for it either. The "
                           "characters below are the ones the archive ships; "
                           "nothing places them yet.")

    types_used, tables_used, spelling = used_counts(dave, enm_files)

    characters = []
    for stem in _stems(dave, "cr"):
        try:
            info = read_creature(dave.read(stem + ".cr"))
        except Exception:
            info = dict(anim=None, render=None, skel=None)
        render = (info["render"] or stem)
        skel = (info["skel"] or stem)
        characters.append(dict(
            id=stem, file=stem + ".cr", label=stem,
            render=render, skel=skel, anim=info["anim"],
            has_model=(render.lower() + "_lods.rsc") in names,
            has_skel=(skel.lower() + ".skel") in names,
            borrowed=render.lower() != stem.lower(),
            used=types_used.get(stem.lower(), 0),
            rung="measured"))

    behaviours = []
    for stem in _stems(dave, "tbl"):
        try:
            states = read_table(dave.read(stem + ".tbl"))
        except Exception:
            states = {}
        # The id is what gets WRITTEN into `Table`. Prefer the spelling this
        # level's own roster already uses: the game is case-insensitive here
        # but a diff against stock is not, and matching the file keeps an edit
        # to one field from showing up as an edit to two.
        ident = spelling.get(stem.lower(), stem)
        behaviours.append(dict(
            id=ident, file=stem + ".tbl",
            label=_label_for(stem, archive_stem),
            states={k: states.get(k) for k in STATE_ORDER},
            used=tables_used.get(stem.lower(), 0),
            rung="measured"))

    # Skins sit in the same row of pickers as Character and Behaviour, both of
    # which are provably exact: 752 of 752 of their values resolve to a real
    # file in this archive. Returning every .tex made Skin *look* equally
    # authoritative while offering blood, bloodchunks, coin, filmgrain,
    # filmdust, filmscratch, filmtintmap, healthbottle, hud_arrow, hud_cannon
    # and hud_w_shotgun as things a character might wear -- 136 options, most
    # of them nonsense.
    #
    # Nothing on the disc marks a texture as a character skin, so this cannot
    # be made exact the way the other two are. What it can do is drop the
    # categories that provably are NOT skins, and carry rung="inferred" so the
    # UI does not present a filtered guess as a measured fact. That
    # distinction is the entire reason the rungs exist.
    NOT_SKINS = ("blood", "coin", "film", "hud", "health", "smoke", "spark",
                 "flare", "glow", "shadow", "dust", "muzz", "impact", "water",
                 "fx", "ui", "lens", "rain", "snow", "cloud", "sky", "lt",
                 "additive", "fire")

    def _looks_like_skin(stem):
        """False for anything that plainly belongs to effects, HUD or overlays.

        A blunt prefix match, deliberately. The first version tried to be
        clever -- prefix-as-word, plus a list of run-on tails so `filmgrain`
        was caught but a hypothetical `blooddrinker` survived -- and it still
        let `additivefire`, `huddynaknife` and `sparkles` through. Every round
        of patching bought one more name and suggested another. That is the
        signature of a rule being fitted to its examples instead of to
        anything real.

        So: if a texture's name begins with an effects word, it is not offered
        as a skin. This will occasionally drop a legitimate texture whose name
        starts the same way. That is the right way to be wrong here -- the
        picker sits beside Character and Behaviour, which are provably exact,
        and a short honest list reads better than a long list with rubbish in
        it. The rung is `inferred` and the UI says so.

        The exact answer, when it is wanted, is to read which textures the
        character render models actually reference. That is measurable; this
        is not, and does not pretend to be.
        """
        low = stem.lower().strip()
        return not any(low.startswith(p) for p in NOT_SKINS)

    skins = [dict(id=stem, file=stem + ".tex", rung="inferred")
             for stem in _stems(dave, "tex") if _looks_like_skin(stem)]

    return dict(archive=archive,
                roster_source=roster_source, roster_note=roster_note,
                characters=characters, behaviours=behaviours,
                skins=skins, drops=drops(game))


# ------------------------------------------------------------ lookups

def resolve_type(cast_doc, value):
    """The exact `Type` string to write, or None. Case-insensitive."""
    want = str(value).strip().lower()
    for c in cast_doc["characters"]:
        if c["id"].lower() == want:
            return c["id"]
    return None


def resolve_table(cast_doc, value):
    """The exact `Table` string to write, or None. Case-insensitive.

    A value already spelled the way the level spells it comes back unchanged,
    so re-saving an untouched field is a zero-byte edit.
    """
    want = str(value).strip().lower()
    for b in cast_doc["behaviours"]:
        if b["id"].lower() == want or b["file"][:-4].lower() == want:
            return b["id"]
    return None


def resolve_skin(cast_doc, value):
    want = str(value).strip().lower()
    for s in cast_doc["skins"]:
        if s["id"].lower() == want:
            return s["id"]
    return None


def resolve_drop(cast_doc, value):
    want = str(value).strip().lower()
    for d in cast_doc["drops"]:
        if d["id"].lower() == want:
            return d["id"]
    return None
