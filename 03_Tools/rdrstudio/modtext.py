"""Reading the plain-text `.mod` dialect -- `version: 1.10`.

M15's first half. a tester asked for a `.mod` importer for weapon models, and
`.mod` was "8,600 files nothing reads". It turns out not to be one format:
of the 3,008 loose `.mod` in SWAT.DAT, **2,635 are binary `2.12`** and a few
hundred are plain ASCII. This module reads the ASCII dialect, which needs no
disassembler. rdr-re has the binary `2.12` layout as a parallel track.

**It covers 31 of the 55 weapons** and, more to the point, the three most
widely shared models -- `ptl_lod_01` (11 weapons), `rfl_lod_01` (11) and
`pt2_lod_01` (6).

## The format

A header of eleven named counts, then records, then materials containing
packets:

    version: 1.10
    verts: 140        normals: 140      colors: 1
    tex1s: 97         tex2s: 0          tangents: 0
    materials: 1      adjuncts: 160     primitives: 238
    matrices: 1       reskins: 1

    v  <x> <y> <z>            n  <x> <y> <z>
    t1 <u> <v>                c  <r> <g> <b> <a>

    mtl <name> {
        packets: 7
        packet <A> <B> <C> {
            adj <vert> <normal> <colour> <tex1> <tex2> <tangent>
            str <n> <i0> <i1> ...
            stp <n> <i0> <i1> ...
            mtx <n>
        }
    }

An `adj` is one vertex's bundle of attribute indices -- the same idea as an
OBJ `v/vt/vn` triple. `str` and `stp` are triangle strips whose indices point
at `adj` entries, not at vertices directly.

## What is verified, and what is NOT

Measured across all 251 plain-text files in `SWAT.DAT` (3,786 packets):

    v lines == header verts                    251 / 251    exact
    packet field 1 == that packet's adj lines 3786 / 3786   exact
    packet field 4 == that packet's reskins   2244 / 2244   exact
    no triangle references a vertex out of range             251 / 251

**A packet header carries THREE or FOUR numbers and nothing announces which.**
`packet 36 10 1 {` and `packet 38 9 5 10 {` both occur — 1,542 of the former,
2,244 of the latter. A reader that demands three silently skips every 4-number
packet, and the file comes back with no geometry at all rather than with an
error: `akabag_lod_02.mod` parsed to zero triangles that way. The flexible
form also brings the packet count to 3,786, which is the figure rdr-re
independently reported, so the two readings now agree.

**Field 1 is what makes the parse safe.** Exact on every packet, and it is
what establishes that each packet carries its OWN adjunct table — so a run's
indices are PACKET-LOCAL, not file-global. Both readings put every index in
range (local is a subset of global), so range alone cannot separate them; the
per-packet count is the evidence, and getting it backwards would silently
build triangles from another packet's vertices.

**Fields 2 and 3 are not identified and this reader does not use them:**

    field 2 == str+stp lines    3630 / 3786   (96%, not a law)
    field 3 == header matrices   406 / 3786
    field 3 == mtx lines         721 / 3786

So record counts come from the records; field 1 is the cross-check.

## The triangle count reconciles: 249 of 251

    header primitives == triangles produced   249 / 251

The two exceptions are `crap.mod` and `shitgood.mod` — dev leftovers by name,
near-identical to each other (both declare 2,019; 131 `str` each, 853 and 848
`stp`), each two to twelve triangles short with no degenerate run and no short
run to explain it. Every model a weapon actually names is exact.

Getting there needed three corrections, each of which had failed SILENTLY:

1. **A packet header has three OR four numbers.** A regex demanding three
   skipped 2,244 of 3,786 packets, and those files returned no geometry and no
   error — `akabag_lod_02.mod` read as zero triangles and looked fine.
2. **`tri` is a third primitive type**, alongside `str` and `stp` — a
   standalone triangle, 15,157 of them across 152 of the 251 files.
   `edge_arrow.mod` is built entirely of them, so a strips-only reader
   returned an empty mesh for it.
3. **Degeneracy is judged on ADJUNCT indices, and only inside a run.** Two
   different adjuncts may share a vertex position while differing in normal or
   UV — a hard edge or a UV seam — and those triangles are real. Judging in
   vertex space threw them away: `ag2_lod_01` came out at 201 against a
   declared 1,848. A repeated adjunct inside a `str`/`stp` run IS a stitch and
   is not counted; an explicitly written `tri` is counted even when
   degenerate, which is why `edge_hat_sn2_lod_01.mod` declares 117 and writes
   117 with one degenerate among them.

`header adjuncts` remains unexplained and nothing here depends on it: it
matches total adj lines on 19 files, distinct on 12, both on 146 (no
duplicates) and neither on 74, and on `ag2_lod_01` it declares FEWER than the
file contains. `parse` reports the header's figures and the counted ones under
separate keys rather than reconciling them by guesswork.
"""

