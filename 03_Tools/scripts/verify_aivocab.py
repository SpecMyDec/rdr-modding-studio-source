"""Prove rdrstudio/aivocab.py against the executable and against the disc.

    python scripts/verify_aivocab.py               # baked data + ELF cross-check
    python scripts/verify_aivocab.py --scan        # ... and re-scan all 14,921
                                                   #     shipped behaviour files
    python scripts/verify_aivocab.py --show FILE   # annotated dump of one file

Run from 03_Tools. --scan takes about 10 s and re-derives every usage number
the module bakes in, so a stale bake fails loudly rather than quietly.
"""
import argparse
import collections
import os
import re
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rdrstudio import actb, aivocab  # noqa: E402
from rdrstudio import config                         # noqa: E402
from rdrstudio.game import Game  # noqa: E402

# `07_Research` has no config constant, so this joins onto `config.ROOT`,
# which is the portable half. REQ-059.
ELF = os.path.join(config.ROOT, "07_Research", "SLUS_205.00.unpacked.bin")
ISO = config.VANILLA_ISO
EXTS = (".actb", ".stateb", ".acttbl")
TOKEN = re.compile(rb"[A-Za-z_][A-Za-z0-9_]*")

fails = []


def check(label, got, want):
    ok = got == want
    if not ok:
        fails.append("%s: got %r want %r" % (label, got, want))
    print("  [%s] %-52s %s" % ("ok" if ok else "FAIL", label, got))
    return ok


def section(t):
    print("\n" + t)
    print("-" * len(t))


