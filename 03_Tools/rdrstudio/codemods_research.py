"""Research code mods (2026-11-08, the reviewer): Function-Hit Logger + Memory Watch.

These are NOT cheats.  They are measuring instruments for people who want to understand the game's code:

* **hitlog**  -- hook up to 16 chosen functions.  Each call is counted and its first four arguments, caller
  (return address) and RETURN VALUE (v0) are recorded in a table, and the last 128 calls go into a ring buffer.
* **memwatch** -- up to 32 watch entries ("player -> +0x54 as float"), evaluated by the GAME ITSELF once per frame
  (pointer chains followed with range/alignment/null checks, so a bad pointer can never crash the game) into a small
  results block.  An external viewer (scripts/rdr_watch.py) reads that block over PINE and draws the live overlay.

Both live in spare RAM 0x000F2000-0x000F5FFF (below the game image, next to the other code mods' caves) and are
built with the same boot-stub installer as noclip & friends.  Nothing is saved to the memory card.  Off unless ticked.

How a hook works (hitlog): the first two words of the target function are replaced by `j stub; nop`.  The stub logs,
swaps the return address for a shared trampoline (the real one goes on a 16-deep shadow stack, so stack arguments and
the callee's frame are untouched), runs the two displaced words and jumps to function+8.  The trampoline records v0
and returns to the real caller.  Only at/v0/v1/t8/t9 are used before the displaced words, so no argument register
(a0-a3, t0-t3 on this ABI) is disturbed.  Limits (honest): float return values (f0) and float args are not recorded;
a function that never returns (longjmp-style) leaves a stale shadow entry (the depth is capped at 16 and recovers
when it fills); threads other than the main one are not locked against each other.

The configuration (which functions / which addresses) is a small JSON table the Studio writes
(02_Modded/research_config.json); see `load_config`.
"""
import json
import os
import struct

# ---------------------------------------------------------------- memory map (all inside the free 0x000F0000 block)
CAVE_HL, HL_DATA = 0x000F2000, 0x000F3000
CAVE_MW, MW_DATA = 0x000F5000, 0x000F5800
HL_MAX, HL_RING = 16, 128
HL_TABLE, HL_SHADOW, HL_RINGOFF = 0x40, 0x240, 0x300
HL_SIZE = HL_RINGOFF + HL_RING * 16            # 0xB00
HL_STUB = 0xC0                                  # bytes per stub
HL_TRAMP = CAVE_HL + HL_MAX * HL_STUB           # 0x000F2800
MW_MAX = 32
MW_TABLE, MW_RESULTS = 0x10, 0x400
MW_SIZE = MW_RESULTS + MW_MAX * 16              # 0x600
MW_HOOK = 0x001431B0                            # pad update: runs once per frame
PTR_LO, PTR_HI = 0x00100000, 0x02000000         # addresses we are willing to dereference

TYPES = {"u32": 0, "s32": 1, "f32": 2, "u16": 3, "u8": 4, "s16": 5}

DEFAULT_CONFIG = {
    "hitlog": [      # defaults avoid every address another code mod already hooks (noclip/freeze/slowmo/cheatsmenu/memwatch)
        {"addr": "0x0017E778", "name": "IsCheatOn(bit)"},
        {"addr": "0x003D4BC0", "name": "CSV reader"},
        {"addr": "0x0025E678", "name": "resource dispatcher"},
    ],
    "watch": [
        {"label": "player ptr", "addr": "0x0079E240", "type": "u32"},
        {"label": "player X", "addr": "0x0079E240", "type": "f32", "chain": [0x54]},
        {"label": "player Y", "addr": "0x0079E240", "type": "f32", "chain": [0x58]},
        {"label": "player Z", "addr": "0x0079E240", "type": "f32", "chain": [0x5C]},
        {"label": "camera ptr", "addr": "0x0079EEE4", "type": "u32"},
        {"label": "frame time", "addr": "0x004F7DF0", "type": "f32"},
        {"label": "cheat bits", "addr": "0x00742118", "type": "u32"},
    ],
}


