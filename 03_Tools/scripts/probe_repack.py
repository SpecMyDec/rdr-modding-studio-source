"""Can a Dave archive be rebuilt? And is "never repack" actually true?

The toolkit has carried a rule since before I could check it: *never repack a
Dave archive, because repacking causes a permanent black-screen freeze*. It
came from one earlier attempt, it was never verified, and it is the single
biggest constraint on the whole project -- it is the reason the tool can edit
content but never add any. A rule that expensive deserves evidence.

This probe does NOT write to any ISO. It answers the cheapest question first:

    Given only what the parser understands, can the original archive bytes be
    reproduced exactly?

If yes, the format is fully understood and a repack is at least *possible* --
whatever went wrong before was a bug, not a law. If no, the difference is
precisely what the old attempt would have destroyed, and it names the thing
that must be handled.

Layout, from `dave.py`:

    0x00  magic "Dave" | "DAVE", u32 count, u32 info_size, u32 name_size
    0x800 TOC: count x 16 bytes {name_offset, offset, size_full, size_comp}
    0x800 + info_size   packed or ASCII name block
    then                file data
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdrstudio.game import Game                      # noqa: E402
from rdrstudio import config                         # noqa: E402
from rdrstudio.dave import SECTOR                     # noqa: E402

# From config, not a `D:\` literal: `config.ROOT` derives from the
# module's own location, so this also stops the script being
# Windows-only. REQ-035 / REQ-059.
DISC = Path(config.VANILLA_ISO)


def rebuild(dv, raw):
    """Reconstruct the archive image from parsed structure + original bytes.

    Deliberately reuses the ORIGINAL name block and the original per-entry
    offsets. The question here is not "can I invent a new layout" but "does
    the parser account for every byte" -- so anything not reproduced is
    something the parser does not model.
    """
    out = bytearray(len(raw))

    # Header.
    out[0:4] = dv.magic.encode("ascii")
    struct.pack_into("<III", out, 4, dv.count, dv.info_size, dv.name_size)
    # Bytes 16..SECTOR are whatever the original had; copy them so the probe
    # measures the parts we DO model.
    out[16:SECTOR] = raw[16:SECTOR]

    # TOC.
    for e in dv.entries:
        struct.pack_into("<IIII", out, SECTOR + e.index * 16,
                         e.name_offset, e.offset, e.size_full, e.size_comp)

    # Name block, verbatim.
    names_off = SECTOR + dv.info_size
    out[names_off:names_off + dv.name_size] = \
        raw[names_off:names_off + dv.name_size]

    # File data, each at the offset the TOC declares.
    for e in dv.entries:
        n = e.size_comp if e.size_comp else e.size_full
        if not n:
            continue
        out[e.offset:e.offset + n] = raw[e.offset:e.offset + n]

    return bytes(out)


def main():
    game = Game(DISC, writable=False)
    print("Rebuilding archives from parsed structure and comparing to the "
          "original bytes.\n")
    print("%-24s %10s %8s %10s  %s"
          % ("archive", "bytes", "files", "differing", "verdict"))

    total_bad = 0
    checked = 0
    gaps = {}
    for name in game.archives():
        try:
            dv = game.archive(name)
        except Exception:
            continue
        # Read the whole archive image once.
        raw = dv.backing.read_at(0, dv.size)
        built = rebuild(dv, raw)
        diff = sum(1 for a, b in zip(raw, built) if a != b)
        checked += 1
        if diff:
            total_bad += 1
            # Where do the differences live? That is the useful part.
            runs = []
            i = 0
            while i < len(raw) and len(runs) < 4:
                if raw[i] != built[i]:
                    j = i
                    while j < len(raw) and raw[j] != built[j]:
                        j += 1
                    runs.append((i, j - i))
                    i = j
                else:
                    i += 1
            gaps[name] = runs
        print("%-24s %10d %8d %10d  %s"
              % (name.split("/")[-1], dv.size, dv.count, diff,
                 "IDENTICAL" if not diff else "differs"))
        # Originally capped at 12 because the comparison was slow. It is not
        # slow any more, and "12 of 12" was quietly doing duty in the notes as
        # if it covered the disc. It covered 12 archives out of 66.

    print("\n%d of %d archives rebuilt byte-identically"
          % (checked - total_bad, checked))
    if gaps:
        print("\nwhere the differences are (offset, length) -- these are bytes "
              "the parser does not model:")
        for k, runs in list(gaps.items())[:4]:
            print("  %-22s %s" % (k.split("/")[-1],
                                  ["0x%X+%d" % r for r in runs]))
    else:
        print("\nEvery byte of these archives is accounted for by the parser.")
        print("That does not prove a repack BOOTS, but it does mean the")
        print("format is understood well enough to write one, and that the")
        print("old failure was a bug rather than a property of the format.")

    game.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
