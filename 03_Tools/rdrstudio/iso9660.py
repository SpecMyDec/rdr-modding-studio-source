"""ISO9660 reader with in-place write support.

Red Dead Revolver ships a plain ISO9660 disc (no Joliet needed, no UDF).
We only ever *read* the directory tree -- the whole modding method depends on
never moving a file, so directory records are never rewritten.
"""

import atexit
import os
import re
import struct
import threading
import time


SECTOR = 2048


class IsoFile:
    __slots__ = ("path", "lba", "size", "is_dir")

    def __init__(self, path, lba, size, is_dir):
        self.path = path
        self.lba = lba
        self.size = size
        self.is_dir = is_dir

    @property
    def offset(self):
        """Absolute byte offset of this file's data inside the ISO."""
        return self.lba * SECTOR

    def __repr__(self):
        return "<IsoFile %s lba=%d size=%d>" % (self.path, self.lba, self.size)


class Iso9660:
    """Random-access ISO9660 volume.

    Open read-only by default. Pass writable=True to enable in-place patching;
    that never changes any file's LBA or length-on-disc, only its contents.
    """

    # REQ-078, answered by the reviewer: ONE WRITER, enforced.
    #
    # Nothing used to serialise two processes writing this file. Session.lock
    # is a threading.RLock, which orders threads inside one Studio and says
    # nothing about a second process, and `flock /tmp/rdr-suite.lock` only
    # ever ordered the things that TAKE it -- not a hand-run script, not a
    # stray Studio, and not a suite started any way but test.sh. The write
    # path is read-modify-write on a slot plus a TOC update; two of those
    # interleaved are protected by nothing.
    #
    # A LOCKFILE rather than fcntl.flock or msvcrt.locking, and that is the
    # load-bearing choice: this tree is driven from BOTH WSL-side python3 and
    # Windows-side `py -3`, and an advisory lock taken through drvfs on one
    # side is not reliably visible to the other. A lockfile is plain
    # filesystem semantics and both sides agree about it. O_CREAT|O_EXCL is
    # atomic, so there is no check-then-act window of the kind that cost
    # REQ-077 its premise.
    #
    # The cost, stated because it is real: a process killed mid-write leaves
    # the file behind and the next writer refuses until a human clears it.
    # That is deliberate -- this code cannot tell a live holder from a dead
    # one when the pid may belong to the other platform, and guessing wrong
    # hands the disc to a second writer while the first is still in it.
    LOCK_SUFFIX = ".writer.lock"

    def __init__(self, path, writable=False):
        self.path = str(path)
        self.writable = writable
        self._lock_path = self.path + self.LOCK_SUFFIX
        self._lock_is_ours = False
        if writable:
            self._take_writer_lock()
        # buffering=0, and it is the fix for REQ-077 rather than a tuning
        # choice. A buffered reader serves a seek that lands inside its
        # existing buffer WITHOUT going back to the OS, so a second process
        # writing this same ISO is invisible to a handle that has already read
        # that region -- no matter what the Studio invalidates above it.
        # Reduced to a plain file, the behaviour this removes:
        #
        #     B reads offset 4096 : AAAAAAAA        <- fills B's buffer
        #     A wrote MOVED!!! at 4096 and fsynced
        #     B reads offset 4096 : AAAAAAAA        <- same handle, after
        #     a FRESH handle sees : MOVED!!!
        #
        # That is `level?reload=1` returning a pre-move position while the disc
        # holds the moved one, which is exactly what was reported. It is
        # history-dependent rather than flaky: it bites only when the edited
        # offset falls inside the reader's current buffer, which is why it
        # resisted reproduction.
        #
        # The cost is a syscall per read where there used to be one per 8 KB,
        # and the OS page cache still absorbs the repeats. The price of the
        # raw layer is that read() and write() may be SHORT -- both are looped
        # below, and neither was before.
        try:
            self._f = open(self.path, "r+b" if writable else "rb", buffering=0)
        except Exception:
            # PCSX2 holding the file raises PermissionError HERE, after the
            # lock was taken. Leaving the lockfile behind would refuse every
            # later writer over an ISO nobody has -- a guard that outlives its
            # own process is indistinguishable from a stale one, and this is
            # the commonest path in the product (start the Studio during a
            # play session).
            self._drop_writer_lock()
            raise
        # A file object carries ONE cursor, and the Studio serves requests on
        # several threads. Seek-then-read is two operations, so without this
        # lock a second thread reseeks between them and the first thread reads
        # from the wrong place. See scripts/test_read_race.py: 34% of
        # concurrent reads came back wrong, and TOMB quietly parsed as 566
        # nodes instead of 896 -- a half-level, cached for the session.
        self._io = threading.Lock()
        self.volume_id = ""
        self.total_sectors = 0
        self._files = {}
        self._read_pvd()
        self._walk()

    # ---------------------------------------------------------------- basics

    @staticmethod
    def _win_started(pid):
        """Wall-clock start time of a Windows process, or None if it cannot be
        read (no such pid, or a process this user may not query)."""
        try:
            import ctypes
            from ctypes import wintypes
            k = ctypes.windll.kernel32
            k.OpenProcess.restype = wintypes.HANDLE
            h = k.OpenProcess(0x1000, False, int(pid))   # QUERY_LIMITED_INFORMATION
            if not h:
                return None
            try:
                c, e, kt, ut = (wintypes.FILETIME() for _ in range(4))
                if not k.GetProcessTimes(wintypes.HANDLE(h), ctypes.byref(c),
                                         ctypes.byref(e), ctypes.byref(kt),
                                         ctypes.byref(ut)):
                    return None
                ticks = (c.dwHighDateTime << 32) | c.dwLowDateTime
                return ticks / 1e7 - 11644473600.0
            finally:
                k.CloseHandle(wintypes.HANDLE(h))
        except Exception:                                   # noqa: BLE001
            return None

    @classmethod
    def _holder_alive(cls, text):
        """`_pid_alive` for a whole lock line: it also knows WHEN the lock was
        taken and by WHAT, so a recycled pid is not mistaken for the holder."""
        m = re.search(r"pid (\d+) on (posix|windows)", text)
        if not m:
            return None
        img = re.search(r"\(([^()]+)\), opened", text)
        when = re.search(r"opened (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", text)
        opened = None
        if when:
            try:
                opened = time.mktime(time.strptime(when.group(1),
                                                   "%Y-%m-%d %H:%M:%S"))
            except (ValueError, OverflowError):
                opened = None
        return cls._pid_alive(int(m.group(1)), m.group(2), opened=opened,
                              image=img.group(1) if img else None)

    @staticmethod
    def _holder_image():
        import sys
        return os.path.basename(sys.executable or "") or "python"

    @staticmethod
    def _pid_alive(pid, plat, opened=None, image=None):
        """True / False / None, and None means "could not tell".

        Windows recycles pids. A Studio that was killed (Task Manager, a crash,
        a power cut) leaves its lock behind, and an hour later some unrelated
        process -- a dllhost.exe, measured 2026-11-11 -- owns that number: "the
        pid exists" then answered True forever and every write was refused
        over a holder that died yesterday. So on Windows a pid only counts as
        the holder if it could BE the holder: it is this program (a python or
        the Studio exe, or the image the lock names) and it started no later
        than the lock was taken. Either test failing is a definite False.

        None is not a maybe to be rounded down. A lock is reclaimed ONLY on a
        definite False, because reclaiming a live holder's lock hands the disc
        to a second writer -- the exact thing the lock exists to prevent, done
        by the lock itself.
        """
        if plat != ("windows" if os.name == "nt" else "posix"):
            # A Windows pid means nothing to WSL and vice versa; the numbers
            # are from different spaces and would collide meaninglessly.
            return None
        try:
            if os.name == "nt":
                import subprocess
                out = subprocess.run(
                    ["tasklist", "/FI", "PID eq %d" % pid, "/NH"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    timeout=30).stdout.decode("utf-8", "replace")
                # tasklist prints an INFO line, not an error, when nothing
                # matches -- so "the pid appears in the output" is the test,
                # not "the command succeeded".
                row = None
                for line in out.splitlines():
                    mm = re.match(r"\s*(.+?)\s+(\d+)\s", line)
                    if mm and int(mm.group(2)) == pid:
                        row = mm.group(1).strip().lower()
                if row is None:
                    return str(pid) in out and None
                # tasklist cuts the image name at 25 characters
                if image:
                    if row[:25] != image.strip().lower()[:25]:
                        return False
                elif not (row.startswith(("python", "py.exe", "pyw.exe"))
                          or "rdr" in row):
                    return False
                if opened is not None:
                    born = Iso9660._win_started(pid)
                    if born is not None and born > opened + 5:
                        return False
                return True
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True          # alive, owned by somebody else
        except Exception:                                   # noqa: BLE001
            return None

    @classmethod
    def clear_stale_lock(cls, iso_path):
        """Remove a writer lock whose owner is provably gone, without opening
        the ISO. Returns the holder line it removed, or None.

        Split out of `_reclaim_if_dead` 2026-09-19 so the SUITE can call it.
        `run_tests.py` ends its Studio with `terminate()`, which on Windows is
        TerminateProcess and cannot be caught, so the lock outlives the run --
        four times in one day. The next writer on Windows heals it here on its
        way past, but `_pid_alive` deliberately answers None across the
        WSL/Windows boundary (the pid spaces are unrelated), so from WSL the
        lock is never reclaimed and every writer refuses over a holder that no
        longer exists. Clearing it belongs at the end of the run that made it,
        where the platform matches.

        "Provably gone" is the whole contract: a None from `_pid_alive` is not
        rounded down, and nothing here removes a lock it merely suspects. That
        would hand the disc to a second writer -- by way of the lock.
        """
        lock = iso_path + cls.LOCK_SUFFIX
        try:
            with open(lock, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            return None
        m = re.search(r"pid (\d+) on (posix|windows)", text)
        if not m:
            return None
        if cls._holder_alive(text) is not False:
            return None
        try:
            os.remove(lock)
        except OSError:
            return None
        return text.strip()

    def _reclaim_if_dead(self):
        """Take over a lock whose owner is provably gone. True if reclaimed.

        This exists because `close()` is not the only way a writer ends.
        run_tests.py stops the Studio it started with `started.terminate()` --
        SIGTERM on POSIX, TerminateProcess on Windows, and the latter cannot
        be caught at all -- so atexit never runs and the lockfile outlives the
        process. Measured: a Studio killed that way left the lock behind, and
        without this the NEXT suite run would refuse to open the working disc
        over a holder that no longer exists.
        """
        try:
            with open(self._lock_path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            return False
        m = re.search(r"pid (\d+) on (posix|windows)", text)
        if not m:
            return False
        pid, plat = int(m.group(1)), m.group(2)
        if self._holder_alive(text) is not False:
            return False
        try:
            os.remove(self._lock_path)
        except OSError:
            return False
        # Said out loud. A guard that silently takes over is indistinguishable
        # from a guard that is not there, and this is the one path where it
        # deletes somebody else's file.
        print("  reclaimed a stale writer lock on %s (pid %d on %s is gone)"
              % (os.path.basename(self.path), pid, plat))
        return True

    def _take_writer_lock(self, _retry=True):
        """Claim sole write access, or raise PermissionError.

        PermissionError specifically, and not a new exception type: the
        Studio already degrades gracefully when the working ISO cannot be
        opened for writing (Session.open catches exactly this and comes up
        read-only rather than failing to start), and "another writer has it"
        is the same situation for a user as "the emulator has it" -- browse
        yes, write no. Session._locked_reason tells the three causes apart so
        the message is not the emulator's.
        """
        try:
            fd = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if _retry and self._reclaim_if_dead():
                return self._take_writer_lock(_retry=False)
            try:
                with open(self._lock_path, encoding="utf-8") as fh:
                    held = fh.read().strip() or "(empty)"
            except OSError:
                held = "(unreadable)"
            raise PermissionError(
                "the working ISO is open for writing by another process: %s. "
                "Two writers are not serialised by anything, so this one is "
                "refused. If nothing is running, delete %s"
                % (held, self._lock_path))
        with os.fdopen(fd, "w") as fh:
            fh.write("pid %d on %s (%s), opened %s\n"
                     % (os.getpid(), "windows" if os.name == "nt" else "posix",
                        self._holder_image(),
                        time.strftime("%Y-%m-%d %H:%M:%S")))
        self._lock_is_ours = True
        # close() is not called for us. Several scripts do
        # `g = Game(MODDED_ISO, writable=True)` and simply exit -- the
        # interpreter closes the file descriptor but nothing calls close(), so
        # without this the FIRST such run would strand the lock and every
        # later writer, the Studio included, would refuse over a disc nobody
        # holds. That is a worse failure than the one this lock prevents, and
        # it would be blamed on the lock rather than on the script.
        #
        # atexit covers a normal exit and an unhandled exception. It does not
        # cover SIGKILL or a power cut, and nothing does -- that is the stale
        # lock the refusal message tells a human how to clear.
        atexit.register(self._drop_writer_lock)

    def _drop_writer_lock(self):
        # Only the process that WON the create may remove it. A refused opener
        # must not delete the holder's lock on its way out -- that would hand
        # the disc to a third process and make the guard undo itself.
        if not self._lock_is_ours:
            return
        self._lock_is_ours = False
        try:
            atexit.unregister(self._drop_writer_lock)
        except Exception:                                  # noqa: BLE001
            pass
        try:
            os.remove(self._lock_path)
        except OSError:
            pass

    def close(self):
        if self._f and not self._f.closed:
            self._f.close()
        # After the handle, so the disc is never lockable while we still hold
        # it open. close() is the handover path too -- the Studio closes the
        # working ISO for PCSX2 -- and releasing there is correct: we are
        # genuinely no longer a writer.
        self._drop_writer_lock()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def read_at(self, offset, length):
        # Looped because the handle is unbuffered: a raw read is allowed to
        # return fewer bytes than asked for, and the buffered layer that used
        # to hide that is gone. A genuinely short read -- EOF -- still raises,
        # and `Session.level` depends on that message ("short read") to refuse
        # caching a half-parsed level.
        chunks = []
        got = 0
        with self._io:
            self._f.seek(offset)
            while got < length:
                chunk = self._f.read(length - got)
                if not chunk:
                    break
                chunks.append(chunk)
                got += len(chunk)
        if got != length:
            raise IOError(
                "short read at 0x%X: wanted %d got %d" % (offset, length, got)
            )
        return chunks[0] if len(chunks) == 1 else b"".join(chunks)

    def write_at(self, offset, data):
        if not self.writable:
            raise IOError("ISO opened read-only: %s" % self.path)
        with self._io:
            self._f.seek(offset)
            # Same reason as read_at: a raw write may be short. Silently
            # writing part of a patched file would corrupt an archive in a way
            # nothing downstream checks.
            view = memoryview(data)
            while view:
                n = self._f.write(view)
                if not n:
                    raise IOError("short write at 0x%X: %d byte(s) left"
                                  % (offset, len(view)))
                view = view[n:]

    def flush(self):
        if self.writable:
            with self._io:
                self._f.flush()
                os.fsync(self._f.fileno())

    # ------------------------------------------------------------------ add
    #
    # REQ-069: claimed by rdr-fmt under rule 3 for M21. Adding a NEW file does
    # not breach the rule above -- nothing existing moves or changes length.
    # The only length that changes belongs to a DIRECTORY.

    def free_runs(self, below=None):
        """Contiguous sector runs claimed by neither filesystem.

        Measured on the shipped disc: the DATA area is packed solid -- the
        last file ends on the final sector and there are zero gaps between
        files. What is free sits BELOW both filesystems: the largest run is
        190 sectors (380 KB) at LBA 66, between UDF's integrity descriptor
        and its anchor, and every one of those 389,120 bytes reads zero.

        **An earlier version of this docstring claimed 118 free sectors at
        268..385, and that was wrong: 268 is where UDF's partition starts.**
        The number came from an ISO9660-only walk that never looked at UDF,
        and it is withdrawn rather than quietly deleted, because the shape of
        the error -- a checker answering a narrower question than the one it
        was asked, and answering it confidently -- is the thing worth
        remembering. See `scripts/verify_iso_add.py`, which re-derives every
        boundary below from the disc on each run.
        """
        used = set()
        for f in self.files():
            n = (f.size + SECTOR - 1) // SECTOR
            used.update(range(f.lba, f.lba + n))
        for lba, length in self._dir_extents():
            n = (length + SECTOR - 1) // SECTOR
            used.update(range(lba, lba + n))
        used.update(range(0, 24))              # system area, volume descriptors
        # THIS IS A UDF BRIDGE DISC, and ignoring that is how the first
        # version of this function reported 474 KB of free space that does
        # not exist. An ISO9660 walk sees files and directories; it does NOT
        # see the UDF filesystem living alongside them. Writing a new file
        # into any of UDF's space destroys the UDF view of the volume.
        used.update(self._udf_sectors())
        hi = below if below is not None else self.total_sectors
        runs, start = [], None
        for s in range(24, hi):
            if s in used:
                if start is not None:
                    runs.append((start, s - start))
                    start = None
            elif start is None:
                start = s
        if start is not None:
            runs.append((start, hi - start))
        return sorted(runs, key=lambda r: -r[1])

    def _udf_sectors(self):
        """Every sector UDF claims, read from UDF's own declared extents.

        **Tag-sniffing is not enough, and that was this function's first
        bug.** It used to mark only sectors whose first 16 bytes parse as a
        descriptor tag, which misses every sector that is INSIDE a declared
        extent but carries no tag of its own. On the shipped disc that is
        38..47 and 54..63 -- the untagged tails of the two 16-sector Volume
        Descriptor Sequences, which stop at a TerminatingDescriptor long
        before the extent does -- and sector 274, an untagged gap between two
        FileIdentifierDescriptors inside the partition. All three were
        reported as free space. Nothing had been written into them yet,
        because `plan_add` takes the LARGEST run and a 190-sector run sorted
        ahead of them; the fault was one allocation away from real.

        So the extents are read from the structures that declare them:

            AnchorVolumeDescriptorPointer at 256   -> Main + Reserve VDS
            PartitionDescriptor                    -> start + length
            the LogicalVolumeIntegrityDescriptor extent named by the LVD

        and tag-sniffing stays on TOP of that as a backstop, not instead of
        it. A false positive costs one sector; a false negative corrupts a
        filesystem, so both are kept.
        """
        out = set()

        # 1. The two Volume Descriptor Sequences, from the anchor that names
        #    them. Whole declared extent, tagged or not.
        for anchor in (256, self.total_sectors - 1):
            out.add(anchor)
            try:
                b = self.read_at(anchor * SECTOR, 40)
            except Exception:
                continue
            if struct.unpack_from("<H", b, 0)[0] != 2:      # not an AVDP
                continue
            for off in (16, 24):                            # Main, Reserve
                length, lba = struct.unpack_from("<II", b, off)
                n = (length + SECTOR - 1) // SECTOR
                if 0 < n < self.total_sectors and lba < self.total_sectors:
                    out.update(range(lba, min(lba + n, self.total_sectors)))

        # 2. The partition itself. UDF file data is partition-relative, so
        #    every sector from its start to its end is UDF's -- including the
        #    ones no FileEntry happens to point at today.
        for s in sorted(out):
            try:
                b = self.read_at(s * SECTOR, 196)
            except Exception:
                continue
            if struct.unpack_from("<H", b, 0)[0] != 5:      # not a PD
                continue
            start = struct.unpack_from("<I", b, 188)[0]
            length = struct.unpack_from("<I", b, 192)[0]
            if start < self.total_sectors and 0 < length <= self.total_sectors:
                out.update(range(start, min(start + length, self.total_sectors)))

        # 3. The backstop: anything that still looks like a tag.
        TAGS = {1, 2, 3, 4, 5, 6, 7, 8, 9, 256, 257, 261, 266}
        for s in range(16, min(self.total_sectors, 1024)):
            try:
                b = self.read_at(s * SECTOR, 16)
            except Exception:
                break
            tag = struct.unpack_from("<H", b, 0)[0]
            if tag in TAGS and struct.unpack_from("<I", b, 12)[0] < self.total_sectors:
                out.add(s)
        return out

    def _dir_extents(self):
        """(lba, declared_length) for every directory, root included."""
        pvd = self.read_at(16 * SECTOR, SECTOR)
        root = (struct.unpack_from("<I", pvd, 158)[0],
                struct.unpack_from("<I", pvd, 166)[0])
        out, seen, stack = [], set(), [root]
        while stack:
            lba, length = stack.pop()
            if lba in seen:
                continue
            seen.add(lba)
            out.append((lba, length))
            for rec in self._records(lba, length):
                if rec["dir"] and rec["name"] not in ("\x00", "\x01", ""):
                    stack.append((rec["lba"], rec["length"]))
        return out

    def _records(self, lba, length):
        """Directory records in the extent at `lba`."""
        blk = self.read_at(lba * SECTOR, length)
        off = 0
        while off < len(blk):
            L = blk[off]
            if L == 0:
                nxt = ((off // SECTOR) + 1) * SECTOR
                if nxt >= len(blk):
                    return
                off = nxt
                continue
            nl = blk[off + 32]
            yield dict(
                off=off, len=L,
                lba=struct.unpack_from("<I", blk, off + 2)[0],
                length=struct.unpack_from("<I", blk, off + 10)[0],
                dir=bool(blk[off + 25] & 0x02),
                name=blk[off + 33:off + 33 + nl].decode("latin-1", "replace"))
            off += L

    def plan_add(self, dir_path, name, size, grow=False):
        """Where a new file WOULD go. Computes, writes nothing.

        Returns a dict carrying every byte offset the write would touch, so a
        caller can inspect the whole operation before any of it happens --
        and so a refusal can quote numbers instead of saying "no room".
        """
        pvd = self.read_at(16 * SECTOR, SECTOR)
        root_lba = struct.unpack_from("<I", pvd, 158)[0]
        root_len = struct.unpack_from("<I", pvd, 166)[0]

        parent = None
        want = dir_path.strip("/").upper()
        for rec in self._records(root_lba, root_len):
            if rec["dir"] and rec["name"].upper() == want:
                parent = rec
                break
        if parent is None:
            return dict(ok=False, reason="no directory %r in the root" % dir_path)

        iso_name = name.upper()
        if ";" not in iso_name:
            iso_name += ";1"
        rec_len = 33 + len(iso_name)
        rec_len += rec_len & 1                      # records are even-padded

        alloc = (parent["length"] + SECTOR - 1) // SECTOR * SECTOR
        dir_room = alloc - parent["length"]

        need = (size + SECTOR - 1) // SECTOR

        # ONE scan, not three. This used to call free_runs() on the line below
        # and TWICE more in the dict -- `X[0] if X else None` evaluates X twice
        # by construction. Cheap while the handle was buffered; since REQ-077
        # made it unbuffered each scan is ~1,000 raw 16-byte reads.
        runs = self.free_runs()
        fits = [r for r in runs if r[1] >= need]

        # Growth: when nothing on the volume is big enough, the file can go
        # PAST the end of the image. See grow(). M21's first half tops out at
        # the largest free run (380 KB on the shipped disc) and the median
        # LVLS_SNG level is 5.37 MB, so this is the difference between a .dat
        # the app lists and one a level could live in.
        grow_by = 0
        if fits:
            data_lba = fits[0][0]
        elif grow:
            # data lands at the old end; the relocated backup anchor takes the
            # sector after it, so the image gains need + 1.
            data_lba = self.total_sectors
            grow_by = need + 1
        else:
            data_lba = None

        return dict(
            ok=(data_lba is not None) and dir_room >= rec_len,
            name=iso_name, bytes=size, sectors_needed=need,
            data_lba=data_lba,
            largest_free_run=runs[0] if runs else None,
            grow_by=grow_by,
            grows=bool(grow_by),
            plan_grow=self.plan_grow(grow_by) if grow_by else None,
            parent=dict(name=parent["name"], lba=parent["lba"],
                        declared=parent["length"], allocated=alloc,
                        room=dir_room),
            record_bytes=rec_len,
            writes=[
                dict(what="file data",
                     at=(data_lba * SECTOR) if data_lba is not None else None,
                     bytes=size),
                dict(what="directory record",
                     at=parent["lba"] * SECTOR + parent["length"],
                     bytes=rec_len),
                dict(what="parent data_length in the root record",
                     at=root_lba * SECTOR + parent["off"] + 10, bytes=8),
            ],
            unchanged=["path table (directories only)",
                       "every existing file extent"]
            + ([] if grow_by else ["PVD total_sectors (no growth needed)"]),
        )

    def add(self, dir_path, name, data, dry=True, lba=None, grow=False):
        """Place a NEW file on the volume. `dry` defaults to TRUE.

        The default is deliberate and matches `api.is_dry`: an omitted flag
        PLANS, it never writes. The opposite default has cost this repo a
        boot test more than once, and a reply to a dry run is otherwise
        indistinguishable from a reply to a write.

        Three writes, nothing relocated:

            1. the file's data, into free sectors
            2. a directory record appended inside the parent's extent
            3. the parent's data_length, in the record that names it

        **Why this does not breach the no-move rule.** The class docstring
        says this object never changes a file's LBA or length, and that is
        load-bearing. A NEW file is not an existing one; no existing extent
        moves or resizes; the only length that changes belongs to a
        DIRECTORY. Anything that would move an existing file is out of
        scope -- see REQ-069.

        **Where the space comes from, and what it is not.** `free_runs()`
        excludes UDF: this is a UDF BRIDGE disc and UDF declares ZERO free
        blocks in its partition (LogicalVolumeIntegrityDescriptor
        FreeSpaceTable = 0, no space table, no bitmap). The usable space is
        the ~221 zero-filled sectors BELOW the UDF partition, largest run
        190 sectors at lba 66. A file there is invisible to UDF by
        construction, because UDF extents are partition-relative -- which is
        acceptable only because the game reads ISO9660: `SLUS_205.00`
        hardcodes `cdrom0:\\STREAMS.DAT;1`, and that `;1` is an ISO9660
        version suffix that UDF file identifiers do not have. See
        `research/probe_fs_iso_or_udf.py`. **If that ever stops being true,
        this function is wrong.**
        """
        plan = self.plan_add(dir_path, name, len(data), grow=grow)
        if not plan.get("ok"):
            return dict(ok=False, dry=bool(dry), written=False, plan=plan)
        if dry:
            return dict(ok=True, dry=True, written=False, plan=plan)
        if not self.writable:
            raise IOError("ISO opened read-only: %s" % self.path)

        # Grow FIRST, so the sectors the record will point at exist before
        # anything points at them. A directory record naming an extent past
        # the end of the image is a corrupt volume; the reverse order is a
        # volume that is merely larger than it needs to be.
        if plan.get("grow_by"):
            g = self.grow(plan["grow_by"], dry=False)
            if not g.get("written"):
                return dict(ok=False, dry=False, written=False,
                            reason="grow refused: %s" % g.get("reason"),
                            plan=plan)

        target = plan["data_lba"] if lba is None else lba
        parent = plan["parent"]
        iso_name = plan["name"]

        # Clone a sibling's fixed fields rather than inventing them: the
        # recording timestamp and volume sequence number are the volume's,
        # not ours, and a hand-built value is a guess where a copy is a fact.
        sibling = None
        for rec in self._records(parent["lba"], parent["declared"]):
            if not rec["dir"]:
                sibling = rec
                break
        if sibling is None:
            raise ValueError("%s has no file record to model the new one on"
                             % dir_path)
        sib = self.read_at(parent["lba"] * SECTOR + sibling["off"],
                           sibling["len"])

        rec = bytearray(plan["record_bytes"])
        rec[0] = plan["record_bytes"]
        rec[1] = 0
        struct.pack_into("<I", rec, 2, target)          # extent, little
        struct.pack_into(">I", rec, 6, target)          # extent, big
        struct.pack_into("<I", rec, 10, len(data))      # length, little
        struct.pack_into(">I", rec, 14, len(data))      # length, big
        rec[18:25] = sib[18:25]                         # recording datetime
        rec[25] = 0                                     # flags: a plain file
        rec[26] = 0
        rec[27] = 0
        rec[28:32] = sib[28:32]                         # volume sequence
        rec[32] = len(iso_name)
        rec[33:33 + len(iso_name)] = iso_name.encode("ascii")

        # A directory record may not straddle a sector boundary.
        end_off = parent["declared"]
        if (end_off % SECTOR) + len(rec) > SECTOR:
            raise ValueError(
                "the new record would straddle a sector boundary at %d; "
                "the extent needs relocating, which is out of scope"
                % end_off)

        self.write_at(target * SECTOR, data)
        pad = (SECTOR - (len(data) % SECTOR)) % SECTOR
        if pad:
            self.write_at(target * SECTOR + len(data), b"\x00" * pad)
        self.write_at(parent["lba"] * SECTOR + end_off, bytes(rec))

        new_len = parent["declared"] + len(rec)
        root_at = plan["writes"][2]["at"]
        self.write_at(root_at, struct.pack("<I", new_len)
                      + struct.pack(">I", new_len))
        self.flush()
        self._files = {}
        self._walk()
        return dict(ok=True, dry=False, written=True, applied=True,
                    plan=plan, lba=target, bytes=len(data))

    def remove_last(self, dir_path, name, dry=True):
        """Undo an `add`: drop the LAST record in a directory. `dry` is TRUE.

        **Deliberately only the last record, and it refuses otherwise.** A
        general remove has to shift every following record back and rewrite
        the tail of the extent, which is the kind of edit that silently
        corrupts a neighbour -- the same shape as the `.tbl` prefix-dedup
        hazard from M10, where moving one record rewrote a different file's
        name. Undoing our own append needs none of that: the record is last,
        so removing it is a truncation.

        This exists because `add()` without it is a one-way door. An
        acceptance test that writes the shared ISO and cannot put it back
        leaves the disc dirty for whichever lane runs next, which the
        contract forbids and which M15's revert bug did for a whole evening.

        The file's data sectors are zeroed, not merely unlinked: leaving a
        stale archive body in free space would make the next `free_runs()`
        see sectors that look occupied by nothing.
        """
        pvd = self.read_at(16 * SECTOR, SECTOR)
        root_lba = struct.unpack_from("<I", pvd, 158)[0]
        root_len = struct.unpack_from("<I", pvd, 166)[0]
        parent = None
        want = dir_path.strip("/").upper()
        for rec in self._records(root_lba, root_len):
            if rec["dir"] and rec["name"].upper() == want:
                parent = rec
                break
        if parent is None:
            return dict(ok=False, reason="no directory %r" % dir_path)

        iso_name = name.upper()
        if ";" not in iso_name:
            iso_name += ";1"
        recs = list(self._records(parent["lba"], parent["length"]))
        if not recs:
            return dict(ok=False, reason="empty directory")
        last = recs[-1]
        if last["name"].upper() != iso_name:
            return dict(ok=False,
                        reason="%s is not the last record in %s (last is %r); "
                               "removing a middle record would have to shift "
                               "the tail and is out of scope"
                               % (iso_name, dir_path, last["name"]))

        plan = dict(name=iso_name, parent=dir_path,
                    record_at=parent["lba"] * SECTOR + last["off"],
                    record_bytes=last["len"],
                    data_lba=last["lba"], data_bytes=last["length"],
                    new_parent_length=parent["length"] - last["len"])
        if dry:
            return dict(ok=True, dry=True, written=False, plan=plan)
        if not self.writable:
            raise IOError("ISO opened read-only: %s" % self.path)

        self.write_at(plan["record_at"], b"\x00" * last["len"])
        sectors = (last["length"] + SECTOR - 1) // SECTOR
        self.write_at(last["lba"] * SECTOR, b"\x00" * (sectors * SECTOR))
        n = plan["new_parent_length"]
        self.write_at(root_lba * SECTOR + parent["off"] + 10,
                      struct.pack("<I", n) + struct.pack(">I", n))
        self.flush()
        self._files = {}
        self._walk()
        return dict(ok=True, dry=False, written=True, plan=plan)

    # ----------------------------------------------------------------- grow
    #
    # M21's second half. The first half places a file in space that already
    # exists -- 380 KB on the shipped disc, against a 5.37 MB median level --
    # so a real level needs the image itself to get bigger.

    @staticmethod
    def _udf_retag(sector, new_lba):
        """A UDF descriptor sector relocated to `new_lba`.

        A descriptor carries the sector it lives at INSIDE its own tag, so a
        byte copy to a new home is not a valid descriptor: `TagLocation` (12:16)
        still names the old sector and `TagChecksum` (byte 4) no longer agrees.
        The checksum is the sum of the tag's first 16 bytes with byte 4 itself
        left out, mod 256 -- verified against six descriptors on the shipped
        disc (both anchors, PVD, PartitionDescriptor, LVD, LVID), all matching.

        `DescriptorCRC` (8:10) covers the 2,032-byte BODY, which a move does
        not touch -- confirmed: both shipped anchors carry the same CRC
        (0xb51e) and differ only in these two fields.
        """
        b = bytearray(sector)
        struct.pack_into("<I", b, 12, new_lba)
        b[4] = 0
        b[4] = sum(b[i] for i in range(16) if i != 4) & 0xFF
        return bytes(b)

    def plan_grow(self, sectors):
        """What growing by `sectors` would touch. Computes, writes nothing."""
        old, new = self.total_sectors, self.total_sectors + sectors
        return dict(
            ok=sectors > 0,
            sectors=sectors, old_total=old, new_total=new,
            old_bytes=old * SECTOR, new_bytes=new * SECTOR,
            anchor_from=old - 1, anchor_to=new - 1,
            writes=[
                dict(what="image length", at=None, bytes=sectors * SECTOR),
                dict(what="PVD volume_space_size, both endians",
                     at=16 * SECTOR + 80, bytes=8),
                dict(what="backup anchor, relocated and retagged",
                     at=(new - 1) * SECTOR, bytes=SECTOR),
                dict(what="old backup anchor, zeroed",
                     at=(old - 1) * SECTOR, bytes=SECTOR),
            ],
            unchanged=[
                "the anchor at 256",
                "both UDF Volume Descriptor Sequences (32..63)",
                "the UDF partition (it does NOT cover the new space)",
                "the path table (directories only)",
                "every existing file's LBA and length",
            ],
        )

    def grow(self, sectors, dry=True):
        """Make the image `sectors` bigger. `dry` defaults to TRUE.

        Four writes and nothing moves:

            1. the file gets longer, zero-filled
            2. the backup anchor is re-tagged onto the new final sector
            3. the old backup anchor is zeroed
            4. the PVD's volume_space_size is updated, both endians

        **The new space is outside the UDF partition, deliberately.** UDF's
        partition ends at 1,142,814 on the shipped disc and is NOT extended
        here, so UDF cannot address the new sectors -- exactly the position the
        first half's file at lba 66 is already in, and acceptable for the same
        reason: the game reads ISO9660 (REQ-070). Extending the partition, its
        space bitmap and the LVID is several times the work to satisfy a
        filesystem the console never asks.

        **The old anchor is zeroed rather than left.** A stale AnchorVolume-
        DescriptorPointer mid-volume is harmless to a reader -- which looks at
        256 and at the last sector -- but `_udf_sectors()` sniffs tags as a
        backstop and would report it as occupied structure forever after.
        """
        if sectors <= 0:
            return dict(ok=False, reason="grow needs a positive sector count")
        plan = self.plan_grow(sectors)
        if dry:
            return dict(ok=True, dry=True, written=False, plan=plan)
        if not self.writable:
            raise IOError("ISO opened read-only: %s" % self.path)

        old_total = self.total_sectors
        new_total = plan["new_total"]
        anchor = self.read_at((old_total - 1) * SECTOR, SECTOR)
        if struct.unpack_from("<H", anchor, 0)[0] != 2:
            return dict(ok=False, written=False,
                        reason="sector %d is not an AnchorVolumeDescriptor"
                               "Pointer; refusing to guess where the backup "
                               "anchor lives" % (old_total - 1))

        # ORDER IS THE GUARANTEE, and the lock does not provide it. A second
        # writer is excluded by the writer lock (REQ-078); this process DYING
        # mid-operation is not, and truncation is the one operation here whose
        # partial result is a structurally invalid image rather than a damaged
        # file. So every intermediate state is arranged to be benign:
        #
        #   extend the image      -> longer than the PVD claims: slack, unread
        #   write the NEW anchor  -> two valid anchors, one of them stale
        #   update the PVD        -> descriptors now agree with the length
        #   zero the OLD anchor   -> last, because until the PVD is updated a
        #                            reader may still be using it
        #
        # The dangerous order is the intuitive one -- fix the descriptors and
        # then change the length. Dying in THAT window leaves a PVD claiming a
        # size the file does not have, and every reader including verify_disc
        # trusts the PVD. (rdr-app raised this; shrink_to below was already
        # descriptors-first-truncate-last, and grow used to zero the old
        # anchor before updating the PVD, which is the swap made here.)
        with self._io:
            self._f.truncate(new_total * SECTOR)
        self.write_at((new_total - 1) * SECTOR,
                      self._udf_retag(anchor, new_total - 1))
        self.write_at(16 * SECTOR + 80,
                      struct.pack("<I", new_total) + struct.pack(">I", new_total))
        self.write_at((old_total - 1) * SECTOR, b"\x00" * SECTOR)
        self.flush()
        self.total_sectors = new_total
        self._files = {}
        self._walk()
        return dict(ok=True, dry=False, written=True, plan=plan,
                    total_sectors=new_total)

    def shrink_to(self, new_total, dry=True):
        """Undo a grow: truncate back to `new_total` sectors. `dry` is TRUE.

        **This is the dangerous half of the pair and it is guarded like it.**
        `grow` adds zeroes; this destroys whatever is above the cut, and a cut
        computed one sector low takes a file's last sector with nothing reading
        it back. So it REFUSES unless the space it is about to drop is claimed
        by nothing -- every file, every directory extent and every UDF
        structure is checked against the cut, and the only thing allowed to
        live above it is the backup anchor, which this moves.
        """
        if new_total >= self.total_sectors:
            return dict(ok=False, reason="shrink_to(%d) does not shrink %d"
                                         % (new_total, self.total_sectors))
        if new_total < 24:
            return dict(ok=False, reason="refusing to cut into the volume "
                                         "descriptors")
        doomed = set(range(new_total - 1, self.total_sectors))

        claimed = {}
        for f in self.files():
            n = (f.size + SECTOR - 1) // SECTOR
            for sec in range(f.lba, f.lba + n):
                if sec in doomed:
                    claimed.setdefault(f.path, sec)
        for lba, length in self._dir_extents():
            n = (length + SECTOR - 1) // SECTOR
            for sec in range(lba, lba + n):
                if sec in doomed:
                    claimed.setdefault("<directory at %d>" % lba, sec)
        for sec in self._udf_sectors():
            if sec in doomed and sec != self.total_sectors - 1:
                claimed.setdefault("<UDF structure>", sec)
        if claimed:
            return dict(ok=False, reason="refusing: %d sector(s) at or above "
                                         "%d are still claimed"
                                         % (len(claimed), new_total - 1),
                        claimed=claimed)

        plan = dict(old_total=self.total_sectors, new_total=new_total,
                    anchor_from=self.total_sectors - 1,
                    anchor_to=new_total - 1,
                    bytes_dropped=(self.total_sectors - new_total) * SECTOR)
        if dry:
            return dict(ok=True, dry=True, written=False, plan=plan)
        if not self.writable:
            raise IOError("ISO opened read-only: %s" % self.path)

        anchor = self.read_at((self.total_sectors - 1) * SECTOR, SECTOR)
        if struct.unpack_from("<H", anchor, 0)[0] != 2:
            return dict(ok=False, written=False,
                        reason="sector %d is not an AVDP" % (self.total_sectors - 1))
        self.write_at((new_total - 1) * SECTOR,
                      self._udf_retag(anchor, new_total - 1))
        self.write_at(16 * SECTOR + 80,
                      struct.pack("<I", new_total) + struct.pack(">I", new_total))
        self.flush()
        with self._io:
            self._f.truncate(new_total * SECTOR)
            self._f.flush()
            os.fsync(self._f.fileno())
        self.total_sectors = new_total
        self._files = {}
        self._walk()
        return dict(ok=True, dry=False, written=True, plan=plan,
                    total_sectors=new_total)

    # ------------------------------------------------------------- structure

    def _read_pvd(self):
        pvd = self.read_at(16 * SECTOR, SECTOR)
        if pvd[0] != 1 or pvd[1:6] != b"CD001":
            raise ValueError("not an ISO9660 image: %s" % self.path)
        self.volume_id = pvd[40:72].decode("ascii", "replace").strip()
        self.total_sectors = struct.unpack_from("<I", pvd, 80)[0]
        self._root_record = pvd[156:190]

    def _walk(self):
        """Depth-first walk of the directory tree into self._files."""
        root_lba = struct.unpack_from("<I", self._root_record, 2)[0]
        root_size = struct.unpack_from("<I", self._root_record, 10)[0]
        pending = [("", root_lba, root_size)]
        seen_lbas = set()

        while pending:
            prefix, lba, size = pending.pop()
            if lba in seen_lbas:
                continue
            seen_lbas.add(lba)
            data = self.read_at(lba * SECTOR, size)

            pos = 0
            while pos < len(data):
                rec_len = data[pos]
                if rec_len == 0:
                    # Records never straddle a sector; skip to the next one.
                    pos = (pos // SECTOR + 1) * SECTOR
                    continue
                rec = data[pos : pos + rec_len]
                pos += rec_len
                if len(rec) < 33:
                    continue

                child_lba = struct.unpack_from("<I", rec, 2)[0]
                child_size = struct.unpack_from("<I", rec, 10)[0]
                flags = rec[25]
                name_len = rec[32]
                raw_name = rec[33 : 33 + name_len]

                # 0x00 = ".", 0x01 = ".."
                if name_len == 1 and raw_name in (b"\x00", b"\x01"):
                    continue

                name = raw_name.decode("ascii", "replace")
                if ";" in name:  # strip the ISO9660 version suffix
                    name = name.split(";")[0]

                is_dir = bool(flags & 0x02)
                full = prefix + name
                self._files[full.upper()] = IsoFile(full, child_lba, child_size, is_dir)
                if is_dir:
                    pending.append((full + "/", child_lba, child_size))

    # ------------------------------------------------------------------ API

    def listdir(self):
        """All entries, directories included, sorted by path."""
        return sorted(self._files.values(), key=lambda e: e.path)

    def files(self):
        """Regular files only."""
        return [e for e in self.listdir() if not e.is_dir]

    def get(self, path):
        entry = self._files.get(path.replace("\\", "/").upper())
        if entry is None:
            raise KeyError("not on disc: %s" % path)
        return entry

    def read_file(self, path):
        e = self.get(path)
        return self.read_at(e.offset, e.size)

    def extract(self, path, dest):
        """Stream a file out of the ISO without loading it all into memory."""
        e = self.get(path)
        os.makedirs(os.path.dirname(str(dest)) or ".", exist_ok=True)
        remaining = e.size
        self._f.seek(e.offset)
        with open(dest, "wb") as out:
            while remaining > 0:
                chunk = self._f.read(min(1 << 22, remaining))
                if not chunk:
                    raise IOError("unexpected EOF extracting %s" % path)
                out.write(chunk)
                remaining -= len(chunk)
        return e.size
