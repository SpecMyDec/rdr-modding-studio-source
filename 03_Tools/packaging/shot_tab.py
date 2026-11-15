#!/usr/bin/env python3
"""Open the app on a named tab and hold it there, so a screenshot can be taken.

Used to prove the 3D viewport really draws inside the packaged window rather
than only in a browser. Starts the same in-process server the app does, opens
the same WebView2 window, clicks the tab by its visible label, and reports what
the page says about itself.

    python packaging/shot_tab.py "3D Editor" [seconds]
"""

import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, TOOLS)

from rdrshell import paths                                 # noqa: E402
from rdrshell.server import StudioServer                   # noqa: E402

import webview                                             # noqa: E402

LABEL = sys.argv[1] if len(sys.argv) > 1 else "3D Editor"
HOLD = float(sys.argv[2]) if len(sys.argv) > 2 else 40.0

CLICK = """
(function (want) {
  var buttons = [].slice.call(document.querySelectorAll('nav button'));
  var labels = buttons.map(function (b) { return b.textContent.trim(); });
  var hit = buttons.filter(function (b) {
    return b.textContent.trim() === want;
  })[0];
  if (hit) hit.click();
  return JSON.stringify({labels: labels, clicked: !!hit});
})(%s);
"""

STATE = """
(function () {
  var cs = [].slice.call(document.querySelectorAll('canvas')).map(function (c) {
    var r = c.getBoundingClientRect();
    return {w: c.width, h: c.height, vis: r.width > 10 && r.height > 10};
  });
  var sel = document.querySelector('nav button.active, nav button[aria-selected=true]');
  return JSON.stringify({
    canvases: cs,
    active: sel ? sel.textContent.trim() : null,
    bodyText: (document.body.innerText || '').slice(0, 260)
  });
})();
"""

out = {}
s = paths.load()
srv = StudioServer()
url = srv.start(s, TOOLS)
srv.wait_ready()
print("server: %s" % url)


def drive(win):
    time.sleep(6.0)
    out["click"] = json.loads(win.evaluate_js(CLICK % json.dumps(LABEL)))
    time.sleep(6.0)
    out["state"] = json.loads(win.evaluate_js(STATE))
    print(json.dumps(out, indent=1))
    time.sleep(HOLD)
    win.destroy()


w = webview.create_window("RDR Modding Studio", url, width=1600, height=1000)
threading.Thread(target=lambda: None).start()
webview.start(drive, w)
srv.stop()
