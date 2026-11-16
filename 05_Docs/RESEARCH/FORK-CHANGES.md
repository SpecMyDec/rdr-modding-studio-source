# What this fork changed (2.0.0), and the research behind it

This fork starts from **RDR Modding Studio 1.4.0** by pop123321pop
(<https://github.com/pop123321pop/rdr-modding-studio>). Everything the original does still works as it did; this
file lists what was added or changed on top of it, why, and what was measured to justify each change. Format facts
that are new are recorded in full in [UNKNOWNS.md](UNKNOWNS.md) (section 3g and the `.pck` section); this file points
to them rather than repeating them.

The fork ships the **Full** edition (the original's "internal" build: every tool, the gated ones included), as
version 2.0.0.

Status words are the original project's: **measured** (checked across the disc or in the game), **untested** (built
and checked in the Studio, not yet seen in the game).

---

## 1. The RDR Data Editor, built in

The standalone RDR Data Editor (previously the PS2 Mod Builder) is now a tab of the Studio: a mods folder of loose
game files, laid out one folder per `.dat`, built into the working disc on demand. Full description:
[../DATA-EDITOR.md](../DATA-EDITOR.md).

- New code: `rdrstudio/dataeditor.py` (build, inject, restore, new levels, characters), `rdrstudio/dataeditor_more.py`
  (Workbench, saved Mods, HUD text, images, Flash screens, audio, streams, export), `rdrstudio/pendingrebuild.py`
  (the rebuild queue), `rdrstudio/dataeditor_lib/` (the Data Editor's build scripts used as a library),
  `web/dataeditor.js`.
- **Edits that do not fit are queued and the archive is rebuilt**, instead of being refused. The original Studio
  patched files in place only; a file that grew past its slot was an error. Now the archive is rebuilt in the
  game's own layout with every queued file (`rebuild_on_overflow`), on the next **Build** or **Play**.
- **The disc is put back in the shipped file order after a rebuild** (`restore_disc_order`). A grown archive used
  to be appended at the end of the image, and the game then seeks across the whole disc on every level load.
- **Build** button in the top bar, with a "Build complete" line in the activity log.
- **New .dat**: new story levels (`lvls_sng/<name>.dat`) and new Showdown maps (`lvls_m2/<name>2.dat` +
  `lvls_m4/<name>4.dat`, with their Showdown menu row, unlock and thumbnail). **Measured:** two custom Showdown maps
  built this way load and play in the game.
- **Showdown map name and sub text** editable in the level editor (both copies of the menu: in-game and front end).
- **Add Character / Delete Character**, a character from your own `.rsc` (the Blender add-on's export).
- **Mod packs** (Build & share) carry the Data Editor's `.dat` folders and Workbench too; **Import mod** and **New
  mod** buttons beside it. **Reset to stock** clears the Data Editor's mods as well (saved as a Saved Mod first).
- **`.dat` folders remember where they belong**: an exported folder carries `rdr_folder.txt` naming its disc folder
  (`lvls_sng`, `lvls_m2`, `lvls_m4`, or the top); importing puts it back there. Without one, a name ending in 2 / 4
  goes to `lvls_m2` / `lvls_m4`. The build never packs that file.
- **Files in a sub-folder of a game `.dat` folder go in at the archive's top.** None of the game's 66 archives has
  folders, and a character dropped in as `swat.dat/gbw/gbw.cr` was never found by the game (an endless load
  screen). Measured on all 66: no archive holds a path with a `/`.
- **Add from game** can copy into the Workbench instead of a `.dat` folder.

## 2. Flash screens (`.pck`): drawn, edited, and their fonts filled

The game's menus, journal pages, shops and popups are `.pck` files: the Flash player's objects dumped from memory.
The original Studio listed them as **PARTIAL** (container known, contents not). Now:

- **Decoded and drawn** (`rdrstudio/pck.py`, `pckscene.py`, `ps2gs.py`; `web/pckview.js`): every object type, the
  timeline, sprites, buttons, shapes, bitmaps, static and edit text. A **Pages view** shows a screen as the game draws
  it with every object in a tree, as FFDEC shows a `.swf`. Format credit: AlgumCorrupto's RAGE-SWF-research
  (`flashpck.h`); the Studio's reading and everything past that reference is in UNKNOWNS.md (`.pck`).
- **Editing in place and with growth**: text (no letter limit: a string that no longer fits is moved to the end of
  the payload and repointed), position / scale / rotation (a transform gizmo), colour transforms, button text,
  replacing a picture with one of any size, adding new pictures. **Measured:** edited text, imported pictures and a
  grown `.pck` show correctly in the game.
- **Font fill**: a title font that only has its own letters gets the missing ones, from the same font elsewhere on
  the disc (matched by glyph shape, across sizes), or drawn from a TTF. Autofill is a checkbox, off by default.
  **The garbled-font bug and its fix**: a filled atlas changes size, and the texture header's upload fields
  (+0x2a size in 256-byte units, +0x31, +0x32 the upload rectangle) must change with it, or the GS reads the
  atlas with the old layout. **Measured** in game after the fix.
- **Sounds** of a screen are listed and replaceable. The `.pck` sound objects are empty stubs; the screen's own
  ActionScript calls `new Sound().attachSound("<bank name>")`, and that name is what leads to the wave (section 4).
  **Measured:** the user checked every sound of a screen against the game.
- **Image conversion** with transparency and no template, `.tex` import into a `.pck`.

## 3. Textures

- **4-bit textures stored swizzled are unswizzled** (`texture.decode`). The original unswizzled only 8-bit ones,
  so a third of some levels' textures drew as noise (MANSINTB: 24 of 72). The 4-bit data is read back from the
  PSMCT32 rectangle the header names (+0x32), the same rule the `.pck` atlases use. Measured on MANSINTB: pixel-to-
  pixel roughness halved on 22 of the 24, the other two (flat colours) unchanged, all 24 drawn clean.

## 4. Audio

- **The Audio tab is organised and named**: banks grouped by what they are, each sound named from the game's own
  bank tables, a search box; the streams grouped into Music, Cutscenes, Levels and ambience, Characters, Story dialogue.
- **Sound name -> wave, the whole chain** (`rdrstudio/vag.py` `key_sets`, `set_waves`): a bank table row
  (`snd_<n>.csv`: name, code, bank, program, key) names a *program* and a *key*; the bank's `.hd` program holds
  *splits* (key range -> sample set); the set names a sample, the sample a wave (`Vagi`). Reading column 1 as the
  wave (it is the sound's code) and the set as the wave index were both tried first and both played the wrong
  sound. **Measured** against the game.
- **New streams** (`.stm`) can be added, and a **stream creator** makes one from a WAV with a loop editor
  (shift + left click sets the loop start, shift + right click the end). The format and its loop rules are in
  UNKNOWNS.md 3g; the key facts that make a loop work in the game: IMA ADPCM in 64-byte channel interleave, the loop
  restart a byte offset with each channel's predictor state stored for it, no loop *end* field (a stream loops at its
  end), and both the restart and the length on a whole interleave unit (128 samples at 12 / 24 kHz, 256 at 48 kHz).
  A third-party creator that ignored the unit is what made a level's music play past its loop and come back
  distorted. Implemented natively; no third-party binary is bundled.
- **Loading speed**: the Audio tab read each archive entry through a call that re-parses the whole name table per
  file (30 s for 256 files); it now reads entry by entry.

## 5. Animation

- **Blender round trip fixed** (`rdrstudio/bvh.py`). Blender's BVH importer turns a clip from Y-up into Z-up and its
  exporter has no axis setting, so an untouched import / export came back with every point as Z.p and every joint
  rotation as Z.R.Z^T: 86% of the samples "changed", it re-encoded bigger and was refused. The importer now tells a
  Z-up file by comparing both readings with the stored clip and turns it back (37.7 degrees off per joint as
  written, 0.00 after). Also: frames past the clip that only hold its last pose (Blender's scene end, 250 by
  default) are dropped; near-zero position channels on every bone are accepted; the dead band is 0.002 (degrees /
  metres) because Blender's float32 arithmetic moves every sample by up to 0.00095 degrees. **Measured:** an
  unedited Blender round trip comes back byte-identical; a 5-degree edit still writes.
- **An edit that grows past its slot is written** (the archive is rebuilt), instead of refused.
- **The same clip in other archives is updated too.** 552 clip names sit in more than one archive, and a level plays
  the copy in its own: PERFNEW's load show (`lvl_perfnew.lsh`, inside PERFNEW.DAT) names `mrd_load_a`, and an edit
  made in SWAT.DAT never reached it. Copies still as they were (before the edit, or as shipped) get the edit, as one
  undo; a copy changed on its own is left and named.
- **Export animation with current model**: one `.glb` (the skinned model, its skeleton, textures, the clip baked from
  the viewer's own posing). **Export all animations** can do the same for every clip that plays on that skeleton,
  into `exports/animations/<model>.anim/`. Checked in Blender 4.5: armature, skin, action and textures import.
- **Preferred skeleton / model**, tried first on every clip, saved in the settings and in profiles.

## 6. App-wide

- **Profiles** (Settings): every preference (theme, viewport and view settings, game lighting, the panels' choices)
  saved under a name, loaded, exported and imported.
- **Settings window scrolls**; the **Disc** tab group is now **Data Editor**; **Boot test** removed (Play does the
  job); **Edit HUD text** stays in the Data Editor, not under Menus.
- **Launch speed**: the Versions view diffed 57,000 files against the stock disc on every launch; the answer is now
  cached on disk, keyed by both discs' size and date.

## 7. Game facts found on the way (all measured)

- **A mission's player character is read from the level's own archive.** All 32 `levelflow.csv` rows: the player's
  `.cr` is in the mission's archive. A custom player character therefore has to be in that archive as well as
  SWAT.DAT.
- **A level's load show is its own `.lsh`** in its archive, naming that level's `<char>_load_*` clips.
- **Showdown maps load only from `lvls_m2` / `lvls_m4`**, and only when the Showdown menu (`postermultiview.swui`, in
  swat.dat and front.dat) has a row for `lvl_<map>`; the row holds the thumbnail, the name and the sub text.
- **No archive has folders** (66 of 66).

## 8. Shipping

- Version **2.0.0**; the download is the **Full** edition (`RDR-Modding-Studio-2.0.0-full.zip`).
- The build's check that no path from the build machine is compiled in now runs for this edition too (it only ran
  for the original's public edition), and covers `dataeditor_lib`.
- Still no game data in the repository. `rdrstudio/dataeditor_lib/assets/` holds three small template images used by
  the picture converter.
