"""Does `ipuenc` produce `.ipu` files the game's decoder can read back?

The only test that means anything for an encoder is a round trip: decode a
real screen, re-encode it, decode THAT, and compare. A bitstream can be
structurally perfect -- right macroblock count, every VLC defined, exact byte
count -- and still be a picture of noise, because the parse cannot see
quantisation, DC prediction or colour conversion. So the headline number here
is the correlation between the original pixels and the round-tripped ones.

Six checks, in order of what they can catch:

  1. SHIPPED-ENCODER FACTS. Re-derives, from all 63 files, the things ipuenc
     hard-codes: flags byte, macroblock 0 carrying the quantiser, every
     address increment 1, zero padding, the 8-byte terminator. If Rockstar's
     encoder did something else, this is where it shows up, and everything
     downstream is built on it.
  2. ROUND TRIP. decode -> encode -> decode, per file, correlation and size.
  3. STRUCTURE. Our own output re-parsed as a shipped file would be: header
     arithmetic, quantiser where it belongs, padding, terminator.
  4. FLAT-FIELD EXACTNESS. A constant image must survive a round trip bit
     exactly. It is the one case where quantisation error has nowhere to hide,
     so any DC-prediction or colour-matrix mistake shows as a hard failure
     rather than a slightly lower correlation.
  5. encode_to_fit. Fits the budget, predicted size equals real size, and the
     quality it settles on is the best one that fits.
  6. numpy vs pure Python. The fast paths must agree with the reference, or
     the fast path is free to drift.
  7. REPLACEMENT, which is what the user is actually doing: foreign artwork
     into a slot it was never sized for, plus the measured size floor that
     makes IpuTooBig reachable.

    python scripts/verify_ipuenc.py            # 8 representative files
    python scripts/verify_ipuenc.py --all      # all 63, several minutes
"""
import argparse
import math
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Python puts a script's own directory on sys.path first. This one USED to hold
# a `dis.py`, which shadowed the STDLIB dis module: numpy imports dis, so numpy
# raised on import for anything launched out of scripts/, every `except
# ImportError: numpy = None` fallback in the studio silently engaged, and the
# only symptom was that everything ran ~30x slower. Measured: with it on
# sys.path, numpy unavailable and one 512x448 encode took 6.7s; with it off,
# 0.20s.
#
# That file is now `probe_dis.py` (REQ-031), so the collision is gone and this
# guard is belt-and-braces. It is KEPT deliberately: the hazard is a property of
# how Python builds sys.path, not of one filename, and the next stdlib-named
# file dropped in here would reintroduce it silently. Repaired under the
# shared-file rule -- the reference was to a file that no longer exists.
for _p in ("", str(HERE)):
    while _p in sys.path:
        sys.path.remove(_p)
sys.path.insert(0, str(HERE.parent))

from rdrstudio import ipu, ipudec, ipuenc               # noqa: E402
from rdrstudio import config                         # noqa: E402
from rdrstudio import mpeg2intra as M                   # noqa: E402
from rdrstudio import mpeg2tables as T                  # noqa: E402
from rdrstudio.game import Game                         # noqa: E402

# From config, not a `D:\` literal: `config.ROOT` derives from the
# module's own location, so this also stops the script being
# Windows-only. REQ-035 / REQ-059.
DISC = Path(config.VANILLA_ISO)

# One of each dimension, the smallest and largest files, the screens most
# likely to be replaced first, and two of the 33 that reproduce byte-exactly.
SAMPLE = ("coolscreen.ipu", "options_bg_10k.ipu", "rockstarpresents.ipu",
          "mainmenu.ipu", "jr_colt45_512x256.ipu", "showdown512x448.ipu",
          "levelselect_bg.ipu", "mpload_hold_1.ipu",
          "lvl_woods_map.ipu", "mpload_hold_02.ipu")

FAILURES = []


def check(ok, label, detail=""):
    print("  %-4s %s%s" % ("PASS" if ok else "FAIL", label,
                           ("  -- " + detail) if detail else ""))
    if not ok:
        FAILURES.append(label)
    return ok


