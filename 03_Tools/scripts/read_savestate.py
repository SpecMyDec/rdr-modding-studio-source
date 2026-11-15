"""Read a PCSX2 save state and pull out what the game computed at runtime.

    python scripts/read_savestate.py [path-to.p2s]

Why this exists: the `cm2` animation compression is unread, and the honest
blocker is that we have the *input* (the file) but no *output* to check a
decoder against. Watching gameplay does not give that — pixels are not bone
rotations, and blending, IK and the camera all sit in between.

A save state does. It is a zip holding the whole machine:

    eeMemory.bin    32 MB   PS2 main RAM
    vu1Memory.bin   16 KB   VU1 data memory
    GS.bin                  graphics state
    Screenshot.png          what was on screen

**VU1 data memory is the prize.** From the skinned-mesh work we already know
the renderer uploads bone matrices to VU1 starting at quadword 210 with a
stride of 4 — that is how `vif.py` decodes a bone slot as
`(slot - 210) / 4`. So VU1 at quadword 210 upwards holds the *decompressed,
posed* bone matrices for whatever was being drawn: exactly the output of the
cm2 decoder, for a known frame of a known animation.

PCSX2 writes save states as a zstd zip by default, which the Python standard
library cannot read. Set `SavestateCompressionType = 0` in
`inis/PCSX2.ini` and they become stored, readable with no dependency. This
script says so plainly if it meets a compressed one.
"""
import struct
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rdrstudio.config import sstates   # noqa: E402


BONE_BASE = 210          # VU1 quadword of bone matrix 0
BONE_STRIDE = 4          # one 4x4 matrix per bone
# Resolved from the emulator the user actually chose (RDR_PCSX2_EXE, or
# the app's own settings), not from one machine's install location.
DEFAULT_DIR = Path(sstates())


def member(z, name):
    try:
        return z.read(name)
    except NotImplementedError:
        raise SystemExit(
            "\nThis save state is compressed with zstd, which Python's zipfile\n"
            "cannot read. Fix it once and every future state is readable:\n\n"
            "  1. close PCSX2\n"
            "  2. edit <PCSX2 folder>\\inis\\PCSX2.ini\n"
            "  3. set  SavestateCompressionType = 0\n"
            "  4. reopen PCSX2 and press F1 again\n\n"
            "(Uncompressed states are ~50 MB instead of ~14 MB. That is the\n"
            "only difference.)\n")


def looks_like_matrix(rows):
    """Is this 4x4 block a plausible bone transform?

    A rotation's three basis vectors are unit length, so their norms should
    sit near 1. That is a real test rather than a vibe: random float garbage
    almost never produces three near-unit rows in a row.
    """
    norms = []
    for r in rows[:3]:
        n = (r[0] ** 2 + r[1] ** 2 + r[2] ** 2) ** 0.5
        norms.append(n)
    if any(n == 0 or n != n for n in norms):        # zero or NaN
        return False, norms
    ok = all(0.90 <= n <= 1.10 for n in norms)
    return ok, norms


def main():
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
    else:
        states = sorted(DEFAULT_DIR.glob("*.p2s"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
        if not states:
            raise SystemExit("No .p2s found in %s — press F1 in PCSX2 first."
                             % DEFAULT_DIR)
        path = states[0]

    print("save state : %s" % path.name)
    print("             %.1f MB, modified %s"
          % (path.stat().st_size / 1e6,
             __import__("time").strftime(
                 "%Y-%m-%d %H:%M", __import__("time").localtime(
                     path.stat().st_mtime))))

    z = zipfile.ZipFile(path)
    have = {i.filename for i in z.infolist()}
    print("contents   : %d members" % len(have))

    shot = DEFAULT_DIR.parent / "rdr_savestate_screen.png"
    if "Screenshot.png" in have:
        try:
            shot.write_bytes(z.read("Screenshot.png"))
            print("screenshot : %s" % shot)
        except Exception:
            pass

    vu = member(z, "vu1Memory.bin")
    print("VU1 memory : %d bytes = %d quadwords" % (len(vu), len(vu) // 16))

    # Walk the bone matrix region and report how many look like real
    # transforms. On a menu screen there will be none, and that is a correct
    # answer rather than a failure.
    print("\nbone matrices at VU1 quadword %d, stride %d:" % (BONE_BASE, BONE_STRIDE))
    good = 0
    shown = 0
    for b in range(64):
        off = (BONE_BASE + b * BONE_STRIDE) * 16
        if off + 64 > len(vu):
            break
        rows = [struct.unpack_from("<4f", vu, off + r * 16) for r in range(4)]
        ok, norms = looks_like_matrix(rows)
        if ok:
            good += 1
        if shown < 5 and (ok or b < 2):
            shown += 1
            print("  bone %-3d %s  row norms %s"
                  % (b, "PLAUSIBLE" if ok else "not a transform",
                     " ".join("%.3f" % n for n in norms)))
            for r in rows:
                print("      %s" % " ".join("%9.4f" % x for x in r))
    print("\n  %d of the first 64 slots hold a plausible bone transform" % good)

    if good < 4:
        print("\n  => Nothing was being skinned when this state was taken. That")
        print("     is expected at a menu or a loading screen. To get animation")
        print("     data, take the state while a CHARACTER IS ON SCREEN AND")
        print("     MOVING — mid-stride while running is ideal.")
    else:
        print("\n  => These are live, posed bone matrices: the decompressed")
        print("     output of the animation system. Pair this with the .anim")
        print("     that was playing and it becomes ground truth for a cm2")
        print("     decoder.")

    # A quick look for the animation name in main RAM, which would tell us
    # WHICH animation produced those matrices.
    if "eeMemory.bin" in have:
        ee = member(z, "eeMemory.bin")
        print("\nEE memory  : %.1f MB" % (len(ee) / 1e6))
        needles = [b"cm2\x00", b".anim", b"_lods", b".skel"]
        for n in needles:
            c = ee.count(n)
            print("  %-8s appears %d time(s) in RAM"
                  % (n.decode("latin-1").strip("\x00"), c))
        # Any cm2 header sitting in RAM is a loaded animation; its frame and
        # channel counts identify it.
        pos, found = 0, 0
        print("\n  cm2 headers resident in RAM (frames / channels):")
        while found < 8:
            pos = ee.find(b"cm2\x00", pos + 1)
            if pos < 0:
                break
            fr, ch = struct.unpack_from("<II", ee, pos + 4)
            if 0 < fr < 20000 and 0 < ch < 4000 and ch % 3 == 0:
                found += 1
                print("    @0x%08X  frames=%-5d channels=%-4d (%d bones)"
                      % (pos, fr, ch, ch // 3))
        if not found:
            print("    none resident")
    return 0


if __name__ == "__main__":
    sys.exit(main())
