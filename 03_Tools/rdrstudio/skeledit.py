"""`.skel` editing: bone offsets, one component at a time.

The last of M7's nine formats without an editing surface. `skeleton.py` has
parsed these fully since the first commit and even carries `set_offset`, but
nothing was ever wired to it -- the Files tab shows a skeleton read-only, and
`set_offset` writes all three components at once, which is the wrong shape for
a panel where someone nudges one number.

WHAT THIS ADDS, and only this:

    doc(data)                     the bones, with an editable cell per
                                  component, shaped like `charfile`'s docs so
                                  a panel can render it the same way
    set_fields(data, changes)     `(data, changes) -> bytes`, which is exactly
                                  the `setter=` contract `Studio.edit_creature`
                                  takes -- so a route gets every-copy writes,
                                  slot checks and one undo group for free
                                  rather than growing a second write path

PATHS are `<bone>/offset/<x|y|z>`. Per COMPONENT, not per bone, for the same
reason `charfile` addresses cells rather than rows: an edit should rewrite one
number's byte span and leave the file otherwise identical. Writing all three
when one changed would touch three spans and make "every unedited byte is
untouched" unprovable -- which is the property `scripts/verify_charstats.py`
asserts and the only real guarantee these tight slots have.

WIDTH. The replacement keeps the original number's width where it can, by
dropping decimal places -- `skeleton.set_offset`'s trick, kept because the
alternative is a file that grows. A value needing more integer digits than the
original had cannot be made to fit and is refused by name rather than silently
truncated to something the user did not ask for.

The usual warning about this family applies: same width is not the same as
free, because the entry is stored deflated. `.skel` is roomier than its
neighbours -- median headroom 1,323 bytes against `.hot`'s 15 -- but the
caller still dry-runs. See `05_Docs/CAPABILITIES.md` C-030.
"""

from . import skeleton

AXES = ("x", "y", "z")


class Unknown(KeyError):
    """A path this module does not address. Mirrors `charfile.Unknown`."""


def _bones(data):
    _version, _declared, bones, _root = skeleton.parse(data)
    return bones


def _index(data):
    """`<bone>/offset/<axis>` -> (bone, component index, (start, end))."""
    out = {}
    for bone in _bones(data):
        for i, span in enumerate(bone.spans[:3]):
            out["%s/offset/%s" % (bone.name, AXES[i])] = (bone, i, span)
    return out


def doc(data):
    """Every bone, with its three offset components as editable cells.

    `world` is the accumulated position and is NOT editable -- it is derived
    from this bone's offset and every parent's, so offering it as a field
    would invite an edit that silently moves the parent instead.
    """
    bones = _bones(data)
    world = skeleton.world_positions(bones)
    rows = []
    for bone in bones:
        if not bone.spans:
            continue
        cells = [dict(path="%s/offset/%s" % (bone.name, AXES[i]),
                      axis=AXES[i],
                      value=("%g" % bone.offset[i]),
                      width=span[1] - span[0])
                 for i, span in enumerate(bone.spans[:3])]
        rows.append(dict(label=bone.name, depth=bone.depth,
                         parent=bone.parent, cells=cells,
                         world=[round(float(n), 4)
                                for n in world.get(bone.name, (0, 0, 0))],
                         channels=list(bone.channels)))
    return dict(layout="tree", kind="skel", title="Skeleton",
                blurb="Bone offsets, relative to the parent. Moving a bone "
                      "moves everything parented to it.",
                columns=list(AXES), rows=rows, bones=len(rows))


def _fit(value, width):
    """`value` as a fixed-point string of exactly `width` characters, or None.

    Decimal places are chosen to FILL the span rather than to be short:
    `1.25` into the eight characters `0.000000` occupied becomes `1.250000`,
    not `    1.25`. These files are whitespace-separated so either would parse,
    but padding with spaces changes how every column below it lines up, and a
    diff of a one-number edit should show one number.

    Returns None when even zero decimals is too wide -- an offset that needs
    more integer digits than the original had cannot be patched in place.
    """
    for places in range(width, -1, -1):
        text = "%.*f" % (places, value)
        if len(text) == width:
            return text
        if len(text) < width:
            # Fewer digits than the span: pad the FRACTION out, not the front.
            return text + "0" * (width - len(text)) if "." in text else None
    return None


def set_fields(data, changes):
    """Rewrite bone offset components in place.

    `(data, changes) -> bytes`, the `setter=` contract, so this drops straight
    into `Studio.edit_creature` and inherits its every-copy write, its
    per-copy slot check and its single undo group.

    The returned file is the same length as the one that went in whenever
    every replacement fits its original width, and each changed component
    touches exactly one byte span.
    """
    index = _index(data)
    unknown = sorted(k for k in changes if k not in index)
    if unknown:
        raise Unknown("no such bone offset: %s" % ", ".join(unknown))

    edits = []
    for path, value in changes.items():
        _bone, _i, (start, end) = index[path]
        text = str(value).strip()
        try:
            number = float(text)
        except ValueError:
            raise Unknown("%s is not a number: %r" % (path, value))
        fitted = _fit(number, end - start)
        if fitted is None:
            raise Unknown(
                "%s does not fit the %d characters the file has for it -- a "
                "bone offset is patched in place, so it cannot get wider. "
                "%r needs at least %d."
                % (path, end - start, text, len("%.0f" % number)))
        # The span keeps its exact length, so nothing after it moves and no
        # other number's byte offsets change under us.
        edits.append((start, end, fitted.encode("latin-1")))

    out = bytearray(data)
    for start, end, text in sorted(edits, key=lambda e: -e[0]):
        out[start:end] = text
    return bytes(out)
