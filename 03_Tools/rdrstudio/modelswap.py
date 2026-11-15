"""Point a creature at another character's model -- body, face and morphs.

A `.cr` opens with three names and the middle two are the whole of "what does
this character look like":

    FileName {
            FileNameAnim    red
            FileNameRender  ncb
            FileNameSkel    ncb
    }

WHY REPOINTING BEATS COPYING GEOMETRY. The face mesh and the morph targets
that drive its expressions live in the SAME `*_lods.rsc` bundle as the body.
Measured over the 122 bundles a `.cr` can name: 84 carry BOTH an `MPH0` chunk
and a `<stem>_face` MOD0, 38 carry neither, and NOT ONE carries only one of
them -- the two sets are identical. So the bundle is the atomic unit. Changing
`FileNameRender` swaps body, hat, face and morphs together and the expressions
stay addressed to the mesh they were authored against. Copying MOD0 body data
into another bundle leaves that bundle's own face and morph targets in place,
pointed at vertices that no longer exist.

THE THREE CONSTRAINTS, all measured on `01_Vanilla\\RDR_VANILLA.iso`:

  1. `FileNameRender == FileNameSkel` in 181 of 181 `.cr` files. They are
     never split, so a swap writes BOTH or it is not a swap.

  2. Bone counts must match. A MOD0's byte 1 is its bone count (rsc.py) and it
     equals the paired `.skel`'s `NumBones` for 122 of 122 render bundles; an
     animation's channel count is `3 + 3 * bones` (bvh.py), and `FileNameAnim`
     is NOT changed by a swap. 139 of the 181 creatures share the 52-bone rig,
     which is why this is a real palette and not a curiosity.

  3. The target bundle has to be in the same archive as the `.cr`. 517 of 517
     `.cr` copies on the disc have their own `_lods.rsc` AND their own `.skel`
     beside them, in every one of the 66 archives -- the disc never relies on
     a lookup escaping its archive. Only SWAT.DAT holds all 122 bundles; the
     median bundle appears in 2 archives. So a swap can only be written where
     the target already is, and `plan_swap` reports the rest as untouched
     rather than writing a reference the archive cannot answer.

WHAT THE DISC PROVES AND WHAT IT DOES NOT. 59 of the 181 `.cr` files already
point at a bundle that is not their own stem, so the indirection is honoured
by the shipped game. Every one of those 59 is a variant of the same character:
53 contain the target stem outright (`mmrd` -> `mrd`, `zztbar` -> `zzt`,
`sev1/2/3` -> `sev`) and the remaining 6 abbreviate it (`dg2` -> `dgo`,
`hr2` -> `hrs`, `ky2` -> `kya`, `tt2` -> `ttk`, `tw7` -> `twg`,
`zz2` -> `zzt`). Nothing on this disc is an arbitrary cross-character swap, so
nothing on this disc proves one works. The UI says that in those words.

HITBOXES DO NOT COME ALONG. `<stem>.hot` and `<stem>.sphs` name a bone in
`parent`, and those names resolve in the skeleton the character's own `.cr`
points at -- 2,136 of 2,136 `.hot` parent lines and 885 of 885 `.sphs` lines
across the 181 creatures. (Resolved against `<stem>.skel` instead the figures
are 2,476 and 635, and the shortfall is entirely the 59 borrowers: `mmrd.sphs`
has no `mmrd.skel`, and never needed one.) Nothing in the `.cr` names these
files -- the pairing is by stem -- so a swap leaves `ncb.hot` parented to bone
names chosen for a skeleton the character no longer uses.

Measured over every pair: 696 of the 21,716 conceivable (creature, target)
pairs DO lose a parent bone, so the check is live. Over the 12,826 the picker
actually offers -- equal bone counts -- ZERO do, because the same-size rigs
share their bone names. `check_hitboxes` runs every time and reports which of
those two answers it got, because "checked, nothing missing" is a different
sentence from "not checked".
"""

import re

# Anchored at the start of a line so a `FileNameRender` appearing in a comment
# or a value cannot be rewritten. Captures the separator as well as the value:
# a swap keeps `sep + value` the same width wherever it can, which is what
# lets it land in a `.cr` whose slot has no headroom at all.
_LINE = re.compile(
    rb"(?m)^([ \t]*)(FileNameRender|FileNameSkel|FileNameAnim)([ \t]+)(\S+)")

