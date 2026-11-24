"""A custom model added to a level: an .obj / .glb (read in the browser into a plain mesh), or a game model (.rsc /
.xrsc bundle, .mod / .xmod) read here -- placed where the user clicked, written on every bundle the level has.

2026-11-18. Built with the formats the Blender add-on's level export uses (dataeditor_lib/rdrformats): builder.build
makes the model; the Xbox bundle takes it as rscwrite.mod0 (level mesh) with rscwrite.texture_chunks, the PS2 bundle
as ps2rsc.mod0 (level: vertex colour light) with ps2rsc.texture_chunks -- each bundle at its own chunk numbers. A zone
node names it (.znes resourceIndex for the PS2, .xznes resourceIndexXbox for the Xbox, identity matrix: the vertices
are where they stand). Optionally collision from its own triangles (collcreate.add).

Stores are collcreate's (FolderStore: the mods folder, journalled through Studio.write for a level only it has;
DiscStore: the working disc's .rsc and zone file, journalled).
"""
import base64
import math
import os
import re

TEX_SIDE = 256          # the PS2 holds 4 MB of video memory: a level's textures at most this a side


class ModelError(Exception):
    pass


def _lib():
    from .collcreate import _lib as cl
    cl()
    from rdrformats import builder, mod, rscwrite, ps2rsc, rsc
    return builder, mod, rscwrite, ps2rsc, rsc


