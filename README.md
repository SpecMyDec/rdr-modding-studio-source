# RDR Modding Studio 2.0 (source)

A modding tool and 3D level editor for **Red Dead Revolver** (PlayStation 2, USA, SLUS-20500):
levels, models, collision, textures, animations (BVH and glTF), game data tables, save files, audio
and streams, Flash menus, cutscenes, mod packs and executable "code mods". Windows desktop app
(Python + pywebview).

**Download:** the ready-made app is on this repository's [Releases](../../releases) page
(`RDR-Modding-Studio-2.0.0-full.zip`: unzip anywhere and run `RDR Modding Studio.exe`).

This is a fork of [RDR Modding Studio 1.4.0](https://github.com/pop123321pop/rdr-modding-studio) by
pop123321pop. It adds, among other things, the RDR Data Editor built in (a mods folder built into the
disc, archives rebuilt when an edit needs more room, new levels and characters), Flash screen (`.pck`)
viewing and editing with font fill, named and replaceable sounds, a stream (`.stm`) creator with a loop
editor, a fixed Blender animation round trip, glTF export of a clip on its model, and profiles.
Everything it changed, and the research behind it: [05_Docs/RESEARCH/FORK-CHANGES.md](05_Docs/RESEARCH/FORK-CHANGES.md).

## You need your own disc

This repository contains **no game data**. Every tool reads a disc image you supply, dumped
from a copy of the game you own. Do not commit, upload or link game files (ISOs, `.dat`,
`.rsc`, `.mod`, `.tex`, ...). `.gitignore` blocks the common ones.

Put your image at `01_Vanilla/RDR_VANILLA.iso`. The working copy that edits go to is
`02_Modded/RDR_MODDED.iso` (made on first run).

## Run from source (Windows)

Python 3.10+ and the Microsoft Edge WebView2 runtime.

```
py -3 -m pip install pywebview pillow numpy
cd 03_Tools
py -3 RDR_Studio.py            # browser UI on http://127.0.0.1:8731
```

`rdr.py` is the command-line toolkit (`py -3 rdr.py --help`).

## Build the desktop app

```
cd 03_Tools
py -3 build_app.py             # creates its own venv, freezes with PyInstaller, runs the self-checks
```

`py -3 build_app.py --internal` builds the **Full** edition this fork ships
(`03_Tools/packaging/dist/internal/`, zipped as `RDR-Modding-Studio-<version>-full.zip`); without `--internal`
it builds the original's public edition, which hides 14 tools. Details: `03_Tools/packaging/BUILDING.md`.

## Tests

```
cd 03_Tools
py -3 run_tests.py --quick     # ~35 min with a disc; prints a SUMMARY block and exits non-zero on failure
py -3 run_tests.py             # full suite, includes two whole-disc sweeps
```

Most tests read your disc image and **fail without it**. These run with no disc:

```
py -3 packaging/test_build.py
py -3 scripts/test_verify_gate.py
```

The code-mod tests (`scripts/test_codemods*.py`) also need an R5900 interpreter that is not
included here; they print SKIP without it (see CONTRIBUTING.md).

## Docs

- `05_Docs/RESEARCH/FORK-CHANGES.md` - what this fork changed from 1.4.0, and the measurements behind it
- `05_Docs/DATA-EDITOR.md` - the Data Editor tab, rebuilding archives, new levels and characters
- `05_Docs/CAPABILITIES.md` - what each format can and cannot do, with the probe that settled it
- `05_Docs/RESEARCH/UNKNOWNS.md` - every file type: decoded, partial or unknown
- `05_Docs/RESEARCH/FIELDS.md`, `GLOSSARY.md`, `ANIM-ABBREV.md` - field and naming notes
- `05_Docs/OPCODES.md`, `CODEMODS.md` - behaviour opcodes and executable hooks
- `05_Docs/USER_GUIDE.md`, `MODDING_GUIDE.md`, `HOWTO-CUSTOM-MODELS.md` - user docs (also in the app)
- `03_Tools/research/` - probes that reproduce each research finding across the whole disc

Code-mod hooks build on community reverse-engineering research. The `.pck` Flash screen format reading
builds on AlgumCorrupto's RAGE-SWF-research. Original Studio by pop123321pop.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Licence: MIT ([LICENSE](LICENSE)).

Not affiliated with or endorsed by Take-Two Interactive, Rockstar Games or Capcom.
