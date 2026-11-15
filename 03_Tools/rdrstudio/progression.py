"""Progression: difficulty, Bounty Hunter, unlocks and cheats.

Everything the player *earns* is driven from two CSVs that live in SWAT.DAT
and nowhere else on the disc (`build_index()` finds one copy of each, in
SWAT.DAT -- they are not in FRONT.DAT and not in any level archive):

    levelflow.csv     32 missions in play order. 18 columns. Columns 13-16
                      are the Bounty Hunter block.
    unlockables.csv   221 rows: 147 journal, 48 character, 16 stage, 10 cheat.

The headline finding this module exists to expose: **`BHunter Health` and
`BHunter Dead-Eye` (columns 15 and 16) are zero on all 32 rows.** They sit in
the middle of a column block the game demonstrably reads, they are named like
difficulty multipliers, and nobody ever set them. They are the only
per-mission difficulty knobs on the disc that vanilla leaves entirely on the
table, so `read_levelflow()` surfaces them first and `set_mission_field()`
makes them writable.

`campaign.py` already reads these two files for the campaign view. This module
is not a replacement for it: it adds the progression semantics (typed rows,
parsed MP criteria, the difficulty ladder, cheat liveness) and a write path
that checks archive-slot fit *before* touching anything. Cell parsing is
reused from `campaign.parse_csv` so there is exactly one CSV reader.

CONFIDENCE. Every fact this module reports carries one of three tags, and the
UI must not flatten them:

    "measured"      read off the disc or the executable; scripts/
                    verify_progression.py re-checks it on every run
    "established"   verified in an earlier pass over the executable and
                    restated here; this module corroborates but does not
                    re-derive
    "inferred"      reasoning from evidence, not directly observed

The unlock ladder is *inferred*. It comes from two message strings, not from
reading the code that grants the unlock. Say so.
"""

import zlib
from typing import Any

from . import campaign

SWAT = "SWAT.DAT"
LEVELFLOW = "levelflow.csv"
UNLOCKABLES = "unlockables.csv"

MEASURED = "measured"
ESTABLISHED = "established"
INFERRED = "inferred"


# --------------------------------------------------------------- levelflow

# The header this file is known to ship with, used only to *check* what we
# read. The parser always trusts the file's own first row -- hardcoding
# indices is how `campaign.LEVELFLOW_COLS` drifted (it names 16 columns as
# ..."BH1","BH2","BH Weapons" where the file says 18 columns ending
# ..."BHunter","BHunter Weapons","BHunter Health","BHunter Dead-Eye","Boss").
# A mismatch is reported, never silently corrected.
EXPECTED_LEVELFLOW_HEADER = [
    "Level Name", "Level Variation", "Player Character", "Weapons",
    "Reward ($)", "Max Time (sec)", "Max Damage Taken (hp lost)",
    "Min Accuracy (0 - 100)%", "Min Combo (total $)",
    "Normal", "Excellent", "Hidden", "Show Reward",
    "BHunter", "BHunter Weapons", "BHunter Health", "BHunter Dead-Eye",
    "Boss",
]

# Short machine keys so a UI does not have to quote "Min Accuracy (0 - 100)%".
LEVELFLOW_KEYS = [
    "level", "variation", "character", "weapons", "reward",
    "max_time", "max_damage", "min_accuracy", "min_combo",
    "reward_normal", "reward_excellent", "reward_hidden", "show_reward",
    "bhunter", "bhunter_weapons", "bhunter_health", "bhunter_deadeye", "boss",
]

# Columns that are semicolon-separated lists, not scalars.
LEVELFLOW_LIST_COLUMNS = {"weapons", "bhunter_weapons"}

# The two knobs this module is really about.
UNUSED_KNOBS = ("bhunter_health", "bhunter_deadeye")


def _cells(row):
    return [c.text for c in row]


def read_levelflow(game):
    """The 32 missions, every column named, plus what is odd about them.

    Returns a dict; `rows` is mission order, which is also the order the game
    plays them in -- `Level Name` repeats (lvl_tomb appears five times) and
    `Level Variation` is what tells the copies apart, so never key a mission
    by its level name alone.
    """
    data = _read(game, LEVELFLOW)
    return parse_levelflow(data)


