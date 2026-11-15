"""[M15, partial] An OBJ becomes a weapon model, and the sharing trap refuses.

M15's full criterion is *"imports a weapon `.mod` and asserts the replaced
weapon re-reads with the new geometry"*. This covers everything up to the
REPOINT, which is REQ-045 and not in this build -- so this deliberately does
NOT claim the milestone. What it does claim is the part that exists:

    python3 scripts/test_weapon_import.py      (needs a Studio on 8731)

Live, because it writes the working ISO through the route the app uses, and
it reverts what it touched.

## The one weapon this can land on, and why only one

    55 weapons name 14 distinct models between them
    ptl_lod_01  11 weapons     rfl_lod_01  11     pt2_lod_01  6
    gat_lod_01   2 weapons     bow_lod_01   1  <-- the only unshared one

Overwriting `ptl_lod_01.mod` re-models eleven weapons at once, so the importer
refuses it. `bow_lod_01.mod` (Sacred_Bow) is the single text-dialect model
named by exactly one weapon, so it is the only one that can be imported today
WITHOUT the ability to repoint a ModelName. That is not a limitation of the
test -- it is the actual scope of the milestone until REQ-045 is decided.

## What it asserts

  * an OBJ's geometry SURVIVES the writer -- vertex count, positions to six
    decimals, and triangle set, checked by re-reading with `modtext`, which is
    rdr-3d's reader and not this module's. Asserting our own writer agrees
    with itself would prove nothing;
  * importing over a SHARED model is refused, and the refusal NAMES all
    eleven weapons rather than saying "shared";
  * a model too big for its slot is refused with the numbers, and the size is
    judged AFTER deflate -- all 3,008 `.mod` entries are compressed, so
    length is not size (C-030);
  * a brand-new model name is refused because nothing could point at it;
  * a request with `target` or `obj` MISSING refuses with a fix, rather than
    answering 500 -- the two guards that no other check in this file reaches,
    because every other one supplies both;
  * the dry run changes nothing -- and `dry` is checked as ABSENT too, because
    studio.py's older routes treat an omitted flag as WRITE while routes2
    treats it as DRY, and this route must take the safe reading;
  * the write lands on the unshared model and THE ARCHIVE BYTES CHANGE;
  * the imported model RE-READS OFF THE DISC with the new geometry;
  * the file is restored byte-identically.
"""

import json
import os
import sys
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _HERE]
sys.path.insert(0, os.path.dirname(_HERE))

from rdrstudio import config, modtext, modimport            # noqa: E402
from rdrstudio.game import Game                             # noqa: E402

BASE = "http://127.0.0.1:8731/api/"
ARC = "SWAT.DAT"
SOLO = "bow_lod_01"          # the one model named by exactly one weapon
SHARED = "ptl_lod_01"        # named by eleven

# Six decimals in, six decimals out. `obj_from` writes the OBJ with `%.6f`, so
# a coordinate can be off by at most half of the last place -- 5e-7 EXACTLY --
# before the importer ever sees it. Comparing with `< 5e-7` therefore rejects
# the correct answer on every exact tie: measured on bow_lod_01, 31 of 180
# components land on one (0.2675905 -> 0.267591) and the worst error is
# 5.00000000014e-07. The loss is entirely in this file's own OBJ export --
# `parsed -> .mod -> reader` measures 0, the writer is exact -- so the bound
# belongs just above one half-ulp, not at it.
TOL = 1e-6
FAILURES = []
# The pristine bytes, stashed the moment they are read, so the
# `finally` can restore even if the run dies mid-write.
_PRISTINE = []
_RESTORED = []


def _worst(want, got):
    """Largest single-component error; nan when the counts disagree."""
    if len(want) != len(got):
        return float("nan")
    return max((abs(a[i] - b[i]) for a, b in zip(want, got)
                for i in range(3)), default=float("nan"))


def check(cond, label, detail=""):
    print("  [%s] %-52s %s" % ("PASS" if cond else "FAIL", label, detail))
    if not cond:
        FAILURES.append(label)


def post(route, body):
    req = urllib.request.Request(
        BASE + route, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode("utf-8"))


