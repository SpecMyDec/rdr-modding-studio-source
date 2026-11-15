"""Importing a weapon model: an OBJ in, a text `.mod` out. M15's write half.

`modtext` (rdr-3d's) READS the `version: 1.10` dialect and now reconciles
249/251 against the files' own declared counts. This module is the other
direction, and it is deliberately a separate file: `modtext.py` was created
after the lane split and is named to rdr-3d, so under REQ-042 rule 1 the
reader is theirs. Nothing here edits it.

## What this does NOT do, and why the milestone is not finished

**It does not repoint `ModelName`, so on its own it cannot safely land a new
weapon model.** That is not an oversight, it is the open half of REQ-045:

    ptl_lod_01   named by 11 of the 55 weapons
    rfl_lod_01   named by 11
    shotgun01    named by  9
    pt2_lod_01   named by  6
    bay_lod_01   named by  5

    55 weapons, 14 distinct models between them

Overwriting `ptl_lod_01.mod` in place re-models ELEVEN weapons at once. Import
a new pistol and you silently also get it as the Revolver, the Twin_Revolvers
and eight more. This is M9's defect in another costume -- there a `.cr` copied
into many levels, here one `.mod` referenced by many weapons -- and the rule
is the same: **the unit the user edits is not the unit the game shares.**

The safe path is M9's: write the model as a NEW `.mod` and repoint ONE
weapon's `ModelName` at it. That last step is blocked -- none of the four name
fields is in `guns.EDITABLE`:

    ModelName  SecondaryModelName  HolsterModelName  AlternateTextureName
                        all editable: False

Making one editable is a feature, so under REQ-042 rule 3 it is a CLAIM on
`guns.py`, and the call is with the developer. Until it lands, `plan()` will cost and
check an overwrite and then REFUSE it whenever the model is shared, rather
than write something that quietly changes ten other weapons.

## Slot fit is judged after deflate, because length is not size here

All **3,008** `.mod` entries in `SWAT.DAT` are compressed -- not one is stored
raw. So C-030 applies squarely: a replacement the same length as the original
can still overflow, and a longer one can fit. Every size this module reports
is the DEFLATED size.

The room is tight, and it is the real constraint on the milestone:

    ptl_lod_01.mod   raw 22,581 -> stored 5,569 of a 6,144 slot    575 spare
    rfl_lod_01.mod   raw 19,918 -> stored 5,542 of a 6,144 slot    602 spare
    bay_lod_01.mod   raw  8,255 -> stored 5,648 of a 5,664 slot     16 spare
    shotgun01.mod    raw  9,105 -> stored 6,248 of a 6,304 slot     56 spare

    largest free run in SWAT.DAT for a NEW file: 6,425 bytes

So a new weapon model gets about 6.4 KB compressed, which is roughly what the
shipped ones cost -- workable, but a model of any generosity will not fit, and
the plan says so with the numbers rather than failing at write time.

## The output is `tri` records only, and that is a real tradeoff

The dialect has three primitive forms: `str` and `stp` (triangle strips) and
`tri` (a standalone triangle). This writer emits only `tri`.

Strips are denser -- a strip of N triangles costs N+2 indices against 3N --
so a stripped file would compress smaller and leave more headroom. But
generating strips means generating the exact walk that rdr-3d spent three
silent-failure bugs getting RIGHT in the reader, and a strip I emit wrongly
produces a weapon that looks plausible and is wrong. `tri` has no walk to get
wrong: three indices, one triangle, no winding alternation.

This is not a guess about legality: `edge_arrow.mod` ships as 50 `tri` records
and no strips at all, and 15,157 `tri` records appear across 152 of the 251
text files. The cost is measured and reported in every plan as `strip_saving`,
so the tradeoff is visible rather than assumed.
"""

import re
import struct
import zlib

from . import modtext

# Weapon models live loose in SWAT.DAT alongside everything else.
WEAPON_ARCHIVE = "SWAT.DAT"

# What a fresh single-material model declares. `colors`, `matrices` and
# `reskins` ship as 1 even on models carrying no such record -- edge_arrow
# declares `reskins: 1` and writes none -- so 1 is the floor, not a count.
_FLOOR = 1


class ImportRefused(Exception):
    """Something the user can fix, carrying the fix. Never a bare failure."""

    def __init__(self, message, fix="", code="bad_request", detail=""):
        Exception.__init__(self, message)
        self.message = message
        self.fix = fix
        self.code = code
        self.detail = detail

    def as_problem(self):
        return dict(code=self.code, message=self.message,
                    fix=self.fix, detail=self.detail)


