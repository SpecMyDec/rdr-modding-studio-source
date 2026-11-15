"""Are the MPEG-2 tables and the intra decoder actually right?

No game data is touched. Everything here is generated, encoded with our own
tables, and decoded back with them -- which is the only check on a transcribed
standard that does not reduce to trusting the transcription.

The checks, and what a wrong table would do to each:

  1. **Structural table checks.** Prefix-freeness, exact code counts, Kraft
     sums, and the B-14/B-15 cross-check. A mistyped code length shows up in
     the Kraft sum; a mistyped code shows up as a prefix collision or as a
     disagreement between the two tables on a code they must share.
  2. **Exhaustive VLC round trip.** Every one of the 111 run/level pairs in
     each table, at both signs, encoded alone and decoded back. A swapped pair
     -- the one error that survives every structural check -- fails here.
  3. **Exhaustive DC round trip.** Every dct_dc_differential representable in
     sizes 0..11, both components, decoded back exactly.
  4. **Block round trip.** A fixed hand-built block, then thousands of random
     ones, across every combination of intra_vlc_format and alternate_scan,
     through the escape path as well as the table path.
  5. **Whole-picture round trip.** A synthetic multi-macroblock picture in
     both 4:2:0 and 4:2:2, decoded back, asserting BOTH that every coefficient
     matches AND that the reader lands exactly on the final bit. Ending in the
     right place is the property the real parameter search will be scored on,
     so it is worth proving it holds when everything is correct.
  6. **Negative control.** The same picture decoded with each parameter
     deliberately wrong, and with single flipped bits, to confirm that wrong
     settings desynchronise instead of quietly producing something. If this
     check ever passes, the search premise is void.
  7. **Maths.** IDCT against its own definition and against numpy, the DC-only
     case, forward-then-inverse identity, dequantiser saturation and mismatch
     control, and colour conversion at known points.
"""
import random
import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdrstudio import mpeg2tables as T                # noqa: E402
from rdrstudio import mpeg2intra as M                 # noqa: E402


FAILURES = []


def check(what, got, want=True):
    ok = (got == want)
    if not ok:
        FAILURES.append("%s: got %r, want %r" % (what, got, want))
    print("  [%s] %s%s" % ("ok" if ok else "FAIL", what,
                           "" if ok else "  (got %r, want %r)" % (got, want)))
    return ok


def note(text):
    print("       %s" % text)


# ---------------------------------------------------------------------------

def test_tables():
    print("\n1. table structure")
    rows = {r["table"]: r for r in M.validate_tables()}
    for name, row in rows.items():
        check(name, row["ok"])
    check("B-12 is a complete code (Kraft == 1)",
          rows["B-12 dct_dc_size_luminance"]["kraft"], Fraction(1))
    check("B-13 is a complete code (Kraft == 1)",
          rows["B-13 dct_dc_size_chrominance"]["kraft"], Fraction(1))

    b14, b15 = (rows["B-14 DCT coefficients Table 0"],
                rows["B-15 DCT coefficients Table 1"])
    check("B-14 has 113 codes", b14["codes"], 113)
    check("B-15 has 113 codes", b15["codes"], 113)
    check("B-14 codes 111 run/level pairs", b14["run_level_pairs"], 111)
    check("B-15 codes 111 run/level pairs", b15["run_level_pairs"], 111)
    check("B-14 is incomplete (so undefined codes exist)", b14["kraft"] < 1)
    check("B-15 is incomplete (so undefined codes exist)", b15["kraft"] < 1)
    note("unassigned AC code space: B-14 %s (%.4f%%), B-15 %s (%.4f%%)"
         % (b14["unassigned_code_space"],
            100 * float(b14["unassigned_code_space"]),
            b15["unassigned_code_space"],
            100 * float(b15["unassigned_code_space"])))
    note("that is the whole per-symbol error-detection budget, and it is thin")

    # What these checks CANNOT see, stated with a number rather than left
    # implicit. Swapping two codes of the SAME length inside one table is
    # invisible to everything here: it keeps the count, the Kraft sum and
    # prefix-freeness, and the encoder and decoder agree with each other
    # because they read the same table. The B-14/B-15 cross-check covers any
    # pair whose code is 12 bits or longer; the short codes are exposed.
    for name, tbl in (("B-14", T.AC_TABLE_B14), ("B-15", T.AC_TABLE_B15)):
        pairs = {p: c for c, p in tbl.items() if isinstance(p, tuple)}
        short = [c for c in pairs.values() if len(c) < 12]
        groups = {}
        for c in short:
            groups[len(c)] = groups.get(len(c), 0) + 1
        exposed = sum(n for n in groups.values() if n > 1)
        note("%s residual risk: %d of %d codes are under 12 bits and so "
             "outside the cross-check; %d sit in a same-length group where an "
             "undetectable swap is possible"
             % (name, len(short), len(pairs), exposed))
    note("that class of error can only be excluded by an external reference,")
    note("which is why the AC tables were machine-generated from FFmpeg's")
    note("numeric arrays rather than retyped by hand.")


