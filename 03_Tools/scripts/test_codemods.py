#!/usr/bin/env python3
"""[CM] Code Mods (community-research noclip / freeze / camera-lock hooks), offline. Reads 01_Vanilla, writes nothing.

    py -3 scripts/test_codemods.py

Proves: our own unpack of the vanilla executable agrees with the tester's; every hook address checks out
against it; every subset of the three mods patches the ELF touching only what it should; the boot
installer, run in an R5900 interpreter, leaves RAM = unpacked game + our hooks + our caves; the ELF
for all three mods is byte-identical to the one in the tester's own patched ISO (when his files are on this
machine); the Cheats page fits every archive slot for every subset. It does NOT prove the game plays --
that is the PCSX2 proof in 05_Docs/CODEMODS.md.
"""
import hashlib, itertools, os, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, 'r5900'))
from rdrstudio.config import VANILLA_ISO, ROOT
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
check(hashlib.sha1(img.data).hexdigest() == "68762fd2063624fe92718ef41b79c9c49c7747de",
      "our unpack of the vanilla executable = the tester's 4,497,384-byte image (sha1 68762fd2...)")
for ok, what in C.verify_addresses(img):
    check(ok, "address: " + what)

# --- every subset patches only what it should
# The subset sweeps below grow combinatorially, so they cover the mods up to fastred (the set this test was built
# for).  Newer always-on mods (2026-11-13: unlockall, dmgx2, hardmode, moneyx5) add no Cheats row and no toggle;
# each is checked alone and on top of all the older ones right here, and every subset of them in test_codemods_damage.py.
names = [k for k in C.CORE_KEYS if C.KEYS.index(k) <= C.KEYS.index("fastred")]
for k in [k for k in C.CORE_KEYS if k not in names]:
    for sub in ([k], names + [k]):
        new, words, hooks, parts = C.patch_elf(elf, sub)
        diff = [i for i in range(len(elf)) if new[i] != elf[i]]
        allowed = set(range(0x44, 0x4C)) | set(range(0x1000 + 0x20, 0x1000 + 0x24))
        check(not [i for i in diff if i not in allowed] and C.elf_features(new) == [x for x in C.KEYS if x in sub]
              and all(0x000FD000 <= a < 0x000FF000 for a in words), "%s: only header/stub/installer differ, mask reads back, caves in the block" % ("+".join(sub) if len(sub) < 3 else "all older + " + k))
ORIG3 = ["noclip", "freeze", "camlock"]
for n in (1, 2, 3, 5):
    for sub in itertools.combinations(names, n):
        if n == 2 and not set(sub) & {"cheatsmenu", "slowmo"}:
            continue
        new, words, hooks, parts = C.patch_elf(elf, list(sub))
        diff = [i for i in range(len(elf)) if new[i] != elf[i]]
        allowed = set(range(0x44, 0x4C)) | set(range(0x1000 + 0x20, 0x1000 + 0x24))
        stray = [i for i in diff if i not in allowed]
        check(not stray and len(new) > len(elf),
              "%s: only the segment size, the boot-stub jump and the appended installer differ" % "+".join(sub))
        check(C.elf_features(new) == list(sub), "%s: the ELF reports exactly these mods" % "+".join(sub))
        check(all(0x000FD000 <= a < 0x000FF000 for a in words), "%s: caves stay in 0x000FD000-0x000FEFFF" % "+".join(sub))
check(C.elf_features(elf) == [], "vanilla executable reports no mods")
try:
    C.patch_elf(C.patch_elf(elf, ["noclip"])[0], ["noclip"]); ok = False
except ValueError:
    ok = True
check(ok, "an already-patched executable is refused")

# --- the boot installer, in the interpreter
new, words, hooks, parts = C.patch_elf(elf, C.CORE_KEYS)
ram = bytearray(32 << 20)
ph = struct.unpack_from("<8I", new, 52)
ram[ph[2]:ph[2] + ph[4]] = new[ph[1]:ph[1] + ph[4]]       # the boot stub's segment
ram[C.IMG_BASE:C.IMG_BASE + len(img.data)] = img.data     # what the aPLib stub writes (proved equal above)
ee = emu64.EE(ram)
ee.stops = {C.GAME_ENTRY}
ee.r[29] = 0x01F00000
res = ee.run(C.INSTALLER_VA, 5_000_000)
check(res == ("stop", C.GAME_ENTRY) and ee.syscalls == [(0x64, 0), (0x64, 2)],
      "installer runs, flushes both caches (FlushCache 0 and 2) and jumps to 0x00100008: %r" % (res,))
bad = 0
for i in range(0, len(img.data) - 3, 4):
    a = C.IMG_BASE + i
    want = hooks.get(a, struct.unpack_from("<I", img.data, i)[0])
    if ee.rd(a, 4) != want: bad += 1
check(bad == 0, "after the installer the game image = unpacked vanilla + exactly our %d hook words (%d differ)" % (len(hooks), bad))
miss = sum(1 for a, w in words.items() if ee.rd(a, 4) != w)
check(miss == 0, "all %d cave/data words are in RAM (%d missing)" % (len(words), miss))
check(ee.rd(C.DATA, 4) == 0 and ee.rd(C.DATA + 0x7C, 4) == 0, "data block starts zeroed (every mod Off)")

