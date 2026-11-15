"""Does rdrstudio/streams.py describe the 8,361 real streams correctly?

Every number this prints is measured off RDR_VANILLA.iso at run time; nothing
is compared against a stored expectation except the handful of constants that
ARE the finding (8,361 files, 44 cue stems, 992 orphans). Those are asserted,
so if a different disc revision or a modded ISO disagrees, this says so
instead of quietly reporting different numbers.

    python scripts/verify_streams.py
    python scripts/verify_streams.py --derive     # re-derive the cue list
    python scripts/verify_streams.py --decode 12  # also decode N streams

Three claims here need more than an internal check, and get one:

  * DURATION IS NOT DECODED. Asserting `seconds` looks right proves nothing
    if it came from the header AND the header is misread. So `--decode`
    decodes real files and compares sample counts: header-derived duration
    must equal the decoded sample count exactly, not approximately.
  * THE WAV IS PLAYABLE. `to_wav` output is re-parsed as a RIFF file and its
    declared sizes checked against its actual length, because a WAV with a
    wrong `data` size opens in some players and not others.
  * 992 ORPHAN STREAMS. Claiming a file is referenced nowhere means searching
    everywhere, so the full-SWAT.DAT token scan runs by default (about 18 s;
    `--no-orphan-scan` skips it and the claim goes unverified rather than
    being assumed).
"""
import argparse
import collections
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rdrstudio import stm, streams                       # noqa: E402
from rdrstudio.game import Game                          # noqa: E402
from rdrstudio import config                         # noqa: E402

# The pristine disc, from config rather than a hardcoded `D:\` literal.
# `config.ROOT` derives from the module's own location, so this also
# stops the script being Windows-only -- it could not run under WSL
# python at all before. REQ-035's second consequence, in a file that
# was never on its list.
DISC = Path(config.VANILLA_ISO)

# The corpus, as measured today. Asserted rather than printed so a disc that
# differs is loud.
EXPECT_FILES = 8361
EXPECT_MONO = 8007
EXPECT_STEREO = 354
EXPECT_HOURS = 13.876
EXPECT_ORPHANS = 992
EXPECT_CUE_STEMS = 44      # stems used by >= 5 distinct subjects

fails = []


def flat(text):
    """The captions are Windows-1252 -- curly quotes and en dashes -- and a
    cp1252 console still refuses some of them. Flatten for printing only."""
    return (text or "").encode("ascii", "replace").decode("ascii")


