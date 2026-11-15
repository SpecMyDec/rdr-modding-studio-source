"""The menu / HUD markup format: `.swui` and its three siblings.

`props.py` is the first thing to reach for on this disc -- 101 of 126
extensions share that `Key value` grammar. It does not apply here, and the
measurement is not close. Across all 215 distinct UI files on the USA disc,
`props.parse` covers **319 of 4,387 content lines, 7.27%**, and 202 of 215
files parse to zero fields. The 13 it does read are 5 `.swuibook` page
descriptions, 4 `.swuipop`, 1 `.swuipost` and 3 `.swui` (`pagedata`,
`pagedata_jp`, and one stray field in `example_jp`), which really are props
files that happen to live under a UI extension -- the extension is not the
format in either direction. Everything else is a different language:

    <TAG:field:field:field>

Colon-separated fields inside angle brackets, several tags to a line, `;` to
end of line for a comment. Measured over the disc: 4,354 tags, 49 distinct tag
names, and `<`/`>` balance in every one of the 215 files.

A field is one or more comma-separated tokens -- `<BUTTON:285,137,0:Return to
Game:HIDE:0,0:640,448:1:-1:0>` is eight fields, the first of which is a
position. Tokens are exposed with their own spans because nudging one
coordinate is the common edit and retyping `285,137,0` to move a button down
two pixels is how typos get made.

Arity is NOT fixed per tag. `TEXT` appears with 1, 2, 3, 4, 5, 6, 8 and 10
fields; `BUTTON` with 5 through 9. Any parser that hardcodes a field count per
tag is wrong on this disc, so this one carries no schema at all -- it reports
what is written and lets shape speak for itself.

THE RENAME TRAP. Values are safe to edit; NAMES are not. Renaming a button in
`pause.swui` froze story-level loading, and the reason is on the disc:
*measured*, 69 of 210 screen names and 34 of 144 widget ids appear verbatim as
strings inside the unpacked `SLUS_205.00`, and 95 cross-references run between
screens. The executable looks widgets up by name; a renamed widget is a widget
the code can no longer find. `risk_of()` grades a proposed value change
against both of those sets so the warning names the actual consequence instead
of saying "be careful".
"""

import re

# `<NAME` then an optional `:...` run that cannot contain another bracket.
# Excluding `<>` from the body is what makes an unterminated `<` in a comment
# harmless -- and there are several, e.g. `;; CODE: pagebountychar.<h,cpp>`.
_TAG = re.compile(rb"<([A-Za-z_][A-Za-z0-9_]*)((?::[^<>]*)?)>")

# Numbers here may carry a C float suffix: `<FONT:bosshealth:FONTSCALE:1.0f,1.0f>`.
_NUM = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?f?$")

