"""A whole level, assembled from its archive.

Reads the .lvl manifest, pulls in every bounds/zone/box/line file it names,
plus the spawn and enemy files, and presents one flat list of placed objects
with 3D positions. Edits are batched per source file and written back through
the in-place patcher.
"""

import re
import threading

from . import scene
from .dave import SlotOverflow
from .rsc import Rsc, area_stem, find_level_areas, find_rsc_names

# Section name in the .lvl manifest -> the extension it lists.
_SECTIONS = ("Bounds", "Zones", "Props", "Lines", "Boxes")


class Level:
    def __init__(self, game, archive, manifest=None, area=None):
        """A level from an archive.

        `manifest` picks a specific .lvl file instead of the archive's own --
        that is how the cut/dev levels living inside SWAT.DAT (dated backups,
        `-borked` versions, demo-era manifests) become loadable.

        `area` picks one of several sibling world bundles in the same
        archive, e.g. "lvl_gunshop.rsc" inside TOMB4. Without it the best
        name match wins, which is the area the mission actually starts in.
        """
        self.game = game
        self.archive = archive
        self.manifest = manifest
        self.area = area
        self.dave = game.archive(archive)
        self.name = (manifest.rsplit(".", 1)[0] if manifest
                     else archive.rsplit("/", 1)[-1].rsplit(".", 1)[0])
        self.sections = {}     # parsed .lvl manifest: section -> [filenames]
        self.lvl_file = None
        self.nodes = []
        self.sources = {}      # filename -> original bytes
        self.errors = []
        self._rsc = False      # False = not looked at yet, None = none found
        self._rsc_lock = threading.Lock()
        self.rsc_name = None
        self._load()

    # ------------------------------------------------------------------ load

    def _entry_names(self, ext):
        return [e.name for e in self.dave if e.ext == ext]

    def _read(self, filename):
        if filename not in self.sources:
            self.sources[filename] = self.dave.read(filename)
        return self.sources[filename]

    def areas(self):
        """Sibling world bundles in this archive, best match for it first.

        Most archives hold one; the Brimstone archives hold seven.
        """
        return find_level_areas(self.dave, self.name)

    _TILE_EXTS = ("znes", "bnds", "bxes", "lnes")

    def _tile_files(self):
        """Files owned by TILES, which no `.lvl` manifest ever names.

        Some levels are not authored as a fixed world. `lvl_traingood.til`
        lists 22 tiles and then a `Map` block reading `sets 3 / size 8 /
        Random 2` -- the track is assembled at runtime from randomly chosen
        tiles, so there is no single layout for a manifest to describe. The
        manifest names 19 of the archive's 41 `.znes` files; the other 22
        belong to tiles and were loaded by nothing.

        The effect in the editor was that 179 of TRAINGOO's 267 zone nodes
        simply did not exist, and the ones that did are train cars whose
        stored transform really is the origin (they are placed along a rail
        spline at runtime) -- so the level opened as a small heap of parts at
        0,0 and looked broken rather than incomplete.

        Loading them does NOT reconstruct the track. Every tile's geometry is
        authored in its own local space, so they will stack until something
        lays them out; that is a separate job and is honest about which
        problem it solves. What this fixes is that the content was invisible.

        Same shape in TRAIN, MSN and MSNCYN, which also ship a `.til`.
        """
        names = {e.name for e in self.dave}
        stems = []
        seq = []
        for til in self._entry_names("til"):
            # A per-tile `.til` is `NumAttachedZones { ... }` -- a leaf, not a
            # list of tiles. Only the level-wide one has a `Tiles { }` block,
            # and taking its lines is what enumerates the set.
            try:
                text = self._read(til).decode("latin-1", "replace")
            except Exception:
                continue
            if "Tiles" not in text:
                continue
            block = text.split("Tiles", 1)[1]
            block = block.split("{", 1)[1].split("}", 1)[0] if "{" in block else ""
            for line in block.splitlines():
                s = line.strip()
                if s and not s.startswith(("{", "}")):
                    stems.append(s.lower())
            # The AUTHORED slot sequence. The same file's Map block lists the
            # order the game lays the tiles down in -- sets*size entries, with
            # repeats; `Random R` marks R slots per set as runtime-random.
            # This was here all along and the layout ignored it.
            if "Map" in text:
                mb = text.split("Map", 1)[1]
                mb = mb.split("{", 1)[1].split("}", 1)[0] if "{" in mb else ""
                for line in mb.splitlines():
                    t = line.strip()
                    if t and not re.match(r"(sets|size|Random)", t):
                        seq.append(t.split()[0].lower())

        out = []
        for stem in stems:
            for ext in self._TILE_EXTS:
                n = "%s.%s" % (stem, ext)
                if n in names:
                    out.append(n)
        self._tile_stems = stems
        self._tile_seq = seq
        return out

    _SOCKET = re.compile(r"node\s+(\S+)\s*\{", re.S)
    _VERTS = re.compile(r"Vertices\s+\d+\s*\{([^}]*)\}", re.S)

    def _tile_layout(self):
        """The authored arrangement of the tiles, chained by their own sockets.

        ORDER comes from the `.til` Map block -- the game's own slot sequence,
        authored, with repeats. MSN and MSNCYN state it completely (Random 0);
        TRAIN and TRAINGOO mark 2 slots per set runtime-random, so for those
        the chain is the authored spine with the random tails laid where the
        file lists them. This layout used to walk the Tiles PALETTE instead --
        one slot per tile in listing order -- which a tester correctly called
        wrong: the order was on the disc all along.

        OFFSETS are derived: each tile's `.lnes` carries `_top`/`_bot` marker
        splines, and `top - bot` is that tile's own extent -- not a constant
        (TRAIN has one 120 among thirteen 60s; MSNCYN tiles climb 18.2), so
        the chain steps by each tile's own delta. A repeated tile is DRAWN
        once, at its first authored slot.

        Returns {stem: (dx, dy, dz)}. Marked on nodes as a display offset, so
        an edit still writes tile-local coordinates to the tile's own file.
        """
        layout, cursor = {}, [0.0, 0.0, 0.0]
        order = list(getattr(self, "_tile_seq", []) or
                     getattr(self, "_tile_stems", []))
        # Palette tiles the sequence never uses still get a place at the end,
        # so nothing on the disc is invisible in the editor.
        for extra in getattr(self, "_tile_stems", []):
            if extra not in order:
                order.append(extra)
        for stem in order:
            pts = {}
            try:
                text = self._read(stem + ".lnes").decode("latin-1", "replace")
            except Exception:
                continue
            for m in self._SOCKET.finditer(text):
                name = m.group(1)
                end = "top" if name.endswith("_top") else \
                      "bot" if name.endswith("_bot") else None
                if not end:
                    continue
                v = self._VERTS.search(text, m.end())
                if v:
                    nums = [float(x) for x in v.group(1).split()][:3]
                    if len(nums) == 3:
                        pts[end] = nums
            if "top" not in pts or "bot" not in pts:
                # No sockets: leave it at the cursor and do not advance, so an
                # unmeasurable tile cannot silently shift everything after it.
                layout.setdefault(stem, tuple(cursor))
                continue
            layout.setdefault(stem,
                              tuple(c - b for c, b in zip(cursor, pts["bot"])))
            cursor = [c + (t - b) for c, t, b
                      in zip(cursor, pts["top"], pts["bot"])]
        return layout

    def _load(self):
        lvls = self._entry_names("lvl")
        wanted = []

        # An area owns a matched set of files sharing one stem: lvl_bank.lvl,
        # lvl_bank.rsc, lvl_bank_*.bnds, lvl_bank.spwn, lvl_bank_var_4.enm.
        # Resolving the stem first is what keeps the bar's enemies out of the
        # gunshop when both live in the same archive.
        found = self.areas()
        if self.area:
            want = area_stem(self.area)
            self.area = next((a for a in found if area_stem(a) == want),
                             self.area)
        elif found:
            self.area = found[0]
        stem = area_stem(self.area) if self.area else None

        if self.manifest:
            self.lvl_file = self.manifest.lower()
        elif stem and (stem + ".lvl") in lvls:
            self.lvl_file = stem + ".lvl"
        elif stem and ("lvl_" + stem + ".lvl") in lvls:
            self.lvl_file = "lvl_" + stem + ".lvl"
        elif lvls:
            # Prefer a manifest whose name matches the archive.
            self.lvl_file = lvls[0]
            for cand in lvls:
                if self.name.lower() in cand.lower():
                    self.lvl_file = cand
                    break

        if self.lvl_file:
            try:
                self.sections = scene.parse_manifest(self._read(self.lvl_file))
            except Exception as ex:
                self.errors.append("%s: %s" % (self.lvl_file, ex))
            for section in _SECTIONS:
                wanted.extend(self.sections.get(section, []))

        # Spawns and enemy rosters are not listed in the manifest. In an
        # explicit-manifest level (a dev manifest inside SWAT.DAT, which holds
        # 300+ rosters from every level) take only files sharing its stem --
        # sweeping them all in would drown the level in unrelated enemies.
        # Multi-area archives need the same scoping for the same reason.
        scope = None
        if self.manifest:
            scope = self.lvl_file.rsplit(".", 1)[0]
        elif len(found) > 1 and self.lvl_file:
            scope = self.lvl_file.rsplit(".", 1)[0]
        if scope:
            # `startswith` alone also matches a LONGER area whose name begins
            # with this one. Scope `lvl_tomb` swallowed `lvl_tombsher.spwn`
            # and `lvl_tombsher_var_1.enm`, so the sheriff's office cast stood
            # in Brimstone's main street. The next character has to be a
            # separator for the file to be this area's own.
            def _mine(n):
                if not n.startswith(scope):
                    return False
                rest = n[len(scope):]
                return rest == "" or rest[0] in "._"
            wanted.extend(n for n in self._entry_names("spwn") if _mine(n))
            wanted.extend(n for n in self._entry_names("enm") if _mine(n))
        else:
            wanted.extend(self._entry_names("spwn"))
            wanted.extend(self._entry_names("enm"))

        wanted.extend(self._tile_files())

        available = {e.name for e in self.dave}
        seen = set()
        for filename in wanted:
            key = filename.lower().strip()
            if not key or key in seen:
                continue
            seen.add(key)
            if key not in available:
                self.errors.append("%s: named in manifest but not in archive" % key)
                continue
            try:
                data = self._read(key)
                self.nodes.extend(scene.parse_any(key, data))
            except Exception as ex:
                self.errors.append("%s: %s: %s" % (key, type(ex).__name__, ex))

        # Tile-owned nodes get their display offset. Everything else keeps
        # origin None -- a level with no `.til` never enters this at all.
        if getattr(self, "_tile_stems", None):
            layout = self._tile_layout()
            for node in self.nodes:
                off = layout.get(node.source.rsplit(".", 1)[0])
                if off and any(off):
                    node.origin = off

        for i, node in enumerate(self.nodes):
            node.uid = i

    # --------------------------------------------------------------- geometry

    def _load_rsc(self):
        """The level's RSC0 bundle -- the real geometry lives here.

        Locked, and the result is only published once the search finishes.
        Assigning None up front let a second caller see "already resolved, and
        it's nothing" while the first was still looking -- the viewport fires
        geometry and render_geometry concurrently, so one of them would
        silently come back empty.
        """
        with self._rsc_lock:
            if self._rsc is not False:
                return self._rsc

            # A failure caused by an EXCEPTION must not be cached. Releasing
            # the disc handles for PCSX2 closes the file under any Level that
            # is mid-flight; the read then throws, and caching that as "this
            # level has no geometry" left the level permanently blank -- a
            # whole world reduced to "undefined/0 chunks decoded" until the
            # Studio was restarted. A genuine "no bundle here" is still cached,
            # because that answer cannot change.
            broke = False
            if self.manifest or self.area:
                # Explicit-manifest levels live in SWAT.DAT, whose 460
                # *_lods.rsc files are characters and props. Falling through
                # to "first bundle with MOD0" would present a random character
                # model as the world -- accept only an exact-stem bundle.
                # A resolved area is just as exact, so it takes the same path.
                stem = (self.lvl_file.rsplit(".", 1)[0] if self.manifest
                        else self.area.rsplit(".", 1)[0])
                try:
                    names = {e.name for e in self.dave}
                    cand = stem + ".rsc"
                    if cand in names:
                        self._rsc = Rsc(self.dave.read(cand), cand)
                        self.rsc_name = cand
                    else:
                        self._rsc = None
                except Exception as ex:
                    self.errors.append("%s: %s" % (stem, ex))
                    self._rsc = False       # unresolved: try again next time
                    broke = True
                return None if broke else self._rsc

            found, found_name = None, None
            try:
                candidates = find_rsc_names(self.dave, self.name)
            except Exception as ex:
                self.errors.append("%s: %s" % (self.name, ex))
                self._rsc = False
                return None
            for name in candidates:
                try:
                    r = Rsc(self.dave.read(name), name)
                except Exception as ex:
                    self.errors.append("%s: %s" % (name, ex))
                    broke = True
                    continue
                if r.tally().get("BND0") or r.tally().get("MOD0"):
                    found, found_name = r, name
                    break
            if found is None and broke:
                self._rsc = False           # every candidate threw; retry later
                return None
            self._rsc = found
            self.rsc_name = found_name
            return self._rsc

    def geometry(self, precision=3):
        """Decoded collision meshes, ready for the viewport.

        Vertices are already world-space, so no node transform is applied --
        the .bnds instance matrices are identity for these.
        """
        r = self._load_rsc()
        if r is None:
            return dict(source=None, meshes=[], verts=0, tris=0)

        # chunk -> the node that references it, for naming/selection.
        by_res = {}
        for n in self.nodes:
            ci = scene.chunk_of(n)
            if ci is not None:
                by_res.setdefault(ci, n)

        meshes = []
        tv = tt = 0
        for index, m in sorted(r.bounds().items()):
            if not m.tris:
                continue
            node = by_res.get(index)
            pos = []
            for v in m.verts:
                pos.extend(round(c, precision) for c in v)
            idx = []
            for t in m.tris:
                idx.extend(t)
            meshes.append(dict(
                resourceIndex=index,
                uid=getattr(node, "uid", None),
                name=node.name if node else "bound_%d" % index,
                positions=pos, indices=idx,
                min=[round(c, precision) for c in m.aabb_min],
                max=[round(c, precision) for c in m.aabb_max]))
            tv += len(m.verts)
            tt += len(m.tris)

        return dict(source=self.rsc_name, meshes=meshes, verts=tv, tris=tt,
                    chunks=r.tally())

    def render_geometry(self, precision=3):
        """Decoded VIF display lists -- the level's visible surfaces.

        Separate from geometry() because these come from a different chunk
        family and a much less certain decode; the viewport shows them as
        their own toggleable layer.
        """
        r = self._load_rsc()
        if r is None:
            return dict(source=None, meshes=[], verts=0, tris=0, chunks=0)

        by_res = {}
        for n in self.nodes:
            ci = scene.chunk_of(n)
            if ci is not None:
                by_res.setdefault(ci, n)

        texmap = r.model_texture_map()
        slotmap = r.tex_slot_map()
        textable = r.texture_table()

        meshes = []
        tv = tt = 0
        attempted = 0
        for c in r.chunks:
            # Only MOD0 holds geometry. TEX0 chunks are 192-byte GS register
            # blocks -- counting them as attempted made coverage look like 74%
            # when every real model was in fact decoding.
            if c.tag != "MOD0":
                continue
            attempted += 1
            try:
                m = r.model(c.index)
            except Exception:
                continue
            if not m or not m["tris"]:
                continue
            node = by_res.get(c.index)
            pos = []
            for v in m["verts"]:
                pos.extend(round(x, precision) for x in v)
            idx = []
            for t in m["tris"]:
                idx.extend(t)
            cols = []
            for c4 in m["colors"][:len(m["verts"])]:
                # PS2 stores alpha with 0x80 as fully opaque; colour channels
                # are 0..255 but authored against a 2x overbright convention.
                cols.extend(min(1.0, c / 128.0) for c in c4[:3])
            uvs = []
            for uv in m["uvs"][:len(m["verts"])]:
                uvs.extend(round(x, 5) for x in uv)

            # One draw group per contiguous run of triangles that share a
            # texture. The part table gives this exactly, so a building can
            # carry a wall texture and a roof texture at once instead of
            # whichever single one the old nearest-preceding-TEX0 guess
            # happened to land on.
            groups = []
            for res, start, count, colour in m.get("part_ranges") or ():
                if count <= 0:
                    continue
                slot = slotmap.get(res)          # None == untextured (res 0)
                # Runs only merge when the texture AND the material tint
                # match; a decal sharing a texture with the surface under it
                # still has to draw separately.
                if groups and groups[-1]["texture"] == slot \
                        and groups[-1]["colour"] == colour \
                        and groups[-1]["start"] + groups[-1]["count"] == start * 3:
                    groups[-1]["count"] += count * 3
                else:
                    groups.append(dict(start=start * 3, count=count * 3,
                                       texture=slot, colour=colour))

            # `texture` stays as the model-level answer for callers that want
            # a single one (exporters, the texture tab's usage list). Prefer
            # the largest real group over the old heuristic.
            tex = None
            if groups:
                best = max((g for g in groups if g["texture"] is not None),
                           key=lambda g: g["count"], default=None)
                tex = best["texture"] if best else None
            if tex is None and not groups:
                tex = texmap.get(c.index)

            # `_2p` in the artist's name means SECOND PASS: the same surface
            # drawn again with a different texture -- baked shadows, dirt,
            # detail -- blended over the first pass rather than replacing it.
            # Every one of them carries vertex RGB 0, which is the tell: a
            # pass that ignores vertex lighting entirely. Drawn as ordinary
            # opaque geometry they sit on top of the real ground and hide it,
            # which is exactly what "the ground texture is wrong" looks like.
            name = node.name if node else "%s_%d" % (c.tag, c.index)
            second = "_2p" in name.lower()
            lit = any(cols[i] > 0.0 for i in range(0, len(cols), 3)) \
                if cols else False

            meshes.append(dict(chunk=c.index, tag=c.tag,
                               name=name, second_pass=second, lit=lit,
                               positions=pos, indices=idx,
                               colors=cols if len(cols) == len(pos) else [],
                               uvs=uvs if len(uvs) * 3 == len(pos) * 2 else [],
                               texture=tex, groups=groups,
                               texture_name=(textable[tex][1]["name"]
                                             if tex is not None and tex < len(textable)
                                             else None)))
            tv += len(m["verts"])
            tt += len(m["tris"])

        return dict(source=self.rsc_name, meshes=meshes, verts=tv, tris=tt,
                    chunks=attempted, decoded=len(meshes),
                    textures=[t[1] for t in textable])

    # ------------------------------------------------------------------ query

    def by_uid(self, uid):
        for n in self.nodes:
            if getattr(n, "uid", None) == uid:
                return n
        raise KeyError("no node with uid %d" % uid)

    # A `line_room_*` spline states a room's nominal extent; it is metadata, not
    # content. One of them, `line_room_newgrnd`, ships in EVERY level as a
    # 4-vertex box reaching 137 m from the origin -- a template artifact nobody
    # removed. Folding it into the camera framing gave every Brimstone interior
    # the same 218 x 187 x 196 m box: 3.2x too large on TOMB, whose real extent
    # is 68.6 x 9.5 x 58.7. Measured per level by scripts/probe_bounds.py.
    ROOM_TOKENS = ("room",)

    def _is_extent_marker(self, node):
        parts = node.name.split("_")
        return (node.kind == "line" and len(parts) > 1
                and parts[1] in self.ROOM_TOKENS)

    def bounds(self, content_only=False):
        """World AABB over everything placed, for framing the camera.

        `content_only` drops the room-extent marker splines. Both boxes are
        reported to the client rather than only the narrower one: "the level is
        nominally this big" is a real fact about the data, it just makes a poor
        default camera.
        """
        lo = [1e30, 1e30, 1e30]
        hi = [-1e30, -1e30, -1e30]
        any_pt = False
        for n in self.nodes:
            if content_only and self._is_extent_marker(n):
                continue
            pts = [n.xyz()]
            for v in n.verts:
                pts.append([c.value for c in v])
            for p in pts:
                any_pt = True
                for i in range(3):
                    lo[i] = min(lo[i], p[i])
                    hi[i] = max(hi[i], p[i])
        if not any_pt:
            return [0, 0, 0], [1, 1, 1]
        return lo, hi

    def stats(self):
        counts = {}
        for n in self.nodes:
            counts[n.kind] = counts.get(n.kind, 0) + 1
        return counts

    def to_json(self):
        lo, hi = self.bounds()
        clo, chi = self.bounds(content_only=True)
        return dict(
            archive=self.archive, name=self.name, lvl=self.lvl_file,
            manifest=self.manifest, sections=self.sections,
            stats=self.stats(), errors=self.errors,
            bounds=dict(min=lo, max=hi),
            # What to actually point the camera at -- see bounds() for why the
            # nominal box is nearly always far larger than the level.
            content_bounds=dict(min=clo, max=chi),
            nodes=self.nodes_json(),
        )

    # ------------------------------------------- what belongs to what
    #
    # A building and the thing you bump into are two separate nodes, and
    # nothing in the file says they are related -- so moving a storefront left
    # the player walking into an invisible wall where it used to be. The names
    # say it, though: `zone_piano_01` is drawn and `bound_piano_01` is solid,
    # and the artists were consistent about it.
    #
    # MEASURED over the 35 campaign archives, default area each: 1,538 bound
    # nodes, of which 826 share a stem with a building. That is 53.7% of all
    # shells, or 69.4% once the 347 MERGED shells are set aside -- those are
    # named bound_bound_grp_<flags>, cover several objects at once, and have
    # no single owner by design. The remainder are shells with no visible
    # object at all (bound_saloonCamera has nothing to draw).
    #
    # Widening the scope does not move it much: 41 distinct areas gives
    # 880/1,585 = 55.5% (69.2% ex-merged), and all 61 level archives give
    # 1,470/2,642 = 55.6% (68.2% ex-merged).
    #
    # An earlier note here said 826 of 1,153 = 71.6%, and that number was
    # copied into the front end where users read it. The numerator is right
    # and the denominator was never counted -- 1,153 is not the size of any
    # set in the data. Quote 53.7%, or 69.4% if the sentence is about shells
    # that COULD have an owner; say which, because the two differ by a third.
    #
    # A pairing is offered, never applied on its own. The shells that do not
    # pair are not failures to hide; they are told to the user, because a tool
    # that silently moved half the collision would be worse than one that says
    # which is which.

    # Merged shells come in two spellings: bound_bound_grp_ttfftt_01, whose
    # letters are a flag string, and plain bound_bound_grp_01_01. Stemming
    # strips the trailing numbers off the second, so match a bare "grp" too.
    _GRP = re.compile(r'^grp(_[tf]+)?$', re.I)

    @staticmethod
    def link_stem(name):
        """The shared part of `zone_piano_01` and `bound_piano_01`.

        Prefixes repeat in the stock data -- the merged shells really are
        called `bound_bound_grp_01_01` -- so they are stripped until none is
        left rather than once.
        """
        s = str(name or "")
        while True:
            nxt = re.sub(r'^(bound|zone)_', '', s, flags=re.I)
            if nxt == s:
                break
            s = nxt
        return re.sub(r'(_\d+)+$', '', s).lower()

    def links(self):
        """{uid: uid} both ways between a building and its collision shell."""
        zones, bounds = {}, {}
        for n in self.nodes:
            stem = self.link_stem(n.name)
            if not stem:
                continue
            if n.kind == "zone":
                zones.setdefault(stem, n)
            elif n.kind == "bound" and not self._GRP.match(stem):
                bounds.setdefault(stem, n)
        out = {}
        for stem, z in zones.items():
            b = bounds.get(stem)
            if b is not None:
                out[z.uid] = b.uid
                out[b.uid] = z.uid
        return out

    def nodes_json(self):
        """Nodes, each carrying the uid of its opposite number if it has one."""
        link = self.links()
        out = []
        for n in self.nodes:
            d = dict(uid=n.uid, **n.to_json())
            partner = link.get(n.uid)
            # `is not None`, not truthiness: uid 0 is a real node. Bounds are
            # enumerated first, so uid 0 is ALWAYS a bound, and a falsy test
            # drops the zone->bound direction every time -- measured, 16 broken
            # pairs across the 35 campaign areas, 100% of them the uid-0 node.
            # That is the one direction that matters: it means dragging the
            # building you can SEE leaves the collision shell behind, which is
            # the exact failure this pairing exists to prevent. It hid because
            # the bound half still reported a correct link.
            if partner is not None:
                d["linked"] = partner
                d["link_role"] = ("collision for this building"
                                  if n.kind == "bound"
                                  else "the shape you bump into")
            elif n.kind == "bound":
                d["link_note"] = (
                    "merged shell -- covers several objects, so it has no "
                    "single owner"
                    if self._GRP.match(self.link_stem(n.name))
                    else "no building shares this name")
            out.append(d)
        return out

    # ------------------------------------------------------------------- edit

    def preview(self, edits_by_file):
        """Dry-run: would each file still fit its slot? -> per-file report."""
        import zlib
        report = {}
        for filename, edits in edits_by_file.items():
            original = self._read(filename)
            new = scene.splice(original, edits)
            e = self.dave.find(filename)
            if e.compressed:
                co = zlib.compressobj(9, 8, -15)
                stored = len(co.compress(new) + co.flush())
            else:
                stored = len(new)
            report[filename] = dict(
                bytes_before=len(original), bytes_after=len(new),
                stored=stored, slot=e.slot, fits=stored <= e.slot,
                headroom=e.slot - stored,
            )
        return report

    def apply(self, edits_by_file, dry=False, writer=None, allow_grow=False):
        """Write edits back into the archive. All-or-nothing per file.

        `writer(filename, data) -> (used, slot)` lets the Studio interpose its
        journal so every write is undoable. Without one, writes go straight to
        the archive.

        `allow_grow` writes files that no longer fit their slot by relocating
        them into the archive's largest free gap (Dave._relocate). The report
        still says which files did not fit, with grew=True on the ones that
        were moved.
        """
        report = self.preview(edits_by_file)
        fits = all(r["fits"] for r in report.values())
        if dry or (not fits and not allow_grow):
            return dict(applied=False, dry=dry, report=report)

        write = writer or (lambda fn, data:
                           self.dave.patch(fn, data, grow=allow_grow))
        written = []
        for filename, edits in edits_by_file.items():
            original = self._read(filename)
            new = scene.splice(original, edits)
            try:
                used, slot = write(filename, new)
                self.sources[filename] = new
                row = dict(file=filename, used=used, slot=slot)
                if not report.get(filename, {}).get("fits", True):
                    row["grew"] = True
                    report[filename]["grew"] = True
                written.append(row)
            except SlotOverflow as ex:
                return dict(applied=False, error=str(ex), report=report,
                            written=written)
        # Spans shifted if any replacement changed length -- reparse only the
        # files that changed: re-parsing every file cost 1.13 s per move on
        # TRAINGOO (measured) to rebuild 163 files that did not change.
        self._reparse(touched=set(edits_by_file))
        return dict(applied=True, written=written, report=report)

    def _reparse(self, touched=None):
        """Rebuild self.nodes; with `touched`, only re-parse those files.

        uids are positional over the full node list, and the list is built in
        sources order -- so replacing one file's span of nodes in place keeps
        every other node's uid stable, which the front end depends on across
        an edit. A touched file whose node COUNT changed still renumbers
        everything after it, exactly as a full reparse would.
        """
        if touched and self.nodes:
            for filename in touched:
                try:
                    fresh = scene.parse_any(filename, self.sources[filename])
                except Exception as ex:
                    self.errors.append("%s: %s" % (filename, ex))
                    continue
                start = None
                end = 0
                for i, n in enumerate(self.nodes):
                    if n.source == filename:
                        if start is None:
                            start = i
                        end = i + 1
                if start is None:
                    self.nodes.extend(fresh)
                else:
                    self.nodes[start:end] = fresh
        else:
            self.nodes = []
            for filename, data in list(self.sources.items()):
                try:
                    self.nodes.extend(scene.parse_any(filename, data))
                except Exception as ex:
                    self.errors.append("%s: %s" % (filename, ex))
        for i, node in enumerate(self.nodes):
            node.uid = i
        # Route payload memos describe the previous bytes.
        self._geo_json = None
        self._render_json = None

    # ---------------------------------------------------------- edit builders

    def _fitting(self, node, edits, keep_width):
        """Full-precision edits if they fit; narrowed ones if they do not.

        A move used to be refused outright whenever the new numbers were
        longer than the old ones, and the file it lands in was full. Bounds
        are where that bites hardest: a collision instance usually ships with
        an identity matrix, so its position reads `0.000000` -- eight bytes --
        and ANY real coordinate needs nine or ten. A tester reported boxes
        that "get removed but never placed", which is what an optimistic
        viewport plus a refused write looks like from the outside.

        `fmt_num` has always been able to drop decimals to fit a byte span,
        and `set_rotation` has always passed `keep_width=True` for exactly
        this reason. Movement never asked. So: try full precision, and if the
        file would outgrow its slot, re-cut the same edit into the widths the
        numbers already occupy. Worst case that costs decimals -- 11.500000
        becomes 11.50000 -- on a format whose own exporter wrote six.

        A caller that explicitly passes keep_width gets what it asked for;
        this only rescues the default.
        """
        if keep_width:
            return {node.source: edits}
        report = self.preview({node.source: edits})
        entry = report.get(node.source) if isinstance(report, dict) else None
        if entry and entry.get("fits", True):
            return {node.source: edits}
        return None

    def move(self, uid, xyz, keep_width=False, allow_grow=False):
        n = self.by_uid(uid)
        wide = scene.edit_node_position(n, xyz, keep_width)
        # With growth allowed the slot is not a constraint, so the precision
        # narrowing that exists only to squeeze into it is skipped -- growing
        # a file to store fewer decimals would be the worst of both.
        if allow_grow:
            return {n.source: wide}
        got = self._fitting(n, wide, keep_width)
        if got is not None:
            return got
        return {n.source: scene.edit_node_position(n, xyz, True)}

    def resize(self, uid, size, keep_width=False):
        n = self.by_uid(uid)
        wide = scene.edit_node_size(n, size, keep_width)
        got = self._fitting(n, wide, keep_width)
        if got is not None:
            return got
        return {n.source: scene.edit_node_size(n, size, True)}

    def set_angle(self, uid, angle, keep_width=False):
        """Point a spawn or an enemy in a new direction.

        Named for what it does. It used to be called `rotate`, which was a
        misnomer with teeth: it reaches `edit_node_angle`, which only touches
        the single `Angle` number that spawns and enemies carry, and does
        nothing at all to the nine-number rotation matrix on a box, zone,
        bound or line. Anyone reading the call site reasonably concluded that
        rotation was already handled and that the viewport had a bug.
        """
        n = self.by_uid(uid)
        return {n.source: scene.edit_node_angle(n, angle, keep_width)}

    # The old name, kept because deleting it would break callers outside this
    # repository for no gain. It is a one-line forward, not a second path.
    rotate = set_angle

    def set_rotation(self, uid, matrix9, keep_width=True):
        """Rewrite the nine rotation numbers of a node's matrix, in file order.

        Raises KeyError for a node with no matrix -- spawns and enemies carry
        a single `Angle` instead, and the caller is pointed at `set_angle`.
        """
        n = self.by_uid(uid)
        if not n.rot:
            raise KeyError("%s has no rotation matrix" % n.name)
        return {n.source: scene.edit_node_rot(n, matrix9, keep_width)}

    def set_vertex(self, uid, index, xyz, keep_width=False):
        """Move one point of a spline.

        `scene.edit_vertex` has existed and been called by nothing: no route,
        no module, no line of the front end. 11,413 splines -- 129 of them
        patrol routes, one of them the train track -- have had per-vertex
        spans parsed all along and could not be reshaped.
        """
        n = self.by_uid(uid)
        if index < 0 or index >= len(n.verts):
            raise IndexError("%s has %d point(s); there is no point %d"
                             % (n.name, len(n.verts), index + 1))
        return {n.source: scene.edit_vertex(n, index, xyz, keep_width)}

    def set_attributes(self, uid, changes):
        """Rewrite typed per-instance attributes in place. Values are strings.

        Only the VALUE span moves, so the `TYPE NAME VALUE` column layout is
        preserved byte for byte and a `BOOL true`->`false` edit costs exactly
        one byte.
        """
        n = self.by_uid(uid)
        edits = []
        for name, value in changes.items():
            edits.extend(scene.edit_attribute(n, name, value))
        return {n.source: edits}

    def duplicate(self, uid, offset=(1.0, 0.0, 1.0)):
        """Clone an object into its own file, updating the header count.

        This is how new content gets created without adding a file to the
        archive -- the block goes in an existing file's slot headroom.
        """
        n = self.by_uid(uid)
        data = self._read(n.source)
        edits, new_name = scene.duplicate_block(data, n, offset)
        edits += scene.bump_count(data, n.source, +1)
        return {n.source: edits}, new_name

    def roomiest(self, ext=None, like=None):
        """The placement file in this level with the most spare slot space.

        Slot headroom is the scarce resource in this format, and it is spread
        very unevenly: in the Brimstone bar one zone file has 31 spare bytes
        and another has 677. Choosing the roomiest turns placements that
        would be refused into ones that fit.
        """
        # Tile files are excluded as placement targets. A tile is a reusable
        # piece the engine drops into a randomly generated track, and its
        # coordinates are tile-local -- so an object placed "here" in the
        # viewport would reappear wherever that tile happened to land next
        # playthrough, and in as many copies as the tile got used. They are
        # loaded so they can be SEEN and edited in place; they are not somewhere
        # to put new things.
        tiles = set(getattr(self, "_tile_stems", []) or [])

        best, best_spare = None, None
        for name in sorted({n.source for n in self.nodes}):
            if name.rsplit(".", 1)[0] in tiles:
                continue
            if ext and not name.endswith(ext):
                continue
            if like and not name.endswith(like.rsplit(".", 1)[-1]):
                continue
            try:
                spare = self.capacity(name)["approx_text"]
            except Exception:
                continue
            if best_spare is None or spare > best_spare:
                best, best_spare = name, spare
        return best

    def transplant(self, uid, target, offset=(0.0, 0.0, 0.0)):
        """Clone an object into a different file of the same level."""
        n = self.by_uid(uid)
        if target == n.source:
            return self.duplicate(uid, offset)
        data = self._read(n.source)
        dest = self._read(target)
        edits, new_name = scene.transplant_block(data, n, dest, offset)
        edits += scene.bump_count(dest, target, +1)
        return {target: edits}, new_name

    def delete(self, uid):
        n = self.by_uid(uid)
        data = self._read(n.source)
        edits = scene.delete_block(data, n)
        edits += scene.bump_count(data, n.source, -1)
        return {n.source: edits}

    def capacity(self, filename):
        """How much room this file has left, in stored and rough text bytes."""
        import zlib
        data = self._read(filename)
        e = self.dave.find(filename)
        if e.compressed:
            co = zlib.compressobj(9, 8, -15)
            stored = len(co.compress(data) + co.flush())
            ratio = len(data) / max(1, stored)
        else:
            stored = len(data)
            ratio = 1.0
        spare = e.slot - stored
        return dict(file=filename, raw=len(data), stored=stored, slot=e.slot,
                    spare_stored=spare, approx_text=int(spare * ratio))

    def set_field(self, uid, key, value):
        """Edit one property of a node (currently enemy fields)."""
        n = self.by_uid(uid)
        data = self._read(n.source)
        return {n.source: scene.edit_enemy_field(data, n, key, value)}

    # -------------------------------------------------------------- enemies

    def enm_files(self):
        """This level's own `.enm` rosters, roomiest first.

        Empty for 26 of the 61 level archives: every Showdown level's roster
        lives only in SWAT.DAT, and `_load` sweeps only the level's own
        archive. That is why those levels show an empty Enemies layer, and it
        is a fact the caller must say out loud rather than render as nothing.
        """
        names = sorted({n.source for n in self.nodes
                        if n.source.lower().endswith(".enm")})
        if not names:
            names = sorted(n for n in self.sources
                           if n.lower().endswith(".enm"))

        def room(fn):
            try:
                return -self.capacity(fn)["approx_text"]
            except Exception:
                return 0
        return sorted(names, key=room)

    def enemy_nodes(self, filename=None):
        return [n for n in self.nodes
                if n.kind == "enemy" and (filename is None
                                          or n.source == filename)]

    def enemy_fields(self, uid):
        """All 21 fields of one enemy, typed, in the frozen order."""
        from . import enemyfields as EF
        n = self.by_uid(uid)
        if n.kind != "enemy":
            raise KeyError("uid %d is a %s, not an enemy" % (uid, n.kind))
        data = self._read(n.source)
        return n, EF.describe(data, n.span)

    def enemy_lines(self, uid):
        from . import enemyfields as EF
        n = self.by_uid(uid)
        return n, EF.parse_block(self._read(n.source), n.span)

    def set_enemy_fields(self, uid, changes):
        """Edits that set/insert/remove fields on one enemy block."""
        from . import enemyfields as EF
        n = self.by_uid(uid)
        if n.kind != "enemy":
            raise KeyError("uid %d is a %s, not an enemy" % (uid, n.kind))
        data = self._read(n.source)
        return {n.source: EF.edits_for(data, n.span, changes)}

    def place_enemy(self, target, template, pos, angle=0.0, fields=None,
                    name=None):
        """Edits that append one new Enemy block to `target`.

        Cloned from an existing block rather than generated, for the same
        reason `duplicate` clones: the surrounding whitespace, the field order
        and the `Spawn` sub-block are all things the game's own exporter wrote,
        and reproducing them from scratch is a way to be subtly wrong. The
        template is the SMALLEST block available unless one is named, because
        the cost of the clone is what decides whether the placement fits.

        Returns ({file: edits}, new_name).
        """
        from . import enemyfields as EF
        src = self._read(template.source)
        dest = self._read(target)
        taken = scene.existing_enemy_names(dest)
        if template.source != target:
            taken |= scene.existing_enemy_names(src)
        if not name:
            base = "%s_01" % (fields or {}).get("Type", template.props.get("Type")
                                                or "enemy")
            name = base if base not in taken else scene.unique_name(base, taken)
        elif name in taken:
            name = scene.unique_name(name, taken)

        eol = b"\r\n" if b"\r\n" in dest else b"\n"
        block = EF.new_block(src, template, name, dict(fields or {}), eol)
        block = EF.set_point(block, pos, angle)
        return {target: EF.append_block(dest, block)}, name, len(block)

    def merge(self, *edit_dicts):
        """Combine several edit dicts, concatenating per-file edit lists."""
        out = {}
        for d in edit_dicts:
            for filename, edits in d.items():
                out.setdefault(filename, []).extend(edits)
        return out

    # ------------------------------------------- model + collision are ONE thing
    #
    # 2026-11-05 (the developer: "collision and the model are treated separate, it
    # makes it cluttered"). A model and its collision are linked by NAME STEM:
    # `zone_<stem>_NN` (drawn, .znes) and `bound_<stem>_NN` (solid, .bnds).
    # Probe `research/probe_model_collision_link.py`, all 61 level archives:
    # 1,470 stem pairs; the pair's world AABBs overlap on 1,377 (93.7%), IoU>0.3
    # on 1,284 (87.3%), median IoU 0.99999 -- i.e. the collision shell is the
    # model's own geometry. (The ~90 IoU=0 pairs are `alpha_*glass*` decals.)
    # Merged `bound_bound_grp_*` shells cover several objects: NO single owner.
    # So linking is exact where a stem pair exists and impossible otherwise;
    # `collision_status` says which, in words the UI shows verbatim.

    def collision_status(self, uid):
        """Plain-English collision state of one model. -> dict(linked, note)."""
        n = self.by_uid(uid)
        partner = self.links().get(uid)
        if n.kind not in ("zone", "bound"):
            return dict(linked=False, note="")
        if partner is None:
            if n.kind == "bound":
                if self._GRP.match(self.link_stem(n.name)):
                    return dict(linked=False, note="Shared collision: this shell "
                                "covers several objects, so it cannot follow one model.")
                return dict(linked=False, note="No model shares this collision's name.")
            return dict(linked=False, note="This model has no matching collision "
                        "(it is walk-through, or covered by a shared shell).")
        return dict(linked=True, partner=partner,
                    note="Collision follows this model automatically.")

    def with_collision(self, changes):
        """Add each moved model's linked collision to a list of position
        changes, translated by the SAME delta. Pure; never mutates the level.

        `changes` = [{uid, pos, ...}]. Returns a new list. A change whose
        partner is already listed is left alone (the caller placed both)."""
        link = self.links()
        have = {int(c["uid"]) for c in changes}
        out = list(changes)
        for c in changes:
            if "pos" not in c:
                continue
            u = int(c["uid"])
            pu = link.get(u)
            if pu is None or pu in have:
                continue
            n, pn = self.by_uid(u), self.by_uid(pu)
            d = [a - b for a, b in zip(c["pos"], n.xyz())]
            out.append(dict(uid=pu, pos=[b + x for b, x in zip(pn.xyz(), d)],
                            via=u))
            have.add(pu)
        return out

    # Flags that make a shell stop walkers. Camera/Shoot/Target are left alone
    # so a "walk-through" model still blocks bullets and the camera as before.
    WALK_FLAGS = ("BoundFlagMover", "BoundFlagPlayer", "BoundFlagEnemy",
                  "BoundFlagHorse")

    def set_solid(self, uid, solid):
        """Per-model solid / walk-through. Edits the linked bound's walker flags
        in place. Refuses (ValueError) when the model has no single linked
        shell. UNTESTED IN-GAME; the node NAME still spells the old flags."""
        st = self.collision_status(uid)
        if not st["linked"]:
            raise ValueError(st["note"] or "this model has no collision to change")
        b = self.by_uid(st["partner"])
        if b.kind != "bound":
            b = self.by_uid(uid)
        edits = []
        for f in self.WALK_FLAGS:
            try:
                edits += scene.edit_attribute(b, f, "true" if solid else "false")
            except KeyError:
                pass
        if not edits:
            raise ValueError("this collision has no walker flags to change")
        return {b.source: edits}


def list_levels(game):
    return [a for a in game.archives()
            if a.startswith(("LVLS_SNG/", "LVLS_M2/", "LVLS_M4/"))]
