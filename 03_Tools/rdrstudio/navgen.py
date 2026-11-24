"""AI nav paths generated over a level's walkable ground.

2026-11-18. The AI walks `line_navPath_*` splines in the level's .lnes (lvl_<name>_path.lnes): polylines that join
where their end points are the same coordinates (the shipped ones meet so: RANCH2's 19 paths, TOMB's bar 99 two-point
segments), a network over the ground. A level built from another's files (a new Showdown map starts from Ranch's)
carries that level's paths, which lead nowhere on the new ground.

From the level's collision (game space, y up):

1. a grid every `spacing` metres: at each point, every surface turned up (|normal y| > 0.7) with `head` metres free
   above it is somewhere to stand (each storey, each roof);
2. neighbouring points along the rows and columns are joined when the rise is one the player walks up (at most `max_slope`
   of the run, and `step` at most for a flat run) and nothing is in the way at knee and chest height;
3. one connected network, cut at its junctions so lines meet at their END points (as the shipped ones), at most
   MAX_POINTS points / MAX_LINES lines (wider spacing until it fits). The first version made rows and columns crossing
   mid-line, 1,533 points in 29 pieces, and the game froze as the AI started (2026-11-18).
"""
import math

UP_N = 0.7


def _tri_norm(a, b, c):
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    ln = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
    return nx / ln, ny / ln, nz / ln


class _Grid:
    """Triangles hashed by their x/z box on a square grid."""

    def __init__(self, tris, cell=4.0):
        self.cell = cell
        self.tris = tris
        self.norm = [_tri_norm(*t) for t in tris]
        self.cells = {}
        for k, (a, b, c) in enumerate(tris):
            x0, x1 = min(a[0], b[0], c[0]), max(a[0], b[0], c[0])
            z0, z1 = min(a[2], b[2], c[2]), max(a[2], b[2], c[2])
            for i in range(int(math.floor(x0 / cell)), int(math.floor(x1 / cell)) + 1):
                for j in range(int(math.floor(z0 / cell)), int(math.floor(z1 / cell)) + 1):
                    self.cells.setdefault((i, j), []).append(k)

    def near(self, x0, z0, x1, z1):
        c = self.cell
        out = set()
        for i in range(int(math.floor(min(x0, x1) / c)), int(math.floor(max(x0, x1) / c)) + 1):
            for j in range(int(math.floor(min(z0, z1) / c)), int(math.floor(max(z0, z1) / c)) + 1):
                out.update(self.cells.get((i, j), ()))
        return out

    def column(self, x, z):
        """[(height, |normal y|)] of every surface over/under (x, z)."""
        out = []
        for k in self.near(x, z, x, z):
            a, b, c = self.tris[k]
            d = (b[2] - c[2]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[2] - c[2])
            if abs(d) < 1e-12:
                continue
            l1 = ((b[2] - c[2]) * (x - c[0]) + (c[0] - b[0]) * (z - c[2])) / d
            l2 = ((c[2] - a[2]) * (x - c[0]) + (a[0] - c[0]) * (z - c[2])) / d
            l3 = 1.0 - l1 - l2
            if l1 < -1e-6 or l2 < -1e-6 or l3 < -1e-6:
                continue
            out.append((l1 * a[1] + l2 * b[1] + l3 * c[1], abs(self.norm[k][1])))
        out.sort()
        return out

    def blocked(self, p, q):
        """Whether the segment p..q goes through a triangle."""
        dx, dy, dz = q[0] - p[0], q[1] - p[1], q[2] - p[2]
        for k in self.near(p[0], p[2], q[0], q[2]):
            a, b, c = self.tris[k]
            e1 = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
            e2 = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
            h = (dy * e2[2] - dz * e2[1], dz * e2[0] - dx * e2[2], dx * e2[1] - dy * e2[0])
            det = e1[0] * h[0] + e1[1] * h[1] + e1[2] * h[2]
            if abs(det) < 1e-12:
                continue
            inv = 1.0 / det
            sx, sy, sz = p[0] - a[0], p[1] - a[1], p[2] - a[2]
            u = inv * (sx * h[0] + sy * h[1] + sz * h[2])
            if u < 0.0 or u > 1.0:
                continue
            qv = (sy * e1[2] - sz * e1[1], sz * e1[0] - sx * e1[2], sx * e1[1] - sy * e1[0])
            v = inv * (dx * qv[0] + dy * qv[1] + dz * qv[2])
            if v < 0.0 or u + v > 1.0:
                continue
            t = inv * (e2[0] * qv[0] + e2[1] * qv[1] + e2[2] * qv[2])
            if 0.0 < t < 1.0:
                return True
        return False


