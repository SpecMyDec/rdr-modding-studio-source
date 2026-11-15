"""Structured reading and editing of swat.guns.

Layout:
    GunRack { Num_GenericWeapon 55 {
        Weapon {
          TypeName Rifle_Testudine
          Damage   3.500000
          ...
        }
        ...
    }}

Edits are applied by locating the weapon's block by TypeName and rewriting a
single value in place -- never by regenerating the file. That keeps the byte
delta minimal (these files sit in tight slots) and works identically across
all 63 copies of swat.guns on the disc, whose byte offsets differ.
"""

import re

_WEAPON_RE = re.compile(rb"(?m)^[ \t]*Weapon[ \t]*\r?\n?[ \t]*\{")
_NUM = rb"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?"

# The fields worth exposing as a real editor, grouped for the UI. Everything
# else is still readable, just not promoted.
FIELD_GROUPS = [
    ("Damage", [
        ("Damage", "damage per hit"),
        ("MinDamage", "damage at max range"),
        ("DisplayDamage", "stat bar shown in menus (cosmetic)"),
        ("StealthDamage", "multiplier when unseen"),
        ("ZoneDamage", "multiplier for hit zones"),
        ("Impulse", "physical knockback force"),
    ]),
    ("Ammo & rate", [
        ("FullLoad", "clip size"),
        ("StartTotalCartridges", "reserve ammo"),
        ("BulletsPerShot", "pellets per trigger pull"),
        ("CyclicRate", "shots per second"),
        ("ReloadTime", "seconds to reload"),
        ("FastReloadTime", "seconds on a perfect reload"),
        ("FireDelay", "delay before the shot leaves"),
    ]),
    ("Accuracy & reach", [
        ("Spread", "cone of fire (0 = perfect)"),
        ("Range", "metres before falloff"),
        ("MuzzleVel", "projectile speed"),
        ("MaxAccuracy", "best accuracy"),
        ("MinAccuracy", "worst accuracy"),
        ("AccuracySpread", "spread multiplier"),
        ("PatternAmplitude", "recoil pattern size"),
        ("PatternFrequency", "recoil pattern speed"),
    ]),
    ("Blast & sound", [
        ("BlastRadius", "explosion radius"),
        ("BlastDuration", "explosion lifetime"),
        ("SoundRadius", "how far AI hears it"),
        ("SoundVolume", "loudness"),
    ]),
    ("Camera", [
        ("CameraFov", "field of view when aiming"),
        ("CameraFovRate", "how fast the FOV changes"),
    ]),
    ("What kind of weapon", [
        ("DisplayWeaponType", "the class: pistol, shotgun, thrown..."),
        ("WeaponType", "how it is fired and held"),
        ("ProjectileType", "what comes out of it"),
    ]),
]

EDITABLE = {k for _, fields in FIELD_GROUPS for k, _ in fields}

# Fields that are an enum rather than a number, with every value the shipped
# disc actually uses. A tester asked for these: "I noticed I can't change the
# weapon type. That dictates if it's Single pistol, double, shotgun or
# throwable". Nothing in the writer ever blocked them -- they were simply
# missing from FIELD_GROUPS, and the panel dumps anything not listed there
# into a read-only table. The split was UI curation, not a safety rule.
#
# The COUNTS are measured across all 55 weapons in swat.guns. The LABELS are
# read off which weapons carry each value and are therefore inference, not a
# string from the disc -- no name for these values exists in any file. They
# are worth having anyway: "0 -- Pistol (14 weapons)" tells you more than "0".
#
# DisplayWeaponType is the one that answers the question. It is not the same
# field as WeaponType: Breech_Loader is WeaponType 3 with the rifles but
# DisplayWeaponType 2 with the shotguns, so WeaponType is closer to how the
# weapon is fired and DisplayWeaponType to what the game calls it.
CHOICES = {
    "DisplayWeaponType": [
        (0, "Pistol (14 weapons)"),
        (1, "Twin pistols (3)"),
        (2, "Shotgun (11)"),
        (3, "Rifle (16)"),
        (4, "Bow (1: Sacred_Bow)"),
        (5, "Knife (2)"),
        (6, "Dynamite (2)"),
        (7, "Thrown bottle (3: Bottle, Molotov, SnakeOil)"),
        (8, "Gatling gun (3)"),
    ],
    "WeaponType": [
        (0, "Pistol (14)"),
        (1, "Twin pistols (3)"),
        (2, "Sawn-off (2)"),
        (3, "Long gun (25)"),
        (4, "Bow (1)"),
        (5, "Blade (3)"),
        (6, "Dynamite (2)"),
        (7, "Thrown bottle (2)"),
        (8, "Gatling gun (3)"),
    ],
    "ProjectileType": [
        (1, "Bullet (35)"),
        (2, "Thrown blade (2)"),
        (3, "Arrow (1)"),
        (5, "Dynamite (2)"),
        (6, "Molotov (2)"),
        (7, "Shot / pellets (11)"),
        (12, "Dios_Martillo (1)"),
        (13, "Bottle (1)"),
    ],
}


