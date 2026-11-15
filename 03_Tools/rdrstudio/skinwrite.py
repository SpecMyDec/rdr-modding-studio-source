"""Custom CHARACTER models: OBJ -> rigged MOD0, skinning inherited from the
model being replaced.

What this does, in one breath: every OBJ vertex is bound to the bone(s) of the
NEAREST vertex of the model it replaces (the same idea as Blender's "Data
Transfer > Vertex Groups"), positions are stored bone-local like the shipped
data, and the display list is emitted with `skinvif` -- whose batch encoder
reproduces all 15,361 shipped narrow batches byte-for-byte.

PROVEN offline (scripts/test_skinvif.py, test_skinwrite.py): the batch/header
rules, the container, and that a model written here re-reads through the
independent reader (vif.py) with the same triangles and bone bindings.

PROVEN in PCSX2 (2026-11-12): a re-striped custom mesh on the town level's player
(enlarged head, 3,184 -> 4,700 points) draws, textures and animates.

NOT PROVEN, and the report says so: (1) only that one character and one mesh
were booted; (2) two part-level numbers are ESTIMATED, not derived: the index count in
the part record (the shipped values follow no exact formula we found) and the
cull-sphere radius (shipped radii sit near the bone-local extent); (3) the
result is rigid-ish: weights are copied from the nearest original vertex, so a
very different shape can deform oddly. Collision does not apply to characters.

Refuses: a rig whose bone count differs from the model's, a model that is not
rigged, an OBJ with nothing to draw, geometry that cannot be quantised, more
batches than a part may hold.
"""
import math
import struct

from . import levelvif, skinvif as S, vif
from . import rsc as R


class SkinError(ValueError):
    pass


CAPS = (48, 40, 32, 26, 20, 14)
UV_LIMIT = 7.99
VU_BUDGET = 176          # 3N + 4 + 2*nrec must stay under this (shipped max 178)


# ----------------------------------------------------- read the base model