def parse_levelflow(data):
    raw = campaign.parse_csv(data)
    if not raw:
        raise ValueError("levelflow.csv parsed to zero rows")

    header = _cells(raw[0])
    notes = []
    if header != EXPECTED_LEVELFLOW_HEADER:
        notes.append({
            "kind": "header-drift",
            "confidence": MEASURED,
            "detail": "levelflow.csv header is not the one this module was "
                      "written against; column meanings below follow the "
                      "FILE, not the constant.",
            "file_header": header,
            "expected_header": EXPECTED_LEVELFLOW_HEADER,
        })

    keys = list(LEVELFLOW_KEYS[:len(header)])
    # More columns than we have names for: keep them, addressable by index.
    for i in range(len(keys), len(header)):
        keys.append("col%d" % i)

    rows = []
    for n, row in enumerate(raw[1:]):
        if not row or not row[0].text:
            continue
        cells = _cells(row)
        m = {"index": n, "cells": cells, "columns": len(cells)}
        for i, key in enumerate(keys):
            v = cells[i] if i < len(cells) else ""
            m[key] = v
        for key in LEVELFLOW_LIST_COLUMNS:
            if key in m:
                m[key + "_list"] = [x for x in m[key].split(";") if x]
        m["bhunter_enabled"] = m.get("bhunter") == "1"
        rows.append(m)

    # Per-column census, so a caller can see "this column is constant" without
    # scanning 32 rows itself. That is how the two dead knobs announce
    # themselves.
    columns = []
    for i, key in enumerate(keys):
        vals = [r.get(key, "") for r in rows]
        distinct = sorted(set(vals))
        columns.append({
            "index": i,
            "key": key,
            "header": header[i] if i < len(header) else "",
            "distinct": len(distinct),
            "values": distinct if len(distinct) <= 8 else distinct[:8],
            "constant": distinct[0] if len(distinct) == 1 else None,
            "blank_rows": sum(1 for v in vals if v == ""),
        })

    by_key = {c["key"]: c for c in columns}
    knobs = []
    for key in UNUSED_KNOBS:
        col = by_key.get(key)
        if col is None:
            continue
        knobs.append({
            "key": key,
            "column": col["index"],
            "header": col["header"],
            "constant": col["constant"],
            "unused": col["constant"] == "0",
            "confidence": MEASURED,
            "note": "Zero on all %d rows. The column sits inside the BHunter "
                    "block the game reads, so it is wired up and never set. "
                    "Units and scale are NOT known -- nothing on the disc "
                    "demonstrates what a non-zero value does."
                    % len(rows),
            "units": {"value": "unknown", "confidence": INFERRED,
                      "guess": "a health / Dead-Eye scale or bonus applied "
                               "in Bounty Hunter mode; 0 most likely means "
                               "'use the normal value'"},
        })

    bh = [r for r in rows if r["bhunter_enabled"]]
    return {
        "file": LEVELFLOW,
        "archive": SWAT,
        "header": header,
        "keys": keys,
        "columns": columns,
        "rows": rows,
        "count": len(rows),
        "bhunter_count": len(bh),
        "bhunter_levels": [r["level"] for r in bh],
        "unused_knobs": knobs,
        "notes": notes,
    }


# ------------------------------------------------------------- unlockables

EXPECTED_UNLOCKABLES_HEADER = [
    "Name", '"Type (character, stage, cheat, journal)"', "Texture Name",
    "Default", "MP Unlock Criteria", "Mp Unlock Value", "???", "???", "Xbox",
]

UNLOCKABLES_KEYS = ["name", "type", "texture", "default", "criteria",
                    "value", "unknown1", "unknown2", "xbox"]

UNLOCKABLE_TYPES = ("journal", "character", "stage", "cheat")

