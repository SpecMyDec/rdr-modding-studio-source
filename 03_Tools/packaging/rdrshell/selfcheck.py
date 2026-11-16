"""Does the frozen build actually work, or does it merely start?

A bundler failure looks exactly like a working app right up until a route
needs a module the bundle silently dropped. So this drives the real HTTP API
of the real server inside the real .exe and checks the capability surface --
including the two things freezing is most likely to break:

    Pillow          texture / still-image import and PNG output
    WEB_ROOT        the front end being found inside the bundle

Run it against a build with:

    "RDR Modding Studio.exe" --selfcheck

Read-only: every route it calls is a GET. It never writes to the disc image.
"""

import json
import os
import sys
import time
import urllib.request


def run(server, url, out=sys.stdout):
    results = []

    def get(path, binary=False, timeout=60):
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(url + path, timeout=timeout) as r:
                body = r.read()
                code = r.status
                ctype = r.headers.get("Content-Type", "")
        except Exception as ex:                            # noqa: BLE001
            return None, 0, "", round(time.perf_counter() - t0, 3), str(ex)
        return body, code, ctype, round(time.perf_counter() - t0, 3), None

    def check(name, ok, detail=""):
        results.append({"check": name, "ok": bool(ok), "detail": detail})
        out.write("  %s  %-42s %s\n" % ("PASS" if ok else "FAIL", name, detail))
        return ok

    out.write("\nself-check against %s\n" % url)

    # --- the front end is inside the bundle
    body, code, ctype, ms, err = get("")
    check("front end: index.html", code == 200 and body and len(body) > 40000,
          "%d KB" % (len(body or b"") / 1024))
    body, code, ctype, ms, err = get("app.js")
    check("front end: app.js", code == 200 and len(body or b"") > 300000,
          "%d KB in %.0f ms" % (len(body or b"") / 1024, ms * 1000))
    body, code, ctype, ms, err = get("three.module.js")
    check("front end: vendored three.js", code == 200 and len(body or b"") > 1000000,
          "%d KB" % (len(body or b"") / 1024))

    # --- every front-end module in the bundle is actually reachable.
    #
    # New UI code is loaded with dynamic import() inside try/catch, which is
    # the right way round for robustness and the wrong way round for noticing:
    # a module the bundle dropped disables its feature and logs to a console
    # nobody has open. So the bundled web folder is enumerated and every file
    # in it is fetched. Whatever the front end grows next is covered without
    # this list being edited.
    from rdrshell import paths as _paths
    web = os.path.join(_paths.resource_dir(), "web")
    served, missing = 0, []
    if os.path.isdir(web):
        for f in sorted(os.listdir(web)):
            if not f.endswith((".js", ".css", ".html")):
                continue
            b, c, _, _, _ = get(f)
            if c == 200 and b:
                served += 1
            else:
                missing.append(f)
    check("every bundled front-end file is served", bool(served) and not missing,
          "%d served%s" % (served, "; MISSING " + ", ".join(missing)
                           if missing else ""))

    # --- the toolkit can read the disc
    body, code, ctype, ms, err = get("api/config")
    cfg = json.loads(body) if body else {}
    check("api/config answers", code == 200, "%.0f ms" % (ms * 1000))
    check("working ISO is the per-user copy",
          "AppData" in str(cfg.get("modded", "")) or
          "RDR_WORKING" in str(cfg.get("modded", "")),
          str(cfg.get("modded", ""))[:70])

    body, code, ctype, ms, err = get("api/archives")
    n = _count(body, "archives")
    # At least the 66 the game ships: a working disc with new levels (the Data
    # Editor's New .dat, an imported .dat) has more, and is not broken for it.
    check("api/archives: the game's 66 (or more)", isinstance(n, int) and n >= 66, "got %s" % n)

    body, code, ctype, ms, err = get("api/levels")
    n = _count(body, "levels")
    check("api/levels: the campaign is there", n and n >= 40,
          "%s levels, %.0f ms" % (n, ms * 1000))

    body, code, ctype, ms, err = get("api/creatures")
    check("api/creatures", code == 200, "%.0f ms" % (ms * 1000))

    body, code, ctype, ms, err = get("api/guns")
    check("api/guns", code == 200, "%.0f ms" % (ms * 1000))

    body, code, ctype, ms, err = get("api/stills")
    stills = json.loads(body) if body and code == 200 else {}
    names = stills.get("stills") or stills.get("items") or []
    check("api/stills lists the .ipu screens", code == 200 and len(names) > 0,
          "%d stills" % len(names))

    # --- Pillow: the module PyInstaller is most likely to have dropped
    try:
        from PIL import Image                              # noqa: F401
        import PIL
        check("Pillow is in the bundle", True, "version %s" % PIL.__version__)
    except Exception as ex:                                # noqa: BLE001
        check("Pillow is in the bundle", False, str(ex))

    # and Pillow used through the real route that needs it
    if names:
        first = names[0]
        name = first.get("name") if isinstance(first, dict) else first
        body, code, ctype, ms, err = get(
            "api/still_png?name=" + urllib.parse.quote(str(name)), timeout=120)
        ok = code == 200 and ctype.startswith("image/png") and body[:8] == b"\x89PNG\r\n\x1a\n"
        check("still_png decodes a .ipu to PNG via Pillow", ok,
              "%s -> %d KB in %.1f s" % (name, len(body or b"") / 1024, ms))

    # --- ROADMAP.md: present in internal, and ABSENT in release
    #
    # This used to assert "bundled" unconditionally, which was right when every
    # build had the Roadmap tab and wrong the moment one did not. The release
    # edition strips the tab, so the honest check is edition-shaped: the file
    # must be there when something reads it, and must NOT be there when nothing
    # does -- it is internal notes, and it shipped to the public once already.
    #
    # Asserting absence is the more valuable half. A missing data file for a
    # missing tab is invisible; a private document in a stranger's download is
    # not, and this is the check that would have caught it.
    edition = _edition()
    body, code, ctype, ms, err = get("api/roadmap")
    r = json.loads(body) if body else {}
    if edition == "release":
        check("ROADMAP.md is NOT in the public build", r.get("ok") is not True,
              "api/roadmap: %s" % (r.get("error") or "unavailable, as intended"))
    else:
        check("ROADMAP.md is bundled (Roadmap tab)", r.get("ok") is True,
              "%s bytes" % r.get("bytes", r.get("error", "")))

    # --- FIRST RUN, which is the only run a new user ever has.
    #
    # This check exists because its absence shipped a broken download. The
    # build reported 13/13 and started perfectly on the machine that made it
    # -- that machine already had settings, so it never took the first-run
    # branch at all. Every fresh install died on a missing firstrun.html with
    # a traceback dialog and no window.
    #
    # A self-check that only walks the path the developer happens to be on is
    # worse than no self-check, because it certifies the build.
    try:
        from rdrshell.shell import _firstrun_html
        p = _firstrun_html()
        sz = os.path.getsize(p)
        check("first-run page resolves (frozen or not)", sz > 0,
              "%d bytes" % sz)
    except Exception as ex:                                # noqa: BLE001
        check("first-run page resolves (frozen or not)", False, str(ex))

    # --- first run's disc test, run against the disc this install is using.
    # It is the gate every new user meets, it is the only thing standing
    # between "wrong file" and a confusing failure ten minutes later, and it
    # costs under a second. Read-only: Game(writable=False).
    try:
        from rdrshell import verify as _verify
        from rdrstudio import config as _cfg
        t0 = time.perf_counter()
        v = _verify.identify(_cfg.VANILLA_ISO)
        check("disc identification says SLUS-20500",
              v.get("boot", "").upper().startswith("SLUS_205"),
              "%s, %s archives, %.2f s" % (v.get("boot"), v.get("archives"),
                                           time.perf_counter() - t0))
    except Exception as ex:                                # noqa: BLE001
        check("disc identification says SLUS-20500", False, str(ex))

    # --- the window can exist at all. Not a rendering test (there is no window
    # here); just the runtime the window needs, named rather than assumed.
    try:
        from rdrshell import webview2
        v = webview2.version()
        check("Edge WebView2 runtime is installed", bool(v), v or
              "MISSING -- the app would open a blank window on this machine")
    except Exception as ex:                                # noqa: BLE001
        check("Edge WebView2 runtime is installed", False, str(ex))

    # --- the emulator hand-off has something to hand off to
    try:
        from rdrshell import pcsx2
        found = pcsx2.find()
        check("PCSX2 auto-detect runs", True,
              found or "not installed here (Play/Boot test stay off)")
    except Exception as ex:                                # noqa: BLE001
        check("PCSX2 auto-detect runs", False, str(ex))

    # --- the window is allowed to save files
    #
    # pywebview defaults ALLOW_DOWNLOADS to False and WebView2 enforces it by
    # cancelling the download outright, so every export in the desktop build
    # produced a success toast and no file. Nothing caught it: the server
    # answered, the bytes were correct, and the failure happened in the browser
    # engine after the front end had already declared victory.
    #
    # Asserting the flag is not the same as exercising an export -- that needs
    # a real window and a real Save dialog -- but it is the difference between
    # a regression that is noticed at build time and one a user reports weeks
    # later as "where do the files go?"
    try:
        import webview as _wv
        allowed = _wv.settings.get("ALLOW_DOWNLOADS")
        check("the window may save files (exports work)", allowed is True,
              "ALLOW_DOWNLOADS=%r" % (allowed,))
    except Exception as ex:                                # noqa: BLE001
        check("the window may save files (exports work)", False, str(ex))

    # --- and the window remembers preferences between launches
    #
    # private_mode=True throws localStorage away on exit, so grid, HUD state,
    # legend, nav position, outliner mode and every folded panel silently
    # reset every launch. Same shape of defect as the downloads one: a
    # browser default that is wrong for an application, invisible because
    # forgetting looks like never having been asked.
    try:
        from rdrshell.shell import _persist
        kw = _persist()
        check("the window remembers preferences between launches",
              kw.get("private_mode") is False and bool(kw.get("storage_path")),
              "private_mode=%r storage=%s" % (kw.get("private_mode"),
                                              kw.get("storage_path") or "none"))
    except Exception as ex:                                # noqa: BLE001
        check("the window remembers preferences between launches", False,
              str(ex))

    # --- the paperwork the disclaimer promises is beside the exe
    root = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) \
        else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    lic = os.path.join(root, "licenses")
    n_lic = len(os.listdir(lic)) if os.path.isdir(lic) else 0
    check("third-party licences are in the build", n_lic > 3,
          "%d files in licenses\\" % n_lic)

    passed = sum(1 for r in results if r["ok"])
    out.write("\n%d/%d checks passed\n" % (passed, len(results)))
    return results, passed == len(results)


def _app_root():
    """The folder holding the exe (frozen) or the packaging dir (source)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _edition():
    """"release" / "internal", from the EDITION.json stamped in _internal/.

    Defaults to "internal" when the stamp is missing, which is what a developer
    running from source is. The default deliberately favours the stricter path:
    an unstamped build is checked as though it should carry everything, so a
    stamp that failed to write shows up as a failure rather than as a build
    that quietly skips its checks.
    """
    # The stamp lives in _internal/ since 1.4.0's slimmer folder; the old
    # spot beside the exe is still read so an older build checks the same.
    for path in (os.path.join(_app_root(), "_internal", "EDITION.json"),
                 os.path.join(_app_root(), "EDITION.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                return (json.load(fh).get("edition") or "internal").lower()
        except Exception:                                      # noqa: BLE001
            continue
    return "internal"


def _count(body, key):
    if not body:
        return None
    try:
        j = json.loads(body)
    except Exception:                                      # noqa: BLE001
        return None
    v = j.get(key, j) if isinstance(j, dict) else j
    return len(v) if hasattr(v, "__len__") else None


import urllib.parse                                        # noqa: E402