def choice_label(field, value):
    """The label for a value, or a plain string for one the disc never uses.

    Values outside the list are NOT refused. Only nine DisplayWeaponType
    values appear on the shipped disc, but nothing measured says a tenth is
    impossible -- the engine reads this as a number -- and a modding tool that
    forbids what it merely has not seen is worse than one that says so.
    """
    try:
        v = int(float(value))
    except (TypeError, ValueError):
        return str(value)
    for k, label in CHOICES.get(field, ()):
        if k == v:
            return label
    return "%d -- not used by any shipped weapon" % v


class Weapon:
    __slots__ = ("index", "name", "span", "fields", "raw")

    def __init__(self, index, name, span, fields, raw):
        self.index = index
        self.name = name
        self.span = span
        self.fields = fields      # key -> {"value", "text", "numeric"}
        self.raw = raw

    def to_json(self, full=False):
        keys = self.fields if full else {
            k: v for k, v in self.fields.items() if k in EDITABLE}
        return dict(index=self.index, name=self.name,
                    fields={k: v["value"] for k, v in keys.items()},
                    model=self.fields.get("ModelName", {}).get("text"),
                    weapon_type=self.fields.get("WeaponType", {}).get("value"))


def _close_brace(data, open_idx):
    depth = 0
    i = open_idx
    while i < len(data):
        if data[i] == 0x7B:
            depth += 1
        elif data[i] == 0x7D:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("unbalanced braces at %d" % open_idx)


def parse(data):
    """Every Weapon block, with its scalar fields."""
    out = []
    for i, m in enumerate(_WEAPON_RE.finditer(data)):
        open_idx = m.end() - 1
        try:
            close = _close_brace(data, open_idx)
        except ValueError:
            continue
        body = data[open_idx + 1:close]

        fields = {}
        for fm in re.finditer(rb"(?m)^[ \t]*([A-Za-z_][\w\[\]]*)[ \t]+(.+?)[ \t]*\r?$",
                              body):
            key = fm.group(1).decode("latin-1")
            text = fm.group(2).decode("latin-1").strip()
            nums = re.findall(_NUM.decode("latin-1"), text)
            # A single bare number is an editable scalar; anything else
            # (vectors, <none>, names) stays text.
            numeric = len(nums) == 1 and nums[0] == text
            fields[key] = dict(text=text, numeric=numeric,
                               value=float(nums[0]) if numeric else text)

        name = fields.get("TypeName", {}).get("text", "weapon_%d" % i)
        out.append(Weapon(i, name, (m.start(), close + 1), fields, body))
    return out


def find(data, typename):
    for w in parse(data):
        if w.name == typename:
            return w
    return None


def _fmt(value, original_text):
    """Match the original's formatting so the byte delta stays small."""
    if isinstance(value, str):
        return value
    if "." in original_text:
        decimals = len(original_text.split(".")[-1].rstrip())
        decimals = min(max(decimals, 1), 6)
        return "%.*f" % (decimals, float(value))
    if float(value) == int(float(value)):
        return str(int(float(value)))
    return "%.6f" % float(value)