def _num(x):
    return int(x, 0) if isinstance(x, str) else int(x)


def normalize_config(cfg):
    """Validate + normalise a config dict -> {'hitlog': [(addr, name)], 'watch': [(label, addr, type, [offs])]}."""
    cfg = cfg or {}
    hl, mw = [], []
    for e in cfg.get("hitlog", DEFAULT_CONFIG["hitlog"]):
        a = _num(e["addr"] if isinstance(e, dict) else e)
        if a % 4 or not (PTR_LO <= a < 0x00500000):
            raise ValueError("hitlog address 0x%X is not a 4-aligned code address in the game image" % a)
        hl.append((a, str(e.get("name", "") if isinstance(e, dict) else "")))
    if len(hl) > HL_MAX or len({a for a, _ in hl}) != len(hl):
        raise ValueError("hitlog: at most %d distinct functions" % HL_MAX)
    for e in cfg.get("watch", DEFAULT_CONFIG["watch"]):
        t = e.get("type", "u32")
        if t not in TYPES:
            raise ValueError("watch type %r (use one of %s)" % (t, ", ".join(TYPES)))
        chain = [int(_num(c)) for c in e.get("chain", [])]
        if len(chain) > 2 or any(not -0x8000 <= c < 0x8000 for c in chain):
            raise ValueError("watch chain: at most 2 signed 16-bit offsets")
        mw.append((str(e.get("label", "")), _num(e["addr"]), t, chain))
    if len(mw) > MW_MAX:
        raise ValueError("memwatch: at most %d entries" % MW_MAX)
    return {"hitlog": hl, "watch": mw}


def load_config(path=None):
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return normalize_config(json.load(f))
    return normalize_config(DEFAULT_CONFIG)


