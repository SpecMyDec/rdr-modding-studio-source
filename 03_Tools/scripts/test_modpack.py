#!/usr/bin/env python3
"""Mod packs: export only the changed files, apply them to someone else's disc.

The tester's ask: post a mod without posting a 2.3 GB .iso. The pack is a zip of
the files that differ from stock plus a manifest of sha1s. The classic way for
this to fail is to say "applied" and leave the disc alone, so every check here
reads BYTES back from an ISO opened by a fresh handle.

Part 1, offline, on private copies of the stock ISO (the real working ISO is
never opened):
  * a modded copy gets three changes: a file that GROWS (so the archive has to
    move it), a same-size edit, and a brand-new file in an existing archive
  * scan -> build_pack -> the zip holds exactly those three, and nothing else
  * apply to a FRESH stock copy: each file reads back byte-identical to the
    modded copy, the raw archive bytes on the ISO changed, and scanning the
    result finds the same three files with the same hashes
  * applying again is a no-op ("already"), not a second write
  * a wrong-hash pack and a wrong stock disc are refused and write NOTHING
    (the ISO's bytes are identical before and after)
  * conflict: pack B touches a file pack A already changed -> the check names
    pack A, nothing is written without allow_conflicts, and with it B's bytes
    land

Part 2, live, against the Studio on 8731 (its working ISO is restored after):
  * edit a file through file_drop, pack_export it, revert it to stock,
    pack_apply (dry run, then real) and see the file's bytes come back
  * a pack whose checksum is wrong is refused; a second pack on the same file
    is a conflict until allow_conflicts

Exit 0 all passed, 1 a check failed.
"""

import base64
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rdrstudio import Game                       # noqa: E402
from rdrstudio import config as cfg              # noqa: E402
from rdrstudio import modpack as mp              # noqa: E402

BASE = "http://127.0.0.1:8731/api/"
FAILS = []


def check(ok, what):
    print("  %s %s" % ("ok  " if ok else "FAIL", what))
    if not ok:
        FAILS.append(what)
    return ok


def sha1(b):
    return hashlib.sha1(b).hexdigest()


def arc_sha(path, archive):
    g = Game(path)
    try:
        f = g.iso.get(archive)
        off, size = f.offset, f.size
    finally:
        g.close()
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        fh.seek(off)
        while size:
            c = fh.read(min(1 << 20, size))
            h.update(c)
            size -= len(c)
    return h.hexdigest()


def read_file(path, archive, name):
    g = Game(path)
    try:
        return g.archive(archive).read(name)
    finally:
        g.close()


# ------------------------------------------------------------------ part 1

ARC = "LVLS_SNG/MANS.DAT"


def pick_targets(vanilla):
    """Two editable text-ish files in ARC, and a legal name for a new one."""
    g = Game(vanilla)
    try:
        d = g.archive(ARC)
        c = [e for e in d if e.ext in ("csv", "txt", "tbl", "props")
             and 1500 < e.size_full < 150000 and e.compressed]
        name = None
        for cand in ("modpack_test.txt", "mpk_test.txt", "mp_test.txt"):
            if d.can_add(cand, 300)["ok"]:
                name = cand
                break
        return [e.name for e in c[:2]], name
    finally:
        g.close()


