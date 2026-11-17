"""Alternate textures for Showdown players 2-4, per texture (2026-11-16).

How the game does it (SLUS_205.00, read from the code and from a savestate of a match with four gbw):
  * a Showdown creature made for variant n > 0 (0x354920 -> 0x185340) formats "%s%02d" (model name, n + 1) -- "gbw02"
    -- and looks that texture up (0x3B0728; a missing one gives the blank default texture *0x00762088), keeping it in
    the creature (+36);
  * the model draws (0x21CB08 and the skinned one 0x21C790) then bind that ONE texture and draw every part with it,
    skipping the model's own: 0x3B6528(model, 0, 0) / 0x3B66F8(model, m, 0, 0) instead of (model, model+4, 1).
    model: byte 0 the part count, +4 the per-part list of shader pointers, a shader = {texture, block}.
  The game's characters have a single texture and ship <code>02..04.tex; a character made with several textures was
  drawn blank as player 2, 3 or 4.

The mod:
  * creation: the game's own alternate ("gbw02") is kept when there is one. Otherwise "gbw02_0", "gbw02_1" ... are
    looked up (one per texture of the model, in the order its parts first use them; numbers 0..15), into a
    record (8 kept, used in turn) -- all 16 numbers are tried and a missing one keeps that texture the model's own, so
    only changed textures take video memory (all of them at 256 x 256 for three players ran past the 4 MB of video
    memory: the screen filled with noise); the creature keeps the record instead of a texture -- or nothing, when
    none is found: the model then draws with its own textures (the blank-texture fix);
  * both draws: a creature holding a record draws its model with a part list made once per model from the record:
    each part's texture swapped for its alternate (the same order), the rest kept.
Every texture is loaded when the creature is made; the draws only read memory.
"""
from .codemods import Asm, _r

CREATE_SITE = 0x00185384       # sw v0,36(s0) / ld s0,32(sp)
CREATE_RESUME = 0x0018538C
DRAW1_SITE = 0x0021CCA0        # beq s1,zero,0x0021CD50 / lui v0,0x7000   (0x21CB08)
DRAW2_SITE = 0x0021C968        # beq s2,zero,0x0021CA18 / lui v0,0x7000   (0x21C790, skinned)
VANILLA = {0x00185384: 0xAE020024, 0x00185388: 0xDFB00020,
           DRAW1_SITE: 0x1220002B, DRAW1_SITE + 4: 0x3C027000,
           DRAW2_SITE: 0x1240002B, DRAW2_SITE + 4: 0x3C027000}
SPRINTF, LOOKUP, DRAW_MODEL, DRAW_SKINNED = 0x0025DF88, 0x003B0728, 0x003B6528, 0x003B66F8
DEFAULT_TEX_HI, DEFAULT_TEX_LO = 0x0076, 0x2088

RECS, REC_SIZE, NRECS = 0x000F6000, 0x400, 8
# record: +0 textures found, +4 models made, +8 textures[16], +72 (model, list) x 2, +128 + 384 m: list[32], shaders
# [32] (8 bytes), +896 the textures seen in the model being made [16]
DATA = RECS + REC_SIZE * NRECS          # +0 next record, +16 name buffer (32), +64 "%s_%d"
CODE = 0x000FA000
MAX_TEX, MAX_PARTS = 16, 32


def _sll(a, rd, rt, sa, c=""):
    a.raw((_r(rt) << 16) | (_r(rd) << 11) | (sa << 6), c)


def _sltiu(a, rt, rs, imm, c=""):
    a._i(11, rs, rt, imm, c)


def _slt(a, rd, rs, rt, c=""):
    a._rr(42, rd, rs, rt, c=c)


def _movz(a, rd, rs, rt, c=""):
    a._rr(10, rd, rs, rt, c=c)


def _mov(a, rd, rs, c=""):
    a.addu(rd, rs, "zero", c)