import re

MAGIC = b"version: 1.10"
HEADER_FIELDS = ("verts", "normals", "colors", "tex1s", "tex2s", "tangents",
                 "materials", "adjuncts", "primitives", "matrices", "reskins")

# A packet header carries THREE or FOUR numbers, and which one you get
# is not stated anywhere. Demanding three silently skips the 4-number
# form -- 2,244 of the 3,786 packets on the disc, whose records then
# vanish and whose models come out empty.
_PACKET = re.compile(r"packet((?:\s+\d+)+)\s*\{")
_MTL = re.compile(r"mtl\s+(\S+)\s*\{")


class NotTextMod(Exception):
    pass


def _f(tok):
    """A float from the file, tolerating the one non-standard form on the disc.

    Almost every number is a plain `-0.010830`. `edge_bottle_bar.mod` writes
    its normals with a C float suffix -- `1.0f` -- 78 times. That is the ONLY
    deviation: a scan of every numeric token in every `v`/`n`/`t1`/`t2`/`c`
    line across all 251 text files turns up no other form. Stripping the
    suffix is therefore a narrow allowance for a known file rather than a
    general leniency, which is why this does not simply swallow junk with a
    try/except and carry on.
    """
    return float(tok[:-1] if tok[-1:] in ("f", "F") else tok)


def is_text_mod(data):
    """Is this the plain-text dialect? Cheap enough for a file listing."""
    return data[:len(MAGIC)] == MAGIC


def parse(data):
    """Read a text `.mod`. -> dict.

    Geometry comes back as `verts` / `normals` / `tex1` / `colors` plus
    `materials`, each material holding packets of triangles already resolved
    through the adjunct table into vertex indices.
    """
    if not is_text_mod(data):
        raise NotTextMod("not a `version: 1.10` .mod")

    header = {}
    verts, normals, tex1, tex2, colors = [], [], [], [], []
    materials = []
    mtl = None
    packet = None

    for line in data.decode("latin-1").splitlines():
        s = line.strip()
        if not s:
            continue

        if ":" in s:
            key = s.split(":", 1)[0].strip()
            if key in HEADER_FIELDS:
                # FIRST occurrence only. `primitives:` and `textures:` appear
                # again INSIDE each `mtl` block with that material's own
                # figures, and letting those overwrite the file header makes
                # a single-material model look like it declares its last
                # material's counts -- `book_lod_01` reads 20 instead of 108.
                header.setdefault(key, int(s.split(":", 1)[1]))
                continue
            if key == "packets" and mtl is not None:
                mtl["packets_declared"] = int(s.split(":", 1)[1])
                continue
            if key == "version":
                continue

        m = _MTL.match(s)
        if m:
            mtl = dict(name=m.group(1), packets=[], packets_declared=None)
            materials.append(mtl)
            continue

        m = _PACKET.match(s)
        if m:
            fields = [int(x) for x in m.group(1).split()]
            packet = dict(fields=fields,
                          stated_adj=fields[0],
                          stated_reskins=fields[3] if len(fields) > 3 else None,
                          adj=[], runs=[], matrices=[], reskins=[])
            if mtl is None:                      # a packet outside any mtl
                mtl = dict(name=None, packets=[], packets_declared=None)
                materials.append(mtl)
            mtl["packets"].append(packet)
            continue

        if s == "}":
            packet = None
            continue

        w = s.split()
        tag = w[0]
        if tag == "v":
            verts.append(tuple(_f(x) for x in w[1:4]))
        elif tag == "n":
            normals.append(tuple(_f(x) for x in w[1:4]))
        elif tag == "t1":
            tex1.append(tuple(_f(x) for x in w[1:3]))
        elif tag == "t2":
            tex2.append(tuple(_f(x) for x in w[1:3]))
        elif tag == "c":
            colors.append(tuple(_f(x) for x in w[1:5]))
        elif packet is None:
            continue
        elif tag == "adj":
            packet["adj"].append(tuple(int(x) for x in w[1:]))
        elif tag in ("str", "stp"):
            packet["runs"].append((tag, [int(x) for x in w[2:]]))
        elif tag == "tri":
            # A standalone triangle: three adjunct indices, no strip walk and
            # no winding alternation. NOT a rare form -- 15,157 of them across
            # 152 of the 251 files, and `edge_arrow.mod` is built entirely of
            # them with no str/stp at all, so a reader that only knows strips
            # returns an empty mesh for it and says nothing.
            packet["runs"].append((tag, [int(x) for x in w[1:4]]))
        elif tag == "mtx":
            packet["matrices"].append(int(w[1]))
        elif tag == "reskin":
            # `reskin <adj> <n> <weight> <x> <y> <z>` -- a re-skinned vertex.
            # Not geometry on its own; kept so a caller can see them and so
            # the packet's 4th field can be checked against them.
            packet["reskins"].append(s)

    for mat in materials:
        for p in mat["packets"]:
            p["tris"] = _triangles(p)

    return dict(
        version="1.10",
        header=header,
        verts=verts, normals=normals, tex1=tex1, tex2=tex2, colors=colors,
        materials=materials,
        # What the header CLAIMS beside what was actually read. They disagree
        # on `adjuncts` for 74 of 251 files, so they are reported separately
        # rather than reconciled -- see the module docstring.
        counted=dict(verts=len(verts), normals=len(normals),
                     tex1s=len(tex1), tex2s=len(tex2), colors=len(colors),
                     materials=len(materials),
                     adjuncts=sum(len(p["adj"])
                                  for m in materials for p in m["packets"]),
                     packets=sum(len(m["packets"]) for m in materials),
                     triangles=sum(len(p["tris"])
                                   for m in materials for p in m["packets"])),
    )


