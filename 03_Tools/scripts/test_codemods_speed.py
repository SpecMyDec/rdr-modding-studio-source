#!/usr/bin/env python3
"""[CMS] Fast Red code mod, offline. Reads 01_Vanilla, writes nothing.

    py -3 scripts/test_codemods_speed.py

The patch is the one measured live in PCSX2 (05_Docs/DEEPDIVE-2026-11-11.md): the three calls of
Mover::Update go through one cave. Run in the R5900 interpreter with a stand-in Mover::Update that
records the frame time it sees: the player's mover sees frame time x factor, anyone else's sees it
unchanged, the frame time is back to normal afterwards and the call returns to its caller.
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
fb = lambda v: struct.unpack("<I", struct.pack("<f", v))[0]
bf = lambda w: struct.unpack("<f", struct.pack("<I", w & 0xFFFFFFFF))[0]
check(C.KEYS.index("fastred") == 10 and C.KEYS[:11] == C.KEYS[:C.KEYS.index("fastred") + 1], "fastred keeps bit 10 (later mods are only ever appended after it)")
check("fastred" not in C.TOGGLE and "fastred" not in C.ROW_LABEL, "always-on: no button combo, no Cheats row")
new, words, hooks, parts = C.patch_elf(elf, ["fastred"])
jal_cave = 0x0C000000 | (C.CAVE_SPEED >> 2)
check(hooks == {x: jal_cave for x in C.SPEED_SITES} and C.elf_features(new) == ["fastred"], "hooks are exactly the three `jal Mover::Update` sites, now `jal` the cave")
check(all(C.CAVE_SPEED <= a < C.CAVE_SPEED + 0x100 for a in words), "cave + save slots stay in 0x%08X..+0x100 (%d words)" % (C.CAVE_SPEED, len(words)))
check(all((ok for ok, _ in C.verify_addresses(img))), "address check passes on the vanilla executable")
DT, SEEN, RET = 1.0 / 30, 0x01000300, 0x00200000
for who in ("player", "enemy", "no player yet"):
    ee = emu64.EE(bytearray(0x02000000))
    for a_, w in words.items(): ee.wr(a_, 4, w)
    PL, MOV_P, MOV_E = 0x01000000, 0x01000100, 0x01000200
    ee.wr(C.PLAYER_PTR, 4, 0 if who == "no player yet" else PL)
    ee.wr(MOV_P + 0x34, 4, PL + 0x30); ee.wr(MOV_E + 0x34, 4, 0x01000430)
    ee.wr(C.FRAME_TIME, 4, fb(DT))
    # stand-in Mover::Update: SEEN = frame time; jr ra
    hi, lo = C.hi_lo(C.FRAME_TIME); shi, slo = C.hi_lo(SEEN)
    stub = C.Asm(C.MOVER_UPDATE)
    stub.la("t0", C.FRAME_TIME); stub.lw("t1", 0, "t0"); stub.la("t0", SEEN); stub.sw("t1", 0, "t0"); stub.jr("ra"); stub.nop()
    for a_, w, _ in stub.assemble(): ee.wr(a_, 4, w)
    try:
        ee.r[:] = [0] * 32; ee.r[4] = MOV_E if who == "enemy" else MOV_P; ee.r[31] = RET
        ee.stops = {RET}
        res = ee.run(C.CAVE_SPEED, 400)
        seen, after = bf(ee.rd(SEEN, 4)), bf(ee.rd(C.FRAME_TIME, 4))
    except Exception as ex:
        check(False, "%s: interpreter could not run the cave (%s: %s)" % (who, type(ex).__name__, ex)); continue
    want = DT * C.SPEED_FACTOR if who == "player" else DT
    check(res[0] == "stop" and abs(seen - want) < 1e-7, "%s: the mover saw frame time %.5f (want %.5f) and returned to its caller" % (who, seen, want))
    check(abs(after - DT) < 1e-7, "%s: frame time is %.5f again afterwards" % (who, after))
new, words, hooks, parts = C.patch_elf(elf, C.CORE_KEYS)
check(C.elf_features(new) == C.CORE_KEYS, "all core mods together (noclip hooks Mover::Update's entry, fastred its callers) build with no conflict")
print("\n%s" % ("ALL PASS" if not fails else "%d FAIL" % len(fails)))
sys.exit(1 if fails else 0)
