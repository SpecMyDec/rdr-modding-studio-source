"""The desktop application: one window, and what has to happen before it.

    first run   disclaimer -> pick ISO -> verify -> copy -> Studio
    every run   Studio, in a native window, on a port nobody else holds

The Studio front end is unchanged and unaware it is in a window rather than a
browser tab. That is the point: this file can be deleted and `python
RDR_Studio.py` still works exactly as before.
"""

import time

# The first line of our own code to run. Everything before it -- the
# PyInstaller bootloader unpacking the archive, the interpreter starting -- is
# invisible from inside the process, so the launcher records its own clock and
# subtracts. Must stay at the top of the file to mean anything.
ENTRY_EPOCH = time.time()

import os                                                  # noqa: E402
import shutil                                              # noqa: E402
import sys                                                 # noqa: E402
import threading                                           # noqa: E402
import traceback                                           # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
if PKG not in sys.path:
    sys.path.insert(0, PKG)

from rdrshell import paths, pcsx2, verify, webview2        # noqa: E402
from rdrshell.server import StudioServer, add_toolkit_to_path  # noqa: E402
from rdrshell.dpi import make_dpi_aware                    # noqa: E402

# MUST run before pywebview is imported. Importing it starts pythonnet and the
# .NET WinForms host, and whichever of those touches the display first freezes
# the process's DPI awareness for good. Called afterwards it silently does
# nothing -- measured: the window still came back 1125 physical pixels wide for
# a 900-pixel request, i.e. still being stretched 1.25x by the compositor.
DPI_MODE = make_dpi_aware()


def _clear_mark_of_the_web():
    """Remove Windows' download mark from our own .NET assemblies.

    MUST run before pywebview is imported, for the same reason the DPI call
    above must: importing it starts pythonnet, which loads Python.Runtime.dll
    through .NET -- and .NET refuses to load an assembly carrying Mark of the
    Web.

    Windows stamps every file extracted from a downloaded zip with a
    `Zone.Identifier` stream saying it came from the internet. So the exact
    sequence a new user performs -- download the zip, extract it, run the exe
    -- produced a traceback dialog and no window:

        RuntimeError: Failed to resolve Python.Runtime.Loader.Initialize
                      from ...\\_internal\\pythonnet\\runtime\\Python.Runtime.dll

    Every download was broken. Every run on this machine was fine, because a
    build folder is not downloaded and so is never marked -- which is why the
    self-check, the window check and the WebGL check all passed on a build no
    stranger could start. It is the same blind spot that once shipped a missing
    firstrun.html: a test that only walks the developer's own path certifies
    the build instead of checking it.

    Scope is deliberately narrow -- `.dll` only, under our own `_internal`, and
    only in the two packages whose DLLs .NET loads. The bundle's other ~90
    marked files are Python, HTML and text, and nothing checks the mark on
    those. Measured: unblocking ONLY these took a downloaded copy from
    crash-on-start to 19/19 self-checks with 93 files still marked. Clearing
    what blocks startup is the job; clearing everything is not.

    Silent on failure by design. Extracted somewhere unwritable, the marks stay
    and the old error returns -- no worse than today, and the README says to
    extract somewhere writable.
    """
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return 0
    root = os.path.join(os.path.dirname(sys.executable), "_internal")
    cleared = 0
    for package in ("pythonnet", "webview"):
        for base, _, files in os.walk(os.path.join(root, package)):
            for name in files:
                if not name.lower().endswith(".dll"):
                    continue
                try:
                    os.remove(os.path.join(base, name) + ":Zone.Identifier")
                    cleared += 1
                except OSError:
                    pass                    # not marked, or we cannot write
    return cleared


UNBLOCKED = _clear_mark_of_the_web()

import webview                                            # noqa: E402

# Let the window save files. pywebview ships `ALLOW_DOWNLOADS: False`, and the
# WebView2 backend enforces it with `args.Cancel = True` the moment a download
# starts -- so in the desktop build EVERY export was cancelled by the browser
# engine: BVH to Blender, level and character OBJ, texture PNGs, raw file
# export, saving a .rdrmod.json.
#
# It failed in the worst possible way, silently and while claiming success. The
# front end exports through one helper that fetches the bytes, builds a blob and
# clicks a hidden <a download>. The fetch succeeds, so the helper returns true
# and the toast says "Edit it in Blender, then Import .bvh to bring it back" --
# and no file exists. A tester asked where the exports were saved, which is what
# a person does when the tool says it saved something and nothing is there.
#
# This is the browser-tab-to-desktop-app assumption showing up a third time,
# after the missing firstrun.html and Mark of the Web. Serving the same HTML in
# a window does not make the window a browser: downloads, DPI and file marking
# are all things the tab did for us that the window does not.
#
# With it on, WebView2 opens a native Save dialog defaulting to Downloads --
# which is better desktop behaviour than a browser's silent save anyway.
webview.settings["ALLOW_DOWNLOADS"] = True

# Links that are meant to leave the app should leave it. Default is already
# True; pinned because a future default flip would turn every documentation
# link into a navigation that replaces the Studio with a web page and no way
# back -- the window has no address bar.
webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True

CHUNK = 8 << 20
IMPORTS_EPOCH = time.time()
TIMINGS = {"entry_epoch": ENTRY_EPOCH,
           "own_imports": round(IMPORTS_EPOCH - ENTRY_EPOCH, 3)}


def _t(name, t0):
    TIMINGS[name] = round(time.perf_counter() - t0, 3)


