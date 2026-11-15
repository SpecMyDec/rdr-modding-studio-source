#!/usr/bin/env python3
"""M20: move a level's player spawn by a known offset, and read the result back.

    python3 scripts/m20_spawn_probe.py show   LEVEL
    python3 scripts/m20_spawn_probe.py patch  LEVEL --dy 40
    python3 scripts/m20_spawn_probe.py revert LEVEL
    python3 scripts/m20_spawn_probe.py find   STATE.p2s --level LEVEL

M20 asks which file the game ACTUALLY loads a spawn from, because the tester
found that setting it gets overridden. Static inspection got as far as: every
single-player level carries a `.spwn` whose group-1 position is the candidate
player start, and three levels carry a one-off alternate (HNG `PlayerStartPos`
in `.aisp`, GHOST `PlayerStart_Box` in `.hb`, TRAIN `PlayerSpawnHorse` in
`.enm`). Static inspection cannot say which one WINS. Only the running game
can, and per the reviewer this is settled in PCSX2 before any disassembly.

**The verification problem, and why this script exists.** `boot_test.ps1` says
plainly that PCSX2 renders through Vulkan so screenshots come back black, and
that a boot test only reaches the main menu -- an in-game level load "cannot
be automated". So "watch where the player appears" is not available to a
tool, and an eyeball verdict is not the kind of evidence this repo accepts
anyway.

`find` is the way round that. A PCSX2 savestate is a zip carrying
`eeMemory.bin`, a full 32 MB dump of EE RAM. Given two savestates taken at the
same moment -- one vanilla, one with the spawn moved by a known offset -- a
float32 triple that reads the VANILLA position in one and the PATCHED position
in the other is the player's position in RAM, found by difference rather than
by disassembly. If no such triple exists, the `.spwn` did not win, and the
same search re-run against each alternate carrier says which did.

**Why the patch preserves byte length.** A `.spwn` is plain text in a
fixed-size archive slot. Rewriting a number to the same number of BYTES keeps
the entry in its slot, so the edit needs no `grow` and no relocation -- which
also means it cannot perturb the thing being measured.

Writes nothing unless told to `patch`, and `patch` writes only into
`04_Extracted`, never to an ISO. Landing it on a disc is a separate, deliberate
step through the app's own patch path.
"""
import argparse
import os
import re
import struct
import sys
import zipfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
SNG = os.path.join(ROOT, "04_Extracted", "LVLS_SNG")

POS = re.compile(rb"(Position\s+)(-?\d+\.?\d*)(\s+)(-?\d+\.?\d*)(\s+)(-?\d+\.?\d*)")


def spwn_paths(level):
    d = os.path.join(SNG, level.upper())
    if not os.path.isdir(d):
        sys.exit("no such single-player level: %s" % level)
    return [os.path.join(d, n) for n in sorted(os.listdir(d))
            if n.lower().endswith(".spwn")]


def first_position(data):
    """(match, (x, y, z)) for the first Position after NumSpawnPoints1."""
    i = data.find(b"NumSpawnPoints1")
    if i < 0:
        return None, None
    m = POS.search(data, i)
    if not m:
        return None, None
    return m, tuple(float(m.group(n)) for n in (2, 4, 6))


def fmt_same_width(value, original):
    """`value` rendered in exactly len(original) bytes, or None if impossible.

    Byte width is the whole point -- a wider number would grow the file and
    move it out of its slot, so the probe would be measuring its own edit.
    """
    width = len(original)
    for places in range(width, -1, -1):
        s = ("%.*f" % (places, value)).encode()
        if len(s) == width:
            return s
    return None


def cmd_show(args):
    for p in spwn_paths(args.level):
        data = open(p, "rb").read()
        m, xyz = first_position(data)
        print("%-40s %s" % (os.path.basename(p),
                            "group-1: %s" % (xyz,) if m else "no group-1 point"))


