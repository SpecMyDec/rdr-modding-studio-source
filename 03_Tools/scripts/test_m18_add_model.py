#!/usr/bin/env python3
"""[M18] a new model lands in a character `.rsc` and the bindings still verify.

    python3 scripts/test_m18_add_model.py

M18's acceptance criterion is *"adds a model to a character `.rsc`, asserts
the resource index and the model->texture binding still verify"*. Both halves
are asserted here, on real bundles off the disc.

**What "a new model" means here, stated plainly because it bounds the claim.**
There is no VIF writer in this codebase -- `geomedit.py` is built specifically
to avoid needing one -- so this does not synthesise a display list from an OBJ.
It copies a known-good MOD0 from another bundle, which is what can be verified
structurally; whether an invented packet renders is an emulator question, not
a byte question. The destination genuinely gains a model it did not have.

The binding half is the part that can silently go wrong. A container MOD0's
per-surface texture reference is a 1-based chunk index into the bundle it came
from. Copied across unchanged it still resolves -- to whatever chunk sits at
that index in the destination -- and draws the wrong texture rather than
failing. So the test asserts the references were repointed, that they land on
TEX0 chunks that are byte-identical to the originals, and that every model
that was already there kept the binding it had.

Reads `01_Vanilla/RDR_VANILLA.iso`. Writes nothing, to it or anywhere else.
"""
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from rdrstudio import dave                         # noqa: E402
from rdrstudio.iso9660 import Iso9660              # noqa: E402
from rdrstudio.rsc import (MAGIC, Rsc, add_model,  # noqa: E402
                           append_chunk, check_bundle)

ISO = os.path.join(os.path.dirname(__file__), '..', '..',
                   '01_Vanilla', 'RDR_VANILLA.iso')

FAILURES = []
WANT = 24         # character bundles to gather before choosing a pair

# A CHARACTER bundle is one with a paired `<stem>_lods.rnt` manifest -- that is
# how `charview.py` and `studio.py` find characters, so it is the codebase's
# own definition rather than a new one invented here. Bone count alone is NOT
# enough: level bundles carry rigged props (`lvl_ranch2` has a 3-bone one), and
# two earlier versions of this test passed while copying between level bundles
# because "has a bone count" admitted them. 306 `*_lods.rsc` exist on the disc
# and 140 of them share a 52-bone rig, so there is no reason to settle for one.
CHARACTER_SUFFIX = "_lods.rsc"


def check(ok, what, detail=""):
    # detail explains a FAILURE; printing it on success made the pass line
    # read "ok found 2 bundles -- nothing to test against", which is nonsense.
    print(("  ok   " if ok else "  FAIL ") + what
          + (("  -- " + detail) if detail and not ok else ""))
    if not ok:
        FAILURES.append(what)
    return ok


def textured_models(r, rigged_only=False):
    """MOD0 indices in `r` whose parts reference at least one TEX0.

    `rigged_only` keeps only models carrying a bone count, which is what makes
    a bundle a CHARACTER bundle rather than a level one: byte 1 of a container
    MOD0 is the bone count of its skeleton, and level containers carry 0.
    """
    out = []
    for c in r.chunks:
        if c.tag != "MOD0":
            continue
        parts = r.parts(c.index)
        if not parts:
            continue
        refs = [p.get("resource") for p in parts if p.get("resource")]
        if rigged_only and not r.bone_count(c.index):
            continue
        if refs and all(1 <= v <= len(r.chunks)
                        and r.chunks[v - 1].tag == "TEX0" for v in refs):
            out.append(c.index)
    return out


def find_bundles(iso):
    """Up to WANT character bundles: (name, bytes, [(model index, bones)]).

    CHARACTER bundles specifically -- M18's criterion says a character `.rsc`.
    The first version of this test took the first two bundles it met
    (`lvl_bridge2`, `lvl_front`: level containers, bone count 0). The second
    required a bone count and got `lvl_ranch4` -> `lvl_ranch2`, a rigged prop
    in a level. Both passed, and neither tested what the milestone asks.
    """
    found, seen = [], set()
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b'Dave', b'DAVE'):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            if (not e.name.lower().endswith(CHARACTER_SUFFIX)
                    or e.name in seen):
                continue
            raw = d.backing.read_at(e.offset, e.size_comp)
            try:
                b = zlib.decompress(raw, -15) if e.compressed else raw
            except zlib.error:
                continue
            if b[:4] != MAGIC or struct.unpack_from("<I", b, 4)[0] == 0:
                continue
            try:
                r = Rsc(b, label=e.name)
            except ValueError:
                continue
            models = [(i, r.bone_count(i))
                      for i in textured_models(r, rigged_only=True)]
            if models:
                seen.add(e.name)
                found.append((e.name, b, models))
                if len(found) >= WANT:
                    return found
    return found


