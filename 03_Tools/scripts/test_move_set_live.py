"""[SET] A model made of several pieces moves as ONE thing, and the collision
welded into the level's merged shells moves with it -- through the Studio's own
routes (move / geom_edit / mass_edit). Drift-free, one undo, disc restored.

    py -3 scripts/test_move_set_live.py      (needs a Studio on 8731)

In-game proof of the same operation: 05_Docs/ingame-proof/move_set_*.
"""
import json, os, struct, sys, urllib.error, urllib.request
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _HERE]
sys.path.insert(0, os.path.dirname(_HERE))
from rdrstudio import config, collparts as CP, bndtree as T, scene
from rdrstudio.game import Game
from rdrstudio.level import Level

BASE = "http://127.0.0.1:%s/api/" % (os.environ.get("RDR_PORT") or "8731")
ARC = "LVLS_SNG/SHER70.DAT"
SET = ["zone_watertowerS_01", "zone_zoneset_waterTower_01_01",
       "zone_troughS_01", "zone_alpha_01_water_01"]
FAILURES = []
f32 = lambda x: struct.unpack("<f", struct.pack("<f", x))[0]


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


def disc(iso=None):
    """(files that matter, {shell chunk: verts}, {zone name: pos}, audits)"""
    g = Game(iso or config.MODDED_ISO)
    try:
        lv = Level(g, ARC); r = lv._load_rsc()
        d = g.archive(ARC)
        names = [lv.rsc_name] + sorted({n.source for n in lv.nodes})
        files = {n: d.read(n) for n in names}
        shells, bad = {}, []
        for n in lv.nodes:
            if n.kind == "bound":
                ci = scene.chunk_of(n)
                if ci is None or ci in shells: continue
                raw = r.raw(ci); shells[ci] = T.parse(raw)["verts"]
                a = T.audit(raw)
                if a["missing"] or a["tight_bad"] or a["faces_listed"] != a["faces"]: bad.append((ci, a))
        pos = {n.name: n.xyz() for n in lv.nodes if n.kind == "zone"}
        return files, shells, pos, bad
    finally:
        g.close()


def stock_claim():
    g = Game(config.VANILLA_ISO)
    try:
        lv = Level(g, ARC); r = lv._load_rsc()
        by = {n.name: n for n in lv.nodes}
        c = CP.claim(lv, r, [by[n].uid for n in SET])
        verts = {ci: T.parse(r.raw(ci))["verts"] for ci in c["shells"]}
        chunks = {n: scene.chunk_of(by[n]) for n in SET}
        big = max((n for n in lv.nodes if n.kind == "zone"),
                  key=lambda n: (lambda b: (b[1][0] - b[0][0]) * (b[1][2] - b[0][2]))(CP.world_box(lv, r, n)))
        return c, verts, chunks, big.name, len(CP.group_of(lv, r, big.uid))
    finally:
        g.close()


def nodes():
    return {n["name"]: n for n in call("level?archive=%s&reload=1" % ARC)["nodes"]}


def moved_ok(shells, claim, sverts, d, what):
    wrong = 0; n = 0
    for ci, idx in claim["shells"].items():
        inset = set(idx)
        for i, sv in enumerate(sverts[ci]):
            want = tuple(f32(sv[k] + d[k]) for k in range(3)) if i in inset else sv
            n += i in inset
            if shells[ci][i] != want: wrong += 1
    check(wrong == 0 and n > 0, "%s: all %d claimed collision vertices are exactly stock%+g,%+g,%+g; every other vertex untouched" % ((what, n) + tuple(d)), wrong)


before, shells0, pos0, bad0 = disc()
claim, sverts, chunks, bigname, bigset = stock_claim()
check(claim["pieces"] >= 10 and len(claim["shells"]) >= 2, "stock: the water tower's collision is found inside merged shells",
      (claim["pieces"], claim["faces"], sorted(claim["shells"])))
check(bigset <= 2, "a map-sized model (%s) is never treated as a set" % bigname, bigset)
nd = nodes()
check(all(n in nd for n in SET), "the four drawn pieces of the water tower are in the level")
anchor = None
for nm in SET:
    st = call("set_solid", dict(archive=ARC, uid=nd[nm]["uid"], status=True))
    if set(SET) - {nm} <= set(st.get("set") or []):
        anchor = nm; ast = st
