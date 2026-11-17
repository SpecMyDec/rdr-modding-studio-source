# Changelog

## 2.1.0 -- 2026-11-17

- **Build (Xbox) builds into the Xbox game's own files**, as the PS2 build builds the working disc: each changed archive of the recomp's game files (assets) is rebuilt from its unedited copy ("unedited assets", backed up the first time) with the mod's files in it, laid out as the game's own (each file stored or compressed as the original's -- the Flash screens are read as stored, no file across a sector). An archive no longer edited goes back to its unedited copy; Reset to stock (Xbox) puts them all back.
- **Builds do only what changed**: the working disc is compared with the stock disc only where an archive changed since the last build (a CRC per archive), an archive whose result is the same is not written again, and Play on the Xbox builds first, then starts the game.
- **Converted files** (Workbench > Converted files > To Xbox / To PS2): every file a build makes the other game's is kept there and used again the next time the same input comes up, so each is converted once. A PS2 file that is the PS2 game's own (in any archive) becomes the Xbox game's own file of that name, unconverted (a conversion would lose what only the Xbox's has: a character's face morphs).
- **Replace All Characters** (Creatures): every humanoid character becomes a copy of one character -- its .cr points at that character's model and skeleton (also when ".cr" is unticked), its model, face, skeleton and LOD files go into every archive that loads a replaced character, and the skins (<model>01-04), the Showdown pictures (mp_ / ui_mphoto_) and the ticked character files (.cr .swt .msd .dam .hot .sphs .rep .ars .tail .acc) are copied. Humanoid: every standard joint, in any order (the women included; animals left alone); male / female (.swt Male) stays each character's own; a coattail or accessory file naming bones the new skeleton has not is emptied. It writes to the mods folder (an earlier run's files taken out first), so Build puts it in either game. Replaces "Replace model (.rsc / .xrsc)".
- **Script Hooks tab** (beside My mod): the code the Studio puts into the games, each on or off per game -- the trainer (Noclip, Freeze World, Camera Lock, Slow-Mo on the PS2 / Free Cam on the Xbox, by controller combos) and the unlock of maps and characters mods add (now on the PS2 too). The trainer's controls are mapped there (PS2 button names with the Xbox's beside them, or pressed on a controller). The second lineup, its background, own textures for players 2-4 and Coliseum on the PS2 are always on.
- **Players 2-4's own textures work on the Xbox**: the recomp draws the creature with its model's materials and each texture's alternate (the draw that binds one texture for every part is sub_00274090).
- **Logic > Missions**: the bottom half is the mission's level enemy roster (.enm) -- every enemy, and its behaviour .tbl, weapons, drops, skin and the rest in the same editor as the 3D editor's, Apply beside the enemy count.
- **Stats > Weapons & stats > Weapon model**: Export .mod / Export .xmod, and Import .mod / .xmod (an Xbox model made the PS2's), checked against the space of every copy first.
- Xbox game crashes fixed on the way: a character's tail / accessory naming bones its skeleton has not, a converted character bundle without its face morphs, a rebuilt archive with its Flash screens compressed.

### Also in 2.1.0 (worked on since 2.0.0)

- **Xbox text formats convert for the PS2**: .xfilm, .xhud, .xlsh, .xshadows, .xmmb, .xflares, .xrain (read from every file of each type on both discs: the same syntax and key order, values tuned per platform). An edited one is merged BY KEY onto the PS2 game's own file (the mod's changes placed, the PS2's tuning kept: 124 test edits); an Xbox-only one keeps everything but the keys the PS2 never reads (.xshadows' ShadowAlpha / ShadowBase, .xrain's Lightning_Flash_*). Converted on their own, with a level, and in whole-mod conversions.
- Xbox level textures in the 8-bit formats without a palette (A8, L8 -- the levels' sun00 glow) are read and converted (they were left in the Xbox's format; converted Xbox Ranch hung loading). Fixed in every copy of the format reader. Animated level objects (SKE0 / SAN0) are the PS2's data plus the Xbox's padding: kept as they are.
- Data Editor: the Xbox game's own levels the PS2 disc has not (Coliseum: lvls_m2/colseum2.dat, lvls_m4/colseum4.dat) are base game folders, "(Xbox game)", whether a mod has their files yet or not.
- The Xbox build carries the Studio's edits from the working disc (checked end to end: an edit to srd.swt came out in the recomp's mods folder), and now puts the queued ones -- edits that did not fit their place, waiting for a rebuild -- on the working disc first, whichever Build starts it.
- **Reset to stock knows both games.** With no Xbox backup it is the PS2 reset, as before. With the unedited Xbox game files backed up ("unedited assets"), it asks which: PS2 (the working disc copied again from the stock disc) or Xbox (the recomp's game files put back as the backup has them -- a changed file copied back, contents compared; one the backup has not to the Recycle Bin -- and the Studio's folder taken out of the recomp's mods folder). Either way the Data Editor's mod files are kept as a Saved Mod and emptied.
- Add to Showdown: the name shown can be two lines, stacked (as the game's own "Pig" over Josh): a second line field under the first.
- **Players 2-4's per-texture alternate looks on the Xbox.** The Xbox build names each alternate after the texture it replaces (gbw02_vest.png.xtex, from the Showdown editor's gbw02_<k>.tex: k = the texture's place in the order the model's parts use them); the recomp's new alttex hook (RDR-XBERecomp src/game/alttex.cpp: the creature's alternate lookup 0x1EB3D0 and its draw 0x96DB0) looks each texture of the model up by that name when the game finds no single alternate, and draws the creature with those swapped in -- the textures with none stay the model's own.
- App setup (Settings): Xbox recomp -- the folder with the recomp's RDR.exe, and its game files (the Xbox side's working disc) -- each chosen in the system's folder dialog.
- Data Editor with Game: Xbox: a file's built / changed / new is against the last Xbox build (it keeps its own record), so building for the Xbox clears them; the PS2 build keeps its own.
- The Xbox build says what it cannot carry yet instead of writing it where the game never looks: Flash screens (.pck -- the Xbox's are .xck, the same objects at Xbox addresses with its own picture formats).
- Game: PS2 is blue over the theme's accent (Xbox green).
- Fixed: declining the Xbox backup closed the app (the window's own confirm dialog). It is asked in the page now -- Back up now / Not now / Don't ask again (only that one is remembered) -- and so is the recomp's place.
- The first time the Xbox game is edited (Game: Xbox), the Studio asks to back up its game files: the recomp's assets copied as they are to "unedited assets" beside them (a job, its progress in the log bar), which becomes the unedited Xbox game the Studio compares against when none is set. Declined once, it is not asked again.
- The recomp's state (RDR.exe ready / running) is in the status bar where PCSX2's is on the PS2 side; Game: PS2 is blue, Game: Xbox green. The Xbox side's levels are named as the game's own (COLSEUM2, COLSEUM4).
- Levels > Export whole level: Format -- the 3D model set as before, or the level's own files for the PS2 (.rsc, .znes ...) or made the Xbox's (.xrsc, .xznes ...), zipped. An Xbox level the PS2 disc has not (Coliseum) exports the Xbox game's own files (the mod's where it has them); a level of both games is converted as the Xbox build converts it (unchanged files: the Xbox game's own).
- **The Studio reads the levels of both games.** The Xbox game's own levels the PS2 disc has not (Coliseum) are listed with the game's Showdown maps (Showdown 2P / 4P), from the mods folder when a mod has them, else read from the Xbox side and made the PS2's in the cache (as an import does: about a second, once); levels only the recomp's game files have (an older tool's maps) are listed as custom (M, Xbox).
- Settings: Xbox working disc -- the recomp's game files (its assets folder, by default beside RDR.exe), and the recomp's RDR.exe.
- Showdown editor: the full portrait is placed in its 128 x 256 frame by hand -- a new picture opens the placer (drag it into place, the mouse wheel sizes it), and Adjust in its frame… does the same with the one it has.
- Build (top bar) says which game it builds for -- Build (PS2) / Build (Xbox) -- and is always there (PS2: the queued Studio changes and the Data Editor's build into the PS2 game). The Data Editor's button is Build into the PS2 game / Build into the Xbox game.
- Levels: a new level in the Data Editor's mods folder that the working disc does not have yet -- an imported Xbox level like Coliseum (the Xbox game has it, the PS2 disc not), or a New .dat map before its first Build -- is listed with the others ("(M, mods folder)") and opens from its folder, so a mod made on the Xbox side shows its Coliseum on either side. A Build puts it on the disc, where it is edited like the others.
- Showdown editor: the pictures are called Full portrait (ui_mphoto_) and Mugshot (mp_), in Add to Showdown too, and Add to Showdown has Edit… for a character already in Showdown (the Showdown editor opened on it).
- **Build (top bar, beside Play) builds for the game the picker says**: PS2 -- the .dat files queued with Studio changes, then the Data Editor's build into the working disc; Xbox -- those queued files first (so the Xbox build sees them), then Build into the Xbox game. No more full-screen wait: the build runs behind the app, its progress in the log bar at the bottom, a green Done! when it finishes. Meanwhile everything can be looked at but nothing changed: write buttons are greyed out and requests that would change something are refused ("A build is running") until it is done -- the Data Editor's own Build too.
- Showdown editor: Pictures -- change the select screen photo (ui_mphoto_) and the portrait (mp_, which the end-of-match mugshot is made from at the next Build), each with its transparency; the photo can take the other photos' colouring. The portrait's "Colour like the other portraits" is gone (Add to Showdown too).
- **Fixed: PS2 characters converted for the Xbox had holes.** A PS2 mesh's strips read back with about a sixth of their triangles wound the other way (the PS2 draws them anyway); the Xbox culls back faces, so they vanished (gbw: 24% of 14,062). Every triangle is now turned to face the way its normals do when a model goes to the Xbox (modconvert.face_out; gbw 0%, as the shipped characters).
- Fixed: the Xbox build wrote a file twice when the mods folder keeps it in a subfolder of its .dat folder (swat.dat/gbw/...): the working disc's copy (the PS2 build puts subfolders' files at the archive's top) was converted again and, being shallower, took over.
- Add to Showdown: "Versus tuning from" lists every Showdown character, alphabetically by name (Red included); one on another skeleton is marked, and the best skeleton match is still the default.
- Add to Showdown: the voice line's "A stream of the game's" has Browse streams…: search the streams by name or by what is said, listen, and Use one.
- The Data Editor's status bar (status line, Cancel, Log) is under every tab now, and its "Game: PS2" is the PS2 / Xbox picker (it replaces the status-bar switch). A running build's status keeps updating on other tabs.
- Media > Cutscenes is called Videos/Pictures.
- Body & Rig edits .swt too: every field of the SWAT creature type (557 in a typical file), the Creatures tab's 40 first in their groups, the rest grouped by name.
- Logic > Cast > Behaviour tables lists every table (SWAT.DAT's 2,705; it stopped at 400).
- Deleting asks Recycle Bin or Delete permanently (Data Editor Remove, Delete new files, Workbench Remove, saved Mods). Data Editor Remove used to delete outright although it said Recycle Bin.
- **The PS2 / Xbox switch (now the Game picker in the status bar).** One mod, both games: on Xbox, the Data Editor's Build makes the mod the Xbox's files and writes them into the recomp's mods\RDR Modding Studio folder (the Studio's own, written again each build; nothing else in mods is touched), and Play starts the recomp's RDR.exe (found at Desktop\RDR.exe, or asked for). File by file (rdrstudio/xboxmode.py): an Xbox file the mod brought in goes as it is unless its PS2 copy was edited after the Studio made it (the Studio now stamps every PS2 copy it makes from an Xbox file); a PS2 file that is only the PS2 game's own (or the base map's filler in a new archive) is left out; everything else goes through the converter (textures, models, bundles, levels, text merged over the Xbox's own); a result the same as the Xbox game's file is left out. Edits the Studio's other editors made on the working disc go too (the mod pack's comparison with the stock disc). The recomp matches loose files by name, so a level archive with a file other archives also have (each level's data.plyr: the same in all 64) goes as a whole archive, as does an archive the Xbox game has not.
- Reading many files from one archive is much faster: an archive's table is parsed once while it is among the last eight read (ps2build.read_entry; the Xbox build went from 89 s to 11 s).
- **Converted Xbox levels play their own sounds and music.** A level's sounds are its snd_cfg_<level>.csv (SndBegin N: bank snd_N in banks.dat) and its lists snd_N / snd_<N+98> (ambience streams) / snd_<N+99> (music streams). Every bank an Xbox level carries as .xbd / .xhd is in the PS2's banks.dat but three (snd_5900 / 6000 / 6100: shernit, ghost, coach), which the Xbox's own banks.dat holds in the PS2's format; every stream the lists name is in the PS2's streams.dat; the PS2's swat.dat even keeps Coliseum's whole setup. A converted new level now gets the PS2 disc's copy of its sound files (or its own when the mod edited them), a missing bank copied from the Xbox reference into banks.dat; only a bank on neither disc falls back to the base map's sounds. Coliseum: its own bank 4900, ambience and music (FreakShow, RdwlfMineB, Bullseye) instead of Ranch's.
- Xbox music and speech streams: the Xbox's stream0-4.dat hold the same 8,361 .stm files as the PS2's streams.dat, byte for byte. An imported stream0-4.dat now goes into streams.dat (its edited streams kept, the rest left out as the disc's).
- Xbox banks.dat's .hd / .bd are Sony's own format (IECS headers, PS-ADPCM): the same-name ones are like any other same-format file. .xalpha (the Xbox renderer's alpha-test settings for vegetation zones) has no PS2 counterpart and no code reading it: noted, never packed.
- **Xbox mods: files both games have under the same name.** Every such pair on the two discs (531: .cam .arfx .plyr .csv .bnds .swui .nav .actb .stateb .strtbl ...) is the same format with values tuned per platform; an Xbox .dat's EDITED copy now gets its changes laid over the PS2 game's own, key by key (modconvert.same_name_to_ps2), instead of bringing the Xbox's tuning along. With no Xbox reference set, the only keys no PS2 file uses are taken out (.plyr's second gun-aim ramp, four .arfx keys, .veh StartRail). Binary ones are kept as they are.
- **Xbox mods of a shared level keep the level whole.** When an imported Xbox .dat edits a level's geometry (lvl_<name>.xrsc), every file of that level's place stays the Xbox's, unedited ones and ones identical to the disc too: its zone, nav and bound files match the Xbox's chunk numbers, which the converted bundle keeps (PS2 Ranch's differ by up to 5). Bound files of a converted level take the Xbox field (325 of the PS2 disc's 4,039 bound references differ: train, traingoo, saloon).
- **Converted Xbox levels get their ground and wall shadows.** An Xbox level mesh can carry a second texture layer, a baked shadow map on its second UVs (Ranch's ground: ranch_GRNDshadow2, the barn: ranch_BSshadow), drawn in one pass. The PS2 cannot, so its own files have a copy of each such mesh drawn over it (zone node <name>_2p_NN): every triangle black, the layered ones textured with the shadow map on their second UVs (material colour 0.8), the rest untextured. The converter now makes these second-pass meshes (added at the end of the bundle, so every other chunk keeps its number) and their zone nodes. Converted Xbox Ranch gets the same three PS2 Ranch has (UVs equal to within 0.01). ps2rsc.mod0 gained `diffuse=` (a colour block per material), in every copy.
- Xbox levels converted by an older version are converted again by the next build (modconvert.level_outdated: animated meshes written static, shadow layers with no second pass). A PS2 bundle of the level's own is never touched.
- **Converted Xbox levels keep the PS2's shadows and shading.** The PS2 bakes its shading and shadows into the level meshes' vertex colours; the Xbox's copies are flatter (lit at run time: on Ranch 8% of the corners are near black on the PS2, 0.93 on the Xbox). When the PS2 disc has the level, each converted mesh is paired with its PS2 twin and every triangle the PS2 has takes the PS2's colours (Ranch: 9,216 of 9,216); a mod's new or moved triangles keep the Xbox's. Textures in a format both games share (all but DXT1/3/5: 1,417 .xtex/.tex pairs byte-identical) are now copied as they are, not re-encoded to 256 colours (the levels' shadow boxes came out changed).
- **Fixed: converted Xbox levels with animated objects black-screened after Start** (Ranch's windmill blades, doors, the train ...). Their meshes are skinned (MOD0 bone count > 0) and were written as static level meshes. They are now written as skinned PS2 meshes, in world space with float positions and the skeleton's full bone count, as the PS2's own are: all 80 in the 28 Xbox level bundles convert to the PS2 disc's own geometry exactly (ps2rsc.mod0 gained `floats=`, in every copy).
- **Importing a .dat the mods folder has already** (dragged from Explorer or the Workbench) asks: Replace (the old folder's files to the Recycle Bin, then the new .dat's) or Merge (the new files over the old, files only the old one has kept) or Cancel.
- Fixed: Restore defaults could list nothing -- it showed the page's last copy of the build record, taken before a build, import or reset; it reads the record afresh when it opens.
- **Import a .dat by dragging it from the Workbench** onto the mods folders (as a .dat dropped from Explorer is: unpacked, placed, Xbox files converted). A name ending in 2 or 4 asks whether it is a Showdown map's half for lvls_m2 / lvls_m4 (else the folder dropped on, or lvls_sng); Explorer drops ask too. A .dat folder of files dragged from the Workbench is still copied as it is.
- **Fixed: converted (and Blender-exported) PS2 levels drew black or as noise.** Two more writer faults, found with a test harness that boots the game in PCSX2 and bisects the level (all meshes emptied: the game drew; meshes 0-2 alone: black): (1) every shipped level texture has 0x00010000 at TEX0 +0x70 (494 of 494; character textures: 0) -- the writer made them the character way, so they drew as noise and the gameplay view stayed black; (2) mesh batches are padded to end 8 bytes past 16 with their REAL length stated (the original padding was right; only its stated 6n + 36 was wrong). Fixed in the Studio's writer and every copy of the add-on's. The build re-converts a level the Xbox converter made before and warns about any lvl_*.rsc the game cannot draw (re-export old Blender levels with the updated add-on). **The Coliseum now plays on the PS2.**
- **Fixed: a converted (or Blender-exported) PS2 level could hang the game while loading.** The game walks a level mesh's batches by the length in each batch's first qword (6n + 36 words; 0x3B05D8 expects the next batch's STMASK there); the level writer padded to 16 bytes from the stream's start instead, so every batch with an even vertex count came out 8 bytes short, the walk lost the rest of the mesh and the game hung reading its vertices (Coliseum: 41 of 130 mesh parts; found from a savestate of the hang, pc 0x3B02F0). Fixed in the Studio's writer and in every copy of the add-on's (installed Blender add-on, RDR-ModTools, the toolkit, the GitHub folders). A level the Xbox converter made before is converted again by the next build, and the build warns about any lvl_*.rsc in the mods folder the game would hang on (re-export it with the updated add-on). The shipped levels pass the same check.
- Faster Xbox imports and conversions: the Xbox game's archives are opened once for the reference comparisons (a Coliseum half: 50 s -> under 1 s).
- **Fixed: The Coliseum never showed on the PS2.** The game's "is this stage available" check (0x131E08) turns lvl_colseum down by name (strcmp, then "not available"), whatever the menu table, unlocks or save say. A new automatic code mod (Coliseum On The PS2: one branch at 0x131E48 made unconditional) is put on by the Data Editor's build when the mods folder has Coliseum's map.
- **Cancel a build** (the Data Editor's status bar): it stops at the next safe point -- between steps, between archives, in the Xbox conversions -- and nothing is written to the working disc. Once it writes the disc the button is greyed: that part finishes.
- The build's log names every automatic code mod it puts on or takes off the executable.
- Fixed: the Studio loaded no data with a freshly imported Xbox map in the mods folder. The Data Editor's folder overview converted every Xbox file inside the page's request (minutes for a whole map); the page asked again and again, the conversions piled up on the same files and every other request waited behind them. The sweep now runs on a thread of its own, one at a time, and an import or a build waits for it instead of racing it.
- Fixed: Convert images opened empty (a script error: its Crop button was used before it was made).
- **Xbox levels convert for the PS2 with no Blender** (Coliseum, a mod of any level, a new map): a level folder holding lvl_<name>.xrsc is converted when it is imported and by every build. Read from the two games' copies of the levels they share: the level's text files are the same on both, the bundles hold the same chunks in the same order with the Xbox's padded (BND0 trimmed by align16(u32@0x80 + 4*ceil(faces/32)), 404 of 470 shipped shells identical; PMT0 the PS2's 64; view/nav as they are), so every chunk keeps its number: meshes and textures encoded for the PS2 (modconvert.convert_level_xonly), zone files given the PS2 field, bound files too. A map the PS2 disc does not have first gets the PS2 base a new map gets (Ranch / the unused saloon: the files every map shares, sound banks, sound setup), its own place left out. Coliseum: 3.3 MB a half (the Blender route made 15-19 MB).
- **The Xbox game as a reference** (Data Editor > Settings > Xbox game): folders of the unedited Xbox game (its .dat files or extracted archives), indexed once. An imported file that is one of the Xbox game's own unedited files is not a mod: left out for an archive the PS2 disc has (its own is used), the PS2 counterpart for a new one (berryliumx_00.xtex -> berrylium_00.tex ...). This also catches Xbox files under PS2 names (banks.dat's Snd_N.bd / .hd are in the Xbox's audio format). Only what a mod changed is converted.
- Importing a .dat with Xbox files converts them at once (the level whole, then every other file), the Xbox files kept.
- Fixed: the Coliseum stage unlock (an "xbox" marker taken off unlockables.csv) only reached the disc at the build after; the mod files are listed again when it is written.
- **Showdown characters' textures at 128 x 128 on the disc, by default** (Showdown editor, a character's Alternate looks: two checkboxes, both on unless unticked): its own model textures and its players 2-4 alternates are made 128 x 128 at most when the build writes the disc (gbw's bundle 812 KB -> 516 KB), to stay inside the PS2's 4 MB of video memory with several custom characters. The mods folder keeps them full size, so unticking puts the full-size ones back at the next build (the options are part of swat.dat's build key). Alternates are now written full size.
- Fixed: per-texture alternates for three players filled the screen with noise -- 18 textures at 256 x 256 ran past the PS2's 4 MB of video memory (the last ones were placed beyond it). Now only changed textures are written and loaded (a missing number keeps the model's own), at 128 x 128 at most; the build removes plain copies and shrinks bigger ones already in the mods folder.
- Fixed: the Data Editor's build left an automatic code mod at an older version when the disc already had one of that name (it compared names only); it now compares the executable it would write with the disc's.
- **Fixed: an added Showdown character was drawn blank as player 2, 3 or 4.** For those players the game loads <model>02 / 03 / 04.tex (0x185340) and draws the WHOLE model with that one texture, skipping its own (0x21CB08 -> 0x3B6528(model, 0, 0)); with no such file it gets the blank default texture. A new automatic code mod (codemods_alttex, added by the Data Editor's build when the mods folder has a character model) drops the blank default, so those players wear the model's own textures. Characters with an alternate are unchanged.
- **Alternate looks for players 2-4, every texture** (Showdown editor, under a character's stats). A model with several textures: a grid of players 2-4 x each texture -- a picture, a recolour (hue / saturation / brightness, previewed) or reset per texture, "Recolour all" per player; each of player 1's textures can be saved as a PNG to paint on. Written as <code>0N_<k>.tex (k = the order the model's parts first use the textures; all of a player's written once any is changed). The alttex code mod (now in caves 0xFA000 / records 0xF6000) loads them when the character is made and draws the model with a part list of its own (both model draws: 0x21CB08, 0x21C790); a single-texture model keeps the game's own <code>0N.tex. Layout read from a savestate of a four-gbw match.
- Showdown editor: the Deadeye field (CharSpecialTable, only the select screen's label) is gone; the special move and melee special list every spmove the game has (55).
- Fixed: end-of-match mugshots were added to mprewards.pck, which the game never loads: the end screen is mprewards_new3.pck ("Rewards_new3", read from a savestate of the screen). The mugshots now go there (its clip is "bossList").
- **Showdown editor, stats**: click a character's photo on the select screen to pick them (no list); their stats underneath -- health, the dodges (Mr Kelly's, Pig Josh's, Dan's), weapons (one or two; the .ars follows), deadeye, special moves, bot brain, damages, and every other value of their .swt.
- **Xbox files are kept**: converting no longer deletes the Xbox originals; their PS2 copies are made beside them (a file that has its PS2 copy is left alone), and the build never packs an Xbox-only file into a PS2 archive.
- Fixed: the end-of-match mugshots still showed squares -- the screen's script copy pointed its code pointer at itself, so the game ran none of it. Fixed, and copies made before are repaired by the next build.
- **Showdown map halves placed by name**: a .dat named <name>2 / <name>_m2 goes to lvls_m2, <name>4 / _m4 to lvls_m4, on import (even dropped on the other folder) and before each build (one already in the wrong folder is moved).
- **End-of-match mugshots for every Showdown character** (mugshots.py): the end screen (swat.dat mprewards.pck)
  finds a player's mugshot by code in two fixed lists and showed a team-coloured square for any code they miss --
  every added character and 13 of the game's own (tcb, gwk, tck, ydr, mon, twg, mom, mdg, shp, msk, mrd, kti, elt).
  Each build now gives every character on the select screen without one its mp_ portrait as its mugshot: the
  screen's first script copied to the end of the file with the codes added, the mugshot clip given a frame per code.
  Nothing in the file moves. Not yet seen at the end of a real match.
- **In-match sounds** in Add to Showdown, beside the voice line: the character's sound set (its .swt
  CreatureSoundSet: voice, injuries, deaths ...) the same as another's, or **a new sound bank copied from another's**
  (the next free number, its rows renumbered, into swat.dat and banks.dat, to change in the Sound tab); and its
  footsteps (StepType).
- **Imported Showdown maps**: the build warns about a map half in the wrong folder (<name>2.dat goes in lvls_m2),
  a level with no geometry (an Xbox level's .xrsc has to be converted in Blender), and Xbox sound banks; and takes
  the "xbox" marker off the map's stage row in unlockables.csv (lvl_colseum), which made the PS2 game skip it.
- The packaged window opens at RDR_WINDOW_POS ("x,y") when it is set (test runs).
- **Showdown editor** (Creatures tab): the character select as it is -- move any character's photo and pointer,
  change its layer or its lineup, change either lineup's background, without adding a character.
- **Play waits for a build**: greyed out while a Data Editor build, a .dat rebuild or a Code Mods build is writing
  the working disc.
- **Fixed: no head shots on an added Showdown character.** Hit zones are <FileNameRender>.hot, the head zone is
  hot11 (186 of the game's 190 .hot files); a character made from Mr Kelly's files has none (mrk.hot), so a head shot
  paid as a body shot. Add to Showdown now adds the game's head zone (Red's) when the character has none and its
  skeleton has a head bone.
- **Second Showdown lineup**: a second page on the Showdown character select. L1 / R1 (any controller) pan
  between the two lineups with the "Move between books" sound. Add to Showdown has a Lineup 1 / Lineup 2 choice;
  the placer shows that lineup's background and characters, and layering works within it. **Change background**
  for either lineup (a picture, cropped, cut into the two 512 x 256 halves the game loads; lineup 2's appears once
  it has a character). A code mod (codemods_lineup.py) that the Data Editor's build puts on the working disc by
  itself while a character is on lineup 2, and takes off when none is; it keeps any other code mods. Seen working
  in PCSX2 on a disc built by the Studio: the lineup built from the file, the pan, the pick limited to the lineup
  shown. Lineup 2 is built once, with the game's own, so switching uses no new memory.
- **Fixed: an added Showdown character never appeared on the select screen.** The screen's list of characters is
  the `<FONTGRIDDATA:y:<code>:0>` lines of postermultiview.swui, not the roster table. Add to Showdown now writes the
  character there too (both copies), with the weapons, special and bot brain of the character its tuning comes from.
  The build warns about a roster character missing from that list.
- **Layer on the select screen**: the game draws the photos in that list's order (later over earlier), so the
  placer's front / back choice now is a layer slider (in front of X, behind Y) drawn live with everyone's photo,
  and both files take the character at that place, in one order.
- **Fixed: every Showdown character said the wrong line on the select screen after adding one.** snd_98.csv
  numbers each character's voice line by its place in the select screen's list, so one put in the middle moved
  everyone after it. The rows are now numbered by the list whenever it changes (and before every build).
  **Voice line** in Add to Showdown: none, the tuning character's, any stream of the game's, or your own .wav /
  .stm (added to streams.dat).
- **Colour like the other photos / portraits**: an option beside the transparency of Add to Showdown's pictures.
  It recolours a picture with the colour gradient and contrast learnt from the game's own 48, keeping its
  transparency.
- **Xbox files in the mods folder are made the PS2's** before every build and whenever the Data Editor lists its
  folders, however they got there. One with a PS2 copy already beside it is deleted instead (a duplicate bundle with
  its exported parts), and the build log lists what was done.
- **Faster Data Editor builds**: an archive is built in memory from its base and the mods, instead of through an
  extracted copy hard linked into a work folder and read back. swat.dat is 47,115 files, so every build touched
  ~190,000 files: about 27 s from source, and around three minutes in the packaged app. Now about 16 s, with the
  same archive out. The Studio's own "no room, rebuild the archive" edits work the same way. The extracted copies
  earlier versions left in data_editor/cache (base_*, one per archive version) are deleted in the background.
- Fixed: a file added from a subfolder of an archive with no folders (swat.dat/gbw/...) went in as gbw/<name>
  although the log said it went in at the top. It goes in at the top now.
- **Xbox files import as the PS2's**: .xtex, .xmod and character .xrsc + .xrnt dropped or picked anywhere (a .dat
  folder, an imported folder or .zip, the Workbench into the mods folder, Add Character, the Showdown pickers) are
  converted with the Data Editor's converter -- textures in the PS2 game's format for that name, with the image
  converter's transparency choices; skinned models moved bone relative with the character's skeleton; bundles rebuilt.
  An Xbox level's files are refused, with how to convert it (Blender: import the .xrsc, export Level as .rsc).
- Dropping a character (its .cr, or a model with no character files) into swat.dat offers to make it a Showdown
  character at once; the Showdown photo and portrait pickers open a crop window fitted to their textures.
- **Showdown select screen**: the character select is a group photo (characterlineup.swui: the town background
  plus every character's ui_mphoto photo laid at its hotspot, back row first; the hotspot is the face the pointer
  goes to). Add to Showdown now places the character there with a **visual placer** (drag the photo, size the
  figure, click the face for the pointer), as a **fresh entry** (all 48 stay) or in place of one (taking its
  hotspot). **New Showdown character...** starts from Add Character (your own model, character files and .tex).
- **Add to Showdown** (Creatures, and a tick box in Add Character): makes a character playable in versus with the
  game's naming worked out for you -- roster code m + its code, its own m<code>.cr with the versus tuning of the
  Showdown character it matches best (same skeleton, then the one it was copied from), the roster and unlock rows,
  its name, and its portraits (mp_<code>.tex, ui_mphoto_<code>.tex) in swat.dat and front.dat.
- **A .tex of another size over one of the game's** opens a crop window, then is made the game's size, format,
  mip count and flags (dropped on a .dat folder or copied from the Workbench).
- **Convert images**: Crop... for every picture and every mode; an **mp photo** preset (ui_mphoto_, 256x128, in
  colour, stored on its side as the game's are); one Ink transparency option with a black / white ink choice.
- The build log warns about a texture that does not match the one it replaces, and about Showdown roster entries
  that break the game's naming.

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
