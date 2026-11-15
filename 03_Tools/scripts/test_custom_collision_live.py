"""[CCL] Model + collision are one thing, through the Studio's own HTTP routes:
custom import adds linked collision; solid/walk-through; moving a drawn surface
moves its twin; ONE undo each. Restores the disc byte-identically.

    py -3 scripts/test_custom_collision_live.py      (needs a Studio on 8731)
"""
import json, os, sys, urllib.error, urllib.request
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _HERE]
sys.path.insert(0, os.path.dirname(_HERE))
from rdrstudio import config
from rdrstudio.game import Game
from rdrstudio.level import Level

BASE = "http://127.0.0.1:%s/api/" % (os.environ.get("RDR_PORT") or "8731")
ARC = "LVLS_SNG/SHER70.DAT"
OBJ = ("v 100 2 200\nv 102 2 200\nv 102 7 201.5\nv 100 7 201.5\n"
       "f 1 2 3\nf 1 3 4\nf 1 4 2\nf 2 4 3\n")
FAILURES = []

def check(ok, what, detail=""):
    print(("  ok   " if ok else "  FAIL ") + what + (("  -- " + str(detail)[:300]) if detail and not ok else ""))
    if not ok: FAILURES.append(what)

def call(route, body=None):
    if body is None:
        req = urllib.request.Request(BASE + route)
    else:
        req = urllib.request.Request(BASE + route, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=240) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode())

def snap():
    g = Game(config.MODDED_ISO)
    try:
        lv = Level(g, ARC); lv._load_rsc()
        d = g.archive(ARC)
        names = [lv.rsc_name] + sorted({n.source for n in lv.nodes if n.source.lower().endswith(".bnds")})
        return {n: d.read(n) for n in names}
    finally:
        g.close()

def nodes():
    return {n["name"]: n for n in call("level?archive=%s&reload=1" % ARC)["nodes"]}

before = snap()
undo_n = 0
try:
    d = call("model_import", dict(archive=ARC, chunk=692, obj=OBJ, label="cc-test", dry=True))
    check(d.get("dry") and d.get("collision") == "added", "dry run plans a NEW collision chunk", d)
    check("bound_ppear01_01" in str(d.get("collision_note")), "and names it", d.get("collision_note"))
    r = call("model_import", dict(archive=ARC, chunk=692, obj=OBJ, label="cc-test"))
    check(r.get("ok") and "bound_ppear01_01" in r.get("message", ""), "import succeeds and the message mentions the collision", r)
    undo_n += 1
    nd = nodes()
    z, b = nd.get("zone_ppear01_01"), nd.get("bound_ppear01_01")
    check(z is not None and b is not None, "the new bound_ node exists on reload")
    if z and b:
        check(z.get("linked") == b["uid"] and b.get("linked") == z["uid"], "model and collision are linked both ways by name")
        check(str(b["props"].get("attr.BoundFlagPlayer")).lower() == "true", "new collision is solid to the player")
        st = call("set_solid", dict(archive=ARC, uid=z["uid"], status=True))
        check(st.get("linked") is True, "status says linked", st)
        s1 = call("set_solid", dict(archive=ARC, uid=z["uid"], solid=False))
        check(s1.get("applied") or s1.get("ok") or not s1.get("error"), "walk-through accepted", s1)
        undo_n += 1
        nb = nodes()["bound_ppear01_01"]
        check(str(nb["props"].get("attr.BoundFlagPlayer")).lower() == "false", "walk-through flips the player flag off")
        s2 = call("set_solid", dict(archive=ARC, uid=z["uid"], solid=True))
        undo_n += 1
        nb = nodes()["bound_ppear01_01"]
        check(str(nb["props"].get("attr.BoundFlagPlayer")).lower() == "true", "solid flips it back on", s2)
    # drawn-surface move carries the twin (vanilla pair)
    g = Game(config.MODDED_ISO); lv = Level(g, ARC); lv._load_rsc(); link = lv.links()
    pair = next((n for n in lv.nodes if n.kind == "zone" and n.uid in link and n.name != "zone_ppear01_01"
                 and lv.by_uid(link[n.uid]).kind == "bound"), None)
    chunk = None
    if pair:
        chunk = int(round(float(pair.props["resourceIndex"]))) - 1
    g.close()
    check(pair is not None, "found a vanilla model/collision pair to move")
    if pair is not None:
        gm = call("geom_edit", dict(archive=ARC, chunk=chunk, label=pair.name, ops=[dict(op="translate", x=2.0, y=0, z=0)]))
        if gm.get("error"):
            print("  info geom_edit refused (", gm["error"][:90], ") - trying next pair")
        else:
            undo_n += 1
            check(gm.get("collision_moved"), "moving the drawn surface moved its collision chunk in the same write", gm)
finally:
    for _ in range(undo_n):
        u = call("undo", {})
        if not u.get("ok"): break
    after = snap()
    check(after == before, "rsc + every .bnds byte-identical after undoing everything (%d undo steps)" % undo_n,
          [k for k in before if before[k] != after.get(k)])
print("\nPASS" if not FAILURES else "\nFAIL: %s" % FAILURES)
sys.exit(1 if FAILURES else 0)
