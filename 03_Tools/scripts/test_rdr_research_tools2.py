#!/usr/bin/env python3
"""[CM-R2] The mapping helpers rdr_snap.py (snapshot/diff RAM over PINE or a RAM file) and rdr_ssdiff.py (diff two PCSX2
savestates), plus the Code Mods tab flags for the Research rows.  Offline, reads/writes only a temp dir.
    py -3 scripts/test_rdr_research_tools2.py"""
import io, json, os, struct, subprocess, sys, tempfile, zipfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, ".."))
fails = []
def check(ok, msg):
    print(("PASS " if ok else "FAIL ") + msg)
    if not ok: fails.append(msg)

tmp = tempfile.mkdtemp()
os.environ["RDR_SNAPS"] = os.path.join(tmp, "snaps")
def ramfile(words):
    r = bytearray(32 << 20)
    for a, v in words.items(): struct.pack_into("<I", r, a, v)
    p = os.path.join(tmp, "ram%d.bin" % len(os.listdir(tmp))); open(p, "wb").write(r); return p
F = lambda x: struct.unpack("<I", struct.pack("<f", x))[0]
A = ramfile({0x0079E240: 0x01B84200, 0x01B84254: F(7.5), 0x01B84218: 0x014BFF80, 0x01B84200: 0x509468})
B = ramfile({0x0079E240: 0x01B84200, 0x01B84254: F(9.25), 0x01B84218: 0x014BFF80, 0x01B84200: 0x509468, 0x01B842B8: 5})
def run(*a):
    r = subprocess.run([sys.executable, os.path.join(HERE, "rdr_snap.py")] + list(a), capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout + r.stderr
rc, o = run("snap", "a", "*0x0079E240:0x100", "--ram", A); check(rc == 0 and "0x01B84200" in o, "snap follows the pointer in *ADDR and saves " + o.strip())
rc, o = run("snap", "b", "*0x0079E240:0x100", "--ram", B); check(rc == 0, "second snapshot saved")
rc, o = run("diff", "a", "b")
check(rc == 0 and "+0x054" in o and "7.5000" in o and "9.2500" in o and "+0x0B8" in o, "diff lists exactly the changed words with float view: " + " | ".join(o.strip().splitlines()[:3]))
check(o.count("(0x") == 2, "diff: two words differ (position X and the new +0xB8)")
rc, o = run("show", "a"); check(rc == 0 and "7.5000" in o and "ptr" in o, "show prints int / hex / float / ptr")

def savestate(path, words):
    r = bytearray(32 << 20)
    for a, v in words.items(): struct.pack_into("<I", r, a, v)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z: z.writestr("eeMemory.bin", bytes(r)); z.writestr("PCSX2 Savestate Version.id", "x")
S1, S2 = os.path.join(tmp, "s1.p2s"), os.path.join(tmp, "s2.p2s")
savestate(S1, {0x01B303B4: 206, 0x01B303BC: 6, 0x01000000: 1}); savestate(S2, {0x01B303B4: 205, 0x01B303BC: 5, 0x01000000: 2})
def ss(*a):
    r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "rdr_ssdiff.py")] + list(a), capture_output=True, text=True, timeout=120); return r.returncode, r.stdout + r.stderr
rc, o = ss(S1, S2, "--count"); check(rc == 0 and o.strip().startswith("3 words differ"), "ssdiff --count = 3: " + o.strip())
rc, o = ss(S1, S2, "--pair", "206:205"); check(rc == 0 and o.strip() == "0x01B303B4: 206 -> 205", "ssdiff --pair finds the ammo-style counter: " + o.strip())
rc, o = ss(S1, S2, "--range", "0x01B30300:0x200"); check(rc == 0 and "0x01B303BC (+0xBC)" in o and "0x01000000" not in o, "ssdiff --range limits to the region")

from rdrstudio import codemods as C
d = C.describe(None, None, os.path.join(tmp, "none.iso")) if False else None
feats = {f[0]: f for f in C.FEATURES}
check(C.RESEARCH_KEYS == ["hitlog", "memwatch"] and all(k in feats for k in C.RESEARCH_KEYS), "hitlog + memwatch registered as research features")
check(set(C.CORE_KEYS) | set(C.RESEARCH_KEYS) == set(C.KEYS), "core + research = all keys")
check("hitlog" in C.PROVEN and "memwatch" in C.PROVEN and all("PCSX2" in C.PROVEN[k] for k in C.RESEARCH_KEYS), "both research rows carry an in-game proof note")
js = open(os.path.join(HERE, "..", "web", "codemods.js"), encoding="utf-8").read()
check("cb.checked = !f.research" in js and "Research tool" in js, "Code Mods tab: research rows are unticked by default and labelled")
print("\nALL PASS" if not fails else "\nFAILED: %d" % len(fails)); sys.exit(1 if fails else 0)
