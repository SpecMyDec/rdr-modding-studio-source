"""Does the BVH importer put back exactly what the exporter took out?

An importer is more dangerous than an exporter: a bad export produces a file
that looks wrong in Blender, a bad import produces a game that is subtly wrong
forever. So the checks here are picked to fail a plausible-but-broken importer,
and the sharpest of them is the first:

  1. **Identity.** Export a clip to BVH, import it back UNCHANGED, and the
     `.anim` bytes must be byte-for-byte the original. This catches a wrong
     quantiser, a wrong rounding rule, a transposed Euler demux, an off-by-one
     in the key matrix and a mis-sized constants array all at once, because any
     one of them moves at least one byte.
  2. **Identity with the dead band OFF.** `bvh_to_anim` keeps the original s16
     when the incoming value is within the BVH's own print precision of the
     original. That is correct, but it could also HIDE a +/-1 error in the
     quantiser, so the whole sweep runs again with `tolerance=0.0`, which
     quantises every sample blind. The two numbers are reported separately and
     the gap is explained rather than averaged away.
  3. **Round trip through an edit.** Move a curve by a real amount, import,
     re-decode. Every sample must come back within one quantiser step (1/scale)
     of what was asked for -- that is the best a 16-bit format can do, and
     anything worse means the value went through the wrong scale or the wrong
     channel.
  4. **Refusals.** Wrong joint count, wrong frame count, a channel that the file
     stores as a constant given a curve, a root with no translation, a motion
     row of the wrong width, and a non-BVH must each be REFUSED with a message,
     not written.
  5. **Size.** Every same-shape re-encode is byte-for-byte the same LENGTH as
     the original. This is the property that keeps the edit inside its archive
     slot, and it is checked on every clip rather than argued from the layout.
  6. **The rounding claim.** bvh.py says it deliberately does not copy the
     engine's truncating `cvt.w.s` (0x002D2E8C) because truncation cannot
     round-trip. That claim is measured here rather than asserted, by running
     the same sweep with `rounding="truncate"`.

Nothing here writes to a disc. The importer is exercised entirely in memory
against the bytes read from the ISO.
"""
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdrstudio.game import Game                      # noqa: E402
from rdrstudio import config                          # noqa: E402
from rdrstudio import anim as A                       # noqa: E402
from rdrstudio import bvh as BVH                      # noqa: E402
from rdrstudio import skeleton as SK                  # noqa: E402

# REQ-035. Was a literal path to the WORKING ISO on one machine:
# unrunnable on anyone else's checkout, and pointed at the disc the
# live tests mutate, so a read-only structural check could go red for
# a reason with nothing to do with the code under test. What this
# measures is a property of the shipped clips, so it reads the stock
# disc. Opened writable=False, which is what makes vanilla correct here.
DISC = Path(config.VANILLA_ISO)
ARC = "SWAT.DAT"
MAX_CLIPS = 60