# --------------------------------------------------------------------- api

class FirstRunApi:
    """Everything the first-run page is allowed to ask Python to do.

    Deliberately small. The page cannot name an arbitrary path to read or
    write -- it can only ask for a file dialog, and the user picks.
    """

    def __init__(self, settings, window_ref, on_done):
        self.s = settings
        self.window = window_ref
        self.on_done = on_done

    # ---- step 1
    def accept_disclaimer(self):
        self.s["accepted_disclaimer"] = True
        paths.save(self.s)
        return True

    # ---- step 2
    def pick_iso(self):
        res = self.window[0].create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=False,
            file_types=("Disc image (*.iso)", "All files (*.*)"))
        if not res:
            return None
        return res[0] if isinstance(res, (list, tuple)) else res

    def verify(self, path):
        try:
            return dict(verify.identify(path))
        except Exception as ex:                            # noqa: BLE001
            return {"verdict": "reject", "confidence": "measured",
                    "message": "Could not read that file: %s" % ex}

    def checksum(self, path):
        def prog(done, total):
            pct = 100.0 * done / total
            self._push(pct, "checking %.0f%%" % pct)
        try:
            return dict(verify.checksum(path, prog))
        except Exception as ex:                            # noqa: BLE001
            return {"verdict": "unsure", "confidence": "measured",
                    "message": "Checksum failed: %s" % ex}

    # ---- step 3
    def suggest_work_dir(self, iso):
        """Default to the drive the ISO is already on.

        A 2.2 GB file has no business landing on a full C: when the user's
        game library clearly lives somewhere else. LOCALAPPDATA is only the
        fallback for when that drive has no room.
        """
        near = os.path.join(os.path.dirname(iso), "RDR Modding Studio")
        free_near = _free(os.path.dirname(iso))
        if free_near > 6 << 30:
            return self._work_info(near)
        return self._work_info(os.path.join(paths.APP_DIR, "working"))

    def pick_work_dir(self, iso=None):
        res = self.window[0].create_file_dialog(webview.FOLDER_DIALOG)
        if not res:
            return None
        d = res[0] if isinstance(res, (list, tuple)) else res
        return self._work_info(os.path.join(d, "RDR Modding Studio"), iso)

    def _work_info(self, d, iso=None):
        p = os.path.join(d, "RDR_WORKING.iso")
        free = _free(_existing_parent(d))
        info = {"dir": d, "path": p,
                "free": "%.1f GB free on %s -- 2.2 GB needed"
                        % (free / (1 << 30), os.path.splitdrive(d)[0] or d)}
        # A working copy already sitting here, the same size as the disc image
        # being pointed at, is almost certainly the one this app made earlier.
        #
        # This matters because setup runs again whenever the ORIGINAL .iso
        # goes missing -- paths.configured() requires it -- and the README
        # actively encourages keeping the original on another drive. Unplug
        # that drive, or rename the file, and the next launch dropped the user
        # back on the disclaimer with no explanation and copied another
        # 2.2 GB over a perfectly good copy. Offer to keep it instead.
        info["reusable"] = False
        try:
            if iso and os.path.isfile(p) and os.path.isfile(iso) \
                    and os.path.getsize(p) == os.path.getsize(iso):
                info["reusable"] = True
                info["reuse_note"] = (
                    "There is already a working copy here, exactly the size of "
                    "your disc image -- almost certainly the one this tool made "
                    "before. You can keep it and skip the 2.2 GB copy.")
        except OSError:
            pass
        return info

    def begin_copy(self, iso, work_dir, reuse=False):
        threading.Thread(target=self._copy, args=(iso, work_dir, bool(reuse)),
                         daemon=True).start()
        return True

    def _adopt(self, iso, dst):
        """Keep a working copy that is already there, instead of remaking it."""
        self.s["vanilla_iso"] = iso
        self.s["working_iso"] = dst
        paths.ensure_dirs(self.s)
        paths.save(self.s)
        self._push(100, "kept the working copy already on disk -- "
                        "nothing was copied")
        try:
            self.window[0].evaluate_js("window.copyDone && window.copyDone()")
        except Exception:                                  # noqa: BLE001
            self.on_done()

    def _copy(self, iso, work_dir, reuse=False):
        dst = os.path.join(work_dir, "RDR_WORKING.iso")
        try:
            if reuse and os.path.isfile(dst) and os.path.isfile(iso) \
                    and os.path.getsize(dst) == os.path.getsize(iso):
                return self._adopt(iso, dst)
            os.makedirs(work_dir, exist_ok=True)
            total = os.path.getsize(iso)
            done = 0
            t0 = time.perf_counter()
            last = 0.0
            with open(iso, "rb") as fi, open(dst + ".part", "wb") as fo:
                while True:
                    buf = fi.read(CHUNK)
                    if not buf:
                        break
                    fo.write(buf)
                    done += len(buf)
                    now = time.perf_counter()
                    if now - last > 0.25:
                        last = now
                        pct = 100.0 * done / total
                        rate = done / (now - t0) / 1048576.0
                        eta = (total - done) / max(done / (now - t0), 1)
                        self._push(pct, "%.0f%%  --  %.0f MB/s, about %ds left"
                                        % (pct, rate, eta))
            os.replace(dst + ".part", dst)   # only a whole file gets the name
            secs = time.perf_counter() - t0
            TIMINGS["first_run_copy"] = round(secs, 1)
            self._push(100, "copied in %.0f s" % secs)

            self.s["vanilla_iso"] = iso
            self.s["working_iso"] = dst
            paths.ensure_dirs(self.s)
            paths.save(self.s)
            # The page moves on to step 4 (the emulator) and calls finish()
            # when the user is done with it. The copy no longer opens the
            # Studio itself, because there is a question left to ask.
            try:
                self.window[0].evaluate_js("window.copyDone && window.copyDone()")
            except Exception:                              # noqa: BLE001
                self.on_done()
        except Exception as ex:                            # noqa: BLE001
            # This used to print a raw Python traceback into the progress
            # label and leave Back, Choose folder and Make the copy all
            # disabled -- no way forward and no way back except killing the
            # process. Disk full, a permissions refusal, or a USB drive pulled
            # mid-copy are all ordinary things, and this is the first five
            # minutes of a stranger's experience.
            try:
                if os.path.exists(dst + ".part"):
                    os.remove(dst + ".part")               # don't strand GBs
            except Exception:                              # noqa: BLE001
                pass
            self._push(-1, self._copy_error(ex, iso, work_dir))

    @staticmethod
    def _copy_error(ex, iso, work_dir):
        """Say what went wrong in words the reader can act on."""
        if isinstance(ex, PermissionError):
            return ("Windows would not let the tool write into that folder.\n"
                    "Pick somewhere under your own user folder -- Documents "
                    "or Desktop -- rather than Program Files or a drive root.")
        if isinstance(ex, FileNotFoundError):
            if not os.path.isfile(iso):
                return ("Your disc image is no longer where it was:\n%s\n"
                        "If it is on a USB or external drive, reconnect it "
                        "and go Back to point at it again." % iso)
            return ("That folder disappeared while copying:\n%s\n"
                    "Choose another one." % work_dir)
        if isinstance(ex, OSError) and getattr(ex, "errno", None) == 28:
            return ("The drive ran out of space. The copy needs about 2.2 GB "
                    "free.\nChoose a folder on a drive with more room.")
        msg = str(ex).strip() or ex.__class__.__name__
        return ("The copy did not finish: %s\n"
                "Choose another folder and try again." % msg)

    # ---- step 4 (skippable): the emulator
    def find_pcsx2(self):
        """Guess, once, before asking. Costs a few isfile() calls."""
        stored = self.s.get("pcsx2_exe") or ""
        if stored and os.path.isfile(stored):
            return pcsx2.describe(stored)
        return pcsx2.describe(pcsx2.find())

    def pick_pcsx2(self):
        res = self.window[0].create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=False,
            file_types=("PCSX2 (*.exe)", "All files (*.*)"))
        if not res:
            return None
        p = res[0] if isinstance(res, (list, tuple)) else res
        return pcsx2.describe(p)

    def set_pcsx2(self, path):
        self.s["pcsx2_exe"] = path or ""
        paths.save(self.s)
        return True

    def finish(self):
        """Leave first run and open the Studio. Called after step 4, whether
        the user pointed at PCSX2 or skipped it."""
        self.on_done()
        return True

    def _push(self, pct, text):
        try:
            self.window[0].evaluate_js(
                "window.copyProgress && window.copyProgress(%.2f, %s)"
                % (pct, _js_str(text)))
        except Exception:                                  # noqa: BLE001
            pass


