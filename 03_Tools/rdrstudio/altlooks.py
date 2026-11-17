"""Alternate looks for Showdown players 2-4 (swat.dat <code>02.tex .. <code>04.tex; per texture <code>02_<k>.tex).

NOTE (corrected 2026-11-16): the game draws the WHOLE model with <code>0N.tex (see codemods_alttex), not just its first
texture. A model with one texture uses <code>0N.tex as the game does; a model with several uses the per-texture
alternates <code>0N_<k>.tex the alttex code mod loads, k = the texture's place in the order the model's parts first use
them (`part_order`), all of 0..K-1 present (the mod stops at the first missing one).
The notes below were written before that was known.

How the game does it (read from the PS2 ELF and the disc, 2026-11-16):
  * when a Showdown creature is made for player slot n > 0 (0x354920 -> 0x185340), the game formats
    "%s%02d" % (creature name, n + 1) -- "gbw02", "dad03" -- and loads that texture (0x3B0728), which is drawn in
    place of the model's FIRST texture (the first TEX0 in the <code>_lods.rsc bundle, in chunk order). Player 1 keeps
    the bundle's own texture.
  * proof it is the first texture, not one matched by name: mgwk's only texture is named "gwk01" and its alternates
    are mgwk02..04; msev's bundle holds msev01, sev01, gta01 and its alternates replace msev01. (The .rnt manifest is
    a hash table -- msev's first entry is gta01 -- so its order is not the one that counts.)
  * the game's alternates: loose 256-colour .tex (format 1, one level), the size of the texture they stand in for,
    rows in the same order as the bundle's.
A character with several textures (a custom one exported from Blender: Vest, Shoes, Head ...) can only vary one of
them; `make_first` moves the chosen one into the first slot. With no <code>0N.tex the game draws the bundle's.
"""
import struct

from . import rsc as R, texture as TX

PLAYERS = (2, 3, 4)


def alt_name(code, n):
    return '%s%02d.tex' % (code, n)


def textures(bundle):
    """[{name, w, h, tex, mip, pal}] in chunk order (the first is the one the alternates replace)."""
    r = R.Rsc(bundle)
    out = []
    for ti, info, mip, pal in r.texture_table():
        out.append({'name': info.get('name') or ('texture %d' % ti), 'w': info.get('width'), 'h': info.get('height'),
                    'tex': ti, 'mip': mip.index, 'pal': pal.index})
    return out


def texture_rgba(bundle, t):
    """(w, h, RGBA bytes) of a bundle texture, rows as the game's alternates store them."""
    r = R.Rsc(bundle)
    got = TX.decode(r.raw(t['mip']), TX.parse_palette(r.raw(t['pal'])))
    if not got:
        raise ValueError('%s could not be read' % t['name'])
    return got                                    # parse_palette has already made the alpha full range


def make_first(bundle, rnt, name):
    """The bundle (and its .rnt) with the texture called `name` in the first texture slot: the pixels, palette and name
    of the two textures exchanged, every model part that drew either pointed at its new slot, the manifest's two
    entries swapped. -> (bundle, rnt); the same when it is already first."""
    ts = textures(bundle)
    if not ts:
        raise ValueError('the bundle has no textures')
    want = next((t for t in ts if t['name'].lower() == (name or '').lower()), None)
    if want is None:
        raise ValueError('the bundle has no texture called %s' % name)
    a, b = ts[0], want
    if a['tex'] == b['tex']:
        return bundle, rnt
    r = R.Rsc(bundle)
    for k in ('mip', 'pal'):
        if r.chunks[a[k]].size != r.chunks[b[k]].size:
            raise ValueError('%s and %s are not the same size (%dx%d, %dx%d): make them the same size in Blender '
                             'and export again' % (a['name'], b['name'], a['w'], a['h'], b['w'], b['h']))
    d = bytearray(bundle)

    def span(i):
        c = r.chunks[i]
        return c.offset, c.offset + c.size

    for k in ('mip', 'pal'):
        (s1, e1), (s2, e2) = span(a[k]), span(b[k])
        d[s1:e1], d[s2:e2] = bytes(bundle[s2:e2]), bytes(bundle[s1:e1])
    # the names (0xA0..0xC0 of each TEX0; everything else in the two blocks but their own slot numbers is alike)
    (s1, e1), (s2, e2) = span(a['tex']), span(b['tex'])
    if e1 - s1 >= 0xC0 and e2 - s2 >= 0xC0:
        n1, n2 = bytes(bundle[s1 + 0xA0:s1 + 0xC0]), bytes(bundle[s2 + 0xA0:s2 + 0xC0])
        d[s1 + 0xA0:s1 + 0xC0], d[s2 + 0xA0:s2 + 0xC0] = n2, n1
    # the model parts: one-based TEX0 references
    ra, rb = a['tex'] + 1, b['tex'] + 1
    for c in r.chunks:
        if c.tag != 'MOD0':
            continue
        offs, refs = R._mod0_refs(r.raw(c.index))
        if not offs:
            continue
        for off, ref in zip(offs, refs):
            if ref in (ra, rb):
                struct.pack_into('<I', d, c.offset + off, rb if ref == ra else ra)
    out_rnt = rnt
    if rnt and rnt[:4] == b'RNT0':
        m = bytearray(rnt)
        i = 0
        while True:
            i = rnt.find(b'TEX0', i)
            if i < 0 or i + 8 > len(rnt):
                break
            v = struct.unpack_from('<I', rnt, i + 4)[0]
            if v in (ra, rb):
                struct.pack_into('<I', m, i + 4, rb if v == ra else ra)
            i += 4
        out_rnt = bytes(m)
    new = bytes(d)
    problems = R.check_bundle(new, 'alternate looks')
    if problems:
        raise AssertionError('; '.join(problems))
    return new, out_rnt


