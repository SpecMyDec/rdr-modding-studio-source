"""PS2 texture decoding: PAL0 palettes + MIP0 pixel data -> RGBA / PNG.

An RSC bundle stores a texture as up to four chunks:
    PMT0   NOT A PALETTE. 64 or 128 bytes, and it looks like a 16-entry
           RGBA CLUT purely because 16 x 4 = 64. All 5,307 on the disc carry
           BND0's own magic (0x0FFFFF00) at offset 0 and an ASCII name at +8,
           and those names are `default` `rubble` `sand` `gravel` `ash` `bone`
           `horseBounce` -- the collision-surface materials from `.pmtl`, not
           colours. No PMT0 anywhere is followed by a TEX0, so nothing ever
           actually paired one as a palette; it was a latent trap, not a live
           bug.
    PAL0   1040 bytes  16-byte header + 256 RGBA entries (8bpp)
    TEX0   192 bytes   resource descriptor -- NOT GS registers
    MIP0   variable    96-byte header, indices, optional mip chain

The MIP0 header states the format outright, so nothing here has to be
inferred from file size:
    0x28  u32  format enum   5 = 8bpp, 6 = 4bpp
    0x2C  u16  width
    0x2E  u16  height

Both agree with TEX0's own copy at 0x88/0x8A for 4,451 of 4,451 textures in
the level archives, and the format enum agrees with palette size for all of
them. TEX0 is a descriptor, not GS state: bytes 0x00-0x6F are zero disc-wide,
0x90 is the chunk's own index and 0x94 points at its palette chunk. No
TBP0/TBW/PSM/TCC packing lines up at any offset.

Things that cost real time to pin down:
  * alpha is 0..128, where 0x80 means fully opaque -- not 0..255. In every
    .rsc on the disc the raw byte never once exceeds 0x80 (1,415,232 entries),
    and it is quantised to a 32-step ladder. Loose .tex and .xrsc palettes,
    by contrast, genuinely are full-range 0..255.
  * the CLUT is swizzled (index bits 3 and 4 swapped) but the PIXELS ARE NOT.
    Applying the usual PSMT8 unswizzle actively corrupts them. Verified by
    rendering the raw indices as greyscale: linear order shows a clean image,
    unswizzled order breaks it into blocks.
  * a texture's palette is the PAL0 chunk that FOLLOWS its MIP0, not the one
    before it -- the order on disc is MIP0, PAL0, TEX0.
  * textures are NOT all square. 46 in the level archives are 2:1 or 1:2, and
    assuming otherwise mis-sized them into the wrong bit depth.
  * loose .tex palettes are B,G,R,A and already in linear order -- the
    opposite of the RSC form on both counts. See decode_tex_file.

There is no per-texture "ignore the alpha channel" flag. The only varying
flag word, TEX0+0x8C, takes seven values disc-wide and only bits 0, 16 and 22
ever move; a sweep of all 192 TEX0 bytes plus all 96 MIP0 header bytes (2,304
candidate bits) found nothing predicting alpha usage above chance. Several
ground textures are pure alpha masks -- flat RGB, image entirely in alpha --
so ignoring alpha would render them as solid quads.
"""

import struct
import zlib

HEADER = 96


def unswizzle_clut(entries):
    """PS2 stores a 256-entry CLUT with bits 3 and 4 of the index swapped."""
    out = [None] * len(entries)
    for i in range(len(entries)):
        j = (i & ~0x18) | ((i & 0x08) << 1) | ((i & 0x10) >> 1)
        out[i] = entries[j if j < len(entries) else i]
    return out


def unswizzle32(data, width, height):
    """Undo the GS PSMT8 tiling into linear rows."""
    out = bytearray(width * height)
    for y in range(height):
        for x in range(width):
            block = (y & ~0xF) * width + (x & ~0xF) * 2
            swap = (((y + 2) >> 2) & 0x1) * 4
            ypos = (((y & ~3) >> 1) + (y & 1)) & 0x7
            column = ypos * width * 2 + ((x + swap) & 0x7) * 4
            byte = ((y >> 1) & 1) + ((x >> 2) & 2)
            src = block + column + byte
            if src < len(data):
                out[y * width + x] = data[src]
    return bytes(out)


