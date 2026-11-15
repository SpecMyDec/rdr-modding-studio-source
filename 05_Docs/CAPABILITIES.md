# CAPABILITIES — what this project claims is impossible, and whether it is

Owner: **rdr-fmt**. Milestone: **M3**.

The developer, 2026-09-13: *"get rid of assumptions like Dat files cant be added to,
they can be, and other stuff it got false ideas on."*

This file exists because at least one inherited "cannot" in this codebase is
provably wrong, and it was steering both the UI and the roadmap. Every claim
of the form *cannot / not possible / unsupported / read-only / we don't know*
that says something about **what the formats, the disc or the toolkit are able
to do** gets an entry here, a verdict, and the probe that settled it.

**Every lane writes here; nobody rewrites the file.** This register was
started twice on the same day, by rdr-fmt for the archive claims and by rdr-3d
for the animation ones, and the second write replaced the first. Nothing was
lost because it was committed — but the rule that prevents a repeat is simple:
**append under your own `## ` section, and never touch the header, another
lane's section, or the `RETIRED-TEXT` block at the end.** The `[M3]` test reads
that block; dropping it silently disarms the whole thing.

**One exception, and it is the only edit another lane needs to make there: if
you add an entry whose verdict is VERIFIED-FALSE, add its line to the
`RETIRED-TEXT` block as well** — the phrase that must never ship again, or `-`
if the claim only ever lived in behaviour and never in copy. The `[M3]` test
fails on a VERIFIED-FALSE entry that says neither, because a claim marked
settled with nothing to watch for is a claim that can come back unnoticed.
(C-028 arrived without one on 2026-09-14 and the test caught it, which is the
system working — but it turns the suite red for whoever runs it next.)

## The verdicts

| Verdict | Means |
|---|---|
| **VERIFIED-FALSE** | A probe did the thing the claim says cannot be done. The claim text must be removed or corrected everywhere it appears. |
| **VERIFIED-TRUE** | A probe tried and it genuinely does not work, or a measurement over the real files settles it. The claim stays, with the evidence attached. |
| **UNTESTED** | Nobody has tried. It stays in the register as an open question and **must not be quoted as a limit** without saying it is untested. |

`UNTESTED` is deliberately its own answer. Treating it as "true" is how this
project acquired the false claims in the first place.

## How the sweep is run

    python3 03_Tools/scripts/probe_claims.py          # candidate claim lines
    python3 03_Tools/scripts/probe_claims.py --count  # totals only
    python3 03_Tools/scripts/probe_claims.py --all    # including guard-rails

It walks `03_Tools/`, `05_Docs/`, `07_Research/`, `README.md` and `ROADMAP.md`
for the claim vocabulary. It skips vendored code (`three.module.js`, the
packaging virtualenv), build products under `packaging/dist` and
`packaging/build` — which are regenerated from the source that IS scanned — and
the lane paperwork, which quotes claims in order to settle them.

