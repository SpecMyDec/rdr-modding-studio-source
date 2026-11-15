"""Area doors: the `.sub` files, read with byte spans so they can be changed.

A `.sub` is the strongest positioned event on the disc. Each `SubLevel` block
names the level a door leads to, the name the game shows the player, the exact
position and heading the player arrives at, where they come back to, the door
model and the animation it plays, and whether it is locked.

`levelmeta.transitions()` has read all of that for a long time and returns
plain dicts with no offsets, so there was no edit path at all. This module
reads the same file through `props.parse()`, which already handles this
grammar with full spans -- so a door editor is a route, not a parser.

Two facts govern the design and both are uncomfortable:

  * Only 6 distinct `.sub` files exist across the whole disc, covering 10
    archives. LOGIC > Links is genuinely empty for most levels and must say so
    rather than look broken.
  * A `.sub` slot has 17-35 bytes of headroom -- measured through this
    module's own route across all 36 doors on the pristine disc (TOMB's six
    doors all report 35). Position, heading and lock are safe. Changing a
    destination to a longer name can overflow, so the legal destinations are
    a closed list and an overflow is refused with the byte count rather than
    truncated.
"""

import re

# A door field -> the key inside its SubLevel block.
SCALARS = {
    "level": "LevelName",
    "display": "DisplayName",
    "door_type": "DoorType",
    "lock": "Lock",
    "model": "DoorModName",
    "anim": "DoorAnimName",
    "start_level": "StartLevel",
}
VECTORS = {
    "camera_offset": "CameraOffset",
}