def _pow2(rgba):
    """An image resized (nearest) to powers of two between 16 and TEX_SIDE a side."""
    import numpy as np
    h, w = rgba.shape[:2]

    def p2(n):
        k = 16
        while k < n and k < TEX_SIDE:
            k *= 2
        return k
    W, H = p2(w), p2(h)
    if (W, H) == (w, h):
        return np.ascontiguousarray(rgba, np.uint8)
    ys = (np.arange(H) * h // H).clip(0, h - 1)
    xs = (np.arange(W) * w // W).clip(0, w - 1)
    return np.ascontiguousarray(rgba[ys][:, xs], np.uint8)


# ------------------------------------------------------------------------------------------------ reading

def from_mesh(mesh):
    """A plain mesh (the browser's reading of an .obj / .glb): {positions, normals?, uvs?, indices, groups:
    [{start, count, material}], materials: [{name, tex: {w, h, rgba (base64)} | null}]} -> (triangles, materials,
    textures {name: rgba})."""
    import numpy as np
    builder, mod, _rw, _p2, _r = _lib()
    P = mesh.get("positions") or []
    N = mesh.get("normals") or []
    U = mesh.get("uvs") or []
    I = mesh.get("indices") or list(range(len(P) // 3))
    if not P or not I:
        raise ModelError("the model has no faces")
    groups = mesh.get("groups") or [dict(start=0, count=len(I), material=0)]
    mats_in = mesh.get("materials") or [dict(name="material", tex=None)]
    mats, textures = [], {}
    for k, m in enumerate(mats_in):
        tex = m.get("tex")
        tname = None
        if tex and tex.get("rgba"):
            raw = np.frombuffer(base64.b64decode(tex["rgba"]), np.uint8)
            img = raw.reshape(int(tex["h"]), int(tex["w"]), 4)
            tname = re.sub(r"[^A-Za-z0-9_]", "_", (m.get("name") or "tex%d" % k))[:24] or "tex%d" % k
            textures[tname] = img
        mats.append(mod.Material(name=m.get("name") or "material", textures=[tname] if tname else []))
    tris = []
    for g in groups:
        mi = min(int(g.get("material", 0)), len(mats) - 1)
        for t in range(int(g["start"]), int(g["start"]) + int(g["count"]) - 2, 3):
            cs = []
            for i in I[t:t + 3]:
                pos = tuple(P[3 * i:3 * i + 3])
                nrm = tuple(N[3 * i:3 * i + 3]) if len(N) >= 3 * i + 3 else (0.0, 1.0, 0.0)
                uv = tuple(U[2 * i:2 * i + 2]) if len(U) >= 2 * i + 2 else (0.0, 0.0)
                cs.append(builder.Corner(position=pos, normal=nrm, uv1=uv))
            tris.append(builder.Triangle(material=mi, corners=tuple(cs)))
    return tris, mats, textures


def from_native(filename, data):
    """A game model: a level or prop bundle (.rsc PS2 / .xrsc Xbox: every MOD0 of it, with its textures) or a .mod /
    .xmod (no textures of its own: named ones the level has are used). -> (triangles, materials, textures)."""
    builder, mod, _rw, ps2rsc, R = _lib()
    low = filename.lower()
    tris, mats, textures = [], [], {}
    if low.endswith((".rsc", ".xrsc")):
        chunks = R.read_chunks(data)
        xbox = low.endswith(".xrsc")
        names = {}
        for i, c in enumerate(chunks, 1):
            if c.tag != "TEX0":
                continue
            try:
                if xbox:
                    name, rgba = R.read_texture(chunks, i)
                else:
                    name, rgba = ps2rsc.texture_of(chunks, i)
            except Exception:                               # noqa: BLE001
                continue
            if rgba is None:
                continue
            names[i] = name
            textures[name] = rgba
        for c in chunks:
            if c.tag != "MOD0":
                continue
            try:
                m = R.mod0_to_model(R.read_mod0(c.data), names) if xbox else \
                    ps2rsc.to_model(ps2rsc.read_mod0(c.data), names)
            except Exception:                               # noqa: BLE001
                continue
            t, ms = builder.triangles_of(m)
            base = len(mats)
            mats += ms
            tris += [builder.Triangle(material=x.material + base, corners=x.corners) for x in t]
    elif low.endswith((".mod", ".xmod")):
        m = mod.read(data)
        tris, mats = builder.triangles_of(m)
    else:
        raise ModelError("%s: not a model this reads (.obj .glb .rsc .xrsc .mod .xmod)" % filename)
    if not tris:
        raise ModelError("%s holds no model this could read" % filename)
    textures = {k: v for k, v in textures.items() if any(k in (mt.textures or []) for mt in mats)}
    return tris, mats, textures


def from_bundle_chunk(data, res_index, xbox=False):
    """One model of a level or prop bundle (its MOD0 at resourceIndex, 1-based) with the textures it draws with --
    a model of another level, for the Library's Place. -> (triangles, materials, textures)."""
    builder, mod, _rw, ps2rsc, R = _lib()
    chunks = R.read_chunks(data)
    if not 0 < res_index <= len(chunks) or chunks[res_index - 1].tag != "MOD0":
        raise ModelError("chunk %d of that bundle is not a model" % res_index)
    at = 0x24 if xbox else 0xA0
    names = {}
    for i, c in enumerate(chunks, 1):
        if c.tag == "TEX0" and b"\0" in c.data[at:]:
            names[i] = c.data[at:c.data.index(b"\0", at)].decode("latin-1")
    c = chunks[res_index - 1]
    m = R.mod0_to_model(R.read_mod0(c.data), names) if xbox else ps2rsc.to_model(ps2rsc.read_mod0(c.data), names)
    tris, mats = builder.triangles_of(m)
    want = {t.lower() for mt in mats for t in (mt.textures or [])}
    textures = {}
    for i, n in names.items():
        if n.lower() not in want or n in textures:
            continue
        try:
            _name, rgba = R.read_texture(chunks, i) if xbox else ps2rsc.texture_of(chunks, i)
        except Exception:                                   # noqa: BLE001
            continue
        if rgba is not None:
            textures[n] = rgba
    if not tris:
        raise ModelError("that model has no faces this could read")
    return tris, mats, textures


def add_to_level(studio, arc, tris, mats, textures, name="custom", collision=True, manifest=None, area=None,
                 label="import model"):
    """The model into a level through the Studio: the mods folder's bundles (journalled when the level is only
    there), else the working disc's. -> add()'s result plus where it went."""
    from . import collcreate as CC
    from .studio import _data_editor_for
    store = CC.folder_store(_data_editor_for(studio), arc)
    lvl = studio.level(arc, manifest=manifest, area=area)
    if store is not None and studio._mods_folder_of(arc):
        grp = studio.journal.new_group()
        store.writer = lambda n, d, _g=grp: studio.write(arc, n, d, label, _g)
    if store is None:
        lvl._load_rsc()
        if not lvl.rsc_name:
            raise ModelError("this part of the level has no model bundle")
        store = CC.DiscStore(studio, arc, lvl, label)
    r = add(store, lvl, tris, mats, textures, name, collision)
    if store.kind == "folder" and store.writer is None:
        CC.remember(store)
    studio._drop_level(arc)
    r["where"] = "folder" if (store.kind == "folder" and store.writer is None) else "journal"
    return r


# ------------------------------------------------------------------------------------------------ placing

def place(tris, pos, turn_deg=0.0, scale=1.0, keep_place=False):
    """The triangles stood on pos (the middle of their bottom there), turned turn_deg round the up axis and scaled --
    or left where they are (keep_place: a level's own model, already in level space)."""
    builder = _lib()[0]
    if keep_place:
        return tris
    ps = [c.position for t in tris for c in t.corners]
    lo = [min(p[k] for p in ps) for k in range(3)]
    hi = [max(p[k] for p in ps) for k in range(3)]
    base = ((lo[0] + hi[0]) / 2, lo[1], (lo[2] + hi[2]) / 2)
    a = math.radians(turn_deg)
    ca, sa = math.cos(a), math.sin(a)
    out = []
    for t in tris:
        cs = []
        for c in t.corners:
            x, y, z = [(c.position[k] - base[k]) * scale for k in range(3)]
            nx, ny, nz = c.normal
            cs.append(builder.Corner(position=(ca * x + sa * z + pos[0], y + pos[1], -sa * x + ca * z + pos[2]),
                                     normal=(ca * nx + sa * nz, ny, -sa * nx + ca * nz), uv1=c.uv1))
        out.append(builder.Triangle(material=t.material, corners=tuple(cs)))
    return out


# ------------------------------------------------------------------------------------------------ writing

def _zone_block(name, ps2_num, xbox_num, nl):
    return nl.join(["node %s {" % name, "\tnumLods 1 ", "\tresourceIndex %d " % ps2_num,
                    "\tresourceIndexXbox %d " % xbox_num, "\tmatrix", "\t{",
                    "\t\t1.000000 \t0.000000 \t0.000000 ", "\t\t0.000000 \t1.000000 \t0.000000 ",
                    "\t\t0.000000 \t0.000000 \t1.000000 ", "\t\t0.000000 \t0.000000 \t0.000000 ", "\t}",
                    "\tattributes 0 {", "\t}", "}"])


def _zone_files(store, lvl):
    """[(file, platform)] the new node goes in: the folder's (each of .znes PS2 / .xznes Xbox, the _custom one first),
    or the disc level's roomiest .znes."""
    if store.kind == "folder":
        have = sorted(f for f in os.listdir(store.folder)
                      if f.lower().startswith(store.stem + "_") and f.lower().endswith((".znes", ".xznes")))
        # one zone file when the level has its PS2 bundle: the .znes, carrying both games' numbers (the Xbox build
        # writes the .xznes from it). A second one beside it went stale with the first delete (2026-11-18)
        if store.ps2 and any(f.lower().endswith(".znes") for f in have):
            have = [f for f in have if f.lower().endswith(".znes")]
        out = []
        for ext, plat in ((".znes", "ps2"), (".xznes", "xbox")):
            fs = [f for f in have if f.lower().endswith(ext)]
            if fs:
                fs.sort(key=lambda f: (not f.lower().endswith("_custom" + ext), f))
                out.append((fs[0], plat))
        return out
    best = lvl.roomiest(".znes") if lvl is not None else None
    return [(best, "ps2")] if best else []


def _new_zone_file(store):
    """A level with no zone file yet (a New .dat blank slate): lvl_<x>_custom.znes (and .xznes when the level has its
    Xbox bundle), empty, listed in its .lvl's Zones. -> [(file, platform)]."""
    out = []
    for ext, plat, have in ((".znes", "ps2", store.ps2), (".xznes", "xbox", getattr(store, "xbox", None))):
        if not have or (plat == "xbox" and store.ps2):
            continue                # with a PS2 bundle: the .znes alone, both numbers in it
        name = store.stem + "_custom" + ext
        store.write(name, b"NumInstNodes 0 \r\n\r\n")
        out.append((name, plat))
    lvl = next((f for f in os.listdir(store.folder) if f.lower() == store.stem + ".lvl"), None)
    if lvl and out:
        text = store.read(lvl).decode("latin-1")
        want = store.stem + "_custom.znes"
        if want not in text:
            text = re.sub(r"(?is)(^\s*Zones\s*\{)", lambda m: m.group(1) + "\r\n" + want + "  ", text, count=1,
                          flags=re.M)
            store.write(lvl, text.encode("latin-1"))
    return out


def add(store, lvl, tris, mats, textures, name="custom", collision=True):
    """The model into the level. -> {name, chunks, zone_files, textures, triangles, collision}."""
    builder, mod, rscwrite, ps2rsc, R = _lib()
    from . import rsc as SR
    from . import collcreate as CC
    if not tris:
        raise ModelError("the model has no faces")
    for t in tris:
        t.corners = tuple(builder.Corner(position=c.position, normal=c.normal, uv1=c.uv1, color=(1.0, 1.0, 1.0, 1.0))
                          for c in t.corners)
    model = builder.build(tris, mats)
    stem = re.sub(r"[^A-Za-z0-9_]+", "_", name or "custom").strip("_").lower()[:20] or "custom"
    texs = [(("%s_%s" % (stem, k))[:30], _pow2(v), k) for k, v in textures.items()]
    nums = {}
    for fname, xbox in ((store.ps2, False), (getattr(store, "xbox", None), True)):
        if not fname:
            continue
        data = store.read(fname)
        if xbox:
            chunks = [(c.tag, c.data) for c in R.read_chunks(data)]
            refs = {}
            for tname, rgba, orig in texs:
                n = len(chunks)
                chunks += rscwrite.texture_chunks(tname, rgba, pal_num=n + 2, mip_num=n + 1)
                refs[orig.lower()] = n + 3
            chunks.append(("MOD0", rscwrite.mod0(model, lambda t: refs.get((t or "").lower(), 0), level=True,
                                                 keep_uv2=False)))
            nums["xbox"] = len(chunks)
            store.write(fname, rscwrite.write_bundle(chunks, rscwrite.ALIGN))
        else:
            refs = {}
            out = data
            for tname, rgba, orig in texs:
                n = len(R.read_chunks(out))
                mip, pal, tex0 = ps2rsc.texture_chunks(tname, rgba, pal_num=n + 2, mip_num=n + 1, level=True)
                for tag, payload in (mip, pal, tex0):
                    out, _i = SR.append_chunk(out, tag, payload, "model import")
                refs[orig.lower()] = n + 3
            mats_in, bones = ps2rsc.model_input(model, refs, level=True)
            out, idx = SR.append_chunk(out, "MOD0", ps2rsc.mod0(mats_in, bones, level=True), "model import")
            nums["ps2"] = idx + 1
            store.write(fname, out)
    if not nums:
        raise ModelError("this level has no model bundle (.rsc / .xrsc) to add a model to")
    zones = _zone_files(store, lvl)
    if not zones and store.kind == "folder":
        zones = _new_zone_file(store)
    if not zones:
        raise ModelError("this level has no zone file (.znes / .xznes) to place a model with")
    taken = set()
    for zf, _p in zones:
        taken |= set(re.findall(r"node\s+(\S+)\s*\{", store.read(zf).decode("latin-1")))
    k = 1
    while "zone_%s_%02d" % (stem, k) in taken:
        k += 1
    zname = "zone_%s_%02d" % (stem, k)
    for zf, plat in zones:
        text = store.read(zf).decode("latin-1")
        nl = "\r\n" if "\r\n" in text else "\n"
        if plat == "ps2":
            # both numbers: the Xbox bundle's (the folder's .xrsc), else the PS2 one (the Xbox build converts the PS2
            # bundle keeping every chunk's number). 0 there was a model the Xbox game loaded from chunk 0 and crashed on
            # (2026-11-18)
            block = _zone_block(zname, nums.get("ps2", 0),
                                nums["xbox"] if "xbox" in nums else (0 if getattr(store, "xbox", None) else
                                                                     nums.get("ps2", 0)), nl)
        else:
            block = _zone_block(zname, 0, nums.get("xbox", 0), nl)
        text = CC._bump(text.rstrip("\r\n \t") + nl + block + nl, +1)
        store.write(zf, text.encode("latin-1"))
    col = None
    if collision:
        # as the add-on builds level collision: corners welded, cut into pieces of at most 2,500 faces and 25 m on
        # their shortest side, named after the zone (bound_ground_02, bound_ground_02p2 ...) -- 2026-11-18
        try:
            names = set_model_collision(store, zname, model_world_points(tris))
            col = names[0] + (" (+%d piece(s))" % (len(names) - 1) if len(names) > 1 else "")
        except (CC.CollError, ModelError) as ex:
            col = "none (%s)" % ex
    return dict(name=zname, chunks=nums, zone_files=[z for z, _p in zones], textures=len(texs),
                triangles=len(tris), collision=col)


# ------------------------------------------------------------------------------------------------ reshaping
#
# 2026-11-18. Move / turn / scale a model of a level only the mods folder has (the Shape boxes, a surface drag, a
# mass edit). geom_edit measures every edit against the unedited disc's PS2 bundle, which has no such level ("not
# on disc"). Here the model is decoded from the level's own bundles, its corners moved, and rebuilt -- on EVERY
# bundle the level has (PS2 .rsc, Xbox .xrsc, each at its own chunk number) -- and its collision shell (bound_X of
# zone_X) takes the same move/turn/scale (boundmove), all in one journal group: one Ctrl+Z.
#
# The Shape boxes show totals. A level of the mods folder has no unedited copy to measure from, so the first edit of
# a model this session keeps its bytes as the reference; each edit is the total applied to that reference (no drift),
# and every result is remembered by its bytes, so after an undo the boxes read the total that is there again.

IDENT = dict(tx=0.0, ty=0.0, tz=0.0, s=1.0, rot=0.0)


def shape_of_ops(ops):
    t = dict(IDENT)
    for st in ops or []:
        k = st.get("op")
        if k == "translate":
            t["tx"] += float(st.get("x", 0))
            t["ty"] += float(st.get("y", 0))
            t["tz"] += float(st.get("z", 0))
        elif k == "scale":
            t["s"] *= float(st.get("x", 1))
        elif k == "rotate_y":
            t["rot"] += float(st.get("deg", 0))
    return t


def _ops_steps(ops):
    out = []
    for st in ops or []:
        k = st.get("op")
        if k == "translate":
            out.append(("t", float(st.get("x", 0)), float(st.get("y", 0)), float(st.get("z", 0))))
        elif k == "scale":
            sx = float(st.get("x", 1))
            out.append(("s", sx, float(st.get("y", sx)), float(st.get("z", sx))))
        elif k == "rotate_y":
            out.append(("r", math.radians(float(st.get("deg", 0))), 0.0, 0.0))
        else:
            raise ModelError("unknown op %r" % k)
    return out


def _transform_tris(tris, steps, about):
    import dataclasses
    builder = _lib()[0]
    ax, ay, az = about

    def pt(p):
        x, y, z = p
        for k, a, b, c in steps:
            if k == "t":
                x, y, z = x + a, y + b, z + c
            elif k == "s":
                x, y, z = ax + (x - ax) * a, ay + (y - ay) * b, az + (z - az) * c
            else:
                ca, sa = math.cos(a), math.sin(a)
                x, z = ax + (x - ax) * ca + (z - az) * sa, az - (x - ax) * sa + (z - az) * ca
        return (x, y, z)

    def nrm(n):
        x, y, z = n
        for k, a, b, c in steps:
            if k == "s":
                x, y, z = x / (a or 1), y / (b or 1), z / (c or 1)
                ln = math.sqrt(x * x + y * y + z * z) or 1.0
                x, y, z = x / ln, y / ln, z / ln
            elif k == "r":
                ca, sa = math.cos(a), math.sin(a)
                x, z = x * ca + z * sa, -x * sa + z * ca
        return (x, y, z)
    out = []
    for t in tris:
        cs = tuple(dataclasses.replace(c, position=pt(c.position), normal=nrm(c.normal)) for c in t.corners)
        out.append(builder.Triangle(material=t.material, corners=cs))
    return out


def _decode(data, num, xbox):
    """(triangles, materials, texture name -> TEX0 number) of MOD0 chunk `num` (1-based) of a bundle."""
    builder, _mod, _rw, ps2rsc, R = _lib()
    chunks = R.read_chunks(data)
    if not 0 < num <= len(chunks) or chunks[num - 1].tag != "MOD0":
        raise ModelError("chunk %d of the %s bundle is not a model" % (num, "Xbox" if xbox else "PS2"))
    at = 0x24 if xbox else 0xA0
    names = {}
    for i, c in enumerate(chunks, 1):
        if c.tag == "TEX0" and b"\0" in c.data[at:]:
            names[i] = c.data[at:c.data.index(b"\0", at)].decode("latin-1")
    d = chunks[num - 1].data
    m = R.mod0_to_model(R.read_mod0(d), names) if xbox else ps2rsc.to_model(ps2rsc.read_mod0(d), names)
    tris, mats = builder.triangles_of(m)
    return tris, mats, {n.lower(): i for i, n in names.items()}


def _decode_ref(ref_payload, data, num, xbox):
    """_decode of a reference copy of chunk `num`, read in the bundle as it is now (its texture numbers)."""
    R = _lib()[4]
    if R.read_chunks(data)[num - 1].data == ref_payload:
        return _decode(data, num, xbox)
    return _decode(_put(data, num, "MOD0", ref_payload, xbox), num, xbox)


def _encode(tris, mats, refs, xbox):
    builder, _mod, rscwrite, ps2rsc, _R = _lib()
    model = builder.build(tris, mats)
    if xbox:
        return rscwrite.mod0(model, lambda t: refs.get((t or "").lower(), 0), level=True, keep_uv2=False)
    mats_in, bones = ps2rsc.model_input(model, refs, level=True)
    return ps2rsc.mod0(mats_in, bones, level=True)


def _chunk(data, num):
    return _lib()[4].read_chunks(data)[num - 1].data


def _put(data, num, tag, payload, xbox):
    """The bundle with chunk `num` (1-based, a `tag`) replaced; chunk numbers never move."""
    _b, _m, rscwrite, _p, R = _lib()
    if not xbox:
        from . import rsc as SR
        return SR.replace_chunk(data, num - 1, payload, "reshape")
    chunks = [(c.tag, c.data) for c in R.read_chunks(data)]
    if not 0 < num <= len(chunks) or chunks[num - 1][0] != tag:
        raise ModelError("chunk %d of the Xbox bundle is not a %s" % (num, tag))
    chunks[num - 1] = (tag, payload)
    return rscwrite.write_bundle(chunks, rscwrite.ALIGN)


def _zone_nums(store, name):
    """{'ps2': n, 'xbox': m} of zone node `name` (its .znes resourceIndex, its .xznes resourceIndexXbox)."""
    from . import collcreate as CC
    nums = {}
    # the .znes first (the editor's; it carries both numbers), an .xznes only for what it lacks
    for f in sorted(os.listdir(store.folder), key=lambda n: (n.lower().endswith(".xznes"), n.lower())):
        lf = f.lower()
        if not (lf.startswith(store.stem + "_") and lf.endswith((".znes", ".xznes"))):
            continue
        text = store.read(f).decode("latin-1")
        span = CC._find_node(text, name)
        if not span:
            continue
        p, x = CC._node_numbers(text, span)
        if not lf.endswith(".xznes") and p > 0:
            nums.setdefault("ps2", p)
        if x > 0:
            nums.setdefault("xbox", x)
    return nums


def _bound_nums(store, name):
    from . import collcreate as CC
    _f, text, span = CC._locate(store, name)
    p, x = CC._node_numbers(text, span)
    return {k: v for k, v in (("ps2", p), ("xbox", x)) if v > 0}


def _bundle(store, plat):
    return store.ps2 if plat == "ps2" else getattr(store, "xbox", None)


def _ctr(tris):
    ps = [c.position for t in tris for c in t.corners]
    return [(min(p[k] for p in ps) + max(p[k] for p in ps)) / 2.0 for k in range(3)]


def _bound_ctr(raw):
    from . import geomedit
    vs = geomedit.bound_verts(raw)["verts"]
    return [(min(v[k] for v in vs) + max(v[k] for v in vs)) / 2.0 for k in range(3)]


def _refs(studio):
    if not hasattr(studio, "_shape_refs"):
        studio._shape_refs = {}
    return studio._shape_refs


def _parts(store, lvl, node):
    """[(role, name, nums, tag)] what a reshape of `node` writes: a model and its linked collision shell, or a
    collision shell alone."""
    from . import collcreate as CC
    out = []
    if node.kind == "zone":
        nums = {k: v for k, v in _zone_nums(store, node.name).items() if _bundle(store, k)}
        if not nums:
            raise ModelError("%s is in none of this level's zone files" % node.name)
        out.append(("model", node.name, nums, "MOD0"))
        pu = lvl.links().get(node.uid)
        if pu is not None:
            b = lvl.by_uid(pu)
            try:
                bn = {k: v for k, v in _bound_nums(store, b.name).items() if _bundle(store, k)}
            except CC.CollError:
                bn = {}
            if bn:
                out.append(("collision", b.name, bn, "BND0"))
    elif node.kind == "bound":
        bn = {k: v for k, v in _bound_nums(store, node.name).items() if _bundle(store, k)}
        if not bn:
            raise ModelError("%s points at no collision chunk" % node.name)
        out.append(("collision", node.name, bn, "BND0"))
    else:
        raise ModelError("%s is not a model or a collision shell" % node.name)
    return out


def _key_bytes(store, part):
    _role, _name, nums, _tag = part
    plat = "ps2" if "ps2" in nums else "xbox"
    return _chunk(store.read(_bundle(store, plat)), nums[plat])


def _sha(b):
    import hashlib
    return hashlib.sha1(b).hexdigest()


def node_for_chunk(lvl, chunk):
    """The zone or bound node drawing chunk `chunk` (0-based) of the editor's bundle, or None."""
    from . import scene
    for n in lvl.nodes:
        if n.kind in ("zone", "bound") and scene.chunk_of(n) == chunk:
            return n
    return None


def shape_now(studio, arc, lvl, node):
    """The total move/turn/scale on `node` since this session first reshaped it (identity if never)."""
    from . import collcreate as CC
    from .studio import _data_editor_for
    store = CC.folder_store(_data_editor_for(studio), arc)
    if store is None:
        return dict(IDENT)
    try:
        parts = _parts(store, lvl, node)
        h = _sha(_key_bytes(store, parts[0]))
    except (ModelError, CC.CollError, OSError, IndexError):
        return dict(IDENT)
    e = _refs(studio).get((arc.upper(), node.name))
    return dict(e["hist"][h]) if e and h in e["hist"] else dict(IDENT)


def reshape(studio, arc, lvl, nodes, ops, label, pivot=None, total=True, collision=True):
    """Move / turn / scale models (and their collision) of a mods-folder level, on every bundle, one Ctrl+Z.

    total: ops are the total against this session's reference copy (the Shape boxes); else a change from now (a
    mass edit). pivot: world point to turn and scale about (default: each model's own middle)."""
    from . import collcreate as CC, boundmove, collparts
    from .studio import _data_editor_for
    store = CC.folder_store(_data_editor_for(studio), arc)
    if store is None:
        raise ModelError("this level has no model bundle in the mods folder")
    grp = studio.journal.new_group()
    store.writer = lambda n, d, _g=grp: studio.write(arc, n, d, label, _g)
    steps = _ops_steps(ops)
    shape = shape_of_ops(ops)
    ident = not steps or all(abs(shape[k] - IDENT[k]) < 1e-9 for k in IDENT)
    refs = _refs(studio)
    pending = {}                                    # bundle file -> its new bytes, written once each
    done, notes, keys = [], [], []
    rebuild = []                                    # (zone name, world triangles): collision made again below

    def cur(fname):
        if fname not in pending:
            pending[fname] = store.read(fname)
        return pending[fname]

    for node in nodes:
        parts = _parts(store, lvl, node)
        key = (arc.upper(), node.name)
        h = _sha(_key_bytes(store, parts[0]))
        e = refs.get(key)
        if not total or e is None or h not in e["hist"]:
            e = dict(ref={}, hist={h: dict(IDENT)})
            for role, name, nums, tag in parts:
                for plat, num in nums.items():
                    e["ref"][(role, plat)] = _chunk(store.read(_bundle(store, plat)), num)
        zpos = list(node.xyz()) if node.kind == "zone" else [0.0, 0.0, 0.0]
        turned = not collparts.identity_rot(node)
        about_local = None
        for role, name, nums, tag in parts:
            for plat, num in nums.items():
                fname, xbox = _bundle(store, plat), plat == "xbox"
                ref = e["ref"].get((role, plat))
                if ref is None:
                    ref = e["ref"][(role, plat)] = _chunk(cur(fname), num)
                if role == "model":
                    tris, mats, trefs = _decode_ref(ref, cur(fname), num, xbox)
                    if about_local is None:
                        about_local = [pivot[k] - zpos[k] for k in range(3)] if pivot else _ctr(tris)
                    moved = tris if ident else _transform_tris(tris, steps, about_local)
                    payload = ref if ident else _encode(moved, mats, trefs, xbox)
                    if collision and not turned and not any(z == node.name for z, _w in rebuild):
                        rebuild.append((node.name, model_world_points(moved, zpos)))
                elif node.kind == "zone":
                    continue                    # a model's own collision: made again from the model, below
                else:
                    if turned and node.kind == "zone":
                        notes.append("%s was not moved with it (the model is turned by its node)" % name)
                        continue
                    bnode = next((n for n in lvl.nodes if n.kind == "bound" and n.name == name), None)
                    bpos = list(bnode.xyz()) if bnode is not None else [0.0, 0.0, 0.0]
                    if about_local is not None:
                        about = [about_local[k] + zpos[k] - bpos[k] for k in range(3)]
                    elif pivot:
                        about = [pivot[k] - bpos[k] for k in range(3)]
                    else:
                        about = _bound_ctr(ref)
                        about_local = list(about)
                    out = ref
                    if not ident:
                        try:
                            for k, a, b, c in steps:
                                if k == "t":
                                    out = boundmove.translate(out, a, b, c)
                                elif k == "s":
                                    out = boundmove.scale(out, a, b, c, about=about)
                                else:
                                    out = boundmove.rotate_y(out, a, about=about)
                        except boundmove.NotEditable as ex:
                            notes.append("%s could not be moved with it: %s" % (name, ex))
                            continue
                        bad = sorted(k for k, v in boundmove.audit(out).items() if v is False)
                        if bad:
                            notes.append("%s was not moved with it (it would no longer agree with itself: %s)"
                                         % (name, ", ".join(bad)))
                            continue
                    payload = out
                pending[fname] = _put(cur(fname), num, tag, payload, xbox)
            if name not in done:
                done.append(name)
        keys.append((key, e, parts))
    changed = [f for f, d in pending.items() if d != store.read(f)]
    for f in changed:
        store.write(f, pending[f])
    for zname, wt in rebuild:
        # the model's collision made again from the model as it now stands (welded, cut into pieces): a shell stretched
        # in place by a scale loaded in the game and stopped nothing (2026-11-18)
        try:
            names = set_model_collision(store, zname, wt)
            done += [n for n in names if n not in done]
            changed.append("collision")
        except Exception as ex:                             # noqa: BLE001
            notes.append("%s: its collision was not made again (%s)" % (zname, ex))
    for key, e, parts in keys:
        if total:
            e["hist"][_sha(_key_bytes(store, parts[0]))] = dict(shape)
            refs[key] = e
        else:
            refs.pop(key, None)
    studio._drop_level(arc)
    return dict(ok=True, changed=bool(changed), moved=done, files=changed,
                collision_note=("; ".join(notes) + ".") if notes else None)


def selection_centre(studio, arc, lvl, nodes):
    """The world middle of several models (and collision shells): the one pivot a mass edit turns and scales about."""
    from . import collcreate as CC
    from .studio import _data_editor_for
    store = CC.folder_store(_data_editor_for(studio), arc)
    lo, hi = [1e30] * 3, [-1e30] * 3
    for node in nodes:
        role, _name, nums, _tag = _parts(store, lvl, node)[0]
        plat = "ps2" if "ps2" in nums else "xbox"
        data = store.read(_bundle(store, plat))
        if role == "model":
            pos = [c.position for t in _decode(data, nums[plat], plat == "xbox")[0] for c in t.corners]
            off = list(node.xyz())
        else:
            from . import geomedit
            pos = geomedit.bound_verts(_chunk(data, nums[plat]))["verts"]
            off = [0.0, 0.0, 0.0]
        for p in pos:
            for k in range(3):
                lo[k] = min(lo[k], p[k] + off[k])
                hi[k] = max(hi[k], p[k] + off[k])
    return [(lo[k] + hi[k]) / 2.0 for k in range(3)]


def empty_model_chunks(store, nums):
    """Models taken out of a level (their zone nodes deleted): each chunk made a speck far under the level, on every
    bundle -- a chunk keeps its number (nothing may move), and left as it was the editor still drew it as a nameless
    surface: the deleted model stood there still (2026-11-18). nums: [{'ps2': n, 'xbox': m}]."""
    from . import collcreate as CC
    builder, mod = _lib()[0], _lib()[1]
    tri = [builder.Triangle(material=0, corners=tuple(builder.Corner(position=p, normal=(0.0, 1.0, 0.0), uv1=(0.0, 0.0))
                                                      for p in CC.PLACEHOLDER))]
    mats = [mod.Material(name="removed", textures=[])]
    for plat in ("ps2", "xbox"):
        fname = _bundle(store, plat)
        idxs = sorted({n[plat] for n in nums if n.get(plat, 0) > 0})
        if not fname or not idxs:
            continue
        data = store.read(fname)
        payload = _encode(tri, mats, {}, plat == "xbox")
        for i in idxs:
            try:
                data = _put(data, i, "MOD0", payload, plat == "xbox")
            except (ModelError, ValueError, IndexError):
                continue
        store.write(fname, data)


def chunk_users(store):
    """{('zone'|'bound', plat, number): count} of every node of a mods-folder level, by chunk."""
    from . import collcreate as CC
    out = {}
    for f in os.listdir(store.folder):
        lf = f.lower()
        if not lf.startswith(store.stem + "_") or not lf.endswith((".znes", ".xznes", ".bnds")):
            continue
        kind = "bound" if lf.endswith(".bnds") else "zone"
        text = store.read(f).decode("latin-1")
        for m in CC.NODE_RE.finditer(text):
            span = CC._find_node(text, m.group(1))
            if not span:
                continue
            p, x = CC._node_numbers(text, span)
            if lf.endswith(".xznes"):
                p = 0
            for plat, n in (("ps2", p), ("xbox", x)):
                if n > 0:
                    out[(kind, plat, n)] = out.get((kind, plat, n), 0) + 1
    return out


# ------------------------------------------------------------------------------------------------ a model's collision
#
# 2026-11-18. A model's collision built as the Blender add-on builds level collision (levelcollision.build_bounds,
# the one proven in the game): its corners welded (neighbouring triangles share their points), faces with no area
# left out, and cut into pieces of at most PIECE_FACES faces whose shortest side is at most PIECE_SHORT metres (a
# bound's skin is 3.5% of its shortest side: a tall one flung the player away). The pieces are named after the
# model's zone: zone_ground_02 -> bound_ground_02, bound_ground_02p2, bound_ground_02p3 ... (the first is its linked
# shell; the rest belong to it by name). A model scaled in the editor gets its collision rebuilt this way from the
# scaled model -- stretched in place, a scaled floor's shell loaded in the game and stopped nothing.

PIECE_FACES = 2500
PIECE_SHORT = 60.0     # skin 2.1 m at most: inside the shipped shells' (up to 5.1 m; Flash's whole ground: 2.8 m). 25 m
                       # cut a scaled floor into 173 pieces


def collision_pieces(tri_points):
    """[(verts, faces)] collision pieces of triangles [(a, b, c)] (game-space points)."""
    index, verts, faces = {}, [], []
    for tri in tri_points:
        ids = []
        for p in tri:
            key = (round(p[0], 4), round(p[1], 4), round(p[2], 4))
            if key not in index:
                index[key] = len(verts)
                verts.append(key)
            ids.append(index[key])
        if len(set(ids)) < 3:
            continue
        a, b, c = (verts[i] for i in ids)
        ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
        cx, cy, cz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        if (cx * cx + cy * cy + cz * cz) ** 0.5 / 2 < 1e-4:
            continue
        faces.append(ids)
    todo, pieces = [faces] if faces else [], []
    while todo:
        part = todo.pop()
        pv = [verts[i] for f in part for i in f]
        short = min(max(v[a] for v in pv) - min(v[a] for v in pv) for a in range(3))
        if len(part) <= PIECE_FACES and (short <= PIECE_SHORT or len(part) < 2):
            pieces.append(part)
            continue
        cents = [[sum(verts[i][a] for i in f) / 3.0 for a in range(3)] for f in part]
        axis = max(range(3), key=lambda a: max(c[a] for c in cents) - min(c[a] for c in cents))
        order = sorted(range(len(part)), key=lambda k: cents[k][axis])
        half = len(order) // 2
        todo += [[part[k] for k in order[:half]], [part[k] for k in order[half:]]]
    out = []
    for part in pieces:
        used, local, fs = {}, [], []
        for f in part:
            ids = []
            for i in f:
                if i not in used:
                    used[i] = len(local)
                    local.append(list(verts[i]))
                ids.append(used[i])
            fs.append(ids)
        out.append((local, fs))
    return out


def piece_names(zone_name, n):
    base = "bound_" + re.sub(r"^zone_", "", zone_name)
    return [base if i == 0 else "%sp%d" % (base, i + 1) for i in range(n)]


def pieces_of(names, zone_name):
    """The collision pieces of a model, by name, of the bound node names given."""
    base = "bound_" + re.sub(r"^zone_", "", zone_name)
    return [n for n in names if n == base or re.fullmatch(re.escape(base) + r"p\d+", n)]


def _bnds_names(store):
    from . import collcreate as CC
    out = []
    for f in store.bnds:
        out += CC.NODE_RE.findall(store.read(f).decode("latin-1"))
    return out


def set_model_collision(store, zone_name, tri_points, solid=True):
    """The model's collision pieces made these triangles' (world space): the pieces it has replaced in place (each
    keeps its chunk numbers), new ones added, ones it no longer needs taken out (their chunks emptied). -> names."""
    from . import collcreate as CC
    shapes = collision_pieces(tri_points)
    if not shapes:
        raise ModelError("the model has no faces to collide with")
    have = pieces_of(_bnds_names(store), zone_name)
    want = piece_names(zone_name, len(shapes))
    keep = [n for n in want if n in have]
    for n, (vs, fs) in zip(want, shapes):
        if n in keep:
            CC.replace(store, n, vs, fs)
    new = [(n, s) for n, s in zip(want, shapes) if n not in keep]
    if new:
        CC.add_many(store, [s for _n, s in new], solid=solid, names=[n for n, _s in new])
    gone = [n for n in have if n not in want]
    if gone:
        CC.remove_many(store, gone)
    return want


def model_world_points(tris, offset=(0.0, 0.0, 0.0)):
    return [tuple(tuple(c.position[k] + offset[k] for k in range(3)) for c in t.corners) for t in tris]
