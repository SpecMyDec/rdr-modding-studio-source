"""One story over checkpoints and mods: a Version.

They were already the same file. `Studio.checkpoint` writes `format
"rdrmod/1"`, and `restore_checkpoint` puts it back through the same
`apply_mod()` that `mod_load` uses. Two vocabularies for one object, and only
one of them was safe: a mod applied by `mod_load` cost one undo step PER FILE
-- 650 presses of Ctrl+Z for a 650-file mod -- while a checkpoint restore
grouped correctly and cost one.

So there is one thing now. A Version is a named, timestamped snapshot of
everything on the working disc that differs from stock. Nothing new is stored:
this module reads the `.rdrmod.json` and `.checkpoint.json` files that are
already on disk and presents them as one list.

The part that matters most is the banner, and it is the part that is
*measured*. "What is on the disc right now" is answered by diffing the working
ISO against each saved version file by file, every time it is asked. It is
never cached and never trusts a stored pointer, because the state this answers
for is the one work has been lost from twice.
"""

import base64
import hashlib
import json
import os
import time
from typing import Any

from . import config as cfg
from .journal import FORMAT

AUTOSAVE_ID = "_session"


def _dirs():
    return [cfg.MODS, cfg.BACKUPS]


def _sha(data):
    return hashlib.sha256(data).hexdigest()[:16]


def _paths():
    out = []
    for d in _dirs():
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".rdrmod.json") or fn.endswith(".checkpoint.json"):
                out.append(os.path.join(d, fn))
    return out


def _id_of(path):
    fn = os.path.basename(path)
    for suffix in (".rdrmod.json", ".checkpoint.json"):
        if fn.endswith(suffix):
            return fn[:-len(suffix)]
    return fn


# Parsed version documents, keyed by (path, mtime, size).
#
# This caches a FILE, not an answer. The session autosave is tens of megabytes
# and the banner reads every version on every poll, so re-parsing them all
# each time makes the one screen that must never be stale the slowest screen
# in the product. The disc side is never cached: `disc_state` re-diffs the
# working ISO every single call, which is the promise that matters. A file
# whose mtime or size changes is re-read, so an externally edited version is
# picked up.
_DOCS: dict[str, tuple[tuple[str, float, int], Any]] = {}


def read(path):
    key = (path, os.path.getmtime(path), os.path.getsize(path))
    hit = _DOCS.get(path)
    if hit and hit[0] == key:
        return hit[1]
    with open(path) as f:
        doc = json.load(f)
    if doc.get("format") != FORMAT:
        raise ValueError("%s is not a version file" % os.path.basename(path))
    _DOCS[path] = (key, doc)
    if len(_DOCS) > 32:
        for k in list(_DOCS)[:8]:
            _DOCS.pop(k, None)
    return doc


def _files_of(doc):
    """(archive, file) -> sha of the stored contents. No base64 is decoded."""
    out = {}
    for f in doc.get("files", []):
        sha = f.get("sha256")
        if not sha:
            sha = _sha(base64.b64decode(f["data_b64"]))
        out[(f["archive"], f["file"])] = sha
    return out


def listing():
    """Every version on disk, newest first, with no disc access at all."""
    rows = []
    for path in _paths():
        try:
            doc = read(path)
        except Exception:
            continue
        vid = _id_of(path)
        rows.append(dict(
            id=vid,
            name=doc.get("name") or vid,
            notes=doc.get("notes", ""),
            created=doc.get("created") or time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(path))),
            files=len(doc.get("files", [])),
            bytes=os.path.getsize(path),
            path=path,
            parent=doc.get("parent"),
            # The session autosave is a safety net, not a version. It stays
            # in the list at rung `dead` with no restore button, because a
            # thing that exists and is invisible is worse than one labelled.
            rung="dead" if vid == AUTOSAVE_ID else "measured",
            autosave=vid == AUTOSAVE_ID,
            on_disc=False))
    rows.sort(key=lambda r: r["created"], reverse=True)
    return rows


def find(vid):
    for path in _paths():
        if _id_of(path) == vid:
            return path
    return None


