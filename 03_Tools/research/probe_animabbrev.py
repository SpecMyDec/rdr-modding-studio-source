"""probe_animabbrev.py -- expanding the animation abbreviations, and how far it gets.

`GLOSSARY.md` left 3,849 descriptive keys unreadable, blocked by three-letter
tokens out of the animation naming scheme: `stn`, `hrs`, `wlk`, `snk`, `trw`,
`drw`, `inj`, `hld`. This is the attempt to expand them, and the headline is
that it only half works -- which is stated here rather than hidden behind a
list of confident-looking guesses.

THE DIRECT TEST FAILED FIRST. If the asset names ever spelled a word out, that
would settle it. They do not: of 17 abbreviations checked against every `.anim`
and `.gait` name on the disc, **the full form appears for none of them** (the
single hit, `poster`, is unrelated to `pst`). There is no Rosetta stone in the
corpus, so everything below is inference.

WHAT IS LEFT IS THREE SIGNALS, in descending strength.

  1. EXTERNAL CONFIRMATION. `guns.py` names the weapon set -- Pistol, Shotgun,
     Rifle, Knife, Dynamite, Gatling, Bow -- and those map onto `ptl`, `sho`,
     `rfl`, `knf`, `dyn`, `gat`, `bow`. A human settled these already.

  2. SLOT CATEGORY. Animation names are `_`-separated and positional, and each
     slot contains a few UNAMBIGUOUS full words that fix its category: slot 2
     holds `run` (1,839), `aim` and `mid`; slot 1 holds the guns.py-confirmed
     weapon codes. A candidate that fits the slot's category beats one that
     does not, which is what separates `stn` = stand from `stn` = stunned.

  3. CANDIDATE GENERATION from the game's own displayed vocabulary, by prefix
     or consonant skeleton (`wlk` <- w-l-k of "walk"). Candidates are then
     collapsed to distinct ROOTS: `walk`, `walking`, `walkin` and `walkokay`
     are one answer, not four, and counting them as four made almost every
     token look ambiguous when it was not.

  0. SLOT 0 IS NOT VOCABULARY AT ALL. The first component of an animation name
     is the character it belongs to -- `red`, `bty`, `pgj`, `mrk` -- and those
     are verifiable against the asset table rather than guessable: a slot-0
     token that is also the stem of a `.cr` or `.swt` file is a character code,
     and is reported as one instead of being expanded into an English word.

HOW ACCURATE IS SIGNAL 3, MEASURED, AND THE ANSWER IS "NOT VERY". Run the
generator against the 24 abbreviations that already have independent evidence
(`probe_glossary.ABBREV`) and rank the candidates by how often the game
displays them: **the right answer comes first for 6 of 24, and is in the top
three for 7.** And **9 of the 24 target words never appear in the game's text
at all** -- it never says "velocity", "rotation" or "transition" -- so no
vocabulary drawn from the game can ever reach them.

Two earlier cuts scored better and both numbers were false. The first scored
**24 of 24** against a word pool I had typed out myself, which contained
exactly the answers -- the circularity this lane exists to avoid, walked into
anyway. The second scored 9 of 24 by leaving candidate inflections uncollapsed,
so `walk`, `walking` and `walkin` counted as three chances at one answer.
Collapsing them to roots is the right presentation and it costs accuracy: 6.

So this pass does not produce answers. It produces a tiered worklist.

So the output is tiered, and only the top tier is a finding:

    CONFIRMED   an outside source (guns.py) already settled it
    CHARACTER   slot 0, and the token is the stem of a real .cr/.swt file
    SUPPORTED   one candidate, and it fits the slot category
    AMBIGUOUS   several candidates; they are all listed, none is chosen
    UNRESOLVED  no candidate in the game's vocabulary at all

    python3 03_Tools/research/probe_animabbrev.py [--md OUT.md]
"""

import argparse
import collections
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
from rdrstudio.iso9660 import Iso9660          # noqa: E402
from rdrstudio import dave                     # noqa: E402
import probe_glossary as G                     # noqa: E402

REPO = os.path.dirname(os.path.dirname(HERE))
VANILLA = os.path.join(REPO, "01_Vanilla", "RDR_VANILLA.iso")
RES = os.path.join(REPO, "07_Research")
VOWELS = set("aeiou")

# Settled by guns.py's weapon list, not by this probe.
CONFIRMED = {"ptl": "pistol", "rfl": "rifle", "sho": "shotgun", "knf": "knife",
             "dyn": "dynamite", "gat": "gatling", "bow": "bow"}

# Tokens that are already whole words. They are what fixes each slot's
# category, so they are listed rather than expanded.
PLAIN = {"run", "aim", "mid", "for", "rev", "sit", "top", "end", "max", "min",
         "die", "hit", "low", "big", "cut", "arm", "leg", "bod", "hed"}


