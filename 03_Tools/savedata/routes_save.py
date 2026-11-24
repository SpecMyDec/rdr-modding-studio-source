"""Studio routes for the Save Editor tab.

Shaped like `rdrstudio/routes2.py` on purpose -- same `has_get`/`handle_get`
pair, same `api.respond` envelope -- so `studio.py` needed one delegation
block and nothing else.

    GET  api/save/cards            memory cards we can see, and which hold RDR
    GET  api/save/open?path=...    the six slots, decoded as far as it goes
    GET  api/save/fields           the field table, with its confidence column
    POST api/save/write            {path, slot, fields:{key: value}}

WRITING IS GUARDED THREE WAYS, and the second is the one worth reading.

1. A timestamped copy of the whole card lands in `06_Backups/saves/` before a
   single byte is written. Not the save -- the card, all 8 MB of it, because
   restoring a save into a card is a harder operation than putting the card
   back.

2. **PCSX2 must be provably absent, and "cannot tell" is not absent.**
   `_pcsx2_state` returns True / False / None exactly like
   `iso9660._pid_alive`, and a write proceeds only on a definite False. From
   WSL we cannot enumerate Windows processes at all, so the honest answer
   there is None and the route refuses rather than guessing. That is
   deliberately the annoying direction: a card written underneath a running
   emulator is corrupted on the emulator's next flush, and the failure
   surfaces hours later as a save that will not load.

3. Only fields in `rdrsave.SLOT_FIELDS` can be set, each at its own width. A
   caller cannot post an offset.

THERE IS NO `dry` FLAG. Every RDR route that has one defaults it to true, so
a caller that omits it is told "ok" and nothing happens -- see
[[routes2-dry-defaults-true]]. A save write is small, backed up, and refusable;
adding a flag whose default silently means "did nothing" would buy nothing and
cost the one thing this route has to get right.
"""

import datetime
import os
import shutil
import sys

from rdrstudio import api

from . import ps2mc
from . import rdrsave

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKUPS = os.path.join(REPO, "06_Backups", "saves")

# Where cards live on this machine. The Studio offers what it finds; it never
# guesses a path the caller did not ask for.
def _card_dirs():
    """PCSX2's memcard folders: beside the configured emulator (portable
    install), the default Documents\\PCSX2\\memcards, and any extra folders
    in RDR_MEMCARDS (separated by os.pathsep)."""
    dirs = []
    try:
        from rdrstudio import config as _cfg
        exe = getattr(_cfg, "PCSX2_EXE", "") or ""
        if exe:
            dirs.append(os.path.join(os.path.dirname(exe), "memcards"))
    except Exception:                                       # noqa: BLE001
        pass
    dirs.append(os.path.join(os.path.expanduser("~"), "Documents", "PCSX2",
                             "memcards"))
    # Common portable-install spots, on any drive.
    if os.name == "nt":
        for drive in "CDEFGH":
            for sub in (("Emulation", "PCSX2"), ("PCSX2",),
                        ("Program Files", "PCSX2")):
                dirs.append(os.path.join(drive + ":\\", *sub, "memcards"))
    dirs += [d for d in os.environ.get("RDR_MEMCARDS", "").split(os.pathsep)
             if d]
    dirs.append(os.path.join(REPO, "08_ForkLab", "savelab", "cards"))
    out = []
    for d in dirs:
        if d not in out:
            out.append(d)
    return out


CARD_DIRS = _card_dirs()


def _pcsx2_state():
    """True running / False not running / None could not tell."""
    if os.name != "nt":
        # A WSL process cannot enumerate Windows processes. Saying "not
        # running" from here would be an answer this probe cannot give --
        # the same mistake as reading a window count from the wrong side.
        return None
    try:
        import subprocess
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq pcsx2*.exe", "/NH"],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             timeout=30).stdout.decode("utf-8", "replace")
        return "pcsx2" in out.lower()
    except Exception:                                       # noqa: BLE001
        return None


def _card_paths():
    out = []
    for d in CARD_DIRS:
        if not os.path.isdir(d):
            continue
        for n in sorted(os.listdir(d)):
            if n.lower().endswith(".ps2"):
                out.append(os.path.join(d, n))
    return out


