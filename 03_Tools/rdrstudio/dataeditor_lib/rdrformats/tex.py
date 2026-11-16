"""Angel Studios textures: Red Dead Revolver .tex (PS2) and .xtex (Xbox), as vollkorn2's Tex2Png / Png2Tex read them.

    u16 width, u16 height, u16 type, u16 mip levels, 6 flag bytes (most Xbox files: 01 00 00 00 00 00)
    palette types: the palette, BGRA, 256 entries (types 1, 14) or 16 (types 15, 16)
    then each mip level, largest first, rows top first, width * height * bits per pixel / 8 bytes (no rounding up to
    whole DXT blocks: the 2x2 and 1x1 levels of a full DXT chain hold part blocks; measured over the shipped files)

Types: 1 P8, 6 RGBA4444, 8 / 11 A8, 14 P8 with alpha, 15 P4, 16 P4 with alpha, 17 RGB888, 18 RGBA8888,
22 DXT1, 24 DXT3, 26 DXT5. Shipped Xbox textures are mostly DXT1 (opaque) and DXT5 (alpha); the PS2 has no DXT, so its
textures are palettes.

Images here are numpy arrays, height x width x 4 RGBA uint8, rows in the file's order. The file's first row is the
picture's bottom (a flag texture has its stars in the last rows), which is why Tex2Png's PNGs come out upside down and
why the model importers flip v: shown with the first row at the top, the picture lines up with v -> 1 - v.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

import numpy as np

P8, RGBA4444, A8_8, A8, PA8, P4, PA4, RGB888, RGBA8888, DXT1, DXT3, DXT5 = 1, 6, 8, 11, 14, 15, 16, 17, 18, 22, 24, 26
BITS = {P8: 8, RGBA4444: 16, A8_8: 8, A8: 8, PA8: 8, P4: 4, PA4: 4, RGB888: 24, RGBA8888: 32, DXT1: 4, DXT3: 8,
        DXT5: 8}
PALETTE = {P8: 256, PA8: 256, P4: 16, PA4: 16}
DEFAULT_FLAGS = bytes([1, 0, 0, 0, 0, 0])
HEADER = 14


class TextureError(ValueError):
    pass


@dataclass
class Texture:
    width: int
    height: int
    type: int
    flags: bytes = DEFAULT_FLAGS
    mips: list = field(default_factory=list)  # decoded levels, RGBA arrays

    @property
    def image(self):
        return self.mips[0]


def level_size(t, w, h):
    return w * h * BITS[t] // 8


def level_dims(w, h, n):
    return [(max(1, w >> i), max(1, h >> i)) for i in range(n)]


# ---------------------------------------------------------------------------------------------------------------
# decode

def _565(c):
    c = c.astype(np.uint32)
    r = (c >> 11) & 31
    g = (c >> 5) & 63
    b = c & 31
    return np.stack([(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)], -1).astype(np.int32)


def _dxt_colors(blocks, dxt1):
    """blocks: n x 8 bytes. The four colors of each block (n x 4 x 4, RGBA)."""
    c0 = blocks[:, 0].astype(np.uint16) | (blocks[:, 1].astype(np.uint16) << 8)
    c1 = blocks[:, 2].astype(np.uint16) | (blocks[:, 3].astype(np.uint16) << 8)
    a, b = _565(c0), _565(c1)
    four = (c0 > c1) | (not dxt1)
    out = np.zeros((len(blocks), 4, 4), np.int32)
    out[:, 0, :3], out[:, 1, :3] = a, b
    out[:, 2, :3] = np.where(four[:, None], (2 * a + b) // 3, (a + b) // 2)
    out[:, 3, :3] = np.where(four[:, None], (a + 2 * b) // 3, 0)
    out[:, :, 3] = 255
    if dxt1:
        out[~four, 3, 3] = 0
    return out


def _bits(blocks, start, count, width):
    """Per block, count indices of width bits from byte start, little endian."""
    v = np.zeros(len(blocks), np.uint64)
    nbytes = (count * width + 7) // 8
    for i in range(nbytes):
        v |= blocks[:, start + i].astype(np.uint64) << np.uint64(8 * i)
    return np.stack([(v >> np.uint64(width * k)) & np.uint64((1 << width) - 1) for k in range(count)], -1).astype(
        np.int64)


def _blocks_to_image(px, w, h):
    """px: n x 16 x 4 by block, blocks row major; cropped to w x h."""
    bw, bh = max(1, (w + 3) // 4), max(1, (h + 3) // 4)
    img = px.reshape(bh, bw, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)
    return img[:h, :w].astype(np.uint8)


def _decode_dxt(raw, t, w, h):
    bw, bh = max(1, (w + 3) // 4), max(1, (h + 3) // 4)
    step = 8 if t == DXT1 else 16
    need = bw * bh * step
    raw = raw + bytes(max(0, need - len(raw)))  # the part blocks of the smallest levels
    blocks = np.frombuffer(raw[:need], np.uint8).reshape(-1, step)
    color = blocks[:, -8:]
    pal = _dxt_colors(color, t == DXT1)
    idx = _bits(color, 4, 16, 2)
    px = np.take_along_axis(pal, idx[:, :, None].repeat(4, 2), 1)
    if t == DXT3:
        px[:, :, 3] = _bits(blocks, 0, 16, 4) * 17
    elif t == DXT5:
        a0, a1 = blocks[:, 0].astype(np.int32), blocks[:, 1].astype(np.int32)
        ai = _bits(blocks, 2, 16, 3)
        eight = a0 > a1
        tab = np.zeros((len(blocks), 8), np.int32)
        tab[:, 0], tab[:, 1] = a0, a1
        for k in range(1, 7):
            tab[:, k + 1] = np.where(eight, ((7 - k) * a0 + k * a1) // 7, 0)
        for k in range(1, 5):
            tab[~eight, k + 1] = (((5 - k) * a0 + k * a1) // 5)[~eight]
        tab[~eight, 6], tab[~eight, 7] = 0, 255
        px[:, :, 3] = np.take_along_axis(tab, ai, 1)
    return _blocks_to_image(px, w, h)


def _decode_level(raw, t, w, h, palette):
    if t in (DXT1, DXT3, DXT5):
        return _decode_dxt(raw, t, w, h)
    n = w * h
    if t in PALETTE:
        if t in (P4, PA4):
            b = np.frombuffer(raw[:(n + 1) // 2], np.uint8)
            if len(b) < (n + 1) // 2:  # a 1x1 level of a 4 bit texture is stored as 0 bytes (w*h*4/8 rounded down)
                b = np.concatenate([b, np.zeros((n + 1) // 2 - len(b), np.uint8)])
            ix = np.stack([b & 15, b >> 4], -1).reshape(-1)[:n]
        else:
            ix = np.frombuffer(raw[:n], np.uint8)
        return palette[ix].reshape(h, w, 4)
    if t == RGB888:
        rgb = np.frombuffer(raw[:n * 3], np.uint8).reshape(h, w, 3)
        return np.concatenate([rgb, np.full((h, w, 1), 255, np.uint8)], -1)
    if t == RGBA8888:
        return np.frombuffer(raw[:n * 4], np.uint8).reshape(h, w, 4).copy()
    if t == RGBA4444:
        b = np.frombuffer(raw[:n * 2], np.uint8).reshape(h, w, 2)
        out = np.stack([b[..., 0] >> 4, b[..., 0] & 15, b[..., 1] >> 4, b[..., 1] & 15], -1) * 17
        return out.astype(np.uint8)
    if t in (A8, A8_8):
        a = np.frombuffer(raw[:n], np.uint8).reshape(h, w)
        return np.stack([np.full_like(a, 255)] * 3 + [a], -1)
    raise TextureError(f'texture type {t} is not supported')


def read(data: bytes) -> Texture:
    if len(data) < HEADER:
        raise TextureError('too short for a texture')
    w, h, t, n = struct.unpack_from('<4H', data)
    if t not in BITS:
        raise TextureError(f'texture type {t} is not supported')
    tex = Texture(w, h, t, bytes(data[8:14]))
    p = HEADER
    palette = None
    if t in PALETTE:
        k = PALETTE[t]
        bgra = np.frombuffer(data[p:p + 4 * k], np.uint8).reshape(k, 4)
        palette = bgra[:, [2, 1, 0, 3]].copy()
        if t in (P8, P4):
            palette[:, 3] = 255
        p += 4 * k
    for lw, lh in level_dims(w, h, max(n, 1)):
        size = level_size(t, lw, lh)
        tex.mips.append(_decode_level(data[p:p + size], t, lw, lh, palette))
        p += size
    return tex


# ---------------------------------------------------------------------------------------------------------------
# encode

def make_mips(img, smallest=4, limit=10):
    """The image and its halvings (box filter) down to smallest pixels on the short side."""
    levels = [img]
    while len(levels) < limit:
        cur = levels[-1]
        h, w = cur.shape[:2]
        if min(h, w) // 2 < smallest:
            break
        f = cur[:h // 2 * 2, :w // 2 * 2].astype(np.uint16)
        nxt = (f[0::2, 0::2] + f[1::2, 0::2] + f[0::2, 1::2] + f[1::2, 1::2] + 2) // 4
        levels.append(nxt.astype(np.uint8))
    return levels


def _to_565(rgb):
    rgb = rgb.astype(np.int32)
    return ((rgb[..., 0] * 31 + 127) // 255 << 11) | ((rgb[..., 1] * 63 + 127) // 255 << 5) | \
        ((rgb[..., 2] * 31 + 127) // 255)


def _image_blocks(img):
    h, w = img.shape[:2]
    bh, bw = (h + 3) // 4, (w + 3) // 4
    pad = np.zeros((bh * 4, bw * 4, 4), np.uint8)
    pad[:h, :w] = img
    if h < bh * 4:
        pad[h:, :w] = img[-1:]  # repeat the edge so a part block does not pull toward black
    if w < bw * 4:
        pad[:, w:] = pad[:, w - 1:w]
    return pad.reshape(bh, 4, bw, 4, 4).transpose(0, 2, 1, 3, 4).reshape(-1, 16, 4)


def _encode_color(px, dxt1):
    """px: n x 16 x 4. Endpoints along each block's longest color axis, then the nearest of the four colors."""
    rgb = px[:, :, :3].astype(np.float32)
    mean = rgb.mean(1, keepdims=True)
    centred = rgb - mean
    cov = np.einsum('nki,nkj->nij', centred, centred)
    axis = np.ones((len(px), 3), np.float32)
    for _ in range(8):  # power iteration for the main axis
        axis = np.einsum('nij,nj->ni', cov, axis)
        axis /= np.linalg.norm(axis, axis=1, keepdims=True) + 1e-6
    proj = np.einsum('nki,ni->nk', centred, axis)
    lo = mean[:, 0] + axis * proj.min(1, keepdims=True)
    hi = mean[:, 0] + axis * proj.max(1, keepdims=True)
    c0 = _to_565(np.clip(hi, 0, 255))
    c1 = _to_565(np.clip(lo, 0, 255))
    swap = c0 < c1
    c0, c1 = np.where(swap, c1, c0), np.where(swap, c0, c1)
    same = c0 == c1
    # equal endpoints would switch DXT1 into its three color mode; nudge one so it stays four colors
    c1 = np.where(same & (c0 > 0), c0 - 1, c1)
    c0 = np.where(same & (c0 == 0), 1, c0)
    blk = np.zeros((len(px), 8), np.uint8)
    blk[:, 0], blk[:, 1] = c0 & 255, c0 >> 8
    blk[:, 2], blk[:, 3] = c1 & 255, c1 >> 8
    pal = _dxt_colors(blk, True)[:, :, :3].astype(np.float32)
    d = ((rgb[:, :, None, :] - pal[:, None, :, :]) ** 2).sum(-1)
    idx = d.argmin(2).astype(np.uint32)
    bits = np.zeros(len(px), np.uint32)
    for k in range(16):
        bits |= idx[:, k] << np.uint32(2 * k)
    for i in range(4):
        blk[:, 4 + i] = (bits >> np.uint32(8 * i)) & 255
    return blk


