"""Moving and scaling a SELECTION of level models as one thing.

M6. The single-model path already exists (`geomedit` plus the `geom_edit`
route); this is the part that is not just "the same call N times", and the
difference is the whole milestone.

**The pivot is shared.** `geomedit.scale` defaults `about` to the mesh's own
centre, and the single-model route keeps that default. Call it once per model
for a multi-selection and every model grows about its own middle: the group
keeps its layout while the models swell into each other. What a mass scale
means is one pivot for the whole selection -- each model keeps its own
proportions AND its offset from the pivot scales, so the arrangement grows
with its contents. Both behaviours are defensible; only one of them is what
"scale the selection" means, and picking the wrong one is invisible until you
look at the level.

**Proportional means one factor.** A uniform factor is conformal: every
model's own bounding box keeps its aspect ratio no matter where it sits or
how it is oriented. Per-axis factors are allowed here because the single-model
route allows them, but they are NOT proportional -- a model whose long axis is
not the axis being stretched comes out a different shape -- so `plan` labels
any per-axis call `proportional=False` and the caller can refuse it.

**The cull spheres move, always.** Each part's packet opens with a VIF unpack
to VU address 0x29 that the microprogram consumes at runtime. Moving verts and
leaving the spheres froze the game -- the 185 s A/B on 2026-08-12/13, recorded
in `geomedit.move_spheres`. Every transform here moves both, together, with no
option to skip; a mass edit is exactly where "I forgot one of the N" would
hide.

**All or nothing.** A selection is planned in full before any of it is
written. A half-applied mass move -- three of five buildings in the new place
-- is worse than a refusal, and it is not something a user can see or undo
their way out of reliably.

**What this does NOT move: collision.** `BND0` is a separate mesh. Moving the
art and leaving the shell is the documented failure in `geomedit` and it is
M16's milestone, not this one. So the plan REPORTS the area's bound chunks
rather than silently leaving them out, and a caller that wants solid buildings
has to deal with them. Silence here is the exact shape of the bug this lane
fears most.
"""

import math

from . import geomedit


class Refused(Exception):
    """The whole selection was refused, with a reason naming the model."""


def _uniform(sx, sy, sz):
    return abs(sx - sy) < 1e-9 and abs(sx - sz) < 1e-9


def select(vr, chunks):
    """Decode the selected chunks. -> [model dict], raising on any refusal.

    Every entry carries the chunk index, the decoded mesh, its raw bytes and
    its part records, so a caller never has to re-open the chunk to write it.
    """
    out = []
    for index in chunks:
        try:
            c = vr.chunks[index]
        except (IndexError, KeyError):
            raise Refused("there is no chunk %r in this area" % (index,))
        if c.tag != "MOD0":
            raise Refused("chunk %d is a %s, which is not geometry"
                          % (index, c.tag))
        mesh = vr.model(index)
        if not mesh:
            raise Refused("chunk %d will not decode" % index)
        if not geomedit.editable(mesh):
            raise Refused(
                "chunk %d has no recorded vertex offsets, so it cannot be "
                "moved in place" % index)
        if any(q is not None for q in (mesh.get("quant") or [])):
            # Reshaping one of these works (verify_skinwrite.py) and is a
            # different path: positions are 16-bit codes against the VIF ROW
            # register, so composing ops means re-quantising between steps
            # rather than reading floats back. M6 is level models; refusing
            # by name beats a silent wrong number.
            raise Refused(
                "chunk %d is skinned -- its positions are quantised against "
                "the VIF ROW register. Mass edit handles level geometry; "
                "use the character path for this one." % index)
        out.append(dict(chunk=index, mesh=mesh, raw=vr.raw(index),
                        parts=vr.parts(index)))
    if not out:
        raise Refused("nothing was selected")
    return out


def bounds(models):
    """The selection's combined axis-aligned box. -> (min3, max3) or None."""
    lo = [None, None, None]
    hi = [None, None, None]
    for m in models:
        b = geomedit.bounds(m["mesh"])
        if b is None:
            continue
        for i in range(3):
            lo[i] = b[0][i] if lo[i] is None else min(lo[i], b[0][i])
            hi[i] = b[1][i] if hi[i] is None else max(hi[i], b[1][i])
    if lo[0] is None:
        return None
    return (lo, hi)


def centre(models):
    """The pivot a mass edit turns about: the middle of the combined box.

    Deliberately the box centre and not the mean vertex. A vertex mean is
    pulled towards whichever model happens to be most finely tessellated, so
    selecting a detailed lamp and a coarse wall would scale about a point
    inside the lamp. The box centre is what the user sees selected.
    """
    b = bounds(models)
    if b is None:
        return (0.0, 0.0, 0.0)
    return tuple((b[0][i] + b[1][i]) / 2.0 for i in range(3))


def _reread(raw, mesh):
    """The mesh as the bytes now describe it, so ops compose.

    The offsets cannot have moved -- an in-place float patch changes no
    structure -- so this reads through them rather than re-decoding the
    packet. Float storage only, which `select` has already guaranteed.
    """
    import struct
    return dict(mesh, verts=[list(struct.unpack_from("<3f", raw, o))
                             for o in mesh["voff"]])


