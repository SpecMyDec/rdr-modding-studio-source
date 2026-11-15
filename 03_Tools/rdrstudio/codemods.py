"""Code Mods: noclip, freeze world and camera lock, built into SLUS_205.00 and the Cheats menu.

CREDIT -- this module is a port of the work of community research ("community research v7"): the whole
idea (unpack the aPLib+XOR executable, find the Mover / pad / renderer hooks, run code in the unused
RAM block 0x000F0000, bake the installer into the boot stub, add rows to the game's Cheats page) and
every address below come from its tools (make_noclip.py, make_elf_patch.py, make_menu_patch.py,
menu_layout.py, asm.py) and its write-up HOW_IT_WAS_BUILT.md.  What is ours: the port into Studio,
the feature checklist (any subset of the three mods), the address check against OUR OWN unpack of
the vanilla executable (`verify_addresses`), and the build-time safety rules below.

How it works (details in 05_Docs/CODEMODS.md):
  * SLUS_205.00 is one PT_LOAD segment holding a boot stub + the aPLib-packed game.  The stub unpacks
    the game to 0x00100000 and then does `j 0x00100008` at 0x00800020.  We append an installer after
    the ELF's section headers, grow the segment to cover it and point that jump at it.  At boot the
    installer copies our code caves into 0x000FE000.., writes the hook words into the freshly unpacked
    game, flushes the caches and jumps to 0x00100008.  The packed game itself is NOT touched, so no
    aPLib encoder is needed.
  * The patched ELF is larger than its disc extent, so it is appended at the end of the ISO and the
    ISO 9660 root record (+ volume size) is repointed.  The UDF bridge still lists the original.
  * cheats.swui (the Cheats page layout) is replaced in swat.dat and all 61 level archives, in place,
    inside its existing slot, so no archive changes size.

Safety: patch_iso() only ever writes to a NEW file (it refuses to write over its source, and refuses
anything under 01_Vanilla).  Nothing here touches the network or runs the third-party tools.
"""
import os
import shutil
import struct
import zlib

from . import dave as _dave

# ---------------------------------------------------------------- features
FEATURES = [
    # key, title, one-line help, controls
    ("noclip", "Noclip", "Fly through the world: no gravity, no collision, camera follows.",
     "L3+R3 or Cheats > Noclip. Stick flies, X up, Square down, R2 fast, L2 slow."),
    ("freeze", "Freeze World", "Every character except you stops moving, animating and shooting.",
     "R3+Triangle or Cheats > Freeze."),
    ("camlock", "Camera Lock", "The view stays where it is while you walk/fly away.",
     "R3+Circle or Cheats > Cam Lock."),
    # added 2026-11-08 (the reviewer) -- modding aids; new keys go at the END so older mod packs keep their bit positions
    ("cheatsmenu", "Cheats Menu Unlock", "Pause > Cheats is always there on ANY save: every built-in cheat is owned (none switched on).",
     "Nothing to press: Pause > Cheats. (Show Player Position / Show AI Info stay hidden: the game never implements them.)"),
    ("slowmo", "Slow-Mo", "The whole game runs at quarter speed (frame time scaled), to check animations and timing.",
     "R3+Square or Cheats > Slow-Mo."),
    # Research instruments (2026-11-08, the reviewer) -- measuring tools for people reading the game's code, NOT cheats.  See codemods_research.py.
    ("hitlog", "Function-Hit Logger (Research)", "Hook chosen game functions: counts every call and records the arguments, the caller and the return value (plus the last 128 calls). Read it with scripts/rdr_hitlog.py.",
     "No controls, no row. Which functions: 02_Modded/research_config.json (\"hitlog\")."),
    ("memwatch", "Memory Watch (Research)", "The game itself evaluates a table of watch entries every frame (pointer chains included) into a results block; scripts/rdr_watch.py draws them live over PINE.",
     "No controls, no row. Which values: 02_Modded/research_config.json (\"watch\")."),
    # Combat mods (2026-11-12, deep dive) -- always on while the disc is built with them; no controls, no row.
    ("infammo", "Infinite Ammo", "Your gun never uses up a round: the magazine and the reserve count stay where they are.",
     "Nothing to press: always on for this disc."),
    ("godmode", "God Mode", "Red takes no damage from anything that goes through the game's damage routine (bullets, melee, explosions).",
     "Nothing to press: always on for this disc."),
    ("onehitkill", "One-Hit Kills", "Anything that is not Red loses all its health on the first hit it takes.",
     "Nothing to press: always on for this disc. (Without God Mode, Red still takes normal damage.)"),
    # added 2026-11-12 (deep dive); again at the END
    ("fastred", "Fast Red", "Red moves one and a half times as fast on foot. Everyone else and the rest of the world keep their normal speed; walls still stop him.",
     "Nothing to press: always on for this disc."),
    # added 2026-11-13 (deep dive); again at the END
    ("unlockall", "Unlock Everything", "Every Showdown character and stage, every bounty journal page and every built-in cheat counts as unlocked, on ANY save. Nothing is written to the memory card.",
     "Nothing to press: always on for this disc. (Show Player Position / Show AI Info stay hidden: the game never implements them.)"),
    # added 2026-11-13 (deep dive, damage scaling in the same cave as God Mode / One-Hit Kills); again at the END
    ("dmgx2", "Double Damage", "Everything that is not Red takes twice the damage from every hit (bullets, melee, explosions): enemies go down in about half the shots.",
     "Nothing to press: always on for this disc. (One-Hit Kills wins if both are ticked.)"),
    ("hardmode", "Hard Mode", "Red takes twice the damage from every hit. Enemies are unchanged.",
     "Nothing to press: always on for this disc. (God Mode wins if both are ticked.)"),
    ("moneyx5", "Money x5", "Every dollar Red picks up or is given counts five times (coins, money bags, rewards, the Big Money cheat). Spending is unchanged.",
     "Nothing to press: always on for this disc."),
]
KEYS = [f[0] for f in FEATURES]
RESEARCH_KEYS = ["hitlog", "memwatch"]
CORE_KEYS = [k for k in KEYS if k not in RESEARCH_KEYS]
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "02_Modded", "research_config.json")


def research_config():
    from . import codemods_research as R
    return R.load_config(os.environ.get("RDR_RESEARCH_CONFIG") or CONFIG_PATH)

# Row labels on the Cheats page.  Kept short on purpose: the page layout must deflate into the
# smallest existing slot (512 bytes) and zlib needs these to get there (the original research used Zopfli instead).
ROW_LABEL = {"noclip": "Noclip", "freeze": "Freeze", "camlock": "Cam Lock", "slowmo": "Slow-Mo"}
ROW_KEYS = [k for k in KEYS if k in ROW_LABEL]          # mods that get a row on the Cheats page

BUTTONS = {"L2": 0x0001, "R2": 0x0002, "L1": 0x0004, "R1": 0x0008, "TRIANGLE": 0x0010, "CIRCLE": 0x0020,
           "CROSS": 0x0040, "SQUARE": 0x0080, "SELECT": 0x0100, "L3": 0x0200, "R3": 0x0400, "START": 0x0800,
           "UP": 0x1000, "RIGHT": 0x2000, "DOWN": 0x4000, "LEFT": 0x8000}

# ---------------------------------------------------------------- constants (all from the original research, verified
# against our own unpack by verify_addresses())
IMG_BASE = 0x100000
CAVE_MOVER, CAVE_INPUT, CAVE_FREEZE, CAVE_CAM = 0x000FE000, 0x000FE400, 0x000FE800, 0x000FE900
CAVE_MENU_INIT, CAVE_MENU_CHANGE, MENU_STRINGS, DATA = 0x000FEA00, 0x000FEC00, 0x000FEE00, 0x000FEF00
MOVER_UPDATE = 0x002C3E10          # Mover::Update(Mover), every character
INPUT_SITE = 0x001433B8            # jal 0x001431B0 (pad update)
PAD_UPDATE = 0x001431B0
FREEZE_HOOK = 0x00268628           # per-character update
CAM_SITE = 0x00152ED0              # jal 0x003BFBB8 (hand camera matrix to the renderer)
CAM_SETTER = 0x003BFBB8
SHIFT_FN = 0x00269C08              # per-character world shift (components, &vec)
PLAYER_PTR = 0x0079E240            # pointer to player 1's creature
CAMERA_PTR = 0x0079EEE4            # pointer to the active camera view
PAD1 = 0x0073B780
FRAME_TIME = 0x004F7DF0
MENU_VT_SLOT, MENU_INIT_FN = 0x005027BC, 0x001228C0
MENU_FIND, MENU_HANDLER_SITE = 0x00104E88, 0x001226D8
MENU_HANDLER_WORDS = (0x56220004, 0xDFB00000)
STUB_JUMP, GAME_ENTRY = 0x00800020, 0x00100008
SEG_VA, SEG_OFF = 0x00800000, 0x1000
INSTALLER_OFF = 0x1BE500
INSTALLER_VA = SEG_VA + INSTALLER_OFF - SEG_OFF            # 0x009BD500
ORIG_FILESZ, ORIG_ELF_SIZE = 0x1BD3B3, 0x1BE4E4
TAG = b"CMOD"                       # + u32 feature mask, after the installer table (our marker)

# controls (the original research's defaults)
TOGGLE = {"noclip": "L3+R3", "freeze": "R3+TRIANGLE", "camlock": "R3+CIRCLE", "slowmo": "R3+SQUARE"}
SLOW_FACTOR = 0.25
# extra caves (2026-11-08), in the same free 0x000F0000 block
CAVE_OWN, CAVE_SLOW = 0x000FD000, 0x000FD200
CAVE_DMG = 0x000FD400              # god mode / one-hit kills (one shared cave: both hook the same two words)
AMMO_SITE = 0x0039BBC8             # sw <count>,0x24(<weapon instance>): the player gun's ammo write-back after a shot
DMG_SITE = 0x0021ADDC              # Health::TakeDamage: sub.s f0,f0,f20 / swc1 f0,16(s0)   (hp -= damage)
DMG_WORDS = (0x46140001, 0xE6000010)
DAMAGE_KEYS = ("godmode", "onehitkill", "dmgx2", "hardmode")    # all four share the one damage cave
CAVE_SPEED = 0x000FD600            # fast Red: wraps the three calls of Mover::Update
SPEED_SAVE = CAVE_SPEED + 0xC0     # frame time (f32) at +0, return address (u64) at +8, while the player's mover runs
SPEED_SITES = (0x00184ABC, 0x00184C7C, 0x00268ACC)      # every `jal Mover::Update` in the game
SPEED_FACTOR = 1.5
CAVE_UNLOCK = 0x000FD800           # unlock everything: wraps the one "is unlockable N unlocked?" test
UNLOCK_FN = 0x00183028             # (unlockables manager, index) -> 1 if bit N is set in the profile or the session field
UNLOCK_FN_WORDS = (0x0080382D, 0x00051143)     # move a3,a0 / sra v0,a1,5
CAVE_MONEY = 0x000FDA00            # money x5: wraps the wallet's one "add money" routine
WALLET_FN = 0x00160198             # (wallet = *(player+0x8A8), amount, kind): every income AND spending goes through it;
                                   # money itself is wallet+0x587C.  3 callers: pickups 0x0035EE6C (kind 0), the player's
                                   # give-money virtual 0x0036F2B8 (kind 1: Big Money, rewards) and take-money 0x0036F2EC
                                   # (kind 1, amount negated = spending)
WALLET_FN_WORDS = (0x27BDFFD0, 0x00A0382D)     # addiu sp,sp,-48 / daddu a3,a1,zero
MONEY_FN = 0x0036F1A0              # player class vtable 0x00509468 slot 0x174: (player, amount, a2, a3); Big Money calls it with 10000
MONEY_VT_SLOT = 0x005095DC
MONEY_FACTOR = 5
OWN_FN = 0x00122248                # (name) -> is this cheat unlockable owned?  one call per Cheats row
OWN_FN_WORDS = (0x27BDFFF0, 0x0080282D)
OWN_MENU = 0x001222A8              # pause menu: does the player own ANY cheat?  (Cheats entry shown if non-zero)
OWN_MENU_WORDS = (0x27BDFFE0, 0x3C020074)
SLOW_FN = 0x0025F960               # per-frame timer: computes the frame time at FRAME_TIME
SLOW_FN_WORDS = (0x27BDFFD0, 0x3C020075)
CTRL = dict(up="CROSS", down="SQUARE", fast="R2", slow="L2", speed=8.0, fast_mult=4.0, slow_mult=0.25,
            deadzone=24, grace=4)