# Every MP unlock criterion the executable will match, in the order they sit
# in the pointer table at 0x004F78A0 (28 entries, 0x004F78A0..0x004F790C, no
# terminator -- the array is bounded by count).
#
# The order is alphabetical only if you IGNORE underscores: `TIMES_KILLED`
# comes after `TIME_POISONED`, which plain strcmp would not do ('S' < '_').
# So this was sorted by a human or a tool, not by the comparison the lookup
# uses -- do not assume a binary search and do not assume you may reorder it.
#
# The CSV only ever uses 20 of these. The other 8 parse fine and are unused,
# which makes them free real estate in exactly the way BHunter Health is.
MP_CRITERIA = (
    "BACK_SHOTS", "BEST_COMBO", "CARDS_COLLECTED", "DISTANCE_TRAVELED",
    "DUELS_WON", "ENEMIES_KILLED", "FAVORITE_CHARACTER",
    "FAVORITE_POKER_RULES", "FAVORITE_WEAPON", "FOCUS_GAINED",
    "GAMES_PLAYED", "GATLING_SHOTS", "HATED_ENEMY", "HEAD_SHOTS",
    "MELEE_HITS", "MONEY_COLLECTED", "MONEY_SPENT", "NUM_SPEC_MOVES_USED",
    "PCT_JOURNAL_COMPLETE", "PEOPLE_POISONED", "PEOPLE_THROWN",
    "SHOTS_FIRED", "SHOTS_HIT", "TIME_FROZEN", "TIME_ON_FIRE",
    "TIME_PLAYING", "TIME_POISONED", "TIMES_KILLED",
)
MP_CRITERIA_TABLE_ADDR = 0x004F78A0

# "NONE" is what the CSV writes for "no criterion", and it is NOT one of the
# 28 names in the table. Treat it as the absence of a criterion, not as a
# criterion the parser knows -- an edit that spells it anything else is a
# lookup miss, and we cannot say from here what a miss does.
NO_CRITERION = "NONE"

# What the Value column means depends entirely on the criterion, and the disc
# does not label it. These are readings of the vanilla numbers, not decoded
# semantics -- hence INFERRED everywhere.
CRITERION_VALUE_KIND = {
    "FAVORITE_CHARACTER": "id",
    "FAVORITE_WEAPON": "id",
    "FAVORITE_POKER_RULES": "id",
    "HATED_ENEMY": "id",
    "TIME_PLAYING": "seconds",
    "TIME_ON_FIRE": "seconds",
    "TIME_POISONED": "seconds",
    "TIME_FROZEN": "seconds",
    "PCT_JOURNAL_COMPLETE": "percent",
}


def read_unlockables(game):
    return parse_unlockables(_read(game, UNLOCKABLES))


def parse_unlockables(data):
    """221 typed rows, grouped, with the MP criterion resolved.

    Row width is NOT uniform: 31 rows carry all 9 columns and 190 stop after
    column 5 (measured through this parser on the pristine disc; row_widths
    comes back {6, 9}), because the two dead `???` columns and `Xbox` were
    left off those lines entirely. Anything that addresses a cell by index has
    to cope with that, and `set_unlockable_field` refuses rather than
    inventing commas.
    """
    raw = campaign.parse_csv(data)
    if not raw:
        raise ValueError("unlockables.csv parsed to zero rows")

    header = _cells(raw[0])
    notes = []
    if header != EXPECTED_UNLOCKABLES_HEADER:
        notes.append({
            "kind": "header-drift", "confidence": MEASURED,
            "detail": "unlockables.csv header differs from the one this "
                      "module was written against.",
            "file_header": header,
            "expected_header": EXPECTED_UNLOCKABLES_HEADER,
        })

    rows = []
    for n, row in enumerate(raw[1:]):
        cells = _cells(row)
        if not cells or not cells[0]:
            continue
        r = {"index": n, "columns": len(cells), "cells": cells}
        for i, key in enumerate(UNLOCKABLES_KEYS):
            r[key] = cells[i] if i < len(cells) else ""
        r["unlocked_by_default"] = r["default"] == "1"
        crit = r["criteria"]
        r["criterion"] = None if crit in ("", NO_CRITERION) else crit
        r["criterion_known"] = crit in MP_CRITERIA or crit in ("", NO_CRITERION)
        r["value_kind"] = CRITERION_VALUE_KIND.get(crit, "count")
        r["value_kind_confidence"] = INFERRED
        try:
            r["value_number"] = int(r["value"]) if r["value"] else 0
        except ValueError:
            r["value_number"] = None
        rows.append(r)

    groups: dict[str, list[Any]] = {t: [] for t in UNLOCKABLE_TYPES}
    for r in rows:
        groups.setdefault(r["type"], []).append(r)

    used = sorted({r["criteria"] for r in rows
                   if r["criteria"] not in ("", NO_CRITERION)})
    unknown = sorted({c for c in used if c not in MP_CRITERIA})
    unused = [c for c in MP_CRITERIA if c not in used]

    # Both `???` columns are empty on every row that even has them, and the
    # executable has no token for either. Do not invent a meaning; report the
    # emptiness as the finding it is.
    dead_columns = []
    for key, idx in (("unknown1", 6), ("unknown2", 7)):
        present = [r for r in rows if r["columns"] > idx]
        dead_columns.append({
            "key": key, "column": idx, "header": "???",
            "rows_with_column": len(present),
            "rows_non_empty": sum(1 for r in present if r[key]),
            "confidence": ESTABLISHED,
            "note": "empty on every row and no parser token in the "
                    "executable -- dead, meaning unknown and unrecoverable "
                    "from this disc",
        })

    return {
        "file": UNLOCKABLES,
        "archive": SWAT,
        "header": header,
        "keys": UNLOCKABLES_KEYS,
        "rows": rows,
        "count": len(rows),
        "groups": {k: v for k, v in groups.items() if v},
        "type_counts": {k: len(v) for k, v in groups.items() if v},
        "unlocked_by_default": [r["name"] for r in rows
                                if r["unlocked_by_default"]],
        "row_widths": sorted({r["columns"] for r in rows}),
        "criteria": {
            "recognised_by_game": list(MP_CRITERIA),
            "table_address": MP_CRITERIA_TABLE_ADDR,
            "table_confidence": MEASURED,
            "used_in_file": used,
            "used_but_unrecognised": unknown,
            "recognised_but_unused": unused,
            "no_criterion_token": NO_CRITERION,
        },
        "dead_columns": dead_columns,
        "notes": notes,
    }