def checks():
    """Everything provable without touching the ISO."""
    # ------------------------------------------------------- 1. the baked tables

    section("1. baked vocabulary, self-consistent")
    check("creature entries", sum(1 for c in aivocab.CONDITIONS
                                  if c.scope == aivocab.SCOPE_CREATURE)
          + sum(len(c.aliases) for c in aivocab.CONDITIONS
                if c.scope == aivocab.SCOPE_CREATURE), 199)
    check("group entries", sum(1 for c in aivocab.CONDITIONS
                               if c.scope == aivocab.SCOPE_GROUP)
          + sum(len(c.aliases) for c in aivocab.CONDITIONS
                if c.scope == aivocab.SCOPE_GROUP), 58)
    check("total entries", aivocab._ENTRY_COUNT, 257)
    check("distinct (scope, id) pairs", len(aivocab.CONDITIONS), 247)
    check("distinct ids", len({c.id for c in aivocab.CONDITIONS}), 236)
    allnames = set()
    for c in aivocab.CONDITIONS:
        allnames.add(c.name)
        allnames.update(c.aliases)
    check("distinct spellings", len(allnames), 248)
    check("unnamed ids in 0..256", len(aivocab.UNNAMED_IDS), 21)
    check("alias groups", sum(1 for c in aivocab.CONDITIONS if c.aliases), 10)

    # Every spelling must resolve, and resolve to the same id it was stored with.
    bad = [n for n in allnames if aivocab.lookup(n) is None]
    check("spellings that fail lookup", len(bad), 0)
    bad = [n for n in allnames if aivocab.lookup(n).id != aivocab.condition_id(n)]
    check("lookup/condition_id disagreements", len(bad), 0)
    # Case-insensitive, because .stateb data spells HasSpecial in three cases.
    check("lookup('HASSPECIAL').id == lookup('hasspecial').id",
          aivocab.lookup("HASSPECIAL").id, aivocab.lookup("hasspecial").id)

    section("2. spot values a human can check")
    check("id 8 canonical", aivocab.name_for_condition(8), "dist")
    check("id 8 aliases", aivocab.aliases_for_condition(8), ("distance",))
    check("id 146 canonical (alias 'dir' is unused in shipped data)",
          aivocab.name_for_condition(146), "direction")
    check("id 252 (Bounty Hunter flag, per the AI-script docs)",
          aivocab.name_for_condition(252), "bountyhuntermode")
    check("id 75 creature scope", aivocab.name_for_condition(75, "creature"), "toughness")
    check("id 75 group scope", aivocab.name_for_condition(75, "group"), "playertoughness")
    check("id 64 is a gap -> None", aivocab.name_for_condition(64), None)
    check("gap id still yields a dict from annotate_condition_id",
          aivocab.annotate_condition_id(64)["name"], None)

    section("3. action opcodes")
    check("range", (aivocab.ACTION_MIN, aivocab.ACTION_MAX), (1001, 1571))
    check("slots with a real handler",
          sum(1 for o in range(1001, 1572) if aivocab.action_has_handler(o)), 296)
    check("1019 has a handler", aivocab.action_has_handler(1019), True)
    check("1022 does not", aivocab.action_has_handler(1022), False)
    check("no name is ever produced for an opcode",
          any(str(o) not in aivocab.describe_action(o) for o in (1001, 1019, 1571)),
          False)

    check("HUD command count", len(aivocab.HUD_COMMANDS), 61)
    check("HUD command 0 / 51 / 60",
          (aivocab.hud_command(0), aivocab.hud_command(51), aivocab.hud_command(60)),
          ("ShowFocusBar", "StartPuppetshowFadein", "DoneTalking"))
    check("HUD command 61 has no name", aivocab.hud_command(61), None)
    rows = aivocab.annotate([dict(off=4 * i, kind="int", value=x)
                             for i, x in enumerate((1560, 48, 48))])
    check("annotate names the argument of 1560 and nothing after it",
          (rows[1].get("hud"), rows[2].get("hud")), ("EndPuppetshowFadeout", None))

    # ------------------------------------------------- 4. re-extract from the ELF

    section("4. cross-check against the unpacked executable")
    if not os.path.exists(ELF):
        print("  [skip] %s not present" % ELF)
    else:
        img = open(ELF, "rb").read()
        check("executable size", len(img), 4497384)
        tabs = aivocab.extract_from_executable(ELF)
        check("re-extracted creature entries", len(tabs["creature"]), 199)
        check("re-extracted group entries", len(tabs["group"]), 58)

        # Compare against the bake, entry for entry, including alias membership.
        baked = collections.defaultdict(set)
        for c in aivocab.CONDITIONS:
            baked[c.scope].add((c.name, c.id))
            for a in c.aliases:
                baked[c.scope].add((a, c.id))
        for scope in ("creature", "group"):
            check("baked %s table == ELF" % scope,
                  baked[scope] == set(tabs[scope]), True)

        handlers, stub = aivocab.extract_action_handlers(ELF)
        check("ELF handler count", len(handlers), 296)
        check("do-nothing stub address", "%08X" % stub, "002DC374")
        check("baked handler set == ELF",
              handlers == {o for o in range(1001, 1572)
                           if aivocab.action_has_handler(o)}, True)

        check("baked HUD commands == ELF",
              aivocab.extract_hud_commands(ELF) == aivocab.HUD_COMMANDS, True)
        sw = struct.unpack_from("<I", img, aivocab.HUD_SWITCH_ADDR - aivocab.ELF_BASE)[0]
        check("executor bounds the command at 61 (sltiu ..., 61)",
              (sw >> 26, sw & 0xFFFF), (11, 61))

        # The negative. Condition strings are pointed at; action/state names are not.
        ptrs = []
        for addr, n in ((aivocab.CREATURE_TABLE_ADDR, 199),
                        (aivocab.GROUP_TABLE_ADDR, 58)):
            for i in range(n):
                ptrs.append(struct.unpack_from(
                    "<I", img, addr - aivocab.ELF_BASE + 8 * i)[0])
        unref = sum(1 for p in ptrs if img.count(struct.pack("<I", p)) == 0)
        check("condition strings with no pointer to them", unref, 0)
        check("'nofire' occurrences in the executable", img.count(b"nofire"), 0)

        # 'nofire' is one anecdote; this is the general form. Names taken from a
        # shipped .stateb, none of which is a pointed-at C string in the image.
        for probe in ("FireTbl_med", "AccuracyTbl_high", "bodyregiontbl_15",
                      "Dueling_DuelGP_Fire", "Peek_A_Boo"):
            check("'%s' in executable" % probe, img.count(probe.encode()), 0)


# ------------------------------------------------------ 5. re-scan the disc

