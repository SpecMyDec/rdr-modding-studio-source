#!/usr/bin/env python3
r"""Shared reader for the two Research code mods (Function-Hit Logger, Memory Watch).  READ-ONLY: it only ever sends
PINE read commands (or reads a savestate file).  Layout constants mirror rdrstudio/codemods_research.py.

Memory source, pick one:
    (default)            PCSX2 over PINE  (Settings > Advanced > Enable PINE, port 28011)
    --savestate X.p2s    a PCSX2 savestate (zip with eeMemory.bin; set SavestateCompressionType = 0 so Python can read it)
    --ram FILE           a raw 32 MB EE RAM dump
"""
import json, math, os, socket, struct, sys, zipfile

HOST, PORT = "127.0.0.1", 28011
HL_DATA, HL_TABLE, HL_RINGOFF, HL_RING, HL_SIZE = 0x000F3000, 0x40, 0x300, 128, 0xB00
MW_DATA, MW_TABLE, MW_RESULTS, MW_SIZE = 0x000F5800, 0x10, 0x400, 0x600
TYPE_NAMES = ["u32", "s32", "f32", "u16", "u8", "s16"]
STATUS = {0: "-", 1: "ok", 2: "null ptr", 3: "bad addr"}
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.normpath(os.path.join(HERE, "..", "..", "02_Modded", "research_config.json"))


class Pine:
    def __init__(self, host=HOST, port=PORT):
        self.s = socket.create_connection((host, port), timeout=3)

    def _recv(self, n):
        b = b""
        while len(b) < n:
            c = self.s.recv(n - len(b))
            if not c:
                raise IOError("PINE closed")
            b += c
        return b

    def _txn(self, payload):
        self.s.sendall(struct.pack("<I", len(payload) + 4) + payload)
        n = struct.unpack("<I", self._recv(4))[0]
        body = self._recv(n - 4)
        if not body or body[0] != 0:
            raise IOError("PINE request failed")
        return body[1:]

    def read32(self, addr):
        return struct.unpack("<I", self._txn(struct.pack("<BI", 2, addr)))[0]

    def read(self, addr, n):
        return b"".join(struct.pack("<I", self.read32(addr + i)) for i in range(0, n, 4))[:n]


class RamFile:
    def __init__(self, data):
        self.d = data

    def read(self, addr, n):
        return self.d[addr & 0x1FFFFFF:(addr & 0x1FFFFFF) + n]


def open_source(argv):
    if "--savestate" in argv:
        z = zipfile.ZipFile(argv[argv.index("--savestate") + 1])
        return RamFile(z.read("eeMemory.bin"))
    if "--ram" in argv:
        return RamFile(open(argv[argv.index("--ram") + 1], "rb").read())
    return Pine()


def load_config(path=None):
    path = path or DEFAULT_CONFIG
    if os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))
    sys.path.insert(0, os.path.join(HERE, ".."))
    from rdrstudio import codemods_research as R
    return R.DEFAULT_CONFIG


def f32(u):
    return struct.unpack("<f", struct.pack("<I", u & 0xFFFFFFFF))[0]


def fmt_value(u, t):
    if t == "f32":
        v = f32(u)
        return "%.4f" % v if math.isfinite(v) else "nan/inf (0x%08X)" % u
    if t == "s32":
        return str(u - (1 << 32) if u & 0x80000000 else u)
    if t == "s16":
        u &= 0xFFFF
        return str(u - 0x10000 if u & 0x8000 else u)
    return "%d (0x%X)" % (u, u)


def read_watch(src, cfg):
    """-> None if the mod is not in this build, else (frames, rows[{label,addr,final,value,text,status}])."""
    h = src.read(MW_DATA, 16)
    if struct.unpack_from("<I", h, 0)[0] != struct.unpack("<I", b"MWCH")[0]:
        return None
    n, frames = struct.unpack_from("<2I", h, 4)
    blob = src.read(MW_DATA + MW_TABLE, 16 * n)
    res = src.read(MW_DATA + MW_RESULTS, 16 * n)
    wl = cfg.get("watch", [])
    rows = []
    for i in range(n):
        a, t, nd, _, o1, o2, _ = struct.unpack_from("<IBBHhhI", blob, 16 * i)
        fin, val, st, fr = struct.unpack_from("<4I", res, 16 * i)
        label = wl[i].get("label", "") if i < len(wl) else ""
        tn = TYPE_NAMES[t] if t < 6 else "?"
        rows.append(dict(label=label or "0x%08X" % a, addr=a, final=fin, value=val, status=st, type=tn,
                         text=fmt_value(val, tn) if st == 1 else STATUS.get(st, "?")))
    return frames, rows


def read_hitlog(src, cfg):
    h = src.read(HL_DATA, 0x20)
    if struct.unpack_from("<I", h, 0)[0] != struct.unpack("<I", b"HLOG")[0]:
        return None
    n, seq, depth = struct.unpack_from("<3I", h, 4)[0], struct.unpack_from("<I", h, 8)[0], struct.unpack_from("<I", h, 0x10)[0]
    names = [e.get("name", "") if isinstance(e, dict) else "" for e in cfg.get("hitlog", [])]
    tbl = src.read(HL_DATA + HL_TABLE, 32 * n)
    funcs = []
    for i in range(n):
        fn, cnt, a0, a1, a2, a3, ra, ret = struct.unpack_from("<8I", tbl, 32 * i)
        funcs.append(dict(addr=fn, name=names[i] if i < len(names) else "", count=cnt, args=[a0, a1, a2, a3], caller=ra, ret=ret))
    ring_raw = src.read(HL_DATA + HL_RINGOFF, 16 * HL_RING)
    ring = []
    for k in range(HL_RING):
        s_, fn, a0, a1 = struct.unpack_from("<4I", ring_raw, 16 * k)
        if s_:
            ring.append(dict(seq=s_, fn=fn, a0=a0, a1=a1))
    ring.sort(key=lambda r: r["seq"])
    return dict(total=seq, shadow_depth=depth, funcs=funcs, ring=ring)


def hitlog_text(d):
    out = ["Function-Hit Logger: %d calls total (shadow-stack depth now %d)" % (d["total"], d["shadow_depth"]), "",
           "%-10s %-28s %8s  %-10s %-10s %-10s %-10s  %-10s %-10s" % ("function", "name", "calls", "a0", "a1", "a2", "a3", "caller", "returned")]
    for f in d["funcs"]:
        if f["count"]:
            out.append("0x%08X %-28s %8d  %s  0x%08X 0x%08X" % (f["addr"], f["name"][:28], f["count"],
                       " ".join("0x%08X" % a for a in f["args"]), f["caller"], f["ret"]))
        else:
            out.append("0x%08X %-28s %8d  (never called)" % (f["addr"], f["name"][:28], 0))
    if d["ring"]:
        nm = {f["addr"]: f["name"] for f in d["funcs"]}
        out += ["", "last %d calls (oldest first): seq, function, a0, a1" % len(d["ring"])]
        out += ["  #%-7d 0x%08X %-24s a0=0x%08X a1=0x%08X" % (r["seq"], r["fn"], nm.get(r["fn"], "")[:24], r["a0"], r["a1"]) for r in d["ring"]]
    return "\n".join(out)
