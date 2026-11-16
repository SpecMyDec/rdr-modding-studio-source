"""Swapping a character's model in SOME levels and not others.

M9. a tester: *"With character .rscs, quick option to copy data from one to
override another one would be great... automatically change their .cr
FileNameRender, to the new model.. I do this since facial animations sync up if
they are all changed using only the .cr"* — and, in his own bold,
**"Important that it gives the option to do it per level"**.

`modelswap.py` already did the hard half and did it well: it establishes that
**repointing `FileNameRender` beats copying geometry** (the face mesh and its
morph targets live in the same bundle as the body, so the bundle is the atomic
unit), that `FileNameRender` and `FileNameSkel` are never split (181 of 181),
that bone counts must match, and that a target bundle has to be present in the
archive being written. None of that is re-litigated here.

**What was missing is the per-level part, and it is not cosmetic.** A `.cr`
is not one file. It is copied into every archive that needs the character, and
the archives ARE the levels:

    how many archives hold one .cr:  1 -> 71 creatures    2 -> 50
                                     3 -> 22              4 -> 11
                                     5..10 -> 26          57 -> 1

**110 of 181 creatures live in more than one level.** `ani.cr` is in eight,
and the outlier is `srd.cr` -- Red himself -- in **57**. The player is the
case that makes this milestone matter: swapping his model without a level
choice rewrites him in fifty-seven levels from one button.
`modelswap.plan_swap` returns a row for every archive that holds the `.cr`, so
applying its plan changes the character in every level at once — which is the
one thing a tester singled out as needing to be a choice. This module makes the
set of levels an argument.

**Each level is written independently and can fail independently.** A level
whose archive has no copy of the target bundle cannot be written at all — the
disc never relies on a lookup escaping its archive — so it is REPORTED as
skipped rather than written with a reference nothing can answer. That is
`modelswap`'s third constraint, and per-level scoping makes it a per-level
answer instead of an all-or-nothing one.
"""

from . import modelswap as _ms


class SwapRefused(Exception):
    pass


def levels_for(game, cr_name, target=None, stock=None):
    """Which levels hold this character, and which could take the swap.

    -> [dict(archive, holds_target, reason)] — every archive that holds the
    `.cr`, in a stable order, each labelled. A caller shows this list and the
    user ticks the levels they mean; nothing is implied by omission.
    """
    f = _ms.facts_for(game, stock)
    if cr_name.lower() not in f.stock:
        raise KeyError(cr_name)
    arcs = list(f.archives_for(cr_name))
    writable = set(f.resolves_in(target, arcs)) if target else set(arcs)
    out = []
    for arc in arcs:
        ok = arc in writable
        out.append(dict(
            archive=arc,
            holds_target=ok,
            reason=None if ok else (
                "%s has no %s_lods.rsc, so this level keeps its own model"
                % (arc, target)),
        ))
    return out


def plan(game, cr_name, target, archives, stock=None):
    """Plan the swap for exactly the levels named. -> (rows, skipped).

    `archives` is the chosen set. An archive that does not hold the `.cr` at
    all is an error rather than a silent no-op: it means the caller and this
    module disagree about where the character is, and guessing which of us is
    right is how a user ends up editing a level they did not pick.
    """
    if not archives:
        raise SwapRefused("no level was chosen; nothing would change")
    f = _ms.facts_for(game, stock)
    if cr_name.lower() not in f.stock:
        raise KeyError(cr_name)
    known = set(f.archives_for(cr_name))
    unknown = [a for a in archives if a not in known]
    if unknown:
        raise SwapRefused(
            "%s is not in %s" % (cr_name, ", ".join(sorted(unknown))))

    writable = set(f.resolves_in(target, list(archives)))
    changes = {k: target for k in _ms.FIELDS}

    rows, skipped = [], []
    for arc in archives:
        if arc not in writable:
            skipped.append(dict(archive=arc, reason=(
                "%s has no %s_lods.rsc, so this level keeps its own model"
                % (arc, target))))
            continue
        dave = game.archive(arc)
        entry = dave.find(cr_name)
        before = dave.read(entry)
        after = _ms.set_fields(before, changes)
        if after == before:
            skipped.append(dict(archive=arc, reason=(
                "%s already points at %s here" % (cr_name, target))))
            continue
        rows.append(dict(archive=arc, name=cr_name, before=before,
                         after=after, slot=entry.slot,
                         grew=len(after) > len(before)))
    if not rows:
        raise SwapRefused(
            "nothing to write: %s" % "; ".join(s["reason"] for s in skipped))
    return rows, skipped


def apply(game, rows, writer=None):
    """Write the planned rows. -> [dict(archive, name, used, slot)].

    `writer(archive, name, data) -> (used, slot)` lets the Studio interpose
    its journal so the swap is undoable; without one the write goes straight
    to the archive. Rows are written in order and the caller is told exactly
    which ones landed, so a failure halfway is reported rather than inferred.
    """
    done = []
    for row in rows:
        arc, name = row["archive"], row["name"]
        if writer is not None:
            used, slot = writer(arc, name, row["after"])
        else:
            used, slot = game.archive(arc).patch(name, row["after"])
        done.append(dict(archive=arc, name=name, used=used, slot=slot))
    return done


def names_in(game, cr_name, archive):
    """What this level's copy of the `.cr` currently points at.

    Keys are the FILE's own field names -- `FileNameRender`, `FileNameSkel`,
    `FileNameAnim` -- because those are the keys `set_fields` takes. Note
    `cast.read_creature` answers the same question with short keys
    (`render`/`skel`/`anim`); mixing the two up returns None for everything
    and reads as "the field is missing" rather than "you asked wrongly".
    """
    return _ms.read_names(game.archive(archive).read(cr_name))


def untouched_elsewhere(game, cr_name, written, stock=None):
    """Every OTHER level's copy, with its bytes, for an after-the-fact check.

    The point of a per-level swap is what it did NOT change, and that is the
    half a caller is most likely to take on trust.
    """
    f = _ms.facts_for(game, stock)
    out = []
    for arc in f.archives_for(cr_name):
        if arc in set(written):
            continue
        out.append(dict(archive=arc,
                        data=game.archive(arc).read(cr_name)))
    return out
