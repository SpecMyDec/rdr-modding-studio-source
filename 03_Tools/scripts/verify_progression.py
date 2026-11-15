"""Verify rdrstudio/progression.py against the vanilla disc.

    python scripts/verify_progression.py

Read-only. Every write is exercised in dry mode, which computes the real
DEFLATE payload and compares it to the real slot size but never touches the
ISO -- 01_Vanilla is read-only and this script must stay runnable against it.

Prints numbers, then asserts. A failing assert here means either the module
drifted or the disc is not the one this was measured on.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rdrstudio import progression as P          # noqa: E402
from rdrstudio.game import Game                 # noqa: E402

ISO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "..", "01_Vanilla", "RDR_VANILLA.iso")

FAILED = []


def _short(x, n=90):
    s = repr(x)
    return s if len(s) <= n else s[:n - 3] + "..."


def check(label, got, want):
    ok = got == want
    print("  %-52s %-28s %s" % (label, _short(got, 28), "OK" if ok else
                                "FAIL (want %s)" % _short(want)))
    if not ok:
        FAILED.append(label)


def note(label, got):
    print("  %-52s %s" % (label, got))


def main():
    g = Game(ISO, writable=False)
    try:
        run(g)
    finally:
        g.close()
    print()
    if FAILED:
        print("FAILED %d check(s): %s" % (len(FAILED), ", ".join(FAILED)))
        return 1
    print("all checks passed")
    return 0


def run(g):
    # ------------------------------------------------------------ location
    print("\n[1] where the files are")
    for name in (P.LEVELFLOW, P.UNLOCKABLES):
        where = g.locate(name)
        check("%s archives" % name, where, ["SWAT.DAT"])
    e = g.archive(P.SWAT).find(P.LEVELFLOW)
    note("levelflow.csv text/comp/slot",
         "%d / %d / %d bytes" % (e.size_full, e.size_comp, e.slot))
    e = g.archive(P.SWAT).find(P.UNLOCKABLES)
    note("unlockables.csv text/comp/slot",
         "%d / %d / %d bytes" % (e.size_full, e.size_comp, e.slot))

    # ------------------------------------------------------------ levelflow
    print("\n[2] levelflow.csv")
    lf = P.read_levelflow(g)
    check("missions", lf["count"], 32)
    check("columns", len(lf["header"]), 18)
    check("header matches module constant", lf["notes"], [])
    check("column 13 header", lf["header"][13], "BHunter")
    check("column 14 header", lf["header"][14], "BHunter Weapons")
    check("column 15 header", lf["header"][15], "BHunter Health")
    check("column 16 header", lf["header"][16], "BHunter Dead-Eye")
    check("column 17 header (not in the brief)", lf["header"][17], "Boss")
    check("BHunter-enabled missions", lf["bhunter_count"], 20)

    by_key = {c["key"]: c for c in lf["columns"]}
    check("BHunter Health distinct values", by_key["bhunter_health"]["distinct"], 1)
    check("BHunter Health constant", by_key["bhunter_health"]["constant"], "0")
    check("BHunter Dead-Eye distinct values",
          by_key["bhunter_deadeye"]["distinct"], 1)
    check("BHunter Dead-Eye constant", by_key["bhunter_deadeye"]["constant"], "0")
    check("unused_knobs reported", [k["key"] for k in lf["unused_knobs"]],
          ["bhunter_health", "bhunter_deadeye"])
    check("both flagged unused", all(k["unused"] for k in lf["unused_knobs"]),
          True)
    note("BHunter Weapons overrides (non-empty rows)",
         sum(1 for r in lf["rows"] if r["bhunter_weapons"]))
    note("lvl_tomb rows (name repeats, variation disambiguates)",
         sum(1 for r in lf["rows"] if r["level"] == "lvl_tomb"))
    check("row 0 is lvl_perfnew", lf["rows"][0]["level"], "lvl_perfnew")
    check("row 0 BHunter weapons list", lf["rows"][0]["bhunter_weapons_list"],
          ["Old_Pistol"])
    check("row 1 BHunter weapons list", lf["rows"][1]["bhunter_weapons_list"],
          ["Old_Pistol", "Owl_Rifle"])

    # campaign.py names these columns differently; record the drift rather
    # than editing a file another agent owns.
    from rdrstudio import campaign
    note("campaign.LEVELFLOW_COLS length (stale: file has 18)",
         len(campaign.LEVELFLOW_COLS))

    # --------------------------------------------------------- unlockables
    print("\n[3] unlockables.csv")
    un = P.read_unlockables(g)
    check("rows", un["count"], 221)
    check("type counts", un["type_counts"],
          {"journal": 147, "character": 48, "stage": 16, "cheat": 10})
    check("row widths present in file", un["row_widths"], [6, 9])
    check("Default=1 rows", len(un["unlocked_by_default"]), 19)
    check("criteria recognised by the executable",
          len(un["criteria"]["recognised_by_game"]), 28)
    check("criteria used in file", len(un["criteria"]["used_in_file"]), 20)
    check("criteria used but unrecognised",
          un["criteria"]["used_but_unrecognised"], [])
    check("criteria recognised but unused",
          un["criteria"]["recognised_but_unused"],
          ["FAVORITE_POKER_RULES", "FOCUS_GAINED", "GAMES_PLAYED",
           "GATLING_SHOTS", "MONEY_SPENT", "PCT_JOURNAL_COMPLETE",
           "PEOPLE_POISONED", "PEOPLE_THROWN"])
    for dc in un["dead_columns"]:
        check("'???' col %d non-empty cells" % dc["column"],
              dc["rows_non_empty"], 0)
    note("rows carrying the '???' columns at all",
         un["dead_columns"][0]["rows_with_column"])

    # The MP criteria table really is in the executable where we say it is.
    elf = os.path.join(os.path.dirname(ISO), "..", "07_Research",
                       "SLUS_205.00.unpacked.bin")
    if os.path.exists(elf):
        import struct
        blob = open(elf, "rb").read()
        base = 0x00100000
        names = []
        for i in range(len(P.MP_CRITERIA)):
            ptr = struct.unpack_from("<I", blob,
                                     P.MP_CRITERIA_TABLE_ADDR - base + i * 4)[0]
            o = ptr - base
            names.append(blob[o:blob.index(b"\x00", o)].decode("latin-1"))
        check("MP criteria table at 0x%08X" % P.MP_CRITERIA_TABLE_ADDR,
              tuple(names), P.MP_CRITERIA)
        # Not strcmp order: 'TIMES_KILLED' sorts BEFORE 'TIME_FROZEN' in
        # ASCII ('S' 0x53 < '_' 0x5F) but sits after it in the table. It is
        # alphabetical with underscores ignored, i.e. hand/tool sorted.
        check("table is NOT in strcmp order", names == sorted(names), False)
        check("table IS alphabetical ignoring underscores",
              names, sorted(names, key=lambda s: s.replace("_", "")))
    else:
        note("executable image", "absent -- skipped the table check")

    # -------------------------------------------------------- difficulty
    print("\n[4] difficulty")
    d = P.difficulty_info(g)
    check("levels", d["count"], 4)
    check("names read from swat.strtbl", [l["name"] for l in d["levels"]],
          ["Normal", "Hard", "Very Hard", "Red Wood Revolver"])
    check("name source", d["levels"][0]["name_source"], "swat.strtbl, read now")
    check("menu bound", d["menu_bound"]["value"], 4)
    check("ladder is inferred, not measured", d["ladder_confidence"],
          P.INFERRED)
    check("every ladder step is inferred",
          sorted({s["confidence"] for s in d["ladder"]}), [P.INFERRED])
    for s in d["ladder"]:
        note("  %s ->" % s["evidence_string_id"], repr(s["evidence_text"]))
    check("diff_unlock_0 text", d["ladder"][0]["evidence_text"],
          "Hard mode unlocked!\nBounty Hunter mode unlocked!")
    check("diff_unlock_1 text", d["ladder"][1]["evidence_text"],
          "Very Hard mode unlocked!\nRed Wood Revolver mode unlocked!")
    check("Bounty Hunter is a flag, not a difficulty",
          d["bounty_hunter"]["is_separate_difficulty"], False)
    check("no disc-side difficulty unlock is offered",
          P.unlock_all_difficulties_note()["supported"], False)

    # ------------------------------------------------------------- cheats
    print("\n[5] cheats")
    c = P.cheats(g)
    check("cheats", c["count"], 10)
    check("live", c["live"], 8)
    check("dead", c["dead"], 2)
    dead = sorted(x["name"] for x in c["cheats"] if not x["live"])
    check("dead cheats", dead, ["cheat_showaiinfo", "cheat_showposition"])
    for x in c["cheats"]:
        if not x["live"]:
            note("  %s" % x["name"], x["dead_reason"])
    check("mask offset", c["mask"]["offset"], 2144)
    check("bits with no consumer", c["mask"]["bits_without_consumers"],
          (1, 2, 4, 8, 9))
    check("8 and 9 are among them",
          {8, 9} <= set(c["mask"]["bits_without_consumers"]), True)
    check("bit->name mapping is NOT claimed",
          c["mask"]["bit_to_name_mapping"], None)
    # CSV row order is not menu order -- proving it keeps anyone from
    # assuming row index == mask bit.
    csv_order = [x["short"] for x in c["cheats"]]
    menu_order = [n[len("cheat_"):] for n in P.CHEAT_MENU_ORDER]
    note("cheat order in unlockables.csv", csv_order)
    note("cheat order in the menu code   ", menu_order)
    check("the two orders differ", csv_order == menu_order, False)

    # ------------------------------------------------------- writes (dry)
    print("\n[6] slot-tight writes, dry only")
    p = P.plan(g, P.UNLOCKABLES, g.read(P.SWAT, P.UNLOCKABLES))
    note("unlockables.csv unchanged: payload/slot",
         "%d / %d (headroom %d)" % (p["payload_bytes"], p["slot_bytes"],
                                    p["headroom"]))
    check("unchanged file fits", p["fits"], True)

    # Same length: 0 -> 1 on a cheat's Default column.
    r = P.set_unlockable_field(g, 0, "default", 1, dry=True)
    note("cheat_bigmoney Default 0->1 payload/slot",
         "%d / %d (headroom %d)" % (r["payload_bytes"], r["slot_bytes"],
                                    r["headroom"]))
    check("that edit fits", r["fits"], True)
    check("dry run wrote nothing", r["written"], False)

    # Longer text. The uncompressed file grows, which a naive "byte count must
    # not grow" rule would refuse -- the real limit is the compressed payload.
    r = P.set_bhunter_knobs(g, 0, health=150, deadeye=200, dry=True)
    note("row 0 BHunter Health=150 Dead-Eye=200",
         "text %d bytes (vanilla %d), payload %d / slot %d"
         % (r["text_bytes"], len(g.read(P.SWAT, P.LEVELFLOW)),
            r["payload_bytes"], r["slot_bytes"]))
    check("text grew", r["text_bytes"] > len(g.read(P.SWAT, P.LEVELFLOW)), True)
    check("but it still fits the slot", r["fits"], True)
    check("flagged untested", r["untested"], True)
    check("both knobs changed", sorted(r["changed"]),
          ["bhunter_deadeye", "bhunter_health"])

    # The edit really lands in the right cells and touches nothing else.
    before = g.read(P.SWAT, P.LEVELFLOW)
    rows = P.parse_levelflow(before)["rows"]
    import rdrstudio.campaign as cp
    new = before
    for key, val, col in (("bhunter_deadeye", "200", 16),
                          ("bhunter_health", "150", 15)):
        new = P._apply_cell(new, cp.parse_csv(new), 0, col, val, key)
    after = P.parse_levelflow(new)["rows"]
    check("row 0 health after edit", after[0]["bhunter_health"], "150")
    check("row 0 dead-eye after edit", after[0]["bhunter_deadeye"], "200")
    check("row 0 everything else unchanged",
          [after[0][k] for k in P.LEVELFLOW_KEYS if k not in P.UNUSED_KNOBS],
          [rows[0][k] for k in P.LEVELFLOW_KEYS if k not in P.UNUSED_KNOBS])
    check("rows 1..31 unchanged",
          [r["cells"] for r in after[1:]], [r["cells"] for r in rows[1:]])

    # Byte-exactness. A zip() diff is meaningless once the file grows -- the
    # insert shifts everything after it -- so rebuild what the file MUST be:
    # vanilla with exactly two cell spans swapped, nothing else.
    vr = cp.parse_csv(before)[1]              # row 0 = header, row 1 = data 0
    s15, e15 = vr[15].start, vr[15].end
    s16, e16 = vr[16].start, vr[16].end
    expect = (before[:s15] + b"150" + before[e15:s16] + b"200" + before[e16:])
    check("file is vanilla with exactly two cells swapped", new, expect)
    note("bytes added by the edit", len(new) - len(before))
    note("bytes outside the two cells that changed",
         sum(1 for a, b in zip(before[:s15], new[:s15]) if a != b)
         + sum(1 for a, b in zip(before[e16:], new[len(new) - (len(before) - e16):])
               if a != b))

    # ------------------------------------------------------- refusals
    print("\n[7] refusals (each must raise, with a reason)")
    for label, fn in (
        ("column 8 on a 6-column row",
         lambda: P.set_unlockable_field(g, 20, "xbox", "1", dry=True)),
        ("unknown criterion name",
         lambda: P.set_unlockable_field(g, 0, "criteria", "MADE_UP", dry=True)),
        ("value containing a comma",
         lambda: P.set_mission_field(g, 0, "weapons", "a,b", dry=True)),
        ("row that does not exist",
         lambda: P.set_mission_field(g, 99, "bhunter", "1", dry=True)),
        ("column key that does not exist",
         lambda: P.set_mission_field(g, 0, "nope", "1", dry=True)),
        ("write to a read-only Game",
         lambda: P.set_mission_field(g, 0, "bhunter", "1", dry=False)),
    ):
        try:
            fn()
            print("  %-52s %s" % (label, "FAIL (did not raise)"))
            FAILED.append(label)
        except P.WriteRefused as ex:
            print("  %-52s refused: %s" % (label, str(ex)[:96]))

    # A value that bursts the slot must be refused. Note WHICH value bursts
    # it: 4000 repeated digits do NOT, because DEFLATE crushes them to
    # nothing. Only high-entropy text actually costs slot space. This is the
    # clearest demonstration that the limit is on the compressed payload, and
    # why a "text must not grow" rule would be both too strict and, for a
    # compressible edit, beside the point.
    import random
    import string
    nines = P.set_mission_field(g, 0, "bhunter_health", "9" * 4000, dry=True)
    note("4000 repeated digits: text/payload/slot",
         "%d / %d / %d -- fits, DEFLATE eats the repetition"
         % (nines["text_bytes"], nines["payload_bytes"], nines["slot_bytes"]))
    check("repetitive oversized text still fits", nines["fits"], True)

    rnd = random.Random(0)
    noise = "".join(rnd.choice(string.ascii_letters + string.digits)
                    for _ in range(2500))
    try:
        P.set_mission_field(g, 0, "bhunter_health", noise, dry=True)
        print("  %-52s %s" % ("2500 high-entropy chars",
                              "FAIL (did not raise)"))
        FAILED.append("oversized value")
    except P.WriteRefused as ex:
        print("  %-52s refused: %s" % ("2500 high-entropy chars",
                                       str(ex)[:96]))

    # ------------------------------------------------- a real, live write
    print("\n[8] live write")
    if "--live-write" in sys.argv:
        live_write()
    else:
        note("skipped", "pass --live-write to extract SWAT.DAT to a temp "
                        "file and patch it for real (~304 MB, ~1 min)")

    # ------------------------------------------------------- facade
    print("\n[9] summary()")
    s = P.summary(g)
    check("summary keys", sorted(s),
          ["cheats", "difficulty", "highlights", "levelflow", "unlockables"])
    for h in s["highlights"]:
        note("  [%s] %s" % (h["confidence"], h["title"]), h["detail"][:110])


def live_write():
    """Patch a real archive and read the bytes back off disk.

    Extracts SWAT.DAT out of the vanilla ISO into a temp file and works on
    that. 01_Vanilla and 02_Modded are read-only; a loose copy is the only
    honest way to prove the write path actually lands, and it is why
    progression.py accepts a bare Dave as well as a Game.
    """
    import shutil
    import tempfile

    from rdrstudio.game import open_loose
    from rdrstudio.iso9660 import Iso9660

    tmp = tempfile.mkdtemp(prefix="rdr_prog_")
    dest = os.path.join(tmp, "SWAT.DAT")
    try:
        iso = Iso9660(ISO)
        try:
            entry = next(e for e in iso.files()
                         if e.path.upper().endswith("SWAT.DAT"))
            note("extracting", "%s (%.1f MB) -> %s"
                 % (entry.path, entry.size / 1e6, dest))
            with open(dest, "wb") as fh:
                left = entry.size
                pos = entry.offset
                while left:
                    n = min(left, 4 << 20)
                    fh.write(iso.read_at(pos, n))
                    pos += n
                    left -= n
        finally:
            iso.close()

        d = open_loose(dest)
        d.backing.close()
        from rdrstudio.dave import Dave, FileBacking
        d = Dave(FileBacking(dest, writable=True), label="SWAT.DAT")

        before = P.parse_levelflow(d.read(P.LEVELFLOW))
        check("temp copy reads 32 missions", before["count"], 32)
        check("row 3 health is vanilla zero",
              before["rows"][3]["bhunter_health"], "0")

        r = P.set_bhunter_knobs(d, 3, health=250, deadeye=175, dry=False)
        check("write reported success", r["written"], True)
        note("payload/slot after the write",
             "%d / %d" % (r["used"], r["slot"]))
        d.backing.close()

        # Re-open from scratch: nothing cached, this is what the game sees.
        d2 = open_loose(dest)
        after = P.parse_levelflow(d2.read(P.LEVELFLOW))
        check("row 3 health persisted", after["rows"][3]["bhunter_health"],
              "250")
        check("row 3 dead-eye persisted", after["rows"][3]["bhunter_deadeye"],
              "175")
        check("still 32 missions", after["count"], 32)
        check("every other row byte-identical",
              [x["cells"] for i, x in enumerate(after["rows"]) if i != 3],
              [x["cells"] for i, x in enumerate(before["rows"]) if i != 3])
        check("unlockables.csv untouched by a levelflow write",
              P.parse_unlockables(d2.read(P.UNLOCKABLES))["count"], 221)

        # A neighbouring file in the same archive must survive the slot write.
        check("swat.strtbl still parses",
              len(P.difficulty_info(d2)["levels"]), 4)
        d2.backing.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
