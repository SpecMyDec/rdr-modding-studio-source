#!/usr/bin/env python3
"""[CMM] Money x5 code mod, offline. Reads 01_Vanilla, writes nothing.

    py -3 scripts/test_codemods_money.py

The cave wraps the wallet's add-money routine 0x00160198 (wallet, amount, kind) -- the one place money comes in
(pickups, the give-money virtual that Big Money and rewards use) and goes out (take-money negates the amount).
In the R5900 interpreter the cave AND the real vanilla routine run until it branches on the amount: a positive
amount arrives x5 in a3 (the routine's working copy), zero and negative (spending) amounts arrive unchanged,
sp is what the vanilla code expects.  (2026-11-14: was the give-money virtual, which pickups never call.)
"""
import os, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, 'r5900'))
from rdrstudio.config import VANILLA_ISO
from rdrstudio import codemods as C, iso9660
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
check(C.KEYS[-1] == "moneyx5" and C.KEYS.index("moneyx5") == 14, "moneyx5 is the LAST key, bit 14")
check("moneyx5" not in C.TOGGLE and "moneyx5" not in C.ROW_LABEL, "always-on: no button combo, no Cheats row")
check(all(ok for ok, _ in C.verify_addresses(img)), "address check passes on the vanilla executable")
def jal(t): return 0x0C000000 | (t >> 2)
check(sorted(a for a in range(0x100000, 0x549FE8, 4) if img.word(a) == jal(C.WALLET_FN)) == [0x0035EE6C, 0x0036F2B8, 0x0036F2EC],
      "add-money 0x%08X has exactly 3 callers: pickups 0x0035EE6C, give-money 0x0036F2B8, take-money 0x0036F2EC" % C.WALLET_FN)
check(not [a for a in range(0x100000, 0x549FE8, 4) if img.word(a) == C.WALLET_FN], "add-money is never taken by address (no vtable / pointer use)")
check(img.word(C.MONEY_VT_SLOT) == C.MONEY_FN, "player vtable slot 0x174 (0x%08X) points at give-money 0x%08X" % (C.MONEY_VT_SLOT, C.MONEY_FN))
# the Big Money cheat calls slot 0x174 with 10000 (addiu a1,zero,10000 / lw v0,372(v1) / jalr) ...
check(img.word(0x00122D34) == 0x24052710 and img.word(0x00122D4C) == 0x8C620174, "Big Money cheat = slot 0x174 with amount 10000")
# ... and give-money hands its own amount (stored at 32(sp) on entry) to add-money; take-money negates first
check(img.word(0x0036F1BC) == 0xAFA50020 and img.word(0x0036F2B4) == 0x8FA50020, "give-money passes its amount (32(sp)) to add-money")
check(img.word(0x0036F2E8) == 0x00052823, "take-money negates the amount (subu a1,zero,a1) before add-money: spending is negative")
new, words, hooks, parts = C.patch_elf(elf, ["moneyx5"])
check(hooks == {C.WALLET_FN: C._j_word(C.CAVE_MONEY), C.WALLET_FN + 4: 0} and C.elf_features(new) == ["moneyx5"],
      "hook = j cave + nop on the first two words of add-money")
check(all(C.CAVE_MONEY <= a < C.CAVE_MONEY + 0x100 for a in words), "cave stays in 0x%08X..+0x100 (%d words)" % (C.CAVE_MONEY, len(words)))
check(not set(range(C.MONEY_FN, C.MONEY_FN + 8, 4)) & set(hooks), "the old hook (give-money virtual) is gone: no double x5 for Big Money")
SP, WAL = 0x01800000, 0x01000000
def run(amount, mods=True):
    ee = emu64.EE(bytearray(0x02000000))
    for a_ in range(C.WALLET_FN, C.WALLET_FN + 0x80, 4): ee.wr(a_, 4, img.word(a_))
    if mods:
        for a_, w in words.items(): ee.wr(a_, 4, w)
        for a_, w in hooks.items(): ee.wr(a_, 4, w)
    ee.r[:] = [0] * 32
    ee.r[29] = SP; ee.r[4] = WAL; ee.r[5] = amount & 0xFFFFFFFFFFFFFFFF if amount >= 0 else (amount & 0xFFFFFFFF) | 0xFFFFFFFF00000000
    ee.r[6] = 1; ee.r[16] = 0xABCD
    ee.stops = {0x001601F0, 0x001601B0}          # amount != 0 path / amount == 0 path
    res = ee.run(C.WALLET_FN, 100)
    a3 = struct.unpack("<i", struct.pack("<I", ee.r[7] & 0xFFFFFFFF))[0]
    return res[0], ee.pc if hasattr(ee, "pc") else None, a3, ee.r[29] & 0xFFFFFFFF, ee.rd(SP - 48 + 32, 4)
for amt, want in ((10000, 50000), (8, 40), (25, 125), (1, 5), (0, 0), (-100, -100), (-1, -1)):
    st, pc, a3, sp, s0s = run(amt)
    check(st == "stop" and a3 == want and sp == SP - 48 and s0s == 0xABCD,
          "add %6d -> the routine works on %6d (want %6d), stack frame + saved s0 as vanilla" % (amt, a3, want))
st, pc, a3, *_ = run(10000, mods=False)
check(st == "stop" and a3 == 10000, "control: the vanilla routine alone works on 10000")
print("\n%s" % ("ALL PASS" if not fails else "%d FAIL" % len(fails)))
sys.exit(1 if fails else 0)
