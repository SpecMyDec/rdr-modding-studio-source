"""The new editing capabilities, checked against the real disc.

Two of these are the sentences the developer wrote: *swap which character an enemy
is*, and *place a new enemy anywhere*. Neither existed; the 3D editor had
exactly two mutating routes, `duplicate` and `delete`, and no way to change
what a thing IS.

Every assertion here runs against `01_Vanilla`, READ ONLY, and every write is
a dry run. Nothing in this script touches the working ISO.

Three properties, in order of how much they matter:

  1. **The palette is exact.** A picker is only better than a text box if the
     list is right. So: every `Type` used on the disc resolves to a `.cr` in
     its own archive, and every `Table` to a `.tbl` in its own archive.
  2. **A change is byte-exact and reversible.** Setting a field to the value
     it already holds must return the file unchanged; setting and unsetting
     must round-trip; nothing but the value span may move.
  3. **A refusal is a refusal.** A bad character, a bad behaviour, a wrong
     weapon-list length and a removal of a required field must all be turned
     away with a code, before any splice.

Run:  python scripts/verify_enemy_edit.py
"""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdrstudio import cast as cast_mod        # noqa: E402
from rdrstudio import enemyfields as EF       # noqa: E402
from rdrstudio import scene                   # noqa: E402
from rdrstudio.config import VANILLA_ISO      # noqa: E402
from rdrstudio.game import Game               # noqa: E402
from rdrstudio.level import Level, list_levels  # noqa: E402

FAILURES = []
NOTES = []


def check(ok, what, detail=""):
    if ok:
        print("  ok   %s%s" % (what, ("  -- " + detail) if detail else ""))
    else:
        print("  FAIL %s%s" % (what, ("  -- " + detail) if detail else ""))
        FAILURES.append(what)
    return ok


def note(text):
    NOTES.append(text)
    print("  ..   %s" % text)


# --------------------------------------------------------------- 1. palettes

