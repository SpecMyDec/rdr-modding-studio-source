"""probe_textedit.py -- can we change one field and leave every other byte alone?

probe_textgrammar.py proves the text formats reproduce byte-for-byte when
parsed and re-emitted untouched. That on its own is close to a tautology. The
claim worth proving is the one the roadmap actually asks for:

    "edits a field, writes, re-reads it byte-exactly, and asserts every
     unedited byte is untouched"   -- ROADMAP-TESTER.md, M7

So for every ASCII entry on the disc this probe:

  1. finds a numeric scalar field (a line whose last token parses as a number),
  2. rewrites that ONE token in place, preserving the line's exact whitespace,
  3. re-emits and diffs against the original,
  4. asserts the diff is exactly that one span and nothing else,
  5. re-parses the output and asserts the field reads back as the new value.

A format passes only if every file of that type passes all five.

    python3 03_Tools/research/probe_textedit.py [--iso PATH] [--ext swt,cr]
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
from rdrstudio import dave                     # noqa: E402
from probe_textgrammar import split_lines, emit, TEXT_OK   # noqa: E402

REPO = os.path.dirname(os.path.dirname(HERE))
VANILLA = os.path.join(REPO, "01_Vanilla", "RDR_VANILLA.iso")
TEXTFORM = os.path.join(REPO, "07_Research", "textform.json")


def is_number(tok):
    try:
        float(tok.rstrip("f"))
        return True
    except ValueError:
        return False


def find_edit_target(lines):
    """First line that looks like `Key <number>` and is not a block head."""
    for i, l in enumerate(lines):
        if l.opens or l.closes or len(l.tokens) < 2:
            continue
        if is_number(l.tokens[-1]) and not is_number(l.tokens[0]):
            return i
    return None


def retoken(raw, old, new):
    """Replace the LAST occurrence of `old` in `raw`, keeping all whitespace."""
    at = raw.rfind(old)
    if at < 0:
        return None
    return raw[:at] + new + raw[at + len(old):]


def bump(tok):
    """A different value of the same textual shape."""
    body = tok[:-1] if tok.endswith("f") else tok
    suf = "f" if tok.endswith("f") else ""
    if "." in body:
        dec = len(body.split(".", 1)[1])
        val = float(body) + 1.0
        return ("%.*f" % (dec, val)) + suf
    return str(int(body) + 1) + suf


def inflate(backing, e):
    raw = backing.read_at(e.offset, e.size_comp)
    return raw if not e.compressed else zlib.decompress(raw, -15)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso", default=VANILLA)
    ap.add_argument("--ext", default="")
    ap.add_argument("--json", default=os.path.join(REPO, "07_Research", "textedit.json"))
    args = ap.parse_args()

    tf = json.load(open(TEXTFORM))
    if args.ext:
        want = {e.strip().lower().lstrip(".") for e in args.ext.split(",")}
    else:
        want = {e for e, s in tf.items() if s["verdict"] == "TEXT"}

    iso = Iso9660(args.iso)
    stat = collections.defaultdict(lambda: {
        "files": 0, "edited": 0, "no_target": 0,
        "clean_diff": 0, "readback_ok": 0, "fail": [],
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
            text = blob.decode("ascii")
            s = stat[ext]
            s["files"] += 1

            lines = split_lines(text)
            i = find_edit_target(lines)
            if i is None:
                s["no_target"] += 1
                continue
            old_tok = lines[i].tokens[-1]
            new_tok = bump(old_tok)
            new_raw = retoken(lines[i].raw, old_tok, new_tok)
            if new_raw is None:
                s["no_target"] += 1
                continue
            s["edited"] += 1

            before = lines[i].raw
            lines[i].raw = new_raw
            out = emit(lines)
            lines[i].raw = before

            # (4) the diff must be exactly the one token
            pre = len(os.path.commonprefix([text, out]))
            suf = len(os.path.commonprefix([text[::-1], out[::-1]]))
            changed_in = text[pre:len(text) - suf]
            changed_out = out[pre:len(out) - suf]
            clean = (changed_in in old_tok and changed_out in new_tok
                     and len(text) - suf >= pre)
            if clean:
                s["clean_diff"] += 1
            elif len(s["fail"]) < 5:
                s["fail"].append({"archive": f.path, "name": e.name, "why": "diff",
                                  "in": changed_in[:40], "out": changed_out[:40]})
                continue

            # (5) re-parse and confirm the new value is what reads back
            back = split_lines(out)
            if back[i].tokens[-1] == new_tok and len(back) == len(lines):
                s["readback_ok"] += 1
            elif len(s["fail"]) < 5:
                s["fail"].append({"archive": f.path, "name": e.name,
                                  "why": "readback", "got": back[i].tokens[-1]})

    out = {}
    for ext, s in stat.items():
        s = dict(s)
        s["verdict"] = ("PASS" if s["edited"] and s["clean_diff"] == s["edited"]
                        and s["readback_ok"] == s["edited"] else
                        "NO-TARGET" if not s["edited"] else "FAIL")
        out[ext] = s
    with open(args.json, "w") as fh:
        json.dump(out, fh, indent=1)

    print("%-16s %7s %8s %10s %10s  %s" % (
        "EXT", "FILES", "EDITED", "CLEANDIFF", "READBACK", "VERDICT"))
    te = tc = tr = 0
    for ext, s in sorted(out.items(), key=lambda kv: -kv[1]["files"]):
        te += s["edited"]; tc += s["clean_diff"]; tr += s["readback_ok"]
        print("%-16s %7d %8d %10d %10d  %s" % (
            ext, s["files"], s["edited"], s["clean_diff"], s["readback_ok"], s["verdict"]))
    print("\nedited %d files; %d had a byte-clean one-token diff; %d read back the new value"
          % (te, tc, tr))
    print("-> %s" % args.json)
    iso.close()


if __name__ == "__main__":
    main()
