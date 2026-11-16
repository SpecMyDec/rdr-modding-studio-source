# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for RDR Modding Studio.

Build both shapes and compare:

    pyinstaller packaging/rdrstudio_app.spec -- --onedir
    pyinstaller packaging/rdrstudio_app.spec -- --onefile

onedir is what ships. onefile is measured so the trade-off is on the record
rather than assumed.

What goes in, and why:

  rdrstudio/        the toolkit, unchanged
  rdrshell/         the window, first run, per-user paths
  web/              the front end -- three.js is vendored, nothing is fetched
  ROADMAP.md        the Roadmap tab reads this file at runtime
  Pillow            texture and still-image import needs it
  numpy             OPTIONAL. Only a speed-up for .ipu encode and the IDCT;
                    every path has a pure-Python definition it falls back to.
                    Costs ~17 MB, so it is a build switch, not a given.

What is deliberately kept out: the whole scripts/ folder (77 verifiers, for
development), ref2/ (research notes), tkinter, and every scientific package
that happens to be installed on the build machine.
"""

import os
import sys

NUMPY = os.environ.get("RDR_WITH_NUMPY", "0") == "1"
ONEFILE = os.environ.get("RDR_ONEFILE", "0") == "1"

PKG = os.path.abspath(os.path.join(SPECPATH))
TOOLS = os.path.dirname(PKG)
PROJECT = os.path.dirname(TOOLS)

# build.py stages web/ into build/<tag>/web, strips the feature spans this
# edition omits, and runs the syntax gate over the result -- so what is frozen
# is exactly what was checked. Building the spec by hand still uses the source
# tree, unstripped, which is the right default for a developer.
WEB = os.environ.get("RDR_WEB_DIR") or os.path.join(TOOLS, "web")

datas = [
    (WEB, "web"),
    (os.path.join(PKG, "rdrshell", "firstrun.html"), "rdrshell"),
    # Convert images > book_red picture: the GIMP template's layers, read from <ps2build's folder>/assets --
    # the bundle's top level once frozen
    (os.path.join(TOOLS, "rdrstudio", "dataeditor_lib", "assets"), "assets"),
]
# Which edition is being frozen. build.py passes it; a hand-run spec is a
# developer building for themselves, so the internal default is right.
EDITION = os.environ.get("RDR_EDITION") or "internal"

# ROADMAP.md is what the Roadmap tab renders, and that tab exists only in the
# internal edition -- so the public build must not carry it.
#
# It used to, and this is worth spelling out because the shape of the bug is
# reusable. build.py DOES filter by edition (`if edition != "release"`), but it
# only governs the copy it places at the bundle root. This spec added a second
# copy through PyInstaller's datas, and a spec that cannot see the edition
# cannot filter by it -- so the release zip shipped ROADMAP.md inside
# `_internal/` while the top-level one was correctly absent. One file, two
# owners, one of them uninformed. Caught on the way to publishing 1.0.0.
#
# README.md is gone entirely rather than made conditional: nothing reads it at
# runtime, in any edition. It is the project's own working notes and describes
# the build machine's folder layout, so shipping it was both a leak and 39 KB
# of dead weight. It was bundled next to ROADMAP.md out of habit.
if EDITION != "release":
    p = os.path.join(PROJECT, "ROADMAP.md")
    if os.path.isfile(p):
        # studio.py reads ROADMAP.md as <resource_dir>/../ROADMAP.md, so it
        # lands one level up from the toolkit inside the bundle.
        datas.append((p, "."))

hiddenimports = [
    "webview.platforms.edgechromium",
    "clr_loader",
    "PIL.Image",
    # DISC > Data Editor: the RDR Data Editor's build scripts, imported from
    # rdrstudio/dataeditor_lib through sys.path (so the analysis cannot see them)
    "ps2build", "modconvert", "rdrtex", "rdrformats", "rdrformats.rsc", "rdrformats.rscwrite",
    "rdrformats.ps2rsc", "rdrformats.mod", "rdrformats.builder", "rdrformats.skel", "rdrformats.tex",
    "rdrformats.bnd", "rdrformats.bnd_write", "rdrformats.disc", "strtbl_tool",
    "rdrstudio.dataeditor", "rdrstudio.dataeditor_more", "rdrstudio.pendingrebuild",
]

excludes = [
    "tkinter", "unittest", "pydoc", "doctest", "test",
    "matplotlib", "scipy", "pandas", "IPython", "setuptools", "pip",
    "PyQt5", "PySide2", "PySide6", "wx",
    # Pillow ships codecs for formats a PS2 texture will never be. AvifImagePlugin
    # alone drags in a 7.5 MB decoder -- a quarter of the whole download -- for a
    # format nobody is going to import a saloon wall as.
    "PIL.AvifImagePlugin", "PIL.ImageQt", "PIL.ImageTk", "PIL.ImageShow",
]
if not NUMPY:
    excludes.append("numpy")

# Dropping the AVIF codec has to be done at the binary level too: excluding the
# plugin module leaves the .pyd behind because nothing else references it.
DROP_BINARIES = ("_avif", "_imagingtk", "_imagingcms")

a = Analysis(
    [os.path.join(PKG, "rdrshell", "shell.py")],
    pathex=[TOOLS, PKG, os.path.join(TOOLS, "rdrstudio", "dataeditor_lib")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
a.binaries = TOC([b for b in a.binaries
                  if not any(k in b[0] for k in DROP_BINARIES)])

pyz = PYZ(a.pure)

NAME = "RDR Modding Studio"

if ONEFILE:
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas, [],
        name=NAME + " (onefile)",
        debug=False, bootloader_ignore_signals=False, strip=False,
        upx=False, console=False, disable_windowed_traceback=False,
    )
else:
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True,
        name=NAME,
        debug=False, bootloader_ignore_signals=False, strip=False,
        upx=False, console=False, disable_windowed_traceback=False,
    )
    coll = COLLECT(
        exe, a.binaries, a.datas,
        strip=False, upx=False, name=NAME,
    )