def scan():
    section("5. re-derive every usage number from the disc")
    t0 = time.time()
    g = Game(ISO, writable=False)
    idx = g.build_index()
    files = sorted(fn for fn in idx if fn.lower().endswith(EXTS))
    byext = collections.Counter(os.path.splitext(f)[1].lower() for f in files)
    check("distinct behaviour filenames", len(files), 14921)
    check(".actb / .stateb (the figure the notes use)",
          byext[".actb"] + byext[".stateb"], 14385)

    hits = collections.Counter()
    ops = collections.Counter()
    unknown = collections.Counter()
    nofire_files = 0
    for fn in files:
        data = g.read(idx[fn][0], fn)
        toks = {m.group().decode("latin-1") for m in TOKEN.finditer(data)}
        if "nofire" in toks:
            nofire_files += 1
        for t in toks:
            c = aivocab.lookup(t)
            if c is not None and t.lower() == c.name.lower():
                hits[c.name] += 1
        try:
            _, _, tk = actb.parse(data, limit=200000)
        except Exception:
            continue
        for t in tk:
            if t.kind == "int" and aivocab.is_action_opcode(t.value):
                ops[t.value] += 1
            elif t.kind == "name" and not aivocab.is_condition_name(t.value):
                unknown[t.value] += 1
    g.close()

    check("files containing 'nofire'", nofire_files, 3126)
    # Entries, then names. They differ by exactly the 8 canonical spellings
    # that exist in both the creature and the group table -- worth asserting
    # separately, because conflating the two is how a "missing 8" ghost starts.
    used_entries = [c for c in aivocab.CONDITIONS if c.name in hits]
    check("canonical entries used by shipped data", len(used_entries), 208)
    check("distinct canonical names used", len(hits), 200)
    check("entries minus names == names in both scopes",
          len(used_entries) - len(hits), 8)
    never = {c.name for c in aivocab.CONDITIONS if c.name not in hits}
    check("canonical names never used", len(never), 39)
    check("NEVER_USED matches the rescan", set(aivocab.NEVER_USED) == never, True)

    # The baked per-name counts must be exactly what the disc says.
    drift = [(n, hits[n], c.shipped_files)
             for c in aivocab.CONDITIONS for n in (c.name,)
             if hits.get(n, 0) != c.shipped_files]
    check("baked shipped_files counts that drifted", len(drift), 0)
    if drift:
        print("     ", drift[:10])

    check("distinct action opcodes in shipped data", len(ops), 298)
    stubbed = sorted(o for o in ops if not aivocab.action_has_handler(o))
    check("opcode values used that hit the stub (parser noise)", len(stubbed), 10)
    print("       ", stubbed)
    print("  handlers never used by shipped data: %d"
          % sum(1 for o in range(1001, 1572)
                if aivocab.action_has_handler(o) and o not in ops))
    print("  distinct non-condition inline names: %d" % len(unknown))
    print("  scanned in %.1fs" % (time.time() - t0))

    section("most-used conditions (files containing the name)")
    for n, c in hits.most_common(15):
        e = aivocab.lookup(n)
        print("  %-24s id %-4d %-8s %6d files" % (n, e.id, e.scope, c))
    section("never used (%d)" % len(never))
    for i in range(0, len(sorted(never)), 4):
        print("  " + "  ".join("%-22s" % x for x in sorted(never)[i:i + 4]))


def show(path_or_name):
    """Annotated dump of one behaviour file, by disc filename or local path."""
    if os.path.exists(path_or_name):
        data = open(path_or_name, "rb").read()
    else:
        g = Game(ISO, writable=False)
        arcs = g.locate(path_or_name)
        if not arcs:
            sys.exit("no such file on the disc: %s" % path_or_name)
        data = g.read(arcs[0], path_or_name)
        print("# %s from %s\n" % (path_or_name, arcs[0]))
        g.close()
    print(aivocab.listing(data))
    print()
    s = aivocab.summary(data)
    print("conditions used:", ", ".join(s["distinct_conditions"]) or "(none)")
    print("action opcodes :", s["action_opcodes"])
    print("names that are not conditions:", ", ".join(s["unnamed_names"]) or "(none)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--show")
    a = ap.parse_args()
    if a.show:
        show(a.show)
        sys.exit(0)
    checks()
    if a.scan:
        scan()
    print()
    if fails:
        print("FAILED %d check(s):" % len(fails))
        for f in fails:
            print("  " + f)
        sys.exit(1)
    print("all checks passed")
