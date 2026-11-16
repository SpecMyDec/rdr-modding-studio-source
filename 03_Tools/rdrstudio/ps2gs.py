"""PS2 GS memory orders for textures uploaded as 32-bit words (the "swizzled" pictures of the game's .pck screens).

The game uploads a picture's bytes as a PSMCT32 rectangle and the GS reads them back as PSMT8 or PSMT4 texels.
Emulating both address maps gives the texel order: `unswizzle8/4` (stored -> rows) and `swizzle8/4` (rows ->
stored). The upload rectangle is the one whose words are exactly the ones the texture reads (a 128x64 PSMT4 atlas
is uploaded 32x32, not 64x16): found by matching, not assumed. Index maps are cached per size (numpy).
"""

from __future__ import annotations

import functools

import numpy as np

BLOCK32 = [[0, 1, 4, 5, 16, 17, 20, 21], [2, 3, 6, 7, 18, 19, 22, 23],
           [8, 9, 12, 13, 24, 25, 28, 29], [10, 11, 14, 15, 26, 27, 30, 31]]
COL32 = [[0, 1, 4, 5, 8, 9, 12, 13], [2, 3, 6, 7, 10, 11, 14, 15]]
BLOCK4 = [[0, 2, 8, 10], [1, 3, 9, 11], [4, 6, 12, 14], [5, 7, 13, 15],
          [16, 18, 24, 26], [17, 19, 25, 27], [20, 22, 28, 30], [21, 23, 29, 31]]


def _addr32(x, y, bw):
    page = (y // 32) * bw + (x // 64)
    block = np.array(BLOCK32)[(y % 32) // 8, (x % 64) // 8]
    word = ((y % 8) // 2) * 16 + np.array(COL32)[y % 2, x % 8]
    return page * 2048 + block * 64 + word


def _col4(y, x):
    r, c = y % 4, y // 4
    i, g = x % 8, x // 8
    flip = (r >= 2) != (c % 2 == 1)
    i = np.where(flip, i ^ 4, i)
    return (i // 2) * 32 + (i % 2) * 8 + g * 2 + (r % 2) * 16 + (r >= 2) + c * 128


def _addr4(x, y, ppr):
    """nibble address of PSMT4 texel (x, y); a PSMT4 page is 128x128."""
    page = (y // 128) * ppr + (x // 128)
    block = np.array(BLOCK4)[(y % 128) // 16, (x % 128) // 32]
    return (page * 2048 + block * 64) * 8 + _col4(y % 16, x % 32)


def upload_h(w, h, bpp=4):
    """The height of the PSMCT32 rectangle the game uploads a w x h texture as -- its texture header says it
    (+0x32) and the game's own are all as square as can be: as wide as high, or twice (64x64 -> 32x16, 128x64 ->
    32x32, 128x128 -> 64x32, 256x128 -> 64x64, 256x256 -> 128x64, 512x256 -> 128x128; every one of the game's 4-bit
    atlases decodes cleanly so and no other way)."""
    words = w * h * bpp // 32
    for H32 in range(1, words + 1):
        if words % H32:
            continue
        W32 = words // H32
        if W32 in (H32, 2 * H32):
            return H32
    return max(1, h // 4)


def header_fields(w, h, bpp, swizzled=True):
    """The texture header's upload fields for a w x h texture: (+0x2a data size in 256-byte units, +0x31, +0x32)
    -- +0x32 is the upload rectangle's height, +0x31 half the width over the rectangle's width (linear ones: 0 and
    the height)."""
    size = w * h * bpp // 8 // 256
    if not swizzled:
        return max(1, size), 0, h
    H32 = upload_h(w, h, bpp)
    W32 = (w * h * bpp // 32) // H32
    return max(1, size), max(1, (w // 2) // max(1, W32)), H32


@functools.lru_cache(maxsize=64)
def _upload(w, h, bpp, H32=None):
    """(W32, H32, bw, addr map of each texel) for a w x h texture of 4 or 8 bpp, uploaded H32 high (the game's
    choice when not given)."""
    ys, xs = np.mgrid[0:h, 0:w]
    if bpp == 4:
        ppr = max(1, w // 128)
        tex = _addr4(xs, ys, ppr)               # nibble addresses
        unit = 8
    else:
        raise ValueError('8bpp uses the closed form (_map8)')
    need = np.unique(tex // unit)
    n = w * h * bpp // 32
    want = H32 or upload_h(w, h, bpp)
    tries = [n // want] + [W for W in (w // 2, w // 4, w, w // 8, w * 2) if W != n // want]
    for W32 in tries:
        if W32 < 1 or n % W32:
            continue
        H32 = n // W32
        bw = max(1, (W32 + 63) // 64)
        uy, ux = np.mgrid[0:H32, 0:W32]
        got = _addr32(ux, uy, bw)
        if np.array_equal(np.unique(got), need):
            # word address -> linear word index of the upload
            lut = np.full(int(got.max()) + 1, -1, dtype=np.int64)
            lut[got.ravel()] = np.arange(H32 * W32)
            word = lut[tex // unit]
            # nibble within the stored bytes: word*8 + nibble-in-word
            return word * 8 + (tex % unit)
    raise ValueError('no upload rectangle matches a %dx%d %dbpp texture' % (w, h, bpp))


def unswizzle4(data, w, h, H32=None):
    """w*h/2 stored bytes -> w*h indices (rows top first); H32: the upload height its header gives."""
    nib = np.frombuffer(bytes(data[:w * h // 2]), dtype=np.uint8)
    flat = np.empty(nib.size * 2, dtype=np.uint8)
    flat[0::2] = nib & 15
    flat[1::2] = nib >> 4
    return flat[_upload(w, h, 4, H32 or None)].reshape(h, w)


def swizzle4(idx, w, h, H32=None):
    """w*h indices (h, w) -> stored bytes (uploaded as the game uploads that size, or H32 high)."""
    flat = np.zeros(w * h, dtype=np.uint8)
    flat[_upload(w, h, 4, H32 or None).ravel()] = np.asarray(idx, dtype=np.uint8).ravel() & 15
    return (flat[0::2] | (flat[1::2] << 4)).astype(np.uint8).tobytes()


@functools.lru_cache(maxsize=64)
def _map8(w, h):
    """the source byte of each texel of a swizzled 8bpp texture (the closed form texture.py uses; correct for any
    width >= 16)."""
    y, x = np.mgrid[0:h, 0:w]
    block = (y & ~0xF) * w + (x & ~0xF) * 2
    swap = (((y + 2) >> 2) & 1) * 4
    ypos = (((y & ~3) >> 1) + (y & 1)) & 7
    column = ypos * w * 2 + ((x + swap) & 7) * 4
    byte = ((y >> 1) & 1) + ((x >> 2) & 2)
    return block + column + byte


def unswizzle8(data, w, h):
    src = np.frombuffer(bytes(data[:w * h]), dtype=np.uint8)
    if w < 16:
        return src.reshape(h, w)
    m = _map8(w, h)
    return src[np.clip(m, 0, src.size - 1)]


def swizzle8(idx, w, h):
    idx = np.asarray(idx, dtype=np.uint8).reshape(h, w)
    if w < 16:
        return idx.tobytes()
    out = np.zeros(w * h, dtype=np.uint8)
    out[_map8(w, h).ravel()] = idx.ravel()
    return out.tobytes()


def clut_order(n):
    """the PS2 order of a 256-entry palette (bits 3 and 4 of the index swapped); its own inverse."""
    i = np.arange(n)
    if n != 256:
        return i
    return (i & ~0x18) | ((i & 0x08) << 1) | ((i & 0x10) >> 1)
