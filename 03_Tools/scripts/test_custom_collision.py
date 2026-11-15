#!/usr/bin/env python3
"""[CC] A custom model gets matching collision: a solid box BND0 (new chunk, or
the existing twin's chunk replaced), a bound_<stem> node that links to the model
by name. Offline: nothing is written to any ISO.

    py -3 scripts/test_custom_collision.py
"""
import os, sys, struct
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from rdrstudio.config import VANILLA_ISO
from rdrstudio.game import Game
from rdrstudio.level import Level
from rdrstudio import custcoll as C, boundmove as BM, rsc as R, scene

fails = []
def check(ok, msg):
    print(("PASS " if ok else "FAIL ") + msg)
    if not ok: fails.append(msg)

d = C.selftest()
check(d <= 16, "identity fit reproduces the stock template (%d byte(s) of float noise)" % d)
raw = C.box_collision((10, 2, 20), (12, 7, 21.5))
au = BM.audit(raw)
check(all(v for k, v in au.items() if k != "nodes"), "generated box passes the shell audit %s" % au)
vs = C._verts(raw)
check(min(v[0] for v in vs) == 10 and max(v[1] for v in vs) == 7, "vertices span exactly the asked box")
check(abs(C._area(vs, C._faces(raw)[0][1]) - struct.unpack_from("<f", raw, C._faces(raw)[0][0] + 12)[0]) < 1e-4,
      "face areas match their quads")
thin = C.box_collision((0, 0, 0), (0.01, 3, 0.01))
tv = C._verts(thin)
check(max(v[0] for v in tv) - min(v[0] for v in tv) >= C.MIN_SIDE - 1e-6, "paper-thin models still get a walk-blocking minimum")

g = Game(VANILLA_ISO)
lv = Level(g, "LVLS_SNG/SHER70.DAT"); lv._load_rsc()
bundle = g.archive("LVLS_SNG/SHER70.DAT").read(lv.rsc_name)
n0 = len(R.Rsc(bundle).chunks)
obj = "v 100 2 200\nv 102 2 200\nv 102 7 201.5\nv 100 7 201.5\nf 1 2 3\n"
p = C.plan(lv, bundle, 692, obj)
check(p["ok"] and p["mode"] == "added", "no-twin model (ppear01 chunk 692): new collision chunk planned: %s" % (p.get("note") or p.get("why")))
if p["ok"] and p["mode"] == "added":
    nr = R.Rsc(p["bundle"])
    check(len(nr.chunks) == n0 + 1 and nr.chunks[-1].tag == "BND0", "bundle gained exactly one BND0 chunk (%d -> %d)" % (n0, len(nr.chunks)))
    check(not R.check_bundle(p["bundle"], "x"), "bundle still passes the RSC0 invariants")
    mb = nr.bounds().get(p["chunk"] + 1) or nr.bounds().get(p["chunk"])
    check(mb is not None and len(mb.verts) == 8, "the new chunk decodes as an 8-vertex shell")
    fn, ed = next(iter(p["edits"].items()))
    new = scene.splice(lv._read(fn), ed)
    nodes = {n.name: n for n in scene.parse_any(fn, new)}
    nb = nodes.get(p["name"])
    check(nb is not None and int(round(float(nb.props.get("resourceIndex")))) == p["chunk"] + 1,
          "new node %s points at the new chunk (resourceIndex %d)" % (p["name"], p["chunk"] + 1))
    check(Level.link_stem(p["name"]) == Level.link_stem("zone_ppear01_01"), "its name stem equals the model's -> Level.links() pairs them")
    check(nb.props.get("attr.BoundFlagPlayer") == "true" and nb.props.get("attr.BoundFlagMover") == "true", "it is solid to the player")
pw = C.plan(lv, bundle, 692, obj)
check(pw["ok"] == p["ok"], "planning is repeatable and writes nothing")
# a twin model -> replace mode
link = lv.links()
z = next(n for n in lv.nodes if n.kind == "zone" and n.uid in link and C._res_index(n))
pr = C.plan(lv, bundle, C._res_index(z) - 1, obj)
check(pr["ok"] and pr["mode"] == "replaced" and len(R.Rsc(pr["bundle"]).chunks) == n0,
      "model with a twin (%s): its collision chunk is replaced in place" % z.name)
# a chunk no node uses
pn = C.plan(lv, bundle, 0, obj)
check(not pn["ok"] and pn["why"], "chunk with no scene object says why: %r" % pn.get("why", "")[:70])
print("RESULT", "FAIL" if fails else "PASS")
sys.exit(1 if fails else 0)