def _probe(path):
    """What this card is, without throwing if it is not one."""
    row = {"path": path, "name": os.path.basename(path), "rdr": False, "note": ""}
    try:
        card = ps2mc.Card.from_file(path)
    except Exception as ex:                                 # noqa: BLE001
        row["note"] = "%s: %s" % (type(ex).__name__, ex)
        return row
    try:
        card.read_file(rdrsave.SAVE_PATH)
        row["rdr"] = True
    except ps2mc.CardError as ex:
        row["note"] = "%s" % ex
    return row


def _xbox():
    """The Studio's Xbox side (the recomp's RDR.exe and its folders), or None."""
    try:
        from rdrstudio.studio import _data_editor_for
        from rdrstudio.xboxmode import xbox
        de = _data_editor_for(_STUDIO[0]) if _STUDIO else None
        return xbox(de) if de is not None else None
    except Exception:                                       # noqa: BLE001
        return None


_STUDIO = []


def _xbox_save_paths():
    """The Xbox game's saves (swat.sav) in the recomp's hard disk folder beside RDR.exe: hdd\\E\\UDATA\\<title>
    \\<save>\\swat.sav -- RDR's title folder is 5454001a; any title folder holding a swat.sav is looked in."""
    X = _xbox()
    out = []
    try:
        exe = X.state().get("exe") if X else None
    except Exception:                                       # noqa: BLE001
        exe = None
    if not exe:
        return out
    udata = os.path.join(os.path.dirname(exe), "hdd", "E", "UDATA")
    if not os.path.isdir(udata):
        return out
    for title in sorted(os.listdir(udata)):
        tdir = os.path.join(udata, title)
        if not os.path.isdir(tdir):
            continue
        for sv in sorted(os.listdir(tdir)):
            p = os.path.join(tdir, sv, rdrsave.XBOX_SAVE_NAME)
            if os.path.isfile(p):
                out.append(p)
    return out


def _is_xbox(path):
    return os.path.basename(path).lower() == rdrsave.XBOX_SAVE_NAME


def _xbox_running():
    """True / False / None (cannot tell), as _pcsx2_state: is the Xbox game (RDR.exe) running?"""
    X = _xbox()
    if X is None:
        return None
    try:
        return bool(X.running())
    except Exception:                                       # noqa: BLE001
        return None


def _xbox_row(path):
    row = {"path": path, "kind": "xbox", "rdr": False, "note": "",
           "name": "Xbox save (%s)" % os.path.basename(os.path.dirname(path))}
    try:
        rdrsave.Save(open(path, "rb").read())
        row["rdr"] = True
    except Exception as ex:                                 # noqa: BLE001
        row["note"] = "%s" % ex
    return row


def _cards(handler, studio, q):
    if studio is not None and not _STUDIO:
        _STUDIO.append(studio)
    return {"cards": [_probe(p) for p in _card_paths()] + [_xbox_row(p) for p in _xbox_save_paths()],
            "pcsx2": _pcsx2_state(), "xbox_running": _xbox_running()}


def _fields(handler, studio, q):
    return {"fields": [{"key": k, "offset": off, "kind": kind, "label": label,
                        "confidence": conf, "why": why}
                       for off, kind, k, label, conf, why in rdrsave.SLOT_FIELDS],
            "file_fields": [{"key": k, "offset": off, "kind": kind, "label": label,
                             "confidence": conf, "why": why}
                            for off, kind, k, label, conf, why in rdrsave.FILE_FIELDS],
            "stat_names": rdrsave.STAT_NAMES,
            "default_names": rdrsave.DEFAULT_NAMES}


def _open(handler, studio, q):
    path = (q.get("path") or [""])[0]
    if not path:
        raise api.Refusal("no path given")
    if not os.path.exists(path):
        return (404, {"error": "no card at %r" % path})
    if studio is not None and not _STUDIO:
        _STUDIO.append(studio)
    if _is_xbox(path):
        # the Xbox game's save: the file itself, no card around it
        save = rdrsave.Save(open(path, "rb").read())
        return {"path": path, "kind": "xbox", "signed": save.signed,
                "slots": [{"index": s.index, "used": s.used, "fields": dict(s.fields)} for s in save.slots],
                "fields": dict(save.fields), "pcsx2": _xbox_running(), "ecc_bad": []}
    card = ps2mc.Card.from_file(path)
    save = rdrsave.Save.from_card(card)
    slots = []
    for s in save.slots:
        slots.append({"index": s.index, "used": s.used,
                      "fields": dict(s.fields)})
    return {"path": path, "slots": slots, "fields": dict(save.fields),
            "pcsx2": _pcsx2_state(),
            "ecc_bad": [list(b) for b in card.ecc_report()[1]]}


