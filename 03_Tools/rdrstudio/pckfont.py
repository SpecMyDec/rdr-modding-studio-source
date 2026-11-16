"""Filling a `.pck` font with the letters it lacks.

The game draws text from each font's glyph atlases (one per size, see pckscene.py), and a screen's font holds only
the letters its texts used: a title font may have 15. To give it more:

  1. the same typeface at the same size in the game's other screens: a font there is the same one when every
     letter both have is pixel-identical (and has the same metrics) -- those letters are taken as they are;
  2. what is still missing, drawn from a TrueType/OpenType font you choose, scaled so its capitals stand as tall as
     the font's own.

The font is then rebuilt: a new atlas per size (the old glyphs and the new, packed), new metrics, a new char table,
its ASCII map and advance table -- and as none of that fits where the old was, it is APPENDED to the payload (the
file grows; the header's size with it) and the font's pointers moved to it. Nothing else in the screen moves.
"""

from __future__ import annotations

import struct

import numpy as np

from . import ps2gs
from .pck import PckError
from .pckscene import Scene

PRINTABLE = ''.join(chr(c) for c in range(0x20, 0x7f))


def glyphs_of(sc, fid):
    """{size: {char: (alpha (h, w) float, [l, t, a, k])}} and the palette alphas of each size's atlas (kept on the
    scene: the game's other screens are asked for every font filled)."""
    cache = sc.__dict__.setdefault('_glyphs', {})
    if fid not in cache:
        cache[fid] = _glyphs_of(sc, fid)
    return cache[fid]


def _glyphs_of(sc, fid):
    chars = (sc.p.fonts.get(fid) or {}).get('chars', '')
    out, pals = {}, {}
    for b in sc.font_blocks(fid):
        try:
            w, h, rgba, facts = sc.picture(b['info'])
        except (PckError, ValueError, IndexError):
            continue
        if facts['colors'] != 16:
            continue
        a = rgba[..., 3].astype(np.float32) / 255
        gl = {}
        for gi, m in enumerate(b['glyphs'][:len(chars)]):
            x, y, gw, gh, l, t, adv, k = m
            gl[chars[gi]] = (a[y:y + gh, x:x + gw].copy(), [l, t, adv, k])
        out[b['size']] = gl
        pals[b['size']] = facts
    return out, pals


def same_typeface(mine, theirs):
    shared = [c for c in mine if c in theirs and c != ' ' and mine[c][0].size]
    if len(shared) < 3:
        return False
    for c in shared:
        A, B = mine[c], theirs[c]
        if A[0].shape != B[0].shape or A[1] != B[1] or not np.allclose(A[0], B[0], atol=0.02):
            return False
    return True


def same_design(mine, theirs):
    """The same typeface at another size: every letter both have (at least 4) has the same advance and nearly the
    same bearings -- they are kept in 1024ths of the size, so a typeface has the same ones at every size."""
    shared = [c for c in mine if c in theirs and c != ' ' and mine[c][0].size and theirs[c][0].size]
    if len(shared) < 4:
        return False
    for c in shared:
        (_a, (l1, t1, a1, _k1)), (_b, (l2, t2, a2, _k2)) = mine[c], theirs[c]
        if abs(a1 - a2) > 4 or abs(t1 - t2) > 40 or abs(l1 - l2) > 40:
            return False
    return True


def _norm(a, h=32):
    """a glyph's alpha at a fixed height, left in a 32 x 64 box (to compare shapes whatever their size)."""
    from PIL import Image
    im = Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8), 'L')
    w = max(1, min(64, round(im.width * h / im.height)))
    im = im.resize((w, h), Image.LANCZOS)
    out = np.zeros((h, 64), np.float32)
    out[:, :w] = np.asarray(im, np.float32) / 255
    return out


_NORMS = {}