# ---------------------------------------------------------------- reading OBJ

_FACE = re.compile(r"^(\d+)(?:/(\d*)(?:/(\d*))?)?$")


def from_obj(text):
    """An OBJ into the corner form a `.mod` wants.

    A `.mod` `adj` record is one corner's bundle of attribute indices -- the
    same idea as an OBJ `v/vt/vn` triple -- so the mapping is direct and needs
    no guessing. Returns positions, normals, uvs and triangles of corners.

    Raises ImportRefused with a fix on anything it cannot read. It does NOT
    quietly drop bad faces: a model that silently loses geometry is the
    REQ-039 shape, where the absence is indistinguishable from it working.
    """
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")

    pos, uvs, nrm, tris = [], [], [], []
    ngon = quads = 0

    for ln, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        w = s.split()
        tag = w[0]
        if tag == "v":
            if len(w) < 4:
                raise ImportRefused(
                    "line %d: a 'v' with fewer than 3 numbers." % ln,
                    "Re-export the OBJ; this file is truncated or not an OBJ.")
            try:
                pos.append((float(w[1]), float(w[2]), float(w[3])))
            except ValueError:
                raise ImportRefused(
                    "line %d: could not read a position." % ln,
                    "Re-export the OBJ.")
        elif tag == "vt":
            try:
                uvs.append((float(w[1]), float(w[2]) if len(w) > 2 else 0.0))
            except (ValueError, IndexError):
                raise ImportRefused("line %d: could not read a texture "
                                    "coordinate." % ln, "Re-export the OBJ.")
        elif tag == "vn":
            if len(w) < 4:
                raise ImportRefused("line %d: a 'vn' with fewer than 3 "
                                    "numbers." % ln, "Re-export the OBJ.")
            try:
                nrm.append((float(w[1]), float(w[2]), float(w[3])))
            except ValueError:
                raise ImportRefused("line %d: could not read a normal." % ln,
                                    "Re-export the OBJ.")
        elif tag == "f":
            corners = []
            for tok in w[1:]:
                m = _FACE.match(tok)
                if not m:
                    if tok.startswith("-"):
                        raise ImportRefused(
                            "line %d: this OBJ uses negative (relative) face "
                            "indices, which this importer does not read."
                            % ln,
                            "Re-export with absolute indices -- most "
                            "exporters do this by default.")
                    raise ImportRefused(
                        "line %d: could not read the face corner %r." %
                        (ln, tok), "Re-export the OBJ.")
                vi = int(m.group(1)) - 1
                ti = int(m.group(2)) - 1 if m.group(2) else None
                ni = int(m.group(3)) - 1 if m.group(3) else None
                corners.append((vi, ti, ni))
            if len(corners) < 3:
                raise ImportRefused(
                    "line %d: a face with %d corners." % (ln, len(corners)),
                    "Every face needs at least 3 corners.")
            if len(corners) == 4:
                quads += 1
            elif len(corners) > 4:
                ngon += 1
            # Fan-triangulate. Correct for convex faces, which is what a
            # triangulating exporter produces; a concave n-gon would fan
            # wrongly, so those are counted and reported, not hidden.
            for i in range(1, len(corners) - 1):
                tris.append((corners[0], corners[i], corners[i + 1]))

    if not pos:
        raise ImportRefused(
            "there are no vertices in this file.",
            "Pick an .obj file -- this one has no 'v' lines at all.")
    if not tris:
        raise ImportRefused(
            "there are vertices but no faces in this file.",
            "Export with faces included. A model with no faces would show up "
            "in the game as nothing at all.")

    # Every index must be in range BEFORE anything is built. An out-of-range
    # corner that survives into the .mod is the exact failure rdr-3d hit from
    # the other side: geometry that parses and is wrong.
    for a, b, c in tris:
        for vi, ti, ni in (a, b, c):
            if not (0 <= vi < len(pos)):
                raise ImportRefused(
                    "a face refers to vertex %d but the file has %d."
                    % (vi + 1, len(pos)),
                    "The OBJ is inconsistent; re-export it.")
            if ti is not None and not (0 <= ti < len(uvs)):
                raise ImportRefused(
                    "a face refers to texture coordinate %d but the file has "
                    "%d." % (ti + 1, len(uvs)),
                    "The OBJ is inconsistent; re-export it.")
            if ni is not None and not (0 <= ni < len(nrm)):
                raise ImportRefused(
                    "a face refers to normal %d but the file has %d."
                    % (ni + 1, len(nrm)),
                    "The OBJ is inconsistent; re-export it.")

    return dict(pos=pos, uvs=uvs, normals=nrm, tris=tris,
                quads=quads, ngons=ngon)


