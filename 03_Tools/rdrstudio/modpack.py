"""Mod packs: share ONLY the files you changed, never the game.

A mod pack is a plain .zip holding a manifest.json and the new bytes of every
file that differs from the stock disc. It carries nothing else of the game:
no ISO, no untouched file, no vanilla data. For each file the manifest names
its archive (.dat) and path inside it, whether it was MODIFIED or ADDED, the
sha1 of the stock original it replaces, and the sha1 of the new file.

  export   scan(stock, working) -> build_pack -> <name>.rdrmodpack.zip
  apply    assess(...) judges every file against the recipient's own discs,
           then apply_pack rebuilds the archives through Dave.patch(grow=True)
           / Dave.add -- the same place/rebuild path the Files tab uses. It
           is not a byte patch: files change size, and the archive moves them.

Safety, in the order it is checked:

  1. Wrong game. Every modified file's stock sha1 must match the file on the
     recipient's STOCK disc. One miss and the whole pack is refused: a pack
     built on another version or region would write the wrong files into
     this one. Nothing is written.
  2. Conflicts. A file the working disc already holds in some third state
     (edited by hand, or by another pack) is a conflict. The check names the
     earlier pack when the ledger knows it. Nothing is written until the user
     says to overwrite.
  3. Already there. A file that already equals the pack's is skipped.
  4. After writing, every file is read back from the disc and its sha1
     compared with the manifest. A pack that "applied" but left the bytes
     alone is reported as a failure, not a success.

Hashes are over DECOMPRESSED contents, the same measure Studio.diff uses, so
a file re-saved at a different compression level is not a change.

Not included: brand-new .dat archives (M21). They grow the ISO itself, which
this file-level pack does not attempt; export lists them so nobody ships a
pack that silently lacks their levels.
"""

import base64
import hashlib
import io
import json
import os
import time
import zipfile

from . import api
from . import config as cfg
from .api import Refusal
from .dave import ALWAYS_RAW_EXTS, SlotOverflow

FORMAT = "rdrmodpack/1"
# A pack that carries code mods is format 2. Older Studios only know format 1
# and refuse it with "not a mod pack (format 'rdrmodpack/2')" -- they never
# half-apply the file part and silently drop the code. Packs without code
# mods stay format 1, so they keep working in every Studio that has packs.
FORMAT_CODE = "rdrmodpack/2"
CODE_VERSION = 1
STUDIO_VERSION = "1.4.0"
GAME = "SLUS-20500"
SUFFIX = ".rdrmodpack.zip"
MAX_UNPACKED = 1 << 30
LEDGER = "packs_applied.json"


def sha1(data):
    return hashlib.sha1(data).hexdigest()


def _key(archive, name):
    return "%s::%s" % (archive, name)


# ------------------------------------------------------------------ export

def scan(stock, working, progress=None, cancelled=None):
    """Every file that differs from stock, with its new bytes.

    Returns (rows, new_archives). Same comparison Studio.diff makes -- stored
    bytes equal is the fast "unchanged" path, else decompressed contents --
    plus the files diff() skips because stock has no copy (ADDED).
    """
    rows, new_archives = [], []
    stock_archives = {a.upper() for a in stock.archives()}
    names = working.archives()
    for n, arc in enumerate(names):
        if cancelled is not None and cancelled():
            raise RuntimeError("cancelled")
        if progress:
            progress(n + 1, len(names), arc)
        if arc.upper() not in stock_archives:
            new_archives.append(arc)
            continue
        try:
            a = working.archive(arc)
            b = stock.archive(arc)
        except Exception:                                  # noqa: BLE001
            continue
        bmap = {e.name: e for e in b}
        for e in a:
            v = bmap.get(e.name)
            if v is None:
                data = a.read(e)
                rows.append(dict(archive=arc, name=e.name, op="added",
                                 data=data, sha1=sha1(data), size=len(data),
                                 vanilla_sha1=None, vanilla_size=None))
                continue
            if (e.size_comp == v.size_comp
                    and a.backing.read_at(e.offset, e.size_comp)
                    == b.backing.read_at(v.offset, v.size_comp)):
                continue
            data, orig = a.read(e), b.read(v)
            if data == orig:
                continue
            rows.append(dict(archive=arc, name=e.name, op="modified",
                             data=data, sha1=sha1(data), size=len(data),
                             vanilla_sha1=sha1(orig), vanilla_size=len(orig)))
        working._cache.pop(arc.upper(), None)
        stock._cache.pop(arc.upper(), None)
    return rows, new_archives