def _fingerprint(A):
    """a glyph at one height, centred and scaled to unit length (cached by the array: sources stay in memory)."""
    k = id(A)
    hit = _NORMS.get(k)
    if hit is None or hit[0] is not A:
        a = _norm(A)
        a = a - a.mean()
        a = a / (np.sqrt((a * a).sum()) + 1e-9)
        hit = (A, a)
        if len(_NORMS) > 200000:
            _NORMS.clear()
        _NORMS[k] = hit
    return hit[1]


def shape_score(A, B):
    """how alike two glyphs look (correlation at one height; 0 when their proportions differ by a quarter)."""
    if not A.size or not B.size:
        return 0.0
    wa, wb = A.shape[1] / A.shape[0], B.shape[1] / B.shape[0]
    if abs(wa - wb) > 0.25 * max(wa, wb):
        return 0.0
    return float((_fingerprint(A) * _fingerprint(B)).sum())


def similar_design(mine, theirs):
    """The same design built at another scale (the game's fonts were not all made at one: the main menu's title font
    and the shops' are one typeface, its letters 1.55x taller at 1.2x the size). -> the scale (their pixels to ours)
    when the letters both have keep one height ratio and look alike at it, else None."""
    shared = [c for c in mine if c in theirs and c != ' ' and mine[c][0].size > 4 and theirs[c][0].size > 4]
    if len(shared) < 5:
        return None
    ratios = np.array([mine[c][0].shape[0] / theirs[c][0].shape[0] for c in shared])
    k = float(np.median(ratios))
    if np.mean(np.abs(ratios / k - 1) <= 0.12) < 0.8:
        return None
    scores = np.array([shape_score(mine[c][0], theirs[c][0]) for c in shared])
    if np.median(scores) < 0.65 or np.mean(scores >= 0.45) < 0.75:
        return None
    return k


def scaled(alpha, k):
    """a glyph's alpha scaled by k (another size of the same typeface)."""
    from PIL import Image
    if not alpha.size or abs(k - 1) < 1e-3:
        return alpha
    h, w = alpha.shape
    nw, nh = max(1, round(w * k)), max(1, round(h * k))
    im = Image.fromarray(alpha.astype(np.float32), 'F').resize((nw, nh), Image.LANCZOS)
    return np.clip(np.asarray(im, dtype=np.float32), 0, 1)


def harvest(target, sources):
    """{size: {char: (alpha, metrics, screen)}}: the letters `target` ({size: glyphs}) lacks that the same typeface
    has elsewhere -- at the same size letter for letter, else from another size (the nearest, larger first) scaled.
    `sources`: [(name, Scene)]."""
    best = {s: {} for s in target}     # size -> char -> (rank, alpha, metrics, name)
    for name, sc in sources:
        for fid, (t, _o) in sc.p.objects.items():
            if t != 5:
                continue
            try:
                theirs, _p = glyphs_of(sc, fid)
            except (PckError, ValueError, IndexError, struct.error):
                continue
            for size2, gl in theirs.items():
                for size, mine in target.items():
                    conv = 1.0                              # their metrics (1024ths of their size) -> ours
                    if size2 == size and same_typeface(mine, gl):
                        rank, k = 0, 1.0
                    elif size2 != size and same_design(mine, gl):
                        # nearer sizes first, and a larger one (scaled down) before a smaller one
                        rank, k = 1 + abs(size2 - size) / size + (0.5 if size2 < size else 0), size / size2
                    else:
                        k = similar_design(mine, gl)
                        if k is None:
                            continue
                        # the same design at another scale: after the exact matches, the least enlarged first
                        rank = 3 + abs(np.log(k)) + (0.5 if k > 1 else 0)
                        conv = (size2 / size) * k
                    for c, g in gl.items():
                        if c in mine or not g[0].size and c != ' ':
                            continue
                        if c not in best[size] or rank < best[size][c][0]:
                            met = g[1] if conv == 1.0 else [round(g[1][0] * conv), round(g[1][1] * conv),
                                                            round(g[1][2] * conv), round(g[1][3] * conv)]
                            best[size][c] = (rank, g[0] if rank == 0 else scaled(g[0], k), met, name)
    return {s: {c: (v[1], v[2], v[3]) for c, v in d.items()} for s, d in best.items()}


