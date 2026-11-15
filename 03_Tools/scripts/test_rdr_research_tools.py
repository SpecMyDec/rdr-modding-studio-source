#!/usr/bin/env python3
"""[RT] rdr_hitlog.py / rdr_watch.py readers against a fake PINE server holding a RAM image of the Research mods' blocks. No emulator, no ISO.
    py -3 scripts/test_rdr_research_tools.py"""
import os, socket, struct, sys, threading
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rdr_research as RR

fails = []
def check(ok, msg):
    print(("PASS " if ok else "FAIL ") + msg)
    if not ok: fails.append(msg)

ram = bytearray(32 << 20)
def w32(a, v): struct.pack_into("<I", ram, a, v & 0xFFFFFFFF)
w32(RR.HL_DATA, struct.unpack("<I", b"HLOG")[0]); w32(RR.HL_DATA + 4, 2); w32(RR.HL_DATA + 8, 5)
for i, (fn, cnt, ret) in enumerate([(0x17E778, 4, 1), (0x3D4BC0, 0, 0)]):
    t = RR.HL_DATA + RR.HL_TABLE + 32 * i
    for k, v in enumerate((fn, cnt, 7, 6, 0, 0, 0x00123456, ret)): w32(t + 4 * k, v)
for s in range(1, 6):
    r = RR.HL_DATA + RR.HL_RINGOFF + 16 * (s & 127)
    for k, v in enumerate((s, 0x17E778, 7, s)): w32(r + 4 * k, v)
w32(RR.MW_DATA, struct.unpack("<I", b"MWCH")[0]); w32(RR.MW_DATA + 4, 3); w32(RR.MW_DATA + 8, 99)
for i, (a, t, nd, o1) in enumerate([(0x79E240, 2, 1, 0x54), (0x79E240, 0, 0, 0), (0x79EEE4, 0, 1, 8)]):
    struct.pack_into("<IBBHhhI", ram, RR.MW_DATA + RR.MW_TABLE + 16 * i, a, t, nd, 0, o1, 0, 0)
res = [(0x300054, struct.unpack("<I", struct.pack("<f", 7.5))[0], 1), (0x79E240, 0x300000, 1), (0, 0, 2)]
for i, (fin, v, st) in enumerate(res):
    for k, x in enumerate((fin, v, st, 99)): w32(RR.MW_DATA + RR.MW_RESULTS + 16 * i + 4 * k, x)
log = []
def serve(srv):
    c, _ = srv.accept()
    while True:
        h = c.recv(4)
        if len(h) < 4: return
        n = struct.unpack("<I", h)[0]; body = b""
        while len(body) < n - 4: body += c.recv(n - 4 - len(body))
        log.append(body[0])
        if body[0] == 2:
            a = struct.unpack_from("<I", body, 1)[0]
            c.sendall(struct.pack("<IB", 9, 0) + bytes(ram[a:a + 4]))
        else:
            c.sendall(struct.pack("<IB", 5, 0xFF))
srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
threading.Thread(target=serve, args=(srv,), daemon=True).start()
p = RR.Pine("127.0.0.1", srv.getsockname()[1])
cfg = {"hitlog": [{"addr": "0x17E778", "name": "IsCheatOn"}, {"addr": "0x3D4BC0", "name": "csv"}],
       "watch": [{"label": "player X"}, {"label": "player ptr"}, {"label": "camera field"}]}
d = RR.read_hitlog(p, cfg)
check(d["total"] == 5 and d["funcs"][0]["count"] == 4 and d["funcs"][0]["ret"] == 1 and d["funcs"][0]["caller"] == 0x123456, "hitlog table read over PINE: counts, caller, return value")
check([r["seq"] for r in d["ring"]] == [1, 2, 3, 4, 5], "ring buffer decoded oldest-first")
txt = RR.hitlog_text(d)
check("IsCheatOn" in txt and "(never called)" in txt, "readable log names the functions and flags the never-called one")
wr = RR.read_watch(p, cfg)
check(wr[0] == 99 and wr[1][0]["text"] == "7.5000" and wr[1][1]["text"].startswith("3145728") and wr[1][2]["text"] == "null ptr", "watch rows: float chain, plain u32, null pointer status")
check(set(log) == {2}, "only PINE read commands were sent (read-only)")
ram[RR.MW_DATA:RR.MW_DATA + 4] = b"\0\0\0\0"
check(RR.read_watch(p, cfg) is None, "mod not present -> None, not a crash")
print("\n%s" % ("ALL PASS" if not fails else "FAILED: %d" % len(fails)))
sys.exit(1 if fails else 0)
