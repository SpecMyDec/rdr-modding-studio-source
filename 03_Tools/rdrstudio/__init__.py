"""rdrstudio -- Red Dead Revolver (PS2, SLUS-20500) modding toolkit.

Pure stdlib. Nothing here repacks an archive: every write lands in the
slot the original file already occupied.

    from rdrstudio import Game
    g = Game(r"C:\\path\\to\\your\\working copy.iso", writable=True)
    print(g.archives())
    data = g.read("SWAT.DAT", "swat.guns")

Modules, roughly bottom-up:

    iso9660   ISO reader with in-place write
    dave      Dave/DAVE archive container, slot-safe patching
    game      the two together: open a disc, read/patch files
    scene     span-tracking parsers for the text level formats
    level     one level assembled from its archive, with edit builders
    rsc       RSC0 resource bundles: BND0 collision, textures, MOD0 models
    vif       PS2 VIF display-list decoding (render geometry)
    texture   PS2 palettes and pixel data <-> RGBA / PNG, both directions
    ipu       .ipu still images -- header decoded, pixels not yet
    stm       .stm streamed audio -- 4-bit IMA/DVI ADPCM, decoded. 8,361
              files, 52% of the disc by bytes. Spec read off adpcm_iop.c in
              AGESND.IRX: high nibble first, 64-byte channel interleave.
    mpeg2tables  ISO/IEC 13818-2 VLC, quantiser and scan tables, as plain data
    mpeg2intra   MPEG-2 intra decoding maths: bit reader, VLC, dequantise,
              IDCT, colour. Knows nothing about the disc -- give it a
              bitstream and the picture parameters a .ipu never states. Its
              docstring says which of those a search can actually settle.
    guns      structured swat.guns reading and editing
    campaign  levelflow / unlockables / item CSVs -- mission order, unlocks
    modes     which half of the game a level belongs to (Campaign / Showdown)
    creature  structured .cr reading and editing (movement, speed, body)
    actb      .actb/.stateb behaviour tables -- structure, not opcode meaning
    skeleton  .skel bone hierarchies (plain text, fully decoded)
    anim      .anim skeletal animation, the "cm2" codec -- layout settled by
              scripts/verify_anim.py, channel semantics still open
    journal   undo/redo and mods as portable files
    export    level -> Wavefront OBJ
    studio    the local web app (API + three.js front end)
    config    machine paths -- edit here, not in the code

Added by the 2026-08 editing pass, which closed the gap between what the
toolkit could already read and what it would let anyone change:

    cast      the legal palette for one archive: which characters exist
              (.cr) and which behaviours (.tbl). Type and Table are
              ORTHOGONAL, so this returns two lists and never one.
    enemyfields  the 21 fields of an enemy, typed, with validation and the
              splices that set, insert, remove or clone a block
    links     .sub area doors, with byte spans, so a door can be edited
    versions  one story over checkpoints and mods -- they were always the
              same file format wearing two coats
    transfer  one shape for every import and export; check never writes
    features  which capabilities exist in this build
    api       the new-route envelope and the frozen `plan` object
    routes2   the routes those modules serve, kept out of studio.py so a
              mistake in new code cannot reach the ~115 routes that work
"""

from .dave import Dave, Entry, SlotOverflow, SECTOR_ALIGNED_EXTS
from .game import Game, open_loose
from .iso9660 import Iso9660

__all__ = [
    "Game", "Dave", "Entry", "Iso9660", "SlotOverflow",
    "SECTOR_ALIGNED_EXTS", "open_loose",
]
__version__ = "0.6.0"
