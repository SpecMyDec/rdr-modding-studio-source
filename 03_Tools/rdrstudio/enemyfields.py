"""One enemy's twenty-one fields: read them, type them, change them, add one.

The `.enm` roster is plain text and has been fully parsed for a long time; the
gap was never the format, it was that eleven of twenty-one fields had no
control and the two that decide what an enemy IS were a bare text box. This
module is the missing half.

The grammar inside an `Enemy { }` block is one field per line:

    <indent>Key<separator>value [value ...]

Whitespace is wildly inconsistent -- the same file uses tabs, four spaces,
one space and trailing tabs, sometimes inside one block -- so every operation
here works on the byte span of the VALUE and never rewrites a line it was not
asked to change. Two consequences worth stating out loud:

  * a field the user did not touch comes back byte-identical, so a one-field
    edit shows up in a diff as one line;
  * `WeaponList` arity is read from the line in front of us. The brief's real
    sample has seven values, the capability study says eight, and SHER70's own
    file has blocks with nine, one and seven. Nothing here assumes a number.

Adding a missing line is the only operation that writes whitespace of its own.
It copies the indentation and separator of the line it lands next to, so an
inserted `IsBeast` looks like it was always there.
"""

import re
from typing import Any

from . import scene

# ------------------------------------------------------------- the field table
#
# Order is frozen by the API contract: all 21, always, in this order, present
# or not. Labels say what the field DOES IN THE GAME -- "Despawns when far
# away", not "Cullable" -- because the filename is not the feature.

FIELDS = [
    # key, label, control, options_from, required, default
    ("Type",             "Character",              "pick", "cast.characters", True,  None),
    ("Table",            "Behaviour",              "pick", "cast.behaviours", True,  None),
    ("Name",             "Name",                   "text", None,             True,  None),
    ("Comment",          "Note to yourself",       "text", None,             True,  None),
    ("Alignment",        "Side it fights for",     "int",  None,             True,  None),
    ("Group",            "Wave it belongs to",     "text", None,             True,  None),
    ("PopFreq",          "Chance it appears",      "int",  None,             True,  None),
    ("BossType",         "Boss rank",              "int",  None,             True,  None),
    ("MyBoss",           "Follows this boss",      "text", None,             True,  None),
    ("Cullable",         "Despawns when far away", "bool", None,             True,  None),
    ("Pushable",         "Can be shoved",          "bool", None,             True,  None),
    ("TextureVariation", "Skin",                   "pick", "cast.skins",     False, None),
    ("PowerUp",          "Drops on death",         "pick", "cast.drops",     False, "0 0.000000"),
    ("WeaponList",       "Weapons carried",        "list", "cast.drops",     False, None),
    ("StartWeapon",      "Weapon in hand",         "int",  None,             False, -1),
    ("ThrownWeaponAmmo", "Thrown ammo",            "int",  None,             False, 0),
    ("Inventory",        "Inventory",              "text", None,             False, "none"),
    ("SpawnHorse",       "Horse it rides",         "text", None,             False, "<null>"),
    ("IsBeast",          "Is an animal",           "bool", None,             False, 0),
    ("WhichHead",        "Head variant",           "int",  None,             False, None),
    ("NeedsSound",       "Loads its own voice",    "bool", None,             False, None),
]

KEYS = [f[0] for f in FIELDS]
BY_KEY = {f[0]: f for f in FIELDS}
REQUIRED = {f[0] for f in FIELDS if f[4]}
BOOLS = {f[0] for f in FIELDS if f[2] == "bool"}
INTS = {f[0] for f in FIELDS if f[2] == "int"}

# `Spawn` is a block, not a field, and it is where an inserted line goes
# before -- keeping the scalar fields together the way every stock block has
# them.
_LINE = re.compile(rb"(?m)^([ \t]*)([A-Za-z_]\w*)([ \t]+)([^\r\n]*)")


class Line:
    """One `Key value` line inside an Enemy block, with its spans."""

    __slots__ = ("key", "indent", "sep", "raw", "val_start", "val_end",
                 "line_start", "line_end")

    def __init__(self, key, indent, sep, raw, val_start, val_end,
                 line_start, line_end):
        self.key = key
        self.indent = indent        # bytes before the key
        self.sep = sep              # bytes between key and value
        self.raw = raw              # the value text verbatim, trailing space in
        self.val_start = val_start
        self.val_end = val_end
        self.line_start = line_start
        self.line_end = line_end    # one past the line's terminator

    @property
    def value(self):
        return self.raw.strip()

    @property
    def tokens(self):
        return self.raw.split()

    def token_spans(self):
        """(text, start, end) per whitespace-separated token, in file bytes.

        `WeaponList` lines are written `0 \t0 \t0 \t...` with the separator
        varying inside one file. Rewriting the whole value would normalise
        that and change the file's length for a change that touched one
        number, so each token is spliced in its own span instead.
        """
        out = []
        i = 0
        n = len(self.raw)
        while i < n:
            while i < n and self.raw[i] in " \t":
                i += 1
            j = i
            while j < n and self.raw[j] not in " \t":
                j += 1
            if j > i:
                out.append((self.raw[i:j], self.val_start + i,
                            self.val_start + j))
            i = j
        return out