def part1(tmp):
    print("part 1: offline, private ISO copies")
    stock = cfg.VANILLA_ISO
    grow_name_list, new_name = pick_targets(stock)
    if len(grow_name_list) < 2 or not new_name:
        check(False, "found two editable files and a free name in %s" % ARC)
        return
    m_grow, m_same = grow_name_list
    modded = os.path.join(tmp, "modded.iso")
    fresh = os.path.join(tmp, "fresh.iso")
    shutil.copyfile(stock, modded)
    shutil.copyfile(stock, fresh)
    ledger = os.path.join(tmp, "ledger.json")

    # --- build the modded disc
    g = Game(modded, writable=True)
    try:
        d = g.archive(ARC)
        v_grow, v_same = d.read(m_grow), d.read(m_same)
        d.patch(m_grow, v_grow + (b"\n// the tester was here\n" * 200), grow=True)
        d.patch(m_same, bytes([v_same[0] ^ 0x20]) + v_same[1:])
        d.add(new_name, b"hello from a mod pack\n" * 20)
        g.iso.flush()
    finally:
        g.close()

    sg = Game(stock)
    mg = Game(modded)
    try:
        rows, newarcs = mp.scan(sg, mg)
    finally:
        sg.close()
        mg.close()
    got = {(r["archive"], r["name"]): r["op"] for r in rows}
    want = {(ARC, m_grow): "modified", (ARC, m_same): "modified",
            (ARC, new_name): "added"}
    check(got == want, "scan finds exactly the 3 changes (%d found)" % len(rows))
    check(not newarcs, "no new archives reported")
    zpath = os.path.join(tmp, "A" + mp.SUFFIX)
    man = mp.build_pack(rows, zpath, "Pack A", "tester", "grow+same+add",
                        newarcs)
    zsize = os.path.getsize(zpath)
    check(zsize < 200000,
          "the pack is small (%d bytes), the ISO is not in it" % zsize)
    raw = open(zpath, "rb").read()
    man2, blobs = mp.read_pack(raw)
    check(man2["pack_id"] == man["pack_id"] and len(blobs) == 3,
          "pack reads back with 3 blobs")

    # --- wrong hash / wrong stock: refuse, write nothing
    snap2 = arc_sha(fresh, ARC)
    bad = json.loads(json.dumps(man2))
    next(f for f in bad["files"] if f["op"] == "modified")["vanilla_sha1"] = "0" * 40
    sg, f2 = Game(stock), Game(fresh, writable=True)
    try:
        arows = mp.assess(sg, f2, bad, blobs, ledger=[])
        v, _ = mp.verdict(arows)
        check(v == "wrong_version" and any(
            r["status"] == "wrong_version" for r in arows),
            "a wrong stock hash is called wrong_version")
    finally:
        sg.close()
        f2.close()
    # ...and a whole wrong stock disc (the modded disc posing as stock)
    sg, f2 = Game(modded), Game(fresh, writable=True)
    try:
        arows = mp.assess(sg, f2, man2, blobs, ledger=[])
        check(mp.verdict(arows)[0] == "wrong_version",
              "pack checked against a different stock disc is refused")
    finally:
        sg.close()
        f2.close()
    check(arc_sha(fresh, ARC) == snap2, "refusals wrote nothing")

    # --- apply to a fresh stock copy
    before = {k: read_file(fresh, *k) for k in want if k[1] != new_name}
    arc_before = arc_sha(fresh, ARC)
    sg, fg = Game(stock), Game(fresh, writable=True)
    try:
        arows = mp.assess(sg, fg, man2, blobs, ledger=[])
        st = {(r["archive"], r["name"]): r["status"] for r in arows}
        check(st == {(ARC, m_grow): "apply", (ARC, m_same): "apply",
                     (ARC, new_name): "add"}, "assess plans apply/apply/add")
        check(mp.verdict(arows)[0] == "ok", "verdict ok")
        res = mp.apply_pack(fg, man2, blobs, arows)
    finally:
        sg.close()
        fg.close()
    check(not res["failed"] and res["verified"] == 3,
          "3 files written and verified (failed=%s)" % res["failed"])
    mp._remember(man2, res["applied"], ledger)
    for k in want:
        after = read_file(fresh, *k)
        mod = read_file(modded, *k)
        check(after == mod, "%s byte-identical to the modded disc" % k[1])
        if k in before:
            check(after != before[k], "%s actually changed on the fresh disc"
                  % k[1])
    check(arc_sha(fresh, ARC) != arc_before,
          "the archive's raw bytes on the fresh ISO changed")
    check(len(read_file(fresh, ARC, m_grow)) > len(before[(ARC, m_grow)]),
          "the grown file really grew (the archive moved it)")
    sg, fg = Game(stock), Game(fresh)
    try:
        rows2, _ = mp.scan(sg, fg)
    finally:
        sg.close()
        fg.close()
    check({(r["archive"], r["name"], r["sha1"]) for r in rows2}
          == {(r["archive"], r["name"], r["sha1"]) for r in rows},
          "scanning the fresh disc finds the same 3 files and hashes")

    # --- again: nothing to do
    sg, fg = Game(stock), Game(fresh, writable=True)
    try:
        arows = mp.assess(sg, fg, man2, blobs, ledger=[])
        check({r["status"] for r in arows} == {"already"},
              "applying twice reports 'already', not a second write")
        snap = arc_sha(fresh, ARC)
        res = mp.apply_pack(fg, man2, blobs, arows)
    finally:
        sg.close()
        fg.close()
    check(res["verified"] == 0 and arc_sha(fresh, ARC) == snap,
          "second apply leaves the ISO untouched")

    # --- conflict: B edits m_grow differently; A is already on `fresh`
    sg = Game(stock)
    try:
        orig = sg.archive(ARC).read(m_grow)
    finally:
        sg.close()
    newB = orig + b"\n// pack B\n"
    rowsB = [dict(archive=ARC, name=m_grow, op="modified", data=newB,
                  sha1=sha1(newB), size=len(newB),
                  vanilla_sha1=sha1(orig), vanilla_size=len(orig))]
    zB = os.path.join(tmp, "B" + mp.SUFFIX)
    manB = mp.build_pack(rowsB, zB, "Pack B", "other", "", ())
    manB, blobsB = mp.read_pack(open(zB, "rb").read())
    onA = read_file(fresh, ARC, m_grow)
    sg, fg = Game(stock), Game(fresh, writable=True)
    try:
        crow = mp.assess(sg, fg, manB, blobsB, ledger=mp._ledger(ledger))
        check(crow[0]["status"] == "conflict" and crow[0]["held_by"] == "Pack A",
              "B's file is a conflict and the check names 'Pack A' (%s / %s)"
              % (crow[0]["status"], crow[0]["held_by"]))
        r0 = mp.apply_pack(fg, manB, blobsB, crow)
        check(r0["verified"] == 0 and
              read_file(fresh, ARC, m_grow) == onA,
              "without allow_conflicts nothing is written")
        r1 = mp.apply_pack(fg, manB, blobsB, crow, allow_conflicts=True)
    finally:
        sg.close()
        fg.close()
    check(r1["verified"] == 1 and read_file(fresh, ARC, m_grow) == newB,
          "with allow_conflicts B's bytes land (%d bytes)" % len(newB))


