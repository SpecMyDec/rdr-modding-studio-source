"""A `.pck` Flash screen as a scene to draw and edit: every object, the timelines, the pictures.

(The container and the text are in pck.py.) What the Studio reads here, checked on the game's 78 screens against the
converted .swf files:

    bitmap (4)     +8 info ptr, u16 w +12, h +14. The info (bmpInfo1): a GS packet (0x80), u16 texture w/h +0x88,
                   palette ptr +0x90, texture ptr +0x94. Palette: u16 count +2, RGBA from +16 (alpha 0x80 =
                   opaque; a 256-colour one in the PS2 order, index bits 3/4 swapped). Texture: pixels ptr +8,
                   storage +0x30 (1 = swizzled, 5/6 = linear). 256 colours: PSMT8; 16 colours: PSMT4 (ps2gs.py).
    shape (1)      fill table +8 (36 bytes each: u8 SWF fill type, RGBA +4, f32 matrix +8 mapping pixels to the
                   bitmap's 0..1 square, bitmap ptr +32), stroke table +12 (8 bytes: u16 width in twips, RGBA +4),
                   display list +16, u16 fill count +20, stroke count +22. The display list, u16 op first:
                   4 fill style (1-based), 5 stroke style, 2 fill a convex polygon, 1 stroke a line (u16 count, s16
                   x/y twips each), 0 end.
    font (5)       as pck.py, and: an ASCII -> glyph map (128 bytes) +0x18, u32 size count +0x98 and that many
                   pointers from +0x9c to a size block: info ptr of the glyph atlas (a bitmap info as above), f32
                   1/w, 1/h, glyph metrics ptr +12, u16 size (twips) +16, u16 glyph count +18. A glyph (12 bytes):
                   u8 atlas x (in 2-pixel units), y, w, h, s16 left, top (1/1024 of the size), advance, kerning.
                   THE GAME DRAWS TEXT FROM THESE ATLASES: the font's "shape" table points at nothing.
    sprite (2)     u16 frame count +8, frames ptr +12 (8 bytes each: 0, commands ptr). The root (payload 0) the same.
    command        u8 type (0 place, 1 place with a name/events, 2 remove, 3 action), u8 flags, next ptr +4,
                   u16 depth +12, u16 character +14 (0xffff: the one at that depth), matrix ptr +16, colour
                   transform ptr +20, name ptr +36 (type 1). The matrix is packed: flags bit 0 -> s16 sx, sy (8.8),
                   bit 1 -> s16 r0, r1 (8.8), then s16 x, y (twips). Colour transform: RGBA multiply (0x40 = 1),
                   RGBA add (signed).
    button (3)     u8 record count +9, records ptr +12 (40 bytes each: u8 states, u8 depth, f32 matrix +4, colour
                   transform +28, object ptr +36), actions ptr +16.

Units: twips (1/20 pixel) everywhere but a fill's matrix (pixels).
"""

from __future__ import annotations

import base64
import io
import struct

import numpy as np

from . import ps2gs
from .pck import Pck, PckError

TWIPS = 20.0


