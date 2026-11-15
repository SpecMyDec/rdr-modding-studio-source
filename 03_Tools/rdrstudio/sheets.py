"""What the disc's 1,022 `.csv` files ARE -- the layer above `csvfile.py`.

`csvfile` knows the grammar and nothing about Red Dead Revolver. This knows
which file is which, which level's music lives where, and how to turn `mjck`
into "Jack Swift". Same split as `props.py` / `cast.py`.

Measured on `01_Vanilla\\RDR_VANILLA.iso`:

  * 1,022 distinct names, 2,541 copies. SWAT.DAT holds ALL 1,022; 595 exist
    nowhere else. Every one of the 427 multi-copy files is byte-identical in
    all of its copies, which is what makes a row/column address safe to send
    to every archive at once.
  * 256 `snd_<num>.csv` and 81 `snd_cfg_lvl_*.csv` (plus `snd_cfg_default`).
  * `snd_cfg_lvl_<level>.csv` carries `SndBegin,<B>` with B a multiple of 100
    in 81 of 81 files. `snd_<B>` exists for 81 of 81 levels, `snd_<B+98>` for
    79, `snd_<B+99>` for 75. That is the level -> music link, and it is the
    only one: nothing in the .csv names the level, and nothing in the level
    names the .csv.
  * Levels share banks. `coach`, `coach2` and `coach4` all say SndBegin 1700,
    so editing "coach's music" edits three levels' music. The map says so.

**No .csv holds a line the player reads, but 186 of them hold the KEY to
one.** Measured: 14,266 distinct cell values across the disc, of which 356 are
identifiers in `swat.strtbl`, spread over 186 files -- and `invmasterlist.csv`
names the column itself, "Item Name Key (for string table)". So the sentence
"CSVs contain no user-visible text" is true and also misleading: nothing here
is displayed, but changing `Dynamite` in a shop's ItemName column changes
which string the game looks up, and a typo makes the lookup miss. Editing text
is `strtbl.py`'s job; this module never renames anything. `annotate` only
RESOLVES a code to the name the string table already gives it, so the roster
reads "Jack Swift" instead of "mjck".

The 518 cells that read as English sentences are all Comment columns in the
`snd_*` families -- the sound designer's notes, shipped on the disc.
"""

import re
from typing import Any

from . import csvfile

_SND_NUM = re.compile(r"^snd_(\d+)\.csv$")
_SND_CFG_LVL = re.compile(r"^snd_cfg_lvl_(.+)\.csv$")
_SND_BEGIN = re.compile(r"^SndBegin\s*,\s*(\d+)", re.M)

# The families worth naming, in the order a person would look for them. The
# predicate is a plain name test -- there is no manifest on the disc and no
# magic in the files, so this is the only classification available.
FAMILIES = [
    ("music", "Music",
     "One cue list per level, plus snd_99 -- the soundtrack that is always "
     "loaded. Rows are stream names, not filenames."),
    ("streams", "Streamed audio",
     "Cutscene and ambient streams for one level."),
    ("sfx", "Level sound effects",
     "The sample bank one level reserves."),
    ("sound_common", "Shared sound banks",
     "System, player, weapon and hit sounds used everywhere."),
    ("sound_cfg", "Sound setup",
     "Reverb, roll-off curve and ambient loop positions for one level. "
     "Not a single table -- several stacked."),
    ("shop", "Shops",
     "What a shop stocks, and how much of it."),
    ("roster", "Multiplayer roster",
     "Who is playable in multiplayer, their bounty and how they unlock."),
    ("progression", "Mission flow and rewards",
     "The campaign order, rewards and par times."),
    ("path", "Patrol paths",
     "Waypoints an AI walks."),
    ("ai", "AI tables",
     "Accuracy, fire rate, dodge and body-region odds."),
    ("pickup", "Pickups and power-ups",
     "What drops, and how often."),
    ("other", "Everything else", ""),
]
FAMILY_LABEL = {k: (lab, why) for k, lab, why in FAMILIES}

ROSTER_FILES = ("mpcharbounty.csv", "unlockables.csv")


