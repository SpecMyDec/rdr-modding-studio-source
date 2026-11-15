"""[M6] A multi-selection moves and scales as one thing, and keeps its shapes.

The tester: *"Mass model edit: multi-select move + proportional scale."*

The milestone is not "call the single-model route N times". Doing that scales
every model about its OWN centre, so the models swell while the layout stands
still and they grow into each other -- the arrangement is destroyed by an
operation whose whole purpose is to preserve it. A mass edit turns about ONE
pivot for the selection: each model keeps its own proportions, and its offset
from the pivot scales with everything else.

Both behaviours produce identically-sized models, so nothing about a single
model can tell them apart. Section 1 measures the only thing that can -- the
gap BETWEEN models -- and that is what makes this milestone a milestone.

What is asserted here, against the real level geometry on the disc:

  1. THE SELECTION SCALES AS ONE. Two models, one uniform factor: the
     distance between their centres must scale by exactly that factor, while
     the same op through per-model pivots leaves it at x1.0000.
  2. PROPORTIONS SURVIVE. Every selected model's own bounding box must keep
     its aspect ratios, and each of its axes must scale by exactly the
     factor. Ratios, not sizes -- sizes only prove the factor was applied,
     ratios prove the SHAPE came through.
  3. THE CULL SPHERES GO TOO. Every part's VU cull sphere must move by the
     same transform as its vertices. Verts-only froze the game for a night
     (the 185 s A/B on 2026-08-12/13). A mass edit is where "I moved N-1 of
     the N" would hide, so it is checked on every part of every model.
  4. THE LEVEL RE-READS WITH THE NEW TRANSFORMS -- off the WORKING DISC, not
     out of a buffer. The bundle is written, re-opened from the ISO, and the
     geometry must come back at the planned boxes. It is then restored and
     the restoration is verified byte-exactly.
  5. NOTHING ELSE MOVED. Models that were not selected must come back byte
     for byte, in the same write.
  6. LENGTHS HOLD. Every chunk stays its exact size, or it would not fit the
     archive slot it came out of.
  7. A REFUSAL IS BY NAME, AND TOTAL. A selection containing something that
     cannot be moved produces NO bytes at all rather than a partial plan.

Sections 1-3 and 5-7 read `01_Vanilla` and write nothing. Section 4 writes to
the working ISO and restores it in a `finally`.
"""
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdrstudio import Game, geomedit, massedit, rsc       # noqa: E402
from rdrstudio.config import MODDED_ISO, VANILLA_ISO      # noqa: E402

ARCHIVE = "LVLS_M2/INT2.DAT"
FACTOR = 2.0
DELTA = (10.0, 0.0, -4.0)

ok = True
OFFLINE = "--offline" in sys.argv   # sections 1-3, 6, 7 only; no disc write


def check(what, got, want, cmp="=="):
    global ok
    good = (got == want if cmp == "==" else
            got >= want if cmp == ">=" else
            got <= want if cmp == "<=" else None)
    ok &= bool(good)
    print("  %-58s %-14s %s"
          % (what, str(got)[:14],
             "ok" if good else "FAIL (want %s %s)" % (cmp, want)))


def size_of(box):
    return [box[1][i] - box[0][i] for i in range(3)]


def centre_of(box):
    return [(box[0][i] + box[1][i]) / 2.0 for i in range(3)]


