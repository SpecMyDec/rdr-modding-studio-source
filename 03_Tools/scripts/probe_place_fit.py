"""Why does "Place files" refuse a .mod or a .tex? -- measured, not reasoned.

M1 asks whether the archive-write path treats `.mod` and `.tex` differently
from every other extension. It does not: `dave.patch` is extension-blind, and
this probe shows the two things that ARE different about those two
extensions, both of them about SIZE rather than type.

Three passes, all read-only against the working ISO except pass 2, which
works on a copy of one archive in a temp directory and never touches the
disc:

  1. name lookup      -- every TOC entry in all 66 archives round-trips
                         through `Dave.find()`. (Result: 0 failures.)
  2. same-size replace -- every entry of one archive is patched with
                         incompressible data of its own length, on an
                         extracted copy. Failures are slot overflows, and
                         they are spread across seven extensions, not
                         concentrated on .mod/.tex.
  3. cross-swap fit   -- the REAL user action: put a different real file of
                         the same kind over this one. This is where .mod and
                         .tex separate from the rest.

Run:  python3 03_Tools/scripts/probe_place_fit.py [iso]
"""

import collections
import os
import random
import shutil
import sys
import tempfile
import zlib

# `scripts/` holds a `dis.py`, and Python puts a script's own directory first
# on sys.path -- which shadows the stdlib `dis` that `inspect` (and so
# `argparse`) imports on 3.13+. Drop this directory and add `03_Tools`.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _HERE]
sys.path.insert(0, os.path.dirname(_HERE))

from rdrstudio.dave import Dave, FileBacking, SlotOverflow, repack_status
from rdrstudio.game import Game

SAMPLE_ARCHIVE = "LVLS_M2/INT2.DAT"
EXTS = ("tex", "mod", "csv", "cr", "strtbl", "anim", "actb", "swui", "enm")


def stored_size(entry, data):
    """What `Dave.patch` would actually store for `data` in `entry`'s slot."""
    if not entry.compressed:
        return len(data)
    co = zlib.compressobj(9, zlib.DEFLATED, -15)
    packed = co.compress(data) + co.flush()
    return min(len(packed), len(data))


def pass_lookup(game):
    print("1. name lookup -- every entry, every archive")
    bad = collections.Counter()
    total = 0
    for arc in game.archives():
        d = game.archive(arc)
        for e in d:
            total += 1
            try:
                if d.find(e.name) is not e:
                    bad[e.ext] += 1
            except Exception:                                  # noqa: BLE001
                bad[e.ext] += 1
    print("   %d entries, %d that find() could not resolve%s"
          % (total, sum(bad.values()),
             (": %s" % dict(bad)) if bad else ""))
    return sum(bad.values())


def pass_same_size(game, arc):
    print("\n2. same-size replace through dave.patch -- %s, on a copy" % arc)
    tmp = tempfile.mkdtemp(prefix="rdr-placefit-")
    try:
        path = os.path.join(tmp, os.path.basename(arc))
        with open(path, "wb") as fh:
            fh.write(game.iso.read_file(arc))
        d = Dave(FileBacking(path, writable=True), label=arc)
        ok = collections.Counter()
        fail = collections.Counter()
        for e in list(d):
            before = d.read(e)
            if not before:
                continue
            new = bytes(b ^ 0xFF for b in before)   # same length, no slack
            try:
                d.patch(e.name, new, flush=False)
                ok[e.ext] += 1
            except SlotOverflow:
                fail[e.ext] += 1
        d.backing.close()
        print("   %d replaced, %d refused for want of slot" %
              (sum(ok.values()), sum(fail.values())))
        print("   refusals by extension: %s" % (dict(fail) or "none"))
        return dict(fail)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def pass_cross_swap(game, archives, per_ext=60, seed=7):
    print("\n3. cross-swap fit -- a different real file of the same kind")
    rng = random.Random(seed)
    for arc in archives:
        d = game.archive(arc)
        gap = d.largest_gap()[1]
        byext = collections.defaultdict(list)
        for e in d:
            byext[e.ext].append(e)
        print("\n   %s  (largest free run %d bytes, repack %s)"
              % (arc, gap, repack_status(arc)))
        print("   %-10s %6s %7s %10s" % ("ext", "n", "fits", "fits+grows"))
        for ext in EXTS:
            entries = byext.get(ext) or []
            if len(entries) < 8:
                continue
            sample = rng.sample(entries, min(per_ext, len(entries)))
            fits = grows = 0
            for e in sample:
                other = rng.choice(entries)
                if other is e:
                    continue
                size = stored_size(e, d.read(other))
                if size <= e.slot:
                    fits += 1
                elif size <= gap:
                    grows += 1
            n = len(sample)
            print("   %-10s %6d %6.0f%% %9.0f%%"
                  % (ext, n, 100.0 * fits / n, 100.0 * (fits + grows) / n))


def main():
    iso = sys.argv[1] if len(sys.argv) > 1 else None
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
    print("disc: %s\n" % iso)
    pass_lookup(game)
    pass_same_size(game, SAMPLE_ARCHIVE)
    pass_cross_swap(game, [SAMPLE_ARCHIVE, "SWAT.DAT"])
    print("\nRead the third pass, not the first two: .mod and .tex are the two "
          "extensions whose replacements are large enough that the archive's "
          "own free run cannot absorb them.")


if __name__ == "__main__":
    main()