def encode_alt(rgba, w, h, tw, th):
    """An alternate .tex (the game's form: 256 colours, one level; with see-through pixels 256 colours + alpha) from
    an RGBA picture, made the size of the texture it stands in for."""
    import numpy as np
    from PIL import Image
    import rdrtex
    im = Image.frombytes('RGBA', (w, h), bytes(rgba))
    if (w, h) != (tw, th):
        im = im.resize((tw, th), Image.LANCZOS)
    a = np.asarray(im)
    kind = rdrtex.PA8 if (a[..., 3] < 250).any() else rdrtex.P8
    return rdrtex.encode(np.ascontiguousarray(a), kind, mips=False, flags=rdrtex.DEFAULT_FLAGS, platform='ps2')


def tinted(rgba, w, h, hue, sat=1.0, light=1.0):
    """The picture with its hue turned by `hue` degrees (and saturation / lightness scaled): a quick alternate."""
    import numpy as np
    from PIL import Image
    im = Image.frombytes('RGBA', (w, h), bytes(rgba))
    alpha = im.getchannel('A')
    hsv = np.asarray(im.convert('RGB').convert('HSV')).astype(np.float32)
    hsv[..., 0] = (hsv[..., 0] + hue * 255.0 / 360.0) % 256
    hsv[..., 1] = np.clip(hsv[..., 1] * sat, 0, 255)
    hsv[..., 2] = np.clip(hsv[..., 2] * light, 0, 255)
    out = Image.fromarray(hsv.astype(np.uint8), 'HSV').convert('RGB')
    out.putalpha(alpha)
    return out.tobytes()


ALT_MAX = 128     # per-texture alternates at most 128 x 128: video memory is 4 MB (three players' worth of 256 x 256
                  # ones ran past it, 2026-11-16)


def alt_size(w, h):
    """The size a per-texture alternate is written at: the texture's, halved until both sides are <= ALT_MAX."""
    while w > ALT_MAX or h > ALT_MAX:
        w, h = max(1, w // 2), max(1, h // 2)
    return w, h


def alt_name_k(code, n, k):
    return '%s%02d_%d.tex' % (code, n, k)


def part_order(bundle):
    """The textures (as `textures` gives them) in the order the model's parts first use them -- the order the alttex
    code mod numbers them in -- then any no part uses."""
    r = R.Rsc(bundle)
    ts = textures(bundle)
    by_tex = {t['tex']: t for t in ts}
    order = []
    for c in r.chunks:
        if c.tag != 'MOD0':
            continue
        offs, refs = R._mod0_refs(r.raw(c.index))
        if not offs:
            continue
        for ref in refs:
            t = by_tex.get(ref - 1)
            if t is not None and t not in order:
                order.append(t)
        if order:
            break                      # the first model (lod 01): the one drawn
    return order + [t for t in ts if t not in order]


def shrink_bundle(bundle, max_side=ALT_MAX):
    """The bundle with every texture bigger than max_side halved until it fits (pixels, palette and the TEX0's size
    rewritten in place: chunk numbers, the model's references and the .rnt stay as they are). -> bytes (the same when
    none is bigger). Video memory is 4 MB; a custom Showdown character's 256 x 256 textures are 64 KB each."""
    import numpy as np
    from PIL import Image
    from .dataeditor_lib.rdrformats import ps2rsc
    out = bundle
    for t in textures(bundle):
        w, h = t['w'] or 0, t['h'] or 0
        tw, th = w, h
        while tw > max_side or th > max_side:
            tw, th = max(16, tw // 2), max(16, th // 2)
        if (tw, th) == (w, h):
            continue
        cw, ch, px = texture_rgba(out, t)
        im = Image.frombytes('RGBA', (cw, ch), bytes(px)).resize((tw, th), Image.LANCZOS)
        chunks = ps2rsc.texture_chunks(t['name'], np.asarray(im), t['pal'] + 1, t['mip'] + 1)
        new = dict(chunks)
        out = R.replace_chunk(out, t['mip'], new['MIP0'], 'shrink')
        out = R.replace_chunk(out, t['pal'], new['PAL0'], 'shrink')
        out = R.replace_chunk(out, t['tex'], new['TEX0'], 'shrink')
    return out
