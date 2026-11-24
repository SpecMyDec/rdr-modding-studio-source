"""The disc's asset catalogue -- every placeable model, and where it lives.

A level is a *scene graph over shared assets*, not a lump of geometry. Each
`.znes` zone node is an instance: a name, a transform, and a `resourceIndex`
pointing at a model chunk in the area's RSC0 bundle. The same asset name
recurs across the disc -- `skydome_cpv` in 51 areas, `table` in 23 -- because
the artists reused a shared kit.

That distinction is what this module exists to make usable. It separates:

    asset   the model itself, identified by name, owned by an area bundle
    path    where an instance of it is placed, owned by a .znes file

so you can browse "every prop on the disc" independently of "what this map
happens to place", then place any of them.

THE PLACEMENT RULE

An asset can only be instanced into an area whose own RSC bundle already
contains that model. 41% of asset names appear in more than one area, which is
what makes cross-map placement work at all: importing the saloon table from
Brimstone into another saloon means instancing *that map's own copy*, which is
a text edit to a .znes file and completely safe.

This used to be justified by asserting that a Dave archive can never take a
new file. That assertion is FALSE -- `dave.add()` inserts a new entry without
moving a byte of anybody else's data, 64 of the 66 archives will take one as
they stand, and the path is boot-verified (scripts/probe_dat_add.py,
scripts/test_add_named.py, 05_Docs/CAPABILITIES.md C-001). The real constraint
is one level down and is about a different container: the model has to be a
chunk in the area's RSC0 *bundle*, and adding a model to an RSC is unsolved
(M18). Getting that wrong is what made the placement rule look like a law of
the archive format.

That 41% flatters the disc, though. Only 20% (997 of 4,961, measured) appear
in more than one distinct LEVEL -- the rest are a level's own 2- and 4-player
copies, BRIDGE/BRIDGE2/BRIDGE4, which is not reuse. `reach` counts levels.

`placements_for` answers that question honestly -- it reports which areas can
take an asset and which cannot, rather than offering a placement that would
need a model the target bundle does not have.
"""

import json
import os
import re
import threading
import time

from . import scene as scene_mod
from .level import Level
from .rsc import area_stem

_INSTANCE_SUFFIX = re.compile(r"_\d+$")
_KIND_PREFIX = re.compile(r"^(zone|bound|box|line)_")

# 4: places gained a `chunk` field. The old cache stored only the raw
# `resourceIndex`, which is one-based, so anything that used it to look up
# geometry was one chunk off.
# 5: the taxonomy below. Records gained category/why/flags/form/reach/size,
# and `reach` counts level FAMILIES, not areas -- see LEVEL_FAMILY.
# 6: the collision-shell rule stopped ignoring case, which changes both the
# flags and the stems 43 names are classified from. A cache from 5 has those
# names filed wrong, so it has to be rebuilt rather than reused.
CACHE_VERSION = 6


def asset_name(node_name):
    """'zone_saloonPorchS_2p_01' -> 'saloonPorchS_2p'.

    Strips the node-kind prefix and the two-digit instance counter, leaving
    the artist's name for the asset itself.
    """
    name = _KIND_PREFIX.sub("", node_name or "")
    return _INSTANCE_SUFFIX.sub("", name) or name


# ============================================================== the taxonomy
#
# Four independent things are true of every asset, and the old single
# `category` conflated them:
#
#   what it is      read from the name -- a wall, a wagon, a bush
#   how it is built read from the chunk it points at -- drawn, solid, or an
#                   invisible collision volume
#   how far it goes how many separate levels ship a copy of it
#   how it is drawn the flags the artists bolt onto a name
#
# The last one is why the old classifier put 15% of the disc in "effect":
# `alpha_01_libChairs` is a chair, and `alpha_01_` only says it is drawn with
# blending. Those affixes are stripped before the name is read, and offered
# as their own filters instead.

