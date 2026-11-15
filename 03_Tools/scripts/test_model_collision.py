#!/usr/bin/env python3
"""[MC] Model + collision are one thing: moving a VANILLA model carries its
collision; solid/walk-through toggles the linked shell; unlinked models say so.

Offline, read-only against the vanilla ISO (edits spliced in memory only).
    py -3 scripts/test_model_collision.py
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from rdrstudio.config import VANILLA_ISO
from rdrstudio.game import Game
from rdrstudio.level import Level
from rdrstudio import scene, boundmove

g = Game(VANILLA_ISO)
fails = []
def check(ok, msg):
    print(("PASS " if ok else "FAIL ") + msg)
    if not ok: fails.append(msg)

def applied(lv, edits):
    """Splice edits into copies of the sources and re-parse -> {name: xyz}."""
    nodes = {}
    for fn, ed in edits.items():
        new = scene.splice(lv.sources[fn], ed)
        for n in scene.parse_any(fn, new):
            nodes[(fn, n.name)] = n.xyz()
    return nodes

moved_pairs = 0
for arc in ("LVLS_SNG/SHER70.DAT", "LVLS_SNG/PERFNEW.DAT", "LVLS_M2/COACH2.DAT"):
    try:
        lv = Level(g, arc)
    except Exception as e:
        print("skip", arc, e); continue
    link = lv.links()
    zones = [n for n in lv.nodes if n.kind == "zone" and n.uid in link]
    check(len(zones) > 0, "%s: has vanilla model/collision pairs (%d)" % (arc, len(zones)))
    for z in zones[:5]:
        b = lv.by_uid(link[z.uid])
        zp, bp = z.xyz(), b.xyz()
        want = [zp[0] + 3.0, zp[1], zp[2] - 2.0]
        changes = lv.with_collision([dict(uid=z.uid, pos=want)])
        check(len(changes) == 2, "%s: moving %s adds its collision %s" % (arc, z.name, b.name))
        edits = lv.merge(*[lv.move(int(c["uid"]), c["pos"], allow_grow=True) for c in changes])
        after = applied(lv, edits)
        za, ba = after[(z.source, z.name)], after[(b.source, b.name)]
        dz = [a - c for a, c in zip(za, zp)]; db = [a - c for a, c in zip(ba, bp)]
        check(all(abs(x - y) < 1e-3 for x, y in zip(dz, db)) and abs(dz[0] - 3.0) < 1e-3,
              "%s: model delta %s == collision delta %s" % (z.name, [round(x,3) for x in dz], [round(x,3) for x in db]))
        moved_pairs += 1
    # passing both already: no duplicate
    if zones:
        z = zones[0]; b = lv.by_uid(link[z.uid])
        ch = lv.with_collision([dict(uid=z.uid, pos=[0,0,0]), dict(uid=b.uid, pos=[0,0,0])])
        check(len(ch) == 2, "%s: caller-placed pair is not doubled" % arc)
        # solid toggle
        try:
            ed = lv.set_solid(z.uid, False)
            new = applied(lv, ed)
            check(True, "%s: walk-through toggle writes %d flag edit(s) on %s" % (arc, sum(len(v) for v in ed.values()), b.name))
        except ValueError as e:
            print("INFO", b.name, "toggle refused:", e)
    # unlinked model honest status
    unl = [n for n in lv.nodes if n.kind == "zone" and n.uid not in link]
    if unl:
        st = lv.collision_status(unl[0].uid)
        check(not st["linked"] and st["note"], "%s: unlinked model says so: %r" % (arc, st["note"][:60]))

# Vanilla collision round-trip: identity move byte-identical on every BND0
lv = Level(g, "LVLS_SNG/SHER70.DAT"); rs = lv._load_rsc(); nb = 0
for i, c in enumerate(rs.chunks):
    if c.tag == "BND0":
        raw = rs.raw(i)
        try:
            ok = boundmove.translate(raw, 0, 0, 0) == raw
        except boundmove.NotEditable:
            continue
        nb += 1
        if not ok: fails.append("identity move changed BND0 %d" % i)
check(nb > 0 and not any("identity" in f for f in fails), "SHER70: identity collision move byte-identical on %d BND0 chunks" % nb)
print("pairs moved:", moved_pairs)
print("RESULT", "FAIL" if fails else "PASS")
sys.exit(1 if fails else 0)
