"""Red Dead Revolver text tables (.strtbl: HUD, menu and subtitle text), PS2 and Xbox alike.

    python strtbl_tool.py dump swat.strtbl swat.txt       # every line as text: ID <TAB> font <TAB> scale x <TAB> scale y <TAB> text
    python strtbl_tool.py build swat.txt swat.strtbl --base swat.strtbl
    python strtbl_tool.py check <folder>                  # every .strtbl in a folder read and written back byte for byte

The format (little endian):
    u32 languages (10)
    u32 per language: the offset of its block (several languages can share one block; an empty language points at
        the end of the file)
    u32 512 (hash buckets), u32 count, then count x (u32 n, n bytes, a 0): the string IDs, sorted ignoring case
    each block: u32 count, then count x
        u32 the ID's ELF hash, u16 1, u32 n + n bytes the font name, u32 characters with the terminating 0 + UTF-16
        text, f32 scale x, f32 scale y, u16 0
In dumps a line break in a text is written \\n. build takes the IDs, fonts and scales from the dump and the language
layout from --base; every language gets the dump's text (the shipped tables have English only, apart from 4).
"""
import os
import struct
import sys


def elf(s):
    h = 0
    for c in s.encode('latin-1'):
        h = ((h << 4) + c) & 0xFFFFFFFF
        g = h & 0xF0000000
        if g:
            h ^= g >> 24
        h &= ~g & 0xFFFFFFFF
    return h


def read(d):
    nlang = struct.unpack_from('<I', d, 0)[0]
    offs = struct.unpack_from(f'<{nlang}I', d, 4)
    o = 4 + 4 * nlang
    buckets, count = struct.unpack_from('<II', d, o)
    o += 8
    names = []
    for _ in range(count):
        n = struct.unpack_from('<I', d, o)[0]
        names.append(d[o + 4:o + 4 + n].decode('latin-1'))
        o += 5 + n
    blocks, lang_block, at = [], [], []
    for lo in offs:
        if lo >= len(d):
            lang_block.append(-1)
            continue
        if lo not in at:
            at.append(lo)
            n = struct.unpack_from('<I', d, lo)[0]
            p = lo + 4
            ents = []
            for _ in range(n):
                h, a = struct.unpack_from('<IH', d, p)
                p += 6
                fl = struct.unpack_from('<I', d, p)[0]
                font = d[p + 4:p + 4 + fl].decode('latin-1')
                p += 4 + fl
                tl = struct.unpack_from('<I', d, p)[0]
                text = d[p + 4:p + 4 + 2 * tl].decode('utf-16-le')
                p += 4 + 2 * tl
                sx, sy, b = struct.unpack_from('<ffH', d, p)
                p += 10
                ents.append([h, a, font, text[:-1] if text.endswith('\0') else text, sx, sy, b])
            blocks.append(ents)
        lang_block.append(at.index(lo))
    return {'buckets': buckets, 'names': names, 'lang_block': lang_block, 'blocks': blocks}


def write(t):
    nlang = len(t['lang_block'])
    out = bytearray(struct.pack('<I', nlang) + bytes(4 * nlang) + struct.pack('<II', t['buckets'], len(t['names'])))
    for n in t['names']:
        b = n.encode('latin-1')
        out += struct.pack('<I', len(b)) + b + b'\0'
    at = []
    for ents in t['blocks']:
        at.append(len(out))
        out += struct.pack('<I', len(ents))
        for h, a, font, text, sx, sy, b in ents:
            fb = font.encode('latin-1')
            tb = (text + '\0').encode('utf-16-le')
            out += struct.pack('<IHI', h, a, len(fb)) + fb + struct.pack('<I', len(tb) // 2) + tb + struct.pack('<ffH', sx, sy, b)
    struct.pack_into(f'<{nlang}I', out, 4, *[at[k] if k >= 0 else len(out) for k in t['lang_block']])
    return bytes(out)


def dump(src, dst):
    t = read(open(src, 'rb').read())
    names = {elf(n): n for n in t['names']}
    with open(dst, 'w', encoding='utf-8', newline='\n') as f:
        for h, _a, font, text, sx, sy, _b in t['blocks'][0] if t['blocks'] else []:
            f.write(f"{names.get(h, '#%08X' % h)}\t{font}\t{sx:g}\t{sy:g}\t{text.replace(chr(10), chr(92) + 'n')}\n")
    print(f'{src}: {len(t["names"])} lines -> {dst}')


def build(src, dst, base):
    t = read(open(base, 'rb').read())
    rows = [l.rstrip('\n').split('\t', 4) for l in open(src, encoding='utf-8') if l.strip()]
    # the base's IDs keep their order (the game's sort); new ones go in where they sort, ignoring case
    wanted = {r[0] for r in rows if not r[0].startswith('#')}
    names = [n for n in t['names'] if n in wanted]
    for n in sorted(wanted - set(names), key=str.lower):
        at = next((i for i, x in enumerate(names) if x.lower() > n.lower()), len(names))
        names.insert(at, n)
    hashes = {}
    for n in names:
        if elf(n) in hashes:
            raise SystemExit(f'{n} and {hashes[elf(n)]} have the same hash: rename one')
        hashes[elf(n)] = n
    ents = [[int(r[0][1:], 16) if r[0].startswith('#') else elf(r[0]), 1, r[1], r[4].replace('\\n', '\n'),
             float(r[2]), float(r[3]), 0] for r in rows]
    t['names'] = names
    t['blocks'] = [[list(e) for e in ents] for _ in t['blocks']] or [ents]
    open(dst, 'wb').write(write(t))
    print(f'{src}: {len(ents)} lines -> {dst}')


def check(folder):
    ok = bad = 0
    for r, _d, fs in os.walk(folder):
        for f in fs:
            if f.lower().endswith('.strtbl'):
                d = open(os.path.join(r, f), 'rb').read()
                if write(read(d)) == d:
                    ok += 1
                else:
                    bad += 1
                    print('differs:', os.path.join(r, f))
    print(f'{ok} byte for byte, {bad} different')


if __name__ == '__main__':
    a = sys.argv[1:]
    if len(a) >= 3 and a[0] == 'dump':
        dump(a[1], a[2])
    elif len(a) >= 5 and a[0] == 'build' and a[3] == '--base':
        build(a[1], a[2], a[4])
    elif len(a) >= 2 and a[0] == 'check':
        check(a[1])
    else:
        print(__doc__)
