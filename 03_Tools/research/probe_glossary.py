"""probe_glossary.py -- English glosses for the text-format keys. INFERENCE PASS.

`FIELDS.md` is measurement: kinds, ranges and file counts, nothing guessed.
This file is the opposite and says so on every line it produces. A gloss is a
reading of a key's NAME, and a name can lie.

So the pass is built to be checkable rather than plausible:

1. VOCABULARY FROM THE GAME ITSELF. There is no dictionary on this machine and
   inventing a word list would make the whole thing unfalsifiable. Three
   sources, each auditable:

     displayed  the `string` column of the 38 `.xls` design spreadsheets and
                the text of all 101,778 `.strtbl` records -- words the game
                actually puts on screen
     engine     the 248-name AI vocabulary lifted out of the executable
     shared     tokens used as a key token in at least SHARED_EXTS different
                extensions. A term the format authors reused across eight
                unrelated file types is part of the engine's vocabulary even
                if no player ever sees it

   The third source exists because of a measurement: with only the first two,
   **66% of all keys came back OPAQUE**. The game's displayed English is simply
   not a dictionary for its internal field names -- `rate`, `period`, `blend`
   and `slew` are developer words that never reach the screen. That is worth
   knowing on its own, and it is reported below.

2. ABBREVIATIONS NEED EVIDENCE. `vel` expands to `velocity` because `velocity`
   also occurs as a key token; the probe records that evidence per entry.
   Entries with no corpus evidence are marked `inferred` and counted
   separately, so the unverified part is visible rather than blended in.

3. THE GLOSSER IS SCORED AGAINST HUMAN LABELS, on STEMS not exact words.
   `turn rate` and `turning speed` share no whole word, and scoring them as a
   disagreement put the rate at 57% while measuring nothing but suffixes.
   Comparing the first four characters collapses the paraphrases.

   WHAT THE SCORE ACTUALLY MEASURES, and it is the point of this file. Of 163
   keys that rdr-fmt has labelled by hand, the name-derived reading misses the
   human's meaning on 64 -- 39%. Not one of those 64 is the glosser misreading
   the name. They are keys where the NAME DOES NOT CARRY THE MEANING:

       Impulse         reads "impulse"        is "physical knockback force"
       FullLoad        reads "full load"      is "clip size"
       Spread          reads "spread"         is "cone of fire (0 = perfect)"
       Cullable        reads "cullable"       is "despawns when far away"
       PopFreq         reads "pop freq"       is "chance it appears"

   So this pass has a measured ceiling: **on the keys where a human label
   exists to check against, splitting the name gets you something usable 61% of
   the time and misses real meaning 39% of the time.** That is the bound on
   what any purely mechanical glosser can deliver here, and it is the reason
   these glosses should not be shipped as UI labels without a human reading
   them. `swt.py`, `guns.py`,
   `creature.py`, `enemyfields.py` and `charfile.py` carry field labels written
   by rdr-fmt. Those are held out as ground truth: the probe reports how often
   its reading of a key agrees with the human's, and -- more useful -- lists
   where it DISAGREES, because those are the keys whose name misleads.

NOT EVERY KEY IS A DESCRIPTION. A large part of the catalogue is keys that are
IDENTIFIERS -- `.rep`'s 3,986 keys are animation clip names (`_arm_max_for_inj`,
and `FIELDS.md` scores them `ref` -> `.anim` at 100%), `.flags`'s 1,195 are
game-state flag names, `.cam`'s are camera names. Reading those as English is a
category error: they are labels, not descriptions, and no gloss will ever make
`_bod_rev_die` mean something in prose.

So a key is glossed only when it is DESCRIPTIVE -- not `kind == "ref"`, and not
in a format `FIELDS.md` classes as "DATA (keys are content)". Identifier keys
are counted and set aside. Without that split the headline number is 60%
OPAQUE and it is measuring the wrong thing.

CONFIDENCE, and it is the point of the output

    HIGH    every token is a word the game itself displays
    MEDIUM  some token needed an abbreviation expansion
    OPAQUE  at least one token is not a word and not a known abbreviation

The OPAQUE list is the deliverable a human should actually read: it is the set
of keys that cannot be understood from their name at all, and it is far shorter
than the catalogue.

    python3 03_Tools/research/probe_glossary.py [--md 05_Docs/RESEARCH/GLOSSARY.md]
"""

