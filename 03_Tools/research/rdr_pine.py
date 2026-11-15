r"""PINE client for PCSX2 (deep-dive research tool; reads AND writes emulator RAM).
  py -3 rdr_pine.py r32 ADDR [N]      N words hex        rf ADDR [N]  floats
  py -3 rdr_pine.py w32 ADDR VAL...   write words        w8 ADDR VAL  wf ADDR FLOAT
  py -3 rdr_pine.py dump ADDR LEN FILE   raw bytes to FILE
  py -3 rdr_pine.py trace SECS HZ FILE   player x y z samples (ptr 0x79E240, +0x54)
"""
import socket, struct, sys, time
def conn():
    s = socket.create_connection(("127.0.0.1", 28011), timeout=5); return s
def xfer(s, body):
    s.sendall(struct.pack("<I", len(body) + 4) + body)
    h = b""
    while len(h) < 4: h += s.recv(4 - len(h))
    n = struct.unpack("<I", h)[0] - 4; d = b""
    while len(d) < n: d += s.recv(n - len(d))
    if d[0] != 0: raise RuntimeError("PINE fail")
    return d[1:]
def r32(s, a, n=1):
    out = []
    for i in range(0, n, 256):
        k = min(256, n - i)
        d = xfer(s, b"".join(struct.pack("<BI", 2, a + 4 * (i + j)) for j in range(k)))
        out += list(struct.unpack("<%dI" % k, d))
    return out
def w32(s, a, vals):
    xfer(s, b"".join(struct.pack("<BII", 6, a + 4 * i, v & 0xFFFFFFFF) for i, v in enumerate(vals)))
def w8(s, a, v): xfer(s, struct.pack("<BIB", 4, a, v))
def f(u): return struct.unpack("<f", struct.pack("<I", u))[0]
def main(a):
    s = conn(); c = a[0]; I = lambda x: int(x, 0)
    if c == "r32":
        ws = r32(s, I(a[1]), I(a[2]) if len(a) > 2 else 1)
        for i in range(0, len(ws), 8): print("%08X: %s" % (I(a[1]) + 4 * i, " ".join("%08X" % w for w in ws[i:i + 8])))
    elif c == "rf":
        ws = r32(s, I(a[1]), I(a[2]) if len(a) > 2 else 1); print(" ".join("%.4f" % f(w) for w in ws))
    elif c == "w32": w32(s, I(a[1]), [I(x) for x in a[2:]]); print("ok")
    elif c == "wf": w32(s, I(a[1]), [struct.unpack("<I", struct.pack("<f", float(a[2])))[0]]); print("ok")
    elif c == "w8": w8(s, I(a[1]), I(a[2])); print("ok")
    elif c == "dump":
        n = (I(a[2]) + 3) // 4; ws = r32(s, I(a[1]), n); open(a[3], "wb").write(struct.pack("<%dI" % n, *ws)); print("wrote", n * 4)
    elif c == "trace":
        t0 = time.time(); out = open(a[3], "w")
        while time.time() - t0 < float(a[1]):
            p = r32(s, 0x79E240)[0]
            if p:
                x = r32(s, p + 0x54, 3); out.write("%.2f %.3f %.3f %.3f\n" % (time.time() - t0, f(x[0]), f(x[1]), f(x[2])))
            time.sleep(1.0 / float(a[2]))
        out.close(); print("done")
if __name__ == "__main__": main(sys.argv[1:])
