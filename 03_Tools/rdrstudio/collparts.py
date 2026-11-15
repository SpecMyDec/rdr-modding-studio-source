"""Which collision belongs to which model, when the level merged it all.

Most levels do NOT give a model its own collision shell. SHER70 has 300 drawn
models and 41 shells; 37 of those are `bound_bound_grp_<flags>` shells, each
the collision of many objects welded into one chunk by flag set. Only 4 models
have a `bound_` twin of their own. So "move the model and its collision" has
to reach INSIDE the merged shells.

How: a shell's faces fall into connected pieces (faces that share a vertex).
A piece belongs to a set of models when every one of its vertices lies inside
the models' combined world box (padded). Those vertices are moved by the same
transform as the models and the shell's octree is rebuilt (`bndtree.rebuild`),
so nothing is left behind at the old place and the index is true at the new.

A model is also often drawn in several chunks: `X` and `X_2p` (the second
texture pass over the same surface) are one object; `parts_of` joins them.
"""
import re

from . import bndtree, scene

PAD = 0.30          # metres a collision piece may poke outside the model box


def base_stem(name):
    """`zone_saloonWallsLow_2p_01` and `zone_saloonWallsLow_01` -> same key."""
    s = re.sub(r'^(zone|bound)_', '', str(name or ""), flags=re.I)
    s = re.sub(r'(_\d+)+$', '', s)
    s = re.sub(r'_2p$', '', s, flags=re.I)
    return s.lower()


def parts_of(level, uid):
    """uids of every drawn node that is the same object as `uid`
    (itself, plus its second-pass twin). Always contains `uid`."""
    n = level.by_uid(uid)
    if n.kind != "zone":
        return [uid]
    key = base_stem(n.name)
    out = [m.uid for m in level.nodes if m.kind == "zone"
           and base_stem(m.name) == key]
    return out if uid in out else [uid] + out


def world_box(level, rsc, node):
    """(lo, hi) of a drawn node's model in world space, or None."""
    ci = scene.chunk_of(node)
    if ci is None:
        return None
    try:
        m = rsc.model(ci)
    except Exception:
        return None
    if not m or not m.get("verts"):
        return None
    t = node.xyz()
    R = [x.value for x in node.rot] if node.rot else [1, 0, 0, 0, 1, 0, 0, 0, 1]
    lo = [1e30] * 3
    hi = [-1e30] * 3
    for v in m["verts"]:
        w = (v[0] * R[0] + v[1] * R[3] + v[2] * R[6] + t[0],
             v[0] * R[1] + v[1] * R[4] + v[2] * R[7] + t[1],
             v[0] * R[2] + v[1] * R[5] + v[2] * R[8] + t[2])
        for i in range(3):
            if w[i] < lo[i]:
                lo[i] = w[i]
            if w[i] > hi[i]:
                hi[i] = w[i]
    return lo, hi


def pieces(parsed):
    """Connected pieces of a parsed shell -> [(set of vertex idx, [face idx])]."""
    par = list(range(parsed["nv"]))

    def find(a):
        while par[a] != a:
            par[a] = par[par[a]]
            a = par[a]
        return a

    for f in parsed["faces"]:
        r = find(f[0])
        for i in f[1:]:
            par[find(i)] = r
    out = {}
    for fi, f in enumerate(parsed["faces"]):
        vs, fs = out.setdefault(find(f[0]), (set(), []))
        vs.update(f)
        fs.append(fi)
    return list(out.values())


def claim(level, rsc, uids, pad=PAD):
    """Collision that belongs to the drawn nodes `uids`.

    -> dict(box=(lo, hi) or None,
            shells={bound chunk: sorted vertex indices},
            faces=total faces claimed, pieces=number of pieces)"""
    lo = [1e30] * 3
    hi = [-1e30] * 3
    got = False
    for u in uids:
        b = world_box(level, rsc, level.by_uid(u))
        if not b:
            continue
        got = True
        for i in range(3):
            lo[i] = min(lo[i], b[0][i])
            hi[i] = max(hi[i], b[1][i])
    res = dict(box=(lo, hi) if got else None, shells={}, faces=0, pieces=0)
    if not got:
        return res
    seen = set()
    for n in level.nodes:
        if n.kind != "bound":
            continue
        ci = scene.chunk_of(n)
        if ci is None or ci in seen:
            continue
        seen.add(ci)
        try:
            raw = rsc.raw(ci)
            # cheap reject on the shell's own box
            import struct
            slo = struct.unpack_from("<3f", raw, 0x08)
            shi = struct.unpack_from("<3f", raw, 0x14)
            if any(slo[i] > hi[i] + pad or shi[i] < lo[i] - pad for i in range(3)):
                continue
            p = bndtree.parse(raw)
        except Exception:
            continue
        V = p["verts"]
        take = set()
        for vs, fs in pieces(p):
            if all(lo[i] - pad <= V[v][i] <= hi[i] + pad
                   for v in vs for i in range(3)):
                take |= vs
                res["faces"] += len(fs)
                res["pieces"] += 1
        if take:
            res["shells"][ci] = sorted(take)
    return res


