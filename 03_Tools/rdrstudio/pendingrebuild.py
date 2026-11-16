"""Edits waiting for their archive to be rebuilt ("Rebuild and play").

An edit with no room in its slot used to rebuild the archive on the spot
(seconds for a level, minutes for swat.dat), interrupting every such edit.
Now it is QUEUED here instead: the new bytes are kept, the Studio reads them
back as if they were on the disc (Dave.overlay), and every queued archive is
rebuilt once, with all of its queued files, when the game is launched
(Play -> "Rebuild and play") or the queue is rebuilt by hand.

Kept on disk beside the Data Editor's folder, so it survives a restart:

    <data editor home>/pending/pending.json      {ARC: {name: {"blob", "add"}}}
    <data editor home>/pending/<blob>            the bytes
"""

import hashlib
import json
import os
import threading


def data_editor_home(modded_path):
    """The Data Editor's folder: Settings' choice, else beside the working disc."""
    from . import config as cfg
    custom = getattr(cfg, "DATA_EDITOR", "") or ""
    if custom:
        return custom
    return os.path.join(os.path.dirname(os.path.abspath(modded_path)), "data_editor")


class PendingRebuilds:
    def __init__(self, folder):
        self.folder = folder
        self.path = os.path.join(folder, "pending.json")
        self.lock = threading.RLock()
        # ARC -> {name: bytes}: replaced files (Dave.overlay reads these)
        self.replace = {}
        # ARC -> {name: bytes}: new files with no room for them
        self.add = {}
        self._new = []           # archives queued since the last take_new()
        self._load()

    # ------------------------------------------------------------ queries

    def archives(self):
        with self.lock:
            return sorted({a for a, d in self.replace.items() if d} | {a for a, d in self.add.items() if d})

    def count(self):
        with self.lock:
            return sum(len(d) for d in self.replace.values()) + sum(len(d) for d in self.add.values())

    def summary(self):
        with self.lock:
            return [dict(archive=a, files=sorted(set(self.replace.get(a, {})) | set(self.add.get(a, {}))))
                    for a in self.archives()]

    def overlay_for(self, archive):
        """The live dict Dave.overlay reads (created empty when needed)."""
        with self.lock:
            return self.replace.setdefault(archive.upper(), {})

    def take(self, archive):
        """(replace, add) for one archive, removed from the queue."""
        with self.lock:
            a = archive.upper()
            rep = dict(self.replace.get(a, {}))
            add = dict(self.add.get(a, {}))
            if a in self.replace:
                self.replace[a].clear()      # the dict a Dave may hold: emptied, not replaced
            self.add.pop(a, None)
            self.save()
            return rep, add

    def take_new(self):
        with self.lock:
            out, self._new = self._new, []
            return out

    # ------------------------------------------------------------ changes

    def queue(self, archive, name, data, add=False):
        with self.lock:
            a = archive.upper()
            n = name.lower()
            if add:
                self.add.setdefault(a, {})[n] = data
            else:
                self.replace.setdefault(a, {})[n] = data
            if a not in self._new:
                self._new.append(a)
            self.save()

    def drop(self, archive, name):
        with self.lock:
            a, n = archive.upper(), name.lower()
            hit = self.replace.get(a, {}).pop(n, None) is not None
            hit = (self.add.get(a, {}).pop(n, None) is not None) or hit
            if hit:
                self.save()
            return hit

    def clear(self):
        with self.lock:
            for d in self.replace.values():
                d.clear()
            self.add.clear()
            self._new = []
            self.save()

    # ------------------------------------------------------------ disk

    def save(self):
        with self.lock:
            index, blobs = {}, {}
            for kind, table in (("replace", self.replace), ("add", self.add)):
                for a, files in table.items():
                    for n, data in files.items():
                        blob = hashlib.sha1(data).hexdigest()
                        blobs[blob] = data
                        index.setdefault(a, {})[n] = dict(blob=blob, add=kind == "add", size=len(data))
            if not index and not os.path.isdir(self.folder):
                return
            os.makedirs(self.folder, exist_ok=True)
            for blob, data in blobs.items():
                p = os.path.join(self.folder, blob)
                if not os.path.exists(p):
                    with open(p + ".tmp", "wb") as fh:
                        fh.write(data)
                    os.replace(p + ".tmp", p)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(index, fh, indent=1)
            os.replace(tmp, self.path)
            for f in os.listdir(self.folder):
                if f not in blobs and f != "pending.json":
                    try:
                        os.remove(os.path.join(self.folder, f))
                    except OSError:
                        pass

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                index = json.load(fh)
        except (OSError, ValueError):
            return
        for a, files in index.items():
            for n, info in files.items():
                try:
                    with open(os.path.join(self.folder, info["blob"]), "rb") as fh:
                        data = fh.read()
                except OSError:
                    continue
                (self.add if info.get("add") else self.replace).setdefault(a, {})[n] = data
