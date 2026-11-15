#!/usr/bin/env python3
"""Launch RDR Modding Studio.

    python RDR_Studio.py                 open on the working (modded) ISO
    python RDR_Studio.py --port 9000
    python RDR_Studio.py --no-browser
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rdrstudio import config as cfg          # noqa: E402
from rdrstudio.studio import Studio, serve   # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="RDR Modding Studio")
    ap.add_argument("--vanilla", default=cfg.VANILLA_ISO)
    ap.add_argument("--modded", default=cfg.MODDED_ISO)
    ap.add_argument("--pcsx2", default=cfg.PCSX2_EXE)
    ap.add_argument("--port", type=int, default=cfg.PORT)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--lan", action="store_true",
                    help="listen on all interfaces so other devices on your "
                         "network can reach the Studio (no authentication)")
    ap.add_argument("--host", default=None,
                    help="explicit bind address; overrides --lan")
    args = ap.parse_args()

    host = args.host or ("0.0.0.0" if args.lan else "127.0.0.1")

    for label, path in (("vanilla ISO", args.vanilla), ("modded ISO", args.modded)):
        if not os.path.exists(path):
            sys.exit("missing %s: %s\nRun scripts/setup_check.py first." % (label, path))

    studio = Studio(args.vanilla, args.modded, args.pcsx2, cfg.emulog())
    studio.note("studio started")
    studio.start_warmup()
    serve(studio, port=args.port, open_browser=not args.no_browser, host=host)


if __name__ == "__main__":
    main()
