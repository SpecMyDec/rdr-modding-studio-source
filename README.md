# RDR Modding Studio (source)

A modding tool and 3D level editor for **Red Dead Revolver** (PlayStation 2, USA, SLUS-20500):
levels, models, collision, textures, animations (BVH), game data tables, save files, audio,
cutscenes, mod packs and executable "code mods". Windows desktop app (Python + pywebview).

Ready-made builds: https://github.com/pop123321pop/rdr-modding-studio/releases

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

Output: `03_Tools/packaging/dist/RDR-Modding-Studio-<version>.zip`. Details: `03_Tools/packaging/BUILDING.md`.

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

- `05_Docs/CAPABILITIES.md` - what each format can and cannot do, with the probe that settled it
- `05_Docs/RESEARCH/UNKNOWNS.md` - every file type: decoded, partial or unknown
- `05_Docs/RESEARCH/FIELDS.md`, `GLOSSARY.md`, `ANIM-ABBREV.md` - field and naming notes
- `05_Docs/OPCODES.md`, `CODEMODS.md` - behaviour opcodes and executable hooks
- `05_Docs/USER_GUIDE.md`, `MODDING_GUIDE.md`, `HOWTO-CUSTOM-MODELS.md` - user docs (also in the app)
- `03_Tools/research/` - probes that reproduce each research finding across the whole disc

Code-mod hooks build on community reverse-engineering research.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Licence: MIT ([LICENSE](LICENSE)).

Not affiliated with or endorsed by Take-Two Interactive, Rockstar Games or Capcom.
