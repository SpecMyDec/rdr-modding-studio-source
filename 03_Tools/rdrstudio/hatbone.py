"""Where a character's hat goes, and whether they wear it at all.

A tester, on the animation player: *"automatically places hats on characters
their `head` bone, some characters wear their hats to their `hat01` bone
(their back), and some don't use their hats. Option to toggle Hat Bone and
toggle show/hide hat would be nice."*

He is right about all three, and the disc turns out to answer every one of
them itself, which makes this less of a toggle than a correction. Every
character ships a `.swt` -- the SWAT creature type, plain text -- and three of
its 548 fields are about the hat:

    SwatCreatureType/Hat          0 or 1: does this character wear one
    SwatCreatureType/HatBone      the bone it hangs from
    SwatCreatureType/HatOffset    an offset from that bone

MEASURED over all 183 distinct `.swt` on the disc:

    Hat         1 on 131, 0 on 52          -- 28% of the cast wear no hat
    HatBone     head 175, spine_02 4, hat01 2, wrist_r 2
    HatOffset   0 0 0 on all 183           -- nothing to apply, ever

So the answer is not a head/hat01 toggle: there are FOUR mount bones in the
shipped data. `spine_02` is the hat slung on the back (`np3`, `twg`, `tw7`,
`mtwg`), `hat01` is a bone that exists in exactly 2 of the disc's 236
skeletons and in both of the characters that name it (`ani`, `mani`), and
`wrist_r` is `own01`/`own07` carrying it in the hand.

AND IT CHECKS OUT AGAINST THE RIGS. Every HatBone named by a character whose
skeleton is on the disc exists in that character's own skeleton: 124 of 124,
no misses, no fallbacks needed. That is what makes this field worth trusting
rather than merely reading.

WHAT IS WRONG TODAY, counted. 101 bundles carry a hat mesh, and the viewer
hangs all of them on `head`:

    74   Hat=1 HatBone=head      correct today
    19   no .swt of their own    no answer on the disc; `head` is the guess
     4   Hat=0 HatBone=head      a hat is drawn on a character who wears none
     1   Hat=0 HatBone=spine_02  the same, and on the wrong bone
     3   Hat=1, not head         ani (hat01), np3 (spine_02), own07 (wrist_r)

Eight of the 101 are wrong, five of them by drawing a hat that should not be
there. The 19 with no `.swt` include `chr`, the player -- so the old rule is
kept as the documented default rather than replaced, and it is `head` for all
of them.

The other direction is worth saying too: 8 characters say `Hat=1` and have no
hat mesh in their own bundle (`bma`, `bt1`, `dua`, `frj`, `mon`, `own01`,
`rhd`, `tw7`). Nothing is drawn for them and nothing is wrong; they are
reported as `no hat mesh in this bundle` rather than passed over, because
"the tool shows no hat" and "the character has no hat" are different answers
and only one of them is a bug.

THE FACE IS NOT THE HAT. A character bundle holds a body LOD plus rigid parts
-- a face and a hat -- and the viewer today hangs BOTH on the same bone. The
face belongs on `head` whatever the hat does, so a mount is per part, not per
character. That is the whole reason `mounts()` returns a dict.
"""

from . import props

BLOCK = "SwatCreatureType"
FIELD_WEARS = BLOCK + "/Hat"
FIELD_BONE = BLOCK + "/HatBone"
FIELD_OFFSET = BLOCK + "/HatOffset"

# The bone the viewer has always used, and still uses when the disc does not
# answer. Keep identical to studio.head_bone / head_bone_index -- see
# default_mount below for why that matters.
DEFAULT_BONE = "head"


