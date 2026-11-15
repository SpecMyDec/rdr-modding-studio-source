"""Export a whole level as a model set -- Wavefront OBJ (+ MTL + PNGs).

The audience is somebody taking a Red Dead Revolver level somewhere else: into
Blender, or into another game's toolchain as a map. That is a different job
from `geomedit`'s single-surface round trip, and it wants three things this
used to get wrong.

**Every area, not one.** An archive is not a level. TOMB4 carries Brimstone
plus six interiors as seven sibling `lvl_*.rsc` bundles, and ROYDUSK6 carries
three; exporting only the best name match dropped 6 of 7 areas on the floor.
`rsc.level_areas` enumerates them and every one is merged into the same file
under its own name.

**Tiles laid out, not stacked.** TRAIN, TRAINGOO, MSN and MSNCYN are assembled
at runtime from tiles, and every tile's geometry is authored in its own local
space around zero. Exported raw they pile up on top of each other. `Level`
already reconstructs a layout by chaining each tile's own `_top`/`_bot` marker
splines, and the chunks a tile's `.znes`/`.bnds` nodes reference are exactly
the ones that layout applies to. MEASURED on TRAINGOO: the 161 tile-owned
chunks span X -3.7..94.4 with no layout -- one tile's width -- and
X -3.7..1980.0 with it, against a chain of 21 steps summing to 1890. It is a
valid layout rather than *the* layout (the game shuffles the set at runtime),
which is exactly what `Level._tile_layout` says about itself.

A tile's chunk can be referenced from more than one tile, so it is written
once per placement: TRAINGOO's 161 tile chunks make 247 placed instances.
Measured across all four tiled levels, no chunk is referenced both from a tile
and from a fixed file, so there is no rule needed to choose between them.

**Materials resolved per surface, not per model.** A container MOD0 states a
texture *per sub-mesh*, and `rsc.tex_slot_map` reads it exactly. The old code
used `model_texture_map`, which is the nearest-preceding-TEX0 heuristic and can
only put one texture on a whole building.

What is NOT authored anywhere: where two sibling areas sit relative to each
other. TOMB4's shop interiors are each modelled around their own origin and
overlap -- the clothes shop's box sits wholly inside the bar's. Merging them at
their stored coordinates interpenetrates them. So the FIRST area is never moved
(its coordinates are the game's, exactly), and any later area whose box would
intersect what is already placed is shifted along +X to sit clear. Each offset
is written into the OBJ header and returned in the stats so it can be undone.
`layout="native"` turns both the area offsets and the tile layout off and
writes every chunk at its stored coordinates.

**Node matrices applied, not just tile offsets.** This used to write every
chunk at its decoded vertex coordinates plus the tile layout offset, and drop
the placement matrix its node carries. That was right for the overwhelming
majority and catastrophic for the rest, because a few hundred chunks are
authored in LOCAL space and positioned only by the matrix. MEASURED across all
35 LVLS_SNG archives, 9406 chunk-referencing nodes:

    8945 + 188   identity rotation, zero translation  -- no-op either way
      51         identity rotation, non-zero translation
     222         NON-IDENTITY rotation (epsilon 1e-4 per element)

All 222 are `.znes` zone nodes; every `.bnds` collision node in the game is
identity with a zero translation, which is what makes collision usable as the
world-space reference below. The worst deviation is RANCH `zone_glassBC_01` at
3.02, whose matrix is `[-2.025 0 0 | 0 1 0 | 0 0 -1]` -- a 180-degree turn
about Y with a 2x scale on X, so these are not pure rotations and the code
below applies the general 3x3 rather than assuming orthonormality.

Two oracles the data itself provides settled both questions.

*That the matrix is a real placement.* Without it, distinct chunks pile up:
5728 pairs of DIFFERENTLY-NAMED chunks landed within 0.5 units of each other
across nine levels, 3003 of them in SHER70 alone, where 88 separate window,
sign and pillar models all sat on the world origin. With the matrix applied
that falls to 64. No level stacks 88 named objects in one cubic metre.

*Which convention.* The nine numbers are stored ROW-major but the engine reads
them as ROW vectors -- `v * M`, not `M * v` -- so the transform is the
TRANSPOSE of the naive reading. Restricted to the 186 nodes whose matrix is
asymmetric (the only ones where the two conventions differ at all), mean
distance from surface sample points to the nearest collision vertex is 0.121
transposed against 0.401 untransposed, and the transpose is closer on 142
objects against 5. Whole-level means: 1.370 with no transform, 1.124 reading
the matrix naively, 0.900 transposed.

No chunk anywhere is referenced by both a trivial-matrix node and a
non-trivial one (measured: 0 of 7476), so there is never a choice to make
about which placement a chunk gets.

What this does NOT cover: whether the scale factors baked into these matrices
are also meant as UV or normal scale, and the four tiled levels, whose
placement went through the tile layout and is unaffected -- none of their
chunk-referencing nodes carries a non-identity matrix.
"""

import hashlib
import os

from . import scene
from . import texture as T
from .rsc import Rsc, area_stem, find_level_areas, find_rsc_names

# Per-element tolerance for calling a stored matrix the identity, and for
# calling a stored translation zero. The files are written as six-decimal
# text, so anything above these is authored intent rather than round-off.
_ROT_EPS = 1e-4
_POS_EPS = 1e-3
_IDENTITY = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)

# Gap left between an area and whatever is already placed, when one has to be
# moved: a fraction of the placed span, floored so small levels still separate.
_GAP_FRAC = 0.06
_GAP_MIN = 20.0