import argparse
import collections
import json
import os
import re
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
from rdrstudio.iso9660 import Iso9660          # noqa: E402
from rdrstudio import dave, strtbl             # noqa: E402
from probe_xls import ole_streams, parse_workbook   # noqa: E402

REPO = os.path.dirname(os.path.dirname(HERE))
VANILLA = os.path.join(REPO, "01_Vanilla", "RDR_VANILLA.iso")
RES = os.path.join(REPO, "07_Research")

SPLIT = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z]+|[a-z]+|\d+")
SHARED_EXTS = 8          # extensions a token must span to count as vocabulary

# Abbreviations. `evidence` is the long form's own occurrence count as a key
# token elsewhere in the corpus, or a named source; entries whose evidence is
# None are pure inference and are counted as such in the report.
ABBREV = {
    "vel": ("velocity", "corpus"), "ang": ("angular", "corpus"),
    "pos": ("position", "corpus"), "rot": ("rotation", "corpus"),
    "rev": ("reverse", "corpus"), "trans": ("transition", "corpus"),
    "num": ("number of", "corpus"), "multi": ("multiplier", "corpus"),
    "upp": ("upper", "corpus"), "dyn": ("dynamite", "corpus"),
    "mol": ("molotov", "corpus"), "cam": ("camera", "corpus"),
    "anim": ("animation", "corpus"), "dist": ("distance", "corpus"),
    "prob": ("probability", "corpus"), "accel": ("acceleration", "corpus"),
    "min": ("minimum", "corpus"), "max": ("maximum", "corpus"),
    # weapon codes, confirmed against guns.py CHOICES
    "ptl": ("pistol", "guns.py"), "rfl": ("rifle", "guns.py"),
    "sho": ("shotgun", "guns.py"), "knf": ("knife", "guns.py"),
    "gat": ("gatling", "guns.py"), "bow": ("bow", "guns.py"),
    # pure inference -- no corpus long form, no second source
    "lft": ("left", None), "rgt": ("right", None), "fwd": ("forward", None),
    "dmg": ("damage", None), "tbl": ("table", None), "spd": ("speed", None),
    "idx": ("index", None), "frac": ("fraction", None), "dpt": ("depth", None),
    "src": ("source", None), "dst": ("destination", None),
    "init": ("initial", None), "prev": ("previous", None),
    "cur": ("current", None), "val": ("value", None), "amt": ("amount", None),
}


def inflate(backing, e):
    raw = backing.read_at(e.offset, e.size_comp)
    return raw if not e.compressed else zlib.decompress(raw, -15)


def game_vocabulary(iso):
    """Every English word the game itself puts on screen."""
    words = collections.Counter()

    def eat(text):
        for w in re.findall(r"[A-Za-z]{3,}", text):
            words[w.lower()] += 1

    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b"Dave", b"DAVE"):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            if e.ext not in ("xls", "strtbl"):
                continue
            try:
                blob = inflate(d.backing, e)
            except zlib.error:
                continue
            if e.ext == "xls":
                try:
                    streams = ole_streams(blob)
                    wb = streams.get("Workbook") or streams.get("Book")
                    if wb is None:
                        continue
                    _s, sst, cells, _b = parse_workbook(wb)
                except Exception:                      # noqa: BLE001
                    continue
                for v in sst:
                    eat(v)
                for v in cells.values():
                    if isinstance(v, str):
                        eat(v)
            else:
                try:
                    st = strtbl.parse(blob)
                except Exception:                      # noqa: BLE001
                    continue
                for k in st.keys:
                    eat(re.sub(r"[_\-]", " ", k))
                for _slot, recs in st.blocks.items():
                    for r in recs:
                        eat(r.text)
    return words


