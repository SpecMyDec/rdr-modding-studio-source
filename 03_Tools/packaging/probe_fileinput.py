#!/usr/bin/env python3
"""Risk probe: can a picture get INTO the app through the desktop window?

The fourth member of the browser-tab-to-desktop-app family, after the missing
firstrun.html, Mark of the Web, cancelled downloads and incognito
localStorage. Every one of those was a thing the tab did for us that the
window does not, and every one shipped because the check ran in a tab.

Export has `packaging/rdrshell/selfcheck.py`. IMPORT had nothing: the Studio
takes a picture, a .wav, a .bvh and an .obj in through `<input type=file>` +
`FileReader`, and none of that is exercised anywhere without a person driving
a mouse. So this drives it.

Two questions, answered separately because they fail separately:

    1  does the PAGE half work -- File -> FileReader -> base64 -> POST ->
       the bytes land on the disc?  Answered by putting a File into the
       input with DataTransfer and firing `change`, which is everything the
       real flow does except the dialog.
    2  does the DIALOG open at all when the input is clicked?  Answered by
       clicking from a worker thread and diffing the desktop's #32770
       windows, then closing whatever appeared.

Measured on this machine, 2026-08-05, against the source tree: 1 passes
end to end (the picture reaches the ISO and decodes back), and 2 opens an
`Open` dialog -- owned by the WebView2 browser process, not by us. What is
still NOT covered is the handoff in between: whether picking a file in that
dialog populates `input.files`. That is Chromium's own plumbing with no app
code in it, and driving the dialog by hand did not reliably confirm it.

Read-only about the tool; it WRITES one still into whatever working ISO the
Studio it talks to has open. Point it at a scratch disc.

    packaging\\.venv\\Scripts\\python.exe packaging/probe_fileinput.py [url]
"""

import base64
import ctypes
import io
import json
import sys
import threading
import time

import webview

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8731/"
TARGET = sys.argv[2] if len(sys.argv) > 2 else "mainmenu.ipu"

OUT = {"url": URL, "target": TARGET}


def _png():
    """A picture no decode can produce by accident: left half red, right blue."""
    from PIL import Image
    im = Image.new("RGB", (512, 448))
    px = im.load()
    for y in range(448):
        for x in range(512):
            px[x, y] = (255, 0, 0) if x < 256 else (0, 0, 255)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


# The page is asked about ITS OWN state, never told what to do -- if the front
# end renamed #stillFile this has to report "missing", not paper over it.
_READY = r"""
JSON.stringify({
  ready: document.readyState === 'complete' &&
         document.querySelectorAll('nav button').length > 3,
  title: document.title
})
"""

_OPEN_TAB = r"""
(function(){
  var b = [].slice.call(document.querySelectorAll('nav button'))
    .find(function(x){ return x.dataset && x.dataset.tab === 'movies'; });
  if (!b) return 'no movies tab button';
  b.click();
  return 'clicked';
})()
"""

_SURVEY = r"""
JSON.stringify({
  input: !!document.getElementById('stillFile'),
  button: !!document.getElementById('stillReplaceBtn'),
  wired: !!window.__stillWired,
  has_DataTransfer: typeof DataTransfer === 'function',
  has_FileReader: typeof FileReader === 'function',
  has_showOpenFilePicker: typeof window.showOpenFilePicker === 'function',
  stills_listed: document.querySelectorAll('#stillList .row').length,
  list_says: ((document.getElementById('stillList') || {}).textContent || '')
               .trim().slice(0, 160),
  tab_on: !!(document.getElementById('tab-movies') || {}).classList
            && document.getElementById('tab-movies').classList.contains('on')
})
"""

# Pick the still by name off the list the app itself rendered, so the probe
# goes through openStill() and stillTarget is set the way a click sets it.
_SELECT = r"""
(function(){
  var want = %s.replace('.ipu','').toLowerCase();
  var rows = [].slice.call(document.querySelectorAll('#stillList .row'));
  var row = rows.find(function(r){
    return (r.textContent || '').trim().toLowerCase().indexOf(want) === 0; });
  if (!row) return 'not in the list';
  row.click();
  return 'selected';
})()
"""

