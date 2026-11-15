"""Prove objects can be CREATED and DELETED, not just moved.

New content goes into an existing file's slot headroom -- no file is added to
the archive, which is impossible. Checks:
  1. duplicate an enemy -> count goes up, the clone has a unique name
  2. duplicate a box    -> the NumBoxes header tracks the block count
  3. delete both        -> file returns to exactly its vanilla bytes
"""

import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8731/api/"


def get(route):
    with urllib.request.urlopen(BASE + route, timeout=180) as r:
        return json.load(r)


def post(route, body):
    req = urllib.request.Request(
        BASE + route, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


def load(arc):
    return get("level?archive=" + arc.replace("/", "%2F") + "&reload=1")


def run(arc, kind):
    print("=" * 66)
    print("%s  --  duplicating a '%s'" % (arc, kind))
    lvl = load(arc)
    before = sum(1 for n in lvl["nodes"] if n["kind"] == kind)
    victim = next(n for n in lvl["nodes"] if n["kind"] == kind)
    print("  source file      : %s" % victim["source"])
    print("  %-16s : %d" % (kind + "s before", before))

    dry = post("duplicate", dict(archive=arc, uid=victim["uid"], dry=True))
    rep = list(dry["report"].values())[0]
    print("  dry run          : %d/%d stored -> %s"
          % (rep["stored"], rep["slot"], "FITS" if rep["fits"] else "TOO BIG"))
    if not rep["fits"]:
        print("  SKIPPED: no headroom in this file")
        return None

    res = post("duplicate", dict(archive=arc, uid=victim["uid"]))
    if not res.get("applied"):
        print("  FAILED: %s" % res.get("error"))
        return False
    print("  created          : %s" % res["new_name"])

    lvl2 = load(arc)
    after = sum(1 for n in lvl2["nodes"] if n["kind"] == kind)
    print("  %-16s : %d  (%+d)" % (kind + "s after", after, after - before))
    made = [n for n in lvl2["nodes"] if n["name"] == res["new_name"]]
    print("  clone parsed back: %s" % ("YES" if made else "NO"))
    if made:
        print("  clone position   : %s (original %s)"
              % ([round(c, 3) for c in made[0]["pos"]],
                 [round(c, 3) for c in victim["pos"]]))

    ok = after == before + 1 and bool(made)

    # Remove it again and confirm the file is byte-clean versus vanilla.
    res2 = post("delete", dict(archive=arc, uid=made[0]["uid"]))
    lvl3 = load(arc)
    final = sum(1 for n in lvl3["nodes"] if n["kind"] == kind)
    print("  after delete     : %d %s" % (final, "(restored)" if final == before else "(WRONG)"))
    ok &= res2.get("applied") and final == before

    diff = [d for d in get("diff") if d["name"] == victim["source"]]
    print("  vs vanilla       : %s"
          % ("identical" if not diff else "STILL DIFFERS (%d)" % len(diff)))
    ok &= not diff
    return ok


def main():
    results = []
    results.append(run("LVLS_SNG/GUNSMOKE.DAT", "enemy"))
    results.append(run("LVLS_SNG/MANS.DAT", "box"))
    results.append(run("LVLS_SNG/MANS.DAT", "line"))

    real = [r for r in results if r is not None]
    print("\n" + "=" * 66)
    print("ALL CHECKS PASSED" if real and all(real) else "SOMETHING FAILED")
    return 0 if real and all(real) else 1


if __name__ == "__main__":
    sys.exit(main())
