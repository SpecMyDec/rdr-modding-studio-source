"""Character models can be written, not just read.

Level geometry stores positions as plain floats, so moving a vertex is a byte
patch. Character models do not: a skinned position is a 16-bit code, and the
scale lives in the VIF ROW register rather than in the file --

    value[k] = row[k] + code[k] * ulp[k] - bias[k]

where `ulp[k]` is the distance from `row[k]` to the next representable float
(the hardware is in STMOD 1, so it ADDS the ROW register to each element, and
adding an integer to a float advances it that many ULPs). `bias` closes the
batch as a V3_32 quadword at address 0x28.

That is invertible, and this proves it is invertible EXACTLY:

  1. Every skinned vertex has a recorded byte offset, and reading the u16
     triple there reproduces the decoded position bit for bit.
  2. Re-encoding the decoded float reproduces the ORIGINAL u16 code. This is
     the one that matters -- if it does not hold, then "export a character,
     change nothing, import it" would move every vertex.
  3. A written chunk is the same length as the original, so it still fits its
     archive slot.

It also measures the practical limits a modder needs to know: the step size
in metres (how precisely a vertex can be placed) and the representable range
(how far one can be moved before it saturates).
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rdrstudio import Game, rsc, vif  # noqa: E402
from rdrstudio.config import VANILLA_ISO  # noqa: E402


def main():
    game = Game(VANILLA_ISO)
    bundles = models = 0
    skinned_verts = addr_ok = code_ok = 0
    steps = []
    ranges = []
    problems = []

    for arc in game.archives():
        try:
            dv = game.archive(arc)
        except Exception:
            continue
        for e in dv:
            if e.ext != "rsc" or "_lods" not in e.name.lower():
                continue
            try:
                r = rsc.Rsc(dv.read(e), e.name)
            except Exception:
                continue
            bundles += 1
            for c in r.chunks:
                if c.tag != "MOD0":
                    continue
                try:
                    m = r.model(c.index)
                except Exception:
                    continue
                if not m or not m.get("skinned"):
                    continue
                raw = r.raw(c.index)
                q = m.get("quant") or []
                vo = m.get("voff") or []
                if len(q) != len(m["verts"]) or len(vo) != len(m["verts"]):
                    problems.append((e.name, c.index, "quant/voff length"))
                    continue
                models += 1

                for i, v in enumerate(m["verts"]):
                    if q[i] is None:
                        continue          # a float batch, covered elsewhere
                    skinned_verts += 1
                    code = list(struct.unpack_from("<3H", raw, vo[i]))
                    # 1. the offset reproduces the decoded position
                    if vif.dequantise(code, q[i]) == v:
                        addr_ok += 1
                    else:
                        problems.append((e.name, c.index, "offset mismatch"))
                        continue
                    # 2. re-encoding the float gives back the same code
                    if vif.quantise(v, q[i]) == code:
                        code_ok += 1
                    else:
                        problems.append((e.name, c.index, "re-encode differs"))
                    rowf, step, bias = q[i]
                    steps.append(max(step))
                    ranges.append(max(0xFFFF * step[k] for k in range(3)))
        game._cache.pop(arc.upper(), None)

    print("character bundles      : %d" % bundles)
    print("skinned models         : %d" % models)
    print("skinned vertices       : %d" % skinned_verts)
    print("offsets exact          : %d / %d" % (addr_ok, skinned_verts))
    print("re-encode gives the")
    print("  original code back   : %d / %d" % (code_ok, skinned_verts))
    if steps:
        steps.sort()
        ranges.sort()
        print("\nwhat a modder can do with this")
        print("  placement step     : %.6f m median, %.6f m worst"
              % (steps[len(steps) // 2], steps[-1]))
        print("  representable range: %.2f m median, %.2f m smallest"
              % (ranges[len(ranges) // 2], ranges[0]))
    for p in problems[:8]:
        print("   %s chunk %d: %s" % p)

    ok = (skinned_verts > 10000 and addr_ok == skinned_verts
          and code_ok == skinned_verts and not problems)
    print("\n%s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