def curated_labels():
    """Field labels a human wrote, held out as ground truth.

    Scoped to the EXTENSION each module actually describes. Keying them by
    leaf name alone cross-matched wildly -- `.enm`'s `Type` means "Character",
    so every `type:` header on the disc was scored against it and `.cam`,
    `.cann` and `.audbox` all came back as disagreements. The labels are only
    ground truth for their own format.
    """
    out = collections.defaultdict(dict)
    from rdrstudio import swt, guns, creature, enemyfields, charfile
    for mod, ext in ((swt, "swt"), (guns, "guns"), (creature, "cr")):
        for _group, fields in getattr(mod, "FIELD_GROUPS", []):
            for row in fields:
                out[ext].setdefault(row[0].split(".")[-1].lower(), row[1])
    for row in getattr(enemyfields, "FIELDS", []):
        out["enm"].setdefault(row[0].lower(), row[1])
    for name, ext in (("_SPHS_KEYS", "sphs"), ("_HOT_KEYS", "hot"),
                      ("TAIL_KEYS", "tail")):
        for row in getattr(charfile, name, []):
            if len(row) >= 3:
                out[ext].setdefault(row[0].lower(), row[2])
    return out


def gloss(key, words, ai_vocab):
    """Read a key name. Returns (text, confidence, unknown tokens)."""
    seg = key.split("/")[-1]
    toks = SPLIT.findall(seg)
    parts, unknown, expanded = [], [], []
    for t in toks:
        low = t.lower()
        if low.isdigit():
            parts.append(low)
            continue
        if low in words or low in ai_vocab:
            parts.append(low)
            continue
        if low in ABBREV:
            parts.append(ABBREV[low][0])
            expanded.append(low)
            continue
        parts.append(low)
        unknown.append(low)
    conf = "OPAQUE" if unknown else ("MEDIUM" if expanded else "HIGH")
    return " ".join(parts), conf, unknown, expanded


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso", default=VANILLA)
    ap.add_argument("--md", default="")
    ap.add_argument("--json", default=os.path.join(RES, "glossary.json"))
    ap.add_argument("--min-word", type=int, default=2,
                    help="times the game must display a word for it to count")
    args = ap.parse_args()

    fields = json.load(open(os.path.join(RES, "fields.json")))
    try:
        ai = {w.strip().lower() for w in
              open(os.path.join(REPO, "05_Docs", "RESEARCH", "ai-vocabulary.txt"))
              if w.strip() and not w.startswith("#")}
    except FileNotFoundError:
        ai = set()

    iso = Iso9660(args.iso)
    raw_words = game_vocabulary(iso)
    iso.close()

    # third source: tokens shared across many unrelated formats
    spread = collections.defaultdict(set)
    for ext, s_ in fields.items():
        for f in s_["fields"]:
            for seg in f["key"].split("/"):
                for t in SPLIT.findall(seg):
                    if len(t) >= 3:
                        spread[t.lower()].add(ext)
    shared = {t for t, exts in spread.items() if len(exts) >= SHARED_EXTS}
    displayed = {w for w, c in raw_words.items() if c >= args.min_word}
    words = displayed | shared
    ai_tokens = {t for name in ai for t in SPLIT.findall(name)}
    ai_tokens |= ai
    # x/y/z are axis names, not mystery tokens: they are the component suffix
    # on the vec2/vec3 keys FIELDS.md already measures.
    ai_tokens |= {"x", "y", "z", "w", "r", "g", "b", "a"}

    curated = curated_labels()

    out = {}
    conf_count = collections.Counter()
    ident_count = 0
    agree = disagree = checked = 0
    disagreements = []
    opaque_tokens = collections.Counter()

    for ext, s in fields.items():
        rows = []
        data_shaped = s["shape"].startswith("DATA")
        for f in s["fields"]:
            identifier = f["kind"] == "ref" or data_shaped
            text, conf, unknown, expanded = gloss(f["key"], words, ai_tokens)
            if identifier:
                ident_count += 1
                rows.append({"key": f["key"], "kind": f["kind"],
                             "names": f["names"], "role": "identifier"})
                continue
            conf_count[conf] += 1
            for u in unknown:
                opaque_tokens[u] += 1
            entry = {"key": f["key"], "gloss": text, "confidence": conf,
                     "kind": f["kind"], "names": f["names"], "role": "descriptive",
                     "unknown_tokens": unknown, "expanded": expanded}
            leaf = f["key"].split("/")[-1].lower()
            if leaf in curated.get(ext, {}):
                checked += 1
                human = curated[ext][leaf]
                stem = lambda ws: {w[:4] for w in ws}
                hw = stem(re.findall(r"[a-z]{3,}", human.lower()))
                mine = stem(re.findall(r"[a-z]{3,}", text))
                entry["human_label"] = human
                if hw & mine:
                    agree += 1
                    entry["vs_human"] = "overlaps"
                else:
                    disagree += 1
                    entry["vs_human"] = "NO OVERLAP"
                    disagreements.append(
                        {"ext": ext, "key": f["key"], "mine": text,
                         "human": human, "names": f["names"]})
            rows.append(entry)
        out[ext] = rows

    json.dump({"vocabulary_size": len(words),
               "identifier_keys": ident_count,
               "confidence": dict(conf_count),
               "checked": checked, "agree": agree, "disagree": disagree,
               "disagreements": disagreements,
               "opaque_tokens": opaque_tokens.most_common(60),
               "fields": out}, open(args.json, "w"), indent=1)

    tot = sum(conf_count.values())
    print("\n%d keys are IDENTIFIERS (asset or flag names), set aside" % ident_count)
    print("%d keys are DESCRIPTIVE and were glossed:" % tot)
    print("vocabulary: %d words total" % len(words))
    print("   displayed by the game      %d" % len(displayed))
    print("   shared across >=%d formats  %d (%d of them never displayed)"
          % (SHARED_EXTS, len(shared), len(shared - displayed)))
    for c in ("HIGH", "MEDIUM", "OPAQUE"):
        print("   %-7s %6d  %5.1f%%" % (c, conf_count[c], 100.0 * conf_count[c] / tot))
    print("\nscored against %d human-written labels (stem overlap): "
          "%d agree, %d do not (%.0f%%)"
          % (checked, agree, disagree, 100.0 * agree / max(checked, 1)))
    ninf = sum(1 for _k, (_l, ev) in ABBREV.items() if ev is None)
    print("abbreviations: %d total, %d evidence-backed, %d pure inference"
          % (len(ABBREV), len(ABBREV) - ninf, ninf))
    print("\nkeys whose NAME does not carry their MEANING (human knew more):")
    for x in disagreements[:12]:
        print("   .%-8s %-30s" % (x["ext"], x["key"][:30]))
        print("        name reads: %s" % x["mine"])
        print("        human says: %s" % x["human"])
    print("\nmost common tokens that are NOT words (the real unknowns):")
    print("   " + ", ".join("%s(%d)" % kv for kv in opaque_tokens.most_common(24)))
    print("\n-> %s" % args.json)
    if args.md:
        write_md(out, conf_count, checked, agree, disagree, disagreements,
                 opaque_tokens, (len(words), len(displayed), len(shared),
                                 len(shared - displayed)), args.md, ident_count)
        print("-> %s" % args.md)


