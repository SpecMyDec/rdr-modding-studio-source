"""Extract every archive on the disc to browsable files, losslessly.

This is READ-ONLY exploration. Nothing here can ever be repacked -- that is
what breaks the game. Extract, read, then express the change as an in-place
patch through rdrstudio.

    python scripts/extract_all.py                 everything
    python scripts/extract_all.py --skip-streams  skip the 1.2 GB audio blob
    python scripts/extract_all.py --only LVLS_SNG

Also writes manifest.json: every file's archive, offset, sizes, slot,
headroom, compression and alignment -- the index the Studio and any future
tooling can query without touching the ISO.
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rdrstudio import Game          # noqa: E402
from rdrstudio import config as cfg  # noqa: E402


def safe(name):
    """Archive names use '/' for subdirs and are already lowercase."""
    parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".", "..")]
    return os.path.join(*parts) if parts else "_unnamed"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso", default=cfg.VANILLA_ISO)
    ap.add_argument("--out", default=cfg.EXTRACTED)
    ap.add_argument("--skip-streams", action="store_true")
    ap.add_argument("--only", default=None, help="substring filter on archive name")
    args = ap.parse_args()

    t0 = time.time()
    g = Game(args.iso)
    archives = g.archives()
    if args.only:
        archives = [a for a in archives if args.only.upper() in a.upper()]
    if args.skip_streams:
        archives = [a for a in archives if "STREAMS" not in a.upper()]

    os.makedirs(args.out, exist_ok=True)
    manifest = []
    total_files = total_bytes = 0

    for arc in archives:
        d = g.archive(arc)
        base = os.path.join(args.out, safe(arc)[:-4] if arc.lower().endswith(".dat")
                            else safe(arc))
        os.makedirs(base, exist_ok=True)
        n = written = 0

        for e in d:
            manifest.append(dict(
                archive=arc, name=e.name, ext=e.ext, index=e.index,
                offset=e.offset, size=e.size_full, stored=e.size_comp,
                slot=e.slot, headroom=e.headroom,
                compressed=e.compressed, aligned=e.aligned))
            try:
                data = d.read(e)
            except Exception as ex:
                print("   ! %s: %s" % (e.name, ex))
                continue
            dest = os.path.join(base, safe(e.name))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(data)
            n += 1
            written += len(data)

        total_files += n
        total_bytes += written
        print("  %-24s %6d files  %8.1f MB" % (arc, n, written / 1048576.0))
        g._cache.pop(arc.upper(), None)

    mpath = os.path.join(args.out, "manifest.json")
    with open(mpath, "w") as f:
        json.dump(dict(iso=args.iso, archives=archives, files=manifest), f)

    print("\n%d files, %.1f MB, %.0fs" % (total_files, total_bytes / 1048576.0,
                                          time.time() - t0))
    print("manifest: %s (%d entries)" % (mpath, len(manifest)))


if __name__ == "__main__":
    main()
