# Code Mods — noclip, freeze world, camera lock (hooks from community reverse-engineering research) + Cheats Menu Unlock, Slow-Mo, position readout (the reviewer, 2026-11-08)

Status date: 2026-11-07. Lane: The reviewer (integration). **Credit: the hooks, addresses and approach come from community reverse-engineering research** —
his "community research v7" (`make_noclip.py`, `make_elf_patch.py`, `make_menu_patch.py`, `menu_layout.py`,
`asm.py`, `HOW_IT_WAS_BUILT.md`, `NOCLIP_SETUP.md`). Ours is the port into the Studio, the checklist, the address
check against our own unpack, and the proof.

Code: `03_Tools/rdrstudio/codemods.py` · tab: PROJECT ▸ **Code Mods** (`web/codemods.js`) · CLI:
`py -3 scripts/build_codemods.py <source.iso> <NEW dest.iso> [noclip,freeze,camlock]` ·
test: `scripts/test_codemods.py` (in the suite) · in-game driver: `scripts/codemods_live.py`.

## What it does

| Mod | What | Controls (also a row on the Cheats page) |
|---|---|---|
| Noclip | no gravity, no collision, the camera follows; flight is level to the camera | L3+R3 · stick flies, X up, Square down, R2 ×4, L2 ×¼ |
| Freeze World | every character but you skips its whole update (no move/animate/AI/shoot) | R3+Triangle |
| Camera Lock | the renderer gets a snapshot of the camera while the game's own camera keeps following you | R3+Circle |

Any subset can be built. Everything starts **Off** at every boot (nothing is saved to the memory card).

## How the build works (no aPLib encoder needed)

1. `SLUS_205.00` is one PT_LOAD segment: a boot stub at `0x00800000` + the aPLib/rolling-XOR packed game. The stub
   unpacks to `0x00100000` and does `j 0x00100008` at `0x00800020`. We **do not repack**. We append an installer
   (and a table of every word to write) after the section headers at file offset `0x1BE500` (`0x009BD500`), grow the
   segment, and point that one jump at the installer. At boot it copies the caves to `0x000FE000..`, writes the hook
   words into the freshly unpacked game, calls `FlushCache(0)`/`FlushCache(2)`, then jumps to `0x00100008`.
   (This is the "patchable without repacking" route C-040 predicted; see C-042.)
2. The patched ELF is ~1.8 MB larger than its disc extent (the IOP modules start right after it), so it is
   **appended at the end of the ISO** and its ISO 9660 root record + the volume size are repointed. The disc's UDF
   bridge still lists the original ELF (see snags).
3. The game reads `cheats.swui` from the archive of whatever level is loaded, so the page is replaced inside
   `swat.dat` and all 61 level archives (`lvls_sng`, `lvls_m2`, `lvls_m4`) in place, in its existing slot (smallest slot
   512 bytes), so no archive changes size. We deflate with zlib (best of 36 settings) or Zopfli if installed; labels are
   kept short ("Noclip", "Freeze", "Cam Lock") so zlib is enough (all three rows: 511 of 512 bytes).
4. Output is always a **new file** `02_Modded/RDR_CODEMODS.iso`; it refuses to overwrite its source, to write into
   `01_Vanilla`, or to build on an ISO that is already code-modded. A build is verified afterwards by reading the result
   back (mods present, Cheats page replaced in 62/62 archives).

## Address check against OUR unpack (done before anything is built)

Our `elfpack.unpack_elf` of the vanilla executable = the tester's `SLUS_205.00_unpacked_0x100000.bin`, byte for byte
(4,497,384 bytes, sha1 `68762fd2063624fe92718ef41b79c9c49c7747de`). `codemods.verify_addresses` then checks, and
`test_codemods.py` asserts:

| Address | Check against the unpacked image |
|---|---|
| `0x001433B8` | `jal 0x001431B0` (pad update) — the input hook site |
| `0x00152ED0` | `jal 0x003BFBB8` (camera matrix → renderer) — camera-lock site |
| `0x002C3E10`, `0x00268628` | Mover::Update / per-character update: first two words are not branches, follow a function end |
| `0x005027BC` | cheat-page vtable slot holds init `0x001228C0` |
| `0x001226D8` | change handler is `bnel s1,v0` / `ld s0,0(sp)` (`56220004 DFB00000`) |
| `0x00269C08` | world-shift function: listed in 1 vtable (called virtually) |
| `0x00104E88` | cheat-row finder: 18 `jal` callers |
| `0x0079E240`, `0x0079EEE4`, `0x004F7DF0` | player ptr / camera ptr / frame time: addressed by 27 / 6 / 490 code sites |
| `0x000F0000` block | below the game image, so the caves cannot overlap game code |

Beyond that: every subset patches only the segment size, the stub jump and the appended installer; the installer
**runs in an R5900 interpreter** (`scripts/r5900/emu64.py`, not included, see CONTRIBUTING.md) and leaves RAM = unpacked game + exactly
our hook words + our caves; and with his row labels our executable is **byte-identical** to the one inside the tester's own
patched ISO. A sector diff of a build against its source shows exactly 126 changed sectors (62 archives × 2, root
directory, volume descriptor) plus the appended ELF.

## Snags (honest)