# ------------------------------------------------------------- difficulty

# Read from the executable: the menu builds `diff_%d` buttons and the bound
# check at 0x001231FC stops at 4. The four display names and the two unlock
# messages are string-table records, so `difficulty_info(game)` re-reads them
# rather than trusting these copies.
DIFFICULTY_IDS = ("diff_0", "diff_1", "diff_2", "diff_3")
DIFFICULTY_NAMES = ("Normal", "Hard", "Very Hard", "Red Wood Revolver")
DIFFICULTY_UNLOCK_IDS = ("diff_unlock_0", "diff_unlock_1")

MENU_BOUND_ADDR = 0x001231FC
PROFILE_BASE = 0x0073DE28
PROFILE_STRIDE = 2840
PROFILE_ACTIVE_INDEX_ADDR = 0x00742118
OFF_NUMGAMESCOMPLETE = 2060
OFF_MISSION_BITFIELD = 2064
OFF_UNLOCKED_DIFFICULTY = 2072
OFF_CHEAT_MASK = 2144

BHUNTER_FLAG_ADDR = 0x0079E21C
BHUNTER_MENU_CALLBACK = 0x0011DF40
BHUNTER_AI_CONDITION_ID = 252


def difficulty_info(game=None):
    """The four difficulties and the ladder that unlocks them.

    The ladder is the one thing here that is NOT read from code. It is
    reconstructed from the two `diff_unlock_*` messages, which say what gets
    unlocked but not what triggers it; `numgamescomplete` at profile+2060 is
    the obvious trigger and the message pairing matches it, but no one has
    read the granting code. Callers must render the ladder differently from
    the rest -- an inference presented as a fact is worse than no answer,
    because the developer would then edit against it.
    """
    names = list(DIFFICULTY_NAMES)
    messages = {}
    source = "module constants (executable pass)"
    if game is not None:
        from . import strtbl
        t = strtbl.parse(_read(game, "swat.strtbl"))
        got = []
        for sid in DIFFICULTY_IDS:
            _, rec = t.find(sid)
            got.append(rec.text if rec is not None else "")
        if all(got):
            names = got
            source = "swat.strtbl, read now"
        for sid in DIFFICULTY_UNLOCK_IDS:
            _, rec = t.find(sid)
            if rec is not None:
                messages[sid] = rec.text

    levels = []
    for i, (sid, name) in enumerate(zip(DIFFICULTY_IDS, names)):
        levels.append({
            "index": i,
            "id": sid,
            "name": name,
            "name_confidence": MEASURED if game is not None else ESTABLISHED,
            "name_source": source,
            "shipped_available": i == 0,
            "shipped_available_confidence": INFERRED,
        })

    ladder = [
        {"step": 0,
         "requires": "finish the game once (numgamescomplete >= 1)",
         "grants": ["diff_1", "bounty_hunter"],
         "evidence_string_id": "diff_unlock_0",
         "evidence_text": messages.get("diff_unlock_0"),
         "confidence": INFERRED,
         "why": "the message names both unlocks together; the trigger is "
                "read off numgamescomplete at profile+2060 by inference, "
                "not from the granting code"},
        {"step": 1,
         "requires": "finish the game twice (numgamescomplete >= 2)",
         "grants": ["diff_2", "diff_3"],
         "evidence_string_id": "diff_unlock_1",
         "evidence_text": messages.get("diff_unlock_1"),
         "confidence": INFERRED,
         "why": "same reasoning as step 0"},
    ]

    return {
        "levels": levels,
        "count": len(levels),
        "menu_bound": {
            "value": 4, "address": MENU_BOUND_ADDR, "confidence": ESTABLISHED,
            "note": "the menu builds exactly 4 difficulty buttons; the bound "
                    "check is at 0x%08X. Adding a fifth needs a code patch, "
                    "not a data edit." % MENU_BOUND_ADDR,
        },
        "ladder": ladder,
        "ladder_confidence": INFERRED,
        "save_fields": {
            "profile_base": PROFILE_BASE,
            "profile_stride": PROFILE_STRIDE,
            "active_index_addr": PROFILE_ACTIVE_INDEX_ADDR,
            "numgamescomplete": OFF_NUMGAMESCOMPLETE,
            "mission_bitfield": OFF_MISSION_BITFIELD,
            "unlocked_difficulty": OFF_UNLOCKED_DIFFICULTY,
            "cheat_mask": OFF_CHEAT_MASK,
            "confidence": ESTABLISHED,
        },
        "bounty_hunter": {
            "is_separate_difficulty": False,
            "confidence": ESTABLISHED,
            "note": "Bounty Hunter is a flag, not a fifth campaign: global "
                    "0x%08X, set by the menu callback at 0x%08X, visible to "
                    "AI scripts as condition id %d. Which missions it applies "
                    "to is data, in levelflow.csv column 13."
                    % (BHUNTER_FLAG_ADDR, BHUNTER_MENU_CALLBACK,
                       BHUNTER_AI_CONDITION_ID),
            "flag_address": BHUNTER_FLAG_ADDR,
            "menu_callback": BHUNTER_MENU_CALLBACK,
            "ai_condition_id": BHUNTER_AI_CONDITION_ID,
            "levelflow_column": 13,
        },
    }