def build_pack(rows, path, name="", author="", description="",
               new_archives=(), codemods=None):
    """Write the zip. Returns the manifest. Atomic: .part, then replace.

    `codemods` (optional) = dict(features=[...], stock_elf_sha1=..., ...).
    It ships NO game bytes: the recipient's Studio rebuilds the executable
    patch and the Cheats rows from its own stock disc.
    """
    files = []
    for i, r in enumerate(rows):
        files.append(dict(archive=r["archive"], name=r["name"], op=r["op"],
                          vanilla_sha1=r["vanilla_sha1"],
                          vanilla_size=r["vanilla_size"],
                          sha1=r["sha1"], size=r["size"],
                          zip="files/%05d.bin" % i))
    manifest = dict(format=FORMAT_CODE if codemods else FORMAT,
                    name=name or "Untitled mod",
                    author=author, description=description,
                    studio_version=STUDIO_VERSION, game=GAME,
                    created=time.strftime("%Y-%m-%d %H:%M:%S"),
                    files=files)
    if new_archives:
        manifest["not_included"] = sorted(new_archives)
    if codemods:
        manifest["codemods"] = dict(codemods, version=CODE_VERSION)
    manifest["pack_id"] = sha1(json.dumps(
        [(f["archive"], f["name"], f["sha1"]) for f in files]
        + ([sorted(codemods["features"]), codemods.get("stock_elf_sha1")]
           if codemods else [])).encode())[:12]
    readme = ["%s%s" % (manifest["name"],
                        (" by " + author) if author else ""), ""]
    if description:
        readme += [description, ""]
    if codemods:
        readme += ["CODE MODS: " + ", ".join(codemods["features"]) + ". These "
                   "change the game's executable, so applying makes a NEW "
                   "disc (02_Modded/RDR_CODEMODS.iso) from your own stock "
                   "game. The pack holds no game code; your Studio rebuilds "
                   "the patch locally.", ""]
    readme += ["An RDR Modding Studio mod pack: only the files this mod "
               "changed, none of the game.",
               "In the Studio: Export / Mod packs > Apply a mod pack, and pick "
               "this .zip. It checks your own stock disc first.", "",
               "%d file(s):" % len(files)]
    readme += ["  %-8s %s :: %s" % (f["op"], f["archive"], f["name"])
               for f in files]
    tmp = path + ".part"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=1))
        z.writestr("README.txt", "\n".join(readme) + "\n")
        for f, r in zip(files, rows):
            z.writestr(f["zip"], r["data"])
    os.replace(tmp, path)
    return manifest


# ------------------------------------------------------------------ import

def read_pack(raw):
    """(manifest, {zip path: bytes}) from zip bytes. Validates everything."""
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as ex:
        raise Refusal("bad_request", "That file is not a mod pack: it is not "
                      "a zip.", "Pick the .zip someone sent you.", str(ex))
    total = sum(i.file_size for i in z.infolist())
    if total > MAX_UNPACKED:
        raise Refusal("bad_request", "That mod pack unpacks to over 1 GB, "
                      "which no real pack does.", "")
    try:
        manifest = json.loads(z.read("manifest.json").decode("utf-8"))
    except (KeyError, ValueError) as ex:
        raise Refusal("bad_request", "That zip is not an RDR Modding Studio "
                      "mod pack: no readable manifest.json.", "", str(ex))
    if (not isinstance(manifest, dict)
            or manifest.get("format") not in (FORMAT, FORMAT_CODE)
            or not isinstance(manifest.get("files"), list)):
        raise Refusal("bad_request", "That zip is not an RDR Modding Studio "
                      "mod pack (format %r)." % (
                          manifest.get("format") if isinstance(manifest, dict)
                          else None), "")
    cm = manifest.get("codemods")
    if manifest["format"] == FORMAT_CODE or cm is not None:
        _check_codemods(manifest, cm)
    blobs, seen = {}, set()
    for f in manifest["files"]:
        try:
            archive, name, op = f["archive"], f["name"], f["op"]
            want, zp = f["sha1"], f["zip"]
            if not all(isinstance(x, str) for x in (archive, name, want, zp)):
                raise TypeError("fields must be text")
            if op not in ("modified", "added"):
                raise ValueError("unknown op %r" % op)
            if op == "modified" and not isinstance(f.get("vanilla_sha1"), str):
                raise ValueError("a modified file needs vanilla_sha1")
        except (KeyError, TypeError, ValueError) as ex:
            raise Refusal("bad_request", "The mod pack's manifest is damaged: "
                          "%s." % ex, "Ask for the pack again.")
        if (archive, name) in seen:
            raise Refusal("bad_request", "The mod pack lists %s :: %s twice."
                          % (archive, name), "")
        seen.add((archive, name))
        if zp not in z.namelist() or zp.startswith(("/", "..")) or ".." in zp:
            raise Refusal("bad_request", "The mod pack is missing %s." % zp,
                          "Ask for the pack again.")
        data = z.read(zp)
        if sha1(data) != want:
            raise Refusal("bad_request", "%s :: %s inside the pack does not "
                          "match its recorded checksum, so the pack is "
                          "damaged." % (archive, name),
                          "Download it again.")
        blobs[zp] = data
    return manifest, blobs


