"""The rest of the per-character file family: .dam .sphs .ars .hot .tail .acc
.armr .rep

`creature.py` reads a character's `.cr` and `swt.py` its `.swt`. Those two are
the numbers. These eight are the body: what each limb takes, where its
collision spheres sit, what it can carry, which animation clip stands in for
which slot. Every one of them is plain text (measured: 0 non-printable bytes
across all 939 files in SWAT.DAT) and every one is edited the same way this
codebase always edits text -- rewrite the value's byte span, leave the keys,
the tabs and the line endings exactly where the game's exporter put them.

WHY NOT `props.py`. Not all eight -- props is checked first, as the house
rule says, and for three of them it is genuinely fine. Measured, fields props
reaches against values this module addresses:

    .dam     3,552 vs  21,064     props cannot reach a cell
    .sphs    4,577 vs   5,771     0 props fields carry a block path
    .hot    19,342 vs  47,880     0 of the 38,304 matrix numbers
    .ars       646 vs     546     props reads it; cannot restructure it
    .tail      569 vs     648     props is FINE; components are the gain
    .acc        71 vs      57     props is FINE
    .armr      147 vs     137     props is FINE
    .rep   350,929 vs 350,545     props reads it; it is a list, not a form

The three real failures:

  * `.dam` has no keys at all. It is bare tables whose first token is a limb
    name, so props reads `body 0.6 0.3 0.3 0.1` as ONE field called `body`
    holding four numbers -- eight fields per file, no columns, no way to name
    a single cell. 3,552 fields where there are 21,064 numbers.
  * `.sphs` writes the block NAME on the same line as the block keyword
    (`BoundSphere<TAB>foot_l`), so props reads that line as a FIELD and the
    `{` beneath it opens an ANONYMOUS block. Measured: across all 199 files,
    ZERO props fields carry a block path -- every file flattens to `parent`,
    `radius#2`, `center#3`, and which sphere `radius#2` belongs to depends on
    the order the exporter happened to write them in, which varies (four
    orderings on the disc). The 9 files that also put the brace on that line
    (`BoundSphere fist_l {`) are not a special case; they are the same case.
  * `.hot`'s 4x3 matrix is twelve numbers under a bare `matrix` line, and
    props needs a leading letter to see a line at all. It reaches 6 things per
    spot and none of the twelve, in all 3,192 spots.

For `.tail`, `.acc` and `.armr` props parses correctly and this module's gain
is narrower: per-COMPONENT spans, so `Gravity 0.0 -9.72 0.0` can have its y
rewritten alone at the same width, and a form that knows which of the nine
`.armr` keys a given block actually has. For `.ars` and `.rep` props reads the
lines but cannot do the work -- `.ars` needs rows added and removed with
`NumTypes` recomputed, and `.rep` is up to 1,902 rows that want a filter.

WHAT IS NOT KNOWN, said out loud because the panel shows these numbers and a
label is a claim. `.dam`'s COLUMNS have no names anywhere on the disc; the
rows are limbs and that is fact, the columns are numbered 1..N and the
measured value set is printed beside them. `.ars`'s 0/1 column is 0 in 492 of
the 546 rows and what it selects is not established. Neither is guessed at
here.

HEADROOM IS THE WHOLE PROBLEM, and it is worse here than anywhere else in the
product. Measured on `01_Vanilla\\RDR_VANILLA.iso`, SWAT.DAT copy, slot minus
stored size:

    .sphs   min 0   median     1   18 files with ZERO
    .acc    min 4   median     7
    .ars    min 2   median     7
    .tail   min 0   median    11    3 files with zero
    .hot    min 0   median    22    3 files with zero
    .dam    min 0   median    25    1 file with zero
    .armr   min 4   median    26
    .rep    min 21  median 1,713

A median of ONE byte means a same-length edit is not a nicety, it is the only
kind that reliably lands -- and even then it is not guaranteed, because these
entries are deflated and a same-length text change can still compress a byte
worse. So `set_fields` renders a new number with the SAME number of decimal
places the old one had (`0.150000` -> `0.550000`, never `0.55`), which is what
`creature.set_fields` already does and for the same reason. The plan the user
approves is computed from the real compressed size either way; nothing here
guesses.

Copies. Measured: 487 of these files exist in more than one archive and NOT
ONE pair differs -- every copy of a given name is byte-identical across the
disc. So "apply the same change everywhere" is well defined, and
`Studio.edit_creature` already does it as one undo step.
"""

import re

# ---------------------------------------------------------------- primitives

_NUM_TOKEN = re.compile(r"^[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$")


