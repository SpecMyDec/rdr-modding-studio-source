#!/usr/bin/env python3
r"""Savestate differ (read-only): which 32-bit words of EE RAM changed between two PCSX2 savestates?

    python3 -I rdr_ssdiff.py A.p2s B.p2s --pair 206:205          # words that went 206 -> 205 (find an ammo counter)
    python3 -I rdr_ssdiff.py A.p2s B.p2s --range 0x01B84200:0x1000 # everything that changed in a region (an actor)
    python3 -I rdr_ssdiff.py A.p2s B.p2s --count                  # just how many words changed
Savestates need SavestateCompressionType = 0 (zip 'stored').  Use: save state, do ONE thing in game, save again, diff."""
import struct, sys, zipfile


def ram(p):
    return zipfile.ZipFile(p).read("eeMemory.bin")


def f32(u):
    return struct.unpack("<f", struct.pack("<I", u))[0]


def fmt(u):
    f = f32(u)
    return "%d/0x%08X%s" % (u if u < 1 << 31 else u - (1 << 32), u, "/%.4f" % f if u and 1e-3 < abs(f) < 1e7 else "")


def main(a):
    A, B = ram(a[0]), ram(a[1])
    n = len(A) // 4
    wa, wb = struct.unpack("<%dI" % n, A), struct.unpack("<%dI" % n, B)
    if "--count" in a:
        print(sum(1 for i in range(n) if wa[i] != wb[i]), "words differ")
        return
    if "--pair" in a:
        x, y = [int(v, 0) for v in a[a.index("--pair") + 1].split(":")]
        for i in range(n):
            if wa[i] == x and wb[i] == y:
                print("0x%08X: %d -> %d" % (i * 4, x, y))
        return
    if "--range" in a:
        s, l = a[a.index("--range") + 1].split(":")
        s, l = int(s, 0), int(l, 0)
        for i in range(s // 4, (s + l) // 4):
            if wa[i] != wb[i]:
                print("0x%08X (+0x%X): %s -> %s" % (i * 4, i * 4 - s, fmt(wa[i]), fmt(wb[i])))
        return
    print(__doc__)


main(sys.argv[1:])