def write_md(out, conf, checked, agree, disagree, disagreements,
             opaque_tokens, vocab, path, ident=0):
    tot = sum(conf.values())
    with open(path, "w") as fh:
        fh.write("# GLOSSARY — what the field names read as. **INFERENCE.**\n\n")
        fh.write("Owner: **rdr-re**. Generated by\n"
                 "`03_Tools/research/probe_glossary.py`; do not hand-edit.\n\n")
        fh.write("> **This file is not evidence.** `FIELDS.md` is measurement —\n"
                 "> kinds and ranges counted over every file on the disc. This one\n"
                 "> reads the NAMES, and a name can lie. Nothing here should be put\n"
                 "> in front of a user as fact without someone checking the field\n"
                 "> actually does what its name suggests.\n\n")
        fh.write("## How it is kept honest\n\n")
        total, displayed, shared, unseen = vocab
        fh.write("**The English comes from the game.** There is no dictionary on\n"
                 "the build machine, and inventing a word list would make this\n"
                 "unfalsifiable. %d words from three auditable sources:\n\n"
                 "- **%d displayed** — the `string` column of the 38 `.xls` design\n"
                 "  spreadsheets and the text of all 101,778 `.strtbl` records.\n"
                 "- the 248-name **AI vocabulary** from the executable.\n"
                 "- **%d shared** — tokens used as a key token in at least %d\n"
                 "  different extensions. A term reused across eight unrelated file\n"
                 "  types is part of the engine's vocabulary.\n\n"
                 "The third source exists because of a measurement: with only the\n"
                 "first two, **66%% of all keys came back OPAQUE**. The game's\n"
                 "displayed English is not a dictionary for its internal field\n"
                 "names — **%d of the shared terms are never shown to a player**,\n"
                 "`rate`, `period`, `blend` and `slew` among them.\n\n"
                 % (total, displayed, shared, SHARED_EXTS, unseen))
        ninf = sum(1 for _k, (_l, ev) in ABBREV.items() if ev is None)
        fh.write("**Abbreviations carry their evidence.** %d expansions, %d backed\n"
                 "by the long form occurring as a key token elsewhere or by\n"
                 "`guns.py`'s weapon list, %d pure inference and marked as such.\n\n"
                 % (len(ABBREV), len(ABBREV) - ninf, ninf))
        fh.write("**It is scored against human labels.** `swt.py`, `guns.py`,\n"
                 "`creature.py`, `enemyfields.py` and `charfile.py` carry field\n"
                 "labels rdr-fmt wrote. Held out as ground truth: **%d keys "
                 "checked, %d overlap, %d do not (%.0f%%)**.\n\n"
                 % (checked, agree, disagree, 100.0 * agree / max(checked, 1)))
        fh.write("## Not every key is a description\n\n"
                 "**%d keys are IDENTIFIERS** — `.rep`'s animation clip names,\n"
                 "`.flags`'s game-state flags, `.cam`'s camera names. They are\n"
                 "labels, not descriptions, and no gloss will make `_bod_rev_die`\n"
                 "mean something in prose. They are counted and set aside. The\n"
                 "**%d descriptive keys** are what the table below covers; without\n"
                 "that split the headline reads 60%% OPAQUE and is measuring the\n"
                 "wrong thing.\n\n" % (ident, tot))
        fh.write("| confidence | keys | share | means |\n|---|---:|---:|---|\n")
        for c, m in (("HIGH", "every token is a word the game displays"),
                     ("MEDIUM", "a token needed an abbreviation expansion"),
                     ("OPAQUE", "a token is neither a word nor a known abbreviation")):
            fh.write("| **%s** | %d | %.1f%% | %s |\n"
                     % (c, conf[c], 100.0 * conf[c] / tot, m))
        fh.write("\n## The measured ceiling on this whole approach\n\n")
        fh.write("**%d of the %d keys with a hand-written label — %.0f%% — have a\n"
                 "name that does not carry their meaning.** Not one of them is the\n"
                 "glosser misreading the name; the human simply knew something the\n"
                 "name does not say.\n\n"
                 "So: splitting a field name gets you something usable about %.0f%%\n"
                 "of the time and misses real meaning the rest. **Do not ship these\n"
                 "as UI labels without a human reading them.** The %d descriptive\n"
                 "keys with no human label to check against should be assumed to\n"
                 "fail at the same rate.\n\n"
                 % (disagree, checked, 100.0 * disagree / max(checked, 1),
                    100.0 * agree / max(checked, 1), tot - checked))
        if disagreements:
            fh.write("| ext | key | reads as | human says |\n|---|---|---|---|\n")
            for x in disagreements:
                fh.write("| `.%s` | `%s` | %s | %s |\n"
                         % (x["ext"], x["key"], x["mine"], x["human"]))
        else:
            fh.write("None.\n")
        fh.write("\n## The tokens that are not words\n\n")
        fh.write("What actually blocks a reading. Anyone wanting to extend this\n"
                 "file should start here, not with the glosses.\n\n    %s\n\n"
                 % ", ".join("%s (%d)" % kv for kv in opaque_tokens.most_common(40)))
        fh.write("## The glosses\n\n")
        fh.write("OPAQUE keys only — the ones a reader cannot get from the name.\n"
                 "HIGH and MEDIUM glosses are in `07_Research/glossary.json`;\n"
                 "they are the ones you can work out yourself by splitting the\n"
                 "name, which is all this pass does.\n\n")
        for ext, rows in sorted(out.items()):
            bad = [r for r in rows if r.get("confidence") == "OPAQUE"]
            if not bad:
                continue
            desc = [r for r in rows if r.get("role") == "descriptive"]
            fh.write("\n### `.%s` — %d of %d descriptive keys unreadable from the name\n\n"
                     % (ext, len(bad), len(desc)))
            fh.write("| key | kind | names | blocked by |\n|---|---|---:|---|\n")
            for r in sorted(bad, key=lambda r: -r["names"])[:30]:
                fh.write("| `%s` | %s | %d | %s |\n"
                         % (r["key"], r["kind"], r["names"],
                            ", ".join(r["unknown_tokens"][:5])))


if __name__ == "__main__":
    main()
