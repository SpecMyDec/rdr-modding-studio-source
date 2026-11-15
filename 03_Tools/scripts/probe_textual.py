"""How many of the "opaque" formats are actually plain text?

This engine stores an unreasonable amount as ASCII: the level manifest, zone
instances, enemy rosters, skeletons, weapon and creature stats are all text
already. The extension census lists ~90 more extensions as opaque, and if
even a handful of those are text they are editable today with no decoding
work at all -- they just need to be recognised.

Sample a few files per extension and classify: printable ASCII, keyword/value
text, a known binary magic, or genuinely opaque.
"""
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rdrstudio import Game  # noqa: E402
from rdrstudio.config import VANILLA_ISO  # noqa: E402

KNOWN_TEXT = {"lvl", "znes", "bnds", "enm", "spwn", "lnes", "bxes", "guns",
              "cr", "skel", "csv", "txt", "ini", "rnt"}
SAMPLES = 4


def textish(b):
    """Fraction of bytes that are printable ASCII, tab, CR or LF."""
    if not b:
        return 0.0
    ok = sum(1 for c in b if 32 <= c < 127 or c in (9, 10, 13))
    return ok / len(b)


def main():
    game = Game(VANILLA_ISO)
    seen = defaultdict(list)
    for arc in game.archives():
        try:
            dv = game.archive(arc)
        except Exception:
            continue
        for e in dv:
            ext = (e.ext or "?").lower()
            if ext in KNOWN_TEXT or len(seen[ext]) >= SAMPLES:
                continue
            try:
                raw = dv.read(e)
            except Exception:
                continue
            if raw:
                seen[ext].append(raw)
        # Do not hold 66 archives of decompressed data in memory.
        game._cache.pop(arc.upper(), None)

    rows = []
    for ext, blobs in seen.items():
        score = sum(textish(b[:4096]) for b in blobs) / len(blobs)
        magic = blobs[0][:4]
        head = blobs[0][:70].decode("latin-1", "replace")
        head = re.sub(r"[^\x20-\x7e]", ".", head)
        rows.append((score, ext, len(blobs), magic, head))

    rows.sort(reverse=True)
    print("Extensions that look like TEXT (>=95%% printable)\n")
    print("%-14s %7s  %s" % ("ext", "ascii", "first bytes"))
    print("-" * 100)
    n_text = 0
    for score, ext, n, magic, head in rows:
        if score < 0.95:
            continue
        n_text += 1
        print("%-14s %6.0f%%  %s" % (ext, score * 100, head))

    print("\n\nBinary, grouped by magic\n")
    print("%-14s %7s  %-10s %s" % ("ext", "ascii", "magic", "first bytes"))
    print("-" * 100)
    for score, ext, n, magic, head in rows:
        if score >= 0.95:
            continue
        m = re.sub(r"[^\x20-\x7e]", ".", magic.decode("latin-1", "replace"))
        print("%-14s %6.0f%%  %-10s %s" % (ext, score * 100, m, head[:60]))

    print("\n%d of %d sampled extensions are plain text and editable today."
          % (n_text, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
