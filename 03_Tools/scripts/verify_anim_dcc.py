"""[M2] Does an animation edited in an OUTSIDE TOOL come back in?

`verify_anim_import.py` already proves the importer is exactly inverse to the
exporter: export a clip, import it back untouched, get the same bytes. That
test passed on every clip and the feature still failed on every clip a tester
tried, because nobody edits a BVH in a text editor. They open it in Blender.

WHAT A DCC DOES TO A BVH, and it does it whether or not you edit anything. The
file's three angles per joint are one decomposition of a rotation. Loading them
builds a matrix; saving writes whatever three angles decompose THAT matrix in
the tool's own rotation order. The pose is identical. The numbers are not --
they have been through float32, and where the tool's rotation order differs
from the engine's they are a different triple entirely.

That is modelled here exactly: every joint's angles are composed into a float32
matrix and re-decomposed, and the file is re-declared in the tool's order. No
edit is applied. A clip that will not survive THAT will not survive Blender.

THE THREE THINGS THAT BROKE, one section each.

**2a. A constant channel that moved by one unit got the whole file refused.**
Roughly 60% of the channels in a stock clip are stored as a single constant --
a bone that does not move in this clip has no row in the key matrix. Giving one
of them a curve resizes the file, so the importer refused, named the archive
slot, and offered `allow_reshape`. But its test for "a curve" was any
difference at all, down to ONE QUANTISER UNIT: the smallest difference the
format can express, and exactly what a tool that re-rounds a printed value
produces. `noise_floor=0` reproduces that rule exactly, so both rules are run
over the same nudged file and the difference is asserted, not described.

**2c. `.anim` is three codecs and the importer knew one.** The export route
writes a BVH for all three; the import route called `anim.parse` and answered
the other two with "is not a cm2 clip". 908 of the disc's 11,138 clips -- every
accessory animation among them -- could go to Blender and not come back. The
section asserts both halves: that `anim.parse` really does refuse those bytes,
and that they import now.

**2b. A fifth of the disc stores a rotation outside +/-pi.** A tool writes back
the principal decomposition -- the identical pose as a number a whole turn away
-- and read literally that is an enormous edit that saturates the quantiser
rail. A round trip with no edit in it has to come back with no edit in it.

What is asserted, per clip and per codec:
  * an unedited export imports back byte-for-byte identical         (identity)
  * the same clip after a DCC round trip imports at all             (the bug)
  * the result is the same LENGTH as what it replaces               (the slot)
  * re-exporting the result gives the same joint count and the same
    joint NAMES as the rig it was written against              (bones + names)
  * a real edit lands and reads back within one quantiser step  (it still works)

And what must still be REFUSED, because a loud crash is the good failure here
and a silently wrong file is the bad one:
  * a real edit to a channel the clip stores as a constant
  * a bone that a DCC translated, which this format cannot store
  * the wrong number of joints, the wrong number of frames, a non-BVH
"""
import math
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdrstudio import config                          # noqa: E402
from rdrstudio.game import Game                       # noqa: E402
from rdrstudio import anim as A                       # noqa: E402
from rdrstudio import animcmp as CM                   # noqa: E402
from rdrstudio import animraw as RW                   # noqa: E402
from rdrstudio import bvh as BVH                      # noqa: E402
from rdrstudio import skeleton as SK                  # noqa: E402

# Derived rather than a literal from the machine that wrote this: a
# hardcoded drive path makes the script unrunnable anywhere else.
#
# The STOCK disc, deliberately (rdr-app raised this under REQ-013 and left
# the call here). What this checks is a property of the shipped clips and
# the shipped codecs -- how a DCC's re-decomposition lands on them -- and
# none of that is a property of whatever the last live test left on the
# working ISO. Reading the working disc made a structural check able to go
# red for a reason with nothing to do with the code under test, which is
# the trap this repo has already been burned by once (the binned "revert
# restores the wrong value" hunt: a disc left dirty by a killed run).
#
# Verified before moving, not after: the whole script passes identically on
# both discs, down to the same worst-case pose change -- 0.04431 degrees on
# chr_bod_for_die_02.anim, key 29, channel 62 -- so this moves no ground
# under a landed milestone.
DISC = Path(config.VANILLA_ISO)
ARCS = ("SWAT.DAT", "FRONT.DAT")
PER_CODEC = 12          # clips per codec; SWAT carries 181 raw and 234 cmp
WRAPPED = 3             # of those, at least this many that wrap past +/-pi
SCAN_LIMIT = 900        # .anim entries to look at before settling for what
                        # has been found; SWAT.DAT holds 47,115 files