MAX_POINTS = 1000      # the shipped networks hold at most ~1,210 points (Bridge); 1,533 froze the game on the AI's start
MAX_LINES = 500
MIN_ISLAND = 8         # a separate piece with fewer points is left out (the AI cannot reach it from the rest)


def _lattice(g, bounds, spacing, head, step, max_slope):
    """(nodes {(i, j, layer): point}, edges [(node, node)]) of the walkable grid at `spacing`."""
    x0, z0, x1, z1 = bounds
    ni = int((x1 - x0) / spacing) + 1
    nj = int((z1 - z0) / spacing) + 1
    nodes, layers = {}, {}
    for i in range(ni):
        x = x0 + i * spacing
        for j in range(nj):
            z = z0 + j * spacing
            col = g.column(x, z)
            got = []
            for k, (h, ny) in enumerate(col):
                if ny < UP_N:
                    continue
                above = [hh for hh, _n in col[k + 1:] if hh > h + 0.05]
                if above and above[0] - h < head:
                    continue
                if got and h - got[-1] < 0.05:
                    continue
                got.append(h)
            for li, h in enumerate(got):
                nodes[(i, j, li)] = (x, h + 0.05, z)
            if got:
                layers[(i, j)] = got
    # joins: along the grid's rows and columns (a diagonal would cross a row and a column between their points)
    edges = []
    for (i, j, li), p in nodes.items():
        for di, dj in ((1, 0), (0, 1)):
            other = layers.get((i + di, j + dj))
            if not other:
                continue
            best = min(range(len(other)), key=lambda k: abs(other[k] - p[1]))
            q = nodes[(i + di, j + dj, best)]
            if abs(q[1] - p[1]) > max(step, spacing * max_slope):
                continue
            if any(g.blocked((p[0], p[1] + hh, p[2]), (q[0], q[1] + hh, q[2])) for hh in (0.5, 1.2)):
                continue
            edges.append(((i, j, li), (i + di, j + dj, best)))
    return nodes, edges


def _network(nodes, edges):
    """Polylines of a grid network as the shipped ones are made: one connected network (pieces of fewer than
    MIN_ISLAND points left out), cut at every junction so lines meet only at their END points -- where three or four
    meet (the shipped: up to ten), and through a point where two meet."""
    adj = {}
    for a, b in edges:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    seen, comps = set(), []
    for n in adj:
        if n in seen:
            continue
        comp, todo = [], [n]
        seen.add(n)
        while todo:
            m = todo.pop()
            comp.append(m)
            for o in adj[m]:
                if o not in seen:
                    seen.add(o)
                    todo.append(o)
        comps.append(comp)
    # the biggest pieces first, each whole, while they fit the shipped networks' size (a point where lines meet
    # counts once per line: about 2.2 points a grid point); pieces under MIN_ISLAND points never
    keep, budget = set(), MAX_POINTS
    for comp in sorted(comps, key=len, reverse=True):
        cost = int(len(comp) * 2.2) + 1
        if len(comp) < MIN_ISLAND or (keep and cost > budget):
            continue
        keep.update(comp)
        budget -= cost
    adj = {n: {o for o in v if o in keep} for n, v in adj.items() if n in keep}
    used, out = set(), []

    def walk(a, b):
        run = [a, b]
        used.add(frozenset((a, b)))
        while len(adj[run[-1]]) == 2:
            nxt = next(o for o in adj[run[-1]] if o != run[-2])
            if frozenset((run[-1], nxt)) in used:
                break
            used.add(frozenset((run[-1], nxt)))
            run.append(nxt)
        return run
    for n in sorted(adj):
        if len(adj[n]) != 2:                       # an end or a junction: the lines start here
            for o in sorted(adj[n]):
                if frozenset((n, o)) not in used:
                    out.append([nodes[k] for k in walk(n, o)])
    for n in sorted(adj):                          # loops with no junction on them
        for o in sorted(adj[n]):
            if frozenset((n, o)) not in used:
                out.append([nodes[k] for k in walk(n, o)])
    return out


def generate(tris, spacing=10.0, head=1.8, step=0.6, max_slope=0.7, bounds=None):
    """Polylines [[(x, y, z), ...]] over the walkable ground of collision triangles [((x,y,z), (x,y,z), (x,y,z))],
    joined at their end points, within the shipped networks' size (spaced wider until they fit).
    bounds: (x0, z0, x1, z1) to keep to (default: the collision's own box)."""
    if not tris:
        return []
    g = _Grid(tris)
    if bounds is None:
        xs = [p[0] for t in tris for p in t]
        zs = [p[2] for t in tris for p in t]
        bounds = (min(xs), min(zs), max(xs), max(zs))
    sp = float(spacing)
    for _try in range(12):
        nodes, edges = _lattice(g, bounds, sp, head, step, max_slope)
        out = _network(nodes, edges)
        if sum(len(l) for l in out) <= MAX_POINTS and len(out) <= MAX_LINES:
            generate.spacing_used = sp
            return out
        sp *= 1.25
    generate.spacing_used = sp
    return out