def parse_block(data, span):
    """Top-level `Key value` lines of one Enemy block -> {key: Line}.

    Depth-aware: the `Spawn 1 { Point ... Angle ... }` sub-block's contents are
    NOT fields of the enemy and must not be offered as if they were. Position
    is edited through the existing move route, which already knows how.
    """
    start, end = span
    body = data[start:end]
    lines = {}
    depth = 0
    pos = 0
    n = len(body)
    while pos < n:
        nl = body.find(b"\n", pos)
        line_end = n if nl < 0 else nl + 1
        line = body[pos:line_end]
        stripped = line.strip()

        if depth == 1 and stripped and not stripped.startswith(b";"):
            m = _LINE.match(line)
            if m and m.group(2) not in (b"Enemy",):
                key = m.group(2).decode("latin-1")
                val = m.group(4)
                # Strip the trailing CR from the captured value; `[^\r\n]*`
                # already stops before it, but a line ending in tabs keeps
                # them and the span must not swallow the terminator.
                vstart = pos + m.start(4)
                vend = pos + m.end(4)
                while vend > vstart and body[vend - 1:vend] in (b" ", b"\t"):
                    vend -= 1
                if key not in lines:
                    lines[key] = Line(
                        key, m.group(1), m.group(3),
                        body[vstart:vend].decode("latin-1"),
                        start + vstart, start + vend,
                        start + pos, start + line_end)

        depth += line.count(b"{") - line.count(b"}")
        pos = line_end
    return lines


def _fmt_bool(value, like):
    """Write a bool back in the form this file already uses.

    Some blocks say `Cullable 1`, some say `IsBeast true`. Rewriting one style
    as the other is a diff for no reason and a chance to be wrong.
    """
    truthy = coerce_bool(value)
    if truthy is None:
        # The disc has one enemy whose `Pushable` reads `1b`. It is not a
        # yes/no value and the game presumably reads the leading digit, so it
        # is passed through untouched rather than silently rewritten as `0`.
        # A user cannot introduce one: `validate` refuses anything it cannot
        # read as a bool.
        return str(value)
    if like is not None and like.strip().lower() in ("true", "false"):
        return "true" if truthy else "false"
    return "1" if truthy else "0"


def coerce_bool(value):
    s = str(value).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    return None


def field_value(key, line):
    """The typed value of a present field, ready for JSON."""
    if line is None:
        return None
    if key == "WeaponList":
        out = []
        for t in line.tokens:
            try:
                out.append(int(t))
            except ValueError:
                out.append(t)
        return out
    if key == "PowerUp":
        # `9 Six_Shooter` -> the weapon name, which is the only part with a
        # knowable legal set. `0 0.000000` -> the literal default.
        t = line.tokens
        if len(t) >= 2 and t[0] == "9":
            return t[1]
        return " ".join(t)
    if key in BOOLS:
        b = coerce_bool(line.value)
        return 1 if b else 0 if b is not None else line.value
    if key in INTS:
        try:
            return int(float(line.value))
        except ValueError:
            return line.value
    return line.value


def describe(data, span, lines=None):
    """All 21 fields of one enemy, in the frozen order, present or not."""
    lines = parse_block(data, span) if lines is None else lines
    out = []
    for key, label, control, options_from, required, default in FIELDS:
        line = lines.get(key)
        arity = 1
        if key == "WeaponList" and line is not None:
            arity = len(line.tokens)
        out.append(dict(
            key=key, label=label,
            value=field_value(key, line),
            control=control, options_from=options_from, options=None,
            editable=True, present=line is not None, required=required,
            default=default, min=None, max=None, arity=arity))
    return out


# --------------------------------------------------------------------- writing

def _insert_point(data, span, lines, key):
    """Where a missing line goes, and what it should look like.

    After the nearest earlier field that IS present, so the block keeps the
    order every stock block uses. Falling back to just before `Spawn`, then to
    just before the closing brace.
    """
    order = KEYS.index(key)
    before = None
    for k in KEYS[:order][::-1]:
        if k in lines:
            before = lines[k]
            break
    if before is not None:
        return before.line_end, before.indent, before.sep
    spawn = lines.get("Spawn")
    if spawn is not None:
        return spawn.line_start, spawn.indent, spawn.sep
    any_line = next(iter(lines.values()), None)
    indent = any_line.indent if any_line else b"\t"
    sep = any_line.sep if any_line else b"\t"
    # Just inside the closing brace of the block.
    close = data.rfind(b"}", span[0], span[1])
    line_start = data.rfind(b"\n", span[0], close) + 1
    return line_start, indent, sep