def small_caps(ref):
    """Whether the font's lowercase letters are small capitals: its letters with ascenders (b d f h k l t) stand no
    taller than its plain ones (a c e m n o r s u ...)."""
    tall = [g[0].shape[0] for c, g in ref.items() if c in 'bdfhklt' and g[0].size]
    low = [g[0].shape[0] for c, g in ref.items() if c in 'acemnorsuvwxz' and g[0].size]
    return bool(tall and low and max(tall) <= float(np.median(low)) + 1)


def _sized(path, px, target_cap):
    from PIL import ImageFont
    f = ImageFont.truetype(path, max(4, round(px)))
    if target_cap:
        hb = f.getbbox('H', anchor='ls')
        f = ImageFont.truetype(path, max(4, round(px * target_cap / max(1, hb[3] - hb[1]))))
    return f


def render_ttf(path, size_twips, chars, ref, smallcaps=None):
    """Glyphs of `chars` from a TrueType font at the size the font's own capitals give (ref: its glyphs). With
    small capitals (guessed from the font when None) its lowercase letters are drawn as capitals as tall as the
    font's own lowercase."""
    from PIL import Image, ImageDraw
    px = size_twips / 20
    caps = [g[0].shape[0] for c, g in ref.items() if c.isupper() and c not in 'QJ' and g[0].size]
    f = _sized(path, px, float(np.median(caps)) if caps else None)
    if smallcaps is None:
        smallcaps = small_caps(ref)
    fs = None
    if smallcaps:
        low = [g[0].shape[0] for c, g in ref.items() if c in 'acemnorsuvwxz' and g[0].size]
        fs = _sized(path, px, float(np.median(low)) if low else (float(np.median(caps)) * 0.8 if caps else px * 0.55))
    out = {}
    for c0 in chars:
        c, f_ = (c0.upper(), fs) if fs is not None and c0.islower() else (c0, f)
        x0, y0, x1, y1 = f_.getbbox(c, anchor='ls')
        adv = f_.getlength(c)
        w, h = max(0, x1 - x0), max(0, y1 - y0)
        im = Image.new('L', (max(1, w), max(1, h)), 0)
        if w and h:
            ImageDraw.Draw(im).text((-x0, -y0), c, font=f_, fill=255, anchor='ls')
        a = np.asarray(im, dtype=np.float32) / 255 if w and h else np.zeros((0, 0), np.float32)
        out[c0] = (a, [round(x0 * 1024 / px), round(y0 * 1024 / px), round(adv * 1024 / px), 0])
    return out


def pack(cells, width):
    """Shelf-pack (w, h) cells (x on 2-pixel steps, as the metrics store it): -> positions, height."""
    order = sorted(range(len(cells)), key=lambda i: -cells[i][1])
    pos = [None] * len(cells)
    x = y = shelf = 0
    for i in order:
        w, h = cells[i]
        if not w or not h:
            pos[i] = (0, 0)
            continue
        if x + w > width:
            x, y, shelf = 0, y + shelf + 1, 0
        pos[i] = (x, y)
        x += w + 1
        x += x % 2
        shelf = max(shelf, h)
    used = y + shelf
    height = 16
    while height < used:
        height *= 2
    return pos, height


def fill_all(data, sources, ttf=None, want=PRINTABLE, smallcaps=None):
    """Every font of a screen given every letter that can be found for it. -> (new bytes, [(font, added letters)])."""
    sc = Scene(data)
    done = []
    for fid in sorted(f for f, (t, _o) in sc.p.objects.items() if t == 5):
        before = (Scene(data).p.fonts.get(fid) or {}).get('chars', '')
        try:
            data = fill(data, fid, sources, ttf, want, smallcaps)
        except (PckError, ValueError, IndexError, struct.error):
            continue
        after = (Scene(data).p.fonts.get(fid) or {}).get('chars', '')
        done.append((fid, after[len(before):]))
    return data, done


