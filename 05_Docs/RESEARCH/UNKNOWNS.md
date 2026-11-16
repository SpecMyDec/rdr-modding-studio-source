# UNKNOWNS — every file type on the disc, and how much of it we actually read

Owner: **rdr-re**. Standing deliverable, per `STATE-CONTRACT.md` Lane 4.

Scope: the vanilla disc, `01_Vanilla/RDR_VANILLA.iso`, opened read-only.
**103 loose files** (66 `.DAT` archives, 22 `.pss` videos, 12 `.irx` modules,
the ELF, `SYSTEM.CNF`, `IOPRP.IMG`) and **127,365 entries inside the archives**
across **139 distinct extensions**.

Everything below is produced by a script in `03_Tools/research/`. Nothing here
is asserted from one file: a status changes only when a probe reproduces it
over the whole corpus. Re-run order:

    python3 03_Tools/research/probe_inventory.py      # walk the disc
    python3 03_Tools/research/probe_coverage.py       # who mentions what
    python3 03_Tools/research/probe_textform.py       # text or binary
    python3 03_Tools/research/probe_textgrammar.py    # the shared grammar
    python3 03_Tools/research/probe_textedit.py       # surgical edits
    python3 03_Tools/research/probe_props_gap.py      # what props.py misses
    python3 03_Tools/research/probe_pup.py            # the .pup format
    python3 03_Tools/research/probe_actb.py          # .actb/.stateb opcodes
    python3 03_Tools/research/probe_elf_keywords.py  # the AI vocabulary
    python3 03_Tools/research/probe_hf.py            # terrain heightfields
    python3 03_Tools/research/probe_xls.py           # the design spreadsheets
    python3 03_Tools/research/probe_xls_strtbl.py    # sheets vs .strtbl ids
    python3 03_Tools/research/probe_fonttex.py       # glyph atlases
    python3 03_Tools/research/probe_pck.py           # the UI memory images
    python3 03_Tools/research/probe_iani.py          # cutscene sampled animation
    python3 03_Tools/research/probe_fields.py \
        --md 05_Docs/RESEARCH/FIELDS.md              # the field catalogue
    python3 03_Tools/research/probe_glossary.py \
        --md 05_Docs/RESEARCH/GLOSSARY.md            # name glosses (INFERENCE)
    python3 03_Tools/research/probe_animabbrev.py \
        --md 05_Docs/RESEARCH/ANIM-ABBREV.md         # anim codes (INFERENCE)
    python3 03_Tools/research/probe_unknowns.py --table   # the table below

All nineteen are read-only against the ISO and need no lock (`probe_elf_keywords.py`
also reads the unpacked executable in `07_Research/`). Their JSON lands in
`07_Research/`.

## What the statuses mean

| Status | Means |
|---|---|
| **DECODED** | A module parses the format as a format, and coverage is ≥ 99% of content lines (text) or every field (binary). |
| **PARTIAL** | Something reads it, but not all of it. A text format under the generic `props.py` reader is PARTIAL by default — it sees `Key value` lines and copies everything else through untouched. |
| **UNKNOWN** | Nothing in `03_Tools/rdrstudio/` parses it. |

A format we read one field of is PARTIAL, not DECODED. `props.py` copying an
unparsed line verbatim keeps round-trip byte-exact while the field is still
invisible to every editor built on it — that is PARTIAL, and the **props
coverage** column is there so the number is never in doubt.

Current totals: **32 DECODED · 98 PARTIAL · 9 UNKNOWN.**

---

## 1. The headline: 44% of this disc is plain text, in one grammar

`probe_textform.py` inflated every one of the 127,365 archive entries and
classified it. **109 of the 139 extensions — 56,507 files, 44.4% of all
entries — are pure ASCII**, and every file of those types is ASCII, not most.
A further 8 extensions are MIXED (`.mod`, `.csv`, `.swui`, `.txt` and four
others carry both). 22 extensions, 30,854 files, are true binary.

`probe_textgrammar.py` then tested whether the text formats share one grammar,
over all 56,399 files that inflate to clean ASCII:

| Claim | Result |
|---|---|
| Parse → re-emit reproduces the input **byte for byte** | **56,399 / 56,399** |
| Braces balance (`{` is a *token*, not a line marker) | **56,396 / 56,399** |
| `NumFoos N` predicts how many `Foo` blocks exist | 11,924 honoured, **10** mismatched, 10,252 are plain integer fields |

The three brace failures are `xlvl_cover3.duel`, `xx_lvl_cover3.duel` and
`xxx_lvl_cover3.duel` — all `x`-prefixed dead assets, all closing four more
braces than they open. The live `lvl_cover3.duel` balances. **These are broken
in the shipped game data, not in the parser.**

`probe_textedit.py` then proved the property the roadmap actually asks for at
M7 — *"edits a field, writes, re-reads it byte-exactly, and asserts every
unedited byte is untouched"*. For every ASCII entry on the disc it rewrote one
numeric token in place and diffed:

> **52,712 files edited; 52,712 had a byte-clean one-token diff; 52,712 read
> back the new value. 106 of 109 extensions PASS, zero FAIL.**

The three non-passing extensions are `.lvl`, `.accu` and `.enemies` — they hold
no numeric scalar field to edit, so the probe had no target. Not failures.