# data block offsets
D_ENABLED, D_PREV, D_TOGGLES, D_MOVER_CALLS = 0x00, 0x04, 0x08, 0x0C
D_HELD, D_LX, D_LY, D_INPUT_CALLS, D_FLY_FRAMES = 0x10, 0x14, 0x15, 0x18, 0x1C
D_HEART, D_FREEZE, D_FREEZE_TOGGLES, D_FROZEN_SKIPS = 0x20, 0x24, 0x28, 0x2C
D_CAMLOCK, D_CAMLOCK_TOGGLES, D_CAM_KEY, D_CAM_FRAMES, D_CAM_MTX = 0x30, 0x34, 0x38, 0x3C, 0x40
D_MENU_W = 0x74
DATA_SIZE = 0x80
D_SLOW, D_SLOW_TOGGLES = 0x90, 0x94         # beyond the menu widget slots (0x74..0x83); zeroed only when slowmo is built
DATA_SIZE_SLOW = 0xA0
FLAG_OFF = {"noclip": D_ENABLED, "freeze": D_FREEZE, "camlock": D_CAMLOCK, "slowmo": D_SLOW}

# ---------------------------------------------------------------- assembler (the original research's asm.py)
REGS = {n: i for i, n in enumerate(
    ["zero", "at", "v0", "v1", "a0", "a1", "a2", "a3", "t0", "t1", "t2", "t3", "t4", "t5", "t6", "t7",
     "s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7", "t8", "t9", "k0", "k1", "gp", "sp", "fp", "ra"])}


def _r(x):
    return REGS[x]


def _f(x):
    assert x.startswith("f")
    return int(x[1:])


def _fbits(v):
    return struct.unpack("<I", struct.pack("<f", v))[0]


def hi_lo(addr):
    lo = addr & 0xFFFF
    hi = (addr >> 16) & 0xFFFF
    if lo & 0x8000:
        hi = (hi + 1) & 0xFFFF
    return hi, lo


class Asm:
    """Tiny two-pass MIPS (R5900 subset) assembler.  Items -> list of (addr, word, comment)."""

    def __init__(self, origin):
        self.origin = origin
        self.items = []
        self.labels = {}

    @property
    def pc(self):
        return self.origin + 4 * len(self.items)

    def label(self, name):
        assert name not in self.labels, name
        self.labels[name] = self.pc

    def _emit(self, fn, comment=""):
        self.items.append((fn, comment))

    def raw(self, word, comment=""):
        self._emit(lambda pc, w=word: w, comment)

    def _i(self, op, rs, rt, imm, c):
        self._emit(lambda pc: (op << 26) | (_r(rs) << 21) | (_r(rt) << 16) | (imm & 0xFFFF), c)

    def _rr(self, fn, rd, rs, rt, sa=0, c=""):
        self._emit(lambda pc: (_r(rs) << 21) | (_r(rt) << 16) | (_r(rd) << 11) | (sa << 6) | fn, c)

    def nop(self, c=""): self.raw(0, c)
    def addiu(self, rt, rs, imm, c=""): self._i(9, rs, rt, imm, c)
    def slti(self, rt, rs, imm, c=""): self._i(10, rs, rt, imm, c)
    def andi(self, rt, rs, imm, c=""): self._i(12, rs, rt, imm, c)
    def ori(self, rt, rs, imm, c=""): self._i(13, rs, rt, imm, c)
    def xori(self, rt, rs, imm, c=""): self._i(14, rs, rt, imm, c)
    def lui(self, rt, imm, c=""): self._i(15, "zero", rt, imm, c)
    def lw(self, rt, off, rs, c=""): self._i(35, rs, rt, off, c)
    def lbu(self, rt, off, rs, c=""): self._i(36, rs, rt, off, c)
    def sw(self, rt, off, rs, c=""): self._i(43, rs, rt, off, c)
    def sb(self, rt, off, rs, c=""): self._i(40, rs, rt, off, c)
    def sd(self, rt, off, rs, c=""): self._i(63, rs, rt, off, c)
    def ld(self, rt, off, rs, c=""): self._i(55, rs, rt, off, c)
    def srl(self, rd, rt, sa, c=""): self._rr(2, rd, "zero", rt, sa, c)
    def addu(self, rd, rs, rt, c=""): self._rr(33, rd, rs, rt, c=c)
    def subu(self, rd, rs, rt, c=""): self._rr(35, rd, rs, rt, c=c)
    def and_(self, rd, rs, rt, c=""): self._rr(36, rd, rs, rt, c=c)
    def or_(self, rd, rs, rt, c=""): self._rr(37, rd, rs, rt, c=c)
    def xor(self, rd, rs, rt, c=""): self._rr(38, rd, rs, rt, c=c)
    def nor(self, rd, rs, rt, c=""): self._rr(39, rd, rs, rt, c=c)
    def movn(self, rd, rs, rt, c=""): self._rr(11, rd, rs, rt, c=c)
    def sra(self, rd, rt, sa, c=""): self._rr(3, rd, "zero", rt, sa, c)
    def jr(self, rs, c=""): self._rr(8, "zero", rs, "zero", c=c)

    def lwc1(self, ft, off, rs, c=""):
        self._emit(lambda pc: (49 << 26) | (_r(rs) << 21) | (_f(ft) << 16) | (off & 0xFFFF), c)

    def swc1(self, ft, off, rs, c=""):
        self._emit(lambda pc: (57 << 26) | (_r(rs) << 21) | (_f(ft) << 16) | (off & 0xFFFF), c)

    def _fpu(self, fn, fd, fs, ft, c):
        self._emit(lambda pc: (17 << 26) | (16 << 21) | (_f(ft) << 16) | (_f(fs) << 11) | (_f(fd) << 6) | fn, c)

    def add_s(self, fd, fs, ft, c=""): self._fpu(0, fd, fs, ft, c)
    def sub_s(self, fd, fs, ft, c=""): self._fpu(1, fd, fs, ft, c)
    def mul_s(self, fd, fs, ft, c=""): self._fpu(2, fd, fs, ft, c)
    def div_s(self, fd, fs, ft, c=""): self._fpu(3, fd, fs, ft, c)
    def mov_s(self, fd, fs, c=""): self._fpu(6, fd, fs, "f0", c)
    def neg_s(self, fd, fs, c=""): self._fpu(7, fd, fs, "f0", c)

    def mtc1(self, rt, fs, c=""):
        self._emit(lambda pc: (17 << 26) | (4 << 21) | (_r(rt) << 16) | (_f(fs) << 11), c)

    def cvt_s_w(self, fd, fs, c=""):
        self._emit(lambda pc: (17 << 26) | (20 << 21) | (_f(fs) << 11) | (_f(fd) << 6) | 32, c)

    def _br(self, op, rs, rt, target, c):
        def enc(pc, op=op, rs=rs, rt=rt, target=target):
            t = self.labels[target] if isinstance(target, str) else target
            off = (t - (pc + 4)) >> 2
            assert -32768 <= off < 32768
            return (op << 26) | (_r(rs) << 21) | (_r(rt) << 16) | (off & 0xFFFF)
        self._emit(enc, c)

    def beq(self, rs, rt, target, c=""): self._br(4, rs, rt, target, c)
    def bne(self, rs, rt, target, c=""): self._br(5, rs, rt, target, c)

    def _jmp(self, op, target, c):
        def enc(pc, op=op, target=target):
            t = self.labels[target] if isinstance(target, str) else target
            assert (t & 0xF0000000) == ((pc + 4) & 0xF0000000) and t % 4 == 0
            return (op << 26) | ((t >> 2) & 0x3FFFFFF)
        self._emit(enc, c)

    def j(self, target, c=""): self._jmp(2, target, c)
    def jal(self, target, c=""): self._jmp(3, target, c)

    def la(self, reg, addr, c=""):
        hi, lo = hi_lo(addr)
        self.lui(reg, hi, c or "%s = 0x%08X" % (reg, addr))
        self.addiu(reg, reg, lo if lo < 0x8000 else lo - 0x10000)

    def li_s(self, freg, value, tmp="at", c=""):
        b = _fbits(value)
        self.lui(tmp, b >> 16, c or "%s = %g" % (freg, value))
        if b & 0xFFFF:
            self.ori(tmp, tmp, b & 0xFFFF)
        self.mtc1(tmp, freg)

    def assemble(self):
        return [(self.origin + 4 * i, fn(self.origin + 4 * i) & 0xFFFFFFFF, c)
                for i, (fn, c) in enumerate(self.items)]


# ---------------------------------------------------------------- helpers
def mask(spec):
    m = 0
    for b in spec.upper().replace(" ", "").split("+"):
        m |= BUTTONS[b]
    return m


def _is_control_flow(w):
    op = w >> 26
    if op == 0 and (w & 63) in (8, 9, 12, 13):
        return True
    if op in (1, 2, 3, 4, 5, 6, 7, 20, 21, 22, 23):
        return True
    return op == 17 and ((w >> 21) & 31) == 8


def _jal_word(target):
    return (3 << 26) | ((target >> 2) & 0x3FFFFFF)


def _j_word(target):
    return (2 << 26) | ((target >> 2) & 0x3FFFFFF)


def _jalr_t9():
    return (_r("t9") << 21) | (31 << 11) | 9


def _sltu_nz(reg):   # sltu reg, zero, reg  ->  reg = (reg != 0)
    return (0 << 21) | (_r(reg) << 16) | (_r(reg) << 11) | 43


class Image:
    """The unpacked game image (0x00100000..) -- OUR unpack of the vanilla executable."""

    def __init__(self, data):
        self.data = data

    def word(self, addr):
        o = addr - IMG_BASE
        if not (0 <= o <= len(self.data) - 4):
            raise ValueError("0x%08X is outside the unpacked image" % addr)
        return struct.unpack_from("<I", self.data, o)[0]


def unpack_image(elf_bytes):
    from . import elfpack
    img, base = elfpack.unpack_elf(elf_bytes)
    if base != IMG_BASE:
        raise ValueError("unexpected unpack base 0x%X" % base)
    return Image(img)


# ---------------------------------------------------------------- the caves
def _target_of_jal(img, site):
    w = img.word(site)
    if w >> 26 != 3:
        raise ValueError("0x%08X is not a jal (%08X) -- not the vanilla executable?" % (site, w))
    return ((site + 4) & 0xF0000000) | ((w & 0x3FFFFFF) << 2), w


