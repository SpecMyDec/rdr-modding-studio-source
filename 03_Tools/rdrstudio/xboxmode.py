"""The Studio's Xbox side: one mod, both games.

The Data Editor's mods folder is the mod. On the PS2 the build packs it into the working disc. On the Xbox the game is
the recompiled RDR.exe, which reads loose files from the mods folder beside it: a file there replaces the game's file
of that NAME wherever the game would find it (any archive), or adds one (src/game/mods.cpp). So the Xbox build writes
the mod's files, made the Xbox's, into <exe folder>/mods/RDR Modding Studio/ -- a folder the Studio owns and writes
again on every build (nothing else in the mods folder is touched).

Which bytes go there, file by file:
  - an Xbox file the mod brought in (an imported Xbox .dat's .xrsc / .xtex / .xbd ...) goes as it is, unless its PS2
    copy beside it was changed after the Studio made it (cache/xbox_made.json stamps every PS2 copy the Studio makes
    from an Xbox file) -- then the PS2 copy is the mod and is converted back;
  - a PS2 file the same as the PS2 game's own (or, in an archive the PS2 disc has not, the PS2 base map's copy the
    Studio laid in) is not a mod: the Xbox game uses its own;
  - every other file is converted to the Xbox's (modconvert.convert: textures, models, bundles, levels, zones, text
    merged over the Xbox's own ...);
  - a result the same as the Xbox game's own file is left out.
Matching by name alone has one catch: a file of a name other archives have would be replaced in all of them (each
level's data.plyr: the same in all 64 on the Xbox disc). A level archive with such a file, or a shared one (swat.dat,
front.dat) whose other copies differ, goes as a whole archive instead (the Xbox game's copy with the mod's files in it), under its own
name -- as does an archive the Xbox game has not.

Settings: data_editor/xbox_game.json {"platform": "ps2" | "xbox", "exe": ".../RDR.exe"}. The Xbox game's unedited
files come from the Xbox reference (Settings: the Xbox game), else the recomp's assets folder.
"""
from __future__ import annotations

import filecmp
import struct
import json
import os
import re
import shutil
import subprocess
import time

OUT_NAME = 'RDR Modding Studio'           # the Studio's folder in the recomp's mods folder
XBOX_FILES = ('.xrsc', '.xrnt', '.xznes', '.xtex', '.xmod', '.xfilm', '.xhud', '.xlsh', '.xshadows', '.xmmb',
              '.xflares', '.xrain', '.xalpha', '.xbd', '.xhd')


def default_exe():
    for p in (os.path.join(os.path.expanduser('~'), 'Desktop', 'RDR.exe', 'RDR.exe'),
              os.path.join(os.path.expanduser('~'), 'Desktop', 'RDR', 'RDR.exe')):
        if os.path.isfile(p):
            return p
    return ''


XRSC_LIMIT = 4_200_000  # bytes: the largest shipped Xbox level bundle is 4.06 MB (lvl_shernite)


