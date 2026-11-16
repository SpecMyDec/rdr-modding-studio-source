# Changelog

## 2.0.0

The first release of this fork of [RDR Modding Studio 1.4.0](https://github.com/pop123321pop/rdr-modding-studio).
Everything 1.4.0 does still works. The download is the **Full** edition (every tool, the original's gated ones
included). Details and the measurements behind each change: `05_Docs/RESEARCH/FORK-CHANGES.md`.

**Download:** `RDR-Modding-Studio-2.0.0-full.zip`, unzip anywhere, run `RDR Modding Studio.exe`. Windows, with the
Microsoft Edge WebView2 runtime. You need your own Red Dead Revolver (PS2, USA) disc image; nothing from the game is
included.

### Added

- **Data Editor tab**: a mods folder of loose game files built into the working disc; archives rebuilt (in the
  game's own layout and disc order) when an edit needs more room instead of refusing it; **Build** button; saved
  Mods, Workbench, HUD text editor, image converter; **New .dat** for new story levels and Showdown maps (with their
  menu row, unlock and thumbnail); Add / Delete Character; `.dat` folders exported with a file naming the disc folder
  they belong in, and imported back there.
- **Flash screens (`.pck`)**: drawn as the game draws them, every object in a tree; edit text (no length limit),
  positions, colours, buttons; replace and add pictures of any size; fill fonts that lack letters from the same font
  elsewhere on the disc or a TTF; list and replace a screen's sounds.
- **Audio**: banks and streams organised and named; sounds traced to the right wave; add streams; a **stream
  creator** with a loop editor (shift + click sets the loop).
- **Animation**: *Export animation with current model* (`.glb`), and Export all animations with the current model;
  preferred skeleton / model.
- **Level editor**: a Showdown map's name and sub text.
- **Profiles** in Settings; **New mod** and **Import mod** beside Build & share; Build complete in the log.

### Fixed

- Swizzled 4-bit textures (a third of some levels') drew as noise.
- A Blender BVH round trip came back as a different motion (Blender writes Z-up) and was refused as too big.
- An edited animation did not reach a level that plays its own archive's copy (now every copy is updated).
- A Flash screen font filled with new letters drew garbled in game (texture header upload fields).
- Sounds played the wrong wave (the bank tables' key -> split -> set -> sample -> wave chain).
- Slow launch (Versions diff now cached) and slow Audio tab.
- Files added in a sub-folder of a game `.dat` folder were never found by the game; Showdown maps filed under
  `lvls_sng` were never loaded (both now handled or explained).
- The self-check no longer fails on a working disc that has new levels.

### Changed

- The **Disc** tab group is now **Data Editor**; **Boot test** removed; the Settings window scrolls.