FIELDS = ("FileNameRender", "FileNameSkel")

# A stem is a bare filename component. Anything else is a caller bug, and it
# would be written verbatim into a path the engine resolves.
_STEM = re.compile(r"^[A-Za-z0-9_]{1,24}$")


def read_names(data):
    """{"FileNameRender": "ncb", ...} -> whatever the file actually declares.

    `cast.read_creature` answers the same question for the UI's character
    list; this one keeps the field names as written because those are the
    keys `set_fields` takes.
    """
    return {m.group(2).decode("latin-1"): m.group(4).decode("latin-1")
            for m in _LINE.finditer(data)}


def set_fields(data, changes):
    """Rewrite FileNameRender / FileNameSkel. Same shape as creature.set_fields.

    That signature is the point: `Studio.edit_creature(setter=...)` already
    knows how to write one file into every archive that holds it as a single
    undo step, and this is the third writer to reuse it rather than fork it.

    Width is preserved by padding the SEPARATOR, never the value: a trailing
    space would be inside the line's value region as far as any other reader
    is concerned, whereas the gap between a key and its value is unambiguously
    whitespace. A shorter target therefore costs zero bytes, and the only case
    that grows the file is a target whose name is longer than the separator
    plus the name it replaces -- at most 3 bytes per field on this disc.

    Which is why no swap this disc allows can overflow a slot: measured, all
    25,103 write rows a swap could ever plan fit. The tightest is `jck2.cr` in
    GHOST.DAT -- 1,120 stored in a 1,120-byte slot, the handoff's zero-headroom
    case -- which lands at 1,115-1,117 with 3 bytes to spare, because this
    recompression happens to beat the one the disc shipped with. The slot check
    still runs; it has simply never had anything to refuse.
    """
    for key, value in changes.items():
        if key not in FIELDS:
            raise KeyError("%r is not a model field; use %s"
                           % (key, " or ".join(FIELDS)))
        if not _STEM.match(str(value)):
            raise ValueError("%r is not a bare model stem" % (value,))

    out = bytearray(data)
    edits = []
    for m in _LINE.finditer(data):
        key = m.group(2).decode("latin-1")
        if key not in changes:
            continue
        sep, old = m.group(3), m.group(4)
        new = str(changes[key]).encode("latin-1")
        want = len(sep) + len(old) - len(new)      # separator that keeps width
        edits.append((m.start(3), m.end(4), sep[:1] * max(1, want) + new))
    if len(edits) != len(changes):
        raise KeyError("this .cr does not declare %s"
                       % ", ".join(sorted(set(changes) - set(read_names(data)))))
    for start, end, text in sorted(edits, reverse=True):
        out[start:end] = text
    return bytes(out)


# ------------------------------------------------------------------ the disc
#
# Everything in `Facts` is a fact about the SHIPPED disc: which bundles exist,
# how many bones each rig has, which archive holds what. None of it can be
# changed by an in-place patch -- `Dave.patch` writes into an existing slot and
# never adds, removes or renames an entry -- so it is read once and kept.
#
# It is built from the PRISTINE image on purpose, and `stock` says so in its
# name. The palette is derived from the render values the `.cr` files name, so
# building it from the working disc would let the palette shrink as the user
# uses it: repoint the last `.cr` that renders as `ncb` and `ncb` drops off the
# list, stranding the user with no way back. The FIRST version of this cached
# the current values too, and a swap left the panel reporting the old model
# because the answer it re-read came out of the cache. What the user has
# changed is read live, every time, by `current_names`.

_CACHE: "dict[str, Facts]" = {}