# Affixes measured across all 4,961 names. Counts in the comments are how many
# names carry each one (measured, not estimated).
_MOD_RULES = [
    # 606 names. crack01/02/03 are progressive damage states of one object.
    ("damaged",    re.compile(r"^crack0?\d+", re.I)),
    # 653 names. Alpha-blended geometry: foliage, glass, dust, cloth.
    ("seethrough", re.compile(r"^alpha[_ ]?\d*[_ ]?", re.I)),
    # 159 names. Pieces of the randomly assembled train/coach routes.
    ("tile",       re.compile(r"(^|_)tile_", re.I)),
    # 267 names. Doors, birds, bats, swinging signs.
    ("animated",   re.compile(r"_anim$", re.I)),
    # 40 names.
    ("dynalite",   re.compile(r"_dynalite$", re.I)),
    # 334 names, every one of which has a same-named twin in the same area,
    # so this really is a second draw of the same surface.
    ("extrapass",  re.compile(r"_\d+p$", re.I)),
    # The capital S is the whole rule. SB / _SB / Sb / sb is the artists'
    # collision shell -- 324 names, 96.0% of them collision-only chunks and
    # 97.2% with collision of some kind (measured).
    #
    # Matching this case-insensitively also swallowed 43 names ending `sB`,
    # and that is a plural followed by a variant letter, not a shell: `rocksB`
    # is the B draw of `rocks`. NOT ONE of the 43 is collision-only -- 40 are
    # drawn-only -- so the flag was wrong every time it fired there. It also
    # ate the plural off the stem before the name was read, leaving `rock`,
    # `coreNewLamp` and `hearthMes` to be classified.
    ("collshell",  re.compile(r"_?(SB|Sb|sb)$")),
    ("_ns",        re.compile(r"_?NS$")),
    ("_s",         re.compile(r"(?<=[a-z0-9])S$")),
]
_MOD_BY = dict(_MOD_RULES)
_MOD_ORDER = [k for k, _ in _MOD_RULES]

# The flags a catalogue record can actually carry. The two keys beginning with
# "_" are working state for the shadow test below, never labels, and
# `shadowvar` is decided from a name's twin rather than from the name alone.
FLAGS = [k for k in _MOD_ORDER if not k.startswith("_")] + ["shadowvar"]

_CAMEL = re.compile(r"([a-z0-9])([A-Z])")


def strip_modifiers(name):
    """(flags, stem). The stem is the artist's word for the thing itself."""
    flags, stem = [], name
    for key in _MOD_ORDER:
        new = _MOD_BY[key].sub("", stem)
        if new != stem and new:
            stem = new
            if key not in flags:
                flags.append(key)
    return flags, stem


def name_words(stem):
    """Split an artist name into words. `saloonPorchS_2p` -> saloon, porch."""
    s = _CAMEL.sub(r"\1 \2", stem)
    s = re.sub(r"[_\-.]+", " ", s)
    s = re.sub(r"\d+", " ", s)
    return [t.lower() for t in s.split() if t]


