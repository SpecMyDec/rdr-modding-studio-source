"""Browsing STREAMS.DAT -- the 8,361 `.stm` streams, 52% of the disc.

`stm.py` decodes one file. This module is about the other problem: making
8,361 of them findable. It answers three questions, in this order of
importance:

  1. What is in here?      -- `list_streams(game)`, header-only, no decoding.
  2. What is this one?     -- `parse_name`, plus the game's OWN captions.
  3. Can I hear it?        -- `to_wav(data)`, decoded on demand, one at a time.

WHY HEADER-ONLY MATTERS. `stm.decode` is pure Python at ~2 M samples/s and
the corpus is 2.497 billion samples, so decoding the lot to build a listing
takes about 19 minutes. Every field a listing needs (rate, channels,
duration, loop point) is in the first 2048 bytes, so `list_streams` reads
2048 bytes per file and nothing else: 17.1 MB, 1.35% of the archive.
Measured at 0.23-0.32 s for all 8,361 headers and 0.9-1.0 s including the
captions below -- about 1,000x faster than decoding, and the difference
between a browser that opens and one that hangs. Never make the listing call
`decode`.

THE CAPTIONS ARE ON THE DISC. This was the surprise. SWAT.DAT carries 256
`snd_<n>.csv` sound-bank definitions -- the same files the `%s.stm` /
`snd_cfg_` format strings at 0x521EF3 point at -- and they name every stream
they use, with a `Comment` column that the audio team filled in by hand. For
scripted dialogue that comment is frequently *the line itself*:

    badguy_bullseye_001b, ... ,The kids down by the river! Go get him!
    anniestokes_brim5_01, ... ,Annie Stokes
    Tomb_mainAMB1_PLII,   ... ,Main exterior ambience

and each CSV opens with its own title comment -- "Voice and unique sounds for
Mexcowboy 1", "Level Tomb Streams", "Music for Bridge". So the listing does
not have to guess what a stream is; for 7,366 of the 8,361 it can quote the
authors. That is what turns this from 8,361 opaque names into content.

MEASURED, against RDR_VANILLA.iso:

  * 8,361 `.stm`, all in STREAMS.DAT and nowhere else (every archive's TOC
    checked). 1.27 GB stored, all raw, all sector-aligned.
  * 8,007 mono / 354 stereo; 8,229 at 24 kHz / 132 at 48 kHz. Every one
    parses. 13.876 hours total.
  * By content: 5,482 barks, 1,993 scripted lines, 515 foley, 152 ambience,
    111 music, 91 cutscene beds, 17 that no rule fits.
  * Stereo is not an audio detail, it is the content axis: 209 of the 210
    `_plii` names are stereo, and stereo is where all the music and ambience
    is. Voice is mono without exception.
  * 179 files carry a non-zero loop point; 178 of them are stereo. Those are
    the looping beds.
  * 7,366 names appear in an `snd_*.csv`; 3 more appear elsewhere in
    SWAT.DAT; **992 appear nowhere on the disc at all**. See ORPHANS below.
  * 239 names that the CSVs mark `Stream,1` have no `.stm` on the disc --
    dangling references to cut recordings.

ORPHANS. 992 streams -- 11.9% of the corpus, and they include whole cutscene
music cues like `11_saloon_close1_plii` -- are not named by any sound bank,
any level, or the executable. Every one of SWAT.DAT's 47,115 entries was
scanned for their names, not just the CSVs. That is 111 minutes of finished
audio the shipped game never asks for. `list_streams` flags each one
(`referenced=False`) rather than hiding it.

**Unreferenced is not unreachable**, and this module used to say it was.
`scripts/probe_stream_lookup.py` disassembles the streamer: the engine builds
`"%s.stm"` and hands it to a case-insensitive bsearch over STREAMS.DAT's own
16-byte TOC records (`0x0027C6D4` -> `0x0048F1C0` -> `0x002CB908`). The key is
the NAME, and every one of the 8,361 has a TOC record carrying it -- the probe
runs that lookup for all 992 orphans and every one resolves to its own offset
and size. There is no manifest of playable streams to be absent from. So an
orphan is one nothing happens to name, and naming it in a sound bank is an
edit this toolkit already makes. See C-010.

Note the asymmetry with `referenced` in the listing, which is set from the
256 sound-bank CSVs alone (7,366 streams) because that is what a listing can
afford. The full scan finds 3 more. So `referenced=False` means "no sound
bank asks for this", which is the modding-relevant statement; "nothing at all
asks for this" costs 15 s and lives in scripts/verify_streams.py.

NAMING CONVENTION, worked out from the corpus and then checked against the
CSVs (scripts/verify_streams.py reports the coverage):

    <subject> _ [scene ...] _ <cue-or-scene><take><variant>

  * **subject** -- the first token, and the recording's identity: a voice set
    (`cowboy5`, `mexcowboy2`, `gd`, `uc`), a level (`tomb`, `barfight`), or a
    mission number and short name (`01_bull`, `12_traitor`).
  * **the last token** carries the take number, and its alphabetic stem is
    either a CUE from a closed vocabulary (`lginj`, `death`, `takingaim`,
    `reveallocation` -- 44 of them, each used by 5 or more subjects) or a
    SCENE (`brim1`, `saloon`, `stagecoach`, `traitor`). Cue means a combat
    bark; scene means a scripted line. That single test splits voice cleanly.
  * **variant** -- a trailing `a`/`b` is an alternate take of the same line,
    not a different line.
  * There is a third form with no take at all, ~100 files strong, where the
    line IS the filename: `gd_adios_americanos_a`, `curly_howdy_stranger`,
    `gd_do_not_fail_me_b`. Those words come back as `phrase`, and become the
    caption when no bank supplies one.

Two things about the convention are worth stating because they will look like
bugs otherwise:

  * The cue vocabulary is misspelled in the shipped data -- `attackmelle`,
    `attakmelee`, `freindattacked`, `surendering`, `movingtoplayer`. Nothing
    in the game reads these names (the CSV maps name to a numeric Code, and
    that is what the engine uses), so the typos were harmless and survived.
    They are folded together here, because a human browsing should not see
    three spellings of one cue.
  * A voice set is reused by several characters: `cowboy6_*` is listed by the
    Cowboy6, OWN_bartender AND OWN_barber banks. So "which character is this"
    has no single answer, and the group label is chosen by coverage and
    reported with its source (`label_source`) rather than asserted.

`_plii` is INFERRED to mean Dolby Pro Logic II. The name and the perfect
correlation with stereo are the whole case: the string "PLII" does not appear
in the executable in any casing, and neither does "Dolby", "Pro Logic" or
"Surround". Treat it as a naming habit that happens to mark the stereo files.

Usage:

    from rdrstudio import streams
    rows = streams.list_streams(game)                # ~1 s, all 8,361
    groups = streams.group_streams(rows)             # reads like a contents page
    wav = streams.wav_for(game, "cowboy5_lginj3.stm")
"""
import array
import collections
import csv
import io
import re
import struct
from typing import Any

