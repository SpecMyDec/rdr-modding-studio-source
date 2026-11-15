r"""Is an `.iani` parseable without its `.pup`? Its LAYOUT is, from its length.

NAME WARNING: this file says "channels" and settles something weaker. It
recovers the per-frame FLOAT COUNT from the file's own length. That the number
IS a channel count is an interpretation nothing here confirms -- see "what this
does and does not settle". The name stays because it is the path already quoted
in REQ-018 and in commit 3f30986.

THE CLAIM, from rdr-re's `.iani` work (REQ-018 follow-up), used to argue that
`.pup` and `.iani` cannot be split across lanes because neither half parses
alone: *"The channel count C is NOT in the file — `fac_brim01_barman_01` and
`fac_duel_player1_01` have byte-identical headers (actors=2, frames=91) and
are 8,024 vs 8,388 bytes, C=22 vs C=23. The engine takes C from the actors'
rigs."*

**The observation is exactly right and the conclusion does not follow.** The
header does not STATE C. But the file still carries it, in its own length:

    magic  "SIA0"        +0
    actors u32           +4     matches the .pup roster on 355 of 355 (rdr-re)
    fps    f32           +8     30.0 throughout
    frames u32          +12
                        +16     samples begin

    C = (len(data) - 16) / (frames * 4)

On rdr-re's own two examples that gives **22 and 23** — their numbers, from the
file alone, with no rig and no cast. The two clips differ by 364 bytes, which
is exactly one track of 91 frames of f32.

**Those two values are NOT independent corroboration, and this probe first
presented them as if they were.** rdr-re's `probe_iani.py:89` computes
`rem // (4 * frames)` -- the same arithmetic -- so 22 and 23 came from this
formula, not from any external measurement. Agreement between two uses of one
formula is one measurement, not two. Recorded rather than quietly fixed,
because the error is the neighbour of the one it was correcting.

MEASURED over all 358 `.iani` on the disc, every one of them in PUPSHOW.DAT:

  * **354 of the 355 that carry samples** divide exactly. 235 have 2 actors,
    119 have more, so this is not an artefact of the simple two-actor case.
  * 3 are header-only -- 16 bytes, `frames = 0`. They carry no samples, so
    they are not counterexamples; there is nothing to count.
  * **1 does not fit**: `lvl_tomb_pupshow_03.iani`, 7,157,504 bytes, 22 actors,
    675 frames, leaving 1,789,372 floats which is 2,650.92 per frame. 119 of
    the other 120 multi-actor clips divide cleanly, so it is not an
    actor-count effect and this file wants explaining on its own terms before
    anyone builds on the rule as universal.

WHAT THIS DOES AND DOES NOT SETTLE.

  SETTLED: the payload is an exact `frames x N x 4` block, so **N is
  recoverable from the file's length alone**. That is enough to kill "the
  channel count is not in the file" as a reason an `.iani` cannot be read
  without its `.pup` -- the LAYOUT is readable either way.

  SETTLED SINCE, BY SOMEONE ELSE, AND THIS IS THE CORROBORATION THIS PROBE
  LACKED: that N means "channels". rdr-re's `research/probe_pup_channels.py`
  (`014d2c7`) fits `N = sum over the cast of w[role]` against the `.pup` roster
  and gets **352 of 352 exact**, with the fitted weight equal to the role code
  itself. That is an EXTERNAL measurement -- N from this file's length, the
  role codes from a different file -- so the two agree without sharing an
  arithmetic, which is exactly what was missing when this probe wrongly treated
  rdr-re's C=22/23 as independent. It also explains the weak signal below:
  **233 of the 354 are `(actors=2, N=22)`, 11 per actor**, and 245 of 354 have
  N divisible by their actor count -- two roles of 11 summing to 22. The 109
  that do not divide are casts with unequal roles, which the sum handles and a
  per-actor constant never could.

  ALSO NOT SETTLED: ATTRIBUTION -- which track belongs to which actor, and to
  which bone or morph. A reader that decodes N tracks of `frames` floats needs
  nothing but this file; one that says which track drives whose jaw may still
  need the cast. Only the second argues for `.pup` and `.iani` sharing a
  module, so that is where the ownership question should be settled.

  rdr-re's own caution applies to the evidence here: at these sizes
  divisibility is cheap, and 354 files each dividing by their own `frames * 4`
  is strong for a LAYOUT while saying nothing about a MEANING.

    python3 03_Tools/scripts/probe_iani_channels.py [iso]

Read-only. Opens the pristine disc and writes nothing.
"""

