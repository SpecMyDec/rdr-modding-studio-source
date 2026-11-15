"""Texture format correctness, checked against the disc rather than by eye.

Four decoding defects were each producing a plausible-looking wrong picture:

  * `dimensions()` assumed every texture was square. 46 in the level archives
    are 2:1 or 1:2, and for those the square root came out whole only because
    halving or doubling the bit depth on a 2:1 image gives a square pixel
    count -- so the guess "succeeded" at the wrong depth against the wrong
    palette, and a fallback below it made that worse instead of erroring.
  * `encode_into()` inherited the same assumption on the WRITE path, so a
    128x128 replacement for a 256x64 texture had a matching byte count and
    was written to disc row-folded, with no error.
  * `decode_tex_file()` dispatched on file size, handing direct-colour files
    to the 8bpp branch, which indexed into image bytes reinterpreted as a
    palette and returned noise.
  * loose .tex palettes are B,G,R,A and already linear; the code read them as
    R,G,B,A and re-swizzled them.

The last one has an exact oracle. 772 distinct loose .tex files (1,246
instances) have a mip-0 index block byte-identical to an RSC texture's, so
both paths must decode them to the same RGB. That is what makes this a proof
rather than an opinion: before the fix, 1,245 of those 1,246 agreed only with
the channels swapped, and none agreed without.
"""

import hashlib
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rdrstudio import Game, texture as T
from rdrstudio.config import VANILLA_ISO
from rdrstudio.rsc import Rsc

# Every format family present among the disc's 4,472 distinct .tex files.
EXPECTED_TEX_FORMATS = {1, 6, 8, 11, 14, 15, 16, 17, 18}


