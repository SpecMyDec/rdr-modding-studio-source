"""Why is the camera framed so far out when a level opens?

The audit measured `Level.bounds()` returning the same 218 x 187 x 196 m box for
every Brimstone interior -- 3.11x too large on TOMB, 2.99x on MANS -- and blamed
one spline, `line_room_newgrnd`. This checks that claim per level and per node,
so the fix targets whatever is actually responsible rather than a suspect.

For each level it prints the full AABB, then the AABB with the single most
extreme node removed, then with all splines removed, then over the DRAWN
geometry only -- which is what the user actually sees and therefore what the
camera should frame.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdrstudio.game import Game                      # noqa: E402
from rdrstudio.level import Level, list_levels        # noqa: E402
from rdrstudio.config import VANILLA_ISO             # noqa: E402

# The STOCK disc, not the working one: a read-only structural probe must
# not depend on what the last test left behind (REQ-035). The repoint was
# measured output-neutral at the time it was made -- see REQ-074.
DISC = Path(VANILLA_ISO)
LEVELS = ["LVLS_SNG/TOMB.DAT", "LVLS_SNG/MANS.DAT", "LVLS_SNG/PERFNEW.DAT",
          "LVLS_SNG/BRIDGE.DAT", "LVLS_SNG/TRAIN.DAT"]


def aabb(points):
    lo = [1e30] * 3
    hi = [-1e30] * 3
    n = 0
    for p in points:
        n += 1
        for i in range(3):
            lo[i] = min(lo[i], p[i])
            hi[i] = max(hi[i], p[i])
    if not n:
        return None
    return lo, hi


def size(box):
    if not box:
        return (0, 0, 0)
    lo, hi = box
    return tuple(round(hi[i] - lo[i], 1) for i in range(3))


def node_points(n):
    out = [n.xyz()]
    for v in n.verts:
        out.append([c.value for c in v])
    return out


def main():
    game = Game(DISC, writable=False)
    for arc in LEVELS:
        try:
            lv = Level(game, arc)
        except Exception as ex:
            print("%s FAILED %s" % (arc, str(ex)[:60]))
            continue
        print("\n" + "=" * 70)
        print("%s -- %d nodes" % (arc, len(lv.nodes)))
        print("=" * 70)

        allpts = [p for n in lv.nodes for p in node_points(n)]
        full = aabb(allpts)
        print("  everything                : %s" % (size(full),))

        # Which single node contributes the extreme?
        worst = None
        for n in lv.nodes:
            pts = node_points(n)
            b = aabb(pts)
            if not b:
                continue
            reach = max(max(abs(c) for c in b[0]), max(abs(c) for c in b[1]))
            if worst is None or reach > worst[0]:
                worst = (reach, n)
        if worst:
            print("  furthest-reaching node    : %-38s (%s, %d verts, reach %.0f m)"
                  % (worst[1].name[:38], worst[1].kind,
                     len(worst[1].verts), worst[0]))
            rest = [p for n in lv.nodes if n is not worst[1]
                    for p in node_points(n)]
            print("  without that one node     : %s" % (size(aabb(rest)),))

        no_lines = [p for n in lv.nodes if n.kind != "line"
                    for p in node_points(n)]
        print("  without any splines       : %s" % (size(aabb(no_lines)),))

        # What the user can actually SEE: buildings, collision, spawns, enemies.
        drawn = [p for n in lv.nodes
                 if n.kind in ("zone", "bound", "spawn", "enemy")
                 for p in node_points(n)]
        print("  drawn things only         : %s" % (size(aabb(drawn)),))

        boxes = [p for n in lv.nodes if n.kind == "box" for p in node_points(n)]
        print("  volumes only              : %s" % (size(aabb(boxes)),))

        # Name the splines that reach furthest -- is it really room_newgrnd?
        far = []
        for n in lv.nodes:
            if n.kind != "line":
                continue
            b = aabb(node_points(n))
            if not b:
                continue
            far.append((max(max(abs(c) for c in b[0]),
                            max(abs(c) for c in b[1])), n.name, len(n.verts)))
        far.sort(reverse=True)
        print("  furthest splines:")
        for reach, name, nv in far[:5]:
            print("      %-42s %6.0f m  %d verts" % (name[:42], reach, nv))

    game.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