from . import stm

ARCHIVE = "STREAMS.DAT"

#: The sound-bank definitions in SWAT.DAT. `snd_cfg_*.csv` are per-level
#: reverb/rolloff curves with no sample list, so they are excluded by the
#: `SAMPLES` header check rather than by name.
SND_CSV = re.compile(r"^snd_\d+\.csv$")


# ---------------------------------------------------------------- vocabulary

#: Cue stems, i.e. the AI/combat reactions a creature voices. Derived, not
#: invented: these are every alphabetic stem that appears as the last token of
#: a name across 5 or more distinct subjects. `verify_streams.py --derive`
#: re-derives the list from the disc and asserts it still matches.
#:
#: They are NOT the AI scripting vocabulary. That was worth checking, because
#: they read like it -- but of the 257 property names in the two arrays at
#: 0x004F9568 and 0x004F9BA8, exactly one ("reloading") also appears here, and
#: no bark stem occurs anywhere in the 4.5 MB executable. This is the audio
#: team's own naming, resolved by the CSVs, not by code.
CUES = {
    # taking damage / dying
    "beingshot": "being shot",
    "lginj": "badly hurt",
    "inj": "hurt",
    "maxinj": "badly hurt",
    "burning": "on fire",
    "death": "death",
    # `leg` is voice, not a footstep, despite sitting next to footmin/med/max
    # in every filename list. The banks settle it: `Mexcowboy1_leg1` occupies
    # request Code 10 in snd_108.csv, and in snd_177.csv Diego fills that same
    # Code 10 by reusing his own `gd_lginj*` recordings. A slot fed by injury
    # takes is an injury reaction.
    "leg": "leg hit",
    # fighting
    "attackgun": "attacking (gun)",
    "attackmelee": "attacking (melee)",
    "attack": "attacking",
    "takingaim": "taking aim",
    "aiming": "aiming",
    "reloading": "reloading",
    "specialmove": "special move",
    "specialtackle": "tackle",
    "striketaunt": "taunt after a hit",
    "killing": "killing someone",
    "killingplayer": "killing the player",
    "injureplayer": "hurting the player",
    "taunting": "taunting",
    "laugh": "laughing",
    "kiai": "attack shout",
    # noticing / searching
    "alerting": "raising the alarm",
    "seeing": "spotting the player",
    "hearing": "hearing something",
    "searching": "searching",
    "losetrack": "lost the player",
    "reveallocation": "calling out a position",
    "warning": "warning",
    "friendattacked": "ally attacked",
    "healthstatus": "calling out own health",
    # moving
    "movetoplayer": "closing in",
    "movefromplayer": "backing off",
    "fleeingplayer": "fleeing the player",
    "fleeingdanger": "fleeing danger",
    "evading": "evading",
    "runforcover": "running for cover",
    "usingcover": "in cover",
    "patrol": "on patrol",
    "idle": "idle",
    "task": "at work",
    "surrendering": "surrendering",
    # being disarmed
    "disarmgun": "disarmed",
    "disarmweapon": "disarmed",
    "disarmpistol": "pistol shot away",
    "disarmrifle": "rifle shot away",
}