class HatSpec:
    """What the disc says about one character's hat.

    `wears` is None, not False, when there is no `.swt` to ask -- 19 of the
    101 bundles with a hat mesh are in that position, the player among them.
    "We do not know" and "the character wears no hat" lead to different
    behaviour and must not share a value.
    """

    __slots__ = ("wears", "bone", "offset", "source")

    def __init__(self, wears=None, bone=None, offset=(0.0, 0.0, 0.0),
                 source="default"):
        self.wears = wears
        self.bone = bone
        self.offset = offset
        self.source = source

    @property
    def known(self):
        return self.source == "swt"

    def to_json(self):
        return dict(wears=self.wears, bone=self.bone, offset=list(self.offset),
                    source=self.source, known=self.known)

    def __repr__(self):
        return "HatSpec(wears=%r, bone=%r, source=%r)" % (
            self.wears, self.bone, self.source)


def read_swt(data):
    """Parse a `.swt`'s three hat fields. -> HatSpec.

    A file that parses but carries none of them comes back `source="default"`,
    because a `.swt` without a `HatBone` tells us no more than no `.swt` at
    all -- and 4 of the 548 keys really are absent from some files.
    """
    try:
        have = {f.path: f.raw.strip() for f in props.parse(data)}
    except Exception:
        return HatSpec()
    bone = have.get(FIELD_BONE) or None
    wears_raw = have.get(FIELD_WEARS)
    if bone is None and wears_raw is None:
        return HatSpec()
    wears = None
    if wears_raw is not None:
        # "1"/"0" on every file measured; anything else is not a yes.
        wears = wears_raw.strip() in ("1", "true", "True")
    return HatSpec(wears=wears, bone=bone, offset=_xyz(have.get(FIELD_OFFSET)),
                   source="swt")


def _xyz(raw):
    """`HatOffset` is three floats separated by spaces on 173 files and by
    TABS on the other 10, so split on whitespace rather than on " "."""
    if not raw:
        return (0.0, 0.0, 0.0)
    parts = raw.split()
    if len(parts) != 3:
        return (0.0, 0.0, 0.0)
    try:
        return tuple(float(p) for p in parts)
    except ValueError:
        return (0.0, 0.0, 0.0)


def spec_for(dave, stem):
    """The HatSpec for a character, read from `<stem>.swt` in this archive.

    Never raises: a bundle with no `.swt` is the ordinary case for 182 of the
    307 in SWAT.DAT, most of which are props and weapons rather than
    characters, and the caller wants "no answer" rather than an exception.
    """
    try:
        return read_swt(dave.read(stem + ".swt"))
    except Exception:
        return HatSpec()


# ------------------------------------------------------------------ parts

def is_hat(name):
    """Is this chunk the hat? `hat_<stem>_lod_01` on all 101 that have one.

    The same prefix test `character_parts` already uses to keep a hat out of
    the body-LOD list, stated once here so the two cannot drift.
    """
    return bool(name) and name.lower().startswith("hat")


def is_face(name):
    """`<stem>_face` on all 92 that have one."""
    return bool(name) and "face" in name.lower() and not is_hat(name)


def part_kind(name):
    return "hat" if is_hat(name) else "face" if is_face(name) else "other"


# ------------------------------------------------------------------ mounting

class Mount:
    """Which bone one rigid part hangs from, and whether it is drawn at all.

    `position` is that bone's world position, which is what a rigid part's
    vertices are offset by; `bone_index` is its index in `.skel` file order,
    which is what an animated viewer weights the part to. Both come from the
    same bone by construction, because supplying them separately is how the
    static preview and the animated one end up disagreeing.
    """

    __slots__ = ("part", "name", "kind", "bone", "bone_index", "position",
                 "visible", "why")

    def __init__(self, part, name, kind, bone, bone_index, position,
                 visible, why):
        self.part = part
        self.name = name
        self.kind = kind
        self.bone = bone
        self.bone_index = bone_index
        self.position = position
        self.visible = visible
        self.why = why

    def to_json(self):
        return dict(part=self.part, name=self.name, kind=self.kind,
                    bone=self.bone, bone_index=self.bone_index,
                    position=None if self.position is None
                    else list(self.position),
                    visible=self.visible, why=self.why)

    def __repr__(self):
        return "Mount(%s %r on %r, visible=%r)" % (
            self.part, self.name, self.bone, self.visible)