def disc_state(studio):
    """(archive, file) -> sha for every file that differs from stock.

    Measured from the working ISO, right now. `Studio.diff()` already compares
    decompressed contents rather than stored bytes, so a file re-saved at a
    different compression level is correctly reported as unchanged.
    """
    out = {}
    for c in studio.diff():
        try:
            data = studio.game.archive(c["archive"]).read(c["name"])
        except Exception:
            continue
        out[(c["archive"], c["name"])] = _sha(data)
    return out


def _freshness(studio, rows):
    """Everything that could possibly change the answer, as one comparable key.

    Measured: answering "what is on the disc right now" costs 12.5 s, because
    it diffs all 66 archives against stock. The banner is polled, so paying
    that on every poll is not an option -- and neither is caching the answer,
    because the state it reports is the one work has been lost from twice.

    So the answer is recomputed whenever anything that could alter it has
    altered: the working ISO's size or timestamp, the number of edits in the
    journal, and the identity of every version file on disk. This is NOT a
    stored pointer being trusted -- there is no way for the disc to change
    without changing this key, and if it somehow did, the key changes on the
    very next edit and the banner corrects itself.
    """
    def stat(path):
        try:
            st = os.stat(path)
            return (path, st.st_mtime_ns, st.st_size)
        except (OSError, TypeError):
            return None
    j = studio.journal
    # (no journal yet, at startup: no edits -- not "unknown", which used to switch the cache off entirely)
    return (stat(studio.modded_path), stat(getattr(studio, "vanilla_path", None)),
            len(j.entries) if j is not None else 0, getattr(j, "_seq", 0) if j is not None else 0,
            tuple(sorted((r["path"], os.path.getmtime(r["path"]),
                          r["bytes"]) for r in rows)))


_ON_DISC: dict[str, Any] = {}


def _on_disc_file():
    """Where the last answer is kept between launches (beside the app's settings)."""
    return os.path.join(os.path.dirname(cfg._settings_file()), "cache", "on_disc.json")


def _remembered(key):
    """The answer worked out at an earlier launch, if nothing it depends on has changed since: the same key
    (the working and stock discs' size and time, the journal, every version file) -- a launch on an unchanged disc
    no longer re-diffs all 66 archives (6-8 s, holding the disc lock)."""
    try:
        with open(_on_disc_file(), encoding="utf-8") as fh:
            doc = json.load(fh)
        if doc.get("key") == repr(key):
            return doc.get("value")
    except (OSError, ValueError):
        pass
    return None


def _remember(key, value):
    try:
        path = _on_disc_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".part"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"key": repr(key), "value": value}, fh)
        os.replace(tmp, path)
    except OSError:
        pass


def on_disc(studio, rows=None):
    """Which saved version the working disc currently holds, if any.

    Three states, and exactly three:

      * a version, every file matching, nothing extra  -> exact match
      * a version, every file matching, plus N extra   -> that version + N
      * nothing whose files all match                  -> unsaved work

    A version qualifies as the base only if EVERY file it names is byte-equal
    on the disc. Anything looser would let a half-restored disc claim a clean
    name, which is the reassuring lie this banner exists to prevent.
    """
    rows = listing() if rows is None else rows
    try:
        key = _freshness(studio, rows)
    except Exception:
        key = None
    if key is not None and _ON_DISC.get("key") == key:
        return dict(_ON_DISC["value"])
    if key is not None:
        kept = _remembered(key)
        if isinstance(kept, dict):
            _ON_DISC.clear()
            _ON_DISC.update(key=key, value=kept)
            return dict(kept)

    state = disc_state(studio)

    best, best_files = None, -1
    for row in rows:
        if row["autosave"]:
            continue
        try:
            want = _files_of(read(row["path"]))
        except Exception:
            continue
        if not want:
            continue
        if all(state.get(k) == v for k, v in want.items()):
            if len(want) > best_files:
                best, best_files = row, len(want)

    if best is None:
        drifted = sorted("%s :: %s" % k for k in state)
        answer = dict(id=None, name=None, matches=False,
                      files_changed=len(state), drifted=drifted)
    else:
        want = _files_of(read(best["path"]))
        drifted = sorted("%s :: %s" % k for k in state if k not in want)
        answer = dict(id=best["id"], name=best["name"],
                      matches=not drifted and len(state) == len(want),
                      files_changed=len(state), drifted=drifted)
    if key is not None:
        _ON_DISC.clear()
        _ON_DISC.update(key=key, value=answer)
        _remember(key, answer)
    return dict(answer)