#: Misspellings that occur in the shipped filenames. Harmless to the game --
#: nothing resolves a stream by parsing its name -- but they fragment a
#: listing, so they are folded into the canonical cue.
CUE_ALIASES = {
    "attackinggun": "attackgun",
    "attackingmelee": "attackmelee",
    "attackimelee": "attackmelee",
    "attackmelle": "attackmelee",
    "attakmelee": "attackmelee",
    "freindattacked": "friendattacked",
    "surendering": "surrendering",
    "disarmsurrendering": "surrendering",
    "lnginjury": "lginj",
    "lging": "lginj",
    "sminj": "inj",
    "movingtoplayer": "movetoplayer",
    "movingfromplayer": "movefromplayer",
}

#: Cues that are body noise rather than speech -- footsteps, animal calls.
#: Split out because a human hunting for dialogue does not want 448 footsteps
#: in the way.
FOLEY_CUES = {
    "footmin", "footmed", "footmax", "foot", "step", "run", "running",
    "walking", "jump", "takeoff", "breath", "grunt", "snort", "snorts", "moo",
    "whinney", "panic", "turn", "charges",
}

#: Tokens that mark a stereo file as music rather than ambience. `cdNtM` is a
#: production-music library reference (disc N, track M) and the CSVs keep
#: using it as the cue's name -- `ActionBattle_Music_CD19T2` is "Final wave".
MUSIC_TOKENS = ("music", "musicend", "endmusic", "bossmusic", "gatmusic",
                "endgatmusic", "frontendmusic", "themeend", "song", "piano",
                "pianointerupt")
CD_TRACK = re.compile(r"^cd(\d+)t(\d+)$")

