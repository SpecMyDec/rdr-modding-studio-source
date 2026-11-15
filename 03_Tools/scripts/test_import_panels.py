"""The two Import panels (level custom model, weapon model), driven through
the SAME routes the buttons call, with a bad file and a good one.

    python3 scripts/test_import_panels.py      (needs a Studio on 8731)

Asserts what a modder sees: a bad/oversize OBJ gives a plain-English error
that says nothing was changed (no traceback, no jargon) and leaves the disc
byte-identical; a good OBJ succeeds with a `message` + `next` step and the
archive bytes really change; everything is restored at the end.
"""
import base64
import json
import os
import sys
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _HERE]
sys.path.insert(0, os.path.dirname(_HERE))

from rdrstudio import config                                  # noqa: E402
from rdrstudio.game import Game                               # noqa: E402
from rdrstudio.level import Level                             # noqa: E402

BASE = "http://127.0.0.1:%s/api/" % (os.environ.get("RDR_PORT") or "8731")
FAILURES = []
GOOD = "v 0 0 0\nv 0.2 0 0\nv 0 0.2 0\nv 0 0 0.2\nf 1 2 3\nf 1 3 4\nf 1 4 2\nf 2 4 3\n"
JARGON = ("Traceback", "MeshError", "KeyError", "NoneType", "line ")


def check(ok, what, detail=""):
    print(("  ok   " if ok else "  FAIL ") + what
          + (("  -- " + str(detail)) if detail and not ok else ""))
    if not ok:
        FAILURES.append(what)


def post(route, body):
    req = urllib.request.Request(
        BASE + route, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode())


def friendly(msg):
    return (isinstance(msg, str) and "Nothing was changed" in msg
            and not any(j in msg for j in JARGON[:4]))


def read_file(arc, name):
    g = Game(config.MODDED_ISO)
    try:
        d = g.archive(arc)
        return d.read(d.find(name))
    finally:
        g.close()


def put_back(arc, name, data):
    r = post("file_drop", dict(archive=arc, name=name,
                               data_b64=base64.b64encode(data).decode()))
    check(not r.get("error"), "restored " + name, r.get("error"))


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


def level_part():
    print("level custom model:")
    g = Game(config.MODDED_ISO)
    arc = next(a for a in g.archives() if "PERFNEW" in a.upper())
    lvl = Level(g, arc)
    rs = lvl._load_rsc()
    ids = [c.index for c in rs.chunks if c.tag == "MOD0"
           and not rs.raw(c.index)[1] and rs.parts(c.index)]
    rsc_name = lvl.rsc_name
    d = g.archive(arc)
    before = d.read(rsc_name)
    g.close()
    chunk = ids[0]
    base = dict(archive=arc, chunk=chunk, label="test")
    try:
        r = post("model_import", dict(base, obj="this is not an obj\n"))
        check(friendly(r.get("error")), "garbage file -> friendly error",
              r.get("error"))
        r = post("model_import", dict(base, obj="v 0 0 0\nv 1 0 0\n"))
        check(friendly(r.get("error")), "no faces -> friendly error",
              r.get("error"))
        r = post("model_import", dict(base, obj="   "))
        check(r.get("error") and "empty" in r["error"], "empty file", r)
        big = "".join("v %d 0 0\nv %d 1 0\nv %d 0 1\nf %d %d %d\n"
                      % (i, i, i, 3 * i + 1, 3 * i + 2, 3 * i + 3)
                      for i in range(60000))
        r = post("model_import", dict(base, obj=big))
        check(friendly(r.get("error")) or "Too big" in str(r.get("error")),
              "huge model -> friendly error", str(r.get("error"))[:200])
        check(read_file(arc, rsc_name) == before,
              "bad files changed nothing on the disc")
        r = post("model_import", dict(base, obj=GOOD, dry=True))
        check(r.get("fits") and r.get("dry"), "good OBJ dry run fits", r)
        r = post("model_import", dict(base, obj=GOOD))
        check(r.get("ok") and "Done" in r.get("message", "")
              and "Boot test" in r.get("next", ""),
              "good OBJ -> success with message + next step", r)
        check(read_file(arc, rsc_name) != before, "archive bytes changed")
        check("ollision" in r.get("message", ""), "success message says what happened to collision", r.get("message"))
        # the import now also adds a collision node (a second file): one Ctrl+Z
        # must take model AND collision back together
        u = post("undo", {})
        check(u.get("ok"), "one undo reverts the model and its collision", u)
    finally:
        if read_file(arc, rsc_name) != before:
            put_back(arc, rsc_name, before)
    check(read_file(arc, rsc_name) == before, "level restored byte-identical")


