"""Collision for a CUSTOM model: generate a box shell, add it as a new bound_ chunk.

2026-11-05 (design rule: collision and the model are one and the same). A custom
model replaces the picture of a level model; this gives it a matching SOLID
box. The shell is a NEW `BND0` chunk appended to the level's RSC0 bundle
(`rsc.append_chunk`) plus a NEW `bound_<stem>_01` node in a `.bnds` file whose
name stem equals the model's, so `Level.links()` pairs them and a move of the
model carries the box (see Level.with_collision).

How the BND0 is made (no format is invented): the template below is a stock
shell -- SHER70 chunk 83, a 5-face axis-aligned box (4 sides + top, 8 verts,
480 bytes). Vertices, the header AABB, centre, the radius, the distance-from-
origin, the spatial-index box and the leaf face-list box are all mapped by the
same per-axis scale+translate, and each face's stored AREA is recomputed from
its quad. An identity fit (target box == the template's own box) must give
back the template byte for byte -- `selftest()` checks that.

Proven offline (re-read, audit, identity) and in PCSX2: an oversized block
(2026-11-05) and a box fitted to the model (2026-11-12, player stops at the
face and slides along it; ingame-proof/custom_box_fitted_*).
"""
import base64
import re
import struct

from . import boundmove as BM

TEMPLATE_B64 = "AP//DwgBAQC+sDnBt2LvP/nVBUHlODPBcjiYQDPhDEHSdDbBIBFUQJZbCUEAAAAAAAAAAAAAAACIprw/WTF1QS60Zzy2BYs7AAAAAAgAAAAFAAAAAAAAAP////8AAAAA/////wAAAACQAAAA8AAAAJABAAD/////AAAAAJQBAADYAQAAAAAAAAAAAAACAAAAVbA5wf5l7z/K4AxBTjkzwf5l7z/K4AxBVbA5waA3mEDK4AxBTjkzwaA3mEDK4AxBVbA5waA3mEBi1gVBTjkzwaA3mEBi1gVBVbA5wf5l7z9i1gVBTjkzwf5l7z9i1gVBAAAAAAAAAIAAAIA/AEmVPwAAAQADAAIA//////////8AAAAAAACAPwAAAIAAETY+AwAFAAQAAgD//////////wAAAAAAAAAAAACAvwBJlT8FAAcABgAEAP//////////AACAPwAAAAAAAACAAJWiPwcABQADAAEA//////////8AAIC/AAAAgAAAAIAAlaI/AAACAAQABgD//////////wEAAABajE3B/mXvPxyI5EBJXR/BoDeYQB5zIEEAAAAAtAEAAFWwOcH+Ze8/YtYFQU45M8GgN5hAyuAMQQUAAAABAAIAAwAEAAAAAAAAAAAA"
# Flags of the stock solid shells (bound_bound_grp_ttffttttffff): walkers,
# horses, enemies and the camera are stopped; bullets/targets are not.
SOLID_FLAGS = ("BoundFlagMover", "BoundFlagCamera", "BoundFlagSneak",
               "BoundFlagEnemy", "BoundFlagHorse", "BoundFlagPlayer")
MIN_SIDE = 0.25            # never a paper-thin box: it would be walked through


class CollisionError(Exception):
    pass


def template():
    return base64.b64decode(TEMPLATE_B64)


def _verts(raw):
    n = struct.unpack_from("<I", raw, BM.BND_COUNTS)[0]
    o = struct.unpack_from("<I", raw, BM.BND_VOFF)[0]
    return [struct.unpack_from("<3f", raw, o + 12 * i) for i in range(n)]


def _faces(raw):
    """[(record offset, [4 vertex idx])] -- 32-byte records after the verts."""
    nv = struct.unpack_from("<I", raw, BM.BND_COUNTS)[0]
    nf = struct.unpack_from("<I", raw, BM.BND_COUNTS + 4)[0]
    o = struct.unpack_from("<I", raw, BM.BND_VOFF)[0] + 12 * nv
    out = []
    for i in range(nf):
        r = o + 32 * i
        idx = list(struct.unpack_from("<4H", raw, r + 16))
        out.append((r, idx))
    return out


def _area(vs, idx):
    pts = [vs[i] for i in idx[:4]]
    sx = sy = sz = 0.0
    for a, b in zip(pts, pts[1:] + pts[:1]):
        sx += a[1] * b[2] - a[2] * b[1]
        sy += a[2] * b[0] - a[0] * b[2]
        sz += a[0] * b[1] - a[1] * b[0]
    return 0.5 * (sx * sx + sy * sy + sz * sz) ** 0.5


