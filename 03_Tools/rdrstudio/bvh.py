"""Animation to and from BVH, which Blender reads and writes natively.

`to_bvh` renders a decoded clip; `bvh_to_anim` re-encodes an edited one back
into `.anim` bytes. The import half has its own long block comment further down
-- read that before touching it, because the safety of the whole thing rests on
the re-encode being the same SIZE as the file it replaces.

BVH is the right target for this data and needs no library: it is plain text, it
describes exactly a bone hierarchy with per-joint Euler rotations plus a root
translation, and Blender has imported it since forever
(File > Import > Motion Capture (.bvh)).

WHAT MAKES THIS POSSIBLE is the channel-to-bone mapping, which was the last
missing piece and turns out to be stated by the skeleton rather than hidden:

  * `.skel` is plain text; every bone declares `transX transY transZ rotX rotY
    rotZ` and the bones are nested by hierarchy, so file order is depth-first.
  * an animation's channel count is ALWAYS `3 + 3 * bones`.

Which reads as: three root translation channels, then three rotation channels
per bone in depth-first order. That also explains a detail the code made plain
but nothing else did -- why channels 0,1,2 dequantise with their own scale
(metres, default 1000 -> 0.001 precision) while 3.. use 32768/pi, which is a
radian quantiser to the last bit.

Tested, not assumed (`scripts/probe_anim_bones.py`):

  * In `chr_for_wlk` the largest rotation ranges land on `hip_l` 1.50,
    `hip_r` 1.41, `knee_r` 1.12, `knee_l` 0.93, `shoulder_l/r` 0.87,
    `ankle_r/l` 0.78 rad -- the bones a walk actually uses -- while fingers and
    thumbs sit near zero. Under a wrong mapping those would land anywhere.
  * Left/right pairs are ANTIPHASE, which is what a walk is: shoulder
    r = -0.971, hip r = -0.901.
  * The limb-to-finger mean range ratio is 2.5x on the walk and 62.7x on the
    run, and the run's ranges are larger throughout, as a run's should be.
  * Control: random channel pairs from the same clip are 50-53% antiphase, the
    real left/right pairs 62-78%.

THE COMPOSITION ORDER IS **YZX**, read out of the engine:

    R = Ry(ch1) . Rz(ch2) . Rx(ch0)     column-vector convention, so X is
                                        applied first, then Z, then Y

In BVH that is `CHANNELS 3 Yrotation Zrotation Xrotation` with values emitted
as (ch1, ch2, ch0); in Blender/Maya it is rotation_mode 'XZY'.

Where that comes from, so it can be re-checked:

  * The bone euler-to-matrix builder is 0x00146B68 (FPU) / 0x001465A8 (VU0).
    Each takes a 3x3 destination and a pointer to three angles and calls the
    sincos helper three times.
  * They are reached from the ANIMATION APPLIER at 0x002D55B8 (FPU) /
    0x002D54C8 (VU0), whose loop copies channels 3+3i..3+3i+2 into a bone's
    80-byte runtime record at +48..+56 and then calls the builder on it --
    which independently CONFIRMS FROM CODE the channel-to-bone mapping that
    until now rested only on the statistics above.
  * Executing those straight-line COP1 blocks shows the nine stored floats are
    transpose(Ry(b) . Rz(c) . Rx(a)). The transpose ambiguity is settled by the
    matrix concatenation at 0x001448E8, called as (dst=local, A=local,
    B=parent): local.parent is a correct scene graph only under the row-vector
    reading, which leaves x -> Ry.Rz.Rx.x.

A TRAP worth recording: the binary holds a SECOND euler-to-matrix pair,
0x001469F8 / 0x001464D0, computing Rz.Ry.Rx. It is used for scene-node
placement and for the skeleton object's own euler, never for bones -- but it is
the pair you reach first walking down from the skeleton root function. Stopping
there yields "XYZ", which is wrong for `.anim` channels.

All six orders stay selectable via `euler=`, because the above is one trace of
one function pair. An independent data-side check was attempted and could NOT
settle it (`scripts/verify_euler_anatomy.py`): all six candidates score within
0.3% on knee-hinge stability and within 0.2% on uprightness. That is a null
result, not corroboration -- both metrics probe the spine, where rotations are
small. The order does matter: 42.3% of bone-frames rotate about more than one
axis, and for those the six orders differ by a median of 0.035 in matrix
entries and by up to 2.0.

Units: offsets and root translation are emitted in METRES, which is what the
disc stores and what makes a 1.8 m character 1.8 units in Blender. BVH files
from motion-capture kit are usually in centimetres, so an importer that assumes
that will need its scale set to 1.
"""

import math
import struct

from . import anim as _anim
from . import animcmp as _cmp
from . import animraw as _raw

# The PS2 ran NTSC at 60 Hz and the animation system is keyed per game frame at
# 30. Not read out of the executable -- if a clip plays at the wrong speed in
# Blender this is the number to question.
DEFAULT_FPS = 30.0

EULER_ORDERS = ("XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX")
_AXIS = {"X": "Xrotation", "Y": "Yrotation", "Z": "Zrotation"}


def _rot_channels(order):
    return " ".join(_AXIS[c] for c in order)


# --------------------------------------------------------- Euler conversion
#
# A BVH's CHANNELS line does not merely LABEL three numbers, it states the
# order they compose in. Two consequences, and both of them were bugs:
#
#   EXPORT. This module used to emit the clip's three stored angles reordered
#   to match whatever CHANNELS line was asked for -- the same numbers, a
#   different label. Only "YZX" was then truthful, because the engine composes
#   R = Ry(ch1).Rz(ch2).Rx(ch0). Picking any of the other five produced a file
#   that declared an order its numbers were not for, so Blender showed a pose
#   the game never had. The order menu was described as "try another if a
#   joint looks wrong", which is the symptom of exactly this.
#
#   IMPORT. Blender writes the channel order of the armature's own rotation
#   mode, which after a round trip is usually Xrotation Yrotation Zrotation --
#   and its numbers ARE a genuine XYZ decomposition. Reading them into the
#   engine's slots unconverted writes a different pose. The importer could not
#   tell the two cases apart, so it refused outright:
#
#       "the BVH declares rotations as XROTATION YROTATION ZROTATION but
#        YROTATION ZROTATION XROTATION was asserted"
#
#   which is what a tester hit the moment he edited a clip in Blender.
#
# Converting on both sides removes the ambiguity instead of policing it: every
# file this tool writes is composed in the order it declares, and every file it
# reads is trusted to be. Same pose, different decomposition, and the matrix is
# the thing both sides agree on.
#
# Column-vector convention throughout, matching the engine: in "YZX", X is
# applied first and Y last.

def _axis_matrix(axis, rad):
    c, s = math.cos(rad), math.sin(rad)
    if axis == "X":
        return ((1.0, 0.0, 0.0), (0.0, c, -s), (0.0, s, c))
    if axis == "Y":
        return ((c, 0.0, s), (0.0, 1.0, 0.0), (-s, 0.0, c))
    return ((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0))


def _mat_mul(a, b):
    return tuple(tuple(sum(a[r][k] * b[k][col] for k in range(3))
                       for col in range(3)) for r in range(3))


def euler_to_matrix(order, angles):
    """R = R_order[0](angles[0]) . R_order[1](angles[1]) . R_order[2](angles[2])"""
    m = _axis_matrix(order[0], angles[0])
    m = _mat_mul(m, _axis_matrix(order[1], angles[1]))
    return _mat_mul(m, _axis_matrix(order[2], angles[2]))


_EVEN = {"XYZ", "YZX", "ZXY"}       # even permutations of (X, Y, Z)


def matrix_to_euler(order, m):
    """The three angles that compose to `m` in `order`. Tait-Bryan only --
    every order in EULER_ORDERS uses three distinct axes, so the repeated-axis
    (proper Euler) case cannot arise here."""
    i = "XYZ".index(order[0])
    j = "XYZ".index(order[1])
    k = "XYZ".index(order[2])
    s = 1.0 if order in _EVEN else -1.0
    sin_b = max(-1.0, min(1.0, s * m[i][k]))
    b = math.asin(sin_b)
    # Gimbal lock: cos(b) == 0 leaves only the sum (or difference) of the outer
    # two angles determined. Put it all on the first and zero the last, which
    # is the conventional resolution and reproduces the same matrix.
    if abs(m[i][k]) > 1.0 - 1e-9:
        a = math.atan2(s * m[j][i], m[j][j])
        c = 0.0
    else:
        a = math.atan2(-s * m[j][k], m[k][k])
        c = math.atan2(-s * m[i][j], m[i][i])
    return (a, b, c)


def convert_euler(angles, from_order, to_order):
    """Re-decompose the same rotation into a different order.

    Identity is a fast path and not merely an optimisation: it keeps an
    unchanged export/import round trip bit-for-bit, so the measured property
    that an untouched clip re-encodes to the bytes it came from survives.
    """
    if from_order == to_order:
        return tuple(angles)
    return matrix_to_euler(to_order, euler_to_matrix(from_order, angles))


_TWO_PI = 2.0 * math.pi


def _nearest_branch(sol, ref):
    """Slide each angle by whole turns to sit as close to `ref` as possible."""
    out = []
    for v, r in zip(sol, ref):
        out.append(v + _TWO_PI * round((r - v) / _TWO_PI))
    return tuple(out)


def equivalent_eulers(order, angles):
    """Both decompositions of the same rotation in `order`.

    A Tait-Bryan triple is not unique: (a, b, c) and (a+pi, pi-b, c+pi) build
    the identical matrix. Which one a formula returns is an artefact of asin's
    principal branch, and picking the wrong one turns a channel that never
    moves into one that swings 180 degrees -- which then reads as an edit, and
    a clip that stores that channel as a single constant is refused.
    """
    a, b, c = angles
    return ((a, b, c), (a + math.pi, math.pi - b, c + math.pi))


