#!/usr/bin/env python3
"""Serve the RELEASE edition's front end from source, without a ten-minute freeze.

    py -3 packaging/preview_release.py [--port 8731] [--lan]

What a downloader sees is not what `Launch Studio.bat` shows: the release build
strips every feature marked `internal` in features.json out of the markup and
404s its routes. Reviewing the dev Studio therefore reviews tabs nobody outside
this machine has. This stages web/ exactly as build.py does for a release
(same strip, same comment removal), points the server at the staged copy and
runs with RDR_BUILD=release, so the nav, the panels and the route gating are
the shipped ones. The discs are the dev machine's; nothing is frozen.

Stop the normal Studio first: two Studios must not share one working disc.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, TOOLS)
os.environ["RDR_BUILD"] = "release"

import build as _build                                    # noqa: E402
from rdrstudio import config as cfg                       # noqa: E402
from rdrstudio import studio as _studio                   # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=cfg.PORT)
    ap.add_argument("--lan", action="store_true")
    args = ap.parse_args()
    stage, report = _build.stage_web("preview-release", "release",
                                     _build.read_manifest())
    print("staged %s (stripped: %s)" % (stage, ", ".join(report.get("stripped") or []) or "nothing"),
          flush=True)
    _studio.WEB_ROOT = stage
    s = _studio.Studio(cfg.VANILLA_ISO, cfg.MODDED_ISO, cfg.PCSX2_EXE, cfg.emulog())
    s.note("release preview started")
    s.start_warmup()
    _studio.serve(s, port=args.port, open_browser=False,
                  host="0.0.0.0" if args.lan else "127.0.0.1")


if __name__ == "__main__":
    main()
