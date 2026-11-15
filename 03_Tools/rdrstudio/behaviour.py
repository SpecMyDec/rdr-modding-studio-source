"""Behaviour tables: the `.tbl` that binds states, and the `.stateb` it names.

An enemy's `Table` field names a `.tbl`. A `.tbl` is nine-ish lines of plain
text, each binding one of the engine's behaviour slots to a state machine:

    StartState:        sher70_gigolobuddy_start
    AttackState:       sher70_gigolobuddy_start
    ...
    EndState:          Enemy_End
    DontMoveState:     default_dontmove_state
    UniversalState:    Sher70_Always

Each value is the stem of a `.stateb` in the SAME archive. That set is exactly
knowable, which is the only reason a picker is possible here: *measured on the
pristine disc*, 27,263 of the 27,544 state bindings across all 3,275 `.tbl`
resolve to a `.stateb` sitting in their own archive (98.98%), and all 281
misses are inside SWAT.DAT -- every one of the 570 `.tbl` in the 35 level
archives resolves completely.

WHAT IS EDITABLE, AND WHAT IS NOT
---------------------------------
A `.tbl` binding is a filename, and the legal filenames are on the disc. So it
gets a dropdown, never a text box.

A `.stateb` is a token stream (`actb.py`). Its FLOAT tokens are parameters and
are safely editable in place -- four bytes for four bytes, so the file length
never moves and the archive slot cannot overflow. Its INTEGER tokens are
opcodes, indices and counts, and *nothing on the disc names them*
(`aivocab.py` has the proof: the executable carries a name for every one of
the 257 conditions and for none of the 571 actions). Control flow is therefore
not authorable and this module does not pretend otherwise: `numbers()` returns
floats as editable and everything else as read-only context.

THREE THINGS THE DISC SAYS THAT THE REST OF THE TOOL DOES NOT
-------------------------------------------------------------
All three are *measured* here, on `01_Vanilla\\RDR_VANILLA.iso`:

  1. "Nine states" is the common case, not the rule. The count runs 5, 6, 7,
     8, 9, 10, 12 and 16; 8 is the mode (2,107 files) and 9 is second (1,027).
     Ten `.tbl` in SWAT.DAT write the same eight keys TWICE, which is why
     paths carry `props`'s occurrence suffix (`StartState#2`).
  2. The key spelling is not consistent. 2,927 of the 27,544 state lines are
     spelled in lower case (`dontmovestate` 800, `specialstate` 797,
     `endstate` 664, `partnerstate` 660, `universalstate` 6), and
     `cast.read_table`'s `(\\w+State)` regex is case-sensitive, so the Cast
     palette silently under-reports those tables. This module reads through
     `props.parse` and sees all of them. There is also a `DeadState` (230
     lines) and one `AlwaysState`, neither of which is in `cast.STATE_ORDER`.

     `cast.read_table` actually loses 3,034 lines, not 2,927, and the gap is
     worth naming because it is a SECOND bug hiding behind the first: 35 more
     are mixed case (`Universalstate` 33, `Partnerstate` 2), and the last 72
     are duplicate keys collapsing, because `read_table` returns a dict and
     nine `.tbl` in SWAT.DAT bind the same eight slots twice.
  3. `.tbl` uses `//` for a trailing comment. 67 archive entries contain one
     anywhere; on a STATE line specifically it is 35 entries (31 distinct
     names) over 113 lines -- and eight of those lines are in one file, so
     this is not a curiosity that four tables have. Every `tombmng_*_crowd`
     comments all eight of its bindings. So the value is the FIRST
     whitespace-separated token and the span written back is that token alone;
     the comment survives untouched. Reading the whole value would have shown
     `Duelist_Enemy_D5_1_special //Barfight_Theater1Main_start` as an
     unresolvable state name in BARFIGHT.
"""

import re
from typing import Any

from . import props as P

# What a state name may be spelled with. Deliberately strict: this string is
# written into a file the game looks a filename up by, and a stray space or
# quote is not a misbehaving enemy, it is a lookup that finds nothing.
_NAME_OK = re.compile(r"^[A-Za-z0-9_.\-]+$")


