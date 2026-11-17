"""Second Showdown lineup: a second page on the Showdown character select, L1 / R1 to pan between the two.

How the select screen works (SLUS_205.00, read from the running game; 05_Docs/RESEARCH/UNKNOWNS.md 3g):
  * Its character list is postermultiview.swui's <FONTGRIDDATA:y:<code>:<n>> lines (parser 0x00107408: one 48-byte
    entry per line; the number n goes to the entry's byte +41 and is otherwise unused). The lineup "photo" is built
    ONCE, when the front end builds the screen (0x0013EF20 -> 0x0013F230): the background halves
    ui_characterlineupl24b / r24b are loaded and every unlocked character's ui_mphoto_<code> is copied into them at
    its hotspot, then made two textures (CompPhotoL / CompPhotoR) shown by the widgets photoleft / photoright.
  * The pointer is free (analog); 0x001352A0 picks the nearest hotspot item, each item's point from 0x001350D0.
  * Textures take video memory from a per-group allocator that only grows (0x003BE4E0): the lineup must not be built
    again on every switch.

The mod:
  * the parser keeps the number: entry +42 = the character's lineup (0 first, 1 second);
  * the build draws only the lineup being built (a filter where it tests the lock byte);
  * at the screen's creation, after the game's own build (lineup 1), lineup 2 is built once the same way, and the
    four textures are kept; L1 / R1 (any controller) pan: the photo slides out, the widgets are given the other
    lineup's pair, it slides in -- the "Move between books" sound (id 283: snd_50 code 1, Move1/2/3 L+R);
  * the nearest-hotspot pick skips the other lineup's characters;
  * a second background (ui_characterlineup2l24b / r24b) is used for lineup 2 when the disc has one.
Nothing is done when no character is on lineup 2: the screen is the game's own.
"""
from .codemods import Asm, _r

DATA = 0x000FCC00
# +0 lineup shown, +4 the screen, +8 request (lineup + 1), +12 frames, +16 L1/R1 held last frame, +20 switches,
# +24/+28 lineup 1 L/R textures, +32/+36 lineup 2 L/R, +40 lineup 2 exists, +44..+56 photo widgets (the select
# screen's pair, then the lineup layout's), +60 pan frames left, +64 built, +68 pan direction (+1 / -1),
# +72/+76 the pair's x, +80 lineup to show at the pan's middle, +84 own background for lineup 2 (set by the build)
DATA_SIZE = 0x60
DRAW, PICK, CREATE, BUILD2, INPUT, BGL, BGR = (0x000FB000, 0x000FB080, 0x000FB100, 0x000FB200, 0x000FB400,
                                               0x000FB700, 0x000FB740)
STRINGS = 0x000FB780

DRAW_SITE = 0x0013F3D8           # lw v0,0(v1) / lbu s1,41(v0): the build's "is this one drawn?"
PICK_SITE, PICK_FN = 0x00135380, 0x001350D0
CREATE_SITE, COMPOSITE = 0x0013F1C4, 0x0013F230
PARSER_SITE = 0x001074D0         # sltu a0,zero,a0 (the number made 0/1) -> sb a0,42(s5)
INPUT_SLOT, INPUT_FN = 0x0050358C, 0x00133950
BG_L_SITE, BG_R_SITE, LOAD_TEX = 0x0013F26C, 0x0013F29C, 0x003B3088
FIND = 0x00104D18
S_LEFT, S_RIGHT = 0x00518319, 0x00518323          # "photoleft" / "photoright"
ENTRY_OF = 0x00112980
SOUND_FN, SOUND_OBJ, MOVE_ID = 0x0027F9B8, 0x004F7E8C, 283
PADS, PAD_STRIDE, PAD_HELD = 0x0073B780, 448, 0x17C
L1, R1 = 0x0004, 0x0008
PAN_FRAMES, PAN_WIDTH = 12, 560.0                # 6 out, 6 in; the photo's width in layout units (2 x 280)
BG2 = ("ui_characterlineup2l24b", "ui_characterlineup2r24b")

# the vanilla words at every hooked place (checked before building)
VANILLA = {DRAW_SITE: 0x8C620000, DRAW_SITE + 4: 0x90510029, PICK_SITE: 0x0C04D434, CREATE_SITE: 0x0C04FC8C,
           PARSER_SITE: 0x0004202B, INPUT_SLOT: INPUT_FN, BG_L_SITE: 0x0C0ECC22, BG_R_SITE: 0x0C0ECC22}


