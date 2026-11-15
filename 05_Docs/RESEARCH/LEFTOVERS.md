# Developer leftovers and cut content on the retail disc

*rdr-re, 2026-09-14. Everything below is reproduced by
`03_Tools/research/probe_leftovers.py` (read-only, no lock) against
`01_Vanilla/RDR_VANILLA.iso`. Quotes are the developers' own words, copied
exactly — including their typos.*

Short version: the retail disc shipped with a working directory on it. Not a
few stray files — a **whole archive**, `SWAT.DAT`, that is visibly somebody's
scratch folder, complete with backups, keyboard mashes, a file named after the
colleague who broke it, and the game's own format documentation.

---

## 1. The single best artifact: `swat.txt before russ broke stuff`

**SWAT.DAT, 493,484 bytes.** It is a UTF-16 spreadsheet export whose header row
is exactly the one I decoded out of the `.xls` design sheets:

    identifier   font   language   string   scale x   scale y   offset x   offset y   flags

So this is **the game's entire text, exported to a tab-separated file and saved
as a panic backup before a developer named Russ changed something.** 2,499 rows.
It sits next to `swat.bak.strtbl` (324,312 bytes) — a second backup of the same
table in the compiled format — while the string table the game actually loads,
`swat.strtbl`, is 118,024 bytes.

### What is in it that never shipped: the protagonist used to be someone else

52 rows are prefixed `TEMP_JASON_` — placeholder story text, written by a
second developer, for four level intros. One of them, `GHOSTINTRO`, reads
cleanly end to end:

> "**Odd Job.** Having recently come to America the trickshooter **Union Jack**
> makes his way out West to claim his promised position as the star of a
> traveling circus "Professor Perry's Potions and Peculiarities." Jack soon
> discovers the position and the circus are not quite as advertised. When
> Professor Perry declares his plans to loot and burn a nearby town and its
> inhabitants Jack decides he has had enough. Unfortunately for Jack he was
> unsuccessful in stopping the Professor and a band of his freaks from heading
> for the unsuspecting town. Now he's locked up in a cage above the traveling
> group's camp......"

The other three (`CORENEWINTRO`, `JAILNEWINTRO`, `WATERFALLINTRO`) are
copy-pastes of that same paragraph where somebody rewrote the opening for the
hero the game actually shipped with — and **stopped editing after two rows:**

> "**Exodus.** After being captured by General Diego's army and then thrown in
> prison, our hero **Red** finds himself indebted to *position as the star of a
> traveling circus* "Professor Perry's Potions and Peculiarities." **Jack** soon
> discovers the position and the circus are not quite as advertised. When
> Professor Perry declares his plans to loot and burn a nearby town… When
> Professor Perry declares his plans… **Jack** decides he has had enough.
> Unfortunately for **Jack**…"

Read the italics: the sentence is broken. One row ends `…indebted to` and the
next begins `position as the star…`, with the span between them deleted. That
is the fingerprint of the edit — the new opening was pasted over the old one and
the join was never fixed, so three of the four intros are ungrammatical *and*
still call the hero Jack three times each.

The probe asserts this rather than describing it:

    CORENEWINTRO     Red=True  UnionJack=False Jack x3  broken_splice=True
    GHOSTINTRO       Red=False UnionJack=True  Jack x4  broken_splice=False
    JAILNEWINTRO     Red=True  UnionJack=False Jack x3  broken_splice=True
    WATERFALLINTRO   Red=True  UnionJack=False Jack x3  broken_splice=True

Union Jack survived into the shipped game as a multiplayer character. This file
is evidence he was once the one the story was about.

Other placeholders in the same table, verbatim: `PlaceHolder_Cutscene` →
"Placeholder Cutscene".

---

## 2. Files whose NAME gives away a human

125 entries across the disc are named after a person, a mood, or a mistake.
By bucket: **jason 86, "shit" 14, "crap" 11, russ 7, deleteme 6, bak 3,
borked 1.** The highlights, with archive and size:

| file | archive | bytes | what it actually is |
|---|---|---|---|
| `swat.txt before russ broke stuff` | SWAT.DAT | 493,484 | the whole string table, above |
| `before russ added cinematic cameras.cm` | SWAT.DAT | 8,547 | a camera file, saved before the change |
| `russcam.ccm` / `russcrap.ccm` | SWAT.DAT | 57,082 / 19,750 | Russ's camera work |
| `russ_crap.csv` | SWAT.DAT | 2,695 | a real texture-page table: `phase, name, width, height, pages` |
| `russorange.lsh` | SWAT.DAT | 1,569 | a lighting shader, presumably orange |
| `player.arsbak` | SWAT.DAT | 174 | **a backup of the player's weapon list** (see below) |
| `swat.bak.strtbl` | SWAT.DAT | 324,312 | backup of the compiled string table |
| `horseride.bak.amp` | SWAT.DAT | 2,483 | backup of a horse-riding anim map |
| `lvl_train-borked.lvl` | SWAT.DAT | 3,396 | a level manifest, broken, kept anyway |
| `deleteme.tex` | SWAT.DAT | 22,884 | a 22 KB texture nobody deleted |
| `lvl_traingood_deleteme.*` | SWAT.DAT | 189–199 | three more, "traingood" |
| `lvl_train_tempdeleteme.bxes` | TRAIN.DAT | 332 | and this one shipped **inside the real level archive** |
| `craptest.ccm` / `craptest2.ccm` | SWAT.DAT | 19,750 each | byte-identical size to `russcrap.ccm` |
| `jasonbosstest.tbl` | SWAT.DAT | 280 | a boss AI table with his name on it |

`russ_crap.csv` is worth a look precisely because it is *not* crap — it is a
clean texture-page budget table:

    phase, name, width, height, pages
    0, lvl_perfnew_shbox_desk, 128, 128, 68
    2, moneybags^02, 64, 64, 26
    2, molotov^02, 32, 32, 10

And `player.arsbak` is a backup of the player's arsenal, in the plain-text
grammar that 44% of this disc uses:

    NumTypes 10
    Revolver            0
    Twin_Revolvers      1
    Breech_Loader       0
    Repeater_Rifle      0
    Sacred_Bow          0
    Knife               0
    Dynamite            0
    Molotov             0
    SnakeOil            0
    gatlinggun_03       0

**One caveat, stated so it does not get quoted as fact later:** `livery_truss.tex`
matches a search for "russ" and has nothing to do with Russ. The probe prints it
rather than filtering it, so the false positive stays visible.

### Jason — and there are TWO of them

> **Correction, 2026-09-14.** This section first said *"86 of the 125 are
> his"*, attributing every `jason` file to the developer. **That is wrong.**
> The disc carries two Jasons and they separate mechanically: a `*jason*` file
> whose name yields a real file when `jason` is swapped for another slasher
> villain belongs to the roster below; one with no such sibling is the person.
> **27 are the slasher, 34 are the developer** (`probe_refs.py`). The
> clincher for the second group is `jasons_actfile_for_tomb_783.actb` — a
> possessive, so unambiguously a human being.

The developer's 34 are not junk — they are shipped AI behaviour files that kept
his name: `bridgeattack_jason.tbl`,
`bridgecenter_shoot_diego_jason.actb`, `crun_jason.actb`, `cwalk_jason.actb`,
`covernearme_obg_jason.actb`, `bridge3rdwave_attack_jason_attack.stateb`. Several
are in `LVLS_SNG/BRIDGE.DAT` as well as SWAT.DAT, i.e. they are in the level the
game loads. My favourite is `jason_moguratataki.actb` (plus `_b`, `_c` and an
`.acttbl`) — *moguratataki* is Japanese for **whack-a-mole**, which is a fair
description of an enemy that pops up, shoots and ducks.

---

## 3. The 272 `.txt` files: only three are text, and all three are a person

