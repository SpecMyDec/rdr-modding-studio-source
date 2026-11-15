"""probe_actb.py -- naming the .actb/.stateb opcodes without disassembling the ELF.

`rdrstudio/actb.py` reads the STRUCTURE of these 20,495 files and says, in
its own docstring, that it does not know what the opcodes MEAN -- that values
like 1019 or 1193 are indices into behaviour compiled inside SLUS_205.00, and
that naming them requires disassembling that ELF. (Paraphrased deliberately:
the exact sentence is a retired claim under CAPABILITIES C-036, and the [M3]
test fails on any file that still ships it.)

This probe tests the cheaper route first. The files carry inline strings, and
every string is preceded by a 32-bit word. If a given word is followed by the
same KIND of string in all 20,495 files -- always a state name, always a
quoted command, always a character -- then the word's meaning is recoverable
from the corpus alone, no disassembly required.

That is the technique that decoded `.pup`'s role codes. What makes it evidence
rather than pattern-matching is scoring each opcode against namespaces built
from **independent** files -- never from the .stateb corpus itself, which
would be circular:

    states   the `*State:` values in the 3,275 TEXT `.tbl` files
    flags    the keys in the 203 TEXT `.flags` files
    shows    the names of the 1,200 `.pup` cutscenes
    assets   the base names of every entry on the disc (animations, faces,
             levels -- whatever the opcode is naming)

and one INTERNAL namespace, which is weaker evidence and is labelled as such
wherever it is used:

    declared  bare (unquoted) identifiers in the .actb corpus itself

`declared` is not independent -- it comes from the same files. It is still
worth scoring, because a quoted argument matching a BARE identifier is a
reference matching a declaration, which is a different position in the format
rather than the same fact restated. Opcode 1005 is the control: it is settled
by `assets` at 99.8%, so whatever `declared` scores for 1005 is the noise
floor for this test.

An opcode whose argument lands in one of those sets, in file after file, is
naming that kind of thing. The first cut of this probe scored against every
bare identifier found in the .actb corpus and got 100% on opcodes that name
variables -- a set that contains the answer cannot test it.

Token model, chosen because it needs no chicken-and-egg opcode table: find the
maximal runs of printable ASCII ending in NUL, and read the 4 bytes
immediately before each run as a little-endian u32. Both halves are defined
without knowing what anything means.

    python3 03_Tools/research/probe_actb.py [--iso PATH] [--min 25]
"""

import argparse
import collections
import json
import os
import re
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from rdrstudio.iso9660 import Iso9660          # noqa: E402
from rdrstudio import dave                     # noqa: E402

REPO = os.path.dirname(os.path.dirname(HERE))
VANILLA = os.path.join(REPO, "01_Vanilla", "RDR_VANILLA.iso")

MAGIC = b"version\x00"
# A string token: two or more printable bytes then a NUL. The leading
# boundary is enforced by taking maximal runs, so a name is never split.
STR = re.compile(rb"[\x20-\x7e]{2,}\x00")


def inflate(backing, e):
    raw = backing.read_at(e.offset, e.size_comp)
    return raw if not e.compressed else zlib.decompress(raw, -15)


def strings_with_prefix(data):
    """(offset, text, preceding u32 or None) for every inline string."""
    out = []
    for m in STR.finditer(data):
        start = m.start()
        if start >= 4:
            prev, = struct.unpack_from("<I", data, start - 4)
        else:
            prev = None
        out.append((start, m.group()[:-1].decode("ascii"), prev))
    return out


