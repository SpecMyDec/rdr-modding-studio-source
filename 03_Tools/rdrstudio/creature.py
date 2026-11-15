"""Structured reading and editing of .cr creature files.

181 creatures, each a nested `Key Value` / `Block { }` text file:

    Creature {
        MoverData { Gravity -10.000000  Acceleration 20.000000 ... }
        LocoData { DefaultGaitSet { SpeedBand { RunSpeed 4.750000 ... } } }
    }

Fields are addressed by path ("MoverData.Gravity"). Edits are byte-surgical:
only the value's span is replaced, so the delta is a handful of bytes and the
file fits back into its slot.

Like swat.guns, most .cr files exist in SWAT.DAT AND in level archives, and
the level copy wins at runtime -- so a creature mod must patch every copy
(Game.patch_everywhere). The handoff's known exception: jck2.cr in GHOST.DAT
has zero slot headroom and keeps stock values.

CAUTION (from the user): the Steam Deck's Moon Gravity + Sprint mod built on
these files NEVER actually worked, despite the handoff listing it as
installed. Treat every .cr change as unproven until it survives a boot test
AND an in-game level load -- a boot test alone does not exercise creatures,
which load per level.
"""

import re

_NUM = r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?"

# The fields worth promoting into a real editor, with what they do.
FIELD_GROUPS = [
    # First, because it is the first thing anyone looks for. `find_field`
    # resolved this path all along and nothing listed it, so the Creatures tab
    # offered gravity and turn rate but not how much health the creature has.
    # Measured: all 181 `.cr` files carry MaxHitPoints. Red ships at 250.
    #
    # It is here rather than in the `.swt` editor because `.swt` -- the file
    # that holds every damage number -- has no health field at all. The two
    # halves of "how tough is this character" live in different files.
    ("Health", [
        ("HealthData.MaxHitPoints", "hit points (Red ships at 250)"),
        ("HealthData.HitDamage", "damage taken per hit, scaled"),
    ]),
    ("Movement", [
        ("MoverData.Gravity", "fall acceleration (stock -10; -3.4 = moon)"),
        ("MoverData.Acceleration", "how fast speed builds"),
        ("MoverData.Deceleration", "how fast it bleeds off"),
        ("MoverData.TurnRate", "turning speed"),
        ("MoverData.SpinRate", "quick-turn speed"),
    ]),
    ("Speed", [
        ("SpeedBand.WalkSpeed", "walking speed"),
        ("SpeedBand.RunMinSpeed", "speed where the run gait starts"),
        ("SpeedBand.RunSpeed", "top running speed (stock 4.75; 8 = sprint)"),
        ("SpeedBand.SideRunSpeed", "strafing run speed"),
        ("SpeedBand.ReverseRunSpeed", "backpedal run speed"),
    ]),
    ("Body", [
        ("MoverData.SphereRadius", "collision radius"),
        ("MoverData.SphereHeight", "collision height"),
        ("MoverData.KnockHorVelScale", "knockback distance scale"),
        ("MoverData.KnockVerVelScale", "knockback launch scale"),
    ]),
]

EDITABLE = {k for _, fields in FIELD_GROUPS for k, _ in fields}


def _find_block(data, name, start=0, end=None):
    """(open_idx, close_idx) of `name { ... }`, or None."""
    if end is None:
        end = len(data)
    m = re.search(rb"\b" + name.encode() + rb"\b\s*\{", data[start:end])
    if not m:
        return None
    open_idx = start + m.end() - 1
    depth = 0
    i = open_idx
    while i < len(data):
        c = data[i]
        if c == 0x7B:
            depth += 1
        elif c == 0x7D:
            depth -= 1
            if depth == 0:
                return open_idx, i
        i += 1
    return None


def find_field(data, path):
    """Locate `path` ("Block.Field" or "Block.Sub.Field") -> (start, end, text).

    Walks the block chain so `SpeedBand.RunSpeed` finds the one inside
    DefaultGaitSet rather than a same-named key elsewhere.
    """
    parts = path.split(".")
    lo, hi = 0, len(data)
    for block in parts[:-1]:
        span = _find_block(data, block, lo, hi)
        if span is None:
            return None
        lo, hi = span
    key = parts[-1]
    m = re.search(rb"\b" + key.encode() + rb"\b[ \t]+(" + _NUM.encode() + rb")",
                  data[lo:hi])
    if not m:
        return None
    return lo + m.start(1), lo + m.end(1), m.group(1).decode()


def read_fields(data, paths=None):
    """path -> float for every requested (or every promoted) field."""
    out = {}
    for path in (paths or sorted(EDITABLE)):
        hit = find_field(data, path)
        if hit:
            out[path] = float(hit[2])
    return out


def set_fields(data, changes):
    """Return data with the given path -> value fields replaced, surgically."""
    edits = []
    for path, value in changes.items():
        hit = find_field(data, path)
        if hit is None:
            raise KeyError("no field %r in this creature" % path)
        start, end, old = hit
        decimals = len(old.split(".")[-1]) if "." in old else 6
        text = "%.*f" % (min(max(decimals, 1), 6), float(value))
        edits.append((start, end, text.encode()))
    out = bytearray(data)
    for s, e, t in sorted(edits, key=lambda x: -x[0]):
        out[s:e] = t
    return bytes(out)


def list_creatures(game):
    """Every .cr in SWAT.DAT with its headline stats."""
    d = game.archive("SWAT.DAT")
    out = []
    for e in d:
        if e.ext != "cr":
            continue
        try:
            data = d.read(e)
            f = read_fields(data, ["MoverData.Gravity", "SpeedBand.RunSpeed",
                                   "MoverData.Acceleration"])
        except Exception:
            f = {}
        out.append(dict(name=e.name, gravity=f.get("MoverData.Gravity"),
                        run=f.get("SpeedBand.RunSpeed"),
                        accel=f.get("MoverData.Acceleration")))
    out.sort(key=lambda c: c["name"])
    return out