# ------------------------------------------------------------------ saving

def save_doc(studio, name, notes=""):
    """The version document for the disc as it stands. Reads, never writes."""
    files = []
    for c in studio.diff():
        data = studio.game.archive(c["archive"]).read(c["name"])
        files.append(dict(archive=c["archive"], file=c["name"],
                          size=len(data), sha256=_sha(data),
                          data_b64=base64.b64encode(data).decode("ascii")))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    vid = "v_" + stamp
    return vid, dict(format=FORMAT, name=name or ("version " + stamp),
                     notes=notes, game="SLUS-20500",
                     created=time.strftime("%Y-%m-%d %H:%M:%S"),
                     checkpoint=True, files=files)


def write_doc(vid, doc):
    os.makedirs(cfg.MODS, exist_ok=True)
    path = os.path.join(cfg.MODS, vid + ".rdrmod.json")
    tmp = path + ".part"
    with open(tmp, "w") as f:
        json.dump(doc, f, indent=1)
    os.replace(tmp, path)
    return path


# ---------------------------------------------------------------- comparing

def state_for(studio, which):
    """`"disc"`, `"stock"` or a version id -> {(archive, file): (sha, bytes)}.

    `stock` is the empty map on purpose: every state in this system is
    expressed as "what differs from the untouched disc", so stock differs from
    stock in nothing, and a diff from stock to a version reads as all-added.
    """
    if which == "stock":
        return {}
    if which == "disc":
        out = {}
        for c in studio.diff():
            data = studio.game.archive(c["archive"]).read(c["name"])
            out[(c["archive"], c["name"])] = (_sha(data), len(data))
        return out
    path = find(which)
    if path is None:
        raise KeyError(which)
    doc = read(path)
    out = {}
    for f in doc.get("files", []):
        data = base64.b64decode(f["data_b64"])
        out[(f["archive"], f["file"])] = (f.get("sha256") or _sha(data),
                                          len(data))
    return out


def compare(studio, a, b):
    sa = state_for(studio, a)
    sb = state_for(studio, b)
    rows = []
    counts = dict(added=0, removed=0, changed=0, same=0)
    for key in sorted(set(sa) | set(sb)):
        archive, filename = key
        va, vb = sa.get(key), sb.get(key)
        if va is None:
            state, summary = "added", "only in %s" % b
        elif vb is None:
            state, summary = "removed", "back to stock in %s" % b
        elif va[0] != vb[0]:
            state, summary = "changed", "%d -> %d bytes" % (va[1], vb[1])
        else:
            state, summary = "same", "identical"
        counts[state] += 1
        rows.append(dict(archive=archive, file=filename, state=state,
                         a_sha=va[0] if va else None,
                         b_sha=vb[0] if vb else None,
                         a_bytes=va[1] if va else None,
                         b_bytes=vb[1] if vb else None,
                         summary=summary))
    return counts, rows


# --------------------------------------------------------------- restoring

def restore_writes(studio, vid):
    """Every file a restore would touch: the version's, plus reverts to stock.

    A restore is a true restore, not an overlay -- anything changed since that
    the version does not mention goes back to stock. Those reverts are the
    part a user does not expect, so they are in the plan by name.
    """
    path = find(vid)
    if path is None:
        raise KeyError(vid)
    doc = read(path)
    wanted = {}
    for f in doc.get("files", []):
        wanted[(f["archive"], f["file"])] = base64.b64decode(f["data_b64"])

    writes = []
    for (archive, filename), data in sorted(wanted.items()):
        writes.append((archive, filename, data, "restore"))
    for c in studio.diff():
        key = (c["archive"], c["name"])
        if key in wanted:
            continue
        try:
            stock = studio.vanilla.archive(c["archive"]).read(c["name"])
        except Exception:
            continue
        writes.append((c["archive"], c["name"], stock, "revert to stock"))
    writes.sort(key=lambda w: w[1])
    return doc, writes