def _check_codemods(manifest, cm):
    from . import codemods
    if manifest.get("format") != FORMAT_CODE or not isinstance(cm, dict):
        raise Refusal("bad_request", "The mod pack's code-mod section is "
                      "damaged.", "Ask for the pack again.")
    if cm.get("version") != CODE_VERSION:
        raise Refusal("bad_request", "This mod pack carries code mods in a "
                      "newer format (v%s) than this Studio understands (v%d). "
                      "Nothing was changed." % (cm.get("version"), CODE_VERSION),
                      "Update the Studio, then apply the pack again.")
    feats = cm.get("features")
    if (not isinstance(feats, list) or not feats
            or not all(isinstance(k, str) for k in feats)):
        raise Refusal("bad_request", "The mod pack's code-mod list is "
                      "damaged.", "Ask for the pack again.")
    unknown = [k for k in feats if k not in codemods.KEYS]
    if unknown:
        raise Refusal("bad_request", "This pack wants code mods this Studio "
                      "does not have (%s). Nothing was changed."
                      % ", ".join(unknown), "Update the Studio.")
    if not isinstance(cm.get("stock_elf_sha1"), str):
        raise Refusal("bad_request", "The mod pack's code-mod section has no "
                      "executable checksum.", "Ask for the pack again.")


# ------------------------------------------------------------------ ledger

def ledger_path():
    return os.path.join(cfg.MODS, LEDGER)


def _ledger(path=None):
    try:
        with open(path or ledger_path()) as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def _remember(manifest, applied, path=None):
    path = path or ledger_path()
    entries = [e for e in _ledger(path)
               if e.get("pack_id") != manifest.get("pack_id")]
    entries.append(dict(
        pack_id=manifest.get("pack_id"), name=manifest.get("name"),
        author=manifest.get("author", ""),
        applied=time.strftime("%Y-%m-%d %H:%M:%S"),
        files={_key(f["archive"], f["name"]): f["sha1"] for f in applied}))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".part", "w") as f:
        json.dump(entries, f, indent=1)
    os.replace(path + ".part", path)


def _holder(ledger, archive, name, current_sha1, own_id):
    """The earlier pack whose version of this file is what the disc holds."""
    for e in reversed(ledger):
        if e.get("pack_id") != own_id and \
                e.get("files", {}).get(_key(archive, name)) == current_sha1:
            return e.get("name") or e.get("pack_id")
    return None


# ------------------------------------------------------------------ assess

def _entry(dave, name):
    return dave._by_name.get(name.lower())