def parse_palette(data):
    """PAL0 -> list of (r, g, b, a) 0..255.

    The 256-entry form is required to be exactly 1040 bytes. `.xrsc` bundles
    in SWAT.DAT carry 1152-byte PAL0 chunks whose body starts at 0x40 and
    whose alpha is full-range 0..255; a `>=` test would slice those at the
    wrong offset *and* double an alpha that is already full scale.
    """
    # A PS2 256-colour palette padded past 1040 (bundles built by the RDR
    # Blender add-on write 1152, which the game reads fine) keeps the PS2
    # header -- byte 8 is 4 -- where the Xbox form has 0 there; it must not be
    # taken for the Xbox layout, which read its colours from the padding.
    if len(data) == 1040 or (len(data) > 1040 and data[8] == 4):
        body, count = data[16:16 + 1024], 256
    elif len(data) >= 64:
        body, count = data[-64:], 16
    else:
        return []
    pal = [tuple(body[i * 4:i * 4 + 4]) for i in range(count)]
    if count == 256:
        pal = unswizzle_clut(pal)
    # Rescale PS2 alpha: 0x80 is opaque. Measured across 1,415,232 entries in
    # every .rsc on the disc, the raw byte never exceeds 0x80 -- so the clamp
    # here is a guard, not a routine operation. (Loose .tex and .xrsc palettes
    # *are* full-range, and are handled on their own paths.)
    return [(r, g, b, min(255, a * 2)) for r, g, b, a in pal]


def mip_offsets(data):
    """The header lists each mip level's offset; they also give the sizes."""
    offs = []
    for i in range(8):
        v = struct.unpack_from("<I", data, 8 + i * 4)[0]
        if v == 0 or v >= len(data):
            break
        if offs and v <= offs[-1]:
            break
        offs.append(v)
    return offs


# MIP0 header fields, verified against every texture on the disc.
FORMAT_OFF = 0x28          # u32 pixel format enum
SIZE_OFF = 0x2C            # u16 width, u16 height
STORAGE_OFF = 0x30         # u8 storage mode: 5/6 = linear, 1 = swizzled
FORMAT_BPP = {5: 8, 6: 4}
SWIZZLED = 1


def is_swizzled(data):
    """Whether this texture's pixels are stored in GS tiled order.

    The module used to state flatly that pixels are linear. That is true for
    851 of 1,447 unique textures and FALSE for the other 596 -- the original
    greyscale spot-check simply landed on linear ones.

    Proven byte-exactly against textures shipped both inside an RSC and as a
    loose .tex: for `0x30 == 1`, 1,693 of 1,998 name-matched pairs are
    identical after unswizzling and **none** are identical raw; for
    `0x30 == 5`, 2,897 of 2,961 are identical raw and **none** unswizzled.
    Zero reversals in either direction.

    The swizzle rate rises with size -- 512x512 89%, 512x256 100%, 64x64 28%
    -- which is why large ground and terrain textures were the ones that
    looked wrong while small props looked fine.
    """
    return len(data) > STORAGE_OFF and data[STORAGE_OFF] == SWIZZLED


def pixel_format(data):
    """Bits per pixel from the MIP0 header, or None if unrecognised.

    The enum at 0x28 agrees with palette size for all 4,451 textures in the
    level archives: 5 -> 256-entry (8bpp), 6 -> 16-entry (4bpp). Reading it
    beats inferring depth from the palette, because the pairing is what goes
    wrong in exactly the cases that matter.
    """
    if len(data) < FORMAT_OFF + 4:
        return None
    return FORMAT_BPP.get(data[FORMAT_OFF])


def dimensions(data, bpp=None):
    """Width and height of mip 0, read from the MIP0 header.

    This used to infer the size from the gap between mip offsets and assume
    the result was square. It is not: 46 textures in the level archives are
    2:1 or 1:2 (`p2_Back001` 256x64, `jailShadows` 512x256, `mismnt` 128x64).
    For those the square root came out whole only because halving or doubling
    the bit depth on a 2:1 image yields a square pixel count -- so the guess
    "succeeded" with the wrong depth against the wrong palette, and the
    fallback below it silently made that worse rather than erroring.

    The real values are in the header at 0x2C, and they match the TEX0
    descriptor's copy at 0x88 for 4,451 of 4,451 textures.

    `bpp` is accepted and ignored, for callers written against the old
    signature.
    """
    if len(data) < SIZE_OFF + 4:
        return None
    w, h = struct.unpack_from("<HH", data, SIZE_OFF)
    if not (0 < w <= 4096 and 0 < h <= 4096):
        return None
    return w, h


