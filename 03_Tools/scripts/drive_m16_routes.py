"""Drive the boundmove-backed geom_edit collision branch ([M16]) over HTTP.

**This driver used to destroy data on a dirty disc, and then certify the disc
was correct.** It restored with `revert_file` (which restores from VANILLA) and
then asserted `shell_off_disc() == stock`, with stock read from VANILLA_ISO --
so that assertion PASSED precisely because a pre-existing edit to `lvl_int2.rsc`
had just been destroyed. Only the changed-file comparison would ever complain.
That is the sharpest instance of the class: a passing condition satisfied by
something other than what was meant.

Fixed with the same pattern as `test_roundtrip.py` (2718ed6): checkpoint before,
`checkpoint_restore` after, the changed-file comparison asserts the SET, and the
byte comparison is against the bytes FOUND on the working disc rather than
against vanilla. `stock` is still read from vanilla, but only for the old-mover
comparison, which is what it is legitimately for.
"""
import json, os, sys, urllib.request, urllib.error

# Derived, not hardcoded: a `...` literal is the defect 0780649
# removed from test_shell.py, and it makes the file unrunnable anywhere else.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rdrstudio import config, boundmove
from rdrstudio.game import Game
from rdrstudio.level import Level

# The port is taken from the environment, NOT hardcoded. These drivers write
# whatever disc the Studio on this port is holding, so a careful run wants them
# on a port of its own, pointed at a copy -- and a file that can only be
# isolated by editing it is a file nobody isolates. Hardcoding 8731 cost two
# runs on 2026-09-18: once by putting a wrong-disc Studio on the shared suite
# port, where rdr-app found it before a suite did, and once by making an
# isolated check lose the bind race for that same port to an outgoing suite's
# Studio. Same class as the `...` literal removed in 0780649.
#
# The default is unchanged, so anything that ran before still runs the same way.
PORT = int(os.environ.get("RDR_PORT") or 8731)
BASE = "http://127.0.0.1:%d/api/" % PORT
# /api/diff walks every archive comparing entry contents. Measured 2026-09-18
# against a copy of the working disc: 273s cold, 212s after a write, 80s fully
# warm -- so it is CPU-bound comparison, not merely a cold page cache, and the
# floor does not drop below ~80s. The old 120s ceiling meant these drivers died
# on their FIRST call against any disc that was not already warm, exiting 1
# having done nothing -- indistinguishable from the failure a negative control
# is looking for, and a trap for anyone careful enough to test on a fresh copy.
# `checkpoint` is built on diff(), so the POST side needs the same headroom.
TIMEOUT = 900

ARC, RSC, BND = "LVLS_M2/INT2.DAT", "lvl_int2.rsc", 59
DELTA = (25.0, 3.0, -17.0)

def call(route, body):
    req = urllib.request.Request(BASE + route, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as fh:
            return fh.getcode(), json.load(fh)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)

def get(route):
    with urllib.request.urlopen(BASE + route, timeout=TIMEOUT) as fh:
        return json.load(fh)

ok = True
def check(label, cond, extra=""):
    global ok
    print("  [%s] %s%s" % ("ok  " if cond else "FAIL", label,
                           ("  -- " + str(extra)) if extra else ""))
    if not cond: ok = False

def shell_off_disc():
    g = Game(config.MODDED_ISO, writable=False)
    vr = Level(g, ARC)._load_rsc()
    return vr.raw(BND)

before = get("diff")
baseline = {(x["archive"], x["name"]) for x in before}
print("diff before: %s" % sorted(n for _, n in baseline))

# Take the restore point BEFORE touching anything, and keep the bytes this
# shell ACTUALLY has on the working disc. Everything the restore is checked
# against below is this, not vanilla.
c, cp = call("checkpoint", {"label": "auto_before_drive_m16"})
cp_path = cp.get("path")
print("restore point: %s (%d pre-existing edit(s))"
      % (os.path.basename(cp_path or "<none>"), len(baseline)))
if not cp_path:
    print("REFUSING: no restore point, so a failure could not be undone.")
    sys.exit(2)
found = shell_off_disc()

print("\n== [M16] the shell on disc audits clean (baseline) ==")
a0 = boundmove.audit(found)
check("baseline clean", all(v for k, v in a0.items() if k != "nodes"), a0)

print("\n== [M16] refusal over HTTP ==")
c, r = call("geom_edit", {"archive": ARC, "chunk": BND,
                          "ops": [{"op": "wobble"}], "dry": True})
check("unknown op refused with 400", c == 400 and "wobble" in r.get("error", ""),
      r.get("error"))

print("\n== [M16] REAL move through the route ==")
c, w = call("geom_edit", {"archive": ARC, "chunk": BND, "label": "M16 drive",
                          "ops": [{"op": "translate", "x": DELTA[0],
                                   "y": DELTA[1], "z": DELTA[2]}]})
check("write 200", c == 200, w.get("error"))
check("changed True", w.get("changed") is True, w)

print("\n== [M16] the shell ON DISC agrees with itself about where it is ==")
moved = shell_off_disc()
a1 = boundmove.audit(moved)
for k in ("centre_matches", "origin_dist_matches", "verts_inside_aabb",
          "root_covers_verts", "nodes_inside_root"):
    check(k, a1.get(k) is True)
check("node count preserved", a1["nodes"] == a0["nodes"], "%s -> %s" % (a0["nodes"], a1["nodes"]))

# The decisive comparison: what the OLD mover would have produced, from the
# same stock bytes, fails the very checks the route's output passes.
g = Game(config.VANILLA_ISO, writable=False)
stock = Level(g, ARC)._load_rsc().raw(BND)
from rdrstudio import geomedit
old = geomedit.translate_bound(stock, *DELTA)
bad = sorted(k for k, v in boundmove.audit(old).items() if v is False)
check("the OLD mover would have failed these", bad == ["centre_matches",
      "origin_dist_matches", "root_covers_verts"], bad)
check("...and the route's output does NOT", moved != old)
check("...though both are the same length", len(moved) == len(old) == len(stock))

print("\n== restore ==")
c, rr = call("checkpoint_restore", {"path": cp_path})
check("checkpoint_restore ok", rr.get("ok") is True, rr.get("failed"))
after = {(x["archive"], x["name"]) for x in get("diff")}
check("disc back to baseline (%d pre-existing edit(s))" % len(baseline),
      after == baseline)
for k in sorted(after - baseline):
    print("   LEFT BEHIND %s :: %s" % k)
for k in sorted(baseline - after):
    print("   DESTROYED   %s :: %s" % k)
# Against what was FOUND, not against vanilla. The old check compared to
# stock and so passed exactly when a pre-existing edit had been destroyed.
check("shell back to the bytes found on the disc", shell_off_disc() == found)

print("\nRESULT: %s" % ("PASS" if ok else "FAIL"))
sys.exit(0 if ok else 1)