COLLISION_MTL = "collision"


def _safe(name):
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in name)


def _box_of(verts, lo=None, hi=None):
    lo = lo if lo is not None else [1e30] * 3
    hi = hi if hi is not None else [-1e30] * 3
    for v in verts:
        for i in range(3):
            if v[i] < lo[i]:
                lo[i] = v[i]
            if v[i] > hi[i]:
                hi[i] = v[i]
    return lo, hi


def _overlaps(a_lo, a_hi, b_lo, b_hi):
    return all(a_lo[i] <= b_hi[i] and b_lo[i] <= a_hi[i] for i in range(3))


# --------------------------------------------------------------- materials

class _Materials:
    """Every material in the export, deduplicated by decoded image bytes.

    Two areas of one archive nearly always share textures -- Brimstone's six
    shops all use the same boards -- and the old per-area numbering wrote each
    copy again under a new name. Keying the PNG on the sha1 of its encoded
    bytes means a repeat costs nothing and `map_Kd` still resolves.
    """

    def __init__(self, tex_dir, want_textures):
        self.tex_dir = tex_dir
        self.want = want_textures
        self.defs = {}            # material name -> dict(kd, d, png)
        self.order = []           # material names, creation order
        self._mat = {}            # (area, slot, tint) -> material name
        self._slot_png = {}       # (area, slot) -> png filename or None
        self._png_by_hash = {}
        self._png_names = set()
        self.png_written = 0
        self.png_deduped = 0
        self.decode_failed = 0
        # png filename -> lowest alpha byte in the decoded image. glTF has to
        # be told whether a material is alpha-tested, and the image itself is
        # the only thing that knows: guessing OPAQUE hides a fence's cutout,
        # guessing BLEND makes every opaque surface sort against every other.
        self.png_alpha = {}

        self.defs[COLLISION_MTL] = dict(kd=(0.55, 0.50, 0.42), d=1.0, png=None)
        self.order.append(COLLISION_MTL)

    def _png(self, area, rsc, table, slot):
        key = (area, slot)
        if key in self._slot_png:
            return self._slot_png[key]
        png = None
        if self.want and 0 <= slot < len(table):
            _, info, mip, pal = table[slot]
            try:
                res = T.decode(rsc.raw(mip.index),
                               T.parse_palette(rsc.raw(pal.index)))
            except Exception:                                  # noqa: BLE001
                res = None
            if res:
                w, h, rgba = res
                blob = T.encode_png(w, h, rgba)
                digest = hashlib.sha1(blob).hexdigest()
                if digest in self._png_by_hash:
                    png = self._png_by_hash[digest]
                    self.png_deduped += 1
                else:
                    base = _safe(info.get("name") or "") or "tex_%03d" % slot
                    cand, n = base + ".png", 2
                    while cand in self._png_names:
                        cand, n = "%s__%d.png" % (base, n), n + 1
                    os.makedirs(self.tex_dir, exist_ok=True)
                    with open(os.path.join(self.tex_dir, cand), "wb") as fh:
                        fh.write(blob)
                    self._png_names.add(cand)
                    self._png_by_hash[digest] = cand
                    self.png_alpha[cand] = min(rgba[3::4]) if rgba else 255
                    self.png_written += 1
                    png = cand
            else:
                self.decode_failed += 1
        self._slot_png[key] = png
        return png

    @staticmethod
    def _tint(colour):
        """A part's base colour, or None when it is plain white."""
        if not colour or len(colour) < 4:
            return None
        rgb = tuple(round(min(1.0, max(0.0, c)), 3) for c in colour[:3])
        a = round(min(1.0, max(0.0, colour[3])), 3)
        if rgb == (1.0, 1.0, 1.0) and a == 1.0:
            return None
        return rgb + (a,)

    def resolve(self, area, rsc, table, slot, colour):
        """-> the material name this (texture slot, tint) pair draws with."""
        tint = self._tint(colour)
        key = (area, slot, tint)
        if key in self._mat:
            return self._mat[key]

        png = self._png(area, rsc, table, slot) if slot is not None else None
        if slot is not None and 0 <= slot < len(table):
            base = _safe(table[slot][1].get("name") or "") or "tex_%03d" % slot
        else:
            base = "untextured"
        name, n = base, 2
        while name in self.defs:
            name, n = "%s__%d" % (base, n), n + 1

        kd, d = (1.0, 1.0, 1.0), 1.0
        if tint:
            # A tint of pure black would export the surface as an unlit slab,
            # which reads as a broken export rather than as the game's intent,
            # so only a non-black tint is carried into Kd. Alpha is carried
            # only when it is a real partial transparency -- 117 records on the
            # disc are transparent black, and honouring those would delete
            # geometry a map author came here for.
            if max(tint[:3]) > 0.0:
                kd = tint[:3]
            if 0.05 <= tint[3] < 1.0:
                d = tint[3]
        self.defs[name] = dict(kd=kd, d=d, png=png)
        self.order.append(name)
        self._mat[key] = name
        return name

    def write(self, path, header):
        with open(path, "w") as m:
            m.write("# %s\n\n" % header)
            for name in self.order:
                r = self.defs[name]
                m.write("newmtl %s\n" % name)
                m.write("Kd %.4f %.4f %.4f\n" % tuple(r["kd"]))
                m.write("Ka 0 0 0\nKs 0 0 0\nillum 1\n")
                if r["d"] < 1.0:
                    m.write("d %.4f\n" % r["d"])
                if r["png"]:
                    m.write("map_Kd textures/%s\n" % r["png"])
                    m.write("map_d textures/%s\n" % r["png"])
                m.write("\n")