def lnes_text(lines, nl="\n", prefix="line_navPath_auto"):
    """The nodes of polylines as .lnes blocks (no header)."""
    blocks = []
    for k, pts in enumerate(lines, 1):
        b = ["node %s_%03d {" % (prefix, k), "\tmatrix", "\t{", "\t\t1.000000\t0.000000\t0.000000",
             "\t\t0.000000\t1.000000\t0.000000", "\t\t0.000000\t0.000000\t1.000000", "\t\t0.000000\t0.000000\t0.000000",
             "\t}", "\tattributes 0 {", "\t}", "\tVertices %d {" % len(pts)]
        b += ["\t\t%.6f\t%.6f\t%.6f" % p for p in pts]
        b += ["\t}", "}"]
        blocks.append(nl.join(b))
    return blocks


def rewrite_paths(text, lines, replace=True, prefix="line_navPath_auto"):
    """A .lnes's text with the generated paths: its navPath nodes (replace) or the earlier generated ones (not
    replace) taken out, the new ones added, NumLineNodes counted again. -> (text, removed, added)."""
    import re
    nl = "\r\n" if "\r\n" in text else "\n"
    blocks, i = [], 0
    for m in re.finditer(r"node\s+(\S+)\s*\{", text):
        if m.start() < i:
            continue
        depth, k = 0, m.end() - 1
        while k < len(text):
            if text[k] == "{":
                depth += 1
            elif text[k] == "}":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        blocks.append((m.group(1), text[m.start():k + 1]))
        i = k + 1
    kept, removed = [], 0
    for name, body in blocks:
        gone = name.startswith(prefix) or (replace and "navpath" in name.lower())
        if gone:
            removed += 1
        else:
            kept.append(body)
    new = lnes_text(lines, nl, prefix)
    allb = kept + new
    return ("NumLineNodes %d" % len(allb) + nl + nl + nl.join(allb) + nl), removed, len(new)


# ------------------------------------------------------------------------------------------------ the nav graph
#
# 2026-11-18. lvl_<name>_nav.rsc (NTG0) is the path file compiled: the AI's graph. Read from the shipped levels (every
# Showdown map rebuilt byte for byte from its _path.lnes):
#   RSC0 header (0x80: 'RSC0', 1, 0x80, 'NTG0', file size), then
#   0x00 u32 segments offset (0x30) | u16 segments, u16 their room (rounded up to 8)
#   0x08 u32 points offset          | u16 points,   u16 room
#   0x10 24 zero bytes
#   0x28 u32 links offset           | u16 links (2 a segment), u16 room
#   segments, 12 bytes each, in the path file's order (each line's, point to point): f32 length (3D), u16 point a,
#       u16 point b, u32 1 when the line is a Jump (attributes BOOL Jump true) else 0
#   points, 16 bytes each, every distinct point in order of first use: f32 x, y, z (the line's matrix offset added),
#       u16 its first link, u8 its link count, u8 0xCD
#   links, u16 each: for each point in turn, the segments that touch it (in segment order)
# A level made from another's files kept that level's graph while its path file changed (new paths, deleted ones):
# the game froze the moment the AI started.

def _f32(v):
    import struct
    return struct.unpack('<f', struct.pack('<f', v))[0]


def path_segments(text):
    """[(point a, point b, jump)] of a path file's lines, points in game space (float32, the matrix offset added)."""
    import re
    out = []
    for m in re.finditer(r'node\s+\S+\s*\{', text):
        depth, k = 0, m.end() - 1
        while k < len(text):
            if text[k] == '{':
                depth += 1
            elif text[k] == '}':
                depth -= 1
                if depth == 0:
                    break
            k += 1
        blk = text[m.start():k + 1]
        mat = re.search(r'matrix\s*\{([^}]*)\}', blk)
        off = [float(x) for x in mat.group(1).split()][9:12] if mat else [0.0, 0.0, 0.0]
        jump = bool(re.search(r'BOOL\s+Jump\s+true', blk, re.I))
        vm = re.search(r'Vertices\s+\d+\s*\{([^}]*)\}', blk)
        if not vm:
            continue
        pts = []
        for row in vm.group(1).strip().splitlines():
            v = row.split()
            if len(v) >= 3:
                pts.append(tuple(_f32(_f32(float(v[i])) + _f32(off[i])) for i in range(3)))
        out += [(a, b, jump) for a, b in zip(pts, pts[1:])]
    return out