def _code():
    a = Asm(CODE)
    # ---------------------------------------------------------------- creation (from 0x185384)
    a.label("create")
    a.lui("t0", DEFAULT_TEX_HI)
    a.lw("t0", DEFAULT_TEX_LO, "t0", "the blank default texture")
    a.bne("v0", "t0", "store", "the game's own alternate: kept")
    a.nop()
    a.addiu("sp", "sp", -48)
    a.sd("s1", 0, "sp"); a.sd("s2", 8, "sp"); a.sd("s3", 16, "sp"); a.sd("s4", 24, "sp"); a.sd("s5", 32, "sp")
    _mov(a, "s5", "zero", "any found")
    a.la("s4", DATA)
    a.lw("t1", 0, "s4")
    a.addiu("t2", "t1", 1)
    a.sw("t2", 0, "s4", "the next record")
    a.andi("t1", "t1", NRECS - 1)
    _sll(a, "t1", "t1", 10)
    a.la("s1", RECS)
    a.addu("s1", "s1", "t1", "s1 = record")
    _mov(a, "s2", "zero", "k = 0")
    a.lui("s3", DEFAULT_TEX_HI)
    a.lw("s3", DEFAULT_TEX_LO, "s3")
    a.label("cr_loop")
    a.slti("t0", "s2", MAX_TEX)
    a.beq("t0", "zero", "cr_done")
    a.nop()
    a.addiu("a0", "s4", 16, "name buffer")
    a.addiu("a1", "s4", 64, '"%s_%d"')
    a.addiu("a2", "sp", 48, 'the game\'s "<model><nn>"')
    a.jal(SPRINTF)
    _mov(a, "a3", "s2")
    a.addiu("a0", "s4", 16)
    a.addiu("a1", "zero", 1)
    a.jal(LOOKUP)
    _mov(a, "a2", "zero")
    a.xor("t0", "v0", "s3")
    _movz(a, "v0", "zero", "t0", "missing (the blank default): none -- that texture stays the model's own")
    _sll(a, "t0", "s2", 2)
    a.addu("t0", "t0", "s1")
    a.sw("v0", 8, "t0", "textures[k] (0 = keep)")
    a.or_("s5", "s5", "v0")
    a.beq("zero", "zero", "cr_loop")
    a.addiu("s2", "s2", 1)
    a.label("cr_done")
    a.sw("s2", 0, "s1")
    a.sw("zero", 4, "s1", "no model made yet")
    _mov(a, "v0", "s1")
    _movz(a, "v0", "zero", "s5", "none found: nothing (own textures)")
    a.ld("s1", 0, "sp"); a.ld("s2", 8, "sp"); a.ld("s3", 16, "sp"); a.ld("s4", 24, "sp"); a.ld("s5", 32, "sp")
    a.addiu("sp", "sp", 48)
    a.label("store")
    a.sw("v0", 36, "s0")
    a.ld("s0", 32, "sp")
    a.j(CREATE_RESUME)
    a.nop()

    # ---------------------------------------------------------------- draw 1 (0x21CB08: s1 texture, s2 model)
    a.label("draw1")
    a.beq("s1", "zero", "d1_norm")
    a.lui("t0", RECS >> 16)
    a.ori("t0", "t0", RECS & 0xFFFF)
    a.subu("t0", "s1", "t0")
    _sltiu(a, "t1", "t0", REC_SIZE * NRECS)
    a.bne("t1", "zero", "d1_mark")
    a.nop()
    a.j(DRAW1_SITE + 8, "a texture: the game's way")
    a.nop()
    a.label("d1_norm")
    a.j(0x0021CD50)
    a.nop()
    a.label("d1_mark")
    _mov(a, "a0", "s1")
    a.jal("build")
    _mov(a, "a1", "s2")
    _mov(a, "a0", "s2")
    _mov(a, "a1", "v0")
    a.jal(DRAW_MODEL)
    a.addiu("a2", "zero", 1)
    a.j(0x0021CD60)
    a.nop()

    # ---------------------------------------------------------------- draw 2 (0x21C790: s2 texture, *s3 model)
    a.label("draw2")
    a.beq("s2", "zero", "d2_norm")
    a.lui("t0", RECS >> 16)
    a.ori("t0", "t0", RECS & 0xFFFF)
    a.subu("t0", "s2", "t0")
    _sltiu(a, "t1", "t0", REC_SIZE * NRECS)
    a.bne("t1", "zero", "d2_mark")
    a.nop()
    a.j(DRAW2_SITE + 8)
    a.nop()
    a.label("d2_norm")
    a.j(0x0021CA18)
    a.nop()
    a.label("d2_mark")
    _mov(a, "a0", "s2")
    a.jal("build")
    a.lw("a1", 0, "s3")
    a.lw("a0", 0, "s3")
    a.lw("a1", 16, "sp")
    _mov(a, "a2", "v0")
    a.jal(DRAW_SKINNED)
    a.addiu("a3", "zero", 1)
    a.j(0x0021CA2C)
    a.nop()

    # ---------------------------------------------------------------- build(a0 record, a1 model) -> v0 part list
    a.label("build")
    a.lw("t0", 4, "a0", "models made")
    _mov(a, "t1", "zero")
    a.label("bl_find")
    a.beq("t1", "t0", "bl_new")
    _sll(a, "t2", "t1", 4)
    a.addu("t2", "t2", "a0")
    a.lw("t3", 72, "t2")
    a.bne("t3", "a1", "bl_next")
    a.nop()
    a.jr("ra")
    a.lw("v0", 76, "t2", "made before")
    a.label("bl_next")
    a.beq("zero", "zero", "bl_find")
    a.addiu("t1", "t1", 1)
    a.label("bl_new")
    a.slti("t2", "t0", 2)
    a.beq("t2", "zero", "bl_fallback")
    a.lbu("t4", 0, "a1", "parts")
    a.slti("t2", "t4", MAX_PARTS + 1)
    a.beq("t2", "zero", "bl_fallback")
    _sll(a, "t3", "t0", 8)
    _sll(a, "t5", "t0", 7)
    a.addu("t3", "t3", "t5")
    a.addu("t3", "t3", "a0")
    a.addiu("t3", "t3", 128, "t3 = list")
    a.addiu("t5", "t3", 128, "t5 = shaders")
    a.lw("t6", 4, "a1", "the model's own list")
    _mov(a, "t7", "zero", "i")
    a.lw("t8", 0, "a0", "textures found")
    _mov(a, "v1", "zero", "seen")
    a.label("bl_part")
    a.beq("t7", "t4", "bl_done")
    _sll(a, "t9", "t7", 2)
    a.addu("t9", "t9", "t6")
    a.lw("t9", 0, "t9", "shader")
    a.lw("t2", 0, "t9", "its texture")
    _mov(a, "t1", "zero")
    a.label("bl_seek")
    a.beq("t1", "v1", "bl_add")
    _sll(a, "t0", "t1", 2)
    a.addu("t0", "t0", "a0")
    a.lw("t0", 896, "t0")
    a.beq("t0", "t2", "bl_have")
    a.nop()
    a.beq("zero", "zero", "bl_seek")
    a.addiu("t1", "t1", 1)
    a.label("bl_add")
    a.slti("t0", "v1", MAX_TEX)
    a.beq("t0", "zero", "bl_have")
    _sll(a, "t0", "v1", 2)
    a.addu("t0", "t0", "a0")
    a.sw("t2", 896, "t0")
    a.addiu("v1", "v1", 1)
    a.label("bl_have")
    _slt(a, "t0", "t1", "t8")
    a.beq("t0", "zero", "bl_keep", "no alternate for this one")
    _sll(a, "t0", "t1", 2)
    a.addu("t0", "t0", "a0")
    a.lw("t0", 8, "t0", "its alternate (0: none)")
    a._rr(11, "t2", "t0", "t0", c="movn t2,t0,t0")
    a.label("bl_keep")
    _sll(a, "t0", "t7", 3)
    a.addu("t0", "t0", "t5")
    a.sw("t2", 0, "t0")
    a.lw("t1", 4, "t9")
    a.sw("t1", 4, "t0")
    _sll(a, "t1", "t7", 2)
    a.addu("t1", "t1", "t3")
    a.sw("t0", 0, "t1")
    a.beq("zero", "zero", "bl_part")
    a.addiu("t7", "t7", 1)
    a.label("bl_done")
    a.lw("t0", 4, "a0")
    _sll(a, "t1", "t0", 4)
    a.addu("t1", "t1", "a0")
    a.sw("a1", 72, "t1")
    a.sw("t3", 76, "t1")
    a.addiu("t0", "t0", 1)
    a.sw("t0", 4, "a0")
    a.jr("ra")
    _mov(a, "v0", "t3")
    a.label("bl_fallback")
    a.jr("ra")
    a.lw("v0", 4, "a1", "the model's own list")
    return a


def build(img=None):
    """-> (cave words [(addr, word, comment)], hook words [(addr, word, comment)])."""
    if img is not None:
        bad = ['0x%08X is %08X, not %08X' % (x, img.word(x), w) for x, w in VANILLA.items() if img.word(x) != w]
        if bad:
            raise ValueError('alternate textures: not the vanilla game here: ' + '; '.join(bad))
    a = _code()
    caves = a.assemble()
    fmt = b'%s_%d\0\0\0'
    caves += [(DATA + i, 0, '') for i in range(0, 64, 4)]
    caves += [(DATA + 64 + i, int.from_bytes(fmt[i:i + 4], 'little'), '"%s_%d"' if i == 0 else '')
              for i in range(0, len(fmt), 4)]
    j = lambda t: 0x08000000 | ((t >> 2) & 0x03FFFFFF)
    hooks = [(CREATE_SITE, j(a.labels['create']), 'j alternate textures (creation)'), (CREATE_SITE + 4, 0, 'nop'),
             (DRAW1_SITE, j(a.labels['draw1']), 'j alternate textures (draw)'),
             (DRAW2_SITE, j(a.labels['draw2']), 'j alternate textures (skinned draw)')]
    return caves, hooks
