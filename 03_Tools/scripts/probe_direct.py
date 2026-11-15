"""Do level display lists carry GS TEX0_1 register writes?

The binding we ship is a heuristic: a model uses the nearest preceding TEX0
chunk. That is guesswork, and it is what puts an interior atlas on a building
wall. The real answer, if it is anywhere, is in the VIF DIRECT packets that
`vif.parse` currently skips: DIRECT hands raw GIF data to the GS, and a
material change is a GIF tag in A+D mode writing register 0x06 (TEX0_1).

TEX0_1 layout (64 bits):
    0..13   TBP0   texture base pointer, in 64-word blocks
    14..19  TBW    buffer width / 64
    20..25  PSM    pixel storage mode
    26..29  TW     log2 width
    30..33  TH     log2 height
    34      TCC
    35..36  TFX
    37..50  CBP    CLUT base pointer
    51..54  CPSM
    55      CSM
    56..60  CSA
    61..63  CLD
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rdrstudio import Game, rsc  # noqa: E402
from rdrstudio.config import VANILLA_ISO  # noqa: E402

GIF_REGS = {
    0x00: "PRIM", 0x01: "RGBAQ", 0x02: "ST", 0x03: "UV",
    0x04: "XYZF2", 0x05: "XYZ2", 0x06: "TEX0_1", 0x07: "TEX0_2",
    0x08: "CLAMP_1", 0x09: "CLAMP_2", 0x0A: "FOG",
    0x0C: "XYZF3", 0x0D: "XYZ3",
    0x14: "TEX1_1", 0x15: "TEX1_2", 0x16: "TEX2_1", 0x17: "TEX2_2",
    0x18: "XYOFFSET_1", 0x19: "XYOFFSET_2", 0x1A: "PRMODECONT",
    0x1B: "PRMODE", 0x1C: "TEXCLUT", 0x22: "SCANMSK",
    0x34: "MIPTBP1_1", 0x36: "MIPTBP2_1", 0x3B: "TEXA",
    0x3D: "FOGCOL", 0x3F: "TEXFLUSH", 0x40: "SCISSOR_1",
    0x42: "ALPHA_1", 0x43: "ALPHA_2", 0x44: "DIMX", 0x45: "DTHE",
    0x46: "COLCLAMP", 0x47: "TEST_1", 0x48: "TEST_2",
    0x49: "PABE", 0x4A: "FBA_1", 0x4C: "FRAME_1", 0x4E: "ZBUF_1",
    0x50: "BITBLTBUF", 0x51: "TRXPOS", 0x52: "TRXREG", 0x53: "TRXDIR",
    0x54: "HWREG", 0x60: "SIGNAL", 0x61: "FINISH", 0x62: "LABEL",
}

PSM = {0: "CT32", 1: "CT24", 2: "CT16", 10: "CT16S", 19: "T8",
       20: "T4", 27: "T8H", 36: "T4HL", 44: "T4HH", 48: "Z32",
       49: "Z24", 50: "Z16", 58: "Z16S"}


def decode_tex0(v):
    return {
        "TBP0": v & 0x3FFF,
        "TBW": (v >> 14) & 0x3F,
        "PSM": PSM.get((v >> 20) & 0x3F, (v >> 20) & 0x3F),
        "W": 1 << ((v >> 26) & 0xF),
        "H": 1 << ((v >> 30) & 0xF),
        "TFX": (v >> 35) & 3,
        "CBP": (v >> 37) & 0x3FFF,
        "CPSM": PSM.get((v >> 51) & 0xF, (v >> 51) & 0xF),
        "CSM": (v >> 55) & 1,
        "CSA": (v >> 56) & 0x1F,
        "CLD": (v >> 61) & 7,
    }


def walk_direct(data):
    """Yield (offset, payload) for every VIF DIRECT packet in a stream."""
    i, end = 0, len(data)
    while i + 4 <= end:
        imm, num, cmd = struct.unpack_from("<HBB", data, i)
        cmd &= 0x7F
        i += 4
        if cmd in (0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
                   0x10, 0x11, 0x13, 0x14, 0x15, 0x17, 0x20):
            continue
        if cmd == 0x30:
            i += 16
            continue
        if cmd == 0x31:
            i += 16
            continue
        if cmd == 0x4A:
            i += (num or 256) * 8
            continue
        if cmd in (0x50, 0x51):
            n = (imm or 65536) * 16
            if i + n > end:
                return
            yield i, data[i:i + n]
            i += n
            continue
        if 0x60 <= cmd <= 0x7F:
            vl, vn = cmd & 3, (cmd >> 2) & 3
            count = num or 256
            if vl == 3:
                size = 2 * count
            else:
                size = ((4, 3, 2, 1)[vn ^ 3] if False else
                        (1, 2, 3, 4)[vn]) * ((32, 16, 8, 16)[vl] // 8) * count
            size = (size + 3) & ~3
            i += size
            continue
        return


def gif_regs(payload):
    """Yield (reg, value) from A+D GIF packets inside a DIRECT payload."""
    i, end = 0, len(payload)
    while i + 16 <= end:
        lo, hi = struct.unpack_from("<QQ", payload, i)
        i += 16
        nloop = lo & 0x7FFF
        eop = (lo >> 15) & 1
        pre = (lo >> 46) & 1
        flg = (lo >> 58) & 3
        nreg = (lo >> 60) & 0xF
        nreg = nreg or 16
        regs = hi
        if flg == 0:            # PACKED
            for _ in range(nloop):
                for r in range(nreg):
                    if i + 16 > end:
                        return
                    d0, d1 = struct.unpack_from("<QQ", payload, i)
                    i += 16
                    desc = (regs >> (r * 4)) & 0xF
                    if desc == 0x0E:      # A+D
                        yield d1 & 0xFF, d0
        elif flg == 1:          # REGLIST
            i += ((nloop * nreg * 8) + 15) & ~15
        elif flg == 2 or flg == 3:   # IMAGE
            i += nloop * 16
        if eop:
            pass
    return


def main():
    game = Game(VANILLA_ISO)
    levels = [("LVLS_SNG/PERFNEW.DAT", "lvl_perfnew.rsc"),
              ("LVLS_SNG/TOMB.DAT", "lvl_tomb.rsc"),
              ("LVLS_SNG/SHER70.DAT", "lvl_sher70.rsc")]
    for arc, area in levels:
        try:
            blob = game.archive(arc).read(area)
        except Exception as e:
            print("%-24s ERR %s" % (arc, e))
            continue
        b = rsc.Rsc(blob)
        mods = [c for c in b.chunks if c.tag == "MOD0"]
        n_direct = n_tex0 = 0
        seen = {}
        regcount = {}
        for c in mods:
            raw = b.raw(c.index)
            for off, payload in walk_direct(raw):
                n_direct += 1
                for reg, val in gif_regs(payload):
                    regcount[GIF_REGS.get(reg, hex(reg))] = \
                        regcount.get(GIF_REGS.get(reg, hex(reg)), 0) + 1
                    if reg in (0x06, 0x07):
                        n_tex0 += 1
                        t = decode_tex0(val)
                        seen.setdefault(c.index, []).append(t)
        print("\n=== %s / %s ===" % (arc, area))
        print("MOD0 chunks: %d   DIRECT packets: %d   TEX0 writes: %d"
              % (len(mods), n_direct, n_tex0))
        if regcount:
            top = sorted(regcount.items(), key=lambda kv: -kv[1])[:12]
            print("regs:", ", ".join("%s=%d" % kv for kv in top))
        for idx, ts in list(seen.items())[:6]:
            uniq = {(t["TBP0"], t["PSM"], t["W"], t["H"], t["CBP"])
                    for t in ts}
            print("  MOD0[%d]: %d writes, %d unique -> %s"
                  % (idx, len(ts), len(uniq), sorted(uniq)[:4]))


if __name__ == "__main__":
    main()