def plan(vr, chunks, ops, about=None):
    """Plan a mass edit. -> dict with per-chunk bytes and a report.

    `ops` is the same list the single-model route takes:
    `[{"op": "translate", "x":, "y":, "z":}, {"op": "scale", "x":, ...},
      {"op": "rotate_y", "deg":}]`.

    Nothing is written. The caller gets `chunks` as `{index: new_bytes}` and
    writes them in one transaction, which is what keeps a mass edit atomic.
    """
    models = select(vr, chunks)
    pivot = tuple(float(v) for v in about) if about else centre(models)
    before = {m["chunk"]: geomedit.bounds(m["mesh"]) for m in models}

    proportional = True
    for step in ops or []:
        if step.get("op") == "scale":
            sx = float(step.get("x", 1))
            sy = float(step.get("y", step.get("x", 1)))
            sz = float(step.get("z", step.get("x", 1)))
            if not _uniform(sx, sy, sz):
                proportional = False

    written = {}
    for m in models:
        out = m["raw"]
        work = m["mesh"]
        for step in ops or []:
            kind = step.get("op")
            if kind == "translate":
                dx = float(step.get("x", 0))
                dy = float(step.get("y", 0))
                dz = float(step.get("z", 0))
                fn = (lambda x, y, z, dx=dx, dy=dy, dz=dz:
                      (x + dx, y + dy, z + dz))
            elif kind == "scale":
                sx = float(step.get("x", 1))
                sy = float(step.get("y", step.get("x", 1)))
                sz = float(step.get("z", step.get("x", 1)))
                ax, ay, az = pivot
                fn = (lambda x, y, z, sx=sx, sy=sy, sz=sz,
                      ax=ax, ay=ay, az=az:
                      (ax + (x - ax) * sx,
                       ay + (y - ay) * sy,
                       az + (z - az) * sz))
            elif kind == "rotate_y":
                rad = math.radians(float(step.get("deg", 0)))
                ca, sa = math.cos(rad), math.sin(rad)
                ax, ay, az = pivot
                fn = (lambda x, y, z, ca=ca, sa=sa, ax=ax, az=az:
                      (ax + (x - ax) * ca + (z - az) * sa,
                       y,
                       az - (x - ax) * sa + (z - az) * ca))
            else:
                raise Refused("unknown op %r" % (kind,))
            out = geomedit.transform(out, work, fn)
            out = geomedit.move_spheres(out, m["parts"], fn)
            # The next op reads what this one wrote, so a translate after a
            # scale means what it says.
            work = _reread(out, work)
        written[m["chunk"]] = out

    after = {}
    for m in models:
        after[m["chunk"]] = geomedit.bounds(_reread(written[m["chunk"]],
                                                    m["mesh"]))

    lengths_held = all(len(written[m["chunk"]]) == len(m["raw"])
                       for m in models)
    bnd = [c.index for c in vr.chunks if c.tag == "BND0"]
    return dict(
        chunks=written,
        pivot=pivot,
        proportional=proportional,
        models=len(models),
        before=before,
        after=after,
        lengths_held=lengths_held,
        collision_untouched=bnd,
        collision_note=(
            "%d BND0 chunk(s) in this area were NOT moved -- collision is a "
            "separate mesh and moving it is M16. The art will be in the new "
            "place and the shell will not." % len(bnd)) if bnd else None,
    )


def proportions(box):
    """A box's aspect ratios, normalised on its longest axis. -> (a, b, c).

    This is the number a proportional scale must not change. Comparing raw
    sizes would only prove the factor was applied; comparing ratios proves
    the SHAPE survived, which is what the milestone asks for.
    """
    if box is None:
        return None
    size = [box[1][i] - box[0][i] for i in range(3)]
    longest = max(size)
    if longest <= 0:
        return None
    return tuple(s / longest for s in size)


def splice(blob, vr, planned):
    """Put every planned chunk back into the bundle. -> new bundle bytes.

    ONE blob, so the caller makes ONE write and the journal gets ONE undo
    entry. Writing the chunks one at a time would work byte-for-byte and
    would still be wrong: a mass move that fails on the fourth of five leaves
    the level in a state no single undo restores.

    Chunk sizes never change -- every transform here is an in-place float
    patch -- so the bundle's header offsets stay valid and the file goes back
    into the archive slot it came out of. That is asserted rather than
    assumed: a chunk whose length moved is a bug in this module, and it must
    not reach the disc.
    """
    out = bytearray(blob)
    for index, data in planned["chunks"].items():
        c = vr.chunks[index]
        if len(data) != c.size:
            raise Refused(
                "chunk %d came back %d bytes from %d -- an in-place patch "
                "cannot change a chunk's length, so this is a bug and the "
                "write is refused" % (index, len(data), c.size))
        out[c.offset:c.offset + c.size] = data
    return bytes(out)