def convert_euler_near(angles, from_order, to_order, ref):
    """convert_euler, choosing the equivalent branch closest to `ref`.

    `ref` is the clip's CURRENT angles for that joint, in `to_order`. Both
    candidates describe exactly the same rotation, so this changes no pose --
    it only decides which of the equal descriptions is written, and choosing
    the one nearest what is already stored is what lets an unchanged channel
    stay unchanged.
    """
    if from_order == to_order:
        return tuple(angles)
    m = euler_to_matrix(from_order, angles)
    best, best_d = None, None
    for cand in equivalent_eulers(to_order, matrix_to_euler(to_order, m)):
        cand = _nearest_branch(cand, ref)
        d = max(abs(x - r) for x, r in zip(cand, ref))
        if best_d is None or d < best_d:
            best, best_d = cand, d
    return best


def nearest_equivalent(order, angles, ref):
    """The description of this same rotation that sits closest to `ref`.

    The same job convert_euler_near does, without the matrix, for the case
    where no conversion is needed because the order already matches. A
    Tait-Bryan triple has an exact algebraic twin and any angle may be slid by
    whole turns, so "the numbers changed" and "the pose changed" are different
    questions -- and a tool that re-decomposes a rotation answers the first
    without touching the second.

    Returns `angles` UNCHANGED, the same objects, when it is already the
    nearest description. The caller uses that to leave the row alone, which is
    what keeps an unedited round trip byte-for-byte identical.
    """
    best, best_d = None, None
    for cand in equivalent_eulers(order, angles):
        cand = _nearest_branch(cand, ref)
        d = max(abs(x - r) for x, r in zip(cand, ref))
        if best_d is None or d < best_d:
            best, best_d = cand, d
    return best


def writable_rig(bones):
    """(True, "") if BVH can represent this skeleton, else (False, why).

    Public so the Animation tab can ask BEFORE offering an Export button,
    rather than after the click. The only structural thing BVH cannot express
    is a forest: it allows exactly one ROOT, and 2 of the 236 skeletons in
    SWAT.DAT are not trees -- blk.skel (roots root, cof_01, gat) and own01.skel
    (root, skel_ptl), both measured.

    Re-parenting the strays under bones[0] would fix the shape and change the
    pose: their offsets are world-relative and their rotations would start
    composing with the first root's. So they are reported, not repaired -- but
    reported early enough that nobody is told "no" after choosing.
    """
    if not bones:
        return False, "this skeleton has no bones"
    kids = {}
    for b in bones:
        kids.setdefault(b.parent, []).append(b)
    reached, stack = set(), [bones[0]]
    while stack:
        b = stack.pop()
        if b.name in reached:
            continue
        reached.add(b.name)
        stack.extend(kids.get(b.name, []))
    if len(reached) == len(bones):
        return True, ""
    stray = [b.name for b in bones if b.name not in reached]
    return False, (
        "this skeleton is a forest, not a tree: %d bone(s) do not descend "
        "from %s (%s%s). BVH allows exactly one ROOT, so exporting it would "
        "silently drop those bones while the clip still drives their channels."
        % (len(stray), bones[0].name, ", ".join(stray[:4]),
           ", ..." if len(stray) > 4 else ""))


def to_bvh(a, bones, fps=DEFAULT_FPS, euler="YZX", name="", allow_short=False):
    """Render `a` (an `anim.Anim`) against `bones` (from `skeleton.parse`).

    `bones` must be the list in FILE order, which is depth-first and is the
    order the channels follow. Raises ValueError when the two do not agree on
    the bone count, because exporting a mismatched pair would produce a file
    that looks fine and animates the wrong joints.

    `allow_short=True` says the CALLER has checked that this shorter rig
    really is this clip's rig; see the note on the length check below.
    """
    euler = euler.upper()
    if euler not in EULER_ORDERS:
        raise ValueError("euler must be one of %s" % (EULER_ORDERS,))
    want = (a.channels - 3) // 3
    if a.channels != 3 + 3 * want:
        raise ValueError(
            "%d channels is not 3 + 3*bones, so this clip does not fit the "
            "translation-plus-rotations layout" % a.channels)
    if len(bones) > want:
        raise ValueError(
            "the clip drives %d bones but this skeleton has %d. Pick a "
            "skeleton this clip can fill, or the surplus joints would be "
            "handed somebody else's curves." % (want, len(bones)))
    # SHORTER is allowed *to a caller that has earned it*, and dropping the
    # surplus channels is then not a liberty the tool is taking -- it is what
    # the engine does. The applier at 0x002D55B8 takes its loop count from the
    # SKELETON, so channels past the last bone are never read. Stock data
    # relies on it: pgj.skel is 52 bones and 252 of its 255 clips drive 53.
    # Requiring an exact match made all 255 unexportable against their own
    # character's rig, INCLUDING the one the picker offers by default, so the
    # ordinary path through the Animation tab ended in an error for that whole
    # family. Bone i still reads channels 3+3i..3+3i+2, so every joint that
    # does exist gets exactly the curves it gets in game; only joints the rig
    # does not have go missing.
    #
    # But length alone does not make a rig this clip's rig. "Shorter" covers
    # both pgj.skel missing one coat-tail bone and a 5-bone door hinge handed a
    # 52-bone walk cycle -- and the second exports 5 joints of a man walking,
    # silently, as a file that opens fine in Blender. The evidence that tells
    # them apart is the BONE NAMES, and it is not in this function: nothing in
    # a .anim names its skeleton, so the check needs the disc. Studio.skels_for
    # _clip does exactly that -- for c < want it requires the rig's names to
    # match the clip's canonical prefix over the bones they share -- and its
    # vetted picks are what the two export routes pass here. So the liberty is
    # opt-in: a caller holding that evidence says `allow_short=True`, and a
    # caller that just has two objects of different lengths gets a refusal.
    if len(bones) < want and not allow_short:
        raise ValueError(
            "the clip drives %d bones but this skeleton has %d. A shorter rig "
            "is only this clip's rig if their bone names line up; pick the "
            "skeleton from the clip's own candidate list, or %d triple(s) of "
            "motion would go missing without a word."
            % (want, len(bones), want - len(bones)))

    # BVH has exactly one ROOT, but two of the 139 distinct skeletons on the
    # disc are a FOREST rather than a tree -- `own01.skel` has `root` and
    # `skel_ptl` at depth 0, `blk.skel` has `root`, `cof_01` and `gat`. The
    # emit() below walks down from bones[0], so those extra trees used to be
    # dropped SILENTLY while the clip still carried their curves: `own01`
    # exported 52 joints for a 53-bone, 162-channel clip. That is a file that
    # imports fine and animates the wrong thing, so it is refused instead.
    # Re-parenting the strays under bones[0] would fix the shape but change the
    # pose -- their offsets are world-relative and their rotations would start
    # composing with the first root's -- so it is not done here.
    ok, why = writable_rig(bones)
    if not ok:
        raise ValueError(why)

    out = []
    # NO HEADER. The file starts at HIERARCHY, because that is what BVH is.
    #
    # This used to emit seven "#" lines above HIERARCHY, under a comment
    # saying "BVH has no comment syntax, so this goes above HIERARCHY ...
    # Blender's importer is happy with it." The first half is true and the
    # second was never tested. Blender's BVH importer requires HIERARCHY as
    # the first token and errors on anything before it, so every clip this
    # tool exported had to be opened in a text editor and topped-and-tailed
    # before it would load -- which a tester finally reported.
    #
    # It survived because our own parse_bvh() strips "#" lines, so the
    # export -> import round trip inside this tool passed every time. The
    # only reader that tolerated the header was the one we wrote.
    #
    # What the header said still matters -- the Euler order, and that offsets
    # are in metres -- so it moved to where a user actually reads it: the
    # Animation panel says it beside the export button. A format with no
    # comment syntax is not the place to put documentation.

    # ------------------------------------------------------------- HIERARCHY
    out.append("HIERARCHY")
    by_parent = {}
    for b in bones:
        by_parent.setdefault(b.parent, []).append(b)
    root = bones[0]

    def emit(b, depth, is_root):
        pad = "\t" * depth
        out.append("%s%s %s" % (pad, "ROOT" if is_root else "JOINT", b.name))
        out.append("%s{" % pad)
        o = b.offset
        out.append("%s\tOFFSET %.6f %.6f %.6f" % (pad, o[0], o[1], o[2]))
        if is_root:
            # The root carries the clip's only translation, channels 0,1,2.
            out.append("%s\tCHANNELS 6 Xposition Yposition Zposition %s"
                       % (pad, _rot_channels(euler)))
        else:
            out.append("%s\tCHANNELS 3 %s" % (pad, _rot_channels(euler)))
        kids = by_parent.get(b.name, [])
        for k in kids:
            emit(k, depth + 1, False)
        if not kids:
            # BVH requires every leaf to declare an End Site. There is no
            # length in the data to use, so a short stub along the bone's own
            # offset direction keeps the skeleton visible in Blender without
            # inventing a measurement.
            L = math.sqrt(o[0] ** 2 + o[1] ** 2 + o[2] ** 2) or 0.02
            out.append("%s\tEnd Site" % pad)
            out.append("%s\t{" % pad)
            out.append("%s\t\tOFFSET 0.000000 %.6f 0.000000" % (pad, L))
            out.append("%s\t}" % pad)
        out.append("%s}" % pad)

    emit(root, 0, True)

    # ---------------------------------------------------------------- MOTION
    # The STORED keys are emitted, not a resampling of them: a half-rate clip
    # keeps every other game frame, so doubling the frame time reproduces the
    # original timing exactly instead of interpolating and introducing error.
    step = (2.0 if a.half_rate else 1.0) / float(fps)
    out.append("MOTION")
    out.append("Frames: %d" % a.keys)
    out.append("Frame Time: %.8f" % step)

    deg = 180.0 / math.pi
    for k in range(a.keys):
        p = a.at_key(k)
        vals = [p[0], p[1], p[2]]                       # root translation, m
        for i in range(len(bones)):
            base = 3 + 3 * i
            # The clip stores ch0/ch1/ch2 as the X/Y/Z angles of a rotation the
            # engine composes as Ry(ch1).Rz(ch2).Rx(ch0) -- so as an ordered
            # triple that is (ch1, ch2, ch0) in "YZX".
            #
            # Re-decompose it into whatever order this file will DECLARE, so
            # the numbers are true for the CHANNELS line above them. This used
            # to reorder the same three numbers instead, which made every order
            # but YZX a file that claimed a composition its values were not
            # for. `euler="YZX"` takes the identity fast path and is unchanged
            # down to the byte.
            triple = convert_euler(
                (p[base + 1], p[base + 2], p[base]), "YZX", euler)
            vals.extend(v * deg for v in triple)
        out.append(" ".join("%.6f" % v for v in vals))

    return "\n".join(out) + "\n"


