# Building the download

One command, from `03_Tools`:

```
python build_app.py
```

That creates the build environment if it is missing, freezes the app, runs
four gates, measures it, and leaves the download in
`packaging/dist/RDR-Modding-Studio-<version>.zip`.

```
python build_app.py --both        release and internal, compared
python build_app.py --internal    just the internal edition
python build_app.py --quick       skip the gates; for iterating, never for shipping
python build_app.py --setup       only build the venv
```

Everything it does is in `packaging/build.py`; `build_app.py` only makes the
environment and calls it.

## What the build actually produces

`onedir`, not `onefile`. The download is the same size either way, and onefile
re-extracts about 24 MB into `%TEMP%` on every single launch. numpy is
included: it costs about 28 MB on disk and roughly halves `.ipu` decode.

The app folder contains the exe, `_internal/`, one short `README.txt` and
`licenses/` (internal builds add `ROADMAP.md`). `EDITION.json`/`EDITION.txt`
and the user guides (`_internal/docs/`, read in the app's Guides viewer) are
inside `_internal/`.

`ROADMAP.md` is copied beside the exe rather than into the bundle, because
`studio.py` resolves it as `<resource dir>/../ROADMAP.md` — one level *above*
the bundle, which PyInstaller's `datas` cannot express. Freezing silently
killed the Roadmap tab once for exactly this reason, which is why the
self-check tests that route by name.

## The four gates

Each one stops the build. `--force` overrides the hard failures and is not a
good idea.

1. **Manifest gate.** `features.json` and the `@feature:` sentinels in `web/`
   must agree. A sentinel with no manifest entry is a hard failure — nothing
   controls it. An internal-only feature with no sentinel is a loud warning:
   it is still switched off at runtime, but its source ships, and that fact is
   written into `EDITION.json` rather than left to be discovered.
2. **JS syntax gate.** Every `.js` and every inline `<script>` that will ship
   is parsed *after* the strip, so what is checked is what ships. Uses
   `node --check` when Node is installed; otherwise the delimiter scanner in
   `jsgate.py`, which proves balance and unterminated literals and says which
   engine gave the verdict. A syntax error in the front end blanks the whole
   product, and that has happened twice.
3. **Self-check.** The frozen exe starts its own server and drives its own
   API: the front end is served, all 66 archives open, Pillow decodes an
   `.ipu`, ROADMAP resolves, the first-run page resolves, every bundled
   front-end file is reachable, WebView2 is present, licences are in the
   build.
4. **Window check.** The frozen window opens, imports the bundled three.js,
   renders a cube, and reads the pixels back. Then it loads SHER70 and
   photographs the result to `dist/<tag>-window.png`. This is the only check
   that cannot be done headlessly and it is the one that matters most.

## Feature sentinels

Wrap feature-gated front-end code so the release build can remove it:

```js
/* @feature:repack */
export function repackPanel() { … }
/* @end:repack */
```

```html
<!-- @feature:repack -->
<button id="repack">Repack…</button>
<!-- @end:repack -->
```

The id must exist in `03_Tools/features.json`. Anything marked
`"build": "internal"` is cut from the release build; anything marked
`"build": "both"` is left alone.

## Checking first run without reinstalling

First run is the only run a new user ever has, and it has shipped broken
because the machine that built it already had settings. Point `LOCALAPPDATA`
somewhere empty and the packaged exe takes the first-run branch for real:

```
set LOCALAPPDATA=%TEMP%\pretend
"dist\release\RDR Modding Studio\RDR Modding Studio.exe" --verify-firstrun --out fr.json --hold 9
```

It reports how many screens exist, whether the JavaScript can reach Python,
and whether the emulator step answers. It cannot click a native file dialog,
so choosing an ISO and copying it is still a human test.

## The installer

Inno Setup 6, not installed on this machine, so `installer.iss` is written and
unverified:

```
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\installer.iss
```

Per-user install, no admin rights, the disclaimer as the licence page, and an
uninstaller that asks before removing the working copy and never touches
`Documents\RDR Mods`.

## Where the measurements come from

`dist/build_report.json` after every build: sizes, build time, cold start,
the WebGL report, and which features were stripped.