class Facts:
    """The stock half of a swap: the palette, the rigs, and who holds what."""

    def __init__(self, game):
        from . import cast as cast_mod
        from . import skeleton as skel_mod
        from .rsc import parse_rnt

        self.path = game.path
        self.game = game
        swat = game.archive("SWAT.DAT")

        # The legal palette is exactly the set of bundles a `.cr` already
        # names -- 122 of the 310 `*_lods.rsc` on the disc. The other 188 are
        # props, mounts and effects; some have no `.skel` at all, and offering
        # them would be a guess dressed as a list. Same reasoning as
        # `cast.py`'s skin picker, opposite outcome: here an exact set exists.
        self.stock = {}              # "ncb.cr" -> {"render": .., "skel": ..}
        stems = set()
        for e in swat:
            if e.ext != "cr":
                continue
            info = cast_mod.read_creature(swat.read(e))
            stem = e.name[:-3]
            self.stock[e.name] = dict(
                render=(info["render"] or stem).lower(),
                skel=(info["skel"] or stem).lower(),
                anim=info["anim"])
            stems.add(self.stock[e.name]["render"])

        self.targets = {}
        for stem in sorted(stems):
            file = stem + "_lods.rsc"
            declared, bones = 0, []
            try:
                _, declared, parsed, _ = skel_mod.parse(swat.read(stem + ".skel"))
                bones = [b.name for b in parsed]
            except Exception:                                  # noqa: BLE001
                pass
            # Read out of the `.rnt` MANIFEST, not the bundle. The manifest
            # lists every chunk's tag and name in a few hundred bytes, while
            # opening the bundles themselves means decompressing 26.4 MB to
            # learn two booleans -- 0.2 s against 0.00 s, measured over these
            # 122. The two agree on both flags for 122 of 122.
            face = morphs = False
            try:
                rnt = swat.read(stem + "_lods.rnt")
                morphs = b"MPH0" in rnt
                face = any(v.lower() == stem + "_face"
                           for v in parse_rnt(rnt).values())
            except Exception:                                  # noqa: BLE001
                pass
            self.targets[stem] = dict(
                id=stem, file=file, bones=declared, bone_names=bones,
                # NumBones and the number of `bone { }` blocks disagree for
                # `blk` (52 declared, 62 blocks) and nothing else. The MOD0
                # byte and the animation channel count both follow NumBones,
                # so that is the number a match is decided on; the extra
                # blocks only ever ADD names, so a hitbox check is unaffected.
                blocks=len(bones), face=face, morphs=morphs)

        # `Game.build_index` is the disc-wide filename -> archives map and it
        # is already cached on the Game, so the Studio has usually paid for it
        # before this module is ever called. A private sweep over the same 66
        # tables of contents would cost 6 s and answer the same question.
        game.build_index()

    def archives_for(self, name):
        return sorted(self.game.locate(name))

    def resolves_in(self, target, archives):
        """Which of `archives` can answer a reference to `target`.

        Both files, not just the bundle: `FileNameSkel` moves too. On the
        stock disc a bundle and its `.skel` are never split -- 0 of 8,052
        (archive, target) pairs has one without the other -- but the check
        costs nothing and the invariant is not ours to rely on.
        """
        rsc = set(self.game.locate(target + "_lods.rsc"))
        skel = set(self.game.locate(target + ".skel"))
        return [a for a in archives if a in rsc and a in skel]


def facts(game):
    f = _CACHE.get(game.path)
    if f is None or f.path != game.path:
        f = _CACHE[game.path] = Facts(game)
    return f


def current_names(game, cr_name, stock=None):
    """What this `.cr` says RIGHT NOW -- never cached, never from stock.

    Read from SWAT.DAT, which is the one archive that holds all 181. The
    level copies are written in the same breath, so they agree; if a copy
    somehow does not, the plan shows the byte counts for every copy anyway.
    """
    f = facts(stock or game)
    if cr_name.lower() not in f.stock:
        raise KeyError(cr_name)
    stem = cr_name[:-3].lower()
    try:
        got = read_names(game.read("SWAT.DAT", cr_name))
    except Exception:                                          # noqa: BLE001
        got = {}
    return dict(render=(got.get("FileNameRender") or stem).lower(),
                skel=(got.get("FileNameSkel") or stem).lower(),
                anim=got.get("FileNameAnim"))


# ---------------------------------------------------------------- hitboxes

_PARENT = re.compile(rb"(?mi)^[ \t]*parent[ \t]+(\S+)")
_PARENTS: dict[tuple[str, str], list[tuple[str, list[str]]]] = {}


def hitbox_parents(game, stem):
    """[(filename, [bone name per `parent` line])] for one character.

    Read once per character rather than once per candidate target: the
    palette asks this question of 93 targets at a time and the answer does
    not depend on which one. 125 of the 181 creatures have a `.hot` and 177
    have a `.sphs`, so "no files" is a real and common answer.
    """
    key = (game.path, stem)
    if key in _PARENTS:
        return _PARENTS[key]
    out = []
    for ext in ("hot", "sphs"):
        name = "%s.%s" % (stem, ext)
        arcs = facts(game).archives_for(name) or game.locate(name)
        if not arcs:
            continue
        try:
            data = game.archive(arcs[0]).read(name)
        except Exception:                                      # noqa: BLE001
            continue
        out.append((name, [m.group(1).decode("latin-1")
                           for m in _PARENT.finditer(data)]))
    _PARENTS[key] = out
    return out