def dist(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


EPS32 = 2.0 ** -24      # float32 relative precision


def aspect_tolerance(box_after, size_before, slack=8.0):
    """How much aspect-ratio drift float32 alone can produce here.

    Derived, not tuned. Positions are stored as float32, so a coordinate at
    `reach` metres carries an absolute error of about `EPS32 * reach`. A
    model's aspect ratio is a quotient of its own extents, so that absolute
    error becomes a RELATIVE error of `EPS32 * reach / smallest_axis` -- which
    is why a 0.14 m prop standing 2 m from the pivot drifts a hundred times
    more than a 30 m building does, with nothing wrong in either case.

    `slack` covers the handful of ulps a translate-then-scale accumulates.
    Measured over all 388 models on the disc: the worst case sits at 8.7% of
    this bound and nothing exceeds it.

    A fixed epsilon was the wrong shape here and nearly the wrong number: the
    disc-wide worst is 7.9e-06 against the 1e-5 constant this replaces.
    """
    reach = max(max(abs(v) for v in box_after[0]),
                max(abs(v) for v in box_after[1]))
    smallest = max(min(size_before), 1e-9)
    return slack * EPS32 * reach / smallest


def movable(r, index):
    """Is this chunk a plain float level mesh this milestone can move?"""
    try:
        m = r.model(index)
    except Exception:
        return None
    if not m or not m.get("tris") or not m.get("verts"):
        return None
    if not geomedit.editable(m):
        return None
    if any(q is not None for q in (m.get("quant") or [])):
        return None
    return m


def pick(r, want=3):
    """Editable models with real extent in all three axes, biggest first.

    A flat quad is a legitimate model and the code handles it, but a degenerate
    axis makes "the ratio came through" vacuous on that axis -- so the models
    this test measures shapes with are chosen to have a shape.
    """
    out = []
    for c in r.chunks:
        if c.tag != "MOD0":
            continue
        m = movable(r, c.index)
        if not m:
            continue
        b = geomedit.bounds(m)
        s = size_of(b)
        if min(s) < 0.5:
            continue
        out.append((min(s), c.index))
    out.sort(reverse=True)
    return [i for _s, i in out[:want]]


def main():
    van = Game(VANILLA_ISO)
    dv = van.archive(ARCHIVE)
    areas = rsc.level_areas(dv)
    area = areas[0]
    blob = dv.read(area)
    r = rsc.Rsc(blob, area)
    sel = pick(r, 3)
    print("%s :: %s -- %d chunks, selection %s"
          % (ARCHIVE, area, len(r.chunks), sel))
    check("three or more models to select", len(sel) >= 3, True, ">=")
    if len(sel) < 3:
        return 1

    # ---------------------------------------------------------------- 1
    print("\n1. the selection scales as ONE thing, not as N things:")
    pair = sel[:2]
    ops = [{"op": "scale", "x": FACTOR}]
    shared = massedit.plan(r, pair, ops)
    before_gap = dist(centre_of(shared["before"][pair[0]]),
                      centre_of(shared["before"][pair[1]]))
    shared_gap = dist(centre_of(shared["after"][pair[0]]),
                      centre_of(shared["after"][pair[1]]))
    each = [massedit.plan(r, [c], ops) for c in pair]
    each_gap = dist(centre_of(each[0]["after"][pair[0]]),
                    centre_of(each[1]["after"][pair[1]]))
    print("    gap between the two models: %.3f before" % before_gap)
    print("      one shared pivot  -> %.3f  (x%.4f)"
          % (shared_gap, shared_gap / before_gap))
    print("      a pivot per model -> %.3f  (x%.4f)"
          % (each_gap, each_gap / before_gap))
    check("shared pivot scales the gap by the factor",
          round(shared_gap / before_gap, 4), FACTOR)
    check("per-model pivots leave the gap alone",
          round(each_gap / before_gap, 4), 1.0)
    same = all(
        abs(a - b) < 1e-4
        for e, c in zip(each, pair)
        for a, b in zip(size_of(e["after"][c]), size_of(shared["after"][c])))
    check("...while the models themselves are the same size either way",
          same, True)
    print("    so no measurement of a single model can tell the two apart;")
    print("    only the arrangement can, which is what mass edit is for.")

    # ---------------------------------------------------------------- 2
    print("\n2. every model keeps its own proportions:")
    p = massedit.plan(r, sel, [{"op": "translate", "x": DELTA[0],
                                "y": DELTA[1], "z": DELTA[2]},
                               {"op": "scale", "x": FACTOR}])
    check("the plan reports itself proportional", p["proportional"], True)
    over = 0
    worst_factor = 0.0
    for c in sel:
        b0, b1 = p["before"][c], p["after"][c]
        pr0 = massedit.proportions(b0)
        pr1 = massedit.proportions(b1)
        drift = max(abs(a - b) for a, b in zip(pr0, pr1))
        tol = aspect_tolerance(b1, size_of(b0))
        over += drift > tol
        fac = [size_of(b1)[i] / size_of(b0)[i] for i in range(3)]
        worst_factor = max(worst_factor, max(abs(f - FACTOR) for f in fac))
        print("    chunk %-4d aspect %s  drift %.2e of %.2e allowed   "
              "axis factors %s"
              % (c, ["%.5f" % v for v in pr0], drift, tol,
                 ["%.5f" % v for v in fac]))
    check("aspect ratios unchanged beyond float32", over, 0)
    check("every axis scaled by exactly the factor", worst_factor < 1e-4, True)

    # a per-axis scale is NOT proportional, and must say so
    skew = massedit.plan(r, sel, [{"op": "scale", "x": 2.0, "y": 1.0,
                                   "z": 1.0}])
    check("a per-axis scale reports proportional=False",
          skew["proportional"], False)

    # ---------------------------------------------------------------- 3
    print("\n3. the cull spheres move with the vertices:")
    tp = massedit.plan(r, sel, [{"op": "translate", "x": DELTA[0],
                                 "y": DELTA[1], "z": DELTA[2]}])
    spheres = bad = 0
    for c in sel:
        old, new = r.raw(c), tp["chunks"][c]
        for part in (r.parts(c) or []):
            off = part["offset"] + 12
            if off + 12 > len(old):
                continue
            a = struct.unpack_from("<3f", old, off)
            b = struct.unpack_from("<3f", new, off)
            spheres += 1
            if max(abs((b[i] - a[i]) - DELTA[i]) for i in range(3)) > 1e-4:
                bad += 1
    print("    %d cull sphere(s) across %d model(s)" % (spheres, len(sel)))
    check("there were spheres to move", spheres >= len(sel), True, ">=")
    check("every one moved by the same vector as its verts", bad, 0)

    # ---------------------------------------------------------------- 6
    print("\n6. lengths hold and an identity plan is byte-exact:")
    check("every chunk kept its length", p["lengths_held"], True)
    noop = massedit.plan(r, sel, [])
    check("a no-op plan reproduces every chunk byte for byte",
          all(noop["chunks"][c] == r.raw(c) for c in sel), True)
    noop2 = massedit.plan(r, sel, [{"op": "translate", "x": 0,
                                    "y": 0, "z": 0}])
    check("so does an explicit zero translate",
          all(noop2["chunks"][c] == r.raw(c) for c in sel), True)

    # ---------------------------------------------------------------- 7
    print("\n7. a refusal names the model and produces nothing:")
    nonmod = next((c.index for c in r.chunks if c.tag != "MOD0"), None)
    for what, chunks, op in (
            ("a chunk that is not geometry", [sel[0], nonmod], None),
            ("a chunk that is not there", [sel[0], 999999], None),
            ("an unknown op", sel, [{"op": "wobble"}]),
    ):
        if chunks and None in chunks:
            continue
        try:
            massedit.plan(r, chunks, op if op is not None else [])
            got = "no refusal"
        except massedit.Refused as ex:
            got = "refused"
            print("      %s -> %s" % (what, ex))
        check("  %s" % what, got, "refused")

    # ---------------------------------------------------------------- 8
    # One area proving the properties is an anecdote. These are claims about
    # the format, so they are measured on every level area the disc has.
    print("\n8. the same properties across every level area on the disc:")
    areas = planned = sph = sph_skipped = partless = 0
    len_bad = drift_over = plan_failed = 0
    worst_drift = 0.0
    for arcname in van.archives():
        if not arcname.startswith("LVLS_"):
            continue
        try:
            adv = van.archive(arcname)
        except Exception:
            continue
        for aname in rsc.level_areas(adv):
            try:
                ar = rsc.Rsc(adv.read(aname), aname)
            except Exception:
                continue
            areas += 1
            some = []
            for ch in ar.chunks:
                if ch.tag != "MOD0":
                    continue
                if movable(ar, ch.index):
                    some.append(ch.index)
                if len(some) >= 4:
                    break
            if len(some) < 2:
                continue
            try:
                ap = massedit.plan(ar, some,
                                   [{"op": "translate", "x": 3.0, "y": -1.0,
                                     "z": 2.0},
                                    {"op": "scale", "x": 1.5}])
            except massedit.Refused:
                plan_failed += 1
                continue
            planned += len(some)
            if not ap["lengths_held"]:
                len_bad += 1
            for ci in some:
                b0, b1 = ap["before"][ci], ap["after"][ci]
                p0, p1 = (massedit.proportions(b0), massedit.proportions(b1))
                if p0 and p1:
                    d = max(abs(x - y) for x, y in zip(p0, p1))
                    worst_drift = max(worst_drift, d)
                    if d > aspect_tolerance(b1, size_of(b0)):
                        drift_over += 1
                prs = ar.parts(ci) or []
                if not prs:
                    partless += 1
                craw = ar.raw(ci)
                for pt in prs:
                    if pt["offset"] + 24 > len(craw):
                        sph_skipped += 1
                    else:
                        sph += 1
    print("    %d areas, %d models planned, %d cull spheres"
          % (areas, planned, sph))
    check("every level area was reachable", areas >= 90, True, ">=")
    check("plans that refused a movable selection", plan_failed, 0)
    check("chunks that changed length", len_bad, 0)
    check("models with no part records at all", partless, 0)
    check("cull spheres move_spheres would have skipped", sph_skipped, 0)
    print("    worst aspect drift anywhere on the disc: %.3e" % worst_drift)
    check("models drifting past what float32 explains", drift_over, 0)

    # ---------------------------------------------------------------- 4/5
    if OFFLINE:
        print("\n4-5. SKIPPED (--offline): the disc write and re-read did "
              "not run.")
        print("\n%s" % ("PARTIAL -- sections 4-5 not run" if ok else "FAIL"))
        return 0 if ok else 1

    print("\n4. the level re-reads off the WORKING DISC with the new "
          "transforms:")
    work = Game(MODDED_ISO, writable=True)
    wdv = work.archive(ARCHIVE)
    original = wdv.read(area)
    wr = rsc.Rsc(original, area)
    wsel = pick(wr, 3)
    untouched = [c.index for c in wr.chunks
                 if c.tag == "MOD0" and c.index not in wsel][:5]
    wp = massedit.plan(wr, wsel, [{"op": "translate", "x": DELTA[0],
                                   "y": DELTA[1], "z": DELTA[2]},
                                  {"op": "scale", "x": FACTOR}])
    new_blob = massedit.splice(original, wr, wp)
    check("the bundle is the same size", len(new_blob), len(original))
    check("the bundle's bytes actually changed", new_blob != original, True)

    # An in-place float patch keeps the LENGTH, not the compressed size --
    # different float bytes pack differently. Check the slot before writing,
    # so a genuine overflow reads as a measured refusal here rather than as a
    # SlotOverflow traceback in a shared suite somebody else has to diagnose.
    entry = wdv.find(area)
    if entry.compressed:
        co = zlib.compressobj(9, 8, -15)
        stored = len(co.compress(new_blob) + co.flush())
    else:
        stored = len(new_blob)
    print("    recompressed %d into a %d-byte slot (headroom %d)"
          % (stored, entry.slot, entry.slot - stored))
    check("the edited bundle still fits its archive slot",
          stored <= entry.slot, True)
    if stored > entry.slot:
        return 1

    restored = False
    try:
        wdv.patch(area, new_blob)
        # Re-open the ISO from scratch: nothing cached, nothing in hand.
        fresh = Game(MODDED_ISO)
        fr = rsc.Rsc(fresh.archive(ARCHIVE).read(area), area)
        worst = 0.0
        for c in wsel:
            got = geomedit.bounds(fr.model(c))
            want = wp["after"][c]
            worst = max(worst, max(abs(got[j][i] - want[j][i])
                                   for j in (0, 1) for i in range(3)))
        print("    re-read %d model(s) from disc; worst box difference %.3e"
              % (len(wsel), worst))
        check("the disc returns the planned geometry", worst < 1e-3, True)
        print("\n5. and nothing that was not selected moved:")
        same = sum(1 for c in untouched if fr.raw(c) == wr.raw(c))
        print("    %d unselected model(s) compared" % len(untouched))
        check("every unselected model is byte-identical", same, len(untouched))
    finally:
        wdv.patch(area, original)
        back = Game(MODDED_ISO).archive(ARCHIVE).read(area)
        restored = back == original
    print("\n   restore:")
    check("the working ISO is back to the bytes it started with",
          restored, True)

    print("\n%s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