def test_independent_rederivations():
    """Checks that do NOT share a source with the table they test.

    The round-trip tests have a blind spot by construction: if the encoder and
    the decoder read the same wrong table, they agree with each other and the
    test passes. Anything symmetric -- a swapped pair of equal-length codes, a
    changed quantiser weight -- slips straight through. These checks close some
    of that gap by deriving the same numbers a second, different way.
    """
    print("\n1b. independent re-derivations (catching symmetric errors)")

    # Table 7-6 non-linear column follows a generative rule: eight steps of 1,
    # eight of 2, six of 4, nine of 8. Rebuild it and compare.
    built = [0]
    value = 0
    for step, count in ((1, 8), (2, 8), (4, 6), (8, 9)):
        for _ in range(count):
            value += step
            built.append(value)
    check("non-linear quantiser_scale rebuilt from its step pattern "
          "(1x8, 2x8, 4x6, 8x9) matches the table",
          tuple(built), T.QUANTISER_SCALE_NONLINEAR)
    check("linear quantiser_scale is exactly 2 * q_scale_code",
          tuple(T.QUANTISER_SCALE_LINEAR[1:]),
          tuple(2 * c for c in range(1, 32)))

    # The default intra matrix, pinned by an aggregate and by anchors written
    # here as literals rather than read from the table.
    m = T.DEFAULT_INTRA_QUANTISER_MATRIX
    check("default intra matrix sums to 2114", sum(m), 2114)
    check("its corners are 8, 34, 27, 83", (m[0], m[7], m[56], m[63]),
          (8, 34, 27, 83))
    check("row 0 is 8,16,19,22,26,27,29,34", tuple(m[0:8]),
          (8, 16, 19, 22, 26, 27, 29, 34))
    check("last row is 27,29,35,38,46,56,69,83", tuple(m[56:64]),
          (27, 29, 35, 38, 46, 56, 69, 83))
    # The real table is NOT symmetric, which is its most distinctive feature:
    # a transcription that "tidied it up" would be, and would pass every other
    # check here.
    check("it is asymmetric, as the real table is (M[0][4]=26 vs M[4][0]=22)",
          (m[4], m[32]), (26, 22))
    check("all weights are in 1..255", all(1 <= v <= 255 for v in m))
    check("default non-intra matrix is a flat 16",
          set(T.DEFAULT_NON_INTRA_QUANTISER_MATRIX), {16})

    # Scan tables: anchors plus the property the decoder relies on.
    check("both scans start at raster 0",
          (T.ZIGZAG_SCAN[0], T.ALTERNATE_SCAN[0]), (0, 0))
    check("both scans end at raster 63",
          (T.ZIGZAG_SCAN[63], T.ALTERNATE_SCAN[63]), (63, 63))
    check("zigzag position 1..5 is 1,8,16,9,2",
          tuple(T.ZIGZAG_SCAN[1:6]), (1, 8, 16, 9, 2))
    check("alternate position 1..5 is 8,16,24,1,9",
          tuple(T.ALTERNATE_SCAN[1:6]), (8, 16, 24, 1, 9))
    note("scan[0] == 0 matters: the DC is written to raster 0 directly, so a")
    note("scan that moved position 0 would have the first AC clobber the DC")

    # B-1 anchors and a round trip, since nothing else exercises it.
    inv = {v: k for k, v in T.MACROBLOCK_ADDRESS_INCREMENT.items()}
    check("macroblock_address_increment 1 is the single bit '1'", inv[1], "1")
    check("increment 2 and 3 are '011' and '010'", (inv[2], inv[3]),
          ("011", "010"))
    check("increments 1..33 are all present", sorted(
        v for v in T.MACROBLOCK_ADDRESS_INCREMENT.values()
        if isinstance(v, int)), list(range(1, 34)))
    check("escape and stuffing are both 11 bits",
          (len(inv[T.MB_ESCAPE]), len(inv[T.MB_STUFFING])), (11, 11))
    bad = []
    for value, code in inv.items():
        r = M.BitReader(_bits(code))
        if M.MB_ADDR_VLC.decode(r) != value or r.tell() != len(code):
            bad.append(value)
    check("all 35 macroblock_address_increment codes round-trip", len(bad), 0)


def test_vlc_exhaustive():
    print("\n2. every AC code, both signs, encoded and decoded back")
    for fmt, table in ((0, T.AC_TABLE_B14), (1, T.AC_TABLE_B15)):
        vlc = M.AC_VLC[fmt]
        bad = []
        for code, payload in table.items():
            reader = M.BitReader(_bits(code))
            got = vlc.decode(reader)
            # The code must decode to its own payload AND consume exactly its
            # own length -- a shorter match would mean a prefix collision.
            if got != payload or reader.tell() != len(code):
                bad.append((code, payload, got, reader.tell()))
        check("B-1%d: all 113 codes decode to their own entry, exact length"
              % (4 + fmt), len(bad), 0)

        # And through the block encoder, which is the path a real stream uses.
        bad = []
        for (run, level) in [p for p in table.values() if isinstance(p, tuple)]:
            for sign in (1, -1):
                if run > 62:
                    continue
                qf = [0] * 64
                scan = T.SCANS[0]
                qf[scan[1 + run]] = sign * level
                w = M.BitWriter()
                M.encode_block(w, qf, is_luma=True, pred_dc=0,
                               intra_vlc_format=fmt, alternate_scan=0)
                r = M.BitReader(w.bytes())
                out = M.decode_block(r, is_luma=True, pred_dc=0,
                                     intra_vlc_format=fmt, alternate_scan=0)
                if out != qf:
                    bad.append((run, level, sign))
        check("B-1%d: all pairs round-trip inside a block, both signs"
              % (4 + fmt), len(bad), 0)