def check_hitboxes(game, stem, target):
    """Do `<stem>.hot` / `<stem>.sphs` still name bones the new rig has?

    `checked` is False when the character has neither file, or the target has
    no readable skeleton: "nothing to warn about" and "nothing was looked at"
    have to stay distinguishable, or a silent pass reads as a guarantee.
    """
    bones = set(facts(game).targets.get(target, {}).get("bone_names") or ())
    found = hitbox_parents(game, stem)
    missing = []
    for name, parents in found:
        gone = sorted({p for p in parents if p not in bones})
        if gone:
            missing.append(dict(file=name, bones=gone))
    return dict(checked=bool(found) and bool(bones),
                files=[dict(file=n, parents=len(p)) for n, p in found],
                parents=sum(len(p) for _, p in found),
                missing=missing)


# -------------------------------------------------------------- the palette

def palette(game, cr_name, stock=None):
    """Every legal target for one `.cr`, with what each swap would cost.

    Filtered to matching bone counts, because `FileNameAnim` is untouched by a
    swap: the clips that drive this character carry `3 + 3 * bones` channels
    and would be feeding a rig of a different size.

    `stock` is the pristine disc. It decides what the palette CONTAINS; `game`
    decides where the character is pointed today.
    """
    f = facts(stock or game)
    cur = current_names(game, cr_name, stock)
    stem = cr_name[:-3]
    here = f.targets.get(cur["render"], dict(bones=0))
    arcs = f.archives_for(cr_name)

    rows = []
    for target, t in sorted(f.targets.items()):
        if not t["bones"] or t["bones"] != here.get("bones"):
            continue
        ok = f.resolves_in(target, arcs)
        hits = check_hitboxes(stock or game, stem, target)
        rows.append(dict(
            id=target, bones=t["bones"], face=t["face"], morphs=t["morphs"],
            current=target == cur["render"],
            stock=target == f.stock[cr_name.lower()]["render"],
            archives=len(ok), archives_total=len(arcs), where=ok,
            hitbox_missing=hits["missing"],
            rung="measured" if target == f.stock[cr_name.lower()]["render"]
                 else "untested"))
    return dict(
        name=cr_name, stem=stem,
        current=dict(render=cur["render"], skel=cur["skel"], anim=cur["anim"],
                     bones=here.get("bones", 0),
                     face=here.get("face", False),
                     morphs=here.get("morphs", False),
                     stock=f.stock[cr_name.lower()]["render"],
                     # The two are the same value in 181 of 181 shipped files
                     # and this tool always writes them together, so a split
                     # means something else edited one of them.
                     split=cur["render"] != cur["skel"]),
        archives=len(arcs), archives_all=arcs,
        hitboxes=check_hitboxes(stock or game, stem, cur["render"]),
        targets=rows)


# ------------------------------------------------------------------ the plan

def plan_swap(game, cr_name, target, stock=None):
    """Per-archive rows for `api.plan`, plus the archives left alone.

    Every archive that holds the `.cr` is accounted for in exactly one of the
    two lists, so the sheet can never quietly drop a copy. The byte counts come
    off `game` -- the WORKING disc -- because the slot has to be measured
    against the file as it stands, not as it shipped.
    """
    from . import api

    f = facts(stock or game)
    if cr_name.lower() not in f.stock:
        raise KeyError(cr_name)
    arcs = f.archives_for(cr_name)
    writable = f.resolves_in(target, arcs)
    changes = {k: target for k in FIELDS}

    rows, skipped = [], []
    for arc in arcs:
        if arc not in writable:
            skipped.append(dict(archive=arc, reason=(
                "%s has no %s_lods.rsc, so this copy keeps its own model"
                % (arc, target))))
            continue
        dave = game.archive(arc)
        entry = dave.find(cr_name)
        before = dave.read(entry)
        after = set_fields(before, changes)
        rows.append(api.write_row(arc, cr_name, len(before), len(after),
                                  api.stored_size(entry, after), entry.slot))
    return rows, skipped, writable
