"""The engine's general-purpose text format.

**109 of the 139** extensions inside this disc's archives are pure ASCII --
every file of that type -- covering 56,507 entries, 44.4% of the disc, and
almost all of them share one grammar. (An earlier count here said "101 of
126"; both numbers were wrong. C-033, `research/probe_textform.py`.) `scene.py`
already parses the `node NAME { ... }` dialect used by `.znes`/`.bnds`; this
handles most of the rest -- lighting, fog, sky, water, rain, snow, grass,
particles, LOD distances, shop inventory, horse gaits, duel timings, physics
materials, and about eighty more.

**It does NOT handle all of it, and the gap bites.** This used to claim it
handled the rest outright. Two spellings the disc uses escape `_OPEN`/`_CLOSE`
below, which both require the brace to own its line: `Node 0 {` (a block head
with an argument before the brace) and ` 1 }` (a `}` sharing its line with
data). The first is read as a FIELD named `Node` whose value is `0 \t{`, so
`apply()` on that field overwrites the `{` and unbalances the file --
**270,750 such fields across 11,555 files**, worst in `.nav`, `.bxes`,
`.lnes`, `.skel`, `.znes` and `.gait`. `apply(data, {})` is still byte-perfect
on all 56,399 files, which is exactly why it went unnoticed. See C-033 and
REQ-017; `skeledit.py` is unaffected (it goes through `skeleton.py`).

The grammar, by inspection across every extension:

    type: a                     optional leading declaration, sometimes
                                followed by a blank line
    Key value                   scalar
    Key v0 v1 v2                vector (whitespace-separated)
    Key[0] value                indexed scalar
    Key: value                  scalar, colon form
    Name {                      block, nestable, name may be absent
      ...
    }
    ; comment                   to end of line

Values are whitespace-separated tokens; a key's value is "the rest of the
line". Numbers keep their written form so a rewrite is byte-identical
wherever a field was not touched -- `0.299998` must not come back as
`0.29999799999`.

The parser records the exact source span of every value so `apply()` can
patch bytes in place. That matters more than elegance here: these files live
in fixed-size archive slots, so an edit that reflows whitespace can overflow
a slot that a targeted edit would have fitted.
"""

import re

# A key line: leading space, a name (letters/digits/_ and an optional
# [index]), a separator, then the value to end of line.
#
# The separator is `:`, `=`, or plain whitespace -- all three are in use, and
# sometimes in the same directory. `.fog` writes `ClampAlpha 150`, `.pmtl`
# writes `friction: 0.100000`, `.shadows` writes `Color= 0.0 0.0 0.0`. Only
# handling the first two left `.shadows`, `.snow`, `.fx` and `.reigns`
# parsing to zero fields, which reads as "binary" and is how formats get
# wrongly written off.
#
# A separator is required, so a bare word on its own line (`Shadow`,
# `RainStorm` -- these files' type headers) is not mistaken for a field with
# an empty value.
# A key may also be dotted, slashed or word-bracketed:
# `SixNumbersRH.pistol.HolsterOffset.x`, `MaxLassoed/OrientTime`,
# `AttackPhaseStart[ptl]`, `WAScalePhase.0`. The pattern used to allow only a
# bare identifier with an optional [digits], and every line it did not match
# was skipped SILENTLY.
#
# The silence is why this survived. An unparsed line is copied verbatim, so
# round-tripping stayed byte-exact and nothing looked broken -- while
# measured across the disc, 48,495 of 102,300 content lines in `.swt` (47%)
# and 15,960 of 38,494 in `.hot` (41%) were invisible to every editor built
# on this module. The per-weapon holster offsets and firing timings a modder
# most wants to touch are exactly the dotted keys being dropped.
#
# "Parses fine" and "parses everything" are different claims, and only the
# second one is worth making. Byte-exact round-trip cannot tell them apart.
_KEY = re.compile(
    rb"^([ \t]*)([A-Za-z_][A-Za-z0-9_]*"
    rb"(?:[./][A-Za-z0-9_]+)*"
    rb"(?:\[[A-Za-z0-9_]+\])?"
    rb"(?:[./][A-Za-z0-9_]+)*)"
    rb"[ \t]*(?:[:=][ \t]*|[ \t]+)"
    rb"([^\r\n;]*?)[ \t]*(?=[\r\n;]|$)",
    re.M)
# LEGACY, and not used by anything in this module. `parse` counted on these
# two until 2026-09-14 and now counts brace tokens instead -- see the long note
# in it. They stay because `research/probe_props_gap.py` imports them to model
# the PRE-FIX behaviour and measure the damage it did; delete them and that
# probe stops running. Its OPENMISS/DRIFTED columns describe this old model,
# not what `parse` does today. (REQ-017.)
_OPEN = re.compile(rb"^[ \t]*([A-Za-z_][A-Za-z0-9_]*)?[ \t]*\{[ \t]*$", re.M)
_CLOSE = re.compile(rb"^[ \t]*\}[ \t]*$", re.M)