def check(label, ok, detail=""):
    print("  [%s] %s%s" % ("ok" if ok else "FAIL", label,
                           ("  -- " + detail) if detail else ""))
    if not ok:
        fails.append(label)
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--decode", type=int, default=6,
                    help="decode N streams and cross-check the header maths")
    ap.add_argument("--derive", action="store_true",
                    help="re-derive the cue vocabulary from the corpus")
    ap.add_argument("--no-orphan-scan", action="store_true")
    ap.add_argument("--iso", default=str(DISC))
    args = ap.parse_args()

    game = Game(Path(args.iso), writable=False)

    # ------------------------------------------------------------- 1. speed
    print("\n1. listing speed -- the whole point of header-only reads")
    t = time.time()
    rows = streams.list_streams(game, captions=False)
    bare = time.time() - t
    t = time.time()
    rows = streams.list_streams(game, captions=True)
    full = time.time() - t
    s = streams.summary(rows)
    print("     %d streams, headers only          %.3f s" % (len(rows), bare))
    print("     %d streams + SWAT.DAT captions    %.3f s" % (len(rows), full))
    print("     bytes read for the listing        %.1f MB of %.2f GB stored"
          % (len(rows) * stm.HEADER_SIZE / 1e6, s["stored_bytes"] / 1e9))
    check("listing is under 5 s", full < 5.0, "%.3f s" % full)
    check("listing reads only headers",
          len(rows) * stm.HEADER_SIZE < 0.02 * s["stored_bytes"],
          "%.2f%% of the archive" % (100.0 * len(rows) * stm.HEADER_SIZE
                                     / s["stored_bytes"]))

    # ------------------------------------------------------------ 2. corpus
    print("\n2. the corpus")
    print("     total          %d" % s["total"])
    print("     failed to parse %d" % s["failed"])
    print("     playing time   %.3f hours" % s["hours"])
    print("     stored         %.2f GB" % (s["stored_bytes"] / 1e9))
    print("     channels       %s" % s["by_channels"])
    print("     rates          %s" % s["by_rate"])
    print("     looping        %d" % s["looping"])
    print("     subjects       %d" % s["subjects"])
    check("every .stm parses", s["failed"] == 0)
    check("file count is %d" % EXPECT_FILES, s["total"] == EXPECT_FILES,
          str(s["total"]))
    check("mono/stereo split is %d/%d" % (EXPECT_MONO, EXPECT_STEREO),
          s["by_channels"].get(1) == EXPECT_MONO
          and s["by_channels"].get(2) == EXPECT_STEREO,
          str(s["by_channels"]))
    check("playing time is %.3f h" % EXPECT_HOURS,
          abs(s["hours"] - EXPECT_HOURS) < 0.002, "%.3f" % s["hours"])

    # stereo is the content axis, not an audio detail
    stereo_kinds = collections.Counter(r["kind"] for r in rows
                                       if r["channels"] == 2)
    mono_kinds = collections.Counter(r["kind"] for r in rows
                                     if r["channels"] == 1)
    print("     stereo kinds   %s" % dict(stereo_kinds))
    print("     mono kinds     %s" % dict(mono_kinds))
    check("no voice is stereo",
          not (stereo_kinds["bark"] or stereo_kinds["dialogue"]))
    check("no music is mono", not mono_kinds["music"])
    loop_stereo = sum(1 for r in rows if r["looping"] and r["channels"] == 2)
    check("loop points are a stereo feature",
          loop_stereo >= 0.99 * s["looping"],
          "%d of %d looping files are stereo" % (loop_stereo, s["looping"]))
    plii = [r for r in rows if r["plii"]]
    check("_plii correlates with stereo",
          sum(1 for r in plii if r["channels"] == 2) >= len(plii) - 1,
          "%d of %d" % (sum(1 for r in plii if r["channels"] == 2), len(plii)))

    # ------------------------------------------------- 3. header vs decoder
    print("\n3. duration comes from the header -- so prove the header is right")
    picked = []
    for want in ("mono24", "stereo48", "stereo24", "looping"):
        for r in rows:
            if r in picked or not r["ok"]:
                continue
            if want == "mono24" and r["channels"] == 1 and r["rate"] == 24000:
                picked.append(r)
                break
            if want == "stereo48" and r["channels"] == 2 and r["rate"] == 48000:
                picked.append(r)
                break
            if want == "stereo24" and r["channels"] == 2 and r["rate"] == 24000:
                picked.append(r)
                break
            if want == "looping" and r["looping"]:
                picked.append(r)
                break
    step = max(1, len(rows) // max(1, args.decode))
    for r in rows[::step][:max(0, args.decode - len(picked))]:
        if r not in picked:
            picked.append(r)

    exact = 0
    print("     %-38s %8s %5s %2s %9s %9s"
          % ("stream", "sec", "rate", "ch", "hdr smp", "decoded"))
    for r in picked:
        data = game.read(streams.ARCHIVE, r["name"])
        t = time.time()
        rate, nch, chans = stm.decode(data)
        dt = time.time() - t
        got = min(len(c) for c in chans)
        same = (got == r["samples"] and rate == r["rate"] and nch == r["channels"])
        exact += bool(same)
        print("     %-38s %8.2f %5d %2d %9d %9d  %s (%.2fs)"
              % (r["name"][:38], r["seconds"], r["rate"], r["channels"],
                 r["samples"], got, "==" if same else "MISMATCH", dt))
    check("header sample count == decoded sample count",
          exact == len(picked), "%d of %d" % (exact, len(picked)))

    # decoding really is too slow to do for a listing -- show the number
    if picked:
        r = picked[0]
        data = game.read(streams.ARCHIVE, r["name"])
        t = time.time()
        stm.decode(data)
        one = time.time() - t
        sps = (r["samples"] * r["channels"]) / one if one else 0
        corpus = sum(x["samples"] * x["channels"] for x in rows if x["ok"])
        est = corpus / sps if sps else 0
        print("     decoding %s took %.2f s (%.2f M samples/s)"
              % (r["name"], one, sps / 1e6))
        print("     corpus is %.3f G samples -> %.1f minutes to decode it all"
              % (corpus / 1e9, est / 60.0))
        print("     the header scan is %.0fx faster" % (est / full))
        check("decoding the corpus is >100x slower than the header scan",
              est > 100 * full, "%.1f min vs %.3f s" % (est / 60.0, full))

    # ------------------------------------------------------------- 4. WAV
    print("\n4. to_wav produces a WAV a player will accept")
    for r in picked[:3]:
        wav = streams.to_wav(game.read(streams.ARCHIVE, r["name"]))
        import struct as _s
        riff, size, wave = wav[:4], _s.unpack_from("<I", wav, 4)[0], wav[8:12]
        fmt_sz, tag, nch, rate, bps, align, bits = _s.unpack_from("<IHHIIHH",
                                                                  wav, 16)
        dtag, dsize = wav[36:40], _s.unpack_from("<I", wav, 40)[0]
        good = (riff == b"RIFF" and wave == b"WAVE" and dtag == b"data"
                and size == len(wav) - 8 and dsize == len(wav) - 44
                and tag == 1 and bits == 16 and fmt_sz == 16
                and nch == r["channels"] and rate == r["rate"]
                and align == nch * 2 and bps == rate * nch * 2
                and dsize == r["samples"] * nch * 2)
        check("WAV %s" % r["name"], good,
              "%d Hz %dch %d bytes pcm" % (rate, nch, dsize))

    # -------------------------------------------------------- 5. convention
    print("\n5. the naming convention")
    parsed = collections.Counter(r["kind"] for r in rows)
    print("     kinds  %s" % dict(parsed))
    named = sum(1 for r in rows
                if r["cue"] or r["scene"] or r["take"] is not None
                or r["phrase"])
    print("     names that yield a cue, scene, take or phrase: %d of %d (%.1f%%)"
          % (named, len(rows), 100.0 * named / len(rows)))
    print("     unclassified ('other'): %d -- %s"
          % (parsed["other"],
             ", ".join(r["name"] for r in rows if r["kind"] == "other")[:150]))
    check("the convention explains over 95% of names",
          named > 0.95 * len(rows), "%.1f%%" % (100.0 * named / len(rows)))
    check("almost nothing falls through to 'other'",
          parsed["other"] < 0.005 * len(rows), "%d in 'other'" % parsed["other"])
    fn_caps = [r for r in rows if r["caption_source"] == "filename"]
    check("a filename-derived caption is only ever put on a spoken line",
          all(r["kind"] == "dialogue" and r["channels"] == 1 for r in fn_caps),
          "%d rows" % len(fn_caps))

    # The cue vocabulary, re-derived from the corpus. Single-token names are
    # excluded: in `pry03` the "subject" and the "cue stem" are the same nine
    # characters, so counting it makes Professor Perry's abbreviation look
    # like a cue used by nine different characters.
    stem_subjects = collections.defaultdict(set)
    for r in rows:
        toks = r["name"][:-4].split("_")
        if len(toks) < 2:
            continue
        m = re.match(r"^([a-z]+)(\d+)([a-d]?)$", toks[-1])
        if m:
            stem = streams.CUE_ALIASES.get(m.group(1), m.group(1))
            stem_subjects[stem].add(toks[0])
    derived = {k for k, v in stem_subjects.items() if len(v) >= 5}
    known = set(streams.CUES) | streams.FOLEY_CUES
    print("     stems used by >=5 subjects: %d" % len(derived))
    missing = sorted(derived - known)
    check("the derived cue vocabulary is %d stems" % EXPECT_CUE_STEMS,
          len(derived) == EXPECT_CUE_STEMS, str(len(derived)))
    check("every derived stem is in CUES or FOLEY_CUES",
          not missing, "missing: %s" % missing)
    if args.derive:
        for k in sorted(derived, key=lambda k: -len(stem_subjects[k])):
            print("       %-20s %3d subjects" % (k, len(stem_subjects[k])))

    # typos really are in the shipped data
    typos = {a: c for a, c in streams.CUE_ALIASES.items()
             if any(("_" + a) in r["name"] for r in rows)}
    check("every CUE_ALIAS is a spelling that actually ships",
          len(typos) == len(streams.CUE_ALIASES),
          "%d of %d found: %s" % (len(typos), len(streams.CUE_ALIASES),
                                  sorted(set(streams.CUE_ALIASES) - set(typos))))

    # ------------------------------------------------------- 6. the captions
    print("\n6. the game's own captions")
    idx = streams.caption_index(game)
    with_caption = [r for r in rows if r["caption"]]
    lines = [r for r in with_caption if len(r["caption"]) > 24]
    print("     sound-bank CSVs parsed             %d" % len(
        {row["csv"] for rs in idx.values() for row in rs}))
    print("     distinct names named by a bank     %d" % len(idx))
    print("     streams with a caption             %d" % len(with_caption))
    print("     captions long enough to be a line  %d" % len(lines))
    print("     streams referenced by a bank       %d" % s["referenced"])
    print("     streams referenced by nothing      %d" % s["unreferenced"])
    check("most streams are claimed by a sound bank",
          s["referenced"] > 0.85 * len(rows),
          "%d of %d" % (s["referenced"], len(rows)))
    # Most bank rows leave Comment blank, so a caption on a fifth of the
    # corpus is the honest ceiling here, not a shortfall.
    check("a useful number of streams carry a hand-written caption",
          len(with_caption) > 1500, "%d" % len(with_caption))
    check("hundreds of captions are long enough to be the spoken line",
          len(lines) > 500, "%d" % len(lines))
    dangling = sorted(n for n, v in idx.items()
                      if any(x["stream"] for x in v)
                      and n + ".stm" not in {r["name"] for r in rows})
    print("     bank rows marked Stream=1 with no .stm on the disc: %d"
          % len(dangling))
    print("     e.g. %s" % ", ".join(dangling[:5]))
    for r in with_caption[:0] or [x for x in rows
                                  if len(x["caption"]) > 30][:5]:
        print('     %-34s "%s"' % (r["name"][:34], flat(r["caption"])[:70]))

    # ------------------------------------------------------- 7. the orphans
    print("\n7. streams nothing on the disc asks for")
    if args.no_orphan_scan:
        print("     skipped (--no-orphan-scan): the 'referenced nowhere' claim")
        print("     is NOT verified in this run.")
    else:
        stems_all = {r["name"][:-4] for r in rows}
        token = re.compile(rb"[A-Za-z0-9_]{5,}")
        seen = set()
        t = time.time()
        swat = game.archive("SWAT.DAT")
        for e in swat:
            try:
                blob = swat.read(e)
            except Exception:
                continue
            for m in token.findall(blob):
                w = m.decode("latin-1").lower()
                if w in stems_all:
                    seen.add(w)
        orphans = sorted(stems_all - seen)
        print("     scanned %d SWAT.DAT entries in %.1f s"
              % (len(swat), time.time() - t))
        print("     referenced somewhere in SWAT.DAT   %d" % len(seen))
        print("     referenced nowhere                 %d" % len(orphans))
        lost = sum(r["seconds"] for r in rows
                   if r["name"][:-4] in set(orphans)) / 60.0
        print("     that is %.1f minutes of finished audio nothing on the"
              % lost)
        print("     disc asks for (reachable, though -- see C-010 and")
        print("     scripts/probe_stream_lookup.py)")
        print("     e.g. %s" % ", ".join(orphans[:6]))
        check("orphan count is %d" % EXPECT_ORPHANS,
              len(orphans) == EXPECT_ORPHANS, str(len(orphans)))
        # `referenced` in the listing uses the CSVs only, which is a subset
        check("listing's 'referenced' never claims more than the full scan",
              {r["name"][:-4] for r in rows if r["referenced"]} <= seen)

    # ------------------------------------------------------- 8. the grouping
    print("\n8. grouping -- does the listing read like content?")
    t = time.time()
    groups = streams.group_streams(rows)
    print("     %d groups built in %.3f s" % (len(groups), time.time() - t))
    check("every stream lands in exactly one group",
          sum(g["count"] for g in groups) == len(rows),
          "%d vs %d" % (sum(g["count"] for g in groups), len(rows)))
    labelled = [g for g in groups if g["label_source"]]
    covered = sum(g["count"] for g in labelled)
    print("     groups with a name taken from the disc: %d (%d streams, %.0f%%)"
          % (len(labelled), covered, 100.0 * covered / len(rows)))
    check("over half the corpus gets a disc-sourced group name",
          covered > 0.5 * len(rows), "%.0f%%" % (100.0 * covered / len(rows)))

    print("\n     top groups:")
    print("     %-16s %-28s %5s %8s  %s"
          % ("key", "label", "n", "minutes", "sections"))
    for g in groups[:12]:
        print("     %-16s %-28s %5d %8.1f  %s"
              % (g["key"][:16], (g["label"] or "")[:28], g["count"],
                 g["seconds"] / 60.0,
                 ", ".join("%s(%d)" % (sec["name"][:18], sec["count"])
                           for sec in g["sections"][:3])))

    # A voice set, shown as its sections: 114 files become a grid of AI cues.
    demo = next((g for g in groups if g["key"] == "mexcowboy2"), groups[0])
    print("\n     a voice set, by section -- %s:" % demo["key"])
    print("     %s   (%s)" % (demo["label"],
                              demo["label_source"] or "no bank names it"))
    for sec in demo["sections"][:8]:
        print("       %-24s %-9s %2d  e.g. %s"
              % (sec["name"][:24], sec["kind"], sec["count"],
                 sec["streams"][0]["name"]))

    # A scene, shown as its streams: this is the case where the disc hands
    # over the script, and a section list would hide exactly the useful part.
    scene = next((g for g in groups if g["key"] == "badguy"), None)
    if scene:
        print("\n     a scene, line by line -- %s (%s):"
              % (scene["key"], scene["label"]))
        for r in scene["sections"][0]["streams"][:8]:
            print("       %-28s %5.2fs  %s"
                  % (r["name"][:28], r["seconds"],
                     ('"%s"' % flat(r["caption"])[:46]) if r["caption"]
                     else "(no caption -- %s)"
                          % ("orphan" if not r["referenced"] else "bank row blank")))

    # ---------------------------------------------------------- 9. searching
    print("\n9. search reaches the spoken words, not just the filenames")
    for q in ("river", "Brimstone", "surrender"):
        hits = streams.search(rows, q)
        ex = next((h for h in hits if h["caption"]), hits[0] if hits else None)
        print("     %-12s %4d hits   %s" % (
            q, len(hits),
            ("%s -- \"%s\"" % (ex["name"], flat(ex["caption"])[:48])) if ex else ""))
    # "river" appears in no filename at all, so any hit must have come from a
    # caption -- which is the thing being tested.
    by_caption = [r for r in streams.search(rows, "river")
                  if "river" not in r["name"]]
    check("a word that appears only in a caption is findable",
          len(by_caption) > 0, "%d streams found by their words alone"
          % len(by_caption))

    game.close()

    print("\n%s" % ("-" * 62))
    if fails:
        print("FAILED %d check(s): %s" % (len(fails), "; ".join(fails)))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