def read_vertices(cont, bone_world):
    """All vertices of a rigged container -> [dict(world, bone, extras)].

    `extras` = [(bone, weight)] secondary influences. Positions are decoded
    exactly as vif.py does (row + code*ulp - bias) and moved to bind-pose
    world space by adding the bone's world position (the bind pose is
    translation-only).
    """
    out = []
    for p in cont.parts:
        raw = p["packet"]
        pro, batches, epi, toks, pe = S.split_packet(raw, 0, len(raw))
        for tl in batches:
            d = S.decode_batch(raw, tl)
            if d["pos_wide"]:
                continue
            rowf, ulp = zip(*[vif._ulp(w) for w in d["pos_row"][:3]])
            bias = struct.unpack("<3f", struct.pack("<3I", *d["bias"]))
            base = d["itop"] + 4
            extras = {}
            for q in range(d["nrec"]):
                a, b, c, wt = struct.unpack_from("<4f", d["extra_raw"], 32 * q)
                dst, badr = struct.unpack_from("<2I", d["extra_raw"], 32 * q + 16)
                vi = (dst - base) // 3
                extras.setdefault(vi, []).append(
                    ((badr - S.BONE_BASE) // 4, wt, (a, b, c)))
            for i in range(d["n"]):
                c = struct.unpack_from("<3H", d["pos_raw"], 8 * i)
                w = struct.unpack_from("<H", d["pos_raw"], 8 * i + 6)[0]
                bone = ((w & 0x7FFF) - S.BONE_BASE) // 4
                loc = tuple(rowf[k] + c[k] * ulp[k] - bias[k] for k in range(3))
                if bone >= len(bone_world):
                    raise SkinError("the model uses bone %d but the skeleton "
                                    "has %d bones" % (bone, len(bone_world)))
                bw = bone_world[bone]
                out.append(dict(world=tuple(loc[k] + bw[k] for k in range(3)),
                                bone=bone,
                                extras=[(e[0], e[1]) for e in extras.get(i, [])]))
    return out


class _Grid:
    """Nearest-vertex lookup on a uniform hash grid."""

    def __init__(self, pts):
        self.pts = pts
        lo = [min(p[k] for p in pts) for k in range(3)]
        hi = [max(p[k] for p in pts) for k in range(3)]
        ext = max(max(hi[k] - lo[k] for k in range(3)), 1e-3)
        self.cell = max(ext / max(round(len(pts) ** (1 / 3)), 1), 1e-4)
        self.cells = {}
        for i, p in enumerate(pts):
            self.cells.setdefault(self._key(p), []).append(i)
        self.maxring = int(ext / self.cell) + 2

    def _key(self, p):
        c = self.cell
        return (int(math.floor(p[0] / c)), int(math.floor(p[1] / c)),
                int(math.floor(p[2] / c)))

    def nearest(self, q):
        kx, ky, kz = self._key(q)
        best, bd = -1, float("inf")
        for ring in range(self.maxring + 1):
            for dx in range(-ring, ring + 1):
                for dy in range(-ring, ring + 1):
                    for dz in range(-ring, ring + 1):
                        if max(abs(dx), abs(dy), abs(dz)) != ring:
                            continue
                        for i in self.cells.get((kx + dx, ky + dy, kz + dz), ()):
                            p = self.pts[i]
                            d = (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 \
                                + (p[2] - q[2]) ** 2
                            if d < bd:
                                best, bd = i, d
            if best >= 0 and math.sqrt(bd) <= ring * self.cell:
                break
        return best, math.sqrt(bd)


# ------------------------------------------------------------- encoding

def _normals(verts, tris):
    """Smooth per-corner normals: face normals summed per POSITION."""
    acc = {}
    for a, b, c in tris:
        p0, p1, p2 = verts[a][0], verts[b][0], verts[c][0]
        u = [p1[k] - p0[k] for k in range(3)]
        v = [p2[k] - p0[k] for k in range(3)]
        n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2],
             u[0] * v[1] - u[1] * v[0])
        for i in (a, b, c):
            key = tuple(round(x, 5) for x in verts[i][0])
            s = acc.setdefault(key, [0.0, 0.0, 0.0])
            for k in range(3):
                s[k] += n[k]
    out = {}
    for i in {i for t in tris for i in t}:
        s = acc[tuple(round(x, 5) for x in verts[i][0])]
        ln = math.sqrt(sum(x * x for x in s)) or 1.0
        out[i] = tuple(x / ln for x in s)
    return out


def _quant_batch(locals_, narrow_first=True):
    """Pick the row (range) and per-axis bias; -> (row, bias words, codes)."""
    for row in (S.POS_ROW_NARROW, S.POS_ROW_WIDE):
        rowf, ulp = zip(*[vif._ulp(w) for w in row[:3]])
        lo = [min(v[k] for v in locals_) for k in range(3)]
        hi = [max(v[k] for v in locals_) for k in range(3)]
        delta = [-int(round(((lo[k] + hi[k]) / 2.0) / ulp[k])) for k in range(3)]
        codes = [[int(round(v[k] / ulp[k])) + 0x8000 + delta[k] for k in range(3)]
                 for v in locals_]
        if all(0 <= c <= 0xFFFF for cs in codes for c in cs):
            bias = tuple((row[k] + 0x8000 + delta[k]) & 0xFFFFFFFF
                         for k in range(3))
            return row, bias, codes
    raise SkinError("a piece of this model is more than 64 m across inside "
                    "one batch: check the scale of your OBJ")


def _batch_fields(rows, itop, pad=None):
    """rows: [(uv_u16 pair, nrm bytes, local(3), bone, adc, extras)]."""
    n = len(rows)
    locs = [r[2] for r in rows]
    row, bias, codes = _quant_batch(locs)
    pos = b""
    for r, c in zip(rows, codes):
        w = (0x8000 if r[4] else 0) | (S.BONE_BASE + 4 * r[3])
        pos += struct.pack("<4H", c[0], c[1], c[2], w)
    nb = b"".join(bytes(r[1]) for r in rows)
    nb += b"\xcd" * ((-len(nb)) % 4)
    extra = b""
    base = itop + 4
    nrec = 0
    for vi, r in enumerate(rows):
        for eb, wt, off in r[5]:
            extra += struct.pack("<4f", off[0], off[1], off[2], wt)
            extra += struct.pack("<4I", base + 3 * vi, S.BONE_BASE + 4 * eb,
                                 0xCDCDCDCD, 0xCDCDCDCD)
            nrec += 1
    d = dict(itop=itop, n=n, uv=[r[0] for r in rows], nrm_raw=nb, pos_raw=pos,
             pos_row=row, bias=bias, extra_raw=extra,
             extra_addr=(itop + 176 - 2 * nrec) if nrec else None)
    if pad is not None:
        d["pad"] = pad
    return d, nrec


def encode_part(batch_fields, radius):
    """-> packet bytes: prologue + batches + epilogue (16-byte aligned)."""
    nb = len(batch_fields)
    if not 1 <= nb <= 4000:
        raise SkinError("a part holds 1..4000 batches, got %d" % nb)
    out = bytearray()
    out += struct.pack("<II", 0, nb)
    out += S._vc(0x29, 1, 0x6C) + struct.pack("<4f", 0.0, 0.0, 0.0, radius)
    out += S._vc(2, 0, 0x14)
    for d in batch_fields:
        out += S.encode_batch(d, len(out))
    out += S._vc(0x101, 0, 0x01) + S._vc(0, 0, 0x11)
    out += b"\x00" * ((-len(out)) % 16)
    return bytes(out)


# ------------------------------------------------------------------ API

def import_obj(bundle, chunk, obj_text, bone_world, label=""):
    """Replace rigged MOD0 `chunk` with the OBJ. -> (new bundle, report)."""
    r = R.Rsc(bundle, label)
    if not (0 <= chunk < len(r.chunks)) or r.chunks[chunk].tag != "MOD0":
        raise SkinError("chunk %s is not a model" % chunk)
    raw = r.raw(chunk)
    try:
        cont = S.parse_container(raw)
    except S.Unrecognised as ex:
        raise SkinError("this model is not a rigged character part (%s), so "
                        "it cannot take a custom character mesh" % ex)
    if len(bone_world) != cont.bones:
        raise SkinError(
            "rig mismatch: the model is skinned to %d bones but the skeleton "
            "supplied has %d. Nothing was written." % (cont.bones, len(bone_world)))
    try:
        mesh = levelvif.parse_obj(obj_text)
    except levelvif.MeshError as ex:
        raise SkinError(str(ex))
    base = read_vertices(cont, bone_world)
    if not base:
        raise SkinError("the model being replaced has no standard (16-bit) "
                        "vertices to take skinning from")
    grid = _Grid([b["world"] for b in base])

    verts = mesh["verts"]
    for p, uv, col in verts:
        if max(abs(uv[0]), abs(uv[1])) > UV_LIMIT:
            raise SkinError("a UV reaches %.1f; characters store at most +/-%.0f"
                            % (max(abs(uv[0]), abs(uv[1])), UV_LIMIT))
    skin = {}
    dist_sum = dist_max = 0.0
    for i in {i for g in mesh["groups"].values() for t in g for i in t}:
        j, dd = grid.nearest(verts[i][0])
        skin[i] = base[j]
        dist_sum += dd
        dist_max = max(dist_max, dd)

    gnames = list(mesh["groups"])
    parts, tri_total, vtotal, rec_total, nbatches = [], 0, 0, 0, 0
    allloc = []
    for gi, gname in enumerate(gnames):
        tris = mesh["groups"][gname]
        nrm = _normals(verts, tris)
        src = cont.parts[gi] if len(gnames) == len(cont.parts) \
            else cont.parts[0]
        chosen = None
        for cap in CAPS:
            bl = levelvif.stripify(tris, cap=cap)
            ok = True
            for b in bl:
                nrec = sum(len(skin[v]["extras"]) for v, _a in b)
                if 3 * len(b) + 4 + 2 * nrec > VU_BUDGET:
                    ok = False
                    break
            if ok:
                chosen = bl
                break
        if chosen is None:
            raise SkinError("the skinning needs more per-batch room than a "
                            "batch has; simplify the mesh")
        bfs, vcount, icount = [], 0, 0
        stride = S.stride(cont.bones)
        for k, b in enumerate(chosen):
            rows = []
            for vid, adc in b:
                pos, uv, col = verts[vid]
                sk = skin[vid]
                bwp = bone_world[sk["bone"]]
                loc = tuple(pos[a] - bwp[a] for a in range(3))
                allloc.append(loc)
                extras = []
                for eb, wt in sk["extras"]:
                    ew = bone_world[eb]
                    extras.append((eb, wt, tuple(pos[a] - ew[a] for a in range(3))))
                s, t = levelvif.encode_uv(uv[0], uv[1])
                nv = nrm[vid]
                nbytes = tuple(max(0, min(255, int(round((c + 1.0) * 128))))
                               for c in nv)
                rows.append(((s, t), nbytes, loc, sk["bone"], adc, extras))
            itop = S.base_addr(cont.bones) + (k % 3) * stride
            d, nrec = _batch_fields(rows, itop)
            bfs.append(d)
            vcount += len(rows)
            icount += len(rows) + 15 + 2 * nrec
            rec_total += nrec
        radius = max((math.sqrt(sum(c * c for c in l)) for l in allloc), default=0.0)
        parts.append(dict(src=src, batches=bfs, verts=vcount, indices=icount,
                          gname=gname))
        tri_total += len(tris)
        vtotal += vcount
        nbatches += len(bfs)
    radius = max((math.sqrt(sum(c * c for c in l)) for l in allloc), default=0.0)
    radius = radius * 1.1 + 0.01
    newparts = []
    for p in parts:
        newparts.append(dict(resource=p["src"]["resource"],
                             material=p["src"]["material"],
                             packet=encode_part(p["batches"], radius),
                             verts=p["verts"], indices=min(p["indices"], 0xFFFF)))
    if len(newparts) > 255:
        raise SkinError("this OBJ needs %d parts; a container holds 255"
                        % len(newparts))
    new = S.build_container(S.Container(cont.bones, newparts, cont.materials))
    out = R.replace_chunk(bundle, chunk, new, label)
    rep = dict(parts=len(newparts), batches=nbatches, triangles=tri_total,
               vertices=vtotal, secondary_influences=rec_total,
               old_bytes=len(raw), new_bytes=len(new), bundle_bytes=len(out),
               skin_distance_mean=round(dist_sum / max(len(skin), 1), 4),
               skin_distance_max=round(dist_max, 4),
               status="UNTESTED IN-GAME: part index counts and cull radius "
                      "are estimated; skinning is copied from the nearest "
                      "original vertex")
    return out, rep