# ---------------------------------------------------------------- geometry aid

def _face_normal(p0, p1, p2):
    ux, uy, uz = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
    vx, vy, vz = p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2]
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    ln = (nx * nx + ny * ny + nz * nz) ** 0.5
    if ln < 1e-12:
        return (0.0, 1.0, 0.0)
    return (nx / ln, ny / ln, nz / ln)


def bounds(obj):
    vs = obj["pos"]
    return ([min(v[i] for v in vs) for i in range(3)],
            [max(v[i] for v in vs) for i in range(3)])


# ---------------------------------------------------------------- writing .mod

def _n(x):
    """A number the way the disc writes them: six decimals, plain."""
    return "%.6f" % (x + 0.0)


def build(obj, material="imported", texture="westgun"):
    """An OBJ-shaped dict into `version: 1.10` bytes.

    Emits ONE material and ONE packet of `tri` records. The three invariants
    rdr-3d measured as exact on all 251 shipped files hold here BY
    CONSTRUCTION, and `verify()` re-reads the result to prove it rather than
    assert it:

        v lines            == header `verts`
        packet field 1     == that packet's `adj` lines
        header `primitives`== triangles produced
    """
    pos = obj["pos"]
    uvs = obj["uvs"]
    nrm = obj["normals"]
    tris = obj["tris"]

    supplied_normals = bool(nrm)
    supplied_uvs = bool(uvs)

    # One adj per distinct corner. Distinct corners -- not distinct positions
    # -- because two corners sharing a position but differing in normal or UV
    # are a hard edge or a seam and must stay separate. That is the same
    # distinction that cost the READER a wrong triangle count in vertex space.
    adj_index = {}
    adj_rows = []
    out_normals = list(nrm)
    out_uvs = list(uvs)

    def corner(c, fallback_normal):
        vi, ti, ni = c
        if ni is None:
            # No normal in the OBJ. Compute the face normal rather than write
            # a zero -- but the plan SAYS it was computed, because a model
            # lit by (0,0,0) normals is black and nothing would say why.
            out_normals.append(fallback_normal)
            ni = len(out_normals) - 1
        if ti is None:
            if not out_uvs:
                out_uvs.append((0.0, 0.0))
            ti = 0
        key = (vi, ni, ti)
        got = adj_index.get(key)
        if got is None:
            got = len(adj_rows)
            adj_index[key] = got
            adj_rows.append((vi, ni, 0, ti, -1, 0))
        return got

    tri_rows = []
    for a, b, c in tris:
        fn = _face_normal(pos[a[0]], pos[b[0]], pos[c[0]])
        tri_rows.append((corner(a, fn), corner(b, fn), corner(c, fn)))

    L = []
    ap = L.append
    ap("version: 1.10")
    ap("verts: %d" % len(pos))
    ap("normals: %d" % len(out_normals))
    ap("colors: %d" % _FLOOR)
    ap("tex1s: %d" % len(out_uvs))
    ap("tex2s: 0")
    ap("tangents: 0")
    ap("materials: 1")
    ap("adjuncts: %d" % len(adj_rows))
    ap("primitives: %d" % len(tri_rows))
    ap("matrices: %d" % _FLOOR)
    ap("reskins: %d" % _FLOOR)
    ap("")
    for v in pos:
        ap("v\t%s\t%s\t%s" % (_n(v[0]), _n(v[1]), _n(v[2])))
    ap("")
    for v in out_normals:
        ap("n\t%s\t%s\t%s" % (_n(v[0]), _n(v[1]), _n(v[2])))
    ap("")
    ap("c\t1.000000\t1.000000\t1.000000\t1.000000")
    ap("")
    for t in out_uvs:
        ap("t1\t%s\t%s" % (_n(t[0]), _n(t[1])))
    ap("")
    ap("mtl %s {" % material)
    ap("\tpackets:\t1")
    ap("\tprimitives:\t%d" % len(tri_rows))
    ap("\ttextures:\t1")
    ap("\tillum: diffuse")
    ap("\tambient:\t1.000000 1.000000 1.000000")
    ap("\tdiffuse:\t1.000000 1.000000 1.000000")
    ap("\tspecular:\t0.500000 0.500000 0.500000")
    ap("\ttexture: 0 \"%s\"" % texture)
    ap("\tattributes:\t0")
    ap("}")
    ap("")
    # Field 1 is the adj count -- the one packet field that is exact on all
    # 3,786 shipped packets, and the evidence that indices are packet-local.
    # Field 2 is the primitive-record count, field 3 the texture count.
    ap("packet %d %d 1 {" % (len(adj_rows), len(tri_rows)))
    for r in adj_rows:
        ap("\tadj %5d %5d %5d %5d %5d %5d" % r)
    for t in tri_rows:
        ap("\ttri %5d %5d %5d" % t)
    ap("\tmtx 0 ")
    ap("}")
    ap("")
    ap("mtxv %d" % len(pos))
    ap("mtxn %d" % len(out_normals))
    ap("")

    data = ("\n".join(L)).encode("latin-1")
    return data, dict(verts=len(pos), normals=len(out_normals),
                      uvs=len(out_uvs), adjuncts=len(adj_rows),
                      triangles=len(tri_rows),
                      computed_normals=not supplied_normals,
                      default_uvs=not supplied_uvs)


