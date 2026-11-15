"""Flags bit 3 means "store every other frame". Does the data agree?

Read out of the game's own decoder at 0x002D2EB0 in the unpacked executable:

    lhu   v0, 8(a1)        ; the flags word
    srl   v0, v0, 3
    andi  v0, v0, 1        ; bit 3
    beq   v0, zero, else
      lhu v0, 4(a1)        ; frames
      ...                  ; a0 = (frames / 2) + 1     <- rounded toward zero
      b   done
    else:
      lh  a0, 4(v0)        ; a0 = frames
    done:

so the number of stored keys is `frames/2 + 1` when bit 3 is set and `frames`
when it is clear. The flags byte in the FILE is at +0x10 and takes exactly four
values disc-wide -- 0x00, 0x01, 0x08, 0x09 -- i.e. bit 3 and bit 0.

The prediction: computing bits-per-sample against the CORRECTED key count
should tighten the distribution, and the half-rate and full-rate groups should
stop disagreeing. If bit 3 meant something else, dividing those files' sample
count in half would make the two groups disagree MORE, not less.

That is a real test: it can fail.
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdrstudio.game import Game                      # noqa: E402
from rdrstudio import config                          # noqa: E402

# REQ-035/036. Was a literal path to the WORKING ISO on one machine:
# unrunnable on anyone else's checkout, and pointed at the disc the live
# tests mutate. What this examines is a property of the SHIPPED data, so
# it reads the stock disc. Read-only throughout, which is what makes
# vanilla the right answer here rather than merely the tidier one.
DISC = Path(config.VANILLA_ISO)
HDR = 18
LIMIT = 4000


def spread(vals):
    """Median and the interquartile spread, as a robust width measure."""
    v = sorted(vals)
    n = len(v)
    if not n:
        return 0.0, 0.0
    med = v[n // 2]
    iqr = v[3 * n // 4] - v[n // 4]
    return med, iqr


def main():
    game = Game(DISC, writable=False)
    rows = []
    seen = set()
    for arc in game.archives():
        try:
            toc = game.toc(arc)
        except Exception:
            continue
        for e in toc:
            n = e.name.lower()
            if not n.endswith(".anim") or n in seen:
                continue
            seen.add(n)
            try:
                data = game.read(arc, e.name)
            except Exception:
                continue
            if data[:4] not in (b"cm2\x00", b"cmp\x00") or len(data) < 24:
                continue
            frames, channels = struct.unpack_from("<II", data, 4)
            if not frames or not channels:
                continue
            rows.append((e.name, len(data) - HDR, frames, channels, data[0x10]))
            if len(rows) >= LIMIT:
                break
        if len(rows) >= LIMIT:
            break
    print("%d distinct animations\n" % len(rows))

    half = [r for r in rows if r[4] & 0x08]
    full = [r for r in rows if not (r[4] & 0x08)]
    print("flags bit 3 SET (half rate) : %d files" % len(half))
    print("flags bit 3 CLEAR           : %d files" % len(full))

    def bps(rs, use_flag):
        out = []
        for _n, pay, frames, ch, fl in rs:
            keys = (frames // 2 + 1) if (use_flag and (fl & 0x08)) else frames
            if keys <= 0:
                continue
            out.append(pay * 8.0 / (keys * ch))
        return out

    print("\n" + "=" * 66)
    print("bits per sample, BEFORE applying the half-rate rule")
    print("=" * 66)
    mh, ih = spread(bps(half, False))
    mf, if_ = spread(bps(full, False))
    print("  half-rate files : median %.3f  IQR %.3f" % (mh, ih))
    print("  full-rate files : median %.3f  IQR %.3f" % (mf, if_))
    gap_before = abs(mh - mf)
    print("  gap between the two groups: %.3f" % gap_before)

    print("\n" + "=" * 66)
    print("bits per sample, AFTER applying the half-rate rule")
    print("=" * 66)
    mh2, ih2 = spread(bps(half, True))
    print("  half-rate files : median %.3f  IQR %.3f" % (mh2, ih2))
    print("  full-rate files : median %.3f  IQR %.3f" % (mf, if_))
    gap_after = abs(mh2 - mf)
    print("  gap between the two groups: %.3f" % gap_after)

    # Absolute IQR is the WRONG yardstick here and the first version of this
    # test used it: halving a file's key count roughly doubles its
    # bits-per-sample AND the absolute width of the distribution, so the raw
    # IQR was always going to grow. The scale-free comparison is IQR/median,
    # and the honest comparison of the two groups is the RATIO of their
    # medians, which is 1.0 when they agree.
    print("\n" + "=" * 66)
    print("scale-free comparison")
    print("=" * 66)
    print("  relative spread (IQR/median), half-rate : %.3f -> %.3f"
          % (ih / mh, ih2 / mh2))
    print("  relative spread (IQR/median), full-rate : %.3f (unchanged)"
          % (if_ / mf))
    r_before = max(mh, mf) / min(mh, mf)
    r_after = max(mh2, mf) / min(mh2, mf)
    print("  median ratio between the groups: %.3f -> %.3f  (1.0 = agree)"
          % (r_before, r_after))

    ok = gap_after < gap_before
    print("\n  the two groups agree better after the correction: %s"
          % ("YES" if ok else "NO"))
    print("\n  READ THIS BEFORE QUOTING IT: the decisive evidence is the CODE.")
    print("  The decoder at 0x002D2EB0 literally computes (frames/2)+1 when")
    print("  bit 3 is set, and `frames` when it is clear -- that is read off")
    print("  the instruction stream, not inferred. This size statistic is")
    print("  only weak corroboration: the medians move closer together, but")
    print("  they do not converge, so something else still varies between the")
    print("  two groups. Do not present it as an independent confirmation.")

    print("\n%s" % ("ALL CHECKS PASSED" if ok else "SOMETHING FAILED"))
    game.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
