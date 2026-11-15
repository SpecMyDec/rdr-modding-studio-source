"""Everything the model-swap button claims, re-measured off the stock disc.

    python scripts/verify_modelswap.py

Reads `01_Vanilla\\RDR_VANILLA.iso` and writes nothing to it. The one real
byte-write goes to a COPY of one 5.7 MB archive in a temp directory, which is
enough to prove the patch path: `Dave.patch` is the only byte writer in the
codebase and it does not care whether its backing is an ISO or a loose file.

The numbers below are the ones quoted in `modelswap.py`, `features.json` and
the panel's own sentences. They are asserted rather than printed so that a
disc, a parser change or a wrong assumption cannot leave the prose claiming
something the data stopped saying.
"""

import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdrstudio import cast as cast_mod          # noqa: E402
from rdrstudio import config as cfg             # noqa: E402
from rdrstudio import modelswap as M            # noqa: E402
from rdrstudio import skeleton as skel_mod      # noqa: E402
from rdrstudio.dave import Dave, FileBacking    # noqa: E402
from rdrstudio.game import Game                 # noqa: E402
from rdrstudio.rsc import Rsc, parse_rnt        # noqa: E402

FAILURES = []


def check(ok, what, detail=""):
    print(("  ok   " if ok else "  FAIL ") + what + (("  -- " + detail) if detail else ""))
    if not ok:
        FAILURES.append(what)
    return ok


