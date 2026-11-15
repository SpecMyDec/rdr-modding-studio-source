"""probe_textform.py -- is this format text or binary, across EVERY file of it?

Walks every entry of every Dave archive on the disc, inflates a prefix, and
classifies it. The point is to separate formats that are ASCII (editable as
text, trivially moddable) from true binary, and to do it over the whole corpus
rather than one lucky sample -- an extension only earns a TEXT verdict here if
*every* file with that extension classifies as text.

Grammar families detected among the text formats:
  kv      "key: value" / "key value" lines
  brace   "name {" ... "}" blocks
  counted "NumThing 12" header followed by that many records
  csv     comma-separated with a header row

    python3 03_Tools/research/probe_textform.py [--iso PATH] [--prefix 4096]
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

TEXT_OK = set(range(0x20, 0x7F)) | {0x09, 0x0A, 0x0D}
RE_COUNTED = re.compile(r"^\s*(Num[A-Za-z0-9_]*|columns)\s+\d+", re.M)
RE_BRACE = re.compile(r"^\s*[A-Za-z_][\w.]*\s*\{\s*$", re.M)
RE_KV = re.compile(r"^\s*[A-Za-z_][\w.\-]*\s*[:=]\s*\S", re.M)


def inflate_prefix(backing, e, n):
    if not e.compressed:
        return backing.read_at(e.offset, min(n, e.size_comp))
    raw = backing.read_at(e.offset, min(e.size_comp, n + 1024))
    try:
        return zlib.decompressobj(-15).decompress(raw, n)
    except zlib.error:
        return None


def classify(buf):
    if not buf:
        return "empty", {}
    bad = sum(1 for c in buf if c not in TEXT_OK)
    ratio = 1.0 - bad / len(buf)
    if ratio < 0.95:
        return "binary", {"printable": ratio}
    txt = buf.decode("ascii", "replace")
    fams = []
    if RE_COUNTED.search(txt):
        fams.append("counted")
    if RE_BRACE.search(txt):
        fams.append("brace")
    if RE_KV.search(txt):
        fams.append("kv")
    if "," in txt and txt.count(",") > txt.count("\n"):
        fams.append("csv")
    return "text", {"printable": ratio, "families": fams}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso", default=VANILLA)
    ap.add_argument("--prefix", type=int, default=4096)
    ap.add_argument("--json", default=os.path.join(REPO, "07_Research", "textform.json"))
    args = ap.parse_args()

    iso = Iso9660(args.iso)
    stat = collections.defaultdict(lambda: {
        "total": 0, "text": 0, "binary": 0, "empty": 0, "unreadable": 0,
        "families": collections.Counter(), "sample": None, "binary_sample": None,
    })

    n_arch = 0
    for f in iso.files():
        if f.size < 16:
            continue
        if iso.read_at(f.offset, 4) not in (b"Dave", b"DAVE"):
            continue
        n_arch += 1
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        sys.stderr.write("[%2d/66] %-28s %5d entries\n" % (n_arch, f.path, d.count))
        for e in d.entries:
            ext = e.ext or "(none)"
            s = stat[ext]
            s["total"] += 1
            buf = inflate_prefix(d.backing, e, args.prefix)
            if buf is None:
                s["unreadable"] += 1
                continue
            kind, info = classify(buf)
            s[kind] += 1
            if kind == "text":
                for fam in info.get("families", ()):
                    s["families"][fam] += 1
                if s["sample"] is None:
                    s["sample"] = {"name": e.name, "archive": f.path,
                                   "head": buf[:240].decode("ascii", "replace")}
            elif kind == "binary" and s["binary_sample"] is None:
                s["binary_sample"] = {"name": e.name, "archive": f.path,
                                      "head": buf[:32].hex()}

    out = {}
    for ext, s in stat.items():
        readable = s["total"] - s["unreadable"]
        out[ext] = {
            "total": s["total"], "text": s["text"], "binary": s["binary"],
            "empty": s["empty"], "unreadable": s["unreadable"],
            # Zero-length entries are real on this disc (arfx has 65 of them).
            # They are neither text nor binary evidence, so they must not drag
            # an otherwise-pure extension into MIXED.
            "verdict": ("TEXT" if s["text"] and not s["binary"] else
                        "BINARY" if s["binary"] and not s["text"] else
                        "MIXED" if s["text"] and s["binary"] else
                        "EMPTY" if s["empty"] else "UNREADABLE"),
            "families": dict(s["families"]),
            "sample": s["sample"], "binary_sample": s["binary_sample"],
        }
    with open(args.json, "w") as fh:
        json.dump(out, fh, indent=1)

    rows = sorted(out.items(), key=lambda kv: (kv[1]["verdict"], -kv[1]["total"]))
    print("%-16s %8s %-10s %6s %6s  %s" % ("EXT", "TOTAL", "VERDICT", "TEXT", "BIN", "FAMILIES"))
    for ext, s in rows:
        print("%-16s %8d %-10s %6d %6d  %s" % (
            ext, s["total"], s["verdict"], s["text"], s["binary"],
            ",".join(sorted(s["families"])) or "-"))
    tot = sum(s["total"] for s in out.values())
    txt = sum(s["total"] for s in out.values() if s["verdict"] == "TEXT")
    print("\n%d entries across %d archives; %d (%.1f%%) are in all-TEXT extensions -> %s"
          % (tot, n_arch, txt, 100.0 * txt / tot, args.json))
    iso.close()


if __name__ == "__main__":
    main()
