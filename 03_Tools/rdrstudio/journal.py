"""Edit journal: undo/redo, and mods as durable, shareable artifacts.

Every write in the Studio funnels through here. An entry stores the file's
contents *after* the edit; the state *before* it is whatever the previous
entry for that same file held, falling back to the vanilla disc. Storing one
side instead of two halves the size and makes an inconsistent history
impossible to represent.

That same structure is the mod format: replaying the final state of every
touched file onto a fresh ISO reproduces the mod exactly, in any order.
"""

import contextlib
import base64
import hashlib
import json
import os
import time
from typing import Any

FORMAT = "rdrmod/1"


class Entry:
    __slots__ = ("seq", "when", "archive", "file", "label", "data", "group")

    def __init__(self, seq, archive, file, data, label="", when=None, group=None):
        self.seq = seq
        self.when = when or time.strftime("%Y-%m-%d %H:%M:%S")
        self.archive = archive
        self.file = file
        self.label = label
        self.data = data
        # Entries sharing a group undo together. One weapon edit touches 62
        # copies of swat.guns; that is one action to a user, not 62.
        self.group = group

    @property
    def key(self):
        return (self.archive, self.file)

    def to_json(self, with_data=True):
        d = dict(seq=self.seq, when=self.when, archive=self.archive,
                 file=self.file, label=self.label, size=len(self.data),
                 group=self.group,
                 sha256=hashlib.sha256(self.data).hexdigest()[:16])
        if with_data:
            d["data_b64"] = base64.b64encode(self.data).decode("ascii")
        return d

    @staticmethod
    def from_json(d):
        return Entry(d["seq"], d["archive"], d["file"],
                     base64.b64decode(d["data_b64"]), d.get("label", ""),
                     d.get("when"), d.get("group"))