def cmd_patch(args):
    for p in spwn_paths(args.level):
        data = open(p, "rb").read()
        m, xyz = first_position(data)
        if not m:
            print("%-40s skipped (no group-1 point)" % os.path.basename(p))
            continue
        bak = p + ".m20bak"
        if not os.path.exists(bak):
            open(bak, "wb").write(data)
        new_y = xyz[1] + args.dy
        rendered = fmt_same_width(new_y, m.group(4))
        if rendered is None:
            print("%-40s REFUSED: %.4f will not fit in %d bytes"
                  % (os.path.basename(p), new_y, len(m.group(4))))
            continue
        out = data[:m.start(4)] + rendered + data[m.end(4):]
        assert len(out) == len(data), "byte length changed -- refusing"
        open(p, "wb").write(out)
        print("%-40s y %s -> %s  (%d bytes, unchanged)"
              % (os.path.basename(p), m.group(4).decode(), rendered.decode(),
                 len(out)))


def cmd_revert(args):
    for p in spwn_paths(args.level):
        bak = p + ".m20bak"
        if os.path.exists(bak):
            open(p, "wb").write(open(bak, "rb").read())
            os.remove(bak)
            print("%-40s reverted" % os.path.basename(p))
        else:
            print("%-40s no backup, untouched" % os.path.basename(p))


MARK_STEP = 0.001       # metres per index: 24 points span 24 mm


GROUP = re.compile(rb"NumSpawnPoints([1-4])")


def group_of(data, pos):
    """Which NumSpawnPoints group the Position at `pos` belongs to."""
    g = None
    for m in GROUP.finditer(data):
        if m.start() > pos:
            break
        g = m.group(1).decode()
    return g


def cmd_mark(args):
    """Make every spawn point uniquely identifiable WITHOUT moving the player.

    Point i gets x nudged by (i+1) * 1 mm. y and z are left exactly as
    shipped. The player therefore spawns on the floor where it always did, at
    most 24 mm to one side, and the x in RAM names the point.

    **Why not a big marker on y, which is where this started.** Marking y with
    20, 21, 22... put the player 20-43 units above the spawn plane (every
    PERFNEW spawn y sits between -4.5 and 0), so on spawn they FALL. y then
    changes every frame and no "bank immediately" beats gravity. Worse, the
    bands were contiguous -- a body dropped from 38 passes through 37, 36,
    35 -- so a state banked slightly late decodes as a confident WRONG group,
    and which group depends on when F1 was pressed. A measurement whose error
    mode is a plausible answer rather than a null is the exact failure this
    milestone is about. Caught by rdr-app before anything reached a disc.

    x was the first fix, since a falling body's x and z are unchanged, but
    x = 20..43 sits outside PERFNEW's own footprint (x spans -12.7..12.7) and
    an out-of-bounds spawn fails in its own way. Hence millimetres:
    **uniqueness is what the readout needs, not magnitude.** 24 values a
    millimetre apart decode exactly as well as 24 a metre apart, and float32
    at |x| ~ 12.7 has an ulp near 1e-6, so a 1e-3 step is ~1000 ulps clear.

    It is also required rather than merely tidy: PERFNEW's 24 points carry only
    13 DISTINCT x values, several identical to the last digit, so copying x
    would not have identified anything.
    """
    n = 0
    legend = []
    for path in spwn_paths(args.level):
        bak = path + ".m20bak"
        if os.path.exists(bak):
            # ALREADY MARKED. Mark from the backup, not from the marked file.
            # Marking a marked file applies the nudge twice: point 0 went
            # +1 mm then +2 mm, silently, and every offset doubled while the
            # legend still said (i+1). readmark reads the file as its own
            # legend so it would still have decoded -- the numbers would have
            # been wrong and the answer right, which is worse than failing,
            # because nothing would ever have shown it.
            data = bytearray(open(bak, "rb").read())
            print("  (%s was already marked; re-marking from vanilla)"
                  % os.path.basename(path))
        else:
            data = bytearray(open(path, "rb").read())
            open(bak, "wb").write(bytes(data))
        out = bytearray()
        last = 0
        for m in POS.finditer(bytes(data)):
            x = float(m.group(2))
            marker = fmt_same_width(x + (n + 1) * MARK_STEP, m.group(2))
            if marker is None:
                print("  point %d: no %d-byte rendering of %.4f -- REFUSED, "
                      "nothing written" % (n, len(m.group(2)), x))
                return
            if abs(float(marker) - x) < MARK_STEP / 2:
                print("  point %d: %d bytes cannot hold a distinguishable "
                      "value (%s) -- REFUSED" % (n, len(m.group(2)),
                                                 marker.decode()))
                return
            out += data[last:m.start(2)] + marker
            last = m.end(2)
            legend.append((n, float(marker), group_of(bytes(data), m.start()),
                           os.path.basename(path)))
            n += 1
        out += data[last:]
        assert len(out) == len(data), "byte length changed -- refusing"
        open(path, "wb").write(bytes(out))
    xs = [v for _, v, _, _ in legend]
    print("%d points marked; %d distinct x (must equal %d)"
          % (n, len(set(xs)), n))
    for idx, x, grp, fn in legend:
        print("    x=%-12.4f point %-3d group %-4s %s" % (x, idx, grp, fn))