def verify(data, stats):
    """Re-read what we just wrote, with rdr-3d's reader, and prove it agrees.

    This is the whole point of the module having a writer and not just an
    encoder: the only claim worth making about a generated model is that an
    INDEPENDENT reader gets the same geometry back out of it. Asserting our
    own intent proves nothing.
    """
    doc = modtext.parse(data)          # raises NotTextMod if the magic is off
    got = doc["counted"]
    hdr = doc["header"]
    problems = []
    if got["verts"] != stats["verts"]:
        problems.append("wrote %d vertices, reader sees %d"
                        % (stats["verts"], got["verts"]))
    if hdr.get("verts") != got["verts"]:
        problems.append("header declares %s vertices, file carries %d"
                        % (hdr.get("verts"), got["verts"]))
    if got["triangles"] != stats["triangles"]:
        problems.append("wrote %d triangles, reader resolves %d"
                        % (stats["triangles"], got["triangles"]))
    if hdr.get("primitives") != got["triangles"]:
        problems.append("header declares %s primitives, reader resolves %d"
                        % (hdr.get("primitives"), got["triangles"]))
    if hdr.get("adjuncts") != got["adjuncts"]:
        problems.append("header declares %s adjuncts, file carries %d"
                        % (hdr.get("adjuncts"), got["adjuncts"]))
    nv = len(doc["verts"])
    for mat in doc["materials"]:
        for p in mat["packets"]:
            if p["stated_adj"] != len(p["adj"]):
                problems.append("packet field 1 says %d adjuncts, packet has "
                                "%d" % (p["stated_adj"], len(p["adj"])))
            for t in p["tris"]:
                if not all(0 <= i < nv for i in t):
                    problems.append("a triangle points outside the vertex "
                                    "table: %s of %d" % (t, nv))
                    break
    return problems


# ------------------------------------------------------------------- the plan

def stored_size(data, compressed=True):
    """Bytes this will actually occupy. Deflate, because length is not size.

    All 3,008 `.mod` entries on the disc are compressed, so costing an import
    by `len(data)` would be wrong in both directions -- it would refuse
    imports that fit and accept ones that do not.
    """
    if not compressed:
        return len(data)
    co = zlib.compressobj(9, zlib.DEFLATED, -15)
    return min(len(co.compress(data) + co.flush()), len(data))


def sharers(guns_data, model):
    """Which weapons name this model. The M9 question, asked before writing."""
    from . import guns as gunsmod
    stem = model[:-4] if model.lower().endswith(".mod") else model
    out = []
    for w in gunsmod.parse(guns_data):
        m = (w.fields.get("ModelName", {}) or {}).get("text")
        if m and m.strip().strip('"').lower() == stem.lower():
            out.append(w.name)
    return out