# Why those two had to be matched against a STRIPPED line and not the raw one,
# kept because it is the same class of bug as the one that retired them: they
# anchor with `$`, which does not consume a `\r`, and the disc is CRLF
# throughout, so
# `b'{\r'` matched neither and every block name was lost. `_KEY` was unaffected
# because its lookahead accepts the carriage return -- which is exactly why the
# parse looked healthy while the structure was gone. Counting has no `$` in it
# and cannot repeat that.
#
# A line holding nothing but one identifier. Either a type header on its own
# (`Shadow`, `HorseTail`) or the name of an Allman-braced block whose `{` is
# on the next line -- which one it is cannot be known until the next line is
# read, so the parser holds it and only uses it if a brace follows.
_BARE_WORD = re.compile(rb"^[A-Za-z_][A-Za-z0-9_]*$")
_NUM = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$")


class Field:
    """One `Key value` line, with the byte span of its value."""

    __slots__ = ("key", "path", "raw", "start", "end", "line")

    def __init__(self, key, path, raw, start, end, line):
        self.key = key
        self.path = path          # e.g. "Fog/ColorStart"
        self.raw = raw            # the value text, verbatim
        self.start = start        # byte offset of the value
        self.end = end
        self.line = line

    @property
    def parts(self):
        return self.raw.split()

    @property
    def kind(self):
        p = self.parts
        if not p:
            return "empty"
        if all(_NUM.match(x) for x in p):
            return "number" if len(p) == 1 else "vector%d" % len(p)
        return "text"

    def numbers(self):
        try:
            return [float(x) for x in self.parts]
        except ValueError:
            return None

    def to_json(self):
        return dict(key=self.key, path=self.path, value=self.raw,
                    kind=self.kind, line=self.line,
                    numbers=self.numbers() if self.kind != "text" else None)


def is_textual(data, threshold=0.95):
    """Does this blob look like one of the engine's text files?"""
    if not data:
        return False
    sample = data[:4096]
    ok = sum(1 for c in sample if 32 <= c < 127 or c in (9, 10, 13))
    return ok / len(sample) >= threshold