# Every word below was taken from a frequency census of the real names, not
# from imagination: 390 words appear in five or more asset names, and these
# are the ones that mean something. Longest keyword wins, so `barber` is read
# as a building before `bar` is read as a counter.
CATEGORY_WORDS = [
 ("blocker", """boundgrp bound_grp seperatebounds snk sneaker cam camera
    coverobj cover bump blocker collision nocam nosnk bound bounds cambound
    invisible blockade libsnk snksb"""),
 ("sky", """skydome sky cloud clouds cpv horizon"""),
 ("effect", """shadow shadows shdw fade dust smoke vapor vapors reflection
    flicker glow flare haze fog mist ray rays colorbar godray burn
    steam spark sparks"""),
 ("light", """light lights lamp lamps lantern lanterns chandelier
    candle torch fire campfire stagelights dynalite gels"""),
 ("character", """scare scarecrow bird birds bat bats horse hrs deadguys
    servant servants enemy enemyspawner pig dog cow cattle chicken corpse
    body totem totems skel skeleton bones bear deer bison horns
    ribs townsfolk drunk coop"""),
 ("vehicle", """wagon wagons wgn coach cart caboose passenger cargo flatbed
    boxer logger supply mailcar coalwagon steamer train handcar gondola
    locomotive buggy stagecoach wheel wheels piston railveh railroad
    track tracks cargocar supplycar carriage"""),
 ("weapon", """cannon gatler gatling turret gun guns rifle pistol ammo
    dynamite bomb"""),
 ("sign", """sign signs flag flags banner poster posters news billboard
    ads placard"""),
 ("vegetation", """tree trees bush bushes bushtop shrub weed weeds grass
    plant plants flower flowers cact cactus veg vegetation branch branches
    limb trunk stump foliage woods thatch vine leaf leaves treetop hedge
    drygrass neargrass grassedge forest bushesfour junkiebush
    brush oak oaks"""),
 ("terrain", """ground grnd grd terrain dirt sand hill hills mtn mount
    mountain mountains mountns mesa cliff canyon cyn butte buttes rockies
    rock rocks rox rk boulder boulders river water waterfall
    creek falls snow cave caves tunnel tunnels tunel tnl tnls road trail
    beach mud gorge ravine crock arock brock drock erock frock ledge
    trn cch tgo tgh tgc tg tun outgrnd desert scrub stalagtite
    stalagtites stalactite transitiongrd mound mounds log logs
    escarp plateau ridge"""),
 ("building", """house houses hs bldg building buildings saloon sal jail
    jl church bank barn shed bshed shack store cstore shop tower twr fort
    hotel stable livery lvry mansion mans cottage lodge hospice diner
    barber parlour parl office ofc theater theatre laundry blacksmith
    smith pawn outhouse station armoury ranch tomb crypt courthouse
    bighouse nhouse facbuilding facbuildingsix slum gazebo gazeebo
    monument circus jailhouse cornerstore clocktower saloonint depot
    mission hacienda cantina bunkhouse warehouse mill storage zoneset
    ruin ruins perfect unforg unforgiven law sheriff sher lib library
    funeral funr general serv servant hospital doc school lemonstand"""),
 # "Building parts" was one bucket of 1,922 -- 39% of the disc, and no more
 # findable than the old "prop". These five are the split the names support:
 # every word below was already in use, and the residue was small enough to
 # place by hand. Measured sizes are roughly 520 / 430 / 370 / 340 / 160.
 ("opening", """door doors dr drs mansdr cdr fdr secretdr window windows
    win wndw cwin bwin ewin glass gate gates gt shutter shutters doorway
    entry entrance enter exit opn threshold sill"""),
 ("surface", """wall walls wl wll sandwall floor flr floors ceiling roof
    roofs shingle brick bricks plank planks board boards siding facade
    facades fachade fachada trim medallion moulding cornice lattice awning
    wood wd upperwood corner corners slab foundation fundation"""),
 ("frame", """post posts postie posties pole poles beam beams column
    columns pillar pillars frame frames frms truss trusses girder joist
    rafter timber timbers scaffold support supports chimney chimneys bars
    grate mesh trellis panel panels balustrade fence fences fnc fench fenc
    rail rails railing"""),
 ("access", """stair stairs strs step steps btmsteps porch porches deck
    ramp platform bridge bridgetop bridgepiece balcony pathway path arch
    arches stairwell"""),
 ("space", """room chamber hall hallway corridor interior interiors
    exterior ext int inside outside basement attic loft story upstairs
    downstairs closet stall stalls divider partition secret build buildng
    extension"""),
 ("furniture", """chair chairs brchair table tables brtable bftable thtop
    bed beds bench benches stool desk bureau cabinet dresser vanity shelf
    shelves rug carpet mirror picture pictures pics painting paint
    curtain curtains piano clock stove safe furniture funiture sofa couch
    wardrobe nightstand bartop kegstand basin bathtub trough sink cot pew
    bookcase case chand showerhead shower hearth fireplace
    bar bartop counter
    altar chest drapes drape kitchen dining"""),
 ("prop", """crate crates barrel barrels box boxes bottle bottles sack
    sacks pot pots basket bucket keg kegs junk junklets debris rubble
    stuff extras clutter mess trash scrap scraps wreck wreckage broom
    brooms tool tools rope cloth clothes papers book books
    treasure item items cup cups plate plates dish bag bags
    canteen saddle harness sandbags sandbag headstone gravestone
    cryptstone tombstone grave graves casket caskets coffin cross
    haybale haybales hay groceries fruit hat hats target targt
    fountain fntn foun fount bullseye luggage
    lugage dress dresses perfume supplies straw marker envelope
    cord hanger wire brbwire boots shirt sock pile"""),
]

CATEGORIES = [c for c, _ in CATEGORY_WORDS] + ["unclassified"]

# Three-letter words are only matched inside a longer word when they are
# unambiguous across the whole disc. Allowing all of them read `bathroomdr`
# as a bat and `textB` as an exterior; these twelve produced no wrong answer
# in a name-by-name check of every match they made.
_SHORT_OK = frozenset(["snk", "rox", "mtn", "veg", "rug", "log",
                       "sky", "drs", "wll", "int", "tnl", "grd"])

_KEYWORDS = []
for _cat, _words in CATEGORY_WORDS:
    for _w in _words.split():
        _KEYWORDS.append((_w.lower(), _cat))
_KEYWORDS.sort(key=lambda kv: -len(kv[0]))
_EXACT: dict[str, str] = {}
for _w, _c in _KEYWORDS:
    _EXACT.setdefault(_w, _c)

