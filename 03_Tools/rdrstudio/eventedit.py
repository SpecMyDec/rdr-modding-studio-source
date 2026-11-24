"""Adding and removing a level's event markers: light glows, camera shots, AI spawn points, doors to other areas.

2026-11-18. The level editor showed these read-only (levelmeta.py reads them; nothing wrote them), so they could
be neither deleted nor added. Each is a text edit of its own file, found the same way levelmeta finds it -- item
`index` is its order in levelmeta's list for that file (for AI spawn points, within its group):

* .glows  `Glow { ... }` blocks under `NumGlows N`
* .cam    one shot a line: `Name  ex ey ez  tx ty tz  lens [extra]` (`NumCams N` when the file has it)
* .aisp   `SpawnPoints { <group> { World x y z heading ... } ... }`
* .sub    `SubLevel { ... Enter { Pos x y z ... } ... }`

An added item is a copy of one of the file's own (the selected one, else the first), moved to where it was put.
The returned bytes go through Studio.write, so a level of the disc is journalled and a level only the mods folder
has is written there.
"""
import re

NUM = r"-?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?"


class EventError(Exception):
    pass


def _f(v):
    return "%.6f" % float(v)


def _blocks(text, label, lo=0, hi=None):
    """[(start, end, open_brace)] of `label { ... }` blocks in text[lo:hi]; start at the label's line, end just
    after the closing brace."""
    hi = len(text) if hi is None else hi
    out = []
    for m in re.finditer(r"(?m)^([ \t]*)" + re.escape(label) + r"[ \t]*\r?\n?[ \t]*\{", text[lo:hi]):
        j = lo + m.end() - 1
        depth, k = 0, j
        while k < hi:
            if text[k] == "{":
                depth += 1
            elif text[k] == "}":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        out.append((lo + m.start(), k + 1, j))
    return out


def _eat_line_end(text, end):
    while end < len(text) and text[end] in " \t":
        end += 1
    if text.startswith("\r\n", end):
        return end + 2
    if text.startswith("\n", end):
        return end + 1
    return end


def _bump(text, key, delta):
    return re.sub(r"(%s[ \t]+)(\d+)" % re.escape(key), lambda m: m.group(1) + str(max(0, int(m.group(2)) + delta)),
                  text, count=1)


def _nl(text):
    return "\r\n" if "\r\n" in text else "\n"


# ---------------------------------------------------------------------------------------------- glows

def _glows(text):
    return _blocks(text, "Glow")


def glow_remove(text, index):
    bl = _glows(text)
    if not 0 <= index < len(bl):
        raise EventError("there is no glow %d in this file" % (index + 1))
    s, e, _o = bl[index]
    return _bump(text[:s] + text[_eat_line_end(text, e):], "NumGlows", -1)


DEFAULT_GLOW = ("Glow\n{\n\tTextureName\tsun01\n\tColor\t1.000000\t1.000000\t1.000000 \n\tWashoutColor\t1.000000\t1.000000"
                "\t1.000000 \n\tWashoutAngle\t66.599945 \n\tFadeoutAngle\t101.599960 \n\tObstructConeSize\t0.000000 \n"
                "FadeInRate\t10.695496 \nFadeOutRate\t10.074048 \n\tScale\t8.000000 \n\tPosition\t0.000000\t0.000000"
                "\t0.000000 \n\tCullDist\t0.000000 \n\tZTestEnable\t1 \n\tZRotateEnable\t1 \n\tAlwaysFaceCamEnable\t1 \n"
                "\tMinDist\t0.000000 \n\tMaxDist\t0.000000 \n\tFollowsCamera\t0 \n}")


def glow_add(text, pos, template=None):
    bl = _glows(text)
    nl = _nl(text)
    if bl:
        s, e, _o = bl[template if template is not None and 0 <= template < len(bl) else 0]
        block = text[s:e]
        at = _eat_line_end(text, bl[-1][1])
    else:
        block = DEFAULT_GLOW.replace("\n", nl)
        at = len(text)
    block = re.sub(r"(Position[ \t]+)%s([ \t]+)%s([ \t]+)%s" % (NUM, NUM, NUM),
                   lambda m: "%s%s%s%s%s%s" % (m.group(1), _f(pos[0]), m.group(2), _f(pos[1]), m.group(3), _f(pos[2])),
                   block, count=1)
    if at == len(text) and text and not text.endswith(("\n", "\r")):
        block = nl + block
    out = text[:at] + block + nl + text[at:]
    if re.search(r"NumGlows[ \t]+\d+", out):
        return _bump(out, "NumGlows", +1)
    return "NumGlows\t1 " + nl + out


# ---------------------------------------------------------------------------------------------- cameras

def _cam_lines(text):
    """[(start, end, parts)] of the lines levelmeta.cameras reads as shots."""
    out = []
    for m in re.finditer(r"[^\r\n]*(?:\r\n|\n|$)", text):
        if m.start() == m.end():
            break
        line = m.group(0).strip()
        if not line or line.startswith(("Version", "NumCams")):
            continue
        parts = line.split()
        if len(parts) < 8:
            continue
        vals = re.findall(NUM, " ".join(parts[1:]))
        if len(vals) < 7:
            continue
        out.append((m.start(), m.end(), parts))
    return out


def camera_remove(text, index):
    cl = _cam_lines(text)
    if not 0 <= index < len(cl):
        raise EventError("there is no camera shot %d in this file" % (index + 1))
    s, e, _p = cl[index]
    return _bump(text[:s] + text[e:], "NumCams", -1)


