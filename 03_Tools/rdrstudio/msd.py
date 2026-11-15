"""`.msd` -- MoveSetData. How a character moves, per weapon stance.

521 files on the disc, 185 distinct in SWAT.DAT, one per character, ~150 KB of
plain CRLF text each. It is the third file in the character triple and the
split across the three is worth stating once:

    .cr   how much health it has        (HealthData.MaxHitPoints)
    .swt  how much damage it deals and takes, by limb
    .msd  how it MOVES -- speeds, gaits, jumps, reach

WHY THIS IS NOT A `charfile` KIND. The house rule is that `props` gets checked
first, and here it passes: `props` already addresses 94-100% of the numbers in
every `.msd`, with correct hierarchical paths. `charfile.py` exists for the
formats `props` genuinely cannot reach -- `.dam` has no keys, `.sphs` puts the
block name on the keyword line, `.hot` hides a matrix under a bare word. None
of that applies. So this module is shaped like `swt.py`: a curated view over
`props`, not a second parser.

The last 6% is doubly-subscripted keys (`DuelReactTimeScale[0][0]`), which
`props._KEY` skips whole because it allows one subscript, not many. Widening it
is one character and measured to be purely additive across all 20,490 text
files on the disc -- `scripts/probe_props_subscript.py`, filed as REQ-007.
Nothing here depends on it; those fields simply stay out of reach until it
lands.

WHY THE CURATED SET IS PATH-SHAPED, NOT NAME-SHAPED. `swt.py` can list 40 bare
field names because `.swt` is one flat block, identical in all 183 files. An
`.msd` is a deep tree whose middle segment is character-specific: Red's rifle
speeds live at

    MoveSetData/MobileData/rflSneakStandGaitset/SpeedBand/RunSpeed

and only 139 of 6,791 distinct paths are present in all 185 files. A fixed name
list would miss almost everything. So sections are declared as patterns, the
fields inside them are DISCOVERED from the file in front of us, and
`set_fields` takes full paths.

GAITSETS are the interesting axis. A name is `<stance><Posture>Gaitset`, and
counted across the 185 files in SWAT.DAT:

    ptl dpt sho rfl knf dyn SneakStand   177 files each
    bow mol             SneakStand       176 files each
    everything else                        1 file (ji2) -- the six
                                           SneakCrouch variants, RegDuck,
                                           and noneSneakStand/Crouch

So **176 of the 185 characters carry exactly 8 gaitsets**, one carries 15, and
8 carry none at all -- those eight have no `MobileData` block, and `read`
omits the section rather than showing it empty. `none` is unarmed.

`MobileData` also holds a **`SpeedBand` of its own**, outside any gaitset, in
176 of the 185 files. That is the character's default band and `read` returns
it as a band like the others, labelled as the default, because leaving it out
would hide the numbers that apply when no weapon is drawn.

SLOT COST. `props.apply` rewrites only the value span, so an edit changes no
key, no indentation and no column. That does NOT make a same-width edit free:
these entries are stored deflated, and changing a digit changes how well the
file compresses. Measured across the disc, a same-width digit change fits the
slot in 521 of 521 `.msd` and 519 of 519 `.swt` -- but only 561 of 585 `.rep`
and 520 of 523 `.hot`. Callers must dry-run the write; `routes2` already does.
See `05_Docs/CAPABILITIES.md` C-030.

Ranges quoted in the section blurbs are MEASURED across the 185 files in
SWAT.DAT.
"""

import re

from . import props

BLOCK = "MoveSetData"


class Unknown(KeyError):
    """A path this module does not address.

    Named to match `charfile.Unknown` and `skeledit.Unknown` so a route can
    catch one exception type across the whole character-stat family instead of
    growing a per-module except clause.
    """

# The segment that varies per character, e.g. `rflSneakStandGaitset`. Only
# segments actually named `...Gaitset` qualify: `MobileData` has 50 children in
# a typical file and most of them -- `ClipData`, `Flee`, `StandGait`,
# `DuckWalk` -- are not gaitsets at all. Matching them glossed `Flee` as a
# weapon stance, which is how this was caught.
_GAITSET = re.compile(r"^MoveSetData/MobileData/([A-Za-z0-9_]*[Gg]aitset)/")

# The character's own band, outside any gaitset: what applies with no weapon.
_DEFAULT_BAND = "MoveSetData/MobileData/SpeedBand/"

# What each three-letter gaitset prefix means. Anything unrecognised keeps its
# own name rather than being glossed wrongly.
STANCES = {
    "bow": "bow", "dpt": "dual pistols", "dyn": "dynamite", "knf": "knife",
    "mol": "molotov", "ptl": "pistol", "rfl": "rifle", "sho": "shotgun",
    "none": "unarmed",
}
POSTURES = {"sneakstand": "standing", "sneakcrouch": "crouched"}

# The 15 SpeedBand fields, in the order the files write them. These are the
# headline numbers: metres per second, and the Min/Speed pairs are a band the
# gait blends across rather than two independent values.
SPEED_FIELDS = (
    "WalkMinSpeed", "WalkSpeed", "RunMinSpeed", "RunSpeed",
    "SearchMinSpeed", "SearchSpeed", "RaceingIdleMinSpeed",
    "SideWalkMinSpeed", "SideWalkSpeed", "SideRunMinSpeed", "SideRunSpeed",
    "ReverseWalkMinSpeed", "ReverseWalkSpeed",
    "ReverseRunMinSpeed", "ReverseRunSpeed",
)