def box_collision(lo, hi):
    """BND0 bytes for a solid box spanning world AABB lo..hi."""
    lo = [float(v) for v in lo]
    hi = [float(v) for v in hi]
    for i in range(3):
        if hi[i] - lo[i] < MIN_SIDE:
            c = (hi[i] + lo[i]) / 2.0
            lo[i], hi[i] = c - MIN_SIDE / 2, c + MIN_SIDE / 2
    t = template()
    vs = _verts(t)
    tlo = [min(v[i] for v in vs) for i in range(3)]
    thi = [max(v[i] for v in vs) for i in range(3)]
    k = [(hi[i] - lo[i]) / (thi[i] - tlo[i]) for i in range(3)]

    def fn(x, y, z):
        p = (x, y, z)
        return tuple(lo[i] + (p[i] - tlo[i]) * k[i] for i in range(3))

    # radius is half the AABB diagonal: scale it by the diagonal ratio
    td = sum((thi[i] - tlo[i]) ** 2 for i in range(3)) ** 0.5
    nd = sum((hi[i] - lo[i]) ** 2 for i in range(3)) ** 0.5
    out = bytearray(BM.transform(t, fn, radius_scale=nd / td))

    # leaf face list: a tight box (24 bytes) sits at the node's face pointer
    for o in BM.nodes(t):
        fp = struct.unpack_from("<I", t, o + BM.NODE_FACES)[0]
        if fp:
            b = struct.unpack_from("<6f", t, fp)
            nlo, nhi = fn(*b[:3]), fn(*b[3:])
            struct.pack_into("<6f", out, fp, *(list(nlo) + list(nhi)))
    # face areas from the new quads
    nvs = _verts(bytes(out))
    for r, idx in _faces(bytes(out)):
        struct.pack_into("<f", out, r + 12, _area(nvs, idx))
    return bytes(out)


def selftest():
    """Identity fit gives back the template exactly. -> raises if not."""
    t = template()
    vs = _verts(t)
    lo = [min(v[i] for v in vs) for i in range(3)]
    hi = [max(v[i] for v in vs) for i in range(3)]
    got = box_collision(lo, hi)
    bad = [i for i in range(len(t)) if t[i] != got[i]]
    # float rounding in the last bit is the only tolerated difference
    if len(got) != len(t) or len(bad) > 16:
        raise AssertionError("identity fit differs in %d bytes: %s"
                             % (len(bad), bad[:20]))
    return len(bad)


def obj_aabb(obj_text):
    vs = []
    for line in obj_text.splitlines():
        b = line.split()
        if len(b) >= 4 and b[0] == "v":
            vs.append((float(b[1]), float(b[2]), float(b[3])))
    if not vs:
        raise CollisionError("the model has no vertices to fit a box to")
    return ([min(v[i] for v in vs) for i in range(3)],
            [max(v[i] for v in vs) for i in range(3)])


def stem_of(zone_name):
    """'zone_ppear01_01' -> 'ppear01' (same rule as Level.link_stem)."""
    s = str(zone_name or "")
    while True:
        nxt = re.sub(r'^(bound|zone)_', '', s, flags=re.I)
        if nxt == s:
            break
        s = nxt
    return re.sub(r'(_\d+)+$', '', s)


def bnds_block(template_block, name, resource_index, solid=True):
    """A .bnds node block for the new chunk, cloned from a stock block."""
    t = template_block.decode("latin1") if isinstance(template_block, bytes) \
        else template_block
    t = re.sub(r'^node\s+\S+', 'node ' + name, t, count=1)
    t = re.sub(r'(resourceIndex\s+)\d+', r'\g<1>%d' % resource_index, t, count=1)
    t = re.sub(r'(resourceIndexXbox\s+)\d+', r'\g<1>%d' % resource_index, t, count=1)
    walk = ("BoundFlagMover", "BoundFlagPlayer", "BoundFlagEnemy", "BoundFlagHorse")
    for f in SOLID_FLAGS:
        want = ("true" if (solid or f not in walk) else "false")
        t = re.sub(r'(BOOL\s+%s\s+)\w+' % f, r'\g<1>%s' % want, t, count=1)
    return t.encode("latin1")


def _res_index(n):
    try:
        return int(round(float(n.props.get("resourceIndex"))))
    except (TypeError, ValueError):
        return None


def zone_for_chunk(lvl, chunk):
    """The drawn node that uses MOD0 chunk `chunk` (resourceIndex is 1-based)."""
    for n in lvl.nodes:
        if n.kind == "zone" and _res_index(n) == chunk + 1:
            return n
    return None