def _encode_alpha(px):
    a = px[:, :, 3].astype(np.int32)
    a0, a1 = a.max(1), a.min(1)
    a1 = np.where(a0 == a1, np.maximum(a0 - 1, 0), a1)
    a0 = np.where(a0 == a1, a1 + 1, a0)
    tab = np.zeros((len(px), 8), np.int32)
    tab[:, 0], tab[:, 1] = a0, a1
    for k in range(1, 7):
        tab[:, k + 1] = ((7 - k) * a0 + k * a1) // 7
    idx = np.abs(a[:, :, None] - tab[:, None, :]).argmin(2).astype(np.uint64)
    bits = np.zeros(len(px), np.uint64)
    for k in range(16):
        bits |= idx[:, k] << np.uint64(3 * k)
    blk = np.zeros((len(px), 8), np.uint8)
    blk[:, 0], blk[:, 1] = a0, a1
    for i in range(6):
        blk[:, 2 + i] = (bits >> np.uint64(8 * i)) & np.uint64(255)
    return blk


def encode_dxt(img, t):
    px = _image_blocks(img)
    color = _encode_color(px, t == DXT1)
    if t == DXT1:
        return color.tobytes()
    return np.concatenate([_encode_alpha(px), color], 1).tobytes()


def has_alpha(img, threshold=250):
    return bool((img[..., 3] < threshold).any())


