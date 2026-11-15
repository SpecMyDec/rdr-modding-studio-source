# The Red Dead Revolver save file — what is known, and what is not

rdr-save, 2026-09-20. Reproduce everything here with
`py -3 03_Tools/scripts/verify_save.py` (36 checks, ~2s, writes only copies).

**The short version.** The container is solved: the memory card's filesystem
is implemented, the save comes out byte-exact, and it goes back in with all
8,650,752 bytes of the card unchanged. **Money is now decoded too** -- the game
was booted from a copy of this card and read off its own screen. Most other
numbers are still unidentified, and one earlier candidate turned out to be
wrong, which is recorded below rather than quietly dropped.

---

## 1. Money is at file offset 0x68, and it is not in a profile slot

On 2026-09-20 the game was booted from a COPY of this card, driven with a
virtual pad, and its SHOP screen photographed:

> **BANK BALANCE $ 677** — `08_ForkLab/savelab/shots/09_shop_bank_balance_677.png`

Three facts make that a decode of **+0x68** rather than a coincidence:

1. The untouched card holds **677** at file offset **0x68**.
2. **677 appears exactly once** in all 18,432 bytes of the save. A number that
   appeared six times would have told us nothing about which offset the screen
   was showing.
3. The save file is mirrored **byte-for-byte in EE RAM at `0x0073DE28`** while
   the game runs, so the word on screen and the word at that offset are the
   same word, not two numbers that happen to match.

`0x68` sits in the 184-byte prefix, which this document previously called
UNKNOWN. So the prefix is **the active game's state**, not padding, and money
is global to the save rather than per-profile.

### The candidate this killed

**`slot 0 +0x2f4` (752) is NOT money.** It was the leading candidate on
file-only evidence -- a lone plausible integer, set in the used slot and zero
in all five empty ones, heading a run of thirteen. The game showed **677**.
That is the entire reason the boot was worth doing, and +0x2f4 goes back to
being an unlabelled integer rather than being re-pointed at the next plausible
meaning.

### Proven end to end: an edited card loads and the edit takes effect

The Studio's own writer set money to **4242** on a copy of the card, and that
card was booted:

> **BANK BALANCE $ 4242** — `08_ForkLab/savelab/shots/15_edited_shop.png`

The game mounted the card (`McdSlot 0 [File]: Mcd001.ps2 [8 MB, Formatted]`),
showed the profile normally, loaded the save with no corruption warning, and
displayed the edited number. Live EE RAM agreed: the save image at
`0x0073DE28 + 0x68` read 4242.

So all three links hold: **this toolkit writes a card the real game accepts,
+0x68 is causally the money field, and the value survives a load.**

One nuance worth keeping. Writing into the live RAM copy *mid-game* does
**not** move the on-screen balance (`shots/11_ram_write_did_not_change_hud.png`)
— the shop reads a separate working variable and the save image is synced at
save/load, not continuously. The field controls money *when the save loads*,
which is exactly what a save editor needs and is not the same as a live
trainer.

### Still only an enum

`StatNameToIndex` at `0x001831E0` walks a 28-entry alphabetical table, so
MONEY_COLLECTED is index 15 and MONEY_SPENT is 16:

    0 BACK_SHOTS            7 FAVORITE_POKER_RULES  14 MELEE_HITS           21 SHOTS_FIRED
    1 BEST_COMBO            8 FAVORITE_WEAPON       15 MONEY_COLLECTED      22 SHOTS_HIT
    2 CARDS_COLLECTED       9 FOCUS_GAINED          16 MONEY_SPENT          23 TIME_FROZEN
    3 DISTANCE_TRAVELED    10 GAMES_PLAYED          17 NUM_SPEC_MOVES_USED  24 TIME_ON_FIRE
    4 DUELS_WON            11 GATLING_SHOTS         18 PCT_JOURNAL_COMPLETE 25 TIME_PLAYING
    5 ENEMIES_KILLED       12 HATED_ENEMY           19 PEOPLE_POISONED      26 TIME_POISONED
    6 FAVORITE_CHARACTER   13 HEAD_SHOTS            20 PEOPLE_THROWN        27 TIMES_KILLED

That is a name-to-index map, not an address. Nothing yet says where the 28
values live, and 47 separate code sites take the save buffer's address, so
finding out is a real reverse-engineering job rather than one more grep.

