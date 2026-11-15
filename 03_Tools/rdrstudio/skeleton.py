"""`.skel` skeletons -- plain text, and fully editable.

All 236 on the disc are text, format `Version: 101`:

    Version: 101
    NumBones 53
    bone root {
            offset 0.000000 1.149070 0.000000
            transX
            transY
            transZ
            rotX  rotY  rotZ
            scaleX scaleY scaleZ
            bone spine_01 {
                    offset 0.000000 0.161926 0.050363
                    ...
            }
    }

Each `bone` names a joint, `offset` is its position relative to its parent,
and the bare `transX`/`rotY`/`scaleZ` lines declare which channels the
animation system may drive on that bone.

Offsets carry byte spans so an edit rewrites exactly one number -- the same
surgical approach the level formats use, for the same reason: these files sit
in fixed slots and must not grow.
"""

import re
from typing import Any

_NUM = r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?"
_BONE_RE = re.compile(rb"(?m)^[ \t]*bone[ \t]+(\S+)[ \t]*\{")
_OFFSET_RE = re.compile(
    (r"offset[ \t]+(%s)[ \t]+(%s)[ \t]+(%s)" % (_NUM, _NUM, _NUM)).encode())

CHANNELS = ("transX", "transY", "transZ", "rotX", "rotY", "rotZ",
            "scaleX", "scaleY", "scaleZ")


class Bone:
    __slots__ = ("name", "depth", "parent", "offset", "spans", "channels",
                 "start", "end", "children")

    def __init__(self, name, depth, parent):
        self.name = name
        self.depth = depth
        self.parent = parent
        self.offset = [0.0, 0.0, 0.0]
        self.spans = []          # byte span of each offset component
        self.channels = []
        self.children = []
        self.start = self.end = 0

    def to_json(self):
        return dict(name=self.name, depth=self.depth, parent=self.parent,
                    offset=self.offset, channels=self.channels,
                    children=[c.name for c in self.children])


def _close_brace(data, open_idx):
    depth = 0
    i = open_idx
    while i < len(data):
        c = data[i]
        if c == 0x7B:
            depth += 1
        elif c == 0x7D:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return len(data) - 1


def is_skeleton(data):
    return data[:8] == b"Version:"


def parse(data):
    """-> (version, declared_bone_count, [Bone] in file order, root)."""
    if not is_skeleton(data):
        raise ValueError("not a .skel (missing 'Version:' header)")
    m = re.match(rb"Version:[ \t]*(\d+)", data)
    version = int(m.group(1)) if m else 0
    m = re.search(rb"NumBones[ \t]+(\d+)", data)
    declared = int(m.group(1)) if m else 0

    bones = []
    stack: list[tuple[Any, int]] = []   # (bone, close_index)
    for bm in _BONE_RE.finditer(data):
        open_idx = bm.end() - 1
        close = _close_brace(data, open_idx)
        # Anything on the stack that closed before this bone starts is done.
        while stack and stack[-1][1] < bm.start():
            stack.pop()
        parent = stack[-1][0].name if stack else None
        b = Bone(bm.group(1).decode("latin-1"), len(stack), parent)
        b.start, b.end = bm.start(), close + 1

        # An offset/channel belongs to THIS bone only if it appears before the
        # first nested `bone` -- otherwise a parent would claim its child's.
        inner = _BONE_RE.search(data, open_idx, close)
        body_end = inner.start() if inner else close
        om = _OFFSET_RE.search(data, open_idx, body_end)
        if om:
            b.offset = [float(om.group(i)) for i in (1, 2, 3)]
            b.spans = [(om.start(i), om.end(i)) for i in (1, 2, 3)]
        body = data[open_idx:body_end]
        b.channels = [c for c in CHANNELS
                      if re.search(rb"(?m)^[ \t]*" + c.encode() + rb"[ \t]*$", body)]

        if stack:
            stack[-1][0].children.append(b)
        bones.append(b)
        stack.append((b, close))

    root = bones[0] if bones else None
    return version, declared, bones, root