def camera_add(text, pos, template=None):
    cl = _cam_lines(text)
    if not cl:
        raise EventError("this file has no camera shot to copy")
    _s, _e, parts = cl[template if template is not None and 0 <= template < len(cl) else 0]
    vals = [float(v) for v in parts[1:8]]
    eye, tgt = vals[0:3], vals[3:6]
    look = [tgt[i] - eye[i] for i in range(3)]
    new_eye = [float(pos[0]), float(pos[1]) + 1.7, float(pos[2])]
    new_tgt = [new_eye[i] + look[i] for i in range(3)]
    taken = {p[0] for _a, _b, p in cl}
    base = re.sub(r"_?\d+$", "", parts[0]) or "cam"
    k = 1
    while "%s_%02d" % (base, k) in taken:
        k += 1
    nl = _nl(text)
    line = "\t".join(["%s_%02d" % (base, k)] + [_f(v) for v in new_eye + new_tgt] + [parts[7]] + parts[8:])
    at = cl[-1][1]
    if at == len(text) and text and not text.endswith(("\n", "\r")):
        line = nl + line
    return _bump(text[:at] + line + nl + text[at:], "NumCams", +1), "%s_%02d" % (base, k)


# ---------------------------------------------------------------------------------------------- AI spawn points

def _aisp_group(text, group):
    for s, e, o in _blocks(text, "SpawnPoints"):
        for gs, ge, go in _blocks(text, group, o + 1, e - 1):
            return gs, ge, go
    raise EventError("no AI spawn group %s in this file" % group)


def _aisp_lines(text, group):
    gs, ge, go = _aisp_group(text, group)
    out = []
    for m in re.finditer(r"[^\r\n]*(?:\r\n|\n)", text[go + 1:ge - 1]):
        line = m.group(0).strip()
        if not line:
            continue
        parts = line.split()
        if parts[0].lower() == "tile" and len(parts) >= 6:
            vals = re.findall(NUM, " ".join(parts[2:]))
        elif len(parts) >= 5 and not re.match(r"-?\d", parts[0]):
            vals = re.findall(NUM, " ".join(parts[1:]))
        else:
            continue
        if len(vals) < 4:
            continue
        out.append((go + 1 + m.start(), go + 1 + m.end(), m.group(0)))
    return out, (gs, ge, go)


def aisp_remove(text, group, index):
    lines, _g = _aisp_lines(text, group)
    if not 0 <= index < len(lines):
        raise EventError("there is no point %d in AI spawn group %s" % (index + 1, group))
    s, e, _l = lines[index]
    return text[:s] + text[e:]


def aisp_add(text, group, pos, heading=0.0):
    lines, (gs, ge, go) = _aisp_lines(text, group)
    nl = _nl(text)
    indent = re.match(r"[ \t]*", lines[0][2]).group(0) if lines else "\t\t"
    line = "%sWorld\t%s\t%s\t%s \t%s%s" % (indent, _f(pos[0]), _f(pos[1]), _f(pos[2]), _f(heading), nl)
    at = lines[-1][1] if lines else ge - 1
    return text[:at] + line + text[at:]


# ---------------------------------------------------------------------------------------------- doors

def _subs(text):
    return _blocks(text, "SubLevel")


def door_remove(text, index):
    bl = _subs(text)
    if not 0 <= index < len(bl):
        raise EventError("there is no door %d in this file" % (index + 1))
    s, e, _o = bl[index]
    return _bump(text[:s] + text[_eat_line_end(text, e):], "NumSubLevels", -1)


def door_add(text, pos, template=None):
    bl = _subs(text)
    if not bl:
        raise EventError("this file has no door to copy")
    s, e, o = bl[template if template is not None and 0 <= template < len(bl) else 0]
    block = text[s:e]
    ent = _blocks(block, "Enter")
    if ent:
        es, ee, eo = ent[0]
        inner = re.sub(r"(Pos[ \t]+)%s([ \t]+)%s([ \t]+)%s" % (NUM, NUM, NUM),
                       lambda m: "%s%s%s%s%s%s" % (m.group(1), _f(pos[0]), m.group(2), _f(pos[1]), m.group(3),
                                                    _f(pos[2])), block[eo:ee], count=1)
        block = block[:eo] + inner + block[ee:]
    nl = _nl(text)
    at = _eat_line_end(text, bl[-1][1])
    return _bump(text[:at] + block + nl + text[at:], "NumSubLevels", +1)


# ---------------------------------------------------------------------------------------------- one entry point

KIND_EXT = {"glow": ".glows", "camera": ".cam", "aispt": ".aisp", "transition": ".sub"}


def edit(text, kind, op, index=None, pos=None, group=None, heading=0.0):
    """-> (new text, name of an added item or None)."""
    if kind == "glow":
        return (glow_remove(text, index), None) if op == "remove" else (glow_add(text, pos, index), None)
    if kind == "camera":
        return (camera_remove(text, index), None) if op == "remove" else camera_add(text, pos, index)
    if kind == "aispt":
        return (aisp_remove(text, group, index), None) if op == "remove" else (aisp_add(text, group, pos, heading), None)
    if kind == "transition":
        return (door_remove(text, index), None) if op == "remove" else (door_add(text, pos, index), None)
    raise EventError("%s markers are not added or removed here" % kind)