def build_input(img, feats):
    site, cave, data = INPUT_SITE, CAVE_INPUT, DATA
    combo_of = {k: mask(TOGGLE[k]) for k in feats if k in TOGGLE}
    order = [k for k in KEYS if k in combo_of]
    toggles = [(combo_of[k], FLAG_OFF[k], {"noclip": D_TOGGLES, "freeze": D_FREEZE_TOGGLES,
                                           "camlock": D_CAMLOCK_TOGGLES, "slowmo": D_SLOW_TOGGLES}[k], k) for k in order]
    fly_buttons = mask(CTRL["up"]) | mask(CTRL["down"]) | mask(CTRL["fast"]) | mask(CTRL["slow"])
    target, w = _target_of_jal(img, site)
    a = Asm(cave)
    a.addiu("sp", "sp", -32)
    a.sd("ra", 0, "sp")
    a.sd("a0", 8, "sp")
    a.jal(target)
    a.nop()
    a.sd("v0", 16, "sp")
    a.ld("a0", 8, "sp")
    a.la("t4", PAD1, "only controller 1")
    a.bne("a0", "t4", "in_done")
    a.nop()
    a.la("t9", data)
    a.lw("t7", D_INPUT_CALLS, "t9")
    a.addiu("t7", "t7", 1)
    a.sw("t7", D_INPUT_CALLS, "t9")
    a.lw("t5", 0x17C, "a0", "buttons held (as the game sees them)")
    a.sw("t5", D_HELD, "t9")
    a.lbu("t6", 0x146, "a0")
    a.sb("t6", D_LX, "t9")
    a.lbu("t6", 0x147, "a0")
    a.sb("t6", D_LY, "t9")
    a.lw("t6", D_PREV, "t9")
    a.sw("t5", D_PREV, "t9")
    a.nor("t6", "t6", "zero")
    a.and_("t8", "t6", "t5", "t8 = newly pressed")
    for n, (cmb, en_off, cnt_off, what) in enumerate(toggles):
        skip = "in_no_toggle%d" % n
        a.andi("t7", "t5", cmb)
        a.xori("t7", "t7", cmb)
        a.bne("t7", "zero", skip)
        a.andi("t6", "t8", cmb)
        a.beq("t6", "zero", skip)
        a.nop()
        a.lw("t6", en_off, "t9")
        a.xori("t6", "t6", 1)
        a.sw("t6", en_off, "t9")
        a.lw("t6", cnt_off, "t9")
        a.addiu("t6", "t6", 1)
        a.sw("t6", cnt_off, "t9")
        a.label(skip)
    a.addu("v1", "zero", "zero")
    for n, (cmb, _, _, what) in enumerate(toggles):
        a.andi("t7", "t5", cmb)
        a.xori("t7", "t7", cmb)
        a.bne("t7", "zero", "in_no_combo%d" % n)
        a.nop()
        a.ori("v1", "v1", cmb)
        a.label("in_no_combo%d" % n)
    if "noclip" in feats:
        a.lw("t6", D_HEART, "t9", "flight heartbeat")
        a.beq("t6", "zero", "in_apply")
        a.addiu("t6", "t6", -1)
        a.sw("t6", D_HEART, "t9")
        a.lw("t6", D_ENABLED, "t9")
        a.beq("t6", "zero", "in_apply")
        a.nop()
        a.ori("v1", "v1", fly_buttons, "flying: hide up/down/fast/slow")
        a.addiu("t6", "zero", 0x80)
        a.sb("t6", 0x146, "a0", "centre the left stick for the game")
        a.sb("t6", 0x147, "a0")
    a.label("in_apply")
    a.beq("v1", "zero", "in_done")
    a.nop()
    a.nor("t6", "v1", "zero")
    a.and_("t5", "t5", "t6")
    a.sw("t5", 0x17C, "a0")
    a.srl("t6", "v1", 8)
    a.lbu("t7", 0x142, "a0")
    a.or_("t7", "t7", "t6")
    a.sb("t7", 0x142, "a0")
    a.andi("t6", "v1", 0xFF)
    a.lbu("t7", 0x143, "a0")
    a.or_("t7", "t7", "t6")
    a.sb("t7", 0x143, "a0")
    a.label("in_done")
    a.ld("v0", 16, "sp")
    a.ld("ra", 0, "sp")
    a.jr("ra")
    a.addiu("sp", "sp", 32)
    return a.assemble(), [(site, _jal_word(cave), "jal pad update -> input cave")]


def build_mover(img):
    cave, data, entry = CAVE_MOVER, DATA, MOVER_UPDATE
    w1, w2 = img.word(entry), img.word(entry + 4)
    for x in (w1, w2):
        if _is_control_flow(x):
            raise ValueError("Mover::Update entry is a branch -- not the vanilla executable?")
    pos_off, pos_deref = 0x24, 0x34
    cam_right = 0x90
    b_up, b_down, b_fast, b_slow = (mask(CTRL[k]) for k in ("up", "down", "fast", "slow"))
    speed, dz = CTRL["speed"], CTRL["deadzone"]
    a = Asm(cave)
    a.la("t9", data)
    a.lw("t7", D_MOVER_CALLS, "t9")
    a.addiu("t7", "t7", 1)
    a.sw("t7", D_MOVER_CALLS, "t9")
    a.lw("t7", D_ENABLED, "t9")
    a.beq("t7", "zero", "original")
    a.nop()
    a.la("t4", PLAYER_PTR, "player filter")
    a.lw("t4", 0, "t4")
    a.beq("t4", "zero", "original")
    a.nop()
    a.addiu("t4", "t4", 0x30)
    a.lw("t6", pos_deref, "a0")
    a.bne("t4", "t6", "original")
    a.nop()
    a.lw("t8", pos_deref, "a0", "t8 = player world matrix")
    a.beq("t8", "zero", "original")
    a.nop()
    a.lw("t7", D_FLY_FRAMES, "t9")
    a.addiu("t7", "t7", 1)
    a.sw("t7", D_FLY_FRAMES, "t9")
    a.addiu("t7", "zero", CTRL["grace"])
    a.sw("t7", D_HEART, "t9")
    a.lw("t5", D_HELD, "t9")
    a.lbu("t6", D_LX, "t9")
    a.lbu("t7", D_LY, "t9")
    for reg in ("t6", "t7"):
        a.addiu(reg, reg, -128)
        a.sra("v0", reg, 31)
        a.xor("v1", reg, "v0")
        a.subu("v1", "v1", "v0")
        a.slti("v1", "v1", dz)
        a.movn(reg, "zero", "v1")
    a.mtc1("t6", "f1")
    a.cvt_s_w("f1", "f1")
    a.mtc1("t7", "f2")
    a.cvt_s_w("f2", "f2")
    a.neg_s("f2", "f2", "forward = stick up")
    a.li_s("f3", speed / 127.0)
    a.la("t4", FRAME_TIME)
    a.lwc1("f4", 0, "t4")
    a.mul_s("f3", "f3", "f4", "speed *= dt")
    for btn, mul, lbl in ((b_fast, CTRL["fast_mult"], "nofast"), (b_slow, CTRL["slow_mult"], "noslow")):
        a.andi("v0", "t5", btn)
        a.beq("v0", "zero", lbl)
        a.nop()
        a.li_s("f4", mul)
        a.mul_s("f3", "f3", "f4")
        a.label(lbl)
    a.mul_s("f1", "f1", "f3")
    a.mul_s("f2", "f2", "f3")
    a.mtc1("zero", "f5")
    a.li_s("f6", 127.0)
    a.mul_s("f6", "f6", "f3")
    a.andi("v0", "t5", b_up)
    a.beq("v0", "zero", "noup")
    a.nop()
    a.add_s("f5", "f5", "f6")
    a.label("noup")
    a.andi("v0", "t5", b_down)
    a.beq("v0", "zero", "nodown")
    a.nop()
    a.sub_s("f5", "f5", "f6")
    a.label("nodown")
    a.la("t4", CAMERA_PTR)
    a.lw("t4", 0, "t4")
    a.beq("t4", "zero", "world")
    a.nop()
    # level flight: horizontal forward = (right.z, 0, -right.x)
    a.lwc1("f10", cam_right, "t4", "right.x")
    a.lwc1("f11", cam_right + 8, "t4", "right.z")
    a.mul_s("f7", "f11", "f2")
    a.mul_s("f12", "f10", "f1")
    a.add_s("f7", "f7", "f12")
    a.mul_s("f9", "f11", "f1")
    a.mul_s("f12", "f10", "f2")
    a.sub_s("f9", "f9", "f12")
    a.mov_s("f8", "f5")
    a.beq("zero", "zero", "apply")
    a.nop()
    a.label("world")
    a.mov_s("f7", "f1")
    a.mov_s("f8", "f5")
    a.mov_s("f9", "f2")
    a.label("apply")
    for i, fo in enumerate(("f7", "f8", "f9")):
        a.lwc1("f10", pos_off + 4 * i, "t8")
        a.add_s("f10", "f10", fo)
        a.swc1("f10", pos_off + 4 * i, "t8")
    a.addiu("sp", "sp", -48)
    a.sd("ra", 0, "sp")
    a.neg_s("f7", "f7")
    a.neg_s("f8", "f8")
    a.neg_s("f9", "f9")
    a.swc1("f7", 16, "sp")
    a.swc1("f8", 20, "sp")
    a.swc1("f9", 24, "sp")
    a.la("t4", PLAYER_PTR)
    a.lw("t4", 0, "t4")
    a.lw("a0", 0x0C, "t4", "a0 = player's components")
    a.beq("a0", "zero", "noshift")
    a.nop()
    a.jal(SHIFT_FN, "game's own world shift (components, &-delta): Mover/physics/AI follow")
    a.addiu("a1", "sp", 16)
    a.label("noshift")
    a.ld("ra", 0, "sp")
    a.addiu("sp", "sp", 48)
    a.addu("v0", "zero", "zero")
    a.jr("ra", "skip the game's movement this frame")
    a.nop()
    a.label("original")
    a.raw(w1, "displaced")
    a.raw(w2, "displaced")
    a.j(entry + 8)
    a.nop()
    return a.assemble(), [(entry, _j_word(cave), "j mover cave"), (entry + 4, 0, "nop")]


def build_freeze(img):
    cave, data, entry = CAVE_FREEZE, DATA, FREEZE_HOOK
    w1, w2 = img.word(entry), img.word(entry + 4)
    for x in (w1, w2):
        if _is_control_flow(x):
            raise ValueError("per-character update entry is a branch -- not the vanilla executable?")
    a = Asm(cave)
    a.la("t9", data)
    a.lw("t7", D_FREEZE, "t9")
    a.beq("t7", "zero", "fr_orig")
    a.nop()
    a.la("t4", PLAYER_PTR)
    a.lw("t4", 0, "t4")
    a.beq("t4", "zero", "fr_orig")
    a.nop()
    a.lw("t4", 0x0C, "t4", "player's components")
    a.beq("t4", "a0", "fr_orig")
    a.nop()
    a.lw("t7", D_FROZEN_SKIPS, "t9")
    a.addiu("t7", "t7", 1)
    a.sw("t7", D_FROZEN_SKIPS, "t9")
    a.addu("v0", "zero", "zero")
    a.jr("ra", "frozen: skip this character's whole update")
    a.nop()
    a.label("fr_orig")
    a.raw(w1)
    a.raw(w2)
    a.j(entry + 8)
    a.nop()
    return a.assemble(), [(entry, _j_word(cave), "j freeze cave"), (entry + 4, 0, "nop")]


def build_camlock(img):
    site, cave, data = CAM_SITE, CAVE_CAM, DATA
    target, w = _target_of_jal(img, site)
    a = Asm(cave)
    a.la("t9", data)
    a.lw("t7", D_CAMLOCK, "t9")
    a.bne("t7", "zero", "cl_locked")
    a.nop()
    a.sw("zero", D_CAM_KEY, "t9")
    a.j(target)
    a.nop()
    a.label("cl_locked")
    a.lw("t7", D_CAM_KEY, "t9")
    a.bne("t7", "zero", "cl_have")
    a.nop()
    for k in range(0, 0x30, 4):
        a.lw("t6", k, "a0")
        a.sw("t6", D_CAM_MTX + k, "t9")
    a.sw("s0", D_CAM_KEY, "t9", "remember this viewport")
    a.beq("zero", "zero", "cl_use")
    a.nop()
    a.label("cl_have")
    a.bne("t7", "s0", "cl_go")
    a.nop()
    a.label("cl_use")
    a.lw("t7", D_CAM_FRAMES, "t9")
    a.addiu("t7", "t7", 1)
    a.sw("t7", D_CAM_FRAMES, "t9")
    a.addiu("a0", "t9", D_CAM_MTX, "render from the snapshot")
    a.label("cl_go")
    a.j(target)
    a.nop()
    return a.assemble(), [(site, _jal_word(cave), "jal set-camera -> camera-lock cave")]


def build_cheatsmenu(img):
    """Cheats Menu Unlock: every built-in cheat counts as OWNED (none is switched on).

    The pause menu shows Cheats when 0x1222A8 finds an owned cheat-type unlockable; each row on the page is shown
    iff 0x122248(name) says owned.  We (1) make 0x1222A8 return 1 and (2) make 0x122248 return 1 for every cheat
    except the two the game never implemented (cheat_showposition / cheat_showaiinfo: byte 10 of the name is
    'p' / 'a'; no other cheat name has either), so those two stay hidden exactly as shipped."""
    for fn, want in ((OWN_FN, OWN_FN_WORDS), (OWN_MENU, OWN_MENU_WORDS)):
        if (img.word(fn), img.word(fn + 4)) != want:
            raise ValueError("0x%08X is not the vanilla cheat-ownership code -- not the vanilla executable?" % fn)
    a = Asm(CAVE_OWN)
    a.lbu("t0", 10, "a0", "name[10]: 'a' = cheat_showaiinfo, 'p' = cheat_showposition")
    a.addiu("t1", "zero", ord("a"))
    a.beq("t0", "t1", "own_orig")
    a.nop()
    a.addiu("t1", "zero", ord("p"))
    a.beq("t0", "t1", "own_orig")
    a.nop()
    a.addiu("v0", "zero", 1, "owned")
    a.jr("ra")
    a.nop()
    a.label("own_orig")
    a.raw(OWN_FN_WORDS[0], "displaced")
    a.raw(OWN_FN_WORDS[1], "displaced")
    a.j(OWN_FN + 8)
    a.nop()
    hooks = [(OWN_FN, _j_word(CAVE_OWN), "j ownership cave"), (OWN_FN + 4, 0, "nop"),
             (OWN_MENU, 0x24020001, "addiu v0,zero,1  (any cheat owned)"), (OWN_MENU + 4, 0x03E00008, "jr ra"),
             (OWN_MENU + 8, 0, "nop")]
    return a.assemble(), hooks


