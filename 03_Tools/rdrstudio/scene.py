"""Parsers for Red Dead Revolver's plain-text level formats.

Every parser records the *byte span* of each number it reads. Editing then
means splicing new text over those spans in the original bytes -- nothing else
in the file moves, comments and whitespace survive untouched, and the diff
stays as small as physically possible. That matters because a patched file
must still fit the slot it came from.

Formats handled:
    .lvl   manifest
    .bnds  collision instance nodes   (node NAME { resourceIndex, matrix })
    .znes  render zones               (same + numLods + typed attributes)
    .bxes  oriented boxes             (Box NAME { width height depth matrix })
    .lnes  splines                    (node NAME { matrix, Vertices N { ... }})
    .spwn  player spawn points
    .enm   enemy roster + placement
"""

import re
from typing import Any

# Tabs and CRLF are everywhere in these files; never anchor on \n alone.
_NUM_RE = re.compile(rb"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?")
# `node NAME {` and `Box NAME {` keep the brace on the same line, but `.enm`
# writes a bare `Enemy` with the brace on the next line -- allow one newline.
_BLOCK_RE = re.compile(
    rb"(?m)^[ \t]*(node|Box|Enemy)[ \t]*([^\s{]*)[ \t]*\r?\n?[ \t]*\{")
_MATRIX_RE = re.compile(rb"\bmatrix\b\s*\{", re.S)
_VERTS_RE = re.compile(rb"\bVertices\b[ \t]*(\d+)[ \t]*\{", re.S)


class Num:
    """A number plus where it lives in the source bytes."""

    __slots__ = ("value", "start", "end")

    def __init__(self, value, start, end):
        self.value = value
        self.start = start
        self.end = end

    def __repr__(self):
        return "Num(%g @%d:%d)" % (self.value, self.start, self.end)


def _nums(data, start, end):
    out = []
    for m in _NUM_RE.finditer(data, start, end):
        try:
            out.append(Num(float(m.group()), m.start(), m.end()))
        except ValueError:
            pass
    return out


def _close_brace(data, open_idx):
    depth = 0
    i = open_idx
    n = len(data)
    while i < n:
        c = data[i]
        if c == 0x7B:
            depth += 1
        elif c == 0x7D:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("unbalanced braces from offset %d" % open_idx)


def _kv(data, start, end, key):
    """First `key <number>` inside a span, as a Num."""
    m = re.search(rb"\b" + re.escape(key) + rb"\b[ \t]*(" + _NUM_RE.pattern + rb")",
                  data[start:end])
    if not m:
        return None
    return Num(float(m.group(1)), start + m.start(1), start + m.end(1))


# --------------------------------------------------------------------- model

class Node:
    """One placed thing in a level."""

    def __init__(self, kind, name, source):
        self.kind = kind          # bound | zone | box | line | spawn | enemy
        self.name = name
        self.source = source      # filename it came from
        self.pos = []             # 3 Num  (translation row of the matrix)
        self.rot = []             # 9 Num  (3x3, row-major) -- may be empty
        self.size = {}            # width/height/depth -> Num  (boxes)
        self.verts = []           # [[Num,Num,Num], ...]      (splines)
        self.angle = None         # Num                       (spawns/enemies)
        self.props = {}           # plain strings for display
        self.attrs = []           # [Attr] typed per-instance attributes
        self.span = (0, 0)
        # DISPLAY-ONLY world offset. Zero for everything the game places
        # itself. Non-zero only for tile-owned nodes, whose files are authored
        # in tile-local space and which the engine lays out at runtime -- see
        # Level._tile_layout. It is deliberately NOT part of `pos`: `pos`
        # carries the byte spans that edits are written through, and adding a
        # viewing offset to it would write the offset into the file the first
        # time anyone nudged a tile.
        self.origin = None

    def xyz(self):
        return [n.value for n in self.pos] if self.pos else [0.0, 0.0, 0.0]

    def to_json(self):
        d = dict(kind=self.kind, name=self.name, source=self.source,
                 pos=self.xyz(), props=self.props)
        if self.origin:
            d["origin"] = list(self.origin)
        if self.rot:
            d["rot"] = [n.value for n in self.rot]
        if self.size:
            d["size"] = {k: v.value for k, v in self.size.items()}
        if self.verts:
            d["verts"] = [[c.value for c in v] for v in self.verts]
        if self.angle is not None:
            d["angle"] = self.angle.value
        return d