def quantize(img, colors=256):
    """A palette of at most colors entries (median cut over the image's colors) and each pixel's index."""
    flat = img.reshape(-1, 4)
    uniq, inverse, counts = np.unique(flat, axis=0, return_inverse=True, return_counts=True)
    inverse = inverse.reshape(-1)
    if len(uniq) <= colors:
        pal = np.zeros((colors, 4), np.uint8)
        pal[:len(uniq)] = uniq
        return pal, inverse.astype(np.uint8 if colors <= 256 else np.uint16).reshape(img.shape[:2])
    boxes = [np.arange(len(uniq))]
    while len(boxes) < colors:
        # split the box with the widest weighted channel range
        best, bi = -1, 0
        for i, b in enumerate(boxes):
            if len(b) < 2:
                continue
            rng = (uniq[b].max(0).astype(int) - uniq[b].min(0)).max() * np.log1p(counts[b].sum())
            if rng > best:
                best, bi = rng, i
        if best <= 0:
            break
        b = boxes.pop(bi)
        ch = int(np.argmax(uniq[b].max(0).astype(int) - uniq[b].min(0)))
        order = b[np.argsort(uniq[b, ch], kind='stable')]
        cum = np.cumsum(counts[order])
        cut = int(np.searchsorted(cum, cum[-1] / 2)) + 1
        cut = min(max(cut, 1), len(order) - 1)
        boxes += [order[:cut], order[cut:]]
    pal = np.zeros((colors, 4), np.uint8)
    for i, b in enumerate(boxes):
        w = counts[b][:, None].astype(np.float64)
        pal[i] = np.round((uniq[b] * w).sum(0) / w.sum())
    return pal, nearest(img, pal[:len(boxes)])


