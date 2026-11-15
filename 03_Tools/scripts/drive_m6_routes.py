"""Drive mass_edit ([M6]) and the boundmove-backed geom_edit ([M16]) over HTTP.

**This driver used to destroy data on a dirty disc.** It restored with
`revert_file`, which restores from VANILLA, and then asserted the disc's
changed-file list matched the one captured at start. If `lvl_int2.rsc` was
already modified when the driver ran, the revert overwrote that edit with
vanilla bytes and the comparison then failed -- so the run that ate the edit
reported FAIL and the next one, on the now-clean disc, passed. That is how the
loss reads as a flake and survives re-running.

Fixed with the same pattern as `test_roundtrip.py` (2718ed6): checkpoint before,
`checkpoint_restore` after (byte-exact, keeps edits this driver did not make),
and the changed-file comparison asserts the SET rather than a sorted name list.
"""
import json, os, sys, urllib.request, urllib.error
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

ARC  = "LVLS_M2/INT2.DAT"
RSC  = "lvl_int2.rsc"
MODS = [93, 94, 95]
BND  = 59

def call(route, body=None, method="POST"):
    if body is None:
        with urllib.request.urlopen(BASE + route, timeout=TIMEOUT) as fh:
            return fh.getcode(), json.load(fh)
    req = urllib.request.Request(BASE + route, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as fh:
            return fh.getcode(), json.load(fh)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)

ok = True
def check(label, cond, extra=""):
    global ok
    print("  [%s] %s%s" % ("ok  " if cond else "FAIL", label,
                           ("  -- " + str(extra)) if extra else ""))
    if not cond: ok = False

_, before = call("diff", method="GET")
baseline = {(x["archive"], x["name"]) for x in before}
print("diff before: %s" % sorted(n for _, n in baseline))

# Take the restore point BEFORE touching anything. A checkpoint stores the
# bytes of every file that differs from vanilla, so restoring it later puts
# the disc back exactly as found -- including edits this driver did not make
# and must not destroy. On a clean disc it stores nothing and the restore
# undoes only our own write, which is the old behaviour exactly.
c, cp = call("checkpoint", {"label": "auto_before_drive_m6"})
cp_path = cp.get("path")
print("restore point: %s (%d pre-existing edit(s))"
      % (os.path.basename(cp_path or "<none>"), len(baseline)))
if not cp_path:
    print("REFUSING: no restore point, so a failure could not be undone.")
    sys.exit(2)

print("\n== [M6] mass_edit refusals over HTTP ==")
c, r = call("mass_edit", {"archive": ARC, "chunks": [MODS[0]], "ops": [], "dry": True})
check("1-chunk selection refused with 400", c == 400 and "2 or more" in r.get("error",""), r.get("error"))
c, r = call("mass_edit", {"archive": ARC, "chunks": MODS, "ops": [{"op":"wobble"}], "dry": True})
check("unknown op refused, named", c == 400 and "wobble" in r.get("error",""), r.get("error"))

print("\n== [M6] mass_edit dry run ==")
c, dry = call("mass_edit", {"archive": ARC, "chunks": MODS,
                            "ops": [{"op":"scale","x":2.0},
                                    {"op":"translate","x":1.0,"y":0,"z":0}],
                            "dry": True})
check("dry run 200", c == 200, dry.get("error"))
check("models == 3", dry.get("models") == 3, dry.get("models"))
check("pivot reported", isinstance(dry.get("pivot"), list) and len(dry["pivot"]) == 3, dry.get("pivot"))
check("proportional True for uniform", dry.get("proportional") is True)
check("collision_note present", bool(dry.get("collision_note")), (dry.get("collision_note") or "")[:60])
check("dry wrote nothing", dry.get("dry") is True)
_, d2 = call("diff", method="GET")
check("disc unchanged by dry run",
      {(x["archive"], x["name"]) for x in d2} == baseline)

print("\n== [M6] per-axis scale reports NOT proportional ==")
c, pa = call("mass_edit", {"archive": ARC, "chunks": MODS,
                           "ops": [{"op":"scale","x":2.0,"y":1.0,"z":1.0}], "dry": True})
check("proportional False", pa.get("proportional") is False, pa.get("proportional"))

print("\n== [M6] mass_edit REAL write ==")
c, w = call("mass_edit", {"archive": ARC, "chunks": MODS,
                          "ops": [{"op":"scale","x":2.0},
                                  {"op":"translate","x":1.0,"y":0,"z":0}],
                          "label": "M6 route drive"})
check("write 200", c == 200, w.get("error"))
check("changed True", w.get("changed") is True, w)
check("fits its slot", w.get("used") is not None and w.get("used") <= w.get("slot", 0),
      "%s/%s" % (w.get("used"), w.get("slot")))
check("report still carried on the write", w.get("models") == 3 and bool(w.get("collision_note")))
_, d3 = call("diff", method="GET")
# The SET, not a count of new entries: the subject file may well have been
# modified before we arrived, which is the case a count cannot express.
check("modified set is exactly the baseline plus our subject",
      {(x["archive"], x["name"]) for x in d3} == baseline | {(ARC, RSC)},
      sorted(x["name"] for x in d3))

print("\n== [M6] the disc really returns the new geometry ==")
c, sh = call("geom_shape", {"archive": ARC, "chunk": MODS[0]})
check("geom_shape reads a transform back", sh.get("shape") is not None, sh.get("why"))
if sh.get("shape"):
    print("     recovered: %s" % {k: round(v,4) for k,v in sh["shape"].items()
                                  if isinstance(v,(int,float))})

print("\n== restore ==")
# Restore the CHECKPOINT, not vanilla. `revert_file` would discard a
# pre-existing edit to this file, which is the bug this driver carried.
c, rr = call("checkpoint_restore", {"path": cp_path})
check("checkpoint_restore ok", rr.get("ok") is True, rr.get("failed"))
_, d4 = call("diff", method="GET")
after = {(x["archive"], x["name"]) for x in d4}
check("disc back to baseline (%d pre-existing edit(s))" % len(baseline),
      after == baseline)
for k in sorted(after - baseline):
    print("   LEFT BEHIND %s :: %s" % k)
for k in sorted(baseline - after):
    print("   DESTROYED   %s :: %s" % k)

print("\nRESULT: %s" % ("PASS" if ok else "FAIL"))
sys.exit(0 if ok else 1)