def _write(handler, studio, body):
    path = body.get("path") or ""
    slot = int(body.get("slot", -1))
    edits = body.get("fields") or {}
    if not path or not os.path.exists(path):
        return (404, {"error": "no card at %r" % path})
    if not 0 <= slot < rdrsave.SLOT_COUNT:
        return (400, {"error": "slot %d is out of range 0..%d"
                               % (slot, rdrsave.SLOT_COUNT - 1)})
    if not edits and not (body.get("file_fields") or {}):
        return (400, {"error": "no fields to write"})
    if studio is not None and not _STUDIO:
        _STUDIO.append(studio)
    if _is_xbox(path):
        return _write_xbox(path, slot, edits, body)

    state = _pcsx2_state()
    if state is not False:
        return (403, {"error":
                      "refusing to write: PCSX2 is %s. A card written under a "
                      "running emulator is overwritten on its next flush."
                      % ("running" if state else
                         "not enumerable from here (WSL cannot see Windows "
                         "processes), so this cannot be proven safe")})

    if not os.path.isdir(BACKUPS):
        os.makedirs(BACKUPS)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = os.path.join(BACKUPS, "%s_%s" % (stamp, os.path.basename(path)))
    shutil.copyfile(path, backup)

    card = ps2mc.Card.from_file(path)
    save = rdrsave.Save.from_card(card)
    applied = {}
    for key, value in edits.items():
        save.set_field(slot, key, value)
        applied[key] = value
    # Money and anything else outside a slot. Kept a separate argument rather
    # than merged into `fields`, because a caller that muddles the two would
    # silently write a per-profile field into the active game's state.
    for key, value in (body.get("file_fields") or {}).items():
        save.set_file_field(key, value)
        applied[key] = value
    card.write_file(rdrsave.SAVE_PATH, save.to_bytes())
    card.save(path)

    # Re-read from the FILE, not from the object we just built, so what comes
    # back has been through the card's own layout and ECC.
    again = rdrsave.Save.from_card(ps2mc.Card.from_file(path))
    return {"ok": True, "backup": backup, "applied": applied,
            "slot": slot, "fields": dict(again.slots[slot].fields),
            "file_fields": dict(again.fields),
            "warning": "No edited card has ever been booted. Nothing here "
                       "proves the game accepts it."}


def _write_xbox(path, slot, edits, body):
    """An Xbox save written: the same three guards as a card -- a copy first, the game provably not running, only
    known fields at their widths."""
    state = _xbox_running()
    if state is not False:
        return (403, {"error": "refusing to write: the Xbox game (RDR.exe) is %s. It writes its save on the way out "
                               "and would put the old one back." % ("running" if state else "not checkable from here")})
    if not os.path.isdir(BACKUPS):
        os.makedirs(BACKUPS)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = os.path.join(BACKUPS, "%s_xbox_%s_%s" % (stamp, os.path.basename(os.path.dirname(path)),
                                                      os.path.basename(path)))
    shutil.copyfile(path, backup)
    save = rdrsave.Save(open(path, "rb").read())
    applied = {}
    for key, value in edits.items():
        save.set_field(slot, key, value)
        applied[key] = value
    for key, value in (body.get("file_fields") or {}).items():
        save.set_file_field(key, value)
        applied[key] = value
    data = save.to_bytes()
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)
    again = rdrsave.Save(open(path, "rb").read())
    return {"ok": True, "backup": backup, "applied": applied, "slot": slot, "signed": again.signed,
            "fields": dict(again.slots[slot].fields), "file_fields": dict(again.fields),
            "warning": "Signed again (the last 20 bytes: HMAC-SHA1 of the rest, the recomp's key) -- the game skips "
                       "a save whose signature does not match."}


_GET = {"save/cards": _cards, "save/open": _open, "save/fields": _fields}
_POST = {"save/write": _write}


def has_get(route):
    return route in _GET


def has_post(route):
    return route in _POST


def handle_get(handler, studio, route, q):
    return api.respond(handler, lambda: _GET[route](handler, studio, q),
                       "reading %s" % route)


def handle_post(handler, studio, route, body):
    return api.respond(handler, lambda: _POST[route](handler, studio, body),
                       "writing %s" % route)