def main():
    game = Game(DISC, writable=False)
    ok = True

    def check(what, got, want, cmp="=="):
        nonlocal ok
        good = (got == want if cmp == "==" else
                got < want if cmp == "<" else got > want)
        ok &= good
        print("  %-52s %-12s %s"
              % (what, str(got)[:12],
                 "ok" if good else "FAIL (want %s %s)" % (cmp, want)))

    # Index the skeletons once, by bone count -- nothing in a .anim names its
    # skeleton, so the bone count is the only link.
    skels = {}
    for e in game.toc(ARC):
        if not e.name.lower().endswith(".skel"):
            continue
        try:
            _v, _d, bones, _r = SK.parse(game.read(ARC, e.name))
        except Exception:
            continue
        skels.setdefault(len(bones), bones)

    picks = []
    seen = set()
    for e in game.toc(ARC):
        if not e.name.lower().endswith(".anim"):
            continue
        try:
            raw = game.read(ARC, e.name)
            a = A.parse(raw)
        except Exception:
            continue
        if raw in seen:
            continue
        seen.add(raw)
        want = (a.channels - 3) // 3
        if a.channels != 3 + 3 * want or want not in skels:
            continue
        picks.append((e.name, raw, a, skels[want]))
        if len(picks) >= MAX_CLIPS:
            break
    print("%d clips with a matching skeleton\n" % len(picks))
    check("found clips to test", len(picks) > 20, True)

    # ---------------------------------------------------------- 1, 2, 5, 6
    print("identity, size and rounding:")
    ident = ident0 = ident_trunc = 0
    same_size = 0
    n_half = n_zero_scale = 0
    worst_selfcheck = 0.0
    for name, raw, a, bones in picks:
        text = BVH.to_bvh(a, bones, name=name)
        out, rep = BVH.bvh_to_anim(raw, text)
        if out == raw:
            ident += 1
        if len(out) == len(raw):
            same_size += 1
        worst_selfcheck = max(worst_selfcheck, rep["worst_error"])
        if a.half_rate:
            n_half += 1
        if a.scale_a == 0.0 or a.scale_b == 0.0:
            n_zero_scale += 1
        # dead band off: every sample re-quantised blind
        out0, _ = BVH.bvh_to_anim(raw, text, tolerance=0.0)
        if out0 == raw:
            ident0 += 1
        # the engine's own truncating cvt.w.s, dead band off
        outt, _ = BVH.bvh_to_anim(raw, text, tolerance=0.0, rounding="truncate")
        if outt == raw:
            ident_trunc += 1

    check("byte-identical re-import (default)", ident, len(picks))
    check("same LENGTH as the original", same_size, len(picks))
    check("self-check worst error is ~0", worst_selfcheck < 1e-3, True)
    print("     of those clips, %d are half rate and %d carry a zero scale"
          % (n_half, n_zero_scale))
    print("     byte-identical with the dead band OFF : %d/%d"
          % (ident0, len(picks)))
    print("     byte-identical with TRUNCATION (the game's own cvt.w.s,"
          " dead band off): %d/%d" % (ident_trunc, len(picks)))
    if ident0 < len(picks):
        print("     -> the %d clip(s) that need the dead band are the ones whose"
              % (len(picks) - ident0))
        print("        scale is so large that \"%.6f\" text cannot resolve one")
        print("        quantiser step; see the note in bvh.py.")
    check("truncation really is worse than nearest", ident_trunc < ident0, True)

    # ------------------------------------------------------------------ 3
    #
    # A NOTE ON WHY THE EDITS AVOID THE RAILS. The game's encoder picks
    # scale = 32768/max|v| over each group, so the extremal sample of a clip
    # lands exactly ON +/-32767 -- measured, 15 of 20 SWAT.DAT clips have a
    # group-B sample within 40 units of the rail. Nudging every sample by a few
    # steps therefore pushes the extremes past the clamp, and the first version
    # of this test reported a 38-step error that was entirely the clamp doing
    # its job. So the edit is held clear of the rails here, and saturation gets
    # its own check below instead of contaminating the accuracy number.
    print("\nround trip through a real edit:")
    rng = random.Random(20260730)
    worst_steps = 0.0
    worst_raw = 0
    differ = 0
    off_boundary = 0
    edited_clips = 0
    edited_samples = 0
    deg = 180.0 / math.pi
    for name, raw, a, bones in picks[:20]:
        if a.scale_b == 0.0 or a.anim_count == 0:
            continue
        movers = [c for c in range(3, a.channels) if a.animated(c)]
        if not movers:
            continue
        text = BVH.to_bvh(a, bones, name=name)
        lines = text.split("\n")
        mi = lines.index("MOTION")
        head, rows = lines[:mi + 3], [l for l in lines[mi + 3:] if l.strip()]
        # Build the column map the way the importer does, so an edit provably
        # lands on the channel it is meant to.
        cols = BVH._column_map(BVH.parse_bvh(text), len(bones))

        step_deg = (1.0 / a.scale_b) * deg      # one quantiser step, degrees
        orig = {c: a.raw_curve(c) for c in movers}
        wanted = {}
        newrows = [[float(x) for x in l.split()] for l in rows]
        for k in range(len(newrows)):
            for c in movers:
                # A deliberately FRACTIONAL target, so the quantiser is really
                # exercised rather than handed an integer it cannot get wrong.
                t = orig[c][k] + rng.uniform(-40.0, 40.0)
                t = max(-32000.0, min(32000.0, t))
                newrows[k][cols[c]] = t * step_deg
                wanted[(c, k)] = (t, t * step_deg)
        edited = "\n".join(head + [" ".join("%.6f" % x for x in r)
                                   for r in newrows]) + "\n"
        out, rep = BVH.bvh_to_anim(raw, edited)
        if len(out) != len(raw):
            print("  %s: an edit changed the file LENGTH %d -> %d"
                  % (name, len(raw), len(out)))
            ok = False
            continue
        if rep["clipped_samples"]:
            print("  %s: %d sample(s) clamped, but this edit was held clear of "
                  "the rails" % (name, rep["clipped_samples"]))
            ok = False
        back = A.parse(out)
        got_raw = {c: back.raw_curve(c) for c in movers}
        # An INDEPENDENT float64 quantiser, fed the same text the importer was
        # fed. Comparing against the pre-text target instead would be wrong and
        # was: "%.6f" moved one target of 37.50002 raw units to 37.49996, so 37
        # is the right answer for the value that actually arrived and 38 is not.
        readback = BVH.parse_bvh(edited)
        for (c, k), (t_raw, wv) in wanted.items():
            worst_steps = max(worst_steps, abs(back.at_key(k)[c] * deg - wv)
                              / step_deg)
            ideal = readback.rows[k][cols[c]] * (math.pi / 180.0) * a.scale_b
            want_i = (int(ideal + 0.5) if ideal >= 0 else -int(-ideal + 0.5))
            d = abs(got_raw[c][k] - want_i)
            if d:
                differ += 1
                # Predicted cause: the module multiplies in float32 because
                # 0x002D2E58 is `mul.s`, so it can land the other side of a
                # half boundary from a float64 ideal. If a disagreement is NOT
                # at a half boundary, something else is wrong and it must show.
                if abs(abs(ideal % 1.0) - 0.5) > 0.01:
                    off_boundary += 1
            worst_raw = max(worst_raw, d)
            edited_samples += 1
        edited_clips += 1
    print("     %d clips, %d edited samples" % (edited_clips, edited_samples))
    check("edited clips found", edited_clips > 5, True)
    check("worst error vs the target, in quantiser steps", worst_steps < 1.0,
          True)
    # NOT "must equal the float64 ideal". The module multiplies in float32
    # because the engine's encoder does (`mul.s f13, f13, f12`, 0x002D2E58), so
    # a value sitting within ~0.002 of a half boundary can legitimately round
    # the other way. What must hold is that it is never more than one unit out
    # and that every disagreement is at such a boundary.
    check("stored s16 is within 1 of a float64 ideal", worst_raw < 2, True)
    check("...and every disagreement is at a half boundary", off_boundary, 0)
    print("     %d of %d samples differ from the float64 ideal (%.4f%%), all "
          "by 1 unit" % (differ, edited_samples,
                         100.0 * differ / max(edited_samples, 1)))
    print("     max |wanted - decoded| = %.4f quantiser steps -- 0.5 is the "
          "floor for" % worst_steps)
    print("     a 16-bit format, and the excess is the \"%.6f\" text rather "
          "than the quantiser")

    # ---- saturation: pushing past the rail must clamp AND be reported
    print("\n  saturation (the +/-32767 rails at 0x002D2E5C / 0x002D2E78):")
    name, raw, a, bones = next((p for p in picks
                                if p[2].scale_b != 0.0 and p[2].anim_count),
                               picks[0])
    text = BVH.to_bvh(a, bones, name=name)
    lines = text.split("\n")
    mi = lines.index("MOTION")
    head, rows = lines[:mi + 3], [l for l in lines[mi + 3:] if l.strip()]
    cols = BVH._column_map(BVH.parse_bvh(text), len(bones))
    c = next(c for c in range(3, a.channels) if a.animated(c))
    huge = (1.0 / a.scale_b) * deg * 1e6
    nr = []
    for i, l in enumerate(rows):
        v = [float(x) for x in l.split()]
        v[cols[c]] = huge if i % 2 == 0 else -huge
        nr.append(" ".join("%.6f" % x for x in v))
    out, rep = BVH.bvh_to_anim(raw, "\n".join(head + nr) + "\n")
    back = A.parse(out)
    rails = set(back.raw_curve(c))
    check("saturating samples are reported", rep["clipped_samples"], a.keys)
    check("the channel is named in the report", c in rep["clipped_channels"],
          True)
    check("...and the stored values sit exactly on the rails",
          rails <= {32767, -32768}, True)
    check("saturation does not change the file length", len(out), len(raw))

    # ------------------------------------------------------------------ 4
    print("\nrefusals:")
    name, raw, a, bones = picks[0]
    good = BVH.to_bvh(a, bones, name=name)

    def refuses(label, text, original=raw, **kw):
        nonlocal ok
        try:
            BVH.bvh_to_anim(original, text, **kw)
        except ValueError as ex:
            print("  %-34s refused: %s" % (label, str(ex)[:70]))
            return True
        print("  %-34s ACCEPTED -- it should not have been" % label)
        ok = False
        return False

    # wrong joint count: drop the last JOINT block
    lines = good.split("\n")
    other = None
    for n, bs in skels.items():
        if n != len(bones):
            other = bs
            break
    if other is not None:
        # A BVH built from a different skeleton entirely.
        other_clip = None
        for nm, r2, a2, b2 in picks:
            if len(b2) != len(bones):
                other_clip = (nm, a2, b2)
                break
        if other_clip:
            wrong = BVH.to_bvh(other_clip[1], other_clip[2], name=other_clip[0])
            check("wrong joint count is refused",
                  refuses("BVH of another skeleton", wrong), True)

    # wrong frame count: delete one motion row
    mi = lines.index("MOTION")
    rows = [l for l in lines[mi + 3:] if l.strip()]
    short = "\n".join(lines[:mi + 1]
                      + ["Frames: %d" % (len(rows) - 1), lines[mi + 2]]
                      + rows[:-1]) + "\n"
    check("wrong frame count is refused",
          refuses("one frame short", short), True)

    # frame count declared wrong but rows unchanged
    lie = "\n".join(lines[:mi + 1] + ["Frames: %d" % (len(rows) + 5),
                                      lines[mi + 2]] + rows) + "\n"
    check("Frames: that disagrees with the rows is refused",
          refuses("Frames: lies about the rows", lie), True)

    # a constant channel given a curve -> shape change
    const_c = next((c for c in range(3, a.channels) if not a.animated(c)), None)
    if const_c is not None:
        m = BVH.parse_bvh(good)
        cols = BVH._column_map(m, len(bones))
        col = cols[const_c]
        nr = []
        for i, l in enumerate(rows):
            v = [float(x) for x in l.split()]
            v[col] += 5.0 * (i % 2)          # unmistakably a curve
            nr.append(" ".join("%.6f" % x for x in v))
        curved = "\n".join(lines[:mi + 3] + nr) + "\n"
        check("animating a constant channel is refused",
              refuses("constant channel given a curve", curved), True)
        # ...and accepted when the caller opts in, with a size change reported
        out, rep = BVH.bvh_to_anim(raw, curved, allow_reshape=True)
        check("allow_reshape does change the shape", rep["reshaped"], True)
        check("allow_reshape reports a bigger file", len(out) > len(raw), True)
        print("     reshaped: %d -> %d animated channels, %d -> %d bytes"
              % (rep["animated_before"], rep["animated"],
                 len(raw), len(out)))
        # and the reshaped file must still decode
        rb = A.parse(out)
        check("the reshaped file re-parses", rb.channels, a.channels)
        check("...and its size formula is exact", rb.consumed, len(out))
    else:
        print("  (no constant channel in %s, so the reshape path is untested "
              "here)" % name)

    # a motion row of the wrong width
    bad_w = "\n".join(lines[:mi + 3] + [rows[0] + " 1.0"] + rows[1:]) + "\n"
    check("a short/long motion row is refused",
          refuses("motion row of the wrong width", bad_w), True)

    # a root with no translation channels
    noroot = good.replace("CHANNELS 6 Xposition Yposition Zposition",
                          "CHANNELS 3", 1)
    check("a root with no translation is refused",
          refuses("root without Xposition", noroot), True)

    # not a BVH at all
    check("a non-BVH is refused", refuses("plain text", "hello\nworld\n"), True)

    # an asserted Euler order that the file does not declare
    check("a mismatched euler= assertion is refused",
          refuses("euler='ZYX' against an XYZ file", good, euler="ZYX"), True)

    # a clip whose channel count does not fit the layout
    odd = None
    for e in game.toc(ARC):
        if not e.name.lower().endswith(".anim"):
            continue
        try:
            r2 = game.read(ARC, e.name)
            x = A.parse(r2)
        except Exception:
            continue
        if x.channels != 3 + 3 * ((x.channels - 3) // 3):
            odd = r2
            break
    if odd is not None:
        check("a clip that does not fit 3+3*bones is refused",
              refuses("clip with an odd channel count", good, original=odd),
              True)
    else:
        print("  (every cm2 clip in %s fits 3 + 3*bones, so that path is "
              "untested here)" % ARC)

    print("\n%s" % ("ALL CHECKS PASSED" if ok else "SOMETHING FAILED"))
    game.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