class Journal:
    def __init__(self, vanilla_game=None, autosave_path=None):
        self.vanilla = vanilla_game
        self.autosave_path = autosave_path
        self.entries = []
        self.redo_stack = []
        self._seq = 0
        self.bases = {}         # (archive, file): its bytes before the first edit, for files the vanilla disc has not
        self._defer = 0          # >0 while a batch is in progress
        self._dirty = False

    # ----------------------------------------------------------- recording

    @contextlib.contextmanager
    def batch(self):
        """Record many edits, autosaving ONCE at the end.

        The session autosave writes the whole history -- every touched file,
        base64'd -- and that file is tens of megabytes on a working session.
        Doing it per entry made bulk operations quadratic in I/O: applying a
        650-file mod wrote a 28 MB document 650 times, about 18 GB, and took
        322 seconds against roughly 6 seconds of actual work.

        The save still happens if the body raises, because the edits it
        describes have already been written to the disc and losing the record
        of them is worse than losing the exception's tidiness.
        """
        self._defer += 1
        try:
            yield self
        finally:
            self._defer -= 1
            if self._defer == 0 and self._dirty:
                self._dirty = False
                self._autosave(force=True)

    def record(self, archive, file, data, label="", group=None):
        self._seq += 1
        e = Entry(self._seq, archive, file, data, label, group=group)
        self.entries.append(e)
        # A fresh edit invalidates anything that was undone.
        self.redo_stack = []
        self._autosave()
        return e

    def new_group(self):
        self._group_seq = getattr(self, "_group_seq", 0) + 1
        return "g%d" % self._group_seq

    def vanilla_state(self, archive, file):
        if self.vanilla is None:
            return None
        try:
            return self.vanilla.archive(archive).read(file)
        except Exception:
            return None

    def state_before(self, index):
        """What (archive, file) held just before entries[index] was applied."""
        e = self.entries[index]
        for prev in reversed(self.entries[:index]):
            if prev.key == e.key:
                return prev.data
        # a level only the mods folder has: what its file held before the first edit (Studio.write keeps it here)
        bases = getattr(self, "bases", None)
        if bases and e.key in bases:
            return bases[e.key]
        return self.vanilla_state(e.archive, e.file)

    # -------------------------------------------------------- undo / redo

    @property
    def can_undo(self):
        return bool(self.entries)

    @property
    def can_redo(self):
        return bool(self.redo_stack)

    def undo(self, write, read=None):
        """Undo one user-visible action: the whole trailing group, or one entry.

        write(archive, file, data) restores a previous state. read(archive, file) -> its bytes now, or None: when
        given, every file of the step must still hold what its entry says, or nothing is undone -- after a crash the
        history on disk can be behind the files (its save waits two seconds), and Ctrl+Z put back a stale copy over
        later work (2026-11-18).
        """
        if not self.entries:
            return None
        last = self.entries[-1]
        if read is not None:
            k = len(self.entries) - 1
            seen = set()
            while k >= 0:
                e = self.entries[k]
                if k < len(self.entries) - 1 and (e.group is None or e.group != last.group):
                    break
                if e.key not in seen:          # the newest entry of each file is the state it must be in
                    seen.add(e.key)
                    try:
                        now = read(e.archive, e.file)
                    except Exception:                       # noqa: BLE001
                        now = None
                    if now is not None and now != e.data:
                        raise ValueError(
                            "cannot undo %s :: %s -- the file is not as this history left it (changed outside it, or "
                            "the history was saved before the Studio closed); nothing was undone" % (e.archive, e.file))
                if e.group is None:
                    break
                k -= 1
        # every file of the step checked before any is written back: one with nothing to go back to stopped the undo
        # half way, the step's other files already undone (and that half state saved) -- 2026-11-18
        k = len(self.entries) - 1
        while k >= 0:
            e = self.entries[k]
            if k < len(self.entries) - 1 and (e.group is None or e.group != last.group):
                break
            if self.state_before(k) is None:
                raise ValueError("cannot undo %s :: %s -- no earlier copy of it to go back to (nothing was undone)"
                                 % (e.archive, e.file))
            if e.group is None:
                break
            k -= 1
        undone: list[Any] = []
        while self.entries:
            e = self.entries[-1]
            if undone and (e.group is None or e.group != last.group):
                break
            prev = self.state_before(len(self.entries) - 1)
            if prev is None:
                raise ValueError(
                    "cannot undo %s :: %s -- no vanilla copy to fall back to"
                    % (e.archive, e.file))
            write(e.archive, e.file, prev)
            self.entries.pop()
            undone.append(e)
            if e.group is None:
                break
        # Reverse so redo replays them in original order.
        self.redo_stack.append(list(reversed(undone)))
        # at once, not debounced: an undo left owed when the Studio closed came back as a live entry on the next
        # launch, and the next Ctrl+Z "restored" a file to the edit that had been undone (2026-11-18)
        self._autosave(force=True)
        return dict(entry=last, count=len(undone), label=last.label,
                    archive=last.archive, file=last.file)

    def redo(self, write):
        if not self.redo_stack:
            return None
        batch = self.redo_stack.pop()
        for e in batch:
            write(e.archive, e.file, e.data)
            self.entries.append(e)
        self._autosave(force=True)
        last = batch[-1]
        return dict(entry=last, count=len(batch), label=last.label,
                    archive=last.archive, file=last.file)

    # ------------------------------------------------------------- state

    def final_state(self):
        """(archive, file) -> the newest contents. This is the mod."""
        out = {}
        for e in self.entries:
            out[e.key] = e
        return out

    def touched_files(self):
        return sorted(self.final_state().keys())

    def history(self, limit=200):
        """One row per user-visible action, newest last. Groups collapse."""
        rows: list[dict[str, Any]] = []
        for e in self.entries[-limit:]:
            if (rows and e.group is not None and rows[-1]["group"] == e.group
                    and not rows[-1]["undone"]):
                rows[-1]["count"] += 1
                rows[-1]["size"] += len(e.data)
                continue
            rows.append(dict(e.to_json(with_data=False), undone=False, count=1))
        for batch in reversed(self.redo_stack):
            last = batch[-1]
            rows.append(dict(last.to_json(with_data=False), undone=True,
                             count=len(batch)))
        return rows

    # --------------------------------------------------------------- mods

    def to_mod(self, name, notes=""):
        return dict(
            format=FORMAT, name=name, notes=notes, game="SLUS-20500",
            created=time.strftime("%Y-%m-%d %H:%M:%S"),
            files=[e.to_json() for e in self.final_state().values()])

    def export(self, path, name, notes=""):
        os.makedirs(os.path.dirname(str(path)) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_mod(name, notes), f, indent=1)
        return path

    # Never rewrite the session document more often than this. `batch()` is the
    # explicit fix for a known bulk path; this is the floor that protects the
    # ones nobody remembered to wrap -- editing a weapon touches 62 archives
    # and a string 64, each of which was its own full rewrite.
    AUTOSAVE_MIN_INTERVAL = 2.0

    def _autosave(self, force=False):
        if not self.autosave_path:
            return
        if not force:
            if self._defer:
                # Inside a batch: remember a save is owed, let batch() do it.
                self._dirty = True
                return
            now = time.time()
            if now - getattr(self, "_last_autosave", 0) < self.AUTOSAVE_MIN_INTERVAL:
                self._dirty = True
                return
        self._dirty = False
        self._last_autosave = time.time()
        try:
            self.export(self.autosave_path, "_session",
                        "auto-saved working history")
        except Exception:
            pass  # never let bookkeeping break an edit

    def flush_autosave(self):
        """Write out a debounced save that is still owed.

        Called from the read paths the UI polls, so a deferred save always
        lands within a second or two of the last edit rather than waiting for
        whatever happens to write next.
        """
        if self._dirty and not self._defer:
            self._autosave(force=True)

    def load_session(self, path):
        """Restore a previous session's history (undo across restarts)."""
        if not os.path.exists(path):
            return 0
        with open(path) as f:
            doc = json.load(f)
        self.entries = [Entry.from_json(d) for d in doc.get("files", [])]
        for i, e in enumerate(self.entries):
            e.seq = i + 1
        self._seq = len(self.entries)
        self.redo_stack = []
        # new groups numbered after the restored ones: g1 again for the first new action joined the last session's g1
        # entries, and one Ctrl+Z took back both (2026-11-18)
        nums = [int(e.group[1:]) for e in self.entries if isinstance(e.group, str) and e.group[1:].isdigit()]
        self._group_seq = max([getattr(self, "_group_seq", 0)] + nums)
        return len(self.entries)


