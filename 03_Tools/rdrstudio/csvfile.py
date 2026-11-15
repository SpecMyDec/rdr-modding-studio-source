"""`.csv` -- the disc's tabular text, with the byte span of every cell.

1,022 distinct names and 2,541 copies (measured on RDR_VANILLA.iso). SWAT.DAT
holds all 1,022; 595 exist nowhere else. They are sound banks, music cues,
stream lists, shop stock, the multiplayer roster, the mission flow table, and
several hundred AI accuracy/fire tables.

`props.py` is the WRONG tool for these. Its grammar wants `Key value` with a
whitespace or colon separator, and a comma-separated line matches at most its
first word: measured, `props.parse` returns 0 fields for `snd_00.csv` and 1
bogus field (`Character`) for `mpcharbounty.csv`. Hence this module.

Three things the stdlib `csv` module cannot do that are required here:

  1. **Byte spans.** THE ONE RULE is that an edit must fit the slot the
     original occupied, compressed. Re-serialising a sheet with `csv.writer`
     rewrites every line -- it normalises quoting, drops the trailing commas
     these files are padded with, and would emit LF where the disc is CRLF on
     all 1,022 files (measured). Patching only the edited cell's span leaves
     every other byte alone, which is what lets a file with 6 bytes of
     headroom (`shophudnames.csv`) take an edit at all.
  2. **Latin-1, not UTF-8.** 22 files carry bytes above 0x7E and one carries a
     NUL (measured). Latin-1 round-trips all 256 values; UTF-8 would raise.
  3. **Sections.** `snd_cfg_lvl_*.csv` is not one table. `snd_cfg_lvl_woods`
     is a run of `Key,Value` pairs, then a `Point,Volume` table, then a
     `ReqCode,x,y,z` one -- three shapes in 557 bytes. Measured across the
     disc: 672 files are one section, 312 are two and 37 are three.

**Where the header is.** Not line 0. The `snd_<num>` family puts a `#` comment
on line 0 and `MaxCode,<n>` on line 1, so the real header is line 2 -- in 253
of 256 files. The other three: `snd_9998` and `snd_9999` have no MaxCode line
(header on line 1) and `snd_100` has two comment lines (header on line 3).
Hard-coding index 2 would silently mis-read three files and mis-label every
ordinary CSV, whose header IS line 0. `sections()` finds it instead, by shape:
see `_labelish`.
"""

import re
from typing import Any

# A cell that parses as a number is data, never a column name. This is the
# whole of the header test's evidence and it is deliberately strict: a bare
# `+`, `1e`, or `NONE` is text.
_NUM = re.compile(r"^[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$")

# Quoting is added only where the scanner above could not read the value back
# otherwise: a delimiter, a line terminator, or a leading quote that would be
# taken as the start of a quoted field. Deliberately NOT triggered by leading
# or trailing spaces -- the disc is full of them (` 128`, ` name`, 1,046 cells
# across 57 files, measured) and quoting those would grow a file by two bytes
# per cell for no gain, in files where six bytes of headroom is normal.
#
# A `"` in the middle of an unquoted field is left alone for the same reason:
# `a"b` reads back as `a"b`, and rewriting it as `"a""b"` would be this tool
# inventing an escape convention the game's own parser may not share.
_NEEDS_QUOTE = re.compile(r'^"|[,\r\n]')


class Cell:
    """One field, with the span of its RAW text -- quotes included."""

    __slots__ = ("text", "start", "end", "quoted")

    def __init__(self, text, start, end, quoted):
        self.text = text        # decoded value, quotes stripped, "" unescaped
        self.start = start      # byte offset of the raw field in the file
        self.end = end
        self.quoted = quoted

    def __repr__(self):
        return "<Cell %r @%d..%d%s>" % (self.text, self.start, self.end,
                                        " q" if self.quoted else "")


class Row:
    __slots__ = ("index", "cells", "kind", "start", "end", "section")

    def __init__(self, index, cells, kind, start, end):
        self.index = index
        self.cells = cells
        self.kind = kind        # comment | blank | header | data
        self.start = start
        self.end = end          # excludes the line terminator
        self.section = -1       # -1 = context (comment/blank), else section id

    @property
    def filled(self):
        return sum(1 for c in self.cells if c.text.strip())

    def to_json(self):
        return dict(i=self.index, kind=self.kind, section=self.section,
                    cells=[c.text for c in self.cells])


# --------------------------------------------------------------- the scanner

def _decode(data):
    return data.decode("latin-1") if isinstance(data, bytes) else data


