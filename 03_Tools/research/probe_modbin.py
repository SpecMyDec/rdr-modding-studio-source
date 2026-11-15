#!/usr/bin/env python3
"""REQ-050: the binary `.mod` layout, read out of the executable.

The reviewer assigned this as a disassembly job because the corpus route had stalled at
2% (rdr-app's `probe_mod_stride.py`).  It was the right call.  What follows was
read from the code in `07_Research/SLUS_205.00.unpacked.bin` (file offset + 
0x00100000 = vaddr) and then checked against all 2,695 binary `.mod` files.

THE DISPATCH -- 0x3B6A50
-----------------------
    0x3B6A6C  jal 0x19A070   read 14 bytes of header into sp
    0x3B6A78  sb zero,13(sp)                 -- NUL at 13: a 13-char string
    0x3B6A84  jal 0x25F608   strncmp(buf, "version: 2.1", 12)

**The literal is `version: 2.1`, twelve characters -- NOT `version: 2.12`.**
So 2.10 and 2.12 take the SAME branch, and the minor version is recovered
separately:

    0x3B6AB4  lbu  t1,12(sp)      -- the 13th character, the final digit
    0x3B6AD4  addiu t1,t1,-38     -- '0' (48) -> 10,  '2' (50) -> 12

Three branches, and they pick a READER OBJECT rather than a parser:

    "version: 2.1"   -> vtable 0x005062F0   t0=1  t1=10 or 12
    "version: 2.00"  -> vtable 0x005062F0   t0=1  t1=1
    "version: 1.10"  -> vtable 0x005063A0   t0=0  t1=10
    no match         -> vtable 0x005063A0   t0=0  t1=0

All four then `jal 0x3B6E38` -- **one driver parses both dialects through two
readers.**  That is why the eleven field names sit beside both string clusters,
and it is why the binary field ORDER is exactly the text dialect's.

THE BINARY READER'S PRIMITIVES -- vtable 0x005062F0
--------------------------------------------------
Each is `jal 0x19A070` (the stream read) with a byte count, so the file is a
plain sequential stream with no offsets:

    +0x10  1 byte  -> lbu   u8         +0x24  12 bytes  vec3
    +0x14  2 bytes -> lhu   u16        +0x28  16 bytes  vec4
    +0x18  4 bytes -> lw    u32        +0x2C  12 bytes  vec3, w=1.0
    +0x1C  4 bytes -> mtc1  f32        +0x30  16 bytes  vec4
    +0x20  8 bytes        vec2

and the NAMED slots, which take a field-name string the binary reader ignores
and the text reader matches:

    +0x34 / +0x38 -> u32      +0x40 -> vec2   +0x44 -> vec3   +0x48 -> vec4

THE HEADER -- proven, not fitted
--------------------------------
The driver makes eleven consecutive named-u32 calls, and the strings it passes
are, in order:

    verts: normals: colors: tex1s: tex2s: tangents:
    materials: adjuncts: primitives: matrices: reskins:

(`primitives:` is loaded as `addiu a1,s0,-4723` rather than a fresh `lui`,
which is why a naive lui/addiu scan finds only ten of the eleven.)

    offset  0   char[14]  "version: 2.1x\\0"
    offset 14   u32 x 11  the counts above, in that order
    offset 58   the vertex block

THE VERTEX BLOCK
----------------
After the header the driver calls +0x44, +0x44, +0x48, +0x40, +0x40 --
vec3, vec3, vec4, vec2, vec2 -- which matches verts / normals / colors /
tex1s / tex2s:

    verts   x 12 bytes      normals x 12      colors x 16
    tex1s   x  8 bytes      tex2s   x  8

STILL OPEN, and stated as such: the material / packet tail.  The text dialect
shows it is `mtl <name> { ... }` blocks and `packet N a b { adj ... }` runs --
variable-length, so no per-count byte cost exists and no least-squares fit will
ever find one.  That is the remaining work, not a defect in the above.


THE PACKET READER -- 0x3AF6A0
-----------------------------
Not visible from the driver's own vtable calls, because the packet data is
consumed by a callee.  0x3AF6A0 is 766 instructions and takes the reader object
as `a0`; the driver calls it once per packet at 0x3B7BB8.  Its literals and
reads, in order:

    "packet"    4 values        -- the 4th is unconditional in binary; the
                                   `bne t0` at 0x3AF888 skips the try-match
    "{"
    "adj" x N   SIX values each -- matching the text row `adj 0 0 0 0 -1 0`
    "reskin"    values
    primitive   u8 type, then operands
                                -- a BYTE in binary; the keywords "tri" /
                                   "str" / "stp" in text, compared at
                                   0x3AFDD0 / 0x3AFDEC / 0x3AFE08
    "mtx"       the matrix section

Adjacency post-processing is 0x3B8780: two `u16` counts at +0 and +2 and a
`u16` array at +4.

EVERY VALUE IS WIDTH-SWITCHED, AND THE WIDTH IS NOT IN THE FILE
---------------------------------------------------------------
`beq s7,zero -> +0x14 (u16); else +0x18 (u32)` at 0x3AF804, 0x3AFA20 and
throughout.  Primitive operands add a third width -- +0x10 (u8) appears
alongside them at 0x3AFE34-0x3AFF6C.

**0x3AF6A0 never writes `s7`** -- checked across all 766 of its instructions --
so it inherits it.  The driver sets `s7 = t2` (0x3B6E64); the dispatch passes
`t2 = s6`; and the dispatch sets `s6 = t1` (0x3B6A28), its own sixth
parameter.  **The index width is handed to the parser by the resource loader.
A `.mod` file does not record it.**

That is worth more than it looks: no amount of corpus work can recover the
width from the files, because the information is not in them.  It is a second,
independent reason the statistical route could never have converged.  A
standalone reader has to infer it -- `u16` suffices whenever the counts fit in
16 bits, and every count is in the header before the packets start.

STILL OPEN: the operand count per primitive type, and which caller flag bit
selects each width.  The packet section is structurally mapped but NOT yet
walked against the corpus.  My first two attempts used FIVE values per `adj`
and scored 953 / 2,695; the count is six.

Read-only.  Takes no lock.
"""
import argparse, collections, json, os, random, struct, sys, zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from rdrstudio.iso9660 import Iso9660
from rdrstudio import dave