def _offsets(mem, x):
    pat = struct.pack("<f", x)
    out, j = [], mem.find(pat)
    while j >= 0:
        out.append(j)
        j = mem.find(pat, j + 1)
    return out


def expected_markers(level):
    """[(index, expected_x, group, file)] from VANILLA plus the intended step.

    **The legend must not come from the file it checks.** readmark used to read
    the patched .spwn as its own legend, which cannot disagree with itself: a
    doubled nudge, a partial write, a half-applied patch or a future edit with
    a different step would all decode perfectly into a confident right-shaped
    answer. Deriving expectation from the backup (vanilla) plus (i+1) mm and
    then ASSERTING the patched file matches turns every one of those into a
    loud failure before anything is read. rdr-app's point, and it is the
    mechanism rather than the cause my idempotency fix addressed.
    """
    out, n = [], 0
    for path in spwn_paths(level):
        bak = path + ".m20bak"
        if not os.path.exists(bak):
            return None
        van = open(bak, "rb").read()
        for m in POS.finditer(van):
            out.append((n, float(m.group(2)) + (n + 1) * MARK_STEP,
                        group_of(van, m.start()), os.path.basename(path)))
            n += 1
    return out


def check_against_expected(level):
    """-> (expected rows, [complaints]). Empty complaints means safe to read."""
    exp = expected_markers(level)
    if exp is None:
        return None, ["%s is not marked (no .m20bak)" % level]
    got, n, bad = [], 0, []
    for path in spwn_paths(level):
        data = open(path, "rb").read()
        for m in POS.finditer(data):
            got.append(float(m.group(2)))
            n += 1
    if len(got) != len(exp):
        return exp, ["point count changed: %d on disk, %d expected"
                     % (len(got), len(exp))]
    for (i, want, _g, fn), have in zip(exp, got):
        if abs(have - want) > MARK_STEP / 10:
            bad.append("point %d in %s: file has %.6f, expected %.6f "
                       "(vanilla + %d mm)" % (i, fn, have, want, i + 1))
    return exp, bad