def build_slowmo(img):
    """Slow-Mo: wrap the per-frame timer; when on, scale the frame time (and its reciprocal) by SLOW_FACTOR."""
    if (img.word(SLOW_FN), img.word(SLOW_FN + 4)) != SLOW_FN_WORDS:
        raise ValueError("0x%08X is not the vanilla frame timer -- not the vanilla executable?" % SLOW_FN)
    a = Asm(CAVE_SLOW)
    a.addiu("sp", "sp", -16)
    a.sd("ra", 0, "sp")
    a.jal("slow_stub", "run the game's own timer")
    a.nop()
    a.la("t9", DATA)
    a.lw("t7", D_SLOW, "t9")
    a.beq("t7", "zero", "slow_done")
    a.nop()
    a.la("t4", FRAME_TIME)
    a.lwc1("f0", 0, "t4")
    a.li_s("f1", SLOW_FACTOR)
    a.mul_s("f0", "f0", "f1", "dt *= factor")
    a.swc1("f0", 0, "t4")
    a.li_s("f2", 1.0)
    a.div_s("f2", "f2", "f0")
    a.swc1("f2", 8, "t4", "1/dt")
    a.label("slow_done")
    a.ld("ra", 0, "sp")
    a.jr("ra")
    a.addiu("sp", "sp", 16)
    a.label("slow_stub")
    a.raw(SLOW_FN_WORDS[0], "displaced")
    a.raw(SLOW_FN_WORDS[1], "displaced")
    a.j(SLOW_FN + 8)
    a.nop()
    return a.assemble(), [(SLOW_FN, _j_word(CAVE_SLOW), "j slow-mo cave"), (SLOW_FN + 4, 0, "nop")]


def build_infammo(img):
    """Infinite Ammo: the one store that writes the reduced round count back into the player's weapon instance
    (+0x24) becomes a nop, so the count never changes.  Measured live 2026-11-11: 9 trigger pulls, HUD unchanged."""
    w = img.word(AMMO_SITE)
    if (w >> 26) != 43 or (w & 0xFFFF) != 0x24:
        raise ValueError("0x%08X is not the vanilla ammo write-back -- not the vanilla executable?" % AMMO_SITE)
    return [], [(AMMO_SITE, 0, "nop  (was sw count,0x24(weapon))")]


def build_fastred(img, factor=None):
    """Fast Red: the three `jal Mover::Update` calls go through a cave. For the player's mover only
    (mover+0x34 == player creature + 0x30) the cave multiplies the frame time, runs the mover and puts
    the frame time back, so only Red's own movement integrates a longer step. Everything else that
    reads the frame time (animation, enemies, timers) sees the normal value."""
    factor = SPEED_FACTOR if factor is None else float(factor)
    jal = 0x0C000000 | (MOVER_UPDATE >> 2)
    for site in SPEED_SITES:
        if img.word(site) != jal or (img.word(site + 4) & 0xFC1FFFFF) != 0x8C04000C:
            raise ValueError("0x%08X is not `jal Mover::Update` / `lw a0,0xc(..)` -- not the vanilla executable?" % site)
    a = Asm(CAVE_SPEED)
    a.la("t0", PLAYER_PTR)
    a.lw("t0", 0, "t0", "player creature")
    a.beq("t0", "zero", "spd_plain", "no player yet")
    a.nop()
    a.addiu("t0", "t0", 0x30)
    a.lw("t1", 0x34, "a0", "the matrix this mover drives")
    a.bne("t0", "t1", "spd_plain", "someone else's mover")
    a.nop()
    a.la("t9", FRAME_TIME)
    a.la("t8", SPEED_SAVE)
    a.lwc1("f1", 0, "t9")
    a.swc1("f1", 0, "t8", "keep the real frame time")
    a.sd("ra", 8, "t8")
    a.li_s("f2", factor)
    a.mul_s("f1", "f1", "f2", "dt *= factor, for this one call")
    a.swc1("f1", 0, "t9")
    a.jal(MOVER_UPDATE)
    a.nop()
    a.la("t9", FRAME_TIME)
    a.la("t8", SPEED_SAVE)
    a.lwc1("f1", 0, "t8")
    a.swc1("f1", 0, "t9", "frame time back")
    a.ld("ra", 8, "t8")
    a.jr("ra")
    a.nop()
    a.label("spd_plain")
    a.j(MOVER_UPDATE)
    a.nop()
    words = a.assemble()
    if words[-1][0] >= SPEED_SAVE:
        raise ValueError("fast-Red cave grew into its own save slots")
    words += [(SPEED_SAVE + 4 * i, 0, "save slot") for i in range(4)]
    return words, [(site, 0x0C000000 | (CAVE_SPEED >> 2), "jal fast-Red cave (was jal Mover::Update)") for site in SPEED_SITES]


def build_unlockall(img):
    """Unlock Everything: the game asks ONE function whether unlockable N (a row of unlockables.csv: 48 Showdown
    characters, 16 stages, 147 journal pages, 10 cheats) is unlocked -- 0x00183028(manager, N), 12 callers.  The cave
    answers 1 for every row except `cheat_sh*` (cheat_showposition / cheat_showaiinfo, never implemented by the game:
    they keep the original test so they stay hidden as shipped) and except an index past the table (original test).
    Nothing is written: the card's own bits are untouched, so a save made on this disc stays a normal save."""
    if (img.word(UNLOCK_FN), img.word(UNLOCK_FN + 4)) != UNLOCK_FN_WORDS:
        raise ValueError("0x%08X is not the vanilla unlock test -- not the vanilla executable?" % UNLOCK_FN)
    a = Asm(CAVE_UNLOCK)
    a.lw("t0", 0x14, "a0", "unlockables table header")
    a.beq("t0", "zero", "ua_orig")
    a.nop()
    a._i(37, "t0", "t1", 4, "lhu t1,4(t0): row count")
    a._rr(43, "t2", "a1", "t1", c="sltu t2,a1,count")
    a.beq("t2", "zero", "ua_orig", "index past the table: original test")
    a.nop()
    a.lw("t0", 0, "t0", "row pointer array")
    a.addu("t1", "a1", "a1")
    a.addu("t1", "t1", "t1")
    a.addu("t0", "t0", "t1")
    a.lw("t0", 0, "t0", "row")
    a.lw("t0", 0, "t0", "row name")
    for off, ch in ((0, "c"), (4, "t"), (5, "_"), (6, "s"), (7, "h")):
        a.lbu("t1", off, "t0")
        a.addiu("t2", "zero", ord(ch))
        a.bne("t1", "t2", "ua_yes")
        a.nop()
    a.label("ua_orig")
    a.raw(UNLOCK_FN_WORDS[0], "displaced: move a3,a0")
    a.raw(UNLOCK_FN_WORDS[1], "displaced: sra v0,a1,5")
    a.j(UNLOCK_FN + 8)
    a.nop()
    a.label("ua_yes")
    a.jr("ra")
    a.addiu("v0", "zero", 1, "unlocked")
    words = a.assemble()
    if words[-1][0] >= CAVE_UNLOCK + 0x200:
        raise ValueError("unlock cave too big")
    return words, [(UNLOCK_FN, _j_word(CAVE_UNLOCK), "j unlock-everything cave"), (UNLOCK_FN + 4, 0, "nop")]


def build_money(img):
    """Money x5: the wallet's add-money routine 0x00160198 (wallet, amount, kind) -- the ONE place money comes in: coin /
    bag pickups (kind 0, from 0x0035EE6C), the player's give-money virtual (kind 1: the Big Money cheat's 10000, rewards)
    and, with the amount negated, spending (take-money 0x0036F2D8).  The cave makes amount*5 when amount > 0;
    0 and negative amounts (spending) go through untouched.  (2026-11-13 first hooked the give-money virtual 0x0036F1A0
    instead: PCSX2 showed Big Money x5 but an $8 pickup arrived as $8 -- pickups never go through that virtual.)"""
    if (img.word(WALLET_FN), img.word(WALLET_FN + 4)) != WALLET_FN_WORDS:
        raise ValueError("0x%08X is not the vanilla add-money routine -- not the vanilla executable?" % WALLET_FN)
    a = Asm(CAVE_MONEY)
    a._rr(42, "t1", "zero", "a1", c="slt t1,zero,a1: amount > 0?")
    a.beq("t1", "zero", "mo_orig", "spending / zero: unchanged")
    a.nop()
    a.addu("t0", "a1", "a1", "x2")
    a.addu("t0", "t0", "t0", "x4")
    a.addu("a1", "a1", "t0", "x5")
    a.label("mo_orig")
    a.raw(WALLET_FN_WORDS[0], "displaced: addiu sp,sp,-48")
    a.raw(WALLET_FN_WORDS[1], "displaced: daddu a3,a1,zero (the routine works on a3 = the scaled amount)")
    a.j(WALLET_FN + 8)
    a.nop()
    return a.assemble(), [(WALLET_FN, _j_word(CAVE_MONEY), "j money cave"), (WALLET_FN + 4, 0, "nop")]


def build_damage(img, god, ohk, dbl=False, hard=False):
    """God Mode, One-Hit Kills, Double Damage and/or Hard Mode: one cave on Health::TakeDamage's `hp -= damage; store hp`.

    s0 = the health component being damaged; the player's is *(*(PLAYER_PTR)+0x18).  f0 = hp, f20 = damage.
    Red (s0 is the player's):  god -> skip both instructions (hp is not reduced, nothing is stored);
                               else hard -> damage := damage + damage, then the original two.
    anyone else:               ohk -> damage := current hp, so the subtraction leaves 0;
                               else dbl -> damage := damage + damage.
    The scaling is `add.s f20,f20,f20` (x2 exactly, no constant needed) and f20 is the routine's own damage
    register: it is not read again after the store (checked by the R5900 interpreter test on the real function)."""
    if (img.word(DMG_SITE), img.word(DMG_SITE + 4)) != DMG_WORDS:
        raise ValueError("0x%08X is not the vanilla damage code -- not the vanilla executable?" % DMG_SITE)
    other = ohk or dbl
    a = Asm(CAVE_DMG)
    a.la("t0", PLAYER_PTR)
    a.lw("t0", 0, "t0", "player creature")
    a.beq("t0", "zero", "dmg_other" if other else "dmg_orig", "no player yet")
    a.nop()
    a.lw("t0", 0x18, "t0", "player health component")
    a.bne("t0", "s0", "dmg_other" if other else "dmg_orig")
    a.nop()
    if god:
        a.j(DMG_SITE + 8, "player: no damage")
        a.nop()
    elif hard:
        a.add_s("f20", "f20", "f20", "player: damage x2 (hard mode)")
        a.beq("zero", "zero", "dmg_orig")
        a.nop()
    else:
        a.beq("zero", "zero", "dmg_orig", "player: normal damage")
        a.nop()
    if other:
        a.label("dmg_other")
        if ohk:
            a.mov_s("f20", "f0", "damage = all remaining hp")
        else:
            a.add_s("f20", "f20", "f20", "damage x2")
    a.label("dmg_orig")
    a.raw(DMG_WORDS[0], "displaced: sub.s f0,f0,f20")
    a.raw(DMG_WORDS[1], "displaced: swc1 f0,16(s0)")
    a.j(DMG_SITE + 8)
    a.nop()
    return a.assemble(), [(DMG_SITE, _j_word(CAVE_DMG), "j damage cave"), (DMG_SITE + 4, 0, "nop")]