# --------------------------------------------------------------- placement

def _node_matrix(node):
    """(rot9, trans3) for a node, or (None, None) when it places nothing.

    `rot9` is returned already TRANSPOSED into the column-vector form
    `_apply` wants, so the transpose is done once, here, and the rest of the
    file never has to remember the convention. See the module docstring for
    the measurement that establishes it.

    Returns (None, None) for a matrix that is the identity with a zero
    translation, which is the overwhelming majority -- callers use that to
    keep sharing one decoded vertex list between instances instead of baking
    a transformed copy per placement.
    """
    rot = getattr(node, "rot", None)
    raw = [float(n.value) for n in rot[:9]] if rot and len(rot) >= 9 else None
    pos = getattr(node, "pos", None)
    t = ([float(n.value) for n in pos[:3]] if pos and len(pos) >= 3
         else [0.0, 0.0, 0.0])
    spun = raw is not None and any(abs(a - b) > _ROT_EPS
                                   for a, b in zip(raw, _IDENTITY))
    moved = any(abs(x) > _POS_EPS for x in t)
    if not spun and not moved:
        return None, None
    if raw is None or not spun:
        return None, tuple(t)
    # Stored row-major, read as row vectors: transpose.
    return (raw[0], raw[3], raw[6],
            raw[1], raw[4], raw[7],
            raw[2], raw[5], raw[8]), tuple(t)


def _apply(verts, rot9, trans3):
    """Place a chunk's vertices: rotate about the chunk origin, then translate.

    The order is not a preference. These matrices carry scale as well as
    rotation, so `R*v + t` and `R*(v + t)` differ by the scale factor, and
    only the former puts RANCH's 2x-scaled window panes on their frames.
    """
    tx, ty, tz = trans3 or (0.0, 0.0, 0.0)
    if rot9 is None:
        return [(v[0] + tx, v[1] + ty, v[2] + tz) for v in verts]
    a, b, c, d, e, f, g, h, i = rot9
    return [(a * v[0] + b * v[1] + c * v[2] + tx,
             d * v[0] + e * v[1] + f * v[2] + ty,
             g * v[0] + h * v[1] + i * v[2] + tz) for v in verts]


def _placements(game, archive, area_name):
    """chunk index -> [(origin, node_name, rot9, trans3), ...], one per instance.

    `origin` is the tile display offset `Level` works out; it is (0,0,0) for
    everything in a level that is not tiled. `rot9`/`trans3` are the node's own
    placement matrix, or (None, None) when it is the identity with a zero
    translation. The node also supplies the artist's name for the chunk, which
    is what makes the export sortable.

    Two nodes are distinct placements when anything about the transform
    differs, not just the tile offset -- deduplicating on the offset alone
    would collapse two genuinely different placements of one chunk into one.

    A failure here costs names, the tile layout and the node matrices, not the
    geometry, so it is swallowed: an area whose manifest will not parse should
    still export, just unplaced.
    """
    try:
        from .level import Level
        lv = Level(game, archive, area=area_name)
    except Exception:                                          # noqa: BLE001
        return {}
    out = {}
    for n in lv.nodes:
        c = scene.chunk_of(n)
        if c is None:
            continue
        raw = getattr(n, "origin", None) or (0.0, 0.0, 0.0)
        origin = tuple(round(float(x), 4) for x in raw)
        rot9, trans3 = _node_matrix(n)
        seen = out.setdefault(c, [])
        key = (origin, rot9, trans3)
        if not any((o, r, t) == key for o, _nm, r, t in seen):
            seen.append((origin, getattr(n, "name", None), rot9, trans3))
    return out


def _open_areas(dave, level, want=None):
    """[(rsc_name, Rsc), ...] for every area bundle that holds geometry."""
    names = find_level_areas(dave, level) or find_rsc_names(dave, level)
    if want:
        keep = {w.lower() for w in want}
        names = [n for n in names
                 if n.lower() in keep or area_stem(n).lower() in keep]
    out = []
    for n in names:
        try:
            r = Rsc(dave.read(n), n)
        except Exception:                                      # noqa: BLE001
            continue
        if r.tally().get("BND0") or r.tally().get("MOD0"):
            out.append((n, r))
    if not out:
        # Last resort: any bundle in the archive with geometry, which is how a
        # dev archive with no `lvl_*` naming still exports something.
        for n in find_rsc_names(dave, level):
            try:
                r = Rsc(dave.read(n), n)
            except Exception:                                  # noqa: BLE001
                continue
            if r.tally().get("BND0") or r.tally().get("MOD0"):
                out.append((n, r))
                break
    return out


# ---------------------------------------------------------------- gathering

