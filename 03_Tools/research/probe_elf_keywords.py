"""probe_elf_keywords.py -- the AI keyword vocabulary in the executable, and
the thing it does NOT give us.

Two results, and the negative one is the reason this file exists.

POSITIVE. The unpacked executable carries the engine's complete AI query
vocabulary as static data: stride-8 records of (u32, char* name) whose names
are exactly the bare identifiers the `.actb`/`.stateb` corpus uses --
`angle`, `dist`, `blocked`, `gundrawn`, `health`, `lastseentime`, `numalive`,
`menuopen`. That is the full list of conditions a behaviour table is able to
ask about, which is worth having on its own: it bounds what is authorable.

NEGATIVE. The `u32` paired with each name is **not** the number the `.actb`
files carry for that keyword. Four readings were tested against 50,746 keyword
occurrences over 4,114 files -- the word before the name, the word after it,
under both possible phase alignments of the table, plus the table INDEX as the
id. Best score **1.7%**, against a noise floor of about 1%. All four fail.

So the cheap route stops here. The numeric opcodes in these files are resolved
by code, not by a lookup table sitting in the data segment, and naming the rest
of them needs the MIPS dispatch disassembled. This probe is what establishes
that before anyone spends a week on it.

The image is `07_Research/SLUS_205.00.unpacked.bin`, produced by
`rdrstudio/elfpack.py`; it is a raw memory image loaded at 0x00100000, not an
ELF, so pointers are resolved against that base rather than a section table.

    python3 03_Tools/research/probe_elf_keywords.py [--dump-vocab OUT.txt]
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
IMAGE = os.path.join(REPO, "07_Research", "SLUS_205.00.unpacked.bin")
LOAD_VADDR = 0x00100000
STR = re.compile(rb"[\x20-\x7e]{2,}\x00")
MAGIC = b"version\x00"


def image_strings(img):
    return {m.start(): m.group()[:-1].decode("ascii") for m in STR.finditer(img)}


def find_tables(img, strings, min_entries=10):
    """Runs of stride-8 (u32, char*) records, at both 4-byte phases.

    Phase matters: the biggest table on this image starts at 0x3f9564, which is
    4 mod 8, so a scan anchored at 0 walks straight past it.
    """
    known = {LOAD_VADDR + off for off in strings}
    tables = []
    for phase in (0, 4):
        run, off = [], phase
        while off + 8 <= len(img):
            val, ptr = struct.unpack_from("<II", img, off)
            if ptr in known and val < 0x10000:
                run.append((off, val, strings[ptr - LOAD_VADDR]))
            else:
                if len(run) >= min_entries:
                    tables.append((phase, run))
                run = []
            off += 8
        if len(run) >= min_entries:
            tables.append((phase, run))
    tables.sort(key=lambda t: -len(t[1]))
    return tables


def inflate(backing, e):
    raw = backing.read_at(e.offset, e.size_comp)
    return raw if not e.compressed else zlib.decompress(raw, -15)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso", default=VANILLA)
    ap.add_argument("--image", default=IMAGE)
    ap.add_argument("--limit", type=int, default=4000,
                    help="files to score the id hypotheses over")
    ap.add_argument("--dump-vocab", default="")
    ap.add_argument("--json", default=os.path.join(REPO, "07_Research", "elf_keywords.json"))
    args = ap.parse_args()

    img = open(args.image, "rb").read()
    strings = image_strings(img)
    tables = find_tables(img, strings)
    print("image %d bytes, %d strings, %d stride-8 name tables"
          % (len(img), len(strings), len(tables)))

    vocab = {}
    for phase, run in tables[:2]:
        print("  table at 0x%06x (phase %d): %d names, e.g. %s"
              % (run[0][0], phase, len(run),
                 ", ".join(n for _o, _v, n in run[:6])))
        for off, val, name in run:
            ptr_at = off + 4
            after = struct.unpack_from("<I", img, ptr_at + 4)[0] if ptr_at + 8 <= len(img) else None
            vocab[name.lower()] = {"pairedA": val, "pairedB": after,
                                   "index": run.index((off, val, name))}

    print("\n%d distinct keyword names" % len(vocab))
    if args.dump_vocab:
        with open(args.dump_vocab, "w") as fh:
            fh.write("# The engine's AI query vocabulary -- every condition a\n"
                     "# .actb/.stateb behaviour table can ask about.\n"
                     "#\n"
                     "# Extracted from the (u32, char*) tables at 0x3f9564 and\n"
                     "# 0x3f9ba4 in 07_Research/SLUS_205.00.unpacked.bin, the\n"
                     "# raw memory image that rdrstudio/elfpack.py produces.\n"
                     "# Regenerate:\n"
                     "#   python3 03_Tools/research/probe_elf_keywords.py \\\n"
                     "#       --dump-vocab 05_Docs/RESEARCH/ai-vocabulary.txt\n"
                     "#\n"
                     "# %d names. The numbers paired with them in the image are\n"
                     "# NOT the codes the .actb files carry -- see the probe.\n"
                     % len(vocab))
            for n in sorted(vocab):
                fh.write(n + "\n")
        print("vocabulary -> %s" % args.dump_vocab)

    # Score every way the paired number could be the file's code for that name.
    iso = Iso9660(args.iso)
    st = collections.Counter()
    n_files = 0
    for f in iso.files():
        if n_files >= args.limit:
            break
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b"Dave", b"DAVE"):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            if e.ext not in ("actb", "stateb", "acttbl") or n_files >= args.limit:
                continue
            try:
                blob = inflate(d.backing, e)
            except zlib.error:
                continue
            if blob[:8] != MAGIC:
                continue
            n_files += 1
            for m in STR.finditer(blob):
                name = m.group()[:-1].decode("ascii").lower()
                v = vocab.get(name)
                if v is None:
                    continue
                st["occurrences"] += 1
                pre = (struct.unpack_from("<I", blob, m.start() - 4)[0]
                       if m.start() >= 4 else None)
                post = (struct.unpack_from("<I", blob, m.end())[0]
                        if m.end() + 4 <= len(blob) else None)
                for key, want in (("pairedA", v["pairedA"]),
                                  ("pairedB", v["pairedB"]),
                                  ("index", v["index"])):
                    if want is None:
                        continue
                    if pre == want:
                        st["before==" + key] += 1
                    if post == want:
                        st["after==" + key] += 1
    iso.close()

    occ = st["occurrences"] or 1
    print("\nscored over %d files, %d keyword occurrences" % (n_files, occ))
    print("%-18s %8s %7s" % ("HYPOTHESIS", "HITS", "RATE"))
    rows = [(k, v) for k, v in st.items() if k != "occurrences"]
    for k, v in sorted(rows, key=lambda kv: -kv[1]):
        print("%-18s %8d %6.1f%%" % (k, v, 100.0 * v / occ))
    best = max((v for _k, v in rows), default=0)
    print("\nVERDICT: best hypothesis scores %.1f%% -- the number paired with a"
          % (100.0 * best / occ))
    print("keyword in the executable is NOT the code the .actb files use for it.")
    print("Naming the remaining opcodes needs the MIPS dispatch disassembled.")

    json.dump({"vocabulary": sorted(vocab), "scores": dict(st),
               "files_scored": n_files},
              open(args.json, "w"), indent=1)
    print("-> %s" % args.json)


if __name__ == "__main__":
    main()