def is_table_key(key):
    """Is this `props` key one of the behaviour-slot bindings?

    Suffix match, case-insensitive, because the disc spells the same key both
    ways (`DontMoveState` 2,483 times, `dontmovestate` 800). Anything else in
    a `.tbl` is left alone rather than offered as a state.
    """
    return key.lower().endswith("state")


def states(data):
    """Every `SomethingState: name` binding in a `.tbl`, in file order.

    `start`/`end` bound the NAME TOKEN only -- not `props`'s whole value span,
    which would swallow a trailing `//comment` (see the module docstring).
    """
    out = []
    for f in P.parse(data):
        if not is_table_key(f.key):
            continue
        # `props` has already trimmed the whitespace either side of the value,
        # so the name token starts exactly at `f.start`.
        parts = f.raw.split(None, 1)
        head = parts[0] if parts else ""
        out.append(dict(key=f.key, path=f.path, value=head,
                        start=f.start, end=f.start + len(head),
                        trailing=parts[1].strip() if len(parts) > 1 else "",
                        line=f.line))
    return out


def state_palette(dave):
    """Every `.stateb` stem in this archive -- the complete legal value set."""
    return sorted({e.name[:-7] for e in dave if e.ext == "stateb"})


def resolve_state(palette, value):
    """The palette's own spelling for `value`, or None. Case-insensitive.

    Returning the palette's spelling rather than the user's keeps a re-save of
    an untouched field a zero-byte edit, the same reason `cast.resolve_table`
    does it.
    """
    want = str(value).strip().lower()
    for stem in palette:
        if stem.lower() == want:
            return stem
    return None


class BadState(Exception):
    def __init__(self, code, message, fix=""):
        Exception.__init__(self, message)
        self.code = code
        self.message = message
        self.fix = fix


def set_states(data, changes):
    """Rewrite state bindings in place. `changes` is {path: new_name}.

    Only the name token moves, so indentation, key spelling, trailing spaces,
    `//` comments and every untouched line come back byte-identical. The file
    still changes LENGTH when a name changes length -- these are text files in
    compressed slots, so the caller must still check the slot.
    """
    by_path = {s["path"]: s for s in states(data)}
    edits = []
    for path, value in changes.items():
        s = by_path.get(path)
        if s is None:
            raise BadState("not_found", "This table has no %s line." % path,
                           "The slots a table binds differ from table to "
                           "table; only the ones it already has can change.")
        text = str(value).strip()
        if not _NAME_OK.match(text):
            raise BadState(
                "unknown_value",
                "%r is not a name a state file can have." % text,
                "Letters, digits, underscore, dot and hyphen only.")
        edits.append((s["start"], s["end"], text.encode("latin-1")))
    out = bytearray(data)
    for start, end, blob in sorted(edits, reverse=True):
        out[start:end] = blob
    return bytes(out)


def set_fields(data, changes):
    """`Studio.edit_creature`'s setter shape, so `.tbl` reuses that loop."""
    return set_states(data, changes)


def table_doc(dave, filename, archive=None):
    """One `.tbl`, its bindings, and the palette each binding may choose from."""
    data = dave.read(filename)
    palette = state_palette(dave)
    lower = {p.lower() for p in palette}
    rows = []
    for s in states(data):
        rows.append(dict(
            key=s["key"], path=s["path"], value=s["value"],
            trailing=s["trailing"], line=s["line"],
            # A value that names no file in this archive is REPORTED, never
            # quietly swapped for a legal one. 281 shipped bindings are like
            # this, all of them in SWAT.DAT.
            resolves=s["value"].lower() in lower,
            file=s["value"] + ".stateb" if s["value"] else None))
    e = dave.find(filename)
    return dict(archive=archive or dave.label, file=filename,
                bytes=len(data), stored=e.size_comp, slot=e.slot,
                headroom=e.slot - e.size_comp,
                states=rows, palette=palette,
                unresolved=[r["value"] for r in rows if not r["resolves"]])


