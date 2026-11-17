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


def recycle(paths, permanent=False):
    """To the Recycle Bin (as the app does); deleted outright where that is not possible -- or, permanent, deleted
    outright (the user chose Delete permanently)."""
    paths = [os.path.abspath(p) for p in paths if os.path.exists(p)]
    if not paths:
        return
    if permanent:
        for p in paths:
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            elif os.path.exists(p):
                os.remove(p)
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

    def remove(self, where, rels, permanent=False):
        self._busy_check(where)
        root = os.path.abspath(self.base(where))
        ps = [self.path(where, r) for r in rels]
        ps = [p for p in ps if p != root]
        recycle(ps, permanent)
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
        files, notes = self.xbox_payload(files)
        added = self.upload('mods', key, files)
        return {'folder': key, 'why': why, 'added': added, 'notes': notes}

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
        notes = self.xbox_in_place('mods', [key])
        return {'folder': key, 'why': why, 'files': n, 'notes': notes}

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

    # ------------------------------------------------------------ Showdown: a character made playable in versus
    #
    # The game's rule, 48 of 48 shipped entries: a Showdown character is m + its code (mjck), with its own
    # m<code>.cr (its versus tuning) beside the character's files; its name (swat.strtbl) and portraits
    # (mp_<code>.tex 128x128, ui_mphoto_<code>.tex 256x128, in swat.dat and front.dat) go by the code without the m.
    # mpcharbounty.csv lists the roster, unlockables.csv when each one is unlocked. Showdown characters load from
    # SWAT.DAT (a map carries only Red's model). A versus .cr and its .rep / .msd / .sphs follow a skeleton, so the
    # tuning is taken from a Showdown character on the SAME skeleton (bone names) -- Red's own (msrd) carries seven
    # coat-tail bones a 52-bone character does not have.

    def _mod_or_disc(self, arch, name):
        top = self.de.folder_path(arch)
        p = os.path.join(top, name)
        if os.path.isfile(p):
            return open(p, 'rb').read()
        # a character dropped in as a folder (swat.dat/gbw/gbw.cr): the build puts it at the archive's top
        if os.path.isdir(top):
            for r, _ds, fs in os.walk(top):
                hit = next((f for f in fs if f.lower() == name.lower()), None)
                if hit:
                    return open(os.path.join(r, hit), 'rb').read()
        try:
            return self.de.s.disc.archive(arch.upper()).read(name)
        except Exception:                                       # noqa: BLE001
            return None

    def _bones(self, skel_name):
        from . import skeleton as SK
        d = self._mod_or_disc('swat.dat', skel_name + '.skel')
        if not d:
            return None
        try:
            return [b.name for b in SK.parse(d)[2]]
        except Exception:                                       # noqa: BLE001
            return None

    @staticmethod
    def _cr_field(text, key):
        m = re.search(r'^\s*%s\s+(\S+)' % key, text or '', re.M)
        return m.group(1).lower() if m else None

    def _names_table(self):
        d = self._mod_or_disc('swat.dat', 'swat.strtbl')
        return ST.read(d) if d else None

    def _roster(self):
        d = self._mod_or_disc('swat.dat', 'mpcharbounty.csv')
        if not d:
            raise ValueError('swat.dat has no mpcharbounty.csv')
        rows = [l.split(',') for l in d.decode('latin-1').splitlines()[1:] if l.strip() and not l.startswith('#')]
        t = self._names_table()
        names = {}
        if t and t['blocks']:
            names = {e[0]: e[3] for e in t['blocks'][0]}
        out = []
        for r in rows:
            c = r[0].strip()
            out.append({'code': c, 'bounty': (r[1].strip() if len(r) > 1 else ''),
                        'name': names.get(ST.elf(c[1:])) if c.startswith('m') else None})
        return out

    def _find_mod(self, arch, name):
        """The path of `name` in the mods folder's `arch` (at any depth), or None."""
        top = self.de.folder_path(arch)
        for r, _ds, fs in os.walk(top) if os.path.isdir(top) else ():
            hit = next((f for f in fs if f.lower() == name.lower()), None)
            if hit:
                return os.path.join(r, hit)
        return None

    def mp_bundles(self, names=None):
        """Models dropped into swat.dat with no character to go with them: {code, bones, sources} for each PS2
        <code>_lods.rsc (with its .rnt) that has no <code>.cr in the mods folder or on the disc; `sources`: the
        characters on a skeleton of the same bone count (the rest of the character is copied from one, as Add
        Character does with your own .rsc), Showdown ones' story selves first. Plus the Xbox files dropped there
        (.xrsc / .xrnt / .xmod / .xtex), which the PS2 game does not read."""
        from .rsc import Rsc
        top = self.de.folder_path('swat.dat')
        out, xbox = [], []
        if not os.path.isdir(top):
            return {'bundles': out, 'xbox': xbox}
        want = {os.path.basename(str(n).replace('\\', '/').rstrip('/')).lower() for n in (names or [])}
        for r, _ds, fs in os.walk(top):
            rel = os.path.relpath(r, top).replace(os.sep, '/').lower()
            for f in fs:
                fl = f.lower()
                if want and fl not in want and rel.split('/')[0] not in want:
                    continue
                if fl.endswith(('.xrsc', '.xrnt', '.xmod', '.xtex')):
                    xbox.append(f)
                if not fl.endswith('_lods.rsc'):
                    continue
                code = fl[:-len('_lods.rsc')]
                if self._mod_or_disc('swat.dat', code + '.cr') or not os.path.isfile(os.path.join(r, code + '_lods.rnt')):
                    continue
                try:
                    b = Rsc(open(os.path.join(r, f), 'rb').read(), f)
                    bones = max([b.bone_count(c.index) for c in b.chunks if c.tag == 'MOD0'] or [0])
                except Exception:                               # noqa: BLE001
                    bones = 0
                sources = []
                try:
                    skels = self.de.s.skeleton_index('SWAT.DAT')
                    for rr in self._roster():
                        twin = rr['code'][1:]
                        cr = self._mod_or_disc('swat.dat', twin + '.cr')
                        sk = self._cr_field(cr.decode('latin-1'), 'FileNameSkel') if cr else None
                        if sk and skels.get(sk + '.skel') == bones and twin not in sources:
                            sources.append(twin)
                except Exception:                               # noqa: BLE001
                    pass
                out.append({'code': code, 'bones': bones, 'sources': sources})
        return {'bundles': out, 'xbox': sorted(set(xbox))}

    def mp_make_character(self, code, source):
        """A dropped model made a character: its <code>_lods.rsc / .rnt (and <code>.skel when given) from the mods
        folder, the rest of the character copied from `source` (Add Character with your own .rsc). Rebuilds SWAT.DAT,
        as Add Character does. -> the files added."""
        rsc = self._find_mod('swat.dat', code + '_lods.rsc')
        rnt = self._find_mod('swat.dat', code + '_lods.rnt')
        if not rsc or not rnt:
            raise ValueError('%s_lods.rsc and .rnt are not both in swat.dat' % code)
        skel = self._find_mod('swat.dat', code + '.skel')
        return self.de.add_character(source, code, rsc=open(rsc, 'rb').read(), rnt=open(rnt, 'rb').read(),
                                     skel_data=open(skel, 'rb').read() if skel else None)

    def mp_candidates(self, names=None):
        """Characters in the mods folder's swat.dat that are not Showdown characters yet: a <code>.cr (at any depth)
        that is not one of the game's own and is not on the roster (nor m<code>). `names`: only those dropped (file
        or folder names)."""
        top = self.de.folder_path('swat.dat')
        if not os.path.isdir(top):
            return []
        try:
            roster = {r['code'] for r in self._roster()}
        except ValueError:
            return []
        van = getattr(self.de.s, 'vanilla', None)
        want = {os.path.basename(str(n).replace('\\', '/').rstrip('/')).lower() for n in (names or [])}
        out = []
        for r, _ds, fs in os.walk(top):
            rel = os.path.relpath(r, top).replace(os.sep, '/').lower()
            for f in fs:
                if not f.lower().endswith('.cr'):
                    continue
                code = f[:-3].lower()
                if want and f.lower() not in want and rel.split('/')[0] not in want:
                    continue
                if code in roster or 'm' + code in roster or code in out:
                    continue
                try:
                    if van is not None and van.archive('SWAT.DAT').find(code + '.cr') is not None:
                        continue                                # one of the game's own characters
                except Exception:                               # noqa: BLE001
                    pass
                out.append(code)
        return sorted(out)

    def mp_info(self, code):
        """What making `code` a Showdown character would use: the roster (its 48 slots), the Showdown characters on the
        same skeleton (their versus tuning fits), the slot suggested and the name the game has for it."""
        code = (code or '').strip().lower().removesuffix('.cr')
        cr = self._mod_or_disc('swat.dat', code + '.cr')
        if not cr:
            raise ValueError('%s.cr is not in swat.dat (the character has to exist first: Add Character)' % code)
        cr = cr.decode('latin-1')
        skel, anim = self._cr_field(cr, 'FileNameSkel'), self._cr_field(cr, 'FileNameAnim')
        bones = self._bones(skel) if skel else None
        roster = self._roster()
        fits = []
        for r in roster:
            c = r['code']
            if c in ('m' + code, code) or not c.startswith('m'):
                continue                                    # itself, or an entry that breaks the m rule
            rcr = self._mod_or_disc('swat.dat', c + '.cr')
            if not rcr:
                continue
            rcr = rcr.decode('latin-1')
            rs = self._cr_field(rcr, 'FileNameSkel')
            same = bool(bones and rs and self._bones(rs) == bones)
            if not same:                                    # offered too (any Showdown character), noted
                fits.append({'code': c, 'name': r['name'], 'same_anim': False, 'alike': -1, 'fits': False,
                             'skeleton': rs})
                continue
            if same:
                # how alike its story self (mrd for mmrd) and this character are: a character made by copying one
                # (gbw from mrd) matches its source line for line, so that one's versus tuning is the natural fit
                twin = self._mod_or_disc('swat.dat', c[1:] + '.cr')
                mine = {l.strip() for l in cr.splitlines() if l.strip()}
                alike = len(mine & {l.strip() for l in twin.decode('latin-1').splitlines()}) if twin else 0
                fits.append({'code': c, 'name': r['name'], 'same_anim': self._cr_field(rcr, 'FileNameAnim') == anim,
                             'alike': alike, 'fits': True, 'skeleton': rs})
        # the best fit is the default; the list itself is alphabetical (every Showdown character)
        best = sorted([f for f in fits if f['fits']], key=lambda f: (not f['same_anim'], -f['alike'], f['code']))
        fits.sort(key=lambda f: (re.sub(r'[\s"]+', ' ', f['name'] or f['code']).strip().lower(), f['code']))
        t = self._names_table()
        name = None
        if t and t['blocks']:
            name = {e[0]: e[3] for e in t['blocks'][0]}.get(ST.elf(code))
        # already in (m + code), or in under its bare code -- the entry that crashes the menu, repaired in its slot
        here = next((r['code'] for r in roster if r['code'] in ('m' + code, code)), None)
        return {'code': code, 'mcode': 'm' + code, 'skeleton': skel, 'bones': len(bones or []), 'anim': anim,
                'roster': roster, 'templates': fits, 'template': best[0]['code'] if best else None,
                'slot': here or (best[0]['code'] if best else (roster[0]['code'] if roster else None)),
                'in_roster': bool(here), 'name': name or code.upper()}

    # The game's own look for the select screen: every shipped ui_mphoto (or mp_ portrait) is a sepia print -- one
    # colour per brightness, from near black to a warm paper white. Learnt from the unedited disc's 48 (the opaque
    # pixels): the colour each brightness has, and how the brightness is spread, so a picture given that look has
    # the same tones and the same contrast as the others.
    _RAMPS = {}

    def _ramp(self, kind):
        import numpy as np
        from . import texture as TX
        key = (kind, getattr(self.de.s, 'vanilla_path', ''))
        if key in self._RAMPS:
            return self._RAMPS[key]
        prefix = 'mp_' if kind == 'portrait' else 'ui_mphoto_'
        van = getattr(self.de.s, 'vanilla', None) or self.de.s.disc
        roster = (self._mod_or_disc('swat.dat', 'mpcharbounty.csv') or b'').decode('latin-1').splitlines()[1:]
        px = []
        for r in roster:
            c = r.split(',')[0].strip()
            if not (c.startswith('m') and len(c) > 1):
                continue
            try:
                d = van.archive('FRONT.DAT').read(prefix + c[1:] + '.tex')
            except Exception:                                   # noqa: BLE001  (an added one: not on the disc)
                continue
            w, h, rgba = TX.decode_tex_file(d)[:3]
            a = np.frombuffer(bytes(rgba), np.uint8).reshape(-1, 4)
            px.append(a[a[:, 3] > 128, :3])
        if not px:
            raise ValueError('the game\'s own %s pictures could not be read' % prefix)
        rgb = np.concatenate(px).astype(np.float64)
        lum = np.clip(rgb @ np.array([0.299, 0.587, 0.114]), 0, 255)
        bins = np.round(lum).astype(int)
        n = np.bincount(bins, minlength=256).astype(np.float64)
        ramp = np.zeros((256, 3))
        for ch in range(3):
            ramp[:, ch] = np.bincount(bins, rgb[:, ch], minlength=256)
        ok = n >= 8
        idx = np.arange(256)
        for ch in range(3):                                     # brightnesses the game never uses: in between
            ramp[:, ch] = np.interp(idx, idx[ok], ramp[ok, ch] / n[ok])
        cdf = np.cumsum(n) / n.sum()
        self._RAMPS[key] = (ramp, cdf)
        return self._RAMPS[key]

    def _tone(self, img, kind):
        """img (RGBA uint8) given the game's photo look (see _ramp): its brightness spread matched to the game's,
        then each brightness its colour. Transparency kept as it is."""
        import numpy as np
        ramp, cdf_game = self._ramp(kind)
        out = np.array(img, dtype=np.uint8, copy=True)
        rgb = out[..., :3].astype(np.float64)
        lum = np.clip(rgb @ np.array([0.299, 0.587, 0.114]), 0, 255)
        mask = out[..., 3] > 16
        if not mask.any():
            return out
        b = np.round(lum[mask]).astype(int)
        cdf_me = np.cumsum(np.bincount(b, minlength=256)) / b.size
        # each brightness of this picture -> the game's brightness at the same place in its spread
        to = np.interp(cdf_me, cdf_game, np.arange(256))
        new = to[np.round(lum).astype(int)]
        col = ramp[np.clip(np.round(new).astype(int), 0, 255)]
        out[..., :3] = np.where(mask[..., None], np.clip(np.round(col), 0, 255), out[..., :3]).astype(np.uint8)
        return out

    def alpha_png(self, data, opt):
        """A picture (.png / .jpg / .tex / .xtex, base64) with the image converter's transparency applied (and the
        game's photo colours, opt tone: 'photo' / 'portrait') -> PNG (base64), rows top first."""
        import io as _io
        import numpy as np
        from PIL import Image
        from . import pckscene as PS
        img = np.asarray(PS.load_image(base64.b64decode(data or '')).convert('RGBA'), dtype=np.uint8)
        img = self._alpha_apply(img, opt)
        if (opt or {}).get('tone'):
            img = self._tone(img, 'portrait' if opt.get('tone') == 'portrait' else 'photo')
        buf = _io.BytesIO(); Image.fromarray(np.ascontiguousarray(img), 'RGBA').save(buf, 'PNG')
        return base64.b64encode(buf.getvalue()).decode()

    def _picture(self, f, kind):
        """An uploaded portrait ({name, data}) as the game's texture of `kind` (mp_portrait / mp_photo): a .png goes
        through the converter's preset; a .tex of the right size is used as it is, else fitted."""
        import io as _io
        import numpy as np
        import rdrtex
        from . import pckscene as PS
        w, h, flags = (128, 128, '010001000100') if kind == 'mp_portrait' else (256, 128, '010001000100')
        raw = base64.b64decode(f.get('data') or '')
        if f.get('name', '').lower().endswith('.tex') and len(raw) >= 8 and struct.unpack_from('<HH', raw) == (w, h):
            return raw
        img = np.asarray(PS.load_image(raw).convert('RGBA'), dtype=np.uint8)
        opt = f.get('alpha') or {}
        tone = (lambda im: self._tone(im, 'portrait' if kind == 'mp_portrait' else 'photo')) if opt.get('tone') \
            else (lambda im: im)
        if kind == 'mp_portrait' and opt.get('alpha') not in (None, '', 'recipe'):
            # a transparency of the converter's instead of the portrait recipe: applied, then made 128 x 128
            img = tone(PB.resize_cover(self._alpha_apply(img, opt), w, h))
            return rdrtex.encode(np.ascontiguousarray(img[::-1]), rdrtex.PA8, mips=False, flags=bytes.fromhex(flags),
                                 platform='ps2')
        if f.get('name', '').lower().endswith(('.tex', '.xtex')):
            # an already-made picture at another size: fitted, its colours kept (the preset would ink it again)
            if kind == 'mp_photo' and img.shape[0] > img.shape[1]:
                # upright: turned on its side, as the game stores it (one already on its side is used as it is)
                img = np.ascontiguousarray(np.rot90(PB.resize_cover(img, 128, 256), 1))
            img = PB.resize_cover(img, w, h)
        else:
            img = PB.mp_portrait(img) if kind == 'mp_portrait' else PB.mp_photo(img)
        img = tone(img)
        return rdrtex.encode(np.ascontiguousarray(img[::-1]), rdrtex.PA8, mips=False, flags=bytes.fromhex(flags),
                             platform='ps2')

    def mp_picture_set(self, code, kind, f):
        """A Showdown character's picture replaced (the Showdown editor): kind 'portrait' -- mp_<code>.tex, 128 x 128,
        which the end-of-match screen's mugshot is made from at the next Build -- or 'photo' -- ui_mphoto_<code>.tex,
        the full-body photo on the select screen. Into swat.dat's and front.dat's folders. -> what was written."""
        self._busy_check('mods')
        code = (code or '').strip().lower()                # the character's code (gbw), not its m entry
        if kind not in ('portrait', 'photo'):
            raise ValueError('which picture: portrait or photo')
        if not f or not f.get('data'):
            raise ValueError('pick a picture first')
        if kind == 'photo' and f.get('upright'):
            data = self._photo_upright(f['data'])               # placed in its frame in the editor
        else:
            data = self._picture(f, 'mp_portrait' if kind == 'portrait' else 'mp_photo')
        prefix = 'mp_' if kind == 'portrait' else 'ui_mphoto_'
        wrote = []
        for arch in ('swat.dat', 'front.dat'):
            folder = self.de.folder_path(arch)
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, prefix + code + '.tex'), 'wb') as fh:
                fh.write(data)
            wrote.append('%s/%s%s.tex' % (arch, prefix, code))
        return wrote

    def mp_add(self, code, name, slot, template=None, unlocked=True, portrait=None, photo=None, mode='replace',
               bounty='500', lineup=None, photo_upright=None, order='front', layer=None, voice=None, on_lineup=None):
        """`code` made a Showdown character in roster slot `slot` (replacing that entry): m<code>.cr and the rest of
        the template's versus files, the roster and unlock rows, its name, and its two portraits (yours, or the
        slot's as placeholders) -- into the mods folder; Build puts them on the disc. -> what was written.
        `lineup`: its place on the select screen ({x, sx, sy}); `on_lineup`: which lineup (0 / 1; None: as now)."""
        spot, lineup = lineup, on_lineup
        self._busy_check('mods')
        info = self.mp_info(code)
        code, mcode = info['code'], info['mcode']
        roster = [r['code'] for r in info['roster']]
        fresh = mode == 'new'
        if fresh:
            slot = mcode                                        # its own new entry: every one of the game's stays
        elif slot not in roster:
            raise ValueError('%s is not in the Showdown roster' % slot)
        tpl = template or info['template'] or (roster[0] if fresh else slot)
        if tpl == mcode:
            tpl = info['template'] or slot
        notes, wrote = [], []
        if not info['template']:
            notes.append('No Showdown character has %s\'s skeleton: the versus tuning is %s\'s, and its .rep / .msd may '
                         'not fit (test it)' % (code, tpl))
        swat, front = self.de.folder_path('swat.dat'), self.de.folder_path('front.dat')
        os.makedirs(swat, exist_ok=True)
        os.makedirs(front, exist_ok=True)

        def put(folder, n, data):
            with open(os.path.join(folder, n), 'wb') as fh:
                fh.write(data)
            wrote.append(os.path.basename(folder) + '/' + n)

        # the versus .cr: the template's tuning, the character's own model, skeleton and animation set
        own = self._mod_or_disc('swat.dat', code + '.cr').decode('latin-1')
        tcr = self._mod_or_disc('swat.dat', tpl + '.cr')
        if not tcr:
            raise ValueError('%s.cr is not in swat.dat' % tpl)
        tcr = tcr.decode('latin-1')
        for key in ('FileNameRender', 'FileNameSkel', 'FileNameAnim'):
            v = self._cr_field(own, key)
            if v:
                tcr = re.sub(r'(^\s*%s\s+)\S+' % key, lambda m, v=v: m.group(1) + v, tcr, count=1, flags=re.M)
        if not re.search(r'^\s*Face\s+1\b', own, re.M):
            tcr = re.sub(r'(^\s*Face\s+)\S+', lambda m: m.group(1) + '0', tcr, count=1, flags=re.M)
        put(swat, mcode + '.cr', tcr.encode('latin-1'))
        try:                                    # its hit zones: a head to hit
            n = self.ensure_head_zone(self._cr_field(tcr, 'FileNameRender') or code, self._cr_field(tcr, 'FileNameSkel') or code)
            if n:
                notes.append(n)
                wrote.append('swat.dat/%s.hot' % (self._cr_field(tcr, 'FileNameRender') or code))
        except Exception as ex:                                 # noqa: BLE001
            notes.append('Its hit zones could not be checked: %s' % ex)
        for ext in ('acc', 'ars', 'dam', 'hot', 'msd', 'rep', 'sphs', 'swt', 'tail'):
            d = self._mod_or_disc('swat.dat', tpl + '.' + ext)
            if d is not None:
                put(swat, mcode + '.' + ext, d)

        # the roster and unlock rows: the slot's entry becomes this character
        def table(n, fix):
            d = self._mod_or_disc('swat.dat', n)
            if d is None:
                raise ValueError('swat.dat has no %s' % n)
            t = d.decode('latin-1')
            nl = '\r\n' if '\r\n' in t else '\n'
            lines = t.split(nl)
            lines = [fix(l) for l in lines]
            put(swat, n, nl.join(lines).encode('latin-1'))
        if fresh:
            def add_row(n, row):
                d = self._mod_or_disc('swat.dat', n).decode('latin-1')
                nl = '\r\n' if '\r\n' in d else '\n'
                keep = [l for l in d.rstrip('\r\n').split(nl) if l.split(',')[0].strip() != mcode]
                put(swat, n, (nl.join(keep) + nl + row + nl).encode('latin-1'))
            add_row('mpcharbounty.csv', '%s,%s' % (mcode, re.sub(r'[^0-9]', '', str(bounty)) or '500'))
        else:
            table('mpcharbounty.csv', lambda l: (mcode + l[len(slot):]) if l.split(',')[0].strip() == slot else l)

        # The unlock. SLUS_205.00 builds its unlock list skipping rows marked "xbox" (lvl_colseum, row 172 of the
        # game's table), but sets a row's Default bit by its ROW number (0x00182F38 -> 0x00183118) and reads a
        # character's bit by its place in the LIST (0x0013F378: 0x00183090 then 0x00183028). After row 172 the two are
        # one apart, so a character starts unlocked only if the row BEFORE it has Default 1 (the game's own msrd reads
        # mshp's 0) -- and a locked one is neither drawn on the select screen nor selectable. So the entry gets a filler
        # row with Default 1 right before it (nothing on the roster reads it), and its own Default 1, which covers
        # both readings. Rows are added at the end (or the slot's row replaced in place), so no other row moves.
        d = self._mod_or_disc('swat.dat', 'unlockables.csv')
        if d is None:
            raise ValueError('swat.dat has no unlockables.csv')
        t0 = d.decode('latin-1')
        unl_nl = '\r\n' if '\r\n' in t0 else '\n'
        rows = t0.rstrip('\r\n').split(unl_nl)
        pad = '_' + mcode
        name0 = lambda r: r.split(',')[0].strip()
        rows = [r for r in rows if name0(r) != pad]
        at = next((i for i, r in enumerate(rows) if name0(r) == (mcode if fresh else slot)
                   and len(r.split(',')) >= 6 and r.split(',')[1].strip() == 'character'), None)
        if at is not None:
            c = rows[at].split(',')
            c[0] = mcode
            if unlocked:
                c[3], c[4], c[5] = '1', 'NONE', '0'
            mine = ','.join(c)
        else:
            mine = '%s,character,carlos,%s' % (mcode, '1,NONE,0' if unlocked else '0,ENEMIES_KILLED,250')
        rows = [r for i, r in enumerate(rows) if name0(r) != mcode or i == at]   # no duplicates of it
        if at is not None:
            at = next(i for i, r in enumerate(rows) if name0(r) == name0(mine) or name0(r) == (slot if not fresh else mcode))
            rows[at] = mine
        else:
            rows.append(mine)
            at = len(rows) - 1
        if unlocked:
            rows.insert(at, '%s,character,carlos,1,NONE,0' % pad)
        put(swat, 'unlockables.csv', (unl_nl.join(rows) + unl_nl).encode('latin-1'))

        # its name, by its code without the m, in every language; the slot's name entry gives the font and size
        t = self._names_table()
        if t is None:
            raise ValueError('swat.dat has no swat.strtbl')
        name = (name or '').strip() or info['name']
        hs, hc = ST.elf((tpl if fresh else slot)[1:]), ST.elf(code)
        if code not in t['names']:
            if any(ST.elf(n) == hc for n in t['names']):
                raise ValueError('the name ID %s clashes with another one in swat.strtbl' % code)
            at = next((i for i, x in enumerate(t['names']) if x.lower() > code), len(t['names']))
            t['names'].insert(at, code)
        for ents in t['blocks']:
            ref = next((e for e in ents if e[0] == hs), None) or (ents[0] if ents else None)
            mine = next((e for e in ents if e[0] == hc), None)
            if mine:
                mine[3] = name
            elif ref is not None:
                ents.append([hc, ref[1], ref[2], name, ref[4], ref[5], ref[6]])
        put(swat, 'swat.strtbl', ST.write(t))

        # portraits: yours, else the slot's (placeholders, so the menu has something to draw)
        for kind, f, prefix in (('mp_portrait', portrait, 'mp_'), ('mp_photo', photo, 'ui_mphoto_')):
            if kind == 'mp_photo' and photo_upright:
                data = self._photo_upright(photo_upright)       # the photo as placed on the lineup
                for folder in (swat, front):
                    put(folder, prefix + code + '.tex', data)
                continue
            if f and f.get('data'):
                data = self._picture(f, kind)
            else:
                data = None
                for who in (slot, tpl, 'msrd'):                 # the slot's, else the tuning's, else Red's
                    data = self._mod_or_disc('front.dat', prefix + who[1:] + '.tex') if who.startswith('m') else None
                    if data:
                        break
                if not data:
                    raise ValueError('no %s picture to use as a placeholder: give it one' % prefix)
                if slot == mcode:
                    for folder in (swat, front):                # its own pictures, kept as they are
                        put(folder, prefix + code + '.tex', data)
                    continue
                notes.append('%s%s.tex: %s\'s for now (give it a picture to replace it)' % (prefix, code, slot))
            for folder in (swat, front):
                put(folder, prefix + code + '.tex', data)
        # its place on the select screen: without a hotspot it is neither drawn nor selectable
        # its lineup: as asked, else where it is (or the slot is) now, else the first
        if lineup is None:
            g = self.grid_lineups()
            lineup = g.get(code, g.get(slot[1:], 0) if not fresh else 0)
        lineup = 1 if lineup else 0
        if layer is None and not fresh:
            # in the slot's place: as many of the others behind it as were behind the slot
            drawn = self._draw_order(lineup)
            if slot[1:] in drawn:
                layer = len([c for c in drawn[:drawn.index(slot[1:])] if c != code])
        # and on the select screen's own list (postermultiview.swui): the screen offers only the characters there,
        # and draws them in its order -- the layer, the same place in both files (the list first: the hotspots'
        # change would not move the others' order, but it is read from the files as they are)
        wrote += self._poster_roster_put(code, replace=None if fresh else slot[1:], tpl=tpl[1:], layer=layer, order=order,
                                         lineup=lineup)
        wrote += self._lineup_put(code, spot, replace=None if fresh else slot[1:], order=order, layer=layer, lineup=lineup)
        if lineup:
            notes.append('On lineup 2: in the game, L1 / R1 pan between the two lineups on the Showdown select. The next '
                         'Build adds that to the working disc (a code mod) by itself.')
        # its voice line on the select screen, and everyone's numbered by the list again (snd_98.csv: by place)
        stream = self._voice_stream(voice, code, tpl[1:])
        if stream is None and not fresh:
            # in a slot: the slot's line, unless one was chosen
            had = next((v for v in self.voice_lines() if v['code'] == slot[1:]), None)
            stream = had['stream'] if had else None
        wrote += self._voice_sync(mcode, stream, name or info['name'])
        if not fresh and stream is not None:
            self._voice_sync(slot, '', None)                   # the slot's own row: its character is gone
        if fresh:
            # SLUS_205.00 0x0013F330: the select screen looks each entry up ("m%s") in the unlock table and skips it
            # -- not drawn, not selectable -- unless its unlock bit (by row, in the save's unlock bits or the table's
            # own) is set. A save made before this entry existed has no bit for it.
            notes.append('A new roster entry (the game ships 48; the code reads any number). It starts unlocked through a filler row (_%s) before its own in unlockables.csv, which works around the game reading a character\'s Default from the row before it' % mcode)
        self.de._log('Showdown: %s (%s) %s, versus tuning from %s: %d files into the mods folder'
                     % (mcode, name, 'as a new entry' if fresh else 'in the slot of ' + slot, tpl, len(wrote)))
        return {'mcode': mcode, 'slot': slot, 'template': tpl, 'name': name, 'wrote': wrote, 'notes': notes}

    # A character's hit zones are <FileNameRender>.hot (the Showdown m<code>.cr has none of its own: mmrk.cr reads
    # mrk.hot): HotSpot hot01..hot19 on the skeleton's bones, the zone by its name -- hot11 on "head" in 186 of the
    # game's 190 .hot files. Without hot11 there is no head to hit: a head shot lands in the neck / chest zone and pays
    # as a body shot (mrk.hot, Mr Kelly's, has none -- a character made from his files inherits that).
    def ensure_head_zone(self, render, skel):
        """<render>.hot given the game's head zone (Red's hot11) when it has none and the skeleton has a head bone.
        -> a note, or None."""
        hot = self._mod_or_disc('swat.dat', render + '.hot')
        sk = self._mod_or_disc('swat.dat', skel + '.skel')
        if not hot or b'hot11' in hot or not sk or not re.search(rb'(?m)^\s*bone\s+head\b', sk):
            return None
        red = self._game_file('srd.hot') or b''
        m = re.search(rb'HotSpot\s+hot11\s*\{.*?\n\}', red, re.S)
        if not m:
            return None
        nl = b'\r\n' if b'\r\n' in hot else b'\n'
        block = m.group(0).replace(b'\r\n', b'\n').replace(b'\n', nl)
        t = hot.rstrip(b'\r\n') + nl + nl + block + nl
        t = re.sub(rb'(NumHotSpots\s+)(\d+)', lambda x: x.group(1) + str(int(x.group(2)) + 1).encode(), t, count=1)
        folder = self.de.folder_path('swat.dat')
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, render + '.hot'), 'wb') as fh:
            fh.write(t)
        return ('%s.hot had no head zone (hot11), so a head shot paid as a body shot: the game\'s head zone (Red\'s) '
                'added' % render)

    # ------------------------------------------------------------ a .tex fitted to the one it replaces

    def tex_target(self, folder, name):
        """The game's texture a file named `name` dropped into `folder` replaces: {w, h, format, levels, flags,
        archive}, or None (not one of the game's)."""
        name = os.path.basename(name or '').lower()
        if not name.endswith('.tex'):
            return None
        folder = (folder or '').replace('\\', '/').lower()
        index, _dats, holders = self.de.index()
        arcs = [a for a in index['archives'] if a.lower() == folder] or sorted(holders.get(name, ()))
        van = getattr(self.de.s, 'vanilla', None) or self.de.s.disc
        for a in arcs:
            try:
                d = van.archive(a.upper()).read(name)
            except Exception:                                   # noqa: BLE001
                continue
            if len(d) >= 14:
                w, h, f, lv = struct.unpack_from('<4H', d)
                return {'w': w, 'h': h, 'format': f, 'levels': lv, 'flags': d[8:14].hex(), 'archive': a}
        return None

    def tex_fit(self, data, crop, target):
        """A picture (.tex / .png, base64) cropped to `crop` ({x, y, w, h} in its pixels, rows top first) and made
        the target's size, format, mip count and flags. -> {data (base64 .tex), note}."""
        import numpy as np
        import rdrtex
        from PIL import Image
        from . import pckscene as PS
        im = PS.load_image(base64.b64decode(data or '')).convert('RGBA')
        if crop:
            x, y = max(0, int(crop.get('x', 0))), max(0, int(crop.get('y', 0)))
            w, h = max(1, int(crop.get('w', im.width))), max(1, int(crop.get('h', im.height)))
            im = im.crop((x, y, min(im.width, x + w), min(im.height, y + h)))
        tw, th = int(target['w']), int(target['h'])
        im = im.resize((tw, th), Image.LANCZOS)
        fmt = int(target.get('format') or 14)
        writable = {rdrtex.P8, rdrtex.PA8, rdrtex.P4, rdrtex.PA4, rdrtex.RGBA8888}
        note = ''
        if fmt not in writable:
            note = 'its format (%d) is not one the converter writes: 256 colours + alpha instead' % fmt
            fmt = rdrtex.PA8
        flags = bytes.fromhex(target.get('flags') or '010000000000')
        out = rdrtex.encode(np.ascontiguousarray(np.asarray(im)[::-1]), fmt, mips=int(target.get('levels') or 1) > 1,
                            flags=flags, platform='ps2')
        got = struct.unpack_from('<4H', out)
        return {'data': base64.b64encode(out).decode(), 'w': got[0], 'h': got[1], 'format': got[2],
                'levels': got[3], 'note': note}

    # ------------------------------------------------------------ the Showdown character select: the lineup
    #
    # characterlineup.swui (front.dat and swat.dat, identical) draws the select screen as a group photo: the
    # background is ui_characterlineupl24b.tex + ui_characterlineupr24b.tex (two 512x256 halves), and each roster
    # character's own photo (ui_mphoto_<code>.tex, stored on its side, 128x256 upright) is laid on it at
    # <POSMARKER:x,0,0:sx,sy:hotspot_<code>>, in the file's order (back row first). (x + sx, sy) is the character's
    # face: the point the selection pointer goes to. Checked by drawing all 48: every point lands on a face. A roster
    # character with no hotspot is neither drawn nor selectable.

    _HOTSPOT = re.compile(r'<POSMARKER:(-?\d+),(-?\d+),(-?\d+):(-?\d+),(-?\d+):hotspot_(\w+)>')

    def _lineup_text(self):
        d = self._mod_or_disc('front.dat', 'characterlineup.swui')
        if not d:
            raise ValueError('front.dat has no characterlineup.swui')
        return d.decode('latin-1')

    def _upright(self, data):
        """A ui_mphoto .tex as its upright 128x256 picture (numpy RGBA, rows top first)."""
        import numpy as np
        from . import texture as TX
        w, h, rgba = TX.decode_tex_file(data)[:3]
        a = np.frombuffer(bytes(rgba), np.uint8).reshape(h, w, 4)[::-1]        # file rows are bottom first
        return np.ascontiguousarray(np.rot90(a, -1))

    def _draw_order(self, lineup=None):
        """The codes the select screen draws, in the order it draws them (later ones over earlier ones): the order of
        postermultiview.swui's character list (the select loop walks that list), keeping those with a hotspot.
        The shipped files keep the list and the hotspots in the same order, back row first. `lineup`: only the
        characters on that lineup (0 the first, 1 the second: the number on their list line)."""
        hot = [m.group(6).lower() for m in self._HOTSPOT.finditer(self._lineup_text())]
        try:
            text, _from = self._poster_text('front.dat')
            grid = [(m.group(2).lower(), int(m.group(3))) for m in self._GRID.finditer(text or '')]
        except Exception:                                       # noqa: BLE001
            grid = []
        if not grid:
            return hot if not lineup else []
        hs = set(hot)
        return [c for c, n in grid if c in hs and (lineup is None or (1 if n else 0) == lineup)]

    # ------------------------------------------------------------ the second lineup (codemods_lineup.py)
    #
    # A character's lineup is the number on its <FONTGRIDDATA:y:<code>:<n>> line (0 the first, 1 the second; the game
    # ignores it, the second lineup's code mod reads it). Its hotspot is in that lineup's picture (the same 1024 x 256
    # space). Lineup 2's background is ui_characterlineup2l24b / r24b when the mods folder or the disc has them, else
    # lineup 1's. The Data Editor's build puts the code mod on the working disc when a character is on lineup 2.
    LINEUP_BG = (('ui_characterlineupl24b.tex', 'ui_characterlineupr24b.tex'),
                 ('ui_characterlineup2l24b.tex', 'ui_characterlineup2r24b.tex'))

    def grid_lineups(self):
        """{code: lineup (0 / 1)} of the select screen's list."""
        text, _from = self._poster_text('front.dat')
        return {m.group(2).lower(): (1 if int(m.group(3)) else 0) for m in self._GRID.finditer(text or '')}

    def _bg_halves(self, lineup):
        """The two background halves (.tex bytes) a lineup is drawn on, and whether they are its own."""
        if lineup:
            own = [self._mod_or_disc('front.dat', n) for n in self.LINEUP_BG[1]]
            if all(own):
                return own, True
        return [self._mod_or_disc('front.dat', n) for n in self.LINEUP_BG[0]], not lineup

    def lineup_info(self):
        """{counts: [lineup 1, lineup 2], own_bg2, bg1_changed}: what the Showdown dialog offers."""
        g = self.grid_lineups()
        mods = lambda n: any(os.path.isfile(os.path.join(self.de.folder_path(a), n)) for a in ('front.dat', 'swat.dat'))
        names = {}
        try:
            t = self._names_table()
            if t and t['blocks']:
                names = {e[0]: e[3] for e in t['blocks'][0]}
        except Exception:                                       # noqa: BLE001
            pass
        return {'roster': [{'code': c, 'name': names.get(ST.elf(c)) or c.upper(), 'lineup': n} for c, n in g.items()],
                'counts': [sum(1 for v in g.values() if v == 0), sum(1 for v in g.values() if v == 1)],
                'own_bg2': all(self._mod_or_disc('front.dat', n) for n in self.LINEUP_BG[1]),
                'bg1_changed': any(mods(n) for n in self.LINEUP_BG[0])}

    def lineup_bg_set(self, lineup, data, crop=None):
        """A picture (base64) as a lineup's background: cropped (crop {x, y, w, h}), made 1024 x 256 and cut into the
        two 512 x 256 halves the game loads, in its own format (24-bit RGB, format 17, flags as shipped), into
        front.dat and swat.dat (both carry the shipped pair)."""
        import numpy as np
        from PIL import Image
        from . import pckscene as PS
        self._busy_check('mods')
        im = PS.load_image(base64.b64decode(data or '')).convert('RGB')
        if crop:
            x, y = max(0, int(crop.get('x', 0))), max(0, int(crop.get('y', 0)))
            w, h = max(1, int(crop.get('w', im.width))), max(1, int(crop.get('h', im.height)))
            im = im.crop((x, y, min(im.width, x + w), min(im.height, y + h)))
        a = np.asarray(im.resize((1024, 256), Image.LANCZOS), dtype=np.uint8)
        head = struct.pack('<4H', 512, 256, 17, 1) + bytes.fromhex('010001004100')
        wrote = []
        for half, name in zip((a[:, :512], a[:, 512:]), self.LINEUP_BG[1 if lineup else 0]):
            tex = head + np.ascontiguousarray(half[::-1]).tobytes()  # stored bottom row first, as the shipped pair
            for arch in ('front.dat', 'swat.dat'):
                folder = self.de.folder_path(arch)
                os.makedirs(folder, exist_ok=True)
                with open(os.path.join(folder, name), 'wb') as fh:
                    fh.write(tex)
                wrote.append(arch + '/' + name)
        return {'wrote': wrote}

    def lineup_bg_reset(self, lineup):
        """A lineup's background back: lineup 1 to the game's, lineup 2 to lineup 1's (its own pair taken out of
        the mods folder)."""
        self._busy_check('mods')
        gone = []
        for name in self.LINEUP_BG[1 if lineup else 0]:
            for arch in ('front.dat', 'swat.dat'):
                p = os.path.join(self.de.folder_path(arch), name)
                if os.path.isfile(p):
                    os.remove(p)
                    gone.append(arch + '/' + name)
        return {'removed': gone}

    # A character's in-match sounds are its .swt's CreatureSoundSet N: the bank snd_N (its voice and unique sounds --
    # kiai, injuries, deaths ...: snd_N.csv + .hd in swat.dat, snd_N.bd + .hd in banks.dat); its footsteps its
    # StepType (1 for 176 of the game's characters, 3 for 7). A Showdown character made from another's files has
    # that one's (gbw from Mr Kelly: set 143, Mr Kelly's voice).
    def sound_sets(self):
        """[{code, name, set, step}] of the Showdown roster (their m<code>.swt), for the dialog."""
        names = {}
        try:
            t = self._names_table()
            if t and t['blocks']:
                names = {e[0]: e[3] for e in t['blocks'][0]}
        except Exception:                                       # noqa: BLE001
            pass
        out = []
        for c in self.grid_lineups():
            d = self._mod_or_disc('swat.dat', 'm%s.swt' % c)
            if not d:
                continue
            s = re.search(rb'CreatureSoundSet\s+(\d+)', d)
            st = re.search(rb'StepType\s+(\d+)', d)
            out.append({'code': c, 'name': re.sub(r'[\s,]+', ' ', names.get(ST.elf(c)) or c.upper()).strip(),
                        'set': int(s.group(1)) if s else None,
                        'step': int(st.group(1)) if st else None})
        return sorted(out, key=lambda r: r['name'])

    def _bank_numbers(self):
        from .game import Game
        have = set()
        g = Game(self.de.s.modded_path, writable=False)
        for arch in ('swat.dat', 'banks.dat'):
            for n in list(self._disc_archive(g, arch)) + (os.listdir(self.de.folder_path(arch))
                                                          if os.path.isdir(self.de.folder_path(arch)) else []):
                m = re.match(r'snd_(\d+)\.(csv|hd|bd)$', n.rsplit('/', 1)[-1].lower())
                if m:
                    have.add(int(m.group(1)))
        return have

    def mp_sounds(self, code, mode='keep', source=None, step='keep'):
        """`code`'s in-match sounds: mode 'same' -- `source`'s sound set; 'new' -- a new bank (the next free number
        from 189) copied from `source`'s, its rows renumbered, so its sounds can be changed alone (the Sound tab);
        'keep' -- as it is. `step`: 'keep', 'source' (the source's StepType) or a number. -> {set, step, wrote}."""
        self._busy_check('mods')
        code = (code or '').lower()
        if len(code) == 4 and code.startswith('m'):
            code = code[1:]
        sets = {r['code']: r for r in self.sound_sets()}
        mine = self._mod_or_disc('swat.dat', 'm%s.swt' % code)
        if not mine:
            raise ValueError('m%s.swt is not in swat.dat: add the character to Showdown first' % code)
        src = sets.get((source or '').lower().removeprefix('m') if source and len(source) == 4 else (source or '').lower())
        if mode in ('same', 'new') and (not src or src['set'] is None):
            raise ValueError('pick the character whose sounds to use')
        wrote, new_set = [], None
        if mode == 'same':
            new_set = src['set']
        elif mode == 'new':
            have = self._bank_numbers()
            n = next(k for k in range(189, 1000) if k not in have)
            old = src['set']
            csv = self._game_file('snd_%d.csv' % old)
            if not csv:
                raise ValueError('snd_%d.csv (%s\'s sounds) is not on the disc' % (old, src['name']))
            t = csv.decode('latin-1')
            nl = '\r\n' if '\r\n' in t else '\n'
            lines = t.split(nl)
            for i, l in enumerate(lines):
                c = l.split(',')
                if i == 0 and l.startswith('#'):
                    lines[i] = '# Voice and unique sounds for %s (a copy of %s\'s, bank %d)' % (code.upper(), src['name'], old) \
                               + ',' * (len(c) - 1)
                elif len(c) > 2 and c[2].strip() == str(old) and c[0].strip() and not c[0].startswith(('#', 'SAMPLES', 'MaxCode')):
                    c[2] = str(n)
                    lines[i] = ','.join(c)
            data = {'swat.dat': [('snd_%d.csv' % n, nl.join(lines).encode('latin-1'))], 'banks.dat': []}
            from .game import Game
            g = Game(self.de.s.modded_path, writable=False)
            for ext in ('hd', 'bd'):
                for arch in ('banks.dat', 'swat.dat'):
                    files = self._disc_archive(g, arch)
                    rd = next((r for k, r in files.items() if k.rsplit('/', 1)[-1] == 'snd_%d.%s' % (old, ext)), None)
                    if rd:
                        data[arch].append(('snd_%d.%s' % (n, ext), rd()))
            if not any(nm.endswith('.bd') for nm, _b in data['banks.dat']):
                raise ValueError('snd_%d.bd (%s\'s sound data) is not in banks.dat' % (old, src['name']))
            for arch, items in data.items():
                folder = self.de.folder_path(arch)
                os.makedirs(folder, exist_ok=True)
                for nm, b in items:
                    with open(os.path.join(folder, nm), 'wb') as fh:
                        fh.write(b)
                    wrote.append(arch + '/' + nm)
            new_set = n
        t = mine.decode('latin-1')
        if new_set is not None:
            t = re.sub(r'(CreatureSoundSet\s+)\d+', lambda m: m.group(1) + str(new_set), t, count=1)
        new_step = None
        if step == 'source' and src and src['step'] is not None:
            new_step = src['step']
        elif str(step).isdigit():
            new_step = int(step)
        if new_step is not None:
            t = re.sub(r'(StepType\s+)\d+', lambda m: m.group(1) + str(new_step), t, count=1)
        if t.encode('latin-1') != mine:
            folder = self.de.folder_path('swat.dat')
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, 'm%s.swt' % code), 'wb') as fh:
                fh.write(t.encode('latin-1'))
            wrote.append('swat.dat/m%s.swt' % code)
        cur = re.search(r'CreatureSoundSet\s+(\d+)', t)
        cst = re.search(r'StepType\s+(\d+)', t)
        return {'set': int(cur.group(1)) if cur else None, 'step': int(cst.group(1)) if cst else None, 'wrote': wrote}

    def unlock_xbox_stages(self, fresh):
        """A Showdown map in the mods folder whose stage row in unlockables.csv ends in "xbox" (lvl_colseum, the
        Xbox-only Coliseum): the PS2 game's unlock loader skips such a row (0x00182C40), so the stage counts as locked
        and is never offered. The marker taken off and the stage made unlocked from the start. Taking it off also ends
        the game's own one-row shift (rows after it read the Default of the row before -- the filler rows the Studio
        writes for new characters cover both readings). -> notes."""
        keys = set()
        for p in fresh:
            m = re.fullmatch(r'lvls_m[24]/(.+?)[24]\.dat', p)
            if m:
                keys.add('lvl_' + m.group(1).lower())
        if not keys:
            return []
        d = self._mod_or_disc('swat.dat', 'unlockables.csv')
        if not d:
            return []
        t = d.decode('latin-1')
        nl = '\r\n' if '\r\n' in t else '\n'
        rows = t.split(nl)
        done = []
        for i, r in enumerate(rows):
            c = r.split(',')
            if len(c) >= 6 and c[0].strip().lower() in keys and c[1].strip() == 'stage' and c[-1].strip().lower() == 'xbox':
                c[-1] = ''
                c[3], c[4], c[5] = '1', 'NONE', '0'
                rows[i] = ','.join(c)
                done.append(c[0].strip())
        if not done:
            return []
        folder = self.de.folder_path('swat.dat')
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, 'unlockables.csv'), 'wb') as fh:
            fh.write(nl.join(rows).encode('latin-1'))
        return ['%s: its unlock row was marked "xbox" (the PS2 game skipped it, so the stage never showed): marker taken '
                'off, unlocked from the start' % ', '.join(done)]

    def _mp_png(self, code):
        """A character's mp_ portrait as PNG bytes (the mugshot the end screen shows), or None."""
        import io as _io
        import numpy as np
        from PIL import Image
        from . import texture as TX
        d = self._mod_or_disc('front.dat', 'mp_%s.tex' % code) or self._mod_or_disc('swat.dat', 'mp_%s.tex' % code)
        if not d:
            return None
        w, h, rgba = TX.decode_tex_file(d)[:3]
        a = np.frombuffer(bytes(rgba), np.uint8).reshape(h, w, 4)[::-1]          # file rows are bottom first
        buf = _io.BytesIO()
        Image.fromarray(np.ascontiguousarray(a)).save(buf, 'PNG')
        return buf.getvalue()

    def mugshots_sync(self):
        """The Showdown end-of-match screen (swat.dat mprewards.pck) given a mugshot for every character on the select
        screen it has none for -- added ones and the dozen of the game's own it never had (mugshots.py) -- from their
        mp_ portraits; the mugshots added before brought up to date with their portraits. Written to the mods folder
        only when it changes. -> notes."""
        from . import mugshots as MG, pckscene as PS
        cur = self._mod_or_disc('swat.dat', MG.FILE)
        if not cur:
            return []
        fixed = MG.repair(cur)                  # a copy written by the first version (its script never ran)
        repaired = fixed != cur
        van = getattr(self.de.s, 'vanilla', None) or self.de.s.disc
        stock = van.archive('SWAT.DAT').read(MG.FILE)
        v0, v1, _c = MG.arrays(PS.Scene(stock))
        c0, c1, _c = MG.arrays(PS.Scene(cur))
        roster = list(self.grid_lineups())
        add, pics = [], {}
        for c in roster:
            if c in v0 or c in v1:
                continue
            png = self._mp_png(c)
            if png is None:
                continue
            pics[c] = png
            if c not in c0 and c not in c1:
                add.append((c, png))
        new = MG.add(fixed, add) if add else fixed
        keep = [(c, png) for c, png in pics.items() if c not in dict(add)]
        if keep:
            new = MG.refresh(new, keep)
        if new == cur:
            return []
        if repaired:
            add = add or []
        folder = self.de.folder_path('swat.dat')
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, MG.FILE), 'wb') as fh:
            fh.write(new)
        out = []
        if add:
            out.append('end-of-match mugshots added for %s' % ', '.join(c for c, _p in add))
        if keep and not repaired:
            out.append('end-of-match mugshots kept up to date with their portraits')
        if repaired:
            out.append('end-of-match mugshots repaired (the screen\'s script copy pointed at itself, so the game ran '
                       'none of it and showed squares)')
        return out

    # A Showdown character's stats, where the game keeps them:
    #   m<code>.cr   HealthData / MaxHitPoints
    #   m<code>.swt  SwatCreatureType: MRKDodge / PGJDodge / BDGDodge (the dodge styles, 0 / 1), DodgeTime,
    #                NormalSpecialMove / MeleeSpecialMove (spmove_lockblast ...), the damages and the rest
    #   postermultiview.swui (both copies): CharWeaponTable (starting weapons, one or two), CharSpecialTable (the
    #                deadeye shown and used: Lock Blast, Ricochet ...), AIBrainTable (the bot) -- by code, else 'default'
    #   m<code>.ars  the weapons it carries (NumTypes n, then "<weapon> 0" lines), kept to the weapon table
    _SWT_LINE = re.compile(r'(?m)^\t(\w+)[ \t]+(\S+)[ \t]*\r?$')

    def _poster_rows(self, table):
        text, _f = self._poster_text('front.dat')
        lines = (text or '').splitlines()
        head = next((i for i, l in enumerate(lines) if l.strip().lower() == '<lookup:%s>' % table.lower()), None)
        rows = {}
        if head is None:
            return rows
        for l in lines[head + 1:]:
            if not l.lstrip().startswith('<LOOKUPDATA:'):
                break
            body = l.strip()[len('<LOOKUPDATA:'):].rstrip('>')
            parts = [x.strip() for x in body.split(',')]
            rows[parts[0].lower()] = parts[1:]
        return rows

    # every special move the game's .swt / .cr files use (swat.dat, 2026-11-16)
    SPMOVES = ('spmove_none', 'spmove_deadeye', 'spmove_lockblast', 'spmove_ricochet', 'spmove_countershoot',
               'spmove_dynamitebarrier', 'spmove_ghosteffect', 'spmove_armor', 'spmove_tackle', 'spmove_glide',
               'spmove_glideattack', 'spmove_crazyhorse', 'spmove_grab', 'spmove_crazyshoot', 'spmove_gunsmoke',
               'spmove_firebreath', 'spmove_cannon', 'spmove_rocktoss', 'spmove_whip', 'spmove_firearrow',
               'spmove_poisonarrow', 'spmove_clawattack', 'spmove_clawthrow', 'spmove_8knives', 'spmove_bucketdodge',
               'spmove_firebarrier', 'spmove_crouchshoot', 'spmove_rollshoot', 'spmove_lasso', 'spmove_flarerifle',
               'spmove_setcharge', 'spmove_coffingun', 'spmove_coffinswing', 'spmove_poisonbreath',
               'spmove_explosiveround', 'spmove_dropprojectile',
               'spmove_timeupgrade_cr_shotgun_ignite', 'spmove_timeupgrade_cr_shotgun_explode',
               'spmove_timeupgrade_cr_pistol_poison', 'spmove_timeupgrade_cr_pistol_ignite',
               'spmove_timeupgrade_cr_molotov_explode', 'spmove_timeupgrade_cr_molotov_ice',
               'spmove_timeupgrade_cr_molotov_hotcoffee', 'spmove_timeupgrade_cr_ghost',
               'spmove_timeupgrade_cr_burningrage', 'spmove_timeupgrade_cr_stickybomb',
               'spmove_timeupgrade_cr_rifle_piercing', 'spmove_timeupgrade_cr_regen_health',
               'spmove_timeupgrade_cr_knife_southpaw', 'spmove_timeupgrade_cr_knife_pickpocket',
               'spmove_timeupgrade_cr_forgedcard', 'spmove_timeupgrade_cr_fastbullet',
               'spmove_timeupgrade_cr_dynamite_explode', 'spmove_timeupgrade_cr_arrow_poison',
               'spmove_timeupgrade_cr_arrow_explode')

    def creature_stats(self, code):
        """{code, health, swt: {field: value} (top level), weapons, special, brain, choices}."""
        code = code.lower().removeprefix('m') if len(code) == 4 and code.startswith('m') else code.lower()
        cr = self._mod_or_disc('swat.dat', 'm%s.cr' % code)
        swt = self._mod_or_disc('swat.dat', 'm%s.swt' % code)
        if not cr or not swt:
            raise ValueError('m%s.cr / m%s.swt are not in swat.dat' % (code, code))
        hp = re.search(rb'MaxHitPoints\s+(\S+)', cr)
        fields = {k: v for k, v in self._SWT_LINE.findall(swt.decode('latin-1'))}
        w, sp, br = self._poster_rows('CharWeaponTable'), self._poster_rows('CharSpecialTable'), self._poster_rows('AIBrainTable')
        # the choices: every value the game's own files use
        allw, allsp, allbr, moves = set(), set(), set(), set()
        for r in w.values():
            allw |= {x for x in r if x}
        for r in sp.values():
            allsp |= {x for x in r if x}
        for r in br.values():
            allbr |= {x for x in r if x}
        for c in self.grid_lineups():
            d = self._mod_or_disc('swat.dat', 'm%s.swt' % c)
            if d:
                moves |= set(re.findall(r'(?:Normal|Melee)SpecialMove\s+(\S+)', d.decode('latin-1')))
        return {'code': code, 'health': float(hp.group(1)) if hp else None, 'swt': fields,
                'weapons': w.get(code) or w.get('default') or [], 'special': (sp.get(code) or sp.get('default') or [''])[0],
                'brain': (br.get(code) or br.get('default') or [''])[0], 'own_rows': {
                    'weapons': code in w, 'special': code in sp, 'brain': code in br},
                'choices': {'weapons': sorted(allw), 'specials': sorted(allsp), 'brains': sorted(allbr),
                            'moves': sorted(moves | set(self.SPMOVES))}}

    def creature_stats_set(self, code, health=None, swt=None, weapons=None, special=None, brain=None):
        """The stats written into the mods folder (see creature_stats). Only what is given changes."""
        self._busy_check('mods')
        code = code.lower().removeprefix('m') if len(code) == 4 and code.startswith('m') else code.lower()
        wrote = []
        folder = self.de.folder_path('swat.dat')
        os.makedirs(folder, exist_ok=True)

        def put(name, data):
            with open(os.path.join(folder, name), 'wb') as fh:
                fh.write(data)
            wrote.append('swat.dat/' + name)
        if health is not None:
            cr = self._mod_or_disc('swat.dat', 'm%s.cr' % code).decode('latin-1')
            new = re.sub(r'(MaxHitPoints\s+)\S+', lambda m: m.group(1) + '%f' % float(health), cr, count=1)
            if new != cr:
                put('m%s.cr' % code, new.encode('latin-1'))
        if swt:
            t = self._mod_or_disc('swat.dat', 'm%s.swt' % code).decode('latin-1')
            new = t
            for k, v in swt.items():
                v = str(v).strip()
                if not re.fullmatch(r'\w+', k) or not v or re.search(r'\s', v):
                    raise ValueError('%s: a value is one word or number' % k)
                new = re.sub(r'(?m)^(\t%s[ \t]+)\S+' % re.escape(k), lambda m, v=v: m.group(1) + v, new, count=1)
            if new != t:
                put('m%s.swt' % code, new.encode('latin-1'))
        # the select screen's tables (both copies of postermultiview.swui)
        changes = {}
        if weapons is not None:
            ws = [x.strip() for x in weapons if x and x.strip()][:2]
            if not ws:
                raise ValueError('a character starts with at least one weapon')
            changes['CharWeaponTable'] = ws
        if special:
            changes['CharSpecialTable'] = [special.strip()]
        if brain:
            changes['AIBrainTable'] = [brain.strip()]
        if changes:
            game = None
            for arch in self._POSTER_IN:
                if game is None and not os.path.isfile(os.path.join(self.de.folder_path(arch), self._POSTER)):
                    from .game import Game
                    game = Game(self.de.s.modded_path, writable=False)
                text, _f = self._poster_text(arch, game)
                if not text:
                    continue
                nl = '\r\n' if '\r\n' in text else '\n'
                lines = text.split(nl)
                for table, vals in changes.items():
                    head = next((i for i, l in enumerate(lines) if l.strip().lower() == '<lookup:%s>' % table.lower()), None)
                    if head is None:
                        continue
                    end = head + 1
                    while end < len(lines) and lines[end].lstrip().startswith('<LOOKUPDATA:'):
                        end += 1
                    row = '<LOOKUPDATA:%s,\t%s>' % (code, ',\t'.join(vals))
                    at = next((i for i in range(head + 1, end)
                               if re.match(r'\s*<LOOKUPDATA:%s\s*,' % re.escape(code), lines[i], re.I)), None)
                    if at is not None:
                        lines[at] = row
                    else:
                        lines.insert(end, row)
                f2 = self.de.folder_path(arch)
                os.makedirs(f2, exist_ok=True)
                with open(os.path.join(f2, self._POSTER), 'wb') as fh:
                    fh.write(nl.join(lines).encode('latin-1'))
                wrote.append(arch + '/' + self._POSTER)
            if 'CharWeaponTable' in changes:
                ars = self._mod_or_disc('swat.dat', 'm%s.ars' % code)
                nl = b'\r\n' if ars and b'\r\n' in ars else b'\n'
                ws = changes['CharWeaponTable']
                put('m%s.ars' % code, (('NumTypes %d' % len(ws)).encode() + nl +
                                       b''.join(('%s \t\t0' % x).encode('latin-1') + nl for x in ws)))
        self.de._log('Showdown editor: %s stats -- %d files into the mods folder' % (code, len(wrote)))
        return {'wrote': sorted(set(wrote))}

    # ------------------------------------------------------------ alternate looks for players 2-4 (altlooks.py)

    def _mod_file(self, arch, name):
        """The path of `name` in the archive's mods folder (at its top or in a character's own folder), or None."""
        top = self.de.folder_path(arch)
        p = os.path.join(top, name)
        if os.path.isfile(p):
            return p
        if os.path.isdir(top):
            for r, _ds, fs in os.walk(top):
                hit = next((f for f in fs if f.lower() == name.lower()), None)
                if hit:
                    return os.path.join(r, hit)
        return None

    def _alt_codes(self, code):
        """The names the alternates are looked up by: the model's (<code>_lods.rsc) -- a match showed dad02 / gbw03
        loaded for mdad / mgbw, which draw dad's / gbw's model; mgwk, with a model of its own, has mgwk02."""
        return [code]

    @staticmethod
    def _png_b64(w, h, rgba):
        from PIL import Image
        buf = io.BytesIO()
        Image.frombytes('RGBA', (w, h), bytes(rgba)).save(buf, 'PNG')
        return base64.b64encode(buf.getvalue()).decode()

    def alt_looks(self, code):
        """{code, textures: [{name, w, h, png}], multi, alts}. One texture: alts {2: {png, own} | None, ...} (<code>0N.tex,
        the game's way). Several: textures in the alttex code mod's order and alts {2: {k: {png, own} | None}}
        (<code>0N_<k>.tex; own = not a plain copy of the model's)."""
        from . import altlooks as AL, texture as TX
        code = code.lower()
        bundle = self._mod_or_disc('swat.dat', '%s_lods.rsc' % code)
        if not bundle:
            raise ValueError('%s_lods.rsc is not in swat.dat' % code)
        ts = AL.part_order(bundle)
        multi = len(ts) > 1
        out, plain = [], {}
        for k, t in enumerate(ts):
            png = None
            try:
                w, h, px = AL.texture_rgba(bundle, t)
                png = self._png_b64(w, h, px)
            except Exception:                                   # noqa: BLE001
                pass
            out.append({'name': t['name'], 'w': t['w'], 'h': t['h'], 'png': png})

        def pic(name, copy=None):
            d = self._mod_or_disc('swat.dat', name)
            got = TX.decode_tex_file(d) if d else None
            if not got:
                return None
            own = bool(self._mod_file('swat.dat', name)) and (copy is None or d != copy)
            return {'png': self._png_b64(*got[:3]), 'own': own}
        alts = {}
        for n in AL.PLAYERS:
            if multi:
                alts[n] = {k: pic(AL.alt_name_k(code, n, k), plain.get(k)) for k in range(len(ts))}
            else:
                alts[n] = pic(AL.alt_name(code, n))
        return {'code': code, 'textures': out, 'multi': multi, 'alts': alts}

    def alt_looks_set(self, code, texture=None, pics=None):
        """`pics`: one texture -- {player: picture}; several -- {player: {k: picture}}. A picture is a base64 image,
        {'tint': degrees, 'sat': x, 'light': x} (the texture recoloured) or '' (back to the model's own); a player given
        '' instead of a dict is reset whole. With several textures every one of a player's is written once any is set
        (the code mod stops at the first missing), the rest as copies of the model's; a player with none of their own
        has their files removed. -> {wrote, removed}."""
        from . import altlooks as AL, pckscene as PS
        self._busy_check('mods')
        code = code.lower()
        folder = self.de.folder_path('swat.dat')
        os.makedirs(folder, exist_ok=True)
        wrote, removed = [], []
        bundle = self._mod_or_disc('swat.dat', '%s_lods.rsc' % code)
        if not bundle:
            raise ValueError('%s_lods.rsc is not in swat.dat' % code)
        ts = AL.part_order(bundle)
        multi = len(ts) > 1
        bases = {}

        def base(k):
            if k not in bases:
                bases[k] = AL.texture_rgba(bundle, ts[k])
            return bases[k]

        def encode(pic, k):
            t = ts[k]
            if isinstance(pic, dict):
                w, h, px = base(k)
                px = AL.tinted(px, w, h, float(pic.get('tint') or 0), float(pic.get('sat') or 1),
                               float(pic.get('light') or 1))
            else:
                im = PS.load_image(base64.b64decode(pic)).convert('RGBA')
                w, h, px = im.width, im.height, im.tobytes()
            return AL.encode_alt(px, w, h, t['w'], t['h'])          # full size: the build shrinks (sd_options)

        def put(name, data):
            p = self._mod_file('swat.dat', name) or os.path.join(folder, name)
            with open(p, 'wb') as fh:
                fh.write(data)
            wrote.append('swat.dat/' + name)

        def drop(name):
            p = self._mod_file('swat.dat', name)
            if p:
                os.remove(p)
                removed.append('swat.dat/' + name)
        for n, val in (pics or {}).items():
            n = int(n)
            if n not in AL.PLAYERS or val is None:
                continue
            if not multi:
                if val == '':
                    drop(AL.alt_name(code, n))
                else:
                    put(AL.alt_name(code, n), encode(val, 0))
                continue
            if val == '':
                val = {k: '' for k in range(len(ts))}
            if not isinstance(val, dict):
                raise ValueError('a model with several textures takes a picture per texture')
            # only the changed ones: the code mod keeps a missing one the model's own (each takes video memory)
            for k in range(len(ts)):
                v = val.get(str(k), val.get(k))
                if v is None:
                    continue
                if v == '':
                    drop(AL.alt_name_k(code, n, k))
                else:
                    put(AL.alt_name_k(code, n, k), encode(v, k))
        self.de._log('Showdown editor: %s alternate looks -- %d written, %d removed' % (code, len(wrote), len(removed)))
        return {'wrote': wrote, 'removed': removed, 'note': ''}

    def alt_tidy(self):
        """Before a build: per-texture alternates (<code>0N_<k>.tex) in the mods folder that are plain copies of the
        model's texture removed (the first version wrote every one), and any bigger than altlooks.ALT_MAX made smaller
        (they are kept in video memory). -> notes."""
        from . import altlooks as AL, texture as TX
        top = self.de.folder_path('swat.dat')
        if not os.path.isdir(top):
            return []
        pat = re.compile(r'^(\w+?)0([2-4])_(\d+)\.tex$', re.I)
        found = {}
        for r, _ds, fs in os.walk(top):
            for f in fs:
                mm = pat.match(f)
                if mm:
                    found.setdefault(mm.group(1).lower(), []).append((os.path.join(r, f), int(mm.group(3))))
        gone = shrunk = 0
        for code, files in found.items():
            bundle = self._mod_or_disc('swat.dat', '%s_lods.rsc' % code)
            if not bundle:
                continue
            ts = AL.part_order(bundle)
            for path, k in files:
                if k >= len(ts):
                    continue
                d = open(path, 'rb').read()
                got = TX.decode_tex_file(d)
                if not got:
                    continue
                w, h, px = got[:3]
                t = ts[k]
                bw, bh, bpx = AL.texture_rgba(bundle, t)
                if (w, h) == (bw, bh) and d == AL.encode_alt(bpx, bw, bh, w, h):
                    os.remove(path)
                    gone += 1
                    continue
        out = []
        if gone:
            out.append('%d unchanged per-texture alternate(s) removed (players 2-4 keep the model\'s own there)' % gone)
        if shrunk:
            out.append('%d per-texture alternate(s) made %d x %d at most (video memory)' % (shrunk, AL.ALT_MAX, AL.ALT_MAX))
        return out

    # ------------------------------------------------------------ Showdown characters' texture sizes on the disc
    #
    # Per character (data_editor/showdown_options.json, both on unless turned off): its own textures and its players 2-4
    # alternates made 128 x 128 at most WHEN THE BUILD WRITES THE DISC -- the mods folder keeps them full size, so
    # turning an option off puts the full-size ones back at the next build. Video memory is 4 MB.

    SD_DEFAULTS = {'own_small': True, 'alt_small': True}

    def _sd_path(self):
        return os.path.join(self.de.home, 'showdown_options.json')

    def sd_options(self, code=None):
        import json
        try:
            allo = json.load(open(self._sd_path(), encoding='utf-8'))
        except (OSError, ValueError):
            allo = {}
        if code is None:
            return allo
        return dict(self.SD_DEFAULTS, **allo.get(code.lower(), {}))

    def sd_options_set(self, code, own_small=None, alt_small=None):
        import json
        allo = self.sd_options()
        cur = dict(self.SD_DEFAULTS, **allo.get(code.lower(), {}))
        if own_small is not None:
            cur['own_small'] = bool(own_small)
        if alt_small is not None:
            cur['alt_small'] = bool(alt_small)
        allo[code.lower()] = cur
        with open(self._sd_path(), 'w', encoding='utf-8') as fh:
            json.dump(allo, fh, indent=1)
        return cur

    def sd_options_key(self):
        """What the build's 'anything changed?' key adds for swat.dat (an option changes no file)."""
        import json
        return json.dumps(self.sd_options(), sort_keys=True)

    _SD_BUNDLE = re.compile(r'^(\w+)_lods\.rsc$', re.I)
    _SD_ALT = re.compile(r'^(\w+?)0[2-4]_\d+\.tex$', re.I)

    def build_transform(self, name, data, log=None):
        """swat.dat, as the build reads a mod file: a Showdown character's bundle / per-texture alternate made
        128 x 128 at most when its option is on (the default). -> bytes."""
        from . import altlooks as AL, texture as TX
        name = name.lower()
        mm = self._SD_BUNDLE.match(name)
        if mm:
            code = mm.group(1)
            if code in self._sd_roster() and self.sd_options(code)['own_small']:
                new = AL.shrink_bundle(data)
                if new != data and log:
                    log('  %s: its textures made %d x %d at most on the disc (Showdown editor: untick to keep them full '
                        'size)' % (name, AL.ALT_MAX, AL.ALT_MAX))
                return new
            return data
        mm = self._SD_ALT.match(name)
        if mm and self.sd_options(mm.group(1))['alt_small']:
            got = TX.decode_tex_file(data)
            if got:
                w, h, px = got[:3]
                tw, th = AL.alt_size(w, h)
                if (tw, th) != (w, h):
                    return AL.encode_alt(px, w, h, tw, th)
        return data

    def _sd_roster(self):
        if getattr(self, '_sd_roster_cache', None) is None:
            try:
                self._sd_roster_cache = set(self.grid_lineups())
            except Exception:                                   # noqa: BLE001
                self._sd_roster_cache = set()
        return self._sd_roster_cache

    def mp_place(self, code, spot, layer=None, on_lineup=None):
        """The Showdown editor: a roster character's place on the select screen changed, nothing else -- its hotspot
        ({x, sx, sy}), its layer among the others on its lineup, its lineup (0 / 1). Both copies of
        characterlineup.swui and postermultiview.swui; the voice lines numbered again (the list order may move)."""
        self._busy_check('mods')
        code = (code or '').lower()
        if len(code) == 4 and code.startswith('m'):
            code = code[1:]
        g = self.grid_lineups()
        if code not in g:
            raise ValueError('%s is not on the select screen\'s list' % code)
        lineup = g[code] if on_lineup is None else (1 if on_lineup else 0)
        wrote = self._poster_roster_put(code, None, None, layer=layer, lineup=lineup)
        wrote += self._lineup_put(code, spot, None, layer=layer, lineup=lineup)
        wrote += self._voice_sync()
        self.de._log('Showdown editor: %s placed on lineup %d' % (code, lineup + 1))
        return {'code': code, 'lineup': lineup, 'wrote': sorted(set(wrote))}

    def mp_lineup(self, exclude=None, lineup=0):
        """The select screen as the game draws it, minus the characters in `exclude`: {png (the background,
        1024x256, base64), stack: [{code, x, png}] every other character's photo in the order the game draws them
        (so a new one can be drawn at any layer), hotspots: [{code, x, sx, sy}], photos: {code: upright png} for the
        excluded ones, own: {code: hotspot}, layer: {code: its place in the stack}}."""
        import io as _io
        import numpy as np
        from PIL import Image
        from . import texture as TX
        exclude = {c.lower().removeprefix('m') if len(c) == 4 and c.startswith('m') else c.lower()
                   for c in (exclude or []) if c}

        def png(im):
            buf = _io.BytesIO(); im.save(buf, 'PNG')
            return base64.b64encode(buf.getvalue()).decode()
        halves = []
        lineup = 1 if lineup else 0
        datas, own_bg = self._bg_halves(lineup)
        for d in datas:
            w, h, rgba = TX.decode_tex_file(d)[:3]
            halves.append(np.frombuffer(bytes(rgba), np.uint8).reshape(h, w, 4))  # these two read top first
        bg = Image.fromarray(np.concatenate(halves, 1)).convert('RGBA')
        spots = {}
        for m in self._HOTSPOT.finditer(self._lineup_text()):
            x, _y, _z, sx, sy, code = m.groups()
            spots[code.lower()] = {'code': code.lower(), 'x': int(x), 'sx': int(sx), 'sy': int(sy)}
        order = self._draw_order(lineup)
        for c in exclude:                       # its own place counts on the lineup shown, wherever it is now
            if c in spots and c not in order:
                order = order + [c]
        stack, photos, layer = [], {}, {}
        for code in order:
            if code in exclude:
                layer[code] = len(stack)                        # where it is drawn now, among the others
                continue
            ph = self._mod_or_disc('front.dat', 'ui_mphoto_%s.tex' % code)
            if ph:
                stack.append({'code': code, 'x': spots[code]['x'], 'png': png(Image.fromarray(self._upright(ph)))})
        for c in exclude:
            ph = self._mod_or_disc('front.dat', 'ui_mphoto_%s.tex' % c)
            if ph:
                photos[c] = png(Image.fromarray(self._upright(ph)))
        g = self.grid_lineups()
        return {'png': png(bg), 'w': bg.width, 'h': bg.height, 'stack': stack, 'lineup': lineup, 'own_bg': own_bg,
                'hotspots': [s for c, s in spots.items() if c not in exclude and c in order],
                'photos': photos, 'own': {c: s for c, s in spots.items() if c in exclude}, 'layer': layer,
                'on': {c: g.get(c, 0) for c in exclude}, 'info': self.lineup_info()}

    def _layer_anchor(self, code, replace, layer, order, lineup=None):
        """(the code to go right before, or None for after everyone else) for `layer` (its place among the other
        characters' photos on its lineup: 0 behind all, len in front of all; None: in front)."""
        others = [c for c in self._draw_order(lineup) if c not in (code, replace)]
        if layer is None:
            layer = 0 if order == 'back' else len(others)
        layer = max(0, min(len(others), int(layer)))
        return others[layer] if layer < len(others) else None, others

    def _lineup_put(self, code, spot, replace=None, order='front', layer=None, lineup=None):
        """hotspot_<code> written into both copies of characterlineup.swui: at `spot` ({x, sx, sy}), or in the place
        and position of hotspot_<replace>; at `layer` among the others on its lineup (see _layer_anchor), the same
        place its line takes in postermultiview.swui (the list the game draws by), so the two files keep one order."""
        before, _others = self._layer_anchor(code, replace, layer, order, lineup)
        t = self._lineup_text()
        nl = '\r\n' if '\r\n' in t else '\n'
        lines = t.split(nl)
        idx = [i for i, l in enumerate(lines) if self._HOTSPOT.search(l)]
        own = [i for i in idx if self._HOTSPOT.search(lines[i]).group(6) == code]
        old = [i for i in idx if replace and self._HOTSPOT.search(lines[i]).group(6) == replace]
        if spot:
            row = '<POSMARKER:%d,0,0:%d,%d:hotspot_%s>' % (int(spot['x']), int(spot['sx']), int(spot['sy']), code)
        elif old:
            m = self._HOTSPOT.search(lines[old[0]])
            row = '<POSMARKER:%s,%s,%s:%s,%s:hotspot_%s>' % (m.group(1), m.group(2), m.group(3), m.group(4), m.group(5), code)
        elif own:
            return []
        else:
            raise ValueError('hotspot_%s needs a place on the lineup' % code)
        if old and not spot and layer is None:
            lines[old[0]] = row                                 # takes the slot's place, its draw order too
            for i in sorted(own, reverse=True):
                if i != old[0]:
                    del lines[i]
        else:
            for i in sorted(own + old, reverse=True):
                del lines[i]
            idx = [i for i, l in enumerate(lines) if self._HOTSPOT.search(l)]
            hit = [i for i in idx if before and self._HOTSPOT.search(lines[i]).group(6).lower() == before]
            at = hit[0] if hit else (idx[-1] + 1 if idx else len(lines))
            lines.insert(at, row)
        data = nl.join(lines).encode('latin-1')
        wrote = []
        for arch in ('front.dat', 'swat.dat'):
            folder = self.de.folder_path(arch)
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, 'characterlineup.swui'), 'wb') as fh:
                fh.write(data)
            wrote.append(arch + '/characterlineup.swui')
        return wrote

    # The select screen's list of characters is NOT the roster table: SLUS_205.00 builds it from the
    # <FONTGRIDDATA:y:<code>:0> lines of postermultiview.swui (the .swui tag parser at 0x00107408 makes one 48-byte
    # entry per line -- the symbol, the code, the number -- and adds it to the screen's list, 0x00113008), and the
    # select loop (0x0013F2F8) draws and offers only those. A character in mpcharbounty.csv with no line there never
    # shows (a savestate on the select screen held exactly the 48 shipped entries, the added one in neither). The
    # symbol: y = the photo turned upright (every character but mok, who lies down: x). The file's tables
    # CharWeaponTable / CharSpecialTable / AIBrainTable give a character its starting weapons, special and bot brain,
    # by code (rows missing: the 'default' row).
    _GRID = re.compile(r'<FONTGRIDDATA:(\w):(\w+):(\d+)>')
    _CHAR_TABLES = ('CharWeaponTable', 'CharSpecialTable', 'AIBrainTable')

    def _poster_roster_put(self, code, replace=None, tpl=None, layer=None, order='front', lineup=None):
        """<FONTGRIDDATA:y:<code>:<lineup>> in both copies of postermultiview.swui -- at `layer` among the others on
        its lineup (the game draws the photos in this list's order: later over earlier), else in the place of
        `replace`'s line, else after the last character -- and its rows in the character tables copied from `tpl`
        (or `replace`). `lineup` None: the number its line (or the slot's) has."""
        game, wrote = None, []
        src = tpl or replace
        before = None
        if layer is not None or not replace:
            before, _others = self._layer_anchor(code, replace, layer, order, lineup)
        for arch in self._POSTER_IN:
            if game is None and not os.path.isfile(os.path.join(self.de.folder_path(arch), self._POSTER)):
                from .game import Game
                game = Game(self.de.s.modded_path, writable=False)
            text, _from = self._poster_text(arch, game)
            if not text:
                continue
            nl = '\r\n' if '\r\n' in text else '\n'
            lines = text.split(nl)
            grid = [i for i, l in enumerate(lines) if self._GRID.search(l)]
            if not grid:
                continue
            own = [i for i in grid if self._GRID.search(lines[i]).group(2).lower() == code]
            old = [i for i in grid if replace and self._GRID.search(lines[i]).group(2).lower() == replace]
            if old and layer is None:
                m = self._GRID.search(lines[old[0]])
                num = m.group(3) if lineup is None else str(1 if lineup else 0)
                lines[old[0]] = lines[old[0]].replace(m.group(0), '<FONTGRIDDATA:%s:%s:%s>' % (m.group(1), code, num))
                for i in sorted(own, reverse=True):
                    if i != old[0]:
                        del lines[i]
            else:
                was = self._GRID.search(lines[(own or old)[0]]) if (old or own) else None
                sym = was.group(1) if was else 'y'
                num = (was.group(3) if was else '0') if lineup is None else str(1 if lineup else 0)
                for i in sorted(set(own + old), reverse=True):
                    del lines[i]
                grid = [i for i, l in enumerate(lines) if self._GRID.search(l)]
                hit = [i for i in grid if before and self._GRID.search(lines[i]).group(2).lower() == before]
                lines.insert(hit[0] if hit else grid[-1] + 1, '<FONTGRIDDATA:%s:%s:%s>' % (sym, code, num))
            if src:
                for table in self._CHAR_TABLES:
                    head = next((i for i, l in enumerate(lines) if l.strip().lower() == '<lookup:%s>' % table.lower()), None)
                    if head is None:
                        continue
                    end = head + 1
                    while end < len(lines) and lines[end].lstrip().startswith('<LOOKUPDATA:'):
                        end += 1
                    keys = {re.match(r'\s*<LOOKUPDATA:([^,>]+)', lines[i]).group(1).strip().lower(): i
                            for i in range(head + 1, end)}
                    if code in keys or src not in keys:
                        continue
                    lines.insert(end, re.sub(r'(<LOOKUPDATA:)[^,>]+', lambda m: m.group(1) + code, lines[keys[src]], count=1))
            folder = self.de.folder_path(arch)
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, self._POSTER), 'wb') as fh:
                fh.write(nl.join(lines).encode('latin-1'))
            wrote.append(arch + '/' + self._POSTER)
        return wrote

    # The select screen's voice lines: snd_98.csv (front.dat and swat.dat, identical), one row per character -- a
    # stream (Stream 1, named as streams.dat names it without .stm) whose Code is the character's place in the
    # screen's list + 1 (pgj 1, bwi 2 ... ctn 48, the comment naming the character: "Red Harlow (msrd)"). A character
    # put in the list anywhere but its end moves everyone after it one place, and each then says the next one's line:
    # so the rows are numbered again by the list whenever it changes.
    _VOICE = 'snd_98.csv'
    _VOICE_WHO = re.compile(r'\(\s*(m\w+)\s*\)\s*"?\s*$')

    def voice_lines(self):
        """The select screen's voice lines: [{code, stream, name}] (code without the m)."""
        d = self._mod_or_disc('front.dat', self._VOICE) or self._mod_or_disc('swat.dat', self._VOICE)
        out = []
        for l in (d or b'').decode('latin-1').splitlines():
            m = self._VOICE_WHO.search(l)
            if m and ',' in l:
                out.append({'code': m.group(1).lower()[1:], 'stream': l.split(',')[0].strip(),
                            'name': l.rsplit(',', 1)[-1].strip().strip('"')})
        return out

    def _stream_names(self):
        from .game import Game
        have = {n.rsplit('.', 1)[0].lower() for n in self._disc_archive(Game(self.de.s.modded_path, writable=False), 'streams.dat')
                if n.lower().endswith('.stm')}
        mine = self.de.folder_path('streams.dat')
        if os.path.isdir(mine):
            have |= {f.rsplit('.', 1)[0].lower() for f in os.listdir(mine) if f.lower().endswith('.stm')}
        return have

    def _voice_sync(self, mcode=None, stream=None, label=None):
        """snd_98.csv's character rows numbered by the select screen's list (both copies); with `mcode` and `stream`
        given, that character's row set to the stream ('' takes its row out). Written only where it changes."""
        text, _from = self._poster_text('front.dat')
        order = [m.group(2).lower() for m in self._GRID.finditer(text or '')]
        if not order:
            return []
        wrote = []
        for arch in ('front.dat', 'swat.dat'):
            d = self._mod_or_disc(arch, self._VOICE)
            if not d:
                continue
            t = d.decode('latin-1')
            nl = '\r\n' if '\r\n' in t else '\n'
            lines = t.split(nl)
            who = lambda l: (self._VOICE_WHO.search(l).group(1).lower() if self._VOICE_WHO.search(l) and ',' in l else None)
            if mcode and stream is not None:
                lines = [l for l in lines if who(l) != mcode]
                if stream:
                    last = max((i for i, l in enumerate(lines) if who(l)), default=len(lines) - 1)
                    tail = '%s (%s)' % (re.sub(r'[",]', ' ', label or mcode).strip(), mcode)
                    lines.insert(last + 1, '%s,0,98,0,0,100,126,0,70,64,0,0,-1,1,0,0,%s' % (stream, tail))
            top = 0
            for i, l in enumerate(lines):
                c = who(l)
                if c and c[1:] in order:
                    name, _code, rest = l.split(',', 2)
                    n = order.index(c[1:]) + 1
                    top = max(top, n)
                    lines[i] = '%s,%d,%s' % (name, n, rest)
            for i, l in enumerate(lines):                      # the bank's highest code has room for them all
                if l.split(',')[0].strip().lower() == 'maxcode':
                    p = l.split(',')
                    if p[1].strip().isdigit() and int(p[1]) <= top:
                        p[1] = str(top + 1)
                        lines[i] = ','.join(p)
            new = nl.join(lines).encode('latin-1')
            if new != d:
                folder = self.de.folder_path(arch)
                os.makedirs(folder, exist_ok=True)
                with open(os.path.join(folder, self._VOICE), 'wb') as fh:
                    fh.write(new)
                wrote.append(arch + '/' + self._VOICE)
        return wrote

    def _voice_stream(self, voice, code, tpl):
        """The stream for a character's voice line from the dialog's choice ({mode: none / template / stream / file,
        stream, file: {name, data}}) -> stream name, '' (none), or None (left as it is)."""
        voice = voice or {}
        mode = voice.get('mode') or 'keep'
        if mode == 'keep':
            return None
        if mode == 'none':
            return ''
        if mode == 'template':
            mine = next((v for v in self.voice_lines() if v['code'] == tpl), None)
            if not mine:
                raise ValueError('%s has no voice line on the select screen to share' % tpl)
            return mine['stream']
        if mode == 'stream':
            s = re.sub(r'\.stm$', '', (voice.get('stream') or '').strip(), flags=re.I)
            if s.lower() not in self._stream_names():
                raise ValueError('streams.dat has no stream named %s' % s)
            return s
        if mode == 'file':
            f = voice.get('file') or {}
            ext = os.path.splitext(f.get('name') or '')[1].lower()
            if ext not in ('.wav', '.stm'):
                raise ValueError('a voice line is a .wav (16-bit PCM) or a .stm')
            got = self.stream_add([{'name': '%s_mp_line%s' % (code, ext), 'data': f.get('data') or ''}])
            return got[0]['name'].rsplit('.', 1)[0]
        raise ValueError('unknown voice line choice: %s' % mode)

    def _photo_upright(self, b64png):
        """The placer's 128x256 upright photo (PNG, base64) as the game's ui_mphoto texture (on its side, 256x128)."""
        import io as _io
        import numpy as np
        import rdrtex
        from PIL import Image
        im = Image.open(_io.BytesIO(base64.b64decode(b64png))).convert('RGBA')
        if im.size != (128, 256):
            im = im.resize((128, 256), Image.LANCZOS)
        stored = np.ascontiguousarray(np.rot90(np.asarray(im), 1))
        return rdrtex.encode(np.ascontiguousarray(stored[::-1]), rdrtex.PA8, mips=False,
                             flags=bytes.fromhex('010001000100'), platform='ps2')

    # ------------------------------------------------------------ Xbox files, imported: made the PS2's
    #
    # The Xbox game keeps its own formats for what the PS2 keeps as .tex / .mod / .rsc + .rnt: .xtex (DXT textures),
    # .xmod (skinned models in model space, not bone relative) and .xrsc + .xrnt (compiled bundles). An Xbox file
    # given to the Studio is converted with the Data Editor's own converter (modconvert.py: the same code that turns
    # a whole mod from one game into the other): textures decoded and encoded in the format the PS2 game's file of
    # that name has, models moved bone relative with the character's skeleton, character bundles' meshes and
    # textures read and built again. A transparency mode (the image converter's) can be given per texture, since the
    # Xbox draws some see-through textures without an alpha (DXT1) that the PS2's carry in their alpha.

    XBOX_TO_PS2 = {'.xtex': '.tex', '.xmod': '.mod', '.xrsc': '.rsc', '.xrnt': '.rnt'}

    def _game_file(self, name):
        """A file of the game by name: the mods folder's swat.dat / front.dat copy, else the working disc's (any
        archive)."""
        for arch in ('swat.dat', 'front.dat'):
            d = self._mod_or_disc(arch, name)
            if d:
                return d
        try:
            g = self.de.s.disc
            for a in g.build_index().get(name.lower(), []):
                return g.archive(a).read(name)
        except Exception:                                       # noqa: BLE001
            pass
        return None

    @staticmethod
    def _alpha_apply(img, opt):
        """The image converter's transparency on an RGBA image (rows either way round): opt {alpha, key, tolerance,
        soft}; 'auto' (or none): left as the converter leaves it."""
        mode = (opt or {}).get('alpha') or 'auto'
        if mode in ('auto', 'keep'):
            return img
        if mode in ('ink_black', 'ink_white'):
            return PB.ink_plain(img, mode == 'ink_white')
        return PB.make_alpha(img, mode, ((opt or {}).get('key') or '000000').lstrip('#'),
                             int((opt or {}).get('tolerance') or 16), int((opt or {}).get('soft') or 48))

    def xbox_to_ps2(self, files, alpha=None, resize=True):
        """The conversion below, through the Workbench's "Converted files" (convcache.py): the same files (and options)
        converted before come back from there instead of being converted again."""
        import hashlib
        import json
        from .convcache import ConvCache
        cache = ConvCache(self.de.workbench, 'To PS2')
        sig = hashlib.sha1(json.dumps([sorted((n.lower(), hashlib.sha1(d).hexdigest()) for n, d in files.items()),
                                       alpha or {}, bool(resize)], sort_keys=True, default=str).encode()).hexdigest()
        key = 'set|%s' % sig
        hit = cache.get(key)
        if hit is not None:
            notes = []
            if '.notes.txt' in hit:
                notes = hit.pop('.notes.txt').decode('utf-8').splitlines()
            return hit, notes
        out, notes = self._xbox_to_ps2(files, alpha, resize)
        if out:
            keep = dict(out)
            keep['.notes.txt'] = '\n'.join(notes).encode('utf-8')
            cache.put(key, keep, '')
            cache.save()
        return out, notes

    def _xbox_to_ps2(self, files, alpha=None, resize=True):
        """{name: bytes} -> ({name: bytes} with the Xbox files made the PS2's, notes). `alpha`: {Xbox file name:
        transparency options} for a texture, or a bundle's textures. `resize`: a texture made the size of the
        PS2 game's own (False keeps its size, for the crop window to decide)."""
        import modconvert as MC
        from rdrformats import tex as TEX
        alpha = {k.lower(): v for k, v in (alpha or {}).items()}
        import tempfile
        notes, out = [], {}
        tmp = tempfile.mkdtemp(prefix='xbox_')
        LEVEL_NOTE = ('is an Xbox level file: the whole level is converted for the PS2 with the level\'s other files '
                      '(lvl_<name>.xrsc in its .dat folder: when it is added, and by every build).')
        try:
            mods_files = {}
            for n, d in files.items():                          # a skeleton given along with the model is used
                if n.lower().endswith('.skel'):
                    p = os.path.join(tmp, os.path.basename(n))
                    open(p, 'wb').write(d)
                    mods_files[os.path.basename(n).lower()] = p
            me = self

            class _Games:
                def find(self, name, prefer=()):
                    d = me._game_file(name)
                    return (None, d) if d else (None, None)
            games = [_Games()]
            low = {n.lower(): n for n in files}
            xexts = set(self.XBOX_TO_PS2) | set(MC.BACK) | {'.xbd', '.xhd'}
            for n, d in files.items():
                stem, ext = os.path.splitext(n)
                ext = ext.lower()
                base = os.path.basename(stem)
                if ext in xexts and self.xbox_original(n, d):
                    pn, pd = self.ps2_counterpart(n)
                    if pd is not None:
                        out[os.path.join(os.path.dirname(n), pn) if os.path.dirname(n) else pn] = pd
                        notes.append('%s is the Xbox game\'s own (unedited) -> %s, the PS2 game\'s own'
                                     % (os.path.basename(n), pn))
                        continue
                    if not base.lower().startswith('lvl_'):
                        notes.append('%s is the Xbox game\'s own (unedited) and the PS2 game has no file for it: '
                                     'left out' % os.path.basename(n))
                        continue
                if ext == '.xtex':
                    like = self._game_file(base + '.tex')
                    t = TEX.read(d)
                    if ((alpha.get(n.lower()) or {}).get('alpha') or 'auto') in ('auto', 'keep') and                             MC.shared_format(t, like if resize else None):
                        # stored the same way on both games (a palette type): as it is, no requantize
                        out[stem + '.tex'] = d
                        notes.append('%s -> %s.tex (the same format on both games: as it is)' % (os.path.basename(n), base))
                        continue
                    img = self._alpha_apply(t.mips[0], alpha.get(n.lower()))
                    kind, mips, flags = t.type, len(t.mips) > 1, t.flags
                    if like and len(like) >= 14:
                        lw, lh, lt, llv = struct.unpack_from('<4H', like)
                        kind, mips, flags = lt, llv > 1, like[8:14]
                        if resize and (lw, lh) != (t.width, t.height):
                            img = PB.resize_to(img, lw, lh)
                    elif kind in (TEX.DXT1, TEX.DXT3, TEX.DXT5, TEX.RGB888):
                        kind = None                             # the PS2 has no DXT: 256 colours (+ alpha if any)
                    if kind not in (TEX.P8, TEX.PA8, TEX.P4, TEX.PA4, TEX.RGBA8888):
                        kind = None
                    if kind in (TEX.P8, TEX.P4) and TEX.has_alpha(img):
                        kind = TEX.PA8 if kind == TEX.P8 else TEX.PA4  # its transparency kept
                    out[stem + '.tex'] = TEX.encode(img, kind, mips=mips, flags=flags, platform='ps2')
                    notes.append('%s -> %s.tex (PS2%s)' % (os.path.basename(n), base, ', the game\'s format' if like else ''))
                elif ext == '.xmod':
                    out[stem + '.mod'] = MC.convert_model(d, base + '.xmod', False, mods_files, games, notes)
                    notes.append('%s -> %s.mod' % (os.path.basename(n), base))
                elif ext == '.xrsc':
                    rnt = low.get((stem + '.xrnt').lower())
                    if base.lower().startswith('lvl_'):
                        notes.append('LEVEL %s %s' % (os.path.basename(n), LEVEL_NOTE))
                        continue
                    if not rnt:
                        notes.append('%s needs its %s.xrnt with it -- left out' % (os.path.basename(n), base))
                        continue
                    textures, meshes = MC.read_bundle(d, files[rnt], True)
                    textures = [(tn, self._alpha_apply(img, alpha.get(n.lower()))) for tn, img in textures]
                    pos = MC.skeleton_for(MC.model_code(base), mods_files, games)
                    if pos is None:
                        notes.append('%s: no skeleton (%s.skel) found: its meshes are not moved bone relative'
                                     % (base, MC.model_code(base)))
                    from rdrformats import ps2rsc
                    built = [(mn, MC.rebase(m, pos, False, version='2.12')) for mn, m in meshes]
                    rsc, rnt_out = ps2rsc.build([(tn, MC.fit_ps2(img)) for tn, img in textures], built)
                    out[stem + '.rsc'], out[stem + '.rnt'] = rsc, rnt_out
                    notes.append('%s + .xrnt -> %s.rsc + .rnt (PS2: %d meshes, %d textures)'
                                 % (os.path.basename(n), base, len(built), len(textures)))
                elif ext == '.xrnt':
                    if base.lower().startswith('lvl_'):
                        if (stem + '.xrsc').lower() not in low:
                            notes.append('LEVEL %s %s' % (os.path.basename(n), LEVEL_NOTE))
                    elif (stem + '.xrsc').lower() not in low:
                        notes.append('%s without its .xrsc -- left out' % os.path.basename(n))
                elif ext in MC.TEXT_FORMATS:
                    # .xfilm, .xhud, .xlsh, .xshadows, .xmmb, .xflares, .xrain: text in the PS2's syntax
                    pn = MC.target_name(base + ext, False)
                    out[stem + os.path.splitext(pn)[1]], how = MC.text_to_ps2(d, ext, self.xbox_clean(n), self._game_file(pn))
                    notes.append('%s -> %s (%s)' % (os.path.basename(n), pn, how))
                elif ext == '.xalpha':
                    # the Xbox renderer's alpha-test settings for a zone (AlphaRef / AlphaBlend / OneBitAlphaRef):
                    # the PS2 game has no such file and no code reading them (2026-11-16: none of the strings on its
                    # disc) -- nothing to convert
                    notes.append('%s: the Xbox\'s alpha-test settings, which the PS2 game does not use -- kept beside '
                                 'the mod, never packed' % os.path.basename(n))
                elif ext in MC.BACK:
                    # .xznes, .xrsc of a level ...: converted with the whole level
                    notes.append('LEVEL %s %s' % (os.path.basename(n), LEVEL_NOTE))
                else:
                    out[n] = d
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return out, notes

    def xbox_payload(self, files, alpha=None, resize=True):
        """[{name, data (base64)}] with any Xbox files made the PS2's -> (list, notes); names keep their folders."""
        if not any(os.path.splitext(f.get('name') or '')[1].lower() in self.XBOX_TO_PS2
                   or os.path.splitext(f.get('name') or '')[1].lower() in __import__('modconvert').BACK
                   for f in files):
            return files, []
        got, notes = self.xbox_to_ps2({f['name']: base64.b64decode(f.get('data') or '') for f in files if f.get('name')},
                                      alpha, resize)
        return [{'name': n, 'data': base64.b64encode(d).decode()} for n, d in got.items()], notes

    def xbox_in_place(self, where, paths, levels=True):
        """Xbox files copied into the mods folder (a file, or under a folder) converted where they lie: the PS2 copy
        written beside the Xbox one, which is kept (the build never packs an Xbox-only file into a PS2 archive). An
        Xbox file that has its PS2 copy beside it already is left alone (that copy may be an edited one)."""
        import modconvert as MC
        xb = tuple(set(self.XBOX_TO_PS2) | set(MC.BACK))       # every Xbox-only extension (a level's too)
        notes = []
        for rel in paths or []:
            top = self.path(where, rel)
            items = [top] if os.path.isfile(top) else [os.path.join(r, f) for r, _d, fs in os.walk(top) for f in fs]
            groups = {}
            for p in items:
                if p.lower().endswith(xb) or p.lower().endswith('.skel'):
                    groups.setdefault(os.path.dirname(p), []).append(p)
            for folder, ps in groups.items():
                have = {f.lower() for f in os.listdir(folder)}

                def done(p):
                    stem, ext = os.path.splitext(os.path.basename(p))
                    return (stem + MC.BACK.get(ext.lower(), self.XBOX_TO_PS2.get(ext.lower(), ''))).lower() in have
                todo = [p for p in ps if p.lower().endswith(xb) and not done(p)
                        and (levels or not os.path.basename(p).lower().startswith('lvl_')
                             or p.lower().endswith(MC.TEXT_FORMATS))]          # a level's text files: on their own
                if not todo:
                    continue
                pick = todo + [p for p in ps if p.lower().endswith('.skel')]
                # a bundle's .xrnt goes with its .xrsc
                for p in ps:
                    if p.lower().endswith('.xrnt') and p not in pick and p[:-5] + '.xrsc' in todo:
                        pick.append(p)
                got, n = self.xbox_to_ps2({os.path.basename(p): open(p, 'rb').read() for p in pick})
                notes += n
                made = []
                for name, data in got.items():
                    if name.lower().endswith('.skel') or name.lower() in have:
                        continue                                # already there
                    with open(os.path.join(folder, name), 'wb') as fh:
                        fh.write(data)
                    made.append(os.path.join(folder, name))
                if made:                                        # the Xbox build: these are copies, not edits
                    from .xboxmode import xbox as _xbox
                    _xbox(self.de).stamp(made)
        return notes

    # ------------------------------------------------------------ the Xbox game, for telling an Xbox mod's own files
    #
    # data_editor/xbox_reference.json {"folders": [...]}: folders of the UNEDITED Xbox game -- its .dat archives, or
    # archives extracted to folders. Indexed once by name and size (cache/xbox_reference_index.json, made again when
    # a folder changes); contents compared only when a name and size match. An imported file that is one of the Xbox
    # game's own unedited files is not a mod: the PS2 game's own copy is used instead (an archive the PS2 disc has:
    # the file is left out; a new archive: the PS2 counterpart goes in). That matters beyond the Xbox extensions: the
    # Xbox banks.dat names its banks as the PS2's do (Snd_117.bd / .hd) in its own audio format.

    def _xref_path(self):
        return os.path.join(self.de.home, 'xbox_reference.json')

    def xbox_ref_folders(self):
        import json
        try:
            return [f for f in json.load(open(self._xref_path(), encoding='utf-8')).get('folders', []) if f]
        except (OSError, ValueError):
            return []

    def xbox_ref_set(self, folders):
        import json
        folders = [str(f).strip().strip('"') for f in (folders or []) if str(f).strip()]
        bad = [f for f in folders if not os.path.isdir(f)]
        if bad:
            raise ValueError('not a folder: ' + ', '.join(bad))
        with open(self._xref_path(), 'w', encoding='utf-8') as fh:
            json.dump({'folders': folders}, fh, indent=1)
        self._xref = None
        return {'folders': folders, 'files': len(self._xref_index())}

    def _xref_index(self):
        """{name (lower case): [(size, source, inner name)]}: source a loose file's path, or a .dat's path with the
        file's name inside it."""
        import json
        idx = getattr(self, '_xref', None)
        if idx is not None:
            return idx
        folders = self.xbox_ref_folders()
        stamp = []
        for f in folders:
            n = t = 0
            for r, _ds, fs in os.walk(f):
                for x in fs:
                    st = os.stat(os.path.join(r, x))
                    n, t = n + 1, max(t, int(st.st_mtime))
            stamp.append([f, n, t])
        cache = os.path.join(self.de.cache, 'xbox_reference_index.json')
        try:
            got = json.load(open(cache, encoding='utf-8'))
            if got.get('stamp') == stamp:
                self._xref = {k: [tuple(e) for e in v] for k, v in got['index'].items()}
                return self._xref
        except (OSError, ValueError):
            pass
        from .dave import Dave, FileBacking
        idx = {}
        for f in folders:
            for r, _ds, fs in os.walk(f):
                for x in fs:
                    p = os.path.join(r, x)
                    if x.lower().endswith('.dat'):
                        try:
                            arc = Dave(FileBacking(p))
                            for e in arc:
                                if not e.name.endswith('/'):
                                    idx.setdefault(e.name.rsplit('/', 1)[-1].lower(), []).append((e.size_full, p, e.name))
                            continue
                        except Exception:                       # noqa: BLE001
                            pass                                # not an archive: a file
                    idx.setdefault(x.lower(), []).append((os.path.getsize(p), p, ''))
        try:
            os.makedirs(self.de.cache, exist_ok=True)
            with open(cache, 'w', encoding='utf-8') as fh:
                json.dump({'stamp': stamp, 'index': idx}, fh)
        except OSError:
            pass
        self._xref = idx
        return idx

    def xbox_original(self, name, data):
        """Is `data` the unedited Xbox game's own file of that name? (False with no reference set.)"""
        from .dave import Dave, FileBacking
        for size, src, inner in self._xref_index().get(os.path.basename(name).lower(), []):
            if size != len(data):
                continue
            try:
                if inner:                               # an archive opened once (reopening it per file took minutes)
                    cache = self.__dict__.setdefault('_xref_daves', {})
                    if src not in cache:
                        cache[src] = Dave(FileBacking(src))
                    raw = cache[src].read(inner)
                else:
                    raw = open(src, 'rb').read()
            except Exception:                                   # noqa: BLE001
                continue
            if raw == data:
                return True
        return False

    def xbox_clean(self, name):
        """The unedited Xbox game's own file of that name (the reference's first of it), or None."""
        from .dave import Dave, FileBacking
        for _size, src, inner in self._xref_index().get(os.path.basename(name).lower(), []):
            try:
                if inner:
                    cache = self.__dict__.setdefault('_xref_daves', {})
                    if src not in cache:
                        cache[src] = Dave(FileBacking(src))
                    return cache[src].read(inner)
                return open(src, 'rb').read()
            except Exception:                                   # noqa: BLE001
                continue
        return None

    XBOX_NAMES = {'berryliumx': 'berrylium', 'damagedx': 'damaged'}     # the Xbox's own names for the PS2's fonts

    def ps2_counterpart(self, name):
        """(PS2 name, bytes) of the PS2 game's file for an Xbox one, or (None, None)."""
        import modconvert as MC
        base = os.path.basename(name)
        cands = [MC.target_name(base, False)]
        stem, ext = os.path.splitext(cands[0])
        for x, p in self.XBOX_NAMES.items():
            if stem.lower().startswith(x):
                cands.append(p + stem[len(x):] + ext)
        for c in cands:
            d = self._game_file(c)
            if d is not None:
                return c, d
        return None, None

    # ------------------------------------------------------------ Xbox levels (any: the game's own, a mod, a new one)
    #
    # A level folder (lvls_m2 / lvls_m4 / lvls_sng) holding an Xbox level (lvl_<name>.xrsc) and no PS2 one -- or an
    # older one than the Xbox file -- is converted where it lies, with no Blender (modconvert.convert_level_xonly:
    # every chunk keeps its number, the meshes and textures encoded for the PS2, the Xbox's padding taken off; zones
    # and bound files given the PS2 field). An archive the PS2 disc does not have (Coliseum, a new map) is packed from
    # its folder alone, so it first gets the PS2 base a new map gets (New .dat: Ranch, or the unused saloon for a story
    # level) -- the files every map shares, the sound banks and sound setup -- with the base's own place left out.
    # The Xbox files stay (the build never packs them).

    def _disc_names(self):
        """{lower case file name: [archives]} of the working disc (cached)."""
        if self.__dict__.get('_disc_names_c') is None:
            try:
                self._disc_names_c = self.de.s.disc.build_index()
            except Exception:                                   # noqa: BLE001
                self._disc_names_c = {}
        return self._disc_names_c

    def level_sounds(self, folder, names, stem, own, log):
        """A converted Xbox level's own sound setup, where the PS2 game can play it. A level's sounds: its
        snd_cfg_<level>.csv (SndBegin N: its bank snd_N, in banks.dat) and its lists snd_N.csv, snd_<N+98>.csv
        (ambience streams), snd_<N+99>.csv (music streams). Read 2026-11-16: every bank an Xbox level carries
        (.xbd / .xhd, its own copies) is in the PS2's banks.dat but snd_5900 / 6000 / 6100 (shernit, ghost, coach),
        which the Xbox's banks.dat has in the PS2's format; every stream the lists name is in the PS2's streams.dat;
        the PS2's swat.dat even keeps Coliseum's whole setup. So: the PS2 disc's copy of each of those files where it
        has one (unless the mod edited it), else the level's own; a missing bank from the Xbox reference's banks.dat
        (into the mods folder's banks.dat); only when the bank is nowhere, the PS2 base map's setup (left as copied).
        own: {lower name: bytes} of the level's sound files as the mod gave them (before the base was laid in).
        -> True when the level keeps its own sounds."""
        cfg = 'snd_cfg_%s.csv' % stem
        disc = self._disc_names()

        def pick(n):
            mine = own.get(n)
            ps2 = self._game_file(n) if n in disc else None
            if mine is not None and (ps2 is None or not self.xbox_original(n, mine)):
                return mine, 'the level\'s own'
            if ps2 is not None:
                return ps2, 'the PS2 disc\'s'
            return None, ''
        data, how = pick(cfg)
        if data is None:
            return False
        m = re.search(r'SndBegin\s*,\s*(\d+)', data.decode('latin-1'))
        if not m:
            return False
        n = int(m.group(1))
        bank = 'snd_%d' % n
        banks_dir = os.path.join(self.de.mods, 'banks.dat')
        have_bank = (bank + '.hd') in disc or os.path.isfile(os.path.join(banks_dir, bank + '.hd'))
        if not have_bank:
            hd, bd = self.xbox_clean(bank + '.hd'), self.xbox_clean(bank + '.bd')
            if hd and bd and hd[:4] == b'IECS':                 # the Xbox's banks.dat: Sony's own format
                os.makedirs(banks_dir, exist_ok=True)
                for ext, d in (('.hd', hd), ('.bd', bd)):
                    with open(os.path.join(banks_dir, bank + ext), 'wb') as fh:
                        fh.write(d)
                log('  its sound bank %s (only the Xbox game has it, in the PS2\'s format) put in banks.dat' % bank)
                have_bank = True
        if not have_bank:
            log('  its sound bank %s is on neither disc (nor the Xbox reference): it plays the PS2 base map\'s '
                'sounds' % bank)
            return False
        with open(os.path.join(folder, names.get(cfg, cfg)), 'wb') as fh:
            fh.write(data)
        missing = []
        for lst in ('snd_%d.csv' % n, 'snd_%d.csv' % (n + 98), 'snd_%d.csv' % (n + 99)):
            d, _h = pick(lst)
            if d is None:
                continue
            with open(os.path.join(folder, names.get(lst, lst)), 'wb') as fh:
                fh.write(d)
            for row in d.decode('latin-1').splitlines():        # streams it names: SAMPLES,Code,...,Stream (col 13)
                c = row.split(',')
                if len(c) > 13 and c[13].strip() == '1' and c[0] and not c[0].startswith('#'):
                    if (c[0].strip().lower() + '.stm') not in disc:
                        missing.append(c[0].strip())
        log('  its own sounds and music kept (%s sound setup, bank %s%s)'
            % (how, bank, '; streams the PS2 disc does not have: %s' % ', '.join(missing[:6]) if missing else ''))
        return True

    _BASES = {'lvls_m2': ('lvls_m2/ranch2.dat', 'lvl_ranch2'), 'lvls_m4': ('lvls_m4/ranch4.dat', 'lvl_ranch4'),
              'lvls_sng': ('lvls_sng/saloon.dat', 'lvl_saloon')}

    def xbox_levels(self, log=None):
        """Convert every Xbox level in the mods folder that has no up-to-date PS2 copy. -> notes."""
        import modconvert as MC
        import ps2build as PB
        log = log or (lambda s: None)
        index, _dats, _holders = self.de.index()
        on_disc = {a.lower() for a in index['archives']}
        van = getattr(self.de.s, 'vanilla', None) or self.de.s.disc
        notes = []
        for group in self._BASES:
            top = os.path.join(self.de.mods, group)
            if not os.path.isdir(top):
                continue
            for arch_dir in sorted(os.listdir(top)):
                if self.de.job.get('cancel') and self.de.job.get('state') == 'running':
                    from .dataeditor import Cancelled
                    raise Cancelled('while converting the Xbox levels')
                folder = os.path.join(top, arch_dir)
                if not (os.path.isdir(folder) and arch_dir.lower().endswith('.dat')):
                    continue
                stem = 'lvl_' + arch_dir[:-4].lower()
                names = {f.lower(): f for f in os.listdir(folder)}
                xr = names.get(stem + '.xrsc')
                if not xr:
                    continue
                pr = names.get(stem + '.rsc')
                if pr and os.path.getmtime(os.path.join(folder, pr)) >= os.path.getmtime(os.path.join(folder, xr)):
                    try:                                        # converted before -- by a writer that was right?
                        pdata = open(os.path.join(folder, pr), 'rb').read()
                        prob = dict(MC.level_problems(pdata))
                        prob.update(MC.level_outdated(open(os.path.join(folder, xr), 'rb').read(), pdata))
                    except Exception:                           # noqa: BLE001
                        prob = {}
                    if not any(prob.values()):
                        continue                                # converted before (or a PS2 level given)
                    log('%s/%s: made by an older converter the game cannot draw (%s): converted again'
                        % (group, arch_dir, ', '.join('%d %s' % (v, k) for k, v in prob.items() if v)))
                arch = '%s/%s' % (group, arch_dir.lower())
                fresh = arch not in on_disc
                log('%s: an Xbox level -- converting it for the PS2 (no Blender needed)...' % arch)
                own_snd = {}                            # the level's sound files as the mod gave them
                for nl0, real in names.items():
                    if nl0.startswith('snd_') and nl0.endswith('.csv'):
                        try:
                            own_snd[nl0] = open(os.path.join(folder, real), 'rb').read()
                        except OSError:
                            pass
                if fresh:
                    bp, bstem = self._BASES[group]
                    try:
                        base = van.archive(bp.upper())
                    except Exception as ex:                     # noqa: BLE001
                        notes.append('%s: the PS2 base %s could not be read (%s): not converted' % (arch, bp, ex))
                        continue
                    pat = re.compile(re.escape(bstem) + r'(?=[_.]|$)', re.I)
                    put = 0
                    for e in base:
                        if e.name.endswith('/'):
                            continue
                        n = e.name.rsplit('/', 1)[-1]
                        low = n.lower()
                        own = bool(pat.match(low)) and low.endswith(PB.PLACE_EXTS)
                        if own:
                            continue                            # the base's place: the Xbox level's goes in
                        new = pat.sub(stem, n)
                        nl = new.lower()
                        stem_x, ext = os.path.splitext(nl)
                        xtwin = (stem_x + MC.EXT.get(ext, '\0')) in names
                        if nl in names and not xtwin and not nl.startswith('snd_cfg_'):
                            have = os.path.join(folder, names[nl])
                            if not self.xbox_original(nl, open(have, 'rb').read()):
                                continue                        # a PS2 file the mod gave (or an edited one): kept
                        data = base.read(e)
                        if MC.is_text(data) and pat.search(data.decode('latin-1')):
                            data = pat.sub(stem, data.decode('latin-1')).encode('latin-1')
                        with open(os.path.join(folder, names.get(nl, new)), 'wb') as fh:
                            fh.write(data)
                        put += 1
                    log('  the PS2 base: %d files from %s (the files every map shares, its sound banks and sound '
                        'setup)' % (put, bp))
                    try:
                        self.level_sounds(folder, {f.lower(): f for f in os.listdir(folder)}, stem, own_snd, log)
                    except Exception as ex:                     # noqa: BLE001
                        log('  its sound setup could not be checked (%s): the PS2 base map\'s is used' % ex)
                like = None                             # the PS2 game's own bundle of the level: its baked light
                if not fresh:
                    try:
                        like = van.archive(arch.upper()).read(stem + '.rsc')
                    except Exception:                   # noqa: BLE001
                        try:
                            like = van.archive(arch).read(stem + '.rsc')
                        except Exception:               # noqa: BLE001
                            like = None
                n0 = len(notes)
                t_conv = time.time() - 2
                MC.place_foreign_level(arch, folder, None, stem, notes, dst=folder, ps2_like=like)
                try:                                            # the Xbox build: copies made from Xbox files
                    from .xboxmode import xbox as _xbox, XBOX_FILES as _XF
                    _xbox(self.de).stamp([os.path.join(folder, f) for f in os.listdir(folder)
                                          if not f.lower().endswith(_XF)
                                          and os.path.getmtime(os.path.join(folder, f)) >= t_conv])
                except Exception:                               # noqa: BLE001
                    pass
                for n in notes[n0:]:
                    log('  ' + n)
        return notes

    def xbox_sweep(self):
        """Every Xbox file in the mods folder, however it got there (Explorer, a Blender export folder, an old
        version), given its PS2 copy beside it -- the Xbox file kept. One with a PS2 copy already is left as it is; a
        level's geometry (lvl_*.xrsc) is not converted here (the build says how). Returns the notes. Cheap when there
        is nothing to do (a walk of the mods folder)."""
        import modconvert as MC
        xb = tuple(MC.BACK)
        root = self.de.mods
        found = []
        for r, _d, fs in os.walk(root):
            have = {f.lower() for f in fs}
            for f in fs:
                lo = f.lower()
                if not lo.endswith(xb) or (lo.startswith('lvl_') and not lo.endswith(MC.TEXT_FORMATS)):
                    continue
                stem, ext = os.path.splitext(lo)
                if (stem + MC.BACK[ext]) in have:
                    continue                                    # converted before (or a PS2 copy given): kept
                if ext == '.xrnt' and stem + '.xrsc' in have:
                    continue                                    # goes with its .xrsc
                found.append(os.path.join(r, f))
        notes = []
        for folder in sorted({os.path.dirname(p) for p in found}):
            if self.de.job.get('cancel') and self.de.job.get('state') == 'running':
                from .dataeditor import Cancelled
                raise Cancelled('while converting the Xbox files')
            r = os.path.relpath(folder, root).replace(os.sep, '/')
            try:
                got = self.xbox_in_place('mods', ['' if r == '.' else r], levels=False)
                notes += [n if n.startswith('LEVEL ') else '%s: %s' % (r, n) for n in got]
            except Exception as ex:                             # noqa: BLE001
                notes.append('%s: its Xbox files could not be converted (%s): left as they are' % (r, ex))
        return notes

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
        if dst_where == 'mods':
            self._xbox_notes = self.xbox_in_place('mods', done)
        return done

    def delete_new(self, folder, permanent=False):
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
        recycle(gone, permanent)
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

    def delete_mod(self, name, permanent=False):
        d = os.path.join(self.saved, _clean_name(name))
        recycle([d], permanent)
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
        xref = self.xbox_ref_folders()
        return {'vanilla': s.vanilla_path, 'working': s.modded_path, 'pcsx2': getattr(s, 'pcsx2', '') or '',
                'home': self.de.home, 'mods': self.de.mods, 'workbench': self.de.workbench, 'saved': self.saved,
                'cache': self.de.cache, 'bases': self.de.bases,
                'pending': s.pending.summary(), 'xbox_reference': xref}


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
    if route == 'de_x_replace_all':                         # Creatures > Replace All Characters
        try:
            return dict(ok=True, **de.replace_all_characters(g('source', ''), dry=bool(g('dry')), kinds=g('kinds')))
        except ValueError as ex:
            return dict(ok=False, error_text=str(ex))
    if route == 'de_x_humanoids':
        return dict(ok=True, characters=[c for c, _r, _k in de.humanoid_characters()],
                    kinds=[dict(key=k, label=l) for k, l, _n in de.REPLACE_KINDS])
    if route == 'de_x_hooks':                               # the Script Hooks tab (rdrstudio/hookmods.py)
        from . import hookmods as _hooks
        return _hooks.listing(de)
    if route in ('de_x_hooks_set', 'de_x_hooks_control'):
        from . import hookmods as _hooks
        try:
            if route == 'de_x_hooks_set':
                return _hooks.set_enabled(de, g('id', ''), g('platform', 'ps2'), bool(g('enabled')))
            return _hooks.set_control(de, g('id', ''), g('combo', ''))
        except ValueError as ex:
            return dict(ok=False, error_text=str(ex))
    if route.startswith('de_x_xbox_'):                     # the Xbox side (rdrstudio/xboxmode.py)
        from .xboxmode import xbox as _xbox
        X = _xbox(de)
        if route == 'de_x_xbox_state':
            return X.state()
        if route == 'de_x_xbox_set':
            try:
                return X.set(g('platform'), g('exe'), g('assets'), g('backup_asked'))
            except ValueError as ex:
                return dict(X.state(), ok=False, error_text=str(ex))
        if route == 'de_x_xbox_play':
            try:
                return X.play()
            except ValueError as ex:
                return dict(ok=False, error_text=str(ex))
        if route == 'de_x_xbox_backup':
            from .dataeditor import Busy
            try:
                return dict(started=de.start('Back up the Xbox game files', X.backup))
            except Busy as ex:
                return dict(ok=False, error_text=str(ex))
        if route == 'de_x_xbox_reset':
            from .dataeditor import Busy
            try:
                return dict(started=de.start('Reset to stock (Xbox)', X.reset, g('clear_mods') is not False))
            except Busy as ex:
                return dict(ok=False, error_text=str(ex))
        if route == 'de_x_xbox_build':
            from .dataeditor import Busy
            try:
                return dict(started=de.start('Build into the Xbox game', X.build))
            except Busy as ex:
                return dict(ok=False, error_text=str(ex))
    if route == 'de_x_new_folder':
        return dict(ok=True, path=m.new_folder(g('where', 'workbench'), g('parent', ''), g('name', '')))
    if route == 'de_x_new_file':
        return dict(ok=True, path=m.new_file(g('where', 'mods'), g('folder', ''), g('name', '')))
    if route == 'de_x_rename':
        return dict(ok=True, path=m.rename(g('where', 'workbench'), g('path', ''), g('name', '')))
    if route == 'de_x_remove':
        return dict(ok=True, count=m.remove(g('where', 'workbench'), g('paths') or [], bool(g('permanent'))))
    if route == 'de_x_upload':
        files, notes = (m.xbox_payload(g('files') or []) if g('where', 'workbench') == 'mods' else (g('files') or [], []))
        return dict(ok=True, added=m.upload(g('where', 'workbench'), g('folder', ''), files), notes=notes)
    if route == 'de_x_paste_text':
        return dict(ok=True, path=m.paste_text(g('folder', ''), g('text', '')))
    if route == 'de_x_transfer':
        return dict(ok=True, done=m.transfer(g('from', 'workbench'), g('paths') or [], g('to_where', 'workbench'),
                                             g('to', ''), bool(g('move'))))
    if route == 'de_x_delete_new':
        return dict(ok=True, deleted=m.delete_new(g('folder', ''), bool(g('permanent'))))
    if route == 'de_x_open':
        return dict(ok=True, path=m.open_in_explorer(g('where', 'workbench'), g('path', '')))
    if route == 'de_x_mod_save':
        return dict(ok=True, name=m.save_mod(g('name', '')))
    if route == 'de_x_mod_load':
        return dict(ok=True, name=m.load_mod(g('name', '')))
    if route == 'de_x_mod_delete':
        return dict(ok=m.delete_mod(g('name', ''), bool(g('permanent'))))
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
    if route == 'de_x_mp_info':
        return dict(ok=True, **m.mp_info(g('code', '')))
    if route == 'de_x_mp_picture':
        return dict(ok=True, wrote=m.mp_picture_set(g('code', ''), g('kind', ''), g('file') or {}))
    if route == 'de_x_mp_add':
        return dict(ok=True, **m.mp_add(g('code', ''), g('name', ''), g('slot', ''), g('template') or None,
                                        g('unlocked', True) is not False, g('portrait') or None, g('photo') or None,
                                        g('mode') or 'replace', g('bounty') or '500', g('lineup') or None,
                                        g('photo_upright') or None, g('order') or 'front',
                                        g('layer') if isinstance(g('layer'), int) else None, g('voice') or None,
                                        g('on_lineup') if g('on_lineup') in (0, 1) else None))
    if route == 'de_x_creature_stats':
        return dict(ok=True, **m.creature_stats(g('code', '')))
    if route == 'de_x_creature_stats_set':
        return dict(ok=True, **m.creature_stats_set(g('code', ''), g('health'), g('swt') or None, g('weapons'),
                                                    g('special') or None, g('brain') or None))
    if route == 'de_x_xbox_ref':
        return dict(ok=True, **m.xbox_ref_set(g('folders') or []))
    if route == 'de_x_sd_options':
        if g('set'):
            return dict(ok=True, options=m.sd_options_set(g('code', ''), g('own_small'), g('alt_small')))
        return dict(ok=True, options=m.sd_options(g('code', '')))
    if route == 'de_x_alt_looks':
        return dict(ok=True, **m.alt_looks(g('code', '')))
    if route == 'de_x_alt_looks_set':
        return dict(ok=True, **m.alt_looks_set(g('code', ''), g('texture') or None, g('pics') or {}))
    if route == 'de_x_sound_sets':
        return dict(ok=True, sets=m.sound_sets())
    if route == 'de_x_mp_sounds':
        return dict(ok=True, **m.mp_sounds(g('code', ''), g('mode') or 'keep', g('source') or None, g('step') or 'keep'))
    if route == 'de_x_mp_place':
        return dict(ok=True, **m.mp_place(g('code', ''), g('spot') or None,
                                          g('layer') if isinstance(g('layer'), int) else None,
                                          g('on_lineup') if g('on_lineup') in (0, 1) else None))
    if route == 'de_x_lineup_info':
        return dict(ok=True, **m.lineup_info())
    if route == 'de_x_lineup_bg':
        if g('reset'):
            return dict(ok=True, **m.lineup_bg_reset(1 if g('lineup') else 0))
        return dict(ok=True, **m.lineup_bg_set(1 if g('lineup') else 0, g('data', ''), g('crop') or None))
    if route == 'de_x_voice_lines':
        return dict(ok=True, lines=m.voice_lines())
    if route == 'de_x_alpha_png':
        return dict(ok=True, png=m.alpha_png(g('data', ''), g('alpha') or {}))
    if route == 'de_x_xbox_convert':
        got, notes = m.xbox_payload(g('files') or [], g('alpha') or None, g('resize', True) is not False)
        return dict(ok=True, files=got, notes=notes)
    if route == 'de_x_mp_candidates':
        return dict(ok=True, codes=m.mp_candidates(g('names') or None), **m.mp_bundles(g('names') or None))
    if route == 'de_x_mp_make_character':
        return dict(ok=True, added=m.mp_make_character(g('code', ''), g('source', '')))
    if route == 'de_x_mp_lineup':
        return dict(ok=True, **m.mp_lineup(g('exclude') or [], 1 if g('lineup') else 0))
    if route == 'de_x_tex_target':
        return dict(ok=True, target=m.tex_target(g('folder', ''), g('name', '')))
    if route == 'de_x_tex_fit':
        return dict(ok=True, **m.tex_fit(g('data', ''), g('crop') or None, g('target') or {}))
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
    if route == 'de_x_transfer_notes':
        n, m._xbox_notes = getattr(m, '_xbox_notes', []), []
        return dict(ok=True, notes=n)
    return None