# ----------------------------------------------------------------- cheats

# The cheat menu binds ten widgets to ten `cheat_*` name strings, unrolled at
# 0x001228D8..0x00122A20 (stride 0x24). That widget order is NOT the order the
# rows appear in unlockables.csv, and neither order is known to be the bit
# order in the save-file mask -- see CHEAT_MASK below.
CHEAT_MENU_ORDER = (
    "cheat_invulnerable", "cheat_infinitefocus", "cheat_goldengun",
    "cheat_sudden_death", "cheat_sad_stars", "cheat_no_hud",
    "cheat_no_distort", "cheat_showposition", "cheat_showaiinfo",
    "cheat_bigmoney",
)

# Display labels the executable carries for the two debug cheats. The other
# eight are named through the string table / combo widgets.
CHEAT_LABELS = {
    "cheat_showposition": "Show Player Position",
    "cheat_showaiinfo": "Show AI Info",
    "cheat_bigmoney": "Big Money",
}

DEAD_CHEATS = {
    "cheat_showaiinfo": "cheat-mask bit 8 has no consumer anywhere in the "
                        "executable, so toggling it changes nothing in the "
                        "retail build",
    "cheat_showposition": "cheat-mask bit 9 has no consumer anywhere in the "
                          "executable, so toggling it changes nothing in the "
                          "retail build",
}

