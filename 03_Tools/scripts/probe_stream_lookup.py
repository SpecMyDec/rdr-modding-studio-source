r"""C-010: how the game asks for a stream, read out of its own executable.

THE CLAIM (`rdrstudio/streams.py`, and again in `verify_streams.py`): 992
streams are named by no sound bank, no level and no entry in SWAT.DAT -- and
from that, that the engine has no route to them whatsoever, leaving 111
minutes of finished audio that shipped unplayable.

The measurement is sound and it stands: nothing on the disc names them. The
conclusion drawn from it -- that the engine therefore could not reach them --
was an inference, never a reading of the code. This probe reads the code.

WHAT THE EXECUTABLE ACTUALLY DOES. Unpack SLUS_205.00 (aPLib + XOR, see
`elfpack.py`) and the audio streamer is at 0x0027C000-0x0027E400:

    0x0027C6D4  sprintf(buf, "%s.stm", name)          <- "%s.stm" at 0x00521EF1
    0x0027C710  jal 0x0048F1C0   (buf)                   stream open
    0x0048F270  jal 0x0048F078   (name, &off, &size)     resolve
    0x0048F0A0  jal 0x002CB908   (archive, name)         find entry
    0x002CB998  jal 0x0025EEB8   bsearch, a3 = 16        <- record size
                comparator 0x002CAA50 -> 0x002CA9D8

and 0x0048F078 reads the winning 16-byte record at +4 (offset) and +12
(stored size) -- exactly the DAVE TOC layout `dave.py` already parses:

    0x00 u32 name   0x04 u32 offset   0x08 u32 size_full   0x0C u32 size_comp

The comparator at 0x002CA9D8 is a **case-insensitive strcmp** that also folds
`\` to `/`: it lowercases each byte in 'A'..'Z' (`addiu a2, a2, 32`), maps 92
to 47 via `movz`, compares, and returns `a2 - v1`.

SO THE LOOKUP IS BY NAME, against the archive's own table of contents. There
is no manifest of playable streams, no index list, no allow-list -- the key
is the string, and every one of the 8,361 `.stm` in STREAMS.DAT has a TOC
record carrying its name. An orphan is not missing from the lookup table; it
is simply a name nothing in the shipped data happens to pass in.

WHAT THIS PROBE ASSERTS, in order:

  1. the instruction words at all eight addresses above are what is quoted
     here -- so the reading fails loudly instead of rotting;
  2. STREAMS.DAT's TOC is sorted under the comparator as disassembled, which
     is bsearch's precondition and therefore a testable prediction of it;
  3. a faithful reimplementation of that bsearch -- 16-byte records, the same
     comparator -- resolves ALL 8,361 streams to their own record, the 992
     orphans included, offset and stored size matching;
  4. controls: a name that is not on the disc does not resolve; a name given
     in the wrong case still does (the fold is real and load-bearing); and
     shuffling the table makes the search fail, so (2) is not vacuous.

VERDICT: FALSE, on the half that says the engine has no route. The true
statement is narrower and is exactly what the measurement supports: *nothing
in the shipped data asks for them*. Naming one in a sound bank is all it
takes, and that is an edit this toolkit already makes.

    python3 03_Tools/scripts/probe_stream_lookup.py [iso]

Read-only. Opens the pristine disc and writes nothing.
"""

import os
import re
import struct
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _HERE]
sys.path.insert(0, os.path.dirname(_HERE))

from rdrstudio import config, elfpack                              # noqa: E402
from rdrstudio.game import Game                                    # noqa: E402
from rdrstudio.iso9660 import Iso9660                              # noqa: E402

ELF = "SLUS_205.00"