def obj_from(doc, scale=1.0, repeat=1):
    """The mesh as a real exporter writes it: v/vt/vn and f a/a/a.

    `repeat` duplicates the geometry to make a model that cannot fit, which is
    how the slot_overflow case is reached with something a user could actually
    produce rather than with a synthetic blob.
    """
    verts = list(doc["verts"])
    tris = [t for m in doc["materials"] for p in m["packets"] for t in p["tris"]]
    base_n = len(verts)
    all_v, all_t = [], []
    for k in range(repeat):
        off = k * base_n
        shift = k * 0.01
        all_v.extend([(v[0] * scale + shift, v[1] * scale, v[2] * scale)
                      for v in verts])
        all_t.extend([(a + off, b + off, c + off) for a, b, c in tris])
    acc = [[0.0, 0.0, 0.0] for _ in all_v]
    for a, b, c in all_t:
        n = modimport._face_normal(all_v[a], all_v[b], all_v[c])
        for i in (a, b, c):
            acc[i][0] += n[0]; acc[i][1] += n[1]; acc[i][2] += n[2]
    L = ["# test export"]
    for v in all_v:
        L.append("v %.6f %.6f %.6f" % v)
    for v in all_v:
        L.append("vt %.6f %.6f" % (abs(v[0]) % 1.0, abs(v[2]) % 1.0))
    for a in acc:
        ln = (a[0] ** 2 + a[1] ** 2 + a[2] ** 2) ** 0.5 or 1.0
        L.append("vn %.6f %.6f %.6f" % (a[0] / ln, a[1] / ln, a[2] / ln))
    for a, b, c in all_t:
        L.append("f %d/%d/%d %d/%d/%d %d/%d/%d"
                 % (a + 1, a + 1, a + 1, b + 1, b + 1, b + 1,
                    c + 1, c + 1, c + 1))
    return "\n".join(L), all_v, all_t


def restore(before):
    """Put the model back, and PROVE it went back.

    `Game(path)` opens the ISO **read-only** -- `writable` defaults to False
    -- so the original `d2.patch(...)` here could never have worked: it raised
    `ISO opened read-only` every run and left the working disc carrying the
    test's half-scale bow. It failed loudly, which is the only reason it was
    not silent corruption, but the disc still did not come back.

    Called from a `finally`, because a test that writes the ISO and then
    throws on the way to its own cleanup leaves the disc dirty for whichever
    lane runs next -- and the contract makes restoring it mandatory, not
    best-effort.
    """
    if _RESTORED:
        return
    _RESTORED.append(True)
    try:
        # Read-only to decide, and the ROUTE to write (REQ-078).
        #
        # This used to open the working ISO writable and patch it directly,
        # while the Studio this test is driving had that same disc open for
        # writing. Two writers, serialised by nothing -- Session.lock orders
        # threads inside one Studio and says nothing about a second process --
        # so the restore raced the very server it was testing. It has not
        # collided yet; that is luck, not design, and it is the exact pattern
        # REQ-078 exists to stop.
        #
        # `file_drop` is the route the app's own drag-and-drop uses. When the
        # name already exists in the archive it REPLACES, riding the same
        # journalled write path as every shipped edit, which also means this
        # restore is undoable and honours grow like any other. Reads stay
        # direct because reads were never the hazard.
        d = Game(config.MODDED_ISO).archive(ARC)
        if d.read(d.find(SOLO + ".mod")) == before:
            check(True, "already as it started -- nothing to undo")
            return
        import base64
        r = post("file_drop", dict(
            archive=ARC, name=SOLO + ".mod",
            data_b64=base64.b64encode(before).decode("ascii")))
        if r.get("error"):
            check(False, "revert", r["error"])
            return
        restore_all_copies(post, SOLO + ".mod", before)
    except Exception as ex:
        check(False, "revert", str(ex))
        return
    # A fresh handle: re-reading through the one that wrote would be the
    # writer agreeing with itself.
    d3 = Game(config.MODDED_ISO).archive(ARC)
    check(d3.read(d3.find(SOLO + ".mod")) == before,
          "the model is byte-identical to how it started")