def main():
    iso = Iso9660(ISO)
    try:
        found = find_bundles(iso)
    finally:
        iso.close()

    by_bones = {}
    for name, b, models in found:
        for idx, bones in models:
            by_bones.setdefault(bones, []).append((name, b, idx))

    # The success path needs two DIFFERENT character bundles rigged to the
    # same bone count, or add_model refuses -- correctly -- because a mesh
    # carries bone indices into a skeleton that lives outside the bundle.
    pair = None
    for bones, rows in sorted(by_bones.items(),
                              key=lambda kv: -len({r[0] for r in kv[1]})):
        names = {r[0] for r in rows}
        if len(names) >= 2:
            first = rows[0]
            other = next(r for r in rows if r[0] != first[0])
            pair = (bones, other, first)
            break
    if not check(pair is not None,
                 "found two character (*%s) bundles sharing a bone count"
                 % CHARACTER_SUFFIX,
                 "character bundles seen: %s"
                 % sorted({(n, bn) for bn, rs in by_bones.items()
                           for n, _, _ in rs})):
        return 1

    bones, (sname, sbytes, src_index), (dname, dbytes, _) = pair
    print("  source %s chunk %d (%d bones) -> destination %s"
          % (sname, src_index, bones, dname))

    pre = Rsc(dbytes, label=dname)
    pre_count = len(pre.chunks)
    pre_map = pre.tex_slot_map()
    pre_models = {i: pre.parts(i) for i in textured_models(pre)}

    out, report = add_model(dbytes, sbytes, src_index, label=dname)

    # --- structure -------------------------------------------------------
    check(not check_bundle(out, dname), "the result verifies",
          "; ".join(check_bundle(out, dname)))
    post = Rsc(out, label=dname)

    # --- the resource index half ----------------------------------------
    check(all(post.raw(i) == pre.raw(i) for i in range(pre_count)),
          "every chunk that was already there is byte-identical")
    check(report["model"] == len(post.chunks) - 1,
          "the new model is the last chunk")
    check(report["resource_index"] == report["model"] + 1,
          "its resourceIndex is 1-based (index + 1)")
    check(all(pre.chunks[i].tag == post.chunks[i].tag
              for i in range(pre_count)),
          "no existing chunk changed index or tag")

    # --- the model->texture binding half --------------------------------
    sr = Rsc(sbytes, label=sname)
    src_refs = [p["resource"] for p in sr.parts(src_index) if p["resource"]]
    new_parts = post.parts(report["model"])
    check(new_parts is not None, "the copied model still reads as a container")
    new_refs = [p["resource"] for p in (new_parts or []) if p["resource"]]

    check(len(new_refs) == len(src_refs),
          "the copied model kept all %d textured surfaces" % len(src_refs))
    check(all(v != o for v, o in zip(new_refs, src_refs)),
          "every reference was REPOINTED, not carried over unchanged",
          "unrepointed refs would resolve to the wrong chunk and draw wrong")
    check(all(1 <= v <= len(post.chunks)
              and post.chunks[v - 1].tag == "TEX0" for v in new_refs),
          "every repointed reference lands on a TEX0 in the destination")

    # the copied textures are the same bytes as the originals
    same = True
    for v, o in zip(new_refs, src_refs):
        dst_tex = bytearray(post.raw(v - 1))
        src_tex = bytearray(sr.raw(o - 1))
        # 0x90 holds this TEX0's own palette/pixel indices and is repointed
        struct.pack_into("<II", dst_tex, 0x90, 0, 0)
        struct.pack_into("<II", src_tex, 0x90, 0, 0)
        same = same and bytes(dst_tex) == bytes(src_tex)
    check(same, "each copied TEX0 is byte-identical apart from its 0x90 pair")

    # every copied TEX0's own palette/pixel pair resolves in the destination
    ok = True
    for v in new_refs:
        pair = post.tex_sources(v - 1)
        ok = ok and pair is not None and pair[0].tag == "PAL0" \
            and pair[1].tag == "MIP0"
    check(ok, "each copied TEX0's palette and pixels resolve in the destination")

    # nothing that was already bound moved
    post_map = post.tex_slot_map()
    check(all(post_map.get(k) == v for k, v in pre_map.items()),
          "every pre-existing texture slot still maps where it did")
    check(all(post.parts(i) == p for i, p in pre_models.items()),
          "every pre-existing model's parts are unchanged")

    # --- the skeleton guard ---------------------------------------------
    check(report["bones"] == bones,
          "the copy reports the source's %d-bone rig" % bones)
    mismatch = None
    for other_bones, rows in by_bones.items():
        if other_bones != bones:
            mismatch = (other_bones, rows[0])
            break
    if mismatch is None:
        print("  skip  rig-mismatch refusal (every character found is %d-bone)"
              % bones)
    else:
        ob, (on, obytes, oidx) = mismatch
        try:
            add_model(dbytes, obytes, oidx)
            check(False, "refuses a %d-bone model into a %d-bone bundle"
                  % (ob, bones), "it returned instead")
        except ValueError as ex:
            check("bone" in str(ex),
                  "refuses a %d-bone model into a %d-bone bundle" % (ob, bones),
                  str(ex))
        try:
            add_model(dbytes, obytes, oidx, allow_rig_mismatch=True)
            check(True, "allow_rig_mismatch=True overrides it deliberately")
        except ValueError as ex:
            check(False, "allow_rig_mismatch=True overrides it deliberately",
                  str(ex))

    # --- the refusals that must stay ------------------------------------
    for what, args in (
            ("a non-MOD0 source chunk", (dbytes, sbytes, 0)),
            ("a source index past the end", (dbytes, sbytes, 10 ** 6))):
        try:
            tag = Rsc(sbytes).chunks[0].tag
            if what.startswith("a non-MOD0") and tag == "MOD0":
                print("  skip  %s (chunk 0 happens to be a MOD0)" % what)
                continue
            add_model(*args)
            check(False, "refuses %s" % what, "it returned instead")
        except ValueError:
            check(True, "refuses %s" % what)

    try:
        append_chunk(dbytes, "MOD0", b"")
        check(False, "refuses a zero-length chunk", "it returned instead")
    except ValueError:
        check(True, "refuses a zero-length chunk")

    print()
    if FAILURES:
        print("FAILED: %d" % len(FAILURES))
        return 1
    print("PASS")
    return 0


if __name__ == '__main__':
    sys.exit(main())
