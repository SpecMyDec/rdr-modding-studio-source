"""Moving level geometry -- in place, in the packet, same number of bytes.

A level surface is a PS2 VIF display list, not a vertex buffer, so "edit the
geometry" sounds like it needs a VIF writer. It does not. Positions travel as
a `V4_32` stream: four IEEE floats per vertex, X Y Z and a W that carries the
GS ADC bit. Overwrite the first three and the packet is otherwise untouched
-- same length, same VIFcodes, same strip breaks, same everything.

That matters because these files live in fixed-size archive slots. Any edit
that changed the packet's length would have to fit the slot, and most have
almost no headroom. An in-place float patch always fits, by construction.

What this does NOT cover:

  * Skinned character meshes. Those send 16-bit quantised positions scaled by
    the VIF ROW register, so moving one means re-deriving the quantisation.
    Their vertices come back with a `None` offset and are refused.
  * Adding or removing vertices. That changes the strip and the packet size.
  * Collision. `BND0` is a separate chunk with its own vertex list and its
    own bounding box; moving the visible surface does not move what you
    bump into. `move_bound` handles that side, and callers that want a
    building to still be solid must do both.
"""

import struct


class NotEditable(Exception):
    pass


def offsets(mesh):
    """The per-vertex byte offsets, or raise if this mesh cannot be moved."""
    vo = mesh.get("voff")
    if not vo or len(vo) != len(mesh["verts"]):
        raise NotEditable("this mesh's vertex offsets were not recorded")
    if any(o is None for o in vo):
        raise NotEditable("some vertices have no recorded offset")
    return vo


def transform(raw, mesh, fn):
    """Apply `fn(x, y, z) -> (x, y, z)` to every vertex. Returns new bytes.

    Two encodings, both handled:

    * **Level geometry** stores positions as three floats. Only those twelve
      bytes are rewritten; W (and the GS ADC bit living in it) is left alone,
      so triangle strips do not break.
    * **Character geometry** stores a 16-bit code per axis, decoded as
      `row + code*ulp - bias` against the VIF ROW register. Writing means
      re-quantising, which is exact: all 2,313,761 skinned vertices on the
      disc re-encode to the byte they came from. Only the three position
      halfwords move; the fourth, which carries the ADC bit and the bone
      slot, is untouched.

    Either way the chunk keeps its length, so it still fits its archive slot.
    """
    from . import vif
    vo = offsets(mesh)
    quant = mesh.get("quant") or [None] * len(vo)
    out = bytearray(raw)
    for i, ((x, y, z), off) in enumerate(zip(mesh["verts"], vo)):
        moved = fn(x, y, z)
        q = quant[i] if i < len(quant) else None
        if q is None:
            struct.pack_into("<3f", out, off, *[float(v) for v in moved])
        else:
            struct.pack_into("<3H", out, off, *vif.quantise(moved, q))
    return bytes(out)


def precision(mesh):
    """Placement step and representable range, in metres, or None if float.

    A modder moving a character vertex needs to know both: the step is how
    finely it can be placed, and the range is how far it can go before the
    16-bit code saturates. Character space is bone-local, so the range is
    sized to the character rather than to the world.
    """
    quant = [q for q in (mesh.get("quant") or []) if q is not None]
    if not quant:
        return None
    step = max(max(q[1]) for q in quant)
    return dict(step=step, range=0xFFFF * step)


def move_spheres(raw, parts, fn):
    """Apply `fn` to each part's cull-sphere centre. Returns new bytes.

    Every level MOD0 is a container of parts, and each part's packet begins
    with a one-quadword VIF unpack to VU address 0x29: (cx, cy, cz, w) -- a
    cull sphere the VU microprogram consumes at RUNTIME. The chunk LOADER
    ignores it (it recomputes node bounds from the verts, and even overwrites
    the header's u16 with a constant -- read from the ELF at 0x003B8968), but
    the VU does not: moving the verts and leaving these spheres froze the
    game, and moving them with the verts plays -- the A/B that settled it,
    185 s runs on 2026-08-12/13.

    The sphere payload sits at part_offset + 12 in 778/778 parts surveyed
    across 12 archives. `w` is NOT this part's own radius (it clusters per
    building group and is sometimes 300x the mesh size, measured) and is not
    position-derived, so a translate leaves it alone; fn receives and returns
    only the centre.
    """
    out = bytearray(raw)
    for part in parts or []:
        off = part["offset"] + 12
        if off + 12 > len(out):
            continue
        x, y, z = struct.unpack_from("<3f", out, off)
        struct.pack_into("<3f", out, off,
                         *[float(v) for v in fn(x, y, z)])
    return bytes(out)