def assess(stock, target, manifest, blobs, ledger=None):
    """Judge every file in the pack against the recipient's own discs.

    Reads only. Each row gets a `status`:
      apply / add     will be written
      already         the disc already holds this exact file
      conflict        the disc holds something else (`held_by` names a pack)
      wrong_version   the stock original is not the pack's -> pack is for
                      another game version; refuses everything
      bad             cannot be applied here (missing archive/file, no room
                      for a new entry)
    """
    ledger = _ledger() if ledger is None else ledger
    stock_arcs = {a.upper() for a in stock.archives()}
    target_arcs = {a.upper() for a in target.archives()}
    rows = []
    for f in manifest["files"]:
        arc, name, op = f["archive"], f["name"], f["op"]
        row = dict(archive=arc, name=name, op=op, size=f["size"],
                   status=None, reason="", held_by=None)
        rows.append(row)
        data = blobs[f["zip"]]
        if arc.upper() not in stock_arcs or arc.upper() not in target_arcs:
            row.update(status="wrong_version",
                       reason="this game has no %s" % arc)
            continue
        try:
            sd, td = stock.archive(arc), target.archive(arc)
        except Exception as ex:                            # noqa: BLE001
            row.update(status="bad", reason="cannot open %s: %s" % (arc, ex))
            continue
        se, te = _entry(sd, name), _entry(td, name)
        if op == "modified":
            if se is None:
                row.update(status="wrong_version",
                           reason="stock disc has no %s" % name)
                continue
            if sha1(sd.read(se)) != f["vanilla_sha1"]:
                row.update(status="wrong_version",
                           reason="stock copy differs from the one this "
                                  "pack was made against")
                continue
            if te is None:
                row.update(status="bad",
                           reason="your working disc has no %s" % name)
                continue
            cur = sha1(td.read(te))
            if cur == f["sha1"]:
                row.update(status="already", reason="already on the disc")
            elif cur == f["vanilla_sha1"]:
                row.update(status="apply")
            else:
                held = _holder(ledger, arc, name, cur, manifest.get("pack_id"))
                row.update(status="conflict", held_by=held,
                           reason=("changed by the mod pack '%s'" % held)
                           if held else "already edited on your disc")
        else:
            if se is not None:
                row.update(status="wrong_version",
                           reason="this file is already on your stock disc, "
                                  "so the pack was made against another one")
                continue
            if te is not None:
                cur = sha1(td.read(te))
                if cur == f["sha1"]:
                    row.update(status="already", reason="already on the disc")
                else:
                    held = _holder(ledger, arc, name, cur,
                                   manifest.get("pack_id"))
                    row.update(status="conflict", held_by=held,
                               reason=("added by the mod pack '%s'" % held)
                               if held else "a different file of that name "
                                            "is already there")
                continue
            chk = td.can_add(name, _stored_len(name, data))
            if chk["ok"]:
                row.update(status="add")
            else:
                row.update(status="bad", reason="no room to add it: "
                           + "; ".join(chk["reasons"]))
    return rows


def assess_codemods(stock_iso, working_iso, manifest):
    """Judge the pack's code mods against the recipient's discs. Reads only.

    -> dict(features, status, reason, out) with status
      ok            stock executable matches; the working disc is clean
      wrong_version stock SLUS_205.00 is not the one the pack was made against
      blocked       the working disc already carries code mods / is unreadable
    """
    from . import codemods, iso9660
    cm = manifest["codemods"]
    out = dict(features=list(cm["features"]), status="ok", reason="")
    try:
        with iso9660.Iso9660(stock_iso) as i:
            elf = i.read_file("SLUS_205.00")
    except Exception as ex:                                # noqa: BLE001
        out.update(status="blocked",
                   reason="cannot read the stock disc: %s" % ex)
        return out
    if sha1(elf) != cm["stock_elf_sha1"]:
        out.update(status="wrong_version",
                   reason="your stock game executable is not the one this "
                          "pack's code mods were made for")
        return out
    try:
        have = codemods.iso_state(working_iso)["features"]
    except Exception as ex:                                # noqa: BLE001
        out.update(status="blocked",
                   reason="cannot read your working disc: %s" % ex)
        return out
    if have != []:
        out.update(status="blocked",
                   reason="your working disc already carries code mods "
                          "(%s); code mods can only be built on a disc "
                          "without them" % (", ".join(have) if have
                                            else "unknown"))
    return out


def _stored_len(name, data):
    import zlib
    ext = name.rsplit(".", 1)[-1] if "." in name.rsplit("/", 1)[-1] else ""
    if ext in ALWAYS_RAW_EXTS:
        return len(data)
    z = zlib.compressobj(9, zlib.DEFLATED, -15)
    return min(len(z.compress(data) + z.flush()), len(data))


