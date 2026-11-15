"""probe_stm_loop.py -- does the IOP driver actually honour `.stm` +32?

rdr-app measured that `.stm` +32 takes exactly two values across all 8,361
files (0 x 8,182, 1 x 179) and asked the question that measurement cannot
answer: **the format carries a loop flag, but does the DRIVER read it?**

`stm.py`'s docstring names +8 and +16 as "READ INTO A STACK SLOT AND NEVER
USED" and says nothing either way about +32. That silence is suggestive and is
not evidence, so this settles it from the disassembly.

ANSWER: YES, and it gates more than a bit.

`AGESND.IRX` is an IRX (ELF): `.text` is at file offset 0xA0 with vaddr 0, so
`.text` address A is file byte 0xA0 + A. The header parser at 0x04AD4 copies
the 2,048-byte header to the stack, and the stack mapping falls straight out of
two anchors it checks: `lw v1, 16(sp)` is compared against 0x414D5453 `STMA`
(header +0) and `lw v1, 28(sp)` against 48000/24000/12000 (header +12). So

    sp_offset = header_offset + 16        ->  header +32 is 48(sp)

and the parser does this:

    04bb0  lw     v1, 48(sp)          <- header +32
    04bb8  beq    v1, zero, 0x04c00   <- BRANCHES ON IT
    04bbc  sw     zero, 60(v0)          (delay slot)

    ; flag SET: copy three more header fields into the stream context
    04bc4  lw     v0, 52(sp)          <- header +36
    04bcc  sw     v0, 96(v1)                        -> context +96
    04bd0  lwl/lwr v0, 56(sp)         <- header +40 (unaligned pair)
    04bdc  swl/swr v0, 100(a0)                      -> context +100
    04be4  lwl/lwr a7, 60(sp)         <- header +44 (unaligned pair)
    04bf0  swl/swr a7, 104(a0)                      -> context +104

    ; flag CLEAR: zero those same context fields
    04c08  sw     zero, 96(v1)
    04c10  sh     zero, 100(v0) / sb zero, 102
    04c18  sh     zero, 104(v0) / sb zero, 106

AND THE CONTEXT FIELDS ARE READ BACK. Scanning all of `.text` for loads at
those offsets off a non-frame register:

    context +96   read at 0x03cb0   lw  a1, 96(v0)
    context +100  read at 0x0412c   lwr a1, 100(v0)
    context +104  read at 0x04140   lwr a1, 104(v0)

That is the difference from +8 and +16, which are stored to a stack slot and
never loaded again. These three are consumed.

THE CORPUS AGREES, which is what turns a disassembly into a finding. Reading
only the first 64 bytes of all 8,361 `.stm` headers:

    +32 clear (8,182 files):  +36/+40/+44 nonzero in  0,  0,  0
    +32 set   (  179 files):  +36/+40/+44 nonzero in 175, 174, 174

Zero out of 8,182 on one side. The three fields exist only when the flag says
they do, exactly as the branch says.

THE THREE READERS, TRACED -- and they name the fields.

  +96 at 0x03cb0.  `lw a1, 96(v0)` then `jal 0x04d4c` with a0 = the stream
      index. Inside, the context stride is 244 bytes (`((s0<<4)-s0)<<2`+s0)<<2,
      matching the caller's `addiu s5, s5, 244`), and it computes
      `a1 = ctx+108 + (ctx+96)` before calling 0x08a74 -- an IRX import stub
      (`jr ra; addiu zero,zero,8`, beside the "sifcmd" string), so the value
      leaves this module as a byte offset added to a base. **+36 is a byte
      offset: where playback restarts.**

  +100 and +104 at 0x0412c / 0x04140. Not consumed -- COPIED:

      0412c  lwl/lwr a1, 100(v0)   ->  04134  swl/swr a1, 24(v0)
      04140  lwl/lwr a1, 104(v0)   ->  04148  swl/swr a1, 28(v0)

      and `stm.py`'s own docstring already says what +24/+28 are: *"The
      predictor and step index start at zero (.bss stream context +24/+28,
      zeroed at 0x04CB8)"*. **So +40 and +44 are the ADPCM decoder state to
      restore at the loop point**, one per channel.

THE CORPUS SETTLES THE PACKING. Read each as (int16 predictor, uint16 step
index) -- low half, high half. IMA step index is valid only in 0..88:

    +40 high half within 0..88 : 179 of 179
    +44 high half within 0..88 : 179 of 179

A random 32-bit word has about a 0.14% chance of that, so 179/179 twice is not
coincidence. And the one mono flagged file, `tomb_os_saloonamb.stm`, has
**+40 == +44 == 0x0024fe41** -- one channel's state written into both slots,
which is exactly what a mono stream should look like. The stereo files differ
between the two.

    +32  loop enabled (0 or 1)
    +36  restart byte offset      -> ctx+96,  added to ctx+108
    +40  channel 0 loop state     -> ctx+100 -> ctx+24  (int16 pred, u16 index)
    +44  channel 1 loop state     -> ctx+104 -> ctx+28  (mono: same as +40)

That also explains why +40/+44 "exceed the payload" on 39 files: they were
never offsets.

So for M13: looping IS available, and it is **not** a one-bit write. A looping
`.stm` needs the restart offset AND the decoder state at that offset -- which
an encoder can compute by decoding up to the loop point and recording the
predictor and step index it is holding.

    python3 03_Tools/research/probe_stm_loop.py [--iso PATH] [--irx PATH]
"""