def translate(raw, mesh, dx, dy, dz):
    return transform(raw, mesh, lambda x, y, z: (x + dx, y + dy, z + dz))


def scale(raw, mesh, sx, sy=None, sz=None, about=None):
    """Scale about a point -- the mesh's own centre unless given."""
    sy = sx if sy is None else sy
    sz = sx if sz is None else sz
    cx, cy, cz = about if about else centre(mesh)
    return transform(raw, mesh, lambda x, y, z: (
        cx + (x - cx) * sx, cy + (y - cy) * sy, cz + (z - cz) * sz))


def rotate_y(raw, mesh, radians, about=None):
    """Yaw about a vertical axis -- the useful one for buildings and props."""
    import math
    cs, sn = math.cos(radians), math.sin(radians)
    cx, cy, cz = about if about else centre(mesh)
    return transform(raw, mesh, lambda x, y, z: (
        cx + (x - cx) * cs + (z - cz) * sn,
        y,
        cz - (x - cx) * sn + (z - cz) * cs))


def centre(mesh):
    vs = mesh["verts"]
    if not vs:
        return (0.0, 0.0, 0.0)
    xs = [v[0] for v in vs]
    ys = [v[1] for v in vs]
    zs = [v[2] for v in vs]
    return ((min(xs) + max(xs)) / 2.0,
            (min(ys) + max(ys)) / 2.0,
            (min(zs) + max(zs)) / 2.0)


def bounds(mesh):
    vs = mesh["verts"]
    if not vs:
        return None
    return ([min(v[i] for v in vs) for i in range(3)],
            [max(v[i] for v in vs) for i in range(3)])


def recover(stock, current):
    """What transform turns the vanilla mesh into the one on the disc now?

    Every geometry edit is applied to the VANILLA chunk rather than to the
    working disc, so the ops the client sends are a *total*, not a delta. That
    total was only ever remembered in the browser -- which meant it was lost
    on a page reload, and, worse, went stale on undo: the disc stepped back
    while the remembered total did not, so the next drag silently re-applied
    the move that had just been undone.

    Reading it back off the disc removes the need to remember anything. The
    ops compose to `p = T + R*S*(v - c) + c` about the vanilla centre `c`, so
    with `a = v - c` and `b = p - c` this is a similarity fit, solved in
    closed form. Because the data really was produced by such a transform the
    residual is ~0; when it is NOT -- a chunk reshaped by an OBJ import, say,
    which no yaw-and-scale can describe -- the caller is told, rather than
    being handed a plausible-looking transform that does not fit.

    Returns `(transform_dict, residual_metres)`.
    """
    import math
    va, vb = stock["verts"], current["verts"]
    if not va or len(va) != len(vb):
        return None, None
    cx, cy, cz = centre(stock)
    a = [(v[0] - cx, v[1] - cy, v[2] - cz) for v in va]
    b = [(v[0] - cx, v[1] - cy, v[2] - cz) for v in vb]
    n = float(len(a))
    ma = [sum(p[i] for p in a) / n for i in range(3)]
    mb = [sum(p[i] for p in b) / n for i in range(3)]
    a0 = [(p[0] - ma[0], p[1] - ma[1], p[2] - ma[2]) for p in a]
    b0 = [(p[0] - mb[0], p[1] - mb[1], p[2] - mb[2]) for p in b]

    # Yaw only, so the fit lives in the XZ plane: the angle maximising the
    # correlation is atan2 of the summed cross and dot products.
    dot = sum(p[0] * q[0] + p[2] * q[2] for p, q in zip(a0, b0))
    crs = sum(p[0] * q[2] - p[2] * q[0] for p, q in zip(a0, b0))
    theta = math.atan2(crs, dot) if (abs(dot) + abs(crs)) > 1e-12 else 0.0

    # Uniform scale from the ratio of spreads. A degenerate (single-point)
    # mesh has no spread to measure, so it stays at 1 rather than dividing.
    na = sum(p[0] ** 2 + p[1] ** 2 + p[2] ** 2 for p in a0)
    nb = sum(p[0] ** 2 + p[1] ** 2 + p[2] ** 2 for p in b0)
    s = math.sqrt(nb / na) if na > 1e-12 else 1.0

    # rotate_y above turns (x,z) into (x*cs + z*sn, -x*sn + z*cs), which is a
    # NEGATIVE mathematical rotation, so the reported degrees are negated to
    # match the number the user typed.
    cs, sn = math.cos(theta), math.sin(theta)

    def fwd(p):
        return (s * (p[0] * cs - p[2] * sn), s * p[1],
                s * (p[0] * sn + p[2] * cs))

    # b = R*S*a + T holds for the centroids too, so T is what is left of the
    # centroid shift once rotation and scale are accounted for.
    fma = fwd(ma)
    t = [mb[i] - fma[i] for i in range(3)]

    resid = 0.0
    for p, q in zip(a, b):
        f = fwd(p)
        d = max(abs(f[0] + t[0] - q[0]), abs(f[1] + t[1] - q[1]),
                abs(f[2] + t[2] - q[2]))
        resid = max(resid, d)

    return ({"tx": t[0], "ty": t[1], "tz": t[2], "s": s,
             "rot": -math.degrees(theta)}, resid)


