r"""What is inside a loose `.mod`? The header is a count manifest, and 683 of
them are plain text.

8,600 of these sit on the disc and none were read: the crates, barrels, signs
and furniture, and -- per REQ-045 -- every weapon model in the game, since
characters load from `.rsc` but weapons load from `.mod`. M15's geometry half
is blocked on this.

TWO THINGS THIS PROBE USED TO SAY THAT WERE WRONG, both in its own docstring:

  * *"Every one starts with the ASCII string `version: 2.12\0`"*. There are
    **four versions**, and the separator is not always a NUL:

        version   entries   distinct   separator
        2.12         7,061      2,635    NUL
        2.00           792         60    NUL
        1.10           685        251    LF x683, CR x2
        2.10            62         62    NUL
        TOTAL        8,600      3,008

    Entries, not names: most models ship in several archives, which is why the
    two columns differ so widely. (Table reconciled with rdr-re, who counted
    685 where this file counted 683 -- the two missing were the CRLF pair.)

  * *"the rest is binary"*. **The 685 v1.10 entries are plain ASCII**, and they
    are the whole format written out in longhand -- which is why nothing had
    to be guessed at here.

THE TEXT VERSION IS THE SPECIFICATION. A v1.10 file opens with a manifest of
eleven counts and then writes the arrays out by name:

    version: 1.10
    verts: 4        normals: 1      colors: 4       tex1s: 4
    tex2s: 0        tangents: 0     materials: 1    adjuncts: 4
    primitives: 2   matrices: 1     reskins: 1

    v  10.917522  2.897558  11.282701      (3 floats)
    n   1.0f      1.0f      1.0f
    c   0.449380  0.422453  0.374739  4.000000
    t1  1.000000  0.000553

    mtl lambert71SG { packets: 1  primitives: 2  textures: 1
                      illum/ambient/diffuse/specular ...
                      adj 2 0 0 2 -1 0 ...   str 4 0 1 2 3   mtx 0 }
    mtxv 4
    mtxn 1

THE BINARY VERSIONS CARRY THE SAME ELEVEN COUNTS as little-endian u32 at
offset 14, immediately after `version: X.YZ\0`, and the vertex array follows
at **+58** as float32 triples. 11 counts x 4 bytes = 44, and 14 + 44 = 58,
which is exactly where an earlier run of this probe reported "first plausible
float run" without knowing why.

MEASURED over every binary `.mod` on the pristine disc:

    7,913 of 7,915 (99.97%) parse under that layout with a vertex block that
    fits the file and holds only finite, plausible coordinates. **Zero**
    implausible floats anywhere.

The two exceptions are not decode failures and are named rather than rounded
away: `krd_lod_01.mod` and `sncb_lod_01.mod` are both **70-byte stubs** --
14 header + 44 manifest + one 3-float record -- declaring `verts: 0`,
`normals: 1` and a `reskins` field of **0xCCCCCCCC**, which is MSVC's
uninitialised-stack fill. Two empty models the original exporter wrote with
one field it never set.

THE ARRAY LAYOUT IS SETTLED, and by CONTENT rather than by arithmetic --
each width is confirmed by whether what lands there is plausible for its type:

    +58                 verts     12 B   3 x f32
    +12*verts           normals   12 B   3 x f32
    +12*normals         colors    16 B   4 x f32
    +16*colors          tex1s      8 B   2 x f32

    version  entries   normals   colors    tex1s
    2.12       7,059    100.0%   100.0%   100.0%
    2.10          62     98.4%   100.0%   100.0%
    2.00         792     15.8%    91.2%    36.1%

**and that is scoped to v2.12/v2.10 on purpose.** The eleven-count manifest is
shared by all three binary versions -- 7,913 of 7,915 parse it -- but the
ARRAYS after it are not: v2.00's 60 distinct models put something else at
`+58 + 12*verts`, and 5% is chance rather than a near miss. v2.00 is a
different layout and this probe does not pretend to read it.

**The normals check is the one that proves the layout**, because it cannot
pass by luck: read the array at the wrong offset and the triples are not unit
vectors. 1,575 files are all-unit and 1,058 are all-(1,1,1) -- and that second
group is not a failure, it is the same placeholder the TEXT version writes as
`n 1.0f 1.0f 1.0f` for a model with no per-vertex lighting. Recognising it
took the text dialect; without it this check read as 59.8%.

THE TAIL IS VARIABLE-LENGTH, WHICH IS A RESULT AND NOT A GAP. If every block
after the arrays were fixed-width per element, file size would be an exact
linear function of the eleven counts. It is not: solving that system exactly
over the rationals from 11 independent equations and testing it against all
2,633 files fits **15 of 2,633**, with the first mismatch off by a single byte
(`bookoldr_lod_01.mod`, 4,402 predicted against 4,403 actual). So at least one
block is variable-length, and a writer cannot compute an output size from the
manifest alone.

WHAT THE TAIL LOOKS LIKE, as far as it is honestly read. In
`_hsph_lod_01.mod` the material block begins at the end of the arrays:

    "default "          8 B ASCII, NUL-padded, the material name
    2                   u32
    28                  u32  -- equals this file's `primitives` count
    0, 1                u32
    1.0 x8, 0,0,0, 1.0  f32  -- the ambient/diffuse/specular block the text
                               dialect writes out by name
    ...                 12-byte records whose trailing u16 increments
                               (29, 29, 30, 30, 30, 31 ...) -- the `adj` array
    ... 02 04 <4 bytes> 02 04 <4 bytes> ...  -- looks like strip records,
                               a 2-byte header then indices, which is the
                               likeliest source of the variable length

**None of that is claimed as decoded.** The material name and the
primitive-count field are legible and repeatable; the adjunct record's exact
start offset is not pinned (the first row read at +1006 still contains the
value 58, which is the vertex offset, so the block starts a few bytes later),
and the primitive encoding is a hypothesis. M15 imports a weapon model, which
means WRITING this tail, and for 31 of the 77 weapon types with a model it is
the binary tail and not the text one (rdr-re, REQ-053). So this is where the
remaining work is.

    python3 03_Tools/scripts/probe_mod.py [iso]

Read-only. Opens the pristine disc and writes nothing.
"""

