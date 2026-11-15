"""Reading a character model the way the GAME reads it: from its `.rsc`.

M8. a tester, verbatim: *".rsc viewer for Character models (not .mod files)"*,
and later in the same notes, *"a .mod importer for characters is currently
pointless as character models are pulled specifically from .rsc and not their
.mod files."*

**The loose `.mod` files are not the model.** Every character part in
`SWAT.DAT` also exists as a loose `<part>.mod` -- `aka_lod_01.mod` beside the
`aka_lod_01` chunk inside `aka_lods.rsc` -- and they are not the same data.
Compared over the first 60 bundles: **111 of 111 pairs differ**, and not
marginally. `ag2_lod_01` is 88,272 bytes in the bundle and 228,155 loose;
`aka_lod_01` is 92,240 against 77,973. They disagree in both directions, so
this is different content rather than one being a wrapper around the other.
Viewing the loose file shows something the game never draws.

**What was missing was not the geometry -- it was the binding.** The character
route already loads the bundle and merges the right parts. But for textures it
sent `textures=len(table)`: a COUNT. Nothing told the viewer which TEX0 belongs
to which part, so a character arrives with UVs and no way to know what to put
on them.

The binding is in the bundle and always was, in the same place as a level's:
each part record in the container MOD0 carries a ONE-BASED chunk index into
the bundle's own TEX0 chunks (`verify_binding.py` established this for levels).
It holds for characters too, measured over every bundle on the disc:

    every MOD0 in all 310 bundles:  689 references, 645 -> TEX0,
                                    44 zero (untextured), 0 fail
    only the parts a viewer draws:  557 references, 520 -> TEX0,
                                    37 zero, 0 fail

(The two counts differ because a bundle holds more LODs than any one view
uses; `character_parts` picks one body LOD plus the accessories. Both are
measured, and neither has a failure.)

`verify_binding.py` only sweeps `LVLS_*`, so none of that was being checked.

**The reference this module is verified against is the file's own claim.** A
part record states its vertex and index counts before any packet is decoded,
so "did we read this model correctly" has an answer that does not depend on
our decoder agreeing with itself -- which is what `stated` versus `decoded`
below is for.
"""

from . import rsc as _rsc


class NotACharacter(Exception):
    pass


def bundle(dave, stem):
    """The character's `.rsc`. Raises rather than falling back to a `.mod`."""
    name = stem + "_lods.rsc"
    try:
        data = dave.read(name)
    except Exception:
        raise NotACharacter(
            "%s has no %s -- character models are read from the bundle, not "
            "from a loose .mod" % (stem, name))
    return _rsc.Rsc(data, name)


def part_names(dave, stem):
    """chunk index -> authored name, from the `.rnt` manifest. May be empty."""
    try:
        return _rsc.parse_rnt(dave.read(stem + "_lods.rnt"))
    except Exception:
        return {}


def textures(b):
    """Every TEX0 in the bundle, by chunk index -> info dict."""
    out = {}
    for idx, info, mip, pal in b.texture_table():
        d = dict(info or {})
        d["chunk"] = idx
        d["pixels"] = mip
        d["palette"] = pal
        out[idx] = d
    return out


def surfaces(b, picks, names=None):
    """Every drawable surface of a character, with the texture it binds.

    One entry per PART, not per chunk: a character's body LOD is a container
    of several parts and they do not share a texture -- the face and the body
    are separate bindings, which is the whole reason a per-chunk answer is
    not good enough for a viewer.

    Each entry carries what the FILE says (`stated_verts`, `resource`) beside
    what the decoder produced (`decoded_verts`), so a caller can tell a
    reading error from a decoding one.
    """
    names = names or {}
    tex = textures(b)
    out = []
    for chunk, _label in picks:
        recs = b.parts(chunk)
        if not recs:
            continue
        mesh = b.model(chunk)
        decoded = len(mesh["verts"]) if mesh else 0
        for i, rec in enumerate(recs):
            res = rec.get("resource", 0)
            t = tex.get(res - 1) if res else None
            out.append(dict(
                chunk=chunk,
                part=i,
                name=names.get(chunk) or _label or "chunk %d" % chunk,
                stated_verts=rec.get("verts", 0),
                stated_indices=rec.get("indices", 0),
                resource=res,
                textured=bool(res),
                texture=(dict(chunk=t["chunk"], name=t.get("name", ""),
                              width=t.get("width", 0),
                              height=t.get("height", 0),
                              pixels=t.get("pixels"),
                              palette=t.get("palette"))
                         if t else None),
                # Only meaningful once per chunk; repeated on each part of it
                # so a caller never has to go back to the chunk to find it.
                chunk_decoded_verts=decoded,
            ))
    return out


def open_character(dave, stem, lod=0, picker=None):
    """Open a character as the game does. -> dict for a viewer.

    `picker(bundle, dave, stem, lod) -> [(chunk, name)]` is
    `studio.character_parts`, passed in rather than imported so this module
    does not reach into rdr-app's file. Without one, every MOD0 is taken,
    which is right for a single-part prop and wrong for a character -- so a
    caller that has the real picker should always pass it.
    """
    b = bundle(dave, stem)
    names = part_names(dave, stem)
    if picker is not None:
        picks = picker(b, dave, stem, lod)
    else:
        picks = [(c.index, names.get(c.index))
                 for c in b.chunks if c.tag == "MOD0"]
    if not picks:
        raise NotACharacter("%s_lods.rsc holds no models" % stem)

    surf = surfaces(b, picks, names)
    return dict(
        stem=stem,
        source=stem + "_lods.rsc",
        parts=len(surf),
        chunks=[c for c, _n in picks],
        surfaces=surf,
        textures=len(textures(b)),
        textured=sum(1 for s in surf if s["textured"]),
        untextured=sum(1 for s in surf if not s["textured"]),
    )


def binding_faults(b, picks):
    """Part records whose texture reference does not resolve. -> list.

    Empty on every stock bundle (0 of 689 references fail disc-wide), so
    anything here is a real fault rather than a quirk of some character.
    """
    bad = []
    for chunk, _label in picks:
        for i, rec in enumerate(b.parts(chunk) or []):
            res = rec.get("resource", 0)
            if not res:
                continue
            j = res - 1
            tag = b.chunks[j].tag if 0 <= j < len(b.chunks) else "out of range"
            if tag != "TEX0":
                bad.append(dict(chunk=chunk, part=i, resource=res, points_at=tag))
    return bad