- **The patched executable has a new CRC** (PCSX2 uses the XOR of the ELF's words). PCSX2 treats it as a different
  game: per-game settings keyed by CRC and cheats/patches for `E169BAF8` do not apply. The tester hit this as "the 100 %
  save wasn't found" because his PCSX2 assigns a memory card by CRC. **Here** there is no per-game ini for this game
  (`gamesettings/` has no `SLUS-20500_*.ini`), so the global card (Mcd001) loads either way — observed: the patched disc
  continued from Red's save. On a setup that does assign a card by CRC, copy that `[MemoryCards]` section to
  `SLUS-20500_<new CRC>.ini`.
- **The disc's UDF bridge (NSR02) still lists the original, shorter `SLUS_205.00`** at its old LBA. PCSX2 and the PS2 BIOS
  boot through ISO 9660 (verified: PCSX2 boots it). Real hardware and any UDF-reading tool are **not tested**.
- **Cheats page is replaced in every level archive, so the rows only exist on a build from this tool.** Other mods that
  edit `cheats.swui` will conflict (the build refuses if the page is not the original layout).
- **Mission triggers fire while flying**; do not save right after noclipping through one. Freeze stops characters only
  (bullets in flight, props, particles, timers and scripts keep running); a horse you ride freezes too.
- **Free RAM assumption:** the caves live in `0x000FE000-0x000FEFFF`, which the tester found zero in his save states. It is
  below the game image; nothing in our proof loaded into it, but it is an assumption, not a proof.
- Not built (no evidence, so not offered): infinite Deadeye and god mode already exist as the game's own cheats
  (`Invulnerable` = cheat 6, `Infinite Deadeye`); moon jump (Mover gravity `+0x4C` of MoverData) has an address but no
  hook and no test.

## In-game proof (PCSX2, 2026-11-07)

See the PROVEN / NOT YET TESTED table at the end (filled in from `05_Docs/ingame-proof/codemods_*.png`).

| Thing | Status | Evidence (`05_Docs/ingame-proof/`, summary in `codemods_PROOF.txt`) |
|---|---|---|
| Patched disc boots in PCSX2, installer + hooks run, game plays | **PROVEN** | full ledger flow to the SHER70 town on `RDR_CODEMODS_CACTUS.iso` (CRC D948CFB5) |
| New Cheats rows (Noclip / Freeze / Cam Lock) | **PROVEN** | `codemods_cheats_page_new_rows.png` |
| Noclip, switched on from its Cheats row | **PROVEN** | `codemods_cheats_noclip_on.png`, `codemods_noclip_rise_above_town.png`, `codemods_noclip_through_building_stairs.png` (flew through a building) |
| Freeze World, R3+Triangle | **PROVEN** | same camera, 15 s apart: 15,088 px changed with the world live, 144 px frozen (`codemods_freeze_*`) |
| Camera Lock, R3+Circle | **PROVEN** | locked: 0 px changed while Red flew away; unlocked: 503,383 px, view snapped to Red (`codemods_camlock_*`) |
| Custom model drawn (cactus) seen with noclip | **PROVEN** (it draws) | `codemods_noclip_cactus_from_above_1/2.png`; same cactus from the spawn: `codemods_town_spawn_cactus_ahead_mods_off.png` |
| L3+R3 noclip combo | NOT YET TESTED | the Cheats row drove noclip in the proof |
| Freeze / Cam Lock via their Cheats rows | NOT YET TESTED | the combos drove those |
| The Cheats rows with a normal save (no cheat unlocked) | NOT SHOWN | the pause menu only offers Cheats once a cheat is owned (stock rule); the proof used a temporary PCSX2 cheat file that unlocked every cheat and kept Invincibility on. It was removed and `PCSX2.ini` restored. `codemods_pause_menu_no_cheats_entry_stock_save.png` shows the stock case |
| Real hardware / UDF-reading tools | NOT TESTED | UDF bridge still lists the original ELF |
| Studio tab → build | tested through the HTTP route on a dev Studio (port 8745, scratch root `D:\RDR-Work`); the in-browser click was not driven | `codemods_build` → `RDR_CODEMODS.iso`, 62/62 archives, verified read-back |
| Infinite Deadeye / god mode | not built | already game cheats (Invincibility, Infinite Deadeye rows exist); moon jump has an address (MoverData gravity `+0x4C`) but no hook or test |

Notes from the run: flight at walk-stick speed is modest (R2 gives ×4). The shipped combos use L3/R3, which have no keyboard
binding in this PCSX2 setup, so a keyboard-driven proof goes through the Cheats rows or a temporary `L3 = Keyboard/N`,
`R3 = Keyboard/M` binding (used for the freeze and camera-lock chords, then restored).

## Share code mods as a small pack (added 2026-11-07)

Code mods do not need a shared ISO. PROJECT ▸ Code Mods ▸ **Export as mod pack** writes a normal mod pack
(`<name>.rdrmodpack.zip`, `02_Modded/exports/`) that carries the chosen mod list, **not game bytes**. The recipient's
Studio rebuilds the executable patch and the Cheats rows locally from their own stock disc (`codemods.patch_iso`, so
every build check still applies). Normal file changes in your working disc ride along in the same pack.

- **Format:** a pack with code mods is `rdrmodpack/2` with `codemods = {version: 1, features: [...], stock_elf_sha1, stock_elf_size}`.
  Packs without code mods stay `rdrmodpack/1`. An older Studio only knows format 1 and refuses a format-2 pack with
  "not an RDR Modding Studio mod pack (format 'rdrmodpack/2')" before touching anything (checked against the previous
  `modpack.py`). A newer code-mod `version`, an unknown mod key or a damaged section is refused by this Studio.
- **Size:** the three mods alone: **792 bytes**. With the 255 leftover file changes in the scratch working disc
  (weapon test models etc., not just the cactus): 607,481 bytes.
- **Apply** (Export ▸ Apply a mod pack): wrong game = the stock `SLUS_205.00` sha1 differs → refused, nothing written;
  file part uses the existing stock-sha1 / conflict / read-back rules on the working disc; then the code mods are built
  into a NEW `02_Modded/RDR_CODEMODS.iso` copied from the working disc (replaces the previous `RDR_CODEMODS.iso`
  only). A working disc that already carries code mods is refused (nothing written). `01_Vanilla` and the stock disc
  are never written.
- **Exporting** refuses if the working disc already carries code mods (its Cheats page would be exported as a file).
- Code: `modpack.py` (`build_pack(codemods=)`, `assess_codemods`, `_check_codemods`), routes `pack_export` (`features`,
  `include_files`) and `pack_apply`; tests `scripts/test_codemods_pack.py` (in the suite); proof driver
  `scripts/codemods_pack_proof.py`.

### Pack proof (2026-11-07, dev Studios on scratch roots; the real stock/working discs were never opened)

| Check | Result |
|---|---|
| Code-mods-only pack (noclip+freeze+camlock) | **792 bytes**. Applied via `pack_apply` to a fresh copy of the clean ISO → `RDR_CODEMODS.iso` **byte-identical (SHA-1 e80f8772…) to a direct Build** of the stock ISO (2,342,318,080 bytes both) |
| Code mods + custom cactus pack | **1,838,996 bytes (1.8 MB)**: the one changed SHER70 level file + the mod list. Applied to a fresh clean copy → `RDR_CODEMODS.iso` **byte-identical (SHA-1 b2f031b9…) to a direct Build of the author's cactus working disc** |
| Pack made for another game (wrong stock executable sha1) | **refused**, "different copy of the game"; working disc and built disc SHA-1 unchanged |
| Combined pack (255 file changes + code mods, 607 KB) | 255 written, all read back; the built disc holds exactly the pack's files + Cheats page in 62/62 archives; the working disc stays free of code mods |
| Stock ISO | SHA-1 unchanged after every run |
| Older Studio (previous `modpack.py`) given a format-2 pack | refuses: "not an RDR Modding Studio mod pack (format 'rdrmodpack/2')" |
| Boots + noclip in PCSX2 | see `05_Docs/ingame-proof/codemods_pack_*.png` and the table below |

In-game (PCSX2, full ledger flow, the SHER70 town, Red's save): the disc built by applying the **1.8 MB code-mods + cactus pack**
to a fresh copy of the clean ISO (CRC D948CFB5, byte-identical to the direct Build):

| Thing | Status | Evidence (`05_Docs/ingame-proof/`) |
|---|---|---|
| Pack-applied disc boots and plays | **PROVEN** | `codemods_pack_spawn_cactus.png` (town, custom cactus ahead of the spawn) |
| Cheats rows Noclip / Freeze / Cam Lock | **PROVEN** | `codemods_pack_cheats_rows.png` |
| Noclip from its Cheats row | **PROVEN** | `codemods_pack_cheats_noclip_on.png`, `codemods_pack_noclip_rise.png`, `codemods_pack_noclip_flying.png` (Red hovering above the town, camera following) |
| Freeze / Cam Lock / L3+R3 on the pack-applied disc | NOT RE-TESTED | the executable and Cheats pages are byte-identical to the already-proven build, so they were not re-driven |

Same test harness as before (temporary PCSX2 cheat unlock + Invincibility; removed afterwards, `PCSX2.ini` restored).
The Code Mods tab's **Export as mod pack** button and Export > Apply a mod pack with a code-mod pack were driven through
their HTTP routes on dev Studios (ports 8745-8748, scratch roots `D:\RDR-Work*`), not clicked in a browser.



## Modding aids added 2026-11-08 (the developer: "no gimmicks, just things that help modding")

Disc: `02_Modded/RDR_CODEMODS_CACTUS2.iso` = `RDR_CUSTOMMODEL.iso` + all five mods (PCSX2 CRC 60C2FF28, 62/62 Cheats pages).
Evidence: `05_Docs/ingame-proof/cheats2_*.png`. Everything below was tested on the **stock save with no pnach/cheat file**.

| Candidate | How it helps modding | Verdict |
|---|---|---|
| **Cheats Menu Unlock** (`cheatsmenu`, ticked by default, no row, no controls) | Pause > Cheats exists on ANY save (fresh card, the developer's Deck, a tester's save), so the Noclip/Freeze/Cam Lock/Slow-Mo rows and the built-ins (Invincibility, No HUD, Infinite Deadeye) are always reachable while testing a mod. Before this, every test needed a pnach or a save that owned a cheat. | **PROVEN** |
| **No HUD** (the game's own cheat, now reachable) | Clean proof screenshots of a custom model/placement: no ammo/health/bounty text over the picture. Costs nothing: it is the shipped `cmb_cht_no_hud` row. | **PROVEN** (HUD gone in `cheats2_nohud_clean.png` / `cheats2_noclip_l3r3_rise.png`) |
| **Slow-Mo** (`slowmo`, row "Slow-Mo", R3+Square) | Quarter-speed world, so you can watch an animation/placement/timing mod frame by frame instead of guessing at 30 fps (e.g. whether a new weapon's fire anim blends, or whether a prop pops in). | **PROVEN** (see below) |
| **Player position + heading readout** (`scripts/rdr_pos.py`, external) | Gives the X/Y/Z and facing of where you stand, to place a prop/model/spawn: walk to the spot, read the numbers, paste them into the placement. `--mark NAME` prints a paste-ready line each time you press Enter. | **PROVEN** (numbers read live; NOT drawn in-game, see below) |
| Free / photo camera (pad-2 roam) | Already covered by what exists: **Noclip + Cam Lock** (fly anywhere, camera follows; Cam Lock then parks the view so you can fly into frame, or leave it behind). The debug pad-2 roam would need reverse-engineering the camera tick for no new view angle. | **DROPPED** (no clear gain, would fight the follow-camera) |
| Hide-HUD hook of our own | The shipped No HUD cheat already does this; a second implementation adds nothing. | **DROPPED** |
| Teleport bookmarks, horse noclip/speed, invincibility, gravity/MoverData tuning | the developer ruled out gimmicks (11/8 02:01) / skipped. Invincibility stays the game's own row. | not built |

### Cheats Menu Unlock: how it works
The pause menu shows "Cheats" when `0x1222A8` finds an OWNED cheat-type unlockable (via `0x183028`); each row on the page is
shown iff `0x122248(name)` says owned. The mod: (1) `0x1222A8` becomes `addiu v0,zero,1 / jr ra / nop` (any cheat owned);
(2) `0x122248` jumps to a cave (`0x000FD000`) that returns 1 for every cheat except the two the game never implemented,
`cheat_showposition` and `cheat_showaiinfo` (byte 10 of the name is `p` / `a`, no other cheat name has either), which fall
through to the original test and stay hidden exactly as shipped. **Ownership only**: nothing is switched on, nothing is saved to the
card (all ten rows were Off after the first open). Test: `test_codemods.py` runs the cave in the R5900 interpreter for all ten
names from the game's own `unlockables.csv`.

### Slow-Mo: how it works, and the measurement
The per-frame timer `0x0025F960` computes the frame time at `0x004F7DF0`. The mod wraps it (the two displaced words run from a
stub in the cave `0x000FD200`); after the game's own code runs, if the flag is on it multiplies the frame time by 0.25 and
rewrites the reciprocal at `0x004F7DF8`. Measured over PINE-read positions on the live game: holding forward for 5 s covered
**5.8 m with Slow-Mo on and 23.6 m off (x4.07)**. The Cheats row, the R3+Square chord and the "flag survives a level restart" were all seen.
Audio/music are not slowed (they are not tied to this timer); input lag is natural (30 fps game at quarter speed).

### Position readout: why it is outside the game
The game's own "Show Player Position" cheat is a stub (nothing in the executable draws it) and the executable contains no
text-draw routine a hook can call without writing a whole UI path (the debug dump strings at `0x516360` have no code reference).
So `scripts/rdr_pos.py` reads PCSX2's RAM over **PINE** (Settings > Advanced > Enable PINE, port 28011; read-only, it only sends
read commands, tested against a fake server in `scripts/test_rdr_pos.py`):

    py -3 scripts/rdr_pos.py            # live X Y Z + heading, 4x a second
    py -3 scripts/rdr_pos.py --once     # one line
    py -3 scripts/rdr_pos.py --mark SPOT   # Enter prints  SPOT_1: (x, y, z) heading h

Player = pointer at `0x0079E240`; world matrix at creature+0x30 (3 rows x 3 floats), position at creature+0x54 (Y is up).
Heading: the character faces along MINUS row 3 of the matrix (a 5 s walk moved (-5.7, -1.3) with row 3 = (0.976, 0, 0.217));
0 = +Z, 90 = +X. Units are the game's world units (metres-ish: the town spawn reads about (7.7, 1.9, 41.8)). Whether those numbers equal the
placement coordinates in the level data was NOT checked: do that once with a known prop before trusting it for placement.

### Controls (all of them)
| What | Control |
|---|---|
| Cheats Menu Unlock | none: Pause (Start) > Cheats on any save |
| Noclip | L3+R3, or Cheats > Noclip. Stick flies level to the camera, Cross up, Square down, R2 x4, L2 x1/4. **L3+R3 now PROVEN** (y 2.0 -> 22.9 m) |
| Freeze World | R3+Triangle or Cheats > Freeze (chord PROVEN: flag flipped, read over PINE) |
| Camera Lock | R3+Circle or Cheats > Cam Lock (chord PROVEN: flag flipped) |
| Slow-Mo | R3+Square or Cheats > Slow-Mo |
| No HUD | Cheats > No HUD (the game's own) |
| Position readout | `py -3 scripts/rdr_pos.py` on the PC running PCSX2 (turn PINE on first) |

### Honest limits
- Cheats page: five rows now fill the 512-byte smallest slot to the byte (zlib alone needs 523). The deflated page for each row
  subset that needs it is pre-computed with Zopfli and stored in `codemods._PRECOMPUTED` (keyed by the page's sha1), so no machine
  needs Zopfli; any new row means re-running that step (the test fails loudly if a page does not fit). The page also lost one
  redundant `<FONT:berrylium>` line and the Big Money button moved to the original's (500,350).
- Show Player Position / Show AI Info stay hidden on purpose (the game never implemented them).
- Freeze/Cam Lock via their Cheats ROWS were still not driven (their chords were). Real hardware / UDF readers: untested.
- Slow-Mo scales world time only as far as the frame timer reaches; audio is not slowed. Not tried during a cutscene or duel.
- Save safety: the new mods do not write the card, but the earlier warning stands (do not save right after noclipping through a trigger).

## Research instruments (added 2026-11-08, the reviewer): Memory Watch + Function-Hit Logger

Not cheats: measuring tools, off by default (rows on the Code Mods tab tagged "Research tool"). Code `rdrstudio/codemods_research.py`; readers `scripts/rdr_watch.py` / `rdr_hitlog.py` (PINE or savestate, read-only); mapping helpers `rdr_snap.py`, `rdr_ssdiff.py`; config `02_Modded/research_config.json` ("hitlog": up to 16 functions, "watch": up to 32 entries with 0-2 pointer hops). Tests: `test_codemods_research.py`, `test_rdr_research_tools.py`, `test_rdr_research_tools2.py`. Full write-up, proof screenshots and the system map: `05_Docs/atlas/index.html`.
In-game proof (PCSX2, SHER70 mission 1): hit counts + args + caller + return values for 16 functions; HP chain player+0x18 -> +0x10/+0x14 (125.09/250.0); ammo word found by savestate diff. Limits: floats (f12/f0) are not logged; numbers are shown in an overlay window, not inside the game picture.

## Combat mods added 2026-11-12 (always on for the disc: no row, no combo)
- **Infinite Ammo** — `0x0039BBC8` `sw count,0x24(weapon)` -> nop (the player gun's write-back; `0x0039BA5C` is a second,
  non-player site and is left alone). PROVEN on a built disc.
- **God Mode** / **One-Hit Kills** — one cave at `0x000FD400` on `Health::TakeDamage` `0x0021ADDC`
  (`sub.s f0,f0,f20` / `swc1 f0,16(s0)`; s0 = health component, player's = `*(*(0x79E240)+0x18)`). God: skip both for
  the player. One-hit: `f20 = f0` for everyone else. Both PROVEN on a built disc (one-hit 2026-11-12: one shot 60 -> 0
  health, the same enemy on a stock disc took four shots).
- Keys `infammo`, `godmode`, `onehitkill` follow the research keys. Test: `scripts/test_codemods_combat.py`.
- **Fast Red** (`fastred`, the last key; 2026-11-12) -- Mover::Update `0x002C3E10` reads the frame time `0x004F7DF0`
  at three places and is called from exactly three sites (`0x00184ABC`, `0x00184C7C`, `0x00268ACC`, each `jal` +
  `lw a0,0xc(..)`). The three `jal`s go to one cave at `0x000FD600`: when the mover is the player's
  (`mover+0x34 == player+0x30`) it multiplies the frame time by 1.5, calls Mover::Update and puts the frame time back
  (save slots at cave+0xC0). Only Red's movement integrates the longer step, so collision still applies. It does not
  touch Mover::Update's entry, so it coexists with Noclip. PROVEN on a built disc: 7.1 m/s vs 4.8 stock, wall stop
  identical to stock (`ingame-proof/fastred_*`). Test: `scripts/test_codemods_speed.py`. Factor: `SPEED_FACTOR`.