def _persist():
    """webview.start kwargs that let the front end REMEMBER things.

    pywebview starts in `private_mode=True`, which is an incognito window:
    localStorage is discarded when the process exits. The Studio keeps real
    preferences there -- grid on/off, the HUD's expanded state, the legend,
    which nav group you had open, the outliner mode, and every folded panel
    heading -- so in the desktop build all of it silently reset on every
    launch, while the same front end in a browser tab remembered fine.

    Nobody reported it as a bug because forgetting looks like never having
    been asked. It is the quietest member of the same family as the cancelled
    downloads: a browser default that is wrong for an app.

    Storage goes beside the settings file rather than into a temp dir, so it
    survives updates and is removed when a user deletes the app folder.
    """
    store = os.path.join(paths.APP_DIR, "webview")
    try:
        os.makedirs(store, exist_ok=True)
    except OSError:
        return {}                       # unwritable: incognito beats crashing
    return {"private_mode": False, "storage_path": store}


class AppApi:
    """What the RUNNING Studio window may ask of the shell.

    FirstRunApi is the setup page's allowance; this is every run after it.
    Same rule and same reason: the front end can never name an arbitrary path
    to read or write -- it can only ask for a native dialog, and the user
    picks. It exists because two things genuinely cannot be done from inside
    the page: opening a real file dialog (an <input type=file> hides the real
    path on purpose), and re-pointing the app at different discs, which is a
    settings-file change the server must not expose over HTTP -- the Studio
    can be served on a LAN, and "change which file on my machine you write
    to" is not something a network peer may ask.

    Switching discs RESTARTS the app rather than hot-swapping. The Studio
    holds open handles, a per-disc journal and per-disc caches; a live swap
    would have to chase all of that state, and a missed cache is an edit
    written to the wrong disc. A restart is two seconds and cannot be
    half-done.
    """

    def __init__(self, s, window_ref, server):
        self.s = s
        self.window = window_ref
        self.server = server

    # ---- read side: what the settings panel shows
    def app_settings(self):
        v = self.s.get("vanilla_iso") or ""
        w = self.s.get("working_iso") or ""
        p = self.s.get("pcsx2_exe") or ""
        return {
            "vanilla": v, "vanilla_ok": os.path.isfile(v),
            "working": w, "working_ok": os.path.isfile(w),
            "pcsx2": p, "pcsx2_ok": bool(p) and os.path.isfile(p),
            "known_working": [x for x in self.s.get("known_working", [])
                              if os.path.isfile(x)],
        }

    # ---- pcsx2, same three verbs the setup page has
    def find_pcsx2(self):
        stored = self.s.get("pcsx2_exe") or ""
        if stored and os.path.isfile(stored):
            return pcsx2.describe(stored)
        return pcsx2.describe(pcsx2.find())

    def pick_pcsx2(self):
        res = self.window[0].create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=False,
            file_types=("PCSX2 (*.exe)", "All files (*.*)"))
        if not res:
            return None
        p = res[0] if isinstance(res, (list, tuple)) else res
        return pcsx2.describe(p)

    def set_pcsx2(self, path):
        self.s["pcsx2_exe"] = path or ""
        paths.save(self.s)
        # Live: Play/Boot work on the next click, no restart needed --
        # the emulator is launched per use, nothing holds state on it.
        try:
            if self.server and self.server.studio:
                self.server.studio.pcsx2 = path or None
        except Exception:                                  # noqa: BLE001
            pass
        return self.app_settings()

    # ---- discs
    def pick_iso(self, purpose):
        """Choose an ISO for `purpose`: 'working' or 'vanilla'.

        Returns a verdict dict; nothing is saved until switch_iso. A working
        candidate may be ALREADY MODDED -- people bring discs from other
        tools -- so identity is checked (is this Red Dead Revolver at all)
        but byte-pristineness deliberately is not. The vanilla slot is the
        restore source and diff baseline, so it gets the strict check.
        """
        res = self.window[0].create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=False,
            file_types=("PS2 disc image (*.iso)", "All files (*.*)"))
        if not res:
            return None
        p = res[0] if isinstance(res, (list, tuple)) else res
        v = dict(verify.identify(p))
        v["path"] = p
        if purpose == "working":
            if os.path.normcase(p) == os.path.normcase(
                    self.s.get("vanilla_iso") or ""):
                v["verdict"] = "reject"
                v["message"] = ("That is the registered vanilla disc. It is "
                                "the pristine restore source and is never "
                                "edited -- pick a copy of it instead.")
            elif v.get("verdict") == "reject":
                pass                       # not this game at all: stays a no
            elif v.get("modified"):
                v["verdict"] = "accept"
                v["message"] = ("This image differs from stock -- fine if it "
                                "is a modded copy you mean to keep working "
                                "on. 'What's changed' will diff it against "
                                "YOUR registered vanilla, so foreign edits "
                                "show up as changes.")
        return v

    def switch_iso(self, purpose, path):
        """Persist the chosen disc and restart into it."""
        if not path or not os.path.isfile(path):
            return {"ok": False, "error": "file does not exist"}
        key = "vanilla_iso" if purpose == "vanilla" else "working_iso"
        old = self.s.get("working_iso")
        self.s[key] = path
        if purpose == "working":
            known = [x for x in self.s.get("known_working", []) if x]
            for candidate in (old, path):
                if candidate and candidate not in known:
                    known.append(candidate)
            self.s["known_working"] = known[-8:]
        paths.save(self.s)
        return {"ok": True, "restart": True}

    def restart_app(self):
        """Relaunch this exe and close this window. The saved settings are
        read fresh on the way up, which is the entire point."""
        import subprocess
        if getattr(sys, "frozen", False):
            subprocess.Popen([sys.executable])
        else:
            subprocess.Popen([sys.executable] + sys.argv)
        try:
            self.window[0].destroy()
        except Exception:                                  # noqa: BLE001
            os._exit(0)
        return True