def plan(data, fid, sources, ttf=None, want=PRINTABLE, smallcaps=None):
    """What filling font `fid` would give: per size, the letters it has, those found in other screens (and where),
    those a TTF would draw, those still missing."""
    sc = Scene(data)
    have, _p = glyphs_of(sc, fid)
    if not have:
        raise PckError('font %d has no glyph atlas this can fill (not a 16-colour one)' % fid)
    found = harvest(have, sources)
    out = []
    for size, gl in sorted(have.items()):
        miss = [c for c in want if c not in gl]
        fromscr = {c: found[size][c][2] for c in miss if c in found[size]}
        rest = [c for c in miss if c not in fromscr]
        out.append({'size': size, 'have': ''.join(gl), 'from_screens': fromscr,
                    'from_ttf': ''.join(rest) if ttf else '', 'missing': '' if ttf else ''.join(rest)})
    first = next(iter(have.values()))
    return {'font': fid, 'chars': (sc.p.fonts.get(fid) or {}).get('chars', ''), 'sizes': out,
            'small_caps': small_caps(first)}


def fill(data, fid, sources, ttf=None, want=PRINTABLE, smallcaps=None):
    """The screen with font `fid` filled (new bytes, longer)."""
    sc = Scene(data)
    p, d = sc.p, sc.d
    t, fo = p.objects.get(fid, (None, None))
    if t != 5:
        raise PckError('object %s is not a font' % fid)
    have, pals = glyphs_of(sc, fid)
    if not have:
        raise PckError('font %d has no glyph atlas this can fill' % fid)
    old_chars = p.fonts[fid]['chars']
    found = harvest(have, sources)
    # the letters every size will have: the old, then the new (a glyph index is shared by all sizes)
    add = []
    for c in want:
        if c in old_chars:
            continue
        if all(c in found[s] for s in have) or ttf:
            add.append(c)
    if not add:
        raise PckError('nothing to add: no other screen has more letters of this typeface' + ('' if ttf else ', and no TrueType font was given'))
    chars = old_chars + ''.join(add)
    blocks = sc.font_blocks(fid)

    def append(raw):
        nonlocal d
        while len(d) % 16:
            d.append(0xCD)
        at = len(d)
        d += raw
        return p.base + at

    new_ptrs = {}
    first_adv = {}      # the added letters' advances at the first size, for a full font's advance table
    for b in blocks:
        size = b['size']
        if size not in have:
            raise PckError('size %d of font %d could not be read' % (size, fid))
        gl = dict(have[size])
        ttf_g = render_ttf(ttf, size, [c for c in add if c not in found[size]], gl, smallcaps) if ttf else {}
        for c in add:
            if c in found[size]:
                gl[c] = (found[size][c][0], found[size][c][1])
            elif c in ttf_g:
                gl[c] = ttf_g[c]
        if b is blocks[0]:
            first_adv.update({c: gl[c][1][2] for c in add})
        facts = pals[size]
        # the palette's alphas: each new pixel takes the nearest
        pal_a = np.frombuffer(bytes(d[facts['pal'] + 16:facts['pal'] + 16 + 64]), dtype=np.uint8).reshape(16, 4)[:, 3]
        pal_a = np.minimum(255, pal_a.astype(np.int32) * 255 // 128) / 255
        cells = [gl[c][0].shape[::-1] if gl[c][0].size else (0, 0) for c in chars]
        # an atlas of a shape the game's own have (as wide as high, or twice): wider until it is not taller
        width = 64
        pos, height = pack(cells, width)
        while height > width and width < 512:
            width *= 2
            pos, height = pack(cells, width)
        if width > 2 * height:
            height = width // 2
        idx = np.zeros((height, width), dtype=np.uint8)
        metrics = bytearray()
        for gi, c in enumerate(chars):
            a, (l, tp, adv, k) = gl[c]
            x, y = pos[gi]
            if a.size:
                q = np.abs(a[..., None] - pal_a[None, None, :]).argmin(-1).astype(np.uint8)
                idx[y:y + a.shape[0], x:x + a.shape[1]] = q
            metrics += struct.pack('<BBBBhhhh', x // 2, y, a.shape[1] if a.size else 0, a.shape[0] if a.size else 0,
                                   int(l), int(tp), int(adv), int(k))
        # the atlas: copies of the old info, palette and texture header, with the new size
        info = b['info']
        tex = sc.at(sc.u32(info + 0x94))
        pal = facts['pal']
        pixels = ps2gs.swizzle4(idx, width, height)
        pix_ptr = append(pixels)
        pal_ptr = append(bytes(d[pal:pal + 16 + 64]))
        th = bytearray(d[tex:tex + 0x40])
        struct.pack_into('<I', th, 8, pix_ptr)
        from .pckscene import set_tex_header
        set_tex_header(th, width, height, 4)        # its size and upload fields (the old atlas's were copied)
        tex_ptr = append(bytes(th))
        ib = bytearray(d[info:info + 0xb0])
        ow, oh = sc.u16(info + 0x88), sc.u16(info + 0x8a)
        lw, lh = ow.bit_length() - 1, oh.bit_length() - 1
        for q in range(0, 0x80, 8):
            v = struct.unpack_from('<Q', ib, q)[0]
            if ((v >> 20) & 0x3f) == 0x14 and ((v >> 26) & 0xf) == lw and ((v >> 30) & 0xf) == lh:
                v &= ~((0x3f << 14) | (0xf << 26) | (0xf << 30))
                v |= (max(1, width // 64) << 14) | ((width.bit_length() - 1) << 26) | ((height.bit_length() - 1) << 30)
                struct.pack_into('<Q', ib, q, v)
        struct.pack_into('<HH', ib, 0x88, width, height)
        struct.pack_into('<II', ib, 0x90, pal_ptr, tex_ptr)
        info_ptr = append(bytes(ib))
        met_ptr = append(bytes(metrics))
        new_ptrs[b['at']] = (info_ptr, met_ptr, width, height)
    # the font's tables: chars, the (unused) per-glyph table, advances
    char_ptr = append(struct.pack('<%dH' % len(chars), *[ord(c) for c in chars]))
    old_p1 = sc.at(sc.u32(fo + 12))
    p1 = bytearray()
    for gi in range(len(chars)):
        src = old_p1 + 8 * min(gi, len(old_chars) - 1) if old_p1 is not None else None
        p1 += bytes(d[src:src + 8]) if src is not None else b'\0' * 8
    p1_ptr = append(bytes(p1)) if old_p1 is not None else sc.u32(fo + 12)
    adv_ptr = sc.u32(fo + 20)
    if sc.at(adv_ptr) is not None:
        old_adv = [sc.u16(sc.at(adv_ptr) + 2 * k) for k in range(len(old_chars))]
        new_adv = old_adv + [max(0, int(first_adv.get(c, 0))) for c in add]
        adv_ptr = append(struct.pack('<%dH' % len(new_adv), *new_adv))
    # point the font and its size blocks at the new
    struct.pack_into('<H', d, fo + 8, len(chars))
    struct.pack_into('<III', d, fo + 12, p1_ptr, char_ptr, adv_ptr)
    amap = bytearray(d[fo + 0x18:fo + 0x98])
    for gi, c in enumerate(chars):
        if ord(c) < 128:
            amap[ord(c)] = gi if gi < 256 else 0
    d[fo + 0x18:fo + 0x98] = amap
    for so, (info_ptr, met_ptr, w, h) in new_ptrs.items():
        struct.pack_into('<IffI', d, so, info_ptr, 1 / w, 1 / h, met_ptr)
        struct.pack_into('<H', d, so + 18, len(chars))
    while len(d) % 16:
        d.append(0xCD)
    head = bytearray(p.head)
    struct.pack_into('<I', head, 12, len(d))
    return bytes(head) + bytes(d)