def main():
    g = Game(cfg.VANILLA_ISO)
    swat = g.archive("SWAT.DAT")
    f = M.facts(g)

    print("\nTHE PALETTE")
    crs = sorted(e.name for e in swat if e.ext == "cr")
    check(len(crs) == 181, "SWAT.DAT holds 181 .cr files", str(len(crs)))
    check(len(f.targets) == 122, "they name 122 distinct render bundles",
          str(len(f.targets)))

    same = sum(1 for n in crs
               if f.stock[n]["render"] == f.stock[n]["skel"])
    check(same == 181, "FileNameRender == FileNameSkel in all of them, so a "
          "swap must write BOTH", "%d/181" % same)
    borrowed = [n for n in crs if f.stock[n]["render"] != n[:-3]]
    check(len(borrowed) == 59, "59 already point at another stem", "")
    # Every one of the 59 is a variant of the same character, which is the
    # claim the panel makes when it says the disc does not prove an arbitrary
    # swap. 53 contain the target stem outright (mmrd -> mrd, zztbar -> zzt);
    # the other 6 are two-letter abbreviations of it (dg2 -> dgo, hr2 -> hrs,
    # ky2 -> kya, tt2 -> ttk, tw7 -> twg, zz2 -> zzt). Nothing on the disc
    # pairs two unrelated characters.
    inside = [n for n in borrowed if f.stock[n]["render"] in n[:-3]]
    rest = [n for n in borrowed if n not in inside]
    prefix = [n for n in rest if n[:2].lower() == f.stock[n]["render"][:2]]
    check((len(inside), len(prefix), len(rest)) == (53, 6, 6),
          "and every one is a variant of the same character -- 53 contain the "
          "target stem, the other 6 abbreviate it (dg2 -> dgo, hr2 -> hrs, "
          "ky2 -> kya, tt2 -> ttk, tw7 -> twg, zz2 -> zzt)",
          "%d inside + %d by prefix = %d" % (len(inside), len(prefix),
                                             len(inside) + len(prefix)))

    print("\nTHE BUNDLE IS ATOMIC -- face and morphs travel with the body")
    both = neither = split = 0
    for stem, t in f.targets.items():
        if t["face"] and t["morphs"]:
            both += 1
        elif not t["face"] and not t["morphs"]:
            neither += 1
        else:
            split += 1
    check((both, neither, split) == (84, 38, 0),
          "84 bundles carry both an MPH0 chunk and a <stem>_face MOD0, "
          "38 carry neither, 0 carry one without the other",
          "%d / %d / %d" % (both, neither, split))

    # The panel reads both flags out of the tiny `.rnt` manifest instead of
    # decompressing 26 MB of bundles. That shortcut is only allowed while it
    # agrees with the bundles themselves.
    agree = 0
    for stem in f.targets:
        rnt = swat.read(stem + "_lods.rnt")
        names = parse_rnt(rnt)
        bundle = Rsc(swat.read(stem + "_lods.rsc"))
        real_face = any((names.get(c.index) or "").lower() == stem + "_face"
                        for c in bundle.chunks if c.tag == "MOD0")
        if (real_face == f.targets[stem]["face"]
                and (bundle.tally().get("MPH0", 0) > 0)
                == f.targets[stem]["morphs"]):
            agree += 1
    check(agree == 122, "the .rnt manifest and the .rsc chunk table agree on "
          "both flags for every bundle", "%d/122" % agree)

    print("\nBONE COUNTS")
    match = 0
    for stem, t in f.targets.items():
        bundle = Rsc(swat.read(t["file"]))
        counts = {bundle.bone_count(c.index) for c in bundle.chunks
                  if c.tag == "MOD0"}
        counts.discard(0)
        if t["bones"] and t["bones"] in counts:
            match += 1
    check(match == 122, "a MOD0 bone count equals the paired .skel NumBones "
          "in every render bundle", "%d/122" % match)
    rigs = {}
    for n in crs:
        rigs.setdefault(f.targets[f.stock[n]["render"]]["bones"], []).append(n)
    check(len(rigs.get(52, ())) == 139, "139 creatures share the 52-bone rig",
          "%d" % len(rigs.get(52, ())))

    print("\nHIT BOXES STAY BEHIND -- and are checked")
    # Resolved against the skeleton the `.cr` actually NAMES, not against
    # `<stem>.skel`. The difference is the 59 borrowers: `mmrd.sphs` has no
    # `mmrd.skel` to check against and looks unresolvable, while the game has
    # been reading it against `mrd.skel` all along.
    tot = {"hot": [0, 0], "sphs": [0, 0]}
    for n in crs:
        bones = set(f.targets[f.stock[n]["skel"]]["bone_names"])
        for fn, parents in M.hitbox_parents(g, n[:-3]):
            ext = fn.rsplit(".", 1)[1]
            tot[ext][0] += len(parents)
            tot[ext][1] += sum(1 for p in parents if p in bones)
    check(tot["hot"] == [2136, 2136], "every .hot parent of a creature names "
          "a bone in the skeleton its .cr points at", "%d/%d" % tuple(tot["hot"]))
    check(tot["sphs"] == [885, 885], "and every .sphs parent too",
          "%d/%d" % tuple(tot["sphs"]))

    # Two populations, and the picker only ever offers the first. The second
    # is what makes the check worth running: 696 pairs DO lose a parent, so a
    # clean answer is a result and not a stub that always says yes.
    equal = [0, 0]
    every = [0, 0]
    firing = None
    for n in crs:
        parents = {p for _, ps in M.hitbox_parents(g, n[:-3]) for p in ps}
        if not parents:
            continue
        mine = f.targets[f.stock[n]["render"]]["bones"]
        for t, row in f.targets.items():
            lost = bool(parents - set(row["bone_names"]))
            every[0] += 1
            every[1] += lost
            if row["bones"] == mine:
                equal[0] += 1
                equal[1] += lost
            elif lost and firing is None:
                firing = (n, t)
    check(equal == [12826, 0],
          "across every equal-bone-count pair the picker offers, no hit box "
          "loses its parent bone", "%d pairs, %d broken" % tuple(equal))
    check(every == [21716, 696],
          "but 696 of all 21,716 conceivable pairs DO lose one, so the check "
          "is live rather than decorative", "%d of %d" % (every[1], every[0]))
    if firing:
        probe = M.check_hitboxes(g, firing[0][:-3], firing[1])
        check(probe["checked"] and probe["missing"],
              "check_hitboxes fires on one of them (%s -> %s)" % firing,
              ", ".join(probe["missing"][0]["bones"][:4]))

    print("\nTHE TARGET HAS TO BE IN THE SAME ARCHIVE")
    copies = ok = 0
    for arc in g.archives():
        d = g.archive(arc)
        names = {e.name for e in d}
        for e in d:
            if e.ext != "cr":
                continue
            info = cast_mod.read_creature(d.read(e))
            stem = (info["render"] or e.name[:-3]).lower()
            copies += 1
            if (stem + "_lods.rsc") in names and (stem + ".skel") in names:
                ok += 1
    check(copies == ok == 517, "every .cr copy on the disc has its model AND "
          "its skeleton beside it, in the same archive", "%d/%d" % (ok, copies))
    full = [a for a in g.archives()
            if all((t + "_lods.rsc") in {e.name for e in g.archive(a)}
                   for t in f.targets)]
    check(full == ["SWAT.DAT"], "and only SWAT.DAT holds all 122 bundles",
          ", ".join(full) or "none")

    print("\nTHE REWRITE IS SURGICAL")
    # The disc is CRLF throughout, so `$` in a multiline pattern does NOT sit
    # where a reader expects -- it wants the position before `\n`, and `\r` is
    # in the way. props.py records the same trap costing it every block name.
    STRIP = re.compile(rb"(?m)^[ \t]*FileName(?:Render|Skel)[ \t]+\S+[ \t]*(?=\r?\n)")
    check(len(STRIP.findall(swat.read(crs[0]))) == 2,
          "the control pattern finds both lines (a CRLF-blind one finds none "
          "and would make the next check vacuous)")
    wrong = disturbed = grew = worst = 0
    for n in crs:
        before = swat.read(n)
        anim = M.read_names(before)["FileNameAnim"]
        rest = STRIP.sub(b"", before)
        for t in f.targets:
            after = M.set_fields(before, {k: t for k in M.FIELDS})
            got = M.read_names(after)
            if (got["FileNameRender"] != t or got["FileNameSkel"] != t
                    or got["FileNameAnim"] != anim):
                wrong += 1
            if STRIP.sub(b"", after) != rest:
                disturbed += 1
            if len(after) != len(before):
                grew += 1
                worst = max(worst, len(after) - len(before))
    check(wrong == 0, "181 x 122 rewrites all set both fields and leave "
          "FileNameAnim alone", "%d rewrites" % (181 * 122))
    check(disturbed == 0, "and not one byte outside those two lines moves")
    check(worst <= 6, "growth is at most 6 bytes, and only when the new name "
          "is longer than the old plus its separator",
          "%d of %d grew, worst +%d" % (grew, 181 * 122, worst))

    print("\nNO SWAP THIS DISC ALLOWS CAN OVERFLOW A SLOT")
    rows_total = overflow = 0
    worst = None
    for name in sorted(f.stock):
        for t in f.targets:
            for r in M.plan_swap(g, name, t)[0]:
                rows_total += 1
                if not r["fits"]:
                    overflow += 1
                if worst is None or r["headroom"] < worst["headroom"]:
                    worst = dict(r)
    check((rows_total, overflow) == (25103, 0),
          "every one of the 25,103 write rows a swap could ever plan fits",
          "%d rows, %d overflow" % (rows_total, overflow))
    check(worst["headroom"] == 3,
          "the tightest is 3 bytes spare (%s in %s)"
          % (worst["file"], worst["archive"]),
          "%d of %d stored" % (worst["stored"], worst["slot"]))
    # jck2.cr in GHOST.DAT is the handoff's famous zero-headroom file: 1,120
    # stored in a 1,120 slot. A swap keeps the byte length identical, and this
    # recompression lands at 1,116-1,117 -- slightly UNDER what the disc itself
    # achieved, which is why the entry with no headroom at all still takes one.
    ghost = [r for t in f.targets for r in M.plan_swap(g, "jck2.cr", t)[0]
             if "GHOST" in r["archive"]]
    check(ghost and all(r["fits"] and r["bytes_after"] == r["bytes_before"]
                        for r in ghost),
          "including jck2.cr in GHOST.DAT, which ships with zero headroom",
          "%d targets, %d-%d stored of %d"
          % (len(ghost), min(r["stored"] for r in ghost),
             max(r["stored"] for r in ghost), ghost[0]["slot"]))

    print("\nONE REAL WRITE, ON A COPY")
    tmp = tempfile.mkdtemp(prefix="rdr_modelswap_")
    try:
        arc = "LVLS_SNG/PERFNEW.DAT"
        path = os.path.join(tmp, "PERFNEW.DAT")
        with open(path, "wb") as fh:
            src = g.archive(arc)
            fh.write(g.iso.read_file(arc))
        dave = Dave(FileBacking(path, writable=True), label="PERFNEW.DAT")
        entry = dave.find("ncb.cr")
        before = dave.read(entry)
        new = M.set_fields(before, {k: "mrd" for k in M.FIELDS})
        used, slot = dave.patch(entry, new)
        check(used <= slot, "the patched ncb.cr fits its slot",
              "%d of %d bytes" % (used, slot))
        again = Dave(FileBacking(path), label="PERFNEW.DAT")
        back = again.read(again.find("ncb.cr"))
        got = M.read_names(back)
        check(got["FileNameRender"] == "mrd" and got["FileNameSkel"] == "mrd",
              "re-reading the archive off disk gives the new model",
              "%s / %s" % (got["FileNameRender"], got["FileNameSkel"]))
        check(back == new, "byte-identical to what was written")
        _, _, bones, _ = skel_mod.parse(swat.read("mrd.skel"))
        check(len(bones) > 0 and M.read_names(back)["FileNameAnim"]
              == M.read_names(before)["FileNameAnim"],
              "and FileNameAnim was left alone", got["FileNameAnim"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "-" * 68)
    if FAILURES:
        print("%d FAILED:" % len(FAILURES))
        for x in FAILURES:
            print("   - " + x)
        return 1
    print("all model-swap checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
