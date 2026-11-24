"""Collision generated from a level's models: a start to edit (Collision editor: Auto-generate collision).

2026-11-18. A level whose models came in without collision (a blank slate with imported models, a map whose export
had the add-on's generator off) gets collision from what it draws:

1. every model's triangles where it stands (its zone node's matrix applied, as the viewport places it: x' = r0 x +
   r3 y + r6 z + px ...);
2. simplified by clustering: corners within `cell` metres of each other made one (small details fold away, a wall
   stays a wall), triangles left with no size dropped, the same triangle twice kept once;
3. cut into shells by place (`tile` metres square), each within what one shell takes, written with
   collcreate.add as bound_auto_NN -- replaced on the next run when asked.
"""
import math

MAX_SHELL = 6000          # faces a generated shell holds at most (a tile with more is split again)


def _shell_boxes(lvl, ignore=lambda name: False):
    """[(lo, hi)] world boxes of the level's collision shells (bounds' vertices; their matrices are identity)."""
    from . import scene, geomedit
    rs = lvl._load_rsc()
    out = []
    if rs is None:
        return out
    for n in lvl.nodes:
        if n.kind != "bound" or ignore(n.name):
            continue
        ci = scene.chunk_of(n)
        try:
            vs = geomedit.bound_verts(rs.raw(ci))["verts"]
        except Exception:                                   # noqa: BLE001
            continue
        if vs:
            off = list(n.xyz()) if n.pos else [0.0, 0.0, 0.0]
            out.append(([min(v[k] for v in vs) + off[k] for k in range(3)],
                        [max(v[k] for v in vs) + off[k] for k in range(3)]))
    return out


def level_triangles(lvl, skip_existing=True, ignore=lambda name: False, unclaimed=True):
    """[(a, b, c)] of the level's models in world space. skip_existing: not the models that have collision already
    -- their own shell (bound_X of zone_X), or standing wholly inside a shell's box (a level's merged collision)
    -- 2026-11-18. ignore(name): shells not to count (the generated ones about to be replaced)."""
    rg = lvl.render_geometry()
    by_chunk = {m["chunk"]: m for m in rg.get("meshes", [])}
    from . import scene
    placed = {}
    skip = set()
    boxes = []
    if skip_existing:
        links = lvl.links()
        for n in lvl.nodes:
            if n.kind == "zone" and links.get(n.uid) is not None and not ignore(lvl.by_uid(links[n.uid]).name):
                skip.add(n.uid)
        boxes = _shell_boxes(lvl, ignore)
    lvl._autogen_skipped = 0
    lvl._autogen_skip_ci = set()
    for n in lvl.nodes:
        if n.kind != "zone":
            continue
        if n.uid in skip:
            lvl._autogen_skipped += 1
            c0 = scene.chunk_of(n)
            if c0 is not None:
                lvl._autogen_skip_ci.add(c0)
            continue
        ci = scene.chunk_of(n)
        if ci is None or ci not in by_chunk:
            continue
        rot = [x.value for x in n.rot] if len(getattr(n, "rot", []) or []) == 9 else None
        pos = [x.value if hasattr(x, "value") else float(x) for x in n.pos] if n.pos else [0.0, 0.0, 0.0]
        placed.setdefault(ci, []).append((rot, pos))
    tris = []
    for ci, m in by_chunk.items():
        if ci not in placed and (ci in getattr(lvl, "_autogen_skip_ci", ()) or not unclaimed):
            continue                                        # drawn only by models skipped above, or by no node
        P, I = m["positions"], m["indices"]
        vs = [tuple(P[k:k + 3]) for k in range(0, len(P), 3)]
        for rot, pos in placed.get(ci, [(None, (0.0, 0.0, 0.0))]):
            if rot:
                r = rot
                w = [(r[0] * x + r[3] * y + r[6] * z + pos[0], r[1] * x + r[4] * y + r[7] * z + pos[1],
                      r[2] * x + r[5] * y + r[8] * z + pos[2]) for x, y, z in vs]
            else:
                w = [(x + pos[0], y + pos[1], z + pos[2]) for x, y, z in vs]
            if boxes and w:
                lo = [min(p[k] for p in w) for k in range(3)]
                hi = [max(p[k] for p in w) for k in range(3)]
                if any(all(b[0][k] - 0.25 <= lo[k] and hi[k] <= b[1][k] + 0.25 for k in range(3)) for b in boxes):
                    lvl._autogen_skipped += 1
                    continue                                # inside collision there already
            for k in range(0, len(I) - 2, 3):
                tris.append((w[I[k]], w[I[k + 1]], w[I[k + 2]]))
    return tris


def simplify(tris, cell=0.3):
    """(verts, faces) of triangles with corners clustered on a `cell` metre grid."""
    index, sums, verts, faces, seen = {}, [], [], [], set()

    def corner(p):
        key = (int(math.floor(p[0] / cell)), int(math.floor(p[1] / cell)), int(math.floor(p[2] / cell)))
        i = index.get(key)
        if i is None:
            i = index[key] = len(sums)
            sums.append([0.0, 0.0, 0.0, 0])
        s = sums[i]
        s[0] += p[0]; s[1] += p[1]; s[2] += p[2]; s[3] += 1
        return i
    for a, b, c in tris:
        f = (corner(a), corner(b), corner(c))
        if f[0] == f[1] or f[1] == f[2] or f[0] == f[2]:
            continue
        k = tuple(sorted(f))
        if k in seen:
            continue
        seen.add(k)
        faces.append(list(f))
    verts = [(s[0] / s[3], s[1] / s[3], s[2] / s[3]) for s in sums]
    # faces that clustering left with no area (corners on a line)
    good = []
    for f in faces:
        a, b, c = (verts[i] for i in f)
        ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
        cx, cy, cz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        if cx * cx + cy * cy + cz * cz > 4e-8:
            good.append(f)
    return verts, good


def shells(verts, faces, tile=40.0):
    """[(verts, faces)] the faces cut by place into tiles of `tile` metres (x, z), each at most MAX_SHELL faces."""
    groups = {}
    for f in faces:
        cx = sum(verts[i][0] for i in f) / 3.0
        cz = sum(verts[i][2] for i in f) / 3.0
        groups.setdefault((int(math.floor(cx / tile)), int(math.floor(cz / tile))), []).append(f)
    out = []
    for key in sorted(groups):
        fs = groups[key]
        for k in range(0, len(fs), MAX_SHELL):
            part = fs[k:k + MAX_SHELL]
            remap, vs = {}, []
            nf = []
            for f in part:
                g = []
                for i in f:
                    if i not in remap:
                        remap[i] = len(vs)
                        vs.append(list(verts[i]))
                    g.append(remap[i])
                nf.append(g)
            out.append((vs, nf))
    return out