# What each field position means.
#
# `example.swui` is a documentation file the developers left on the disc -- it
# ships in SWAT.DAT -- and only there, 1 entry, 3,005 bytes -- and lists the
# signature of most tags in the vocabulary. That is the authority for these
# names, so most of them are READ, not guessed. Where it is silent, a label is
# only listed if one shape held for >=95% of that field's instances across the
# whole disc.
#
# The doc is also partly STALE, and three of its signatures disagree with the
# shipped files. Those are corrected here and the disagreement is recorded,
# because a label that matches the manual and not the data is worse than none.
# Note the disagreement is not total: POSMARKER and TEXTURE ship in BOTH the
# documented and the split form, so these labels are right for the majority
# shape and wrong for the rest -- which is what the `support` string on every
# entry is for, and why the panel shows it:
#
#   MODEL      doc `<MODEL:pos:scale:modelname>`, disc `<MODEL:name:pos:scale:
#              modelname>` -- a leading widget name the doc never mentions, in
#              68 of 68 instances.
#   POSMARKER  doc `<POSMARKER:X,Y,Z,W,H,Data>` (one field, commas), disc
#              `<POSMARKER:x,y,z:w,h:name>` (three fields, colons), 144/208.
#              The other 64 are NOT noise: 54 are the documented one-field
#              form verbatim (`450,70,0,1,1,WEAPONPOSB`) and 10 are a
#              two-field variant. Field 0 of those 54 is the whole record, not
#              a position, so the label below is wrong for them -- read the
#              `support` string, which says so.
#   TEXTURE    doc `<TEXTURE:X,Y,Z,W,H:TextureName>`. Both that form (195) and
#              a split `x,y,z:w,h:...` form (208) are on the disc, and the
#              split form takes optional `REVERSE_Y` and `NAME:<id>` modifier
#              fields the doc does not document at all.
#
# Entry is (label, provenance, support).
_DOC = "example.swui"
LABELS = {
    ("BUTTON", 0): ("position x,y,z", _DOC, "195 of 197 instances agree"),
    ("BUTTON", 1): ("label / widget name", _DOC, "197 of 197 instances agree"),
    ("BUTTON", 2): ("link file name, or CLOSEUI/TRYCLOSEUI/HIDE/CALLB/RESET",
                    _DOC, "192 of 197 instances agree"),
    ("BUTTON", 3): ("new page rect x,y,w,h", _DOC, ""),
    ("BUTTON", 4): ("pop-up (0) vs show (1)", _DOC, ""),
    ("BUTTON", 5): ("inputs: -1 all, 1 pad1, 2 pad2", _DOC, ""),
    ("BUTTON", 6): ("data passed to CALLB", _DOC, ""),
    ("TEXT", 0): ("position x,y,z", _DOC, "635 of 662 instances agree"),
    ("TEXT", 1): ("text (via the string table)", _DOC,
                  "639 of 640 instances agree"),
    ("READOUT", 0): ("position x,y,z", _DOC, "138 of 140 instances agree"),
    ("READOUT", 1): ("text -- does NOT use the string table", _DOC,
                     "140 of 140 instances agree"),
    ("COMBO", 0): ("position x,y,z", _DOC, "119 of 121 instances agree"),
    ("COMBO", 1): ("text", _DOC, "121 of 121 instances agree"),
    ("COMBO", 2): ("drop-down list rect x,y,w,h", _DOC, ""),
    ("COMBO", 3): ("arrow width", _DOC, ""),
    ("COMBODATA", 0): ("text", _DOC, ""),
    ("COMBODATA", 1): ("data", _DOC, ""),
    ("LOOKUP", 0): ("table name", _DOC, "85 of 85 instances agree"),
    ("LOOKUPDATA", 0): ("key", _DOC, ""),
    ("LOOKUPDATA", 1): ("value, or a comma-separated list of values", _DOC, ""),
    ("SHOPBOX", 0): ("position x,y,z", _DOC, ""),
    ("SHOPBOX", 1): ("title", _DOC, ""),
    ("SHOPBOX", 2): ("number of columns", _DOC, ""),
    ("SHOPBOX", 3): ("y spacing", _DOC, ""),
    ("SHOPCOLUMN", 0): ("column index", _DOC, ""),
    ("SHOPCOLUMN", 1): ("name", _DOC, ""),
    ("SHOPCOLUMN", 2): ("x,y,width", _DOC, ""),
    ("FONT", 0): ("font name", _DOC, "347 of 347 instances agree"),
    ("POSITION", 0): ("page rect x,y,w,h -- widget offsets are relative to it",
                      _DOC, ""),
    ("FADE", 0): ("start colour r,g,b,a", _DOC, "139 of 141 instances agree"),
    ("FADE", 1): ("end colour r,g,b,a", _DOC, "139 of 141 instances agree"),
    ("FADE", 2): ("time", _DOC, "139 of 141 instances agree"),
    ("PULSE", 0): ("start colour r,g,b,a", _DOC, "79 of 86 instances agree"),
    ("PULSE", 1): ("end colour r,g,b,a", _DOC, "79 of 86 instances agree"),
    ("PULSE", 2): ("time", _DOC, "80 of 86 instances agree"),
    ("SCALESINE", 0): ("rate", _DOC, ""),
    ("SCALESINE", 1): ("minimum scale", _DOC, ""),
    ("SCALESINE", 2): ("maximum scale", _DOC, ""),
    ("BACKGROUND", 0): ("file name -- position and size are IGNORED", _DOC, ""),
    ("IPU_BACKGROUND", 0): ("file name (compressed); position is IGNORED",
                            _DOC, ""),
    # Corrected against the disc; see the note above.
    ("MODEL", 0): ("widget name", "measured", "68 of 68 instances; the doc "
                   "omits this field entirely"),
    ("MODEL", 1): ("position x,y,z", "measured", "66 of 68 instances agree"),
    ("MODEL", 2): ("scale x,y,z", "measured", "66 of 68 instances agree"),
    ("MODEL", 3): ("model name", _DOC, ""),
    ("POSMARKER", 0): ("position x,y,z", "measured",
                       "144 of 208 instances; the doc writes x,y,z,w,h,data "
                       "as one comma-separated field, the disc splits it"),
    ("POSMARKER", 1): ("size w,h", "measured", "144 of 154 instances agree"),
    ("POSMARKER", 2): ("data / marker name", "measured",
                       "144 of 144 instances agree"),
    # Not in the doc at all; shape held across the disc.
    ("SLIDE", 0): ("position x,y,z", "measured", "40 of 40 instances agree"),
    ("COLOR", 0): ("r,g,b,a", "measured", "75 of 75 instances agree"),
    ("COLORC", 0): ("r,g,b,a", "measured", "85 of 85 instances agree"),
    ("BASECOLOR", 0): ("r,g,b,a", "measured", "101 of 101 instances agree"),
    ("GROUP", 0): ("group name", "measured", "71 of 71 instances agree"),
    ("TEXPHASE", 0): ("texture phase", "measured", "50 of 50 instances agree"),
}