check(anchor is not None, "one piece's box holds the other three: picking it selects the whole set", anchor)
if anchor:
    NSET = len(ast["set"]) + 1          # the four pieces + anything else standing inside (a weed)
    check("welded" in (ast.get("note") or "") and ast.get("merged", {}).get("pieces") == claim["pieces"],
          "the inspector says its collision is welded in and moves with it", ast)
    # ---- 1. drag (node move): one action on ONE piece
    D1 = (-2.0, 0.0, -8.0)
    r = call("move", dict(archive=ARC, changes=[dict(uid=nd[anchor]["uid"], pos=list(D1))], label="set test"))
    check(r.get("applied") and r.get("set") and r["set"][0]["count"] == NSET, "move: applied, %d pieces moved as a set" % NSET, r)
    check((r.get("collision") or {}).get("shells") == len(claim["shells"]) and r["collision"]["pieces"] == claim["pieces"],
          "move: collision pieces followed (%d pieces, %d faces)" % (claim["pieces"], claim["faces"]), r.get("collision"))
    f1, sh1, pos1, bad1 = disc()
    check(all(tuple(pos1[n]) == D1 for n in SET), "move: all four drawn pieces are at the new place", {n: pos1[n] for n in SET})
    check(not bad1, "move: every collision shell's index is true for its own faces", bad1[:2])
    moved_ok(sh1, claim, sverts, D1, "move")
    # ---- 2. move it again: measured from stock, no drift
    D2 = (-4.0, 0.0, -8.0)
    r = call("move", dict(archive=ARC, changes=[dict(uid=nd[anchor]["uid"], pos=list(D2))], label="set test 2"))
    f2, sh2, pos2, bad2 = disc()
    check(r.get("applied") and all(tuple(pos2[n]) == D2 for n in SET) and not bad2, "second move: applied, pieces together, shells sound", r)
    moved_ok(sh2, claim, sverts, D2, "second move")
    for i in range(2):
        u = call("undo", {})
    f3 = disc()[0]
    check(f3 == before, "two undos put the disc back byte for byte (each move = ONE undo step)", [k for k in before if before[k] != f3.get(k)])
    # ---- 3. reshape controls (geometry patch) on the same piece
    D3 = (3.0, 0.0, 1.0)
    r = call("geom_edit", dict(archive=ARC, chunk=chunks[anchor], label=anchor, ops=[dict(op="translate", x=D3[0], y=D3[1], z=D3[2])]))
    check(r.get("changed") and (r.get("set") or {}).get("count") == NSET and not r.get("collision_moved") and (r.get("collision") or {}).get("shells") == len(claim["shells"]),
          "reshape: whole set moved, collision followed", r)
    f4, sh4, pos4, bad4 = disc()
    check(not bad4, "reshape: shells sound", bad4[:2])
    moved_ok(sh4, claim, sverts, D3, "reshape")
    call("undo", {})
    f5 = disc()[0]
    check(f5 == before, "reshape: one undo restores the disc", [k for k in before if before[k] != f5.get(k)])
    # ---- 4. selection of all four (mass edit)
    r = call("mass_edit", dict(archive=ARC, chunks=[chunks[n] for n in SET], label="set", ops=[dict(op="translate", x=D3[0], y=D3[1], z=D3[2])]))
    check(r.get("changed") and (r.get("collision") or {}).get("shells") == len(claim["shells"]), "selection move: collision followed", r)
    f6, sh6, pos6, bad6 = disc()
    check(not bad6, "selection move: shells sound", bad6[:2])
    moved_ok(sh6, claim, sverts, D3, "selection move")
    call("undo", {})
    f7 = disc()[0]
    check(f7 == before, "selection move: one undo restores the disc", [k for k in before if before[k] != f7.get(k)])
    # ---- 5. mixed history: reshape, then drag, then reshape again while it
    #         sits at the dragged place. The collision is measured from the
    #         drawn model (stock -> live), so the steps add up.
    def drawn_fn():
        g0 = Game(config.VANILLA_ISO); g1 = Game(config.MODDED_ISO)
        try:
            l0 = Level(g0, ARC); l1 = Level(g1, ARC)
            n0 = [n for n in l0.nodes if n.name == anchor][0]
            n1 = [n for n in l1.nodes if n.name == anchor][0]
            ft = CP.fit_xform(CP.world_verts(l0._load_rsc(), n0), CP.world_verts(l1._load_rsc(), n1))
            return ft[0] if ft else None
        finally:
            g0.close(); g1.close()

    def follows(sh, fn, what):
        wrong = 0; n = 0
        for ci, idx in claim["shells"].items():
            inset = set(idx)
            for i, sv in enumerate(sverts[ci]):
                want = fn(*sv) if i in inset else sv
                n += i in inset
                if max(abs(x - y) for x, y in zip(sh[ci][i], want)) > 5e-3: wrong += 1
        check(wrong == 0 and n > 0, what, wrong)
    call("geom_edit", dict(archive=ARC, chunk=chunks[anchor], label=anchor, ops=[dict(op="translate", x=D3[0], y=D3[1], z=D3[2])]))
    r = call("move", dict(archive=ARC, changes=[dict(uid=nd[anchor]["uid"], pos=list(D1))], label="set test mixed"))
    fa, sha, posa, bada = disc()
    check(r.get("applied") and not bada and (r.get("collision") or {}).get("measured"),
          "mixed: drag after a reshape applied, collision measured from the drawn model", r.get("collision"))
    follows(sha, lambda x, y, z: (x + D3[0] + D1[0], y + D3[1] + D1[1], z + D3[2] + D1[2]),
            "mixed: reshape THEN drag = collision at stock + both (the reshape is not lost)")
    r = call("geom_edit", dict(archive=ARC, chunk=chunks[anchor], label=anchor, ops=[dict(op="rotate_y", deg=90)]))
    fb, shb, posb, badb = disc()
    g = drawn_fn()
    check(r.get("changed") and not badb and g is not None, "mixed: a turn while it sits at the dragged place applied, shells sound", r.get("collision"))
    if g:
        far = sum(1 for ci in claim["shells"] for i in claim["shells"][ci]
                  if max(abs(x - y) for x, y in zip(shb[ci][i], sha[ci][i])) > 0.05)
        check(far > 100, "mixed: the turn moved the collision (%d vertices)" % far)
        follows(shb, g, "mixed: drag THEN turn = collision exactly where the drawn model is")
    for i in range(3):
        call("undo", {})
    fd = disc()[0]
    check(fd == before, "mixed: three undos restore the disc", [k for k in before if before[k] != fd.get(k)])
after = disc()[0]
if after != before:
    for i in range(6):
        if not call("undo", {}).get("ok"): break
        if disc()[0] == before: break
print("\nPASS" if not FAILURES else "\nFAIL: %s" % FAILURES)
import time; time.sleep(6)      # let the Studio finish saving its history before a runner stops it
sys.exit(1 if FAILURES else 0)
