#!/usr/bin/env python3
"""Measure cold start of the Studio, broken into phases.

Read-only: it points BOTH disc paths at the vanilla ISO, which is marked
read-only on disk, so Game(writable=True) raises PermissionError and the
Studio comes up in its read-only mode. Nothing is ever written. The phase
that matters for startup -- parsing two ISO directory trees -- still runs.

    python packaging/measure_startup.py
"""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

t0 = time.perf_counter()

from rdrstudio import config as cfg                       # noqa: E402
t_cfg = time.perf_counter()

from rdrstudio.studio import Studio, Handler              # noqa: E402
t_import = time.perf_counter()

ISO = cfg.VANILLA_ISO
studio = Studio(ISO, ISO, None, None)
t_open = time.perf_counter()

from http.server import ThreadingHTTPServer               # noqa: E402
Handler.studio = studio
httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
port = httpd.server_address[1]
t_bind = time.perf_counter()

studio.close()
httpd.server_close()

print("phase timings (seconds)")
print("  import config          %.3f" % (t_cfg - t0))
print("  import studio + deps   %.3f" % (t_import - t_cfg))
print("  open both ISOs         %.3f" % (t_open - t_import))
print("  bind HTTP server       %.3f" % (t_bind - t_open))
print("  ---------------------------")
print("  total to serving       %.3f" % (t_bind - t0))
print()
print("interpreter startup is NOT included above; measure that separately with")
print('  python -X importtime -c "pass"')