def verdict(rows):
    """('ok'|'wrong_version'|'blocked', counts) -- conflicts do not block here;
    the caller decides with allow_conflicts."""
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    if counts.get("wrong_version"):
        return "wrong_version", counts
    if counts.get("bad"):
        return "blocked", counts
    return "ok", counts


# ------------------------------------------------------------------- apply

def apply_pack(target, manifest, blobs, rows, allow_conflicts=False):
    """Write the pack into `target` (a writable Game) and read it all back.

    Returns dict(applied=[...], failed=[...], verified=n). `applied` holds
    only files whose bytes on the disc now equal the pack's.
    """
    by_key = {(f["archive"], f["name"]): f for f in manifest["files"]}
    want = [r for r in rows if r["status"] in ("apply", "add")
            or (allow_conflicts and r["status"] == "conflict")]
    failed = []
    for r in sorted(want, key=lambda r: r["status"] != "add"):
        f = by_key[(r["archive"], r["name"])]
        data = blobs[f["zip"]]
        d = target.archive(r["archive"])
        try:
            if _entry(d, r["name"]) is None:
                ext = r["name"].rsplit(".", 1)[-1] if "." in r["name"] else ""
                d.add(r["name"], data, compress=ext not in ALWAYS_RAW_EXTS)
            else:
                d.patch(r["name"], data, flush=False, grow=True)
        except SlotOverflow as ex:
            failed.append(dict(archive=r["archive"], name=r["name"],
                               error=str(ex)))
        except Exception as ex:                            # noqa: BLE001
            failed.append(dict(archive=r["archive"], name=r["name"],
                               error="%s: %s" % (type(ex).__name__, ex)))
    try:
        target.iso.flush()
    except Exception:                                      # noqa: BLE001
        pass
    # Trust nothing that was only reported: read each file back through a
    # fresh parse of its archive and compare against the manifest.
    bad = {(x["archive"], x["name"]) for x in failed}
    applied = []
    for r in want:
        k = (r["archive"], r["name"])
        if k in bad:
            continue
        target._cache.pop(r["archive"].upper(), None)
        f = by_key[k]
        try:
            back = target.archive(r["archive"]).read(r["name"])
        except Exception as ex:                            # noqa: BLE001
            failed.append(dict(archive=k[0], name=k[1],
                               error="could not read back: %s" % ex))
            continue
        if sha1(back) != f["sha1"]:
            failed.append(dict(archive=k[0], name=k[1],
                               error="written, but it reads back different"))
            continue
        applied.append(dict(archive=k[0], name=k[1], op=r["op"],
                            sha1=f["sha1"], data=blobs[f["zip"]]))
    return dict(applied=applied, failed=failed, verified=len(applied))


# ------------------------------------------------------------------ routes

def _exports_dir():
    return getattr(cfg, "EXPORTS", None) or os.path.join(
        os.path.dirname(cfg.MODDED_ISO), "exports")


def _safe(name, default):
    keep = "".join(c for c in (name or "") if c.isalnum() or c in " _-.").strip()
    return keep.replace(" ", "_").strip(".") or default


def _need(s):
    if s.game is None or s.vanilla is None:
        raise api.DiscLocked(*s.locked_problem())


def _codemods_for_export(s, body):
    """None, or the code-mod section for the pack (feature list + the stock
    executable's sha1). Refuses unknown keys and a working disc that already
    carries code mods (its cheats.swui would be exported as a plain file)."""
    feats = body.get("features")
    if not feats:
        return None
    from . import codemods, iso9660
    unknown = [k for k in feats if k not in codemods.KEYS]
    if unknown or not isinstance(feats, list):
        raise Refusal("bad_request", "Unknown code mod(s): %s."
                      % ", ".join(map(str, unknown)), "")
    feats = [k for k in codemods.KEYS if k in feats]
    have = codemods.iso_state(s.modded_path)["features"]
    if have != []:
        raise Refusal("bad_request", "Your working disc already carries "
                      "code mods, so a pack cannot be made from it.",
                      "Export from a working disc without code mods; the "
                      "pack records which code mods to build.")
    with iso9660.Iso9660(s.vanilla_path) as i:
        elf = i.read_file("SLUS_205.00")
    return dict(features=feats, stock_elf_sha1=sha1(elf),
                stock_elf_size=len(elf))


