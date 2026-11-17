"""Unlock added maps and characters on the PS2 (Script Hooks: "unlocknew"; the Xbox's is the recomp's trainer.cpp).

A Showdown character or stage a mod adds has a row in unlockables.csv that no save knows: a save made before the mod
has no bit for it, so it stays locked. The game asks ONE function whether unlockable N is unlocked, 0x00183028
(manager, N) (Unlock Everything wraps the same one: codemods.build_unlockall). This cave answers 1 for a row whose name
is one the mod added (the names that are not in the shipped table, found at build time), and runs the game's own test
for every other row. Nothing is written to the memory card.
"""
from __future__ import annotations

from . import codemods as C

CAVE = 0x000F8000                  # free in the 0x000F0000 block (research F2000-F5FFF, alttex F6000-F7FFF / FA000)
CAVE_END = 0x000F9000
STOCK = frozenset(n.lower() for n in (
    'cheat_bigmoney', 'cheat_goldengun', 'cheat_infinitefocus', 'cheat_invulnerable', 'cheat_showaiinfo',
    'cheat_showposition', 'cheat_sudden_death', 'cheat_sad_stars', 'cheat_no_hud', 'cheat_no_distort',
    'lvl_bridge', 'lvl_coach', 'lvl_core', 'lvl_flash', 'lvl_fort', 'lvl_ghost', 'lvl_gun', 'lvl_hng', 'lvl_int',
    'lvl_jail', 'lvl_mans', 'lvl_perf', 'lvl_ranch', 'lvl_sher70', 'lvl_shernit', 'lvl_colseum',
    'maka', 'mani', 'mbdg', 'mbfs', 'mbkt', 'mblk', 'mbra', 'mbty', 'mbwi', 'mchk', 'mcls', 'mctn', 'mdad', 'mdgo',
    'melt', 'mfrk', 'mgay', 'mggl', 'mgwk', 'mhdh', 'mjck', 'mkng', 'mkti', 'mmdg', 'mmok', 'mmom', 'mmon', 'mmrd',
    'mmrk', 'mmsk', 'mmst', 'mncb', 'mntl', 'mpgj', 'mpry', 'msev', 'mshp', 'msrd', 'msto', 'mtcb', 'mtck', 'mttk',
    'mtwg', 'mugl', 'mwsk', 'mwyn', 'mydr', 'mzzt'))


def added_names(csv_text):
    """The character and stage rows of an unlockables.csv the game did not ship (filler rows "_..." left out)."""
    out = []
    for line in (csv_text or '').splitlines()[1:]:
        cells = [c.strip() for c in line.split(',')]
        if len(cells) < 2 or not cells[0] or cells[0].startswith('_'):
            continue
        if cells[1].lower() not in ('character', 'stage'):
            continue
        if cells[0].lower() not in STOCK and cells[0] not in out:
            out.append(cells[0])
    return out


def build(img, names):
    if img is not None and (img.word(C.UNLOCK_FN), img.word(C.UNLOCK_FN + 4)) != C.UNLOCK_FN_WORDS:
        raise ValueError("0x%08X is not the vanilla unlock test -- not the vanilla executable?" % C.UNLOCK_FN)
    a = C.Asm(CAVE)
    a.lw("t0", 0x14, "a0", "unlockables table header")
    a.beq("t0", "zero", "un_orig")
    a.nop()
    a._i(37, "t0", "t1", 4, "lhu t1,4(t0): row count")
    a._rr(43, "t2", "a1", "t1", c="sltu t2,a1,count")
    a.beq("t2", "zero", "un_orig", "index past the table: original test")
    a.nop()
    a.lw("t0", 0, "t0", "row pointer array")
    a.addu("t1", "a1", "a1")
    a.addu("t1", "t1", "t1")
    a.addu("t0", "t0", "t1")
    a.lw("t0", 0, "t0", "row")
    a.lw("t0", 0, "t0", "row name")
    a.beq("t0", "zero", "un_orig")
    a.nop()
    a.la("t3", 0, "the added names' table (patched below)")
    a.label("un_loop")
    a.lw("t4", 0, "t3", "an added name (0 = end)")
    a.beq("t4", "zero", "un_orig")
    a.nop()
    a.addu("t5", "t0", "zero")
    a.label("un_cmp")
    a.lbu("t6", 0, "t5")
    a.lbu("t7", 0, "t4")
    a.bne("t6", "t7", "un_next")
    a.nop()
    a.beq("t6", "zero", "un_yes", "the whole name matched")
    a.nop()
    a.addiu("t5", "t5", 1)
    a.beq("zero", "zero", "un_cmp")
    a.addiu("t4", "t4", 1)
    a.label("un_next")
    a.beq("zero", "zero", "un_loop")
    a.addiu("t3", "t3", 4)
    a.label("un_orig")
    a.raw(C.UNLOCK_FN_WORDS[0], "displaced: move a3,a0")
    a.raw(C.UNLOCK_FN_WORDS[1], "displaced: sra v0,a1,5")
    a.j(C.UNLOCK_FN + 8)
    a.nop()
    a.label("un_yes")
    a.jr("ra")
    a.addiu("v0", "zero", 1, "an added row: unlocked")
    code = a.assemble()
    # the table (pointers, 0 at the end) and the names after the code
    table = CAVE + 4 * len(code)
    table = (table + 3) & ~3
    strings = table + 4 * (len(names) + 1)
    blob, ptrs = b"", []
    for n in names:
        ptrs.append(strings + len(blob))
        blob += n.encode("latin-1") + b"\0"
    blob += b"\0" * ((-len(blob)) % 4)
    if strings + len(blob) > CAVE_END:
        raise ValueError("too many added maps and characters for the unlock cave (%d)" % len(names))
    hi, lo = (table + 0x8000) >> 16, table & 0xFFFF
    i = next(k for k, (_a, _w, c) in enumerate(code) if c.startswith("the added names"))
    code[i] = (code[i][0], (15 << 26) | (11 << 16) | hi, "lui t3: the added names' table")
    code[i + 1] = (code[i + 1][0], (9 << 26) | (11 << 21) | (11 << 16) | lo, "addiu t3")
    words = list(code)
    words += [(table + 4 * k, p, "added name") for k, p in enumerate(ptrs)] + [(table + 4 * len(ptrs), 0, "end")]
    words += [(strings + k, int.from_bytes(blob[k:k + 4], "little"), "names") for k in range(0, len(blob), 4)]
    return words, [(C.UNLOCK_FN, C._j_word(CAVE), "j unlock-added cave"), (C.UNLOCK_FN + 4, 0, "nop")]