def chunk_of(node):
    """Which RSC0 chunk this instance draws, or None if it draws nothing.

    `resourceIndex` is ONE-BASED, like the `.rnt` manifest and the container
    MOD0's texture binding. Every resource reference in this engine is.

    Read 0-based it is right about three quarters of the time -- models are
    the commonest chunk kind and they cluster, so a reference lands on some
    MOD0 by luck. The other quarter lands on a MIP0, a PAL0 or a SAN0, which
    no instance can point at. Corrected, all 11,953 references across the
    disc's 97 areas land on a MOD0 or a BND0, and nothing else. Nothing
    crashed while it was wrong -- objects simply wore their neighbour's name.

    Always go through here rather than subtracting one at the call site: the
    raw value is what gets written back to the file, so the two must not be
    confused.
    """
    ri = node.props.get("resourceIndex")
    if ri is None:
        return None
    try:
        i = int(ri) - 1
    except (TypeError, ValueError):
        return None
    return i if i >= 0 else None


# -------------------------------------------------------------------- parsers

def parse_instance_nodes(data, source, kind):
    """.bnds and .znes -- node NAME { resourceIndex N, matrix {...} }"""
    nodes = []
    for m in _BLOCK_RE.finditer(data):
        if m.group(1) != b"node":
            continue
        open_idx = m.end() - 1
        close = _close_brace(data, open_idx)
        node = Node(kind, m.group(2).decode("latin-1"), source)
        node.span = (m.start(), close + 1)

        mm = _MATRIX_RE.search(data, open_idx, close)
        if mm:
            mopen = mm.end() - 1
            mclose = _close_brace(data, mopen)
            vals = _nums(data, mopen, mclose)
            if len(vals) >= 12:
                node.rot = vals[0:9]
                node.pos = vals[9:12]

        for key in (b"resourceIndex", b"numLods"):
            v = _kv(data, open_idx, close, key)
            if v is not None:
                node.props[key.decode()] = v.value

        # The typed per-instance render flags, which used to be parsed past and
        # discarded. They are the only handle on WHY a surface renders the way
        # it does -- why it casts no shadow, why it sits in the alpha bucket,
        # why its texture scrolls. Shape is:
        #
        #     attributes 2 {
        #         INT   AlphaBucket  1
        #         BOOL  NoShadows    true
        #     }
        #
        # Prefixed `attr.` so they cannot collide with a real field name, and
        # kept as strings because the type set is open -- INT, BOOL and a NAME
        # form (effect_01_Name) all occur.
        node.attrs = _attribute_spans(data, open_idx, close)
        for a in node.attrs:
            node.props["attr." + a.name] = a.value
        nodes.append(node)
    return nodes


_ATTR_RE = re.compile(rb"attributes\s+\d+\s*\{")


class Attr:
    """One `TYPE NAME VALUE` attribute line, with the span of its VALUE.

    The span is the entire point. This function used to return decoded
    `(name, value)` strings and throw the offsets away, so 57,645 typed
    attributes across the disc were parsed, glossed, colour-coded, rendered --
    and unwritable, because even the server had nothing to write against.
    Recording where the value sits is what turns "who collides with what" and
    the 65 named particle effects into one-token edits.

    Only the VALUE span is kept, so a rewrite preserves the file's column
    layout exactly: these blocks are aligned with tabs and a reflowed
    `attributes` block would grow the file past a slot for no visible gain.
    """

    __slots__ = ("type", "name", "value", "start", "end")

    def __init__(self, atype, name, value, start, end):
        self.type = atype
        self.name = name
        self.value = value
        self.start = start
        self.end = end

    def __repr__(self):
        return "Attr(%s %s=%r @%d:%d)" % (self.type, self.name, self.value,
                                          self.start, self.end)


