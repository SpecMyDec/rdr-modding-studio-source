"""Texture import round-trip.

Decode a texture, write it back through the encoder, decode again and compare.
A same-size replacement must produce byte-identical chunk lengths -- that
property is what makes retexturing always fit its slot.

Also checks the PNG reader against our own writer, and that a genuinely
different image actually lands.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rdrstudio import Game                       # noqa: E402
from rdrstudio import config as cfg               # noqa: E402
from rdrstudio import texture as T                # noqa: E402
from rdrstudio.rsc import Rsc                     # noqa: E402

TMP = os.path.join(cfg.ROOT, "07_Research", "tex_roundtrip")


def mean_abs_diff(a, b):
    n = min(len(a), len(b))
    if not n:
        return 999
    return sum(abs(a[i] - b[i]) for i in range(0, n, 4)) / (n / 4)


def main():
    os.makedirs(TMP, exist_ok=True)
    g = Game(cfg.VANILLA_ISO)
    d = g.archive("LVLS_SNG/MANS.DAT")
    r = Rsc(d.read("lvl_mans.rsc"), "lvl_mans.rsc")
    table = r.texture_table()
    ok = True

    # Pick a 256-colour texture with real detail.
    target = None
    for i, (_, info, mip, pal) in enumerate(table):
        if pal.tag == "PAL0" and info["width"] >= 64:
            target = (i, info, mip, pal)
            break
    if target is None:
        print("no suitable texture found")
        return 1
    i, info, mip, pal = target
    print("texture         : #%d %s %dx%d" % (i, info["name"], info["width"], info["height"]))

    w, h, original = T.decode(r.raw(mip.index), T.parse_palette(r.raw(pal.index)))
    T.write_png(os.path.join(TMP, "before.png"), w, h, original)

    # 1. our PNG writer -> our PNG reader must agree exactly
    rw, rh, reread = T.read_png(open(os.path.join(TMP, "before.png"), "rb").read())
    same = (rw, rh) == (w, h) and reread == original
    print("png write/read  : %s" % ("exact" if same else "MISMATCH"))
    ok &= same

    # 2. re-encode the identical image; chunk sizes must not change
    new_mip, new_pal = T.encode_into(r.raw(mip.index), r.raw(pal.index),
                                     original, w, h)
    size_ok = len(new_mip) == mip.size and len(new_pal) == pal.size
    print("chunk sizes     : mip %d->%d  pal %d->%d  %s"
          % (mip.size, len(new_mip), pal.size, len(new_pal),
             "unchanged" if size_ok else "CHANGED!"))
    ok &= size_ok

    # 3. decode the re-encoded texture; quantisation loss should be small
    w2, h2, again = T.decode(new_mip, T.parse_palette(new_pal))
    T.write_png(os.path.join(TMP, "after.png"), w2, h2, again)
    err = mean_abs_diff(original, again)
    print("round-trip error: %.2f mean abs per channel (256 levels)" % err)
    ok &= err < 12.0

    # 4. a clearly different image must actually take effect
    grad = bytearray(w * h * 4)
    for y in range(h):
        for x in range(w):
            o = (y * w + x) * 4
            grad[o] = (x * 255) // max(1, w - 1)
            grad[o + 1] = (y * 255) // max(1, h - 1)
            grad[o + 2] = 128
            grad[o + 3] = 255
    gm, gp = T.encode_into(r.raw(mip.index), r.raw(pal.index), bytes(grad), w, h)
    _, _, gdec = T.decode(gm, T.parse_palette(gp))
    T.write_png(os.path.join(TMP, "gradient.png"), w, h, gdec)
    gerr = mean_abs_diff(bytes(grad), gdec)
    changed = mean_abs_diff(original, gdec)
    print("gradient recall : %.2f error, %.1f away from the original" % (gerr, changed))
    ok &= gerr < 12.0 and changed > 20.0

    print("\nwrote before/after/gradient PNGs to %s" % TMP)
    print("\n%s" % ("ALL CHECKS PASSED" if ok else "SOMETHING FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
