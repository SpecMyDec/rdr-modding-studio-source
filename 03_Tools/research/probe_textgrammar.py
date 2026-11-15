"""probe_textgrammar.py -- one grammar behind 109 of the game's file types?

probe_textform.py showed that 44% of the disc's archive entries are ASCII.
This probe tests the stronger claim: that they are all instances of ONE
line-oriented grammar, and that a structure-preserving parser can rebuild any
of them BYTE-FOR-BYTE.

That last property is the whole point. The roadmap's acceptance bar for the
character formats is "edits a field, writes, re-reads it byte-exactly, and
asserts every unedited byte is untouched". A line-preserving model gives that
for free: a line you did not edit is re-emitted as the bytes it arrived as.

Three claims are tested over every text entry on the disc:

  R  ROUND-TRIP   parse -> emit reproduces the input byte for byte.
  B  BRACES       every '{' closes, nesting never goes negative.
  N  COUNTS       a "NumThing N" header is followed by N records of that thing
                  (this is what adding an enemy or a hotspot has to maintain).

    python3 03_Tools/research/probe_textgrammar.py [--iso PATH] [--ext swt,cr]
"""

import argparse
import collections
import json
import os
import re
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from rdrstudio.iso9660 import Iso9660          # noqa: E402
from rdrstudio import dave                     # noqa: E402

REPO = os.path.dirname(os.path.dirname(HERE))
VANILLA = os.path.join(REPO, "01_Vanilla", "RDR_VANILLA.iso")
TEXTFORM = os.path.join(REPO, "07_Research", "textform.json")

TEXT_OK = set(range(0x20, 0x7F)) | {0x09, 0x0A, 0x0D}


# ------------------------------------------------------------------ the model

class Line:
    """One physical line, kept verbatim plus a parsed view of it.

    `raw` is the line WITHOUT its terminator; `eol` is the exact terminator
    seen ("\\n", "\\r\\n", or "" at a final unterminated line). Keeping the
    terminator per-line is what lets a file with mixed endings round-trip.

    Braces are TOKENS, not line markers. The first cut of this probe assumed a
    '}' owned its line and reported 83 of 289 .nav files as unbalanced; they
    are fine -- .nav writes "Nodes 9 1" style rows and closes with " 1 }" on a
    line that carries data. Count braces inside the line and the false alarms
    go away.
    """
    __slots__ = ("raw", "eol", "indent", "tokens", "opens", "closes")

    def __init__(self, raw, eol):
        self.raw = raw
        self.eol = eol
        self.indent = raw[:len(raw) - len(raw.lstrip())]
        self.opens = raw.count("{")
        self.closes = raw.count("}")
        self.tokens = raw.replace("{", " ").replace("}", " ").split()

    @property
    def key(self):
        return self.tokens[0].rstrip(":") if self.tokens else None

    @property
    def is_block_head(self):
        """Opens a block: has a name and a '{' here or as the next non-blank."""
        return self.opens > self.closes

    def emit(self):
        return self.raw + self.eol


def split_lines(text):
    """Split keeping each terminator exactly as it was."""
    out = []
    i = 0
    n = len(text)
    while i < n:
        j = text.find("\n", i)
        if j < 0:
            out.append(Line(text[i:], ""))
            break
        raw = text[i:j]
        eol = "\n"
        if raw.endswith("\r"):
            raw, eol = raw[:-1], "\r\n"
        out.append(Line(raw, eol))
        i = j + 1
    return out


def emit(lines):
    return "".join(l.emit() for l in lines)


def brace_check(lines):
    depth = 0
    for n, l in enumerate(lines, 1):
        depth += l.opens - l.closes
        if depth < 0:
            return "negative depth at line %d" % n
    return None if depth == 0 else "unclosed depth %d at EOF" % depth


RECORD_SUFFIXES = ("data", "info", "entry", "def", "")

RE_NUM = re.compile(r"^Num(?:ber[Oo]f)?([A-Za-z0-9_]+)$")


def _singular(word):
    """Plural -> singular, conservatively.

    The naive "strip -es" rule turned Bones into "bon" and Nodes into "nod",
    which silently cost 247 .nav and 1119 .skel headers their typed match.
    -es only drops whole when the stem ends in a sibilant (Boxes -> box);
    otherwise just the -s comes off.
    """
    w = word.lower()
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith("es") and len(w) > 3:
        stem = w[:-2]
        if stem.endswith(("s", "x", "z", "ch", "sh")):
            return stem
        return w[:-1]
    if w.endswith("s") and not w.endswith("ss") and len(w) > 2:
        return w[:-1]
    return w