def weapon_part():
    print("weapon model:")
    arc, name = "SWAT.DAT", "bow_lod_01.mod"
    before = read_file(arc, name)
    try:
        r = post("weapon_import", dict(target="bow_lod_01", obj="nope\n"))
        p = r.get("problem") or {}
        check(r.get("ok") is False and p.get("message") and p.get("fix"),
              "garbage -> message + fix, no write", r)
        check(not r.get("written"), "garbage wrote nothing")
        r = post("weapon_import", dict(target="ptl_lod_01", obj=GOOD,
                                       dry=False, allow_shared=False))
        p = r.get("problem") or {}
        check(r.get("ok") is False and "allow_shared" in p.get("fix", ""),
              "shared model refused with a plain instruction", p)
        check(read_file(arc, name) == before, "refusals changed nothing")
        r = post("weapon_import", dict(target="bow_lod_01", obj=GOOD,
                                       dry=False))
        check(r.get("ok") and r.get("written") and "Done" in r.get("message", "")
              and r.get("next"), "good OBJ -> success message + next", r)
        check(read_file(arc, name) != before, "weapon archive bytes changed")
    finally:
        restore_all_copies(post, name, before)
    check(read_file(arc, name) == before, "weapon restored byte-identical")


def character_part():
    print("character mesh (experimental):")
    g = Game(config.MODDED_ISO)
    arc = None
    for a in g.archives():
        try:
            d = g.archive(a)
            d.find("srd_lods.rsc")
            d.find("srd.skel")
            arc = a
            break
        except Exception:                                     # noqa: BLE001
            continue
    g.close()
    if arc is None:
        check(False, "found an archive with srd_lods.rsc")
        return
    name = "srd_lods.rsc"
    before = read_file(arc, name)
    base = dict(archive=arc, name=name, chunk=4)
    try:
        r = post("character_model_import", dict(base, obj="nonsense\n"))
        check(friendly(r.get("error")), "garbage -> friendly error", r.get("error"))
        r = post("character_model_import", dict(base, obj="  "))
        check(r.get("error") and "empty" in r["error"], "empty file", r)
        r = post("character_model_import", dict(base, chunk=0, obj=GOOD))
        check(friendly(r.get("error")), "non-model chunk refused", r.get("error"))
        check(read_file(arc, name) == before, "refusals changed nothing")
        box = ("v -0.1 0.9 0\nv 0.1 0.9 0\nv 0 1.1 0\nv 0 1.0 0.2\n"
               "f 1 2 3\nf 1 3 4\nf 1 4 2\nf 2 4 3\n")
        r = post("character_model_import", dict(base, obj=box, dry=True))
        check(r.get("fits") and r.get("dry"), "dry run reports it fits", r)
        r = post("character_model_import", dict(base, obj=box))
        check(r.get("ok") and "EXPERIMENTAL" in r.get("message", "")
              and r.get("next"), "good mesh -> success + experimental note", r)
        check(read_file(arc, name) != before, "archive bytes changed")
    finally:
        if read_file(arc, name) != before:
            put_back(arc, name, before)
    check(read_file(arc, name) == before, "character bundle restored byte-identical")


def main():
    try:
        level_part()
        weapon_part()
        character_part()
    except Exception as ex:                                   # noqa: BLE001
        check(False, "test crashed", repr(ex))
    print("\n%s" % ("FAIL: " + "; ".join(FAILURES) if FAILURES else "PASS"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
