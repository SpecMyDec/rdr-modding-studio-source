"""Every level area on the disc decodes all of its render geometry.

Three separate defects each hid large amounts of world:

  * VIF UNPACK payloads are padded to a 32-bit word. Advancing by the
    unpadded length desynced the walk mid-model and dropped the remainder.
  * An archive is not one level. TOMB4 holds Brimstone plus six interiors
    as sibling bundles; only one was ever loaded, and for the archives with
    a Variation digit in the name the match failed entirely and a character
    LOD model stood in for the world.
  * The largest MOD0 in a level is a container of many sub-packets, not one
    display list. Those went undecoded -- in lvl_mansroof that was a single
    430 KB chunk.

Together these took render coverage from 97.2% of chunks / 563k triangles
to 100% / 1.4M.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rdrstudio import Game
from rdrstudio.config import VANILLA_ISO
from rdrstudio.level import Level

# Brimstone's seven areas, and the two archives whose names carry a mission
# Variation digit that must be trimmed before the bundle can be found.
EXPECTED_AREAS = {
    "LVLS_SNG/TOMB4.DAT": 7,
    "LVLS_SNG/TOMB.DAT": 7,
    "LVLS_SNG/ROYDUSK6.DAT": 3,
    "LVLS_SNG/WOODS.DAT": 2,
    "LVLS_SNG/CORE.DAT": 2,
}


def main():
    game = Game(VANILLA_ISO)
    archives = [a for a in game.archives() if a.startswith("LVLS")]
    failures = []
    chunks = decoded = tris = 0
    areas = 0
    containers = 0

    for archive in archives:
        base = Level(game, archive)
        found = base.areas()
        want = EXPECTED_AREAS.get(archive)
        if want is not None and len(found) != want:
            failures.append("%s: expected %d areas, found %d"
                            % (archive, want, len(found)))

        for area in (found or [None]):
            areas += 1
            level = base if area == base.area else Level(game, archive,
                                                         area=area)
            render = level.render_geometry()
            chunks += render["chunks"]
            decoded += render["decoded"]
            tris += render["tris"]
            if render["chunks"] and render["decoded"] < render["chunks"]:
                failures.append("%s [%s]: only %d/%d chunks decoded"
                                % (archive, area, render["decoded"],
                                   render["chunks"]))

            # A container MOD0 states its own vertex count per sub-mesh.
            # That is the check that proves the format was read correctly
            # rather than merely producing plausible-looking floats.
            rsc = level._load_rsc()
            if rsc is None:
                continue
            for chunk in rsc.chunks:
                if chunk.tag != "MOD0":
                    continue
                parts = rsc.parts(chunk.index)
                if not parts:
                    continue
                containers += 1
                model = rsc.model(chunk.index)
                stated = sum(p["verts"] for p in parts)
                got = len(model["verts"]) if model else 0
                if got != stated:
                    failures.append(
                        "%s [%s] chunk %d: container declares %d vertices, "
                        "decoded %d" % (archive, area, chunk.index, stated,
                                        got))

    print("  %d archives, %d areas, %d container models"
          % (len(archives), areas, containers))
    print("  %d/%d MOD0 chunks decoded (%.2f%%), %d triangles"
          % (decoded, chunks, 100.0 * decoded / max(1, chunks), tris))

    if decoded != chunks:
        failures.append("render coverage is not complete")
    if tris < 1300000:
        failures.append("only %d triangles; expected at least 1.3M" % tris)

    for line in failures[:20]:
        print("  FAIL " + line)
    if failures:
        print("  %d failure(s)" % len(failures))
        return 1
    print("  OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