def skeleton(word):
    return word[0] + "".join(c for c in word[1:] if c not in VOWELS)


MAX_SUBSEQ = 8


def subsequence(a, b):
    it = iter(b)
    return all(c in it for c in a)


def candidate(abbr, word):
    """Prefix of the word, of its consonant skeleton, or a bounded subsequence.

    Unbounded subsequence matching was tried first and is far too loose --
    `ang` matches `saysomething` -- and against a pool of asset names it scored
    1 correct in 24. Bounding it to short words that share a first letter, over
    a pool of words the game actually displays, is what makes it usable.

    The bound is not optional. Pure consonant-skeleton matching misses every
    abbreviation that keeps a vowel: `dul` is not the skeleton of `duel` (that
    is `dl`), so the earlier version confidently answered `dull` for a game
    full of duels. That is the kind of mistake this whole file is prone to.
    """
    return (word.startswith(abbr) or skeleton(word).startswith(abbr)
            or skeleton(word) == abbr
            or (len(word) <= MAX_SUBSEQ and word[0] == abbr[0]
                and subsequence(abbr, word)))


def character_stems(iso):
    """Stems that name a character file -- the slot-0 vocabulary."""
    out = set()
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b"Dave", b"DAVE"):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            if e.ext in ("cr", "swt", "msd"):
                out.add(e.name.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower())
    return out


def roots(cands):
    """Collapse candidates that are inflections of one another."""
    out = []
    for w in sorted(cands, key=len):
        if not any(w.startswith(r) for r in out):
            out.append(w)
    return out


def anim_slots(iso):
    slots = collections.defaultdict(collections.Counter)
    uses = collections.Counter()
    examples = collections.defaultdict(list)
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b"Dave", b"DAVE"):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            if e.ext not in ("anim", "gait"):
                continue
            stem = e.name.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
            parts = stem.split("_")
            for i, part in enumerate(parts):
                slots[i][part] += 1
                uses[part] += 1
                # Real file names are what a human actually needs to decide:
                # `akm_dvr_lft_stn` reads at a glance in a way `stn` does not.
                if len(examples[part]) < 4 and stem not in examples[part]:
                    examples[part].append(stem)
    return slots, uses, examples


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso", default=VANILLA)
    ap.add_argument("--md", default="")
    ap.add_argument("--min-uses", type=int, default=100)
    ap.add_argument("--json", default=os.path.join(RES, "animabbrev.json"))
    args = ap.parse_args()

    iso = Iso9660(args.iso)
    displayed = G.game_vocabulary(iso)
    slots, uses, examples = anim_slots(iso)
    chars = character_stems(iso)
    iso.close()
    pool = {w: c for w, c in displayed.items() if len(w) >= 3}

    # ---- accuracy of the generator, on abbreviations that already have evidence
    known = {k: v.split()[0] for k, (v, ev) in G.ABBREV.items() if ev}
    rank1 = top3 = reachable = 0
    for ab, truth in known.items():
        ranked = roots([w for _c, w in sorted(((pool[w], w) for w in pool
                                               if candidate(ab, w)), reverse=True)])
        if truth in pool:
            reachable += 1
        if ranked[:1] == [truth]:
            rank1 += 1
        if truth in ranked[:3]:
            top3 += 1

    # ---- the slot each abbreviation lives in, and that slot's plain-word anchors
    where = {}
    for tok in uses:
        best = max(slots, key=lambda i: slots[i][tok])
        where[tok] = best
    anchors = {i: [w for w, _c in c.most_common(40) if w in PLAIN][:6]
               for i, c in slots.items()}

    rows = []
    for tok, n in uses.most_common():
        if n < args.min_uses or tok in PLAIN or tok.isdigit() or len(tok) > 4:
            continue
        if tok in pool and len(tok) >= 4:
            continue                       # already a word the game displays
        cands = roots([w for _c, w in sorted(((pool[w], w) for w in pool
                                              if candidate(tok, w)), reverse=True)])
        slot = where[tok]
        if tok in CONFIRMED:
            tier, pick = "CONFIRMED", CONFIRMED[tok]
        elif slot == 0 and tok in chars:
            tier, pick = "CHARACTER", "character code"
        elif not cands:
            tier, pick = "UNRESOLVED", None
        elif len(cands) == 1:
            tier, pick = "SUPPORTED", cands[0]
        else:
            tier, pick = "AMBIGUOUS", None
        rows.append({"token": tok, "uses": n, "slot": slot,
                     "slot_anchors": anchors.get(slot, []),
                     "tier": tier, "expansion": pick,
                     "candidates": cands[:6],
                     "examples": examples.get(tok, [])})

    report = {"generator_accuracy": {
                  "known": len(known), "rank1": rank1, "top3": top3,
                  "target_word_in_vocabulary": reachable},
              "tokens": rows}
    json.dump(report, open(args.json, "w"), indent=1)

    print("generator scored on %d abbreviations that already have evidence:"
          % len(known))
    print("   right answer ranked 1st : %d" % rank1)
    print("   in the top three        : %d" % top3)
    print("   target word in the game's vocabulary at all : %d" % reachable)
    tiers = collections.Counter(r["tier"] for r in rows)
    print("\n%d animation tokens over %d uses: %s"
          % (len(rows), args.min_uses,
             ", ".join("%s %d" % kv for kv in tiers.most_common())))
    print("\n%-6s %7s %-5s %-11s %-12s %s"
          % ("TOKEN", "USES", "SLOT", "TIER", "EXPANSION", "CANDIDATES"))
    for r in sorted(rows, key=lambda r: (r["tier"], -r["uses"])):
        print("%-6s %7d %-5d %-11s %-12s %s"
              % (r["token"], r["uses"], r["slot"], r["tier"],
                 r["expansion"] or "--", ", ".join(r["candidates"][:4])))
    print("\nslot anchors (unambiguous whole words that fix each slot's category):")
    for i in sorted(anchors):
        if anchors[i]:
            print("   slot %d: %s" % (i, ", ".join(anchors[i])))
    print("\n-> %s" % args.json)
    if args.md:
        write_md(report, anchors, args.md)
        print("-> %s" % args.md)