def classify(name):
    """Which family a .csv belongs to, by name alone."""
    m = _SND_NUM.match(name)
    if m:
        num = m.group(1)
        # The suffix carries the meaning: <B>99 music, <B>98 streams, <B>00
        # the level's own effects. The suffix is checked BEFORE the
        # two-digit case, because `snd_98` and `snd_99` are not shared banks
        # -- both call themselves "Soundtrack streams, should always be
        # loaded" on their own first line.
        if num.endswith("99"):
            return "music"
        if num.endswith("98"):
            return "streams"
        # snd_00 system, snd_01 player, snd_03 weapons, snd_20 Kelly's voice.
        if len(num) <= 2:
            return "sound_common"
        return "sfx"
    if name.startswith("snd_cfg"):
        return "sound_cfg"
    if name in ROSTER_FILES:
        return "roster"
    if name.startswith("shop"):
        return "shop"
    if name in ("levelflow.csv", "expectedlvlclrtime.csv", "playerranking.csv",
                "invmasterlist.csv", "shitmasterlist.csv"):
        return "progression"
    if name.endswith("path.csv"):
        return "path"
    if "pupshow" in name or "powerup" in name or "powup" in name \
            or "itembox" in name or "poweruptable" in name:
        return "pickup"
    for tag in ("firetbl", "firetable", "accuracy", "acctbl", "bodyregion",
                "sneak", "dodge", "attacktbl", "detect", "search", "trigger",
                "shoot", "fire", "axn_tbl", "tbl"):
        if tag in name:
            return "ai"
    return "other"


# ------------------------------------------------------------------ catalogue

def catalogue(disc):
    """Every .csv on the disc with its family and its room to grow.

    Built from the TOC only -- no file is decompressed -- because this is the
    list the picker renders and it must be cheap enough to ask for on every
    visit to the tab.

    `headroom` is the TIGHTEST copy's, not SWAT.DAT's. A write goes to every
    archive that holds the file and is refused if any one of them overflows,
    so SWAT's figure is the wrong number to put on the row: measured, 75 of
    the 427 multi-copy files have a roomier SWAT copy than their worst one,
    and `snd_2900.csv` badged 2,019 spare bytes while the copy in its level
    archive had 4. `stored` and `slot` come from that same tightest copy so
    the three numbers describe one entry rather than two. `bytes` is the
    decompressed size, which is identical in every copy (measured on all
    427).
    """
    index = disc.build_index()
    swat = disc.archive("SWAT.DAT")
    rows = []
    for name in sorted(n for n in index if n.endswith(".csv")):
        try:
            e = swat.find(name)
        except Exception:
            continue
        tight, worst = e, e.headroom
        for arc in index[name]:
            if arc == "SWAT.DAT":
                continue
            try:
                other = disc.archive(arc).find(name)
            except Exception:
                continue
            if other.headroom < worst:
                tight, worst = other, other.headroom
        rows.append(dict(name=name, family=classify(name),
                         copies=len(index[name]),
                         bytes=e.size_full, stored=tight.size_comp,
                         slot=tight.slot, headroom=worst))
    fam: dict[str, int] = {}
    for r in rows:
        fam[r["family"]] = fam.get(r["family"], 0) + 1
    return dict(files=rows, total=len(rows),
                families=[dict(id=k, label=lab, why=why, count=fam.get(k, 0))
                          for k, lab, why in FAMILIES if fam.get(k)])


def copies(disc, name):
    """Per-archive slot facts for one .csv, worst headroom first."""
    out = []
    for arc in disc.locate(name):
        try:
            e = disc.archive(arc).find(name)
        except Exception:
            continue
        out.append(dict(archive=arc, stored=e.size_comp, slot=e.slot,
                        headroom=e.headroom, compressed=e.compressed))
    out.sort(key=lambda d: (d["headroom"], d["archive"]))
    return out


# --------------------------------------------------------------- the sound map

def sound_map(disc):
    """Which .csv holds each level's music, streams and effects.

    The chain is `snd_cfg_lvl_<level>.csv` -> `SndBegin,<B>` -> `snd_<B+99>`.
    Nothing else on the disc states it. `present` is checked rather than
    assumed because six levels have no music file at all.
    """
    index = disc.build_index()
    swat = disc.archive("SWAT.DAT")
    order = _campaign_order(swat)
    rows = []
    for name in sorted(n for n in index if _SND_CFG_LVL.match(n)):
        lvl = _SND_CFG_LVL.match(name)
        if lvl is None:             # unreachable: the filter above matched
            continue
        level = "lvl_" + lvl.group(1)
        try:
            m = _SND_BEGIN.search(swat.read(name).decode("latin-1"))
        except Exception:
            m = None
        base = int(m.group(1)) if m else None
        slots = {}
        for role, off in (("sfx", 0), ("streams", 98), ("music", 99)):
            fn = "snd_%d.csv" % (base + off) if base is not None else None
            slots[role] = dict(file=fn, present=bool(fn and fn in index),
                               copies=len(index.get(fn, [])) if fn else 0)
        rows.append(dict(level=level, config=name, begin=base,
                         archives=index.get(name, []),
                         order=order.get(level, 999), **slots))
    # Campaign order first, then the variants and the unused levels, so the
    # list opens on the missions someone actually plays.
    rows.sort(key=lambda r: (r["order"], r["level"]))
    shared: dict[Any, list[Any]] = {}
    for r in rows:
        if r["begin"] is not None:
            shared.setdefault(r["begin"], []).append(r["level"])
    for r in rows:
        r["shares_with"] = [lv for lv in shared.get(r["begin"], [])
                            if lv != r["level"]]
    return dict(levels=rows, total=len(rows))