# (key, label, blurb, path test). Order is display order.
SECTIONS = (
    ("speed", "Movement speed, per weapon stance",
     "Metres per second. One band per stance -- a character can be quick with "
     "a pistol and slow with a rifle, and most of them are.",
     lambda p: "/SpeedBand/" in p),
    ("jump", "Jump",
     "Launch velocities and gravity per direction, plus the frame numbers the "
     "jump animation switches phase on.",
     lambda p: p.startswith("MoveSetData/JumpData/")
     and p.count("/") == 2),
    ("reach", "Reach and offsets",
     "Where a grab, a lasso or a duel draw is anchored on the body. Measured "
     "spread is wide -- 66 distinct values for GrabData/SpOffset across 177 "
     "characters -- because it tracks how tall the character is.",
     lambda p: p.split("/")[1] in ("GrabData", "LassoData", "DuelData")
     and p.count("/") == 2),
    ("aim", "Aim",
     "Approach and decay rates for bringing the weapon on target.",
     lambda p: p.startswith("MoveSetData/AimData/") and p.count("/") == 2),
)

_BY_KEY = {key: test for key, _l, _b, test in SECTIONS}


def _fields(data):
    return [f for f in props.parse(data) if f.path.startswith(BLOCK + "/")]


def gaitsets(data):
    """The gaitset names this character carries, in file order.

    Eight of the 185 characters have none. Returning [] rather than raising is
    deliberate: "this character has no movement data" is an answer, and the
    caller shows the other sections.
    """
    out = []
    for f in _fields(data):
        m = _GAITSET.match(f.path)
        if m and m.group(1) not in out:
            out.append(m.group(1))
    return out


def stance_of(gaitset):
    """`rflSneakStandGaitset` -> "rifle, standing".

    Both halves are glossed only when both are recognised; anything else comes
    back as its own name. `RegDuckGaitset` exists on exactly one character and
    inventing a meaning for it would be worse than printing it.
    """
    body = gaitset[:-7] if gaitset.lower().endswith("gaitset") else gaitset
    for prefix, stance in STANCES.items():
        if body.lower().startswith(prefix):
            posture = POSTURES.get(body[len(prefix):].lower())
            return "%s, %s" % (stance, posture) if posture else stance
    return gaitset


def _row(f):
    value = f.raw.strip()
    return dict(path=f.path, field=f.path.rsplit("/", 1)[-1], value=value,
                kind=f.kind)


def read(data):
    """The curated view of one `.msd`, grouped for display.

    Speed is grouped a second time, by gaitset, because "this character's
    rifle speeds" is the question people actually have and a flat list of 270
    numbers is not an answer to it.
    """
    fields = _fields(data)
    groups = []
    for key, label, blurb, test in SECTIONS:
        rows = [_row(f) for f in fields if test(f.path)]
        if not rows:
            continue
        group = dict(key=key, label=label, blurb=blurb, fields=rows)
        if key == "speed":
            bands = {}
            for f in fields:
                if "/SpeedBand/" not in f.path:
                    continue
                m = _GAITSET.match(f.path)
                if m:
                    bands.setdefault(m.group(1), []).append(_row(f))
                elif f.path.startswith(_DEFAULT_BAND):
                    bands.setdefault("", []).append(_row(f))
            group["bands"] = [
                dict(gaitset=g, stance=stance_of(g) if g else "default (no weapon drawn)",
                     default=not g, fields=rows_)
                for g, rows_ in bands.items()]
        groups.append(group)
    return groups


def summary(data):
    """Flat {path: value} over the curated set. For diffing against stock."""
    return {f.path: f.raw.strip() for f in _fields(data)
            if any(test(f.path) for _k, _l, _b, test in SECTIONS)}


def editable(path):
    """Is this path inside the curated set?"""
    return (path.startswith(BLOCK + "/")
            and any(test(path) for _k, _l, _b, test in SECTIONS))


def set_fields(data, changes):
    """Rewrite curated fields in place. `changes` is {full path: new value}.

    Keyed on the full path rather than a bare field name, because a bare name
    is ambiguous in this format: `RunSpeed` exists once per gaitset, eighteen
    times in most files. Refuses a path outside the curated set for the same
    reason `swt.set_fields` does -- the Files tab edits anything, and two
    editors sharing responsibility for the same bytes with no shared
    validation is how a format gets corrupted quietly.

    Returns the new bytes. Same length whenever every replacement is the same
    width as what it replaced; see the module docstring on why that still does
    not guarantee the slot.
    """
    bad = sorted(k for k in changes if not editable(k))
    if bad:
        raise Unknown("not editable through the move-set editor: %s"
                      % ", ".join(bad))
    have = {f.path for f in _fields(data)}
    missing = sorted(k for k in changes if k not in have)
    if missing:
        raise Unknown("not present in this character's move set: %s"
                      % ", ".join(missing))
    return props.apply(data, {k: str(v) for k, v in changes.items()})
