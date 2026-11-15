"""`features.py`'s DEFAULTS list says the same thing as `features.json`.

DEFAULTS is the fallback the app reads when the shipped manifest is missing
or unreadable. Two files, one answer, and only one of them has an owner who
edits it -- which is the shape this project has now hit three times
(ROADMAP.md, the spec's own example; features.json before ship_manifest();
and this list, which had drifted from the manifest in three separate ways
while nothing checked).

What the drift costs is not symmetrical, because `enabled()` reads an id it
has never heard of as OFF:

  * an id in features.json and NOT here -> that feature is silently absent
    wherever the fallback is in force. Eleven were, `behaviour_tables` among
    them, and it gates a panel.
  * `build` disagreeing -> the feature is switched the wrong way. This list
    had scripting_browser as `both` against the manifest's `internal`, which
    is an unfinished panel turned ON in a release build.
  * `rung` disagreeing -> the app tells the user something the manifest does
    not say. place_enemy was still `untested` here after the manifest
    measured it.

`why` is deliberately NOT mirrored, and not compared. ship_manifest() strips
it from the release manifest on purpose -- each one is a candid note about
what is unproven, written for us -- but features.py is compiled into BOTH
editions, so a `why` in this list ships to the public whatever the edition
filter does. The switches are what DEFAULTS exists to get right; the prose
lives in features.json, which is the file that gets filtered.

So this is a lockfile check, and like a lockfile it comes with the command
that regenerates it:

    python scripts/verify_feature_defaults.py --write

features.json is the authority -- a human edits it, and this list follows.
Never the other way round.
"""
import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

# `why` is absent on purpose -- see the note above. Adding it here would put
# the internal notes back into the public build.
FIELDS = ("label", "workspace", "rung", "build")
SOURCE = HERE / "rdrstudio" / "features.py"
# The generated span. Everything above the opening line is hand-written and
# survives a --write; the entries themselves do not.
BLOCK = re.compile(r"(?m)^DEFAULTS = \[\n.*?^\]\n", re.S)


def manifest():
    """The authority. Unreadable is a refusal, not a failure -- see CLAUDE.md.

    Absent features.json is exactly the state DEFAULTS exists to survive, so
    the app is right to carry on; this check is not, because it has nothing
    to compare against and a silent pass would say the two agree.
    """
    try:
        with open(HERE / "features.json", encoding="utf-8") as fh:
            return {f["id"]: f for f in json.load(fh)["features"]}
    except (OSError, ValueError, KeyError, TypeError) as ex:
        print("  could not read %s: %s" % (HERE / "features.json", ex))
        print("Nothing has been checked.")
        sys.exit(2)


def render(man):
    """The DEFAULTS block, in the house style the hand-written one used."""
    out = ["DEFAULTS = ["]
    for fid in sorted(man):
        f = man[fid]
        out.append('    dict(id=%r, label=%r,' % (fid, f["label"]))
        out.append('         workspace=%r, rung=%r, build=%r),'
                   % (f["workspace"], f["rung"], f["build"]))
    out.append("]")
    return "\n".join(out) + "\n"


def compare(man):
    # Imported here, not at the top: --write's whole job is to repair this
    # file, and it cannot do that if reading it is a precondition for
    # starting.
    from rdrstudio import features
    have = {d["id"]: d for d in features.DEFAULTS}
    rows = []
    for fid in sorted(set(man) | set(have)):
        if fid not in have:
            rows.append((fid, "missing from DEFAULTS", man[fid]["build"]))
        elif fid not in man:
            rows.append((fid, "not in features.json", have[fid].get("build")))
        else:
            for k in FIELDS:
                if have[fid].get(k) != man[fid].get(k):
                    rows.append((fid, "%s differs" % k, ""))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true",
                    help="regenerate DEFAULTS from features.json")
    args = ap.parse_args()
    man = manifest()

    if args.write:
        src = SOURCE.read_text(encoding="utf-8")
        if not BLOCK.search(src):
            # Nothing ran and nothing changed: exit(2), not a failure verdict.
            print("  no DEFAULTS block found in %s" % SOURCE)
            print("Nothing has been written.")
            return 2
        SOURCE.write_text(BLOCK.sub(lambda _: render(man), src, count=1),
                          encoding="utf-8")
        print("wrote %d features into %s" % (len(man), SOURCE))
        return 0

    rows = compare(man)
    for fid, what, detail in rows:
        print("   %-20s %-22s %s" % (fid, what, detail))
    from rdrstudio import features
    print("%d features in features.json, %d in DEFAULTS, %d disagreement(s)"
          % (len(man), len(features.DEFAULTS), len(rows)))
    if rows:
        print("regenerate with: python scripts/verify_feature_defaults.py "
              "--write")
    print("PASS" if not rows else "FAIL")
    return 0 if not rows else 1


if __name__ == "__main__":
    sys.exit(main())
