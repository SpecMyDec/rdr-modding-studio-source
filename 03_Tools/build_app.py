#!/usr/bin/env python3
"""Turn this source tree into the thing people download. One command.

    python build_app.py                 build the release download
    python build_app.py --internal      build the internal edition instead
    python build_app.py --both          both, compared
    python build_app.py --setup         only create/refresh the build venv
    python build_app.py --quick         skip the checks (for iterating; never ship one)

It does the boring part first: a build needs PyInstaller, pywebview, Pillow and
numpy, and none of those belong in the environment that runs the Studio itself.
So the build gets its own virtual environment in packaging/.venv, created here
if it is missing, and every later step runs inside it.

Then it hands over to packaging/build.py, which freezes, gates, checks and
zips. Everything it knows how to do is described there; this file exists so
that "how do I make the download" has a one-line answer that does not start
with "first, activate the...".

Nothing here touches 01_Vanilla or 02_Modded. The build reads web/, rdrstudio/
and packaging/, and writes only inside packaging/build and packaging/dist.
"""

import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "packaging")
VENV = os.path.join(PKG, ".venv")
VENV_PY = os.path.join(VENV, "Scripts", "python.exe")

# Pinned loosely on purpose: these are the versions the build was measured
# with, and a newer PyInstaller has broken a working freeze before.
NEED = ["pyinstaller==6.21.0", "pywebview==6.2.1", "pillow", "numpy"]


def have_venv():
    return os.path.isfile(VENV_PY)


def setup(force=False):
    if have_venv() and not force:
        print("build environment: %s" % VENV)
        return True
    print("creating the build environment in %s ..." % VENV)
    r = subprocess.run([sys.executable, "-m", "venv", VENV])
    if r.returncode:
        print("could not create a virtual environment with %s" % sys.executable)
        return False
    r = subprocess.run([VENV_PY, "-m", "pip", "install", "--upgrade", "pip"],
                       capture_output=True, text=True)
    print("installing %s ..." % ", ".join(NEED))
    r = subprocess.run([VENV_PY, "-m", "pip", "install"] + NEED)
    return r.returncode == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--internal", action="store_true")
    ap.add_argument("--both", action="store_true")
    ap.add_argument("--setup", action="store_true")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--no-window", action="store_true",
                    help="skip the WebGL window check (it needs a desktop)")
    ap.add_argument("--notes", default="")
    a = ap.parse_args()

    if not setup(force=a.setup):
        return 1
    if a.setup:
        return 0

    cmd = [VENV_PY, os.path.join(PKG, "build.py")]
    if a.both:
        cmd.append("--both")
    elif a.internal:
        cmd += ["--build", "internal"]
    if a.quick:
        cmd.append("--quick")
    if a.no_window:
        cmd.append("--no-window")
    if a.notes:
        cmd += ["--notes", a.notes]

    rc = subprocess.run(cmd, cwd=PKG).returncode
    dist = os.path.join(PKG, "dist")
    if rc == 0:
        zips = sorted((os.path.join(dist, f) for f in os.listdir(dist)
                       if f.endswith(".zip")),
                      key=os.path.getmtime, reverse=True)
        if zips:
            print("\nThe download is:\n  %s" % zips[0])
    else:
        print("\nThe build did not pass its own checks. Nothing above is "
              "shippable.\nRead the failures printed above -- each names the "
              "gate it stopped at.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
