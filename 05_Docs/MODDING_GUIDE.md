# RDR Modding Studio — Modding Guide

The UI states facts in one line. The reasons live here.

## Boot testing
A boot test launches PCSX2 for about three minutes and reads its log. The
intro alone runs ~100 seconds, so a shorter run cannot tell a working disc
from a frozen one — a frozen disc shows a perfectly good title screen that
stops updating. "Play" hands the disc to the emulator in one step because
the Studio holds open file handles; launching PCSX2 by hand while the
Studio is up usually fails on a file lock.

## Confidence flags
- **Measured** — read straight off the disc; the value shown is the value
  the game reads.
- **Documented** — known from PS2 hardware or file-format documentation
  that predates this project; not measured here.
- **Inferred** — reasoned from evidence on the disc, not confirmed in
  game. The best current explanation, not a fact.
- **Unverified in game** — the tool writes it and the write fits its
  slot, but no boot has confirmed the game reads the change.
- **Inert** — present in the game's data and does nothing; the code that
  would read it was cut or never finished.

## Creature (.cr) edits
Writes fit their slots and read back exactly, but a boot test does not
exercise creature files — the intro never spawns most creatures. Load a
level that uses the edited creature in PCSX2 before trusting the change.
Ctrl+Z reverts them like any other edit.

## Adding files to an archive
A new file needs three things at once: a spare table-of-contents slot,
room in the name block, and a free run of bytes big enough for the
payload. The entry lands at its correct alphabetical position and every
existing file keeps its exact bytes and offset. **Adds cannot be undone
with Ctrl+Z** — there is no previous content to restore. Reset to stock
removes them. The packed name alphabet is a-z 0-9 and `. _ - / ( ) # $ ~`.

## Slots and capacity
Edits are patched in place; nothing is repacked on an ordinary write. An
edit must fit the byte gap its file occupies. Some files compress; an
edit can fail to fit even at the same text length if the new bytes
compress worse. "Allow files to grow" relocates an oversized file into
the archive's largest free gap instead of refusing — stock level archives
carry about 2 KB of free space each.

## Repacking
Repacking FRONT, STREAMS, BANKS and PUPSHOW was verified by playing the
game past its title screen. Repacking SWAT.DAT freezes the game —
verified by running it; every offline check passes on the frozen disc
too, which is why offline checks alone are never trusted. Archives not
listed as verified are unknown: treat a repack there as untested until a
boot proves it.

## Level events: flags, doors, cameras
Flags have no coordinates anywhere on the disc — nothing binds a flag
name to a position, so the tool lists them and draws no marker. Doors are
real positioned data from the `.sub` file: level, display name, arrival
spot, heading, door model and animation. Camera shots are positioned, but
what fires them is a named flag, so the trigger point cannot be drawn.

## Trains and tiled levels
Car positions are stated on the disc: the `.trn` gives each car a
resource index and a gap. Tile ORDER is read from the `.til` Map block —
the game's own authored sequence. Stagecoach (MSN) and Hell Pass (MSNCYN)
state it completely; the train levels mark two slots per set as picked at
runtime. Tile OFFSETS are derived by chaining each tile's rail splines
and are not written to disc. "Lay out along the track" is view-only:
positions no longer match the files, so editing is disabled while it is
on.

## Collision vs art
Collision is authored much coarser than the art — Brimstone has 342
visible surfaces and 16 collision meshes. A building's node carries no
position of its own, so dragging it moves the actual surface in the
level bundle. Collision is a separate mesh and does not follow unless
Solidity is ticked; shared shells (`bound_bound_grp_*`) cover several
objects and are never auto-moved. Bounds with per-flag names spell their
twelve collision flags in the name itself.

## Character models (OBJ round trip)
Parts export in bind pose — a standing figure — and the pose is removed
again on import. Move vertices only: adding, deleting or reordering
vertices is refused because the importer matches by index. Scaling the
mesh does not scale the skeleton; height comes from the skeleton, a
plain-text file editable in Files. Level-surface OBJ exports keep
triangles, UVs, vertex colours and textures as authored; the same
vertex-count rule applies on import.

## Animations
A clip stores three channels per bone plus a root translation. The
channel-to-bone mapping is verified; the Euler order is not, so a joint
turning on two axes at once may twist — try another order from the
dropdown. Rigs with fewer bones than the clip drives are legal: the
engine stops its apply loop at the skeleton's bone count. BVH exports
target the skeleton shown in the picker; multi-root skeletons cannot be
written to BVH (it allows exactly one root) but play fine in the viewer.

## Audio streams
Stream captions come from the disc's own sound-bank files, which name
each stream and often write out the line. Music and ambience link to
levels through the level's sound config: `SndBegin B` means music is
`snd_(B+99)`, streams `snd_(B+98)`, effects `snd_B`; levels sharing a
value share those files. Writing audio INTO a stream is not possible yet
— the tool reads the .stm format but cannot write it. Short sound
effects can be replaced today.

## Spawn points
Missions start at point #1 of the start area's `.spwn`. Respawns pick
randomly when a slot's bucket holds two or more points — move both
points to pin a respawn. The start area is the first `.sub` record with
StartLevel set, else the master area.

## Unlockables columns
The game reads both unlock-condition columns, but on the shipped disc
both are 0 on every mission — nothing in the game has ever used them.
Editing them is untested territory: boot and play before trusting an
effect. No bit number is shown beside cheat names because three
different orderings of the ten exist and nothing on the disc joins a
name to a bit.

## Editor geometry notes
`lvl_<area>.sneak` holds one int per AI-sneak box, positionally — adding
or deleting a box desyncs the list. `line_room_newgrnd` is why initial
framing is far out on Brimstone interiors. Raw textures are darker than
in game: each level multiplies them by an ambient colour and fades by
distance.