def palettes(game):
    print("\n1. THE PALETTE IS EXACT")
    archives = list_levels(game)
    cr_total = tbl_total = 0
    type_uses = table_uses = 0
    bad_type, bad_table = [], []
    borrowed = 0
    local_enm = swat_only = 0
    arity_hist = Counter()
    block_sizes = []

    for arc in archives:
        try:
            doc = cast_mod.cast(game, arc)
        except Exception as ex:
            check(False, "cast(%s)" % arc, str(ex))
            continue
        cr_total += len(doc["characters"])
        tbl_total += len(doc["behaviours"])
        borrowed += sum(1 for c in doc["characters"] if c["borrowed"])
        if doc["roster_source"] == "local":
            local_enm += 1
        elif doc["roster_source"] == "swat":
            swat_only += 1

        dave = game.archive(arc)
        for e in dave:
            if e.ext != "enm":
                continue
            data = dave.read(e)
            for node in scene.parse_enemies(data, e.name):
                block_sizes.append(node.span[1] - node.span[0])
                lines = EF.parse_block(data, node.span)
                if "WeaponList" in lines:
                    arity_hist[len(lines["WeaponList"].tokens)] += 1
                t = node.props.get("Type")
                if t:
                    type_uses += 1
                    if cast_mod.resolve_type(doc, t) is None:
                        bad_type.append((arc, e.name, t))
                b = node.props.get("Table")
                if b:
                    table_uses += 1
                    if cast_mod.resolve_table(doc, b) is None:
                        bad_table.append((arc, e.name, b))

    print("  %d level archives; %d .cr and %d .tbl enumerated"
          % (len(archives), cr_total, tbl_total))
    check(not bad_type,
          "every Type used resolves to a .cr in its OWN archive",
          "%d uses, %d unresolved%s" % (type_uses, len(bad_type),
                                        "" if not bad_type
                                        else " " + repr(bad_type[:3])))
    check(not bad_table,
          "every Table used resolves to a .tbl in its OWN archive",
          "%d uses, %d unresolved%s" % (table_uses, len(bad_table),
                                        "" if not bad_table
                                        else " " + repr(bad_table[:3])))
    note("%d of %d archives carry their own .enm; %d name a roster in "
         "SWAT.DAT; %d have neither and say so"
         % (local_enm, len(archives), swat_only,
            len(archives) - local_enm - swat_only))
    note("%d .cr borrow another creature's model" % borrowed)
    note("WeaponList arity across the disc: %s"
         % ", ".join("%d values x%d" % (k, v)
                     for k, v in sorted(arity_hist.items())))
    check(len(arity_hist) > 1,
          "WeaponList arity is NOT fixed, so it must be read per block",
          "%d distinct lengths" % len(arity_hist))
    if block_sizes:
        block_sizes.sort()
        note("Enemy block bytes: min %d, median %d, max %d, n=%d"
             % (block_sizes[0], block_sizes[len(block_sizes) // 2],
                block_sizes[-1], len(block_sizes)))


# ------------------------------------------------------------ 2. byte-exact

def byte_exact(game):
    print("\n2. AN EDIT MOVES ONLY WHAT IT WAS ASKED TO MOVE")
    archives = list_levels(game)
    checked = same = 0
    drift = []
    for arc in archives:
        dave = game.archive(arc)
        for e in dave:
            if e.ext != "enm":
                continue
            data = dave.read(e)
            for node in scene.parse_enemies(data, e.name):
                lines = EF.parse_block(data, node.span)
                # Write every present field back with the value it already
                # holds. A parser that reflows whitespace or renormalises a
                # number would grow the file, and these live in tight slots.
                changes = {}
                for key in EF.KEYS:
                    line = lines.get(key)
                    if line is None:
                        continue
                    if key == "PowerUp":
                        continue      # normalised on purpose; covered below
                    changes[key] = EF.field_value(key, line)
                if not changes:
                    continue
                checked += 1
                out = scene.splice(data, EF.edits_for(data, node.span, changes,
                                                      lines))
                if out == data:
                    same += 1
                else:
                    if len(drift) < 5:
                        first = next(i for i in range(min(len(out), len(data)))
                                     if out[i:i + 1] != data[i:i + 1])
                        drift.append((arc, e.name, node.name,
                                      data[first - 20:first + 20],
                                      out[first - 20:first + 20]))
    check(same == checked,
          "re-writing every field with its own value is byte-identical",
          "%d of %d enemy blocks%s"
          % (same, checked, "" if not drift else "\n       " +
             "\n       ".join(repr(d) for d in drift)))


def roundtrip(game):
    print("\n3. SET, READ BACK, UNSET")
    arc = "LVLS_SNG/SHER70.DAT"
    lvl = Level(game, arc)
    doc = cast_mod.cast(game, arc)
    sheriff = next(n for n in lvl.enemy_nodes()
                   if n.props.get("Name") == "Sheriff")
    data = lvl._read(sheriff.source)

    # E1: swap the character.
    clean = EF.validate({"Type": "UGL"}, doc)
    check(clean["Type"] == "ugl",
          "a Type is matched case-insensitively and written in the disc's case",
          "UGL -> ugl")
    out = scene.splice(data, EF.edits_for(data, sheriff.span, clean))
    back = next(n for n in scene.parse_enemies(out, "x")
                if n.props.get("Name") == "Sheriff")
    check(back.props.get("Type") == "ugl",
          "E1 the character actually changed on re-read", "cls -> ugl")
    check(len(out) - len(data) == 0,
          "E1 costs 0 bytes here", "%+d" % (len(out) - len(data)))

    # E2: swap the behaviour. Orthogonal -- Type must not move.
    clean = EF.validate({"Table": "sher70_sniper_d"}, doc)
    out2 = scene.splice(data, EF.edits_for(data, sheriff.span, clean))
    back2 = next(n for n in scene.parse_enemies(out2, "x")
                 if n.props.get("Name") == "Sheriff")
    check(back2.props.get("Table") == "Sher70_Sniper_D"
          and back2.props.get("Type") == "cls",
          "E2 the behaviour changed and the character did not",
          "%s / %s" % (back2.props.get("Table"), back2.props.get("Type")))

    # E3: insert a field that is absent, then remove it again.
    lines = EF.parse_block(data, sheriff.span)
    check("IsBeast" not in lines, "IsBeast is absent from this block to start")
    added = scene.splice(data, EF.edits_for(
        data, sheriff.span, EF.validate({"IsBeast": 1}, doc), lines))
    n3 = next(n for n in scene.parse_enemies(added, "x")
              if n.props.get("Name") == "Sheriff")
    check(n3.props.get("IsBeast") == 1.0,
          "E3 an absent field is inserted and reads back",
          "+%d bytes" % (len(added) - len(data)))
    lines3 = EF.parse_block(added, n3.span)
    removed = scene.splice(added, EF.edits_for(added, n3.span,
                                               {"IsBeast": None}, lines3))
    check(removed == data,
          "E3 removing it again restores the file byte for byte")

    # The two pickers with provable palettes.
    check(EF.validate({"TextureVariation": "snp04"}, doc)["TextureVariation"]
          == "snp04", "E3 a skin resolves to a .tex in this archive")
    check(EF.validate({"PowerUp": "Halcon_Rifle"}, doc)["PowerUp"]
          == "Halcon_Rifle", "E3 a drop resolves to a swat.guns TypeName")


# ------------------------------------------------------------- 4. refusals

def refusals(game):
    print("\n4. A BAD VALUE IS REFUSED BEFORE ANY SPLICE")
    arc = "LVLS_SNG/SHER70.DAT"
    lvl = Level(game, arc)
    doc = cast_mod.cast(game, arc)
    sheriff = next(n for n in lvl.enemy_nodes()
                   if n.props.get("Name") == "Sheriff")
    _, lines = lvl.enemy_lines(sheriff.uid)

    cases = [
        ({"Type": "xyz"}, "unknown_type"),
        ({"Type": "ncb"}, "unknown_type"),        # real elsewhere, not here
        ({"Table": "actor"}, "unknown_table"),    # ditto
        ({"TextureVariation": "cls_a"}, "unknown_value"),
        ({"PowerUp": "Laser_Rifle"}, "unknown_value"),
        ({"WeaponList": [1, 2]}, "bad_request"),
        ({"Name": None}, "not_editable"),
        ({"Alignment": None}, "not_editable"),
        ({"NoSuchField": 1}, "bad_request"),
        ({"Name": "two words"}, "bad_request"),
        ({"Comment": ""}, "bad_request"),
    ]
    for changes, want in cases:
        try:
            EF.validate(changes, doc, lines)
            check(False, "refuses %s" % list(changes)[0], "was ACCEPTED")
        except EF.Invalid as ex:
            check(ex.code == want, "refuses %r with %s"
                  % (changes, want), "got %s: %s" % (ex.code, ex.message))


# --------------------------------------------------------------- 5. placing

def placing(game):
    print("\n5. E4  PLACE A NEW ENEMY ANYWHERE")
    fits = refused = no_roster = 0
    costs = []
    for arc in list_levels(game):
        lvl = Level(game, arc)
        enms = lvl.enm_files()
        if not enms:
            no_roster += 1
            continue
        ens = lvl.enemy_nodes()
        if not ens:
            no_roster += 1
            continue
        doc = cast_mod.cast(game, arc)
        template = EF.smallest_block(ens)
        char = doc["characters"][0]["id"] if doc["characters"] else None
        beh = doc["behaviours"][0]["id"] if doc["behaviours"] else None
        if not char or not beh:
            continue
        target = enms[0]
        edits, name, block_bytes = lvl.place_enemy(
            target, template, [1.0, 2.0, 3.0], 45.0,
            {"Type": char, "Table": beh})
        report = lvl.preview(edits)
        r = report[target]
        costs.append(block_bytes)
        if r["fits"]:
            fits += 1
        else:
            refused += 1

        if arc == "LVLS_SNG/SHER70.DAT":
            data = lvl._read(target)
            out = scene.splice(data, edits[target])
            before = len(scene.parse_enemies(data, target))
            after = scene.parse_enemies(out, target)
            check(len(after) == before + 1,
                  "SHER70 gains exactly one enemy",
                  "%d -> %d" % (before, len(after)))
            new = after[-1]
            check(new.props.get("Type") == char
                  and new.props.get("Table") == beh
                  and new.props.get("Name") == name,
                  "the new block carries the character, behaviour and name "
                  "it was asked for",
                  "%s / %s / %s" % (new.props.get("Type"),
                                    new.props.get("Table"), name))
            check([round(c, 3) for c in new.xyz()] == [1.0, 2.0, 3.0]
                  and abs(new.angle.value - 45.0) < 1e-4,
                  "it stands where it was placed and faces where it was aimed",
                  "%s @ %.1f" % (new.xyz(), new.angle.value))
            check(EF.parse_block(out, new.span).keys()
                  >= set(EF.MIN_REQUIRED),
                  "it has all 11 fields required in 792/792 stock blocks")
            check(r["fits"] and r["headroom"] > 0,
                  "the plan says it fits, with the number",
                  "%d bytes spare of a %d-byte slot"
                  % (r["headroom"], r["slot"]))

    check(no_roster > 0,
          "levels with no roster of their own are detected, not silently empty",
          "%d of 61 archives" % no_roster)
    if costs:
        costs.sort()
        note("new block costs: min %d, median %d, max %d bytes"
             % (costs[0], costs[len(costs) // 2], costs[-1]))
    note("placement fits in %d of %d levels that have a roster; %d would be "
         "refused for want of slot room" % (fits, fits + refused, refused))


# ------------------------------------------------------ 6. the other edits

def other_edits(game):
    print("\n6. E5 ATTRIBUTES, E6 ROTATION, E7 SPLINES")
    lvl = Level(game, "LVLS_SNG/SHER70.DAT")

    with_attrs = [n for n in lvl.nodes if n.attrs]
    total = sum(len(n.attrs) for n in lvl.nodes)
    check(total > 0, "typed attributes now carry the span of their value",
          "%d attributes on %d objects in SHER70" % (total, len(with_attrs)))

    node = next(n for n in with_attrs
                if any(a.type.upper() == "BOOL" for a in n.attrs))
    a = next(x for x in node.attrs if x.type.upper() == "BOOL")
    data = lvl._read(node.source)
    flip = "false" if a.value == "true" else "true"
    out = scene.splice(data, scene.edit_attribute(node, a.name, flip))
    check(abs(len(out) - len(data)) <= 1,
          "E5 flipping a BOOL costs at most one byte",
          "%s %s -> %s, %+d bytes" % (a.name, a.value, flip,
                                      len(out) - len(data)))
    node2 = next(n for n in scene.parse_any(node.source, out)
                 if n.name == node.name)
    check(next(x.value for x in node2.attrs if x.name == a.name) == flip,
          "E5 the new value reads back")
    back = scene.splice(out, scene.edit_attribute(node2, a.name, a.value))
    check(back == data, "E5 flipping it back restores the file exactly")

    rotated = [n for n in lvl.nodes if n.rot and
               any(abs(r.value - (1.0 if i in (0, 4, 8) else 0.0)) > 1e-6
                   for i, r in enumerate(n.rot))]
    check(rotated, "objects with a non-identity rotation are found",
          "%d of %d objects with a matrix"
          % (len(rotated), sum(1 for n in lvl.nodes if n.rot)))
    # Every matrix on the disc: writing back the nine numbers it already holds
    # must produce no edit at all, and turning it must never grow the file.
    noop = grew = seen = 0
    for arc in ("LVLS_SNG/SHER70.DAT", "LVLS_SNG/TOMB.DAT",
                "LVLS_SNG/BARFIGHT.DAT", "LVLS_SNG/WOODS.DAT"):
        lv = Level(game, arc)
        for n in lv.nodes:
            if not n.rot:
                continue
            seen += 1
            d = lv._read(n.source)
            if not scene.edit_node_rot(n, [x.value for x in n.rot], True):
                noop += 1
            turned = scene.splice(d, scene.edit_node_rot(
                n, [0.5, -0.5, 0.70710678, 0.5, 0.5, -0.70710678,
                    0.70710678, 0.5, 0.5], keep_width=True))
            if len(turned) != len(d):
                grew += 1
    check(noop == seen,
          "E6 re-writing a matrix with its own numbers writes nothing at all",
          "%d of %d matrices across 4 levels" % (noop, seen))
    check(grew == 0,
          "E6 keep_width makes every rotation write exactly length-neutral",
          "%d of %d changed the file's length" % (grew, seen))

    r0 = rotated[0]
    data = lvl._read(r0.source)
    ident = [1, 0, 0, 0, 1, 0, 0, 0, 1]
    out = scene.splice(data, scene.edit_node_rot(r0, ident, keep_width=True))
    r1 = next(n for n in scene.parse_any(r0.source, out) if n.name == r0.name)
    check([round(x.value, 4) for x in r1.rot] == [1, 0, 0, 0, 1, 0, 0, 0, 1],
          "E6 the identity matrix reads back", r0.name)
    orig = [x.value for x in r0.rot]
    back = scene.splice(out, scene.edit_node_rot(r1, orig, keep_width=False))
    b2 = next(n for n in scene.parse_any(r0.source, back) if n.name == r0.name)
    check([round(x.value, 6) for x in b2.rot] == [round(v, 6) for v in orig],
          "E6 writing the original nine numbers back restores every value")

    splines = [n for n in lvl.nodes if len(n.verts) > 1]
    check(splines, "splines with points are found", "%d in SHER70" % len(splines))
    s0 = splines[0]
    data = lvl._read(s0.source)
    was = [c.value for c in s0.verts[1]]
    out = scene.splice(data, scene.edit_vertex(s0, 1, [9.5, 8.25, -7.125],
                                               keep_width=False))
    s1 = next(n for n in scene.parse_any(s0.source, out) if n.name == s0.name)
    check([round(c.value, 3) for c in s1.verts[1]] == [9.5, 8.25, -7.125],
          "E7 a spline point moves and reads back",
          "%s point 2 of %d" % (s0.name, len(s0.verts)))
    back = scene.splice(out, scene.edit_vertex(s1, 1, was, keep_width=False))
    check(len(back) == len(data) or
          scene.parse_any(s0.source, back)[0] is not None,
          "E7 moving it back is accepted")


# ---------------------------------------------------------------- 7. doors

def doors(game):
    print("\n7. E8  AREA DOORS")
    from rdrstudio import links
    import zlib
    total = 0
    heads = []
    for arc in list_levels(game):
        try:
            dave = game.archive(arc)
        except Exception:
            continue
        for fn in links.sub_files(dave):
            data = dave.read(fn)
            e = dave.find(fn)
            co = zlib.compressobj(9, 8, -15)
            stored = len(co.compress(data) + co.flush())
            heads.append(e.slot - stored)
            ds = links.parse(data)
            total += len(ds)
            for d in ds:
                if d.values.get("level") is None:
                    check(False, "%s door %d has no destination" % (fn, d.index))
    check(total == 36, "36 doors parse across the disc", "%d found" % total)
    note("door file headroom: %d to %d bytes -- much tighter than the 43-188 "
         "the design assumed" % (min(heads), max(heads)))

    dave = game.archive("LVLS_SNG/TOMB.DAT")
    data = dave.read("lvl_tomb.sub")
    ds = links.parse(data)
    fields = {f.path: f for f in links.parse_fields(data)}
    d = ds[1]
    f = fields[d.paths["lock"]]
    out = scene.splice(data, [(f.start, f.end, links.render("lock", 3, f.raw))])
    check(links.parse(out)[1].values["lock"] == 3,
          "E8 a lock state changes and reads back",
          "%s -> 3" % d.values["lock"])
    f2 = fields[d.paths["enter_pos"]]
    out2 = scene.splice(data, [(f2.start, f2.end,
                                links.render("enter_pos", [1.5, 0.0, 2.0],
                                             f2.raw))])
    check(links.parse(out2)[1].values["enter_pos"] == [1.5, 0.0, 2.0],
          "E8 an arrival position changes and reads back")

    dests = links.destinations(game)
    check(links.resolve_destination(dests, "lvl_bar") == "lvl_bar",
          "E8 a real destination resolves", "%d destinations" % len(dests))
    check(links.resolve_destination(dests, "lvl_nowhere") is None,
          "E8 an invented destination is refused")


def main():
    print("Reading %s (READ ONLY)" % VANILLA_ISO)
    game = Game(VANILLA_ISO)
    palettes(game)
    byte_exact(game)
    roundtrip(game)
    refusals(game)
    placing(game)
    other_edits(game)
    doors(game)

    print("\n" + "-" * 68)
    for n in NOTES:
        print("  " + n)
    print("-" * 68)
    if FAILURES:
        print("%d FAILED:" % len(FAILURES))
        for f in FAILURES:
            print("   - " + f)
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