def cmd_readmark(args):
    """Which marker x is resident -> which point the game used.

    The patched .spwn files ARE the legend, so this reads them rather than
    recomputing. If the level is not currently marked there is nothing to look
    for, and it says so instead of reporting a confident nothing.
    """
    paths = spwn_paths(args.level)
    exp, bad = check_against_expected(args.level)
    if bad:
        print("REFUSING TO READ -- the marked files do not match expectation:")
        for b in bad:
            print("    %s" % b)
        print("  Expectation is derived from the .m20bak (vanilla) plus the")
        print("  intended (i+1) mm step, NOT from the patched file, so this is")
        print("  a real disagreement rather than a self-check. Re-mark before")
        print("  reading anything.")
        return 1
    print("legend derived from vanilla + (i+1) mm; %d points verified against "
          "the marked files" % len(exp))
    mem = ee_memory(args.state)
    top = loaded_levels(mem)
    print("resident level names: %s" % ", ".join("%s x%d" % t for t in top))
    if top and top[0][0] == "lvl_front":
        print("  *** MAIN MENU state -- no level loaded, this proves nothing.")
    print()
    hits = []
    for idx, x, grp, fn in exp:
        c = len(_offsets(mem, x))
        if c:
            hits.append((idx, x, grp, fn, c))
    n = len(exp)
    if not hits:
        print("NO MARKER RESIDENT across %d points." % n)
        print("  This does NOT establish that the .spwn was unused. Matching is")
        print("  EXACT, and markers sit 1 mm apart, so a body that settled or")
        print("  was nudged by collision loses the match entirely. \"Used, then")
        print("  the player moved\" and \"never used\" look identical here.")
        print("  Distinguish with the spawn TABLE: if the unmarked coordinates")
        print("  are resident but no marker is, the file was read. If nothing")
        print("  from the file is resident at all, it was not.")
        return
    objs = walk_spawn_objects(mem)
    spans = [table_span(o) for o, _ in objs]
    if objs:
        print("  spawn list: %d object(s); counts %s"
              % (len(objs), [c for _, c in objs]))
        print("  -- verify those counts against the file's own "
              "NumSpawnPoints before believing anything below --")
    else:
        print("  spawn list is empty or NULL: hits cannot be classified as")
        print("  table-vs-elsewhere, so treat every one of them as unattributed.")
    print()
    for idx, x, grp, fn, c in hits:
        where = []
        pat = struct.pack("<f", x)
        j = mem.find(pat)
        while j >= 0:
            where.append("table" if any(lo <= j < hi for lo, hi in spans)
                         else "0x%08X" % j)
            j = mem.find(pat, j + 1)
        print("  point %-3d group %-4s x=%-12.4f %-24s %d copies  %s"
              % (idx, grp, x, fn, c, ", ".join(where[:6])))
    outside = [h for h in hits
               if any(not any(lo <= j < hi for lo, hi in spans)
                      for j in _offsets(mem, h[1]))]
    print()
    if spans and outside:
        print("  %d marker(s) have a copy OUTSIDE the parsed records." % len(outside))
        print("  That proves the value was COPIED somewhere live. It does NOT")
        print("  prove the destination is the player: a camera target, a")
        print("  checkpoint, a respawn cache or a non-player actor all look")
        print("  identical at this resolution -- one float32 in a struct we")
        print("  cannot name. Read it as a CANDIDATE pending corroboration.")
        print()
        print("  Actor check on each outside copy (A-1464+0xA8 must read 1..16):")
        for h in outside:
            for j in _offsets(mem, h[1]):
                if any(lo <= j < hi for lo, hi in spans):
                    continue
                ok, raw, band = actor_check(mem, j)
                print("    point %-3d @0x%08X  actor+0xA8 = %-10s %s"
                      % (h[0], j,
                         ("0x%08X" % raw) if raw is not None else "unreadable",
                         ("PLAUSIBLE actor, band %d -> should have used group %d"
                          % (band, band)) if ok else
                         "NOT an actor position field"))
        print()
        print("  A plausible hit closes the loop: the band names the group the")
        print("  engine should have used, comparable against which marker")
        print("  actually landed there. A disagreement is the finding.")
    elif spans:
        print("  Every hit lies inside the parsed records, so all of them are")
        print("  the spawn table. Nothing here locates the player.")
    if len(hits) > 1:
        print("\n  More than one marker resident -- the spawn table itself is")
        print("  in RAM, so a hit is not proof the PLAYER is at that point.")
        print("  Distinguish by copy count and by which value the player")
        print("  object carries; do not read a single hit as the answer.")


def ee_memory(path):
    with zipfile.ZipFile(path) as z:
        return z.read("eeMemory.bin")


def find_triples(mem, xyz, tol=0.01):
    """Offsets where three consecutive float32 read approximately xyz."""
    hits = []
    want = struct.pack("<f", xyz[0])[2:]      # coarse prefilter on the high bytes
    start = 0
    while True:
        i = mem.find(want, start)
        if i < 0:
            break
        off = i - 2
        if off >= 0 and off + 12 <= len(mem):
            try:
                got = struct.unpack_from("<fff", mem, off)
                if all(abs(a - b) <= tol for a, b in zip(got, xyz)):
                    hits.append((off, got))
            except struct.error:
                pass
        start = i + 1
    return hits


def loaded_levels(mem):
    """Which `lvl_*` names are resident, most frequent first.

    A savestate taken at the MAIN MENU holds the whole level LIST, so a name
    being present proves nothing on its own -- but `lvl_front` dominating is
    the menu's signature, because the main menu is itself a level (M17).
    """
    import collections
    names = collections.Counter(
        m.group(0).decode() for m in re.finditer(rb"lvl_[a-z0-9_]{2,20}", mem))
    return names.most_common(6)


