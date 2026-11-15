#!/usr/bin/env python3
"""The TRIGGER side of action scripts: .stateb / .actb CONDITION grammar, checked against the executable.

    py -3 03_Tools/research/probe_stateb_cond.py [CORPUS_DIR]

Code facts it checks (no emulator):
  * stateb loader 0x2F7BA0 reads "version", f32, u32 nStates; state parser 0x2F6F40 dispatches 1002/1004/1005;
  * condition tree 0x307C90: node 0 = leaf (0x307990: operand, u32 cmp, operand), node 1 = binary (and/or);
  * compare jump table 0x528630: 0 <, 1 >, 2 ==, 3 !=, 4 <=, 5 >=;
  * binary op 1 = AND, 2 = OR (0x3081E8);
  * operand reader 0x2FA038: type 0 float, 2 named game value (table 0x4F9BA8), 3 target + property (table 0x4F9568);
  * name hash 0x182570 (one-at-a-time, case-folded) reproduces the stock sub-room constants for
    SHER70 'entry1' / 'porch1' (goto_entry1_sher70.actb / goto_porch1_sher70.actb).
Optional CORPUS_DIR (extracted .actb/.stateb): also counts how many leaves parse cleanly.
Encoder for building conditions: 07_Research/op_cond.py. In-game proof: 05_Docs/OPCODES-2026-11-13.md section 6.
"""
import os, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(REPO, "07_Research"))
from op_cond import rhash
img = open(os.path.join(REPO, "07_Research", "SLUS_205.00.unpacked.bin"), "rb").read()
w = lambda va: struct.unpack_from("<I", img, va - 0x100000)[0]
def cstr(va):
    o = va - 0x100000; return img[o:img.index(b"\0", o)].decode("latin1")
def table(start, n):
    return {cstr(w(start + 8 * i)): w(start + 8 * i + 4) for i in range(n)}
ok = True
def check(name, cond):
    global ok; ok &= bool(cond); print(("PASS " if cond else "FAIL ") + name)
cmp_tbl = [w(0x528630 + 4 * i) for i in range(6)]
check("compare table 0x528630 = < > == != <= >=", cmp_tbl == [0x307B80, 0x307B8C, 0x307BC8, 0x307BB0, 0x307B98, 0x307BA4])
check("case 0 is c.olt.s f20,f2 (L<R)", w(0x307B80) == 0x4602A034 and w(0x307B8C) == 0x46141034)
check("state parser dispatches 1004/1002/1005", w(0x2F6FDC) == 0x240203EC and w(0x2F6FEC) == 0x240203EA and w(0x2F7000) == 0x240203ED)
check("binary node: op 1 = AND, op 2 = OR", w(0x3081EC) == 0x24020001 and w(0x3081F4) == 0x24020002)
props = table(0x4F9568, 199); glob = table(0x4F9BA8, 58)
check("property table has subroomname/health/dist", all(k in props for k in ("subroomname", "health", "dist", "value")))
check("game-value table has time/acttime/hudtime/levelvar", all(k in glob for k in ("time", "acttime", "hudtime", "levelvar")))
check("hash: entry1 = 0x54E954B7, porch1 = 0x2A6E96B9", rhash("entry1") == 0x54E954B7 and rhash("porch1") == 0x2A6E96B9)
if len(sys.argv) > 1:
    import re
    n = bad = 0
    for root, _, fs in os.walk(sys.argv[1]):
        for f in fs:
            if not f.endswith((".actb", ".stateb")): continue
            b = open(os.path.join(root, f), "rb").read()
            for m in re.finditer(rb"(?<=\x00\x00\x00)([a-z_]{3,30})\x00", b):
                if m.group(1).decode() not in props and m.group(1).decode() not in glob: continue
                e = m.end()
                if e + 8 > len(b): continue
                c, t = struct.unpack_from("<II", b, e); n += 1
                bad += not (c <= 5 and t in (0, 2, 3))
    print("corpus: %d property/value operands, %d followed by a bad cmp/type" % (n, bad))
print("ALL PASS" if ok else "SOME FAIL"); sys.exit(0 if ok else 1)
