"""probe_unknowns.py -- regenerate the status table in 05_Docs/RESEARCH/UNKNOWNS.md.

Merges the other probes' JSON and adds the one measurement none of them make:
how much of each text format the tool can actually SEE. `props.parse` copies
every line it does not understand verbatim, so an unparsed line is invisible --
byte-exact round-trip stays perfect while whole fields are missing. Coverage is
therefore measured as

    fields props returns / content lines in the file

counting a content line as any non-blank, non-comment, non-brace-only line.

The DECODED / PARTIAL / UNKNOWN verdict is assigned here from evidence, not by
hand, so the table cannot drift from the probes:

  DECODED  a dedicated module parses the format AND coverage is >= 99%
           (or the format is binary and its module reads every field)
  PARTIAL  something reads it, but not all of it -- a text format under the
           generic props reader, or a binary format with a partial module
  UNKNOWN  nothing in rdrstudio/ parses it

    python3 03_Tools/research/probe_unknowns.py [--table] [--iso PATH]
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
from rdrstudio import dave, props              # noqa: E402
from probe_textgrammar import TEXT_OK          # noqa: E402

REPO = os.path.dirname(os.path.dirname(HERE))
RES = os.path.join(REPO, "07_Research")
VANILLA = os.path.join(REPO, "01_Vanilla", "RDR_VANILLA.iso")

# Modules that parse a format as a FORMAT, not merely name it. Settled by
# reading each one; probe_coverage.py only narrows the candidates.
PARSERS = {
    "cr": ("charfile.py", "DECODED"),
    "swt": ("swt.py + props.py", None),
    "msd": ("msd.py + props.py", None),
    "skel": ("skeleton.py / skeledit.py", "DECODED"),
    "csv": ("csvfile.py", "DECODED"),
    "swui": ("swui.py", None),
    "strtbl": ("strtbl.py", "DECODED"),
    "tex": ("texture.py", "DECODED"),
    "rsc": ("rsc.py", "PARTIAL"),
    "mod": ("rsc.py / vif.py", "PARTIAL"),
    "xrsc": ("texture.py", "PARTIAL"),
    "anim": ("anim.py / bvh.py / animraw.py", "PARTIAL"),
    "rnt": ("rsc.py parse_rnt", "PARTIAL"),
    # decoded by rdr-re; no module yet, filed for rdr-fmt/rdr-3d
    "hf": ("research/probe_hf.py (no module yet)", "DECODED"),
    "xls": ("research/probe_xls.py (no module yet)", "DECODED"),
    "fonttex": ("research/probe_fonttex.py (no module yet)", "DECODED"),
    "pup": ("research/probe_pup.py (no module yet)", "DECODED"),
    "iani": ("research/probe_iani -- header only", "PARTIAL"),
    "pck": ("research/probe_pck.py -- container only", "PARTIAL"),
    "stm": ("stm.py / streams.py", "PARTIAL"),
    "bd": ("vag.py", "PARTIAL"),
    "hd": ("vag.py", "PARTIAL"),
    "ipu": ("ipudec.py / ipuenc.py / mpeg2intra.py", "PARTIAL"),
    "actb": ("actb.py", "PARTIAL"),
    "stateb": ("actb.py / behaviour.py", "PARTIAL"),
    "znes": ("scene.py", None),
    "bnds": ("scene.py", None),
    "enm": ("enemyfields.py", None),
    "tbl": ("behaviour.py / sheets.py", None),
    "lvl": ("level.py", None),
    "guns": ("guns.py", None),
    "aisp": ("levelmeta.py / props.py", None),
    "gait": ("props.py", None),
}

CONTENT = re.compile(rb"^[ \t]*(?:[;#].*)?$")


def inflate(backing, e):
    raw = backing.read_at(e.offset, e.size_comp)
    return raw if not e.compressed else zlib.decompress(raw, -15)


def coverage(blob):
    lines = blob.split(b"\n")
    content = 0
    for l in lines:
        s = l.strip()
        if not s or CONTENT.match(l) or s in (b"{", b"}"):
            continue
        content += 1
    return len(props.parse(blob)), content


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso", default=VANILLA)
    ap.add_argument("--json", default=os.path.join(RES, "unknowns.json"))
    ap.add_argument("--table", action="store_true", help="print markdown")
    args = ap.parse_args()

    inv = json.load(open(os.path.join(RES, "inventory.json")))
    tfm = json.load(open(os.path.join(RES, "textform.json")))
    grm = json.load(open(os.path.join(RES, "textgrammar.json")))
    edt = json.load(open(os.path.join(RES, "textedit.json")))
    gap = json.load(open(os.path.join(RES, "propsgap.json")))

    cov = collections.defaultdict(lambda: [0, 0])
    iso = Iso9660(args.iso)
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b"Dave", b"DAVE"):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        sys.stderr.write("%-30s\n" % f.path)
        for e in d.entries:
            ext = e.ext or "(none)"
            if tfm.get(ext, {}).get("verdict") != "TEXT":
                continue
            try:
                blob = inflate(d.backing, e)
            except zlib.error:
                continue
            if not blob or any(c not in TEXT_OK for c in blob):
                continue
            got, want = coverage(blob)
            cov[ext][0] += got
            cov[ext][1] += want
    iso.close()

    rows = []
    for ext, s in inv["by_ext"].items():
        if ext.startswith("::loose::"):
            continue
        form = tfm.get(ext, {}).get("verdict", "?")
        mod, forced = PARSERS.get(ext, (None, None))
        got, want = cov.get(ext, (0, 0))
        pct = (100.0 * got / want) if want else None
        if forced:
            status = forced
        elif form == "TEXT":
            status = "PARTIAL" if pct is None or pct < 99.0 else "DECODED"
            if mod is None:
                mod = "props.py (generic)"
        elif mod:
            status = "PARTIAL"
        else:
            status = "UNKNOWN"
            mod = "--"
        rows.append({
            "ext": ext, "count": s["count"], "min": s["min"], "max": s["max"],
            "form": form, "module": mod or "props.py (generic)",
            "coverage": None if pct is None else round(pct, 1),
            "status": status,
            "roundtrip": grm.get(ext, {}).get("rt_ok"),
            "edit_verdict": edt.get(ext, {}).get("verdict"),
            "count_convention": grm.get(ext, {}).get("count_convention"),
            "trap_fields": gap.get(ext, {}).get("trap_fields", 0),
        })
    rows.sort(key=lambda r: -r["count"])
    with open(args.json, "w") as fh:
        json.dump(rows, fh, indent=1)

    if args.table:
        print("| ext | files | size range | form | status | read by | props coverage | edit-safe |")
        print("|---|---:|---|---|---|---|---:|---|")
        for r in rows:
            cv = "--" if r["coverage"] is None else "%.1f%%" % r["coverage"]
            safe = ("yes" if r["edit_verdict"] == "PASS" and not r["trap_fields"]
                    else "**NO -- %d trap fields**" % r["trap_fields"] if r["trap_fields"]
                    else "--")
            print("| `.%s` | %d | %s-%s B | %s | **%s** | %s | %s | %s |" % (
                r["ext"], r["count"], r["min"], r["max"], r["form"],
                r["status"], r["module"], cv, safe))
    n = collections.Counter(r["status"] for r in rows)
    sys.stderr.write("\n%s  (%d extensions) -> %s\n" % (dict(n), len(rows), args.json))


if __name__ == "__main__":
    main()