# ------------------------------------------------------------------ part 2

LIVE_ARC = "LVLS_SNG/MANS.DAT"


def call(route, body=None, timeout=900):
    if body is None:
        req = urllib.request.Request(BASE + route)
    else:
        req = urllib.request.Request(
            BASE + route, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as ex:
        return {"ok": False, "_status": ex.code,
                "error": ex.read().decode("utf-8", "replace")}


def b64(b):
    return base64.b64encode(b).decode("ascii")


def part2(tmp):
    print("part 2: live Studio routes")
    import zlib
    g = Game(cfg.VANILLA_ISO)
    tgt, stock_data, donors = None, None, []
    try:
        d = g.archive(LIVE_ARC)
        rows = sorted((e for e in d if e.ext == "mod"),
                      key=lambda e: -e.headroom)
        for t in rows[:4]:
            raw = not t.compressed
            fits = []
            for e in sorted(rows, key=lambda e: -e.size_full)[:40]:
                data = d.read(e)
                if e.name == t.name or not data or data in fits:
                    continue
                co = zlib.compressobj(9, zlib.DEFLATED, -15)
                size = len(data) if raw else min(
                    len(co.compress(data) + co.flush()), len(data))
                if size <= t.slot:
                    fits.append(data)
            if len(fits) >= 2:
                tgt, stock_data, donors = t.name, d.read(t), fits
                break
    finally:
        g.close()
    if tgt is None:
        check(False, "found a .mod target with two donors in %s" % LIVE_ARC)
        return
    d1, d2 = donors[0], donors[1]
    path = None
    ledger_file = mp.ledger_path()
    ledger_was = open(ledger_file, "rb").read() if os.path.exists(
        ledger_file) else None
    try:
        w = call("file_drop", dict(archive=LIVE_ARC, name=tgt,
                                   data_b64=b64(d1)))
        check(w.get("ok"), "edit a file through file_drop")
        e = call("pack_export", dict(name="Live test", author="suite",
                                     description="d", filename="_live_modpack_test",
                                     replace=True, dry=False))
        check(e.get("ok") and e.get("files", 0) >= 1,
              "pack_export wrote a pack (%s)" % (e.get("path") or e))
        path = e.get("path")
        raw = open(path, "rb").read()
        man, blobs = mp.read_pack(raw)
        ent = [f for f in man["files"] if f["name"] == tgt]
        check(ent and ent[0]["op"] == "modified"
              and ent[0]["sha1"] == sha1(d1)
              and ent[0]["vanilla_sha1"] == sha1(stock_data),
              "the pack records the edited file with stock + new sha1")
        call("revert_file", dict(archive=LIVE_ARC, name=tgt))
        check(read_file(cfg.MODDED_ISO, LIVE_ARC, tgt) == stock_data,
              "reverted to stock before applying")
        dry = call("pack_apply", dict(zip_b64=b64(raw), dry=True))
        st = {(r["name"]): r["status"] for r in dry.get("rows", [])}
        check(dry.get("ok") and st.get(tgt) == "apply",
              "dry run says it will apply %s (%s)" % (tgt, st.get(tgt)))
        check(read_file(cfg.MODDED_ISO, LIVE_ARC, tgt) == stock_data,
              "dry run wrote nothing")
        # wrong checksum: tamper a blob inside the zip
        zi = zipfile.ZipFile(io.BytesIO(raw))
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as zo:
            for n in zi.namelist():
                b = zi.read(n)
                if n == ent[0]["zip"]:
                    b = b[:-1] + bytes([b[-1] ^ 1])
                zo.writestr(n, b)
        r = call("pack_apply", dict(zip_b64=b64(out.getvalue()), dry=False))
        check(not r.get("ok"), "a damaged pack is refused")
        # wrong stock hash in the manifest
        man_bad = json.loads(json.dumps(man))
        for f in man_bad["files"]:
            if f["name"] == tgt:
                f["vanilla_sha1"] = "1" * 40
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as zo:
            zo.writestr("manifest.json", json.dumps(man_bad))
            for n in zi.namelist():
                if n != "manifest.json":
                    zo.writestr(n, zi.read(n))
        r = call("pack_apply", dict(zip_b64=b64(out.getvalue()), dry=False))
        check(not r.get("ok") and "different copy of the game"
              in (r.get("problem") or {}).get("message", ""),
              "a wrong-version pack is refused with a plain message")
        check(read_file(cfg.MODDED_ISO, LIVE_ARC, tgt) == stock_data,
              "refusals wrote nothing")
        # the real apply
        r = call("pack_apply", dict(zip_b64=b64(raw), dry=False))
        check(r.get("ok") and r.get("written", 0) >= 1 and not r.get("failed"),
              "pack_apply wrote %s file(s)" % r.get("written"))
        now = read_file(cfg.MODDED_ISO, LIVE_ARC, tgt)
        check(now == d1 and now != stock_data,
              "the disc now holds the pack's bytes (changed from stock)")
        # conflict: another edit, then the pack again
        call("file_drop", dict(archive=LIVE_ARC, name=tgt, data_b64=b64(d2)))
        r = call("pack_apply", dict(zip_b64=b64(raw), dry=False))
        check(not r.get("ok") and r.get("conflict")
              and read_file(cfg.MODDED_ISO, LIVE_ARC, tgt) == d2,
              "re-applying over a different edit is a conflict, nothing written")
        r = call("pack_apply", dict(zip_b64=b64(raw), dry=False,
                                    allow_conflicts=True))
        check(r.get("ok") and read_file(cfg.MODDED_ISO, LIVE_ARC, tgt) == d1,
              "allow_conflicts overwrites it with the pack's bytes")
    finally:
        call("revert_file", dict(archive=LIVE_ARC, name=tgt))
        if path and os.path.exists(path):
            os.remove(path)
        if ledger_was is None:
            if os.path.exists(ledger_file):
                os.remove(ledger_file)
        else:
            open(ledger_file, "wb").write(ledger_was)
        check(read_file(cfg.MODDED_ISO, LIVE_ARC, tgt) == stock_data,
              "working ISO restored to stock")


def main():
    tmp = tempfile.mkdtemp(prefix="_modpack_test_",
                           dir=os.path.join(cfg.ROOT, "02_Modded"))
    try:
        part1(tmp)
        part2(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for f in FAILS:
        print("  FAIL " + f)
    if FAILS:
        return 1
    print("  OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
