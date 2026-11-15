"""probe_props_gap.py -- how much of the text corpus does props.py mis-nest?

`rdrstudio/props.py` is the module every text-format editor in the tool is
built on. It tracks blocks with two regexes:

    _OPEN  = ^[ \\t]*([A-Za-z_]\\w*)?[ \\t]*\\{[ \\t]*$      name then '{', alone
    _CLOSE = ^[ \\t]*\\}[ \\t]*$                            '}' alone on its line

probe_textgrammar.py found that the disc uses two spellings those patterns do
not cover:

    Node 0 {            a block head with an ARGUMENT before the brace
     1 }                a '}' sharing its line with data

Neither matches, so the first is recorded as a *field* named `Node` with the
value `0 {` and never pushes a block, and the second never pops one. Both
failures are invisible to a round-trip test, because props copies every line it
does not understand verbatim -- the bytes stay perfect while the block paths
drift. That is the same class of bug props.py's own comments describe twice
already (the CRLF `_CLOSE` miss, and the dotted-key miss).

This probe measures the damage over every ASCII entry on the disc:

  A  BLOCK OPENS MISSED  lines that open a block but do not match _OPEN
  B  BLOCK CLOSES MISSED lines that close one but do not match _CLOSE
  C  DEPTH DRIFT         files where props' stack is not empty at EOF although
                         the file's braces are balanced -- every field after
                         the first miss carries a wrong path
  D  ROUND-TRIP          apply(data, {}) still returns the input byte for byte
  E  BOOBY-TRAPPED       fields props OFFERS FOR EDITING whose value still
                         contains the block's '{'. Editing one through the
                         normal path overwrites the brace and unbalances the
                         file -- this is a live corruption route, not a
                         cosmetic parse miss.

    python3 03_Tools/research/probe_props_gap.py [--iso PATH] [--ext nav,sphs]
"""

import argparse
import collections
import json
import os
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
from rdrstudio.iso9660 import Iso9660          # noqa: E402
from rdrstudio import dave, props              # noqa: E402
from probe_textgrammar import split_lines, TEXT_OK      # noqa: E402

REPO = os.path.dirname(os.path.dirname(HERE))
VANILLA = os.path.join(REPO, "01_Vanilla", "RDR_VANILLA.iso")
TEXTFORM = os.path.join(REPO, "07_Research", "textform.json")


def props_depth_walk(data):
    """Replay props.parse's block bookkeeping and report where it diverges.

    Deliberately a copy of the real control flow rather than a call into it:
    parse() returns fields, not the stack, and the question here is what the
    stack DID, not what came out.
    """
    stack = []
    opens_missed = closes_missed = 0
    pending = b""
    for raw in data.split(b"\n"):
        line = raw
        stripped = line.strip()
        true_open = stripped.count(b"{")
        true_close = stripped.count(b"}")
        if stripped.startswith(b";") or not stripped:
            continue
        if props._CLOSE.match(stripped):
            if stack:
                stack.pop()
            continue
        m = props._OPEN.match(stripped)
        if m:
            name = (m.group(1) or b"") or pending
            stack.append(name)
            pending = b""
            continue
        if props._BARE_WORD.match(stripped):
            pending = stripped
            continue
        # props falls through to _KEY here and pushes/pops nothing.
        if true_open > true_close:
            opens_missed += 1
        elif true_close > true_open:
            closes_missed += 1
        pending = b""
    return len(stack), opens_missed, closes_missed