def decode(mip_data, palette):
    """-> (width, height, RGBA bytes) for mip level 0, or None."""
    if not palette:
        return None
    bpp = pixel_format(mip_data)
    if bpp is None:
        # Fall back to the palette only when the header enum is unknown;
        # never override a header that stated its format.
        bpp = 4 if len(palette) <= 16 else 8
    dim = dimensions(mip_data)
    if not dim:
        return None
    w, h = dim

    offs = mip_offsets(mip_data)
    start = offs[0] if offs else HEADER
    need = w * h // 2 if bpp == 4 else w * h
    raw = mip_data[start:start + need]
    if len(raw) < need:
        return None

    if bpp == 4:
        idx = bytearray(w * h)
        for i, byte in enumerate(raw):
            idx[i * 2] = byte & 0x0F
            idx[i * 2 + 1] = byte >> 4
    else:
        idx = raw

    # Undo GS tiling for the textures that declare it. The guard is on WIDTH
    # alone: unswizzle32 is a clean bijection for any width >= 16 at any
    # height, and breaks below that by reading out of bounds. No swizzled
    # 8bpp texture on this disc is narrower than 16 px, so it never fires
    # here -- but it is the correct bound rather than a lucky one.
    if bpp == 8 and w >= 16 and is_swizzled(mip_data):
        idx = unswizzle32(idx, w, h)
    # 4-bit ones were drawn as stored -- a third of MANSINTB's textures (24 of 72) are 4-bit swizzled and came out
    # as noise. They are read back from the PSMCT32 rectangle the header gives (+0x32, its height), the same
    # unswizzle the .pck fonts and Png2tex use (ps2gs.unswizzle4); measured on MANSINTB it halves the pixel-to-pixel
    # roughness of 22 of the 24 and leaves the flat ones alike.
    elif bpp == 4 and w >= 32 and h >= 16 and is_swizzled(mip_data):
        try:
            from . import ps2gs
            idx = bytes(ps2gs.unswizzle4(raw, w, h, mip_data[0x32] or None).reshape(-1))
        except Exception:                                      # noqa: BLE001
            pass
    out = bytearray(w * h * 4)
    n = len(palette)
    for i, p in enumerate(idx):
        r, g, b, a = palette[p] if p < n else (255, 0, 255, 255)
        o = i * 4
        out[o] = r
        out[o + 1] = g
        out[o + 2] = b
        out[o + 3] = a
    return w, h, bytes(out)


def encode_png(width, height, rgba):
    """Minimal PNG encoder -> bytes. No dependencies.

    Separate from `write_png` because the web layer wants the bytes, and was
    otherwise round-tripping every texture request through a temp file.
    """
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type 0
        raw += rgba[y * width * 4:(y + 1) * width * 4]

    def chunk(tag, payload):
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
            + chunk(b"IEND", b""))


def write_png(path, width, height, rgba):
    """Minimal PNG writer -- no dependencies."""
    with open(path, "wb") as f:
        f.write(encode_png(width, height, rgba))
    return path


# --------------------------------------------------------- loose .tex files

TEX_HEADER = 14


# Loose .tex header: seven little-endian u16.
#     0x00 width   0x02 height   0x04 format   0x06 levels
#     0x08 / 0x0A / 0x0C  flags
# The format enum partitions the disc's 4,472 distinct .tex files cleanly.
TEX_P8 = (1, 14)          # 1024-byte palette + 8bpp indices
TEX_P4 = (15, 16)         # 64-byte palette + 4bpp indices
TEX_I8 = (8, 11)          # greyscale, no palette
TEX_RGBA16 = (6,)         # RGBA5551
TEX_RGB24 = (17,)
TEX_RGBA32 = (18,)


def tex_header(data):
    """-> (width, height, format, levels) for a loose .tex, or None."""
    if len(data) < TEX_HEADER:
        return None
    w, h, fmt, levels = struct.unpack_from("<4H", data, 0)
    if not (0 < w <= 4096 and 0 < h <= 4096):
        return None
    return w, h, fmt, levels


