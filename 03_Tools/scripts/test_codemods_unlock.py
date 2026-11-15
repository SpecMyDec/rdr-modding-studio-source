#!/usr/bin/env python3
"""[CMU] Unlock Everything code mod, offline. Reads 01_Vanilla, writes nothing.

    py -3 scripts/test_codemods_unlock.py

The cave wraps 0x00183028 (manager, index) -> unlocked?, the game's one unlock test. Run in the R5900
interpreter on the REAL vanilla function with a stand-in unlockables table built from the shipped
unlockables.csv row names: every row reads unlocked, the two cheats the game never implemented
(cheat_showposition / cheat_showaiinfo) and an index past the table keep the original answer.
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
check(C.KEYS.index("unlockall") == 11, "unlockall keeps bit 11 (later mods are only ever appended after it)")
check("unlockall" not in C.TOGGLE and "unlockall" not in C.ROW_LABEL, "always-on: no button combo, no Cheats row")
check(all(ok for ok, _ in C.verify_addresses(img)), "address check passes on the vanilla executable")
new, words, hooks, parts = C.patch_elf(elf, ["unlockall"])
check(hooks == {C.UNLOCK_FN: C._j_word(C.CAVE_UNLOCK), C.UNLOCK_FN + 4: 0} and C.elf_features(new) == ["unlockall"],
      "hook = j cave + nop on the first two words of the unlock test")
check(all(C.CAVE_UNLOCK <= a < C.CAVE_UNLOCK + 0x200 for a in words), "cave stays in 0x%08X..+0x200 (%d words)" % (C.CAVE_UNLOCK, len(words)))
NAMES = ["cheat_bigmoney", "cheat_goldengun", "cheat_infinitefocus", "cheat_invulnerable", "cheat_showaiinfo",
         "cheat_showposition", "cheat_sudden_death", "cheat_sad_stars", "cheat_no_hud", "cheat_no_distort",
         "jaka", "jlvl_sher70", "red", "lvl_sher70"]
MGR, PROF, HDR, ARR, ROWS, STR, RET = 0x01000000, 0x01000400, 0x01000800, 0x01000900, 0x01000A00, 0x01001000, 0x00200000
def run(idx, owned_bits=0, header=True):
    ee = emu64.EE(bytearray(0x02000000))
    for a_ in range(C.UNLOCK_FN, C.UNLOCK_FN + 0x50, 4):
        ee.wr(a_, 4, img.word(a_))                      # the vanilla function
    for a_, w in words.items(): ee.wr(a_, 4, w)
    for a_, w in hooks.items(): ee.wr(a_, 4, w)
    ee.wr(MGR, 4, PROF); ee.wr(PROF + 0x24, 4, owned_bits); ee.wr(MGR + 0x18, 4, 0)
    ee.wr(MGR + 0x14, 4, HDR if header else 0)
    ee.wr(HDR, 4, ARR); ee.wr(HDR + 4, 2, len(NAMES))
    p = STR
    for i, n in enumerate(NAMES):
        ee.wr(ARR + 4 * i, 4, ROWS + 16 * i); ee.wr(ROWS + 16 * i, 4, p)
        for k, ch in enumerate(n.encode() + b"\0"): ee.wr(p + k, 1, ch)
        p += 32
    ee.r[:] = [0] * 32; ee.r[4] = MGR; ee.r[5] = idx; ee.r[31] = RET; ee.stops = {RET}
    res = ee.run(C.UNLOCK_FN, 200)
    return res[0], ee.r[2] & 0xFFFFFFFF
for i, n in enumerate(NAMES):
    st, v = run(i)
    want = 0 if n.startswith("cheat_sh") else 1
    check(st == "stop" and v == want, "%-20s nothing owned on the card -> %d (want %d)" % (n, v, want))
st, v = run(5, owned_bits=1 << 5)
check(st == "stop" and v == 1, "cheat_showposition owned on the card -> original test still answers 1")
st, v = run(len(NAMES) + 3)
check(st == "stop" and v == 0, "index past the table -> original test (0)")
st, v = run(0, header=False)
check(st == "stop" and v == 0, "no table loaded yet -> original test (0)")
new, words2, hooks2, parts2 = C.patch_elf(elf, C.CORE_KEYS)
check(C.elf_features(new) == C.CORE_KEYS, "all core mods together (cheatsmenu + unlockall share no hook) build with no conflict")
print("\n%s" % ("ALL PASS" if not fails else "%d FAIL" % len(fails)))
sys.exit(1 if fails else 0)