def _gather_area(rsc, area_tag, mats, places, collision, surfaces, tiles,
                 counters):
    """Decode one area into a list of placed, named, materialled objects.

    Each object is
        dict(name, kind, verts, uvs, runs=[(material, [(a,b,c), ...]), ...],
             offset)
    with `verts` shared between the instances of one chunk -- a tile chunk
    placed 4 times decodes once.
    """
    table = rsc.texture_table()
    slotmap = rsc.tex_slot_map()
    fallback = rsc.model_texture_map()
    objects = []

    def instances(index):
        got = places.get(index) or [((0.0, 0.0, 0.0), None, None, None)]
        if not tiles:
            got = [((0.0, 0.0, 0.0), got[0][1], got[0][2], got[0][3])]
        return got

    def placed(verts, rot9, trans3):
        """Vertices for one instance, SHARING the decoded list when it can.

        A chunk placed several times decodes once, and the untransformed case
        must keep returning the very same list object: `write_glb` instances
        meshes by `id(verts)`, so copying here would silently multiply the
        glTF's mesh count.
        """
        if rot9 is None and trans3 is None:
            return verts
        counters["placed_rotated" if rot9 is not None
                 else "placed_translated"] += 1
        return _apply(verts, rot9, trans3)

    if collision:
        for index, mesh in sorted(rsc.bounds().items()):
            if not mesh.tris:
                continue
            runs = [(COLLISION_MTL, mesh.tris)]
            for origin, node_name, rot9, trans3 in instances(index):
                nm = node_name or "bound_%d" % index
                objects.append(dict(
                    name="%s__collision__%s" % (area_tag, _safe(nm)),
                    kind="collision", verts=placed(mesh.verts, rot9, trans3),
                    uvs=None, colors=None, runs=runs, offset=origin))
            counters["collision_meshes"] += 1

    if surfaces:
        for c in rsc.chunks:
            if c.tag != "MOD0":
                continue
            try:
                mesh = rsc.model(c.index)
            except Exception:                                  # noqa: BLE001
                continue
            if not mesh or not mesh["tris"]:
                continue
            tris = mesh["tris"]
            uvs = mesh.get("uvs") or []
            uvs = uvs if len(uvs) == len(mesh["verts"]) else None
            # PS2 vertex colour: 0x80 is full scale, authored against a 2x
            # overbright convention -- the same normalisation the viewport
            # uses. This is the ONLY thing an explicitly untextured surface
            # carries, and 15.3% of the triangles measured across six levels
            # are exactly that, so dropping it loses real shading.
            #
            # The fourth channel is written as 255, not as the stored value.
            # Nothing in this codebase has established what vertex alpha means
            # here -- the viewport reads three channels and ignores it -- and
            # on an alpha-masked material a wrong 0 would punch holes in the
            # map. Declining to claim it costs nothing; guessing it deletes
            # geometry.
            cols = mesh.get("colors") or []
            cols = ([(min(255, c4[0] * 2), min(255, c4[1] * 2),
                      min(255, c4[2] * 2), 255)
                     for c4 in cols[:len(mesh["verts"])]]
                    if len(cols) >= len(mesh["verts"]) else None)

            # One run per contiguous stretch of triangles that share a
            # texture and a tint -- the part table states this outright, so a
            # building carries its wall texture and its roof texture at once.
            runs = []
            ranges = mesh.get("part_ranges") or ()
            if ranges:
                for res, start, count, colour in ranges:
                    if count <= 0:
                        continue
                    if res:
                        slot = slotmap.get(res)
                        if slot is None:
                            counters["tris_unresolved"] += count
                    else:
                        slot = None            # explicit 0 == untextured
                        counters["tris_untextured"] += count
                    mtl = mats.resolve(area_tag, rsc, table, slot, colour)
                    seg = tris[start:start + count]
                    if runs and runs[-1][0] == mtl:
                        runs[-1][1].extend(seg)
                    else:
                        runs.append((mtl, list(seg)))
            else:
                # Not a container: one display list, one texture, and the only
                # binding available is the nearest-preceding-TEX0 guess.
                slot = fallback.get(c.index)
                if slot is None:
                    counters["tris_unresolved"] += len(tris)
                counters["tris_nocontainer"] += len(tris)
                runs.append((mats.resolve(area_tag, rsc, table, slot, None),
                             list(tris)))

            for origin, node_name, rot9, trans3 in instances(c.index):
                nm = node_name or "MOD0_%d" % c.index
                objects.append(dict(
                    name="%s__surface__%s" % (area_tag, _safe(nm)),
                    kind="surface", verts=placed(mesh["verts"], rot9, trans3),
                    uvs=uvs, colors=cols, runs=runs, offset=origin))
            counters["surface_meshes"] += 1

    return objects


def _unique_names(objects):
    """OBJ object names have to be distinct for an importer to keep them apart."""
    used = {}
    for o in objects:
        base = o["name"]
        n = used.get(base, 0) + 1
        used[base] = n
        if n > 1:
            o["name"] = "%s_%03d" % (base, n)
    return objects


def _layout(area_objects):
    """Per-area translations that stop sibling areas interpenetrating.

    Returns [(offset, lo, hi), ...] parallel with `area_objects`. The first
    area is never moved.
    """
    out = []
    plo = phi = None
    for objs in area_objects:
        lo, hi = [1e30] * 3, [-1e30] * 3
        for o in objs:
            lo, hi = _box_of([(v[0] + o["offset"][0], v[1] + o["offset"][1],
                               v[2] + o["offset"][2]) for v in o["verts"]],
                             lo, hi)
        if lo[0] > 1e29:
            out.append(((0.0, 0.0, 0.0), None, None))
            continue
        if plo is None:
            off = (0.0, 0.0, 0.0)
        elif _overlaps(lo, hi, plo, phi):
            gap = max(_GAP_MIN, _GAP_FRAC * (phi[0] - plo[0]))
            off = (phi[0] + gap - lo[0], 0.0, 0.0)
        else:
            off = (0.0, 0.0, 0.0)
        nlo = [lo[i] + off[i] for i in range(3)]
        nhi = [hi[i] + off[i] for i in range(3)]
        plo = nlo if plo is None else [min(plo[i], nlo[i]) for i in range(3)]
        phi = nhi if phi is None else [max(phi[i], nhi[i]) for i in range(3)]
        out.append((off, nlo, nhi))
    return out


# ------------------------------------------------------------------ writing

