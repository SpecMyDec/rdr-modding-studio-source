"""The campaign: mission order, unlockables and the item economy.

Three CSVs in SWAT.DAT (and only there) drive the whole progression:

    levelflow.csv      32 missions in play order -- level, player character,
                       starting weapons, cash reward, and the Normal /
                       Excellent / Hidden reward slots, plus the challenge
                       thresholds (time, damage taken, accuracy, combo)
    unlockables.csv    221 characters / stages / cheats / journal entries.
                       Column "Default" is the master unlock switch: 1 means
                       shipped unlocked, and only 19 rows are set vanilla
    invmasterlist.csv  177 items with shop / repair / upgrade costs

Cells are tracked by byte span so an edit rewrites exactly one field. These
files sit in tight slots, and regenerating a CSV would balloon it.
"""

import re

FILES = {
    "levelflow": "levelflow.csv",
    "unlockables": "unlockables.csv",
    "items": "invmasterlist.csv",
}

# Column names taken from levelflow.csv's own header row, not invented.
#
# The previous list stopped at 16 names and was WRONG from index 13 on: it
# called columns 13/14/15 "BH1", "BH2" and "BH Weapons" where the file calls
# them "BHunter", "BHunter Weapons" and "BHunter Health", and it had no name
# at all for 16 and 17. So the Campaign tab showed every Bounty Hunter value
# under the wrong heading -- the weapons override was labelled "BH2", the
# Health column was labelled "BH Weapons" -- and the last two columns were
# invisible. Shortened here only where the header carries a unit
# ("Max Time (sec)" -> "Max Time"); nothing is renamed.
LEVELFLOW_COLS = [
    "Level", "Variation", "Character", "Weapons", "Reward $", "Max Time",
    "Max Damage", "Min Accuracy", "Min Combo", "Normal", "Excellent",
    "Hidden", "Show Reward",
    # 13-16 are the Bounty Hunter block. Health and Dead-Eye are parsed by the
    # game and zero on all 32 rows: difficulty knobs that were wired up and
    # never used, which makes them the most interesting columns in the file.
    "BHunter", "BHunter Weapons", "BHunter Health", "BHunter Dead-Eye",
    "Boss",
]


class Cell:
    __slots__ = ("text", "start", "end")

    def __init__(self, text, start, end):
        self.text = text
        self.start = start
        self.end = end


def parse_csv(data):
    """Rows of Cells with byte spans. Handles simple double-quoted fields."""
    rows = []
    i = 0
    n = len(data)
    row = []
    cell_start = 0
    in_quote = False

    def flush_cell(end):
        raw = data[cell_start:end]
        rows_text = raw.decode("latin-1").strip()
        row.append(Cell(rows_text, cell_start, end))

    while i < n:
        c = data[i:i + 1]
        if in_quote:
            if c == b'"':
                in_quote = False
            i += 1
            continue
        if c == b'"':
            in_quote = True
            i += 1
            continue
        if c == b",":
            flush_cell(i)
            i += 1
            cell_start = i
            continue
        if c in (b"\r", b"\n"):
            flush_cell(i)
            rows.append(row)
            row = []
            while i < n and data[i:i + 1] in (b"\r", b"\n"):
                i += 1
            cell_start = i
            continue
        i += 1
    if cell_start < n or row:
        flush_cell(n)
        rows.append(row)
    return [r for r in rows if any(c.text for c in r)]


def missions(data):
    """levelflow.csv -> ordered mission list."""
    rows = parse_csv(data)
    if not rows:
        return []
    out = []
    for n, row in enumerate(rows[1:]):
        if not row or not row[0].text:
            continue
        m = dict(index=n, cells=[c.text for c in row])
        for i, name in enumerate(LEVELFLOW_COLS):
            m[name] = row[i].text if i < len(row) else ""
        out.append(m)
    return out


def unlockables(data):
    rows = parse_csv(data)
    out = []
    for n, row in enumerate(rows[1:]):
        if len(row) < 4 or not row[0].text:
            continue
        out.append(dict(index=n, name=row[0].text, type=row[1].text,
                        texture=row[2].text, default=row[3].text,
                        criteria=row[4].text if len(row) > 4 else "",
                        value=row[5].text if len(row) > 5 else ""))
    return out


def items(data):
    rows = parse_csv(data)
    out = []
    for n, row in enumerate(rows[1:]):
        if len(row) < 6 or not row[0].text:
            continue
        out.append(dict(index=n, name=row[0].text, type=row[1].text,
                        icon=row[2].text, shop=row[3].text,
                        repair=row[4].text, upgrade=row[5].text))
    return out


def set_cell(data, row_index, col_index, value, header=True):
    """Replace one cell. row_index counts data rows, skipping the header."""
    rows = parse_csv(data)
    r = row_index + (1 if header else 0)
    if not (0 <= r < len(rows)):
        raise IndexError("row %d out of range (%d rows)" % (row_index, len(rows)))
    row = rows[r]
    if not (0 <= col_index < len(row)):
        raise IndexError("column %d out of range (%d columns)"
                         % (col_index, len(row)))
    cell = row[col_index]
    text = str(value)
    if "," in text or '"' in text:
        text = '"%s"' % text.replace('"', '""')
    return data[:cell.start] + text.encode("latin-1") + data[cell.end:]


def unlock_all(data, only_type=None):
    """Set every row's Default column to 1.

    This is the correct switch for making cheats and characters available --
    in story mode they are otherwise Hidden rewards on specific late levels,
    not purchasable.
    """
    rows = parse_csv(data)
    edits = []
    changed = 0
    for row in rows[1:]:
        if len(row) < 4 or not row[0].text:
            continue
        if only_type and row[1].text != only_type:
            continue
        if row[3].text != "1":
            edits.append((row[3].start, row[3].end, b"1"))
            changed += 1
    out = bytearray(data)
    for s, e, t in sorted(edits, key=lambda x: -x[0]):
        out[s:e] = t
    return bytes(out), changed


def summary(game):
    """Everything the campaign view needs, read from SWAT.DAT."""
    lf = game.read("SWAT.DAT", FILES["levelflow"])
    un = game.read("SWAT.DAT", FILES["unlockables"])
    it = game.read("SWAT.DAT", FILES["items"])
    ms = missions(lf)
    us = unlockables(un)
    return dict(
        missions=ms,
        columns=LEVELFLOW_COLS,
        unlockables=us,
        items=items(it),
        unlocked=sum(1 for u in us if u["default"] == "1"),
        types=sorted({u["type"] for u in us if u["type"]}),
    )