# ---------------------------------------------------------------- tiny decode for the displaced-word safety check
def _reads_writes(w):
    """-> (set of registers read, set written) for the plain integer/load/store forms a prologue uses; None = unknown."""
    op, rs, rt, rd = w >> 26, (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31
    if w == 0:
        return set(), set()
    if op == 15:                                                    # lui
        return set(), {rt}
    if op in (8, 9, 10, 11, 12, 13, 14, 24, 25):                    # alu immediate
        return {rs}, {rt}
    if op in (32, 33, 34, 35, 36, 37, 38, 39, 26, 27, 55, 30):      # loads (lb lh lwl lw lbu lhu lwr lwu ldl ldr ld lq)
        return {rs}, {rt}
    if op in (49, 57):                                              # lwc1 / swc1: only the base reg is an integer read
        return {rs}, set()
    if op in (40, 41, 42, 43, 44, 45, 46, 63, 31):                  # stores (sb sh swl sw sdl sdr swr sd sq)
        return {rs, rt}, set()
    if op == 0 and (w & 63) in (0, 2, 3, 56, 58, 59, 60, 62, 63):   # shifts by constant
        return {rt}, {rd}
    if op == 0 and (w & 63) in (32, 33, 34, 35, 36, 37, 38, 39, 42, 43, 44, 45, 46, 47, 10, 11):
        return {rs, rt}, {rd}
    return None


SCRATCH = {1, 2, 3, 24, 25}          # at v0 v1 t8 t9 : what our pre-stub clobbers


def check_displaced(img, fn, extra_words=()):
    """The two words we move must not depend on registers we clobber, nor be control flow, nor be a branch target."""
    from . import codemods as C
    ws = (img.word(fn), img.word(fn + 4))
    defined = set()
    for w in ws:
        if C._is_control_flow(w):
            raise ValueError("0x%08X: first two words are control flow (%08X)" % (fn, w))
        rw = _reads_writes(w)
        if rw is None:
            raise ValueError("0x%08X: cannot prove word %08X is safe to move (unknown opcode)" % (fn, w))
        bad = (rw[0] & SCRATCH) - defined
        if bad:
            raise ValueError("0x%08X: word %08X reads a register the logger clobbers" % (fn, w))
        defined |= rw[1]
    # nobody may branch/jump into fn+4 (we would skip the stub for the second word)
    tgt = fn + 4
    for i in range(0, len(img.data) - 3, 4):
        w = struct.unpack_from("<I", img.data, i)[0]
        pc = C.IMG_BASE + i
        op = w >> 26
        if op in (2, 3) and (((pc + 4) & 0xF0000000) | ((w & 0x3FFFFFF) << 2)) == tgt:
            raise ValueError("0x%08X: something jumps to function+4" % fn)
        if op in (4, 5, 6, 7, 20, 21, 22, 23) or (op == 1 and ((w >> 16) & 31) in (0, 1, 2, 3, 16, 17)):
            off = w & 0xFFFF
            off = off - 0x10000 if off & 0x8000 else off
            if pc + 4 + (off << 2) == tgt:
                raise ValueError("0x%08X: a branch targets function+4" % fn)
    return ws


# ---------------------------------------------------------------- assembler additions
def _asm_cls():
    from . import codemods as C

    class A(C.Asm):
        def sll(self, rd, rt, sa, c=""): self._rr(0, rd, "zero", rt, sa, c)
        def sltu(self, rd, rs, rt, c=""): self._rr(43, rd, rs, rt, c=c)
        def slt(self, rd, rs, rt, c=""): self._rr(42, rd, rs, rt, c=c)
        def lh(self, rt, off, rs, c=""): self._i(33, rs, rt, off, c)
        def lhu(self, rt, off, rs, c=""): self._i(37, rs, rt, off, c)
        def lb(self, rt, off, rs, c=""): self._i(32, rs, rt, off, c)
        def li(self, rt, v, c=""):
            if -0x8000 <= v < 0x8000:
                self.addiu(rt, "zero", v, c)
            else:
                self.lui(rt, (v >> 16) & 0xFFFF, c)
                if v & 0xFFFF:
                    self.ori(rt, rt, v & 0xFFFF)
    return A


# ---------------------------------------------------------------- hitlog
def build_hitlog(img, hl):
    """hl = [(addr, name)].  -> (cave words, hook words)"""
    from . import codemods as C
    A = _asm_cls()
    n = len(hl)
    if not n:
        raise ValueError("hitlog needs at least one function")
    a = A(CAVE_HL)
    hooks = []
    for i, (fn, name) in enumerate(hl):
        d0, d1 = check_displaced(img, fn)
        while a.pc < CAVE_HL + i * HL_STUB:
            a.nop()
        t = HL_TABLE + i * 32
        a.la("at", HL_DATA, "stub %d: %s @0x%08X" % (i, name or "?", fn))
        a.lw("v0", 8, "at", "seq")
        a.addiu("v0", "v0", 1)
        a.sw("v0", 8, "at")
        a.lw("v1", t + 4, "at", "count")
        a.addiu("v1", "v1", 1)
        a.sw("v1", t + 4, "at")
        a.sw("a0", t + 8, "at")
        a.sw("a1", t + 12, "at")
        a.sw("a2", t + 16, "at")
        a.sw("a3", t + 20, "at")
        a.sw("ra", t + 24, "at", "caller")
        a.andi("v1", "v0", HL_RING - 1)
        a.sll("v1", "v1", 4)
        a.addu("v1", "v1", "at")
        a.sw("v0", HL_RINGOFF, "v1", "ring: seq")
        a.li("t9", fn)
        a.sw("t9", HL_RINGOFF + 4, "v1", "ring: fn")
        a.sw("a0", HL_RINGOFF + 8, "v1")
        a.sw("a1", HL_RINGOFF + 12, "v1")
        a.lw("v0", 0x10, "at", "shadow depth")
        a.slti("v1", "v0", 16)
        a.beq("v1", "zero", "nopush%d" % i)
        a.nop()
        a.sll("v1", "v0", 3)
        a.addu("v1", "v1", "at")
        a.sw("ra", HL_SHADOW, "v1", "push real ra")
        a.addiu("t9", "zero", i)
        a.sw("t9", HL_SHADOW + 4, "v1")
        a.addiu("v0", "v0", 1)
        a.sw("v0", 0x10, "at")
        a.la("ra", HL_TRAMP, "return through the trampoline")
        a.label("nopush%d" % i)
        a.raw(d0, "displaced")
        a.raw(d1, "displaced")
        a.j(fn + 8)
        a.nop()
        assert a.pc <= CAVE_HL + (i + 1) * HL_STUB, "stub too long"
        hooks.append((fn, C._j_word(CAVE_HL + i * HL_STUB), "j hitlog stub %d" % i))
        hooks.append((fn + 4, 0, "nop"))
    while a.pc < HL_TRAMP:
        a.nop()
    # trampoline: v0/v1 are the callee's return values -- touch only caller-saved t regs
    a.la("at", HL_DATA, "trampoline")
    a.lw("t0", 0x10, "at")
    a.addiu("t0", "t0", -1)
    a.sw("t0", 0x10, "at")
    a.sll("t1", "t0", 3)
    a.addu("t1", "t1", "at")
    a.lw("t2", HL_SHADOW, "t1", "real ra")
    a.lw("t3", HL_SHADOW + 4, "t1", "function index")
    a.sll("t3", "t3", 5)
    a.addu("t3", "t3", "at")
    a.sw("v0", HL_TABLE + 28, "t3", "last return value")
    a.jr("t2")
    a.nop()
    words = a.assemble()
    # data: header + per-function slot address, ring zeroed
    data = {HL_DATA: struct.unpack("<I", b"HLOG")[0], HL_DATA + 4: n}
    for k in range(8, HL_SIZE, 4):
        data[HL_DATA + k] = 0
    for i, (fn, _) in enumerate(hl):
        data[HL_DATA + HL_TABLE + i * 32] = fn
    cave = [(ad, w, c) for ad, w, c in words] + [(ad, w, "hitlog data") for ad, w in sorted(data.items())]
    return cave, hooks


# ---------------------------------------------------------------- memwatch
def encode_watch(entries):
    """-> 16-byte records: u32 addr, u8 type, u8 nderef, u16 0, s16 off1, s16 off2, u32 0."""
    out = []
    for label, addr, t, chain in entries:
        offs = (chain + [0, 0])[:2]
        out.append(struct.pack("<IBBHhhI", addr, TYPES[t], len(chain), 0, offs[0], offs[1], 0))
    return out


def build_memwatch(img, entries):
    from . import codemods as C
    A = _asm_cls()
    d0, d1 = check_displaced(img, MW_HOOK)
    a = A(CAVE_MW)
    a.addiu("sp", "sp", -64, "save t0-t7")
    for k in range(8):
        a.sd("t%d" % k, 8 * k, "sp")
    a.la("at", MW_DATA)
    a.lw("v0", 8, "at")
    a.addiu("v0", "v0", 1)
    a.sw("v0", 8, "at", "frames++")
    a.lw("t2", 4, "at", "count")
    a.addiu("t0", "at", MW_TABLE)
    a.addiu("t1", "at", MW_RESULTS)
    a.beq("t2", "zero", "mw_done")
    a.nop()
    a.label("mw_loop")
    a.lw("t3", 0, "t0", "A = start address")
    a.lbu("t4", 5, "t0", "nderef")
    a.addiu("t5", "zero", 3, "align mask for a pointer slot")
    a.addiu("t6", "zero", 0, "status")
    for k in range(2):
        a.beq("t4", "zero", "mw_fin")                  # no more derefs
        a.nop()
        _valid(a, "t3", "t5", "mw_bad")
        a.lw("t3", 0, "t3", "p = *A")
        a.beq("t3", "zero", "mw_null")
        a.nop()
        a.lh("t7", 8 + 2 * k, "t0")
        a.addu("t3", "t3", "t7", "A = p + off")
        a.addiu("t4", "t4", -1)
    a.label("mw_fin")
    a.lbu("t4", 4, "t0", "type")
    a.addiu("t5", "zero", 3)
    a.addiu("v1", "zero", 3)
    a.beq("t4", "v1", "mw_a1")                         # u16
    a.addiu("v1", "zero", 5)
    a.beq("t4", "v1", "mw_a1")                         # s16
    a.addiu("v1", "zero", 4)
    a.beq("t4", "v1", "mw_a0")                         # u8
    a.nop()
    a.beq("zero", "zero", "mw_chk")
    a.nop()
    a.label("mw_a1")
    a.addiu("t5", "zero", 1)
    a.beq("zero", "zero", "mw_chk")
    a.nop()
    a.label("mw_a0")
    a.addiu("t5", "zero", 0)
    a.label("mw_chk")
    _valid(a, "t3", "t5", "mw_bad")
    a.addiu("v1", "zero", 4)
    a.beq("t4", "v1", "mw_lbu")
    a.addiu("v1", "zero", 3)
    a.beq("t4", "v1", "mw_lhu")
    a.addiu("v1", "zero", 5)
    a.beq("t4", "v1", "mw_lh")
    a.nop()
    a.lw("t7", 0, "t3")
    a.beq("zero", "zero", "mw_ok")
    a.nop()
    a.label("mw_lbu")
    a.lbu("t7", 0, "t3")
    a.beq("zero", "zero", "mw_ok")
    a.nop()
    a.label("mw_lhu")
    a.lhu("t7", 0, "t3")
    a.beq("zero", "zero", "mw_ok")
    a.nop()
    a.label("mw_lh")
    a.lh("t7", 0, "t3")
    a.label("mw_ok")
    a.addiu("t6", "zero", 1, "status 1 = ok")
    a.beq("zero", "zero", "mw_store")
    a.nop()
    a.label("mw_null")
    a.addiu("t6", "zero", 2, "status 2 = null pointer")
    a.beq("zero", "zero", "mw_zero")
    a.nop()
    a.label("mw_bad")
    a.addiu("t6", "zero", 3, "status 3 = bad / misaligned address")
    a.label("mw_zero")
    a.addiu("t7", "zero", 0)
    a.label("mw_store")
    a.sw("t3", 0, "t1", "result: final address")
    a.sw("t7", 4, "t1", "result: value")
    a.sw("t6", 8, "t1", "result: status")
    a.sw("v0", 12, "t1", "result: frame")
    a.addiu("t0", "t0", 16)
    a.addiu("t1", "t1", 16)
    a.addiu("t2", "t2", -1)
    a.bne("t2", "zero", "mw_loop")
    a.nop()
    a.label("mw_done")
    for k in range(8):
        a.ld("t%d" % k, 8 * k, "sp")
    a.addiu("sp", "sp", 64)
    a.raw(d0, "displaced")
    a.raw(d1, "displaced")
    a.j(MW_HOOK + 8)
    a.nop()
    cave = list(a.assemble())
    assert a.pc < MW_DATA, "memwatch cave overruns its data"
    data = {MW_DATA: struct.unpack("<I", b"MWCH")[0], MW_DATA + 4: len(entries)}
    for k in range(8, MW_SIZE, 4):
        data[MW_DATA + k] = 0
    for i, rec in enumerate(encode_watch(entries)):
        for j in range(4):
            data[MW_DATA + MW_TABLE + 16 * i + 4 * j] = struct.unpack_from("<I", rec, 4 * j)[0]
    cave += [(ad, w, "memwatch data") for ad, w in sorted(data.items())]
    hooks = [(MW_HOOK, C._j_word(CAVE_MW), "j memwatch cave"), (MW_HOOK + 4, 0, "nop")]
    return cave, hooks


def _valid(a, reg, align, bad):
    """jump to `bad` unless PTR_LO <= reg < PTR_HI and (reg & align) == 0.  Clobbers v0? no: uses v1 only + t7."""
    a.lui("t7", PTR_LO >> 16)
    a.sltu("t7", reg, "t7")
    a.bne("t7", "zero", bad)
    a.nop()
    a.lui("t7", PTR_HI >> 16)
    a.sltu("t7", reg, "t7")
    a.beq("t7", "zero", bad)
    a.nop()
    a.and_("t7", reg, align)
    a.bne("t7", "zero", bad)
    a.nop()