def _write_obj(path, mtl_name, header_lines, area_objects, area_offsets):
    vcount = uvcount = 0
    verts = tris = 0
    with open(path, "w") as f:
        for line in header_lines:
            f.write("# %s\n" % line)
        f.write("\nmtllib %s\n\n" % mtl_name)
        group = None
        for objs, (aoff, _lo, _hi) in zip(area_objects, area_offsets):
            for o in objs:
                dx = aoff[0] + o["offset"][0]
                dy = aoff[1] + o["offset"][1]
                dz = aoff[2] + o["offset"][2]
                g = o["name"].split("__")[0] + "_" + o["kind"]
                if g != group:
                    f.write("g %s\n" % g)
                    group = g
                f.write("o %s\n" % o["name"])
                for v in o["verts"]:
                    f.write("v %.5f %.5f %.5f\n"
                            % (v[0] + dx, v[1] + dy, v[2] + dz))
                have_uv = o["uvs"] is not None
                if have_uv:
                    for uv in o["uvs"]:
                        # OBJ's V axis runs opposite to the game's.
                        f.write("vt %.5f %.5f\n" % (uv[0], 1.0 - uv[1]))
                for mtl, run in o["runs"]:
                    if not run:
                        continue
                    f.write("usemtl %s\n" % mtl)
                    for a, b, c in run:
                        if have_uv:
                            f.write("f %d/%d %d/%d %d/%d\n" % (
                                vcount + a + 1, uvcount + a + 1,
                                vcount + b + 1, uvcount + b + 1,
                                vcount + c + 1, uvcount + c + 1))
                        else:
                            f.write("f %d %d %d\n" % (vcount + a + 1,
                                                      vcount + b + 1,
                                                      vcount + c + 1))
                    tris += len(run)
                vcount += len(o["verts"])
                verts += len(o["verts"])
                if have_uv:
                    uvcount += len(o["uvs"])
    return verts, tris


# ------------------------------------------------------------------- glTF

# glTF 2.0 binary, hand-rolled, no dependencies -- the same choice
# `texture.py` makes for PNG. An OBJ can only say "this face uses a named
# material"; a .glb carries the images, the alpha mode, the per-vertex colour
# and the scene graph in one file, which is what another game's importer
# actually wants. Written from the same gathered objects as the OBJ, so the
# two agree by construction rather than by a second decode.
#
# NO axis or handedness conversion is applied. The coordinates are the game's,
# byte-for-byte the ones in the .obj. Rotating one and not the other would be
# a worse failure than leaving both as they are and saying so.

_GLB_MAGIC = 0x46546C67
_JSON_CHUNK = 0x4E4F534A
_BIN_CHUNK = 0x004E4942
_F32, _U8, _U32 = 5126, 5121, 5125
_ARRAY_BUFFER, _ELEMENT_ARRAY_BUFFER = 34962, 34963


class _GlbBuffer:
    def __init__(self):
        self.buf = bytearray()
        self.views = []
        self.accessors = []

    def view(self, data, target=None, stride=None):
        off = len(self.buf)
        self.buf.extend(data)
        self.buf.extend(b"\x00" * ((-len(self.buf)) % 4))
        v = dict(buffer=0, byteOffset=off, byteLength=len(data))
        if target:
            v["target"] = target
        if stride:
            v["byteStride"] = stride
        self.views.append(v)
        return len(self.views) - 1

    def accessor(self, view, comp, count, kind, mn=None, mx=None,
                 normalized=False):
        a = dict(bufferView=view, componentType=comp, count=count, type=kind)
        if mn is not None:
            a["min"], a["max"] = mn, mx
        if normalized:
            a["normalized"] = True
        self.accessors.append(a)
        return len(self.accessors) - 1