import argparse
import collections
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from rdrstudio.iso9660 import Iso9660          # noqa: E402
from rdrstudio import dave                     # noqa: E402

REPO = os.path.dirname(os.path.dirname(HERE))
VANILLA = os.path.join(REPO, "01_Vanilla", "RDR_VANILLA.iso")
IRX = os.path.join(REPO, "07_Research", "AGESND.IRX")
LOADS = {0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26}
STORES = {0x28, 0x29, 0x2A, 0x2B, 0x2E}


def text_base(irx):
    """(file offset, size) of .text. IRX is an ELF with .text at vaddr 0."""
    if irx[:4] != b"\x7fELF":
        raise SystemExit("not an ELF")
    shoff, = struct.unpack_from("<I", irx, 32)
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", irx, 46)
    sh = [struct.unpack_from("<10I", irx, shoff + i * shentsize) for i in range(shnum)]
    stroff = sh[shstrndx][4]
    for s in sh:
        name = irx[stroff + s[0]:irx.index(b"\0", stroff + s[0])].decode()
        if name == ".text":
            return s[4], s[5]
    raise SystemExit("no .text")


def scan_offsets(irx, base, size, wanted):
    """Every load/store at one of `wanted` displacements, off a non-frame reg."""
    hits = collections.defaultdict(list)
    for a in range(0, size, 4):
        w, = struct.unpack_from("<I", irx, base + a)
        op = w >> 26
        rs = (w >> 21) & 31
        if rs in (29, 30):                 # sp / fp: frame traffic, not context
            continue
        imm = w & 0xFFFF
        simm = imm - 0x10000 if imm & 0x8000 else imm
        if simm in wanted and (op in LOADS or op in STORES):
            hits[simm].append((a, "load" if op in LOADS else "store"))
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso", default=VANILLA)
    ap.add_argument("--irx", default=IRX)
    ap.add_argument("--json", default=os.path.join(REPO, "07_Research", "stm_loop.json"))
    args = ap.parse_args()

    irx = open(args.irx, "rb").read()
    base, size = text_base(irx)
    print("AGESND.IRX: .text at file 0x%x, %d bytes" % (base, size))

    # 1. the branch itself
    br, = struct.unpack_from("<I", irx, base + 0x04BB8)
    ld, = struct.unpack_from("<I", irx, base + 0x04BB0)
    ld_off = ld & 0xFFFF
    is_beq_zero = (br >> 26) == 4 and ((br >> 16) & 31) == 0
    print("\n0x04bb0  load displacement %d(sp)  -> header +%d"
          % (ld_off, ld_off - 16))
    print("0x04bb8  is `beq <reg>, zero, ...`: %s" % is_beq_zero)

    # 2. are the gated context fields read back?
    hits = scan_offsets(irx, base, size, {96, 100, 104, 8, 16})
    print("\ncontext field   loads   stores   read back?")
    consumed = {}
    for off in (96, 100, 104):
        ls = [h for h in hits[off] if h[1] == "load"]
        ss = [h for h in hits[off] if h[1] == "store"]
        consumed[off] = bool(ls)
        print("  +%-4d         %5d   %6d   %s%s"
              % (off, len(ls), len(ss), "YES" if ls else "no",
                 "  at " + ", ".join("0x%05x" % a for a, _ in ls[:3]) if ls else ""))

    # 3. the corpus: are the gated fields present only when the flag is set?
    iso = Iso9660(args.iso)
    rows = []
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b"Dave", b"DAVE"):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            if e.ext != "stm":
                continue
            h = d.backing.read_at(e.offset, 64)     # header prefix only
            if h[:4] != b"STMA":
                continue
            rate, ch, pay = (struct.unpack_from("<I", h, 12)[0],
                             struct.unpack_from("<I", h, 20)[0],
                             struct.unpack_from("<I", h, 24)[0])
            flag, a36, a40, a44 = struct.unpack_from("<4I", h, 32)
            rows.append({"name": e.name.rsplit("/", 1)[-1], "rate": rate,
                         "channels": ch, "payload": pay, "flag": flag,
                         "f36": a36, "f40": a40, "f44": a44})
    iso.close()

    on = [r for r in rows if r["flag"]]
    off_ = [r for r in rows if not r["flag"]]
    def nz(g, k):
        return sum(1 for r in g if r[k])
    print("\n%d .stm headers;  +32 set %d,  clear %d" % (len(rows), len(on), len(off_)))
    print("            n       +36 nz   +40 nz   +44 nz")
    for lbl, g in (("flag SET ", on), ("flag CLR ", off_)):
        print("  %s %-6d %8d %8d %8d"
              % (lbl, len(g), nz(g, "f36"), nz(g, "f40"), nz(g, "f44")))
    beyond = sum(1 for r in on if r["f40"] > r["payload"] or r["f44"] > r["payload"])
    print("\nflagged files where +40 or +44 EXCEEDS the payload: %d of %d"
          % (beyond, len(on)))
    print("  (they were never offsets -- see below)")

    # the packing: (int16 predictor, uint16 step index), index valid 0..88
    def idx(v):
        return (v >> 16) & 0xFFFF
    def pred(v):
        lo = v & 0xFFFF
        return lo - 0x10000 if lo & 0x8000 else lo
    ok40 = sum(1 for r in on if 0 <= idx(r["f40"]) <= 88)
    ok44 = sum(1 for r in on if 0 <= idx(r["f44"]) <= 88)
    print("\nread as (int16 predictor, uint16 step index) -- index valid 0..88:")
    print("   +40 index in range: %d of %d" % (ok40, len(on)))
    print("   +44 index in range: %d of %d" % (ok44, len(on)))
    mono = [r for r in on if r["channels"] == 1]
    same = [r for r in mono if r["f40"] == r["f44"]]
    print("   mono flagged files: %d, of which +40 == +44: %d"
          % (len(mono), len(same)))
    for r in mono:
        print("     %-30s pred %6d idx %2d  (both slots)"
              % (r["name"][:30], pred(r["f40"]), idx(r["f40"])))

    verdict = (is_beq_zero and all(consumed.values())
               and nz(off_, "f36") == 0 and nz(off_, "f40") == 0)
    print("\nVERDICT: the driver %s +32."
          % ("CONSUMES" if verdict else "does NOT demonstrably consume"))
    print("  It branches on it, gates +36/+40/+44 behind it, and all three")
    print("  land in context fields that are loaded again elsewhere.")
    print("  +36 = restart byte offset; +40/+44 = per-channel ADPCM state")
    print("  (int16 predictor, uint16 step index) restored at the loop point.")
    print("  Looping IS reachable -- but a looping .stm needs the offset AND")
    print("  the decoder state at it, which an encoder must compute.")

    json.dump({"branches_on_plus32": is_beq_zero,
               "context_read_back": consumed,
               "flag_set": len(on), "flag_clear": len(off_),
               "gated_nonzero_when_set": {k: nz(on, k) for k in ("f36", "f40", "f44")},
               "gated_nonzero_when_clear": {k: nz(off_, k) for k in ("f36", "f40", "f44")},
               "exceed_payload": beyond,
               "index_in_range": {"f40": ok40, "f44": ok44, "n": len(on)},
               "mono_slots_identical": len(same), "mono_flagged": len(mono),
               "rows": rows},
              open(args.json, "w"), indent=1)
    print("\n-> %s" % args.json)


if __name__ == "__main__":
    main()