def _triangles(packet):
    """Resolve a packet's strips into (v0, v1, v2) vertex-index triples.

    Indices in a run address this PACKET's adjunct table -- see the module
    docstring for why that is the settled reading and not a choice. An adj's
    first field is the vertex index.

    Standard strip winding: every other triangle is reversed, so the surface
    keeps one facing. Degenerate triangles (a repeated index, which is how a
    strip stitches to the next one) are dropped rather than emitted as
    zero-area faces.
    """
    out = []
    adj = packet["adj"]
    for kind, idx in packet["runs"]:
        if kind == "tri":
            spans = [(0, idx[0], idx[1], idx[2])] if len(idx) >= 3 else []
        else:
            spans = [(i, idx[i], idx[i + 1], idx[i + 2])
                     for i in range(len(idx) - 2)]
        for i, a, b, c in spans:
            if kind != "tri" and i % 2:
                a, c = c, a
            if not (0 <= a < len(adj) and 0 <= b < len(adj)
                    and 0 <= c < len(adj)):
                continue
            # Degeneracy is judged on the ADJUNCT indices, not on the vertex
            # indices they resolve to. A strip stitches to the next one by
            # repeating an adjunct, and those are the triangles the format
            # does not count. But two DIFFERENT adjuncts may legitimately
            # share a vertex position while differing in normal or UV -- a
            # hard edge or a UV seam -- and those triangles are real.
            #
            # Judging in vertex space threw them away: `ag2_lod_01` came out
            # at 201 triangles against a declared 1,848, and
            # `akabag_lod_02` at zero. In adjunct space both are exact.
            # A strip STITCH -- a repeated adjunct inside a run -- is not a
            # primitive and the format does not count it. An explicitly
            # written `tri` is, even when its three indices are not distinct:
            # `edge_hat_sn2_lod_01.mod` declares 117 and writes 117, one of
            # which is degenerate. Dropping it gave 116 and looked like an
            # off-by-one in the strip walk, which is the wrong place to look.
            if kind != "tri" and len({a, b, c}) != 3:
                continue
            out.append((adj[a][0], adj[b][0], adj[c][0]))
    return out


def mesh(data):
    """One merged mesh for a viewer: verts plus every triangle in the file."""
    m = parse(data)
    tris = [t for mat in m["materials"] for p in mat["packets"]
            for t in p["tris"]]
    return dict(verts=m["verts"], tris=tris,
                normals=m["normals"], uvs=m["tex1"],
                materials=[mat["name"] for mat in m["materials"]],
                header=m["header"], counted=m["counted"])


def bounds(m):
    """Axis-aligned box of a parsed mesh, or None when it has no vertices."""
    vs = m["verts"]
    if not vs:
        return None
    return ([min(v[i] for v in vs) for i in range(3)],
            [max(v[i] for v in vs) for i in range(3)])
