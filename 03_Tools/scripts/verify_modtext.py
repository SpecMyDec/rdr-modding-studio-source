"""The plain-text `.mod` reader agrees with what the files declare.

M15's first half. **This is a READER test, not M15's acceptance test** — see
the note at the bottom before registering it under that label.

`.mod` was "8,600 files nothing reads". It is not one format: of the 3,008
loose `.mod` in `SWAT.DAT`, 2,635 are binary `2.12` and 251 are plain ASCII
`1.10`. This covers the ASCII dialect, which is 31 of the 55 weapons and the
three most widely shared models — `ptl_lod_01` (11 weapons), `rfl_lod_01` (11)
and `pt2_lod_01` (6).

What is asserted, all of it against the files' OWN declared counts rather than
against the reader agreeing with itself:

  1. EVERY TEXT FILE PARSES, and its `v` lines match its header `verts`.
  2. THE TRIANGLE COUNT RECONCILES. `header primitives` == triangles produced
     on 249 of 251. The two exceptions are named individually, so a THIRD
     file drifting is a failure rather than an allowance.
  3. THE PACKET FIELDS THAT ARE LAWS HOLD. Field 1 == that packet's `adj`
     lines on all 3,786 packets; field 4 == its `reskin` lines on all 2,244
     four-number packets.
  4. NO TRIANGLE POINTS OFF THE END of the vertex list.
  5. THE THREE SILENT FAILURES STAY FIXED. Each of these produced a WRONG
     ANSWER WITH NO ERROR while the reader was being written, so each gets a
     named guard on the exact file that exposed it.
  6. A BINARY `.mod` IS REFUSED BY NAME rather than read as garbage.

Reads `01_Vanilla` and writes nothing.

## Why this should NOT be registered as `[M15]` as it stands

The roadmap's acceptance for M15 is, verbatim:

    `[M15]` test imports a weapon `.mod` and asserts the replaced weapon
    re-reads with the new geometry.

**This test imports nothing.** It reads; it never writes a model back, never
replaces a weapon's geometry and never re-reads one off the disc. Registering
it as `[M15]` would let the milestone land while the thing the tester actually
asked for -- importing a weapon model -- was never demonstrated. That is the
failure this project has catalogued five ways today: a passing case wide
enough that something other than the intended thing satisfies it, with the
gate as the casualty.

Two honest options, both the reviewer's call and neither mine:

  * register it under a plain label, as the other `verify_*` rows are, and
    keep `[M15]` for the import test when the import path exists; or
  * amend the roadmap's acceptance to name a reader milestone separately,
    which is a scope change and therefore the developer's.

The import path additionally needs the developer's `ModelName` decision (REQ-045):
55 weapons share 14 models, so overwriting one `.mod` silently re-models up to
eleven weapons, and the safe alternative needs a field that is not currently
editable.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdrstudio import Game, guns, modtext                 # noqa: E402
from rdrstudio.config import VANILLA_ISO                  # noqa: E402

ARCHIVE = "SWAT.DAT"

# Named individually rather than allowed as a count. Both are dev leftovers by
# name, near-identical to each other, and each is a handful of triangles short
# with no degenerate or short run to explain it. If a third file joins them the
# test fails, which is the point.
KNOWN_OFF = {"crap.mod", "shitgood.mod"}

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
    names = [e.name for e in game.toc(ARCHIVE)
             if e.name.lower().endswith(".mod")]

    text, binary = [], []
    for n in names:
        try:
            b = dv.read(n)
        except Exception:
            continue
        (text if modtext.is_text_mod(b) else binary).append((n, b))
    print("%s: %d loose .mod -- %d plain text, %d binary"
          % (ARCHIVE, len(names), len(text), len(binary)))
    check("there are text .mod files to read", len(text) >= 200, True, ">=")
    check("and the binary dialect is the majority", len(binary) > len(text),
          True)

    # ---------------------------------------------------------------- 1
    print("\n1. every text file parses, and its own header agrees:")
    parsed = verts_ok = 0
    failed = []
    for n, b in text:
        try:
            m = modtext.mesh(b)
        except Exception as ex:
            failed.append((n, str(ex)[:40]))
            continue
        parsed += 1
        verts_ok += m["counted"]["verts"] == m["header"].get("verts")
    for n, why in failed[:3]:
        print("      %s -> %s" % (n, why))
    check("every text .mod parses", parsed, len(text))
    check("v lines == header verts", verts_ok, len(text))

    # ---------------------------------------------------------------- 2
    print("\n2. the triangle count reconciles with the header:")
    exact = 0
    offenders = []
    for n, b in text:
        m = modtext.mesh(b)
        want, got = m["header"].get("primitives"), len(m["tris"])
        if want == got:
            exact += 1
        else:
            offenders.append((n, want, got))
    for n, w, g in offenders:
        print("      %-24s header %-6d got %-6d %s"
              % (n, w, g, "(known)" if n in KNOWN_OFF else "*** NEW ***"))
    check("files whose triangles match their header",
          exact, len(text) - len(KNOWN_OFF))
    check("no file drifts that was not already known",
          sorted(n for n, _w, _g in offenders), sorted(KNOWN_OFF))

    # ---------------------------------------------------------------- 3
    print("\n3. the packet fields that are laws:")
    packets = f1 = f4 = four = 0
    for n, b in text:
        for mat in modtext.parse(b)["materials"]:
            for p in mat["packets"]:
                packets += 1
                f1 += p["fields"][0] == len(p["adj"])
                if len(p["fields"]) > 3:
                    four += 1
                    f4 += p["fields"][3] == len(p["reskins"])
    print("    %d packets, %d of them four-number" % (packets, four))
    check("packet field 1 == that packet's adj lines", f1, packets)
    check("packet field 4 == that packet's reskin lines", f4, four)
    check("both packet shapes are present", four and four < packets, True)

    # ---------------------------------------------------------------- 4
    print("\n4. no triangle points off the end of the vertex list:")
    oob = 0
    for n, b in text:
        m = modtext.mesh(b)
        nv = len(m["verts"])
        if any(i < 0 or i >= nv for t in m["tris"] for i in t):
            oob += 1
    check("files with an out-of-range vertex index", oob, 0)

    # ---------------------------------------------------------------- 5
    print("\n5. the three silent failures stay fixed:")
    print("    each of these returned a WRONG answer with NO error")

    m = modtext.mesh(dv.read("akabag_lod_02.mod"))
    check("  4-number packet header parses (was 0 triangles)",
          len(m["tris"]), m["header"]["primitives"])

    m = modtext.mesh(dv.read("edge_arrow.mod"))
    check("  a tri-only file parses (was an empty mesh)",
          len(m["tris"]), m["header"]["primitives"])

    m = modtext.mesh(dv.read("ag2_lod_01.mod"))
    check("  degeneracy judged in adjunct space (was 201 of 1848)",
          len(m["tris"]), m["header"]["primitives"])

    m = modtext.mesh(dv.read("edge_hat_sn2_lod_01.mod"))
    check("  an explicit degenerate `tri` still counts (was 116 of 117)",
          len(m["tris"]), m["header"]["primitives"])

    m = modtext.mesh(dv.read("book_lod_01.mod"))
    check("  the header is the FILE's, not the last material's",
          m["header"]["primitives"], len(m["tris"]))

    # ---------------------------------------------------------------- 6
    print("\n6. the weapons this actually unblocks:")
    ws = guns.parse(dv.read("swat.guns"))
    have = {n for n, _b in text}
    named = []
    for w in ws:
        f = getattr(w, "fields", {}).get("ModelName")
        t = f.get("text") if isinstance(f, dict) else None
        if t:
            named.append(t)
    textual = [t for t in named if (t + ".mod") in have]
    print("    %d weapons, %d distinct models; %d weapons use a text model"
          % (len(named), len(set(named)), len(textual)))
    check("a real share of the arsenal is covered", len(textual) >= 25,
          True, ">=")
    readable = 0
    for t in sorted(set(textual)):
        mm = modtext.mesh(dv.read(t + ".mod"))
        readable += bool(mm["verts"]) and bool(mm["tris"])
    check("every text weapon model reads as real geometry",
          readable, len(set(textual)))

    # ---------------------------------------------------------------- 7
    print("\n7. a binary .mod is refused, not read as garbage:")
    bn, bb = binary[0]
    try:
        modtext.parse(bb)
        got = "no refusal"
    except modtext.NotTextMod as ex:
        got = "refused"
        print("      %s -> %s" % (bn, ex))
    check("  the binary dialect raises by name", got, "refused")

    print("\n%s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