def _scan_row(s, pos, n):
    """Cells of the row starting at `pos`, plus the offset after its EOL.

    Written by hand rather than handed to `csv.reader` for one reason: the
    reader gives values and this needs offsets. A quoted field may legally
    contain the delimiter and the line terminator, so rows are found by
    scanning fields, not by splitting on newlines first.
    """
    cells = []
    start_of_row = pos
    while True:
        fstart = pos
        if pos < n and s[pos] == '"':
            pos += 1
            buf = []
            while pos < n:
                c = s[pos]
                if c == '"':
                    if pos + 1 < n and s[pos + 1] == '"':
                        buf.append('"')
                        pos += 2
                        continue
                    pos += 1
                    break
                buf.append(c)
                pos += 1
            # Anything between the closing quote and the delimiter is junk the
            # game's own exporter never wrote, but keep it inside the span so
            # a rewrite cannot orphan it.
            #
            # A quote that is never closed swallows the rest of the file into
            # one cell. That is the one input where a rewrite is not
            # byte-exact -- re-encoding adds the missing closing quote, one
            # byte -- and no file on this disc has one (measured: identity
            # rewrite is byte-exact on all 1,022). The value still survives
            # intact, and `apply`'s read-back check would catch it if it did
            # not; the extra byte is the slot check's problem, not silent
            # corruption.
            while pos < n and s[pos] not in ',\r\n':
                pos += 1
            cells.append(Cell("".join(buf), fstart, pos, True))
        else:
            while pos < n and s[pos] not in ',\r\n':
                pos += 1
            cells.append(Cell(s[fstart:pos], fstart, pos, False))
        if pos < n and s[pos] == ',':
            pos += 1
            continue
        break

    end = pos
    if pos < n and s[pos] == '\r':
        pos += 1
    if pos < n and s[pos] == '\n':
        pos += 1
    return cells, start_of_row, end, pos


def _classify(cells):
    if not any(c.text.strip() for c in cells):
        return "blank"
    for c in cells:
        t = c.text.strip()
        if t:
            return "comment" if t.startswith("#") else "data"
    return "blank"


def _labelish(row):
    """Could this row be a column-name row?

    Two or more filled cells and not one of them a number. `MaxCode,50,,,...`
    fails on the 50, which is exactly why the `snd_<num>` preamble does not
    get mistaken for the header; `SAMPLES,Code,VB,...` passes.

    A data row CAN pass -- `lvl_hng,hud512HNGStore,hud_paper1,...` in
    `shophudnames.csv` has no numbers anywhere. That is why passing this test
    is not sufficient on its own; `sections()` also requires a change of
    shape.
    """
    if row.kind != "data" or row.filled < 2:
        return False
    return not any(_NUM.match(c.text.strip()) for c in row.cells if c.text.strip())


class Sheet:
    """A parsed .csv: every row, every cell, every span."""

    def __init__(self, data):
        self.raw = data if isinstance(data, bytes) else data.encode("latin-1")
        s = _decode(self.raw)
        n = len(s)
        self.rows = []
        pos = 0
        i = 0
        while pos < n:
            cells, rstart, rend, nxt = _scan_row(s, pos, n)
            r = Row(i, cells, _classify(cells), rstart, rend)
            self.rows.append(r)
            i += 1
            pos = nxt
        self.sections = _sections(self.rows)

    # ------------------------------------------------------------- access

    def cell(self, row, col):
        try:
            r = self.rows[row]
        except IndexError:
            raise KeyError("no row %d in this file" % row)
        try:
            return r.cells[col]
        except IndexError:
            raise KeyError("row %d has %d columns, not %d"
                           % (row, len(r.cells), col + 1))

    def header_row(self):
        """Index of the first section's header row, or None."""
        for sec in self.sections:
            if sec["header"] is not None:
                return sec["header"]
        return None

    def to_json(self, limit=3000):
        return dict(
            rows=[r.to_json() for r in self.rows[:limit]],
            total_rows=len(self.rows),
            truncated=len(self.rows) > limit,
            sections=self.sections,
            header=self.header_row(),
            tabular=len(self.sections) == 1 and self.sections[0]["header"] == 0,
            bytes=len(self.raw))


