"""`.pck`: the game's Flash screens (menus, the journal, shops, popups, credits), and their text edited in place.

A .pck is a load-in-place memory image of the game's Flash player objects -- a Flash movie already turned into the
C++ objects that draw it, dumped as they lay in memory:

    0x00  u32  base   the address the payload is loaded at (payload[i] lives at base + i)
    0x04  u32  1
    0x08  u32  0
    0x0c  u32  size   == file size - 16
    0x10  payload; every pointer in it is an absolute address in [base, base + size)

The object layout follows the reverse engineering in AlgumCorrupto's RAGE-SWF-research
(github.com/AlgumCorrupto/RAGE-SWF-research, `flashpck.h`): this is the Studio's own reading of those facts, checked
on all 78 of the game's .pck (every object list walks, every static text decodes against its font).

    the root (swfFILE) at payload 0: u8 type 0, u16 frames at +8, frames ptr +12, object list ptr +16, fps +20,
        f32 width +28 and height +36, u16 object count +42. The list holds a pointer per object id (0 = none).
    every object: u8 type, 3 pad, u32 vtable. 1 shape, 2 sprite, 3 button, 4 bitmap, 5 font, 6 text, 7 edit text.
    font (5):      u16 glyph count +8, glyph shapes ptr +12, char codes ptr +16 (u16 each), advances ptr +20 (u16
                   each, in 1024ths of the size; null in the subset fonts the static texts use)
    text (6):      6 f32 matrix +8, records ptr +32. The records, u16 type first:
                   1 font (u16 font id, u16 size)  2 colour (RGBA)  3 y offset, 4 x offset (s16)
                   5 glyphs (u16 count, then u16 glyph index + u16 advance each)  0 end
                   So the text is glyph indices into the font's char codes, not characters. Units are twips (1/20 px).
    edit text (7): text ptr +8, name ptr +12, u16 text length with its NUL +16, s16 leading +18, RGBA +20,
                   u16 font id +24, u16 size +26, s16 x0/x1/y0/y1? +28 (the box), u8 alignment +36.
                   With no initial text the text ptr points outside the file (an empty string in the game).

The editing never moves anything: a text's records are rewritten in the bytes they had (plus the padding after
them up to the next 4-byte boundary, which is always 0xCD fill), an edit text's string in its own. So a new text may
have as many glyphs as the old, give or take a few, and only characters its font has.
"""

from __future__ import annotations

import struct
from collections import Counter

TYPES = {0: 'file', 1: 'shape', 2: 'sprite', 3: 'button', 4: 'bitmap', 5: 'font', 6: 'text', 7: 'edit text',
         8: 'sound'}
PAD = 0xCD


class PckError(ValueError):
    pass