def post_pack_export(handler, s, body):
    name = (body.get("name") or "").strip()
    author = (body.get("author") or "").strip()
    desc = (body.get("description") or "").strip()
    fn = _safe(body.get("filename") or name, "mod_" + time.strftime("%Y%m%d-%H%M"))
    if fn.lower().endswith(".zip"):
        fn = fn[:-4]
    if fn.lower().endswith(".rdrmodpack"):
        fn = fn[:-len(".rdrmodpack")]
    path = os.path.join(_exports_dir(), fn + SUFFIX)
    if os.path.exists(path) and not body.get("replace"):
        raise Refusal("bad_request", "%s already exists in the exports folder."
                      % os.path.basename(path),
                      "Pick another name, or tick Replace.")
    plan = api.plan([], "Writes %s: only the files that differ from stock, no "
                    "game data." % os.path.basename(path),
                    "Wrote mod pack %s (nothing on the disc changed)"
                    % os.path.basename(path), steps=0)
    if api.is_dry(body):
        return api.ok(plan=plan, path=path)
    t0 = time.time()
    cm = _codemods_for_export(s, body)
    with s.lock:
        s.ensure_open(force=True)
        _need(s)
        rows, new_arcs = scan(s.vanilla, s.game)
    if cm is not None and not body.get("include_files", True):
        rows, new_arcs = [], []
    if not rows and cm is None:
        raise Refusal("bad_request", "Nothing to export: the working disc "
                      "matches stock.", "Make an edit first.")
    os.makedirs(_exports_dir(), exist_ok=True)
    manifest = build_pack(rows, path, name or fn, author, desc, new_arcs,
                          codemods=cm)
    size = os.path.getsize(path)
    mod = sum(1 for r in rows if r["op"] == "modified")
    s.note("exported mod pack %s (%d modified, %d added, %s bytes)"
           % (path, mod, len(rows) - mod, size))
    return api.ok(plan=plan, applied=True, path=path, size=size,
                  files=len(rows), modified=mod, added=len(rows) - mod,
                  not_included=sorted(new_arcs), pack_id=manifest["pack_id"],
                  codemods=cm["features"] if cm else [],
                  seconds=round(time.time() - t0, 1))


def _overview(manifest, rows):
    v, counts = verdict(rows)
    return dict(
        pack=dict(name=manifest.get("name"), author=manifest.get("author", ""),
                  description=manifest.get("description", ""),
                  studio_version=manifest.get("studio_version"),
                  files=len(manifest["files"]),
                  codemods=(manifest.get("codemods") or {}).get("features", []),
                  not_included=manifest.get("not_included", [])),
        verdict=v, counts=counts,
        conflicts=[r for r in rows if r["status"] == "conflict"],
        rows=[{k: r[k] for k in ("archive", "name", "op", "status", "reason",
                                 "held_by")} for r in rows[:300]])