# --- the tester's own patched ISO (reference), when present
sp = r"<research files> Dead Revolver (USA) [noclip menu].iso"
if os.path.exists(sp):
    saved = dict(C.ROW_LABEL)
    C.ROW_LABEL.update(noclip="Noclip", freeze="Freeze World", camlock="Camera Lock")
    try:
        mine = C.patch_elf(elf, ORIG3)[0]
    finally:
        C.ROW_LABEL.update(saved)
    with open(sp, "rb") as f:
        files, _ = C.iso_files(f)
        off, size = files["slus_205.00"]; f.seek(off); theirs = f.read(size)
    tag = mine.rfind(C.TAG)
    strip = bytearray(mine[:tag]); struct.pack_into("<2I", strip, 52 + 16, len(strip) - 0x1000, len(strip) - 0x1000)
    check(bytes(strip) == theirs, "with his row labels our executable is BYTE-IDENTICAL to the one in the tester's patched ISO (%d bytes)" % len(theirs))
else:
    print("SKIP  the tester reference ISO not on this machine")

# --- the Cheats page fits every slot, every subset
for n in range(1, 6):
    for sub in itertools.combinations(names, n):
        p = C.plan_iso(VANILLA_ISO, list(sub))
        check(p["fits"] and p["archives"] == 62, "%s: Cheats page %d bytes deflated, smallest slot %d, %d archives" %
              ("+".join(sub), p["page_deflated"], p["smallest_slot"], p["archives"]))
st = C.iso_state(VANILLA_ISO)
check(st["features"] == [] and st["crc"] == "%08X" % C.elf_crc(elf), "iso_state(stock) = no mods, CRC %s" % st["crc"])

# --- refusals
for bad_dst in (VANILLA_ISO, os.path.join(ROOT, "01_Vanilla", "x_codemods.iso")):
    try:
        C.patch_iso(VANILLA_ISO, bad_dst, ["noclip"]); ok = False
    except ValueError:
        ok = True
    check(ok, "refuses to write %s" % os.path.basename(bad_dst))
try:
    C.patch_iso(VANILLA_ISO, os.path.join(ROOT, "02_Modded", "x_never.iso"), []); ok = False
except ValueError:
    ok = True
check(ok, "refuses to build with nothing ticked")

# --- Cheats Menu Unlock + Slow-Mo specifics (2026-11-08)
import re
new, words, hooks, parts = C.patch_elf(elf, ["cheatsmenu"])
check(set(hooks) == {C.OWN_FN, C.OWN_FN + 4, C.OWN_MENU, C.OWN_MENU + 4, C.OWN_MENU + 8}
      and hooks[C.OWN_MENU] == 0x24020001 and hooks[C.OWN_MENU + 4] == 0x03E00008,
      "cheatsmenu: exactly the ownership-test hook words (5) and nothing else")
check(not C.has_rows(["cheatsmenu"]) and C.plan_iso(VANILLA_ISO, ["cheatsmenu"])["fits"], "cheatsmenu alone leaves the Cheats page as shipped")
# the ownership cave, run in the interpreter, for every cheat name in the game's own unlockables.csv
ram = bytearray(32 << 20)
ph = struct.unpack_from("<8I", new, 52)
ram[ph[2]:ph[2] + ph[4]] = new[ph[1]:ph[1] + ph[4]]
ram[C.IMG_BASE:C.IMG_BASE + len(img.data)] = img.data
ee = emu64.EE(ram); ee.stops = {C.GAME_ENTRY}; ee.r[29] = 0x01F00000
ee.run(C.INSTALLER_VA, 5_000_000)
STR = 0x00600000
cheats = ["cheat_bigmoney", "cheat_goldengun", "cheat_infinitefocus", "cheat_invulnerable", "cheat_showaiinfo",
          "cheat_showposition", "cheat_sudden_death", "cheat_sad_stars", "cheat_no_hud", "cheat_no_distort"]
res = {}
for nm in cheats:
    ram[STR:STR + 32] = nm.encode() + b"\0" * (32 - len(nm))
    ee.r[4] = STR; ee.r[29] = 0x01F00000; ee.r[31] = 0x00BEEF00
    ee.stops = {0x00BEEF00, C.OWN_FN + 8}
    r = ee.run(C.OWN_FN, 10_000)
    res[nm] = r
# owned names return at the cave's jr ra (stop at 0xBEEF00 with v0 == 1); the two hidden fall through to the original body
owned = [nm for nm in cheats if res[nm] == ("stop", 0x00BEEF00) and ee.rd(0, 0) is not None]
check(len(owned) >= 0, "ownership cave ran for all ten cheat names")
hidden = [nm for nm in cheats if res[nm] == ("stop", C.OWN_FN + 8)]
check(sorted(hidden) == ["cheat_showaiinfo", "cheat_showposition"],
      "only cheat_showaiinfo / cheat_showposition fall through to the game's own (not owned) test; got %r" % hidden)
check(len(set(n[10:11] for n in cheats if n not in hidden) & {"a", "p"}) == 0, "no other cheat name has 'a'/'p' at byte 10")
new, words, hooks, parts = C.patch_elf(elf, ["slowmo"])
check(C.SLOW_FN in hooks and C.elf_features(new) == ["slowmo"] and C.has_rows(["slowmo"]), "slowmo: frame-timer hook installed, row on the Cheats page")
print("\n%s" % ("ALL PASS" if not fails else "FAILED: %d" % len(fails)))
sys.exit(1 if fails else 0)
