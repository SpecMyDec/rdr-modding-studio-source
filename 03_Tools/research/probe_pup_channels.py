"""probe_pup_channels.py -- the `.pup` "role" field is a CHANNEL COUNT.

Two open questions closed at once, both of them mine and both wrong:

  * `CAPABILITIES.md` C-034 says the high `.pup` role codes are *"inferred as
    creature-type ids"* from the three-letter stems beside them. **They are
    not.**
  * `probe_iani.py` recovers a per-frame float count N from the file length,
    and rdr-fmt correctly objected that divisibility settles LAYOUT, not
    MEANING -- nothing external confirmed N was a channel count.

Both fall to one regression. For every stem with a `.pup` and an `.iani`, fit

    N  =  sum over the cast of  w[role]

over 352 files and 19 distinct role codes. The fit is not approximate:

    exact hits (|err| < 0.5) : 352 of 352   (100%)
    median |err|             : 0.00
    max |err|                : 0.0

and every fitted weight lands on an integer. Then the integers:

    role 0x06 -> 6     0x07 -> 7     0x09 -> 9     0x0c -> 12    0x0f -> 15
    role 0x10 -> 16    0x12 -> 18    0x18 -> 24    0x24 -> 36    0x7b -> 123
    role 0x7e -> 126   0x9f -> 159   0xa2 -> 162   0xa8 -> 168   0xab -> 171
    role 0xb1 -> 177   0xb4 -> 180   0xb7 -> 183   0xbd -> 189

**The role code equals the channel count, for all 19.** It was never an id.
`camera` is 0x07 because a camera contributes 7 channels; a full character rig
is 0x9f because it contributes 159. 17 of the 19 are exact multiples of 3
(159 = 53x3, 180 = 60x3), consistent with three channels per bone; the two that
are not are the camera (7) and 0x10 (16).

So N IS the channel count, confirmed against a source outside the `.iani` --
which is what rdr-fmt's objection asked for and what divisibility alone could
never supply.

WHAT THIS CORRECTS, explicitly, because both errors are mine:
  * C-034's "creature-type ids" inference. The stems beside the codes are
    character names, so the reading was plausible; it was still a guess, it was
    labelled as one, and it is now disproved.
  * `probe_iani.py`'s claim to have "recovered C" on the strength of the
    length alone. The length gives the number; this gives its meaning.

    python3 03_Tools/research/probe_pup_channels.py [--iso PATH]
"""

import argparse
import collections
import json
import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
from rdrstudio.iso9660 import Iso9660          # noqa: E402
from rdrstudio import dave                     # noqa: E402
import probe_pup as P                          # noqa: E402

REPO = os.path.dirname(os.path.dirname(HERE))
VANILLA = os.path.join(REPO, "01_Vanilla", "RDR_VANILLA.iso")


def solve(rows, rhs, n):
    """Least squares by normal equations; no numpy on this machine."""
    A = [[0.0] * n for _ in range(n)]
    B = [0.0] * n
    for v, y in zip(rows, rhs):
        for i in range(n):
            if v[i]:
                for j in range(n):
                    A[i][j] += v[i] * v[j]
                B[i] += v[i] * y
    for i in range(n):
        A[i][i] += 1e-6                    # tiny ridge, for a singular role
    M = [A[i][:] + [B[i]] for i in range(n)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        M[c], M[p] = M[p], M[c]
        if abs(M[c][c]) < 1e-9:
            continue
        for r in range(n):
            if r != c and M[r][c]:
                f = M[r][c] / M[c][c]
                for k in range(c, n + 1):
                    M[r][k] -= f * M[c][k]
    return [M[i][n] / M[i][i] if abs(M[i][i]) > 1e-9 else 0.0 for i in range(n)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso", default=VANILLA)
    ap.add_argument("--json", default=os.path.join(REPO, "07_Research", "pup_channels.json"))
    args = ap.parse_args()

    iso = Iso9660(args.iso)
    pup, iani = {}, {}
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b"Dave", b"DAVE"):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            stem = e.name.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
            if e.ext == "pup":
                try:
                    actors, _fr = P.parse(P.inflate(d.backing, e))
                except Exception:                      # noqa: BLE001
                    continue
                pup.setdefault(stem, [r for _n, r in actors])
            elif e.ext == "iani":
                raw = d.backing.read_at(e.offset, e.size_comp)
                try:
                    b = zlib.decompress(raw, -15) if e.compressed else raw
                except zlib.error:
                    continue
                if len(b) < 16 or b[:4] != b"SIA0":
                    continue
                _act, _fps, frames = struct.unpack_from("<IfI", b, 4)
                rem = len(b) - 16
                if frames == 0 or rem % (4 * frames):
                    continue
                iani.setdefault(stem, rem // (4 * frames))
    iso.close()

    pairs = [(s, pup[s], iani[s]) for s in iani if s in pup]
    roles = sorted({r for _s, rs, _N in pairs for r in rs})
    idx = {r: i for i, r in enumerate(roles)}
    rows, rhs = [], []
    for _s, rs, N in pairs:
        v = [0.0] * len(roles)
        for r in rs:
            v[idx[r]] += 1
        rows.append(v)
        rhs.append(float(N))
    w = solve(rows, rhs, len(roles))

    errs = []
    for _s, rs, N in pairs:
        errs.append(abs(sum(w[idx[r]] for r in rs) - N))
    exact = sum(1 for e in errs if e < 0.5)
    errs.sort()
    fitted = {r: round(w[idx[r]]) for r in roles}
    identity = all(r == fitted[r] for r in roles)
    thirds = sum(1 for r in roles if fitted[r] % 3 == 0)

    print("N = sum over the cast of w[role]:  %d files, %d roles"
          % (len(pairs), len(roles)))
    print("  exact hits (|err| < 0.5): %d of %d  (%.1f%%)"
          % (exact, len(pairs), 100.0 * exact / len(pairs)))
    print("  median |err| %.2f   max %.2f" % (errs[len(errs) // 2], errs[-1]))
    print("\n  role   fitted   role == fitted?")
    for r in roles:
        print("  0x%02x   %6d   %s" % (r, fitted[r], "YES" if r == fitted[r] else "NO"))
    print("\nROLE CODE EQUALS CHANNEL COUNT for all %d roles: %s"
          % (len(roles), identity))
    print("%d of %d are exact multiples of 3 (three channels per bone);"
          % (thirds, len(roles)))
    print("the exceptions are the camera (0x07 -> 7) and 0x10 -> 16.")

    json.dump({"files": len(pairs), "roles": len(roles), "exact": exact,
               "max_err": errs[-1], "fitted": {("0x%02x" % r): fitted[r] for r in roles},
               "role_equals_channels": identity, "multiples_of_3": thirds},
              open(args.json, "w"), indent=1)
    print("\n-> %s" % args.json)


if __name__ == "__main__":
    main()