def tables(dave):
    """Every `.tbl` in this archive, with the count of bindings each carries."""
    out = []
    for e in sorted(dave, key=lambda x: x.name):
        if e.ext != "tbl":
            continue
        try:
            n = len(states(dave.read(e)))
        except Exception:
            n = None
        out.append(dict(file=e.name, id=e.name[:-4], states=n,
                        bytes=e.size_full, stored=e.size_comp, slot=e.slot,
                        headroom=e.slot - e.size_comp))
    return out


# --------------------------------------------------------------- .stateb

#: How far back to look for a name token to caption a number with. A `.stateb`
#: names its states and conditions inline, so the nearest preceding name is
#: usually the rule the number belongs to -- but "usually" is doing work here,
#: which is why the row says `near` and not `belongs_to`.
_LANDMARK_SPAN = 24


def numbers(data, limit=200000):
    """The float parameters in a `.stateb`, each with its surrounding tokens.

    Floats only. An integer in this stream is an opcode, an index or a count
    and there is no way on this disc to tell which -- `aivocab.describe_action`
    is the long version. Offering them as "numbers to tune" would be offering
    control flow as a parameter, which is the one mistake this layer exists to
    avoid.

    Which four bytes ARE a float is `actb._is_float`'s range test, not anything
    the file declares, so a row here is a strong hint and not a guarantee.
    Two of the 24 rows in `sher70_gigolobuddy_start.stateb` read 2e-06 and
    2052.91, which look far more like an integer that happens to decode as a
    plausible float than like a tuning knob. The UI says this; the caller must
    not present these as a labelled parameter list.
    """
    from . import actb
    from . import aivocab

    version, count, tokens = actb.parse(data, limit=limit)
    rows = aivocab.annotate((version, count, tokens))

    out: list[dict[str, Any]] = []
    for i, r in enumerate(rows):
        if r["kind"] != "float":
            continue
        near, back = None, None
        for j in range(i - 1, max(-1, i - 1 - _LANDMARK_SPAN), -1):
            if rows[j]["kind"] == "name":
                near, back = rows[j]["value"], i - j
                break
        out.append(dict(
            off=r["off"], value=r["value"], index=len(out),
            near=near, near_tokens_back=back,
            before=rows[i - 1]["label"] if i else None,
            after=rows[i + 1]["label"] if i + 1 < len(rows) else None))
    return dict(version=version, header_count=count, tokens=len(rows),
                bytes=len(data), numbers=out,
                names=[r["value"] for r in rows if r["kind"] == "name"][:40],
                counts=dict(
                    floats=len(out),
                    ints=sum(1 for r in rows if r["kind"] == "int"),
                    names=sum(1 for r in rows if r["kind"] == "name"),
                    opcodes=sum(1 for r in rows if r.get("action")),
                    unresolved_text=sum(1 for r in rows if r["kind"] == "chars")),
                truncated=len(rows) >= limit)


def float_offsets(data, limit=200000):
    """Byte offsets of every float token. The only offsets a write may touch."""
    from . import actb
    return {t.offset for t in actb.parse(data, limit=limit)[2]
            if t.kind == "float"}


def set_numbers(data, changes, limit=200000):
    """Replace float parameters by offset. `changes` is {offset: value}.

    Four bytes for four bytes, so the file length never changes and no slot
    can overflow. Every offset is checked against the file's own float tokens
    first: writing four bytes at an unchecked offset is how a name gets cut in
    half, and nothing downstream could tell afterwards.

    This is `Studio.edit_creature`'s setter shape, so a `.stateb` edit reuses
    the same "every archive that holds it, one undo step" loop as a `.cr`.
    """
    from . import actb

    legal = float_offsets(data, limit=limit)
    out = data
    for off, value in changes.items():
        off = int(off)
        if off not in legal:
            raise BadState(
                "not_editable",
                "There is no number at byte %d in this state file." % off,
                "Only the float parameters this file actually holds can be "
                "changed. Whole numbers are opcodes and indices, and nothing "
                "on the disc names them.")
        try:
            v = float(value)
        except (TypeError, ValueError):
            raise BadState("bad_request", "%r is not a number." % value, "")
        out = actb.set_float(out, off, v)
    return out