def _attribute_spans(data, open_idx, close):
    """[Attr] for each typed attribute inside this node's block."""
    m = _ATTR_RE.search(data, open_idx, close)
    if not m:
        return []
    aopen = m.end() - 1
    aclose = _close_brace(data, aopen)
    out = []
    pos = aopen + 1
    while pos < aclose:
        nl = data.find(b"\n", pos, aclose)
        end = aclose if nl < 0 else nl
        line = data[pos:end]
        # TYPE NAME VALUE. Take the tail rather than parts[2] so a value with
        # a space in it -- VECTOR3 carries three -- survives instead of being
        # silently truncated.
        m2 = re.match(rb"^([ \t]*)(\S+)([ \t]+)(\S+)([ \t]+)(\S.*?)[ \t\r]*$",
                      line)
        if m2:
            vstart = pos + m2.start(6)
            vend = pos + m2.end(6)
            out.append(Attr(m2.group(2).decode("latin-1"),
                            m2.group(4).decode("latin-1"),
                            data[vstart:vend].decode("latin-1"),
                            vstart, vend))
        pos = end + 1
    return out


def _attributes(data, open_idx, close):
    """(name, value) for each typed attribute inside this node's block."""
    return [(a.name, a.value) for a in _attribute_spans(data, open_idx, close)]


def edit_attribute(node, name, value):
    """Edits that rewrite ONE attribute's value in place.

    Adding an attribute that is not already in the block is deliberately out
    of scope: the block's `attributes N {` count would have to be bumped in
    step, and getting that wrong is a corrupt level rather than a wrong value.
    """
    for a in node.attrs:
        if a.name.lower() == str(name).lower():
            return [(a.start, a.end, str(value))]
    raise KeyError("%s has no attribute %r" % (node.name, name))


def parse_boxes(data, source):
    """.bxes -- Box NAME { width height depth matrix {...} }"""
    nodes = []
    for m in _BLOCK_RE.finditer(data):
        if m.group(1) != b"Box":
            continue
        open_idx = m.end() - 1
        close = _close_brace(data, open_idx)
        node = Node("box", m.group(2).decode("latin-1"), source)
        node.span = (m.start(), close + 1)

        for key in (b"width", b"height", b"depth"):
            v = _kv(data, open_idx, close, key)
            if v is not None:
                node.size[key.decode()] = v

        mm = _MATRIX_RE.search(data, open_idx, close)
        if mm:
            mopen = mm.end() - 1
            mclose = _close_brace(data, mopen)
            vals = _nums(data, mopen, mclose)
            if len(vals) >= 12:
                node.rot = vals[0:9]
                node.pos = vals[9:12]

        # Boxes carry the largest share of the disc's typed attributes --
        # 35,486 of 57,645 -- and they were parsed past entirely here.
        node.attrs = _attribute_spans(data, open_idx, close)
        for a in node.attrs:
            node.props["attr." + a.name] = a.value
        nodes.append(node)
    return nodes


def parse_lines(data, source):
    """.lnes -- node NAME { matrix {...} Vertices N { x y z ... } }"""
    nodes = []
    for m in _BLOCK_RE.finditer(data):
        if m.group(1) != b"node":
            continue
        open_idx = m.end() - 1
        close = _close_brace(data, open_idx)
        node = Node("line", m.group(2).decode("latin-1"), source)
        node.span = (m.start(), close + 1)

        mm = _MATRIX_RE.search(data, open_idx, close)
        if mm:
            mopen = mm.end() - 1
            mclose = _close_brace(data, mopen)
            vals = _nums(data, mopen, mclose)
            if len(vals) >= 12:
                node.rot = vals[0:9]
                node.pos = vals[9:12]

        vm = _VERTS_RE.search(data, open_idx, close)
        if vm:
            vopen = vm.end() - 1
            vclose = _close_brace(data, vopen)
            flat = _nums(data, vopen, vclose)
            for i in range(0, len(flat) - 2, 3):
                node.verts.append(flat[i:i + 3])

        node.attrs = _attribute_spans(data, open_idx, close)
        for a in node.attrs:
            node.props["attr." + a.name] = a.value
        nodes.append(node)
    return nodes