def inflate(backing, e):
    raw = backing.read_at(e.offset, e.size_comp)
    return raw if not e.compressed else zlib.decompress(raw, -15)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso", default=VANILLA)
    ap.add_argument("--ext", default="")
    ap.add_argument("--json", default=os.path.join(REPO, "07_Research", "propsgap.json"))
    args = ap.parse_args()

    tf = json.load(open(TEXTFORM))
    if args.ext:
        want = {e.strip().lower().lstrip(".") for e in args.ext.split(",")}
    else:
        want = {e for e, s in tf.items() if s["verdict"] == "TEXT"}

    iso = Iso9660(args.iso)
    stat = collections.defaultdict(lambda: {
        "files": 0, "files_with_blocks": 0, "opens_missed": 0,
        "closes_missed": 0, "drifted": 0, "rt_fail": 0, "example": None,
        "trap_fields": 0, "trap_files": 0, "trap_example": None,
    })

    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b"Dave", b"DAVE"):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        sys.stderr.write("%-30s\n" % f.path)
        for e in d.entries:
            ext = e.ext or "(none)"
            if ext not in want:
                continue
            try:
                blob = inflate(d.backing, e)
            except zlib.error:
                continue
            if not blob or any(c not in TEXT_OK for c in blob):
                continue
            s = stat[ext]
            s["files"] += 1

            lines = split_lines(blob.decode("ascii"))
            true_depth = 0
            n_blocks = 0
            for l in lines:
                n_blocks += l.opens
                true_depth += l.opens - l.closes
            if n_blocks:
                s["files_with_blocks"] += 1

            left, om, cm = props_depth_walk(blob)
            s["opens_missed"] += om
            s["closes_missed"] += cm
            if true_depth == 0 and left != 0:
                s["drifted"] += 1
                if s["example"] is None:
                    s["example"] = {"archive": f.path, "name": e.name,
                                    "props_stack_left": left,
                                    "opens_missed": om, "closes_missed": cm}
            if props.apply(blob, {}) != blob:
                s["rt_fail"] += 1

            # (E) fields whose value swallowed the brace
            traps = [fl for fl in props.parse(blob) if "{" in fl.raw]
            if traps:
                s["trap_fields"] += len(traps)
                s["trap_files"] += 1
                if s["trap_example"] is None:
                    t = traps[0]
                    after = props.apply(blob, {t.path: "99"})
                    s["trap_example"] = {
                        "archive": f.path, "name": e.name, "path": t.path,
                        "value": t.raw,
                        "braces_before": blob.count(b"{"),
                        "braces_after": after.count(b"{"),
                    }

    out = {}
    for ext, s in stat.items():
        s = dict(s)
        s["verdict"] = ("CLEAN" if not s["drifted"] and not s["opens_missed"]
                        and not s["closes_missed"] and not s["trap_fields"]
                        else "MIS-NESTED")
        out[ext] = s
    with open(args.json, "w") as fh:
        json.dump(out, fh, indent=1)

    rows = sorted(out.items(), key=lambda kv: -kv[1]["trap_fields"])
    print("%-16s %7s %9s %9s %9s %9s" % (
        "EXT", "FILES", "OPENMISS", "DRIFTED", "TRAPFILES", "TRAPFLDS"))
    for ext, s in rows:
        if s["verdict"] == "CLEAN":
            continue
        print("%-16s %7d %9d %9d %9d %9d" % (
            ext, s["files"], s["opens_missed"], s["drifted"],
            s["trap_files"], s["trap_fields"]))
    nd = sum(s["drifted"] for s in out.values())
    nf = sum(s["files"] for s in out.values())
    nrt = sum(s["rt_fail"] for s in out.values())
    bad_ext = [e for e, s in out.items() if s["verdict"] == "MIS-NESTED"]
    print("\n%d of %d text files drift (props' block stack never empties) "
          "across %d extensions" % (nd, nf, len(bad_ext)))
    print("round-trip failures: %d  (a no-op apply() must be a no-op)" % nrt)
    tfl = sum(s["trap_files"] for s in out.values())
    tfd = sum(s["trap_fields"] for s in out.values())
    print("BOOBY-TRAPPED: %d files offer %d editable fields whose value still "
          "holds the block brace;\n  editing any one of them deletes a '{' "
          "and unbalances the file." % (tfl, tfd))
    print("-> %s" % args.json)
    iso.close()


if __name__ == "__main__":
    main()