class Field:
    """One value with its byte span, and the spans of its whitespace-separated
    components. `center 0.0 -0.1 0.0` is one Field with three parts."""

    __slots__ = ("key", "raw", "start", "end", "parts")

    def __init__(self, key, raw, start, end):
        self.key = key
        self.raw = raw
        self.start = start
        self.end = end
        self.parts = []          # [(start, end, text)]
        for m in re.finditer(r"\S+", raw):
            self.parts.append((start + m.start(), start + m.end(), m.group(0)))


class Block:
    """`Head name { ... }` -- one bounding sphere, hot spot, tail node, armour
    piece or accessory."""

    __slots__ = ("head", "name", "index", "fields", "start", "end", "body")

    def __init__(self, head, name, index, start, end, body):
        self.head = head
        self.name = name
        self.index = index
        self.start = start       # byte offset of the head keyword
        self.end = end           # byte offset just past the closing brace
        self.body = body         # (start, end) of the brace interior
        self.fields = {}         # key -> Field, FIRST occurrence wins

    def get(self, key):
        return self.fields.get(key)


def _decode(data):
    return data.decode("latin-1") if isinstance(data, bytes) else data


def _close_brace(text, open_idx):
    depth = 0
    for i in range(open_idx, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
    return len(text) - 1


# A key line inside a block. The trailing `[ \t]*\r?$` is deliberate: this disc
# is CRLF in seven of these eight extensions and LF in `.hot` (measured, 190 of
# 190), and a pattern anchored with a bare `$` silently loses the last value
# token on every CRLF line.
_KEY_LINE = re.compile(r"(?m)^[ \t]*([A-Za-z_]\w*)[ \t]+([^\r\n]*?)[ \t]*\r?$")
_BARE_LINE = re.compile(r"(?m)^[ \t]*([A-Za-z_]\w*)[ \t]*\r?$")


def lines(text, start=0, end=None):
    """(start, end, text-without-its-line-ending) for each line in a span.

    Hand-rolled rather than `(?m)^[^\\r\\n]*$`, which looks equivalent and is
    not: on CRLF `$` matches only immediately before the `\\n`, and `[^\\r\\n]*`
    cannot cross the `\\r` to reach it, so that pattern yields nothing but
    empty matches on a CRLF file. It cost a silently empty `.dam` reader --
    216 files that parsed without error into zero tables.
    """
    end = len(text) if end is None else end
    pos = start
    while pos < end:
        nl = text.find("\n", pos, end)
        stop = end if nl < 0 else nl
        yield pos, stop, text[pos:stop].rstrip("\r")
        pos = stop + 1


def scan_blocks(text, heads):
    """Every `Head [name] { ... }` in `text`, in file order.

    Tolerates both brace styles in the same file, because the disc does:
    `frj.sphs` writes `BoundHotdog<TAB>pusher_main` with the brace on the next
    line and `BoundSphere fist_l {` with it on the same one. Nine `.sphs` files
    mix the two.

    Blocks are keyed by INDEX, never by name. Measured: 9 of 190 `.hot` files
    repeat a spot name -- `blk.hot` has two `hot04`, two `hot03` and two
    `hot02` (the left/right pair of each) and one spot called
    `red_cof_shield`. A name-keyed editor would write the left hip's radius
    into the right hip and report success.
    """
    heads = tuple(heads)
    pat = re.compile(r"(?m)^[ \t]*(%s)(?:[ \t]+([^\s{]+))?[ \t]*(\{)?[ \t]*\r?$"
                     % "|".join(re.escape(h) for h in heads))
    out = []
    for m in pat.finditer(text):
        if m.group(3):
            open_idx = m.end(3) - 1
        else:
            open_idx = text.find("{", m.end())
            if open_idx < 0:
                continue
            # A brace that far away belongs to something else, not to us.
            if text[m.end():open_idx].strip():
                continue
        close = _close_brace(text, open_idx)
        b = Block(m.group(1), m.group(2) or "", len(out),
                  m.start(1), close + 1, (open_idx + 1, close))
        for km in _KEY_LINE.finditer(text, open_idx + 1, close):
            key = km.group(1)
            if key in b.fields:
                continue
            b.fields[key] = Field(key, km.group(2), km.start(2), km.end(2))
        out.append(b)
    return out


def top_field(text, key):
    """A `Key value` line outside any block -- `NumTypes`, `NumBoundSpheres`."""
    m = re.search(r"(?m)^[ \t]*%s[ \t]+([^\r\n]*?)[ \t]*\r?$" % re.escape(key),
                  text)
    return Field(key, m.group(1), m.start(1), m.end(1)) if m else None


def splice(text, edits):
    """Apply [(start, end, new_text)] back-to-front and return bytes.

    Back-to-front so every span computed against the original stays valid --
    the same reason `props.apply` does it, and the reason an editor built on
    this cannot corrupt a file by changing two fields at once.
    """
    out = list(text)
    for start, end, new in sorted(edits, key=lambda e: -e[0]):
        out[start:end] = list(new)
    return "".join(out).encode("latin-1")


def _eol_after(text, pos):
    """Offset just past the line terminator at or after `pos`, if there is one.

    Returns `pos` unchanged when the file simply ends there -- 39 of the 100
    `.ars` files have no newline on their last line.
    """
    if pos < len(text) and text[pos] == "\r":
        pos += 1
    if pos < len(text) and text[pos] == "\n":
        pos += 1
    return pos


def render_number(old, value):
    """Format `value` at the decimal width `old` was written with.

    `0.150000` -> `0.550000`, not `0.55`. With a median of one byte of slot
    headroom on `.sphs`, matching the original width is the difference between
    an edit that lands and one the plan refuses.
    """
    s = str(old).strip()
    try:
        v = float(value)
    except (TypeError, ValueError):
        # Only where the file itself holds a number. Measured through the live
        # route before this was here: `0/body/0 = "abc"` was accepted and wrote
        # `body abc 0.3 0.3 0.1` to the disc, plan green, because the fallback
        # was `str(value)`. The four call sites are all bare numeric grids --
        # a .dam cell, a .hot matrix cell, a vec3 component, a scalar whose
        # current value is a number token -- so there is no legitimate caller
        # asking to put a word in one of them. `-` and a bare `.` are the
        # usual half-typed states, which is why this is a refusal and not a
        # silent no-op: the panel must be able to say what it would not write.
        if _NUM_TOKEN.match(s):
            raise Unknown("%r is not a number; this field holds %s" % (value, s))
        return str(value)
    if "e" in s.lower():
        return repr(v)
    if "." in s:
        return "%.*f" % (len(s.split(".", 1)[1]), v)
    return "%d" % int(round(v)) if float(v).is_integer() else repr(v)


def _row(path, label, value, control="float", hint="", options=None):
    d = dict(path=path, label=label, value=value, control=control, hint=hint)
    if options is not None:
        d["options"] = options
    return d


class Unknown(KeyError):
    """A change names something this file does not have. Never a crash.

    Raised by every setter and turned into a `not_editable` refusal by the
    route, so a stale panel asking for a field that is no longer there gets a
    sentence rather than a 500.
    """

    # `KeyError.__str__` is `repr(args[0])`, not the message -- so the route's
    # `Refusal("not_editable", str(ex), ...)` shipped the sentence to the user
    # inside quotes: "'angle0 and angle1 are 0.0 in all 3,192 hot spots...'",
    # and the one that names a path came out double-quoted around a
    # single-quoted path. Measured through the live route before this was here.
    def __str__(self):
        return str(self.args[0]) if self.args else ""


# ------------------------------------------------------------------- .dam
#
# Three tables at most, no keys, no braces. Measured across all 216 files:
#
#   table 1   8 rows (body head Rfoot Rleg Rarm Lfoot Lleg Larm)
#             4 values in 209 files, 3 in 7 -- and those 7 are not characters,
#             they are accessories (`ctn_hair`, `gdn_scarf_root`,
#             `nrd_coat_01`, `_red`, `_mrk`...). Values come from a set of
#             seven: 0.0 0.1 0.2 0.3 0.4 0.6 1.0.
#   table 2   8 rows, ALWAYS 8 values, and every value on the disc is 0, 1 or 2.
#   table 3   present in 12 files only, introduced by a bare `StunTable` line,
#             8 rows of 4. Values 0.0 - 20.0.
#
# The brief this was built from said "THREE fixed 8-row tables" in every file.
# That is not what is on the disc: 204 of 216 have two.
#
# What the COLUMNS mean is not known and is not guessed at here. The row keys
# are limbs and that is stated as fact; the columns are numbered and the
# measured value set is shown beside them, which is everything that can be
# said honestly.

DAM_LIMBS = ("body", "head", "Rfoot", "Rleg", "Rarm", "Lfoot", "Lleg", "Larm")

_DAM_TABLES = [
    ("Multipliers", "float",
     "8 limbs x %d. Every value on the disc is one of "
     "0.0 0.1 0.2 0.3 0.4 0.6 1.0."),
    ("Codes", "int",
     "8 limbs x 8. Every value on the disc is 0, 1 or 2."),
    ("StunTable", "float",
     "8 limbs x 4, and only 12 of the 216 files have it: blk bty dar frj frk "
     "gay mrk mst pgj sfv tck ugl. Values run 0.0 to 20.0."),
]


def _dam_tables(text):
    """[(header_label_or_None, [(limb, [(start, end, text), ...])])]"""
    tables, rows, header = [], [], None
    for lo, _hi, line in lines(text):
        if not line.strip():
            if rows:
                tables.append((header, rows))
                rows, header = [], None
            continue
        toks = list(re.finditer(r"\S+", line))
        if len(toks) == 1:
            # A bare word between tables names the table that follows it.
            header = toks[0].group(0)
            continue
        cells = [(lo + t.start(), lo + t.end(), t.group(0)) for t in toks[1:]]
        rows.append((toks[0].group(0), cells))
    if rows:
        tables.append((header, rows))
    return tables


def dam_read(text):
    tables = []
    for i, (header, rows) in enumerate(_dam_tables(text)):
        label, control, note = _DAM_TABLES[min(i, 2)]
        width = max(len(c) for _, c in rows)
        tables.append(dict(
            index=i, label=header or label, control=control,
            note=(note % width) if "%d" in note else note,
            columns=[str(c + 1) for c in range(width)],
            rows=[dict(label=limb,
                       cells=[dict(path="%d/%s/%d" % (i, limb, j), value=t)
                              for j, (_, _, t) in enumerate(cells)])
                  for limb, cells in rows]))
    return dict(layout="grid", tables=tables)


def dam_set(text, changes):
    cells = {}
    for i, (_h, rows) in enumerate(_dam_tables(text)):
        for limb, row in rows:
            for j, span in enumerate(row):
                cells["%d/%s/%d" % (i, limb, j)] = span
    edits = []
    for path, value in changes.items():
        if path not in cells:
            raise Unknown("no cell %r in this .dam" % path)
        start, end, old = cells[path]
        edits.append((start, end, render_number(old, value)))
    return edits


# ------------------------------------------------------------------- .sphs
#
# Measured across all 199: NumBoundSpheres is 5 in every one, and the five are
# always the same five -- four BoundSphere (foot_l foot_r fist_r fist_l) and
# one BoundHotdog (pusher_main). Only the ORDER varies (four orderings seen).
# Every one of the 995 shapes carries parent/radius/center; the hotdog adds
# length and rotate.
#
# 635 of 635 `parent` values resolve to a real bone in the same-stem `.skel`
# -- but only 127 of the 199 characters HAVE a same-stem `.skel` anywhere on
# the disc. The other 72 (every `m`-prefixed multiplayer skin, `chr1`-`chr4`,
# `dog`, `jck2`...) have none, so for those the parent is free text and the
# UI says so rather than offering a list it cannot vouch for.

SPHS_HEADS = ("BoundSphere", "BoundHotdog")

_SPHS_KEYS = [
    ("parent", "bone", "the bone this shape rides on"),
    ("radius", "float", "sphere radius in metres"),
    ("length", "float", "capsule length, hotdog only"),
    ("center", "vec3", "offset from the bone"),
    ("rotate", "vec3", "capsule orientation, hotdog only"),
]


def _shape_rows(blocks, keyspec, bones):
    sections = []
    for b in blocks:
        rows = []
        for key, kind, hint in keyspec:
            f = b.get(key)
            if f is None:
                continue
            base = "%d/%s" % (b.index, key)
            if kind == "vec3":
                for j, (_s, _e, t) in enumerate(f.parts):
                    rows.append(_row("%s/%d" % (base, j), "%s %s" % (key, "xyz"[j:j + 1] or j),
                                     t, "float", hint if j == 0 else ""))
            elif kind == "bone":
                rows.append(_row(base, key, f.raw.strip(),
                                 "pick" if bones else "text", hint,
                                 options=sorted(bones) if bones else None))
            else:
                rows.append(_row(base, key, f.raw.strip(), kind, hint))
        sections.append(dict(label=b.name or "#%d" % (b.index + 1),
                             kind=b.head, index=b.index, rows=rows))
    return sections


def sphs_read(text, bones=None):
    blocks = scan_blocks(text, SPHS_HEADS)
    n = top_field(text, "NumBoundSpheres")
    return dict(layout="form",
                sections=_shape_rows(blocks, _SPHS_KEYS, bones or set()),
                counter=dict(key="NumBoundSpheres",
                             declared=n.raw.strip() if n else None,
                             actual=len(blocks)))


def _block_set(text, heads, changes, vector_keys=()):
    blocks = {b.index: b for b in scan_blocks(text, heads)}
    edits = []
    for path, value in changes.items():
        bits = path.split("/")
        try:
            b = blocks[int(bits[0])]
        except (ValueError, KeyError):
            raise Unknown("no shape %r in this file" % bits[0])
        f = b.get(bits[1]) if len(bits) > 1 else None
        if f is None:
            raise Unknown("%s has no %s" % (b.name or bits[0],
                                            bits[1] if len(bits) > 1 else "?"))
        if len(bits) == 3:
            try:
                start, end, old = f.parts[int(bits[2])]
            except (ValueError, IndexError):
                raise Unknown("%s has no component %s" % (path, bits[2]))
            edits.append((start, end, render_number(old, value)))
        elif _NUM_TOKEN.match(f.raw.strip()) and bits[1] not in vector_keys:
            edits.append((f.start, f.end, render_number(f.raw, value)))
        else:
            edits.append((f.start, f.end, str(value).strip()))
    return edits


def sphs_set(text, changes):
    return _block_set(text, SPHS_HEADS, changes, ("center", "rotate"))


# ------------------------------------------------------------------- .hot
#
# 190 files, 3,192 spots, NumHotSpots 4-21 and it agrees with the block count
# in every file. Every spot has exactly the same six entries:
# parent radius height angle0 angle1 matrix.
#
# angle0 and angle1 are `0.0` in all 3,192 spots on the disc, so they are read
# and shown as fixed rather than offered as inputs -- an editor that lets you
# set a field the game has never once been observed to use is offering a guess
# dressed as a control.
#
# `matrix` is a bare line followed by four rows of three numbers: a 3x3
# rotation and then a translation. props cannot see any of it (no key), which
# is why 15,960 of 38,494 `.hot` content lines were invisible to every editor
# built on props.

HOT_HEADS = ("HotSpot",)

_HOT_KEYS = [
    ("parent", "bone", "the bone this spot rides on"),
    ("radius", "float", "capsule radius"),
    ("height", "float", "capsule height"),
]

_HOT_MATRIX_LABELS = ("right", "up", "forward", "offset")


def _hot_matrix(text, b):
    """The twelve numbers under the bare `matrix` line, with their spans."""
    m = _BARE_LINE.search(text, b.body[0], b.body[1])
    while m and m.group(1) != "matrix":
        m = _BARE_LINE.search(text, m.end(), b.body[1])
    if not m:
        return []
    rows = []
    for lo, _hi, line in lines(text, m.end() + 1, b.body[1]):
        if len(rows) == 4:
            break
        toks = list(re.finditer(r"\S+", line))
        if not toks:
            continue
        if not all(_NUM_TOKEN.match(t.group(0)) for t in toks):
            break
        rows.append([(lo + t.start(), lo + t.end(), t.group(0)) for t in toks])
    return rows


def hot_read(text, bones=None):
    blocks = scan_blocks(text, HOT_HEADS)
    sections = _shape_rows(blocks, _HOT_KEYS, bones or set())
    for b, sec in zip(blocks, sections):
        for r, cells in enumerate(_hot_matrix(text, b)):
            for c, (_s, _e, t) in enumerate(cells):
                sec["rows"].append(_row(
                    "%d/matrix/%d/%d" % (b.index, r, c),
                    "%s %s" % (_HOT_MATRIX_LABELS[r] if r < 4 else r,
                               "xyz"[c:c + 1] or c), t, "float",
                    "3x3 rotation then the offset" if r == 0 and c == 0 else ""))
        a0, a1 = b.get("angle0"), b.get("angle1")
        sec["fixed"] = ["angle0 %s, angle1 %s -- 0.0 in all 3,192 spots on the "
                        "disc, so they are shown but not offered"
                        % (a0.raw.strip() if a0 else "?",
                           a1.raw.strip() if a1 else "?")]
    n = top_field(text, "NumHotSpots")
    return dict(layout="form", sections=sections,
                counter=dict(key="NumHotSpots",
                             declared=n.raw.strip() if n else None,
                             actual=len(blocks)))


def hot_set(text, changes):
    blocks = {b.index: b for b in scan_blocks(text, HOT_HEADS)}
    plain, edits = {}, []
    for path, value in changes.items():
        bits = path.split("/")
        if len(bits) == 4 and bits[1] == "matrix":
            try:
                b = blocks[int(bits[0])]
                start, end, old = _hot_matrix(text, b)[int(bits[2])][int(bits[3])]
            except (ValueError, KeyError, IndexError):
                raise Unknown("no matrix cell %r in this .hot" % path)
            edits.append((start, end, render_number(old, value)))
        elif len(bits) >= 2 and bits[1] in ("angle0", "angle1"):
            raise Unknown(
                "angle0 and angle1 are 0.0 in all 3,192 hot spots on the disc; "
                "this editor does not write them")
        else:
            plain[path] = value
    return edits + _block_set(text, HOT_HEADS, plain)


# ------------------------------------------------------------------- .tail
#
# 17 files, 72 nodes, and every node has exactly the same seven knobs. The
# container is looser than the brief suggested: `NumTails` is absent from 6 of
# the 17 and `wlf.tail` has no `TailDir` either, so the chains are found by
# walking the `NumNodes` lines rather than by trusting a declared count.
# Gravity is a vector; the other six are scalars.

TAIL_KEYS = [
    ("Length", "float", "segment length"),
    ("Gravity", "vec3", "gravity on this node (stock about -9.7 y)"),
    ("Limit", "float", "how far it may swing, radians (3.141593 everywhere)"),
    ("Drag", "float", "air resistance (1.0 everywhere)"),
    ("OmegaDamping", "float", "spin damping (0.0 everywhere)"),
    ("Rigidity", "float", "stiffness (0.0 everywhere)"),
    ("Mass", "float", "node mass (about 6)"),
]

_TAIL_NODE = re.compile(r"(?m)^[ \t]*(\w+)[ \t]*\{")


def _tail_blocks(text):
    return scan_blocks(text, [m.group(1) for m in _TAIL_NODE.finditer(text)]
                       or ["__none__"])


def tail_read(text):
    blocks = _tail_blocks(text)
    # A chain starts at each `NumNodes` line; nodes after it belong to it.
    marks = [(m.start(), m.group(1))
             for m in re.finditer(r"(?m)^[ \t]*NumNodes[ \t]+(\d+)", text)]
    sections = []
    for b in blocks:
        chain = 0
        for i, (pos, _n) in enumerate(marks):
            if pos < b.start:
                chain = i
        rows = []
        for key, kind, hint in TAIL_KEYS:
            f = b.get(key)
            if f is None:
                continue
            if kind == "vec3":
                for j, (_s, _e, t) in enumerate(f.parts):
                    rows.append(_row("%d/%s/%d" % (b.index, key, j),
                                     "%s %s" % (key, "xyz"[j]), t, "float",
                                     hint if j == 0 else ""))
            else:
                rows.append(_row("%d/%s" % (b.index, key), key,
                                 f.raw.strip(), kind, hint))
        sections.append(dict(label=b.name, kind="chain %d" % (chain + 1),
                             index=b.index, rows=rows))
    return dict(layout="form", sections=sections,
                counter=dict(key="NumNodes",
                             declared=", ".join(n for _, n in marks) or None,
                             actual=len(blocks)))


def tail_set(text, changes):
    heads = [m.group(1) for m in _TAIL_NODE.finditer(text)] or ["__none__"]
    return _block_set(text, heads, changes, ("Gravity",))


# ------------------------------------------------------------------- .acc
#
# 14 files, NumAccessories 1 in every one. Four keys, plus DefaultAnim in
# exactly one file (`gdn.acc` -- measured by scanning all 14; the brief said
# `wlf.acc` and the disc says otherwise). RepTable names a `.rep`; SkelInsert
# is the index of the bone in the host skeleton the accessory hangs off.

ACC_HEADS = ("Accessory",)

_ACC_KEYS = [
    ("Name", "text", "what this accessory is called"),
    ("SkelInsert", "int", "index of the host bone it attaches to"),
    ("RepTable", "text", "the .rep that remaps its animation slots"),
    ("NumBones", "int", "bones in the accessory's own skeleton"),
    ("DefaultAnim", "text", "idle clip (only gdn.acc has this)"),
]


def acc_read(text):
    blocks = scan_blocks(text, ACC_HEADS)
    n = top_field(text, "NumAccessories")
    return dict(layout="form",
                sections=_shape_rows(blocks, _ACC_KEYS, set()),
                counter=dict(key="NumAccessories",
                             declared=n.raw.strip() if n else None,
                             actual=len(blocks)))


def acc_set(text, changes):
    return _block_set(text, ACC_HEADS, changes)


# ------------------------------------------------------------------- .armr
#
# 11 files, 18 Armor blocks. Six keys are in all 18; Material is in 11, and
# Offset/EulerOffset in 3. NumArmor agrees with the block count everywhere.

ARMR_HEADS = ("Armor",)

_ARMR_KEYS = [
    ("Name", "text", "what this piece is called"),
    ("AllowsAutoDodge", "bool", "0 or 1"),
    ("AlwaysActive", "bool", "0 or 1"),
    ("NumDamageStages", "int", "how many hits it takes"),
    ("DamagePerStageMin", "float", "-1.0 means no threshold"),
    ("DamagePerStageMax", "float", "-1.0 means no threshold"),
    ("Material", "text", "physics material, e.g. cr_wood_armor"),
    ("Offset", "vec3", "where it sits, only 3 blocks have it"),
    ("EulerOffset", "vec3", "how it is turned, only 3 blocks have it"),
]


def armr_read(text):
    blocks = scan_blocks(text, ARMR_HEADS)
    n = top_field(text, "NumArmor")
    return dict(layout="form",
                sections=_shape_rows(blocks, _ARMR_KEYS, set()),
                counter=dict(key="NumArmor",
                             declared=n.raw.strip() if n else None,
                             actual=len(blocks)))


def armr_set(text, changes):
    return _block_set(text, ARMR_HEADS, changes, ("Offset", "EulerOffset"))


# ------------------------------------------------------------------- .ars
#
# `NumTypes N` then N lines of `<WeaponName><whitespace><0|1>`. Measured:
# NumTypes equals the row count in all 100 files, every one of the 36 distinct
# names is a real `TypeName` in `swat.guns`, and 19 of the 55 TypeNames are
# never used by any arsenal (mostly the `_u` upgraded variants).
#
# The flag is 0 in 492 rows and 1 in 54. What it selects is not established
# and is not labelled here as though it were.
#
# This is the one format in the family where an edit is NOT a same-width value
# rewrite: adding a weapon adds a line and can push NumTypes from 9 to 10.
# Median `.ars` headroom is 7 bytes, so that will sometimes be refused, with
# the byte counts, which is the correct outcome.

_ARS_ROW = re.compile(r"(?m)^[ \t]*(\S+)([ \t]+)([01])[ \t]*\r?$")


def _ars_rows(text):
    """Weapon rows only -- `NumTypes 1` is shaped exactly like one.

    18 of the 100 files carry a single weapon, so their header reads
    `NumTypes 1` and matches `<name> <0|1>` perfectly. Without this the reader
    counted two weapons where there is one, and a save that dropped the
    phantom would have deleted the header line.
    """
    return [m for m in _ARS_ROW.finditer(text) if m.group(1) != "NumTypes"]


def ars_read(text, catalogue=()):
    rows = _ars_rows(text)
    have = {m.group(1): m.group(3) for m in rows}
    order = [m.group(1) for m in rows]
    extra = [w for w in catalogue if w not in have]
    n = top_field(text, "NumTypes")
    return dict(layout="checklist",
                items=[dict(name=w, on=True, flag=have[w],
                            in_catalogue=w in catalogue) for w in order]
                      + [dict(name=w, on=False, flag="0", in_catalogue=True)
                         for w in sorted(extra)],
                counter=dict(key="NumTypes",
                             declared=n.raw.strip() if n else None,
                             actual=len(rows)),
                note="Names come from swat.guns TypeName. The 0/1 column is "
                     "0 in 492 of the 546 rows on the disc; what it selects "
                     "is not established.")


def ars_write(text, want):
    """Rebuild the weapon list. `want` is {name: "0"|"1"}; absent names are
    dropped, new ones appended, `NumTypes` recomputed.

    The one editor in this family whose edits are not all same-width value
    rewrites: a dropped weapon deletes a whole line and an added one inserts
    one, so the file's length genuinely moves. It still comes back as a span
    edit list, applied back-to-front by `splice` like everything else, and the
    caller still checks the result against every slot -- with a median of 7
    spare bytes, adding a weapon is refused about as often as it lands.
    """
    rows = _ars_rows(text)
    eol = "\r\n" if "\r\n" in text else "\n"
    edits = []
    keep = 0
    for m in rows:
        name = m.group(1)
        if name in want:
            keep += 1
            if want[name] != m.group(3):
                edits.append((m.start(3), m.end(3), want[name]))
        else:
            # Take exactly ONE line ending with the line. Consuming every
            # `\r\n` that follows would also eat the trailing blank line that
            # 35 of the 100 files end with, which is a change nobody asked for.
            edits.append((m.start(), _eol_after(text, m.end()), ""))

    added = [w for w in want if not any(m.group(1) == w for m in rows)]
    if added:
        # `m.end()` sits BETWEEN the `\r` and the `\n` -- the row pattern ends
        # `\r?$`, and `$` matches before the `\n`, so `\r?` must have eaten the
        # `\r` to get there. Appending at that offset spliced the new weapon
        # into the middle of the previous line's terminator and produced
        # `gatlinggun_01  0\rWidowmaker\t1`, one line where there should be
        # two. Step past the whole terminator instead.
        if rows:
            tail = _eol_after(text, rows[-1].end())
            pad = "" if tail > rows[-1].end() else eol   # no newline at EOF
        else:
            tail, pad = len(text), (eol if text and text[-1] not in "\r\n" else "")
        edits.append((tail, tail, pad + eol.join(
            "%s\t%s" % (w, want[w]) for w in added) + eol))
        keep += len(added)

    n = top_field(text, "NumTypes")
    if n is not None and n.raw.strip() != str(keep):
        edits.append((n.start, n.end, str(keep)))
    return edits


# ------------------------------------------------------------------- .rep
#
# `type: a`, `NumReplacements 3500`, then one `slot<TAB>clip` row per line.
#
# NumReplacements is 3500 in ALL 192 files while the actual row count runs
# 697 to 1,902 (measured; 350,545 rows in total). It is a table capacity, not
# a row count, so editing a row never touches it.
#
# The right-hand side is an animation clip. Measured over all 192 files:
# 349,049 of 350,545 right-hand values name a real `.anim` on the disc. The
# 1,496 that do not are the game's own dangling references
# (`red_rev_for_gup`, `red_snk_dpt_rgt_hld`, ...), so an unresolved name is
# flagged and never refused -- refusing them would refuse the stock disc.
#
# All 4,187 `.anim` stems live in SWAT.DAT, so the roster costs one archive
# walk (0.02 s) rather than a 66-archive sweep (0.80 s) for the same answer.

_REP_ROW = re.compile(r"(?m)^[ \t]*(\S+)[ \t]+([^\r\n]*?)[ \t]*\r?$")


def _rep_rows(text):
    out = []
    for m in _REP_ROW.finditer(text):
        if m.group(1) in ("type:", "NumReplacements"):
            continue
        out.append(m)
    return out


def rep_read(text, anims=None):
    rows = []
    unresolved = 0
    for m in _rep_rows(text):
        clip = m.group(2).strip()
        ok = anims is None or clip.lower() in anims
        if not ok:
            unresolved += 1
        rows.append(dict(path=m.group(1), slot=m.group(1), clip=clip, ok=ok))
    n = top_field(text, "NumReplacements")
    return dict(layout="table", rows=rows, unresolved=unresolved,
                checked=anims is not None,
                counter=dict(key="NumReplacements",
                             declared=n.raw.strip() if n else None,
                             actual=len(rows)),
                note="NumReplacements is 3500 in all 192 files while the real "
                     "row count runs 697-1,902, so it is a capacity and an "
                     "edit never touches it.")


def rep_set(text, changes):
    by_slot = {m.group(1): m for m in _rep_rows(text)}
    edits = []
    for slot, clip in changes.items():
        m = by_slot.get(slot)
        if m is None:
            raise Unknown("no slot %r in this .rep" % slot)
        edits.append((m.start(2), m.end(2), str(clip).strip()))
    return edits


# --------------------------------------------------------------- the registry

KINDS = {
    "dam":  dict(title="Damage tables", reader=dam_read, editor=dam_set,
                 wants=(), blurb="What each limb takes."),
    "sphs": dict(title="Bounding shapes", reader=sphs_read, editor=sphs_set,
                 wants=("bones",),
                 blurb="Four spheres and one capsule that push the world."),
    "ars":  dict(title="Arsenal", reader=ars_read, editor=None,
                 wants=("catalogue",),
                 blurb="Which weapons this character carries."),
    "hot":  dict(title="Hot spots", reader=hot_read, editor=hot_set,
                 wants=("bones",),
                 blurb="The capsules bullets are tested against."),
    "tail": dict(title="Physics chains", reader=tail_read, editor=tail_set,
                 wants=(), blurb="Hair, tails and coats that swing."),
    "acc":  dict(title="Accessories", reader=acc_read, editor=acc_set,
                 wants=(), blurb="Extra skeletons bolted onto this one."),
    "armr": dict(title="Armour", reader=armr_read, editor=armr_set,
                 wants=(), blurb="Shields and plates that soak damage."),
    "rep":  dict(title="Animation slots", reader=rep_read, editor=rep_set,
                 wants=("anims",), blurb="Which clip plays for which slot."),
}

ORDER = ("dam", "sphs", "ars", "hot", "tail", "acc", "armr", "rep")


def read(ext, data, **ctx):
    """Structured document for one file. `ctx` supplies whatever the kind
    declares in `wants` -- a bone set, the weapon catalogue, the anim roster."""
    spec = KINDS[ext]
    doc = spec["reader"](_decode(data),
                         **{k: ctx[k] for k in spec["wants"] if k in ctx})
    doc.update(kind=ext, title=spec["title"], blurb=spec["blurb"])
    return doc


def edits_for(ext, data, changes):
    """[(start, end, text)] for a change set. Raises Unknown for a bad path."""
    text = _decode(data)
    if ext == "ars":
        return ars_write(text, {k: str(v) for k, v in changes.items()})
    return KINDS[ext]["editor"](text, changes)


def setter_for(ext):
    """A `setter=` callable for `Studio.edit_creature`, bound to one kind.

    A closure rather than a `set_fields(data, changes)` that reads the
    extension out of the change set. `edit_creature` builds its journal label
    by joining `changes.items()`, so a synthetic key rides all the way into
    the undo history and the user reads "ani.dam: 0/body/0=0.9, __ext__=dam".
    Nothing that is not a change belongs in the change set.
    """
    def set_fields(data, changes):
        return splice(_decode(data), edits_for(ext, data, changes))
    return set_fields