def _find(bones, name):
    """Index of a bone by name, case-insensitively. -> int or None."""
    if not name:
        return None
    low = name.lower()
    for i, b in enumerate(bones):
        if b.name.lower() == low:
            return i
    return None


def default_bone_index(bones, world):
    """The rule the viewer has always used: the bone called `head`, else the
    highest one.

    A duplicate of studio.head_bone_index, and deliberately so for now: this
    module cannot import studio (studio imports the model code, not the other
    way round) and studio's copy is rdr-app's file to change. They must stay
    identical -- if they drift, a hat lands in one place in the still preview
    and another in the animated one. `verify_hatbone.py` asserts they agree on
    every character rig on the disc, so the drift cannot happen quietly.
    """
    if not bones:
        return None
    i = _find(bones, DEFAULT_BONE)
    if i is not None:
        return i
    return max(range(len(bones)), key=lambda j: world[bones[j].name][1])


def resolve_bone(bones, world, spec=None, override=None):
    """Which bone this character's hat hangs from. -> (index, name, why).

    Precedence, and each step is a different kind of knowledge:

      1. `override` -- the person looking at the model said so. Always wins,
         including over the disc, because the whole point of the control is
         to try a hat somewhere the shipped data does not put it.
      2. the character's own `.swt` HatBone, when that bone exists in THIS
         rig. It exists on 124 of 124 measured, but the rig is a parameter in
         this app -- you can pose one character's mesh on another's skeleton
         -- so it is checked rather than assumed.
      3. the old rule: `head`, else the highest bone.

    An override naming a bone the rig does not have is reported, not
    substituted: picking a bone that is not there is a mistake worth seeing.
    """
    if override:
        i = _find(bones, override)
        if i is None:
            return None, override, "no bone called %r in this rig" % override
        return i, bones[i].name, "you chose %s" % bones[i].name
    if spec is not None and spec.bone:
        i = _find(bones, spec.bone)
        if i is not None:
            return i, bones[i].name, "the character's .swt says %s" % spec.bone
        # The disc's answer does not fit the rig in front of us. Say which,
        # rather than falling through silently to a different bone.
        j = default_bone_index(bones, world)
        return (j, bones[j].name if j is not None else None,
                "the .swt says %s, which this rig does not have, so %s"
                % (spec.bone, bones[j].name if j is not None else "nothing"))
    j = default_bone_index(bones, world)
    if j is None:
        return None, None, "this rig has no bones"
    return j, bones[j].name, "no .swt for this character, so the default %s" \
        % bones[j].name


def mounts(picks, bones, world, spec=None, bone=None, show=None):
    """Where every part of this character hangs. -> {chunk index: Mount}.

    `picks` is `studio.character_parts`'s list of `(chunk_index, name)`.
    `bones` and `world` come from `skeleton.parse` and
    `skeleton.world_positions`.

    `bone` and `show` are the two controls a tester asked for, and both are
    None when nobody has touched them:

      bone   name of the bone to hang the hat from, overriding the disc
      show   True to force the hat visible, False to force it hidden

    Only the HAT is affected by either. A face rides `head` whatever the hat
    does; a skinned body part rides its own per-vertex bones and is handed a
    mount with `bone_index=None` so a caller can tell the two apart without
    re-testing the name.
    """
    hat_i, hat_name, why = resolve_bone(bones, world, spec, bone)
    head_i = default_bone_index(bones, world)
    head_name = bones[head_i].name if head_i is not None else None
    head_pos = world[head_name] if head_name is not None else None
    hat_pos = world[hat_name] if (hat_i is not None and hat_name) else None

    out = {}
    for index, name in picks:
        kind = part_kind(name)
        if kind == "hat":
            visible, vis_why = _visible(spec, show)
            out[index] = Mount(index, name, kind, hat_name, hat_i, hat_pos,
                               visible, why if visible else vis_why)
        else:
            # Face, and anything else rigid: the head bone, as before. A
            # skinned body ignores this entirely -- merge_models only applies
            # a mount to a part with no per-vertex bone -- but it is reported
            # for every part so a caller never has to ask "was this one
            # skipped, or is it hidden".
            out[index] = Mount(index, name, kind, head_name, head_i, head_pos,
                               True, "rides %s" % head_name
                               if head_name else "no head bone in this rig")
    return out