class FirstRunThenApp(FirstRunApi):
    """Both surfaces, because the setup window BECOMES the Studio window.

    pywebview binds `js_api` once, at create_window. On a first run the setup
    page is created with FirstRunApi, and when setup finishes open_studio()
    swaps that same window to the Studio URL -- so the Studio came up with
    only the setup allowance attached, and every AppApi call was undefined for
    the rest of the session.

    That is squarely on the new user's path, not an edge case. Setup's last
    screen invites skipping PCSX2 with "you can set it up later from App
    setup, the gear button in the Studio's top bar", and clicking Play opens
    exactly that panel -- which then throws on app_settings() after it has
    already shown itself. Restarting the app fixed it, which is why it
    survived: it only ever reproduces on the very first launch.

    The four verbs both classes define (pick_iso, pick_pcsx2, find_pcsx2,
    set_pcsx2) stay with FirstRunApi. They open the same dialogs and write the
    same settings keys, and inheriting rather than overriding keeps setup's
    behaviour byte-for-byte what it was.
    """

    def __init__(self, s, window_ref, on_done, server):
        super().__init__(s, window_ref, on_done)
        self._app = AppApi(s, window_ref, server)

    def app_settings(self):
        return self._app.app_settings()

    def switch_iso(self, *a, **kw):
        return self._app.switch_iso(*a, **kw)

    def restart_app(self, *a, **kw):
        return self._app.restart_app(*a, **kw)


