#!/usr/bin/env python3
"""Risk probe: does the existing front end actually work inside WebView2?

Opens a pywebview window on an already-running Studio, waits for the page to
settle, then runs JavaScript in it to report: the webview engine, whether
WebGL2 is available and through which driver, whether three.js imported, and
the navigation timings. Prints one JSON blob and exits.

Read-only. It talks to a Studio that is already listening; it starts nothing
and writes nothing.

    python packaging/probe_webview.py [url]
"""

import json
import sys
import threading
import time

import webview

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8731/"

PROBE = r"""
(function () {
  var c = document.createElement('canvas');
  var gl2 = c.getContext('webgl2');
  var gl = gl2 || c.getContext('webgl');
  var dbg = gl && gl.getExtension('WEBGL_debug_renderer_info');
  var n = performance.getEntriesByType('navigation')[0] || {};
  var paint = {};
  performance.getEntriesByType('paint').forEach(function (p) {
    paint[p.name] = Math.round(p.startTime);
  });
  var res = performance.getEntriesByType('resource');
  var bytes = 0;
  res.forEach(function (r) { bytes += (r.decodedBodySize || 0); });

  // Does the app's own three.js canvas exist and has it drawn?
  var canvases = document.querySelectorAll('canvas');
  var live = 0;
  canvases.forEach(function (cv) {
    if (cv.width > 1 && cv.height > 1) live++;
  });

  return JSON.stringify({
    ua: navigator.userAgent,
    webgl2: !!gl2,
    webgl: !!gl,
    vendor: dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : null,
    renderer: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : null,
    maxTextureSize: gl ? gl.getParameter(gl.MAX_TEXTURE_SIZE) : null,
    maxSamples: gl2 ? gl2.getParameter(gl2.MAX_SAMPLES) : null,
    domContentLoaded: Math.round(n.domContentLoadedEventEnd || 0),
    loadEventEnd: Math.round(n.loadEventEnd || 0),
    firstContentfulPaint: paint['first-contentful-paint'] || null,
    resourceCount: res.length,
    resourceKB: Math.round(bytes / 1024),
    canvases: canvases.length,
    liveCanvases: live,
    title: document.title,
    bodyChildren: document.body ? document.body.children.length : 0,
    modulesSupported: 'noModule' in document.createElement('script')
  });
})();
"""

result = {}
t_start = time.perf_counter()


def probe(window):
    # Give the page time to import three.js and lay itself out.
    time.sleep(6.0)
    try:
        raw = window.evaluate_js(PROBE)
        result.update(json.loads(raw))
        result["window_to_probe_s"] = round(time.perf_counter() - t_start, 3)
    except Exception as ex:                       # noqa: BLE001
        result["error"] = "%s: %s" % (type(ex).__name__, ex)
    finally:
        window.destroy()


win = webview.create_window("RDR Modding Studio -- webview probe", URL,
                            width=1400, height=900)
webview.start(probe, win, debug=False)

print(json.dumps(result, indent=1))
