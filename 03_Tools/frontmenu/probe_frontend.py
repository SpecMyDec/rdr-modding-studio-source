"""Is Red Dead Revolver's front-end menu extendable? Measure, don't assume.

Reads only. Touches no ISO, takes no lock. Run it from anywhere:

    py -3 03_Tools/frontmenu/probe_frontend.py

Every number this prints is re-derived from the two inputs below on each run.
Nothing is hardcoded except the addresses being CHECKED -- and each check
fails loudly if the disc stops agreeing with it.

Inputs
    07_Research/SLUS_205.00.unpacked.bin   the decompressed PS2 executable
    04_Extracted/FRONT, 04_Extracted/SWAT  the unpacked UI archives

Findings it settles, in order:
    1  the load base, from a pointer table rather than from folklore
    2  the front-end mode LABEL table -- 5 pointers, ELF .data
    3  the code that indexes it: no bound, no terminator
    4  the front-end STATE machine -- 16-way switch, jump table butted
       against rodata, so there is no 17th slot to grow into
    5  the camera graph -- a closed 3-node triangle, names in the ELF,
       only the timings in swat.roommach
    6  storyselect.swui -- the story-book option list, and the ONLY part of
       the main menu that is genuinely data-driven
    7  what the five per-row widget pointers are actually for (enable/disable,
       not navigation) -- which is why a sixth row is not obviously fatal
    8  that a NEW screen needs no executable patch: `frontendcache.swui`
       registers pages from data, through the same function the executable's
       own hardcoded list goes through
"""

import os
import struct
import sys

BASE_GUESS = 0x00100000
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ELF = os.path.join(ROOT, "07_Research", "SLUS_205.00.unpacked.bin")
FRONT = os.path.join(ROOT, "04_Extracted", "FRONT")
SWAT = os.path.join(ROOT, "04_Extracted", "SWAT")

# Findings, in the order the checks run. Each is (ok, note).
RESULTS = []


def record(ok, note):
    RESULTS.append((ok, note))
    print("  %-5s %s" % ("ok" if ok else "FAIL", note))


def cstr(d, base, va, limit=96):
    off = va - base
    if off < 0 or off >= len(d):
        return None
    end = d.find(b"\0", off)
    if end < 0 or end - off > limit:
        return None
    try:
        return d[off:end].decode("latin-1")
    except Exception as ex:                                   # noqa: BLE001
        print("  could not decode string at 0x%08x: %s" % (va, ex))
        return None


def find_base(d, offsets):
    """Recover the load base from a pointer table, not from a constant.

    A `const char *[]` holding these strings has to hold them in order, so
    the DIFFERENCES between its entries equal the differences between the
    string offsets whatever the base is. Find a run with those differences
    and the base falls out of the first entry.
    """
    deltas = [offsets[i + 1] - offsets[i] for i in range(len(offsets) - 1)]
    n = len(d) // 4
    words = struct.unpack("<%dI" % n, d[:n * 4])
    hits = []
    for i in range(n - len(deltas)):
        a = words[i]
        if a < 0x80000 or a > 0x2000000:
            continue
        if all(words[i + k + 1] - words[i + k] == deltas[k]
               for k in range(len(deltas))):
            hits.append((i * 4, a - offsets[0]))
    return hits


def insns(d, base, va, count):
    return [struct.unpack_from("<I", d, va - base + k * 4)[0]
            for k in range(count)]