def edits_for(data, span, changes, lines=None, eol=None):
    """[(start, end, text)] that apply `changes` to one Enemy block.

    A key with a value sets it, inserting the line when absent. A key with
    None removes the line entirely. Every edit is expressed against the
    ORIGINAL bytes and applied in one splice by the caller, so no offset ever
    shifts under another edit -- staging them is what silently corrupted a
    Point line once already.
    """
    lines = parse_block(data, span) if lines is None else lines
    if eol is None:
        eol = b"\r\n" if b"\r\n" in data[span[0]:span[1]] else b"\n"
    out = []
    for key, value in changes.items():
        line = lines.get(key)
        if value is None:
            if line is not None:
                out.append((line.line_start, line.line_end, b""))
            continue
        text = render_value(key, value, line)
        if line is not None:
            # Setting a field to the value it already holds writes NOTHING.
            # Compared token by token, not character by character, so a value
            # that differs only in the file's own tab-and-space padding is
            # correctly seen as unchanged -- otherwise saving an untouched
            # field would reflow the line and change the file's length.
            if text.split() == line.tokens:
                continue
            spans = line.token_spans()
            new_tokens = text.split()
            if len(new_tokens) == len(spans) and len(spans) > 1:
                # Same shape: splice only the numbers that actually moved and
                # leave every separator exactly as the game wrote it.
                for (old, start, end), fresh in zip(spans, new_tokens):
                    if old != fresh:
                        out.append((start, end, fresh))
                continue
            out.append((line.val_start, line.val_end, text))
        else:
            at, indent, sep = _insert_point(data, span, lines, key)
            out.append((at, at, indent + key.encode("latin-1") + sep
                        + text.encode("latin-1") + eol))
    return out


def render_value(key, value, line=None):
    """The exact text to write for one field."""
    if key in BOOLS:
        return _fmt_bool(value, line.value if line else None)
    if key == "WeaponList":
        vals = value if isinstance(value, (list, tuple)) else str(value).split()
        return " ".join(str(v) for v in vals)
    if key == "PowerUp":
        s = str(value).strip()
        if s in ("", "none", "0") or s.split()[:1] == ["0"]:
            sep = "\t" if (line and "\t" in line.raw) else "  "
            return "0" + sep + "0.000000"
        sep = "\t" if (line and "\t" in line.raw) else "     "
        return "9" + sep + s
    if key in INTS:
        try:
            return str(int(float(value)))
        except (TypeError, ValueError):
            return str(value)
    return str(value)


# ------------------------------------------------------------------ validation

class Invalid(Exception):
    """(code, message, fix) for one bad field value."""

    def __init__(self, code, message, fix=""):
        Exception.__init__(self, message)
        self.code = code
        self.message = message
        self.fix = fix