def f32(x):
    return struct.unpack("<f", struct.pack("<f", x))[0]


def parse_any(raw):
    if raw[:4] == CM.MAGIC:
        return "cmp", CM.parse(raw)
    if raw[:4] == RW.MAGIC:
        return "raw", RW.parse(raw)
    return "cm2", A.parse(raw)


def constant_channels(codec, a):
    """The channels this clip stores as ONE value. The uncompressed codec has
    none -- every channel carries its own sample per key."""
    if codec == "cmp":
        return [c for c in range(a.channels) if a.index[c] == CM.CONSTANT]
    if codec == "raw":
        return []
    return [c for c in range(a.channels) if not a.animated(c)]


def wraps(a):
    """Does this clip store a rotation outside the principal branch?

    2,215 of the disc's 11,137 clips do -- a fifth of them -- with the largest
    at 2,835 degrees. Every tool that loads and re-saves one writes the
    principal decomposition back, which is the identical pose as a number a
    whole turn away, and an importer that reads that literally saturates the
    quantiser rail and writes a file that opens fine and animates wrong.
    """
    return any(abs(a.at_key(k)[c]) > math.pi
               for k in range(a.keys) for c in range(3, a.channels))


def step_of(codec, a, c):
    """One quantiser unit of channel `c`, in the unit the clip stores.

    The smallest difference the format can express on that channel, which is
    the size the old constant-channel rule refused a whole file over.
    """
    if codec == "cm2":
        sc = a.scale_a if c < 3 else a.scale_b
        return 1.0 / sc if sc else 0.0
    if codec == "cmp":
        return abs(a.scale[c])
    return 0.0                    # the uncompressed codec has no quantiser


def remotion(text, m):
    """`text`'s HIERARCHY with `m`'s rows under it."""
    head = text.split("MOTION")[0]
    lines = text.replace("\r\n", "\n").split("\n")
    ft = next(l for l in lines if l.strip().upper().startswith("FRAME TIME"))
    return (head + "MOTION\nFrames: %d\n%s\n" % (m.frames, ft.strip())
            + "\n".join(" ".join("%.6f" % v for v in r) for r in m.rows)
            + "\n")


def dcc_roundtrip(text, order_out):
    """The BVH as an outside tool would write it back, with no edit applied.

    Every joint's angles are composed into a float32 matrix and decomposed
    again into `order_out`, and the CHANNELS lines are re-declared to match.
    The root translation goes through float32 as well, which is how any tool
    holds a position.
    """
    m = BVH.parse_bvh(text)
    cols = BVH._column_map(m, len(m.joints))
    orders = BVH.declared_orders(m)
    lines = text.replace("\r\n", "\n").split("\n")
    mi = next(i for i, l in enumerate(lines) if l.strip().upper() == "MOTION")

    out = []
    for l in lines[:mi]:
        s = l.strip()
        if s.upper().startswith("CHANNELS"):
            head = s.split()
            pos = [c for c in head[2:] if c.upper().endswith("POSITION")]
            pad = l[:len(l) - len(l.lstrip())]
            out.append("%sCHANNELS %s %s" % (
                pad, head[1],
                " ".join(pos + [BVH._AXIS[c] for c in order_out])))
        else:
            out.append(l)
    out.append("MOTION")
    out.append("Frames: %d" % m.frames)
    out.append("Frame Time: %s" % lines[mi + 2].split(":", 1)[1].strip())

    rad, deg = math.pi / 180.0, 180.0 / math.pi
    for row in m.rows:
        vals = [f32(row[cols[0]]), f32(row[cols[1]]), f32(row[cols[2]])]
        for j in range(len(m.joints)):
            b = 3 + 3 * j
            o = orders[j]
            by_axis = {"X": cols[b + 0], "Y": cols[b + 1], "Z": cols[b + 2]}
            angles = tuple(f32(row[by_axis[ax]] * rad) for ax in o)
            mat = BVH.euler_to_matrix(o, angles)
            mat = [[f32(v) for v in r] for r in mat]
            vals.extend(v * deg for v in BVH.matrix_to_euler(order_out, mat))
        out.append(" ".join("%.6f" % v for v in vals))
    return "\n".join(out) + "\n"