def move_collision(rsc, shells, fn, keep_normals=False):
    """{chunk: new BND0 bytes} with the claimed vertices moved by `fn(x,y,z)`.

    Each rebuilt shell is audited; a shell whose rebuilt index would miss a
    face raises (nothing is returned for writing)."""
    out = {}
    for ci, vidx in shells.items():
        raw = rsc.raw(ci)
        p = bndtree.parse(raw)
        new = {i: fn(*p["verts"][i]) for i in vidx}
        nb = bndtree.rebuild(raw, new, keep_normals=keep_normals)
        a = bndtree.audit(nb)
        if a["missing"] or a["tight_bad"] or a["faces_listed"] != a["faces"]:
            raise ValueError("rebuilt collision index for chunk %d does not "
                             "check out (%r)" % (ci, a))
        out[ci] = nb
    return out


# ---------------------------------------------------------------- sets
#
# One object is often several drawn chunks (a water tower = legs + tank +
# trough + water) with ONE welded collision. `group_of` finds the drawn pieces
# that sit inside the picked model's box, so a move takes the whole set.

SET_MAX = 40            # more members than this is a district, not an object
SET_AREA_FRAC = 0.25    # a model covering this much of the map is terrain


def identity_rot(node):
    R = [x.value for x in node.rot] if node.rot else None
    return R is None or all(abs(a - b) < 1e-5 for a, b in
                            zip(R, (1, 0, 0, 0, 1, 0, 0, 0, 1)))


def _boxes(level, rsc):
    cache = getattr(level, "_collparts_boxes", None)
    if cache is None or cache[0] is not rsc:
        cache = (rsc, {n.uid: world_box(level, rsc, n)
                       for n in level.nodes if n.kind == "zone"})
        try:
            level._collparts_boxes = cache
        except Exception:
            pass
    return cache[1]


def group_of(level, rsc, uid, pad=PAD):
    """uids of the drawn nodes that move as one object with `uid`: itself, its
    second-pass twin, and every drawn model whose box lies inside its box.
    A model that spans a large share of the map (terrain) is never a set."""
    n = level.by_uid(uid)
    if n.kind != "zone":
        return [uid]
    boxes = _boxes(level, rsc)
    out = [u for u in parts_of(level, uid)]
    b = boxes.get(uid)
    if not b:
        return out
    lo = [min(v[0][i] for v in boxes.values() if v) for i in range(3)]
    hi = [max(v[1][i] for v in boxes.values() if v) for i in range(3)]
    whole = max(1e-6, (hi[0] - lo[0]) * (hi[2] - lo[2]))
    mine = (b[1][0] - b[0][0]) * (b[1][2] - b[0][2])
    if mine / whole > SET_AREA_FRAC:
        return out
    inside = []
    for u, ob in boxes.items():
        if u in out or not ob:
            continue
        if all(b[0][i] - pad <= ob[0][i] and ob[1][i] <= b[1][i] + pad
               for i in range(3)):
            inside.append(u)
    if len(inside) > SET_MAX:
        return out
    return out + sorted(inside)


