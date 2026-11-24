#!/usr/bin/env python3
"""RDR Data Editor's builder: Red Dead Revolver's .dat archives rebuilt with loose mod files, for PS2 or Xbox.

    PS2:  a test ISO for PCSX2, made from the unedited ISO (--iso) as --out
    Xbox: --platform xbox --iso <the unedited game folder (default.xbe, swat.dat ...)> --out <the recomp's assets
          folder>: the rebuilt archives are written into that folder over the game's (the others are left as they
          are); --run starts the recomp (--recomp RDR.exe)

    python ps2build.py                      # the defaults below
    python ps2build.py --mods <folder> --iso <clean.iso> --out <test.iso>
    python ps2build.py --run                # then start PCSX2 with it
    ps2build.cmd                            # the same as --run, by double click

Every file in the mods folder (subfolders too) replaces the file of the same name inside the game's .dat archives,
in every archive that has it (aka_lods.rsc replaces swat.dat's). A file no archive has is reported and left out.
For each archive with a replacement:
    1. the archive is read out of the clean ISO and extracted once (cached; EdnessP's dave.py does the extracting
       and building)
    2. the mods are laid over a copy of it, and it is built again as the original was: Dave (compressed names) or
       DAVE, its files compressed or not, with directory entries if it had them
    3. it goes into a copy of the clean ISO: where the original was when it fits, else appended to the end of the
       image, its directory entry (ISO9660) pointed at it
The clean ISO is never written to. The test ISO is made from it: the first time copied whole, after that the last
run's changes put back from the clean ISO (a state file beside it records them), so nothing from an earlier run
stays. An archive whose mods have not changed since its last build is not built again: a run with nothing new
takes seconds; rebuilding swat.dat takes about a minute.
"""
from __future__ import annotations

import argparse
import io
import re
import hashlib
import json
import os
import shutil
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULTS = {  # paths are given by the builder app (its Settings page) or on the command line
    'iso': '',  # the clean game ISO
    'out': '',  # the test ISO; empty: beside the clean one, named "... (mod test).iso"
    'mods': os.path.join(HERE, 'RDRMods'),
    'dave': '',  # the folder with EdnessP's dave.py; empty: beside this script
    'cache': os.path.join(HERE, '_ps2cache'),
    'pcsx2': '',
    'recomp': '',  # Xbox: the recomp's RDR.exe (--run)
}
XBOX = [False]  # --platform xbox: the game is a folder (the Xbox disc's files), archives built as the originals
STATE_FILE = 'rdr_data_editor.json'  # Xbox: in the output folder, what the last build wrote there
SECTOR = 2048
MODES_FILE = 'master_modes.txt'  # in the mods folder: <file name> TAB replace|add, a line a Master file
FOLDER_FILE = 'rdr_folder.txt'   # in an exported .dat folder: the disc folder it belongs in (lvls_m2, ...); never built
TAIL_PAD = 2 * 1024 * 1024  # zeros after an archive appended to the image's end
CHARS = "\x00 #$()-./?0123456789_abcdefghijklmnopqrstuvwxyz~\x7F"  # Dave compressed name characters


# ---------------------------------------------------------------------------------------------------------------
# ISO9660

