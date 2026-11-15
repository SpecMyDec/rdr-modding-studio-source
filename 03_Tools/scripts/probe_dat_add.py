"""Settle the claim that files cannot be added to a `.DAT` archive.

The developer, 2026-09-13: *"get rid of assumptions like Dat files cant be added to,
they can be"*. This is the probe that settles it, and it is deliberately
independent of the Windows suite: it never writes to any ISO. The disc is
opened read-only, the one archive that gets written is an extracted COPY in a
temp directory, and the copy is deleted on the way out.

Two passes:

  A. FEASIBILITY, all 66 archives, read-only. `Dave.can_add()` asked for a
     realistic new file. Reports how many archives would take one and the
     reasons the rest would not -- the reasons are the interesting part,
     because "no spare TOC slot" and "the free run is too small" are
     different problems with different answers.

  B. PROOF, one archive, on a copy. Add a new file, close the archive, re-open
     it from the bytes on disk, and demand:
       - the newcomer reads back byte-exact under its own name;
       - the entry count went up by exactly one;
       - every pre-existing entry still has the same name, offset, both
         sizes, and the same SHA-1 of its contents.
     The last one is the point: adding a file must not move a single byte of
     anybody else's data.

    python3 03_Tools/scripts/probe_dat_add.py [--archive LVLS_M2/INT2.DAT]

Structural validity is not the same as the game tolerating it. The boot side
of this is `scripts/test_add_named.py`, which drives the emulator.
"""

import argparse
import collections
import hashlib
import os
import shutil
import sys
import tempfile

# `scripts/` holds a `dis.py`, and Python puts a script's own directory first
# on sys.path -- which shadows the stdlib `dis` that `inspect` (and so
# `argparse`) imports on 3.13+. Drop this directory and add `03_Tools`.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _HERE]
sys.path.insert(0, os.path.dirname(_HERE))

from rdrstudio.dave import Dave, FileBacking
from rdrstudio.game import Game

# Mid-sorting, spellable in the 6-bit packed alphabet, and obviously ours.
NEW_NAME = "rdrstudio_addprobe.txt"
PAYLOAD = (b"Added to a Dave archive by RDR Modding Studio.\n"
           b"If you can read this back out of the TOC, a .DAT archive took a "
           b"brand new file.\n") * 3


def fingerprint(dave):
    """name -> (offset, size_full, size_comp, sha1) for every entry."""
    out = {}
    for e in dave:
        out[e.name] = (e.offset, e.size_full, e.size_comp,
                       hashlib.sha1(dave.read(e)).hexdigest())
    return out


def pass_feasibility(game):
    print("A. feasibility -- can_add(%r, %d bytes) on every archive"
          % (NEW_NAME, len(PAYLOAD)))
    ok = []
    reasons = collections.Counter()
    for arc in game.archives():
        d = game.archive(arc)
        check = d.can_add(NEW_NAME, len(PAYLOAD))
        if check["ok"]:
            ok.append(arc)
        else:
            for r in check["reasons"]:
                reasons[r.split("(")[0].split(",")[0].strip()] += 1
    total = len(game.archives())
    print("   %d of %d archives would take it right now." % (len(ok), total))
    if reasons:
        print("   why the rest would not:")
        for r, n in reasons.most_common():
            print("     %4d x %s" % (n, r))
    return ok


def pass_proof(game, arc):
    print("\nB. proof -- add, re-open, read back  [%s, on a copy]" % arc)
    tmp = tempfile.mkdtemp(prefix="rdr-datadd-")
    try:
        path = os.path.join(tmp, os.path.basename(arc))
        with open(path, "wb") as fh:
            fh.write(game.iso.read_file(arc))

        d = Dave(FileBacking(path, writable=True), label=arc)
        before = fingerprint(d)
        n_before = len(d)
        check = d.can_add(NEW_NAME, len(PAYLOAD))
        if not check["ok"]:
            print("   REFUSED: %s" % "; ".join(check["reasons"]))
            return False
        result = d.add(NEW_NAME, PAYLOAD)
        d.backing.close()
        print("   added at index %d, payload at 0x%X, %d entries -> %d"
              % (result["index"], result["offset"], n_before, result["count"]))

        # Re-open from the bytes on disk. Nothing is carried over in memory.
        d2 = Dave(FileBacking(path, writable=False), label=arc)
        problems = []
        if len(d2) != n_before + 1:
            problems.append("entry count is %d, expected %d"
                            % (len(d2), n_before + 1))
        try:
            back = d2.read(NEW_NAME)
        except Exception as ex:                                # noqa: BLE001
            problems.append("the new entry does not read back: %s" % ex)
            back = None
        if back is not None and back != PAYLOAD:
            problems.append("the new entry read back %d bytes, wrote %d"
                            % (len(back), len(PAYLOAD)))
        after = fingerprint(d2)
        moved = [n for n, v in before.items()
                 if n not in after or after[n] != v]
        if moved:
            problems.append("%d pre-existing entr%s changed: %s"
                            % (len(moved), "y" if len(moved) == 1 else "ies",
                               ", ".join(sorted(moved)[:5])))
        d2.backing.close()

        if problems:
            for p in problems:
                print("   FAIL: %s" % p)
            return False
        print("   the new file reads back byte-exact (%d bytes)" % len(PAYLOAD))
        print("   all %d pre-existing entries unchanged: name, offset, both "
              "sizes, SHA-1" % n_before)
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso")
    ap.add_argument("--archive", default="LVLS_M2/INT2.DAT")
    args = ap.parse_args()

    iso = args.iso
    if iso is None:
        from rdrstudio import config
        # The PRISTINE disc is the reference. The working copy carries every
        # edit anyone has made -- level-9 recompression shrinks files, which
        # grows the archive's free run, so a figure measured there flatters
        # the format. (SWAT.DAT's largest free run: 2,047 bytes on vanilla,
        # 6,425 on this machine's working copy.) Read-only, so it also means
        # this probe never queues behind the suite lock.
        iso = config.VANILLA_ISO
    game = Game(iso, writable=False)
    print("disc: %s  (opened read-only; no ISO is written by this probe)\n" % iso)

    takers = pass_feasibility(game)
    arc = args.archive if args.archive in takers else (takers[0] if takers else None)
    if arc is None:
        print("\nNo archive can take a new entry as it stands -- nothing to prove.")
        return 1
    good = pass_proof(game, arc)
    print("\nVERDICT: files %s be added to a .DAT archive."
          % ("CAN" if good else "could NOT"))
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