def set_fields(data, typename, changes):
    """Return `data` with a weapon's fields replaced. Byte-surgical.

    Raises KeyError if the weapon or a field is not present, so a typo fails
    loudly instead of silently editing nothing.
    """
    w = find(data, typename)
    if w is None:
        raise KeyError("no weapon named %r" % typename)

    start, end = w.span
    block = data[start:end]
    edits = []

    for key, value in changes.items():
        if key not in w.fields:
            raise KeyError("%s has no field %r" % (typename, key))
        fm = re.search(
            rb"(?m)^([ \t]*" + re.escape(key.encode("latin-1")) + rb"[ \t]+)(.+?)([ \t]*\r?)$",
            block)
        if not fm:
            raise KeyError("could not locate %s in %s" % (key, typename))
        new_text = _fmt(value, fm.group(2).decode("latin-1"))
        edits.append((fm.start(2), fm.end(2), new_text.encode("latin-1")))

    # Right-to-left so earlier spans stay valid.
    out = bytearray(block)
    for s, e, t in sorted(edits, key=lambda x: -x[0]):
        out[s:e] = t
    return data[:start] + bytes(out) + data[end:]


# --------------------------------------------------------------- new weapons
#
# The rack is read BY NAME, and that is what makes a genuinely new weapon
# possible rather than only an overwrite of one you do not use. Read out of
# the unpacked executable (`rdr.py unpack-elf`), all three read-from-code:
#
#   0x003813A8  WeaponIndexByName(name): for i in 0..*(int*)0x0079FD80-1,
#               strcmp(*(char**)0x0079FD84 + i*1040, name) -- the TypeName is
#               the first field of a 1040-byte record -- return i, else -1.
#   0x0039E4DC  the .ars arsenal loader calls exactly that function once per
#               listed name and stores the returned index. So the 100 arsenal
#               files hold NAMES on the disc; indices only exist after load.
#   0x003825E8  the rack is malloc(Num_GenericWeapon * 1040 + 16) from the
#               file's own count. There is no compiled-in ceiling of 55.
#
# So: append, never insert. Load order *is* the runtime index, so a mid-file
# insert would renumber every weapon after it -- harmless for anything that
# looks a weapon up by name, but there is no reason to find out the hard way
# whether some save-game or script holds a raw index. Appending leaves all 55
# existing indices exactly where they were.
#
# The one hard limit found: the .ars loader reads each name into a 32-byte
# stack buffer (`addiu a2, zero, 32` at 0x0039E4D8), so 31 characters.
MAX_TYPENAME = 31

_COUNT_RE = re.compile(rb"(Num_GenericWeapon[ \t]+)(\d+)")
_NAME_OK = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def check_name(name):
    """(ok, why). The `why` is a sentence for a person, not an exception."""
    if not isinstance(name, str) or not _NAME_OK.match(name):
        return False, ("A weapon name must start with a letter and use only "
                       "letters, digits and underscores.")
    if len(name) > MAX_TYPENAME:
        return False, ("%d characters is too long -- the arsenal loader reads "
                       "each name into a %d-byte buffer."
                       % (len(name), MAX_TYPENAME + 1))
    return True, ""