def nearest(img, pal):
    flat = img.reshape(-1, 4).astype(np.int32)
    out = np.empty(len(flat), np.uint8)
    p = pal.astype(np.int32)
    for s in range(0, len(flat), 65536):
        d = ((flat[s:s + 65536, None, :] - p[None, :, :]) ** 2).sum(-1)
        out[s:s + 65536] = d.argmin(1)
    return out.reshape(img.shape[:2])


def encode(img, t=None, mips=True, flags=DEFAULT_FLAGS, platform='xbox') -> bytes:
    """A texture file from an RGBA image. t None picks as the shipped files do: Xbox DXT1, or DXT5 with alpha; PS2
    an 8 bit palette (P8, or PA8 with alpha)."""
    img = np.ascontiguousarray(img, np.uint8)
    if img.ndim != 3 or img.shape[2] != 4:
        raise TextureError('the image must be height x width x RGBA')
    h, w = img.shape[:2]
    if w > 0xFFFF or h > 0xFFFF:
        raise TextureError('too large')
    alpha = has_alpha(img)
    if t is None:
        t = (DXT5 if alpha else DXT1) if platform == 'xbox' else (PA8 if alpha else P8)
    dxt = t in (DXT1, DXT3, DXT5)
    if dxt and (w % 4 or h % 4):
        t, dxt = RGBA8888, False  # DXT levels are whole 4x4 blocks: sides that are not cannot be stored as DXT
    levels = make_mips(img, 4 if dxt else 1) if mips else [img]
    if dxt:
        # every level must stay whole blocks for its size to follow the layout rule
        keep = 1
        while keep < len(levels) and all(s % 4 == 0 for s in levels[keep].shape[:2]):
            keep += 1
        levels = levels[:keep]
    out = bytearray(struct.pack('<4H', w, h, t, len(levels)) + bytes(flags[:6]).ljust(6, b'\0'))
    if t in (DXT1, DXT5):
        for lv in levels:
            out += encode_dxt(lv, t)
    elif t in (P8, PA8, P4, PA4):
        k = PALETTE[t]
        pal, _ = quantize(img, k)
        if t in (P8, P4):
            pal[:, 3] = 255
        out += pal[:, [2, 1, 0, 3]].tobytes()
        for lv in levels:
            ix = nearest(lv, pal)
            if k == 16:
                flat = ix.reshape(-1)
                flat = flat[:len(flat) // 2 * 2]  # as the game sizes levels: w*h*4/8 rounded down (1x1: 0 bytes)
                out += ((flat[0::2] & 15) | (flat[1::2] << 4)).astype(np.uint8).tobytes()
            else:
                out += ix.astype(np.uint8).tobytes()
    elif t == RGBA8888:
        for lv in levels:
            out += lv.tobytes()
    elif t == RGB888:
        for lv in levels:
            out += lv[..., :3].tobytes()
    else:
        raise TextureError(f'writing type {t} is not supported')
    return bytes(out)


def load(path) -> Texture:
    with open(path, 'rb') as f:
        return read(f.read())