def write_glb(path, area_objects, area_offsets, mats, tex_dir, header_lines):
    """Write a self-contained glTF 2.0 binary. -> stats dict."""
    import json
    import struct

    b = _GlbBuffer()
    images, textures, gmaterials = [], [], []
    png_tex = {}
    mat_index = {}

    def texture_for(png):
        if png in png_tex:
            return png_tex[png]
        full = os.path.join(tex_dir, png)
        try:
            with open(full, "rb") as fh:
                blob = fh.read()
        except OSError:
            png_tex[png] = None
            return None
        v = b.view(blob)
        images.append(dict(bufferView=v, mimeType="image/png",
                           name=png.rsplit(".", 1)[0]))
        textures.append(dict(sampler=0, source=len(images) - 1))
        png_tex[png] = len(textures) - 1
        return png_tex[png]

    for name in mats.order:
        r = mats.defs[name]
        pbr = dict(baseColorFactor=[r["kd"][0], r["kd"][1], r["kd"][2],
                                    r["d"]],
                   metallicFactor=0.0, roughnessFactor=0.9)
        mode = "OPAQUE"
        if r["png"]:
            ti = texture_for(r["png"])
            if ti is not None:
                pbr["baseColorTexture"] = dict(index=ti, texCoord=0)
                # Measured from the decoded image, not assumed: a texture with
                # a fully transparent pixel is a cutout (fences, foliage) and
                # has to be masked or it draws as a solid card.
                if mats.png_alpha.get(r["png"], 255) < 32:
                    mode = "MASK"
        if r["d"] < 1.0:
            mode = "BLEND"
        m = dict(name=name, pbrMetallicRoughness=pbr, doubleSided=True,
                 alphaMode=mode)
        if mode == "MASK":
            m["alphaCutoff"] = 0.5
        gmaterials.append(m)
        mat_index[name] = len(gmaterials) - 1

    meshes, nodes = [], []
    mesh_cache = {}
    nprim = nvert = ntri = 0

    for objs, (aoff, _lo, _hi) in zip(area_objects, area_offsets):
        for o in objs:
            # A tile chunk placed several times shares one decoded mesh, so it
            # is written once and instanced by node translation.
            key = (id(o["verts"]), id(o["runs"]))
            mi = mesh_cache.get(key)
            if mi is None:
                verts = o["verts"]
                lo = [min(v[i] for v in verts) for i in range(3)]
                hi = [max(v[i] for v in verts) for i in range(3)]
                pv = b.view(b"".join(struct.pack("<3f", *v) for v in verts),
                            _ARRAY_BUFFER)
                attrs = dict(POSITION=b.accessor(pv, _F32, len(verts), "VEC3",
                                                 lo, hi))
                if o["uvs"] is not None:
                    uv = b.view(b"".join(struct.pack("<2f", u, 1.0 - v)
                                         for u, v in o["uvs"]),
                                _ARRAY_BUFFER)
                    attrs["TEXCOORD_0"] = b.accessor(uv, _F32, len(o["uvs"]),
                                                     "VEC2")
                if o.get("colors"):
                    cv = b.view(b"".join(struct.pack("<4B", *c)
                                         for c in o["colors"]),
                                _ARRAY_BUFFER)
                    attrs["COLOR_0"] = b.accessor(cv, _U8, len(o["colors"]),
                                                  "VEC4", normalized=True)
                prims = []
                for mtl, run in o["runs"]:
                    if not run:
                        continue
                    iv = b.view(b"".join(struct.pack("<3I", *t) for t in run),
                                _ELEMENT_ARRAY_BUFFER)
                    prims.append(dict(
                        attributes=attrs, mode=4,
                        indices=b.accessor(iv, _U32, len(run) * 3, "SCALAR"),
                        material=mat_index.get(mtl, 0)))
                    ntri += len(run)
                if not prims:
                    continue
                meshes.append(dict(name=o["name"], primitives=prims))
                mi = len(meshes) - 1
                mesh_cache[key] = mi
                nvert += len(verts)
                nprim += len(prims)
            t = [aoff[i] + o["offset"][i] for i in range(3)]
            n = dict(name=o["name"], mesh=mi)
            if any(t):
                n["translation"] = t
            nodes.append(n)

    # TWO BRANCHES, so one action hides all the collision or all the art.
    #
    # A flat scene dropped 600 objects at the top of Blender's outliner with
    # nothing to grab. The names said __collision__, but there was no way to
    # switch the whole set off and work on the other -- which is the thing
    # this export is for. glTF has no notion of a collection, but it does have
    # parenting, and Blender turns a parent node into an Empty, so the two
    # branches at least EXIST and can be reasoned about.
    #
    # The grouping is necessary and not sufficient. Nothing in Blender hides a
    # branch through parenting -- not the eye icon, not H, both of which act on
    # the objects you selected and stop there. Only a collection carries a
    # whole branch, and glTF cannot express one, so _write_blender_script ships
    # a script that turns these two parents into collections after import.
    # Without it the user gets two tidy branches and no switch.
    #
    # The parents are pure grouping -- no mesh, no transform -- so every child
    # keeps the translation it already had and nothing moves.
    surf_kids, coll_kids = [], []
    for i, n in enumerate(nodes):
        (coll_kids if "__collision__" in n.get("name", "")
         else surf_kids).append(i)
    roots = []
    if surf_kids:
        nodes.append(dict(name="SURFACES", children=surf_kids))
        roots.append(len(nodes) - 1)
    if coll_kids:
        nodes.append(dict(name="COLLISION", children=coll_kids))
        roots.append(len(nodes) - 1)
    if not roots:
        roots = list(range(len(nodes)))

    doc = dict(
        asset=dict(version="2.0", generator="rdrstudio",
                   copyright=" | ".join(header_lines[:2])),
        scene=0, scenes=[dict(nodes=roots)],
        nodes=nodes, meshes=meshes, materials=gmaterials,
        accessors=b.accessors, bufferViews=b.views,
        buffers=[dict(byteLength=len(b.buf))],
    )
    if textures:
        doc.update(images=images, textures=textures,
                   samplers=[dict(wrapS=10497, wrapT=10497, magFilter=9729,
                                  minFilter=9987)])

    js = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    js += b" " * ((-len(js)) % 4)
    binblob = bytes(b.buf)
    total = 12 + 8 + len(js) + 8 + len(binblob)
    with open(path, "wb") as f:
        f.write(struct.pack("<III", _GLB_MAGIC, 2, total))
        f.write(struct.pack("<II", len(js), _JSON_CHUNK))
        f.write(js)
        f.write(struct.pack("<II", len(binblob), _BIN_CHUNK))
        f.write(binblob)
    return dict(path=path, bytes=total, meshes=len(meshes), nodes=len(nodes),
                primitives=nprim, verts=nvert, tris=ntri,
                materials=len(gmaterials), textures=len(textures))


# --------------------------------------------------------------------- api