**Scope.** A candidate line becomes a register entry when it tells a reader
what the FORMATS, the DISC or the TOOLKIT can do. An implementation comment
explaining why one code branch exists ("the two eyes cannot overwrite each
other", "a stray angle bracket cannot inject markup") is not a capability
claim and is not registered. The harvester prints every candidate either way,
so the decision is auditable line by line.

---

## Register — archives, disc and formats (rdr-fmt)

### C-001 — "Files cannot be added to a Dave archive"
**Verdict: VERIFIED-FALSE.** This is the one the developer named.

**Probe:** `03_Tools/scripts/probe_dat_add.py` (read-only against the disc;
the one archive it writes is an extracted copy in a temp directory).

**Evidence, 2026-09-13:**
- `Dave.can_add()` says **64 of 66** archives would take a new file *as they
  stand today*, with no repack and no file moved.
- Proof pass on `LVLS_M2/INT2.DAT`: a new 381-byte entry was inserted at its
  sorted index 574, the archive was closed, re-opened from the bytes on disk,
  and the newcomer read back **byte-exact**. All **720** pre-existing entries
  kept the same name, offset, both size fields and the same SHA-1.
- Independently, the add path has been driven past the title screen —
  `scripts/test_add_named.py`, and commit f7f524 "The add half of file_drop is
  boot-verified".

**Where the false text is / was:**

| File | Line | Text | State |
|---|---|---|---|
| `03_Tools/rdrstudio/assets.py` | 19 | "Files cannot be added to a Dave archive -- the one hard constraint on this whole toolkit." | **fixed 2026-09-13** |
| `03_Tools/rdrstudio/assets.py` | 757 | "…files cannot be added to an archive without repacking it." | **fixed 2026-09-13** |
| `README.md` | 216 | "This works because you cannot add a *file* to an archive…" | **fixed 2026-09-13** |
| `07_Research/FORMAT_NOTES.md` | 1518 | "Files cannot be added to a Dave archive. Therefore…" | **fixed 2026-09-13** |
| `03_Tools/scripts/test_assets.py` | 11 | "…says where it can go instead -- files cannot be added to an archive" | **fixed 2026-09-13** |

`README.md` already carried the correct statement 300 lines further down
("Adding a file to an archive works, but is not free"), which is exactly the
failure mode this register is for: the stale half is the one the code read.

**What is still true, and why the placement rule survives.** An asset can only
be instanced into an area whose own RSC0 bundle already holds that model — but
that is a fact about **RSC bundles**, not about Dave archives, and it is M18's
problem, not a hard constraint on the toolkit.

### C-002 — ".DAT archives with ASCII names cannot take a new file"
**Verdict: VERIFIED-FALSE** (of the format) / true only of today's code.

**Probe:** `probe_dat_add.py`, pass A.

`BANKS.DAT` and `STREAMS.DAT` are the two archives that refuse, and the reason
`can_add` gives is *"only the 'Dave' packed-name variant is supported"*. Both
are the uppercase `DAVE` variant with plain NUL-terminated ASCII names. Both
have room: **74** spare TOC records and 606 spare name bytes in BANKS.DAT,
**87** and 1,948 in STREAMS.DAT. Nothing about the container forbids the add —
`dave.py` has `_decode_ascii_names` but no ASCII encoder, so `add()` has no way
to write the name. That is a missing twenty lines, not a limit.

**Action:** recorded, not yet implemented; it is not on the critical path for
M3. Whoever needs it (M13 wants BANKS/STREAMS for audio) should write
`encode_ascii_name` and extend `can_add`/`add`.

### C-003 — "Place Files cannot overwrite `.mod` and `.tex`"
**Verdict: VERIFIED-FALSE.** the tester's M1 report, taken as a format claim.

**Probe:** `03_Tools/scripts/probe_place_fit.py`.

All **127,365** TOC entries in all 66 archives resolve through `Dave.find()`
— zero failures. `dave.patch` has no extension branch: replacing every entry
of `LVLS_M2/INT2.DAT` with incompressible data of its own length succeeded 700
times and was refused 19 times for want of slot, across seven extensions.
Putting a *different real file of the same kind* over an entry succeeds for
**62% of `.tex`** and **45% of `.mod`** in SWAT.DAT with no growth at all.
Allowing growth lifts `.csv` to 100%, `.actb` to 100% and `.enm` to 98% — and
`.tex` not one point, `.mod` to 62%. That gap is the whole of the tester's
report.

All figures here are read off `01_Vanilla`. The working copy flatters them:
level-9 recompression shrinks files, so its SWAT.DAT free run has grown to
6,425 bytes. A capability measured on an edited disc is not a capability of
the format.

What is true is narrower and worth keeping: `.mod` and `.tex` replacements are
the ones routinely too big for both the slot and the archive's free run
(2,047 bytes in SWAT.DAT on the pristine disc, 1,994 in a level archive), so
the grow path rescues them far less often than it rescues everything else — on
vanilla it does not rescue `.tex` at all. The user-facing bug is in
`file_drop`'s add half — see `05_Docs/LANE-REQUESTS.md` REQ-001.

### C-004 — "4,180 files have zero headroom and cannot grow by a single byte"
**Verdict: the number is VERIFIED-TRUE, the conclusion is VERIFIED-FALSE.**

**Probe:** `probe_dat_add.py` measures the same population; recount below is
from the same read-only pass over all 66 archives, 2026-09-13.

Exactly **4,180** of 127,365 entries have zero or negative slot headroom — the
README's figure is right to the file. But "cannot grow by a single byte" does
not follow: `Dave.patch(grow=True)` relocates a payload into the archive's
largest free run, and **1,972 of those 4,180 (47%)** are small enough to fit
their own archive's free run on the pristine disc. Repacking raises that further — SWAT.DAT
alone frees 12.95 MB with `logo01.pck` pinned.

**Where:** `README.md:343`, `07_Research/FORMAT_NOTES.md:62`. Both are being
reworded to state the measurement and drop the conclusion.

### C-005 — "SWAT.DAT is the one archive that cannot be repacked"
**Verdict: VERIFIED-FALSE as worded.**

`03_Tools/rdrstudio/dave.py:305` opens with *"SWAT.DAT is the one that fails"*,
and the comment block twenty lines below it (`dave.py:320`) records the
opposite: with `logo01.pck` pinned to a sector boundary the same flat-16
repack plays fine and reclaims **12.95 MB**. `repack_status("SWAT.DAT")`
already returns `needs-pinning`, not `breaks-the-game`. `FORMAT_NOTES.md:4164`
states it correctly in the past tense.

The first paragraph is the stale half and is the one a reader hits first.
**Fixed 2026-09-13.** The caveat that the bisect only found the *highest*
offending block stays — that is a real, stated limit on the evidence, not a
capability claim.

### C-006 — "Adding is effectively impossible" (`.til` / `.trn` / `.aisp`)
**Verdict: VERIFIED-FALSE as worded.** `07_Research/FORMAT_NOTES.md:1741`.

The measurement behind it is right — `lvl_train.til` has 8 spare bytes,
`lvl_train.trn` 3, `lvl_train.aisp` 21 of 2,784 — but the archives those files
live in have free runs of **2,035 / 1,994 / 1,994 / 2,027** bytes
(TRAIN, TRAINCAR, TRAINEND, TRAINGOO), and 22 of TRAIN's 24 zero-headroom
files fit theirs. The sentence should say "these files have no headroom of
their own", which is what was measured, not "adding is impossible".

### C-007 — "`pause.swui` is read-only"
**Verdict: VERIFIED-TRUE**, on recorded boot evidence: renaming a button in it
freezes story-level loading (`README.md:495`). Not re-run by this sweep — a
boot test needs the emulator and the Windows suite. Kept, with the evidence
named in the text.

### C-008 — "Control flow in `.actb` / `.stateb` is not authorable"
**Verdict: UNTESTED.** `ROADMAP.md:231`, `actb.py:21`, `behaviour.py:31`.

The structure parses and float parameters round-trip. Nobody has tried to
author control flow and boot it. M10 (`.tbl`/`.stateb` tab) and M20 (spawn
point) both touch this, and neither may quote it as a settled limit.

Note `behaviour.py:312` already exposes *"Why `name.tbl` can or cannot be
added to this archive"* — so adding behaviour FILES is not in question here;
only authoring new control flow inside one is.

### C-009 — "There is no `.stm` writer, so this codec cannot be exported"
**Verdict: VERIFIED-FALSE**, settled by M13 on 2026-09-14. Was `app.js:11298`
and `:8486`; the text has since moved to **`app.js:11663`** (comment) and
**`:11666`** (UI string).

This entry said "M13 settles it", and M13 has landed: `stm.encode` exists
(`stm.py:444`), `[M13]` is registered and drives a real `stream_import` route
(`studio.py:5397`). Checked first-hand rather than inferred from the commits —
encoding a 4,800-sample 24 kHz tone gives 4,480 bytes which `stm.decode` reads
back as 24 kHz mono, mean absolute error 75 against a peak amplitude of 12,000
(0.6%), which is what 4-bit IMA ADPCM costs.

**Two halves, and only one of them is retired — the distinction is the whole
entry.** `app.js:11663` says *"there is no .stm writer"*: false, there is one,
and that phrase is retired. `app.js:11666` says *"Stream import is not
available yet"*: **still TRUE**, because `grep stream_import web/` returns
nothing — the route exists and no UI calls it. Retiring that one would be
punishing a string for being accurate.

This is the same shape as REQ-009's `.msd`/`.skel` routes: the API landed and
the app could not reach it, and the honest statement was "delivered, not
reachable". The UI string becomes false the moment rdr-app wires the route, and
whoever wires it should retire it in the same commit.

### C-010 — "111 minutes of finished audio cannot play — the game has no way to ask for it"
**Verdict: VERIFIED-FALSE**, settled by disassembly on 2026-09-14.
`scripts/probe_stream_lookup.py`. Was `rdrstudio/streams.py:57`, and again in
`scripts/verify_streams.py:327`; both corrected.

The measurement underneath it is right and stands: **992 streams (111 minutes)
are named by nothing on the disc** — all 47,115 SWAT.DAT entries scanned, not
just the CSVs. What was wrong is the conclusion drawn from it. "Nothing names
it" was read as "the engine cannot reach it", which was an inference from the
data, never a reading of the code. The executable has been unpacked since
`elfpack.py` landed, so it was answerable all along.

**What the code does.** Unpack `SLUS_205.00` and the streamer is at
`0x0027C000`–`0x0027E400`:

    0x0027C6D4  sprintf(buf, "%s.stm", name)      "%s.stm" at 0x00521EF1
    0x0027C710  jal 0x0048F1C0                    open the stream
    0x0048F270  jal 0x0048F078                    resolve name -> entry
    0x0048F0A0  jal 0x002CB908                    find it in the TOC
    0x002CB998  jal 0x0025EEB8   a3 = 16          bsearch, 16-byte records
                comparator 0x002CAA50 -> 0x002CA9D8

`0x0048F078` then reads the winning record at `+4` (offset) and `+12` (stored
size) — exactly the DAVE TOC layout `dave.py` already parses. The comparator
at `0x002CA9D8` is a **case-insensitive `strcmp`** that also folds `\` to `/`.

So the lookup key is the NAME, searched against the archive's own table of
contents. There is no manifest of playable streams, no index list, no
allow-list — nothing an orphan could be absent from. Every one of the 8,361
`.stm` has a TOC record carrying its name.

**Evidence.** The probe pins all 15 instruction words it reads, so it fails
loudly if the executable is not the one that was disassembled; asserts the TOC
is sorted under the comparator as disassembled (bsearch's precondition, and so
a testable prediction of it); then reimplements that bsearch and runs it for
every stream: **8,361 of 8,361 resolve to their own record, including 992 of
992 orphans**, offset and stored size matching. Controls: a name not on the
disc misses; an orphan asked for in UPPER CASE still resolves, and misses
without the fold; reversing the table loses 400 of the first 400.

**What to say instead.** *Nothing in the shipped data asks for them.* That is
the modding-relevant statement and it is the one the measurement supports.
Naming an orphan in a sound bank is all it would take, and that is an edit
this toolkit already makes — which moves 111 minutes of finished audio from
"dead weight" to "content waiting for a line in a CSV". Relevant to **M12**
(`.csv` editing) and **M13** (audio).

One measured detail worth keeping: every shipped `.stm` name is already lower
case, so the comparator's case-fold never fires on the disc as it stands. It
matters for what a modder writes, not for what shipped.

### C-011 — "Cannot create a new menu screen or navigable button"
**Verdict: UNTESTED.** `README.md:493`. The stated reason — widgets are looked
up by name from code compiled into the ELF — is plausible and unverified.
**M17** (`Front.dat` in the level editor) is the first milestone that will
push on it.

### C-012 — "Vertex counts are fixed: you cannot add, delete or merge vertices"
**Verdict: UNTESTED.** `README.md` "Known limits". The stated reason is that
retopology means re-serialising the VIF packet, which changes its length and
can overflow a fixed archive slot — but C-004 shows the grow path and the
repack path both create room, so the reason no longer holds on its own. Owned
by **rdr-3d**; recorded here so it is not inherited as settled. See
`05_Docs/LANE-REQUESTS.md`.

**Update 2026-11-05 — level geometry: the writer exists (`levelvif.py`).**
Verdict stays **UNTESTED** as a whole, because the half that matters to a
player — *does the console draw it* — has not been settled. What is now proven
(`scripts/test_levelvif.py`, reads vanilla only):

* The level MOD0 container rebuilds **byte-identically** for 11,379 / 11,379
  rigid containers on the disc (header, part tables, material blocks, packet
  placement).
* The packet writer, given the rows of a shipped packet, reproduces it
  **byte-for-byte for 20,540 / 20,540** packets of the 3-stream grammar (453
  other packets use colour forms this writer does not emit and were skipped).
  Two choices the original tool made freely are inputs to that claim, not
  derived: the trailing NOP pad (0 or 2 words — header word B counts it, and
  the shipped choice flips with it on ~15% of batches) and the first header
  word (322 on all but 74 batches). The writer's own default for the pad is the
  majority rule (2 for even N, else 0).
* An OBJ with a **different vertex count** (4 → 520 vertices, among 36 imports
  into real models) replaces a level model, the bundle still passes
  `check_bundle`, tags/order and every other chunk are unchanged, every part's
  texture reference resolves to a TEX0, and `Rsc.model()` decodes the new
  triangles back to exactly the OBJ's (float32-exact).

NOT proven: that the VU1 microprogram accepts what is written. The part
record's index count (`int(1.5N)+11` per batch, within 1 of shipped on
single-batch parts) and the cull sphere (bbox centre, radius ×1.1 — shipped
`w` is often much larger) are derived, not decoded. **Collision is not touched**
(a new wall is not solid; an old one still blocks). Size is bounded by the
archive slot (level archives have ~2 KB spare in total), so a custom model
fits only if it compresses into the space of the model it replaces.

**Emulator result, 2026-11-05 (PCSX2, `scripts/test_custom_model_boot.py`).**
A disc with six of lvl_perfnew's rigid models near the player spawn replaced by
a 36-vertex box loaded, played the opening cutscene and gameplay normally for
~60 s, and showed NO crash or hang. It also showed NO box and no change to the
scene — the same frame as vanilla — so those chunks are evidently not what that
view draws (or the console culled the packets); **"it draws in-game" remains
UNCONFIRMED**, and "it does not crash the level load" is the only in-game claim
this supports. Next step: replace a chunk known to be on screen (e.g. find the
chunk by bisecting replacements) and compare against a `--control` run.

**VERIFIED IN-GAME 2026-11-05 (PCSX2): a custom level model DRAWS.** Disc with
every rigid MOD0 chunk of all 61 `LVLS_SNG` level archives replaced by one 36-vertex
OBJ box (`scripts/test_custom_model_boot.py --all-levels`, box at each level's first
spawn, half-size 6). New game -> first mission loaded, and the whole scene
(buildings, fence, ground) is gone, replaced by the textured box with the HUD
still live and no crash (`05_Docs/ingame-proof/patched_box_in_game.png`; same
point on an unmodified disc: `.../vanilla_town_same_mission.png`, taken with only
PERFNEW patched, so that level was untouched). Settled WHY the 6-chunk test showed
nothing: the menu flow had loaded a different mission (the town), not PERFNEW,
so nothing replaced was on screen -- the writer was never the problem.
Caveats: all chunks of the level were replaced (not one targeted chunk), collision
is untouched, and which archive the town is was not isolated. Menu timing drifts with
laptop load (title screen 108-250 s after launch), so `play_test.py` now reads
`RDR_TITLE_AT`; `scripts/ingame_boot.cmd` runs it in the developer's interactive session
(`schtasks /it`) -- PCSX2 launched from WSL lands in session 0 and has no window.

**Update 2026-11-05 (collision) — model and collision are ONE thing in the Studio.**
The developer: "collision and the model treated separate is cluttered ... moving any
model should also address collision." What exists now, and how far each part is
proven:

- *Linked move (vanilla models):* a `zone_X` model and its `bound_X` shell are
  paired by name stem (1,470 pairs over all 61 levels, median IoU 0.99999) and move
  together server-side (node moves AND the Shape/geom_edit move, one write, one
  undo). Offline + live-HTTP proven (`test_model_collision`, `test_custom_collision_live`).
  Models with no single shell (shared `bound_bound_grp_*`, props without a
  `bound_` twin) get one plain line in their panel saying so. Not boot-tested for
  a moved vanilla prop.
- *Solid / walk-through switch:* one two-button "Collision" block in each linked
  model's panel (default Solid; flips the player/mover/enemy/horse flags, leaves
  camera/bullets alone). The standalone collision pick/checkbox is folded in:
  clicking a shell selects its model. HTTP-proven (flag flips + one undo);
  **walk-through side NOT boot-tested.**
- *Custom-mesh collision:* importing a custom model now fits a solid box to it
  (`custcoll.py`; template = stock SHER70 chunk 83, a 5-face box, identity
  self-test reproduces it to float noise). A model with no twin gets a NEW
  `BND0` chunk (`rsc.append_chunk`) plus a new `bound_<stem>_01` `.bnds` node;
  a model that has a twin gets the twin's chunk refitted. If there is no room
  (SHER70's `.bnds` had 24 spare bytes after this) the import still succeeds and
  the UI says "no collision added: no room" rather than faking it. It is a box,
  not the mesh outline.
- *In-game proof (PCSX2, SHER70 town, full ledger flow):* **the generated chunk
  is honoured by the game's collision** -- with an oversized 6 m solid block placed
  in front of the cactus the player stops dead and 3 consecutive frames with W held
  are identical (`ingame-proof/collision_wall_blocked.png`), while the same inputs on
  a build WITHOUT the chunk keep walking (`collision_control_no_collision.png`).
  **The cactus-fitted trunk box is PROVEN too (2026-11-12, teleport + position trace rig).** On
  `RDR_CACTUS_COLL.iso` (box x -18.24..-16.34, z 34.47..36.37) the player, put 3.5 m short and walked at it,
  stops at x = -15.881 and stays there (`ingame-proof/custom_box_fitted_blocked.png`,
  `custom_box_fitted_trace_stop.txt`). Held longer at the camera heading (not square to the face) he stops at the
  same x, slides along the face from z 36.08 to 34.33 and walks on once past the corner
  (`custom_box_fitted_trace_slide.txt`). So the four 11/5 runs that "slid past" were the game sliding him round a
  1.9 m box as it does with any stock post, not a hole in the collision.
- Test rig notes: PCSX2 only reads the pad's left stick from the SDL gamepad, so
  `W/A/S/D` were added as left-stick binds for the runs (copy kept as
  `07_Research/PCSX2.ini.with-wasd-sticks`; the live ini was restored). Builds:
  `scripts/cactus_build2.py` (env `CACT_AHEAD/SIDE/R/WALL/NOCOLL`), driver
  `cactus_live.py` (`HOLD <KEY> <secs>` added).
- Not done: a moved *vanilla* prop was not boot-tested; custom-mesh collision for
  non-level (character/weapon) models does not apply.

**Update 2026-11-05 — character display lists: the walker now tiles all of
them; a skinned WRITER does not exist.** `vifwalk.walk_packet` ends a packet
at its FLUSH instead of running on, and `vifwalk.trailer` recognises what can
follow. The 41 "failures" the walker used to report on character parts were
never VIFcodes 0x0B/0x0C: those are the byte values 11 and 12 in an ascending
index list that trails the LAST part of a rigid (bone-count 0) model. Proven
(`scripts/test_vifwalk_chars.py`, every unique `*_lods.rsc` on the disc): 683 /
683 parts tile with no gap and re-emit byte-identically; the parts the old walk
could not tile are exactly the 87 that carry a trailer; every trailer opens
with the same four-pointer table, sits after a model's last part, and none
occurs on a rigged (bone-count > 0) model — so a body mesh has no trailer to
keep consistent. What the trailer's three index lists *mean* (vertex subsets
of a batch, 56-60 entries each) is not decoded.

**Update 2026-11-05 (daylane) — skinned header DECODED, a character writer EXISTS (offline-proven, NOT game-tested).**
The four header words are decoded (`05_Docs/SKINNED-HEADER-DECODE.md`): w1 = number
of secondary-influence records, A = N+26, B = batch size in words, C = 36+ceil(7N/4),
w0/0x1116 constant; plus the VU buffer cycle, influence-block address and pad rules.
PROVEN (`scripts/test_skinvif.py`, vanilla only): `skinvif.encode_batch` rebuilds
15,361 / 15,361 narrow batches, 365 / 365 rigged containers and 420 / 420 whole part
packets byte-for-byte. `skinwrite.import_obj` then writes a custom character part
(skinning copied from the nearest original vertex, positions re-quantised
bone-local, rig-count mismatch refused) and `scripts/test_skinwrite.py` shows it
re-reads through the independent reader with the same triangles, bones and positions
(to the quantiser step), the bundle verifies, other chunks are untouched. The Studio
route `character_model_import` + the "↑ new mesh" button drive it
(`test_import_panels.py`, live, restores byte-identical). ESTIMATED, not decoded:
the part record's index count and the cull-sphere radius. **Update 2026-11-12: PROVEN in PCSX2** -- a re-striped custom mesh on the town level's player (head x1.7,
3,184 -> 4,700 points) draws, textures and animates (`ingame-proof/custom_character_bighead_*.png`,
DEEPDIVE-2026-11-11 section 5). One character and one mesh; a console and other characters are untested. Size
caveat: re-striping makes a faithful re-import ~25% bigger than the original, which
usually does not fit the archive slot, so "↑ new mesh" suits decimated/simpler
meshes; for faithful edits use "↑ obj" (same point count). Float-position batches (452)
have no encoder.

**Update 2026-11-05 — why only Sacred_Bow was importable, and what changed.**
The importer's refusal was never about binary `version: 2.12` files: `plan()`
does not read the file it replaces, and the game's own dispatch (0x3B6A50)
sends `version: 1.10` to the text reader regardless. The one guard that
blocked 13 of 14 models was "shared by more than one weapon" (ptl_lod_01 by 11,
rfl_lod_01 by 11, ...). `plan(..., allow_shared=True)` — and `allow_shared` on
the `weapon_import` route — now lets a user import over a shared model
knowingly; the plan lists exactly which weapons change. Proven
(`scripts/test_weapon_shared_ack.py`, library-only): refused by default with
all names, allowed with the flag, the slot check still applies, 7 of 7
sampled models plannable. **Update (daylane 2026-11-05):** the Weapon-model
panel is no longer hidden: Weapons tab > right panel (Choose .obj / Check /
Import, an "I know this changes every weapon" tick-box that sets
`allow_shared`, a how-to and the limits). PROVEN by `test_import_panels.py`
(live, drives the real `weapon_import` route): garbage OBJ -> message + fix and
nothing written; shared model refused until acknowledged; a good OBJ over
Sacred_Bow writes, changes the archive bytes, and is restored byte-identical.
Vanilla census (measured): 5 of the 14 weapon models are already text
`version: 1.10` (bow_lod_01, gat_lod_01, pt2_lod_01, ptl_lod_01, rfl_lod_01 —
the ones we write natively), 9 are binary version: 2.12 (bay_lod_01, bottle,
bucknife, cof_lod_01, dynamite, molotov, poison, sawedoff, shotgun01),
see the test). Text-over-binary: the EXE dispatch (0x3B6A50) reads 14 header
bytes and `strncmp`s `version: 2.1` -> binary reader; `1.10` and any non-match
-> text reader, both feeding one parser, so the header alone selects the reader
(`test_weapon_shared_ack.py` asserts our output carries the text header and the
originals are binary). That is STATIC evidence only. **UNPROVEN: that the game
actually draws a text .mod placed where a binary one was, and that the
resulting weapon looks right (materials, scale, hand grip). No PCSX2 boot of a
custom weapon has been done.** The model's texture/UV binding is the importer's
`texture="westgun"` default. A true binary 2.12 *writer* is blocked by the open
packet section (UNKNOWNS.md, REQ-050) and is not attempted.

**Update (daylane 2026-11-07, IN-GAME, PCSX2, SHER70 town):** (1) PROVEN: a weapon-model import over rfl_lod_01 (Red's Owl Rifle, shared by 11 weapons) with a plain 8-point/12-triangle box DRAWS on Red's back (05_Docs/ingame-proof/weapon_custom_box_on_reds_back.png; vanilla for comparison weapon_vanilla_rifle_for_comparison.png). The text-.mod-in-place-of-binary question is therefore ANSWERED for that case. (2) FOUND, NOT FIXED: imports with 24 points/44 triangles (cylinder) and 48 points/68 triangles (blunderbuss), both over a vanilla rifle model and over a new weapon's own model file, FREEZE the level load (loading logo forever); limit unknown, the Studio size check does not catch it; the weapon panel now says so. (3) PROVEN: New weapon with its own model file (placeholder block added to every level archive carrying the source model) boots and loads the town. (4) NOT DONE: equipping a new weapon on Red: the weapon-select screen lists Red's saved inventory only; writing the weapon table, invmasterlist.csv shop row, player/allweapons/cls/twg arsenals and levelflow.csv loadout did not make it selectable or buyable. Needs the save-inventory format. (5) PROVEN test aid: creature srd.cr MaxHitPoints 100000 + Invulnerable 1 keeps Red alive 3+ min. (6) UI: acknowledgement tick-box replaced by an amber shared-model warning, Art/Collision + Pick toolbar removed (View settings > Show collision), Animations has its own top-level tab (it was folded under MEDIA in September).

**Ease of use (daylane 2026-11-05):** the level "Import custom model" button
and the weapon panel carry a numbered how-to + plain limits in the UI,
friendly errors ("Too big ... Nothing was changed") and a success line with the
next step; one-page guide `05_Docs/HOWTO-CUSTOM-MODELS.md`.

### C-013 — "Terrain tiles cannot be placed at all"
**Verdict: VERIFIED-TRUE**, with a scope note. `FORMAT_NOTES.md:1720`.

Zero of TRAIN's 206 chunks carry a tile name and index order does not line up,
so there is no chunk→tile mapping to place against. `ROADMAP.md:292` records
that an earlier, *stronger* version of this claim ("attribution is not
recoverable") was wrong and was overturned by geometry rather than names. The
surviving claim is about placement, not attribution, and stands.

### C-014 — "Cannot add a new skeleton"
**Verdict: UNTESTED.** `ROADMAP.md:178`, in the parity table.

Never attempted. `.skel` is plain text and fully decoded (`rdrstudio/skeleton.py`),
and a `.skel` file can now be added to 64 of 66 archives (C-001) — so the two
things that would have made this hard are both gone. What is genuinely unknown
is whether the game binds a skeleton it did not ship with. Owned by **rdr-3d**;
recorded so the table is not read as settled.

### C-015 — "This rig cannot export to BVH"
**Verdict: VERIFIED-TRUE**, and the scope is tiny. `studio.py:922`,
`app.js:10571`.

Measured on `01_Vanilla`: of SWAT.DAT's **236** `.skel` files, **234** are
BVH-writable and **2** are not — `blk.skel` and `own01.skel`, both forests
(more than one root). The app already handles this the right way: an
unwritable rig stays on offer for WATCHING and only loses ties, so the Export
button is never a trap. Recorded as the pattern other claims should copy — the
limit is named, counted, and routed around rather than generalised.

### C-016 — "The AI condition vocabulary is read-only"
**Verdict: UNTESTED.** `studio.py:2831`, `index.html:902`.

`aivocab` bakes both name tables in as data, read out of SLUS_205.00. The
route is genuinely read-only and says so honestly. What nobody has tried is
whether the names can be *changed* — the executable is unpacked and readable
(`verify_elfpack.py`), so this is answerable, and "read-only" here describes a
panel rather than a format.

### C-017 — "A skinned mesh cannot be moved by patching floats"
**Verdict: VERIFIED-TRUE**, on evidence already recorded rather than re-run.
`studio.py:4395`, `studio.py:3506`.

Skinned positions are 16-bit quantised against the VIF ROW register
(`FORMAT_NOTES.md:923`), so there are no floats in the file to patch. This is
the same root fact as C-012 and the README's "placement grid" limit, and it is
narrower than it sounds: *reshaping* a character mesh works and is tested
(`verify_skinwrite.py`). What does not work is treating it like level geometry.
The route's message is accurate and names the reason, which is what a refusal
should do.

### C-018 — "These files are patched in place and cannot grow"
**Verdict: VERIFIED-FALSE.** `routes2.py:2203`, in the advice a user sees when
a character-stat edit overflows its slot.

The sentence tells the user their value *"has nowhere to go"*. It has
somewhere to go: `Dave.patch(grow=True)` relocates a payload into the
archive's free run, which is what the "allow files to grow" tick already does
for other edits. Measured on `01_Vanilla`: of the **939** files in the
`.dam .sphs .ars .hot .tail .acc .armr .rep` family, all of them in SWAT.DAT,
**747** are small enough to relocate into its 2,047-byte free run. The advice
is wrong for four files in five.

What is true is that *this route* does not pass `grow` — `_write_copies` calls
`s.write` without it. So the fix is one argument plus the tick the UI already
owns, not a format change. Filed to rdr-app; the wording must stop asserting a
property of the files.

### C-019 — "props cannot address six of the character formats"
**Verdict: VERIFIED-TRUE**, measured per format. `routes2.py:2008`,
`charfile.py:13-50`.

`charfile.py` already does what this whole register is for: it states the
claim, gives the count behind it (`.dam` 3,552 fields against 21,064 numbers;
`.sphs` 0 of 199 files carrying a block path; `.hot` 6 things per spot and
none of the twelve matrix numbers), separates the three real failures from the
three formats where `props` is fine, and ends with a **WHAT IS NOT KNOWN**
section naming `.dam`'s unnamed columns and `.ars`'s unexplained 0/1 column.
Nothing to fix. Kept here so the claim is findable from outside the module.

### C-025 — "Enemies cannot be placed here yet" (Showdown archives)
**Verdict: UNTESTED**, and it is closer than the wording suggests.
`web/cast.js:117`.

26 of the 61 level archives are Showdown archives with no `.enm` of their own;
their roster lives only in SWAT.DAT, so the Enemies layer is empty. The panel
says so and says why, which is right. But the implied fix — *give that archive
its own `.enm`* — is no longer blocked by the archive format: C-001 shows 64 of
66 archives take a new file, and `scripts/test_add_named.py` was written
around exactly this case (`LVLS_M2/BRIDGE2.DAT` ← `lvl_bridge2_var_1.enm`,
cloned from the single-player bridge roster).

What is genuinely unknown is whether the **game** reads a level-local `.enm`
it did not ship with. The boot-verified add (commit f7f657c) put a file into
SWAT.DAT, not a roster into a Showdown archive, so that is the untested half
and it is the only half left. **M11** is where this gets answered.

### C-026 — "Read-only, and it will stay that way until the action opcodes have names"
**Verdict: UNTESTED**, and the precondition is the part to doubt.
`web/logic.js:415`, `index.html:2066`.

The executable names all of the conditions a mission script can test and none
of the actions (C-008). Shipping the Scripting tab read-only on that basis is a
defensible *safety* decision, and the panel is honest about its rung. But the
sentence asserts that naming is what read-only is waiting on, and nothing has
tested that. Rebinding a `.stateb`, which is what **M10** asks for, does not
need an action's name — it needs the binding, and `behaviour.py:312` already
answers whether a `.tbl` can be added. Two different questions have been
joined by an "until".

### C-027 — "Replace… on any texture"
**Verdict: VERIFIED-FALSE as shipped**, and the gap is large.

**The claim.** `ROADMAP.md:177` listed retexture as *"yes, import and export"*
with an empty limits column, and `ROADMAP.md:210` filed 5,687 in-bundle
textures under "import and export" in the **Solid — verified, tested, used**
table. The UI offers **Replace…** on every one of them.

**Measured on `01_Vanilla`, all 1,139 RSC bundles, 5,687 in-bundle textures:**

| | count | share |
|---|---|---|
| replaceable today | **2,970** | 52.2% |
| refused — stored swizzled | 2,391 | 42.0% |
| refused — not 8bpp (16-colour) | 689 | 12.1% |
| refused — both | 363 | 6.4% |
| **refused overall** | **2,717** | **47.8%** |

Both refusals are in `texture.py`'s `encode_into`, both are deliberate, and
both say "yet": *"only 256-colour textures can be replaced for now"*
(`texture.py:591`) and *"re-swizzling on import is not implemented yet"*
(`texture.py:597`). They are the right refusals — a same-length write of
linear indices into a slot the game reads as tiled cannot damage the archive
but silently ruins the picture.

**The UI gates on the wrong property.** `app.js:6894` offers Replace when
`pal_kind === 'PAL0'` and otherwise prints *"16-colour — read only"*. Every
palette chunk on the disc is a `PAL0`: **5,687 of 5,687**. So the read-only
message never renders — it is dead code — and the button is offered on 2,717
textures the encoder will turn away. `README.md:182` gets this right ("on any
256-colour one"); the ROADMAP and the UI do not.

**Fixed 2026-09-13** in `ROADMAP.md` (both rows). The UI gate is rdr-app's —
REQ-006. The real unlock is re-swizzling on import, which would take the
replaceable share from 52% to 88% in one change; `texture.py` already
*reads* swizzled data (`is_swizzled`, and the 8bpp unswizzle at line 220), so
this is an inverse that exists, not a format that is closed.

### C-030 — "A same-width edit costs zero bytes"
**Verdict: VERIFIED-FALSE**, and it is the reasoning behind two pieces of
user-facing advice. `swt.py:13`, and the same premise under `routes2.py:2203`
(C-018).

A same-width value edit rewrites no key, no indentation and no column, so it
costs zero **uncompressed** bytes. But these entries are stored deflated, and
changing a digit changes how well the file compresses — so the number that has
to fit the slot can still go up.

**A sharper instance, 2026-09-14, from M10's rename work.** Renaming a
`.stateb` rewrites the name token in every `.tbl` that binds it. Renaming
`barfight_always` to `zz_fight_always` — **the same 15 characters** — left
`barfight_bar1.tbl` at exactly 372 bytes and moved its STORED size from 128 to
130, in a 128-byte slot. The write is impossible and the file length never
moved. Two consequences worth keeping: a planner that costs `len(new_bytes)`
against the slot says "fits" and is wrong, which is why `Dave.would_fit` was
factored out of `Dave.patch` so plan and write answer from one rule; and
across the level archives **8.3% of same-length `.stateb` renames are refused
for exactly this**, against 91.7% that go through. The rate is not a rounding
error, and nothing about the name's length predicts it.

**Measured on `01_Vanilla`**, changing one mid-file digit to a different digit
of the same width and re-deflating at level 9:

| format | files | same-width edit still fits the slot |
|---|---|---|
| `.msd` | 521 | **521** |
| `.swt` | 519 | **519** |
| `.rep` | 585 | 561 — **24 overflow**, worst by 7 bytes (`nsd.rep` in BRIDGE) |
| `.hot` | 523 | 520 — **3 overflow**, worst by 2 bytes (`nod.hot` in TRAIN) |

So the claim is true of `.swt` by luck and false of its neighbours, which is
the worst shape for a rule of thumb: it holds everywhere you first try it.
`routes2.py:2203` tells the user to *"write the number with the same digits it
already had"* — advice that does not work for those 24 `.rep` files, on top of
C-018's separate problem that the value has somewhere to go anyway.

Nothing in the shipped write path is unsafe: `routes2` dry-runs every copy and
refuses as a whole before writing. The defect is the explanation, not the code.
**Fixed 2026-09-14** in `swt.py`; `routes2.py` is rdr-app's (REQ-004).

---

## Animation (rdr-3d)

Started by the rdr-3d lane out of M2's findings and kept verbatim; the
wording, the measurements and the probes are theirs. Only the claim ids were
added, so the `[M3]` test can see them.

### C-020 — "`.anim` import only works on `cm2` clips"
**Verdict: VERIFIED-FALSE.** rdr-3d, M2.

**The claim, as the code made it.** `bvh_to_anim` called `anim.parse`, which
rejects anything without the `cm2\0` magic, and the `anim_import` route turned
that into *"NAME is not a cm2 clip"*. Meanwhile the *export* route deliberately
handled all three codecs, so the tool would hand you a BVH it would not take
back.

**Verdict: wrong, and it was 8.2% of the disc.** Counted over every `.anim`
file in `04_Extracted` (11,138 of them): **10,230 `cm2`, 666 `cmp`, 242
uncompressed**. 108 of the uncompressed ones are the accessory clips — a
character's hair, scarf, coat tail and jaw — which are exactly the clips
somebody wants to edit.

Both now import, and both re-encode **in place**, which is the property that
keeps an edit inside its archive slot:

* uncompressed — one float32 per channel per key after a 17-byte header.
  Neither count can change without changing the clip's shape, so the length is
  the same by construction; `animraw.RawAnim.pack` asserts it rather than
  trusting it.
* `cmp` — the 17-byte header, the per-track `(scale, bias, index)` records and
  the column count are copied through and only the packed 32-bit words are
  rewritten.

**Probe:** `03_Tools/scripts/verify_anim_dcc.py` (all three codecs, identity
and round trip). The `cmp` layout was checked across all 666 files on the disc
first: the size identity `17 + tracks*12 + 2 + nchan*keys*4` holds exactly on
all of them, no two channels sharing a 32-bit column share a bit slot (maximum
3 channels per column, one per slot), and re-packing a file's own decoded words
reproduces it byte for byte on the 200 sampled.

### C-021 — "A channel stored as a single constant cannot be edited"
**Verdict: VERIFIED-TRUE**, with a correction. rdr-3d, M2.

**The limit is real.** A `cm2` clip stores a channel that does not move as one
`s16` with no row in the key matrix, and a `cmp` clip stores it as a bias with
no column. Giving either of them a curve changes the file's size, which can
overflow the archive slot. That refusal stays, and `allow_reshape` is still the
documented way through it.

**What was wrong was the test for "edited".** Any difference at all counted,
including differences no human made. A BVH's three angles per joint are one
decomposition of a rotation; loading it into a tool builds a matrix and saving
writes whatever three angles decompose that matrix, so the numbers come back
through float32 having moved a little. A constant channel sitting on a
quantiser boundary then rounds one unit up on some keys and not others — two
distinct values, which the importer read as a curve. Measured: **1e-5 degrees
of injected noise already refuses 5 of 25 stock clips and 1e-2 degrees refuses
all 25.** That is why animation import failed on every clip a tester tried.

A wobble below `CONST_NOISE_DEG` (0.02°) or `CONST_NOISE_M` (0.1 mm) is now
read as the writing tool's arithmetic and the channel is kept constant, named
in the report. Both numbers are bounded at both ends, and both ends are
measured: the engine's own test for an animated channel is 33 quantiser units
(`slti v0, v0, 33` at `0x002D31D4`), which is 0.18° at the disc's median
rotation scale, and a faithful DCC round trip moves a constant channel by at
most **0.0001°** over 120 stock clips in both tool orders. 200x of headroom
below the allowance, 9x above it.

### C-022 — "Only the root may have translation channels"
**Verdict: VERIFIED-TRUE**, but it was the wrong thing to refuse on. rdr-3d, M2.

A clip stores exactly three translation channels and `3 * bones` rotations.
There is nowhere to put a translated bone, and that part is real.

But Blender's BVH exporter has a **"Root Translation Only"** checkbox, and with
it unticked every joint gets position channels as well as rotations. The
importer refused the whole file. What Blender writes there for a bone nobody
translated is the bone's own rest offset, the same number on every frame — no
motion, so no edit. Those files are now accepted, with the ignored joints named
in the report; a joint whose position actually *moves* is still refused, by
name, with the checkbox named in the message.

### C-023 — "A rotation in a stock clip is between -pi and pi"
**Verdict: VERIFIED-FALSE.** rdr-3d, M2.

Never written down anywhere, but the importer behaved as though it were true.

**2,215 of the disc's 11,137 clips store at least one rotation outside ±pi**,
the largest at 49.49 rad (2,835°). Every tool that loads and re-saves one
writes back the *principal* decomposition — the identical pose as a number a
whole turn away. Read literally that is an enormous edit: it saturates the
quantiser rail, or lands on a constant channel and gets the file refused. The
importer now slides each joint to the equivalent description nearest what the
clip already stores, which changes no pose and leaves an unedited joint's
numbers untouched. **Probe:** `verify_anim_dcc.py`, section 2b.

### C-028 — "A `cmp` track can only hold values inside the range it shipped with"
**Verdict: VERIFIED-FALSE.** rdr-3d, M2.

Never written down, and the first `cmp` importer behaved as though it were
true, which made the codec technically editable and practically useless.

A `cmp` track carries a `(scale, bias)` pair fitted tight around the curve it
shipped with — measured across the disc, the stock span is the **full** field,
2047 of 2047 units. So a track covering 12.7° has a 12.7° window, and a 3° edit
falls straight out of it and clamps against the rail: the numbers read back
wrong while the file looks fine. That is the silent kind of wrong.

But both numbers live in the track's own **12-byte record**, and that record is
a fixed size — so widening the window changes nothing about the file's length,
and the record belongs to one track, so the up-to-two other channels sharing
its 32-bit column are untouched. The importer now retunes a track's window when
an edit needs it, and an edit lands exactly.
**Probe:** `verify_anim_dcc.py`, "a real edit lands and reads back".

### C-029 — How much of the disc survives an animation round trip
**Verdict: VERIFIED-TRUE**, and it is the summary figure for M2. rdr-3d.

Every `.anim` on the disc, exported to BVH against a matching rig and imported
back unedited, must come back byte-for-byte:

| | before M2 | after |
|---|---|---|
| round-trips byte-identically | 10,216 (91.7%) | **11,063 (99.3%)** |
| refused outright | 908 (`cmp` + uncompressed) | 0 |
| written at the wrong size | 0 | 0 |

The 74 that remain are clips whose channel count matches no skeleton on the
disc — 57 of them uncompressed camera and face tracks, which have no bone
layout to rig against and are correctly not offered a BVH at all. That is a
separate gap and it is **UNTESTED** whether the other 17 have a rig somewhere
that the bone-count index is missing.

### C-031 — "The animation player has to guess which bone a hat hangs from"
**Verdict: VERIFIED-FALSE.** rdr-3d, M5.

Never written as a sentence; the code simply did it. `studio.head_bone` and
`head_bone_index` pick the bone named `head`, else the highest one, and the
viewer hangs every rigid part of a character on it.

**The disc answers this itself.** Every character ships a `.swt` and three of
its 548 fields are about the hat. Measured over all 183 distinct `.swt`:

| field | values |
|---|---|
| `Hat` | `1` on 131, `0` on **52** — 28% of the cast wear none |
| `HatBone` | `head` 175, `spine_02` 4, `hat01` 2, `wrist_r` 2 |
| `HatOffset` | `0 0 0` on all 183 — nothing to apply, ever |

So it is not a `head`/`hat01` choice: four bones are in use. `spine_02` is the
hat slung on the back (`np3`, `twg`, `tw7`, `mtwg`), `wrist_r` is `own01` and
`own07` carrying it in the hand, and `hat01` exists in exactly 2 of the disc's
236 skeletons — both of them characters that name it.

**And the field is trustworthy:** every `HatBone` named by a character whose
skeleton is on the disc exists in that character's own rig, 124 of 124, no
misses. That is what makes it a default rather than a hint.

**What was wrong.** Of the 84 rigged bundles that carry a hat mesh, **8 were
placed wrongly**: 4 on the wrong bone and 5 drawn at all for a character whose
`.swt` says they wear none (`twg` is both). 19 have no `.swt` of their own —
`chr`, the player, among them — and for those the old rule is kept and the
panel is told it is guessing.

**Probe:** `03_Tools/scripts/verify_hatbone.py`. It builds the merged mesh
both ways and asserts the hat's vertices move by exactly the distance between
the two bones while the face and body do not move at all, and that a
`Hat: 0` character comes back with exactly the hat's vertices and triangles
missing.

### C-032 — "`HatOffset` has to be applied for a hat to sit right"
**Verdict: VERIFIED-TRUE that the field exists, VERIFIED-FALSE that it
matters.** rdr-3d, M5.

`SwatCreatureType/HatOffset` is in all 183 `.swt` and reads `0.000000
0.000000 0.000000` in every one of them — 173 space-separated, 10
tab-separated, which is the only thing about it worth coding around.
`HatDeathOffsetY` is non-zero on 7 characters but applies to a dropped hat on
death, not to the player's preview.

Recorded so the next person does not implement an offset that is always zero,
and so that if a modded `.swt` ever carries a non-zero one it is a known gap
rather than a discovery. `hatbone.HatSpec` parses and reports it; nothing
applies it.

### C-024 — `.pss` video: nothing is claimed either way
**Verdict: UNTESTED.** rdr-3d, M19.

M19. Nobody has decoded a frame yet. No claim either way.

---

## Claims held by other lanes

These are capability claims in files rdr-fmt does not own. They are registered
here because M3 is a project-wide sweep; the wording is the owning lane's to
change. Filed in `05_Docs/LANE-REQUESTS.md`.

| ID | Claim | File (owner) | Verdict |
|---|---|---|---|
| C-003 | Place Files cannot overwrite `.mod`/`.tex` | `web/app.js`, `studio.py` (rdr-app) | VERIFIED-FALSE |
| C-009 | no `.stm` writer | `web/app.js:11298`, `:8486` (rdr-app) | UNTESTED |
| C-012 | vertex counts are fixed | `README.md`, `export.py` (rdr-3d) | UNTESTED |
| C-014 | cannot add a new skeleton | `ROADMAP.md:178` (rdr-3d) | UNTESTED |
| — | "Read-only here. Edit the file as text." | `web/app.js:4254-4257` (rdr-app) | true of the UI, not of the format — reword to say so |
| — | AI conditions / Scripting tabs "Read-only" | `web/index.html:902,909,2066` (rdr-app) | true of the UI, not of the format |
| C-018 | "these files are patched in place and cannot grow" | `routes2.py:2203` (rdr-app) | VERIFIED-FALSE |
| C-020 | "%s is not a cm2 clip" as the catch-all import error | `studio.py:4656`, `transfer.py:386` (rdr-app) | names a cause that is no longer the requirement |
| C-027 | Replace… offered on 2,717 textures the encoder refuses; the read-only branch is dead code | `web/app.js:6894` (rdr-app) | VERIFIED-FALSE |
| C-025 | "enemies cannot be placed here yet" | `web/cast.js:117` (rdr-app) | UNTESTED — M11 answers it |
| C-026 | "read-only until the action opcodes have names" | `web/logic.js:415` (rdr-app) | UNTESTED precondition |

---

## Sweep status

The harvester returns 572 candidate lines after the guard-rail filter. Read
line by line so far: `README.md`, `ROADMAP.md`, `07_Research/FORMAT_NOTES.md`,
the format modules, **`studio.py` (37 candidates)** and **`routes2.py` (31)**.

`routes2.py` is almost entirely `Refusal(...)` text — runtime guard-rails
about the file in front of the user, not claims about the formats — which is
the right shape for that layer. Two were claims and both are registered
(C-018, C-019). `studio.py` yielded three (C-015, C-016, C-017). Dismissed
with the reason, so the decision is on the record rather than implied:

| Line | Text | Why it is not a capability claim |
|---|---|---|
| `studio.py:3423` | "an OBJ-only export cannot say which meshes are art and which are collision" | a property of the OBJ format, not of this disc — and GLB ships alongside it for exactly this reason |
| `studio.py:4316` | "a yaw-and-scale cannot describe an arbitrary reshape" | arithmetic: a 7-DOF transform cannot express an arbitrary one. The route reports the residual instead of pretending |
| `studio.py:4335` | "makes 'put it back' impossible" | why the code derives from stock rather than composing deltas — float drift, not a format limit |
| `studio.py:1500`, `:1563` | "a shorter run cannot…", "a clean log here cannot tell…" | statements about the boot test's power, and correct ones |
| `routes2.py:879` | `rung="untested"` | not a claim — the opposite. The route ships the word "untested" on the row rather than implying the numbers are understood. Copy this |

**`web/` swept 2026-09-13** — `app.js` (68 candidates), `index.html` (13), and
the nine smaller modules the harvester reaches: `anim3d.js`, `arcadd.js`,
`attrs.js`, `cast.js`, `csvedit.js`, `logic.js`, `menus.js`, `nav.js`,
`rig.js`, `states.js`, `TransformControls.js`. Three became entries (C-025,
C-026, C-027). The rest split into two groups, and the split is the finding:

*Implementation comments* — the large majority. `app.js` in particular
narrates why a branch exists ("the two eyes cannot overwrite each other", "a
stray angle bracket cannot inject markup", "float deltas drift, which makes
put-it-back impossible"). These are about the code in front of them, not about
the disc, and several are *corrections already made* — `app.js:1839` ("this
was previously recorded as impossible"), `app.js:2463`/`:2493` ("which is why
moving collision FELT impossible"), `app.js:6251` and `app.js:8320` ("no
longer opaque"), `attrs.js:1-13` (three things "the toolkit already parsed and
could not change", in a file that exists to change them). That is the habit
this milestone wants and it is already common here.

*Scoped user-facing strings* — the good pattern, kept as examples rather than
findings, because each names the boundary instead of generalising:

| Where | String | Why it is right |
|---|---|---|
| `app.js:7069` | "Other fields (read-only here)" | "here" scopes it to the panel |
| `app.js:10571` | "This rig cannot export to BVH. Pick another to export." | true of 2 of 236 rigs (C-015), and it offers the way out |
| `app.js:11298` | "Stream import is not available yet." | accurate, and "yet" is doing real work |
| `cast.js:204`, `:598` | "This build cannot write that field yet." | says *this build*, not the format |
| `menus.js:5` | "3 cannot be read at all (two are zero bytes, one is UTF-16)" | counted, with the reason |
| `menus.js:152` | "This file cannot be edited here." + `cur.why` | refusal carries its own reason |
| `arcadd.js:47`, `states.js:518` | "Adds cannot be undone." | true — an add is not journalled |

Runtime guard-rails (`'Cannot reach the Studio.'`, `"cannot launch until it is
handed over"`, every `Refusal(...)` in `routes2.py`) are statements about the
machine right now and are not claims. **The sweep is complete.**

---

## Register — the disc survey (rdr-re)

Lane 4, added 2026-09-14. Every entry here is settled by a probe in
`03_Tools/research/` that reproduces over the whole corpus, not one file. The
probes are read-only against `01_Vanilla/RDR_VANILLA.iso` and take no lock.

**One honest note before the entries.** rdr-re went looking for inherited
"impossible" claims about the file formats and found almost none left in copy —
`07_Research/FORMAT_NOTES.md` carries no `cannot` / `unknown format` /
`not decoded` line at all, and rdr-fmt's M3 sweep has already taken the ones
that were in the code. The three entries below are what the disc survey turned
up that the register did not already hold.

### C-033 — "101 of the 126 file extensions on this disc are plain ASCII … this handles the rest"
**Verdict: VERIFIED-FALSE**, on both halves. `03_Tools/rdrstudio/props.py`,
lines 3-7.

**Probes:** `03_Tools/research/probe_textform.py` (the count),
`03_Tools/research/probe_props_gap.py` (the coverage).

**The count.** Walking every entry of all 66 archives: the disc has **139**
distinct extensions inside the archives (145 counting the six loose ones), and
**109** of them are pure ASCII — every file of that type, not most — covering
56,507 entries, 44.4% of the disc. A further 8 extensions are MIXED. So the
shape of the claim is right and its numbers are not, which matters because
"126" is the denominator anyone reaches for when asking how much of this disc
is still unread.

**The coverage.** "This handles the rest" is the load-bearing half, and it is
false for 18 extensions. `props.py` tracks blocks with two regexes that require
the brace to own its line:

    _OPEN  = ^[ \t]*([A-Za-z_]\w*)?[ \t]*\{[ \t]*$
    _CLOSE = ^[ \t]*\}[ \t]*$

The disc uses two spellings neither covers — `Node 0 {` (a block head with an
argument before the brace) and ` 1 }` (a `}` sharing its line with data). The
first is recorded as a **field** named `Node` whose value is `0 \t{`.

Measured over every ASCII entry on the disc:

- **11,555 files offer 270,750 editable fields whose value still contains the
  block's opening brace.** Editing one through the normal path overwrites the
  `{`. Demonstrated on three files:

      blk.skel   field 'bone' = 'root {'   ->  apply({'bone': '99'})
      line 3  BEFORE  b'bone root {'
      line 3  AFTER   b'bone 99'
      open braces 62 -> 61,  brace balance 0 -> -1

- Worst affected: `.nav` 71,844 such fields, `.bxes` 48,244, `.lnes` 42,684,
  `.skel` 33,557, `.znes` 24,802, `.gait` 21,160.
- Line coverage where the misses bite: `.gait` **14.9%**, `.skel` **30.4%**,
  `.znes` **53.2%**, `.bxes` **57.8%** of content lines.
- `props.apply` is reachable from live routes, `field_bulk_set` among them,
  which applies one edit across many files in a batch.

**What it is not.** Not a round-trip failure: `apply(data, {})` is byte-perfect
on all 56,399 files, which is precisely why it was never noticed. This is the
third instance of the failure mode `props.py`'s own comments describe twice
already — the CRLF `_CLOSE` miss and the dotted-key miss. And it is not a bug
in `.skel` editing: `skeledit.py` goes through `skeleton.py`, not `props.py`.
The exposure is the generic Files tab.

**Action:** `props.py` is rdr-fmt's. Filed as REQ-017 with the probe.

### C-034 — `.pup` is a binary blob nothing on this disc reads
**Verdict: VERIFIED-FALSE.** 1,200 files, decoded, whole-corpus.

**Probe:** `03_Tools/research/probe_pup.py`.

`.pup` ("pupshow" — the engine's word for a scripted cutscene) was the largest
file type on the disc that **no module in `03_Tools/rdrstudio/` mentioned at
all**. It is a cutscene cast list plus a track timeline:

    u32     count                  actors cast in this cutscene
    count x { char[] name\0 ; u32 role }
    u32     nframes
    nframes x { u32 time ; u8 state[count] }

**1,200 of 1,200 parse to EOF exactly, 0 fail.** The parse is required to land
on the final byte — no slack, no overrun — so the `state[count]` tie between
the roster and the timeline is proven rather than assumed. Corroboration from
the role codes: `0x07` is attached to the name `camera` and to nothing else, in
every one of the 1,200 files; `0x0f`/`0x10` carry 1,475 of 1,487 names ending
`Facial`.

**That inference was WRONG, and is now disproved (2026-09-14).** I wrote that
role codes at `0x7b` and above are *"inferred, not proven"* to be creature-type
ids, because they sit beside the same three-letter stems `.cr` uses for
`FileNameRender`. Labelling it a guess was right; the guess was wrong.

`probe_pup_channels.py` settles it. Fit `N = Σ w[role]`, where N is the
per-frame float count of the matching `.iani`, over **352 files and 19 role
codes**: **352 of 352 exact hits, max error 0.0**, every weight an integer —
and each integer equal to its own role code.

    0x07 -> 7    0x0f -> 15   0x10 -> 16   0x9f -> 159   0xb4 -> 180  ...

**The role code IS the channel count**, for all 19. `camera` is 0x07 because a
camera contributes 7 channels; a full character rig is 0x9f because it
contributes 159. 17 of the 19 are exact multiples of 3, consistent with three
channels per bone (159 = 53x3, 180 = 60x3); the exceptions are the camera and
0x10. It was never an id — the character names beside it are what made the
wrong reading plausible.

**Where the false text is:** nowhere. Like C-023 and C-028 this was never
written down — it was the operative assumption, and 1,200 files went unread on
it. `-` in the retired-text block.

**Action:** filed as REQ-018 (rdr-fmt) for a reader module.

### C-035 — A `.skel`'s `NumBones` header matches the number of bone blocks
**Verdict: VERIFIED-FALSE**, in the shipped game data.

**Probe:** `03_Tools/research/probe_textgrammar.py`.

Across all 1,128 `.skel` files, **9 declare a bone count that does not match
their body.** `blk.skel` (LVLS_SNG/CEMETERY.DAT) says `NumBones 52` and holds
62 `bone ... {` blocks; `own01.skel` says 52 and holds 53. Confirmed
independently of the probe:

    grep -cE '^[[:space:]]*bone[[:space:]]' blk.skel  ->  62

This is the only place on the whole disc where the counted-record convention
breaks, and it is worth knowing because the convention otherwise holds hard:
25 extensions honour a `Num<Thing> N` header with zero exceptions across 11,924
headers, with exactly one other mismatch (`test.dof`, a file named `test`).

**`skeleton.py` already gets this right** and deserves the credit here rather
than a request: it scans for bone blocks and keeps `declared` as a separate
value, to the point of printing *"NumBones %d (%d bone blocks found)"*. Nothing
needs fixing. The entry exists so that the next parser written against `.skel`
does not size a buffer from the header.

**Open, and not settleable by probe:** whether the *game* reads the header or
the body. That needs a boot test.


### C-036 — "nothing short of disassembling that ELF will name them" (the `.actb` opcodes)
**Verdict: VERIFIED-FALSE** as stated. Ships in
`03_Tools/rdrstudio/actb.py:19-20`, `README.md:589` and `ROADMAP.md:231`.

**Probe:** `03_Tools/research/probe_actb.py`, over all 20,494 `.actb` /
`.stateb` / `.acttbl` files that carry the `version` magic.

Five opcodes are named from the corpus alone, no disassembly. Every inline
string in these files is preceded by a 32-bit word; score those words against
namespaces built from **independent** files and the meanings fall out:

| Opcode | Uses | Argument resolves to | Reading |
|---|---:|---|---|
| **1005** | 40,427 | **`.actb` 89.7% + `.acttbl` 9.8%** of uses | run this action table |
| **1004** | 42,691 | a declared label, **99.4%** (a file only 1.2%) | go to this state |
| **1003** | 1,581 | `.tbl` names, 99.1% | use this table |
| **1566** | 943 | `.pup` cutscene names, **100%** | play this facial show |
| **1522** | 341 | `.pup` cutscene names, 96.8% | play this cutscene |

The namespaces — `.tbl` state values, `.flags` keys, `.pup` names, every file
stem on the disc — come from files that are not `.actb`, so none of this is the
corpus confirming itself.

**Someone got to part of this first, and it is only fair to say so.**
`07_Research/MISSION_LOGIC.md:900` already records that the operand vocabulary
"is a plain string table at `0x005270A3` / `0x00528000` / `0x004F9568` and can
be shipped as a lookup **today**". That is the same table
`probe_elf_keywords.py` found from the other end — its two tables start at file
offsets 0x3f9564 and 0x3f9ba4 in the unpacked image, which load at **0x4F9568**
and 0x4F9BA8, and 0x5270A3 is where the string `angle` sits. I did not read
that file before probing, so this is independent rediscovery rather than
original work. What the probe adds is the extracted 248-name list
(`05_Docs/RESEARCH/ai-vocabulary.txt`) and the negative result that the u32
paired with each name is **not** the code the `.actb` files carry -- six
readings tested over 49,105 keyword occurrences, best 2.0% against a ~1% floor.

**The control matters more than the percentages.** 1004's argument is quoted
and matches no external namespace, so it was scored against bare identifiers
declared inside the corpus. That is *not* independent evidence, so 1005 was run
through the same test as a control: **1004 scores 99.4% where 1005 scores
4.2%.** A 24× separation against a known-different opcode is what makes 1004 a
label reference rather than a coincidence.

An earlier cut of this probe scored opcodes against every bare identifier found
in the `.actb` files themselves and returned 100% for opcodes that merely name
variables. A set that contains the answer cannot test it; the namespaces above
were rebuilt from independent files for exactly that reason.

**What is still true, and must not be overstated.** Most opcodes remain
unnamed — the survey reports 7 with a confident reading out of dozens seen
above the reporting threshold, and the low-numbered words (0, 2, 5, 11, 17)
are operand/type tags whose meaning this probe does not settle. The claim is
false as an absolute ("nothing short of"), not because the format is now fully
understood.

**And the second half of the original claim is false too — in the other
direction.** `07_Research/MISSION_LOGIC.md` §1.7 establishes by exhaustive
search that a disassembly would **not** name the action opcodes either: every
4-byte alignment at strides 4, 8, 12 and 16 was scanned across all 4,497,384
bytes for `{char*, small-int}` records, exactly **four** such tables exist, and
**none holds any value in the 1001..1571 action range**. The string `nofire`
appears 273 times in shipped `.actb`/`.stateb` data and **zero times** in the
executable. The names were resolved by the offline authoring toolchain and are
not on this disc. What remains is reading each of the **296 handlers** and
naming them from behaviour.

Those four tables are the same four `probe_elf_keywords.py` found from the
other end — 0x004F7568, 0x004F7FD4, 0x004F9564, 0x004F9BA4 — which is why its
six-hypothesis test came back at 2.0%: there was nothing there to find.

**The neighbouring claim, deliberately NOT settled.** *"control flow is not
authorable"* (`actb.py:21`, `README.md:589`, `ROADMAP.md:231`) is now narrower
than it reads — 1004 and 1005 are exactly the control-flow operands — but
knowing what an operand NAMES is not the same as knowing the record layout well
enough to write one. **UNTESTED.** It stays in this register as an open
question and must not be quoted as a limit without saying so.

**Action:** filed as REQ-020 (rdr-fmt owns `actb.py`).

**Consequence for C-026, added 2026-09-14 — not an edit to that entry, which is
rdr-fmt's.** C-026 records the Scripting tab shipping *"Read-only, and it will
stay that way until the action opcodes have names"* (`web/logic.js:415`), with
the verdict UNTESTED and the note that the "until" joins two unrelated
questions. Two things have happened since that make the precondition worse than
untested:

- §1.7 of `07_Research/MISSION_LOGIC.md` shows the action opcode names are
  **not on this disc in any form** — resolved by the offline authoring
  toolchain, unrecoverable by disassembly.
- REQ-021 asked whether to do the 296 handler-naming jobs that would be needed
  regardless, and the answer was **no** (the reviewer, 2026-09-14).

So the shipped sentence promises a user that the tab becomes editable when
something happens, and **that something is now known not to be coming**. That
is a stronger finding than C-026's existing one and it belongs to whoever owns
the string; filed as REQ-034 for rdr-app rather than edited in here.
## Register — the front-end menu (rdr-menu)

### C-037 — "the main menu can't be extended without ELF surgery"
**Verdict: VERIFIED-FALSE as a blanket claim. TRUE of exactly one half of the
menu.** Never written down in this repo — it is the assumption the lane was
handed, and it is wrong about the half that matters.

**Probe:** `03_Tools/frontmenu/probe_frontend.py` — 19 checks over
`07_Research/SLUS_205.00.unpacked.bin` and `04_Extracted/FRONT`. Reads only;
no ISO, no lock. Full working: `05_Docs/FRONTMENU-FEASIBILITY.md`.

The front end is two menus and they answer differently.

| | Outer desk (tilt between Story / Showdown / Journal) | Story-book list (LOAD GAME, NEW GAME, PROFILE, PLAYER OPTIONS, BOUNTY HUNTER) |
|---|---|---|
| Where it lives | `SLUS_205.00` | `FRONT/storyselect.swui` |
| Shape | 16-state switch, jump table at `0x00517d40` | five `<BUTTON>` tags, 20px pitch |
| A new entry costs | a relocated jump table, a new state, 3 camera animations, 6 transition names | one line of text |

What settles it:

- **The labels are an ELF table, not data.** `0x004f6ed0` is a `const char *[5]`
  — `""`, `"Story Mode"`, `"Showdown"`, `"Journal"`, `"Options"` — indexed raw
  at `0x0012e250` (`sll v0,s0,2; addu; lw`), no bound and no terminator.
  Mode 4 (`"Options"`) has no caller: a dead slot, the one free label in the
  front end.
- **The desk switch cannot grow in place.** `sltiu v0,v1,16` at `0x001393b0`;
  the 16-entry table at `0x00517d40` is followed immediately by the string
  `"swcTuningSet"`.
- **The camera graph reads like data and is not.** `FRONT/swat.roommach`
  declares 15 named transitions — a closed 3-desk triangle — but all 15 names
  and all 8 camera-animation names are ELF string constants
  (`0x00517a36`..`0x00517c02`). The file supplies `StartPhase` / `EndPhase` /
  `Loop` / `Speed` and nothing else. **This is the entry's real content:** a
  `.roommach` looks editable and will not change the graph.
- **The book's row list is genuinely data.** The five rows are `<BUTTON>` tags.
  The screen class at `0x0011dad0` resolves each row id by NAME and keeps the
  pointer only to grey the row out (`sb flag,240(widget)` + vtable call at
  `0x0011e028`..). Nothing enumerates rows for the cursor.

**Still untested, and named as untested:** whether the pad's cursor reaches a
sixth `<BUTTON>` the executable has never heard of. Nothing in the ELF says it
cannot. Only a disc settles it, and that is this lane's next step — see
[[defaults-that-silently-mean-off]] territory: the row rendering is not the
same claim as the row being reachable, and I am not counting the first as the
second.

### C-038 — "SWAT/main.swui is the main menu"
**Verdict: VERIFIED-FALSE.** It is the pre-release DVD-style menu and it is
dead. `main_new`, `linkStory`, `linkGallery`, `linkBountyHunter`, `gallery`
and `modeselect` appear **nowhere** in `SLUS_205.00` (same probe, byte grep
on NUL-delimited strings). `SWAT/main_new.swui` even carries a ready-made
`<BUTTON:0,0,0:linkGallery:gallery:...>` row — editing it changes nothing on
screen. Registered because it is the single most inviting wrong turn in this
area and costs a day.


### C-039 — "a new front-end screen needs an executable patch"
**Verdict: VERIFIED-FALSE.** Mine, from C-037's own §5, written 2026-09-18 and
wrong the same day. Recorded rather than quietly edited, because the reasoning
that produced it is the kind that reads as thorough.

**What I got wrong.** I found `0x0013bba8` — the front end resolves a screen
by walking a registered list and comparing names, returning NULL for anything
else — and I found the list being filled at `0x001389c8` by 14 literal calls.
Both true. I concluded a new screen must therefore cost an executable patch.
That does not follow, and the step I skipped was asking **whether anything
else fills the same list.**

**Probe:** `03_Tools/frontmenu/probe_frontend.py` §8, 4 checks. Something else
does:

| caller | `jal` | rect | names from |
|---|---|---|---|
| front-end init `0x001389c8` | `0x001186d8` | 512x256 | 14 compiled in |
| cache loader `0x00119400` | `0x001186d8` | 640x448 | **one per line of a text file** |

**The same function.** `0x00119400` opens `<name>.swui` (`0x00515b37`), reads
it line by line (`0x002603e0`, delimiter 10, 255-byte lines), and calls
`0x001186d8` once per line. The file is `FRONT/frontendcache.swui` — data.

**What settles it is not the disassembly, it is two screens that ship.**
`gamepicture` and `gamesound` are reachable Options pages on the retail disc.
Both are listed in `frontendcache.swui`. **Neither is among the executable's
14.** They are openable because a text file names them.

Demonstrated end to end: `02_Modded/RDR_MENUTEST.iso` carries a new
`modlist.swui`, registered through that file and reachable from two screens,
with **no executable byte changed** and all 317 pre-existing `FRONT.DAT`
members keeping their offsets — `03_Tools/frontmenu/verify_modlist_row.py`,
13 checks.

**BOOTED 2026-09-18, and the verdict needs splitting.** The hedge above was the
right one to write and it is now cashed in. On real hardware the new screen
**registered, loaded and rendered its three entries correctly** — so the claim
as worded stays VERIFIED-FALSE: getting a new screen onto the disc and onto the
television needs no executable patch.

**But it handles no input, and that is fatal.** rdr-play pressed all twelve pad
buttons on it and measured **0.00 mean absolute frame difference on every one**,
with the emulator live at 30fps and the pad confirmed reaching the driver. The
page is a soft-lock: the only way out is killing the emulator.

The mechanism is a **screen-class factory** at `0x00118b78`..`0x001191f0` — a
strcmp chain on the screen NAME, each arm allocating a different C++ object
with its own vtable (`gamesound` -> `new(0x47c)`, `gamepicture` -> `new(0x4a8)`,
`mainoptions`, `difficultyselect`). The default arm at `0x001191dc` allocates
`new(0x44c)` — the BASE size — and stores **no vtable**. A name the executable
has never heard of gets the plain base page: it draws, and it does nothing.

**So the honest form of this capability is two claims, not one:**

| | verdict |
|---|---|
| a new front-end screen can be registered, loaded and RENDERED from data | **true, no patch** |
| ...and made INTERACTIVE from data | **false — needs a class, so needs a patch** |

And a patch cannot be shipped at all: `rdrstudio/elfpack.py` is unpack-only
(`depack`, `unpack_elf`, no packer) against an aPLib + XOR-obfuscated
`SLUS_205.00`. Not expensive — unavailable.

**What works instead**, and is on the disc now: write the content onto a screen
that already HAS a class. `mainoptions` is one, so the mod names ride there and
inherit working input and Cancel for free —
`03_Tools/frontmenu/verify_modlist_row.py`, 11 checks, which also assert the
soft-lock is *not* on the disc.

*Two corrections on one claim in one day. The first came from finding a single
filler of a list and not asking whether anything else filled it; the second
from proving a thing loads and treating that as proving it works. Both were
answerable before the boot and I did not answer them.*


### C-041 — "a selectable Mod List row needs an executable patch"
**Verdict: VERIFIED-FALSE. Booted 2026-09-19.** Mine, and I told it to the reviewer,
who relayed it to the developer. A wrong *impossible* is the most expensive kind of
wrong claim, because nobody re-tests it.

**What I got right and then over-generalised.** C-039 is sound: the screen-class
factory at `0x00118b78` strcmps the screen name, and the default arm at
`0x001191dc` hands an unrecognised name the BASE class with no input handling.
A brand-new screen really does render and then trap the player. From that I
concluded a selectable row was impossible without a packer. **It does not
follow.** I checked what happens to names the factory does not know, and never
asked how many names it *does* know.

**It knows 25.** And `videooptions` is one of them — `new(0x454)`, a real
subclass — while being registered **nowhere**: absent from
`frontendcache.swui`, from `singleplayercache.swui`, and from the executable's
14 front-end registrations, with `videooptions.swui` already sitting in
`FRONT.DAT`. A working class with nothing wired to it.

**So the route is three data edits and zero executable bytes:** register the
name in `frontendcache.swui`, put the list on its page, and link a real
`<BUTTON>` row to it. The row inherits a class that already drives the pad.

**Proven on a television**, not on paper: `02_Modded/RDR_CROWD_060_ROW.iso`,
booted by the reviewer — the row appears under Picture/Sound Options, opens a page
titled *Mod List* carrying the names, and Back returns. Verified by
`03_Tools/frontmenu/verify_modlist_row.py` (20 checks) beforehand, which now
also asserts that any added row points at a name in the CLASSED set.

**What is still true, and the distinction I blurred the first time.** This is a
selectable row in the **menu system** — the Options page. It is NOT a fourth
item on the title-screen desk: that is the 16-state machine and the camera
graph of C-037, and it still needs executable work. "A selectable Mod List row"
and "a new item on the title screen" are different claims and I merged them.

**RETRACTED IN PART, 2026-09-19. The row is selectable. The page it opens
HANGS THE CONSOLE, and I called this proven twice.**

`videooptions` is not an idle screen. Its constructor stores **10.0f** into the
object and nothing else touches it:

    0x00118e9c  lui  at,0x4120      ; 0x41200000 = 10.0
    0x00118eb4  swc1 f0,1100(s0)

That is a **video-mode confirmation countdown** -- the "keep this setting,
reverting in ten seconds" every console video-test screen has. It is compiled
in, not data, and it cannot be neutralised from a `.swui`.

rdr-play measured the consequence on `RDR_CROWD_060_ROW4`: the page renders
correctly, and **about ten seconds later, with no input at all**, the screen
goes black and freezes. Frame delta 0.000 across four samples, every pad button
unresponsive, 31 threads at 19.3 percent CPU with **zero disc reads** -- alive
and spinning. The timer fires, the class acts on it, and there is nothing
behind it.

**Why I called it proven anyway, twice.** The first disc was booted, the row
opened, Back worked, and that was reported as success -- by me, then by the reviewer.
**Nobody waited ten seconds.** The row IS selectable and the page DOES render;
both halves of what I claimed are true, and the feature is still unusable.
That is the same presence-mistaken-for-function in
`05_Docs/CHECKS-THAT-LIE.md` entry 20, committed by the person who wrote the
entry, one build later.

**So the honest verdict is narrower than either thing I have said:**

| | |
|---|---|
| a selectable row, no executable patch | **true** -- it draws, highlights and activates |
| ...opening a page that survives | **not by this route**, and not fixably in data |

**`videooptions` was the only classed screen registered nowhere**, which is
what made it look free. It was unowned because it is a special-purpose timed
screen, not because nobody had got round to it. A page that is idle may be idle
for a reason.

**C-040 stands unchanged** — there is still no aPLib packer, and that is still
what blocks anything needing an executable change. It just turned out this did
not need one.

### C-040 — "SLUS_205.00 can be patched" (it cannot, today)
**Verdict: CONFIRMED-TRUE as a limit** — recorded because it is a *constraint*,
not a false claim, and because it is the kind that otherwise gets rediscovered
separately by every lane that wants one instruction changed.

**Nothing in this toolkit can put a modified executable back on the disc.**
`03_Tools/rdrstudio/elfpack.py` exposes `depack`, `unpack_elf` — and no packer.
The shipped executable is aPLib-compressed with an XOR-obfuscated stream (key
starts at 1, increments per byte consumed; the first output byte is not XORed),
per that module's own header. Unpacking is solved; repacking has never been
written.

**Why it matters beyond the menu.** It converts a whole class of answer from
"expensive" to "not currently possible", and those are different answers to
give the developer. rdr-menu hit it at the end of a chain that had already been
narrowed to a **two-instruction** change — repointing one `lui`/`addiu` pair —
and two instructions are not cheaper than four hundred when the number you can
ship is zero.

**It is not a claim that it cannot be done.** aPLib compressors exist and the
XOR is one line; `scripts/dis_elf_stub.py` already disassembled the
self-extractor and the stub itself lives in the first 4 KB **uncompressed**,
so it is patchable without repacking anything. This entry says only that the
packer **does not exist yet and no lane owns the file** — `elfpack.py` is one
of the pre-split unowned modules named in `STATE-CONTRACT.md`.

**Anyone who writes it unlocks more than one feature.** For rdr-menu alone it
would turn C-039's dead half live: a screen-class arm, or a sixth arm on the
story book's CALLB dispatcher, and the Mod List becomes a real selectable menu
entry instead of a list of names on the Options page. Flagged for rdr-re rather
than claimed — this lane is not the right owner for it.


## rdr-codemods (the reviewer, 2026-11-07) — community-research executable hooks

### C-042 — "SLUS_205.00 can be patched" (supersedes the headline of C-040)
**Verdict: VERIFIED — it can, and without writing an aPLib packer.** C-040 said the shipped executable cannot go back on
the disc because no packer exists, while noting the boot stub sits uncompressed and is patchable. That second half is
the whole answer: **community reverse-engineering research** appends an installer after the ELF's section headers, grows the
single PT_LOAD segment, and points the stub's `j 0x00100008` (`0x00800020`) at it. The installer runs once after the
original unpacker, writes our code caves and hook words into the unpacked game, flushes both caches and jumps to the
game entry. The packed game bytes are never touched. Ported to `03_Tools/rdrstudio/codemods.py`; offline proof is
`scripts/test_codemods.py` (addresses verified against our unpack, installer executed in an R5900 interpreter, and the
executable is byte-identical to the one in the tester's own patched ISO). In-game result: `05_Docs/CODEMODS.md`.

**What this does NOT unlock (still true from C-040):** there is still no way to change the *packed* image's bytes in
place; everything goes through hooks + caves in the free block `0x000F0000-0x000FFFFF` (assumed free, not proven).
A new front-end screen class (C-039) is *possible* the same way but is not built.

**Snags recorded, not hidden:**
1. The patched executable's PCSX2 CRC differs from `E169BAF8`, so CRC-keyed per-game settings, cheats and patches do not
   apply, and a memory card assigned by CRC is not picked up. (On this machine there is no per-game ini, so the global
   card loads and Red's save was found.)
2. It is larger than its disc extent, so it is appended at the end of the image and the ISO 9660 root record + volume size
   are repointed. The UDF bridge (NSR02) still lists the original ELF; PCSX2/BIOS boot through ISO 9660. Real hardware
   untested.
3. The Cheats page layout (`cheats.swui`) is replaced in swat.dat and all 61 level archives; it must deflate into the
   smallest slot (512 B), which forced short row labels ("Noclip", "Freeze", "Cam Lock") with zlib.

### C-043 — "Code mods can ship as a small pack, not a whole ISO"
**Verdict: VERIFIED offline and by apply (2026-11-07); in-game result below.** A mod pack (format `rdrmodpack/2`) can carry
the code-mod list instead of game bytes. The recipient's Studio rebuilds the executable patch and the Cheats rows from
its own stock disc and writes a NEW `02_Modded/RDR_CODEMODS.iso` (C-042 build; same refusals). Proof: a 792-byte pack
(three mods) and a 1.8 MB pack (three mods + custom cactus) applied to fresh copies of the clean ISO produced discs
**byte-identical to a direct Build**; a pack with a different stock executable is refused with nothing written; an older
Studio refuses format 2 cleanly. Tests: `scripts/test_codemods_pack.py` (suite), `scripts/codemods_pack_proof.py`,
`codemods_pack_proof2.py` (live, need a scratch Studio + 2 GB copies, not in the suite). See `05_Docs/CODEMODS.md`.
**Not proven:** the pack's Studio-UI buttons (Code Mods > Export as mod pack; Export > Apply a mod pack with a code-mod
pack) were driven through their HTTP routes, not clicked in a browser; real hardware / UDF readers (same as C-042);
applying a code-mod pack on top of a working disc that already has code mods is refused by design, tested with a stub.

---




---

## Retired text — machine-checked

Every VERIFIED-FALSE claim above leaves a string behind. The `[M3]` acceptance
test (`03_Tools/scripts/verify_capabilities.py`) reads the block below and
fails if any of these appears anywhere in the shipped app or docs. Matching is
case-insensitive and whitespace-insensitive, so a reflow cannot smuggle one
back in.

Add a line here whenever a claim is settled FALSE. Do not delete one: a
retired claim that comes back is exactly the regression this test is for.

A claim whose entry is `pending(<lane>)` has text that still ships, in a file
this lane does not own. The test PRINTS those rather than failing on them —
one lane cannot be blocked by another's queue — but it prints them every run,
so they cannot quietly become permanent. Move the line to a plain phrase the
moment the owning lane lands the fix.

A claim whose entry is `-` was settled FALSE but never had shipped text to
retire — it lived in code behaviour, or in a bug report, not in copy. The `-`
is how you say so; the test refuses a VERIFIED-FALSE claim that says nothing
either way, because silence is indistinguishable from forgetting.

- **C-002** — nothing in the app ever claimed this in words. `can_add`'s
  message (*"only the 'Dave' packed-name variant is supported"*) is accurate
  about today's encoder; the FALSE part is the inference that the container
  forbids it.
- **C-003** — the claim is the tester's bug report, not shipped copy. What ships
  is the `file_drop` behaviour, which is rdr-app's to fix (REQ-001).
- **C-018** — ~~`pending(rdr-app)`~~ **landed 2026-09-14 (rdr-app).** The
  sentence is gone from `routes2.py` and the line above is now enforced,
  not printed. The advice it replaced told the user a property of the
  route as a property of the files; there are now three sentences where
  there was one, because there are three situations — the arsenal case,
  the one where the archive has a free run big enough and the user has
  simply not ticked "allow files to grow", and the one where it really
  will not fit anywhere. `grow` is threaded through `edit_creature` as
  well, so the 747 are editable and not merely described correctly.
- **C-020** — the false part was behaviour, and rdr-3d fixed it. The string
  *"is not a cm2 clip"* still stands in `studio.py:4656` and
  `transfer.py:386`, but as the catch for a clip that genuinely will not
  parse — wrong cause, not a wrong capability. Wording filed as REQ-004.
- **C-023** — never written down anywhere; the importer merely behaved as
  though it were true.
- **C-028** — the same shape, and rdr-3d says so in the entry: "never written
  down, and the first `cmp` importer behaved as though it were true". Nothing
  in the copy to retire; the falsehood was in the encoder's behaviour.
- **C-027** — two halves. The ROADMAP row is retired outright. The UI's
  *"16-colour — read only"* was `pending(rdr-app)`: it is dead code today (no
  texture on the disc has a non-`PAL0` palette) but it is the wrong sentence
  gated on the wrong property, so it is watched rather than quietly left.
  **Landed 2026-09-14 (rdr-app)** and the line above is now enforced. The
  `textures` route reports `bpp` and `swizzled` per row and the grid gates
  `Replace…` on them, so the refusal happens before the file picker instead
  of after the user has chosen a PNG. Both sentences mirror
  `encode_into`'s own — a second reading of a policy `texture.py` owns,
  which is the kind of copy that drifts; a `can_replace()` helper there
  would be better and is asked for in LANE-REQUESTS.md.

<!-- RETIRED-TEXT:BEGIN -->
    C-001 | Files cannot be added to a Dave archive
    C-001 | files cannot be added to an archive
    C-001 | you cannot add a *file* to an archive
    C-004 | cannot grow by a single byte
    C-004 | Zero headroom (cannot grow one byte)
    C-005 | SWAT.DAT is the one that fails
    C-006 | Reordering is free; adding is effectively impossible
    C-002 | -
    C-003 | -
    C-018 | these files are patched in place and cannot grow
    C-027 | 5,687 in-bundle + 4,472 loose, import and export
    C-009 | pending(rdr-app) | there is no .stm writer
    C-010 | The game has no way to ask for them, so they cannot play
    C-010 | minutes of finished audio the game cannot play
    C-027 | 16-colour — read only
    C-020 | -
    C-023 | -
    C-028 | -
    C-031 | -
    C-032 | -
    C-030 | a same-width edit costs zero bytes
    C-033 | 101 of the 126 file extensions on this disc are plain ASCII
    C-034 | -
    C-035 | -
    C-036 | nothing short of disassembling that ELF will name them
    C-037 | -
    C-038 | -
    C-039 | -
    C-041 | -
C-045 | -
C-046 | -
<!-- RETIRED-TEXT:END -->

### C-044 — "Pause > Cheats on any save, quarter-speed world, and a live position readout, for modding"
**Verdict: PROVEN in PCSX2 on a stock save (2026-11-08), disc `RDR_CODEMODS_CACTUS2.iso`.** `cheatsmenu` makes every built-in cheat
OWNED (ownership test `0x122248` + pause-menu gate `0x1222A8`; none switched on; the two never-implemented debug cheats stay hidden),
so the Cheats page and its Noclip / Freeze / Cam Lock / Slow-Mo rows exist on any save with no pnach. `slowmo` wraps the frame
timer `0x0025F960` and scales the frame time x0.25: 5.8 m vs 23.6 m walked in 5 s (x4.07). `scripts/rdr_pos.py` reads player X/Y/Z
+ heading from PCSX2 RAM over PINE (read-only). Also seen: L3+R3 noclip combo, R3+Triangle/Circle/Square chords flip their flags,
built-in No HUD hides the HUD. Evidence `05_Docs/ingame-proof/cheats2_*.png`; tests `test_codemods.py`, `test_rdr_pos.py` (suite).
**Not done / not proven:** an IN-GAME text readout (no text-draw routine to call; the readout is external); a free pad-2 camera
(dropped: Noclip + Cam Lock already cover it); Freeze/Cam Lock through their Cheats rows; hardware; whether the readout's coordinates
equal level-file placement coordinates. See `05_Docs/CODEMODS.md` ("Modding aids added 2026-11-08").

### C-045 — "Moving a model leaves its collision behind" / "collision is a separate mesh you move yourself"
**VERIFIED-FALSE as a limit (2026-11-12).** It was true of the tool, not of the format. Most models have no shell of
their own: their collision is welded into merged `bound_bound_grp_*` shells (SHER70: 37 of 41 shells, 296 of 300 models),
and a shell's index is an octree that must be rebuilt after any vertex moves. The Studio now finds the collision pieces
inside a model (set)'s box, moves them by the same transform, rebuilds and audits the octree, in the same undo step, on
all three move paths (drag, reshape, selection). A model made of several drawn pieces moves as one set.
Proof: `scripts/test_move_set_live.py`; PCSX2 walks with a position trace, through the Studio route, turn 90 + move
(`05_Docs/ingame-proof/move_set_studio_*`). Scaled collision PROVEN 2026-11-12 (`scale_set_*`: wall 0.84 m further out at Scale 1.5, stock control). **Not proven:** tile-set (manifest) levels;
pieces poking > 0.3 m outside the model box stay put. Details: `05_Docs/DEEPDIVE-2026-11-11.md`.

### C-046 — "Infinite ammo / god mode need a cheat device"
**VERIFIED-FALSE (2026-11-12).** Code Mods builds them into the disc copy: Infinite Ammo (one nop at 0x0039BBC8) and
God Mode (cave on Health::TakeDamage 0x0021ADDC) are PROVEN in PCSX2 on a Studio-built disc
(`05_Docs/ingame-proof/combat_mods_*.png`). One-Hit Kills shares that cave and is PROVEN on the built disc too (2026-11-12): one
shot took the spawn gunman 60 -> 0 health; the same enemy on a stock disc needed four (60 -> 41.1 -> 26.9 -> 0;
`ingame-proof/onehitkill_*.png`). Tests: `scripts/test_codemods_combat.py`.
Fast Red (2026-11-12): Red on foot at 1.5x, everyone else unchanged, PROVEN on a built disc (7.1 vs 4.8 m/s; walls
still stop him). Test: `scripts/test_codemods_speed.py`.
