"""A level's events and its module layout.

`scene.py` reads the things that have a matrix -- buildings, volumes, splines,
spawns, enemies. This reads the files that describe what HAPPENS in a level and
how a level is assembled, which is everything the editor needed in order to
show more than geometry:

    .sub      area transitions -- the doors that load another map
    .cam      named camera shots, including the TGS_* cutscene sequences
    .glows    lens flares, positioned and scaled
    .shadows  the level's own sun direction
    .flags    named level events -- deliberately WITHOUT coordinates
    .aisp     AI spawn points in named groups, each naming its owner module
    .trn      train consist: which cars, in what order, with what spacing
    .til      terrain tile sequence for a moving-origin level
    .znv      train speed

All of these are plain text. `props.py` already handles the general
`Key value` / `Name { }` grammar, but three of them (`.trn`, `.til`, `.aisp`)
carry bare-value blocks and repeated keys that a key/value parser flattens and
loses, so they get purpose-built readers here.

Honesty rules that the callers depend on:

  * A flag has NO position. Nothing on this disc binds a flag name to a
    coordinate -- searching every one of TRAINGOO's 1,242 files for
    `Inst_Jump` returns exactly one hit, its own `.flags`. So flags are
    returned as a list and never as a marker.
  * A `.cam` shot is positioned, but WHERE IT FIRES is not in the data. The
    trigger is a flag.
  * The train layout below is DERIVED (tile order x rail length), not stated
    anywhere on the disc. `derived=True` says so, and callers must show it.
"""

import re
from typing import Any


# --------------------------------------------------------------- small tools

