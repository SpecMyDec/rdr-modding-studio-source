"""The files that hold a level's EVENTS, as opposed to its geometry.

He asked for markers showing where the player moves to a new area, where a
prompt appears, and where a cutscene plays. This checks, file by file, which of
those actually exist as positioned data on the disc -- so the editor draws only
what is real and says so where nothing is.

  .sub      sub-level / area transitions -- doors into other maps
  .cam      named camera shots, including cutscene ("TGS_") sequences
  .glows    lens flares / light glows, positioned and scaled
  .shadows  the level's own sun direction (the viewport hardcodes one today)
  .flags    named level events -- NO coordinates, deliberately
  .aisp     AI spawn points, in named groups
  .trn/.til train consist and terrain-tile sequence (moving-origin levels)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdrstudio.game import Game                      # noqa: E402
from rdrstudio.config import VANILLA_ISO             # noqa: E402

# The STOCK disc, not the working one: a read-only structural probe must
# not depend on what the last test left behind (REQ-035). The repoint was
# measured output-neutral at the time it was made -- see REQ-074.
DISC = Path(VANILLA_ISO)
EXTS = (".sub", ".cam", ".glows", ".shadows", ".flags", ".aisp", ".trn",
        ".til", ".znv", ".chase", ".mmb", ".film")
ARCHIVES = ["LVLS_SNG/TOMB.DAT", "LVLS_SNG/TRAIN.DAT", "LVLS_SNG/TRAINGOO.DAT"]


def main():
    game = Game(DISC, writable=False)
    for arc in ARCHIVES:
        print("\n" + "=" * 72)
        print(arc)
        print("=" * 72)
        names = [e.name for e in game.toc(arc)]
        for ext in EXTS:
            hits = [n for n in names if n.lower().endswith(ext)]
            if not hits:
                print("\n  %-9s -- none" % ext)
                continue
            print("\n  %-9s -- %d file(s): %s"
                  % (ext, len(hits), ", ".join(hits[:6])
                     + (" ..." if len(hits) > 6 else "")))
            # Show the smallest one whole; it is the most likely to be readable.
            pick = sorted(hits, key=lambda n: len(game.read(arc, n)))[0]
            data = game.read(arc, pick)
            print("     %s  (%d bytes)" % (pick, len(data)))
            try:
                txt = data.decode("ascii")
            except UnicodeDecodeError:
                try:
                    txt = data.decode("utf-16-le")
                    print("     [UTF-16LE]")
                except UnicodeDecodeError:
                    print("     BINARY -- first 48 bytes: %s"
                          % data[:48].hex(" "))
                    continue
            lines = [l for l in txt.splitlines() if l.strip()]
            for l in lines[:26]:
                print("       %s" % l[:96])
            if len(lines) > 26:
                print("       ... %d more lines" % (len(lines) - 26))
    game.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