# ----------------------------------------------------------------- cloning

# The alphabet `dave.encode_packed_name` can spell, minus the characters a
# filename has no business carrying. Checked here so the refusal names the
# rule rather than surfacing a ValueError from the encoder.
_CLONE_OK = re.compile(r"^[a-z0-9_\-]+$")


_TABLE_FIELD = re.compile(rb"(?m)^([ \t]*Table[ \t]+)(\S+)")


def table_referrers(dave, stem):
    """Everything in this archive that names `<stem>.tbl`, by kind.

    A `.tbl` is referred to by NAME, so renaming one is a referential edit and
    not a cosmetic one. There are two classes and they are not equally easy:

      `enm`    an enemy's `Table` field. Plain text, one token, safe to
               rewrite -- and common: measured on the pristine disc, 1,258 of
               the 3,275 `.tbl` are named by at least one enemy, 798 by
               exactly one and 87 by five or more. The worst is `actor` in
               BARFIGHT.DAT, named by **20**.
      `stateb` a `.stateb` / `.actb` naming it inline. rdr-re settled this as
               opcode 1003 (C-036, 1,581 uses, 99.1% resolving to `.tbl`
               names). These are token streams, and rewriting a name inside
               one is not a text edit -- so this module REPORTS them and
               refuses rather than pretending otherwise.

    Matched against `actb`'s decoded inline NAME tokens, never against raw
    bytes. A substring scan is useless here: real stems include `actor` and
    `core`, which occur all over the disc by coincidence.
    """
    from . import actb as A
    want = str(stem).strip().lower()
    enm, locked, enemies = [], [], 0
    for e in sorted(dave, key=lambda x: x.name):
        if e.ext not in ("enm", "stateb", "actb", "acttbl"):
            continue
        try:
            data = dave.read(e)
        except Exception:
            continue
        if e.ext == "enm":
            # Two different numbers, and the UI needs both: how many FILES
            # must be rewritten, and how many ENEMIES that actually changes.
            # `actor` in BARFIGHT.DAT is 2 files and 20 enemies -- reporting
            # either one alone misleads in a different direction.
            hits = sum(1 for m in _TABLE_FIELD.finditer(data)
                       if m.group(2).decode("latin-1").lower() == want)
            if hits:
                enm.append(e.name)
                enemies += hits
            continue
        if not A.is_actb(data):
            continue
        try:
            _v, _c, tokens = A.parse(data)
        except Exception:
            continue
        if any(t.kind == "name" and str(t.value).lower() == want
               for t in tokens):
            locked.append(e.name)
    return dict(enm=enm, locked=locked, enemies=enemies)


def set_table_field(data, old_stem, new_stem):
    """Repoint an enemy's `Table` field. Only the stem token moves.

    Same discipline as `set_states`: the key, its spacing and everything else
    on the line come back byte-identical, and the file still changes LENGTH
    when the stem does, so the caller checks the slot.
    """
    want = str(old_stem).strip().lower()
    text = str(new_stem).strip()
    if not _NAME_OK.match(text):
        raise BadState("unknown_value",
                       "%r is not a name a table file can have." % text,
                       "Letters, digits, underscore, dot and hyphen only.")
    out = bytearray(data)
    for m in reversed(list(_TABLE_FIELD.finditer(data))):
        if m.group(2).decode("latin-1").lower() == want:
            out[m.start(2):m.end(2)] = text.encode("latin-1")
    return bytes(out)


