#!/usr/bin/env python3
"""[CM-R] Research code mods (Function-Hit Logger + Memory Watch), offline, in the R5900 interpreter.
Reads 01_Vanilla, writes nothing.      py -3 scripts/test_codemods_research.py

Proves: the installer places both mods; the hit logger really counts a call, records args / caller / RETURN VALUE and
leaves the call's result unchanged (the game function runs from its displaced words); the memory-watch table follows a
pointer chain and reports null / bad pointers as such instead of touching memory; hooks that collide are refused;
unsafe functions (control flow in the first two words) are refused; and every combination with the other mods builds.
It does NOT prove the game plays with them -- that is the PCSX2 proof in 05_Docs/CODEMODS.md."""
import itertools, os, struct, sys, json, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, 'r5900'))
from rdrstudio.config import VANILLA_ISO
from rdrstudio import codemods as C, codemods_research as R, iso9660
try:
    import emu64
except ImportError:
    print('SKIP  needs the R5900 interpreter (emu64.py + mips.py) in scripts/r5900/ -- see CONTRIBUTING.md')
    sys.exit(0)

fails = []
def check(ok, msg):
    print(("PASS " if ok else "FAIL ") + msg)
    if not ok: fails.append(msg)

with iso9660.Iso9660(VANILLA_ISO) as I:
    elf = I.read_file("SLUS_205.00")
img = C.unpack_image(elf)
SENT = 0x00700000

def boot(feats, cfg):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "cfg.json"); json.dump(cfg, open(p, "w"))
    os.environ["RDR_RESEARCH_CONFIG"] = p
    new, words, hooks, parts = C.patch_elf(elf, feats)
    ram = bytearray(32 << 20)
    ph = struct.unpack_from("<8I", new, 52)
    ram[ph[2]:ph[2] + ph[4]] = new[ph[1]:ph[1] + ph[4]]
    ram[C.IMG_BASE:C.IMG_BASE + len(img.data)] = img.data
    ee = emu64.EE(ram); ee.stops = {C.GAME_ENTRY}; ee.r[29] = 0x01F00000
    res = ee.run(C.INSTALLER_VA, 6_000_000)
    return ee, words, hooks, res, new

# --- hit logger -----------------------------------------------------------------------------------------------
FN = 0x0017E778          # IsCheatOn(bit): a small leaf that reads the profile bitmask
cfg = {"hitlog": [{"addr": FN, "name": "IsCheatOn"}, {"addr": 0x003D4BC0, "name": "csv"}], "watch": []}
ee, words, hooks, res, new = boot(["hitlog"], cfg)
check(res == ("stop", C.GAME_ENTRY), "installer with the hit logger runs to the game entry")
check(C.elf_features(new) == ["hitlog"], "ELF reports the hitlog mod")
check(all(R.CAVE_HL <= a < R.HL_DATA + R.HL_SIZE for a in words), "hitlog caves/data stay in 0x000F2000-0x000F3AFF")
check(ee.rd(R.HL_DATA, 4) == struct.unpack("<I", b"HLOG")[0] and ee.rd(R.HL_DATA + 4, 4) == 2, "log header says HLOG, 2 functions")
check(ee.rd(R.HL_DATA + R.HL_TABLE, 4) == FN and ee.rd(R.HL_DATA + R.HL_TABLE + 32, 4) == 0x003D4BC0, "function table holds the chosen addresses")
# reference result with the hook absent
ref = emu64.EE(bytearray(ee.m)); ref.r[:] = [0] * 32
for a_, w in hooks.items():            # un-hook: restore vanilla words
    ref.wr(a_, 4, img.word(a_) if a_ < C.IMG_BASE + len(img.data) else w)
for bit in (6, 10, 3):
    ee.stops = {SENT}
    ref.stops = {SENT}
    # give the profile a cheat mask so the answer is not just 0: profile base 0x0073DE28 + index*0xB18, mask at +2144
    for e_ in (ee, ref):
        e_.wr(0x00742118, 4, 0)
        e_.wr(0x0073DE28 + 2144, 4, 0b1000_0100_0000)
    ref.r[4] = 7; ref.r[5] = bit; ref.r[29] = 0x01F7E000; ref.r[31] = SENT
    rr = ref.run(FN, 10000)
    ee.r[4] = 7; ee.r[5] = bit; ee.r[29] = 0x01F7E000; ee.r[31] = SENT
    r1 = ee.run(FN, 10000)
    check(r1[0] == "stop" and ee.r[2] == ref.r[2] and ee.r[29] == 0x01F7E000,
          "hooked IsCheatOn(%d) returns the same value as the vanilla function (v0=%d) and the stack is balanced" % (bit, ee.r[2] & 0xFFFFFFFF))
