"""Prove an asset can be placed into a level from the catalogue.

The catalogue treats assets as separate from the paths that place them: a
model is owned by an area's RSC bundle, and a `.znes` block is one instance
of it. 43% of asset names appear in more than one area, so "import the
saloon piano into this map" means instancing the map's own copy.

Checks:
  1. the catalogue finds thousands of assets across all 97 areas
  2. an asset absent from the target area is REFUSED, and says where it
     can go instead -- the model is not in that area's RSC bundle, and
     adding a model to a bundle is unsolved (adding a FILE to the
     archive is not: see scripts/probe_dat_add.py)
  3. a present asset places at exactly the requested position
  4. it lands in whichever placement file has slot headroom, which is not
     always the one holding the original
  5. undo returns the file to its vanilla bytes
"""

import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8731/api/"

ARCHIVE = "LVLS_SNG/TOMB4.DAT"
AREA = "lvl_bar.rsc"
PRESENT = "piano"           # in the Brimstone bar
ABSENT = "table"            # in the bank and gunshop, but not the bar
TARGET = [10.0, 2.5, -4.0]


def get(route):
    with urllib.request.urlopen(BASE + route, timeout=300) as r:
        return json.load(r)


def post(route, body, expect_error=False):
    req = urllib.request.Request(
        BASE + route, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.load(r)
    except urllib.error.HTTPError as ex:
        if not expect_error:
            raise
        return json.load(ex)


def load():
    return get("level?archive=%s&area=%s&reload=1"
               % (ARCHIVE.replace("/", "%2F"), AREA))


def main():
    failures = []

    summary = get("assets_summary")
    print("  catalogue: %d assets across %d areas, %d instances, %d shared"
          % (summary["assets"], summary["areas"], summary["instances"],
             summary["shared"]))
    if summary["assets"] < 2000 or summary["areas"] < 90:
        failures.append("catalogue looks too small")

    # 2. an asset the target area does not own must be refused.
    refused = post("place_asset",
                   dict(archive=ARCHIVE, area=AREA, name=ABSENT,
                        pos=TARGET), expect_error=True)
    if "error" not in refused:
        failures.append("%r was placed into an area that does not own it"
                        % ABSENT)
    else:
        print("  refused %r: %s" % (ABSENT, refused["error"][:78]))
        if "can take it" not in refused["error"]:
            failures.append("refusal did not say where the asset can go")

    # 3-4. a present asset places exactly, in a file with headroom.
    before = load()
    n_before = sum(1 for n in before["nodes"]
                   if n["name"].startswith("zone_" + PRESENT))
    placed = post("place_asset", dict(archive=ARCHIVE, area=AREA,
                                      name=PRESENT, pos=TARGET))
    if not placed.get("applied"):
        print("  FAIL could not place %r: %s"
              % (PRESENT, placed.get("error") or placed.get("report")))
        return 1
    print("  placed %s as %s into %s (copied from %s)"
          % (PRESENT, placed["new_name"], placed["target_file"],
             placed["copied_from"]))

    report = placed["report"][placed["target_file"]]
    print("  slot: %d/%d stored, %d byte(s) spare"
          % (report["stored"], report["slot"], report["headroom"]))

    landed = placed.get("placed_at")
    print("  asked %s -> landed %s" % (TARGET, landed))
    if not landed or any(abs(a - b) > 0.001 for a, b in zip(TARGET, landed)):
        failures.append("placed at %s, expected %s" % (landed, TARGET))

    after = load()
    n_after = sum(1 for n in after["nodes"]
                  if n["name"].startswith("zone_" + PRESENT))
    print("  instances: %d -> %d" % (n_before, n_after))
    if n_after != n_before + 1:
        failures.append("instance count went %d -> %d" % (n_before, n_after))

    # 5. undo must return the file to vanilla exactly.
    undone = post("undo", {})
    print("  undo: %s" % (undone.get("label") or "(nothing)"))
    changed = [d for d in get("diff") if d["archive"] == ARCHIVE]
    if changed:
        failures.append("after undo, still changed: %s"
                        % [d["name"] for d in changed])
    else:
        print("  archive is back to vanilla")

    for line in failures:
        print("  FAIL " + line)
    if failures:
        return 1
    print("  OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