def build_menu(img, feats):
    """Extra rows on the game's own Cheats page, mirroring the mod flags."""
    rows = [(ROW_LABEL[k], FLAG_OFF[k]) for k in ROW_KEYS if k in feats]
    if img.word(MENU_VT_SLOT) != MENU_INIT_FN:
        raise ValueError("cheat page vtable slot changed -- not the vanilla executable?")
    w1, w2 = img.word(MENU_HANDLER_SITE), img.word(MENU_HANDLER_SITE + 4)
    if (w1, w2) != MENU_HANDLER_WORDS:
        raise ValueError("cheat change-handler site changed: %08X %08X" % (w1, w2))
    sw, addrs, blob = [], [], b""
    for name, _ in rows:
        addrs.append(MENU_STRINGS + len(blob))
        blob += name.encode("latin-1") + b"\0"
    blob += b"\0" * ((-len(blob)) % 4)
    for k in range(0, len(blob), 4):
        sw.append((MENU_STRINGS + k, struct.unpack_from("<I", blob, k)[0], "row name"))
    a = Asm(CAVE_MENU_INIT)
    a.addiu("sp", "sp", -32)
    a.sd("ra", 0, "sp")
    a.sd("s0", 8, "sp")
    a.sd("s1", 16, "sp")
    a.addu("s0", "a0", "zero")
    a.jal(MENU_INIT_FN, "the page's own init")
    a.nop()
    for k, (name, flag) in enumerate(rows):
        skip = "mi_skip%d" % k
        a.addu("a0", "s0", "zero")
        a.la("a1", addrs[k], name)
        a.jal(MENU_FIND, "find the row by name")
        a.nop()
        a.la("t9", DATA)
        a.sw("v0", D_MENU_W + 4 * k, "t9")
        a.beq("v0", "zero", skip)
        a.addu("s1", "v0", "zero")
        a.lw("t9", 0, "s1", "show it: widget vfunc +0x54 (widget, 1)")
        a.lw("t9", 0x54, "t9")
        a.addu("a0", "s1", "zero")
        a.raw(_jalr_t9(), "jalr t9")
        a.addiu("a1", "zero", 1)
        a.la("t9", DATA)
        a.lw("t6", flag, "t9")
        a.raw(_sltu_nz("t6"))
        a.sw("t6", 0x1290, "s1", "row shows On/Off")
        a.label(skip)
    a.ld("ra", 0, "sp")
    a.ld("s0", 8, "sp")
    a.ld("s1", 16, "sp")
    a.jr("ra")
    a.addiu("sp", "sp", 32)
    iw_ = a.assemble()
    c = Asm(CAVE_MENU_CHANGE)
    c.beq("s1", "v0", "mc_match", "replays: bnel s1,v0")
    c.nop()
    c.la("t9", DATA)
    for k, (name, flag) in enumerate(rows):
        nx = "mc_next%d" % k
        c.lw("t6", D_MENU_W + 4 * k, "t9")
        c.bne("t6", "s1", nx)
        c.nop()
        c.lw("t7", 0x1290, "s1", "%s row changed: flag = value" % name)
        c.raw(_sltu_nz("t7"))
        c.sw("t7", flag, "t9")
        c.label(nx)
    c.j(MENU_HANDLER_SITE + 0x10)
    c.nop()
    c.label("mc_match")
    c.j(MENU_HANDLER_SITE + 8)
    c.nop()
    cw_ = c.assemble()
    hooks = [(MENU_VT_SLOT, CAVE_MENU_INIT, "cheat page vtable init -> menu init cave"),
             (MENU_HANDLER_SITE, _j_word(CAVE_MENU_CHANGE), "j menu change cave"),
             (MENU_HANDLER_SITE + 4, 0, "nop")]
    return iw_ + cw_ + sw, hooks


def build(img, feats):
    """-> (cave words {addr: word}, hook words {addr: word}, list of (title, [(addr,word,comment)...]))"""
    feats = [k for k in KEYS if k in feats]
    if not feats:
        raise ValueError("pick at least one code mod")
    parts = []
    if any(k in TOGGLE for k in feats):
        cw, ch = build_input(img, feats)
        parts.append(("input", cw, ch))
    if "noclip" in feats:
        parts.append(("mover", *build_mover(img)))
    if "freeze" in feats:
        parts.append(("freeze", *build_freeze(img)))
    if "camlock" in feats:
        parts.append(("camera lock", *build_camlock(img)))
    if "cheatsmenu" in feats:
        parts.append(("cheats menu unlock", *build_cheatsmenu(img)))
    if "slowmo" in feats:
        parts.append(("slow-mo", *build_slowmo(img)))
    if "infammo" in feats:
        parts.append(("infinite ammo", *build_infammo(img)))
    if any(k in feats for k in DAMAGE_KEYS):
        parts.append(("damage", *build_damage(img, "godmode" in feats, "onehitkill" in feats, "dmgx2" in feats, "hardmode" in feats)))
    if "fastred" in feats:
        parts.append(("fast Red", *build_fastred(img)))
    if "unlockall" in feats:
        parts.append(("unlock everything", *build_unlockall(img)))
    if "moneyx5" in feats:
        parts.append(("money x5", *build_money(img)))
    if any(k in ROW_LABEL for k in feats):
        mw, mh = build_menu(img, feats)
        parts.append(("cheat menu", mw, mh))
    if "hitlog" in feats or "memwatch" in feats:
        from . import codemods_research as R
        rc = research_config()
        if "hitlog" in feats:
            parts.append(("hitlog", *R.build_hitlog(img, rc["hitlog"])))
        if "memwatch" in feats:
            parts.append(("memwatch", *R.build_memwatch(img, rc["watch"])))
    spans = sorted((p[1][0][0], p[1][-1][0], p[0]) for p in parts if p[1])
    for (s1, e1, n1), (s2, e2, n2) in zip(spans, spans[1:]):
        if e1 >= s2:
            raise ValueError("caves %s and %s overlap" % (n1, n2))
    dsize = DATA_SIZE_SLOW if "slowmo" in feats else DATA_SIZE
    if any(s < DATA + dsize and DATA <= e for s, e, _ in spans if s >= CAVE_MOVER):
        raise ValueError("a cave overlaps the data block")
    words, hooks = {}, {}
    owner = {}
    for pname, cws, hks in parts:
        for a_, w, _ in cws:
            words[a_] = w
        for a_, w, _ in hks:
            if a_ in hooks and hooks[a_] != w:
                raise ValueError("hook conflict at 0x%08X: %s and %s both patch it (pick different functions)" % (a_, owner[a_], pname))
            hooks[a_] = w
            owner[a_] = pname
    if any(k in TOGGLE or k in ROW_LABEL for k in feats):
        for k in range(0, dsize, 4):
            words[DATA + k] = 0
    return words, hooks, parts


# ---------------------------------------------------------------- address check vs OUR unpack
def _count_refs(img, addr):
    """How many lui/(load|store|addiu) pairs in the game image build `addr`?"""
    hi, lo = hi_lo(addr)
    n = 0
    d = img.data
    nw = len(d) // 4
    for i in range(nw):
        w = struct.unpack_from("<I", d, 4 * i)[0]
        if w >> 26 == 15 and (w & 0xFFFF) == hi:
            rt = (w >> 16) & 31
            for j in range(i + 1, min(i + 8, nw)):
                x = struct.unpack_from("<I", d, 4 * j)[0]
                if ((x >> 21) & 31) == rt and (x & 0xFFFF) == lo and (x >> 26) in (9, 35, 33, 36, 32, 43, 40, 41, 49, 57, 55, 63):
                    n += 1
                    break
    return n