class Scene:
    def __init__(self, data: bytes):
        self.p = Pck(data)
        self.d = self.p.d
        self.by_ptr = {self.p.base + o: i for i, (_t, o) in self.p.objects.items()}

    # --------------------------------------------------------------- small readers
    def u8(self, o):
        return self.d[o]

    def u16(self, o):
        return struct.unpack_from('<H', self.d, o)[0]

    def s16(self, o):
        return struct.unpack_from('<h', self.d, o)[0]

    def u32(self, o):
        return struct.unpack_from('<I', self.d, o)[0]

    def f32s(self, o, n):
        return list(struct.unpack_from('<%df' % n, self.d, o))

    def at(self, ptr):
        return ptr - self.p.base if ptr and self.p.inside(ptr) else None

    def oid(self, ptr):
        return self.by_ptr.get(ptr)

    # --------------------------------------------------------------- pictures
    def picture(self, info):
        """(w, h, rgba (h, w, 4) uint8, facts) of a bitmap info."""
        d = self.d
        w, h = self.u16(info + 0x88), self.u16(info + 0x8a)
        pal, tex = self.at(self.u32(info + 0x90)), self.at(self.u32(info + 0x94))
        if pal is None or tex is None:
            raise PckError('a picture with no palette or pixels')
        pix = self.at(self.u32(tex + 8))
        n = self.u16(pal + 2)
        storage = d[tex + 0x30]
        cols = np.frombuffer(bytes(d[pal + 16:pal + 16 + 4 * n]), dtype=np.uint8).reshape(n, 4).copy()
        cols = cols[ps2gs.clut_order(n)]
        cols[:, 3] = np.minimum(255, cols[:, 3].astype(np.int32) * 255 // 128)
        if n <= 16:
            if storage == 1:
                idx = ps2gs.unswizzle4(d[pix:], w, h, self.u16(tex + 0x32))
            else:
                nib = np.frombuffer(bytes(d[pix:pix + w * h // 2]), dtype=np.uint8)
                idx = np.empty(w * h, dtype=np.uint8)
                idx[0::2] = nib & 15
                idx[1::2] = nib >> 4
                idx = idx.reshape(h, w)
        else:
            if storage == 1:
                idx = ps2gs.unswizzle8(d[pix:], w, h)
            else:
                idx = np.frombuffer(bytes(d[pix:pix + w * h]), dtype=np.uint8).reshape(h, w)
        idx = np.minimum(idx, n - 1)
        return w, h, cols[idx], {'colors': n, 'storage': storage, 'pal': pal, 'pix': pix}

    def bitmap(self, oid):
        t, o = self.p.objects[oid]
        if t != 4:
            raise PckError('object %d is not a bitmap' % oid)
        w, h, rgba, facts = self.picture(self.at(self.u32(o + 8)))
        bw, bh = self.u16(o + 12) or w, self.u16(o + 14) or h
        return rgba[:bh, :bw], facts

    def atlas(self, fid, k):
        blk = self.font_blocks(fid)[k]
        w, h, rgba, _f = self.picture(blk['info'])
        return rgba

    # --------------------------------------------------------------- fonts
    def font_blocks(self, fid):
        t, o = self.p.objects[fid]
        out = []
        for k in range(min(8, self.u32(o + 0x98))):
            so = self.at(self.u32(o + 0x9c + 4 * k))
            if so is None:
                continue
            met = self.at(self.u32(so + 12))
            size, ng = self.u16(so + 16), self.u16(so + 18)
            glyphs = [list(struct.unpack_from('<BBBBhhhh', self.d, met + 12 * g)) for g in range(ng)] if met is not None else []
            for g in glyphs:
                g[0] *= 2
            out.append({'size': size, 'info': self.at(self.u32(so)), 'glyphs': glyphs, 'at': so})
        return out

    def font(self, fid):
        f = self.p.fonts.get(fid) or {'chars': ''}
        return {'chars': f['chars'], 'sizes': [{'size': b['size'], 'glyphs': b['glyphs']} for b in self.font_blocks(fid)]}

    # --------------------------------------------------------------- objects
    def shape(self, o):
        fs, ss, dl = self.at(self.u32(o + 8)), self.at(self.u32(o + 12)), self.at(self.u32(o + 16))
        nf, ns = self.u16(o + 20), self.u16(o + 22)
        fills = []
        for k in range(nf if fs is not None else 0):
            e = fs + 36 * k
            fills.append({'type': self.d[e], 'color': bytes(self.d[e + 4:e + 8]).hex(), 'm': self.f32s(e + 8, 6),
                          'bitmap': self.oid(self.u32(e + 32))})
        strokes = []
        for k in range(ns if ss is not None else 0):
            e = ss + 8 * k
            strokes.append({'width': self.u16(e), 'color': bytes(self.d[e + 4:e + 8]).hex()})
        cmds = []
        d = dl
        fill = stroke = 0
        while d is not None and len(cmds) < 20000:
            op = self.u16(d)
            if op == 0:
                break
            if op == 4:
                fill = self.u16(d + 2)
                d += 4
            elif op == 5:
                stroke = self.u16(d + 2)
                d += 4
            elif op in (1, 2, 3):
                c = self.u16(d + 2)
                pts = list(struct.unpack_from('<%dh' % (2 * c), self.d, d + 4))
                cmds.append({'op': 'fill' if op == 2 else 'line' if op == 1 else 'op3',
                             'style': fill if op == 2 else stroke, 'pts': pts})
                d += 4 + 4 * c
            else:
                break
        return {'fills': fills, 'strokes': strokes, 'cmds': cmds}

    def text(self, oid, o):
        recs, _s, _e = self.p.records(o)
        runs = []
        font = size = None
        color = '000000ff'
        x = y = 0
        for r in recs:
            if r[0] == 'font':
                font, size = r[1], r[2]
            elif r[0] == 'color':
                color = r[1].hex()
            elif r[0] == 'x':
                x = r[1]
            elif r[0] == 'y':
                y = r[1]
            elif r[0] == 'glyphs':
                runs.append({'font': font, 'size': size, 'color': color, 'x': x, 'y': y,
                             'g': [[gi, ga] for gi, ga in r[1]]})
                x += sum(ga for _g, ga in r[1])
        return {'m': self.f32s(o + 8, 6), 'runs': runs}

    def edit(self, o):
        ln, tp = self.u16(o + 16), self.u32(o + 8)
        text = ''
        if ln and self.p.inside(tp):
            so = self.at(tp)
            text = bytes(self.d[so:so + ln - 1]).decode('latin-1')
        name = ''
        kp = self.u32(o + 12)
        if self.p.inside(kp):
            ko = self.at(kp)
            name = bytes(self.d[ko:self.d.index(0, ko)]).decode('latin-1')
        return {'text': text, 'name': name, 'leading': self.s16(o + 18), 'color': bytes(self.d[o + 20:o + 24]).hex(),
                'font': self.u16(o + 24), 'size': self.u16(o + 26),
                'box': [self.s16(o + 28), self.s16(o + 30), self.s16(o + 32), self.s16(o + 34)],
                # the box is [width, height, xmin, ymin] in 1024ths of the size (checked against the .swf bounds)
                'boxpx': [v * self.u16(o + 26) / 1024 / TWIPS for v in
                          (self.s16(o + 32), self.s16(o + 34), self.s16(o + 28), self.s16(o + 30))],
                'align': self.d[o + 36]}

    def matrix(self, flags, mo):
        """a packed matrix -> ([a, b, c, d, tx, ty], what it holds, its fields' offsets)."""
        a = dd = 1.0
        b = c = 0.0
        k = mo
        has = {'scale': False, 'rotate': False}
        if flags != 0xCD and flags & 1:
            a, dd = self.s16(k) / 256, self.s16(k + 2) / 256
            has['scale'] = True
            k += 4
        if flags != 0xCD and flags & 2:
            b, c = self.s16(k) / 256, self.s16(k + 2) / 256
            has['rotate'] = True
            k += 4
        tx, ty = self.s16(k), self.s16(k + 2)
        return [a, b, c, dd, tx, ty], has

    def cxform(self, co):
        if co is None:
            return None
        m = list(self.d[co:co + 4])
        a = list(struct.unpack_from('<4b', self.d, co + 4))
        return {'mul': [v / 64 for v in m], 'add': a}

    def timeline(self, o):
        frames = []
        nfr = self.u16(o + 8)
        fr = self.at(self.u32(o + 12))
        for f in range(nfr if fr is not None else 0):
            cmds = []
            c = self.u32(fr + 8 * f + 4)
            guard = 0
            while c and self.p.inside(c) and guard < 5000:
                guard += 1
                co = self.at(c)
                t = self.d[co]
                if t in (0, 1):
                    flags = self.d[co + 1]
                    mo = self.at(self.u32(co + 16))
                    m, has = self.matrix(flags, mo) if mo is not None else (None, {})
                    name = ''
                    if t == 1:
                        no = self.at(self.u32(co + 36))
                        if no is not None:
                            name = bytes(self.d[no:self.d.index(0, no)]).decode('latin-1')
                    ch = self.u16(co + 14)
                    cmds.append({'op': 'place', 'at': co, 'depth': self.u16(co + 12),
                                 'char': None if ch == 0xFFFF else ch, 'm': m, 'has': has,
                                 'cx': self.cxform(self.at(self.u32(co + 20))), 'name': name})
                elif t == 2:
                    cmds.append({'op': 'remove', 'depth': self.u16(co + 12)})
                elif t == 3:
                    cmds.append({'op': 'action'})
                c = self.u32(co + 4)
            frames.append(cmds)
        return frames

    def button(self, o):
        n = self.d[o + 9]
        r = self.at(self.u32(o + 12))
        recs = []
        for k in range(n if r is not None else 0):
            e = r + 40 * k
            recs.append({'states': self.d[e], 'depth': self.d[e + 1], 'm': self.f32s(e + 4, 6),
                         'cx': self.cxform(e + 28), 'char': self.oid(self.u32(e + 36)), 'at': e})
        return {'records': recs}

    # --------------------------------------------------------------- the lot, for the page
    def to_json(self):
        objs = {}
        for i, (t, o) in sorted(self.p.objects.items()):
            try:
                if t == 1:
                    objs[i] = {'type': 'shape', **self.shape(o)}
                elif t == 2:
                    objs[i] = {'type': 'sprite', 'frames': self.timeline(o)}
                elif t == 3:
                    objs[i] = {'type': 'button', **self.button(o)}
                elif t == 4:
                    objs[i] = {'type': 'bitmap', 'w': self.u16(o + 12), 'h': self.u16(o + 14)}
                    try:
                        info = self.at(self.u32(o + 8))
                        objs[i]['tw'], objs[i]['th'] = self.u16(info + 0x88), self.u16(info + 0x8a)
                        _w, _h, _r, facts = self.picture(info)
                        objs[i]['colors'] = facts['colors']
                    except (PckError, ValueError, IndexError):
                        objs[i]['error'] = 'its picture could not be read'
                elif t == 5:
                    objs[i] = {'type': 'font', **self.font(i)}
                elif t == 6:
                    objs[i] = {'type': 'text', **self.text(i, o)}
                elif t == 7:
                    objs[i] = {'type': 'edit', **self.edit(o)}
                else:
                    objs[i] = {'type': {0: 'file', 8: 'sound'}.get(t, 'type %d' % t)}
            except (PckError, ValueError, IndexError, struct.error) as ex:
                objs[i] = {'type': 'broken', 'error': str(ex)}
        return {'width': self.p.width, 'height': self.p.height, 'root': self.timeline(0), 'objects': objs}


# ------------------------------------------------------------------- writing

def _put_s16(d, o, v):
    v = int(round(v))
    if not -32768 <= v <= 32767:
        raise PckError('%d is out of range for this field' % v)
    struct.pack_into('<h', d, o, v)


def _s88(v):
    if not -127.99 <= v <= 127.99:
        raise PckError('a scale of %.2f is beyond what the game stores (128)' % v)
    return int(round(v * 256))


def write_place(s, co, m):
    """A place command's matrix: in place when it holds the parts `m` needs, else a new packed matrix appended
    (with the flags for scale / rotation) and the command pointed at it."""
    d = s.d
    if d[co] not in (0, 1):
        raise PckError('no place command at %#x' % co)
    flags, mo = d[co + 1], s.at(s.u32(co + 16))
    f = 0 if flags == 0xCD else flags
    need_s = abs(m[0] - 1) > 1e-4 or abs(m[3] - 1) > 1e-4
    need_r = abs(m[1]) > 1e-4 or abs(m[2]) > 1e-4
    if mo is not None and (f & 1 or not need_s) and (f & 2 or not need_r):
        o = mo
        if f & 1:
            _put_s16(d, o, _s88(m[0]))
            _put_s16(d, o + 2, _s88(m[3]))
            o += 4
        if f & 2:
            _put_s16(d, o, _s88(m[1]))
            _put_s16(d, o + 2, _s88(m[2]))
            o += 4
        _put_s16(d, o, m[4])
        _put_s16(d, o + 2, m[5])
        return
    nf = (f & ~3) | (1 if need_s or f & 1 else 0) | (2 if need_r or f & 2 else 0)
    raw = b''
    if nf & 1:
        raw += struct.pack('<hh', _s88(m[0]), _s88(m[3]))
    if nf & 2:
        raw += struct.pack('<hh', _s88(m[1]), _s88(m[2]))
    for v in (m[4], m[5]):
        v = int(round(v))
        if not -32768 <= v <= 32767:
            raise PckError('a position of %d twips is out of range' % v)
        raw += struct.pack('<h', v)
    struct.pack_into('<I', d, co + 16, s.p.append(raw))
    d[co + 1] = nf


def _letters_needed(data, changes):
    """{font id: letters it lacks} for the text changes."""
    from .pck import Pck
    p = Pck(data)
    need = {}
    for c in changes:
        k = c.get('kind')
        try:
            t, o = p.objects[int(c.get('id', -1))]
        except (KeyError, ValueError):
            continue
        if k == 'text' and t == 6:
            recs, _s, _e = p.records(o)
            font, i = None, 0
            lines = list(c.get('lines') or [])
            for r in recs:
                if r[0] == 'font':
                    font = r[1]
                elif r[0] == 'glyphs':
                    if i < len(lines):
                        chars = (p.fonts.get(font) or {}).get('chars', '')
                        for ch in lines[i]:
                            if ch not in chars and ch != ' ':
                                need.setdefault(font, set()).add(ch)
                    i += 1
        elif k == 'edit' and t == 7:
            font = p.u16(o + 24)
            chars = (p.fonts.get(font) or {}).get('chars', '')
            for ch in (c.get('text') or ''):
                if ch not in chars and ch not in '\r\n\t':
                    need.setdefault(font, set()).add(ch)
    return need


def apply(data: bytes, changes, sources=None, ttf=None, smallcaps=None, autofill=False):
    """Changes to a screen. The file keeps its size when everything fits and GROWS when not (a longer text, a font
    given new letters, a matrix with parts it did not have, a new picture or image):

        {kind: 'text', id, lines: [...], align}         a static text's lines (no limit)
        {kind: 'edit', id, text}                        an edit text's own text (no limit)
        {kind: 'place', at, m: [a, b, c, d, tx, ty]}   a place command's matrix (any: scale and rotation too)
        {kind: 'textm', id, m}                          a static text's matrix
        {kind: 'button', at, m}                         a button record's matrix
        {kind: 'cxform', at, mul: [4], add: [4]}        a place command's colour transform (when it has one)
        {kind: 'bitmap', id, png, mode}                 a picture: 'resize' into the old one, 'fit' (its own
                                                        resolution in the old one's place), 'own' (its own size)
        {kind: 'add_image', png, parent, frame, x, y}   a new image placed on a timeline (0 = the screen)

    With `autofill`, letters a text needs that its font lacks are first given to the font (pckfont.fill: the same
    typeface in `sources`, a callable giving [(name, Scene)], then the TrueType font `ttf`); without, such a text is
    refused, naming them."""
    need = _letters_needed(data, changes) if autofill else {}
    if need:
        from . import pckfont as PF
        srcs = sources() if callable(sources) else (sources or [])
        for fid, chs in need.items():
            try:
                data = PF.fill(data, fid, srcs, ttf, want=''.join(sorted(chs)), smallcaps=smallcaps)
            except PckError:
                pass            # nothing to add: the text change says which letters are missing
    s = Scene(data)
    d = s.d
    repair_textures(s)          # pictures an earlier Studio wrote with the wrong upload fields
    order = sorted(changes, key=lambda c: c.get('kind') == 'add_image')
    for c in order:
        k = c.get('kind')
        if k in ('text', 'edit'):
            try:
                if k == 'text':
                    s.p.set_text(int(c['id']), list(c.get('lines') or []), c.get('align') or 'keep')
                else:
                    s.p.set_edit(int(c['id']), c.get('text') or '')
            except PckError as ex:
                if 'has no' not in str(ex):
                    raise
                raise PckError(str(ex) + (' -- no other screen has them in this typeface: pick a TrueType font for '
                                          'missing letters' if autofill else
                                          ' -- tick "Fill missing letters on save" to add them to the font (from the '
                                          'same typeface in the other screens, or a TrueType font)'))
        elif k == 'place':
            write_place(s, int(c['at']), [float(v) for v in c['m']])
        elif k == 'textm':
            t, o = s.p.objects[int(c['id'])]
            if t != 6:
                raise PckError('object %s is not a static text' % c['id'])
            struct.pack_into('<6f', d, o + 8, *[float(v) for v in c['m']])
        elif k == 'button':
            struct.pack_into('<6f', d, int(c['at']) + 4, *[float(v) for v in c['m']])
        elif k == 'cxform':
            co = int(c['at'])
            xo = s.at(s.u32(co + 20))
            if xo is None:
                raise PckError('that placement has no colour transform')
            for j in range(4):
                d[xo + j] = max(0, min(255, int(round(c['mul'][j] * 64))))
                struct.pack_into('<b', d, xo + 4 + j, max(-128, min(127, int(round(c['add'][j])))))
        elif k == 'bitmap':
            put_picture(s, int(c['id']), base64.b64decode(c['png']), c.get('mode') or 'resize')
        elif k == 'add_image':
            add_image(s, base64.b64decode(c['png']), int(c.get('parent') or 0), int(c.get('frame') or 0),
                      float(c.get('x') or 0), float(c.get('y') or 0))
        else:
            raise PckError('unknown change %r' % k)
    return s.p.to_bytes()


# ---- pictures

def _old_upload_h(w, h):
    """the upload height the Studio's first texture writer used for a PSMT4 texture (the first rectangle, trying
    widths w/2, w/4, w, w/8, 2w, that covers the texture's memory) -- to read what it wrote."""
    ys, xs = np.mgrid[0:h, 0:w]
    tex = ps2gs._addr4(xs, ys, max(1, w // 128))
    need = np.unique(tex // 8)
    n = w * h // 8
    for W32 in (w // 2, w // 4, w, w // 8, w * 2):
        if W32 < 1 or n % W32:
            continue
        H32 = n // W32
        uy, ux = np.mgrid[0:H32, 0:W32]
        if np.array_equal(np.unique(ps2gs._addr32(ux, uy, max(1, (W32 + 63) // 64))), need):
            return H32
    return None


def repair_textures(s):
    """Textures an earlier Studio wrote (a filled font's atlas, a new picture) kept the texture header's size and
    upload fields of the one they were copied from, so the game uploaded them wrong (stripes). Every texture of the
    game's own has the fields ps2gs.header_fields gives; one that has not is rewritten: its pixels read as they were
    written, written as the game uploads that size, the fields set (a tall 256-colour one: linear, as the game's
    tall ones are). -> how many were repaired."""
    d = s.d
    seen, fixed = set(), 0
    infos = []
    for _i, (t, o) in s.p.objects.items():
        if t == 4:
            infos.append(s.at(s.u32(o + 8)))
        elif t == 5:
            infos += [b['info'] for b in s.font_blocks(_i)]
    for info in infos:
        if info is None or info in seen:
            continue
        seen.add(info)
        tex, pal = s.at(s.u32(info + 0x94)), s.at(s.u32(info + 0x90))
        if tex is None or pal is None or d[tex + 0x30] != 1:
            continue
        w, h = s.u16(info + 0x88), s.u16(info + 0x8a)
        n = s.u16(pal + 2)
        bpp = 4 if n <= 16 else 8
        if (s.u16(tex + 0x2a), d[tex + 0x31], s.u16(tex + 0x32)) == ps2gs.header_fields(w, h, bpp):
            continue
        pix = s.at(s.u32(tex + 8))
        hb = bytearray(d[tex:tex + 0x40])
        if bpp == 4:
            idx = ps2gs.unswizzle4(d[pix:], w, h, _old_upload_h(w, h))
            raw = ps2gs.swizzle4(idx, w, h)
            d[pix:pix + len(raw)] = raw
            set_tex_header(hb, w, h, 4)
        elif h > w or w > 2 * h:
            idx = ps2gs.unswizzle8(d[pix:], w, h)
            d[pix:pix + w * h] = np.ascontiguousarray(idx).tobytes()
            size, x31, h32 = ps2gs.header_fields(w, h, 8, swizzled=False)
            struct.pack_into('<HHHH', hb, 0x28, 5, size, w, h)
            hb[0x30], hb[0x31] = 5, x31
            struct.pack_into('<H', hb, 0x32, h32)
        else:
            set_tex_header(hb, w, h, 8)       # the pixels were already in the game's order (w/2 x h/2)
        d[tex:tex + 0x40] = hb
        fixed += 1
    return fixed


def load_image(raw):
    """A picture given as a .png / .jpg, or as one of the game's .tex textures (decoded with texture.py; its rows
    are stored bottom first, so they are turned the right way up) -> a PIL RGBA image."""
    from PIL import Image
    if raw[:8] == b'\x89PNG\r\n\x1a\n' or raw[:3] == b'\xff\xd8\xff':
        return Image.open(io.BytesIO(raw)).convert('RGBA')
    from . import texture as T
    dec = T.decode_tex_file(raw)
    if not dec:
        try:
            return Image.open(io.BytesIO(raw)).convert('RGBA')
        except Exception:                                       # noqa: BLE001
            raise PckError('not a picture this can read (a .png, .jpg or the game\'s .tex)')
    w, h, rgba = dec[:3]
    a = np.frombuffer(bytes(rgba), dtype=np.uint8).reshape(h, w, 4)[::-1]
    return Image.fromarray(np.ascontiguousarray(a), 'RGBA')


def _quantize(rgba, n, tw, th):
    """an RGBA image on a tw x th page -> (indices (th, tw), palette (n, 4) RGBA)."""
    from PIL import Image
    im = Image.fromarray(np.ascontiguousarray(rgba), 'RGBA')
    page = Image.new('RGBA', (tw, th), (0, 0, 0, 0))
    page.paste(im, (0, 0))
    q = page.quantize(colors=n, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)
    palette = np.zeros((n, 4), dtype=np.uint8)
    used = np.frombuffer(bytes(q.getpalette(rawmode='RGBA') or b''), dtype=np.uint8)
    used = used[:4 * n].reshape(-1, 4)
    palette[:len(used)] = used
    return np.asarray(q, dtype=np.uint8).reshape(th, tw), palette


def _ps2_palette(palette):
    n = len(palette)
    ps2 = palette.copy()
    ps2[:, 3] = (palette[:, 3].astype(np.int32) * 128 + 127) // 255
    return ps2[ps2gs.clut_order(n)].tobytes()


def _pixels(idx, n, storage, tw, th):
    if n <= 16:
        return ps2gs.swizzle4(idx, tw, th) if storage == 1 else (idx.ravel()[0::2] | (idx.ravel()[1::2] << 4)).astype(np.uint8).tobytes()
    return ps2gs.swizzle8(idx, tw, th) if storage == 1 else idx.tobytes()


def game_size(tw, th):
    """A texture size of the shapes the game's own swizzled ones have: as wide as high, or twice as wide."""
    if th > tw:
        tw = th
    if tw > 2 * th:
        th = tw // 2
    return tw, th


def set_tex_header(hb, w, h, bpp):
    """A texture header (bytes from +0) for a swizzled w x h texture: its size, storage and upload fields."""
    size, x31, h32 = ps2gs.header_fields(w, h, bpp)
    struct.pack_into('<H', hb, 0x28, 6 if bpp == 4 else 5)
    struct.pack_into('<HHH', hb, 0x2a, size, w, h)
    hb[0x30] = 1
    hb[0x31] = x31
    struct.pack_into('<H', hb, 0x32, h32)


def _pow2(v, lo=16, hi=1024):
    p = lo
    while p < v and p < hi:
        p *= 2
    return p


def new_texture(s, template_info, rgba, n=None):
    """A new picture (bitmap info, palette, texture header, pixels) appended, from a template info of the screen
    (its GS packet copied with the new size): -> (info ptr, texture w, h). Swizzled, `n` colours (the template's)."""
    d = s.d
    _w, _h, _r, f = s.picture(template_info)
    n = n or f['colors']
    bh, bw = rgba.shape[:2]
    tw, th = game_size(_pow2(bw), _pow2(bh))
    idx, palette = _quantize(rgba, n, tw, th)
    tex = s.at(s.u32(template_info + 0x94))
    pal = f['pal']
    pix_ptr = s.p.append(_pixels(idx, n, 1, tw, th))
    pal_ptr = s.p.append(bytes(d[pal:pal + 16]) + _ps2_palette(palette))
    th_b = bytearray(d[tex:tex + 0x40])
    struct.pack_into('<I', th_b, 8, pix_ptr)
    set_tex_header(th_b, tw, th, 4 if n <= 16 else 8)
    tex_ptr = s.p.append(bytes(th_b))
    ib = bytearray(d[template_info:template_info + 0xb0])
    ow, oh = s.u16(template_info + 0x88), s.u16(template_info + 0x8a)
    psm = 0x14 if n <= 16 else 0x13
    for q in range(0, 0x80, 8):
        v = struct.unpack_from('<Q', ib, q)[0]
        if ((v >> 20) & 0x3f) == psm and ((v >> 26) & 0xf) == ow.bit_length() - 1 and ((v >> 30) & 0xf) == oh.bit_length() - 1:
            v &= ~((0x3f << 14) | (0xf << 26) | (0xf << 30))
            v |= (max(1, tw // 64) << 14) | ((tw.bit_length() - 1) << 26) | ((th.bit_length() - 1) << 30)
            struct.pack_into('<Q', ib, q, v)
    struct.pack_into('<HH', ib, 0x88, tw, th)
    struct.pack_into('<II', ib, 0x90, pal_ptr, tex_ptr)
    return s.p.append(bytes(ib)), tw, th


def _fit(im, limit=1024):
    from PIL import Image
    if max(im.size) > limit:
        k = limit / max(im.size)
        im = im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), Image.LANCZOS)
    return im


def put_picture(s, oid, png, mode='resize'):
    """A new picture for bitmap `oid`. 'resize': made the old one's size and written over it (the file keeps its
    size). 'fit': a new texture at the picture's own resolution, shown where the old one was. 'own': the same,
    and the shapes that show it resized to the picture's own size (about their origin)."""
    from PIL import Image
    t, o = s.p.objects[oid]
    if t != 4:
        raise PckError('object %d is not a bitmap' % oid)
    info = s.at(s.u32(o + 8))
    w, h = s.u16(info + 0x88), s.u16(info + 0x8a)
    bw, bh = s.u16(o + 12) or w, s.u16(o + 14) or h
    _w, _h, _rgba, f = s.picture(info)
    n, storage, pal, pix = f['colors'], f['storage'], f['pal'], f['pix']
    im = load_image(png)
    if mode == 'resize' or im.size == (bw, bh):
        if im.size != (bw, bh):
            im = im.resize((bw, bh), Image.LANCZOS)
        idx, palette = _quantize(np.asarray(im), n, w, h)
        s.d[pal + 16:pal + 16 + 4 * n] = _ps2_palette(palette)
        raw = _pixels(idx, n, storage, w, h)
        s.d[pix:pix + len(raw)] = raw
        return
    im = _fit(im)
    nw, nh = im.size
    info_ptr, tw, th = new_texture(s, info, np.asarray(im), max(n, 256) if n > 16 else 256)
    inv = struct.unpack_from('<ff', s.d, o + 16)
    by_tex = abs(inv[0] - 1 / w) < abs(inv[0] - 1 / bw) if bw != w else False
    struct.pack_into('<IHHff', s.d, o + 8, info_ptr, nw, nh, 1 / (tw if by_tex else nw), 1 / (th if by_tex else nh))
    # the fills that show it: the same place on the shape now covers the new picture's share of its texture
    kx, ky = (nw / tw) / (bw / w), (nh / th) / (bh / h)
    sx, sy = (nw / bw, nh / bh) if mode == 'own' else (1.0, 1.0)
    ptr = s.p.base + o
    for sid, (st, so) in s.p.objects.items():
        if st != 1:
            continue
        fs, nf = s.at(s.u32(so + 8)), s.u16(so + 20)
        hit = False
        for k in range(nf if fs is not None else 0):
            e = fs + 36 * k
            if s.u32(e + 32) != ptr:
                continue
            hit = True
            a, b, c, dd, tx, ty = struct.unpack_from('<6f', s.d, e + 8)
            struct.pack_into('<6f', s.d, e + 8, kx * a / sx, ky * b / sx, kx * c / sy, ky * dd / sy, kx * tx, ky * ty)
        if hit and mode == 'own':
            dl = s.at(s.u32(so + 16))
            q = dl
            while q is not None:
                op = s.u16(q)
                if op == 0:
                    break
                if op in (4, 5):
                    q += 4
                    continue
                cnt = s.u16(q + 2)
                for j in range(cnt):
                    x, y = struct.unpack_from('<hh', s.d, q + 4 + 4 * j)
                    _put_s16(s.d, q + 4 + 4 * j, x * sx)
                    _put_s16(s.d, q + 6 + 4 * j, y * sy)
                q += 4 + 4 * cnt


def add_image(s, png, parent, frame, x, y):
    """A new image on a timeline (0: the screen, else a sprite), centred at (x, y) px in it: a bitmap, a shape that
    shows it (a quad its size), a place command at a new top depth appended to that frame's commands; objects added
    to the object list (a new, longer list). Templates (vtables, the GS packet) are the screen's own."""
    from PIL import Image
    d, p = s.d, s.p
    tmpl_bmp = next((o for i, (t, o) in sorted(p.objects.items()) if t == 4 and
                     _safe_colors(s, o) == 256), None)
    if tmpl_bmp is None:
        tmpl_bmp = next((o for i, (t, o) in sorted(p.objects.items()) if t == 4), None)
    tmpl_shape, tmpl_fill = None, None
    for i, (t, o) in sorted(p.objects.items()):
        if t == 1 and s.u16(o + 20):
            fs = s.at(s.u32(o + 8))
            for k in range(s.u16(o + 20)):
                if fs is not None and s.u32(fs + 36 * k + 32):
                    tmpl_shape, tmpl_fill = o, fs + 36 * k
                    break
        if tmpl_shape is not None:
            break
    tmpl_cmd = _find_cmd(s, 0)
    if tmpl_bmp is None or tmpl_shape is None or tmpl_cmd is None:
        raise PckError('this screen has no picture, picture shape or placement to copy from')
    im = _fit(load_image(png), 512)
    w, h = im.size
    info_ptr, tw, th = new_texture(s, s.at(s.u32(tmpl_bmp + 8)), np.asarray(im),
                                   256 if _safe_colors(s, tmpl_bmp) > 16 else 16)
    bo = bytearray(d[tmpl_bmp:tmpl_bmp + 24])
    struct.pack_into('<IHHff', bo, 8, info_ptr, w, h, 1 / w, 1 / h)
    bmp_ptr = p.append(bytes(bo))
    fill = bytearray(d[tmpl_fill:tmpl_fill + 36])
    fill[0] = 0x41
    fill[4:8] = b'\xff\xff\xff\xff'
    struct.pack_into('<6fI', fill, 8, 1 / tw, 0.0, 0.0, 1 / th, (w / 2) / tw, (h / 2) / th, bmp_ptr)
    fill_ptr = p.append(bytes(fill))
    hw, hh = round(w * 10), round(h * 10)       # half the size in twips
    dl = struct.pack('<HH', 4, 1) + struct.pack('<HH', 2, 4) + struct.pack('<8h', -hw, -hh, hw, -hh, hw, hh, -hw, hh) + b'\0\0'
    dl_ptr = p.append(dl)
    sh = bytearray(d[tmpl_shape:tmpl_shape + 24])
    struct.pack_into('<IIIHH', sh, 8, fill_ptr, 0, dl_ptr, 1, 0)
    shape_ptr = p.append(bytes(sh))
    # the object list, two longer
    n = s.u16(42)
    lst = s.at(s.u32(16))
    ptrs = list(struct.unpack_from('<%dI' % n, d, lst)) + [bmp_ptr, shape_ptr]
    struct.pack_into('<I', d, 16, p.append(struct.pack('<%dI' % len(ptrs), *ptrs)))
    struct.pack_into('<H', d, 42, len(ptrs))
    shape_id = n + 1
    # the place command: a top depth in that timeline
    tl = 0 if not parent else p.objects[parent][1]
    if parent and p.objects[parent][0] != 2:
        raise PckError('object %d is not a sprite' % parent)
    frames = s.timeline(tl)
    depth = 1 + max([c['depth'] for fr in frames for c in fr if c['op'] in ('place', 'remove')] or [0])
    mat = p.append(struct.pack('<hhhh', 256, 256, int(round(x * 20)), int(round(y * 20))))
    cmd = bytearray(d[tmpl_cmd:tmpl_cmd + 24])
    cmd[0], cmd[1] = 0, 1
    struct.pack_into('<I', cmd, 4, 0)
    struct.pack_into('<HHII', cmd, 12, depth, shape_id, mat, 0)
    cmd_ptr = p.append(bytes(cmd))
    fr = s.at(s.u32(tl + 12))
    nfr = s.u16(tl + 8)
    if fr is None or not nfr:
        raise PckError('that timeline has no frames')
    f = min(max(0, frame), nfr - 1)
    c = s.u32(fr + 8 * f + 4)
    if not c:
        struct.pack_into('<I', d, fr + 8 * f + 4, cmd_ptr)
    else:
        while True:
            co = s.at(c)
            nxt = s.u32(co + 4)
            if not nxt:
                struct.pack_into('<I', d, co + 4, cmd_ptr)
                break
            c = nxt
    return shape_id


def _safe_colors(s, bo):
    try:
        return s.picture(s.at(s.u32(bo + 8)))[3]['colors']
    except (PckError, ValueError, IndexError, TypeError):
        return 0


def _find_cmd(s, kind):
    for tl in [0] + [o for _i, (t, o) in s.p.objects.items() if t == 2]:
        fr = s.at(s.u32(tl + 12))
        for f in range(s.u16(tl + 8) if fr is not None else 0):
            c = s.u32(fr + 8 * f + 4)
            while c and s.p.inside(c):
                co = s.at(c)
                if s.d[co] == kind:
                    return co
                c = s.u32(co + 4)
    return None


def png_of(rgba):
    from PIL import Image
    out = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(rgba), 'RGBA').save(out, 'PNG')
    return out.getvalue()
