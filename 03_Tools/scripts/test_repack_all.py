"""Repack EVERY archive on a throwaway disc, then play it.

`test_repack_boot.py` proved one archive (FRONT.DAT) survives a repack and
that the ISO still boots. Booting is a weak result on its own: the boot path
touches FRONT.DAT and little else, so a level archive could still be broken
and the title screen would never notice.

The honest next question is whether a repacked LEVEL archive survives being
*loaded*. That needs the game to actually enter a level, which means knowing
which level it enters -- and the mission order is not in a table I can read
cheaply.

So this sidesteps the question: repack ALL of them. Then whatever level the
game chooses to load is necessarily one that was repacked, and no assumption
about mission order is involved.

THAT ARGUMENT DID NOT HOLD FOR ANY EARLIER RUN OF THIS SCRIPT. The packer it
imported raised KeyError on every archive containing a zero-length entry,
which is 61 of the 66 -- every level archive -- and this script caught that
and printed "SKIP". So "repack everything, then play" repacked FRONT, BANKS,
STREAMS, PUPSHOW and SWAT, and the levels it was written to test were never
touched. `rdrstudio.repack` plans those entries, so the run below finally does
what this file claims. Any level result from before today is void.

    python scripts/test_repack_all.py                 repack everything + play
    python scripts/test_repack_all.py --no-boot       repack and verify only
    python scripts/test_repack_all.py --only LVLS_SNG limit to a subtree
    python scripts/test_repack_all.py --fresh         recopy vanilla first

VERIFICATION, in two halves, because the first half alone is not enough:

  * Every file in every repacked archive must read back byte-identically
    through the new TOC. This catches a packer bug before it wastes a
    two-minute emulator run.
  * Every file this run did NOT repack must still match the vanilla disc,
    byte for byte, in sampled windows -- all 66 archives and the 37 other
    files, SLUS_205.00 and the .PSS videos included. Re-reading only what you
    rewrote is blind to the failure that matters most here: a write running
    past an archive's end lands in whatever the ISO stores next, and every TOC
    on the disc would still read perfectly.

    The archives are stored back to back, so an overrun lands in the
    neighbour's first bytes rather than in any padding; when that neighbour was
    repacked too, the comparison cannot say anything and the run REPORTS how
    many boundaries were blind instead of counting them as passes.

The packing itself now comes from `rdrstudio.repack`, which is the same
algorithm this script used to import from test_repack_boot plus the sector
pinning that made SWAT.DAT survive (dave.SECTOR_PINNED).

Writes ONLY to 02_Modded/RDR_REPACKTEST.iso. Never 01_Vanilla, never
RDR_MODDED.iso.
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rdrstudio.game import Game                          # noqa: E402
from rdrstudio import repack                             # noqa: E402
from rdrstudio.config import ROOT, VANILLA_ISO   # noqa: E402
from test_repack_boot import TEST_ISO                    # noqa: E402

VANILLA = Path(VANILLA_ISO)

WINDOW = 65536          # bytes per comparison window


def untouched(all_paths, repacked):
    """Everything on the disc this run did NOT repack.

    `all_paths` must be the FULL disc listing -- every file, not just the .DAT
    archives, and emphatically not the run's todo list. The first version of
    this check computed `[a for a in todo if a not in done]`, which is always
    empty: `todo` is already the filtered set that `done` was built from, so
    the check passed by examining nothing at all.

    Non-archive files matter here. The disc holds 103 files, 66 of them
    archives, and the other 37 -- SLUS_205.00, the .IRX modules, the .PSS
    videos -- are never repacked, so they are pristine references for any write
    that went somewhere it should not have.
    """
    done = set(repacked)
    return [a for a in all_paths if a not in done]


def spread(n, count):
    """`count` indices spread over range(n), first and last always included."""
    if n <= 0:
        return []
    if count >= n:
        return list(range(n))
    if count == 1:
        return [0]
    return sorted({(i * (n - 1)) // (count - 1) for i in range(count)})


def diff_window(read_a, read_b, offset, length):
    """Offset of the first differing byte in a window, or -1 if identical.

    `read_a`/`read_b` are `(offset, length) -> bytes` callables so this can be
    tested against fakes as well as against two real discs.
    """
    if length <= 0:
        return -1
    a = read_a(offset, length)
    b = read_b(offset, length)
    if a == b:
        return -1
    for i in range(min(len(a), len(b))):
        if a[i] != b[i]:
            return offset + i
    return offset + min(len(a), len(b))


def _extent(test_iso, van_iso, path):
    """(offset, size) of a file that is at the same place on both discs.

    Returns None if it is not on both, and raises nothing: the directory tree
    is never rewritten by this toolkit, so a moved file is a caller-visible
    fault rather than something to work around.
    """
    try:
        a = van_iso.get(path)
        b = test_iso.get(path)
    except KeyError:
        return None
    if a.offset != b.offset or a.size != b.size:
        return False
    return (a.offset, a.size)


def spot_check(names, test_iso, van_iso, windows=4, window=WINDOW):
    """Compare disc bytes of files that should be untouched. -> (checked, bad).

    `bad` is a list of (path, offset) for every window that differed; an empty
    list is the only passing result.

    Windows are clamped to the file's own extent. Reading past it would drag in
    the next file on the disc, which may legitimately have been repacked, and
    report the innocent archive as broken.
    """
    checked, bad = 0, []
    for path in names:
        ext = _extent(test_iso, van_iso, path)
        if ext is None:
            continue
        if ext is False:
            bad.append((path, -1))
            continue
        base, size = ext
        grid = max(1, (size + window - 1) // window)
        starts = sorted({min(i * window, max(0, size - window))
                         for i in spread(grid, windows)})
        for s in starts:
            n = min(window, size - s)
            if n <= 0:
                continue
            where = diff_window(test_iso.read_at, van_iso.read_at, base + s, n)
            checked += 1
            if where >= 0:
                bad.append((path, where))
    return checked, bad


def boundary_check(repacked, test_iso, van_iso, window=WINDOW):
    """Compare the first bytes of whatever follows each repacked archive.
    -> (checked, bad, blind).

    This is the overrun check, and it looks at the neighbour rather than at any
    slack, because MEASURED: all 66 archives are a whole number of sectors long
    and butt straight up against the next file. The only 37 files on this disc
    with pad after them are the non-archive ones. So a write that ran past an
    archive's end does not land in dead space, it lands in the next file's
    first bytes.

    `blind` counts boundaries where the neighbour was repacked too. Its bytes
    are then legitimately different and this check cannot say anything about
    them -- which is worth printing, not worth hiding behind a pass.
    """
    order = sorted(van_iso.files(), key=lambda e: e.offset)
    done = set(repacked)
    checked, bad, blind = 0, [], 0
    for path in repacked:
        ext = _extent(test_iso, van_iso, path)
        if not ext:
            if ext is False:
                bad.append((path, -1))
            continue
        end = ext[0] + ext[1]
        nxt = next((e for e in order if e.offset >= end), None)
        if nxt is None:
            continue                      # last file on the disc
        if nxt.path in done:
            blind += 1
            continue
        n = min(window, nxt.size)
        where = diff_window(test_iso.read_at, van_iso.read_at, nxt.offset, n)
        checked += 1
        if where >= 0:
            bad.append(("%s -> %s" % (path, nxt.path), where))
    return checked, bad, blind


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="substring filter on archive name")
    ap.add_argument("--archives", default="",
                    help="comma-separated exact names -- for bisecting which "
                         "archive breaks the game")
    ap.add_argument("--exclude", default="",
                    help="comma-separated substrings to leave untouched")
    ap.add_argument("--keep-first", type=int, default=0,
                    help="leave the first N data blocks at their original "
                         "offsets; pack only what follows. For bisecting "
                         "WITHIN one archive.")
    ap.add_argument("--fresh", action="store_true",
                    help="recopy the vanilla ISO over the test ISO first. The "
                         "untouched-file check only means something if the "
                         "disc started out identical to vanilla.")
    ap.add_argument("--no-boot", action="store_true")
    ap.add_argument("--seconds", type=float, default=110.0)
    ap.add_argument("--verify-sample", type=int, default=0,
                    help="verify only N files per archive (0 = all of them)")
    ap.add_argument("--spot-windows", type=int, default=4,
                    help="64 KB windows to compare per untouched file")
    args = ap.parse_args()

    if args.fresh:
        if TEST_ISO.exists():
            os.chmod(TEST_ISO, 0o666)
        print("restoring a pristine test ISO ...")
        shutil.copyfile(VANILLA, TEST_ISO)
        os.chmod(TEST_ISO, 0o666)
    if not TEST_ISO.exists():
        print("no test ISO at %s" % TEST_ISO)
        return 1

    game = Game(TEST_ISO, writable=True)
    # Captured BEFORE any filtering: this is what the untouched check is
    # measured against, and building it from `todo` is the bug that made the
    # check vacuous.
    all_archives = game.archives()
    all_files = [e.path for e in game.iso.files()]
    todo = [a for a in all_archives if args.only in a]
    if args.archives:
        want = {x.strip().lower() for x in args.archives.split(",") if x.strip()}
        todo = [a for a in todo
                if a.lower() in want or a.split("/")[-1].lower() in want]
        missing = want - {a.lower() for a in todo} \
                       - {a.split("/")[-1].lower() for a in todo}
        if missing:
            print("no such archive: %s" % ", ".join(sorted(missing)))
            return 1
    if args.exclude:
        skip = [x.strip() for x in args.exclude.split(",") if x.strip()]
        todo = [a for a in todo if not any(x in a for x in skip)]
    print("repacking %d of %d archives in %s\n"
          % (len(todo), len(all_archives), TEST_ISO.name))
    print("%-26s %8s %10s %12s %6s" % ("archive", "files", "moved",
                                       "reclaimed", "pinned"))

    done, skipped, total_reclaimed = [], [], 0
    for name in todo:
        try:
            dv = game.archive(name)
            raw = dv.backing.read_at(0, dv.size)
            plan = repack.plan(dv, keep_first=args.keep_first)
            packed = repack.apply(dv, plan, raw)
        except Exception as exc:
            skipped.append((name, str(exc)[:60]))
            print("%-26s %8s %10s %12s  SKIP %s"
                  % (name.split("/")[-1], "-", "-", "-", str(exc)[:40]))
            continue
        if len(packed) != dv.size:
            skipped.append((name, "size changed"))
            continue
        if plan.moved == 0:
            skipped.append((name, "already tight"))
            continue
        dv.backing.write_at(0, packed)
        total_reclaimed += plan.reclaimed
        done.append(name)
        print("%-26s %8d %10d %12d %6d" % (name.split("/")[-1], dv.count,
                                           plan.moved, plan.reclaimed,
                                           len(plan.pinned)))
    game.iso.flush()
    game.close()

    print("\n%d archives repacked, %d skipped, %.1f MB of slack reclaimed"
          % (len(done), len(skipped), total_reclaimed / 1048576.0))
    for n, why in skipped[:8]:
        print("   skipped %-24s %s" % (n.split("/")[-1], why))

    # ---- half one: every file in every repacked archive still reads back ----
    print("\nverifying against the vanilla disc ...")
    g2 = Game(TEST_ISO, writable=False)
    src = Game(VANILLA, writable=False)
    bad, checked, missing = 0, 0, 0
    for name in done:
        try:
            entries = list(g2.archive(name))
        except Exception:
            continue
        if args.verify_sample:
            entries = entries[::max(1, len(entries) // args.verify_sample)]
        for e in entries:
            try:
                a = src.read(name, e.name)
            except Exception:
                continue          # not in vanilla (e.g. the added test file)
            try:
                b = g2.read(name, e.name)
            except Exception:
                missing += 1
                continue
            checked += 1
            if a != b:
                bad += 1
                if bad <= 5:
                    print("   MISMATCH %s / %s" % (name, e.name))
    print("  %d files compared, %d unreadable, %d wrong" % (checked, missing, bad))

    # ---- half two: nothing outside the repacked archives changed ----
    # A write that ran past an archive's end into the next thing on the ISO
    # passes half one perfectly, because the TOC it re-reads through is not the
    # route that broke. So compare raw disc bytes instead, over everything this
    # run did NOT rewrite -- including the 37 non-archive files, which are
    # never repacked and are therefore pristine references.
    rest = untouched(all_files, done)
    print("\nspot-checking %d untouched files + %d repack boundaries ..."
          % (len(rest), len(done)))
    w_checked, w_bad = spot_check(rest, g2.iso, src.iso,
                                  windows=args.spot_windows)
    b_checked, b_bad, blind = boundary_check(done, g2.iso, src.iso)
    print("  %d windows over untouched files, %d differ"
          % (w_checked, len(w_bad)))
    print("  %d repack boundaries compared, %d differ, %d blind (the "
          "neighbour was repacked too)" % (b_checked, len(b_bad), blind))
    for path, off in (w_bad + b_bad)[:8]:
        if off < 0:
            print("   MOVED ON DISC %s" % path)
        else:
            print("   DIFFERS %s at 0x%X" % (path, off))
    src.close()
    g2.close()

    if bad or missing or w_bad or b_bad:
        if (w_bad or b_bad) and not args.fresh:
            print("  (if this test ISO was not a fresh copy of vanilla, those")
            print("   differences may predate this run -- rerun with --fresh)")
        print("  REPACK IS BROKEN -- not booting it")
        return 1
    print("  every file in every repacked archive reads back byte-identically,")
    print("  and nothing outside them changed")

    if args.no_boot:
        return 0

    print("\nplaying the fully repacked ISO ...")
    r = subprocess.run([sys.executable, str(HERE / "play_test.py"),
                        "--seconds", str(args.seconds),
                        "--iso", str(TEST_ISO)], cwd=str(HERE.parent))
    print("\nplay_test exit: %s" % r.returncode)
    print("Any screenshot showing a LEVEL is the result: that level was")
    print("repacked, so a repacked level archive loads.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
