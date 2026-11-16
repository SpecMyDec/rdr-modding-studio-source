"""DISC > Data Editor: the rest of the RDR Data Editor app's working, for the tab laid out as the app.

    the Workbench        a folder that holds files freely (beside the mods folder, never built)
    Saved Mods           named sessions: the mods folder + the Workbench, kept by name; .rdrmod export / import
                         (the app's format: the mods folder's layout, master_modes.txt, mod.ini, Workbench/)
    HUD text             .strtbl tables as rows (every language block), new / renamed / deleted IDs, lines
    Flash screens        .pck (menus, journal, shops, popups): their text edited in place (rdrstudio/pck.py)
    Convert images       .png -> the game's .tex (see-through kinds too: dark, key colour, mask, book)
    Export .dat          an archive in full (with your mods laid in) into the Workbench
    Delete new files     a .dat folder's files the archive has no file of the name of
    New File             an empty file in a .dat folder

Routes de_x_* (studio.py hands them here). Paths are always relative to the mods folder or the Workbench; nothing
here takes a path on the machine from the page.
"""

from __future__ import annotations

import base64
import contextlib
import io
import os
import re
import shutil
import struct
import sys
import time
import zipfile

from .dataeditor import PB, _LIB, Busy  # noqa: F401  (PB: the app's build script)

if _LIB not in sys.path:
    sys.path.insert(0, _LIB)
import strtbl_tool as ST  # noqa: E402

_SCENES = {}          # path -> ((path, mtime, size), pckscene.Scene): the open Flash screens
_PCK_SOURCES = {}     # 'disc' -> ((disc path, mtime), [(name, Scene)]): every .pck of the working disc
_TTFS = {}            # 'list' -> the fonts a font can be filled from
_FE_BANK = {}         # 'rows' -> ((disc, mtime), the front-end sound bank's definition rows)

# The screens' sounds: a .pck holds no audio (its sound objects are 8-byte stubs nothing points at). Its scripts play
# sounds by the game's own sound names -- soundScroll = new Sound(); soundScroll.attachSound("Menu_HandScroll") --
# read from the code by pckavm.py; the name is a row of a bank definition (snd_<n>.csv), the bank snd_<n>.bd.
XBOX_EXTS = ('.xrsc', '.xrnt', '.xznes', '.xtex', '.xmod', '.xbd', '.xhd')


# ---------------------------------------------------------------- helpers

def write_folder_file(folder, archive):
    """The marker that says which of the disc's folders a .dat folder belongs in (lvls_sng, lvls_m2, lvls_m4, or
    '.' for the top: swat.dat, front.dat), so an import puts it back there by itself."""
    archive = archive.replace('\\', '/').lower()
    home = archive.rsplit('/', 1)[0] if '/' in archive else '.'
    with open(os.path.join(folder, PB.FOLDER_FILE), 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(home + '\n# RDR Modding Studio: this folder is %s, and belongs in %s on the disc. Import .dat puts it '
                 'there; the build never packs this file.\n' % (archive.rsplit('/', 1)[-1],
                                                             'the top folder' if home == '.' else home))


def read_folder_file(text):
    """The disc folder a marker names, or None."""
    for line in (text or '').splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            return line.strip('/').lower()
    return None


def recycle(paths):
    """To the Recycle Bin (as the app does); deleted outright where that is not possible."""
    paths = [os.path.abspath(p) for p in paths if os.path.exists(p)]
    if not paths:
        return
    try:
        import ctypes
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", ctypes.c_void_p),
                        ("pTo", ctypes.c_void_p), ("fFlags", ctypes.c_ushort),
                        ("fAnyOperationsAborted", wintypes.BOOL), ("hNameMappings", ctypes.c_void_p),
                        ("lpszProgressTitle", wintypes.LPCWSTR)]
        buf = ctypes.create_unicode_buffer("\0".join(paths) + "\0\0")
        # FO_DELETE, FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
        op = SHFILEOPSTRUCTW(None, 3, ctypes.addressof(buf), None, 0x40 | 0x10 | 0x04 | 0x400, False, None, None)
        if ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0:
            if not any(os.path.exists(p) for p in paths):
                return
    except Exception:                                      # noqa: BLE001
        pass
    for p in paths:
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
        elif os.path.exists(p):
            os.remove(p)


def _clean_name(name):
    name = os.path.basename((name or '').replace('\\', '/')).strip()
    if not name or name in ('.', '..') or re.search(r'[<>:"|?*\x00-\x1f]', name):
        raise ValueError('not a usable name: %r' % name)
    return name


@contextlib.contextmanager
def _printing_to(log):
    """The app's scripts print what they do: each line into the job log."""
    class W(io.TextIOBase):
        def __init__(self):
            self.buf = ''

        def write(self, s):
            self.buf += s
            while '\n' in self.buf:
                line, self.buf = self.buf.split('\n', 1)
                if line.strip():
                    log(line)
            return len(s)
    old = sys.stdout
    sys.stdout = W()
    try:
        yield
    finally:
        sys.stdout = old


def _f(v, default=0.8):
    """A scale from the page: 0 is a real value (some lines have it), only a missing one is the default."""
    try:
        return float(v) if v is not None and v != '' else default
    except (TypeError, ValueError):
        return default