# ===========================================================================
# IMPORT -- an edited BVH back into a `.anim`
# ===========================================================================
#
# WHY THIS IS SHAPED THE WAY IT IS. A `.anim` lives in a fixed archive slot and
# the Dave archives are patched in place rather than repacked (repacking is
# NOT fatal -- that was tested and disproved on 2026-07-31 -- but in-place
# black-screen freeze -- see dave.patch, which raises SlotOverflow rather than
# move anything). So the default here is a SAME-SHAPE re-encode, and for the
# cm2 codec that means:
#
#   * the header, the animated-channel bitmask and both trailing scales are
#     copied from the original file byte for byte;
#   * only the constants array (s16[C-N]) and the key matrix (s16[N*K]) are
#     rewritten, and both are a fixed size once N, C and K are fixed.
#
# The other two codecs keep the same promise by their own route -- see the
# block above _parse_any, and _encode_cmp / _encode_raw.
#
# That makes the output the same LENGTH as the input, always, which is the only
# property that makes this safe to run unattended. Anything that would change
# N, K or the half-rate flag resizes the payload and is REFUSED unless the
# caller explicitly opts in with `allow_reshape`, and even then the caller still
# has to check the result against the archive slot -- note that a same-length
# file is NOT automatically a same-STORED-length file, because most .anim
# entries are deflated and different bytes compress differently.
#
# THE QUANTISER, and the one place this deliberately differs from the game.
# The engine's own encoder quantises at 0x002D2E58:
#
#     0x002D2E58  mul.s    f13, f13, f12     ; value * scale
#     0x002D2E5C  lui      at, 0x46FF        ; \ 0x46FFFE00 =  32767.0f
#     0x002D2E60  ori      at, at, 0xFE00    ; /
#     0x002D2E68  c.lt.s   f0, f13           ; > 32767 -> return  32767
#     0x002D2E78  lui      at, 0xC700        ; 0xC7000000 = -32768.0f
#     0x002D2E80  c.lt.s   f13, f0           ; < -32768 -> return -32768
#     0x002D2E8C  cvt.w.s  f0, f13           ; TRUNCATES toward zero
#
# The rails are reproduced exactly. The rounding is NOT: `cvt.w.s` on the R5900
# truncates (anim.py already records that at 0x002D1A60), and truncation cannot
# round-trip a value that was itself produced by dequantising. Measured over
# 348,204 samples from 80 clips, re-encoding a decoded value with truncation
# reproduces the original s16 for only 94.24% of samples -- 20,053 come back one
# unit low -- while round-half-away-from-zero reproduces 100% of them. So
# `rounding="nearest"` is the default and `rounding="truncate"` is offered only
# so that claim stays testable (verify_anim_import.py measures it).
#
# THE DEAD BAND, which is what actually makes an unedited import a no-op.
# to_bvh prints "%.6f", so the text carries a value to within 1e-6 of a metre
# (channels 0-2) or 1e-6 of a degree (channels 3+). For a typical clip that is
# nothing -- scaleB is around 10430, so a quantiser step is 0.0055 degrees and
# the text is ~5,500x finer. But scaleA is 32768/max|v| over its own group, so a
# clip whose root barely moves gets an enormous scale: the largest on the disc
# is 1.48e20, where one quantiser step is 7e-21 m and "%.6f" cannot express it.
# 157 of the 3,677 distinct cm2 clips are past the limit for group A and 4 are
# past it for group B. For those, blind re-quantisation of the printed text
# would rewrite bytes that nobody edited.
#
# So a sample whose new value is within `tolerance` of the ORIGINAL decoded
# value keeps its ORIGINAL raw s16. That is not a fudge and it is not a
# comparison against what we are about to write -- it is the statement "a BVH
# cannot express a difference this small, so a difference this small is not an
# edit". Pass tolerance=0.0 to disable it and quantise everything blind; the
# verifier runs both ways so the quantiser is tested without the dead band
# hiding a +/-1 error in it.
#
# THE DEAD BAND IS NOT ENOUGH ON ITS OWN, and believing it was is what made
# this feature fail on every clip a tester tried. It only catches a value that
# came back the SAME. Round a file through a DCC and nothing comes back the
# same: the angles are re-decomposed out of a float32 matrix, so they land a
# few thousandths of a degree away, over the band and into the quantiser. For
# an animated channel that is harmless -- it re-quantises to the same s16 or to
# one next door. For a channel the clip stores as a SINGLE CONSTANT it was
# fatal, because two neighbouring s16 values are a curve and a curve resizes
# the file. See _flatten_constants, which is the other half of this.
#
# The same dead band is what lets the 176 clips with a ZERO scale survive a
# round trip at all. When a scale is 0.0 the whole group decodes to exactly 0.0
# (0x002D2D60 / 0x002D2DA4 store 0.0 rather than dividing), so those channels
# carry no recoverable information and no value written to them can ever decode
# to anything but 0.0. Their original raws are preserved and the report names
# the group, rather than silently zeroing bytes or silently dropping an edit.
#
# WHAT IS NOT VERIFIED, stated plainly. The importer demuxes the motion columns
# by the axis NAME in each joint's CHANNELS line, so it is exactly inverse to
# to_bvh whatever `euler=` that ran with. What it cannot check is whether a
# third-party tool RE-COMPOSED the angles: if Blender exported with
# rotation_mode left at its default instead of the engine's order, the three
# numbers per joint are a different decomposition of the same pose and this will
# write them straight through as if they were not. Pass `euler=` to assert the
# order the file must declare; there is no way to detect the rest from the file.

# One unit in the last place of to_bvh's "%.6f", in the units it prints:
# metres for channels 0-2, degrees for 3+.
TEXT_ULP = 1e-6

# The encoder's clamp rails, 0x002D2E5C and 0x002D2E78.
RAIL_HI = 32767.0
RAIL_LO = -32768.0

# The engine's own encoder calls a channel animated when some key differs from
# key 0 by 33 quantiser units or more -- `slti v0, v0, 33` at 0x002D31D4, inside
# the loop at 0x002D3198-0x002D3224. Reproduced here only as a default the
# caller may ask for; `reshape_threshold=0` (this module's default) keeps every
# channel that varies at all, because dropping a small motion is a worse failure
# for a modding tool than a slightly larger N.
ENGINE_CONSTANT_THRESHOLD = 33

_ROT_AXIS = {"XROTATION": 0, "YROTATION": 1, "ZROTATION": 2}
_POS_AXIS = {"XPOSITION": 0, "YPOSITION": 1, "ZPOSITION": 2}


class ImportRefused(ValueError):
    """This BVH will not be written, and why.

    Deliberately a ValueError so the existing `except ValueError` around the
    exporter catches it too, and deliberately its own type so a caller can tell
    "you handed me a file I will not accept" apart from a genuine bug.
    """


def _f32(x):
    """Round to float32. Mirrors anim._f32 -- the two must agree exactly."""
    return struct.unpack("<f", struct.pack("<f", x))[0]


def _quantise(value, scale, rounding="nearest"):
    """One float -> one s16, the way 0x002D2E58 does it.

    The multiply and the rail tests happen in float32 because they do on the
    console; doing them in float64 would move the clamp boundary.
    """
    p = _f32(_f32(value) * _f32(scale))
    if not (p == p) or p in (float("inf"), float("-inf")):
        raise ImportRefused("a value in the BVH is not finite")
    if p > RAIL_HI:                       # 0x002D2E68 c.lt.s f0, f13
        return 32767, True
    if p < RAIL_LO:                       # 0x002D2E80 c.lt.s f13, f0
        return -32768, True
    if rounding == "truncate":            # 0x002D2E8C cvt.w.s, the game's own
        return int(p), False
    # Round half away from zero. NOT Python's round(), which is half-to-even and
    # would bias a curve that sits on .5 boundaries.
    return (int(p + 0.5) if p >= 0.0 else -int(-p + 0.5)), False


# ---------------------------------------------------------------- BVH reading

class BvhMotion:
    """A parsed BVH: the joint order, the channel layout and the raw rows.

    Nothing here is interpreted as animation yet -- `bvh_to_anim` does that --
    because the two failures worth telling apart are "this is not a BVH" and
    "this is a BVH of the wrong clip".
    """

    __slots__ = ("joints", "channels", "frames", "frame_time", "rows", "width")

    def to_json(self):
        return dict(joints=len(self.joints), frames=self.frames,
                    frame_time=self.frame_time, width=self.width,
                    names=self.joints)