slot = R.HL_DATA + R.HL_TABLE
check(ee.rd(slot + 4, 4) == 3, "call count = 3")
check(ee.rd(slot + 8, 4) == 7 and ee.rd(slot + 12, 4) == 3, "last args recorded (a0=7, a1=3)")
check(ee.rd(slot + 24, 4) == SENT, "caller (return address) recorded = the real caller")
check(ee.rd(slot + 28, 4) == ee.r[2] & 0xFFFFFFFF, "RETURN VALUE recorded = v0 of the last call")
ee.r[4] = 7; ee.r[5] = 6; ee.r[29] = 0x01F7E000; ee.r[31] = SENT; ee.run(FN, 10000)
check(ee.r[2] == 1 and ee.rd(slot + 28, 4) == 1, "a NON-ZERO return value (IsCheatOn(bit 6) = 1 with that mask) is recorded as 1")
ee.wr(R.HL_DATA + 8, 4, 3); ee.wr(slot + 4, 4, 3)
check(ee.rd(R.HL_DATA + 8, 4) == 3 and ee.rd(R.HL_DATA + 0x10, 4) == 0, "global sequence = 3 calls, shadow stack back to depth 0")
ring = [(ee.rd(R.HL_DATA + R.HL_RINGOFF + 16 * i, 4), ee.rd(R.HL_DATA + R.HL_RINGOFF + 16 * i + 4, 4), ee.rd(R.HL_DATA + R.HL_RINGOFF + 16 * i + 12, 4)) for i in (1, 2, 3)]
check(ring == [(1, FN, 6), (2, FN, 10), (3, FN, 3)], "ring buffer holds calls 1..3 with their second argument (the cheat bit): %r" % (ring,))
check(ee.rd(slot + 32 + 4, 4) == 0, "the other hooked function was never called: count 0")

# --- memory watch ---------------------------------------------------------------------------------------------
wcfg = {"hitlog": [], "watch": [
    {"label": "ptr", "addr": 0x0079E240, "type": "u32"},
    {"label": "x", "addr": 0x0079E240, "type": "f32", "chain": [0x54]},
    {"label": "nullchain", "addr": 0x0079EEE4, "type": "u32", "chain": [8]},
    {"label": "badptr", "addr": 0x0079E300, "type": "u32", "chain": [8]},
    {"label": "u8", "addr": 0x0079E240, "type": "u8", "chain": [0x60]},
    {"label": "s16", "addr": 0x0079E240, "type": "s16", "chain": [0x62]},
    {"label": "twohop", "addr": 0x0079E320, "type": "u32", "chain": [4, 8]},
    {"label": "misaligned", "addr": 0x0079E240, "type": "u32", "chain": [0x57]},
]}
ee, words, hooks, res, new = boot(["memwatch"], wcfg)
check(res == ("stop", C.GAME_ENTRY), "installer with memwatch runs to the game entry")
check(all(R.CAVE_MW <= a < R.MW_DATA + R.MW_SIZE for a in words), "memwatch caves/data stay in 0x000F5000-0x000F5DFF")
P = 0x00300000
ee.wr(0x0079E240, 4, P)                                  # player pointer
ee.wr(P + 0x54, 4, struct.unpack("<I", struct.pack("<f", 7.5))[0])
ee.wr(P + 0x60, 1, 0xAB); ee.wr(P + 0x62, 2, 0xFFFE)    # s16 -2
ee.wr(0x0079EEE4, 4, 0)                                  # camera pointer null
ee.wr(0x0079E300, 4, 0x7FFFFFF0)                         # bad pointer (outside RAM)
ee.wr(0x0079E320, 4, 0x00310000); ee.wr(0x00310004, 4, 0x00320000); ee.wr(0x00320008, 4, 0xC0FFEE)
ee.stops = {C.MW_HOOK + 8} if hasattr(C, "MW_HOOK") else {R.MW_HOOK + 8}
ee.r[29] = 0x01F7E000
for k in range(2):
    ee.r[29] = 0x01F7E000
    r = ee.run(R.CAVE_MW, 200000)
    check(r == ("stop", R.MW_HOOK + 8), "memwatch cave (frame %d) runs the displaced words and continues at pad-update+8" % (k + 1))