def _visible(spec, show):
    """Is the hat drawn? -> (bool, why).

    The override is a real tri-state: True shows, False hides, None defers to
    the disc. `show=False` has to be distinguishable from `show=None` or the
    hide half of the control cannot work, which is why this is not `show or
    ...`.
    """
    if show is not None:
        return bool(show), ("you chose to show it" if show
                            else "you chose to hide it")
    if spec is not None and spec.wears is False:
        return False, "the character's .swt says they wear no hat"
    if spec is not None and spec.wears is True:
        return True, "the character's .swt says they wear one"
    return True, "no .swt for this character, so it is drawn as before"


def merge_with_mounts(bundle, picks, bone_world, by_part, merge=None):
    """Merge a character with every rigid part on ITS OWN bone.

    `studio.merge_models` takes one `head` and applies it to every part that
    carries no per-vertex bone -- which is right for a character whose only
    rigid part is a face, and wrong the moment a hat belongs somewhere else.
    This calls it once per part, each with that part's own mount, and stitches
    the results; a part whose mount says `visible=False` is not merged at all,
    which is the show/hide half of the control.

    `merge` is the merge function, injected so this module does not import
    `studio` at load time (studio imports the model code, not the other way
    round). It defaults to `studio.merge_models`, imported lazily.

    The stitching is bookkeeping and nothing else: vertex indices in `tris`
    and triangle indices in `groups` are rebased, everything else
    concatenates. `parts[].attach` carries the bone NAME rather than the old
    literal "head", because "head" was only ever true by assumption.
    """
    if merge is None:
        from .studio import merge_models as merge
    out = None
    for index, name in picks:
        m = by_part.get(index)
        if m is not None and not m.visible:
            continue
        one = merge(bundle, [(index, name)], bone_world,
                    None if m is None else m.position,
                    None if m is None else m.bone_index)
        if not one:
            continue
        if m is not None:
            for p in one["parts"]:
                if p.get("attach") != "skin":
                    p["attach"] = m.bone or "head"
        out = _concat(out, one)
    return out


def _concat(a, b):
    """Two merged models into one. Rebases `tris` and `groups`; the rest
    concatenates. Returns `b` untouched when there is nothing to join it to."""
    if a is None:
        return b
    base = len(a["verts"])
    tri_base = len(a["tris"])
    a["verts"].extend(b["verts"])
    a["tris"].extend((x + base, y + base, z + base) for x, y, z in b["tris"])
    a["uvs"].extend(b["uvs"])
    a["bones"].extend(b["bones"])
    a["influences"].extend(b["influences"])
    a["groups"].extend((slot, first + tri_base, count)
                       for slot, first, count in b["groups"])
    a["parts"].extend(b["parts"])
    a["skinned"] = a["skinned"] or b["skinned"]
    a["bone_count"] = max(a["bone_count"], b["bone_count"])
    return a


def summary(picks, bones, world, spec=None, bone=None, show=None):
    """The whole answer in one dict, for a route to hand to the UI.

    `bones_available` is every bone in this rig, so the toggle can be a list
    to choose from rather than a free-text box -- the four bones the disc
    actually uses are not guessable and `hat01` exists in 2 rigs of 236.
    """
    ms = mounts(picks, bones, world, spec, bone, show)
    hat = next((m for m in ms.values() if m.kind == "hat"), None)
    return dict(
        spec=(spec or HatSpec()).to_json(),
        hat=None if hat is None else hat.to_json(),
        has_hat_mesh=hat is not None,
        note=("no hat mesh in this bundle" if hat is None else hat.why),
        default_bone=(bones[default_bone_index(bones, world)].name
                      if bones else None),
        bones_available=[b.name for b in bones],
        mounts=[m.to_json() for m in ms.values()],
    )