def world_positions(bones):
    """Bone name -> world-space position, by accumulating parent offsets."""
    by_name = {b.name: b for b in bones}
    out: dict[Any, Any] = {}

    def resolve(b):
        if b.name in out:
            return out[b.name]
        if b.parent is None or b.parent not in by_name:
            pos = list(b.offset)
        else:
            p = resolve(by_name[b.parent])
            pos = [p[i] + b.offset[i] for i in range(3)]
        out[b.name] = pos
        return pos

    for b in bones:
        resolve(b)
    return out


def set_offset(data, bone, xyz, keep_width=True):
    """Move one bone. Byte-surgical; matches the original number width."""
    if not bone.spans:
        raise ValueError("%s has no offset to edit" % bone.name)
    edits = []
    for (start, end), value in zip(bone.spans, xyz):
        text = "%.6f" % float(value)
        if keep_width and len(text) > end - start:
            for dec in (5, 4, 3, 2, 1):
                text = "%.*f" % (dec, float(value))
                if len(text) <= end - start:
                    break
        edits.append((start, end, text.encode("latin-1")))
    out = bytearray(data)
    for s, e, t in sorted(edits, key=lambda x: -x[0]):
        out[s:e] = t
    return bytes(out)


ANIM_MAGICS = (b"cm2\x00", b"cmp\x00")


def anim_header(data):
    """Header of a paired `.anim`. Curves are compressed and NOT decoded.

    Channel count is bones x 3 -- divisible by 3 in 100% of the 4,005 files
    with a valid magic, and equal to the matching skeleton's bone count
    wherever the pairing is unambiguous.
    """
    import struct as _s
    if len(data) < 20 or data[:4] not in ANIM_MAGICS:
        return None
    magic = data[:4].rstrip(b"\x00").decode("latin-1")
    frames, channels, zero, flags = _s.unpack_from("<4I", data, 4)
    return dict(magic=magic, frames=frames, channels=channels,
                bones=channels // 3 if channels % 3 == 0 else None,
                flags=flags, size=len(data))


# --------------------------------------------------- which rigs are the same
#
# A clip's channel count states how many bones it drives, and until now that
# number had to EQUAL a skeleton's bone count for the tool to offer it. That is
# far stricter than the data:
#
#   * The canonical 52-bone name sequence is an exact PREFIX of the canonical
#     53-bone one. The 53rd bone is `coattail`, parent `pelvis`. Measured:
#     107 of 108 52-bone skeletons share the 52 sequence, 10 of 11 53-bone
#     skeletons share the 53 sequence, and the shorter is the longer truncated.
#   * 2,601 of 4,187 clips on the disc drive 53 bones, and only 12 of 306
#     character bundles in SWAT.DAT are rigged to 53 -- so the strict rule
#     offered a dozen models for the majority of the library while 140 more
#     were compatible in everything but a single trailing bone.
#
# Comparing NAMES rather than counts is what makes widening safe. A bare
# `len >= want` would happily drive a horse with a man's walk, because a
# per-vertex bone index means whatever that model's own skeleton says at that
# position. Equal names in the same order means equal positions.

def prefix_names(bones, n=None):
    """The first `n` bone names, in file order, as a comparable key."""
    return tuple(b.name for b in (bones if n is None else bones[:n]))


def hosts(skel_bones, want):
    """True when a `want`-bone clip can drive this skeleton unchanged.

    Length alone is not enough and never was; the caller must also check the
    names line up, which `compatible` does for a pair.
    """
    return len(skel_bones) >= want


def compatible(a_bones, b_bones):
    """Do these two skeletons agree wherever they overlap?

    Order matters and the shared span must match name for name. Two rigs that
    agree on their first N bones drive those N bones identically, whichever is
    longer; the surplus simply stays at bind pose.
    """
    n = min(len(a_bones), len(b_bones))
    if not n:
        return False
    return prefix_names(a_bones, n) == prefix_names(b_bones, n)