ISO = os.path.join(os.path.dirname(__file__), '..', '..',
                   '01_Vanilla', 'RDR_VANILLA.iso')

HEADER = 58                  # 14-byte version line + 11 u32
FIELDS = ('verts', 'normals', 'colors', 'tex1s', 'tex2s', 'tangents',
          'materials', 'adjuncts', 'primitives', 'matrices', 'reskins')
ELEM = dict(verts=12, normals=12, colors=16, tex1s=8, tex2s=8)
STUB = 0xCCCCCCCC            # the two 70-byte empty stubs
NAME_END = (0x20, 0x0a, 0x0d, 0x09, 0x00)   # +0x0C skips 32/10/13/9


def mods(iso):
    seen = set()
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b'Dave', b'DAVE'):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            if not e.name.lower().endswith('.mod') or e.name in seen:
                continue
            seen.add(e.name)
            raw = d.backing.read_at(e.offset, e.size_comp)
            try:
                b = zlib.decompress(raw, -15) if e.compressed else raw
            except zlib.error:
                continue
            yield e.name, b


def header(b):
    """(version_code, counts dict) or None if this is not a binary .mod."""
    if not b.startswith(b'version: 2.1') or len(b) < HEADER:
        return None
    code = b[12] - 38                      # '0' -> 10, '2' -> 12
    u = struct.unpack_from('<11I', b, 14)
    return code, dict(zip(FIELDS, u))


def vertices(b, counts):
    """The vertex array, straight off the layout."""
    n = counts['verts']
    if n == 0 or HEADER + n * 12 > len(b):
        return []
    f = struct.unpack_from('<%df' % (n * 3), b, HEADER)
    return [f[i:i + 3] for i in range(0, len(f), 3)]


def read_name(b, o):
    """Slot +0x0C: a token terminated by space/CR/LF/TAB (0x25FF40 compares
    against 32, 13, 10, 9).  The SAME routine serves both vtables, which is
    why a binary `.mod` still carries ASCII material and texture names."""
    s = o
    while o < len(b) and b[o] not in NAME_END:
        o += 1
    name = b[s:o].decode('latin-1', 'replace')
    # 0x25FF40 LOOPS over whitespace, so the terminator is one-or-more.
    # Consuming exactly one fails on one file in 2,695: `pistol01.mod` writes
    # `scorpion` followed by TWO spaces, so the second space became the low
    # byte of the next u32 and its `attributes` read 32 instead of 0.
    while o < len(b) and b[o] in NAME_END:
        o += 1
    return name, o


