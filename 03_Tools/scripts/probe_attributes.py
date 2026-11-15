"""Every per-instance attribute on the disc, with counts.

`scene.py` used to parse past the `attributes N { }` block on every .znes and
.bnds node and throw it away. It turns out to hold two quite different things:

  * RENDER flags on .znes nodes -- NoShadows, TwoSided, AlphaBucket, and the
    texture-scroll rates. These say WHY a surface draws the way it does.
  * COLLISION behaviour flags on .bnds nodes -- BoundFlagPlayer,
    BoundFlagHorse, BoundFlagWater, BoundFlagPlummet and friends. These say
    WHAT a collision surface does: who collides with it, whether it is water,
    whether falling in it kills you.

The second group was a surprise, so this enumerates all of it properly rather
than documenting the handful that happened to show up in one level.
"""
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdrstudio.game import Game                      # noqa: E402
from rdrstudio.level import Level                    # noqa: E402
from rdrstudio.config import VANILLA_ISO             # noqa: E402

# The STOCK disc, not the working one: a read-only structural probe must
# not depend on what the last test left behind (REQ-035). The repoint was
# measured output-neutral at the time it was made -- see REQ-074.
DISC = Path(VANILLA_ISO)


def main():
    game = Game(DISC, writable=False)
    archives = [a for a in game.archives() if a.startswith("LVLS")]
    print("%d level archives" % len(archives))

    names = defaultdict(Counter)       # kind -> attr name -> count
    values = defaultdict(Counter)      # attr name -> value -> count
    per_kind_nodes = Counter()
    with_attrs = Counter()
    example = {}

    for arc in archives:
        try:
            lv = Level(game, arc)
        except Exception:
            continue
        for n in lv.nodes:
            per_kind_nodes[n.kind] += 1
            got = False
            for k, v in n.props.items():
                if not k.startswith("attr."):
                    continue
                got = True
                a = k[5:]
                names[n.kind][a] += 1
                values[a][str(v)] += 1
                example.setdefault(a, "%s (%s)" % (n.name, n.source))
            if got:
                with_attrs[n.kind] += 1

    print("\nnodes carrying at least one attribute:")
    for kind in sorted(per_kind_nodes):
        tot = per_kind_nodes[kind]
        n = with_attrs[kind]
        print("  %-7s %6d of %6d  (%.1f%%)"
              % (kind, n, tot, 100.0 * n / tot if tot else 0))

    for kind in sorted(names):
        print("\n" + "=" * 72)
        print("%s -- %d distinct attribute names" % (kind, len(names[kind])))
        print("=" * 72)
        for a, c in names[kind].most_common():
            vals = values[a].most_common(4)
            vs = ", ".join("%s x%d" % (v, k) for v, k in vals)
            print("  %-26s %6d nodes   values: %s" % (a, c, vs[:60]))
            print("       e.g. %s" % example[a][:64])

    game.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