#: Cutscene slots. Every mission-numbered `_plii` cue is one of these.
SCENE_SLOTS = ("open", "ingame", "close")

KINDS = ("music", "ambience", "cutscene", "dialogue", "bark", "foley", "other")


# -------------------------------------------------------------- name parsing

_TAKE = re.compile(r"^(?P<stem>[a-z]*?)(?P<num>\d*)(?P<var>[a-f]?)$")

#: A handful of names carry a second index on the take: `patrol1-01`,
#: `fleeingplayer2.1`. Dropped before the take is read, or the whole token
#: fails to parse and the file falls out of its own voice set's listing.
_EXTRA_INDEX = re.compile(r"[-.]\d+$")


def _split_take(token):
    """`lginj3` -> ("lginj", 3, ""); `01a` -> ("", 1, "a"); `brim5` -> ...

    Returns (stem, number-or-None, variant). The variant letter is only
    accepted when a number precedes it, so `barfight` does not become
    ("barfigh", None, "t").
    """
    token = _EXTRA_INDEX.sub("", token)
    m = _TAKE.match(token)
    if not m or (not m.group("stem") and not m.group("num")):
        return token, None, ""
    stem, num, var = m.group("stem"), m.group("num"), m.group("var")
    if var and not num:
        return token, None, ""
    return stem, (int(num) if num else None), var


def parse_name(name):
    """Break a stream name into subject / scene / cue / take.

    Pure string work -- no disc access, no header. Returns a dict; the fields
    it cannot determine are None rather than guessed.
    """
    base = name[:-4] if name.lower().endswith(".stm") else name
    base = base.lower()
    toks = base.split("_")

    variant = ""
    # A lone trailing letter is an alternate take of the previous token:
    # `gd_adios_americanos_a` / `_b`.
    if len(toks) > 1 and len(toks[-1]) == 1 and toks[-1] in "abcd":
        variant = toks.pop()

    plii = "plii" in toks
    if plii:
        toks = [t for t in toks if t != "plii"]
    if not toks:
        toks = [base]

    subject = toks[0]
    # A mission cue is numbered: `01_bull_close2_plii`. The number alone is a
    # useless subject, so it joins the name that follows it.
    if len(toks) > 1 and subject.isdigit():
        subject = "%s_%s" % (subject, toks[1])
        toks = toks[1:]

    cd = None
    for t in toks:
        m = CD_TRACK.match(t)
        if m:
            cd = "CD%s track %s" % (m.group(1), m.group(2))

    last = toks[-1] if len(toks) > 1 else ""
    stem, take, var2 = _split_take(last) if last else ("", None, "")
    variant = variant or var2

    # A single-token name like `pry03` still carries a take.
    if not last and len(toks) == 1:
        s, n, v = _split_take(toks[0])
        if n is not None and s:
            subject, take, variant = s, n, variant or v

    cue = CUE_ALIASES.get(stem, stem)
    is_cue = cue in CUES or cue in FOLEY_CUES

    # Middle tokens are the scene; if the last token's stem is not a cue it is
    # a scene name with the take fused onto it (`dtroops1_traitor01`).
    middle = toks[1:-1] if len(toks) > 2 else []
    scene_parts = list(middle)
    if last and not is_cue and stem:
        scene_parts.append(stem)
    scene = "_".join(scene_parts) or None

    # A third naming form, 111 files strong: the line IS the filename.
    # `gd_adios_americanos_a`, `curly_come_on_mister`, `gd_do_not_fail_me_b`.
    # No cue, no take number, just words -- so read them back as words.
    phrase = None
    if not is_cue and take is None:
        words = [t for t in toks[1:] if t.isalpha() and t != "plii"]
        if (len(words) >= 2 and sum(len(w) for w in words) >= 6
                and any(len(w) >= 3 for w in words)):
            phrase = " ".join(words)

    return dict(base=base, subject=subject, scene=scene,
                cue=cue if is_cue else None, take=take, variant=variant,
                plii=plii, cd_track=cd, phrase=phrase,
                cue_label=CUES.get(cue) if is_cue else None)