def _count_jal(img, target):
    want = _jal_word(target)
    d = img.data
    return sum(1 for (w,) in struct.iter_unpack("<I", d[:len(d) // 4 * 4]) if w == want)


def _count_words(img, value):
    d = img.data
    return sum(1 for (w,) in struct.iter_unpack("<I", d[:len(d) // 4 * 4]) if w == value)


def verify_addresses(img):
    """Check every address the mods depend on against the unpacked vanilla executable.
    -> list of (ok, what).  All must be ok before anything is built."""
    out = []

    def chk(ok, what):
        out.append((bool(ok), what))
    try:
        t, w = _target_of_jal(img, INPUT_SITE)
        chk(t == PAD_UPDATE, "0x%08X is `jal 0x%08X` (pad update)" % (INPUT_SITE, t))
        t, w = _target_of_jal(img, CAM_SITE)
        chk(t == CAM_SETTER, "0x%08X is `jal 0x%08X` (hand camera matrix to the renderer)" % (CAM_SITE, t))
        for name, a_ in (("Mover::Update", MOVER_UPDATE), ("per-character update", FREEZE_HOOK)):
            w1, w2 = img.word(a_), img.word(a_ + 4)
            prev = img.word(a_ - 4)
            chk(not _is_control_flow(w1) and not _is_control_flow(w2) and (prev == 0 or (prev & 0xFC1FFFFF) == 8 or (img.word(a_ - 8) & 0xFC1FFFFF) == 8),
                "%s 0x%08X: first two words relocatable, follows a function end" % (name, a_))
        chk(img.word(MENU_VT_SLOT) == MENU_INIT_FN, "cheat-page vtable 0x%08X holds init 0x%08X" % (MENU_VT_SLOT, MENU_INIT_FN))
        chk((img.word(MENU_HANDLER_SITE), img.word(MENU_HANDLER_SITE + 4)) == MENU_HANDLER_WORDS,
            "cheat change-handler site 0x%08X = bnel s1,v0 / ld s0" % MENU_HANDLER_SITE)
        for nm, a_ in (("world shift", SHIFT_FN), ("cheat row finder", MENU_FIND)):
            n = _count_jal(img, a_)
            v = _count_words(img, a_)       # virtual calls: the address sits in a vtable
            chk(n + v >= 1, "%s 0x%08X is called by %d `jal`s, listed in %d vtables" % (nm, a_, n, v))
        for nm, a_ in (("player pointer", PLAYER_PTR), ("camera pointer", CAMERA_PTR), ("frame time", FRAME_TIME)):
            n = _count_refs(img, a_)
            chk(n >= 3, "%s 0x%08X is addressed by %d code sites" % (nm, a_, n))
        for nm, a_, ws in (("cheat-row ownership test", OWN_FN, OWN_FN_WORDS), ("pause-menu 'any cheat owned'", OWN_MENU, OWN_MENU_WORDS),
                           ("frame timer", SLOW_FN, SLOW_FN_WORDS)):
            chk((img.word(a_), img.word(a_ + 4)) == ws and not _is_control_flow(ws[0]) and not _is_control_flow(ws[1]),
                "%s 0x%08X: first two words %08X %08X, relocatable" % (nm, a_, ws[0], ws[1]))
        chk(_count_jal(img, OWN_FN) >= 10, "ownership test 0x%08X is called by %d `jal`s (one per cheat row)" % (OWN_FN, _count_jal(img, OWN_FN)))
        chk(_count_jal(img, SLOW_FN) + _count_words(img, SLOW_FN) >= 1, "frame timer 0x%08X has a caller" % SLOW_FN)
        w = img.word(AMMO_SITE)
        chk((w >> 26) == 43 and (w & 0xFFFF) == 0x24, "ammo write-back 0x%08X is `sw ..,0x24(..)` (%08X)" % (AMMO_SITE, w))
        chk((img.word(DMG_SITE), img.word(DMG_SITE + 4)) == DMG_WORDS, "damage site 0x%08X = sub.s f0,f0,f20 / swc1 f0,16(s0)" % DMG_SITE)
        jal_mu = 0x0C000000 | (MOVER_UPDATE >> 2)
        chk(all(img.word(x) == jal_mu and (img.word(x + 4) & 0xFC1FFFFF) == 0x8C04000C for x in SPEED_SITES) and _count_jal(img, MOVER_UPDATE) == len(SPEED_SITES),
            "Mover::Update is called from exactly %d places, each `jal` + `lw a0,0xc(..)`" % len(SPEED_SITES))
        chk((img.word(UNLOCK_FN), img.word(UNLOCK_FN + 4)) == UNLOCK_FN_WORDS and _count_jal(img, UNLOCK_FN) == 12,
            "unlock test 0x%08X = move a3,a0 / sra v0,a1,5, called from 12 places" % UNLOCK_FN)
        chk((img.word(WALLET_FN), img.word(WALLET_FN + 4)) == WALLET_FN_WORDS and _count_jal(img, WALLET_FN) == 3,
            "add-money 0x%08X = addiu sp,-48 / daddu a3,a1, called from 3 places" % WALLET_FN)
        chk(len(img.data) == 0x549FE8 - IMG_BASE, "unpacked image is 0x100000..0x549FE8")
        chk(CAVE_MOVER < IMG_BASE and DATA + DATA_SIZE <= IMG_BASE, "code caves sit below the game image (0x000F0000 block)")
    except Exception as ex:   # noqa: BLE001 -- report, never crash the caller
        chk(False, "address check raised %s: %s" % (type(ex).__name__, ex))
    return out


# ---------------------------------------------------------------- installer / ELF
def _blocks(words):
    out, cur = [], None
    for a_ in sorted(words):
        if cur and a_ == cur[0] + 4 * len(cur[1]):
            cur[1].append(words[a_])
        else:
            cur = [a_, [words[a_]]]
            out.append(cur)
    return out


def feature_mask(feats):
    return sum(1 << i for i, k in enumerate(KEYS) if k in feats)


def mask_features(m):
    return [k for i, k in enumerate(KEYS) if m >> i & 1]


def build_installer(img, feats):
    words, hooks, parts = build(img, feats)
    table = _blocks(words) + _blocks(hooks)      # hooks last: caves exist before anything jumps to them
    a = Asm(INSTALLER_VA)
    a.la("t0", 0)
    a.label("loop")
    a.lw("t1", 0, "t0", "destination (0 = end)")
    a.beq("t1", "zero", "done")
    a.lw("t2", 4, "t0", "(delay) word count")
    a.addiu("t0", "t0", 8)
    a.label("copy")
    a.lw("t3", 0, "t0")
    a.sw("t3", 0, "t1")
    a.addiu("t0", "t0", 4)
    a.addiu("t2", "t2", -1)
    a.bne("t2", "zero", "copy")
    a.addiu("t1", "t1", 4)
    a.beq("zero", "zero", "loop")
    a.nop()
    a.label("done")
    a.addiu("v1", "zero", 0x64, "FlushCache(0)")
    a.addu("a0", "zero", "zero")
    a.raw(0x0000000C, "syscall")
    a.addiu("v1", "zero", 0x64, "FlushCache(2)")
    a.addiu("a0", "zero", 2)
    a.raw(0x0000000C, "syscall")
    a.j(GAME_ENTRY)
    a.nop()
    code = a.assemble()
    tbl_va = INSTALLER_VA + 4 * len(code)
    hi, lo = (tbl_va + 0x8000) >> 16, tbl_va & 0xFFFF
    code[0] = (code[0][0], (15 << 26) | (8 << 16) | hi, "lui t0")
    code[1] = (code[1][0], (9 << 26) | (8 << 21) | (8 << 16) | lo, "addiu t0")
    blob = b"".join(struct.pack("<I", w) for _, w, _ in code)
    for dest, ws in table:
        blob += struct.pack("<2I", dest, len(ws)) + b"".join(struct.pack("<I", w) for w in ws)
    blob += struct.pack("<2I", 0, 0)
    blob += TAG + struct.pack("<I", feature_mask(feats))
    return blob, words, hooks, parts


def patch_elf(vanilla_elf, feats):
    """-> (patched ELF bytes, cave words, hook words, parts).  `vanilla_elf` must be the shipped file."""
    d = bytearray(vanilla_elf)
    if d[:4] != b"\x7fELF" or struct.unpack_from("<I", d, 24)[0] != 0x00800008:
        raise ValueError("not the original SLUS_205.00")
    ph = struct.unpack_from("<8I", d, 52)
    if ph[1] != SEG_OFF or ph[2] != SEG_VA or ph[4] != ORIG_FILESZ:
        raise ValueError("unexpected program header (already patched?)")
    if struct.unpack_from("<I", d, SEG_OFF + STUB_JUMP - SEG_VA)[0] != _j_word(GAME_ENTRY):
        raise ValueError("boot stub jump at 0x%08X is not `j 0x%08X`" % (STUB_JUMP, GAME_ENTRY))
    if len(d) > INSTALLER_OFF:
        raise ValueError("ELF longer than expected")
    img = unpack_image(bytes(vanilla_elf))
    bad = [w for ok, w in verify_addresses(img) if not ok]
    if bad:
        raise ValueError("address check failed: " + "; ".join(bad))
    blob, words, hooks, parts = build_installer(img, feats)
    d += b"\0" * (INSTALLER_OFF - len(d)) + blob
    size = len(d) - SEG_OFF
    struct.pack_into("<2I", d, 52 + 16, size, size)
    struct.pack_into("<I", d, SEG_OFF + STUB_JUMP - SEG_VA, _j_word(INSTALLER_VA))
    return bytes(d), words, hooks, parts


def elf_features(elf_bytes):
    """Which mods a (possibly patched) SLUS_205.00 carries -> list of keys ([] = vanilla, None = unknown)."""
    if len(elf_bytes) <= ORIG_ELF_SIZE:
        return []
    jw = struct.unpack_from("<I", elf_bytes, SEG_OFF + STUB_JUMP - SEG_VA)[0]
    if jw == _j_word(GAME_ENTRY):
        return []
    i = elf_bytes.rfind(TAG)
    if jw != _j_word(INSTALLER_VA) or i < 0:
        return None
    return mask_features(struct.unpack_from("<I", elf_bytes, i + 4)[0])


def elf_crc(b):
    """PCSX2's game CRC: XOR of the ELF's 32-bit words."""
    b = b + b"\0" * ((-len(b)) % 4)
    c = 0
    for (w,) in struct.iter_unpack("<I", b):
        c ^= w
    return c


# ---------------------------------------------------------------- Cheats page layout
def swui_layout(feats):
    """cheats.swui with the original rows plus one row per selected mod (the original research's menu_layout.py)."""
    rows = ["cmb_cht_invulnerable", "cmb_cht_inf_deadeye", "cmb_cht_golden_gun", "cmb_cht_sudden_death",
            "cmb_cht_sad_stars", "cmb_cht_no_hud", "cmb_cht_no_distort"]
    rows += [ROW_LABEL[k] for k in ROW_KEYS if k in feats]
    rows += ["Show Player Position", "Show AI Info"]
    top, step, scale, help_y = 120, 20, "0.6", 360
    L = ["<TEXPHASE:HUD>",
         "<RGB:ArrowHighlight:0.549:0.443:0.388>",
         "<IPU_BACKGROUND:0,0,0:0,0:options_bg_10k>",
         "<FONT:regulatorcondensed:SCALE:2,2:CENTER>",
         "<COLOR:1,1,1,1><COLORC:1,1,1,1>",
         "<TEXT:200,71,0:Cheats><BASECOLOR:0.288,0.039,0,1>",
         "<FONT:berrylium:SCALE:%s,%s:DROPSHADOW>" % (scale, scale),
         "<COLOR:1,1,1,1><COLORC:1,1,1,0.500>"]
    for i, name in enumerate(rows):
        y = top + step * i
        L.append("<COMBO:84,%d,0:%s:350,%d:200,20:10>" % (y, name, y))
        L.append("<COMBODATA:Off:0>")
        L.append("<COMBODATA:On:1>")
    L += ["<BUTTON:500,%d,0:Big Money:CALLB:0,0:0,0:0:-1:bigmoney>" % 350,
          "<FONT:berrylium>",
          "<TEXT:180,%d,0:BUTTON_CONFIRM>" % help_y,
          "<TEXT:331,%d,0:BUTTON_BACK>" % help_y,
          "<TEXT:218,%d,0:Accept>" % help_y,
          "<TEXT:369,%d,0:Cancel>" % help_y]
    return ("\r\n".join(L) + "\r\n").encode("latin-1")


_ORIGINAL_SWUI_MARKERS = (b"<COMBO:84,375,0:Show Player Position:350,375:200,24:10>",
                          b"<COMBO:84,405,0:Show AI Info:350,405:200,24:10>", b"cmb_cht_no_distort")


_DEFLATE_CACHE = {}

_PRECOMPUTED = {   # sha1(layout) -> Zopfli raw-deflate (hex), made offline so no machine needs zopfli installed
    "8d83192febec02e323487598a82044720a34fdff":
        "b209718d00945217ba6ec3501880c5d2e81dfa0059e5e0d2330c9421454d1c051cd04dedabc0a5a79f5d4bc5711405cf"
        "7fbeb097236bd387d1cefdf2eef5a7f5d006ab2ce9e3284fb382cd35a0aeaef5d852d354b6544d93e7c6cb9d6f5bce74"
        "b8f6760b1790c426e033bdaf734a2a3f4c7d19ddf1e8c05b6ca1c4695304352d234a624c2a1cc3c6b1667d5024059cfe"
        "62db5ff3ace3cdbc35c8d261fa22769de33e0bb0ebdd828290f441961038190eeaeacb279bdd8168455dc53425d4456a"
        "4f42927c3c7f88cbf2b9c89bbd382dcb19129bc15d7bcbcdc872bdef7f3a3deaea0889d0dcf6c0d42459e1371ced433f"
        "ca6a3f270f4d4170198405065547bc7cb852b6924f8daeb5b5c04b12b83e4440bed4b54b3df1631cc4f8190b5c6b871b"
        "e7784a8b18133f6d88b08d76b6796e574dcced1807752674b395aea00b3d88fdaa0eca8ad3bcd68a56ce6942fdac8985"
        "abb473b52b37ceab9a96b5b0b576b6c1ed4189f10b169ed1ce33b9e704fbce8c4677426cf7c254c4c54d461f3bcb2278"
        "c6656749ab9c0f125ce7e556ba72d4ad71674c122a54e57f547bb7dd7a0bd059234710d879da9953829fc1b166331bd8"
        "3131c37b19c23cddf3daed18731ca864934987372468dff11683f17a7e0ca8aa7c19e063eab1aac8a6a8821545f8be3e"
        "b5193d51002720112e58e107"
    ,
    "9cda7fd05482e9f7fcb489fa491c2c2752dbf560":
        "b209718d08f0700c76b5f20875b1e3e5b2097277029452173a92c20018805f850760272d766c4f91716134172748912c"
        "d32eb6f2f6d74e27a3e784a0ffcf8783acaaa2afa33ccd0a363608f474ed914d354d6553d534796fbcdcf9b6e54c876b"
        "6fb7701190d980f8489f9b9c92da0f531f82275e1d788b2daa70da164143ab889218931ac768e358b33e52640539fdc5"
        "b6bfe65dc79b796b04e5c3f045ac3aa7755660e7bb450a00f2072803e4643868ea2f9f6c76056257d0534c53063da03e"
        "ca4086a7e387b8aade8bbcdd8bc3b29e21b311b96b6fb91959aef7fd4f87073d1d00519adb1e3235192afc82a37de847"
        "59e3e7e4a52d08ae82b0c048d5018f0f67ca66f0bca36b6d2de42509badd4410bcd6b56b3df1631cc4f8fd886bdd70e3"
        "124f691163e2a72d11b6d1cd362fedba8db91de3a0c9846e76d21570a507b15f374155739a679de8abe749a89fb5b170"
        "956eae76e3c679ddd0aa11b6d6cd36b8ed047b6946a327211add44938b9b82be3ecca900bb3d31151cc08cbe4acb2278"
        "c795b4a475ceff125ce771275d39e9d6581a93840a55f91fd5de6db7de02e980013a77ed3c95e694e077e458b399cdff"
        "7062440f108579bae7d9fd4fe6f4a78226930e8f48d0bee32d06e3f5fc545055785de03fd553aa40f3985a51849f9b53"
        "a01a8fc7c00948840b16fc00"
    ,
    "470139800ec9fa635726c27a3dbe6a824440af8a":
        "b209718d00945217ba6ec3501880c5d2e81dfa0059e5e0d2330c9421454d1c051cd04ded2a70e9e9675f4bc5711405cf"
        "7fbeb097236bd387d1cefdf2eef5a7f5d006ab2ce9c3284fb382cd35a0aeaef5d852d354b6544d93e7c6cb9d6f5bce74"
        "b8f6760b1790c426e0333dd43925951fa6be8cee7874e02db650e2b429829a9611253126158e61e358b33e2892024e7f"
        "b1edaf79d6f166de1a64e965fa22769de33e0bb0ebdd828290f441961038190eeaeacb279bdd8168455dc53425d4456a"
        "4f42927c3c7f88cbf2a9c89bbd382dcb19129bc15d7bcbcdc872bdef7f3a3deaea0889d0dcf6c0d42459e1371ced433f"
        "ca6a3f27f74d4170198405065547bcfc72a56c259f1a5d6b6b819724707d88807ca96b977ae2c73888f11316b8d60e37"
        "cef1941631267eda10611bed6cf3dcae9a98db310eea4ce8662b5d41177a10fb551d9415a339d98e56ce6942fdac8985"
        "abb473b52b37ceab9a96b5b0b576b6c1ed058d8afc203ca39d67726f5062fc8c8567b6f254c43d27d8776634bae3223f"
        "d44a54b8b8c9e8436759044fb8ec2c6995f34147e84a3b5d3bead6b833260915aaf63faabddb6ebd05e8ac912308ec3c"
        "edcc29c14fc006a1990dec9898e1bd0c619eee79ed76cc3a0e7cb2c924837708da77bcc560bc9e1f03aa2a5f06f8187d"
        "ac2ab229aa6045113ed4a736a3270ae00424c2052bfc00"
    ,
    "51fca30035e79520dae050cb746c690cb16da0c3":
        "a5d465b2db301000e0dfa53be4007e1919abb74553387170da7f1e830c5347ca181e9dbe529432d70cbbfb99f7e5de7f"
        "bf9ed83b1f2607eff5b3c72fb76307ecba66b793322f2a3eb78086a671cd9786a1f3a58eb1c89bae0fa163bbf3f13638"
        "ac3c400a1fc512d8a92d196dc2380f55f441a48e82d51e6a927755d4b23a613425b42129ec5c7be183a668e0faabbdbf"
        "15b96eb008b6a02ae7f1b5dc753feff3047ebd7bd010529eab0a02b72051dbbc7ee9f03b90a568a861aca021d2af15a4"
        "a89fcf1f93babeafcaee78392d1a5a0a9fc1db06ebddc4f682777f3a3d1a9a08c9a4a5130036145513379c1ce33029da"
        "b0a4375d45491dc51501dd44227cbe52be52bf147af6de8620cbe0fb4314d46f75e35b3d0b5312a5e4fe821bfd70eb6b"
        "3c67554a68987754da563f1b7f6d375d2aec94446d2175dc4bd7d0377a94864d1bd50da705d98ffee67d5216165d2a5d"
        "ad9f6b7ce7a665d3b2ba95b6d1cfb684bd6249559ea467f5f3b0f04635210f447ab897a723e1ed2a767bb564675047fd"
        "40ed0c16ec76b0aea27b520fd6ac2945cf91bad64f373eebf67430a51993aaf13faa73d8ef831598bc5020089c321f2c"
        "1925f7c07bd0c211fd52ce70a5425ce64711fbb1657dee7b2ae692252a241dbac16a34dd2e3f27e8bafa6d8268d19fa3"
        "9a8a2f513b49c8a9fd52665d5f026e441352f1c047"
    ,
    "c00d5f982dbaa4c3ac662eadd149dbea0f9adeed":
        "b209718d08f0700c76b5f20875b1e3e5b201945217cad1c24000805fe51e803241ff5c7e05ce8dd3f9c71924c8944b0e"
        "abbc7d1352f71697ddfdf0dd8e6d649525bd9c64499ab3b9464036f43e5beabac6961a843c6fba3e78b6e5ccc75bf7b0"
        "1a2020b1912f113dd519259517249e02ce79eac85ded51899326f76b5a869444985438423bc75a0c912aa9c819aef6c3"
        "2dcf75dc85bb458ad48e7fc4ae73b7cf13d8f5ee910a80f44391007252ecd7d59f5f36bb03510a64154209c840eb4ba0"
        "2d11e70f70595ee75973bc3d2d904d89cd68b075d7bb893570ff7f747a201b0088a4a5ed22a84b8aca6f383c065e98d6"
        "5e462e9a9ce0d20f728c3403f0707ba56ca53c140eacbd85dc3846cf0f11a43cd5f5a77aec45d88ff0f52dae77c3cdc7"
        "7842f308132f6988b0cd6e367c6c574dc4ed08fb752a74d84957c113dd8fbcaaf6cb8ad19cec463f799f847a69130957"
        "ede6eacfdc28ab6a5ad6c2d6bbd926b75734ccb393f0cc6e1ee49ee31f7b0b1a9e0b11761235c0c55d4e2fcf96b40535"
        "d00d545b30a597bd75ee5fe3b2b7a655c6bb8ed0d56eba7eaf5bd3de94c454a8fa7754fbb0dfbb2b64000618dcb5b3a4"
        "b7a4045f23c75a2c6cde31c58cce141464c991c75e36adfbcea7402699add4d29ee3ae46d3edf23e41d394a709bc493f"
        "344e05de46ad30c4a7faa1ccecdf061c9f843867811b"
    ,
    "6af8523fa51b3c23aa79d32bfde280a1b547929d":
        "a5d4d592db301406e0ebd23be4011664ac738aa670e2e0b4771e830cb38eb463587afa4ad14cb06c86f3eb33ebe3dafd"
        "3e1f982b17061be7f3bbd71f977d0bccb2a48f833ccd0a36d7806e34b5cb96aaaab0a562183c379c6f7ccbb4c7fda5b7"
        "993980aed8c89740efeb9c92ca0f535f42773cdaf3666b2871da14414dcb889218930ac7b0b2cd890bf2950cb63b5bbb"
        "4b9eb5bd89b704e96a377e16bbf67e9f05d8f5ae4146e8eabdc44e666738a8abcf1f2d7607a229ba910de30add20a5cb"
        "2e49da9f3fc465f95ce4cd569c96e5f42b3683b3f4e6ab81e978dffe747af6181012a1a9e581a15e4932bfe1681bfa51"
        "56fb3979680a82cb202c30281ae2e5dd95b2957468e8986b13bc2481f34304a4535d3dd5133fc6418c9f05ceaaad70fd"
        "184f691163e2a70d11b6dece368eedaa89b9cdaebdce846eb4d26574a207b15fd54159319a93ed68f99826d4cf9a58b8"
        "723b573d73e3bcaa69590b5b6d67ebdcee9518bf60e1e9ed3c837b76b0ed4c68742744a395a8202eae0afa783da51c64"
        "47da81f20ecce863675e04cfb8eccc6995f35e47e8723b5dddebe6b033240915aafa3faab559afbd196888011a77ad3c"
        "ed4c29c1cfc07aa18905ec9898e15a82304fb7bc76d969ed7b3ec96092ce5b08dab7bd596fb89cee038a229d067827bd"
        "afca9221aa604611beaf0fcdf4ae28801d900817acf003"
    ,
    "b1383cd5926c463b8b90c7136a284f39af3dedfe":
        "b209718d08f0700c76b5f20875b1e3e50294521f388ec2501880af92033091a9cbbcad407a2355ab8e28a668881d51a6"
        "9d7eed584add6e21eafbdf47f7a7f5d005a7aae8cba8c8f292cd0da0ae693cb2a561e86ca9db36cf8d97bbc075bce970"
        "edef163d400a9b80cff4d01494d44194052a7ae2d181bfd84285b3b60c1b5ac5942498d438818de7ccfaa0291a78fdc5"
        "b6bfe659cf9ff96b5095e3f445ec7aa77d1660d7bb050d21e583aa20f0721c36f5974f2ebb03d18aba9a6d2ba88bf447"
        "0529eae9fc11aeaab7b268f7e2b42c67296c86deda5f6e464ecffffea7d3a3ae899008cd5d1f6c4351357ec3f13e0ae2"
        "bc090af2dc960457615462d04dc4cbc72b652bf5dcd873b60ef8690ab78708a8d7ba71ada74182c304bf61811b72b875"
        "8967b44c3009b29608db92b3ed4bbb6e136e27386c72a1db52ba86aef43009ea26ac6a4673528ed62e694283bc4d84ab"
        "c9b9c68d9b147543ab46d8869c6d717b41e3b23808cf92f36cee0d2a8cdfb1f06c294f47dcf3c27d6746e3272ef24352"
        "a2c6c54d495f1ee654809a1c681cc19cbe749665f886abce92d6051fc5846ec8e9d64977c69d3149a950adff51dddd76"
        "eb2fc0648d1c41e01659674e097e03cf99cd5c3e028b191e54888a6ccf6bf783e06924e5ffaab84241079ebf188cd7f3"
        "5340d7d5eb001ff44f554db545159c38c687e6dc663d8a0278218971c90a3f00"
    ,
}


def deflate_small(b):
    """Smallest raw deflate we can get: zopfli if installed, else the best zlib setting."""
    b = bytes(b)
    if b in _DEFLATE_CACHE:
        return _DEFLATE_CACHE[b]
    best = None
    import hashlib
    pre = _PRECOMPUTED.get(hashlib.sha1(b).hexdigest())
    if pre:
        z = bytes.fromhex(pre)
        if zlib.decompress(z, -15) == b:
            best = z
    try:
        import zopfli.zopfli as zz           # optional
        z = zz.compress(bytes(b), numiterations=500, gzip_mode=0)[2:-4]
        if zlib.decompress(z, -15) == b:
            best = z
    except Exception:                        # noqa: BLE001 -- zopfli is optional
        pass
    for lvl in (6, 7, 8, 9):
        for ml in range(1, 10):
            c = zlib.compressobj(lvl, zlib.DEFLATED, -15, ml, 0)
            z = c.compress(b) + c.flush()
            if best is None or len(z) < len(best):
                best = z
    _DEFLATE_CACHE[b] = best
    return best


# ---------------------------------------------------------------- ISO plumbing (raw, on our own COPY)
def _read_dir(f, lba, size, prefix, out, records):
    f.seek(lba * 2048)
    d = f.read(size)
    p = 0
    while p < len(d):
        ln = d[p]
        if ln == 0:
            p = (p // 2048 + 1) * 2048
            continue
        flba, fsize = struct.unpack_from("<I", d, p + 2)[0], struct.unpack_from("<I", d, p + 10)[0]
        raw = d[p + 33:p + 33 + d[p + 32]]
        if raw not in (b"\0", b"\1"):
            nm = prefix + raw.decode("latin-1").split(";")[0].lower()
            if d[p + 25] & 2:
                _read_dir(f, flba, fsize, nm + "/", out, records)
            else:
                out[nm] = (flba * 2048, fsize)
                records[nm] = lba * 2048 + p
        p += ln


def iso_files(f):
    """-> ({lowercase path: (byte offset, size)}, {path: offset of its directory record})"""
    f.seek(16 * 2048)
    pvd = f.read(2048)
    if pvd[1:6] != b"CD001":
        raise ValueError("not an ISO 9660 image")
    root = pvd[156:156 + 34]
    out, records = {}, {}
    _read_dir(f, struct.unpack_from("<I", root, 2)[0], struct.unpack_from("<I", root, 10)[0], "", out, records)
    return out, records


def _relocate_elf(f, records, new_elf):
    f.seek(16 * 2048)
    pvd = bytearray(f.read(2048))
    blocks = struct.unpack_from("<I", pvd, 80)[0]
    f.seek(0, 2)
    if f.tell() != blocks * 2048:
        raise ValueError("ISO size does not match its volume size (already relocated?)")
    pad = (-len(new_elf)) % 2048
    f.write(new_elf + b"\0" * pad)
    new_blocks = blocks + (len(new_elf) + pad) // 2048
    rec = records["slus_205.00"]
    f.seek(rec)
    r = bytearray(f.read(34))
    struct.pack_into("<I", r, 2, blocks)
    struct.pack_into(">I", r, 6, blocks)
    struct.pack_into("<I", r, 10, len(new_elf))
    struct.pack_into(">I", r, 14, len(new_elf))
    f.seek(rec)
    f.write(r)
    struct.pack_into("<I", pvd, 80, new_blocks)
    struct.pack_into(">I", pvd, 84, new_blocks)
    f.seek(16 * 2048)
    f.write(pvd)
    return blocks, new_blocks


class _Slice:
    """Backing for rdrstudio.dave over a byte range of an open file."""

    def __init__(self, f, base, size):
        self.f, self.base, self.size, self.writable = f, base, size, True

    def read_at(self, off, n):
        self.f.seek(self.base + off)
        return self.f.read(n)

    def write_at(self, off, data):
        self.f.seek(self.base + off)
        self.f.write(data)

    def flush(self):
        self.f.flush()

    def close(self):
        pass


def menu_archives(files):
    return sorted(n for n in files if n == "swat.dat" or (n.startswith("lvls_") and n.endswith(".dat")))


def _cheats_entry(f, name, files):
    off, size = files[name]
    d = _dave.Dave(_Slice(f, off, size), name)
    return d, d.find("cheats.swui")


def has_rows(feats):
    """Does any chosen mod add a row to the Cheats page?  (If not, the page is left exactly as shipped.)"""
    return any(k in ROW_LABEL for k in feats)


def plan_swui(feats, d, e):
    """-> (new_full_bytes, compressed) or None if already right.  Raises if it will not fit the slot."""
    new = swui_layout(feats)
    if d.read(e) == new:
        return None
    cur = d.read(e)
    if not all(m in cur for m in _ORIGINAL_SWUI_MARKERS):
        raise ValueError("%s: cheats.swui is not the original layout" % d.label)
    z = deflate_small(new)
    if len(z) > e.slot:
        raise ValueError("%s: Cheats page needs %d bytes, its slot is %d" % (d.label, len(z), e.slot))
    return new, z


def plan_iso(iso_path, feats):
    """Dry run against any ISO (read only): the slot every archive offers vs what the page needs."""
    new = swui_layout(feats)
    z = deflate_small(new)
    if not has_rows(feats):
        return {"archives": 62, "page_bytes": 0, "page_deflated": 0, "smallest_slot": 512, "fits": True}
    with open(iso_path, "rb") as f:
        files, _ = iso_files(f)
        slots = []
        for nm in menu_archives(files):
            d, e = _cheats_entry(f, nm, files)
            slots.append((nm, e.slot))
    tight = min(s for _, s in slots)
    return {"archives": len(slots), "page_bytes": len(new), "page_deflated": len(z), "smallest_slot": tight,
            "fits": len(z) <= tight}


def iso_state(iso_path):
    """Read-only: which mods does this ISO carry?  -> {"features": [...]|None, "crc": "XXXXXXXX", "archives_with_rows": n, "archives": n}"""
    with open(iso_path, "rb") as f:
        files, _ = iso_files(f)
        off, size = files["slus_205.00"]
        f.seek(off)
        e = f.read(size)
        feats = elf_features(e)
        names = menu_archives(files)
        want = swui_layout(feats or []) if feats else None
        n = 0
        if feats and not has_rows(feats):
            n = len(names)                       # nothing to put on the page: it stays as shipped
        elif feats:
            for nm in names:
                d, ent = _cheats_entry(f, nm, files)
                if d.read(ent) == want:
                    n += 1
    return {"features": feats, "crc": "%08X" % elf_crc(e), "archives": len(names), "archives_with_rows": n}


def _guard_dst(src, dst):
    s, t = os.path.abspath(src), os.path.abspath(dst)
    if os.path.normcase(s) == os.path.normcase(t):
        raise ValueError("write the patched ISO to a NEW file")
    if os.path.exists(t):
        raise ValueError("%s already exists -- pick a new name (nothing is overwritten)" % t)
    parts = [p.lower() for p in t.replace("\\", "/").split("/")]
    if "01_vanilla" in parts:
        raise ValueError("refusing to write inside 01_Vanilla")


def patch_iso(src_iso, dst_iso, feats, progress=None):
    """Copy `src_iso` to the NEW file `dst_iso` and build the chosen mods into the copy.
    Returns a dict of what was done.  The source is only ever read."""
    say = progress or (lambda m: None)
    feats = [k for k in KEYS if k in feats]
    if not feats:
        raise ValueError("pick at least one code mod")
    _guard_dst(src_iso, dst_iso)
    # dry run first: fail before copying 2 GB
    plan = plan_iso(src_iso, feats)
    if not plan["fits"]:
        raise ValueError("the Cheats page (%d bytes) does not fit the smallest slot (%d)"
                         % (plan["page_deflated"], plan["smallest_slot"]))
    with open(src_iso, "rb") as f:
        files, _ = iso_files(f)
        off, size = files["slus_205.00"]
        f.seek(off)
        vanilla_elf = f.read(size)
    if elf_features(vanilla_elf) != []:
        raise ValueError("the source ISO is already code-modded -- start from the clean ISO")
    new_elf, words, hooks, parts = patch_elf(vanilla_elf, feats)
    say("copying the ISO ...")
    tmp = dst_iso + ".part"
    try:
        shutil.copyfile(src_iso, tmp)
        with open(tmp, "r+b") as f:
            files, records = iso_files(f)
            say("moving the patched executable ...")
            b0, b1 = _relocate_elf(f, records, new_elf)
            files, _ = iso_files(f)
            n = 0
            for nm in (menu_archives(files) if has_rows(feats) else []):
                d, e = _cheats_entry(f, nm, files)
                r = plan_swui(feats, d, e)
                if r is None:
                    continue
                new, z = r
                f.seek(files[nm][0] + e.offset)
                f.write(z + b"\0" * (e.slot - len(z)))
                f.seek(files[nm][0] + 0x800 + 16 * e.index + 8)
                f.write(struct.pack("<2I", len(new), len(z)))
                n += 1
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, dst_iso)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    st = iso_state(dst_iso)
    if st["features"] != feats or st["archives_with_rows"] != st["archives"]:
        raise ValueError("verification failed after the build: %r" % (st,))
    st.update(volume_sectors=(b0, b1), archives_patched=n, elf_bytes=len(new_elf))
    return st


# What has been SEEN working in PCSX2 on this machine (see 05_Docs/CODEMODS.md and
# 05_Docs/ingame-proof/codemods_*.png).  Only add a key from evidence.
PROVEN = {
    "hitlog": "2026-11-08 PCSX2, game booted + played: scripts/rdr_hitlog.py read over PINE counted 165,319 IsCheatOn calls, 698 resource-dispatcher calls, 17 CSV-reader calls; IsCheatOn(bit 10) returned 1 after Infinite Deadeye was switched on (bit 6 = Invincibility likewise)",
    "memwatch": "2026-11-08 PCSX2 in gameplay: scripts/rdr_watch.py (live window) showed player pointer, X/Y/Z (matching rdr_pos), camera pointer, frame time 0.0332 s and followed a 2-hop pointer chain; null pointers read as null",
    "noclip": "2026-11-07 PCSX2: the Cheats row switched it on; Red flew above and through buildings, camera followed (L3+R3 combo not tried)",
    "freeze": "2026-11-07 PCSX2: R3+Triangle froze the town (144 px changed in 15 s vs 15,088 live); the Cheats row was not tried",
    "camlock": "2026-11-07 PCSX2: R3+Circle held the view while Red flew away; unlocking snapped it back; the Cheats row was not tried",
    "cheatsmenu": "2026-11-08 PCSX2, STOCK save, no pnach: pause menu offers Cheats and the page lists every built-in cheat (Invincibility .. No Distortion, Big Money); none was on; Show Player Position / Show AI Info stay hidden",
    "infammo": "2026-11-12 PCSX2, disc built by the Studio from stock: six aimed trigger pulls in the town, the HUD stayed at 206 rounds / 6 in the cylinder; the patched word read back as a nop over PINE (the same nop held 203 through 9 pulls on 2026-11-11)",
    "godmode": "2026-11-12 PCSX2, same disc: Red stood in front of a gunman for 2+ minutes, health read 250.0 every 6 s over PINE and the bar stayed full; the same level without it killed him in under a minute",
    "onehitkill": "2026-11-12 PCSX2, disc built by the Studio from stock: one revolver shot took the gunman facing the town spawn from 60.0 health to 0 (health read over PINE before and after each shot; he dropped his gun). The same enemy on a stock disc needed four shots (60 -> 41.1 -> 26.9 -> 0). Red is not affected",
    "fastred": "2026-11-12 PCSX2, disc built by the Studio from stock: Red's steady run speed read over PINE was 7.1 m/s against 4.8 m/s on a stock disc (x1.48; 10.0 m covered in a 1.5 s push against 7.6 m), and walking into the water tower he stopped at x = -12.442, the same spot as on the stock disc. The same cave injected live at x2 gave 9.5 m/s",
    "unlockall": "2026-11-14 PCSX2, disc built from stock, same memory card as the control (which really owns 10 of 48 Showdown characters, 2 of 16 stages, 16 of 147 journal pages, 0 cheats -- read over PINE): the Showdown roster offered all 48 characters, the journal had no LOCKED rows and the pause menu had Cheats (Big Money etc.). The stock disc on that card showed 10 characters, LOCKED journal rows and no Cheats entry",
    "hardmode": "2026-11-14 PCSX2, disc built from stock: Red's health read over PINE dropped 50.1 / 46.3 / 43.9 / 46.8 / 42.5 per gunshot (mean 45.9) against 24.7 / 22.4 / 23.0 / 22.9 / 23.2 / 23.4 (mean 23.3) on a stock disc in the same town fight (x1.97); he died in 6 hits instead of 11",
    "dmgx2": "2026-11-14 PCSX2, disc built from stock (with God Mode, Unlock Everything, Money x5): the same gunman lost 37.6 health to the first hits (60 -> 22.4 = 2 x 18.8) where the stock disc took 18.9 for its first hit (60 -> 41.1); one extra headshot read 94.5 = 2 x 47.25",
    "moneyx5": "2026-11-14 PCSX2, disc built from stock: Cheats > Big Money (+10,000) took the wallet from 677 to 50,677 over PINE and the HUD showed it (x5); the two pickups from a shot gunman then arrived as +30 and +20 (stock disc: the same kill gave $8, so drops are not multiples of 5 unmodded) -- pickups are x5 by that evidence and by the hook on the shared wallet routine, but a single pickup was not isolated against a stock twin",
    "slowmo": "2026-11-08 PCSX2: the Cheats row (On) made Red cover 5.8 m in a 5 s walk vs 23.6 m with it off (x4.07 = 1/4 speed); R3+Square toggled it; the flag survived a level restart",
}
# Also seen 2026-11-08 on the same disc: the L3+R3 noclip combo (Red rose 2 -> 22.9 m), R3+Triangle / R3+Circle chords flip
# their flags (read back over PINE), No HUD (the built-in cheat, now reachable on any save) hides the HUD.

# ---------------------------------------------------------------- Studio glue (background build)
import threading
import time

_JOB = {"state": "idle", "log": [], "result": None, "error": None, "started": None, "features": []}
_JOB_LOCK = threading.Lock()


def job_status():
    with _JOB_LOCK:
        return dict(_JOB, log=list(_JOB["log"]))


def start_build(base_iso, dst_iso, feats):
    """Run patch_iso on a thread (the copy takes minutes).  -> (started, why)"""
    feats = [k for k in KEYS if k in feats]
    if not feats:
        return False, "Tick at least one code mod."
    if not os.path.basename(dst_iso).upper().startswith("RDR_CODEMODS"):
        return False, "refusing to write anything but RDR_CODEMODS*.iso"
    with _JOB_LOCK:
        if _JOB["state"] == "running":
            return False, "a build is already running"
        _JOB.update(state="running", log=[], result=None, error=None, started=time.time(), features=feats)

    def log(m):
        with _JOB_LOCK:
            _JOB["log"].append("%s  %s" % (time.strftime("%H:%M:%S"), m))

    def run():
        try:
            if os.path.exists(dst_iso):          # our own previous output only (name checked above)
                log("replacing the previous %s" % os.path.basename(dst_iso))
                os.remove(dst_iso)
            res = patch_iso(base_iso, dst_iso, feats, progress=log)
            log("done: PCSX2 CRC %s, Cheats page patched in %d archives" % (res["crc"], res["archives_patched"]))
            with _JOB_LOCK:
                _JOB.update(state="done", result=res)
        except Exception as ex:                  # noqa: BLE001 -- shown to the user
            log("FAILED: %s" % ex)
            with _JOB_LOCK:
                _JOB.update(state="error", error="%s: %s" % (type(ex).__name__, ex))

    threading.Thread(target=run, name="codemods-build", daemon=True).start()
    return True, ""


def describe(vanilla_iso, working_iso, out_iso):
    """Everything the Code Mods tab shows, read-only."""
    # FINAL PASS: the two research instruments are read with scripts/rdr_hitlog.py
    # and scripts/rdr_watch.py, which the download does not carry -- so the
    # release edition does not offer a mod whose results nobody can read.
    try:
        from . import features as _F
        hide = set(RESEARCH_KEYS) if _F.build() == "release" else set()
    except Exception:                                       # noqa: BLE001
        hide = set()
    out = {"features": [dict(key=k, title=t, help=h, controls=c, row=ROW_LABEL.get(k), research=k in RESEARCH_KEYS) for k, t, h, c in FEATURES if k not in hide],
           "output_path": out_iso, "job": job_status(), "proven": dict(PROVEN)}
    for name, p in (("stock", vanilla_iso), ("working", working_iso), ("built", out_iso)):
        try:
            if not os.path.exists(p):
                out[name] = {"path": p, "exists": False}
                continue
            st = iso_state(p)
            st.update(path=p, exists=True)
            out[name] = st
        except Exception as ex:                  # noqa: BLE001
            out[name] = {"path": p, "exists": True, "error": "%s: %s" % (type(ex).__name__, ex)}
    try:
        out["fit"] = plan_iso(vanilla_iso, KEYS)
    except Exception as ex:                      # noqa: BLE001
        out["fit"] = {"fits": False, "error": str(ex)}
    return out