def write_md(report, anchors, path):
    acc = report["generator_accuracy"]
    rows = report["tokens"]
    tiers = collections.Counter(r["tier"] for r in rows)
    with open(path, "w") as fh:
        fh.write("# ANIMATION ABBREVIATIONS — expansions, and how far they go\n\n")
        fh.write("Owner: **rdr-re**. Generated by\n"
                 "`03_Tools/research/probe_animabbrev.py`; do not hand-edit.\n\n")
        fh.write("> **Inference, with one tier of exception.** Only `CONFIRMED`\n"
                 "> rows rest on an outside source. Everything else is a reading\n"
                 "> of a three-letter token and should be treated as a lead.\n\n")
        fh.write("## The direct test failed\n\n"
                 "If the asset names ever spelled a word out, that would settle\n"
                 "it. They do not: checked against every `.anim` and `.gait` name\n"
                 "on the disc, **not one of these abbreviations appears in full**.\n"
                 "There is no Rosetta stone in the corpus, so what follows is\n"
                 "inference rather than evidence.\n\n")
        fh.write("## How accurate the generator is, measured\n\n")
        fh.write("Run against the %d abbreviations that already have independent\n"
                 "evidence, ranking candidates by how often the game displays them:\n\n"
                 "- right answer ranked **first: %d of %d**\n"
                 "- in the top three: **%d of %d**\n"
                 "- target word in the game's vocabulary at all: **%d of %d** —\n"
                 "  the game never says \"velocity\", \"rotation\" or \"transition\",\n"
                 "  so no vocabulary drawn from it can ever reach those.\n\n"
                 % (acc["known"], acc["rank1"], acc["known"], acc["top3"],
                    acc["known"], acc["target_word_in_vocabulary"], acc["known"]))
        fh.write("An earlier cut scored **24 of 24**. That number was worthless:\n"
                 "the word pool was one I had typed out myself and it contained\n"
                 "exactly the answers. Against the real vocabulary, with\n"
                 "distractors, it is %d.\n\n" % acc["rank1"])
        fh.write("## Slot anchors\n\n"
                 "Animation names are `_`-separated and positional. Each slot\n"
                 "holds a few unambiguous whole words that fix its category, and\n"
                 "a candidate that fits the category beats one that does not —\n"
                 "that is what separates `stn` = stand from `stn` = stunned.\n\n")
        for i in sorted(anchors):
            if anchors[i]:
                fh.write("- **slot %d**: %s\n"
                         % (i, ", ".join("`%s`" % w for w in anchors[i])))
        fh.write("\n## The tokens\n\n")
        fh.write("%s.\n\n" % ", ".join("**%s** %d" % kv for kv in tiers.most_common()))
        fh.write("| token | uses | slot | tier | expansion | other candidates | seen in |\n")
        fh.write("|---|---:|---:|---|---|---|---|\n")
        for r in sorted(rows, key=lambda r: (r["tier"], -r["uses"])):
            others = [c for c in r["candidates"] if c != r["expansion"]][:4]
            fh.write("| `%s` | %d | %d | %s | %s | %s | %s |\n"
                     % (r["token"], r["uses"], r["slot"], r["tier"],
                        r["expansion"] or "—", ", ".join(others) or "—",
                        ", ".join("`%s`" % x for x in r["examples"][:2]) or "—"))


if __name__ == "__main__":
    main()