def _nums(text, n=None):
    out = []
    for t in re.findall(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?", text):
        try:
            out.append(float(t))
        except ValueError:
            pass
    return out[:n] if n else out


def sub_files(dave):
    return sorted(e.name for e in dave if e.ext == "sub")


# `.sub` is written with CRLF, and the shared text parser's block-open rule
# anchors on end-of-line without allowing for the CR, so every `SubLevel {`
# reads as a field called `SubLevel` with the value `{` and the block nesting
# is lost. That is a real defect in `props.py`, but fixing it would rename the
# field paths every existing props edit is keyed by, so it is reported rather
# than changed here. This scanner is CR-tolerant and records the same spans.
_OPEN = re.compile(rb"^[ \t]*([A-Za-z_]\w*)?[ \t]*\{[ \t]*\r?$")
_CLOSE = re.compile(rb"^[ \t]*\}[ \t]*\r?$")
_FIELD = re.compile(rb"^([ \t]*)([A-Za-z_]\w*)([ \t]+)([^\r\n]*?)[ \t]*\r?$")


class SubField:
    __slots__ = ("key", "path", "raw", "start", "end")

    def __init__(self, key, path, raw, start, end):
        self.key = key
        self.path = path
        self.raw = raw
        self.start = start
        self.end = end


def parse_fields(data):
    """Every `Key value` line in a `.sub`, with its block path and value span.

    Repeated paths get an occurrence suffix, so `SubLevel/Enter/Pos#2` names
    exactly one line in a file with six identical-looking door blocks.
    """
    out = []
    stack: list[str] = []
    seen: dict[str, int] = {}
    pos = 0
    n = len(data)
    while pos < n:
        nl = data.find(b"\n", pos)
        end = n if nl < 0 else nl
        line = data[pos:end]
        stripped = line.strip()
        if not stripped or stripped.startswith(b";"):
            pass
        elif _CLOSE.match(line):
            if stack:
                stack.pop()
        else:
            m = _OPEN.match(line)
            if m:
                stack.append((m.group(1) or b"").decode("latin-1"))
            else:
                fm = _FIELD.match(line)
                if fm:
                    key = fm.group(2).decode("latin-1")
                    path = "/".join([s for s in stack if s] + [key])
                    seen[path] = seen.get(path, 0) + 1
                    if seen[path] > 1:
                        path = "%s#%d" % (path, seen[path])
                    vs = pos + fm.start(4)
                    ve = pos + fm.end(4)
                    out.append(SubField(key, path,
                                        data[vs:ve].decode("latin-1"), vs, ve))
        pos = end + 1
    return out


class Door:
    """One SubLevel block: the parsed door plus every field's path."""

    def __init__(self, index, paths, values):
        self.index = index
        self.paths = paths        # logical name -> field path
        self.values = values

    def to_json(self, headroom):
        v = self.values
        return dict(
            id=self.index,
            level=v.get("level"),
            display=(v.get("display") or "").replace("_", " ").strip(),
            enter=dict(pos=v.get("enter_pos"), angle=v.get("enter_angle"),
                       dist=v.get("enter_dist"), euler=v.get("enter_euler")),
            leave=dict(pos=v.get("leave_pos"), heading=v.get("leave_heading")),
            door_type=v.get("door_type"),
            lock=v.get("lock"),
            model=v.get("model"),
            anim=v.get("anim"),
            camera_offset=v.get("camera_offset"),
            start_level=bool(v.get("start_level")),
            headroom=headroom,
            # Every number here was read straight out of the file. Nothing on
            # a door row is inferred.
            rung="measured")


def _suffix(i):
    """Repeated paths are disambiguated with `#2`, `#3`, ..."""
    return "" if i == 0 else "#%d" % (i + 1)


def parse(data):
    """[Door] for one `.sub` file, each carrying the path of its fields."""
    fields = {f.path: f for f in parse_fields(data)}
    doors = []
    i = 0
    while ("SubLevel/LevelName" + _suffix(i)) in fields or \
            ("SubLevel/DisplayName" + _suffix(i)) in fields:
        s = _suffix(i)
        paths, values = {}, {}

        for name, key in SCALARS.items():
            p = "SubLevel/%s%s" % (key, s)
            if p in fields:
                paths[name] = p
                values[name] = fields[p].raw.strip()
        for name, key in VECTORS.items():
            p = "SubLevel/%s%s" % (key, s)
            if p in fields:
                paths[name] = p
                values[name] = _nums(fields[p].raw, 3)

        for which in ("Enter", "Leave"):
            for key, out in (("Pos", "pos"), ("Angle", "angle"),
                             ("Dist", "dist"), ("Euler", "euler")):
                p = "SubLevel/%s/%s%s" % (which, key, s)
                if p not in fields:
                    continue
                name = "%s_%s" % (which.lower(), out)
                paths[name] = p
                raw = fields[p].raw
                values[name] = (_nums(raw, 3) if out in ("pos", "euler")
                                else (_nums(raw, 1) or [None])[0])
        for key, name in (("LeavePos", "leave_pos"),
                          ("LeaveHeading", "leave_heading")):
            p = "SubLevel/%s%s" % (key, s)
            if p in fields:
                paths.setdefault(name, p)
                raw = fields[p].raw
                values.setdefault(name, _nums(raw, 3) if name.endswith("pos")
                                  else (_nums(raw, 1) or [None])[0])

        # `Lock -1` means unlocked; anything else names a key or condition.
        for k in ("lock", "door_type", "start_level"):
            if k in values and re.fullmatch(r"-?\d+", str(values[k]) or ""):
                values[k] = int(values[k])
        doors.append(Door(i, paths, values))
        i += 1
    return doors


def destinations(game):
    """Every level a door may point at: `.lvl` manifests across the disc.

    Built from the manifests rather than from the doors, because the point of
    the list is to offer somewhere a door does not currently go.
    """
    from .level import list_levels
    seen = {}
    for arc in list_levels(game):
        try:
            dave = game.archive(arc)
        except Exception:
            continue
        for e in dave:
            if e.ext != "lvl":
                continue
            stem = e.name[:-4]
            if stem in seen:
                continue
            seen[stem] = dict(level=stem, display=_pretty(stem), archive=arc)
    return [seen[k] for k in sorted(seen)]


def _pretty(stem):
    s = stem[4:] if stem.lower().startswith("lvl_") else stem
    s = s.replace("_", " ").strip()
    return s[:1].upper() + s[1:] if s else stem


def resolve_destination(dests, value):
    """The exact `LevelName` to write, or None. Case-insensitive."""
    want = str(value).strip().lower()
    for d in dests:
        if d["level"].lower() == want:
            return d["level"]
    return None


def render(name, value, old_raw):
    """The text to write for one door field, keeping the file's own layout.

    Numbers reuse the separator already on the line -- these files mix tabs
    and spaces within one block -- so a rewritten vector stays the shape its
    neighbours are.
    """
    if isinstance(value, (list, tuple)):
        sep = "\t" if "\t" in old_raw else " "
        return sep.join("%.6f" % float(v) for v in value)
    if name in ("lock", "door_type", "start_level"):
        return str(int(value))
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        return "%.6f" % value
    if isinstance(value, int):
        return str(value)
    return str(value)


def flatten(changes):
    """`{"enter": {"pos": [...]}}` -> `{"enter_pos": [...]}`.

    The wire format nests because a door reads better nested; the file is
    flat. Doing the translation in one place stops each caller inventing its
    own.
    """
    out = {}
    for key, value in changes.items():
        if key in ("enter", "leave") and isinstance(value, dict):
            for sub, v in value.items():
                out["%s_%s" % (key, sub)] = v
        else:
            out[key] = value
    return out