# Question 1. A File built in the page, handed to the input the way a picker
# hands one over, then `change` -- from here on it is the app's own code.
_FEED = r"""
(function(){
  var b64 = %s;
  var bin = atob(b64);
  var buf = new Uint8Array(bin.length);
  for (var i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
  var inp = document.getElementById('stillFile');
  if (!inp) return 'no #stillFile';
  try {
    var dt = new DataTransfer();
    dt.items.add(new File([buf], 'probe.png', { type: 'image/png' }));
    inp.files = dt.files;
  } catch (e) { return 'DataTransfer failed: ' + String(e); }
  if (!inp.files || inp.files.length !== 1) return 'input.files stayed empty';
  inp.dispatchEvent(new Event('change'));
  return 'fed ' + inp.files[0].size + ' bytes';
})()
"""

_DROP = r"""
JSON.stringify({
  text: (document.getElementById('stillDrop') || {}).textContent || '',
  commit: !!document.getElementById('stillCommit')
})
"""

_COMMIT = r"""
(function(){
  var b = document.getElementById('stillCommit');
  if (!b) return 'no commit button';
  b.click();
  return 'clicked';
})()
"""


# ---- question 2: the native dialog ---------------------------------------

def _dialogs():
    """Every VISIBLE top-level #32770 on the desktop, with its owning pid.

    NOT filtered to this process, and that is the whole point: WebView2 puts
    the file picker up from the msedgewebview2.exe browser process, not from
    the host. Enumerating our own windows finds nothing and reads exactly
    like "the picker never opened" -- measured, and wrong.
    """
    user32 = ctypes.windll.user32
    found = []
    CB = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value != "#32770":
            return True
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        title = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, 256)
        found.append((hwnd, cls.value, "%s [pid %d]" % (title.value, pid.value)))
        return True

    user32.EnumWindows(CB(cb), None)
    return found


def _close(hwnd):
    ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)      # WM_CLOSE


def run(win):
    def work():
        try:
            t0 = time.time()
            while time.time() - t0 < 40:
                r = win.evaluate_js(_READY)
                if r and json.loads(r).get("ready"):
                    break
                time.sleep(0.5)
            OUT["front_end"] = json.loads(win.evaluate_js(_READY))

            OUT["open_tab"] = win.evaluate_js(_OPEN_TAB)
            t0 = time.time()
            while time.time() - t0 < 120:                # the scan is slow
                s = json.loads(win.evaluate_js(_SURVEY))
                if s.get("stills_listed"):
                    break
                time.sleep(1.0)
            OUT["survey"] = s

            OUT["select"] = win.evaluate_js(_SELECT % json.dumps(TARGET))

            OUT["feed"] = win.evaluate_js(_FEED % json.dumps(_png()))
            t0 = time.time()
            drop = {}
            while time.time() - t0 < 300:
                drop = json.loads(win.evaluate_js(_DROP))
                if drop.get("commit") or "not" in drop["text"].lower():
                    break
                time.sleep(1.0)
            OUT["estimate"] = drop

            if drop.get("commit"):
                OUT["commit"] = win.evaluate_js(_COMMIT)
                t0 = time.time()
                while time.time() - t0 < 300:
                    drop = json.loads(win.evaluate_js(_DROP))
                    if "Written" in drop["text"] or "Failed" in drop["text"]:
                        break
                    time.sleep(1.0)
                OUT["written"] = drop["text"].strip()[:200]

            # Question 2. Clicked from a worker thread so a modal picker
            # cannot deadlock the probe: evaluate_js runs on the UI thread,
            # which the dialog owns for as long as it is up.
            before = {t for _, _, t in _dialogs()}
            done = []

            def click():
                try:
                    win.evaluate_js(
                        "document.getElementById('stillFile').click()")
                    done.append("returned")
                except Exception as ex:                    # noqa: BLE001
                    done.append("raised: %s" % ex)

            th = threading.Thread(target=click, daemon=True)
            th.start()
            time.sleep(5.0)
            dlgs = [d for d in _dialogs() if d[2] not in before]
            OUT["picker"] = {
                "opened": [t for _, _, t in dlgs],
                "click_call": done[0] if done else "still blocked (modal)",
            }
            for hwnd, _, _ in dlgs:
                _close(hwnd)
            time.sleep(2.0)
            OUT["picker"]["after_close"] = done[0] if done else "still blocked"
        except Exception as ex:                            # noqa: BLE001
            OUT["error"] = "%s: %s" % (type(ex).__name__, ex)
        print(json.dumps(OUT, indent=1))
        win.destroy()

    threading.Thread(target=work, daemon=True).start()


def main():
    win = webview.create_window("import probe", URL, width=1280, height=860)
    webview.start(run, win)
    return 0


if __name__ == "__main__":
    sys.exit(main())
