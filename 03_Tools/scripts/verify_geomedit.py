"""Level geometry can be moved in place, exactly, and moved back.

Five properties, checked across every level area on the disc:

  1. **Addressable.** Every vertex of every editable mesh has a byte offset,
     and reading four floats there reproduces the decoded position bit for
     bit. If this is off by a word, an edit silently corrupts the packet.
  2. **Length-preserving.** A transformed chunk is the same size as the
     original. These files sit in fixed archive slots with almost no
     headroom, so this is the property that makes the whole approach safe.
  3. **Lossless when nothing moves.** Writing every vertex back with the
     value just read must produce the original bytes exactly. This is the
     real "no corruption" property: it proves the decode and the encode
     agree, with no rounding introduced by the round trip itself.
  4. **Accurate when it does move.** Translate by a vector, decode, translate
     back, and every position must return to within a tight epsilon.

Note 3 and 4 are different claims, and 4 is deliberately not byte-exact:
`(x + 12.5) - 12.5` is not `x` in 32-bit float for most x, so a *sequence* of
relative nudges drifts. That is why the editor stores an absolute target
position per object and re-derives the transform from the vanilla geometry
each time, rather than accumulating deltas.

Skinned meshes pass these same checks now: their 16-bit positions re-quantise
exactly, so an identity write is still byte-identical. See verify_skinwrite.py.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rdrstudio import Game, geomedit, rsc  # noqa: E402
from rdrstudio.config import VANILLA_ISO  # noqa: E402

DELTA = (12.5, -3.25, 7.75)


def main():
    game = Game(VANILLA_ISO)
    areas = meshes = editable = refused = 0
    addr_ok = length_ok = identity_ok = obj_ok = roundtrip_ok = 0
    worst = 0.0
    problems = []

    for arc in game.archives():
        if not arc.startswith("LVLS_"):
            continue
        try:
            dv = game.archive(arc)
        except Exception:
            continue
        for area in rsc.level_areas(dv):
            try:
                r = rsc.Rsc(dv.read(area), area)
            except Exception:
                continue
            areas += 1
            for c in r.chunks:
                if c.tag != "MOD0":
                    continue
                try:
                    m = r.model(c.index)
                except Exception:
                    continue
                if not m or not m["tris"]:
                    continue
                meshes += 1
                raw = r.raw(c.index)

                if not geomedit.editable(m):
                    refused += 1
                    continue
                editable += 1

                # 1. every offset reproduces its decoded position.
                # Two encodings: level positions are three floats, character
                # positions are three u16 codes that have to go back through
                # the ROW-register dequantisation to become metres again.
                import struct
                from rdrstudio import vif
                quant = m.get("quant") or [None] * len(m["verts"])
                bad = 0
                for i, (v, off) in enumerate(zip(m["verts"], m["voff"])):
                    q = quant[i] if i < len(quant) else None
                    if q is None:
                        got = struct.unpack_from("<3f", raw, off)
                    else:
                        got = vif.dequantise(
                            list(struct.unpack_from("<3H", raw, off)), q)
                    if got != v:
                        bad += 1
                if bad:
                    problems.append((area, c.index, "%d offsets wrong" % bad))
                    continue
                addr_ok += 1

                # 2. same length
                moved = geomedit.translate(raw, m, *DELTA)
                if len(moved) != len(raw):
                    problems.append((area, c.index, "length changed"))
                    continue
                length_ok += 1

                # 3. writing back what we read changes nothing
                if geomedit.translate(raw, m, 0.0, 0.0, 0.0) != raw:
                    problems.append((area, c.index, "identity write differs"))
                    continue
                identity_ok += 1

                # 3b. OBJ round trip: export, re-import untouched, get the
                # same bytes back. This is what makes Blender editing safe.
                # If it were lossy, opening a surface and saving it without
                # changing anything would still move every vertex -- which is
                # exactly what %.6f did before this was checked.
                objtext = geomedit.to_obj(m, "m%d" % c.index, {})
                if geomedit.apply_obj(raw, m,
                                      geomedit.from_obj(objtext)) != raw:
                    problems.append((area, c.index, "OBJ round trip is lossy"))
                    continue
                obj_ok += 1

                # 4. move there and back lands within epsilon
                m2 = rsc.Rsc(_swap(r, c.index, moved), area).model(c.index)
                if m2 is None:
                    problems.append((area, c.index, "moved copy won't decode"))
                    continue
                back = geomedit.translate(moved, m2,
                                          -DELTA[0], -DELTA[1], -DELTA[2])
                m3 = rsc.Rsc(_swap(r, c.index, back), area).model(c.index)
                err = max((abs(a - b)
                           for va, vb in zip(m["verts"], m3["verts"])
                           for a, b in zip(va, vb)), default=0.0)
                worst = max(worst, err)
                if err > 1e-3:
                    problems.append((area, c.index,
                                     "returns %.6f off" % err))
                    continue
                roundtrip_ok += 1

    print("areas                 : %d" % areas)
    print("render meshes         : %d" % meshes)
    print("  editable            : %d" % editable)
    # Skinned meshes used to be refused here; they are writable now (their
    # 16-bit quantisation inverts exactly -- see verify_skinwrite.py), so
    # anything still refused has genuinely lost its offsets.
    print("  refused             : %d" % refused)
    print("offsets exact         : %d / %d" % (addr_ok, editable))
    print("length preserved      : %d / %d" % (length_ok, editable))
    print("identity write exact  : %d / %d" % (identity_ok, editable))
    print("OBJ round trip exact  : %d / %d" % (obj_ok, editable))
    print("move-and-back accurate: %d / %d  (worst drift %.2e m)"
          % (roundtrip_ok, editable, worst))
    for p in problems[:10]:
        print("   %s chunk %d: %s" % p)

    ok = (editable > 0 and identity_ok == editable
          and obj_ok == editable
          and roundtrip_ok == editable and not problems)
    print("\n%s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def _swap(bundle, index, new_chunk):
    """The bundle's bytes with one chunk replaced. Chunk sizes are unchanged,
    so every offset in the header stays valid."""
    c = bundle.chunks[index]
    data = bytearray(bundle.data)
    data[c.offset:c.offset + c.size] = new_chunk
    return bytes(data)


if __name__ == "__main__":
    sys.exit(main())
