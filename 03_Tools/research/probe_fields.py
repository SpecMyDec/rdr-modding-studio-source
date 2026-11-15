"""probe_fields.py -- what every key in the text formats actually holds.

`probe_textgrammar.py` settled the STRUCTURE of the 109 ASCII formats. This
settles the next question: for each key, what kind of value does it carry, over
what range, in how many files -- measured across the whole corpus rather than
guessed from one example.

It deliberately does NOT invent meanings. "TurnRate is how fast the character
turns" is a guess; "TurnRate is a float, present in 517 of 517 `.cr`, measured
0.5 .. 12.0" is a measurement, and it is what lets someone edit the file with
their eyes open. Any English gloss belongs in the write-up, marked as
inference, not in this output.

WHAT IT MEASURES, per key

    kind      int | float | bool | vec2/3/4 | enum | ref | text | empty
    names     how many DISTINCT file names of that type carry the key. The
              same asset is duplicated across level archives -- there are 517
              `.cr` entries on the disc but only 181 distinct characters -- so
              counting entries would triple every number for no extra meaning
    range     min..max for numerics, the value set for enums
    ref       for string values: the share that match the base name of a real
              file on the disc, and which extensions those files have -- so a
              key like `FileNameRender` is reported as naming an asset rather
              than as opaque text

TWO NORMALISATIONS, both needed or the key list is nonsense

  * A block head is `Type instance {` -- `Node 0 {`, `BoundSphere fist_l {`,
    `HotSpot hot19 {`. Only the TYPE belongs in a key path. Blocks named by a
    bare instance label (`VCam0001 {`) are collapsed to `*`: a name is treated
    as structural only if it appears in at least 20% of that extension's files.
    Without this, `.cam` alone reports 27,125 distinct keys, one per camera.
  * Consecutive identical segments collapse -- `.skel` nests `bone` inside
    `bone` to depth 21, and `bone/bone/bone/offset` is the same field as
    `bone/offset`.
  * A bare word on its own line followed by `{` on the NEXT line is a block
    NAME, not a key. `.sphs`, `.aisp` and `.swptxemitter` all write this
    Allman style. Missing it produced 432 phantom "keys" in `.swptxemitter`
    alone -- one per emitter, each with no value -- and pushed the real
    fields down under an anonymous `*` parent.

Lines whose tokens are all numeric are DATA ROWS, not keys, and are counted
separately. Reading them as keys is what inflated the first count of this probe
to 129,863.

NOT EVERY FORMAT HAS KEYS. `.lvl` is a manifest -- named blocks each listing
asset filenames, no `key value` line anywhere -- and `.accu` is a plain CSV
table. Both would vanish from a catalogue that only counts keys, so they are
kept with a shape of LIST/TABLE and a sample row.

SCHEMA vs DATA. Some formats have a fixed key set (`.swt` asks the same 548
questions of every character); in others the keys ARE the content (`.flags` is
1,724 game-state flags, `.acttbl` is a list of action names). The two want
different UI, so each extension is classified by how much its key set varies
per file.

REQ-017. Key PATHS here come from the token-based grammar, which is correct.
`rdrstudio/props.py` mis-nests 18 extensions today, so for those the paths a
tool currently produces differ from the ones below. Each affected extension is
flagged in the output.

    python3 03_Tools/research/probe_fields.py [--ext swt,cr] [--md OUT.md]
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
sys.path.insert(0, HERE)
from rdrstudio.iso9660 import Iso9660          # noqa: E402
from rdrstudio import dave                     # noqa: E402
from probe_textgrammar import split_lines, TEXT_OK   # noqa: E402

REPO = os.path.dirname(os.path.dirname(HERE))
VANILLA = os.path.join(REPO, "01_Vanilla", "RDR_VANILLA.iso")
RES = os.path.join(REPO, "07_Research")

# A key token, with the separator optionally glued to it. All three
# separators are in use and sometimes in the same directory: `.fog` writes
# `ClampAlpha 150`, `.pmtl` writes `friction: 0.100000`, `.shadows` writes
# `Color= 0.0 0.0 0.0`.
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-\[\]/]*[:=]?$")
SPLIT_EQ = re.compile(r"^([A-Za-z_][A-Za-z0-9_.\-\[\]/]*)=(.*)$")
# A value must be this long before it is allowed to "name a file". Without it
# `type: a` matches `a.tex` and every `type` key on the disc is reported as an
# asset reference.
MIN_REF_LEN = 3
NUM = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?f?$")
INT = re.compile(r"^[-+]?\d+$")
STRUCTURAL_SHARE = 0.20


def inflate(backing, e):
    raw = backing.read_at(e.offset, e.size_comp)
    return raw if not e.compressed else zlib.decompress(raw, -15)


def text_entries(iso, want):
    """(ext, basename, text) for every clean-ASCII entry of a wanted type."""
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b"Dave", b"DAVE"):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            if e.ext not in want:
                continue
            try:
                blob = inflate(d.backing, e)
            except zlib.error:
                continue
            if not blob or any(c not in TEXT_OK for c in blob):
                continue
            yield e.ext, e.name.rsplit("/", 1)[-1], blob.decode("ascii")


def block_name(line):
    t = line.tokens
    return t[0] if t and IDENT.match(t[0]) else None


def norm_path(stack, key, structural):
    out = []
    for seg in stack:
        seg = seg if seg in structural else "*"
        if not out or out[-1] != seg:
            out.append(seg)
    out.append(key)
    return "/".join(out)


def classify(values):
    """One kind for a key, from every value it ever takes."""
    if not values:
        return "empty", {}
    toks = [v.split() for v in values]
    widths = {len(t) for t in toks}
    flat = [x for t in toks for x in t]
    numeric = all(NUM.match(x) for x in flat) if flat else False

    if numeric:
        nums = [float(x.rstrip("f")) for x in flat]
        info = {"min": min(nums), "max": max(nums)}
        if len(widths) == 1:
            w = widths.pop()
            if w > 1:
                return "vec%d" % w, info
        allint = all(INT.match(x) for x in flat)
        if allint:
            uniq = set(nums)
            if uniq <= {0.0, 1.0}:
                return "bool", info
            return "int", info
        return "float", info

    uniq = collections.Counter(v.strip() for v in values)
    if len(uniq) <= 12:
        return "enum", {"values": [v for v, _ in uniq.most_common(12)]}
    return "text", {"distinct": len(uniq),
                    "examples": [v for v, _ in uniq.most_common(3)]}



def fmt_range(f):
    if f["kind"] == "ref":
        return "-> %s (%.0f%%)" % (", ".join(f.get("ref_to", [])), f.get("ref_match_pct", 0))
    if "min" in f:
        lo, hi = f["min"], f["max"]
        if lo == hi:
            return "always %g" % lo
        return "%g .. %g" % (lo, hi)
    if "values" in f:
        return ", ".join(str(v) for v in f["values"][:6])
    if "examples" in f:
        return "%d distinct, e.g. %s" % (f.get("distinct", 0),
                                         ", ".join(f["examples"][:2]))
    return ""


def write_md(out, path, detail, top):
    with open(path, "w") as fh:
        fh.write("# FIELDS — every key in the disc's text formats, measured\n\n")
        fh.write("Owner: **rdr-re**. Generated by `03_Tools/research/probe_fields.py`;\n"
                 "do not hand-edit. Regenerate with\n\n"
                 "    python3 03_Tools/research/probe_fields.py --md 05_Docs/RESEARCH/FIELDS.md\n\n")
        tot = sum(s["distinct_keys"] for s in out.values())
        kinds = collections.Counter(f["kind"] for s in out.values() for f in s["fields"])
        nref = kinds.get("ref", 0)
        bad = sorted(e for e, s in out.items() if s["props_misnests_paths"])
        fh.write("**%d extensions, %d distinct keys.** Kinds: %s.\n\n"
                 % (len(out), tot,
                    ", ".join("%s %d" % kv for kv in kinds.most_common())))
        fh.write("`names` is DISTINCT FILE NAMES, not entries: the same asset is\n"
                 "duplicated across level archives, so there are 517 `.cr` entries on\n"
                 "the disc but only 181 distinct characters.\n\n")
        fh.write("## What is measured and what is not\n\n"
                 "Kind, range and file counts are **measured over every file of that\n"
                 "type on the disc**. No English gloss is attached to any key here:\n"
                 '"`TurnRate` is how fast the character turns" is a guess, while\n'
                 '"`TurnRate` is a float present in all 181 characters, measured\n'
                 "0.5 .. 12.0\" is a fact, and it is the fact that lets someone edit\n"
                 "the file with their eyes open. Inference belongs in\n"
                 "`UNKNOWNS.md`, labelled as inference.\n\n"
                 "**`ref` is the exception, and it is still mechanical.** A string\n"
                 "key is marked `ref` when at least half its values match the base\n"
                 "name of a real file on the disc; the arrow shows which extensions\n"
                 "those files have. That turns `%d` opaque text keys into \"this one\n"
                 "names an asset of type X\" without anyone guessing.\n\n" % nref)
        fh.write("## REQ-017 — %d extensions whose key PATHS are not what the tool "
                 "produces today\n\n" % len(bad))
        fh.write("Paths below come from the token-based grammar, which is correct.\n"
                 "`rdrstudio/props.py` mis-nests these extensions (CAPABILITIES\n"
                 "C-033), so until REQ-017 lands the tool's own paths for them differ\n"
                 "-- fields land in the wrong parent, and a block head with an\n"
                 "argument is offered as an editable field. **Every other extension\n"
                 "in this file is unaffected.**\n\n")
        aff = sum(out[e]["keys_affected_by_req017"] for e in bad)
        tot_bad = sum(out[e]["distinct_keys"] for e in bad)
        fh.write("| ext | keys | of those, nested (paths differ) |\n|---|---:|---:|\n")
        for e in sorted(bad, key=lambda x: -out[x]["keys_affected_by_req017"]):
            fh.write("| `.%s` | %d | **%d** |\n"
                     % (e, out[e]["distinct_keys"],
                        out[e]["keys_affected_by_req017"]))
        fh.write("\n**%d of the %d keys in these 17 formats sit inside a block and so\n"
                 "carry a path the tool does not currently produce.** The other %d are\n"
                 "at top level and are addressed correctly today. Across the whole\n"
                 "catalogue that is %d of %d keys (%.1f%%) waiting on REQ-017; the\n"
                 "remaining %d are usable now.\n\n"
                 % (aff, tot_bad, tot_bad - aff, aff, tot,
                    100.0 * aff / tot, tot - aff))
        fh.write("## Shape of each format\n\n")
        fh.write("| ext | entries | names | keys | keys/file | shape | REQ-017 |\n")
        fh.write("|---|---:|---:|---:|---:|---|---|\n")
        for ext, s in sorted(out.items(), key=lambda kv: -kv[1]["entries"]):
            fh.write("| `.%s` | %d | %d | %d | %.1f | %s | %s |\n"
                     % (ext, s["entries"], s["names"], s["distinct_keys"],
                        s["mean_keys_per_file"], s["shape"],
                        "**paths differ**" if s["props_misnests_paths"] else ""))
        fh.write("\n`SCHEMA` means every file asks the same questions -- a fixed\n"
                 "field set a panel can lay out once. `DATA (keys are content)` means\n"
                 "the keys ARE the payload: `.flags` is 1,724 game-state flags,\n"
                 "`.acttbl` is a list of action names. The two want different UI.\n")
        fh.write("\n## The formats, key by key\n\n")
        fh.write("The %d formats with the most files. Full data for all %d is in\n"
                 "`07_Research/fields.json` (regenerable, so not tracked).\n"
                 % (detail, len(out)))
        for ext, s in sorted(out.items(), key=lambda kv: -kv[1]["entries"])[:detail]:
            fh.write("\n### `.%s` — %d entries, %d distinct names, %d keys%s\n\n"
                     % (ext, s["entries"], s["names"], s["distinct_keys"],
                        "  **(REQ-017: paths differ today)**"
                        if s["props_misnests_paths"] else ""))
            if not s["fields"]:
                fh.write("No `key value` lines at all -- this format is a list or a\n"
                         "table. Sample rows:\n\n")
                for r in s.get("row_sample", []):
                    fh.write("    %s\n" % r)
                fh.write("\n")
                continue
            fields = s["fields"][:top]
            if len(s["fields"]) > top:
                fh.write("Top %d of %d by how many files carry them.\n\n"
                         % (top, len(s["fields"])))
            fh.write("| key | kind | names | range / values |\n|---|---|---:|---|\n")
            for f in fields:
                fh.write("| `%s` | %s | %d | %s |\n"
                         % (f["key"], f["kind"], f["names"], fmt_range(f)))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso", default=VANILLA)
    ap.add_argument("--ext", default="")
    ap.add_argument("--md", default="")
    ap.add_argument("--json", default=os.path.join(RES, "fields.json"))
    ap.add_argument("--top", type=int, default=24, help="formats to write up")
    ap.add_argument("--per-format", type=int, default=40,
                    help="keys to list per format in the Markdown")
    args = ap.parse_args()

    tf = json.load(open(os.path.join(RES, "textform.json")))
    want = ({e.strip().lower().lstrip(".") for e in args.ext.split(",")}
            if args.ext else
            {e for e, s in tf.items() if s["verdict"] == "TEXT"})
    try:
        gap = json.load(open(os.path.join(RES, "propsgap.json")))
    except FileNotFoundError:
        gap = {}
    misnested = {e for e, s in gap.items() if s.get("trap_fields")}

    # ---- pass 1: which block names are structural, and the asset name table
    iso = Iso9660(args.iso)
    assets = collections.defaultdict(set)      # stem -> {ext}
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b"Dave", b"DAVE"):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            base = e.name.rsplit("/", 1)[-1]
            assets[base.rsplit(".", 1)[0].lower()].add(e.ext)

    nfiles = collections.Counter()
    seen_block = collections.defaultdict(collections.Counter)
    for ext, _name, text in text_entries(iso, want):
        nfiles[ext] += 1
        here = set()
        pending = None
        for l in split_lines(text):
            if l.is_block_head:
                b = block_name(l) or pending
                if b:
                    here.add(b)
                pending = None
                continue
            t = l.tokens
            pending = (t[0].rstrip(":=") if len(t) == 1 and not l.closes
                       and IDENT.match(t[0]) and not NUM.match(t[0]) else None)
        for b in here:
            seen_block[ext][b] += 1
    structural = {ext: {b for b, c in cnt.items()
                        if c >= STRUCTURAL_SHARE * nfiles[ext]}
                  for ext, cnt in seen_block.items()}

    # ---- pass 2: the catalogue
    keys = collections.defaultdict(lambda: collections.defaultdict(
        lambda: {"files": set(), "n": 0, "values": []}))
    rows = collections.Counter()
    row_sample = collections.defaultdict(list)
    per_file_keys = collections.defaultdict(list)

    for ext, name, text in text_entries(iso, want):
        stack = []
        mine = set()
        pending = None          # a bare word that may name the next `{`
        for l in split_lines(text):
            t = l.tokens
            if l.is_block_head:
                stack.append(block_name(l) or pending or "*")
                pending = None
                continue
            if len(t) == 1 and not l.closes and IDENT.match(t[0]) \
                    and not NUM.match(t[0]):
                pending = t[0].rstrip(":=")
                continue
            pending = None
            if l.closes > l.opens:
                if stack:
                    stack.pop()
                continue
            if not t:
                continue
            if all(NUM.match(x) for x in t) or not IDENT.match(t[0]):
                rows[ext] += 1
                if len(row_sample[ext]) < 3:
                    row_sample[ext].append(" ".join(t)[:70])
                continue
            head, rest = t[0], t[1:]
            m = SPLIT_EQ.match(head)          # `Color=0.0` with no space
            if m:
                head = m.group(1)
                if m.group(2):
                    rest = [m.group(2)] + rest
            key = norm_path(stack, head.rstrip(":="), structural.get(ext, set()))
            rec = keys[ext][key]
            rec["files"].add(name)
            rec["n"] += 1
            if len(rec["values"]) < 4000:
                rec["values"].append(" ".join(rest))
            mine.add(key)
        per_file_keys[ext].append(len(mine))
    iso.close()

    out = {}
    for ext in sorted(want & set(nfiles)):
        kk = keys.get(ext, {})
        nf = nfiles[ext]
        per = per_file_keys[ext]
        mean_per_file = sum(per) / len(per) if per else 0
        shape = ("LIST/TABLE (no key=value lines)" if not kk else
                 "SCHEMA" if len(kk) <= max(4 * mean_per_file, 12)
                 else "DATA (keys are content)")
        fields = []
        for key, rec in kk.items():
            kind, info = classify([v for v in rec["values"] if v != ""])
            entry = {"key": key, "names": len(rec["files"]),
                     "occurrences": rec["n"], "kind": kind}
            entry.update(info)
            if kind in ("text", "enum"):
                vals = [v.strip().lower() for v in rec["values"]
                        if len(v.strip()) >= MIN_REF_LEN]
                hit = [v for v in vals if v in assets]
                if vals and len(hit) / len(vals) >= 0.5:
                    exts = collections.Counter(
                        x for v in hit for x in assets[v])
                    entry["kind"] = "ref"
                    entry["ref_match_pct"] = round(100.0 * len(hit) / len(vals), 1)
                    # Sort by count, then by name. `assets[v]` is a set, so
                    # ties come out of most_common() in arbitrary order and
                    # the generated Markdown differs run to run -- which is
                    # indistinguishable from the disc having changed.
                    entry["ref_to"] = ["." + x for x, _c in
                                       sorted(exts.items(),
                                              key=lambda kv: (-kv[1], kv[0]))[:4]]
            fields.append(entry)
        fields.sort(key=lambda e: (-e["names"], e["key"]))
        # In a mis-nested extension a top-level key is still addressed
        # correctly; it is the ones INSIDE a block that get the wrong parent.
        nested = sum(1 for f in fields if "/" in f["key"])
        out[ext] = {"entries": nf, "names": len(set(
                        n for rec in kk.values() for n in rec["files"])) if kk else 0,
                    "distinct_keys": len(kk),
                    "mean_keys_per_file": round(mean_per_file, 1),
                    "data_rows": rows[ext], "shape": shape,
                    "row_sample": row_sample.get(ext, []),
                    "props_misnests_paths": ext in misnested,
                    "keys_affected_by_req017": nested if ext in misnested else 0,
                    "nested_keys": nested,
                    "fields": fields}

    json.dump(out, open(args.json, "w"), indent=1)

    print("%-14s %7s %6s %7s %7s %-22s %s"
          % ("EXT", "ENTRIES", "NAMES", "KEYS", "/FILE", "SHAPE", "REQ-017?"))
    for ext, s in sorted(out.items(), key=lambda kv: -kv[1]["entries"])[:args.top]:
        print("%-14s %7d %6d %7d %7.1f %-22s %s"
              % (ext, s["entries"], s["names"], s["distinct_keys"],
                 s["mean_keys_per_file"], s["shape"],
                 "paths differ" if s["props_misnests_paths"] else ""))
    tot = sum(s["distinct_keys"] for s in out.values())
    kinds = collections.Counter(f["kind"] for s in out.values() for f in s["fields"])
    print("\n%d extensions, %d distinct keys catalogued" % (len(out), tot))
    print("kinds: %s" % ", ".join("%s=%d" % kv for kv in kinds.most_common()))
    nref = sum(1 for s in out.values() for f in s["fields"] if f["kind"] == "ref")
    print("%d keys name a real file on the disc (kind=ref)" % nref)
    print("%d of %d extensions have paths props.py gets wrong today (REQ-017)"
          % (sum(1 for s in out.values() if s["props_misnests_paths"]), len(out)))
    print("-> %s" % args.json)
    if args.md:
        write_md(out, args.md, args.top, args.per_format)
        print("-> %s" % args.md)


if __name__ == "__main__":
    main()