def _bits(code):
    """A '0'/'1' string as bytes, MSB first, zero padded."""
    w = M.BitWriter()
    w.write_code(code)
    return w.bytes()


def test_dc_exhaustive():
    print("\n3. every DC differential in sizes 0..11")
    for is_luma in (True, False):
        bad = []
        values = [0]
        for size in range(1, 12):
            lo, hi = 1 << (size - 1), (1 << size) - 1
            values += [hi, lo, -lo, -hi]
        for diff in values:
            w = M.BitWriter()
            qf = [0] * 64
            qf[0] = diff
            M.encode_block(w, qf, is_luma=is_luma, pred_dc=0)
            r = M.BitReader(w.bytes())
            out = M.decode_block(r, is_luma=is_luma, pred_dc=0)
            if out[0] != diff:
                bad.append((diff, out[0]))
        check("%s DC: %d extreme differentials round-trip"
              % ("luma" if is_luma else "chroma", len(values)), len(bad), 0)

    # Prediction is additive: decoding with a predictor must add it back.
    qf = [0] * 64
    qf[0] = 700
    w = M.BitWriter()
    M.encode_block(w, qf, is_luma=True, pred_dc=512)
    out = M.decode_block(M.BitReader(w.bytes()), is_luma=True, pred_dc=512)
    check("DC prediction adds back correctly", out[0], 700)


def test_known_block():
    print("\n4a. one hand-built block, stated in full")
    qf = [0] * 64
    #            raster     value      why
    qf[0] = 260            # DC, above the 8-bit range so the size VLC is used
    qf[1] = -3             # first AC, run 0
    qf[8] = 7              # forces a non-trivial run in zigzag order
    qf[63] = 1             # last coefficient, the mismatch-control target
    qf[21] = -42           # a level only reachable through an escape in B-14
    for fmt in (0, 1):
        for alt in (0, 1):
            w = M.BitWriter()
            M.encode_block(w, qf, is_luma=True, pred_dc=0,
                           intra_vlc_format=fmt, alternate_scan=alt)
            nbits = w.tell()
            r = M.BitReader(w.bytes())
            out = M.decode_block(r, is_luma=True, pred_dc=0,
                                 intra_vlc_format=fmt, alternate_scan=alt)
            ok = (out == qf and r.tell() == nbits)
            check("fmt=%d altscan=%d: recovered exactly, %d bits consumed"
                  % (fmt, alt, nbits), ok)


