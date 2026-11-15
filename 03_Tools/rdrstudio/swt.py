"""`.swt` -- the SWAT creature type. Every damage number a character has.

183 files, one per character, ~19.5 KB each, plain text. One `type: a` line
then a single flat `SwatCreatureType { }` block of 548 fields.

It pairs with `.cr`, and the split is not intuitive: **`.swt` has no health
field**. It says how much damage this character DEALS and how much each of its
limbs takes as a multiplier; how many hit points it HAS is `HealthData.
MaxHitPoints` in the `.cr`. Anyone tuning "how tough is this enemy" needs
both files, which is why the Creatures tab now shows them together.

Editing goes through `props.apply`, which rewrites only the value span -- keys,
indentation and column layout come through byte-identical, so a same-width edit
costs zero UNCOMPRESSED bytes. That matters here: these files sit in slots with
very little headroom, and 62 copies of a character's `.swt` can exist across
the disc.

It does not make the write free, and the distinction is not pedantic. These
entries are stored deflated, so changing a digit changes how well the file
compresses. It happens to be safe for this format -- a same-width digit change
still fits the slot in all 519 `.swt` on the disc -- but the same reasoning
applied to its neighbours is wrong: 24 of 585 `.rep` and 3 of 523 `.hot`
overflow. Dry-run the write; `routes2` does. See `05_Docs/CAPABILITIES.md`
C-030.

The curated list below is 40 of the 548 fields. The rest are real and
editable through the generic Files tab; they are simply not what anyone came
for -- 265 of them are per-weapon holster offsets like
`SixNumbersRH.pistol.HolsterOffset.x`, which only became visible at all when
the props key pattern was widened to accept dotted keys.

Ranges in the comments are MEASURED across all 183 files.
"""

from . import props

BLOCK = "SwatCreatureType"

# (label, [(field, hint, kind)]) -- kind is "num" | "int" | "bool" | "text"
FIELD_GROUPS = [
    ("Damage this character deals", [
        ("PunchDamage", "punch (0-40)", "num"),
        ("PunchStealthDamage", "punch from behind (5-30)", "num"),
        ("KickDamage", "kick (5-40)", "num"),
        ("NutKickDamage", "low kick (5-40)", "num"),
        ("DiveDamage", "diving tackle (5-50)", "num"),
        ("ThrowDamage", "throw (40 on every character)", "num"),
        ("PushDamage", "shove (20 on every character)", "num"),
        ("SuperTackleDamage", "super tackle (75 on every character)", "num"),
    ]),
    ("Damage this character takes, by limb", [
        ("HeadDamage", "headshot multiplier (20-100)", "num"),
        ("BodyDamage", "torso (20-50)", "num"),
        ("RightArmDamage", "right arm (20-30)", "num"),
        ("LeftArmDamage", "left arm (20-30)", "num"),
        ("RightLegDamageUpper", "right thigh (20-30)", "num"),
        ("RightLegDamageLower", "right shin (15-30)", "num"),
        ("LeftLegDamageUpper", "left thigh", "num"),
        ("LeftLegDamageLower", "left shin", "num"),
    ]),
    ("Environmental damage", [
        ("CannonDamage", "cannon hit (50-100)", "num"),
        ("RockDamage", "falling rock (50 everywhere)", "num"),
        ("FireDamage", "burning (10 everywhere)", "num"),
        ("FallDmgPerMeter", "fall damage per metre (0-20)", "num"),
        ("FallDmgMaxDamage", "fall damage ceiling (0-100)", "num"),
        ("FallDmgMinDistance", "free-fall distance before it hurts (3-999)", "num"),
    ]),
    ("Dead-eye and valor", [
        ("ValorForHit", "valor per hit (5 everywhere)", "num"),
        ("ValorForKill", "valor per kill (20 everywhere)", "num"),
        ("ValorBonusForCritical", "bonus for a critical (10)", "num"),
        ("ValorBonusForStealth", "bonus for a stealth kill (10)", "num"),
        ("MaxNotoriety", "notoriety ceiling (3-4)", "num"),
    ]),
    ("Movement and body", [
        ("Height", "character height in metres (0.5-2.1)", "num"),
        ("FleeRunSpeed", "speed when fleeing (4.0 everywhere)", "num"),
        ("PoisonRunSpeed", "speed while poisoned (0.25)", "num"),
        ("WaterRunSpeed", "speed in water (0.63)", "num"),
        ("SpeedModifierBullet", "speed change when shot (1.0)", "num"),
    ]),
    ("Aim and identity", [
        ("HitProbabilityMin", "worst-case AI accuracy (0.0)", "num"),
        ("HitProbabilityMax", "best-case AI accuracy (1.0)", "num"),
        ("DefaultName", "display name", "text"),
        ("Male", "0 or 1", "bool"),
        ("Mountable", "can be ridden", "bool"),
        ("AnimalType", "0 = human, 1-5 animals", "int"),
        ("CreatureSoundSet", "which voice bank", "int"),
        ("HatBone", "bone a hat attaches to", "text"),
    ]),
]

EDITABLE = {f for _, fields in FIELD_GROUPS for f, _, _ in fields}


def _path(field):
    return "%s/%s" % (BLOCK, field)


def read(data):
    """Every curated field present in this file, grouped for display.

    A field missing from a particular character is omitted rather than shown
    empty -- 544 of the 548 keys are in all 183 files, but a handful are not,
    and an input box for a line that does not exist would write a field the
    game never reads.
    """
    have = {f.path: f for f in props.parse(data)}
    groups = []
    for label, fields in FIELD_GROUPS:
        rows = []
        for field, hint, kind in fields:
            f = have.get(_path(field))
            if f is None:
                continue
            rows.append(dict(field=field, hint=hint, kind=kind,
                             value=f.raw.strip(), path=f.path))
        if rows:
            groups.append(dict(label=label, fields=rows))
    return groups


def summary(data):
    """Flat {field: value} for the curated set. For diffing against stock."""
    have = {f.path: f.raw.strip() for f in props.parse(data)}
    return {f: have[_path(f)] for f in EDITABLE if _path(f) in have}


def set_fields(data, changes):
    """Rewrite curated fields in place. `changes` is {field: new_value}.

    Refuses a field outside the curated set. That is not paternalism about
    what is safe -- it is that this function's whole contract is the names in
    FIELD_GROUPS, and silently accepting `SixNumbersRH.pistol.HolsterOffset.x`
    here would make two editors responsible for the same bytes with no shared
    validation. The Files tab edits anything.
    """
    unknown = [k for k in changes if k not in EDITABLE]
    if unknown:
        raise KeyError("not editable through the creature editor: %s"
                       % ", ".join(sorted(unknown)))
    return props.apply(data, {_path(k): str(v) for k, v in changes.items()})