def parse_spawns(data, source):
    """.spwn -- NumSpawnPointsN then Position/Heading pairs."""
    nodes = []
    for m in re.finditer(rb"NumSpawnPoints(\d)[ \t]*(\d+)", data):
        players = int(m.group(1))
        count = int(m.group(2))
        if count == 0:
            continue
        # Consume `count` Position/Heading pairs following this header.
        pos_iter = re.finditer(
            rb"Position((?:[ \t]+" + _NUM_RE.pattern + rb"){3})"
            rb"\s*Heading[ \t]+(" + _NUM_RE.pattern + rb")", data[m.end():])
        for i, pm in enumerate(pos_iter):
            if i >= count:
                break
            base = m.end()
            node = Node("spawn", "player%d_spawn_%d" % (players, i + 1), source)
            node.pos = _nums(data, base + pm.start(1), base + pm.end(1))
            node.angle = Num(float(pm.group(2)),
                             base + pm.start(2), base + pm.end(2))
            node.props["players"] = players
            node.span = (base + pm.start(), base + pm.end())
            nodes.append(node)
    return nodes


_ENEMY_STR_KEYS = (b"Table", b"Type", b"Name", b"Group", b"Comment",
                   b"TextureVariation", b"Inventory", b"MyBoss",
                   b"SpawnHorse")

# `Cullable` and `Pushable` were declared editable and never extracted, so the
# inspector could not render a row for them and the write the server would
# have accepted could never be asked for. The rest were simply never read.
_ENEMY_NUM_KEYS = (b"Alignment", b"PopFreq", b"BossType", b"StartWeapon",
                   b"Cullable", b"Pushable", b"ThrownWeaponAmmo",
                   b"IsBeast", b"WhichHead", b"NeedsSound")

# Multi-token values: `PowerUp 9 Six_Shooter`, `WeaponList 0 0 0 0 0 0 0`.
# The list's length is NOT fixed -- SHER70 alone holds blocks with 1, 7 and 9
# entries -- so it is kept as written and never reshaped.
_ENEMY_TAIL_KEYS = (b"PowerUp", b"WeaponList")


def parse_enemies(data, source):
    """.enm -- Enemy { ... Spawn N { Point x y z  Angle a } }"""
    nodes = []
    for m in _BLOCK_RE.finditer(data):
        if m.group(1) != b"Enemy":
            continue
        open_idx = m.end() - 1
        close = _close_brace(data, open_idx)
        body = data[open_idx:close]

        props = {}
        for key in _ENEMY_STR_KEYS:
            km = re.search(rb"(?m)^[ \t]*" + re.escape(key) + rb"[ \t]+(\S+)", body)
            if km:
                props[key.decode()] = km.group(1).decode("latin-1")
        for key in _ENEMY_NUM_KEYS:
            v = _kv(data, open_idx, close, key)
            if v is not None:
                props[key.decode()] = v.value
        for key in _ENEMY_TAIL_KEYS:
            km = re.search(rb"(?m)^[ \t]*" + re.escape(key)
                           + rb"[ \t]+([^\r\n]*)", body)
            if km:
                props[key.decode()] = " ".join(
                    km.group(1).decode("latin-1").split())

        label = props.get("Name") or props.get("Table") or "enemy"
        for i, pm in enumerate(re.finditer(
                rb"Point((?:[ \t]+" + _NUM_RE.pattern + rb"){3})"
                rb"\s*Angle[ \t]+(" + _NUM_RE.pattern + rb")", body)):
            node = Node("enemy", "%s#%d" % (label, i + 1), source)
            node.pos = _nums(data, open_idx + pm.start(1), open_idx + pm.end(1))
            node.angle = Num(float(pm.group(2)),
                             open_idx + pm.start(2), open_idx + pm.end(2))
            node.props = dict(props)
            node.span = (m.start(), close + 1)
            nodes.append(node)
    return nodes


def parse_manifest(data):
    """.lvl -- Section { file file ... } -> {section: [filenames]}"""
    out = {}
    for m in re.finditer(rb"(?m)^[ \t]*(\w+)[ \t]*\r?\n?[ \t]*\{", data):
        section = m.group(1).decode("latin-1")
        close = _close_brace(data, m.end() - 1)
        body = data[m.end():close].decode("latin-1")
        files = [t.strip() for t in body.split() if t.strip()]
        out[section] = files
    return out