def export_level(game, archive, out_dir, collision=True, surfaces=True,
                 textures=True, areas=None, layout="auto", glb=False):
    """Write <level>.obj (+ .mtl + textures/) covering EVERY area, into out_dir.

    `areas`   restrict to these bundle names or area stems; None means all.
    `layout`  "auto" places tiles by their own sockets and shifts a sibling
              area that would interpenetrate; "native" writes every chunk at
              its stored coordinates and moves nothing.
    `glb`     also write <level>.glb, a self-contained glTF 2.0 binary.
    """
    d = game.archive(archive)
    level = archive.rsplit("/", 1)[-1]
    level = level[:-4] if level.lower().endswith(".dat") else level

    opened = _open_areas(d, level, areas)
    if not opened:
        raise ValueError("%s: no geometry bundle found" % archive)

    os.makedirs(out_dir, exist_ok=True)
    tex_dir = os.path.join(out_dir, "textures")
    obj_path = os.path.join(out_dir, "%s.obj" % level)
    mtl_path = os.path.join(out_dir, "%s.mtl" % level)

    place = layout != "native"
    mats = _Materials(tex_dir, textures)
    counters = dict(collision_meshes=0, surface_meshes=0, tris_untextured=0,
                    tris_unresolved=0, tris_nocontainer=0,
                    placed_rotated=0, placed_translated=0)

    area_objects, area_rows = [], []
    for name, rsc in opened:
        tag = _safe(area_stem(name))
        places = _placements(game, archive, name) if place else {}
        objs = _unique_names(_gather_area(rsc, tag, mats, places, collision,
                                          surfaces, place, counters))
        area_objects.append(objs)
        area_rows.append(dict(rsc=name, area=tag, chunks=len(rsc.chunks),
                              objects=len(objs),
                              verts=sum(len(o["verts"]) for o in objs),
                              tris=sum(len(r) for o in objs
                                       for _m, r in o["runs"])))

    offsets = _layout(area_objects) if place else \
        [((0.0, 0.0, 0.0), None, None)] * len(area_objects)
    for row, (off, lo, hi) in zip(area_rows, offsets):
        row["offset"] = list(off)
        row["min"] = [round(x, 3) for x in lo] if lo else None
        row["max"] = [round(x, 3) for x in hi] if hi else None

    head = ["Red Dead Revolver -- %s (%s)" % (level, archive),
            "exported by rdrstudio; layout=%s" % layout,
            "%d area(s):" % len(area_rows)]
    for row in area_rows:
        head.append("  %-16s %-22s objects %-5d verts %-7d tris %-7d "
                    "offset %.3f %.3f %.3f"
                    % (row["area"], row["rsc"], row["objects"], row["verts"],
                       row["tris"], row["offset"][0], row["offset"][1],
                       row["offset"][2]))
    head.append("offset is what THIS EXPORTER added so sibling areas do not")
    head.append("interpenetrate; subtract it to get the game's own coordinates.")
    head.append("The first area is never moved.")

    # Say outright what happened to the node matrices. An export that is
    # silent about this cannot be checked by the person opening it, and for
    # most levels the honest answer is "nothing, because there was nothing".
    head.append("")
    if not place:
        head.append("NODE MATRICES: NOT APPLIED. layout=native writes every chunk at "
                    "its stored")
        head.append("  vertex coordinates. Chunks authored in local space "
                    "(see below) will sit")
        head.append("  on the world origin rather than in position. Use "
                    "layout=auto for placed geometry.")
    else:
        rot = counters["placed_rotated"]
        tra = counters["placed_translated"]
        head.append("NODE MATRICES: APPLIED. Each chunk's placement node "
                    "carries a 3x3 matrix")
        head.append("  and a translation. The nine numbers are stored "
                    "row-major but read as ROW")
        head.append("  vectors, so the applied transform is the TRANSPOSE: "
                    "v' = transpose(M)*v + t,")
        head.append("  rotating about the chunk's own origin BEFORE "
                    "translating. The matrices carry")
        head.append("  scale as well as rotation, so they are applied as a "
                    "general 3x3.")
        head.append("  ROTATED (non-identity 3x3, epsilon 1e-4 per element) "
                    "in this export: %d object(s)." % rot)
        head.append("  TRANSLATED ONLY (identity 3x3, non-zero translation): "
                    "%d object(s)." % tra)
        if not rot and not tra:
            head.append("  Every placement node in this level is the identity "
                        "with a zero translation,")
            head.append("  so no vertex was moved by this step and the "
                        "coordinates are the disc's own.")
        head.append("  Everything else was written at its stored coordinates, "
                    "untouched.")
    head.append("")

    verts, tris = _write_obj(obj_path, os.path.basename(mtl_path), head,
                             area_objects, offsets)
    mats.write(mtl_path, "Red Dead Revolver -- %s" % level)

    lo, hi = [1e30] * 3, [-1e30] * 3
    for objs, (aoff, _l, _h) in zip(area_objects, offsets):
        for o in objs:
            lo, hi = _box_of([(v[0] + aoff[0] + o["offset"][0],
                               v[1] + aoff[1] + o["offset"][1],
                               v[2] + aoff[2] + o["offset"][2])
                              for v in o["verts"]], lo, hi)

    stats = dict(counters)
    stats.update(
        obj=obj_path, mtl=mtl_path, areas=area_rows, area_count=len(area_rows),
        objects=sum(len(o) for o in area_objects), verts=verts, tris=tris,
        textures=mats.png_written, textures_deduped=mats.png_deduped,
        texture_decode_failed=mats.decode_failed,
        materials=len(mats.defs), layout=layout,
        min=[round(x, 3) for x in lo] if lo[0] < 1e29 else None,
        max=[round(x, 3) for x in hi] if hi[0] > -1e29 else None,
    )
    if glb:
        stats["glb"] = write_glb(os.path.join(out_dir, "%s.glb" % level),
                                 area_objects, offsets, mats, tex_dir, head)
    _write_blender_note(out_dir, level, stats, glb)
    return stats