def validate(changes, cast_doc, lines=None):
    """Normalise every change or refuse. NOTHING is spliced before this runs.

    The whole reason `Table` was locked was that a typo there is a crash
    rather than a visible mistake. It is unlocked here because the value is
    checked against the archive's own `.tbl` list first, which is a stronger
    guarantee than a text box could ever offer.
    """
    from . import cast as cast_mod
    out: dict[str, Any] = {}
    for key, value in changes.items():
        if key not in BY_KEY:
            raise Invalid("bad_request", "%r is not a field on an enemy." % key,
                          "Pick one of: %s." % ", ".join(KEYS))
        if value is None:
            if key in REQUIRED:
                raise Invalid(
                    "not_editable",
                    "Every enemy must have %s, so it cannot be removed."
                    % BY_KEY[key][1].lower(),
                    "Change its value instead of clearing it.")
            out[key] = None
            continue

        if key == "Type":
            r = cast_mod.resolve_type(cast_doc, value)
            if r is None:
                raise Invalid(
                    "unknown_type",
                    "There is no character called %r in this level." % value,
                    "This level carries %d: %s."
                    % (len(cast_doc["characters"]),
                       ", ".join(c["id"] for c in cast_doc["characters"])))
            out[key] = r
        elif key == "Table":
            r = cast_mod.resolve_table(cast_doc, value)
            if r is None:
                raise Invalid(
                    "unknown_table",
                    "There is no behaviour called %r in this level." % value,
                    "This level carries %d behaviour tables; pick one from "
                    "the list." % len(cast_doc["behaviours"]))
            out[key] = r
        elif key == "TextureVariation":
            r = cast_mod.resolve_skin(cast_doc, value)
            if r is None:
                raise Invalid(
                    "unknown_value",
                    "There is no skin called %r in this level." % value,
                    "A skin has to exist as a .tex file in this archive.")
            out[key] = r
        elif key == "PowerUp":
            s = str(value).strip()
            if s in ("", "none", "0") or s.split()[:1] == ["0"]:
                out[key] = "0"
            else:
                r = cast_mod.resolve_drop(cast_doc, s)
                if r is None:
                    raise Invalid(
                        "unknown_value",
                        "There is no weapon called %r to drop." % value,
                        "Drops come from the 55 weapons in swat.guns.")
                out[key] = r
        elif key == "WeaponList":
            vals = value if isinstance(value, (list, tuple)) else str(value).split()
            line = (lines or {}).get(key)
            if line is not None and len(vals) != len(line.tokens):
                raise Invalid(
                    "bad_request",
                    "This enemy's weapon list holds %d values and you sent %d."
                    % (len(line.tokens), len(vals)),
                    "Send exactly %d values -- the length is read from the "
                    "file and is not the same for every enemy."
                    % len(line.tokens))
            out[key] = list(vals)
        elif key in BOOLS:
            b = coerce_bool(value)
            if b is None:
                raise Invalid("bad_request",
                              "%s is a yes/no field and %r is neither."
                              % (BY_KEY[key][1], value),
                              "Send 0, 1, true or false.")
            out[key] = b
        elif key in INTS:
            try:
                out[key] = int(float(value))
            except (TypeError, ValueError):
                raise Invalid("bad_request",
                              "%s is a whole number and %r is not."
                              % (BY_KEY[key][1], value), "")
        else:
            s = str(value)
            if any(c in s for c in "\r\n"):
                raise Invalid("bad_request",
                              "%s cannot contain a line break." % BY_KEY[key][1],
                              "")
            if not s.strip():
                raise Invalid("bad_request",
                              "%s cannot be blank." % BY_KEY[key][1],
                              "Stock files use --- for an empty comment.")
            if " " in s.strip():
                raise Invalid(
                    "bad_request",
                    "%s is one word in this format and %r has a space in it."
                    % (BY_KEY[key][1], s),
                    "Use an underscore instead of a space.")
            out[key] = s.strip()
    return out


# -------------------------------------------------------------------- placing

MIN_REQUIRED = ("Table", "Type", "Name", "Comment", "Alignment", "Group",
                "PopFreq", "BossType", "MyBoss", "Cullable", "Pushable")


def block_size(node):
    return node.span[1] - node.span[0]


def smallest_block(nodes):
    """The cheapest Enemy block to clone. Median cost 380 B, minimum 294 B.

    Cloning the smallest is not tidiness -- `.enm` files sit in slots with a
    few hundred spare bytes, and cloning a 15,969-byte boss block instead of a
    294-byte one is the difference between a placement that fits and one that
    is refused.
    """
    return min(nodes, key=block_size) if nodes else None


def new_block(data, node, name, changes, eol=None):
    """The bytes of a new Enemy block cloned from `node` with fields replaced.

    One splice against the original block, exactly like `scene.clone_block`.
    The `Spawn` point is set through the node's own parsed spans, which is why
    the position lands in the right place even though the surrounding
    whitespace differs from block to block.
    """
    start, end = node.span
    block = data[start:end]
    if eol is None:
        eol = b"\r\n" if b"\r\n" in block else b"\n"
    lines = parse_block(data, node.span)
    todo = dict(changes)
    todo["Name"] = name
    edits = edits_for(data, node.span, todo, lines, eol)
    # Rebase file offsets onto the block, which is what gets spliced.
    edits = [(s - start, e - start, t) for s, e, t in edits]
    return scene.splice(block, edits)


def set_point(block, pos, angle):
    """Move the single `Point x y z  Angle a` of a standalone block.

    No enemy on the disc has more than one spawn point, which removes a whole
    class of edge case: there is exactly one Point to rewrite.
    """
    m = re.search(rb"Point((?:[ \t]+" + scene._NUM_RE.pattern + rb"){3})"
                  rb"(\s*Angle[ \t]+)(" + scene._NUM_RE.pattern + rb")", block)
    if not m:
        return block
    nums = scene._nums(block, m.start(1), m.end(1))
    edits = []
    for num, val in zip(nums, pos):
        edits.append((num.start, num.end, scene.fmt_num(val)))
    if angle is not None:
        edits.append((m.start(3), m.end(3), scene.fmt_num(angle)))
    return scene.splice(block, edits)


def append_block(target_data, block):
    """Edits that put a finished block at the end of an `.enm`.

    `.enm` has no count header -- enemies are just a run of `Enemy { }` blocks
    -- so nothing needs bumping. Verified: `scene.COUNT_KEYS` has no `enm`
    entry.
    """
    sep = b"\r\n\r\n" if b"\r\n" in target_data else b"\n\n"
    at = len(target_data.rstrip(b"\r\n \t"))
    return [(at, at, sep + block)]