def decode_tex_file(data):
    """Decode a standalone .tex file (12,932 of them on the disc).

    Dispatch is on the format enum at offset 4, not on file size. Guessing
    from size silently handed direct-colour files (`logo.tex`, `mainmenu.tex`,
    `sky.tex`, 46 copies of `sun01.tex`) to the 8bpp branch, which indexed
    into raw image bytes reinterpreted as a palette and returned a
    texture-shaped field of noise -- a wrong answer with no error.

    Two things about the loose palette that differ from the RSC form, both
    established by hashing mip-0 index blocks and matching the 1,246 loose
    textures that are pixel-identical to an RSC one:

      * it is stored **B, G, R, A** -- 1,245 of those 1,246 pairs agree with
        the channels swapped and *none* agree without;
      * it is already in linear order, so it must NOT be run through
        `unswizzle_clut`. Doing so re-swizzles it and speckles the result.

    Both fixes have to land together: either alone still yields a wrong
    image. Alpha here is genuinely full-range 0..255, unlike the RSC palettes
    where 0x80 is opaque, so this path must not double it.
    """
    head = tex_header(data)
    if head is None:
        return None
    w, h, fmt, _levels = head
    px = w * h
    out = bytearray(px * 4)
    body = TEX_HEADER

    def from_palette(pal, idx):
        n = len(pal)
        for i, p in enumerate(idx):
            b, g, r, a = pal[p] if p < n else (255, 0, 255, 255)
            o = i * 4
            out[o] = r
            out[o + 1] = g
            out[o + 2] = b
            out[o + 3] = a

    if fmt in TEX_P8:
        if body + 1024 + px > len(data):
            return None
        pal = [tuple(data[body + i * 4:body + i * 4 + 4]) for i in range(256)]
        from_palette(pal, data[body + 1024:body + 1024 + px])

    elif fmt in TEX_P4:
        if body + 64 + px // 2 > len(data):
            return None
        pal = [tuple(data[body + i * 4:body + i * 4 + 4]) for i in range(16)]
        raw = data[body + 64:body + 64 + px // 2]
        idx = bytearray(px)
        for i, byte in enumerate(raw):
            idx[i * 2] = byte & 0x0F
            idx[i * 2 + 1] = byte >> 4
        # Channel order matches the 8bpp palettes; there is no pixel-identical
        # 4bpp pair on the disc to prove it, only a population lean.
        from_palette(pal, idx)

    elif fmt in TEX_I8:
        if body + px > len(data):
            return None
        for i, v in enumerate(data[body:body + px]):
            o = i * 4
            out[o] = out[o + 1] = out[o + 2] = v
            out[o + 3] = 255

    elif fmt in TEX_RGBA16:
        if body + px * 2 > len(data):
            return None
        for i in range(px):
            v = struct.unpack_from("<H", data, body + i * 2)[0]
            o = i * 4
            out[o] = (v & 0x1F) << 3
            out[o + 1] = ((v >> 5) & 0x1F) << 3
            out[o + 2] = ((v >> 10) & 0x1F) << 3
            out[o + 3] = 255 if v & 0x8000 else 0

    elif fmt in TEX_RGB24:
        if body + px * 3 > len(data):
            return None
        # RGB24 rows are stored bottom-up, unlike every palettised format on
        # this disc. All 33 of these are full-screen menu art, so they most
        # likely came through an art pipeline that wrote BMP/TGA, both of
        # which are bottom-up by convention. Proven on content that cannot be
        # read two ways: `logo.tex` is the main menu with legible text, and
        # `ui_characterlineupl24b.tex` is a Brimstone street -- both arrive
        # inverted and both come right with a row flip.
        for y in range(h):
            src = body + (h - 1 - y) * w * 3
            for x in range(w):
                o, s = (y * w + x) * 4, src + x * 3
                out[o] = data[s]
                out[o + 1] = data[s + 1]
                out[o + 2] = data[s + 2]
                out[o + 3] = 255

    elif fmt in TEX_RGBA32:
        if body + px * 4 > len(data):
            return None
        out[:] = data[body:body + px * 4]

    else:
        # An unknown format must fail loudly rather than be forced through a
        # branch that will produce a plausible-looking wrong picture.
        return None

    return w, h, bytes(out)


# ------------------------------------------------------------ PNG reading

def read_png(data):
    """Minimal PNG reader -> (width, height, RGBA bytes). No dependencies.

    Handles 8-bit truecolour (with or without alpha), greyscale and palette
    images -- everything an image editor is likely to hand back.
    """
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos = 8
    idat = bytearray()
    width = height = depth = ctype = None
    plte = None
    trns = None
    while pos + 8 <= len(data):
        length, tag = struct.unpack_from(">I4s", data, pos)
        pos += 8
        body = data[pos:pos + length]
        pos += length + 4  # skip CRC
        if tag == b"IHDR":
            width, height, depth, ctype = struct.unpack_from(">IIBB", body, 0)
        elif tag == b"PLTE":
            plte = body
        elif tag == b"tRNS":
            trns = body
        elif tag == b"IDAT":
            idat += body
        elif tag == b"IEND":
            break

    if depth != 8:
        raise ValueError("only 8-bit PNGs are supported (got %d-bit)" % depth)
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(ctype)
    if channels is None:
        raise ValueError("unsupported PNG colour type %d" % ctype)

    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    out = bytearray(width * height * 4)
    prev = bytearray(stride)
    p = 0
    for y in range(height):
        f = raw[p]
        p += 1
        line = bytearray(raw[p:p + stride])
        p += stride
        # Undo the per-scanline filter.
        if f == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif f == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif f == 4:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        elif f != 0:
            raise ValueError("bad PNG filter %d" % f)
        prev = line

        for x in range(width):
            o = (y * width + x) * 4
            if ctype == 6:
                out[o:o + 4] = line[x * 4:x * 4 + 4]
            elif ctype == 2:
                out[o:o + 3] = line[x * 3:x * 3 + 3]
                out[o + 3] = 255
            elif ctype == 0:
                v = line[x]
                out[o] = out[o + 1] = out[o + 2] = v
                out[o + 3] = 255
            elif ctype == 4:
                v = line[x * 2]
                out[o] = out[o + 1] = out[o + 2] = v
                out[o + 3] = line[x * 2 + 1]
            elif ctype == 3:
                i = line[x]
                if plte and i * 3 + 3 <= len(plte):
                    out[o:o + 3] = plte[i * 3:i * 3 + 3]
                out[o + 3] = trns[i] if trns and i < len(trns) else 255
    return width, height, bytes(out)


# --------------------------------------------------------------- encoding

def _median_cut(pixels, want):
    """Quantise to `want` colours, returning (palette, per-pixel index).

    Median cut partitions the pixels themselves, so each pixel's bucket IS its
    palette index -- no nearest-colour search afterwards, which matters a lot
    in pure Python (a 256x256 image would otherwise be 16M distance tests).
    """
    boxes = [list(range(len(pixels)))]
    while len(boxes) < want:
        # Split the box with the widest spread on its widest channel.
        target, best_range, best_ch = None, -1, 0
        for b, idxs in enumerate(boxes):
            if len(idxs) < 2:
                continue
            for ch in range(3):
                vals = [pixels[i][ch] for i in idxs]
                spread = max(vals) - min(vals)
                if spread > best_range:
                    target, best_range, best_ch = b, spread, ch
        if target is None or best_range <= 0:
            break
        idxs = boxes.pop(target)
        idxs.sort(key=lambda i: pixels[i][best_ch])
        mid = len(idxs) // 2
        boxes.append(idxs[:mid])
        boxes.append(idxs[mid:])

    palette = []
    index = [0] * len(pixels)
    for n, idxs in enumerate(boxes):
        if not idxs:
            palette.append((0, 0, 0, 0))
            continue
        r = g = b = a = 0
        for i in idxs:
            p = pixels[i]
            r += p[0]
            g += p[1]
            b += p[2]
            a += p[3]
            index[i] = n
        k = len(idxs)
        palette.append((r // k, g // k, b // k, a // k))
    while len(palette) < want:
        palette.append((0, 0, 0, 0))
    return palette, index


def _nearest(palette, colour):
    best, bd = 0, None
    for i, p in enumerate(palette):
        d = ((p[0] - colour[0]) ** 2 + (p[1] - colour[1]) ** 2
             + (p[2] - colour[2]) ** 2 + (p[3] - colour[3]) ** 2)
        if bd is None or d < bd:
            best, bd = i, d
    return best


def _box_downscale(rgba, w, h):
    """Halve an RGBA image with a 2x2 box filter."""
    nw, nh = max(1, w // 2), max(1, h // 2)
    out = bytearray(nw * nh * 4)
    for y in range(nh):
        for x in range(nw):
            acc = [0, 0, 0, 0]
            for dy in range(2):
                for dx in range(2):
                    sx, sy = min(w - 1, x * 2 + dx), min(h - 1, y * 2 + dy)
                    o = (sy * w + sx) * 4
                    for c in range(4):
                        acc[c] += rgba[o + c]
            o = (y * nw + x) * 4
            for c in range(4):
                out[o + c] = acc[c] // 4
    return nw, nh, bytes(out)


def encode_into(mip_data, pal_data, rgba, width, height):
    """Re-encode an image into an existing MIP0 + PAL0 pair.

    Returns (new_mip_bytes, new_pal_bytes). Both keep their original length,
    so a replacement at the same dimensions is guaranteed to fit its slot --
    which is what makes retexturing safe on this disc.
    """
    dim = dimensions(mip_data)
    if dim is None:
        raise ValueError("cannot determine the target texture's size")
    tw, th = dim
    if (width, height) != (tw, th):
        raise ValueError("image is %dx%d but this texture is %dx%d -- resize it"
                         % (width, height, tw, th))
    # The size check above compared against a hardcoded 8bpp reading, so for
    # the eight 256x64 textures a 128x128 replacement had the same byte count
    # and was accepted without complaint -- then written to disc row-folded.
    # A silently wrong write is worse than a refusal, so the depth now comes
    # from the header and anything but 8bpp is turned away.
    bpp = pixel_format(mip_data)
    if bpp is not None and bpp != 8:
        raise ValueError("target is a %d-bit texture; only 256-colour "
                         "textures can be replaced for now" % bpp)
    # Writing linear indices into a slot the game reads as tiled produces a
    # scrambled texture. The write itself is same-length and in-slot, so it
    # cannot damage the archive -- but it silently ruins the picture, and a
    # refusal is better than a mess that looks like a decode bug later.
    if is_swizzled(mip_data):
        raise ValueError("this texture is stored swizzled, and re-swizzling "
                         "on import is not implemented yet -- replacing it "
                         "would scramble the image")
    if len(pal_data) < 1040:
        raise ValueError("target uses a %d-byte palette; only 256-colour "
                         "textures can be replaced for now" % len(pal_data))

    pixels = [tuple(rgba[i * 4:i * 4 + 4]) for i in range(width * height)]
    palette, index = _median_cut(pixels, 256)

    offs = mip_offsets(mip_data)
    out = bytearray(mip_data)
    start = offs[0] if offs else HEADER
    out[start:start + width * height] = bytes(index)

    # Regenerate the mip chain against the same palette, so lower levels stay
    # consistent with the new artwork instead of showing the old texture.
    cur_rgba, cw, ch = rgba, width, height
    for level in range(1, len(offs)):
        cw, ch, cur_rgba = _box_downscale(cur_rgba, cw, ch)
        n = cw * ch
        if offs[level] + n > len(out):
            break
        cache = {}
        small = bytearray(n)
        for i in range(n):
            col = tuple(cur_rgba[i * 4:i * 4 + 4])
            j = cache.get(col)
            if j is None:
                j = cache[col] = _nearest(palette, col)
            small[i] = j
        out[offs[level]:offs[level] + n] = bytes(small)

    # Write the CLUT back swizzled and with PS2 alpha (0x80 = opaque).
    entries = [(r, g, b, min(128, a // 2)) for r, g, b, a in palette]
    entries = unswizzle_clut(entries)  # the transform is its own inverse
    pal = bytearray(pal_data)
    for i, (r, g, b, a) in enumerate(entries):
        o = 16 + i * 4
        pal[o:o + 4] = bytes((r, g, b, a))
    return bytes(out), bytes(pal)


def textures_in(rsc):
    """Pair each MIP0 pixel chunk with the palette that FOLLOWS it.

    Order on disc is MIP0, PAL0, TEX0. Pairing with the preceding palette
    instead gives every texture its neighbour's colours -- which looks exactly
    like a broken swizzle, and cost an hour to spot.
    """
    out = []
    for i, c in enumerate(rsc.chunks):
        if c.tag != "MIP0":
            continue
        for nxt in rsc.chunks[i + 1:i + 4]:
            if nxt.tag == "PAL0":
                out.append((c, nxt))
                break
    return out
