"""probe_iani.py -- `.iani` is the sampled animation for a `.pup` cutscene.

358 files, `SIA0`, 16 B to 15.8 MB. `dave.py` mentioned one only as a
name-decoding example; nothing parsed them.

    0x00  char[4]  "SIA0"
    0x04  u32      actors     -- the cast of the matching `.pup`
    0x08  f32      30.0       -- frame rate, on 355 of 358
    0x0c  u32      frames
    0x10  frames x C float32  -- C channels per frame, C = (size-16)/(frames*4)

WHAT SETTLES IT is not the header, which is ambiguous on its own, but a
cross-file identity. Every `.iani` shares its stem with a `.pup`, and `.pup`'s
roster length is known independently (`probe_pup.py`, 1,200/1,200 parsed):

    f@4 == the .pup actor count on 356 of 356 stems that have both.  100%.

So the two files are the same cutscene: `.pup` casts it and drives a sparse
timeline, `.iani` carries the per-frame samples. Their counts are unrelated and
should be -- `.pup`'s frame count is keyframes, `.iani`'s is sampled frames
(`.pup` frame count matches `.iani` frames on 0 of 356, which is the expected
answer, not a failure).

C IS NOT IN THE HEADER -- BUT IT IS IN THE FILE. **Corrected 2026-09-14 after
rdr-fmt checked it (`scripts/probe_iani_channels.py`, 3f30986); the first
version of this docstring said "C NOT STORED" and was wrong.**

What is true: no header FIELD states C. `fac_brim01_barman_01` and
`fac_duel_player1_01` have byte-identical headers -- `actors=2, frames=91` --
and are 8,024 and 8,388 bytes, C=22 and C=23. That part was measured properly.

What does not follow: that C is unavailable from the file. **The length is part
of the file.**

    C = (size - 16) / (frames * 4)

divides exactly on **354 of the 355 files that carry samples**, including
**119 of 119** with more than two actors, so it is not an artefact of the
simple case. The 364-byte gap between those two examples is exactly one channel
of 91 frames of f32 -- 91*4 -- which is what should have pointed at the length
in the first place. `parse()` below has computed C this way since it was
written: the docstring contradicted the code beside it.

The error is worth naming because it is a shape, not a slip: *"the header does
not state X"* is a measurement, *"therefore X is not recoverable"* is an
inference, and the second reached one step past the first.

The `fac_*` clips carry that claim: **233 of them agree on C=22** with
actors=2 (a camera plus one face -- the same 0x07 camera slot `probe_pup.py`
found in every roster). The `lvl_*` cutscenes run actors=3..26 and C scatters
from 172 to 2,161, which is what a sum over differently-rigged actors looks
like.

N IS CONFIRMED AS A CHANNEL COUNT, from outside the file. rdr-fmt objected --
correctly -- that dividing the length settles LAYOUT and not MEANING.
`probe_pup_channels.py` supplies the meaning: `N = sum of w[role]` over the
matching `.pup` cast fits 352 of 352 exactly across 19 role codes, and every
fitted weight equals its own role code. The `.pup` role field is a channel
count, so N is the cast's channel total.

WHAT IS NOT DECODED: **attribution** -- which track belongs to which actor,
and to which bone or morph of that actor's rig. Recovering the channel COUNT
(above, from the length) is not the same thing and does not imply it. A reader
that decodes C tracks of `frames` floats needs nothing but the `.iani`; a
reader that says which track drives whose jaw may still need the cast from the
`.pup`. That is untested. `.iani` stays PARTIAL.

The one file that does not fit is named rather than rounded away:
`lvl_tomb_pupshow_03.iani`, 7,157,504 bytes, 22 actors, 675 frames, leaving
2,650.92 floats per frame. 119 of the other 120 multi-actor clips divide
cleanly, so it is not an actor-count effect and wants explaining on its own
terms before the rule is called universal.

A NOTE ON WEAK EVIDENCE, now half retracted. "(size-16) divides by 4*frames"
is nearly free at these sizes when read as *evidence for a structure*, and that
caution stands. But it is not weak as a *computation*: dividing exactly on 354
of 355, including 119 of 119 multi-actor files, is how C is recovered, and the
two examples above confirm it against their known values. Doubting it as
evidence was right; concluding from that doubt that C was unavailable was not.

    python3 03_Tools/research/probe_iani.py [--iso PATH]
"""