def _jal(t):
    return 0x0C000000 | ((t >> 2) & 0x3FFFFFF)


def _sll(a, rd, rt, sa, c=""):
    a.raw((_r(rt) << 16) | (_r(rd) << 11) | (sa << 6), c)


def draw_cave():
    a = Asm(DRAW)
    a.lbu('s1', 41, 'v0', 'locked?')
    a.bne('s1', 'zero', 'ret')
    a.lbu('t0', 42, 'v0', 'its lineup')
    a.la('t1', DATA)
    a.lw('t2', 0, 't1', 'the lineup being built')
    a.beq('t0', 't2', 'ret')
    a.nop()
    a.addiu('s1', 'zero', 1, 'the other lineup: not drawn')
    a.label('ret')
    a.jr('ra')
    a.nop()
    return a.assemble()


def pick_cave():
    a = Asm(PICK)
    a.addiu('sp', 'sp', -48)
    a.sd('ra', 0, 'sp')
    a.sd('a0', 8, 'sp')
    a.sd('a1', 16, 'sp')
    a.jal(PICK_FN, "the item's point, as the game works it out")
    a.nop()
    a.la('t9', DATA)
    a.lw('t0', 40, 't9')
    a.beq('t0', 'zero', 'done', 'no lineup 2: nothing to skip')
    a.nop()
    a.ld('a1', 16, 'sp')
    a.lw('a0', 1132, 'a1', 'the list')
    a.jal(ENTRY_OF)
    a.addu('a1', 's0', 'zero', "the item's index (the caller loops in s0)")
    a.beq('v0', 'zero', 'done')
    a.nop()
    a.lbu('t1', 42, 'v0')
    a.la('t9', DATA)
    a.lw('t0', 0, 't9')
    a.beq('t1', 't0', 'done')
    a.nop()
    a.ld('a0', 8, 'sp')
    a.lui('t2', 0x4E6E, 'the other lineup: a point far away, never the nearest')
    a.sw('t2', 0, 'a0')
    a.sw('t2', 4, 'a0')
    a.sw('t2', 8, 'a0')
    a.label('done')
    a.ld('ra', 0, 'sp')
    a.jr('ra')
    a.addiu('sp', 'sp', 48)
    return a.assemble()


def create_cave():
    """The screen's creation: the game's own build (lineup 1), then lineup 2's."""
    a = Asm(CREATE)
    a.addiu('sp', 'sp', -32)
    a.sd('ra', 0, 'sp')
    a.sd('a0', 8, 'sp')
    a.la('t9', DATA)
    a.sw('a0', 4, 't9')
    a.sw('zero', 0, 't9')
    a.sw('zero', 40, 't9')
    a.sw('zero', 60, 't9')
    a.sw('zero', 64, 't9')
    a.jal(COMPOSITE, "the game's own build: lineup 1")
    a.nop()
    a.jal(BUILD2, "lineup 2, once, where the game builds")
    a.nop()
    a.ld('ra', 0, 'sp')
    a.jr('ra')
    a.addiu('sp', 'sp', 32)
    return a.assemble()


