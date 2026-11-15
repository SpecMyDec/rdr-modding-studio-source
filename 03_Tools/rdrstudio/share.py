"""Getting work OUT of the Studio: a mod file, a patch, or a playable ISO.

Every edit lands on the working ISO the moment it is applied, so there is no
separate "build" step -- the working ISO already IS the build. What was
missing was the last step: nothing in the app put that build anywhere a user
could hand it to someone, and nothing took a mod file someone else made.

Three ways out, cheapest to share first:

  mod file (.rdrmod.json)  versions.save_doc -- the files that differ from
                           stock. Needs the Studio on the other end.
  patch (.xdelta)          vcdiff.write_patch, stock ISO -> working ISO.
                           Applies with xdelta3 or Delta Patcher to the
                           recipient's own disc; the game itself never
                           travels.
  playable ISO (.iso)      a byte-exact copy of the working ISO, hashed on
                           both sides of the copy.

One way in: mod_import, a .rdrmod.json from anywhere, filed where the Changes
tab lists it.

Nothing here writes to either disc. The exports read the working ISO under
the Studio's lock so an edit cannot land half way through a copy.
"""

import hashlib
import json
import os
import shutil
import sys
import time

from . import api
from . import config as cfg
from . import vcdiff
from .api import Refusal
from .journal import FORMAT

CHUNK = 8 << 20


def exports_dir():
    return getattr(cfg, "EXPORTS", None) or os.path.join(
        os.path.dirname(cfg.MODDED_ISO), "exports")


def _discs(s):
    """The two discs THIS Studio has open. Not cfg's: --vanilla/--modded and
    the gear button re-point the Studio without touching cfg, and a patch
    measured against a disc the Studio is not editing is a patch for the
    wrong game."""
    return (getattr(s, "vanilla_path", None) or cfg.VANILLA_ISO,
            getattr(s, "modded_path", None) or cfg.MODDED_ISO)


def _safe(name, default):
    keep = "".join(c for c in (name or "") if c.isalnum() or c in " _-.").strip()
    keep = keep.replace(" ", "_").strip(".")
    return keep or default


def _stamp():
    return time.strftime("%Y%m%d-%H%M")


def _listing():
    d = exports_dir()
    out = []
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            p = os.path.join(d, fn)
            if fn.endswith(".part") or not os.path.isfile(p):
                continue
            st = os.stat(p)
            out.append(dict(name=fn, path=p, size=st.st_size,
                            modified=time.strftime("%Y-%m-%d %H:%M",
                                                   time.localtime(st.st_mtime))))
    out.sort(key=lambda r: r["modified"], reverse=True)
    return out


def _free(path):
    d = path
    while d and not os.path.isdir(d):
        d = os.path.dirname(d)
    try:
        return shutil.disk_usage(d or ".").free
    except OSError:
        return None


def _gb(n):
    return "%.2f GB" % (n / 1e9)