# Every instruction this probe's reading depends on. If the executable is not
# the one that was disassembled, say so instead of quietly proving nothing.
PINNED = [
    (0x0027C6D4, 0x3C050052, 'lui a1, 0x0052        ; "%s.stm"'),
    (0x0027C6E0, 0x24A51EF1, "addiu a1, a1, 0x1EF1  ; -> 0x00521EF1"),
    (0x0027C710, 0x0C123C70, "jal 0x0048F1C0        ; open the stream"),
    (0x0048F270, 0x0C123C1E, "jal 0x0048F078        ; resolve name -> entry"),
    (0x0048F0A0, 0x0C0B2E42, "jal 0x002CB908        ; find in the TOC"),
    (0x0048F0B4, 0x8C640004, "lw a0, 4(v1)          ; entry+4  = offset"),
    (0x0048F0BC, 0x8C63000C, "lw v1, 12(v1)         ; entry+12 = stored size"),
    (0x002CB990, 0x24070010, "addiu a3, zero, 16    ; bsearch record size"),
    (0x002CB998, 0x0C097BAE, "jal 0x0025EEB8        ; bsearch"),
    (0x002CAA58, 0x8C840000, "lw a0, 0(a0)          ; key = entry+0, the name"),
    (0x002CA9D8, 0x2407002F, "addiu a3, zero, 47    ; '/'"),
    (0x002CA9FC, 0x24C60020, "addiu a2, a2, 32      ; 'A'-'Z' -> lower"),
    (0x002CAA00, 0x38C2005C, "xori v0, a2, 92       ; '\\' ?"),
    (0x002CAA04, 0x00E2300A, "movz a2, a3, v0       ; ...then '/'"),
    (0x002CAA48, 0x00C31023, "subu v0, a2, v1       ; return a2 - v1"),
]

STRINGS = [(0x00521EF1, b"%s.stm"), (0x00521F25, b"cdrom0:\\STREAMS.DAT;1")]

BASE = 0x00100000
FAILED = []


def check(label, ok, detail=""):
    print("     %-52s %s%s"
          % (label, "ok" if ok else "FAIL", ("  " + detail) if detail else ""))
    if not ok:
        FAILED.append(label)
    return ok


def engine_key(name):
    """The comparator's view of a name: 0x002CA9D8, byte for byte.

    Lowercase 'A'-'Z' by adding 32, then fold '\\' (92) to '/' (47). Compare
    as unsigned bytes; the return is the difference of the first pair that
    differs, so Python's own ordering over this transformed bytes object is
    the same ordering.
    """
    out = bytearray(name.encode("latin-1"))
    for i, b in enumerate(out):
        if 65 <= b <= 90:
            b += 32
        if b == 92:
            b = 47
        out[i] = b
    return bytes(out)


def engine_bsearch(keys, want):
    """C bsearch over `keys` (already in comparator form), sorted ascending."""
    lo, hi = 0, len(keys) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if keys[mid] == want:
            return mid
        if keys[mid] < want:
            lo = mid + 1
        else:
            hi = mid - 1
    return -1