def parse_bvh(text):
    """Read a BVH into a `BvhMotion`. Raises ImportRefused on anything odd.

    Tolerant in exactly one direction: leading `#` comment lines are skipped,
    because to_bvh writes them and BVH has no comment syntax of its own. Not
    tolerant about structure -- a file whose MOTION rows are the wrong width is
    the single most likely way to import a clip that is silently shifted by one
    axis, so the width is checked against the CHANNELS declarations rather than
    assumed.
    """
    if isinstance(text, (bytes, bytearray)):
        text = bytes(text).decode("utf-8", "replace")
    lines = [l.strip() for l in text.replace("\r\n", "\n").split("\n")]
    lines = [l for l in lines if l and not l.startswith("#")]
    if not lines or lines[0].upper() != "HIERARCHY":
        raise ImportRefused("this does not start with HIERARCHY, so it is not "
                            "a BVH file")
    try:
        mi = next(i for i, l in enumerate(lines) if l.upper() == "MOTION")
    except StopIteration:
        raise ImportRefused("there is no MOTION section, so this BVH carries a "
                            "skeleton but no animation")

    m = BvhMotion()
    m.joints = []
    m.channels = []
    depth = 0
    pending = None          # joint whose CHANNELS line has not been seen yet
    in_end_site = False
    for l in lines[1:mi]:
        head = l.split()
        key = head[0].upper() if head else ""
        if key in ("ROOT", "JOINT"):
            if len(head) < 2:
                raise ImportRefused("a %s line has no name" % key)
            pending = " ".join(head[1:])
            in_end_site = False
        elif key == "END":                      # "End Site"
            in_end_site = True
        elif l == "{":
            depth += 1
            if pending is not None:
                m.joints.append(pending)
                m.channels.append(None)
                pending = None
        elif l == "}":
            depth -= 1
            in_end_site = False
        elif key == "CHANNELS":
            if in_end_site:
                raise ImportRefused("an End Site declares CHANNELS, which is "
                                    "not legal BVH")
            if not m.joints:
                raise ImportRefused("a CHANNELS line appears before any joint")
            n = int(head[1])
            names = head[2:]
            if len(names) != n:
                raise ImportRefused(
                    "%s declares CHANNELS %d but lists %d of them"
                    % (m.joints[-1], n, len(names)))
            if m.channels[-1] is not None:
                raise ImportRefused("%s declares CHANNELS twice" % m.joints[-1])
            m.channels[-1] = [c.upper() for c in names]
    if depth != 0:
        raise ImportRefused("braces do not balance in the HIERARCHY block")
    missing = [n for n, c in zip(m.joints, m.channels) if c is None]
    if missing:
        raise ImportRefused("%d joint(s) declare no CHANNELS, starting with %s"
                            % (len(missing), missing[0]))
    if not m.joints:
        raise ImportRefused("the HIERARCHY block declares no joints")

    # ---- MOTION
    rest = lines[mi + 1:]
    if len(rest) < 2:
        raise ImportRefused("the MOTION section is missing Frames/Frame Time")
    if not rest[0].upper().startswith("FRAMES"):
        raise ImportRefused("MOTION does not begin with a Frames: line")
    try:
        m.frames = int(rest[0].split(":", 1)[1])
    except (IndexError, ValueError):
        raise ImportRefused("cannot read a frame count from %r" % rest[0][:40])
    if not rest[1].upper().startswith("FRAME TIME"):
        raise ImportRefused("MOTION has no Frame Time: line")
    try:
        m.frame_time = float(rest[1].split(":", 1)[1])
    except (IndexError, ValueError):
        raise ImportRefused("cannot read a frame time from %r" % rest[1][:40])

    m.width = sum(len(c) for c in m.channels)
    m.rows = []
    for i, l in enumerate(rest[2:]):
        parts = l.split()
        if len(parts) != m.width:
            raise ImportRefused(
                "motion row %d has %d numbers but the CHANNELS declarations "
                "add up to %d -- a row of the wrong width would shift every "
                "later joint by an axis" % (i, len(parts), m.width))
        try:
            m.rows.append([float(x) for x in parts])
        except ValueError:
            raise ImportRefused("motion row %d contains something that is not "
                                "a number" % i)
    if len(m.rows) != m.frames:
        raise ImportRefused("Frames: says %d but there are %d motion rows"
                            % (m.frames, len(m.rows)))
    return m


def declared_orders(m):
    """Each joint's rotation order, as it is declared in its CHANNELS line.

    BVH allows a different order per joint, and a DCC will happily write one,
    so this is a list rather than a single value.
    """
    out = []
    for names in m.channels:
        order = "".join(nm[0] for nm in names if nm in _ROT_AXIS)
        out.append(order)
    return out


def recompose_rows(m, cols, joints, reference=None):
    """Rewrite the motion rows so every joint's angles are the engine's YZX
    decomposition, whatever order the file declared.

    Returns (converted_joints, orders_seen, snapped_joints).

    The rotation is the invariant. A DCC that re-exports with its own channel
    order has not changed the pose, it has written a different decomposition
    of the same pose, so converting is the only reading under which the file
    means what it says.

    THE SAME IS TRUE WITHIN ONE ORDER, and that is the second half of this.
    A joint declaring the engine's own YZX used to be skipped entirely -- and
    for a file this tool wrote and nobody else touched, that is right and it is
    what keeps the round trip byte-exact. But a tool that loads the angles and
    writes them back returns the PRINCIPAL branch of the decomposition, and
    stock clips do not all sit on it: a rotation stored as 190 degrees comes
    back as -170, the identical pose written as a number 360 away. Left alone,
    that reads as an enormous edit -- it saturates the quantiser rail, or lands
    on a channel stored as a constant and gets the whole file refused.

    So when a `reference` is available every joint is checked against the
    clip's own current angles and slid to the nearest equal description. A
    joint already on the nearest one is not written at all, so the byte-exact
    round trip survives; `snapped_joints` counts the ones that were not.
    """
    orders = declared_orders(m)
    convert = [j for j, o in enumerate(orders) if o != "YZX" and len(o) == 3]
    if reference is None:
        todo = convert
    else:
        todo = [j for j, o in enumerate(orders) if len(o) == 3]
    if not todo:
        return 0, sorted(set(orders)), 0
    deg = 180.0 / math.pi
    rad = math.pi / 180.0
    snapped = set()
    for k, row in enumerate(m.rows):
        for j in todo:
            o = orders[j]
            cx = cols[3 + 3 * j + 0]
            cy = cols[3 + 3 * j + 1]
            cz = cols[3 + 3 * j + 2]
            by_axis = {"X": cx, "Y": cy, "Z": cz}
            # values in the order the file declares them, in radians
            vals = tuple(row[by_axis[ax]] * rad for ax in o)
            if reference is None:
                yang, zang, xang = convert_euler(vals, o, "YZX")
            else:
                # The clip's own current angles for this joint, as a YZX
                # triple, so an equal decomposition near them can be chosen
                # over one that differs by half a turn on two axes.
                rx, ry, rz = reference(k, j)
                if o == "YZX":
                    got = nearest_equivalent("YZX", vals, (ry, rz, rx))
                    if got == vals:
                        continue          # already the nearest; do not touch
                    snapped.add(j)
                    yang, zang, xang = got
                else:
                    yang, zang, xang = convert_euler_near(
                        vals, o, "YZX", (ry, rz, rx))
            # channels ch0/ch1/ch2 are the X/Y/Z angles of that YZX composition
            row[cx] = xang * deg
            row[cy] = yang * deg
            row[cz] = zang * deg
    return len(convert), sorted(set(orders)), len(snapped)


def _column_map(m, joints_wanted, extra_pos=None):
    """Motion-column index for every animation channel, or raise.

    Returns a list of `3 + 3*joints` column indices: the root's three positions
    first, then each joint's rotX, rotY, rotZ -- IN ANIMATION CHANNEL ORDER, not
    in the order the BVH prints them. Demuxing by the axis name is what makes
    this exactly inverse to to_bvh whatever Euler order it wrote.

    `extra_pos`, if a list is passed, collects `(joint_index, joint_name,
    column)` for every POSITION channel on a joint that is not the root. Those
    columns have nowhere to go -- the clip stores exactly three translation
    channels -- so the caller has to decide what they mean; see bvh_to_anim.
    """
    # FEWER joints than the clip drives is the shorter-rig case and is allowed,
    # because to_bvh will now write one: pgj.skel is 52 bones and its clips
    # drive 53, which is what the engine itself runs. Refusing it here would
    # have let someone export a pgj clip, spend an evening on it in Blender,
    # and then find the tool would not take it back -- worse than refusing the
    # export outright. The trailing bones simply have no column; the caller
    # leaves their original channel values alone, so what the BVH does not
    # mention is not changed.
    #
    # MORE joints is still refused. That is a BVH from a different skeleton,
    # and there is nowhere for the surplus curves to go.
    if len(m.joints) > joints_wanted:
        raise ImportRefused(
            "the BVH has %d joints but this clip drives only %d. This BVH "
            "belongs to a different skeleton or a different clip."
            % (len(m.joints), joints_wanted))

    cols = [None] * (3 + 3 * joints_wanted)
    base = 0
    for j, names in enumerate(m.channels):
        rot = {}
        pos = {}
        for k, nm in enumerate(names):
            if nm in _ROT_AXIS:
                rot[_ROT_AXIS[nm]] = base + k
            elif nm in _POS_AXIS:
                pos[_POS_AXIS[nm]] = base + k
            else:
                raise ImportRefused("joint %s declares an unknown channel %r"
                                    % (m.joints[j], nm))
        base += len(names)
        if len(rot) != 3:
            raise ImportRefused(
                "joint %s declares %d rotation channel(s); every joint needs "
                "all three, because the clip stores three angles per bone"
                % (m.joints[j], len(rot)))
        if j == 0:
            if len(pos) != 3:
                raise ImportRefused(
                    "the ROOT joint %s declares %d position channel(s); it "
                    "needs all three, because channels 0-2 of the clip are the "
                    "root translation" % (m.joints[0], len(pos)))
            for i in range(3):
                cols[i] = pos[i]
        elif pos:
            # NOT a refusal any more, and this one cost a tester a whole
            # evening. Blender's BVH exporter has a "Root Translation Only"
            # checkbox; with it OFF -- which is not the default everywhere and
            # is easy to miss -- EVERY joint gets Xposition Yposition Zposition
            # as well as its rotations. The clip has exactly three translation
            # channels, so those columns have nowhere to go, but that does not
            # make them an error: what Blender writes there for a bone nobody
            # translated is the bone's own rest offset, the same number on
            # every frame. Carrying no motion, they carry no edit either.
            #
            # So they are collected and the CALLER checks whether they move.
            # Constant -> ignored, and said so in the report. Moving -> refused
            # by name, because a bone that really was translated is an edit
            # this format cannot store and dropping it silently is exactly the
            # failure this module exists to avoid.
            if extra_pos is None:
                raise ImportRefused(
                    "joint %s declares position channels; only the root may, "
                    "because the clip has exactly three translation channels"
                    % m.joints[j])
            for i in sorted(pos):
                extra_pos.append((j, m.joints[j], pos[i]))
        for i in range(3):
            cols[3 + 3 * j + i] = rot[i]
    return cols