def build2_cave():
    a = Asm(BUILD2)
    a.addiu('sp', 'sp', -48)
    a.sd('ra', 0, 'sp')
    a.sd('s0', 8, 'sp')
    a.sd('s1', 16, 'sp')
    a.la('s1', DATA)
    a.addiu('t0', 'zero', 1)
    a.sw('t0', 64, 's1')
    a.lw('s0', 4, 's1')
    a.beq('s0', 'zero', 'out')
    a.nop()
    for k, (scr, name) in enumerate(((28, S_LEFT), (28, S_RIGHT), (80, S_LEFT), (80, S_RIGHT))):
        a.lw('a0', scr, 's0')
        a.la('a1', name)
        a.jal(FIND)
        a.nop()
        a.sw('v0', 44 + 4 * k, 's1')
    for k, off in enumerate((24, 28)):
        a.lw('t0', 44 + 4 * k, 's1')
        a.lw('t1', 304, 't0')
        a.sw('t1', off, 's1', "lineup 1's picture")
        a.lw('t1', 76, 't0')
        a.sw('t1', 72 + 4 * k, 's1', 'its x')
    # anyone on lineup 2?
    a.lw('t0', 92, 's0', 'the list')
    a.lw('t1', 332, 't0')
    a.lw('t2', 336, 't0')
    a.andi('t2', 't2', 0xFFFF)
    a.label('scan')
    a.beq('t2', 'zero', 'out')
    a.nop()
    a.lw('t3', 0, 't1')
    a.lbu('t3', 42, 't3')
    a.bne('t3', 'zero', 'has2')
    a.addiu('t1', 't1', 4)
    a.beq('zero', 'zero', 'scan')
    a.addiu('t2', 't2', -1)
    a.label('has2')
    a.addiu('t0', 'zero', 1)
    a.sw('t0', 40, 's1')
    a.sw('t0', 0, 's1', 'build lineup 2')
    a.jal(COMPOSITE)
    a.addu('a0', 's0', 'zero')
    for k, off in enumerate((32, 36)):
        a.lw('t0', 44 + 4 * k, 's1')
        a.lw('t1', 304, 't0')
        a.sw('t1', off, 's1', "lineup 2's picture")
    a.sw('zero', 0, 's1', 'lineup 1 shown')
    a.lw('t1', 24, 's1')
    a.lw('t2', 28, 's1')
    for k, reg in enumerate(('t1', 't2', 't1', 't2')):
        a.lw('t0', 44 + 4 * k, 's1')
        a.sw(reg, 304, 't0')
    a.label('out')
    a.ld('s1', 16, 'sp')
    a.ld('s0', 8, 'sp')
    a.ld('ra', 0, 'sp')
    a.jr('ra')
    a.addiu('sp', 'sp', 48)
    return a.assemble()