def classify(parsed, channels, loop_point):
    """Which of KINDS this is.

    Channel count leads, because it is measured rather than parsed: voice is
    mono on all 8,007 mono files and every one of the 354 stereo files is
    music, ambience or a cutscene bed.
    """
    toks = parsed["base"].split("_")
    if channels >= 2:
        if parsed["cd_track"] or any(t in MUSIC_TOKENS for t in toks):
            return "music"
        if any("amb" in t for t in toks) or loop_point:
            return "ambience"
        if any(any(s in t for s in SCENE_SLOTS) for t in toks):
            return "cutscene"
        return "ambience"
    cue = parsed["cue"]
    if cue in FOLEY_CUES:
        return "foley"
    if cue:
        return "bark"
    if parsed["take"] is not None or parsed["phrase"]:
        return "dialogue"
    return "other"


# --------------------------------------------------- the game's own captions

def _clean_title(text):
    return re.sub(r"\s+", " ", (text or "").lstrip("# ").strip()).rstrip(",")


def _parse_snd_csv(text, filename):
    """Rows of one `snd_NNN.csv`.

    Thirteen different column layouts ship on this disc -- `Delay`,
    `Platform`, `GroupDelay`, `MinVol` and `Key` come and go -- so columns are
    resolved by the `SAMPLES` header row's names, never by position. The
    first `#` line above that header is the file's title.
    """
    title = None
    cols = None
    out = []
    for row in csv.reader(io.StringIO(text)):
        if not row:
            continue
        head = row[0].strip()
        if cols is None:
            if head.startswith("#") and title is None:
                title = _clean_title(head)
            elif head == "SAMPLES":
                cols = {n.strip().lower(): i for i, n in enumerate(row)}
            continue
        if not head or head.startswith("#"):
            continue

        def get(key):
            i = cols.get(key)
            return row[i].strip() if i is not None and i < len(row) else ""

        out.append(dict(name=head.lower(), csv=filename, title=title,
                        code=get("code"), bank=get("vb"), volume=get("vol"),
                        stream=get("stream") == "1", loop=get("loop") == "1",
                        comment=get("comment")))
    return out


def caption_index(game):
    """name (no extension) -> list of sound-bank rows that mention it.

    Cached on the Game, because a stream browser asks for this on every
    listing and it costs ~0.5 s to build from 256 CSVs.
    """
    cached = getattr(game, "_stm_captions", None)
    if cached is not None:
        return cached
    index: dict[Any, list[Any]] = {}
    arc = game.archive("SWAT.DAT")
    for entry in arc:
        if not SND_CSV.match(entry.name):
            continue
        try:
            # latin-1, not utf-8: the comments contain Windows-1252 curly
            # apostrophes and a strict decode throws on them.
            text = arc.read(entry).decode("latin-1")
        except Exception:
            continue
        for row in _parse_snd_csv(text, entry.name):
            index.setdefault(row["name"], []).append(row)
    try:
        game._stm_captions = index
    except AttributeError:      # pragma: no cover - Game has no __slots__
        pass
    return index


def clear_caption_cache(game):
    """Drop the cached index -- call after patching any `snd_*.csv`."""
    if hasattr(game, "_stm_captions"):
        del game._stm_captions


def _best_caption(rows):
    """The most useful `Comment` among a stream's sound-bank rows.

    Longest wins. Comments range from "" through a speaker name ("Jack
    Swift") to the spoken line, and the line is always the longest, so this
    picks the transcript when one exists without needing to recognise it.
    """
    best = ""
    for r in rows:
        c = r["comment"].strip()
        if len(c) > len(best):
            best = c
    return best


# ----------------------------------------------------------------- the listing