The `.txt` extension was marked UNKNOWN in `UNKNOWNS.md` because nothing read
it. Reading all 272: **269 are UTF-16LE subtitle scripts** (154 distinct names),
timestamped dialogue, e.g. `lvl_barfight_pupshow_01_en.txt`:

    00:11.0   "All I'm saying, is if it was me that lost an arm,"        5
    00:14.2   "Well maybe, but a cannon? "   Well that's too much gun for any man!   5
    00:19.0   Cork your whisky holes.        "You didn't know him before, like I did. "   5
    00:52.0   "Say goodbye, Mister."                                     5

That is a format finding, not a leftover, and it is going into `UNKNOWNS.md`
separately. **31 of the 154 do not decode cleanly** — they alternate a good line
with a mojibake one, because UTF-16 text was written into an already-UTF-16 file.
`03_uglystreetfight_open 1_en.txt` is the clearest case: line 1 is fine, line 2 is
garbage, line 3 is fine. That is a shipped bug in their export tool.

**The three actual text files are all in SWAT.DAT and all three are a human:**

- `crap2.txt` (10 bytes) — `adfadfda`. A keyboard mash. Its sibling
  `crap.actb` (6 bytes) is `adfadf`, the same mash saved under a different
  extension.
- `shit.txt` (41 bytes) — a debug dump somebody redirected to a file:

      octree: 04232710
      num polys: 1
      poly 0: 0

- `mpscreens.txt` (157 bytes) — the only one that is genuinely useful. It is a
  format note written for whoever came next:

      Format:  Name_Of_Multiplayer_Type<TAB>Count_Of_Files_For_Type
      gen 3 / hunt 2 / sun 2 / noon 2 / stud 2 / hold 2 / none 1 / duel 2

  Those eight names are the same multiplayer-mode vocabulary as the ELF table at
  `0x004F756C` that `07_Research/MISSION_LOGIC.md` §1.7 documents.

---

## 4. Cut content: the `/api/hidden` numbers, verified independently

The developer's brief quoted the route: 27 manifests (12 unshipped, 9 loadable), 182
rosters, 83 curiosities. I re-derived all four from the ISO without calling the
API. **All four reproduce exactly.**

    archives 66   shipped level stems 61
    manifests 27   unshipped 12   rosters 182   curiosities 83

The "9 loadable" deserves a caveat and then survives it. The route calls a
manifest loadable when **one** of its referenced assets is present — a floor, not
a verdict. I scored the *fraction* present across the whole disc instead:

    MANIFEST                        REFS  HAVE   FRAC   GRADE
    lvl_mansint.lvl                   23    23  100.0%  COMPLETE
    lvl_mansint2.lvl                  20    20  100.0%  COMPLETE
    lvl_mansinta.lvl                  28    28  100.0%  COMPLETE
    lvl_train-borked.lvl             120   120  100.0%  COMPLETE
    lvl_train.05072003.lvl            79    79  100.0%  COMPLETE
    lvl_train053002.lvl               24    24  100.0%  COMPLETE
    lvl_trainbak.lvl                  17    17  100.0%  COMPLETE
    lvl_trainmodel.lvl                14    14  100.0%  COMPLETE
    lvl_trainnew.lvl                  11    10   90.9%  MOSTLY
    lvl_core_k.lvl                     4     0    0.0%  EMPTY
    lvl_coremulti.lvl                 24     0    0.0%  EMPTY
    lvl_mansmulti.lvl                 12     0    0.0%  EMPTY

Eight are **100% complete** and one is 90.9%. That is the same 9, on a far
stricter test: every asset those eight manifests name is still on the disc.

### What the 12 actually are

**Two of them are dated.** `lvl_train.05072003.lvl` and `lvl_train053002.lvl` —
**7 May 2003** and **30 May 2002**. Somebody saved a copy of the train level
under the date, twice, a year apart, and both shipped. `lvl_trainbak.lvl` is a
third snapshot, `lvl_train-borked.lvl` a fourth, `lvl_trainnew.lvl` a fifth,
`lvl_trainmodel.lvl` a sixth. **Half the unshipped manifests are one level's
version history**, kept by hand in a folder, the way people did before anyone
put the assets in source control.