def follow(stock_level, stock_rsc, live_rsc, moves, skip=(), pad=PAD):
    """Collision that follows moved models, drift-free.

    `moves` = [(stock uids, fn)]: the collision pieces inside those models'
    STOCK box are found in the STOCK shells, and the same vertex numbers in
    the LIVE shells are set to fn(stock position). So repeating or undoing a
    move never accumulates, and two sets sharing one merged shell keep each
    other's moves. `skip` = bound chunks handled elsewhere (name-linked twins).

    -> ({bound chunk: new BND0 bytes}, dict(pieces, faces, shells, skipped))"""
    plan = {}                       # chunk -> {vertex index: new xyz}
    info = dict(pieces=0, faces=0, shells=0, skipped=[])
    for uids, fn in moves:
        c = claim(stock_level, stock_rsc, uids, pad=pad)
        for ci, vidx in c["shells"].items():
            if ci in skip:
                continue
            sv = bndtree.parse(stock_rsc.raw(ci))["verts"]
            d = plan.setdefault(ci, {})
            for i in vidx:
                if i not in d:
                    d[i] = fn(*sv[i])
    out = {}
    for ci, new in plan.items():
        try:
            raw = live_rsc.raw(ci)
            lp = bndtree.parse(raw)
            sp = bndtree.parse(stock_rsc.raw(ci))
            if lp["nv"] != sp["nv"] or len(lp["faces"]) != len(sp["faces"]):
                info["skipped"].append(ci)       # replaced by a custom shell
                continue
            nb = bndtree.rebuild(raw, new)
            a = bndtree.audit(nb)
            if a["missing"] or a["tight_bad"] or a["faces_listed"] != a["faces"]:
                info["skipped"].append(ci)
                continue
        except Exception:
            info["skipped"].append(ci)
            continue
        if nb != raw:
            out[ci] = nb
        for vs, fs in pieces(sp):
            if vs <= new.keys():
                info["pieces"] += 1
                info["faces"] += len(fs)
    info["shells"] = len(out)
    return out, info


def _solve3(M, b):
    """3x3 linear solve (Gauss, partial pivot) or None when singular."""
    A = [list(M[i]) + [b[i]] for i in range(3)]
    for c in range(3):
        p = max(range(c, 3), key=lambda r: abs(A[r][c]))
        if abs(A[p][c]) < 1e-9:
            return None
        A[c], A[p] = A[p], A[c]
        for r in range(3):
            if r != c:
                f = A[r][c] / A[c][c]
                A[r] = [x - f * y for x, y in zip(A[r], A[c])]
    return [A[i][3] / A[i][i] for i in range(3)]


def fit_xform(P, Q, tol=0.02):
    """The map that takes points P onto Q, as the Studio's edits can make it:
    x and z mixed (turn about Y, scale, move), y scaled and moved on its own.

    -> (fn, worst error in metres) or None when P and Q are not that kind of
    copy of each other (different point count, a flat sliver, a free-form
    vertex edit). Used so collision goes where the drawn model REALLY is,
    whatever mix of drags and reshapes got it there."""
    n = len(P)
    if n < 3 or n != len(Q):
        return None
    cx = sum(p[0] for p in P) / n
    cz = sum(p[2] for p in P) / n
    cy = sum(p[1] for p in P) / n
    sxx = sxz = szz = 0.0
    bx = [0.0, 0.0, 0.0]
    bz = [0.0, 0.0, 0.0]
    syy = syq = 0.0
    qy = sum(q[1] for q in Q) / n
    for p, q in zip(P, Q):
        x, z = p[0] - cx, p[2] - cz
        sxx += x * x; sxz += x * z; szz += z * z
        bx[0] += x * q[0]; bx[1] += z * q[0]; bx[2] += q[0]
        bz[0] += x * q[2]; bz[1] += z * q[2]; bz[2] += q[2]
        y = p[1] - cy
        syy += y * y; syq += y * (q[1] - qy)
    M = [[sxx, sxz, 0.0], [sxz, szz, 0.0], [0.0, 0.0, float(n)]]
    if sxx * szz - sxz * sxz < 1e-6 * max(1.0, sxx * szz):
        return None
    X = _solve3(M, bx)
    Z = _solve3(M, bz)
    if X is None or Z is None:
        return None
    sy = syq / syy if syy > 1e-9 else 1.0

    def fn(x, y, z):
        u, w = x - cx, z - cz
        return (X[0] * u + X[1] * w + X[2], qy + sy * (y - cy),
                Z[0] * u + Z[1] * w + Z[2])
    err = 0.0
    for p, q in zip(P, Q):
        r = fn(*p)
        e = max(abs(r[0] - q[0]), abs(r[1] - q[1]), abs(r[2] - q[2]))
        if e > err:
            err = e
    if err > tol:
        return None
    return fn, err


def world_verts(rsc, node):
    """A drawn node's model vertices in world space (unrotated nodes only)."""
    ci = scene.chunk_of(node)
    if ci is None or not identity_rot(node):
        return None
    try:
        m = rsc.model(ci)
    except Exception:
        return None
    if not m or not m.get("verts"):
        return None
    t = node.xyz()
    return [(v[0] + t[0], v[1] + t[1], v[2] + t[2]) for v in m["verts"]]