PARSERS = {
    "bnds": lambda d, s: parse_instance_nodes(d, s, "bound"),
    "znes": lambda d, s: parse_instance_nodes(d, s, "zone"),
    "bxes": parse_boxes,
    "lnes": parse_lines,
    "spwn": parse_spawns,
    "enm": parse_enemies,
}


def parse_any(filename, data):
    ext = filename.rsplit(".", 1)[-1].lower()
    fn = PARSERS.get(ext)
    return fn(data, filename) if fn else []


# --------------------------------------------------------------------- edits

def fmt_num(value, width_hint=None):
    """Render a float the way the game's exporter did (6 decimals).

    If a width hint is supplied and the natural form is longer, progressively
    drop precision so the replacement fits the original span -- keeping the
    file byte-identical in length wherever possible.
    """
    s = "%.6f" % value
    if width_hint is None or len(s) <= width_hint:
        return s
    for dec in (5, 4, 3, 2, 1):
        s = "%.*f" % (dec, value)
        if len(s) <= width_hint:
            return s
    return "%g" % value


def splice(data, edits):
    """Apply [(start, end, text)] to bytes, right-to-left so spans stay valid."""
    out = bytearray(data)
    for start, end, text in sorted(edits, key=lambda e: -e[0]):
        if isinstance(text, str):
            text = text.encode("latin-1")
        out[start:end] = text
    return bytes(out)


def edit_node_position(node, xyz, keep_width=False):
    """Edits that move a node to xyz. Returns [(start, end, text)]."""
    edits = []
    for num, val in zip(node.pos, xyz):
        hint = (num.end - num.start) if keep_width else None
        edits.append((num.start, num.end, fmt_num(val, hint)))
    return edits


def edit_node_size(node, size, keep_width=False):
    edits = []
    for key, val in size.items():
        num = node.size.get(key)
        if num is None:
            continue
        hint = (num.end - num.start) if keep_width else None
        edits.append((num.start, num.end, fmt_num(val, hint)))
    return edits


def edit_node_angle(node, angle, keep_width=False):
    if node.angle is None:
        return []
    hint = (node.angle.end - node.angle.start) if keep_width else None
    return [(node.angle.start, node.angle.end, fmt_num(angle, hint))]


def edit_node_rot(node, matrix9, keep_width=True):
    """Edits that rewrite the nine rotation numbers of a node's matrix.

    These nine `Num`s have had byte spans on every bound, zone, box and line
    since the parser was written, and nothing has ever written them: the route
    called `rotate` reaches `edit_node_angle`, which touches `node.angle`, and
    `node.angle` only exists on spawns and enemies. 8,489 of 19,072 boxes and
    364 zones ship with a non-identity rotation, so without this you cannot
    place a rotated trigger volume, camera box or AI door.

    The nine numbers are taken and written IN FILE ORDER. This function
    asserts nothing about row- versus column-major; that interpretation is
    inferred and belongs in the viewport, not in the file layer.

    `keep_width` formats each number into the byte width it already had, which
    keeps the file exactly the same length. Off, growth is up to +90 bytes.
    """
    if not node.rot or len(node.rot) < 9:
        return []
    edits = []
    for num, val in zip(node.rot, list(matrix9)[:9]):
        val = float(val)
        # A number that has not moved is not written. Rotations arrive from a
        # viewport gizmo as all nine numbers every time, and eight of them are
        # usually unchanged; rewriting them all would re-format the file for
        # no reason and, under `keep_width`, could round a value that nobody
        # touched.
        if val == num.value:
            continue
        hint = (num.end - num.start) if keep_width else None
        text = fmt_num(val, hint)
        if hint is not None and len(text) < hint:
            # Pad rather than shrink. These nine numbers are whitespace-
            # separated, so trailing spaces inside the span are harmless, and
            # a rotation write that is exactly length-neutral cannot overflow
            # a slot no matter how many objects are turned.
            text = text + " " * (hint - len(text))
        edits.append((num.start, num.end, text))
    return edits


