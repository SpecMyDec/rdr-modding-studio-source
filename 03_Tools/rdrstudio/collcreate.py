"""The collision creator: new collision shells from triangles, a shell's triangles replaced, a shell removed -- on
every bundle the level has.

2026-11-18. The level editor makes shapes (blocks, ramps, floor plates, corners dragged, faces deleted) and sends
them here as game-space points and faces. This writes them as the game reads collision:

* a BND0 chunk built by the same writer the Blender add-on's level export uses (dataeditor_lib/rdrformats/
  bnd_write: a triangle is [a, b, c, 0] -- with a 4th corner of 0xFFFF the player was flung, 2026-11-18), its
  surface the bundle's own "default" physics material (a PMT0 chunk number, each bundle its own);
* a `bound_<name>_NN` node in the level's .bnds whose resourceIndex points at the PS2 bundle's chunk and
  resourceIndexXbox at the Xbox bundle's. The two bundles number their chunks their own way (a custom level's
  .rsc held 921 chunks and its .xrsc 923): one number for both pointed the Xbox at the wrong chunk ("Expected
  BND0", a crash).

Where it writes:

* a level with a mods folder (mods/<group>/<name>.dat/): there -- its .rsc and/or .xrsc and its .bnds. The mods
  folder is what a Build puts in the game, so an edit of the game's copy would be undone by the next Build; and a
  level the mods folder holds for the Xbox only is shown from a PS2 copy in the cache, which is made again from
  the folder when a file of it changes (xboxmode.converted_folder_level). Undone with `undo` (the files as they
  were, kept here).
* a level of the disc: its .rsc on the working disc, through Studio.write (the journal: Ctrl+Z), the .bnds through
  Level.apply. The Xbox build makes its .xrsc from the .rsc (with its own numbers, xboxmode._bnds_for_xbox).

An .xrsc's chunks end on 0x80 (the game loads them so), a .rsc's on 16: rewritten each with its own.
"""
import os
import re
import sys


SOLID_FLAGS = {          # as the add-on writes a solid shell (bound_grp_tttttttfffff): walked on, stops the camera
    "BoundFlagMover": True, "BoundFlagCamera": True, "BoundFlagTarget": True, "BoundFlagShoot": True,
    "BoundFlagSneak": True, "BoundFlagEnemy": True, "BoundFlagHorse": True, "BoundFlagPlayer": False,
    "BoundFlagDoor": False, "BoundFlagCamClip": False, "BoundFlagWater": False, "BoundFlagPlummet": False,
}
WALK_OFF = ("BoundFlagMover", "BoundFlagSneak", "BoundFlagEnemy", "BoundFlagHorse")   # walk-through: these off
MAX_FACES = 20000
NODE_RE = re.compile(r'node\s+(\S+)\s*\{', re.S)


class CollError(Exception):
    pass


def _lib():
    from .dataeditor import _LIB
    if _LIB not in sys.path:
        sys.path.insert(0, _LIB)
    from rdrformats import bnd_write, rscwrite, rsc as rrsc, bnd
    return bnd_write, rscwrite, rrsc, bnd


# ---------------------------------------------------------------------------------------------------- the shell

def default_pmt(bundle):
    """The PMT0 chunk number (1-based) of the bundle's "default" physics material, else its first, else 1."""
    _bw, _rw, R, _b = _lib()
    first = None
    for i, c in enumerate(R.read_chunks(bundle), 1):
        if c.tag == "PMT0" and len(c.data) > 8:
            raw = c.data[8:40]
            name = (raw[:raw.index(b"\0")] if b"\0" in raw else raw).decode("latin-1").lower()
            if name == "default":
                return i
            first = first or i
    return first or 1


def shell(verts, faces, pmt):
    """BND0 bytes from game-space points [(x, y, z)] and faces [[i, j, k(, l ...)]] (counter-clockwise seen from
    the side they collide on; n-gons and bent quads cut into triangles)."""
    BW, _rw, _r, _b = _lib()
    vs = [tuple(float(c) for c in v) for v in verts]
    if not vs or not faces:
        raise CollError("the shape has no faces")
    if len(faces) > MAX_FACES:
        raise CollError("%d faces is more than one shell takes here (%d)" % (len(faces), MAX_FACES))
    for f in faces:
        if len(f) < 3 or any(not 0 <= int(i) < len(vs) for i in f):
            raise CollError("a face points at a corner the shape does not have")
    fs = BW.faces_from_polygons(vs, [[int(i) for i in f] for f in faces], 0)
    if not fs:
        raise CollError("every face of the shape has no size")
    if len(fs) > 500:
        leaf, split = BW.LIGHT_LEAF
        return BW.write(vs, fs, (pmt,), leaf_faces=leaf, min_split=split)
    return BW.write(vs, fs, (pmt,))


# ---------------------------------------------------------------------------------------------------- bundles

