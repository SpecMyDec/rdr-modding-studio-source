"""Add a genuinely NEW file to a Dave archive, and prove nothing else moved.

The project's founding rule is "never repack" -- but that rule came from a
handoff document, not an experiment, and it conflates two different things:

  * moving file data around (which really does break the game), and
  * adding one TOC entry while every existing file stays exactly where it is.

This tests the second. It writes to the WORKING ISO only, checks every
original file still reads byte-identically, and can undo itself.

    python scripts/test_add_file.py            add, verify, leave it in place
    python scripts/test_add_file.py --revert   put the archive back
    python scripts/test_add_file.py --dry      report feasibility only

After a successful add, run the emulator check -- structural validity is not
the same as the game tolerating it:

    python scripts/play_test.py --level --seconds 116

FRONT.DAT is the deliberate target: it holds the front end, so the game reads
it within seconds of booting. If shifting every entry index by one breaks
anything, this is where it shows up fastest.
"""

import argparse
import hashlib
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rdrstudio import Game
from rdrstudio import config as cfg

ARCHIVE = "FRONT.DAT"
NEWNAME = "#modtest.txt"     # '#' sorts before every real name on the disc
PAYLOAD = (b"Red Dead Revolver -- added by RDR Modding Studio.\n"
           b"If you can read this out of the archive, a new file entry "
           b"survived.\n")


def fingerprint(game, archive):
    """name -> sha1 of contents, for every entry."""
    dave = game.archive(archive)
    out = {}
    for e in dave:
        try:
            out[e.name] = hashlib.sha1(dave.read(e)).hexdigest()
        except Exception as ex:
            out[e.name] = "ERROR %s" % ex
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--revert", action="store_true")
    ap.add_argument("--archive", default=ARCHIVE)
    # Point at a throwaway ISO. Without this the test writes to the
    # user's WORKING disc, which is not a thing a test should decide.
    ap.add_argument("--iso", default=None)
    args = ap.parse_args()

    target = args.iso or cfg.MODDED_ISO
    if not os.path.exists(target):
        print("no ISO at %s" % target)
        return 2

    if args.revert:
        # There is no "remove entry" -- restoring the archive means restoring
        # the disc, which is what the vanilla copy is for.
        print("Restoring the working ISO from vanilla ...")
        shutil.copyfile(cfg.VANILLA_ISO, target)
        print("done.")
        return 0

    print("baseline: reading every file in %s from the pristine disc" % args.archive)
    vanilla = Game(cfg.VANILLA_ISO)
    before = fingerprint(vanilla, args.archive)
    print("  %d entries fingerprinted" % len(before))

    game = Game(target, writable=not args.dry)
    dave = game.archive(args.archive)
    check = dave.can_add(NEWNAME, len(PAYLOAD))

    print("\nfeasibility")
    print("  spare TOC entries : %d" % check["spare_entries"])
    print("  spare name bytes  : %d  (this name needs %d)"
          % (check["spare_name_bytes"], check["encoded_name_bytes"]))
    print("  largest free run  : %d bytes at 0x%X"
          % (check["payload_room"], check["payload_offset"]))
    print("  verdict           : %s"
          % ("CAN ADD" if check["ok"] else "; ".join(check["reasons"])))
    if not check["ok"] or args.dry:
        return 0 if check["ok"] else 1

    count_before = dave.count
    print("\nadding %r (%d bytes) to %s" % (NEWNAME, len(PAYLOAD), args.archive))
    result = dave.add(NEWNAME, PAYLOAD)
    print("  entry count : %d -> %d" % (count_before, result["count"]))
    print("  payload at  : 0x%X" % result["offset"])

    # Reopen from scratch so the check goes through a fresh parse, not the
    # in-memory object that just wrote the bytes.
    game.close()
    game = Game(target)
    dave = game.archive(args.archive)
    failures = []

    print("\nverifying")
    print("  re-parsed    : %d entries, magic %s" % (dave.count, dave.magic))
    if dave.count != count_before + 1:
        failures.append("count is %d, expected %d" % (dave.count,
                                                      count_before + 1))

    try:
        got = dave.read(NEWNAME)
        print("  new file     : reads back %d bytes, %s"
              % (len(got), "identical" if got == PAYLOAD else "DIFFERENT"))
        if got != PAYLOAD:
            failures.append("new file content differs")
    except Exception as ex:
        failures.append("new file unreadable: %s" % ex)

    after = fingerprint(game, args.archive)
    lost = [n for n in before if n not in after]
    changed = [n for n in before
               if n in after and after[n] != before[n]]
    print("  originals    : %d present, %d missing, %d changed"
          % (len(before) - len(lost), len(lost), len(changed)))
    if lost:
        failures.append("%d original files vanished: %s" % (len(lost), lost[:5]))
    if changed:
        failures.append("%d original files changed: %s"
                        % (len(changed), changed[:5]))

    names = [e.name for e in dave]
    if names != sorted(names):
        failures.append("the TOC is no longer name-sorted")
    else:
        print("  TOC order    : still sorted")

    # Files moving is REPORTED, not failed.
    #
    # This used to be the whole point of the test, back when "never repack"
    # was believed and not moving anything was the only safe add. That rule
    # was tested on 2026-07-31 and is false: FRONT.DAT repacked with 315 of
    # 317 files relocated, and the ISO boots and plays. So an add performed on
    # an already-compacted archive legitimately shows every file moved, and
    # calling that a failure reports the repack rather than the add.
    #
    # What still matters -- content unchanged, nothing missing, TOC sorted,
    # the new file readable -- is checked above and still fails hard.
    van = vanilla.archive(args.archive)
    moved = [e.name for e in van
             if e.name in {x.name for x in dave}
             and dave.find(e.name).offset != e.offset]
    print("  file offsets : %d of %d unchanged%s"
          % (len(before) - len(moved), len(before),
             "  (the rest were relocated by a repack, which is allowed)"
             if moved else ""))

    print()
    for line in failures:
        print("  FAIL " + line)
    if failures:
        print("  %d failure(s) -- run with --revert" % len(failures))
        return 1

    print("  OK -- structurally sound.")
    print()
    print("  This proves the ARCHIVE is valid, not that the GAME accepts it.")
    print("  SLUS_205.00 is a packed executable and has never been read, so")
    print("  whether anything looks files up by index is still unknown.")
    print("  Next:  python scripts/play_test.py --level --seconds 116")
    return 0


if __name__ == "__main__":
    sys.exit(main())