def top_level_names(data):
    """The `count` names a table declares, read straight after the header.

    The first string after the 16-byte header is the first entry's name; the
    rest are found by the same walk. Only used to build the corpus-wide set of
    state names, so being conservative here costs nothing.
    """
    if data[:8] != MAGIC or len(data) < 16:
        return 0, []
    count, = struct.unpack_from("<I", data, 12)
    ss = strings_with_prefix(data[16:])
    return count, [t for _o, t, _p in ss]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso", default=VANILLA)
    ap.add_argument("--min", type=int, default=25,
                    help="only report opcodes seen at least this often")
    ap.add_argument("--json", default=os.path.join(REPO, "07_Research", "actb.json"))
    args = ap.parse_args()

    iso = Iso9660(args.iso)

    files = []
    n_files = 0
    bad_magic = 0
    declared_ok = 0
    ns = {"states": set(), "flags": set(), "shows": set(), "assets": set(),
          "declared": set()}
    by_ext = {}          # asset stem -> the extensions it exists with

    RE_STATE = re.compile(r"^\s*\w*State\s*:\s*(\S+)", re.M)

    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b"Dave", b"DAVE"):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        sys.stderr.write("%-30s\n" % f.path)
        for e in d.entries:
            base = e.name.rsplit("/", 1)[-1]
            stem = base.rsplit(".", 1)[0].lower()
            ns["assets"].add(stem)
            by_ext.setdefault(stem, set()).add(e.ext)
            if e.ext == "pup":
                ns["shows"].add(stem)

            if e.ext in ("tbl", "flags"):
                try:
                    txt = inflate(d.backing, e).decode("ascii", "replace")
                except zlib.error:
                    continue
                if e.ext == "tbl":
                    for m in RE_STATE.finditer(txt):
                        ns["states"].add(m.group(1).strip().lower())
                else:
                    for line in txt.splitlines():
                        tok = line.split()
                        if not tok:
                            continue
                        key = tok[1] if tok[0] == "String" and len(tok) > 1 else tok[0]
                        ns["flags"].add(key.lower())
                continue

            if e.ext not in ("actb", "stateb", "acttbl"):
                continue
            try:
                blob = inflate(d.backing, e)
            except zlib.error:
                continue
            n_files += 1
            if blob[:8] != MAGIC:
                bad_magic += 1
                continue
            declared_ok += 1
            files.append((f.path, e.name, e.ext, blob))
            for _o, t, _p in strings_with_prefix(blob):
                if not t.startswith('"') and re.fullmatch(r"[A-Za-z_]\w*", t):
                    ns["declared"].add(t.lower())
    iso.close()

    # pass two: what follows each opcode, corpus-wide
    prof = collections.defaultdict(lambda: {
        "n": 0, "quoted": 0, "distinct": collections.Counter(),
        "hit": collections.Counter(), "ext": collections.Counter(),
    })
    for _arch, _nm, _ext, blob in files:
        for _o, text, prev in strings_with_prefix(blob):
            if prev is None:
                continue
            p = prof[prev]
            p["n"] += 1
            body = text[1:-1] if text.startswith('"') and text.endswith('"') else text
            if text.startswith('"'):
                p["quoted"] += 1
            p["distinct"][body] += 1
            low = body.lower()
            for k, names in ns.items():
                if low in names:
                    p["hit"][k] += 1
            for x in by_ext.get(low, ()):
                p["ext"][x] += 1

    rows = []
    for op, p in prof.items():
        if p["n"] < args.min:
            continue
        best = p["hit"].most_common(1)
        rows.append({
            "opcode": op, "n": p["n"],
            "quoted_pct": round(100.0 * p["quoted"] / p["n"], 1),
            "distinct": len(p["distinct"]),
            "namespace": best[0][0] if best else "-",
            "namespace_pct": round(100.0 * best[0][1] / p["n"], 1) if best else 0.0,
            "all_namespaces": {k: round(100.0 * v / p["n"], 1)
                               for k, v in p["hit"].items()},
            "target_exts": [("." + x, round(100.0 * c / p["n"], 1))
                            for x, c in p["ext"].most_common(4)],
            "examples": [k for k, _ in p["distinct"].most_common(6)],
        })
    rows.sort(key=lambda r: -r["n"])

    report = {
        "files": n_files, "with_magic": declared_ok, "bad_magic": bad_magic,
        "namespace_sizes": {k: len(v) for k, v in ns.items()},
        "opcodes": rows,
    }
    with open(args.json, "w") as fh:
        json.dump(report, fh, indent=1)

    print("%d .actb/.stateb/.acttbl files; %d carry the 'version' magic, %d do not"
          % (n_files, declared_ok, bad_magic))
    print("namespaces built from independent files: %s\n"
          % ", ".join("%s=%d" % (k, len(v)) for k, v in ns.items()))
    print("%-8s %8s %8s %9s %-9s %7s  %s" % (
        "OPCODE", "USES", "QUOTED%", "DISTINCT", "NAMESPACE", "MATCH%", "EXAMPLES"))
    for r in rows:
        print("%-8d %8d %7.1f%% %9d %-9s %6.1f%%  %s" % (
            r["opcode"], r["n"], r["quoted_pct"], r["distinct"],
            r["namespace"], r["namespace_pct"],
            ", ".join(r["examples"][:4])[:64]))
    print("\n-> %s" % args.json)


if __name__ == "__main__":
    main()