class Iso:
    def __init__(self, path, mode='rb'):
        self.f = open(path, mode)
        self.f.seek(16 * SECTOR)
        pvd = self.f.read(SECTOR)
        if pvd[1:6] != b'CD001':
            raise ValueError(f'{path}: no ISO9660 volume descriptor')
        self.pvd_at = 16 * SECTOR
        self.blocks = struct.unpack_from('<I', pvd, 80)[0]
        self.root = self._record(pvd, 156, self.pvd_at + 156)

    @staticmethod
    def _record(buf, o, absolute):
        n = buf[o]
        lba = struct.unpack_from('<I', buf, o + 2)[0]
        size = struct.unpack_from('<I', buf, o + 10)[0]
        flags = buf[o + 25]
        name = buf[o + 33:o + 33 + buf[o + 32]].decode('latin-1')
        return {'at': absolute, 'len': n, 'lba': lba, 'size': size, 'dir': bool(flags & 2), 'name': name}

    def listdir(self, rec):
        self.f.seek(rec['lba'] * SECTOR)
        data = self.f.read(rec['size'])
        out, o = [], 0
        while o < len(data):
            n = data[o]
            if n == 0:  # records do not cross sectors: on to the next
                o = (o // SECTOR + 1) * SECTOR
                continue
            r = self._record(data, o, rec['lba'] * SECTOR + o)
            if r['name'] not in ('\x00', '\x01'):
                out.append(r)
            o += n
        return out

    def files(self):
        """{path (lower case, / separated, no ;1): record} of every file."""
        out, todo = {}, [('', self.root)]
        while todo:
            prefix, rec = todo.pop()
            for r in self.listdir(rec):
                name = r['name'].split(';')[0].lower()
                path = f'{prefix}{name}'
                if r['dir']:
                    todo.append((path + '/', r))
                else:
                    out[path] = r
        return out

    def read(self, rec):
        self.f.seek(rec['lba'] * SECTOR)
        return self.f.read(rec['size'])

    def replace(self, rec, data, extents):
        """Write data as rec's file: over the old one if it fits before the next file's extent, else at the end."""
        room = min((e for e in extents if e > rec['lba']), default=rec['lba'] + (rec['size'] + SECTOR - 1) // SECTOR)
        room = (room - rec['lba']) * SECTOR
        if len(data) <= room:
            lba = rec['lba']
        else:
            self.f.seek(0, 2)
            end = self.f.tell()
            lba = (end + SECTOR - 1) // SECTOR
        self.f.seek(lba * SECTOR)
        self.f.write(data)
        pad = -len(data) % SECTOR
        if pad:
            self.f.write(bytes(pad))
        if lba != rec['lba']:
            # the game reads ahead in whole blocks: a file ending the image has to have room after it, or the read
            # goes past the end and the emulator refuses it over and over (the load hangs). The unedited disc has
            # spare sectors after its last file too
            self.f.write(bytes(TAIL_PAD))
        # the directory record: extent and size, both byte orders
        self.f.seek(rec['at'] + 2)
        self.f.write(struct.pack('<I', lba) + struct.pack('>I', lba) + struct.pack('<I', len(data)) +
                     struct.pack('>I', len(data)))
        self.f.seek(0, 2)
        blocks = self.f.tell() // SECTOR
        if blocks > self.blocks:
            self.blocks = blocks
            self.f.seek(self.pvd_at + 80)
            self.f.write(struct.pack('<I', blocks) + struct.pack('>I', blocks))
        return 'in place' if lba == rec['lba'] else 'appended'

    def add_file(self, dir_path, name, data):
        """A new file in a directory of the image (lvls_m2/MYMAP2.DAT): its data appended at the end (with room
        after it), its record put in the directory's own sector(s) in name order, the directory's size grown in its
        '.' record and in its parent's record. A directory has room up to its whole sector(s) (the disc's have about
        30 records free). Returns what restore_new needs to take it out again."""
        rec = self.root
        for part in [x for x in dir_path.replace('\\', '/').split('/') if x]:
            hit = [r for r in self.listdir(rec) if r['dir'] and r['name'].upper() == part.upper()]
            if not hit:
                raise ValueError(f'no folder {dir_path} on the disc')
            rec = hit[0]
        if any(r['dir'] for r in self.listdir(rec)):
            raise ValueError(f'{dir_path}: a folder with folders in it is not handled')
        alloc = -(-rec['size'] // SECTOR) * SECTOR
        self.f.seek(rec['lba'] * SECTOR)
        old = self.f.read(alloc)
        recs, o = [], 0
        while o < rec['size']:
            n = old[o]
            if n == 0:
                o = (o // SECTOR + 1) * SECTOR
                continue
            recs.append(bytearray(old[o:o + n]))
            o += n
        iso_name = (name.upper() + ';1').encode('ascii')
        if any(bytes(r[33:33 + r[32]]) == iso_name for r in recs):
            raise ValueError(f'{dir_path}/{name} is on the disc already')
        stem, _, ext = name.upper().partition('.')
        if len(stem) > 8 or len(ext) > 3 or not re.fullmatch(r'[A-Z0-9_]+', stem + ext):
            raise ValueError(f'{name}: a disc file name is 8 letters, digits or _ at most, then .DAT')
        self.f.seek(0, 2)
        lba = -(-self.f.tell() // SECTOR)
        self.f.seek(lba * SECTOR)
        self.f.write(data)
        self.f.write(bytes(-len(data) % SECTOR))
        self.f.write(bytes(TAIL_PAD))
        r = bytearray(33 + len(iso_name) + ((33 + len(iso_name)) % 2))
        r[0] = len(r)
        struct.pack_into('<I', r, 2, lba)
        struct.pack_into('>I', r, 6, lba)
        struct.pack_into('<I', r, 10, len(data))
        struct.pack_into('>I', r, 14, len(data))
        r[18:25] = (recs[2] if len(recs) > 2 else recs[0])[18:25]  # a neighbour's date
        struct.pack_into('<H', r, 28, 1)
        struct.pack_into('>H', r, 30, 1)
        r[32] = len(iso_name)
        r[33:33 + len(iso_name)] = iso_name
        files = sorted(recs[2:] + [r], key=lambda x: bytes(x[33:33 + x[32]]))
        out = bytearray()
        for x in recs[:2] + files:
            if len(out) // SECTOR != (len(out) + len(x) - 1) // SECTOR:
                out += bytes(-len(out) % SECTOR)  # a record never crosses a sector
            out += x
        if len(out) > alloc:
            raise ValueError(f'{dir_path} has no room left for another file')
        size = alloc if rec['size'] % SECTOR == 0 else len(out)
        struct.pack_into('<I', out, 10, size)  # the '.' record
        struct.pack_into('>I', out, 14, size)
        self.f.seek(rec['lba'] * SECTOR)
        self.f.write(bytes(out) + bytes(alloc - len(out)))
        self.f.seek(rec['at'] + 10)  # the folder's record in its parent
        self.f.write(struct.pack('<I', size) + struct.pack('>I', size))
        self.f.seek(0, 2)
        blocks = self.f.tell() // SECTOR
        if blocks > self.blocks:
            self.blocks = blocks
            self.f.seek(self.pvd_at + 80)
            self.f.write(struct.pack('<I', blocks) + struct.pack('>I', blocks))
        return {'new': True, 'lba': lba, 'dir_lba': rec['lba'], 'dir_at': rec['at'], 'dir_size': rec['size'],
                'dir_old': old.hex()}

    def restore_new(self, rec):
        """A file add_file put in taken out again: its folder's sector(s) and size as they were."""
        self.f.seek(rec['dir_lba'] * SECTOR)
        self.f.write(bytes.fromhex(rec['dir_old']))
        self.f.seek(rec['dir_at'] + 10)
        self.f.write(struct.pack('<I', rec['dir_size']) + struct.pack('>I', rec['dir_size']))

    def read_head(self, rec):
        """An archive's header and tables (no file data)."""
        self.f.seek(rec['lba'] * SECTOR)
        head = self.f.read(16)
        _c, info, names = struct.unpack_from('<3I', head, 4)
        self.f.seek(rec['lba'] * SECTOR)
        return self.f.read(0x800 + info + names)

    def close(self):
        self.f.close()


class GameFolder:
    """The Xbox game as a folder of its disc's files (default.xbe, swat.dat, lvls_sng/...): read as Iso is."""

    def __init__(self, path, mode='rb'):
        if not os.path.isdir(path):
            raise ValueError(f'{path}: no such folder')
        self.root = path

    def files(self):
        """{path (lower case, / separated): record} of every file."""
        out = {}
        for root, _dirs, fs in os.walk(self.root):
            for f in fs:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, self.root).replace(os.sep, '/').lower()
                out[rel] = {'path': full, 'size': os.path.getsize(full), 'lba': 0, 'at': 0}
        return out

    def read(self, rec):
        with open(rec['path'], 'rb') as f:
            return f.read()

    def read_head(self, rec):
        with open(rec['path'], 'rb') as f:
            head = f.read(16)
            _c, info, names = struct.unpack_from('<3I', head, 4)
            f.seek(0)
            return f.read(0x800 + info + names)

    def close(self):
        pass


def open_game(path, mode='rb'):
    """The game: an ISO image (PS2) or a folder of the disc's files (Xbox)."""
    return GameFolder(path, mode) if os.path.isdir(path) else Iso(path, mode)


def game_stamp(path):
    """What tells whether the unedited game changed (the index is made again then): size and date."""
    if os.path.isdir(path):
        n, t = 0, 0
        for root, _d, fs in os.walk(path):
            for f in fs:
                if f.lower().endswith(('.dat', '.zip')):
                    st = os.stat(os.path.join(root, f))
                    n, t = n + st.st_size, max(t, int(st.st_mtime))
        return f'dir-{n}-{t}'
    return f'{os.path.getsize(path)}-{int(os.path.getmtime(path))}'


# ---------------------------------------------------------------------------------------------------------------
# Dave / DAVE archives

def archive_info(data):
    """(names, compressed names, compressed files, has directories, file alignment in 16 byte units) of a Dave/DAVE
    archive, as dave.py reads it."""
    magic = data[:4]
    if magic not in (b'DAVE', b'Dave'):
        raise ValueError('not a DAVE/Dave archive')
    count, info_size, _name_size = struct.unpack_from('<3I', data, 4)
    names, name, compressed, gcd = [], '', False, 0
    import math
    for i in range(count):
        name_offs, off, full, comp = struct.unpack_from('<4I', data, 0x800 + 16 * i)
        gcd = math.gcd(gcd, off)
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
        if full != comp and not name.endswith('/'):
            compressed = True
    align = 128  # 2048 bytes, dave.py's default
    for unit in (128, 64, 32, 16, 8, 4, 2, 1):
        if gcd % (unit * 16) == 0:
            align = unit
            break
    return names, magic == b'Dave', compressed, any(n.endswith('/') for n in names), align


def _table(data):
    """{lower case name: (offset, full size, stored size)} of an archive's files."""
    names = archive_info(data)[0]
    out = {}
    for i, n in enumerate(names):
        _no, off, full, comp = struct.unpack_from('<4I', data, 0x800 + 16 * i)
        out[n.lower()] = (off, full, comp)
    return out


def file_table(head):
    """{lower case name: (offset, compressed)} of an archive's files, from its header and tables."""
    names = archive_info(head)[0]
    out = {}
    for i, n in enumerate(names):
        _no, off, full, comp = struct.unpack_from('<4I', head, 0x800 + 16 * i)
        if not n.endswith('/'):
            out[n.lower()] = (off, full != comp)
    return out


def game_layout(path, table=None, order=None):
    """An archive dave.py built, laid out as the game's own are (PS2 and Xbox, every archive):
      - each file 32 bytes after the last, but a file that would cross a 2048 byte sector starts at the next one
        (the Xbox game cannot read a file across a sector: the recomp crashed at boot)
      - the files in the order the game loads them, which is the original's data order (not its name order: built
        in name order, a level's files are spread over the archive and the disc seeks back and forth while loading)
      - each file compressed or stored as the original's of its name
    table: the original's file_table; order: {lower case name: sort key} for files the original has not (or for an
    archive with no original), see new_archive_order. Rewrites the entry table; returns how many files' compression
    was changed to match."""
    import zlib
    d = open(path, 'rb').read()
    count, info, nsz = struct.unpack_from('<3I', d, 4)
    names = archive_info(d)[0]
    table = table or {}
    order = order or {}
    # The packing the original uses: 32 bytes for the levels, front.dat and swat.dat; banks.dat packs at 16 (its
    # .hd headers sit 16 bytes apart: 310 of 310 offsets follow the 16 byte rule, 284 the 32 byte one). Read from
    # the original's offsets, so an archive is laid out exactly as shipped.
    pack = 16 if any(off % 32 == 16 for off, _c in table.values()) else 32
    # An archive whose files are ALL stored (banks.dat, pupshow.dat, the streams: data the IOP streams straight
    # off the disc) keeps a new file stored too.
    all_stored = bool(table) and not any(c for _o, c in table.values())
    ents = [list(struct.unpack_from('<4I', d, 0x800 + 16 * i)) for i in range(count)]
    start = 0x800 + info + nsz
    last = (float('inf'),)

    def key(i):
        n = names[i].lower()
        if n in table:
            return (table[n][0],)
        return order.get(n, last) + (ents[i][1],)
    files = sorted((i for i in range(count) if not names[i].endswith('/')), key=key)
    out = bytearray(d[:start])
    changed = 0
    for i in files:
        no, off, full, comp = ents[i]
        data = d[off:off + comp]
        want = table.get(names[i].lower())
        if want is None and all_stored:
            want = (0, False)
        if want is not None:
            if full != comp and not want[1]:  # the game keeps it stored
                data = zlib.decompress(data, -15)
                changed += 1
            elif full == comp and want[1] and full:
                z = zlib.compressobj(9, zlib.DEFLATED, -15)
                packed = z.compress(data) + z.flush()
                if len(packed) < len(data):
                    data = packed
                    changed += 1
        at = -(-len(out) // pack) * pack
        if data and at // SECTOR != (at + len(data) - 1) // SECTOR:
            at = -(-len(out) // SECTOR) * SECTOR
        out += bytes(at - len(out))
        out += data
        ents[i] = [no, at, full, len(data)]
    out += bytes(-len(out) % SECTOR)
    for i in range(count):
        if not names[i].endswith('/'):
            struct.pack_into('<4I', out, 0x800 + 16 * i, *ents[i])
    with open(path, 'wb') as f:
        f.write(out)
    return changed


def new_archive_order(p, folder, iso, dats, archives):
    """The load order for a new archive (lvls_m2/mymap2.dat ...), from the game's archives it was made from: the
    clean archive of its folder sharing the most files (Ranch's for a Showdown map: the shared files and the
    template's own, its level name swapped), and for the files of another level's place (a story level's,
    lvl_<story>_...) that level's archive, in its order, where the template's own level files were.
    {lower case name: sort key}. folder: a folder, or the archive's files in memory {name: bytes}."""
    fresh = [k for k in folder if '/' not in k] if isinstance(folder, dict) else os.listdir(folder)
    low = [f.lower() for f in fresh]
    stem = 'lvl_' + os.path.splitext(p.rsplit('/', 1)[-1])[0].lower()
    group = p.rsplit('/', 1)[0] + '/'
    best, ref = -1, None
    for q in archives:
        if q.startswith(group) and q in dats:
            t = file_table(iso.read_head(dats[q]))
            n = sum(1 for f in low if f in t)
            if n > best:
                best, ref = n, (q, t)
    if ref is None:
        return {}
    q, t = ref
    rstem = 'lvl_' + os.path.splitext(q.rsplit('/', 1)[-1])[0].lower()
    order, rest = {}, []
    for f in low:
        if f in t:
            order[f] = (t[f][0], 0)
        elif stem in f and f.replace(stem, rstem) in t:
            order[f] = (t[f.replace(stem, rstem)][0], 0)
        else:
            rest.append(f)
    if rest:
        # another level's place: the archive (and level name) that has most of them
        anchor = min((v[0] for k, v in t.items() if k.startswith(rstem)), default=float('inf'))
        best, story = 0, None
        for a2 in archives:
            if a2 == q or a2 not in dats or not a2.startswith('lvls_'):
                continue
            t2 = file_table(iso.read_head(dats[a2]))
            # its level names: lvl_<x> of its lvl_<x>.lvl files (tomb2.dat holds lvl_tomb's)
            for cand in {k[:-4] for k in t2 if k.startswith('lvl_') and k.endswith('.lvl')}:
                hits = sum(1 for f in rest if stem in f and f.replace(stem, cand) in t2)
                if hits > best:
                    best, story = hits, (t2, cand)
        if story:
            t2, cand = story
            for f in rest:
                g = f.replace(stem, cand)
                if g in t2:
                    order[f] = (anchor, 1, t2[g][0])
    return order


def load_dave(path):
    sys.path.insert(0, path)
    try:
        import dave
    finally:
        sys.path.remove(path)
    return dave


# ---------------------------------------------------------------------------------------------------------------

# (compress files, compress names) by group; --<group>-files / --<group>-names change them. swat.dat and the levels
# (front.dat too) are Dave compressed, the other archives DAVE uncompressed (the game stops at its first load with an
# uncompressed swat.dat)
COMPRESSION = {'swat': (True, True), 'levels': (True, True), 'banks': (False, False), 'pupshow': (False, False),
               'streams': (False, False)}
GROUP_NAMES = {'swat': 'swat.dat', 'levels': 'the levels (lvls_* and front.dat)', 'banks': 'banks.dat',
               'pupshow': 'pupshow.dat', 'streams': 'streams.dat'}


def group_of(archive):
    name = os.path.splitext(os.path.basename(archive))[0].lower()
    return name if name in ('swat', 'banks', 'pupshow', 'streams') else 'levels'


def master_modes(mods):
    """{lower case file name: 'replace' | 'add'} for the Master files, from the mods folder's master_modes.txt."""
    out = {}
    path = os.path.join(mods, MODES_FILE)
    if os.path.isfile(path):
        for line in open(path, encoding='utf-8', errors='ignore'):
            name, _, mode = line.strip().partition('	')
            if name and mode.strip().lower() in ('replace', 'add'):
                out[name.lower()] = mode.strip().lower()
    return out


def master_mode(name, modes, holders):
    """How a Master file goes in: 'replace' (only into the .dat files with a file of its name) or 'add' (into every
    level .dat, front.dat and swat.dat). A file the game has not can only be added; the default for one it has is
    replace."""
    if not any(master_target(p) for p in holders.get(name, ())):
        return 'add'
    return modes.get(name, 'replace')


def master_target(archive):
    """The archives Master files go into: the levels (lvls_*), front.dat and swat.dat (not banks, pupshow, streams)."""
    a = archive.replace(os.sep, '/').lower()
    return a.startswith('lvls_') or a in ('front.dat', 'swat.dat')


def build_settings(archive, info=None):
    """How an archive is built: (compress files, compress names, directory entries, alignment in 16 bytes, level).
    PS2: its group's compression (the app's settings), directory entries as the original (none), 32 byte alignment
    (game_layout then lays it out as the original: load order, sectors).
    Xbox: as the original was (info, from the index; a new one as the level archives: Dave, files compressed, no
    directory entries, 32 byte alignment). The Xbox game keeps an archive's whole table in memory: swat.dat (45881
    files) built with directory entries and other settings does not fit its heap (Heap overrun at boot)."""
    if XBOX[0]:
        if info:
            return info['compfiles'], info['compnames'], info['dirs'], info['align'], 6
        return True, True, False, 2, 6
    files, names = COMPRESSION[group_of(archive)]
    if info:  # as the original: no directory entries (the layout packs the files)
        return files, names, info['dirs'], 2, 6
    return files, names, False, 2, 6


def quiet_prompts(dave):
    """dave.py asks before overwriting (input()): answered yes, as the app has no console."""
    dave.exists_prompt = lambda output, prompt: True


def extract(dave, archive, folder):
    """An archive's files into folder/<archive name> (folder itself when it is already named so)."""
    name = os.path.splitext(os.path.basename(archive))[0]
    out = folder if os.path.basename(os.path.normpath(folder)).lower() == name.lower() else os.path.join(folder, name)
    quiet_prompts(dave)
    names, cn, cf, dirs, align = archive_info(open(archive, 'rb').read())
    print(f'{os.path.basename(archive)}: {len(names)} entries, names {"compressed (Dave)" if cn else "plain (DAVE)"}, '
          f'files {"compressed" if cf else "stored"}')
    dave.read_dave(archive, out)
    print(f'Extracted to {out}')
    return 0


def pack(dave, folder, output, files, names):
    """A folder as an archive (.dat or .zip), as the game's: directory entries, 2048 byte alignment, level 6."""
    if output.lower().endswith('.zip') and names:
        print('A .zip archive must have plain names (DAVE): compressing names turned off')
        names = False
    quiet_prompts(dave)
    if os.path.exists(output):
        os.remove(output)
    dave.build_dave(folder, output, files, 0, 6, names, True, 128, False)
    print(f'Built {output} ({os.path.getsize(output):,} bytes)')
    return 0


def mod_files(folder, archives=()):
    """The mods: (loose, targeted, every, master).
        master    {lower case file name: path}: files in the Master folder, each into every level .dat, front.dat and
                  swat.dat (replacing a file of
                  its name, else added at the archive's top)
        loose     {lower case file name: path}: other files outside the archive folders (the shallower of two with one
                  name), each into every archive that has a file of that name
        targeted  {archive: [(path inside it, lower case, / separated), file path]}: files in a folder named as an
                  archive, laid out as on the disc (swat.dat/, lvls_sng/tomb.dat/), each into that archive only
        every     {mod key (its path in the mods folder, lower case, / separated): file path}
    archives: the disc's archive paths (swat.dat, lvls_sng/tomb.dat, ...)."""
    folders = {a.lower(): a for a in archives}
    loose, depth, targeted, every, master = {}, {}, {}, {}, {}
    for root, _dirs, files in os.walk(folder):
        rel = os.path.relpath(root, folder).replace(os.sep, '/')
        rel = '' if rel == '.' else rel.lower()
        parts = rel.split('/') if rel else []
        archive, inner = None, ''
        for n in range(1, len(parts) + 1):
            if '/'.join(parts[:n]) in folders:
                archive, inner = folders['/'.join(parts[:n])], '/'.join(parts[n:])
                break
        for f in files:
            k = f.lower()
            if k in ('readme.txt', MODES_FILE, FOLDER_FILE):  # the folder's own note; Master's modes; its home
                continue
            path = os.path.join(root, f)
            every[(rel + '/' if rel else '') + k] = path
            if archive:
                targeted.setdefault(archive, []).append(((inner + '/' if inner else '') + k, path))
            elif parts and parts[0] == 'master':
                master.setdefault(k, path)
            elif k not in loose or len(parts) < depth[k]:
                loose[k], depth[k] = path, len(parts)
    return loose, targeted, every, master


def new_archives(mods, archives) -> dict:
    """Folders in the mods folder named like an archive the disc has not (lvls_m2/mymap2.dat/), in one of the disc's
    archive folders: {archive path: folder}. Each is built from its folder alone and added to the disc."""
    known = {x.lower() for x in archives}
    dirs = {x.rsplit('/', 1)[0].lower() for x in archives if '/' in x}
    out = {}
    for d in sorted(dirs):
        top = os.path.join(mods, *d.split('/'))
        if not os.path.isdir(top):
            continue
        for f in os.listdir(top):
            full = os.path.join(top, f)
            key = f'{d}/{f.lower()}'
            if os.path.isdir(full) and f.lower().endswith('.dat') and key not in known and \
                    any(files for _r, _d, files in os.walk(full)):
                out[key] = full
    return out


def load_index(a, iso):
    """Which names each archive on the clean ISO holds (and how it was built), cached by the ISO's size and date."""
    files = iso.files()
    dats = {p: r for p, r in files.items() if p.endswith(('.dat', '.zip'))}
    stamp = game_stamp(a.iso)
    index_path = os.path.join(a.cache, 'index.json')
    index = json.load(open(index_path)) if os.path.isfile(index_path) else {}
    if index.get('stamp') != stamp or index.get('version') != 2:
        print('Indexing the archives on the clean ISO (once)...')
        index = {'stamp': stamp, 'version': 2, 'archives': {}}
        for p, r in sorted(dats.items()):
            try:
                names, cn, cf, dirs, align = archive_info(iso.read(r))
            except ValueError:
                continue
            index['archives'][p] = {'names': names, 'compnames': cn, 'compfiles': cf, 'dirs': dirs, 'align': align}
        json.dump(index, open(index_path, 'w'))
    # for the builder app: the archives in the disc's order, and which archives each name goes into
    with open(os.path.join(a.cache, 'archives.tsv'), 'w', encoding='utf-8', newline='\n') as f:
        for p in sorted(index['archives']):
            f.write(p + '\n')
    holders = {}
    for p, info in index['archives'].items():
        for n in info['names']:
            holders.setdefault(os.path.basename(n).lower(), set()).add(p)
    contents = os.path.join(a.cache, 'contents.tsv')  # every archive's files, for the app's Add from game
    if not os.path.isfile(contents) or os.path.getmtime(contents) < os.path.getmtime(index_path):
        with open(contents, 'w', encoding='utf-8', newline='\n') as f:
            for p in sorted(index['archives']):
                for n in index['archives'][p]['names']:
                    if not n.endswith('/'):
                        f.write(p + '\t' + n + '\n')
    with open(os.path.join(a.cache, 'holders.tsv'), 'w', encoding='utf-8', newline='\n') as f:
        for k in sorted(holders):
            f.write(k + '\t' + ','.join(sorted(holders[k])) + '\n')
    return index, dats, stamp, holders


_ENTRY_CACHE = []  # [(archive bytes, names, {path: index}, {name: index})]: the last few archives read from


def _entry_index(data):
    """An archive's names and lookups, parsed once while it is among the last eight read (archive_info walks the
    whole table: reading a few hundred files from swat.dat one by one took minutes)."""
    for k, item in enumerate(_ENTRY_CACHE):
        if item[0] is data:
            if k:
                _ENTRY_CACHE.insert(0, _ENTRY_CACHE.pop(k))
            return item[1:]
    names = archive_info(data)[0]
    full, short = {}, {}
    for i, n in enumerate(names):
        low = n.lower()
        full.setdefault(low, i)
        short.setdefault(low.rsplit('/', 1)[-1], i)
    _ENTRY_CACHE.insert(0, (data, names, full, short))
    del _ENTRY_CACHE[8:]
    return names, full, short


def read_entry(data, name):
    """One file of a Dave/DAVE archive (its bytes) by its path or name (any case); None if it has none."""
    import zlib
    names, full_ix, short_ix = _entry_index(data)
    want = name.lower().replace('\\', '/')
    hit = full_ix.get(want)
    if hit is None:
        hit = short_ix.get(want.rsplit('/', 1)[-1])
    if hit is None:
        return None, None
    _no, off, full, comp = struct.unpack_from('<4I', data, 0x800 + 16 * hit)
    raw = data[off:off + comp]
    return names[hit], (zlib.decompress(raw, -15) if full != comp else raw)


def extract_all(a, iso, index, dats):
    """Every .dat of the clean ISO into a.to, laid out as on the disc: a folder named after each archive
    (swat.dat/, lvls_sng/tomb.dat/, ...) holding its files. Archives already extracted into the cache are copied
    from there; the others are extracted (into the cache too, which later builds use)."""
    archives = sorted(index['archives'], key=lambda p: (p.count('/'), p))
    os.makedirs(a.to, exist_ok=True)
    dave = None
    t0 = time.time()
    total = 0
    for i, p in enumerate(archives, 1):
        clean = os.path.join(a.cache, 'clean', p.replace('/', '_'))
        if not os.path.isdir(clean):
            if dave is None:
                dave = load_dave(a.dave)
                quiet_prompts(dave)
            src = os.path.join(a.cache, 'src.dat')
            with open(src, 'wb') as f:
                f.write(iso.read(dats[p]))
            dave.read_dave(src, clean)
            os.remove(src)
        out = os.path.join(a.to, *p.split('/'))
        if os.path.isdir(out):
            shutil.rmtree(out)
        shutil.copytree(clean, out)
        n = sum(len(fs) for _r, _d, fs in os.walk(out))
        total += n
        print(f'[{i}/{len(archives)}] {p}: {n} files', flush=True)
    print(f'Extracted {len(archives)} archives ({total} files) to {a.to} in {time.time() - t0:.0f} s')
    return 0


TEXT_EXTS = ('.lvl', '.znes', '.xznes', '.bnds', '.lnes', '.bxes', '.spwn', '.aisp', '.lgt', '.shadows', '.fog', '.glows', '.sneak',
             '.fade', '.enm', '.flags', '.duel', '.csv', '.txt', '.hud', '.sub', '.film', '.mmb', '.hb', '.strtbl', '.cam',
             '.swui', '.tbl', '.pup')


def _clean(a, iso, dats, p):
    """An archive of the clean ISO extracted into the cache (once): its folder."""
    clean = os.path.join(a.cache, 'clean', p.replace('/', '_'))
    if not os.path.isdir(clean):
        dave = load_dave(a.dave)
        quiet_prompts(dave)
        src = os.path.join(a.cache, 'src.dat')
        with open(src, 'wb') as f:
            f.write(iso.read(dats[p]))
        dave.read_dave(src, clean)
        os.remove(src)
    return clean


def _copy_renamed(src_dir, dst_dir, files, old, new):
    """Files copied, a level's name old (lvl_saloon) turned into new in their names and inside the text ones."""
    pat = re.compile(re.escape(old) + r'(?=[_.]|$)', re.I)
    os.makedirs(dst_dir, exist_ok=True)
    renamed = 0
    for f in files:
        data = open(os.path.join(src_dir, f), 'rb').read()
        name = pat.sub(new, f)
        if name != f:
            renamed += 1
            if f.lower().endswith(TEXT_EXTS) and b'\0' not in data[:2048]:
                data = pat.sub(new, data.decode('latin-1')).encode('latin-1')
        with open(os.path.join(dst_dir, name), 'wb') as fh:
            fh.write(data)
    return renamed


def _mod_or_clean(a, iso, dats, archive, name):
    """A game text file to change: the mods folder's copy when there is one, else the clean ISO's."""
    mine = os.path.join(a.mods, *archive.split('/'), name)
    if os.path.isfile(mine):
        return mine, open(mine, encoding='latin-1', newline='').read()
    clean = os.path.join(_clean(a, iso, dats, archive), name)
    return mine, open(clean, encoding='latin-1', newline='').read() if os.path.isfile(clean) else None


# a level's own files that make up its place (geometry, collision, paths, volumes, spawns, lights); the rest of its
# files (duels, enemies, films, scripts) are its missions'
PLACE_EXTS = ('.rsc', '.rnt', '.znes', '.xrsc', '.xrnt', '.xznes', '.xtex', '.bnds', '.lnes', '.bxes', '.spwn', '.aisp', '.lgt', '.shadows', '.fog', '.glows',
              '.sneak', '.fade', '.tex', '.lvl')


def spawn_sets(text):
    out = []
    for part in re.split(r'NumSpawnPoints\d+[ \t]+\d+', text)[1:]:
        out.append([tuple(map(float, m)) for m in
                    re.findall(r'Position\s+(\S+)\s+(\S+)\s+(\S+)\s+Heading\s+(\S+)', part)])
    return out


def fill_spawns(spwn_text, aisp_text, sizes):
    """A .spwn with sets of at least the given sizes (a Showdown map: 4 sets of 4), filled from the level's spawn and
    AI spawn spots, as far apart as possible (as the Blender add-on's levelexport.fill_spawns)."""
    import math
    sets = spawn_sets(spwn_text)
    while len(sets) < len(sizes):
        sets.append([])
    cand = [p[:3] for st in sets for p in st]
    for m in re.finditer(r'^\s*World\s+(\S+)\s+(\S+)\s+(\S+)', aisp_text, re.M):
        cand.append(tuple(map(float, m.groups())))
    uniq = []
    for c in cand:
        if all(math.dist(c, u) > 0.5 for u in uniq):
            uniq.append(c)
    if not uniq:
        return spwn_text
    mid = tuple(sum(c[k] for c in uniq) / len(uniq) for k in range(3))
    used, out = set(), []
    for k, want in enumerate(sizes):
        st = list(sets[k])
        pts = [q[:3] for q in st]
        while len(st) < want:
            pool = [c for n, c in enumerate(uniq) if n not in used and c not in pts] or \
                [c for c in uniq if c not in pts] or uniq
            ref = pts or [mid]
            best = max(pool, key=lambda c: min(math.dist(c, r) for r in ref))
            used.add(uniq.index(best))
            st.append((best[0], best[1], best[2], math.atan2(mid[0] - best[0], mid[2] - best[2])))
            pts.append(best)
        out.append(f'NumSpawnPoints{k + 1}\t{len(st)} ')
        for x, y, z, h in st:
            out += [f'Position\t{x:.6f}\t{y:.6f}\t{z:.6f} ', f'Heading\t{h:.6f} ', '']
    return '\r\n'.join(out) + '\r\n'


# High Noon (Showdown's duels) puts the duelists on the .aisp's DuelSpawnpoints (4) and TwoPDuelSpawnpoints (its first
# 2): four points round a middle, back to back, facing out. Every Showdown map has them; story levels have not (High
# Noon then shows a black screen). Ranch's, from their middle: (x, z, heading in degrees)
DUEL_LAYOUT = [(0.30329, 3.46699, 8.380890), (3.63498, -0.53665, 97.156600),
               (-3.80925, 0.37093, -82.662086), (-0.69436, -3.38107, -174.271255)]


def duel_middle(folder, stem):
    """Where a level's duel goes: its own duel spot (the .aisp's PlayerDuel group: a story level's duel, out in the
    open), else its first player spawn point, else the middle of its spawn points. (line_pos_01 is not it: on the
    Showdown maps it lies 10 to 150 m from their duel spots.)"""
    aisp = os.path.join(folder, stem + '.aisp')
    if os.path.isfile(aisp):
        t = open(aisp, encoding='latin-1').read()
        m = re.search(r'(?m)^\tPlayerDuel\s*\{\s*World\s+(\S+)\s+(\S+)\s+(\S+)', t)
        if m:
            return tuple(map(float, m.groups()))
    spwn = os.path.join(folder, stem + '.spwn')
    if os.path.isfile(spwn):
        sets = spawn_sets(open(spwn, encoding='latin-1').read())
        if sets and sets[0]:
            return sets[0][0][:3]
    return None


def add_duel_spawns(folder, stem):
    """A level's .aisp given the duel groups High Noon needs (DuelSpawnpoints, TwoPDuelSpawnpoints), Ranch's layout
    round its duel middle, when it has none. Returns whether it added them."""
    path = os.path.join(folder, stem + '.aisp')
    if not os.path.isfile(path):
        return False
    text = open(path, encoding='latin-1', newline='').read()
    if re.search(r'(?m)^\tDuelSpawnpoints\s*$', text):
        return False
    mid = duel_middle(folder, stem)
    if mid is None:
        return False
    nl = '\r\n' if '\r\n' in text else '\n'
    rows = [f'\t\tWorld\t{mid[0] + dx:.6f}\t{mid[1]:.6f}\t{mid[2] + dz:.6f} \t{h:.6f} ' for dx, dz, h in DUEL_LAYOUT]
    # 4 player maps have only DuelSpawnpoints, as the game's
    block = (nl.join(['\tDuelSpawnpoints', '\t{'] + rows + ['\t}'] +
             (['\tTwoPDuelSpawnpoints', '\t{'] + rows[:2] + ['\t}'] if stem.endswith('2') else []))
             + nl)
    end = text.rstrip().rfind('}')
    text = text[:end] + block + text[end:]
    open(path, 'w', encoding='latin-1', newline='').write(text)
    return True


def strip_walkrun(folder, stem):
    """A story level's walk/run rooms (guns holstered, no jumping) taken out; box files left empty deleted and taken
    off the .lvl. Returns how many."""
    taken = 0
    lvl_path = os.path.join(folder, stem + '.lvl')
    lvl = open(lvl_path, encoding='latin-1', newline='').read() if os.path.isfile(lvl_path) else None
    for f in sorted(os.listdir(folder)):
        if not f.lower().endswith('.bxes'):
            continue
        path = os.path.join(folder, f)
        text = open(path, encoding='latin-1', newline='').read()
        if 'WALKRUNROOM' not in text.upper():
            continue
        nl = '\r\n' if '\r\n' in text else '\n'
        boxes = re.findall(r'^Box \S+ \{.*?^\}' + re.escape(nl) + r'?(?:' + re.escape(nl) + r')?', text, re.S | re.M)
        keep = [x for x in boxes if 'WALKRUNROOM' not in x.split('{', 1)[0].upper()]
        taken += len(boxes) - len(keep)
        if keep:
            open(path, 'w', encoding='latin-1', newline='').write(f'NumBoxes {len(keep)}{nl}{nl}' + ''.join(keep))
        else:
            os.remove(path)
            if lvl is not None:
                lvl = re.sub(r'(?im)^[ \t]*' + re.escape(f) + r'[ \t]*\r?\n', '', lvl)
    if lvl is not None:
        open(lvl_path, 'w', encoding='latin-1', newline='').write(lvl)
    return taken


def blank_slate(folder, stem, pmt_chunks):
    """New .dat "Blank slate": after strip_geometry, a level with nothing of the template's but its spawns (and its
    lights, fog, sounds and shared files): a level bundle of the template's physics materials (PMT0) and one bound that
    stops nothing, far under the level, in lvl_<x>_custom.bnds -- somewhere for the Studio's Collision editor and model
    importer to add to -- and one box and one line kept of each box / line file (every level of the game lists some:
    the Xbox crashed loading a level with none). -> notes."""
    import sys as _sys
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in _sys.path:
        _sys.path.insert(0, here)
    from rdrformats import bnd_write, rscwrite
    v = [(0.0, -1000.0, 0.0), (0.001, -1000.0, 0.0), (0.0, -1000.0, 0.001)]
    pmt_num = 1
    chunks = list(pmt_chunks)
    if chunks:
        names = [d[8:40].split(b'\0')[0].lower() for _t, d in chunks]
        pmt_num = 1 + (names.index(b'default') if b'default' in names else 0)
    chunks.append(('BND0', bnd_write.write(v, [([0, 2, 1], 0)], (pmt_num,))))
    with open(os.path.join(folder, stem + '.rsc'), 'wb') as f:
        f.write(rscwrite.write_bundle(chunks, 16))
    flags = (('BoundFlagMover', 1), ('BoundFlagCamera', 1), ('BoundFlagTarget', 1), ('BoundFlagShoot', 1),
             ('BoundFlagSneak', 1), ('BoundFlagEnemy', 1), ('BoundFlagHorse', 1), ('BoundFlagPlayer', 0),
             ('BoundFlagDoor', 0), ('BoundFlagCamClip', 0), ('BoundFlagWater', 0), ('BoundFlagPlummet', 0))
    n = len(chunks)
    bnds = ['NumInstNodes 1 ', '', 'node bound_bound_grp_tttttttfffff_custom_01 {', '\tresourceIndex %d ' % n,
            '\tresourceIndexXbox %d ' % n, '\tmatrix', '\t{', '\t\t1.000000 \t0.000000 \t0.000000 ',
            '\t\t0.000000 \t1.000000 \t0.000000 ', '\t\t0.000000 \t0.000000 \t1.000000 ',
            '\t\t0.000000 \t0.000000 \t0.000000 ', '\t}', '\tattributes 12 {']
    bnds += ['\t\tBOOL\t%s\t%s' % (k, 'true' if on else 'false') for k, on in flags]
    bnds += ['\t}', '}', '']
    with open(os.path.join(folder, stem + '_custom.bnds'), 'w', newline='\r\n') as f:
        f.write('\n'.join(bnds))
    lvl = os.path.join(folder, stem + '.lvl')
    if os.path.isfile(lvl):
        text = open(lvl, 'rb').read().decode('latin-1')
        text = re.sub(r'(?is)(^\s*Bounds\s*\{)(.*?)(\})', lambda m: m.group(1) + '\r\n%s_custom.bnds  \r\n' % stem
                      + m.group(3), text, count=1, flags=re.M)
        open(lvl, 'wb').write(text.encode('latin-1'))
    kept = 0
    for f in os.listdir(folder):
        low = f.lower()
        if not (low.startswith(stem + '_') or low.startswith(stem + '.')) or not low.endswith(('.bxes', '.lnes')):
            continue
        p = os.path.join(folder, f)
        text = open(p, 'rb').read().decode('latin-1')
        m = re.search(r'(?m)^[ 	]*(?:node|Box|Line)\s+\S+\s*\{', text)
        if not m:
            continue
        depth, k = 0, m.end() - 1
        while k < len(text):
            if text[k] == '{':
                depth += 1
            elif text[k] == '}':
                depth -= 1
                if depth == 0:
                    break
            k += 1
        key = 'NumBoxes' if low.endswith('.bxes') else 'NumLineNodes'
        head = re.search(key + r'[ \t]+\d+[^\r\n]*', text)
        out = (key + ' 1 ') + '\r\n\r\n' + text[m.start():k + 1] + '\r\n'
        if head is None:
            out = text[m.start():k + 1] + '\r\n'
        open(p, 'wb').write(out.encode('latin-1'))
        kept += 1
    return ['blank slate: an empty level bundle and collision file, one box / line kept in %d file(s)' % kept]


def strip_geometry(folder, stem, blank=False):
    """A New .dat level without its template's place (New .dat: "Leave out its geometry, zones and collision", on by
    default): its level bundle (lvl_<x>.rsc / .xrsc), zone files (.znes / .xznes), bound files (.bnds), boxes
    (.bxes), lines (.lnes) and shadows taken out and the .lvl's Zones, Bounds, Lines and Boxes lists emptied, so a
    level of your own (the Blender add-on's export) goes in their place with nothing of the template's left standing
    in it. The spawns, AI spawn spots, duels, lights, fog, sounds and shared files stay. -> files taken out."""
    gone = 0
    pmt = []
    if blank:
        # the template's physics materials, for the blank bundle (its surfaces: default, wood, dirt ...)
        import sys as _sys
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in _sys.path:
            _sys.path.insert(0, here)
        from rdrformats import rsc as _R
        src = os.path.join(folder, stem + '.rsc')
        if os.path.isfile(src):
            pmt = [(c.tag, c.data) for c in _R.read_chunks(open(src, 'rb').read()) if c.tag == 'PMT0']
    for f in os.listdir(folder):
        low = f.lower()
        if not re.match(re.escape(stem) + r'[_.]', low):
            continue
        # its geometry, zones and collision -- and the files shaped to the template's place: boxes (AI avoid / rooms,
        # camera, shadow boxes), lines (paths, fire lines) and its shadows. Its spawns, AI spawn spots, duels, lights,
        # fog, sounds, navigation and view bundles stay
        if low in (stem + '.rsc', stem + '.xrsc') or low.endswith(('.znes', '.xznes', '.bnds')):
            os.remove(os.path.join(folder, f))
            gone += 1
    lvl = os.path.join(folder, stem + '.lvl')
    if os.path.isfile(lvl):
        text = open(lvl, 'rb').read().decode('latin-1')
        # its Lines and Boxes stay: every level of the game lists some (99 of 99, at least 2 and 3) and the Xbox
        # crashed loading a level with none (2026-11-17) -- they are no collision
        for section in ('Zones', 'Bounds'):
            text = re.sub(r'(?is)(^\s*' + section + r'\s*\{)(.*?)(\})', lambda m: m.group(1) + '\r\n' + m.group(3),
                          text, count=1, flags=re.M)
        open(lvl, 'wb').write(text.encode('latin-1'))
    if blank:
        for n in blank_slate(folder, stem, pmt):
            print('  ' + n)
    return gone


def new_level(a, iso, index, dats):
    """A new level as new archives in the mods folder, from the game's own:
        sng       lvls_sng/<name>.dat: the unused saloon.dat (or --template), its level lvl_saloon renamed lvl_<name>
        showdown  lvls_m2/<name>2.dat and lvls_m4/<name>4.dat: the files every Showdown map shares, and a template
                  map's own files (Ranch, or --template) renamed lvl_<name>2 / 4; plus the Showdown menu's row
                  (postermultiview.swui, in swat.dat and front.dat), the map's unlock (unlockables.csv, unlocked from
                  the start) and its thumbnail (levelselect_<name>.tex, the template's)
    The level's look is then the template's until a level is exported over it (Blender: Export as level)."""
    kind, name = a.new_level, (a.name or '').strip().lower()
    if name.startswith('lvl_'):
        name = name[4:]
    limit = 8 if kind == 'sng' else 7
    if not re.fullmatch(r'[a-z0-9_]{1,%d}' % limit, name):
        print(f'--name: up to {limit} letters, digits or _ (the game names archives with 8 at most)'
              + (', the 8th is the player count' if kind != 'sng' else ''))
        return 1
    archives = index['archives']
    made = []
    if kind == 'sng':
        tpl = (a.template or 'saloon').lower().removeprefix('lvl_')
        tp = f'lvls_sng/{tpl}.dat'
        target = f'lvls_sng/{name}.dat'
        if tp not in archives:
            print(f'No {tp} on the disc')
            return 1
        if target in archives:
            print(f'{target} is on the disc already: pick another name')
            return 1
        src = _clean(a, iso, dats, tp)
        dst = os.path.join(a.mods, 'lvls_sng', f'{name}.dat')
        if os.path.isdir(dst) and os.listdir(dst):
            print(f'{dst} has files already: pick another name or empty it')
            return 1
        files = os.listdir(src)
        n = _copy_renamed(src, dst, files, f'lvl_{tpl}', f'lvl_{name}')
        print(f'Made {target}: {len(files)} files from {tp}, {n} of them lvl_{tpl}\'s, now lvl_{name}\'s')
        if getattr(a, 'no_geometry', False):
            g = strip_geometry(dst, f'lvl_{name}', getattr(a, 'blank', False))
            print(f'   its geometry, zones, collision, boxes and lines left out ({g} files): its spawns and the rest kept for your level')
        made.append(target)
    else:
        tpl = (a.template or 'ranch').lower().removeprefix('lvl_')
        story = None
        if f'lvls_m2/{tpl.rstrip("24")}2.dat' in archives:
            tpl = tpl.rstrip('24')
        elif any(x.startswith('lvls_sng/') and os.path.splitext(x.split('/')[1])[0] == tpl for x in archives):
            # a story level: its place over a Showdown map's base (Ranch's duels and multiplayer files)
            story = tpl
            tpl = 'ranch'
        if a.replace:
            for pc in ('2', '4'):
                old = os.path.join(a.mods, f'lvls_m{pc}', f'{name}{pc}.dat')
                if os.path.isdir(old):
                    shutil.rmtree(old)
                    print(f'lvls_m{pc}/{name}{pc}.dat: its old files taken out')
        for pc in ('2', '4'):
            tp = f'lvls_m{pc}/{tpl}{pc}.dat'
            target = f'lvls_m{pc}/{name}{pc}.dat'
            if tp not in archives:
                print(f'No {tp} on the disc')
                return 1
            if target in archives:
                print(f'{target} is on the disc already: pick another name')
                return 1
            group = [x for x in archives if x.startswith(f'lvls_m{pc}/')]
            sets = {x: set(os.listdir(_clean(a, iso, dats, x))) for x in group}
            shared = set.intersection(*sets.values())
            src = _clean(a, iso, dats, tp)
            dst = os.path.join(a.mods, f'lvls_m{pc}', f'{name}{pc}.dat')
            if os.path.isdir(dst) and os.listdir(dst):
                # the map's folder is there already: its files kept, only the menu row, unlock and thumbnail added
                print(f'{target}: its folder has files already: kept as they are')
                made.append(target)
                continue
            own = [f for f in sets[tp] if re.search(re.escape(f'lvl_{tpl}{pc}') + r'(?=[_.]|$)', f, re.I)]
            extra = sorted(sets[tp] - shared - set(own))
            _copy_renamed(src, dst, sorted(shared) + extra, f'lvl_{tpl}{pc}', f'lvl_{name}{pc}')
            n = _copy_renamed(src, dst, own, f'lvl_{tpl}{pc}', f'lvl_{name}{pc}')
            print(f'Made {target}: {len(shared)} files every Showdown map shares, {n} level files from '
                  f'lvl_{tpl}{pc} renamed lvl_{name}{pc}, {len(extra)} more of {tpl}{pc}\'s (its effects and sounds)')
            if getattr(a, 'no_geometry', False) and not story:
                g = strip_geometry(dst, f'lvl_{name}{pc}', getattr(a, 'blank', False))
                print(f'   its geometry, zones, collision, boxes and lines left out ({g} files): its spawns and the rest kept for your level')
            if story:
                # the story level's place in place of the base map's: geometry, collision, paths, spawns, lights
                stem = f'lvl_{name}{pc}'
                for f in os.listdir(dst):
                    if re.match(re.escape(stem) + r'[_.]', f, re.I) and f.lower().endswith(PLACE_EXTS):
                        os.remove(os.path.join(dst, f))
                sarch = next(x for x in archives if x.startswith('lvls_sng/') and os.path.splitext(x.split('/')[1])[0] == story)
                ssrc = _clean(a, iso, dats, sarch)
                place = [f for f in os.listdir(ssrc) if re.match(re.escape(f'lvl_{story}') + r'[_.]', f, re.I)
                         and f.lower().endswith(PLACE_EXTS)]
                k = _copy_renamed(ssrc, dst, place, f'lvl_{story}', stem)
                spwn = os.path.join(dst, stem + '.spwn')
                aisp = os.path.join(dst, stem + '.aisp')
                if os.path.isfile(spwn):
                    t = fill_spawns(open(spwn, encoding='latin-1', newline='').read(),
                                    open(aisp, encoding='latin-1').read() if os.path.isfile(aisp) else '', [4, 4, 4, 4])
                    open(spwn, 'w', encoding='latin-1', newline='').write(t)
                w = strip_walkrun(dst, stem)
                duel = add_duel_spawns(dst, stem)
                print(f'   the place is lvl_{story}\'s ({k} files): spawns filled to 4 sets of 4'
                      + (f', {w} walk/run room(s) taken out' if w else '')
                      + (', High Noon duel spots added (round its own duel spot)' if duel else ''))
                if getattr(a, 'no_geometry', False):
                    g = strip_geometry(dst, stem, getattr(a, 'blank', False))
                    print(f'   its geometry, zones, collision, boxes and lines left out ({g} files): its spawns and the rest kept for your level')
            made.append(target)
        # the Showdown menu, the unlock, the thumbnail
        title = (a.title or name.title()).replace(',', ' ')
        subtitle = (a.subtitle or 'custom map').replace(',', ' ')
        row = f'<LOOKUPDATA:lvl_{name},\tlevelselect_{name},\t{title},\t{subtitle}>'
        for arch in ('swat.dat', 'front.dat'):
            path, text = _mod_or_clean(a, iso, dats, arch, 'postermultiview.swui')
            if text is None:
                continue
            if f'<LOOKUPDATA:lvl_{name},' not in text:
                nl = '\r\n' if '\r\n' in text else '\n'
                m = re.search(r'<LOOKUP:LevelTable>.*?(?=' + re.escape(nl) + r'\s*' + re.escape(nl) + r'\s*' +
                              re.escape(nl) + r'|' + re.escape(nl) + r';;;)', text, re.S)
                rows = list(re.finditer(r'<LOOKUPDATA:lvl_[^>]*>', text))
                at = rows[-1].end() if rows else (m.end() if m else len(text))
                text = text[:at] + nl + row + text[at:]
                os.makedirs(os.path.dirname(path), exist_ok=True)
                open(path, 'w', encoding='latin-1', newline='').write(text)
            tx = '.xtex' if XBOX[0] else '.tex'
            thumb_src = os.path.join(_clean(a, iso, dats, arch), f'levelselect_{tpl}{tx}')
            if os.path.isfile(thumb_src):
                shutil.copyfile(thumb_src, os.path.join(os.path.dirname(path), f'levelselect_{name}{tx}'))
            print(f'{arch}: the Showdown menu lists "{title}" (lvl_{name})'
                  + (f', thumbnail levelselect_{name}{tx}' if os.path.isfile(thumb_src) else ''))
        path, text = _mod_or_clean(a, iso, dats, 'swat.dat', 'unlockables.csv')
        if text is not None and not re.search(r'(?m)^lvl_' + re.escape(name) + r',', text):
            nl = '\r\n' if '\r\n' in text else '\n'
            text = text.rstrip('\r\n') + nl + f'lvl_{name},stage,ugly,1,TIME_PLAYING,1,,,' + nl
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, 'w', encoding='latin-1', newline='').write(text)
            print(f'swat.dat: unlockables.csv has lvl_{name}, unlocked from the start')
    print('New level made: ' + ', '.join(made) + '. Export your level over it from Blender (Export as level: lvl_'
          + name + ('' if kind == 'sng' else '2, then lvl_' + name + '4') + ') into ' +
          (' and '.join(made)) + "'s folder, then Rebuild and Run")
    return 0


# ---------------------------------------------------------------------------------------------------------------
# images to textures: .png to the game's .tex (PS2: 256 colour palette) or .xtex (Xbox: DXT1, DXT5 with alpha)

def read_png(path):
    """A PNG as an RGBA numpy array (rows top first): 8 and 16 bit grey, grey+alpha, RGB, RGBA and palette images
    (palette 1, 2, 4 or 8 bit, with tRNS transparency), not interlaced."""
    import zlib
    import numpy as np
    data = open(path, 'rb').read()
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError(f'{os.path.basename(path)}: not a PNG')
    o, idat, plte, trns = 8, bytearray(), None, None
    w = h = depth = ctype = 0
    while o < len(data):
        n = struct.unpack('>I', data[o:o + 4])[0]
        kind, body = data[o + 4:o + 8], data[o + 8:o + 8 + n]
        o += 12 + n
        if kind == b'IHDR':
            w, h, depth, ctype, _comp, _filt, inter = struct.unpack('>IIBBBBB', body)
            if inter:
                raise ValueError(f'{os.path.basename(path)}: interlaced PNGs are not read: save it without interlacing')
        elif kind == b'PLTE':
            plte = np.frombuffer(body, np.uint8).reshape(-1, 3)
        elif kind == b'tRNS':
            trns = body
        elif kind == b'IDAT':
            idat += body
        elif kind == b'IEND':
            break
    chans = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    bpp = max(1, chans * depth // 8)  # bytes a pixel, for the filters
    stride = (w * chans * depth + 7) // 8
    raw = zlib.decompress(bytes(idat))
    rows = np.zeros((h, stride), np.uint8)
    prev = np.zeros(stride, np.int32)
    pos = 0
    for y in range(h):
        f = raw[pos]
        line = np.frombuffer(raw, np.uint8, stride, pos + 1).astype(np.int32)
        pos += 1 + stride
        if f == 0:
            cur = line
        elif f == 2:
            cur = (line + prev) & 255
        else:
            cur = line.copy()
            for x in range(stride):
                left = cur[x - bpp] if x >= bpp else 0
                if f == 1:
                    cur[x] = (cur[x] + left) & 255
                elif f == 3:
                    cur[x] = (cur[x] + ((left + prev[x]) >> 1)) & 255
                elif f == 4:
                    up, ul = prev[x], (prev[x - bpp] if x >= bpp else 0)
                    pa, pb, pc = abs(up - ul), abs(left - ul), abs(left + up - 2 * ul)
                    pr = left if pa <= pb and pa <= pc else up if pb <= pc else ul
                    cur[x] = (cur[x] + pr) & 255
        rows[y] = cur
        prev = cur
    if depth == 16:
        px = rows.reshape(h, w * chans, 2)[..., 0]  # the high byte
    elif depth < 8:
        bits = np.unpackbits(rows, axis=1).reshape(h, -1)[:, :w * depth]
        px = bits.reshape(h, w, depth).dot(1 << np.arange(depth - 1, -1, -1)).astype(np.uint8)
    else:
        px = rows
    px = px.reshape(h, w, chans)
    out = np.full((h, w, 4), 255, np.uint8)
    if ctype == 3:
        pal = np.full((256, 4), 255, np.uint8)
        pal[:len(plte), :3] = plte
        if trns:
            pal[:len(trns), 3] = np.frombuffer(trns, np.uint8)
        out = pal[px[..., 0]]
    elif ctype in (0, 4):
        out[..., :3] = px[..., :1]
        if ctype == 4:
            out[..., 3] = px[..., 1]
    else:
        out[..., :3] = px[..., :3]
        if ctype == 6:
            out[..., 3] = px[..., 3]
    return out


def power_of_two(img, limit=2048):
    """An image resized (bilinear) to power of two sides, the nearest ones, at most limit: as the game's textures."""
    import numpy as np
    h, w = img.shape[:2]
    def p2(n):
        k = 1
        while k * 2 <= n:
            k *= 2
        return min(limit, k * 2 if n - k > k * 2 - n else k)
    nh, nw = p2(h), p2(w)
    if (nh, nw) == (h, w):
        return img
    ys = np.clip((np.arange(nh) + 0.5) * h / nh - 0.5, 0, h - 1)
    xs = np.clip((np.arange(nw) + 0.5) * w / nw - 0.5, 0, w - 1)
    y0, x0 = ys.astype(int), xs.astype(int)
    y1, x1 = np.minimum(y0 + 1, h - 1), np.minimum(x0 + 1, w - 1)
    fy, fx = (ys - y0)[:, None, None], (xs - x0)[None, :, None]
    f = img.astype(np.float32)
    top = f[y0][:, x0] * (1 - fx) + f[y0][:, x1] * fx
    bot = f[y1][:, x0] * (1 - fx) + f[y1][:, x1] * fx
    return np.clip(top * (1 - fy) + bot * fy + 0.5, 0, 255).astype(np.uint8)


def resize_to(img, w, h):
    """An image resized (bilinear) to w x h."""
    import numpy as np
    ih, iw = img.shape[:2]
    if (ih, iw) == (h, w):
        return img
    ys = np.clip((np.arange(h) + 0.5) * ih / h - 0.5, 0, ih - 1)
    xs = np.clip((np.arange(w) + 0.5) * iw / w - 0.5, 0, iw - 1)
    y0, x0 = ys.astype(int), xs.astype(int)
    y1, x1 = np.minimum(y0 + 1, ih - 1), np.minimum(x0 + 1, iw - 1)
    fy, fx = (ys - y0)[:, None, None], (xs - x0)[None, :, None]
    f = img.astype(np.float32)
    top = f[y0][:, x0] * (1 - fx) + f[y0][:, x1] * fx
    bot = f[y1][:, x0] * (1 - fx) + f[y1][:, x1] * fx
    return np.clip(top * (1 - fy) + bot * fy + 0.5, 0, 255).astype(np.uint8)


def book_red_page(img, crop=True, place=None):
    """An image made into a book_red texture, as the GIMP template does it (assets/book_red_*.png are its layers):
    the picture fills the 256x256 page (scaled to cover, centred), the SHADOW layer under it and the BookBackground
    layer (the page's paper and the frame's ornaments) over it, then the whole colour-erased with black: white,
    see-through where it was dark (the game's book_red is white with alpha). Alpha topped at 214, the original's
    highest. Returns 256x256 RGBA, rows top first."""
    import numpy as np
    here = os.path.join(HERE, 'assets')

    def load(n):
        from PIL import Image
        return np.asarray(Image.open(os.path.join(here, n)).convert('RGBA'), dtype=np.float64) / 255

    shadow, page = load('book_red_shadow.png'), load('book_red_background.png')
    # place: the picture's own placement from the converter's preview ({s: zoom on top of "cover", dx, dy: pixels
    # of the 256 page, y down}); without one it covers the page, centred
    pic = _grey((place_on_page(img, place) if place else resize_cover(img, 256, 256)).astype(np.float64) / 255)
    # INVERTED: the game draws this texture's alpha as ink (more alpha, darker on the page), so a picture goes in as
    # its negative or it shows as one (tested in game)
    pic[..., :3] = 1 - pic[..., :3]
    # the picture layer at 75 % (alpha 191 on the figure, measured on the template author's own greyscaled Red),
    # its cut-out kept: the shadow shows through it as in the original
    pic[..., 3] *= 191 / 255
    if crop:
        # only inside the frame's oval: the SHADOW layer fills exactly that opening; softened over a pixel
        inside = (shadow[..., 3] > 0).astype(np.float64)
        pad = np.pad(inside, 1, mode='edge')
        inside = sum(pad[y:y + 256, x:x + 256] for y in range(3) for x in range(3)) / 9
        pic[..., 3] *= inside

    def over(dst, src):
        sa, da = src[..., 3:4], dst[..., 3:4]
        oa = sa + da * (1 - sa)
        rgb = np.where(oa > 0, (src[..., :3] * sa + dst[..., :3] * da * (1 - sa)) / np.maximum(oa, 1e-9), 0)
        return np.concatenate([rgb, oa], -1)

    comp = over(over(shadow, pic), page)
    m = comp[..., :3].max(-1, keepdims=True)          # colour erase, black: alpha by the brightest channel
    rgb = np.where(m > 0, comp[..., :3] / np.maximum(m, 1e-9), 1.0)
    alpha = comp[..., 3:4] * m * (214 / 255)
    return np.clip(np.concatenate([rgb, alpha], -1) * 255 + 0.5, 0, 255).astype(np.uint8)


def _grey(a):
    """RGBA (0..1) with its colour made grey (luminance), alpha kept."""
    import numpy as np
    lum = a[..., 0] * 0.299 + a[..., 1] * 0.587 + a[..., 2] * 0.114
    out = a.copy()
    out[..., 0] = out[..., 1] = out[..., 2] = lum
    return out


# The game's own picture textures, by kind: (width, height, format, mipmaps, flags), as the shipped ones are.
PICTURE_KINDS = {
    'mp_portrait': (128, 128, 'pa8', False, '010001000100'),   # mp_<code>.tex, the multiplayer portraits
    'level_sp': (128, 128, 'pa8', False, '010000000000'),      # img0_*.tex, the single player journal pictures
    'level_mp': (256, 128, 'pa4', False, '010001000100'),      # levelselect_*.tex, the Showdown map pictures
    'mp_photo': (256, 128, 'pa8', False, '010001000100'),      # ui_mphoto_<code>.tex, the Showdown full-body photos
}


# The names the game gives each picture kind: book_red is one file; the others carry their prefix.
PICTURE_PREFIX = {'mp_portrait': 'mp_', 'level_sp': 'img0_', 'level_mp': 'levelselect_', 'mp_photo': 'ui_mphoto_'}


def picture_name(mode, src_name, wanted=None):
    """The texture's name (no extension): book_red for book_red; for the other picture kinds the name wanted (else
    the image's own), lower case, with its prefix (mp_, img0_, levelselect_) added when it has not got it; for every
    other conversion the name wanted, else the image's own."""
    stem = os.path.splitext(src_name)[0]
    if mode == 'book_red':
        return 'book_red'
    if mode in PICTURE_PREFIX:
        name = re.sub(r'[^a-z0-9_]+', '_', (wanted or stem).strip().lower()).strip('_') or 'picture'
        if name.endswith('.tex'):
            name = name[:-4]
        pre = PICTURE_PREFIX[mode]
        return name if name.startswith(pre) else pre + name
    if wanted and wanted.strip():
        # a name typed for any other conversion: as typed, minus an extension and anything a file name cannot hold
        name = re.sub(r'[\\/:*?"<>|]+', '_', wanted.strip())
        return name[:-4] if name.lower().endswith('.tex') else name
    return stem


def _page(img, place, w, h):
    return place_on_page(img, place, w, h) if place else resize_cover(img, w, h)


def mp_portrait(img, place=None):
    """A multiplayer portrait (mp_<code>.tex), as the template mp template.xcf makes one: the character pasted
    twice -- one copy ERASES its shape out of the white team square (assets/mp_square.png), the other goes on top as
    black with its darkness as alpha (GIMP's colour-to-alpha with white: matches the template's own layer exactly).
    The shipped mp_*.tex are just that. 128x128 RGBA, rows top first."""
    import numpy as np
    from PIL import Image
    pic = _grey(_page(img, place, 128, 128).astype(np.float64) / 255)
    sq = np.asarray(Image.open(os.path.join(HERE, 'assets', 'mp_square.png')).convert('RGBA'), dtype=np.float64) / 255
    cut = pic[..., 3]
    sq = sq.copy()
    sq[..., 3] *= 1 - cut                                        # the erase layer
    ink = (1 - pic[..., 0]) * cut                                # black, alpha = darkness
    oa = ink + sq[..., 3] * (1 - ink)
    rgb = np.where(oa[..., None] > 0, (sq[..., :3] * (sq[..., 3] * (1 - ink))[..., None]) / np.maximum(oa, 1e-9)[..., None], 0)
    return np.clip(np.concatenate([rgb, oa[..., None]], -1) * 255 + 0.5, 0, 255).astype(np.uint8)


def mp_photo(img, place=None):
    """A Showdown full-body photo (ui_mphoto_<code>.tex), as the game's are: the picture in colour with its own
    transparency, fitted upright on a 128 x 256 page and stored a quarter turn anticlockwise (head to the left) in a
    256 x 128 texture -- the game turns it back. Checked on ui_mphoto_srd / _jck: turned clockwise they stand upright.
    256x128 RGBA, rows top first."""
    import numpy as np
    page = _page(img, place, 128, 256)
    return np.ascontiguousarray(np.rot90(page, 1))


def level_picture(img, kind, place=None):
    """A level picture as the game's are: greyscale with the DARKNESS as alpha (the game draws it as ink) --
    black for the single player journal pictures (img0_*.tex, 128x128), white for the Showdown map pictures
    (levelselect_*.tex, 256x128)."""
    import numpy as np
    w, h = PICTURE_KINDS[kind][:2]
    pic = _grey(_page(img, place, w, h).astype(np.float64) / 255)
    out = np.zeros_like(pic) if kind == 'level_sp' else np.ones_like(pic)
    out[..., 3] = (1 - pic[..., 0]) * pic[..., 3]
    return np.clip(out * 255 + 0.5, 0, 255).astype(np.uint8)


def ink_plain(img, white=False):
    """The greyscale "ink" transparency with no template, frame or set size: the picture greyscaled, its DARKNESS
    as the alpha (the game draws the alpha as ink -- dark parts solid, light parts clear), the colour black (or
    white for white ink); the picture keeps its own size and its own see-through parts stay clear."""
    import numpy as np
    a = _grey(img.astype(np.float64) / 255)
    out = np.ones_like(a) if white else np.zeros_like(a)
    out[..., 3] = (1 - a[..., 0]) * a[..., 3]
    return np.clip(out * 255 + 0.5, 0, 255).astype(np.uint8)


def book_red_plain(img):
    """book_red's greyscale transparency on a whole image, no frame: greyscaled, then white with the darkness as
    its alpha (the game draws the alpha as ink: dark parts of the picture solid, light parts clear), topped at 214
    like the original."""
    import numpy as np
    a = _grey(img.astype(np.float64) / 255)
    out = np.ones_like(a)
    # inverted: the game draws the alpha as ink, so dark parts of the picture get the most
    out[..., 3] = (1 - a[..., 0]) * a[..., 3] * (214 / 255)
    return np.clip(out * 255 + 0.5, 0, 255).astype(np.uint8)


def place_on_page(img, place, w=256, h=256):
    """img scaled and moved as the converter's preview shows it, on a transparent w x h page: scale = the cover
    scale * place['s'], centred, then moved by (dx, dy)."""
    import numpy as np
    from PIL import Image
    ih, iw = img.shape[:2]
    k = max(w / iw, h / ih) * max(0.05, float(place.get('s', 1)))
    nw, nh = max(1, round(iw * k)), max(1, round(ih * k))
    im = Image.fromarray(np.ascontiguousarray(img), 'RGBA').resize((nw, nh), Image.LANCZOS)
    page = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    x = round((w - nw) / 2 + float(place.get('dx', 0)))
    y = round((h - nh) / 2 + float(place.get('dy', 0)))
    page.paste(im, (x, y), im)
    return np.asarray(page, dtype=np.uint8)


def resize_cover(img, w, h):
    """img scaled to cover w x h (aspect kept) and centre-cropped."""
    import numpy as np
    from PIL import Image
    ih, iw = img.shape[:2]
    k = max(w / iw, h / ih)
    nw, nh = max(w, round(iw * k)), max(h, round(ih * k))
    im = Image.fromarray(np.ascontiguousarray(img), 'RGBA').resize((nw, nh), Image.LANCZOS)
    x, y = (nw - w) // 2, (nh - h) // 2
    return np.asarray(im.crop((x, y, x + w, y + h)), dtype=np.uint8)


def make_alpha(img, mode, key='000000', tolerance=16, soft=48):
    """Transparency for an image (most PNGs have none), as the game's see-through textures have it:
        keep    the image's own alpha
        dark    a dark background clear: the dark parts joined to the image's edges, fading in with brightness
                (the black backgrounds of the menu portraits ui_mphoto_*); dark parts inside stay solid
        key     one colour clear (key, as RRGGBB; within tolerance, fading over soft)
        mask    the brightness is the shape, the colour white (dust, smoke, shadows, masks)
        book    greyscaled, the white see-through: dark ink solid, paper clear (the book textures: book_red, the
                weapon pages book_w*), near-white within tolerance fully clear"""
    import numpy as np
    out = img.copy()
    rgb = img[..., :3].astype(np.int32)
    soft = max(1, soft)
    if mode == 'dark':
        # the dark background only: near-black pixels (within tolerance) joined to the image's edges are clear,
        # everything else solid (a dark coat inside stays), the cut-out's border softened over a pixel or two
        level = rgb.max(2)
        dark = level <= tolerance
        bg = np.zeros_like(dark)
        bg[0, :], bg[-1, :], bg[:, 0], bg[:, -1] = dark[0, :], dark[-1, :], dark[:, 0], dark[:, -1]
        while True:
            grow = bg.copy()
            grow[1:] |= bg[:-1]
            grow[:-1] |= bg[1:]
            grow[:, 1:] |= bg[:, :-1]
            grow[:, :-1] |= bg[:, 1:]
            grow &= dark
            if (grow == bg).all():
                break
            bg = grow
        a = np.where(bg, 0.0, 255.0)
        for _ in range(max(0, soft // 24)):  # soft 48: two passes of a 3x3 blur
            pad = np.pad(a, 1, mode='edge')
            a = sum(pad[y:y + a.shape[0], x:x + a.shape[1]] for y in range(3) for x in range(3)) / 9
        out[..., 3] = np.minimum(img[..., 3], np.clip(a + 0.5, 0, 255).astype(np.uint8))
    elif mode == 'key':
        k = np.array([int(key[i:i + 2], 16) for i in (0, 2, 4)])
        dist = np.abs(rgb - k).max(2)
        out[..., 3] = np.minimum(img[..., 3], np.clip((dist - tolerance) * 255 // soft, 0, 255))
    elif mode == 'book':
        lum = (rgb[..., 0] * 299 + rgb[..., 1] * 587 + rgb[..., 2] * 114) // 1000
        out[..., :3] = lum[..., None].astype(np.uint8)
        clear_from = 255 - max(0, min(tolerance, 254))  # this bright and up: fully see-through
        a = np.clip((clear_from - lum) * 255 // max(1, clear_from), 0, 255)
        out[..., 3] = np.minimum(img[..., 3], a).astype(np.uint8)
    elif mode == 'mask':
        lum = (rgb[..., 0] * 299 + rgb[..., 1] * 587 + rgb[..., 2] * 114) // 1000
        out[..., :3] = 255
        out[..., 3] = (lum * img[..., 3].astype(np.int32) // 255).astype(np.uint8)
    return out


def check_view_rooms(folder):
    """The levels in a folder whose AI rooms do not match their view data: a level's _view bundle (RSV0) has a record
    per AIROOM box of its .lvl's box files, in order, and the game pairs them up when the level loads (true of all
    25 shipped levels with view data). A room deleted (in Blender) leaves a record with no room: the Xbox game
    crashes loading the level (a null read in the view loader). Returns the warnings. folder: a folder, or the
    archive's files in memory {name: bytes}."""
    warnings = []
    if isinstance(folder, dict):
        mem = folder
        files = {k.lower(): k for k in mem if '/' not in k}

        def _open(name, *a, encoding=None):
            b = mem[name]
            return io.StringIO(b.decode(encoding)) if encoding else io.BytesIO(b)
    else:
        try:
            files = {f.lower(): f for f in os.listdir(folder)}
        except OSError:
            return warnings

        def _open(name, *a, encoding=None):
            return open(os.path.join(folder, name), *a, encoding=encoding)
    for low, f in files.items():
        if not (low.startswith('lvl_') and low.endswith('.lvl')):
            continue
        stem = low[:-4]
        view = files.get(stem + '_view.xrsc') or files.get(stem + '_view.rsc')
        if not view:
            continue
        try:
            data = _open(view, 'rb').read()
            n, hs = struct.unpack_from('<II', data, 4)
            records = struct.unpack_from('<I', data, hs)[0]
            lvl = _open(f, encoding='latin-1').read()
        except (OSError, struct.error):
            continue
        rooms = 0
        for b in re.findall(r'(\S+\.bxes)', lvl):
            real = files.get(b.lower())
            if real:
                rooms += len(re.findall(r'(?m)^Box\s+\S*AIROOM_', _open(real, encoding='latin-1').read()))
        if rooms != records:
            warnings.append(f'WARNING {stem}: its view data has {records} records but its box files have {rooms} AI rooms '
                            f'(box_AIROOM_...): the game crashes loading this level. Was an AI room box deleted in '
                            f'Blender? Put it back (Add from game: the level\'s .bxes).')
    return warnings


def fit_sneak(files):
    """An archive's files {name (lower case): bytes} with each level's .sneak counting the sneak boxes its .lvl's box
    files have (box_AISNEAK_...): one value per box, "NumOfSneakBoxes 0" with none (Core's). Every shipped Showdown
    level has the two equal; a New .dat level kept its template's 7 with its boxes deleted and the Xbox game crashed
    (a null write, "write 1000000F6") right after reading it (2026-11-18). -> notes."""
    notes = []
    for low in list(files):
        if not (low.startswith('lvl_') and low.endswith('.lvl')):
            continue
        stem = low[:-4]
        sn = files.get(stem + '.sneak')
        if sn is None:
            continue
        text = sn.decode('latin-1')
        m = re.search(r'NumOfSneakBoxes\s+(\d+)', text)
        if not m:
            continue
        have = int(m.group(1))
        boxes = 0
        for b in re.findall(r'(\S+\.bxes)', files[low].decode('latin-1')):
            data = files.get(b.lower())
            if data is not None:
                boxes += len(re.findall(r'(?m)^Box\s+\S*AISNEAK', data.decode('latin-1')))
        if boxes == have:
            continue
        vals = text[m.end():].split()
        vals = (vals + ['1'] * boxes)[:boxes]
        out = text[:m.start()] + 'NumOfSneakBoxes\t%d \n' % boxes + (' '.join(vals) + ' ' if vals else '')
        files[stem + '.sneak'] = out.encode('latin-1')
        notes.append('%s: its .sneak counted %d sneak boxes, the level has %d: made %d (the game crashes loading a '
                     'level whose .sneak counts boxes it has not)' % (stem, have, boxes, boxes))
    return notes


def fit_level_counts(files):
    """The counts other files keep of a level's boxes and paths made right (fit_sneak, pad_view_rooms, and the AI nav
    graph from the path file: rdrstudio.navgen.fit_nav). -> notes."""
    notes = fit_sneak(files) + pad_view_rooms(files)
    try:
        from rdrstudio import navgen as _ng
    except ImportError:
        try:
            import navgen as _ng
        except ImportError:
            _ng = None
    if _ng is not None:
        notes += _ng.fit_nav(files)
    return notes


def pad_view_rooms(files):
    """An archive's files {name (lower case): bytes} with each level's AI rooms made at least as many as its view
    data's records: placeholder rooms (box_AIROOM_Low_placeholder_NN, half a metre, 1000 m under the map) added to
    the last box file its .lvl lists. The Xbox game pairs view record i with AI room i and crashed (a null read,
    "read 100000080") loading a level with fewer -- a New .dat level keeps one box of its template's 22, and a box
    select delete takes the rest (2026-11-18). -> notes."""
    notes = []
    for low in list(files):
        if not (low.startswith('lvl_') and low.endswith('.lvl')):
            continue
        stem = low[:-4]
        view = files.get(stem + '_view.xrsc') or files.get(stem + '_view.rsc')
        if not view:
            continue
        try:
            _n, hs = struct.unpack_from('<II', view, 4)
            records = struct.unpack_from('<I', view, hs)[0]
        except struct.error:
            continue
        lvl = files[low].decode('latin-1')
        listed = re.findall(r'(\S+\.bxes)', lvl)
        rooms = 0
        for b in listed:
            data = files.get(b.lower())
            if data is not None:
                rooms += len(re.findall(r'(?m)^Box\s+\S*AIROOM_', data.decode('latin-1')))
        if rooms >= records or not listed:
            continue
        target = listed[-1].lower()
        text = files.get(target, b'NumBoxes 0 \r\n').decode('latin-1')
        nl = '\r\n' if '\r\n' in text or not text.strip() else '\n'
        add = []
        for k in range(records - rooms):
            add.append(nl.join(['Box box_AIROOM_Low_placeholder_%02d {' % (k + 1), '\twidth 0.500000',
                                '\theight 0.500000', '\tdepth 0.500000', '\tmatrix', '\t{',
                                '\t\t1.000000\t0.000000\t0.000000', '\t\t0.000000\t1.000000\t0.000000',
                                '\t\t0.000000\t0.000000\t1.000000', '\t\t0.000000\t-1000.000000\t0.000000',
                                '\t}', '\tattributes 0 {', '\t}', '}']))
        m = re.search(r'NumBoxes\s+(\d+)', text)
        have = int(m.group(1)) if m else 0
        body = text.rstrip('\r\n \t') + nl + nl + (nl + nl).join(add) + nl
        if m:
            body = body[:m.start(1)] + str(have + len(add)) + body[m.end(1):]
        else:
            body = 'NumBoxes %d %s%s' % (len(add), nl, body)
        files[target] = body.encode('latin-1')
        notes.append('%s: its view data has %d AI-room records, its boxes %d AI rooms: %d placeholder room(s) added '
                     '(1000 m under the map) so the Xbox game does not crash loading it' % (stem, records, rooms,
                                                                                          len(add)))
    return notes


def png_to_tex(a):
    """--png2tex: each .png given as a .tex (PS2, 256 colours; with alpha PA8) or .xtex (Xbox, DXT1; with alpha
    DXT5), with mipmaps, beside it or into --to."""
    sys.path.insert(0, HERE)
    import rdrtex
    ext = '.xtex' if a.platform == 'xbox' else '.tex'
    kinds = {'auto': None, 'dxt1': rdrtex.DXT1, 'dxt5': rdrtex.DXT5, 'p8': rdrtex.P8, 'pa8': rdrtex.PA8,
             'p4': rdrtex.P4, 'pa4': rdrtex.PA4, 'rgba': rdrtex.RGBA8888}
    like = None
    if a.like:
        # a game texture to match: its format, size, mipmaps and flags
        raw = open(a.like, 'rb').read()
        lw, lh, lt, llv = struct.unpack_from('<4H', raw)
        like = (lw, lh, lt, llv, raw[8:14])
        names = {v: k for k, v in kinds.items() if v}
        print(f'Like {os.path.basename(a.like)}: {lw}x{lh}, {names.get(lt, lt).upper()}, {llv} level(s), flags {raw[8:14].hex()}')
        if lt not in names.keys():
            print(f'  its format ({lt}) is not one the converter writes: the format picked is used')
            like = (lw, lh, None, llv, raw[8:14])
    done = 0
    for src in a.png2tex:
        try:
            img = read_png(src)
            h, w = img.shape[:2]
            place = (getattr(a, 'place', None) or {}).get(os.path.basename(src))
            preset = PICTURE_KINDS.get(a.alpha)
            if a.alpha == 'book_red':
                img = book_red_page(img, getattr(a, 'crop', True), place)
            elif a.alpha == 'book_red_plain':
                img = book_red_plain(img)
            elif a.alpha in ('ink_black', 'ink_white'):
                img = ink_plain(img, a.alpha == 'ink_white')
            elif a.alpha == 'mp_portrait':
                img = mp_portrait(img, place)
            elif a.alpha == 'mp_photo':
                img = mp_photo(img, place)
            elif a.alpha in ('level_sp', 'level_mp'):
                img = level_picture(img, a.alpha, place)
            else:
                img = make_alpha(img, a.alpha, a.key, a.tolerance, a.soft)
            kind = kinds[a.format]
            if kind is None and a.alpha in ('book', 'book_red', 'book_red_plain') and a.platform == 'ps2':
                kind = rdrtex.PA4  # the PS2's book textures: 16 colours with alpha (the Xbox's: DXT5)
            mips, flags = not a.no_mips, rdrtex.DEFAULT_FLAGS
            if preset:
                # the shipped textures of this kind: their size (made above), format, no mipmaps, their flags
                kind = kinds[preset[2]] if a.format == 'auto' else kind
                mips, flags = preset[3], bytes.fromhex(preset[4])
            if like:
                img = resize_to(img, like[0], like[1])
                kind = like[2] if like[2] is not None else kind
                mips, flags = like[3] > 1, like[4]
            elif a.resize and not preset:
                img = power_of_two(img)
            data = rdrtex.encode(img[::-1], kind, mips=mips, flags=flags, platform=a.platform)  # rows bottom first
            out = os.path.join(a.to or os.path.dirname(src), picture_name(a.alpha, os.path.basename(src),
                               (getattr(a, 'names', None) or {}).get(os.path.basename(src))) + ext)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, 'wb') as f:
                f.write(data)
            t = rdrtex.read(data)
            names = {rdrtex.DXT1: 'DXT1', rdrtex.DXT5: 'DXT5', rdrtex.P8: '256 colours', rdrtex.PA8: '256 colours + alpha',
                     rdrtex.P4: '16 colours', rdrtex.PA4: '16 colours + alpha', rdrtex.RGBA8888: 'RGBA'}
            print(f'{os.path.basename(src)} ({w}x{h}) -> {os.path.basename(out)}: {t.width}x{t.height}, '
                  f'{names.get(t.type, t.type)}, {len(t.mips)} mip level(s)')
            done += 1
        except Exception as e:  # one bad image does not stop the rest
            print(f'{os.path.basename(src)}: not converted: {e}')
    print(f'{done} of {len(a.png2tex)} image(s) converted to {ext}')
    return 0 if done else 1


def get_files(a, iso, archive, names, folder):
    """Files out of an archive on the clean ISO into folder (as the game has them), for editing or as new ones."""
    files = iso.files()
    if archive not in files:
        print(f'{archive}: not on the disc')
        return 1
    data = iso.read(files[archive])
    os.makedirs(folder, exist_ok=True)
    got = 0
    for n in names:
        path, raw = read_entry(data, n)
        if raw is None:
            print(f'  {n}: {archive} has no such file')
            continue
        out = os.path.join(folder, path.rsplit('/', 1)[-1])
        with open(out, 'wb') as f:
            f.write(raw)
        print(f'  {path}: copied to {out}')
        got += 1
    print(f'{got} file(s) from {archive}')
    return 0


def export_full(a, iso, index, dats, holders, archive, to):
    """An archive in full into a folder: the unedited game's files, with the mods for it laid over them as a build
    would put them in (its .dat folder's files; Master's files: Replace only into it when it has a file of that
    name, Add to all into the levels, front.dat and swat.dat; loose files of a name it has). A new archive (a .dat
    folder the game has not): its folder's files."""
    archive = archive.replace(os.sep, '/').lower()
    fresh = new_archives(a.mods, index['archives'])
    out = os.path.join(to, os.path.basename(archive))
    if os.path.isdir(out):
        shutil.rmtree(out)
    if archive in fresh:
        shutil.copytree(fresh[archive], out)
        n = sum(len(f) for _r, _d, f in os.walk(out))
        print(f'{archive} (a new archive): its {n} files copied to {out}')
        return 0
    if archive not in index['archives']:
        print(f'{archive}: not an archive of the game')
        return 1
    print(f'{archive}: the game\'s files (extracted once into the cache)...')
    shutil.copytree(_clean(a, iso, dats, archive), out)
    loose, targeted, every, master = mod_files(a.mods, list(index['archives']) + list(fresh))
    by_name, by_path = {}, {}
    for root, _d, fs in os.walk(out):
        for f in fs:
            full = os.path.join(root, f)
            by_name.setdefault(f.lower(), []).append(full)
            by_path[os.path.relpath(full, out).replace(os.sep, '/').lower()] = full
    put = 0

    def lay(src, targets):
        nonlocal put
        for t in targets:
            os.makedirs(os.path.dirname(t), exist_ok=True)
            shutil.copyfile(src, t)
            put += 1
    for k, path in loose.items():
        if k in by_name and k not in master:
            lay(path, by_name[k])
    modes = master_modes(a.mods)
    for k, path in master.items():
        mode = master_mode(k, modes, holders)
        if mode == 'replace' and k in by_name:
            lay(path, by_name[k])
        elif mode == 'add' and master_target(archive):
            lay(path, by_name.get(k) or [os.path.join(out, k)])
    for inner, path in targeted.get(archive, []):
        if '/' in inner and inner in by_path:
            lay(path, [by_path[inner]])
        elif by_name.get(inner.rsplit('/', 1)[-1]):
            lay(path, by_name[inner.rsplit('/', 1)[-1]])
        else:
            lay(path, [os.path.join(out, *inner.split('/'))])
    n = sum(len(f) for _r, _d, f in os.walk(out))
    print(f'{archive} in full: {n} files in {out} ({put} of them your mods\')')
    return 0


def restore(a, archives):
    """Archives back to the clean ISO's in the test ISO, at once (no rebuild): each one the last build wrote gets its
    original bytes and directory entry back. Data appended past the clean ISO's end stays until the next build."""
    if XBOX[0]:
        done = restore_folder(a, archives)
        _forget_restored(a, archives, done)
        return 0
    state_path = a.out + '.ps2build.json'
    if not os.path.isfile(a.out) or not os.path.isfile(state_path):
        print('No test ISO yet: nothing to put back')
        return 0
    state = json.load(open(state_path))
    src = Iso(a.iso)
    dst = Iso(a.out, 'r+b')
    clean_files = src.files()
    done = []
    # archives the builds added to the disc: taken out, the newest first (each changed its folder after the last)
    wanted = {x.lower() for x in archives}
    for p in reversed([k for k, r in state['records'].items() if r.get('new')]):
        later = [k for k, r in state['records'].items() if r.get('new')]
        if p.lower() not in wanted:
            continue
        # one added after it in the same folder has to come out first (its saved sectors hold this one)
        for q in reversed(later[later.index(p) + 1:]):
            if state['records'][q]['dir_lba'] == state['records'][p]['dir_lba'] and q.lower() not in wanted:
                wanted.add(q.lower())
                print(f'  {q}: taken out too (added after {p} in the same folder)')
        dst.restore_new(state['records'].pop(p))
        done.append(p)
    for p in [x for x in archives if x.lower() in wanted and x not in done and not
              state['records'].get(x, {}).get('new')]:
        rec = state['records'].pop(p, None)
        if rec is None:
            if p not in done:
                print(f'  {p}: the test ISO has the original already')
            continue
        orig = clean_files[p]
        if rec['lba'] == orig['lba']:
            dst.f.seek(orig['lba'] * SECTOR)
            dst.f.write(src.read(orig))
            dst.f.write(bytes(-orig['size'] % SECTOR))
        dst.f.seek(rec['at'] + 2)
        dst.f.write(struct.pack('<I', orig['lba']) + struct.pack('>I', orig['lba']) +
                    struct.pack('<I', orig['size']) + struct.pack('>I', orig['size']))
        done.append(p)
    src.close()
    dst.close()
    with open(state_path, 'w') as f:
        json.dump(state, f)
    _forget_restored(a, archives, done)
    return 0


def _folder_path(root, p):
    """Where archive p (lower case, / separated) is in a game folder: the file of that name in any case, else p."""
    d = root
    for part in p.split('/'):
        hit = next((x for x in os.listdir(d) if x.lower() == part), None) if os.path.isdir(d) else None
        d = os.path.join(d, hit or part)
    return d


def restore_folder(a, archives, keep=()):
    """Xbox: archives the builds wrote into the output folder put back as the unedited game's (copied from it), and
    archives they added taken out. keep: archives about to be written again (not copied back first)."""
    state_path = os.path.join(a.out, STATE_FILE)
    if not os.path.isfile(state_path):
        print('Nothing built into the game folder yet: nothing to put back')
        return []
    state = json.load(open(state_path))
    wanted = {x.lower() for x in archives}
    done = []
    for p in list(state['records']):
        if p.lower() not in wanted:
            continue
        rec = state['records'].pop(p)
        if p in keep:
            continue
        target = _folder_path(a.out, p)
        if rec.get('new'):
            if os.path.isfile(target):
                os.remove(target)
        else:
            shutil.copyfile(_folder_path(a.iso, p), target)
        done.append(p)
    with open(state_path, 'w') as f:
        json.dump(state, f)
    return done


def _forget_restored(a, archives, done):
    # what the last build was made from, without those archives' folders (their files were taken out)
    last = os.path.join(a.cache, 'last_build.tsv')
    if os.path.isfile(last):
        # files no longer in the test ISO show as to go in again: those of the restored archives' folders, and
        # loose ones (Master) of a name those archives hold
        held, folders = set(), ()
        index_path = os.path.join(a.cache, 'index.json')
        if os.path.isfile(index_path):
            index = json.load(open(index_path))
            folders = tuple(f.lower() + '/' for f in index['archives'])
            for p in archives:
                held.update(n.rsplit('/', 1)[-1].lower() for n in index['archives'].get(p, {}).get('names', []))

        def gone(line):
            k = line.split('\t', 1)[0].lower()
            if any(k.startswith(p.lower() + '/') for p in archives):
                return True
            return not k.startswith(folders) and k.rsplit('/', 1)[-1] in held
        keep = [l for l in open(last, encoding='utf-8') if not gone(l)]
        with open(last, 'w', encoding='utf-8', newline='\n') as f:
            f.writelines(keep)
    for p in done:
        print(f'  {p}: ' + ('the original is back in the game folder' if XBOX[0] else 'the original is back in the test ISO'))


def make_folders(a, index):
    """A folder in the mods folder for each archive, laid out as on the disc (lvls_sng/tomb.dat/ ...)."""
    made = 0
    os.makedirs(os.path.join(a.mods, 'Master'), exist_ok=True)  # its files go into every archive with their names
    for p in sorted(index['archives']):
        d = os.path.join(a.mods, *p.split('/'))
        if not os.path.isdir(d):
            os.makedirs(d)
            made += 1
    print(f'{made} archive folders made in {a.mods} ({len(index["archives"])} archives on the disc). Files in one go '
          f'into that archive only; a file it does not have is added to it.')
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    for k, v in DEFAULTS.items():
        ap.add_argument('--' + k, default=v)
    ap.add_argument('--run', action='store_true', help='start the game when it is built (PCSX2; Xbox: --recomp)')
    for group in COMPRESSION:
        for what in ('files', 'names'):
            ap.add_argument(f'--{group}-{what}', type=int, choices=(0, 1),
                            help=f'compress {what} in {GROUP_NAMES[group]} (0/1)')
    ap.add_argument('--extract', metavar='ARCHIVE', help='extract an archive (to --to) instead of building the ISO')
    ap.add_argument('--convert', metavar='MODS', help='convert a mods folder (or .rdrmod) for the other game into --to: '
                    '--platform is the game converted to, --iso its unedited game, --src-iso the source game\'s')
    ap.add_argument('--import', dest='import_', nargs='+', metavar='PATH',
                    help='.dat archives (files or folders) and files into the mods folder (--into: the folder dropped '
                         'on), converted to --platform\'s game when they are the other\'s (--src-iso)')
    ap.add_argument('--export-full', metavar='ARCHIVE', help='an archive in full (the game\'s files with your mods over '
                    'them) into --to/<archive>')
    ap.add_argument('--as', dest='as_name', help='--import: the archive a folder of files makes (mymap2.dat)')
    ap.add_argument('--into', default='', help='--import: the mods folder\'s folder they were dropped on')
    ap.add_argument('--src-iso', help='--convert: the unedited game the mod was made for')
    ap.add_argument('--src-cache', help='--convert: the source game\'s cache (default _ps2cache / _xboxcache)')
    ap.add_argument('--png2tex', nargs='+', metavar='PNG', help='convert .png images to .tex (--platform ps2) or .xtex')
    ap.add_argument('--platform', choices=('ps2', 'xbox'), default='ps2',
                    help='ps2: --iso is the unedited ISO, --out the test ISO; xbox: --iso is the unedited game folder, '
                         '--out the recomp\'s assets folder (--png2tex: .tex or .xtex)')
    ap.add_argument('--format', choices=('auto', 'dxt1', 'dxt5', 'p8', 'pa8', 'p4', 'pa4', 'rgba'), default='auto',
                    help='--png2tex: auto = as the game (Xbox DXT1/DXT5, PS2 256 colours); pa8/pa4: palette with '
                         'transparency (the menu portraits, blood, dust)')
    ap.add_argument('--like', metavar='TEX', help='--png2tex: match this game texture (format, size, mipmaps, flags)')
    ap.add_argument('--alpha', choices=('keep', 'dark', 'key', 'mask', 'book'), default='keep',
                    help='--png2tex: transparency: keep the image\'s; dark = dark parts clear; key = --key colour clear; '
                         'mask = brightness is the shape, colour white')
    ap.add_argument('--key', default='000000', help='--png2tex --alpha key: the colour to make clear, RRGGBB')
    ap.add_argument('--tolerance', type=int, default=16, help='--png2tex: how far from black / the key still clear')
    ap.add_argument('--soft', type=int, default=48, help='--png2tex: how wide the fade from clear to solid is')
    ap.add_argument('--no-mips', action='store_true', help='--png2tex: no mipmaps')
    ap.add_argument('--resize', action='store_true', help='--png2tex: resize to power of two sides, as the game')
    ap.add_argument('--new-level', choices=('sng', 'showdown'),
                    help='make a new level as new archives in the mods folder (sng: from saloon.dat; showdown: '
                         'lvls_m2 and lvls_m4 archives, the menu row, the unlock, the thumbnail)')
    ap.add_argument('--name', help='the new level\'s name (--new-level): up to 8 letters, 7 for showdown')
    ap.add_argument('--template', help='the level to start from (--new-level): saloon / ranch by default')
    ap.add_argument('--title', help='the Showdown menu\'s name for the new map')
    ap.add_argument('--subtitle', help='the Showdown menu\'s line under the name')
    ap.add_argument('--replace', action='store_true', help='(--new-level) the map\'s folders made again from the template')
    ap.add_argument('--extract-all', action='store_true',
                    help='extract every .dat of the clean ISO into --to, laid out as on the disc (swat.dat/, '
                         'lvls_sng/tomb.dat/, ...)')
    ap.add_argument('--pack', metavar='FOLDER', help='build a folder as an archive (to --to: .dat or .zip)')
    ap.add_argument('--to', help='where --extract or --pack writes')
    ap.add_argument('--files', type=int, choices=(0, 1), default=1, help='--pack: compress the files')
    ap.add_argument('--names', type=int, choices=(0, 1), default=1, help='--pack: compress the names (Dave)')
    ap.add_argument('--get', nargs='+', metavar=('ARCHIVE', 'NAME'),
                    help='copy files (names or paths) out of an archive on the clean ISO into --to')
    ap.add_argument('--restore', metavar='ARCHIVES', help='put these archives (comma separated: swat.dat,lvls_sng/tomb.dat) '
                    'back to the original in the test ISO, without a rebuild')
    ap.add_argument('--make-folders', action='store_true',
                    help='make a folder in the mods folder for each archive on the disc, laid out as on the disc')
    a = ap.parse_args()
    if a.png2tex:  # needs no dave.py
        return png_to_tex(a)
    XBOX[0] = a.platform == 'xbox'
    if XBOX[0] and os.path.normcase(a.cache) == os.path.normcase(DEFAULTS['cache']):
        a.cache = os.path.join(HERE, '_xboxcache')  # the Xbox game's archives are not the PS2's
    if a.import_:  # dropped archives and files (modconvert.py)
        import modconvert
        a.dave = a.dave or HERE
        os.makedirs(a.mods, exist_ok=True)
        return modconvert.import_main(a)
    if a.convert:  # a mod for the other game (modconvert.py)
        if not a.to:
            print('--to: the folder for the converted mod')
            return 1
        import modconvert
        return modconvert.main(a)
    for group in list(COMPRESSION):
        files, names = COMPRESSION[group]
        f, n = getattr(a, f'{group}_files'), getattr(a, f'{group}_names')
        COMPRESSION[group] = (files if f is None else bool(f), names if n is None else bool(n))
    a.dave = a.dave or HERE
    if not os.path.isfile(os.path.join(a.dave, 'dave.py')):
        print(f'No dave.py in {a.dave}: --dave <the folder with EdnessP\'s dave.py> (github.com/EdnessP)')
        return 1
    if a.extract or a.pack:
        if not a.to:
            print('--to: where to write')
            return 1
        dave = load_dave(a.dave)
        if a.extract:
            return extract(dave, a.extract, a.to)
        return pack(dave, a.pack, a.to, bool(a.files), bool(a.names))
    t0 = time.time()
    if XBOX[0]:
        if not a.iso or not os.path.isfile(os.path.join(a.iso, 'default.xbe')):
            print(f'No unedited Xbox game at "{a.iso}": --iso <the folder with its default.xbe, swat.dat, lvls_sng ...>')
            return 1
        if not a.out:
            print('--out: the game folder to build into (the recomp\'s assets folder)')
            return 1
        if os.path.normcase(os.path.realpath(a.out)) == os.path.normcase(os.path.realpath(a.iso)):
            print('The game folder to build into must not be the unedited game (it is copied from)')
            return 1
    else:
        if not a.iso or not os.path.isfile(a.iso):
            print(f'No clean game ISO at "{a.iso}": --iso <Red Dead Revolver (USA).iso, unmodified>')
            return 1
        if not a.out:
            a.out = os.path.splitext(a.iso)[0] + ' (mod test).iso'
        if os.path.abspath(a.out) == os.path.abspath(a.iso):
            print('The output ISO must not be the clean ISO')
            return 1
    os.makedirs(a.cache, exist_ok=True)
    os.makedirs(a.mods, exist_ok=True)
    iso = open_game(a.iso)
    index, dats, stamp, holders = load_index(a, iso)
    if a.new_level:
        result = new_level(a, iso, index, dats)
        iso.close()
        return result
    if a.extract_all:
        if not a.to:
            print('--to: where to write')
            iso.close()
            return 1
        result = extract_all(a, iso, index, dats)
        iso.close()
        return result
    if a.export_full:
        if not a.to:
            print('--to: the folder to export into')
            return 1
        result = export_full(a, iso, index, dats, holders, a.export_full, a.to)
        iso.close()
        return result
    if a.get:
        if len(a.get) < 2 or not a.to:
            print('--get ARCHIVE NAME [NAME ...] --to FOLDER')
            return 1
        result = get_files(a, iso, a.get[0].replace(os.sep, '/').lower(), a.get[1:], a.to)
        iso.close()
        return result
    if a.restore:
        iso.close()
        wanted = [x.strip().replace(os.sep, '/').lower() for x in a.restore.split(',') if x.strip()]
        if wanted == ['all'] and XBOX[0]:  # every archive the builds wrote back to the unedited game's
            state_path = os.path.join(a.out, STATE_FILE)
            wanted = sorted(json.load(open(state_path))['records']) if os.path.isfile(state_path) else []
            result = restore(a, wanted)
            print('The game folder has the unedited game\'s archives again')
            return result
        if wanted == ['all']:  # the whole test ISO back to the unedited one
            state_path = a.out + '.ps2build.json'
            wanted = sorted(json.load(open(state_path))['records']) if os.path.isfile(state_path) else []
            result = restore(a, wanted)
            if os.path.isfile(a.out):
                with open(a.iso, 'rb') as c:
                    c.seek(16 * SECTOR)
                    pvd = c.read(SECTOR)
                with open(a.out, 'r+b') as f:  # what the builds appended goes, and the disc's size with it
                    f.truncate(os.path.getsize(a.iso))
                    f.seek(16 * SECTOR + 80)
                    f.write(pvd[80:88])
            print('The test ISO is the unedited ISO again')
            return result
        added = set()
        if XBOX[0] and os.path.isfile(os.path.join(a.out, STATE_FILE)):
            added = {k for k, r in json.load(open(os.path.join(a.out, STATE_FILE)))['records'].items() if r.get('new')}
        unknown = [x for x in wanted if x not in index['archives'] and x not in added]
        for x in unknown:
            print(f'  {x}: not an archive on the disc')
        return restore(a, [x for x in wanted if x in index['archives'] or x in added])
    if a.make_folders:
        iso.close()
        return make_folders(a, index)
    fresh = new_archives(a.mods, index['archives'])
    loose, targeted, every, master = mod_files(a.mods, list(index['archives']) + list(fresh))
    for p in fresh:
        targeted.pop(p, None)  # built from its folder below, not over an archive of the disc
        print(f'New archive {p}: built from its folder and added to the disc')
        for w in check_view_rooms(fresh[p]):
            print('  ' + w)
    if not every:
        print(f'No mod files in {a.mods}: put the changed files there (aka_lods.rsc, ...)')
        return 1
    dave = load_dave(a.dave)
    for k in sorted(k for k in loose if k not in holders):
        print(f'  {k}: no archive on the disc has a file of that name: left out')
    # each archive's plan: (match, file path, how): 'name' replaces every file of that name; 'path' replaces the file
    # at that path, or the files of its name when there is none at the path, or is added there
    todo = {}
    for k, path in loose.items():
        if k in master:
            continue  # Master's own copy goes everywhere
        for p in holders.get(k, ()):
            todo.setdefault(p, []).append((k, path, 'name'))
    master_into = [p for p in index['archives'] if master_target(p)]
    modes = master_modes(a.mods)
    if master:
        n_add = sum(1 for k in master if master_mode(k, modes, holders) == 'add')
        print(f'Master: {len(master)} file(s): {len(master) - n_add} replacing the files of their name, {n_add} added '
              f'to every level .dat, front.dat and swat.dat')
    for k, path in master.items():
        if master_mode(k, modes, holders) == 'replace':
            for p in holders.get(k, ()):
                if master_target(p):
                    todo.setdefault(p, []).append((k, path, 'name'))
        else:
            for p in master_into:
                todo.setdefault(p, []).append((k, path, 'all'))
    for p, items in targeted.items():
        for inner, path in items:
            todo.setdefault(p, []).append((inner, path, 'path'))
    if not todo and not fresh:
        print('Nothing to build: none of the mods is a file of the game')
        return 1

    built = {}
    new_built = {}
    if fresh:
        dave_new = load_dave(a.dave)
        quiet_prompts(dave_new)
        for p, folder in fresh.items():
            out = os.path.join(a.cache, 'new_' + p.replace('/', '_'))
            settings = build_settings(p)
            key = hashlib.md5(json.dumps(list(settings) + ['layout3', 'xbox' if XBOX[0] else 'ps2']).encode())
            for r_, _d, fs in sorted(os.walk(folder)):
                for f in sorted(fs):
                    path = os.path.join(r_, f)
                    key.update(os.path.relpath(path, folder).lower().encode() + hashlib.md5(open(path, 'rb').read()).digest())
            key = key.hexdigest()
            if os.path.isfile(out) and os.path.isfile(out + '.key') and open(out + '.key').read() == key:
                print(f'{p}: as last time, the last build kept')
            else:
                compfiles, compnames, dirs_, align, level = settings
                for stale in (out, out + '.key'):
                    if os.path.exists(stale):
                        os.remove(stale)
                dave_new.build_dave(folder, out, compfiles, 0, level, compnames, dirs_, align, False)
                game_layout(out, None, new_archive_order(p, folder, iso, dats, index['archives']))
                with open(out + '.key', 'w') as f:
                    f.write(key)
                print(f'Built new {p}: {sum(len(fs) for _r, _d, fs in os.walk(folder))} files, {os.path.getsize(out):,} bytes')
            new_built[p] = out
    for p, items in sorted(todo.items()):
        info = index['archives'][p]
        clean = os.path.join(a.cache, 'clean', p.replace('/', '_'))
        if not os.path.isdir(clean):
            print(f'Extracting {p} (once)...')
            src = os.path.join(a.cache, 'src.dat')
            with open(src, 'wb') as f:
                f.write(iso.read(dats[p]))
            dave.read_dave(src, clean)
            os.remove(src)
        out = os.path.join(a.cache, 'built_' + p.replace('/', '_'))
        # the same mods as the last build of this archive: that build again, no rebuilding
        settings = build_settings(p, info)
        key = hashlib.md5(json.dumps(list(settings) + ['layout3', 'xbox' if XBOX[0] else 'ps2']).encode())
        for k, path, how in sorted(items):
            key.update(k.encode() + how.encode() + hashlib.md5(open(path, 'rb').read()).digest())
        key = key.hexdigest()
        if os.path.isfile(out) and os.path.isfile(out + '.key') and open(out + '.key').read() == key:
            print(f'{p}: its mods are as last time, the last build kept')
            built[p] = out
            continue
        work = os.path.join(a.cache, 'work')
        if os.path.isdir(work):
            shutil.rmtree(work)
        try:  # hard links: the unchanged files are not copied (the mods replace their links, not the files)
            shutil.copytree(clean, work, copy_function=os.link)
        except OSError:
            if os.path.isdir(work):
                shutil.rmtree(work)
            shutil.copytree(clean, work)
        by_name, by_path = {}, {}
        for root, _dirs, fs in os.walk(work):
            for f in fs:
                full = os.path.join(root, f)
                by_name.setdefault(f.lower(), []).append(full)
                by_path[os.path.relpath(full, work).replace(os.sep, '/').lower()] = full

        def put(src, target):
            if os.path.exists(target):
                os.remove(target)  # the link only, never the cached clean file
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copyfile(src, target)
        replaced, added = 0, []
        for k, path, how in items:
            name = k.rsplit('/', 1)[-1]
            if how == 'path' and '/' in k and k in by_path:
                put(path, by_path[k])
                replaced += 1
            elif by_name.get(name):
                for target in by_name[name]:
                    put(path, target)
                    replaced += 1
            elif how == 'all':  # a Master file the archive lacks: added at its top
                put(path, os.path.join(work, name))
                added.append(k)
            else:  # new to the archive: added where it sits in the archive folder
                put(path, os.path.join(work, *os.path.relpath(path, os.path.join(a.mods, *p.split('/'))).split(os.sep)))
                added.append(k)
        for w in check_view_rooms(work):
            print('  ' + w)
        for stale in (out, out + '.key'):
            if os.path.exists(stale):
                os.remove(stale)  # dave.py asks before overwriting
        print(f'Building {p}: {replaced} files replaced' + (f', {len(added)} added ({", ".join(added[:8])}{" ..." if len(added) > 8 else ""})' if added else '') +
              f' ({", ".join(sorted(k for k, _p, _h in items)[:8])}{" ..." if len(items) > 8 else ""})')
        # packed as the original was (its file alignment), so it fits where the original was
        compfiles, compnames, dirs, align, level = settings
        dave.build_dave(work, out, compfiles, 0, level, compnames, dirs, align, False)
        table = file_table(iso.read_head(dats[p]))
        if compfiles != info['compfiles']:
            table = {k: (v[0], compfiles) for k, v in table.items()}  # compression changed in the settings: as asked
        n = game_layout(out, table, new_archive_order(p, work, iso, dats, index['archives']) if added else None)
        print(f'  laid out as the game\'s: its load order, packed in sectors' + (f', {n} files stored/compressed as the original' if n else ''))
        with open(out + '.key', 'w') as f:
            f.write(key)
        built[p] = out
    iso.close()
    if XBOX[0]:
        return write_folder(a, built, new_built, every, t0)

    # the test ISO: last run's kept and put back as the clean one was where that run wrote, else a fresh copy
    state_path = a.out + '.ps2build.json'
    state = json.load(open(state_path)) if os.path.isfile(state_path) and os.path.isfile(a.out) else None
    clean_size = os.path.getsize(a.iso)
    if state and state.get('stamp') == stamp:
        print(f'Restoring what the last run changed in {os.path.basename(a.out)}...')
        src = Iso(a.iso)
        dst = Iso(a.out, 'r+b')
        clean_files = src.files()
        for p, rec in reversed([kv for kv in state['records'].items() if kv[1].get('new')]):
            dst.restore_new(rec)  # the files last run added, newest first
        for p, rec in state['records'].items():
            if rec.get('new'):
                continue
            orig = clean_files[p]
            if rec['lba'] == orig['lba']:  # written in place: the original's bytes back
                dst.f.seek(orig['lba'] * SECTOR)
                dst.f.write(src.read(orig))
                pad = -orig['size'] % SECTOR
                dst.f.write(bytes(pad))
            dst.f.seek(rec['at'] + 2)
            dst.f.write(struct.pack('<I', orig['lba']) + struct.pack('>I', orig['lba']) +
                        struct.pack('<I', orig['size']) + struct.pack('>I', orig['size']))
        dst.f.seek(dst.pvd_at + 80)
        dst.f.write(struct.pack('<I', src.blocks) + struct.pack('>I', src.blocks))
        dst.f.truncate(clean_size)
        src.close()
        dst.close()
    else:
        print(f'Copying the clean ISO to {a.out}...')
        shutil.copyfile(a.iso, a.out)
    iso = Iso(a.out, 'r+b')
    iso.blocks = clean_size // SECTOR
    files = iso.files()
    extents = sorted(r['lba'] for r in files.values())
    records = {}
    for p, out in built.items():
        data = open(out, 'rb').read()
        how = iso.replace(files[p], data, extents)
        f2 = iso.files()[p]
        records[p] = {'at': f2['at'], 'lba': f2['lba']}
        print(f'  {p}: {len(data):,} bytes, {how}')
    for p, out in new_built.items():
        data = open(out, 'rb').read()
        d, _, name = p.rpartition('/')
        try:
            records[p] = iso.add_file(d, name, data)
            print(f'  {p}: {len(data):,} bytes, added to the disc')
        except ValueError as e:
            print(f'  {p}: not added: {e}')
    iso.close()
    with open(state_path, 'w') as f:
        json.dump({'stamp': stamp, 'records': records}, f)
    # read back what the disc now holds
    iso = Iso(a.out)
    files = iso.files()
    for p, out in list(built.items()) + [(p, o) for p, o in new_built.items() if p in records]:
        ok = p in files and hashlib.md5(iso.read(files[p])).digest() == hashlib.md5(open(out, 'rb').read()).digest()
        print(f'  {p} on the new ISO: {"matches" if ok else "DIFFERS"}')
    iso.close()
    # for the builder app: what this build was made from
    with open(os.path.join(a.cache, 'last_build.tsv'), 'w', encoding='utf-8', newline='\n') as f:
        for k, path in sorted(every.items()):
            f.write(k + '\t' + hashlib.md5(open(path, 'rb').read()).hexdigest() + '\n')
    print(f'Done in {time.time() - t0:.0f} s: {a.out}')
    if a.run:
        if a.pcsx2 and os.path.isfile(a.pcsx2):
            import subprocess
            subprocess.Popen([a.pcsx2, '-fastboot', '--', a.out], cwd=os.path.dirname(a.pcsx2))
            print('PCSX2 started')
        else:
            print(f'No PCSX2 at {a.pcsx2}: --pcsx2 <path to pcsx2-qt.exe>')
    return 0


def write_folder(a, built, new_built, every, t0):
    """Xbox: the built archives into the game folder (the recomp's assets), over the game's; the archives the last
    build wrote that this one does not are put back as the unedited game's first, and its added ones taken out."""
    if not os.path.isfile(os.path.join(a.out, 'default.xbe')):
        if os.path.isdir(a.out) and os.listdir(a.out):
            print(f'{a.out} is not a game folder (no default.xbe) and not empty: pick the recomp\'s assets folder')
            return 1
        print(f'Copying the unedited game to {a.out} (once)...')
        shutil.copytree(a.iso, a.out, dirs_exist_ok=True)
    state_path = os.path.join(a.out, STATE_FILE)
    state = json.load(open(state_path)) if os.path.isfile(state_path) else {'records': {}}
    now = set(built) | set(new_built)
    back = restore_folder(a, list(state['records']), keep=now) if state['records'] else []
    for p in back:
        print(f'  {p}: the unedited game\'s again (no mods for it this time)')
    records = {}
    for p, out in list(built.items()) + list(new_built.items()):
        target = _folder_path(a.out, p)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(out, target)
        with open(target, 'ab') as f:  # the game's archives end on a whole 2048 byte sector
            f.write(bytes(-os.path.getsize(out) % SECTOR))
        records[p] = {'new': p in new_built}
        same = os.path.getsize(target) == os.path.getsize(out) + (-os.path.getsize(out) % SECTOR)
        print(f'  {p}: {os.path.getsize(out):,} bytes, ' + ('added to the game' if p in new_built else 'written') +
              ('' if same else ' (DIFFERS: the copy is not whole)'))
    with open(state_path, 'w') as f:
        json.dump({'records': records}, f)
    with open(os.path.join(a.cache, 'last_build.tsv'), 'w', encoding='utf-8', newline='\n') as f:
        for k, path in sorted(every.items()):
            f.write(k + '\t' + hashlib.md5(open(path, 'rb').read()).hexdigest() + '\n')
    print(f'Done in {time.time() - t0:.0f} s: {a.out}')
    if a.run:
        if a.recomp and os.path.isfile(a.recomp):
            import subprocess
            subprocess.Popen([a.recomp], cwd=os.path.dirname(a.recomp))
            print('The recomp started')
        else:
            print(f'No recomp at {a.recomp}: --recomp <its RDR.exe>')
    return 0


if __name__ == '__main__':
    sys.exit(main())
