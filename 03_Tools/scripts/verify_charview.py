"""[M8] A character model opens from its `.rsc`, textures and all.

The tester: *".rsc viewer for Character models (not .mod files)"*, and later
*"a .mod importer for characters is currently pointless as character models
are pulled specifically from .rsc and not their .mod files."*

Two claims, and this checks both rather than taking either on trust.

  1. THE LOOSE `.mod` IS NOT THE MODEL. Every character part also exists as a
     loose `<part>.mod` beside the bundle that contains it. They are not the
     same data -- and the sizes disagree in BOTH directions, so one is not a
     wrapper around the other. Viewing the loose file shows something the
     game does not draw. If this ever came back "identical", the milestone
     would be pointless and should be told so.
  2. THE BUNDLE IS A COMPLETE SOURCE. Geometry and texture binding both come
     out of the `.rsc`, and both are checked against the file's OWN claims
     rather than against our decoder agreeing with itself:
       * a part record states its vertex and index counts before any packet
         is decoded, so `stated == decoded` is a reference we did not write;
       * each part names a ONE-BASED chunk index for its texture, the same
         binding law `verify_binding.py` proves for levels -- which sweeps
         `LVLS_*` only, so characters were never covered by it.

What was actually missing before this milestone was the BINDING, not the
geometry: the character route already read the bundle, but for textures it
sent `textures=len(table)` -- a count. Nothing said which TEX0 belonged to
which part, so a character arrived with UVs and nothing to put on them.

Everything here reads `01_Vanilla` and writes nothing.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdrstudio import Game, charview, rsc                # noqa: E402
from rdrstudio.config import VANILLA_ISO                 # noqa: E402
from rdrstudio.studio import character_parts             # noqa: E402

ARCHIVE = "SWAT.DAT"
SUBJECT = "aka"
MOD_PAIRS = 120          # loose-.mod comparisons before we have made the point

ok = True


def check(what, got, want, cmp="=="):
    global ok
    good = (got == want if cmp == "==" else
            got >= want if cmp == ">=" else got <= want)
    ok &= bool(good)
    print("  %-58s %-12s %s"
          % (what, str(got)[:12],
             "ok" if good else "FAIL (want %s %s)" % (cmp, want)))


def main():
    game = Game(VANILLA_ISO)
    dv = game.archive(ARCHIVE)
    names = set(e.name for e in game.toc(ARCHIVE))
    stems = sorted({n[:-9] for n in names if n.endswith("_lods.rsc")})
    print("%s: %d character bundles" % (ARCHIVE, len(stems)))
    check("there are characters to open", len(stems) >= 100, True, ">=")

    # ---------------------------------------------------------------- 1
    print("\n1. the loose .mod is not what the game draws:")
    pairs = same = differ = bigger = smaller = 0
    for stem in stems:
        if pairs >= MOD_PAIRS:
            break
        try:
            b = charview.bundle(dv, stem)
        except Exception:
            continue
        rnt = charview.part_names(dv, stem)
        for c in b.chunks:
            if c.tag != "MOD0" or pairs >= MOD_PAIRS:
                continue
            nm = rnt.get(c.index)
            if not nm or (nm + ".mod") not in names:
                continue
            try:
                inner, loose = b.raw(c.index), dv.read(nm + ".mod")
            except Exception:
                continue
            pairs += 1
            if inner == loose:
                same += 1
            else:
                differ += 1
                bigger += len(loose) > len(inner)
                smaller += len(loose) < len(inner)
    print("    %d parts compared against their loose .mod" % pairs)
    print("      loose file bigger: %d    smaller: %d" % (bigger, smaller))
    check("there were pairs to compare", pairs >= 50, True, ">=")
    check("none of them is byte-identical to the bundle's chunk", same, 0)
    check("and they differ in BOTH directions, so neither wraps the other",
          bigger > 0 and smaller > 0, True)

    # ---------------------------------------------------------------- 2
    print("\n2. geometry matches what the file itself states:")
    bundles = chunks = matched = mismatched = undecoded = 0
    for stem in stems:
        try:
            b = charview.bundle(dv, stem)
        except Exception:
            continue
        bundles += 1
        for chunk, _n in character_parts(b, dv, stem, 0):
            recs = b.parts(chunk)
            if not recs:
                continue
            chunks += 1
            m = b.model(chunk)
            if not m:
                undecoded += 1
                continue
            stated = sum(r.get("verts", 0) for r in recs)
            if stated == len(m["verts"]):
                matched += 1
            else:
                mismatched += 1
    print("    %d bundles, %d model chunks" % (bundles, chunks))
    check("every bundle opened", bundles, len(stems))
    check("every chunk decoded", undecoded, 0)
    check("decoded vertex total == the file's stated total", mismatched, 0)
    check("and that was checked on real models", matched >= 400, True, ">=")

    # ---------------------------------------------------------------- 3
    print("\n3. every part's texture binding resolves, disc-wide:")
    refs = bound = zero = faults = 0
    sized = 0
    for stem in stems:
        try:
            b = charview.bundle(dv, stem)
        except Exception:
            continue
        picks = character_parts(b, dv, stem, 0)
        faults += len(charview.binding_faults(b, picks))
        for s in charview.surfaces(b, picks, charview.part_names(dv, stem)):
            refs += 1
            if not s["textured"]:
                zero += 1
                continue
            bound += 1
            t = s["texture"]
            if t and t["width"] > 0 and t["height"] > 0:
                sized += 1
    print("    %d part->resource references: %d bound, %d untextured"
          % (refs, bound, zero))
    check("no reference points at a non-TEX0 chunk", faults, 0)
    check("every bound texture names real dimensions", sized, bound)
    check("and most parts really are textured", bound >= refs // 2, True)

    # ---------------------------------------------------------------- 3b
    # REQ-029's regression guard. `texture_info` used to require 0xB0 bytes
    # -- the space the NAME needs -- and so returned None for a shorter TEX0
    # whose dimensions were sitting at 0x88. `texture_table` drops whatever
    # it rejects, so those textures were invisible and 13 character surfaces
    # bound to one. The failure mode is silence, not an error, which is why
    # it needs an assertion of its own rather than trusting the count above.
    print("\n3b. the short TEX0 form is admitted (REQ-029):")
    short = short_sized = 0
    for stem in stems:
        try:
            b = charview.bundle(dv, stem)
        except Exception:
            continue
        for c in b.chunks:
            if c.tag != "TEX0" or not (0x8C <= c.size < 0xB0):
                continue
            short += 1
            info = b.texture_info(c.index)
            if info and info["width"] > 0 and info["height"] > 0:
                short_sized += 1
    print("    %d TEX0 chunks are the short form (size and no name)" % short)
    check("there are short TEX0s to admit", short >= 10, True, ">=")
    check("every one reports real dimensions", short_sized, short)

    # ---------------------------------------------------------------- 4
    print("\n4. one character, opened as the game opens it:")
    c = charview.open_character(dv, SUBJECT, 0, picker=character_parts)
    for s in c["surfaces"]:
        t = s["texture"]
        print("    %-22s stated %5d verts / %5d idx  %s"
              % (s["name"][:22], s["stated_verts"], s["stated_indices"],
                 ("-> %s %dx%d" % (t["name"], t["width"], t["height"]))
                 if t else "-> untextured"))
    check("it came from the bundle, not a .mod",
          c["source"], SUBJECT + "_lods.rsc")
    check("it has surfaces", c["parts"] >= 2, True, ">=")
    check("every surface is bound to a texture", c["untextured"], 0)
    check("each surface names the part it is",
          all(s["name"] and not s["name"].startswith("chunk ")
              for s in c["surfaces"]), True)
    check("the body part is there",
          any("lod_01" in s["name"] for s in c["surfaces"]), True)

    # ---------------------------------------------------------------- 5
    print("\n5. a character with no bundle is refused, not silently "
          "substituted:")
    try:
        charview.bundle(dv, "definitely_not_a_character")
        got = "no refusal"
    except charview.NotACharacter as ex:
        got = "refused"
        print("      %s" % ex)
    check("  a missing bundle raises rather than falling back", got,
          "refused")

    print("\n%s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