# Tags whose whole purpose is to name something the engine resolves later.
# A text field in one of these is a lookup key first and a caption second.
NAMING_TAGS = frozenset((
    "BUTTON", "GROUP", "LOOKUP", "MODEL", "FONT", "TEXPHASE", "COMBO",
    "READOUT", "TEXTBOX", "SLIDER", "MENUBOX", "SHOPBOX", "FACADE",
    # Not a tag: the synthetic name `routes2` grades a preload-list entry
    # under. Those files are nothing BUT lookup keys, so an entry that is not
    # in the executable still deserves the "resolved by name" wording rather
    # than the vaguer fallback.
    "PRELOAD",
))

UI_EXTS = ("swui", "swuibook", "swuipop", "swuipost")


class Token:
    """One comma-separated piece of a field, with its byte span."""

    __slots__ = ("text", "start", "end")

    def __init__(self, text, start, end):
        self.text = text
        self.start = start
        self.end = end

    @property
    def numeric(self):
        return bool(_NUM.match(self.text.strip()))


class Field:
    """One colon-separated field of a tag, with its byte span."""

    __slots__ = ("index", "path", "raw", "start", "end", "tokens")

    def __init__(self, index, path, raw, start, end, tokens):
        self.index = index
        self.path = path          # e.g. "BUTTON#3.f1"
        self.raw = raw
        self.start = start
        self.end = end
        self.tokens = tokens

    @property
    def kind(self):
        """`num1`..`numN` when every token is a number, else `text`."""
        if not self.tokens:
            return "empty"
        if all(t.numeric for t in self.tokens):
            return "num%d" % len(self.tokens)
        return "text"

    def to_json(self, tag_name):
        lab = LABELS.get((tag_name, self.index))
        d = dict(path=self.path, index=self.index, value=self.raw,
                 kind=self.kind,
                 tokens=[dict(path="%s.t%d" % (self.path, i), text=t.text,
                              numeric=t.numeric)
                         for i, t in enumerate(self.tokens)])
        if lab:
            d["label"], d["label_from"], d["label_support"] = lab
        return d


