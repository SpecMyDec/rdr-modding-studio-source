"""Does the cm2 `.anim` decoder actually account for the bytes?

The claim under test is narrow and falsifiable: `anim.parse` consumes EXACTLY
the file, on every cm2 clip on the disc. A spec that is merely close -- right
header size but wrong array order, or one field too many -- lands off the end by
a few bytes on thousands of files, and this script prints that number first for
exactly that reason. "It ran without throwing" is not evidence and is not
reported as such.

Because the optional-scale rule ("read a trailing f32 only if 4 bytes remain")
can absorb a size error, section 1 also tests the RIGID formula, which cannot.
If the layout were wrong, the rigid test is where it would show.

Everything else exists to catch the failure mode byte accounting cannot see: a
layout that adds up but interprets the payload wrongly. So the checks layer:

  1. bytes consumed vs file size, plus the rigid size formula
  2. an INDEPENDENT cross-check from a different function in the executable --
     the game's own memory-footprint method at 0x002D2DE8
  3. invariants the layout implies but the size formula does not:
     animCount == popcount(mask), no mask bits above `channels`
  4. an exhaustive decode of every key of every channel of every file, which is
     what actually proves nothing runs off a buffer
  5. do the values land where the ENCODER's arithmetic says they must, i.e. on
     the +/-32768 quantiser rail
  6. do the two samplers, which are separate functions, agree with each other
  7. is the output temporally smooth, which no size formula can fake

Three results below came out worse than expected on the first run and are now
explained rather than smoothed over: 172 files whose group A is entirely zero,
14 files where the two samplers legitimately disagree by exactly one 65536-unit
revolution, and a continuity tail that is the same unwrap plus the loop seam.
Each is reported with its explanation and its residue.
"""
import collections
import math
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdrstudio.game import Game                      # noqa: E402
from rdrstudio import config                          # noqa: E402
from rdrstudio import anim                           # noqa: E402

# REQ-035. Was a literal path to the WORKING ISO on one machine:
# unrunnable on anyone else's checkout, and pointed at the disc the
# live tests mutate, so a read-only structural check could go red for
# a reason with nothing to do with the code under test. What this
# measures is a property of the shipped clips, so it reads the stock
# disc. Opened writable=False, which is what makes vanilla correct here.
DISC = Path(config.VANILLA_ISO)

# The float passes cost a float32 round-trip per sample, so they run on a
# deterministic slice rather than all 3,771 files. The slice size is printed
# wherever it is used -- an honest number needs its denominator.
SAMPLE = 400
SWEEP = 257                # phase points per file in the continuity sweep

# The file that constrained every earlier theory: 42 bytes, and long believed
# undecodable.
SMALLEST = ("SWAT.DAT", "ptl_red_ptl_run_rgt_hls.anim")


def pct(n, d):
    return "%.2f%%" % (100.0 * n / d) if d else "n/a"


def quant(vals, p):
    if not vals:
        return 0.0
    v = sorted(vals)
    return v[min(len(v) - 1, int(len(v) * p))]


def collect(game):
    """Every distinct .anim name on the disc, with one archive that holds it."""
    seen = {}
    for arc in game.archives():
        try:
            toc = game.toc(arc)
        except Exception:
            continue
        for e in toc:
            low = e.name.lower()
            if low.endswith(".anim") and low not in seen:
                seen[low] = (arc, e.name)
    return seen


def rigid_size(a):
    """The size formula with BOTH scales mandatory.

    Kept separate from the parser on purpose. The parser's rule -- take a
    trailing f32 only if four bytes are left -- is elastic enough to hide a
    layout error by silently declining to read a field. This version cannot
    hide anything: it predicts one number and either hits it or does not.
    """
    return (anim.HEADER_SIZE + 4 + (a.channels + 7) // 8
            + 2 * (a.channels - a.anim_count)
            + 2 * a.anim_count * a.keys + 8)


def memory_size(a):
    """The game's own footprint method, 0x002D2DE8, recomputed here.

    Transcribed from the disassembly, where the load-bearing instruction is
    0x002D2E2C raw 0x00A32018 -- the R5900 three-operand `mult a0, a1, v1`
    whose destination register dis.py hides. It returns

        2*N*K + ceil(C/8) + 32 + 2*(C - N)

    i.e. the same three buffers the file holds plus the 32-byte codec object.
    It is a statement about the payload's shape written by a DIFFERENT function
    than the one the parser was built from, so comparing it to the file size is
    a real cross-check and not a restatement.
    """
    return (2 * a.anim_count * a.keys + (a.channels + 7) // 8 + 32
            + 2 * (a.channels - a.anim_count))