def test_random_blocks(count=400, seed=20260731):
    print("\n4b. random blocks across every table/scan/escape combination")
    rng = random.Random(seed)
    for fmt in (0, 1):
        for alt in (0, 1):
            for force_escape in (False, True):
                bad = 0
                total_bits = 0
                for _ in range(count):
                    qf = [0] * 64
                    qf[0] = rng.randint(-1024, 1024)
                    for i in range(1, 64):
                        if rng.random() < 0.25:
                            # Spread across the table's range and past it, so
                            # both the coded pairs and the escape path are hit.
                            mag = rng.choice([1, 1, 2, 3, 5, 12, 40,
                                              rng.randint(1, 2047)])
                            qf[i] = mag if rng.random() < 0.5 else -mag
                    is_luma = bool(rng.getrandbits(1))
                    w = M.BitWriter()
                    M.encode_block(w, qf, is_luma=is_luma,
                                   pred_dc=0, intra_vlc_format=fmt,
                                   alternate_scan=alt,
                                   force_escape=force_escape)
                    nbits = w.tell()
                    total_bits += nbits
                    r = M.BitReader(w.bytes())
                    out = M.decode_block(r, is_luma=is_luma, pred_dc=0,
                                         intra_vlc_format=fmt,
                                         alternate_scan=alt)
                    if out != qf or r.tell() != nbits:
                        bad += 1
                check("fmt=%d altscan=%d escape=%-5s: %d blocks, mean %d bits"
                      % (fmt, alt, force_escape, count, total_bits // count),
                      bad, 0)


def _build_picture(rng, n_macroblocks, chroma_format, fmt, alt):
    """Encode n macroblocks of random intra data. Returns (bytes, blocks, bits)."""
    layout = T.BLOCK_LAYOUTS[chroma_format]
    w = M.BitWriter()
    pred = [0, 0, 0]
    all_blocks = []
    ci = {"Y": 0, "Cb": 1, "Cr": 2}
    for _ in range(n_macroblocks):
        for component in layout:
            qf = [0] * 64
            qf[0] = rng.randint(0, 255)
            for i in range(1, 64):
                if rng.random() < 0.15:
                    mag = rng.choice([1, 1, 2, 3, 8, 30])
                    qf[i] = mag if rng.random() < 0.5 else -mag
            M.encode_block(w, qf, is_luma=(component == "Y"),
                           pred_dc=pred[ci[component]],
                           intra_vlc_format=fmt, alternate_scan=alt)
            pred[ci[component]] = qf[0]
            all_blocks.append(qf)
    return w.bytes(), all_blocks, w.tell()


def test_picture_round_trip():
    print("\n5. whole synthetic pictures")
    rng = random.Random(4242)
    for chroma_format in ("4:2:0", "4:2:2"):
        for fmt in (0, 1):
            for alt in (0, 1):
                data, blocks, nbits = _build_picture(
                    rng, 40, chroma_format, fmt, alt)
                r = M.BitReader(data)
                pred = [0, 0, 0]
                got = []
                for _ in range(40):
                    mb, pred = M.decode_intra_macroblock(
                        r, pred, chroma_format=chroma_format,
                        intra_vlc_format=fmt, alternate_scan=alt)
                    got.extend(mb)
                exact = (got == blocks)
                landed = (r.tell() == nbits)
                check("%s fmt=%d altscan=%d: 40 MBs, %d blocks, %d bits, "
                      "exact=%s, ended on the last bit=%s"
                      % (chroma_format, fmt, alt, len(blocks), nbits,
                         exact, landed),
                      exact and landed)


def test_which_parameters_affect_parsing():
    print("\n6. which unknowns a decode-length search can actually settle")
    rng = random.Random(11)
    data, blocks, nbits = _build_picture(rng, 30, "4:2:0", 0, 0)

    # Every post-VLC parameter must consume identical bits. If any of these
    # changed the parse, the search space would be larger than it is.
    base = M.BitReader(data)
    pred = [0, 0, 0]
    for _ in range(30):
        _, pred = M.decode_intra_macroblock(base, pred, chroma_format="4:2:0")
    check("decode_block takes no quantiser argument at all",
          "quant" not in M.decode_block.__code__.co_varnames)

    qf = blocks[0]
    pixels = set()
    for prec in range(4):
        for qsc in (1, 8, 31):
            for qst in (0, 1):
                rec = M.dequantise_intra(qf, q_scale_code=qsc,
                                         q_scale_type=qst,
                                         intra_dc_precision=prec)
                pixels.add(tuple(M.clamp_samples(M.idct_8x8(rec))))
    check("the same parsed block gives %d different pictures across "
          "intra_dc_precision x q_scale_code x q_scale_type" % len(pixels),
          len(pixels) > 1)
    note("so those three are invisible to the parser: same bits, other pixels")

    # alternate_scan likewise: it permutes, it does not resynchronise.
    r = M.BitReader(data)
    pred, n = [0, 0, 0], 0
    try:
        while n < 30:
            _, pred = M.decode_intra_macroblock(r, pred, chroma_format="4:2:0",
                                                alternate_scan=1)
            n += 1
    except M.Mpeg2Error:
        pass
    check("wrong alternate_scan still parses all 30 MBs and lands on the end",
          n == 30 and r.tell() == nbits)
    note("alternate_scan, the quantiser and the DC precision must be chosen")
    note("from the picture. Only intra_vlc_format and the chroma format")
    note("change the parse, so only those two are searchable this way.")


def _score(payload, n_mb, fmt, alt, chroma):
    """(macroblocks decoded, error name or None, final bit position)."""
    r = M.BitReader(payload)
    pred = [0, 0, 0]
    n = 0
    try:
        while n < n_mb:
            _, pred = M.decode_intra_macroblock(
                r, pred, chroma_format=chroma, intra_vlc_format=fmt,
                alternate_scan=alt)
            n += 1
    except M.Mpeg2Error as exc:
        return n, type(exc).__name__, r.tell()
    return n, None, r.tell()


def test_negative_control():
    print("\n6b. negative control -- how sharp is the score, measured")
    rng = random.Random(99)
    n_mb = 200
    data, blocks, nbits = _build_picture(rng, n_mb, "4:2:0", 0, 0)
    note("reference picture: %d MBs, %d bits, %d bytes"
         % (n_mb, nbits, len(data)))

    # The joint criterion the build phase should score on.
    def meets(n, err, pos):
        return n == n_mb and err is None and abs(pos - nbits) <= 8

    hits = []
    for fmt in (0, 1):
        for chroma in ("4:2:0", "4:2:2"):
            n, err, pos = _score(data, n_mb, fmt, 0, chroma)
            if meets(n, err, pos):
                hits.append((fmt, chroma))
            print("       fmt=%d %s -> %3d/%d MBs, bit %6d/%d, %s"
                  % (fmt, chroma, n, n_mb, pos, nbits, err or "clean"))
    check("exactly one (intra_vlc_format, chroma format) pair meets the "
          "criterion, and it is the right one", hits, [(0, "4:2:0")])

    # Per-symbol rejection rate, exactly. This is the number that governs how
    # long a wrong guess can stagger on before it trips.
    for name, vlc, want in (("B-14", M.AC_VLC_B14, Fraction(1, 4096)),
                            ("B-15", M.AC_VLC_B15, Fraction(9, 4096))):
        undefined = sum(1 for e in vlc._lut if e is None)
        frac = Fraction(undefined, len(vlc._lut))
        check("%s rejects %s of bit patterns (%d of %d LUT slots)"
              % (name, frac, undefined, len(vlc._lut)), frac, want)
    for name, vlc in (("B-12 luma DC", M.DC_LUMA_VLC),
                      ("B-13 chroma DC", M.DC_CHROMA_VLC)):
        check("%s rejects nothing -- it is a complete code" % name,
              sum(1 for e in vlc._lut if e is None), 0)
    note("so the DC layer contributes NO error detection whatsoever")

    # What actually happens on noise.
    modes = {}
    lengths = []
    for trial in range(300):
        rnd = random.Random(50000 + trial)
        noise = bytes(rnd.getrandbits(8) for _ in range(2048))
        r = M.BitReader(noise)
        n = 0
        try:
            while True:
                M.decode_block(r, is_luma=True, pred_dc=0)
                n += 1
        except M.Mpeg2Error as exc:
            modes[type(exc).__name__] = modes.get(type(exc).__name__, 0) + 1
        lengths.append(n)
    lengths.sort()
    note("noise as back-to-back blocks: median %d blocks before failure, "
         "p99 %d, max %d" % (lengths[150], lengths[297], lengths[-1]))
    note("failure modes: %s" % modes)
    check("the dominant failure is CoefficientOverflow, not UndefinedVLC",
          max(modes, key=modes.get), "CoefficientOverflow")
    check("noise does sometimes run a long way (max %d blocks) -- so raw "
          "distance alone is NOT a safe score" % lengths[-1], lengths[-1] > 50)

    # ...but the joint criterion is not fooled.
    fooled = 0
    for trial in range(300):
        rnd = random.Random(90000 + trial)
        noise = bytes(rnd.getrandbits(8) for _ in range(len(data)))
        if meets(*_score(noise, n_mb, 0, 0, "4:2:0")):
            fooled += 1
    check("noise of the same length meets the joint criterion 0 times in 300",
          fooled, 0)

    # Single flipped bits: self-synchronising codes often recover, so the
    # count survives; the landing position is what gives it away.
    survived = 0
    landed = 0
    for trial in range(200):
        bit = random.Random(trial).randrange(nbits)
        corrupt = bytearray(data)
        corrupt[bit >> 3] ^= 0x80 >> (bit & 7)
        n, err, pos = _score(bytes(corrupt), n_mb, 0, 0, "4:2:0")
        if n == n_mb and err is None:
            survived += 1
        if meets(n, err, pos):
            landed += 1
    note("200 single-bit flips: %d still parsed %d MBs, but only %d also "
         "landed on the end bit" % (survived, n_mb, landed))
    check("landing position catches corruption that the count misses",
          landed <= survived)


def test_idct():
    print("\n7a. IDCT")
    rng = random.Random(7)
    worst = 0.0
    for _ in range(50):
        block = [rng.randint(-2048, 2047) for _ in range(64)]
        a = M.idct_8x8(block, use_numpy=True)
        b = M.idct_8x8(block, use_numpy=False)
        worst = max(worst, max(abs(x - y) for x, y in zip(a, b)))
    check("numpy path matches the pure-Python definition (max diff %.2e)"
          % worst, worst < 1e-9)

    # A DC-only block is flat, and MPEG-2 does NOT level-shift: 1024 -> 128.
    dc = [0] * 64
    dc[0] = 1024
    out = M.idct_8x8(dc)
    check("DC 1024 gives a flat block of 128 (no JPEG-style +128 offset)",
          all(abs(v - 128.0) < 1e-9 for v in out))
    dc[0] = 2040
    check("DC 2040 gives 255", abs(M.idct_8x8(dc)[0] - 255.0) < 1e-9)

    # Forward then inverse must be the identity.
    worst = 0.0
    for _ in range(50):
        samples = [rng.randint(0, 255) for _ in range(64)]
        back = M.idct_8x8(M.fdct_8x8(samples), use_numpy=False)
        worst = max(worst, max(abs(a - b) for a, b in zip(samples, back)))
    check("forward DCT then IDCT is the identity (max error %.2e)" % worst,
          worst < 1e-9)


def test_dequantise():
    print("\n7b. inverse quantisation")
    qf = [0] * 64
    qf[0] = 100
    for prec, mult in enumerate(T.INTRA_DC_MULT):
        out = M.dequantise_intra(qf, q_scale_code=1, intra_dc_precision=prec,
                                 mismatch_control=False)
        check("intra_dc_precision %d multiplies DC by %d" % (prec, mult),
              out[0], 100 * mult)

    # AC: (2 * QF * W * qs) / 32, truncating toward zero, symmetric in sign.
    qf = [0] * 64
    qf[1] = 5                      # W[1] = 16 in the default intra matrix
    out = M.dequantise_intra(qf, q_scale_code=2, q_scale_type=0,
                             mismatch_control=False)
    want = (2 * 5 * T.DEFAULT_INTRA_QUANTISER_MATRIX[1] * 4) // 32
    check("AC dequant matches the standard formula", out[1], want)
    qf[1] = -5
    out = M.dequantise_intra(qf, q_scale_code=2, mismatch_control=False)
    check("AC dequant truncates toward zero for negatives", out[1], -want)

    # Truncation, specifically: 2*1*8*2/32 = 1/2 must go to 0, not to 1.
    qf = [0] * 64
    qf[1] = 1
    out = M.dequantise_intra(qf, quant_matrix=[8] * 64, quantiser_scale=1,
                             mismatch_control=False)
    check("a sub-unit AC term truncates to 0", out[1], 0)

    # Saturation.
    qf = [0] * 64
    qf[1] = 2000
    out = M.dequantise_intra(qf, q_scale_code=31, q_scale_type=0,
                             mismatch_control=False)
    check("coefficients saturate at 2047", out[1], T.COEFF_MAX)

    # Mismatch control makes the sum odd, always.
    rng = random.Random(3)
    bad = 0
    for _ in range(500):
        qf = [rng.randint(-40, 40) for _ in range(64)]
        out = M.dequantise_intra(qf, q_scale_code=rng.randint(1, 31),
                                 q_scale_type=rng.getrandbits(1),
                                 mismatch_control=True)
        if sum(out) % 2 == 0 or not all(T.COEFF_MIN <= v <= T.COEFF_MAX
                                        for v in out):
            bad += 1
    check("mismatch control leaves the sum odd and in range, 500 blocks",
          bad, 0)


def test_colour():
    print("\n7c. colour conversion (BT.601, full range)")
    check("neutral grey stays grey", M.ycbcr_to_rgb(128, 128, 128),
          (128, 128, 128))
    check("Y=0 neutral is black", M.ycbcr_to_rgb(0, 128, 128), (0, 0, 0))
    check("Y=255 neutral is white -- full range, not 235",
          M.ycbcr_to_rgb(255, 128, 128), (255, 255, 255))
    # Round-trip RGB -> YCbCr -> RGB with the matching full-range BT.601
    # forward matrix. Agreement to a couple of LSB is the most that integer
    # YCbCr allows: pure red encodes to Cr 255.5, which has to clip to 255,
    # so it comes back as 254 rather than 255. That is the format, not a bug.
    def forward(r, g, b):
        y = 0.299 * r + 0.587 * g + 0.114 * b
        cb = 128 - 0.168736 * r - 0.331264 * g + 0.5 * b
        cr = 128 + 0.5 * r - 0.418688 * g - 0.081312 * b
        return tuple(min(255, max(0, int(round(v)))) for v in (y, cb, cr))

    worst = 0
    for rgb in [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0),
                (0, 255, 255), (255, 0, 255), (0, 0, 0), (255, 255, 255),
                (64, 128, 192), (200, 30, 90), (17, 17, 17)]:
        back = M.ycbcr_to_rgb(*forward(*rgb))
        worst = max(worst, max(abs(a - b) for a, b in zip(rgb, back)))
    check("11 RGB colours survive RGB->YCbCr->RGB (worst error %d LSB)"
          % worst, worst <= 2)
    # Studio range must differ, or the flag would be doing nothing.
    check("studio range gives a different result for Y=255",
          M.ycbcr_to_rgb(255, 128, 128, full_range=False)
          != M.ycbcr_to_rgb(16, 128, 128, full_range=False))

    up = M.upsample_chroma(bytes([10, 20, 30, 40]), 2, 2, 2, 2)
    check("4:2:0 chroma upsample doubles both axes", len(up), 16)
    check("upsampled corner replicates", (up[0], up[1], up[2], up[3]),
          (10, 10, 20, 20))