class Tag:
    """One `<NAME:...>` element."""

    __slots__ = ("name", "path", "fields", "line", "start", "end", "commented")

    def __init__(self, name, path, fields, line, start, end, commented):
        self.name = name
        self.path = path          # e.g. "BUTTON#3"
        self.fields = fields
        self.line = line
        self.start = start
        self.end = end
        self.commented = commented

    def to_json(self):
        return dict(tag=self.name, path=self.path, line=self.line,
                    commented=self.commented,
                    text=None,   # filled by summary(); needs the source bytes
                    fields=[f.to_json(self.name) for f in self.fields])


def is_swui(name):
    base = name.rsplit("/", 1)[-1].lower()
    return "." in base and base.rsplit(".", 1)[-1] in UI_EXTS


def dialect(data):
    """Which of the three languages under these four extensions this is.

    The extension is not the format. *Measured* over the 212 readable UI files:

        tags   187  `<TAG:field:...>`         -- this module
        props   12  `Key value` / `block {`   -- props.py reads these outright
                                                 (the .swuibook page setups,
                                                 the .swuipop/.swuipost popups
                                                 and pagedata.swui)
        list    13  one bare name per line    -- the `*cache*.swui` preload
                                                 manifests, which are neither

    Routing on content rather than on extension is what takes coverage from
    88.35% of content lines to 100%. Guessing from the name would have put
    `pagedata.swui` in the tag reader, where it parses to nothing at all.
    """
    okay, why = looks_parsable(data)
    if not okay:
        return "unreadable"
    if parse(data):
        return "tags"
    from . import props as P
    if P.parse(data):
        return "props"
    return "list"


class Line:
    """One bare entry in the `list` dialect, with its byte span."""

    __slots__ = ("index", "path", "raw", "start", "end", "line")

    def __init__(self, index, path, raw, start, end, line):
        self.index = index
        self.path = path
        self.raw = raw
        self.start = start
        self.end = end
        self.line = line

    def to_json(self):
        return dict(path=self.path, index=self.index, value=self.raw,
                    line=self.line)


def parse_list(data):
    """The `*cache*.swui` preload manifests: one screen name per line.

    These decide which screens are resident before they are needed, so a name
    here is a lookup key exactly like a name in a tag -- `risk_of` grades them
    on the same evidence.
    """
    if isinstance(data, str):
        data = data.encode("latin-1")
    out: list[Line] = []
    pos = 0
    line_no = 1
    n = len(data)
    while pos <= n:
        nl = data.find(b"\n", pos)
        if nl < 0:
            nl = n
        line = data[pos:nl]
        cut = _comment_at(line)
        body = line[:cut] if cut >= 0 else line
        stripped = body.strip()
        if stripped:
            lead = len(body) - len(body.lstrip())
            start = pos + lead
            out.append(Line(len(out), "line%d" % len(out),
                            stripped.decode("latin-1"),
                            start, start + len(stripped), line_no))
        pos = nl + 1
        line_no += 1
        if nl == n:
            break
    return out


def apply_list(data, edits):
    """Rewrite bare list entries in place, same in-place contract as `apply`."""
    if isinstance(data, str):
        data = data.encode("latin-1")
    spans = {ln.path: ln for ln in parse_list(data)}
    todo = []
    for path, value in edits.items():
        ln = spans.get(path)
        if ln is None:
            raise KeyError("no entry %r in this file" % path)
        text = str(value)
        bad = [c for c in ";\r\n" if c in text]
        if bad:
            raise ValueError("%s cannot contain %s" % (path, " or ".join(bad)))
        todo.append((ln.start, ln.end, text.encode("latin-1")))
    out = bytearray(data)
    for start, end, val in sorted(todo, reverse=True):
        out[start:end] = val
    return bytes(out)


def looks_parsable(data):
    """Is this the ASCII tag dialect, or something else wearing the extension?

    `wip.swui` is UTF-16LE with a BOM -- the only one on the disc -- and two
    `blank*.swui` are zero bytes. Refusing them up front is better than
    handing back a field list built from every other byte.
    """
    if not data:
        return False, "the file is empty (0 bytes)"
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return False, "the file is UTF-16, not the ASCII tag dialect"
    sample = data[:4096]
    ok = sum(1 for c in sample if 32 <= c < 127 or c in (9, 10, 13))
    if ok / len(sample) < 0.95:
        return False, "the file does not look like text"
    return True, ""