def check_rename_table(dave, old, new):
    """Why `old.tbl` can or cannot become `new.tbl` here. Never raises.

    Three separate hazards, and only the first is about the name itself:

      1. the archive's own rules -- alphabet, collision, name-block spare
         (`Dave.can_rename`);
      2. `.stateb` / `.actb` referrers, which this refuses outright because
         rewriting a name inside a token stream is not a solved problem;
      3. `.enm` referrers, which are safe to rewrite but must be rewritten --
         so they are returned for the caller to write, and `ok` stays False
         until the caller says it wants them rewritten.

    `undoable` is False on purpose and is not an oversight: a rename rewrites
    TOC records and name-block bytes, and the journal's undo rewrites file
    CONTENT into a slot. It cannot put a name back. Clone has the same
    property; the plan must say so where the user can see it.
    """
    stem = str(old).strip().lower()
    filename = stem if stem.endswith(".tbl") else stem + ".tbl"
    stem = filename[:-4]
    target = str(new).strip().lower()
    target = target[:-4] if target.endswith(".tbl") else target
    arch_ok = dave.can_rename(filename, target + ".tbl")
    refs = table_referrers(dave, stem)
    why = list(arch_ok["reasons"])
    # The ARCHIVE would take a name the reference syntax cannot carry. The
    # 6-bit alphabet includes a space (index 1 of 49), so `can_rename` accepts
    # `has a space.tbl` quite correctly -- the format can spell it. But an
    # enemy names its table with `Table <token>`, read to the first whitespace,
    # so a table with a space in its name could never be referenced by anything
    # again. Same rule and same reason as `_NAME_OK` on state names: this
    # string is written into a field that is parsed as one token.
    if not _NAME_OK.match(target):
        why.append("%r cannot be a table name: an enemy names its table with "
                   "`Table <name>`, read up to the first space, so only "
                   "letters, digits, underscore, dot and hyphen can survive "
                   "the round trip" % target)
    if refs["locked"]:
        why.append(
            "%d state machine(s) name this table inline (%s%s) -- a name "
            "inside a .stateb token stream cannot be rewritten safely yet, so "
            "this table cannot be renamed"
            % (len(refs["locked"]), ", ".join(refs["locked"][:3]),
               ", ..." if len(refs["locked"]) > 3 else ""))
    return dict(ok=not why, reasons=why,
                file=filename, to=target + ".tbl",
                enm_referrers=refs["enm"], locked_by=refs["locked"],
                rewrites_needed=len(refs["enm"]),
                enemies_affected=refs["enemies"],
                spare_name_bytes=arch_ok["spare_name_bytes"],
                undoable=False)


def state_referrers(dave, stem):
    """Every `.tbl` binding in this archive that names `<stem>.stateb`.

    THE MIRROR OF `table_referrers`, AND THE POLICY COMES OUT THE OTHER WAY.
    Measured over the 35 level archives: **1,315 of 1,315 `.stateb` are named
    by at least one `.tbl` state binding — no orphans at all** — and **zero**
    are named inline inside an `.actb`/`.stateb` token stream. (671 have one
    referring table, 502 have five or more.)

    So the two verbs cannot share a policy, and the difference is not taste:

      renaming a `.tbl`     some referrers live in token streams we cannot
                            rewrite, so a referenced table is REFUSED and the
                            2,017 orphans are what remains renameable.
      renaming a `.stateb`  every referrer is a `.tbl` state binding, which is
                            plain text with a known span -- and since NOTHING
                            is an orphan, refusing while referrers exist would
                            refuse 100% of them and the verb would not exist.
                            So these are REWRITTEN, not refused.

    Scope note, because it is a real limit: this is measured on the level
    archives. SWAT.DAT is where all 281 unresolved bindings on the disc live,
    so a `.stateb` there may well have no referrer -- the code handles that
    (an empty list is simply a rename with nothing to repoint) but the "no
    orphans" figure is a statement about levels, not the whole disc.
    """
    want = str(stem).strip().lower()
    tbl, bindings = [], 0
    for e in sorted(dave, key=lambda x: x.name):
        if e.ext != "tbl":
            continue
        try:
            data = dave.read(e)
        except Exception:
            continue
        hits = sum(1 for st in states(data) if st["value"].lower() == want)
        if hits:
            tbl.append(e.name)
            bindings += hits
    return dict(tbl=tbl, bindings=bindings)


