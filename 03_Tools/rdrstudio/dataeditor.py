"""DISC > Data Editor: the RDR Data Editor, inside the Studio.

The Data Editor works on whole .dat archives. You keep changed game files in its own mods folder (laid out as the
disc: Master\\, swat.dat\\, lvls_sng\\tomb.dat\\ ...); Build lays them over the archives they belong in, rebuilds each
archive laid out as the game's own (load order, 32 byte packing that never crosses a 2048 byte sector, each file
compressed or stored as the original) and INJECTS the rebuilt archives into the Studio's working disc.

It is kept apart from the Studio's own edits:

  * Before an archive is first injected, it is saved exactly as it is on the working disc at that moment, Studio
    edits included (the BASE). Every later build starts from that base, so the Studio's earlier edits are kept.
  * What the Data Editor injected is undone only here (Restore): the base goes back. The Studio's Undo, Versions
    and Reset to stock are its own business; Reset to stock is noticed and the record dropped.
  * An archive the Studio changed after the Data Editor injected it is not overwritten: the build stops for it and
    says so, and Keep the Studio's version takes the disc's copy as the new base.

Everything it keeps lives beside the working disc, in data_editor\\:
    mods\\        the mod files (Master, the .dat folders, new archives)
    workbench\\   files being worked on (nothing there is built)
    bases\\       the archives as they were before their first injection
    cache\\       the unedited disc's index, extracted archives, built archives
    injected.json what was injected: per archive, the base's and the written bytes' md5

Archives are written with the Studio's own `dave.create` (the engine's sort order) and laid out by the RDR Data
Editor's `game_layout`, so no outside tool is needed. Every archive is written with compressed (packed) names; files
are compressed or stored as the original's, so banks.dat, pupshow.dat and streams.dat (all stored, the first and the
last shipped with plain names) are rebuilt with names compressed and files stored, at the original's offsets (banks
packs at 16 bytes, streams on whole sectors: the layout reproduces all 310 / 358 / 8,361 of them)
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import struct
import sys
import threading
import time
import types
import zipfile

from . import dave as D
from .iso9660 import Iso9660

_LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataeditor_lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)
import ps2build as PB  # noqa: E402  (the RDR Data Editor's build script, as a library)

SECTOR = 2048
TEXT_EXTS = ('.csv', '.txt', '.cr', '.skel', '.lvl', '.znes', '.bnds', '.lnes', '.bxes', '.spwn', '.aisp', '.swui',
             '.tbl', '.lgt', '.fog', '.glows', '.sneak', '.fade', '.shadows', '.duel', '.flags', '.enm', '.sub',
             '.film', '.cam', '.hud', '.pup', '.ini', '.cfg', '.dam', '.hot', '.msd', '.rep', '.sphs', '.swt', '.acc',
             '.ars', '.tail', '.guns', '.colors', '.strtbl_txt')


class Busy(Exception):
    """The working disc cannot be written right now (PCSX2 has it, another writer, ...)."""


class _Bytes:
    """A Dave backing over bytes in memory."""

    def __init__(self, data):
        self.data = data
        self.size = len(data)

    def read_at(self, offset, length):
        return self.data[offset:offset + length]

    def close(self):
        pass


def _md5(b):
    return hashlib.md5(b).hexdigest()


def _key(p):
    return p.replace('/', '_')


class DataEditor:
    def __init__(self, studio):
        self.s = studio
        from .pendingrebuild import data_editor_home
        self.home = data_editor_home(studio.modded_path)
        self.mods = os.path.join(self.home, "mods")
        self.workbench = os.path.join(self.home, "workbench")
        self.bases = os.path.join(self.home, "bases")
        self.cache = os.path.join(self.home, "cache")
        self.state_path = os.path.join(self.home, "injected.json")
        for d in (self.mods, self.workbench, self.bases, self.cache):
            os.makedirs(d, exist_ok=True)
        os.makedirs(os.path.join(self.mods, "Master"), exist_ok=True)
        self.lock = threading.Lock()          # one Data Editor job at a time
        self.job = {"state": "idle", "label": "", "log": [], "started": 0.0, "result": None}
        self._index = None
        PB._clean = self._clean_for_pb        # New .dat extracts archives with the Studio's reader

    # ------------------------------------------------------------------ the unedited disc's index

    def _ns(self, **kw):
        a = types.SimpleNamespace(iso=self.s.vanilla_path, cache=self.cache, mods=self.mods, dave=_LIB,
                                  out=self.s.modded_path)
        for k, v in kw.items():
            setattr(a, k, v)
        return a

    def index(self):
        """(index, dats, holders) of the UNEDITED disc: which names each archive holds."""
        if self._index is None:
            iso = PB.Iso(self.s.vanilla_path)
            try:
                index, dats, _stamp, holders = PB.load_index(self._ns(), iso)
            finally:
                iso.close()
            self._index = (index, dats, holders)
        return self._index

    def archives(self):
        index, _d, _h = self.index()
        return sorted(index['archives'])

    # ------------------------------------------------------------------ extracting with the Studio's reader

    def extract_bytes(self, data, folder):
        """Every file of an archive (bytes) into folder, as their paths say. Returns the count."""
        arc = D.Dave(_Bytes(data), "archive")
        n = 0
        for e in arc.entries:
            if e.name.endswith('/'):
                continue
            out = os.path.join(folder, *e.name.split('/'))
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, 'wb') as f:
                f.write(arc.read(e))
            n += 1
        return n

    def _clean_for_pb(self, a, iso, dats, p):
        """ps2build's _clean (an archive of the unedited disc extracted once), with the Studio's Dave reader."""
        clean = os.path.join(self.cache, 'clean', _key(p))
        if not os.path.isdir(clean):
            tmp = clean + '.part'
            shutil.rmtree(tmp, ignore_errors=True)
            self.extract_bytes(iso.read(dats[p]), tmp)
            os.replace(tmp, clean)
        return clean

    def _extracted(self, data):
        """An archive (bytes) extracted once into the cache, keyed by its md5."""
        folder = os.path.join(self.cache, 'base_' + _md5(data))
        if not os.path.isdir(folder):
            tmp = folder + '.part'
            shutil.rmtree(tmp, ignore_errors=True)
            self.extract_bytes(data, tmp)
            os.replace(tmp, folder)
        return folder

    # ------------------------------------------------------------------ building an archive

    def _all_on_disc(self, rec, items):
        """Is every mod file for an archive on the working disc's copy of it already, byte for byte?"""
        try:
            arc = D.Dave(_Bytes(self._disc_read(rec)), 'disc')
        except Exception:                                       # noqa: BLE001
            return False
        by_name = {}
        for e in arc:
            by_name.setdefault(e.name.rsplit('/', 1)[-1], []).append(e)
        for k, path, how in items:
            name = k.rsplit('/', 1)[-1]
            hits = [e for e in arc if e.name == k] if how == 'path' and '/' in k else by_name.get(name, [])
            if not hits:
                return False
            want = open(path, 'rb').read()
            if any(arc.read(e) != want for e in hits):
                return False
        return True

    @staticmethod
    def pack_folder(folder):
        """A folder's files as a Dave archive (bytes), laid out by the sector rule (no original to follow)."""
        files = {}
        for root, _d, fs in os.walk(folder):
            for f in fs:
                if f.lower() == PB.FOLDER_FILE:                 # which disc folder it belongs in: not a game file
                    continue
                full = os.path.join(root, f)
                files[os.path.relpath(full, folder).replace(os.sep, '/').lower()] = open(full, 'rb').read()
        return files

    def build_archive(self, files, out, table=None, order=None):
        """files {name: bytes} as an archive at out, laid out as the game's (table: the original's file_table)."""
        data = D.create(files)
        with open(out, 'wb') as f:
            f.write(data)
        PB.game_layout(out, table, order)
        return open(out, 'rb').read()

    # ------------------------------------------------------------------ the record of what was injected

    def state(self):
        try:
            with open(self.state_path, encoding='utf-8') as f:
                st = json.load(f)
        except (OSError, ValueError):
            st = {}
        st.setdefault('disc', self.s.modded_path)
        st.setdefault('archives', {})
        return st

    def save_state(self, st):
        tmp = self.state_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(st, f, indent=1)
        os.replace(tmp, self.state_path)

    # ------------------------------------------------------------------ the working disc

    def _disc_files(self):
        iso = PB.Iso(self.s.modded_path)
        try:
            return iso.files()
        finally:
            iso.close()

    def _disc_read(self, rec):
        iso = PB.Iso(self.s.modded_path)
        try:
            return iso.read(rec)
        finally:
            iso.close()

    def check_state(self):
        """What happened to the injected archives since: {archive: 'ok' | 'changed' | 'gone' | 'stock'}.
        'stock' (the disc's copy is the base again: Reset to stock, or a restored Version) drops the record."""
        st = self.state()
        out = {}
        if not st['archives']:
            return out
        try:
            stat = os.stat(self.s.modded_path)
            stamp = (stat.st_size, stat.st_mtime_ns, json.dumps(st['archives'], sort_keys=True))
        except OSError:
            stamp = None
        cached = getattr(self, '_health', None)
        if stamp and cached and cached[0] == stamp:
            return dict(cached[1])  # the disc has not changed since the last look
        try:
            files = self._disc_files()
        except OSError:
            return {p: 'unknown' for p in st['archives']}
        dropped = False
        for p, rec in list(st['archives'].items()):
            r = files.get(p)
            if r is None:
                out[p] = 'gone'
                if rec.get('new'):
                    del st['archives'][p]  # the disc no longer has the added archive (Reset to stock)
                    dropped = True
                continue
            md5 = _md5(self._disc_read(r))
            if md5 == rec.get('written'):
                out[p] = 'ok'
            elif rec.get('follow') and md5 != rec.get('base'):
                # changed by the Studio, and those edits are mirrored into its .dat folder: still ours
                rec['written'] = md5
                rec.pop('follow', None)
                out[p] = 'ok'
                dropped = True
            elif md5 == rec.get('base'):
                out[p] = 'stock'
                del st['archives'][p]
                dropped = True
            else:
                out[p] = 'changed'
        if dropped:
            self.save_state(st)
        try:
            stat = os.stat(self.s.modded_path)
            self._health = ((stat.st_size, stat.st_mtime_ns, json.dumps(st['archives'], sort_keys=True)), dict(out))
        except OSError:
            pass
        return out

    PCSX2_BUSY = ("PCSX2 is running, and it has the working disc. Close PCSX2 and build again. Nothing was written.")

    @staticmethod
    def pcsx2_running():
        """Is a PCSX2 process running? Asked of Windows' task list: PCSX2 opens the disc SHARED, so a test open of
        the file succeeds while the game is running, and the build then failed half way with no message."""
        if os.name != 'nt':
            return False
        try:
            import subprocess
            out = subprocess.run(['tasklist', '/FO', 'CSV', '/NH'], capture_output=True, text=True, timeout=10,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout.lower()
            return 'pcsx2' in out
        except Exception:                                       # noqa: BLE001
            return False

    def check_pcsx2(self):
        if self.pcsx2_running():
            raise Busy(self.PCSX2_BUSY)

    def _take_disc(self):
        """The working disc to ourselves: the Studio's handles closed (it reopens them after), refused when PCSX2
        or another writer has it."""
        self.check_pcsx2()
        lock_file = self.s.modded_path + Iso9660.LOCK_SUFFIX
        self.s.close()
        try:
            with open(self.s.modded_path, 'r+b'):
                pass
        except OSError:
            self.s.open()
            raise Busy("PCSX2 (or another program) has the working disc. Close it and build again. Nothing was "
                       "written.")
        if os.path.exists(lock_file):
            try:
                Iso9660.clear_stale_lock(self.s.modded_path)
            except Exception:                                   # noqa: BLE001
                pass
            if os.path.exists(lock_file):
                self.s.open()
                raise Busy("Another program is writing the working disc (%s). Wait for it. Nothing was written."
                           % lock_file)

    def _give_back(self):
        try:
            self.s.open()
        finally:
            self.s._assets = None

    # ------------------------------------------------------------------ the mods: status for the page

    def mod_files(self):
        index, _dats, holders = self.index()
        fresh = PB.new_archives(self.mods, index['archives'])
        loose, targeted, every, master = PB.mod_files(self.mods, list(index['archives']) + list(fresh))
        return index, holders, fresh, loose, targeted, every, master

    def overview(self):
        """The folders (Master, every archive, new archives) and each one's files with their status."""
        index, holders, fresh, loose, targeted, every, master = self.mod_files()
        last = self._last_build()
        st = self.state()
        health = self.check_state() if st['archives'] else {}
        modes = PB.master_modes(self.mods)
        folders = [{'key': 'master', 'label': 'Master  (levels, front, swat)', 'kind': 'master'}]
        for p in sorted(index['archives'], key=lambda a: (a != 'swat.dat', list(index['archives']).index(a))):
            folders.append({'key': p, 'label': p, 'kind': 'archive',
                            'injected': p in st['archives'], 'health': health.get(p)})
        for p in sorted(fresh):
            folders.append({'key': p, 'label': p + '  (new archive)', 'kind': 'new',
                            'injected': p in st['archives'], 'health': health.get(p)})
        files = []
        arc_names = {}
        for k, path in sorted(every.items()):
            name = os.path.basename(path)
            low = name.lower()
            md5 = _md5(open(path, 'rb').read())
            parts = k.split('/')
            folder = 'master' if parts[0] == 'master' else next(
                (a for a in list(index['archives']) + list(fresh) if k.startswith(a + '/')), '')
            into, mode = [], None
            if folder == 'master':
                mode = PB.master_mode(low, modes, holders)
                into = ([a for a in holders.get(low, ()) if PB.master_target(a)] if mode == 'replace'
                        else [a for a in index['archives'] if PB.master_target(a)])
            elif folder:
                into = [folder]
            else:
                into = sorted(holders.get(low, ()))
            status = 'built' if last.get(k) == md5 else ('changed' if k in last else 'new')
            if not into:
                status = 'not a game file'
            # a .dat folder's file the archive has no file of the name of is ADDED to it
            added = False
            if folder and folder != 'master' and folder in index['archives']:
                names = arc_names.get(folder)
                if names is None:
                    names = arc_names[folder] = {n.rsplit('/', 1)[-1].lower() for n in index['archives'][folder].get('names', [])}
                added = low not in names
            files.append({'key': k, 'folder': folder or '(loose)', 'name': name, 'size': os.path.getsize(path),
                          'status': status, 'into': into, 'mode': mode, 'text': self.is_text(path),
                          'game_file': low in holders, 'new_to_archive': added,
                          'mtime': int(os.path.getmtime(path))})
        removed = [k for k in last if k not in every]
        dots = {}
        for f in files:
            if f['status'] in ('new', 'changed'):
                dots[f['folder']] = 'pending'
            elif f['folder'] not in dots:
                dots[f['folder']] = 'built'
        for k in removed:
            fol = 'master' if k.startswith('master/') else next((a for a in index['archives'] if k.startswith(a + '/')), '')
            dots[fol or '(loose)'] = 'pending'
        return {'folders': folders, 'files': files, 'removed': removed, 'dots': dots,
                'injected': st['archives'], 'health': health, 'home': self.home, 'mods': self.mods,
                'workbench': self.workbench, 'disc': self.s.modded_path, 'job': self.job_status()}

    # ------------------------------------------------------------------ the disc's file order

    def _order_quietly(self):
        """restore_disc_order for the Studio's own rebuilds: a failure is noted, never in the way of the edit."""
        try:
            self.restore_disc_order(self.s.note)
        except Exception as ex:                                 # noqa: BLE001
            self.s.note('Could not put the disc back in order: %s' % ex)

    def disc_order_plan(self):
        """Where every file of the working disc goes in the unedited disc's order: [(path, record, new lba)], or
        None when it is in that order already. A rebuilt archive that grew is appended at the end of the image; the
        game then seeks between the end of the disc and swat.dat / banks / streams on every level load (a very long
        load screen, whatever the level). Each file goes back where it shipped -- or right after the file before it,
        when that one grew -- in the shipped order; new files (custom levels) after their folder's last one."""
        stock = PB.Iso(self.s.vanilla_path)
        try:
            sf = stock.files()
        finally:
            stock.f.close()
        iso = PB.Iso(self.s.modded_path)
        try:
            files = iso.files()
        finally:
            iso.f.close()
        start = min(r['lba'] for r in sf.values())
        if any(r['lba'] < start for r in files.values()):
            return None                                         # not laid out as the shipped disc: left alone
        last_in = {}
        for pth, r in sf.items():
            d = pth.rsplit('/', 1)[0] if '/' in pth else ''
            last_in[d] = max(last_in.get(d, 0), r['lba'])
        def key(item):
            pth, r = item
            if pth in sf:
                return (sf[pth]['lba'], 0, pth)
            d = pth.rsplit('/', 1)[0] if '/' in pth else ''
            return (last_in.get(d, 1 << 30), 1, pth)
        plan, cur, moved = [], start, False
        for pth, r in sorted(files.items(), key=key):
            want = sf[pth]['lba'] if pth in sf else cur
            lba = max(want, cur)
            plan.append((pth, r, lba))
            if lba != r['lba']:
                moved = True
            cur = lba + max(1, -(-r['size'] // SECTOR))
        return plan if moved else None

    def restore_disc_order(self, log=None):
        """The working disc rewritten with its files in the shipped order (disc_order_plan). The image up to the
        first file (volume descriptors, path tables, folders) is kept as it is; each file is copied to its place
        and its folder record's extent updated; the volume size follows. Written beside the disc and swapped in, so
        a failure leaves the disc as it was. Returns how many files moved."""
        log = log or self._log
        plan = self.disc_order_plan()
        if not plan:
            return 0
        start = min(l for _p, _r, l in plan)
        end = max(l + max(1, -(-r['size'] // SECTOR)) for _p, r, l in plan)
        try:
            stock_blocks = PB.Iso(self.s.vanilla_path)
            vb = stock_blocks.blocks
            stock_blocks.f.close()
        except Exception:                                       # noqa: BLE001
            vb = 0
        total = max(end + PB.TAIL_PAD // SECTOR, vb)
        need = total * SECTOR + (64 << 20)
        folder = os.path.dirname(os.path.abspath(self.s.modded_path))
        if shutil.disk_usage(folder).free < need:
            raise ValueError('not enough free space next to the working disc to put its files back in order: %s MB '
                             'needed' % (need >> 20))
        moved = sum(1 for _p, r, l in plan if l != r['lba'])
        log('Putting the files of the disc back in the shipped order (%d moved), so levels load as fast as the '
            'unedited game...' % moved)
        tmp = self.s.modded_path + '.reorder'
        with self.s.lock:
            self._take_disc()
            try:
                with open(self.s.modded_path, 'rb') as src, open(tmp, 'wb') as out:
                    src.seek(0)
                    out.write(src.read(start * SECTOR))
                    for _pth, r, lba in sorted(plan, key=lambda t: t[2]):
                        if out.tell() < lba * SECTOR:
                            out.write(bytes(lba * SECTOR - out.tell()))
                        src.seek(r['lba'] * SECTOR)
                        left = r['size']
                        while left:
                            chunk = src.read(min(left, 8 << 20))
                            if not chunk:
                                raise ValueError('the disc ended inside %s' % _pth)
                            out.write(chunk)
                            left -= len(chunk)
                        out.write(bytes(-out.tell() % SECTOR))
                    if out.tell() < total * SECTOR:
                        out.write(bytes(total * SECTOR - out.tell()))
                    for _pth, r, lba in plan:
                        out.seek(r['at'] + 2)
                        out.write(struct.pack('<I', lba) + struct.pack('>I', lba))
                    out.seek(16 * SECTOR + 80)
                    out.write(struct.pack('<I', total) + struct.pack('>I', total))
                # read back: every file where the plan put it, its bytes the same
                chk = PB.Iso(tmp)
                try:
                    got = chk.files()
                    for pth, r, lba in plan[:: max(1, len(plan) // 12)]:
                        if got[pth]['lba'] != lba or got[pth]['size'] != r['size']:
                            raise ValueError('%s did not land where planned' % pth)
                finally:
                    chk.f.close()
                os.replace(tmp, self.s.modded_path)
            except Exception:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
            finally:
                self._give_back()
        self._health = None
        log('Disc order restored: %d file(s) back in place, the image %s MB.' % (moved, format(total * SECTOR >> 20, ',')))
        return moved

    # ------------------------------------------------------------------ the Studio's edits, shown in the .dat folders

    def _mirror_path(self):
        return os.path.join(self.cache, 'mirrored.json')

    def _mirrored(self):
        try:
            return json.load(open(self._mirror_path(), encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def _write_last_build(self, last):
        p = os.path.join(self.cache, 'last_build.tsv')
        with open(p + '.tmp', 'w', encoding='utf-8', newline='\n') as f:
            for k in sorted(last):
                f.write(k + '\t' + last[k] + '\n')
        os.replace(p + '.tmp', p)

    def mirror(self, items):
        """Studio edits [(archive, file, bytes)] shown in the Data Editor: each written into its archive's .dat folder
        and marked built (it is on the disc already, so it is not a change waiting for a Build). An edit back to the
        unedited disc's bytes takes the copy out again. (Every archive is shown: banks,
        pupshow and the streams too.) Never raises: showing an edit must not fail the edit."""
        try:
            last = self._last_build()
            mirrored = self._mirrored()
            touched = set()
            for archive, name, data in items:
                p = archive.lower().replace('\\', '/')
                n = name.replace('\\', '/').lower()
                key = p + '/' + n
                dest = os.path.join(self.mods, *p.split('/'), *n.split('/'))
                try:
                    stock = self.s.vanilla.archive(archive).read(name)
                except Exception:                               # noqa: BLE001
                    stock = None
                if stock is not None and data == stock:
                    if key in mirrored and os.path.isfile(dest):
                        os.remove(dest)
                    mirrored.pop(key, None)
                    last.pop(key, None)
                else:
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with open(dest, 'wb') as f:
                        f.write(data)
                    mirrored[key] = last[key] = _md5(data)
                touched.add(p)
            self._write_last_build(last)
            with open(self._mirror_path(), 'w', encoding='utf-8') as f:
                json.dump(mirrored, f, indent=0)
            # an archive the Data Editor injected follows the Studio's edits instead of turning "changed"
            st = self.state()
            if any(p in st['archives'] for p in touched):
                for p in touched:
                    if p in st['archives']:
                        st['archives'][p]['follow'] = True
                self.save_state(st)
            self._wait_cache = None
        except Exception as ex:                                 # noqa: BLE001
            try:
                self.s.note('Data Editor: could not show a Studio edit in its folder: %s' % ex)
            except Exception:                                   # noqa: BLE001
                pass

    def mirror_reset(self):
        """Reset to stock: the mirrored copies go (the disc no longer has those edits), and nothing counts as built."""
        try:
            for key, md5 in self._mirrored().items():
                path = os.path.join(self.mods, *key.split('/'))
                if os.path.isfile(path) and _md5(open(path, 'rb').read()) == md5:
                    os.remove(path)
            for p in (self._mirror_path(), os.path.join(self.cache, 'last_build.tsv')):
                if os.path.exists(p):
                    os.remove(p)
            self._wait_cache = None
        except Exception:                                       # noqa: BLE001
            pass

    def waiting(self):
        """How many mod files are waiting for a Build (new, changed, removed), cheaply: recounted only when the mods
        folder or the last build changes. -> (count, [folders])."""
        sig = []
        for r, _d, fs in os.walk(self.mods):
            for f in fs:
                full = os.path.join(r, f)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                sig.append((full, st.st_size, st.st_mtime_ns))
        lb = os.path.join(self.cache, 'last_build.tsv')
        try:
            sig.append((lb, os.path.getsize(lb), os.stat(lb).st_mtime_ns))
        except OSError:
            pass
        sig = tuple(sorted(sig))
        c = getattr(self, '_wait_cache', None)
        if c and c[0] == sig:
            return c[1]
        try:
            ov = self.overview()
            files = [f for f in ov['files'] if f['status'] in ('new', 'changed')]
            folders = sorted({f['folder'] for f in files} | {k.split('/')[0] for k in ov['removed']})
            res = (len(files) + len(ov['removed']), folders)
        except Exception:                                       # noqa: BLE001
            res = (0, [])
        self._wait_cache = (sig, res)
        return res

    def _last_build(self):
        p = os.path.join(self.cache, 'last_build.tsv')
        out = {}
        if os.path.isfile(p):
            for line in open(p, encoding='utf-8'):
                k, _, md5 = line.rstrip('\n').partition('\t')
                if k:
                    out[k] = md5
        return out

    @staticmethod
    def is_text(path):
        if path.lower().endswith(TEXT_EXTS):
            return True
        try:
            with open(path, 'rb') as f:
                head = f.read(4096)
        except OSError:
            return False
        return bool(head) and b'\0' not in head and sum(1 for b in head if b < 9 or 13 < b < 32) * 20 < len(head)

    # ------------------------------------------------------------------ files in the mods folder

    def _safe(self, rel, base=None):
        base = os.path.abspath(base or self.mods)
        full = os.path.abspath(os.path.join(base, *[x for x in re.split(r'[\\/]', rel or '') if x not in ('', '.', '..')]))
        if not (full + os.sep).startswith(base + os.sep) and full != base:
            raise ValueError('outside the mods folder')
        return full

    def folder_path(self, folder):
        if folder in ('master', 'Master'):
            return os.path.join(self.mods, 'Master')
        if folder in ('', '(loose)'):
            return self.mods
        return self._safe(folder)

    def add_files(self, folder, files):
        """files [{name, data (base64)}] into a folder of the mods folder (master, an archive, a new archive)."""
        if self.job['state'] == 'running':
            raise Busy('A build is running: wait for it to finish before adding files.')
        dest = self.folder_path(folder)
        os.makedirs(dest, exist_ok=True)
        done = []
        for f in files:
            name = os.path.basename(f['name'].replace('\\', '/'))
            if not name:
                continue
            with open(os.path.join(dest, name), 'wb') as fh:
                fh.write(base64.b64decode(f['data']))
            done.append(name)
        return done

    def delete_files(self, keys):
        if self.job['state'] == 'running':
            raise Busy('A build is running: wait for it to finish before deleting files.')
        gone = []
        for k in keys:
            p = self._safe(k)
            if os.path.isfile(p):
                os.remove(p)
                gone.append(k)
        return gone

    def read_file(self, key, base=None):
        p = self._safe(key, base)
        return open(p, 'rb').read()

    def save_text(self, key, text, base=None):
        if self.job['state'] == 'running' and base is None:
            raise Busy('A build is running: wait for it to finish before saving into the mods folder.')
        p = self._safe(key, base)
        old = open(p, 'rb').read() if os.path.isfile(p) else b''
        crlf = b'\r\n' in old
        data = text.replace('\r\n', '\n')
        if crlf:
            data = data.replace('\n', '\r\n')
        with open(p, 'wb') as f:
            f.write(data.encode('latin-1', errors='replace'))

    def set_master_mode(self, names, mode):
        path = os.path.join(self.mods, PB.MODES_FILE)
        modes = PB.master_modes(self.mods)
        for n in names:
            modes[n.lower()] = mode
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            for n in sorted(modes):
                f.write(n + '\t' + modes[n] + '\n')

    def from_game(self, archive, names, folder=None, to=None):
        """Files of an archive of the WORKING disc (so the Studio's edits come along) copied into its .dat folder --
        or, `to` 'workbench', into the Workbench (a folder "From <archive>" there: to look at or work on, never built).
        Read entry by entry (PB.read_entry re-parses the archive's whole name table for every file)."""
        from .game import Game
        files = self._disc_files()
        if archive not in files:
            raise ValueError('%s is not on the working disc' % archive)
        try:
            dave = Game(self.s.modded_path, writable=False).archive(archive.upper())
        except Exception as ex:                                 # noqa: BLE001
            raise ValueError('%s could not be read: %s' % (archive, ex))
        by_name = {}
        for e in dave:
            by_name.setdefault(e.name.lower(), e.name)
            by_name.setdefault(e.name.lower().rsplit('/', 1)[-1], e.name)
        if to == 'workbench':
            dest = os.path.join(self.workbench, 'From ' + archive.replace('/', '_'))
            os.makedirs(dest, exist_ok=True)
            from .dataeditor_more import write_folder_file
            write_folder_file(dest, archive)
        else:
            dest = self.folder_path(folder or archive)
        os.makedirs(dest, exist_ok=True)
        got = []
        for n in names:
            path = by_name.get(n.lower()) or by_name.get(n.lower().rsplit('/', 1)[-1])
            if not path:
                continue
            raw = dave.read(path)
            with open(os.path.join(dest, path.rsplit('/', 1)[-1]), 'wb') as f:
                f.write(raw)
            got.append(path)
        return got

    def place_archive(self, name, into=''):
        """Where an imported archive (tomb.dat) goes: the game's archive of that name (in the folder dropped on when
        several have it), else a new archive in the lvls_ folder dropped on, or by its player count."""
        name = name.lower()
        into = (into or '').strip('/').lower()
        hits = [a for a in self.archives() if a.rsplit('/', 1)[-1] == name]
        if into.endswith('.dat') and into.rsplit('/', 1)[-1] == name:
            return into
        group = into.split('/')[0] if into.startswith('lvls_') else ''
        for h in hits:
            if not group or h.startswith(group + '/'):
                return h
        if hits:
            return hits[0]
        if not group:
            stem = os.path.splitext(name)[0]
            group = 'lvls_m2' if stem.endswith('2') else 'lvls_m4' if stem.endswith('4') else 'lvls_sng'
        return '%s/%s' % (group, name)

    def import_archive(self, name, data, into=''):
        """An uploaded .dat into the mods folder as its .dat folder. For an archive the game has, only the files that
        differ from the working disc's copy are kept (so the Studio's edits are not counted as changes)."""
        if self.job['state'] == 'running':
            raise Busy('A build is running: wait for it to finish before importing.')
        rel = self.place_archive(name, into)
        tmp = os.path.join(self.cache, 'import')
        shutil.rmtree(tmp, ignore_errors=True)
        n = self.extract_bytes(data, tmp)
        dest = os.path.join(self.mods, *rel.split('/'))
        kept = same = 0
        disc = None
        if rel in self.archives():
            files = self._disc_files()
            disc = self._disc_read(files[rel]) if rel in files else None
        xbox = 0
        for root, _d, fs in os.walk(tmp):
            for f in fs:
                full = os.path.join(root, f)
                inner = os.path.relpath(full, tmp).replace(os.sep, '/')
                if os.path.splitext(f)[1].lower() in ('.xrsc', '.xrnt', '.xznes', '.xtex', '.xmod'):
                    xbox += 1
                if disc is not None:
                    _p, raw = PB.read_entry(disc, inner)
                    if raw is not None and raw == open(full, 'rb').read():
                        same += 1
                        continue
                out = os.path.join(dest, *inner.split('/'))
                os.makedirs(os.path.dirname(out), exist_ok=True)
                shutil.copyfile(full, out)
                kept += 1
        shutil.rmtree(tmp, ignore_errors=True)
        note = ('%s -> %s%s: %d of %d files kept' % (name, rel, '' if disc is not None else ' (a new archive)', kept, n)
                + (', %d the same as the working disc\'s left out' % same if same else ''))
        if xbox:
            note += ('. It holds %d Xbox file(s): the PS2 game does not load them (convert the mod in the RDR Data '
                     'Editor app first)' % xbox)
        return {'archive': rel, 'kept': kept, 'same': same, 'note': note}

    def archive_names(self, archive):
        index, _d, _h = self.index()
        return [n for n in index['archives'].get(archive, {}).get('names', []) if not n.endswith('/')]

    # ------------------------------------------------------------------ jobs (one at a time, on a thread)

    def job_status(self):
        j = dict(self.job)
        j['log'] = list(self.job['log'][-400:])
        return j

    def _log(self, line):
        self.job['log'].append(line)
        del self.job['log'][:-2000]

    def start(self, label, fn, *args):
        if not self.lock.acquire(blocking=False):
            raise Busy('The Data Editor is already busy (%s).' % self.job.get('label'))
        self.job = {"state": "running", "label": label, "log": [], "started": time.time(), "result": None}

        def run():
            try:
                self.job['result'] = fn(*args)
                self.job['state'] = 'done'
            except Busy as ex:
                self._log(str(ex))
                self.job['state'] = 'refused'
            except Exception as ex:                             # noqa: BLE001
                import traceback
                self._log('Stopped: %s: %s' % (type(ex).__name__, ex))
                self._log(traceback.format_exc())
                self.job['state'] = 'error'
            finally:
                self.job['seconds'] = round(time.time() - self.job['started'], 1)
                self.lock.release()
        threading.Thread(target=run, name='dataeditor', daemon=True).start()
        return True

    # ------------------------------------------------------------------ Build: mods -> rebuilt archives -> working disc

    def build(self):
        log = self._log
        self.check_pcsx2()
        t0 = time.time()
        log('Reading the unedited disc\'s archive list...')
        index, holders, fresh, loose, targeted, every, master = self.mod_files()
        for p in fresh:
            targeted.pop(p, None)
        # A Showdown map is only found under lvls_m2 (<name>2.dat) and lvls_m4 (<name>4.dat), listed in the Showdown
        # menu (postermultiview.swui) -- the same pair put in lvls_sng is built onto the disc and never loaded.
        for p in sorted(fresh):
            m = re.fullmatch(r'lvls_sng/(.+)([24])\.dat', p)
            if m and 'lvls_sng/%s%s.dat' % (m.group(1), '4' if m.group(2) == '2' else '2') in fresh:
                log('  %s looks like a Showdown map (it has a %s twin): the game only loads those from lvls_m%s/%s%s.dat. '
                    'Move the folder there (and its twin to lvls_m%s), then New .dat > Showdown with the name %s to '
                    'list it in the Showdown menu.' % (p, '4' if m.group(2) == '2' else '2', m.group(2), m.group(1),
                                                       m.group(2), '4' if m.group(2) == '2' else '2', m.group(1)))
        st = self.state()
        health = self.check_state()
        st = self.state()
        # what goes into which archive, as the RDR Data Editor's build decides it
        todo = {}
        for k, path in loose.items():
            if k in master:
                continue
            for p in holders.get(k, ()):
                todo.setdefault(p, []).append((k, path, 'name'))
        modes = PB.master_modes(self.mods)
        for k, path in master.items():
            if PB.master_mode(k, modes, holders) == 'replace':
                for p in holders.get(k, ()):
                    if PB.master_target(p):
                        todo.setdefault(p, []).append((k, path, 'name'))
            else:
                for p in index['archives']:
                    if PB.master_target(p):
                        todo.setdefault(p, []).append((k, path, 'all'))
        for p, items in targeted.items():
            # A folder dropped INTO a .dat folder (the Workbench's "gbw" folder into swat.dat) went into the archive
            # as gbw/gbw.cr -- and SWAT.DAT, like most archives, has no folders: the game looks for gbw.cr at its
            # top, never finds it, and a level that needs it loads forever. Into an archive with no folders of its
            # own, a file in a subfolder goes in at the top.
            info = index['archives'].get(p)
            flat = bool(info) and not any('/' in n.rstrip('/') for n in info['names'])
            moved = set()
            for inner, path in items:
                if flat and '/' in inner:
                    moved.add(inner.split('/', 1)[0])
                    inner = inner.rsplit('/', 1)[-1]
                todo.setdefault(p, []).append((inner, path, 'path'))
            if moved:
                log('  %s has no folders: the files in your %s folder(s) %s go in at its top (the game would not '
                    'look inside a folder)' % (p, len(moved), ', '.join(sorted(moved))))
        for k in sorted(k for k in loose if k not in holders and k not in master):
            log('  %s: no archive of the game has a file of that name: left out' % k)
        # the archives to put back: injected before, no mods for them now
        back = [p for p in st['archives'] if p not in todo and p not in fresh]
        conflicts = [p for p, h in health.items() if h == 'changed' and (p in todo or p in fresh or p in back)]
        for p in conflicts:
            log('  %s: changed in the Studio after the Data Editor built it: left alone. Restore it here, or "Keep '
                'the Studio\'s version" to build over the disc\'s copy.' % p)
        if not todo and not fresh and not back:
            log('Nothing to build: the mods folder has no game files.')
            return {'built': [], 'restored': []}
        files = self._disc_files()
        built = {}
        bases_new = {}
        built_keys = {}
        skipped = []
        # files taken out of the mods folder since the last build: the archives they went into must be rebuilt
        removed = [k for k in self._last_build() if k not in every]
        def lost_a_file(p):
            return any(k.startswith('master/') or k.startswith(p + '/') for k in removed)
        for p in sorted(todo):
            if p in conflicts:
                continue
            if p not in files:
                log('  %s: not on the working disc: left out' % p)
                continue
            info = index['archives'][p]
            rec = st['archives'].get(p)
            if rec and os.path.isfile(os.path.join(self.bases, _key(p) + '.dat')):
                base = open(os.path.join(self.bases, _key(p) + '.dat'), 'rb').read()
            else:
                base = self._disc_read(files[p])
                bases_new[p] = base
            items = todo[p]
            key = hashlib.md5(('layout3|' + _md5(base)).encode())
            for k, path, how in sorted(items):
                key.update(k.encode() + how.encode() + hashlib.md5(open(path, 'rb').read()).digest())
            key = key.hexdigest()
            out = os.path.join(self.cache, 'built_' + _key(p))
            # Only archives with changes are rebuilt and written. Left alone: one built from these same mods whose
            # build is still on the disc, and one whose every mod file is on the disc already as it is (a Studio
            # edit shown in its folder, or a build of the same files).
            if rec and rec.get('key') == key and health.get(p) == 'ok':
                log('%s: no changes since its last build: left as it is' % p)
                skipped.append(p)
                continue
            if not lost_a_file(p) and self._all_on_disc(files[p], items):
                log('%s: its files are on the working disc already: left as it is' % p)
                skipped.append(p)
                bases_new.pop(p, None)
                continue
            if os.path.isfile(out) and os.path.isfile(out + '.key') and open(out + '.key').read() == key:
                log('%s: its mods are as last time, the last build kept' % p)
                built[p] = open(out, 'rb').read()
                built_keys[p] = key
                continue
            log('Building %s (%d mod file(s))...' % (p, len(items)))
            src = self._extracted(base)
            work = os.path.join(self.cache, 'work')
            shutil.rmtree(work, ignore_errors=True)
            try:
                shutil.copytree(src, work, copy_function=os.link)
            except OSError:
                shutil.rmtree(work, ignore_errors=True)
                shutil.copytree(src, work)
            by_name, by_path = {}, {}
            for root, _d, fs in os.walk(work):
                for f in fs:
                    full = os.path.join(root, f)
                    by_name.setdefault(f.lower(), []).append(full)
                    by_path[os.path.relpath(full, work).replace(os.sep, '/').lower()] = full

            def put(srcf, target):
                if os.path.exists(target):
                    os.remove(target)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copyfile(srcf, target)
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
                elif how == 'all':
                    put(path, os.path.join(work, name))
                    added.append(k)
                else:
                    put(path, os.path.join(work, *os.path.relpath(path, os.path.join(self.mods, *p.split('/'))).split(os.sep)))
                    added.append(k)
            for w in PB.check_view_rooms(work):
                log('  ' + w)
            table = PB.file_table(base[:0x800 + sum(struct.unpack_from('<2I', base, 8))])
            order = None
            if added:
                order = PB.new_archive_order(p, work, _DiscHeads(base, p, self.s.vanilla_path, index), _Dats(p, index),
                                             index['archives'])
            data = self.build_archive(self.pack_folder(work), out, table, order)
            with open(out + '.key', 'w') as f:
                f.write(key)
            built_keys[p] = key
            log('  %d replaced%s; %s bytes (was %s)' % (replaced, (', %d added' % len(added)) if added else '',
                                                        format(len(data), ','), format(len(base), ',')))
            built[p] = data
        new_built = {}
        for p, folder in sorted(fresh.items()):
            if p in conflicts:
                continue
            log('Building the new archive %s...' % p)
            for w in PB.check_view_rooms(folder):
                log('  ' + w)
            out = os.path.join(self.cache, 'new_' + _key(p))
            order = PB.new_archive_order(p, folder, _VanillaHeads(self.s.vanilla_path), _Dats(None, index),
                                         index['archives'])
            new_built[p] = self.build_archive(self.pack_folder(folder), out, None, order)
            log('  %s files, %s bytes' % (sum(len(f) for _r, _d, f in os.walk(folder)), format(len(new_built[p]), ',')))
        # inject: the Studio's disc handles closed, our writes, the handles back
        # Every backup on disk BEFORE the first write to the disc, and room checked: a Build that died on a full
        # drive half way through once left 45 archives injected with no record and no way to Restore them.
        need = sum(len(v) for v in bases_new.values()) + (16 << 20)
        free = shutil.disk_usage(self.bases).free
        if free < need:
            raise ValueError('not enough free space for the backups: %s MB needed on the drive of %s, %s MB free. '
                             'Nothing was written to the disc.' % (need >> 20, self.bases, free >> 20))
        for p, data in bases_new.items():
            if p in built:
                path = os.path.join(self.bases, _key(p) + '.dat')
                with open(path + '.tmp', 'wb') as f:
                    f.write(data)
                os.replace(path + '.tmp', path)
        log('Injecting into the working disc...')
        with self.s.lock:
            self._take_disc()
            try:
                iso = PB.Iso(self.s.modded_path, 'r+b')
                try:
                    disc = iso.files()
                    if not st['archives']:
                        # the disc's size before the Data Editor first wrote to it: Restore shrinks it back to this
                        # when everything it added is gone and nothing else lies beyond
                        st['disc_blocks_before'] = iso.blocks
                        st['disc_bytes_before'] = os.path.getsize(self.s.modded_path)
                    for p in back:
                        if p in conflicts:
                            continue
                        self._put_back(iso, disc, st, p, log)
                        disc = iso.files()
                    for p, data in built.items():
                        disc = iso.files()
                        extents = sorted(r['lba'] for r in disc.values())
                        how = iso.replace(disc[p], data, extents)
                        rec = st['archives'].get(p) or {'new': False}
                        if p in bases_new:
                            rec['base'] = _md5(bases_new[p])   # the backup was written before the injection began
                        rec['written'] = _md5(data)
                        rec['time'] = time.strftime('%Y-%m-%d %H:%M')
                        if p in built_keys:
                            rec['key'] = built_keys[p]         # what it was built from: unchanged next time -> skipped
                        st['archives'][p] = rec
                        self.save_state(st)                    # recorded at once: Restore can undo it whatever follows
                        log('  %s: %s bytes, %s' % (p, format(len(data), ','), how))
                    for p, data in new_built.items():
                        disc = iso.files()
                        if p in disc:  # added by an earlier build: replaced
                            extents = sorted(r['lba'] for r in disc.values())
                            how = iso.replace(disc[p], data, extents)
                            st['archives'][p]['written'] = _md5(data)
                            log('  %s: %s bytes, %s' % (p, format(len(data), ','), how))
                        else:
                            d, _, name = p.rpartition('/')
                            try:
                                r = iso.add_file(d, name, data)
                            except ValueError as ex:
                                log('  %s: not added: %s' % (p, ex))
                                continue
                            r['written'] = _md5(data)
                            r['time'] = time.strftime('%Y-%m-%d %H:%M')
                            st['archives'][p] = r
                            self.save_state(st)
                            log('  %s: %s bytes, added to the disc' % (p, format(len(data), ',')))
                    self._shrink_if_unused(iso, st, log)
                    self.save_state(st)
                    disc = iso.files()
                    for p, data in list(built.items()) + list(new_built.items()):
                        if p in disc and _md5(iso.read(disc[p])) != _md5(data):
                            log('  %s on the working disc: DIFFERS from what was built' % p)
                finally:
                    iso.close()
            finally:
                self._give_back()
        with open(os.path.join(self.cache, 'last_build.tsv'), 'w', encoding='utf-8', newline='\n') as f:
            for k, path in sorted(every.items()):
                f.write(k + '\t' + _md5(open(path, 'rb').read()) + '\n')
        if built or new_built or back:
            self.restore_disc_order(log)
        self.s.note('Data Editor: %d archive(s) built into the working disc' % (len(built) + len(new_built)))
        log('Done in %d s: %d archive(s) rebuilt into the working disc%s%s.' % (
            time.time() - t0, len(built) + len(new_built), (', %d put back' % len(back)) if back else '',
            (', %d without changes left as they are' % len(skipped)) if skipped else ''))
        return {'built': sorted(built) + sorted(new_built), 'restored': back, 'conflicts': conflicts}

    def _put_back(self, iso, disc, st, p, log):
        rec = st['archives'].get(p)
        if not rec:
            return
        if rec.get('new'):
            if p in disc:
                iso.restore_new(rec)
            log('  %s: taken off the disc again (it was added by the Data Editor)' % p)
        else:
            path = os.path.join(self.bases, _key(p) + '.dat')
            if not os.path.isfile(path):
                log('  %s: its base copy is missing: left as it is' % p)
                return
            base = open(path, 'rb').read()
            extents = sorted(r['lba'] for r in disc.values())
            iso.replace(disc[p], base, extents)
            log('  %s: back to how it was before the Data Editor (Studio edits kept)' % p)
            try:
                os.remove(path)
            except OSError:
                pass
        del st['archives'][p]

    # ------------------------------------------------------------------ the Studio's own edits that do not fit

    def rebuild_archive(self, archive, replace=None, add=None, remove=None):
        """A Studio edit with no room in its slot: the archive rebuilt from the working disc's copy (every Studio edit
        kept) with these files replaced / added, laid out as the game's, and put back on the disc (in place when it
        fits, else at the end). The caller holds the Studio's lock. Returns the new archive's bytes.

        This is the Studio's edit, not the Data Editor's: Ctrl+Z undoes it as any other. (An archive the Data Editor
        injected is seen as changed in the Studio afterwards, and its builds leave it alone until you choose.)"""
        self.check_pcsx2()
        p = archive.lower().replace('\\', '/')
        files = self._disc_files()
        if p not in files:
            raise ValueError('%s is not on the working disc' % archive)
        base = self._disc_read(files[p])
        if base[:4] not in (b'Dave', b'DAVE'):
            raise ValueError('%s is not a Dave archive' % archive)
        # Room to work: the archive unpacked (up to ~3x its stored size) and the rebuilt copy. A full drive used to
        # stop this a couple of seconds in with nothing but "No space left on device".
        need = 4 * len(base) + (64 << 20)
        free = shutil.disk_usage(self.cache).free
        if free < need:
            raise ValueError('not enough free space to rebuild %s: it needs about %s MB on the drive of %s, and %s MB '
                             'is free. Free some space there, or move the Data Editor folder (App setup > Folders > '
                             'Data Editor) to a drive with room.' % (archive, need >> 20, self.cache, free >> 20))
        src = self._extracted(base)
        work = os.path.join(self.cache, 'work_rebuild')
        shutil.rmtree(work, ignore_errors=True)
        try:
            shutil.copytree(src, work, copy_function=os.link)
        except OSError:
            shutil.rmtree(work, ignore_errors=True)
            shutil.copytree(src, work)
        by_path = {}
        for root, _d, fs in os.walk(work):
            for f in fs:
                full = os.path.join(root, f)
                by_path[os.path.relpath(full, work).replace(os.sep, '/').lower()] = full

        def put(target, data):
            if os.path.exists(target):
                os.remove(target)  # the link only, never the cached file
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, 'wb') as f:
                f.write(data)
        for name, data in (replace or {}).items():
            n = name.lower().replace('\\', '/')
            target = by_path.get(n) or next((v for k, v in by_path.items() if k.rsplit('/', 1)[-1] == n), None)
            if target is None:
                raise ValueError('%s has no file %s' % (archive, name))
            put(target, data)
        for name, data in (add or {}).items():
            put(os.path.join(work, *name.lower().replace('\\', '/').split('/')), data)
        for name in (remove or ()):
            n = name.lower().replace('\\', '/')
            target = by_path.get(n) or next((v for k, v in by_path.items() if k.rsplit('/', 1)[-1] == n), None)
            if target is not None:
                os.remove(target)  # the link only, never the cached file
        head = base[:0x800 + sum(struct.unpack_from('<2I', base, 8))]
        out = os.path.join(self.cache, 'rebuilt_' + _key(p))
        data = self.build_archive(self.pack_folder(work), out, PB.file_table(head), None)
        # the extracted copy stays, as the new archive's: the next edit of it starts at once
        keep = os.path.join(self.cache, 'base_' + _md5(data))
        if os.path.isdir(keep):
            shutil.rmtree(work, ignore_errors=True)
        else:
            os.replace(work, keep)
        # Only the newest extracted copy of an archive is kept: the one it was rebuilt from is out of date now (a
        # swat.dat copy is ~600 MB, and keeping every version filled a drive). The built bytes go once injected.
        if os.path.normcase(src) != os.path.normcase(keep):
            shutil.rmtree(src, ignore_errors=True)
        g = self.s.game
        if g is None:  # the Studio has handed the disc over (Play): take it as a build does
            self._take_disc()
        try:
            iso = PB.Iso(self.s.modded_path, 'r+b')
            try:
                disc = iso.files()
                extents = sorted(r['lba'] for r in disc.values())
                how = iso.replace(disc[p], data, extents)
            finally:
                iso.close()
            try:
                os.remove(out)          # on the disc now; the extracted copy is what the next edit needs
            except OSError:
                pass
        finally:
            if g is None:
                self._give_back()
            else:
                # the Studio's own disc object reloaded IN PLACE: code in the middle of an edit holds it, and a
                # closed one would fail under it (the batch writer, the menu installer ...)
                keep_index = g._index if not add else None
                try:
                    g.iso.close()
                except Exception:                               # noqa: BLE001
                    pass
                g.__init__(self.s.modded_path, writable=True)
                g._index = keep_index
                self.s._levels = {}
                self.s._assets = None
        self.s.note('%s rebuilt to make room (%s -> %s bytes, %s)' % (archive, format(len(base), ','),
                                                                     format(len(data), ','), how))
        return data

    # ------------------------------------------------------------------ Add Character (BALANCE > Creatures)

    CHAR_FILES = ('cr', 'dam', 'hot', 'msd', 'rep', 'sphs', 'swt', 'ars', 'tail', 'acc')

    def add_character(self, source, code, rsc=None, rnt=None, skel_data=None, extra=None, copy=None):
        """A new character slot in SWAT.DAT from an existing one (the RDR Blender add-on's rules): every file the
        game reads for a character by its code copied under the new code; the .cr's FileNameRender and FileNameSkel
        become the new code (its model bundle <code>_lods.rsc/.rnt and skeleton <code>.skel, copied from the
        source's, the bundle's mesh names renamed -- the game finds a character's meshes as <code>_lod_01 ...);
        FileNameAnim (the shared animation set) stays; Face becomes 0 (the face morphs belong to the source's
        bundle). One rebuild of SWAT.DAT. Returns the files added.

        rsc / rnt: a model bundle of your own (the RDR Blender add-on's export) instead of the source's: everything
        else (skeleton, tuning, damage ...) still comes from the source, so export the model on the source's
        skeleton. Its mesh names become <code>_lod_01 / _lod_02 / hat_<code>_lod_01 (a lone mesh: <code>_lod_01)."""
        src = source.strip().lower()
        src = src[:-3] if src.endswith('.cr') else src
        code = code.strip().lower()
        if not re.fullmatch(r'[a-z][a-z0-9_]{1,11}', code):
            raise ValueError('The new code is 2 to 12 letters, digits or _, starting with a letter (e.g. "gbw")')
        g = self.s.game
        if g is None:
            raise Busy('PCSX2 has the working disc. Close it first.')
        arc = g.archive('SWAT.DAT')
        names = {e.name for e in arc.entries}
        if '%s.cr' % code in names:
            raise ValueError('There is a character %s already' % code)
        if '%s.cr' % src not in names:
            raise ValueError('SWAT.DAT has no %s.cr' % src)
        cr = arc.read('%s.cr' % src).decode('latin-1')
        render = re.search(r'^\s*FileNameRender\s+(\S+)', cr, re.M)
        skel = re.search(r'^\s*FileNameSkel\s+(\S+)', cr, re.M)
        if not render or not skel:
            raise ValueError('%s.cr names no model bundle or skeleton' % src)
        render, skel = render.group(1).lower(), skel.group(1).lower()
        # Your own character files (picked with the .rsc: a .cr, .dam, .hot ...) are used as they are; the rest are
        # copied from the source character, the kinds in `copy` (None: every kind it has).
        extra = dict(extra or {})
        own = {}
        for n in list(extra):
            ext = n.rsplit('.', 1)[-1].lower() if '.' in n else ''
            if ext in self.CHAR_FILES and ext not in own:
                own[ext] = extra.pop(n)
        want_copy = None if copy is None else {c.lower().lstrip('.') for c in copy}
        adds = {}
        for ext in self.CHAR_FILES:
            n = '%s.%s' % (src, ext)
            if ext in own:
                data = own[ext]
            elif (want_copy is None or ext in want_copy) and n in names:
                data = arc.read(n)
            else:
                continue
            if ext == 'cr':
                text = data.decode('latin-1')
                for key in ('FileNameRender', 'FileNameSkel'):
                    text = re.sub(r'(^\s*%s\s+)\S+' % key, lambda m: m.group(1) + code, text, count=1, flags=re.M)
                if ext not in own:
                    text = re.sub(r'(^\s*Face\s+)\S+', lambda m: m.group(1) + '0', text, count=1, flags=re.M)
                data = text.encode('latin-1')
            adds['%s.%s' % (code, ext)] = data
        if '%s.cr' % code not in adds:
            raise ValueError('A character needs a .cr: tick ".cr" to copy it from %s, or pick your own .cr' % src)
        if skel_data is None and (want_copy is None or 'skel' in want_copy):
            if '%s.skel' % skel not in names:
                raise ValueError('SWAT.DAT has no %s.skel (the skeleton %s.cr names)' % (skel, src))
            adds['%s.skel' % code] = arc.read('%s.skel' % skel)
        elif skel_data is None:
            raise ValueError('A character needs a skeleton: tick ".skel" to copy it from %s, or pick your own .skel' % src)
        else:
            adds['%s.skel' % code] = skel_data
        if rsc is not None:
            adds['%s_lods.rsc' % code], adds['%s_lods.rnt' % code] = self._own_bundle(rsc, rnt, code)
            # the model and its skeleton must agree on the bone count: a model skinned to bones its skeleton does not
            # have (or a skeleton from another rig) is what turns a character into a crash
            from .rsc import Rsc as _Rsc
            from . import skeleton as _SK
            b = _Rsc(rsc, code + '_lods.rsc')
            want = max([b.bone_count(c.index) for c in b.chunks if c.tag == 'MOD0'] or [0])
            try:
                have = len(_SK.parse(adds['%s.skel' % code])[2])
            except Exception as ex:                             # noqa: BLE001
                raise ValueError('The skeleton (.skel) does not read: %s' % ex)
            if want and want != have:
                raise ValueError('Your model is rigged to %d bones, and the skeleton it would get (%s) has %d. Pick the '
                                 '.skel the model was made on too (the Blender add-on exports it), or copy from a '
                                 'character whose skeleton has %d bones.' % (
                                     want, 'yours' if skel_data is not None else src + "'s", have, want))
            self.s.rebuild_with('SWAT.DAT', add=adds)
            self._order_quietly()
            self._to_workbench(code, adds, extra)
            self.s.note('new character %s (model from your .rsc, the rest from %s): %s' % (code, src, ', '.join(sorted(adds))))
            return sorted(adds)
        for ext in ('rsc', 'rnt'):
            n = '%s_lods.%s' % (render, ext)
            if n not in names:
                raise ValueError('SWAT.DAT has no %s (the model bundle %s.cr names)' % (n, src))
            data = arc.read(n)
            if ext == 'rnt':
                from rdrformats import rsc as _rsc, rscwrite as _rw
                entries = [(tag, num, re.sub(r'(?<![a-z0-9])%s(?=_)' % re.escape(render), code, name))
                           for tag, num, name in _rsc.read_names(data)]
                data = _rw.write_names(entries)
            adds['%s_lods.%s' % (code, ext)] = data
        self.s.rebuild_with('SWAT.DAT', add=adds)
        self._order_quietly()
        self._to_workbench(code, adds, extra)
        self.s.note('new character %s (from %s): %s' % (code, src, ', '.join(sorted(adds))))
        return sorted(adds)

    @staticmethod
    def _own_bundle(rsc, rnt, code):
        """An uploaded bundle checked, its mesh names made the new character's."""
        if rsc[:4] != b'RSC0':
            raise ValueError('That is not a compiled model bundle (.rsc): it does not start with RSC0')
        if not rnt or rnt[:4] != b'RNT0':
            raise ValueError('Pick the bundle .rnt too (the names file the Blender add-on exports beside the .rsc)')
        from rdrformats import rsc as _rsc, rscwrite as _rw
        entries = _rsc.read_names(rnt)
        mods = [i for i, (tag, _n, _name) in enumerate(entries) if tag == 'MOD0']
        if not mods:
            raise ValueError('The .rnt names no model (MOD0): is it the right file?')
        out = []
        for i, (tag, num, name) in enumerate(entries):
            if tag == 'MOD0':
                m = re.fullmatch(r'(hat_)?.+?_lod_(\d+)', name.lower())
                if m:
                    name = '%s%s_lod_%s' % (m.group(1) or '', code, m.group(2))
                elif len(mods) == 1:
                    name = code + '_lod_01'
            out.append((tag, num, name))
        if code + '_lod_01' not in [n for t, _x, n in out if t == 'MOD0']:
            raise ValueError('The bundle has no body mesh (a model named <something>_lod_01). Its models: %s. Name the '
                             'body <code>_lod_01 in Blender and export again.' % ', '.join(n for t, _x, n in entries if t == 'MOD0'))
        return rsc, _rw.write_names(out)

    def _to_workbench(self, code, adds, extra=None):
        """Every file of a new character (and the extra files picked with it, e.g. its .tex textures) copied to
        the Workbench, in a folder named after the code, to keep and to work on."""
        dest = os.path.join(self.workbench, code)
        os.makedirs(dest, exist_ok=True)
        for name, data in list(adds.items()) + list((extra or {}).items()):
            name = os.path.basename(name.replace(os.sep, '/'))
            if name:
                with open(os.path.join(dest, name), 'wb') as f:
                    f.write(data)
        self.s.note('%s: its files copied to the Workbench (%s)' % (code, code))

    # ------------------------------------------------------------------ Delete Character (BALANCE > Creatures)

    def character_files(self, code, fresh=True):
        """{archive: [file names]} of a character on the working disc (<code>.* and <code>_lods.*)."""
        code = code.strip().lower()
        code = code[:-3] if code.endswith('.cr') else code
        pre = (code + '.', code + '_lods.')
        g = self.s.game if self.s.game is not None else self.s.disc
        if fresh:
            g._index = None
        out = {}
        for n, arcs in g.build_index().items():
            if n.startswith(pre):
                for a in arcs:
                    out.setdefault(a, []).append(n)
        return code, out

    def is_base_character(self, code):
        try:
            self.s.vanilla.archive('SWAT.DAT').find(code + '.cr')
            return True
        except KeyError:
            return False

    def delete_plan(self, code, fresh=False):
        code, where = self.character_files(code, fresh)
        pre = (code + '.', code + '_lods.')
        mods = []
        for r, _d, fs in os.walk(self.mods):
            for f in fs:
                if f.lower().startswith(pre):
                    mods.append(os.path.relpath(os.path.join(r, f), self.mods).replace(os.sep, '/'))
        return {'code': code, 'base_game': self.is_base_character(code),
                'archives': sorted(where), 'files': sorted({n for ns in where.values() for n in ns}), 'mods': mods}

    def _strip(self, data, names):
        """An archive (bytes) without these files, laid out as the game's."""
        arc = D.Dave(_Bytes(data), 'archive')
        keep = {e.name: arc.read(e) for e in arc if e.name not in names}
        tmp = os.path.join(self.cache, 'strip.dat')
        out = self.build_archive(keep, tmp, PB.file_table(data[:0x800 + sum(struct.unpack_from('<2I', data, 8))]), None)
        os.remove(tmp)
        return out

    def delete_character(self, code):
        """A CUSTOM character off the working disc: every file of it out of every archive. Base game characters are
        refused. Files of it in the Data Editor's mods folder go to the Workbench (else the next Build would bring it
        back), its files come out of the Data Editor's base copies too, the Data Editor's archives are rebuilt from
        those, and any other archive holding it is rebuilt without it. A job (de_job); not undoable with Ctrl+Z."""
        log = self._log
        plan = self.delete_plan(code, fresh=True)
        code = plan['code']
        if plan['base_game']:
            raise ValueError('%s is one of the game\'s own characters: only characters you added can be deleted' % code)
        if not plan['archives'] and not plan['mods']:
            raise ValueError('No character %s on the working disc' % code)
        names = set(plan['files'])
        # 1. the mods folder: out of the way, kept in the Workbench
        if plan['mods']:
            dest = os.path.join(self.workbench, 'Deleted character %s' % code)
            for rel in plan['mods']:
                src = os.path.join(self.mods, *rel.split('/'))
                out = os.path.join(dest, *rel.split('/'))
                os.makedirs(os.path.dirname(out), exist_ok=True)
                shutil.move(src, out)
            log('%d file(s) of %s moved from the mods folder to the Workbench (Deleted character %s)' % (len(plan['mods']), code, code))
        # 2. the Data Editor's base copies: without it, so a Build from them does not carry it
        st = self.state()
        stripped = []
        for p, rec in st['archives'].items():
            path = os.path.join(self.bases, _key(p) + '.dat')
            if rec.get('new') or not os.path.isfile(path):
                continue
            base = open(path, 'rb').read()
            arc = D.Dave(_Bytes(base), p)
            if not any(e.name in names for e in arc):
                continue
            new = self._strip(base, names)
            with open(path + '.tmp', 'wb') as f:
                f.write(new)
            os.replace(path + '.tmp', path)
            rec['base'] = _md5(new)
            stripped.append(p)
        if stripped:
            self.save_state(st)
            log('%s taken out of the Data Editor\'s base copies of %d archive(s)' % (code, len(stripped)))
        # 3. the Data Editor's own archives rebuilt (from those bases and the mods left)
        if plan['mods'] or stripped:
            log('Building the Data Editor\'s archives without %s...' % code)
            self.build()
        # 4. anything else still holding it: rebuilt without it
        code, left = self.character_files(code)
        for i, (arc, ns) in enumerate(sorted(left.items())):
            log('  %d/%d %s: %d file(s) of %s out' % (i + 1, len(left), arc, len(ns), code))
            with self.s.lock:
                self.rebuild_archive(arc, remove=ns)
        code, left = self.character_files(code)
        if left:
            raise ValueError('%s is still in %s' % (code, ', '.join(sorted(left))))
        self.restore_disc_order(log)
        self.s.note('character %s deleted from the working disc' % code)
        log('%s deleted: it is on no archive of the working disc now.' % code)
        return {'code': code, 'archives': plan['archives']}

    @staticmethod
    def slot_of(data, name):
        """(stored bytes, slot) of a file in an archive (bytes)."""
        arc = D.Dave(_Bytes(data), 'archive')
        e = arc.find(name.lower())
        return e.size_comp, e.slot

    # ------------------------------------------------------------------ Restore (only here)

    def restore(self, archives):
        log = self._log
        st = self.state()
        wanted = [p for p in archives if p in st['archives']] if archives != ['all'] else list(st['archives'])
        if not wanted:
            log('Nothing the Data Editor injected to put back.')
            return {'restored': []}
        health = self.check_state()
        st = self.state()
        with self.s.lock:
            self._take_disc()
            try:
                iso = PB.Iso(self.s.modded_path, 'r+b')
                try:
                    # added archives come off newest first (each changed its folder after the last)
                    order = [p for p in wanted if not st['archives'].get(p, {}).get('new')] + \
                        list(reversed([p for p in st['archives'] if st['archives'][p].get('new') and p in wanted]))
                    for p in order:
                        if p not in st['archives']:
                            continue
                        if health.get(p) == 'changed':
                            log('  %s: the Studio changed it after the Data Editor built it; putting back the base '
                                'drops those later Studio edits too' % p)
                        self._put_back(iso, iso.files(), st, p, log)
                    self._shrink_if_unused(iso, st, log)
                    self.save_state(st)
                finally:
                    iso.close()
            finally:
                self._give_back()
        lb = os.path.join(self.cache, 'last_build.tsv')
        if os.path.isfile(lb):
            keep = [l for l in open(lb, encoding='utf-8')
                    if not any(l.lower().startswith(p + '/') for p in wanted)]
            with open(lb, 'w', encoding='utf-8', newline='\n') as f:
                f.writelines(keep)
        if order:
            self.restore_disc_order(log)
        self.s.note('Data Editor: %d archive(s) put back' % len(order))
        log('Done: %d archive(s) put back on the working disc.' % len(order))
        return {'restored': order}

    def _shrink_if_unused(self, iso, st, log):
        """With nothing of the Data Editor's left on the disc, its appended data goes: the volume size back to what
        it was and the file trimmed -- but only when no file (the Studio's own additions included) lies beyond."""
        blocks = st.get('disc_blocks_before')
        if st['archives'] or not blocks:
            return
        end = max((r['lba'] + -(-r['size'] // SECTOR) for r in iso.files().values()), default=0)
        if end > blocks:
            log('  the disc keeps its size: a file the Studio added lies past where the Data Editor\'s data began')
            st.pop('disc_blocks_before', None)
            st.pop('disc_bytes_before', None)
            return
        iso.f.seek(iso.pvd_at + 80)
        iso.f.write(struct.pack('<I', blocks) + struct.pack('>I', blocks))
        iso.blocks = blocks
        size = st.get('disc_bytes_before') or blocks * SECTOR
        if os.path.getsize(self.s.modded_path) > size:
            iso.f.truncate(size)
        log('  the working disc is back to its size before the Data Editor (%s bytes)' % format(size, ','))
        st.pop('disc_blocks_before', None)
        st.pop('disc_bytes_before', None)

    def adopt(self, archive):
        """Keep the Studio's version: the working disc's copy becomes the base the next build starts from."""
        st = self.state()
        rec = st['archives'].get(archive)
        if not rec or rec.get('new'):
            raise ValueError('%s was not injected by the Data Editor' % archive)
        files = self._disc_files()
        data = self._disc_read(files[archive])
        with open(os.path.join(self.bases, _key(archive) + '.dat'), 'wb') as f:
            f.write(data)
        rec['base'] = rec['written'] = _md5(data)
        self.save_state(st)
        return True

    # ------------------------------------------------------------------ New .dat

    def new_level(self, kind, name, template=None, title=None, subtitle=None, replace=False):
        log = self._log
        index, dats, _h = self.index()
        a = self._ns(new_level=kind, name=name, template=template, title=title, subtitle=subtitle, replace=replace)
        PB.XBOX[0] = False
        iso = PB.Iso(self.s.vanilla_path)
        import contextlib
        import io
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                r = PB.new_level(a, iso, index, dats)
        finally:
            iso.close()
        for line in buf.getvalue().splitlines():
            log(line)
        if r:
            raise ValueError('New .dat refused (see the log)')
        return True

    # ------------------------------------------------------------------ Manual .dat/.zip extracting/building

    def manual_extract(self, archive_path, to):
        log = self._log
        data = open(archive_path, 'rb').read()
        name = os.path.splitext(os.path.basename(archive_path))[0]
        out = to if os.path.basename(os.path.normpath(to)).lower() == name.lower() else os.path.join(to, name)
        if archive_path.lower().endswith('.zip'):
            with zipfile.ZipFile(archive_path) as z:
                z.extractall(out)
                n = len([i for i in z.infolist() if not i.is_dir()])
        else:
            n = self.extract_bytes(data, out)
        log('%s: %d files extracted to %s' % (os.path.basename(archive_path), n, out))
        return {'folder': out, 'files': n}

    def manual_build(self, folder, output):
        log = self._log
        if not os.path.isdir(folder):
            raise ValueError('no such folder: %s' % folder)
        if output.lower().endswith('.zip'):
            with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as z:
                for root, _d, fs in os.walk(folder):
                    for f in fs:
                        full = os.path.join(root, f)
                        z.write(full, os.path.relpath(full, folder).replace(os.sep, '/'))
            log('Built %s (.zip, plain names: the prototype build\'s kind)' % output)
            return {'output': output, 'size': os.path.getsize(output)}
        data = self.build_archive(self.pack_folder(folder), output, None, None)
        log('Built %s: %s bytes, laid out as the game\'s archives (32 byte packing, never across a sector)'
            % (output, format(len(data), ',')))
        return {'output': output, 'size': len(data)}

    def manual_extract_all(self, to):
        log = self._log
        index, dats, _h = self.index()
        iso = PB.Iso(self.s.vanilla_path)
        try:
            for i, p in enumerate(sorted(index['archives'], key=lambda x: (x.count('/'), x)), 1):
                clean = self._clean_for_pb(None, iso, dats, p)
                out = os.path.join(to, *p.split('/'))
                shutil.rmtree(out, ignore_errors=True)
                shutil.copytree(clean, out)
                log('[%d/%d] %s' % (i, len(index['archives']), p))
        finally:
            iso.close()
        log('Every archive of the unedited disc extracted to %s' % to)
        return {'folder': to}


class _Dats(dict):
    """ps2build.new_archive_order wants dats[archive]: every archive of the unedited disc."""

    def __init__(self, own, index):
        super().__init__({p: p for p in index['archives']})


class _VanillaHeads:
    """read_head(archive) from the unedited disc, for new_archive_order."""

    def __init__(self, vanilla):
        self.vanilla = vanilla
        self._files = None

    def read_head(self, p):
        iso = PB.Iso(self.vanilla)
        try:
            if self._files is None:
                self._files = iso.files()
            return iso.read_head(self._files[p])
        finally:
            iso.close()


class _DiscHeads(_VanillaHeads):
    """As _VanillaHeads, but the archive being built answers from its base."""

    def __init__(self, base, own, vanilla, index):
        super().__init__(vanilla)
        self.base, self.own = base, own

    def read_head(self, p):
        if p == self.own:
            return self.base[:0x800 + sum(struct.unpack_from('<2I', self.base, 8))]
        return super().read_head(p)