def plan(game, obj_text, target, archive=WEAPON_ARCHIVE,
         material="imported", texture="westgun", guns_file="swat.guns",
         allow_shared=False):
    """Cost and check a weapon-model import. Writes NOTHING.

    Returns `(plan, problem, data)`. `problem` is None when the import may
    proceed; `data` is the exact bytes that were COSTED, so the caller writes
    what was measured rather than rebuilding and trusting the two to agree.
    otherwise it is an api-shaped refusal and `plan` still carries the numbers,
    because "it does not fit" with no figure is not an answer a user can act
    on.

    The plan is shaped for `renderPlan`/`openSheet`: `writes[]` rows plus a
    `fits` flag, since the front end disables its own Go button on
    `plan.fits === false` and renders nothing at all when a route answers with
    no plan.
    """
    dave = game.archive(archive)

    obj = from_obj(obj_text)                       # raises ImportRefused
    data, stats = build(obj, material=material, texture=texture)

    problems = verify(data, stats)
    if problems:
        # Our own output did not survive an independent re-read. This should
        # be unreachable; if it fires, the writer is wrong and the LAST thing
        # to do is write the file anyway.
        raise ImportRefused(
            "the model this built did not read back correctly, so it was not "
            "written.", "This is a bug in the importer, not in your file. "
            "Please report it with the .obj attached.",
            code="server_fault", detail="; ".join(problems))

    stem = target[:-4] if target.lower().endswith(".mod") else target
    fname = stem + ".mod"

    try:
        entry = dave.find(fname)
    except Exception:
        entry = None

    lo, hi = bounds(obj)
    dims = [round(hi[i] - lo[i], 4) for i in range(3)]
    stored = stored_size(data, compressed=True if entry is None
                         else entry.compressed)

    who = []
    try:
        who = sharers(dave.read(dave.find(guns_file)), stem)
    except Exception:
        who = []

    p = dict(
        target=fname, archive=archive, material=material, texture=texture,
        verts=stats["verts"], triangles=stats["triangles"],
        adjuncts=stats["adjuncts"], normals=stats["normals"],
        computed_normals=stats["computed_normals"],
        default_uvs=stats["default_uvs"],
        quads_triangulated=obj["quads"], ngons_triangulated=obj["ngons"],
        size_m=dims, weapons_affected=who,
        bytes_raw=len(data), stored=stored,
        undo=dict(steps=1),
        writes=[],
    )

    if entry is None:
        # The safe path -- a NEW .mod plus a repoint -- needs the repoint, and
        # the repoint is blocked. Say exactly that, with the room figure, so
        # the answer is not mistaken for "the importer is broken".
        room = dave.largest_gap()[1]
        p["new_file"] = True
        p["room_for_new_file"] = room
        p["fits"] = stored <= room
        p["writes"] = [dict(archive=archive, name=fname, bytes_before=0,
                            bytes_after=len(data), stored=stored,
                            slot=room, headroom=room - stored)]
        return p, dict(
            code="not_editable",
            message="a new model can be stored, but nothing would point at "
                    "it yet: no weapon's ModelName can be changed in this "
                    "build, so the game would never load it.",
            fix="Import over one of the models a weapon already uses. "
                "Pointing a weapon at a brand-new model is the safe way and "
                "it is being decided on now.",
            detail="ModelName, SecondaryModelName, HolsterModelName "
                   "and AlternateTextureName are all non-editable. Room for a "
                   "new file here is %d bytes; this model needs %d."
                   % (room, stored)), data

    headroom = entry.slot - stored
    fits = stored <= entry.slot
    p["new_file"] = False
    p["fits"] = fits
    p["compressed"] = entry.compressed
    p["writes"] = [dict(archive=archive, name=fname,
                        bytes_before=entry.size_full, bytes_after=len(data),
                        stored=stored, slot=entry.slot, headroom=headroom)]

    p["shared_by"] = list(who)
    p["shared_acknowledged"] = bool(allow_shared and len(who) > 1)
    if len(who) > 1 and not allow_shared:
        # M9's defect in another costume. Refused BEFORE the slot question,
        # because a write that fits is still the wrong write here.
        #
        # `allow_shared=True` is the user saying "yes, re-model all of them":
        # 9 of the 14 weapon models are shared, so without it only Sacred_Bow
        # (the one unshared model) could ever be imported over. The plan
        # carries `shared_by`, so the caller can show exactly who changes.
        return p, dict(
            code="not_editable",
            message="%s is used by %d weapons, not one: %s. Importing over it "
                    "would change all %d of them."
                    % (fname, len(who), ", ".join(who), len(who)),
            fix="Send allow_shared to go ahead (the Studio panel does, and shows "
                "an amber warning naming who changes), or make a New weapon "
                "with its own model so only it changes.",
            detail="55 weapons share 14 models on this disc."), data

    if not fits:
        return p, dict(
            code="slot_overflow",
            message="this model needs %d bytes packed and %s has %d -- %d "
                    "over." % (stored, fname, entry.slot, stored - entry.slot),
            fix="Reduce the model: it has %d triangles and %d vertices. The "
                "one already there packs to %d bytes. Note the file is "
                "compressed, so the packed size is what counts, not the "
                "number of triangles on its own."
                % (stats["triangles"], stats["verts"],
                   stored_size(dave.read(entry), entry.compressed)),
            detail=""), data

    return p, None, data