def parse(data, with_depth=False):
    """Every `Key value` line in the file, with its block path.

    `with_depth=True` returns `(fields, leftover)` instead, where `leftover`
    is how many blocks this walk never closed -- 0 for a well-formed file.
    It is reported from the SAME walk rather than a second one on purpose:
    the bug this parser was carrying until 2026-09-14 was found by a probe
    that had to reimplement the block bookkeeping to see it, and two
    implementations of one rule is how the rule drifts. `verify_props.py`
    asserts on it.

    Blocks are tracked by a simple brace depth walk rather than a recursive
    grammar. These files are not always well-formed -- a few have a stray
    brace or a block that never closes -- and a strict parser would refuse
    the whole file over one bad line, which is the opposite of useful when
    the goal is to let someone edit the other 200 lines.
    """
    if isinstance(data, str):
        data = data.encode("latin-1")
    fields = []
    stack: list[str] = []
    seen: dict[str, int] = {}
    pos = 0
    line_no = 1
    pending_name = ""       # a bare word that may be naming the next `{`
    n = len(data)
    while pos < n:
        nl = data.find(b"\n", pos)
        if nl < 0:
            nl = n
        line = data[pos:nl]
        stripped = line.strip()

        if stripped.startswith(b";") or not stripped:
            # Transparent: a blank line or a comment between a block's name
            # and its `{` must not break the Allman pairing below, so these
            # do not clear `pending_name`.
            pass
        else:
            # BRACES ARE TOKENS, NOT LINE MARKERS.
            #
            # This used to match two patterns that each demanded the brace own
            # its line -- `^\s*(name)?\s*\{\s*$` and `^\s*\}\s*$` -- and the
            # disc uses two spellings neither covers:
            #
            #     Node 0 {          a block head with an ARGUMENT before it
            #      1 }              a `}` sharing its line with data (.nav)
            #
            # The first fell through to `_KEY` and was recorded as an editable
            # FIELD named `Node` whose value was `0 \t{`, pushing no block; the
            # second popped none. So `apply()` on one of those fields deleted
            # the `{` and unbalanced the file, and every field inside a missed
            # block carried the wrong path. 270,750 such fields across 11,555
            # files, worst in `.nav`, `.bxes`, `.lnes`, `.skel`, `.znes`,
            # `.gait`. Measured, and re-measured after this fix, by
            # `research/probe_props_gap.py`. C-033 / REQ-017.
            #
            # Counting braces instead makes both spellings fall out, and the
            # corpus says counting is safe: across every ASCII entry on the
            # disc no line carries more than one `{`, and no line has anything
            # after the `{` of a block head. What DOES occur is a balanced
            # `{ ... }` inside a VALUE -- `.nav`'s `Links 12 { 52 51 ... }`,
            # `.chase`'s `SideOffsets: { 4.0 5.5 7.0 }`, `.enm`'s empty
            # `{\t}` -- 294 lines in all. Those open and close on one line, so
            # `opens == closes` leaves them to `_KEY`, which is what keeps them
            # editable and keeps their braces inside the value where they
            # belong.
            #
            # `;` starts a comment here, which is what `_KEY` has always
            # assumed (its value group stops at one), so braces are counted on
            # the comment-stripped body. Eight `.dst` lines depend on it.
            body = stripped.split(b";", 1)[0]
            opens = body.count(b"{")
            closes = body.count(b"}")
            # Consumed only by a brace on the NEXT content line, as the
            # comment below has always claimed; before this it was never
            # cleared, so a bare word could name an anonymous block much
            # further down the file.
            carried, pending_name = pending_name, ""
            if closes > opens:
                for _ in range(closes - opens):
                    if stack:
                        stack.pop()
            elif opens > closes:
                head = b" ".join(body.split(b"{", 1)[0].split())
                name = head.decode("latin-1")
                # Allman braces: some formats put the block name on its own
                # line and the `{` on the next. `.aisp` does it for every one
                # of its 1,306 spawn groups across the disc, so the name --
                # which is the entire semantic content of the file -- landed
                # in an anonymous block and every path came back as bare
                # `World`/`Tile` with an occurrence suffix.
                #
                # Round-trip stayed byte-exact throughout, because an
                # unnamed block still copies its lines verbatim. Structure
                # was being silently discarded, not corrupted, which is the
                # harder kind of wrong to notice.
                #
                # `pending_name` is only consumed by an immediately
                # following brace, so a bare word that is really a type
                # header (`Shadow`, `HorseTail`, `StunTable`) still names
                # nothing when no `{` follows it.
                #
                # A multi-token head keeps its whole label -- `Node 0`,
                # `BoundSphere fist_l`, `packet 4 1 1` -- rather than just its
                # first word. That label is the block's identity in the file,
                # and keeping it is what makes a path name one block instead
                # of relying on an occurrence suffix to tell four `Node`s
                # apart. Single-token heads, which are 352,435 of the disc's
                # block opens, are unchanged.
                if not name and carried:
                    name = carried
                stack.append(name)
            elif _BARE_WORD.match(stripped):
                pending_name = stripped.decode("latin-1")
                pos = nl + 1
                line_no += 1
                continue            # not a field; may name the next brace
            else:
                km = _KEY.match(line)
                if km:
                    key = km.group(2).decode("latin-1")
                    val = km.group(3)
                    vstart = pos + km.start(3)
                    path = "/".join([s for s in stack if s] + [key])
                    # Repeated keys are normal here -- a `.em` hit mesh is 154
                    # lines all called `v`, and a `.pmtl` repeats the same
                    # field inside 58 `mtl` blocks. Disambiguate with an
                    # occurrence suffix so a path always names exactly one
                    # line. Without it `apply` would patch the first `v` and
                    # silently ignore which one you meant.
                    seen[path] = seen.get(path, 0) + 1
                    if seen[path] > 1:
                        path = "%s#%d" % (path, seen[path])
                    fields.append(Field(key, path,
                                        val.decode("latin-1"),
                                        vstart, vstart + len(val), line_no))
        pos = nl + 1
        line_no += 1
    if with_depth:
        return fields, len(stack)
    return fields


def apply(data, edits):
    """Rewrite values in place. `edits` is {path: new_value_text}.

    Applied back-to-front so earlier spans stay valid, and only the value
    text is touched -- keys, indentation, comments, line endings and every
    untouched field come through byte-identical. A file whose archive slot
    has no headroom can still take an edit that keeps the same length.
    """
    if isinstance(data, str):
        data = data.encode("latin-1")
    fields = parse(data)
    by_path = {f.path: f for f in fields}   # parse() guarantees uniqueness

    todo = []
    for path, value in edits.items():
        f = by_path.get(path)
        if f is None:
            raise KeyError("no field %r in this file" % path)
        todo.append((f.start, f.end, str(value).encode("latin-1")))

    out = bytearray(data)
    for start, end, val in sorted(todo, reverse=True):
        out[start:end] = val
    return bytes(out)


def summary(data, limit=400):
    """Parsed fields plus the block outline, for a UI to render."""
    fields = parse(data)
    blocks = []
    seen = set()
    for f in fields:
        head = f.path.rsplit("/", 1)[0] if "/" in f.path else ""
        if head not in seen:
            seen.add(head)
            blocks.append(head)
    return dict(fields=[f.to_json() for f in fields[:limit]],
                total=len(fields), blocks=blocks,
                truncated=len(fields) > limit)