def post_pack_apply(handler, s, body):
    raw_b64 = body.get("zip_b64")
    if not raw_b64:
        raise Refusal("bad_request", "No mod pack was sent.", "")
    try:
        raw = base64.b64decode(raw_b64, validate=True)
    except Exception as ex:                                # noqa: BLE001
        raise Refusal("bad_request", "The mod pack did not arrive intact.",
                      "", str(ex))
    manifest, blobs = read_pack(raw)
    dry = api.is_dry(body)
    allow = bool(body.get("allow_conflicts"))
    with s.lock:
        s.ensure_open(force=True)
        _need(s)
        rows = assess(s.vanilla, s.game, manifest, blobs)
        info = _overview(manifest, rows)
        v = info["verdict"]
        code = None
        if manifest.get("codemods"):
            code = assess_codemods(s.vanilla_path, s.modded_path, manifest)
            info["codemods"] = code
            if code["status"] == "wrong_version":
                v = "wrong_version"
            elif code["status"] == "blocked" and v == "ok":
                raise Refusal("bad_request", "The code mods in this pack "
                              "can't be built: %s. Nothing was changed."
                              % code["reason"], "", **info)
        todo = [r for r in rows if r["status"] in ("apply", "add")]
        if v == "wrong_version":
            bad = [r for r in rows if r["status"] == "wrong_version"]
            if code and code["status"] == "wrong_version":
                bad = bad + [dict(archive="SLUS_205.00", name="(executable)",
                                  reason=code["reason"])]
            raise Refusal(
                "bad_request",
                "This mod pack is for a different copy of the game "
                "(%d of %d parts don't match your stock disc). Nothing was "
                "changed." % (len(bad), len(rows) + (1 if code else 0)),
                "It needs the same version and region it was made on "
                "(Red Dead Revolver, %s). Check the stock ISO set in the "
                "Studio." % GAME,
                "; ".join("%s :: %s (%s)" % (r["archive"], r["name"],
                                              r["reason"]) for r in bad[:5]),
                **info)
        if v == "blocked":
            bad = [r for r in rows if r["status"] == "bad"]
            raise Refusal("bad_request",
                          "%d file(s) in this pack can't be put on your disc, "
                          "so nothing was changed." % len(bad),
                          "", "; ".join("%s :: %s (%s)" % (
                              r["archive"], r["name"], r["reason"])
                                         for r in bad[:5]), **info)
        n_add = sum(1 for r in todo if r["status"] == "add")
        n_over = len(info["conflicts"]) if allow else 0
        plan = api.plan(
            [], "Applies '%s': %d file(s) to write, %d already there%s."
            % (manifest.get("name"), len(todo) + n_over,
               info["counts"].get("already", 0),
               (", %d conflict(s)" % len(info["conflicts"]))
               if info["conflicts"] else ""),
            "Applied mod pack '%s'" % manifest.get("name"), steps=1)
        if code:
            plan["summary"] = (plan.get("summary") or "") + (
                " Then builds a NEW disc with the code mods (%s) at "
                "02_Modded/RDR_CODEMODS.iso; your working disc stays free of "
                "code mods." % ", ".join(code["features"]))
        if dry:
            return api.ok(plan=plan, applied=False, **info)
        if info["conflicts"] and not allow:
            raise Refusal(
                "bad_request",
                "%d file(s) in this pack were already changed on your disc. "
                "Nothing was written." % len(info["conflicts"]),
                "Apply anyway to overwrite them with this pack's version.",
                "; ".join("%s :: %s (%s)" % (r["archive"], r["name"],
                                              r["reason"])
                          for r in info["conflicts"][:5]),
                conflict=True, **info)
        res = apply_pack(s.game, manifest, blobs, rows, allow_conflicts=allow)
        group = s.journal.new_group()
        with s.journal.batch():
            for a in res["applied"]:
                if a["op"] == "modified":
                    s.journal.record(a["archive"], a["name"], a["data"],
                                     "apply mod pack '%s'" % manifest.get("name"),
                                     group)
        s._levels = {}
        if res["applied"]:
            _remember(manifest, res["applied"])
        s.note("applied mod pack '%s': %d written, %d already there, %d failed"
               % (manifest.get("name"), len(res["applied"]),
                  info["counts"].get("already", 0), len(res["failed"])))
    info.pop("rows", None)
    build = None
    if code and not res["failed"]:
        # The working disc now holds the file part; the code mods go into a
        # NEW copy of it (patch_iso never writes its source, never 01_Vanilla).
        from . import codemods
        out = os.path.join(os.path.dirname(s.modded_path), "RDR_CODEMODS.iso")
        ok, why = codemods.start_build(s.modded_path, out, code["features"])
        build = dict(started=ok, problem=None if ok else why, output=out,
                     features=code["features"])
        s.note("mod pack '%s': code-mod build %s -> %s"
               % (manifest.get("name"), "started" if ok else "NOT started: "
                  + str(why), out))
    elif code:
        build = dict(started=False, output=None, features=code["features"],
                     problem="not built: some files failed to apply")
    return api.ok(plan=plan, applied=True, written=len(res["applied"]),
                  codemods_build=build,
                  added=sum(1 for a in res["applied"] if a["op"] == "added"),
                  failed=res["failed"], verified=res["verified"],
                  already=info["counts"].get("already", 0),
                  overwritten=n_over, ok_all=not res["failed"], **{
                      k: info[k] for k in ("pack", "counts")})