def repoint_states(data, old_stem, new_stem):
    """Rewrite every binding in one `.tbl` that names `old_stem`.

    Goes through `set_states`, so the same guarantees hold: only the name
    token moves, and key spelling, indentation and a trailing `//comment` come
    back byte-identical. The file still changes LENGTH when the name does, so
    each referrer needs its own slot check -- which is why this returns bytes
    for the caller to plan rather than writing anything itself.
    """
    want = str(old_stem).strip().lower()
    changes = {st["path"]: str(new_stem).strip()
               for st in states(data) if st["value"].lower() == want}
    if not changes:
        return data
    return set_states(data, changes)


def check_rename_state(dave, old, new):
    """Why `old.stateb` can or cannot become `new.stateb` here. Never raises.

    Unlike `check_rename_table` this does not refuse over referrers, because
    every `.stateb` on a level has them and all of them are rewritable -- see
    `state_referrers`. What it reports instead is the COST: which `.tbl` must
    be rewritten, how many bindings that is, and (the part that bites) that a
    longer name grows every one of those tables inside its own archive slot.
    502 of 1,315 states are named by five or more tables.

    `undoable` is False for the same reason it is on a table rename: the
    journal rewrites file CONTENT into a slot and cannot put a name back.
    """
    stem = str(old).strip().lower()
    filename = stem if stem.endswith(".stateb") else stem + ".stateb"
    stem = filename[:-7]
    target = str(new).strip().lower()
    target = target[:-7] if target.endswith(".stateb") else target

    arch_ok = dave.can_rename(filename, target + ".stateb")
    why = list(arch_ok["reasons"])
    # A state name is written into `SomeState: <name>`, read as one token, so
    # it takes the same alphabet as a table name and for the same reason.
    if not _NAME_OK.match(target):
        why.append("%r cannot be a state name: a table binds it as "
                   "`SomeState: <name>`, read up to the first space, so only "
                   "letters, digits, underscore, dot and hyphen survive the "
                   "round trip" % target)

    refs = state_referrers(dave, stem)
    # Cost every referrer through `Dave.would_fit`, which is the same rule
    # `patch` writes by. Asking whether `len(out)` exceeds the slot would be
    # the WRONG question twice over: the slot holds the DEFLATED size, and a
    # same-length rename can still change it (C-030). On the pristine disc
    # `barfight_bar1.tbl` is stored 128 of a 128-byte slot -- zero headroom --
    # so a state rename that lengthens the name genuinely cannot be written
    # there, and the plan has to say so before the write rather than during.
    grows = []
    if not why and target != stem:
        for name in refs["tbl"]:
            try:
                data = dave.read(name)
            except Exception:
                continue
            out = repoint_states(data, stem, target)
            grows.append(dave.would_fit(name, out))
    blocked = [w for w in grows if not w["fits"]]
    if blocked:
        worst = min(blocked, key=lambda w: w["headroom"])
        why.append(
            "%d of the %d table(s) that name this state cannot take the new "
            "name in their slot -- %s would need %d bytes of %d (over by %d)"
            % (len(blocked), len(grows), worst["file"], worst["stored"],
               worst["slot"], worst["stored"] - worst["slot"]))
    return dict(ok=not why, reasons=why,
                file=filename, to=target + ".stateb",
                tbl_referrers=refs["tbl"], bindings=refs["bindings"],
                rewrites_needed=len(refs["tbl"]), rewrites=grows,
                blocked_by_slot=[w["file"] for w in grows if not w["fits"]],
                spare_name_bytes=arch_ok["spare_name_bytes"],
                undoable=False)


def check_clone_name(dave, name):
    """Why `name.tbl` can or cannot be added to this archive. Never raises."""
    stem = str(name).strip().lower()
    why = []
    if not stem:
        why.append("a new table needs a name")
    elif not _CLONE_OK.match(stem):
        why.append("letters, digits, underscore and hyphen only -- the "
                   "archive's name table is a 6-bit alphabet")
    if len(stem) > 60:
        why.append("that name is longer than anything on the disc")
    filename = stem + ".tbl"
    if any(e.name == filename for e in dave):
        why.append("%s is already in this archive" % filename)
    return dict(ok=not why, reasons=why, file=filename, id=stem)