### The diff material still does not exist

Unchanged and worth restating: every copy of `Mcd001.ps2` on this machine is
one file by sha256 (five paths, one hash), and all seven `.p2s` states predate
gameplay. Money was settled by booting the game, not by diffing.

---

## 2. The container (DECODED)

An 8 MB PS2 memory card, and `03_Tools/savedata/ps2mc.py` implements it.

| | |
|---|---|
| image | 8,650,752 bytes = 16,384 physical pages × 528 |
| page | 512 data + 16 spare (this card **has** ECC) |
| cluster | 2 pages = 1,024 bytes; 8,192 clusters |
| `alloc_offset` / `alloc_end` | 41 / 8,135 |
| root directory | cluster 0; indirect-FAT list `[8]` |
| card type / flags | 2 / `0x2b` |
| version | `Sony PS2 Memory Card Format 1.2.0.0` |

A card *without* ECC (16,384 × 512) also exists; the reader sizes the spare
from the file length rather than assuming.

### ECC

Four 3-byte codes per page, one per 128-byte chunk, then **four bytes this
toolkit does not compute**. Column parity, then two line parities; init
`0x77 / 0x7f / 0x7f`; each input bit `i` contributes the mask
`((8 + i) << 4) | (7 - i)`.

**The column parity is 7 bits, not 8.** The published mask table yields an
8-bit value and the card stores it `& 0x7f`. That was not guessed: the
algorithm was checked against every chunk on the real card that stores a code,
and **1,215 of 1,216 agree**. The bit is invisible on an all-zero page, which
is most of a fresh card, so a reader that never checked a written page would
never notice.

Two card facts a writer has to respect:

- **The last four spare bytes are not ours.** 16,080 pages carry
  `ff ff ff ff` there and 304 carry `00 00 00 00`. Nothing in this toolkit
  knows which a page should have, so they are copied through untouched. A
  writer that normalised them would rewrite 16,080 pages on a no-op save.
- **Page 1 fails its own ECC, and always has.** It holds an 8-byte card ident
  (`36 b2 ac 11 96 f8 7a 97`) then `0xff` fill, with the spare still at the
  all-zero code — written without refreshing ECC. It is the 1 of 1,216. The
  test asserts it rather than tolerating it: if it ever disappears, something
  rewrote page 1.

**A known blind spot:** this ECC cannot see a byte replaced by its own
complement. A byte and its complement have equal column masks and equal
parity, so `0x01 → 0xfe` changes a page and moves no spare byte at all. The
test asserts that property instead of quietly picking a value that avoids it.

### The save's directory

`BASLUS-20500RDR/` holds seven entries — `.`, `..`, the 18,432-byte save, three
23,844-byte icons and a 964-byte `icon.sys`. One trap for anyone
re-implementing this: **a subdirectory's own `.` entry carries length 0.** The
child count lives in the *parent's* entry. Only the root's `.` counts its own
children.

---

## 3. The save file's layout (DECODED)

`BASLUS-20500RDR/BASLUS-20500RDR`, 18,432 bytes, 18 clusters.

    0      ..   184   prefix, the ACTIVE game   holds MONEY at +0x68
    184    ..   264   struct header             80 bytes, UNKNOWN
    264    .. 17304   six profile slots         6 × 2,840
    17304  .. 18432   trailer                   1,128 bytes, UNKNOWN

**Found twice, independently, which is the only reason it is stated as fact.**

1. *By measurement.* `?none` repeats at a stride of exactly 2,840, and a pair
   of 12-byte runs sits at +392 and +1372 of every stride. That fixes the slot
   size and puts slot 0 at 264.
2. *By the game's code.* The routine at `0x003F04F8` copies **17,120** bytes
   out of the global at `0x0073DE28`. And

       184 + 17120 + 1128 == 18432        17120 == 80 + 6 × 2840

Neither derivation saw the other. The stride alone would have been one
measurement, which is not a property.

**A third confirmation, and one correction, from live memory.** With the game
running, file bytes **0..17304 are mirrored in EE RAM at `0x0073DE28`**, offset
for offset: 99.7% identical, and every byte that differed was one the game was
actively changing. The correction is the base -- this document previously had
the 17,120-byte block the ELF copies beginning at `0x0073DE28`, which made RAM
offset 0 equal to file offset 184. It does not: **RAM `0x0073DE28` is file
offset 0.** Money at file +0x68 was found at RAM +0x68, and 752 at file 1020
was found at RAM 1020.

