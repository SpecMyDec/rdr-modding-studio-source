"""Does rdrstudio/repack.py produce a correct archive, and does
test_repack_all.py's new spot-check actually catch anything?

Neither question needs an emulator, and neither is allowed to write to a disc:
`apply()` returns bytes, so a repacked archive can be parsed and read back
entirely in memory and compared against the vanilla ISO. Nothing here opens
anything writable.

What that can and cannot settle:

  * CORRECT it can settle. Every file must come back byte-identical through
    the rebuilt TOC, every destination must be inside the archive and outside
    the header, and no two blocks may overlap. A packer bug fails these.
  * SAFE it cannot. SWAT.DAT's freezing layout passed exactly these checks --
    47,115 of 47,115 files read back perfectly -- because logo01.pck is DMA'd
    off the disc rather than read through the TOC. That is why the pinning
    assertion below is worth as much as the read-back one: it is the only part
    of this file that tests the thing that actually broke.

The numbers are asserted against what the emulator runs already established
(FRONT.DAT 315 of 317 blocks, 211,280 bytes; SWAT.DAT 47,099 of 47,101 and
12.95 MB with logo01.pck landing on a sector), so a change in the packer that
silently alters the layout fails here rather than in PCSX2 twenty minutes
later. The disc-wide total, 38,023,744 bytes over 66 archives, is asserted the
same way.

    python scripts/verify_repack.py                a few seconds
    python scripts/verify_repack.py --swat-apply   also rebuild SWAT.DAT's
                                                   290 MB in memory

Reads 01_Vanilla and nothing else. It never opens the test ISO, so it is safe
to run while an emulator experiment is using it.
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from rdrstudio.dave import Dave, SECTOR, SECTOR_PINNED   # noqa: E402
from rdrstudio import config                         # noqa: E402
from rdrstudio.game import Game                          # noqa: E402
from rdrstudio import dave as dave_mod                   # noqa: E402
from rdrstudio import repack                             # noqa: E402
from rdrstudio.iso9660 import Iso9660                    # noqa: E402

import test_repack_all as tra                            # noqa: E402

# From config, not a `D:\` literal: `config.ROOT` derives from the
# module's own location, so this also stops the script being
# Windows-only. REQ-035 / REQ-059.
VANILLA = Path(config.VANILLA_ISO)

# From the emulator runs. Any change to these means the layout changed, which
# means the boot results no longer describe what the packer produces.
KNOWN = {
    "FRONT.DAT": dict(blocks=317, moved=315, reclaimed=211280),
    "SWAT.DAT": dict(blocks=47101, moved=47099, reclaimed=13577840),
}

# Sum over all 66 archives of the vanilla disc, measured. Asserted exactly
# rather than as "more than X" so that a packer change that quietly reclaims
# less shows up here, and because the number itself is the answer to "how much
# room is there for new content" -- 36.26 MB, of which SWAT.DAT is 13.58 and
# STREAMS.DAT 6.43.
DISC_TOTAL = 38023744


class BytesBacking:
    """Read-only Dave backing over a bytes object, so a repack can be parsed
    without writing it to a disc first."""

    def __init__(self, data):
        self.data = data
        self.size = len(data)
        self.writable = False

    def read_at(self, offset, length):
        return bytes(self.data[offset:offset + length])

    def write_at(self, offset, data):
        raise IOError("read-only")

    def flush(self):
        pass

    def close(self):
        pass


class FlippedIso:
    """A real ISO with one byte lied about, to prove a check can fail."""

    def __init__(self, iso, at):
        self._iso = iso
        self._at = at

    def __getattr__(self, name):
        return getattr(self._iso, name)

    def read_at(self, offset, length):
        data = bytearray(self._iso.read_at(offset, length))
        if offset <= self._at < offset + length:
            data[self._at - offset] ^= 0xFF
        return bytes(data)


def plan_invariants(dv, plan):
    """Everything that must hold of a layout regardless of which archive it is.

    Returns a list of complaints; empty is a pass.
    """
    bad = []
    floor = SECTOR + dv.info_size + dv.name_size
    covered = {b.src for b in plan.blocks}
    for e in dv.entries:
        if e.offset not in covered:
            bad.append("entry %s at 0x%X is not in the plan" % (e.name, e.offset))
            break
    prev_end = 0
    # Sorted by (dst, size) so a zero-length block sharing a destination with
    # the block after it does not read as an overlap: it occupies nothing.
    for b in sorted(plan.blocks, key=lambda x: (x.dst, x.size)):
        if b.dst < floor:
            bad.append("block 0x%X lands at 0x%X, inside the header/TOC/names"
                       % (b.src, b.dst))
        if b.dst + b.size > plan.size:
            bad.append("block 0x%X runs past the archive end" % b.src)
        if b.dst < prev_end:
            bad.append("block 0x%X at 0x%X overlaps the previous block (ends "
                       "0x%X)" % (b.src, b.dst, prev_end))
        if b.dst % repack.ALIGN:
            bad.append("block 0x%X lands at 0x%X, not %d-aligned"
                       % (b.src, b.dst, repack.ALIGN))
        if b.pinned and b.dst % SECTOR:
            bad.append("PINNED block 0x%X lands at 0x%X, off-sector"
                       % (b.src, b.dst))
        prev_end = b.dst + b.size
    if plan.end + plan.reclaimed != plan.size:
        bad.append("reclaimed %d + end %d != size %d"
                   % (plan.reclaimed, plan.end, plan.size))
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--swat-apply", action="store_true",
                    help="also rebuild SWAT.DAT in memory (290 MB, slow)")
    args = ap.parse_args()

    game = Game(VANILLA, writable=False)
    ok = True

    def check(what, good, detail=""):
        nonlocal ok
        ok &= bool(good)
        print("  %-54s %s%s" % (what, "ok" if good else "FAIL",
                                ("  " + detail) if detail else ""))

    # ------------------------------------------------------- FRONT.DAT, fully
    print("FRONT.DAT -- plan, apply, and read every file back")
    dv = game.archive("FRONT.DAT")
    raw = dv.backing.read_at(0, dv.size)
    p = repack.plan(dv)
    k = KNOWN["FRONT.DAT"]
    print("  %d files, %d bytes, %d blocks, %d moved, %d reclaimed (%.1f KB)"
          % (dv.count, dv.size, len(p.blocks), p.moved, p.reclaimed,
             p.reclaimed / 1024.0))
    check("block count matches the booted layout", len(p.blocks) == k["blocks"],
          "%d vs %d" % (len(p.blocks), k["blocks"]))
    check("moved count matches", p.moved == k["moved"],
          "%d vs %d" % (p.moved, k["moved"]))
    check("reclaimed bytes match", p.reclaimed == k["reclaimed"],
          "%d vs %d" % (p.reclaimed, k["reclaimed"]))
    check("headroom() agrees with the plan", repack.headroom(dv) == p.reclaimed)
    problems = plan_invariants(dv, p)
    check("layout invariants hold", not problems,
          problems[0] if problems else "%d blocks" % len(p.blocks))

    packed = repack.apply(dv, p, raw)
    check("archive size unchanged", len(packed) == dv.size,
          "%d" % len(packed))
    head = SECTOR + dv.info_size + dv.name_size
    check("header, TOC block and name block region kept",
          packed[:SECTOR] == raw[:SECTOR]
          and packed[SECTOR + dv.count * 16:head] == raw[SECTOR + dv.count * 16:head])
    check("apply is deterministic", repack.apply(dv, p, raw) == packed)

    rebuilt = Dave(BytesBacking(packed), label="FRONT.DAT")
    check("the repacked archive re-parses", rebuilt.count == dv.count,
          "%d entries" % rebuilt.count)
    wrong, compared = [], 0
    for e in dv.entries:
        a = dv.read(e)
        b = rebuilt.read(e.name)
        compared += 1
        if a != b:
            wrong.append(e.name)
    check("every file reads back byte-identically", not wrong,
          "%d compared, %d wrong" % (compared, len(wrong)))
    moved_now = sum(1 for e, f in zip(dv.entries, rebuilt.entries)
                    if e.offset != f.offset)
    check("the TOC really points somewhere new", moved_now > 300,
          "%d of %d entries have a new offset" % (moved_now, dv.count))

    before = dv.largest_gap()[1]
    after = rebuilt.largest_gap()[1]
    check("the free run grew (this is the whole point)",
          after >= p.reclaimed > before,
          "largest gap %d bytes -> %d bytes" % (before, after))

    # ------------------------------------------------------ keep_first, pins
    print("\nkeep_first and pinning")
    p100 = repack.plan(dv, keep_first=100)
    frozen = [b for b in p100.blocks[:100] if b.moved]
    check("keep_first=100 leaves the first 100 blocks alone", not frozen,
          "%d blocks moved" % len(frozen))
    check("keep_first reclaims less than a full repack",
          0 <= p100.reclaimed < p.reclaimed,
          "%d vs %d" % (p100.reclaimed, p.reclaimed))
    check("keep_first invariants hold", not plan_invariants(dv, p100))
    pall = repack.plan(dv, keep_first=len(p.blocks) + 10)
    check("keep_first past the end is a no-op", pall.moved == 0,
          "%d moved" % pall.moved)

    # A synthetic pin on FRONT.DAT: any sector-aligned file will do, and it
    # must land on a sector without that being true by luck.
    aligned = [e for e in dv.entries if e.offset % SECTOR == 0
               and e.size_comp > 4096]
    name = aligned[len(aligned) // 2].name
    pp = repack.plan(dv, pin={name})
    pb = [b for b in pp.blocks if b.pinned]
    check("an extra pin is honoured", len(pb) == 1 and pb[0].dst % SECTOR == 0,
          "%s -> 0x%X" % (name, pb[0].dst) if pb else "no pinned block")
    check("pinning costs a little slack and no more",
          0 <= p.reclaimed - pp.reclaimed < SECTOR,
          "%d bytes" % (p.reclaimed - pp.reclaimed))
    unpinnable = repack.plan(dv, pin={"no-such-file.xyz"})
    check("a pin naming nothing changes nothing",
          unpinnable.reclaimed == p.reclaimed and not unpinnable.pinned)

    # ------------------------------------------------------------- refusals
    print("\nrefusals")
    try:
        repack.apply(dv, repack.plan(game.archive("BANKS.DAT")), raw)
        check("apply refuses a plan from another archive", False)
    except repack.RepackRefused as exc:
        check("apply refuses a plan from another archive", True,
              str(exc)[:44])

    # REPACK_BROKEN is empty today -- SWAT.DAT moved to "needs-pinning" once
    # logo01.pck was pinned -- so the refusal path is unreachable on the retail
    # disc without forcing it.
    dave_mod.REPACK_BROKEN.add("FRONT.DAT")
    try:
        try:
            repack.plan(dv)
            check("plan refuses a breaks-the-game archive", False)
        except repack.RepackRefused as exc:
            check("plan refuses a breaks-the-game archive", True,
                  str(exc)[:44])
        forced = repack.plan(dv, allow_unsafe=True)
        check("allow_unsafe still plans it", forced.reclaimed == p.reclaimed)
    finally:
        dave_mod.REPACK_BROKEN.discard("FRONT.DAT")
    check("REPACK_BROKEN restored", "FRONT.DAT" not in dave_mod.REPACK_BROKEN)

    # ----------------------------------------------------- SWAT.DAT, the one
    print("\nSWAT.DAT -- the archive that froze the game")
    sw = game.archive("SWAT.DAT")
    ps = repack.plan(sw)
    ks = KNOWN["SWAT.DAT"]
    print("  %d files, %.1f MB, %d blocks, %d moved, %d reclaimed (%.2f MB)"
          % (sw.count, sw.size / 1048576.0, len(ps.blocks), ps.moved,
             ps.reclaimed, ps.reclaimed / 1048576.0))
    check("block count matches the booted layout",
          len(ps.blocks) == ks["blocks"],
          "%d vs %d" % (len(ps.blocks), ks["blocks"]))
    check("moved count matches", ps.moved == ks["moved"],
          "%d vs %d" % (ps.moved, ks["moved"]))
    check("reclaimed bytes match", ps.reclaimed == ks["reclaimed"],
          "%.2f MB" % (ps.reclaimed / 1048576.0))
    srcs = {b.src for b in ps.pinned}
    got = sorted({e.name.rsplit("/", 1)[-1] for e in sw.entries
                  if e.offset in srcs})
    want = sorted({e.name.rsplit("/", 1)[-1] for e in sw.entries
                   if e.name.rsplit("/", 1)[-1] in SECTOR_PINNED})
    check("every SECTOR_PINNED file present here was pinned", got == want,
          "pinned %s, present %s" % (", ".join(got) or "-",
                                     ", ".join(want) or "-"))
    for b in ps.pinned:
        nm = next(e.name for e in sw.entries if e.offset == b.src)
        check("%s lands on a sector" % nm, b.dst % SECTOR == 0,
              "0x%08X -> 0x%08X" % (b.src, b.dst))
    check("SWAT layout invariants hold", not plan_invariants(sw, ps))

    # The flat layout is the one that freezes. Its logo01.pck destination is
    # the 624-byte-off-sector address the bisect found, and the ONLY difference
    # between the two plans is the padding that fixes it.
    flat = repack.plan(sw, pin=set())
    off_by = [b.dst % SECTOR for b in flat.blocks if b.src in srcs]
    check("without pinning that file lands off-sector", all(off_by),
          "%s bytes short of the next sector"
          % ", ".join(str(SECTOR - o) for o in off_by))
    check("pinning costs under 2 KB of the 12.95 MB",
          0 < flat.reclaimed - ps.reclaimed < 2 * SECTOR,
          "%d bytes" % (flat.reclaimed - ps.reclaimed))

    if args.swat_apply:
        rawsw = sw.backing.read_at(0, sw.size)
        big = repack.apply(sw, ps, rawsw)
        check("SWAT rebuild keeps its size", len(big) == sw.size,
              "%d" % len(big))
        r2 = Dave(BytesBacking(big), label="SWAT.DAT")
        sample = sw.entries[::max(1, sw.count // 400)]
        pinned_names = [e for e in sw.entries
                        if e.name.rsplit("/", 1)[-1].lower() in SECTOR_PINNED]
        bad2 = [e.name for e in list(sample) + pinned_names
                if sw.read(e) != r2.read(e.name)]
        check("sampled files read back from the rebuild", not bad2,
              "%d compared, %d wrong" % (len(sample) + len(pinned_names),
                                         len(bad2)))
        for e in pinned_names:
            check("%s is sector-aligned in the rebuild" % e.name,
                  r2.find(e.name).offset % SECTOR == 0,
                  "0x%08X" % r2.find(e.name).offset)

    # ------------------------------ level archives: zero-length entries
    # These are the archives the packer this module replaces could not touch at
    # all, and nothing said so out loud: `test_repack_all.py` caught the
    # KeyError and printed "SKIP".
    print("\nLVLS_SNG/TOMB.DAT -- a level archive, and why the old packer "
          "skipped every one")
    lv = game.archive("LVLS_SNG/TOMB.DAT")
    empty = [e for e in lv.entries if (e.size_comp or e.size_full) == 0]
    shared = {e.offset for e in lv.entries if (e.size_comp or e.size_full) > 0}
    orphan = [e for e in empty if e.offset not in shared]
    check("this archive has a zero-length entry at its own offset",
          len(orphan) >= 1,
          "%d zero-length, %d of them alone at that offset: %s"
          % (len(empty), len(orphan), ", ".join(e.name for e in orphan)))

    pl = repack.plan(lv)
    covered = {b.src for b in pl.blocks}
    check("the plan covers the zero-length entries",
          all(e.offset in covered for e in empty))
    check("level layout invariants hold", not plan_invariants(lv, pl),
          "%d blocks, %d reclaimed" % (len(pl.blocks), pl.reclaimed))
    lraw = lv.backing.read_at(0, lv.size)
    lrb = Dave(BytesBacking(repack.apply(lv, pl, lraw)), label="TOMB.DAT")
    lbad = [e.name for e in lv.entries if lv.read(e) != lrb.read(e.name)]
    check("every file in the level archive reads back", not lbad,
          "%d compared, %d wrong" % (lv.count, len(lbad)))
    check("the zero-length file is still zero length",
          all(lrb.find(e.name).size_full == 0 for e in empty))

    try:
        from test_repack_boot import repack_archive as old_packer
    except ImportError:
        old_packer = None
    if old_packer is None:
        print("  (test_repack_boot.repack_archive is gone; skipping the "
              "regression check)")
    else:
        try:
            old_packer(lv, lraw)
            check("the old packer really did fail on this archive", False,
                  "it succeeded -- the finding below no longer holds")
        except KeyError as exc:
            check("the old packer really did fail on this archive", True,
                  "KeyError %s = the zero-length entry's offset" % exc)

    orphans = 0
    for name in game.archives():
        d = game.archive(name)
        real = {e.offset for e in d.entries if (e.size_comp or e.size_full) > 0}
        if any(e.offset not in real for e in d.entries
               if (e.size_comp or e.size_full) == 0):
            orphans += 1
    print("  %d of %d archives would have hit that KeyError"
          % (orphans, len(game.archives())))
    check("this was not a one-archive quirk", orphans > 50, "%d" % orphans)

    # ------------------------------------------------- every archive on the disc
    print("\nplanning all 66 archives (both magics, every shape)")
    total, tight, worst = 0, [], []
    for name in game.archives():
        d = game.archive(name)
        pl = repack.plan(d)
        problems = plan_invariants(d, pl)
        if problems:
            worst.append("%s: %s" % (name, problems[0]))
        total += pl.reclaimed
        if pl.moved == 0:
            tight.append(name)
    check("every archive plans without refusing", True,
          "%d archives" % len(game.archives()))
    check("no layout violates an invariant", not worst,
          worst[0] if worst else "%d archives" % len(game.archives()))
    print("  %.1f MB reclaimable across the disc, %d archives already tight%s"
          % (total / 1048576.0, len(tight),
             (": " + ", ".join(n.split("/")[-1] for n in tight[:4]))
             if tight else ""))
    check("the disc-wide figure is the measured one", total == DISC_TOTAL,
          "%d bytes vs %d" % (total, DISC_TOTAL))
    check("every archive has slack worth reclaiming", not tight,
          "smallest is 145,696 bytes (GHOST4.DAT)")

    game.close()

    # ------------------------------------- test_repack_all.py's own spot-check
    print("\ntest_repack_all.py -- the check that used to examine nothing")
    full = ["A.DAT", "B.DAT", "C.DAT", "SLUS_205.00"]
    todo = ["A.DAT"]                       # e.g. `--archives A.DAT`
    done = ["A.DAT"]
    buggy = [a for a in todo if a not in done]        # the bug, kept as a test
    fixed = tra.untouched(full, done)
    check("the old expression really was vacuous", buggy == [],
          "it only ever sees the run's own todo list")
    check("untouched() uses the full disc listing",
          fixed == ["B.DAT", "C.DAT", "SLUS_205.00"], ", ".join(fixed))
    check("repacking everything still leaves the non-archives",
          tra.untouched(full, ["A.DAT", "B.DAT", "C.DAT"]) == ["SLUS_205.00"])
    check("spread() covers both ends", tra.spread(10, 4)[0] == 0
          and tra.spread(10, 4)[-1] == 9, str(tra.spread(10, 4)))
    check("spread() never over-samples", tra.spread(3, 9) == [0, 1, 2])

    check("diff_window finds nothing in identical bytes",
          tra.diff_window(lambda o, n: b"abcd", lambda o, n: b"abcd", 0, 4) == -1)
    check("diff_window reports the exact differing byte",
          tra.diff_window(lambda o, n: b"abXd", lambda o, n: b"abcd",
                          0x1000, 4) == 0x1002)

    iso_a = Iso9660(VANILLA, writable=False)
    iso_b = Iso9660(VANILLA, writable=False)
    order = sorted(iso_a.files(), key=lambda e: e.offset)
    arcs = [e.path for e in order if e.path.upper().endswith(".DAT")]
    others = [e.path for e in order if not e.path.upper().endswith(".DAT")]
    print("  %d files on the disc: %d archives, %d not"
          % (len(order), len(arcs), len(others)))
    sample = arcs[:4] + others[:4] + arcs[-2:]
    c1, b1 = tra.spot_check(sample, iso_a, iso_b, windows=3)
    check("spot_check finds no difference between a disc and itself",
          not b1 and c1 > 0, "%d windows over %d files" % (c1, len(sample)))

    victim = iso_a.get(sample[1])
    flipped = FlippedIso(iso_b, victim.offset + 64)
    c3, b3 = tra.spot_check(sample, flipped, iso_a, windows=3)
    check("spot_check catches a single flipped byte",
          [x for x in b3 if x[1] == victim.offset + 64],
          "%s at 0x%X" % (b3[0][0], b3[0][1]) if b3 else "MISSED IT")

    # Pick a real neighbour pair off the disc rather than assuming one.
    pair = next(((a.path, b.path) for a, b in zip(order, order[1:])
                 if a.path.upper().endswith(".DAT")), None)
    check("archives have a neighbour to overrun into", pair is not None,
          "%s -> %s" % pair if pair else "none found")
    nb = iso_a.get(pair[1])
    c4, b4, blind4 = tra.boundary_check([pair[0]], iso_b, iso_a)
    check("boundary_check passes a clean neighbour",
          c4 == 1 and not b4 and blind4 == 0, "%s" % pair[1])
    over = FlippedIso(iso_b, nb.offset)
    c5, b5, _ = tra.boundary_check([pair[0]], over, iso_a)
    check("boundary_check catches a byte written past an archive's end",
          len(b5) == 1 and b5[0][1] == nb.offset,
          "%s at 0x%X" % (b5[0][0], b5[0][1]) if b5 else "MISSED IT")
    # Same flipped byte, but now the neighbour is one of the repacked archives,
    # so the difference is legitimate and unjudgeable. It must be counted as
    # blind rather than quietly passed -- and the OTHER boundary in the pair,
    # whose neighbour was not repacked, must still be compared.
    c6, b6, blind6 = tra.boundary_check(list(pair), over, iso_a)
    check("a repacked neighbour is reported blind, not passed",
          blind6 == 1 and not b6,
          "%d blind, %d still compared" % (blind6, c6))
    iso_a.close()
    iso_b.close()

    print("\n%s" % ("ALL CHECKS PASSED" if ok else "SOMETHING FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