MERGE = 0.11           # metres: points this near on every axis are one point of the graph


def build_nav(text):
    """The NTG0 bundle (PS2 .rsc bytes) of a path file's text."""
    import struct
    import numpy as np
    segs = path_segments(text)
    points, cells = [], {}
    edges = []

    def point(p):
        # a point within MERGE of one already met (on every axis) is that one: the shipped graphs were made so
        # (49 of the 50 rebuilt with exactly their counts)
        key = tuple(int(round(p[k] / MERGE)) for k in range(3))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for i in cells.get((key[0] + dx, key[1] + dy, key[2] + dz), ()):
                        q = points[i]
                        if max(abs(p[k] - q[k]) for k in range(3)) <= MERGE:
                            return i
        cells.setdefault(key, []).append(len(points))
        points.append(p)
        return len(points) - 1
    for a, b, jump in segs:
        ia, ib = point(a), point(b)
        if ia == ib:
            continue                                 # both ends one point: no segment
        pa, pb = np.array(points[ia], np.float32), np.array(points[ib], np.float32)
        ln = float(np.sqrt(np.sum((pa - pb) * (pa - pb), dtype=np.float32), dtype=np.float32))
        edges.append((ln, ia, ib, 1 if jump else 0))
    if len(points) > 0xFFFF or len(edges) > 0x7FFF:
        raise ValueError('a nav graph of %d points / %d segments is more than the game takes' % (len(points), len(edges)))
    touch = [[] for _ in points]
    for i, (_l, a, b, _j) in enumerate(edges):
        touch[a].append(i)
        if b != a:
            touch[b].append(i)
    room = lambda n: (n + 7) & ~7
    eo = 0x30
    no = eo + 12 * len(edges)
    ao = no + 16 * len(points)
    links = [i for t in touch for i in t]
    body = bytearray(struct.pack('<I2HI2H', eo, len(edges), room(len(edges)), no, len(points), room(len(points))))
    body += bytes(24)
    body += struct.pack('<I2H', ao, len(links), room(len(links)))
    for ln, a, b, j in edges:
        body += struct.pack('<f2HI', ln, a, b, j)
    first = 0
    for p, t in zip(points, touch):
        body += struct.pack('<3fHBB', p[0], p[1], p[2], first, min(len(t), 255), 0xCD)
        first += len(t)
    body += struct.pack('<%dH' % len(links), *links)
    while len(body) % 16:
        body += b'\0'
    head = bytearray(0x80)
    struct.pack_into('<4sII4sI', head, 0, b'RSC0', 1, 0x80, b'NTG0', 0x80 + len(body))
    return bytes(head + body)


def nav_counts(nav):
    """(segments, points) an NTG0 bundle holds, or None."""
    import struct
    try:
        if nav[12:16] != b'NTG0':
            return None
        _eo, ne, _ce, _no, nn, _cn = struct.unpack_from('<I2HI2H', nav, 0x80)
        return ne, nn
    except struct.error:
        return None


def xbox_nav(ps2):
    """The Xbox game's copy of an NTG0 bundle: the PS2's, padded with zeros to a multiple of 0x80 (its size word too)."""
    import struct
    n = (len(ps2) + 0x7F) & ~0x7F
    out = bytearray(ps2 + bytes(n - len(ps2)))
    struct.pack_into('<I', out, 16, n)
    return bytes(out)


def fit_nav(files):
    """An archive's files {name (lower case): bytes} with each level's nav graph (_nav.rsc / _nav.xrsc) made again
    from its path file when the two disagree (a New .dat level keeps its template's graph; paths generated, moved or
    deleted in the editor change the path file only). -> notes."""
    notes = []
    for low in list(files):
        if not (low.startswith('lvl_') and low.endswith('_path.lnes')):
            continue
        stem = low[:-len('_path.lnes')]
        try:
            nav = build_nav(files[low].decode('latin-1'))
        except Exception as ex:                             # noqa: BLE001
            notes.append('%s: its nav graph could not be made from its paths (%s)' % (stem, ex))
            continue
        for name, xbox in ((stem + '_nav.rsc', False), (stem + '_nav.xrsc', True)):
            have = files.get(name)
            if have is None or nav_counts(have) == nav_counts(nav):
                continue
            files[name] = xbox_nav(nav) if xbox else nav
            notes.append('%s: its AI nav graph held %s segments / points, its path file %s: made again from the path '
                         'file (the game froze as the AI started)' % (stem, nav_counts(have), nav_counts(nav)))
    return notes