def editable(mesh):
    """Can this mesh be moved? Cheap check for a UI to grey out a control."""
    try:
        offsets(mesh)
        return True
    except NotEditable:
        return False


# --------------------------------------------------------------- collision

BND_MIN, BND_MAX, BND_RADIUS = 0x08, 0x14, 0x38
BND_COUNTS, BND_OFFSETS = 0x4C, 0x68


def _bound_header(raw):
    import struct
    nverts, nfaces = struct.unpack_from("<II", raw, BND_COUNTS)
    voff, foff = struct.unpack_from("<II", raw, BND_OFFSETS)
    if voff + nverts * 12 > len(raw):
        raise NotEditable("bound vertex list runs past the end of the chunk")
    return nverts, voff


def bound_verts(raw):
    """A BND0's collision vertices, as a mesh `recover` can fit.

    The chunk's AABB header is deliberately wider than the vertex list -- see
    `_transform_bound` -- so the box is the wrong thing to measure a transform
    from. The vertices are exact, and they are transformed by every bound op,
    so they are what gets compared.
    """
    import struct
    nverts, voff = _bound_header(raw)
    return {"verts": [struct.unpack_from("<3f", raw, voff + i * 12)
                      for i in range(nverts)]}


def _transform_bound(raw, fn, corners_fn, radius_scale):
    """Move a BND0 collision chunk. Returns new bytes, the same length.

    Moving the visible surface without this leaves you walking through a wall
    that is plainly in front of you.

    The header is TRANSFORMED, not recomputed. Its AABB is deliberately not
    the extent of the vertex list -- measured across Brimstone it is wider --
    so rebuilding it from the vertices would silently change the engine's
    culling volume. Instead the eight corners of the stored box are put
    through the same transform and re-bounded, which is exact for a
    translation and correct-and-conservative for a rotation.

    The radius is scaled by the caller's factor rather than re-derived, for
    the same reason: it is stored about the AABB centre (verified -- it
    matches to five decimals) but we did not author it, so we scale what is
    there instead of substituting our own.
    """
    import struct
    nverts, voff = _bound_header(raw)
    out = bytearray(raw)
    for i in range(nverts):
        o = voff + i * 12
        x, y, z = struct.unpack_from("<3f", raw, o)
        nx, ny, nz = fn(x, y, z)
        struct.pack_into("<3f", out, o, float(nx), float(ny), float(nz))

    lo = list(struct.unpack_from("<3f", raw, BND_MIN))
    hi = list(struct.unpack_from("<3f", raw, BND_MAX))
    corners = corners_fn(lo, hi)
    struct.pack_into("<3f", out, BND_MIN,
                     *[min(c[i] for c in corners) for i in range(3)])
    struct.pack_into("<3f", out, BND_MAX,
                     *[max(c[i] for c in corners) for i in range(3)])
    if radius_scale != 1.0:
        r = struct.unpack_from("<f", raw, BND_RADIUS)[0]
        struct.pack_into("<f", out, BND_RADIUS, float(r * radius_scale))
    return bytes(out)


def _box_corners(lo, hi):
    return [(lo[0] if a else hi[0], lo[1] if b else hi[1],
             lo[2] if c else hi[2])
            for a in (0, 1) for b in (0, 1) for c in (0, 1)]