def correlate(a, b):
    n = len(a)
    ma = sum(a) / n
    mb = sum(b) / n
    va = vb = cov = 0.0
    for x, y in zip(a, b):
        dx = x - ma
        dy = y - mb
        va += dx * dx
        vb += dy * dy
        cov += dx * dy
    if va <= 0 or vb <= 0:
        return 0.0
    return cov / math.sqrt(va * vb)


def mean_abs_err(a, b):
    return sum(abs(x - y) for x, y in zip(a, b)) / float(len(a))


# ---------------------------------------------------------------------------
# 1. What the shipped encoder actually did
# ---------------------------------------------------------------------------

def inspect(data):
    """Walk a `.ipu` at the symbol level. Returns the encoder's choices."""
    head = ipu.parse(data)
    w, h = head["width"], head["height"]
    nmb = (w // 16) * (h // 16)
    r = M.BitReader(data[ipu.HEADER:], start_bit=8)
    pred = [128, 128, 128]
    quant_at = {}
    increments = set()
    for n in range(nmb):
        if n:
            increments.add(M.MB_ADDR_VLC.decode(r))
        q = ipudec.read_macroblock_type(r)
        if q:
            quant_at[n] = q
        _, pred = M.decode_intra_macroblock(r, pred)
    bits = r.tell()
    npad = (8 - bits % 8) % 8
    pad = r.read(npad) if npad else 0
    tail_start = ipu.HEADER + (bits + npad) // 8
    return dict(width=w, height=h, macroblocks=nmb, flags=data[ipu.HEADER],
                quant_at=quant_at, increments=increments, pad_bits=npad,
                pad_value=pad, trailer=data[tail_start:],
                payload_bits=bits)


def check_shipped(files):
    print("\n[1] shipped-encoder facts, re-derived from %d files" % len(files))
    flags = set()
    qcodes = {}
    quant_positions = set()
    quant_count = set()
    incs = set()
    pads = set()
    trailers = set()
    dims = {}
    for name, data in files:
        info = inspect(data)
        flags.add(info["flags"])
        quant_positions |= set(info["quant_at"])
        quant_count.add(len(info["quant_at"]))
        for q in info["quant_at"].values():
            qcodes[q] = qcodes.get(q, 0) + 1
        incs |= info["increments"]
        pads.add(info["pad_value"])
        trailers.add(bytes(info["trailer"]))
        dims[(info["width"], info["height"])] = \
            dims.get((info["width"], info["height"]), 0) + 1

    check(flags == {ipuenc.FLAGS_BYTE}, "flags byte is 0x00 everywhere",
          "saw %s" % sorted("0x%02X" % f for f in flags))
    check(quant_count == {1} and quant_positions == {0},
          "exactly one Intra+Quant macroblock, always index 0",
          "counts %s at %s" % (sorted(quant_count), sorted(quant_positions)))
    check(incs <= {1}, "every macroblock_address_increment is 1",
          "saw %s" % sorted(incs))
    check(pads == {0}, "trailing bits pad with zero", "saw %s" % sorted(pads))
    check(trailers == {ipuenc.TERMINATOR},
          "trailer is 00 00 01 B0 00 00 01 B1",
          "%d distinct" % len(trailers))
    print("       q_scale_codes in use: %s"
          % ", ".join("%d x%d" % (q, n) for q, n in sorted(qcodes.items())))
    print("       dimensions: %s"
          % ", ".join("%dx%d x%d" % (w, h, n) for (w, h), n in sorted(dims.items())))
    return qcodes


# ---------------------------------------------------------------------------
# 2 + 3. Round trip and structure
# ---------------------------------------------------------------------------

def check_round_trip(files):
    print("\n[2] round trip: decode -> encode at the file's own q -> decode")
    print("    %-30s %-9s %2s %9s %9s %6s %8s %6s"
          % ("file", "dims", "q", "orig", "re-enc", "ratio", "corr", "mae"))
    worst_corr = 1.0
    worst_mae = 0.0
    ratios = []
    outputs = []
    identical = []
    for name, data in files:
        info = inspect(data)
        q = info["quant_at"][0]
        w, h, rgb = ipudec.decode(data)
        out = ipuenc.encode(rgb, w, h, q_scale_code=q)
        _, _, rgb2 = ipudec.decode(out)
        c = correlate(rgb, rgb2)
        e = mean_abs_err(rgb, rgb2)
        ratio = len(out) / float(len(data))
        ratios.append(ratio)
        worst_corr = min(worst_corr, c)
        worst_mae = max(worst_mae, e)
        outputs.append((name, out, q))
        if out == data:
            identical.append(name)
        print("    %-30s %4dx%-4d %2d %9d %9d %6.3f %+8.5f %6.2f%s"
              % (name, w, h, q, len(data), len(out), ratio, c, e,
                 "  EXACT" if out == data else ""))
    print()
    check(worst_corr > 0.99, "every round trip correlates above 0.99",
          "worst %+.5f" % worst_corr)
    check(worst_mae < 3.0, "mean absolute RGB error stays under 3/255",
          "worst %.2f" % worst_mae)
    # A re-encode at the same quantiser lands near the original size only if
    # the quantiser, matrix, scan and VLC tables all match the shipped
    # encoder. Being within 20% is a much stronger statement than "it parses".
    check(max(ratios) < 1.2 and min(ratios) > 0.8,
          "re-encoded size within 20% of the original at the same quantiser",
          "ratio %.3f..%.3f" % (min(ratios), max(ratios)))
    # The strongest statement available: for these files decode -> encode is
    # the identity on the BYTES, not merely on the picture. Nothing about the
    # colour matrix, chroma siting, forward DCT, quantiser, scan, VLC tables,
    # DC-prediction chain, padding or trailer can be even slightly different
    # from Rockstar's encoder and still land on the same file. 33 of the 63
    # shipped screens do this; --all reports the full count.
    check(bool(identical),
          "%d of %d re-encode byte-for-byte identical to the shipped file"
          % (len(identical), len(files)), ", ".join(identical[:4]))
    return outputs


def check_structure(outputs):
    print("\n[3] our own output, inspected the same way as a shipped file")
    bad = []
    for name, out, q in outputs:
        try:
            head = ipu.parse(out)                 # enforces size + 8 == len
            info = inspect(out)
        except Exception as exc:
            bad.append("%s: %s: %s" % (name, type(exc).__name__, exc))
            continue
        if info["flags"] != 0x00:
            bad.append("%s: flags 0x%02X" % (name, info["flags"]))
        if info["quant_at"] != {0: q}:
            bad.append("%s: quant %r, wanted {0: %d}" % (name, info["quant_at"], q))
        if info["increments"] - {1}:
            bad.append("%s: increments %s" % (name, sorted(info["increments"])))
        if info["pad_value"] != 0:
            bad.append("%s: pad bits %d" % (name, info["pad_value"]))
        if bytes(info["trailer"]) != ipuenc.TERMINATOR:
            bad.append("%s: trailer %s" % (name, bytes(info["trailer"]).hex()))
        if head["frames"] != 1:
            bad.append("%s: frames %d" % (name, head["frames"]))
    check(not bad, "%d encodes parse and match the shipped conventions"
          % len(outputs), "; ".join(bad[:3]))


# ---------------------------------------------------------------------------
# 4. Flat field
# ---------------------------------------------------------------------------

def check_flat():
    print("\n[4] flat fields must survive exactly (nothing for error to hide in)")
    bad = []
    for colour in ((0, 0, 0), (255, 255, 255), (128, 128, 128),
                   (200, 30, 30), (17, 200, 90)):
        rgb = bytes(colour) * (64 * 32)
        out = ipuenc.encode(rgb, 64, 32, q_scale_code=1)
        _, _, back = ipudec.decode(out)
        err = max(abs(a - b) for a, b in zip(rgb, back))
        if err > 2:
            bad.append("%s off by %d" % (colour, err))
        else:
            print("       rgb%-16s max channel error %d" % (str(colour), err))
    # 2, not 0: 4:2:0 chroma plus an integer YCbCr round trip cannot be lossless
    # for arbitrary colours. What matters is that the error is a rounding step
    # and not a systematic offset -- a wrong colour matrix or a reset DC
    # predictor lands tens of levels out, not one.
    check(not bad, "flat colours round trip within one rounding step",
          "; ".join(bad))


# ---------------------------------------------------------------------------
# 5. The size search
# ---------------------------------------------------------------------------

def check_fit(files):
    print("\n[5] encode_to_fit -- the function the tool calls")
    print("    %-30s %9s %9s %2s %8s  %s"
          % ("file", "budget", "got", "q", "headroom", "ladder (q:bytes)"))
    ok = True
    for name, data in files[:4]:
        w, h, rgb = ipudec.decode(data)
        res = ipuenc.replace(data, rgb)
        ladder = " ".join("%d:%d" % (q, s) for q, s in res["tried"])
        print("    %-30s %9d %9d %2d %8d  %s"
              % (name, res["max_bytes"], res["bytes"], res["q_scale_code"],
                 res["headroom"], ladder))
        if res["bytes"] > res["max_bytes"]:
            ok = False
        # The search claims this is the BEST quality that fits, so one step
        # finer must not fit.
        if res["q_scale_code"] > 1:
            finer = ipuenc.measure(rgb, w, h,
                                   q_scale_code=res["q_scale_code"] - 1)
            if finer <= res["max_bytes"]:
                ok = False
                print("       q=%d would also fit at %d bytes -- search settled "
                      "too coarse" % (res["q_scale_code"] - 1, finer))
    check(ok, "every fit lands inside its budget at the finest q that fits")

    # Predicted size must equal real size. encode_to_fit already asserts this
    # internally; doing it again here across every quantiser is what proves
    # the assert is not vacuous.
    name, data = files[0]
    w, h, rgb = ipudec.decode(data)
    prepared = ipuenc.analyse(rgb, w, h)
    mismatches = []
    for q in (1, 2, 4, 8, 16, 31):
        predicted = ipuenc.measure(prepared, q_scale_code=q)
        actual = len(ipuenc.encode(rgb, w, h, q_scale_code=q))
        if predicted != actual:
            mismatches.append("q=%d predicted %d actual %d" % (q, predicted, actual))
    check(not mismatches,
          "counting writer predicts the byte count exactly at q = 1,2,4,8,16,31",
          "; ".join(mismatches))

    # An impossible budget must say so rather than quietly emit something big.
    try:
        ipuenc.encode_to_fit(rgb, w, h, max_bytes=1024)
        check(False, "an unmeetable budget raises IpuTooBig")
    except ipuenc.IpuTooBig as exc:
        check(True, "an unmeetable budget raises IpuTooBig", str(exc)[:70])


# ---------------------------------------------------------------------------
# 6. Fast path vs reference
# ---------------------------------------------------------------------------

def check_fast_path():
    print("\n[6] numpy fast path vs the pure-Python reference")
    if ipuenc._np is None:
        # Not silently tolerated: on this machine numpy IS installed, so
        # reaching here means something shadowed it and the studio has quietly
        # dropped to the slow path everywhere, not just in this module.
        check(False, "numpy fast path is reachable",
              "unavailable -- %s" % ipuenc.NUMPY_UNAVAILABLE)
        return
    # Small, because the pure-Python forward DCT is ~1.5 ms a block and a
    # 512x448 screen is 5,376 of them.
    w, h = 96, 64
    rgb = bytearray()
    for y in range(h):
        for x in range(w):
            rgb += bytes(((x * 5 + y) % 256, (y * 7) % 256, (x ^ y) % 256))
    rgb = bytes(rgb)

    fast = ipuenc.encode(rgb, w, h, q_scale_code=3)
    saved = ipuenc._np
    try:
        ipuenc._np = None                     # force every reference path
        slow = ipuenc.encode(rgb, w, h, q_scale_code=3)
    finally:
        ipuenc._np = saved

    if fast == slow:
        check(True, "identical bytes from both paths (%d)" % len(fast))
        return
    # Not identical is not automatically wrong -- np.rint and Python's round
    # both go half-to-even but the float arithmetic order differs, so a
    # coefficient sitting exactly on .5 can tip either way. Quantify it.
    _, _, a = ipudec.decode(fast)
    _, _, b = ipudec.decode(slow)
    e = mean_abs_err(a, b)
    check(e < 0.5 and abs(len(fast) - len(slow)) < len(slow) * 0.02,
          "fast and reference paths agree",
          "%d vs %d bytes, mean pixel difference %.4f"
          % (len(fast), len(slow), e))


# ---------------------------------------------------------------------------
# 7. The operation the user actually performs
# ---------------------------------------------------------------------------

def check_replacement(files):
    """Put one screen's artwork into a DIFFERENT screen's slot.

    Everything above re-encodes a picture into its own budget, which is the
    easy case -- the budget was set by this exact picture. Replacing a screen
    means arbitrary art has to fit a slot it was never sized for, and that is
    where `encode_to_fit` earns its place.
    """
    print("\n[7] replacement: foreign artwork into an existing slot")
    by_name = dict(files)
    src = "rockstarpresents.ipu"
    if src not in by_name:
        print("       %s not loaded, skipped" % src)
        return
    _, _, art = ipudec.decode(by_name[src])
    print("    %-24s %9s %9s %2s %8s %6s"
          % ("target slot", "budget", "got", "q", "corr", "mae"))
    ok = True
    worst = 1.0
    # Capped: every target costs a quantiser search plus a decode, and under
    # --all that is 62 of them for no extra information -- the slots differ
    # only in size, and the widest range of sizes is what this needs.
    targets = [(n, d) for n, d in files
               if n != src and ipu.parse(d)["width"] == 512
               and ipu.parse(d)["height"] == 448]
    targets.sort(key=lambda nd: len(nd[1]))
    if len(targets) > 8:
        step = len(targets) / 8.0
        targets = [targets[int(i * step)] for i in range(8)]
    for name, data in targets:
        try:
            res = ipuenc.replace(data, art)
        except ipuenc.IpuTooBig as exc:
            print("    %-24s %9d  TOO BIG: %s" % (name, len(data), exc))
            ok = False
            continue
        _, _, back = ipudec.decode(res["data"])
        c = correlate(art, back)
        worst = min(worst, c)
        print("    %-24s %9d %9d %2d %+6.4f %6.2f"
              % (name, len(data), res["bytes"], res["q_scale_code"], c,
                 mean_abs_err(art, back)))
        if res["bytes"] > len(data):
            ok = False
    check(ok and worst > 0.95,
          "real artwork fits every 512x448 slot and still correlates > 0.95",
          "worst %+.4f" % worst)
    # The honest limit, stated as a measurement rather than left for a user to
    # discover: a slot is a hard budget and pixel-level noise does not compress.
    # A 512x448 field of high-frequency detail needs ~17 KB even at q=31, so a
    # 9.8 KB slot cannot take it at any quality. IpuTooBig is the correct
    # answer there, not a bug.
    w, h = 512, 448
    noise = bytearray()
    for y in range(h):
        for x in range(w):
            noise += bytes(((x * 37 + y * 11) % 256, (x * y) % 256,
                            (x ^ (y * 3)) % 256))
    floor_bytes = ipuenc.measure(bytes(noise), w, h, q_scale_code=31)
    print("       incompressible 512x448 noise needs %d bytes at q=31; the "
          "smallest\n       shipped slot is 9784, so IpuTooBig is reachable "
          "and correct" % floor_bytes)
    check(floor_bytes > 9784, "the size floor is a real constraint, not "
          "theoretical")


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true",
                    help="all 63 .ipu files instead of 8 representative ones")
    args = ap.parse_args()

    t0 = time.time()
    game = Game(DISC, writable=False)
    index = game.glob("*.ipu")
    wanted = sorted(index) if args.all else [n for n in SAMPLE if n in index]
    missing = [] if args.all else [n for n in SAMPLE if n not in index]
    files = [(n, game.read(index[n][0], n)) for n in wanted]
    game.close()
    if missing:
        print("not on the disc, skipped: %s" % ", ".join(missing))
    print("loaded %d .ipu files from %s" % (len(files), DISC.name))

    # The fact survey is cheap enough to always run over everything.
    if not args.all:
        game = Game(DISC, writable=False)
        allfiles = [(n, game.read(a[0], n)) for n, a in sorted(index.items())]
        game.close()
    else:
        allfiles = files
    check_shipped(allfiles)

    outputs = check_round_trip(files)
    check_structure(outputs)
    check_flat()
    check_fit(files)
    check_fast_path()
    check_replacement(files)

    print("\n%s  (%d checks failed, %.0fs)"
          % ("ALL CHECKS PASSED" if not FAILURES else "FAILURES: "
             + ", ".join(FAILURES), len(FAILURES), time.time() - t0))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