def _mb(n):
    return "%.1f MB" % (n / 1e6) if n >= 1e5 else "%d KB" % max(1, n // 1024)


def _target(body, ext, default):
    name = _safe(body.get("name"), default)
    if not name.lower().endswith(ext):
        name += ext
    return os.path.join(exports_dir(), name)


def _refuse_existing(path, body):
    if os.path.exists(path) and not body.get("replace"):
        raise Refusal("bad_request",
                      "%s already exists in the exports folder."
                      % os.path.basename(path),
                      "Pick another name, or tick Replace.")


def _need_discs(s):
    stock, work = _discs(s)
    for label, p in (("stock", stock), ("working", work)):
        if not os.path.isfile(p):
            raise Refusal("not_found", "The %s disc is not where the Studio "
                          "expects it: %s" % (label, p),
                          "Point the Studio at it with the gear button.")
    return stock, work


# ------------------------------------------------------------------ routes

def get_share_info(handler, s, q):
    sp, wp = _discs(s)
    stock = os.path.getsize(sp) if os.path.isfile(sp) else None
    work = os.path.getsize(wp) if os.path.isfile(wp) else None
    return api.ok(folder=exports_dir(), exports=_listing(),
                  stock=dict(path=sp, size=stock),
                  working=dict(path=wp, size=work),
                  mods_folder=cfg.MODS, stamp=_stamp(),
                  can_open_folder=sys.platform == "win32")


def post_export_patch(handler, s, body):
    stock, work = _need_discs(s)
    path = _target(body, ".xdelta", "RDR_mod_" + _stamp())
    _refuse_existing(path, body)
    plan = api.plan([], "Writes %s: only the bytes that differ from the stock "
                    "disc. Apply it with xdelta3 or Delta Patcher."
                    % os.path.basename(path),
                    "Wrote patch %s (nothing on the disc changed)"
                    % os.path.basename(path), steps=0)
    if api.is_dry(body):
        return api.ok(plan=plan, path=path)
    os.makedirs(exports_dir(), exist_ok=True)
    tmp = path + ".part"
    t0 = time.time()
    with s.lock:
        with open(tmp, "wb") as out:
            info = vcdiff.write_patch(stock, work, out)
    os.replace(tmp, path)
    if info["source_sha256"] == info["target_sha256"]:
        note = ("The working disc matches stock, so this patch changes "
                "nothing.")
    else:
        note = ""
    readme = os.path.splitext(path)[0] + ".txt"
    with open(readme, "w") as f:
        f.write("Red Dead Revolver (SLUS-20500) patch made with RDR Modding "
                "Studio, %s.\n\n" % time.strftime("%Y-%m-%d %H:%M"))
        f.write("Apply %s to an unmodified copy of the game with xdelta3 or "
                "Delta Patcher.\n\n" % os.path.basename(path))
        f.write("Expects  %s bytes, sha256 %s\n"
                % (info["source_size"], info["source_sha256"]))
        f.write("Produces %s bytes, sha256 %s\n"
                % (info["target_size"], info["target_sha256"]))
    size = os.path.getsize(path)
    s.note("exported patch %s (%s, %d changed byte(s))"
           % (path, _mb(size), info["changed_bytes"]))
    return api.ok(plan=plan, applied=True, path=path, readme=readme,
                  size=size, seconds=round(time.time() - t0, 1), note=note,
                  **info)


def post_export_iso(handler, s, body):
    stock, work = _need_discs(s)
    path = _target(body, ".iso", "RDR_MODDED_" + _stamp())
    if os.path.normcase(os.path.abspath(path)) in (
            os.path.normcase(os.path.abspath(work)),
            os.path.normcase(os.path.abspath(stock))):
        raise Refusal("bad_request", "That name is one of the Studio's own "
                      "discs.", "Pick another name.")
    _refuse_existing(path, body)
    size = os.path.getsize(work)
    free = _free(exports_dir())
    plan = api.plan([], "Copies the working disc to %s (%s). Plays in PCSX2 "
                    "as it is." % (os.path.basename(path), _gb(size)),
                    "Exported %s (nothing on the disc changed)"
                    % os.path.basename(path), steps=0)
    if free is not None and free < size + (64 << 20):
        raise Refusal("bad_request", "Not enough free space: the copy needs "
                      "%s and the drive has %s." % (_gb(size), _gb(free)),
                      "Free some space, or move the exports folder.",
                      plan=plan)
    if api.is_dry(body):
        return api.ok(plan=plan, path=path, size=size)
    os.makedirs(exports_dir(), exist_ok=True)
    tmp = path + ".part"
    t0 = time.time()
    src_sha = hashlib.sha256()
    with s.lock:
        with open(work, "rb") as fi, open(tmp, "wb") as fo:
            while True:
                b = fi.read(CHUNK)
                if not b:
                    break
                src_sha.update(b)
                fo.write(b)
    # Read the copy back rather than trusting the write: a short write to a
    # full or failing drive is exactly the case this has to catch.
    dst_sha = hashlib.sha256()
    with open(tmp, "rb") as f:
        while True:
            b = f.read(CHUNK)
            if not b:
                break
            dst_sha.update(b)
    if dst_sha.hexdigest() != src_sha.hexdigest():
        os.remove(tmp)
        raise Refusal("server_fault", "The copy did not read back the same "
                      "as the working disc, so it was deleted.",
                      "Check the drive the exports folder is on.")
    os.replace(tmp, path)
    s.note("exported ISO %s (%s)" % (path, _gb(size)))
    return api.ok(plan=plan, applied=True, path=path, size=size,
                  sha256=src_sha.hexdigest(),
                  seconds=round(time.time() - t0, 1))


def post_export_open(handler, s, body):
    which = body.get("which") or "exports"
    folder = cfg.MODS if which == "mods" else exports_dir()
    if sys.platform != "win32":
        raise Refusal("not_editable", "Opening a folder only works on the "
                      "Windows machine running the Studio.", folder)
    os.makedirs(folder, exist_ok=True)
    os.startfile(folder)                                   # noqa: S606
    return api.ok(folder=folder)


def post_mod_import(handler, s, body):
    doc = body.get("doc")
    if isinstance(doc, str):
        try:
            doc = json.loads(doc)
        except ValueError as ex:
            raise Refusal("bad_request", "That file is not a mod: %s" % ex, "")
    if (not isinstance(doc, dict) or doc.get("format") != FORMAT
            or not isinstance(doc.get("files"), list)):
        raise Refusal("bad_request", "That file is not an RDR Modding Studio "
                      "mod (.rdrmod.json).", "")
    name = doc.get("name") or os.path.splitext(body.get("filename") or "")[0]
    base = _safe(name, "imported_" + _stamp())
    os.makedirs(cfg.MODS, exist_ok=True)
    path = os.path.join(cfg.MODS, base + ".rdrmod.json")
    n = 2
    while os.path.exists(path):
        path = os.path.join(cfg.MODS, "%s_%d.rdrmod.json" % (base, n))
        n += 1
    with open(path + ".part", "w") as f:
        json.dump(doc, f, indent=1)
    os.replace(path + ".part", path)
    s.note("imported mod '%s' (%d file(s)) -> %s"
           % (name, len(doc["files"]), path))
    return api.ok(path=path, name=name, files=len(doc["files"]))