def count_check(lines):
    """Does "NumFoos 3" declare how many Foo blocks the file contains?

    Two rules are tested, because the first cut of this probe conflated them
    and called 34,942 headers "mismatched" when most were not record counts at
    all:

      SCOPED  count every block opened between this header and the next
              top-level Num* header. Wrong for .nav, which writes BOTH headers
              up front and then both groups of blocks.
      TYPED   count blocks anywhere in the file whose head token matches the
              header's noun -- NumNodes -> `Node 0 {`, NumBones -> `Bone ... {`.

    Plenty of Num* keys are not counts in either sense: .sm's NumRoundsToWin
    is a gameplay value, and a Node block's own NumLinks field is data. Those
    show up as TYPED-unprovable (no block of that name exists) and are
    reported separately rather than as failures -- claiming them as failures
    would be inventing a convention the game does not follow.
    """
    # Every block head token in the file.
    #
    # Two spellings are in use and both must be attributed to the same name:
    #     Node 0 {          -- brace on the naming line
    #     BoundHotdog x     -- brace on the NEXT line (.sphs, .nav)
    #     {
    # Missing the second spelling is what made 515 .sphs files look like they
    # had no BoundSphere blocks at all.
    heads = collections.Counter()
    prev_name = None
    for l in lines:
        if l.is_block_head:
            name = l.tokens[0] if l.tokens else prev_name
            if name:
                heads[_singular(name.rstrip(":"))] += 1
        if l.tokens:
            prev_name = l.tokens[0]

    total_blocks = sum(heads.values())

    out = []
    depth = 0
    for i, l in enumerate(lines):
        at_top = depth == 0
        depth += l.opens - l.closes
        if not at_top or len(l.tokens) < 2 or l.opens:
            continue
        m = RE_NUM.match(l.tokens[0].rstrip(":"))
        if not m:
            continue
        try:
            want = int(l.tokens[1])
        except ValueError:
            continue
        noun = _singular(m.group(1))
        typed = heads.get(noun)
        if typed is None:
            # The block may spell the record with a suffix -- .gait declares
            # NumSteps and writes `StepData {`. Allow only the suffixes the
            # corpus actually uses. A bare prefix match is too loose: it let
            # NumLinks match .nav's `LinkRefs {` container and reported 31
            # sound files as broken.
            cands = [v for k, v in heads.items()
                     if k.startswith(noun) and k[len(noun):] in RECORD_SUFFIXES]
            typed = cands[0] if len(cands) == 1 else None
        rules = []
        if typed is not None and typed == want:
            rules.append("typed")
        if total_blocks == want:
            rules.append("total")
        if rules:
            out.append(("ok", l.tokens[0], want, "+".join(rules)))
        elif typed is None:
            # No block type carries this noun, so there is nothing for the
            # number to be counting. .crk's NumSettleFrames and .sm's
            # NumRoundsToWin are gameplay values that merely start with "Num".
            # Calling these mismatches would invent a convention the game does
            # not follow -- they are fields, and they are reported as such.
            out.append(("field", l.tokens[0], want, "no block named %r" % noun))
        else:
            out.append(("mismatch", l.tokens[0], want,
                        "typed=%d total=%d" % (typed, total_blocks)))
    return out


# ----------------------------------------------------------------- the driver