def _set_chunk(data, index, payload, xbox):
    """The bundle with chunk `index` (0-based) replaced by payload, or payload appended (index None). -> (bytes,
    index). Chunk numbers never move."""
    if not xbox:
        from . import rsc as SR
        if index is None:
            return SR.append_chunk(data, "BND0", payload, "collision")
        return SR.replace_chunk(data, index, payload, "collision"), index
    _bw, RW, R, _b = _lib()
    chunks = [(c.tag, c.data) for c in R.read_chunks(data)]
    if index is None:
        chunks.append(("BND0", payload))
        index = len(chunks) - 1
    else:
        if not 0 <= index < len(chunks) or chunks[index][0] != "BND0":
            raise CollError("chunk %d of the Xbox bundle is not collision" % (index + 1))
        chunks[index] = ("BND0", payload)
    return RW.write_bundle(chunks, RW.ALIGN), index


def _node_block(name, ps2_num, xbox_num, solid, nl):
    flags = dict(SOLID_FLAGS)
    if not solid:
        for f in WALK_OFF:
            flags[f] = False
    lines = ["node %s {" % name, "\tresourceIndex %d " % ps2_num, "\tresourceIndexXbox %d " % xbox_num,
             "\tmatrix", "\t{", "\t\t1.000000 \t0.000000 \t0.000000 ", "\t\t0.000000 \t1.000000 \t0.000000 ",
             "\t\t0.000000 \t0.000000 \t1.000000 ", "\t\t0.000000 \t0.000000 \t0.000000 ", "\t}",
             "\tattributes 12 {"]
    lines += ["\t\tBOOL\t%s\t%s" % (f, "true" if v else "false") for f, v in flags.items()]
    lines += ["\t}", "}"]
    return nl.join(lines)


def _find_node(text, name):
    """(start, end) of node `name`'s block in .bnds text, or None."""
    for m in NODE_RE.finditer(text):
        if m.group(1) != name:
            continue
        depth, i = 0, m.end() - 1
        while i < len(text):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    return m.start(), i + 1
            i += 1
    return None


def _node_numbers(text, span):
    blk = text[span[0]:span[1]]
    a = re.search(r"resourceIndex\s+(-?\d+)", blk)
    b = re.search(r"resourceIndexXbox\s+(-?\d+)", blk)
    return (int(a.group(1)) if a else 0), (int(b.group(1)) if b else 0)


def _bump(text, by):
    return re.sub(r"(NumInstNodes\s+)(\d+)", lambda m: m.group(1) + str(max(0, int(m.group(2)) + by)), text,
                  count=1)


# ---------------------------------------------------------------------------------------------------- stores

class FolderStore:
    """A level's files in its mods folder."""
    kind = "folder"

    def __init__(self, folder, stem):
        self.folder, self.stem = folder, stem
        have = {f.lower(): f for f in os.listdir(folder)}
        self.ps2 = have.get(stem + ".rsc")
        self.xbox = have.get(stem + ".xrsc")
        self.bnds = sorted(f for lf, f in have.items() if lf.startswith(stem + "_") and lf.endswith(".bnds"))
        self.before = {}

    def read(self, name):
        with open(os.path.join(self.folder, name), "rb") as f:
            return f.read()

    writer = None        # Studio.write for a level only the mods folder has: journalled (Ctrl+Z)

    def write(self, name, data):
        if self.writer is not None:
            self.writer(name, data)
            return
        p = os.path.join(self.folder, name)
        if name not in self.before:
            self.before[name] = open(p, "rb").read() if os.path.exists(p) else None
        tmp = p + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, p)


class DiscStore:
    """A level of the working disc: its .rsc and .bnds through Studio.write (journalled: one group, one Ctrl+Z)."""
    kind = "disc"
    xbox = None

    def __init__(self, studio, archive, lvl, label):
        self.s, self.arc, self.label = studio, archive, label
        self.group = studio.journal.new_group()
        self.ps2 = lvl.rsc_name
        self.bnds = list(dict.fromkeys(n.source for n in lvl.nodes
                                       if n.kind == "bound" and n.source.lower().endswith(".bnds")))
        self.before = {}

    def read(self, name):
        return self.s.disc.archive(self.arc).read(name)

    def write(self, name, data):
        self.s.write(self.arc, name, data, self.label, self.group)


def mods_folder(de, archive):
    """mods/<group>/<name>.dat/ of a level archive ("LVLS_M2/MANTOMB2.DAT"), or None."""
    if de is None or "/" not in archive:
        return None
    group, fname = archive.split("/", 1)
    top = os.path.join(de.mods, group.lower())
    if not os.path.isdir(top):
        return None
    for f in os.listdir(top):
        if f.lower() == fname.lower() and os.path.isdir(os.path.join(top, f)):
            return os.path.join(top, f)
    return None


