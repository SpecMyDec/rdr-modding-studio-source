# Data Editor, and rebuilding archives to make room

The RDR Data Editor, built into the Studio and laid out as the desktop app. Code: `rdrstudio/dataeditor.py`
(build, inject, restore), `rdrstudio/dataeditor_more.py` (Workbench, saved Mods, HUD text, images, export),
`rdrstudio/pendingrebuild.py` (the rebuild queue), `web/dataeditor.js` (page), `rdrstudio/dataeditor_lib/` (the RDR
Data Editor's build scripts and `strtbl_tool.py`, used as a library).

## The tab

The **Data Editor** tab group (called DISC before 2.0.0) holds **Data Editor** (first) and **Archives**; the old Files tab is gone from the nav (a file opened from
another tab, e.g. Archives, still opens in its text view).

Top bar, as the app's: the pages **Working disc**, **Manual .dat/.zip extracting/building** (formerly *Archive
tools*) and **Settings**, then the saved Mods: **Mod:** box, **Save**, **Save As…**, **Load…**, **Delete**,
**Export Mod…** (one `.rdrmod`: the mods folder's layout, `master_modes.txt`, `mod.ini`, `Workbench/`, the app's
format), **Import Mod…** (a `.rdrmod`, from the Studio or the desktop app). Saved Mods live in
`data_editor/Saved Mods/<name>/` (`RDRMods`, `Workbench`, `mod.ini`).

**Working disc** page, top to bottom:

- The app's header: *Drop files into .dat folders to add them to the specified .dat file*, and the Master rule.
- **Build into working disc**, **Add files…**, **Add from game…**, **Remove**, **Delete new files**, **Open folder**, **Restore defaults…**
  (checked .dat files or **Restore entire ISO**, optionally their files to the Recycle Bin), **Convert images…**.
- **All Master files with game names: Replace only / Add to all**, the counts, **Edit HUD text…**, **Export .dat to Workbench**,
  **New .dat…**, **Import .dat…** (a .dat file, or a folder of a .dat's files).
- The folder tree (Master, every .dat, new archives; right click: export in full, HUD text, add from game,
  delete new files, open folder) and the list (File, Status, Size, Modified, Goes into, Master; statuses New,
  Changed, Built, New to the archive, New to the game, Removed, not a game file; right click: delete, open its
  folder, copy to the Workbench, Master modes, delete new files). Drag files from the list onto a folder to move
  them, onto the Workbench to copy them, or out to Explorer.
- The editor under the list: text files (Save, Ctrl+S, Revert), binary files as hex, `.strtbl` opens the HUD text
  editor.
- The **Workbench** below: a table that holds files freely (never built). Add New Folder, Add files, Rename (F2), Remove (Delete key; to the Recycle Bin), Copy to .dat folder, Open
  in Explorer. Drop files and whole folders on it; drag within it moves, drag to a .dat folder copies.
- The status line and **Log**.

**Edit HUD text…** lists the text tables (swat.strtbl is the HUD text); the chosen one is copied to the Workbench
(`HUD text/<archive>/`) unless its .dat folder has one. The editor: language block, Find, **New entry…**, **Rename
ID…**, **Delete entry**, the rows (ID, Text, Font, Size), and for the selected entry its text with **Add line** /
**Remove line** (lines stack in the game), Font, Size X / Y, *Change it in every language*; **Save**, **Save and put
in <archive>**. An unchanged save is byte-identical; the game's own IDs keep their order, new ones go where they
sort, a hash clash is refused.

**TEXT > Flash screens** (`rdrstudio/pck.py`): the 82 `.pck` screens (menus, journal pages, shops, popups,
credits; 78 in swat.dat, 4 in front.dat). The chosen one is copied to the Workbench (`Flash screens/<archive>/`)
unless its .dat folder has one, and opens as a card per text: a static text's lines (one box per line, with a sketch
of where they sit: dashed boxes are the old lines), an edit text's own string. The text goes back **in place**:
nothing in the screen moves and its size stays the same, so a static text has the bytes its records had (4 per
letter; shortening one line gives room to the others) plus the 0xCD fill to the next 4-byte boundary, and an edit
text its string plus that fill. Only letters the line's font has: titles use subset fonts of just their own letters,
listed on the card. New lines are laid with the advance the glyph has at that size elsewhere in the screen, else the
font's table plus the line's letter spacing (re-laying the game's own 6,548 lines that way gives the same width for
70%, within 2% for 75%). Centred or right-aligned lines (guessed from where the lines start and end, can be changed)
keep their middle / right edge. Unchanged lines keep their bytes; an unchanged save is byte-identical (checked on all
78). Format credit: AlgumCorrupto's RAGE-SWF-research (`flashpck.h`); `pck.py` is the Studio's own reading of it.