def _comment_at(line):
    """Byte offset of the `;` that starts a comment on this line, or -1.

    A `;` is a comment start anywhere on the line -- there is no escaping and
    no quoting in this format. *Measured:* not one of the 4,354 tags on the
    disc contains a `;` inside its body, so this cannot cut a tag in half.
    What it does do is correctly mark the 392 commented-out tag lines, which
    are real content someone may want to see but must not be told are live.
    """
    return line.find(b";")


def parse(data, include_commented=True):
    """Every tag in the file, with byte spans for each field and token.

    Comment handling runs per line rather than over the whole blob because the
    disc is CRLF and a `;` only reaches its own line ending. Commented tags are
    still returned -- they are how the original authors left notes about what a
    screen used to do -- but flagged, and `apply` refuses to write one.
    """
    if isinstance(data, str):
        data = data.encode("latin-1")
    tags = []
    seen: dict[str, int] = {}
    pos = 0
    line_no = 1
    n = len(data)
    while pos <= n:
        nl = data.find(b"\n", pos)
        if nl < 0:
            nl = n
        line = data[pos:nl]
        cut = _comment_at(line)
        for m in _TAG.finditer(line):
            commented = cut >= 0 and m.start() > cut
            if commented and not include_commented:
                continue
            name = m.group(1).decode("latin-1").upper()
            seen[name] = seen.get(name, 0) + 1
            tpath = "%s#%d" % (name, seen[name])
            body = m.group(2)              # includes the leading ':' or empty
            fields = []
            if body:
                # Field i runs between colon i and colon i+1. Offsets are
                # tracked against the file, not the field text, so a span is
                # usable by `apply` without any re-scanning.
                base = pos + m.start(2)
                parts = body.split(b":")   # parts[0] is empty by construction
                off = base + 1             # skip the leading ':'
                for i, p in enumerate(parts[1:]):
                    toks = []
                    toff = off
                    for piece in p.split(b","):
                        toks.append(Token(piece.decode("latin-1"),
                                          toff, toff + len(piece)))
                        toff += len(piece) + 1
                    fields.append(Field(i, "%s.f%d" % (tpath, i),
                                        p.decode("latin-1"),
                                        off, off + len(p), toks))
                    off += len(p) + 1
            tags.append(Tag(name, tpath, fields, line_no,
                            pos + m.start(), pos + m.end(), commented))
        pos = nl + 1
        line_no += 1
        if nl == n:
            break
    return tags


def index(data):
    """{path: (Field|Token span)} for both field and token paths."""
    spans = {}
    for t in parse(data):
        for f in t.fields:
            spans[f.path] = (f.start, f.end, t.commented)
            for i, tk in enumerate(f.tokens):
                spans["%s.t%d" % (f.path, i)] = (tk.start, tk.end, t.commented)
    return spans


def apply(data, edits):
    """Rewrite field or token values in place. `edits` is {path: new text}.

    Same contract as `props.apply`: only the value span moves, so a file whose
    slot has no headroom still takes any same-width edit. Applied back to
    front so earlier spans stay valid.

    Two refusals that matter more here than in props: a `:` or a `<`/`>` typed
    into a value would silently re-cut the tag into different fields, and a
    commented-out tag is not live markup so writing to it is always a mistake.
    """
    if isinstance(data, str):
        data = data.encode("latin-1")
    spans = index(data)

    todo = []
    for path, value in edits.items():
        sp = spans.get(path)
        if sp is None:
            raise KeyError("no field %r in this file" % path)
        start, end, commented = sp
        if commented:
            raise ValueError(
                "%s is inside a comment, so writing to it would change "
                "nothing the game reads" % path)
        text = str(value)
        bad = [c for c in ":<>;\r\n" if c in text]
        if bad:
            raise ValueError(
                "%s cannot contain %s -- those characters end a field, a tag "
                "or the line" % (path, " or ".join(repr(c) for c in bad)))
        if "," in text and path.rsplit(".", 1)[-1].startswith("t"):
            raise ValueError(
                "%s is a single token, so it cannot contain a comma" % path)
        todo.append((start, end, text.encode("latin-1")))

    # Overlap would mean a field edit and one of its own token edits in the
    # same call; back-to-front splicing would corrupt both silently.
    ordered = sorted(todo, reverse=True)
    for i in range(len(ordered) - 1):
        if ordered[i][0] < ordered[i + 1][1]:
            raise ValueError("two edits in this batch cover the same bytes; "
                             "edit the whole field or its tokens, not both")

    out = bytearray(data)
    for start, end, val in ordered:
        out[start:end] = val
    return bytes(out)