def read_mod(path):
    with open(path) as f:
        doc = json.load(f)
    if doc.get("format") != FORMAT:
        raise ValueError("not an rdrmod file: %s" % path)
    return doc


def apply_mod(doc, game):
    """Patch every file the mod describes into `game`.

    Returns (applied, failed). Never aborts partway on one bad file -- a mod
    built against a different disc revision should report everything wrong
    with it at once, not just the first problem.
    """
    from .dave import SlotOverflow
    applied, failed = [], []
    for f in doc.get("files", []):
        archive, name = f["archive"], f["file"]
        data = base64.b64decode(f["data_b64"])
        try:
            # flush=False: ONE fsync at the end instead of one per file. For an
            # ISO backing every flush is `os.fsync` on a 4 GB file, so applying
            # a mod spent nearly all its time in the kernel -- a 638-file mod
            # took over five minutes and timed the client out.
            used, slot = game.archive(archive).patch(name, data, flush=False)
            applied.append(dict(archive=archive, file=name, used=used, slot=slot))
        except SlotOverflow as ex:
            failed.append(dict(archive=archive, file=name, error=str(ex)))
        except Exception as ex:
            failed.append(dict(archive=archive, file=name,
                               error="%s: %s" % (type(ex).__name__, ex)))
    # Outside the loop, and unconditional: whatever was written before a bad
    # file still needs to reach the disk.
    try:
        game.iso.flush()
    except Exception:
        pass
    return applied, failed