# What this module could measure about the mask itself, independent of the
# name->bit question:
#
#   getter  0x0017E778(slot, bit) -> lw 2144(profile) ; srlv ; andi 1
#   setter  0x0017E8F8(slot, word) -> sw 2144(profile)
#   both take a multiplayer branch on the global at 0x0079E224, where the
#   mask instead lives at +68 of a 276-byte per-player struct at 0x007424F8.
#
# 25 call sites reach the getter. Resolving the bit argument at each (constant
# `addiu a1,zero,N` or `daddu a1,zero,zero`, scanning back to the first write
# of a1) gives 11 distinct constants: 0,3,5,6,7,10,11,12,13,14,15. Bits 1, 2,
# 4, 8 and 9 are never asked for.
#
# Eleven live bits against ten cheats means the word at +2144 is wider than
# the unlockables cheat list -- so DO NOT assume bit == CSV row index. The
# only bit->name claim here is the established showaiinfo/showposition one.
CHEAT_MASK = {
    "offset": OFF_CHEAT_MASK,
    "access_width": "32-bit lw/sw (the field is documented as 16-bit; the "
                    "code reads a full word)",
    "getter": 0x0017E778,
    "setter": 0x0017E8F8,
    "multiplayer_flag": 0x0079E224,
    "multiplayer_struct": 0x007424F8,
    "multiplayer_stride": 276,
    "multiplayer_offset": 68,
    "call_sites": 25,
    "bits_with_consumers": (0, 3, 5, 6, 7, 10, 11, 12, 13, 14, 15),
    "bits_without_consumers": (1, 2, 4, 8, 9),
    "confidence": MEASURED,
    "bit_to_name_mapping": None,
    "bit_to_name_confidence": "unresolved",
    "note": "The bit census is measured. The mapping from cheat NAME to bit "
            "index is not resolved: the menu widget order, the CSV row order "
            "and the bit order all differ, and no table on the disc joins "
            "them. Bits 8 and 9 being consumer-free is consistent with the "
            "established finding that showaiinfo and showposition are dead.",
}


def cheats(game):
    """The 10 cheats from unlockables.csv, each flagged live or dead."""
    un = parse_unlockables(_read(game, UNLOCKABLES))
    out = []
    for r in un["groups"].get("cheat", []):
        name = r["name"]
        dead = name in DEAD_CHEATS
        out.append({
            "name": name,
            "short": name[len("cheat_"):] if name.startswith("cheat_") else name,
            "label": CHEAT_LABELS.get(name),
            "csv_row": r["index"],
            "menu_order": (CHEAT_MENU_ORDER.index(name)
                           if name in CHEAT_MENU_ORDER else None),
            "texture": r["texture"],
            "default": r["default"],
            "unlocked_by_default": r["unlocked_by_default"],
            "criteria": r["criteria"],
            "value": r["value"],
            "live": not dead,
            "dead_reason": DEAD_CHEATS.get(name),
            "confidence": ESTABLISHED if dead else INFERRED,
            "confidence_note": (
                "dead: established by an earlier executable pass and "
                "corroborated here (bit is in bits_without_consumers)"
                if dead else
                "live: this cheat is not one of the two known-dead ones. "
                "Per-cheat bit numbers are NOT resolved, so 'live' means "
                "'not known dead', not 'consumer verified'"),
        })
    return {
        "cheats": out,
        "count": len(out),
        "live": sum(1 for c in out if c["live"]),
        "dead": sum(1 for c in out if not c["live"]),
        "mask": dict(CHEAT_MASK),
        "unlock_switch": {
            "column": "Default",
            "note": "Setting Default to 1 is what makes a cheat available; "
                    "in story mode cheats are otherwise Hidden rewards on "
                    "specific late missions, not purchasable.",
            "confidence": ESTABLISHED,
        },
    }


# ------------------------------------------------------------------ writes

class WriteRefused(Exception):
    """An edit that would corrupt the archive. Carries a human reason."""


# Every entry point takes `game`, which may be either a `Game` (an ISO) or a
# bare `Dave` for SWAT.DAT sitting on disk. The loose form is not a
# convenience: it is the only way to exercise the real write path without a
# writable 4 GB ISO, so the write path is testable at all.
def _arc(game):
    return game.archive(SWAT) if hasattr(game, "archive") else game


def _read(game, filename):
    return _arc(game).read(filename)


def _writable(game):
    if hasattr(game, "writable"):
        return game.writable
    return getattr(getattr(game, "backing", None), "writable", True)


def _entry(game, filename):
    return _arc(game).find(filename)