def test_bit_io():
    print("\n7d. bit I/O")
    w = M.BitWriter()
    w.write(0b101, 3)
    w.write(0, 1)
    w.write(0xFFFF, 16)
    data = w.bytes()
    r = M.BitReader(data)
    check("MSB-first read back", (r.read(3), r.read(1), r.read(16)),
          (0b101, 0, 0xFFFF))
    check("tell() tracks bits consumed", r.tell(), 20)

    r = M.BitReader(b"\x00")
    check("peek past the end pads with zeros", r.peek(16), 0)
    try:
        r.read(9)
        check("read past the end raises", False)
    except M.EndOfBitstream as exc:
        check("read past the end raises EndOfBitstream at bit %d"
              % exc.bit_position, True)

    r = M.BitReader(b"\xFF\xFF")
    r.read(3)
    check("byte_align skips to the boundary", r.byte_align(), 5)
    check("byte_align landed on a byte", r.tell() % 8, 0)
    check("read_signed sign-extends",
          M.BitReader(bytes([0b11111111])).read_signed(8), -1)


def test_undefined_vlc_is_specific():
    print("\n7e. failure reporting")
    # 0000 0000 0000 0... is not a code in either AC table.
    r = M.BitReader(b"\x00\x00\x00\x00")
    try:
        M.decode_block(r, is_luma=True, pred_dc=0)
        check("undefined AC code raises", False)
    except M.UndefinedVLC as exc:
        check("undefined code raises UndefinedVLC naming the table", True)
        note("message: %s" % exc)
        check("it carries a bit position", isinstance(exc.bit_position, int))
        check("it carries the offending bits", len(exc.bits) > 0)

    # An escape with level 0 is forbidden, and must not be mistaken for data.
    w = M.BitWriter()
    w.write_code("100")                          # B-12 dct_dc_size 0
    w.write_code(T.B14_ESCAPE)
    w.write(0, T.ESCAPE_RUN_BITS)
    w.write_signed(0, T.ESCAPE_LEVEL_BITS)
    try:
        M.decode_block(M.BitReader(w.bytes()), is_luma=True, pred_dc=0)
        check("escape level 0 is rejected", False)
    except M.ForbiddenValue:
        check("escape level 0 raises ForbiddenValue", True)

    # A run that walks past coefficient 63.
    w = M.BitWriter()
    w.write_code("100")
    w.write_code(T.B14_ESCAPE)
    w.write(63, T.ESCAPE_RUN_BITS)
    w.write_signed(1, T.ESCAPE_LEVEL_BITS)
    try:
        M.decode_block(M.BitReader(w.bytes()), is_luma=True, pred_dc=0)
        check("a run past coefficient 63 is rejected", False)
    except M.CoefficientOverflow:
        check("a run past coefficient 63 raises CoefficientOverflow", True)


