#!/usr/bin/env python3
"""`rsc.append_chunk` against every `.rsc` on the disc. Read-only.

    python3 scripts/verify_rsc_insert.py

M18's acceptance criterion is *"the resource index and the model->texture
binding still verify"*. This is that claim, asserted rather than described, on
all 623 bundles that carry a table rather than on one convenient file.

For each bundle: append a chunk, then check that

  * the result passes `check_bundle` -- the writer's output satisfies the
    reader's own invariants;
  * **every original chunk comes back byte-identical**, which is the real
    claim. Not "the file still parses": the same bytes, chunk by chunk;
  * the new chunk reads back as the payload, zero-padded to 16;
  * the new chunk's index is the old count, so its 1-based `resourceIndex` is
    count + 1 and **no existing index moved**;
  * `texture_table()` is unchanged, and `model_texture_map()` is unchanged for
    every model that already existed.

The last two are the binding half of the criterion. The one thing appending
DOES decide is the new model's own texture -- it inherits the last TEX0 in the
bundle -- and that is asserted too, so the behaviour is pinned rather than
assumed.

The 4 bundles whose header has no room for another entry are the interesting
ones: they take the growth path, every chunk moves, and every stored end is
rewritten. The run fails if the disc stops containing such a file, because
then this script would be silently testing one path instead of two.

Reads `01_Vanilla/RDR_VANILLA.iso` and writes nothing to it.
"""
import collections
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from rdrstudio import dave                        # noqa: E402
from rdrstudio.iso9660 import Iso9660             # noqa: E402
from rdrstudio.rsc import (MAGIC, Rsc, append_chunk,  # noqa: E402
                           check_bundle)

ISO = os.path.join(os.path.dirname(__file__), '..', '..',
                   '01_Vanilla', 'RDR_VANILLA.iso')

FAILURES = []


def check(ok, what, detail=""):
    if not ok:
        FAILURES.append(what)
        print("  FAIL " + what + (("  -- " + detail) if detail else ""))
    return ok


def collect(iso):
    out = {}
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b'Dave', b'DAVE'):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            if not e.name.lower().endswith('.rsc') or e.name in out:
                continue
            raw = d.backing.read_at(e.offset, e.size_comp)
            try:
                b = zlib.decompress(raw, -15) if e.compressed else raw
            except zlib.error:
                continue
            if b[:4] == MAGIC:
                out[e.name] = b
    return out


PAYLOAD = b"M18 appended chunk -- not real geometry, just bytes." * 3


def tex_key(row):
    """texture_table() row -> a comparable value.

    `Chunk` defines no `__eq__`, so comparing two `texture_table()` results
    with `==` compares object IDENTITY and is false for every non-empty table,
    whatever the bytes say. The first version of this script did exactly that
    and "passed" on 269 of 623 bundles -- every one of which has an EMPTY
    texture table, so it was comparing [] with []. It never once compared two
    real tables. Hence `tex_rows`, printed below: a check that cannot say how
    much it read is not a check.

    Chunk OFFSETS are deliberately excluded. When the header grows, every
    chunk legitimately shifts by the same delta; identity of index, tag and
    size is the invariant, and the bytes themselves are asserted separately.
    """
    idx, info, mip, pal = row
    return (idx, tuple(sorted(info.items())),
            (mip.index, mip.tag, mip.size),
            (pal.index, pal.tag, pal.size))


def tex_view(rsc):
    return [tex_key(r) for r in rsc.texture_table()]


def main():
    iso = Iso9660(ISO)
    try:
        files = collect(iso)
    finally:
        iso.close()

    tabled = {n: b for n, b in files.items()
              if struct.unpack_from("<I", b, 4)[0] > 0}
    print("bundles with a table: %d" % len(tabled))

    grew = same = 0
    identical = reparse = idx_ok = tex_ok = map_ok = newbind_ok = 0
    tex_rows = map_rows = 0
    refused = []

    for name, b in sorted(tabled.items()):
        pre = Rsc(b, label=name)
        pre_tex = tex_view(pre)
        pre_map = pre.model_texture_map()
        count, hsize = struct.unpack_from("<II", b, 4)

        try:
            out, index = append_chunk(b, "MOD0", PAYLOAD, label=name)
        except Exception as ex:                       # noqa: BLE001
            refused.append((name, str(ex)))
            continue

        new_hsize = struct.unpack_from("<I", out, 8)[0]
        if new_hsize != hsize:
            grew += 1
        else:
            same += 1

        if check(not check_bundle(out, name), "result verifies: %s" % name,
                 "; ".join(check_bundle(out, name))):
            reparse += 1

        post = Rsc(out, label=name)
        if check(len(post) == count + 1, "chunk count grew by one: %s" % name):
            pass
        if check(index == count, "new index is the old count: %s" % name,
                 "index=%d count=%d" % (index, count)):
            idx_ok += 1

        # the real claim: every original chunk, byte for byte
        ok = all(post.raw(i) == pre.raw(i) for i in range(count))
        if check(ok, "every original chunk is byte-identical: %s" % name):
            identical += 1

        want = PAYLOAD + b"\x00" * ((-len(PAYLOAD)) % 16)
        check(post.raw(index) == want, "new chunk reads back: %s" % name)

        if check(tex_view(post) == pre_tex,
                 "texture_table unchanged: %s" % name):
            tex_ok += 1
        if pre_tex:
            tex_rows += 1
        if pre_map:
            map_rows += 1

        post_map = post.model_texture_map()
        if check(all(post_map.get(i) == v for i, v in pre_map.items()),
                 "model->texture binding unchanged for existing models: %s"
                 % name):
            map_ok += 1

        # and the one thing appending decides, pinned rather than assumed
        order = {t[0]: n for n, t in enumerate(pre.texture_table())}
        last_tex = None
        for c in pre.chunks:
            if c.tag == "TEX0" and c.index in order:
                last_tex = order[c.index]
        if check(post_map.get(index) == last_tex,
                 "appended MOD0 inherits the last TEX0: %s" % name,
                 "got %r want %r" % (post_map.get(index), last_tex)):
            newbind_ok += 1

    n = len(tabled)
    print()
    print("  result verifies                        %d / %d" % (reparse, n))
    print("  every original chunk byte-identical    %d / %d" % (identical, n))
    print("  new index == old count                 %d / %d" % (idx_ok, n))
    print("  texture_table unchanged                %d / %d" % (tex_ok, n))
    print("  existing model->texture unchanged      %d / %d" % (map_ok, n))
    print("  appended MOD0 inherits last TEX0       %d / %d" % (newbind_ok, n))
    print()
    print("  ...of which actually had a texture table    %d" % tex_rows)
    print("  ...of which actually had a model->tex map   %d" % map_rows)
    check(tex_rows > 0, "the texture_table check compared real tables",
          "every bundle had an empty table, so it compared nothing")
    check(map_rows > 0, "the model->texture check compared real maps",
          "every bundle had an empty map, so it compared nothing")
    print()
    print("  header stayed put %d,  header grew %d" % (same, grew))
    check(grew > 0, "the header-growth path was exercised at all",
          "no bundle on the disc needed it -- this run tested one path, not two")
    if refused:
        print("  refused: %d" % len(refused))
        for nm, why in refused[:5]:
            print("     %s: %s" % (nm, why))

    print()
    if FAILURES:
        print("FAILED: %d" % len(FAILURES))
        for f in collections.Counter(
                x.split(":")[0] for x in FAILURES).most_common():
            print("   %s x %d" % f)
        return 1
    print("all checks passed over %d bundles" % n)
    return 0


if __name__ == '__main__':
    sys.exit(main())