def plan(game, filename, new_data):
    """Will `new_data` fit the archive slot? Answer without writing anything.

    Both CSVs are stored DEFLATE-compressed in 2048-byte slots, so the limit
    is on the compressed payload, not on the text. That is worth being precise
    about: `unlockables.csv` is 8245 bytes of text in a 2048-byte slot. A rule
    of "the byte count must not grow" would refuse `0` -> `100`, which
    actually fits with room to spare. `dave.patch` recompresses at level 9 and
    raises SlotOverflow; this reproduces that arithmetic exactly so the UI can
    warn before the user commits.
    """
    e = _entry(game, filename)
    if e.compressed:
        co = zlib.compressobj(9, zlib.DEFLATED, -15)
        payload = co.compress(new_data) + co.flush()
        if len(payload) >= len(new_data):
            payload = new_data          # dave.patch stores raw in this case
    else:
        payload = new_data
    return {
        "file": filename,
        "archive": SWAT,
        "compressed": bool(e.compressed),
        "text_bytes": len(new_data),
        "payload_bytes": len(payload),
        "slot_bytes": e.slot,
        "headroom": e.slot - len(payload),
        "fits": len(payload) <= e.slot,
    }


def _apply_cell(data, rows, row_index, col_index, value, what):
    """Replace one cell's bytes. Everything else stays byte-identical."""
    r = row_index + 1                      # +1: skip the header row
    if not (0 <= r < len(rows)):
        raise WriteRefused("%s: row %d does not exist (%d data rows)"
                           % (what, row_index, len(rows) - 1))
    row = rows[r]
    if not (0 <= col_index < len(row)):
        # The alternative is appending commas, which changes the row's shape
        # and can only be right by luck. 159 rows of unlockables.csv genuinely
        # stop at column 5.
        raise WriteRefused(
            "%s: row %d has %d columns, so column %d is not present on that "
            "line. Refusing rather than adding commas -- the game reads "
            "these files positionally and a reshaped row is a corrupt row."
            % (what, row_index, len(row), col_index))
    text = str(value)
    if "," in text or '"' in text or "\n" in text or "\r" in text:
        raise WriteRefused(
            "%s: value %r contains a comma, quote or newline. This writer "
            "only does plain cells; quoting would change the row's byte "
            "layout in ways the game's parser has not been shown to accept."
            % (what, text))
    cell = row[col_index]
    return data[:cell.start] + text.encode("latin-1") + data[cell.end:]


def _write(game, filename, new_data, dry):
    p = plan(game, filename, new_data)
    if not p["fits"]:
        raise WriteRefused(
            "%s would not fit its archive slot: %d compressed bytes into a "
            "%d-byte slot (%d over). Shorten the value, or revert another "
            "edit in the same file."
            % (filename, p["payload_bytes"], p["slot_bytes"],
               p["payload_bytes"] - p["slot_bytes"]))
    if dry:
        p["written"] = False
        return p
    if not _writable(game):
        raise WriteRefused("this Game was opened read-only")
    used, slot = _arc(game).patch(filename, new_data)
    p["written"] = True
    p["used"] = used
    p["slot"] = slot
    return p


def set_mission_field(game, row_index, key, value, dry=True):
    """Change one levelflow.csv cell, addressed by its column key.

    `dry=True` by default: this returns a fit report and touches nothing.
    Pass dry=False (on a writable Game, over a copy of the ISO) to commit.
    """
    data = _read(game, LEVELFLOW)
    info = parse_levelflow(data)
    if key not in info["keys"]:
        raise WriteRefused("no levelflow column named %r; have %s"
                           % (key, ", ".join(info["keys"])))
    col = info["keys"].index(key)
    rows = campaign.parse_csv(data)
    new = _apply_cell(data, rows, row_index, col, value,
                      "levelflow.csv %s" % key)
    out = _write(game, LEVELFLOW, new, dry)
    out["row"] = row_index
    out["key"] = key
    out["column"] = col
    out["value"] = str(value)
    return out


