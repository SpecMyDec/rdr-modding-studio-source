"""Reading the unedited game as a reference: an extracted game folder, a PS2 disc image (ISO9660) or an Xbox one
(XISO), and the Dave / DAVE archives (.dat) inside it.

    ref = open_reference(path)            # a folder or an image
    ref.files()                           # {path (lower case, '/'): opaque} of every file
    ref.read(path)                        # its bytes
    level_files(ref, 'lvl_tomb', ps2)     # {name: bytes} of a level: its bundle, .lvl and zone files
"""
from __future__ import annotations

import os
import struct
import zlib

SECTOR = 2048
CHARS = "\x00 #$()-./?0123456789_abcdefghijklmnopqrstuvwxyz~\x7F"  # Dave packed name characters


class Folder:
    def __init__(self, path):
        self.root = path

    def files(self):
        out = {}
        for d, _dirs, names in os.walk(self.root):
            for n in names:
                p = os.path.join(d, n)
                out[os.path.relpath(p, self.root).replace(os.sep, '/').lower()] = p
        return out

    def read(self, path):
        with open(self.files()[path] if path in self.files() else os.path.join(self.root, path), 'rb') as f:
            return f.read()


class Iso9660:
    def __init__(self, path):
        self.f = open(path, 'rb')
        self.f.seek(16 * SECTOR)
        pvd = self.f.read(SECTOR)
        if pvd[1:6] != b'CD001':
            raise ValueError('not an ISO9660 image')
        self.root = self._record(pvd, 156)
        self._files = None

    @staticmethod
    def _record(buf, o):
        lba, size = struct.unpack_from('<I', buf, o + 2)[0], struct.unpack_from('<I', buf, o + 10)[0]
        return lba, size, bool(buf[o + 25] & 2), buf[o + 33:o + 33 + buf[o + 32]].decode('latin-1')

    def files(self):
        if self._files is None:
            out, todo = {}, [('', self.root)]
            while todo:
                prefix, (lba, size, _d, _n) = todo.pop()
                self.f.seek(lba * SECTOR)
                data = self.f.read(size)
                o = 0
                while o < len(data):
                    if data[o] == 0:
                        o = (o // SECTOR + 1) * SECTOR
                        continue
                    rec = self._record(data, o)
                    o += data[o]
                    if rec[3] in ('\x00', '\x01'):
                        continue
                    name = prefix + rec[3].split(';')[0].lower()
                    if rec[2]:
                        todo.append((name + '/', rec))
                    else:
                        out[name] = rec
            self._files = out
        return self._files

    def read(self, path):
        lba, size, _d, _n = self.files()[path]
        self.f.seek(lba * SECTOR)
        return self.f.read(size)


class Xiso:
    """An Xbox disc image: the XDVDFS header at 0x10000 (after the video partition in a full image)."""
    MAGIC = b'MICROSOFT*XBOX*MEDIA'

    def __init__(self, path):
        self.f = open(path, 'rb')
        for base in (0, 0x18300000, 0xFD90000, 0x2080000):
            self.f.seek(base + 0x10000)
            head = self.f.read(0x1C)
            if head[:20] == self.MAGIC:
                self.base = base
                self.root = struct.unpack_from('<II', head, 20)
                break
        else:
            raise ValueError('not an Xbox disc image')
        self._files = None

    def _dir(self, sector, size, prefix, out):
        self.f.seek(self.base + sector * SECTOR)
        data = self.f.read(size)
        todo = [0]
        seen = set()
        while todo:
            o = todo.pop()
            if o in seen or o + 14 > len(data):
                continue
            seen.add(o)
            left, right, start, length, attr, nl = struct.unpack_from('<HHIIBB', data, o)
            if left == 0xFFFF:
                continue
            name = data[o + 14:o + 14 + nl].decode('latin-1').lower()
            if left:
                todo.append(left * 4)
            if right:
                todo.append(right * 4)
            if attr & 0x10:
                if length:
                    self._dir(start, length, prefix + name + '/', out)
            else:
                out[prefix + name] = (start, length)

    def files(self):
        if self._files is None:
            out = {}
            self._dir(self.root[0], self.root[1], '', out)
            self._files = out
        return self._files

    def read(self, path):
        start, length = self.files()[path]
        self.f.seek(self.base + start * SECTOR)
        return self.f.read(length)


def open_reference(path: str):
    if os.path.isdir(path):
        return Folder(path)
    try:
        return Iso9660(path)
    except ValueError:
        return Xiso(path)


# ---------------------------------------------------------------------------------------------------------------
# Dave / DAVE archives

def dave_names(data: bytes) -> list:
    magic = data[:4]
    if magic not in (b'DAVE', b'Dave'):
        raise ValueError('not a Dave archive')
    count, info_size, _ = struct.unpack_from('<3I', data, 4)
    names, name = [], ''
    for i in range(count):
        name_offs = struct.unpack_from('<I', data, 0x800 + 16 * i)[0]
        p = 0x800 + info_size + name_offs
        if magic == b'DAVE':
            name = data[p:data.index(b'\0', p)].decode('latin-1')
        else:
            def bits(q):
                v = int.from_bytes(data[q:q + 3], 'little')
                return [v >> (6 * k) & 0x3F for k in range(4)]
            b = bits(p)
            p += 3
            prev, name = name, ''
            if b[0] >= 0x38:
                n = (b.pop(1) - 0x20) * 8 + b.pop(0) - 0x38
                name = prev[:n]
            while b[0]:
                name += CHARS[b.pop(0)]
                if not b:
                    b = bits(p)
                    p += 3
        names.append(name)
    return names


def dave_read(data: bytes, index: int) -> bytes:
    _no, off, full, comp = struct.unpack_from('<4I', data, 0x800 + 16 * index)
    raw = data[off:off + comp]
    return zlib.decompress(raw, -15) if full != comp else raw


def level_files(ref, stem: str, ps2: bool) -> tuple:
    """(archive path, {file name: bytes}) of a level in the reference game: its bundle (lvl_x.rsc / .xrsc), its .lvl
    and its zone files (.znes / .xznes). The archive named after the level (lvls_sng/tomb.dat for lvl_tomb) is tried
    first, then any level archive holding the bundle."""
    stem = stem.lower()
    bundle = stem + ('.rsc' if ps2 else '.xrsc')
    zext = '.znes' if ps2 else '.xznes'
    short = stem[4:] if stem.startswith('lvl_') else stem
    archives = sorted((p for p in ref.files() if p.endswith('.dat') and p.split('/')[0].startswith('lvls_')),
                      key=lambda p: (os.path.splitext(p.rsplit('/', 1)[-1])[0] != short, p))
    for arch in archives:
        try:
            data = ref.read(arch)
            names = dave_names(data)
        except (ValueError, OSError, struct.error):
            continue
        low = [n.lower().rsplit('/', 1)[-1] for n in names]
        if bundle not in low:
            continue
        out = {}
        for i, n in enumerate(low):
            # every file of the level (its bundle, .lvl, zones, bounds, lines, boxes, spawns, lights ...)
            if n.startswith(stem + '_') or n.startswith(stem + '.'):
                out[names[i].rsplit('/', 1)[-1]] = dave_read(data, i)
        return arch, out
    return '', {}