def _sections(rows):
    """Split the sheet where its shape changes.

    A new section opens at a `_labelish` row when the run above it has no
    header of its own, or has a header of a different width. Both conditions
    are load-bearing:

      * "no header above" is what promotes `SAMPLES,...` on line 2 over the
        anonymous `MaxCode,50` run on line 1.
      * "different width" is what keeps `lvl_hng,...` in `shophudnames.csv`
        a data row -- five cells, same as the header above it -- while
        promoting `ReqCode,x,y,z` in `snd_cfg_lvl_woods.csv`, whose four
        cells follow a two-cell `Point,Volume` table.

    Comments and blanks belong to no section; they are context and stay
    exactly where they are.
    """
    out: list[dict[str, Any]] = []
    cur = None
    for r in rows:
        if r.kind in ("comment", "blank"):
            continue
        if _labelish(r) and (cur is None
                             or cur["header"] is None
                             or cur["width"] != len(r.cells)):
            r.kind = "header"
            cur = dict(id=len(out), header=r.index, width=len(r.cells),
                       columns=[c.text for c in r.cells],
                       first=r.index, last=r.index, rows=0)
            out.append(cur)
        else:
            if cur is None:
                cur = dict(id=len(out), header=None, width=len(r.cells),
                           columns=[], first=r.index, last=r.index, rows=0)
                out.append(cur)
            cur["rows"] += 1
        r.section = cur["id"]
        cur["last"] = r.index
    return out


def parse(data):
    return Sheet(data)


# ---------------------------------------------------------------- rewriting

def _encode(cell, text):
    if cell.quoted or _NEEDS_QUOTE.search(text):
        return ('"%s"' % text.replace('"', '""')).encode("latin-1")
    return text.encode("latin-1")


def _key(path):
    """`"12.3"` -> (12, 3). Row and column, both zero-based.

    Negatives are rejected rather than passed through. `Sheet.cell` indexes
    Python lists, so `-1.0` did not raise -- it silently resolved to the LAST
    row (measured on `mpcharbounty.csv`: `apply(d, {"-1.0": "zzzz"})` renamed
    `mzzt`) and the route's out-of-range check never fired, because there was
    nothing out of range. An address that names one row and edits another is
    worse than a refusal.
    """
    try:
        rs, cs = str(path).split(".", 1)
        r, c = int(rs), int(cs)
    except (ValueError, TypeError):
        raise KeyError("cell address %r is not row.column" % (path,))
    if r < 0 or c < 0:
        raise KeyError("cell address %r is negative; rows and columns count "
                       "from 0" % (path,))
    return r, c


class Unwritable(Exception):
    """The requested value cannot be stored in a cell and read back as itself."""


def apply(data, edits):
    """Rewrite cells in place. `edits` is {"row.col": text}.

    Back-to-front, so earlier spans stay valid, and only the addressed field's
    bytes move -- every comment, blank line, trailing comma, quoting style and
    CRLF in the file survives untouched. Same contract as `props.apply`, and
    for the same reason: these live in fixed slots.

    The result is re-parsed before it is returned and every edited cell is
    compared with what was asked for. The plan a user approves is a byte count
    for a specific set of values; a quoting rule that silently ate a character
    would make that plan a lie while still fitting the slot. Cheap insurance:
    the largest .csv on the disc is snd_2898.csv at 48,429 bytes.
    """
    sheet = Sheet(data)
    todo = []
    want = {}
    for path, value in edits.items():
        r, c = _key(path)
        cell = sheet.cell(r, c)
        text = str(value)
        want[(r, c)] = text
        todo.append((cell.start, cell.end, _encode(cell, text)))
    out = bytearray(sheet.raw)
    for start, end, val in sorted(todo, reverse=True):
        out[start:end] = val
    raw = bytes(out)

    if want:
        back = Sheet(raw)
        for (r, c), text in want.items():
            try:
                got = back.cell(r, c).text
            except KeyError:
                raise Unwritable("row %d column %d disappeared after the edit"
                                 % (r, c))
            if got != text:
                raise Unwritable("row %d column %d would store %r, not %r"
                                 % (r, c, got, text))
    return raw


def set_fields(data, changes):
    """The `setter=` signature `Studio.edit_creature` calls.

    Same shape as `creature.set_fields` and `swt.set_fields`, so a CSV edit
    reuses the loop that already locates every copy of a file, checks each
    against its own slot, and journals them all as ONE undo step.
    """
    return apply(data, changes)


def preview(data, edits):
    """(new_bytes, [{row, col, before, after}]) without touching the disc."""
    sheet = Sheet(data)
    rows = []
    for path, value in edits.items():
        r, c = _key(path)
        cell = sheet.cell(r, c)
        rows.append(dict(row=r, col=c, before=cell.text, after=str(value)))
    rows.sort(key=lambda d: (d["row"], d["col"]))
    return apply(data, edits), rows