def split_accessory(bones, insert, nbones):
    """Split a character skeleton into (base, accessory, note).

    `.acc` declares an accessory as a contiguous run of `nbones` bones that
    already lives INSIDE the character's own `.skel`, beginning at ONE-BASED
    index `insert`. Cut that run out and what is left is the character's base
    rig, name for name.

    That is the opposite of the obvious reading -- the field looks like it
    names a host bone to hang something off, and charfile.py said so -- and it
    is settled by an oracle rather than by argument. Removing the slice from
    each of the eight characters that have both a `.acc` and a same-stem
    `.skel` leaves EXACTLY the canonical base sequence, name for name, in
    seven of eight; under a zero-based reading, or the slice shifted by one,
    all eight disagree. Corroborated independently: in four of the fourteen
    `.acc` files the `Name` field is itself a bone name, and it sits at
    precisely one-based `insert` (ct2 skel_hair 6, gdn scarf_root 6, doa and
    wlf jaw 10).

    srd is the eighth and it is the DATA that is off, not the reading:
    srd.acc says insert 54 with 7 bones, but srd.skel's tail run starts at 53
    and the file only has 59 bones, so 54+7-1 overruns by one. Cutting 7 from
    53 gives exactly the canonical 52. The overrun is clamped and reported
    rather than silently absorbed -- it ships in 57 archives, so anything
    reading it will meet the same inconsistency.
    """
    note = ""
    i0 = insert - 1                       # one-based, measured
    if i0 < 0:
        i0, note = 0, "SkelInsert %d is below 1; clamped" % insert
    if i0 + nbones > len(bones):
        fixed = max(0, len(bones) - nbones)
        note = ("this .acc runs off the end of the skeleton -- it asks for "
                "%d bones from index %d but the file has %d, so the slice was "
                "taken from %d instead (stock-data inconsistency, srd/msrd)"
                % (nbones, insert, len(bones), fixed + 1))
        i0 = fixed
    acc = bones[i0:i0 + nbones]
    base = bones[:i0] + bones[i0 + nbones:]
    return base, acc, note


def listing(data):
    version, declared, bones, _ = parse(data)
    out = ["Version %d, NumBones %d (%d bone blocks found)"
           % (version, declared, len(bones)), ""]
    world = world_positions(bones)
    for b in bones:
        w = world[b.name]
        out.append("%s%-24s offset %8.3f %8.3f %8.3f   world %8.3f %8.3f %8.3f   %s"
                   % ("  " * b.depth, b.name, b.offset[0], b.offset[1],
                      b.offset[2], w[0], w[1], w[2],
                      "".join("T" if c.startswith("trans") else
                              "R" if c.startswith("rot") else "S"
                              for c in b.channels)))
    return "\n".join(out)


def to_text(bones):
    """Serialise a bone list back to the `.skel` text the viewer parses.

    Needed because an ACCESSORY rig is not a file. `.acc` declares it as a
    contiguous slice inside a character's own `.skel`, so the picker offers it
    under a label like `ct2 :: skel_hair` -- and the viewer, which loads a rig
    by fetching a filename, had nothing to fetch. Measured: 28 clips in
    SWAT.DAT are offered ONLY an accessory rig, and every one of them fell
    through to the alphabetically-first skeleton and then failed.

    Only `bone NAME { offset x y z ... }` and the nesting matter to the
    parser; it skips every other token. Depth drives the braces, and a slice
    lifted out of a larger rig can start at any depth, so the first bone is
    re-based to zero and the rest keep their relative nesting.
    """
    if not bones:
        return "Version: 1\nNumBones: 0\n"
    base = bones[0].depth
    out = ["Version: 1", "NumBones: %d" % len(bones)]
    open_at: list[int] = []            # depths currently open, outermost first
    for b in bones:
        d = max(0, b.depth - base)
        while open_at and open_at[-1] >= d:
            open_at.pop()
            out.append("%s}" % ("  " * len(open_at)))
        pad = "  " * d
        out.append("%sbone %s {" % (pad, b.name))
        out.append("%s  offset %.6f %.6f %.6f"
                   % (pad, b.offset[0], b.offset[1], b.offset[2]))
        open_at.append(d)
    while open_at:
        open_at.pop()
        out.append("%s}" % ("  " * len(open_at)))
    return "\n".join(out) + "\n"