def materials(b, counts):
    """The material block, straight off the driver at 0x3B7460-0x3B76A0.

        name                       -- +0x0C read_name
        u32 packets                -- +0x38 "packets:"
        u32 primitives             -- +0x38 "primitives:"
        u32 textures               -- +0x38 "textures:"
        u32 illum                  -- +0x34 "illum:", then +0x18 u32 in BINARY
                                      (text takes the +0x0C word branch and
                                       strcmps "emit"/"diffuse")
        vec4 ambient/diffuse/specular  -- +0x48 x3 in BINARY; text uses +0x44
                                          (vec3), branch at 0x3B75F0 on t0
        textures x name
        u32 attributes             -- ONCE per material, not per texture

    That last line cost two wrong walks.  With `textures: 1` the two readings
    are indistinguishable; `revolver01.mod` has a material with `textures: 0`
    and still carries the u32, which settles it.
    """
    o = HEADER + sum(counts[k] * ELEM[k] for k in ELEM)
    out = []
    for _ in range(counts['materials']):
        name, o = read_name(b, o)
        pk, pr, tx, il = struct.unpack_from('<4I', b, o)
        o += 16
        cols = struct.unpack_from('<12f', b, o)
        o += 48
        texs = []
        for _ in range(tx):
            tn, o = read_name(b, o)
            texs.append(tn)
        attrs = struct.unpack_from('<I', b, o)[0]
        o += 4
        out.append(dict(name=name, packets=pk, primitives=pr, illum=il,
                        ambient=cols[0:4], diffuse=cols[4:8],
                        specular=cols[8:12], textures=texs, attributes=attrs))
    return out, o


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    iso = Iso9660(ISO)
    rows = []
    for name, b in mods(iso):
        h = header(b)
        if h and h[1]['reskins'] != STUB:
            rows.append((name, h[0], h[1], b))
    iso.close()

    codes = collections.Counter(c for _, c, _, _ in rows)
    good = bad = 0
    extent = []
    nonneg = 0
    remainders = []
    badex = []

    for name, code, c, b in rows:
        vs = vertices(b, c)
        if vs:
            flat = [v for t in vs for v in t]
            if all(v == v and abs(v) < 1e4 for v in flat):
                good += 1
                xs, ys, zs = flat[0::3], flat[1::3], flat[2::3]
                extent.append(max(max(xs) - min(xs), max(ys) - min(ys),
                                  max(zs) - min(zs)))
            else:
                bad += 1
                if len(badex) < 4:
                    badex.append((name, c['verts']))
        body = sum(c[k] * ELEM[k] for k in ELEM)
        r = len(b) - HEADER - body
        remainders.append(r)
        if r >= 0:
            nonneg += 1

    # CONTROL: the same sanity test one byte off. If +58 is merely "a place
    # where floats happen to look fine", +59 should score similarly.
    rnd = random.Random(0)
    ctl_ok = ctl_n = 0
    for name, code, c, b in rnd.sample(rows, min(400, len(rows))):
        n = c['verts']
        if n == 0 or HEADER + 1 + n * 12 > len(b):
            continue
        ctl_n += 1
        f = struct.unpack_from('<%df' % (n * 3), b, HEADER + 1)
        if all(v == v and abs(v) < 1e4 for v in f):
            ctl_ok += 1

    # the material block, walked on every file
    mat_ok = mat_bad = 0
    tails = []
    for name, code, c, b in rows:
        try:
            mats, end = materials(b, c)
            if end <= len(b) and all(m['name'] for m in mats):
                mat_ok += 1
                tails.append(len(b) - end)
            else:
                mat_bad += 1
        except Exception:
            mat_bad += 1
    tails.sort()

    extent.sort()
    remainders.sort()
    res = dict(files=len(rows), version_codes=dict(codes),
               vertices_sane=[good, good + bad], bad_examples=badex,
               control_offset_59=[ctl_ok, ctl_n],
               remainder_nonnegative=[nonneg, len(rows)],
               remainder_min=remainders[0] if remainders else None,
               extent_median=round(extent[len(extent) // 2], 3) if extent else None,
               materials_walk=[mat_ok, mat_bad],
               tail_after_materials=dict(
                   min=tails[0], median=tails[len(tails) // 2], max=tails[-1])
               if tails else None)
    if args.json:
        print(json.dumps(res, indent=1)); return

    print('binary .mod files (the 2 stubs excluded): %d' % len(rows))
    print('version codes from buf[12]-38: %s' % dict(codes))
    print()
    print('VERTEX BLOCK AT +%d, verts x 12 bytes' % HEADER)
    print('   all floats finite and |v| < 10000 : %d / %d  (%.2f%%)'
          % (good, good + bad, 100 * good / (good + bad) if good + bad else 0))
    print('   CONTROL, same test read at +%d   : %d / %d  (%.1f%%)'
          % (HEADER + 1, ctl_ok, ctl_n, 100 * ctl_ok / ctl_n if ctl_n else 0))
    print('   model bounding-box extent, median %.2f units' % res['extent_median'])
    print()
    print('SIZE CONSISTENCY  header + verts*12 + normals*12 + colors*16'
          ' + tex1s*8 + tex2s*8')
    print('   remainder non-negative : %d / %d   (min %d bytes)'
          % (nonneg, len(rows), remainders[0]))
    print()
    print('MATERIAL BLOCK  name + 4xu32 + 3xvec4 + textures + u32 attributes')
    print('   walks cleanly to a valid end : %d / %d' % (mat_ok, mat_ok + mat_bad))
    print('   bytes left afterwards        : min %d  median %d  max %d'
          % (tails[0], tails[len(tails) // 2], tails[-1]))
    print()
    print('PACKET SECTION -- structure mapped from the code (0x3AF6A0), not')
    print('yet walked on the corpus. See the module docstring.')
    print()
    print('That remainder is the PACKET section and it is NOT settled.')
    print('What is known: it is bulk-read rather than field-by-field, and the')
    print('routine that consumes it is 0x3B8780 -- it takes two u16 counts at')
    print('+0 and +2 and a u16 array at +4, and builds an adjacency map. The')
    print('driver then does stream work at 0x19A408 and rebuilds a filename')
    print('around a 46 (\'.\'), so a second stream may be involved. Not walked.')


if __name__ == '__main__':
    main()