class More:
    def __init__(self, de):
        self.de = de
        self.saved = os.path.join(de.home, 'Saved Mods')
        os.makedirs(self.saved, exist_ok=True)
        self.current_path = os.path.join(de.home, 'current_mod.txt')

    # ------------------------------------------------------------ the two areas

    def base(self, where):
        return self.de.workbench if where == 'workbench' else self.de.mods

    def path(self, where, rel):
        return self.de._safe(rel or '', self.base(where))

    def _busy_check(self, where):
        if where != 'workbench' and self.de.job['state'] == 'running':
            raise Busy('A build is running: wait for it to finish before changing the mods folder.')

    def tree(self, where):
        root = self.base(where)
        out = []
        for r, ds, fs in os.walk(root):
            ds.sort(key=str.lower)
            rel = os.path.relpath(r, root).replace(os.sep, '/')
            rel = '' if rel == '.' else rel
            for d in ds:
                out.append({'path': (rel + '/' if rel else '') + d, 'dir': True})
            for f in sorted(fs, key=str.lower):
                full = os.path.join(r, f)
                st = os.stat(full)
                out.append({'path': (rel + '/' if rel else '') + f, 'dir': False, 'size': st.st_size,
                            'mtime': int(st.st_mtime)})
        return {'root': root, 'items': out}

    def new_folder(self, where, parent, name):
        self._busy_check(where)
        p = os.path.join(self.path(where, parent), _clean_name(name))
        if os.path.exists(p):
            raise ValueError('%s is already there' % name)
        os.makedirs(p)
        return os.path.relpath(p, self.base(where)).replace(os.sep, '/')

    def new_file(self, where, folder, name):
        self._busy_check(where)
        p = os.path.join(self.path(where, folder), _clean_name(name))
        if os.path.exists(p):
            raise ValueError('%s is already there' % name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, 'wb').close()
        return os.path.relpath(p, self.base(where)).replace(os.sep, '/')

    def rename(self, where, rel, name):
        self._busy_check(where)
        src = self.path(where, rel)
        if src == os.path.abspath(self.base(where)):
            raise ValueError('the folder itself cannot be renamed here')
        dst = os.path.join(os.path.dirname(src), _clean_name(name))
        if os.path.exists(dst) and os.path.normcase(dst) != os.path.normcase(src):
            raise ValueError('%s is already there' % name)
        os.replace(src, dst)
        return os.path.relpath(dst, self.base(where)).replace(os.sep, '/')

    def remove(self, where, rels):
        self._busy_check(where)
        root = os.path.abspath(self.base(where))
        ps = [self.path(where, r) for r in rels]
        ps = [p for p in ps if p != root]
        recycle(ps)
        return len(ps)

    def upload(self, where, folder, files):
        """files [{name: relative path (a dropped folder keeps its layout), data: base64}]."""
        self._busy_check(where)
        dest = self.path(where, folder)
        done = []
        for f in files:
            parts = [x for x in re.split(r'[\\/]', f.get('name') or '') if x not in ('', '.', '..')]
            if not parts:
                continue
            out = self.de._safe('/'.join(parts), dest)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, 'wb') as fh:
                fh.write(base64.b64decode(f.get('data') or ''))
            done.append('/'.join(parts))
        return done

    # ------------------------------------------------------------ .dat folders that know where they belong

    def dat_home(self, name, marker=None):
        """Where a .dat folder named `name` goes in the mods folder -> (key, why). In order: its marker file (written
        by every export); the folder of the game's archive of that name; a name ending in 2 / 4 (a Showdown map's
        pair: lvls_m2/<map>2.dat, lvls_m4/<map>4.dat -- the game loads Showdown maps from nowhere else); else
        lvls_sng (a story level)."""
        name = os.path.basename((name or '').replace('\\', '/').strip('/')).lower()
        if not name.endswith('.dat'):
            name += '.dat'
        index = self.de.index()[0]
        archives = [a.lower() for a in index['archives']]
        dirs = {a.rsplit('/', 1)[0] for a in archives if '/' in a}
        home = read_folder_file(marker) if marker else None
        if home == '.':
            return name, 'its folder file says the top folder'
        if home in dirs:
            return home + '/' + name, 'its folder file says ' + home
        hit = next((a for a in archives if a.rsplit('/', 1)[-1] == name), None)
        if hit:
            return hit, 'the game has it there'
        m = re.fullmatch(r'(.+)([24])\.dat', name)
        if m:
            return 'lvls_m%s/%s' % (m.group(2), name), 'a Showdown map (%s-player): the game loads those from lvls_m%s' % (
                m.group(2), m.group(2))
        return 'lvls_sng/' + name, 'a new story level'

    def import_dir(self, name, marker, files):
        """A picked folder (<name>.dat with its files) into the mods folder where it belongs (dat_home)."""
        key, why = self.dat_home(name, marker)
        added = self.upload('mods', key, files)
        return {'folder': key, 'why': why, 'added': added}

    def import_zip(self, fname, data):
        """A .dat folder exported as a .zip (Export .dat folder) into the mods folder where its marker says."""
        self._busy_check('mods')
        z = zipfile.ZipFile(io.BytesIO(data))
        names = [i.filename.replace('\\', '/') for i in z.infolist() if not i.is_dir()]
        if not names:
            raise ValueError('%s is empty' % fname)
        tops = {n.split('/', 1)[0] for n in names}
        strip = ''
        if len(tops) == 1 and all('/' in n for n in names):
            strip = tops.pop() + '/'
            name = strip[:-1]
        else:
            name = re.sub(r'(?i)\.zip$', '', os.path.basename(fname))
        name = re.sub(r'(?i)\.dat$', '', name)
        marker = None
        if strip + PB.FOLDER_FILE in names:
            marker = z.read(strip + PB.FOLDER_FILE).decode('utf-8', 'replace')
        key, why = self.dat_home(name, marker)
        dest = self.path('mods', key)
        n = 0
        for nm in names:
            rel = nm[len(strip):] if nm.startswith(strip) else nm
            parts = [x for x in rel.split('/') if x not in ('', '.', '..')]
            if not parts:
                continue
            out = self.de._safe('/'.join(parts), dest)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, 'wb') as fh:
                fh.write(z.read(nm))
            n += 1
        if not marker:
            write_folder_file(dest, key)
        return {'folder': key, 'why': why, 'files': n}

    def export_zip(self, archive):
        """A .dat folder of the mods folder (your files for it) as <name>.dat.zip in the exports folder, its marker
        inside -- Import .dat puts it back in the same disc folder (lvls_sng / lvls_m2 / lvls_m4) on any computer."""
        from .modpack import _exports_dir
        archive = archive.replace('\\', '/').lower()
        src = self.path('mods', archive)
        if not os.path.isdir(src) or not any(fs for _r, _d, fs in os.walk(src)):
            raise ValueError('%s has no files in your mods folder to export' % archive)
        name = archive.rsplit('/', 1)[-1]
        out_dir = _exports_dir()
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, name + '.zip')
        home = archive.rsplit('/', 1)[0] if '/' in archive else '.'
        n = 0
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
            for r, _d, fs in os.walk(src):
                for f in sorted(fs):
                    if f.lower() == PB.FOLDER_FILE:
                        continue
                    full = os.path.join(r, f)
                    z.write(full, name + '/' + os.path.relpath(full, src).replace(os.sep, '/'))
                    n += 1
            z.writestr(name + '/' + PB.FOLDER_FILE,
                       home + '\n# RDR Modding Studio: this folder is %s, and belongs in %s on the disc. Import .dat '
                       'puts it there; the build never packs this file.\n' % (name, 'the top folder' if home == '.' else home))
        return {'path': out, 'files': n, 'home': home}

    def paste_text(self, folder, text):
        dest = self.path('workbench', folder)
        os.makedirs(dest, exist_ok=True)
        n, out = 1, os.path.join(dest, 'Pasted text.txt')
        while os.path.exists(out):
            n += 1
            out = os.path.join(dest, 'Pasted text (%d).txt' % n)
        with open(out, 'w', encoding='utf-8', newline='') as fh:
            fh.write(text)
        return os.path.relpath(out, self.de.workbench).replace(os.sep, '/')

    def transfer(self, src_where, rels, dst_where, to, move=False):
        """Copy (or move) files and folders, within an area or between the mods folder and the Workbench."""
        self._busy_check(dst_where)
        if move:
            self._busy_check(src_where)
        dest = self.path(dst_where, to)
        if os.path.isfile(dest):
            dest = os.path.dirname(dest)
        os.makedirs(dest, exist_ok=True)
        done = []
        for r in rels:
            src = self.path(src_where, r)
            if not os.path.exists(src) or src == os.path.abspath(self.base(src_where)):
                continue
            out = os.path.join(dest, os.path.basename(src))
            mark = os.path.join(src, PB.FOLDER_FILE)
            if dst_where == 'mods' and os.path.isdir(src) and src.lower().endswith('.dat') and os.path.isfile(mark):
                # its folder file says which disc folder it is (an export of lvls_m2/x2.dat dropped on lvls_sng)
                key, _why = self.dat_home(os.path.basename(src), open(mark, encoding='utf-8', errors='replace').read())
                out = self.path('mods', key)
                os.makedirs(os.path.dirname(out), exist_ok=True)
            if os.path.normcase(os.path.abspath(out)) == os.path.normcase(src):
                continue
            if os.path.isdir(src) and (os.path.abspath(dest) + os.sep).startswith(src + os.sep):
                raise ValueError('a folder cannot go inside itself')
            if move:
                if os.path.exists(out):
                    recycle([out])
                shutil.move(src, out)
            elif os.path.isdir(src):
                shutil.copytree(src, out, dirs_exist_ok=True)
            else:
                shutil.copyfile(src, out)
            done.append(os.path.relpath(out, self.base(dst_where)).replace(os.sep, '/'))
        return done

    def delete_new(self, folder):
        """A .dat folder's files the archive has no file of the name of (they would be ADDED): to the Recycle Bin."""
        self._busy_check('mods')
        index, _d, _h = self.de.index()
        if folder not in index['archives']:
            raise ValueError('pick one of the game\'s .dat folders (a new archive is all new files)')
        names = {n.rsplit('/', 1)[-1].lower() for n in index['archives'][folder].get('names', [])}
        root = self.de.folder_path(folder)
        gone = []
        for r, _ds, fs in os.walk(root):
            for f in fs:
                if f.lower() not in names:
                    gone.append(os.path.join(r, f))
        recycle(gone)
        return [os.path.relpath(p, root).replace(os.sep, '/') for p in gone]

    def open_in_explorer(self, where, rel):
        p = self.path(where, rel) if rel else self.base(where)
        if where == 'disc':
            p = os.path.dirname(os.path.abspath(self.de.s.modded_path))
        elif where == 'home':
            p = self.de.home
        elif where == 'saved':
            p = self.saved
        if os.path.isfile(p):
            import subprocess
            subprocess.Popen(['explorer.exe', '/select,', p])
        else:
            os.makedirs(p, exist_ok=True)
            os.startfile(p)                                # noqa: S606
        return p

    # ------------------------------------------------------------ Saved Mods

    def current(self):
        try:
            return open(self.current_path, encoding='utf-8').read().strip()
        except OSError:
            return ''

    def _set_current(self, name):
        with open(self.current_path, 'w', encoding='utf-8') as fh:
            fh.write(name or '')

    def saved_mods(self):
        out = []
        for n in sorted(os.listdir(self.saved), key=str.lower):
            d = os.path.join(self.saved, n)
            if not os.path.isdir(d):
                continue
            files = sum(len(fs) for _r, _d, fs in os.walk(os.path.join(d, 'RDRMods')))
            wb = sum(len(fs) for _r, _d, fs in os.walk(os.path.join(d, 'Workbench')))
            out.append({'name': n, 'files': files, 'workbench': wb,
                        'when': time.strftime('%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(d)))})
        return {'mods': out, 'current': self.current()}

    @staticmethod
    def _copy_contents(src, dst):
        os.makedirs(dst, exist_ok=True)
        if os.path.isdir(src):
            for n in os.listdir(src):
                s = os.path.join(src, n)
                (shutil.copytree(s, os.path.join(dst, n), dirs_exist_ok=True) if os.path.isdir(s)
                 else shutil.copyfile(s, os.path.join(dst, n)))

    @staticmethod
    def _empty(folder, keep=()):
        if os.path.isdir(folder):
            recycle([os.path.join(folder, n) for n in os.listdir(folder) if n not in keep])

    def save_mod(self, name):
        self._busy_check('mods')
        name = _clean_name(name)
        d = os.path.join(self.saved, name)
        if os.path.isdir(d):
            shutil.rmtree(d)
        self._copy_contents(self.de.mods, os.path.join(d, 'RDRMods'))
        self._copy_contents(self.de.workbench, os.path.join(d, 'Workbench'))
        with open(os.path.join(d, 'mod.ini'), 'w', encoding='utf-8') as fh:
            fh.write('# a Mod saved in the RDR Modding Studio (DISC > Data Editor)\nsaved=%s\n'
                     % time.strftime('%Y-%m-%d %H:%M'))
        self._set_current(name)
        return name

    def clear_for_reset(self):
        """Reset to stock, with the Data Editor's own mod files too: they are kept as a Saved Mod ("Before reset
        <time>") and the mods folder emptied, so the next Build does not put them back. -> (saved name, files)."""
        files = [os.path.join(r, f) for r, _d, fs in os.walk(self.de.mods) for f in fs
                 if f.lower() != PB.MODES_FILE.lower()]
        # the editors' working copies in the Workbench (Flash screens, HUD text): made from the disc when first
        # opened and edited there -- left, the Studio keeps showing the edits the disc no longer has
        copies = [os.path.join(self.de.workbench, d) for d in ('Flash screens', 'HUD text')
                  if os.path.isdir(os.path.join(self.de.workbench, d))]
        n_copies = sum(len(fs) for c in copies for _r, _d, fs in os.walk(c))
        if not files and not n_copies:
            return None, 0
        name = self.save_mod('Before reset %s' % time.strftime('%Y-%m-%d %H%M'))
        self._empty(self.de.mods)
        os.makedirs(os.path.join(self.de.mods, 'Master'), exist_ok=True)
        if copies:
            recycle(copies)
        _SCENES.clear()
        try:
            self.de.mirror_reset()
        except Exception:                                       # noqa: BLE001
            pass
        return name, len(files) + n_copies

    def load_mod(self, name):
        """The Mod's files in place of the mods folder's and the Workbench's (those go to the Recycle Bin)."""
        self._busy_check('mods')
        d = os.path.join(self.saved, _clean_name(name))
        if not os.path.isdir(d):
            raise ValueError('no saved Mod named %s' % name)
        self._empty(self.de.mods)
        self._empty(self.de.workbench)
        self._copy_contents(os.path.join(d, 'RDRMods'), self.de.mods)
        self._copy_contents(os.path.join(d, 'Workbench'), self.de.workbench)
        os.makedirs(os.path.join(self.de.mods, 'Master'), exist_ok=True)
        self._set_current(name)
        return name

    def delete_mod(self, name):
        d = os.path.join(self.saved, _clean_name(name))
        recycle([d])
        if self.current() == name:
            self._set_current('')
        return True

    def export_mod(self):
        """The Mod as one .rdrmod (a zip: the mods folder's layout, mod.ini, Workbench/), as the app writes it."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
            for root, sub in ((self.de.mods, ''), (self.de.workbench, 'Workbench/')):
                for r, _ds, fs in os.walk(root):
                    for f in fs:
                        full = os.path.join(r, f)
                        z.write(full, sub + os.path.relpath(full, root).replace(os.sep, '/'))
            z.writestr('mod.ini', '# a Mod exported from the RDR Modding Studio (DISC > Data Editor)\nexported=%s\n'
                       % time.strftime('%Y-%m-%d %H:%M'))
        return buf.getvalue()

    def import_mod(self, name, data):
        """A .rdrmod: its .dat folders into the mods folder, its Workbench into a new Workbench folder."""
        self._busy_check('mods')
        stem = _clean_name(os.path.splitext(os.path.basename(name or 'Imported mod'))[0])
        z = zipfile.ZipFile(io.BytesIO(data))
        mods = wb = xbox = 0
        root = os.path.abspath(self.de.mods)
        for i in z.infolist():
            if i.is_dir():
                continue
            parts = [x for x in i.filename.replace('\\', '/').split('/') if x not in ('', '.', '..')]
            if not parts or parts == ['mod.ini']:
                continue
            if parts[0].lower() == 'workbench':
                out = self.de._safe('/'.join([stem] + parts[1:]), self.de.workbench)
                wb += 1
            else:
                if parts[0].lower() == 'master':
                    parts[0] = 'Master'
                out = self.de._safe('/'.join(parts), root)
                mods += 1
                if os.path.splitext(parts[-1])[1].lower() in XBOX_EXTS:
                    xbox += 1
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, 'wb') as fh:
                fh.write(z.read(i))
        note = '%s: %d mod file(s) into the mods folder' % (name, mods) + (
            ', its Workbench (%d) into Workbench\\%s' % (wb, stem) if wb else '')
        if xbox:
            note += ('. %d are Xbox files: the PS2 game does not use them (convert the Mod with the RDR Data Editor '
                     'app\'s Convert Mod first)' % xbox)
        return note

    # ------------------------------------------------------------ HUD text (.strtbl)

    def strtbls(self, archive=None):
        """The text tables the game's archives (or one) hold, and the ones the .dat folders have of their own."""
        index, _d, _h = self.de.index()
        out = []
        for a, info in sorted(index['archives'].items()):
            if archive and a != archive:
                continue
            for n in info.get('names', []):
                if n.lower().endswith('.strtbl'):
                    mine = os.path.join(self.de.folder_path(a), *n.split('/'))
                    out.append({'archive': a, 'name': n, 'edited': os.path.isfile(mine)})
        return out

    def hud_open(self, archive, name, folder='HUD text'):
        """The table to edit: its .dat folder's copy when it has one, else a copy in the Workbench (your edited
        copy, if the Workbench has it already, else the game's)."""
        mine = os.path.join(self.de.folder_path(archive), *name.split('/'))
        if os.path.isfile(mine):
            return {'where': 'mods', 'key': os.path.relpath(mine, self.de.mods).replace(os.sep, '/'), 'archive': archive}
        wb_rel = '%s/%s/%s' % (folder, archive.replace('/', '_'), name.rsplit('/', 1)[-1])
        wb = self.de._safe(wb_rel, self.de.workbench)
        if not os.path.isfile(wb):
            files = self.de._disc_files()
            raw = None
            if archive in files:
                _p, raw = PB.read_entry(self.de._disc_read(files[archive]), name)
            if raw is None:
                raise ValueError('%s has no %s' % (archive, name))
            os.makedirs(os.path.dirname(wb), exist_ok=True)
            with open(wb, 'wb') as fh:
                fh.write(raw)
        return {'where': 'workbench', 'key': wb_rel, 'archive': archive}

    def strtbl_read(self, where, key):
        d = open(self.path(where, key), 'rb').read()
        t = ST.read(d)
        names = {ST.elf(n): n for n in t['names']}
        blocks = []
        for ents in t['blocks']:
            blocks.append([{'id': names.get(h, '#%08X' % h), 'font': font, 'text': text, 'sx': sx, 'sy': sy}
                           for h, _a, font, text, sx, sy, _b in ents])
        langs = []
        for b in range(len(t['blocks'])):
            ls = [i + 1 for i, x in enumerate(t['lang_block']) if x == b]
            langs.append(('Language %d' % ls[0] if len(ls) == 1 else 'Languages ' + ', '.join(map(str, ls)))
                         + (' (English)' if 1 in ls else ''))
        return {'where': where, 'key': key, 'langs': langs, 'blocks': blocks, 'count': len(t['names'])}

    def strtbl_save(self, where, key, blocks, put_in=None):
        """Every block's rows [{id, font, text, sx, sy}] written back over the table's own layout (the base's IDs
        keep their order, new ones go where they sort, a hash clash is refused)."""
        self._busy_check(where)
        p = self.path(where, key)
        t = ST.read(open(p, 'rb').read())
        if len(blocks) != len(t['blocks']):
            raise ValueError('the table has %d language blocks, not %d' % (len(t['blocks']), len(blocks)))
        wanted = []
        for rows in blocks:
            for r in rows:
                i = (r.get('id') or '').strip()
                if not i:
                    raise ValueError('an entry has no ID')
                # the game's own IDs may hold spaces ("Hostage Grab"); new ones are letters, digits and _
                if not i.startswith('#') and i not in t['names'] and not re.match(r'^[A-Za-z0-9_]+$', i):
                    raise ValueError('%s: an ID is letters, digits and _' % i)
                if i not in wanted:
                    wanted.append(i)
        ids = [i for i in wanted if not i.startswith('#')]
        names = [n for n in t['names'] if n in ids]
        for n in sorted(set(ids) - set(names), key=str.lower):
            at = next((k for k, x in enumerate(names) if x.lower() > n.lower()), len(names))
            names.insert(at, n)
        seen = {}
        for n in names:
            h = ST.elf(n)
            if h in seen:
                raise ValueError('%s and %s have the same hash (the game could not tell them apart): rename one'
                                 % (n, seen[h]))
            seen[h] = n
        t['names'] = names
        t['blocks'] = [[[int(r['id'][1:], 16) if r['id'].startswith('#') else ST.elf(r['id']), 1, r.get('font') or '',
                         (r.get('text') or ''), _f(r.get('sx')), _f(r.get('sy')), 0]
                        for r in rows] for rows in blocks]
        data = ST.write(t)
        with open(p, 'wb') as fh:
            fh.write(data)
        note = 'Saved %s' % key
        if put_in:
            dest = os.path.join(self.de.folder_path(put_in), os.path.basename(p))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copyfile(p, dest)
            note += ' and put it in %s\'s .dat folder (it goes in at the next build)' % put_in
        return note

    # ------------------------------------------------------------ Flash screens (.pck)

    def pcks(self):
        """The game's Flash screens: every .pck the archives hold, and whether its .dat folder has one of its own."""
        index, _d, _h = self.de.index()
        out = []
        for a, info in sorted(index['archives'].items()):
            for n in info.get('names', []):
                if n.lower().endswith('.pck'):
                    mine = os.path.join(self.de.folder_path(a), *n.split('/'))
                    out.append({'archive': a, 'name': n, 'edited': os.path.isfile(mine)})
        return out

    def pck_read(self, where, key):
        from . import pck as PK
        k = PK.Pck(open(self.path(where, key), 'rb').read())
        return {'where': where, 'key': key, 'width': k.width, 'height': k.height, 'counts': k.counts(),
                'fonts': k.font_info(), 'texts': k.texts()}

    def _scene(self, where, key):
        from . import pckscene as PS
        p = self.path(where, key)
        st = os.stat(p)
        ck = (p, st.st_mtime_ns, st.st_size)
        hit = _SCENES.get(p)
        if not hit or hit[0] != ck:
            hit = (ck, PS.Scene(open(p, 'rb').read()))
            _SCENES[p] = hit
        return hit[1]

    def _disc_archive(self, game, name):
        """{lower file name: reader} of one archive of the working disc, read entry by entry (PB.read_entry
        re-parses the whole name table for every file: 256 of them in SWAT.DAT took 30 s; this takes 0.1 s)."""
        try:
            a = game.archive(name.upper())
        except Exception:                                       # noqa: BLE001
            return {}
        return {e.name.lower(): (lambda n=e.name: a.read(n)) for e in a}

    # ------------------------------------------------------------ a Showdown map's name in the menu

    _POSTER = 'postermultiview.swui'
    _POSTER_IN = ('swat.dat', 'front.dat')     # the menu's two copies: in-game (swat) and the front end's

    @staticmethod
    def _menu_key(archive):
        """LVLS_M2/HNG2.DAT -> lvl_hng (the Showdown menu names a map once, for both player counts); None for a
        level that is not a Showdown map."""
        a = (archive or '').replace('\\', '/').lower()
        m = re.fullmatch(r'lvls_m[24]/(.+?)[24]?\.dat', a)
        return 'lvl_' + m.group(1) if m else None

    def _poster_text(self, arch, game=None):
        """(text, from) of one copy of the menu: the mods folder's when there is one (your edits, not built yet),
        else the working disc's."""
        mod = os.path.join(self.de.folder_path(arch), self._POSTER)
        if os.path.isfile(mod):
            return open(mod, encoding='latin-1', newline='').read(), 'mods'
        if game is None:
            from .game import Game
            game = Game(self.de.s.modded_path, writable=False)
        files = self._disc_archive(game, arch)
        rd = next((r for n, r in files.items() if n.rsplit('/', 1)[-1] == self._POSTER), None)
        return (rd().decode('latin-1') if rd else None), 'disc'

    @staticmethod
    def _menu_row(text, key):
        return re.search(r'<LOOKUPDATA:' + re.escape(key) + r',([^,>]*),([^,>]*),([^,>]*)>', text or '', re.I)

    def level_name(self, archive):
        """The map's name and sub text in the Showdown menu: {key, title, subtitle, found}."""
        key = self._menu_key(archive)
        if not key:
            return {'key': None, 'found': False}
        text, _src = self._poster_text('swat.dat')
        m = self._menu_row(text, key)
        if not m:
            return {'key': key, 'found': False}
        return {'key': key, 'found': True, 'title': m.group(2).strip(), 'subtitle': m.group(3).strip()}

    def level_name_set(self, archive, title, subtitle):
        """The map's name and sub text changed in both copies of the Showdown menu (into the mods folder: the next
        Build puts them on the disc)."""
        key = self._menu_key(archive)
        if not key:
            raise ValueError('only Showdown maps (lvls_m2 / lvls_m4) have a name in the menu')

        def clean(s, what):
            s = re.sub(r'\s+', ' ', re.sub(r'[,<>]+', ' ', str(s or ''))).strip()
            if not s:
                raise ValueError('the %s cannot be empty' % what)
            if len(s) > 40:
                raise ValueError('the %s is %d letters: 40 at most' % (what, len(s)))
            return s
        title, subtitle = clean(title, 'name'), clean(subtitle, 'sub text')
        self._busy_check('mods')
        from .game import Game
        game = Game(self.de.s.modded_path, writable=False)
        done = []
        for arch in self._POSTER_IN:
            text, _src = self._poster_text(arch, game)
            m = self._menu_row(text, key)
            if not m:
                continue
            # keep the row's own spacing (a tab after each comma)
            lead2 = re.match(r'\s*', m.group(2)).group(0)
            lead3 = re.match(r'\s*', m.group(3)).group(0)
            row = '<LOOKUPDATA:%s,%s,%s%s,%s%s>' % (key, m.group(1), lead2, title, lead3, subtitle)
            text = text[:m.start()] + row + text[m.end():]
            dest = self.de.folder_path(arch)
            os.makedirs(dest, exist_ok=True)
            with open(os.path.join(dest, self._POSTER), 'w', encoding='latin-1', newline='') as fh:
                fh.write(text)
            done.append(arch)
        if not done:
            raise ValueError('%s is not in the Showdown menu (New .dat > Showdown lists a map there)' % key)
        return {'key': key, 'title': title, 'subtitle': subtitle, 'in': done}

    def _sound_index(self):
        """Every sound the game's bank definitions name (snd_<n>.csv in SWAT.DAT, PS2 rows), with the wave it plays in
        its bank (snd_<n>.bd in BANKS.DAT; a row's fifth column is a sample set of the bank's .hd, resolved set ->
        sample -> wave by vag.set_waves). -> {'by_name': {lower name: [row]}, 'banks': {bd: [row]}}; a row is
        {bd, index (the wave: the number the Audio tab, sound_wav and sound_import use), name, note, set}."""
        from . import vag
        path = self.de.s.modded_path
        ck = (path, os.stat(path).st_mtime_ns)
        hit = _FE_BANK.get('index')
        if not hit or hit[0] != ck:
            import csv
            from .game import Game
            game = Game(path, writable=False)
            swat = self._disc_archive(game, 'swat.dat')
            banks = self._disc_archive(game, 'banks.dat')
            bank_names = set(banks)
            by_name, per_bank, titles = {}, {}, {}
            for n in sorted(swat):
                m = re.match(r'(snd_\d+)\.csv$', n, re.I)
                if not m or (m.group(1).lower() + '.bd') not in bank_names:
                    continue
                bd = m.group(1).lower() + '.bd'
                raw = swat[n]()
                first = (raw or b'').decode('latin-1').splitlines()[:3]
                titles[bd] = next((l.strip(',"# \t') for l in first if l.lstrip('"').startswith('#')), '')
                hd = banks[bd[:-3] + '.hd']() if bd[:-3] + '.hd' in banks else b''
                waves_of = vag.set_waves(hd or b'')
                keys = vag.key_sets(hd or b'')
                rows = []
                for f in csv.reader(io.StringIO((raw or b'').decode('latin-1'))):
                    if len(f) > 16 and f[16] == 'PS2':
                        # the row plays program f[3] at key f[4]: the program's split for that key names the set
                        try:
                            prog, key = int(f[3] or 0), int(f[4])
                        except ValueError:
                            continue
                        sset = keys.get((prog, key))
                        waves = waves_of[sset] if sset is not None and 0 <= sset < len(waves_of) else []
                        if not waves:
                            continue
                        row = {'bd': bd, 'index': waves[0], 'name': f[0], 'note': ','.join(f[17:]).strip(), 'set': sset,
                               'key': key}
                        rows.append(row)
                        by_name.setdefault(f[0].lower(), []).append(row)
                per_bank[bd] = rows
            all_bds = sorted(n for n in bank_names if n.endswith('.bd'))
            hit = (ck, {'by_name': by_name, 'banks': per_bank, 'titles': titles, 'all': all_bds})
            _FE_BANK['index'] = hit
        return hit[1]

    # ---- MEDIA > Audio, organised: the banks by what they are, their sounds by name

    @staticmethod
    def _bank_label(bd, title):
        """(group, short title) of a bank from its definition's title."""
        num = int(re.sub(r'\D', '', bd) or 0)
        t = (title or '').strip().rstrip('.')
        m = re.match(r'voice and unique sounds for (.+)', t, re.I)
        if m:
            return 'Characters', m.group(1).strip()
        m = re.match(r'specific objects(?: sounds)?, for (.+)', t, re.I)
        if m:
            what = m.group(1).strip()
            return ('Menus', 'Menus (' + what.lower() + ')') if 'mode select' in what.lower() else ('Levels', what)
        m = re.match(r'level (.+?) specific sound effects', t, re.I)
        if m:
            return 'Levels', m.group(1).strip() + ' (level)'
        if num < 100 or num in (9998, 9999):
            return 'Game and system', t or bd
        return 'Other', t or bd

    def sound_catalog(self):
        """Every sound bank grouped (Game and system, Menus, Characters, Levels, Other), with its title and how many
        named sounds its definition gives."""
        idx = self._sound_index()
        order = ['Game and system', 'Menus', 'Characters', 'Levels', 'Other']
        groups = {g: [] for g in order}
        for bd in idx['all']:
            g, short = self._bank_label(bd, idx['titles'].get(bd, ''))
            named = len({r['index'] for r in idx['banks'].get(bd, [])})
            groups[g].append({'bd': bd, 'title': short, 'full': idx['titles'].get(bd, ''), 'named': named})
        for g in groups.values():
            g.sort(key=lambda b: (b['title'].lower(), b['bd']))
        return {'groups': [{'title': g, 'banks': groups[g]} for g in order if groups[g]]}

    def sound_names(self, bd):
        """{wave index: [{name, note}]} of one bank (the names its definition gives each sound)."""
        idx = self._sound_index()
        out = {}
        for r in idx['banks'].get(bd.lower(), []):
            lst = out.setdefault(str(r['index']), [])
            if all(x['name'] != r['name'] for x in lst):
                lst.append({'name': r['name'], 'note': r['note']})
        g, short = self._bank_label(bd.lower(), idx['titles'].get(bd.lower(), ''))
        return {'bd': bd.lower(), 'title': short, 'group': g, 'full': idx['titles'].get(bd.lower(), ''), 'names': out}

    def sound_search(self, q):
        """The named sounds of every bank whose name or description has `q`: [{bd, bank, index, name, note}]."""
        q = (q or '').strip().lower()
        if len(q) < 2:
            return []
        idx = self._sound_index()
        out, seen = [], set()
        for bd, rows in sorted(idx['banks'].items()):
            for r in rows:
                if q in r['name'].lower() or q in r['note'].lower():
                    k = (bd, r['index'], r['name'])
                    if k in seen:
                        continue
                    seen.add(k)
                    out.append({'bd': bd, 'bank': self._bank_label(bd, idx['titles'].get(bd, ''))[1], 'index': r['index'],
                                'name': r['name'], 'note': r['note']})
                    if len(out) >= 400:
                        return out
        return out

    def stream_add(self, files):
        """New streams for STREAMS.DAT: each .stm (its header checked) or .wav (16-bit PCM, mono or stereo, encoded to
        .stm by stm.py) put in streams.dat's .dat folder, so the next Build adds it (a name the archive has already
        replaces that stream). -> [{name, seconds, rate, channels, replaces}]."""
        from . import stm
        from .game import Game
        self._busy_check('mods')
        have = set(self._disc_archive(Game(self.de.s.modded_path, writable=False), 'streams.dat'))
        dest = self.de.folder_path('streams.dat')
        os.makedirs(dest, exist_ok=True)
        out = []
        for f in files:
            name = _clean_name(f.get('name')).lower()
            data = base64.b64decode(f.get('data') or '')
            stem, ext = os.path.splitext(name)
            if ext == '.wav':
                rate, chans = stm.from_wav(data)
                data = stm.encode(chans, rate)
                name = stem + '.stm'
            elif ext != '.stm':
                raise ValueError('%s: a .stm or a .wav' % name)
            if not re.match(r'^[a-z0-9_]+\.stm$', name):
                raise ValueError('%s: a stream name is letters, digits and _ (the game asks for streams by name)' % name)
            h = stm.parse_header(data)           # refuses what is not a stream
            with open(os.path.join(dest, name), 'wb') as fh:
                fh.write(data)
            out.append({'name': name, 'seconds': round(stm.duration(data), 2), 'rate': h['rate'],
                        'channels': h['channels'], 'replaces': name in have})
        return out

    @staticmethod
    def stm_unit(rate):
        """Samples per channel of the driver's read unit (+8: 64 bytes * channels * rate/24000): 128 at 12/24 kHz,
        256 at 48 kHz. All 179 looping streams the game ships have their restart point AND their length on this grid
        -- a loop off it (64-byte blocks alone are not enough at 48 kHz) restarts wrong and plays as noise."""
        return 128 * max(1, rate // 24000)

    def stm_make(self, name, rate, chans_b64, loop=None, save=True):
        """A stream from PCM (16-bit, one base64 string per channel, at `rate`): the loop (start, end) in samples put
        on the driver's grid, the audio CUT at the loop end (the format has no loop end: a stream loops when it runs
        out, back to the restart point with the decoder state it had there -- encoded in two passes so that state is
        exact), checked against the shipped files' rules. Saved in streams.dat's .dat folder (save) and returned."""
        import array as _array
        from . import stm
        from .game import Game
        rate = int(rate)
        if rate not in stm.RATES:
            raise ValueError('the game plays streams at 48000, 24000 or 12000 Hz, not %s' % rate)
        chans = []
        for b in chans_b64[:2]:
            a = _array.array('h')
            a.frombytes(base64.b64decode(b or ''))
            chans.append(a)
        if not chans or not len(chans[0]):
            raise ValueError('no audio')
        n = min(len(c) for c in chans)
        chans = [c[:n] for c in chans]
        unit = self.stm_unit(rate)
        snap = lambda v: max(0, int(round(float(v) / unit)) * unit)  # noqa: E731
        start = end = None
        if loop:
            start, end = snap(loop.get('start', 0)), snap(loop.get('end', n))
            end = min(end, (n // unit) * unit if n >= unit else unit)
            if end - start < unit:
                raise ValueError('the loop is shorter than one read unit (%d samples): move its end further from its '
                                 'start' % unit)
            chans = [c[:end] for c in chans]
            total = end
        else:
            total = ((n + unit - 1) // unit) * unit
        chans = [c + _array.array('h', bytes(2 * (total - len(c)))) for c in chans]
        data = stm.encode(chans, rate, loop_at=start)
        h = stm.parse_header(data)
        tick = 64 * len(chans) * max(1, rate // 24000)
        assert h['data_size'] % tick == 0
        if loop:
            restart = struct.unpack_from('<I', data, 36)[0]
            assert restart % tick == 0 and restart == start // 2 * len(chans)
        name = _clean_name((name or 'my_stream').strip()).lower()
        if not name.endswith('.stm'):
            name += '.stm'
        if not re.match(r'^[a-z0-9_]+\.stm$', name):
            raise ValueError('a stream name is letters, digits and _ (the game asks for streams by name)')
        out = {'name': name, 'rate': rate, 'channels': len(chans), 'seconds': round(total / rate, 3),
               'loop': {'start': start, 'end': end, 'start_s': round(start / rate, 3), 'end_s': round(end / rate, 3)}
               if loop else None, 'bytes': len(data)}
        if save:
            self._busy_check('mods')
            have = set(self._disc_archive(Game(self.de.s.modded_path, writable=False), 'streams.dat'))
            dest = self.de.folder_path('streams.dat')
            os.makedirs(dest, exist_ok=True)
            with open(os.path.join(dest, name), 'wb') as fh:
                fh.write(data)
            out['replaces'] = name in have
        else:
            out['stm_b64'] = base64.b64encode(data).decode()
        return out

    def stm_open(self, data_b64=None, stream=None):
        """A .stm (a file, or one of the disc's streams by name) as a WAV to edit, with its loop (start, end) in
        samples per channel when it has one."""
        from . import stm
        from .game import Game
        if stream:
            files = self._disc_archive(Game(self.de.s.modded_path, writable=False), 'streams.dat')
            rd = files.get(stream.lower() if stream.lower().endswith('.stm') else stream.lower() + '.stm')
            if not rd:
                raise ValueError('no stream named %s' % stream)
            data = rd()
        else:
            data = base64.b64decode(data_b64 or '')
        h = stm.parse_header(data)
        rate, nch, chans = stm.decode(data)
        import array as _array
        n = min(len(c) for c in chans)
        if nch == 1:
            pcm = chans[0][:n].tobytes()
        else:
            il = _array.array('h', bytes(2 * n * nch))
            for c, ch in enumerate(chans):
                il[c::nch] = ch[:n]
            pcm = il.tobytes()
        buf = io.BytesIO()
        buf.write(b'RIFF' + struct.pack('<I', 36 + len(pcm)) + b'WAVEfmt ')
        buf.write(struct.pack('<IHHIIHH', 16, 1, nch, rate, rate * nch * 2, nch * 2, 16))
        buf.write(b'data' + struct.pack('<I', len(pcm)) + pcm)
        loop = None
        if h['loop_point']:
            restart = struct.unpack_from('<I', data, 36)[0]
            loop = {'start': restart * 2 // nch, 'end': len(chans[0])}
        return {'wav_b64': base64.b64encode(buf.getvalue()).decode(), 'rate': rate, 'channels': nch, 'loop': loop}

    def stream_added(self):
        """The streams waiting in streams.dat's .dat folder for the next Build."""
        d = self.de.folder_path('streams.dat')
        if not os.path.isdir(d):
            return []
        return sorted(n for n in os.listdir(d) if n.lower().endswith('.stm'))

    def pck_sounds(self, where, key):
        """The sounds a screen plays: each attachSound("<name>") of its scripts (pckavm.py), with the variable it is
        played through and every bank that defines the name (the wave it is there)."""
        from . import pckavm
        sc = self._scene(where, key)
        idx = self._sound_index()
        out = []
        for var, name in pckavm.attached_sounds(bytes(sc.d)):
            where_ = idx['by_name'].get(name.lower(), [])
            # one row per bank (alternative rows of one name in one bank: the first)
            seen, banks = set(), []
            for r in where_:
                if r['bd'] not in seen:
                    seen.add(r['bd'])
                    banks.append(r)
            out.append({'name': var or name, 'sound': name, 'banks': banks})
        stubs = sum(1 for t, _o in sc.p.objects.values() if t == 8)
        involved = sorted({b['bd'] for s in out for b in s['banks']})
        return {'sounds': out, 'bank_rows': {bd: idx['banks'].get(bd, []) for bd in involved}, 'stubs': stubs}

    def pck_scene(self, where, key):
        return self._scene(where, key).to_json()

    def pck_image(self, where, key, kind, oid, k=0):
        from . import pckscene as PS
        sc = self._scene(where, key)
        rgba = sc.bitmap(oid)[0] if kind == 'bitmap' else sc.atlas(oid, k)
        return PS.png_of(rgba)

    def pck_apply(self, where, key, changes, put_in=None, ttf_id=None, autofill=False):
        """Text, layout and picture changes (pckscene.apply); letters a font lacks are given to it first."""
        from . import pckscene as PS
        self._busy_check(where)
        p = self.path(where, key)
        old = open(p, 'rb').read()
        data = PS.apply(old, changes, sources=lambda: self._sources_for(key, old),
                        ttf=self._ttf_path(ttf_id), autofill=autofill)
        with open(p, 'wb') as fh:
            fh.write(data)
        note = 'Saved %s' % key + (' (the file grew %d KB)' % ((len(data) - len(old) + 1023) // 1024)
                                    if len(data) > len(old) else '')
        if put_in:
            dest = os.path.join(self.de.folder_path(put_in), os.path.basename(p))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copyfile(p, dest)
            note += " and put it in %s's .dat folder (it goes in at the next build)" % put_in
        return note

    # ---- filling a font (rdrstudio/pckfont.py)

    def _pck_sources(self, skip=None):
        """Every .pck of the working disc, as scenes: where a font's missing letters are looked for."""
        from . import pckscene as PS
        path = self.de.s.modded_path
        ck = (path, os.stat(path).st_mtime_ns)
        hit = _PCK_SOURCES.get('disc')
        if not hit or hit[0] != ck:
            from .game import Game
            index, _d, _h = self.de.index()
            game = Game(path, writable=False)
            got = []
            for a, info in sorted(index['archives'].items()):
                names = [n for n in info.get('names', []) if n.lower().endswith('.pck')]
                if not names:
                    continue
                files = self._disc_archive(game, a)
                for n in names:
                    rd = files.get(n.lower()) or files.get(n.rsplit('/', 1)[-1].lower())
                    raw = rd() if rd else None
                    if raw:
                        try:
                            got.append(('%s/%s' % (a, n), PS.Scene(raw)))
                        except ValueError:
                            pass
            hit = (ck, got)
            _PCK_SOURCES['disc'] = hit
        return [x for x in hit[1] if not skip or x[0].replace('/', '_').lower() != skip.replace('/', '_').lower()]

    def _sources_for(self, key, data):
        """Where a screen's fonts look for letters: the screen itself as it is now (its other fonts: one typeface
        can be in it twice, at two sizes), then every other .pck of the working disc -- the same-named one in the
        other archive too (only this screen's own stock copy is left out, the open one stands for it)."""
        from . import pckscene as PS
        parts = key.replace(os.sep, '/').split('/')
        own = '%s/%s' % (parts[-2], parts[-1]) if len(parts) >= 2 else parts[-1]
        return [('%s (this screen)' % parts[-1], PS.Scene(data))] + self._pck_sources(skip=own)

    def pck_ttfs(self):
        """The TrueType / OpenType fonts on this computer (and ones added here), for filling a font."""
        if 'list' not in _TTFS:
            from PIL import ImageFont
            out = []
            for tag, folder in self._ttf_dirs():
                if not os.path.isdir(folder):
                    continue
                for n in sorted(os.listdir(folder), key=str.lower):
                    if not n.lower().endswith(('.ttf', '.otf')):
                        continue
                    try:
                        fam, style = ImageFont.truetype(os.path.join(folder, n), 12).getname()
                    except (OSError, ValueError):
                        continue
                    out.append({'id': tag + ':' + n, 'name': '%s %s' % (fam, style) if style and style != 'Regular' else fam})
            out.sort(key=lambda f: f['name'].lower())
            _TTFS['list'] = out
        return _TTFS['list']

    def _ttf_dirs(self):
        win = os.environ.get('WINDIR', r'C:\Windows')
        local = os.environ.get('LOCALAPPDATA', '')
        return [('added', os.path.join(self.de.home, 'Fonts')), ('sys', os.path.join(win, 'Fonts')),
                ('user', os.path.join(local, 'Microsoft', 'Windows', 'Fonts'))]

    def _ttf_path(self, ttf_id):
        if not ttf_id:
            return None
        tag, _c, name = ttf_id.partition(':')
        for t, folder in self._ttf_dirs():
            if t == tag:
                path = os.path.join(folder, os.path.basename(name))
                if os.path.isfile(path):
                    return path
        raise ValueError('no font %s' % ttf_id)

    def pck_ttf_add(self, name, data):
        """A font file of your own, kept in the Data Editor's Fonts folder."""
        name = os.path.basename(name or '')
        if not name.lower().endswith(('.ttf', '.otf')):
            raise ValueError('a .ttf or .otf file')
        folder = os.path.join(self.de.home, 'Fonts')
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, name), 'wb') as fh:
            fh.write(data)
        _TTFS.pop('list', None)
        return 'added:' + name

    def pck_font_plan(self, where, key, fid, ttf_id=None, smallcaps=None):
        """What filling the font would give, and a preview of its first atlas after."""
        from . import pckfont as PF, pckscene as PS
        data = open(self.path(where, key), 'rb').read()
        sources = self._sources_for(key, data)
        ttf = self._ttf_path(ttf_id)
        plan = PF.plan(data, fid, sources, ttf, smallcaps=smallcaps)
        try:
            new = PF.fill(data, fid, sources, ttf, smallcaps=smallcaps)
            sc = PS.Scene(new)
            plan['preview'] = 'data:image/png;base64,' + base64.b64encode(PS.png_of(sc.atlas(fid, 0))).decode()
            plan['grows'] = len(new) - len(data)
            plan['chars_after'] = sc.p.fonts[fid]['chars']
        except ValueError as ex:
            plan['nothing'] = str(ex)
        return plan

    def pck_fill_all(self, where, key, ttf_id=None, put_in=None, in_job=False):
        """Every font of one screen filled with the letters that can be found. -> note."""
        from . import pckfont as PF
        if not in_job:
            self._busy_check(where)
        p = self.path(where, key)
        data = open(p, 'rb').read()
        new, done = PF.fill_all(data, self._sources_for(key, data), self._ttf_path(ttf_id))
        added = sum(len(a) for _f, a in done)
        if not added:
            return '%s: no font could get more letters%s' % (os.path.basename(key), '' if ttf_id else
                                                                ' (pick a TrueType font for letters no screen has)')
        with open(p, 'wb') as fh:
            fh.write(new)
        note = '%s: %d font(s) filled, %d letters added (the file grew %d KB)' % (
            os.path.basename(key), len([1 for _f, a in done if a]), added, (len(new) - len(data) + 1023) // 1024)
        if put_in:
            dest = os.path.join(self.de.folder_path(put_in), os.path.basename(p))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copyfile(p, dest)
            note += " and put in %s's .dat folder" % put_in
        return note

    def pck_fill_every_screen(self, ttf_id=None):
        """Every screen's fonts filled; each changed screen put in its .dat folder (the next Build takes them in).
        A job: it reads every screen several times over."""
        notes = []
        for s in self.pcks():
            o = self.hud_open(s['archive'], s['name'], 'Flash screens')
            try:
                n = self.pck_fill_all(o['where'], o['key'], ttf_id, put_in=s['archive'] if o['where'] != 'mods' else None,
                                      in_job=True)
            except ValueError as ex:
                n = '%s: %s' % (s['name'], ex)
            self.de._log(n)
            notes.append(n)
        filled = [n for n in notes if 'letters added' in n]
        self.de._log('%d of %d screens got letters; they are in their .dat folders: the next Build puts them on the disc'
                     % (len(filled), len(notes)))
        return filled

    def pck_font_fill(self, where, key, fid, ttf_id=None, smallcaps=None, put_in=None):
        from . import pckfont as PF
        self._busy_check(where)
        p = self.path(where, key)
        data = open(p, 'rb').read()
        new = PF.fill(data, fid, self._sources_for(key, data), self._ttf_path(ttf_id),
                      smallcaps=smallcaps)
        with open(p, 'wb') as fh:
            fh.write(new)
        note = 'Font %d filled (%d letters now; the file grew %d KB)' % (fid, len(PF.Scene(new).p.fonts[fid]['chars']),
                                                                         (len(new) - len(data) + 1023) // 1024)
        if put_in:
            dest = os.path.join(self.de.folder_path(put_in), os.path.basename(p))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copyfile(p, dest)
            note += " and put in %s's .dat folder" % put_in
        return note

    def pck_save(self, where, key, changes, put_in=None, ttf_id=None, autofill=False):
        """The Text view's changes [{id, kind: text|edit, lines|text, align}]: as pck_apply (no length limit; letters
        a font lacks taken from the game's other screens, then the TrueType font)."""
        return self.pck_apply(where, key, changes, put_in, ttf_id, autofill)

    # ------------------------------------------------------------ Convert images

    def convert_images(self, files, opts):
        """[.png uploads] -> .tex into the Workbench (Textures) or a .dat folder. Returns the converter's lines."""
        to_folder = opts.get('to') or 'workbench'
        if to_folder == 'workbench':
            out = os.path.join(self.de.workbench, 'Textures')
        else:
            self._busy_check('mods')
            out = self.de.folder_path(to_folder)
        tmp = os.path.join(self.de.cache, 'png_in')
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp)
        srcs = []
        for f in files:
            n = _clean_name(f.get('name'))
            if not n.lower().endswith('.png'):
                continue
            p = os.path.join(tmp, n)
            with open(p, 'wb') as fh:
                fh.write(base64.b64decode(f.get('data') or ''))
            srcs.append(p)
        if not srcs:
            raise ValueError('no .png images given')
        like = None
        if opts.get('like_where') and opts.get('like_key'):
            like = self.path(opts['like_where'], opts['like_key'])
        a = self.de._ns(png2tex=srcs, platform='ps2', like=like, alpha=opts.get('alpha') or 'keep',
                        key=(opts.get('key') or '000000').lstrip('#'), tolerance=int(opts.get('tolerance') or 16),
                        soft=int(opts.get('soft') or 48), format=opts.get('format') or 'auto',
                        no_mips=not opts.get('mips', True), resize=bool(opts.get('resize', True)), to=out,
                        crop=bool(opts.get('crop', True)), place=opts.get('place') or {},
                        names=opts.get('names') or {})
        try:
            import numpy  # noqa: F401  (the converter works on arrays)
        except ImportError:
            raise ValueError('Convert images needs numpy: it is in the desktop app; from source, '
                             'py -3 -m pip install numpy pillow')
        lines = []
        with _printing_to(lines.append):
            PB.png_to_tex(a)
        shutil.rmtree(tmp, ignore_errors=True)
        rel = os.path.relpath(out, self.de.workbench if to_folder == 'workbench' else self.de.mods).replace(os.sep, '/')
        return {'lines': lines, 'folder': rel, 'where': 'workbench' if to_folder == 'workbench' else 'mods'}

    # ------------------------------------------------------------ Export .dat to the Workbench (a job)

    def export_dat(self, archive):
        de = self.de
        index, dats, holders = de.index()
        to = os.path.join(de.workbench, archive.rsplit('/', 1)[-1])
        iso = PB.Iso(de.s.vanilla_path)
        try:
            with _printing_to(de._log):
                PB.export_full(de._ns(), iso, index, dats, holders, archive, de.workbench)  # makes <name>.dat in it
        finally:
            iso.close()
        if os.path.isdir(to):
            write_folder_file(to, archive)
        de._log('%s in full is in the Workbench: %s' % (archive, os.path.relpath(to, de.workbench)))
        return {'folder': os.path.relpath(to, de.workbench).replace(os.sep, '/')}

    # ------------------------------------------------------------ the settings page

    def settings(self):
        s = self.de.s
        return {'vanilla': s.vanilla_path, 'working': s.modded_path, 'pcsx2': getattr(s, 'pcsx2', '') or '',
                'home': self.de.home, 'mods': self.de.mods, 'workbench': self.de.workbench, 'saved': self.saved,
                'cache': self.de.cache, 'bases': self.de.bases,
                'pending': s.pending.summary()}


def more(de):
    m = getattr(de, '_more', None)
    if m is None:
        m = de._more = More(de)
    return m


# ---------------------------------------------------------------- routes

LOCAL_ONLY = ('de_x_open',)


def handle_get(h, route, q):
    """(code, body, ctype) for a de_x_* GET, or None."""
    from .studio import _data_editor
    de = _data_editor(h.studio)
    m = more(de)
    one = lambda k, d=None: (q.get(k) or [d])[0]  # noqa: E731
    if route == 'de_x_tree':
        return 200, m.tree(one('where', 'workbench')), None
    if route == 'de_x_saved':
        return 200, m.saved_mods(), None
    if route == 'de_x_export_mod':
        return 200, m.export_mod(), 'application/zip'
    if route == 'de_x_strtbls':
        return 200, {'tables': m.strtbls(one('archive') or None)}, None
    if route == 'de_x_strtbl':
        return 200, m.strtbl_read(one('where', 'workbench'), one('key', '')), None
    if route == 'de_x_settings':
        return 200, m.settings(), None
    if route == 'de_x_pcks':
        return 200, {'screens': m.pcks()}, None
    if route == 'de_x_pck':
        return 200, m.pck_read(one('where', 'workbench'), one('key', '')), None
    if route == 'de_x_pck_ttfs':
        return 200, {'fonts': m.pck_ttfs()}, None
    if route == 'de_x_stream_added':
        return 200, {'files': m.stream_added()}, None
    if route == 'de_x_sound_catalog':
        return 200, m.sound_catalog(), None
    if route == 'de_x_sound_names':
        return 200, m.sound_names(one('bd', '')), None
    if route == 'de_x_sound_search':
        return 200, {'results': m.sound_search(one('q', ''))}, None
    if route == 'de_x_pck_sounds':
        return 200, m.pck_sounds(one('where', 'workbench'), one('key', '')), None
    if route == 'de_x_pck_scene':
        return 200, m.pck_scene(one('where', 'workbench'), one('key', '')), None
    if route == 'de_x_pck_image':
        return 200, m.pck_image(one('where', 'workbench'), one('key', ''), one('kind', 'bitmap'),
                                int(one('id', '0')), int(one('k', '0'))), 'image/png'
    if route == 'de_x_asset':
        # the book_red template's layers, for the converter's crop preview
        name = os.path.basename(one('name', ''))
        if name not in ('book_red_shadow.png', 'book_red_background.png', 'mp_square.png'):
            return 404, {'error': 'no such asset'}, None
        # beside ps2build, as the converter reads them (in the built app that is the bundle's top level, not
        # dataeditor_lib -- looking there left the preview blank)
        return 200, open(os.path.join(os.path.dirname(os.path.abspath(PB.__file__)), 'assets', name), 'rb').read(), 'image/png'
    if route == 'de_x_download':
        return 200, open(m.path(one('where', 'workbench'), one('key', '')), 'rb').read(), 'application/octet-stream'
    return None


def handle_post(h, route, body):
    from .studio import _data_editor
    de = _data_editor(h.studio)
    m = more(de)
    g = body.get
    if route in LOCAL_ONLY and h.client_address[0] not in ('127.0.0.1', '::1', 'localhost'):
        return dict(ok=False, error_text='Opening folders works only in the Studio on this computer.')
    if route == 'de_x_new_folder':
        return dict(ok=True, path=m.new_folder(g('where', 'workbench'), g('parent', ''), g('name', '')))
    if route == 'de_x_new_file':
        return dict(ok=True, path=m.new_file(g('where', 'mods'), g('folder', ''), g('name', '')))
    if route == 'de_x_rename':
        return dict(ok=True, path=m.rename(g('where', 'workbench'), g('path', ''), g('name', '')))
    if route == 'de_x_remove':
        return dict(ok=True, count=m.remove(g('where', 'workbench'), g('paths') or []))
    if route == 'de_x_upload':
        return dict(ok=True, added=m.upload(g('where', 'workbench'), g('folder', ''), g('files') or []))
    if route == 'de_x_paste_text':
        return dict(ok=True, path=m.paste_text(g('folder', ''), g('text', '')))
    if route == 'de_x_transfer':
        return dict(ok=True, done=m.transfer(g('from', 'workbench'), g('paths') or [], g('to_where', 'workbench'),
                                             g('to', ''), bool(g('move'))))
    if route == 'de_x_delete_new':
        return dict(ok=True, deleted=m.delete_new(g('folder', '')))
    if route == 'de_x_open':
        return dict(ok=True, path=m.open_in_explorer(g('where', 'workbench'), g('path', '')))
    if route == 'de_x_mod_save':
        return dict(ok=True, name=m.save_mod(g('name', '')))
    if route == 'de_x_mod_load':
        return dict(ok=True, name=m.load_mod(g('name', '')))
    if route == 'de_x_mod_delete':
        return dict(ok=m.delete_mod(g('name', '')))
    if route == 'de_x_mod_import':
        return dict(ok=True, note=m.import_mod(g('name', ''), base64.b64decode(g('data') or '')))
    if route == 'de_x_hud_open':
        return dict(ok=True, **m.hud_open(g('archive', ''), g('name', '')))
    if route == 'de_x_pck_open':
        return dict(ok=True, **m.hud_open(g('archive', ''), g('name', ''), 'Flash screens'))
    if route == 'de_x_pck_font_plan':
        sc = g('smallcaps')
        return dict(ok=True, **m.pck_font_plan(g('where', 'workbench'), g('key', ''), int(g('id', 0)), g('ttf') or None,
                                               None if sc is None else bool(sc)))
    if route == 'de_x_pck_font_fill':
        sc = g('smallcaps')
        return dict(ok=True, note=m.pck_font_fill(g('where', 'workbench'), g('key', ''), int(g('id', 0)), g('ttf') or None,
                                                  None if sc is None else bool(sc), g('put_in') or None))
    if route == 'de_x_pck_fill_all':
        return dict(ok=True, note=m.pck_fill_all(g('where', 'workbench'), g('key', ''), g('ttf') or None,
                                                 g('put_in') or None))
    if route == 'de_x_pck_fill_every':
        return dict(started=de.start('Fill the fonts of every Flash screen', m.pck_fill_every_screen, g('ttf') or None))
    if route == 'de_x_tex_to_png':
        # one of the game's .tex textures as a .png (to import into a Flash screen, or to show it)
        from . import pckscene as PS
        out = io.BytesIO()
        PS.load_image(base64.b64decode(g('data') or '')).save(out, 'PNG')
        return dict(ok=True, png=base64.b64encode(out.getvalue()).decode())
    if route == 'de_x_pck_ttf_add':
        return dict(ok=True, id=m.pck_ttf_add(g('name', ''), base64.b64decode(g('data') or '')))
    if route == 'de_x_stm_make':
        return dict(ok=True, **m.stm_make(g('name', ''), g('rate', 24000), g('chans') or [], g('loop') or None,
                                         g('save', True) is not False))
    if route == 'de_x_stm_open':
        return dict(ok=True, **m.stm_open(g('data') or None, g('stream') or None))
    if route == 'de_x_stream_add':
        return dict(ok=True, added=m.stream_add(g('files') or []))
    if route == 'de_x_pck_apply':
        return dict(ok=True, note=m.pck_apply(g('where', 'workbench'), g('key', ''), g('changes') or [],
                                              g('put_in') or None, g('ttf') or None, bool(g('autofill'))))
    if route == 'de_x_pck_save':
        return dict(ok=True, note=m.pck_save(g('where', 'workbench'), g('key', ''), g('changes') or [],
                                             g('put_in') or None, g('ttf') or None, bool(g('autofill'))))
    if route == 'de_x_strtbl_save':
        return dict(ok=True, note=m.strtbl_save(g('where', 'workbench'), g('key', ''), g('blocks') or [],
                                                g('put_in') or None))
    if route == 'de_x_convert_images':
        return dict(ok=True, **m.convert_images(g('files') or [], g('opts') or {}))
    if route == 'de_x_level_name':
        return dict(ok=True, **m.level_name(g('archive', '')))
    if route == 'de_x_level_name_set':
        return dict(ok=True, **m.level_name_set(g('archive', ''), g('title', ''), g('subtitle', '')))
    if route == 'de_x_import_dir':
        return dict(ok=True, **m.import_dir(g('name', ''), g('marker') or None, g('files') or []))
    if route == 'de_x_import_zip':
        return dict(ok=True, **m.import_zip(g('name', ''), base64.b64decode(g('data') or '')))
    if route == 'de_x_export_zip':
        return dict(ok=True, **m.export_zip(g('archive', '')))
    if route == 'de_x_dat_home':
        key, why = m.dat_home(g('name', ''), g('marker') or None)
        return dict(ok=True, folder=key, why=why)
    if route == 'de_x_export_dat':
        return dict(started=de.start('Export %s to the Workbench' % g('archive', ''), m.export_dat, g('archive', '')))
    return None
