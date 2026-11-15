#!/usr/bin/env python3
r"""Snapshot + diff RAM regions over PINE (read-only) -- the "what changed when I did X" tool for mapping structs.

    py -3 scripts/rdr_snap.py snap LABEL ADDR:LEN [ADDR:LEN ...]      # ADDR may be '*0x0079E240' = follow that pointer first
    py -3 scripts/rdr_snap.py diff LABEL_A LABEL_B                    # every 32-bit word that differs, shown as int / float
    py -3 scripts/rdr_snap.py show LABEL [ADDR]                       # words of a snapshot, int / float / pointer-ish
Snapshots live in 07_Research/snaps/LABEL.json.  Source: PINE (default) or --savestate X.p2s.  Only PINE reads are sent."""
import json, os, struct, sys
import rdr_research as RR
HERE = os.path.dirname(os.path.abspath(__file__))
SNAPS = os.environ.get("RDR_SNAPS") or os.path.normpath(os.path.join(HERE, "..", "..", "07_Research", "snaps"))


def f32(u):
    return struct.unpack("<f", struct.pack("<I", u))[0]


def describe(u):
    f = f32(u)
    parts = ["%d" % (u - (1 << 32) if u & 0x80000000 else u), "0x%08X" % u]
    if u and 1e-3 < abs(f) < 1e7:
        parts.append("%.4f" % f)
    if 0x00100000 <= u < 0x02000000:
        parts.append("ptr")
    return "  ".join(parts)


def snap(argv):
    skip = {i + 1 for i, a in enumerate(argv) if a in ("--ram", "--savestate")}      # their values are file paths, not ADDR:LEN
    label, specs = argv[0], [a for i, a in enumerate(argv[1:], 1) if not a.startswith("--") and i not in skip]
    src = RR.open_source(argv)
    out = {}
    for sp in specs:
        a, n = sp.split(":")
        base = None
        if a.startswith("*"):
            base = int(a[1:], 0)
            a = struct.unpack("<I", src.read(base, 4))[0]
        else:
            a = int(a, 0)
        n = int(n, 0)
        out["0x%08X" % a] = src.read(a, n).hex()
        if base is not None:
            out["0x%08X" % a + "_via"] = "0x%08X" % base
    os.makedirs(SNAPS, exist_ok=True)
    json.dump(out, open(os.path.join(SNAPS, label + ".json"), "w"))
    print("saved %s: %s" % (label, ", ".join(k for k in out if not k.endswith("_via"))))


def load(label):
    return json.load(open(os.path.join(SNAPS, label + ".json")))


def diff(argv):
    A, B = load(argv[0]), load(argv[1])
    for k in A:
        if k.endswith("_via") or k not in B:
            continue
        a, b = bytes.fromhex(A[k]), bytes.fromhex(B[k])
        base = int(k, 16)
        rows = []
        for i in range(0, min(len(a), len(b)) - 3, 4):
            x, y = struct.unpack_from("<I", a, i)[0], struct.unpack_from("<I", b, i)[0]
            if x != y:
                rows.append("  +0x%03X (0x%08X): %s  ->  %s" % (i, base + i, describe(x), describe(y)))
        print("region %s: %d words differ" % (k, len(rows)))
        print("\n".join(rows))


def show(argv):
    A = load(argv[0])
    for k, v in A.items():
        if k.endswith("_via"):
            continue
        b = bytes.fromhex(v)
        for i in range(0, len(b) - 3, 4):
            u = struct.unpack_from("<I", b, i)[0]
            if u:
                print("+0x%03X  %s" % (i, describe(u)))


if __name__ == "__main__":
    cmd, rest = sys.argv[1], sys.argv[2:]
    {"snap": snap, "diff": diff, "show": show}[cmd](rest)