# ---------------------------------------------------------------- re-encoding

# `.anim` is three codecs wearing one extension, and until now the importer
# only knew one of them. Counted over every `.anim` on the disc:
#
#     cm2   10,230   the quantised one, and the only one this used to take
#     cmp      666   fixed 32-bit words, three channels packed per word
#     raw      242   float32 samples, no compression at all
#
# The EXPORT route already writes a BVH for all three (studio.py chose that
# deliberately -- the uncompressed ones are the accessory clips, a character's
# hair, scarf, coat tail and jaw). The import route did not, so 908 clips --
# 8.2% of the disc, and every accessory animation on it -- could be exported to
# Blender, edited, and then refused on the way back with "is not a cm2 clip".
# That is a dead end the tool led the user into, and it is one of the two
# things that made anim import fail on a tester's clips.
#
# All three now re-encode IN PLACE, which is the property that keeps the file
# inside its archive slot:
#
#   * cm2  -- header, mask and both scales copied; constants and key matrix
#             rewritten.  Same length by construction.
#   * raw  -- one float32 per channel per key, and neither count can change.
#             Same length by construction (animraw.RawAnim.pack checks it).
#   * cmp  -- header, the per-track scale/bias/index records and the column
#             count copied; only the packed words are rewritten. Verified over
#             all 666 files on the disc: the size identity holds exactly, no
#             two channels sharing a column share a bit slot (max 3 channels
#             per column, one per slot), and re-packing the decoded words
#             reproduces the file byte for byte.


def _parse_any(original):
    """(codec, parsed clip) for whichever of the three codecs this is.

    Every failure comes back as ImportRefused rather than each codec's own
    exception type, because the caller's question is "will you take this file",
    not "which parser was tried".
    """
    magic = original[:4]
    codec = ("cmp" if magic == _cmp.MAGIC else
             "raw" if magic == _raw.MAGIC else "cm2")
    parser = {"cmp": _cmp.parse, "raw": _raw.parse, "cm2": _anim.parse}[codec]
    try:
        return codec, parser(original)
    except (_cmp.CmpError, _raw.RawError, _anim.AnimError) as ex:
        raise ImportRefused(
            "the clip this would replace does not read back as a %s "
            "animation, so there is nothing to write an edit into: %s"
            % ({"cmp": "cmp", "raw": "an uncompressed", "cm2": "cm2"}[codec],
               ex))


# What separates "somebody edited this channel" from "the tool that wrote this
# BVH did its arithmetic in float32". These are PHYSICAL sizes, in the units
# to_bvh prints, rather than quantiser units -- because a quantiser unit is not
# always a meaningful size. cm2 gives channels 0-2 one shared scale, and a clip
# whose root barely moves gets a scale so large that one unit is far below
# anything "%.6f" can print (the largest on the disc is 1.48e20, where a unit
# is 7e-21 m); cmp collapses a constant track's scale to nothing (median
# 2.4e-7 rad over the disc's 45,612 constant tracks). Measured against both
# ends rather than picked:
#
#   * BELOW the engine's own test for "is this channel animated",
#     `slti v0, v0, 33` at 0x002D31D4 -- 33 quantiser units is 0.18 degrees at
#     the disc's median rotation scale and 33 mm at the default translation
#     scale. So every channel this flattens is one the game's OWN encoder
#     would have stored as a single constant too.
#   * ABOVE what a round trip through a DCC actually costs. Composing every
#     joint's angles into a float32 matrix, decomposing them again into the
#     tool's own rotation order and printing them at "%.6f" moves a constant
#     channel by at most 0.000100 degrees, and a constant root translation not
#     at all. Measured over 120 stock clips in both tool orders, and measured
#     on exactly the quantity these are compared against -- the SPAN of one
#     channel across the keys, not the error of a single sample.
#     scripts/verify_anim_dcc.py re-measures it on every run and fails if
#     either allowance stops covering it.
#
# So the window is 0.0001 degrees of real drift, a 0.02-degree allowance, and
# 0.18 degrees before the game itself would have called the channel animated.
# 200x of headroom below, 9x above.
CONST_NOISE_DEG = 0.02
CONST_NOISE_M = 1e-4


def _bvh_unit(c):
    """to_bvh prints channels 0-2 in metres and 3+ in degrees, so a comparison
    against the text has to happen in the unit the text was printed in."""
    return 1.0 if c < 3 else 180.0 / math.pi


def _median(v):
    s = sorted(v)
    n = len(s)
    if not n:
        return None
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def _constant_verdict(orig_value, curve, spread_limit):
    """One channel the clip stores as a single value, handed a curve.

    -> (value to keep it constant at, spread) when the curve is noise, or
    (None, spread) when it is an edit big enough to need the channel animated.

    The value already on the disc wins unless the keys have genuinely moved
    AWAY from it -- the same line that separates a curve from noise decides
    that too, so a channel nobody touched keeps its exact byte and the round
    trip stays byte-for-byte. Only when the keys have settled somewhere else
    has the channel really been retargeted to a different constant, and then
    the median of the keys is what it was retargeted to: the median rather
    than the mean or the first key because it is the one summary a few noisy
    outliers cannot drag.
    """
    lo, hi = min(curve), max(curve)
    spread = hi - lo
    if spread > spread_limit:
        return None, spread
    mid = _median(curve)
    if lo <= orig_value <= hi or abs(mid - orig_value) <= spread_limit:
        return orig_value, spread
    return mid, spread


def _const_noise(c, k=1.0):
    """How far channel `c` may wobble and still count as noise, in the unit
    to_bvh prints it in. `k` is the caller's noise_floor multiplier."""
    return k * (CONST_NOISE_M if c < 3 else CONST_NOISE_DEG)


def _flat_maxima(flattened, k=1.0):
    """The flattening figures, split by the unit each number is in.

    `flattened` carries spans in the unit to_bvh PRINTS -- metres for channels
    0-2 and degrees above them -- so one "largest wobble" over the whole list
    is a number whose unit depends on which channel produced it. A caller
    printing that has to either work the unit out from the channel indices or
    drop it, and dropping the unit off a measurement is how a millimetre gets
    read as a degree.

    So each family gets its own maximum, `None` when that family flattened
    nothing, and the allowance is stated beside them rather than left to be
    re-derived from CONST_NOISE_DEG and the noise_floor multiplier.
    `flattened_max_units` is kept as the larger of the two for callers written
    against the first version of this report.
    """
    deg = [s for c, s in flattened if c >= 3]
    met = [s for c, s in flattened if c < 3]
    return dict(
        flattened_max_degrees=(max(deg) if deg else None),
        flattened_max_metres=(max(met) if met else None),
        flattened_max_units=max([s for _, s in flattened], default=0),
        noise_allowance_degrees=CONST_NOISE_DEG * k,
        noise_allowance_metres=CONST_NOISE_M * k,
    )


def _flatten_constants(new_raw, orig_raw, orig_animated, varies,
                       step_printed, k=1.0):
    """Keep the constant channels constant. -> (flattened, still_broken).

    THIS IS THE BUG THAT MADE ANIMATION IMPORT FAIL ON EVERY CLIP A TESTER
    TRIED, and it is worth writing down at length because the old behaviour
    looked principled.

    A cm2 clip stores most of its channels as ONE s16: a bone that does not
    move in this clip has no row in the key matrix at all. Roughly 60% of the
    channels in a stock humanoid clip are like that. Giving one of them a curve
    changes the bitmask, the constants array and the matrix all at once, which
    resizes the file -- so the importer refused, and told the caller to pass
    allow_reshape.

    That refusal is right about the format and wrong about the input. Round a
    clip through ANY DCC -- it does not have to be Blender -- and every angle
    comes back as a re-decomposition of a float32 matrix rather than the number
    that went in. The error is tiny, a few thousandths of a degree, but it is
    not zero, and a constant channel sitting near a quantiser boundary flips
    one unit up on some keys and not on others. That is a "curve": two distinct
    s16 values. With 480 channels in a humanoid clip, at least one of them
    lands on a boundary essentially every time, so every clip was refused.
    MEASURED on 25 stock clips with noise injected at the printed precision:
    1e-5 degrees already refuses 5 of 25, and 1e-2 degrees -- still far below
    what is visible, and roughly what a real armature round trip produces --
    refuses 25 of 25. That is "it errors with all of my tests".

    So the question is not "did these keys come back identical" but "is this
    an EDIT" -- and CONST_NOISE_DEG / CONST_NOISE_M are where that line is
    drawn, below the engine's own idea of an animated channel and above what a
    DCC round trip costs. A span under the line is flattened back to a single
    value; a span over it is a real edit, still needs a reshape, and is still
    refused. Every flattened channel is named in the report, so this is never
    a thing that happens quietly.

    `step_printed(c)` gives channel c's quantiser step in the unit to_bvh
    prints, which is what converts that physical line into the s16 units these
    curves are counted in. Where the step is finer than the text can print --
    the huge-scaleA clips -- the limit comes out enormous and the channel is
    always flattened, which is the right answer: the BVH cannot express a
    difference that small, so no difference that small is an edit.
    """
    flattened = []
    broke = []
    for c in range(len(new_raw)):
        if orig_animated[c] or varies[c] == 0:
            continue
        step = step_printed(c)
        # Never tighter than ONE quantiser unit, whatever the physical
        # allowance works out to. Two adjacent s16 values are a channel
        # sitting on a rounding boundary, not an edit -- and for channels 0-2
        # the step is often coarser than the allowance (the default scaleA
        # puts a unit at a millimetre, ten times CONST_NOISE_M), which would
        # otherwise refuse a file for a difference of one bit.
        # k=0 asks for no allowance at all, which is the rule this replaced;
        # the verifier runs both ways so "these clips used to be refused" is
        # a checkable claim rather than a story in a commit message.
        if not k:
            limit = 0.0
        elif not step:
            limit = float("inf")
        else:
            limit = max(1.0, _const_noise(c, k) / step)
        keep, spread = _constant_verdict(orig_raw[c][0], new_raw[c], limit)
        if keep is None:
            broke.append((c, spread * step))
            continue
        flattened.append((c, spread * step))
        new_raw[c] = [int(round(keep))] * len(new_raw[c])
    return flattened, broke