import argparse
import collections
import json
import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
from rdrstudio.iso9660 import Iso9660          # noqa: E402
from rdrstudio import dave                     # noqa: E402
import probe_pup as P                          # noqa: E402

REPO = os.path.dirname(os.path.dirname(HERE))
VANILLA = os.path.join(REPO, "01_Vanilla", "RDR_VANILLA.iso")
MAGIC = b"SIA0"
HEADER = 16


class BadIani(Exception):
    pass


def parse(data):
    if len(data) < HEADER:
        raise BadIani("shorter than the header")
    if data[:4] != MAGIC:
        raise BadIani("bad magic %r" % data[:4])
    actors, fps, frames = struct.unpack_from("<IfI", data, 4)
    rem = len(data) - HEADER
    if frames == 0:
        if rem:
            raise BadIani("frames=0 but %d bytes of payload" % rem)
        return {"actors": actors, "fps": fps, "frames": 0, "channels": 0}
    if rem % (4 * frames):
        raise BadIani("payload %d is not a whole number of floats per frame "
                      "(%d frames)" % (rem, frames))
    return {"actors": actors, "fps": fps, "frames": frames,
            "channels": rem // (4 * frames)}


def inflate(backing, e):
    raw = backing.read_at(e.offset, e.size_comp)
    return raw if not e.compressed else zlib.decompress(raw, -15)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso", default=VANILLA)
    ap.add_argument("--json", default=os.path.join(REPO, "07_Research", "iani.json"))
    args = ap.parse_args()

    iso = Iso9660(args.iso)
    rows, fails = [], []
    pup_actors = {}
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b"Dave", b"DAVE"):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            stem = e.name.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
            if e.ext == "pup":
                try:
                    actors, frames = P.parse(P.inflate(d.backing, e))
                except Exception:                       # noqa: BLE001
                    continue
                pup_actors.setdefault(stem, (len(actors), len(frames)))
            elif e.ext == "iani":
                try:
                    blob = inflate(d.backing, e)
                    info = parse(blob)
                except (BadIani, zlib.error, struct.error) as ex:
                    fails.append({"name": e.name.rsplit("/", 1)[-1],
                                  "size": f.size, "why": str(ex)})
                    continue
                rows.append(dict(stem=stem, size=len(blob), **info))
    iso.close()

    both = [r for r in rows if r["stem"] in pup_actors]
    match = sum(1 for r in both if r["actors"] == pup_actors[r["stem"]][0])
    frame_match = sum(1 for r in both
                      if r["frames"] == pup_actors[r["stem"]][1])
    fps30 = sum(1 for r in rows if abs(r["fps"] - 30.0) < 1e-6)
    empty = sum(1 for r in rows if r["frames"] == 0)
    fac = [r for r in rows if r["stem"].startswith("fac_") and r["frames"]]
    chan = collections.Counter(r["channels"] for r in fac)

    json.dump({"files": len(rows) + len(fails), "parsed": len(rows),
               "failed": len(fails), "fails": fails,
               "with_pup": len(both), "actors_match_pup": match,
               "frames_match_pup": frame_match,
               "fps_30": fps30, "empty": empty,
               "fac_channels": chan.most_common(),
               "rows": rows}, open(args.json, "w"), indent=1)

    print("%d .iani; %d parse, %d fail, %d are empty (frames=0, 16 bytes)"
          % (len(rows) + len(fails), len(rows), len(fails), empty))
    for x in fails:
        print("   FAIL %-32s %s" % (x["name"][:32], x["why"]))
    print("frame rate 30.0: %d of %d" % (fps30, len(rows)))
    print("\nTHE IDENTITY -- header actors vs the matching .pup roster:")
    print("   %d of %d stems that have both  (%.0f%%)"
          % (match, len(both), 100.0 * match / max(len(both), 1)))
    print("   .pup frame count matches .iani frames: %d of %d"
          % (frame_match, len(both)))
    print("   (0 is expected: .pup frames are keyframes, .iani frames are samples)")
    print("\nchannels per frame in the %d fac_* clips:" % len(fac))
    for c, n in chan.most_common(6):
        print("   C=%-6d %4d files" % (c, n))
    print("\n-> %s" % args.json)


if __name__ == "__main__":
    main()
