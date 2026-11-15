"""What does the game call the things in its levels?

Every volume and spline in a .bxes / .lnes carries an uppercase type token in
its own name -- `box_TRIGGER_saloon_01`, `line_OCCLUDE_02`. The tool currently
throws all of it away and draws one gold box and one gold line, which is why
nothing in a level says what it does.

This enumerates the real tokens across every level archive, with counts and
example names, so the glossary in the UI is a description of this disc rather
than a guess.
"""
import re
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


def token(name):
    """The type token: the part after the `box_` / `line_` prefix.

    Names run `<kind>_<TOKEN>_<what>_<nn>`, but plenty are `box_room_x` with a
    lowercase word, and some have no token at all. Returning the raw second
    segment keeps this honest -- the UI can decide what to do with lowercase.
    """
    parts = name.split("_")
    return parts[1] if len(parts) > 1 else "(none)"


def main():
    game = Game(DISC, writable=False)
    archives = [a for a in game.archives() if a.startswith("LVLS")]
    print("%d level archives" % len(archives))

    tok = defaultdict(Counter)          # kind -> token -> count
    examples = defaultdict(dict)        # kind -> token -> example name
    props_seen = defaultdict(Counter)   # kind -> prop key -> count
    empty = Counter()
    total = Counter()
    per_archive = defaultdict(set)

    for arc in archives:
        try:
            lv = Level(game, arc)
        except Exception as ex:
            print("  %-28s FAILED %s" % (arc, str(ex)[:50]))
            continue
        for n in lv.nodes:
            k = n.kind
            total[k] += 1
            t = token(n.name)
            tok[k][t] += 1
            examples[k].setdefault(t, n.name)
            per_archive[k].add(arc)
            props = n.props or {}
            if not props:
                empty[k] += 1
            for key in props:
                props_seen[k][key] += 1

    for kind in sorted(tok):
        print("\n=== %s  (%d nodes across %d archives, %d with NO props) ==="
              % (kind, total[kind], len(per_archive[kind]), empty[kind]))
        rows = tok[kind].most_common()
        print("  %d distinct name tokens" % len(rows))
        for t, c in rows:
            print("    %-18s %6d   e.g. %s" % (t, c, examples[kind][t][:52]))
        if props_seen[kind]:
            print("  props keys seen: %s"
                  % ", ".join("%s(%d)" % (k, v)
                              for k, v in props_seen[kind].most_common(12)))

    game.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