# ------------------------------------------------------ add / remove objects

# The header count each format keeps, which must track the block count.
# .enm has no count header -- enemies are just a run of Enemy { } blocks.
COUNT_KEYS = {
    "bxes": b"NumBoxes",
    "lnes": b"NumLineNodes",
    "bnds": b"NumInstNodes",
    "znes": b"NumInstNodes",
}


def count_field(data, filename):
    """The `NumBoxes 182` style header as a Num, or None if the format has none."""
    ext = filename.rsplit(".", 1)[-1].lower()
    key = COUNT_KEYS.get(ext)
    if not key:
        return None
    m = re.search(rb"\b" + key + rb"\b[ \t]*(\d+)", data)
    if not m:
        return None
    return Num(float(m.group(1)), m.start(1), m.end(1))


def existing_names(data):
    return {m.group(2).decode("latin-1") for m in _BLOCK_RE.finditer(data)}


def existing_enemy_names(data):
    return {m.group(1).decode("latin-1")
            for m in re.finditer(rb"(?m)^[ \t]*Name[ \t]+(\S+)", data)}


def unique_name(base, taken):
    """`zone_piano_01` -> `zone_piano_02`, then `_03`, ... until unused.

    The game's own convention is a two-digit instance counter, and following
    it matters for more than tidiness: these files live in fixed-size slots,
    and `_copy` costs three bytes more than reusing the counter. A placement
    has been refused for overrunning its slot by a single byte, so the
    shorter name is sometimes the difference between an edit that fits and
    one that does not.
    """
    m = re.match(r"^(.*_)(\d+)$", base)
    if m:
        stem, width = m.group(1), len(m.group(2))
        for n in range(int(m.group(2)) + 1, 100):
            cand = "%s%0*d" % (stem, width, n)
            if cand not in taken:
                return cand
    cand = base + "_copy"
    n = 2
    while cand in taken:
        cand = "%s_copy%d" % (base, n)
        n += 1
    return cand


def clone_block(data, node, offset=(0.0, 0.0, 0.0), taken=None):
    """The bytes of a renamed, offset copy of a node's block.

    `taken` is the set of names the copy must avoid, which is not always the
    names in the node's own file -- a copy destined for a different file has
    to be unique there instead.
    """
    start, end = node.span
    block = data[start:end]
    m = _BLOCK_RE.match(block)
    kw = m.group(1) if m else b""

    # Every edit below is expressed against the ORIGINAL block and applied in
    # one splice. Editing in stages would shift the offsets of later edits --
    # renaming first is what silently corrupted the Point line before.
    edits = []

    if kw == b"Enemy":
        # Enemy blocks carry no header name -- the label is an inner `Name`
        # field. Rename only that. `Table` must NOT change: it points at an AI
        # behaviour table that has to exist.
        base = node.props.get("Name") or "enemy"
        new_name = unique_name(base, existing_enemy_names(data)
                               if taken is None else taken)
        nm = re.search(rb"(?m)^([ \t]*Name[ \t]+)(\S+)", block)
        if nm:
            edits.append((nm.start(2), nm.end(2), new_name))
        parsed_name = "%s#1" % new_name
    else:
        new_name = unique_name(node.name, existing_names(data)
                               if taken is None else taken)
        if m and m.group(2):
            edits.append((m.start(2), m.end(2), new_name))
        parsed_name = new_name

    # Nudge the copy so it does not sit exactly inside its original. Node spans
    # are file-relative, so rebase them onto the block.
    if any(offset) and node.pos:
        for num, delta in zip(node.pos, offset):
            edits.append((num.start - start, num.end - start,
                          fmt_num(num.value + delta)))

    if edits:
        block = splice(block, edits)
    return block, parsed_name


def duplicate_block(data, node, offset=(0.0, 0.0, 0.0)):
    """Edits that append a copy of a node's block to its file.

    The copy is inserted immediately after the original so the file stays
    readable, gets a unique name, and is nudged by `offset` so it does not sit
    exactly inside the thing it was cloned from.
    """
    block, parsed_name = clone_block(data, node, offset)
    end = node.span[1]
    sep = b"\r\n\r\n" if b"\r\n" in data else b"\n\n"
    return [(end, end, sep + block)], parsed_name


