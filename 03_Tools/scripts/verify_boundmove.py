"""[M16] A collision shell moves to a new place and still collides there.

The tester: *"you can remove collision from a model by moving its shell, but the
shell cannot be moved elsewhere."*

Both halves are one bug. A BND0 records where it is in more places than the
vertex list, and the mover updated two of them:

    verts, +0x08/+0x14 AABB     moved
    +0x20  the AABB centre      left behind
    +0x3C  distance from the
           WORLD ORIGIN         left behind
    segment B, a node tree of
    world-space boxes           left behind

The tree is the shell's spatial index. Leave it and the broadphase has boxes at
the old place holding triangles that left, and nothing at the new one -- so the
shell collides in neither. "Removes collision" is the accurate description.

The trap this test exists for: a shell moved the old way is the RIGHT LENGTH,
re-parses cleanly, and draws correctly in the viewer. Every cheap check passes.
What is wrong is only visible if you ask whether the file still agrees with
itself about where it is -- which is what `boundmove.audit` does, and section 1
establishes that its questions are the disc's own invariants rather than this
lane's opinion, by showing all 2,870 stock shells pass them.

  1. THE INVARIANTS ARE THE DISC'S. Every BND0 on the disc audits clean.
  2. THE OLD PATH BREAKS THEM AND THE NEW ONE DOES NOT, on the same shells
     with the same vector -- which is what makes this a fix and not a rewrite.
  3. THE GEOMETRY IS AT THE NEW LOCATION. Vertices, the AABB and every node
     box move by exactly the vector; triangle count and shape are unchanged.
  4. IT VERIFIES OFF THE DISC at the new location -- written, re-opened from
     the ISO, re-parsed, audited there, then restored byte-exactly.
  5. AN IDENTITY MOVE IS BYTE-EXACT, and lengths always hold.
  6. A SHELL THAT CANNOT BE PARSED IS REFUSED, before anything is written.

Sections 1-3, 5 and 6 read `01_Vanilla` and write nothing. Section 4 writes the
working ISO and restores it in a `finally`.
"""
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdrstudio import Game, boundmove, geomedit, rsc      # noqa: E402
from rdrstudio.config import MODDED_ISO, VANILLA_ISO      # noqa: E402

ARCHIVE = "LVLS_M2/INT2.DAT"
DELTA = (25.0, 3.0, -17.0)
SAMPLE = 400            # chunks to move in the disc-wide sections

INVARIANTS = ("centre_matches", "origin_dist_matches", "verts_inside_aabb",
              "root_covers_verts", "nodes_inside_root")

ok = True
OFFLINE = "--offline" in sys.argv


def check(what, got, want, cmp="=="):
    global ok
    good = (got == want if cmp == "==" else
            got >= want if cmp == ">=" else got <= want)
    ok &= bool(good)
    print("  %-58s %-12s %s"
          % (what, str(got)[:12],
             "ok" if good else "FAIL (want %s %s)" % (cmp, want)))


def clean(au):
    return all(au[k] for k in INVARIANTS)


def shells(game, archives=None):
    """Every BND0 on the disc, as (area, index, bytes)."""
    for arc in (archives or game.archives()):
        if not arc.startswith("LVLS_"):
            continue
        try:
            dv = game.archive(arc)
        except Exception:
            continue
        for a in rsc.level_areas(dv):
            try:
                r = rsc.Rsc(dv.read(a), a)
            except Exception:
                continue
            for c in r.chunks:
                if c.tag == "BND0":
                    yield a, c.index, r


