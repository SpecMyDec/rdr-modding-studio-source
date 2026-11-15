"""Top-level handle on a Red Dead Revolver disc image."""

import fnmatch
import os

from .dave import Dave, IsoBacking, FileBacking, SlotOverflow
from .iso9660 import Iso9660

# Most gameplay files exist in BOTH SWAT.DAT and every level archive. Level
# copies appear to win at runtime, so a mod must patch every archive that has
# the file. These three CSVs are the known exceptions -- SWAT.DAT only.
SWAT_ONLY = ("levelflow.csv", "unlockables.csv", "invmasterlist.csv")


class Game:
    def __init__(self, iso_path, writable=False):
        self.iso = Iso9660(iso_path, writable=writable)
        self.path = str(iso_path)
        self.writable = writable
        self._cache = {}
        self._index = None

    def close(self):
        self.iso.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -------------------------------------------------------------- archives

    def archives(self):
        """Every Dave container on the disc, in disc order."""
        out = []
        for e in self.iso.files():
            if e.path.upper().endswith(".DAT"):
                out.append(e.path)
        return sorted(out)

    def archive(self, name):
        key = name.upper()
        if key not in self._cache:
            self._cache[key] = Dave(IsoBacking(self.iso, name), label=name)
        return self._cache[key]

    # ------------------------------------------------------------------ read

    def read(self, archive, filename):
        return self.archive(archive).read(filename)

    def toc(self, archive):
        return list(self.archive(archive))

    # ----------------------------------------------------------------- index

    def build_index(self, progress=None):
        """filename -> [archive, ...] across the whole disc.

        Takes a few seconds (62 archives, ~100k entries) so it is cached --
        but ONLY a complete one is cached. An archive that fails to open is
        skipped, and caching a run that skipped any of them makes the disc
        permanently claim it does not contain files that are sitting on it.

        Measured: close the handle under a Game (which is exactly what
        `Studio.hand_over` does to hand the disc to PCSX2) and every archive
        raises, so this built {} and kept it. `locate()` then answers [] for
        every file for the life of the object, and the routes that begin with
        a locate say "no still named 'mainmenu.ipu'" about a still that is
        right there -- which reads as the importer being broken. The rebuild
        costs seconds; being wrong costs a session.
        """
        if self._index is not None:
            return self._index
        index: dict[str, list[str]] = {}
        archives = self.archives()
        lost = 0
        for n, arc in enumerate(archives):
            if progress:
                progress(n + 1, len(archives), arc)
            try:
                d = self.archive(arc)
            except Exception:
                lost += 1
                continue
            for e in d:
                index.setdefault(e.name, []).append(arc)
        if not lost:
            self._index = index
        return index

    def locate(self, filename):
        """Which archives contain this file."""
        return self.build_index().get(filename.lower(), [])

    def glob(self, pattern):
        """Match a filename pattern across every archive."""
        idx = self.build_index()
        pat = pattern.lower()
        return {n: a for n, a in idx.items() if fnmatch.fnmatch(n, pat)}

    # ----------------------------------------------------------------- write

    def patch(self, archive, filename, fn, **kw):
        return self.archive(archive).edit(filename, fn, **kw)

    def patch_everywhere(self, filename, fn, dry=False, **kw):
        """Apply an edit to every copy of a file on the disc.

        Returns (applied, skipped) where skipped lists (archive, reason) for
        copies that had no slot headroom. A partial application is normal --
        e.g. jck2.cr in GHOST.DAT has zero headroom and keeps stock values.
        """
        applied, skipped = [], []
        for arc in self.locate(filename):
            d = self.archive(arc)
            try:
                e = d.find(filename)
                new = fn(d.read(e))
                if dry:
                    from .dave import SECTOR  # noqa: F401
                    import zlib
                    co = zlib.compressobj(9, 8, -15)
                    payload = new if not e.compressed else co.compress(new) + co.flush()
                    if len(payload) > e.slot:
                        raise SlotOverflow(e.name, len(payload), e.slot)
                    applied.append((arc, len(payload), e.slot))
                else:
                    # One fsync for the whole batch, not one per archive.
                    # `swat.guns` lives in 62 archives and `swat.strtbl` in 64,
                    # and on an ISO backing each flush is `os.fsync` over 4 GB.
                    kw.setdefault("flush", False)
                    used, slot = d.patch(e, new, **kw)
                    applied.append((arc, used, slot))
            except SlotOverflow as ex:
                skipped.append((arc, str(ex)))
            except Exception as ex:  # noqa: BLE001 - report, never abort the batch
                skipped.append((arc, "%s: %s" % (type(ex).__name__, ex)))
        if not dry:
            # Unconditional: copies written before a bad one still need to land.
            try:
                self.iso.flush()
            except Exception:
                pass
        return applied, skipped


def open_loose(path):
    """Open a .DAT sitting on disk rather than inside an ISO."""
    return Dave(FileBacking(path), label=os.path.basename(str(path)))