def input_cave():
    a = Asm(INPUT)
    a.addiu('sp', 'sp', -48)
    a.sd('ra', 0, 'sp')
    a.sd('a0', 8, 'sp')
    a.jal(INPUT_FN, "the select screen's own input")
    a.nop()
    a.sd('v0', 16, 'sp')
    a.la('t9', DATA)
    a.lw('t0', 12, 't9')
    a.addiu('t0', 't0', 1)
    a.sw('t0', 12, 't9')
    a.lw('t0', 40, 't9')
    a.beq('t0', 'zero', 'done', 'no lineup 2: the game alone')
    a.nop()
    # L1 / R1 on any controller (one not plugged in reads nothing held)
    a.la('t4', PADS)
    a.lw('t5', PAD_HELD, 't4')
    for k in (1, 2, 3):
        a.lw('t6', PAD_HELD + k * PAD_STRIDE, 't4')
        a.or_('t5', 't5', 't6')
    a.andi('t5', 't5', L1 | R1)
    a.lw('t6', 16, 't9')
    a.sw('t5', 16, 't9')
    a.nor('t6', 't6', 'zero')
    a.and_('t6', 't6', 't5', 'newly pressed')
    a.lw('t0', 60, 't9')
    a.bne('t0', 'zero', 'pan', 'mid-pan: presses wait')
    a.nop()
    a.andi('t7', 't6', L1)
    a.bne('t7', 'zero', 'want')
    a.addu('t8', 'zero', 'zero', 'L1: lineup 1')
    a.andi('t7', 't6', R1)
    a.beq('t7', 'zero', 'done')
    a.addiu('t8', 'zero', 1, 'R1: lineup 2')
    a.label('want')
    a.lw('t1', 0, 't9')
    a.beq('t1', 't8', 'done', 'already there')
    a.nop()
    a.sw('t8', 80, 't9')
    a.addiu('t0', 'zero', PAN_FRAMES)
    a.sw('t0', 60, 't9')
    a.subu('t1', 't8', 't1', '+1 to lineup 2 (slides left), -1 to lineup 1 (slides right)')
    a.sw('t1', 68, 't9')
    a.lw('t1', 20, 't9')
    a.addiu('t1', 't1', 1)
    a.sw('t1', 20, 't9')
    a.la('t0', SOUND_OBJ)
    a.lw('a0', 0, 't0')
    a.jal(SOUND_FN, 'Move between books')
    a.addiu('a1', 'zero', MOVE_ID)
    a.la('t9', DATA)
    # one frame of the pan: k = frames left (12..1)
    a.label('pan')
    a.lw('t0', 60, 't9')
    a.addiu('t1', 'zero', PAN_FRAMES // 2)
    a.bne('t0', 't1', 'no_swap')
    a.nop()
    a.lw('t2', 80, 't9', 'the middle: the other lineup')
    a.sw('t2', 0, 't9')
    _sll(a, 't2', 't2', 3)
    a.addu('t2', 't2', 't9')
    a.lw('t3', 24, 't2')
    a.lw('t4', 28, 't2')
    for k, reg in enumerate(('t3', 't4', 't3', 't4')):
        a.lw('t5', 44 + 4 * k, 't9')
        a.sw(reg, 304, 't5')
    a.label('no_swap')
    a.lw('t2', 68, 't9', 'direction')
    a.slti('t3', 't0', PAN_FRAMES // 2 + 1)
    a.bne('t3', 'zero', 'in_half')
    a.nop()
    a.addiu('t4', 'zero', PAN_FRAMES + 1, 'out: (13 - k) sixths, against the direction')
    a.subu('t4', 't4', 't0')
    a.beq('zero', 'zero', 'apply')
    a.subu('t2', 'zero', 't2')
    a.label('in_half')
    a.addiu('t4', 't0', -1, 'in: (k - 1) sixths, from the direction')
    a.label('apply')
    a.mtc1('t4', 'f0')
    a.cvt_s_w('f0', 'f0')
    a.li_s('f1', PAN_WIDTH / (PAN_FRAMES // 2), 'at')
    a.mul_s('f0', 'f0', 'f1')
    a.slti('t3', 't2', 0)
    a.beq('t3', 'zero', 'pos')
    a.nop()
    a.neg_s('f0', 'f0')
    a.label('pos')
    for k in (0, 1):
        a.lwc1('f2', 72 + 4 * k, 't9')
        a.add_s('f2', 'f2', 'f0')
        a.lw('t5', 44 + 4 * k, 't9')
        a.swc1('f2', 76, 't5')
    a.lw('t0', 60, 't9')
    a.addiu('t0', 't0', -1)
    a.sw('t0', 60, 't9')
    a.label('done')
    a.ld('v0', 16, 'sp')
    a.ld('ra', 0, 'sp')
    a.jr('ra')
    a.addiu('sp', 'sp', 48)
    return a.assemble()


def bg_caves():
    """The background loads: lineup 2's own pictures when the disc has them (DATA+84 set by the build)."""
    out = []
    for cave, k in ((BGL, 0), (BGR, 1)):
        a = Asm(cave)
        a.la('t9', DATA)
        a.lw('t0', 84, 't9')
        a.beq('t0', 'zero', 'go')
        a.lw('t0', 0, 't9')
        a.beq('t0', 'zero', 'go', 'lineup 1: its own')
        a.nop()
        a.la('a0', STRINGS + 32 * k)
        a.label('go')
        a.j(LOAD_TEX)
        a.nop()
        out += a.assemble()
    return out


def strings():
    import struct
    out = []
    for k, s in enumerate(BG2):
        b = s.encode() + b'\0' * (32 - len(s))
        out += [(STRINGS + 32 * k + i, struct.unpack_from('<I', b, i)[0], s if i == 0 else '') for i in range(0, 32, 4)]
    return out


def build(img=None, own_background=False):
    """-> (cave words [(addr, word, comment)], hook words [(addr, word, comment)]). With `img` (the unpacked game),
    every hooked word is checked to be the vanilla one first."""
    if img is not None:
        bad = ['0x%08X is %08X, not %08X' % (a, img.word(a), w) for a, w in VANILLA.items() if img.word(a) != w]
        if bad:
            raise ValueError('second lineup: not the vanilla game here: ' + '; '.join(bad))
    caves = (draw_cave() + pick_cave() + create_cave() + build2_cave() + input_cave() + bg_caves() + strings())
    caves += [(DATA + i, 0, '') for i in range(0, DATA_SIZE, 4)]
    caves = [(a, w, c) for a, w, c in caves if a != DATA + 84] + [(DATA + 84, 1 if own_background else 0, 'own bg 2')]
    hooks = [(DRAW_SITE, _jal(DRAW), 'jal draw filter'), (DRAW_SITE + 4, 0x8C620000, 'lw v0,0(v1) (delay slot)'),
             (PICK_SITE, _jal(PICK), 'jal pick filter'), (CREATE_SITE, _jal(CREATE), 'jal create'),
             (PARSER_SITE, 0xA2A4002A, 'sb a0,42(s5): the lineup number'), (INPUT_SLOT, INPUT, 'input slot'),
             (BG_L_SITE, _jal(BGL), 'jal background L'), (BG_R_SITE, _jal(BGR), 'jal background R')]
    return caves, hooks