class Xbox:
    def __init__(self, de):
        from .dataeditor_more import more
        self.de = de
        self.mx = more(de)

    # ------------------------------------------------------------------ settings
    def _path(self):
        return os.path.join(self.de.home, 'xbox_game.json')

    def _read(self):
        try:
            return json.load(open(self._path(), encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def state(self):
        s = self._read()
        exe = s.get('exe') or default_exe()
        ref = self.mx.xbox_ref_folders()
        assets = self.assets_dir(s, exe)
        # the backup of the unedited game files: one set (a backup the user has already, 2026-11-24), else
        # "unedited assets" beside the game files
        backup = s.get('backup') or self.backup_dir(assets)
        return {'platform': s.get('platform') if s.get('platform') in ('ps2', 'xbox') else 'ps2',
                'exe': exe, 'exe_ok': bool(exe and os.path.isfile(exe)),
                'out': self.out_dir(exe) if exe else '',
                'reference': ref[0] if ref else '',
                'assets': assets, 'assets_ok': bool(assets and os.path.isdir(assets)),
                'backup': backup, 'backup_exists': bool(backup and os.path.isdir(backup)),
                'backup_set': bool(s.get('backup')),
                'backup_asked': bool(s.get('backup_asked')),
                'running': self.running()}

    def reset(self, clear_mods=True, save=True, save_name=None, include_new=True):
        """Reset to stock on the Xbox side: the recomp's game files put back as "unedited assets" has them (a file
        that differs copied back, one the backup has not to the Recycle Bin) and the Studio's own folder in the
        recomp's mods folder taken out; with clear_mods the Data Editor's mod files go too, kept as a Saved Mod (as
        the PS2 side's reset does). A Data Editor job. -> {saved, cleared, copied, removed}."""
        from .dataeditor_more import recycle
        log = self.de._log
        st = self.state()
        src, dst = st['backup'], st['assets']
        if not st['backup_exists']:
            raise ValueError('There is no backup of the unedited Xbox game files (%s)' % src)
        if self.running():
            raise ValueError('The Xbox game (RDR.exe) is running: quit it first')
        files = [os.path.relpath(os.path.join(r, f), src) for r, _d, fs in os.walk(src) for f in fs]
        have = {os.path.normcase(r) for r in files}
        log("Reset to stock (Xbox): the recomp's game files from %s" % src)
        copied = 0
        for i, rel in enumerate(files):
            self.de._stop_point('while putting the Xbox game files back')
            a, b = os.path.join(src, rel), os.path.join(dst, rel)
            # the same size is not the same file (an edit can keep it): the contents compared then
            same = os.path.isfile(b) and os.path.getsize(a) == os.path.getsize(b) and filecmp.cmp(a, b, shallow=False)
            if not same:
                os.makedirs(os.path.dirname(b), exist_ok=True)
                shutil.copy2(a, b)
                copied += 1
                log('  put back %s' % rel)
        extra = [os.path.join(r, f) for r, _d, fs in os.walk(dst, followlinks=True) for f in fs
                 if os.path.normcase(os.path.relpath(os.path.join(r, f), dst)) not in have]
        if extra:
            recycle(extra)
            log('  %d file(s) the unedited game has not: to the Recycle Bin' % len(extra))
        out = self.out_dir(st['exe']) if st['exe'] else ''
        if out and os.path.isdir(out):
            shutil.rmtree(out)
            log("  the Studio's files taken out of the recomp's mods folder")
        saved, cleared = None, 0
        if clear_mods:
            saved, cleared = self.mx.clear_for_reset(save, save_name, include_new)
        for rec in (self._record_path(), self._assets_record()):     # nothing of the mod is built in it now
            try:
                os.remove(rec)
            except OSError:
                pass
        log('Done: the Xbox game is as the backup has it (%d file(s) put back).' % copied)
        return {'saved': saved, 'cleared': cleared, 'copied': copied, 'removed': len(extra)}

    @staticmethod
    def backup_dir(assets):
        """Where the unedited Xbox game files are kept: "unedited assets" beside the recomp's assets folder."""
        return os.path.join(os.path.dirname(os.path.normpath(assets)), 'unedited assets') if assets else ''

    def backup(self):
        """The first time the Xbox game is edited: its game files (the recomp's assets) copied, as they are, to
        "unedited assets" beside them -- the Xbox reference too when none is set. A Data Editor job."""
        log = self.de._log
        st = self.state()
        src, dst = st['assets'], st['backup']
        if not st['assets_ok']:
            raise ValueError('The Xbox game files are not set (Settings: Xbox working disc)')
        files = [os.path.join(r, f) for r, _d, fs in os.walk(src, followlinks=True) for f in fs]
        total = sum(os.path.getsize(p) for p in files)
        log('Backing up the Xbox game files: %d files, %.1f GB, %s -> %s' % (len(files), total / 2 ** 30, src, dst))
        done = 0
        for i, p in enumerate(files):
            self.de._stop_point('while backing up the Xbox game files')
            rel = os.path.relpath(p, src)
            out = os.path.join(dst, rel)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            if not (os.path.isfile(out) and os.path.getsize(out) == os.path.getsize(p)):
                shutil.copy2(p, out)
            done += os.path.getsize(p)
            if i % 25 == 0 or i == len(files) - 1:
                log('  %d%%  %s' % (done * 100 // max(total, 1), rel))
        s = self._read()
        s['backup_asked'] = True
        with open(self._path(), 'w', encoding='utf-8') as f:
            json.dump(s, f, indent=1)
        if not self.mx.xbox_ref_folders():
            self.mx.xbox_ref_set([dst])
            log('It is now the unedited Xbox game the Studio compares mods against (Settings: Xbox game).')
        log('Done: the unedited Xbox game files are in %s' % dst)
        return {'backup': dst}

    @staticmethod
    def assets_dir(s, exe):
        """The Xbox side's "working disc": the recomp's game files (its assets folder, set or beside RDR.exe)."""
        if s.get('assets'):
            return s['assets']
        return os.path.join(os.path.dirname(exe), 'assets') if exe else ''

    # ---- the Xbox game's levels the PS2 disc has not (Coliseum): read from the Xbox side, made the PS2's to show
    def xbox_level_archives(self):
        """{'LVLS_M2/COLSEUM2.DAT': its .dat file}: every level archive of the Xbox game, from its working disc (the
        recomp's assets) and the unedited reference."""
        out = {}
        st = self.state()
        self.shipped_levels = set()                    # the ones the unedited Xbox game has (the reference)
        refs = self.mx.xbox_ref_folders()
        # the new level archives the Studio's own Xbox build wrote are the mods folder's levels, built: not listed
        # again from the recomp's files (left there after a PS2 reset, they showed as custom levels still)
        try:
            built = {a.upper() for a, r in json.load(open(self._assets_record(), encoding='utf-8')).items()
                     if r.get('new')}
        except (OSError, ValueError):
            built = set()
        for root in refs + [st['assets']]:
            if not root or not os.path.isdir(root):
                continue
            for group in ('lvls_m2', 'lvls_m4', 'lvls_sng'):
                for d in (os.path.join(root, group), os.path.join(root, group.upper())):
                    if not os.path.isdir(d):
                        continue
                    for f in os.listdir(d):
                        if f.lower().endswith('.dat') and os.path.isfile(os.path.join(d, f)):
                            arc = ('%s/%s' % (group, f)).upper()
                            if arc in built and root not in refs:
                                continue
                            out.setdefault(arc, os.path.join(d, f))
                            if root in refs:
                                self.shipped_levels.add(arc)
        return out

    def converted_level(self, arc, src):
        """An Xbox level archive (its .dat) made the PS2's in the cache, to look at: its files extracted, the level
        converted where they lie (modconvert.place_foreign_level, as an import does). -> the folder."""
        import modconvert as MC
        from . import dave as D
        group, fname = arc.lower().split('/', 1)
        folder = os.path.join(self.de.cache, 'xbox_levels', group, fname)
        stamp = os.path.join(folder, '.from')
        key = '%s %d %d' % (src, os.path.getsize(src), int(os.path.getmtime(src)))
        try:
            if open(stamp, encoding='utf-8').read() == key:
                return folder
        except OSError:
            pass
        shutil.rmtree(folder, ignore_errors=True)
        os.makedirs(folder, exist_ok=True)
        a = D.Dave(D.FileBacking(src))
        for e in a:
            if e.name.endswith('/'):
                continue
            p = os.path.join(folder, *e.name.split('/')[-1:])
            with open(p, 'wb') as f:
                f.write(a.read(e))
        stem = 'lvl_' + fname[:-4]
        MC.place_foreign_level(arc.lower(), folder, None, stem, [], dst=folder)
        with open(stamp, 'w', encoding='utf-8') as f:
            f.write(key)
        return folder

    def converted_folder_level(self, arc, folder):
        """A new level in the mods folder that has only its Xbox bundle (lvl_<name>.xrsc, no .rsc): its files copied
        into the cache and the level made the PS2's there, to look at in the 3D level editor -- the mods folder itself
        is left as it is (nothing converted on the way in). Made again when a file of the folder changes. -> the
        cache folder."""
        import modconvert as MC
        group, fname = arc.lower().split('/', 1)
        out = os.path.join(self.de.cache, 'xbox_levels', 'mods_' + group, fname)
        names = sorted(f for f in os.listdir(folder) if os.path.isfile(os.path.join(folder, f)))
        key = '|'.join('%s:%d:%d' % (f, os.path.getsize(os.path.join(folder, f)),
                                     int(os.path.getmtime(os.path.join(folder, f)))) for f in names)
        stamp = os.path.join(out, '.from')
        try:
            if open(stamp, encoding='utf-8').read() == key:
                return out
        except OSError:
            pass
        shutil.rmtree(out, ignore_errors=True)
        os.makedirs(out, exist_ok=True)
        for f in names:
            shutil.copyfile(os.path.join(folder, f), os.path.join(out, f))
        stem = 'lvl_' + fname[:-4]
        MC.place_foreign_level(arc.lower(), out, None, stem, [], dst=out)
        with open(stamp, 'w', encoding='utf-8') as f:
            f.write(key)
        return out

    def level_files(self, arc, ps2_files, xbox):
        """A level's own files for Export whole level as level files: ps2_files {name: bytes} (the archive's files of
        the level, as the PS2 disc or the mods folder has them) as they are, or made the Xbox's. An Xbox level the PS2
        disc has not (Coliseum) is the Xbox game's own: its mods folder Xbox files, else its .dat on the Xbox side."""
        if not xbox:
            return dict(ps2_files)
        import modconvert as MC
        group, fname = arc.lower().split('/', 1)
        stem = 'lvl_' + fname[:-4]
        out = {}
        native = self.xbox_level_archives()
        if arc.upper() in getattr(self, 'shipped_levels', set()):
            from .dave import Dave, FileBacking
            mods = os.path.join(self.de.mods, group, fname)
            mine = {f.lower(): os.path.join(mods, f) for f in os.listdir(mods)} if os.path.isdir(mods) else {}
            a = Dave(FileBacking(native[arc.upper()]))
            for e in a:
                n = e.name.rsplit('/', 1)[-1]
                if n.lower().startswith(stem):
                    out[n] = open(mine[n.lower()], 'rb').read() if n.lower() in mine else a.read(e)
            for n, p in mine.items():                       # the mod's own Xbox files the game has not
                if n.startswith(stem) and os.path.splitext(n)[1] in XBOX_FILES and n not in {k.lower() for k in out}:
                    out[os.path.basename(p)] = open(p, 'rb').read()
            return out
        # a level of both games: converted as the Xbox build converts (the PS2 and the Xbox game read for it)
        st = self.state()
        xroot = st['reference'] or st['assets']
        ps2_iso = getattr(self.de.s, 'vanilla_path', None) or self.de.s.modded_path
        src = MC.Game(ps2_iso, os.path.join(self.de.cache, 'xbox_build_ps2'), False)
        tgt = MC.Game(xroot, os.path.join(self.de.cache, 'xbox_build_xbox'), True)
        stage = os.path.join(self.de.cache, 'level_export_stage')
        conv = os.path.join(self.de.cache, 'level_export_out')
        for d in (stage, conv):
            shutil.rmtree(d, ignore_errors=True)
        folder = os.path.join(stage, group, fname)
        os.makedirs(folder, exist_ok=True)
        for n, b in ps2_files.items():
            with open(os.path.join(folder, n), 'wb') as f:
                f.write(b)
        _counts, notes = MC.convert(stage, conv, src, tgt, True, log=lambda *_a: None)
        for root, _d, fs in os.walk(conv):
            for f in fs:
                out[f] = open(os.path.join(root, f), 'rb').read()
        if notes:
            out['conversion notes.txt'] = '\r\n'.join(notes).encode('utf-8')
        return out

    @staticmethod
    def looks_like_game(folder):
        """Is it the Xbox game's files (default.xbe, or its level folders)?"""
        try:
            have = {f.lower() for f in os.listdir(folder)}
        except OSError:
            return False
        return 'default.xbe' in have or ('lvls_m2' in have and 'swat.dat' in have)

    def set(self, platform=None, exe=None, assets=None, backup_asked=None, backup=None):
        s = self._read()
        if platform in ('ps2', 'xbox'):
            s['platform'] = platform
        if exe is not None:
            exe = str(exe).strip().strip('"')
            if exe and os.path.isdir(exe):
                exe = os.path.join(exe, 'RDR.exe')
            if exe and not os.path.isfile(exe):
                raise ValueError('There is no RDR.exe at %s' % exe)
            s['exe'] = exe
        if assets is not None:
            assets = str(assets).strip().strip('"')
            if assets and not os.path.isdir(assets):
                raise ValueError('There is no folder %s' % assets)
            s['assets'] = assets
        if backup_asked is not None:
            s['backup_asked'] = bool(backup_asked)
        if backup is not None:
            # a backup of the unedited game files the user has already: Reset to stock (Xbox) puts the game back from
            # it ('' goes back to "unedited assets" beside the game files)
            backup = str(backup).strip().strip('"')
            if backup:
                if not os.path.isdir(backup):
                    raise ValueError('There is no folder %s' % backup)
                if not self.looks_like_game(backup):
                    raise ValueError('%s does not look like the Xbox game files (no default.xbe, no lvls_m2 and '
                                     'swat.dat): pick the folder that holds them' % backup)
                if os.path.normcase(os.path.abspath(backup)) == os.path.normcase(os.path.abspath(self.assets_dir(s, s.get('exe') or default_exe()) or '?')):
                    raise ValueError('That is the game files folder itself (the one the Studio builds into): a backup '
                                     'has to be a separate copy')
                s['backup'] = backup
                s['backup_asked'] = True
            else:
                s.pop('backup', None)
        os.makedirs(self.de.home, exist_ok=True)
        with open(self._path(), 'w', encoding='utf-8') as f:
            json.dump(s, f, indent=1)
        return self.state()

    @staticmethod
    def out_dir(exe):
        return os.path.join(os.path.dirname(exe), 'mods', OUT_NAME)

    @staticmethod
    def running():
        try:
            r = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq RDR.exe', '/NH'], capture_output=True, text=True,
                               timeout=10, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            return 'rdr.exe' in r.stdout.lower()
        except Exception:                                       # noqa: BLE001
            return False

    def play(self):
        st = self.state()
        if not st['exe_ok']:
            raise ValueError('No Xbox game set: point the Studio at the recomp\'s RDR.exe (the Xbox switch, bottom right)')
        subprocess.Popen([st['exe']], cwd=os.path.dirname(st['exe']))
        return {'ok': True, 'launched': st['exe']}

    # ------------------------------------------------------------------ which PS2 copies the Studio made
    def _made_path(self):
        return os.path.join(self.de.cache, 'xbox_made.json')

    def made(self):
        try:
            return json.load(open(self._made_path(), encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def stamp(self, paths):
        """PS2 copies the Studio just made from Xbox files: their size and time, so the Xbox build can tell a copy
        changed since (the mod's edit, to convert back) from one as made (the Xbox file goes as it is)."""
        m = self.made()
        for p in paths:
            try:
                st = os.stat(p)
            except OSError:
                continue
            m[os.path.relpath(p, self.de.mods).replace(os.sep, '/').lower()] = [st.st_size, st.st_mtime_ns]
        os.makedirs(self.de.cache, exist_ok=True)
        with open(self._made_path(), 'w', encoding='utf-8') as f:
            json.dump(m, f)

    def changed_since_made(self, ps2_path, xbox_path, made):
        st = os.stat(ps2_path)
        if st.st_mtime <= os.stat(xbox_path).st_mtime + 1:
            return False        # the Xbox file is the newer (or both written together: the Blender add-on's pair)
        rec = made.get(os.path.relpath(ps2_path, self.de.mods).replace(os.sep, '/').lower())
        if rec:
            if rec[1] < os.stat(xbox_path).st_mtime_ns:
                # the stamp is older than this Xbox file: it was a copy of an EARLIER Xbox file, and the PS2 file there
                # now is the mod's own (dropped in after the Xbox one) -- both kept, the Xbox one for the Xbox. A stale
                # stamp had the mod's PS2 level converted over its own .xrsc (2026-11-17)
                return False
            return [st.st_size, st.st_mtime_ns] != rec
        # made before the stamps: a copy written within half an hour of its Xbox file counts as made from it
        return st.st_mtime - os.stat(xbox_path).st_mtime > 1800

    # ------------------------------------------------------------------ the build
    def build(self):
        import modconvert as MC
        import ps2build as PB
        from . import dave as D
        log = self.de._log
        st = self.state()
        if not st['exe_ok']:
            raise ValueError('No Xbox game set: point the Studio at the recomp\'s RDR.exe (the Xbox switch, bottom right)')
        if st['running']:
            raise ValueError('The Xbox game (RDR.exe) is running: quit it first (it reads the mods folder)')
        # the build writes into the recomp's game files (assets), as the PS2 build writes the working disc: the
        # unedited ones are read from their backup ("unedited assets", made now the first time) or the reference
        if not st['assets_ok']:
            raise ValueError('The Xbox game files are not set (Settings: Xbox working disc)')
        if not st['backup_exists']:
            log('First Xbox build: the unedited Xbox game files are backed up first ("unedited assets")')
            self.backup()
            st = self.state()
        xroot = st['reference'] or st['backup']
        ps2_iso = getattr(self.de.s, 'vanilla_path', None) or self.de.s.modded_path
        t0 = time.time()
        # the Studio's edits that did not fit their place on the working disc wait in a queue for a rebuild: they go
        # on the disc first, so the comparison below (the disc's edits are the mod too) has them
        pend = getattr(self.de.s, 'pending', None)
        try:
            queued = pend.archives() if pend is not None else []
        except Exception:                                       # noqa: BLE001
            queued = []
        if queued and hasattr(self.de.s, 'rebuild_pending'):
            log('Putting %d .dat file(s) with queued Studio edits on the working disc first: %s'
                % (len(queued), ', '.join(queued)))
            self.de.s.rebuild_pending()
        try:                                                    # a level export under another name: its .dat's
            self.mx.adopt_level_exports(log)
        except Exception as ex:                                 # noqa: BLE001
            log('  (the level exports in the mods folder could not be checked: %s)' % ex)
        log('Reading the two games (the first time indexes them)...')
        src = MC.Game(ps2_iso, os.path.join(self.de.cache, 'xbox_build_ps2'), False)
        tgt = MC.Game(xroot, os.path.join(self.de.cache, 'xbox_build_xbox'), True)
        self.de._stop_point('before converting')
        mods = self.de.mods
        fresh_ps2 = PB.new_archives(mods, src.archives)
        loose, targeted, every, master = PB.mod_files(mods, list(src.archives) + list(fresh_ps2))
        made = self.made()
        out = {}          # archive ('' for files every archive takes by name) -> {inner path: bytes}
        convert = []      # (archive or '', inner, path): PS2 files to convert
        kept_x = 0
        pck_left = []     # Flash screens (.pck): the Xbox's are .xck, not converted yet
        for arch, items in sorted(targeted.items()):
            arch_l = arch.lower()
            in_ps2 = arch_l in {a.lower() for a in src.archives}
            byname = {inner.lower(): path for inner, path in items}
            used = set()
            for inner, path in items:                           # the mod's Xbox files
                low = inner.lower()
                ext = os.path.splitext(low)[1]
                if ext not in XBOX_FILES:
                    continue
                sib = MC.target_name(low, False)
                if sib != low and sib in byname and self.changed_since_made(byname[sib], path, made):
                    continue                                    # its PS2 copy was edited: that is the mod now
                if ext == '.xznes' and sib in byname and os.path.getmtime(byname[sib]) > os.path.getmtime(path) + 2:
                    log('  %s: older than its .znes (the editor\'s edits are there) -- made again from it' % inner)
                    continue
                if ext == '.xrsc' and low.rsplit('/', 1)[-1].startswith('lvl_') and sib in byname and \
                        self._level_pair_stale(open(byname[sib], 'rb').read(), open(path, 'rb').read()):
                    # a level bundle pair the editor keeps together that has come apart: the PS2 one is converted
                    log('  %s: its .xrsc no longer matches its .rsc chunk for chunk -- the .rsc converted instead'
                        % inner)
                    continue
                out.setdefault(arch, {})[inner] = open(path, 'rb').read()
                used.add(sib)
                kept_x += 1
            for inner, path in items:                           # the PS2 ones
                low = inner.lower()
                if os.path.splitext(low)[1] in XBOX_FILES or low in used:
                    continue
                data = open(path, 'rb').read()
                if self._ps2_own(arch, inner, data, src, in_ps2):
                    continue
                if low.endswith('.pck'):
                    # a Flash screen: the Xbox's are .xck -- the same objects at Xbox addresses, its pictures in
                    # the Xbox's formats -- not converted yet; written as it is the Xbox game would never read it
                    pck_left.append(inner)
                    continue
                convert.append((arch, inner, path))
        for group in (master, loose):                           # by name, into every archive that has it
            for low, path in group.items():
                data = open(path, 'rb').read()
                if os.path.splitext(low)[1] in XBOX_FILES:
                    out.setdefault('', {})[low] = data
                    continue
                _a, clean = src.find(low)
                if clean is not None and clean == data:
                    continue
                convert.append(('', low, path))
        if os.path.isfile(os.path.join(mods, PB.MODES_FILE)) and master:
            modes = open(os.path.join(mods, PB.MODES_FILE), encoding='utf-8').read()
            if '\tadd' in modes:
                log('Master files set to "Add to all" go only where the Xbox game has a file of that name: the Xbox '
                    'game reads loose files by name.')
        if pck_left:
            log('Not on the Xbox yet: %d Flash screen(s) (%s) -- the Xbox game reads .xck, its own build of them, '
                'which the Studio does not convert yet' % (len(pck_left), ', '.join(sorted(set(pck_left))[:6])))
        # the Studio's other editors write the working disc itself: its files that differ from the stock disc are
        # the mod too (where the mods folder has no file of theirs -- that is the Data Editor's, built onto it)
        disc_rows = self._disc_edits(targeted, master, loose, log)
        log('%d Xbox file(s) of the mod as they are; %d file(s) to convert for the Xbox (%d of them edited on the '
            'working disc)' % (kept_x, len(convert) + len(disc_rows), len(disc_rows)))
        # each file made the Xbox's, in this order:
        #   1. the Xbox game's own: a PS2 file the same as the PS2 game's own file of its name (in ANY archive -- a
        #      character put into a level that had not got it) is the Xbox game's file of that name, as it is. A
        #      conversion of the PS2 one would lose what only the Xbox's has (pgj's face morphs, MPH0: converted, the
        #      face animation crashed the level, 2026-11-17)
        #   2. an earlier conversion of the same input, kept in the Workbench's "Converted files" (convcache.py)
        #   3. converted now, archive by archive, and kept there for next time
        from .convcache import ConvCache
        cache = ConvCache(self.de.workbench, 'To Xbox')
        # the files the mod gives for the Xbox itself: never replaced by a conversion
        given_x = {(a.lower(), i.lower().rsplit('/', 1)[-1]) for a, files in out.items() for i in files}
        # a NEW level archive whose own level bundle the mod gives for the Xbox (lvl_<name>.xrsc, the Blender add-on's
        # export for both games): its files are not converted -- the text ones (the same on both games) go as they
        # are, the PS2's binary ones are left out, and the Xbox game's own base fills in the rest
        own_levels = set()
        for a, files in out.items():
            name = a.rsplit('/', 1)[-1]
            if a and a not in tgt.archives and name.endswith('.dat') and \
                    any(i.lower().rsplit('/', 1)[-1] == 'lvl_%s.xrsc' % name[:-4] for i in files):
                own_levels.add(a)
        groups = {}                                         # archive ('' = by name) -> [(inner, bytes)]
        for arch, inner, data in disc_rows:
            groups.setdefault(arch, []).append((inner, data))
        for arch, inner, path in convert:
            groups.setdefault(arch, []).append((inner, open(path, 'rb').read()))
        for a in list(groups):
            if a in own_levels:
                kept_text = 0
                for inner, data in groups.pop(a):
                    low_n = inner.lower().rsplit('/', 1)[-1]
                    if low_n.endswith('.znes') and MC.is_text(data):
                        # the editor's zone file carries both games' numbers (resourceIndex the PS2 bundle's,
                        # resourceIndexXbox the .xrsc's): the Xbox reads its .xznes, made here with the Xbox numbers
                        # unless the mod gives its own (the Blender add-on's) -- 2026-11-18
                        xn = low_n[:-5] + '.xznes'
                        if (a, xn) not in given_x:
                            def _x(m):
                                n = int(m[6]) if int(m[6]) > 0 else int(m[4])
                                return '%s0%s%d' % (m[1], m[5], n)
                            out.setdefault(a, {})[inner[:-5] + '.xznes'] = MC.ZONE_RE.sub(
                                _x, data.decode('latin-1')).encode('latin-1')
                            kept_text += 1
                        continue
                    if MC.is_text(data) and (a, low_n) not in given_x:
                        out.setdefault(a, {})[inner] = data
                        kept_text += 1
                log('  %s: the mod\'s own Xbox level -- used as it is, nothing converted (%d text file(s) as they are)'
                    % (a, kept_text))
        own_n, converted_n = 0, 0
        for arch, items in sorted(groups.items()):
            self.de._stop_point('before converting')
            # a level's files convert together (zones, bundles, collision): their key covers all of them
            lvl = sorted((inner.lower(), cache.key(inner, data)) for inner, data in items
                         if os.path.basename(inner).lower().startswith('lvl_'))
            lvl_ctx = __import__('hashlib').sha1(repr(lvl).encode()).hexdigest() if lvl else ''
            todo = []
            for inner, data in items:
                name = os.path.basename(inner).lower()
                folder = inner[:len(inner) - len(os.path.basename(inner))]
                _a, clean = src.find(name)
                if clean is not None and clean == data:
                    xname = MC.target_name(name, True)
                    _a2, xown = tgt.find(xname)
                    if xown is not None:
                        out.setdefault(arch, {})[folder + xname] = xown
                        own_n += 1
                        continue
                ctx = lvl_ctx if name.startswith('lvl_') else ''
                hit = cache.get(cache.key(inner, data, ctx))
                if hit is not None:
                    for oname, obytes in hit.items():
                        if (arch.lower(), oname.lower().rsplit('/', 1)[-1]) not in given_x:
                            out.setdefault(arch, {})[oname] = obytes
                    continue
                todo.append((inner, data, ctx))
            if not todo:
                continue
            stage = os.path.join(self.de.cache, 'xbox_build_stage')
            conv = os.path.join(self.de.cache, 'xbox_build_out')
            for d in (stage, conv):
                shutil.rmtree(d, ignore_errors=True)
            top = arch.split('/') if arch else ['master']
            for inner, data, _c in todo:
                dst = os.path.join(stage, *top, *inner.split('/'))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, 'wb') as f:
                    f.write(data)
            extra = {a: {} for a in tgt.archives if a not in src.archives}
            saved = src.archives
            src.archives = dict(saved, **extra)
            try:
                counts, notes = MC.convert(stage, conv, src, tgt, True, log=log)
            finally:
                src.archives = saved
            for n in notes:
                log('  ' + n)
            made = {}
            for root, _d, fs in os.walk(conv):
                for f in fs:
                    rel = os.path.relpath(os.path.join(root, f), conv).replace(os.sep, '/')
                    if rel.lower() == PB.MODES_FILE:
                        continue
                    a2, inner = self._split(rel, tgt, src)
                    b = open(os.path.join(root, f), 'rb').read()
                    made[inner.lower()] = (inner, b)
                    if (a2.lower(), inner.lower().rsplit('/', 1)[-1]) in given_x:
                        continue                                # the mod's own Xbox file stays
                    out.setdefault(a2, {})[inner] = b
            # each input's outputs kept: its name made the Xbox's (.rsc -> .xrsc), its names file with a bundle
            for inner, data, ctx in todo:
                low = inner.lower()
                want = {MC.target_name(low, True), low}
                if low.endswith('.rsc'):
                    want.add(MC.target_name(low[:-4] + '.rnt', True))
                got = {made[w][0]: made[w][1] for w in want if w in made}
                if got:
                    cache.put(cache.key(inner, data, ctx), got, arch)
            converted_n += len(todo)
        cache.save()
        if groups:
            log('  %d file(s) are the Xbox game\'s own, %d from earlier conversions (Workbench > Converted files), %d '
                'converted now' % (own_n, cache.hits, converted_n))
        self.de._stop_point('before writing the Xbox game files')
        self._name_alternates(out, log)
        # the Xbox game's own already: left out
        dropped = 0
        for arch in list(out):
            for inner in list(out[arch]):
                own = tgt.get(arch, inner) if arch else tgt.find(inner.rsplit('/', 1)[-1])[1]
                if own is not None and own == out[arch][inner]:
                    del out[arch][inner]
                    dropped += 1
            if not out[arch]:
                del out[arch]
        if dropped:
            log('%d file(s) the same as the Xbox game\'s own left out' % dropped)
        # files given by name: into every Xbox archive that has a file of the name (as the PS2 build's loose and
        # Master files); one no archive has stays loose in the mods folder, where the recomp finds it by name
        per = {}
        leftover = {}
        for arch, files in out.items():
            if arch:
                # at the archive's top, as the PS2 build puts a .dat folder's subfolders' files
                per.setdefault(arch, {}).update({k.lower().rsplit('/', 1)[-1]: v for k, v in files.items()})
        for inner, b in out.get('', {}).items():
            name = inner.rsplit('/', 1)[-1].lower()
            holders = [a for a in tgt.holders.get(name, ()) if a in tgt.archives]
            if not holders:
                leftover[name] = b
                continue
            for a in holders:
                per.setdefault(a, {}).setdefault(name, b)
        self._complete_new_levels(per, tgt, log)
        self._bnds_for_xbox(per, log)
        for arch, files in per.items():                         # view records vs AI rooms (crash on load)
            try:
                for n in PB.fit_level_counts(files):
                    log('  ' + n)
            except Exception as ex:                             # noqa: BLE001
                log('  %s: AI rooms not checked against its view data (%s)' % (arch, ex))
        built = self._write_assets(per, tgt, st, log)
        # the mods folder keeps only the Script Hooks switches and files no archive has (the game's files are in
        # assets now: an old loose copy there would hide them)
        dest = self.out_dir(st['exe'])
        if os.path.isdir(dest):
            shutil.rmtree(dest)
        os.makedirs(dest, exist_ok=True)
        from . import hookmods as _hooks                       # the recomp's script hooks switched off (Script Hooks)
        _hooks.write_xbox(self.de, dest)
        for name, b in leftover.items():
            with open(os.path.join(dest, name), 'wb') as f:
                f.write(b)
        self.record_build(every)
        log('Done in %d s: %d archive(s) rebuilt into the Xbox game files (%s), %d unchanged, %d put back as '
            'unedited%s. Play starts the Xbox game with them.'
            % (time.time() - t0, len(built['written']), st['assets'], built['same'], len(built['restored']),
               (', %d loose file(s) in %s' % (len(leftover), dest)) if leftover else ''))
        return {'archives': built['written'], 'restored': built['restored'], 'out': st['assets']}

    # a new level archive's base on the Xbox: the Xbox game's own files, as New .dat (and complete_new_level) give
    # the PS2 one -- a Showdown half: the files every Xbox Showdown map of its player count shares and Ranch's other
    # ones; a single player level: the saloon's. Their geometry, zones and collision are the level's own.
    _XBOX_BASES = {'lvls_m2': ('lvls_m2/ranch2.dat', 'lvl_ranch2'), 'lvls_m4': ('lvls_m4/ranch4.dat', 'lvl_ranch4'),
                   'lvls_sng': ('lvls_sng/saloon.dat', 'lvl_saloon')}

    def make_level_xrsc(self, folder, stem, group, log=print):
        """lvl_<x>.xrsc beside a new level's lvl_<x>.rsc (New .dat without its template's geometry): the PS2 bundle
        made the Xbox's chunk by chunk, every chunk at its number (modconvert.convert_level_ps2only, as the Xbox build
        converts a new level), the physics materials the Xbox game's own base level's. From then on the editor writes
        both (a model, collision, a move), the PS2 build packs the .rsc and the Xbox build the .xrsc (2026-11-18).
        -> the path, or None."""
        import modconvert as MC
        from rdrformats import rsc as _rsc
        src = os.path.join(folder, stem + '.rsc')
        if not os.path.isfile(src):
            return None
        pmt = None
        st = self.state()
        xroot = st['reference'] or st['assets']
        if xroot and os.path.isdir(xroot) and group in self._XBOX_BASES:
            bp, bstem = self._XBOX_BASES[group]
            try:
                tgt = MC.Game(xroot, os.path.join(self.de.cache, 'xbox_build_xbox'), True)
                raw = tgt.get(bp, bstem + '.xrsc')
                if raw:
                    pmt = [(c.tag, c.data) for c in _rsc.read_chunks(raw) if c.tag == 'PMT0']
            except Exception as ex:                             # noqa: BLE001
                log('  (the Xbox game\'s physics materials could not be read: %s -- the PS2 ones padded)' % ex)
        notes = []
        data = MC.convert_level_ps2only(open(src, 'rb').read(), notes, stem, pmt)
        out = os.path.join(folder, stem + '.xrsc')
        with open(out, 'wb') as f:
            f.write(data)
        os.utime(out, None)
        log('  %s.xrsc made beside %s.rsc (the Xbox game\'s level bundle, every chunk at the PS2 one\'s number)'
            % (stem, stem))
        return out

    @staticmethod
    def _level_pair_stale(ps2_data, xbox_data):
        """Does a level's .xrsc no longer follow its .rsc (an edit that reached only one)? The two hold the same
        chunks in the same order (the editor writes both); textures and meshes are each game's encoding."""
        from rdrformats import rsc as _rsc
        try:
            a = [c.tag for c in _rsc.read_chunks(ps2_data)]
            b = [c.tag for c in _rsc.read_chunks(xbox_data)]
        except Exception:                                       # noqa: BLE001
            return True
        return a != b

    @staticmethod
    def _bnds_for_xbox(per, log):
        """A bound file (.bnds) carries both games' chunk numbers -- resourceIndex the PS2 bundle's, resourceIndexXbox
        the Xbox one's (the Blender add-on's export for both games) -- but the Xbox game reads resourceIndex too (the
        shipped levels have both the same): packed for the Xbox with its own numbers in resourceIndex. With the PS2's
        there it loaded the wrong chunk ("datResourceHeader::Translate - Expected BND0", 2026-11-17)."""
        node = re.compile(r'(node\s+\S+\s*\{.*?)(resourceIndex\s+)(-?\d+)(.*?resourceIndexXbox\s+)(-?\d+)', re.S)
        from rdrformats import rsc as _rsc
        fixed = 0
        for arch, files in per.items():
            # the chunk tags of the level bundles going into this archive: each bound's number is the one that
            # names a BND0 there (the mod's own .xrsc has its Xbox numbers; one converted from the PS2 bundle keeps
            # the PS2's -- the game read past the end of a converted one and crashed, "Expected BND0")
            tags = []
            for name, data in files.items():
                if name.lower().endswith('.xrsc') and name.lower().startswith('lvl_'):
                    try:
                        tags.append([c.tag for c in _rsc.read_chunks(data)])
                    except Exception:                       # noqa: BLE001
                        pass

            def is_bound(num):
                return any(0 < num <= len(t) and t[num - 1] == 'BND0' for t in tags)

            def pick(m):
                ps2_n, x_n = int(m.group(3)), int(m.group(5))
                n = x_n if is_bound(x_n) or not tags else ps2_n if is_bound(ps2_n) else x_n
                return m.group(1) + m.group(2) + str(n) + m.group(4) + str(n)
            for name in list(files):
                if not name.lower().endswith('.bnds'):
                    continue
                text = files[name].decode('latin-1')
                new = node.sub(pick, text)
                if new != text:
                    files[name] = new.encode('latin-1')
                    fixed += 1
        if fixed:
            log('  %d bound file(s) given the chunk numbers of the level bundle that goes with them' % fixed)

    def _complete_new_levels(self, per, tgt, log):
        import ps2build as PB
        from . import dave as D
        cache = {}

        def names_of(arch):
            if arch not in cache:
                data = tgt.data(arch)
                cache[arch] = (data, [e.name for e in D.Dave(_Bytes(data), arch).entries if not e.name.endswith('/')])
            return cache[arch]
        for arch, files in per.items():
            group, _, name = arch.rpartition('/')
            if arch in tgt.archives or group not in self._XBOX_BASES or not name.endswith('.dat'):
                continue
            stem = 'lvl_' + name[:-4]
            if not any(k == stem + '.xrsc' for k in files):
                continue
            bp, bstem = self._XBOX_BASES[group]
            if bp not in tgt.archives:
                continue
            data, base_names = names_of(bp)
            take = set(n.lower() for n in base_names)
            if group != 'lvls_sng':
                sets = [set(n.lower() for n in names_of(a)[1]) for a in tgt.archives
                        if a.startswith(group + '/') and a != arch]
                if sets:
                    take |= set.intersection(*sets)
            pat = re.compile(r'(?<![a-z0-9])' + re.escape(bstem) + r'(?=[_.]|$)', re.I)
            by_low = {n.lower(): n for n in base_names}
            lvl_b = next((v for k, v in files.items() if k.lower() == stem + '.lvl'), b'')
            listed = {ln.strip().lower() for ln in lvl_b.decode('latin-1').splitlines() if ln.strip()}
            added = 0
            for low in sorted(take):
                own = bool(pat.search(low))
                if own and low.startswith(bstem) and (low == bstem + '.xrsc' or
                                                      low.endswith(('.znes', '.xznes', '.bnds'))):
                    continue                            # the base's geometry, zones and collision
                out_name = pat.sub(stem, low) if own else low
                if out_name in files:
                    continue                            # the level's own
                if low in by_low:
                    b = PB.read_entry(data, by_low[low])[1]
                else:                                   # shared, not in the base map: any Showdown map's copy
                    src_arch = next((a for a in tgt.archives if a.startswith(group + '/') and a != arch
                                     and low in set(n.lower() for n in names_of(a)[1])), None)
                    if not src_arch:
                        continue
                    sd, sn = names_of(src_arch)
                    b = PB.read_entry(sd, next(n for n in sn if n.lower() == low))[1]
                if own and low.endswith(('.lvl', '.lnes', '.bxes', '.spwn', '.aisp', '.lgt', '.shadows', '.fog',
                                         '.glows', '.sneak', '.fade', '.duel', '.flags', '.csv')):
                    b = pat.sub(stem, b.decode('latin-1')).encode('latin-1')
                files[out_name] = b
                added += 1
            if added:
                log('  %s: a new level -- %d of the Xbox game\'s own files added (%s\'s base, renamed for %s)'
                    % (arch, added, bp, stem))
            # a .lvl listing no lines or no boxes (every level of the game lists some; the Xbox crashed loading one
            # with none, 2026-11-17): the base's lists, renamed (their files were added above)
            lk = next((k for k in files if k.lower() == stem + '.lvl'), None)
            bl = by_low.get(bstem + '.lvl')
            if lk and bl:
                text = files[lk].decode('latin-1')
                btext = pat.sub(stem, PB.read_entry(data, bl)[1].decode('latin-1'))
                filled = []
                for section in ('Lines', 'Boxes'):
                    m = re.search(r'(?is)(^\s*' + section + r'\s*\{)(.*?)(\})', text, re.M)
                    bm = re.search(r'(?is)^\s*' + section + r'\s*\{(.*?)\}', btext, re.M)
                    if m and not m.group(2).split() and bm and bm.group(1).split():
                        names = [n for n in bm.group(1).split() if n.lower() in {k.lower() for k in files}]
                        if names:
                            text = text[:m.start(2)] + '\r\n' + ''.join(n + ' \r\n' for n in names) + text[m.end(2):]
                            filled.append('%s (%d)' % (section, len(names)))
                if filled:
                    files[lk] = text.encode('latin-1')
                    log('  %s: its .lvl listed no %s: %s\'s given (every level has them)'
                        % (arch, ' / '.join(s.split(' ')[0] for s in filled), bp))

    def _assets_record(self):
        return os.path.join(self.de.cache, 'xbox_assets_built.json')

    def _write_assets(self, per, tgt, st, log):
        """The Xbox game's archives with the mod's files in them, written into the recomp's assets folder: each built
        from its unedited copy (the backup) with the files put in. An archive whose files are what they were at the
        last build (and still on disk as written) is left alone; one the last build changed and this one does not
        goes back to its unedited copy. -> {written, restored, same}."""
        import hashlib
        from . import dave as D
        import ps2build as PB
        assets, backup = st['assets'], st['backup']
        try:
            rec = json.load(open(self._assets_record(), encoding='utf-8'))
        except (OSError, ValueError):
            rec = {}

        def real_path(root, arch):
            """The archive's file under root, matching the folder's own case (lvls_m2/colseum2.dat)."""
            cur = root
            for part in arch.split('/'):
                hit = None
                if os.path.isdir(cur):
                    for f in os.listdir(cur):
                        if f.lower() == part.lower():
                            hit = f
                            break
                cur = os.path.join(cur, hit or part.lower())
            return cur
        written, same, restored = [], 0, []
        new_rec = {}
        for arch in sorted(per):
            self.de._stop_point('before writing the Xbox game files')
            files = per[arch]
            sig = hashlib.sha1(b'layout 6')     # 2: game_layout; 3: PS2 strips; 4: .lvl lists; 5/6: .bnds numbers
            for k in sorted(files):
                sig.update(k.encode('utf-8') + b'\0' + hashlib.sha1(files[k]).digest())
            sig = sig.hexdigest()
            dst = real_path(assets, arch)
            old = rec.get(arch)
            if old and old.get('sig') == sig and os.path.isfile(dst) and os.path.getsize(dst) == old.get('size') \
                    and int(os.path.getmtime(dst)) == old.get('mtime'):
                new_rec[arch] = old
                same += 1
                continue
            base, table = {}, None
            if arch in tgt.archives:
                data = tgt.data(arch)                          # the unedited copy (the backup / reference)
                for e in D.Dave(_Bytes(data), arch).entries:
                    if not e.name.endswith('/'):
                        base[e.name.lower()] = PB.read_entry(data, e.name)[1]
                table = PB.file_table(data[:0x800 + sum(struct.unpack_from('<2I', data, 8))])
            base.update(files)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            tmp = dst + '.studio-tmp'
            with open(tmp, 'wb') as f:
                f.write(D.create(base))
            # laid out as the game's own (each file stored or compressed as the original's -- the Flash screens
            # (.xck) are read as stored: compressed, the boot logo failed and the game crashed, 2026-11-17 -- in the
            # original's order, no file across a sector)
            PB.game_layout(tmp, table, None)
            os.replace(tmp, dst)
            new_rec[arch] = {'sig': sig, 'size': os.path.getsize(dst), 'mtime': int(os.path.getmtime(dst)),
                             'new': arch not in tgt.archives}
            written.append(arch)
            log('  %s: %d file(s) of the mod -> %s' % (arch, len(files), dst))
        for arch, old in rec.items():                           # built before, not now: back to unedited
            if arch in new_rec:
                continue
            dst = real_path(assets, arch)
            src = real_path(backup, arch)
            try:
                if old.get('new') or not os.path.isfile(src):
                    if os.path.isfile(dst):
                        os.remove(dst)
                else:
                    shutil.copy2(src, dst)
                restored.append(arch)
                log('  %s: no mod files now -- put back as unedited' % arch)
            except OSError as ex:
                log('  %s could not be put back: %s' % (arch, ex))
                new_rec[arch] = old
        with open(self._assets_record(), 'w', encoding='utf-8') as f:
            json.dump(new_rec, f, indent=1)
        return {'written': written, 'restored': restored, 'same': same}

    def _disc_edits(self, targeted, master, loose, log):
        """[(archive, inner path, bytes)]: files of the working disc that differ from the stock disc and that the
        mods folder does not give (modpack.scan: the same comparison a mod pack export makes)."""
        from . import modpack
        from .game import Game
        s = self.de.s
        try:
            # its own handles: the Studio's shared one on the stock disc can be closed under it ("I/O operation on
            # closed file" the first time, 2026-11-17)
            stock = Game(s.vanilla_path, writable=False)
            work = Game(s.modded_path, writable=False)
        except Exception as ex:                                 # noqa: BLE001
            log('The working disc could not be read (%s): only the Data Editor\'s mods folder goes to the Xbox' % ex)
            return []
        try:
            rows = self._scan_cached(stock, work, log)
        finally:
            for gm in (work, stock):
                try:
                    gm.close()
                except Exception:                               # noqa: BLE001
                    pass
        # by name within its archive: the PS2 build puts a .dat folder's subfolders' files at the archive's top
        # (swat.dat/gbw/gbw_lods.rsc is the disc's swat.dat:gbw_lods.rsc)
        given = {(a.lower(), inner.rsplit('/', 1)[-1].lower()) for a, items in targeted.items() for inner, _p in items}
        by_name = set(master) | set(loose)
        out = []
        for r in rows:
            arch, name = r['archive'].lower(), r['name']
            base = name.rsplit('/', 1)[-1].lower()
            if (arch, base) in given or base in by_name:
                continue
            out.append((arch, name, r['data']))
        return out

    def _scan_cached(self, stock, work, log):
        """modpack.scan, archive by archive, cached: an archive whose bytes on the working disc are what they were at
        the last build (a CRC of the whole archive) reuses that build's list of its changed files instead of
        comparing every file again. -> rows as modpack.scan's."""
        import zlib
        from . import modpack
        root = os.path.join(self.de.cache, 'xbox_scan')
        os.makedirs(root, exist_ok=True)
        try:
            idx = json.load(open(os.path.join(root, 'index.json'), encoding='utf-8'))
        except (OSError, ValueError):
            idx = {}
        stock_archives = {a.upper() for a in stock.archives()}
        rows, new_idx, rescanned = [], {}, 0
        for arc in work.archives():
            if arc.upper() not in stock_archives:
                continue
            self.de._stop_point('while comparing the working disc')
            rec = work.iso.get(arc)
            crc, off, left = 0, rec.offset if hasattr(rec, 'offset') else rec.lba * 2048, rec.size
            while left > 0:
                n = min(left, 8 << 20)
                crc = zlib.crc32(work.iso.read_at(off, n), crc)
                off += n
                left -= n
            key = '%08x:%d' % (crc, rec.size)
            old = idx.get(arc.upper())
            folder = os.path.join(root, re.sub(r'[^a-z0-9_.-]+', '_', arc.lower()))
            if old and old.get('key') == key and all(os.path.isfile(os.path.join(folder, f)) for f in old['files']):
                for name, f in zip(old['names'], old['files']):
                    rows.append({'archive': arc, 'name': name, 'data': open(os.path.join(folder, f), 'rb').read()})
                new_idx[arc.upper()] = old
                continue
            rescanned += 1

            class _One:                                         # the scan over this one archive
                def __init__(self, g):
                    self.g = g
                    self._cache = g._cache

                def archives(self):
                    return [arc]

                def archive(self, a):
                    return self.g.archive(a)
            got, _new = modpack.scan(_One(stock), _One(work))
            shutil.rmtree(folder, ignore_errors=True)
            os.makedirs(folder, exist_ok=True)
            names, files = [], []
            for k, r in enumerate(got):
                f = '%05d.bin' % k
                with open(os.path.join(folder, f), 'wb') as fh:
                    fh.write(r['data'])
                names.append(r['name'])
                files.append(f)
                rows.append({'archive': arc, 'name': r['name'], 'data': r['data']})
            new_idx[arc.upper()] = {'key': key, 'names': names, 'files': files}
        with open(os.path.join(root, 'index.json'), 'w', encoding='utf-8') as f:
            json.dump(new_idx, f)
        log('Working disc compared with the stock disc: %d archive(s) changed since the last build looked again'
            % rescanned)
        return rows

    def _name_alternates(self, out, log):
        """Players 2-4's per-texture alternates (the Showdown editor's <code>0N_<k>.tex, k = the texture's place in
        the order the model's parts use them) named for the Xbox after the texture they replace: gbw02_vest.png.xtex.
        The recomp's alttex (src/game/alttex.cpp) looks each texture of the model up by that name."""
        from . import altlooks as AL
        orders = {}
        renamed = 0
        for arch, files in out.items():
            for inner in list(files):
                base = inner.rsplit('/', 1)[-1]
                m = re.match(r'(?i)^(.+?)(0[2-4])_(\d+)\.xtex$', base)
                if not m:
                    continue
                code, n, k = m.group(1).lower(), m.group(2), int(m.group(3))
                if code not in orders:
                    bundle = self.mx._mod_or_disc('swat.dat', '%s_lods.rsc' % code)
                    try:
                        orders[code] = [t['name'] for t in AL.part_order(bundle)] if bundle else []
                    except Exception:                           # noqa: BLE001
                        orders[code] = []
                if k >= len(orders[code]):
                    continue
                new = inner[:len(inner) - len(base)] + '%s%s_%s.xtex' % (code, n, orders[code][k])
                files[new] = files.pop(inner)
                renamed += 1
        if renamed:
            log('%d alternate texture(s) for players 2-4 named after the textures they replace (as the recomp '
                'looks them up)' % renamed)

    def _record_path(self):
        return os.path.join(self.de.cache, 'xbox_last_build.tsv')

    def record_build(self, every):
        import hashlib
        with open(self._record_path(), 'w', encoding='utf-8') as f:
            for k, path in sorted(every.items()):
                try:
                    f.write('%s\t%s\n' % (k, hashlib.md5(open(path, 'rb').read()).hexdigest()))
                except OSError:
                    pass

    def last_build(self):
        """{mod key: md5} of the last Xbox build (the PS2 build's twin, last_build.tsv)."""
        out = {}
        try:
            for line in open(self._record_path(), encoding='utf-8'):
                k, _, md5 = line.rstrip('\n').partition('\t')
                if k:
                    out[k] = md5
        except OSError:
            pass
        return out

    def platform(self):
        return self._read().get('platform') if self._read().get('platform') in ('ps2', 'xbox') else 'ps2'

    def _ps2_own(self, arch, inner, data, src, in_ps2):
        """Whether a PS2 file of the mods folder is only the PS2 game's own: the disc archive's copy, or in an
        archive the disc has not, the copy of the PS2 base map the Studio laid in (lvl_ranch2 -> lvl_<map>)."""
        name = inner.rsplit('/', 1)[-1]
        if in_ps2:
            clean = src.get(arch, inner)
            return clean is not None and clean == data
        _a, any_copy = src.find(name.lower())
        if any_copy is not None and any_copy == data:
            return True
        group, base = arch.rsplit('/', 1) if '/' in arch else ('', arch)
        b = self.mx._BASES.get(group)
        if not b:
            return False
        bp, bstem = b
        stem = 'lvl_' + os.path.splitext(base)[0].lower()
        bname = re.sub(re.escape(stem) + r'(?=[_.]|$)', bstem, name, flags=re.I)
        bdata = src.get(bp, bname)
        if bdata is None:
            return False
        if bdata == data:
            return True
        if MC_is_text(bdata):
            pat = re.compile(re.escape(bstem) + r'(?=[_.]|$)', re.I)
            return pat.sub(stem, bdata.decode('latin-1')).encode('latin-1') == data
        return False

    @staticmethod
    def _split(rel, tgt, src):
        """A converted file's path -> (archive, inner path); 'master/...' -> ('', name)."""
        low = rel.lower()
        if low.startswith('master/'):
            return '', rel.split('/', 1)[1]
        archs = sorted(set(tgt.archives) | set(src.archives), key=len, reverse=True)
        for a in archs:
            if low.startswith(a.lower() + '/'):
                return a, rel[len(a) + 1:]
        parts = rel.split('/')
        for n in range(len(parts) - 1, 0, -1):
            if parts[n - 1].lower().endswith('.dat'):
                return '/'.join(parts[:n]).lower(), '/'.join(parts[n:])
        return '', rel

    @staticmethod
    def _clashes(arch, inner, tgt):
        """Whether replacing this file by name would reach another archive's DIFFERENT file of that name."""
        name = inner.rsplit('/', 1)[-1].lower()
        holders = tgt.holders.get(name, set())
        others = [a for a in holders if a != arch]
        if not others:
            return False
        if arch.lower().startswith('lvls_'):
            # a level's own file: the mod changes that level only (each level's data.plyr is the same on the Xbox
            # disc, 64 of them; loose, the change would reach all 64)
            return True
        mine = tgt.get(arch, inner)
        for a in others:
            try:
                if tgt.get(a, name) != mine:
                    return True
            except Exception:                                   # noqa: BLE001
                return True
        return False


from .dataeditor import _Bytes  # noqa: E402  (a Dave backing over bytes)


def MC_is_text(d):
    return b'\0' not in d[:4096]


def xbox(de):
    if getattr(de, '_xbox', None) is None:
        de._xbox = Xbox(de)
    return de._xbox