check(ee.r[29] == 0x01F7E000 - 48, "stack: the cave restores sp (only the displaced `addiu sp,-48` remains)")
def res_(i):
    b = R.MW_DATA + R.MW_RESULTS + 16 * i
    return ee.rd(b, 4), ee.rd(b + 4, 4), ee.rd(b + 8, 4), ee.rd(b + 12, 4)
check(res_(0)[1:3] == (P, 1), "u32 plain: value = player pointer, ok")
x = res_(1); check(x[0] == P + 0x54 and struct.unpack("<f", struct.pack("<I", x[1]))[0] == 7.5 and x[2] == 1, "pointer chain player->+0x54 reads 7.5 as float")
check(res_(2)[2] == 2, "null pointer in the chain -> status 2 (null), no memory touched")
check(res_(3)[2] == 3, "pointer outside RAM -> status 3 (bad), no memory touched")
check(res_(4)[1] == 0xAB and res_(4)[2] == 1, "u8 read")
check(res_(5)[1] == 0xFFFFFFFE and res_(5)[2] == 1, "s16 read sign-extended")
check(res_(6)[1] == 0xC0FFEE and res_(6)[2] == 1, "two-hop chain")
check(res_(7)[2] == 3, "misaligned u32 -> status 3, no unaligned load (would be an exception on the PS2)")
check(ee.rd(R.MW_DATA + 8, 4) == 2 and res_(0)[3] == 2, "frame counter advanced to 2")

# --- refusals ---------------------------------------------------------------------------------------------------
def refused(cfg_, feats, what):
    d = tempfile.mkdtemp(); p = os.path.join(d, "c.json"); json.dump(cfg_, open(p, "w"))
    os.environ["RDR_RESEARCH_CONFIG"] = p
    try:
        C.patch_elf(elf, feats); ok = False
    except ValueError as ex:
        ok = True; print("     (refused: %s)" % ex)
    check(ok, what)
refused({"hitlog": [{"addr": 0x00268628}], "watch": []}, ["hitlog", "freeze"], "hitlog on a function the freeze mod already hooks is refused")
refused({"hitlog": [{"addr": 0x0017E77C}], "watch": []}, ["hitlog"], "hitlog on function+4 (not a function start: lw reads a register we clobber / branch target rules) is refused or unsafe")
refused({"hitlog": [{"addr": 0x001431B0}], "watch": []}, ["hitlog", "memwatch"], "hitlog on the memwatch hook is refused")
refused({"hitlog": [{"addr": 0x00100001}], "watch": []}, ["hitlog"], "unaligned address refused")
refused({"hitlog": [], "watch": [{"label": "x", "addr": 0x100000, "type": "f64"}]}, ["memwatch"], "unknown watch type refused")
refused({"hitlog": [], "watch": [{"label": "x", "addr": 0x100000, "chain": [1, 2, 3]}]}, ["memwatch"], "chain longer than 2 refused")

# --- combos build and keep every cave inside the free block -----------------------------------------------------------
os.environ.pop("RDR_RESEARCH_CONFIG", None)
for sub in (["hitlog"], ["memwatch"], ["hitlog", "memwatch"], C.KEYS):
    new, words, hooks, parts = C.patch_elf(elf, list(sub))
    check(C.elf_features(new) == [k for k in C.KEYS if k in sub] and all(0x000F2000 <= a < 0x000FF000 for a in words),
          "%s: builds, reports itself, all caves inside 0x000F2000-0x000FEFFF" % "+".join(sub))
plan = C.plan_iso(VANILLA_ISO, ["hitlog", "memwatch"])
check(plan["fits"] and plan["page_deflated"] == 0, "research-only build leaves the Cheats page alone (no row, nothing to fit)")
print("\nFAILED: %d" % len(fails) if fails else "\nALL PASS")
sys.exit(1 if fails else 0)