def level_stem(archive):
    return "lvl_" + archive.rsplit("/", 1)[-1][:-4].lower()


def folder_store(de, archive):
    folder = mods_folder(de, archive)
    if not folder:
        return None
    st = FolderStore(folder, level_stem(archive))
    return st if (st.ps2 or st.xbox) and st.bnds else None


# ---------------------------------------------------------------------------------------------------- operations

def _pick_bnds(store):
    """The .bnds a new node goes in: the add-on's _custom one, else the one holding most bound nodes."""
    best, n_best = None, -1
    for f in store.bnds:
        if f.lower().endswith("_custom.bnds"):
            return f
        n = len(NODE_RE.findall(store.read(f).decode("latin-1")))
        if n > n_best:
            best, n_best = f, n
    return best


def _unique(store, stem):
    taken = set()
    for f in store.bnds:
        taken |= set(NODE_RE.findall(store.read(f).decode("latin-1")))
    stem = re.sub(r"[^A-Za-z0-9_]+", "_", stem or "custom").strip("_").lower() or "custom"
    k = 1
    while "bound_%s_%02d" % (stem, k) in taken:
        k += 1
    return "bound_%s_%02d" % (stem, k)


def add(store, verts, faces, name="custom", solid=True):
    """A new shell. -> {name, chunks: {ps2, xbox}, file}."""
    nums = {}
    for fname, xbox in ((store.ps2, False), (store.xbox, True)):
        if not fname:
            continue
        data = store.read(fname)
        out, idx = _set_chunk(data, None, shell(verts, faces, default_pmt(data)), xbox)
        store.write(fname, out)
        nums["xbox" if xbox else "ps2"] = idx + 1
    ps2_num = nums.get("ps2", nums.get("xbox"))
    xbox_num = nums.get("xbox", nums.get("ps2"))
    target = _pick_bnds(store)
    raw = store.read(target)
    text = raw.decode("latin-1")
    nl = "\r\n" if "\r\n" in text else "\n"
    node = _unique(store, name)
    body = text.rstrip("\r\n \t")
    text = _bump(body + nl + _node_block(node, ps2_num, xbox_num, solid, nl) + nl, +1)
    store.write(target, text.encode("latin-1"))
    return dict(name=node, chunks=nums, file=target)


def add_many(store, shapes, name="auto", solid=True, names=None):
    """Several new shells at once [(verts, faces)]: each bundle written once and the .bnds once (one shell at a
    time rewrote an 11 MB bundle per shell). names: the node names to give them (a model's collision pieces: named
    after its zone); else bound_<name>_NN. -> [node names]."""
    if not shapes:
        return []
    given = list(names) if names else None
    nums = []                                          # per shape: {ps2, xbox}
    per = [dict() for _ in shapes]
    for fname, xbox in ((store.ps2, False), (store.xbox, True)):
        if not fname:
            continue
        data = store.read(fname)
        pmt = default_pmt(data)
        for k, (verts, faces) in enumerate(shapes):
            data, idx = _set_chunk(data, None, shell(verts, faces, pmt), xbox)
            per[k]["xbox" if xbox else "ps2"] = idx + 1
        store.write(fname, data)
    target = _pick_bnds(store)
    text = store.read(target).decode("latin-1")
    nl = "\r\n" if "\r\n" in text else "\n"
    body = text.rstrip("\r\n \t")
    names = []
    taken = set()
    for f in store.bnds:
        taken |= set(NODE_RE.findall(store.read(f).decode("latin-1")))
    stem = re.sub(r"[^A-Za-z0-9_]+", "_", name or "auto").strip("_").lower() or "auto"
    k = 1
    for j, n in enumerate(per):
        if given:
            node = given[j]
        else:
            while "bound_%s_%02d" % (stem, k) in taken:
                k += 1
            node = "bound_%s_%02d" % (stem, k)
        taken.add(node)
        p, x = n.get("ps2", n.get("xbox")), n.get("xbox", n.get("ps2"))
        body += nl + _node_block(node, p, x, solid, nl)
        names.append(node)
    text = _bump(body + nl, len(names))
    store.write(target, text.encode("latin-1"))
    return names


PLACEHOLDER = [(0.0, -1000.0, 0.0), (0.2, -1000.0, 0.0), (0.0, -1000.0, 0.2)]  # big enough for the writer (1 cm2)


def _empty_chunks(store, nums):
    """The chunks of removed shells made a bound that stops nothing, far under the level, on every bundle: a chunk
    keeps its number (nothing may move), and left as it was the editor still drew it, nameless."""
    for fname, xbox, key in ((store.ps2, False, 0), (store.xbox, True, 1)):
        if not fname:
            continue
        idxs = [n[key] for n in nums if n[key] > 0]
        if not idxs:
            continue
        data = store.read(fname)
        body = shell(PLACEHOLDER, [[0, 2, 1]], default_pmt(data))
        for i in idxs:
            try:
                data, _i = _set_chunk(data, i - 1, body, xbox)
            except (CollError, ValueError):
                continue
        store.write(fname, data)