# A few prefixes are a hard label the artists applied deliberately, and they
# beat any word later in the name: WHEELS_armouryAA is a wheel, not a shop.
_PREFIX_RULES = [
    (re.compile(r"^WHEELS_"), "vehicle", "the WHEELS_ prefix"),
    (re.compile(r"^railveh_"), "vehicle", "the railveh_ prefix"),
    (re.compile(r"^SHADOW_"), "effect", "the SHADOW_ prefix"),
    (re.compile(r"^fade_"), "effect", "the fade_ prefix"),
    (re.compile(r"^bound_grp"), "blocker", "a bound_grp collision group"),
    (re.compile(r"^seperatebounds", re.I), "blocker", "a bounds group"),
    (re.compile(r"^zoneset_"), "building", "the zoneset_ prefix"),
    (re.compile(r"^cannon\d"), "weapon", "the cannonNN prefix"),
    (re.compile(r"^item\d"), "prop", "the itemNN prefix"),
    # A colour-bar test card. camelCase splits it into "color"
    # and "bar", and "bar" then reads as a saloon counter.
    (re.compile(r"^colorBar", re.I), "effect", "a colour-bar test card"),
    (re.compile(r"^door\d"), "opening", "the doorNN prefix"),
]


def _ranked_words(stem):
    """The words of a name, most likely head noun first.

    These names are head-final: the LAST word says what the thing is and the
    earlier ones say whose it is. `cottageStair` is a stair, `officeChairSB` is
    a chair, `generalRoof` is a roof. Reading them longest-word-first instead
    filed 497 assets under the building they belong to rather than under what
    they are. Length only breaks ties, so `barber` still beats `bar`.

    The ordering has to be total. Reading the words out of a `set` left ties
    to Python's per-process hash seed, so `houseFloor` came back as a house on
    one run and a floor on the next, and a cached catalogue disagreed with
    itself between restarts.
    """
    last = {}
    for i, w in enumerate(name_words(stem)):
        last[w] = i
    return sorted(last, key=lambda w: (-last[w], -len(w)))


def classify(name):
    """(category, why). `why` is shown in the panel so nothing is a black box.

    Measured on the whole disc: 95.5% of names land in a category, 4.5% stay
    "unclassified" -- names like `stopGnc` and `ppear01` that carry no word
    anyone could read.
    """
    for rx, cat, why in _PREFIX_RULES:
        if rx.search(name):
            return cat, why
    _, stem = strip_modifiers(name)
    words = _ranked_words(stem)
    # Plural tested per word, inside the head-final walk, not as a second
    # pass over all of them. As two passes any exact hit anywhere beat a
    # plural on the last word, which is exactly backwards: `fortCliffs` came
    # out a fort, `roofGirders` a roof, `stableJoists` a stable. 17 names
    # measured, 16 of them now landing on the noun the artist put last.
    for w in words:
        if w in _EXACT:
            return _EXACT[w], "the word %r" % w
        # The same word minus a plural s: `moreRugs` is a rug.
        if w.endswith("s") and w[:-1] in _EXACT:
            return _EXACT[w[:-1]], "the word %r" % w[:-1]
    # The artists squash words together (officewallleft, chimneyspikes), so
    # try each keyword at the start or end of a word. The anchoring is not
    # decoration: an unanchored search reads "machAir" as a chair.
    for w in words:
        for key, cat in _KEYWORDS:
            if w == key or len(key) < 3:
                continue
            if len(key) == 3 and key not in _SHORT_OK:
                continue
            if w.startswith(key) or w.endswith(key):
                return cat, "%r inside %r" % (key, w)
    # Last resort: a long word anywhere. Six characters is the shortest
    # length that stopped producing nonsense matches when measured.
    low = stem.lower()
    for key, cat in _KEYWORDS:
        if len(key) >= 6 and key in low:
            return cat, "the name contains %r" % key
    return "unclassified", "no readable word in the name"


# --------------------------------------------------------------- other axes

# BRIDGE.DAT, BRIDGE2.DAT and BRIDGE4.DAT are the same map shipped for one,
# two and four players. Counting them as three maps makes an asset that only
# ever existed in one level look widely shared: 2,019 assets are in more than
# one AREA, but only 997 are in more than one level FAMILY. `reach` uses the
# family, because a copy inside your own map's multiplayer twin is not reuse.
_FAMILY_SUFFIX = re.compile(r"(NEW|\d+)$", re.I)

# Two levels spell their single-player archive out in full while the
# multiplayer twins abbreviate, so stripping the digits does not land on the
# same stem. Listed rather than inferred by prefix, because MANSROOF and
# TRAINCAR share a prefix with MANS and TRAIN and are genuinely different
# levels -- a prefix rule would merge those too. Worth 80 assets, 41 of which
# were sitting in the wrong reach band.
_FAMILY_ALIAS = {"SHERNIT": "SHERNITE", "GUN": "GUNSMOKE"}


