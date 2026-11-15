"""Do the eleven header counts actually explain the file size?

The executable names the fields a .mod carries, in order:

    verts: normals: colors: tex1s: tex2s: tangents:
    materials: adjuncts: primitives: matrices: reskins:

and the eleven words after the version string have exactly the right shapes to
be those counts -- tangents always 0, tex2s almost always 0, materials 1..9.
That is suggestive, not proof. If the reading is right then

    filesize = header + SUM(count_i * stride_i) + variable part

should hold across the whole disc with one fixed stride per field. This solves
for those strides over hundreds of files and reports how much of each file is
left unexplained. A wrong field order gives strides that are negative,
fractional, or absurd, and a large unexplained remainder.
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdrstudio.game import Game                      # noqa: E402
from rdrstudio.config import VANILLA_ISO             # noqa: E402

# The STOCK disc, not the working one: a read-only structural probe must
# not depend on what the last test left behind (REQ-035). The repoint was
# measured output-neutral at the time it was made -- see REQ-074.
DISC = Path(VANILLA_ISO)
MAGIC = b"version: 2.12\x00"
FIELDS = ["verts", "normals", "colors", "tex1s", "tex2s", "tangents",
          "materials", "adjuncts", "primitives", "matrices", "reskins"]


def collect(game, limit=600):
    out, seen = [], set()
    for arc in game.archives():
        try:
            toc = game.toc(arc)
        except Exception:
            continue
        for e in toc:
            if e.ext.lower() != "mod" or e.name in seen:
                continue
            seen.add(e.name)
            try:
                d = game.read(arc, e.name)
            except Exception:
                continue
            if d[:len(MAGIC)] == MAGIC and len(d) > 58:
                out.append((e.name, d))
            if len(out) >= limit:
                return out
    return out


def solve(rows, sizes):
    """Least-squares stride per field, via the normal equations.

    Written out rather than pulled from numpy so this runs anywhere the rest
    of the toolkit does.
    """
    n = len(FIELDS)
    ata = [[0.0] * (n + 1) for _ in range(n + 1)]
    atb = [0.0] * (n + 1)
    for r, sz in zip(rows, sizes):
        v = list(r) + [1.0]              # +1 for a constant overhead term
        for i in range(n + 1):
            for j in range(n + 1):
                ata[i][j] += v[i] * v[j]
            atb[i] += v[i] * sz
    # Gaussian elimination with partial pivoting
    m = [row[:] + [b] for row, b in zip(ata, atb)]
    N = n + 1
    for c in range(N):
        p = max(range(c, N), key=lambda r: abs(m[r][c]))
        if abs(m[p][c]) < 1e-9:
            continue
        m[c], m[p] = m[p], m[c]
        pv = m[c][c]
        for j in range(c, N + 1):
            m[c][j] /= pv
        for r in range(N):
            if r == c or abs(m[r][c]) < 1e-12:
                continue
            f = m[r][c]
            for j in range(c, N + 1):
                m[r][j] -= f * m[c][j]
    return [m[i][N] for i in range(N)]


def main():
    game = Game(DISC, writable=False)
    files = collect(game)
    print("%d loose .mod files\n" % len(files))

    rows, sizes, names = [], [], []
    for name, d in files:
        w = struct.unpack_from("<11I", d, len(MAGIC))
        rows.append([float(x) for x in w])
        sizes.append(float(len(d)))
        names.append(name)

    # Sanity: the shapes the executable's field order predicts.
    print("field shapes across the sample:")
    for i, f in enumerate(FIELDS):
        col = [int(r[i]) for r in rows]
        print("  %-11s min %-6d max %-6d  zero in %d/%d files"
              % (f, min(col), max(col), sum(1 for c in col if c == 0), len(col)))

    sol = solve(rows, sizes)
    print("\nleast-squares bytes per element:")
    for f, s in zip(FIELDS, sol[:-1]):
        print("  %-11s %8.3f" % (f, s))
    print("  %-11s %8.3f" % ("(constant)", sol[-1]))

    # How well does it actually fit?
    resid = []
    for r, sz in zip(rows, sizes):
        pred = sum(a * b for a, b in zip(r, sol[:-1])) + sol[-1]
        resid.append(sz - pred)
    resid.sort()
    n = len(resid)
    print("\nunexplained bytes per file (actual - predicted):")
    print("  min %.0f   p10 %.0f   median %.0f   p90 %.0f   max %.0f"
          % (resid[0], resid[n // 10], resid[n // 2],
             resid[9 * n // 10], resid[-1]))
    near = sum(1 for x in resid if abs(x) < 64)
    print("  within 64 bytes: %d/%d (%.0f%%)" % (near, n, 100.0 * near / n))

    game.close()


if __name__ == "__main__":
    main()