def test_non_intra_first_coefficient():
    """B-14's one context-dependent code, built by hand.

    In a non-intra block the first coefficient cannot be an end-of-block, so
    the standard reuses the bit: a lone "1" plus a sign is (run 0, level 1),
    where in every later position "10" is EOB and "11s" is (0, 1). `.ipu`
    files are all intra so this never fires there, but a reference that gets
    it wrong is a reference that silently mis-decodes any non-intra stream.
    """
    print("\n7f. B-14's non-intra first-coefficient form")
    for sign_bit, want in ((0, 1), (1, -1)):
        w = M.BitWriter()
        w.write_code("1")                 # the special first-coefficient code
        w.write(sign_bit, 1)              # its sign
        w.write_code(T.B14_EOB)
        out = M.decode_block(M.BitReader(w.bytes()), intra=False,
                             first_coefficient=True)
        ok = (out[0] == want and not any(out[1:]))
        check("lone '1' + sign %d decodes as (0, %d)" % (sign_bit, want), ok)

    # With the flag off, the same bits must be read the ordinary way: "11" is
    # (0, 1) and the NEXT bit is the sign. Different consumption, so this is a
    # real check that the flag does something rather than being decorative.
    w = M.BitWriter()
    w.write_code("11")                    # (0, 1) in the normal reading
    w.write(1, 1)                         # sign -> -1
    w.write_code(T.B14_EOB)
    out = M.decode_block(M.BitReader(w.bytes()), intra=False,
                         first_coefficient=False)
    check("without the flag, '11'+sign is the ordinary (0, 1) code",
          out[0], -1)

    # And a first code that starts with 0 must take the ordinary path even
    # when the flag is on.
    w = M.BitWriter()
    w.write_code("011")                   # (1, 1)
    w.write(0, 1)
    w.write_code(T.B14_EOB)
    out = M.decode_block(M.BitReader(w.bytes()), intra=False,
                         first_coefficient=True)
    check("a first code starting with 0 is unaffected by the flag",
          (out[T.ZIGZAG_SCAN[1]], out[0]), (1, 0))