def _js_str(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') \
                  .replace("\n", "\\n") + '"'


def _free(path):
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return 0


def _existing_parent(d):
    while d and not os.path.isdir(d):
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return d or "C:\\"


def _firstrun_html():
    """Locate firstrun.html, frozen or not.

    This was `os.path.join(HERE, "firstrun.html")` and it crashed EVERY first
    run of the packaged app -- which is every run a new user will ever have,
    so the download was unusable while working perfectly on the machine that
    built it (that machine already had settings and never took this branch).

    The cause: HERE is derived from __file__, and PyInstaller runs this module
    as __main__ rather than as rdrshell.shell, so __file__ loses its package
    folder. HERE becomes _internal, the file is at _internal/rdrshell, and the
    open() fails with a traceback dialog and no window.

    Every candidate is tried and the failure names all of them, because a
    resource that resolves differently frozen vs unfrozen is exactly the class
    of bug that only shows up on someone else's computer.
    """
    from rdrshell import paths as _p
    res = _p.resource_dir()
    tried = [
        os.path.join(HERE, "firstrun.html"),                 # source layout
        os.path.join(HERE, "rdrshell", "firstrun.html"),     # frozen: __main__
        os.path.join(res, "rdrshell", "firstrun.html"),      # frozen: _MEIPASS
        os.path.join(res, "firstrun.html"),
    ]
    for p in tried:
        if os.path.isfile(p):
            return p
    raise FileNotFoundError(
        "firstrun.html is missing from the application. Looked in:\n  "
        + "\n  ".join(tried))


# -------------------------------------------------------------------- main

def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    t_boot = time.perf_counter()

    TIMINGS["dpi_awareness"] = DPI_MODE

    res = paths.resource_dir()
    add_toolkit_to_path(res)

    s = paths.load()
    paths.ensure_dirs(s)

    if "--reset" in argv:
        s["vanilla_iso"] = ""
        s["accepted_disclaimer"] = False
        paths.save(s)

    if "--selfcheck" in argv:
        return _selfcheck(s, res, argv)

    # Before any window is created. A missing WebView2 runtime is the one
    # failure that produces a blank rectangle instead of an error, and it is
    # the likeliest failure on the older Windows 10 machines that still have a
    # PS2 emulator on them.
    TIMINGS["webview2"] = webview2.version() or ""
    if webview2.preflight() is None:
        return 3

    server = StudioServer()
    window_ref = []

    def open_studio():
        """Boot the Studio and point the (already open) window at it."""
        t0 = time.perf_counter()
        url = server.start(s, res)
        server.wait_ready()
        _t("server_start", t0)
        window_ref[0].load_url(url)
        window_ref[0].set_title("RDR Modding Studio")

        # --open-tab "3D Editor": for build screenshots. Clicks a nav button by
        # its visible label once the front end has settled. Not a user feature.
        tab = _arg_value(argv, "--open-tab")
        if tab and "--verify-window" not in argv:   # the verifier does its own
            def click():
                time.sleep(5.0)
                try:
                    window_ref[0].evaluate_js(
                        "(function(){var b=[].slice.call("
                        "document.querySelectorAll('nav button'))"
                        ".find(function(x){return x.textContent.trim()===%s});"
                        "if(b){b.click();return 'clicked'}return 'not found'})()"
                        % _js_str(tab))
                except Exception:                          # noqa: BLE001
                    pass
            threading.Thread(target=click, daemon=True).start()

    if paths.configured(s):
        # Normal launch. Create the window on a blank page, then swap it to the
        # Studio the moment the socket answers -- so the window appears
        # immediately instead of after the backend is up.
        win = webview.create_window(
            "RDR Modding Studio", html=_SPLASH,
            width=s["window"]["width"], height=s["window"]["height"],
            min_size=(1100, 700),
            # The running Studio's line to the shell: native file dialogs and
            # disc/emulator re-pointing. See AppApi for why this cannot be an
            # HTTP route.
            js_api=AppApi(s, window_ref, server))
        window_ref.append(win)

        def boot(_w):
            TIMINGS["window_shown"] = round(time.perf_counter() - t_boot, 3)
            open_studio()
            TIMINGS["total_to_studio"] = round(time.perf_counter() - t_boot, 3)
            if "--verify-window" in argv:
                _verify_window(window_ref[0], argv)
            elif "--timings" in argv or _arg_value(argv, "--timings-file"):
                _report(window_ref[0], argv)

        webview.start(boot, win, debug="--debug" in argv, **_persist())
    else:
        with open(_firstrun_html(), encoding="utf-8") as fh:
            html = fh.read()
        api = FirstRunThenApp(s, window_ref, lambda: open_studio(), server)
        # 900 wide, not 820: the card is 760 plus 60 of padding, and Windows
        # counts the frame in the size it gives you, so 820 clipped the right
        # edge of every paragraph. Measured, not guessed.
        win = webview.create_window("RDR Modding Studio -- setup", html=html,
                                    width=900, height=820, js_api=api)
        window_ref.append(win)
        if "--verify-firstrun" in argv:
            webview.start(lambda _w: _verify_firstrun(win, argv), win,
                          debug="--debug" in argv)
        else:
            webview.start(debug="--debug" in argv)

    server.stop()
    if TIMINGS:
        import json
        blob = json.dumps(TIMINGS, indent=1)
        print(blob)
        # A windowed (console=False) exe has nowhere to print, so the build
        # check reads the numbers back out of a file instead.
        out = _arg_value(argv, "--timings-file")
        if out:
            try:
                with open(out, "w", encoding="utf-8") as fh:
                    fh.write(blob)
            except OSError:
                pass
    return 0


def _arg_value(argv, flag):
    for i, a in enumerate(argv):
        if a == flag and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith(flag + "="):
            return a.split("=", 1)[1]
    return None


def _selfcheck(s, res, argv):
    """Drive the real API inside this build, with no window. Writes its report
    to --out as well as stdout, because a windowed .exe has no stdout."""
    import io

    from rdrshell import selfcheck

    if not paths.configured(s):
        print("not set up yet -- run the app once first")
        return 2

    buf = io.StringIO()
    srv = StudioServer()
    t0 = time.perf_counter()
    url = srv.start(s, res)
    srv.wait_ready()
    buf.write("server up in %.3f s on %s\n" % (time.perf_counter() - t0, url))
    try:
        results, ok = selfcheck.run(srv, url, buf)
    finally:
        srv.stop()

    text = buf.getvalue()
    print(text)
    out = _arg_value(argv, "--out")
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(text)
    return 0 if ok else 1


def _verify_window(win, argv):
    """The make-or-break test: does WebGL actually work in the packaged window?

    Everything else about this build can be checked headlessly. This cannot --
    a frozen app draws through WebView2, which uses whatever the machine's Edge
    runtime and GPU driver give it, and no amount of reading Python source says
    whether a triangle appears. So the real bundled window is opened, the
    bundled copy of three.js is imported into it, a cube is rendered, and the
    pixels are read back. Non-black pixels or it did not happen.

    Writes JSON to --out and quits. --hold N keeps the window alive N seconds
    first, which is how an external screenshot gets taken of the same window
    this probe just measured.
    """
    import json

    out_path = _arg_value(argv, "--out") or os.path.join(
        os.path.expanduser("~"), "rdr_window_check.json")
    hold = float(_arg_value(argv, "--hold") or 0)
    tabs = (_arg_value(argv, "--open-tab") or
            "Build,3D Editor,LEVEL,Level").split(",")

    def run():
        result = {"ok": False, "stage": "start"}
        try:
            # 1. wait for the front end to settle. Polling beats a fixed sleep:
            #    a slow first paint is not a failure, it is a slow first paint.
            t0 = time.perf_counter()
            ready = None
            while time.perf_counter() - t0 < 40:
                try:
                    ready = win.evaluate_js(_READY)
                except Exception:                          # noqa: BLE001
                    ready = None
                if ready and json.loads(ready).get("ready"):
                    break
                time.sleep(0.5)
            result["front_end"] = json.loads(ready) if ready else {}
            result["front_end_seconds"] = round(time.perf_counter() - t0, 2)

            # 2. the WebGL + three.js probe, kicked off and then polled,
            #    because evaluate_js cannot await a dynamic import().
            result["stage"] = "webgl"
            win.evaluate_js(_PROBE3D)
            t1 = time.perf_counter()
            blob = None
            while time.perf_counter() - t1 < 40:
                raw = win.evaluate_js("JSON.stringify(window.__rdr3d||null)")
                if raw and raw != "null":
                    blob = json.loads(raw)
                    break
                time.sleep(0.25)
            result["webgl"] = blob or {"error": "probe never returned"}

            # 3. best effort: open the level editor so the screenshot has
            #    something in it, then look at the canvases the app itself made.
            result["stage"] = "viewport"
            for label in tabs:
                clicked = win.evaluate_js(_CLICK % _js_str(label.strip()))
                if clicked == "clicked":
                    result["clicked_tab"] = label.strip()
                    break
            time.sleep(4.0)

            # 4. load a real level. An empty grid proves WebGL works; a level
            #    proves the viewport works, which is the thing being shipped.
            #    Driven through the header's own <select> so it does not depend
            #    on any button label surviving the redesign.
            result["stage"] = "level"
            result["level"] = win.evaluate_js(_LOADLEVEL)
            t2 = time.perf_counter()
            scene = {}
            while time.perf_counter() - t2 < 60:
                time.sleep(2.0)
                scene = json.loads(win.evaluate_js(_SCENE))
                # "no level loaded" is the app's own words for an empty
                # viewport. Waiting for it to stop saying that is the only
                # honest way to know the level arrived.
                if not any("no level loaded" in s.lower()
                           for s in scene.get("status", [])):
                    break
            # The status line says a level is loaded while it is still
            # decoding geometry, so give the viewport a moment to have
            # something in it before the screenshot is taken.
            time.sleep(10.0)
            result["canvases"] = json.loads(win.evaluate_js(_CANVASES))
            result["level_seconds"] = round(time.perf_counter() - t2, 1)
            result["scene"] = json.loads(win.evaluate_js(_SCENE))
            result["scene_at_load"] = scene

            w = result.get("webgl") or {}
            result["ok"] = bool(w.get("three_rendered_pixels", 0) > 0)
            result["stage"] = "done"
        except Exception as ex:                            # noqa: BLE001
            result["error"] = "%s: %s" % (type(ex).__name__, ex)
        result["timings"] = dict(TIMINGS)
        try:
            with open(out_path, "w", encoding="utf-8") as fh:
                json.dump(result, fh, indent=1)
        except OSError:
            pass
        if hold:
            time.sleep(hold)
        win.destroy()

    threading.Thread(target=run, daemon=True).start()


_READY = r"""
JSON.stringify({
  ready: document.readyState === 'complete' &&
         document.querySelectorAll('button').length > 3,
  title: document.title,
  buttons: document.querySelectorAll('button').length,
  navButtons: document.querySelectorAll('nav button').length
})
"""

_CLICK = r"""
(function(){
  var want = %s.toLowerCase();
  var b = [].slice.call(document.querySelectorAll('button,[role=tab],a'))
    .find(function(x){ return (x.textContent||'').trim().toLowerCase() === want; });
  if (b) { b.click(); return 'clicked'; }
  return 'not found';
})()
"""

# Pick a specific known level rather than "the first option of the first
# <select>" -- the first select on the page turned out to be an audio sample
# rate, and "chose 22050" is not a level. Named level first, everywhere, then
# a clickable row with that name, and only then give up.
_LOADLEVEL = r"""
(function(){
  var NAME = /^\s*SHER70\b/i;
  var sels = [].slice.call(document.querySelectorAll('select'));
  for (var i = 0; i < sels.length; i++) {
    var s = sels[i];
    var want = [].slice.call(s.options).find(function(o){
      return NAME.test(o.textContent || '') || NAME.test(o.value || ''); });
    if (!want) continue;
    s.value = want.value;
    s.dispatchEvent(new Event('change', {bubbles:true}));
    return 'select: ' + (want.textContent || want.value).trim();
  }
  var el = [].slice.call(document.querySelectorAll(
      'button,li,a,tr,div,span')).find(function(x){
    return NAME.test(x.textContent || '') &&
           (x.textContent || '').trim().length < 40 &&
           x.getClientRects().length; });
  if (el) { el.click(); return 'clicked: ' + el.textContent.trim(); }
  return 'no level picker found';
})()
"""

# How much did the viewport actually draw? Counted off the app's own renderer
# rather than a probe canvas, so it is the real scene or it is nothing.
_SCENE = r"""
(function(){
  var out = {};
  try {
    var cvs = [].slice.call(document.querySelectorAll('canvas'))
      .filter(function(c){ return c.width > 400; });
    out.viewports = cvs.length;
    if (cvs.length) {
      var c = cvs[0], gl = null;
      try { gl = c.getContext('webgl2') || c.getContext('webgl'); } catch(e){}
      if (gl) {
        out.drawing = gl.drawingBufferWidth + 'x' + gl.drawingBufferHeight;
        out.gl_error = gl.getError();
      }
    }
    var s = (document.body.innerText || '');
    var m = s.match(/(\d[\d,]*)\s+objects?/i);
    if (m) out.objects_text = m[0];
    out.status = (s.split('\n').filter(function(l){
      return /loaded|objects|level/i.test(l); }).slice(-3));
  } catch (e) { out.error = String(e); }
  return JSON.stringify(out);
})()
"""

_CANVASES = r"""
(function(){
  return JSON.stringify([].slice.call(document.querySelectorAll('canvas'))
    .map(function(c){
      var gl = null;
      try { gl = c.getContext('webgl2') || c.getContext('webgl'); } catch(e){}
      return { w: c.width, h: c.height,
               css: Math.round(c.clientWidth) + 'x' + Math.round(c.clientHeight),
               live: !!gl,
               drawingBuffer: gl ? gl.drawingBufferWidth + 'x' + gl.drawingBufferHeight : null };
    }));
})()
"""

# Kicked off, not awaited: dynamic import() is a promise and evaluate_js is
# synchronous, so the answer is parked on window.__rdr3d and polled for.
_PROBE3D = r"""
(function () {
  window.__rdr3d = null;
  var out = { errors: [] };
  window.addEventListener('error', function (e) {
    out.errors.push(String(e.message)); });
  function finish() { window.__rdr3d = out; }

  try {
    var c = document.createElement('canvas'); c.width = 64; c.height = 64;
    var gl2 = c.getContext('webgl2');
    var gl = gl2 || c.getContext('webgl');
    out.webgl2 = !!gl2;
    out.webgl1 = !!gl;
    if (gl) {
      var dbg = gl.getExtension('WEBGL_debug_renderer_info');
      out.vendor = dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL)
                       : gl.getParameter(gl.VENDOR);
      out.renderer = dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL)
                         : gl.getParameter(gl.RENDERER);
      out.glsl = gl.getParameter(gl.SHADING_LANGUAGE_VERSION);
      out.maxTexture = gl.getParameter(gl.MAX_TEXTURE_SIZE);
    }
  } catch (e) { out.context_error = String(e); }

  if (!out.webgl1) { finish(); return 'no-webgl'; }

  import('./three.module.js').then(function (THREE) {
    try {
      out.three_revision = THREE.REVISION;
      var cv = document.createElement('canvas'); cv.width = 128; cv.height = 128;
      var r = new THREE.WebGLRenderer({ canvas: cv, antialias: false,
                                        preserveDrawingBuffer: true });
      r.setSize(128, 128, false);
      var sc = new THREE.Scene();
      var cam = new THREE.PerspectiveCamera(60, 1, 0.1, 100);
      cam.position.z = 3;
      sc.add(new THREE.Mesh(new THREE.BoxGeometry(2, 2, 2),
                            new THREE.MeshBasicMaterial({ color: 0xff5522 })));
      r.render(sc, cam);
      var g = r.getContext();
      var px = new Uint8Array(128 * 128 * 4);
      g.readPixels(0, 0, 128, 128, g.RGBA, g.UNSIGNED_BYTE, px);
      var lit = 0, sample = null;
      for (var i = 0; i < px.length; i += 4) {
        if (px[i] > 8 || px[i + 1] > 8 || px[i + 2] > 8) {
          lit++;
          if (!sample) sample = [px[i], px[i + 1], px[i + 2]];
        }
      }
      out.three_rendered_pixels = lit;
      out.three_sample_rgb = sample;
      out.three_gl_error = g.getError();
      r.dispose();
    } catch (e) { out.three_error = String(e); }
    finish();
  }).catch(function (e) { out.three_import_error = String(e); finish(); });
  return 'started';
})()
"""


def _verify_firstrun(win, argv):
    """Drive the setup screens without a human, and without a real install.

    First run is the only run a new user ever has, and it has shipped broken
    before -- the machine that built the app already had settings, so the
    build never took this branch at all. Point LOCALAPPDATA at an empty folder
    and the packaged exe takes it for real:

        set LOCALAPPDATA=%TEMP%\\pretend & "RDR Modding Studio.exe" --verify-firstrun

    Nothing is picked and nothing is copied -- the file dialogs need a person.
    What is proved is that all four screens exist and render, that the
    JavaScript can reach Python across the bridge, and that the emulator step
    answers. --hold keeps each screen up long enough to be photographed.
    """
    import json

    out_path = _arg_value(argv, "--out") or os.path.join(
        os.path.expanduser("~"), "rdr_firstrun_check.json")
    hold = float(_arg_value(argv, "--hold") or 0)

    def run():
        res = {"ok": False, "settings_file": paths.SETTINGS_FILE}
        try:
            t0 = time.perf_counter()
            while time.perf_counter() - t0 < 30:
                if win.evaluate_js("document.readyState") == "complete":
                    break
                time.sleep(0.25)
            res["screens"] = json.loads(win.evaluate_js(_FR_SCREENS))
            # The bridge: JavaScript calling Python. If this is broken the
            # setup screens are a picture of a wizard, not a wizard.
            res["bridge"] = json.loads(win.evaluate_js(_FR_BRIDGE) or "null")
            t1 = time.perf_counter()
            while time.perf_counter() - t1 < 20 and res["bridge"] is None:
                time.sleep(0.5)
                res["bridge"] = json.loads(
                    win.evaluate_js("JSON.stringify(window.__fr||null)") or "null")
            if hold:
                time.sleep(hold)              # screen 1, for the camera
            win.evaluate_js("step(4)")
            res["step4"] = json.loads(win.evaluate_js(_FR_STEP4))
            res["ok"] = bool(res["screens"].get("panels") == 4
                             and res["screens"].get("steps") == 4
                             and res["bridge"])
            if hold:
                time.sleep(hold)              # screen 4, for the camera
        except Exception as ex:                            # noqa: BLE001
            res["error"] = "%s: %s" % (type(ex).__name__, ex)
        try:
            with open(out_path, "w", encoding="utf-8") as fh:
                json.dump(res, fh, indent=1)
        except OSError:
            pass
        win.destroy()

    threading.Thread(target=run, daemon=True).start()


_FR_SCREENS = r"""
JSON.stringify({
  panels: document.querySelectorAll('.panel').length,
  steps: document.querySelectorAll('.steps div').length,
  labels: [].slice.call(document.querySelectorAll('.steps div'))
            .map(function(d){ return d.textContent.trim(); }),
  disclaimer_chars: (document.querySelector('.legal')||{textContent:''})
                      .textContent.trim().length,
  rungs: /measured/.test(document.body.textContent) &&
         /inferred/.test(document.body.textContent),
  agree_box: !!document.getElementById('agree'),
  continue_disabled: !!(document.getElementById('go1')||{}).disabled
})
"""

_FR_BRIDGE = r"""
(function(){
  window.__fr = null;
  pywebview.api.find_pcsx2().then(function(d){ window.__fr = d; })
    .catch(function(e){ window.__fr = {error: String(e)}; });
  return JSON.stringify(window.__fr);
})()
"""

_FR_STEP4 = r"""
JSON.stringify({
  visible: document.getElementById('p4').classList.contains('on'),
  emu_path: (document.getElementById('emuPath')||{}).textContent,
  note: (document.getElementById('emuNote')||{}).textContent,
  skip_button: !!document.getElementById('skipEmu')
})
"""


def _report(win, argv):
    """--timings: measure the front end too, then quit. Used by the build
    check, never by a real user."""
    def run():
        time.sleep(6.0)
        try:
            raw = win.evaluate_js(_PROBE)
            import json
            TIMINGS["frontend"] = json.loads(raw)
        except Exception as ex:                            # noqa: BLE001
            TIMINGS["frontend_error"] = str(ex)
        win.destroy()
    threading.Thread(target=run, daemon=True).start()


_SPLASH = """
<style>
 html,body{height:100%;margin:0;background:#1e2124;color:#98a2a9;
   font:13px "Segoe UI",system-ui,sans-serif;display:flex;
   align-items:center;justify-content:center}
 div{text-align:center}
 b{display:block;color:#e4eaef;font-size:16px;font-weight:600;margin-bottom:6px}
</style>
<div><b>RDR Modding Studio</b>opening your disc&hellip;</div>
"""

_PROBE = r"""
(function () {
  var c = document.createElement('canvas');
  var gl2 = c.getContext('webgl2');
  var n = performance.getEntriesByType('navigation')[0] || {};
  var res = performance.getEntriesByType('resource');
  var bytes = 0; res.forEach(function (r) { bytes += (r.decodedBodySize || 0); });
  var nav = document.querySelectorAll('nav button');
  return JSON.stringify({
    webgl2: !!gl2,
    domContentLoaded: Math.round(n.domContentLoadedEventEnd || 0),
    resourceKB: Math.round(bytes / 1024),
    navButtons: nav.length,
    canvases: document.querySelectorAll('canvas').length,
    title: document.title
  });
})();
"""


if __name__ == "__main__":
    sys.exit(main())