def level_family(archive):
    stem = archive.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    base = _FAMILY_SUFFIX.sub("", stem) or stem
    return _FAMILY_ALIAS.get(base.upper(), base)


def reach_band(families):
    if families <= 1:
        return "unique"
    if families <= 3:
        return "shared"
    return "everywhere"


def form_of(kinds):
    """What the asset is made of, from the chunk kinds its nodes point at.

    Measured across the disc and exact, not a heuristic: every `zone` node
    resolves to a MOD0 (drawn) chunk and every `bound` node to a BND0
    (collision) chunk, 4,961/4,961.
    """
    zone = kinds.get("zone", 0)
    bound = kinds.get("bound", 0)
    if zone and bound:
        return "solid"          # drawn AND collidable
    if bound:
        return "collision"      # an invisible volume
    return "visual"             # drawn, walk straight through it


# Bands for the largest edge of a collision box, in world units. The
# quartiles measured over the 1,757 assets that have one are 2.4 / 7.9 / 22.2.
def size_band(extent):
    if extent is None:
        return None
    if extent < 1.0:
        return "tiny"
    if extent < 5.0:
        return "small"
    if extent < 20.0:
        return "medium"
    if extent < 80.0:
        return "large"
    return "huge"


def _flag_list(flag):
    """Flags as a list, from either a query string or a list."""
    if isinstance(flag, str):
        return [f for f in flag.split(",") if f]
    return list(flag or ())


def _bound_extent(rsc, chunk):
    """Longest edge of a chunk's collision box, or None if it has none.

    Only BND0 chunks carry one, which is 35.4% of assets -- everything else
    gets no size band rather than a guessed one.
    """
    if rsc is None or chunk is None or chunk < 0:
        return None
    try:
        if chunk >= len(rsc.chunks) or rsc.chunks[chunk].tag != "BND0":
            return None
        mesh = rsc.bound(chunk)
    except Exception:
        return None
    if mesh is None:
        return None
    return round(max(hi - lo for lo, hi
                     in zip(mesh.aabb_min, mesh.aabb_max)), 3)


