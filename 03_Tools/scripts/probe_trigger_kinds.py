"""What kinds of trigger and volume subtype does the disc actually use?

`probe_name_tokens.py` showed each volume's TYPE token (TRIGGER, AIROOM,
CAMDIFF, ...). Several of those carry a THIRD segment that names the specific
behaviour -- `box_TRIGGER_FallDeath_crackpost_01`, `box_CAMCONTEXT_Wagon_...`,
`box_AIROOM_Medium_...`. That third word is the closest thing on this disc to
an answer for "where does the player get a prompt", so it is worth enumerating
exactly rather than sampling.

Also counts splines by suffix, because a spline's species is in its LAST
segment as often as its first (`line_armouryC_rail`, `line_coach_wheels`).
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

# The types worth breaking down. Chosen because their examples showed a
# meaningful third segment rather than an object name.
DETAIL = ("TRIGGER", "CAMCONTEXT", "CAMDIFF", "CAMROOM", "CAMROOMDIFF",
          "AIROOM", "AIAVOID", "AISNEAK", "AIDOOR", "AISUBROOM",
          "WALKRUNROOM", "WALKROOM", "TELEPORT", "RAIL")


def main():
    game = Game(DISC, writable=False)
    archives = [a for a in game.archives() if a.startswith("LVLS")]

    sub = defaultdict(Counter)        # type -> third segment -> count
    where = defaultdict(set)          # type -> archives
    suffix = Counter()                # spline last segment
    suffix_eg = {}

    for arc in archives:
        try:
            lv = Level(game, arc)
        except Exception:
            continue
        for n in lv.nodes:
            parts = n.name.split("_")
            if n.kind == "box" and len(parts) > 2:
                t = parts[1]
                if t in DETAIL:
                    sub[t][parts[2]] += 1
                    where[t].add(arc)
            if n.kind == "line" and len(parts) > 1:
                s = parts[-1]
                # trailing _01 / _001 is an instance number, not a species
                if s.isdigit() or s.lstrip("0").isdigit():
                    s = parts[-2] if len(parts) > 2 else s
                suffix[s] += 1
                suffix_eg.setdefault(s, n.name)

    for t in DETAIL:
        if not sub[t]:
            continue
        rows = sub[t].most_common()
        print("\n%s -- %d subtypes, %d archives" % (t, len(rows), len(where[t])))
        for s, c in rows[:24]:
            print("    %-24s %5d" % (s, c))
        if len(rows) > 24:
            print("    ... and %d more" % (len(rows) - 24))

    print("\n\nspline species by LAST segment (top 30):")
    for s, c in suffix.most_common(30):
        print("    %-16s %5d   e.g. %s" % (s, c, suffix_eg[s][:48]))

    game.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