def translate_bound(raw, dx, dy, dz):
    """DO NOT USE THIS TO MOVE A SHELL. See `boundmove` instead.

    These three functions move a BND0's triangles and its header AABB, and
    leave three other records of where the shell is exactly where they were:
    the stored centre at +0x20, the distance from the world origin at +0x3C,
    and segment B's node tree -- which is the shell's own broadphase index,
    in world space. A shell moved this way has its index still describing the
    old location and nothing describing the new one, so the game collides
    with it in neither. That is the M16 bug, reported as "moving a shell
    removes collision".

    The damage is invisible to every cheap check: the chunk is the right
    length, it re-parses, and it draws correctly in the viewer. Measured by
    `verify_boundmove.py` over 400 real shells, this mover breaks three of
    `boundmove.audit`'s five invariants on 400 of 400 -- and the two it does
    NOT break are the two a naive check looks at.

    They are kept, rather than deleted, because two tests still want them:
    `verify_boundmove.py` uses this one as its negative control, and
    `verify_geomshape.py` uses them to build input for transform RECOVERY,
    which reads vertices and does not care about the rest of the file.
    Nothing that writes to a disc should call them.
    """
    return _transform_bound(
        raw, lambda x, y, z: (x + dx, y + dy, z + dz),
        lambda lo, hi: [(lo[0] + dx, lo[1] + dy, lo[2] + dz),
                        (hi[0] + dx, hi[1] + dy, hi[2] + dz)],
        1.0)


def scale_bound(raw, sx, sy=None, sz=None, about=None):
    """Same warning as `translate_bound`: this does NOT move a shell
    correctly. Use `boundmove` for anything that reaches a disc."""
    sy = sx if sy is None else sy
    sz = sx if sz is None else sz
    import struct
    lo = struct.unpack_from("<3f", raw, BND_MIN)
    hi = struct.unpack_from("<3f", raw, BND_MAX)
    cx, cy, cz = about or tuple((a + b) / 2.0 for a, b in zip(lo, hi))

    def f(x, y, z):
        return (cx + (x - cx) * sx, cy + (y - cy) * sy, cz + (z - cz) * sz)
    return _transform_bound(
        raw, f, lambda l, h: [f(*c) for c in _box_corners(list(l), list(h))],
        max(abs(sx), abs(sy), abs(sz)))


def rotate_bound_y(raw, radians, about=None):
    """Same warning as `translate_bound`: this does NOT move a shell
    correctly. Use `boundmove` for anything that reaches a disc."""
    import math
    import struct
    cs, sn = math.cos(radians), math.sin(radians)
    lo = struct.unpack_from("<3f", raw, BND_MIN)
    hi = struct.unpack_from("<3f", raw, BND_MAX)
    cx, cy, cz = about or tuple((a + b) / 2.0 for a, b in zip(lo, hi))

    def f(x, y, z):
        return (cx + (x - cx) * cs + (z - cz) * sn, y,
                cz - (x - cx) * sn + (z - cz) * cs)
    return _transform_bound(
        raw, f, lambda l, h: [f(*c) for c in _box_corners(list(l), list(h))],
        1.0)


def bound_centre(bound):
    """Centre of a decoded BoundMesh, for scaling and rotating about it."""
    return tuple((a + b) / 2.0
                 for a, b in zip(bound.aabb_min, bound.aabb_max))


# --------------------------------------------------------- material tint

MATERIAL_BLOCK = 68


def set_material_colour(raw, offset, rgba):
    """Rewrite a part's base colour. Returns new bytes, the same length.

    A material block is a fixed 68 bytes at an offset the part record names,
    and only the first four floats are touched. Nothing about the chunk's
    size, its offsets or its VIF packets changes, so this is the safest kind
    of edit in the toolkit -- it cannot overflow an archive slot, by
    construction.

    Values are clamped to [0,1]. Every one of the 23,042 material blocks on
    the disc holds sixteen floats in that range, and it is also the range the
    decoder validates before believing a block is a material at all -- so
    writing outside it would make the surface's own material unreadable.
    """
    import struct
    if offset <= 0 or offset + MATERIAL_BLOCK > len(raw):
        raise NotEditable("material offset %d is outside this chunk" % offset)
    if len(rgba) != 4:
        raise NotEditable("need four components (r, g, b, a)")
    out = bytearray(raw)
    struct.pack_into("<4f", out, offset,
                     *[min(1.0, max(0.0, float(v))) for v in rgba])
    return bytes(out)