def main():
    game = Game(VANILLA_ISO)
    failures = []

    # ---- RSC textures: header dimensions, and no out-of-palette pixels ----
    rsc_by_block = {}
    total = wrong_dims = undecoded = magenta = 0

    for archive in game.archives():
        dave = game.archive(archive)
        for entry in dave:
            if entry.ext != "rsc":
                continue
            try:
                bundle = Rsc(dave.read(entry), entry.name)
            except Exception:
                continue
            for tex_i, info, mip, pal in bundle.texture_table():
                total += 1
                raw_mip = bundle.raw(mip.index)

                # The MIP0 header and the TEX0 descriptor must agree.
                mw, mh = struct.unpack_from("<HH", raw_mip, T.SIZE_OFF)
                tw, th = struct.unpack_from("<HH", bundle.raw(tex_i), 0x88)
                if (mw, mh) != (tw, th):
                    failures.append("%s %s: MIP0 says %dx%d, TEX0 says %dx%d"
                                    % (entry.name, info["name"], mw, mh,
                                       tw, th))

                res = T.decode(raw_mip, T.parse_palette(bundle.raw(pal.index)))
                if res is None:
                    undecoded += 1
                    continue
                w, h, rgba = res
                if (w, h) != (info["width"], info["height"]):
                    wrong_dims += 1
                    continue

                # decode() paints out-of-palette indices magenta. A 4bpp image
                # read as 8bpp against a 16-entry palette lights up here.
                step = 4 * 13
                sampled = len(rgba) // step
                bad = sum(1 for k in range(0, len(rgba), step)
                          if rgba[k] == 255 and rgba[k + 1] == 0
                          and rgba[k + 2] == 255)
                if sampled and bad > sampled * 0.15:
                    magenta += 1
                    failures.append("%s %s: %.0f%% out-of-palette pixels"
                                    % (entry.name, info["name"],
                                       100.0 * bad / sampled))

                if pal.size >= 1040:
                    offs = T.mip_offsets(raw_mip)
                    start = offs[0] if offs else T.HEADER
                    block = raw_mip[start:start + w * h]
                    if len(block) == w * h:
                        rsc_by_block.setdefault(
                            hashlib.md5(block).hexdigest(), (res, info["name"]))

    print("  RSC textures      : %d, %d wrong dimensions, %d undecoded, "
          "%d out-of-palette" % (total, wrong_dims, undecoded, magenta))
    if wrong_dims or undecoded:
        failures.append("%d wrong dimensions, %d undecoded"
                        % (wrong_dims, undecoded))

    # ---- Loose .tex: every format decodes, and matches the RSC oracle ----
    seen = set()
    loose = decoded = 0
    formats = set()
    pairs = matched = 0

    for archive in game.archives():
        dave = game.archive(archive)
        for entry in dave:
            if entry.ext != "tex":
                continue
            try:
                data = dave.read(entry)
            except Exception:
                continue
            digest = hashlib.md5(data).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            loose += 1

            head = T.tex_header(data)
            if head is None:
                failures.append("%s: unreadable .tex header" % entry.name)
                continue
            w, h, fmt, _ = head
            formats.add(fmt)

            res = T.decode_tex_file(data)
            if res is None:
                failures.append("%s: format %d did not decode" % (entry.name,
                                                                  fmt))
                continue
            decoded += 1

            if fmt not in T.TEX_P8:
                continue
            block = data[T.TEX_HEADER + 1024:T.TEX_HEADER + 1024 + w * h]
            if len(block) != w * h:
                continue
            twin = rsc_by_block.get(hashlib.md5(block).hexdigest())
            if twin is None:
                continue
            (rw, rh, rgba), name = twin
            pairs += 1
            lw, lh, lrgba = res
            if (lw, lh) != (rw, rh):
                failures.append("%s: %dx%d but its RSC twin %s is %dx%d"
                                % (entry.name, lw, lh, name, rw, rh))
                continue
            same = all(lrgba[i] == rgba[i] and lrgba[i + 1] == rgba[i + 1]
                       and lrgba[i + 2] == rgba[i + 2]
                       for i in range(0, len(lrgba), 4 * 7))
            if same:
                matched += 1
            else:
                failures.append("%s: RGB differs from its RSC twin %s"
                                % (entry.name, name))

    print("  loose .tex        : %d unique, %d decoded, formats %s"
          % (loose, decoded, sorted(formats)))
    print("  pixel-identical   : %d pairs, %d match the RSC decode"
          % (pairs, matched))

    if decoded != loose:
        failures.append("only %d of %d loose .tex decoded" % (decoded, loose))
    if formats != EXPECTED_TEX_FORMATS:
        failures.append("format set changed: %s" % sorted(formats))
    # Counted over DISTINCT file contents, not instances -- the same texture
    # is shipped into many archives, so the instance count is ~1,246.
    if pairs < 700:
        failures.append("only %d cross-container pairs; expected 770+" % pairs)
    if matched != pairs:
        failures.append("%d of %d pairs disagree between the two decoders"
                        % (pairs - matched, pairs))

    # ---- Swizzled textures: the byte-exact oracle ----
    # 41% of textures declare GS tiling at MIP0[0x30]. Textures shipped both
    # inside an RSC and as a loose .tex give an exact answer: the swizzled
    # ones must match ONLY after unswizzling, the linear ones ONLY raw. This
    # is what catches a regression that would otherwise just look like
    # "some ground textures are a bit odd".
    loose_idx = {}
    for archive in game.archives():
        dave = game.archive(archive)
        for entry in dave:
            if entry.ext != "tex":
                continue
            try:
                data = dave.read(entry)
            except Exception:
                continue
            head = T.tex_header(data)
            if head is None or head[2] not in T.TEX_P8:
                continue
            w, h, _, _ = head
            block = data[T.TEX_HEADER + 1024:T.TEX_HEADER + 1024 + w * h]
            if len(block) == w * h:
                loose_idx.setdefault(entry.name[:-4].lower(), (w, h, block))

    tally = {1: [0, 0, 0], 5: [0, 0, 0]}   # flag -> [pairs, raw_id, unswz_id]
    swizzled = linear = 0
    for archive in game.archives():
        dave = game.archive(archive)
        for entry in dave:
            if entry.ext != "rsc":
                continue
            try:
                bundle = Rsc(dave.read(entry), entry.name)
            except Exception:
                continue
            for _, info, mip, pal in bundle.texture_table():
                raw_mip = bundle.raw(mip.index)
                if T.is_swizzled(raw_mip):
                    swizzled += 1
                else:
                    linear += 1
                if pal.size < 1040:
                    continue
                name = (info["name"] or "").lower()
                twin = loose_idx.get(name)
                if twin is None:
                    continue
                lw, lh, lblock = twin
                w, h = struct.unpack_from("<HH", raw_mip, T.SIZE_OFF)
                if (w, h) != (lw, lh):
                    continue
                offs = T.mip_offsets(raw_mip)
                start = offs[0] if offs else T.HEADER
                block = raw_mip[start:start + w * h]
                if len(block) != w * h:
                    continue
                flag = raw_mip[T.STORAGE_OFF]
                if flag not in tally:
                    continue
                tally[flag][0] += 1
                tally[flag][1] += (block == lblock)
                tally[flag][2] += (T.unswizzle32(block, w, h) == lblock)

    print("  storage modes     : %d swizzled, %d linear" % (swizzled, linear))
    for flag, label in ((1, "swizzled"), (5, "linear  ")):
        pairs, raw_id, unswz_id = tally[flag]
        print("  0x30=%d %s : %d pairs, %d identical raw, %d identical unswizzled"
              % (flag, label, pairs, raw_id, unswz_id))
    if tally[1][0] < 1000 or tally[5][0] < 1000:
        failures.append("too few cross-container pairs to judge swizzling")
    # The direction must be absolute. One reversal means the flag is not what
    # we think it is.
    if tally[1][1] != 0:
        failures.append("%d swizzled textures matched RAW" % tally[1][1])
    if tally[5][2] != 0:
        failures.append("%d linear textures matched UNSWIZZLED" % tally[5][2])
    if tally[1][2] < tally[1][0] * 0.8:
        failures.append("only %d/%d swizzled textures matched after unswizzling"
                        % (tally[1][2], tally[1][0]))
    if tally[5][1] < tally[5][0] * 0.9:
        failures.append("only %d/%d linear textures matched raw"
                        % (tally[5][1], tally[5][0]))

    # ---- RGB24 is stored bottom-up and must be flipped on decode ----
    # Asserted structurally rather than by looking at the picture: row 0 of
    # the decoded image has to be the LAST row of the file's pixel data.
    checked = 0
    for archive in game.archives():
        dave = game.archive(archive)
        for entry in dave:
            if entry.ext != "tex":
                continue
            try:
                data = dave.read(entry)
            except Exception:
                continue
            head = T.tex_header(data)
            if head is None or head[2] not in T.TEX_RGB24:
                continue
            w, h, _, _ = head
            res = T.decode_tex_file(data)
            if res is None:
                continue
            _, _, rgba = res
            last = T.TEX_HEADER + (h - 1) * w * 3
            if tuple(rgba[0:3]) != tuple(data[last:last + 3]):
                failures.append("%s: RGB24 row order is not bottom-up"
                                % entry.name)
            checked += 1
            break
        if checked:
            break
    print("  RGB24 row order   : %s"
          % ("bottom-up, flipped on decode" if checked else "NOT CHECKED"))
    if not checked:
        failures.append("no RGB24 texture was available to check row order")

    # ---- The write path must refuse what it cannot encode ----
    # The existing round-trip test happens to pick a LINEAR 8bpp texture, so
    # it can never catch a swizzled target being written linearly. Both kinds
    # of unsupported target are checked here explicitly.
    refused_bpp = refused_swz = accepted = 0
    for archive in ("LVLS_SNG/PERFNEW.DAT", "LVLS_SNG/MANS.DAT"):
        dave = game.archive(archive)
        for entry in dave:
            if entry.ext != "rsc":
                continue
            try:
                bundle = Rsc(dave.read(entry), entry.name)
            except Exception:
                continue
            for _, info, mip, pal in bundle.texture_table():
                raw_mip = bundle.raw(mip.index)
                swz = T.is_swizzled(raw_mip)
                if T.pixel_format(raw_mip) == 8 and not swz:
                    continue
                w, h = info["width"], info["height"]
                try:
                    T.encode_into(raw_mip, bundle.raw(pal.index),
                                  b"\x00" * (w * h * 4), w, h)
                except ValueError:
                    if swz:
                        refused_swz += 1
                    else:
                        refused_bpp += 1
                else:
                    accepted += 1
                    failures.append(
                        "%s: encode_into accepted an unsupported target "
                        "(%s)" % (info["name"],
                                  "swizzled" if swz else "4bpp"))
    print("  write path        : refused %d swizzled + %d 4bpp targets, "
          "accepted %d" % (refused_swz, refused_bpp, accepted))
    if refused_swz == 0:
        failures.append("no swizzled target was exercised on the write path")

    for line in failures[:20]:
        print("  FAIL " + line)
    if failures:
        print("  %d failure(s)" % len(failures))
        return 1
    print("  OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