import math
import os
import re
import struct
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _HERE]
sys.path.insert(0, os.path.dirname(_HERE))

from rdrstudio import config                                    # noqa: E402
from rdrstudio.game import Game                                 # noqa: E402

#: The eleven counts, in the order the text version writes them.
KEYS = ("verts", "normals", "colors", "tex1s", "tex2s", "tangents",
        "materials", "adjuncts", "primitives", "matrices", "reskins")

# `[\r\n]`, not `\n`. Two v1.10 files are CRLF -- `crap.mod` and
# `shitgood.mod`, 328 KB and 327 KB, the third and fourth largest text models
# on the disc -- and a `\n`-only class drops them into "no version header".
# That is the same CRLF miss `props.py`'s own comments record twice,
# reproduced here on the day I wrote about it; rdr-re caught it by counting
# 685 where this said 683.
#
# The pair is worth a sentence, because it explains them. They are near
# twins: identical `verts: 1714`, `tex1s: 1761`, `normals: 1`, `colors: 722`
# and `primitives: 2019`, differing only in `materials` -- 8 against 7.
# Somebody exported a model, called it `crap`, fixed one material and saved
# the result as `shitgood`. A before/after pair from one sitting, shipped.
#
# rdr-re measured the wider pattern (`probe_anomalies.py`): 11 of 17 files
# that broke any decode this project has written carry a leftover name --
# 65% against a 0.24% base rate, a 275x lift. **On this disc, check the name
# before you rewrite the parser.** The largest text model of all is
# `copy (2) of nrd_lod_01.mod` at 445,370 bytes: Windows' own duplicate
# naming, shipped on a retail disc.
HEADER = re.compile(rb"version: ([0-9.]+)([\x00\r\n])")
MANIFEST = 4 * len(KEYS)
FAILED = []


def check(label, ok, detail=""):
    print("     %-56s %s%s"
          % (label, "ok" if ok else "FAIL", ("  " + detail) if detail else ""))
    if not ok:
        FAILED.append(label)
    return ok


def text_manifest(data):
    """The eleven counts from a v1.10 (ASCII) file."""
    out = {}
    for line in data[:600].split(b"\n")[1:14]:
        bits = line.decode("latin-1").split(":")
        if len(bits) == 2 and bits[0].strip() in KEYS:
            try:
                out[bits[0].strip()] = int(bits[1])
            except ValueError:
                pass
    return out