def set_fields(data, changes):
    """The `setter=` signature `Studio.edit_creature` expects."""
    return apply(data, changes)


# ------------------------------------------------------------------ the trap

_RISK_CACHE: dict[str, tuple[frozenset[str], frozenset[str], str]] = {}
_SCREEN_CACHE: dict[str, tuple[dict[str, bytes], dict[str, list[str]]]] = {}


def invalidate(disc=None):
    """Drop the cached screen bytes after a write.

    Without this the browse list and -- worse -- the rename evidence keep
    answering from the bytes that were on the disc before the edit, so a
    freshly renamed widget still reads as "referenced by another screen" and
    the next edit is graded against a disc that no longer exists.
    """
    if disc is None:
        _SCREEN_CACHE.clear()
        _RISK_CACHE.clear()
        return
    key = getattr(disc, "path", str(disc))
    _SCREEN_CACHE.pop(key, None)
    _RISK_CACHE.pop(key, None)


def all_screens(disc):
    """{filename: bytes} for every distinct UI file on the disc.

    *Measured:* 1,111 `.swui` entries resolve to 205 distinct names, plus 5
    `.swuibook`, 4 `.swuipop` and 1 `.swuipost` -- 215 files, and they are
    small. Read once and cached, because the cross-reference evidence behind
    the rename warning needs all of them and the browse list needs their
    dialects.
    """
    key = getattr(disc, "path", str(disc))
    hit = _SCREEN_CACHE.get(key)
    if hit is not None:
        return hit
    out = {}
    where: dict[str, list[str]] = {}
    for arc in disc.archives():
        try:
            d = disc.archive(arc)
        except Exception:
            continue
        for e in d:
            if e.ext not in UI_EXTS:
                continue
            where.setdefault(e.name, []).append(arc)
            if e.name in out:
                continue
            try:
                out[e.name] = d.read(e)
            except Exception:
                out[e.name] = b""
    _SCREEN_CACHE[key] = (out, where)
    return out, where


def lookup_names(disc, screens=None):
    """The two evidence sets `risk_of` grades a rename against.

    `exe`   -- every printable run of 3..64 bytes in the UNPACKED SLUS_205.00,
               lowercased. The shipped executable is aPLib-packed, so the
               strings are not visible until `elfpack` undoes it; searching the
               packed image finds nothing and would have made every rename look
               safe. *Measured:* 17,537 distinct strings, and 69 of the 210
               screen names plus 34 of the 144 widget ids are among them.
    `disc`  -- every screen name that some other UI file names in a field.
               *Measured:* 95 cross-references over 31 screens.

    Returns (exe, disc_refs, note). `note` is empty when both sets are real and
    carries the reason when the executable could not be read -- the caller MUST
    surface it. Degrading silently would turn every genuine "danger" into a
    "caution" and quietly remove the one warning this panel exists to give.

    Cached per ISO path. The unpack is ~4.6 s *measured*, which is fine once
    and unacceptable per keystroke -- the front end asks for a risk grade on
    every edit to a text field.
    """
    key = getattr(disc, "path", str(disc))
    hit = _RISK_CACHE.get(key)
    if hit is not None:
        return hit

    exe: frozenset[str] = frozenset()
    note = ""
    try:
        from . import elfpack
        # unpack_elf returns (image, base_vaddr) -- NOT bytes. Unpacking the
        # tuple wrongly raised here, was caught by this very handler, and left
        # the exe set empty while the panel went on reporting "no match in the
        # executable" as if it had looked.
        image, _base = elfpack.unpack_elf(disc.iso.read_file("SLUS_205.00"))
        exe = frozenset(m.group(0).decode("latin-1").lower() for m in
                        re.finditer(rb"[\x20-\x7e]{3,64}", image))
    except Exception as ex:
        note = ("SLUS_205.00 could not be read (%s), so names were checked "
                "only against other screens on the disc." % ex)

    refs = set()
    if screens:
        stems = set(n.rsplit(".", 1)[0].lower() for n in screens)
        for data in screens.values():
            if not data or dialect(data) != "tags":
                continue
            for t in parse(data, include_commented=False):
                for f in t.fields:
                    for tok in f.raw.split(","):
                        low = tok.strip().lower()
                        if low in stems:
                            refs.add(low)

    out = (exe, frozenset(refs), note)
    _RISK_CACHE[key] = out
    return out