BLENDER_SCRIPT = '''"""Sort this level into two Blender collections you can switch on and off.

Run once, right after importing the .glb:
    Scripting tab  >  Open  >  pick this file  >  Run Script  (the play arrow)

You get SURFACES and COLLISION in the Outliner with a checkbox each. The
checkbox hides the whole branch -- every mesh under it -- which is the thing
you actually want when you are moving a wall and the collision shell is in
the way.

Why this is needed: glTF can express parenting but has no notion of a
collection, so the importer gives you two Empties with everything parented
under them. Blender's eye icon is per-object and does NOT follow parenting,
so clicking the eye on the parent hides the empty and leaves every mesh
under it exactly where it was. Measured on SALOON: 57 meshes visible before
the click, 57 after. Collections are the only structure in Blender whose
visibility toggle covers a whole branch, and this script builds them.
"""
import bpy

ROOTS = ("SURFACES", "COLLISION")


def descendants(obj):
    out, stack = [], list(obj.children)
    while stack:
        o = stack.pop()
        out.append(o)
        stack.extend(o.children)
    return out


scene = bpy.context.scene
moved = 0
for name in ROOTS:
    root = bpy.data.objects.get(name)
    if root is None:
        print("no %s in this scene -- did the .glb import?" % name)
        continue
    col = bpy.data.collections.get(name) or bpy.data.collections.new(name)
    if col.name not in scene.collection.children:
        scene.collection.children.link(col)
    for o in [root] + descendants(root):
        for c in list(o.users_collection):
            c.objects.unlink(o)
        col.objects.link(o)
        moved += 1
    print("%-10s -> %d objects" % (name, len(col.objects)))

print("done: %d objects sorted. Use the checkboxes in the Outliner." % moved)
'''


def _write_blender_script(out_dir):
    """Ship the collection-sorting script beside the model.

    The note used to tell people to select a branch and press H. That is
    wrong in a way that is hard to notice: H hides the SELECTED objects, and
    selecting the parent Empty selects exactly one object, so the branch stays
    put. Clicking the eye icon is wrong the same way. Both were written from
    how Blender feels rather than from running it -- hiding COLLISION on
    SALOON by either route leaves all 57 meshes visible, measured in Blender
    5.2 headless.

    Collections are the only thing in Blender whose visibility toggle covers a
    whole branch, and glTF cannot carry them, so the export carries a script
    that builds them instead.
    """
    try:
        with open(os.path.join(out_dir, "SPLIT INTO LAYERS.py"), "w",
                  encoding="utf-8") as fh:
            fh.write(BLENDER_SCRIPT)
    except OSError:
        pass


def _write_blender_note(out_dir, level, stats, glb):
    """A short note saying how to hide one half in Blender.

    The whole point of separating art from collision is being able to work on
    one with the other out of the way, and neither format makes that a visible
    switch on its own -- OBJ has no grouping at all, glTF has parenting but no
    collections. So the export explains it rather than leaving it to be
    discovered, because the person opening this may never have used Blender
    before.
    """
    _write_blender_script(out_dir)
    lines = [
        "%s -- exported by RDR Modding Studio" % level,
        "=" * 60,
        "",
        "Every object is named   area__surface__name   or",
        "                        area__collision__name",
        "so the two halves can always be told apart by name.",
        "",
    ]
    if glb:
        lines += [
            "USING THE .glb  (recommended -- one file, textures included)",
            "",
            "  File > Import > glTF 2.0, pick %s.glb" % level,
            "",
            "  In the Outliner (top right) you get two branches:",
            "      SURFACES    everything you can see",
            "      COLLISION   everything you bump into",
            "",
            "TO SWITCH ONE HALF OFF  (do this once, it is worth the 20 seconds)",
            "",
            "  Scripting tab > Open > pick  SPLIT INTO LAYERS.py  (it is in",
            "  this folder) > Run Script.",
            "",
            "  SURFACES and COLLISION become collections, each with a checkbox",
            "  in the Outliner. Untick one and that entire half disappears;",
            "  tick it to bring it back. That is the switch.",
            "",
            "  Do not bother clicking the eye icon on SURFACES or COLLISION",
            "  before running the script. Blender's eye is per-object and does",
            "  not follow parenting, so it hides the little branch marker and",
            "  leaves every mesh under it on screen. Selecting the branch and",
            "  pressing H does the same nothing, for the same reason. Only",
            "  collections cover a whole branch.",
            "",
        ]
    lines += [
        "USING THE .obj  (needs the .mtl and the textures folder beside it)",
        "",
        "  File > Import > Wavefront (.obj)",
        "",
        "  There is no grouping in an .obj, but the names still carry it, so:",
        "",
        "    Select > Select Pattern...   type  *collision*   OK",
        "    then press H to hide what it selected.",
        "",
        "  Use  *surface*  to do the reverse. Alt+H brings everything back.",
        "  (This one works because it selects the meshes themselves. Measured",
        "  on SALOON: 19 of 57 matched, 38 left visible.)",
        "",
        "IF EVERYTHING LOOKS GREY",
        "  Press Z and choose Material Preview. Solid mode ignores textures.",
        "",
        "IF YOU CANNOT SEE ANYTHING",
        "  Press Home to frame the whole level.",
        "",
        "Areas were placed side by side so interiors do not sit inside the",
        "street where the game keeps them. Re-export with 'true world",
        "positions' if you want the coordinates the disc actually states.",
    ]
    try:
        with open(os.path.join(out_dir, "HOW TO USE IN BLENDER.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError:
        pass