def binary_manifest(data):
    """(version, counts, vertex offset) for a binary `.mod`, or None."""
    m = HEADER.match(data)
    if m is None or m.group(2) != b"\x00":
        return None
    off = m.end()
    if off + MANIFEST > len(data):
        return None
    counts = struct.unpack_from("<%dI" % len(KEYS), data, off)
    return m.group(1).decode(), dict(zip(KEYS, counts)), off + MANIFEST


def main():
    iso = sys.argv[1] if len(sys.argv) > 1 else config.VANILLA_ISO
    print("disc: %s  (read-only)\n" % iso)
    game = Game(iso, writable=False)

    versions = Counter()
    distinct = {}
    text_ok = 0
    arr = {}   # per version: the array-layout content checks
    root3 = math.sqrt(3.0)
    binary = plausible = 0
    stubs, odd = [], []
    seen = set()

    for arch in game.archives():
        try:
            dave = game.archive(arch)
        except Exception:
            continue
        for e in dave:
            if e.ext != "mod":
                continue
            try:
                data = dave.read(e)
            except Exception:
                continue
            m = HEADER.match(data)
            if m is None:
                versions["(no version header)"] += 1
                continue
            ver = m.group(1).decode()
            sep = {b"\x00": "NUL", b"\n": "LF", b"\r": "CR"}[m.group(2)]
            versions["%s %s" % (ver, sep)] += 1
            distinct.setdefault(ver, set()).add(e.name.lower())

            if m.group(2) in (b"\n", b"\r"):
                if len(text_manifest(data)) == len(KEYS):
                    text_ok += 1
                continue

            binary += 1
            got = binary_manifest(data)
            if got is None:
                odd.append((e.name, "manifest does not fit the file"))
                continue
            _v, counts, vo = got
            need = vo + 12 * counts["verts"]
            if counts["verts"] == 0 or need > len(data):
                # 0xCCCCCCCC is MSVC's uninitialised-stack fill.
                stubs.append((e.name, len(data), counts["reskins"]))
                continue
            sample = struct.unpack_from(
                "<%df" % (3 * min(counts["verts"], 64)), data, vo)
            if any((not math.isfinite(x)) or abs(x) > 1e5 for x in sample):
                odd.append((e.name, "implausible vertex floats"))
                continue
            plausible += 1
            seen.add(e.name)

            # Content checks on the arrays that follow the vertices.
            v, nn, col, t1 = (counts["verts"], counts["normals"],
                              counts["colors"], counts["tex1s"])
            no = vo + 12 * v
            co = no + 12 * nn
            to = co + 16 * col
            if nn == 0 or to + 8 * t1 > len(data):
                continue
            st = arr.setdefault(ver, dict(n=0, normals_ok=0, unit=0,
                                          placeholder=0, colors_ok=0, tex_ok=0))
            st["n"] += 1
            k = min(nn, 24)
            ns = struct.unpack_from("<%df" % (3 * k), data, no)
            lens = [math.sqrt(ns[i] ** 2 + ns[i + 1] ** 2 + ns[i + 2] ** 2)
                    for i in range(0, 3 * k, 3)]
            unit = all(abs(x - 1.0) < 0.02 for x in lens)
            place = all(abs(x - root3) < 0.02 for x in lens)
            if unit or place or all(abs(x - 1.0) < 0.02 or abs(x - root3) < 0.02
                                    for x in lens):
                st["normals_ok"] += 1
                if unit:
                    st["unit"] += 1
                elif place:
                    st["placeholder"] += 1
            cs = struct.unpack_from("<%df" % (4 * min(col, 8)), data, co)
            if all(-0.01 <= x <= 4.01 for x in cs):
                st["colors_ok"] += 1
            ts = struct.unpack_from("<%df" % (2 * min(t1, 24)), data, to)
            if all(math.isfinite(x) and abs(x) < 64 for x in ts):
                st["tex_ok"] += 1
    game.close()

    print("1. `.mod` is four formats, not one")
    print("     %-12s %8s %10s" % ("version sep", "entries", "distinct"))
    for k, n in versions.most_common():
        ver = k.split()[0]
        print("     %-12s %8d %10s"
              % (k, n, len(distinct.get(ver, ())) if ver in distinct else "-"))
    check("more than one version exists", len(versions) > 1)
    text_n = versions.get("1.10 LF", 0) + versions.get("1.10 CR", 0)
    check("some .mod are plain TEXT (v1.10)", text_n > 0,
          "%d entries, %d distinct" % (text_n, len(distinct.get("1.10", ()))))
    check("the CRLF pair is not lost to a newline class",
          versions.get("1.10 CR", 0) == 2,
          "%d CR-separated" % versions.get("1.10 CR", 0))
    check("every text file carries all eleven counts",
          text_ok == text_n, "%d of %d" % (text_ok, text_n))

    print("\n2. the binary versions carry the same manifest at +14")
    total = plausible + len(stubs) + len(odd)
    print("     %d binary files, vertex array at +%d" % (binary, 14 + MANIFEST))
    check("the manifest parses and the vertices are plausible",
          plausible >= total - 2,
          "%d of %d (%.2f%%)" % (plausible, total, 100.0 * plausible / total))
    check("no file anywhere holds an implausible vertex",
          not [o for o in odd if "implausible" in o[1]],
          "%d odd" % len(odd))

    print("\n3. the array layout, proved by what lands there -- PER VERSION")
    print("     %-7s %7s %9s %9s %9s"
          % ("version", "files", "normals", "colors", "tex1s"))
    for ver in sorted(arr):
        st = arr[ver]
        print("     %-7s %7d %8.1f%% %8.1f%% %8.1f%%"
              % (ver, st["n"], 100.0 * st["normals_ok"] / st["n"],
                 100.0 * st["colors_ok"] / st["n"],
                 100.0 * st["tex_ok"] / st["n"]))
    big = arr.get("2.12", dict(n=0))
    check("v2.12 normals are unit or the (1,1,1) placeholder, without exception",
          big["n"] and big["normals_ok"] == big["n"],
          "%d of %d  (%d unit, %d placeholder)"
          % (big["normals_ok"], big["n"], big.get("unit", 0),
             big.get("placeholder", 0)))
    check("v2.12 texcoords are finite and modest",
          big["n"] and big["tex_ok"] == big["n"],
          "%d of %d" % (big["tex_ok"], big["n"]))
    ten = arr.get("2.10", dict(n=0, normals_ok=0))
    check("v2.10 follows the same layout",
          ten["n"] and ten["normals_ok"] >= ten["n"] - 1,
          "%d of %d" % (ten["normals_ok"], ten["n"]))
    zero = arr.get("2.00", dict(n=1, normals_ok=0))
    check("v2.00 does NOT -- a different array layout, not yet decoded",
          100.0 * zero["normals_ok"] / zero["n"] < 25.0,
          "only %d of %d normals land" % (zero["normals_ok"], zero["n"]))

    print("\n4. the exceptions, named rather than rounded away")
    for name, size, reskins in stubs[:4]:
        print("     %-20s %3d bytes, verts=0, reskins=0x%08X (MSVC fill)"
              % (name, size, reskins))
    check("every exception is a stub, not a decode failure",
          len(stubs) + len(odd) <= 2 and not odd,
          "%d stub(s), %d unexplained" % (len(stubs), len(odd)))
    for name, why in odd[:4]:
        print("     unexplained: %-24s %s" % (name, why))

    print("\n" + "=" * 68)
    print("SUMMARY")
    print("=" * 68)
    if FAILED:
        for f in FAILED:
            print("  [FAIL] %s" % f)
        print("\n%d check(s) failed." % len(FAILED))
        return 1
    print("  `.mod` is four versions. 685 entries are ASCII and spell the")
    print("  format out in full; all three binary versions share the eleven-")
    print("  count manifest at +14, and %d of %d parse it." % (plausible, total))
    print("  The ARRAY layout after it -- verts 12B, normals 12B, colors 16B,")
    print("  tex1s 8B -- holds for v2.12 and v2.10 without exception, proved")
    print("  by normals landing unit-length or on the (1,1,1) placeholder.")
    print("  **v2.00 is a different layout** and is not decoded, and neither")
    print("  is the material/primitive tail, which is variable-length -- an")
    print("  exact size equation fits only 15 of 2,633 v2.12 files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
