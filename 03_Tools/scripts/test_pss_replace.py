"""[M19] A .pss frame decodes, and a cutscene is replaced and re-reads.

    python3 scripts/test_pss_replace.py        (no Studio needed)

Offline on purpose: M19's two halves are a DECODER and a loose-ISO write,
neither of which goes through a route. It writes the working ISO, so it takes
the suite lock like any other write, and it restores what it touched.

## What it asserts

  * a real cutscene frame DECODES to pixels -- not "no exception": the
    macroblock count must equal rows x columns exactly, and the image must
    carry actual contrast, because a silently-wrong decoder produces a
    plausible flat frame (that is how the double-inverse-scan bug looked);
  * the decode generalises -- three different .pss files, not just the one
    the decoder was written against;
  * a replacement that is TOO BIG is refused with the numbers;
  * a replacement is padded to EXACTLY the original extent, because
    `Iso9660` never changes a file's length and a short write would leave the
    game reading stale bytes past the end;
  * `dry` defaults to TRUE -- checked with the flag ABSENT, not just false;
  * the write lands and THE ISO BYTES CHANGE;
  * the replaced file re-reads as the NEW video: its first I-frame decodes to
    the replacement's content, asserted against the source's own pixels
    rather than against "it parsed";
  * the file is restored byte-identically.

Every comparison that could pass on empty or flat data reports how much it
actually read, and the "is not the old cutscene" check asserts the two
differ enough that it is capable of failing.
"""
import hashlib
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _HERE]
sys.path.insert(0, os.path.dirname(_HERE))

from rdrstudio import config, pss, pssvideo                 # noqa: E402
from rdrstudio.game import Game                             # noqa: E402

TARGET = "PSS/BRIDGE05.PSS"       # 4,243,460 bytes -- the slot
SOURCE = "PSS/COACH02.PSS"        # 1,703,940 bytes -- the replacement
FAILURES = []
_PRISTINE = []
_RESTORED = []


def check(cond, label, detail=""):
    print("  [%s] %-54s %s" % ("PASS" if cond else "FAIL", label, detail))
    if not cond:
        FAILURES.append(label)


def read_loose(game, path):
    e = game.iso.get(path)
    return game.iso.read_at(e.offset, e.size)