def inflate(backing, e):
    raw = backing.read_at(e.offset, e.size_comp)
    if not e.compressed:
        return raw
    return zlib.decompress(raw, -15)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso", default=VANILLA)
    ap.add_argument("--ext", default="", help="comma list; default = every TEXT ext")
    ap.add_argument("--json", default=os.path.join(REPO, "07_Research", "textgrammar.json"))
    args = ap.parse_args()

    tf = json.load(open(TEXTFORM))
    if args.ext:
        want = {e.strip().lower().lstrip(".") for e in args.ext.split(",")}
    else:
        want = {e for e, s in tf.items() if s["verdict"] == "TEXT"}

    iso = Iso9660(args.iso)
    stat = collections.defaultdict(lambda: {
        "files": 0, "bytes": 0, "rt_ok": 0, "rt_fail": [],
        "brace_ok": 0, "brace_fail": [], "count_ok": 0, "count_bad": 0,
        "count_mismatch": [], "count_unprovable": 0, "no_counts": 0,
        "rules": collections.Counter(),
        "max_depth": 0,
    })

    n_arch = 0
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b"Dave", b"DAVE"):
            continue
        n_arch += 1
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        sys.stderr.write("[%2d] %-28s\n" % (n_arch, f.path))
        for e in d.entries:
            ext = e.ext or "(none)"
            if ext not in want:
                continue
            try:
                blob = inflate(d.backing, e)
            except zlib.error:
                continue
            if not blob:
                continue
            if any(c not in TEXT_OK for c in blob):
                continue                       # binary straggler, not our claim
            text = blob.decode("ascii")
            s = stat[ext]
            s["files"] += 1
            s["bytes"] += len(blob)

            lines = split_lines(text)
            if emit(lines) == text:
                s["rt_ok"] += 1
            elif len(s["rt_fail"]) < 5:
                s["rt_fail"].append({"archive": f.path, "name": e.name})

            bad = brace_check(lines)
            if bad is None:
                s["brace_ok"] += 1
            elif len(s["brace_fail"]) < 5:
                s["brace_fail"].append({"archive": f.path, "name": e.name, "why": bad})

            depth = mx = 0
            for l in lines:
                depth += l.opens - l.closes
                mx = max(mx, depth)
            s["max_depth"] = max(s["max_depth"], mx)

            probs = count_check(lines)
            if not probs:
                s["no_counts"] += 1
            s["count_ok"] += sum(1 for p in probs if p[0] == "ok")
            for p in probs:
                if p[0] == "ok":
                    s["rules"][p[3]] += 1
            s["count_unprovable"] += sum(1 for p in probs if p[0] == "unprovable")
            s["count_field"] = s.get("count_field", 0) + sum(1 for p in probs if p[0] == "field")
            mis = [p for p in probs if p[0] == "mismatch"]
            s["count_bad"] = s.get("count_bad", 0) + len(mis)
            if mis and len(s["count_mismatch"]) < 5:
                s["count_mismatch"].append(
                    {"archive": f.path, "name": e.name,
                     "detail": [{"header": p[1], "declared": p[2], "found": p[3]}
                                for p in mis[:3]]})

    out = {}
    for k, v in stat.items():
        v = dict(v)
        v["rules"] = dict(v["rules"])
        bad = v.get("count_bad", 0)
        ok = v["count_ok"]
        v["count_convention"] = (
            "NONE" if not ok and not bad else
            "RELIABLE" if ok and not bad else
            "MOSTLY(%d bad)" % bad if ok and bad <= 0.02 * (ok + bad) else
            "UNRELIABLE")
        out[k] = v
    with open(args.json, "w") as fh:
        json.dump(out, fh, indent=1)

    print("%-16s %7s %10s  %8s %8s %9s" % ("EXT", "FILES", "BYTES", "ROUNDTRIP", "BRACES", "COUNTHDRS"))
    tf_files = tr = tb = 0
    for ext, s in sorted(out.items(), key=lambda kv: -kv[1]["files"]):
        tf_files += s["files"]; tr += s["rt_ok"]; tb += s["brace_ok"]
        print("%-16s %7d %10d  %8s %8s %9s" % (
            ext, s["files"], s["bytes"],
            "%d/%d" % (s["rt_ok"], s["files"]),
            "%d/%d" % (s["brace_ok"], s["files"]),
            "%d ok %d bad %s" % (s["count_ok"], s.get("count_bad", 0),
                                 s["count_convention"])))
    tco = sum(s["count_ok"] for s in out.values())
    tcb = sum(s.get("count_bad", 0) for s in out.values())
    tcu = sum(s["count_unprovable"] for s in out.values())
    print("\nTOTAL %d files  round-trip %d/%d  braces balanced %d/%d"
          % (tf_files, tr, tf_files, tb, tf_files))
    tcf = sum(s.get("count_field", 0) for s in out.values())
    print("Num* headers: %d honoured as record counts, %d mismatched, "
          "%d plain integer fields (no block of that name) -> %s"
          % (tco, tcb, tcf, args.json))
    iso.close()


if __name__ == "__main__":
    main()
