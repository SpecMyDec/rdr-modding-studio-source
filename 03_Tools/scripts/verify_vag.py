"""Do the `.bd` sound banks really decode to audio?

A wrong ADPCM decoder still produces 16-bit numbers, so "it ran" proves
nothing and a waveform picture proves little more. These checks are chosen so
that a decoder with the wrong predictor, the wrong nibble order or the wrong
shift fails them:

  1. **Format gate.** Every bank must satisfy the SPU block constraint --
     shift <= 12, filter <= 4 -- on essentially every block. Unrelated bytes
     score about 0.25 on this, and `.stm` bodies score 0.441, so a bank that
     is not SPU-ADPCM cannot sneak through.
  2. **Flags are legal.** Byte 1 of a block may only be a combination of END,
     LOOP and LOOP_START.
  3. **It sounds like audio, statistically.** Decoded sounds must be centred
     near zero (a wrong predictor drifts, because the two-tap filter is
     recursive and any error accumulates), must not clip constantly, and
     must be strongly autocorrelated at lag 1 for tonal sounds. Percussive
     sounds legitimately are not, so this is a *proportion* over many sounds,
     not a rule for each one.
  4. **No runaway.** The predictor is an IIR filter; get the coefficients
     wrong and the output saturates. A decode where most samples sit at the
     clip rails is a failure even though every sample is "valid".
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdrstudio.game import Game                      # noqa: E402
from rdrstudio import config                         # noqa: E402
from rdrstudio import vag                             # noqa: E402

# The pristine disc, from config rather than a hardcoded `D:\` literal.
# `config.ROOT` derives from the module's own location, so this also
# stops the script being Windows-only -- it could not run under WSL
# python at all before. REQ-035's second consequence, in a file that
# was never on its list.
DISC = Path(config.VANILLA_ISO)
LEGAL_FLAGS = {0, 1, 2, 3, 4, 5, 6, 7}


def corr(v):
    n = len(v)
    if n < 3:
        return 0.0
    m = sum(v) / n
    num = sum((v[i] - m) * (v[i + 1] - m) for i in range(n - 1))
    den = sum((x - m) ** 2 for x in v)
    return num / den if den else 0.0


def main():
    game = Game(DISC, writable=False)
    ok = True

    def check(what, good, detail=""):
        nonlocal ok
        ok &= bool(good)
        print("  %-52s %s%s" % (what, "ok" if good else "FAIL",
                                ("  " + detail) if detail else ""))

    banks = [e for e in game.toc("BANKS.DAT") if e.ext.lower() == "bd"]
    print("%d .bd banks in BANKS.DAT\n" % len(banks))
    check("found the sound banks", len(banks) > 100, "%d" % len(banks))

    # ---- 1 and 2: every bank, format gate
    worst_gate = 1.0
    bad_flags = 0
    total_blocks = 0
    total_sounds = 0
    for e in banks:
        try:
            d = game.read("BANKS.DAT", e.name)
        except Exception:
            continue
        worst_gate = min(worst_gate, vag.looks_like_adpcm(d, blocks=100000))
        for i in range(0, len(d) - 16, 16):
            total_blocks += 1
            if d[i + 1] not in LEGAL_FLAGS:
                bad_flags += 1
        total_sounds += len(vag.split_sounds(d))
    print("  %d blocks across every bank, %d sounds\n"
          % (total_blocks, total_sounds))
    check("every bank is SPU-ADPCM", worst_gate >= 0.999,
          "worst bank scores %.4f" % worst_gate)
    check("no illegal block flags", bad_flags == 0, "%d bad" % bad_flags)

    # ---- 3 and 4: decode a spread of sounds and measure them
    dc_worst = 0.0
    clipped_worst = 0.0
    tonal = 0
    measured = 0
    for e in banks[:24]:
        try:
            d = game.read("BANKS.DAT", e.name)
        except Exception:
            continue
        for off, nb in vag.split_sounds(d)[:6]:
            if nb < 8:
                continue
            s = vag.decode(d, off, nb * 16)
            if len(s) < 512:
                continue
            measured += 1
            # DC measured against the sound's own PEAK, not in absolute
            # counts. An absolute threshold says nothing: a loud sound and a
            # quiet one drift by different amounts for the same decoder error.
            # Measured here -- median |mean| is 14.2 over the 68 sounds longer
            # than 3,000 samples, i.e. dead centre; the single short sound sits
            # at 7% of peak because the predictor starts from silence and a
            # short sound never averages it out. That is the format, not a bug.
            pk = max(1, max(abs(x) for x in s))
            dc_worst = max(dc_worst, abs(sum(s) / len(s)) / pk)
            rails = sum(1 for x in s if x <= -32700 or x >= 32700)
            clipped_worst = max(clipped_worst, rails / len(s))
            if corr(s) > 0.5:
                tonal += 1

    print("  measured %d decoded sounds" % measured)
    check("decoded enough sounds to judge", measured > 40, "%d" % measured)
    # A wrong predictor drifts: the recursion has no DC blocker, so an error
    # in the coefficients accumulates and shows up as offset proportional to
    # signal level.
    check("output stays centred (no predictor drift)", dc_worst < 0.15,
          "worst |mean|/peak = %.3f" % dc_worst)
    # A wrong coefficient set saturates instead of tracking.
    check("output does not saturate", clipped_worst < 0.25,
          "worst clipped fraction %.3f" % clipped_worst)
    frac = tonal / measured if measured else 0
    check("most sounds are strongly autocorrelated", frac > 0.5,
          "%.0f%% above +0.5 (percussion legitimately is not)" % (100 * frac))


    # ---- 6. the .hd gives real rates, and encode/decode round-trips
    print("\n.hd headers and the encoder:")
    rates = set()
    hd_banks = 0
    worst_snr = 99.0
    tested = 0
    for e in banks[:40]:
        try:
            bd = game.read("BANKS.DAT", e.name)
            hd = game.read("BANKS.DAT", e.name[:-3] + ".hd")
        except Exception:
            continue
        snds = vag.sounds_with_rates(bd, hd)
        if not snds or snds[0]["source"] != "hd":
            continue
        hd_banks += 1
        for x in snds:
            if x["rate"]:
                rates.add(x["rate"])
        if tested < 6:
            for x in snds[:2]:
                orig = vag.decode(bd, x["offset"], x["blocks"] * 16,
                                  stop_at_end=False)
                if len(orig) < 2000:
                    continue
                enc = vag.encode(orig)
                back = vag.decode(enc, 0, len(enc), stop_at_end=False)
                m = min(len(orig), len(back))
                num = sum((orig[i] - back[i]) ** 2 for i in range(m)) / m
                sig = sum(v * v for v in orig[:m]) / m
                if num:
                    worst_snr = min(worst_snr, 10 * math.log10(sig / num))
                tested += 1
    check("banks carry a readable .hd", hd_banks > 20, "%d" % hd_banks)
    # Rates genuinely vary; a decoder that assumed one would be wrong here.
    check("the .hd states MANY different rates", len(rates) > 4,
          "%d distinct: %s" % (len(rates), sorted(rates)[:6]))
    # A wrong predictor or shift search shows up as low SNR. The shift search
    # once ran the wrong way and scored 12.9 dB; correct is around 30.
    check("encode -> decode round-trips cleanly", worst_snr > 25.0,
          "worst SNR %.1f dB over %d sounds" % (worst_snr, tested))

    # ---- the negative control: .stm must NOT pass the gate
    stm = [e for e in game.toc("STREAMS.DAT") if e.ext.lower() == "stm"][:3]
    worst_stm = 0.0
    for e in stm:
        d = game.read("STREAMS.DAT", e.name)[2048:]
        worst_stm = max(worst_stm, vag.looks_like_adpcm(d, blocks=20000))
    print("\n  control: streamed .stm scores %.3f on the same gate" % worst_stm)
    check("the gate rejects the streamed .stm codec", worst_stm < 0.9,
          "%.3f" % worst_stm)

    print("\n%s" % ("ALL CHECKS PASSED" if ok else "SOMETHING FAILED"))
    game.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