def _campaign_order(swat):
    """lvl_name -> position in levelflow.csv, for sorting only."""
    try:
        sheet = csvfile.parse(swat.read("levelflow.csv"))
    except Exception:
        return {}
    out: dict[str, int] = {}
    for r in sheet.rows:
        if r.kind != "data" or not r.cells:
            continue
        key = r.cells[0].text.strip()
        if key and key not in out:
            out[key] = len(out)
    return out


# ------------------------------------------------------------------- the roster

def roster(disc):
    """The 48 multiplayer characters, with the name the game shows.

    The codes in `mpcharbounty.csv` and `unlockables.csv` carry a leading `m`
    that the string table does not: `mjck` -> `jck` -> "Jack Swift". Measured:
    48 of 48 resolve that way and 0 of 48 resolve with the `m` left on, so the
    rule is the disc's, not a guess.
    """
    swat = disc.archive("SWAT.DAT")
    names = _display_names(swat)
    bounty, unlock = {}, {}
    try:
        for r in csvfile.parse(swat.read("mpcharbounty.csv")).rows:
            if r.kind == "data" and len(r.cells) >= 2:
                bounty[r.cells[0].text.strip()] = r.cells[1].text.strip()
    except Exception:
        pass
    try:
        for r in csvfile.parse(swat.read("unlockables.csv")).rows:
            if r.kind != "data" or len(r.cells) < 6:
                continue
            if r.cells[1].text.strip() != "character":
                continue
            unlock[r.cells[0].text.strip()] = dict(
                default=r.cells[3].text.strip(),
                criteria=r.cells[4].text.strip(),
                value=r.cells[5].text.strip())
    except Exception:
        pass

    out = []
    for code in sorted(set(bounty) | set(unlock)):
        u = unlock.get(code, {})
        out.append(dict(code=code, name=names.get(_stem(code)),
                        bounty=bounty.get(code), **u))
    resolved = sum(1 for r in out if r["name"])
    return dict(characters=out, total=len(out), resolved=resolved)


def _stem(code):
    """`mjck` -> `jck`. The string-table identifier is the code minus its m."""
    return code[1:] if code.startswith("m") and len(code) > 1 else code


def _display_names(swat):
    from . import strtbl
    try:
        st = strtbl.parse(swat.read("swat.strtbl"))
    except Exception:
        return {}
    out = {}
    for key, rec in st.entries(0):
        if key and key not in out:
            # The table stores two-line names with a literal newline
            # ("Annie\nStoakes"); one line reads better beside a code.
            out[key] = rec.text.replace("\n", " ").replace("\r", " ").strip()
    return out


# ---------------------------------------------------------------- annotations

def annotate(disc, name, sheet):
    """Human-readable labels for rows whose first cell is a code, not a word.

    Only the multiplayer roster qualifies today. This deliberately does NOT
    invent labels for the AI tables: their first column is already the thing
    it says it is.
    """
    if name not in ROSTER_FILES:
        return None
    names = _display_names(disc.archive("SWAT.DAT"))
    labels = {}
    for r in sheet.rows:
        if r.kind != "data" or not r.cells:
            continue
        code = r.cells[0].text.strip()
        # unlockables.csv also holds cheats, journal entries and stages; only
        # the 48 four-letter `m` codes are characters.
        if len(code) != 4 or not code.startswith("m"):
            continue
        who = names.get(_stem(code))
        if who:
            labels[r.index] = who
    if not labels:
        return None
    return dict(column="Character", labels=labels,
                why="Resolved through swat.strtbl: the code minus its leading "
                    "'m' is the string-table identifier. 48 of 48 resolve.")