def set_unlockable_field(game, row_index, key, value, dry=True):
    """Change one unlockables.csv cell, addressed by its column key."""
    if key not in UNLOCKABLES_KEYS:
        raise WriteRefused("no unlockables column named %r; have %s"
                           % (key, ", ".join(UNLOCKABLES_KEYS)))
    if key == "criteria":
        v = str(value)
        if v != NO_CRITERION and v not in MP_CRITERIA:
            raise WriteRefused(
                "%r is not one of the %d criteria the executable recognises "
                "(table at 0x%08X) and is not %s. A name the game cannot "
                "look up is a silent no-op at best."
                % (v, len(MP_CRITERIA), MP_CRITERIA_TABLE_ADDR, NO_CRITERION))
    data = _read(game, UNLOCKABLES)
    col = UNLOCKABLES_KEYS.index(key)
    rows = campaign.parse_csv(data)
    new = _apply_cell(data, rows, row_index, col, value,
                      "unlockables.csv %s" % key)
    out = _write(game, UNLOCKABLES, new, dry)
    out["row"] = row_index
    out["key"] = key
    out["column"] = col
    out["value"] = str(value)
    return out


def set_bhunter_knobs(game, row_index, health=None, deadeye=None, dry=True):
    """Set BHunter Health / BHunter Dead-Eye on one mission.

    The point of the module. Both are 0 on all 32 vanilla rows, so any
    non-zero value here is untested by definition -- the return carries
    `untested` to make the UI say so.
    """
    if health is None and deadeye is None:
        raise WriteRefused("nothing to set: pass health and/or deadeye")
    data = _read(game, LEVELFLOW)
    info = parse_levelflow(data)
    rows = campaign.parse_csv(data)
    new = data
    changed = {}
    # Highest column first so the earlier cell's span stays valid.
    for key, val in (("bhunter_deadeye", deadeye), ("bhunter_health", health)):
        if val is None:
            continue
        col = info["keys"].index(key)
        new = _apply_cell(new, campaign.parse_csv(new), row_index, col, val,
                          "levelflow.csv %s" % key)
        changed[key] = str(val)
    out = _write(game, LEVELFLOW, new, dry)
    out["row"] = row_index
    out["changed"] = changed
    out["untested"] = True
    out["warning"] = ("These two columns are zero on all 32 vanilla rows. "
                      "The game parses them, but no shipped data exercises "
                      "them, so the effect of a non-zero value is unknown "
                      "until you boot it. Boot tests must run past ~100s to "
                      "prove anything.")
    return out


def unlock_all_difficulties_note():
    """Why there is no `unlock_all_difficulties(game)` here.

    Difficulty availability is a save-file field (profile+2072), not disc
    data. Nothing in SWAT.DAT can pre-unlock Hard for a fresh profile -- that
    needs either a save editor or a code patch to the menu's gate. Returning
    the reason beats offering a button that silently does nothing.
    """
    return {
        "supported": False,
        "confidence": ESTABLISHED,
        "reason": "unlocked difficulty lives at profile+%d in the save file, "
                  "not in any file on the disc" % OFF_UNLOCKED_DIFFICULTY,
        "alternatives": [
            "edit the save (profile base 0x%08X, stride %d, active index at "
            "0x%08X)" % (PROFILE_BASE, PROFILE_STRIDE,
                         PROFILE_ACTIVE_INDEX_ADDR),
            "patch the menu gate near 0x%08X" % MENU_BOUND_ADDR,
        ],
    }


# ------------------------------------------------------------------ facade

def summary(game):
    """Everything a progression view needs, in one read."""
    lf = read_levelflow(game)
    un = read_unlockables(game)
    return {
        "levelflow": lf,
        "unlockables": un,
        "difficulty": difficulty_info(game),
        "cheats": cheats(game),
        "highlights": [
            {"title": "Two unused difficulty knobs",
             "confidence": MEASURED,
             "detail": "levelflow.csv columns 15 and 16 (BHunter Health, "
                       "BHunter Dead-Eye) are 0 on all %d missions. The game "
                       "reads them. Nobody set them."
                       % lf["count"]},
            {"title": "Eight unused unlock criteria",
             "confidence": MEASURED,
             "detail": "the executable recognises %d MP unlock criteria; "
                       "unlockables.csv uses %d. Unused: %s"
                       % (len(MP_CRITERIA),
                          len(un["criteria"]["used_in_file"]),
                          ", ".join(un["criteria"]["recognised_but_unused"]))},
            {"title": "Bounty Hunter is data, not a campaign",
             "confidence": ESTABLISHED,
             "detail": "%d of %d missions are whitelisted by column 13; the "
                       "mode itself is one global flag"
                       % (lf["bhunter_count"], lf["count"])},
        ],
    }