def material_colour(raw, offset):
    """The base colour currently stored at `offset`, or None."""
    import struct
    if offset <= 0 or offset + MATERIAL_BLOCK > len(raw):
        return None
    vals = struct.unpack_from("<16f", raw, offset)
    if not all(0.0 <= v <= 1.0 for v in vals):
        return None
    return list(vals[:4])


# ------------------------------------------------------------ OBJ bridge
#
# The round trip to Blender and back. Only POSITIONS come back -- the packet's
# triangle strips, UVs, vertex colours, ADC bits and texture bindings all stay
# exactly as the game authored them. That is a deliberate limit, not an
# oversight: rebuilding a strip means re-serialising the VIF packet, which
# changes its length, which can overflow a fixed archive slot.
#
# So this supports sculpting and reshaping, not retopology. Move vertices as
# far as you like; add or delete one and the import is refused.

OBJ_HEADER = "# RDR Modding Studio"


def to_obj(mesh, name, meta=None):
    """One mesh as Wavefront OBJ, in the packet's own vertex order.

    The vertex order IS the contract. Blender preserves it as long as you do
    not add, delete or merge vertices, so the file that comes back can be
    matched up index for index. The header records where it came from so an
    import into the wrong chunk can be caught rather than silently corrupting
    a different building.
    """
    skinned = any(q is not None for q in (mesh.get("quant") or []))
    out = [OBJ_HEADER + (" -- character part" if skinned else " -- level surface"),
           "# %s" % name]
    for k, v in sorted((meta or {}).items()):
        out.append("# %s: %s" % (k, v))
    out.append("# vertices: %d   triangles: %d"
               % (len(mesh["verts"]), len(mesh["tris"])))
    out.append("#")
    out.append("# Move these vertices freely. Do NOT add, delete, merge or")
    out.append("# reorder them -- the importer matches by index and will")
    out.append("# refuse a file whose count has changed.")
    if skinned:
        p = precision(mesh)
        out.append("#")
        out.append("# This is a CHARACTER part, in bone-local space -- the")
        out.append("# standing figure you see in the tool is this data pushed")
        out.append("# through the skeleton. Positions are re-quantised on")
        out.append("# import, so they snap to a %.6f m grid, and a vertex"
                   % (p["step"] if p else 0))
        out.append("# further than %.1f m from the origin will clamp."
                   % (p["range"] / 2 if p else 0))
    out.append("o %s" % name)
    # %.9g, not %.6f. A 32-bit float needs nine significant decimal digits to
    # survive a text round trip; at six, every single vertex comes back
    # slightly moved and an "I changed nothing" import rewrites the whole
    # packet. With nine, exporting and re-importing is byte-identical.
    for x, y, z in mesh["verts"]:
        out.append("v %.9g %.9g %.9g" % (x, y, z))
    for u, v in (mesh.get("uvs") or [])[:len(mesh["verts"])]:
        out.append("vt %.9g %.9g" % (u, 1.0 - v))
    has_uv = len(mesh.get("uvs") or []) >= len(mesh["verts"])
    for a, b, c in mesh["tris"]:
        if has_uv:
            out.append("f %d/%d %d/%d %d/%d"
                       % (a + 1, a + 1, b + 1, b + 1, c + 1, c + 1))
        else:
            out.append("f %d %d %d" % (a + 1, b + 1, c + 1))
    return "\n".join(out) + "\n"


def from_obj(text):
    """Positions out of an OBJ, in file order. Raises on anything malformed."""
    verts = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.startswith("v "):
            continue
        bits = line.split()
        if len(bits) < 4:
            raise NotEditable("line %d: 'v' with fewer than 3 numbers" % n)
        try:
            verts.append((float(bits[1]), float(bits[2]), float(bits[3])))
        except ValueError:
            raise NotEditable("line %d: could not read a position" % n)
    if not verts:
        raise NotEditable("no vertices in this file")
    return verts


def apply_obj(raw, mesh, verts):
    """Write imported positions into the packet. Count must match exactly."""
    have, want = len(verts), len(mesh["verts"])
    if have != want:
        raise NotEditable(
            "this OBJ has %d vertices but the surface has %d. Vertices were "
            "added, deleted or merged -- the importer matches by index, so it "
            "cannot tell which is which. Re-export and move only." %
            (have, want))
    it = iter(verts)
    return transform(raw, mesh, lambda x, y, z: next(it))