class AssetIndex:
    """A catalogue of every asset instance on the disc.

    Building it walks all 97 areas and opens each one's RSC bundle for the
    collision-box sizes (6.4s without the bundles, 10.1s with them, measured),
    so the result is cached to disc and rebuilt only when the cache is missing
    or stale.
    """

    def __init__(self, game, cache_path=None):
        self.game = game
        self.cache_path = cache_path
        self.lock = threading.Lock()
        self._data = None

    # ----------------------------------------------------------------- build

    def _scan(self):
        from .level import list_levels

        assets = {}      # asset name -> record
        areas = []       # every (archive, area) pair with its stats

        for archive in list_levels(self.game):
            try:
                base = Level(self.game, archive)
            except Exception:
                continue
            found = base.areas()
            for area in (found or [None]):
                try:
                    level = (base if area == base.area
                             else Level(self.game, archive, area=area))
                except Exception:
                    continue

                # The area's own RSC bundle, for the size of an asset's
                # collision box. A failure here must not lose the area: the
                # catalogue is still useful without a size band.
                try:
                    rsc = level._load_rsc()
                except Exception:
                    rsc = None

                counts = {}
                instances = 0
                for node in level.nodes:
                    res = node.props.get("resourceIndex")
                    if res is None or node.kind not in ("zone", "bound"):
                        continue
                    # Both are kept on purpose. `resourceIndex` is the raw
                    # file value and is what gets written back; `chunk` is the
                    # decoded 0-based index and is what you look geometry up
                    # with. They differ by one -- see scene.chunk_of.
                    chunk = scene_mod.chunk_of(node)
                    name = asset_name(node.name)
                    if not name:
                        continue
                    instances += 1
                    cat, why = (classify(name) if name not in assets
                                else (assets[name]["category"],
                                      assets[name]["why"]))
                    rec = assets.setdefault(name, dict(
                        name=name, category=cat, why=why, instances=0,
                        places=[], kinds={}, families=[], extent=None))
                    rec["instances"] += 1
                    rec["kinds"][node.kind] = rec["kinds"].get(node.kind, 0) + 1
                    fam = level_family(archive)
                    if fam not in rec["families"]:
                        rec["families"].append(fam)
                    ext = _bound_extent(rsc, chunk)
                    if ext is not None and (rec["extent"] is None
                                            or ext > rec["extent"]):
                        rec["extent"] = ext
                    key = (archive, area)
                    if key not in counts:
                        counts[key] = True
                    rec["places"].append(dict(
                        archive=archive, area=area,
                        area_name=area_stem(area) if area else None,
                        resourceIndex=int(res), chunk=chunk, kind=node.kind,
                        source=getattr(node, "source", None),
                        node=node.name))

                areas.append(dict(archive=archive, area=area,
                                  area_name=area_stem(area) if area else None,
                                  level=level.name, instances=instances,
                                  manifest=level.lvl_file))

        # Collapse each asset's places to one entry per area -- an asset
        # placed 40 times in one map is still one thing you can place there.
        for rec in assets.values():
            seen, collapsed = set(), []
            for place in rec["places"]:
                key = (place["archive"], place["area"], place["resourceIndex"])
                if key in seen:
                    continue
                seen.add(key)
                collapsed.append(place)
            rec["places"] = collapsed
            rec["areas"] = len({(p["archive"], p["area"])
                                for p in collapsed})
            rec["maps"] = len(rec["families"])
            rec["reach"] = reach_band(rec["maps"])
            rec["form"] = form_of(rec["kinds"])
            rec["size"] = size_band(rec["extent"])

        # The shadow flag is the one that cannot be read off a single name.
        # A trailing capital S is usually a plain variant letter (brchairS is
        # the nineteenth bar chair), so it is only reported as a shadow
        # variant when the matching ...NS name actually exists on the disc.
        # Measured: 456 names end NS, and 264 of the 341 ending S have that
        # twin -- 720 assets with real evidence, out of 797 candidates.
        known = set(assets)
        for name, rec in assets.items():
            flags, _ = strip_modifiers(name)
            flags = [f for f in flags if not f.startswith("_")]
            if re.search(r"_?NS$", name):
                flags.append("shadowvar")
            elif name.endswith("S") and (name[:-1] + "NS" in known
                                         or name[:-2] + "_NS" in known):
                flags.append("shadowvar")
            rec["flags"] = flags

        return dict(version=CACHE_VERSION, built=time.time(),
                    assets=assets, areas=areas)

    # ------------------------------------------------------------------ load

    def data(self, rebuild=False):
        with self.lock:
            if self._data is not None and not rebuild:
                return self._data
            if not rebuild and self.cache_path and \
                    os.path.exists(self.cache_path):
                try:
                    with open(self.cache_path, "r", encoding="utf-8") as fh:
                        cached = json.load(fh)
                    if cached.get("version") == CACHE_VERSION:
                        self._data = cached
                        return self._data
                except Exception:
                    pass
            self._data = self._scan()
            if self.cache_path:
                try:
                    os.makedirs(os.path.dirname(self.cache_path),
                                exist_ok=True)
                    with open(self.cache_path, "w", encoding="utf-8") as fh:
                        json.dump(self._data, fh)
                except Exception:
                    pass
            return self._data

    # --------------------------------------------------------------- queries

    def summary(self, **filters):
        """Headline numbers plus a count for every value of every facet.

        The counts are computed against whatever else is already filtered, so
        picking "collision only" immediately shows how many walls are left --
        a count that ignored the other filters would promise rows that are
        not there.
        """
        rows = self._match(**filters)
        data = self.data()
        facets = dict(category={}, form={}, reach={}, size={}, flag={})
        shared_areas = shared_maps = 0
        for rec in rows:
            for key in ("category", "form", "reach"):
                facets[key][rec[key]] = facets[key].get(rec[key], 0) + 1
            band = rec.get("size") or "unknown"
            facets["size"][band] = facets["size"].get(band, 0) + 1
            for flag in rec.get("flags", ()):
                facets["flag"][flag] = facets["flag"].get(flag, 0) + 1
            if rec["areas"] > 1:
                shared_areas += 1
            if rec.get("maps", 1) > 1:
                shared_maps += 1
        return dict(assets=len(rows), total=len(data["assets"]),
                    areas=len(data["areas"]),
                    instances=sum(r["instances"] for r in rows),
                    shared=shared_areas, shared_maps=shared_maps,
                    categories=facets["category"], facets=facets,
                    unclassified=facets["category"].get("unclassified", 0),
                    built=data["built"])

    def _match(self, query="", category=None, archive=None, area=None,
               shared_only=False, form=None, reach=None, size=None,
               flag=None, exclude=None):
        """Every record passing the filter. `exclude` skips one facet, which
        is how a facet list can show its own alternatives' counts."""
        data = self.data()
        q = (query or "").lower().strip()
        flags = _flag_list(flag)
        out = []
        for rec in data["assets"].values():
            if q and q not in rec["name"].lower():
                continue
            if category and exclude != "category" \
                    and rec["category"] != category:
                continue
            if form and exclude != "form" and rec.get("form") != form:
                continue
            if reach and exclude != "reach" and rec.get("reach") != reach:
                continue
            if size and exclude != "size":
                if (rec.get("size") or "unknown") != size:
                    continue
            if flags and exclude != "flag":
                if not all(f in rec.get("flags", ()) for f in flags):
                    continue
            if shared_only and rec.get("maps", rec["areas"]) < 2:
                continue
            if archive:
                places = [p for p in rec["places"]
                          if p["archive"] == archive
                          and (not area or p["area"] == area)]
                if not places:
                    continue
            out.append(rec)
        return out

    def flag_facet(self, **filters):
        """How many rows each naming-flag row would actually return.

        Flags stack, and that makes their counts a different question from the
        single-choice axes. Dropping the whole axis is right for "what it is",
        where picking a second value replaces the first -- but for flags it
        counts every row as though nothing were picked. Measured: with
        "Damage stage" on, the see-through row advertised 653 and returned
        NONE, because nothing on the disc is both cracked and alpha-blended.

        So each row is counted as the click it stands for -- a flag you have
        not picked counted with it ADDED, one you have picked with it REMOVED.
        A row then never promises rows it cannot produce, and since removing a
        flag can only widen the result, the flag you are standing in always
        keeps a non-zero count and so a way back out.
        """
        chosen = _flag_list(filters.get("flag"))
        rest = dict(filters)
        rest.pop("flag", None)
        rest.pop("exclude", None)
        out = {}
        for name in FLAGS:
            want = ([f for f in chosen if f != name] if name in chosen
                    else chosen + [name])
            n = len(self._match(flag=want, **rest))
            if n:
                out[name] = n
        return out

    def search(self, limit=400, **filters):
        """Assets matching a filter, most widely used first."""
        rows = self._match(**filters)
        out = [dict(name=r["name"], category=r["category"], why=r.get("why"),
                    form=r.get("form"), reach=r.get("reach"),
                    size=r.get("size"), extent=r.get("extent"),
                    flags=r.get("flags", []), maps=r.get("maps", r["areas"]),
                    instances=r["instances"], areas=r["areas"],
                    places=r["places"][:40])
               for r in rows]
        out.sort(key=lambda r: (-r["maps"], -r["areas"], -r["instances"],
                                r["name"]))
        return dict(total=len(out), assets=out[:limit])

    def asset(self, name):
        data = self.data()
        rec = data["assets"].get(name)
        if rec is None:
            return None
        return dict(rec, places=rec["places"])

    def placements_for(self, name, archive, area=None):
        """Can `name` be instanced into this area, and if not, where can it?

        The honest answer matters more than a convenient one: offering a
        placement that needs a file added to the archive would produce a
        disc that freezes on load.
        """
        rec = self.data()["assets"].get(name)
        if rec is None:
            return dict(name=name, found=False, here=None, elsewhere=[])
        here = None
        for place in rec["places"]:
            if place["archive"] != archive:
                continue
            if area and place["area"] != area:
                continue
            here = place
            break
        elsewhere = [p for p in rec["places"] if p is not here]
        return dict(name=name, found=True, category=rec["category"],
                    here=here, elsewhere=elsewhere,
                    placeable=here is not None)


