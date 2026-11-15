#!/usr/bin/env python3
"""Drive the first-run window far enough to photograph it.

Everything here is the real flow except the file-picker dialog, which cannot be
automated: instead of opening it, the script calls the same
`pywebview.api.verify(path)` the dialog would have called, with the real ISO.
So the verdict on screen is a genuine tier-3 verification, not a mock-up.

    python packaging/shot_firstrun.py [1|2|3]      which step to stop on

Never completes step 3 -- it does not start the copy.
"""

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, TOOLS)

from rdrshell import paths                                 # noqa: E402
from rdrshell.shell import FirstRunApi                     # noqa: E402
from rdrstudio.config import VANILLA_ISO                   # noqa: E402

import webview                                             # noqa: E402

STEP = int(sys.argv[1]) if len(sys.argv) > 1 else 2
HOLD = float(sys.argv[2]) if len(sys.argv) > 2 else 45.0
ISO = VANILLA_ISO

with open(os.path.join(HERE, "rdrshell", "firstrun.html"), encoding="utf-8") as fh:
    HTML = fh.read()

# A throwaway settings dict -- this must not overwrite the user's real one.
s = dict(paths.DEFAULTS)
s = paths._fill_defaults(s)
window_ref = []


class ShotApi(FirstRunApi):
    def accept_disclaimer(self):
        return True            # deliberately does not save


def drive(win):
    time.sleep(2.5)
    if STEP >= 2:
        win.evaluate_js(
            "document.getElementById('agree').checked=true;"
            "document.getElementById('agree').onchange({target:{checked:true}});"
            "document.getElementById('go1').click();")
        time.sleep(1.0)
        # the real verify, on the real disc, minus the file dialog
        win.evaluate_js(
            "(async function(){"
            " iso=%s;"
            " document.getElementById('isoPath').textContent=iso;"
            " document.getElementById('isoPath').classList.add('set');"
            " showVerdict(await pywebview.api.verify(iso));"
            "})();" % json.dumps(ISO))
    if STEP >= 3:
        time.sleep(2.5)
        win.evaluate_js("document.getElementById('go2').click();")
    time.sleep(HOLD)
    win.destroy()


api = ShotApi(s, window_ref, lambda: None)
w = webview.create_window("RDR Modding Studio -- setup", html=HTML,
                          width=900, height=820, js_api=api)
window_ref.append(w)
webview.start(drive, w)
