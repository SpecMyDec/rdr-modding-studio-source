#!/usr/bin/env python3
"""[CMC] Combat code mods (infinite ammo, god mode, one-hit kills), offline. Reads 01_Vanilla, writes nothing.

    py -3 scripts/test_codemods_combat.py

Proves the patches are the ones measured live in PCSX2 (05_Docs/DEEPDIVE-2026-11-11.md): the ammo store is
nopped, the damage site jumps to one shared cave, every subset builds without touching anything else, the
caves stay in the spare block, and the cave -- run in the R5900 interpreter -- leaves the player's hp alone
(god), zeroes anyone else's (one-hit) and otherwise does exactly what the vanilla two instructions do.
"""
import itertools, os, struct, sys
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
NEW = ["infammo", "godmode", "onehitkill"]
check(C.KEYS[C.KEYS.index("infammo"):][:3] == NEW and C.KEYS.index("infammo") == 7, "the three combat mods keep bit positions 7-9 (older mod packs keep theirs)")
check(all(k not in C.TOGGLE and k not in C.ROW_LABEL for k in NEW), "always-on: no button combo, no Cheats row")
fb = lambda v: struct.unpack("<I", struct.pack("<f", v))[0]
bf = lambda w: struct.unpack("<f", struct.pack("<I", w & 0xFFFFFFFF))[0]
for n in (1, 2, 3):
    for sub in itertools.combinations(NEW, n):
        new, words, hooks, parts = C.patch_elf(elf, list(sub))
        want = {}
        if "infammo" in sub: want[C.AMMO_SITE] = 0
        if "godmode" in sub or "onehitkill" in sub:
            want[C.DMG_SITE] = C._j_word(C.CAVE_DMG); want[C.DMG_SITE + 4] = 0
        check(hooks == want and C.elf_features(new) == list(sub), "%s: hooks are exactly %s" % ("+".join(sub), ", ".join("0x%08X" % a for a in sorted(want))))
        check(all(C.CAVE_DMG <= a < C.CAVE_DMG + 0x100 for a in words), "%s: cave words stay in 0x%08X..+0x100 (%d words)" % ("+".join(sub), C.CAVE_DMG, len(words)))
        if not words: continue
        # run the cave: s0 = health component, f0 = hp, f20 = damage; stop at DMG_SITE+8
        for who in ("player", "enemy"):
            ee = emu64.EE(bytearray(0x02000000))
            for a_, w in words.items(): ee.wr(a_, 4, w)
            PL, HP_P, HP_E = 0x01000000, 0x01000100, 0x01000200
            ee.wr(C.PLAYER_PTR, 4, PL); ee.wr(PL + 0x18, 4, HP_P)
            for h in (HP_P, HP_E): ee.wr(h + 16, 4, fb(100.0))
            s0 = HP_P if who == "player" else HP_E
            try:
                ee.r[:] = [0] * 32; ee.r[16] = s0
                ee.f[0] = fb(100.0); ee.f[20] = fb(30.0)
                ee.stops = {C.DMG_SITE + 8}
                res = ee.run(C.CAVE_DMG, 200)
                got = bf(ee.rd(s0 + 16, 4))
            except Exception as ex:
                check(False, "%s/%s: interpreter could not run the cave (%s: %s)" % ("+".join(sub), who, type(ex).__name__, ex)); continue
            exp = 100.0 if (who == "player" and "godmode" in sub) else (0.0 if (who == "enemy" and "onehitkill" in sub) else 70.0)
            check(res[0] == "stop" and got == exp, "%s: a 30-damage hit on the %s's 100 hp leaves %.0f (want %.0f)" % ("+".join(sub), who, got, exp))
new, words, hooks, parts = C.patch_elf(elf, C.CORE_KEYS)
check(C.elf_features(new) == C.CORE_KEYS, "all core mods together build with no cave or hook conflict")
check(all(k in C.PROVEN or k in NEW for k in C.CORE_KEYS if C.KEYS.index(k) < C.KEYS.index("infammo")), "every older mod still has its PROVEN note")
print("\n%s" % ("ALL PASS" if not fails else "%d FAIL" % len(fails)))
sys.exit(1 if fails else 0)
