#!/usr/bin/env python3
"""Action-file (.actb/.stateb) opcode LOADER table, read straight from the executable.

    py -3 03_Tools/research/probe_action_loader.py [--json OUT] [--md OUT]

What it proves (code facts, no emulator):
  * the action loader is 0x002D81C0: `opcode - 1001`, `sltiu 571`, jump table at 0x00524490;
  * 296 of the 571 slots have their own case, the rest share the default (ignored);
  * per opcode: the arguments it reads from the file (readInt / readStr / readFloat through the
    stream vtable +0x18 / +0x0C / +0x1C), the property KIND it stores them under
    (setters 0x2DE180 int, 0x2DE320 float, 0x2DE4C0 pointer/string, 0x2DDFE0 byte),
    or the flag bit it ORs into the action record (+0x8 / +0xC / +0x10);
  * the action EXECUTOR 0x002E4E40 reads those kinds back with get(params, kind) = 0x2DED80.
Exit 0 only if the spot checks below (each tied to an in-game proof or to code read by hand) hold.
In-game proofs: 05_Docs/OPCODES-2026-11-13.md. Static tracer: follows unconditional branches,
both sides of conditional ones (bounded), constant-tracks a1/a2; it is a reader, not an emulator,
so cases that call sub-parsers (0x2E9900 target spec, 0x2DC408 named action/state, ...) list the call.
"""
import json, os, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
ELF = os.path.join(REPO, "07_Research", "SLUS_205.00.unpacked.bin")
img = open(ELF, "rb").read()
def w(va): return struct.unpack_from('<I',img,va-0x100000)[0]
def s16(x): x&=0xFFFF; return x-0x10000 if x&0x8000 else x
SET={0x2de180:'int',0x2de320:'float',0x2de4c0:'ptr',0x2ddfe0:'byte'}
EXIT={0x2dc374,0x2dc378}
VT={0x18:'readInt',0x0c:'readStr',0x1c:'readFloat',0x10:'rd10',0x14:'rd14',0x20:'rd20',0x24:'rd24'}
def run(start,maxsteps=400):
    out=[]; seen=set(); global SW; SW=out
    stack=[(start,{0:0})]
    paths=0
    while stack and paths<12:
        va,regs=stack.pop(); regs=dict(regs); steps=0
        while steps<maxsteps:
            if va in EXIT: break
            if (va,tuple(sorted(regs.items()))) in seen: break
            seen.add((va,tuple(sorted(regs.items()))))
            steps+=1
            x=w(va); o=x>>26; rs=(x>>21)&31; rt=(x>>16)&31; rd=(x>>11)&31; imm=x&0xFFFF
            def ex(x,regs):
                o=x>>26; rs=(x>>21)&31; rt=(x>>16)&31; rd=(x>>11)&31; imm=x&0xFFFF
                if o==9 or o==25:
                    if rs in regs: regs[rt]=(regs[rs]+s16(imm))&0xFFFFFFFF
                    else: regs.pop(rt,None)
                elif o==13:
                    if isinstance(regs.get(rs),tuple) and regs[rs][0]=='A': regs[rt]=('A',regs[rs][1],regs[rs][2]|imm,regs[rs][3])
                    elif isinstance(regs.get(rs),int): regs[rt]=regs[rs]|imm
                    else: regs.pop(rt,None)
                elif o==15: regs[rt]=(imm<<16)
                elif o==12 and isinstance(regs.get(rs),tuple) and regs[rs][0]=='A': regs[rt]=('A',regs[rs][1],regs[rs][2],regs[rs][3]|((~imm)&0xFFFF))
                elif o==43 and rs==18: SW.append(('sw_act',hex(imm),regs.get(rt)))
                elif o==0 and (x&63) in (0x2d,0x21,0x25) and (rs==0 or rt==0):
                    if rt==0 and rs in regs: regs[rd]=regs[rs]
                    elif rs==0 and rt in regs: regs[rd]=regs[rt]
                    else: regs.pop(rd,None)
                elif o==35 or o==32 or o==36 or o==33 or o==37 or o==55:
                    if (rs==3) and rt==2 and imm in VT: regs[2]=('VT',imm)
                    elif rs==18 and o==35: regs[rt]=('A',imm,0,0)
                    else: regs.pop(rt,None)
                elif o==0 and (x&63) in (0x25,0x24) and (isinstance(regs.get(rs),tuple) and regs[rs][0]=='A' and isinstance(regs.get(rt),int) or isinstance(regs.get(rt),tuple) and regs[rt][0]=='A' and isinstance(regs.get(rs),int)):
                    a,c=(regs[rs],regs[rt]) if isinstance(regs.get(rs),tuple) else (regs[rt],regs[rs])
                    regs[rd]=('A',a[1],a[2]|c if (x&63)==0x25 else a[2], a[3]|((~c)&0xFFFFFFFF) if (x&63)==0x24 else a[3])
                elif o==0 and rd: regs.pop(rd,None)
                regs[0]=0
            if o==3 or (o==0 and (x&63)==9):  # jal / jalr
                tgt=(x&0x3FFFFFF)<<2 if o==3 else regs.get(rs)
                ex(w(va+4),regs)
                if isinstance(tgt,tuple): out.append((VT.get(tgt[1]),))
                elif tgt in SET: out.append(('set_'+SET[tgt],regs.get(5),regs.get(6)))
                else: out.append(('call',hex(tgt) if isinstance(tgt,int) else '?'))
                for r in (2,3,4,5,6,7,8,9,10,11,12,13,14,15,24,25): regs.pop(r,None)
                va+=8; continue
            if o==4 and rs==0 and rt==0 or o==2:  # b / j
                ex(w(va+4),regs)
                va= va+4+(s16(imm)<<2) if o==4 else ((x&0x3FFFFFF)<<2); continue
            if o in (4,5,6,7,1,20,21,22,23) or (o==17 and rs==8):
                tgt=va+4+(s16(imm)<<2)
                likely=o in (20,21,22,23) or (o==17 and rs==8 and (rt&2))
                r2=dict(regs); ex(w(va+4),r2); stack.append((tgt,r2)); paths+=1
                if likely: va+=8
                else: ex(w(va+4),regs); va+=8
                out.append(('br',))
                continue
            if o==0 and (x&63)==8: break  # jr
            ex(x,regs); va+=4
    return out