def bvh_to_anim(original, text, bones=None, euler=None, tolerance=TEXT_ULP,
                rounding="nearest", allow_reshape=False, reshape_threshold=0,
                noise_floor=1.0):
    """An edited BVH back into `.anim` bytes. -> (bytes, report dict).

    Takes all three `.anim` codecs -- cm2, cmp and the uncompressed one -- and
    re-encodes in place, so the result is the same length as what it replaces.

    `original` is the CURRENT contents of the file being replaced, and it is
    required rather than optional: the animated-channel bitmask, both scales,
    the flags and the key count all come from it, and re-deriving them would be
    how a same-shape edit turns into a resize.

    `noise_floor` scales how far a channel the clip stores as a SINGLE
    CONSTANT may wobble in the BVH before that wobble is read as a real edit
    rather than as arithmetic noise from the tool that wrote the file. 1.0 is
    CONST_NOISE_DEG / CONST_NOISE_M; 0 turns the allowance off entirely, which
    is how the verifier tests the strict behaviour this replaced. See
    _flatten_constants for why there is an allowance at all.

    `bones` is optional and is only used to cross-check joint NAMES; a mismatch
    is reported, not refused, because renaming a joint in a DCC is harmless.
    A mismatched joint COUNT is always refused.

    `euler`, if given, asserts the rotation order the BVH must declare. Leave it
    None to accept whatever the file says -- the demux is by axis name either
    way. See the block comment above for the part this cannot check.

    Raises ImportRefused, with a message meant to be shown to a person, for
    every case where writing would be wrong or would change the file's size
    without the caller having asked for that.
    """
    if rounding not in ("nearest", "truncate"):
        raise ImportRefused("rounding must be 'nearest' or 'truncate'")
    original = bytes(original)
    codec, a = _parse_any(original)

    want_joints = (a.channels - 3) // 3
    if a.channels != 3 + 3 * want_joints:
        raise ImportRefused(
            "%d channels is not 3 + 3*bones, so this clip does not fit the "
            "translation-plus-rotations layout and there is nothing to map a "
            "BVH onto" % a.channels)

    m = parse_bvh(text)
    if euler is not None:
        # An OPTIONAL assertion, for a caller that knows what the file should
        # declare and wants to be told when it does not. It is no longer how
        # correctness is achieved -- see recompose_rows -- so nothing in the
        # app passes it. It used to be passed automatically from the export
        # dropdown, which meant a clip edited in Blender was refused every
        # time, because Blender writes its own channel order on the way out.
        euler = euler.upper()
        if euler not in EULER_ORDERS:
            raise ImportRefused("euler must be one of %s" % (EULER_ORDERS,))
        want = [_AXIS[c].upper() for c in euler]
        got = [c for c in m.channels[0] if c in _ROT_AXIS]
        if got != want:
            raise ImportRefused(
                "the BVH declares rotations as %s but %s was asserted"
                % (" ".join(got), " ".join(want)))
    extra_pos = []
    cols = _column_map(m, want_joints, extra_pos=extra_pos)

    # Position channels on a joint that is not the root (Blender's "Root
    # Translation Only" left unticked). They have nowhere to go, so the only
    # question is whether they carry an edit. Constant down the whole clip
    # means nobody translated that bone and the columns are the rest offset
    # repeated; anything else is a translation this format cannot store, and
    # writing the file without it would be the silent kind of wrong.
    ignored_positions = []
    for j, jname, col in extra_pos:
        vals = [row[col] for row in m.rows]
        span = max(vals) - min(vals) if vals else 0.0
        if span > tolerance:
            raise ImportRefused(
                "joint %s is translated by %.4f over the clip, but only the "
                "root has translation channels in this format -- a clip "
                "stores three translation channels and %d*3 rotations, "
                "nothing else. Re-export with Blender's \"Root Translation "
                "Only\" ticked, or move the bone by rotating its parent."
                % (jname, span, want_joints))
        ignored_positions.append(jname)

    # Whatever order each joint declared, express it as the engine's. A joint
    # already declaring YZX is not touched at all.
    #
    # The clip's existing angles are handed in as the reference, so that where
    # two Euler triples describe the same rotation the one nearest what is
    # already stored is chosen. Without it a channel that never moves can come
    # back half a turn away -- the same pose, a different number -- and be
    # refused as "stored as a single constant but the BVH gives it a curve".
    def _reference(k, j):
        p = a.at_key(k)
        base = 3 + 3 * j
        return (p[base], p[base + 1], p[base + 2])

    converted_joints, orders_seen, snapped_joints = recompose_rows(
        m, cols, want_joints, reference=_reference)

    # The key count is the one dimension a same-shape re-encode cannot touch:
    # it is fixed by `frames` and the half-rate flag, both of which live in the
    # header, and changing it resizes the matrix. Refuse it outright rather than
    # hide a resize behind allow_reshape -- lengthening a clip also changes the
    # phase arithmetic (anim.key_at_phase) and the loop wrap, and none of that
    # has been worked out.
    if m.frames != a.keys:
        raise ImportRefused(
            "the BVH has %d frames but this clip stores %d keys%s. Changing "
            "the key count would resize the file and change the clip's timing; "
            "re-export at the original length."
            % (m.frames, a.keys,
               " (%d game frames at half rate)" % a.frames if a.half_rate
               else ""))

    names_differ = []
    if bones is not None:
        # Shorter is the pgj case and is legal (see _column_map). What must
        # still hold is that the rig and the BVH agree with EACH OTHER -- a
        # 52-bone rig paired with a 53-joint BVH means one of them is not
        # what the caller thinks it is.
        if len(bones) > want_joints or len(bones) != len(m.joints):
            raise ImportRefused(
                "the skeleton passed for cross-checking has %d bones, the BVH "
                "has %d joints and the clip drives %d; a shorter skeleton is "
                "fine but all three have to line up"
                % (len(bones), len(m.joints), want_joints))
        names_differ = [(b.name, j) for b, j in zip(bones, m.joints)
                        if b.name != j]

    # Everything above this line is true of a BVH whatever codec it is going
    # back into; everything below is the codec's own business.
    shared = dict(
        ok=True, codec=codec,
        channels=a.channels, keys=a.keys, joints=want_joints,
        original_size=len(original),
        total_samples=a.channels * a.keys,
        rotation_order=" ".join(c for c in m.channels[0] if c in _ROT_AXIS),
        # A conversion silently applied is the thing this module is most
        # careful not to do, so it is reported whether or not it happened.
        orders_declared=orders_seen,
        joints_converted=converted_joints,
        conversion_note=(
            "" if not converted_joints else
            "%d of %d joints declared %s rather than the engine's YZX, so "
            "their angles were re-decomposed into YZX. Same rotation, "
            "different three numbers."
            % (converted_joints, want_joints,
               "/".join(o for o in orders_seen if o != "YZX")
               or "another order")),
        joints_snapped=snapped_joints,
        snap_note=(
            "" if not snapped_joints else
            "%d joint(s) came back on the principal branch of their "
            "decomposition rather than the one the clip stores -- the same "
            "pose written as angles a whole or a half turn away -- and were "
            "slid back beside the clip's own. No pose was changed."
            % snapped_joints),
        joint_names_differ=names_differ[:16],
        rounding=rounding, tolerance=tolerance, noise_floor=noise_floor,
        ignored_position_joints=sorted(set(ignored_positions))[:16],
        ignored_position_count=len(set(ignored_positions)),
    )

    if codec != "cm2":
        encode = _encode_raw if codec == "raw" else _encode_cmp
        out, rep = encode(a, original, m, cols, tolerance, rounding,
                          noise_floor)
        rep.update(shared)
        rep.update(size=len(out), same_size=(len(out) == len(original)),
                   identical=(out == original))
        return out, rep

    deg = math.pi / 180.0
    scales = [a.scale_a if c < 3 else a.scale_b for c in range(a.channels)]
    frozen_groups = []
    if a.scale_a == 0.0:
        frozen_groups.append("A (channels 0-2)")
    if a.scale_b == 0.0:
        frozen_groups.append("B (channels 3+)")

    # ---- quantise every channel, and note what actually moved
    orig_raw = [a.raw_curve(c) for c in range(a.channels)]
    new_raw = []          # [channel][key]
    clipped = 0
    clipped_channels = []
    saturated = set()
    frozen_edits = 0
    for c in range(a.channels):
        col = cols[c]
        if col is None:
            # A channel the BVH never carried, because the skeleton it was
            # written against is shorter than the clip -- the pgj case, where
            # a 52-bone rig drives a 53-bone clip exactly as the engine does.
            # Silence is not an instruction to zero it: keep what the clip
            # already said, so importing an unedited export is still a
            # no-change import.
            new_raw.append(list(orig_raw[c]))
            continue
        sc = scales[c]
        inv = a.dequant(c)
        orig = orig_raw[c]
        # to_bvh prints metres for 0-2 and DEGREES for 3+, so the dead band and
        # the comparison both live in the printed unit.
        to_bvh_unit = 1.0 if c < 3 else 180.0 / math.pi
        curve = []
        hit = False
        for k in range(a.keys):
            v_txt = m.rows[k][col]
            v_old = _f32(orig[k] * inv) * to_bvh_unit
            if abs(v_txt - v_old) <= tolerance:
                # Indistinguishable at the precision the text carries. Keep the
                # byte that was there -- see the dead-band note above.
                curve.append(orig[k])
                continue
            if sc == 0.0:
                # No value can be represented in this group; say so instead of
                # writing a zero over the original.
                frozen_edits += 1
                curve.append(orig[k])
                continue
            q, sat = _quantise(v_txt * deg if c >= 3 else v_txt, sc, rounding)
            if sat:
                clipped += 1
                hit = True
                saturated.add((c, k))
            curve.append(q)
        if hit:
            clipped_channels.append(c)
        new_raw.append(curve)

    # ---- decide the shape
    orig_animated = [a.animated(c) for c in range(a.channels)]
    varies = [max(cv) - min(cv) if cv else 0 for cv in new_raw]
    flattened = []
    if allow_reshape:
        animated = [v > reshape_threshold for v in varies]
    else:
        animated = list(orig_animated)
        flattened, broke = _flatten_constants(
            new_raw, orig_raw, orig_animated, varies,
            lambda c: 0.0 if not scales[c] else _bvh_unit(c) / scales[c],
            k=noise_floor)
        if broke:
            c0, span0 = broke[0]
            raise ImportRefused(
                "%d channel(s) are stored as a single constant in this clip "
                "but the BVH gives them a real curve (first: channel %d, "
                "which now moves by %.4g %s -- past the %g the importer reads "
                "as a tool's rounding noise). Animating a new channel changes "
                "the bitmask, the constants array AND the key matrix, so the "
                "file would change size and could overflow its archive slot. "
                "Pass allow_reshape to do it anyway and check the slot."
                % (len(broke), c0, span0,
                   "metres" if c0 < 3 else "degrees",
                   _const_noise(c0, noise_floor)))
        # Flattening rewrote those curves, so anything measured off new_raw
        # has to be measured again.
        if flattened:
            varies = [max(cv) - min(cv) if cv else 0 for cv in new_raw]

    n_anim = sum(1 for x in animated if x)
    reshaped = animated != orig_animated

    # ---- build the payload
    nmask = (a.channels + 7) // 8
    const_off = 0x15 + nmask
    matrix_off = const_off + 2 * (a.channels - a.anim_count)

    consts = [new_raw[c][0] for c in range(a.channels) if not animated[c]]
    matrix = []
    for k in range(a.keys):
        for c in range(a.channels):
            if animated[c]:
                matrix.append(new_raw[c][k])

    if not reshaped:
        # Splice in place. Everything outside these two spans -- header, mask,
        # both scales, and any slack past `consumed` -- survives untouched, so
        # the length is identical by construction rather than by arithmetic.
        out = bytearray(original)
        out[const_off:const_off + 2 * len(consts)] = struct.pack(
            "<%dh" % len(consts), *consts) if consts else b""
        out[matrix_off:matrix_off + 2 * len(matrix)] = struct.pack(
            "<%dh" % len(matrix), *matrix) if matrix else b""
        out = bytes(out)
    else:
        mask = bytearray(nmask)
        for c in range(a.channels):
            if animated[c]:
                mask[c >> 3] |= 1 << (c & 7)     # LSB-first, 0x002D3200
        tail = original[a.consumed:]             # anything past the scales
        out = (original[0x00:0x11]                       # header, unchanged
               + struct.pack("<I", n_anim)               # +0x11 animCount
               + bytes(mask)
               + (struct.pack("<%dh" % len(consts), *consts) if consts else b"")
               + (struct.pack("<%dh" % len(matrix), *matrix) if matrix else b"")
               + original[matrix_off + 2 * a.anim_count * a.keys:a.consumed]
               + tail)

    # ---- self-check: decode what we just built and compare to what was asked
    # for. A re-encoder that reports success without reading its own output back
    # is exactly how a subtly wrong quantiser survives.
    #
    # Saturated samples are EXCLUDED. They differ from the request by however
    # far past the rail the request was, which is honest but says nothing about
    # accuracy -- `clipped_samples` is the number that describes them. Leaving
    # them in would make one out-of-range value swamp the figure and hide a real
    # quantiser error behind it.
    #
    # Flattened channels are excluded for the same reason and reported the same
    # way: they differ from the BVH by the span that was flattened, which
    # `flattened_max_units` states outright, and averaging that into a figure
    # meant to catch a wrong quantiser would hide the thing it is there for.
    flat_channels = set(c for c, _ in flattened)
    worst = 0.0
    worst_channel = None
    check = _anim.parse(out)
    for c in range(a.channels):
        if scales[c] == 0.0:
            continue                       # decodes to 0.0 by design, not error
        unit = 1.0 if c < 3 else 180.0 / math.pi
        if cols[c] is None or c in flat_channels:
            continue          # no BVH column to check this channel against
        inv = check.dequant(c)
        got = check.raw_curve(c)
        for k in range(a.keys):
            if (c, k) in saturated:
                continue
            d = abs(_f32(got[k] * inv) * unit - m.rows[k][cols[c]])
            if d > worst:
                worst, worst_channel = d, c

    changed = sum(1 for c in range(a.channels) for k in range(a.keys)
                  if new_raw[c][k] != orig_raw[c][k])
    changed_channels = [c for c in range(a.channels)
                        if new_raw[c] != orig_raw[c]]

    report = dict(shared)
    report.update(
        animated=n_anim, animated_before=a.anim_count,
        reshaped=reshaped,
        size=len(out),
        same_size=(len(out) == len(original)),
        identical=(out == original),
        changed_samples=changed,
        changed_channels=changed_channels[:64],
        changed_channel_count=len(changed_channels),
        clipped_samples=clipped,
        clipped_channels=clipped_channels[:64],
        frozen_groups=frozen_groups,
        frozen_edits_dropped=frozen_edits,
        flattened_channels=[c for c, _ in flattened][:64],
        flattened_channel_count=len(flattened),
        **_flat_maxima(flattened, noise_floor),
        rescaled_channels=[], rescaled_channel_count=0,
        worst_error=worst,
        worst_error_channel=worst_channel,
        worst_error_unit=(None if worst_channel is None else
                          "metres" if worst_channel < 3 else "degrees"),
    )
    return out, report