def plan(lvl, bundle, chunk, obj_text, solid=True, box=None):
    """Work out the collision for a custom model. NOTHING is written.

    `bundle` is the RSC0 bytes AFTER the model import. `box` = (lo, hi) overrides
    the fit (default: the OBJ's own bounding box).

    -> dict(ok=True, mode='added'|'replaced', bundle=<new bytes>, edits=
    {bnds file: [...]} or {}, name, chunk, note)
    or dict(ok=False, why="plain English reason").  Never raises for a
    "cannot" -- those become the UI / CAPABILITIES text, not a fake success.
    """
    from . import rsc as R
    from . import scene
    zone = zone_for_chunk(lvl, chunk)
    if zone is None:
        return dict(ok=False, why="No scene object uses this model, so there "
                    "is no name to link collision to. The model is drawn "
                    "but walk-through.")
    try:
        lo, hi = box if box else obj_aabb(obj_text)
        shell = box_collision(lo, hi)
    except (CollisionError, ValueError, struct.error) as ex:
        return dict(ok=False, why="Could not fit a collision box: %s" % ex)

    stem = stem_of(zone.name)
    partner = lvl.links().get(zone.uid)
    if partner is not None:
        # The model already has its own collision twin: swap its shape for the
        # box that matches the NEW model (same chunk number, nothing renamed).
        b = lvl.by_uid(partner)
        bi = _res_index(b)
        if bi is None or not (1 <= bi <= len(R.Rsc(bundle).chunks)):
            return dict(ok=False, why="The existing collision shell is not in "
                        "this level's bundle, so it was left unchanged.")
        out = R.replace_chunk(bundle, bi - 1, shell, "custom collision")
        return dict(ok=True, mode="replaced", bundle=out, edits={},
                    name=b.name, chunk=bi - 1,
                    note="Collision: the old shell %s now matches your model "
                         "(a solid box). Moves with the model." % b.name)

    return _add_new(lvl, bundle, shell, stem, solid)


def plan_box(lvl, bundle, lo, hi, stem="custom", solid=True):
    """A standalone collision box (world AABB lo..hi): a NEW BND0 chunk + a NEW bound_<stem>_NN node. NOTHING is
    written. -> the same dict as plan(). For collision where the level has none: a wall, a ledge, a model of
    your own that never had a shell."""
    try:
        shell = box_collision(lo, hi)
    except (CollisionError, ValueError, struct.error) as ex:
        return dict(ok=False, why="Could not make the box: %s" % ex)
    stem = re.sub(r'[^A-Za-z0-9]+', '', stem_of(stem) or "custom") or "custom"
    return _add_new(lvl, bundle, shell, stem, solid)


def _add_new(lvl, bundle, shell, stem, solid=True):
    """A NEW bound_<stem>_NN node + a NEW BND0 chunk holding `shell`."""
    from . import rsc as R
    from . import scene
    try:
        out, new_idx = R.append_chunk(bundle, "BND0", shell, "custom collision")
    except (ValueError, AssertionError, struct.error) as ex:
        return dict(ok=False, why="This level's bundle is not shaped like the "
                    "ones the chunk-adder was tested on (%s), so no collision "
                    "chunk was added." % ex)
    name = "bound_%s_01" % stem
    taken = {n.name for n in lvl.nodes}
    k = 1
    while name in taken:
        k += 1
        name = "bound_%s_%02d" % (stem, k)
    bnds = [f for f in dict.fromkeys(n.source for n in lvl.nodes
                                     if n.kind == "bound"
                                     and n.source.lower().endswith(".bnds"))]
    if not bnds:
        return dict(ok=False, why="This level has no .bnds collision file to "
                    "add a node to.")

    def room(fn):
        try:
            return -lvl.capacity(fn)["approx_text"]
        except Exception:
            return 0
    bnds.sort(key=room)
    last = None
    for target in bnds:
        src = next(n for n in lvl.nodes if n.source == target and n.kind == "bound")
        data = lvl._read(target)
        block = bnds_block(lvl._read(src.source)[src.span[0]:src.span[1]],
                           name, new_idx + 1, solid=solid)
        sep = b"\r\n\r\n" if b"\r\n" in data else b"\n\n"
        end = len(data.rstrip(b"\r\n \t"))
        edits = [(end, end, sep + block)] + scene.bump_count(data, target, +1)
        trial = lvl.apply({target: edits}, dry=True)
        last = trial
        if all(r.get("fits") for r in trial.get("report", {}).values()):
            return dict(ok=True, mode="added", bundle=out, edits={target: edits},
                        name=name, chunk=new_idx, file=target,
                        note="Collision: added a solid box %s (new chunk %d) "
                             "linked to your model by name; it moves with it."
                             % (name, new_idx))
    short = min((r.get("headroom", 0) for r in (last or {}).get("report", {}).values()),
                default=0)
    return dict(ok=False, why="No room for a collision node: every .bnds file "
                "in this level is full (best one is %d byte(s) short). Free "
                "space by deleting an unused object, or accept a walk-through "
                "model." % abs(short))