LOADER, TBL, NCASE, DEFAULT = 0x2D81C0, 0x524490, 0x23B, 0x2DC374


def table():
    res = {}
    for i in range(NCASE):
        a = w(TBL + 4 * i)
        if a == DEFAULT:
            continue
        res[1001 + i] = dict(addr=a, ev=[e for e in run(a) if e[0] != "br"])
    return res


def fmt(e):
    if e[0].startswith("set_"):
        return "%s k%s%s" % (e[0][4:], e[1], "" if e[2] is None else "=%s" % e[2])
    if e[0] == "sw_act":
        v = e[2]
        if isinstance(v, tuple) and v[0] == "A":
            return "act+%s%s%s" % (e[1], (" |=%#x" % v[2]) if v[2] else "", (" &=~%#x" % v[3]) if v[3] else "")
        return "act+%s=%s" % (e[1], hex(v) if isinstance(v, int) else v)
    if e[0] == "call":
        return "call " + e[1]
    return str(e[0])


def main(argv):
    bad = []

    def check(ok, what):
        print(("  ok   " if ok else "  FAIL ") + what)
        if not ok:
            bad.append(what)
    x = w(LOADER + 0x14)
    check(x == 0x2623FC17, "loader 0x2D81C0 subtracts 1001 (addiu v1, s1, -0x3E9)")
    check(w(LOADER + 0x3C) == 0x2C62023B, "loader bounds at 571 (sltiu v0, v1, 0x23B)")
    T = table()
    check(len(T) == 296, "296 opcodes have their own case (got %d)" % len(T))
    s = {op: " ".join(fmt(e) for e in d["ev"]) for op, d in T.items()}
    spot = {1326: "int k1=35", 1032: "readFloat readFloat readFloat", 1555: "readInt int k166",
            1544: "readStr", 1503: "readFloat float k120", 1508: "readInt int k126 readFloat float k127",
            1260: "act+0x8 |=0x1000 readInt int k33", 1004: "call 0x2dc408", 1005: "call 0x2dc408"}
    for op, want in spot.items():
        check(want in s.get(op, ""), "%d: %s   [%s]" % (op, want, s.get(op, "")[:80]))
    check("ptr k104" in s[1544] and "int k105" in s[1544], "1544: zone name -> k104, int -> k105")
    check("ptr k20" in s[1032], "1032: three floats -> k20 (target position)")
    out = {"--json": None, "--md": None}
    for k in out:
        if k in argv:
            out[k] = argv[argv.index(k) + 1]
    if out["--json"]:
        json.dump({str(k): dict(addr=hex(v["addr"]), does=s[k]) for k, v in sorted(T.items())}, open(out["--json"], "w"), indent=1)
    if out["--md"]:
        with open(out["--md"], "w") as f:
            f.write("| opcode | case | reads / stores |\n|---|---|---|\n")
            for k in sorted(T):
                f.write("| %d | %s | %s |\n" % (k, hex(T[k]["addr"]), s[k].replace("|", "\\|")))
    print("RESULT", "FAIL" if bad else "PASS")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