def source_instance(level, name, resource_index=None):
    """An existing node in `level` to copy when placing `name`."""
    for node in level.nodes:
        if node.props.get("resourceIndex") is None:
            continue
        if asset_name(node.name) != name:
            continue
        if resource_index is not None and \
                int(node.props["resourceIndex"]) != resource_index:
            continue
        return node
    return None


def place_from_elsewhere(studio, archive, name, places, position, area=None, dry=False):
    """Place a model that only other levels have (see place)."""
    from . import modeladd as MA
    from . import collcreate as CC
    last = None
    for p in sorted(places, key=lambda q: (q.get("kind") != "zone", q["archive"])):
        try:
            data = studio.vanilla.archive(p["archive"]).read(p["area"])
            tris, mats, texs = MA.from_bundle_chunk(data, int(p["resourceIndex"]), xbox=False)
        except (MA.ModelError, KeyError, ValueError, OSError) as ex:
            last = ex
            continue
        if dry:
            return dict(applied=False, dry=True, asset=name, copied_from=p["archive"], triangles=len(tris))
        tris = MA.place(tris, [float(v) for v in (position or (0.0, 0.0, 0.0))])
        try:
            r = MA.add_to_level(studio, archive, tris, mats, texs, name, True, area=area, label="place " + name)
        except (MA.ModelError, CC.CollError) as ex:
            raise ValueError("%r could not be added to this level: %s" % (name, ex))
        fresh = studio.level(archive, reload=True, area=area)
        placed = next((n for n in fresh.nodes if n.name == r["name"]), None)
        return dict(applied=True, asset=name, new_name=r["name"], copied_from="%s %s" % (p["archive"], p["area"]),
                    uid=getattr(placed, "uid", None), position=list(position) if position else None,
                    triangles=r["triangles"], textures=r["textures"], collision=r["collision"], new_model=True,
                    target_file=", ".join(r.get("zone_files") or []),
                    note="copied in from %s with its textures (a new model in this level)" % p["archive"])
    raise ValueError("%r could not be read from the levels that have it (%s)" % (name, last))


