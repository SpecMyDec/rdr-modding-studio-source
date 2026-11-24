"""RDR Modding Studio -- local web app backend.

Serves a JSON API over the toolkit plus the three.js front end in web/.
Stdlib only. Binds to 127.0.0.1 so nothing is exposed off the machine.

    python RDR_Studio.py
"""

import base64
import json
import mimetypes
import os
import posixpath
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import zipfile
from typing import Any
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .dave import SlotOverflow
from . import iso9660
from . import frontmenu
# `api` is imported at module level for DiscLocked, which Session.write
# raises. api imports json and time and nothing of ours, so there is no
# cycle; the older `from . import api` inside a handler stays as it was.
from . import api
from .game import Game
from .journal import Journal, apply_mod, read_mod
from .level import Level, list_levels
from .scene import splice
from . import config as cfg

mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/javascript", ".mjs")

HANDOVER_GRACE = 90.0    # seconds to let the emulator open the ISO

def _app_root():
    """The directory the app's own files sit in, frozen or not.

    PyInstaller unpacks the bundle somewhere else entirely and leaves
    `__file__` pointing inside it, so paths built by walking up from this
    module resolve one directory above the bundle and silently miss. That is
    not a crash -- it is a tab that renders empty, which is how the Roadmap
    was lost in the first frozen build and not noticed until someone opened
    the one tab that needed it.

    `sys._MEIPASS` is PyInstaller's own answer to the question and is present
    only when frozen, so the unfrozen path is unchanged.
    """
    import sys
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return base
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource(*parts):
    """A file shipped alongside the app. Tries the bundle, then the repo.

    Both are checked because the layouts genuinely differ: a frozen build
    flattens `03_Tools/web` to `web`, while the repo keeps `ROADMAP.md` one
    level ABOVE `03_Tools`. Returning the first path that exists means one
    helper serves both without either having to know about the other.
    """
    root = _app_root()
    candidates = [os.path.join(root, *parts),
                  os.path.join(root, "..", *parts)]
    for path in candidates:
        if os.path.exists(path):
            return os.path.abspath(path)
    return os.path.abspath(candidates[0])


WEB_ROOT = resource("web")

# The environment a level is wrapped in, grouped the way you would think
# about changing it rather than by file extension. Every one of these is
# plain text and editable field-by-field -- see props.py.
# One-click looks. Each names fields by (extension, field path) so a preset
# works on any level -- the actual filenames differ per area. Values are
# written with the same 6-decimal formatting the game's own tools used, so a
# diff of a preset-touched file reads like the original.
#
# `.lgt` Ambient is the colour every surface is multiplied by; `.fog`
# ColorStart/ColorEnd and ClampDist are the haze the world fades into.
def _swap_chunk(bundle, index, new_chunk):
    """The bundle's bytes with one chunk replaced. Every edit here is
    length-preserving, so the header's offsets all stay valid."""
    c = bundle.chunks[index]
    data = bytearray(bundle.data)
    data[c.offset:c.offset + c.size] = new_chunk
    return bytes(data)


def _ops_fn(ops, pivot):
    """One world-space function for a geom_edit/mass_edit op list about `pivot`."""
    import math
    steps = []
    ax, ay, az = pivot
    for st in ops or []:
        k = st.get("op")
        if k == "translate":
            steps.append(("t", float(st.get("x", 0)), float(st.get("y", 0)),
                          float(st.get("z", 0))))
        elif k == "scale":
            sx = float(st.get("x", 1))
            steps.append(("s", sx, float(st.get("y", sx)), float(st.get("z", sx))))
        elif k == "rotate_y":
            rad = math.radians(float(st.get("deg", 0)))
            steps.append(("r", math.cos(rad), math.sin(rad), 0.0))

    def fn(x, y, z):
        for k, a, b, c in steps:
            if k == "t":
                x, y, z = x + a, y + b, z + c
            elif k == "s":
                x, y, z = ax + (x - ax) * a, ay + (y - ay) * b, az + (z - az) * c
            else:
                x, z = (ax + (x - ax) * a + (z - az) * b,
                        az - (x - ax) * b + (z - az) * a)
        return (x, y, z)
    return fn


def _follow_blob(s, body, lvl, blob, moves):
    """Collision inside MERGED shells follows moved models (collparts).

    `moves` = [(live zone uids, world fn)]. Pieces are claimed on the STOCK
    level and written into `blob` (the live bundle). -> (blob, info). The blob
    comes back untouched when nothing was claimed or the result will not fit;
    `info` says which."""
    from . import collparts, custcoll, rsc as RSC
    info = dict(pieces=0, faces=0, shells=0, skipped=[], nofit=False)
    try:
        stock = Level(s.vanilla, body["archive"], manifest=body.get("manifest"),
                      area=body.get("area"))
        vr = stock._load_rsc()
        if vr is None:
            return blob, info
        by = {}
        for n in stock.nodes:
            if n.kind == "zone":
                by.setdefault(n.name, []).append(n)
        smoves = []
        for uids, fn in moves:
            su = []
            for u in uids:
                c = by.get(lvl.by_uid(u).name) or []
                if len(c) == 1 and c[0].uid not in su:
                    su.append(c[0].uid)
            if su:
                smoves.append((su, fn, uids))
        if not smoves:
            return blob, info
        link = stock.links()
        skip = set()
        for n in stock.nodes:
            if n.kind == "bound" and (n.uid in link
                                      or any(abs(v) > 1e-6 for v in n.xyz())):
                bi = custcoll._res_index(n)
                if bi:
                    skip.add(bi - 1)
        live = RSC.Rsc(blob, lvl.rsc_name)
        # The collision goes where the drawn model REALLY is now: the map is
        # measured stock model -> live model (node position + vertices), so a
        # drag after a reshape, or two reshapes in a row, add up. The caller's
        # own `fn` is kept when it already agrees (exact), or when the model
        # cannot be measured.
        fitted = []
        for su, fn, uids in smoves:
            try:
                for u in uids:
                    ln = lvl.by_uid(u)
                    c = by.get(ln.name) or []
                    if len(c) != 1:
                        continue
                    P = collparts.world_verts(vr, c[0])
                    Q = collparts.world_verts(live, ln)
                    ft = collparts.fit_xform(P, Q) if P and Q else None
                    if ft:
                        g = ft[0]
                        if any(max(abs(a - b) for a, b in zip(g(*p), fn(*p))) > 0.005
                               for p in P[::max(1, len(P) // 64)]):
                            fn = g
                            info["measured"] = info.get("measured", 0) + 1
                    break
            except Exception as ex:
                info["measure_error"] = "%s: %s" % (type(ex).__name__, ex)
            fitted.append((su, fn))
        out, finfo = collparts.follow(stock, vr, live, fitted, skip=skip)
        info.update(finfo)
        if not out:
            return blob, info
        nb = blob
        for ci, b in out.items():
            nb = RSC.replace_chunk(nb, ci, b, lvl.rsc_name)
        if not s.disc.archive(body["archive"]).would_fit(lvl.rsc_name, nb)["fits"]:
            info["nofit"] = True
            info["shells"] = 0
            return blob, info
        return nb, info
    except Exception as ex:           # never let the follow break the move
        info["error"] = "%s: %s" % (type(ex).__name__, ex)
        info["shells"] = 0
        return blob, info


def _follow_note(info):
    if not info:
        return None
    if info.get("nofit"):
        return ("Its collision was NOT moved: the rebuilt collision does not "
                "fit this level's file. The old collision is still at the old place.")
    if info.get("error"):
        return "Its collision was NOT moved (%s)." % info["error"]
    if info.get("shells"):
        t = "Collision moved with it (%d piece(s), %d faces)." % (
            info["pieces"], info["faces"])
        if info.get("skipped"):
            t += " %d collision shell(s) could not be rebuilt and stayed put." % len(info["skipped"])
        return t
    if info.get("skipped"):
        return "Its collision could not be rebuilt safely and stayed where it was."
    return None


def _splice_and_write(handler, s, body, lvl, chunk, raw, out, extra=None, note=None,
                      follow=None, setinfo=None, twins=None):
    """Put a rewritten chunk back into its bundle and journal the write.

    Chunk sizes never change here -- every transform is an in-place float
    patch -- so the bundle's header offsets all stay valid and the file goes
    back into the same archive slot it came out of.
    """
    live = lvl._load_rsc()
    cur = s.disc.archive(body["archive"]).read(lvl.rsc_name)
    c = live.chunks[chunk]
    blob = bytearray(cur)
    blob[c.offset:c.offset + c.size] = out
    # Linked collision (model + collision are one thing): same-size patches to
    # other chunks of this bundle, written in the SAME undo step.
    for xi, xout in (extra or {}).items():
        xc = live.chunks[xi]
        if len(xout) != xc.size:
            raise ValueError("linked chunk %d changed size" % xi)
        blob[xc.offset:xc.offset + xc.size] = xout
    blob = bytes(blob)
    finfo = None
    if follow:
        blob, finfo = _follow_blob(s, body, lvl, blob, follow)
        fnote = _follow_note(finfo)
        if fnote:
            note = fnote if not note else note + " " + fnote
    if blob == cur:
        return handler._send(200, dict(ok=True, changed=False,
                                       note="no net change"))
    if body.get("dry"):
        return handler._send(200, dict(dry=True, bytes=len(blob),
                                       same_size=len(blob) == len(cur),
                                       collision_note=note, set=setinfo,
                                       collision=finfo))
    label = "move %s" % (body.get("label") or ("chunk %d" % chunk))
    used, slot = s.write(body["archive"], lvl.rsc_name, bytes(blob), label)
    s.note("%s :: geometry %s (%d/%d bytes)"
           % (body["archive"], body.get("label") or chunk, used, slot))
    res = dict(ok=True, changed=True, used=used, slot=slot)
    if extra:
        res["collision_moved"] = sorted(extra) if twins is None else twins
    if finfo:
        res["collision"] = finfo
    if setinfo:
        res["set"] = setinfo
    if note:
        res["collision_note"] = note
    return handler._send(200, res)


def _linked_twin_edit(s, lvl, vr, chunk, ops, about):
    """A drawn surface's linked collision, moved by the SAME ops and pivot.

    -> (extra {bound chunk: new bytes}, note). The twin is derived from STOCK
    like the model is (no drift). Skips, with a plain reason, when the model
    has no single twin or the twin was replaced by a custom shell.
    """
    import math
    from . import boundmove, custcoll
    z = custcoll.zone_for_chunk(lvl, chunk)
    if z is None:
        return {}, "This surface is not a named object, so it has no linked collision."
    partner = lvl.links().get(z.uid)
    if partner is None:
        return {}, lvl.collision_status(z.uid)["note"]
    bi = custcoll._res_index(lvl.by_uid(partner))
    if bi is None or not (0 < bi <= len(vr.chunks)) or vr.chunks[bi - 1].tag != "BND0":
        return {}, "Its collision shell is not in this level's bundle."
    cur = lvl._load_rsc()
    if cur is not None and bi - 1 < len(cur.chunks) \
            and len(cur.raw(bi - 1)) != len(vr.raw(bi - 1)):
        return {}, ("Its collision was replaced by a generated box; it was left "
                    "where it is. Re-import the model to refit it.")
    raw = vr.raw(bi - 1)
    out = raw
    try:
        for step in ops:
            k = step.get("op")
            if k == "translate":
                out = boundmove.translate(out, float(step.get("x", 0)),
                                          float(step.get("y", 0)), float(step.get("z", 0)))
            elif k == "scale":
                out = boundmove.scale(out, float(step.get("x", 1)),
                                      float(step.get("y", step.get("x", 1))),
                                      float(step.get("z", step.get("x", 1))), about=about)
            elif k == "rotate_y":
                out = boundmove.rotate_y(out, math.radians(float(step.get("deg", 0))),
                                         about=about)
    except boundmove.NotEditable as ex:
        return {}, "Its collision could not be moved safely: %s" % ex
    if out != raw and any(v is False for v in boundmove.audit(out).values()):
        return {}, "Its moved collision failed the self-check, so it was left alone."
    return {bi - 1: out}, "Collision moved with it."


def _reread(blob, mesh):
    """Positions as they now stand in `blob`, using the mesh's own offsets.

    Chaining transforms needs each step to see what the last one wrote.
    Re-decoding the whole packet between steps would work too, but the
    offsets are already known and cannot have moved -- nothing about an
    in-place float patch changes the packet's structure.
    """
    import struct
    return [struct.unpack_from("<3f", blob, o) for o in mesh["voff"]]


ENV_PRESETS = {
    "night": {
        "label": "Night",
        "note": "Deep blue ambient and a close, dark haze.",
        "lgt": {"Ambient": "0.090000\t0.100000\t0.160000",
                "AmbientScale": "1.000000"},
        "fog": {"ColorStart": "0.040000\t0.055000\t0.110000",
                "ColorEnd": "0.040000\t0.055000\t0.110000",
                "Enable": "1"},
    },
    "dusk": {
        "label": "Golden hour",
        "note": "Warm low sun, orange haze.",
        "lgt": {"Ambient": "0.480000\t0.360000\t0.240000",
                "AmbientScale": "1.000000"},
        "fog": {"ColorStart": "0.640000\t0.400000\t0.220000",
                "ColorEnd": "0.640000\t0.400000\t0.220000",
                "Enable": "1"},
    },
    "overcast": {
        "label": "Overcast",
        "note": "Flat grey light, pale haze.",
        "lgt": {"Ambient": "0.400000\t0.400000\t0.420000",
                "AmbientScale": "1.000000"},
        "fog": {"ColorStart": "0.660000\t0.670000\t0.700000",
                "ColorEnd": "0.660000\t0.670000\t0.700000",
                "Enable": "1"},
    },
    "clear": {
        "label": "Clear the haze",
        "note": "Turns fog off so you can see the whole map.",
        "fog": {"Enable": "0"},
    },
    "bright": {
        "label": "Full bright",
        "note": "Ambient at 1.0 -- shows the raw art with no baked tint.",
        "lgt": {"Ambient": "1.000000\t1.000000\t1.000000",
                "AmbientScale": "1.000000"},
    },
}

ENV_GROUPS = [
    ("Lighting", {"lgt", "glows", "flares", "shadows"}),
    ("Atmosphere", {"fog", "sky", "fade", "film", "dst"}),
    ("Weather", {"rain", "snow", "wind"}),
    ("Water", {"water", "gls"}),
    ("Ground cover", {"grass", "grasp", "til"}),
    ("Detail distance", {"lod", "accu"}),
    ("Particles", {"ptx", "swptx", "swptxset", "swptxemitter", "fx", "fxcr",
                   "glassfx"}),
    ("Physics", {"pmtl", "phys", "ecs", "ebox"}),
    ("Cameras & paths", {"cam", "paths", "nav", "aisp", "sneak"}),
    ("Vehicles", {"veh", "trn", "wheels", "znv"}),
    ("Audio", {"audbox", "vsnd", "swaudio", "animsnd"}),
    ("Rules & rewards", {"inv", "pow", "duel", "ntr", "flags", "sub"}),
]

# Extensions we know are human-readable text.
TEXT_EXTS = {
    "lvl", "spwn", "enm", "enemies", "bnds", "znes", "bxes", "lnes", "guns",
    "cr", "plyr", "tbl", "csv", "txt", "swui", "hud", "duel", "flags", "gait",
    "xls", "cfg", "ini", "dam", "hot", "ars", "em", "cm", "reigns",
}
# Not "rnt": it is a binary name table ("RNT0", offsets, then names -- see
# rsc.parse_rnt), and listed here it opened in the text editor as garbage.


class _Picks(list):
    """A part list that also remembers how many body LODs exist."""
    body_count = 1


def character_parts(bundle, dave, stem, lod=0):
    """Which MOD0 chunks make up this character, at the requested detail.

    A character is NOT four LODs. `mrd_lods.rsc` holds `mrd_lod_01`,
    `mrd_lod_02`, `mrd_face` and `hat_mrd_lod_01` -- two body LODs plus a
    face and a hat, which the game draws together. Picking one chunk by
    ordinal gives you a body with no face, or at the wrong index a floating
    head. The `.rnt` manifest names them, so use it.

    Returns [(chunk_index, name)]: one body LOD plus every accessory.
    """
    from .rsc import parse_rnt
    names = {}
    try:
        names = parse_rnt(dave.read(stem + "_lods.rnt"))
    except Exception:
        pass
    mods = [c.index for c in bundle.chunks if c.tag == "MOD0"]
    if not mods:
        return []
    if not names:
        # No manifest: fall back to ordinal, which is what we did before and
        # is right for the single-part props.
        pick = max(0, min(lod, len(mods) - 1))
        return [(mods[pick], None)]

    bodies, extras = [], []
    for i in mods:
        nm = names.get(i) or ""
        low = nm.lower()
        if "_lod_" in low and not low.startswith("hat"):
            bodies.append((i, nm))
        else:
            extras.append((i, nm or None))     # face, hat, anything else
    bodies.sort(key=lambda t: t[1])
    if not bodies:
        pick = max(0, min(lod, len(mods) - 1))
        return [(mods[pick], names.get(mods[pick]))]
    chosen = bodies[max(0, min(lod, len(bodies) - 1))]
    picks = _Picks([chosen] + extras)
    # How many body LODs EXIST, not how many were chosen -- the caller shows
    # this in the detail dropdown.
    picks.body_count = len(bodies)
    return picks


def merge_models(bundle, picks, bone_world=None, head=None, head_index=None):
    """Decode several MOD0 chunks into one mesh, in bind pose.

    Parts are bind-posed HERE, per part, because they are not all posed the
    same way. The body is skinned: every vertex names a bone and is offset by
    it. Faces and hats carry `bone_count == 0` and no per-vertex bone at all
    -- they are rigid meshes authored in HEAD-LOCAL space, so they need the
    head bone's world position instead. Left alone they pile up at the model
    origin, which is why the hat used to lie at the character's feet.

    Two extra keys come back for anything that wants to ANIMATE the result
    rather than just look at it:

      influences  per vertex, [(bone_index, weight), ...] summing to 1
      groups      [(texture_slot, first_triangle, triangle_count)]

    `head_index` is the .skel index of the bone a rigid part hangs from. It
    only matters to `influences`: a face and a hat have no per-vertex bone,
    so the only honest thing to say about them is "this whole part rides the
    head bone" -- which is rigid attachment, not skinning, and is reported as
    such by `parts[].attach`.
    """
    from . import vif as vifmod
    verts: list[Any] = []
    tris: list[Any] = []
    uvs: list[Any] = []
    bones: list[Any] = []
    parts, influences, groups = [], [], []
    skinned = False
    # The exact per-surface binding (see Rsc.tex_slot_map): a container's
    # sub-mesh table names its own TEX0 by ONE-BASED chunk reference. Read
    # once -- 37 of the 310 character bundles in SWAT.DAT carry more than one
    # texture, and `red_lod_01` puts 1,872 of its 2,503 triangles on the
    # SECOND one, so "texture 0 for the whole character" is visibly wrong for
    # those.
    slotmap = bundle.tex_slot_map()
    fallback = bundle.model_texture_map()
    for index, name in picks:
        try:
            m = bundle.model(index)
        except Exception:
            continue
        if not m or not m["tris"]:
            continue

        # "skinned" only means it came through the skinned decoder. A face or
        # a hat goes through it too and comes back with EVERY bone None, so
        # test what actually matters: does any vertex name a bone?
        rigged = any(b is not None for b in (m.get("bones") or ()))
        if rigged and bone_world:
            posed = vifmod.to_bind_pose(m, bone_world)
        elif head is not None:
            posed = [(v[0] + head[0], v[1] + head[1], v[2] + head[2])
                     for v in m["verts"]]
        else:
            posed = m["verts"]

        base = len(verts)
        tri_base = len(tris)
        verts.extend(posed)
        tris.extend((base + a, base + b, base + c) for a, b, c in m["tris"])
        uvs.extend(m["uvs"][:len(posed)])
        while len(uvs) < len(verts):
            uvs.append((0.0, 0.0))
        if rigged:
            skinned = True
            bones.extend(m["bones"])
        else:
            bones.extend([None] * len(posed))

        # Per-vertex influences. `weights` already carries the primary bone
        # first (see vif._extract_skinned_at), so it is the whole answer when
        # it is populated; the per-vertex bone is the fallback for a batch
        # that shipped no secondary-influence block.
        wl = m.get("weights") or []
        bl = m.get("bones") or []
        for i in range(len(posed)):
            e = wl[i] if i < len(wl) else None
            if not e:
                b = bl[i] if i < len(bl) else None
                e = [(b, 1.0)] if b is not None \
                    else ([(head_index, 1.0)] if head_index is not None else [])
            influences.append(list(e))

        pr = m.get("part_ranges")
        if pr:
            for res, start, count, _colour in pr:
                if count > 0:
                    groups.append((slotmap.get(res), tri_base + start, count))
        elif m["tris"]:
            groups.append((fallback.get(index), tri_base, len(m["tris"])))

        parts.append(dict(chunk=index, name=name, verts=len(posed),
                          tris=len(m["tris"]),
                          attach="skin" if rigged else "head"))
    if not verts:
        return None
    return dict(verts=verts, tris=tris, uvs=uvs, bones=bones,
                skinned=skinned, parts=parts, posed=True,
                influences=influences, groups=groups,
                bone_count=max((bundle.bone_count(i) for i, _ in picks),
                               default=0))


def head_bone(bones, world):
    """Where a hat or a face hangs. The `head` bone, else the highest one."""
    if not bones:
        return None
    for b in bones:
        if b.name.lower() == "head":
            return world[b.name]
    return max((world[b.name] for b in bones), key=lambda p: p[1])


def head_bone_index(bones, world):
    """WHICH bone a hat or a face hangs from, in .skel file order.

    Deliberately the same rule as head_bone() -- the named `head` bone, else
    the highest one -- returning the index instead of the position, because a
    viewer that animates the model needs the bone, not the point. Keeping the
    two rules identical is what makes the animated hat land where the static
    preview already puts it.
    """
    if not bones:
        return None
    for i, b in enumerate(bones):
        if b.name.lower() == "head":
            return i
    return max(range(len(bones)), key=lambda i: world[bones[i].name][1])


class JobCancelled(Exception):
    """A long job stopped because /api/job_cancel asked it to."""


class Studio:
    """Shared state: the two discs plus caches."""

    # Set through `s._stream_cache = ...` from the Handler and read back
    # with getattr. A bare annotation creates no attribute, so the getattr
    # default still fires on first use.
    _stream_cache: Any

    def __init__(self, vanilla, modded, pcsx2=None, emulog=None):
        self.vanilla_path = vanilla
        self.modded_path = modded
        self.pcsx2 = pcsx2
        # Fall back to the configured log rather than leaving it None. With no
        # log path, boot_test reads nothing, finds neither "is executing" nor
        # "Elf entry", and reports booted=False -- a FAIL for a disc that
        # booted perfectly. A test that fails when the harness is
        # misconfigured, and says the same thing as a bricked disc, is worse
        # than no test: it teaches you to ignore it. Caught by running one
        # against a disc byte-identical to stock and getting a FAIL.
        self.emulog = emulog or cfg.emulog()
        self.lock = threading.RLock()
        self.game = None
        self.vanilla = None
        self._levels = {}
        self._assets = None
        self.log = []
        self.journal = None
        self._pending = None
        # Warm-up progress, read by the lock-free /api/warmup route so the
        # boot overlay can draw a real bar while the caches fill. "idle"
        # means nobody started it (tests, CLI); the overlay treats that the
        # same as done.
        self.warm: dict[str, Any] = {"state": "idle", "stage": "", "done": 0, "total": 0,
                     "started": 0.0, "seconds": 0.0}
        # The long job holding the lock right now, if it registered itself:
        # {"label", "started", "cancel"}. Read by the lock-free /api/job so
        # the boot screen and status bar can NAME what the server is doing
        # instead of "reading the disc...", and set by /api/job_cancel, which
        # the job checks between archives (DESIGN-REVIEW B1).
        self.job: dict[str, Any] | None = None
        self.open()

    def start_warmup(self):
        """Pay every expensive first-touch cost ONCE, up front, on a thread.

        The app used to boot into an empty shell and pay these costs lazily,
        on whichever click happened to arrive first -- and because every
        disc-touching route serialises on one lock, that click also stalled
        the 3-second polls behind it, which read as "the app does not load".
        The work was never the problem; paying it silently, mid-interaction,
        was. Now it happens immediately at launch, with progress the front
        end can SHOW, and navigation afterwards runs on hot caches.

        Steps take and release the studio lock one at a time, so anything a
        user does during warm-up interleaves instead of waiting for the whole
        thing. Measured order-of-magnitude costs, cold: index ~20s, level
        catalog ~7s, archive TOC walk is covered by the index (build_index
        opens every archive, which is what fills the cache the archives route
        reads).
        """
        if self.warm["state"] not in ("idle",):
            return
        self.warm.update(state="running", started=time.time())

        def _step(stage, done, total):
            self.warm.update(stage=stage, done=done, total=total,
                             seconds=round(time.time() - self.warm["started"], 1))

        def _run():
            try:
                _step("opening discs", 0, 3)
                with self.lock:
                    self.ensure_open()
                _step("reading every archive (file index)", 1, 3)
                with self.lock:
                    disc = self.disc
                disc.build_index(progress=lambda n, total, arc: _step(
                    "reading archives  %d / %d  (%s)" % (n, total, arc),
                    1, 4))
                _step("level catalogue", 2, 3)
                with self.lock:
                    from . import modes
                    modes.catalog(self.disc)
                _step("ready", 3, 3)
                # The vanilla-disc index is NOT built here any more: it is a
                # second full 66-archive walk over a second 4 GB ISO, it
                # doubled the warm-up, and only the diff panel reads it --
                # build_index() self-memoises on the first diff call.
                self.warm.update(state="done")
            except Exception as ex:                        # noqa: BLE001
                # A failed warm-up must not take the app down -- everything
                # it does is an optimisation, and the lazy paths all still
                # exist. Report and get out of the way.
                self.warm.update(state="error", stage=str(ex))

        threading.Thread(target=_run, name="warmup", daemon=True).start()

    # ------------------------------------------------------------------ discs

    def _overflow_batch(self, pending):
        """Game.patch_everywhere's copies that had no room: queued for their
        archive's rebuild (on the next Play). [(archive, used, "rebuild")]."""
        done = []
        for arc, filename, data in pending:
            self.pending.queue(arc, filename, data)
            done.append((arc, len(data), "rebuild"))
        self._mirror([(arc, filename, data) for arc, filename, data in pending])
        return done

    @property
    def pending(self):
        """Edits with no room in their slot, waiting for their archive to be
        rebuilt: all at once, on the next Play ("Rebuild and play")."""
        if self._pending is None:
            from .pendingrebuild import PendingRebuilds, data_editor_home
            self._pending = PendingRebuilds(os.path.join(data_editor_home(self.modded_path), "pending"))
        return self._pending

    def rebuild_pending(self, progress=None):
        """Rebuild every archive with queued edits, each once with all of them.
        [archive] done."""
        done = []
        with self.lock:
            arcs = self.pending.archives()
            for n, arc in enumerate(arcs):
                if progress:
                    progress(n, len(arcs), arc)
                self.rebuild_with(arc)
                done.append(arc)
                self.note("%s rebuilt with its queued edits" % arc)
            if done:
                if progress:
                    progress(len(arcs), len(arcs), "putting the disc back in order")
                _data_editor(self)._order_quietly()
        return done

    def open(self):
        """Open both discs. Tolerates the working ISO being busy.

        PCSX2 holds an exclusive handle while it is running, so starting the
        Studio during a play session used to abort with errno 13 before the
        web server ever came up. The Studio is useful without write access --
        every read comes from the pristine disc -- so it now starts anyway and
        picks the working ISO up on the first request after the emulator
        closes.
        """
        with self.lock:
            self.close()
            try:
                self.game = Game(self.modded_path, writable=True)
                self.game.overflow_hook = (self._overflow_batch
                                           if self.rebuild_on_overflow else None)
                self.game.pending = self.pending
            except PermissionError:
                self.game = None
                self.note(self._locked_reason())
            self.vanilla = Game(self.vanilla_path, writable=False)
            self._levels = {}
            old = self.journal
            keep = old.entries if old else None
            session = os.path.join(cfg.MODS, "_session.rdrmod.json")
            self.journal = Journal(self.vanilla, session)
            if keep:
                self.journal.entries = keep
                self.journal._seq = len(keep)
                # and what else only memory holds: a mods-folder file's bytes before its first edit (undo's only
                # copy of it -- lost here, Ctrl+Z on a box delete said "no earlier copy"), the redo steps, and the
                # group counter (restarted at g1, a new action shared a group with an old one and undid with it)
                # -- 2026-11-18
                self.journal.bases = dict(getattr(old, "bases", {}) or {})
                self.journal.redo_stack = list(getattr(old, "redo_stack", []) or [])
                self.journal._group_seq = getattr(old, "_group_seq", 0)
            else:
                # First open of this process: pick up the previous session's
                # autosave so undo history survives a Studio restart. Without
                # this, edits made before a restart could only be reverted
                # per-file, not undone.
                try:
                    n = self.journal.load_session(session)
                    if n:
                        self.note("restored %d journal entr%s from the last session"
                                  % (n, "y" if n == 1 else "ies"))
                except Exception:
                    pass

    def _clear_session(self):
        """Forget the edit history (used after Restore Vanilla)."""
        with self.lock:
            self.journal.entries = []
            self.journal.redo_stack = []
            self.journal._seq = 0
            try:
                path = os.path.join(cfg.MODS, "_session.rdrmod.json")
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass

    # ------------------------------------------------------------- writing

    def locked_problem(self):
        """Why the working ISO cannot be written: (message, fix), both in the
        user's terms.

        SPLIT IN TWO, 2026-09-19, and that is the whole point of it. The single
        string this used to return reached the browser through
        `routes2._disc_busy`, which threw it away and substituted "PCSX2 has
        the working disc / Close PCSX2 and try again" -- the exact advice the
        three-way split below exists to avoid giving -- keeping the real reason
        only as `problem.detail`, which `web/transfer.js:renderProblem` puts
        behind a collapsed "Technical detail" disclosure. So the careful cause
        was computed, and then hidden under a wrong one. A refusal a user
        cannot act on is not better than a 500; it is a 500 that looks handled.

        Both halves are public now (no leading underscore) because routes2 is
        entitled to the same answer this class gives its own log.

        Two of the three causes look identical from `PermissionError` and only
        one mentions an emulator. Measured: `Copy-Item RDR_VANILLA.iso ...`
        copies the vanilla disc's ReadOnly ATTRIBUTE along with the bytes, so a
        freshly made scratch disc is read-only, and the old wording told the
        user to close a PCSX2 that was not running -- advice they cannot act
        on, for a problem one checkbox fixes. Restart Manager reports no holder
        in that case, because nothing is holding it.
        """
        # FOUR causes, not three. A MISSING disc also fails os.access(W_OK),
        # so until 2026-09-19 a working ISO that was not there reported "marked
        # read-only" and told the user to clear a tick in the Properties of a
        # file that does not exist. Same disease as the PCSX2 wording this
        # method was split up over: one sentence covering causes it cannot
        # tell apart. Found by calling locked_problem() on a path with no file.
        if not os.path.exists(self.modded_path):
            return ("The working ISO is not where the Studio expects it (%s)."
                    % self.modded_path,
                    "Restore that file, or start the Studio pointed at the "
                    "copy you mean. Nothing was written.")
        try:
            ro = not os.access(self.modded_path, os.W_OK)
        except Exception:
            ro = False
        if ro:
            return ("The working ISO is marked read-only, so nothing can be "
                    "written to it.",
                    "Clear the read-only tick in its Properties (a copy of the "
                    "vanilla disc inherits it). Nothing was written.")
        # THREE causes now, not two (REQ-078). A second writer is the only one
        # that leaves evidence naming itself, so it is asked about before the
        # emulator -- the same reasoning the read-only branch above was added
        # for. Telling someone to close a PCSX2 that is not running is advice
        # they cannot act on, and here the thing they CAN act on is named.
        lock = self.modded_path + iso9660.Iso9660.LOCK_SUFFIX
        try:
            with open(lock, encoding="utf-8") as fh:
                held = fh.read().strip() or "(empty)"
        except OSError:
            held = None
        if held:
            return ("Another process has the working ISO open for writing "
                    "(%s), and two writers are not serialised by anything, so "
                    "this one is refused." % held,
                    "Wait for it, or if nothing is running delete %s. Nothing "
                    "was written." % lock)
        return ("PCSX2 has the working ISO.",
                "Close it and try again. Nothing was written.")

    def _locked_reason(self):
        """The same answer as one sentence, for `note()` and for anything that
        wants a plain string. Always contains "working ISO" so `routes2._busy`
        still files it as a refusal rather than a fault.
        """
        msg, fix = self.locked_problem()
        return (msg + " " + fix).strip()

    # ------------------------------------------------- levels only the mods folder has
    #
    # A new level (lvl_mantomb2: the add-on's export, in mods/lvls_m2/mantomb2.dat/) is shown from that folder, or
    # from a PS2 copy of it in the cache when the folder holds only the Xbox files. Its edits used to go to the
    # working disc like every other -- which does not have it ("not on disc"), so nothing in such a level could be
    # deleted, moved or added (2026-11-18). They go into the folder now, which is what a Build puts in the game.

    def node_template(self, kind, token=""):
        """(Level, node) of a level of the working disc with a node of this kind whose name holds token -- a
        template for node_add. Searched once per (kind, token), the levels in the disc's order."""
        cache = self.__dict__.setdefault("_node_templates", {})
        key = (kind, token)
        if key in cache:
            return cache[key]
        found = None
        try:
            src = self.game or self.vanilla
            arcs = sorted(a for a in src.archives() if a.upper().startswith(("LVLS_SNG/", "LVLS_M2/", "LVLS_M4/")))
        except Exception:                                   # noqa: BLE001
            arcs = []
        for a in arcs:
            try:
                lv = self.level(a)
            except Exception:                               # noqa: BLE001
                continue
            for n in lv.nodes:
                if n.kind == kind and not getattr(n, "readonly", False) and (not token or token in n.name.lower()):
                    found = (lv, n)
                    break
            if found:
                break
        cache[key] = found
        return found

    def _mods_folder_of(self, archive):
        """The mods folder of a level the working disc has not, or None."""
        try:
            src = self.game or self.vanilla
            if archive.upper() in {a.upper() for a in src.archives()}:
                return None
        except Exception:                                   # noqa: BLE001
            return None
        from . import collcreate as _cc
        return _cc.mods_folder(_data_editor_for(self), archive)

    def _mods_only_view(self, archive, filename):
        """A file of a mods folder level as the editor shows it (the cache's PS2 copy for an Xbox-only one)."""
        shown = self._mods_level_folders().get(archive.upper())
        name = filename.replace("\\", "/").rsplit("/", 1)[-1]
        if shown:
            for f in os.listdir(shown):
                if f.lower() == name.lower():
                    with open(os.path.join(shown, f), "rb") as fh:
                        return fh.read()
        return None

    def _mods_only_write(self, folder, filename, data):
        """A file into a mods folder level. A zone file of a level whose folder holds the Xbox ones goes into the
        .xznes (the editor's .znes is it with each node's resourceIndex and resourceIndexXbox swapped); its models
        (.rsc: the folder holds the Xbox bundle) are not edited here."""
        import re as _re
        name = filename.replace("\\", "/").rsplit("/", 1)[-1]
        have = {f.lower(): f for f in os.listdir(folder)}
        low = name.lower()
        target, out = name, data
        if low in have:
            target = have[low]
        elif low.endswith(".znes") and low[:-5] + ".xznes" in have:
            target = have[low[:-5] + ".xznes"]
            text = data.decode("latin-1")
            text = _re.sub(r"resourceIndex(\s+)(-?\d+)(\s+)resourceIndexXbox(\s+)(-?\d+)",
                           lambda m: "resourceIndex%s%s%sresourceIndexXbox%s%s" % (
                               m.group(1), m.group(5), m.group(3), m.group(4), m.group(2)), text)
            out = text.encode("latin-1")
        elif low.endswith((".rsc", ".rnt")) and _re.sub(r"\.(rsc|rnt)$", r".x\1", low) in have:
            raise ValueError("%s: this level's models are its Xbox files (.xrsc) -- reshape them in Blender and "
                             "export again (collision: the Collision creator)" % name)
        p = os.path.join(folder, target)
        if not out:
            # nothing: a file that was not there before the edit made it (undone)
            if os.path.exists(p):
                os.remove(p)
            return
        tmp = p + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(out)
        os.replace(tmp, p)

    # "Edit both Multiplayer level variants": a Showdown map is two levels, lvl_<name>2 (LVLS_M2) and lvl_<name>4
    # (LVLS_M4); with this on, an edit of a file of one that the other has exactly the same (its level name aside)
    # is made in the other too, in the same undo step.
    sync_mp = False

    def _mp_partner(self, archive, filename):
        """(partner archive, partner file name, renamer) of a Showdown level file, or None."""
        import re as _re
        m = _re.fullmatch(r"(LVLS_M)([24])/(.+)([24])\.DAT", archive.upper())
        if not m or m.group(2) != m.group(4):
            return None
        a, b = m.group(2), "4" if m.group(2) == "2" else "2"
        stem = m.group(3).lower()
        other = "%s%s/%s%s.DAT" % (m.group(1), b, m.group(3), b)
        mine, theirs = ("lvl_%s%s" % (stem, a)).encode(), ("lvl_%s%s" % (stem, b)).encode()
        name = filename.replace("lvl_%s%s" % (stem, a), "lvl_%s%s" % (stem, b))

        def rename(data):
            return data.replace(mine, theirs)
        return other, name, rename

    def _read_level_file(self, archive, filename):
        """A level file's bytes as the editor has them now (the mods folder's, or the working disc's), or None."""
        if self._mods_folder_of(archive):
            return self._mods_only_view(archive, filename)
        try:
            return self.game.archive(archive).read(filename)
        except Exception:                                   # noqa: BLE001
            return None

    def write(self, archive, filename, data, label="", group=None, grow=False, _mirror_mp=True):
        """The one place bytes reach the ISO. Everything else routes here."""
        if self.sync_mp and _mirror_mp:
            pm = self._mp_partner(archive, filename)
            if pm is not None:
                other, oname, rename = pm
                mine = self._read_level_file(archive, filename)
                theirs = self._read_level_file(other, oname)
                if mine is not None and theirs is not None and rename(mine) == theirs:
                    group = group or self.journal.new_group()
                    self.write(other, oname, rename(data), label, group, grow, _mirror_mp=False)
                    self._drop_level(other)
        folder = self._mods_folder_of(archive)
        if folder:
            with self.lock:
                key = (archive, filename)
                if not hasattr(self.journal, "bases"):
                    self.journal.bases = {}
                if key not in self.journal.bases and not any(e.key == key for e in self.journal.entries):
                    before = self._mods_only_view(archive, filename)
                    # a file the edit makes (a model import's new zone file): b"" -- undone, it goes again
                    self.journal.bases[key] = before if before is not None else b""
                self._mods_only_write(folder, filename, data)
                self.journal.record(archive, filename, data, label, group)
                self._drop_level(archive)
                return len(data), len(data)
        with self.lock:
            # An edit says "I want the disc" outright, so it overrides the
            # handover grace period -- but not the emulator actually holding
            # the file, which is a real refusal.
            self.ensure_open(force=True)
            if self.game is None:
                raise api.DiscLocked(*self.locked_problem())
            try:
                used, slot = self.game.archive(archive).patch(filename, data,
                                                              grow=grow)
            except SlotOverflow as ex:
                # No room in its slot (nor, with grow, in a free gap): queued
                # for the archive's rebuild (laid out as the game's own, every
                # other edit kept), which happens once, with every queued file,
                # on the next Play. The Studio reads the new bytes back
                # meanwhile. Still one journalled edit: Ctrl+Z takes it out of
                # the queue again.
                if not self.rebuild_on_overflow:
                    raise
                self.pending.queue(archive, filename, data)
                used, slot = ex.need, ex.have   # numbers callers report; the archive grows on rebuild
            self.journal.record(archive, filename, data, label, group)
            self._drop_level(archive)
            self._mirror([(archive, filename, data)])
            if filename.lower().endswith(("_lods.rsc", ".skel")):
                # the Animation tab's lists (rigs, skeletons) describe these
                for attr in ("_rig_index", "_skel_index", "_skel_names", "_skel_parents"):
                    if getattr(self, attr, None):
                        setattr(self, attr, {})
            return used, slot

    # Edits that do not fit their slot rebuild the archive to make room (set
    # False to get the old refusal back).
    rebuild_on_overflow = True

    def rebuild_with(self, archive, replace=None, add=None):
        """The archive rebuilt with these files (replace / add), and every edit
        queued for it: (stored, slot) of the first one in the new archive.
        Holds the lock; the disc handles are closed and reopened around the
        write."""
        from . import dataeditor
        with self.lock:
            de = _data_editor(self)
            q_rep, q_add = self.pending.take(archive)
            all_rep = dict(q_rep, **(replace or {}))
            all_add = dict(q_add, **(add or {}))
            try:
                data = de.rebuild_archive(archive, all_rep, all_add)
            except Exception:
                for n, d in q_rep.items():         # nothing lost: back in the queue
                    self.pending.queue(archive, n, d)
                for n, d in q_add.items():
                    self.pending.queue(archive, n, d, add=True)
                raise
            replace, add = all_rep, all_add
            self._mirror([(archive, n, d) for n, d in list(all_rep.items()) + list(all_add.items())])
            self._levels = {}
            self._assets = None
            name = next(iter((replace or add or {})), None)
            return dataeditor.DataEditor.slot_of(data, name) if name else (len(data), len(data))

    def writer_for(self, archive, label="", group=None, grow=False):
        """A writer callable Level.apply() can use, so its writes are logged.

        Edits spanning several files share a group, so one user action is one
        undo step.
        """
        group = group or self.journal.new_group()

        def w(filename, data):
            return self.write(archive, filename, data, label, group, grow=grow)
        return w

    def _undo_read(self, archive, filename):
        """A mods-folder level file as it is now, for undo's check (None: not checked -- a disc file, or a level
        shown from a cache copy)."""
        folder = self._mods_folder_of(archive)
        if not folder or self._mods_level_folders().get(archive.upper()) != folder:
            return None
        name = filename.replace("\\", "/").rsplit("/", 1)[-1].lower()
        for f in os.listdir(folder):
            if f.lower() == name:
                with open(os.path.join(folder, f), "rb") as fh:
                    return fh.read()
        return None

    def undo(self):
        with self.lock:
            r = self.journal.undo(self._raw_write, self._undo_read)
            if r:
                self._levels = {}
                self.note("undo: %s (%d file(s))" % (r["label"] or "edit", r["count"]))
            return r

    def redo(self):
        with self.lock:
            r = self.journal.redo(self._raw_write)
            if r:
                self._levels = {}
                self.note("redo: %s (%d file(s))" % (r["label"] or "edit", r["count"]))
            return r

    def _raw_write(self, archive, filename, data):
        """Undo/redo path -- must NOT append to the journal."""
        folder = self._mods_folder_of(archive)
        if folder:
            self._mods_only_write(folder, filename, data)
            self._drop_level(archive)
            return
        try:
            self.game.archive(archive).patch(filename, data)
        except SlotOverflow:
            if not self.rebuild_on_overflow:
                raise
            self.pending.queue(archive, filename, data)
        self._drop_level(archive)
        self._mirror([(archive, filename, data)])

    def _mirror(self, items):
        """Studio edits shown in the Data Editor's .dat folders (DataEditor.mirror)."""
        if not getattr(self, "mirror_to_data_editor", True):
            return
        try:
            _data_editor(self).mirror(items)
        except Exception:                                  # noqa: BLE001
            pass

    def close(self):
        with self.lock:
            for g in (self.game, self.vanilla):
                try:
                    if g:
                        g.close()
                except Exception:
                    pass
            self.game = self.vanilla = None
            self._levels = {}

    def note(self, msg):
        stamp = time.strftime("%H:%M:%S")
        self.log.append("[%s] %s" % (stamp, msg))
        del self.log[:-400]

    def free_for_emulator(self):
        """Can something else open the working ISO exclusively right now?

        Asked by trying it. Nothing else is reliable: a process list tells you
        PCSX2 is running but not whether it has this particular file, and the
        Studio's own handle is invisible to it either way.
        """
        try:
            with open(self.modded_path, "r+b"):
                return True
        except OSError:
            return False

    def hand_over(self):
        """Give the disc to the emulator and then STAY OUT OF THE WAY.

        This is the fix for the oldest recurring bug in the project: "I press
        Play and PCSX2 says permission denied."

        The original behaviour released the handle and let `ensure_open` take
        it back after a 3-second throttle. PCSX2 needs far longer than that to
        boot before it opens the CDVD, so the Studio reliably won the race --
        and the 4-second disc-state poll caused it on its own, with no user
        action at all.

        The first attempt at a fix was ALSO wrong, which is worth recording:
        it reclaimed as soon as the file *looked* free. Immediately after a
        handover the file IS free, because the emulator has not reached it
        yet, so that reclaimed within two seconds and changed nothing.
        "Free" cannot distinguish "not taken yet" from "finished with".

        Hence two pieces of state: when we handed over, and whether we ever
        actually saw the emulator holding it.
        """
        with self.lock:
            self._handed_over: "float | None" = time.time()
            self._emulator_took_it = False
            # Release ONLY the working ISO. The emulator has no interest in
            # the pristine one, and the `disc` property promises browsing and
            # decoding keep working during a play session -- a promise that
            # closing both handles quietly broke. It went unnoticed because
            # the old code reclaimed within three seconds, so the window in
            # which reads failed was too short to ever hit.
            try:
                if self.game:
                    self.game.close()
            except Exception:
                pass
            self.game = None
            if self.vanilla is None:
                self.vanilla = Game(self.vanilla_path, writable=False)
            # Every cached Level holds a Dave bound to the handle just closed.
            # Reading a stale one raises "short read ... got 0" rather than
            # anything that names the real cause. `open()` already clears this
            # cache, which is why the bug stayed hidden while a handover only
            # lasted three seconds -- the reclaim cleaned up before anything
            # could read.
            self._levels = {}

    def ensure_open(self, force=False):
        """Reopen the discs if they were released (boot test / play mode).

        `force` is for a deliberate user action -- an edit, or Reconnect. It
        skips the handover grace period, but still cannot take a file the
        emulator is actually holding.
        """
        with self.lock:
            if self.game is not None:
                return
            now = time.time()
            if not force and now - getattr(self, "_last_open_try", 0) < 3.0:
                return
            self._last_open_try = now

            ho = getattr(self, "_handed_over", None)
            if ho:
                if not self.free_for_emulator():
                    # The emulator has it. Remember that, so the moment it
                    # lets go we know the session is over and can reclaim.
                    self._emulator_took_it = True
                    return
                if not (force or getattr(self, "_emulator_took_it", False)
                        or now - ho > HANDOVER_GRACE):
                    # Free only because the emulator has not opened it yet.
                    # This is the window the old code kept losing.
                    return
                self._handed_over = None
            self.open()

    def take_back(self):
        """Reclaim the disc deliberately. True if we now hold it."""
        self.ensure_open(force=True)
        return self.game is not None

    @property
    def disc(self):
        """The disc to READ from.

        Normally the working ISO. While PCSX2 holds it, the pristine one --
        so browsing, decoding and inspecting keep working during a play
        session instead of the whole app erroring out. Writes never come
        through here; they go via `write()`, which refuses when locked.
        """
        self.ensure_open()
        return self.game or self.vanilla

    def skeleton_index(self, archive="SWAT.DAT"):
        """`.skel` name -> bone count, for one archive.

        Which skeletons fit a clip is asked on every click in the Animation
        tab, and answering it means parsing every `.skel` in the archive --
        236 files in SWAT.DAT, and the route measured 3.1 s per click cold.
        The files do not change under us except through an edit, and an edit
        reopens the disc, so caching on (disc path, archive) is safe.
        """
        from . import skeleton as SK
        key = self._arc_key(archive)
        cache: "dict[Any, Any] | None" = getattr(self, "_skel_index", None)
        if cache is None:
            cache = self._skel_index = {}
        if getattr(self, "_skel_names", None) is None:
            self._skel_names: dict[Any, Any] = {}
        if getattr(self, "_skel_parents", None) is None:
            self._skel_parents: dict[Any, Any] = {}
        if key in cache:
            return cache[key]
        out = {}
        names = {}
        parents = {}
        for e in self.disc.toc(archive):
            if not e.name.lower().endswith(".skel"):
                continue
            try:
                _v, _d, bones, _r = SK.parse(self.disc.read(archive, e.name))
            except Exception:
                continue
            # Names, not just the count. Widening "which skeleton fits this
            # clip" from equality to "long enough" is only safe if bone i is
            # the SAME JOINT in both rigs, and the only evidence for that is
            # the names lining up. Counting alone would cheerfully offer a
            # 73-bone horse for a 52-bone walk.
            out[e.name] = len(bones)
            names[e.name] = SK.prefix_names(bones)
            # Parents too, because a NAME is only a proxy for "same joint" and
            # it is a lossy one: srd.skel calls the canonical `coattail`
            # `tail_root`. Where the name differs, the parent still says
            # whether the two rigs branch at the same place. See the near-fit
            # test in the `anim` route.
            parents[e.name] = tuple(b.parent for b in bones)
        cache[key] = out
        self._skel_names.setdefault(key, names)
        self._skel_parents.setdefault(key, parents)
        return out

    def _arc_key(self, archive):
        """A cache key for what an archive holds: the disc, the archive, and its table's size and file count -- so a
        new character (Add Character, a Data Editor build, a rebuild) shows in the lists without a restart. These
        caches used to be keyed by disc and archive alone, and kept a list made before the character existed."""
        try:
            d = self.disc.archive(archive)
            return (self.disc.path, archive, d.count, d.size)
        except Exception:                                  # noqa: BLE001
            return (self.disc.path, archive)

    def skeleton_names(self, archive="SWAT.DAT"):
        """`.skel` name -> tuple of bone names, in file order."""
        key = self._arc_key(archive)
        if key not in getattr(self, "_skel_names", {}):
            self.skeleton_index(archive)          # fills both
        return self._skel_names.get(key, {})

    def skeleton_parents(self, archive="SWAT.DAT"):
        """`.skel` name -> tuple of each bone's PARENT name, in file order."""
        key = self._arc_key(archive)
        if key not in getattr(self, "_skel_parents", {}):
            self.skeleton_index(archive)          # fills all three
        return self._skel_parents.get(key, {})

    def hosts_clip(self, archive, want, skel_name, clip_name=""):
        """Can this skeleton, LONGER than the clip needs, drive it unchanged?

        Both the Animation tab's picker and the BVH export ask this, and they
        must agree: a picker that offers a rig the exporter then refuses is
        worse than one that never offered it. So the rule lives here once.

        The plain case is a name-for-name match over the bones the clip drives;
        the surplus bones simply stay at rest. That is not a liberty the tool
        is taking, it is what the engine does. The animation applier at
        0x002D55B8 takes its loop count from the SKELETON -- `lw v0, 0(s2)`
        then `lbu v0, 0(v0)`, where s2 is the skeleton (its +4 is the array of
        80-byte bone records) and the clip is the OTHER argument (its +4 is the
        float channel array, walked from byte 12 in steps of 12, so bone i gets
        channels 3+3i..3+3i+2). Its caller at 0x00222080 then loads the clip's
        channel count, divides by 3, compares it with that same bone count, and
        where the clip falls short walks the remaining bone records resetting
        each to identity (0x002220B0-0x00222128). Stock data leans on this:
        brb.skel and tck.skel are 52 bones and every one of their clips drives
        53; pgj.skel is 52 and 252 of its 255 clips drive 53.

        ONE RENAME AT THE ACCESSORY ATTACHMENT POINT is also allowed, because a
        NAME is only a proxy for "bone i is the same joint" and on this disc it
        is lossy in exactly one place. srd.skel's bone 52 is `tail_root` where
        the canonical 53-bone rig says `coattail` -- but it has the same index,
        the same parent (`pelvis`), the same channel set (rX rY rZ), and t01..
        t06 all hang off it. It is `coattail` renamed, and refusing it meant
        Red's own skeleton was rejected for Red's own clips, so srd_load_a.anim
        opened on bdg.skel.

        A rename tolerance is a threshold, and thresholds misbehave, so it is
        fenced by four conditions that each do real work:

          * the skeleton is strictly LONGER than the clip, so it can host the
            whole thing -- this is what keeps 2-bone gun rigs out;
          * exactly ONE name over the driven span differs;
          * it is the LAST driven bone, which is where an accessory attaches
            (`.acc` SkelInsert is the one-based index of the bone after it);
          * that bone's parent name matches the canonical rig's AND is not the
            root, because every rig on the disc starts at `root` and agreeing
            there is no evidence at all.

        MEASURED over SWAT.DAT across every clip size present on the disc: the
        rename clause admits srd.skel and srdsdw.skel for the 2,600 clips that
        drive 53 bones, and nothing else at any other size. Drop the last
        condition and a 2-bone clip is offered 180 unrelated character rigs.

        NOT covered: this is a static argument about rigs, not a playback test.
        Nothing here was checked against a running game.
        """
        names = self.skeleton_names(archive)
        got = names.get(skel_name) or ()
        if len(got) <= want:
            return False
        prefix = self.canonical_prefix(archive, want, clip_name)
        if not prefix:
            return False
        if got[:want] == prefix:
            return True
        diff = [i for i in range(want) if got[i] != prefix[i]]
        if diff != [want - 1]:
            return False
        parents = self.skeleton_parents(archive)
        cpar = next((parents.get(m, ()) for m, v in names.items()
                     if len(v) == want and v[:want] == prefix), ())
        if not cpar:
            return False
        i = diff[0]
        par = parents.get(skel_name, ())[i:i + 1]
        par = par[0] if par else None
        return bool(par) and par == cpar[i] and par != prefix[0]

    def rigs_for_clip(self, archive, want, clip_name=""):
        """Every rig that can drive this clip, best first. THE only such rule.

        Returns [(label, bones, note, writable)] where `bones` is the bone list
        a renderer or the BVH writer, already cut to what the clip drives, and
        `note` is a sentence or "" -- never a warning, because nothing in this
        list is a mistake to pick.

        WHY THIS IS ONE FUNCTION. The picker, the 3D viewer and the BVH export
        each used to decide this for themselves, and three copies of a rule
        drift: the picker offered 158 skeletons for a clip the exporter would
        accept 14 of, and answered the other 144 with "is not a 53-bone
        skeleton" AFTER the user had chosen one. For the 255 pgj clips even
        the picker's own default was refused, so the ordinary path through the
        Animation tab ended in an error. A list that is computed once and used
        by everything cannot disagree with itself.

        The rule, in the order the engine allows:

          EXACT    the rig has one bone per curve triple.
          LONGER   the surplus bones stay at rest. Fenced by hosts_clip, which
                   carries the evidence and the one rename tolerance.
          SHORTER  the surplus CHANNELS have no bone. The applier at
                   0x002D55B8 takes its loop count from the SKELETON, so the
                   game does exactly this -- pgj.skel is 52 bones and 252 of
                   its 255 clips drive 53. Names must agree over the bones
                   they share, or length alone would offer a 73-bone horse rig
                   for a 52-bone walk.
          ACCESSORY a contiguous slice of a character's own .skel, because a
                   13-bone skel_hair clip has no standalone skeleton anywhere
                   on the disc.

        Ranked by name affinity FIRST, then by distance from the clip's own
        bone count. Sorting by bone count first looks right and is not: Red's
        skeleton carries his coat tail at 59 bones while many of his clips
        drive 52, so srd_load_a.anim sat behind 108 exact-length strangers and
        opened on bdg.skel. The game plays Red's animation on Red.
        """
        idx = self.skeleton_index(archive)
        prefix = self.canonical_prefix(archive, want, clip_name)
        names = self.skeleton_names(archive)
        scored = []                      # (affinity_key, distance, label)
        keep = {}                        # label -> (bones, note)

        for n, c in sorted(idx.items()):
            note = ""
            if c == want:
                pass
            elif not prefix:
                continue
            elif c < want:
                if names.get(n, ())[:c] != prefix[:c]:
                    continue
                note = ("%s has %d bones and this clip drives %d, so the last "
                        "%d rotation triple(s) have nothing to move -- the "
                        "game leaves them out the same way."
                        % (n, c, want, want - c))
            elif not self.hosts_clip(archive, want, n, clip_name):
                continue
            scored.append((abs(c - want), n))
            keep[n] = (None, note)       # bones parsed lazily below

        for label, bones, note in self.accessory_skels(archive, want):
            scored.append((0, label))
            keep[label] = (bones, note or "")

        from . import bvh as BVH
        from . import skeleton as SK
        resolved = {}
        for _d, label in scored:
            if label in resolved:
                continue
            bones, note = keep[label]
            if bones is None:
                try:
                    bones = SK.parse(self.disc.read(archive, label))[2]
                except Exception:                          # noqa: BLE001
                    continue
            bones = bones[:want]
            resolved[label] = (bones, note, BVH.writable_rig(bones)[0])

        # A rig BVH cannot write is still perfectly good to WATCH -- the viewer
        # builds a bone hierarchy and does not care how many roots it has -- so
        # it stays on offer and only loses ties. That is enough to keep it from
        # ever being the default: 2 of SWAT.DAT's 236 skeletons are forests,
        # measured, so in practice the default always exports and the Export
        # button is never a trap.
        scored.sort(key=lambda t: (name_affinity(t[1], clip_name),
                                   not resolved.get(t[1], (0, 0, False))[2],
                                   t[0], str(t[1]).lower()))
        out, seen = [], set()
        for _d, label in scored:
            if label in seen or label not in resolved:
                continue
            seen.add(label)
            bones, note, writable = resolved[label]
            out.append((label, bones, note, writable))
        return out

    def part_pose(self, archive, bundle_name, mesh):
        """(offsets, skeleton name) to turn this part into a standing figure.

        Character meshes are stored in BONE-LOCAL space, so dumping them
        straight to OBJ piles every limb on the origin -- a tester opened one
        in Blender and got a crumpled ball, and called it "imploded". The bind
        pose is translation only (vif.to_bind_pose), so posing is an add of
        each vertex's own bone's world position and un-posing is the same
        subtraction.

        BOTH SIDES CALL THIS. Export adds what it returns, import subtracts
        what it returns, and neither writes anything into the file about it --
        which matters because BLENDER STRIPS COMMENTS. Any scheme that carried
        the offsets in an OBJ header would work perfectly in a test and then
        silently subtract nothing on the only path anybody uses.

        Returns ([], None) when the part is not skinned or no skeleton can be
        found. That answer is deterministic for a given disc, so export and
        import agree by construction: either both pose or neither does.
        """
        from . import skeleton as SK
        if not mesh.get("skinned") or not mesh.get("bones"):
            return [], None
        stem = bundle_name.rsplit(".", 1)[0]
        for suffix in ("_lods", ""):
            base = stem[:-len(suffix)] if suffix and stem.endswith(suffix) \
                else stem
            want = base + ".skel"
            for arc in (archive, "SWAT.DAT"):
                try:
                    bones = SK.parse(self.disc.read(arc, want))[2]
                except Exception:                          # noqa: BLE001
                    continue
                world = SK.world_positions(bones)
                bw = [world[b.name] for b in bones]
                zero = (0.0, 0.0, 0.0)
                offs = [bw[b] if (b is not None and b < len(bw)) else zero
                        for b in mesh["bones"][:len(mesh["verts"])]]
                if len(offs) < len(mesh["verts"]):
                    offs += [zero] * (len(mesh["verts"]) - len(offs))
                return offs, want
        return [], None

    def skel_world(self, archive, bundle_name):
        """Bind-pose world position of every bone of this bundle's skeleton,
        in .skel file order, or None if no skeleton can be found. Same search
        as part_pose."""
        from . import skeleton as SK
        stem = bundle_name.rsplit(".", 1)[0]
        for suffix in ("_lods", ""):
            base = stem[:-len(suffix)] if suffix and stem.endswith(suffix) \
                else stem
            for arc in (archive, "SWAT.DAT"):
                try:
                    bones = SK.parse(self.disc.read(arc, base + ".skel"))[2]
                except Exception:                          # noqa: BLE001
                    continue
                world = SK.world_positions(bones)
                return [world[b.name] for b in bones]
        return None

    def accessory_skels(self, archive, want):
        """Accessory rigs of exactly `want` bones, cut out of their character.

        An accessory animation drives ONLY the accessory -- a 13-bone
        skel_hair clip is 42 channels, not the character's 58 -- and no
        standalone .skel of that length exists, because the bones live inside
        the character's own file. So the rig has to be cut out before such a
        clip can be exported at all. See skeleton.split_accessory: SkelInsert
        is the ONE-BASED index of the first accessory bone.

        Returns [(label, bones, note)]. `note` carries any inconsistency in
        the stock data rather than swallowing it -- srd.acc overruns its own
        skeleton by one bone and ships in 57 archives.
        """
        import re
        from . import skeleton as SK
        out: list[Any] = []
        try:
            entries = list(self.disc.toc(archive))
        except Exception:                                  # noqa: BLE001
            return out
        for e in entries:
            low = e.name.lower()
            if not (low.endswith(".acc") or low.endswith(".tail")):
                continue
            stem = e.name.rsplit(".", 1)[0]
            try:
                txt = self.disc.read(archive, e.name).decode("latin-1", "replace")
                bones = SK.parse(self.disc.read(archive, stem + ".skel"))[2]
            except Exception:                              # noqa: BLE001
                continue
            for m in re.finditer(
                    r"Name\s+(\S+).*?SkelInsert\s+(\d+).*?NumBones\s+(\d+)",
                    txt, re.S):
                nm, ins, nb = m.group(1), int(m.group(2)), int(m.group(3))
                if nb != want:
                    continue
                _base, acc, note = SK.split_accessory(bones, ins, nb)
                if len(acc) != want:
                    continue
                out.append(("%s :: %s" % (stem, nm), acc, note))
        return out

    def canonical_prefix(self, archive, want, clip_name=""):
        """The bone-name prefix a `want`-bone clip expects, or None.

        Taken from the skeletons that are EXACTLY that length: whatever the
        majority of them agree on is what "bone i" means for a clip of this
        size.

        WHEN NOTHING IS EXACTLY THAT LENGTH, the clip's own family answers.
        Requiring an exact-length rig to anchor the names left 16 of SWAT.DAT's
        4,187 clips (measured) with no rig on offer at all -- 11 of them wolf
        clips driving 29 bones, while wlf.skel sits in the same archive with
        41. A 41-bone rig hosting a 29-bone clip is the ordinary LONGER case;
        it was excluded only because the anchor could not be built, so the
        answer was "nothing" for a question with an obvious answer.

        So: fall back to the rig named by the clip's own prefix. `wlf_dwn_bit`
        -> `wlf.skel` -> its first 29 names. That is the same family rule the
        ranking already trusts to put a clip on its own character, and it is
        strictly narrower than "anything goes" -- a rig still has to agree with
        it name-for-name to be offered.
        """
        from collections import Counter
        names = self.skeleton_names(archive)
        votes = Counter(v[:want] for v in names.values() if len(v) == want)
        if votes:
            return votes.most_common(1)[0][0]
        stem = str(clip_name or "").split("_", 1)[0].lower()
        if stem:
            for label, v in names.items():
                base = str(label).rsplit(".", 1)[0].lower()
                if base == stem and len(v) >= want:
                    return v[:want]
        return None

    def character_rig_index(self, archive="SWAT.DAT"):
        """Every character bundle in one archive with the rig size it needs.

        The Animation tab uses this to offer only the models a given clip's
        skeleton can actually drive. Reading it costs one pass over the
        bundles' chunk tables -- 310 bundles in SWAT.DAT, 0.27 s measured
        cold -- so it is cached per (disc, archive) rather than per request.
        """
        from .rsc import Rsc
        key = self._arc_key(archive)
        cache: "dict[Any, Any] | None" = getattr(self, "_rig_index", None)
        if cache is None:
            cache = self._rig_index = {}
        if key in cache:
            return cache[key]
        dave = self.disc.archive(archive)
        rows = []
        for e in dave:
            if not e.name.endswith("_lods.rsc"):
                continue
            try:
                b = Rsc(dave.read(e.name), e.name)
            except Exception:
                continue
            counts = [b.bone_count(c.index) for c in b.chunks
                      if c.tag == "MOD0"]
            if not counts:
                continue
            rows.append(dict(stem=e.name[:-9], file=e.name,
                             bone_count=max(counts),
                             textures=len(b.texture_table())))
        rows.sort(key=lambda r: r["stem"])
        cache[key] = rows
        return rows

    def keep_level(self, archive, manifest, area, lvl):
        """Put an already-consistent Level back in the cache after a write.

        Studio.write drops the cached Level for safety, but Level.apply has
        just reparsed this one against the bytes it wrote -- rebuilding it
        from the ISO on the next request repeats that work for nothing.
        """
        with self.lock:
            self._levels[(archive, manifest, area)] = lvl
            self._levels[(archive, manifest, lvl.area)] = lvl

    # A new level in the Data Editor's mods folder that is not on the working disc yet -- an imported Xbox level
    # (Coliseum: the Xbox game has it, the PS2 disc not) or a New .dat map before its first Build: listed with the
    # disc's levels and opened from its folder (packed in memory), to look at; a Build puts it on the disc, where
    # it can be edited like the others. One mod for both games: on the Xbox side as on the PS2 side.
    def _mods_level_folders(self):
        """{ARCHIVE: folder} of the levels the working disc has not: the mods folder's new ones, then the Xbox game's
        own the PS2 disc lacks (Coliseum), made the PS2's in the cache to show (rdrstudio/xboxmode.py)."""
        out = {}
        de = _data_editor_for(self)
        if de is None:
            return out
        try:
            from .xboxmode import xbox as _xbox
            X = _xbox(de)
            xlevels = X.xbox_level_archives()
        except Exception:                                   # noqa: BLE001
            X, xlevels = None, {}
        self._xbox_levels = set(getattr(X, "shipped_levels", set())) if X is not None else set()
        try:
            src = self.game or self.vanilla
            disc = {a.upper() for a in src.archives()} if src is not None else set()
        except Exception:                                   # noqa: BLE001
            disc = set()
        for group in ("lvls_m2", "lvls_m4", "lvls_sng"):
            top = os.path.join(de.mods, group)
            if not os.path.isdir(top):
                continue
            for f in os.listdir(top):
                folder = os.path.join(top, f)
                stem = "lvl_" + f[:-4].lower()
                if not (f.lower().endswith(".dat") and os.path.isdir(folder)):
                    continue
                have = {n.lower() for n in os.listdir(folder)}
                try:
                    xbox_game = X is not None and X.platform() == "xbox"
                except Exception:                           # noqa: BLE001
                    xbox_game = False
                # the PS2 bundle and the Xbox one side by side: shown from a PS2 copy of the Xbox files only when
                # those are the newer (a Blender pair). A pair the editor keeps (New .dat makes the .xrsc; its zone
                # edits go to the .znes) is shown from the folder itself: shown from the .xznes, a deleted model came
                # back and the next delete removed the wrong one (2026-11-18)
                def _newest(ext):
                    ts = [os.path.getmtime(os.path.join(folder, n)) for n in os.listdir(folder)
                          if n.lower().startswith(stem) and n.lower().endswith(ext)]
                    return max(ts) if ts else None
                xz, pz = _newest(".xznes"), _newest(".znes")
                xbox_view = xbox_game and stem + ".xrsc" in have and xz is not None and (pz is None or xz >= pz - 2)
                if stem + ".rsc" in have and not xbox_view:
                    out[("%s/%s" % (group, f)).upper()] = folder
                elif stem + ".xrsc" in have and X is not None:
                    # a new level imported for the Xbox only: shown from a PS2 copy made in the cache (the mods
                    # folder itself is left as it is)
                    try:
                        out[("%s/%s" % (group, f)).upper()] = X.converted_folder_level(
                            ("%s/%s" % (group, f)).upper(), folder)
                    except Exception:                       # noqa: BLE001
                        pass
        self._mods_only = set(out)
        for arc, path in sorted(xlevels.items()):
            if arc in out or arc in disc:
                continue
            try:
                out[arc] = X.converted_level(arc, path)
            except Exception:                               # noqa: BLE001
                continue
        return out

    def mods_levels(self, listed):
        """Catalog entries for the mods folder's new levels the working disc does not have (see above)."""
        from . import modes as _modes
        out = []
        folders = self._mods_level_folders()
        native = getattr(self, "_xbox_levels", set())
        for arc, _folder in sorted(folders.items()):
            if arc in listed:
                continue
            group, fname = arc.split("/", 1)
            name = fname[:-4].upper()                      # as the game's own are listed (RANCH2)
            showdown = group in ("LVLS_M2", "LVLS_M4")
            players = "2P" if group == "LVLS_M2" else "4P" if group == "LVLS_M4" else ""
            if arc in native:
                # one of the Xbox game's own (Coliseum): with the game's levels, as on the Xbox
                mode = "showdown2" if group == "LVLS_M2" else "showdown4" if group == "LVLS_M4" else "bonus"
                out.append(dict(archive=arc, name=name, group=group, base=name.upper(), mode=mode,
                                mode_label=_modes.MODE_LABELS[mode],
                                missions=[], players=players, from_mods=True, xbox_level=True))
                continue
            where = "Xbox" if arc not in getattr(self, "_mods_only", set()) else "mods folder"
            out.append(dict(archive=arc, name=name + (" (M, %s)" % where if showdown else " (S, %s)" % where),
                            group=group, base=name.upper(), mode="custom", mode_label="Custom levels",
                            missions=[], kind="showdown" if showdown else "single", players=players,
                            from_mods=True))
        return out

    def level(self, archive, reload=False, manifest=None, area=None):
        with self.lock:
            self.ensure_open()
            key = (archive, manifest, area)
            if reload:
                # `reload` means distrust everything, and until REQ-077 it only
                # reached the TOP layer. There are three more underneath: the
                # parsed Level here, then Game._cache holding a `Dave` whose
                # TOC was read once at construction, then that Dave's
                # IsoBacking holding the archive's extent captured from
                # iso.get(), then the ISO handle itself (now unbuffered --
                # see iso9660.Iso.__init__).
                #
                # Dropping the Dave rebuilds the middle two. It costs a TOC
                # re-parse, which is a handful of reads, NOT the 0.34-1.14 s
                # Level rebuild -- and a caller passing reload=1 is paying that
                # anyway. Writes through this process do not need it: patch()
                # keeps the in-memory TOC correct, and _drop_level is
                # deliberately left alone so the ordinary post-edit refresh
                # stays cheap. This is for the case where the bytes moved
                # under us -- another process, or a restore.
                for g in (self.game, self.vanilla):
                    if g is not None:
                        g._cache.pop(archive.upper(), None)
            if reload or key not in self._levels:
                # Fall back to the pristine disc while the working one is
                # locked by the emulator: browsing and inspecting still work,
                # only writing is unavailable.
                src = self.game or self.vanilla
                try:
                    on_disc = archive.upper() in {a.upper() for a in src.archives()}
                except Exception:                           # noqa: BLE001
                    on_disc = True
                if not on_disc:
                    folder = self._mods_level_folders().get(archive.upper())
                    if folder:
                        src = _FolderLevelGame(src, archive, folder)
                lv = Level(src, archive, manifest, area=area)
                lv.grow_by_rebuild = bool(src is self.game and self.rebuild_on_overflow)
                # A level whose parse hit a READ error is short some objects,
                # and caching it means editing a half-level for the rest of the
                # session. A decode error is different -- that file will fail
                # the same way every time, so caching it is correct.
                if any("short read" in e or "decompress" in e for e in lv.errors):
                    return lv
                self._levels[key] = lv
                # Alias the resolved area too: the first open asks with
                # area=None, every follow-up names the area the payload
                # returned, and the two keys used to build the same Level
                # twice (measured 0.34 s each on TOMB4).
                self._levels[(archive, manifest, lv.area)] = lv
            return self._levels[key]

    def asset_index(self, rebuild=False):
        """The disc-wide asset catalogue, built once and cached to disc.

        Built from the pristine disc: the catalogue answers "what models
        exist and where", which edits to placement files do not change.
        """
        with self.lock:
            if self._assets is None:
                from . import assets as assets_mod
                self._assets = assets_mod.AssetIndex(
                    self.vanilla, os.path.join(cfg.BACKUPS,
                                               "asset-index.json"))
        self._assets.data(rebuild=rebuild)
        return self._assets

    def _drop_level(self, archive):
        """Invalidate every cached view of an archive (all manifests/areas)."""
        with self.lock:
            for key in [k for k in self._levels if k[0] == archive]:
                self._levels.pop(key, None)

    def restore(self, clear_mods=False, save=True, save_name=None, include_new=True):
        """Copy the pristine disc over the working one. With clear_mods the Data Editor's own mod files go too
        (kept as a Saved Mod first): else they count as waiting and the next Build puts them back on the disc.
        -> (saved Mod name or None, files cleared)."""
        out = (None, 0)
        with self.lock:
            self.close()
            shutil.copyfile(self.vanilla_path, self.modded_path)
            self.pending.clear()              # queued edits of the disc just replaced
            try:
                _data_editor(self).mirror_reset()
            except Exception:                              # noqa: BLE001
                pass
            # The history describes edits that no longer exist on disc; keeping
            # it would offer undos of a world that was just wiped away.
            if self.journal:
                self._clear_session()
            if clear_mods:
                try:
                    from . import dataeditor_more as _more
                    out = _more.more(_data_editor(self)).clear_for_reset(save, save_name, include_new)
                except Exception as ex:                         # noqa: BLE001
                    self.note("reset: the Data Editor's mod files stayed (%s)" % ex)
            self.open()
            self.note("restored modded ISO from vanilla")
        return out

    def diff(self):
        """Every file whose CONTENT differs from vanilla.

        Compares decompressed bytes, not stored bytes. Re-saving a file at
        zlib level 9 changes its stored form even when the content is
        byte-identical to vanilla, and reporting that as a modification would
        be a lie. Stored-bytes equality is used only as a fast "definitely
        unchanged" path.
        """
        with self.lock:
            # Nothing to compare while the working ISO is released. Say so
            # rather than raising AttributeError on None.
            self.ensure_open(force=True)
            if self.game is None:
                # The cause, not a guess at it: the disc can also be missing,
                # read-only, or held by another writer, and only one of the
                # four is the emulator. See locked_problem().
                raise api.DiscLocked(*self.locked_problem())
            changed = []
            for arc in self.game.archives():
                if self.job is not None and self.job.get("cancel"):
                    raise JobCancelled(self.job.get("label", ""))
                try:
                    a = self.game.archive(arc)
                    b = self.vanilla.archive(arc)
                except Exception:
                    continue
                bmap = {e.name: e for e in b}
                for e in a:
                    v = bmap.get(e.name)
                    if v is None:
                        continue
                    if (e.size_comp == v.size_comp
                            and a.backing.read_at(e.offset, e.size_comp)
                            == b.backing.read_at(v.offset, v.size_comp)):
                        continue  # identical on disc, nothing to decompress
                    try:
                        if a.read(e) == b.read(v):
                            continue  # repacked but content-identical
                    except Exception:
                        pass  # undecodable -> fall through and report it
                    changed.append(dict(archive=arc, name=e.name,
                                        size=e.size_full,
                                        vanilla_size=v.size_full))
                self.game._cache.pop(arc.upper(), None)
                self.vanilla._cache.pop(arc.upper(), None)
            return changed

    # --------------------------------------------------------- checkpoints

    def checkpoint(self, label=""):
        """Save a restore point of everything that differs from vanilla.

        Built from the real diff rather than the journal, so it captures the
        disc's actual state even across restarts or edits made another way.
        Only changed files are stored, so a checkpoint is kilobytes rather
        than the 2.2 GB a full ISO copy would cost.
        """
        import base64
        import hashlib
        import json as _json
        with self.lock:
            changed = self.diff()
            files = []
            for c in changed:
                data = self.game.archive(c["archive"]).read(c["name"])
                files.append(dict(archive=c["archive"], file=c["name"],
                                  size=len(data),
                                  sha256=hashlib.sha256(data).hexdigest()[:16],
                                  data_b64=base64.b64encode(data).decode("ascii")))
            stamp = time.strftime("%Y%m%d-%H%M%S")
            safe = "".join(ch for ch in label if ch.isalnum() or ch in " _-").strip()
            safe = safe.replace(" ", "_")
            name = "%s%s" % (stamp, "_" + safe if safe else "")
            os.makedirs(cfg.BACKUPS, exist_ok=True)
            path = os.path.join(cfg.BACKUPS, name + ".checkpoint.json")
            doc = dict(format="rdrmod/1", name=label or ("checkpoint " + stamp),
                       notes="restore point", game="SLUS-20500",
                       created=time.strftime("%Y-%m-%d %H:%M:%S"),
                       checkpoint=True, files=files)
            with open(path, "w") as f:
                _json.dump(doc, f, indent=1)
            self.note("checkpoint '%s': %d changed file(s) -> %s"
                      % (doc["name"], len(files), path))
            return dict(ok=True, path=path, name=doc["name"], files=len(files))

    def checkpoints(self):
        out: list[Any] = []
        if not os.path.isdir(cfg.BACKUPS):
            return out
        import json as _json
        for fn in sorted(os.listdir(cfg.BACKUPS), reverse=True):
            if not fn.endswith(".checkpoint.json"):
                continue
            path = os.path.join(cfg.BACKUPS, fn)
            try:
                with open(path) as f:
                    doc = _json.load(f)
            except Exception:
                continue
            out.append(dict(path=path, file=fn, name=doc.get("name"),
                            created=doc.get("created"),
                            files=len(doc.get("files", [])),
                            bytes=os.path.getsize(path)))
        return out

    def restore_checkpoint(self, path):
        """Put the disc back exactly as it was when the checkpoint was taken.

        Anything changed since that is not in the checkpoint is reverted to
        vanilla first, so this is a true restore rather than an overlay.
        """
        import json as _json
        with self.lock:
            with open(path) as f:
                doc = _json.load(f)
            wanted = {(f["archive"], f["file"]) for f in doc.get("files", [])}
            group = self.journal.new_group()
            reverted = 0
            # One autosave for the whole restore, not one per file -- the
            # session document is tens of megabytes. See Journal.batch().
            with self.journal.batch():
                for c in self.diff():
                    key = (c["archive"], c["name"])
                    if key in wanted:
                        continue
                    original = self.vanilla.archive(c["archive"]).read(c["name"])
                    self.write(c["archive"], c["name"], original,
                               "restore checkpoint: revert %s" % c["name"], group)
                    reverted += 1
                applied, failed = apply_mod(doc, self.game)
                for a in applied:
                    data = self.game.archive(a["archive"]).read(a["file"])
                    self.journal.record(a["archive"], a["file"], data,
                                        "restore checkpoint '%s'" % doc.get("name"),
                                        group)
            self._levels = {}
            self.note("restored checkpoint '%s': %d file(s) applied, %d reverted"
                      % (doc.get("name"), len(applied), reverted))
            return dict(ok=not failed, name=doc.get("name"),
                        applied=len(applied), reverted=reverted, failed=failed)

    def file_diff(self, archive, name, context=3):
        """Line-level diff of one file against vanilla, when it is text."""
        import difflib
        with self.lock:
            try:
                stock = self.vanilla.archive(archive).read(name)
            except Exception:
                return dict(ok=False, error="no vanilla copy of %s" % name)
            current = self.game.archive(archive).read(name)
            if stock == current:
                return dict(ok=True, identical=True, lines=[])
            probe = current[:2048]
            texty = probe and sum(
                1 for c in probe if 32 <= c < 127 or c in (9, 10, 13)
            ) > len(probe) * 0.95
            if not texty:
                return dict(ok=True, identical=False, binary=True,
                            stock_size=len(stock), size=len(current),
                            lines=[])
            a = stock.decode("latin-1").splitlines()
            b = current.decode("latin-1").splitlines()
            lines = list(difflib.unified_diff(
                a, b, fromfile="vanilla", tofile="yours",
                n=context, lineterm=""))
            return dict(ok=True, identical=False, binary=False,
                        lines=lines[:4000], truncated=len(lines) > 4000)

    def revert_file(self, archive, name):
        """Restore one file from vanilla, leaving every other mod intact.

        Surgical undo: reads the pristine bytes and patches them back into the
        same slot. Always fits, because it is exactly what shipped there.
        """
        with self.lock:
            original = self.vanilla.archive(archive).read(name)
            used, slot = self.write(archive, name, original,
                                    "revert %s to vanilla" % name)
            self.note("reverted %s :: %s to vanilla (%d/%d bytes)"
                      % (archive, name, used, slot))
            return dict(ok=True, archive=archive, name=name,
                        used=used, slot=slot, size=len(original))

    # ------------------------------------------------------------- weapons

    def edit_gun(self, typename, changes, everywhere=True, dry=False):
        """Change a weapon's stats in every copy of swat.guns on the disc.

        Most gameplay files exist in SWAT.DAT *and* every level archive, and
        the level copy appears to win at runtime -- so editing only SWAT.DAT
        looks like it did nothing. Default is all 63 copies.
        """
        import zlib
        from . import guns

        with self.lock:
            archives = (self.game.locate("swat.guns") if everywhere
                        else ["SWAT.DAT"])
            applied, failed = [], []
            # One weapon edit = one undo step, however many copies it touches.
            group = self.journal.new_group()
            for arc in archives:
                d = self.game.archive(arc)
                try:
                    e = d.find("swat.guns")
                    new = guns.set_fields(d.read(e), typename, changes)
                    if e.compressed:
                        co = zlib.compressobj(9, 8, -15)
                        stored = len(co.compress(new) + co.flush())
                    else:
                        stored = len(new)
                    if stored > e.slot:
                        failed.append(dict(archive=arc, error=(
                            "needs %d stored bytes, slot is %d" % (stored, e.slot))))
                        continue
                    if dry:
                        applied.append(dict(archive=arc, stored=stored, slot=e.slot))
                    else:
                        used, slot = self.write(
                            arc, "swat.guns", new,
                            "%s: %s" % (typename, ", ".join(
                                "%s=%s" % kv for kv in changes.items())),
                            group=group)
                        applied.append(dict(archive=arc, stored=used, slot=slot))
                except Exception as ex:
                    failed.append(dict(archive=arc,
                                       error="%s: %s" % (type(ex).__name__, ex)))
            if not dry:
                self.note("%s edited in %d archive(s), %d failed"
                          % (typename, len(applied), len(failed)))
            return dict(ok=not failed, dry=dry, weapon=typename,
                        archives=len(archives), applied=applied, failed=failed)

    # ------------------------------------------------------------ creatures

    def edit_swt(self, filename, changes, everywhere=True, dry=False,
                 group=None):
        """Change combat fields in every copy of a `.swt` on the disc.

        A thin alias for `edit_creature` with a different field writer. The
        machinery -- locate every copy, check each against its own slot,
        journal them all as ONE undo step -- is identical and worth having in
        one place: a weapon edit touches 62 copies of `swat.guns`, and the
        same is true here.
        """
        from . import swt
        return self.edit_creature(filename, changes, everywhere=everywhere,
                                  dry=dry, group=group,
                                  setter=swt.set_fields, kind="combat")

    def edit_creature(self, filename, changes, everywhere=True, dry=False,
                      group=None, setter=None, kind="creature", archives=None,
                      grow=False):
        """Change creature fields in every copy of a .cr on the disc.

        Same shape as edit_gun: dry-run reports which copies fit, a real run
        journals all writes as ONE undo group. Creature edits are EXPERIMENTAL
        -- the Steam Deck build of this mod never worked in game, so nothing
        here claims success beyond "bytes applied and disc still boots".

        `group` lets a CALLER own the undo step. A preset that edits every
        creature on the disc calls this once per `.cr`, so opening a group
        here made "Moon gravity" 181 undo steps for one click. The default is
        unchanged: on its own this is still one step.

        `grow` relocates a copy that outgrew its slot into its archive's free
        run, exactly as the "allow files to grow" tick already does for a
        dropped file. Off by default and opt-in for the same reason it is
        there: relocation moves a payload, and the set of files with an
        undiscovered alignment need is not known to be empty (see
        dave.SECTOR_PINNED). What it is NOT is impossible: the character-stat
        route's old overflow advice said so in as many words, and measured
        on stock, 747 of the 939 files in that family fit SWAT.DAT's free
        run. See CAPABILITIES.md C-018 -- the retired sentence is quoted
        there and deliberately not repeated here, because the M3 check
        greps the whole toolkit for it.

        `archives` narrows the sweep to a caller-chosen subset. A model swap
        needs it: repointing `FileNameRender` is only legal in an archive that
        already holds the target bundle, and the median bundle is in 2 of the
        66 archives. Everything else -- the per-copy slot check, the failure
        list, the single undo group -- is identical, which is the whole reason
        this is a parameter rather than a second copy of the loop.
        """
        import zlib
        from . import creature
        write_fields = setter or creature.set_fields

        with self.lock:
            # `self.game` is None whenever the working ISO is not ours -- the
            # emulator has it, or it opened read-only. `write()` guards this
            # and raises the same thing the routes turn into a `disc_busy`
            # refusal, but this function reaches `game.locate` and
            # `game.archive` BEFORE the first write, so without this the
            # dry-run died on `'NoneType' has no attribute 'archive'` and the
            # user got a 500 fault instead of a refusal. The SAME exception as
            # `write()` on purpose -- it used to be the same hand-typed
            # sentence, which is how it came to name the emulator for all four
            # causes.
            if self.game is None:
                raise api.DiscLocked(*self.locked_problem())
            if archives is None:
                archives = (self.game.locate(filename) if everywhere
                            else ["SWAT.DAT"])
            applied, failed = [], []
            group = group or self.journal.new_group()
            for arc in archives:
                d = self.game.archive(arc)
                try:
                    e = d.find(filename)
                    old = d.read(e)
                    new = write_fields(old, changes)
                    if e.compressed:
                        co = zlib.compressobj(9, 8, -15)
                        stored = len(co.compress(new) + co.flush())
                    else:
                        stored = len(new)
                    # Both byte counts ride on every row, refused or not.
                    # `routes2` projects these straight into `api.plan`, and a
                    # refusal that cannot say how far over it is is not a
                    # message. Purely additive: `stored`, `slot`, `archive` and
                    # `error` all keep their old names and meanings.
                    row = dict(archive=arc, bytes_before=len(old),
                               bytes_after=len(new), stored=stored, slot=e.slot)
                    if stored > e.slot:
                        # Say whether there is room to MOVE it, not just that
                        # it does not fit where it sits. The two need opposite
                        # advice, and the caller cannot tell them apart from a
                        # byte count.
                        gap = d.largest_gap()[1]
                        row = dict(row, gap=gap, grow_possible=gap >= stored)
                        if not (grow and gap >= stored):
                            failed.append(dict(row, error=(
                                "needs %d stored bytes, slot is %d"
                                % (stored, e.slot))))
                            continue
                        row = dict(row, grown=True)
                    if dry:
                        applied.append(row)
                    else:
                        used, slot = self.write(
                            arc, filename, new,
                            "%s: %s" % (filename, ", ".join(
                                "%s=%s" % kv for kv in changes.items())),
                            group=group, grow=grow)
                        applied.append(dict(row, stored=used, slot=slot))
                except Exception as ex:
                    failed.append(dict(archive=arc,
                                       error="%s: %s" % (type(ex).__name__, ex)))
            if not dry:
                self.note("%s edited in %d archive(s), %d failed"
                          % (filename, len(applied), len(failed)))
            # `group` rides back so a caller writing a PAIR of files (the
            # Creatures panel saves a .cr and a .swt together) can hand it to
            # the second write and keep the pair one undo step.
            return dict(ok=not failed, dry=dry, creature=filename, kind=kind,
                        group=group, archives=len(archives),
                        applied=applied, failed=failed)

    # ------------------------------------------------------------------- boot

    # The intro sequence alone runs about 100 seconds. A shorter run cannot
    # tell a disc that plays from one that is frozen behind the logos, and
    # this project has already published a "boot passed" that meant nothing
    # for exactly that reason. 45s was less than half the floor; 185s is the
    # length that has actually distinguished a good disc from a bad one here.
    BOOT_SECONDS = 185
    BOOT_FLOOR = 100

    def boot_test(self, seconds=None):
        seconds = self.BOOT_SECONDS if seconds is None else int(seconds)
        if not self.pcsx2 or not os.path.exists(self.pcsx2):
            return dict(ok=False, error="PCSX2 not found at %r" % self.pcsx2)
        log = self.emulog
        launched_at = time.time()
        # PCSX2 refuses the ISO (errno 13) while our writable handle is open,
        # so release everything for the duration of the run. The next API
        # call reopens automatically.
        self.close()
        proc = subprocess.Popen([self.pcsx2, "-fastboot", "-nogui", self.modded_path])
        self.note("launched PCSX2 for boot test (%ds)" % seconds)
        time.sleep(seconds)
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

        self.open()

        # PCSX2 REWRITES emulog.txt from scratch on every launch, so the whole
        # file is this run's log -- there is nothing to diff against. Reading
        # from the previous size found an empty tail and reported healthy
        # boots as failures. Guard staleness by timestamp instead: if the log
        # predates the launch, PCSX2 never wrote and the result is unknown.
        text = ""
        stale = True
        if log and os.path.exists(log):
            if os.path.getmtime(log) >= launched_at - 5:
                stale = False
                with open(log, "r", errors="replace") as f:
                    text = f.read()
        if stale:
            self.note("boot test: emulator log not updated -- did PCSX2 start?")
            return dict(ok=False, booted=False, tlb_miss=0, stale=True,
                        error="PCSX2 did not write %s -- it may have failed to "
                              "launch, or is configured without file logging"
                              % log)
        tlb = text.count("TLB Miss")
        booted = "is executing" in text or "Elf entry" in text
        ok = booted and tlb == 0
        # A clean log is not a playing game. The run has to outlast the intro
        # before "it booted" means the disc is actually good, so a short run
        # says so in its own result rather than letting PASS speak for it.
        short = seconds < self.BOOT_FLOOR
        self.note("boot test: booted=%s tlb_miss=%d -> %s%s"
                  % (booted, tlb, "PASS" if ok else "FAIL",
                     " (only %ds -- proves little)" % seconds if short else ""))
        return dict(ok=ok, booted=booted, tlb_miss=tlb, seconds=seconds,
                    conclusive=not short, floor=self.BOOT_FLOOR,
                    caveat=("This ran %d seconds. The intro alone is about %d, "
                            "so a clean log here cannot tell a disc that plays "
                            "from one frozen behind the logos."
                            % (seconds, self.BOOT_FLOOR)) if short else "",
                    tail="\n".join(text.splitlines()[-60:]))


# ------------------------------------------------- progression write adapter
#
# progression.py takes a `Game` OR a bare `Dave`, and reaches for exactly
# three things: `.archive(name)`, then `.read`/`.find`/`.patch` on it, plus a
# `.writable` flag. It calls `Dave.patch` itself, which writes real bytes
# while leaving the journal with no record of them -- so an edit made this way
# could not be undone and would not appear in Changes.
#
# Rather than fork the module's careful slot arithmetic, hand it an object
# with that same tiny surface whose `patch` is `Studio.write`. Dry runs never
# reach patch at all, so they cost nothing and touch nothing.

class _ProgressionArchive:
    def __init__(self, studio, archive, label):
        self._s, self._arc, self._label = studio, archive, label

    def read(self, filename):
        return self._s.disc.archive(self._arc).read(filename)

    def find(self, filename):
        # plan() reads .slot and .compressed off this entry to decide whether
        # a change fits; it must describe the archive being WRITTEN.
        return self._s.disc.archive(self._arc).find(filename)

    def patch(self, filename, data):
        return self._s.write(self._arc, filename, data, self._label)


class _ProgressionDisc:
    writable = True

    def __init__(self, studio, label):
        self._s, self._label = studio, label

    def archive(self, name):
        return _ProgressionArchive(self._s, name, self._label)


# ---------------------------------------------------------- the feature gate

# Routes in THIS module that belong to a feature the manifest can hold back.
#
# routes2.py carries a feature id per route and drops the route from its
# dispatch table when the feature is off. Nothing equivalent existed here, so
# features.py's promise --
#
#     "A feature switched off here is genuinely absent from the server: the
#      route is never added to the dispatch table"
#
# -- was only ever true of the ~30 routes that had been moved into routes2.
# The other ~109 live in this module and answered on every build.
#
# It mattered. strings_editor is marked internal, and README.txt tells the
# reader the text editor is held back because it "is not good enough to hand
# over yet". On the release build GET /api/strings answered 200 with real
# data, and POST /api/string_set wrote UTF-16 tables into the user's working
# disc. Held back in the documentation, shipped in the binary.
#
# `INTERNAL_ONLY` means exactly that and does not pretend to be a feature id.
# Borrowing an unrelated id to mean "internal build only" reads as a fact
# about that feature to the next person, and it is not one.
INTERNAL_ONLY = "@internal"

# In-app guides: short name -> file. A fixed table, so the route can never be
# asked for an arbitrary path.
_GUIDES = {"modding": "MODDING_GUIDE.md",
           "custom-models": "HOWTO-CUSTOM-MODELS.md",
           "user-guide": "USER_GUIDE.md"}

_FEATURE_ROUTES = {
    "strings": "strings_editor",
    "string_set": "strings_editor",
    # The Roadmap tab is gone from both editions and ROADMAP.md is furnished
    # only into the internal bundle, so in a release build this route has
    # nothing to read. Gated rather than deleted: the internal build still
    # uses it.
    "roadmap": INTERNAL_ONLY,
}


def rank_skeletons(names, clip):
    """Order skeleton candidates so the clip's OWN character comes first.

    Nothing inside a `.anim` names its skeleton, so the tool matched on bone
    count alone and then took whichever name sorted first. That is arbitrary,
    and arbitrary is usually wrong: `ctn_cir_rgt_run.anim` would open against
    `aka.skel` because A beats C, and the character you were looking at moved
    like someone else.

    The clip's name says whose it is. `ctn_*` is ctn's, `srd_*` is Red's --
    the same prefix the model picker already keys on.

    MEASURED over the 4,166 clips in SWAT.DAT with a bone layout: the prefix
    names a real skeleton for 91.9% of them, and that skeleton has the right
    bone count for 77.2%. Alphabetical order disagreed with the name in 3,137
    cases, so this changes the default for roughly three clips in four.

    Ranking, never filtering. A wrong guess here is a pose, not a write, and
    the picker still lists everything that fits so the player can switch --
    which is the point: land on what the game does, then let them try others.
    """
    return sorted(names, key=lambda n: (name_affinity(n, clip), str(n).lower()))


def name_affinity(skel_name, clip):
    """How strongly this skeleton's name says it belongs to this clip.

    0 best, 3 worst. Split out from rank_skeletons because callers that also
    care about bone count need the TIER, not a position in a sorted list.
    """
    stem = str(clip or "").rsplit(".", 1)[0].lower()
    head = stem.split("_")[0]
    base = str(skel_name).rsplit(".", 1)[0].lower()
    # An accessory rig is labelled "<character> :: <accessory>", so take the
    # character off the front of that too.
    base = base.split(" :: ")[0]
    if base == stem:
        return 0                      # the whole clip name is a skeleton
    if head and base == head:
        return 1                      # its character's own skeleton
    if head and base.startswith(head):
        return 2                      # a variant of that character
    return 3


def _feature_allows(route):
    """False if `route` belongs to a feature this build holds back.

    Fails CLOSED on any error: an unreadable manifest must not silently open
    a route that was meant to be absent.
    """
    want = _FEATURE_ROUTES.get(route)
    if want is None:
        return True
    try:
        from . import features
        if want == INTERNAL_ONLY:
            return features.build() == "internal"
        return features.enabled(want)
    except Exception:
        return False


# ---------------------------------------------------------------- HTTP layer

def names_swat_get(studio, skel_name):
    """A skeleton's bone names from SWAT.DAT (where every character's .skel is), or None."""
    if not skel_name:
        return None
    try:
        return studio.skeleton_names("SWAT.DAT").get(skel_name)
    except Exception:                                      # noqa: BLE001
        return None


def _build_waiting(studio):
    """What the top bar's Build button would do: queued rebuilds and Data Editor files waiting for a Build."""
    de = _data_editor(studio)
    if studio.game is None:
        # PCSX2 has the disc: counting reads it, and must not race a booting emulator -- the last count stands
        c = getattr(de, "_wait_cache", None)
        n, folders = c[1] if c else (0, [])
    else:
        try:
            n, folders = de.waiting()
        except Exception:                                  # noqa: BLE001
            n, folders = 0, []
    q = studio.pending.summary()
    return dict(queued=q, data_editor=n, folders=folders,
                running=de.job.get("state") == "running",
                any=bool(q) or n > 0)


def _data_editor(studio):
    """DISC > Data Editor (rdrstudio/dataeditor.py), made on first use."""
    de = getattr(studio, "_data_editor", None)
    if de is None:
        from . import dataeditor
        de = studio._data_editor = dataeditor.DataEditor(studio)
    return de


class Handler(BaseHTTPRequestHandler):
    server_version = "RDRStudio/0.1"
    # `serve()` injects the real Studio before the first request (see
    # `Handler.studio = studio` at the bottom of this file), so every route
    # body can rely on it. Declared as Studio rather than None so the checker
    # can read the 256 `s = self.studio` uses below; the value stays None at
    # class-definition time exactly as before.
    studio: "Studio" = None     # type: ignore[assignment]

    def log_message(self, fmt, *args):
        pass  # keep the console clean; the app has its own log panel

    # ------------------------------------------------------------- utilities

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # An edit this request queued for a rebuild (no room in its slot):
        # the page says so, and Play becomes "Rebuild and play".
        try:
            st = self.studio
            q = st.pending.take_new() if st is not None and st._pending is not None else []
            if q:
                self.send_header("X-RDR-Rebuild-Queued", ",".join(q))
        except Exception:                                  # noqa: BLE001
            pass
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8"))

    # The stream listing costs ~0.8 s cold (8,361 header reads plus the 256
    # sound-bank CSVs that carry the captions). Three routes want the same
    # rows, and the browsing UI hits all three, so it is cached on the Studio
    # -- keyed by disc path, because `s.disc` silently swaps to the pristine
    # ISO while PCSX2 holds the working one and the two are different discs.
    def _stream_rows(self):
        from . import streams as _st
        s = self.studio
        key = s.disc.path
        cached = getattr(s, "_stream_cache", None)
        if cached and cached[0] == key:
            return cached[1]
        rows = _st.list_streams(s.disc)
        s._stream_cache = (key, rows)
        return rows

    def _streams_summary(self):
        from . import streams as _st
        return _st.summary(self._stream_rows())

    def _static(self, path):
        rel = posixpath.normpath(urllib.parse.unquote(path)).lstrip("/")
        if rel in ("", "."):
            rel = "index.html"
        full = os.path.join(WEB_ROOT, rel.replace("/", os.sep))
        if not os.path.abspath(full).startswith(os.path.abspath(WEB_ROOT)):
            return self._send(403, {"error": "forbidden"})
        if not os.path.isfile(full):
            return self._send(404, {"error": "not found: %s" % rel})
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as f:
            self._send(200, f.read(), ctype)

    # ------------------------------------------------------------------- GET

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        q = urllib.parse.parse_qs(parsed.query)
        if not path.startswith("/api/"):
            return self._static(path)
        # Slow-request log, to stderr, permanently. A day was lost to "the app
        # does not load levels" because nothing recorded which route was
        # holding the one lock everything serialises on -- the answer had to
        # be reconstructed by curling routes one at a time from outside. Only
        # requests over a second are logged: the 3-second polls are the noise
        # this is protecting FROM, not the signal.
        t0 = time.perf_counter()
        try:
            # `disc_state` exists to REPORT ownership; it is polled every few
            # seconds and must never acquire the disc as a side effect. That
            # poll alone was enough to steal the ISO back from a booting PCSX2.
            if path[5:] not in ("config", "log", "disc_state", "warmup",
                                "settings", "profiles", "profile_get", "job", "anims_export_status",
                                "codemods", "codemods_status") and not path[5:].startswith("de_"):
                self.studio.ensure_open()
            return self._api_get(path[5:], q)
        except Exception as ex:
            import traceback
            traceback.print_exc()
            return self._send(500, {"error": "%s: %s" % (type(ex).__name__, ex)})
        finally:
            dt = time.perf_counter() - t0
            if dt > 1.0:
                print("[slow] GET %-28s %6.1fs" % (path[5:], dt),
                      file=sys.stderr, flush=True)

    def _api_get(self, route, q):
        s = self.studio
        one = lambda k, d=None: (q.get(k) or [d])[0]  # noqa: E731

        if not _feature_allows(route):
            return self._send(404, {"error": "no such route: %s" % route})

        if route.startswith("de_"):
            return self._de_get(route, q)

        if route == "config":
            return self._send(200, dict(
                vanilla=s.vanilla_path, modded=s.modded_path,
                pcsx2=s.pcsx2, emulog=s.emulog,
                modded_exists=os.path.exists(s.modded_path),
                pcsx2_exists=bool(s.pcsx2 and os.path.exists(s.pcsx2)),
            ))

        if route == "settings":
            from . import settings as settings_mod
            # `path` is reported so a caller can say WHERE the answers live
            # without recomputing it -- the server's environment is the only
            # one that knows, and a test driving this over HTTP may not be
            # running on the same OS as the Studio.
            return self._send(200, dict(settings=settings_mod.load(),
                                        defaults=settings_mod.DEFAULTS,
                                        path=settings_mod.path()))

        if route == "profiles":
            from . import settings as settings_mod
            return self._send(200, dict(profiles=settings_mod.profiles()))
        if route == "profile_get":
            from . import settings as settings_mod
            try:
                return self._send(200, dict(profile=settings_mod.get_profile(one("name", ""))))
            except ValueError as ex:
                return self._send(400, dict(error=str(ex)))

        if route == "characters":
            # Every *_lods.rsc on the disc: the cast, plus rigged props.
            out, seen = [], {}
            # SWAT.DAT last: a character added in the Studio lives only there.
            for arc in list_levels(s.disc) + ["SWAT.DAT"]:
                try:
                    d = s.disc.archive(arc)
                except Exception:
                    continue
                for e in d:
                    if e.ext != "rsc" or not e.name.endswith("_lods.rsc"):
                        continue
                    if e.name in seen:
                        continue
                    seen[e.name] = True
                    out.append(dict(file=e.name, stem=e.name[:-9],
                                    archive=arc, bytes=e.size_full))
            out.sort(key=lambda r: r["stem"])
            return self._send(200, dict(characters=out))

        if route == "character":
            from .rsc import Rsc
            from . import skeleton as skel, vif as vifmod
            arc, name = one("archive"), one("name")
            lod = int(one("lod", "0"))
            try:
                bundle = Rsc(s.disc.archive(arc).read(name), name)
            except Exception as ex:
                return self._send(404, dict(error=str(ex)))
            stem = name[:-9]
            picks = character_parts(bundle, s.disc.archive(arc), stem, lod)
            if not picks:
                return self._send(404, dict(error="%s has no models" % name))
            lods = getattr(picks, "body_count", 1)

            # Bone-local vertices mean nothing on their own; the paired .skel
            # puts them back together into a standing figure.
            bones, bworld, head = [], [], None
            for src in ("SWAT.DAT", arc):
                try:
                    _, _, parsed, _ = skel.parse(
                        s.disc.archive(src).read(stem + ".skel"))
                except Exception:
                    continue
                world = skel.world_positions(parsed)
                bones = [dict(name=b.name, pos=world[b.name]) for b in parsed]
                bworld = [world[b.name] for b in parsed]
                head = head_bone(parsed, world)
                break
            model = merge_models(bundle, picks, bworld, head)
            if not model:
                return self._send(404, dict(error="no part of %s decoded" % name))
            verts = model["verts"]

            flat: list[float] = []
            for v in verts:
                flat.extend(round(c, 4) for c in v)
            idx = []
            for t in model["tris"]:
                idx.extend(t)
            uvs: list[float] = []
            for uv in model["uvs"][:len(verts)]:
                uvs.extend(round(c, 5) for c in uv)
            table = bundle.texture_table()
            return self._send(200, dict(
                name=name, stem=stem, archive=arc, lod=lod, lods=lods,
                positions=flat, indices=idx,
                uvs=uvs if len(uvs) * 3 == len(flat) * 2 else [],
                verts=len(verts), tris=len(model["tris"]),
                skinned=bool(model.get("skinned")),
                bone_count=model.get("bone_count", 0),
                parts=model.get("parts", []),
                # [texture index, first triangle, triangle count]: which part of
                # the mesh each of the bundle's textures covers (a custom model
                # often has one texture per piece of clothing)
                groups=[[g[0], g[1], g[2]] for g in model.get("groups", [])],
                bones=bones, textures=len(table)))

        if route == "character_rigs":
            # Which character models could be driven by a clip that needs
            # `bones` bones. The bone count is the ONLY link -- a model's
            # per-vertex bone is an index into its skeleton's file order, so
            # feeding it a skeleton of a different length reads bones that
            # are not there. Byte 1 of a MOD0 states the rig size (see
            # Rsc.bone_count), which is what this filters on.
            from .rsc import Rsc
            arc = one("archive") or "SWAT.DAT"
            try:
                want_bones = int(one("bones", "0"))
            except ValueError:
                want_bones = 0
            try:
                rows = s.character_rig_index(arc)
            except Exception:
                # An archive with no character bundles is a normal answer, not
                # a fault -- most level archives hold none, and the caller then
                # falls back to SWAT.DAT.
                rows = []
            # Exact rig size first, then models rigged to MORE bones than the
            # clip drives. The surplus joints simply stay at bind pose, and
            # that is safe here for the same reason it is safe for skeletons:
            # the canonical rigs nest, so bone i is the same joint in both.
            #
            # This is the answer to "why do most animations only have a few
            # character models". Measured on the vanilla disc: 2,601 of 4,187
            # clips drive 53 bones, and only 12 of the 306 character bundles
            # in SWAT.DAT are rigged to exactly 53 -- while 140 more are
            # rigged to 52, which is the same rig minus one trailing bone.
            # Strict equality hid all 140.
            #
            # A model rigged to FEWER bones than the clip drives is NOT
            # offered: its per-vertex bone index would run off the end of its
            # own skeleton, which is a torn mesh rather than a stiff one.
            if not want_bones:
                out = list(rows)
            else:
                prefix = s.canonical_prefix(arc, want_bones)
                names = s.skeleton_names(arc)

                def agrees(r):
                    """Does this model's own rig mean the same thing this
                    clip does, over the bones they share?"""
                    if not prefix:
                        return False
                    stem = (r.get("stem") or r.get("name") or "").split("_lods")[0]
                    got = names.get(stem + ".skel")
                    if not got:
                        return False
                    ov = min(len(got), want_bones)
                    return got[:ov] == prefix[:ov]

                exact, near = [], []
                for r in rows:
                    c = r["bone_count"]
                    if c == want_bones:
                        r["fit"] = "exact"
                        exact.append(r)
                    elif agrees(r):
                        r["fit"] = ("%d extra bones, held at rest" % (c - want_bones)
                                    if c > want_bones else
                                    "%d fewer bones; those channels are ignored"
                                    % (want_bones - c))
                        near.append((abs(c - want_bones), r))
                out = exact + [r for _, r in sorted(near, key=lambda t: t[0])]
                if one("all"):
                    # Every other character too, retargeted by BONE NAME onto
                    # the clip: its own skeleton, each bone driven by the clip's
                    # bone of the same name (exact, then ignoring _ and case),
                    # the rest left at rest. Best first.
                    def _norm(x):
                        return re.sub(r"[^a-z0-9]", "", x.lower())
                    clip = list(names.get(one("skel") or "", ()) or names_swat_get(s, one("skel")) or ())
                    cx = {n.lower() for n in clip}
                    cn = {_norm(n) for n in clip}
                    have_ids = {id(r) for r in out}
                    extra = []
                    # a bundle with no .skel of its own: the skeleton its character's .cr names (FileNameSkel)
                    render_skel = {}
                    try:
                        from . import modelswap as _msw
                        for _cr, _v in _msw.facts_for(s.disc, s.vanilla).stock.items():
                            render_skel.setdefault(_v["render"], _v["skel"])
                    except Exception:                      # noqa: BLE001
                        pass
                    for r in rows:
                        if id(r) in have_ids:
                            continue
                        stem = (r.get("stem") or "").split("_lods")[0]
                        skel_file = stem + ".skel"
                        own = names.get(skel_file) or names_swat_get(s, skel_file)
                        if not own and render_skel.get(stem):
                            skel_file = render_skel[stem] + ".skel"
                            own = names.get(skel_file) or names_swat_get(s, skel_file)
                        r["skel"] = skel_file
                        if not own and clip and 0 < (r.get("bone_count") or 0) <= len(clip):
                            # no skeleton of its own anywhere: rigged in the standard order, so posed on the
                            # clip's own skeleton by index (its bones are the clip's first ones)
                            r["fit"] = "no skeleton of its own: posed on this clip's"
                            r["matched"] = r["bone_count"]
                            extra.append(r)
                            continue
                        if not own or not clip:
                            continue
                        m = sum(1 for n in own if n.lower() in cx or _norm(n) in cn)
                        if not m:
                            continue
                        r["fit"] = "%d of %d bones matched by name" % (m, len(own))
                        r["by_name"] = True
                        r["matched"] = m
                        extra.append(r)
                    extra.sort(key=lambda r: (-r["matched"] / max(1, r["bone_count"] or 1), r["stem"]))
                    out = out + extra
            return self._send(200, dict(archive=arc, bones=want_bones,
                                        models=out, total=len(rows),
                                        exact=sum(1 for r in out
                                                  if r.get("fit") == "exact")))

        if route == "character_rig":
            # The same character the Creatures tab draws, plus what it takes
            # to MOVE it: per-vertex bone influences, the bind pose those
            # influences are relative to, and the exact per-surface texture
            # slots. The Creatures tab does not need any of that, so it is a
            # separate route rather than more weight on `character`.
            #
            # The bind pose in this engine is translation-only -- a bone's
            # bind transform is just its accumulated offset
            # (skeleton.world_positions), with no rotation anywhere -- so a
            # client can skin with
            #     v' = sum_j w_j * M_j * (v_bind - bind_j)
            # and nothing else is needed. That is why only bind POSITIONS go
            # over the wire, not bind matrices.
            from .rsc import Rsc
            from . import skeleton as skel
            arc, name = one("archive"), one("name")
            lod = int(one("lod", "0"))
            try:
                bundle = Rsc(s.disc.archive(arc).read(name), name)
            except Exception as ex:
                return self._send(404, dict(error=str(ex)))
            stem = name[:-9] if name.endswith("_lods.rsc") else name
            picks = character_parts(bundle, s.disc.archive(arc), stem, lod)
            if not picks:
                return self._send(404, dict(error="%s has no models" % name))
            lods = getattr(picks, "body_count", 1)

            # WHICH skeleton to bind-pose against is a parameter, not the
            # model's own file. A clip is posed on one specific skeleton and
            # the mesh has to be bound to THAT one or the two disagree at
            # every joint -- and 81 of the 306 character bundles have no
            # <stem>.skel of their own at all, so insisting on it would
            # refuse a quarter of the cast. Any skeleton of the right length
            # is legitimate: the per-vertex bone is an index into file order.
            want = one("skel") or (stem + ".skel")
            order = [a for a in (one("skel_archive"), "SWAT.DAT", arc) if a]
            bones, bworld, head, hidx, skel_src = [], [], None, None, None
            for src in order:
                try:
                    _, _, parsed, _ = skel.parse(
                        s.disc.archive(src).read(want))
                except Exception:
                    continue
                world = skel.world_positions(parsed)
                at = {b.name: i for i, b in enumerate(parsed)}

                def _pi(b):
                    pn = b.parent if isinstance(b.parent, str) or b.parent is None else getattr(b.parent, "name", None)
                    return at.get(pn, -1)
                bones = [dict(name=b.name, pos=world[b.name], parent=_pi(b)) for b in parsed]
                bworld = [world[b.name] for b in parsed]
                head = head_bone(parsed, world)
                hidx = head_bone_index(parsed, world)
                skel_src = src
                break
            if not bones:
                return self._send(404, dict(
                    error="no %s on the disc, so there is no rig to animate "
                          "this model on" % want))
            rig = max((bundle.bone_count(i) for i, _ in picks), default=0)
            near = False
            if rig and rig < len(bones):
                # A model rigged to FEWER bones than the clip's skeleton, whose own
                # skeleton names the same joints in the same order over the bones
                # it has (52 of a 53-bone rig, or a custom character's own .skel):
                # the same rule the model list offers it by. Its vertices name
                # bones 0..rig-1, which are those same joints here; the clip's
                # surplus bones simply move nothing.
                own = None
                for src in (arc, "SWAT.DAT"):
                    own = s.skeleton_names(src).get(stem + ".skel")
                    if own:
                        break
                names = [b["name"] for b in bones]
                near = (bool(own) and len(own) == rig and tuple(own) == tuple(names[:rig])) or not own
            if rig and len(bones) != rig and not near:
                return self._send(400, dict(
                    error="%s is rigged to %d bones and %s has %d. A vertex "
                          "names its bone by index into the skeleton's file "
                          "order, so the two have to be the same length."
                          % (name, rig, want, len(bones))))

            model = merge_models(bundle, picks, bworld, head, hidx)
            if not model:
                return self._send(404, dict(error="no part of %s decoded" % name))
            verts = model["verts"]
            nb = len(bones)

            flat = []
            for v in verts:
                flat.extend(round(c, 4) for c in v)
            idx = []
            for t in model["tris"]:
                idx.extend(t)
            uvs = []
            for uv in model["uvs"][:len(verts)]:
                uvs.extend(round(c, 5) for c in uv)

            # Four influences per vertex is what a GPU skinning shader takes.
            # Measured over all 306 bundles that decode to a model (570,435
            # vertices at LOD 0): 380,220 name one bone, 83,286 two, 22,076
            # three, and none more than three -- so nothing is being dropped
            # here. Say so when it does happen rather than quietly
            # renormalising behind the reader's back.
            si, sw, over, orphan, rigid = [], [], 0, 0, 0
            for e in model.get("influences") or []:
                e = [(b, w) for b, w in e if b is not None and 0 <= b < nb]
                if not e:
                    # No bone at all: pin to the root so the vertex travels
                    # with the figure instead of collapsing onto the origin,
                    # which is what all-zero weights produce in the shader.
                    e = [(0, 1.0)]
                    orphan += 1
                if len(e) > 4:
                    over += 1
                    e = sorted(e, key=lambda t: -t[1])[:4]
                tot = sum(w for _, w in e) or 1.0
                for j in range(4):
                    si.append(e[j][0] if j < len(e) else 0)
                    sw.append(round(e[j][1] / tot, 5) if j < len(e) else 0.0)
            for p in model.get("parts", []):
                if p.get("attach") != "skin":
                    rigid += p["verts"]

            table = bundle.texture_table()
            groups: list[dict[str, Any]] = []
            for slot, start, count in model.get("groups") or ():
                if groups and groups[-1]["texture"] == slot \
                        and groups[-1]["start"] + groups[-1]["count"] == start * 3:
                    groups[-1]["count"] += count * 3
                else:
                    groups.append(dict(start=start * 3, count=count * 3,
                                       texture=slot))
            used = sorted({g["texture"] for g in groups
                           if g["texture"] is not None})
            return self._send(200, dict(
                name=name, stem=stem, archive=arc, lod=lod, lods=lods,
                skel=want, skel_archive=skel_src,
                own_skel=(want.lower() == (stem + ".skel").lower()),
                positions=flat, indices=idx,
                uvs=uvs if len(uvs) * 3 == len(flat) * 2 else [],
                skin_index=si, skin_weight=sw,
                verts=len(verts), tris=len(model["tris"]),
                skinned=bool(model.get("skinned")),
                bone_count=model.get("bone_count", 0),
                parts=model.get("parts", []),
                rigid_verts=rigid, orphan_verts=orphan, over_four=over,
                bones=bones, groups=groups,
                textures=[dict(i=i, name=table[i][1]["name"],
                               width=table[i][1]["width"],
                               height=table[i][1]["height"])
                          for i in used if 0 <= i < len(table)]))

        if route == "level_characters":
            # The cast actually present in a level. An enemy node's `Type` is
            # the three-letter stem of a *_lods.rsc in the same archive, so a
            # mission can be shown populated with its real actors instead of
            # a field of identical capsules.
            from .rsc import Rsc
            from . import skeleton as skel, vif as vifmod
            arc = one("archive")
            lod = int(one("lod", "1"))
            lvl = s.level(arc, area=one("area") or None)
            wanted: dict[str, int] = {}
            for n in lvl.nodes:
                t = n.props.get("Type")
                if n.kind == "enemy" and isinstance(t, str) and t.strip():
                    wanted.setdefault(t.strip().lower(), 0)
                    wanted[t.strip().lower()] += 1

            dave = s.disc.archive(arc)
            names = {e.name for e in dave}

            # The player, for the spawn markers. levelflow.csv says who you
            # play each mission as -- mission 1 is `mrd` (Red), most of the
            # rest `srd`. Without this a spawn is an anonymous cone in a level
            # full of named actors.
            player = None
            try:
                from . import campaign as camp
                stem = arc.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
                for m in camp.summary(s.disc)["missions"]:
                    lvl_name = (m.get("Level") or "").lower().replace("lvl_", "")
                    if not lvl_name:
                        continue
                    if stem.startswith(lvl_name[:8]) or lvl_name.startswith(stem[:8]):
                        who = (m.get("Character") or "").strip().lower()
                        if who and (who + "_lods.rsc") in names:
                            player = who
                            break
            except Exception:
                pass
            if player:
                wanted.setdefault(player, 0)

            out = []
            for stem, count in sorted(wanted.items()):
                file = stem + "_lods.rsc"
                if file not in names:
                    out.append(dict(type=stem, count=count, model=None,
                                    why="no %s in this archive" % file))
                    continue
                try:
                    bundle = Rsc(dave.read(file), file)
                    picks = character_parts(bundle, dave, stem, lod)
                    bworld, head = [], None
                    for src in ("SWAT.DAT", arc):
                        try:
                            _, _, parsed, _ = skel.parse(
                                s.disc.archive(src).read(stem + ".skel"))
                        except Exception:
                            continue
                        world = skel.world_positions(parsed)
                        bworld = [world[b.name] for b in parsed]
                        head = head_bone(parsed, world)
                        break
                    model = merge_models(bundle, picks, bworld, head)
                except Exception as ex:
                    out.append(dict(type=stem, count=count, model=None,
                                    why=str(ex)))
                    continue
                if not model:
                    out.append(dict(type=stem, count=count, model=None,
                                    why="no part of %s decoded" % file))
                    continue

                verts = model["verts"]      # merge_models already posed them

                flat = []
                for v in verts:
                    flat.extend(round(c, 4) for c in v)
                idx = []
                for t in model["tris"]:
                    idx.extend(t)
                uvs = []
                for uv in model["uvs"][:len(verts)]:
                    uvs.extend(round(c, 5) for c in uv)
                out.append(dict(
                    type=stem, count=count, file=file, lod=lod,
                    parts=[p["name"] for p in model.get("parts", [])],
                    model=dict(positions=flat, indices=idx,
                               uvs=uvs if len(uvs) * 3 == len(flat) * 2 else [],
                               groups=[[g[0], g[1], g[2]] for g in model.get("groups", [])],
                               verts=len(verts), tris=len(model["tris"]))))
            return self._send(200, dict(archive=arc, characters=out,
                                        player=player))

        if route == "character.png":
            # A texture out of an arbitrary bundle. The level texture route
            # resolves the LEVEL's rsc, which is the wrong bundle entirely for
            # a character living in the same archive.
            from .rsc import Rsc
            from . import texture as T
            try:
                bundle = Rsc(s.disc.archive(one("archive")).read(one("name")),
                             one("name"))
            except Exception as ex:
                return self._send(404, dict(error=str(ex)))
            table = bundle.texture_table()
            i = int(one("i", "0"))
            if not (0 <= i < len(table)):
                return self._send(404, dict(error="no texture %d" % i))
            _, info, mip, pal = table[i]
            res = T.decode(bundle.raw(mip.index),
                           T.parse_palette(bundle.raw(pal.index)))
            if not res:
                return self._send(404, dict(error="texture %d did not decode" % i))
            w, h, rgba = res
            return self._send(200, T.encode_png(w, h, rgba), "image/png")

        if route == "disc_state":
            # Whether the working ISO is grabbable right now, WITHOUT taking
            # it. Polled by the header so "why won't the game open" is visible
            # instead of something you have to already understand.
            #
            # NOTE this route must not call ensure_open(). It is polled every
            # few seconds, and reopening here is what used to snatch the disc
            # back out from under a booting PCSX2.
            held = s.game is not None
            free = False if held else s.free_for_emulator()
            # a build writing the working disc right now (Play waits for it: PCSX2 would read a half-written disc)
            building = None
            de = getattr(s, "_data_editor", None)
            if de is not None and de.job.get("state") == "running":
                building = de.job.get("label") or "Data Editor build"
            elif (getattr(s, "_rebuild_job", None) or {}).get("state") == "running":
                building = "Rebuilding .dat files"
            else:
                try:
                    from . import codemods as _cm
                    if _cm.job_status().get("state") == "running":
                        building = "Code Mods build"
                except Exception:                               # noqa: BLE001
                    pass
            return self._send(200, dict(
                studio_holds=held, free_for_emulator=(not held) and free,
                locked_by_other=(not held) and not free,
                handed_over=bool(getattr(s, "_handed_over", False)),
                pending_rebuild=s.pending.summary(),
                rebuild_job=getattr(s, "_rebuild_job", None),
                build_waiting=_build_waiting(s), building=building))

        if route in ("codemods", "codemods_status"):
            # Code Mods (the original research's noclip/freeze/camera-lock hooks). READ-ONLY: looks at the stock ISO,
            # the working ISO and the RDR_CODEMODS.iso output without taking the disc from anyone.
            from . import codemods
            if route == "codemods_status":
                return self._send(200, codemods.job_status())
            return self._send(200, codemods.describe(
                s.vanilla_path, s.modded_path,
                os.path.join(os.path.dirname(s.modded_path), "RDR_CODEMODS.iso")))

        if route == "levels":
            # Levels carry which part of the game they belong to: a campaign
            # mission number, a Showdown 2P/4P variant, or unused by the
            # mission list. On disc the three sit intermixed.
            from . import modes
            try:
                cat = modes.catalog(s.disc, stock=s.vanilla)
                cat += s.mods_levels({e["archive"].upper() for e in cat})
                # the game's levels in their groups again (Coliseum among the Showdown maps)
                order = {"campaign": 0, "bonus": 1, "showdown2": 2, "showdown4": 3, "frontend": 4, "custom": 5}
                cat.sort(key=lambda e: (order.get(e["mode"], 9),
                                        e["missions"][0]["number"] if e.get("missions") else 99, e["name"]))
                return self._send(200, cat)
            except Exception:
                out = []
                for a in list_levels(s.disc):
                    out.append(dict(archive=a, name=a.rsplit("/", 1)[-1][:-4],
                                    group=a.split("/")[0], mode="campaign",
                                    mode_label="Campaign", missions=[],
                                    base=a.rsplit("/", 1)[-1][:-4]))
                return self._send(200, out)

        if route == "modes":
            from . import modes
            return self._send(200, modes.summary(s.disc))

        if route == "hidden":
            # Cut and dev content left on the retail disc. SWAT.DAT carries
            # .lvl manifests that no shipped archive owns -- dated backups,
            # "-borked" versions, demo-era layouts -- and 100+ enemy rosters
            # for levels that never shipped (arena, bank, barbershop...).
            import re as _re
            d = s.disc.archive("SWAT.DAT")
            names = {e.name for e in d}
            shipped_stems = set()
            for a in list_levels(s.disc):
                shipped_stems.add(a.rsplit("/", 1)[-1][:-4].lower())

            manifests = []
            for e in d:
                if e.ext != "lvl":
                    continue
                stem = e.name[:-4].replace("lvl_", "")
                refs = []
                try:
                    text = d.read(e).decode("latin-1")
                    refs = [t.strip() for t in
                            text.replace("{", " ").replace("}", " ").split()
                            if "." in t]
                except Exception:
                    pass
                present = sum(1 for r in refs if r.lower() in names)
                manifests.append(dict(
                    manifest=e.name, stem=stem, refs=len(refs),
                    present=present, loadable=present > 0,
                    shipped=stem in shipped_stems or stem[:8] in shipped_stems))

            rosters = []
            for e in d:
                if e.ext != "enm":
                    continue
                m = _re.match(r"(?:copy of )?lvl_([a-z0-9_\-\.]+?)(?:_var_\d+)?\.enm$",
                              e.name)
                if not m:
                    continue
                stem = m.group(1)
                base = _re.sub(r"\d+$", "", stem)
                if stem in shipped_stems or base in shipped_stems or \
                        stem[:8] in shipped_stems:
                    continue
                rosters.append(dict(file=e.name, stem=stem, size=e.size_full))

            curiosities = [n for n in sorted(names)
                           if "konami" in n or n.startswith("copy of ")
                           or "borked" in n or "demo" in n]
            return self._send(200, dict(
                manifests=sorted(manifests, key=lambda m: (m["shipped"], m["manifest"])),
                rosters=sorted(rosters, key=lambda r: r["file"]),
                curiosities=curiosities))

        if route == "archives":
            # `headroom` is what you could reclaim by repacking, and
            # `repack_status` says whether repacking this archive is known to
            # be safe. They belong together: headroom you must not take is not
            # headroom. See rdrstudio/dave.py for how the three states are
            # decided -- each "verified" was earned by playing the game past
            # its title screen, and "untested" stays its own answer.
            from .dave import repack_status
            out = []
            for a in s.disc.archives():
                d = s.disc.archive(a)
                try:
                    _, gap = d.largest_gap()
                except Exception:
                    gap = None
                out.append(dict(archive=a, magic=d.magic, count=d.count,
                                size=d.size, largest_gap=gap,
                                repack_status=repack_status(a)))
            # The cache is NOT dropped here any more.
            #
            # This used to `s.disc._cache.pop(a.upper(), None)` after each
            # archive, presumably to bound memory while walking all 66. The
            # cost was invisible until measured: it re-parses every archive
            # from a 2.2 GB ISO on every call AND leaves the cache empty for
            # whatever runs next, so the route was slow and it made the next
            # route slow too. Cold, this call ran over 180 seconds and the
            # `levels` request behind it took another 20.
            #
            # Because every disc-touching route serialises on one lock and the
            # front end polls every 3 seconds, that turned into a backlog the
            # server could not drain: the window opened with an empty level
            # list and stayed there. Reported as "not loading any levels" --
            # it was loading them, behind three minutes of work.
            #
            # Keeping the parsed TOCs costs tens of MB against a tool that
            # already holds decoded level geometry, and every other route was
            # already relying on that cache existing.
            return self._send(200, out)

        if route == "level":
            arc = one("archive")
            lvl = s.level(arc, reload=one("reload") == "1",
                          manifest=one("manifest") or None,
                          area=one("area") or None)
            out = lvl.to_json()
            # An archive can hold several playable areas -- Brimstone's is
            # the town plus six interiors -- so the viewport needs to know
            # which one it is showing and what else it could show.
            from .rsc import area_stem
            out["area"] = lvl.area
            out["areas"] = [dict(rsc=a, name=area_stem(a))
                            for a in lvl.areas()]
            return self._send(200, out)

        if route in ("assets", "assets_summary"):
            index = s.asset_index(rebuild=one("rebuild") == "1")
            # One parser for both routes. If the list and the counts beside
            # it read the query differently they drift, and a category with
            # "412" next to it opens on an empty table.
            filters = dict(
                query=one("q", ""), category=one("category") or None,
                archive=one("archive") or None, area=one("area") or None,
                form=one("form") or None, reach=one("reach") or None,
                size=one("size") or None, flag=one("flag") or None,
                shared_only=one("shared") == "1")
            if route == "assets":
                return self._send(200, index.search(
                    limit=int(one("limit", "400")), **filters))
            out = index.summary(**filters)
            # The counts for the facet the user is already standing in must
            # ignore that facet, or every row but the chosen one reads zero
            # and there is no way back out of a filter.
            for axis in ("category", "form", "reach", "size"):
                if filters.get(axis):
                    out["facets"][axis] = index.summary(
                        exclude=axis, **filters)["facets"][axis]
            # Flags are the one axis you can pick several of, so "how many
            # rows does this row give me" is a different sum -- dropping the
            # axis wholesale advertised counts no click could produce. See
            # AssetIndex.flag_facet. With nothing picked it returns exactly
            # what the plain tally did, so this is safe to run every time.
            out["facets"]["flag"] = index.flag_facet(**filters)
            out["categories"] = out["facets"]["category"]
            return self._send(200, out)

        if route == "asset":
            index = s.asset_index()
            name = one("name", "")
            arc = one("archive") or None
            if arc:
                return self._send(200, index.placements_for(
                    name, arc, one("area") or None))
            rec = index.asset(name)
            if rec is None:
                return self._send(404, dict(error="no asset named %r" % name))
            return self._send(200, rec)

        if route == "campaign":
            from . import campaign
            data = campaign.summary(s.disc)
            # Link each mission to the level archive it loads, so the
            # campaign view can open it in 3D.
            levels = {a.rsplit("/", 1)[-1][:-4].lower(): a
                      for a in list_levels(s.disc)}
            for m in data["missions"]:
                key = m["Level"].lower()
                if key.startswith("lvl_"):
                    key = key[4:]
                arc = levels.get(key)
                if arc is None:
                    # Archive names are truncated on disc (lvl_traingood ->
                    # TRAINGOO.DAT), so fall back to the longest prefix match.
                    cands = [(len(k), a) for k, a in levels.items()
                             if key.startswith(k) or k.startswith(key)]
                    if cands:
                        arc = max(cands)[1]
                m["archive"] = arc
            return self._send(200, data)

        if route == "creatures":
            from . import creature
            return self._send(200, dict(
                creatures=creature.list_creatures(s.disc),
                groups=creature.FIELD_GROUPS))

        if route == "creature":
            from . import creature, swt as swtmod
            name = one("name")
            data = s.disc.read("SWAT.DAT", name)
            try:
                stock = creature.read_fields(s.vanilla.read("SWAT.DAT", name))
            except Exception:                              # noqa: BLE001
                # A character added in the Studio (Add Character) is not on
                # the unedited disc: its own values are its stock.
                stock = creature.read_fields(data)

            # The combat sheet lives in a SECOND file. `.cr` holds health and
            # movement; every damage number is in `<stem>.swt`, and neither
            # file has the other's fields -- .swt has no hit points at all.
            # Returning them together is what lets one panel answer "how tough
            # is this character" instead of sending the user to the Files tab
            # to find a name they would have to already know.
            combat = combat_stock = None
            stem = name.rsplit(".", 1)[0] + ".swt"
            if s.disc.locate(stem):
                try:
                    combat = swtmod.read(s.disc.read("SWAT.DAT", stem))
                    try:
                        combat_stock = swtmod.summary(
                            s.vanilla.read("SWAT.DAT", stem))
                    except Exception:                      # noqa: BLE001
                        combat_stock = swtmod.summary(s.disc.read("SWAT.DAT", stem))
                except Exception:                          # noqa: BLE001
                    # A character whose .swt will not parse still has a
                    # perfectly good .cr, and the panel is more useful with
                    # half the data than with an error page.
                    combat = combat_stock = None
            return self._send(200, dict(
                name=name, fields=creature.read_fields(data), stock=stock,
                copies=len(s.disc.locate(name)),
                combat_file=stem if combat else None,
                combat=combat, combat_stock=combat_stock,
                combat_copies=len(s.disc.locate(stem)) if combat else 0))

        if route == "guns":
            from . import guns
            data = s.disc.read("SWAT.DAT", "swat.guns")
            return self._send(200, dict(
                weapons=guns.summary(data), groups=guns.FIELD_GROUPS,
                # Enum fields get a dropdown of the values the disc uses
                # instead of a free number box. See guns.CHOICES.
                choices=guns.CHOICES,
                copies=len(s.disc.locate("swat.guns"))))

        if route == "gun":
            from . import guns
            data = s.disc.read("SWAT.DAT", "swat.guns")
            w = guns.find(data, one("name"))
            if w is None:
                return self._send(404, {"error": "no weapon named %r" % one("name")})
            vanilla = guns.find(s.vanilla.read("SWAT.DAT", "swat.guns"), one("name"))
            return self._send(200, dict(
                weapon=w.to_json(full=True),
                stock={k: v["value"] for k, v in vanilla.fields.items()} if vanilla else {},
                editable=sorted(guns.EDITABLE)))

        if route == "movies":
            # The FMV cutscenes. They sit in PSS/ on the ISO itself, not in a
            # Dave archive, so they are read straight off the disc image.
            from . import pss
            want = one("name")
            files = [f for f in s.disc.iso.files()
                     if f.path.upper().endswith(".PSS")]
            files.sort(key=lambda f: f.path)
            if not want:
                return self._send(200, dict(movies=[
                    dict(path=f.path, name=f.path.split("/")[-1],
                         bytes=f.size) for f in files]))
            hit = next((f for f in files
                        if f.path.split("/")[-1].lower() == want.lower()), None)
            if not hit:
                return self._send(404, {"error": "no movie %r" % want})
            # Scan the WHOLE file: the duration comes from the last pack's
            # SCR, so a truncated read reports a truncated running time.
            data = s.disc.iso.read_at(hit.lba * 2048, hit.size)
            try:
                info = pss.scan(data)
            except ValueError as ex:
                return self._send(200, dict(ok=False, name=want, error=str(ex)))
            size = pss.video_size(data)
            return self._send(200, dict(
                ok=True, name=hit.path.split("/")[-1], path=hit.path,
                bytes=hit.size, packs=info["packs"], seconds=info["seconds"],
                mux_rate_bps=info["mux_rate_bps"], streams=info["streams"],
                width=size[0] if size else None,
                height=size[1] if size else None))

        if route == "movie_export":
            # Handed out with an .mpg extension because that is what it is --
            # an MPEG-2 Program Stream. Most players open it directly; the
            # audio is Sony's private-stream ADPCM, so some will show video
            # only, which is the file's nature and not a fault in the export.
            want = one("name") or ""
            hit = next((f for f in s.disc.iso.files()
                        if f.path.upper().endswith(".PSS")
                        and f.path.split("/")[-1].lower() == want.lower()), None)
            if not hit:
                return self._send(404, {"error": "no movie %r" % want})
            data = s.disc.iso.read_at(hit.lba * 2048, hit.size)
            self.send_response(200)
            self.send_header("Content-Type", "video/mpeg")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition",
                             'attachment; filename="%s.mpg"'
                             % want.rsplit(".", 1)[0])
            self.end_headers()
            self.wfile.write(data)
            return

        if route == "stills":
            # The `.ipu` full-screen images: menus, logos, loading screens.
            # 251 entries but only 63 distinct files -- the same three are
            # copied into ~62 level archives -- so the listing deduplicates by
            # name and counts the copies rather than repeating them.
            from . import ipu
            rows = []
            rows_by_name: dict[str, Any] = {}
            for arc in s.disc.archives():
                try:
                    toc = s.disc.toc(arc)
                except Exception:
                    continue
                for e in toc:
                    if e.ext.lower() != "ipu":
                        continue
                    if e.name in rows_by_name:
                        rows_by_name[e.name]["copies"] += 1
                        continue
                    try:
                        data = s.disc.read(arc, e.name)
                    except Exception as ex:
                        row = dict(name=e.name, archive=arc, ok=False,
                                   error=str(ex), copies=1, bytes=0)
                    else:
                        row = ipu.describe(e.name, data)
                        row.update(archive=arc, copies=1)
                    rows_by_name[e.name] = row
                    rows.append(row)
            rows.sort(key=lambda r: r["name"])
            return self._send(200, dict(
                stills=rows, distinct=len(rows),
                total_entries=sum(r["copies"] for r in rows),
                # Stated in the API response, not only in the UI, so a caller
                # that skips the UI learns where the pictures come from. This
                # said "pixels are NOT decoded" long after ipudec.py landed;
                # still_png had been serving decoded pictures the whole time.
                renderable=True,
                note="Dimensions are read from each file's header. The "
                     "bitstream is MPEG-2 intra data with no sequence or "
                     "picture header -- the game programs those parameters "
                     "into the PS2 IPU itself -- so still_png decodes it with "
                     "the parameters recovered from the game's IPU driver "
                     "(rdrstudio/ipudec.py). still_export hands out the exact "
                     "bytes."))

        if route == "still_png":
            # The decoded picture, as a PNG the browser can show. The decoder
            # is rdrstudio/ipudec.py; every parameter in it was recovered from
            # the game's own IPU driver rather than searched for.
            from . import ipu, ipudec
            want = one("name") or ""
            arc = one("archive") or ""
            hit = None
            for a in ([arc] if arc else s.disc.archives()):
                try:
                    toc = s.disc.toc(a)
                except Exception:
                    continue
                for e in toc:
                    if e.name.lower() == want.lower():
                        hit = (a, e)
                        break
                if hit:
                    break
            if not hit:
                return self._send(404, {"error": "no still %r" % want})
            data = s.disc.read(hit[0], hit[1].name)
            try:
                w, h, rgb = ipudec.decode(data)
            except Exception as ex:
                return self._send(200, dict(ok=False, name=want,
                                            error="%s: %s" % (type(ex).__name__, ex)))
            try:
                import io as _io
                from PIL import Image
            except ImportError:
                return self._send(500, {"error": "Pillow is not installed"})
            buf = _io.BytesIO()
            Image.frombytes("RGB", (w, h), bytes(rgb)).save(buf, "PNG")
            png = buf.getvalue()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(png)))
            self.end_headers()
            self.wfile.write(png)
            return

        if route == "still_export":
            # The exact bytes, container and all. Nothing is transcoded,
            # because nothing here can decode them yet -- handing out a file
            # that claimed to be a PNG would be a lie.
            from . import ipu
            want = one("name") or ""
            arc = one("archive") or ""
            hit = None
            for a in ([arc] if arc else s.disc.archives()):
                try:
                    toc = s.disc.toc(a)
                except Exception:
                    continue
                for e in toc:
                    if e.name.lower() == want.lower():
                        hit = (a, e)
                        break
                if hit:
                    break
            if not hit:
                return self._send(404, {"error": "no still %r" % want})
            try:
                data = s.disc.read(hit[0], hit[1].name)
            except Exception as ex:
                return self._send(404, {"error": str(ex)})
            if not ipu.is_ipu(data):
                return self._send(400, {"error": "%s is not an ipum file"
                                                 % want})
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition",
                             'attachment; filename="%s"' % want)
            self.end_headers()
            self.wfile.write(data)
            return

        if route == "sounds":
            # Every sound in a `.bd` bank, taken from the `.hd` region table.
            #
            # This used to say "split on the ADPCM END flag", and it was still
            # saying it after the code below had moved to the `.hd` -- the same
            # stale sentence that also sat in app.js and cost an earlier pass a
            # session, because the two index spaces are not the same list and
            # the prose named the wrong one. Measured: 154 of the 155 banks
            # disagree between them (snd_00 is 30 by the `.hd` and 58 by the
            # flag), because 3,432 of the 3,486 regions close with a one-block
            # END|LOOP|LOOP_START terminator the flag counts as its own sound.
            # The flag survives only as the fallback inside sounds_with_rates,
            # for the 1 bank whose header does not parse.
            from . import vag
            arc = one("archive") or "BANKS.DAT"
            name = one("name")
            if not name:
                banks = sorted(e.name for e in s.disc.archive(arc)
                               if e.name.lower().endswith(".bd"))
                return self._send(200, dict(archive=arc, banks=banks))
            try:
                data = s.disc.read(arc, name)
            except Exception as ex:
                return self._send(404, {"error": str(ex)})
            gate = vag.looks_like_adpcm(data)
            if gate < 0.99:
                return self._send(200, dict(
                    ok=False, name=name, gate=round(gate, 4),
                    error="this is not SPU-ADPCM (scores %.3f, needs 0.99)"
                          % gate))
            # Prefer the `.hd`: it states each sound's offset AND its sample
            # rate, which the body cannot. Rates really do vary inside one
            # bank -- snd_00 alone carries 12000 through 24000 -- so playing
            # everything at one rate is audibly wrong.
            hd = None
            try:
                hd = s.disc.read(arc, name[:-3] + ".hd")
            except Exception:
                pass
            snds = vag.sounds_with_rates(data, hd)
            return self._send(200, dict(
                ok=True, archive=arc, name=name, bytes=len(data),
                gate=round(gate, 4), rates_from=snds[0]["source"] if snds else None,
                sounds=[dict(index=x["index"], offset=x["offset"],
                             blocks=x["blocks"], rate=x["rate"],
                             samples=x["blocks"] * vag.SAMPLES_PER_BLOCK)
                        for x in snds]))

        if route == "sound_wav":
            # One sound as a playable WAV.
            #
            # This lived in `_api_post` and nothing could reach it. The front
            # end plays a sound by pointing an <audio src> at it and saves one
            # with an <a download>, both of which are GETs, so every ▶ and
            # every save in the bank browser got `404 no such route:
            # sound_wav`; a POST reached the branch and died on `NameError:
            # one` -- `one()` only exists in _api_get. That is why "BANKS
            # sounds do not load": not the codec, not the DAVE container, a
            # route in the wrong method.
            #
            # It also has to index sounds the SAME WAY the listing does.
            # `sounds` and `sound_import` both use sounds_with_rates (the .hd
            # region table); this used split_sounds (the END flag). They are
            # not the same list: measured across the disc, 154 of the 155
            # banks disagree, because nearly every sound ends with a one-block
            # terminator -- 3,432 of the 3,486 regions close with the same
            # 16 bytes, `00 07 77 77...`, flags END|LOOP|LOOP_START -- and the
            # END flag counts that block as a sound of its own. snd_00 is 30
            # sounds by the .hd and 58 by the flag, snd_50 is 130 against 260.
            # So #17 in the table played whatever sat at flag-index 17, and
            # above the halfway mark it 404'd.
            from . import vag
            arc = one("archive") or "BANKS.DAT"
            name = one("name")
            snd_idx = int(one("index", "0"))
            try:
                data = s.disc.read(arc, name)
            except Exception as ex:
                return self._send(404, {"error": str(ex)})
            hd = None
            try:
                hd = s.disc.read(arc, name[:-3] + ".hd")
            except Exception:
                pass
            snds = vag.sounds_with_rates(data, hd)
            if not 0 <= snd_idx < len(snds):
                return self._send(404, {"error": "sound %d of %d"
                                                 % (snd_idx, len(snds))})
            snd = snds[snd_idx]
            # The bank's own rate wins. `rate` stays a parameter for the 1 of
            # 155 banks whose .hd does not parse (snd_9999.bd), where there is
            # nothing to be authoritative about; 22050 is what the decoded
            # lengths sound sensible at.
            rate = int(one("rate") or 0) or snd["rate"] or 22050
            wav = vag.to_wav(vag.decode(data, snd["offset"],
                                        snd["blocks"] * vag.BLOCK), rate=rate)
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(wav)))
            self.send_header("Content-Disposition",
                             'inline; filename="%s_%02d.wav"'
                             % (name.rsplit(".", 1)[0], snd_idx))
            self.end_headers()
            self.wfile.write(wav)
            return

        # ------------------------------------------------------- game rules
        # rdrstudio/progression.py. Every one of these is a thin wrapper: the
        # module already returns JSON-ready dicts with a confidence tag on
        # each claim, and the UI renders those tags rather than flattening
        # them. `s.disc` is passed even to difficulty_info, which does not
        # strictly need a disc, so the difficulty names and the two unlock
        # messages are read out of swat.strtbl NOW instead of trusted from a
        # constant -- the page shows them as evidence.

        if route == "progression":
            from . import progression
            return self._send(200, progression.summary(s.disc))

        if route == "progression_levelflow":
            from . import progression
            return self._send(200, progression.read_levelflow(s.disc))

        if route == "progression_unlockables":
            from . import progression
            return self._send(200, progression.read_unlockables(s.disc))

        if route == "progression_difficulty":
            from . import progression
            return self._send(200, progression.difficulty_info(s.disc))

        if route == "progression_cheats":
            from . import progression
            return self._send(200, progression.cheats(s.disc))

        if route == "progression_can_unlock_difficulty":
            # Answers "why is that control greyed out" with the addresses,
            # instead of hiding the control and leaving it a mystery.
            from . import progression
            return self._send(200, progression.unlock_all_difficulties_note())

        # ---------------------------------------------------- AI vocabulary
        # Read-only reference. aivocab bakes both name tables in as data and
        # touches neither the ISO nor the executable, so this answers with no
        # disc open.

        if route == "aivocab":
            from . import aivocab
            return self._send(200, dict(
                conditions=[c.to_json() for c in aivocab.CONDITIONS],
                count=len(aivocab.CONDITIONS),
                unnamed_ids=list(aivocab.UNNAMED_IDS),
                never_used=list(aivocab.NEVER_USED),
                action_min=aivocab.ACTION_MIN,
                action_max=aivocab.ACTION_MAX,
                action_names_available=False,
                action_note=aivocab.ACTION_NO_NAME))

        # -------------------------------------------------- streamed audio
        # The 8,361 `.stm` files: voice, music and ambience. Headers only --
        # decoding is pure Python at 1-2 M samples/s, so the corpus would take
        # ~16 minutes. The listing reads 1.35% of the archive and takes under
        # a second; audio is fetched one file at a time, on click.

        if route == "streams":
            return self._send(200, dict(
                summary=self._streams_summary(), streams=self._stream_rows()))

        if route == "streams_groups":
            from . import streams as _st
            rows = self._stream_rows()
            groups = _st.group_streams(rows)
            # How many of each group loop. `summary` already counts them
            # disc-wide (179 of 8,361) but `group_streams` does not carry it
            # per group, and per group is what the browser needs: looping is
            # the one property you cannot hear in the first few seconds and
            # cannot read off a filename, and it is exactly what somebody
            # about to replace a music bed has to know first. Counted here
            # rather than in streams.py, which is not this lane's file.
            for grp in groups:
                n = 0
                for sec in grp.get("sections") or []:
                    for st in sec.get("streams") or []:
                        if st.get("looping"):
                            n += 1
                grp["looping"] = n
            return self._send(200, dict(groups=groups,
                                        summary=self._streams_summary()))

        if route == "streams_search":
            from . import streams as _st
            q = one("q") or ""
            hits = _st.search(self._stream_rows(), q) if q.strip() else []
            # The captions are the game's own text, so this finds a line by
            # its words even when the filename says nothing about them.
            return self._send(200, dict(q=q, count=len(hits), hits=hits[:400],
                                        truncated=len(hits) > 400))

        if route == "streams_header":
            from . import streams as _st
            try:
                return self._send(200, _st.read_header(s.disc, one("name") or ""))
            except Exception as ex:
                return self._send(404, {"error": str(ex)})

        if route == "streams_wav":
            # ONE file per request. A 90-second stereo music cue is ~3.5 s of
            # pure-Python ADPCM; never call this in a loop to build a listing.
            from . import streams as _st
            want = one("name") or ""
            try:
                wav = _st.wav_for(s.disc, want)
            except Exception as ex:
                return self._send(404, {"error": str(ex)})
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(wav)))
            self.send_header("Content-Disposition",
                             'inline; filename="%s.wav"'
                             % want.rsplit(".", 1)[0])
            self.end_headers()
            self.wfile.write(wav)
            return

        if route == "guide":
            # The user guides live inside the app (frozen: _internal/docs/,
            # source: 05_Docs/) and are read in the in-app Guides viewer, so
            # the app folder itself is just the exe, README.txt and licenses.
            fname = _GUIDES.get(one("name", ""))
            if not fname:
                return self._send(200, {"ok": True,
                                        "guides": sorted(_GUIDES)})
            for path in (resource("docs", fname), resource("05_Docs", fname)):
                if os.path.isfile(path):
                    with open(path, encoding="utf-8") as fh:
                        return self._send(200, {"ok": True, "name": fname,
                                                "text": fh.read()})
            return self._send(200, {"ok": False,
                                    "error": "guide not found: %s" % fname})

        if route == "roadmap":
            # Served from the file rather than copied into the UI, so the page
            # and the repo cannot disagree about the state of the project.
            #
            # `os` and `time` are NOT imported here. Doing so made them local
            # to this whole function, so the `config` route above -- which uses
            # the module-level `os` and runs long before this line -- started
            # raising UnboundLocalError. `config` is what run_tests polls to
            # decide the server is alive, so every live test silently skipped.
            path = resource("ROADMAP.md")
            try:
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
            except Exception as ex:
                return self._send(200, {"ok": False, "error": str(ex),
                                        "path": os.path.abspath(path)})
            return self._send(200, {
                "ok": True, "text": text,
                "bytes": len(text.encode("utf-8")),
                "modified": time.strftime(
                    "%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(path)))})

        if route == "history":
            # The UI polls this, which makes it the natural place to land a
            # debounced session save -- see Journal.flush_autosave().
            s.journal.flush_autosave()
            # `limit` is the journal's own window, and until now no caller could
            # widen it. Journal.history(limit=200) returns self.entries[-limit:]
            # -- a SLIDING window -- so once a session passes 200 entries, each
            # new one evicts the oldest and the row count pins at 200. An edit
            # then stops costing an OBSERVABLE undo step, which is different
            # from not costing one: undo itself is correct at any depth, only
            # the report is capped.
            #
            # That cost a registered row. test_new_routes asserts "one import is
            # one undo step" by counting live rows either side, and on
            # 2026-09-18 it read 139 -> 139 and failed. Measured against the
            # real Journal, the boundary is exact:
            #
            #     199 entries -> 199 rows, +1 -> 200   observable
            #     200 entries -> 200 rows, +1 -> 200   NOT observable
            #
            # The default is unchanged, so the UI polls exactly what it did
            # before; a caller that needs to see past the window now asks.
            try:
                lim = int(one("limit", "200"))
            except ValueError:
                return self._send(400, {"error": "limit must be a whole number"})
            if lim < 1:
                return self._send(400, {"error": "limit must be 1 or more"})
            return self._send(200, dict(
                entries=s.journal.history(limit=lim), can_undo=s.journal.can_undo,
                can_redo=s.journal.can_redo,
                touched=[list(k) for k in s.journal.touched_files()]))

        if route == "checkpoints":
            return self._send(200, s.checkpoints())

        if route == "file_diff":
            return self._send(200, s.file_diff(one("archive"), one("name")))

        if route == "mods":
            out = []
            if os.path.isdir(cfg.MODS):
                for fn in sorted(os.listdir(cfg.MODS)):
                    if not fn.endswith(".rdrmod.json"):
                        continue
                    # _session.rdrmod.json is the crash autosave. The restore
                    # route already refuses it as "a safety net, not a version"
                    # -- but this listing offered it to the Changes tab, where
                    # "Load onto my disc" applies a multi-megabyte snapshot
                    # with no confirmation. Machine files stay out of menus.
                    if fn.startswith("_"):
                        continue
                    path = os.path.join(cfg.MODS, fn)
                    try:
                        doc = read_mod(path)
                    except Exception:
                        continue
                    out.append(dict(path=path, file=fn, name=doc.get("name"),
                                    notes=doc.get("notes", ""),
                                    created=doc.get("created"),
                                    files=len(doc.get("files", []))))
            return self._send(200, out)

        if route == "capacity":
            arc = one("archive")
            lvl = s.level(arc, area=one("area") or None)
            rows = []
            for filename in sorted(lvl.sources):
                try:
                    rows.append(lvl.capacity(filename))
                except Exception:
                    continue
            for r in rows:
                r["kinds"] = sorted({n.kind for n in lvl.nodes
                                     if n.source == r["file"]})
                r["objects"] = sum(1 for n in lvl.nodes if n.source == r["file"])
            rows.sort(key=lambda r: -r["approx_text"])
            return self._send(200, dict(archive=arc, files=rows))

        if route == "editable_fields":
            from .scene import EDITABLE_ENEMY_FIELDS
            return self._send(200, EDITABLE_ENEMY_FIELDS)

        if route == "geometry":
            lvl = s.level(one("archive"), manifest=one("manifest") or None,
                          area=one("area") or None)
            if getattr(lvl, "_geo_json", None) is None:
                lvl._geo_json = json.dumps(lvl.geometry()).encode("utf-8")
            return self._send(200, lvl._geo_json)

        if route == "textures":
            lvl = s.level(one("archive"), area=one("area") or None)
            r = lvl._load_rsc()
            if r is None:
                return self._send(200, dict(source=None, textures=[]))
            # `bpp` and `swizzled` ride on every row so the grid can offer
            # Replace... only where texture.encode_into will actually take it.
            # The panel used to gate on `pal_kind == "PAL0"`, which is EVERY
            # texture on the disc (5,687 of 5,687) -- so its "read only"
            # branch was dead code and the button was offered on thousands of
            # textures that fail after the user has already picked a PNG.
            # rdr-fmt measured the split; CAPABILITIES.md C-027.
            #
            # Both conditions mirror texture.encode_into's own refusals
            # (texture.py, "only 256-colour textures" and "re-swizzling on
            # import is not implemented yet"). That module owns the policy --
            # this is a second reading of it, which is exactly the kind of
            # copy that drifts, so a `can_replace()` there would be better and
            # is asked for in LANE-REQUESTS.md.
            from . import texture as _tex
            rows = []
            for i, (tex_i, info, mip, pal) in enumerate(r.texture_table()):
                try:
                    head = r.raw(mip.index)
                    bpp = _tex.pixel_format(head)
                    swizzled = bool(_tex.is_swizzled(head))
                except Exception:                          # noqa: BLE001
                    bpp, swizzled = None, False
                refused = ("swizzled" if swizzled
                           else "bpp" if (bpp is not None and bpp != 8)
                           else None)
                rows.append(dict(i=i, name=info["name"], chunk=mip.index,
                                 pal=pal.index, bytes=mip.size,
                                 pal_kind=pal.tag, bpp=bpp,
                                 swizzled=swizzled,
                                 replaceable=refused is None,
                                 refused=refused,
                                 width=info["width"], height=info["height"]))
            return self._send(200, dict(source=lvl.rsc_name, textures=rows))

        if route == "texture_users":
            # Which models actually draw with this texture. Only answerable
            # now that the binding is exact -- under the old nearest-preceding
            # guess this list would have been fiction.
            lvl = s.level(one("archive"), area=one("area") or None)
            r = lvl._load_rsc()
            if r is None:
                return self._send(200, dict(users=[]))
            slotmap = r.tex_slot_map()
            want = int(one("i", "0"))
            from .scene import chunk_of
            names = {}
            for n in lvl.nodes:
                ci = chunk_of(n)
                if ci is not None:
                    names.setdefault(ci, n.name)
            users = []
            for c in r.chunks:
                if c.tag != "MOD0":
                    continue
                p = r.parts(c.index)
                if not p:
                    continue
                tris = sum(rec.get("indices", 0) for rec in p
                           if slotmap.get(rec["resource"]) == want)
                if tris:
                    users.append(dict(chunk=c.index,
                                      name=names.get(c.index,
                                                     "MOD0_%d" % c.index),
                                      strip=tris, parts=len(p)))
            users.sort(key=lambda u: -u["strip"])
            return self._send(200, dict(i=want, area=lvl.rsc_name,
                                        users=users[:200],
                                        total=len(users)))

        if route == "texture.png":
            from . import texture as T
            # Area-aware: a texture index only means something inside one
            # bundle, so painting a secondary area without it served the
            # wrong artwork entirely.
            lvl = s.level(one("archive"), area=one("area") or None)
            r = lvl._load_rsc()
            if r is None:
                return self._send(404, {"error": "no resource bundle"})
            table = r.texture_table()
            i = int(one("i", "0"))
            if not (0 <= i < len(table)):
                return self._send(404, {"error": "no texture %d" % i})
            _, info, mip, pal = table[i]
            memo = getattr(lvl, "_tex_png", None)
            if memo is None:
                memo = lvl._tex_png = {}
            png_bytes = memo.get(i)
            if png_bytes is None:
                res = T.decode(r.raw(mip.index),
                               T.parse_palette(r.raw(pal.index)))
                if not res:
                    return self._send(404,
                                      {"error": "could not decode texture %d" % i})
                w, h, rgba = res
                png_bytes = memo[i] = T.encode_png(w, h, rgba)
            # The pure-Python decode cost 1.44 s per level open (measured,
            # TOMB4's 79 textures); an edited texture drops the whole Level
            # from the cache, so an hour of browser cache is safe.
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(png_bytes)))
            self.send_header("Cache-Control", "max-age=3600")
            self.end_headers()
            try:
                self.wfile.write(png_bytes)
            except (ConnectionError, OSError):
                pass
            return

        if route == "model":
            # One model, decoded on its own, so any panel can show a 3D
            # preview of the thing it is talking about without pulling in the
            # whole level. Same shape as one entry of render_geometry, so the
            # viewer code is shared rather than forked.
            lvl = s.level(one("archive"), area=one("area") or None)
            r = lvl._load_rsc()
            if r is None:
                return self._send(404, {"error": "no resource bundle"})
            try:
                chunk = int(one("chunk", "-1"))
            except ValueError:
                chunk = -1
            if not (0 <= chunk < len(r.chunks)) or r.chunks[chunk].tag != "MOD0":
                return self._send(404,
                                  {"error": "chunk %s is not a model" % chunk})
            try:
                m = r.model(chunk)
            except Exception as exc:
                return self._send(404, {"error": "decode failed: %s" % exc})
            if not m or not m["tris"]:
                return self._send(404,
                                  {"error": "chunk %d decoded empty" % chunk})

            slotmap = r.tex_slot_map()
            table = r.texture_table()
            vpos: list[float] = []
            idx = []
            uvs = []
            cols: list[Any] = []
            for v in m["verts"]:
                vpos.extend(round(x, 3) for x in v)
            for t in m["tris"]:
                idx.extend(t)
            for uv in m["uvs"][:len(m["verts"])]:
                uvs.extend(round(x, 5) for x in uv)
            for c4 in m["colors"][:len(m["verts"])]:
                cols.extend(min(1.0, c / 128.0) for c in c4[:3])

            groups = []
            for res, start, count, colour in m.get("part_ranges") or ():
                if count <= 0:
                    continue
                slot = slotmap.get(res)
                if groups and groups[-1]["texture"] == slot \
                        and groups[-1]["colour"] == colour \
                        and groups[-1]["start"] + groups[-1]["count"] == start * 3:
                    groups[-1]["count"] += count * 3
                else:
                    groups.append(dict(start=start * 3, count=count * 3,
                                       texture=slot, colour=colour))
            used = sorted({g["texture"] for g in groups
                           if g["texture"] is not None})
            return self._send(200, dict(
                archive=lvl.archive, area=lvl.rsc_name, chunk=chunk,
                positions=vpos, indices=idx,
                uvs=uvs if len(uvs) * 3 == len(vpos) * 2 else [],
                colors=cols if len(cols) == len(vpos) else [],
                groups=groups, parts=m.get("parts", 1),
                verts=len(m["verts"]), tris=len(m["tris"]),
                textures=[dict(i=i, name=table[i][1]["name"],
                               width=table[i][1]["width"],
                               height=table[i][1]["height"]) for i in used]))

        if route == "render_geometry":
            lvl = s.level(one("archive"), manifest=one("manifest") or None,
                          area=one("area") or None)
            if getattr(lvl, "_render_json", None) is None:
                lvl._render_json = json.dumps(
                    lvl.render_geometry()).encode("utf-8")
            return self._send(200, lvl._render_json)

        if route == "toc":
            arc = one("archive")
            filt = (one("q") or "").lower()
            # `ext` is an EXACT extension match, which `q` cannot express: `q`
            # is a substring, so `q=.anim` also matched the 2,420 `.animsnd`
            # files, and they ate half the row budget before the caller saw a
            # clip it could actually use.
            ext = (one("ext") or "").lower().lstrip(".")
            # The cap stops a 47,115-file archive being dumped by accident, but
            # a flat 4,000 silently truncated SWAT.DAT's 6,607 `.anim` matches
            # mid-alphabet -- everything after `red_flt_...`, i.e. most of the
            # player's own animations. A caller that knows it wants a big
            # filtered list may ask for one.
            try:
                limit = max(1, min(60000, int(one("limit") or 4000)))
            except ValueError:
                limit = 4000
            d = s.disc.archive(arc)
            rows = []
            for e in d:
                if ext and e.ext.lower() != ext:
                    continue
                if filt and filt not in e.name:
                    continue
                rows.append(dict(i=e.index, name=e.name, ext=e.ext,
                                 offset=e.offset, size=e.size_full,
                                 stored=e.size_comp, slot=e.slot,
                                 headroom=e.headroom,
                                 compressed=e.compressed, aligned=e.aligned,
                                 text=e.ext in TEXT_EXTS))
            rows.sort(key=lambda r: r["name"])
            # `count` is the TRUE number of matches and `rows` may be fewer, so
            # say which outright rather than leaving a caller to notice that two
            # numbers it may never have compared disagree.
            return self._send(200, dict(archive=arc, count=len(rows),
                                        total=d.count, rows=rows[:limit],
                                        truncated=len(rows) > limit))

        if route == "file":
            import re as _re
            arc, name = one("archive"), one("name")
            d = s.disc.archive(arc)
            e = d.find(name)
            data = d.read(e)
            file_info = dict(archive=arc, name=name, ext=e.ext, size=len(data),
                        slot=e.slot, stored=e.size_comp, compressed=e.compressed)
            # Heuristic text detection as a fallback: many extensions are
            # text without being on the known list.
            probe = data[:2048]
            texty = probe and sum(1 for c in probe
                                  if 32 <= c < 127 or c in (9, 10, 13)) > len(probe) * 0.95
            if e.ext in TEXT_EXTS or texty:
                file_payload = dict(file_info, kind="text", text=data.decode("latin-1"))
                # Skeletons are text (so still directly editable) but read far
                # better as a tree with world positions worked out -- offer
                # both rather than making the user choose.
                from . import skeleton as _skel
                if _skel.is_skeleton(data):
                    try:
                        v, declared, bones, _root = _skel.parse(data)
                        file_payload["tree"] = _skel.listing(data)
                        file_payload["tree_note"] = (
                            "%d bones, version %d." % (len(bones), v)
                            # The header is right, not stale: the runtime
                            # skins exactly NumBones joints, and the ones
                            # listed after them are attachment points (the
                            # weapon and prop joints on the characters that
                            # were checked). This sentence used to call the
                            # game's own file wrong.
                            + ("  NumBones says %d: the skinned body. The "
                               "%d after them are joints the header does not "
                               "count, used as attachment points."
                               % (declared, len(bones) - declared)
                               if declared < len(bones) else
                               "  NumBones says %d." % declared
                               if declared != len(bones) else "")
                            + "  offset is relative to the parent, world is "
                              "accumulated.  T/R/S = channels animation may drive.")
                    except Exception:
                        pass
                return self._send(200, file_payload)
            # Skeletons are plain text but read far better as a tree with
            # world-space positions worked out.
            from . import skeleton as _skel
            if _skel.is_skeleton(data):
                try:
                    v, declared, bones, root = _skel.parse(data)
                    return self._send(200, dict(
                        file_info, kind="skeleton", listing=_skel.listing(data),
                        bones=[b.to_json() for b in bones],
                        version=v, declared=declared,
                        stale_header=declared != len(bones)))
                except Exception:
                    pass

            # Behaviour tables get a structural listing rather than raw hex:
            # names, float parameters and opcode indices with their offsets.
            # The CONDITIONS are named now -- aivocab carries both name tables
            # out of SLUS_205.00, so `dist` reads as `dist = id 8 (creature)`
            # instead of a bare token. The ACTIONS are still numbers and stay
            # numbers: no action name ships on this disc, and inventing one is
            # the single mistake this whole layer is built to avoid.
            #
            # The actb keys stay in the payload. app.js reads s.floats, s.ints,
            # s.names, s.opcodes and s.unresolved_text; dropping them would
            # blank the header line rather than improve it.
            from . import actb as _actb
            if _actb.is_actb(data):
                try:
                    from . import aivocab as _voc
                    parsed = _actb.parse(data, limit=4000)
                    return self._send(200, dict(
                        file_info, kind="behaviour",
                        listing=_voc.listing(data),
                        rows=_voc.annotate(parsed),
                        summary=dict(_actb.summary(data), **_voc.summary(data))))
                except Exception:
                    # Any surprise in the vocabulary layer must not cost the
                    # user the plain listing they had before.
                    try:
                        return self._send(200, dict(
                            file_info, kind="behaviour",
                            listing=_actb.listing(data),
                            summary=_actb.summary(data)))
                    except Exception:
                        pass

            # Animations: show what the header says even though the curve
            # data itself is still compressed.
            hdr = _skel.anim_header(data)
            if hdr:
                file_info["anim"] = hdr

            offset = int(one("offset", "0"))
            window = data[offset:offset + 4096]
            strings = [dict(off=m.start(), s=m.group().decode("latin-1"))
                       for m in _re.finditer(rb"[ -~]{4,}", data)][:800]
            return self._send(200, dict(
                file_info, kind="binary", offset=offset,
                hex=window.hex(),
                ascii="".join(chr(c) if 32 <= c < 127 else "." for c in window),
                strings=strings))

        if route == "file.bin":
            # Raw export of any file on the disc.
            arc, name = one("archive"), one("name")
            data = s.disc.archive(arc).read(name)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition",
                             'attachment; filename="%s"'
                             % name.replace("/", "_").replace('"', ""))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionAbortedError):
                pass
            return None

        if route == "props":
            # Any of the engine's ~100 plain-text formats, as editable
            # fields. Lighting, fog, sky, water, weather, particles, LOD
            # distances, shop stock -- all of it is text, and all of it was
            # sitting behind "opaque binary" until it was actually checked.
            from . import props as P
            arc, name = one("archive"), one("name")
            try:
                raw = s.disc.archive(arc).read(name)
            except Exception as exc:
                return self._send(404, {"error": str(exc)})
            if not P.is_textual(raw):
                return self._send(200, dict(archive=arc, name=name,
                                            textual=False, fields=[],
                                            total=0, blocks=[]))
            out = P.summary(raw)
            out.update(archive=arc, name=name, textual=True, bytes=len(raw))
            try:
                e = s.disc.archive(arc).find(name)
                out.update(slot=e.slot, headroom=e.slot - e.size_comp)
            except Exception:
                pass
            return self._send(200, out)

        if route == "character_export":
            # A character part as an OBJ. Exported in BONE-LOCAL space, which
            # is how it is stored -- the standing figure you see in the viewer
            # is that same data pushed through the skeleton, and exporting the
            # posed version would be unimportable.
            from . import geomedit
            from .rsc import Rsc
            arc, name = one("archive"), one("name")
            chunk = int(one("chunk", "-1"))
            try:
                try:
                    bundle = Rsc(s.vanilla.archive(arc).read(name), name)
                except Exception:                          # noqa: BLE001
                    # an added character (not on the unedited disc)
                    bundle = Rsc(s.disc.archive(arc).read(name), name)
            except Exception as ex:
                return self._send(404, dict(error=str(ex)))
            if not (0 <= chunk < len(bundle.chunks)) \
                    or bundle.chunks[chunk].tag != "MOD0":
                return self._send(404, {"error": "chunk %s is not a model"
                                                 % chunk})
            m = bundle.model(chunk)
            if not m:
                return self._send(404, {"error": "chunk %d will not decode"
                                                 % chunk})
            if not geomedit.editable(m):
                return self._send(400, {"error": "this part has no editable "
                                                 "vertex data"})
            p = geomedit.precision(m)
            # Pose it. Exported bone-local it arrives in Blender as a crumpled
            # ball with every limb on the origin, which is what a tester hit.
            offs, skel_used = s.part_pose(arc, name, m)
            meta = dict(archive=arc, bundle=name, chunk=chunk,
                        space="bind pose (standing)" if offs else "bone-local")
            if skel_used:
                meta["skeleton"] = skel_used
            if p:
                meta["step_m"] = "%.6f" % p["step"]
                meta["range_m"] = "%.2f" % p["range"]
            posed = m
            if offs:
                posed = dict(m)
                posed["verts"] = [(v[0] + o[0], v[1] + o[1], v[2] + o[2])
                                  for v, o in zip(m["verts"], offs)]
            obj = geomedit.to_obj(posed,
                                  "%s_%d" % (name.rsplit(".", 1)[0], chunk),
                                  meta)
            return self._send(200, obj.encode("latin-1"),
                              "text/plain; charset=latin-1")

        if route == "character_rsc":
            # The whole character as the game's compiled bundle, for the RDR
            # Blender add-on (File > Import > Red Dead Revolver bundle): a zip
            # of <stem>_lods.rsc and .rnt from the WORKING disc (your edits
            # in it), with the .skel and .cr it uses when SWAT.DAT has them.
            import io as _io
            import re as _re
            import zipfile as _zf
            arc, name = one("archive"), one("name")
            d = s.disc.archive(arc)
            stem = name.rsplit(".", 1)[0]
            code = stem[:-5] if stem.endswith("_lods") else stem
            buf = _io.BytesIO()
            with _zf.ZipFile(buf, "w", _zf.ZIP_DEFLATED) as z:
                z.writestr(name, d.read(name))
                try:
                    z.writestr(stem + ".rnt", d.read(stem + ".rnt"))
                except Exception:                          # noqa: BLE001
                    pass
                sw = s.disc.archive("SWAT.DAT")
                try:
                    cr = sw.read(code + ".cr")
                    z.writestr(code + ".cr", cr)
                    m = _re.search(rb"(?m)^\s*FileNameSkel\s+(\S+)", cr)
                    sk = (m.group(1).decode("latin-1") if m else code) + ".skel"
                    z.writestr(sk, sw.read(sk))
                except Exception:                          # noqa: BLE001
                    pass
            return self._send(200, buf.getvalue(), "application/zip")

        if route == "character_parts":
            # The editable pieces of a character bundle, for a UI to list.
            from . import geomedit
            from .rsc import Rsc
            arc, name = one("archive"), one("name")
            try:
                bundle = Rsc(s.disc.archive(arc).read(name), name)
            except Exception as ex:
                return self._send(404, dict(error=str(ex)))
            names = {}
            try:
                from .rsc import parse_rnt
                rnt = parse_rnt(s.disc.archive(arc).read(
                    name.rsplit(".", 1)[0] + ".rnt"))
                names = rnt or {}
            except Exception:
                pass
            rows = []
            for c in bundle.chunks:
                if c.tag != "MOD0":
                    continue
                try:
                    m = bundle.model(c.index)
                except Exception:
                    continue
                if not m or not m["tris"]:
                    continue
                p = geomedit.precision(m)
                rows.append(dict(
                    chunk=c.index, name=names.get(c.index) or ("part %d" % c.index),
                    verts=len(m["verts"]), tris=len(m["tris"]),
                    skinned=bool(m.get("skinned")),
                    editable=geomedit.editable(m),
                    step=round(p["step"], 6) if p else None,
                    range=round(p["range"], 2) if p else None))
            return self._send(200, dict(archive=arc, bundle=name, parts=rows))

        if route == "mission_enemies":
            # Logic > Missions, the bottom half: a level's enemy rosters (.enm) and the enemies in one -- the one
            # for the mission's variation (lvl_<level>_var_<n>.enm) when asked. Each enemy as the 3D editor's
            # inspector has it (uid, name, file, its fields as written), edited through GET enemy / POST enemy_set.
            archive = one("archive")
            if not archive:
                return self._send(400, {"error": "need a level archive"})
            try:
                lvl = s.level(archive)
            except Exception as ex:                             # noqa: BLE001
                return self._send(200, {"ok": False, "error_text": "%s could not be opened: %s" % (archive, ex)})
            files = lvl.enm_files()
            want = (one("file") or "").lower()
            pick = next((f for f in files if f.lower() == want), None) or (sorted(files)[0] if files else None)
            enemies = []
            for n in (lvl.enemy_nodes(pick) if pick else []):
                try:
                    pos = list(n.xyz())
                except Exception:                               # noqa: BLE001
                    pos = None
                enemies.append(dict(uid=n.uid, name=n.props.get("Name") or n.name, source=n.source,
                                    props=dict(n.props), pos=pos,
                                    angle=(n.angle.value if getattr(n, "angle", None) else 0.0)))
            return self._send(200, {"ok": True, "archive": archive, "files": sorted(files), "file": pick,
                                    "enemies": enemies})

        if route == "weapon_model_export":
            # Stats > Weapons & stats > Weapon model: the weapon's model as the game has it, the PS2's .mod (as on
            # the working disc) or converted to the Xbox's .xmod.
            target = one("target")
            fmt = (one("format", "mod") or "mod").lower()
            if not target:
                return self._send(400, {"error": "need the model, as `target`"})
            arcs = s.disc.locate(target)
            if not arcs:
                return self._send(404, {"error": "the working disc has no %s" % target})
            arc = next((a for a in arcs if a.upper() == "SWAT.DAT"), arcs[0])
            data = s.disc.read(arc, target)
            stem = target.rsplit(".", 1)[0]
            if fmt == "xmod":
                try:
                    data, _notes = _weapon_model_convert(s.disc, data, target, True)
                except Exception as ex:                         # noqa: BLE001
                    return self._send(200, {"ok": False, "problem": {
                        "message": "the model could not be made the Xbox's: %s" % ex}})
                fname = stem + ".xmod"
            else:
                fname = stem + ".mod"
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", 'attachment; filename="%s"' % fname)
            self.end_headers()
            self.wfile.write(data)
            return

        if route == "level_export":
            # A WHOLE level as one OBJ + MTL + textures, zipped.
            #
            # Different job from geom_export below, which hands back one
            # surface for reshaping and re-importing. This is for taking the
            # map somewhere else -- somebody modding another game asked for
            # exactly this -- so it wants EVERY area of the archive at once,
            # placed, with materials that resolve.
            #
            # Read from the WORKING disc, not vanilla: someone exporting a map
            # wants the level as they have edited it. geom_export reads vanilla
            # for the opposite reason -- it round-trips back in.
            import io as _io
            import tempfile
            import zipfile as _zip
            from . import export as EX
            arc = one("archive")
            if not arc:
                return self._send(400, {"error": "need an archive"})
            want_col = one("collision", "1") not in ("0", "false", "no")
            want_tex = one("textures", "1") not in ("0", "false", "no")
            # Defaults to ON, like collision and textures beside it. OBJ has no
            # scene graph, so an OBJ-only export cannot say which meshes are
            # art and which are collision -- and that split is the reason to
            # take a level out of the game at all. Shipping it off by default
            # meant the ordinary click handed people the one format that drops
            # the feature, silently.
            want_glb = one("glb", "1") not in ("0", "false", "no")
            # "native" writes every chunk at its stored coordinates: no tile
            # layout, and no shove to keep sibling areas from interpenetrating.
            lay = "native" if one("layout", "auto") == "native" else "auto"
            # Comma-separated area bundle names or stems; empty means all.
            want_areas = [a for a in one("areas", "").split(",") if a.strip()]
            fmt = one("format", "model")
            if fmt in ("rsc", "xrsc"):
                # the level's own files, as the game reads them: for the PS2 (.rsc, .znes ...) or made the Xbox's
                # (.xrsc, .xznes ...), zipped -- for another mod, Blender's importer, or the other game
                try:
                    stem = "lvl_" + arc.rsplit("/", 1)[-1][:-4].lower()
                    files = {}
                    folder = s._mods_level_folders().get(arc.upper()) if arc.upper() not in {
                        a.upper() for a in (s.game or s.vanilla).archives()} else None
                    if folder:
                        for f in os.listdir(folder):
                            p = os.path.join(folder, f)
                            if os.path.isfile(p) and f.lower().startswith(stem) and not f.lower().endswith(
                                    (".xrsc", ".xrnt", ".xznes", ".xtex", ".xmod", ".xalpha", ".xbd", ".xhd")):
                                files[f] = open(p, "rb").read()
                    else:
                        from .game import Game as _Game
                        g = _Game(s.modded_path, writable=False)
                        try:
                            a = g.archive(arc)
                            for e in a:
                                n = e.name.rsplit("/", 1)[-1]
                                if not e.name.endswith("/") and n.lower().startswith(stem):
                                    files[n] = a.read(e)
                        finally:
                            g.close()
                    if not files:
                        return self._send(400, {"error": "%s has no level files (%s...)" % (arc, stem)})
                    from .xboxmode import xbox as _xbox
                    out = _xbox(_data_editor(s)).level_files(arc, files, fmt == "xrsc")
                except Exception as ex:                    # noqa: BLE001
                    return self._send(400, {"error": "export failed: %s" % ex})
                buf = _io.BytesIO()
                with _zip.ZipFile(buf, "w", _zip.ZIP_DEFLATED, compresslevel=6) as z:
                    for n in sorted(out):
                        z.writestr(n, out[n])
                s.note("exported %s's level files for the %s: %d files"
                       % (arc, "Xbox" if fmt == "xrsc" else "PS2", len(out)))
                self._send(200, buf.getvalue(), "application/zip")
                return
            tmp = tempfile.mkdtemp(prefix="rdrlvl")
            try:
                # A FRESH read-only handle on the same working file, not the
                # live `s.disc`. MEASURED on TOMB4: 7.6 s this way against
                # 179 s through the shared disc -- a whole-level export touches
                # every archive the level uses, and the live disc does
                # something per read that a private handle does not. It reads
                # the same bytes, because edits are written through to the
                # file, so the export still reflects what the user changed.
                #
                # It also keeps a long job off the object the rest of the UI is
                # using. Three minutes of a frozen window is the failure this
                # project has already shipped once.
                from .game import Game as _Game
                try:
                    src = _Game(s.modded_path)
                except Exception as ex:                    # noqa: BLE001
                    return self._send(400, {"error": "cannot open the working "
                                                     "disc: %s" % ex})
                try:
                    st = EX.export_level(src, arc, tmp, collision=want_col,
                                         surfaces=True, textures=want_tex,
                                         areas=want_areas or None, layout=lay,
                                         glb=want_glb)
                except Exception as ex:                    # noqa: BLE001
                    return self._send(400, {"error": "export failed: %s" % ex})
                finally:
                    try:
                        src.close()
                    except Exception:                      # noqa: BLE001
                        pass
                buf = _io.BytesIO()
                n_files = 0
                n_tex = 0
                # compresslevel is KEYWORD-only in effect: ZipFile's fourth
                # POSITIONAL is allowZip64, so `..., ZIP_DEFLATED, 6)` set
                # allowZip64=6 and left compresslevel=None -- this export
                # had never actually deflated at 6, only at zlib's default.
                # Found by rdr-re 2026-09-18 while typing studio.py.
                with _zip.ZipFile(buf, "w", _zip.ZIP_DEFLATED,
                                  compresslevel=6) as z:
                    for base, _dirs, files in os.walk(tmp):
                        for f in sorted(files):
                            full = os.path.join(base, f)
                            rel = os.path.relpath(full, tmp)
                            z.write(full, rel.replace("\\", "/"))
                            n_files += 1
                            if f.lower().endswith(".png"):
                                n_tex += 1
                blob = buf.getvalue()
                s.note("exported %s as a model set: %d area(s), %d objects, "
                       "%d triangles, %d textures, %d files, %.1f MB"
                       % (arc, st.get("area_count", 1), st.get("objects", 0),
                          st.get("tris", 0), n_tex, n_files,
                          len(blob) / 1048576.0))
                # The client names the file (see saveFile in app.js); this
                # just hands over the bytes.
                self._send(200, blob, "application/zip")
                return
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

        if route == "geom_export":
            # One surface as an OBJ you can open in Blender. Exported from
            # VANILLA, so what comes back can be compared against stock and
            # the transform stays exact.
            from . import geomedit
            stock = Level(s.vanilla, one("archive"), area=one("area") or None)
            vr = stock._load_rsc()
            chunk = int(one("chunk", "-1"))
            if vr is None or not (0 <= chunk < len(vr.chunks)):
                return self._send(404, {"error": "no chunk %d" % chunk})
            if vr.chunks[chunk].tag != "MOD0":
                return self._send(400, {"error": "chunk %d is a %s, not a "
                                                 "model"
                                        % (chunk, vr.chunks[chunk].tag)})
            m = vr.model(chunk)
            if not m or not geomedit.editable(m):
                return self._send(400, {"error": "this surface cannot be "
                                                 "exported for editing"})
            name = one("name") or ("chunk_%d" % chunk)
            obj = geomedit.to_obj(m, name, dict(
                archive=stock.archive, area=stock.rsc_name or "",
                chunk=chunk))
            return self._send(200, obj.encode("latin-1"),
                              "text/plain; charset=latin-1")

        if route == "strings":
            # Every displayed string in the game, searchable. The same table
            # is duplicated across archives (a level carries the strings it
            # needs), so results are grouped by identifier and the copies are
            # listed -- editing one usually means editing all of them.
            from . import strtbl as ST
            # NOT `q` -- `one()` closes over the query dict of that name, so
            # a local `q` here silently turns every later one() call into an
            # AttributeError on a string.
            needle = (one("q") or "").strip().lower()
            slot = int(one("slot", "0"))
            scope = one("archive") or None
            limit = min(int(one("limit", "500")), 4000)

            found: dict[Any, Any] = {}
            file_count = 0
            archives = [scope] if scope else s.disc.archives()
            for arc in archives:
                try:
                    dv = s.disc.archive(arc)
                except Exception:
                    continue
                for e in dv:
                    if e.ext != "strtbl":
                        continue
                    try:
                        t = ST.parse(dv.read(e))
                    except Exception:
                        continue
                    file_count += 1
                    for name, r in t.entries(slot):
                        if name is None:
                            continue
                        if needle and needle not in name.lower() \
                                and needle not in r.text.lower():
                            continue
                        row = found.setdefault(name, dict(
                            name=name, text=r.text, font=r.font,
                            scale=[round(r.sx, 4), round(r.sy, 4)],
                            copies=[]))
                        row["copies"].append(dict(archive=arc, file=e.name))
                        # Copies do diverge between archives; surface it
                        # rather than pretending the first one is canonical.
                        if r.text != row["text"]:
                            row["varies"] = True
                s.disc._cache.pop(arc.upper(), None)

            rows = sorted(found.values(), key=lambda r: r["name"].lower())
            return self._send(200, dict(
                strings=rows[:limit], total=len(rows), files=file_count,
                capped=len(rows) > limit,
                languages={str(k): v for k, v in ST.LANG_NAMES.items()}))

        if route == "field_search":
            # Find one setting everywhere it occurs, across the whole disc.
            #
            # There are ~1.46M parsed fields in ~55,000 text files. Searching
            # all of them for every query would be slow and useless; scoping
            # by extension is both fast and what you actually want, because
            # a setting belongs to a format. "Enable in every .fog" is a
            # question with an answer; "Enable everywhere" is not.
            from . import props as P
            key = (one("key") or "").strip().lower()
            ext = (one("ext") or "").strip().lower().lstrip(".")
            scope = one("archive") or None
            # A whole format sweep is the normal case, not an edge case:
            # 301 .lgt files carry 602 Ambient fields, so a 600 cap truncated
            # the answer to a question that has a complete one.
            limit = min(int(one("limit", "4000")), 20000)
            if not ext:
                return self._send(400, {"error": "an extension is required"})

            hits = []
            file_count = scanned = 0
            archives = [scope] if scope else s.disc.archives()
            for arc in archives:
                if len(hits) >= limit:
                    break
                try:
                    dv = s.disc.archive(arc)
                except Exception:
                    continue
                for e in dv:
                    if e.ext != ext:
                        continue
                    scanned += 1
                    if len(hits) >= limit:
                        break
                    try:
                        raw = dv.read(e)
                    except Exception:
                        continue
                    if not P.is_textual(raw):
                        continue
                    got = False
                    for f in P.parse(raw):
                        if key and key not in f.key.lower() \
                                and key not in f.path.lower():
                            continue
                        got = True
                        hits.append(dict(archive=arc, file=e.name,
                                         path=f.path, key=f.key,
                                         value=f.raw, kind=f.kind,
                                         line=f.line))
                        if len(hits) >= limit:
                            break
                    if got:
                        file_count += 1
                s.disc._cache.pop(arc.upper(), None)

            # Group identical values so "they are all 1 except these three"
            # is visible at a glance -- that is usually the actual question.
            vals: dict[Any, int] = {}
            for h in hits:
                vals[h["value"]] = vals.get(h["value"], 0) + 1
            common = sorted(vals.items(), key=lambda kv: -kv[1])[:12]
            return self._send(200, dict(
                hits=hits, files=file_count, scanned=scanned,
                capped=len(hits) >= limit,
                distinct=len(vals),
                common=[dict(value=v, count=n) for v, n in common]))

        if route == "env_presets":
            return self._send(200, [dict(key=k, label=v["label"],
                                         note=v["note"])
                                    for k, v in ENV_PRESETS.items()])

        if route == "anim_bvh":
            # Download an animation as BVH, which Blender imports natively.
            # See rdrstudio/bvh.py for what is verified and what is not -- the
            # channel-to-bone mapping is confirmed, the Euler composition order
            # is not, hence the `euler` parameter. The decode + rig choice live
            # in animbatch.clip_to_bvh, shared with "Export all animations" so
            # the single clip and the batch cannot pick different rigs.
            from . import animbatch as AB
            arc, name = one("archive"), one("name")
            # YZX, not XYZ: the order the engine composes in (0x00146B68).
            euler = (one("euler") or "YZX").upper()
            try:
                raw_bytes = s.disc.read(arc, name)
            except Exception as ex:
                return self._send(400, {"error": str(ex)})
            try:
                a = AB.decode_clip(raw_bytes, name)
            except Exception as ex:
                return self._send(400, {"error": str(ex)})
            try:
                r = AB.clip_to_bvh(s, arc, name, skeleton=one("skeleton"),
                                   euler=euler, a=a)
            except AB.ClipRefused as ex:
                d = {"error": str(ex)}
                if ex.candidates:
                    d["candidates"] = ex.candidates
                return self._send(400, d)
            except ValueError as ex:
                return self._send(400, {"error": str(ex)})
            body = r["text"].encode("utf-8")
            fn = name.rsplit(".", 1)[0] + ".bvh"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Disposition",
                             'attachment; filename="%s"' % fn)
            # A shorter rig is legal and is what the game does, but the user
            # should not have to work out why their BVH has fewer joints than
            # the clip has channels. It cannot go in the file: a comment above
            # HIERARCHY is what Blender's importer chokes on.
            if r["note"]:
                self.send_header("X-RDR-Note", r["note"])
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if route == "rig_skel":
            # The skeleton TEXT for any label the picker offers, including the
            # ones that are not files. An accessory rig is a slice of a
            # character's own .skel, offered as "ct2 :: skel_hair", so a viewer
            # that loads rigs by filename had nothing to fetch and fell through
            # to whatever sorted first -- measured, 28 clips in SWAT.DAT are
            # offered ONLY an accessory rig.
            arc = one("archive", "")
            label = one("skel", "")
            clip = one("name", "")
            if not arc or not label:
                return self._send(400, {"error": "need an archive and a skel"})
            from . import skeleton as SK
            try:
                if "::" not in label:
                    body = s.disc.read(arc, label)
                else:
                    want = int(one("bones", "0") or 0)
                    hit = next((b for lbl, b, _n, _w
                                in s.rigs_for_clip(arc, want, clip)
                                if lbl == label), None)
                    if hit is None:
                        return self._send(404, {"error": "no rig named %r for "
                                                "this clip" % label})
                    body = SK.to_text(hit).encode("utf-8")
            except Exception as ex:                        # noqa: BLE001
                return self._send(404, {"error": str(ex)})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if route == "anim":
            # A decoded animation: the header, which channels move, and the
            # actual curves. See rdrstudio/anim.py -- the LAYOUT is settled and
            # verified byte-exact on all 3,771 cm2 files, but what the channels
            # MEAN is not, so this reports numbers and says so rather than
            # claiming to pose a skeleton.
            from . import anim as A
            from . import skeleton as SK
            arc, name = one("archive"), one("name")
            try:
                data = s.disc.read(arc, name)
            except Exception as ex:
                return self._send(404, {"error": str(ex)})
            magic = A.magic_of(data)
            # `magic` is bytes, and bytes are not JSON. Serialising it raw
            # made every ok=False reply below a 500, so all 182 uncompressed
            # clips lost their Curves view in Files AND their skeleton
            # suggestions in the viewer -- the error path itself was broken.
            tag = (magic[:3].decode("ascii")
                   if all(32 <= b < 127 for b in magic[:3]) else magic.hex())

            def fitting_skels(want):
                # One line, because the rule lives in Studio.rigs_for_clip and
                # the BVH export route asks the same function. What used to be
                # here was a second copy of it, and the copies drifted until
                # the picker was offering rigs the exporter refused.
                return [lbl for lbl, _b, _n, _w in
                        s.rigs_for_clip(arc, want, name)]

            # cmp is a second codec with its own runtime class (read at
            # 0x002D35F0 / 0x002D3F08). It exposes the same surface as cm2 --
            # channels, keys, at_key, animated -- so everything downstream
            # works unchanged.
            if data[:4] == b"cmp\x00":
                from . import animcmp as CM
                try:
                    a = CM.parse(data)
                except CM.CmpError as ex:
                    return self._send(200, dict(
                        ok=False, name=name, magic=tag, bytes=len(data),
                        error=str(ex)))
            elif data[:4] == b"\x00\x00\x00\x00":
                # The third codec: no compression, just float32 per channel
                # per key after a 17-byte header. 181 clips, and all of them
                # used to be refused here because anim.parse only accepts cm2
                # -- which is why every accessory animation (hair, scarf, coat
                # tail, jaw) looked broken. See animraw.py.
                from . import animraw as RW
                try:
                    a = RW.parse(data)
                except RW.RawError as ex:
                    return self._send(200, dict(
                        ok=False, name=name, magic=tag, bytes=len(data),
                        error=str(ex)))
            else:
                try:
                    a = A.parse(data)
                except A.AnimError as ex:
                    # Say WHICH codec: the remaining undecoded .anim files are
                    # a different format rather than broken.
                    summary_out = dict(
                        ok=False, name=name, magic=tag, bytes=len(data),
                        error=str(ex),
                        note=("all three codecs decode here now -- cm2, cmp "
                              "and the uncompressed one -- so reaching this "
                              "branch means the file is none of them, or is "
                              "damaged."))
                    # The uncompressed codec (see scripts/verify_anim_raw.py,
                    # read off the loader at 0x002CF320): a 17-byte header
                    # then f32[channels*keys], nothing else. When the size
                    # matches that formula to the byte, the header's channel
                    # count is trustworthy -- so the viewer still gets told
                    # which skeletons fit, instead of a blank picker.
                    if len(data) >= 17:
                        import struct as _struct
                        frames = _struct.unpack_from("<I", data, 4)[0] & 0xffff
                        channels = _struct.unpack_from("<I", data, 8)[0] & 0xffff
                        keys = (frames // 2 + 1) if (data[0x10] & 0x08) \
                            else frames
                        if (len(data) == 17 + 4 * channels * keys
                                and channels >= 6
                                and (channels - 3) % 3 == 0):
                            summary_out.update(channels=channels,
                                       bones=(channels - 3) // 3,
                                       skeletons=fitting_skels(
                                           (channels - 3) // 3))
                    return self._send(200, summary_out)
            summary_out = a.to_json()
            summary_out.update(ok=True, name=name, archive=arc, magic=tag)
            # Curves, downsampled to something a browser can draw. Every
            # animated channel, plus the three the game scales separately.
            animated_ch = [c for c in range(a.channels) if a.animated(c)]
            step = max(1, len(animated_ch) // 48)
            # at_key() dequantises the WHOLE channel vector, so asking it once
            # per (curve, key) did that work up to 48 times over. Once per key
            # instead: same numbers, and it is the bulk of what the Animation
            # tab waits on every time you click a clip.
            keyvals = [a.at_key(k) for k in range(a.keys)]
            summary_out["curves"] = [
                dict(channel=c,
                     group="A (channels 0-2)" if c < 3 else "B (channels 3+)",
                     values=[round(keyvals[k][c], 6) for k in range(a.keys)])
                for c in animated_ch[::step]
            ]
            # NOT `constant_channels` -- to_json() already uses that name for
            # the COUNT, and overwriting it with a list would silently break
            # anything reading the summary.
            summary_out["constants"] = [
                dict(channel=c, value=round(keyvals[0][c], 6) if keyvals
                     else round(a.at_key(0)[c], 6))
                for c in range(a.channels) if not a.animated(c)
            ][:64]
            summary_out["curves_shown"] = len(summary_out["curves"])
            # Which skeletons this clip could be exported against. Gated on
            # the 3 + 3*bones layout; *measured* over SWAT.DAT, all 4,005
            # decodable clips (3,771 cm2 + 234 cmp) satisfy it.
            want = (a.channels - 3) // 3
            rigs = (s.rigs_for_clip(arc, want, name)
                    if a.channels == 3 + 3 * want else [])
            summary_out["bones"] = want
            # EVERY name in this list can drive this clip. There is no such
            # thing as a wrong choice here any more, so the front end offers
            # exactly these and nothing else -- it used to list all 236
            # skeletons in the archive and tag the unusable ones "wrong bone
            # count", which is a warning about a mistake the tool invited.
            summary_out["skeletons"] = [lbl for lbl, _b, _n, _w in rigs]
            # ...and of those, the ones a BVH file can actually represent, so
            # the Export button can be right BEFORE it is pressed. Only a
            # multi-root skeleton is excluded, and there are 2 on the disc.
            summary_out["exportable"] = [lbl for lbl, _b, _n, w in rigs if w]
            summary_out["skeleton_notes"] = {lbl: n for lbl, _b, n, _w in rigs if n}
            return self._send(200, summary_out)

        if route == "level_events":
            # Everything a level contains that is NOT a placed matrix: the
            # doors into other areas, the named camera shots, the glows, the
            # sun, the flag list, the AI spawn groups, and -- for a train
            # level -- the module layout that turns a pile of pieces at the
            # origin into a kilometre of track. See rdrstudio/levelmeta.py;
            # every derived number is flagged as derived.
            from . import levelmeta
            arc = one("archive")
            lvl = s.level(arc, area=one("area") or None)
            # a level only the mods folder has is read from its own files (the disc does not have it: it showed no
            # doors, cameras, glows or AI spawn points at all)
            src = _LevelFiles(lvl) if s._mods_folder_of(arc) else s.disc
            return self._send(200, levelmeta.everything(src, arc, lvl))

        if route == "level_props":
            # The environment files belonging to one level area, grouped so
            # the editor can offer "the weather and lighting of this map"
            # rather than a flat file list.
            from . import props as P
            lvl = s.level(one("archive"), area=one("area") or None)
            # `rsc_name` is only filled in once the bundle has been loaded,
            # and this route does not need the bundle. `area` is the .rsc
            # filename and is set during _load(), so derive the prefix from
            # that instead -- files are named lvl_<area>_<thing>.<ext>.
            stem = (lvl.area or lvl.rsc_name or "").rsplit(".", 1)[0].lower()
            groups = []
            for title, exts in ENV_GROUPS:
                rows = []
                for e in s.disc.archive(lvl.archive):
                    if e.ext not in exts:
                        continue
                    # Prefer this area's own files; a Brimstone archive
                    # holds seven areas' worth and mixing them is how the
                    # bar's fog ends up on the gunshop.
                    own = bool(stem) and e.name.lower().startswith(stem)
                    rows.append(dict(name=e.name, ext=e.ext, own=own,
                                     bytes=e.size_full,
                                     headroom=e.slot - e.size_comp))
                if rows:
                    rows.sort(key=lambda r: (not r["own"], r["name"]))
                    groups.append(dict(title=title, files=rows))
            return self._send(200, dict(archive=lvl.archive,
                                        area=lvl.area or lvl.rsc_name,
                                        stem=stem, groups=groups))

        if route == "search_content":
            # Search INSIDE decompressed file contents, disc-wide or scoped.
            term = (one("q") or "").encode("latin-1", "ignore")
            if len(term) < 3:
                return self._send(200, dict(hits=[], note="need 3+ characters"))
            scope = one("archive")
            ext = (one("ext") or "").lower().lstrip(".")
            limit = min(int(one("limit", "200")), 1000)
            ci = one("ci", "1") == "1"
            needle = term.lower() if ci else term
            hits = []
            archives = [scope] if scope else s.disc.archives()
            for arc in archives:
                if len(hits) >= limit:
                    break
                try:
                    d = s.disc.archive(arc)
                except Exception:
                    continue
                for e in d:
                    if len(hits) >= limit:
                        break
                    if ext and e.ext != ext:
                        continue
                    try:
                        raw = d.read(e)
                    except Exception:
                        continue
                    hay = raw.lower() if ci else raw
                    pos = hay.find(needle)
                    if pos < 0:
                        continue
                    ctx = raw[max(0, pos - 40):pos + len(term) + 60]
                    hits.append(dict(
                        archive=arc, name=e.name, ext=e.ext, offset=pos,
                        count=hay.count(needle),
                        context="".join(chr(c) if 32 <= c < 127 else "."
                                        for c in ctx)))
                s.disc._cache.pop(arc.upper(), None)
            return self._send(200, dict(hits=hits, capped=len(hits) >= limit,
                                        archives=len(archives)))

        if route == "search":
            term = (one("q") or "").lower()
            if len(term) < 2:
                return self._send(200, [])
            # Collect EVERYTHING, then sort, then cut. The old code broke out
            # of the loop at 500 and sorted afterwards, so what came back was an
            # arbitrary 500 in index order that had merely been sorted -- a
            # subset that looks alphabetical and is not, with no hint that
            # anything was missing.
            try:
                limit = max(1, min(20000, int(one("limit") or 500)))
            except ValueError:
                limit = 500
            hits = []
            idx = s.disc.build_index()
            for name, arcs in idx.items():
                if term in name:
                    hits.append(dict(name=name, archives=arcs, n=len(arcs)))
            hits.sort(key=lambda h: h["name"])
            total = len(hits)
            return self._send(200, dict(total=total, shown=min(total, limit),
                                        truncated=total > limit,
                                        hits=hits[:limit]))

        if route == "log":
            return self._send(200, s.log[-200:])

        if route == "warmup":
            # Lock-free on purpose: this is what the boot overlay polls while
            # the warm thread HOLDS the lock. Behind the lock it would answer
            # exactly when it is no longer needed.
            return self._send(200, s.warm)

        if route == "job":
            # Lock-free for the same reason as warmup: it is asked exactly
            # while a job holds the lock.
            j = s.job
            if j is None:
                return self._send(200, dict(running=False))
            return self._send(200, dict(
                running=True, label=j.get("label", ""),
                seconds=round(time.time() - j.get("started", time.time()), 1),
                cancelling=bool(j.get("cancel"))))

        if route == "diff":
            return self._send(200, s.diff())

        # Routes added by the 2026-08 redesign. They live in their own module
        # and answer in their own envelope; nothing above this line changed.
        from . import routes2
        if routes2.has_get(route):
            return routes2.handle_get(self, s, route, q)

        # Save Editor (rdr-save). Same delegation shape as routes2 above; the
        # module lives in 03_Tools/savedata/ and owns its own envelope.
        from savedata import routes_save
        if routes_save.has_get(route):
            return routes_save.handle_get(self, s, route, q)

        return self._send(404, {"error": "no such route: %s" % route})

    # ------------------------------------------------------------------ POST

    # ------------------------------------------------------------ DISC > Data Editor

    def _de_get(self, route, q):
        from . import dataeditor as _dem
        de = _data_editor(self.studio)
        one = lambda k, d=None: (q.get(k) or [d])[0]  # noqa: E731
        if route == "de_job":
            return self._send(200, de.job_status())
        if route == "de_overview":
            return self._send(200, de.overview())
        if route == "de_file":
            base = de.workbench if one("in") == "workbench" else None
            data = de.read_file(one("key", ""), base)
            text = None
            if de.is_text(de._safe(one("key", ""), base)) and len(data) < 4 << 20:
                text = data.decode("latin-1")
            return self._send(200, dict(key=one("key"), size=len(data), text=text,
                                        hex=None if text is not None else data[:4096].hex()))
        if route == "de_archive_names":
            return self._send(200, dict(names=de.archive_names(one("archive", ""))))
        if route == "de_levels":
            arcs = de.archives()
            mp = sorted({a.split("/")[1][:-5] for a in arcs if a.startswith("lvls_m2/")})
            sng = sorted({a.split("/")[1][:-4] for a in arcs if a.startswith("lvls_sng/")})
            return self._send(200, dict(showdown=mp, story=sng))
        if route.startswith("de_x_"):
            # the rest of the RDR Data Editor app: Workbench, Saved Mods, HUD text ... (dataeditor_more.py)
            from . import dataeditor_more as _more
            try:
                r = _more.handle_get(self, route, q)
            except (ValueError, OSError, KeyError) as ex:
                return self._send(200, dict(ok=False, error_text=str(ex)))
            if r is not None:
                code, body, ctype = r
                return self._send(code, body, ctype) if ctype else self._send(code, body)
        return self._send(404, {"error": "no such route: %s" % route})

    def _de_post(self, route, body):
        from . import dataeditor as _dem
        de = _data_editor(self.studio)
        if route.startswith("de_manual") and self.client_address[0] not in ("127.0.0.1", "::1", "localhost"):
            # These name paths on this machine to read and write. The Studio
            # can be served on a LAN (--lan); a network peer may not ask it to
            # write anywhere on the disk. Only this machine's own window may.
            return self._send(200, dict(ok=False, error_text="Manual extracting/building works only in the Studio on "
                                        "this computer, not over the network."))
        try:
            if route == "de_add":
                # every file as it is given, Xbox ones too: nothing converted on the way in (the PS2 build makes a
                # PS2 copy of an Xbox file the mod has no PS2 file for; the Xbox build uses the Xbox files)
                return self._send(200, dict(added=de.add_files(body.get("folder", ""), body.get("files") or []),
                                            notes=[]))
            if route == "de_dat_place":
                return self._send(200, de.dat_place(body.get("name", ""), body.get("into", "")))
            if route == "de_import":
                notes = [de.import_archive(f["name"], base64.b64decode(f["data"]), body.get("into", ""),
                                           bool(body.get("replace")))
                         for f in body.get("files") or []]
                return self._send(200, dict(results=notes))
            if route == "de_delete_dat":
                # Data Editor: Delete .dat (a new level: both halves, its menu row, thumbnail and unlock) -- 2026-11-18
                try:
                    return self._send(200, de.delete_dat(body.get("archive", ""), bool(body.get("permanent")),
                                                         bool(body.get("dry"))))
                except ValueError as ex:
                    return self._send(400, {"error": str(ex)})
            if route == "de_delete":
                return self._send(200, dict(deleted=de.delete_files(body.get("keys") or [],
                                                                    bool(body.get("permanent")))))
            if route == "de_save":
                base = de.workbench if body.get("in") == "workbench" else None
                de.save_text(body.get("key", ""), body.get("text", ""), base)
                return self._send(200, dict(ok=True))
            if route == "de_mode":
                de.set_master_mode(body.get("names") or [], "add" if body.get("mode") == "add" else "replace")
                return self._send(200, dict(ok=True))
            if route == "de_from_game":
                return self._send(200, dict(copied=de.from_game(body.get("archive", ""), body.get("names") or [],
                                                                to=body.get("to") or None), to=body.get("to") or "dat"))
            if route == "de_adopt":
                return self._send(200, dict(ok=de.adopt(body.get("archive", ""))))
            if route == "de_cancel":
                return self._send(200, de.cancel())
            if route in ("de_build", "de_restore", "de_newdat"):
                de.check_pcsx2()          # refused at once, with the reason, instead of from inside the job
            if route == "de_build":
                return self._send(200, dict(started=de.start("Build into the working disc", de.build)))
            if route == "de_restore":
                return self._send(200, dict(started=de.start("Restore", de.restore, body.get("archives") or [])))
            if route == "de_newdat":
                return self._send(200, dict(started=de.start(
                    "New .dat", de.new_level, "sng" if body.get("kind") == "sng" else "showdown",
                    body.get("name", ""), body.get("template") or None, body.get("title") or None,
                    body.get("subtitle") or None, bool(body.get("replace")), bool(body.get("no_geometry")),
                    bool(body.get("blank")))))
            if route == "de_manual_extract":
                return self._send(200, dict(started=de.start(
                    "Extract an archive", de.manual_extract, body.get("archive", ""), body.get("to", ""))))
            if route == "de_manual_build":
                return self._send(200, dict(started=de.start(
                    "Build an archive", de.manual_build, body.get("folder", ""), body.get("output", ""))))
            if route == "de_manual_extract_all":
                return self._send(200, dict(started=de.start(
                    "Extract every archive", de.manual_extract_all, body.get("to", ""))))
            if route.startswith("de_x_"):
                from . import dataeditor_more as _more
                r = _more.handle_post(self, route, body)
                if r is not None:
                    return self._send(200, r)
        except _dem.Busy as ex:
            return self._send(200, dict(ok=False, busy=True, error_text=str(ex)))
        except (ValueError, OSError, KeyError, zipfile.BadZipFile) as ex:
            return self._send(200, dict(ok=False, error_text=str(ex)))
        return self._send(404, {"error": "no such route: %s" % route})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            # `play` hands the disc over itself; opening it first only to
            # close it again is pointless and widens the race.
            #
            # Everything else here is an intent to MODIFY, so it reclaims the
            # disc outright (force=True) rather than waiting out the handover
            # grace period. That matters for correctness, not just latency:
            # read-modify-write routes read through `s.disc`, which falls back
            # to the PRISTINE image while the working one is released -- so
            # without this a "change this string" would read vanilla bytes,
            # diff them against vanilla, and decide nothing needed writing.
            # If the emulator genuinely holds the file, `write()` still
            # refuses and nothing is written.
            # `settings` writes a preferences file under %LOCALAPPDATA% and
            # never reads or writes the disc. Reclaiming the ISO for it would
            # steal the file back from a running PCSX2 every time somebody
            # ticked a checkbox in the View panel.
            # The exports only READ the working ISO, and taking it back from
            # a running PCSX2 to copy it would stop the game the user is
            # testing. mod_import touches neither disc.
            if parsed.path[5:] not in ("boot", "release", "play", "settings",
                                       "settings_reset", "export_patch",
                                       "export_iso", "export_open",
                                       "mod_import", "job_cancel",
                                       "codemods_build") and not parsed.path[5:].startswith("de_"):
                self.studio.ensure_open(force=True)
            return self._api_post(parsed.path[5:], self._body())
        except Exception as ex:
            import traceback
            traceback.print_exc()
            return self._send(500, {"error": "%s: %s" % (type(ex).__name__, ex)})

    def _api_post(self, route, body):
        s = self.studio

        if not _feature_allows(route):
            return self._send(404, {"error": "no such route: %s" % route})

        if route.startswith("de_"):
            return self._de_post(route, body)

        if route == "character_rsc_import_all":
            # Creatures > Replace All Characters: ONE character bundle (.rsc + .rnt, or the Xbox's .xrsc + .xrnt) over
            # the model of every humanoid character -- every character whose skeleton has the bone count the model is
            # rigged to (the others, animals and the like, would crash on it and are left as they are). Each target's
            # copy has its meshes named for that character (<render>_lod_01 ...); an Xbox bundle is made the PS2's
            # with each target's skeleton. Every copy of every bundle on the disc, as ONE undo step; an archive with no
            # room is rebuilt on the next Build or Play. `dry` lists what it would do.
            import base64 as _b64
            import re as _re
            import sys as _sys
            from . import dataeditor as _dem
            if _dem._LIB not in _sys.path:
                _sys.path.insert(0, _dem._LIB)
            rsc_in = _b64.b64decode(body.get("rsc_b64") or "")
            rnt_in = _b64.b64decode(body.get("rnt_b64") or "") if body.get("rnt_b64") else None
            xbox_in = (body.get("rsc_name") or "").lower().endswith(".xrsc")
            if rnt_in is None:
                return self._send(200, dict(ok=False, error_text=(
                    "Pick the names file too: the .rnt with the .rsc (or the .xrnt with the .xrsc)")))
            import modconvert as _MC
            # the bone count the model is rigged to
            try:
                if xbox_in:
                    _tex, meshes = _MC.read_bundle(rsc_in, rnt_in, True)
                    want = max([(m.header_totals or (0, 0, 0))[2] for _n, m in meshes] or [0])
                else:
                    if rsc_in[:4] != b"RSC0":
                        return self._send(200, dict(ok=False, error_text=(
                            "That is not a compiled bundle (.rsc): it does not start with RSC0")))
                    from .rsc import Rsc as _Rsc
                    r0 = _Rsc(rsc_in, "model_lods.rsc")
                    want = max([r0.bone_count(c.index) for c in r0.chunks if c.tag == "MOD0"] or [0])
            except Exception as ex:                             # noqa: BLE001
                return self._send(200, dict(ok=False, error_text="The bundle could not be read: %s" % ex))
            if not want:
                return self._send(200, dict(ok=False, error_text="The bundle has no rigged model in it"))
            # every character: its .cr's model bundle and skeleton
            # humanoid = a skeleton with at least that many bones whose first ones are the same joints as the model's
            # rig (the 52 of the standard humanoid skeleton: gbw's 59, Black's 62, Red's 53 add tails and coattails
            # after them; ani, ct2 and dar order theirs differently, and the animals have fewer)
            from . import skeleton as _SK
            swat = s.disc.archive("SWAT.DAT")
            names_of = {}

            def _bone_names(skel):
                if skel not in names_of:
                    try:
                        names_of[skel] = [x.name for x in _SK.parse(swat.read(skel + ".skel"))[2]]
                    except Exception:                           # noqa: BLE001
                        names_of[skel] = None
                return names_of[skel]
            chars = []
            for e in swat:
                if not e.name.lower().endswith(".cr"):
                    continue
                code = e.name.lower()[:-3]
                try:
                    crt = swat.read(e.name).decode("latin-1")
                except Exception:                               # noqa: BLE001
                    continue
                mr = _re.search(r"^\s*FileNameRender\s+(\S+)", crt, _re.M)
                ms = _re.search(r"^\s*FileNameSkel\s+(\S+)", crt, _re.M)
                render = (mr.group(1) if mr else code).lower()
                skel = (ms.group(1) if ms else code).lower()
                if s.disc.locate(render + "_lods.rsc"):
                    chars.append((code, render, skel))
            # the model's rig: the joints of the skeleton most characters with exactly that many bones play on
            from collections import Counter as _Counter
            exact = _Counter(sk for _c, _r, sk in chars if len(_bone_names(sk) or ()) == want)
            ref_skel = exact.most_common(1)[0][0] if exact else None
            ref = (_bone_names(ref_skel) or [])[:want] if ref_skel else None
            by_render, skipped = {}, {}
            for code, render, skel in chars:
                nm = _bone_names(skel)
                if not nm or not ref or len(nm) < want or nm[:want] != ref:
                    skipped.setdefault(render, (code, skel, len(nm or ())))
                    continue
                t = by_render.setdefault(render, dict(render=render, skel=skel, codes=[]))
                t["codes"].append(code)
            for r in by_render:
                skipped.pop(r, None)
            if not by_render:
                return self._send(200, dict(ok=False, error_text=(
                    "No character's skeleton matches the %d bones this model is rigged to" % want)))
            # each target's bundle: converted per skeleton (Xbox), then its meshes named for the target
            disc = s.disc

            class _Disc:
                def find(self, fname, prefer=()):
                    for a in disc.locate(fname):
                        try:
                            return a, disc.read(a, fname)
                        except Exception:                       # noqa: BLE001
                            continue
                    return None, None
            per_skel, notes, plan, bad = {}, [], [], []
            for render, t in sorted(by_render.items()):
                if xbox_in:
                    # the dry run sizes every copy with ONE conversion (they differ only in bone positions): the
                    # real run converts once per skeleton, which takes a while for a hundred of them
                    key = "_dry" if body.get("dry") else t["skel"]
                    if key not in per_skel:
                        nn = []
                        try:
                            per_skel[key] = _MC.convert_bundle(rsc_in, rnt_in, t["skel"] + "_lods", False, {},
                                                               [_Disc()], nn)
                        except Exception as ex:                 # noqa: BLE001
                            per_skel[key] = None
                            nn.append("%s: %s" % (t["skel"], ex))
                        notes.extend(nn)
                    got = per_skel[key]
                    if not got:
                        bad.append(render)
                        continue
                    rsc, rnt = got
                else:
                    rsc, rnt = rsc_in, rnt_in
                try:
                    rsc, rnt = _dem.DataEditor._own_bundle(rsc, rnt, render)
                except ValueError as ex:
                    return self._send(200, dict(ok=False, error_text=str(ex)))
                name = render + "_lods.rsc"
                copies = []
                for a in s.disc.locate(name):
                    fit = s.disc.archive(a).would_fit(name, rsc)
                    copies.append(dict(archive=a, fits=fit["fits"]))
                plan.append(dict(render=render, codes=t["codes"], name=name, rsc=rsc, rnt=rnt, copies=copies))
            summary = dict(want=want, characters=sum(len(p["codes"]) for p in plan), models=len(plan),
                           copies=sum(len(p["copies"]) for p in plan),
                           rebuilds=len({c["archive"] for p in plan for c in p["copies"] if not c["fits"]}),
                           skipped=len(skipped), skipped_some=sorted(skipped)[:12], failed=bad, notes=notes[:10],
                           models_list=[dict(render=p["render"], codes=p["codes"], copies=len(p["copies"])) for p in plan])
            if body.get("dry"):
                return self._send(200, dict(ok=True, dry=True, **summary))
            group = s.journal.new_group()
            for p in plan:
                stem = p["render"] + "_lods"
                for c in p["copies"]:
                    a = c["archive"]
                    s.write(a, p["name"], p["rsc"], "replace all characters: %s" % p["name"], group)
                    try:
                        s.disc.archive(a).find(stem + ".rnt")
                    except KeyError:
                        continue
                    s.write(a, stem + ".rnt", p["rnt"], "replace all characters: %s" % p["name"], group)
            s.note("Replace All Characters: %d models (%d characters) replaced in %d archive copies"
                   % (summary["models"], summary["characters"], summary["copies"]))
            return self._send(200, dict(ok=True, dry=False, **summary))

        if route == "character_rsc_import":
            # A character bundle exported by the RDR Blender add-on (.rsc, and
            # its .rnt) over every copy of that bundle on the disc, as ONE undo
            # step. An archive with no room for it is rebuilt to make room.
            import base64 as _b64
            from . import dataeditor as _dem
            name = (body.get("name") or "").lower()
            rsc = _b64.b64decode(body.get("rsc_b64") or "")
            rnt = _b64.b64decode(body.get("rnt_b64") or "") if body.get("rnt_b64") else None
            stem0 = name.rsplit(".", 1)[0]
            code0 = stem0[:-5] if stem0.endswith("_lods") else stem0
            notes = []
            if (body.get("rsc_name") or "").lower().endswith(".xrsc"):
                # the Xbox's bundle: made the PS2's, its meshes moved with THIS character's skeleton
                if rnt is None:
                    return self._send(200, dict(ok=False, error_text="Pick the .xrnt with the .xrsc (both)"))
                from .dataeditor_more import more as _more
                got, notes = _more(_data_editor(s)).xbox_to_ps2({stem0 + ".xrsc": rsc, stem0 + ".xrnt": rnt})
                if (stem0 + ".rsc") not in got:
                    return self._send(200, dict(ok=False, error_text="; ".join(n.replace("LEVEL ", "") for n in notes)
                                                or "the .xrsc could not be converted"))
                rsc, rnt = got[stem0 + ".rsc"], got[stem0 + ".rnt"]
            if rnt is not None and rnt[:4] == b"RNT0":
                # meshes named for another character (a model made as gbw, put on srd): renamed to this one's, as Add
                # Character does -- the game finds a character's meshes as <code>_lod_01 ...
                try:
                    rsc, rnt = _dem.DataEditor._own_bundle(rsc, rnt, code0)
                except ValueError as ex:
                    return self._send(200, dict(ok=False, error_text=str(ex)))
            if rsc[:4] != b"RSC0":
                return self._send(200, dict(ok=False, error_text="That is not a compiled bundle (.rsc): it does not start with RSC0"))
            stem = name.rsplit(".", 1)[0]
            code = stem[:-5] if stem.endswith("_lods") else stem
            if rnt is not None:
                if rnt[:4] != b"RNT0":
                    return self._send(200, dict(ok=False, error_text="The names file is not an .rnt (RNT0)"))
                import sys as _sys
                _sys.path.insert(0, _dem._LIB)
                from rdrformats import rsc as _rr
                mods = [n for t, _num, n in _rr.read_names(rnt) if t == "MOD0"]
                if code + "_lod_01" not in mods:
                    return self._send(200, dict(ok=False, error_text=(
                        "The .rnt names no %s_lod_01, the body the game loads for %s (it names: %s). "
                        "Export it from Blender with Model name %s_lod_01." % (code, code, ", ".join(mods) or "nothing", code))))
            copies = [a for a in s.disc.locate(name)] if body.get("everywhere", True) else [body.get("archive")]
            copies = [a for a in copies if a]
            if not copies:
                return self._send(200, dict(ok=False, error_text="No archive on the disc has %s" % name))
            # the model and the skeleton the character plays it on must agree on the bone count, or it crashes
            try:
                from .rsc import Rsc as _Rsc
                want = max([_Rsc(rsc, name).bone_count(c.index) for c in _Rsc(rsc, name).chunks if c.tag == "MOD0"] or [0])
                skel = None
                for a in ("SWAT.DAT",) + tuple(copies):
                    try:
                        crt = s.disc.archive(a).read(code0 + ".cr").decode("latin-1")
                        import re as _re
                        m = _re.search(r"^\s*FileNameSkel\s+(\S+)", crt, _re.M)
                        skel = m.group(1).lower() if m else None
                        break
                    except Exception:                          # noqa: BLE001
                        continue
                have = s.skeleton_index("SWAT.DAT").get((skel or code0) + ".skel")
                if want and have and want != have:
                    return self._send(200, dict(ok=False, error_text=(
                        "That model is rigged to %d bones and %s plays on %s.skel, which has %d: it would crash. Export it "
                        "on %s's skeleton (or a character with %d bones)." % (want, code0, skel or code0, have, code0, have))))
            except Exception:                                  # noqa: BLE001
                pass
            plan = []
            for a in copies:
                fit = s.disc.archive(a).would_fit(name, rsc)
                plan.append(dict(archive=a, stored=fit["stored"], slot=fit["slot"], fits=fit["fits"]))
            if body.get("dry"):
                return self._send(200, dict(dry=True, copies=plan, rebuilds=sum(1 for p in plan if not p["fits"]), notes=notes))
            group = s.journal.new_group()
            for a in copies:
                s.write(a, name, rsc, "character bundle %s" % name, group)
                if rnt is not None:
                    try:
                        s.disc.archive(a).find(stem + ".rnt")
                    except KeyError:
                        continue  # this copy of the bundle has no names file beside it
                    s.write(a, stem + ".rnt", rnt, "character bundle %s" % name, group)
            s.note("%s replaced in %d archive(s) from a .rsc import" % (name, len(copies)))
            return self._send(200, dict(ok=True, copies=plan, rebuilds=sum(1 for p in plan if not p["fits"]), notes=notes))

        if route == "creature_bundle":
            # the model bundle a character draws (its .cr's FileNameRender), for Replace model in Creatures
            code = (body.get("code") or "").lower().removesuffix(".cr")
            try:
                crt = s.disc.archive("SWAT.DAT").read(code + ".cr").decode("latin-1")
            except Exception:                                  # noqa: BLE001
                return self._send(200, dict(ok=False, error_text="SWAT.DAT has no %s.cr" % code))
            import re as _re
            m = _re.search(r"^\s*FileNameRender\s+(\S+)", crt, _re.M)
            render = m.group(1).lower() if m else code
            file = render + "_lods.rsc"
            return self._send(200, dict(ok=True, code=code, render=render, file=file, archive="SWAT.DAT",
                                        copies=len(s.disc.locate(file))))

        if route == "creature_delete":
            # BALANCE > Creatures > Delete Character: custom characters only
            # (a character the unedited disc has is refused). A Data Editor job.
            from . import dataeditor as _dem
            de = _data_editor(s)
            try:
                plan = de.delete_plan(body.get("code", ""))
                if body.get("dry"):
                    return self._send(200, dict(ok=True, **plan))
                if plan["base_game"]:
                    return self._send(200, dict(ok=False, error_text=(
                        "%s is one of the game's own characters: only characters you added can be deleted" % plan["code"])))
                return self._send(200, dict(ok=True, started=de.start(
                    "Delete character %s" % body.get("code", ""), de.delete_character, body.get("code", ""))))
            except (_dem.Busy, ValueError) as ex:
                return self._send(200, dict(ok=False, error_text=str(ex)))

        if route == "creature_add":
            # BALANCE > Creatures > Add Character: a new character slot in
            # SWAT.DAT, cloned from another (rdrstudio/dataeditor.py). Adds are
            # not undoable; Reset to stock takes it away.
            from . import dataeditor as _dem
            try:
                import base64 as _b64c
                rsc = _b64c.b64decode(body["rsc_b64"]) if body.get("rsc_b64") else None
                rnt = _b64c.b64decode(body["rnt_b64"]) if body.get("rnt_b64") else None
                skl = _b64c.b64decode(body["skel_b64"]) if body.get("skel_b64") else None
                extra = {f["name"]: _b64c.b64decode(f["data"]) for f in (body.get("extra") or [])
                         if f.get("name") and f.get("data")}
                # an Xbox model (.xrsc + .xrnt) and Xbox textures / models among the files: made the PS2's first
                from .dataeditor_more import more as _more
                xb = {}
                if rsc is not None and (body.get("rsc_name") or "").lower().endswith(".xrsc"):
                    stem = body["rsc_name"][:-5]
                    xb[stem + ".xrsc"], xb[stem + ".xrnt"] = rsc, rnt
                for n in list(extra):
                    if n.lower().endswith((".xtex", ".xmod", ".skel")):
                        xb[n] = extra[n] if not n.lower().endswith((".xtex", ".xmod")) else extra.pop(n)
                if skl is not None:
                    xb.setdefault((body.get("code") or "x") + ".skel", skl)
                xnotes = []
                if any(k.lower().endswith((".xrsc", ".xtex", ".xmod")) for k in xb):
                    got, xnotes = _more(_data_editor(s)).xbox_to_ps2(xb)
                    lvl = [n for n in xnotes if n.startswith("LEVEL ")]
                    if lvl:
                        raise ValueError(lvl[0][6:])
                    for n, d in got.items():
                        if n.lower().endswith(".rsc"):
                            rsc = d
                        elif n.lower().endswith(".rnt"):
                            rnt = d
                        elif not n.lower().endswith(".skel"):
                            extra[n] = d
                added = _data_editor(s).add_character(body.get("source", ""), body.get("code", ""), rsc, rnt, skl, extra,
                                                      body.get("copy"))
            except (_dem.Busy, ValueError) as ex:
                return self._send(200, dict(ok=False, error_text=str(ex)))
            return self._send(200, dict(ok=True, added=added, name=(body.get("code") or "").strip().lower() + ".cr",
                                        notes=locals().get("xnotes") or []))

        if route == "codemods_build":
            # Builds a NEW file, 02_Modded/RDR_CODEMODS.iso, from the stock or the working ISO.
            # Neither source is written to; the build runs on a thread and /api/codemods_status
            # reports it. Nothing is built unless at least one mod is ticked.
            from . import codemods
            feats = [k for k in (body.get("features") or []) if k in codemods.KEYS]
            base = s.vanilla_path if body.get("base") == "stock" else s.modded_path
            out = os.path.join(os.path.dirname(s.modded_path), "RDR_CODEMODS.iso")
            ok, why = codemods.start_build(base, out, feats)
            return self._send(200, dict(ok=ok, started=ok, problem=None if ok else why, output=out,
                                        base=base, features=feats))

        if route == "job_cancel":
            # Lock-free: the job it cancels is the one holding the lock. It
            # only raises a flag; the job stops at its next check.
            j = s.job
            if j is None:
                return self._send(200, dict(ok=True, cancelled=False))
            j["cancel"] = True
            return self._send(200, dict(ok=True, cancelled=True,
                                        label=j.get("label", "")))

        if route == "settings":
            # No `dry` here on purpose. Everywhere else in this API `dry`
            # separates a costed plan from a write to the disc; a preference
            # has no plan and no disc, and a settings route that could
            # silently no-op would be the same trap that cost a boot test
            # three runs.
            from . import settings as settings_mod
            try:
                return self._send(200, settings_mod.save(
                    body.get("settings", body)))
            except ValueError as ex:
                return self._send(400, dict(error=str(ex)))

        if route in ("profile_save", "profile_load", "profile_delete", "profile_import"):
            from . import settings as settings_mod
            try:
                name = body.get("name", "")
                if route == "profile_save":
                    return self._send(200, dict(profiles=settings_mod.save_profile(name, body.get("local") or {})))
                if route == "profile_load":
                    return self._send(200, dict(local=settings_mod.load_profile(name)))
                if route == "profile_delete":
                    return self._send(200, dict(profiles=settings_mod.delete_profile(name)))
                return self._send(200, dict(profiles=settings_mod.import_profile(name, body.get("profile"))))
            except ValueError as ex:
                return self._send(400, dict(error=str(ex)))

        if route == "note":
            # a line for the activity log from the page (the Build button's "Build complete", which spans a queued
            # rebuild and a Data Editor build -- neither knows when the other is done)
            msg = str(body.get("text") or "").strip()[:300]
            if msg:
                s.note(msg)
            return self._send(200, dict(ok=True))

        if route == "settings_reset":
            from . import settings as settings_mod
            return self._send(200, settings_mod.reset())

        if route == "place_asset":
            from . import assets as assets_mod
            try:
                result = assets_mod.place(
                    s, s.asset_index(), body["archive"], body["name"],
                    body.get("pos"), area=body.get("area"),
                    dry=bool(body.get("dry")), source=body.get("source"))
            except ValueError as ex:
                return self._send(400, dict(error=str(ex)))
            if result.get("applied"):
                s.note("%s: placed %s as %s" % (body["archive"], body["name"],
                                                result.get("new_name")))
            return self._send(200, result)

        if route == "move":
            grow = bool(body.get("grow"))
            lvl = s.level(body["archive"], manifest=body.get("manifest"),
                          area=body.get("area"))
            # Model + collision are ONE thing: a moved model's linked
            # collision shell moves by the same delta, server-side, in this
            # same undo step (no client opt-in).
            from . import collparts
            sets = []          # (anchor uid, member uids incl. anchor, new pos)
            alone = bool(body.get("alone"))   # "Edit collision on its own": no pairing either way
            if not body.get("manifest") and not alone:
                try:
                    live_rsc = lvl._load_rsc()
                    have = {int(c["uid"]) for c in body["changes"]}
                    added = []
                    for c in body["changes"]:
                        if "pos" not in c:
                            continue
                        n = lvl.by_uid(int(c["uid"]))
                        if n.kind != "zone" or live_rsc is None:
                            continue
                        mem = [n.uid]
                        if body.get("set", True):
                            d = [a - b for a, b in zip(c["pos"], n.xyz())]
                            for u in collparts.group_of(lvl, live_rsc, n.uid):
                                if u in have:
                                    if u != n.uid:
                                        mem.append(u)
                                    continue
                                m = lvl.by_uid(u)
                                added.append(dict(
                                    uid=u, via=n.uid,
                                    pos=[b + x for b, x in zip(m.xyz(), d)]))
                                have.add(u)
                                mem.append(u)
                        sets.append((n.uid, mem, [float(v) for v in c["pos"]]))
                    body["changes"] = list(body["changes"]) + added
                except Exception:
                    sets = []
            if not alone:
                body["changes"] = lvl.with_collision(body["changes"])
            group = s.journal.new_group()
            # A level in the mods folder: a model's own collision shell follows by its points (boundmove, on every
            # bundle the level has), not by its node's matrix -- the shipped levels' bound matrices are all identity,
            # and the editor draws a shell from its points, so a matrix move left it drawn behind (2026-11-18)
            shell_moves = []
            if not alone and not body.get("dry") and s._mods_folder_of(body["archive"]):
                keep = []
                for c in body["changes"]:
                    if "via" in c and "pos" in c:
                        bn = lvl.by_uid(int(c["uid"]))
                        if bn.kind == "bound":
                            shell_moves.append((bn.name, [a - b for a, b in zip(c["pos"], bn.xyz())]))
                            continue
                    keep.append(c)
                body["changes"] = keep
                # a model's other collision pieces (bound_x_01p2 ...) move with it too (2026-11-18)
                from . import modeladd as _MA
                bnames = [n.name for n in lvl.nodes if n.kind == "bound"]
                for c in keep:
                    if "pos" not in c:
                        continue
                    zn = lvl.by_uid(int(c["uid"]))
                    if zn.kind != "zone":
                        continue
                    d = [a - b for a, b in zip(c["pos"], zn.xyz())]
                    for pn in _MA.pieces_of(bnames, zn.name):
                        if not any(pn == m for m, _d in shell_moves):
                            shell_moves.append((pn, d))
            edits = lvl.merge(*[
                lvl.move(int(c["uid"]), c["pos"], allow_grow=grow)
                for c in body["changes"] if "pos" in c
            ] + [
                lvl.resize(int(c["uid"]), c["size"]) for c in body["changes"]
                if "size" in c
            ] + [
                lvl.rotate(int(c["uid"]), c["angle"]) for c in body["changes"]
                if "angle" in c
            ])
            label = body.get("label") or "move %d object(s)" % len(body["changes"])
            result = lvl.apply(edits, dry=bool(body.get("dry")),
                               writer=s.writer_for(body["archive"], label,
                                                   group=group, grow=grow),
                               allow_grow=grow)
            if shell_moves and result.get("applied"):
                from . import boundmove as _bm, collcreate as _cc
                st = _cc.folder_store(_data_editor_for(s), body["archive"])
                if st is not None:
                    st.writer = lambda n, d: s.write(body["archive"], n, d, label, group)
                    try:
                        for nname, d in shell_moves:
                            _f, text, span = _cc._locate(st, nname)
                            p_num, x_num = _cc._node_numbers(text, span)
                            for fname, xbox, num in ((st.ps2, False, p_num), (st.xbox, True, x_num)):
                                if not fname or num <= 0:
                                    continue
                                data = st.read(fname)
                                ch = _cc._lib()[2].read_chunks(data)
                                body_b = _bm.translate(ch[num - 1].data, d[0], d[1], d[2])
                                data, _i = _cc._set_chunk(data, num - 1, body_b, xbox)
                                st.write(fname, data)
                        result["shells_moved"] = [n for n, _d in shell_moves]
                        result["reload"] = True     # the bundle was rewritten: the kept Level would be stale
                    except Exception as ex:                 # noqa: BLE001
                        result["collision_note"] = "Its collision was not moved with it: %s" % ex
            if sets:
                result["set"] = [dict(uid=a, count=len(m)) for a, m, _ in sets
                                 if len(m) > 1]
            # the merged shells' follow measures from the unedited disc's level: a level it has not (a new one, in
            # the mods folder) has no merged shells of the game's to follow -- its models' own shells moved above
            try:
                on_vanilla = body["archive"].upper() in {a.upper() for a in s.vanilla.archives()}
            except Exception:                               # noqa: BLE001
                on_vanilla = False
            if result.get("applied") and sets and not body.get("dry") and on_vanilla:
                # The collision welded into the merged shells follows, in the
                # same undo step. Deltas are measured from STOCK (no drift).
                try:
                    stock = Level(s.vanilla, body["archive"], area=body.get("area"))
                    sp = {}
                    for n in stock.nodes:
                        if n.kind == "zone":
                            sp.setdefault(n.name, []).append(n.xyz())
                    moves = []
                    newpos = {int(c["uid"]): c["pos"] for c in body["changes"]
                              if "pos" in c}

                    def _tr(u):
                        q = sp.get(lvl.by_uid(u).name) or []
                        if len(q) != 1 or u not in newpos:
                            return None
                        d = [float(a) - b for a, b in zip(newpos[u], q[0])]
                        return lambda x, y, z, d=d: (x + d[0], y + d[1], z + d[2])
                    for a, mem, _ in sets:
                        for u in mem:
                            if u != a and _tr(u):
                                moves.append(([u], _tr(u)))
                    for a, mem, _ in sets:
                        if _tr(a):
                            moves.append((mem, _tr(a)))
                    cur = s.disc.archive(body["archive"]).read(lvl.rsc_name)
                    nb, finfo = _follow_blob(s, body, lvl, cur, moves)
                    if nb != cur:
                        s.write(body["archive"], lvl.rsc_name, nb, label, group)
                        result["reload"] = True
                    result["collision"] = finfo
                    fnote = _follow_note(finfo)
                    if fnote:
                        result["collision_note"] = fnote
                except Exception as ex:
                    result["collision_note"] = ("Its collision was NOT moved "
                                                "(%s: %s)." % (type(ex).__name__, ex))
            if result.get("applied"):
                s.note("%s: wrote %d change(s) to %d file(s)"
                       % (body["archive"], len(body["changes"]),
                          len(result["written"])))
                # apply() has already reparsed this Level in place; putting it
                # back in the cache spares the reload that follows every edit
                # a full rebuild (measured 1.14 s on TOMB per gizmo drag).
                # (Not when the geometry bundle was just rewritten: the cached
                # Level would hold stale chunk offsets.)
                if not result.get("reload"):
                    s.keep_level(body["archive"], body.get("manifest"),
                                 body.get("area"), lvl)
                if any(c.get("via") is not None for c in body["changes"]):
                    result["reload"] = True
            return self._send(200, result)

        if route == "set_solid":
            # Per-model solid / walk-through (the ONLY collision control).
            lvl = s.level(body["archive"], manifest=body.get("manifest"),
                          area=body.get("area"))
            uid = int(body["uid"])
            if body.get("status"):
                st = dict(lvl.collision_status(uid))
                # Sets + collision welded into the merged shells (collparts).
                try:
                    from . import collparts
                    n0 = lvl.by_uid(uid)
                    rs = lvl._load_rsc()
                    if n0.kind == "zone" and rs is not None \
                            and not body.get("manifest"):
                        g = collparts.group_of(lvl, rs, uid)
                        st["set"] = [lvl.by_uid(u).name for u in g if u != uid]
                        if lvl.links().get(uid) is None:
                            c = collparts.claim(lvl, rs, g)
                            st["merged"] = dict(pieces=c["pieces"], faces=c["faces"])
                            if c["pieces"]:
                                st["note"] = (
                                    "Its collision is welded into the level's shared "
                                    "collision (%d piece(s)). It moves with the model; "
                                    "nothing is left behind." % c["pieces"])
                            else:
                                st["note"] = (
                                    "No collision piece sits wholly inside this model, "
                                    "so moving it moves the picture only.")
                except Exception:
                    pass
                return self._send(200, st)
            try:
                edits = lvl.set_solid(uid, bool(body.get("solid", True)))
            except ValueError as ex:
                return self._send(400, {"error": str(ex)})
            n = lvl.by_uid(uid)
            return self._send(200, lvl.apply(
                edits, dry=bool(body.get("dry")),
                writer=s.writer_for(body["archive"], "%s: %s" % (
                    n.name, "solid" if body.get("solid", True) else "walk-through"))))

        if route == "set_field":
            lvl = s.level(body["archive"], manifest=body.get("manifest"),
                          area=body.get("area"))
            n = lvl.by_uid(int(body["uid"]))
            # `Table` and `Type` name files that must exist on disc, and a
            # typo in either is a crash rather than a visible mistake. They
            # are checked against the archive's own `.cr`/`.tbl` list before
            # anything is spliced. This route's shape is unchanged -- it can
            # only now refuse a value it would previously have written.
            if body["key"] in ("Table", "Type"):
                from . import cast as _cast
                doc = _cast.cast(s.disc, body["archive"])
                resolve = (_cast.resolve_table if body["key"] == "Table"
                           else _cast.resolve_type)
                fixed = resolve(doc, body["value"])
                if fixed is None:
                    return self._send(400, {"error": (
                        "%s is not a %s in %s -- nothing was written"
                        % (body["value"],
                           "behaviour table" if body["key"] == "Table"
                           else "character", body["archive"]))})
                body = dict(body, value=fixed)
            result = lvl.apply(
                lvl.set_field(int(body["uid"]), body["key"], body["value"]),
                dry=bool(body.get("dry")),
                writer=s.writer_for(body["archive"],
                                    "%s: %s = %s" % (n.name, body["key"],
                                                     body["value"])))
            return self._send(200, result)

        if route == "duplicate":
            lvl = s.level(body["archive"], manifest=body.get("manifest"),
                          area=body.get("area"))
            edits, new_name = lvl.duplicate(int(body["uid"]),
                                            body.get("offset", (1.0, 0.0, 1.0)))
            result = lvl.apply(
                edits, dry=bool(body.get("dry")),
                writer=s.writer_for(body["archive"], "create %s" % new_name))
            result["new_name"] = new_name
            if result.get("applied"):
                s.note("%s: duplicated -> %s" % (body["archive"], new_name))
            return self._send(200, result)

        if route == "delete":
            lvl = s.level(body["archive"], manifest=body.get("manifest"),
                          area=body.get("area"))
            n = lvl.by_uid(int(body["uid"]))
            name = n.name
            result = lvl.apply(
                lvl.delete(int(body["uid"])), dry=bool(body.get("dry")),
                writer=s.writer_for(body["archive"], "delete %s" % name))
            if result.get("applied"):
                s.note("%s: deleted %s" % (body["archive"], name))
            return self._send(200, result)

        if route == "delete_many":
            # A box selection deleted as ONE action (2026-11-18). The browser used to send one delete per object,
            # finding the next by NAME after each (names repeat: player1_spawn_1 went twice) and stopping at the
            # first refusal, so a box select left most of what it held behind. Here: every target named up front,
            # deleted from the highest uid down -- uids are positions in the node list, so the ones below a deleted
            # node keep theirs -- one journal group, one Ctrl+Z; a refusal is reported and the rest still go.
            # links: a model's own collision shell (bound_X of zone_X) goes with it.
            lvl = s.level(body["archive"], manifest=body.get("manifest"), area=body.get("area"))
            uids = {int(u) for u in body.get("uids") or []}
            if body.get("links", True):
                lk = lvl.links()
                from . import modeladd as _MA0
                bnodes = {n.name: n.uid for n in lvl.nodes if n.kind == "bound"}
                for u in list(uids):
                    try:
                        zn = lvl.by_uid(u)
                        if zn.kind == "zone" and lk.get(u) is not None:
                            uids.add(lk[u])
                        if zn.kind == "zone":
                            uids |= {bnodes[pn] for pn in _MA0.pieces_of(list(bnodes), zn.name)}
                    except KeyError:
                        pass
            want = {}
            for u in uids:
                try:
                    want[u] = lvl.by_uid(u).name
                except KeyError:
                    pass
            label = body.get("label") or ("delete %d object(s)" % len(want) if len(want) != 1
                                           else "delete %s" % next(iter(want.values())))
            group = s.journal.new_group()
            deleted, failed = [], []
            # a level only the mods folder has: the chunks of the models and shells deleted are emptied too (below),
            # their numbers read now, before the nodes go
            chunks_of = {}
            mstore = None
            if s._mods_folder_of(body["archive"]):
                from . import collcreate as _CC, modeladd as _MA
                mstore = _CC.folder_store(_data_editor_for(s), body["archive"])
                if mstore is not None:
                    for u, nm in want.items():
                        try:
                            k = lvl.by_uid(u).kind
                            if k == "zone":
                                chunks_of[nm] = ("zone", _MA._zone_nums(mstore, nm))
                            elif k == "bound":
                                chunks_of[nm] = ("bound", _MA._bound_nums(mstore, nm))
                        except Exception:                   # noqa: BLE001
                            pass
            for u in sorted(want, reverse=True):
                try:
                    n = lvl.by_uid(u)
                    if n.name != want[u]:
                        raise ValueError("the level changed under it (uid %d is now %s)" % (u, n.name))
                    res = lvl.apply(lvl.delete(u), writer=s.writer_for(body["archive"], label, group=group))
                    if not res.get("applied"):
                        raise ValueError(res.get("error") or "the file would not take it")
                    deleted.append(want[u])
                except Exception as ex:                     # noqa: BLE001
                    failed.append(dict(name=want[u], error="%s" % ex))
            if mstore is not None and deleted and chunks_of:
                # emptied only where no node draws that chunk any more (a placed copy can share its source's)
                try:
                    mstore.writer = lambda n, d: s.write(body["archive"], n, d, label, group)
                    users = _MA.chunk_users(mstore)
                    zones_e, bounds_e = [], []
                    for nm in deleted:
                        kind, nums = chunks_of.get(nm, (None, {}))
                        free = {p: n for p, n in nums.items() if not users.get((kind, p, n))}
                        if not free:
                            continue
                        if kind == "zone":
                            zones_e.append(free)
                        elif kind == "bound":
                            bounds_e.append((free.get("ps2", 0), free.get("xbox", 0)))
                    if zones_e:
                        _MA.empty_model_chunks(mstore, zones_e)
                    if bounds_e:
                        _CC._empty_chunks(mstore, bounds_e)
                    s._drop_level(body["archive"])
                except Exception as ex:                     # noqa: BLE001
                    s.note("%s: the deleted models' chunks were not emptied (%s)" % (body["archive"], ex))
            if deleted:
                s.note("%s: deleted %d object(s): %s" % (body["archive"], len(deleted), ", ".join(deleted[:40])))
            for f in failed:
                s.note("%s: could not delete %s: %s" % (body["archive"], f["name"], f["error"]))
            return self._send(200, dict(applied=bool(deleted), deleted=deleted, failed=failed))

        if route == "env_preset":
            # A whole look, applied across the level's lighting and fog files
            # as ONE undo step. Dry-run everything before writing anything --
            # a preset that fitted one file and overflowed the next would
            # leave the level half-changed.
            from . import props as P
            key = body.get("preset")
            preset = ENV_PRESETS.get(key)
            if not preset:
                return self._send(400, {"error": "no preset %r" % key})
            lvl = s.level(body["archive"], area=body.get("area"))
            stem = (lvl.area or lvl.rsc_name or "").rsplit(".", 1)[0].lower()
            dv = s.disc.archive(lvl.archive)

            targets = []
            for ext, field_edits in preset.items():
                if ext in ("label", "note"):
                    continue
                mine = [e for e in dv if e.ext == ext
                        and e.name.lower().startswith(stem)]
                if ext == "lgt":
                    # Only the world's own lighting; `_cre_` lights actors
                    # and `_shb_` the shadow blob.
                    world = [e for e in mine if "_lvl_" in e.name.lower()]
                    mine = world or mine[:1]
                for e in mine:
                    raw = dv.read(e.name)
                    present = {f.path: f for f in P.parse(raw)}
                    use = {}
                    for k, v in field_edits.items():
                        # Fields live at different depths per file: `.lgt` is
                        # flat, `.fog` nests under a Fog block.
                        hit = next((p for p in present
                                    if p == k or p.endswith("/" + k)), None)
                        if hit:
                            use[hit] = v
                    if use:
                        targets.append((e.name, raw, use))

            if not targets:
                return self._send(404, {"error": "this level has no lighting "
                                                 "or fog files to change"})

            planned = []
            for name, raw, use in targets:
                try:
                    out = P.apply(raw, use)
                except KeyError as ex:
                    return self._send(400, {"error": str(ex)})
                e = dv.find(name)
                if e.compressed:
                    import zlib
                    co = zlib.compressobj(9, 8, -15)
                    stored = len(co.compress(out) + co.flush())
                else:
                    stored = len(out)
                if stored > e.slot:
                    return self._send(200, dict(
                        ok=False, fits=False, file=name,
                        error="%s would need %d bytes of a %d-byte slot"
                              % (name, stored, e.slot)))
                planned.append((name, out, len(use)))

            if body.get("dry"):
                return self._send(200, dict(dry=True, fits=True,
                                            files=[p[0] for p in planned],
                                            fields=sum(p[2] for p in planned)))

            group = s.journal.new_group()
            label = "%s: %s" % (lvl.area or lvl.archive, preset["label"])
            for name, out, _ in planned:
                s.write(lvl.archive, name, out, label, group)
            s.note("%s :: preset '%s' over %d file(s)"
                   % (lvl.archive, preset["label"], len(planned)))
            return self._send(200, dict(ok=True, label=preset["label"],
                                        files=[p[0] for p in planned],
                                        fields=sum(p[2] for p in planned)))

        if route == "geom_shape":
            # What transform is currently on this chunk, measured against
            # vanilla rather than remembered?
            #
            # geom_edit always derives from the stock disc, so the ops it
            # receives are a total. That total used to live only in the
            # browser, where it was lost on reload and went stale on undo --
            # the disc stepped back, the remembered total did not, and the
            # next drag re-applied the move that had just been undone. Reading
            # it back means the tool never has to remember it.
            from . import geomedit
            # This route froze the game for a night. The verts moved but the
            # per-part cull spheres (VIF unpack to VU 0x29, part_offset+12)
            # did not, and the VU microprogram consumes them at runtime --
            # verts-only froze the 185 s run, verts+spheres plays. Every
            # transform below now moves the spheres through the same maths.
            lvl = s.level(body["archive"], area=body.get("area"))
            chunk = int(body["chunk"])
            if s._mods_folder_of(body["archive"]):
                # a level only the mods folder has: no unedited copy -- the total since this session first
                # reshaped it (modeladd.reshape keeps that), 2026-11-18
                from . import modeladd as MA
                node = MA.node_for_chunk(lvl, chunk)
                if node is None:
                    return self._send(200, {"shape": None, "why": "no model or collision draws chunk %d" % chunk})
                return self._send(200, {"shape": MA.shape_now(s, body["archive"], lvl, node), "residual": 0.0,
                                        "exact": True, "why": None})
            stock = Level(s.vanilla, body["archive"], area=body.get("area"))
            vr, cr = stock._load_rsc(), lvl._load_rsc()
            if vr is None or cr is None or chunk >= len(vr.chunks):
                return self._send(404, {"error": "no chunk %d" % chunk})
            tag = vr.chunks[chunk].tag
            if tag == "BND0":
                # Collision carries a real vertex list, and every bound op
                # transforms it, so the same fit works. The AABB header is
                # deliberately wider than the vertices, which is exactly why
                # the vertices and not the box are what get measured.
                try:
                    a = geomedit.bound_verts(vr.raw(chunk))
                    b = geomedit.bound_verts(cr.raw(chunk))
                except geomedit.NotEditable as ex:
                    return self._send(200, {"shape": None, "why": str(ex)})
                shape, resid = geomedit.recover(a, b)
                if shape is None:
                    return self._send(200, {"shape": None,
                                            "why": "vertex counts differ"})
                exact = resid <= 1e-3
                return self._send(200, {
                    "shape": shape, "residual": resid, "exact": exact,
                    "why": None if exact else
                           "this bound was reshaped in a way no move/turn/"
                           "scale describes (worst vertex %.3f m out)" % resid})
            if tag != "MOD0":
                return self._send(200, {"shape": None,
                                        "why": "chunk %d is a %s, not geometry"
                                               % (chunk, tag)})
            a, b = vr.model(chunk), cr.model(chunk)
            if not a or not b:
                return self._send(200, {"shape": None,
                                        "why": "chunk will not decode"})
            shape, resid = geomedit.recover(a, b)
            if shape is None:
                return self._send(200, {"shape": None,
                                        "why": "vertex counts differ"})
            # A yaw-and-scale cannot describe an arbitrary reshape (an OBJ
            # import, say). Say so instead of returning a transform that does
            # not actually reproduce the geometry.
            exact = resid <= 1e-3
            return self._send(200, {
                "shape": shape, "residual": resid, "exact": exact,
                "why": None if exact else
                       "this chunk was reshaped in a way no move/turn/scale "
                       "describes (worst vertex is %.3f m out)" % resid})

        if route == "geom_edit":
            # Move, scale or rotate a piece of the world itself -- not an
            # instance node, the actual surface. Positions are floats inside
            # the VIF packet, so this is an in-place patch of the same length
            # and always fits the archive slot.
            #
            # The transform is always applied to the VANILLA geometry, never
            # to whatever is on the working disc. Applying deltas on top of
            # deltas drifts (32-bit float), and it also makes "put it back"
            # impossible; deriving from stock every time means the edit is
            # exact and revertible.
            from . import geomedit
            # This route froze the game for a night. The verts moved but the
            # per-part cull spheres (VIF unpack to VU 0x29, part_offset+12)
            # did not, and the VU microprogram consumes them at runtime --
            # verts-only froze the 185 s run, verts+spheres plays. Every
            # transform below now moves the spheres through the same maths.
            lvl = s.level(body["archive"], area=body.get("area"))
            chunk = int(body["chunk"])
            if s._mods_folder_of(body["archive"]):
                # A level only the mods folder has (a new level, a model placed from another level): the unedited
                # disc has none of it ("not on disc"). Rebuilt from its own bundles, PS2 and Xbox, with its
                # collision shell taking the same move/turn/scale -- one Ctrl+Z (modeladd.reshape, 2026-11-18).
                from . import modeladd as MA, collcreate as CC
                node = MA.node_for_chunk(lvl, chunk)
                if node is None:
                    return self._send(404, {"error": "no model or collision draws chunk %d" % chunk})
                pivot = None
                if body.get("about"):
                    off = list(node.xyz()) if node.kind == "zone" else [0.0, 0.0, 0.0]
                    pivot = [float(body["about"][k]) + off[k] for k in range(3)]
                try:
                    r = MA.reshape(s, body["archive"], lvl, [node], body.get("ops") or [],
                                   body.get("label") or "reshape %s" % node.name, pivot=pivot,
                                   collision=not body.get("alone"))
                except (MA.ModelError, CC.CollError, KeyError, IndexError, ValueError) as ex:
                    return self._send(400, {"error": "%s could not be reshaped: %s" % (node.name, ex)})
                if r["changed"]:
                    s.note("%s :: reshaped %s" % (body["archive"], ", ".join(r["moved"])))
                return self._send(200, r)
            stock = Level(s.vanilla, body["archive"], area=body.get("area"))
            vr = stock._load_rsc()
            if vr is None or chunk >= len(vr.chunks):
                return self._send(404, {"error": "no chunk %d" % chunk})
            tag = vr.chunks[chunk].tag

            if tag == "BND0":
                # Collision. Authored much coarser than the art -- Brimstone
                # has 342 visible zones and 16 collision meshes -- so there
                # is no per-building pairing to follow automatically. The
                # bound is moved as its own object.
                #
                # M16. This branch used to call geomedit's bound movers, and
                # they move two of the FOUR places a BND0 records where it
                # is: the triangles and the header AABB. Left behind were the
                # stored centre (+0x20), the distance from the world origin
                # to the farthest AABB corner (+0x3C), and segment B -- a
                # tree of world-space boxes that IS the shell's broadphase
                # index. A shell moved that way has its index still pointing
                # at the old place and nothing at the new one, so it collides
                # in neither, which is exactly what a tester reported as
                # "moving it removes collision".
                #
                # `boundmove` moves all four. The damage the old path did is
                # invisible to every cheap check -- right length, re-parses,
                # draws correctly -- which is why this survived: measured,
                # the old mover breaks three of audit()'s five invariants on
                # 400 of 400 real shells, and the two it does NOT break are
                # the two a naive check would look at.
                from . import boundmove
                import math
                raw = vr.raw(chunk)
                bd = vr.bound(chunk)
                if bd is None:
                    return self._send(400, {
                        "error": "this BND0 is the model-derived form, which "
                                 "needs the .mod format finished first"})
                about = body.get("about") or geomedit.bound_centre(bd)
                out = raw
                try:
                    for step in (body.get("ops") or []):
                        k = step.get("op")
                        if k == "translate":
                            out = boundmove.translate(
                                out, float(step.get("x", 0)),
                                float(step.get("y", 0)),
                                float(step.get("z", 0)))
                        elif k == "scale":
                            out = boundmove.scale(
                                out, float(step.get("x", 1)),
                                float(step.get("y", step.get("x", 1))),
                                float(step.get("z", step.get("x", 1))),
                                about=about)
                        elif k == "rotate_y":
                            out = boundmove.rotate_y(
                                out, math.radians(float(step.get("deg", 0))),
                                about=about)
                        else:
                            return self._send(400,
                                              {"error": "unknown op %r" % k})
                except boundmove.NotEditable as ex:
                    # Refused before anything is written, and it says which
                    # shell and why rather than throwing a 500.
                    return self._send(400, {
                        "error": "this collision shell cannot be moved "
                                 "safely: %s" % ex})

                # Ask the shell whether it still agrees with itself about
                # where it is, and refuse rather than write one that does
                # not. Every one of the 2,870 stock shells on the disc passes
                # these, so a False here is this edit's fault and not the
                # disc's. Cheap -- it is a parse and some comparisons -- and
                # it is the only check that can tell a shell that MOVED from
                # one that merely had its triangles moved.
                if out != raw:
                    bad = sorted(k for k, v in boundmove.audit(out).items()
                                 if v is False)
                    if bad:
                        return self._send(500, {
                            "error": "refusing to write: the moved shell no "
                                     "longer agrees with itself about where "
                                     "it is (%s). Nothing was written."
                                     % ", ".join(bad)})
                return _splice_and_write(self, s, body, lvl, chunk, raw, out)

            if tag != "MOD0":
                return self._send(404, {"error": "chunk %d is a %s, which is "
                                                 "not geometry" % (chunk, tag)})
            m = vr.model(chunk)
            if not m:
                return self._send(404, {"error": "chunk %d will not decode"
                                                 % chunk})
            if not geomedit.editable(m):
                return self._send(400, {
                    "error": "this mesh is skinned -- its positions are "
                             "quantised against the VIF ROW register, so it "
                             "cannot be moved by patching floats"})

            raw = vr.raw(chunk)
            about = body.get("about") or geomedit.centre(m)
            parts = vr.parts(chunk)
            out = raw
            mm = m
            for step in (body.get("ops") or []):
                kind = step.get("op")
                if kind == "translate":
                    dx = float(step.get("x", 0))
                    dy = float(step.get("y", 0))
                    dz = float(step.get("z", 0))
                    out = geomedit.translate(out, mm, dx, dy, dz)
                    out = geomedit.move_spheres(
                        out, parts, lambda x, y, z: (x + dx, y + dy, z + dz))
                elif kind == "scale":
                    sx = float(step.get("x", 1))
                    sy = float(step.get("y", step.get("x", 1)))
                    sz = float(step.get("z", step.get("x", 1)))
                    out = geomedit.scale(out, mm, sx, sy, sz, about=about)
                    ax, ay, az = about
                    out = geomedit.move_spheres(
                        out, parts,
                        lambda x, y, z: (ax + (x - ax) * sx,
                                         ay + (y - ay) * sy,
                                         az + (z - az) * sz))
                elif kind == "rotate_y":
                    import math
                    rad = math.radians(float(step.get("deg", 0)))
                    out = geomedit.rotate_y(out, mm, rad, about=about)
                    ax, ay, az = about
                    ca, sa = math.cos(rad), math.sin(rad)
                    out = geomedit.move_spheres(
                        out, parts,
                        lambda x, y, z: (ax + (x - ax) * ca + (z - az) * sa,
                                         y,
                                         az - (x - ax) * sa + (z - az) * ca))
                else:
                    return self._send(400, {"error": "unknown op %r" % kind})
                # Each step reads the positions the previous one wrote.
                mm = dict(m, verts=_reread(out, m))

            extra, cnote = _linked_twin_edit(s, lvl, vr, chunk,
                                             body.get("ops") or [], about)
            # One object, several drawn pieces, one welded collision: the
            # pieces inside this model's box take the same transform, and the
            # collision inside the merged shells follows (collparts).
            follow = setinfo = None
            twins = sorted(extra)
            from . import collparts, custcoll, massedit, scene as _scene
            z = custcoll.zone_for_chunk(lvl, chunk)
            if z is not None and collparts.identity_rot(z):
                ta = z.xyz()
                wpivot = [about[i] + ta[i] for i in range(3)]
                members = [z.uid]
                if body.get("set", True):
                    names = []
                    for u in collparts.group_of(lvl, lvl._load_rsc(), z.uid):
                        m = lvl.by_uid(u)
                        mc = _scene.chunk_of(m)
                        if u == z.uid or mc is None or mc == chunk or mc in extra \
                                or not collparts.identity_rot(m):
                            continue
                        tm = m.xyz()
                        try:
                            pl = massedit.plan(vr, [mc], body.get("ops") or [],
                                               about=[wpivot[i] - tm[i] for i in range(3)])
                        except Exception:
                            continue
                        extra[mc] = pl["chunks"][mc]
                        members.append(u)
                        names.append(m.name)
                    if names:
                        setinfo = dict(count=len(names) + 1, also=names)
                follow = [(members, _ops_fn(body.get("ops") or [], wpivot))]
                if not twins:
                    cnote = None        # "no linked collision" is no longer the story
            return _splice_and_write(self, s, body, lvl, chunk, raw, out,
                                     extra=extra, note=cnote, follow=follow,
                                     setinfo=setinfo, twins=twins)

        if route == "mass_edit":
            # M6. The same transform over a SELECTION, about ONE pivot, in ONE
            # write.
            #
            # Deliberately NOT a loop over `geom_edit`. That route's pivot
            # defaults to each mesh's own centre, so calling it N times scales
            # every model about its own middle and leaves the arrangement
            # exactly where it was -- measured at x1.0000 against x2.0000 for
            # the real thing (REQ-015). It would also be N writes and N
            # journal entries, and a mass move that half-applies leaves a
            # level that no single undo restores.
            #
            # Like `geom_edit`, the transform is derived from the VANILLA
            # geometry rather than from whatever is on the working disc, so
            # the ops are a total, the result does not drift through repeated
            # float32 deltas, and "put it back" stays possible.
            from . import massedit
            lvl = s.level(body["archive"], area=body.get("area"))
            chunks = [int(c) for c in (body.get("chunks") or [])]
            if len(chunks) < 2:
                return self._send(400, {
                    "error": "mass_edit is for a selection of 2 or more; use "
                             "geom_edit for a single chunk"})
            if s._mods_folder_of(body["archive"]):
                # a level only the mods folder has: every model (and its collision) about one pivot, on every
                # bundle, one Ctrl+Z (modeladd.reshape; the values are a change from now) -- 2026-11-18
                from . import modeladd as MA, collcreate as CC
                nodes = [MA.node_for_chunk(lvl, c) for c in chunks]
                nodes = [n for n in nodes if n is not None]
                try:
                    pivot = [float(v) for v in body["about"]] if body.get("about") else \
                        MA.selection_centre(s, body["archive"], lvl, nodes)
                    sc = [o for o in body.get("ops") or [] if o.get("op") == "scale"]
                    report = dict(models=len(nodes), pivot=pivot,
                                  proportional=all(float(o.get("y", o.get("x", 1))) == float(o.get("x", 1)) ==
                                                   float(o.get("z", o.get("x", 1))) for o in sc),
                                  collision_untouched=[], collision_note=None)
                    if body.get("dry"):
                        return self._send(200, dict(report, dry=True))
                    r = MA.reshape(s, body["archive"], lvl, nodes, body.get("ops") or [],
                                   body.get("label") or "move %d models" % len(nodes), pivot=pivot, total=False,
                                   collision=not body.get("alone"))
                except (MA.ModelError, CC.CollError, KeyError, IndexError, ValueError) as ex:
                    return self._send(400, {"error": str(ex)})
                return self._send(200, dict(report, ok=True, changed=r["changed"],
                                            collision_note=r["collision_note"]))
            stock = Level(s.vanilla, body["archive"], area=body.get("area"))
            vr = stock._load_rsc()
            if vr is None:
                return self._send(404, {"error": "no geometry bundle here"})
            try:
                planned = massedit.plan(vr, chunks, body.get("ops") or [],
                                        about=body.get("about"))
            except massedit.Refused as ex:
                # A refusal names the model it refused, and nothing has been
                # written -- the whole selection is planned before any of it
                # reaches the disc.
                return self._send(400, {"error": str(ex)})
            except (IndexError, KeyError, ValueError) as ex:
                return self._send(400, {"error": "%s: %s"
                                                 % (type(ex).__name__, ex)})

            # Reported on every reply, including the refusals and the dry run.
            # `proportional` is False for a per-axis scale, and the collision
            # note says IN WORDS that the BND0 shells stayed put -- that is
            # M16, not this milestone, and silence about it is the exact shape
            # of bug this lane fears most.
            report = dict(models=planned["models"],
                          pivot=[float(v) for v in planned["pivot"]],
                          proportional=planned["proportional"],
                          collision_untouched=planned["collision_untouched"],
                          collision_note=planned["collision_note"])

            live = lvl._load_rsc()
            if live is None:
                return self._send(404, {
                    "error": "the working disc has no geometry bundle for "
                             "this area"})
            cur = s.disc.archive(body["archive"]).read(lvl.rsc_name)
            try:
                blob = massedit.splice(cur, live, planned)
            except massedit.Refused as ex:
                return self._send(400, dict(report, error=str(ex)))
            # Collision inside the merged shells follows the selection.
            from . import collparts, custcoll
            zs = [custcoll.zone_for_chunk(lvl, c) for c in chunks]
            zs = [z for z in zs if z is not None]
            only_move = all(o.get("op") == "translate" for o in body.get("ops") or [])
            if zs and all(collparts.identity_rot(z) for z in zs) and (
                    only_move or all(max(abs(v) for v in z.xyz()) < 1e-6 for z in zs)):
                blob, finfo = _follow_blob(
                    s, body, lvl, blob,
                    [([z.uid for z in zs],
                      _ops_fn(body.get("ops") or [], planned["pivot"]))])
                report["collision"] = finfo
                fnote = _follow_note(finfo)
                if finfo.get("shells"):
                    report["collision_note"] = fnote
                    report["collision_untouched"] = []
                elif fnote:
                    report["collision_note"] = fnote
                elif report.get("collision_note"):
                    report["collision_note"] = (
                        "No collision pieces sit wholly inside this selection, "
                        "so no collision was moved.")
            if blob == cur:
                return self._send(200, dict(report, ok=True, changed=False,
                                            note="no net change"))
            if body.get("dry"):
                return self._send(200, dict(report, dry=True, bytes=len(blob),
                                            same_size=len(blob) == len(cur)))
            label = body.get("label") or ("move %d models" % planned["models"])
            used, slot = s.write(body["archive"], lvl.rsc_name, blob, label)
            s.note("%s :: mass edit over %d model(s) about one pivot "
                   "(%d/%d bytes)"
                   % (body["archive"], planned["models"], used, slot))
            return self._send(200, dict(report, ok=True, changed=True,
                                        used=used, slot=slot))

        if route == "material_set":
            # Recolour one surface. The material block is a fixed 68 bytes at
            # an offset the part record names, so this cannot change any
            # length and cannot overflow a slot.
            from . import geomedit
            # This route froze the game for a night. The verts moved but the
            # per-part cull spheres (VIF unpack to VU 0x29, part_offset+12)
            # did not, and the VU microprogram consumes them at runtime --
            # verts-only froze the 185 s run, verts+spheres plays. Every
            # transform below now moves the spheres through the same maths.
            lvl = s.level(body["archive"], area=body.get("area"))
            chunk = int(body["chunk"])
            part = int(body.get("part", 0))
            rgba = body.get("colour")
            stock = Level(s.vanilla, body["archive"], area=body.get("area"))
            vr = stock._load_rsc()
            if vr is None or not (0 <= chunk < len(vr.chunks)) \
                    or vr.chunks[chunk].tag != "MOD0":
                return self._send(404, {"error": "chunk %s is not a model"
                                                 % chunk})
            recs = vr.parts(chunk)
            if not recs or not (0 <= part < len(recs)):
                return self._send(404, {"error": "no part %d in chunk %d"
                                                 % (part, chunk)})
            raw = vr.raw(chunk)
            off = recs[part]["material"]
            if rgba is None:
                return self._send(200, dict(
                    chunk=chunk, part=part, offset=off,
                    colour=geomedit.material_colour(raw, off)))
            try:
                out = geomedit.set_material_colour(raw, off, rgba)
            except geomedit.NotEditable as ex:
                return self._send(400, {"error": str(ex)})

            # Every part sharing this offset shares the material, so say so
            # rather than letting the user wonder why a neighbour changed.
            shared = sum(1 for r0 in recs if r0["material"] == off)
            body = dict(body, label=body.get("label") or
                        ("tint chunk %d" % chunk))
            r = _splice_and_write(self, s, body, lvl, chunk, raw, out)
            return r

        if route == "character_import":
            # An edited character part back from Blender. Same contract as
            # level geometry: positions only, matched by index. Positions are
            # re-quantised to the 16-bit codes the format uses, which is
            # exact -- every one of the 2,313,761 skinned vertices on the disc
            # re-encodes to the byte it came from.
            from . import geomedit
            from .rsc import Rsc
            arc, name = body["archive"], body["name"]
            chunk = int(body["chunk"])
            try:
                try:
                    stock_rsc = Rsc(s.vanilla.archive(arc).read(name), name)
                except Exception:                          # noqa: BLE001
                    stock_rsc = Rsc(s.disc.archive(arc).read(name), name)  # an added character
            except Exception as ex:
                return self._send(404, dict(error=str(ex)))
            if not (0 <= chunk < len(stock_rsc.chunks)) \
                    or stock_rsc.chunks[chunk].tag != "MOD0":
                return self._send(404, {"error": "chunk %s is not a model"
                                                 % chunk})
            m = stock_rsc.model(chunk)
            if not m:
                return self._send(404, {"error": "chunk %d will not decode"
                                                 % chunk})
            raw = stock_rsc.raw(chunk)
            try:
                verts = geomedit.from_obj(body.get("obj") or "")
                # Undo the bind pose the export applied. Recomputed here from
                # the same part and the same skeleton rather than read out of
                # the file, because Blender strips comments and anything
                # carried in the header would be gone by now -- the importer
                # would then subtract nothing and quietly move every vertex.
                offs, skel_used = s.part_pose(arc, name, m)
                if offs and len(offs) == len(verts):
                    verts = [(v[0] - o[0], v[1] - o[1], v[2] - o[2])
                             for v, o in zip(verts, offs)]
                    # Safety net for "exported one part, imported another":
                    # bone-local positions live inside a small quantiser range
                    # (about +/-4 m), so anything far outside it means the
                    # subtraction used a different skeleton than the addition.
                    pr = geomedit.precision(m)
                    if pr:
                        lim = pr["range"]
                        far = max((max(abs(c) for c in v) for v in verts),
                                  default=0.0)
                        if far > lim:
                            return self._send(400, {
                                "error":
                                    "after removing the bind pose these "
                                    "vertices sit up to %.1f m from their "
                                    "bone, past the %.1f m this format can "
                                    "store. That usually means the OBJ came "
                                    "from a different part, or was scaled in "
                                    "Blender -- scaling the mesh does not "
                                    "scale the skeleton." % (far, lim)})
                out = geomedit.apply_obj(raw, m, verts)
            except geomedit.NotEditable as ex:
                return self._send(400, {"error": str(ex)})

            moved = sum(1 for a, b in zip(verts, m["verts"])
                        if any(abs(x - y) > 1e-6 for x, y in zip(a, b)))
            # Report saturation honestly: a vertex pushed past the 16-bit
            # range clamps, and silently clamping is how a model comes back
            # subtly wrong.
            reread = Rsc(_swap_chunk(stock_rsc, chunk, out), name).model(chunk)
            drift = max((max(abs(a - b) for a, b in zip(w, g))
                         for w, g in zip(verts, reread["verts"])), default=0.0)
            p = geomedit.precision(m)
            if body.get("dry"):
                return self._send(200, dict(
                    dry=True, vertices=len(verts), moved=moved,
                    worst_error=round(drift, 6),
                    step=round(p["step"], 6) if p else None,
                    saturated=bool(p and drift > p["step"] * 1.5)))

            cur = s.disc.archive(arc).read(name)
            live = Rsc(cur, name)
            c = live.chunks[chunk]
            blob = bytearray(cur)
            blob[c.offset:c.offset + c.size] = out
            used, slot = s.write(arc, name, bytes(blob),
                                 "import %s part %d" % (name, chunk))
            s.note("%s :: %s part %d imported (%d verts moved, %d/%d bytes)"
                   % (arc, name, chunk, moved, used, slot))
            return self._send(200, dict(ok=True, moved=moved,
                                        worst_error=round(drift, 6),
                                        used=used, slot=slot))

        if route == "character_model_import":
            # EXPERIMENTAL: an OBJ with ANY vertex count replaces a character
            # part; skinning is copied from the nearest original vertex. See
            # skinwrite.py for what is proven and what is not (one
            # in-game boot of a custom character, 2026-11-12).
            from . import skinwrite
            arc, name = body.get("archive"), body.get("name")
            try:
                chunk = int(body["chunk"])
            except (KeyError, TypeError, ValueError):
                return self._send(400, {"error": "Pick a character part first."})
            if not (body.get("obj") or "").strip():
                return self._send(400, {"error": "That file is empty. Export "
                                        "the mesh as .obj from Blender and "
                                        "choose that file."})
            bw = s.skel_world(arc, name or "")
            if not bw:
                return self._send(400, {"error": "No skeleton was found for "
                                        "this character, so a new mesh cannot "
                                        "be attached to its bones. Nothing "
                                        "was changed."})
            try:
                cur = s.disc.archive(arc).read(name)
                out, rep = skinwrite.import_obj(cur, chunk, body["obj"], bw,
                                                label=name)
            except skinwrite.SkinError as ex:
                return self._send(400, {"error": str(ex).rstrip(". ")
                                        + ". Nothing was changed."})
            except Exception as ex:                    # noqa: BLE001
                return self._send(400, {"error": "Import failed: %s. Nothing "
                                        "was changed." % ex})
            fit = s.disc.archive(arc).would_fit(name, out)
            rep["stored"], rep["slot"] = fit["stored"], fit["slot"]
            rep["rebuild"] = not fit["fits"] and s.rebuild_on_overflow
            rep["fits"], rep["headroom"] = fit["fits"] or rep["rebuild"], fit["headroom"]
            if body.get("dry"):
                return self._send(200, dict(rep, dry=True))
            if not rep["fits"]:
                return self._send(400, {
                    "error": "Too big: this model needs %d bytes but the "
                             "original only had room for %d (%d too many). "
                             "Nothing was changed. Reduce the triangle count "
                             "(Blender: Decimate) and import again."
                             % (fit["stored"], fit["slot"],
                                fit["stored"] - fit["slot"])})
            used, slot = s.write(arc, name, out,
                                 "custom character mesh %s part %d" % (name, chunk))
            s.note("%s :: %s part %d custom character mesh (%d tris, %d/%d "
                   "bytes) EXPERIMENTAL" % (arc, name, chunk,
                                            rep["triangles"], used, slot))
            return self._send(200, dict(
                rep, ok=True, used=used, slot=slot,
                message="Done: part %d now has your mesh (%d triangles, %d of "
                        "%d bytes used). Skinning was copied from the nearest "
                        "original vertices. EXPERIMENTAL: shown drawing and "
                        "animating in PCSX2 on one character so far." % (chunk, rep["triangles"], used, slot),
                next="Next: press Boot test and look at the character. "
                     "Ctrl+Z undoes this."))

        if route == "geom_import":
            # An OBJ back from Blender. Positions only, matched by index --
            # see geomedit.apply_obj for why that is the whole contract.
            from . import geomedit
            # This route froze the game for a night. The verts moved but the
            # per-part cull spheres (VIF unpack to VU 0x29, part_offset+12)
            # did not, and the VU microprogram consumes them at runtime --
            # verts-only froze the 185 s run, verts+spheres plays. Every
            # transform below now moves the spheres through the same maths.
            lvl = s.level(body["archive"], area=body.get("area"))
            chunk = int(body["chunk"])
            stock = Level(s.vanilla, body["archive"], area=body.get("area"))
            vr = stock._load_rsc()
            if vr is None or not (0 <= chunk < len(vr.chunks)) \
                    or vr.chunks[chunk].tag != "MOD0":
                return self._send(404, {"error": "chunk %s is not a model"
                                                 % chunk})
            m = vr.model(chunk)
            if not m:
                return self._send(404, {"error": "chunk %d will not decode"
                                                 % chunk})
            try:
                verts = geomedit.from_obj(body.get("obj") or "")
                out = geomedit.apply_obj(vr.raw(chunk), m, verts)
            except geomedit.NotEditable as ex:
                return self._send(400, {"error": str(ex)})

            moved = sum(1 for a, b in zip(verts, m["verts"])
                        if any(abs(x - y) > 1e-6 for x, y in zip(a, b)))
            if body.get("dry"):
                return self._send(200, dict(dry=True, vertices=len(verts),
                                            moved=moved))
            r = _splice_and_write(self, s, body, lvl, chunk,
                                  vr.raw(chunk), out)
            return r

        if route == "model_import":
            # A CUSTOM MODEL: an OBJ with any number of vertices replaces a
            # level model. geom_import above cannot (it matches vertices by
            # index); this writes a fresh VIF display list -- see levelvif.py
            # for what is proven about it and what only the console can prove.
            #
            # Built on the CURRENT working-disc bundle, so earlier edits stay.
            # The slot is fixed: a level archive has ~2 KB of free space in
            # total, so the answer to "will it fit" is part of the reply.
            from . import levelvif
            from .dave import SlotOverflow
            lvl = s.level(body["archive"], area=body.get("area"))
            try:
                chunk = int(body["chunk"])
            except (KeyError, TypeError, ValueError):
                return self._send(400, {"error": "Pick a model first, then "
                                                 "import."})
            if lvl.rsc_name is None:
                lvl._load_rsc()            # name is resolved lazily
            if not (body.get("obj") or "").strip():
                return self._send(400, {"error": "That file is empty. Export "
                                        "the model as .obj from Blender and "
                                        "choose that file."})
            cur = s.disc.archive(body["archive"]).read(lvl.rsc_name)
            tex = {k: int(v) for k, v in (body.get("textures") or {}).items()}
            try:
                out, rep = levelvif.import_obj(
                    cur, chunk, body.get("obj") or "", resource_for=tex,
                    label=lvl.rsc_name)
            except (levelvif.MeshError, ValueError) as ex:
                return self._send(400, {"error": levelvif.friendly(ex)})
            except Exception as ex:                    # noqa: BLE001
                return self._send(400, {"error": levelvif.friendly(ex)})
            fit = s.disc.archive(body["archive"]).would_fit(lvl.rsc_name, out)
            rep["stored"], rep["slot"] = fit["stored"], fit["slot"]
            rep["fits"], rep["headroom"] = fit["fits"], fit["headroom"]
            # Bigger than its old space: queued for the archive's rebuild on the
            # next Play (Rebuild and play), as every other edit with no room.
            grow_ok = bool(s.rebuild_on_overflow)
            # Model + collision are ONE thing: a matching solid box is made
            # for the new model (new bound_ chunk + node, or the existing
            # twin's chunk refitted). Whether the console honours it is
            # proven only by ingame-proof/collision_*.png; the note says so.
            cplan = None
            rep["collision"] = "off"
            if body.get("collision", True):
                from . import custcoll
                try:
                    cplan = custcoll.plan(lvl, out, chunk, body.get("obj") or "")
                except Exception as ex:                # noqa: BLE001
                    cplan = dict(ok=False, why="Collision could not be "
                                 "generated: %s" % ex)
                if cplan["ok"]:
                    cfit = s.disc.archive(body["archive"]).would_fit(
                        lvl.rsc_name, cplan["bundle"])
                    if cfit["fits"] or grow_ok:
                        out = cplan["bundle"]
                        fit = cfit
                        rep["stored"], rep["slot"] = fit["stored"], fit["slot"]
                        rep["fits"], rep["headroom"] = cfit["fits"], fit["headroom"]
                    else:
                        cplan = dict(ok=False, why="No room for the collision "
                                     "chunk: the level file is %d byte(s) too "
                                     "small for model + collision. The model "
                                     "alone fits, so it is imported WITHOUT "
                                     "collision (walk-through)."
                                     % (cfit["stored"] - cfit["slot"]))
                rep["collision"] = cplan["mode"] if cplan["ok"] else "none"
                rep["collision_note"] = (cplan["note"] if cplan["ok"]
                                         else cplan["why"])
            rep["note"] = (rep.get("collision_note")
                           or "Collision is NOT changed: the old shell still "
                              "blocks you and new walls are not solid.") + (
                " Whether the console honours it is untested until a boot "
                "test shows it.")
            rep["rebuild"] = not fit["fits"] and grow_ok
            if rep["rebuild"]:
                rep["fits"] = True
                rep["note"] += (" It is bigger than the original's space (%d of %d bytes): %s is rebuilt to make room "
                                "on the next Rebuild and play." % (fit["stored"], fit["slot"], body["archive"]))
            if body.get("dry"):
                return self._send(200, dict(rep, dry=True))
            if not fit["fits"] and not grow_ok:
                return self._send(400, {
                    "error": "Too big: this model needs %d bytes but the "
                             "original only had room for %d (%d too many). "
                             "Nothing was changed. Reduce the triangle count "
                             "(Blender: Decimate modifier) and import again."
                             % (fit["stored"], fit["slot"],
                                fit["stored"] - fit["slot"])})
            label = "custom model %s" % (body.get("label") or ("chunk %d" % chunk))
            group = s.journal.new_group()
            try:
                used, slot = s.write(body["archive"], lvl.rsc_name, out, label,
                                     group)
                if cplan and cplan["ok"] and cplan["edits"]:
                    res = lvl.apply(cplan["edits"],
                                    writer=s.writer_for(body["archive"], label,
                                                        group))
                    if not res.get("applied"):
                        # Cannot happen after the dry-run in plan(); if it
                        # does, undo the model write rather than half-apply.
                        s.journal.undo(s._raw_write)
                        return self._send(400, {"error": "The collision node "
                            "did not fit its file, so nothing was changed."})
            except SlotOverflow as ex:
                return self._send(400, {"error": str(ex)})
            s.note("%s :: custom model in chunk %d (%d verts, %d tris, "
                   "%d/%d bytes)" % (body["archive"], chunk,
                                     rep["vertices"], rep["triangles"],
                                     used, slot))
            return self._send(200, dict(
                rep, ok=True, changed=True, used=used, slot=slot,
                message="Done: model %d now has your shape (%d triangles, "
                        "%d of %d bytes used). %s"
                        % (chunk, rep["triangles"], used, slot,
                           rep.get("collision_note") or "Collision is unchanged."),
                next="Next: press Boot test in the top bar (or run RDR_MODDED.iso "
                     "in PCSX2) to see it. Ctrl+Z undoes this."))

        if route == "model_add":
            # A custom model into the level where the user clicked (modeladd.py): an .obj / .glb read by the page
            # into a plain mesh, or a game model (.rsc .xrsc .mod .xmod) read here; on every bundle the level has.
            import base64 as _b64
            from . import collcreate as CC, modeladd as MA
            arc = body["archive"]
            try:
                if body.get("mesh"):
                    tris, mats, texs = MA.from_mesh(body["mesh"])
                else:
                    nat = body.get("native") or {}
                    tris, mats, texs = MA.from_native(nat.get("filename") or "", _b64.b64decode(nat.get("data") or ""))
                tris = MA.place(tris, [float(v) for v in body.get("pos") or (0, 0, 0)], float(body.get("turn") or 0),
                                float(body.get("scale") or 1), bool(body.get("keep_place")))
                r = MA.add_to_level(s, arc, tris, mats, texs, body.get("name") or "custom",
                                    bool(body.get("collision", True)), body.get("manifest"), body.get("area"))
            except (MA.ModelError, CC.CollError, KeyError, ValueError, TypeError) as ex:
                return self._send(200, dict(ok=False, error_text=str(ex)))
            s.note("%s: model %s added (%d triangles)" % (arc, r["name"], r["triangles"]))
            return self._send(200, dict(ok=True, **r))

        if route == "coll_autogen":
            # Collision generated from the level's models (collauto.py), as new shells bound_auto_NN: a start to edit.
            # replace: the earlier generated ones (bound_auto_*) taken out first. One undo step on a mods folder level
            # only it has, or a disc level.
            import re as _re
            from . import collauto as CA, collcreate as CC
            arc = body["archive"]
            lvl = s.level(arc, manifest=body.get("manifest"), area=body.get("area"))
            try:
                cell = max(0.05, min(5.0, float(body.get("cell") or 0.3)))
                tile = max(10.0, min(200.0, float(body.get("tile") or 40.0)))
            except (TypeError, ValueError):
                cell, tile = 0.3, 40.0
            replace_auto = body.get("replace", True)
            tris = CA.level_triangles(lvl, skip_existing=body.get("skip_existing", True),
                                      # a level of the mods folder draws only what its zone nodes name: a chunk none
                                      # names is a deleted model's (the stock levels draw some surfaces no node names)
                                      unclaimed=not s._mods_folder_of(arc),
                                      ignore=(lambda nm: bool(_re.match(r"bound_auto_\d+$", nm))) if replace_auto
                                      else (lambda nm: False))
            skipped = getattr(lvl, "_autogen_skipped", 0)
            if not tris:
                return self._send(200, dict(ok=False, error_text=(
                    "Every model here has collision already (%d skipped): nothing to generate" % skipped) if skipped
                    else "This level has no models to make collision from"))
            verts, faces = CA.simplify(tris, cell)
            parts = CA.shells(verts, faces, tile)
            store = CC.folder_store(_data_editor_for(s), arc)
            grp = s.journal.new_group()
            if store is not None and s._mods_folder_of(arc):
                store.writer = lambda n, d: s.write(arc, n, d, "auto collision", grp)
            if store is None:
                lvl._load_rsc()
                if not lvl.rsc_name:
                    return self._send(200, dict(ok=False, error_text="This part of the level has no model bundle"))
                store = CC.DiscStore(s, arc, lvl, "auto collision")
                store.group = grp
            removed = 0
            try:
                if body.get("replace", True):
                    old = set()
                    for f in store.bnds:
                        old |= set(_re.findall(r"node\s+(bound_auto_\d+)\s*\{", store.read(f).decode("latin-1")))
                    removed = CC.remove_many(store, sorted(old))
                names = CC.add_many(store, parts, "auto", True)
            except CC.CollError as ex:
                return self._send(200, dict(ok=False, error_text=str(ex)))
            if store.kind == "folder" and store.writer is None:
                CC.remember(store)
            s._drop_level(arc)
            s.note("%s: auto collision, %d shells (%d faces from %d model triangles)" % (arc, len(names), len(faces),
                                                                                        len(tris)))
            return self._send(200, dict(ok=True, shells=len(names), faces=len(faces), source=len(tris), removed=removed,
                                        where="journal" if (store.kind == "disc" or store.writer) else "folder"))

        if route == "navpath_generate":
            # AI nav paths over the level's walkable ground (navgen.py), into its path .lnes: the nav paths there
            # replaced (replace) or added beside them. One undo step.
            from . import navgen
            import time as _time
            arc = body["archive"]
            lvl = s.level(arc, manifest=body.get("manifest"), area=body.get("area"))
            g = lvl.geometry()
            tris = []
            for m in g.get("meshes", []):
                P, I = m["positions"], m["indices"]
                vs = [tuple(P[k:k + 3]) for k in range(0, len(P), 3)]
                tris += [(vs[I[k]], vs[I[k + 1]], vs[I[k + 2]]) for k in range(0, len(I), 3)]
            if not tris:
                return self._send(200, dict(ok=False, error_text="This level has no collision to find its ground from"))
            lnes = sorted({n.source for n in lvl.nodes if n.kind == "line"})
            target = next((f for f in lnes if any(n.source == f and "navpath" in n.name.lower()
                                                  for n in lvl.nodes)), None)
            target = target or next((f for f in (e.name for e in lvl.dave) if f.lower().endswith("_path.lnes")), None)
            if not target:
                return self._send(200, dict(ok=False, error_text="This level has no path file (lvl_<name>_path.lnes) "
                                                                 "to write nav paths into"))
            t0 = _time.time()
            try:
                spacing = max(1.0, min(20.0, float(body.get("spacing") or 10.0)))
            except (TypeError, ValueError):
                spacing = 10.0
            lines = navgen.generate(tris, spacing=spacing)
            if not lines:
                return self._send(200, dict(ok=False, error_text="No walkable ground found in the collision"))
            text = lvl._read(target).decode("latin-1")
            new, removed, added = navgen.rewrite_paths(text, lines, replace=bool(body.get("replace", True)))
            grp = s.journal.new_group()
            s.write(arc, target, new.encode("latin-1"), "generate AI nav paths", grp)
            # the AI's graph (lvl_<x>_nav.rsc, NTG0) is the path file compiled: made again with it, the Xbox copy too
            # when the level has one. A graph kept from the template froze the game as the AI started (2026-11-18)
            nav_name = re.sub(r"_path\.lnes$", "_nav.rsc", target, flags=re.I)
            try:
                nav = navgen.build_nav(new)
                have = {e.name.lower() for e in lvl.dave}
                folder = s._mods_folder_of(arc)
                if folder:
                    have |= {f.lower() for f in os.listdir(folder)}
                if nav_name.lower() in have:
                    s.write(arc, nav_name, nav, "generate AI nav paths", grp)
                xname = nav_name[:-4] + ".xrsc"
                if folder and xname.lower() in have:
                    s.write(arc, xname, navgen.xbox_nav(nav), "generate AI nav paths", grp)
            except Exception as ex:                         # noqa: BLE001
                s.note("%s: the AI nav graph was not made again (%s); the build makes it" % (arc, ex))
            pts = sum(len(x) for x in lines)
            s.note("%s: %d AI nav paths generated (%d points) in %s" % (arc, added, pts, target))
            return self._send(200, dict(ok=True, file=target, added=added, removed=removed, points=pts,
                                        spacing=round(getattr(navgen.generate, "spacing_used", spacing), 1),
                                        seconds=round(_time.time() - t0, 1)))

        if route == "mp_sync":
            # the "Edit both Multiplayer level variants" box (web: Collision editor panel)
            if "on" in body:
                s.sync_mp = bool(body["on"])
            arc = body.get("archive") or ""
            pm = s._mp_partner(arc, "x") if arc else None
            return self._send(200, dict(ok=True, on=s.sync_mp, partner=pm[0] if pm else None))

        if route == "event_edit":
            # A light glow, camera shot, AI spawn point or door added or removed (eventedit.py): a text edit of
            # its own file, through Studio.write (Ctrl+Z; a mods folder level's own folder).
            from . import eventedit as EE
            arc = body["archive"]
            lvl = s.level(arc, manifest=body.get("manifest"), area=body.get("area"))
            kind, op = body.get("kind"), body.get("op")
            src = body.get("source")
            if not src:
                ext = EE.KIND_EXT.get(kind, "")
                have = [e.name for e in lvl.dave if e.name.lower().endswith(ext)] if ext else []
                if not have:
                    return self._send(200, dict(ok=False, error_text="This level has no %s file to add a %s to"
                                                % (ext or "?", kind)))
                src = have[0]
            try:
                text = lvl._read(src).decode("latin-1")
                new, name = EE.edit(text, kind, op, index=body.get("index"), pos=body.get("pos"),
                                    group=body.get("group"), heading=float(body.get("heading") or 0.0))
                s.write(arc, src, new.encode("latin-1"), "%s %s" % ("add" if op == "add" else "delete", kind),
                        s.journal.new_group())
            except (EE.EventError, KeyError, ValueError, TypeError) as ex:
                return self._send(200, dict(ok=False, error_text=str(ex)))
            s.note("%s: %s %s in %s" % (arc, op, kind, src))
            return self._send(200, dict(ok=True, source=src, name=name))

        if route == "node_add":
            # A new volume, spline, player spawn or enemy where the user clicked: a copy of one of this level's
            # own of that kind (and type), else of one from a level of the disc, moved there.
            arc = body["archive"]
            lvl = s.level(arc, manifest=body.get("manifest"), area=body.get("area"))
            kind, token = body.get("kind"), (body.get("token") or "").lower()
            pos = [float(v) for v in body["pos"]]
            players = body.get("players")

            def _pos(n):
                return [float(c.value if hasattr(c, "value") else c) for c in n.pos]

            def fits(n):
                if n.kind != kind or getattr(n, "readonly", False):
                    return False
                if kind == "spawn":
                    return players is None or int(n.props.get("players", 0)) == int(players)
                return not token or token in n.name.lower()
            try:
                tpl = next((n for n in lvl.nodes if fits(n)), None)
                if tpl is not None:
                    tp = _pos(tpl)
                    edits, new_name = lvl.duplicate(tpl.uid, [pos[i] - tp[i] for i in range(3)])
                else:
                    if kind == "spawn":
                        return self._send(200, dict(applied=False, error="This level has no %s-player spawn "
                                                    "section to add to" % players))
                    ext = {"box": ".bxes", "line": ".lnes", "enemy": ".enm"}.get(kind)
                    dest = lvl.roomiest(ext) if ext else None
                    if not dest:
                        return self._send(200, dict(applied=False, error="This level has no %s file for a new %s"
                                                    % (ext, kind)))
                    found = s.node_template(kind, token)
                    if not found:
                        return self._send(200, dict(applied=False, error="No %s%s on the disc to copy"
                                                    % (kind, " of type " + token if token else "")))
                    slvl, sn = found
                    from . import scene as _scene
                    sdata = slvl._read(sn.source)
                    ddata = lvl._read(dest)
                    sp = _pos(sn)
                    e1, new_name = _scene.transplant_block(sdata, sn, ddata, [pos[i] - sp[i] for i in range(3)])
                    edits = {dest: e1 + _scene.bump_count(ddata, dest, +1)}
            except (KeyError, IndexError, ValueError) as ex:
                return self._send(200, dict(applied=False, error=str(ex)))
            result = lvl.apply(edits, writer=s.writer_for(arc, "create %s" % new_name))
            result["new_name"] = new_name
            if result.get("applied"):
                s.note("%s: added %s" % (arc, new_name))
            return self._send(200, result)

        if route == "vertex_move":
            # A spline's points moved (the level editor: select a spline, T, drag its points, Save points).
            # Level.set_vertex per point, one undo step.
            lvl = s.level(body["archive"], manifest=body.get("manifest"), area=body.get("area"))
            try:
                uid = int(body["uid"])
                edits = []
                for c in body["changes"]:
                    one = lvl.set_vertex(uid, int(c["index"]), [float(v) for v in c["xyz"]])
                    for _f, e in one.items():
                        edits += e
                n = lvl.by_uid(uid)
            except (KeyError, IndexError, TypeError, ValueError) as ex:
                return self._send(200, dict(applied=False, error=str(ex)))
            result = lvl.apply({n.source: edits}, writer=s.writer_for(body["archive"], "reshape %s" % n.name))
            if result.get("applied"):
                s.note("%s: %s reshaped (%d point(s))" % (body["archive"], n.name, len(body["changes"])))
            return self._send(200, result)

        if route == "coll_edit":
            # The collision creator (web: Collision creator panel): a new shell from game-space triangles, a
            # shell's triangles replaced (faces deleted, corners dragged, moved), a shell removed, or the last of
            # these undone -- on every bundle the level has, the mods folder's when it has one (collcreate.py).
            from . import collcreate as CC
            arc = body.get("archive") or ""
            op = body.get("op")
            try:
                if op == "undo":
                    if body.get("where") == "folder":
                        if CC.undo_folder() is None:
                            return self._send(200, dict(ok=False, error_text="Nothing to undo"))
                    else:
                        r = s.undo()
                        if not r:
                            return self._send(200, dict(ok=False, error_text="Nothing to undo"))
                    s._drop_level(arc)
                    return self._send(200, dict(ok=True, undone=True))
                store = CC.folder_store(_data_editor_for(s), arc)
                where = store.kind if store is not None else "disc"
                if store is not None and s._mods_folder_of(arc):
                    # a level only the mods folder has: through Studio.write, which writes it there and keeps it in
                    # the journal -- Ctrl+Z takes a collision edit back like any other
                    grp = s.journal.new_group()
                    store.writer = lambda n, d, _g=grp: s.write(arc, n, d, "collision %s" % op, _g)
                    where = "journal"
                if store is None:
                    lvl = s.level(arc, manifest=body.get("manifest"), area=body.get("area"))
                    lvl._load_rsc()
                    if not lvl.rsc_name:
                        return self._send(200, dict(ok=False, error_text="This part of the level has no model "
                                                    "bundle (.rsc) to hold collision: pick an area that draws "
                                                    "geometry"))
                    store = CC.DiscStore(s, arc, lvl, "collision %s" % op)
                if op == "add":
                    r = CC.add(store, body["verts"], body["faces"], body.get("name") or "custom",
                               bool(body.get("solid", True)))
                elif op == "replace":
                    r = CC.replace(store, body.get("node"), body["verts"], body["faces"], body.get("chunk"))
                elif op == "remove":
                    r = CC.remove(store, body["node"])
                else:
                    return self._send(400, {"error": "op must be add, replace, remove or undo"})
            except CC.CollError as ex:
                return self._send(200, dict(ok=False, error_text=str(ex)))
            except (KeyError, TypeError, ValueError) as ex:
                return self._send(200, dict(ok=False, error_text="The collision could not be written: %s" % ex))
            if where == "folder":
                CC.remember(store)
            s._drop_level(arc)
            s.note("%s: collision %s %s (%s)" % (arc, op, r.get("name"), where))
            return self._send(200, dict(ok=True, where=where,
                                        platforms=[p for p in ("ps2", "xbox") if getattr(store, p, None)], **r))

        if route == "collision_add":
            # LEVEL: a NEW collision box anywhere (world AABB lo..hi), not tied
            # to a model import: a new BND0 chunk in the level bundle and a new
            # bound_<stem>_NN node. One undo step; no room -> rebuilt on the
            # next Rebuild and play.
            from . import custcoll
            arc = body["archive"]
            lvl = s.level(arc, manifest=body.get("manifest"), area=body.get("area"))
            try:
                lo = [float(v) for v in body["lo"]]
                hi = [float(v) for v in body["hi"]]
            except (KeyError, TypeError, ValueError):
                return self._send(400, {"error": "need lo and hi, three numbers each"})
            if any(h <= l for l, h in zip(lo, hi)):
                return self._send(400, {"error": "the box needs a size above 0 on every axis"})
            lvl._load_rsc()                  # resolves rsc_name (set when the bundle is first read)
            if not lvl.rsc_name:
                return self._send(200, dict(ok=False, error_text="This part of the level has no model bundle (.rsc) to "
                                            "hold a collision shell: pick an area that draws geometry"))
            bundle = s.disc.archive(arc).read(lvl.rsc_name)
            cplan = custcoll.plan_box(lvl, bundle, lo, hi, body.get("name") or "custom", bool(body.get("solid", True)))
            if not cplan["ok"]:
                return self._send(200, dict(ok=False, error_text=cplan["why"]))
            fit = s.disc.archive(arc).would_fit(lvl.rsc_name, cplan["bundle"])
            rep = dict(ok=True, name=cplan["name"], chunk=cplan["chunk"], file=cplan.get("file"),
                       rebuild=not fit["fits"],
                       note="Collision: added %s (new chunk %d), a %s box of its own."
                            % (cplan["name"], cplan["chunk"], "solid" if body.get("solid", True) else "walk-through"))
            if body.get("dry"):
                return self._send(200, dict(rep, dry=True))
            label = "new collision %s" % cplan["name"]
            group = s.journal.new_group()
            s.write(arc, lvl.rsc_name, cplan["bundle"], label, group)
            res = lvl.apply(cplan["edits"], writer=s.writer_for(arc, label, group))
            if not res.get("applied"):
                s.journal.undo(s._raw_write)
                return self._send(200, dict(ok=False, error_text="The collision node could not be written, so nothing "
                                            "was changed: %s" % (res.get("error") or "no room")))
            s.note("%s: new collision box %s (chunk %d)" % (arc, cplan["name"], cplan["chunk"]))
            return self._send(200, dict(rep, applied=True))

        if route == "anim_glb_save":
            # "Export all animations" with the current model: the page bakes
            # each clip on the model (anim3d.js bakeClipGlb) and hands the
            # .glb here, to go in the exports folder's animations/<model>.anim.
            import base64 as _b64
            import re as _re
            from .share import exports_dir
            folder = _re.sub(r'[^A-Za-z0-9_. -]+', '_', str(body.get("folder") or "model.anim")).strip(". ") or "model.anim"
            out_dir = os.path.join(exports_dir(), "animations", folder)
            fname = body.get("name")
            if not fname:
                return self._send(200, dict(ok=True, folder=out_dir))
            fname = _re.sub(r'[^A-Za-z0-9_. -]+', '_', os.path.basename(str(fname))).strip(". ")
            if not fname.lower().endswith((".glb", ".csv")):
                return self._send(400, dict(error="only .glb and .csv files go here"))
            os.makedirs(out_dir, exist_ok=True)
            data = body.get("text")
            raw = data.encode("utf-8") if isinstance(data, str) else _b64.b64decode(body.get("data") or "")
            with open(os.path.join(out_dir, fname), "wb") as fh:
                fh.write(raw)
            return self._send(200, dict(ok=True, folder=out_dir, path=os.path.join(out_dir, fname), bytes=len(raw)))

        if route == "anim_import":
            # An edited BVH back into a .anim. Same contract as geom_import --
            # dry-run first, report the numbers, then one write through
            # s.write() so it lands in the undo journal.
            #
            # Two things make this stricter than geom_import. First, the source
            # is the CURRENT file on the working disc, not vanilla: the mask,
            # the scales and the key count all come out of it, and re-importing
            # on top of an earlier import has to see that earlier import.
            # Second, "same length" is NOT the same as "still fits": most .anim
            # entries are deflated and different bytes compress differently, so
            # the stored size is measured the way the archive will store it
            # before anything is written.
            from . import anim as A
            from . import bvh as BVH
            from . import skeleton as SK
            arc, name = body.get("archive"), body.get("name")
            text = body.get("bvh")
            if not arc or not name or text is None:
                return self._send(400, {"error": "need archive, name and bvh"})
            try:
                cur = s.disc.read(arc, name)
            except Exception as ex:
                return self._send(404, dict(error=str(ex)))

            # Optional cross-check against a named skeleton -- joint NAMES only.
            bones = None
            if body.get("skeleton"):
                try:
                    _v, _d, bones, _r = SK.parse(
                        s.disc.read(arc, body["skeleton"]))
                except Exception as ex:
                    return self._send(400, {
                        "error": "cannot read skeleton %s: %s"
                                 % (body["skeleton"], ex)})
            # tolerance=0 is a meaningful request (quantise every sample blind),
            # so it must not be coerced back to the default by an `or`.
            tol = body.get("tolerance")
            thr = body.get("reshape_threshold")
            try:
                out, rep = BVH.bvh_to_anim(
                    cur, text, bones=bones,
                    euler=body.get("euler") or None,
                    tolerance=BVH.DCC_TOLERANCE if tol is None else float(tol),
                    rounding=body.get("rounding") or "nearest",
                    allow_reshape=bool(body.get("allow_reshape")),
                    reshape_threshold=0 if thr is None else int(thr))
            except (BVH.ImportRefused, ValueError) as ex:
                return self._send(400, {"error": str(ex)})
            except A.AnimError as ex:
                # NOT "is not a cm2 clip" any more. Import takes all three
                # codecs now (CAPABILITIES.md C-020), so naming cm2 as the
                # requirement told a cmp or uncompressed clip the one thing
                # that is no longer why it failed. rdr-fmt, REQ-004(b).
                return self._send(400, {
                    "error": "%s could not be read as an animation: %s"
                             % (name, ex)})

            # How big will this actually be once the archive stores it?
            e = s.disc.archive(arc).find(name)
            if e.compressed:
                import zlib
                co = zlib.compressobj(9, 8, -15)
                stored = len(co.compress(out) + co.flush())
                if stored >= len(out):
                    stored = len(out)
            else:
                stored = len(out)
            rep = dict(rep, archive=arc, name=name,
                       stored=stored, slot=e.slot,
                       headroom=e.slot - stored,
                       compressed=bool(e.compressed))
            # The same clip in OTHER archives. 552 clip names sit in more than
            # one: mrd_load_a.anim is in SWAT.DAT and in LVLS_SNG/PERFNEW.DAT,
            # and the level's load show (its own lvl_perfnew.lsh) plays the
            # copy in ITS archive -- so an edit made in SWAT.DAT never showed
            # in the game. Every copy that is still this clip as it was
            # (before this edit, or as the disc shipped it) gets the edit too,
            # as one undo step. A copy someone changed on its own is left
            # alone and named.
            copies, own_copies = [], []
            try:
                holders = s.disc.build_index().get(name.lower()) or s.disc.build_index().get(name) or []
            except Exception:                                  # noqa: BLE001
                holders = []
            try:
                stock = s.vanilla.archive(arc).read(name) if s.vanilla is not None else None
            except Exception:                                  # noqa: BLE001
                stock = None
            if body.get("copies", True) is not False:
                for other in holders:
                    if other.upper() == arc.upper():
                        continue
                    try:
                        theirs = s.disc.read(other, name)
                    except Exception:                          # noqa: BLE001
                        continue
                    if theirs == out:
                        continue
                    if theirs == cur or (stock is not None and theirs == stock):
                        copies.append(other)
                    else:
                        own_copies.append(other)
            rep = dict(rep, copies=copies, copies_left=own_copies)

            # An unedited round trip: every sample as it was. Nothing to write
            # -- even when the re-encoder lays the same samples out in more
            # bytes, which used to be refused as "over its slot" for a clip
            # nobody had touched. (Unless a copy elsewhere is still behind
            # this one: then that is what gets written.)
            if (rep["identical"] or not rep.get("changed_samples")) and not (copies and cur != stock):
                return self._send(200, dict(
                    rep, ok=True, written=False,
                    note="every sample is as it was on the disc, so nothing was "
                         "written"))
            if rep["identical"] or not rep.get("changed_samples"):
                out = cur                     # this one has the edit already: only the copies are behind
            # Bigger than its slot: s.write queues the archive for a rebuild
            # (laid out as the game's own, every other edit kept) that the
            # next Build or Play runs -- the same path every other edit that
            # grows takes. It used to be refused outright.
            rep["grows"] = stored > e.slot
            if rep["grows"]:
                rep["note"] = ("%d bytes against a %d-byte slot: %s is rebuilt to make room on the next "
                               "Build (or Play)" % (stored, e.slot, arc))
            if body.get("dry"):
                return self._send(200, dict(rep, dry=True))

            label = body.get("label") or ("import %s" % name)
            group = s.journal.new_group() if (copies and s.journal is not None) else None
            if cur != out:
                used, slot = s.write(arc, name, out, label, group)
            else:
                used, slot = len(out), e.slot
            for other in copies:
                s.write(other, name, out, label, group)
            s.note("%s :: %s imported (%d/%d samples changed, %d/%d bytes)%s"
                   % (arc, name, rep["changed_samples"], rep["total_samples"],
                      used, slot, ("; also into " + ", ".join(copies)) if copies else ""))
            return self._send(200, dict(rep, ok=True, written=True,
                                        used=used, slot=slot))

        if route == "string_set":
            # Rewrite a string in every archive that carries it, as one undo.
            #
            # Unlike almost everything else here this is NOT byte-count
            # neutral: text is length-prefixed UTF-16, so a longer string
            # grows the file and can overflow its archive slot. Every copy is
            # dry-run first and the whole edit is refused if any one of them
            # would not fit -- a string that changed in nine archives out of
            # ten would be worse than one that did not change at all.
            from . import strtbl as ST
            name = body.get("name")
            text = body.get("text")
            slot = int(body.get("slot", 0))
            if not name or text is None:
                return self._send(400, {"error": "need name and text"})

            planned_writes, refused = [], []
            targets = body.get("copies")
            if not targets:
                return self._send(400, {"error": "no copies given"})
            for t in targets:
                arc, fn = t["archive"], t["file"]
                try:
                    dv = s.disc.archive(arc)
                    raw = dv.read(fn)
                    tab = ST.parse(raw)
                    tab.set_text(name, text, slot)
                    out = tab.encode()
                except Exception as ex:
                    refused.append(dict(archive=arc, file=fn, why=str(ex)))
                    continue
                if out == raw:
                    continue
                e = dv.find(fn)
                if e.compressed:
                    import zlib
                    co = zlib.compressobj(9, 8, -15)
                    stored = len(co.compress(out) + co.flush())
                else:
                    stored = len(out)
                if stored > e.slot:
                    refused.append(dict(
                        archive=arc, file=fn,
                        why="the new text needs %d bytes of a %d-byte slot; "
                            "shorten it by about %d character(s)"
                            % (stored, e.slot, (stored - e.slot + 1) // 2)))
                    continue
                planned_writes.append((arc, fn, out, stored, e.slot))

            if refused:
                return self._send(200, dict(ok=False, applied=0,
                                            refused=refused,
                                            would_apply=len(planned_writes),
                                            note="nothing was written"))
            if body.get("dry"):
                return self._send(200, dict(
                    dry=True, ok=True, would_apply=len(planned_writes),
                    tightest=min((p[4] - p[3] for p in planned_writes), default=0),
                    refused=[]))

            group = s.journal.new_group()
            label = "text %s = %r" % (name, text[:40])
            for arc, fn, out, _, _ in planned_writes:
                s.write(arc, fn, out, label, group)
            s.note("%s (%d copies)" % (label, len(planned_writes)))
            return self._send(200, dict(ok=True, applied=len(planned_writes),
                                        refused=[], label=label))

        if route == "field_bulk_set":
            # Set one field to one value across many files, as ONE undo step.
            #
            # Everything is dry-run first and the whole batch is refused if
            # ANY file would overflow its archive slot. A half-applied bulk
            # edit is far worse than a refused one: you would have to work
            # out which of 90 files took it.
            from . import props as P
            value = body.get("value")
            targets = body.get("targets") or []
            if value is None or not targets:
                return self._send(400, {"error": "need value and targets"})

            planned, refused = [], []
            for tgt in targets:
                arc, name, path = tgt["archive"], tgt["file"], tgt["path"]
                try:
                    dv = s.disc.archive(arc)
                    raw = dv.read(name)
                    out = P.apply(raw, {path: value})
                except Exception as ex:
                    refused.append(dict(archive=arc, file=name,
                                        why=str(ex)))
                    continue
                if out == raw:
                    continue
                e = dv.find(name)
                if e.compressed:
                    import zlib
                    co = zlib.compressobj(9, 8, -15)
                    stored = len(co.compress(out) + co.flush())
                else:
                    stored = len(out)
                if stored > e.slot:
                    refused.append(dict(
                        archive=arc, file=name,
                        why="needs %d bytes of a %d-byte slot"
                            % (stored, e.slot)))
                    continue
                planned.append((arc, name, out))

            if refused:
                return self._send(200, dict(ok=False, applied=0,
                                            refused=refused,
                                            would_apply=len(planned),
                                            note="nothing was written -- fix "
                                                 "or deselect the refusals "
                                                 "first"))
            if body.get("dry"):
                return self._send(200, dict(dry=True, ok=True,
                                            would_apply=len(planned),
                                            refused=[]))

            group = s.journal.new_group()
            label = "set %s = %s (%d file(s))" % (
                targets[0].get("path", "?"), value, len(planned))
            for arc, name, out in planned:
                s.write(arc, name, out, label, group)
            s.note("bulk: %s" % label)
            return self._send(200, dict(ok=True, applied=len(planned),
                                        refused=[], label=label))

        if route == "props_save":
            # Patch named fields in place. Only the value spans move, so a
            # file in a tight archive slot still fits as long as the new
            # text is no longer than the old -- which is why this exists
            # instead of re-serialising the whole file.
            from . import props as P
            arc, name = body["archive"], body["name"]
            edits = body.get("edits") or {}
            if not edits:
                return self._send(400, {"error": "no edits"})
            d = s.disc.archive(arc)
            raw = d.read(name)
            try:
                out = P.apply(raw, edits)
            except KeyError as exc:
                return self._send(400, {"error": str(exc)})
            if body.get("dry"):
                import zlib
                e = d.find(name)
                if e.compressed:
                    co = zlib.compressobj(9, 8, -15)
                    stored = len(co.compress(out) + co.flush())
                else:
                    stored = len(out)
                return self._send(200, dict(dry=True, stored=stored,
                                            slot=e.slot,
                                            fits=stored <= e.slot,
                                            headroom=e.slot - stored,
                                            changed=out != raw))
            label = "%s: %s" % (name, ", ".join(sorted(edits))[:60])
            used, slot = s.write(arc, name, out, label)
            s.note("%s :: %s (%d field(s), %d/%d bytes)"
                   % (arc, name, len(edits), used, slot))
            return self._send(200, dict(ok=True, used=used, slot=slot,
                                        fields=len(edits)))

        if route == "savetext":
            arc, name = body["archive"], body["name"]
            data = body["text"].encode("latin-1")
            d = s.disc.archive(arc)
            e = d.find(name)
            if body.get("dry"):
                import zlib
                if e.compressed:
                    co = zlib.compressobj(9, 8, -15)
                    stored = len(co.compress(data) + co.flush())
                else:
                    stored = len(data)
                rebuild = stored > e.slot and s.rebuild_on_overflow
                return self._send(200, dict(dry=True, stored=stored,
                                            slot=e.slot, fits=stored <= e.slot or rebuild,
                                            rebuild=rebuild, headroom=e.slot - stored))
            used, slot = s.write(arc, name, data, "edit %s" % name)
            s.note("%s :: %s saved (%d/%d bytes of slot)" % (arc, name, used, slot))
            return self._send(200, dict(ok=True, used=used, slot=slot))

        if route == "patch_everywhere":
            name = body["name"]
            find, repl = body["find"].encode("latin-1"), body["replace"].encode("latin-1")
            def fn(data):
                return data.replace(find, repl)
            applied, skipped = s.game.patch_everywhere(
                name, fn, dry=bool(body.get("dry")))
            s.note("%s: %d applied, %d skipped%s"
                   % (name, len(applied), len(skipped),
                      " (dry run)" if body.get("dry") else ""))
            s._levels = {}
            return self._send(200, dict(applied=applied, skipped=skipped))

        if route == "file_drop":
            # Drag-and-drop placement: replace when the name exists in the
            # chosen archive, add when it does not. Replace rides the same
            # journalled write path as every shipped edit (undoable, grow
            # honoured); add rides dave.add, which verifies the whole TOC
            # after insertion and is NOT undoable -- the response says which
            # path each file took, and the dry run says it before any write.
            import base64
            import zlib as _z
            arc = body["archive"]
            name = (body.get("name") or "").strip()
            data = base64.b64decode(body.get("data_b64") or "")
            if not arc or not name:
                return self._send(400, {"error": "need an archive and a name"})
            from .dave import SlotOverflow
            grow = bool(body.get("grow"))
            dry = bool(body.get("dry"))

            def stored_size(payload, keep_raw=False):
                """How many bytes the payload will OCCUPY once written.

                Mirrors dave.patch and dave.add exactly: deflate at level 9,
                and fall back to raw when "compression" grew it. Pricing the
                write with the deflate length alone refuses files that would
                have fitted -- and what people drop over a `.tex` or a `.mod`
                is a PNG or already-packed geometry, i.e. precisely the
                incompressible case where deflate comes back BIGGER.
                """
                if keep_raw:
                    return len(payload)
                co = _z.compressobj(9, _z.DEFLATED, -15)
                packed = co.compress(payload) + co.flush()
                return min(len(packed), len(payload))

            d = s.disc.archive(arc)
            try:
                e = d.find(name)
            except Exception:
                e = None
            if e is not None:
                stored = stored_size(data, keep_raw=not e.compressed)
                fits = stored <= e.slot
                gap = d.largest_gap()[1] if not fits else 0
                # No room in place: the archive is rebuilt to make room (the
                # RDR Data Editor's layout), still one undoable edit.
                rebuild = (not fits and s.rebuild_on_overflow
                           and not (grow and gap >= stored))
                plan = dict(mode="replace", name=e.name, stored=stored,
                            slot=e.slot, fits=fits or rebuild, rebuild=rebuild,
                            grow_possible=(not fits and gap >= stored),
                            undoable=True)
                if dry:
                    return self._send(200, dict(plan, dry=True))
                if not fits and not grow and not rebuild:
                    return self._send(200, dict(
                        plan, ok=False,
                        error="needs %d stored bytes, slot is %d -- tick "
                              "\"allow files to grow\" or shrink the file"
                              % (stored, e.slot)))
                try:
                    used, slot = s.write(arc, name, data,
                                         "place %s into %s" % (name, arc),
                                         grow=grow)
                except SlotOverflow as ex:
                    return self._send(200, dict(plan, ok=False,
                                                error=str(ex)))
                s.note("placed %d bytes over %s :: %s"
                       % (len(data), arc, name))
                # `dict(plan, ...)`, never `dict(..., **plan)`: the plan
                # already carries `slot`, so spreading it beside a second
                # `slot=` raised TypeError -- AFTER s.write had landed the
                # bytes. Every overwrite therefore answered 500 while the
                # archive really had changed, which is what "Place Files
                # cannot overwrite .mod and .tex" was. The add half never hit
                # it, which is why the add half is the half that got verified.
                return self._send(200, dict(plan, ok=True, used=used,
                                            slot=slot))
            # New file. dave.add re-decodes the whole TOC after insertion and
            # refuses rather than write a wrong name; there is no old content,
            # so this path is not undoable and says so.
            try:
                r = d.add(name, data, dry=dry)
            except ValueError:
                # can_add said no. Ask it again directly, because a refusal
                # has to reach the caller as a LIST of reasons the plan can
                # render and block on -- `error` alone makes the browser
                # client throw before it ever reads the plan, and a dry run
                # that answers "no" has answered, not failed.
                r = dict(d.can_add(name, stored_size(data)),
                         index=d.insertion_index(name),
                         stored_bytes=stored_size(data))
            except Exception as ex:                        # noqa: BLE001
                return self._send(200, dict(ok=False, mode="add", name=name,
                                            undoable=False, why=[str(ex)],
                                            error=str(ex)))
            r = dict(r, mode="add", name=name, undoable=False, dry=dry,
                     why=list(r.get("reasons") or []))
            if r["why"] and s.rebuild_on_overflow and not r.get("added"):
                # No room for one more entry (table slot, name block or
                # bytes): the archive is rebuilt with the new file in it.
                plan = dict(mode="add", name=name, undoable=False, dry=dry,
                            rebuild=True, why=[], reasons_in_place=r["why"])
                if dry:
                    return self._send(200, plan)
                s.pending.queue(arc, name, data, add=True)
                s.note("%s queued for %s: it is added when the archive is rebuilt (Rebuild and play)"
                       % (name, arc))
                return self._send(200, dict(plan, ok=True, added=True, queued=True))
            if dry:
                return self._send(200, r)
            if r.get("added"):
                s.note("added %s to %s (%d bytes)" % (name, arc, len(data)))
                r["ok"] = True
                return self._send(200, r)
            return self._send(200, dict(
                r, ok=False,
                error="cannot add %s: %s" % (name, "; ".join(r["why"]))))

        if route == "file_import":
            # Replace ANY file on the disc with uploaded bytes. Slot-checked,
            # journalled, compression state preserved -- the generic
            # "change anything" primitive.
            import base64
            import zlib as _z
            arc, name = body["archive"], body["name"]
            data = base64.b64decode(body["data_b64"])
            d = s.disc.archive(arc)
            e = d.find(name)
            if e.compressed:
                co = _z.compressobj(9, 8, -15)
                stored = len(co.compress(data) + co.flush())
            else:
                stored = len(data)
            rebuild = stored > e.slot and s.rebuild_on_overflow
            if body.get("dry"):
                return self._send(200, dict(dry=True, stored=stored,
                                            slot=e.slot, fits=stored <= e.slot or rebuild,
                                            rebuild=rebuild, headroom=e.slot - stored))
            if stored > e.slot and not rebuild:
                return self._send(200, dict(
                    ok=False, error="needs %d stored bytes, slot is %d"
                    % (stored, e.slot)))
            used, slot = s.write(arc, name, data, "import %s" % name)
            s.note("imported %d bytes over %s :: %s" % (len(data), arc, name))
            return self._send(200, dict(ok=True, used=used, slot=slot))

        if route in ("still_estimate", "still_replace"):
            # Put your own picture on a menu, a loading screen or the boot
            # logo. Encoding is rdrstudio/ipuenc.py; the only hard part here is
            # the BUDGET, and getting it wrong is the difference between a
            # useful estimate and one that lies on more than half the screens.
            #
            # The budget is NOT the size of the .ipu file. 60 of the 63 stills
            # are stored deflate-compressed, and on 36 of them the raw file is
            # LARGER than the slot it lives in -- options_bg_10k.ipu is 9,784
            # bytes raw in a 6,304-byte slot. Re-encoding can also go the other
            # way: mpload_hold_1.ipu re-encodes 89 bytes smaller raw but 123
            # bytes BIGGER stored. So the test mirrors dave.patch exactly:
            # compress, fall back to raw if compression grew it, compare to the
            # slot.
            #
            # And against the SMALLEST slot among every archive holding the
            # name: 7 stills live in more than one archive and 6 of those have
            # different slots in different ones, so a single encode has to fit
            # the tightest or the write lands only partly.
            import base64
            import zlib as _z
            from . import ipu as _ipu, ipuenc, ipudec

            want = body.get("name") or ""
            archives = s.disc.locate(want)
            if not archives:
                return self._send(404, {"error": "no still named %r" % want})
            # Checked here, by name, because the alternative was a KeyError
            # inside the encode try/except that came back as "could not read
            # that image: 'data_b64'" -- a message that blames the user's
            # picture for a caller that never sent one. A missing field is a
            # caller bug and has to read as one.
            if not body.get("data_b64"):
                return self._send(400, {"error": (
                    "no picture in the request: still_estimate and "
                    "still_replace both want the image as base64 under the "
                    "key 'data_b64'")})

            slots = {}
            for a in archives:
                e = s.disc.archive(a).find(want)
                slots[a] = (e.slot, e.compressed)
            budget = min(v[0] for v in slots.values())
            tightest = min(slots, key=lambda a: slots[a][0])
            any_compressed = any(v[1] for v in slots.values())

            original = s.disc.read(archives[0], want)
            try:
                head = _ipu.parse(original)
            except _ipu.NotIpu as ex:
                return self._send(400, {"error": str(ex)})
            w, h = head["width"], head["height"]

            def stored_len(raw):
                if not any_compressed:
                    return len(raw)
                co = _z.compressobj(9, 8, -15)
                p = co.compress(raw) + co.flush()
                return min(len(p), len(raw))

            try:
                # rgb_from_image takes a path or a PIL image, so raw upload
                # bytes have to be wrapped -- handing it bytes makes Pillow try
                # to read them as a filename and fail on the PNG signature.
                import io as _io
                rgb = ipuenc.rgb_from_image(
                    _io.BytesIO(base64.b64decode(body["data_b64"])), w, h,
                    fit=body.get("fit", "cover"))
            except Exception as ex:
                return self._send(400, {"error": "could not read that image: "
                                                 "%s" % ex})

            # Best quality first, stop at the first that fits. q_scale_code 1
            # is what the shipped files use; higher is coarser.
            ladder, chosen = [], None
            for q in range(1, 32):
                enc = ipuenc.encode(rgb, w, h, q_scale_code=q)
                st = stored_len(enc)
                ladder.append(dict(q=q, raw=len(enc), stored=st,
                                   fits=st <= budget))
                if st <= budget:
                    chosen = (q, enc, st)
                    break

            info = dict(name=want, width=w, height=h, archives=len(archives),
                        slot=budget, tightest_archive=tightest,
                        original_raw=len(original),
                        original_stored=stored_len(original), ladder=ladder)
            if chosen is None:
                info.update(ok=False, error=(
                    "even the coarsest quality does not fit: %d stored bytes "
                    "against a %d-byte slot. A simpler image -- flatter "
                    "colour, less fine detail -- compresses far better."
                    % (ladder[-1]["stored"], budget)))
                return self._send(200, info)

            q, enc, st = chosen
            info.update(ok=True, quality=q, stored=st, headroom=budget - st,
                        quality_note=("full quality" if q == 1
                                      else "quality reduced to fit the slot"))
            if route == "still_estimate":
                info["dry"] = True
                return self._send(200, info)

            # What we are about to write must decode back. Cheap, and it means
            # a bad encode is caught here rather than as a black screen.
            try:
                dw, dh, _ = ipudec.decode(enc)
            except Exception as ex:
                return self._send(200, dict(ok=False, error=(
                    "refusing to write: the encoded image does not decode "
                    "back (%s). That is an encoder bug, not a problem with "
                    "your picture." % ex)))
            if (dw, dh) != (w, h):
                return self._send(200, dict(ok=False, error=(
                    "refusing to write: encode returned %dx%d, expected %dx%d"
                    % (dw, dh, w, h))))

            # ONE undo step for one user action. Replacing a still touches up
            # to 62 archives, and without a shared group that was 62 presses
            # of Ctrl+Z for a thing the tool described as "replace this
            # screen". `journal.batch()` only defers the autosave; it does not
            # assign a group, which is why this looked handled and was not.
            done, failed = [], []
            group = s.journal.new_group()
            for a in archives:
                try:
                    s.write(a, want, enc, "replace still %s" % want, group)
                    done.append(a)
                except Exception as ex:
                    failed.append(dict(archive=a, error=str(ex)))
            s.note("replaced %s in %d of %d archives (quality %d, %d of %d "
                   "bytes)" % (want, len(done), len(archives), q, st, budget))
            info.update(ok=bool(done), written=done, failed=failed)
            return self._send(200, info)

        if route == "texture_import":
            import base64
            from . import texture as T
            arc = body["archive"]
            lvl = s.level(arc, area=body.get("area"))
            r = lvl._load_rsc()
            if r is None:
                return self._send(404, {"error": "%s has no resource bundle" % arc})
            table = r.texture_table()
            i = int(body["i"])
            if not (0 <= i < len(table)):
                return self._send(404, {"error": "no texture %d" % i})
            _, info, mip, pal = table[i]

            png = base64.b64decode(body["png_b64"])
            w, h, rgba = T.read_png(png)
            new_mip, new_pal = T.encode_into(r.raw(mip.index), r.raw(pal.index),
                                             rgba, w, h)

            # RSC chunks live inside one archive file, so patch the whole file
            # with both chunks replaced -- byte counts are unchanged.
            rsc_bytes = bytearray(r.data)
            rsc_bytes[mip.offset:mip.offset + mip.size] = new_mip
            rsc_bytes[pal.offset:pal.offset + pal.size] = new_pal
            used, slot = s.write(arc, lvl.rsc_name, bytes(rsc_bytes),
                                 "replace texture %s" % info["name"])
            s._drop_level(arc)
            s.note("replaced texture %s in %s" % (info["name"], arc))
            return self._send(200, dict(ok=True, name=info["name"],
                                        width=w, height=h, used=used, slot=slot))

        if route == "stream_import":
            # Replace one song/ambience stream with a WAV. M13.
            #
            # Streams are stored RAW -- 0 of 8,361 entries in STREAMS.DAT are
            # compressed -- so unlike a .tbl or a .csv, length here really is
            # size and there is no deflate surprise. What there is instead is
            # almost no room: the median entry has 512 spare bytes, 2,064 have
            # ZERO, and the two biggest (endcredits_xbox at 26 MB) have none.
            # So a replacement is bounded by the slot it is going into, and
            # the honest thing is to say by how much BEFORE writing.
            from . import stm
            import base64
            arc = body.get("archive") or "STREAMS.DAT"
            name = body.get("name")
            if not name:
                return self._send(400, {"error": "this needs a stream name"})
            try:
                wav = base64.b64decode(body.get("wav_b64") or "")
            except Exception as ex:
                return self._send(400, {"error": "bad wav_b64: %s" % ex})
            try:
                dave = s.disc.archive(arc)
                entry = dave.find(name)
            except Exception as ex:
                return self._send(404, dict(error=str(ex)))

            try:
                rate, chans = stm.from_wav(wav)
            except Exception as ex:
                return self._send(400, {"error": "%s" % ex})
            if rate not in (48000, 24000, 12000):
                # Said plainly rather than resampled silently. The driver
                # compares this field against exactly these three and rejects
                # anything else, so a 44.1k WAV -- which is what most tools
                # produce -- would make a file the game will not load.
                return self._send(400, {
                    "error": "the driver accepts 48000, 24000 or 12000 Hz "
                             "only, and this WAV is %d Hz." % rate,
                    "fix": "Resample it to 24000 Hz (what most of the disc "
                           "uses) or 48000 Hz before importing. This will not "
                           "resample for you: doing it silently would hide a "
                           "choice that changes how the music sounds."})

            loop_at = body.get("loop_at")
            try:
                out = stm.encode(chans, rate,
                                 loop_at=None if loop_at is None
                                 else int(loop_at))
            except ValueError as ex:
                return self._send(400, {"error": "%s" % ex})

            fits = len(out) <= entry.slot
            plan = dict(
                name=name, archive=arc,
                bytes_before=entry.size_full, bytes_after=len(out),
                slot=entry.slot, headroom=entry.slot - len(out),
                fits=fits, rate=rate, channels=len(chans),
                seconds=round(len(chans[0]) / float(rate), 2),
                looped=loop_at is not None,
                undo=dict(steps=1))
            if not fits:
                return self._send(200, dict(
                    ok=False, plan=plan,
                    problem=dict(
                        code="slot_overflow",
                        message="%s needs %d bytes and its slot is %d -- %d "
                                "over." % (name, len(out), entry.slot,
                                           len(out) - entry.slot),
                        fix="Use a shorter clip, or the same length at a "
                            "lower rate. Streams are stored uncompressed, so "
                            "seconds map straight to bytes: this slot holds "
                            "about %.1f s at %d Hz %s."
                            % (entry.slot / float(rate * len(chans) / 2.0),
                               rate,
                               "stereo" if len(chans) == 2 else "mono"),
                        detail="")))
            if body.get("dry"):
                return self._send(200, dict(ok=True, dry=True, plan=plan))

            used, slot = s.write(arc, name, out,
                                 "replace stream %s" % name)
            s.note("%s :: replaced %s (%d/%d bytes, %.2fs %dHz %s%s)"
                   % (arc, name, used, slot, plan["seconds"], rate,
                      "stereo" if len(chans) == 2 else "mono",
                      ", looped" if loop_at is not None else ""))
            return self._send(200, dict(ok=True, applied=True, plan=plan,
                                        used=used, slot=slot))

        if route == "weapon_model_import":
            # Stats > Weapons & stats > Weapon model: a .mod (the PS2's) or .xmod (the Xbox's, made the PS2's) put
            # over the weapon's model, in EVERY archive copy (as weapon_import below): each slot is checked first and
            # nothing is written unless it fits them all. `dry` defaults to TRUE, as weapon_import's.
            import base64 as _b64
            import sys as _sys
            from .dataeditor import _LIB
            if _LIB not in _sys.path:
                _sys.path.insert(0, _LIB)
            from rdrformats import mod as _modfmt
            target = body.get("target")
            fname = (body.get("filename") or "").strip()
            dry = body.get("dry", True)
            if not target or not body.get("data"):
                return self._send(200, dict(ok=False, dry=bool(dry), written=False, problem=dict(
                    message="this needs the weapon's model and a .mod or .xmod file.",
                    fix="Pick a weapon, then Import .mod / .xmod.")))
            try:
                data = _b64.b64decode(body["data"])
            except Exception:                                   # noqa: BLE001
                return self._send(400, {"error": "the file did not arrive whole"})
            notes = []
            if fname.lower().endswith(".xmod"):
                try:
                    data, notes = _weapon_model_convert(s.disc, data, target, False)
                except Exception as ex:                         # noqa: BLE001
                    return self._send(200, dict(ok=False, dry=bool(dry), written=False, problem=dict(
                        message="%s could not be made the PS2's: %s" % (fname, ex),
                        fix="Is it an Xbox .xmod (a model, not a bundle .xrsc)?")))
            try:
                m = _modfmt.read(data)
                tris = sum(len(prim.triangles()) for pk in m.packets() for prim in pk.primitives)
            except Exception as ex:                             # noqa: BLE001
                return self._send(200, dict(ok=False, dry=bool(dry), written=False, problem=dict(
                    message="%s is not a model the Studio can read: %s" % (fname or "the file", ex),
                    fix="Export a .mod or .xmod from the Blender add-on or another weapon.")))
            copies = list(s.disc.locate(target))
            if not copies:
                return self._send(200, dict(ok=False, dry=bool(dry), written=False, problem=dict(
                    message="the working disc has no %s." % target, fix="Pick another weapon.")))
            from . import modimport as _mi
            too_small, stored = [], None
            for ca in copies:
                ce = s.disc.archive(ca).find(target)
                need = _mi.stored_size(data, compressed=ce.compressed)
                stored = need if stored is None else max(stored, need)
                if need > ce.slot:
                    too_small.append((ca, ce.slot, need))
            plan = dict(target=target, copies=len(copies), bytes=len(data), stored=stored,
                        slot=min(s.disc.archive(ca).find(target).slot for ca in copies), notes=notes,
                        triangles=tris)
            if too_small:
                ca, cslot, need = min(too_small, key=lambda t: t[1])
                return self._send(200, dict(ok=False, dry=bool(dry), written=False, plan=plan, problem=dict(
                    code="slot_overflow",
                    message="this model needs %d bytes but the copy in %s only has %d." % (need, ca, cslot),
                    fix="Use a smaller model (fewer triangles), or a New weapon with its own model.")))
            if dry:
                return self._send(200, dict(ok=True, dry=True, written=False, plan=plan))
            grp = s.journal.new_group()
            with s.journal.batch():
                for ca in copies:
                    used, slot = s.write(ca, target, data, "import weapon model %s (%s)" % (target, fname), grp)
            s.note("imported %s over %s in %d archive copies" % (fname, target, len(copies)))
            return self._send(200, dict(ok=True, dry=False, written=True, plan=plan,
                                        message="%s is now %s, in all %d copies on the working disc." % (
                                            target, fname, len(copies)),
                                        next="Press Play to see it (Build (Xbox) for the Xbox game). Ctrl+Z undoes it."))

        if route == "weapon_import":
            # M15: an OBJ becomes a weapon model. Plans and refuses here; the
            # repoint half is REQ-045 and is not in this build.
            #
            # `dry` DEFAULTS TO TRUE on this route. studio.py's older routes
            # read `bool(body.get("dry"))`, so an omitted flag means WRITE,
            # while routes2/api.is_dry reads the same absence as DRY. The two
            # halves of this file disagree, and the losing side of that
            # disagreement is a model silently overwritten. So this one takes
            # the safe reading AND states which it took in every response --
            # `dry` and `written` are always present, so a caller can never
            # be left guessing whether anything happened.
            from . import api, modimport
            arc = body.get("archive") or modimport.WEAPON_ARCHIVE
            target = body.get("target") or body.get("name")
            if not target:
                return self._send(200, api.Refusal(
                    "bad_request", "this needs the model to import over, as "
                    "`target`.", fix="Pick a weapon first.").body())
            obj_text = body.get("obj")
            if not obj_text:
                return self._send(200, api.Refusal(
                    "bad_request", "this needs the .obj text, as `obj`.",
                    fix="Choose an .obj file to import.").body())
            dry = body.get("dry", True)

            try:
                plan, problem, data = modimport.plan(
                    s.disc, obj_text, target, archive=arc,
                    material=body.get("material") or "imported",
                    texture=body.get("texture") or "westgun",
                    # No tick-box any more: sharing is a WARNING in the UI
                    # (plan.shared_by names who changes), not a gate.
                    allow_shared=bool(body.get("allow_shared", True)))
            except modimport.ImportRefused as ex:
                return self._send(200, dict(ok=False, dry=bool(dry),
                                            written=False,
                                            problem=ex.as_problem()))
            except Exception as ex:
                return self._send(404, dict(error=str(ex)))

            if problem is not None:
                # The plan still rides along: "it does not fit" with no number
                # is not something a user can act on, and openSheet needs a
                # plan present or it renders an unpressable button.
                return self._send(200, dict(ok=False, dry=bool(dry),
                                            written=False, plan=plan,
                                            problem=problem))
            if dry:
                return self._send(200, dict(ok=True, dry=True, written=False,
                                            plan=plan))

            # `data` is what plan() costed -- not a rebuild. If the two ever
            # diverged, the bytes checked against the slot would not be the
            # bytes written to it.
            # Every level archive carries its own identical copy of each
            # weapon model (62 of them + SWAT.DAT, measured), and the level
            # being played may read its own. So the import goes into EVERY
            # copy, all-or-nothing: each slot is checked before any byte moves.
            copies = [a for a in s.disc.locate(plan["target"])] or [arc]
            too_small = []
            for ca in copies:
                ce = s.disc.archive(ca).find(plan["target"])
                if plan["stored"] > ce.slot:
                    too_small.append((ca, ce.slot))
            if too_small:
                ca, cslot = min(too_small, key=lambda t: t[1])
                return self._send(200, dict(
                    ok=False, dry=False, written=False, plan=plan,
                    problem=dict(
                        code="slot_overflow",
                        message="this model needs %d bytes packed but the copy "
                                "in %s only has %d." % (plan["stored"], ca, cslot),
                        fix="Reduce the model's triangle count.", detail="")))
            grp = s.journal.new_group()
            with s.journal.batch():
                for ca in copies:
                    used, slot = s.write(ca, plan["target"], data,
                                         "import weapon model %s" % plan["target"],
                                         grp)
            s.note("%s :: imported %s into %d archive copies (%d tris, %d/%d bytes packed)"
                   % (arc, plan["target"], len(copies), plan["triangles"], used, slot))
            who = plan.get("weapons_affected") or []
            return self._send(200, dict(
                ok=True, dry=False, written=True, applied=True, plan=plan,
                used=used, slot=slot,
                message="Done: %s now has your shape (%d triangles, %d of %d "
                        "bytes used)%s." % (
                            plan["target"], plan["triangles"], used, slot,
                            "; changes %d weapons: %s" % (
                                len(who), ", ".join(who)) if len(who) > 1
                            else ""),
                next="Next: press Boot test in the top bar (or run RDR_MODDED.iso "
                     "in PCSX2) to see it. Ctrl+Z undoes this."))

        if route == "sound_import":
            # Replace one sound in a bank with a WAV.
            #
            # Patched in place, so the re-encoded sound must fit the slot the
            # original occupied -- the bank is a flat run of blocks and the
            # `.hd` holds absolute offsets into it, so moving anything would
            # invalidate every later sound.
            from . import vag
            arc = body.get("archive") or "BANKS.DAT"
            name = body.get("name")
            idx = int(body.get("index", 0))
            import base64
            try:
                wav = base64.b64decode(body.get("wav_b64") or "")
            except Exception as ex:
                return self._send(400, {"error": "bad wav_b64: %s" % ex})
            try:
                data = s.disc.read(arc, name)
            except Exception as ex:
                return self._send(404, dict(error=str(ex)))
            hd = None
            try:
                hd = s.disc.read(arc, name[:-3] + ".hd")
            except Exception:
                pass
            snds = vag.sounds_with_rates(data, hd)
            if not 0 <= idx < len(snds):
                return self._send(404, {"error": "sound %d of %d"
                                                 % (idx, len(snds))})
            try:
                samples, in_rate = vag.from_wav(wav)
            except vag.VagError as ex:
                return self._send(400, {"error": str(ex)})
            enc = vag.encode(samples)
            slot = snds[idx]["blocks"] * vag.BLOCK
            rep = dict(name=name, index=idx, slot_bytes=slot,
                       encoded_bytes=len(enc), spare=slot - len(enc),
                       wav_rate=in_rate, plays_at=snds[idx]["rate"],
                       samples=len(samples))
            if len(enc) > slot:
                return self._send(400, dict(
                    rep, ok=False,
                    error="the encoded sound needs %d bytes of a %d-byte slot "
                          "(over by %d). Shorten the clip: a bank is patched "
                          "in place and the .hd holds absolute offsets, so "
                          "nothing may grow." % (len(enc), slot, len(enc) - slot)))
            if body.get("dry"):
                return self._send(200, dict(rep, ok=True, dry=True))
            out = bytearray(data)
            off = snds[idx]["offset"]
            out[off:off + len(enc)] = enc
            # Pad the remainder with silent END blocks so the voice stops
            # rather than running into whatever the old sound left behind.
            pad = off + len(enc)
            while pad + vag.BLOCK <= off + slot:
                blk = bytearray(vag.BLOCK)
                blk[1] = vag.FLAG_END
                out[pad:pad + vag.BLOCK] = blk
                pad += vag.BLOCK
            used, arc_slot = s.write(arc, name, bytes(out),
                                     "sound %s #%d from WAV" % (name, idx))
            s.note("%s: replaced sound %d (%d bytes of %d)"
                   % (name, idx, len(enc), slot))
            return self._send(200, dict(rep, ok=True, written=True,
                                        used=used, archive_slot=arc_slot))

        # ------------------------------------------------------- game rules
        # progression.py patches the Dave directly, which would leave the
        # undo history blind to the edit. `_ProgressionDisc` gives it the two
        # methods it actually uses and routes the patch through s.write, so
        # these edits journal and revert like every other write in the tool.
        if route in ("progression_mission", "progression_unlockable",
                     "progression_bhunter"):
            from . import progression
            dry = body.get("dry", True) is not False
            row = int(body["row"])
            if route == "progression_mission":
                label = "levelflow.csv row %d: %s = %s" % (
                    row, body["key"], body["value"])
            elif route == "progression_unlockable":
                label = "unlockables.csv row %d: %s = %s" % (
                    row, body["key"], body["value"])
            else:
                label = "levelflow.csv row %d: BHunter health/dead-eye" % row
            disc = _ProgressionDisc(s, label)
            try:
                if route == "progression_mission":
                    out = progression.set_mission_field(
                        disc, row, body["key"], body["value"], dry=dry)
                elif route == "progression_unlockable":
                    out = progression.set_unlockable_field(
                        disc, row, body["key"], body["value"], dry=dry)
                else:
                    out = progression.set_bhunter_knobs(
                        disc, row, health=body.get("health"),
                        deadeye=body.get("deadeye"), dry=dry)
            except progression.WriteRefused as ex:
                # Verbatim. These messages are written for the developer and already
                # say what to do instead; rewording them here loses that.
                return self._send(400, {"error": str(ex)})
            if out.get("written"):
                s.note(label)
            return self._send(200, out)

        if route == "campaign_cell":
            from . import campaign
            fname = campaign.FILES[body["file"]]
            data = s.disc.read("SWAT.DAT", fname)
            new = campaign.set_cell(data, int(body["row"]), int(body["col"]),
                                    body["value"])
            used, slot = s.write("SWAT.DAT", fname, new,
                                 "%s row %s col %s = %s"
                                 % (fname, body["row"], body["col"], body["value"]))
            return self._send(200, dict(ok=True, used=used, slot=slot))

        if route == "unlock_all":
            from . import campaign
            fname = campaign.FILES["unlockables"]
            data = s.disc.read("SWAT.DAT", fname)
            new, changed = campaign.unlock_all(data, body.get("type") or None)
            if not changed:
                return self._send(200, dict(ok=True, changed=0,
                                            note="already all unlocked"))
            used, slot = s.write("SWAT.DAT", fname, new,
                                 "unlock %d entries" % changed)
            s.note("unlocked %d entries in unlockables.csv" % changed)
            return self._send(200, dict(ok=True, changed=changed,
                                        used=used, slot=slot))

        # `group` lets the caller chain the two halves of one character edit:
        # the `.cr` write returns its journal group and the `.swt` write joins
        # it, so "health 400, punch 33" is ONE Ctrl+Z -- which is what the
        # Creatures panel promises. Undo only pops the TRAILING run of a
        # group, so a stale id cannot drag older edits along with it.
        if route == "creature_save":
            return self._send(200, s.edit_creature(
                body["name"], body["changes"],
                everywhere=body.get("everywhere", True),
                dry=bool(body.get("dry")), group=body.get("group")))

        if route == "combat_save":
            return self._send(200, s.edit_swt(
                body["name"], body["changes"],
                everywhere=body.get("everywhere", True),
                dry=bool(body.get("dry")), group=body.get("group")))

        if route == "gun_save":
            return self._send(200, s.edit_gun(
                body["name"], body["changes"],
                everywhere=body.get("everywhere", True),
                dry=bool(body.get("dry"))))

        if route in ("undo", "redo"):
            r = s.undo() if route == "undo" else s.redo()
            return self._send(200, dict(
                ok=bool(r), label=r["label"] if r else None,
                count=r["count"] if r else 0,
                archive=r["archive"] if r else None,
                file=r["file"] if r else None,
                can_undo=s.journal.can_undo, can_redo=s.journal.can_redo))

        if route == "mod_save":
            name = (body.get("name") or "untitled").strip()
            safe = "".join(c for c in name if c.isalnum() or c in " _-").strip()
            safe = safe.replace(" ", "_") or "untitled"
            path = os.path.join(cfg.MODS, safe + ".rdrmod.json")
            s.journal.export(path, name, body.get("notes", ""))
            files = len(s.journal.final_state())
            s.note("saved mod '%s' (%d file(s)) -> %s" % (name, files, path))
            return self._send(200, dict(ok=True, path=path, name=name,
                                        files=files))

        if route == "mod_load":
            path = body["path"]
            doc = read_mod(path)
            applied, failed = apply_mod(doc, s.disc)
            # One autosave for the whole mod. Recording each file individually
            # rewrote the entire session document -- 28 MB on a working session
            # -- once per file, which is what made loading a 650-file mod take
            # 322 seconds against about 6 seconds of real work.
            # Applying a mod is ONE action. It was one journal entry per file,
            # so a 650-file mod was 650 presses of Ctrl+Z while the tool said
            # "Ctrl+Z to undo".
            group = s.journal.new_group()
            with s.journal.batch():
                for a in applied:
                    data = s.disc.archive(a["archive"]).read(a["file"])
                    s.journal.record(a["archive"], a["file"], data,
                                     "apply mod '%s'" % doc.get("name", "?"),
                                     group)
            s._levels = {}
            # Advertise the mod on the front-end Options page. Cosmetic, and
            # deliberately guarded: a list of names must never be able to fail
            # a mod load. Cheap next to the trap described above -- one small
            # archive member read and patched ONCE, not per file.
            # The page is also its own registry (it records what it lists), so
            # this appends rather than replacing. Caveat, because it is real:
            # the patch goes through Dave outside the journal, so an undo or a
            # checkpoint restore will NOT drop the name again. The list
            # over-reports, never under-reports.
            # See 03_Tools/rdrstudio/frontmenu.py.
            # `s.game`, NOT `s.disc`: the disc property falls back to the
            # PRISTINE ISO while PCSX2 holds the working one, so a write
            # through it aims at the vanilla disc. It fails safe today only
            # because vanilla is opened writable=False -- the right outcome by
            # the wrong route, and one flag change away from writing the
            # master. Skip instead, and say so. (Caught by rdr-app on review.)
            game = s.game
            if game is not None:
                try:
                    frontmenu.add_mod(game.archive(frontmenu.ARCHIVE),
                                      doc.get("name") or "untitled")
                except Exception as ex:                       # noqa: BLE001
                    print("  could not list '%s' on the front-end menu: %s"
                          % (doc.get("name"), ex))
            s.note("applied mod '%s': %d file(s), %d failed"
                   % (doc.get("name"), len(applied), len(failed)))
            return self._send(200, dict(ok=not failed, name=doc.get("name"),
                                        notes=doc.get("notes", ""),
                                        applied=applied, failed=failed))

        if route == "revert_file":
            return self._send(200, s.revert_file(body["archive"], body["name"]))

        if route == "checkpoint":
            return self._send(200, s.checkpoint(body.get("label", "")))

        if route == "checkpoint_restore":
            return self._send(200, s.restore_checkpoint(body["path"]))

        if route == "release":
            # Let PCSX2 open the ISO: the Studio's writable handle blocks it
            # (errno 13). hand_over() keeps the Studio out until the file is
            # genuinely free again -- s.close() alone was not enough, because
            # the next request took it straight back.
            s.hand_over()
            s.log.append("[%s] released ISO handles for PCSX2"
                         % time.strftime("%H:%M:%S"))
            return self._send(200, dict(ok=True))

        if route == "rebuild_pending":
            # "Rebuild and play", first half: every archive with queued edits
            # rebuilt once, as a job the page polls (disc_state.rebuild_job).
            from . import dataeditor as _dem0
            if _dem0.DataEditor.pcsx2_running():
                return self._send(200, dict(ok=False, error_text=_dem0.DataEditor.PCSX2_BUSY))
            job = getattr(s, "_rebuild_job", None)
            if job and job.get("state") == "running":
                return self._send(200, dict(ok=True, job=job))
            job = s._rebuild_job = dict(state="running", done=0, total=len(s.pending.archives()),
                                        archive="", error="", rebuilt=[], started=time.time())

            def _run():
                try:
                    def prog(n, total, arc):
                        job.update(done=n, total=total, archive=arc)
                    r = s.rebuild_pending(prog)
                    job.update(state="done", done=len(r), rebuilt=r)
                except Exception as ex:                    # noqa: BLE001
                    job.update(state="error", error=str(ex))
            threading.Thread(target=_run, name="rebuild_pending", daemon=True).start()
            return self._send(200, dict(ok=True, job=job))

        if route == "play":
            # Release, then launch, in that order and from one click. Doing it
            # by hand is a trap: the Studio reclaims the write handle on its
            # next action, so a disc that was free a second ago is locked
            # again by the time the emulator's file dialog opens.
            if not s.pcsx2 or not os.path.exists(s.pcsx2):
                return self._send(400, dict(
                    error="PCSX2 not found at %r" % s.pcsx2))
            if s.pending.archives():
                # Rebuild and play: the queued edits go on the disc first.
                try:
                    s.rebuild_pending()
                except Exception as ex:                    # noqa: BLE001
                    return self._send(500, dict(error="could not rebuild the queued .dat files: %s" % ex))
            s.hand_over()
            try:
                subprocess.Popen([s.pcsx2, "-fastboot", s.modded_path])
            except Exception as ex:
                return self._send(500, dict(error="could not launch: %s" % ex))
            s.note("released the disc and launched PCSX2")
            return self._send(200, dict(ok=True, launched=True))

        if route == "restore":
            saved, n = s.restore(bool(body.get("clear_mods")), body.get("save", True) is not False,
                                 body.get("save_name") or None, body.get("include_new", True) is not False)
            return self._send(200, dict(ok=True, saved_mod=saved, cleared=n))

        if route == "boot":
            return self._send(200, s.boot_test(body.get("seconds")))

        # Routes added by the 2026-08 redesign. Every one of them dry-runs by
        # default and opens exactly one journal group; see rdrstudio/routes2.py.
        from . import routes2
        if routes2.has_post(route):
            return routes2.handle_post(self, s, route, body)

        from savedata import routes_save
        if routes_save.has_post(route):
            return routes_save.handle_post(self, s, route, body)

        return self._send(404, {"error": "no such route: %s" % route})


def _lan_ip():
    """Best-guess LAN address, for printing a reachable URL."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no packets sent; just picks a route
        return s.getsockname()[0]
    except Exception:
        return socket.gethostbyname(socket.gethostname())
    finally:
        s.close()


def serve(studio, port=8731, open_browser=True, host="127.0.0.1"):
    Handler.studio = studio
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = "http://127.0.0.1:%d/" % port
    print("RDR Modding Studio")
    print("  vanilla : %s" % studio.vanilla_path)
    print("  modded  : %s" % studio.modded_path)
    print("  serving : %s" % url)
    if host not in ("127.0.0.1", "localhost"):
        print("  network : http://%s:%d/   <- reachable from your LAN" % (_lan_ip(), port))
        print("  WARNING : no authentication. Anyone on this network can edit")
        print("            or restore your working ISO. Use on trusted Wi-Fi only.")
    print("  Ctrl-C to stop\n")
    if open_browser:
        threading.Timer(1.0, lambda: __import__("webbrowser").open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        httpd.server_close()
        studio.close()


class _LevelFiles:
    """levelmeta's game interface (read, toc) over one Level's own archive -- a mods folder level's files."""

    def __init__(self, lvl):
        self.lvl = lvl

    def read(self, archive, name):
        return self.lvl._read(name)

    def toc(self, archive):
        return list(self.lvl.dave)


class _FolderLevelGame:
    """The game, with one level archive more: a mods folder level packed in memory (Studio.level)."""

    def __init__(self, game, archive, folder):
        from . import dave as D
        from .dataeditor import DataEditor, _Bytes
        self._game = game
        self._name = archive.upper()
        self._dave = D.Dave(_Bytes(D.create(DataEditor.pack_folder(folder))), archive)

    def archive(self, name):
        if name.upper() == self._name:
            return self._dave
        return self._game.archive(name)

    def archives(self):
        return list(self._game.archives()) + [self._name]

    def __getattr__(self, attr):
        return getattr(self._game, attr)


def _data_editor_for(studio):
    """The Data Editor of this Studio (its mods folder), or None."""
    try:
        return _data_editor(studio)
    except Exception:                                       # noqa: BLE001
        return None


def _weapon_model_convert(disc, data, name, to_xbox):
    """A weapon model between the PS2's .mod and the Xbox's .xmod (dataeditor_lib/modconvert.convert_model: a skinned
    model is bone relative on the PS2 and model space on the Xbox, moved with its .skel read from the working disc; a
    one bone prop is the same on both). -> (bytes, notes)."""
    import sys as _sys
    from .dataeditor import _LIB
    if _LIB not in _sys.path:
        _sys.path.insert(0, _LIB)
    import modconvert as MC

    class _Disc:
        def find(self, fname, prefer=()):
            for a in disc.locate(fname):
                try:
                    return a, disc.read(a, fname)
                except Exception:                               # noqa: BLE001
                    continue
            return None, None
    notes = []
    out = MC.convert_model(data, name, to_xbox, {}, [_Disc()], notes)
    return out, notes