def main():
    game = Game(DISC, writable=False)
    ok = True

    def check(what, got, want, cmp="=="):
        nonlocal ok
        good = (got == want if cmp == "==" else
                got >= want if cmp == ">=" else
                got <= want if cmp == "<=" else got < want)
        ok &= good
        print("  %-54s %-14s %s"
              % (what, str(got)[:14],
                 "ok" if good else "FAIL (want %s %s)" % (cmp, want)))

    # ---- rigs, indexed by bone count. Nothing in a .anim names its skeleton.
    skels = {}
    for arc in ARCS:
        for e in game.toc(arc):
            if not e.name.lower().endswith(".skel"):
                continue
            try:
                _v, _d, bones, _r = SK.parse(game.read(arc, e.name))
            except Exception:
                continue
            if BVH.writable_rig(bones)[0]:
                skels.setdefault(len(bones), bones)

    # ---- clips, a spread of each codec, deliberately including some that
    # store a rotation outside the principal branch. Those are a fifth of the
    # disc and they are the ones a DCC mangles worst, so leaving it to chance
    # whether the sample caught any would make this check weaker on a good day.
    plain = {"cm2": [], "cmp": [], "raw": []}
    wrap = {"cm2": [], "cmp": [], "raw": []}
    seen = set()
    scanned = 0
    for arc in ARCS:
        for e in game.toc(arc):
            if not e.name.lower().endswith(".anim"):
                continue
            if all(len(plain[c]) >= PER_CODEC and len(wrap[c]) >= WRAPPED
                   for c in plain):
                break
            if scanned >= SCAN_LIMIT:
                break
            try:
                raw = game.read(arc, e.name)
                codec, a = parse_any(raw)
            except Exception:
                continue
            want = (a.channels - 3) // 3
            if a.channels != 3 + 3 * want or want not in skels or a.keys < 2:
                continue
            if raw in seen:
                continue
            scanned += 1
            bucket = wrap[codec] if wraps(a) else plain[codec]
            cap = WRAPPED if bucket is wrap[codec] else PER_CODEC
            if len(bucket) >= cap:
                continue
            seen.add(raw)
            bucket.append((arc, e.name, raw, a, skels[want]))
    picks = {c: wrap[c] + plain[c][:max(0, PER_CODEC - len(wrap[c]))]
             for c in plain}

    for codec in ("cm2", "cmp", "raw"):
        print("%s: %d clips" % (codec, len(picks[codec])))
    check("all three codecs have clips to test",
          min(len(v) for v in picks.values()) >= 3, True)

    # ------------------------------------------------------------------ 1
    # Identity. The property verify_anim_import proves for cm2, now for all
    # three -- an export imported back untouched is the same bytes.
    print("\nunedited export -> import is byte-identical:")
    for codec in ("cm2", "cmp", "raw"):
        ident = same = 0
        for _arc, name, raw, a, bones in picks[codec]:
            text = BVH.to_bvh(a, bones, name=name)
            out, rep = BVH.bvh_to_anim(raw, text, bones=bones)
            ident += out == raw
            same += len(out) == len(raw)
        check("  %s byte-identical" % codec, ident, len(picks[codec]))
        check("  %s same length" % codec, same, len(picks[codec]))

    # ------------------------------------------------------------------ 2
    # The bug. A DCC round trip, no edit, in the tool's own rotation order.
    print("\nafter a DCC round trip (float32 matrix, re-decomposed):")
    worst_dcc_rot = 0.0
    worst_dcc_pos = 0.0
    for order in ("XYZ", "YZX"):
        for codec in ("cm2", "cmp", "raw"):
            old_refused = new_ok = same = bones_ok = names_ok = 0
            worst = 0.0
            worst_channel = None
            worst_clip = None
            flat = 0
            for _arc, name, raw, a, bones in picks[codec]:
                text = BVH.to_bvh(a, bones, name=name)
                edited = dcc_roundtrip(text, order)

                # What the DCC round trip actually costs, measured on exactly
                # the quantity CONST_NOISE_DEG / CONST_NOISE_M are compared
                # against: the SPAN of a channel the clip stores as ONE value,
                # across the keys, in the unit the BVH prints. Not the error of
                # a single sample -- a sample that moved is not a problem, a
                # constant that stopped being constant is.
                #
                # Measured AFTER recompose_rows, because that is where the
                # importer puts the angles before it looks at them, and a
                # rotation written a whole turn away is the same rotation.
                m1 = BVH.parse_bvh(edited)
                c1 = BVH._column_map(m1, len(bones))
                BVH.recompose_rows(
                    m1, c1, len(bones),
                    reference=lambda k, j: tuple(a.at_key(k)[3 + 3 * j:
                                                             6 + 3 * j]))
                for c in constant_channels(codec, a):
                    if c1[c] is None:
                        continue
                    col = [m1.rows[k][c1[c]] for k in range(m1.frames)]
                    span = max(col) - min(col)
                    if c < 3:
                        worst_dcc_pos = max(worst_dcc_pos, span)
                    else:
                        worst_dcc_rot = max(worst_dcc_rot, span)

                # The OLD rule, reproduced exactly: no allowance at all for a
                # constant channel that wobbles.
                try:
                    BVH.bvh_to_anim(raw, edited, bones=bones, noise_floor=0)
                except BVH.ImportRefused:
                    old_refused += 1

                try:
                    out, rep = BVH.bvh_to_anim(raw, edited, bones=bones)
                except BVH.ImportRefused as ex:
                    print("    %s %s REFUSED: %s" % (codec, name, ex))
                    continue
                new_ok += 1
                same += len(out) == len(raw)
                if rep["worst_error"] > worst:
                    worst = rep["worst_error"]
                    worst_channel = rep["worst_error_channel"]
                    worst_clip = a
                flat += rep["flattened_channel_count"]

                # Bone counts and names, off the file that was just written.
                _c2, back = parse_any(out)
                again = BVH.parse_bvh(BVH.to_bvh(back, bones, name=name))
                bones_ok += (back.channels == 3 + 3 * len(bones)
                             and len(again.joints) == len(bones))
                names_ok += [b.name for b in bones] == again.joints

            n = len(picks[codec])
            # Reported, NOT asserted. This model is a single clean float32
            # matrix round trip and it moves a constant channel by 1e-5 of a
            # degree -- a thousandth of a quantiser step, so on most clips
            # nothing flips and the old rule would have taken them too. What
            # the old rule cannot survive is a difference of ONE UNIT, and
            # that is asserted on its own terms in section 2a rather than
            # dressed up as a consequence of this model.
            print("  %s, tool order %s: %d flattened channel(s), "
                  "%d of %d refused by the old rule"
                  % (codec, order, flat, old_refused, n))
            check("    imported now", new_ok, n)
            check("    same length as the original", same, n)
            check("    re-exports to the right joint count", bones_ok, n)
            check("    re-exports to the right joint NAMES", names_ok, n)
            # Against the CHANNEL'S OWN quantiser step, not a flat number.
            # A cmp track spends 10 or 11 bits on its own span, so a track
            # covering 90 degrees has a 0.088-degree step and cannot do
            # better than half of that -- which says nothing about whether
            # the encoder is right. cm2 shares one scale across every
            # rotation channel, and the uncompressed codec has no step at all.
            wc = worst_channel
            step = (step_of(codec, worst_clip, wc) * 180.0 / math.pi
                    if wc is not None and wc >= 3 else
                    step_of(codec, worst_clip, wc) if wc is not None else 0.0)
            limit = step if step else 1e-4
            check("    worst sample error, in quantiser steps",
                  worst <= limit, True)

    print("\n  what the DCC round trip actually costs:")
    print("    worst rotation drift : %.3g degrees  (allowance %g)"
          % (worst_dcc_rot, BVH.CONST_NOISE_DEG))
    print("    worst position drift : %.3g metres   (allowance %g)"
          % (worst_dcc_pos, BVH.CONST_NOISE_M))
    check("  rotation allowance is above the measured drift",
          worst_dcc_rot < BVH.CONST_NOISE_DEG, True)
    check("  position allowance is above the measured drift",
          worst_dcc_pos < BVH.CONST_NOISE_M, True)

    # ------------------------------------------------------------------ 2a
    # THE OLD RULE, on its own terms and at its own scale. It refused a file
    # for a difference of ONE QUANTISER UNIT on a channel the clip stores as a
    # constant -- the smallest difference the format can express, and exactly
    # what any tool that re-rounds a printed value produces. No noise model is
    # needed to show it: put one unit on one key and ask.
    #
    # This is what "it errors with all of my tests" was. The message named the
    # archive slot and offered allow_reshape, so a user re-exporting from
    # Blender had no way to satisfy it.
    print("\none quantiser unit on one constant channel:")
    for codec in ("cm2", "cmp"):
        old_refused = new_ok = stayed = 0
        tried = 0
        for _arc, name, raw, a, bones in picks[codec]:
            consts = [c for c in constant_channels(codec, a) if c >= 3]
            if not consts:
                continue
            c = consts[0]
            text = BVH.to_bvh(a, bones, name=name)
            m = BVH.parse_bvh(text)
            cols = BVH._column_map(m, len(bones))
            step = step_of(codec, a, c) * 180.0 / math.pi     # degrees
            if not step:
                continue
            tried += 1
            # One unit, on one key, up. Everything else untouched.
            m.rows[0][cols[c]] += step
            nudged = remotion(text, m)
            try:
                BVH.bvh_to_anim(raw, nudged, bones=bones, noise_floor=0)
            except BVH.ImportRefused:
                old_refused += 1
            try:
                out, rep = BVH.bvh_to_anim(raw, nudged, bones=bones)
            except BVH.ImportRefused as ex:
                print("    %s %s REFUSED: %s" % (codec, name, ex))
                continue
            new_ok += 1
            # ...and the channel is still a single value afterwards, which is
            # the whole reason the file did not have to grow.
            _c2, back = parse_any(out)
            vals = set(round(back.at_key(k)[c], 12) for k in range(a.keys))
            stayed += len(vals) == 1 and len(out) == len(raw)
        check("  %s clips with a constant channel to nudge" % codec,
              tried >= 3, True)
        check("  %s refused by the OLD rule" % codec, old_refused, tried)
        check("  %s imported now" % codec, new_ok, tried)
        check("  %s channel is still one value, file still fits" % codec,
              stayed, tried)

    # ------------------------------------------------------------------ 2c
    # The other half of the bug: `.anim` is three codecs and the importer knew
    # one. `anim.parse` is what the import route called, so what it does with
    # these bytes IS what the user saw -- "NAME is not a cm2 clip" -- for 908
    # of the disc's 11,138 clips, every accessory animation among them.
    print("\nthe two codecs the importer used to refuse outright:")
    for codec in ("cmp", "raw"):
        threw = imported = 0
        for _arc, name, raw, a, bones in picks[codec]:
            try:
                A.parse(raw)
            except A.AnimError:
                threw += 1
            text = BVH.to_bvh(a, bones, name=name)
            out, rep = BVH.bvh_to_anim(raw, dcc_roundtrip(text, "XYZ"),
                                       bones=bones)
            imported += len(out) == len(raw) and rep["codec"] == codec
        n = len(picks[codec])
        check("  %s: anim.parse refuses it, as the route did" % codec,
              threw, n)
        check("  %s: imports now, same length" % codec, imported, n)

    # ------------------------------------------------------------------ 2b
    # The clips that store a rotation outside the principal branch. A fifth of
    # the disc does, and a DCC writes every one of them back on the principal
    # branch -- the same pose as a number a whole turn away. Read literally
    # that is an enormous edit: it saturates the quantiser rail, and where it
    # lands on a channel stored as a constant it gets the file refused. Read as
    # what it is, nothing changed at all, and that is what is asserted here --
    # a round trip with no edit in it must come back with no edit in it.
    print("\nclips that store an angle outside +/-pi:")
    wrapped = [(codec, p) for codec in picks for p in picks[codec]
               if wraps(p[3])]
    print("  %d of the %d clips picked"
          % (len(wrapped), sum(len(v) for v in picks.values())))
    check("  there are wrapped clips to test", len(wrapped) >= 1, True)
    # Judged on the POSE, not on the bytes. A value sitting on a quantiser
    # boundary legitimately lands one unit either side of where it started and
    # that is not a change to the animation; a rotation read a turn away is off
    # by 360 degrees and could not hide inside any sane bound.
    quiet = snapped_any = 0
    worst_pose = 0.0
    worst_where = None
    for codec, (_arc, name, raw, a, bones) in wrapped:
        text = BVH.to_bvh(a, bones, name=name)
        out, rep = BVH.bvh_to_anim(raw, dcc_roundtrip(text, "YZX"),
                                   bones=bones)
        snapped_any += rep["joints_snapped"] > 0
        _c2, back = parse_any(out)
        before = [a.at_key(k) for k in range(a.keys)]
        after = [back.at_key(k) for k in range(a.keys)]
        ok_clip = True
        for c in range(a.channels):
            # One step of this channel's own quantiser, or a hair for the
            # codec that has none.
            step = step_of(codec, a, c) or 1e-7
            for k in range(a.keys):
                d = abs(after[k][c] - before[k][c])
                if c >= 3 and d * 180.0 / math.pi > worst_pose:
                    worst_pose = d * 180.0 / math.pi
                    worst_where = (name, c, k)
                if d > step * 1.001:
                    ok_clip = False
        quiet += ok_clip
    print("    worst pose change: %.4g degrees at %s"
          % (worst_pose, worst_where))
    check("  a no-op round trip changes no pose by more than one step",
          quiet, len(wrapped))
    check("  and the branch really was the reason", snapped_any >= 1, True)

    # ------------------------------------------------------------------ 3
    # An edit must still land, on every codec -- flattening a constant channel
    # must not have turned the importer into something that ignores changes.
    print("\na real edit lands and reads back:")
    for codec in ("cm2", "cmp", "raw"):
        landed = 0
        for _arc, name, raw, a, bones in picks[codec]:
            text = BVH.to_bvh(a, bones, name=name)
            m = BVH.parse_bvh(text)
            cols = BVH._column_map(m, len(bones))
            # The first channel the clip actually animates, so the edit has
            # somewhere to go that needs no reshape.
            moving = [c for c in range(3, a.channels)
                      if len(set(round(a.at_key(k)[c], 9)
                                 for k in range(a.keys))) > 1]
            if not moving:
                landed += 1          # nothing animated; nothing to assert
                continue
            c = moving[0]
            for k in range(m.frames):
                m.rows[k][cols[c]] += 3.0          # degrees
            edited = remotion(text, m)
            out, rep = BVH.bvh_to_anim(raw, edited, bones=bones)
            _c2, back = parse_any(out)
            deg = 180.0 / math.pi
            got = [back.at_key(k)[c] * deg for k in range(a.keys)]
            want = [a.at_key(k)[c] * deg + 3.0 for k in range(a.keys)]
            # One quantiser step is the best a 16-bit format can do; the
            # uncompressed codec should be far better than that.
            # cm2 shares one scale across every rotation channel, so one
            # quantiser step is the best it can do. cmp retunes the edited
            # track's own window and the uncompressed codec has no quantiser
            # at all, so both should be far better.
            tol = 0.02 if codec == "cm2" else 1e-3
            landed += all(abs(g - w) < tol for g, w in zip(got, want))
        check("  %s edit survives the round trip" % codec,
              landed, len(picks[codec]))

    # ------------------------------------------------------------------ 4
    # Refusals. Loud beats silently wrong.
    print("\nstill refused:")
    arc, name, raw, a, bones = picks["cm2"][0]
    good = BVH.to_bvh(a, bones, name=name)

    def refused(what, text, **kw):
        try:
            BVH.bvh_to_anim(raw, text, **kw)
        except BVH.ImportRefused:
            return check("  " + what, True, True)
        except Exception as ex:
            return check("  " + what + " (wrong exception %s)"
                         % type(ex).__name__, False, True)
        check("  " + what, False, True)

    refused("not a BVH at all", "this is not a BVH\n")
    refused("a frame count the clip cannot hold",
            good.replace("Frames: %d" % a.keys, "Frames: %d" % (a.keys + 7)))

    m = BVH.parse_bvh(good)
    cols = BVH._column_map(m, len(bones))
    consts = [c for c in range(3, a.channels) if not a.animated(c)]
    if consts:
        c = consts[0]
        for k in range(m.frames):
            m.rows[k][cols[c]] += 4.0 * (k % 2)       # a 4-degree square wave
        curved = remotion(good, m)
        refused("a real curve on a channel stored as a constant", curved)
        # ...and allow_reshape is still the documented way through it.
        out, rep = BVH.bvh_to_anim(raw, curved, allow_reshape=True)
        check("  allow_reshape still animates it", rep["reshaped"], True)

    # A joint that a DCC translated. Six channels on a non-root joint is what
    # Blender writes with "Root Translation Only" unticked; constant is the
    # rest offset and must pass, moving is an edit this format cannot store.
    lines = good.replace("\r\n", "\n").split("\n")
    mi = next(i for i, l in enumerate(lines) if l.strip().upper() == "MOTION")
    hier = []
    hit = 0
    for l in lines[:mi]:
        s = l.strip()
        if s.upper().startswith("CHANNELS 3") and hit < 1:
            hit += 1
            pad = l[:len(l) - len(l.lstrip())]
            hier.append("%sCHANNELS 6 Xposition Yposition Zposition %s"
                        % (pad, " ".join(s.split()[2:])))
        else:
            hier.append(l)
    rows = [r.split() for r in lines[mi + 3:] if r.strip()]
    # the extra three columns land right before that joint's rotations
    first_rot = 3 + 3 * 1
    fixed = ["\n".join(hier), "MOTION", "Frames: %d" % a.keys,
             lines[mi + 2]]
    moving = list(fixed)
    for i, r in enumerate(rows):
        pre, post = r[:first_rot], r[first_rot:]
        fixed.append(" ".join(pre + ["0.250000", "0.000000", "0.000000"]
                              + post))
        moving.append(" ".join(pre + ["%.6f" % (0.25 + 0.01 * i),
                                      "0.000000", "0.000000"] + post))
    try:
        _o, rep = BVH.bvh_to_anim(raw, "\n".join(fixed) + "\n")
        check("  a bone with a CONSTANT position is accepted",
              rep["ignored_position_count"], 1)
    except BVH.ImportRefused as ex:
        check("  a bone with a CONSTANT position is accepted: %s" % ex,
              False, True)
    refused("a bone a DCC actually translated",
            "\n".join(moving) + "\n")

    print("\n%s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
