"""Does the .ipu decoder produce the picture the GAME actually shows?

Every other check on this decoder is internal: the bitstream parses, the
macroblock count is exact, the histogram looks plausible. All of those can be
satisfied by a decode that is structurally correct and visually wrong -- get
intra_dc_precision or the quantiser scale wrong and you still parse 63 of 63
files, because those parameters change reconstruction, not VLC code lengths.
The bit-consumption test is blind to exactly the errors that would ruin an
image.

So this compares against ground truth instead. `play_test.py` screenshots the
emulator, and the boot logo it captures at 10 seconds is the Rockstar Games
brand burned into wood -- which is what `rockstarpresents.ipu` should decode
to. If the decode matches that photograph of the running game, the decode is
right, and no amount of internal consistency can fake it.

    python scripts/verify_ipu.py
    python scripts/verify_ipu.py --ipu mainmenu.ipu --shot <path>.png

Caveats this script is honest about rather than hiding:

  * The screenshot includes the emulator's window chrome and letterboxing, so
    the game viewport has to be found rather than assumed. It is located by
    trimming uniform borders, and the trim is REPORTED so a bad crop is
    visible instead of silently scoring low.
  * The PS2 renders 512x448 into a 4:3 display, and PCSX2 scales it again, so
    the comparison is done on heavily resampled data. Expect a good match to
    score high but not near-perfect; a WRONG decode scores near zero, which is
    the distinction that matters.
  * The logo may be an FMV rather than the .ipu. If correlation is low for
    every candidate pairing, that is a real possibility and this script says
    so instead of concluding the decoder is broken.
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rdrstudio import config                         # noqa: E402

# `07_Research` has no config constant, so this joins onto `config.ROOT`.
# REQ-059.
SHOTS = Path(config.ROOT) / "07_Research" / "play_test"
DISC = Path(config.VANILLA_ISO)


def luma_grid(img, n=64):
    """Downsample to n x n mean luminance. Resampling kills fine detail, which
    is the point: it compares composition, not pixels, so the emulator's
    scaling and filtering do not dominate the score."""
    g = img.convert("L").resize((n, n))
    return list(g.getdata())


def correlate(a, b):
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va <= 0 or vb <= 0:
        return 0.0
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    return cov / (va ** 0.5 * vb ** 0.5)


def trim_uniform(img, tol=12):
    """Crop away near-uniform borders (window chrome, letterbox bars)."""
    import itertools
    g = img.convert("L")
    w, h = g.size
    px = g.load()

    def row_flat(y):
        vals = [px[x, y] for x in range(0, w, max(1, w // 64))]
        return max(vals) - min(vals) < tol

    def col_flat(x):
        vals = [px[x, y] for y in range(0, h, max(1, h // 64))]
        return max(vals) - min(vals) < tol

    top = next((y for y in range(h) if not row_flat(y)), 0)
    bot = next((y for y in range(h - 1, -1, -1) if not row_flat(y)), h - 1)
    left = next((x for x in range(w) if not col_flat(x)), 0)
    right = next((x for x in range(w - 1, -1, -1) if not col_flat(x)), w - 1)
    if right - left < 32 or bot - top < 32:
        return img, (0, 0, w, h)
    return img.crop((left, top, right + 1, bot + 1)), (left, top, right + 1, bot + 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ipu", default="rockstarpresents.ipu")
    ap.add_argument("--shot", default="")
    ap.add_argument("--save", default="")
    args = ap.parse_args()

    try:
        from PIL import Image
    except ImportError:
        print("Pillow is required")
        return 2

    try:
        from rdrstudio import ipudec
    except ImportError:
        print("rdrstudio/ipudec.py does not exist yet -- the decoder has not")
        print("been built. Nothing to verify. This is a real answer, not a")
        print("failure: run this again once the build phase lands.")
        return 3

    from rdrstudio.game import Game
    game = Game(DISC, writable=False)
    src = None
    for a in game.archives():
        try:
            if any(e.name.lower() == args.ipu.lower() for e in game.toc(a)):
                src = a
                break
        except Exception:
            continue
    if not src:
        print("no such .ipu: %s" % args.ipu)
        return 1
    data = game.read(src, args.ipu)
    game.close()

    try:
        w, h, rgb = ipudec.decode(data)
    except Exception as exc:
        print("decode FAILED: %s: %s" % (type(exc).__name__, exc))
        return 1
    got = Image.frombytes("RGB", (w, h), bytes(rgb))
    print("decoded %s -> %dx%d" % (args.ipu, w, h))
    if args.save:
        got.save(args.save)
        print("saved %s" % args.save)

    shots = ([Path(args.shot)] if args.shot
             else sorted(SHOTS.glob("*_010.0s.png"))[-4:])
    if not shots:
        print("no reference screenshots found in %s" % SHOTS)
        return 1

    print("\n%-30s %-22s %s" % ("reference", "viewport crop", "correlation"))
    best = 0.0
    for p in shots:
        try:
            ref = Image.open(p)
        except Exception as exc:
            print("  %-30s could not open: %s" % (p.name, exc))
            continue
        cropped, box = trim_uniform(ref)
        c = correlate(luma_grid(got), luma_grid(cropped))
        best = max(best, c)
        print("  %-30s %-22s %+.3f" % (p.name, "%d,%d %dx%d" % (
            box[0], box[1], box[2] - box[0], box[3] - box[1]), c))

    print("\nbest correlation: %+.3f" % best)
    if best > 0.75:
        print("MATCH -- this is the picture the game displays.")
    elif best > 0.4:
        print("PARTIAL. Composition lines up but something is off; suspect a")
        print("reconstruction parameter (DC precision, quantiser scale) rather")
        print("than the bitstream parse, which those cannot break.")
    else:
        print("NO MATCH. Either the decode is wrong, or this screen is an FMV")
        print("rather than this .ipu. Check a different pairing before")
        print("concluding the decoder is broken.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