import os
import struct
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _HERE]
sys.path.insert(0, os.path.dirname(_HERE))

from rdrstudio import config                                    # noqa: E402
from rdrstudio.game import Game                                 # noqa: E402

MAGIC = b"SIA0"
HEADER = 16


def header_of(data):
    """(actors, fps, frames) -- or None if this is not an SIA0 file."""
    if len(data) < HEADER or data[:4] != MAGIC:
        return None
    actors, fps, frames = struct.unpack_from("<IfI", data, 4)
    return actors, fps, frames


def channels(data):
    """C from the file alone, or None when it cannot be derived."""
    head = header_of(data)
    if head is None:
        return None
    _actors, _fps, frames = head
    span = frames * 4
    rest = len(data) - HEADER
    if frames <= 0 or span <= 0 or rest % span:
        return None
    return rest // span


def main():
    iso = sys.argv[1] if len(sys.argv) > 1 else config.VANILLA_ISO
    print("disc: %s  (read-only)\n" % iso)
    game = Game(iso, writable=False)

    exact = empty = 0
    odd = []
    actors_hist = Counter()
    cited = {}
    for arch in game.archives():
        try:
            dave = game.archive(arch)
        except Exception:
            continue
        for e in dave:
            if e.ext != "iani":
                continue
            try:
                data = dave.read(e)
            except Exception:
                continue
            head = header_of(data)
            if head is None:
                odd.append((e.name, len(data), "not SIA0"))
                continue
            actors, _fps, frames = head
            if frames <= 0:
                empty += 1
                continue
            c = channels(data)
            if c is None:
                odd.append((e.name, len(data),
                            "%d actors, %d frames, %d bytes of samples"
                            % (actors, frames, len(data) - HEADER)))
                continue
            exact += 1
            actors_hist[min(actors, 3)] += 1
            if e.name in ("fac_brim01_barman_01.iani",
                          "fac_duel_player1_01.iani"):
                cited[e.name] = (len(data), actors, frames, c)
    game.close()

    print("1. the two clips the claim is built on")
    for name in sorted(cited):
        size, actors, frames, c = cited[name]
        print("     %-30s %5d bytes, actors=%d frames=%d  ->  C=%d"
              % (name, size, actors, frames, c))
    ok = len(cited) == 2 and {v[3] for v in cited.values()} == {22, 23}
    print("     %s the file alone yields C=22 and C=23, which are the numbers"
          % ("ok  " if ok else "FAIL"))
    print("          the claim says are not in the file.\n")

    total = exact + empty + len(odd)
    print("2. every .iani on the disc")
    print("     %d files: %d divide exactly, %d are header-only (frames=0),"
          % (total, exact, empty))
    print("     %d do not fit" % len(odd))
    print("     of the %d that divide, %d have >2 actors"
          % (exact, actors_hist.get(3, 0)))
    for name, size, why in odd:
        print("       unexplained: %-28s %9d bytes  (%s)" % (name, size, why))

    good = ok and exact > 300 and len(odd) <= 1
    print("\n" + "=" * 68)
    print("SUMMARY")
    print("=" * 68)
    if not good:
        print("  The rule does not hold as stated; do not build on it.")
        return 1
    print("  N = (len - 16) / (frames * 4) is exact for %d of the %d clips that"
          % (exact, exact + len(odd)))
    print("  carry samples, so the payload is a frames x N x 4 block and N is")
    print("  recoverable from the file's LENGTH. The header does not STATE it,")
    print("  which was observed correctly; that does not make it unavailable.")
    print("  Whether N means CHANNELS is a separate claim this does not test,")
    print("  and attribution -- which track drives which actor -- is a third.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
