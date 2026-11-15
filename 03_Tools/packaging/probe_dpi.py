#!/usr/bin/env python3
"""Is the packaged window actually rendering at the screen's real resolution?

The window being 1125 physical pixels wide for a 900-pixel request proves
nothing on its own -- that is what BOTH a stretched unaware window and a
correctly scaled aware one look like from the outside. The difference is
visible only from inside the page:

    devicePixelRatio 1.00  ->  the app drew 900 pixels and Windows blew them
                               up 1.25x. Text is resampled. Blurry.
    devicePixelRatio 1.25  ->  the app drew 1125 real pixels. Sharp.

    python packaging/probe_dpi.py --aware
    python packaging/probe_dpi.py --unaware
"""

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, TOOLS)

AWARE = "--unaware" not in sys.argv

mode = None
if AWARE:
    from rdrshell.dpi import make_dpi_aware                # noqa: E402
    mode = make_dpi_aware()

import webview                                            # noqa: E402

from rdrshell import paths                                # noqa: E402
from rdrshell.server import StudioServer                  # noqa: E402

PROBE = """
JSON.stringify({
  devicePixelRatio: window.devicePixelRatio,
  innerWidth: window.innerWidth,
  innerHeight: window.innerHeight,
  screenW: screen.width,
  screenH: screen.height
});
"""

s = paths.load()
srv = StudioServer()
url = srv.start(s, TOOLS)
srv.wait_ready()

out = {"dpi_awareness_set": mode, "requested_window": 900}


def drive(win):
    time.sleep(5.0)
    out.update(json.loads(win.evaluate_js(PROBE)))
    win.destroy()


w = webview.create_window("RDR Modding Studio", url, width=900, height=700)
webview.start(drive, w)
srv.stop()
print(json.dumps(out, indent=1))