def _text(game, archive, name):
    """A level file as text, or None if it is absent or not text."""
    try:
        data = game.read(archive, name)
    except Exception:
        return None
    for enc in ("ascii", "utf-8", "utf-16-le"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return None


def _find(game, archive, suffix):
    """Every file in the archive with this extension, lowercase names."""
    try:
        return [e.name for e in game.toc(archive)
                if e.name.lower().endswith(suffix)]
    except Exception:
        return []


def _nums(s):
    return [float(x) for x in re.findall(r"-?\d+\.?\d*(?:[eE][-+]?\d+)?", s)]


def _blocks(text):
    """Split `Name { ... }` into (name, body) pairs, one nesting level deep.

    Written by hand rather than reused from props.py because these files nest
    anonymous blocks -- `Train { } { }` -- which a key/value grammar drops.
    """
    out = []
    i = 0
    n = len(text)
    while i < n:
        j = text.find("{", i)
        if j < 0:
            break
        head = text[i:j].strip().splitlines()
        label = head[-1].strip() if head else ""
        depth = 1
        k = j + 1
        while k < n and depth:
            if text[k] == "{":
                depth += 1
            elif text[k] == "}":
                depth -= 1
            k += 1
        out.append((label, text[j + 1:k - 1]))
        i = k
    return out


# ------------------------------------------------------------- area transitions

def transitions(game, archive):
    """The doors out of this level, from the `.sub` file.

    This is the strongest positioned event on the disc: each sub-level names
    the map it loads, the name the game shows the player, the exact position
    and heading the player enters at, where they come back to, and the door
    model plus the animation it plays.
    """
    out = []
    for fn in _find(game, archive, ".sub"):
        txt = _text(game, archive, fn)
        if not txt:
            continue
        master = re.search(r"MasterNameDisplay\s+(\S+)", txt)
        sub_index = -1
        for label, body in _blocks(txt):
            if label != "SubLevel":
                continue
            sub_index += 1
            def one(key):
                m = re.search(key + r"\s+(\S+)", body)
                return m.group(1) if m else None

            enter = leave = None
            for sub, sbody in _blocks(body):
                if sub == "Enter":
                    p = re.search(r"Pos\s+([-\d.\s]+)", sbody)
                    enter = _nums(p.group(1))[:3] if p else None
                elif sub == "Leave":
                    p = re.search(r"Pos\s+([-\d.\s]+)", sbody)
                    leave = _nums(p.group(1))[:3] if p else None
            lp = re.search(r"LeavePos\s+([-\d.\s]+)", body)
            lh = re.search(r"LeaveHeading\s+(-?[\d.]+)", body)
            lock = one("Lock")
            out.append(dict(
                source=fn, index=sub_index,
                level=one("LevelName"),
                # The game's own player-facing name, underscores and all.
                display=(one("DisplayName") or "").replace("_", " ").strip(),
                master=(master.group(1).replace("_", " ") if master else None),
                enter=enter,
                leave=leave or (_nums(lp.group(1))[:3] if lp else None),
                leave_heading=(float(lh.group(1)) if lh else None),
                door_model=one("DoorModName"),
                door_anim=one("DoorAnimName"),
                door_type=one("DoorType"),
                # -1 means unlocked; anything else names a key/condition index.
                lock=(int(lock) if lock and re.fullmatch(r"-?\d+", lock) else lock),
                start=one("StartLevel"),
            ))
    return out


# --------------------------------------------------------------------- cameras

def cameras(game, archive):
    """Named camera shots from every `.cam`.

    Format is one shot per line: `Name  ex ey ez  tx ty tz  lens [extra]`.
    `TGS_` names are cutscene shots -- the whole `TGS_train_puppet1..4`
    sequence is here, positioned. Where each fires is a flag, and flags carry
    no coordinate, so this reports the shot and says nothing about timing.
    """
    out = []
    for fn in _find(game, archive, ".cam"):
        txt = _text(game, archive, fn)
        if not txt:
            continue
        cam_index = -1
        for line in txt.splitlines():
            line = line.strip()
            if not line or line.startswith(("Version", "NumCams")):
                continue
            parts = line.split()
            if len(parts) < 8:
                continue
            name = parts[0]
            vals = _nums(" ".join(parts[1:]))
            if len(vals) < 7:
                continue
            eye, target, lens = vals[0:3], vals[3:6], vals[6]
            cam_index += 1
            # PrincessCam and friends are parked at y=10000 rather than deleted.
            parked = abs(eye[1]) > 5000 or abs(target[1]) > 5000
            out.append(dict(
                source=fn, index=cam_index, name=name, eye=eye, target=target, lens=lens,
                cutscene=name.startswith("TGS_"),
                duel=fn.lower().startswith("duelview"),
                parked=parked,
            ))
    return out


# ----------------------------------------------------------- glows and the sun

def glows(game, archive):
    """Lens flares: a texture, a position, a scale, fade angles."""
    out = []
    for fn in _find(game, archive, ".glows"):
        txt = _text(game, archive, fn)
        if not txt:
            continue
        glow_index = -1
        for label, body in _blocks(txt):
            if label != "Glow":
                continue
            glow_index += 1
            def one(key, cast=str):
                m = re.search(key + r"\s+(\S+)", body)
                if not m:
                    return None
                try:
                    return cast(m.group(1))
                except ValueError:
                    return None
            p = re.search(r"Position\s+([-\d.\s]+)", body)
            out.append(dict(
                source=fn, index=glow_index,
                texture=one("TextureName"),
                name=one("Name") or one("TextureName"),
                pos=(_nums(p.group(1))[:3] if p else None),
                scale=one("Scale", float),
                fade_angle=one("FadeoutAngle", float),
            ))
    return out


def sun(game, archive):
    """The level's own shadow settings.

    Two different shapes exist on this disc and both must be handled: outdoor
    levels carry `DirLight= x y z` (TRAIN: 0.689 -0.543 0.480), while small
    interiors carry `SimpleShadows` with a radius and alpha and no direction at
    all. The viewport hardcodes one sun for all 61 levels today.
    """
    for fn in _find(game, archive, ".shadows"):
        txt = _text(game, archive, fn)
        if not txt:
            continue
        m = re.search(r"DirLight=?\s+([-\d.\s]+)", txt)
        if m:
            v = _nums(m.group(1))[:3]
            col = re.search(r"Color=?\s+([-\d.\s]+)", txt)
            return dict(source=fn, kind="directional", dir=v,
                        color=(_nums(col.group(1))[:4] if col else None))
        if "SimpleShadows" in txt:
            return dict(source=fn, kind="simple",
                        note="Blob shadows only -- this file states no sun "
                             "direction, so the viewport's default is used.")
    return None


# ----------------------------------------------------------------- level flags

def flags(game, archive):
    """Named level events. NO positions -- see the module docstring."""
    out = []
    for fn in _find(game, archive, ".flags"):
        txt = _text(game, archive, fn)
        if not txt:
            continue
        for line in txt.splitlines():
            parts = line.split()
            if len(parts) == 2 and re.fullmatch(r"-?\d+", parts[1]):
                out.append(dict(source=fn, name=parts[0], value=int(parts[1])))
    return out


# ------------------------------------------------------------- AI spawn points

def spawn_points(game, archive):
    """`.aisp` groups: where the game actually puts characters.

    Each line is `<owner> x y z heading`, where owner is `World` for a
    level-space point, `Tile <name>` for one that belongs to a terrain module,
    or a bare module name for one that rides a train car. That owner field is
    what lets a train level show its cast in the right place.
    """
    out = []
    for fn in _find(game, archive, ".aisp"):
        txt = _text(game, archive, fn)
        if not txt:
            continue
        for label, body in _blocks(txt):
            if label != "SpawnPoints":
                continue
            for group, gbody in _blocks(body):
                pts = []
                for line in gbody.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split()
                    if parts[0].lower() == "tile" and len(parts) >= 6:
                        owner, vals = parts[1], _nums(" ".join(parts[2:]))
                        kind = "tile"
                    elif len(parts) >= 5 and not re.match(r"-?\d", parts[0]):
                        # A number STARTS with a digit or minus. The old test
                        # asked whether the name CONTAINED digits, which threw
                        # away every module owner with a digit in its name --
                        # 20 real spawn points on 3 owners, disc-wide
                        # (crack01frontCoach_dynalite and friends), silently.
                        owner, vals = parts[0], _nums(" ".join(parts[1:]))
                        kind = "world" if owner.lower() == "world" else "module"
                    else:
                        continue
                    if len(vals) < 4:
                        continue
                    pts.append(dict(owner=owner, kind=kind, index=len(pts),
                                    pos=vals[:3], heading=vals[3]))
                if pts:
                    out.append(dict(source=fn, group=group, points=pts))
    return out


# ---------------------------------------------- moving-origin level structure

def consist(game, archive):
    """`.trn`: the trains, their cars in order, and the gap before each car.

    Shape is `Train { size N  <name / ResourceIndex / ResourceIndexXbox> xN }`
    followed by an anonymous `{ }` holding N bare floats -- the spacing. The
    generic props parser drops that second block entirely, which is why this
    is hand-written.

    ResourceIndex is ONE-BASED like every other resource reference in this
    engine, and is returned raw; subtract 1 to index a chunk.
    """
    out = []
    for fn in _find(game, archive, ".trn"):
        txt = _text(game, archive, fn)
        if not txt:
            continue
        blocks = _blocks(txt)
        i = 0
        while i < len(blocks):
            label, body = blocks[i]
            if label != "Train":
                i += 1
                continue
            cars = []
            cur = None
            for line in body.splitlines():
                line = line.strip()
                if not line or line.startswith("size"):
                    continue
                m = re.match(r"ResourceIndex\s+(\d+)", line)
                if m and cur:
                    cur["resource"] = int(m.group(1))
                    continue
                if line.startswith("ResourceIndexXbox"):
                    continue
                cur = dict(name=line.split()[0], resource=None)
                cars.append(cur)
            gaps = []
            if i + 1 < len(blocks) and not blocks[i + 1][0]:
                gaps = _nums(blocks[i + 1][1])
                i += 1
            # Cumulative x along the train: car k sits gaps[0..k] behind the
            # front. The gap list is per-car, so it accumulates.
            x, offs = 0.0, []
            for k in range(len(cars)):
                x += gaps[k] if k < len(gaps) else 0.0
                offs.append(x)
            for car, gap, off in zip(cars, gaps + [0.0] * len(cars), offs):
                car["gap"] = gap
                car["offset"] = off
            out.append(dict(source=fn, cars=cars, length=offs[-1] if offs else 0.0))
            i += 1
    return out


def tiles(game, archive):
    """`.til`: the terrain modules and the order they are laid down in.

    `Tiles { }` is the palette; `Map { sets N  size M  Random R  <names> }` is
    the AUTHORED sequence, sets*size entries long -- the layout the game lays
    down. MSN and MSNCYN are fully fixed (Random 0); TRAIN and TRAINGOO carry
    Random 2. Which R slots the Random governs is a HYPOTHESIS (last-of-set,
    below); the ELF proves only that the keyword is parsed. Callers present
    the sequence as authored and the random slots as uncertain.
    """
    out = []
    for fn in _find(game, archive, ".til"):
        txt = _text(game, archive, fn)
        if not txt:
            continue
        palette, seq, sets, size, rand = [], [], 0, 0, 0
        for label, body in _blocks(txt):
            names = [l.strip().split()[0] for l in body.splitlines()
                     if l.strip() and not re.match(
                         r"(sets|size|Random)\b", l.strip())]
            if label == "Tiles":
                palette = names
            elif label == "Map":
                m = re.search(r"sets\s+(\d+)", body)
                sets = int(m.group(1)) if m else 0
                m = re.search(r"size\s+(\d+)", body)
                size = int(m.group(1)) if m else 0
                m = re.search(r"Random\s+(\d+)", body)
                rand = int(m.group(1)) if m else 0
                seq = names
        if palette or seq:
            out.append(dict(source=fn, palette=palette, sequence=seq,
                            sets=sets, per_set=size, random_tail=rand))
    return out


def train_speed(game, archive):
    """`.znv`: how fast the world moves past a train level."""
    for fn in _find(game, archive, ".znv"):
        txt = _text(game, archive, fn)
        if not txt:
            continue
        m = re.search(r"Velocity0\s+([\d.]+)", txt)
        if m:
            return dict(source=fn, velocity=float(m.group(1)))
    return None


# ------------------------------------------------------------------- assembly

def module_key(name):
    """The module a rail spline belongs to, from its name.

    Two naming styles ship on this disc and both have to work:

        line_armouryC_rail     -> armouryc     (a train car)
        line_trn30rail_01      -> trn30        (a terrain tile)
        line_trn50railBad_01   -> trn50        (same tile, the OTHER track)

    `railBad` is not a typo or a variant module: TRAIN lays two parallel
    tracks, the player's at z=2 and the bandit train's at z=54, and every tile
    carries both. They are the same length, so either measures the tile.
    """
    s = name.lower()
    s = re.sub(r"^line_", "", s)
    s = re.sub(r"_\d+$", "", s)            # trailing instance number
    s = re.sub(r"_?rail(bad)?$", "", s)    # ..._rail, ...rail, ...railBad
    return s.strip("_")


def seam_deltas(level):
    """Each tile's seam-to-seam step, from its own `_bot`/`_top` anchors.

    Every tiled module carries a pair of splines named
    `line_tile_<module>_bot` and `line_tile_<module>_top` -- the tile's entry
    and exit seams. The vector between their midpoints is exactly where the
    next tile begins, in ALL THREE axes.

    This replaces measuring a rail spline's x-extent, which failed twice:

    * `module_key` strips a TRAILING rail token (`line_trn30rail_01` ->
      `trn30`), but MSN names its splines `line_RAIL_cch20_01`, token
      leading, and keeps them in `*_rails.lnes` -- which the manifest does not
      list. So MSN and MSNCYN measured nothing and laid out to 0 m.
    * x-extent alone discards the climb. MEASURED here: TRAINGOO's `tgostr`
      steps (90.00, -11.00, -18.53) and MSNCYN's `cynstr` steps
      (120.00, +18.21, +4.50). Laying those out on x alone puts TRAINGOO's
      entire third tile set 11 m high and 18.5 m sideways -- which is what the
      tool did, while reporting the level as correctly unfolded.

    The anchors exist for every tile on all four tiled archives (MSN 7,
    MSNCYN 11, TRAIN 14, TRAINGOO 22 pairs) and are already loaded with the
    level, so this costs no extra file reads.
    """
    bot: dict[Any, Any] = {}
    top: dict[Any, Any] = {}
    for n in level.nodes:
        name = (n.name if hasattr(n, "name") else n.get("name", "")) or ""
        kind = n.kind if hasattr(n, "kind") else n.get("kind")
        if kind != "line":
            continue
        m = re.match(r"line_tile_(.+?)_(bot|top)$", name.lower())
        if not m:
            continue
        verts = n.verts if hasattr(n, "verts") else n.get("verts") or []
        pts = [[x.value if hasattr(x, "value") else float(x) for x in v]
               for v in verts]
        if not pts:
            continue
        mid = [sum(p[i] for p in pts) / float(len(pts)) for i in range(3)]
        (bot if m.group(2) == "bot" else top)[m.group(1)] = mid

    out = {}
    for key in sorted(set(bot) & set(top)):
        b, t = bot[key], top[key]
        out[key] = dict(delta=[t[0] - b[0], t[1] - b[1], t[2] - b[2]],
                        length=t[0] - b[0], source="seam")
    return out


def module_lengths(level):
    """Each module's length along the track, from its own rail spline.

    A rail spline is the module's track spine, so its extent in x IS how much
    world the module occupies. The whole derived train layout rests on this one
    measurement, so it is computed here rather than assumed anywhere.

    Measured on this disc: every TRAIN tile rail is exactly 60.00 m and every
    TRAINGOO tile rail exactly 90.00 m, except `trn50` -- 120.00 m, and the
    only rail that changes z (2.00 -> 15.07). That is the seam tile between the
    two tile sets, so the geometry and the `.til` structure agree independently.
    """
    out = {}
    for n in level.nodes:
        name = n.name if hasattr(n, "name") else n.get("name", "")
        kind = n.kind if hasattr(n, "kind") else n.get("kind")
        if kind != "line" or "rail" not in name.lower():
            continue
        verts = n.verts if hasattr(n, "verts") else n.get("verts") or []
        pts = []
        for v in verts:
            pts.append([x.value if hasattr(x, "value") else float(x) for x in v])
        if len(pts) < 2:
            continue
        xs = [p[0] for p in pts]
        zs = [p[2] for p in pts]
        key = module_key(name)
        rec = dict(length=max(xs) - min(xs), verts=len(pts),
                   z_start=zs[0], z_end=zs[-1], z_shift=zs[-1] - zs[0],
                   rail=name)
        # Prefer the player's own track when a module carries two.
        if key in out and "bad" in name.lower():
            continue
        out[key] = rec
    return out


def frames(game, archive, level):
    """The modules a level is built from, laid out end to end.

    Returns [] for an ordinary level: a normal map has one frame, itself, and
    inventing a hierarchy for it would just add a layer to click through.

    For a train level this is the answer to "the map is bigger than it looks":
    TRAIN's 14 terrain tiles and 10 cars are all authored at the origin, and
    the game moves them. Laying them out by `.til` order times each tile's own
    rail length turns that pile into a corridor.

    DERIVED, and flagged as such. Nothing on the disc states "tile trn33 sits
    at x=180"; the layout is the tile sequence times the measured rail length.
    """
    tl = tiles(game, archive)
    tr = consist(game, archive)
    if not tl and not tr:
        return []

    lens = module_lengths(level)
    seams = seam_deltas(level)          # preferred: exact, and in three axes
    out = []

    for ti, t in enumerate(tl):
        # The running seam position. Chaining only x drew TRAINGOO's third
        # tile set 11 m high and 18.5 m sideways, because `tgostr` is a ramp.
        # Measured across all four tiled archives, seams never ROTATE -- the
        # yaw is identical at both ends of every tile -- so a plain vector sum
        # is the whole of it; no matrix stack is needed.
        px = py = pz = 0.0
        for slot, name in enumerate(t["sequence"]):
            # `lvl_train_tile_trn30` -> `trn30`, which is how its rail is named.
            key = re.sub(r"^lvl_[a-z0-9]+_tile_", "", name.lower())
            seam = seams.get(key) or seams.get(name.lower().split("_")[-1])
            info = lens.get(key) or lens.get(name.lower().split("_")[-1]) or {}
            if seam:
                step = seam["delta"]
                length = seam["length"]
                derived_from = "its own bot/top seam anchors"
            else:
                length = info.get("length", 0.0)
                step = [length, 0.0, 0.0]
                derived_from = "its rail-spline length"
            set_i = slot // t["per_set"] if t["per_set"] else 0
            in_set = slot % t["per_set"] if t["per_set"] else slot
            # The last `random_tail` slots of a set may be swapped at runtime.
            maybe = t["per_set"] and in_set >= t["per_set"] - t["random_tail"]
            out.append(dict(
                id="tile:%d:%d" % (ti, slot),
                kind="tile", label="%s · slot %d of %d" % (name, slot + 1,
                                                           len(t["sequence"])),
                module=name, slot=slot, set_index=set_i,
                offset=[px, py, pz], length=length, step=step,
                z_shift=info.get("z_shift", 0.0),
                runtime_random=bool(maybe),
                # A tile used twice is ONE mesh in two places: reshaping it
                # changes both, and the caller must warn before writing.
                repeats=t["sequence"].count(name),
                gloss=("Terrain module. Position comes from the .til order and "
                       "%s (%.1f m%s)%s."
                       % (derived_from, length,
                          ", climbing %.1f m" % step[1] if abs(step[1]) > 0.5
                          else "",
                          " — this slot is inside the Random tail, so the game "
                          "may substitute another tile at runtime" if maybe else "")),
            ))
            px += step[0]
            py += step[1]
            pz += step[2]

    for tri, train in enumerate(tr):
        for ci, car in enumerate(train["cars"]):
            out.append(dict(
                id="car:%d:%d" % (tri, ci),
                kind="train_car",
                label="Train %d · car %d of %d · %s"
                      % (tri + 1, ci + 1, len(train["cars"]),
                         car["name"].split("_")[-1]),
                module=car["name"], slot=ci, resource=car["resource"],
                offset=[car["offset"], 0.0, 0.0],
                length=lens.get(
                    re.sub(r"^lvl_[a-z0-9]+_", "", car["name"].lower()), {}
                ).get("length", 0.0),
                gap=car["gap"],
                gloss=("Train car. Offset %.1f m behind the front, from the "
                       ".trn spacing list. ResourceIndex %s is one-based."
                       % (car["offset"], car["resource"])),
            ))
    return out


def everything(game, archive, level=None):
    """One call for the whole per-level picture, for /api/level_events."""
    out = dict(
        transitions=transitions(game, archive),
        cameras=cameras(game, archive),
        glows=glows(game, archive),
        sun=sun(game, archive),
        flags=flags(game, archive),
        spawn_groups=spawn_points(game, archive),
        consist=consist(game, archive),
        tiles=tiles(game, archive),
        speed=train_speed(game, archive),
        frames=(frames(game, archive, level) if level is not None else []),
        derived=True,
    )
    out["notes"] = [
        "Flags have no coordinates. Nothing on this disc binds a flag name to "
        "a position, so they are listed, never placed.",
        "Camera shots are positioned, but what makes one fire is a flag, so "
        "the tool shows the shot and not the trigger.",
        "Train and tile layout is DERIVED from the .til order times each "
        "module's own rail length. It is not stated anywhere on the disc.",
    ]
    return out