def cmd_find(args):
    mem = ee_memory(args.state)
    print("eeMemory.bin: %d bytes" % len(mem))
    top = loaded_levels(mem)
    print("resident level names: %s" % ", ".join("%s x%d" % t for t in top))
    menu = top and top[0][0] == "lvl_front"
    if menu:
        print()
        print("  *** This looks like a MAIN MENU state (lvl_front dominates).")
        print("  *** A level's spawn being absent here means nothing at all --")
        print("  *** the level is not loaded. Take the state AFTER a level")
        print("  *** loads, or this run proves nothing in either direction.")
    print()
    for p in spwn_paths(args.level):
        m, xyz = first_position(open(p, "rb").read())
        if not m:
            continue
        hits = find_triples(mem, xyz)
        print("%-40s %s -> %d resident copies"
              % (os.path.basename(p), xyz, len(hits)))
        for off, got in hits[:8]:
            print("    0x%08X  %s" % (off, got))
        if not hits:
            if menu:
                print("    not resident -- EXPECTED, the level is not loaded")
            else:
                print("    NOT RESIDENT while this level appears loaded, which")
                print("    IS evidence the .spwn value is not what was used.")
                print("    Re-run against the alternate carriers before")
                print("    concluding: absence here names no winner by itself.")


POS_OFF = 1464            # addiu a1, s1, 1464 at 0x00357A4C  (0x05B8)
ROT_OFF = 1476            # addiu a2, s1, 1476 at 0x00357A54  (0x05C4)
BAND_OFF = 0xA8           # actor->0xA8, the field the group dispatch reads


def band_of(v):
    """The jump table at 0x005218B0: 1-4 -> 1, 5-8 -> 2, 9-12 -> 3, 13-16 -> 4."""
    if not 1 <= v <= 16:
        return None
    return (v - 1) // 4 + 1


def actor_check(mem, addr):
    """Is `addr` plausibly an actor's position field? -> (ok, raw, band).

    Two-sided, and rdr-app's idea. At 0x00357A40 the SAME s1 supplies the
    actor to the group dispatch (daddu a0, s1, zero) and the position and
    rotation pointers (addiu a1, s1, 1464; addiu a2, s1, 1476). So if a marker
    value sits at A and A really is an actor's position, then A - 1464 is the
    actor and A - 1464 + 0xA8 is the field the dispatch reads to pick a group.

    That word must read 1..16 -- the jump table's populated range. Anything
    else and A is not a player position field. It fails LOUDLY rather than
    agreeing quietly, which a proximity heuristic would not.

    It also closes the loop: the band this returns is the group the engine
    SHOULD have used, comparable against which marker actually landed there.
    """
    base = addr - POS_OFF
    if base < 0 or base + BAND_OFF + 4 > len(mem):
        return False, None, None
    raw = struct.unpack_from("<I", mem, base + BAND_OFF)[0]
    b = band_of(raw)
    return b is not None, raw, b


SPAWN_LIST = 0x00760CF8   # global head; verified at 0x003563C8 (lui 0x0076 +3320)
REC_BASE = 36             # records start at object+36 (addiu a3, s0, 36)
GROUP_STRIDE = 360        # addiu t1, zero, 360
POINT_STRIDE = 36         # addiu t2, zero, 36
COUNTS_OFF = 8            # addiu t0, s0, 8 -- four u32 group counts


def walk_spawn_objects(mem):
    """[(object address, [4 group counts])] from the global spawn list.

    PS2 RAM maps from zero, so a console address IS an offset into
    eeMemory.bin. rdr-app found the list; the layout constants come from the
    parser at 0x00354E44, which I disassembled myself.

    **Untested machinery.** The global has been observed reading NULL with no
    level loaded -- a labelled negative. Nobody has yet watched it hold a
    valid pointer and walk to a real object, so this has no positive control
    until a real in-level state arrives. It validates itself in that same
    moment: the counts at object+8 must equal what the .spwn file declares,
    and for PERFNEW that is (6, 6, 6, 6). If they do not match, the walk is
    wrong and nothing downstream of it should be believed however sensible it
    looks.
    """
    if len(mem) <= SPAWN_LIST + 4:
        return []
    head = struct.unpack_from("<I", mem, SPAWN_LIST)[0]
    out, node, seen = [], head, set()
    while node and node not in seen and node + 8 <= len(mem):
        seen.add(node)
        obj = struct.unpack_from("<I", mem, node)[0]
        nxt = struct.unpack_from("<I", mem, node + 4)[0]
        if obj and obj + COUNTS_OFF + 16 <= len(mem):
            counts = list(struct.unpack_from("<4I", mem, obj + COUNTS_OFF))
            out.append((obj, counts))
        node = nxt
        if len(out) > 64:
            break
    return out