def _encode_raw(a, original, m, cols, tolerance, rounding, noise_floor):
    """A BVH back into an UNCOMPRESSED clip. -> (bytes, partial report).

    The easiest of the three and the safest: the file is one float32 per
    channel per key with a 17-byte header, so there is no quantiser to get
    wrong, no scale to divide by, no bitmask, and no channel stored as a
    constant. Neither the key count nor the channel count can change without
    changing the clip's shape, so the output is the same length by
    construction -- RawAnim.pack asserts that rather than trusting it.

    242 clips on the disc are this codec, and 108 of them are the accessory
    animations (hair, scarf, coat tail, jaw). Those are exactly the clips a
    tester is most likely to want to edit and exactly the ones the importer
    used to answer with "is not a cm2 clip".

    The dead band still applies, and here it is doing something slightly
    different from the cm2 case: float32 holds far more precision than
    to_bvh's "%.6f" prints, so re-reading a printed value and storing it back
    would move the low bits of every sample in the file. Keeping the original
    float when the text agrees with it to the precision the text carries is
    what makes an unedited round trip byte-for-byte identical here.
    """
    values = [list(a.at_key(k)) for k in range(a.keys)]
    orig = [list(r) for r in values]
    for c in range(a.channels):
        col = cols[c]
        if col is None:
            continue                       # shorter rig; leave it alone
        unit = _bvh_unit(c)
        for k in range(a.keys):
            v_txt = m.rows[k][col]
            if abs(v_txt - _f32(values[k][c]) * unit) <= tolerance:
                continue
            v = _f32(v_txt / unit)
            if v != v or v in (float("inf"), float("-inf")):
                raise ImportRefused("a value in the BVH is not finite")
            values[k][c] = v
    out = a.pack(values)

    # Self-check: read our own output back rather than trusting the write.
    check = _raw.parse(out)
    worst = 0.0
    worst_channel = None
    for c in range(a.channels):
        if cols[c] is None:
            continue
        unit = _bvh_unit(c)
        got = check.curve(c)
        for k in range(a.keys):
            d = abs(_f32(got[k]) * unit - m.rows[k][cols[c]])
            if d > worst:
                worst, worst_channel = d, c

    changed = sum(1 for k in range(a.keys) for c in range(a.channels)
                  if values[k][c] != orig[k][c])
    changed_channels = [c for c in range(a.channels)
                        if any(values[k][c] != orig[k][c]
                               for k in range(a.keys))]
    return out, dict(
        animated=a.channels, animated_before=a.channels, reshaped=False,
        changed_samples=changed,
        changed_channels=changed_channels[:64],
        changed_channel_count=len(changed_channels),
        # None of these can happen in a codec with no quantiser and no
        # constants array. Reported as zero rather than left out, so a caller
        # reading the report does not have to know which codec it got.
        clipped_samples=0, clipped_channels=[],
        frozen_groups=[], frozen_edits_dropped=0,
        flattened_channels=[], flattened_channel_count=0,
        **_flat_maxima([], noise_floor),
        rescaled_channels=[], rescaled_channel_count=0,
        worst_error=worst, worst_error_channel=worst_channel,
        worst_error_unit=(None if worst_channel is None else
                          "metres" if worst_channel < 3 else "degrees"),
    )