def _headers(dave, progress=None):
    """offset-ordered 2048-byte reads -> {entry: header dict}.

    Ordered by offset because a cold page cache reads a 1.21 GB archive far
    better sequentially, and deduplicated by offset because a Dave archive
    stores identical files once and several TOC entries can share bytes.
    """
    entries = [e for e in dave if e.ext == "stm"]
    by_offset: dict[Any, Any] = {}
    out = {}
    total = len(entries)
    for n, e in enumerate(sorted(entries, key=lambda e: e.offset)):
        head = by_offset.get(e.offset)
        if head is None:
            raw = dave.backing.read_at(e.offset, stm.HEADER_SIZE)
            try:
                head = stm.parse_header(raw)
            except ValueError as exc:
                head = {"error": str(exc)}
            by_offset[e.offset] = head
        out[e.name] = head
        if progress and (n % 512 == 0 or n + 1 == total):
            progress(n + 1, total, e.name)
    return out


def list_streams(game, captions=True, progress=None):
    """Every `.stm` in STREAMS.DAT, with everything a listing needs.

    Reads 2048 bytes per file and decodes nothing. `seconds` comes from
    `data_size / channels / rate` in the header -- see the module docstring
    for why that is not negotiable.

    `captions=False` skips the SWAT.DAT cross-reference (saves ~0.25 s and
    the `caption` / `referenced` / `used_by` fields go empty).
    """
    dave = game.archive(ARCHIVE)
    heads = _headers(dave, progress=progress)
    index = caption_index(game) if captions else {}

    rows = []
    for entry in dave:
        if entry.ext != "stm":
            continue
        h = heads[entry.name]
        p = parse_name(entry.name)
        row = dict(
            name=entry.name, stored_bytes=entry.size_comp, offset=entry.offset,
            subject=p["subject"], scene=p["scene"], cue=p["cue"],
            cue_label=p["cue_label"], take=p["take"], variant=p["variant"],
            plii=p["plii"], cd_track=p["cd_track"], phrase=p["phrase"],
            caption="", caption_source=None,
            referenced=False, used_by=[], banks=[], codes=[],
        )
        if "error" in h:
            row.update(ok=False, error=h["error"], kind="other",
                       rate=None, channels=None, seconds=0.0, samples=0,
                       loop_point=0, looping=False, format=None)
            rows.append(row)
            continue

        nch = h["channels"] or 1
        samples = h["data_size"] * 2 // nch
        row.update(ok=True, error=None, format=h["format"], rate=h["rate"],
                   channels=nch, samples=samples,
                   seconds=samples / float(h["rate"]) if h["rate"] else 0.0,
                   loop_point=h["loop_point"], looping=bool(h["loop_point"]))
        row["kind"] = classify(p, nch, h["loop_point"])

        refs = index.get(p["base"], ())
        if refs:
            row["referenced"] = True
            row["caption"] = _best_caption(refs)
            row["used_by"] = sorted({r["title"] for r in refs if r["title"]})
            row["banks"] = sorted({r["bank"] for r in refs if r["bank"]})
            row["codes"] = sorted({r["code"] for r in refs if r["code"]})
        if row["caption"]:
            row["caption_source"] = "bank"
        elif p["phrase"] and row["kind"] == "dialogue":
            # Second-best, and labelled as such: the filename's own words.
            # Only for spoken lines -- `bridge_wtrlvl_wr1_amb_plii` also
            # parses as "words", and "wtrlvl amb" is not a caption.
            row["caption"] = p["phrase"]
            row["caption_source"] = "filename"
        rows.append(row)

    rows.sort(key=lambda r: r["name"])
    return rows


def read_header(game, name):
    """One stream's header fields, without reading its body."""
    dave = game.archive(ARCHIVE)
    entry = dave.find(name if name.lower().endswith(".stm") else name + ".stm")
    return stm.parse_header(dave.backing.read_at(entry.offset, stm.HEADER_SIZE))


# ------------------------------------------------------------------ grouping

#: A bank has to account for at least this much of a subject's streams before
#: its title is used as the subject's name. Below it the raw token is kept: a
#: wrong name is worse than no name, and music cues in particular are loaded
#: by a dozen level banks that have nothing to do with what they are.
LABEL_MIN_COVERAGE = 0.25