def table_span(obj):
    """(start, end) of an object's parsed point records."""
    return obj + REC_BASE, obj + REC_BASE + 4 * GROUP_STRIDE


def cmd_walk(args):
    """Walk the global spawn list in a savestate and print what it holds."""
    mem = ee_memory(args.state)
    head = struct.unpack_from("<I", mem, SPAWN_LIST)[0]
    print("global spawn list head @ 0x%08X = 0x%08X" % (SPAWN_LIST, head))
    if head == 0:
        print("  NULL -- no spawn object registered, consistent with no level")
        print("  loaded. This is a labelled NEGATIVE: it confirms the address")
        print("  maps without translation, and proves nothing about a loaded")
        print("  level, because the walk has never been seen to succeed.")
        return
    objs = walk_spawn_objects(mem)
    print("  %d object(s)" % len(objs))
    for obj, counts in objs:
        lo, hi = table_span(obj)
        print("    object 0x%08X  counts %s  records 0x%08X..0x%08X"
              % (obj, counts, lo, hi))


def cmd_selftest(args):
    """Prove the matcher can tell 1 mm apart, against a synthetic RAM buffer.

    Exists because this check has the property that made my earlier ones
    worthless: it cannot fail visibly in a dry run. One marker in an otherwise
    clean file matches under ANY tolerance, so a too-wide window only shows up
    when 24 markers sit a millimetre apart -- i.e. during the one measurement
    that matters. rdr-app raised exactly that: a +/-0.01 window spans +/-10
    markers and would name a point, confidently, at random.

    So: plant ONE marker in noise and require exactly one point to match, the
    right one. A checker that has never been observed to succeed is not
    evidence when it fails.
    """
    import random
    paths = spwn_paths(args.level)
    marked = [(float(m.group(2)), i)
              for p in paths
              for i, m in enumerate(POS.finditer(open(p, "rb").read()))]
    if not any(os.path.exists(p + ".m20bak") for p in paths):
        print("%s is not marked -- run `mark` first, the test needs the "
              "marker values" % args.level)
        return
    xs = [x for x, _ in marked]
    print("%d points, %d distinct x, tightest gap %.6f m"
          % (len(xs), len(set(xs)),
             min(abs(a - b) for i, a in enumerate(sorted(xs))
                 for b in sorted(xs)[i + 1:i + 2]) if len(xs) > 1 else 0))
    random.seed(20)
    fails = 0
    for target in (0, len(xs) // 2, len(xs) - 1):
        mem = bytearray(random.getrandbits(8) for _ in range(4096))
        struct.pack_into("<f", mem, 2048, xs[target])
        matched = [i for i, x in enumerate(xs)
                   if bytes(mem).find(struct.pack("<f", x)) >= 0]
        ok = matched == [target]
        fails += not ok
        print("  planted point %-3d -> matched %-20s %s"
              % (target, matched, "ok" if ok else "FAIL"))
    print("selftest: %s" % ("PASS -- exact matching separates 1 mm neighbours"
                            if not fails else "FAIL"))
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("show"); s.add_argument("level"); s.set_defaults(fn=cmd_show)
    s = sub.add_parser("patch"); s.add_argument("level")
    s.add_argument("--dy", type=float, default=40.0); s.set_defaults(fn=cmd_patch)
    s = sub.add_parser("revert"); s.add_argument("level"); s.set_defaults(fn=cmd_revert)
    s = sub.add_parser("mark"); s.add_argument("level"); s.set_defaults(fn=cmd_mark)
    s = sub.add_parser("readmark"); s.add_argument("state")
    s.add_argument("--level", required=True)
    s.set_defaults(fn=cmd_readmark)
    s = sub.add_parser("walk"); s.add_argument("state"); s.set_defaults(fn=cmd_walk)
    s = sub.add_parser("selftest"); s.add_argument("level")
    s.set_defaults(fn=cmd_selftest)
    s = sub.add_parser("find"); s.add_argument("state")
    s.add_argument("--level", required=True); s.set_defaults(fn=cmd_find)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
