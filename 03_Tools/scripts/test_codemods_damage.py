#!/usr/bin/env python3
"""[CMD] Damage-scaling code mods (Double Damage, Hard Mode) + new-mod combinations, offline. Reads 01_Vanilla, writes nothing.

    py -3 scripts/test_codemods_damage.py

Double Damage and Hard Mode live in the same cave as God Mode / One-Hit Kills (Health::TakeDamage's
`hp -= damage; store hp`). For EVERY subset of those four keys the cave is run in the R5900 interpreter for a
hit on Red, on an enemy and before any player exists, and the hp left must be exactly the documented rule.
The vanilla routine after the hook is scanned: the damage register f20 is not read again before it is
rewritten, so doubling it changes the hp result only. Then every subset of the 2026-11-13 mods
(unlockall, dmgx2, hardmode, moneyx5) is built alone and with all older core mods, and all four with
each older core mod alone: no cave overlap, no hook clash, the feature mask reads back.
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
def check(ok, msg, quiet=False):
    if not ok or not quiet: print(("PASS " if ok else "FAIL ") + msg)
    if not ok: fails.append(msg)

with iso9660.Iso9660(VANILLA_ISO) as I:
    elf = I.read_file("SLUS_205.00")
img = C.unpack_image(elf)
check([C.KEYS.index(k) for k in ("unlockall", "dmgx2", "hardmode", "moneyx5")] == [11, 12, 13, 14] and C.KEYS[-1] == "moneyx5",
      "new keys at the END: unlockall bit 11, dmgx2 12, hardmode 13, moneyx5 14 (older mod packs keep their bit positions)")
check(all(k not in C.TOGGLE and k not in C.ROW_LABEL for k in ("dmgx2", "hardmode")), "always-on: no button combo, no Cheats row")
check(all(ok for ok, _ in C.verify_addresses(img)), "address check passes on the vanilla executable")

# --- f20 after the hook: scan the vanilla routine from DMG_SITE+8 to its jr ra
f20_read, pc = None, C.DMG_SITE + 8
for _ in range(400):
    w = img.word(pc)
    if (w >> 26) == 17 and ((w >> 21) & 31) in (16, 20):           # COP1 S/W arithmetic: ft=20..16, fs=15..11, fd=10..6
        ft, fs, fd = (w >> 16) & 31, (w >> 11) & 31, (w >> 6) & 31
        if 20 in (ft, fs): f20_read = pc; break
        if fd == 20: break
    if (w >> 26) == 57 and ((w >> 16) & 31) == 20: f20_read = pc; break  # swc1 f20
    if (w >> 26) == 49 and ((w >> 16) & 31) == 20: break                 # lwc1 f20 = rewritten
    if (w >> 26) == 17 and ((w >> 21) & 31) == 4 and ((w >> 11) & 31) == 20: f20_read = pc; break  # mfc1 from f20
    if w == 0x03E00008: break                                             # jr ra
    pc += 4
check(f20_read is None, "vanilla code after the hook never reads the damage register f20 again (scanned to 0x%08X)" % pc)

fb = lambda v: struct.unpack("<I", struct.pack("<f", v))[0]
bf = lambda w: struct.unpack("<f", struct.pack("<I", w & 0xFFFFFFFF))[0]
def expect(sub, who):
    if who == "player":
        return 100.0 if "godmode" in sub else (40.0 if "hardmode" in sub else 70.0)
    return 0.0 if "onehitkill" in sub else (40.0 if "dmgx2" in sub else 70.0)
DK = list(C.DAMAGE_KEYS)
n_runs = 0
for n in range(1, 5):
    for sub in itertools.combinations(DK, n):
        new, words, hooks, parts = C.patch_elf(elf, list(sub))
        tag = "+".join(sub)
        check(hooks == {C.DMG_SITE: C._j_word(C.CAVE_DMG), C.DMG_SITE + 4: 0} and C.elf_features(new) == list(sub),
              "%s: hooks are exactly j cave + nop at 0x%08X" % (tag, C.DMG_SITE))
        check(all(C.CAVE_DMG <= a < C.CAVE_DMG + 0x100 for a in words), "%s: cave stays in 0x%08X..+0x100 (%d words)" % (tag, C.CAVE_DMG, len(words)), quiet=True)
        for who in ("player", "enemy", "noplayer"):
            ee = emu64.EE(bytearray(0x02000000))
            for a_, w in words.items(): ee.wr(a_, 4, w)
            PL, HP_P, HP_E = 0x01000000, 0x01000100, 0x01000200
            ee.wr(C.PLAYER_PTR, 4, 0 if who == "noplayer" else PL); ee.wr(PL + 0x18, 4, HP_P)
            for h in (HP_P, HP_E): ee.wr(h + 16, 4, fb(100.0))
            s0 = HP_P if who == "player" else HP_E
            ee.r[:] = [0] * 32; ee.r[16] = s0
            ee.f[0] = fb(100.0); ee.f[20] = fb(30.0)
            ee.stops = {C.DMG_SITE + 8}
            try:
                res = ee.run(C.CAVE_DMG, 200); got = bf(ee.rd(s0 + 16, 4))
            except Exception as ex:
                check(False, "%s/%s: interpreter error %s: %s" % (tag, who, type(ex).__name__, ex)); continue
            exp = expect(sub, "player" if who == "player" else "enemy")
            n_runs += 1
            check(res[0] == "stop" and got == exp, "%s: a 30-damage hit on %s (100 hp) leaves %.0f (want %.0f)" % (tag, who, got, exp))

# --- every subset of the new mods with the older core mods
NEW = ["unlockall", "dmgx2", "hardmode", "moneyx5"]
OLD = [k for k in C.CORE_KEYS if k not in NEW]
builds = 0
combos = [(sub, extra) for n in range(1, 5) for sub in itertools.combinations(NEW, n) for extra in ([], OLD)]
combos += [(tuple(NEW), [k]) for k in OLD]
if True:
    for sub, extra in combos:
        if True:
            feats = [k for k in C.KEYS if k in set(sub) | set(extra)]
            try:
                new, words, hooks, parts = C.patch_elf(elf, feats)
                ok = C.elf_features(new) == feats
            except Exception as ex:
                ok = False; print("   ", feats, ex)
            builds += 1
            check(ok, "%s + %s builds, mask reads back" % ("+".join(sub), "+".join(extra) or "nothing"), quiet=True)
check(not any("builds" in f for f in fails), "%d builds: every subset of %s alone and with all older core mods; all of them with each older core mod" % (builds, "+".join(NEW)))
new, words, hooks, parts = C.patch_elf(elf, C.CORE_KEYS)
check(C.elf_features(new) == C.CORE_KEYS, "all %d core mods together build with no cave or hook conflict" % len(C.CORE_KEYS))
print("\n%s (%d cave runs)" % ("ALL PASS" if not fails else "%d FAIL" % len(fails), n_runs))
sys.exit(1 if fails else 0)