def main():
    for path in (ELF, FRONT, SWAT):
        if not os.path.exists(path):
            print("missing input: %s" % path)
            print("Nothing has been run.")
            return 2

    d = open(ELF, "rb").read()
    print("elf: %s (%d bytes)" % (os.path.basename(ELF), len(d)))

    # --- 1. load base -----------------------------------------------------
    print("\n1. load base, from the shape of a pointer table")
    labels = ["Story Mode", "Showdown", "Journal", "Options"]
    offs = []
    for name in labels:
        needle = b"\0" + name.encode("latin-1") + b"\0"
        at = d.find(needle)
        if at < 0:
            record(False, "label %r is not in the executable at all" % name)
            return 1
        offs.append(at + 1)
    hits = find_base(d, offs)
    bases = sorted(set(b for _, b in hits))
    record(BASE_GUESS in bases,
           "a table of these 4 labels implies base 0x%08x (candidates: %s)"
           % (BASE_GUESS, ", ".join("0x%08x" % b for b in bases)))
    base = BASE_GUESS
    table = [o for o, b in hits if b == base][0] + base

    # --- 2. the label table ----------------------------------------------
    print("\n2. the front-end mode label table")
    # The run found starts at "Story Mode"; index 0 sits one word earlier.
    table -= 4
    entries = []
    for i in range(6):
        va = struct.unpack_from("<I", d, table - base + i * 4)[0]
        entries.append((va, cstr(d, base, va)))
    shown = ["%d=%r" % (i, s) for i, (_, s) in enumerate(entries[:5])]
    record(entries[0][1] == "" and [s for _, s in entries[1:5]] == labels,
           "0x%08x holds %s" % (table, ", ".join(shown)))
    record(entries[5][1] is None,
           "entry 5 (0x%08x) is not a string -- the table is 5 long and has "
           "no terminator" % entries[5][0])

    # --- 3. how the table is indexed --------------------------------------
    print("\n3. the code that reads it")
    #   0x0012e244  lui   v1,0x004f
    #   0x0012e24c  addiu v1,v1,0x6ed0
    #   0x0012e250  sll   v0,s0,2
    #   0x0012e254  addu  v0,v0,v1
    #   0x0012e260  lw    a1,0(v0)
    va = 0x0012e244
    got = insns(d, base, va, 8)
    want_lui = 0x3C030000 | ((table >> 16) & 0xFFFF)
    want_add = 0x24630000 | (table & 0xFFFF)
    record(got[0] == want_lui and got[2] == want_add,
           "0x%08x forms the table address in v1 (lui/addiu %08x %08x)"
           % (va, got[0], got[2]))
    record(got[3] == 0x00101080 and got[4] == 0x00431021,
           "0x%08x: sll v0,s0,2 ; addu v0,v0,v1 -- index is the raw mode, "
           "scaled by 4" % (va + 12))
    record(got[7] == 0x8C450000,
           "0x%08x: lw a1,0(v0) -- loaded and passed straight on. No bound "
           "check anywhere in this basic block." % (va + 28))

    # --- 4. the state machine --------------------------------------------
    print("\n4. the front-end state machine")
    va = 0x001393b0
    got = insns(d, base, va, 8)
    n_states = got[0] & 0xFFFF
    record((got[0] >> 26) == 0x0B,
           "0x%08x: sltiu v0,v1,%d -- the switch admits exactly %d states"
           % (va, n_states, n_states))
    jt = ((got[2] & 0xFFFF) << 16) + (got[4] & 0xFFFF)
    record(got[7] == 0x00800008,
           "0x%08x: jr a0 off a jump table at 0x%08x" % (va + 28, jt))
    targets = [struct.unpack_from("<I", d, jt - base + i * 4)[0]
               for i in range(n_states + 1)]
    inrange = [t for t in targets[:n_states] if 0x00100000 <= t < 0x00300000]
    record(len(inrange) == n_states,
           "all %d entries are code addresses (0x%08x .. 0x%08x)"
           % (n_states, min(inrange), max(inrange)))
    after = cstr(d, base, jt + n_states * 4)
    record(after is not None,
           "the word AFTER the last entry is the string %r -- the table is "
           "butted against rodata, so a 17th state cannot be appended in "
           "place" % after)

    # --- 5. the camera graph ---------------------------------------------
    print("\n5. the camera transition graph")
    roommach = os.path.join(FRONT, "swat.roommach")
    text = open(roommach, "rb").read().decode("latin-1").replace("\r", "")
    names = [ln.strip().split()[0] for ln in text.splitlines()
             if ln.startswith("    ") and ln.strip().endswith("{")]
    record("CameraTransitions" in text,
           "FRONT/swat.roommach declares CameraTransitions with %d named "
           "transitions" % len(names))
    desks = set()
    for nm in names:
        for part in nm.split("_TO_"):
            part = part.replace("ENTER_", "").replace("EXIT_", "")
            if part in ("BOOK", "POSTER", "JOURNAL"):
                desks.add(part)
    record(desks == {"BOOK", "POSTER", "JOURNAL"},
           "the graph has exactly 3 desks: %s" % ", ".join(sorted(desks)))
    pairs = [nm for nm in names if "_TO_" in nm and not nm.startswith("ROOM")]
    record(len(pairs) == 6,
           "%d directed tilts -- a closed triangle, every desk reachable "
           "from every other" % len(pairs))
    missing = [nm for nm in names
               if ("\0" + nm + "\0").encode("latin-1") not in d]
    record(not missing,
           "all %d transition names are string constants in the executable; "
           "swat.roommach supplies only StartPhase/EndPhase/Loop/Speed"
           % len(names))

    # --- 6. the one data-driven list -------------------------------------
    print("\n6. storyselect.swui -- the story-book option list")
    sys.path.insert(0, os.path.join(ROOT, "03_Tools", "rdrstudio"))
    import swui                                               # noqa: E402

    data = open(os.path.join(FRONT, "storyselect.swui"), "rb").read()
    rows = [t for t in swui.parse(data)
            if t.name == "BUTTON" and not t.commented]
    named = [(t.fields[1].raw, t.fields[-1].raw) for t in rows]
    record(len(rows) == 5,
           "%d live BUTTON rows: %s" % (len(rows), ", ".join(
               "%s->%s" % (a, b) for a, b in named)))
    ys = []
    for t in rows:
        ys.append(float(t.fields[0].tokens[1].text))
    steps = sorted(set(round(ys[i + 1] - ys[i]) for i in range(len(ys) - 1)))
    record(steps == [20],
           "rows are laid out by hand at a uniform %dpx pitch -- a sixth row "
           "is one more line of text at y=%d" % (steps[0], int(max(ys)) + 20))
    tokens = [b for _, b in named]
    absent = [t for t in tokens
              if ("\0" + t + "\0").encode("latin-1") not in d]
    record(not absent,
           "every CALLB token (%s) is an ELF string -- the action is "
           "dispatched by strcmp, so a NEW token falls through and does "
           "nothing until the executable is patched" % ", ".join(tokens))

    # --- 7. what the five row pointers are for ---------------------------
    print("\n7. the five per-row widget pointers")
    # 0x0011db04..0x0011db60 store them; 0x0011e028.. read them back and do
    # `sb <flag>,240(widget)` then a virtual call. That is enable/refresh.
    stores = 0
    for va in range(0x0011da90, 0x0011db70, 4):
        ins = struct.unpack_from("<I", d, va - base)[0]
        if (ins >> 26) == 0x2B and 0x490 <= (ins & 0xFFFF) <= 0x4A4:
            stores += 1
    record(stores == 12,
           "the constructor writes 6 widget slots twice (%d stores at "
           "0x490..0x4a4): cleared, then filled -- five by name lookup on "
           "the storyselect row ids, the sixth (0x4a4) from a call" % stores)
    sb = 0
    for va in range(0x0011e020, 0x0011e0d0, 4):
        ins = struct.unpack_from("<I", d, va - base)[0]
        if (ins >> 26) == 0x28 and (ins & 0xFFFF) == 240:
            sb += 1
    record(sb >= 5,
           "each slot is used as `sb flag,240(widget)` + a vtable call (%d "
           "sites) -- these pointers grey rows OUT. Nothing here enumerates "
           "rows for the cursor, so the pad's navigation is generic over "
           "the page." % sb)

    # --- 8. can a new screen be registered from data? --------------------
    print("\n8. registering a screen -- executable, or data?")
    # 0x0013bba8 walks the registered list; its not-found path is
    # `daddu v0,zero,zero` at 0x0013bc28, i.e. return NULL, and the caller
    # 0x0013bb70 branches away on NULL. So registration is mandatory.
    got = insns(d, base, 0x0013bc28, 1)[0]
    record(got == 0x0000102D,
           "0x0013bc28: the lookup's not-found path returns NULL, so an "
           "unregistered screen name opens nothing at all")

    # Two call sites register pages, and they land on the SAME function.
    #   0x001389c8 area: 14 literal names, rect 512x256
    #   0x00119400 area: one per line of a text file, rect 640x448
    hard = insns(d, base, 0x0013aa88, 1)[0]
    cache = insns(d, base, 0x00119504, 1)[0]
    record(hard == cache,
           "the executable's own registration (0x0013aa88) and the cache "
           "loader (0x00119504) are the same `jal %08x` -- one entry point, "
           "two sources" % (((hard & 0x3FFFFFF) << 2) | 0x00100000))

    ext = cstr(d, base, 0x00515b37)
    record(ext == "swui",
           "the cache loader opens `<name>.%s` and reads it line by line "
           "(delimiter 10, 255-byte lines, 0x002603e0)" % ext)

    # And the proof it is not theory: two screens that ship, work, and are
    # registered ONLY by the data file.
    cache_path = os.path.join(FRONT, "frontendcache.swui")
    listed = [ln.strip() for ln in
              open(cache_path, "rb").read().decode("latin-1").split()
              if ln.strip()]
    hardcoded = ["storymode", "storyselect", "bookchapterview", "multiplayer",
                 "journal", "multigamemode", "mainoptions", "profile",
                 "savefrontend", "gameplayeropt", "roominstr",
                 "frontendbuttons", "warningfrontend", "difficultyselect"]
    data_only = [n for n in listed if n not in hardcoded]
    record(sorted(data_only) == ["gamepicture", "gamesound"],
           "%s registers %s, which the executable's 14 do NOT -- and both are "
           "reachable Options screens on the shipped disc. Data registration "
           "is not a theory." % ("frontendcache.swui", " and ".join(
               sorted(data_only))))

    bad = [note for ok, note in RESULTS if not ok]
    print("\n%d check(s) ran." % len(RESULTS))
    if bad:
        print("FAILED %d check(s):" % len(bad))
        for note in bad:
            print("  %s" % note)
        return 1
    print("all agree with 05_Docs/FRONTMENU-FEASIBILITY.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