# The three bit fields of a cmp column, 0x002D3F08. Slot 1 is one bit narrower,
# which is why the loader scales it by 1/1024 where the others get 1/2048.
_CMP_FIELD = ((0, 0x7FF), (11, 0x3FF), (21, 0x7FF))


def _encode_cmp(a, original, m, cols, tolerance, rounding, noise_floor):
    """A BVH back into a `cmp` clip. -> (bytes, partial report).

    Same shape, same length, same everything but the packed words: the 17-byte
    header, the per-track (scale, bias, index) records and the column count are
    copied straight through, and only the `nchan * keys` 32-bit words are
    rewritten. That is what keeps the file inside its archive slot, and it is
    checked rather than argued -- every one of the 666 cmp clips on the disc
    satisfies the size identity `17 + tracks*12 + 2 + nchan*keys*4`, and
    re-packing their own decoded words reproduces them byte for byte.

    Writing a channel touches only its own bit field. A column carries up to
    three channels, but the slot a channel uses is fixed by `c % 3`, so two
    channels can share a column only by using different slots -- measured true
    on all 666 files, maximum three channels per column, one per slot.

    A track whose index is 0xFFFF stores no column at all: its value is the
    bias alone. Those are this codec's constant channels and they get the same
    treatment as cm2's, for the same reason -- see _flatten_constants. The one
    difference is that a cmp constant is a float32 bias rather than a
    quantised s16, so retargeting one to a DIFFERENT constant is exact and
    needs no reshape; only giving it a genuine curve does.
    """
    base = _cmp.HEADER_SIZE + a.channels * 12 + 2
    words = list(a.words)
    orig_bias = list(a.bias)
    bias = list(a.bias)
    scale_file = [None] * a.channels     # a retuned track's on-disc scale
    rescaled = []

    # Every key decoded once. at_key() walks all the channels, so asking it
    # per channel per key would make this quadratic in a 159-channel clip.
    before = [a.at_key(k) for k in range(a.keys)]

    clipped = 0
    clipped_channels = []
    flattened = []
    frozen = 0
    frozen_channels = []
    broke = []
    for c in range(a.channels):
        col = cols[c]
        if col is None:
            continue                       # shorter rig; leave it alone
        unit = _bvh_unit(c)
        curve = [m.rows[k][col] / unit for k in range(a.keys)]   # native units
        for v in curve:
            if v != v or v in (float("inf"), float("-inf")):
                raise ImportRefused("a value in the BVH is not finite")
        old = [before[k][c] for k in range(a.keys)]
        if all(abs(n * unit - _f32(o) * unit) <= tolerance
               for n, o in zip(curve, old)):
            continue                       # no edit this file can express

        if a.index[c] == _cmp.CONSTANT:
            # A constant track here is a float32 bias and nothing else, so
            # retargeting it to a DIFFERENT constant is exact and costs no
            # reshape -- only a genuine curve is impossible. The line between
            # a curve and a tool's rounding noise is the same physical one the
            # cm2 path uses, and it is tested in the printed unit directly
            # because this track has no meaningful step to convert through.
            lim = _const_noise(c, noise_floor) / unit
            keep, spread = _constant_verdict(_f32(orig_bias[c]), curve, lim)
            if keep is None:
                broke.append((c, spread * unit))
            else:
                if keep != orig_bias[c]:
                    bias[c] = _f32(keep)
                flattened.append((c, spread * unit))
            continue

        shift, mask = _CMP_FIELD[c % 3]
        factor = _cmp.SCALE_NARROW if c % 3 == 1 else _cmp.SCALE_WIDE
        sc, bi = a.scale[c], a.bias[c]
        lo, hi = min(curve), max(curve)
        if not sc or lo < bi or hi > bi + mask * sc:
            # RETUNE THE TRACK'S OWN WINDOW, which costs nothing and without
            # which this codec can barely be edited at all.
            #
            # A cmp track carries a scale and a bias fitted tight around the
            # curve it shipped with -- measured on the disc, the stock span is
            # the FULL field, 2047 of 2047 units -- so a 12.7-degree track has
            # a 12.7-degree window and a 3-degree edit falls straight out of
            # it and clamps against the rail. The numbers would come back
            # wrong while the file looked fine, which is the failure this
            # module exists to avoid.
            #
            # Both numbers live in the track's own 12-byte record, and that
            # record is a fixed size, so widening the window does not change
            # the file's length. And the record belongs to ONE track -- the
            # column it points at may be shared with two other channels, but
            # the scale and bias are not -- so nothing else is disturbed.
            span = hi - lo
            # A hair of margin, so float32 rounding the scale DOWN cannot
            # leave the top of the range one unit outside the field.
            eff = span / mask * (1.0 + 1e-6) if span > 0 else abs(sc)
            s_file = _f32(eff / factor)
            if not (s_file > 0.0) or s_file in (float("inf"),):
                raise ImportRefused(
                    "channel %d asks for a range of %.6g that a 32-bit "
                    "column cannot resolve" % (c, span))
            sc, bi = _f32(s_file * factor), _f32(lo)
            scale_file[c], bias[c] = s_file, bi
            rescaled.append(c)
        if not sc:
            # A track with a zero scale decodes to its bias for every key
            # whatever is in its column, so no value written here can come
            # back. Say so and keep the bytes, rather than writing a number
            # that will read back as something else -- the same treatment cm2
            # gives a zero-scale group.
            frozen += a.keys
            frozen_channels.append(c)
            continue
        hit = False
        for k in range(a.keys):
            f = (curve[k] - bi) / sc
            if rounding == "truncate":
                q = int(f)
            else:
                q = int(f + 0.5) if f >= 0.0 else -int(-f + 0.5)
            if q < 0 or q > mask:
                q = 0 if q < 0 else mask
                clipped += 1
                hit = True
            w = words[k * a.nchan + a.index[c]]
            words[k * a.nchan + a.index[c]] = (
                (w & ~(mask << shift)) | (q << shift)) & 0xFFFFFFFF
        if hit:
            clipped_channels.append(c)

    if broke:
        c0, span0 = broke[0]
        raise ImportRefused(
            "%d channel(s) are stored as a single constant in this clip but "
            "the BVH gives them a real curve (first: channel %d, which now "
            "moves by %.4g %s -- past the %g the importer reads as a tool's "
            "rounding noise). This codec packs three channels into every "
            "32-bit column and a constant track has no column at all, so "
            "animating one would have to add a column and resize the file."
            % (len(broke), c0, span0, "metres" if c0 < 3 else "degrees",
               _const_noise(c0, noise_floor)))

    out = bytearray(original)
    for c in range(a.channels):
        rec = _cmp.HEADER_SIZE + c * 12
        if scale_file[c] is not None:
            struct.pack_into("<f", out, rec, scale_file[c])
        if bias[c] != orig_bias[c]:
            struct.pack_into("<f", out, rec + 4, bias[c])
    for i, w in enumerate(words):
        struct.pack_into("<I", out, base + 4 * i, w)
    out = bytes(out)
    if len(out) != len(original):
        raise ImportRefused("the re-encode changed the file's length, %d -> %d"
                            % (len(original), len(out)))

    # Self-check, same contract as the other two: decode what was just built
    # and compare it to what the BVH asked for.
    flat = set(c for c, _ in flattened)
    check = _cmp.parse(out)
    after = [check.at_key(k) for k in range(a.keys)]
    worst = 0.0
    worst_channel = None
    for c in range(a.channels):
        if cols[c] is None or c in flat:
            continue
        unit = _bvh_unit(c)
        for k in range(a.keys):
            d = abs(_f32(after[k][c]) * unit - m.rows[k][cols[c]])
            if d > worst:
                worst, worst_channel = d, c

    changed = sum(1 for k in range(a.keys) for c in range(a.channels)
                  if after[k][c] != before[k][c])
    changed_channels = [c for c in range(a.channels)
                        if any(after[k][c] != before[k][c]
                               for k in range(a.keys))]
    n_const = sum(1 for c in range(a.channels)
                  if a.index[c] == _cmp.CONSTANT)
    return out, dict(
        animated=a.channels - n_const, animated_before=a.channels - n_const,
        reshaped=False,
        changed_samples=changed,
        changed_channels=changed_channels[:64],
        changed_channel_count=len(changed_channels),
        clipped_samples=clipped, clipped_channels=clipped_channels[:64],
        frozen_groups=(["%d track(s) with a zero scale" % len(frozen_channels)]
                       if frozen_channels else []),
        frozen_edits_dropped=frozen,
        flattened_channels=[c for c, _ in flattened][:64],
        flattened_channel_count=len(flattened),
        **_flat_maxima(flattened, noise_floor),
        rescaled_channels=rescaled[:64],
        rescaled_channel_count=len(rescaled),
        worst_error=worst, worst_error_channel=worst_channel,
        worst_error_unit=(None if worst_channel is None else
                          "metres" if worst_channel < 3 else "degrees"),
    )


def candidate_skeletons(a, skels):
    """Which skeletons could this clip belong to?

    `skels` maps name -> bone list. The clip's channel count fixes the bone
    count exactly, but several skeletons usually share one -- 11 of them have
    53 bones -- and nothing in a `.anim` names its skeleton. So this returns
    every candidate rather than picking one, and the caller must say so.
    """
    want = (a.channels - 3) // 3
    return sorted(n for n, bones in skels.items() if len(bones) == want)