VOICE_TITLE = "voice and unique sounds for"


def _norm(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _subject_labels(rows):
    """subject -> (bank title, streams that bank lists, streams in total).

    Three tiers, each a weaker claim than the one above, and each held to a
    correspondingly higher bar:

      1. a bank whose title contains the subject token. Accepted however few
         streams it accounts for -- the token match IS the evidence.
         `cowboy6` picks "...for Cowboy6" over "...for OWN_bartender", which
         lists nearly the same files because that voice set is reused for the
         bartender.
      2. failing that, a character bank ("Voice and unique sounds for X"),
         if it covers LABEL_MIN_COVERAGE of the subject. This is what turns
         `gd` into Diego, `uc` into Ugly Chris and `dtroops1` into Diego
         Troops 1, none of which match by token.
      3. failing that, any bank -- but only if it is the ONLY bank that lists
         the subject. Without that condition `brimstone` comes out labelled
         "Tomb", because the Brimstone music cues are loaded by the Tomb
         level and eleven others; being loaded somewhere is not being named.

    Whichever tier answers, the caller gets the count behind it and can see
    how thin the evidence is.
    """
    counts: collections.defaultdict[Any, Any] = collections.defaultdict(
        lambda: collections.defaultdict(set))
    total: collections.Counter[Any] = collections.Counter()
    for r in rows:
        total[r["subject"]] += 1
        for title in r["used_by"]:
            counts[r["subject"]][title].add(r["name"])

    out = {}
    for subject, per_title in counts.items():
        token = _norm(subject)
        by_token = [t for t in per_title if token and token in _norm(t)]
        by_voice = [t for t in per_title if t.lower().startswith(VOICE_TITLE)]
        floor = LABEL_MIN_COVERAGE * total[subject]

        if by_token:
            pool = by_token
        elif by_voice:
            pool = by_voice
        elif len(per_title) == 1:
            pool = list(per_title)
        else:
            continue
        title = max(pool, key=lambda t: (len(per_title[t]), -len(t)))
        n = len(per_title[title])
        if not by_token and n < floor:
            continue
        out[subject] = (title, n, total[subject])
    return out


#: Prefixes and suffixes the audio team put on their own bank titles. Stripped
#: for display only -- `label_source` always shows the title verbatim.
_TITLE_PREFIXES = ("Voice and unique sounds for", "Music for",
                   "specific objects sounds, for", "specific objects, for",
                   "Level")
_TITLE_SUFFIXES = (re.compile(r"(?i)\s*(ambient\s+)?streams?$"),
                   re.compile(r"(?i)\s*specific sound effects\.?$"),
                   re.compile(r"(?i)^lvl_"))


def _pretty_subject(subject, label):
    if not label:
        return subject
    text = label
    for prefix in _TITLE_PREFIXES:
        if text.lower().startswith(prefix.lower()):
            text = text[len(prefix):].strip()
            break
    for pat in _TITLE_SUFFIXES:
        text = pat.sub("", text).strip()
    return text.strip(" .,") or subject


def _section_name(row):
    """The heading a stream belongs under inside its subject's group.

    Music is deliberately NOT split by its `cdNtM` token: a library reference
    is a different track every time, so using it as a heading gives one
    section per file and no grouping at all.
    """
    if row["cue"]:
        return row["cue_label"] or row["cue"]
    if row["kind"] in ("music", "ambience", "cutscene"):
        parts = [t for t in (row["scene"] or "").split("_")
                 if t and not CD_TRACK.match(t) and t not in MUSIC_TOKENS]
        return "_".join(parts) or row["kind"]
    return row["scene"] or row["kind"]


def group_streams(rows, min_group=1):
    """Rows -> a contents page: one group per subject, sections inside it.

    Groups are ordered by total playing time, so the listing opens on the
    voice sets and levels that actually carry the game rather than on
    whichever name sorts first. Each group carries:

        key          the subject token, e.g. "mexcowboy2", "01_bull"
        label        the game's own name for it where one could be pinned
        label_source which snd_*.csv that came from, and its coverage
        sections     [{name, kind, streams:[row, ...]}], cue or scene named

    `sections` is the second axis a human wants: within one speaker, "badly
    hurt" and "Brimstone, scene 1" are different things to look through.
    """
    labels = _subject_labels(rows)
    buckets = collections.defaultdict(list)
    for r in rows:
        buckets[r["subject"]].append(r)

    groups = []
    for subject, items in buckets.items():
        if len(items) < min_group:
            continue
        label, n, total = labels.get(subject, (None, 0, len(items)))
        sections = collections.defaultdict(list)
        for r in items:
            sections[(r["kind"], _section_name(r))].append(r)
        groups.append(dict(
            key=subject,
            label=_pretty_subject(subject, label),
            label_source=("%s (%d of %d streams)" % (label, n, total)
                          if label else None),
            count=len(items),
            seconds=sum(r["seconds"] for r in items),
            kinds=sorted({r["kind"] for r in items}),
            unreferenced=sum(1 for r in items if not r["referenced"]),
            sections=[dict(name=name, kind=kind,
                           count=len(v), seconds=sum(x["seconds"] for x in v),
                           streams=sorted(v, key=lambda x: x["name"]))
                      for (kind, name), v in
                      sorted(sections.items(),
                             key=lambda kv: (-len(kv[1]), kv[0][1]))],
        ))
    groups.sort(key=lambda g: (-g["seconds"], g["key"]))
    return groups


def summary(rows):
    """Counts a listing header can show without walking the rows again."""
    ok = [r for r in rows if r["ok"]]
    return dict(
        total=len(rows),
        failed=len(rows) - len(ok),
        hours=sum(r["seconds"] for r in ok) / 3600.0,
        stored_bytes=sum(r["stored_bytes"] for r in rows),
        by_kind=dict(collections.Counter(r["kind"] for r in rows)),
        by_rate=dict(collections.Counter(r["rate"] for r in ok)),
        by_channels=dict(collections.Counter(r["channels"] for r in ok)),
        looping=sum(1 for r in ok if r["looping"]),
        referenced=sum(1 for r in rows if r["referenced"]),
        unreferenced=sum(1 for r in rows if not r["referenced"]),
        subjects=len({r["subject"] for r in rows}),
    )


def search(rows, text):
    """Substring match over name, caption, scene and cue label.

    The caption is included because it is the only field that holds the
    spoken words -- "river" finds `badguy_bullseye_001b`, whose name says
    nothing about a river.
    """
    q = text.lower().strip()
    if not q:
        return list(rows)
    out = []
    for r in rows:
        hay = " ".join(filter(None, (
            r["name"], r["caption"], r["scene"] or "", r["cue_label"] or "",
            r["subject"], " ".join(r["used_by"]))))
        if q in hay.lower():
            out.append(r)
    return out


# --------------------------------------------------------------------- audio

def to_wav(data):
    """One `.stm` -> 16-bit PCM WAV bytes. Decodes; call it per file, on demand.

    Roughly 0.6 s per 10 seconds of mono 24 kHz audio in pure Python, which
    is fine for previewing one stream and ruinous for previewing a list.
    """
    rate, nch, chans = stm.decode(data)
    n = min(len(c) for c in chans)
    if nch == 1:
        pcm = chans[0][:n].tobytes()
    else:
        inter = array.array("h", bytes(2 * n * nch))
        for c, ch in enumerate(chans):
            inter[c::nch] = ch[:n]
        pcm = inter.tobytes()
    head = (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt "
            + struct.pack("<IHHIIHH", 16, 1, nch, rate,
                          rate * nch * 2, nch * 2, 16)
            + b"data" + struct.pack("<I", len(pcm)))
    return head + pcm


def wav_for(game, name):
    """Read one stream out of the disc and hand back WAV bytes."""
    if not name.lower().endswith(".stm"):
        name += ".stm"
    return to_wav(game.read(ARCHIVE, name))
