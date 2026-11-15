#!/usr/bin/env python3
"""[POS] rdr_pos.py (live player position over PINE), against a fake PINE server. Touches no emulator, no ISO.

    py -3 scripts/test_rdr_pos.py
"""
import os, socket, struct, sys, threading
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rdr_pos as R

fails = []
def check(ok, msg):
    print(("PASS " if ok else "FAIL ") + msg)
    if not ok: fails.append(msg)

PLAYER = 0x00ABC000
MEM = {R.PLAYER_PTR: PLAYER}
def setf(addr, v): MEM[addr] = struct.unpack("<I", struct.pack("<f", v))[0]
for i, v in enumerate((7.72, 1.9, 41.833)): setf(PLAYER + R.POS_OFF + 4 * i, v)
rows = [[0.217, 0, -0.976], [0, 1, 0], [0.976, 0, 0.217]]       # what the live game showed
for r in range(3):
    for c in range(3): setf(PLAYER + R.MATRIX_OFF + 12 * r + 4 * c, rows[r][c])
log = []

def serve(srv):
    c, _ = srv.accept()
    while True:
        h = c.recv(4)
        if len(h) < 4: return
        n = struct.unpack("<I", h)[0]
        body = b""
        while len(body) < n - 4: body += c.recv(n - 4 - len(body))
        log.append(body[0])                                      # opcode: only reads (2) must appear
        if body[0] == 2:
            addr = struct.unpack_from("<I", body, 1)[0]
            c.sendall(struct.pack("<IB", 9, 0) + struct.pack("<I", MEM.get(addr, 0)))
        else:
            c.sendall(struct.pack("<IB", 5, 0xFF))

srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
threading.Thread(target=serve, args=(srv,), daemon=True).start()
p = R.Pine("127.0.0.1", srv.getsockname()[1])
pos, m = R.player(p)
check([round(x, 3) for x in pos] == [7.72, 1.9, 41.833], "position read from creature+0x54: %r" % (pos,))
check(abs(R.heading(m) - 257.5) < 0.2, "heading from the live matrix (facing = -row 3) = %.1f deg (257.5 expected)" % R.heading(m))
check(set(log) == {2}, "only PINE read commands were sent (read-only tool)")
MEM[R.PLAYER_PTR] = 0
check(R.player(p) is None, "no player pointer -> 'no player yet', not a crash")
print("\n%s" % ("ALL PASS" if not fails else "FAILED: %d" % len(fails)))
sys.exit(1 if fails else 0)