def risk_of(tag_name, field, old, new, exe_names=None, disc_names=None):
    """How dangerous is changing this value from `old` to `new`?

    Returns (level, reason) where level is "safe", "caution" or "danger".

    The grading is evidence-first. A number is a number and moving a button is
    free. A text field is a *possible* lookup key. A text field whose current
    value is a string in the executable, or is referenced by another screen on
    the disc, is a *known* lookup key -- that is the case that froze story-level
    loading, and it is the only one that earns "danger".
    """
    exe_names = exe_names or frozenset()
    disc_names = disc_names or frozenset()
    o = (old or "").strip()
    nw = (new or "").strip()
    if o == nw:
        return "safe", "unchanged"
    if field.kind != "text":
        # A single token inside a numeric field is graded on its own value,
        # not on the field's. Typing a word into the y of `285,137,0` leaves
        # `285,word,0`, which the parser will happily round-trip and the
        # engine will not -- and the field's kind still reads `num3`, so
        # trusting that alone called it safe.
        if _NUM.match(o) and not _NUM.match(nw):
            return "caution", ("this position/size/colour slot held a number "
                               "and %r is not one; the engine parses these as "
                               "numbers and will not read it back" % nw)
        return "safe", "a number -- position, size, colour or timing"

    low = o.lower()
    where = []
    if low in exe_names:
        where.append("SLUS_205.00 contains this exact string")
    if low in disc_names:
        where.append("another screen on the disc points at this name")
    if where:
        return "danger", (
            "%s. The engine finds this widget by name, so renaming it here "
            "does not rename it there -- the lookup fails and the screen or "
            "the level it loads can hang. This is the failure that froze "
            "story-level loading." % " and ".join(where))
    if tag_name in NAMING_TAGS:
        return "caution", (
            "a text field on a <%s>, which is the kind of tag the engine "
            "resolves by name. This exact name is not in the executable and "
            "no other screen references it, so it is probably a caption -- "
            "but nothing on the disc proves it." % tag_name)
    return "caution", ("text rather than a number; if anything looks this up "
                       "by name, the lookup will stop matching")


def summary(data, limit=800):
    """Tags, fields and spans in the shape the UI renders."""
    okay, why = looks_parsable(data)
    if not okay:
        return dict(parsable=False, why=why, tags=[], total=0,
                    live=0, commented=0, bytes=len(data or b""))
    tags = parse(data)
    live = [t for t in tags if not t.commented]
    out = []
    for t in tags[:limit]:
        d = t.to_json()
        d["text"] = data[t.start:t.end].decode("latin-1")
        out.append(d)
    counts: dict[str, int] = {}
    for t in live:
        counts[t.name] = counts.get(t.name, 0) + 1
    return dict(parsable=True, why="", tags=out, total=len(tags),
                live=len(live), commented=len(tags) - len(live),
                truncated=len(tags) > limit, bytes=len(data),
                tag_counts=sorted(counts.items(), key=lambda kv: -kv[1]))