**Pages view** (`web/pckview.js`, `rdrstudio/pckscene.py`): the screen drawn as the game draws it, from the .pck
itself -- the main timeline (its frames), sprites, buttons (up state), shapes (bitmap-filled polygons), static text
from each font's own glyph atlas, edit texts (their own text, or a dashed box where the game fills one) -- and every
object in a tree as FFDEC shows a .swf (sprites with their instance names, buttons, shapes, bitmaps, texts, edit texts,
fonts with their atlases). Click to select, drag to move; the wheel zooms. A selected thing shows the levels it can be
moved at (a sprite in the screen, a shape in that sprite ...), X / Y and, where the game stored one, scale and skew --
written into the placement's packed matrix in place (whole twips, 1/256 scale steps; a placement stored without a
scale cannot get one). "Hide it" hides a placement in the view only (popups the game hides by script). Bitmaps:
Export PNG, Replace... (resized to the picture's size, quantised to its 16 / 256 colours, written swizzled as the
game's). Pictures decode through a GS memory emulation (`rdrstudio/ps2gs.py`: PSMT8 / PSMT4 read from a PSMCT32
upload, the upload rectangle matched to the memory the texture reads); a replaced picture read back differs by ~1
level on average.

**Fill this font** (select a font in the tree; `rdrstudio/pckfont.py`): a font holds only the letters its texts
used. Fill gives it the rest -- first the same typeface at the same size from every other screen on the working disc
(a font there is the same one when all the letters both have are pixel-identical with the same metrics; those letters
are copied exactly), then what is still missing drawn from a TrueType / OpenType font you pick (installed fonts, or
one added with "Add a font file...", kept in the Data Editor's `Fonts` folder), scaled to the font's cap height;
lowercase as small capitals when the font's are (guessed: its b d f h k l t no taller than its a c e ...). Preview
shows where each letter comes from and the new atlas. The font is rebuilt (new atlas per size, metrics, char table,
ASCII map, advance table) and **appended**: the file grows (the main menu's title font, 15 -> 95 letters: +34 KB),
which the game has not been seen to accept yet -- test it. Measured on the user's 78: the journal body and the
UI fonts complete from other screens; one-off title fonts need the TrueType font.

**Growing a screen** (`Pck.append`, `pckscene.apply` -- the one write path of both views): what does not fit where
it was goes to the end of the payload (16-byte aligned) and is pointed at; the header's size follows. So:
- **No length limit on text.** A static text whose new records outgrow their room (plus the padding after) is
  appended and the text's records pointer moved; an edit text's string likewise (also a starting text for one the
  game fills). Text edits in the Pages view too (select a text, or double-click it): a box per line, re-laid live.
- **Missing letters** are added on save only with **Fill missing letters on save** ticked (off by default; one
  setting for both views); unticked, a save needing them is refused, naming them. Ticked, they are given to the font
  first (`pckfont.fill` with just those letters): the same typeface from this screen and the game's other screens --
  at the same size letter for letter; else at another size scaled (found by its advances and bearings, kept in
  1024ths of the size); else the same design built at another scale (the title font of the main menu and the
  journal / shop titles: letters 1.55x taller at 1.2x the size -- found when the shared letters keep one height
  ratio and look alike at it, median shape correlation >= 0.65) -- then the TrueType font picked in "Missing letters
  from". A .pck font carries no name (the .swf's `PCKFont_<offset>` are the converter's), so it is matched by its
  letters. **Fill every font** (Pages view) and **Fill fonts in all screens...** (above the screen list; a Data
  Editor job, about a minute; each changed screen put in its .dat folder) fill whole fonts on request. On the
  user's set, 50 of 82 screens got letters; the main menu title font all 80 it lacked.
- **Transform** (the gizmo round a selection: corners scale, Shift keeps proportions, the knob rotates; or X / Y /
  Scale / Rotation / Flip in the panel). A placement stored without scale or rotation gets a new packed matrix with
  them (flags set, pointer moved); 8.8 limits: scale below 128, steps of 1/256.
- **Pictures**: Replace... as 'made the old picture's size' (in place), 'its own resolution where the old one was'
  or 'its own size' (a new texture, 256 colours, the shapes showing it rescaled about their origin). **Import
  image...** puts a new picture on the screen (or the sprite shown, at its frame): a new bitmap, a quad shape, a
  place command at a new top depth appended to the frame's commands, the object list rebuilt two longer; the
  vtables and GS packet are copied from the screen's own.
- The view's pictures are fetched with a per-view token: a replaced picture showed the browser's cached old copy
  after saving (the file itself was right).
Unchanged saves stay byte-identical (all 78). Growth has not been tried in the game yet.

**Convert images…**: `.png` to `.tex` (PS2) with the app's transparency kinds (the image's own, dark background,
one colour, brightness mask, book page), format, mipmaps, power-of-two resize; into `Workbench\Textures` or the
selected .dat folder.

**Settings** page: the discs and PCSX2 (changed in App setup), the Data Editor's folders with Open buttons, the
build layout, and the edits waiting for a rebuild. No compression switches: the original's settings are always used
(they are what keeps loading as fast as the unedited game).

### Kept apart from the Studio's own edits

- Before an archive is first injected, it is saved exactly as it is on the working disc (Studio edits included):
  its **base** (`data_editor/bases`). Every later build starts from the base, so earlier Studio edits are kept.
- Restore defaults puts the base back; added archives come off the disc; with nothing of the Data Editor's left, the
  disc goes back to its size before it.
- An archive the Studio changes after an injection is **left alone** by later builds (⚠). **Keep the Studio's
  version** makes the disc's copy the new base.
- **Reset to stock** copies the stock disc over the working one. Its sheet has *Also take the Data Editor's mod
  files out* (on by default): the mods folder's files (an edited .pck put in front.dat, ...) are kept as a Saved Mod
  "Before reset <date time>" and the folder emptied. Left in, they count as waiting after the reset and the next
  Build or Play puts them straight back -- which is how a reset once seemed not to revert a .pck edit. The
  Workbench is not touched (nothing in it is built).

### Layout

Archives are written with `dave.create` (the engine's name order) and laid out by the RDR Data Editor's
`game_layout`: each file 32 bytes after the last but never across a 2048 byte sector, the data in the original's
order, each file compressed or stored as the original's, whole sectors. Every archive can be built, banks.dat, pupshow.dat and
streams.dat included: names compressed, files stored (as the originals), at the original's offsets. The packing is
read from the original (banks.dat packs at 16 bytes, the rest at 32; the streams' files all start on a sector), so
the layout reproduces all 310 / 358 / 8,361 files' places; tested by rebuilding each, every file read back identical.

## No room? Queued, and rebuilt on the next Play

`Studio.write()` still patches in place whenever an edit fits (or, with *allow files to grow*, fits a free gap).
When it does not, the edit is **queued** (`rdrstudio/pendingrebuild.py`, kept in `data_editor/pending/`, so it
survives a restart):

- the page shows *"<archive> has been updated and will rebuild on next launch"* (the server marks the response with
  `X-RDR-Rebuild-Queued`, whichever tab made the edit);
- the Studio reads the new bytes back at once (`Dave.overlay`), so every tab shows the change;
- the top bar's **Play** turns into **Rebuild and play**: one click rebuilds each queued archive once, with every
  queued file in it (`rebuild_pending`, a job the page polls), then starts PCSX2;
- Ctrl+Z works as before: undoing to bytes that fit writes them in place and takes the file out of the queue;
- a new file with no room (Files tab) is queued as an add; Reset to stock clears the queue; any rebuild of an
  archive (Add Character, a later overflow) takes that archive's queued edits along.

`Studio.rebuild_on_overflow = False` brings the old refusals back.

Cost of the rebuild: a level archive in seconds; swat.dat (47,000 files) in about 3 to 4 minutes.

## Characters

- **BALANCE > Creatures > Add Character…** copies a character's files under a new code into SWAT.DAT (one rebuild of
  SWAT.DAT, at once: the new character has to exist for the other tabs). Not undoable; Reset to stock removes it.
- **Whole character (.rsc)** above a character's parts: **↓ .rsc** / **↑ .rsc** for the RDR Blender add-on, beside
  the per-part OBJ round trip. A bundle bigger than its space is queued for Rebuild and play.

- **Add Character** asks where the model comes from: **Copy from an existing character**, or **Use an .rsc from my
  files** (the Blender add-on's `.rsc` + `.rnt`; its mesh names become `<code>_lod_01` ...; skeleton, animation set
  and stats come from the character picked, so export the model on that skeleton).
- The character views (Creatures, the level cast, cast thumbnails) put each of a bundle's textures on its own
  triangles (`groups` from the `character` / `level_characters` routes); they used to paint texture 0 everywhere.
- PS2 palettes padded past 1,040 bytes (the add-on writes 1,152) decode as PS2 palettes (`texture.parse_palette`:
  byte 8 is 4 for PS2, 0 for the Xbox form).

- Add Character's *Use an .rsc* also takes the model's `.skel` (the bone counts of model and skeleton must agree,
  else it is refused: a 52-bone model on a 59-bone skeleton is what broke a disc) and its `.tex` files. Every file of
  a new character, and the `.tex` picked, is copied to `Workbench\<code>\`.
- With *Use an .rsc*, the rest of the character is still copied from a character you pick (**Copy the rest
  from**), kind by kind (**Copy these**: tuning .cr, skeleton, damage, hit spots, motion, rep, spheres, collision,
  ars, tail, accessories). A character file of your own picked with the .rsc (a .cr, .skel, .dam ...) is used instead
  of the copy (its FileNameRender / FileNameSkel are pointed at the new code). A .cr and a skeleton are required.
- **Delete Character…** (Creatures): custom characters only (one the unedited disc has is refused). Its files come
  out of every archive; files of it in the Data Editor's mods folder go to the Workbench and out of its base copies,
  so a later Build does not bring it back. A job; not undoable with Ctrl+Z.
- The Animation tab's model and skeleton lists are keyed by each archive's table, so new characters show at once.

## Level editor

- Edits that outgrow their level file (moves, new collision nodes) and custom models bigger than the original's
  space are accepted and queued for Rebuild and play (`Level.grow_by_rebuild`), like every other "no room" edit.
- **Edit collision on its own** (a model's Collision panel): a collision shell can be selected and moved by itself,
  and a model moves without its shell (`move` with `alone`).
- **Add collision box…** (Collision panel): a new solid or walk-through box anywhere (`collision_add`, built from the
  proven `custcoll` box), starting around the selected object. A name matching a model ties it to that model.

## Studio edits in the Data Editor

Every file the Studio writes (an edit, an undo, a queued rebuild, Add Character's files) is copied into its archive's
.dat folder in the Data Editor (`DataEditor.mirror`) and marked **Built**: it is on the disc already, so it is not
waiting for a Build. An edit back to the unedited disc's bytes removes the copy; Reset to stock removes them all
(`cache/mirrored.json` remembers which copies the Studio made). An archive the Data Editor injected follows these
edits instead of turning "changed". Every archive is included, banks, pupshow and streams too.

## Build (top bar)

**Build**, beside Play, puts everything waiting on the working disc in one go: the archives queued for a rebuild,
then the Data Editor's mod files not built yet. Greyed out when there is nothing (`disc_state.build_waiting`,
recounted only when the mods folder changes, and not while PCSX2 has the disc).

## Disk space

The Data Editor keeps the extracted copy of each archive it rebuilds (so the next edit starts at once), but only the
newest one: the copy it was rebuilt from is removed, and the built archive file goes once it is on the disc. A
rebuild first checks for about 4x the archive's size free on the cache's drive and says so if there is not.

## App setup

App setup (the top bar's **Settings**) now lists the folders too: Mods, Checkpoints, Exports and Data Editor, each
with Open, Change… and Default (the Studio restarts to use a new one), and opens the working and original discs'
folders.

## Tested

On a scratch copy of the working disc: build + inject, Restore byte-identical to stock; an overflowing save queued
(instant), read back through the overlay, the disc unchanged until `rebuild_pending` (FRONT.DAT, 1 s), the queue
surviving a server restart, Ctrl+Z taking it out; every `de_x_*` route (Workbench folders, upload with sub folders,
paste, rename, copy to a .dat folder, delete new files, new file, Recycle Bin removal, saved Mods save / export /
import / load / delete, HUD text open + unchanged save byte-identical + new two-line entry put in swat.dat, Convert
images, Export tomb.dat in full: 1,749 files).
**In game (2.0.0):** discs rebuilt through Build load and play: two new Showdown maps (New .dat), a custom player
character, edited and grown Flash screens, replaced sounds and streams.

## Added in 2.0.0

- **`.dat` folders that remember where they go.** *Export .dat folder (.zip)* (toolbar and a folder's right click)
  saves a `.dat` folder's files with `rdr_folder.txt` inside, naming the disc folder it belongs in (`lvls_sng`,
  `lvls_m2`, `lvls_m4`, or `.` for the top). *Import .dat… > A .dat folder exported as a .zip* (or a dropped .zip),
  *A folder of a .dat's files*, and a Workbench folder copied into the mods folder all go where that file says.
  Without one: the game's own folder for that name, else `lvls_m2` / `lvls_m4` for a name ending in 2 / 4 (a
  Showdown map's pair), else `lvls_sng`. Each import says where it went and why. The build never packs the file.
- **A sub-folder inside a game `.dat` folder goes in at the archive's top**, logged. No archive of the game has
  folders, and a character added as `gbw/gbw.cr` was never found (an endless load screen).
- **A Showdown pair filed under `lvls_sng`** is named in the build log, with how to move it.
- **Add from game > Copy into: the Workbench** (`From <archive>`), besides the `.dat` folder.
- **Showdown map name and sub text**: the level editor's *Name in the Showdown menu* (under Export whole level)
  edits the map's row in both copies of the menu; Build puts it on the disc.
- **Flash screens, Audio and Streams**: see [RESEARCH/FORK-CHANGES.md](RESEARCH/FORK-CHANGES.md) sections 2 and 4.
- **Build complete** / **Build failed** lines in the activity log.

## After 2.0.0

- **The Showdown select screen** (`characterlineup.swui`, front.dat and swat.dat): the background
  `ui_characterlineupl24b.tex` + `ui_characterlineupr24b.tex` (two 512x256 halves, rows top first), then each roster
  character's `ui_mphoto_<code>.tex` drawn upright (128x256) at `<POSMARKER:x,0,0:sx,sy:hotspot_<code>>` in file
  order; `(x + sx, sy)` is the face the pointer goes to (drawn for all 48: every point is on a face). A roster
  entry without a hotspot is neither drawn nor selectable -- which is why a character put in Red's slot showed
  nowhere and Red was gone. **Add to Showdown** draws this screen and places the character on it (drag, size, click
  the face); a **fresh entry** adds its own roster row, unlock row and hotspot (the game ships 48: a 49th is not yet
  seen in game); **in place of** an entry takes that entry's hotspot too. **New Showdown character…** chains Add
  Character (own .rsc / .rnt / .skel / .tex / character files) into it.
- **Add to Showdown…** (Creatures; also offered at the end of Add Character). A Showdown character is m + its code
  (48 of 48 shipped entries), with its own m<code>.cr; its name (swat.strtbl) and portraits (mp_<code>.tex 128x128,
  ui_mphoto_<code>.tex 256x128, in swat.dat and front.dat) go by the code without the m; mpcharbounty.csv is the
  roster, unlockables.csv its unlocks. The dialog fills all of it in: it takes one roster slot, copies the versus
  files (.cr .ars .dam .msd .rep .sphs .swt) of the Showdown character on the same skeleton that matches best (its
  story .cr closest to the new character's -- gbw, copied from mrk, gets mmrk's), points the .cr at the character's
  own model, writes the name in every language, and converts the portraits you give (else the slot's for now). A
  roster entry under a bare code (the crash: the game read "gbw" as "bw") is offered as the slot to repair.
- **Texture fit**: a .tex dropped on a .dat folder, or copied there from the Workbench, over one of the game's of
  another size, format or mip count opens a crop window (locked to the game's shape); the part kept is made that
  size, format, mip count and flags (`de_x_tex_target`, `de_x_tex_fit`).
- **Convert images**: Crop… on every picture, in every mode (a preset's page shape locks it); **mp photo** preset;
  the two ink options merged into one with an ink colour.
- The build log warns about a texture that differs from the one it replaces, and roster entries breaking the rule.