They are plain text and read like a parts list:

    Bounds
    {
    lvl_train_cargocar01.bnds
    lvl_train_tile_terrain_01.bnds
    …
    }
    Zones { … }  Props { }  Lines { … }  Boxes { }

`lvl_train-borked.lvl` is the big one — 120 references, including train cars the
shipped game does not use: `lvl_train_presidential.bnds`, `heartCar`, `mailCar`,
`logger`, `flatbed`, `boxer`, `caboose`, and — visible in the name — both
`lvl_train_coalWagon` and `lvl_train_coalWagonBad`, `steamer` and `steamerBad`.

The three **mansion** manifests (`mansint`, `mansint2`, `mansinta`) are a mansion
interior, complete. The three EMPTY ones are the interesting failures:
`lvl_coremulti.lvl` and `lvl_mansmulti.lvl` are **multiplayer versions of the
core and mansion maps** — a deathmatch mansion, listing `fountain`, `stage`,
`station`, `tombstone`, `watertower`, `skydome` — whose geometry was pulled off
the disc entirely. The manifest is all that is left of them.

### What "loadable" means, conceptually

A `.lvl` is a manifest, not a level: it names bounds, zones, lines and boxes that
the engine loads by name. For the eight COMPLETE ones **every file they name is
still present**, so there is no missing-asset reason the engine could not load
them. That is a strong statement about the data and *not* a claim the game will
boot them — level entry also goes through the mission/level tables, and I have
not tested a load. The honest framing for the developer: *the assets for eight cut
level layouts are all still on the disc, and the file that tells the engine how
to assemble them is there too.*

### 182 rosters, 61 level names that never shipped

Each is a `.enm` enemy roster for a level stem with no archive: `arena`,
`arena2`, `arena3`, `bank`, `barber`, `barnight`, `bath`, `bombcatch`,
`bombold`, `cem_kirk`, `closhop`, `estate`, `genstore`, `gunshop`, `hotelnite`,
`hotelroom`, `jail`, `jailnew`, `lawyer`, `maze`, `mine`, `sea`, `station`,
`teepee`, `tunnel`, `p75josh` (a third name — Josh), and 35 more. These are
populated enemy lists — who spawns, where, with what — for levels that were
designed and then dropped. They carry comments:

    //Duelist Goes Here
    //Ambush Group A
    //Ambush Group B
    //type table group drop  junk  health  weapon myboss amIboss  name
    //     Shooting gallery guys
    //           Cowboy version
    //Cinematics Actors
    //lvl_perfect tape scenario

### 83 curiosities

60 are `copy of …` — literally Windows Explorer's duplicate naming, shipped:
`copy of lvl_train_tile_trn30.hf`, `copy of lvl_train_tile_trn31.hf`, and so on
through the train tiles, plus `copy (2) of …` variants. 21 are `demo`-prefixed
behaviour files from whatever build was shown to press: `demoplayer.tbl` with its
own `_attack` / `_dodge` / `_search` / `_start` state machines, `dueldemo_draw`,
`dueldemo_dtcondensed_fire`, `autotargetdemo`, `breakoutdemo`, `jumpdemo`,
`endsneakdemo`. One is `lvl_train-borked.lvl`.

And one is **`konami_code.actb`** (1,384 bytes) — a behaviour file containing
**19 `pressed` events** at version 1.0. Somebody built a button-sequence
recogniser out of the AI action-table format and named it after the Konami code.

---

## 5. Traces from the build machines

### Paths, off somebody's actual PC

Four survive in the main executable (`SLUS_205.00`), and they are specific:

| string | what it tells you |
|---|---|
| `c:\soft\swat\src\airooms\spawn.cpp` | the **source tree**: `c:\soft\swat\src\`, module `airooms`. This is an assert or log line that shipped with the filename baked in. |
| `s:/swat/assets/current` | the **asset root on a mapped network drive** — `S:`, shared, "current". |
| `c:\summary.txt` | sits directly beside `Heap Overrun (%d bytes requested)`, `Lower guard word`, `Upper guard word`, `Linked list` — the memory debugger's report file. |
| `c:/usr/local/sce/iop/modules/` | Sony's IOP module path, with a `c:` glued on the front — a Windows build of the PS2 toolchain. |

A fifth is in the data rather than the ELF. `characterlineup.swui` carries a
comment to the next person:

> "The size values should be copied directly from the values as specified in
> the .xls file, mpposterpictures.xls (currently in **s:\swat\doc\ui**)."

Same `S:` drive. And it confirms, from inside the game data, that the `.xls`
design spreadsheets I decoded were the working source for the shipped UI —
`mpposterpictures.xls` is one of the 38.

### Seven source filenames survive

`spawn.cpp`, `hash.cpp`, `parse.cpp`, `string.cpp`, `bank.cpp`, `bklocbank.cpp`,
`bkwinbank.cpp`. The last three are the memory "bank" allocator, which is why
they are next to the heap-overrun strings.

### The debug menu is still in the executable

A contiguous table of command-line / debug options at `0x41500D`–`0x415107`,
immediately before the `s:/swat/assets/current` asset root:

    berrylium   memcard   attract   multiarchive   frontend
    level       lvl_sher70          noextension    player
    sublevels   stream    bootup
    unlockall   unlockcheats   unlockjournal   unlockmulti   unlockdiffs
    lvlaccess   unlockallsave
    newfades    noenemies      oldfades
    archive     multiarchivepath      path      s:/swat/assets/current

`unlockall`, `unlockcheats`, `unlockjournal`, `unlockmulti`, `unlockdiffs`,
`unlockallsave`, `lvlaccess` — the full unlock set. `noenemies` — walk the level
empty. `newfades` / `oldfades` — an A/B toggle somebody left in. `level
lvl_sher70` — a default level to boot straight into, which is a *level name*, so
the option takes one. `berrylium` is a codename and is spelled wrong (beryllium).

I have **not** tested whether any of these are reachable on retail hardware —
this is a string table, and the presence of an option string is not proof the
parser still honours it. Worth a targeted disassembly pass later; filed as a
follow-up rather than claimed.

### `example.swui` — they shipped the format's documentation

**SWAT.DAT, 3,005 bytes.** This is not a leftover so much as a gift: a complete
commented reference for the `.swui` HUD/menu format, left in the archive.

    <BUTTON:X,Y,Z:Text:LinkFileName:NewPageX,…:pop-up vs show(0 vs 1):Inputs(-1=all,1=pad1 2=pad2 etc):Data>
    //The following Special LinkFileNames are supported:
    //CLOSEUI  -closes the userinterface and jumps to next state
    //TRYCLOSEUI  -closes the userinterface only if there are no active pages
    //HIDE     -closes this page and returns to last page (used to close popups)
    //CALLB    -Calls a hardcoded function which looks at the Data
    //RESET    -Tells swat root to Reset (resets and reloads the game)

…plus `<POSITION>`, `<CENTER>`, `<BACKGROUND>` / `<IPU_BACKGROUND>` ("IPU=
compressed. The position and dimensions are ignored!"), `<FONT>`/`<FONT2>`/
`<FONT3>`, `<TEXT>`, `<READOUT>` ("does not use string table"), `<TEXTURE>`,
`<COMBO>`/`<COMBODATA>`, `<SHOPBOX>`/`<SHOPCOLUMN>`, `<LOOKUP>`/`<LOOKUPDATA>`
(with a documented "new way" and "old way"), `<MODEL>`, `<MODELCOMBOGRID>`
("e.g., multiplayer character select grid… Accepts up to 4 independent inputs"),
`<SCALESINE>`, `<FADE>` and `<PULSE>`.

**This is the spec for M14.** It is going in as a lane request.

### Cut dialogue, with the lines attached

The `.csv` sound tables have a `Comment` column, and the cut lines were left in
with their transcripts and a note in that column. From `snd_2998.csv`:

> `cbro2_siege_01` … **DO NOT USE** Cornet Brother 2 - "The Governor'll have our heads if th…"
> `cbro2_siege_06` … **DO NOT USE** Cornet Brother 2 - "Thats for Joe, Bounty Hunter!"
> `cbro3_siege_04` … **DO NOT USE** Cornet Brother 3 - "They got Joe Jr.!"

and from `snd_398.csv`, `ll_freak_14` … `DO NOT USE`. Several tables also carry a
`blank` sample row marked `unused`.

### The disc carries the Xbox version's data

The 2,541 `.csv` sound tables have a `Platform` column, and every table ships
both platforms' rows. Counted across the disc: **72,964 rows marked `PS2` and
78,114 marked `XBOX`** — on a PS2 disc. The Xbox rows are the majority. Nobody
stripped them at build time.


### Uninitialised memory, part two — and the disc has BOTH fill patterns

*Added 2026-09-14. The `.duel` finding above is 0xCD; rdr-fmt turned up 0xCC in
`.mod` while working REQ-045, and I confirmed it. They are different bugs.*

MSVC's debug CRT fills memory with a different byte depending on where it came
from. The retail disc ships both:

| byte | means | where | how much |
|---|---|---|---|
| **0xCD** | heap allocated and never written | 15 `.duel` damage files, always in a `Pattern` field | **765 bytes**, 51 fields |
| **0xCC** | **stack** never written | 2 `.mod` stubs, `krd_lod_01.mod` and `sncb_lod_01.mod` | **8 bytes**, one field each |

The two `.mod` stubs are 70 bytes, byte-identical to each other, `version: 2.12`,
and their manifest is eleven u32 of which the **eleventh is `0xCCCCCCCC`** at
offset +54, followed by three `1.0f`:

    76 65 72 73 69 6f 6e 3a 20 32 2e 31 32 00   "version: 2.12\0"
    00 00 00 00 00 01 00 00 ... 00              ten zero counts
    cc cc cc cc                                 <- uninitialised STACK
    00 00 80 3f  00 00 80 3f  00 00 80 3f       1.0  1.0  1.0

So they are empty models: every count zero, one field never initialised, and a
unit scale. Somebody exported a model with nothing in it, twice.

**Why the distinction is worth keeping.** 0xCD says a `new` was never filled;
0xCC says a local variable was never assigned. Same debug build, two different
mistakes, shipped in two different formats — and it took two lanes to see both.


### The files that break parsers are the abandoned ones — 275x

*Added 2026-09-14, after rdr-fmt found that the two `.mod` files separating
their version string with CR rather than LF are `crap.mod` and `shitgood.mod`.
Probe: `03_Tools/research/probe_anomalies.py`.*

Every decode this lane has done threw off a few files that did not fit. Listed
together they stop looking like coincidence:

    3 `.duel`   unbalanced braces (2 opens, 6 closes)   x / xx / xxx prefixed
    2 `.mod`    version string separated by CR          crap.mod, shitgood.mod
    1 `.anim`   matches no codec at all                 red_shit.anim
    1 `.rec`    size not a multiple of 16               shit.rec
    4 `.nav`    cached distances stale                  fortold, old_, old2_
    3 `.swui`   no dialect reads them                   blank, blank_jp, wip

**11 of 17 anomalous files carry a leftover name — 65%, against a 0.24% base
rate across all 127,365 archive entries. A 275x lift.**

**The six exceptions are listed too, because a pattern quoted without them is
advocacy**: the two `0xCCCCCCCC` `.mod` stubs, the misfiled heightfield and
both `blank` screens are broken with perfectly ordinary names.
`lvl_perfect.nav` is the honest near-miss — it *was* superseded, by
`lvl_perfnew`, so it is a leftover in fact, but nothing in its name says so and
the pattern set gets no credit for it.

**The practical consequence, which is the point:** on this disc, a tool that
hits a malformed file should check the name before it rewrites the parser. Two
lanes have now spent time on exactly that mistake — I nearly filed three
`.duel` files as a format problem before checking their braces, and rdr-fmt's
`.mod` reader had a `[\x00\n]` class that dropped `crap.mod` and `shitgood.mod`
into an "unexplained: 2" bucket.

**And the two it dropped are the largest text models on the disc bar one.**
`crap.mod` is 328,078 bytes and `shitgood.mod` 326,790 — and they are near
twins: identical `verts: 1714` and `tex1s: 1761`, differing only by one
material and one packet (8/76 against 7/75). Both are structurally perfect;
all seven verified header counts hold. Somebody exported a model, called it
`crap`, fixed one material, and saved the result as `shitgood`.

**The largest text model on the disc is `copy (2) of nrd_lod_01.mod`, 445,370
bytes** — Windows' own duplicate-file naming, shipped on the master. That is
the finding, and it needs no ranking to stand up.

> **Correction, same day.** I first wrote that *"the three biggest
> hand-authorable models in the game are all developer scratch files."* **That
> is wrong**, and rdr-fmt caught it. The order is scratch, **ordinary**,
> scratch, scratch:
>
>     445,370  copy (2) of nrd_lod_01.mod   scratch
>     336,795  ydg_lod_01.mod               ORDINARY
>     328,078  crap.mod                     scratch
>     326,790  shitgood.mod                 scratch
>
> The second biggest is an ordinary asset. The ranking was printed in my own
> probe output, which I read past — the same failure this section is about.
>
> The 275x measurement above is untouched by this; the flourish was a separate
> claim resting on nothing, and it is the kind of line that gets repeated. By
> the standard two paragraphs up — *a pattern quoted without its exceptions is
> advocacy* — it had to go.


### The slasher roster — enemy AI named after horror villains

*Found 2026-09-14 while walking the reference graph. `probe_refs.py`.*

The bridge-sniper enemies have names, and the names are the slashers:

| villain | files |
|---|---:|
| Michael *(Myers)* | 64 |
| Vorhees *(Jason Voorhees)* | 34 |
| Myers | 34 |
| Leather *(face)* | 32 |
| Kruger *(Freddy Krueger)* | 27 |
| Freddy | 27 |

The pattern is one behaviour file per villain per position:

    atopbridge_variationshoot_freddy.acttbl
    atopbridge_variationshoot_jason.acttbl
    atopbridge_variationshoot_kruger.acttbl
    atopbridge_variationshoot_leather.acttbl
    atopbridge_variationshoot_michael.acttbl
    atopbridge_variationshoot_myers.acttbl
    atopbridge_variationshoot_vorhees.acttbl

…and the same seven again for `belowbridge_`, `bridge3rdwave_` and the rest.
Somebody needed seven names for seven enemy slots on a bridge and reached for
*Friday the 13th*, *Halloween*, *A Nightmare on Elm Street* and *The Texas
Chain Saw Massacre*. Note they used **both** Jason Voorhees' first and last
names as separate enemies, and the same for Michael Myers — which is how the
roster reaches seven.


---

## 6. What this changes for the project

Four things here are not archaeology, they are work:

1. **`example.swui` is the M14 spec.** Filing for rdr-app/rdr-fmt.
2. **`.txt` is decoded** — UTF-16LE subtitles — and 31 files have a shipped
   encoding bug. `UNKNOWNS.md` updated.
3. **Eight cut level layouts have 100% of their assets present.** If a level can
   be pointed at a manifest, that is eight levels' worth of content already on
   the disc — relevant to M21.
4. **`s:\swat\doc\ui` confirms the `.xls` sheets were the live source** for
   shipped UI values, which raises the value of the `.xls` decode.

---

## Reproducing all of it

    python3 03_Tools/research/probe_leftovers.py          # table form
    python3 03_Tools/research/probe_leftovers.py --json    # 07_Research/leftovers.json

Read-only against `01_Vanilla/RDR_VANILLA.iso`. No lock taken.