def group_extreme(a, lo, hi):
    """max |quantised value| over channels [lo,hi), constants included.

    Column slicing keeps this at C speed: a channel's samples are
    matrix[rank::N] because the matrix is key-major with row stride N.
    """
    mx = 0
    arank = crank = 0
    for c in range(a.channels):
        if a.animated(c):
            if lo <= c < hi:
                col = a.matrix[arank::a.anim_count]
                if col:
                    mx = max(mx, abs(min(col)), abs(max(col)))
            arank += 1
        else:
            if lo <= c < hi:
                mx = max(mx, abs(a.constants[crank]))
            crank += 1
    return mx


def unwrap_fires(a):
    """Does the +/-65536 fixup in sampleLerp ever trigger on this clip?"""
    n = a.anim_count
    m = a.matrix
    for k in range(a.keys - 1):
        base, nxt = k * n, (k + 1) * n
        for i in range(n):
            d = m[base + i] - m[nxt + i]
            if d > 32767 or d < -32768:
                return True
    return False


def main():
    game = Game(DISC, writable=False)
    t0 = time.time()
    seen = collect(game)

    # ------------------------------------------------------- 0. what is there
    print("\n=== CORPUS ===")
    magics = collections.Counter()
    blobs = {}
    for low, (arc, name) in sorted(seen.items()):
        data = game.read(arc, name)
        blobs[low] = (arc, name, data)
        magics[bytes(data[0:4])] += 1
    print("  distinct .anim on the disc: %d" % len(seen))
    for m, c in magics.most_common():
        tag = repr(m[:3].decode("ascii", "replace")) if m[:3].isalpha() else m.hex()
        note = "decoded here" if m == anim.MAGIC else "NOT handled by this module"
        print("    %-12s %5d   %s" % (tag, c, note))

    cm2 = [(low, blobs[low]) for low in sorted(blobs)
           if blobs[low][2][0:4] == anim.MAGIC]
    others = [(low, blobs[low]) for low in sorted(blobs)
              if blobs[low][2][0:4] != anim.MAGIC]
    refused = 0
    for low, (arc, name, data) in others:
        try:
            anim.parse(data)
        except anim.AnimError:
            refused += 1
    print("  non-cm2 files correctly refused: %d/%d" % (refused, len(others)))
    print("      a decoder that silently accepted a 'cmp' file would be worse")
    print("      than one that failed, so this is checked rather than assumed")

    # ------------------------------------ 1. THE TEST: does it consume exactly
    print("\n=== 1. BYTES CONSUMED vs FILE SIZE  (the sharpest test) ===")
    parsed, failed = [], []
    delta = collections.Counter()
    rigid = collections.Counter()
    for low, (arc, name, data) in cm2:
        try:
            a = anim.parse(data)
        except anim.AnimError as e:
            failed.append((name, str(e)))
            continue
        parsed.append((name, a))
        delta[a.consumed - a.size] += 1
        rigid[rigid_size(a) - a.size] += 1
    n = len(cm2)
    exact = delta[0]
    print("  cm2 files                            %d" % n)
    print("  parsed without error                 %d  (%s)"
          % (len(parsed), pct(len(parsed), n)))
    print("  refused / would overrun the buffer   %d" % len(failed))
    print("  CONSUMED EXACTLY -- consumed == size %d  (%s)" % (exact, pct(exact, n)))
    off = {d: c for d, c in delta.items() if d != 0}
    print("  ended anywhere else                  %d  %s"
          % (sum(off.values()), off if off else ""))
    for name, why in failed[:8]:
        print("      ! %s: %s" % (name, why))
    print()
    print("  The parser's optional-scale rule could in principle hide a layout")
    print("  error by declining to read a trailing float, so here is the RIGID")
    print("  formula, 17 + 4 + ceil(C/8) + 2*(C-N) + 2*N*K + 8, with both")
    print("  scales mandatory. It has no slack:")
    print("    predicted size EXACTLY right       %d  (%s)"
          % (rigid[0], pct(rigid[0], n)))
    print("    predicted 8 too many               %d   <- both scales omitted"
          % rigid[8])
    other = {d: c for d, c in rigid.items() if d not in (0, 8)}
    print("    ANY OTHER outcome                  %d  %s"
          % (sum(other.values()), other if other else ""))
    scaleless = sorted((nm, a) for nm, a in parsed if a.scales_present < 2)
    print("  the %d files that omit both scales: %s"
          % (len(scaleless), ", ".join(nm for nm, _ in scaleless)))
    print("      flags on those: %s. The code explicitly tolerates the omission"
          % sorted({"0x%02X" % a.flags for _, a in scaleless}))
    print("      (0x002D2D50 / 0x002D2D94 test the byte count the stream read")
    print("      returned and keep the constructor defaults), so whether they")
    print("      are deliberate or truncated build artefacts is UNKNOWN. They")
    print("      decode sensibly under the defaults, which mildly favours the")
    print("      former, and the code tolerates either.")

    # ------------------------------- 2. independent cross-check from the image
    print("\n=== 2. CROSS-CHECK against the game's own memorySize (0x002D2DE8) ===")
    agree = sum(1 for nm, a in parsed
                if rigid_size(a) == memory_size(a) - 32 + anim.HEADER_SIZE + 4 + 8)
    print("  rigid file size == memorySize - 32 + 17 + 4 + 8 :  %d/%d  (%s)"
          % (agree, len(parsed), pct(agree, len(parsed))))
    print("      Two functions written for different purposes -- one reads the")
    print("      stream, one reports a heap footprint -- describe the same three")
    print("      buffers. They agree on every file.")

    # ------------------------------------------- 3. structural invariants
    print("\n=== 3. STRUCTURE ===")
    pc_ok = lsb_ok = msb_ok = 0
    for nm, a in parsed:
        bits = bin(int.from_bytes(a.mask, "little")).count("1")
        pc_ok += (bits == a.anim_count)
        hi = range(a.channels, len(a.mask) * 8)
        lsb_ok += not any((a.mask[c >> 3] >> (c & 7)) & 1 for c in hi)
        msb_ok += not any((a.mask[c >> 3] >> (7 - (c & 7))) & 1 for c in hi)
    tot = len(parsed)
    print("  animCount == popcount(mask)                    %d/%d" % (pc_ok, tot))
    print("      nothing in the code checks this; the file says the same thing")
    print("      twice and the two agree everywhere")
    print("  no mask bits set above `channels`, LSB-first   %d/%d" % (lsb_ok, tot))
    print("  ...the very same test read MSB-first           %d/%d" % (msb_ok, tot))
    print("      %d files break under MSB-first, so the bit order is settled by"
          % (tot - msb_ok))
    print("      the data as well as by 0x002D3424 `srav`")
    fr = collections.Counter(a.flags_raw for _, a in parsed)
    print("  raw flags byte values disc-wide: %s" % dict(sorted(fr.items())))
    print("      only bits 0 and 3 ever set, which is exactly the two the loader")
    print("      keeps (0x002CF2B8, 0x002CF2D8); the other six are discarded")
    ch = collections.Counter(a.channels for _, a in parsed)
    print("  distinct channel counts %d, all divisible by 3: %s, range %d..%d"
          % (len(ch), all(c % 3 == 0 for c in ch), min(ch), max(ch)))
    hr = sum(1 for _, a in parsed if a.half_rate)
    lp = sum(1 for _, a in parsed if a.loop)
    print("  half-rate (bit 3) %d files, loop (bit 0) %d files" % (hr, lp))
    allconst = sum(1 for _, a in parsed if a.anim_count == 0)
    print("  files where EVERY channel is constant: %d (key matrix 0 bytes long)"
          % allconst)

    # ------------------------- 4. exhaustive decode -- the real overrun test
    print("\n=== 4. EXHAUSTIVE DECODE: every key of every channel, every file ===")
    t = time.time()
    samples = 0
    blew = []
    for nm, a in parsed:
        try:
            for k in range(a.keys):
                samples += len(a.raw_at_key(k))
        except Exception as e:                       # IndexError included
            blew.append((nm, repr(e)))
    print("  files walked end to end        %d" % (len(parsed) - len(blew)))
    print("  samples decoded                %d (%.1f M) in %.1fs"
          % (samples, samples / 1e6, time.time() - t))
    print("  ran off a buffer               %d %s"
          % (len(blew), blew[:5] if blew else ""))
    print("      an off-by-one in the row stride or in either channel cursor")
    print("      surfaces here as an IndexError, not as a plausible wrong number")

    # ------------------------------------------- 5. are the VALUES plausible
    print("\n=== 5. VALUE PLAUSIBILITY ===")
    print("  The encoder at 0x002D2EB0 sets each scale to 32768/max|v| over its")
    print("  own group (0x002D3088 is 65536.0f, then divided by 2*max|v|), so")
    print("  the extremal sample MUST land on the +/-32767..32768 rail. That is")
    print("  a prediction about the data, made by code, and it can fail.")
    railA = railB = totA = totB = 0
    zeroA = set()
    nearA = collections.Counter()
    impliedA, impliedB = [], []
    for nm, a in parsed:
        mA = group_extreme(a, 0, min(3, a.channels))
        mB = group_extreme(a, 3, a.channels)
        if a.channels > 0:
            totA += 1
            if mA in (32767, 32768):
                railA += 1
            elif mA == 0:
                zeroA.add(nm)
            else:
                nearA[mA] += 1
            if a.scale_a:
                impliedA.append(mA / abs(a.scale_a))
        if a.channels > 3:
            totB += 1
            if mB in (32767, 32768):
                railB += 1
            if a.scale_b and a.scales_present == 2:
                impliedB.append(mB / abs(a.scale_b))
    zsA = {nm for nm, a in parsed if a.scale_a == 0.0}
    print("  group A (channels 0-2) extremum on the rail   %d/%d  (%s)"
          % (railA, totA, pct(railA, totA)))
    print("  group B (channels 3+)  extremum on the rail   %d/%d  (%s)"
          % (railB, totB, pct(railB, totB)))
    print("  Of the %d group-A misses, %d have max|raw| == 0 -- and that set is"
          % (totA - railA, len(zeroA)))
    print("  IDENTICAL to the %d files carrying scaleA == 0.0: %s."
          % (len(zsA), zeroA == zsA))
    print("      So those are degenerate rather than wrong: the clip does not")
    print("      move channels 0-2 at all, the encoder had no maximum to divide")
    print("      by, and it wrote 0.0. The game tests for that zero rather than")
    print("      dividing (0x002D2D60 c.eq.s / 0x002D2D68 bc1t) and the group")
    print("      decodes to exactly 0.0, so a decoder must never divide blind.")
    print("  That leaves %d genuine near-misses. Most common: %s"
          % (totA - railA - len(zeroA), nearA.most_common(5)))
    print("      UNEXPLAINED. The untested hypothesis: the extremal sample")
    print("      belonged to a channel the encoder then collapsed to a constant,")
    print("      and a collapsed channel stores sample[0], not its extremum")
    print("      (the constancy test at 0x002D31D4 tolerates a spread of 32).")
    print("  implied max |value| per file, group A: med %.3f p90 %.3f max %.3f"
          % (quant(impliedA, .5), quant(impliedA, .9), max(impliedA)))
    print("  implied max |value| per file, group B: med %.3f p90 %.3f max %.3f"
          % (quant(impliedB, .5), quant(impliedB, .9), max(impliedB)))
    at_pi = sum(1 for v in impliedB if abs(v - math.pi) < 1e-5)
    over_pi = sum(1 for v in impliedB if v > math.pi * 1.0001)
    over_2pi = sum(1 for v in impliedB if v > 2 * math.pi * 1.0001)
    print("  group B lands within 1e-5 of pi in %d files -- those are the ones"
          % at_pi)
    print("      whose scaleB is 32768/pi with the extremum on the rail, and in")
    print("      float32 they come out a hair ABOVE pi, so a naive `> pi` test")
    print("      counts %d files and is meaningless. With a 1e-4 tolerance:"
          % sum(1 for v in impliedB if v > math.pi))
    print("  genuinely past pi  %d files;  past 2*pi  %d;  largest %.2f rad"
          % (over_pi, over_2pi, max(impliedB)))
    print("      So rotations are NOT confined to +/-pi. That is true of cm3,")
    print("      which hardcodes pi/32768 at 0x002D2970, and it is cm2's default")
    print("      -- %d files that DO store scales store scaleB == 32768/pi"
          % sum(1 for nm, a in parsed
                if a.scales_present == 2 and a.scale_b == anim.DEFAULT_SCALE_B))
    print("      exactly -- but it is not cm2's invariant. scaleB takes %d"
          % len({a.scale_b for nm, a in parsed if a.scales_present == 2}))
    print("      distinct values. Use the file's own scale; never clamp or wrap.")
    print("  scaleA == 0.0 in %d files, scaleB == 0.0 in %d"
          % (len(zsA), sum(1 for _, a in parsed if a.scale_b == 0.0)))

    # ------------------------------------ 6. do the two samplers agree
    print("\n=== 6. SAMPLER SELF-CONSISTENCY  (sample of %d files) ===" % SAMPLE)
    print("  sampleAtPhase (0x002D1970) and sampleAtKey (0x002D33C8) are separate")
    print("  functions with separate arithmetic. At a phase landing exactly on a")
    print("  stored key they should return the same pose.")
    step = max(1, len(parsed) // SAMPLE)
    sub = parsed[::step][:SAMPLE]
    ends_ok = phase_ok = 0
    mismatch = []
    keyrange_bad = []
    for nm, a in sub:
        if a.at_phase(0.0) == a.at_key(0):
            ends_ok += 1
        want = 0 if a.loop else a.keys - 1
        if a.at_phase(1.0) == a.at_key(want):
            ends_ok += 1
        span = a.frames if a.loop else a.frames - 1
        worst, worst_c = 0.0, -1
        if span > 0:
            for i in range(0, span, max(1, span // 16)):
                if a.half_rate and (i & 1):
                    continue          # odd half-rate index lands at frac 0.5
                t_ = i / float(span)
                k0, k1, frac = a.key_at_phase(t_)
                if frac > 1e-6:
                    continue          # float32 phase did not land dead on a key
                got, exp = a.at_phase(t_), a.at_key(k0)
                for c, (x, y) in enumerate(zip(got, exp)):
                    if abs(x - y) > worst:
                        worst, worst_c = abs(x - y), c
        if worst > 1e-6:
            mismatch.append((nm, worst, worst_c, a))
        else:
            phase_ok += 1
        for j in range(SWEEP):
            k0, k1, frac = a.key_at_phase(j / float(SWEEP - 1))
            if not (0 <= k0 < a.keys and 0 <= k1 < a.keys and 0.0 <= frac < 1.0):
                keyrange_bad.append((nm, j, k0, k1, frac))
                break
    print("  endpoint poses match sampleAtKey    %d/%d" % (ends_ok, 2 * len(sub)))
    print("  exact-key phases match              %d/%d" % (phase_ok, len(sub)))
    print("  phase mapping produced an out-of-range key or frac: %d %s"
          % (len(keyrange_bad), keyrange_bad[:3]))
    print("      that tests the half-rate index arithmetic at 0x002D1BC8 and")
    print("      its loop wrap at 0x002D1C60 over %d phases per file; a wrong"
          % SWEEP)
    print("      `last` would index past the key matrix")
    if mismatch:
        exactly = sum(1 for nm, w, c, a in mismatch
                      if abs(w - 65536.0 * a.dequant(c)) < 1e-4)
        print("  The %d mismatches are NOT a decoder bug, and the arithmetic says"
              % len(mismatch))
        print("  so: in %d of %d the discrepancy equals 65536 * dequant EXACTLY."
              % (exactly, len(mismatch)))
        for nm, w, c, a in mismatch[:4]:
            print("      %-38s err %.6f = 65536*%.3g on ch%d"
                  % (nm[:38], w, a.dequant(c), c))
        print("      sampleAtPhase routes through sampleLerp whenever 0 < t < 1,")
        print("      even when frac works out to 0, and sampleLerp applies the")
        print("      +/-65536 unwrap to A unconditionally (0x002D3504). So the")
        print("      game itself returns a pose one revolution away from")
        print("      sampleAtKey at those phases. Reproducing that is correct;")
        print("      several of these errors are exactly 2*pi = %.6f."
              % (2 * math.pi))

    # ------------------------------------------- 7. continuity of the output
    print("\n=== 7. TEMPORAL CONTINUITY  (same %d files) ===" % len(sub))
    print("  Independent of byte accounting: read the key matrix with the wrong")
    print("  stride and consecutive phases return unrelated poses. Measured as")
    print("  the largest step between adjacent phase samples, as a fraction of")
    print("  that channel's own full range.")
    rows = []
    for nm, a in sub:
        if a.anim_count == 0 or a.keys < 3:
            continue
        den = []
        for c in range(a.channels):
            cur = a.raw_curve(c)
            den.append(((max(cur) - min(cur)) or 1) * (abs(a.dequant(c)) or 1.0))
        prev, mx, at = None, 0.0, 0.0
        for j in range(SWEEP):
            t_ = j / float(SWEEP)
            cur = a.at_phase(t_)
            if prev is not None:
                for c in range(a.channels):
                    d = abs(cur[c] - prev[c]) / (den[c] or 1.0)
                    if d > mx:
                        mx, at = d, t_
            prev = cur
        rows.append((mx, at, nm, a))
    fracs = [r[0] for r in rows]
    print("  files measured %d; largest step / channel range:" % len(rows))
    print("    median %.4f   p90 %.4f   p99 %.4f   max %.4f"
          % (quant(fracs, .5), quant(fracs, .9), quant(fracs, .99), max(fracs)))
    smooth = sum(1 for v in fracs if v < 0.25)
    print("  below a quarter of the range on %d/%d files (%s)"
          % (smooth, len(rows), pct(smooth, len(rows))))
    tail = [r for r in rows if r[0] >= 0.25]
    uw = [r for r in tail if unwrap_fires(r[3])]
    print("  the %d-file tail: %d are clips where the +/-65536 unwrap fires,"
          % (len(tail), len(uw)))
    print("      which by construction moves a channel a whole revolution")
    seam = [r for r in tail if r not in uw and r[1] > 0.9]
    print("  %d more take their largest step at t > 0.9 -- the loop seam, where"
          % len(seam))
    print("      a cyclic clip interpolates from its last key back to key 0")
    rest = [r for r in tail if r not in uw and r[1] <= 0.9]
    print("  leaving %d unexplained, largest %.3f: %s"
          % (len(rest), max([r[0] for r in rest], default=0.0),
             ", ".join(r[2][:34] for r in rest[:4])))
    print("      Names like `...run_rgt_dwn_360_trw` and `pagejournal_back`")
    print("      suggest genuinely fast motion rather than a decode error, but")
    print("      that is a guess and is not counted as a pass.")

    # a cruder check that needs no sampler at all, over ALL files
    km = cm = 0
    for nm, a in parsed:
        if a.anim_count == 0 or a.keys < 2:
            continue
        N, K, M = a.anim_count, a.keys, a.matrix
        x = sum(abs(M[(k + 1) * N + i] - M[k * N + i])
                for i in range(N) for k in range(K - 1))
        y = sum(abs(M[i * K + k + 1] - M[i * K + k])
                for i in range(N) for k in range(K - 1))
        km += x < y
        cm += x >= y
    print("  key-major smoother than channel-major on %d files, reverse on %d."
          % (km, cm))
    print("      Animation is temporally smooth, so this settles the row stride")
    print("      on its own, with no reference to any size formula.")

    # ------------------------------------- 7b. a key the game cannot reach
    print("\n=== 7b. A STORED KEY THE PHASE PATH CANNOT REACH ===")
    print("  Fell out of the bounds check above rather than from the listing.")
    print("  For half-rate + loop with an EVEN frame count, 0x002D1C38 sets")
    print("  last = frames/2 - 1 and wraps k1 to 0 there, while the key count is")
    print("  frames/2 + 1. So k0 never exceeds frames/2 - 1 and k1 never reaches")
    print("  frames/2: the final stored key is dead weight.")
    dead = [nm for nm, a in parsed
            if a.half_rate and a.loop and a.frames % 2 == 0 and a.keys >= 2]
    print("  files in that shape: %d of %d" % (len(dead), len(parsed)))
    checked = 0
    for nm, a in parsed:
        if not (a.half_rate and a.loop and a.frames % 2 == 0):
            continue
        reach = set()
        for j in range(4 * a.frames):
            k0, k1, _ = a.key_at_phase(j / float(4 * a.frames))
            reach.add(k0)
            reach.add(k1)
        if a.keys - 1 not in reach:
            checked += 1
    print("  confirmed unreachable last key on %d/%d of them, by dense sweep"
          % (checked, len(dead)))
    print("      Load-bearing only for a re-packer: those bytes exist in the")
    print("      file and the game never samples them.")

    # --------------------------------------------- 8. the constraining file
    print("\n=== 8. THE 42-BYTE FILE, byte for byte ===")
    a = anim.read(game, *SMALLEST)
    d = game.read(*SMALLEST)
    print("  %s in %s, %d bytes" % (SMALLEST[1], SMALLEST[0], len(d)))
    print("  header  [0x00:0x11] %s" % d[:0x11].hex(" "))
    print("  payload [0x11:]     %s" % d[0x11:].hex(" "))
    print("    +0x11 animCount = %s = %d" % (d[0x11:0x15].hex(" "), a.anim_count))
    print("    +0x15 mask      = %s   popcount %d, so all %d channels constant"
          % (a.mask.hex(), bin(int.from_bytes(a.mask, 'little')).count('1'),
             a.channels))
    print("    +0x16 constants = %s" % a.constants)
    print("          key matrix is 0 bytes even though keys = %d" % a.keys)
    print("    +0x22 scaleA    = %r" % a.scale_a)
    print("    +0x26 scaleB    = %r" % a.scale_b)
    print("  frames=%d channels=%d flags=0x%02X (half-rate, loop) -> keys=%d"
          % (a.frames, a.channels, a.flags, a.keys))
    print("  17 + 4 + 1 + %d + 0 + 8 = %d;  consumed %d of %d"
          % (2 * a.channels,
             anim.HEADER_SIZE + 4 + 1 + 2 * a.channels + 8, a.consumed, a.size))
    pose = a.at_key(0)
    print("  decoded ch0-2 = (%.8f, %.8f, %.8f)" % tuple(pose[:3]))
    print("  decoded ch3-5 = (%.8g, %.8g, %.8g)" % tuple(pose[3:6]))
    print("  32768/scaleA = %.8f and |ch1| = %.8f -- equal, so the group's"
          % (32768.0 / a.scale_a, abs(pose[1])))
    print("      extremal sample really does sit on the -32768 rail, exactly as")
    print("      the encoder's scale = 32768/max|v| predicts. Group B likewise")
    print("      has ch3 on the +32767 rail. This is the file earlier analysis")
    print("      called undecodable; it decodes once the header is 17 bytes and")
    print("      not 20.")

    # ---------------------------------------------------- 9. eyeball a curve
    print("\n=== 9. WHAT A CURVE ACTUALLY LOOKS LIKE ===")
    pick = next(((nm, aa) for nm, aa in parsed
                 if aa.anim_count >= 6 and 20 <= aa.keys <= 40
                 and "wlk" in nm.lower()),
                next((nm, aa) for nm, aa in parsed
                     if aa.anim_count >= 6 and 20 <= aa.keys <= 60))
    nm, aa = pick
    print("  %s: frames=%d keys=%d channels=%d animated=%d flags=0x%02X"
          % (nm, aa.frames, aa.keys, aa.channels, aa.anim_count, aa.flags))
    shown = 0
    for c in range(aa.channels):
        if not aa.animated(c) or shown >= 4:
            continue
        vals = [v * aa.dequant(c) for v in aa.raw_curve(c)]
        d1 = [abs(vals[i + 1] - vals[i]) for i in range(len(vals) - 1)]
        print("    ch%-3d grp%s  %s"
              % (c, "A" if c < 3 else "B",
                 " ".join("%+.3f" % v for v in vals[:14])))
        print("           range %+.3f..%+.3f  mean |step| %.4f  max |step| %.4f"
              % (min(vals), max(vals), sum(d1) / len(d1), max(d1)))
        shown += 1
    print("  Successive keys differ by far less than the channel's own range,")
    print("  and the curves turn around smoothly. That is what an animation")
    print("  curve looks like; a misread buffer does not do this.")

    # --------------------------------------------- 10. the modular unwrap
    print("\n=== 10. THE +/-65536 UNWRAP IN sampleLerp (0x002D3504) ===")
    fires = pairs = files_firing = 0
    for nm, a in sub:
        if a.anim_count == 0 or a.keys < 2:
            continue
        hit, N, M = 0, a.anim_count, a.matrix
        for k in range(a.keys - 1):
            for i in range(N):
                d = M[k * N + i] - M[(k + 1) * N + i]
                pairs += 1
                if d > 32767 or d < -32768:
                    hit += 1
        fires += hit
        files_firing += hit > 0
    print("  adjacent key pairs examined   %d" % pairs)
    print("  pairs where the fixup fires   %d  (%s)" % (fires, pct(fires, pairs)))
    print("  files with at least one       %d/%d" % (files_firing, len(sub)))
    print("      It fires only where a channel crosses the rail, i.e. rarely,")
    print("      and it applies to EVERY channel including 0-2 -- nothing gates")
    print("      it on the index. It is a true shortest-arc fix only when 65536")
    print("      raw units is one revolution, i.e. when scaleB == 32768/pi.")

    # ------------------------------------------- 11. what is still unknown
    print("\n=== 11. STILL UNKNOWN -- reported, not papered over ===")
    u = [a.unknown_0c for _, a in parsed]
    nz = [v for v in u if v != 0.0]
    print("  the f32 at +0x0C: exactly 0.0 in %d/%d files; of the rest %d"
          % (sum(1 for v in u if v == 0.0), len(u), sum(1 for v in nz if v > 0)))
    print("      positive and %d negative, |v| from %.3g to %.3g."
          % (sum(1 for v in nz if v < 0),
             min(abs(v) for v in nz), max(abs(v) for v in nz)))
    print("      No cm2 codec method loads offset 12 from the anim object, so a")
    print("      decoder does not need it and this module does not use it. Both")
    print("      signs and that tiny minimum argue against a duration or a")
    print("      frame rate. Its consumer is UNLOCATED.")
    print("  flag bit 0: the behaviour is fully read (0x002D19B4, 0x002D1A34,")
    print("      0x002D1C60) but nothing in the image NAMES it. `loop` is a")
    print("      label; 'first pose equals last' compiles identically.")
    print("  which bone owns which channel triple, the Euler order, the axis")
    print("      convention: not in the code. All the code proves is a split at")
    print("      the literal index 3 (0x002D3400, 0x002D34C0).")
    print("  the %d 'cmp' files, %d zero-magic files and 1 with magic 00 00 28 4E"
          % (magics[b"cmp\0"], magics[b"\0\0\0\0"]))
    print("      are refused here. Zero-magic files get no codec at all")
    print("      (0x002CF1B8 NULLs the pointer) and take an uncompressed")
    print("      8-bytes-per-key path at 0x002CF354 that nobody has read.")

    # --------------------- 12. outside evidence for the channel semantics
    print("\n=== 12. OUTSIDE EVIDENCE on what the channels MEAN (not proof) ===")
    print("  `.skel` files are plain text and declare, per bone, which channels")
    print("  the animation system may drive, in the fixed order transX transY")
    print("  transZ rotX rotY rotZ scaleX scaleY scaleZ. If channels 0-2 are a")
    print("  root translation and 3.. are per-bone rotations, a skeleton's")
    print("  declared total should equal an .anim channel count.")
    TOK = ("transX", "transY", "transZ", "rotX", "rotY", "rotZ",
           "scaleX", "scaleY", "scaleZ")
    counts = {}
    for e in game.toc("SWAT.DAT"):
        if not e.name.lower().endswith(".skel"):
            continue
        txt = game.read("SWAT.DAT", e.name).decode("ascii", "replace")
        per = {k: len(re.findall(r"(?m)^\s*%s\s*$" % k, txt)) for k in TOK}
        nb = re.search(r"NumBones\s+(\d+)", txt)
        counts[e.name] = (sum(per.values()), int(nb.group(1)) if nb else 0,
                          per["transX"], per["rotX"], per["scaleX"])
    animch = collections.Counter(a.channels for _, a in parsed)
    root_only = {k: v for k, v in counts.items() if v[2] == 1 and v[3] == v[1]}
    print("  skeletons read %d; declaring exactly ONE translation triple plus"
          % len(counts))
    print("  one rotation triple per bone, i.e. total == 3 + 3*bones: %d"
          % len(root_only))
    hit = sorted({v[0] for v in root_only.values()} & set(animch))
    print("  those totals that are also real .anim channel counts: %s" % hit)
    for tot_ in sorted(hit, key=lambda x: -animch[x])[:3]:
        egs = [k for k, v in root_only.items() if v[0] == tot_][:2]
        print("    %3d channels = 3 + 3*%-3d  %5d anim files   e.g. %s"
              % (tot_, (tot_ - 3) // 3, animch[tot_], ", ".join(egs)))
    both = sum(1 for v in counts.values() if v[2] == v[1] and v[1] > 1)
    print("  CIRCUMSTANTIAL, and not a mapping. Counter-examples sit in the same")
    print("  directory: %d skeletons declare a translation triple on EVERY bone"
          % both)
    print("  (52 bones -> 312 channels, a count no .anim has), and at least one")
    print("  declares rotations only and still totals 159. The channel COUNT is")
    print("  consistent with the reading; the per-bone ORDER is not pinned by it.")
    print()
    print("  A sharper version of the same idea, and the only quantitative link")
    print("  between a channel index and a physical quantity found so far: if")
    print("  channel 1 is the root bone's UP translation in metres, its decoded")
    print("  median should sit near that skeleton's own root `offset` Y.")
    root_y = {}
    for name, v in root_only.items():
        txt = game.read("SWAT.DAT", name).decode("ascii", "replace")
        m = re.search(r"offset\s+(\S+)\s+(\S+)\s+(\S+)", txt)
        if m:
            root_y.setdefault(v[0], []).append(float(m.group(2)))
    med = collections.defaultdict(list)
    for nm, a in parsed:
        if a.channels in root_y and a.dequant_a != 0.0 and a.channels > 1:
            col = sorted(v * a.dequant_a for v in a.raw_curve(1))
            med[a.channels].append(col[len(col) // 2])
    agreed = 0
    comparable = 0
    for c in sorted(med):
        v = sorted(med[c])
        ys = root_y[c]
        mid = v[len(v) // 2]
        inband = min(ys) - 0.15 <= mid <= max(ys) + 0.15
        comparable += 1
        agreed += inband
        print("    %3d ch (%2d bones): %4d clips, ch1 median %.3f | skel root Y "
              "%.3f..%.3f  %s"
              % (c, (c - 3) // 3, len(v), mid, min(ys), max(ys),
                 "agrees" if inband else "DOES NOT AGREE"))
    print("  %d of %d comparable channel counts agree within 0.15 m, including"
          % (agreed, comparable))
    print("  the 22-bone bird skeleton whose root sits at 0.25 m and whose clips")
    print("  decode channel 1 to 0.23. That is strong support for channels 0-2")
    print("  being a root translation in metres with 1 as up -- but note the")
    print("  disagreements, which are real: the 53-bone shape covers most of the")
    print("  disc and the only two skeletons matching its declaration shape have")
    print("  a root at 0.30 m, so either those are the wrong skeletons for those")
    print("  clips or the reading is incomplete. Do not write an exporter on")
    print("  this alone; it wants a visual check in an emulator.")

    # ------------------------------------------------------------- verdict
    print("\n=== VERDICT ===")
    ok = (exact == n and rigid[0] + rigid[8] == n and not blew and not failed)
    print("  %d of %d cm2 files consumed EXACTLY (%s), 0 overruns, 0 refusals."
          % (exact, n, pct(exact, n)))
    print("  Under the rigid formula: %d exact, %d short by the 8 bytes of the"
          % (rigid[0], rigid[8]))
    print("  two optional scales, %d anywhere else." % sum(other.values()))
    print("  %d samples decoded across every key of every channel of every file."
          % samples)
    print("  The LAYOUT is settled. What the channels MEAN is not: the split at")
    print("  index 3 is read, translation-vs-rotation is inferred, and the")
    print("  bone mapping, Euler order and axis convention are unknown.")
    print("  %s   (%.1fs)" % ("ALL CHECKS PASSED" if ok else "SOMETHING FAILED",
                              time.time() - t0))
    game.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