**What this means for the roadmap.** M7's nine formats (`.swt .msd .dam .hot
.rep .skel .sphs .tail .acc`) are all in this set, and so are M10's `.tbl`,
M11's `.enm` and M20's `.aisp`. The value-span rewrite that `props.apply`
already performs is sound across the entire corpus, not just the curated
fields. The per-format work that remains is naming and validating fields, not
decoding structure.

### The counted-record convention

25 extensions honour a `Num<Thing> N` record count with **zero** exceptions —
`.gait`, `.bxes`, `.sphs`, `.hot`, `.lgt`, `.glows`, `.acc`, `.armr`, `.spwn`
and 16 others. Two conventions are in use and both are real: `.nav` counts by
*type* (`NumNodes` → `Node` blocks), `.sphs` counts *total* blocks
(`NumBoundSpheres 5` = 1 `BoundHotdog` + 4 `BoundSphere`). **Anything that adds
a record — M11's "add an enemy" — must bump the matching header**, and
`probe_textgrammar.py` says per-extension which rule applies.

Not every `Num*` key is a count. 10,252 of them name no block at all:
`.crk`'s `NumSettleFrames`, `.sm`'s `NumRoundsToWin` are gameplay values that
merely start with "Num". The probe reports those as fields rather than
failures, because calling them failures would invent a convention the game
does not follow.

### The 10 genuine count mismatches

Nine `.skel` files and one `.dof`. `blk.skel` declares `NumBones 52` and
contains 62 `bone ... {` blocks (confirmed independently:
`grep -cE '^[[:space:]]*bone[[:space:]]' → 62`). `own01.skel` declares 52 and
has 53. **Shipped data where the header and the body disagree** — anything that
trusts `NumBones` to size a buffer reads a short skeleton. Registered as
**C-035**. No request filed: `skeleton.py` already scans for bone blocks and
keeps the declared count separate, so the tool is right today — the entry
exists so the next `.skel` parser does not repeat the assumption.

---

## 2. `.pup` — 1,200 files, nothing read them, now decoded

The largest file type on the disc that **no module in `03_Tools/rdrstudio/`
mentioned at all**: 1,200 files, 24–739 bytes. "pupshow" is the engine's name
for a scripted cutscene, and the format is its cast list plus a track timeline.

    u32     count                  actors cast in this cutscene
    count x {
        char[]  name               NUL-terminated ASCII
        u32     role
    }
    u32     nframes
    nframes x {
        u32     time
        u8      state[count]       one byte per actor, in roster order
    }

`probe_pup.py`: **1,200 of 1,200 parse to EOF exactly, 0 fail.** The parse must
land precisely on the last byte — no slack, no overrun — so the `state[count]`
tie between the two halves is load-bearing and proven, not assumed.

Role codes, by what they are ever attached to:

| Role | Slots | Evidence |
|---|---:|---|
| `0x07` | 1,200 | Only ever the name `camera`, exactly once in every file. |
| `0x0f` | 1,344 | 1,334 of 1,344 names end `Facial`. |
| `0x10` | 143 | 141 of 143 end `Facial`; the names are Red's (`srdFacial`, `player1Facial`). |
| `0x06` `0x09` `0x0c` | 637 | Carry the `_anim` names (`downExit_anim`, `minecart_anim`). |
| `0x9f` `0xb4` `0xa2` `0xb1` … | 931 | Bare three-letter stems — `srd`, `kng`, `jck`, `npb` — the same stems `.cr` uses for `FileNameRender`. |

**Proven:** the container, and the 0x07/0x0f/0x10 name correlations.
**That inference was WRONG and is now disproved (2026-09-14).** The "role" is a
**CHANNEL COUNT**, not an id. `probe_pup_channels.py` fits `N = Σ w[role]` — N
being the per-frame float count of the matching `.iani` — over **352 files and
19 codes**: **352 of 352 exact, max error 0.0**, every weight an integer, and
each integer equal to its own role code (0x07→7, 0x0f→15, 0x9f→159, 0xb4→180).
17 of 19 are multiples of 3, consistent with three channels per bone.

Cutscene casting is a modding target in its own right — which character stands
in which cutscene, and whose face animates. Filed as REQ-018.

---

## 3. The corruption route this survey found

`props.py` — the module every text-format editor in the tool is built on —
tracks blocks with two regexes that require the brace to own its line:

    _OPEN  = ^[ \t]*([A-Za-z_]\w*)?[ \t]*\{[ \t]*$
    _CLOSE = ^[ \t]*\}[ \t]*$

The disc uses two spellings neither covers: `Node 0 {` (a block head with an
argument) and ` 1 }` (a `}` sharing its line with data). The first is recorded
as a **field** named `Node` whose value is `0 \t{`; the second never pops.

`probe_props_gap.py`, over the whole corpus:

> **11,555 files offer 270,750 editable fields whose value still contains the
> block's opening brace.** Editing any one of them through the normal path
> writes over the `{` and unbalances the file.

Demonstrated end to end on three files, `.nav`, `.sphs` and `.skel`:

    blk.skel   field 'bone' = 'root {'   ->  apply(bone=99)
    line 3  BEFORE  b'bone root {'
    line 3  AFTER   b'bone 99'
    open braces: 62 -> 61     brace balance: 0 -> -1

The worst-affected are `.nav` (71,844 such fields), `.bxes` (48,244), `.lnes`
(42,684), **`.skel` (33,557 — an M7 format)**, `.znes` (24,802) and `.gait`
(21,160). `props.apply` is reachable from live routes, including
`field_bulk_set`, which applies one edit across many files at once.

Two things this is **not**. It is not a round-trip failure — a no-op
`apply(data, {})` is byte-perfect on all 56,399 files, which is exactly why it
went unnoticed; this is the third instance of that failure mode `props.py`'s
own comments describe (the CRLF `_CLOSE` miss, the dotted-key miss). And it is
not a bug in `.skel` *editing*: `skeledit.py` goes through `skeleton.py`, not
`props.py`, and is unaffected. The exposure is the generic Files tab.

Filed as REQ-017 (rdr-fmt owns `props.py`).

---

## 3b. `.actb` / `.stateb` — five opcodes named without disassembling anything

20,494 files carry the `version` magic (833 of the 21,327 with these
extensions do not and are something else). `actb.py` reads their structure and
states in its docstring — and in `README.md:589` and `ROADMAP.md:231` — that
naming the opcodes requires disassembling `SLUS_205.00`.

It does not, or not entirely. Every inline string in these files is preceded by
a 32-bit word. `probe_actb.py` scores those words against namespaces built from
**independent** files — the `*State:` values in the 3,275 text `.tbl` files,
the keys in the 203 `.flags` files, the 1,200 `.pup` names, and every file stem
on the disc:

| Opcode | Uses | Argument resolves to | Reading |
|---|---:|---|---|
| **1005** | 40,427 | `.actb` 89.7% + `.acttbl` 9.8% of uses | run this action table |
| **1004** | 42,691 | a declared label, 99.4% (a file only 1.2%) | go to this state |
| **1003** | 1,581 | `.tbl` names, 99.1% | use this table |
| **1566** | 943 | `.pup` cutscene names, **100%** | play this facial show |
| **1522** | 341 | `.pup` cutscene names, 96.8% | play this cutscene |

1004's argument matches no external namespace, so it is scored against bare
identifiers declared inside the corpus — **not** independent evidence. 1005 is
therefore run through the same test as a control: **1004 scores 99.4% where
1005 scores 4.2%.** The 24× separation against a known-different opcode is the
evidence; the raw percentage on its own is not.

An earlier cut scored against every bare identifier in the `.actb` files and
returned a confident 100% for opcodes that merely name variables. A set that
contains the answer cannot test it.


### The AI vocabulary, and the wall behind it

`probe_elf_keywords.py` reads the unpacked executable
(`07_Research/SLUS_205.00.unpacked.bin`, a raw memory image at 0x00100000, not
an ELF) and recovers the engine's **complete AI query vocabulary**: two
stride-8 `(u32, char* name)` tables at 0x3f9564 and 0x3f9ba4, **248 names**,
exactly the bare identifiers the `.actb` corpus uses — `angle`, `dist`,
`blocked`, `gundrawn`, `health`, `lastseentime`, `numalive`, `menuopen`.
Written out to `05_Docs/RESEARCH/ai-vocabulary.txt`. **This is the full list of
conditions a behaviour table can ask about**, and it bounds what is authorable.

Phase matters when looking for these: the larger table starts at 0x3f9564,
which is 4 mod 8, so a stride-8 scan anchored at zero walks straight past it.

**The negative result is the more useful half.** The number paired with each
name is **not** the code the `.actb` files carry for it. Six readings were
tested — the word before the name and the word after it, under both phase
alignments, plus the table index — over **49,105 keyword occurrences in 4,000
files**. Best score **2.0%** against a noise floor near 1%. All six fail.

The opcodes are therefore resolved by code, not by a table in the data segment.
**Nor by a disassembly**: §1.7 of `MISSION_LOGIC.md` shows the names are not in
the executable in any form, so the remaining work is naming 296 handlers from
their behaviour. Raised as **REQ-021**, and corrected there after I read it.

**Still unknown, and not to be overstated.** Most opcodes are unnamed. The
low-numbered words (0, 2, 5, 11, 17 — 240,000+ uses between them) are
operand/type tags this probe does not settle. `.actb`/`.stateb` stay **PARTIAL**
in the table below, and the neighbouring claim *"control flow is not
authorable"* is **UNTESTED**, not disproven — knowing what an operand names is
not knowing the record layout well enough to write one.
Registered as **C-036**; filed as **REQ-020**, with the disassembly itself
raised separately as **REQ-021**.

**And the rest cannot be finished by a disassembly either — corrected
2026-09-14.** `07_Research/MISSION_LOGIC.md` §1.7 establishes by exhaustive
search that no action-opcode name table exists in the executable: every 4-byte
alignment at strides 4, 8, 12 and 16 across all 4,497,384 bytes, exactly
**four** `{char*, small-int}` tables, and **none holds a value in the
1001..1571 action range**. `nofire` appears 273 times in shipped `.actb` data
and **zero times** in the executable. The names were resolved by the offline
authoring toolchain and are not on this disc; what is left is naming **296
handlers from behaviour**, which is 296 jobs rather than one.

Those four tables are the same four `probe_elf_keywords.py` found from the
other end — 0x004F7568, 0x004F7FD4, 0x004F9564, 0x004F9BA4 — which is exactly
why its six-hypothesis test scored 2.0%: there was nothing there to find. I had
not read §1.7 when I wrote §3b and §3c, and REQ-021 carries the correction.

**Update 2026-11-12 (deep dive): one exception to "no action names on the disc".** The ARGUMENT of opcode 1560 is
named: the 61 strings at 0x00527C35 (ShowFocusBar .. DoneTalking) are its enum, proven by corpus (1,756 uses, all
0..60, names predict neighbours and file names) and by code (61-way switch at 0x002E5260, Show/Hide pairs
set/clear the same HUD bit). `probe_hud_commands.py`; baked into `aivocab.HUD_COMMANDS`. The opcodes themselves
still have no names; file-name hints for ~40 of them are in DEEPDIVE-2026-11-11 (hints, not proof).

**REQ-021 is CLOSED — NO (the reviewer, 2026-09-14).** The disassembly is not
authorised. **`.actb`/`.stateb` stay PARTIAL and that is their end state**
unless the developer asks otherwise: five opcodes named, the 248-name vocabulary
extracted, the remaining action opcodes left unnamed because they are not on
this disc to find.

---

## 3d. The remaining unknowns: three decoded, one narrowed, one still opaque

The five extensions nothing in `03_Tools/rdrstudio/` parsed, worked in size
order. Three now have an exact structure proved over their whole corpus.

### `.hf` — terrain heightfields. 67/67, DECODED

    u32  width, height
    f32  min_x, min_z, max_x, max_z      world extent of the patch
    f32  height[width * height]          row-major

**All 67 files satisfy `size == 24 + w*h*4`** with a sane grid, a
non-degenerate extent and no NaN or absurd height anywhere. Grids run 20×30 to
256×256 — 42 of the 67 are 21×31 — and heights span −21.1 to +18.5 across the
disc. This is the terrain under the levels, and it is the most directly
moddable thing in this section. Filed as **REQ-022** (rdr-3d).

A size check alone would pass on a wrong field order, so the probe also
asserts every height is finite and in range, and that `max > min` on both axes.

### `.xls` — the design spreadsheets shipped on the retail disc. 38/38, DECODED

`D0 CF 11 E0 A1 B1 1A E1` on all 38: OLE2/CFBF, with a BIFF8 `Workbook`
stream inside. These are Excel workbooks, and every one of them has the same
nine columns:

    identifier | font | language | string | scale x | scale y |
    offset x | offset y | flags

**38/38 parse, 0 fail, 0 unresolved cells, 26,754 cells recovered.** `swat.xls`
alone is 1,199 rows. Parsed with stdlib only — `olefile`/`xlrd` are not
installed and a dependency to read 38 files is the wrong trade.

The trap, and the reason an earlier cut returned `?` for 854 strings: BIFF8's
shared string table is split across CONTINUE records and a string may straddle
the boundary, where the first byte is a **fresh encoding flag** for the
remainder — a 16-bit string can continue as 8-bit. Concatenate the payloads and
parse straight through and the first few hundred strings come out right before
it walks off the end, which looks like a corrupt file and is not.

**What this is NOT.** I expected these to recover identifiers the tool could
not otherwise see, because `.strtbl` addresses records by a PJW/ELF hash. They
do not: `probe_xls_strtbl.py` measured it, and `.strtbl` carries its own key
table, so `StringTable.name_of()` already names **101,778 of 101,778** records.
The spreadsheets add **zero** names.

What they do instead is **confirm** them. All **1,394** spreadsheet identifiers
hash to ids that exist on disc — 0 collisions, 0 orphans in either direction —
which is independent verification that `strtbl.py`'s hash is the right one. The
design value is the columns the binary does not obviously carry (`language`,
`offset x/y`) and the authoring intent.

### `.fonttex` — glyph atlases. 242/243, DECODED

    0x00  f32  scale (1.02)
    0x2c  u32  glyph count N
    0x30  N x { u32 codepoint; u8 x, y, w, h; s16 y_offset }
          u32 range count R; R x u32 end index, last == N

Atlas extent is **≤128×128** across the whole corpus, glyph counts 8–451,
codepoints U+0021–U+FF50. These are the fonts the `.xls` `font` column names —
`berrylium`, `arial14`, `courier18`. Filed as **REQ-023** (rdr-fmt).

Two mistakes worth recording, because both parsed without complaining:
reading `x` as a **u16** rather than two `u8`s (glyph 93 of `arial14` then
sits at x=25922, which no atlas has — but the first row of glyphs looks
perfectly sane); and assuming the file ends at the last glyph, when a 4–28 byte
range table follows whose final entry equals N.

Two honest exceptions rather than a clean sweep. `instructions2.fonttex` and
`instructions2_glw.fonttex` list **codepoint 191 twice**, at different atlas
positions — that is in the shipped data, so the probe allows equal codepoints
and counts them. And `default.fonttex` carries scale **1.00**, starts its
glyphs at 0x14, and its records are not a fixed 10 bytes: 43 parse cleanly and
then `M` takes 11. Its range table is valid and agrees, so the file is intact
and it is the record shape that differs. It is reported **VARIANT**, not FAIL,
and it is the one `.fonttex` this probe does not claim.

### `.iani` — the sampled animation for a `.pup` cutscene. Container DECODED

    0x00  char[4]  "SIA0"
    0x04  u32      actors    -- the cast of the matching `.pup`
    0x08  f32      30.0      -- frame rate, on 357 of 357
    0x0c  u32      frames
    0x10  frames x C float32 -- C channels per frame, C NOT STORED

**What settles it is a cross-file identity, not the header.** Every `.iani`
shares its stem with a `.pup`, whose roster length is known independently
(`probe_pup.py`, 1,200/1,200):

> **`f@4` equals the `.pup` actor count on 355 of 355 stems that have both —
> 100%.**

So the two are the same cutscene: `.pup` casts it and drives a sparse
timeline, `.iani` carries the per-frame samples. Their frame counts match on
**0 of 355**, which is the expected answer rather than a failure — `.pup`
counts keyframes, `.iani` counts samples.

**C is not in the HEADER — but it is in the file. Corrected 2026-09-14 after
rdr-fmt checked it.** `fac_brim01_barman_01` and `fac_duel_player1_01` have
byte-identical headers — `actors=2, frames=91` — and are 8,024 and 8,388
bytes, i.e. **C=22 and C=23**. No header field distinguishes them: that much
was measured properly. But *"therefore C is unavailable from the file"* does
not follow, because **the length is part of the file**:

    C = (size - 16) / (frames * 4)

divides exactly on **354 of the 355 files carrying samples**, including
**119 of 119** with more than two actors. The 364-byte gap between those two
examples is precisely one channel of 91 frames of f32. `probe_iani.py` has
computed C this way since it was written — **the write-up contradicted the code
beside it.** The `fac_*` clips carry the claim: **232 of 234 agree on
C=22** with actors=2, a camera plus one face (the same `0x07` camera slot that
appears in every `.pup` roster). The `lvl_*` cutscenes run actors=3..26 and C
scatters from 172 to 2,161 — what a sum over differently-rigged actors looks
like.

**And the caution that produced the error is worth keeping, half retracted.**
"(size−16) divides by 4×frames" is nearly free at these sizes *as evidence for
a structure*, which is why I would not lean on it — and that reasoning had
already saved me once, on the stride claim rejected earlier in this file. But
it is not weak as a **computation**: dividing exactly on 354 of 355 is how C is
recovered. Doubting it as evidence was right; concluding from that doubt that C
was unavailable was not, and it is the same shape as C-010 — a true measurement
with an inference one step past it.

**The count is now confirmed from OUTSIDE the file** (2026-09-14). rdr-fmt was
right that dividing the length settles layout, not meaning. The `.pup`
regression supplies the meaning: N equals the sum of the cast's role codes on
352 of 352 files, and those codes are channel counts.

**Still open: attribution, a different question again.**
Which track belongs to which actor, and to which bone or morph of that actor's
rig. Recovering C does not imply it. A reader that decodes C tracks of `frames`
floats needs nothing but the `.iani`; one that says which track drives whose
jaw may still need the cast from the `.pup`. Untested. `.iani` stays
**PARTIAL**.

One file fails outright and is named rather than rounded away:
`lvl_tomb_pupshow_03.iani`, 7,157,504 bytes, 22 actors, 675 frames — 2,650.92
floats per frame. 119 of the other 120 multi-actor clips divide cleanly, so it
is not an actor-count effect.

### `.pck` — a relocatable memory image. Container DECODED, contents not

82 files, 11 KB to 1.8 MB, **all stored uncompressed**, 78 in SWAT.DAT and 4 in
FRONT.DAT. The names are UI screens — `shop_gun`, `mprewards`,
`weaponselection`, `memoryicon`, `logo01`, `j01_01_mainmenu` — and no known
magic appears anywhere inside one.

    0x00  u32  base   the address the payload is loaded at
    0x04  u32  1      on all 82
    0x08  u32  0      on all 82
    0x0c  u32  size   == file size - 16, on all 82
    0x10  payload, `size` bytes, where payload[i] lives at address base + i

Only five distinct base addresses exist across the 82 files (0x188b80 on 65 of
them), which is what you would expect of screens loaded one at a time into the
same buffer.

**The load-address claim is tested, not asserted.** If `base` is really where
the block lands, words in the payload that point back into it must fall in
`[base, base+size)` far more often than chance:

| | |
|---|---|
| `memoryicon.pck` | 5.967% of its words land in an 11,664-byte window that is 0.00027% of the address space — **21,972×** |
| all 82 | every file enriched, worst case **17×** (`weaponselection.pck`) |
| all 82 | **73.0%** of the 165,947 pointers so found are 4-byte aligned, against 25% for random data |

The header reads as a UI node: `memoryicon.pck` carries floats **640.0** and
**480.0** where a screen rect belongs, `j01_01_mainmenu.pck` has 512.0/256.0,
with RGBA colours beside them and block pointers at +0x1c, +0x20 and +0x54.
About 1% of every file is `CD` — MSVC's uninitialised-heap fill — sitting
exactly where struct padding would be.

**Three hypotheses this killed, one of them mine and load-bearing.**

- **Not a VIF/GIF display list.** `vifwalk.walk`, which requires tokens to tile
  the packet with no gap, rejects the payload at its first code from every
  plausible start offset.
- **No evidence for a vtable object graph.** If pointer targets were C++
  objects starting with a vtable, the word at each target would be an
  ELF-range address. It is, 3.3% of the time, against 2.9% for random in-block
  offsets — identical to within noise. On the ten smallest files alone it came
  out 3.1% against 4.9%, which reads as evidence *against*. Neither survives
  the full corpus: the test does not discriminate, and the model is
  **unsupported, not disproved**.
- **The sector-alignment argument is worthless.** All 82 `.pck` sit on
  2,048-byte boundaries against a 41.7% disc-wide baseline, which looks like
  strong support for "these are DMA'd to a fixed address". It is not: **every
  file of 2 KB or more on this disc is sector-aligned**, all 30,993 of them.
  Size explains it completely. I had already drafted a prediction on top of
  this before checking the baseline.

**The one thing this does explain.** `dave.py` pins `logo01.pck` to a sector
boundary because SWAT.DAT would not repack without it — found by bisecting
47,101 blocks over 17 emulator runs — and the comment says the list is "almost
certainly incomplete". A file that is a raw memory image full of absolute
addresses is exactly the kind that cares where it lands. That makes the other
81 `.pck` the obvious next suspects. **It is a hypothesis, not a finding**: the
alignment evidence that would have supported it turns out to be vacuous, and
nothing here has been booted. Recorded in REQ-024 for whoever next touches
repack, clearly labelled.

**Update: the objects are decoded** (after AlgumCorrupto's RAGE-SWF-research, `flashpck.h`). The payload is the
game's Flash player objects dumped as they lay in memory: the root (swfFILE) at payload 0 holds a pointer list, one
per object id, and each object starts with a type byte (1 shape, 2 sprite, 3 button, 4 bitmap, 5 font, 6 text, 7
edit text, 8 sound). Walked on all 78 files of the user's set, the object counts match the converted `.swf`
(j01_01_mainmenu: 21 edit texts, 2 fonts, 9 bitmaps, 7 buttons, 7 sprites), and every static text decodes against
its font (glyph indices into the font's char codes; bad glyphs only in `popupsx_01`, 3 of 667). Allocations are
16-byte aligned (4-byte for a few), which is what lets an edited string use the 0xCD fill up to the next 4-byte
boundary. Layout in `rdrstudio/pck.py`; text editing ships as TEXT > Flash screens.

**Second update: drawn in full** (`rdrstudio/pckscene.py`, `ps2gs.py`), everything checked against the converted
`.swf` where it has the answer:

- **The game draws text from bitmap glyph atlases, not shapes.** A font (5) has, past the reference's fields, an
  ASCII -> glyph map (128 bytes at +0x18), a size count at +0x98 and that many pointers from +0x9c to a size block:
  atlas info ptr, f32 1/w and 1/h, glyph metrics ptr, u16 size (twips), u16 glyph count. A glyph is 12 bytes: u8 atlas
  x in 2-pixel units, y, w, h, s16 left / top / advance (1024ths of the size), kerning. The reference's per-glyph
  "pointers that can't be found" point nowhere: they are left over from the converter's heap. The advances match
  the font's advance table exactly. The full UI font carries an atlas per size the screen uses (24 and 22 px).
- **Pictures** (bitmaps and atlases): 256 colours = PSMT8, 16 = PSMT4, both read by the GS from a PSMCT32 upload.
  The 4-bit one has no reference decoder; it is the PSMT4/PSMCT32 address maps, with the upload rectangle the one
  whose words are the ones the texture reads (a 128x64 atlas is uploaded 32x32, not 64x16 -- assuming w/2 x h/4
  put everything in the left half). All 2,018 bitmaps of the 78 decode; the atlases read as clean letters.
- **Place matrices are packed**: flags bit 0 -> s16 sx, sy in 8.8, bit 1 -> s16 r0, r1 in 8.8, then s16 x, y in
  twips; the instance name follows in the same block. Every row of j01_01_mainmenu matches the .swf
  (0x00ff = 0.9961, 0x011d = 1.1133, 0xffb3 = -0.3008). Commands flagged 0xcd have no matrix pointer.
- **Shapes** are convex polygons (2,655 quads, 454 triangles, 6 more, the 16-point circles), 94% filled with a
  bitmap; a fill's matrix maps pixels to the bitmap's 0..1 square. Fill entries 36 bytes, stroke entries 8.
- **Edit text box**: [width, height, xmin, ymin] in 1024ths of the size (6869 x 480 / 1024 = 3220 twips = the .swf's).
- **Buttons**: u8 record count +9, 40-byte records (states, depth, f32 matrix, Q2.6 colour transform, object ptr).

**A .pck that GROWS works in the game** (2.0.0, measured): font fill, longer strings and new pictures append to the
payload and repoint, and the screens show correctly. Two conditions made it work, both now enforced on every save:

- **The header's size follows the payload** (+12, file size - 16).
- **A texture's upload fields follow its size.** A picture's header holds +0x2a = data size in 256-byte units,
  +0x30 storage (1 swizzled, 5 / 6 linear), +0x31 = (w/2) / W32 and +0x32 = H32, the PSMCT32 rectangle the GS
  uploads it as. The game's own are as square as can be (W32 = H32 or W32 = 2 x H32: 64x64 4-bit -> 32x16, 128x64 ->
  32x32, 256x256 -> 128x64). A filled font atlas that kept the old fields drew as scrambled letters; with them
  rewritten it draws clean (`pckscene.set_tex_header`, `repair_textures` on save).

**Sounds**: the sound objects (type 8) are empty stubs. A screen's ActionScript creates `new Sound()` and calls
`attachSound("<name>")`; that name is a row of the bank tables (section 3g), which is how a screen's sounds are found
(`pckavm.attached_sounds`, read from the constant pools and a stack walk).

`.pck` stays **PARTIAL** only for AVM1 (read, not edited).
(When this section was first written the object layout was unknown.)

---

## 3g. Found while building 2.0.0 (the fork): audio, streams, textures, levels, animation

All measured on the disc unless marked. What the Studio does with each is in [FORK-CHANGES.md](FORK-CHANGES.md).

### Sound bank tables -> wave: the whole chain

`snd_<n>.csv` (SWAT.DAT) rows, PS2 columns: **name, code, bank, program, key**. Column 1 is the sound's code, not a
wave; column 4 a key. The bank's `.hd` (BANKS.DAT) program holds **splits**: per program a u32 offset to its split
table, a u8 count and a u8 entry size; a split is u16 sample set, u8 key low, u8, u8 key high. The key picks the
split, the split names the **set** (`Sset`), the set a **sample** (`Smpl`), the sample a **wave** (`Vagi` index into
the `.bd`). Reading column 1 as the wave, then the set number as the wave, both played the wrong sound (most landed on
one page-turn sound); the full chain is right against the game. `vag.key_sets`, `vag.set_waves`.

### `.stm` streams: what a loop needs

IMA ADPCM, high nibble first, channels interleaved in 64-byte blocks. Header: +32 loop flag, +36 the loop restart as
a byte offset into the data, +40 / +44 the ADPCM predictor and step index of each channel at that point. There is
**no loop-end field**: a looping stream plays to its end and jumps to the restart. Rates seen: 48,000, 24,000, 12,000.
The restart and the length must both sit on a whole interleave unit, 64 x channels x rate / 24,000 bytes (128 samples
at 12 / 24 kHz, 256 at 48 kHz). A stream whose length was not a whole unit played past its intended loop and came back
distorted (a third-party creator's output, on a level's music); cut to the unit, it loops clean (untested in game for
every rate; tested for 24 kHz stereo). `.stm` stays **PARTIAL** (the remaining header words are not all named).

### 4-bit textures in level bundles are swizzled too

Level `RSC0` bundles hold MIP0 textures with the same header as above. `0x30 == 1` (swizzled) occurs on **4-bit**
textures as well as 8-bit: MANSINTB has 24 of 72. They are PSMT4 read back from the PSMCT32 rectangle +0x32 names,
exactly as a `.pck` atlas. Unswizzled that way, 22 of the 24 lose half their pixel-to-pixel roughness and all 24 draw
clean (sofa, panelling, books, marble, rug, wallpaper); drawn raw they were noise.

### Levels: where the game looks

- **A mission's player character is read from its level archive.** For all 32 `levelflow.csv` rows the player's `.cr`
  is in that mission's archive (and in SWAT.DAT).
- **A level's load show is its own `.lsh`** (`lvl_perfnew.lsh` in PERFNEW.DAT names `mrd_load_a`, `mrd_load_hls`,
  `ptl_mrd_load_*`), and the clips it names are in the same archive. 552 `.anim` names exist in more than one archive.
- **Showdown maps** load only from `lvls_m2/<map>2.dat` and `lvls_m4/<map>4.dat`, and only with a row
  `<LOOKUPDATA:lvl_<map>, levelselect_<thumb>, <name>, <sub text>>` in `postermultiview.swui` (swat.dat and
  front.dat). Measured: two new maps load and play.
- **No archive has folders**: 66 of 66 hold only top-level names. A file added as `gbw/gbw.cr` is never found.

### BVH through Blender

Blender's BVH importer converts Y-up to Z-up (forward -Z, up Y) and its exporter has no axis option, so a clip
exported again is in Z-up: points as Z.p and joint rotations as Z.R.Z^T, with Z mapping (x, y, z) to (x, -z, y)
(root offset 1.125 m written in the Z column). It also writes frames up to the scene end (250 by default) holding
the last pose, and with float32 arithmetic moves every sample by up to 0.00095 degrees / 3.5e-6 m. Turned back and
with a 0.002 dead band, an unedited round trip re-encodes byte-identical (red_cof_rev_die: 16,200 samples).

---

## 3f. Field naming: 18,395 keys, measured

`05_Docs/RESEARCH/FIELDS.md` is the catalogue, generated by
`probe_fields.py` — 109 text extensions, **18,395 distinct keys**, each with
its kind, how many distinct assets carry it, and its range measured over every
file of that type on the disc.

What it deliberately does not do is guess. *"`TurnRate` is how fast the
character turns"* is inference; *"`TurnRate` is a float present in all 181
characters, measured 0.5 .. 12.0"* is a measurement, and it is the measurement
that lets someone edit a file with their eyes open. `.cr`'s
`HealthData/MaxHitPoints` reads **1 .. 1500** across 181 characters — that is
the number a modder actually needs, and nothing about it is a guess.

Kinds across the catalogue: float 8,411 · **ref 4,213** · bool 2,343 · int 757 ·
vec2/3/4 1,239 · enum 537 · text 90, plus some wide vectors (`.ptx` writes
10-value keyframe curves).

### The one inference that is still mechanical: `ref`

**4,213 keys name a real file on the disc.** A string key is marked `ref` when
at least half its values match the base name of an entry in an archive, and the
catalogue records which extensions those entries have. That converts opaque
text into a cross-format dependency graph without anyone guessing:

| key | resolves to |
|---|---|
| `.cr` `FileName/FileNameRender` | `.skel`, `.msd`, `.cr`, `.rep` — 100% |
| `.cr` `FileName/FileNameAnim` | `.skel`, `.rep`, `.sphs`, `.hot` — 100% |
| `.ptx` `Particle/TextureName` | `.tex` — 100% |
| `.swptx` `swptx/TextureName` | `.tex` — **95.3% per entry, 92.4% per distinct file** (`probe_fx.py`, 2026-09-14; the earlier "96%" here was the entry-level figure) |
| `.rep` `_arm_max_for_inj` | `.anim`, `.animsnd`, `.gait` — 100% |

A minimum value length of 3 is required, because without it `type: a` matches
`a.tex` and every `type` key on the disc is reported as an asset reference.

### Not every format has keys

`.lvl` is a manifest — named blocks each listing asset filenames, with no
`key value` line anywhere — and `.accu` is a plain CSV table. Both vanished
from the first catalogue, which only counted keys; they are kept now with a
shape of LIST/TABLE and a sample row.

### Two normalisations, and a defect in my own parse

A block head is `Type instance {`, so only the TYPE belongs in a path, and a
block named by a bare instance label (`VCam0001 {`) collapses to `*` unless
that name appears in at least 20% of the format's files. Without this `.cam`
alone reports 27,125 keys, one per camera. Consecutive identical segments
collapse too — `.skel` nests `bone` 21 deep, and `bone/bone/bone/offset` is the
same field as `bone/offset`.

The defect was mine and it is the same Allman-brace case `props.py` handles
with `pending_name`: a bare word on its own line followed by `{` on the NEXT
line is a block NAME, not a key. Missing it invented **432 phantom keys in
`.swptxemitter` alone** — one per emitter, each with no value — and pushed the
real fields under an anonymous parent. Fixing it dropped the catalogue from
22,310 keys to 18,395 and removed the `empty` kind entirely. `.swptxemitter`
went from a spurious "DATA (keys are content)" with 568 keys to what it is: a
25-key schema.

### The glosses, and their measured ceiling

`GLOSSARY.md` is the separate inference pass, marked as such on every page.
`FIELDS.md` measures; this one **reads names**, and a name can lie.

Kept checkable three ways. The English comes from the game rather than from me
— 3,669 words displayed in the `.xls` `string` column and the 101,778
`.strtbl` records, the 248-name AI vocabulary, and 102 tokens shared across 8+
unrelated formats. Abbreviations carry their evidence: 24 of 40 are backed by
the long form occurring elsewhere as a key token or by `guns.py`'s weapon list;
**16 are pure inference and are labelled so**. And the glosser is scored
against 163 field labels rdr-fmt wrote by hand, held out as ground truth.

**9,184 keys are IDENTIFIERS, not descriptions** — `.rep`'s 3,986 animation
clip names, `.flags`'s 1,195 game-state flags, `.cam`'s camera names. No gloss
will ever make `_bod_rev_die` mean something in prose. Setting them aside
leaves **9,211 descriptive keys: 52.7% HIGH, 5.5% MEDIUM, 41.8% OPAQUE**.
Without that split the headline reads 60% opaque and is measuring the wrong
thing.

**The result that matters is the ceiling.** Of the 163 keys with a hand-written
label, the name-derived reading misses the human's meaning on **64 — 39%** —
and not one of those is the glosser misreading the name:

| key | reads as | the human knew |
|---|---|---|
| `Impulse` | impulse | physical knockback force |
| `FullLoad` | full load | clip size |
| `Spread` | spread | cone of fire (0 = perfect) |
| `Cullable` | cullable | despawns when far away |
| `PopFreq` | pop freq | chance it appears |

So splitting a field name gets you something usable about 61% of the time and
misses real meaning the rest. **That is the bound on what any mechanical
glosser can do here**, and it is why these must not ship as UI labels without a
human reading them. The 9,048 descriptive keys with no human label to check
against should be assumed to fail at the same rate.

The genuinely useful output is therefore the **OPAQUE list** — the 3,849
descriptive keys that cannot be read from their name at all — and the tokens
that block them: `stn`, `hrs`, `wlk`, `snk`, `trw`, `dwn`, `crp`, `drw`, `dck`,
`inj`, `hld`.

### Expanding those animation codes: mostly did not work

`ANIM-ABBREV.md`, from `probe_animabbrev.py`. **The direct test failed first:**
checked against every `.anim` and `.gait` name on the disc, **not one of these
abbreviations is ever spelled out in full**. There is no Rosetta stone in the
corpus, so everything is inference.

**Measured on the 24 abbreviations that already have independent evidence, the
generator puts the right answer first 6 times.** Nine of those 24 target words
never appear in the game's text at all — it never says "velocity", "rotation"
or "transition" — so no vocabulary drawn from the game can reach them.

Two earlier scores were false and both are recorded in the probe. The first was
**24 of 24** against a word pool I had typed out myself containing exactly the
answers — the circularity this lane exists to avoid, walked into anyway. The
second was 9 of 24, inflated by letting `walk`, `walking` and `walkin` count as
three chances at one answer.

So 68 tokens come out tiered rather than answered:

| tier | n | what it rests on |
|---|---:|---|
| **CONFIRMED** | 7 | `guns.py`'s weapon list — `ptl` pistol, `rfl` rifle, `knf` knife, `dyn` dynamite, `sho` shotgun, `gat` gatling, `bow` |
| **CHARACTER** | 7 | slot 0, and the token is the stem of a real `.cr`/`.swt` file |
| **SUPPORTED** | 7 | one candidate, fitting the slot — `wlk` walk, `lft` left, `upp` upper, `drw` draw, `jmp` jump, `rvl` revolver |
| **AMBIGUOUS** | 44 | several readings, none chosen — `stn` stand/stain/strong, `snk` sneak/snake, `pst` past/post/pistol |
| **UNRESOLVED** | 3 | no candidate at all — `inj`, `dr2`, `mlg` |

Only the first two tiers are evidence; the other 58 are a worklist. The slot
anchors are the useful part for whoever picks it up: slot 2 holds `run` as a
whole word next to `wlk` and `lft`, slot 1 holds the guns.py-confirmed weapon
codes, so the category of each slot is fixed even where the token is not.

One error worth keeping: an earlier rule matched only consonant skeletons and
so answered **`dul` = "dull"** — confidently, in a game built around duels —
because `duel`'s skeleton is `dl` and the abbreviation keeps its vowel. The
method produces confident-looking mistakes, which is the argument for the
tiers.

### What waits on REQ-017

**546 of the 18,395 keys — 3.0% — carry a path the tool does not currently
produce.** They are the nested keys in the 17 extensions `props.py` mis-nests
(C-033): `.guns` 255, `.duel` 185, `.enm` 25, `.nav` 11, `.pmtl` 11 and twelve
more. A key at top level in those formats is still addressed correctly, which
is why the count is 546 and not all 621. **The other 17,849 keys are usable
today.** FIELDS.md carries the per-extension breakdown.

---

## 4. Ranked by moddability payoff

Weighted by what it unlocks × how tractable it is now.

**1 — Fix `props.py`'s block grammar. (highest, and it is a bug fix.)**
Unblocks correct block paths for 11,555 files across 18 extensions, closes a
live corruption route, and every text format in the tool inherits the fix at
once. Tractable: two regexes and the walk around them; the probe that measures
it already exists. This outranks any new decoding work because the tool is
currently able to damage files it offers to edit.

**2 — A generic counted-record editor.** 25 extensions honour a record count
with zero exceptions, and the two counting rules are known per extension. This
is M11 ("add enemies to `.enm`") generalised — add a block, bump the header —
and it lands `.hot` hotspots, `.sphs` collision volumes, `.bxes`, `.glows`,
`.lgt` and 20 more on the same code path. Tractable: the grammar is proven.

**3 — Field naming. DONE, see §3f.** `FIELDS.md` catalogues all 18,395 keys
with measured kinds and ranges, and 4,213 of them resolve to a named asset on
disc. 546 keys (3.0%) wait on REQ-017; the rest are usable now. What is left
here is English glosses, which is guesswork and is deliberately not in the
catalogue.

**4 — `.pup` cutscene casting.** Structurally decoded above, 1,200 files.
Needs a writer and role-code semantics for the high codes. Moderate payoff,
high tractability.

**5 — The remaining true unknowns. Done, see §3d.** `.hf`, `.xls` and
`.fonttex` decoded over their whole corpora; `.iani` and `.pck` narrowed to
their containers and honestly left PARTIAL. What is left as UNKNOWN is nine
extensions totalling 294 files, and 272 of those are `.txt` strays.

**`.actb`/`.stateb` (20,494 files) — started, see §3b.** The biggest unread
thing on the disc by file count, and where AI behaviour lives. Five opcodes are
now named, covering 86,000 of the operand references, which makes these files
*readable*; the remaining work is the record layout, which is what authoring
would need. It stays below items 1-3 on payoff-per-week — those are proven and
tractable today — but it is no longer untouched.

---

## 5. The table

Generated by `probe_unknowns.py --table`. **props coverage** = fields
`props.parse` returns ÷ content lines in the file, over every file of that
type. **edit-safe** = passed `probe_textedit.py` *and* carries no field whose
value swallowed a brace.| ext | files | size range | form | status | read by | props coverage | edit-safe |
|---|---:|---|---|---|---|---:|---|
| `.actb` | 13403 | 6-15129 B | MIXED | **PARTIAL** | actb.py | -- | -- |
| `.swptx` | 12944 | 874-2221 B | TEXT | **DECODED** | props.py (generic) + `probe_fx.py` (the `Key= value` tail) | 97.8% | yes |
| `.tex` | 12932 | 110-917518 B | MIXED | **DECODED** | texture.py | -- | -- |
| `.anim` | 11138 | 42-75767 B | BINARY | **DECODED** | anim.py (cm2) / animcmp.py (cmp) / `probe_animraw.py` (uncompressed) | -- | -- |
| `.mod` | 8600 | 70-445370 B | MIXED | **PARTIAL** | rsc.py / vif.py + `probe_modtext.py` (1.10) + `probe_modbin.py` (2.1x header+verts) | -- | header and vertex block settled from the ELF; material/packet tail open |
| `.stm` | 8361 | 3584-26126336 B | BINARY | **PARTIAL** | stm.py / streams.py / dataeditor_more.stm_make -- loop rules in 3g | -- | new streams |
| `.stateb` | 7092 | 36-40329 B | BINARY | **PARTIAL** | actb.py / behaviour.py | -- | -- |
| `.animsnd` | 5797 | 27-2397 B | TEXT | **DECODED** | props.py (generic) + `probe_animsnd.py` | 79.8% | yes |
| `.gait` | 5159 | 208-111582 B | TEXT | **DECODED** | props.py (9/14 keys) + `probe_gait.py` (all 14) | 11.9% | yes (0 trap fields; 21160 block opens unseen) |
| `.swptxemitter` | 4436 | 219-1691 B | TEXT | **DECODED** | props.py (generic) + `probe_fx.py` | 88.1% | yes |
| `.arfx` | 3538 | 0-359 B | TEXT | **DECODED** | props.py (generic) + `probe_fx.py` | 80.0% | yes |
| `.tbl` | 3275 | 196-870 B | TEXT | **DECODED** | behaviour.py / sheets.py | 99.9% | yes |
| `.csv` | 2541 | 2-48429 B | MIXED | **DECODED** | csvfile.py | -- | -- |
| `.fx` | 2286 | 9-4804 B | TEXT | **DECODED** | props.py (generic) + `probe_fx.py` | 87.6% | yes |
| `.znes` | 1445 | 199-33518 B | TEXT | **DECODED** | scene.py + `probe_scene.py` | 34.5% | yes (0 trap fields; 24802 block opens unseen) |
| `.rnt` | 1226 | 24-29227 B | BINARY | **DECODED** | rsc.py `parse_rnt` + `probe_rnt.py` (fixed layout) | -- | -- |
| `.pup` | 1200 | 24-739 B | BINARY | **DECODED** | research/probe_pup.py (no module yet) | -- | -- |
| `.rsc` | 1139 | 64-4235840 B | MIXED | **PARTIAL** | rsc.py + `probe_rsc.py` (container settled) | -- | -- |
| `.skel` | 1128 | 163-57076 B | TEXT | **DECODED** | props.py + `probe_skel.py` (recursive) | -- | yes (0 trap fields; 33557 block opens unseen) |
| `.swui` | 1111 | 0-19961 B | MIXED | **DECODED** | swui.py (complete: 4792/4792 tags) + `probe_swui.py` | -- | yes -- `apply()` splices value spans only |
| `.film` | 871 | 186-1027 B | TEXT | **DECODED** | props.py (generic) + `probe_filmcrk.py` | 93.6% | yes |
| `.bxes` | 859 | 242-105628 B | TEXT | **DECODED** | props.py (generic) + `probe_scene.py` | 40.9% | yes (0 trap fields; 48244 block opens unseen) |
| `.acttbl` | 832 | 62-6244 B | TEXT | **DECODED** | props.py (generic) + `probe_acttbl.py` | 92.8% | yes |
| `.bnds` | 709 | 189-30324 B | TEXT | **DECODED** | scene.py + `probe_scene.py` | 44.9% | yes (0 trap fields; 8783 block opens unseen) |
| `.lnes` | 659 | 262-167353 B | TEXT | **DECODED** | props.py (generic) + `probe_scene.py` | 2.7% | yes (0 trap fields; 42684 block opens unseen) |
| `.rep` | 585 | 30857-80981 B | TEXT | **DECODED** | props.py (generic) | 100.0% | yes |
| `.ptx` | 566 | 211-2099 B | TEXT | **DECODED** | props.py (generic) + `probe_filmcrk.py` | 96.4% | yes |
| `.dam` | 550 | 324-590 B | TEXT | **DECODED** | props.py (generic) | 99.7% | yes |
| `.sphs` | 532 | 544-583 B | TEXT | **DECODED** | props.py (generic) | 100.0% | **NO -- 34 trap fields** |
| `.hot` | 523 | 872-4555 B | TEXT | **DECODED** | props.py (generic) + `probe_tail.py` | 54.8% | yes (0 trap fields; 8674 block opens unseen) |
| `.msd` | 521 | 5199-169886 B | TEXT | **DECODED** | msd.py + props.py + `probe_tail.py` | 89.9% | yes |
| `.swt` | 519 | 19473-20425 B | TEXT | **DECODED** | swt.py + props.py | 99.8% | yes |
| `.cr` | 517 | 6714-7312 B | TEXT | **DECODED** | charfile.py | 90.4% | yes |
| `.crk` | 474 | 164-744 B | TEXT | **DECODED** | props.py (generic) + `probe_filmcrk.py` | 90.5% | yes |
| `.lgt` | 440 | 480-695 B | TEXT | **DECODED** | props.py (generic) | 100.0% | yes |
| `.xznes` | 418 | 199-18308 B | TEXT | **DECODED** | props.py (generic) + `probe_scene.py` | 31.7% | yes (0 trap fields; 5670 block opens unseen) |
| `.em` | 401 | 206-192678 B | TEXT | **DECODED** | props.py (generic) | 100.0% | yes |
| `.reigns` | 389 | 574-828 B | TEXT | **DECODED** | props.py (generic) + `probe_fxcr.py` | 97.2% | yes |
| `.enm` | 378 | 24-25726 B | TEXT | **PARTIAL** | enemyfields.py | 94.1% | **NO -- 1 trap fields** |
| `.iani` | 358 | 16-15859440 B | BINARY | **PARTIAL** | research/probe_iani -- header only | -- | -- |
| `.fxcr` | 350 | 164-906 B | TEXT | **DECODED** | props.py (generic) + `probe_fxcr.py` | 55.9% | yes |
| `.cm` | 331 | 0-22536 B | TEXT | **PARTIAL** | props.py (generic) | 94.3% | yes |
| `.cam` | 330 | 23-92827 B | TEXT | **PARTIAL** | props.py (generic) | 90.3% | yes |
| `.hd` | 310 | 288-11696 B | BINARY | **PARTIAL** | vag.py | -- | -- |
| `.nav` | 289 | 24-129561 B | TEXT | **DECODED** | props.py (generic) + `probe_nav.py` (both dialects) | 58.4% | **NO -- 75 trap fields in `Tracks`/`Links`, 83 files drift** |
| `.txt` | 272 | 0-207228 B | MIXED | **DECODED** | 269/272 UTF-16LE subtitle scripts; 3 plain-ASCII dev files | `probe_leftovers.py` | `05_Docs/RESEARCH/LEFTOVERS.md` |
| `.bnd` | 260 | 0-132876 B | TEXT | **DECODED** | props.py (generic) | 100.0% | yes |
| `.ipu` | 251 | 9784-156546 B | BINARY | **PARTIAL** | ipudec.py / ipuenc.py / mpeg2intra.py | -- | -- |
| `.ars` | 245 | 25-364 B | TEXT | **DECODED** | props.py (generic) | 100.0% | yes |
| `.fonttex` | 243 | 144-4598 B | BINARY | **DECODED** | research/probe_fonttex.py (no module yet) | -- | -- |
| `.strtbl` | 203 | 2006-633169 B | BINARY | **DECODED** | strtbl.py | -- | -- |
| `.duel` | 203 | 14-76790 B | TEXT | **DECODED** | props.py (generic) + `probe_tail.py` | 99.4% | yes (0 trap fields; 1273 block opens unseen) |
| `.flags` | 203 | 27-6036 B | TEXT | **DECODED** | props.py (generic) | 100.0% | yes |
| `.aisp` | 192 | 149-15651 B | TEXT | **PARTIAL** | levelmeta.py / props.py | 70.8% | yes |
| `.hud` | 192 | 73-34197 B | TEXT | **PARTIAL** | props.py (generic) | 93.8% | yes |
| `.swptxset` | 186 | 41-113 B | TEXT | **PARTIAL** | props.py (generic) | 40.0% | yes |
| `.glows` | 156 | 413-4200 B | TEXT | **PARTIAL** | props.py (generic) | 95.1% | yes |
| `.bd` | 155 | 17696-1393472 B | BINARY | **PARTIAL** | vag.py | -- | -- |
| `.fog` | 145 | 223-962 B | TEXT | **PARTIAL** | props.py (generic) | 91.0% | yes |
| `.fade` | 141 | 90-97 B | TEXT | **PARTIAL** | props.py (generic) | 80.0% | yes |
| `.lvl` | 125 | 174-3517 B | TEXT | **PARTIAL** | level.py | 83.7% | -- |
| `.glassfx` | 124 | 403-404 B | TEXT | **PARTIAL** | props.py (generic) | 93.8% | yes |
| `.accu` | 117 | 301-681 B | TEXT | **PARTIAL** | props.py (generic) | 0.0% | -- |
| `.xrsc` | 114 | 256-3688832 B | BINARY | **PARTIAL** | texture.py | -- | -- |
| `.ccm` | 106 | 884-57155 B | TEXT | **PARTIAL** | props.py (generic) | 98.4% | yes |
| `.tail` | 102 | 505-1321 B | TEXT | **PARTIAL** | props.py (generic) | 87.9% | yes |
| `.sneak` | 97 | 28-303 B | TEXT | **PARTIAL** | props.py (generic) | 76.1% | yes |
| `.spwn` | 97 | 127-1565 B | TEXT | **DECODED** | props.py (generic) | 100.0% | yes |
| `.lsh` | 94 | 249-3319 B | TEXT | **PARTIAL** | props.py (generic) | 98.9% | yes |
| `.pow` | 92 | 6-2555 B | TEXT | **PARTIAL** | props.py (generic) | 71.4% | yes |
| `.pck` | 82 | 11680-1828928 B | BINARY | **PARTIAL** (AVM1 only) | pck.py / pckscene.py / pckfont.py -- objects decoded, drawn, edited, can grow (see `.pck`) | -- | yes |
| `.shadows` | 81 | 54-990 B | TEXT | **PARTIAL** | props.py (generic) | 82.1% | yes |
| `.acc` | 77 | 95-145 B | TEXT | **PARTIAL** | props.py (generic) | 83.4% | yes |
| `.hf` | 67 | 1788-262168 B | BINARY | **DECODED** | research/probe_hf.py (no module yet) | -- | -- |
| `.pmp` | 66 | 702-3119 B | TEXT | **PARTIAL** | props.py (generic) | 98.5% | yes |
| `.plyr` | 64 | 2528-6413 B | TEXT | **PARTIAL** | props.py (generic) | 91.2% | yes |
| `.amp` | 64 | 341-2801 B | TEXT | **PARTIAL** | props.py (generic) | 53.6% | **NO -- 239 trap fields** |
| `.pmtl` | 63 | 10295-10295 B | TEXT | **DECODED** | props.py (generic) + `probe_tail.py` | 100.0% | yes (0 trap fields; 3654 block opens unseen) |
| `.chase` | 63 | 84-84 B | TEXT | **DECODED** | props.py (generic) + `probe_skel.py` | -- | **NO -- 63 trap fields in `SideOffsets`** |
| `.dof` | 63 | 429-1040 B | TEXT | **PARTIAL** | props.py (generic) | 72.7% | yes |
| `.guns` | 63 | 81330-148463 B | TEXT | **PARTIAL** | guns.py | 98.4% | **NO -- 63 trap fields** |
| `.breakables` | 62 | 336-336 B | TEXT | **PARTIAL** | props.py (generic) | 80.0% | yes |
| `.colors` | 62 | 4209-4209 B | TEXT | **PARTIAL** | props.py (generic) | 87.7% | yes |
| `.doors` | 62 | 74-74 B | TEXT | **PARTIAL** | props.py (generic) | 75.0% | yes |
| `.enemies` | 62 | 20-20 B | TEXT | **PARTIAL** | props.py (generic) | 50.0% | -- |
| `.ntr` | 62 | 583-583 B | TEXT | **PARTIAL** | props.py (generic) | 94.7% | yes |
| `.vsnd` | 62 | 1617-1617 B | TEXT | **DECODED** | props.py (generic) | 100.0% | yes |
| `.cann` | 57 | 104-617 B | TEXT | **PARTIAL** | props.py (generic) | 96.4% | yes |
| `.hb` | 56 | 62-3510 B | TEXT | **DECODED** | props.py (generic) | 100.0% | yes |
| `.veh` | 56 | 100-814 B | TEXT | **PARTIAL** | props.py (generic) | 59.5% | yes |
| `.wheels` | 45 | 74-176 B | TEXT | **DECODED** | props.py (generic) | 100.0% | yes |
| `.grasp` | 44 | 384-413 B | TEXT | **PARTIAL** | props.py (generic) | 88.2% | yes |
| `.xls` | 38 | 16896-220672 B | BINARY | **DECODED** | research/probe_xls.py (no module yet) | -- | -- |
| `.dst` | 32 | 116-204 B | TEXT | **PARTIAL** | props.py (generic) | 83.9% | yes |
| `.plat` | 24 | 44-1466 B | TEXT | **PARTIAL** | props.py (generic) | 96.5% | yes |
| `.inv` | 24 | 13-1557 B | TEXT | **PARTIAL** | props.py (generic) | 82.3% | yes |
| `.mmb` | 22 | 65-942 B | TEXT | **PARTIAL** | props.py (generic) | 76.8% | yes |
| `.audbox` | 19 | 158-169 B | TEXT | **PARTIAL** | props.py (generic) | 90.0% | yes |
| `.phys` | 18 | 156-191 B | TEXT | **DECODED** | props.py (generic) | 100.0% | yes |
| `.ibox` | 18 | 116-298 B | TEXT | **PARTIAL** | props.py (generic) | 90.1% | yes |
| `.til` | 17 | 51-1292 B | TEXT | **PARTIAL** | props.py (generic) | 14.7% | yes |
| `.sky` | 16 | 72-92 B | TEXT | **PARTIAL** | props.py (generic) | 79.7% | yes |
| `.armr` | 15 | 181-370 B | TEXT | **PARTIAL** | props.py (generic) | 89.3% | yes |
| `.sub` | 13 | 580-4649 B | TEXT | **PARTIAL** | props.py (generic) | 89.4% | yes |
| `.sm` | 13 | 740-1113 B | TEXT | **PARTIAL** | props.py (generic) | 47.2% | yes |
| `.flares` | 12 | 509-6879 B | TEXT | **PARTIAL** | props.py (generic) | 85.8% | yes |
| `.pist` | 10 | 15-895 B | TEXT | **DECODED** | props.py (generic) | 100.0% | yes |
| `.water` | 9 | 175-228 B | TEXT | **PARTIAL** | props.py (generic) | 88.2% | yes |
| `.outl` | 8 | 340-438 B | TEXT | **PARTIAL** | props.py (generic) | 94.4% | yes |
| `.swuibook` | 7 | 870-1311 B | TEXT | **PARTIAL** | props.py (generic) | 97.2% | yes |
| `.grass` | 7 | 391-538 B | TEXT | **PARTIAL** | props.py (generic) | 95.5% | yes |
| `.rain` | 6 | 3896-32424 B | MIXED | **DECODED** | `probe_unknown7.py` | -- | 3/4 are `RainStorm` configs; 1 is a misfiled `.hf` |
| `.gls` | 6 | 23-23 B | TEXT | **DECODED** | props.py (generic) | 100.0% | yes |
| `.trn` | 6 | 150-991 B | TEXT | **PARTIAL** | props.py (generic) | 47.7% | yes |
| `.diff` | 6 | 96-604 B | TEXT | **PARTIAL** | props.py (generic) | 91.7% | yes |
| `.swuipop` | 5 | 276-293 B | TEXT | **PARTIAL** | props.py (generic) | 90.0% | yes |
| `.paths` | 5 | 1254-2171 B | TEXT | **PARTIAL** | props.py (generic) | 21.2% | yes |
| `.znv` | 5 | 72-1824 B | TEXT | **PARTIAL** | props.py (generic) | 97.3% | yes |
| `.chix` | 5 | 812-1009 B | TEXT | **PARTIAL** | props.py (generic) | 97.4% | yes |
| `.ico` | 4 | 23844-89688 B | BINARY | **DECODED** | `probe_unknown7.py` | -- | PS2 memory-card icon geometry |
| `.rec` | 4 | 14336-33296 B | BINARY | **PARTIAL** | `probe_unknown7.py` | -- | 30 fps recording, 32-byte record; fields unnamed |
| `.ebox` | 3 | 338-352 B | TEXT | **PARTIAL** | props.py (generic) | 92.3% | yes |
| `.psi` | 3 | 37012-37012 B | BINARY | **DECODED** | `probe_unknown7.py` | -- | the same PS2 icon format as `.ico` |
| `.scorp` | 2 | 225-225 B | TEXT | **PARTIAL** | props.py (generic) | 90.9% | yes |
| `.sys` | 2 | 964-964 B | BINARY | **DECODED** | `probe_unknown7.py` | -- | `PS2D` memory-card save descriptor |
| `.swuipost` | 2 | 114-114 B | TEXT | **PARTIAL** | props.py (generic) | 75.0% | yes |
| `.roommach` | 2 | 1875-1875 B | TEXT | **PARTIAL** | props.py (generic) | 78.2% | yes |
| `.lod` | 2 | 2561-2561 B | TEXT | **DECODED** | props.py (generic) | 100.0% | yes |
| `.movie` | 2 | 11-29 B | TEXT | **PARTIAL** | props.py (generic) | 66.7% | yes |
| `.wind` | 2 | 319-327 B | TEXT | **PARTIAL** | props.py (generic) | 83.3% | yes |
| `.snow` | 2 | 557-580 B | TEXT | **PARTIAL** | props.py (generic) | 96.2% | yes |
| `.clock` | 2 | 187-188 B | TEXT | **PARTIAL** | props.py (generic) | 87.5% | yes |
| `.bin` | 1 | 27704-27704 B | BINARY | **UNKNOWN** | -- | -- | -- |
| `.natr` | 1 | 576-576 B | TEXT | **PARTIAL** | props.py (generic) | 82.1% | **NO -- 5 trap fields** |
| `.modb` | 1 | 10556-10556 B | BINARY | **DECODED** | rsc.py / vif.py + `probe_unknown7.py` | -- | a `.mod`: identical `version: 2.12` header |
| `.arsbak` | 1 | 174-174 B | TEXT | **DECODED** | props.py (generic) | 100.0% | yes |
| `.ecs` | 1 | 457-457 B | TEXT | **DECODED** | props.py (generic) | 100.0% | yes |
| `.swaudio` | 1 | 194-194 B | TEXT | **PARTIAL** | props.py (generic) | 87.5% | yes |
| `.txt before russ broke stuff` | 1 | 493484-493484 B | BINARY | **DECODED** | UTF-16LE TSV export of the master string table; same 9 columns as the `.xls` sheets | `probe_leftovers.py` | `05_Docs/RESEARCH/LEFTOVERS.md` |

### Loose files on the disc (outside the archives)

| path | count | size range | status | notes |
|---|---:|---|---|---|
| `*.DAT` | 66 | 1.6 MB – 1.27 GB | **DECODED** | `dave.py`. `Dave` packed-name and `DAVE` ASCII-name variants. |
| `*.PSS` | 22 | 1.1 – 26.8 MB | **PARTIAL** | `pss.py` + `ipudec.py`. M19. |
| `*.IRX` | 12 | 6.6 – 96 KB | **PARTIAL** | PS2 IOP (R3000A) modules: ELF with `.text` at vaddr 0, file offset 0xA0. `AGESND.IRX` disassembled far enough to settle the `.stm` loop descriptor (see PROGRESS-rdr-re). Not game data. |
| `SLUS_205.00` | 1 | 1.83 MB | **PARTIAL** | `elfpack.py`; unpacked copy in `07_Research/`. |
| `IOPRP.IMG` | 1 | 270 KB | **UNKNOWN** | IOP boot image. |
| `SYSTEM.CNF` | 1 | 57 B | **DECODED** | `BOOT2 = ...`, plain text. |

## Open questions

Closed, so nobody reopens them by accident:

- ~~**`.actb` / `.stateb` action opcodes.**~~ **CLOSED, and not reopening.**
  Five named from the corpus (C-036); the rest are **not on this disc** — §1.7
  of `MISSION_LOGIC.md` shows the names were resolved by the offline authoring
  toolchain, so a disassembly does not recover them either. REQ-021 asked
  whether to spend the 296 handler-naming jobs it would really take, and the
  answer was **no** (the reviewer, 2026-09-14). `.actb`/`.stateb` stay **PARTIAL** as
  their end state unless the developer says otherwise.

Still genuinely open:

- **`.iani` per-channel meaning.** The container is decoded (§3d) and the
  header's `actors` field is confirmed against `.pup` at 100%. What is unknown
  is which of the C floats per frame is which channel, because C is a total
  across the actors and comes from their rigs rather than the file.
- **`.pck` object layout.** The container is decoded (base address,
  self-pointers, 82/82) but what the objects ARE is unknown, and the vtable
  hypothesis did not discriminate. Likely the same shape of wall as `.actb`.
- **High `.pup` role codes.** Inferred as creature ids from the naming; untested.
- **The 10 `Num*` mismatches.** Is the game reading the header or the body?
  Settling it needs a boot test, not a probe.
- **The 44 ambiguous animation abbreviations.** Filed as REQ-032; needs a human
  who knows the game, not another probe.

## `.gait` — DECODED: the locomotion parameter file

*rdr-re, 2026-09-14. `03_Tools/research/probe_gait.py`, read-only, no lock.*

**5,159 files, 3,906 distinct names, 5,159 parsed, 0 failed.** Every declared
count is honoured by what follows it. `.gait` is one gait — one character
moving one way — and there is one per character per direction per speed.

### Layout

Plain ASCII in the Angel Studios `key value` grammar. 14 keys, and all 14 are
present on all 5,159 files:

    Axis          0 or 1  -- which horizontal column carries the travel
    IdealSpeed    float
    StrideLength  float   -- signed
    MaxPeriod     float
    VelScale      float
    SlewVelScale  float
    AngVelScale   float
    DistComfort2  float
    AngComfort    float
    NumSteps      N
    StepData {                      -- exactly N of these
      Stance:          x y z
      Trigger:         float
      DutyFactor:      float
      NumFrames:      F { F rows x 6 floats }
      NumStandFrames: S { S rows x 2 floats }
    }

`NumFrames: 20 {` both declares a count **and** opens a block. The count is the
number of ROWS, not the number of numbers — the probe asserts the row count and
the row width separately, because conflating them is what produced 34,942 false
`Num*` mismatches in `probe_textgrammar.py`.

### What the six columns are

Bounded-ness settles it, over all 10,580 frame blocks:

| column | within ±π | within ±π/2 | max abs | reading |
|---|---:|---:|---:|---|
| col0 | 10022/10580 | 9484/10580 | 78.09 | position X |
| col1 | 10540/10580 | 10373/10580 | 45.67 | position Y (vertical) |
| col2 | 9828/10580 | 9617/10580 | 1040.56 | position Z |
| col3 | **10580/10580** | 8945/10580 | 3.1414 | angle (radians) |
| col4 | **10580/10580** | 9415/10580 | 3.1416 | angle (radians) |
| col5 | **10580/10580** | **10580/10580** | 1.5663 | angle, bounded to ±π/2 |

Columns 3–5 are inside ±π on **every one of 10,580 blocks** and touch 3.1414
and 3.1416, i.e. they reach the bound — so it is a real wrap, not a coincidence
of small numbers. Columns 0–2 are unbounded and reach 78, 45 and 1040. col5 is
additionally inside ±π/2 on every block and reaches 1.5663 against a bound of
1.5708, which is the usual pitch convention. So each row is **3 positions then
3 Euler angles**, and I saw the wrap happen in `ani_dck_for_wlk.gait`: col3
steps −2.84850 → +3.05749 between adjacent frames.

### `Axis` selects which column carries the travel — 98.2%, against controls

    Axis-as-selector      6240 / 6357  =  98.2%
    CONTROL best constant                 62.3%     ("always col2")
    CONTROL shuffled Axis                 52.8%
    4223 step blocks excluded: no translation on either axis

`Axis` 0 picks col0, `Axis` 1 picks col2. The controls are the point: 62.3% of
step blocks are col2-dominant anyway, so a constant answer scores 62.3% and a
shuffled `Axis` scores 52.8%. 98.2% is the claim clearing both.

The residual 1.8% is concentrated where "dominant translation axis" is not a
meaningful question: files whose names contain `spn`, `tur`, `die`, `inj` or
`cir` mismatch at 5.8% against 2.2% for the rest — 2.6× enriched. Those are
spins, turns and deaths, which rotate rather than travel.

**Travel on the selected column is normalised**, median 0.992 over 6,357 step
blocks. That is why the obvious first guess fails: the matrix does not store
world distance, so `StrideLength` has to scale it.

### Two things I tested and am NOT claiming

- **`sign(travel) == sign(StrideLength)`: 3552 / 5962 = 59.6%.** Near chance.
  `StrideLength` and `IdealSpeed` are genuinely signed (`ncb_lft_run.gait` has
  StrideLength −3.8981), and on a two-file comparison the sign looked like it
  carried the direction. Across the corpus it does not. Rejected.
- **`NumStandFrames` is all zeros on the entire disc — 10,580 / 10,580 blocks.**
  The field is declared, counted and honoured everywhere, and never carries a
  value. Either it is reserved, or whatever wrote it never had data to put
  there. I am not guessing which.

### Correction to this file's own earlier row

The `.gait` row previously read **"NO -- 21160 trap fields"**. I first wrote
that off as a conflation of `opens_missed` with `trap_fields`. **That
explanation was wrong, and the real one is worth more.** Re-running the
generator settles it: `probe_unknowns.py` reads `trap_fields` correctly and now
emits `yes` for `.gait`. The row was **stale output** — generated before
rdr-fmt's fix in `02e8e0e`, when those fields genuinely *were* editable traps.
The fix reclassified them from "editable and corrupting" to "structurally
invisible", and the table was never regenerated. See the stale-table note at
the end of this file.

Either way the number now has an exact account: **10,580 `NumFrames` + 10,580
`NumStandFrames` = 21,160.** Every missed open is one of the two counted
blocks, which is precisely the construct `props.py`'s generic reader does not
model. Nothing is left over.

## `.nav` — DECODED: the AI navigation graph, in two generations

*rdr-re, 2026-09-14. `03_Tools/research/probe_nav.py`, read-only, no lock.*

**289 files, 289 parsed, 0 failed.** `.nav` was the biggest structural blind
spot on the disc — **71,769 of the 271,618 block opens `props.py` misses are in
`.nav` alone (26%)**, and it holds 75 of the 139 remaining trap fields and all
83 files that drift on round-trip. That is why it was next.

### Two dialects, both labelled `type: a`

The file starts `type: a` either way, so the header does not distinguish them.
The second key does.

**Dialect A — 278 files, what shipped.** Separate node and link tables:

    NumNodes  N
    NumLinks  L
    Node i { LocalPos x y z ; Links <ids...> ; FirstLinkRef r ; NumLinks n }
    Link j { Length f ; Nodes a b ; Flags n }
    Tracks T { Links c { ids... } ... }
    ForeLinks F { ids... }
    BackLinks B { ids... }
    LinkRefs { ids... }                      -- note: no count

**Dialect B — 11 files, the older format.** Links embedded per node, each
carrying its own distance, plus a `navtable`:

    NumOfNodes  N
    node i { pos x y z ; jump 0 ; oneway 0 ;
             links c { target dist  target dist ... } ;
             navtable c { ... } }

`NumLinks` means two different things in dialect A — the number of `Link`
blocks at top level, the degree of the node inside a `Node`. Same key, two
scopes. A flat reader cannot get that right, which is most of why this format
mis-parses.

### Every declared count holds

| invariant | holds | fails |
|---|---:|---:|
| `NumNodes` == number of `Node` blocks | 278 | 0 |
| `NumLinks` (top) == number of `Link` blocks | 278 | 0 |
| node degree == length of its `Links` list | 278 | 0 |
| `FirstLinkRef` == running prefix sum of degrees | 278 | 0 |
| `len(LinkRefs)` == total degree | 278 | 0 |
| `NumOfNodes` == number of `node` blocks (dialect B) | 11 | 0 |

`FirstLinkRef` is the one worth naming: node *i*'s value is the sum of every
earlier node's degree, so `LinkRefs` is one flat array that each node indexes
into by offset and length. It holds on all 278 files with no exceptions.

### The check that makes this a decode and not a shape

**`Link.Length` is the Euclidean distance between the `LocalPos` of the two
nodes the link names — 40,355 / 40,355.** Worst relative error 1.508e-05, on a
link 0.0358 long in `lvl_sherlo.nav`; the tolerance is 2e-5 because the values
are float32 and a short link amplifies the rounding. Nothing is near the bound
for any other reason.

That is topology and geometry corroborating each other from inside the same
file: the `Nodes a b` field is an index into a table the parser read separately,
and the number stored next to it is recomputable from that table's coordinates.
Dialect B does the same thing per node and is **symmetric on 2,441 / 2,441**
link pairs — if *i* lists *j* at distance *d*, *j* lists *i* at the same *d*.

### The stale-distance result, which names its own files

Dialect B's cached distances disagree with the positions on 80 links. The
disagreement is not spread — it is four files, and all four are named as
abandoned:

    file                             stale  links      worst  verdict
    lvl_fortold.nav                     72    360   9.38e-03  STALE
    lvl_perfect.nav                      4    422   2.53e-03  STALE
    old2_lvl_cover.nav                   2    392   1.03e-03  STALE
    old_lvl_cover.nav                    2    392   1.03e-03  STALE
    backup_lvl_perfnew.nav               0    716   1.75e-06  consistent
    lvl_train_gatlingwagon.nav           0     14   4.22e-07  consistent
    lvl_train_tile_trn11.nav             0     84   3.02e-07  consistent
    lvl_train_tile_trn20.nav             0     24   1.19e-07  consistent
    lvl_train_tile_trn21.nav             0     24   2.76e-07  consistent
    lvl_train_tile_trn22.nav             0     24   2.76e-07  consistent

`fortold` is "fort, old"; `lvl_perfect` was superseded by `lvl_perfnew`;
`old_`/`old2_` say it outright. Someone moved nodes and never recomputed the
cached lengths, then moved on. The six consistent files agree to 1.75e-06,
which is float32 noise. **A hypothesis I checked and dropped:** that the
mismatches were `jump` links storing a path cost rather than a straight line.
All 80 are `jump 0`, and every one of the 35 `jump` links is consistent.

This also connects two findings — `lvl_train_tile_trn20.nav` (dialect B) carries
the same coordinates and the same link length 55.485073 as the dialect-A
`lvl_train_tile_trn30.nav`. The same navigation data exists on the disc in both
generations of the format.

### The trap, in its own words

`Tracks` is where the 75 trap fields live. The construct is

    Tracks 2
    {
        Links 12  { 52  51  45  46  44  47  48  43  42  49  41  50  }
        Links 10  { 60  57  61  58  59  62  54  55  53  56  }
    }

`props.py` reads key `Links` with the value `12  { 52  51  ... }` — count,
braces and all, as one editable string. `propsgap.json` records what happens:
`braces_before 131, braces_after 130`. **Editing that field through the generic
path drops a brace.** 75 such fields across 49 files, and 83 `.nav` files drift
on round-trip. Those are the only remaining live corruption routes of their kind
on the disc.

## Note: the edit-safe and coverage columns above were stale

*rdr-re, 2026-09-14, found while taking the scene-geometry family.*

Seven rows in the table carried an `edit-safe` cell of the form
**"NO -- N trap fields"** with N in the tens of thousands. I twice wrote that
up as a labelling mistake — `opens_missed` reported as `trap_fields`. **It was
not.** `probe_unknowns.py:164` reads `trap_fields` from `propsgap.json`
correctly, and re-running it emits the right values today.

What actually happened: the table was generated **before rdr-fmt's fix in
`02e8e0e`**, when `props.py` really did offer every one of those fields for
editing. The fix took the disc-wide trap count from 270,750 to 139. Those
fields did not go away — they were reclassified from *editable and corrupting*
to *structurally invisible*, which is `opens_missed`. So the old numbers were
true when written and false afterwards, and nothing regenerated them.

    row said (stale)        current generator
    .bxes  NO -- 48244      yes        .nav   NO -- 71844   NO -- 75 trap fields
    .lnes  NO -- 42684      yes        .gait  NO -- 21160   yes
    .znes  NO -- 24802      yes
    .bnds  NO --  8783      yes
    .xznes NO --  5670      yes

The coverage column drifted the same way (`.gait` 14.9% -> 11.9%, `.lnes`
26.3% -> 2.7%). All seven rows are corrected above.

**Do not fix this by regenerating the whole table.** `probe_unknowns.py`
derives `status` from module coverage, so a wholesale regenerate emits
`PARTIAL` for every extension this lane has DECODED by probe and would erase
39 hand-set statuses. Refresh the `coverage` and `edit-safe` columns only, or
teach the generator to read the DECODED set from the sections below first.

## `.bxes` `.lnes` `.znes` `.xznes` `.bnds` — DECODED as one family

*rdr-re, 2026-09-14. `03_Tools/research/probe_scene.py`, read-only, no lock.*

Taken as one class rather than five, because they are one grammar and because
together they were **130,183 of the 178,689 block opens `props.py` still missed
(73%)** once `.nav` and `.gait` were accounted for. They are also exactly the
sections a `.lvl` manifest lists — `Bounds` / `Zones` / `Lines` / `Boxes` — so
this is the geometry a level is assembled from.

**2,947 distinct files, 2,947 parsed, 0 failed.** (The table above counts 4,090
archive entries; many are duplicated across archives, and this probe
de-duplicates by name.)

| ext | parsed | failed | `Num*` header matches |
|---|---:|---:|---:|
| `.bxes` | 556 | 0 | 556 |
| `.lnes` | 457 | 0 | 457 |
| `.znes` | 1019 | 0 | 1019 |
| `.xznes` | 418 | 0 | 418 |
| `.bnds` | 497 | 0 | 497 |
| **total** | **2947** | **0** | **2947** |

### The shared grammar

    Num<Kind>  N                     -- NumBoxes / NumLineNodes / NumInstNodes
    <Kind> <name> {                  -- `Box` or `node`, N of them
        matrix { 4 rows x 3 floats } -- uncounted block: 3x3 basis + translation
        attributes A { A entries }   -- counted block: `TYPE name value...`
        ...per-extension fields
    }

`matrix` is **4x3 on all 41,295 nodes** — a 3x3 basis followed by a translation
row. Per extension: `.bxes` adds `width`/`height`/`depth`; `.lnes` adds
`Vertices V { V rows x 3 }`; `.znes`/`.xznes` add `numLods` and
`resourceIndex`/`resourceIndexXbox`; `.bnds` adds the resource indices and
carries the collision flags in its attributes.

### Attribute arity is set by the type — the mistake worth recording

An attribute is `TYPE name value...`, and **the number of values depends on the
TYPE**. Assuming a flat three tokens per attribute failed on 30 files with
errors like *"attributes declared 5, found 17 tokens"*. The cause is `VECTOR3`:

    STRING   Effect_01_Name    lvl_barfight_tabledust
    VECTOR3  Effect_01_Offset  0.000000  -1.000000  0.000000
    FLOAT    offsetX_01        0.000000

Five types, and the probe raises on any type not in its table rather than
guessing, so the set is complete or it says so:

| type | values | count |
|---|---:|---:|
| `BOOL` | 1 | 43,228 |
| `STRING` | 1 | 3,085 |
| `INT` | 1 | 1,000 |
| `FLOAT` | 1 | 918 |
| `VECTOR3` | **3** | 249 |

### A `.bnds` node's NAME encodes its own collision flags — 452 / 452

`.bnds` carries twelve booleans per node:

    BoundFlagMover  BoundFlagCamera  BoundFlagTarget   BoundFlagShoot
    BoundFlagSneak  BoundFlagEnemy   BoundFlagHorse    BoundFlagPlayer
    BoundFlagDoor   BoundFlagCamClip BoundFlagWater    BoundFlagPlummet

and a node named `bound_bound_grp_fffffffffftf_01` has a twelve-character f/t
string in its name. **Character *i* is `t` exactly when flag *i* is true, on
452 of 452 nodes that carry such a name.** In that example the 11th character
is the only `t` and `BoundFlagWater` is the only true flag.

**Scope and control, because 100% invites both questions.** Only 452 of 3,205
`.bnds` nodes (14.1%) have a flag-shaped name at all; the rest are named for
what they are (`bound_bridge_01`). And the match is not free: those 452 names
span **82 distinct 12-bit patterns**, with all-false accounting for just 2
nodes (0.4%). A random 12-character f/t string would agree by luck with
probability 2^-12.

So the name is a redundant, human-readable copy of the flags. That has a
practical consequence for any editor: **change a `BoundFlag` and the node name
becomes a lie** unless it is renamed too. Nothing in the data enforces
agreement — it held on 452/452 because the tool that wrote these files
generated both from the same source.

### Why this matters beyond the parse

`.bnds` attributes are the collision surface: whether a bound blocks the player,
the camera, enemies, horses, shooting, sneaking, doors, water. That is the
"remove collision from a model" capability in M16, expressed as twelve editable
booleans per bound, across 497 files.

## `.skel` — DECODED: the bone hierarchy is recursive. Plus `.chase`, the last trap.

*rdr-re, 2026-09-14. `03_Tools/research/probe_skel.py`, read-only, no lock.*

The last two items in the `props.py` structural gap. `.skel` is 1,128 entries
(236 distinct) and **33,557 of the 48,506 block opens still missed (69%)**;
`.chase` is 63 entries holding **63 of the 139 remaining trap fields**.

**236 distinct `.skel` parsed, 0 failed. `Version: 101` on all 236.**

### Why a flat reader cannot count it

A bone block *contains* bone blocks, to arbitrary depth:

    Version: 101
    NumBones N
    bone <name> {
        offset x y z
        transX transY transZ rotX rotY rotZ scaleX scaleY scaleZ  -- valueless
        bone <child> { ... }                                      -- RECURSIVE
    }

No earlier format on this disc did that. Measured depth runs from 0 to **20**,
with the bulk at 7–9. `offset` is 3 floats on **8,844 / 8,844** bones.

The nine `transX..scaleZ` keys carry **no value**. They are channel
declarations — which degrees of freedom the bone animates. 13 distinct channel
sets, dominated by `rotX rotY rotZ` (6,101 bones), then the 6-channel
translate+rotate set (1,575) and the full 9 (949). 63 bones declare none.

### `NumBones` counts the FIRST root's subtree, not the file

234 of 236 files have a single root bone, and the count matched immediately.
Two did not — and the right reading makes it **236 / 236**:

    blk.skel     declared 52   first root `root` 52 bones  + cof_01 (2), gat (8)
    own01.skel   declared 52   first root `root` 52 bones  + skel_ptl (1)

The extra root-level bones are **attached props with their own small
skeletons** — `gat` is a gatling gun, `skel_ptl` a pistol. They are not part of
the character and are not counted. The first root is named `root` on **236 /
236** files, so the rule is unambiguous.

*This is the same shape as an earlier mistake and I want it recorded as a
success rather than quietly fixed: the first pass reported "2 bad" and the
temptation was to call them corrupt. They are not — the invariant was stated
wrong.*

### Cross-check: `.anim`'s channel count comes from the skeleton

`.anim` carries `channels` at +0x08 (`anim.py`). Pairing each clip to a
skeleton by filename stem:

| | files | share |
|---|---:|---:|
| A: `channels` == the skeleton's own declared channel total | 5,475 | 55.2% |
| B: `channels` == `NumBones*3 + 3` (all bones rotate, root also translates) | 2,721 | 27.4% |
| C: paired to the wrong rig | 1,721 | 17.4% |

**82.65% of 9,917 paired files.** The 17.4% is not a failure of the rule — it
is a failure of my pairing. **90.1% of them have `channels` exactly 162 and
5.9% exactly 159**, and 162 is `red.skel`'s 53×3+3 while 159 is 53×3. Those
clips animate a **shared 53-bone humanoid rig** rather than the skeleton their
filename prefix names; across all `.anim`, 159 (5,668) and 162 (4,100) are the
two dominant values by a wide margin. Pairing by stem is a guess about which
rig a clip drives, so A+B is a floor, not a rate.

**The pairing-free version of the claim, which needs no guess:**

    channels divisible by 3 : 10,230 / 10,230 = 100.00%
    CONTROL                 : a random integer is, one time in three (33.3%)

28 distinct values, every one a multiple of 3 — which is exactly what
3-channels-per-bone requires.

> **Scope correction, 2026-09-14 (found while decoding the uncompressed
> `.anim` path).** Those 10,230 files are the ones `anim.parse()` handles,
> i.e. the `cm2` codec — the function raises on the other codecs, so the
> sample silently excluded them and I did not say so. **Divisibility by 3 is
> not a property of `.anim` in general.** Among the 181 uncompressed files,
> **20 break it**: `channels=7` on 15 camera clips
> (`lvl_front_shot_introcam.anim`), and 17 and 29 on five face animations
> (`fchk_face_anim_chanc.anim`, `wsk_face_anim_01.anim`).
>
> The corrected claim is narrower and says more: **3-channels-per-bone holds
> for SKELETAL animation.** A camera is not a skeleton — 7 channels is a
> camera's own parameter set — and a face rig animates morphs rather than
> bones. See the `.anim` section below.

### `.chase` — 63 files, one file, the last trap field

All 63 archive entries are the same 84-byte `trainchase.chase`, **byte-identical
everywhere**:

    NumTrainChases: 3
    NumSideOffsets: 3
    SideOffsets: { 4.000000  5.500000  7.000000  }

`NumSideOffsets` == `len(SideOffsets)`. The trap is the same shape as `.nav`'s
`Tracks`: `props.py` offers `SideOffsets` with the braces *inside the value*,
so an edit through the generic path writes them back as text. With `.nav`'s 75
and `.enm`'s 1, that is all 139 remaining trap fields accounted for.

## `.swui` — the M14 tag table, validated against the developers' own spec

*rdr-re, 2026-09-14. `03_Tools/research/probe_swui.py`, read-only, no lock.
This is the M14 prerequisite work, done on the reviewer's instruction; the pointer was
filed as REQ-046 out of the LEFTOVERS sweep.*

M14 is *"`.swui` editing for HUD changes"* with a byte-exact round-trip as its
acceptance test. What that needs is not another parser — `rdrstudio/swui.py`
already has a good one — but a **tag table somebody can trust**. The disc ships
the documentation for it.

### Two spec files, not one

`example.swui` (SWAT.DAT, 3,005 B) is a commented reference for the format,
written by the people who wrote the parser. There is also **`example_jp.swui`**,
the Japanese build's copy, and it documents two tags the English one had only in
commented-out form (`MODEL`, `MODELCOMBOSELECTION`). Between them they document
**25 tags**.

Both are excluded from the corpus statistics below — a spec is not a screen, and
counting it would let the documentation corroborate itself. *That exclusion also
explains a small discrepancy: `swui.py`'s own docstring reports 187 tag-dialect
files, measured with `example.swui` counted as a screen.*

Three more screens carry documentation **in live markup** — the developers left
the argument names in as placeholders:

    postermultiview.swui  <FONTGRID: grid id : grid pos x,y : scale x,y : rows : cell size x,y : spacing x,y >
    multichar.swui        <FONTGRID: ... >
    multichar_jp.swui     <FONTGRID: ... >

### The corpus

**213 UI files** across four extensions (`swui` 203, `swuibook` 5, `swuipop` 4,
`swuipost` 1), in three dialects — **185 tags, 13 list, 12 props**, plus **3
unreadable**: `blank.swui`, `blank_jp.swui`, `wip.swui`. (The first two are
empty by name and the third says what it is.)

### `swui.py`'s parser loses nothing — 4,792 / 4,792

    swui.parse() emits 4792 tags; a raw regex scan of the same files finds 4792.

That is the check M14 actually needed, because a parser that silently drops a
tag cannot round-trip. It drops none. The only disagreements between the two
counts are **case** — a few screens write `ModelComboData` where the parser
normalises to `MODELCOMBODATA` — and that is not a write risk either: `apply()`
splices **only the value span**, so a tag's name is never rewritten.

### 44 tags used, 25 documented, 22 undocumented

The spec is older than the shipped screens. The heavily-used tags it does not
mention:

| tag | uses | fields |
|---|---:|---|
| `BASECOLOR` | 132 | 1 (`r,g,b,a`) |
| `COLORC` | 97 | 1 |
| `COLOR` | 85 | 1 |
| `FONTGRIDDATA` | 84 | 3 |
| `GROUP` | 82 | variable — counted, see below |
| `TEXPHASE` | 51 | 1 (`FRONTEND1`…) |
| `SLIDE` | 49 | 5 |
| `TEXTURESLIDE` | 25 | 1 |
| `COLORCHANGE` | 23 | variable — counted |
| `TEXTBOX` | 21 | 3–7 |
| `FLOAT` | 19 | 5 (`name:value:min:max:step`) |
| `FACADE` | 18 | 3 — counted |
| `TRANSITION` | 15 | 1–2 |
| `SCALECHANGE` | 14 | variable — counted |
| `SLIDER` | 13 | 7 |
| `RGB` / `RGBA` | 12 / 1 | 4 / 5 |
| `MENUBOX` | 9 | 3–4 |
| `FONTGRID` | 6 | 6 |
| `BARMETER` | 1 | 8 |

Real examples, verbatim:

    <FLOAT:XlsToSwuiOffsetX:-49:-1000:1000:0.500>
    <SLIDER:Brightness:0.050:Brightness:marker_bright:groove_bright:left_bright:right_bright>
    <BARMETER:61,0,0:10,4:4,0:6:0.478,0.227,0.227,0.192:3:durabilitybar_tuning:NoTexture>
    <FACADE:barberText:2:map_barber_closed,map_barber_open>
    <MENUBOX:200,380,0:80:10:HORIZONTAL>

**Documented but never used: 3** — `CENTER`, `MODELCOMBODATA`,
`MODELCOMBOSELECTION`. Those are parser features no shipped screen exercises,
which is worth knowing before an editor offers them.

### Counted-record tags — the constraint an editor must not break

Four tags declare how many members follow. **Change a member list without
updating the count and the screen desyncs.**

| invariant | ok | bad |
|---|---:|---:|
| `GROUP`: N == members across the trailing fields | **82** | 0 |
| `FACADE`: N == tokens in field 2 | **18** | 0 |
| `SCALECHANGE`: fields == 1 + 2N | **14** | 0 |
| `COLORCHANGE`: fields == 1 + 2N | 21 | **2** |

`GROUP` needed two attempts and the failure is instructive: members appear
**either** as separate colon fields **or** as comma tokens inside one field —

    <GROUP:photo:nodata:3:front_paper1:photoleft:photoright>
    <GROUP:map_barber:lvl_floor:2:barberText,map_barber_bot>

Counting colon fields alone scores 64/82. Counting **comma tokens across all
trailing fields** scores **82 / 82**. A writer must handle both spellings.

The two `COLORCHANGE` exceptions are stated rather than smoothed over: both
declare `N=3` but carry 6 fields, and both have a five-token colour
(`1,1,1,0,0`) where every other use has four. One field is over-long. I have not
worked out whether the game tolerates it or those two elements are subtly wrong
on screen.

### 20 of 44 tags have a variable field count

Trailing fields are optional and the spec says so for some of them
(*"scale, dropshadow and center are optional for any font"*,
*"optional COLOR and SCALE attributes for TEXT, READOUT..."*). Measured:
`TEXT` occurs with 1,2,3,4,5,6,8 and 10 fields; `TEXTURE` with 2–7; `BUTTON`
with 5–9; `FONT` with 1–5. **An editor must not normalise a tag to its longest
form** — that would change bytes the game reads.

Full per-tag table in `07_Research/swui_tags.json`.

## `.anim` — the 182 files nobody had read

*rdr-re, 2026-09-14. `03_Tools/research/probe_animraw.py`, read-only, no lock.*

`anim.py` decodes the `cm2` codec and `animcmp.py` the `cmp` one. Its docstring
named what was left, and that is what this closes:

    magic          files
    cm2\0          3,771   anim.py
    cmp\0            234   animcmp.py
    00 00 00 00      181   "an uncompressed path that nobody has read yet"
    00 00 28 4E        1   "one file, unexplained"

4,187 distinct names, and after this all 4,187 are accounted for.

### The uncompressed path: `17 + frames x channels x 4` — 181 / 181

The 17-byte header is the same one the resource loader parses at `0x002CF0F0`
(magic, frames, channels, one f32, one flags byte). With a NULL codec pointer
the payload is simply **one float32 per channel per frame**, and the size
identity holds on every one of the 181 files with no exceptions.

**A correction to `anim.py`'s own docstring, which says this path is
"8-bytes-per-key".** It is not 8 bytes per key in any useful sense: a *key* is
`channels` floats wide, and a *sample* is 4 bytes. The identity
`size == 17 + frames*channels*4` is exact on 181/181, and no 8-byte reading
reproduces it.

### What is in them — and the scope correction it forced

Channel counts among the 181: 162 (39 files), 18 (32), 42 (28), 6 (26), 12 (17),
**7 (15)**, 15 (10), 30 (9)…

The 7 is the interesting one. **20 of the 181 have a channel count that is not
divisible by 3** — 7 on fifteen camera clips (`lvl_front_shot_introcam.anim`,
`lvl_front_shot_book_cam.anim`), and 17 and 29 on five face animations
(`fchk_face_anim_*`, `wsk_face_anim_*`).

That is a direct counterexample to the way I stated the `.skel` cross-check
above, and I have annotated it there. My 10,230-file sample came from
`anim.parse()`, which handles `cm2` and raises on everything else, so it
excluded these without my saying so. **The corrected reading is narrower and
more informative: 3-channels-per-bone is a property of SKELETAL animation.**
Cameras and face rigs are in the same extension and do not follow it.

### The one file that is not an animation

`red_shit.anim` (SWAT.DAT, 16,217 bytes) is reported with evidence rather than
a guessed codec. Three independent things are wrong with it:

- its first four bytes match **no codec the loader knows**;
- it declares 48 frames x 162 channels, which the uncompressed layout would
  need **31,121** bytes for; the payload is **16,200**, and the `cm2` layout
  does not fit either;
- reshaped as int16 key-major under **every** factorisation of its 8,100
  values, the per-channel series is **no smoother than a shuffle of the same
  bytes**:

      key-major C=162 (K=50)    16633.0
      key-major C=50  (K=162)   16279.0
      key-major C=90  (K=90)    16378.0
      CONTROL shuffled C=162    16253.0

  Real animation channels are autocorrelated; these are not.

**The `C=324` row reads 0.0 and must not be counted as a hit.** With 324
columns and 2,355 zeros among 8,100 values, many columns are constant zero, so
the median difference is 0 for free — the same "divides evenly is nearly free"
trap this lane has been caught by before. The probe prints it rather than
hiding it.

Its name also places it: `red_shit.anim` sits alongside `crap.actb`
(six bytes, `adfadf`) and `shit.txt` (an octree debug dump) in the LEFTOVERS
sweep. **It is developer junk that shipped**, not a codec waiting to be found,
and I am not inventing one for a single file.

## The tail: `.hot` `.pmtl` `.duel` `.msd` `.amp` `.guns` `.sphs` `.dst` `.natr` `.cam`

*rdr-re, 2026-09-14. `03_Tools/research/probe_tail.py`, read-only, no lock.*

The last ten extensions in the `props.py` structural gap — 14,949 block opens,
58% of them in `.hot`. **845 distinct files, 845 parsed, 0 failed.** With
REQ-047/049/050/051 this takes the gap from 271,618 missed opens to **zero
unaccounted for**.

They are one grammar in ten costumes: a block opens as `Kind {`, `Kind name {`
or `Kind name N {`, and blocks nest. One recursive reader handles all ten.

### Count invariants

| invariant | ok | bad |
|---|---:|---:|
| `.hot` `NumHotSpots` == `HotSpot` blocks | **190** | 0 |
| `.sphs` `NumBoundSpheres` == `BoundSphere` + `BoundHotdog` blocks | **199** | 0 |
| `.pmtl` `num_materials` == `mtl` blocks | **1** | 0 |
| `.natr` `attributes N` == entries | **1** | 0 |
| `.duel` `NumDuelData` == `Type` blocks | 26 | **2** |

`.sphs` is worth a note: **two different block kinds count toward one total** —
`BoundSphere` and `BoundHotdog` (a capsule). Counting only one of them would
have scored 0.

**A parser warning for `.hot`:** its `matrix` is **twelve bare floats with no
braces**, unlike the `matrix { ... }` of the scene-geometry family. Same key,
same meaning, different spelling, same disc.

`.natr` reuses the scene family's `attributes N { TYPE name value... }`
construct, arity by type — so that decode transfers.

### The two mismatches are broken files, and they say so in their names

All three `.duel` files that fail brace balance carry **2 opening braces
against 6 closing** ones:

    xlvl_cover3.duel      open 2 close 6
    xx_lvl_cover3.duel    open 2 close 6
    xxx_lvl_cover3.duel   open 2 close 6

Every other file in all ten extensions is balanced (842 / 845). The shipped
`lvl_cover3.duel` is fine and declares 3. Someone hand-deleted the opening
braces of the nested blocks, and then marked the file dead by prefixing it —
**`x`, then `xx`, then `xxx`**, three generations of "not this one" on the same
broken file. The `xx`-prefix dead marker is the same one the LEFTOVERS sweep
counted 86 of.

So the format is consistent and the *files* are broken. The two count
mismatches are a consequence of that, not a separate problem.

### Uninitialised memory shipped inside the damage tables

**15 `.duel` files carry 0xCD bytes — 765 of them — and 0xCD is MSVC's debug
fill for heap memory that was allocated and never written.**

    NumDuelDmgData 3
    Type	pdmg5 {
      Pattern <CD CD CD CD CD CD CD CD CD CD CD CD CD CD CD>
      LossInjuryPct 1.0000...

It is the **only** high byte value anywhere in these ten formats — zero files
contain any other — and **every one of the 765 sits inside a `Pattern` field**,
51 fields across the 15 files. That is not data corruption in transit; it is an
exporter running under a debug CRT that wrote out a string buffer it had never
filled, on files including `lvl_tomb_dmg.duel`, `lvl_atl_dmg.duel` and
`xdefault_dmg.duel` (165 bytes each).

One more entry for the LEFTOVERS ledger: the retail disc ships 765 bytes of
uninitialised Windows heap.

## The last small UNKNOWNs: `.rain` `.sys` `.ico` `.psi` `.modb` `.rec` `.bin`

*rdr-re, 2026-09-14. `03_Tools/research/probe_unknown7.py`, read-only, no lock.*

21 archive entries between them, so they never ranked against the big formats.
With the `props.py` structural gap closed they are what is left, and the lane's
mandate is unexplained **bytes**, not popular ones. **Six of the seven are now
settled.**

### `.rain` — a weather config, and one file that is not one

Three of the four are plain text: a `RainStorm` header then `Key= value` lines
— note the **`=`**, which none of the other text formats on this disc use.
148 keys in `lvl_cemetery.rain`: `NumCubes`, `Amount`, `Stride`, `XVarence`,
`BirthHeightMod`, `DeathHeightMod`, `Velocity`, `VelocityVar`, `Flutter`,
`Gravity`, `Wind`, `ExpDampening`, `ThresholdX/Z`, `Size`…

**The fourth is not a `.rain` at all.** `lvl_perfnew_hf.rain` (32,424 B) has no
`RainStorm` header, and it satisfies the `.hf` heightfield identity exactly:

    w=90  h=90   24 + 90*90*4 = 32,424 = its size, exactly
    all 8,100 heights finite, range -7.608 .. 3.589

Its name even says `_hf`. It is a **heightfield saved under the wrong
extension**, and it is a *different* terrain from the real `lvl_perfnew.hf`
(100x100) — so it is an alternate or earlier version, misfiled. Anything that
routes on extension alone will mis-handle it; that is the same lesson `swui.py`
records in its `dialect()` docstring — *the extension is not the format*.

### `.sys` / `.ico` / `.psi` — the PS2 memory-card save assets

`icon.sys` carries the magic **`PS2D`**: the PS2 memory-card save descriptor,
the thing that gives a save its title and points at its icons. It names
**`iconview.ico` three times** — the normal, copy and delete icon slots.

The icons it points at are on the disc, under two extensions. Both are the
standard PS2 icon format and the header is identical across all six files:

    0x00  u32  magic        0x00010000   (all 6)
    0x04  u32  anim shapes  1 or 8
    0x08  u32  texture type 0x0F or 0x07
    0x0C  f32  1.0                        (all 6)
    0x10  u32  nbvtx

| file | ext | shapes | tex | nbvtx | nbvtx % 3 |
|---|---|---:|---|---:|---:|
| `slime1.ico` | ico | 1 | 0x0F | 990 | **0** |
| `slime2.ico` | ico | 8 | 0x07 | 708 | **0** |
| `slime3.ico` | ico | 8 | 0x07 | 708 | **0** |
| `iconcopy.psi` | psi | 1 | 0x0F | 276 | **0** |
| `icondel.psi` | psi | 1 | 0x0F | 276 | **0** |
| `iconview.psi` | psi | 1 | 0x0F | 276 | **0** |

`nbvtx` is divisible by 3 on **6 / 6** — a triangle list, as the format
requires. Note the mismatch worth knowing: `icon.sys` asks for
`iconview.**ico**` while the file on disc is `iconview.**psi**`. Same format,
two extensions.

### `.modb` — a `.mod`

One file, `pagejournalr_lod_01.modb`, and its first 13 bytes are
`version: 2.12` — **byte-identical to the header every `.mod` on the disc
carries**. It is not the same content as `pagejournalr_lod_01.mod` (10,556 vs
8,713 bytes), so it is a variant of that model, not a copy.

### `.rec` — 30 fps recordings, PARTIAL

All four open with the float **30.0** and have a **32-byte** byte period, which
is the record stride. `kelley.rec`, `puppetshow.rec`, `rec_out.rec` — the names
say recording, and `puppetshow` ties them to the `.pup` cutscene system.

Marked **PARTIAL, not DECODED**: the stride is measured but the fields inside a
record are not named, and I am not calling a format decoded on a stride.
`shit.rec` is the only one whose size is not a multiple of 16 — truncated, and
named like the rest of the developer junk on this disc.

### `.bin` — still UNKNOWN, with the negatives recorded

`agestdfx.bin` (SWAT.DAT, 27,704 B). 2,052 leading zero bytes, 55.4% nonzero
after that, 45 printable runs and none of them words. The name suggests the AGE
(Angel Game Engine) standard effects data, next to `AGESND.IRX`.

**Tested and rejected: SPU ADPCM.** 16-byte blocks with byte 1 as flags 0..7
pass on 794 / 1,731 blocks — 45.9% against a 3.1% base rate, which looks like a
hit. **It is not one.** 677 of those blocks are entirely zero, so they pass for
free; strip them and 117 real passes remain out of 1,054 non-zero blocks, which
is nothing. Recorded so the next person does not spend the same hour.

This is the only extension on the disc still marked UNKNOWN with game data in
it.

## `.mod` — there is a HUMAN-READABLE dialect, and 251 files use it

*rdr-re, 2026-09-14. `03_Tools/research/probe_modtext.py`, read-only, no lock.
M15 prerequisite work.*

M15 is the `.mod` importer for weapon models. `.mod` sat PARTIAL with 8,600
entries (3,008 distinct names) behind `rsc.py` / `vif.py`, which read the PS2
binary display lists.

**`.mod` is four container versions, and they are not one format:**

| version | distinct names | archive entries | form |
|---|---:|---:|---|
| `2.12` | 2,635 | 7,061 | binary |
| **`1.10`** | **251** | **685** | **plain ASCII — 251 / 251 pure** |
| `2.00` | 60 | 792 | binary |
| `2.10` | 62 | 62 | binary |

*Count the unit you mean.* rdr-fmt reported 683 text `.mod` files and this
section says 251 — both correct: **685 archive entries, 251 distinct names**,
because most models ship in several archives. (683 of the 685 separate the
version string with LF; **two use CR**.) Totals: 8,600 entries, 3,008 names.

**A model in this game can be read, diffed and written in a text editor.** For
M15 and M18 that is a different proposition from emitting VIF display lists,
and nothing in the tool or the docs said so.

### The text dialect

A header declaring every count, then one record per line:

    version: 1.10
    verts: 942      normals: 1185   colors: 1      tex1s: 741
    tex2s: 0        tangents: 0     materials: 1   adjuncts: 1383
    primitives: 1848                matrices: 52   reskins: 3

    v   0.000000 -0.023228  0.120881          -- vertex
    n   ...                                   -- normal
    c   ...                                   -- colour
    t1  ...                                   -- texcoord set 1
    mtl initialShadingGroup {
        packets: 7   primitives: 188   textures: 0
        illum: diffuse
        ambient: 1.0 1.0 1.0    diffuse: 1.0 1.0 1.0    specular: 0 0 0
        attributes: 0
    }
    packet 52 13 1 {
        adj  0  0  0  0 -1  0
        ...                                   -- exactly 52 `adj` lines
    }
    reskin 43 2 0.50 -0.003122 0.636476 0.003841

### What verifies

| identity | ok | bad |
|---|---:|---:|
| `verts` == `v` lines | **251** | 0 |
| `normals` == `n` lines | **251** | 0 |
| `colors` == `c` lines | **251** | 0 |
| `tex1s` == `t1` lines | **251** | 0 |
| `tex2s` == `t2` lines | **251** | 0 |
| `tangents` == `tan` lines | **251** | 0 |
| `materials` == `mtl` lines | **251** | 0 |
| **`packet N { ... }` == its own `adj` line count** | **3786** | 0 |
| **sum of each material's `packets:` == packet blocks in the file** | **251** | 0 |

The last two are the interesting ones: they are *nested* counts, so the file
declares its structure at three levels and all three agree.

### What does NOT verify — reported open, not guessed

`adjuncts`, `matrices` and `reskins` are declared in every header and match
none of the rules I tested:

    adjuncts == distinct `adj` index        21 / 251
    adjuncts == max `adj` index + 1         21 / 251
    matrices == all `mtx` lines            160 / 251
    reskins  == all `reskin` lines           0 / 251

Those records live inside `packet` and `mtl` blocks and are shared between
them, so the header is counting something other than lines — most likely unique
entries in a pool the packets index into. **I am not naming it on 160/251.**
The failed rules are recorded in the probe so the next person does not retry
them.

`primitives` is also not settled: it matches `tri`, `tri+str` and
`tri+str+stp` on the same 148 of 251 files, which means those three cannot be
told apart on this evidence and none of them is the rule.

### Which dialect do the WEAPONS use — M15's real question

`.guns` names every weapon's `ModelName`, so this comes from the game's data:

    weapon TypeNames with a model: 77
      text 1.10     46 types (59.7%)   5 models: bow_lod_01, gat_lod_01,
                                       pt2_lod_01, ptl_lod_01, rfl_lod_01
      binary 2.12   31 types (40.3%)   9 models: bay_lod_01, bottle, bucknife,
                                       cof_lod_01, dynamite, molotov, poison,
                                       sawedoff, shotgun01

The text dialect drives **every revolver, pistol, repeater and rifle, plus the
bow and the gatling** — 46 weapon types off five models. It does not cover the
shotguns, melee, thrown weapons or the bayonet rifles. An importer built only
on the text reader covers 60% of the weapon list and none of the shotguns or
throwables.

## The particle/FX family — DECODED, and the chain that makes it moddable

*rdr-re, 2026-09-14. `03_Tools/research/probe_fx.py`, read-only, no lock.*

`.swptx` 12,944 entries / 788 distinct, `.swptxemitter` 4,436 / 368, `.arfx`
3,538 / 61, `.fx` 2,286 / 197. The biggest block left PARTIAL, and the one
that decides how the game LOOKS — muzzle flashes, blood, dust, fire,
explosions.

### Why they mis-read: two grammars in one file

Each file is a braced block in the ordinary `Key value` grammar **followed by a
trailing section in a different one** — `Key= value`, with an `=`, the spelling
`.rain` uses — carrying counted keyframe records:

    swptx {                        <- props.py reads this
      NumPoints 400
      TextureName firefx4
      ...
    }
                                   <- and then, OUTSIDE the braces:
    Keyframes= 5
    Time=  0.000000
    Size=  0.096999 0.093999
    Color= 0.000000 0.000000 0.460990 0.400000

A reader that stops at the closing brace gets the system's parameters and none
of its animation. That is the missing coverage, and it is why these sat at
80–98% rather than 100%.

### Counted records

| invariant | ok | bad |
|---|---:|---:|
| `.swptx` `Keyframes= N` == `Time=` records | **788** | 0 |
| `.swptx` `TuneFrames=` groups consume the line | **788** | 0 |
| `.swptxemitter` `Keyframes= N` == `Time=` records | **734** | 0 |
| `.swptxemitter` `TuneFrames=` groups consume the line | **734** | 0 |
| `.swptxemitter` `NumSystems= N` == named blocks | 366 | 2 |

**`TuneFrames=` needed two attempts and the first rule scored 0/788.** It is
not one count — the line is a *sequence* of `N v1..vN` groups that must exactly
consume it:

    TuneFrames= 3 0.0 0.5 1.0   5 0.0 0.29 0.5 0.9 1.0   2 0.0 1.0

Read as a single count it fails every file; read as repeated groups it passes
every file. A rule that fails 100% of a corpus is usually the rule, not the
corpus.

### The reference chain

    .swptxemitter  --names-->  .swptx  --TextureName-->  .tex

| hop | resolves |
|---|---|
| emitter names a particle system | **730 / 734 (99.5%)** |
| system names its texture | **728 / 788 (92.4%) distinct — 12,340 / 12,944 (95.3%) per entry** |

*Both units, deliberately.* The reference table earlier in this file carried
"96%" for this hop, measured per archive entry; per distinct file it is 92.4%.
Neither is wrong and the gap is duplication across archives — the same units
trap that made rdr-fmt's `.mod` count of 683 and mine of 251 look like a
disagreement when they were the same finding.

An emitter lists the systems it spawns **by name**, and each system names its
texture. Both hops are plain strings in plain text, so *"make this explosion
throw different particles"* and *"retexture this effect"* are name edits.

**A normalisation bug of mine worth recording**, because it made a clean result
look broken: I first stripped the `_PTX-MOD` suffix before lookup and got
90.3%, with all 71 "failures" ending in `_PTX-MOD`. They were not failures —
`Explosion_wood_PTX-MOD` **is itself a `.swptx` basename**. Matching the name as
written first takes it to 98.9%, and handling the emitters that write the name
with its `.swptx` extension takes it to 99.5%. Every one of those 71 was my
own normalisation.

### Dangling references — cut content, found by the chain

The four hops that genuinely do not resolve are cut particle systems:
`lvl_bridge_water_ripples_01`, `jail_explosion_flare_trail_01`,
`lvl_woods_rock_sparkles_01`, `water_trail_FLAT`.

More striking on the texture side: **60 `.swptx` files name a texture that is
not on the disc, and the 26 distinct names appear under NO extension
anywhere** — not `.tex`, not `.mod`, not anything:

    bottlea  bottleb  brickpiece  chairpiece  coina  glasspiece
    hsph_lod  metal_piece  rockpiece  rockweaponchunk  woodchunk  ...

They are debris: the chunks a breakable object throws. Somebody built the
particle systems for breaking bottles, bricks, chairs, glass and wood, and the
art never shipped. (I tested and rejected the obvious alternative — that these
are model names rather than textures. **0 of 26 resolve as `.mod`.**)

## `.animsnd` — the sound track of an animation, and it checks against the animation

*rdr-re, 2026-09-14. `03_Tools/research/probe_animsnd.py`, read-only, no lock.*

5,797 entries, 2,420 distinct. Three parallel arrays under one count:

    NumKeyframes 8
    animsnd {
      Frame  3 27 29 39 41 59 102 105
      Value  1.0 -2.0 1.0 -2.0 3.0 3.0 -1.0 3.0
      Value2 9.0  2.0 10.0 2.0 91.0 91.0 0.0 81.0
    }

**`NumKeyframes` == `len(Frame)` == `len(Value)` == `len(Value2)` on
2,420 / 2,420.**

**68 of those declare 0 and carry an empty block.** My first pass called them
"missing an array" and scored 2,352/2,420; they are correct files with no
events, and counting them as failures was my bug, not theirs. Worth naming
because an empty record is the easiest thing in any format to mis-read as a
broken one.

### It checks against the animation it is named for

A `.animsnd` is named for its `.anim`, so two things are verifiable from
outside the file:

| check | result |
|---|---|
| the name resolves to an `.anim` on the disc | 1,886 / 2,420 (77.9%) |
| **`max(Frame)` <= that animation's own frame count** | **1,812 / 1,840 (98.5%)** |

The frame count comes from the `.anim` header at +0x04, parsed separately, so
the keyframe indices and the clip length corroborate each other across two
files and two formats.

**The control that makes the second row mean something.** Comparing against
"the `.anim` for this stem" is only safe if there is one answer. Measured:
**0 of 4,187 stems carry more than one frame count** across all the archives
they appear in. So the comparison is not a sampling guess — and I checked that
before quoting the number, having been caught by exactly that on `.anim`
channel counts earlier the same day.

**The 28 overruns are real**, not an artefact, and I am not explaining them
away: `bty_upp_lft_rol_gup` (36 against 27), `ncb_upp_for_rol_gup` (42/37),
`hrs_for_jmp` (43/33), `pgj_ant_tkl` (65/25). Most are `_rol_gup` — roll,
then get up — which look like sound tracks written for a *sequence* rather
than the single clip they are named after. That is a hypothesis; the
measurement is the 28.

### What `Value` is

A small signed vocabulary, not free numbers: `1` (2,175), `3` (1,089),
`100` (888), `-1` (851), `-2` (397), `-3` (268), `0` (83), `126` (15). Eight
values cover essentially the whole corpus, and they come in +/- pairs, so
`Value` is an event type with a sign rather than a magnitude. **I am not
naming the types** — that needs either the ELF or a listening test, and
guessing at "footstep" from a histogram is how a glossary gets 6/24.

## `.rsc` — the RSC0 container, and the two things M18 has to verify

*rdr-re, 2026-09-14. `03_Tools/research/probe_rsc.py`, read-only, no lock.
M18 prerequisite.*

M18 is *"add NEW models to a `.rsc`"* and its acceptance test asserts that
**"the resource index and the model->texture binding still verify"**. Adding a
chunk to a container you have mis-modelled corrupts every later reference, so
both halves wanted settling first. 627 distinct `.rsc` files (1,139 entries),
623 with a non-zero table.

### The table stores chunk ENDS, not starts

`rsc.py`'s docstring calls the field `file_offset`, which reads as a start. It
is not:

    chunk i spans [previous end, offs[i]),  the first starting at header_size
    offs[-1] == the file length

| invariant | result |
|---|---|
| offsets non-decreasing | **623 / 623** |
| offsets 16-byte aligned | **623 / 623** |
| table fits inside `header_size` | **623 / 623** |
| ENDS model consistent and `offs[-1] == filesize` | **623 / 623** |
| every chunk has positive size | **623 / 623** |

Under a "starts" reading the last entry is a zero-length chunk sitting at EOF
and **623 of 627 files appear to have an offset past the end** — which is
exactly what my first run reported before I looked at what the number meant.

**Why M18 cares:** inserting a chunk is not appending an entry. Every
subsequent END offset moves, so the whole tail of the table is rewritten, and
`header_size` is not a constant either — 128 on 515 files, but also 64, 1408,
1536 and 2048.

### `resourceIndex` is 1-BASED

A `.bnds` / `.znes` / `.xznes` node's `resourceIndex` indexes this table.
Measured across every scene file against the largest `RSC0` count in its own
archive:

    1 <= v <= count : 18,539 of 18,541   (100.0%)
    v == 0          : 2                  ("none")
    out of range    : 0

**Read as 0-based, 59 scene files appear to reference one chunk past the end of
their own `.rsc`** — and every one of them has `max == count` exactly.
59 files failing identically at the boundary is the signature of an off-by-one,
not of 59 broken files. `rsc.py` says `resourceIndex` is "a direct index into
this table" without stating the base; it is 1-based, and 0 means none.

That is M18's first acceptance criterion settled in advance: **the resource
index verifies iff every node's value lies in `1..count` after the insert.**

### Chunk tags

    MOD0 8011   PMT0 4181   MIP0 3549   PAL0 3549   TEX0 3549
    BND0 2822   SAN0 1602   SKE0  359   GAI0  203   NTG0  168
    MPH0   88   RSV0   71   FNT0    2

`MIP0`, `PAL0` and `TEX0` occur **3,549 times each** — one triple per texture,
which is the other half of M18's test (the model->texture binding) and a
count worth asserting on.

## `.rnt` — DECODED: the layout is FIXED, not variable

*rdr-re, 2026-09-14. `03_Tools/research/probe_rnt.py`, read-only, no lock.*

`.rnt` gives the chunks in a `.rsc` their artist names — what stops a tool
handing you a disembodied head when you ask for "LOD 2". 1,226 entries, 769
distinct.

`rsc.py`'s `parse_rnt` reads it with a deliberately tolerant scan and states
why: *"the entry field order varies within the file"*. **It does not vary.**

    0x00  "RNT0"
    0x04  u32 count
    0x08  16 bytes, zero                                   769 / 769
    0x18  count x { u32 name_offset, char[4] tag, u32 index }
          name block at exactly 0x18 + 12*count            607 / 607
          every name_offset lands on a string start        607 / 607

The other **162 files declare `count == 0`** and carry no entries — an empty
manifest, not a mismatch. Counting them as failures is what made my own first
run report 607/769 instead of 607/607; `min()` of an empty list was the bug.

### What the tolerance costs: 462 entries

The scan anchors on `rsc.py`'s `RNT_TAGS`, and two tags that occur in real
entries are missing from it — **`GAI0` (457 occurrences) and `FNT0` (2)**:

    entries by the fixed layout : 6,181
    entries parse_rnt returns   : 5,719   (92.5%)

Five files lose entries, and the losses are not small:

    preload.rnt       982 entries -> 855
    shit.rnt          902 entries -> 775
    preload_02.rnt    798 entries -> 595
    fe.rnt             64 entries ->  61
    fakebook_01.rnt    42 entries ->  40

A fixed-offset reader needs no tag list at all and returns all 6,181. (`shit.rnt`
appearing here is consistent with the 275x result above — though in this case
the file is fine and the *parser* is what drops its entries.)

### Corroboration for REQ-055

`parse_rnt`'s docstring already records that the stored resource index is
**1-based** — *"`mrd_lod_01` says 4 and is chunk 3"* — arrived at from the
character bundles. My `resourceIndex` measurement in REQ-055 reaches the same
conclusion from the scene files, 18,539 of 18,541. **Two independent paths to
the same off-by-one**, which is worth more than either alone.

## Reference integrity across the disc

*rdr-re, 2026-09-14. `03_Tools/research/probe_refs.py`, read-only, no lock.*

4,213 keys name real files. Do they resolve?

    ref keys resolving 100%      4,163
    ref keys resolving partially    50
    ref keys resolving 0%            0

**The dependency graph is 98.8% intact at key level and nothing is entirely
broken.** That is the modding-safety map: rename a file and this says what
breaks.

**The caveat is half the answer, so it travels with the number.** A partially
resolving `ref` key is **not** automatically a dangling reference. `ref` is a
heuristic — a key qualifies when at least half its values match a filename — so
a key whose values are a small vocabulary can qualify by coincidence.

`pmtl`'s `mtl/sound` is the control. Its 24 values are `none`, `dirt`, `wood`,
`adobe`, `rock`, `metal`, `water`, `stone`… surface names. The disc happens to
contain `dirt.tex`, `wood.tex`, `rock.tex`, so it scores **69%** — and it
refers to no file at all.

So the 50 partial keys split into two classes — genuinely cut references, and
keys the heuristic mis-classified — and only the first is cut content. They are
reported separately rather than summed into one alarming figure. The ones most
likely to be real are `guns GunRack/…/HolsterModelName` (59.9% over 3,437
occurrences) and five `rep` animation keys at ~60.6%.

## `.fxcr` — effects bound to SKELETON BONES, checked against `.skel`

*rdr-re, 2026-09-14. `03_Tools/research/probe_fxcr.py`, read-only, no lock.*

`.fxcr` is a character-effect binder, and it names the bones an effect attaches
to — so "set this character on fire along the left arm" is a bone list in a
text file:

    fxcr { Duration 10.0  Loop 0 }
    NumPtxSystems= 0
    NumEmitters= 4
    burning_01                                      <- the effect
    NumEvents= 1
    0.000000 1                                      <- time, flag
    clavicle_l,shoulder_l,elbow_l,wrist_l,neck_01   <- the bones it burns on
    Offset= 0.0 0.0 0.0
    ... x NumEmitters

| check | result |
|---|---|
| `NumEmitters= N` == emitter blocks | **29 / 29** |
| `NumEvents= N` == event lines | 31 / 33 |
| **bone names resolve against `.skel`** | **66 / 67 (98.5%)** |
| effect names resolve to `.swptx` / `.swptxemitter` / `.ptx` | 31 / 33 (93.9%) |

The bone check is the one worth having: the 352 distinct bone names come from
this lane's own `.skel` decode, parsed from a different format entirely, and
`.fxcr`'s lists land inside that set. Two formats checking each other.

**The single unresolved bone is `body`**, and I am not calling it a dangling
reference — no skeleton has a bone called `body`, so it reads as a special
value meaning the whole character rather than a joint. That is a reading, not a
measurement; the measurement is 66/67.

**The unresolved effect is `puppet_step_dust`** (twice) — that one *is* a
dangling reference, and it joins the 26 missing debris textures and 4 missing
particle systems from the FX family above.

## `.reigns` — horse-rein physics, and it indexes its own mass list

`NumMasses= 15` then `LeftHand= 0`, `RightHand= 14`, `Saddle= 0` — indices into
that list. **39 / 39 in range**, none out of bounds. Small, but it is the same
self-consistency shape and it costs nothing to assert.

**`.film`, `.crk` and `.ptx` were PARTIAL here for one turn**, on the grounds
that "I looked at it" is not a decode. That is now followed through — see the
section below. **`.cm` still is**, and for the same honest reason.

## `.film`, `.crk`, `.ptx` — the invariants I deferred, now measured

*rdr-re, 2026-09-14. `03_Tools/research/probe_filmcrk.py`, read-only, no lock.*

One section above I wrote that these stayed PARTIAL because *"I looked at it is
not a decode"*. This is the follow-through. Three earn DECODED; `.cm` does not.

| invariant | ok | bad |
|---|---:|---:|
| `.crk` `State_N` numbered 1..N with no gaps | **259** | 0 |
| `.ptx` `StartTextureTile` <= `EndTextureTile` | **118** | 0 |
| `.ptx` `EndTextureTile` <= `TilesX * TilesY` | **118** | 0 |
| `.ptx` `TextureName` -> a `.tex` on the disc | **118** | 0 |
| `.film` `DustMinCount` <= `DustMaxCount` | **52** | 0 |
| `.film` `fxFilmPresetNames` values -> a `.film` file | **346** | 0 |

`.film`'s preset block is a small reference chain of its own: a film preset
names other film presets (`"duelslow"`, `"gameplay"`, `"loadshow"`,
`"weaponselect"`), and every one of the 346 resolves.

**`.cm` stays PARTIAL.** Camera tuning: a fixed set of named blocks —
`General`, `Follow`, `Riser`, `Swing`, `TurnZones`, `Gatling_Cam`, `Roam_Cam`,
`HorseRiser` — with float leaves. No count, no cross-reference I could find.
Nothing to assert, so nothing is claimed.

### Three wrong rules, and each failed in a way that named its own fix

Worth recording together, because the pattern is the useful part:

1. `EndTextureTile < TilesX*TilesY` failed on 12 files — **all with
   `End == TilesX*TilesY` exactly**. The bound is inclusive. That is the same
   signature as `resourceIndex` being 1-based in the `.rsc` work: when every
   failure sits precisely on the boundary, the rule is off by one, not the
   data.
2. `fxFilmPresetNames` scored **346 / 692** because I matched every word in the
   block and half of them are the KEYS, not the values.
3. Then **0 / 346**, because the values are quoted and I compared
   `"duelslow"` against `duelslow`.

None of the three was subtle; all three were caught only because the number
came out wrong in a shape that pointed somewhere. A rule that fails 100%, or
exactly 50%, or only on the boundary, is telling you which rule to change.

## `.acttbl` — the weighted action table, and 47 behaviours that were cut

*rdr-re, 2026-09-14. `03_Tools/research/probe_acttbl.py`, read-only, no lock.*

832 entries, 536 distinct. **Not blocked by REQ-021** — that closed the MIPS
disassembly of the behaviour opcode *dispatch*, which is a different question
from what this file does. `.acttbl` is plain text and says what it is:

    columns 6
    0.2  0.2  0.2  0.1  0.1  0.2              <- weights, one per column
    Goto_Balc4  Goto_Balc6  ... Goto_Balc12   <- row 1
    ... 16 rows

A weighted random-selection table: pick a column by weight, and the row gives
the `.actb` behaviour to run.

| invariant | ok | bad |
|---|---:|---:|
| `columns N` == weight count | **536** | 0 |
| every row has N entries | **536** | 0 |
| weights sum to 1.0 | 533 | 3 |
| 16 rows | 523 | 13 |
| action names resolve to a `.actb` | **33,706 / 34,308 (98.2%)** | |

**What the 16 rows index is not settled here** and I am not guessing at it.

The three weight outliers are named rather than smoothed: `lvl_tomb_endrwd`
sums to **150.0**, and `playidlevar1_barfight_md` / `playidlevar2_barfight_md`
to 1.25. Either the engine normalises and 1.0 is a convention, or those three
are wrong; I cannot tell which from the data.

### 47 cut behaviours, in 12 of 536 files

The 1.8% that do not resolve are not scattered — **47 distinct names, all in 12
files**:

| file | cut behaviours |
|---|---|
| `gravedigger1..4_rp_deathrow0.acttbl` | 20 — `GraveDiggerN_RP_R0B1..5` |
| `mrblack_rp_deathrow0.acttbl` | 5 — `MrBlack_RP_R0B1..5` |
| `lvl_tomb_endrwd.acttbl` | 10 |
| `circle_and_fire.acttbl` | 3 — `Circle_And_Fire_1..3` |
| `bartender_speech.acttbl` | 2 — `BarTender_Dialog2`, `Dialog3` |
| `reposition_jack.acttbl` | 2 — `CRun_Jack`, `CWalk_Jack` |
| `vshoot_ghost_jack.acttbl` | 2 — `Shoot_Enemy_Jack`, `SubFire_Enemy_Jack` |
| `vshoot_dad.acttbl` | 2 — `Shoot_Yrd_RedSr_Dad`, `SubFire_Yrd_RedSr_Dad` |
| `gglattacklong.acttbl` | 1 — `hdhseek` |

**A whole scene was designed and never built.** Four gravediggers and Mr Black,
five behaviours each, all in `*_rp_deathrow0.acttbl` — the selection tables
ship, the behaviours do not.

And note `reposition_jack` / `vshoot_ghost_jack` referencing `CRun_Jack`,
`CWalk_Jack`, `Shoot_Enemy_Jack`: **more traces of the Jack protagonist** the
`TEMP_JASON_*` intro text describes, in a second, unrelated format.

## `.mod` binary 2.1x — the layout, read out of the executable (REQ-050)

*rdr-re, 2026-09-14. `03_Tools/research/probe_modbin.py`, read-only, no lock.
The reviewer assigned this as a disassembly job after the corpus route stalled at 2%.*

**The dispatch is at 0x3B6A50 and the parser at 0x3B6E38.** Fourteen header
bytes are read, NUL-terminated at 13, then `strncmp(buf, "version: 2.1", 12)`.
**The literal is twelve characters — `version: 2.1`, not `version: 2.12`** — so
2.10 and 2.12 share a branch, and the minor version comes from
`lbu t1,12(sp); addiu t1,t1,-38`: `'0'` -> 10, `'2'` -> 12. Measured: code 12
on 2,633 files, code 10 on 62.

**The branch picks a READER, not a parser.** `2.1` and `2.00` get vtable
`0x005062F0`; `1.10` and the no-match fallback get `0x005063A0`. All four then
`jal 0x3B6E38`. **One driver, two readers** — which is why the eleven field
names appear beside both string clusters, and why the binary field order is
exactly the text dialect's.

Each binary primitive is a `jal 0x19A070` stream read with a byte count, so the
file is sequential with no offsets:

    +0x10 u8   +0x14 u16   +0x18 u32   +0x1C f32
    +0x20 vec2(8)   +0x24 vec3(12)   +0x28 vec4(16)   +0x2C vec3,w=1
    named: +0x34/+0x38 u32   +0x40 vec2   +0x44 vec3   +0x48 vec4

### The layout

    +0   char[14]  "version: 2.1x\0"
    +14  u32 x 11  verts normals colors tex1s tex2s tangents
                   materials adjuncts primitives matrices reskins
    +58  verts x 12 | normals x 12 | colors x 16 | tex1s x 8 | tex2s x 8

| check | result |
|---|---|
| vertex floats finite and \|v\| < 10000 at +58 | **2,695 / 2,695 (100.00%)** |
| **CONTROL — the same test read at +59** | **10 / 400 (2.5%)** |
| size − header − vertex block non-negative | **2,695 / 2,695** (min 126 B) |

The control is what makes it a finding: one byte off, the same sanity test
collapses to 2.5%, so +58 is the offset rather than a place floats happen to
look plausible.

### Two things this settles that statistics could not

**`reskins` is a count after all** — 1 on 2,322 files, 3 on 370, 2 on 3. The
`0xCCCCCCCC` that suggested otherwise comes from exactly two 70-byte empty
stubs (`krd_lod_01.mod`, `sncb_lod_01.mod`), and it is the **stack** fill, as
against the heap `0xCD` in the `.duel` files. Two junk files were distorting a
2,697-file statistic — the same shape as the 275x anomaly result in
`LEFTOVERS.md`.

**And why no fit was ever going to work.** The tail after the vertex block is
`mtl <name> { ... }` blocks and `packet N a b { adj ... }` runs — **variable
length**. There is no per-count byte cost for them, so a least-squares model
over the eleven counts cannot converge no matter how clean the corpus.

### The material block — 2,695 / 2,695

From the driver at 0x3B7460-0x3B76A0:

    name                 -- slot +0x0C, space/CR/LF/TAB terminated
    u32 packets, primitives, textures, illum
    vec4 ambient, diffuse, specular      -- BINARY; text reads vec3 here,
                                            branch at 0x3B75F0 on the t0 flag
    textures x name
    u32 attributes       -- ONCE per material, not per texture

**That last line is the trap.** With `textures: 1` a per-texture and a
per-material reading are indistinguishable; only a material with `textures: 0`
separates them. `revolver01.mod`'s first material is `default` with
`textures: 0` and still carries the u32. Per-texture scores 2,599 / 2,695;
per-material scores **2,695 / 2,695**.

Note a binary `.mod` **does** contain ASCII names: slot +0x0C is literally the
same routine (0x25FF40) in both vtables, so material and texture names are
plain text inside the binary file.

### Still open: the packet section

The 50 to 132,547 bytes after the materials. What is established:

* it is **bulk-read**, not field-by-field — no vtable primitive covers it;
* the consumer is **0x3B8780**: two `u16` counts at +0 and +2, then a `u16`
  array at +4, building an adjacency map;
* the driver then calls `0x19A408` on the stream handle and rebuilds a string
  around a literal `46` (`'.'`) — an extension swap, so **a second stream may
  be involved**.

Two attempts at walking it scored 953 / 2,695 with the rest overrunning — the
signature of a wrong model, not a nearly-right one. **Not filed as a guess.**


## Model <-> collision link (rdr-collision, 2026-11-05)

`03_Tools/research/probe_model_collision_link.py`, all 61 level archives, read-only.
A level model (`zone_<stem>_NN`, .znes -> MOD0 chunk) and its collision
(`bound_<stem>_NN`, .bnds -> BND0 chunk) are linked **only by name stem**; the
chunk numbers are not adjacent (6/1,470). The link is real geometrically:
1,470 stem pairs, world AABBs overlap on 1,377 (93.7%), IoU>0.3 on 1,284
(87.3%), model centre inside the shell on 1,373, median IoU 0.99999 -- the shell
is the model's own geometry. IoU=0 cases are `alpha_*glass*` decals. Merged
`bound_bound_grp_*` shells (and props with no `bound_` twin, e.g. SHER70's
`zone_ppear01_01` cactus) have no single partner: nothing in the data links them.

**BND0 layout used by the collision generator (2026-11-05, `custcoll.py`).** One chunk = 0x68-byte header (AABB at +0x08/+0x14, centre +0x20, radius +0x38, origin-distance +0x3C, counts at +0x4C: verts, faces; vertex offset +0x68, node-array offset +0x7C) | N x 12-byte verts | M x 32-byte face records (normal 12 B, **area** 4 B, 4 x u16 vertex ids in polygon order, 8 B of 0xFF) | node count (u32) + 32-byte node (box, child, face-list offset) | face list = tight box (24 B) + u32 count + u16 face ids. Verified by regenerating the stock 5-face box (SHER70 chunk 83) from its own AABB: identical except the last bit of 5 areas. In-game: a chunk built this way, appended as a NEW chunk and referenced from a new `.bnds` node, blocks the player (wall test). The engine's reaction to 6-face boxes or to chunks added to bundles other than SHER70's is untested.

## BND0 collision index = an octree; most collision lives in merged shells (2026-11-12) — DECODED
After the face records comes an OCTREE, not a flat node list: root cube, then 8-child blocks depth-first; a leaf holds a
tight box, a u16 count and face numbers; nodes split at >15 faces while depth < 8 and wider than 0.5 m. `+0x40` =
0.035 x the shortest box edge, `+0x44` = 0.3 x that, `+0x80` = one bit per face. Stock index audited with an exact
triangle/box test on SHER70 41/41 shells (0 missing, 0 extra): `03_Tools/research/probe_bnd_octree.py`,
`rdrstudio/bndtree.py`. Rebuilt shells collide correctly in PCSX2 (moved and turned; see DEEPDIVE-2026-11-11.md).
`bound_bound_grp_<flags>` shells are the welded collision of many objects per flag set; a model's part of one is found by
connected pieces inside its box (`rdrstudio/collparts.py`). Still unknown: the 8 bytes of 0xFF per face record; whether
every level uses the same split limits (only SHER70 was audited whole).