def clone(data, source, newname, model=None):
    """Return `data` with a copy of `source`'s Weapon block appended.

    Every asset the clone names -- model, holster model, muzzle flash, sounds,
    skeleton -- is the source's, so nothing else on the disc has to exist for
    it to load. What comes back is a file with one more weapon in it; it is
    the caller's job to check it still fits its slot.
    """
    ok, why = check_name(newname)
    if not ok:
        raise ValueError(why)

    ws = parse(data)
    if not ws:
        raise ValueError("no Weapon blocks in this file")
    by_name = {w.name: w for w in ws}
    if newname in by_name:
        raise ValueError("there is already a weapon called %r" % newname)
    src = by_name.get(source)
    if src is None:
        raise KeyError("no weapon named %r" % source)

    block = data[src.span[0]:src.span[1]]
    m = re.search(rb"(?m)^([ \t]*TypeName[ \t]+)(\S+)", block)
    if m is None:
        raise ValueError("%s has no TypeName line to rename" % source)
    block = block[:m.start(2)] + newname.encode("latin-1") + block[m.end(2):]

    if model:
        # Give the clone its OWN model file instead of the source's. The value
        # on the ModelName line is just a file stem looked up in the archive.
        mm = re.search(rb"(?m)^([ \t]*ModelName[ \t]+)(\S+)", block)
        if mm is None:
            raise ValueError("%s has no ModelName line to repoint" % source)
        block = block[:mm.start(2)] + model.encode("latin-1") + block[mm.end(2):]

    # Whatever actually separates two blocks in THIS file (b"\r\n" on the
    # retail disc), rather than a guess that would be wrong on a re-saved one.
    sep = (data[ws[-2].span[1]:ws[-1].span[0]] if len(ws) > 1 else b"\r\n")
    end = ws[-1].span[1]
    out = data[:end] + sep + block + data[end:]

    count = str(len(ws) + 1).encode("latin-1")
    return _COUNT_RE.sub(lambda mm: mm.group(1) + count, out, count=1)


# ------------------------------------------------------------------ arsenals
#
# An `.ars` is a `NumTypes N` header and N `Name flag` lines, which is the
# ordinary props grammar -- so reading one goes through props rather than a
# fourth private parser. Only the insertion is done by hand, because props
# rewrites spans and has no notion of adding a line.

def arsenal(data):
    """The weapons an arsenal grants: [{name, flag}], in file order."""
    from . import props
    rows = []
    for f in props.parse(data):
        key = f.path.split("#")[0]
        if key == "NumTypes":
            continue
        rows.append(dict(name=key, flag=f.raw.strip()))
    return rows


def arsenal_add(data, name, flag="0", first=False):
    """Grant `name` in an `.ars`. Returns `data` unchanged if it is already in.

    The flag column is 0 almost everywhere: measured on the vanilla disc, 52
    of the 100 arsenals set it on exactly one line, and 48 of those are
    Twin_Revolvers -- so it reads as "this is the dual-wield slot". A clone
    inherits 0 unless the caller says otherwise.
    """
    from . import props
    fields = props.parse(data)
    if not fields or fields[0].path.split("#")[0] != "NumTypes":
        raise ValueError("not an arsenal file (no NumTypes header)")
    if any(f.path.split("#")[0] == name for f in fields[1:]):
        return data

    count = int(fields[0].raw.split()[0])
    data = props.apply(data, {fields[0].path: str(count + 1)})

    # Re-parse: bumping 9 -> 10 moves every span after the header by one byte.
    fields = props.parse(data)
    nl = b"\r\n" if b"\r\n" in data else b"\n"
    if first:
        # Slot 0 is the weapon a character starts with in hand.
        eol = data.find(b"\n", fields[0].end)
        nl2 = b"\r\n" if b"\r\n" in data else b"\n"
        line = name.encode("latin-1") + b"\t" + str(flag).encode("latin-1") + nl2
        return data[:eol + 1] + line + data[eol + 1:]
    eol = data.find(b"\n", fields[-1].end)
    if eol < 0:
        # allweapons.ars ends `Molotov\t\t0` with no terminator at all, so
        # appending blind produced `Molotov\t\t0Peacekeeper\t0` -- one line,
        # one weapon, silently. Terminate the last line first.
        head, tail = data + nl, b""
    else:
        head, tail = data[:eol + 1], data[eol + 1:]
    line = name.encode("latin-1") + b"\t" + str(flag).encode("latin-1") + nl
    return head + line + tail


def summary(data):
    """Compact list for a picker: name, damage, clip, rate."""
    rows = []
    for w in parse(data):
        f = w.fields
        get = lambda k: f.get(k, {}).get("value")  # noqa: E731
        rows.append(dict(index=w.index, name=w.name,
                         damage=get("Damage"), clip=get("FullLoad"),
                         rate=get("CyclicRate"), range=get("Range"),
                         mp=get("CanBeUsedInMP"),
                         model=(f.get("ModelName", {}).get("text") or "").strip().strip('"')))
    return rows
