"""What can this toolkit actually read, measured against the disc.

An inventory, not a feature list. Every extension is classified by *testing*
a sample of its files rather than by a hand-written table -- the hand-written
table is precisely how ~100 plain-text formats came to be filed as "opaque
binary" and stayed that way.

Classification:

  text      parses with props.py into named, editable fields
  binary+   a binary format with a decoder in this toolkit
  binary-   binary, no decoder yet

Run it after any decoding work; the totals are what the README quotes.
"""
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rdrstudio import Game, props  # noqa: E402
from rdrstudio.config import VANILLA_ISO  # noqa: E402

# Binary formats this toolkit has a decoder for, and what it gets you.
DECODED_BINARY = {
    "rsc":    "geometry, textures, collision, per-surface materials",
    "xrsc":   "same container, Showdown variant",
    "tex":    "loose texture -> PNG, and PNG -> texture",
    "mod":    "model container (the RSC0-embedded form)",
    "modb":   "model container variant",
    "actb":   "behaviour table (structure; opcodes unknown)",
    "stateb": "state table (structure; opcodes unknown)",
    "anim":   "fully decoded -- uncompressed, cm2 and cmp curves",
    # Everything below was decoded AFTER this table was first written, and
    # nobody updated it -- which is why the headline coverage figure had been
    # understating itself. A stale "what we can read" list is its own kind of
    # measurement error, and this file's own docstring warns about exactly
    # that failure mode for text formats.
    "stm":    "streamed audio -- 4-bit IMA ADPCM (rdrstudio/stm.py)",
    "bd":     "sound bank -- PS2 SPU-ADPCM (rdrstudio/vag.py)",
    "hd":     "Sony bank header, IECS chunks: offsets + real sample rates",
    "strtbl": "every displayed string, PJW-hash keyed (all 203 re-encode)",
    "ipu":    "full-screen still, MPEG-2 intra (rdrstudio/ipudec.py)",
    "skel":   "skeleton -- plain text, 1,128 files, 33,557 bones",
    # Text, but a different grammar from props.py -- each has its own reader,
    # so they are readable even though the generic parser finds no fields.
    "csv":    "comma table (own reader)",
    "txt":    "free text",
    "swui":   "menu layout, <TAG:VALUE> grammar (own reader)",
    "rnt":    "chunk-name manifest (own reader)",
}
SAMPLES = 6


def main():
    game = Game(VANILLA_ISO)
    count = Counter()
    size = Counter()
    sampled = defaultdict(list)

    for arc in game.archives():
        try:
            dv = game.archive(arc)
        except Exception:
            continue
        for e in dv:
            ext = (e.ext or "?").lower()
            count[ext] += 1
            size[ext] += e.size_full or 0
            if len(sampled[ext]) < SAMPLES:
                try:
                    sampled[ext].append(dv.read(e))
                except Exception:
                    pass
        game._cache.pop(arc.upper(), None)

    kind = {}
    fields = {}
    for ext, blobs in sampled.items():
        blobs = [b for b in blobs if b]
        if not blobs:
            kind[ext] = "binary-"
            continue
        if all(props.is_textual(b) for b in blobs):
            n = sum(len(props.parse(b)) for b in blobs)
            if n:
                kind[ext] = "text"
                fields[ext] = n // len(blobs)
                continue
        kind[ext] = "binary+" if ext in DECODED_BINARY else "binary-"

    total_files = sum(count.values())
    total_size = sum(size.values())
    buckets = defaultdict(lambda: [0, 0, 0])

    print("%-14s %8s %9s  %-8s %8s  %s"
          % ("ext", "files", "MB", "kind", "fields", "note"))
    print("-" * 96)
    for ext, n in count.most_common():
        k = kind.get(ext, "binary-")
        b = buckets[k]
        b[0] += n
        b[1] += size[ext]
        b[2] += 1
        print("%-14s %8d %9.1f  %-8s %8s  %s"
              % (ext, n, size[ext] / 1e6, k,
                 fields.get(ext, "") or "", DECODED_BINARY.get(ext, "")))

    print("\n" + "=" * 96)
    print("%-24s %6s %10s %8s %10s %8s"
          % ("", "exts", "files", "of all", "MB", "of all"))
    readable = 0
    for k, label in (("text", "text (fully editable)"),
                     ("binary+", "binary, decoded"),
                     ("binary-", "binary, not decoded")):
        n, b, x = buckets[k]
        if k != "binary-":
            readable += n
        print("%-24s %6d %10d %7.1f%% %10.1f %7.1f%%"
              % (label, x, n, 100.0 * n / total_files,
                 b / 1e6, 100.0 * b / total_size))
    print("%-24s %6d %10d %7.1f%% %10.1f %7.1f%%"
          % ("TOTAL", len(count), total_files, 100.0,
             total_size / 1e6, 100.0))
    print("\nreadable: %d / %d files  (%.1f%%)"
          % (readable, total_files, 100.0 * readable / total_files))

    print("\nstill opaque, biggest first -- the work queue")
    op = sorted(((size[e], count[e], e) for e in count
                 if kind.get(e) == "binary-"), reverse=True)
    for b, n, e in op[:12]:
        print("  %-12s %7d files  %8.1f MB" % (e, n, b / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