class Pck:
    def __init__(self, data: bytes):
        if len(data) < 0x40:
            raise PckError('too small to be a .pck')
        self.base, one, zero, size = struct.unpack_from('<4I', data, 0)
        if size != len(data) - 16 or one != 1:
            raise PckError('not a PS2 .pck (its header does not match its size)')
        self.head = bytes(data[:16])
        self.d = bytearray(data[16:])
        if self.d[0] != 0:
            raise PckError('the root object is not at the start')
        self.width, = struct.unpack_from('<f', self.d, 28)
        self.height, = struct.unpack_from('<f', self.d, 36)
        n = self.u16(42)
        lst = self.off(self.u32(16))
        self.objects = {}                       # id -> (type, payload offset)
        for i in range(n):
            q = self.u32(lst + 4 * i)
            if q and self.inside(q):
                self.objects[i] = (self.d[self.off(q)], self.off(q))
        self.fonts = {}
        for i, (t, o) in self.objects.items():
            if t == 5:
                cnt, ca, aa = self.u16(o + 8), self.u32(o + 16), self.u32(o + 20)
                chars = [self.u16(self.off(ca) + 2 * k) for k in range(cnt)] if ca and self.inside(ca) else []
                adv = [self.u16(self.off(aa) + 2 * k) for k in range(cnt)] if aa and self.inside(aa) else None
                # the glyph atlases' own advances (1024ths of the size), per size: every glyph has one, so new
                # letters (a filled font's) can be laid too
                sizes = {}
                for k in range(min(8, self.u32(o + 0x98))):
                    sp = self.u32(o + 0x9c + 4 * k)
                    if not self.inside(sp):
                        continue
                    so = self.off(sp)
                    mp, sz, ng = self.u32(so + 12), self.u16(so + 16), self.u16(so + 18)
                    if self.inside(mp):
                        mo = self.off(mp)
                        sizes[sz] = [struct.unpack_from('<h', self.d, mo + 12 * g + 8)[0] for g in range(ng)]
                self.fonts[i] = {'chars': ''.join(map(chr, chars)), 'adv': adv, 'sizes': sizes}
        self._learn()

    # --------------------------------------------------------------- reading
    def off(self, ptr):
        return ptr - self.base

    def inside(self, ptr):
        return 0 <= ptr - self.base < len(self.d)

    def u16(self, o):
        return struct.unpack_from('<H', self.d, o)[0]

    def u32(self, o):
        return struct.unpack_from('<I', self.d, o)[0]

    def counts(self):
        c = Counter(TYPES.get(t, str(t)) for t, _ in self.objects.values())
        return dict(c)

    def records(self, o):
        """A text object's records: [(kind, value...)], and the byte span they take (end marker included)."""
        start = tr = self.off(self.u32(o + 32))
        out = []
        while True:
            ty = self.u16(tr)
            if ty == 0:
                tr += 2
                break
            if ty == 1:
                out.append(('font', self.u16(tr + 2), self.u16(tr + 4)))
                tr += 6
            elif ty == 2:
                out.append(('color', bytes(self.d[tr + 2:tr + 6])))
                tr += 6
            elif ty in (3, 4):
                out.append(('y' if ty == 3 else 'x', struct.unpack_from('<h', self.d, tr + 2)[0]))
                tr += 4
            elif ty == 5:
                g = self.u16(tr + 2)
                out.append(('glyphs', [struct.unpack_from('<HH', self.d, tr + 4 + 4 * k) for k in range(g)]))
                tr += 4 + 4 * g
            else:
                raise PckError('text record %d at %#x is not one this reader knows' % (ty, tr))
        return out, start, tr

    def room_after(self, end):
        """The 0xCD fill after `end` that is surely padding: up to the last 4-byte boundary the fill reaches."""
        k = end
        while k < len(self.d) and self.d[k] == PAD:
            k += 1
        k -= (self.base + k) % 4
        return max(0, k - end)

    def _learn(self):
        """Advances (in 1024ths of the size) and letter spacing seen in the file: what the subset fonts, which have
        no advance table, need to lay new text, and each font's median spacing."""
        seen, exact = {}, {}
        for _i, (t, o) in self.objects.items():
            if t != 6:
                continue
            try:
                recs, _s, _e = self.records(o)
            except PckError:
                continue
            font = size = None
            for r in recs:
                if r[0] == 'font':
                    font, size = r[1], r[2]
                elif r[0] == 'glyphs' and size:
                    for gi, ga in r[1]:
                        seen.setdefault((font, gi), Counter())[round(ga * 1024 / size)] += 1
                        exact.setdefault((font, gi, size), Counter())[ga] += 1
        self.learned = {k: v.most_common(1)[0][0] for k, v in seen.items()}
        self.exact = {k: v.most_common(1)[0][0] for k, v in exact.items()}

    def advance(self, font, gi, size):
        f = self.fonts.get(font) or {}
        if f.get('adv') and gi < len(f['adv']):
            return round(f['adv'][gi] * size / 1024)
        if (font, gi) in self.learned:
            return round(self.learned[(font, gi)] * size / 1024)
        sizes = f.get('sizes') or {}
        if sizes:
            near = min(sizes, key=lambda s: abs(s - size))
            if gi < len(sizes[near]):
                return round(sizes[near][gi] * size / 1024)
        return round(size * 0.5)

    # --------------------------------------------------------------- the text, for the page
    def texts(self):
        """Every static text (its lines, as runs of glyphs with their place and style) and every edit text with an
        initial string, for the editor. Ids are the objects' ids."""
        out = []
        for i, (t, o) in sorted(self.objects.items()):
            if t == 6:
                try:
                    recs, s, e = self.records(o)
                except PckError as ex:
                    out.append({'id': i, 'kind': 'text', 'error': str(ex), 'runs': []})
                    continue
                runs = []
                font = size = None
                color = b'\0\0\0\xff'
                x = y = 0
                for r in recs:
                    if r[0] == 'font':
                        font, size = r[1], r[2]
                    elif r[0] == 'color':
                        color = r[1]
                    elif r[0] == 'x':
                        x = r[1]
                    elif r[0] == 'y':
                        y = r[1]
                    elif r[0] == 'glyphs':
                        chars = (self.fonts.get(font) or {}).get('chars', '')
                        text = ''.join(chars[gi] if gi < len(chars) else '�' for gi, _a in r[1])
                        width = sum(a for _g, a in r[1])
                        runs.append({'text': text, 'font': font, 'size': size, 'x': x, 'y': y, 'w': width,
                                     'color': color.hex(), 'glyphs': len(r[1])})
                        x += width
                fixed = (e - s) - sum(4 + 4 * len(r[1]) for r in recs if r[0] == 'glyphs')
                m = struct.unpack_from('<6f', self.d, o + 8)
                out.append({'id': i, 'kind': 'text', 'runs': runs, 'fixed': fixed, 'room': (e - s) + self.room_after(e),
                            'matrix': [round(v, 4) for v in m], 'align': guess_align(runs)})
            elif t == 7:
                ln = self.u16(o + 16)
                tp = self.u32(o + 8)
                if not ln or not self.inside(tp):
                    continue
                so = self.off(tp)
                text = bytes(self.d[so:so + ln - 1]).decode('latin-1').replace('\r', '\n')
                fid, fsz = struct.unpack_from('<HH', self.d, o + 24)
                out.append({'id': i, 'kind': 'edit', 'text': text, 'font': fid, 'size': fsz,
                            'max': ln - 1 + self.room_after(so + ln), 'color': bytes(self.d[o + 20:o + 24]).hex(),
                            'align': ('left', 'right', 'center', 'justify')[self.d[o + 36]] if self.d[o + 36] < 4 else 'left'})
        return out

    def font_info(self):
        """Each font's characters and their advances in 1024ths of the size (the table's, or the ones its texts
        use), for the page to measure new lines."""
        out = {}
        for i, f in self.fonts.items():
            adv = {}
            for gi, c in enumerate(f['chars']):
                if f['adv'] and gi < len(f['adv']):
                    adv[c] = f['adv'][gi]
                elif (i, gi) in self.learned:
                    adv[c] = self.learned[(i, gi)]
            out[str(i)] = {'chars': f['chars'], 'table': bool(f['adv']), 'adv': adv}
        return out

    # --------------------------------------------------------------- writing
    def set_text(self, oid, lines, align='keep'):
        """A static text's runs given new strings (a list, one per run, as texts() gave them). Unchanged runs keep
        their bytes exactly; changed ones are laid with the font's advances and the run's own letter spacing, and
        (align center/right) moved so the line keeps its middle/right edge."""
        t, o = self.objects.get(oid, (None, None))
        if t != 6:
            raise PckError('object %s is not a static text' % oid)
        recs, s, e = self.records(o)
        runs = [r for r in recs if r[0] == 'glyphs']
        if len(lines) != len(runs):
            raise PckError('text %d has %d lines, not %d' % (oid, len(runs), len(lines)))
        if align == 'keep':
            align = guess_align(self.texts_one(oid))
        new = []
        font = size = None
        k = 0
        last_x = None                       # index in `new` of the x record before the run, to move it
        for r in recs:
            if r[0] == 'font':
                font, size = r[1], r[2]
                new.append(r)
                continue
            if r[0] == 'x':
                last_x = len(new)
                new.append(r)
                continue
            if r[0] == 'y':
                last_x = None if last_x is None else last_x
                new.append(r)
                continue
            if r[0] != 'glyphs':
                new.append(r)
                continue
            chars = (self.fonts.get(font) or {}).get('chars', '')
            old = ''.join(chars[gi] if gi < len(chars) else '�' for gi, _a in r[1])
            want = lines[k]
            k += 1
            if want == old:
                new.append(r)
                last_x = None
                continue
            glyphs = self.lay(font, size, want, r[1])
            if align in ('center', 'right') and last_x is not None:
                dw = sum(a for _g, a in r[1]) - sum(a for _g, a in glyphs)
                kind, xv = new[last_x]
                new[last_x] = (kind, max(-32768, min(32767, xv + (dw // 2 if align == 'center' else dw))))
            if glyphs:
                new.append(('glyphs', glyphs))
            last_x = None
        data = pack_records(new)
        room = (e - s) + self.room_after(e)
        if len(data) > room:
            # longer than the room it had: the records go to the end of the file and the text points there
            struct.pack_into('<I', self.d, o + 32, self.append(data))
            return
        self.d[s:s + len(data)] = data
        # what the old records used and the new do not: back to fill
        for j in range(s + len(data), e):
            self.d[j] = PAD

    def texts_one(self, oid):
        for t in self.texts():
            if t['id'] == oid:
                return t['runs']
        return []

    def lay(self, font, size, text, old):
        """New glyph entries for `text` in `font`: the advances the font gives, plus the old run's letter spacing
        (the median difference between its advances and the font's)."""
        chars = (self.fonts.get(font) or {}).get('chars', '')
        missing = sorted({c for c in text if c not in chars and c != ' '})
        if missing:
            raise PckError('the font of this line has no %s (it has: %s)' % (' '.join(repr(c) for c in missing), chars))
        # the advance this glyph has at this size elsewhere in the file is the best guess (it carries the
        # letter spacing the text was made with); else the font's, plus the old run's spacing
        deltas = sorted(ga - self.advance(font, gi, size) for gi, ga in old) or [0]
        spacing = deltas[len(deltas) // 2]
        here = {}
        for gi, ga in old:
            here.setdefault(gi, Counter())[ga] += 1
        out = []
        for c in text:
            if c not in chars:
                # a space the subset font has no glyph for: widen the glyph before it (Flash did the same)
                w = round(size * 0.27)
                if out:
                    out[-1] = (out[-1][0], out[-1][1] + w)
                continue
            gi = chars.index(c)
            if gi in here:
                a = here[gi].most_common(1)[0][0]
            elif (font, gi, size) in self.exact:
                a = self.exact[(font, gi, size)]
            else:
                a = self.advance(font, gi, size) + spacing
            out.append((gi, max(0, a)))
        return out

    def set_edit(self, oid, text):
        t, o = self.objects.get(oid, (None, None))
        if t != 7:
            raise PckError('object %s is not an edit text' % oid)
        ln, tp = self.u16(o + 16), self.u32(o + 8)
        # a line break in an edit text is Flash's \r
        text = text.replace('\r\n', '\r').replace('\n', '\r')
        own = ln and self.inside(tp)
        if own and text.encode('latin-1', 'replace') == bytes(self.d[self.off(tp):self.off(tp) + ln - 1]):
            return
        if not own and not text:
            return
        try:
            raw = text.encode('latin-1')
        except UnicodeEncodeError:
            raise PckError('edit text %d: a letter outside Latin-1' % oid)
        chars = (self.fonts.get(self.u16(o + 24)) or {}).get('chars')
        if chars:
            missing = sorted({c for c in text if c not in chars and c not in '\r\t'})
            if missing:
                raise PckError('edit text %d: its font has no %s' % (oid, ' '.join(repr(c) for c in missing)))
        room = (ln - 1 + self.room_after(self.off(tp) + ln)) if own else -1
        if len(raw) > room:
            # longer than its room (or a text of its own where the game filled it): to the end of the file
            struct.pack_into('<I', self.d, o + 8, self.append(raw + b'\0'))
            struct.pack_into('<H', self.d, o + 16, len(raw) + 1)
            return
        so = self.off(tp)
        old_end = so + ln
        self.d[so:so + len(raw)] = raw
        self.d[so + len(raw)] = 0
        for j in range(so + len(raw) + 1, max(old_end, so + len(raw) + 1)):
            self.d[j] = PAD
        struct.pack_into('<H', self.d, o + 16, len(raw) + 1)

    def append(self, raw):
        """`raw` laid at the end of the payload (16-byte aligned, 0xCD between): its address. The file grows."""
        while len(self.d) % 16:
            self.d.append(PAD)
        at = len(self.d)
        self.d += raw
        return self.base + at

    def to_bytes(self):
        while len(self.d) % 16:
            self.d.append(PAD)
        head = bytearray(self.head)
        struct.pack_into('<I', head, 12, len(self.d))
        return bytes(head) + bytes(self.d)


def pack_records(recs):
    out = bytearray()
    for r in recs:
        if r[0] == 'font':
            out += struct.pack('<HHH', 1, r[1], r[2])
        elif r[0] == 'color':
            out += struct.pack('<H', 2) + r[1]
        elif r[0] in ('y', 'x'):
            out += struct.pack('<Hh', 3 if r[0] == 'y' else 4, r[1])
        elif r[0] == 'glyphs':
            out += struct.pack('<HH', 5, len(r[1]))
            for gi, ga in r[1]:
                out += struct.pack('<HH', gi, ga)
    return bytes(out + b'\0\0')


def guess_align(runs):
    """How a text's lines are lined up, from where they start and end: left when they share a left edge, else
    center/right when they share a middle/right edge (a single line: left)."""
    if len(runs) < 2:
        return 'left'
    spread = lambda vals: max(vals) - min(vals)  # noqa: E731
    left = spread([r['x'] for r in runs])
    mid = spread([r['x'] + r['w'] / 2 for r in runs])
    right = spread([r['x'] + r['w'] for r in runs])
    best = min(left, mid, right)
    return 'left' if best == left else 'center' if best == mid else 'right'


def edit(data: bytes, changes):
    """`changes`: [{id, kind: text|edit, lines|text, align}] applied to a .pck's bytes; the new bytes (same size)."""
    p = Pck(data)
    for c in changes:
        if c.get('kind') == 'edit':
            p.set_edit(int(c['id']), c.get('text') or '')
        else:
            p.set_text(int(c['id']), list(c.get('lines') or []), c.get('align') or 'keep')
    return p.to_bytes()