def main():
    iso = sys.argv[1] if len(sys.argv) > 1 else config.VANILLA_ISO
    print("disc: %s  (read-only)\n" % iso)

    # ------------------------------------------------- 1. the code is as read
    print("1. the executable says what this probe says it says")
    disc = Iso9660(iso)
    e = disc.get(ELF)
    img, base = elfpack.unpack_elf(disc.read_at(e.offset, e.size))
    check("SLUS_205.00 unpacks", len(img) > len(disc.read_at(e.offset, e.size)),
          "%d bytes at 0x%08X" % (len(img), base))
    check("unpacked at the expected base", base == BASE, "0x%08X" % base)

    for addr, want in STRINGS:
        got = img[addr - base:addr - base + len(want)]
        check("string at 0x%08X" % addr, got == want, repr(got.decode("latin-1")))

    for addr, word, what in PINNED:
        got = struct.unpack_from("<I", img, addr - base)[0]
        check("0x%08X  %s" % (addr, what), got == word,
              "" if got == word else "found %08X, expected %08X" % (got, word))

    if FAILED:
        print("\nThe executable is not the one that was disassembled; the rest")
        print("of this probe would be proving nothing. Stopping.")
        return 1

    # ------------------------------------------- 2. the TOC bsearch works on
    print("\n2. STREAMS.DAT's TOC, as the engine's bsearch sees it")
    game = Game(iso, writable=False)
    dave = game.archive("STREAMS.DAT")
    entries = list(dave)
    keys = [engine_key(x.name) for x in entries]
    print("     %d entries, 16 bytes per TOC record" % len(entries))

    ordered = all(keys[i] < keys[i + 1] for i in range(len(keys) - 1))
    check("sorted under the comparator (bsearch's precondition)", ordered)
    check("every entry's name is distinct under it", len(set(keys)) == len(keys),
          "%d distinct" % len(set(keys)))

    # --------------------------------------------------- 3. every stream, found
    print("\n3. the engine's own lookup, run for every stream on the disc")
    t = time.time()
    missed = []
    for i, ent in enumerate(entries):
        # The caller asks for "<name>.stm"; the TOC entry IS that name.
        hit = engine_bsearch(keys, engine_key(ent.name))
        if hit < 0 or entries[hit].offset != ent.offset \
                or entries[hit].size_comp != ent.size_comp:
            missed.append(ent.name)
    print("     %d lookups in %.2f s" % (len(entries), time.time() - t))
    check("all %d resolve to their own record" % len(entries), not missed,
          "" if not missed else "%d missed, e.g. %s" % (len(missed), missed[:3]))

    # ---------------------------------------------------------- 4. the orphans
    print("\n4. the 992 the claim is about")
    stems = {x.name[:-4].lower() for x in entries}
    token = re.compile(rb"[A-Za-z0-9_]{5,}")
    seen = set()
    t = time.time()
    swat = game.archive("SWAT.DAT")
    for ent in swat:
        try:
            blob = swat.read(ent)
        except Exception:
            continue
        for m in token.findall(blob):
            w = m.decode("latin-1").lower()
            if w in stems:
                seen.add(w)
    orphans = sorted(stems - seen)
    print("     scanned %d SWAT.DAT entries in %.1f s"
          % (len(swat), time.time() - t))
    print("     named nowhere in SWAT.DAT          %d" % len(orphans))
    check("the orphan set is non-empty", len(orphans) > 0, str(len(orphans)))

    by_stem = {x.name[:-4].lower(): x for x in entries}
    bad = []
    for stem in orphans:
        ent = by_stem[stem]
        hit = engine_bsearch(keys, engine_key(ent.name))
        if hit < 0 or entries[hit].offset != ent.offset \
                or entries[hit].size_comp != ent.size_comp:
            bad.append(stem)
    check("EVERY orphan resolves through the same lookup", not bad,
          "%d of %d" % (len(orphans) - len(bad), len(orphans)))
    print("     e.g. %s" % ", ".join(orphans[:5]))

    # ------------------------------------------------------------- 5. controls
    print("\n5. controls -- the search can fail, and the fold is real")
    absent = "no_such_stream_zzz_%d.stm" % len(entries)
    check("a name that is not on the disc does NOT resolve",
          engine_bsearch(keys, engine_key(absent)) < 0)

    probe = orphans[0]
    shouty = by_stem[probe].name.upper()
    hit = engine_bsearch(keys, engine_key(shouty))
    check("the same orphan in UPPER CASE still resolves",
          hit >= 0 and entries[hit].offset == by_stem[probe].offset,
          shouty)

    # Every shipped name is already lower case, so the fold never fires on
    # the disc as it stands. That is worth stating rather than dressing up:
    # what the fold buys is that a name written in ANY case -- in a sound
    # bank a modder edits, say -- still lands on the same record.
    shouting = sum(1 for x in entries if x.name != x.name.lower())
    check("every shipped name is already lower case", shouting == 0,
          "%d carry upper case" % shouting)

    raw = sorted(x.name.encode("latin-1") for x in entries)
    check("without the fold that UPPER CASE name would MISS "
          "(so the fold is what found it)",
          engine_bsearch(raw, shouty.encode("latin-1")) < 0)

    backwards = list(reversed(keys))
    broke = sum(1 for x in entries[:400]
                if engine_bsearch(backwards, engine_key(x.name)) < 0)
    check("an unsorted table breaks the search (2 is not vacuous)",
          broke > 300, "%d of the first 400 lost" % broke)

    game.close()
    print("\n" + "=" * 62)
    print("SUMMARY")
    print("=" * 62)
    if FAILED:
        for f in FAILED:
            print("  [FAIL] %s" % f)
        print("\n%d check(s) failed." % len(FAILED))
        return 1
    print("  The game asks for a stream BY NAME, with a case-insensitive")
    print("  bsearch over STREAMS.DAT's own 16-byte TOC records. All %d"
          % len(entries))
    print("  streams resolve through it, including all %d that nothing on"
          % len(orphans))
    print("  the disc names. C-010 is VERIFIED-FALSE on the half that says")
    print("  the engine has no route; what is true is that nothing asks.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