def first_iframe(data):
    es = pssvideo.demux_video(data)
    offs = pssvideo.iframe_offsets(es)
    if not offs:
        return None
    return pssvideo.decode_frame(es, offs[len(offs) // 2])


def contrast(frame):
    y = frame["y"]
    return max(y) - min(y)


def restore():
    """Put the cutscene back, and prove it went back."""
    if _RESTORED or not _PRISTINE:
        return
    _RESTORED.append(True)
    try:
        g = Game(config.MODDED_ISO, writable=True)
        e = g.iso.get(TARGET)
        if g.iso.read_at(e.offset, e.size) == _PRISTINE[0]:
            check(True, "already as it started -- nothing to undo")
            g.close()
            return
        g.iso.write_at(e.offset, _PRISTINE[0])
        g.iso.flush()
        g.close()
    except Exception as ex:
        check(False, "revert", str(ex))
        return
    g2 = Game(config.MODDED_ISO)
    check(read_loose(g2, TARGET) == _PRISTINE[0],
          "the cutscene is byte-identical to how it started")


def _run():
    van = Game(config.VANILLA_ISO)

    print("1. a real cutscene frame decodes to pixels:")
    src_bytes = read_loose(van, SOURCE)
    fr = first_iframe(src_bytes)
    check(fr is not None, "an intra frame was found and decoded")
    check(fr["macroblocks"] == fr["expected"],
          "every macroblock decoded, none skipped",
          "%d of %d" % (fr["macroblocks"], fr["expected"]))
    check(fr["width"] == 512 and fr["height"] == 448,
          "dimensions agree with the sequence header",
          "%dx%d" % (fr["width"], fr["height"]))
    check(fr["width"] * fr["height"] == len(fr["y"]),
          "the luma plane is fully populated", "%d samples" % len(fr["y"]))
    check(contrast(fr) > 40,
          "the image has real contrast, not a flat frame",
          "luma spread %d" % contrast(fr))
    rgb = pssvideo.to_rgb(fr)
    check(len(rgb) == fr["width"] * fr["height"] * 3,
          "it converts to RGB at full size", "%d bytes" % len(rgb))

    print()
    print("2. the decoder is not tuned to one file:")
    for name in ("PSS/RSLOGO.PSS", "PSS/SALOON06.PSS", "PSS/STGOPN01.PSS"):
        f2 = first_iframe(read_loose(van, name))
        ok = f2 and f2["macroblocks"] == f2["expected"] and contrast(f2) > 20
        check(bool(ok), "%s decodes completely" % name.split("/")[-1],
              "%d/%d mb, spread %d" % (f2["macroblocks"], f2["expected"],
                                       contrast(f2)) if f2 else "no frame")

    print()
    print("3. a replacement too big for the slot is refused, with numbers:")
    g = Game(config.MODDED_ISO)
    before = read_loose(g, TARGET)
    _PRISTINE.append(before)
    big = read_loose(van, "PSS/BRIDGE04.PSS")          # 26.8 MB into 4.2 MB
    plan, problem, _ = pssvideo.plan_replace(g, TARGET, big)
    check(problem is not None, "refused")
    check((problem or {}).get("code") == "slot_overflow", "code",
          (problem or {}).get("code"))
    check("too long" in (problem or {}).get("message", ""),
          "it says how far over", (problem or {}).get("message", "")[:46])
    check(plan.get("fits") is False, "plan.fits is False")

    print()
    print("4. a shorter replacement is padded to the slot EXACTLY:")
    plan, problem, padded = pssvideo.plan_replace(g, TARGET, src_bytes)
    check(problem is None, "accepted", str(problem)[:50])
    check(len(padded) == plan["slot"],
          "padded to the byte", "%d == %d" % (len(padded), plan["slot"]))
    check(padded.endswith(pssvideo.END_CODE),
          "the program end code is still last")
    check(pss.is_pss(padded[:16]), "and it is still a Program Stream")
    check(plan["padding"] == plan["slot"] - len(src_bytes),
          "the plan states the padding", "%d bytes" % plan["padding"])

    print()
    print("5. dry run writes nothing -- including when `dry` is ABSENT:")
    r = pssvideo.replace(g, TARGET, src_bytes, dry=True)
    check(r["written"] is False, "written:false is stated, not implied")
    r = pssvideo.replace(g, TARGET, src_bytes)          # no dry=
    check(r["written"] is False, "an OMITTED dry flag does NOT write",
          "dry=%s" % r.get("dry"))
    check(read_loose(Game(config.MODDED_ISO), TARGET) == before,
          "the file on disc is untouched")

    print()
    print("6. the write lands and the ISO bytes change:")
    gw = Game(config.MODDED_ISO, writable=True)
    r = pssvideo.replace(gw, TARGET, src_bytes, dry=False)
    gw.close()
    check(r.get("written") is True, "written:true", str(r.get("problem"))[:40])
    after = read_loose(Game(config.MODDED_ISO), TARGET)
    check(after != before, "THE ISO BYTES CHANGED",
          "sha1 %s -> %s" % (hashlib.sha1(before).hexdigest()[:10],
                             hashlib.sha1(after).hexdigest()[:10]))
    check(len(after) == len(before), "and the extent length is unchanged",
          "%d" % len(after))

    print()
    print("7. it re-reads as the NEW cutscene, not the old one:")
    got = first_iframe(after)
    want = fr
    check(got is not None, "the replaced file still decodes")
    check(got["macroblocks"] == got["expected"],
          "completely", "%d/%d" % (got["macroblocks"], got["expected"]))
    # Say how much was actually compared. rdr-3d's texture_table check
    # reported 269 passes that were every EMPTY table comparing [] with [],
    # because Chunk has no __eq__ -- it had never compared two real tables.
    # A byte-equality check has the same failure mode the moment one side is
    # empty or flat, so these numbers are printed rather than assumed.
    a, b = bytes(got["y"]), bytes(want["y"])
    old = first_iframe(before)
    c = bytes(old["y"])
    check(len(a) and len(a) == len(b),
          "there is something to compare at all",
          "%d bytes, %d distinct values" % (len(a), len(set(a))))
    check(len(set(b)) > 1 and len(set(c)) > 1,
          "and neither side is a flat frame",
          "new %d distinct, old %d distinct" % (len(set(b)), len(set(c))))
    differing = sum(1 for x, y in zip(b, c) if x != y)
    check(differing > len(b) // 10,
          "the two cutscenes genuinely differ, so this check CAN fail",
          "%d of %d samples (%.1f%%)"
          % (differing, len(b), 100.0 * differing / len(b)))
    check(a == b, "and its frame is the REPLACEMENT's, pixel for pixel")
    check(a != c, "and is NOT the cutscene that was there before")

    print()
    print("8. restore:")
    restore()

    print()
    print("SUMMARY")
    if FAILURES:
        print("  [FAIL] %d check(s) failed: %s"
              % (len(FAILURES), "; ".join(FAILURES[:3])))
        return 1
    print("  [PASS] a .pss frame decodes; a cutscene is replaced and re-reads")
    return 0


def main():
    try:
        return _run()
    finally:
        if _PRISTINE and not _RESTORED:
            print()
            print("restore (after an aborted run):")
            restore()


if __name__ == "__main__":
    sys.exit(main())