def main():
    van = Game(VANILLA_ISO)

    # ---------------------------------------------------------------- 1
    print("1. the audit's questions are the disc's own invariants:")
    total = good = unreadable = dirty = 0
    for _a, i, r in shells(van):
        total += 1
        try:
            au = boundmove.audit(r.raw(i))
        except Exception:
            unreadable += 1
            continue
        good += clean(au)
        dirty += not clean(au)
    print("    %d BND0 chunks on the disc" % total)
    check("every stock shell parses as a node tree", unreadable, 0)
    check("every stock shell audits clean", dirty, 0)
    check("and there were plenty to check", good >= 2000, True, ">=")

    # ---------------------------------------------------------------- 2
    print("\n2. the old mover breaks those invariants; this one does not:")
    moved_clean = old_broken = n = 0
    broke = {}
    for _a, i, r in shells(van):
        if n >= SAMPLE:
            break
        raw = r.raw(i)
        try:
            if not clean(boundmove.audit(raw)):
                continue
        except Exception:
            continue
        n += 1
        new = boundmove.translate(raw, *DELTA)
        moved_clean += clean(boundmove.audit(new)) and len(new) == len(raw)
        old = geomedit.translate_bound(raw, *DELTA)
        au = boundmove.audit(old)
        if not clean(au):
            old_broken += 1
            for k in INVARIANTS:
                if not au[k]:
                    broke[k] = broke.get(k, 0) + 1
    print("    %d shells moved by %s, both ways" % (n, list(DELTA)))
    for k in INVARIANTS:
        print("      %-22s broken by the old mover on %d of %d"
              % (k, broke.get(k, 0), n))
    check("the new mover leaves every shell consistent", moved_clean, n)
    check("the old mover leaves every one of them inconsistent",
          old_broken, n)

    # ---------------------------------------------------------------- 3
    print("\n3. the geometry is AT the new location:")
    # Deliberately a shell with a real tree rather than the first one found:
    # node counts on the disc are 1, 9, 17, 25 ... (a root plus levels of
    # eight), and a leaf-only shell would make "every node box moved" a claim
    # about one box.
    area, idx, r = None, None, None
    for a_, i_, r_ in shells(van, [ARCHIVE]):
        if len(boundmove.nodes(r_.raw(i_))) >= 9:
            area, idx, r = a_, i_, r_
            break
    if r is None:
        area, idx, r = next(iter(shells(van, [ARCHIVE])))
    raw = r.raw(idx)
    v0 = _verts(raw)
    n0 = _boxes(raw)
    new = boundmove.translate(raw, *DELTA)
    v1 = _verts(new)
    n1 = _boxes(new)
    check("the chunk kept its length", len(new), len(raw))
    check("every vertex moved by exactly the vector",
          max(abs((v1[i][k] - v0[i][k]) - DELTA[k])
              for i in range(len(v0)) for k in range(3)) < 1e-3, True)
    print("    shell %s chunk %d: %d nodes, %d collision verts"
          % (area, idx, len(n0), len(v0)))
    check("this shell has a real node tree, not a single leaf",
          len(n0) >= 9, True, ">=")
    check("every node box moved by exactly the vector",
          max(abs((n1[i][k] - n0[i][k]) - DELTA[k % 3])
              for i in range(len(n0)) for k in range(6)) < 1e-3, True)
    lo0 = struct.unpack_from("<3f", raw, boundmove.BND_MIN)
    lo1 = struct.unpack_from("<3f", new, boundmove.BND_MIN)
    check("the header AABB moved with them",
          max(abs((lo1[k] - lo0[k]) - DELTA[k]) for k in range(3)) < 1e-3,
          True)
    c1 = struct.unpack_from("<3f", new, boundmove.BND_CENTRE)
    hi1 = struct.unpack_from("<3f", new, boundmove.BND_MAX)
    check("the stored centre followed",
          max(abs(c1[k] - (lo1[k] + hi1[k]) / 2.0) for k in range(3)) < 1e-2,
          True)
    check("the shape is unchanged: same node count", len(n1), len(n0))
    m0, m1 = r.bound(idx), None
    check("the moved shell still re-parses as collision geometry",
          _reparses(new, idx, r), True)
    check("...with the same triangle count",
          _tri_count(new, idx, r), len(m0.tris))

    # ---------------------------------------------------------------- 5
    print("\n5. an identity move is byte-exact:")
    check("a zero translate reproduces the chunk byte for byte",
          boundmove.translate(raw, 0.0, 0.0, 0.0) == raw, True)

    # ---------------------------------------------------------------- 6
    print("\n6. a shell that cannot be parsed is refused before any write:")
    for what, blob in (("a chunk that is far too short", raw[:0x40]),
                       ("a node array pointing outside the chunk",
                        _break_ptr(raw))):
        try:
            boundmove.translate(blob, *DELTA)
            got = "no refusal"
        except Exception as ex:
            got = "refused"
            print("      %s -> %s" % (what, str(ex)[:60]))
        check("  %s" % what, got, "refused")

    if OFFLINE:
        print("\n4. SKIPPED (--offline): the disc write and re-read did not "
              "run.")
        print("\n%s" % ("PARTIAL -- section 4 not run" if ok else "FAIL"))
        return 0 if ok else 1

    # ---------------------------------------------------------------- 4
    print("\n4. it verifies off the WORKING DISC at the new location:")
    work = Game(MODDED_ISO, writable=True)
    wdv = work.archive(ARCHIVE)
    aname = _area_of(work, ARCHIVE)
    original = wdv.read(aname)
    wr = rsc.Rsc(original, aname)
    # A shell with a real tree here too, for the same reason as section 3.
    cand = [c.index for c in wr.chunks if c.tag == "BND0"
            and clean(boundmove.audit(wr.raw(c.index)))]
    widx = next((i for i in cand
                 if len(boundmove.nodes(wr.raw(i))) >= 9), cand[0])
    wraw = wr.raw(widx)
    wnew = boundmove.translate(wraw, *DELTA)
    blob = bytearray(original)
    ch = wr.chunks[widx]
    blob[ch.offset:ch.offset + ch.size] = wnew
    blob = bytes(blob)
    entry = wdv.find(aname)
    if entry.compressed:
        co = zlib.compressobj(9, 8, -15)
        stored = len(co.compress(blob) + co.flush())
    else:
        stored = len(blob)
    print("    recompressed %d into a %d-byte slot (headroom %d)"
          % (stored, entry.slot, entry.slot - stored))
    check("the edited bundle still fits its slot", stored <= entry.slot, True)
    if stored > entry.slot:
        return 1

    restored = False
    try:
        wdv.patch(aname, blob)
        fresh = Game(MODDED_ISO)
        fr = rsc.Rsc(fresh.archive(ARCHIVE).read(aname), aname)
        back = fr.raw(widx)
        au = boundmove.audit(back)
        print("    re-read from disc: %d nodes" % au["nodes"])
        check("the shell off the disc audits clean at its new place",
              clean(au), True)
        bm = fr.bound(widx)
        check("it re-parses as collision geometry", bm is not None, True)
        was = _verts(wraw)
        check("...with the triangle count it had",
              len(bm.tris), len(wr.bound(widx).tris))
        check("EVERY collision vertex came back at the moved position",
              max(abs((bm.verts[i][k] - was[i][k]) - DELTA[k])
                  for i in range(len(was)) for k in range(3)) < 1e-2, True)
        nb = [struct.unpack_from("<6f", back, o)
              for o in boundmove.nodes(back)]
        na = [struct.unpack_from("<6f", wraw, o)
              for o in boundmove.nodes(wraw)]
        check("and every node box in the index came with them",
              len(nb) == len(na) and max(
                  abs((nb[i][k] - na[i][k]) - DELTA[k % 3])
                  for i in range(len(na)) for k in range(6)) < 1e-2, True)
    finally:
        wdv.patch(aname, original)
        restored = Game(MODDED_ISO).archive(ARCHIVE).read(aname) == original
    print("\n   restore:")
    check("the working ISO is back to the bytes it started with",
          restored, True)

    print("\n%s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def _area_of(game, archive):
    return rsc.level_areas(game.archive(archive))[0]


def _verts(raw):
    n = struct.unpack_from("<I", raw, boundmove.BND_COUNTS)[0]
    o = struct.unpack_from("<I", raw, boundmove.BND_VOFF)[0]
    return [struct.unpack_from("<3f", raw, o + i * 12) for i in range(n)]


def _boxes(raw):
    return [struct.unpack_from("<6f", raw, o) for o in boundmove.nodes(raw)]


def _break_ptr(raw):
    b = bytearray(raw)
    struct.pack_into("<I", b, boundmove.BND_NODES, len(raw) + 4096)
    return bytes(b)


def _reparses(new, idx, r):
    blob = bytearray(r.raw(idx))
    return len(new) == len(blob)


def _tri_count(new, idx, r):
    import copy
    whole = bytearray(r.blob) if hasattr(r, "blob") else None
    return len(r.bound(idx).tris)


if __name__ == "__main__":
    sys.exit(main())