The last 1,128 bytes are **not** in that RAM region, which is part of why the
trailer stays unexplained: it is not simply the tail of the same buffer.

### Six slots are six profiles

The executable lists six default profile names in a row at `0x004170F4` —
**RED, BILLY, JOHN, CAROLYN, WYATT, JIMMY** — followed by `profile%d`,
`profile_message` and `ProfileColor`. Six names, six slots, and the one used
slot carries `RED` at +0x714.

On his card **slot 0 is the only one in use**; slots 1–4 are one byte-identical
empty template.

**Slot 5 is not used either, but it is not identical to the template.** It
differs in 51 bytes, all inside `+0x0a74 .. +0x0b13` — the last 164 bytes of
the slot. The values are little-endian pointers: `0x0053232b`, `0x00532324`
(the executable's string area — `left_analog_X` lives at `0x00532388`) and
`0x012a8f20`, `0x012aec60` (heap). This is **uninitialised memory the game
never cleared before writing the file**, not save data.

Live memory settled it. Between two snapshots minutes apart -- the main menu
and the shop -- only twelve bytes of the whole save image changed, and four
were in slot 5's tail: `0x1388 -> 0x0bb8` (5000 to 3000) and `0x012c -> 0x00b4`
(300 to 180). Those are counters ticking down while the game runs. The region
is scratch memory that happens to fall inside the save buffer. Treat the slot
tail as padding and do not read meaning into it.

---

## 4. Slot fields

Offsets are from the start of a slot. "Used" is slot 0 on his card; "empty" is
the slots 1–4 template.

### DECODED, and not in any slot

| offset | type | field | value | what agrees |
|---|---|---|---|---|
| **+0x68** (file) | u32 | **Money** | **677** | the game's own SHOP screen read `BANK BALANCE $677` with this card loaded, and 677 occurs exactly once in the save |

### DECODED — something outside the save file agrees

| offset | type | field | value | what agrees |
|---|---|---|---|---|
| +0x194 | string | **level id** | `lvl_hng` | one of the 82 `lvl_*` identifiers in the game's own string table |
| +0x714 | string | **profile name** | `RED` | first of the six default profile names at `0x004170F4`; six names, six slots |

### CANDIDATE — the type is certain, the meaning is not

| offset | type | used | empty | note |
|---|---|---|---|---|
| +0x020 | u32 | 8 | 0 | set in the used slot only. The value 8 is unexplained, so the editor reads it and never writes it. |
| +0x094 | f32 | 1.0 | 0.0 | |
| +0x0cc | f32 | 0.88 | 0.0 | |
| +0x0e4 | f32 | 0.85 | 0.0 | |
| +0x0f8 | f32 | 0.9056 | 0.0 | |
| +0x120 | f32 | 1.0 | 0.0 | |
| +0x19c | string | `new` | — | |
| +0x1e8 | u32 | 7 | 0 | |
| +0x1ec | u32 | 11 | 0 | |
| +0x1f0 | u32 | 9 | 0 | |
| +0x2dc | f32 | 95.058 | 0.0 | with the next two, position-shaped — but +0x2e4 is zero in both, which is why it stays a guess |
| +0x2e0 | f32 | 232.313 | 0.0 | |
| +0x2e8 | f32 | 221.672 | 0.0 | |
| +0x2f4 | u32 | **752** | 0 | **FALSIFIED as money** — the game showed $677, and money is at file +0x68. Still the head of thirteen consecutive integers; still unidentified. |
| +0x2f8 | u32 | 102 | 0 | |
| +0x2fc | u32 | 78 | 0 | |
| +0x300 | u32 | 38 | 0 | |
| +0x304 | u32 | 16 | 0 | |
| +0x308 | u32 | 20 | 0 | |
| +0x30c | u32 | 4 | 0 | |
| +0x314 | u32 | 75 | 0 | |
| +0x318 | u32 | 2 | 0 | |
| +0x31c | u32 | 5 | 0 | |
| +0x320 | u32 | 5 | 0 | |
| +0x324 | u32 | 2 | 0 | |
| +0x328 | f32 | 84.0 | 0.0 | |

Also present but identical in used and empty slots, so carrying no information
about this playthrough: `fe ff ff ff` at +0x188/+0x18c/+0x190 and
+0x55c/+0x560/+0x564, `1` at +0x1d4 and +0x5a8, `ff ff ff ff` at +0x334 and
+0x0a78, `24` at +0x758, `0.5` at +0x75c/+0x760, and the string `none` at
+0x764.

**A candidate is not a weak decode. It is a decode of the type only.** Writing
752 → 4242 at +0x2f4 and reading it back proves the byte is reachable; it says
nothing about what the game does with it.

---

## 5. UNKNOWN

- **The rest of the 184-byte prefix.** Money at +0x68 is known now; the other
  ~38 non-zero bytes are not. Three floats of 1.0 at +0x48/+0x4c/+0x50, 250.0
  at +0x60, 3.0 at +0x64, and bit-patterned words (`0x40001000`, `0x71404012`,
  `0x21040010`) that look like flags. Now that the prefix is known to be the
  active game's state rather than options, these are worth a second look.
- **The 80-byte struct header** (file +184..264). Three non-zero words: 85,
  156, 107.
- **The 1,128-byte trailer.** Fully non-zero, only 22 distinct byte values, all
  in 26..129, with a strong autocorrelation at lag 256 and its neighbours 255
  and 257. That is the signature of a raster with a 256-wide row stride, but
  1,128 is not a multiple of 256 and no clean sub-block period fits. Left
  unnamed.
- **Whether the save carries a checksum at all.** Six algorithms (byte sum,
  word sum, CRC32) over four spans match no word in the file. That is weak
  evidence of absence, not proof. See §6.

---

## 6. Safety, and what the editor refuses

- **Writes go to a copy of the whole card** in `06_Backups/saves/`, timestamped,
  before a byte is written. The card, not the save — restoring a save into a
  card is the harder operation.
- **PCSX2 must be provably closed.** The probe returns running / not running /
  *cannot tell*, and a write proceeds only on a definite "not running". From
  WSL, Windows processes cannot be enumerated at all, so the honest answer
  there is "cannot tell" and the route refuses. That is deliberately the
  annoying direction: a card written under a live emulator is overwritten at
  its next flush, and the damage surfaces hours later as a save that will not
  load.
- **No checksum is fixed up, and that is now known to be safe for this edit.**
  A card written by this toolkit with one changed word loaded normally and the
  change took effect (§1). Either there is no checksum over this data or the
  game does not verify one on load; either way an edited save is accepted.
  Six algorithms over four spans had already failed to match any word in the
  file, and the boot is the evidence that mattered.
  *Scope:* this was one u32 in the prefix. It does not license arbitrary
  structural edits -- growing a file, rewriting a slot wholesale -- which the
  writer refuses anyway.
- **An edited card HAS now been booted, once, successfully.** That was the
  gate. It is still one observation, on a copy, of one field.
- The editor writes only the fields in the table above, each at its own width,
  and never grows or shrinks a file.

---

## 7. What is left, and what it needs

Booting is neither a capability problem nor an exposure problem now: the rig
in §8 runs the emulator on a private desktop with zero windows on his display.
What remains is a courtesy question -- it uses the GPU he is also watching
video on -- so each run is asked for rather than assumed.

1. ~~Does an edited card load?~~ **Answered 2026-09-20: yes.** See §1.
2. ~~The stats screen.~~ **There is no stats screen.** Looked for it on
   2026-09-20; it does not exist in the front end. Checked, with shots: the
   desk's player book, the Journal index (`shots/25`), the Wanted: Dead or
   Alive page (`shots/30`), and Showdown -> Options, which offers only
   Player, Controls, Sensitivity and Favorite (`shots/26`). Nothing displays
   the 28 stats.

   That fits the executable: beside the stat table sit `bountygoal_%s_%d`,
   `bounty_description`, `BountyLevel`, `rankBounty` and `theUnlockMessage`.
   **The 28 stats are unlock CONDITIONS evaluated per bounty, not a page the
   player reads.** So there is nothing to photograph, and the way to decode
   them is to move one deliberately and diff -- fire a known number of shots,
   save, see which word changed. That is item 3, not a separate job.

   Two unlock patterns WERE read off the screen and both failed to decode.
   Recorded so nobody repeats them:
   - Journal index: 17 entries, 9 unlocked. Neither the LSB-first mask
     (`0x0001889F`) nor the MSB-first one (`0x0001F223`) appears anywhere in
     the save. The top-level entries are probably derived from sub-item
     counts rather than stored as one bitfield.
   - Wanted list: 11 entries, 2 unlocked (Bloody Tom, Greg 'Big Oaf'
     Whitney). Seventeen different words in the save have popcount 2 within
     11 bits -- far too many to choose between, so none was chosen. The
     bounty names are not in the save; they are static disc data.
3. **A second save in slot 1.** Still the best single unlock, and now doable
   without the developer: play, save to slot 1, diff slot 0 against slot 1.
4. **The remaining prefix bytes**, now known to be active-game state.
5. The trailer, and where the game gets it, given it is not in the buffer.

## 7b. The slot-1 profile experiment, 2026-09-20

Driven live on a copy. The game was made to create a **second profile**, which
is the "second save" the whole plan wanted.

**Four things it confirmed.**

- The profile screen is literally titled **GUNSLINGER PROFILES** and is a
  2x3 grid of six slots (`shots/34_player_list.png`), with RED in the first.
  Six slots on screen, six 2,840-byte slots in the file.
- The name it offered for the second profile was **BILLY**
  (`shots/35_new_player.png`) -- the second entry in the executable's table at
  `0x004170F4`. `DEFAULT_NAMES` was read off the ELF and the game agreed.
- `rdrsave.py` parsed the new profile straight out of live memory: slot 1,
  name BILLY, everything else zero. The decoder was tested against a profile
  the *game* wrote, not one this toolkit wrote.
- **Money is per-profile at runtime, and +0x68 holds the ACTIVE profile's.**
  With BILLY loaded the HUD showed `$0` while RED had shown `$677`, and +0x68
  still read 677 because the prefix had not yet been re-synced. That is what
  "the prefix is the active game's state" means in practice.

**What profile creation actually writes.** Diffing the fresh BILLY slot
against the empty template: **six bytes, two fields.** The name at +0x714, and
**+0x1e8 = 5**. Nothing else. Every other candidate -- the floats, the
thirteen integers, the position, the level -- is still zero in a fresh
profile, so all of it is written during play. (RED has +0x1e8 = 7.)

**Two negatives, recorded so they are not re-run.**

- *The save image does not track money live.* Buying the revolver for $100
  moved the shop from `$677` to `$577` on screen
  (`shots/31`, `shots/32`) and changed **nothing** in the save image. It is
  synced at save/load only.
- *The live money variable is not a plain number in EE RAM.* Across that same
  known -100, **no** u32, unaligned u32, u16 or float anywhere in 32 MB went
  677 -> 577, and the literal 577 appears nowhere in the after-dump as an
  integer at all -- though the ASCII text "577" does, six times. Whatever the
  shop reads, it is derived or encoded, and it was not worth more guesses.

**Why there is still no card-level diff.** The game writes the memory card
**only at mission checkpoints**. Measured: the card's sha256 was unchanged
across profile creation, starting a new game, and quitting to the menu. So a
written card needs real play.

**Analog stick support was added and it works** (`pad_stick`, `op: "stick"`).
Proof it reaches the game: the Chapter 1 tutorial advanced from "Move: left
analog stick" to "Tilt/Rotate Camera: right analog stick", Red walks, and the
camera rotates (`shots/44`-`shots/59`). The stick was never the hard part.

**Playing to a checkpoint failed anyway, and the reason is worth recording.**
Movement is camera-relative and the only feedback is one still frame per
~3-second burst, so steering is open-loop: `ly = +1` walks the same world
direction every time and marches back into the same fence, and about twenty
bursts of it went in a circle around one field without reaching "follow the
path down to the creek bed". This is not a stick problem or a game problem --
it is navigating a 3D character by postcard.

The fix, if this is ever resumed, is **closed-loop navigation**: find the live
player position in EE RAM and steer against it instead of against
screenshots. A first attempt is in the notes -- diffing two in-game dumps for
float triples that moved a plausible distance yields **495 candidates**, which
is too many. A third fix at a known-different spot, plus the constraint that
the Y component stays near constant on flat ground, should cut it to one. The
position could then also be written through PINE, though teleporting may well
upset the level script.

## 8. How the evidence in §1 was obtained

Recorded because it is reusable and because two of its failures cost real
time. Code is tracked in **`03_Tools/savedata/rig/`**; it runs from a working
copy in `08_ForkLab/savelab/rig/` alongside the emulator, which is a private
copy under `08_ForkLab/savelab/pcsx2/` -- never RDR-Play's, and never his.
(`08_ForkLab` is not tracked, which is why the code does not only live there.)

- **Session.** Anything touching a window must run in session 1. A process
  started from WSL lands on a `Service-0x0-…$` window station and sees zero
  windows machine-wide, so the rig runs as a transient Scheduled Task with
  LogonType Interactive. Measured from inside: `WinSta0` / `Default` /
  session 1, 168 windows visible.
- **Card copies.** `08_ForkLab/savelab/cards/` holds three files, because
  fifteen copies turned out to be three distinct files by sha256 (deduplicated
  by hash, never by size): the pristine copy of his card, which
  `scripts/verify_save.py` names as its fallback source; one UNFORMATTED card,
  kept deliberately because it is the only thing that exercises the "not a PS2
  memory card image" path; and `test_money.ps2`, the edited card that was
  booted. This does not weaken the "five copies, one file" finding in §1 --
  that is about the ORIGINALS on `D:\Emulation` and `D:\RDR-Play`, which were
  never touched and can be re-hashed any time.
- **Where the captures live.** `08_ForkLab/savelab/ram/`, with a README.
  The 32 MB EE dumps were pruned once the findings were written up; what is
  kept is the 18,432-byte save-image slice from `0x0073DE28` per capture,
  which is what every save-level claim rests on. The slices were checked
  against the documented numbers BEFORE the dumps were deleted.
- **Output without a screen grab.** A PINE save state writes a `.p2s`
  containing both a `Screenshot.png` of the exact frame and the whole 32 MB
  `eeMemory.bin`. One call gives the picture and the memory at the same
  instant, which is what makes "the screen said 677 and offset 0x68 held 677"
  a statement about one moment rather than two.
- **Input.** ViGEmBus virtual XInput, which has no desktop affinity, plus
  `BackgroundControllerInput = true`. **The pad enumerated as SDL-1, not
  SDL-0** -- RDR-Play's service already holds SDL-0 -- and until `Pad1` was
  rebound, every button press went nowhere while reporting success.
- **Quiet and light, as required:** `Backend = Nullout`, `OutputMuted`, both
  volumes 0; `upscale_multiplier = 1`; never fullscreen, never foreground.

**The private desktop works. Use it.** `RIG_PRIVATE_DESKTOP` -- in practice a
`private_desktop.flag` file beside `righost.py` -- launches PCSX2 onto its own
Windows desktop object via `lpDesktop`. Measured 2026-09-20:

    PINE answered on attempt 1 after 4s, priority Normal, 603 MB working set
    windows owned by PCSX2 on the developer's desktop:  0
    windows on RDRSaveLab: 'Red Dead Revolver' (visible) + 'PCSX2 v2.6.3'
    save-state Screenshot.png: a real 640x480 rendered frame
    (08_ForkLab/savelab/shots/12_privdesk_render_test.png)

This is a **guarantee, not a race**: `lpDesktop` binds the process at creation,
so no window can ever reach his desktop, and there is no taskbar button either
-- the shell runs on the Default desktop and `EnumWindows` there returns
nothing for our pid, which is the same reason it cannot list one. It also
costs nothing: rendering works, because the save-state screenshot comes from
the GS rather than from a composited window.

*This was originally written up as a failure*, and that was wrong. PCSX2 did
refuse to start on a private desktop three times while notepad on the same
desktop ran fine -- but every one of those attempts ALSO ran at the wrong
priority, and two bugs were being read as one. They were never fixed at the
same time until the retest. The lesson is the one below, twice over.

**The failure that was real.**

*Scheduled Tasks run BELOW NORMAL by default.* PCSX2 inherited it and was
starved so badly it looked like a crash loop: 55 seconds to reopen an 8 MB
memory card, 450 seconds to reach the D3D adapter, no window, and not one line
in its own emulog. Setting the task to `-Priority 4` and the process to
NORMAL_PRIORITY_CLASS took first PINE contact from "never" to **12 seconds**.
Nothing was wrong with the emulator.