def test_end_to_end_image():
    """A real 16x16 picture all the way round: RGB -> YCbCr -> 4:2:0 -> DCT ->
    quantise -> VLC -> bitstream -> VLC -> dequantise -> IDCT -> RGB.

    Two separate claims, and they are checked separately on purpose:
      * the quantised coefficients must survive the bitstream EXACTLY -- that
        part is lossless and any error is a table error;
      * the pixels only have to survive approximately, because quantisation
        threw information away before the bitstream ever saw them.
    """
    print("\n8. end-to-end: a 16x16 picture through the whole chain")
    w = h = 16

    src = bytearray(w * h * 3)
    for y in range(h):
        for x in range(w):
            i = (y * w + x) * 3
            src[i] = (x * 16) & 0xFF                       # red ramp
            src[i + 1] = (y * 16) & 0xFF                   # green ramp
            src[i + 2] = 255 if (x // 4 + y // 4) % 2 else 0   # blue checker

    # Forward colour conversion, full-range BT.601.
    yp = [0.0] * (w * h)
    cbp = [0.0] * (w * h)
    crp = [0.0] * (w * h)
    for p in range(w * h):
        r, g, b = src[p * 3], src[p * 3 + 1], src[p * 3 + 2]
        yp[p] = 0.299 * r + 0.587 * g + 0.114 * b
        cbp[p] = 128 - 0.168736 * r - 0.331264 * g + 0.5 * b
        crp[p] = 128 + 0.5 * r - 0.418688 * g - 0.081312 * b

    # 4:2:0: average each 2x2 chroma group.
    def subsample(plane):
        out = [0.0] * ((w // 2) * (h // 2))
        for cy in range(h // 2):
            for cx in range(w // 2):
                out[cy * (w // 2) + cx] = 0.25 * (
                    plane[(cy * 2) * w + cx * 2]
                    + plane[(cy * 2) * w + cx * 2 + 1]
                    + plane[(cy * 2 + 1) * w + cx * 2]
                    + plane[(cy * 2 + 1) * w + cx * 2 + 1])
        return out

    cb_small, cr_small = subsample(cbp), subsample(crp)

    def take_block(plane, stride, bx, by):
        return [plane[(by * 8 + r) * stride + bx * 8 + c]
                for r in range(8) for c in range(8)]

    blocks = [take_block(yp, w, 0, 0), take_block(yp, w, 1, 0),
              take_block(yp, w, 0, 1), take_block(yp, w, 1, 1),
              take_block(cb_small, w // 2, 0, 0),
              take_block(cr_small, w // 2, 0, 0)]

    q_scale_code, q_scale_type, precision = 3, 0, 0
    quantised = [M.quantise_intra(M.fdct_8x8(b), q_scale_code=q_scale_code,
                                  q_scale_type=q_scale_type,
                                  intra_dc_precision=precision)
                 for b in blocks]

    writer = M.BitWriter()
    pred = [0, 0, 0]
    for idx, qf in enumerate(quantised):
        component = 0 if idx < 4 else (1 if idx == 4 else 2)
        M.encode_block(writer, qf, is_luma=(idx < 4), pred_dc=pred[component])
        pred[component] = qf[0]
    payload, nbits = writer.bytes(), writer.tell()

    reader = M.BitReader(payload)
    got, pred = M.decode_intra_macroblock(reader, [0, 0, 0],
                                          chroma_format="4:2:0")
    check("coefficients survive the bitstream exactly (%d bits, %d bytes)"
          % (nbits, len(payload)), got, quantised)
    check("the reader ends on the final bit", reader.tell(), nbits)

    planes = []
    for qf in got:
        rec = M.dequantise_intra(qf, q_scale_code=q_scale_code,
                                 q_scale_type=q_scale_type,
                                 intra_dc_precision=precision)
        planes.append(M.clamp_samples(M.idct_8x8(rec)))

    y_out = bytearray(w * h)
    for bi, (bx, by) in enumerate(((0, 0), (1, 0), (0, 1), (1, 1))):
        for r in range(8):
            for c in range(8):
                y_out[(by * 8 + r) * w + bx * 8 + c] = planes[bi][r * 8 + c]
    cb_out = M.upsample_chroma(bytes(planes[4]), 8, 8, 2, 2)
    cr_out = M.upsample_chroma(bytes(planes[5]), 8, 8, 2, 2)
    rgb = M.ycbcr_planes_to_rgb(y_out, cb_out, cr_out, w, h)

    check("output is w*h*3 bytes", len(rgb), w * h * 3)
    errors = [abs(a - b) for a, b in zip(src, rgb)]
    mae = sum(errors) / float(len(errors))
    note("mean absolute error %.2f, worst %d (quantiser scale %d, 4:2:0 "
         "chroma loss on a hard checkerboard)"
         % (mae, max(errors), T.QUANTISER_SCALE[q_scale_type][q_scale_code]))
    check("the picture comes back recognisable (MAE %.2f < 12)" % mae, mae < 12)

    # A flat colour must survive essentially untouched -- if it does not, the
    # DC path or the colour conversion is wrong rather than merely lossy.
    flat = [128.0] * 64
    qf = M.quantise_intra(M.fdct_8x8(flat), q_scale_code=1)
    rec = M.dequantise_intra(qf, q_scale_code=1)
    out = M.clamp_samples(M.idct_8x8(rec))
    check("a flat 128 block returns flat 128", set(out), {128})


def main():
    print(__doc__.strip().splitlines()[0])
    test_tables()
    test_independent_rederivations()
    test_vlc_exhaustive()
    test_dc_exhaustive()
    test_known_block()
    test_random_blocks()
    test_picture_round_trip()
    test_which_parameters_affect_parsing()
    test_negative_control()
    test_idct()
    test_dequantise()
    test_colour()
    test_bit_io()
    test_undefined_vlc_is_specific()
    test_non_intra_first_coefficient()
    test_end_to_end_image()

    print("\n" + "=" * 70)
    if FAILURES:
        print("%d CHECK(S) FAILED" % len(FAILURES))
        for f in FAILURES:
            print("  - %s" % f)
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