def remove_many(store, nodes):
    """Several nodes out of their .bnds files, each file written once; their chunks emptied."""
    gone = 0
    nums = []
    for f in store.bnds:
        text = store.read(f).decode("latin-1")
        hit = 0
        for node in nodes:
            span = _find_node(text, node)
            if span:
                nums.append(_node_numbers(text, span))
                a, b = span
                while b < len(text) and text[b] in "\r\n":
                    b += 1
                text = text[:a] + text[b:]
                hit += 1
        if hit:
            store.write(f, _bump(text, -hit).encode("latin-1"))
            gone += hit
    _empty_chunks(store, nums)
    return gone


def _locate(store, node):
    for f in store.bnds:
        text = store.read(f).decode("latin-1")
        span = _find_node(text, node)
        if span:
            return f, text, span
    raise CollError("no collision node %s in this level's .bnds" % node)


def replace(store, node, verts, faces, chunk=None):
    """Node `node`'s shell made these triangles, on every bundle (each at its own chunk). A shell no node names
    (bound_<chunk>) only on a disc level: by its PS2 chunk (0-based)."""
    if node is None or re.fullmatch(r"bound_\d+", node or ""):
        if store.kind != "disc" or chunk is None:
            raise CollError("this collision has no node in the level's .bnds, so its Xbox chunk is not known: "
                            "delete it and add a new shape instead")
        ps2_num, xbox_num = int(chunk) + 1, 0
    else:
        _f, text, span = _locate(store, node)
        ps2_num, xbox_num = _node_numbers(text, span)
    done = {}
    for fname, xbox, num in ((store.ps2, False, ps2_num), (store.xbox, True, xbox_num)):
        if not fname or num <= 0:
            continue
        data = store.read(fname)
        out, _i = _set_chunk(data, num - 1, shell(verts, faces, default_pmt(data)), xbox)
        store.write(fname, out)
        done["xbox" if xbox else "ps2"] = num
    if not done:
        raise CollError("%s points at no collision chunk" % node)
    return dict(name=node, chunks=done)


def remove(store, node):
    """Node `node` taken out of its .bnds; its chunk (it keeps its number) made a bound that stops nothing."""
    f, text, span = _locate(store, node)
    nums = _node_numbers(text, span)
    a, b = span
    while b < len(text) and text[b] in "\r\n":
        b += 1
    text = _bump(text[:a] + text[b:], -1)
    store.write(f, text.encode("latin-1"))
    _empty_chunks(store, [nums])
    return dict(name=node, file=f)


# ---------------------------------------------------------------------------------------------------- undo

_UNDO = []          # [(folder, {name: bytes or None})], newest last


def remember(store):
    if store.kind == "folder" and store.before:
        _UNDO.append((store.folder, dict(store.before)))
        del _UNDO[:-50]


def undo_folder():
    """The last folder edit taken back. -> the folder, or None when there is nothing to undo."""
    if not _UNDO:
        return None
    folder, files = _UNDO.pop()
    for name, data in files.items():
        p = os.path.join(folder, name)
        if data is None:
            if os.path.exists(p):
                os.remove(p)
        else:
            with open(p, "wb") as f:
                f.write(data)
    return folder


def selftest(folder_copy, archive="LVLS_M2/MANTOMB2.DAT"):
    """add, replace, remove on a COPY of a level's mods folder; re-read each bundle's shell. -> notes."""
    _bw, _rw, R, B = _lib()
    st = FolderStore(folder_copy, level_stem(archive))
    box_v = [(0, 0, 0), (2, 0, 0), (2, 0, 2), (0, 0, 2), (0, 2, 0), (2, 2, 0), (2, 2, 2), (0, 2, 2)]
    box_f = [[0, 1, 2, 3], [4, 7, 6, 5], [0, 4, 5, 1], [1, 5, 6, 2], [2, 6, 7, 3], [3, 7, 4, 0]]
    r = add(st, box_v, box_f, "selftest")
    notes = ["added %s %s" % (r["name"], r["chunks"])]
    for key, fname in (("ps2", st.ps2), ("xbox", st.xbox)):
        if fname:
            ch = R.read_chunks(st.read(fname))
            b = B.read(ch[r["chunks"][key] - 1].data)
            notes.append("%s: %d faces %d verts" % (key, len(b.faces), len(b.vertices)))
    replace(st, r["name"], box_v[:4] + [(1, 1, 1)], [[0, 1, 4], [1, 2, 4], [2, 3, 4], [3, 0, 4]])
    notes.append("replaced")
    remove(st, r["name"])
    notes.append("removed")
    return notes