def place(studio, index, archive, name, position, area=None, dry=False, source=None):
    """Instance an asset into a level at a world position.

    Works by copying an existing instance's block in its `.znes` file and
    then moving the copy -- the same slot-safe text edit as any other
    create, so no archive is ever repacked. The asset must already be in the
    target area's bundle; see `placements_for`.
    """
    where = index.placements_for(name, archive, area)
    if not where["found"]:
        raise ValueError("no asset named %r on the disc" % name)
    if source:
        # a particular map's copy (the Library's map list: each map's model and textures can differ) -- 2026-11-18
        rec = index.asset(name) or {}
        pick = next((p for p in rec.get("places", []) if p["archive"] == source.get("archive")
                     and (p["area"] or None) == (source.get("area") or None)
                     and int(p["resourceIndex"]) == int(source.get("resourceIndex", -1))), None)
        if pick is None:
            raise ValueError("%r is not in %s any more" % (name, source.get("archive")))
        if pick["archive"] != archive or (area and pick["area"] != area):
            return place_from_elsewhere(studio, archive, name, [pick], position, area, dry)
        where = dict(where, here=pick, placeable=True)
    if not where["placeable"]:
        # A model of another level: copied in from that level's bundle (the unedited disc's) with the textures it
        # draws with, written into this level's bundles (modeladd: every bundle the level has, PS2 and Xbox) with a
        # zone node and collision from its own triangles (2026-11-18; it used to stop here, "not solved yet").
        return place_from_elsewhere(studio, archive, name, where["elsewhere"], position, area, dry)

    level = studio.level(archive, area=area)
    node = source_instance(level, name, where["here"]["resourceIndex"])
    if node is None:
        raise ValueError("%r is catalogued in this area but no instance of it "
                         "remains to copy" % name)

    # Clone straight to the destination rather than cloning then moving.
    # Two writes would mean two chances to overrun the slot, and a placement
    # that landed but could not then be moved leaves the copy sitting
    # invisibly inside its original. One splice either fits or it does not.
    origin = [n.value for n in node.pos] if node.pos else [0.0, 0.0, 0.0]
    if position is None:
        offset = (1.0, 0.0, 1.0)
    else:
        offset = tuple(float(p) - o for p, o in zip(position, origin))

    # Try the asset's own file first so the level stays organised the way the
    # artists left it, and fall back to the roomiest file of the same kind
    # only when the slot cannot take the copy.
    targets = [node.source]
    roomiest = level.roomiest(like=node.source)
    if roomiest and roomiest != node.source:
        targets.append(roomiest)

    result = None
    for target in targets:
        edits, new_name = level.transplant(node.uid, target, offset)
        trial = level.apply(edits, dry=True)
        if all(r.get("fits") for r in trial.get("report", {}).values()):
            result = level.apply(
                edits, dry=dry,
                writer=studio.writer_for(archive, "place " + name))
            result["target_file"] = target
            break
        result = trial
        result["target_file"] = target

    result["new_name"] = new_name
    result["asset"] = name
    result["copied_from"] = node.name
    result["position"] = list(position) if position else None

    if not dry and not result.get("applied"):
        short = min((r for r in result.get("report", {}).values()
                     if not r.get("fits")),
                    key=lambda r: r.get("headroom", 0), default=None)
        raise ValueError(
            "no room in this level: the copy needs %d more byte(s) than any "
            "of its placement files can hold. Delete an unused object here "
            "first, or place into a level with more headroom."
            % abs(short.get("headroom", 0)) if short else
            "no room in this level for another object")

    if dry or not result.get("applied"):
        return result

    fresh = studio.level(archive, reload=True, area=area)
    placed = next((n for n in fresh.nodes if n.name == new_name), None)
    result["uid"] = getattr(placed, "uid", None)
    if placed is not None and placed.pos:
        result["placed_at"] = [round(n.value, 4) for n in placed.pos]
    return result
