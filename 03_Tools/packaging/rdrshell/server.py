"""Start the existing Studio inside this process, on a port nobody else has.

Deliberately thin. It imports rdrstudio unchanged and changes exactly two
things about it:

  * the config paths, so a downloaded app reads and writes in the user's
    own folders instead of the repo folder
  * WEB_ROOT, so the front end is found inside the frozen bundle

Both are module-level names that every use site reads at call time, so
assigning to them here is enough -- no source edit, nothing to keep in sync,
and running from source still behaves exactly as it always did.

The server runs on a daemon thread bound to 127.0.0.1 with port 0, which means
the OS picks a free one. Two copies of the app can therefore be open at once,
and a Studio the developer already has running on 8731 is never disturbed.
"""

import os
import socket
import sys
import threading
import time


class StudioServer:
    def __init__(self):
        self.port = None
        self.url = None
        self.studio = None
        self.httpd = None
        self._thread = None

    # ------------------------------------------------------------ patching

    @staticmethod
    def configure(settings, resource_dir):
        """Point the toolkit at the user's folders and the bundled web root.

        Returns the rdrstudio.studio module, imported and patched.
        """
        from rdrstudio import config as cfg
        from rdrstudio import studio as studio_mod

        cfg.VANILLA_ISO = settings["vanilla_iso"]
        cfg.MODDED_ISO = settings["working_iso"]
        cfg.MODS = settings["mods_dir"]
        cfg.BACKUPS = settings["checkpoints_dir"]
        cfg.EXTRACTED = settings["exports_dir"]
        cfg.EXPORTS = settings["exports_dir"]
        cfg.DATA_EDITOR = settings.get("data_editor_dir") or ""
        if settings.get("pcsx2_exe"):
            cfg.PCSX2_EXE = settings["pcsx2_exe"]
            cfg.PCSX2_DIR = os.path.dirname(settings["pcsx2_exe"])

        web = os.path.join(resource_dir, "web")
        if os.path.isdir(web):
            studio_mod.WEB_ROOT = web
        return studio_mod

    # -------------------------------------------------------------- lifecycle

    def start(self, settings, resource_dir, host="127.0.0.1", port=0):
        from http.server import ThreadingHTTPServer

        studio_mod = self.configure(settings, resource_dir)
        from rdrstudio import config as cfg

        self.studio = studio_mod.Studio(
            settings["vanilla_iso"], settings["working_iso"],
            settings.get("pcsx2_exe") or None, cfg.emulog())
        self.studio.note("desktop app started")
        # Pay all the first-touch costs now, visibly, instead of on the first
        # click. The boot overlay reads /api/warmup for the bar.
        self.studio.start_warmup()

        studio_mod.Handler.studio = self.studio
        self.httpd = ThreadingHTTPServer((host, port), studio_mod.Handler)
        self.port = self.httpd.server_address[1]
        self.url = "http://127.0.0.1:%d/" % self.port

        self._thread = threading.Thread(target=self.httpd.serve_forever,
                                        name="studio-http", daemon=True)
        self._thread.start()
        return self.url

    def wait_ready(self, timeout=10.0):
        """Block until the socket actually answers. Returns seconds waited."""
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < timeout:
            try:
                with socket.create_connection(("127.0.0.1", self.port), 0.25):
                    return time.perf_counter() - t0
            except OSError:
                time.sleep(0.01)
        raise RuntimeError("server did not come up within %.0fs" % timeout)

    def stop(self):
        try:
            if self.httpd:
                self.httpd.shutdown()
                self.httpd.server_close()
        except Exception:                                  # noqa: BLE001
            pass
        try:
            if self.studio:
                self.studio.close()
        except Exception:                                  # noqa: BLE001
            pass


def add_toolkit_to_path(resource_dir):
    """Running from source, rdrstudio sits beside packaging/. Frozen, it is
    already importable from the bundle."""
    if not getattr(sys, "frozen", False) and resource_dir not in sys.path:
        sys.path.insert(0, resource_dir)
