# Roadmap

What is proven, what is built but not yet seen in the game, and what is next. The full list of what 2.0.0 changed
is in `05_Docs/RESEARCH/FORK-CHANGES.md`.

## Proven in the game (2.0.0)

- Data Editor builds: new Showdown maps (New .dat) load and play; a custom player character loads once its files
  are at the top of SWAT.DAT and in the mission's own archive.
- Flash screens: edited text, imported pictures, filled fonts, and `.pck` files that grow.
- Sounds replaced through a screen's own `attachSound` names; streams added to the Audio tab.
- Animations: an unedited Blender round trip is byte-identical; an edited clip written into every archive that
  holds it (a level's load show plays the copy in its own archive).

## Built, not yet seen in the game

- Streams at 12 kHz and 48 kHz made by the stream creator (24 kHz stereo is tested).
- A Showdown map renamed through the level editor's *Name in the Showdown menu*.
- `.glb` exports (checked in Blender 4.5, not round-tripped back).

## Next

- AVM1 (the Flash screens' ActionScript): read, not yet edited.
- `.stm`: name the remaining header words.
- Import a `.glb` animation back into a clip (today the way back is BVH).
- A release check that runs without a disc image (most self-checks read one).