def restore_all_copies(post, name, before):
    """weapon_import writes EVERY archive copy of the model (the copies are
    byte-identical on the retail disc), so a test must put every copy back,
    not just the one it inspects -- otherwise it leaves the ISO dirty."""
    import base64
    g = Game(config.MODDED_ISO)
    try:
        todo = []
        for a in g.archives():
            try:
                d = g.archive(a)
                e = d.find(name)
                if d.read(e) != before:
                    todo.append(a)
            except Exception:                                 # noqa: BLE001
                continue
    finally:
        g.close()
    for a in todo:
        post("file_drop", dict(archive=a, name=name,
                               data_b64=base64.b64encode(before).decode("ascii")))


def _run():
    g = Game(config.MODDED_ISO)
    d = g.archive(ARC)
    before = d.read(d.find(SOLO + ".mod"))
    _PRISTINE.append(before)
    doc = modtext.parse(before)
    print("target: %s.mod  %d verts %d tris, slot %d"
          % (SOLO, len(doc["verts"]),
             doc["counted"]["triangles"], d.find(SOLO + ".mod").slot))
    print()

    # A model that is NOT the one on the disc: scaled, so "the geometry
    # changed" is a real assertion and not satisfied by the original.
    objtext, want_v, want_t = obj_from(doc, scale=0.5)

    print("1. the writer's output re-reads with rdr-3d's reader:")
    built, stats = modimport.build(modimport.from_obj(objtext))
    problems = modimport.verify(built, stats)
    check(not problems, "it verifies clean", "; ".join(problems) or "no problems")
    back = modtext.parse(built)
    check(len(back["verts"]) == len(want_v), "vertex count survives",
          "%d" % len(back["verts"]))
    got_t = set(tuple(sorted(t)) for m in back["materials"]
                for p in m["packets"] for t in p["tris"])
    check(got_t == set(tuple(sorted(t)) for t in want_t),
          "the triangle set survives", "%d triangles" % len(got_t))
    check(len(back["verts"]) == len(want_v)
          and all(abs(a[i] - b[i]) < TOL
                  for a, b in zip(want_v, back["verts"]) for i in range(3)),
          "positions survive to six decimals",
          "worst %.3g of %g" % (_worst(want_v, back["verts"]), TOL))

    print()
    print("2. importing over a SHARED model is refused, by name:")
    r = post("weapon_import", dict(target=SHARED, obj=objtext, dry=True,
                                   allow_shared=False))
    check(r.get("ok") is False, "refused")
    prob = r.get("problem") or {}
    check(prob.get("code") == "not_editable", "code", prob.get("code"))
    msg = prob.get("message", "")
    check("11 weapons" in msg, "it says how many weapons")
    check("Twin_Revolvers" in msg and "Six_Shooter" in msg,
          "it NAMES them, not just the count")
    check(r.get("plan") is not None,
          "the plan still rides along (openSheet needs one)")
    check(r.get("written") is False, "and nothing was written")

    print()
    print("3. a model too big for the slot is refused WITH the numbers:")
    big, _, _ = obj_from(doc, repeat=12)
    r = post("weapon_import", dict(target=SOLO, obj=big, dry=True))
    prob = r.get("problem") or {}
    check(r.get("ok") is False, "refused")
    check(prob.get("code") == "slot_overflow", "code", prob.get("code"))
    check("over" in prob.get("message", ""), "it says how far over",
          prob.get("message", "")[:60])
    plan = r.get("plan") or {}
    check(plan.get("fits") is False, "plan.fits is False")
    check(plan.get("stored", 0) > plan["writes"][0]["slot"],
          "and the size it compares is the PACKED size",
          "stored %s > slot %s" % (plan.get("stored"),
                                   plan["writes"][0]["slot"]))

    print()
    print("4. a brand-new model is refused -- nothing could point at it:")
    r = post("weapon_import", dict(target="totally_new_gun", obj=objtext,
                                   dry=True))
    prob = r.get("problem") or {}
    check(r.get("ok") is False, "refused")
    check("ModelName" in prob.get("message", "") + prob.get("detail", ""),
          "it names the reason (ModelName is not editable)")

    print()
    print("5. a request MISSING its arguments refuses, and does not crash:")
    # Added after the route answered `500 NameError: name 'api' is not
    # defined` to an empty body. Every other check in this file supplies both
    # `target` and `obj`, so the two bad_request guards had never once been
    # executed -- the coverage gap, not the typo, is why it reached a running
    # Studio. A 500 is not a refusal: it carries no `fix`, and the UI renders
    # it as a crash rather than as something the user can act on.
    for missing, body in (("target", dict(obj=objtext)),
                          ("obj", dict(target=SOLO))):
        r = post("weapon_import", body)
        prob = r.get("problem") or {}
        check(r.get("ok") is False and "error" not in r,
              "a missing `%s` refuses rather than 500s" % missing,
              r.get("error") or "")
        check(prob.get("code") == "bad_request",
              "  ...with code bad_request", prob.get("code"))
        check(bool(prob.get("fix")), "  ...and a fix the user can act on",
              prob.get("fix", "")[:48])

    print()
    print("6. dry run plans and writes nothing -- including when `dry` is"
          " ABSENT:")
    r = post("weapon_import", dict(target=SOLO, obj=objtext, dry=True))
    check(r.get("ok") is True, "dry run ok")
    check(r.get("written") is False, "written:false is stated, not implied")
    check((r.get("plan") or {}).get("fits") is True, "the plan fits")
    check(d.read(d.find(SOLO + ".mod")) == before, "the file is untouched")

    r = post("weapon_import", dict(target=SOLO, obj=objtext))   # no `dry`
    check(r.get("written") is False,
          "an OMITTED dry flag does NOT write", "dry=%s" % r.get("dry"))
    check(d.read(d.find(SOLO + ".mod")) == before,
          "and the file is still untouched")

    print()
    print("7. the write lands and the archive bytes change:")
    r = post("weapon_import", dict(target=SOLO, obj=objtext, dry=False))
    check(r.get("ok") is True, "applied", r.get("problem"))
    check(r.get("written") is True, "written:true")
    g2 = Game(config.MODDED_ISO)
    d2 = g2.archive(ARC)
    after = d2.read(d2.find(SOLO + ".mod"))
    check(after != before, "THE ARCHIVE BYTES CHANGED",
          "%d -> %d bytes" % (len(before), len(after)))

    print()
    print("8. and the imported model RE-READS off the disc:")
    try:
        rd = modtext.parse(after)
        check(True, "it parses as a text .mod off the disc")
        check(len(rd["verts"]) == len(want_v), "with the imported vertex count",
              "%d" % len(rd["verts"]))
        got = set(tuple(sorted(t)) for m in rd["materials"]
                  for p in m["packets"] for t in p["tris"])
        check(got == set(tuple(sorted(t)) for t in want_t),
              "and the imported triangles")
        check(len(rd["verts"]) == len(want_v)
              and all(abs(a[i] - b[i]) < TOL
                      for a, b in zip(want_v, rd["verts"]) for i in range(3)),
              "and the imported positions, not the originals",
              "worst %.3g of %g" % (_worst(want_v, rd["verts"]), TOL))
        b2 = modtext.bounds(rd)
        check(abs((b2[1][2] - b2[0][2])
                  - (max(v[2] for v in want_v) - min(v[2] for v in want_v)))
              < 1e-4, "and it is the SCALED model, not the shipped one",
              "%.3f m long" % (b2[1][2] - b2[0][2]))
    except Exception as ex:
        check(False, "it re-reads off the disc", str(ex))

    print()
    print("9. restore:")
    restore(before)

    print()
    print("SUMMARY")
    if FAILURES:
        print("  [FAIL] %d check(s) failed: %s" % (len(FAILURES),
                                                   "; ".join(FAILURES[:3])))
        return 1
    print("  [PASS] an OBJ becomes a weapon model; sharing and slot refuse")
    return 0


def main():
    try:
        return _run()
    finally:
        # A safety net, not a second restore: step 9 normally does it and
        # sets the flag. This only fires when the run died before reaching
        # step 9 -- which is exactly the case that would otherwise hand the
        # next lane a disc with a half-scale bow in it.
        if _PRISTINE and not _RESTORED:
            print()
            print("restore (after an aborted run):")
            restore(_PRISTINE[0])


if __name__ == "__main__":
    sys.exit(main())
