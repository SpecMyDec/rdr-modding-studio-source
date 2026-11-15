#!/usr/bin/env python3
r"""Live player position / heading readout for Red Dead Revolver in PCSX2 (modding aid).

    py -3 scripts/rdr_pos.py              # prints X Y Z + heading ~4x a second, Ctrl+C to stop
    py -3 scripts/rdr_pos.py --once       # one line (for scripts)
    py -3 scripts/rdr_pos.py --mark NAME  # Enter = print a ready-to-paste waypoint line (NAME_1, NAME_2 ...)

Why this exists: the game's own "Show Player Position" cheat is a stub (nothing in the executable draws it), and the
executable has no text-drawing routine we can call from a hook. So the readout is OUTSIDE the game: it reads PCSX2's
RAM over PINE (PCSX2 Settings > Advanced > Enable PINE, port 28011; the Studio's live proof turns it on temporarily).
Read-only: it never writes game memory.

Addresses (see codemods.py): player pointer 0x0079E240 -> creature; its world matrix is at creature+0x30 (3 rows of
3 floats) with the position at creature+0x54 (x, y, z; y is up).
"""
import math, socket, struct, sys, time

HOST, PORT = "127.0.0.1", 28011
PLAYER_PTR = 0x0079E240
MATRIX_OFF, POS_OFF = 0x30, 0x54


class Pine:
    def __init__(self, host=HOST, port=PORT):
        self.s = socket.create_connection((host, port), timeout=3)

    def _txn(self, payload):
        self.s.sendall(struct.pack("<I", len(payload) + 4) + payload)
        hdr = self._recv(4)
        n = struct.unpack("<I", hdr)[0]
        body = self._recv(n - 4)
        if not body or body[0] != 0:
            raise IOError("PINE request failed")
        return body[1:]

    def _recv(self, n):
        b = b""
        while len(b) < n:
            c = self.s.recv(n - len(b))
            if not c:
                raise IOError("PINE closed")
            b += c
        return b

    def read32(self, addr):
        return struct.unpack("<I", self._txn(struct.pack("<BI", 2, addr)))[0]

    def readf(self, addr):
        return struct.unpack("<f", struct.pack("<I", self.read32(addr)))[0]

    def write32(self, addr, v):
        self._txn(struct.pack("<BII", 6, addr, v & 0xFFFFFFFF))


def player(p):
    ptr = p.read32(PLAYER_PTR)
    if not (0x00100000 < ptr < 0x02000000):
        return None
    pos = [p.readf(ptr + POS_OFF + 4 * i) for i in range(3)]
    rows = [[p.readf(ptr + MATRIX_OFF + 12 * r + 4 * c) for c in range(3)] for r in range(3)]
    return pos, rows


def heading(rows):
    """Degrees, 0 = +Z, 90 = +X, 180 = -Z, 270 = -X.  The character faces along MINUS the matrix's third row
    (checked in PCSX2 2026-11-08: a 5 s walk moved (-5.7, -1.3) while rows[2] = (0.976, 0, 0.217))."""
    fx, fz = -rows[2][0], -rows[2][2]
    return (math.degrees(math.atan2(fx, fz)) + 360) % 360


def fmt(pos, rows):
    return "x=%9.3f  y=%9.3f  z=%9.3f   heading=%6.1f deg" % (pos[0], pos[1], pos[2], heading(rows))


def main(argv):
    p = Pine()
    if "--once" in argv:
        r = player(p)
        print(fmt(*r) if r else "no player yet (not in a level)")
        return 0
    if "--mark" in argv:
        name = argv[argv.index("--mark") + 1] if len(argv) > argv.index("--mark") + 1 else "spot"
        n = 0
        print("Enter = mark the current spot, Ctrl+C = quit")
        while True:
            input()
            r = player(p)
            if not r:
                print("no player yet")
                continue
            n += 1
            print("%s_%d: (%.3f, %.3f, %.3f) heading %.0f" % (name, n, r[0][0], r[0][1], r[0][2], heading(r[1])))
    try:
        while True:
            r = player(p)
            print("\r" + (fmt(*r) if r else "no player yet (not in a level)          "), end="", flush=True)
            time.sleep(0.25)
    except KeyboardInterrupt:
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