def transplant_block(data, node, target, offset=(0.0, 0.0, 0.0)):
    """Edits that append a copy of a node's block to a DIFFERENT file.

    Every placement file listed by an area's manifest is parsed into the same
    scene, and a zone's `resourceIndex` points into the area's bundle rather
    than into its own file, so an instance behaves identically whichever of
    those files holds it.

    That is what makes placement practical at all. Files sit in fixed-size
    slots: the Brimstone bar's piano file has 31 spare bytes, so a second
    piano cannot go there -- but the bar's main zone file has 677, and the
    copy fits with room to spare.
    """
    block, parsed_name = clone_block(data, node, offset,
                                     taken=existing_names(target))
    sep = b"\r\n\r\n" if b"\r\n" in target else b"\n\n"
    end = len(target.rstrip(b"\r\n \t"))
    return [(end, end, sep + block)], parsed_name


def delete_block(data, node):
    """Edits that remove a node's block from its file."""
    start, end = node.span
    # Swallow trailing blank space so deletions do not leave holes.
    while end < len(data) and data[end:end + 1] in (b"\r", b"\n", b"\t", b" "):
        end += 1
    # If that was the final block there is no trailing space to absorb the
    # separator, so take the leading blank space instead -- but put back one
    # line terminator, because the file's own trailing newline lives there.
    if end >= len(data):
        while start > 0 and data[start - 1:start] in (b"\r", b"\n", b"\t", b" "):
            start -= 1
        return [(start, end, b"\r\n" if b"\r\n" in data else b"\n")]
    return [(start, end, b"")]


def bump_count(data, filename, delta):
    """Keep the `NumBoxes` style header in step with the block count."""
    num = count_field(data, filename)
    if num is None:
        return []
    return [(num.start, num.end, str(int(num.value) + delta))]


# Enemy fields safe to edit from the inspector.
#
# `Table` was excluded here for a good reason: it names an AI behaviour table
# that has to exist on disc, and a typo there is a crash rather than a visible
# mistake. That reason is still correct -- it is exactly why `Table` is a
# picker built from the archive's own `.tbl` list rather than a text box. The
# field is now listed, and every route that writes it validates the value
# against `cast.py` first, so the typo that motivated the lock cannot be
# spelled.
#
# The full 21-field table with types, labels and legal sets lives in
# `enemyfields.py`; this dict stays as the older route's allowlist.
EDITABLE_ENEMY_FIELDS = {
    "Name": "label only",
    "Type": "3-letter creature id -- must exist as a .cr file",
    "Table": "AI behaviour table -- must exist as a .tbl file in this archive",
    "Group": "spawn phase / wave this enemy belongs to",
    "Alignment": "faction (0 = ally, 1+ = hostile)",
    "PopFreq": "spawn probability 0-100",
    "StartWeapon": "index into WeaponList",
    "BossType": "0 normal, 1+ boss behaviour",
    "Cullable": "may be despawned when far away",
    "Pushable": "can be shoved",
}


def edit_enemy_field(data, node, key, value):
    """Edits that set one `Key Value` line inside an enemy's block."""
    if key not in EDITABLE_ENEMY_FIELDS:
        raise KeyError("field %r is not editable" % key)
    start, end = node.span
    m = re.search(rb"(?m)^([ \t]*" + re.escape(key.encode("latin-1"))
                  + rb"[ \t]+)(\S+)", data[start:end])
    if not m:
        raise KeyError("%s has no field %r" % (node.name, key))
    return [(start + m.start(2), start + m.end(2), str(value))]


def edit_vertex(node, index, xyz, keep_width=False):
    edits: list[tuple[Any, Any, Any]] = []
    if index >= len(node.verts):
        return edits
    for num, val in zip(node.verts[index], xyz):
        hint = (num.end - num.start) if keep_width else None
        edits.append((num.start, num.end, fmt_num(val, hint)))
    return edits
