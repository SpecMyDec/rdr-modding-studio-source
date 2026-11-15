# How to put your own 3D model in the game

Two places in the Studio take a model you made: **level models** (scenery,
buildings, props in a level) and **weapon models**. Both work the same way:
export an `.obj`, edit it in Blender, import it back, boot the game.

## Level model (Level editor tab)

1. **Pick the level**, then click the model in the list (Inspector panel).
2. Press **Export .obj** and open the file in Blender.
3. Reshape it. Keep it about the same size and in the same place.
4. In Blender: **File > Export > Wavefront (.obj)** (include UVs; triangles or
   quads are both fine).
5. Back in the Studio press **Import custom model** and choose your `.obj`.
   The Studio tells you the size and whether it fits, and asks to confirm.
6. Press **Boot test** in the top bar (or run `RDR_MODDED.iso` in PCSX2).

**Limits (the Studio also shows these):**
- It has to fit in the space the original used. If it is too big you get
  *"Too big ... Reduce the triangle count"*: use Blender's **Decimate**
  modifier and try again. Nothing is changed when an import is refused.
- **Collision and model move together (2026-11-05).** Drag or nudge a model in
  the Level editor and its collision shell moves with it, in the same undo step.
  There is no separate collision step. This works wherever the game itself pairs
  them (a `zone_X` model with a `bound_X` shell): 1,470 pairs across all 61
  levels. Where a model has no single matching shell (shared `bound_bound_grp`
  shells, or props that were never solid) the Studio says so instead.
- **A custom model gets its own collision (2026-11-05).** Import a mesh and the
  Studio fits a solid box to it (`rdrstudio/custcoll.py`). If the model had a
  collision twin the twin's shape is replaced; if not (e.g. the SHER70 cactus,
  `zone_ppear01_01` had none) a NEW `bound_<name>_01` collision chunk + node is
  added with the same name stem, so it follows every move of the model. The
  confirm box and the result message say which happened, or say plainly why no
  collision was added (no room in the level file, no named object uses the
  model). It is a box around the mesh, not the mesh's exact outline. Ctrl+Z
  takes model and collision back in one step.
- **Moving the drawn surface moves its collision too** (Shape panel, same
  pivot and amounts), in the same write.
- **Solid / walk-through** is one switch in each model's panel ("Collision"):
  it flips the walker flags on its shell. Camera/shoot/target flags are left
  alone, so a walk-through model still stops bullets. Default solid. Models with
  no single collision of their own (shared `bound_bound_grp` shells, props that
  never had a `bound_` twin) show one plain line saying so instead of a switch.
  There is no separate collision object to select any more: picking a shell
  selects its model. (The Collision *view* toggle in the toolbar only shows or
  hides the shells; it does not create a second thing to manage.)
- **In-game status:** a custom model draws, and a generated collision box
  blocks the player (PCSX2). The walk-through switch has NOT been boot-tested.
- It keeps the original texture.
- **Ctrl+Z** undoes an import.

## Weapon model (Balance > Weapons & stats > Weapons)

![the weapon model panel](HOWTO-weapon-panel.png)

1. Press **New weapon...** to make your own weapon with its own model (or pick an existing weapon on the left; the right panel shows its model name).
2. Make the gun in Blender, export **Wavefront (.obj)**. Keep it TINY: see the limit below.
3. Press **Choose .obj**. The Studio checks it straight away and says whether it fits.
4. If the model is shared, an amber warning names the other weapons that change too (no tick-box any more). A New weapon has its own model, so it changes only itself.
5. Press **Import** (written into every level archive copy), then **Boot test**.

**In-game status (2026-11-07, PCSX2):**
- PROVEN: a disc with a New weapon + its own model boots into the town; a plain 12-triangle box imported over Red's rifle model draws on his back (ingame-proof/weapon_custom_box_on_reds_back.png).
- FOUND: imports of 24 points/44 triangles and 48 points/68 triangles froze the level load (logo screen forever). The Studio's size check does not catch it. Limit unknown; stay box-small.
- NOT DONE: equipping the new weapon. The weapon-select screen only lists weapons in Red's save inventory; the weapon table, shop list, arsenals and level-flow loadout were all written but it still did not appear.
- Invincible testing: creature srd.cr MaxHitPoints 100000 + Invulnerable 1 kept Red alive 3+ minutes.

## Character part (Creatures > Model parts) -- EXPERIMENTAL

1. Open the creature, find the part in the list and press **↓ obj** (it is
   exported standing in its bind pose).
2. Reshape or rebuild it in Blender, export **Wavefront (.obj)**.
3. **↑ obj** = same number of points, just moved (safe, proven).
   **↑ new mesh** = any shape or size. Each new point is attached to the
   bones nearest to it on the original, so keep the new shape roughly where the
   old one was. It has to fit the original's space (the Studio says if not;
   use Decimate). Ctrl+Z undoes it.

Characters: **a custom mesh has been booted in PCSX2** (the town level's player
with an enlarged head: it draws, keeps its texture and animates). One character
and one mesh were tested, so still check yours with Boot test.

Two things that test taught:

- A rebuilt mesh is about a quarter bigger than the original, and all of a
  character's detail levels share one space. If it does not fit, import a very
  small mesh over a far-away detail level first (the later, smaller parts in
  the list) to free room.
- The character you play is not always `red`. In the town level the player is
  `srd`, stored in that level's own archive. Edit the creature the level
  actually uses, or nothing changes on screen.

## What is proven and what is not

- Level models: a custom box **was seen drawing in the game** (PCSX2).
- Weapon models: the file is written and re-reads correctly, and the loader
  chooses its reader from the file's first line, so the text format we write
  loads in place of the original. In PCSX2 a plain 12-triangle box drew on
  Red's back; meshes of 44 and 68 triangles **froze the level load**. Keep a
  weapon mesh to a few triangles until that limit is found. A brand-new weapon
  is not in any save, so it cannot be picked in game yet.
- Characters: moving points of an existing part is proven; "↑ new mesh" is
  built and re-reads correctly offline but is **unconfirmed in-game**.

If something goes wrong, the message says what to change. Restore the original
at any time with **Ctrl+Z** or **Reset to stock**.
