"""Dave / DAVE archive container (Angel Studios / Rockstar San Diego).

Two magics exist on the RDR disc:
    "DAVE"  plain ASCII, NUL-terminated filenames   (STREAMS.DAT, BANKS.DAT)
    "Dave"  6-bit packed filenames with prefix dedup (everything else)

Header (16 bytes, padded to 0x800):
    0x00  char[4]  magic
    0x04  u32      entry count
    0x08  u32      info_size  (TOC block, 0x800-aligned)
    0x0C  u32      name_size  (name block, 0x800-aligned)

TOC at 0x800, 16 bytes per entry:
    0x00  u32  name_offset  (relative to 0x800 + info_size)
    0x04  u32  file_offset  (archive-relative)
    0x08  u32  size_full    (uncompressed)
    0x0C  u32  size_comp    (as stored)

size_comp == size_full  =>  stored raw
otherwise               =>  raw DEFLATE (zlib window -15)

THE ONE RULE: never repack. Patch bytes where they already sit. See
docs/MODDING_GUIDE.md section "why repacking is fatal".
"""

import struct
import threading
import zlib

SECTOR = 2048

# 6-bit alphabet for the "Dave" packed-name variant. Indices 49..55 are unused;
# 0x38..0x3F are prefix-dedup markers, not characters.
CHARS = "\x00 #$()-./?0123456789_abcdefghijklmnopqrstuvwxyz~\x7f"

# Measured over all 127,365 entries on the USA disc (scripts/verify_disc.py).
# 41.7% of files are sector-aligned overall.
#
# NOTE: alignment is a property of the individual FILE, not of its extension.
# Extensions below are 100% aligned on this disc, but several the old notes
# listed are not: .vsnd is 8/62, .plyr 50/64, .rain 5/6, .msd 520/521.
# This set is informational only -- patching in place preserves each file's
# original offset, so the DMA rule is satisfied automatically and for free.
SECTOR_ALIGNED_EXTS = {
    "bd", "bin", "guns", "ico", "ipu", "modb", "pck", "psi",
    "rep", "stm", "swt", "xls",
}

# 100% stored uncompressed on the disc. The engine streams these straight to
# hardware without checking the compression flag -- compressing one hangs the
# game. Entry-level state is preserved by patch() defaults regardless.
ALWAYS_RAW_EXTS = {"bd", "enemies", "iani", "movie", "pck", "stm"}


class SlotOverflow(Exception):
    """Replacement data does not fit in the gap the original file occupied."""

    def __init__(self, name, need, have, size_full=None, was_full=None):
        # Two different failures wear the same number, and telling a user to
        # "shorten the data" is wrong advice for one of them.
        #
        # Files in a Dave archive are stored DEFLATED and the slot is sized
        # for the compressed original, so a replacement can overflow while
        # being exactly the same length -- it just compresses worse. That is
        # what a tester hit: he matched a character's vertex count exactly and
        # still overflowed, because vanilla's position stream is full of
        # repeats (a model's 3,160 vertex slots hold far fewer distinct
        # positions) and a mesh authored elsewhere has no such redundancy.
        # Nothing about his file was too long, so "shorten the data" sent him
        # to delete vertices he had already matched.
        same_length = (size_full is not None and was_full is not None
                       and size_full <= was_full)
        if same_length:
            why = ("The data did NOT get longer -- %d bytes in, %d before -- "
                   "it just compresses worse than the original did. Vanilla "
                   "geometry repeats a lot of identical positions and that is "
                   "what made it fit. Deleting vertices may not help; making "
                   "the mesh more regular (shared/welded positions, fewer "
                   "unique values) will." % (size_full, was_full))
        else:
            why = "Shorten the data -- do NOT repack."
        super().__init__(
            "%s: needs %d bytes, slot is %d (over by %d). %s"
            % (name, need, have, need - have, why))
        self.name = name
        self.need = need
        self.have = have
        self.size_full = size_full
        self.was_full = was_full
        self.compresses_worse = same_length


class Entry:
    __slots__ = ("index", "name", "name_offset", "offset", "size_full",
                 "size_comp", "slot")

    def __init__(self, index, name, name_offset, offset, size_full, size_comp):
        self.index = index
        self.name = name
        self.name_offset = name_offset
        self.offset = offset
        self.size_full = size_full
        self.size_comp = size_comp
        self.slot = 0  # filled in after the TOC is sorted by offset

    @property
    def compressed(self):
        return self.size_comp != self.size_full

    @property
    def ext(self):
        base = self.name.rsplit("/", 1)[-1]
        return base.rsplit(".", 1)[-1].lower() if "." in base else ""

    @property
    def aligned(self):
        return self.offset % SECTOR == 0

    @property
    def headroom(self):
        return self.slot - self.size_comp

    def __repr__(self):
        return "<Entry %s @0x%X %d/%d slot=%d>" % (
            self.name, self.offset, self.size_comp, self.size_full, self.slot)


# --------------------------------------------------------------- name decode

def _decode_packed_names(block, count, offsets):
    """Decode "Dave" 6-bit packed filenames.

    Values stream out of 3-byte little-endian groups, four 6-bit values each.
    A leading value >= 0x38 means "share a prefix with the previous name":
    two values are consumed to form the shared-prefix length.
    """
    names = []
    prev = ""
    for i in range(count):
        pos = offsets[i]
        out = []
        vals: list[int] = []
        first = True

        while True:
            if not vals:
                if pos + 3 > len(block):
                    break
                trio = block[pos] | (block[pos + 1] << 8) | (block[pos + 2] << 16)
                pos += 3
                vals = [(trio >> s) & 0x3F for s in (0, 6, 12, 18)]

            v = vals.pop(0)

            if first:
                first = False
                if v >= 0x38:
                    if not vals:
                        if pos + 3 > len(block):
                            break
                        trio = (block[pos] | (block[pos + 1] << 8)
                                | (block[pos + 2] << 16))
                        pos += 3
                        vals = [(trio >> s) & 0x3F for s in (0, 6, 12, 18)]
                    v1 = vals.pop(0)
                    dedup = (v1 - 0x20) * 8 + v - 0x38
                    out.append(prev[:dedup])
                    continue

            if v == 0:
                break
            out.append(CHARS[v] if v < len(CHARS) else "?")

        prev = "".join(out)
        names.append(prev)
    return names


def encode_packed_name(name):
    """A filename as a literal (non-dedup) 6-bit packed name.

    The inverse of `_decode_packed_names` for the simple case: four 6-bit
    values per 3-byte little-endian group, NUL-terminated, no prefix sharing.
    Verified by re-encoding real entries -- PUPSHOW's entry 0
    `fac_brim01_barman_01.iani` reproduces its 21 stored bytes exactly.
    """
    name = name.lower()
    vals = []
    for ch in name:
        i = CHARS.find(ch)
        if i <= 0 or i >= 0x38:
            raise ValueError("%r cannot appear in a packed name; the alphabet "
                             "is %r" % (ch, CHARS[1:0x31]))
        vals.append(i)
    vals.append(0)                      # terminator
    while len(vals) % 4:
        vals.append(0)
    out = bytearray()
    for i in range(0, len(vals), 4):
        trio = (vals[i] | (vals[i + 1] << 6) | (vals[i + 2] << 12)
                | (vals[i + 3] << 18))
        out += bytes(((trio & 0xFF), (trio >> 8) & 0xFF, (trio >> 16) & 0xFF))
    return bytes(out)


MAX_SHARED_RUN = 31      # the shipped archives never chain more prefix-shared names than this


def encode_packed_name_shared(name, prev):
    """A packed name sharing its prefix with `prev` (the name before it in the
    table), as `_decode_packed_names` reads it: a first value 0x38 + n % 8 and a
    second 0x20 + n // 8 say "the first n characters of the previous name",
    then the rest. Shared from 2 characters up (the marker costs two values),
    at most 255. A name with nothing to share is written in full."""
    name = name.lower()
    n = 0
    for a, b in zip(name, prev):
        if a != b:
            break
        n += 1
    n = min(n, 255)
    if n < 2:
        return encode_packed_name(name)
    vals = [0x38 + n % 8, 0x20 + n // 8]
    for ch in name[n:]:
        i = CHARS.find(ch)
        if i <= 0 or i >= 0x38:
            raise ValueError("%r cannot appear in a packed name" % ch)
        vals.append(i)
    vals.append(0)
    while len(vals) % 4:
        vals.append(0)
    out = bytearray()
    for i in range(0, len(vals), 4):
        trio = (vals[i] | (vals[i + 1] << 6) | (vals[i + 2] << 12)
                | (vals[i + 3] << 18))
        out += bytes(((trio & 0xFF), (trio >> 8) & 0xFF, (trio >> 16) & 0xFF))
    return bytes(out)


def create(files, magic="Dave", align_exts=True):
    """Build a complete `Dave` archive from nothing. Returns bytes.

    `files` is {name: payload}. M21 needs this: `add()` puts an entry into an
    archive that already exists, and until now there was no way to make the
    archive. Nothing here patches anything -- it emits a fresh image, so THE
    ONE RULE about never repacking does not apply. It is the one case where
    laying bytes out from scratch is correct.

    **Entries are sorted, and that is load-bearing, not tidiness.** The engine
    resolves a name by BINARY-SEARCHING the TOC (see `_sort_key`: bsearch at
    0x0025EEB8, comparator 0x002CA9D8, three callers). An unsorted TOC does
    not merely fail to find one entry, it can miss arbitrary others. All 66
    shipped archives are sorted under that comparator, 66 of 66, and an
    archive this function emits must be too or the game will lose files that
    are physically present.

    Layout, matching `_parse` exactly:

        0x000  magic, count, info_size, name_size   padded to SECTOR
        0x800  TOC, 16 bytes per entry              padded to info_size
        +info  packed name block                    padded to name_size
        +name  file data, each payload sector-padded

    A payload is DEFLATEd (raw, window -15) and stored compressed only when
    that is actually smaller; otherwise it is stored raw and `size_comp ==
    size_full`, which is the flag `_parse` reads. Extensions the disc always
    stores sector-aligned are honoured when `align_exts` is set, because the
    DMA rule that produced them is a property of the engine, not of any one
    archive.
    """
    if magic != "Dave":
        raise ValueError("only the packed-name 'Dave' variant can be built; "
                         "DAVE needs an ASCII name encoder (C-002)")
    if not files:
        raise ValueError("an archive with no entries has no valid TOC")

    # Sorted under the ENGINE's collation, not Python's.
    def key(nm):
        return [CHARS.find(c) for c in nm.lower()]
    names = sorted(files, key=key)

    # Names share their prefix with the previous name, as the shipped archives
    # do. Written in full, swat.dat's name table came out 907 KB against the
    # original's 473 KB -- and the game holds an archive's whole table in
    # memory, so the PS2 game went to a black screen at boot. Shared: 477 KB.
    #
    # And never more than 31 SHARED NAMES IN A ROW: the shipped archives hold
    # to that in all 118,694 entries on the disc (the longest run is 31), so a
    # name is always found within 31 steps of one written in full. Unbounded,
    # swat.dat chained 7,483 names and every level took minutes to load.
    name_block = bytearray()
    name_offsets = []
    prev = ""
    run = 0
    for nm in names:
        name_offsets.append(len(name_block))
        enc = encode_packed_name_shared(nm, prev) if run < MAX_SHARED_RUN else encode_packed_name(nm)
        run = run + 1 if (enc[0] & 0x3F) >= 0x38 else 0
        name_block += enc
        prev = nm.lower()

    info_size = _align(len(names) * 16, SECTOR)
    name_size = _align(len(name_block), SECTOR)
    data_start = SECTOR + info_size + name_size

    toc = bytearray()
    blob = bytearray()
    for i, nm in enumerate(names):
        raw = bytes(files[nm])
        ext = nm.rsplit(".", 1)[-1].lower() if "." in nm else ""
        store_raw = align_exts and ext in SECTOR_ALIGNED_EXTS
        payload = raw
        if not store_raw:
            c = zlib.compressobj(9, zlib.DEFLATED, -15)
            packed = c.compress(raw) + c.flush()
            if len(packed) < len(raw):
                payload = packed
        size_comp = len(payload)
        offset = data_start + len(blob)
        toc += struct.pack("<IIII", name_offsets[i], offset, len(raw), size_comp)
        blob += payload + b"\x00" * (_align(len(payload), SECTOR) - len(payload))

    head = struct.pack("<4sIII", magic.encode("ascii"), len(names),
                       info_size, name_size)
    out = bytearray(head)
    out += b"\x00" * (SECTOR - len(out))
    out += toc + b"\x00" * (info_size - len(toc))
    out += name_block + b"\x00" * (name_size - len(name_block))
    out += blob
    return bytes(out)


def _align(n, a):
    return (n + a - 1) // a * a


def _packed_name_end(block, start):
    """Where the packed name beginning at `start` finishes.

    Used to find the name block's high-water mark: names are stored
    back-to-front, so the LOWEST-indexed entry's name ends last.
    """
    pos = start
    first = True
    while pos + 3 <= len(block):
        trio = block[pos] | (block[pos + 1] << 8) | (block[pos + 2] << 16)
        pos += 3
        for shift in (0, 6, 12, 18):
            v = (trio >> shift) & 0x3F
            if first:
                first = False
                if v >= 0x38:
                    continue       # dedup marker consumes the next value too
            if v == 0:
                return pos
    return len(block)


def _decode_ascii_names(block, count, offsets):
    names = []
    for i in range(count):
        pos = offsets[i]
        end = block.find(b"\x00", pos)
        if end < 0:
            end = len(block)
        names.append(block[pos:end].decode("ascii", "replace").lower())
    return names


# ------------------------------------------------------------------- backing

class FileBacking:
    """Backing store for a Dave archive that lives as a loose file on disk."""

    def __init__(self, path, writable=False):
        self.path = str(path)
        self._f = open(self.path, "r+b" if writable else "rb")
        self._f.seek(0, 2)
        self.size = self._f.tell()
        self.writable = writable
        # One cursor, several server threads -- same hazard as Iso9660, same
        # lock. IsoBacking needs none of its own: it delegates to Iso.read_at.
        self._io = threading.Lock()

    def read_at(self, offset, length):
        with self._io:
            self._f.seek(offset)
            return self._f.read(length)

    def write_at(self, offset, data):
        if not self.writable:
            raise IOError("read-only backing")
        with self._io:
            self._f.seek(offset)
            self._f.write(data)

    def flush(self):
        with self._io:
            self._f.flush()

    def close(self):
        self._f.close()


class IsoBacking:
    """Backing store for a Dave archive that lives inside an ISO."""

    def __init__(self, iso, path):
        self.iso = iso
        entry = iso.get(path)
        self.base = entry.offset
        self.size = entry.size
        self.writable = iso.writable

    def read_at(self, offset, length):
        return self.iso.read_at(self.base + offset, length)

    def write_at(self, offset, data):
        self.iso.write_at(self.base + offset, data)

    def flush(self):
        self.iso.flush()

    def close(self):
        pass


# --------------------------------------------------------------------- Dave

# Repacking -- moving every file to close the slack between them -- is what
# turns a stock archive's ~2 KB largest free run into hundreds of KB, so it is
# the difference between editing content and adding it. It is NOT universally
# safe, and the split is empirical, not reasoned: each of these was cleared by
# repacking it alone and then driving the game past its title screen, which
# takes about 100 seconds of intro before the menu even appears.
#
# SWAT.DAT is the one that needs a condition attached, and the reason is known:
# a FLAT repack of it freezes at the title screen -- six screenshots over 76
# seconds byte-identical, "Press START button" never appearing, while every
# offline check still passes, so nothing but running it can see this. The cause
# was found by bisection and is `logo01.pck`, which must stay on a 2048-byte
# sector; see SECTOR_PINNED below. With that one file pinned the same repack
# plays and reclaims 12.95 MB, which is why `repack_status` answers
# "needs-pinning" rather than "breaks-the-game". Do not read this paragraph as
# "SWAT.DAT cannot be repacked" -- it was read that way for weeks, and it was
# wrong (05_Docs/CAPABILITIES.md C-005).
REPACK_VERIFIED = {"FRONT.DAT", "STREAMS.DAT", "BANKS.DAT", "PUPSHOW.DAT"}
REPACK_BROKEN: set[str] = set()

# Files that must start on a 2048-byte sector, presumably because they are
# DMA'd straight off the disc rather than read through the archive's own file
# path. Move one of these off a sector boundary -- 624 bytes is enough -- and
# the game freezes at the title screen, while every file still reads back
# byte-identically through the TOC, because the TOC was never the route.
#
# SWAT.DAT was the one archive that could not be repacked at all. With
# logo01.pck pinned, the same flat-16 repack that used to freeze plays fine
# and reclaims 12.95 MB. Found by bisecting 47,101 blocks over 17 emulator
# runs, then confirmed by moving that single file and nothing else.
#
# This list is almost certainly incomplete: the bisect returns the HIGHEST
# offending block, so any sector-sensitive file below index 46955 in SWAT.DAT
# is still unknown. Treat it as "the ones found so far".
SECTOR_PINNED = {"logo01.pck"}


def repack_status(label):
    """"verified", "needs-pinning", "breaks-the-game" or "untested".

    Untested is deliberately its own answer. Treating it as "safe" is how the
    all-archives repack got called proven, and treating it as "unsafe" would
    re-create the blanket ban that was wrong in the first place.

    "needs-pinning" means the archive repacks only if the files in
    SECTOR_PINNED are kept on sector boundaries -- see scripts/
    test_repack_pinned.py.
    """
    base = str(label or "").replace("\\", "/").split("/")[-1].upper()
    if base in REPACK_BROKEN:
        return "breaks-the-game"
    if base in REPACK_VERIFIED:
        return "verified"
    if base == "SWAT.DAT":
        return "needs-pinning"
    return "untested"


class Dave:
    # Files queued for a rebuild ("Rebuild and play", rdrstudio/pendingrebuild):
    # name -> the new bytes, read back as if they were on the disc. Set by the
    # Studio's Game on the working disc only; a write that lands in place takes
    # the file out of the queue (on_overlay_drop is told).
    overlay = None
    on_overlay_drop = None

    def __init__(self, backing, label=""):
        self.backing = backing
        self.label = label
        self.size = backing.size
        self._parse()

    def _parse(self):
        head = self.backing.read_at(0, 16)
        self.magic = head[:4].decode("ascii", "replace")
        if self.magic not in ("Dave", "DAVE"):
            raise ValueError("%s: bad magic %r" % (self.label, head[:4]))
        count, info_size, name_size = struct.unpack_from("<III", head, 4)
        self.count = count
        self.info_size = info_size
        self.name_size = name_size

        toc_off = SECTOR
        toc = self.backing.read_at(toc_off, count * 16)
        names_off = SECTOR + info_size
        name_block = self.backing.read_at(names_off, name_size)

        raw = [struct.unpack_from("<IIII", toc, i * 16) for i in range(count)]
        name_offsets = [r[0] for r in raw]

        if self.magic == "Dave":
            names = _decode_packed_names(name_block, count, name_offsets)
        else:
            names = _decode_ascii_names(name_block, count, name_offsets)

        self.entries = [
            Entry(i, names[i], raw[i][0], raw[i][1], raw[i][2], raw[i][3])
            for i in range(count)
        ]
        self._compute_slots()
        self._by_name: dict[str, Entry] = {}
        for e in self.entries:
            self._by_name.setdefault(e.name, e)

    def _compute_slots(self):
        """slot = bytes available before the next file begins.

        Duplicate offsets happen (identical files are stored once), so we walk
        the distinct sorted offsets rather than adjacent entries.
        """
        offsets = sorted({e.offset for e in self.entries})
        nxt = {}
        for i, off in enumerate(offsets):
            nxt[off] = offsets[i + 1] if i + 1 < len(offsets) else self.size
        for e in self.entries:
            e.slot = nxt[e.offset] - e.offset

    # ------------------------------------------------------------------ read

    def __len__(self):
        return len(self.entries)

    def __iter__(self):
        return iter(self.entries)

    def find(self, name):
        e = self._by_name.get(name.lower())
        if e is None:
            raise KeyError("%s: no such file in %s" % (name, self.label))
        return e

    def raw(self, name_or_entry):
        e = self._resolve(name_or_entry)
        return self.backing.read_at(e.offset, e.size_comp)

    def read(self, name_or_entry):
        """Decompressed contents."""
        e = self._resolve(name_or_entry)
        if self.overlay and e.name in self.overlay:
            return self.overlay[e.name]
        data = self.backing.read_at(e.offset, e.size_comp)
        if not e.compressed:
            return data
        return zlib.decompress(data, -15)

    def _resolve(self, x):
        return x if isinstance(x, Entry) else self.find(x)

    # ----------------------------------------------------------------- write

    def _stored_payload(self, entry, new_data, keep_raw=None):
        """What this file would occupy in its slot: (payload, keep_raw).

        Factored out of `patch` so a PLAN can ask the question without writing
        anything, and so there is exactly one answer to it. A second copy of
        this rule is how a planner and a writer come to disagree about whether
        an edit fits -- and they disagree silently, because both look right.
        """
        if keep_raw is None:
            keep_raw = not entry.compressed
        if keep_raw:
            return new_data, True
        co = zlib.compressobj(9, zlib.DEFLATED, -15)
        payload = co.compress(new_data) + co.flush()
        # If "compression" grew it, store raw instead.
        if len(payload) >= len(new_data):
            return new_data, True
        return payload, False

    def would_fit(self, name_or_entry, new_data, keep_raw=None):
        """Would this content fit the file's existing slot? Never writes.

        Returns the stored size the write would produce and the slot it has to
        land in. Note `stored` is not `len(new_data)`: these entries are
        deflated, so a SAME-LENGTH edit can still change the stored size -- 24
        `.rep` and 3 `.hot` on the disc do exactly that (C-030). Asking this
        about the uncompressed length instead is the mistake the question
        exists to prevent.
        """
        e = self._resolve(name_or_entry)
        payload, raw = self._stored_payload(e, new_data, keep_raw)
        return dict(file=e.name, stored=len(payload), slot=e.slot,
                    fits=len(payload) <= e.slot, raw=raw,
                    headroom=e.slot - len(payload),
                    bytes_before=e.size_full, bytes_after=len(new_data))

    def patch(self, name_or_entry, new_data, keep_raw=None, flush=True,
              grow=False):
        """Replace a file's contents in place.

        Recompresses at level 9 (which beats the 2004 packer, so edits usually
        shrink). Raises SlotOverflow rather than moving anything.

        keep_raw: force stored-uncompressed. Defaults to matching what the
        vanilla entry did -- files the engine DMAs straight to hardware must
        never become compressed.

        flush: fsync the backing before returning. On by default, because a
        single edit should be durable the moment it reports success. Bulk
        callers must pass False and flush ONCE at the end: for an ISO backing
        this is `os.fsync` on a 4 GB file, and doing it per file made loading a
        638-file mod take over five minutes and time out.
        """
        e = self._resolve(name_or_entry)
        payload, keep_raw = self._stored_payload(e, new_data, keep_raw)

        if len(payload) > e.slot:
            if not grow:
                # Hand over both LENGTHS as well as both compressed sizes, so
                # the message can tell "your data got bigger" apart from "your
                # data compresses worse", which need opposite advice.
                raise SlotOverflow(e.name, len(payload), e.slot,
                                   size_full=len(new_data), was_full=e.size_full)
            return self._relocate(e, new_data, payload, keep_raw, flush)

        # Data, then zero the rest of the slot so no stale bytes survive.
        self.backing.write_at(e.offset, payload)
        pad = e.slot - len(payload)
        if pad:
            self.backing.write_at(e.offset + len(payload), b"\x00" * pad)

        # Only the two size fields of this one TOC entry change.
        e.size_full = len(new_data)
        e.size_comp = len(new_data) if keep_raw else len(payload)
        self.backing.write_at(
            SECTOR + e.index * 16 + 8,
            struct.pack("<II", e.size_full, e.size_comp),
        )
        if flush:
            self.backing.flush()
        self._landed(e)
        return len(payload), e.slot

    def _landed(self, e):
        """The file is on the disc now: no longer waiting for a rebuild."""
        if self.overlay and e.name in self.overlay:
            if self.on_overlay_drop:
                self.on_overlay_drop(self.label, e.name)
            else:
                self.overlay.pop(e.name, None)

    def _relocate(self, e, new_data, payload, keep_raw, flush):
        """Grow a file by moving its payload into the largest free gap.

        The one demonstrated way an in-archive move freezes the game is
        alignment: logo01.pck must start on a 2048-byte sector. Every file
        keeps its own alignment class -- a sector-aligned original lands on a
        sector boundary, everything else on 16 -- so a file with an
        undiscovered alignment need keeps it satisfied.

        The vacated slot is zeroed, the full TOC record (offset + both sizes)
        is rewritten, and every entry's slot is recomputed: the move changes
        the neighbour's slot too.
        """
        # Only the one file with DEMONSTRATED sector sensitivity keeps its
        # 2048 alignment as a hard requirement. Everything else prefers its
        # original alignment class but falls back to 16 when the gap is too
        # small for a sector boundary -- every level archive's free gap is
        # ~2,030 bytes (measured, all 61), so a hard 2048 rule would have
        # made growth impossible exactly where it is wanted. The 16-byte
        # fallback for a formerly sector-aligned file is what the grow boot
        # test exercises.
        gap_off, gap_len = self.largest_gap()
        aligns = [16]
        if e.offset % SECTOR == 0:
            aligns.insert(0, SECTOR)
        if e.name.lower() in SECTOR_PINNED:
            aligns = [SECTOR]
        start = avail = None
        for align in aligns:
            st = ((gap_off + align - 1) // align) * align
            av = gap_off + gap_len - st
            if av >= len(payload):
                start, avail = st, av
                break
        if start is None:
            raise SlotOverflow(
                e.name, len(payload), max(0, gap_len),
                size_full=len(new_data), was_full=e.size_full)
        self.backing.write_at(start, payload)
        # No stale copy of the file survives at the old address.
        self.backing.write_at(e.offset, b"\x00" * e.slot)
        e.offset = start
        e.size_full = len(new_data)
        e.size_comp = len(new_data) if keep_raw else len(payload)
        self.backing.write_at(
            SECTOR + e.index * 16 + 4,
            struct.pack("<III", e.offset, e.size_full, e.size_comp),
        )
        self._compute_slots()
        if flush:
            self.backing.flush()
        self._landed(e)
        return len(payload), e.slot

    def edit(self, name_or_entry, fn, **kw):
        """read -> fn(bytes) -> patch."""
        e = self._resolve(name_or_entry)
        return self.patch(e, fn(self.read(e)), **kw)

    # ------------------------------------------------------- growing the TOC

    def spare_entries(self):
        """How many more TOC entries fit without moving any file data.

        `info_size` is `ceil(count*16 / 2048) * 2048` in all 66 archives, so
        the tail of the TOC block is alignment padding rather than designed
        headroom -- but it is genuinely unused, and writing into it moves
        nothing.
        """
        return (self.info_size - self.count * 16) // 16

    def name_used(self):
        """Bytes of the name block actually occupied.

        STOCK archives write packed names back-to-front -- `name_offset`
        decreases as the index rises -- so entry 0's encoded name ends at the
        high-water mark. But `add()` appends its name AT that mark, for
        whatever index the sorted insert lands on, so after the first add the
        high-water name no longer belongs to entry 0. Taking entry 0's end
        here made the SECOND add to an archive reuse the first add's name
        offset and overwrite its bytes: two TOC records decoding to one name,
        caught only by add()'s post-insert verification, after the bytes were
        already down. The mark is the max over every entry -- identical to
        entry 0's end on a stock archive, and correct after any number of
        adds.
        """
        block = self.backing.read_at(SECTOR + self.info_size, self.name_size)
        if self.magic != "Dave":
            end = 0
            for e in self.entries:
                stop = block.find(b"\x00", e.name_offset)
                end = max(end, (len(block) if stop < 0 else stop) + 1)
            return end
        return max(_packed_name_end(block, e.name_offset)
                   for e in self.entries)

    def spare_name_bytes(self):
        return self.name_size - self.name_used()

    def largest_gap(self):
        """(offset, length) of the biggest unused run between stored files."""
        spans = sorted((e.offset, e.offset + e.size_comp) for e in self.entries)
        best = (0, 0)
        cursor = SECTOR + self.info_size + self.name_size
        for start, end in spans:
            if start - cursor > best[1]:
                best = (cursor, start - cursor)
            cursor = max(cursor, end)
        if self.size - cursor > best[1]:
            best = (cursor, self.size - cursor)
        return best

    def can_add(self, name, size):
        """Why a new entry would or would not fit. Returns a dict, never raises.

        Every check here exists because failing it would require *moving file
        data*, which is the thing that actually breaks this format.
        """
        why = []
        if self.magic != "Dave":
            why.append("only the 'Dave' packed-name variant is supported")
        if self.spare_entries() < 1:
            why.append("no spare TOC slot (%d bytes of padding)"
                       % (self.info_size - self.count * 16))
        encoded = b""
        try:
            encoded = encode_packed_name(name)
        except ValueError as ex:
            why.append(str(ex))
        if encoded and len(encoded) > self.spare_name_bytes():
            why.append("name needs %d bytes, only %d spare in the name block"
                       % (len(encoded), self.spare_name_bytes()))
        if any(e.name == name.lower() for e in self.entries):
            why.append("an entry named %r already exists" % name.lower())
        off, room = self.largest_gap()
        if room < size:
            why.append("largest free run between files is %d bytes, need %d"
                       % (room, size))
        return dict(ok=not why, reasons=why,
                    encoded_name_bytes=len(encoded),
                    spare_entries=self.spare_entries(),
                    spare_name_bytes=self.spare_name_bytes(),
                    payload_offset=off, payload_room=room,
                    repack_status=repack_status(self.label))

    def _sort_key(self, name):
        """The TOC's own collation: position in the 6-bit packing alphabet.

        THE GAME'S COMPARATOR IS NOW KNOWN, and this note used to say it could
        not be proven from the disc alone. It can, from the executable:
        `SLUS_205.00` resolves a file in an archive by **binary-searching this
        TOC**, 16-byte records, with a case-insensitive `strcmp` that also
        folds `\\` to `/` (`0x002CB908` -> bsearch `0x0025EEB8`, comparator
        `0x002CAA50` -> `0x002CA9D8`). That wrapper has three callers and only
        one of them is the audio path, so it is the generic lookup and not a
        streams special case. See `scripts/probe_stream_lookup.py`, C-010.

        **So sortedness is not a tidiness convention, it is a load-bearing
        invariant**: bsearch on an out-of-order array does not merely fail to
        find the moved entry, it can miss arbitrary others. All 66 archives
        are sorted under the engine's comparator -- checked, 66 of 66 -- and
        anything that reorders the TOC (`add`, `rename`) must keep them that
        way.

        This key stays the 6-bit alphabet rather than the engine's byte
        compare: the two orders differ only at `?`, which no real name uses,
        and any name that would order differently is refused by the alphabet
        check in `encode_packed_name` long before it reaches here.
        """
        return [CHARS.find(c) for c in name.lower()]

    def insertion_index(self, name):
        """Where a new name belongs in the name-sorted TOC."""
        key = self._sort_key(name)
        lo = 0
        for i, e in enumerate(self.entries):
            if self._sort_key(e.name) < key:
                lo = i + 1
        return lo

    def _stored_name_dedups(self, block, offset):
        """Does the name stored at `offset` open with a prefix-share marker?"""
        if offset + 3 > len(block):
            return False
        trio = block[offset] | (block[offset + 1] << 8) | (block[offset + 2] << 16)
        return (trio & 0x3F) >= 0x38

    def can_rename(self, name, new_name):
        """Why `name` can or cannot become `new_name` here. Never raises."""
        why = []
        want = str(new_name or "").strip().lower()
        try:
            entry = self.find(name)
        except Exception:
            entry = None
        if entry is None:
            why.append("%s is not in this archive" % name)
        if not want:
            why.append("a new name cannot be empty")
        encoded = b""
        if want:
            if any(e.name.lower() == want for e in self.entries
                   if entry is None or e.index != entry.index):
                why.append("%s is already in this archive" % want)
            try:
                encoded = encode_packed_name(want)
            except ValueError as ex:
                why.append(str(ex))
        # Up to three names may have to be rewritten as literals: the renamed
        # entry, and whichever entries lose their prefix base when the record
        # moves. Cost them all before writing any of them.
        need = len(encoded) * 3 if encoded else 0
        spare = self.spare_name_bytes()
        if encoded and need > spare:
            why.append("the name block has %d spare bytes and this needs up "
                       "to %d" % (spare, need))
        return dict(ok=not why, reasons=why, file=want,
                    spare_name_bytes=spare, needs_up_to=need)

    def rename(self, name, new_name, dry=False):
        """Change an entry's NAME. No file data moves, not one byte.

        A rename is cheaper than an `add` -- no payload is copied and every
        file keeps its offset -- and harder, for two reasons that are both
        invisible in the format's own documentation:

        1. **The TOC must stay sorted.** The game finds a file by binary
           search over these records (see `_sort_key`), so a renamed entry
           left at its old index does not merely become unfindable itself, it
           can hide arbitrary others. The record therefore MOVES to the new
           name's sorted position, exactly as `add` inserts at one.

        2. **Names share prefixes with the entry before them.** A stored name
           may open with "copy the first L characters of the PREVIOUS name",
           and 89.4% of `.tbl` entries are followed by a name that does. Both
           the hole the record leaves and the place it lands change somebody's
           predecessor, so any neighbour that was deduping is rewritten as a
           literal at its own fresh offset first.

        There is no `remove()` in this format and none is invented here: the
        old name's bytes stay in the name block as dead space, so renames are
        finite. `can_rename` reports the spare.

        Verified the way `add` is -- the whole TOC is re-decoded afterwards
        and compared name for name, plus a sortedness check, and anything
        unexpected raises rather than leaving a plausible-looking archive.

        Boot-test after using this, every time.
        """
        check = self.can_rename(name, new_name)
        if not check["ok"]:
            raise ValueError("cannot rename %r: %s"
                             % (name, "; ".join(check["reasons"])))
        want = check["file"]
        entry = self.find(name)
        i = entry.index
        before = [e.name for e in self.entries]

        # Where the record lands once it is no longer where it was.
        others = [e for e in self.entries if e.index != i]
        key = self._sort_key(want)
        j = 0
        for e in others:
            if self._sort_key(e.name) < key:
                j += 1

        order = [e.index for e in others]
        order.insert(j, i)
        names_after = [want if k == i else before[k] for k in order]
        if dry:
            return dict(check, index_from=i, index_to=j,
                        moves=sum(1 for a, b in zip(order, range(len(order)))
                                  if a != b))

        block = self.backing.read_at(SECTOR + self.info_size, self.name_size)
        records = {e.index: self.backing.read_at(SECTOR + e.index * 16, 16)
                   for e in self.entries}

        # Who needs a literal: the renamed entry always, plus any entry whose
        # predecessor is not the one it was encoded against.
        cursor = self.name_used()
        writes = []

        def literal_for(idx, text):
            nonlocal cursor
            enc = encode_packed_name(text)
            at = cursor
            cursor += len(enc)
            writes.append((at, enc))
            return at

        new_offsets = {i: literal_for(i, want)}
        for pos, idx in enumerate(order):
            if idx == i:
                continue
            was_prev = before[idx - 1] if idx > 0 else None
            now_prev = names_after[pos - 1] if pos > 0 else None
            if was_prev == now_prev:
                continue
            off = struct.unpack_from("<I", records[idx], 0)[0]
            if self._stored_name_dedups(block, off):
                new_offsets[idx] = literal_for(idx, before[idx])

        if cursor - self.name_used() > self.spare_name_bytes():
            raise ValueError("cannot rename %r: the name block ran out of "
                             "room (%d spare, %d needed)"
                             % (name, self.spare_name_bytes(),
                                cursor - self.name_used()))

        for at, enc in writes:
            self.backing.write_at(SECTOR + self.info_size + at, enc)
        for pos, idx in enumerate(order):
            rec = records[idx]
            if idx in new_offsets:
                rec = struct.pack("<I", new_offsets[idx]) + rec[4:]
            self.backing.write_at(SECTOR + pos * 16, rec)
        self.backing.flush()

        self._parse()
        after = [e.name for e in self.entries]
        if after != names_after:
            bad = next((k for k, (a, b) in enumerate(zip(after, names_after))
                        if a != b), len(after))
            raise RuntimeError(
                "post-rename verification failed: the re-decoded TOC does not "
                "match at index %d (%r, expected %r). The archive in the "
                "WORKING copy is now suspect -- restore it."
                % (bad, after[bad] if bad < len(after) else "?",
                   names_after[bad] if bad < len(names_after) else "?"))
        keys = [self._sort_key(n) for n in after]
        if any(keys[k] > keys[k + 1] for k in range(len(keys) - 1)):
            raise RuntimeError(
                "post-rename verification failed: the TOC is no longer sorted, "
                "and the game binary-searches it. Restore the working copy.")
        return dict(check, renamed=name.lower(), to=want,
                    index_from=i, index_to=j, literals=len(writes),
                    name_bytes_used=cursor - (self.name_used() - 0))

    def add(self, name, data, dry=False, compress=True):
        """Add a NEW file entry, without moving a single byte of file data.

        EXPERIMENTAL, and deliberately narrow. The archive's own structures
        say this is safe -- there is no checksum anywhere (header bytes
        0x10..0x7FF are zero in all 66 archives), `info_size` and `name_size`
        stay put, and every existing file keeps its exact offset -- but the
        game's loader has never been read: SLUS_205.00 is a *packed*
        executable (entropy 7.9999 bits/byte), so "nothing looks files up by
        index" is an untested assumption, only an assumption that has
        survived every boot test so far.

        The entry goes in at its CORRECT SORTED POSITION, under the 6-bit
        alphabet's own collation, which is what lets the name be anything the
        alphabet can spell -- the old index-0 '#'-prefix restriction was just
        the special case where the sorted position happens to be first.
        Every entry after the insertion point shifts by one index; the
        '#'-test already shifted ALL of them and booted to the menu, so index
        stability is the assumption least in doubt.

        The subtle part is prefix dedup. A stored name may open with a marker
        meaning "copy the first L characters of the PREVIOUS entry's name".
        Inserting N between entries A and B changes B's previous name from A
        to N -- but if A and B share an L-prefix and A <= N <= B in sort
        order, N necessarily shares that same L-prefix (a string ordered
        between two strings with a common prefix carries that prefix), so B
        still decodes to exactly the same characters. That argument is done
        in maths above and re-done in bytes below: after every insertion the
        whole TOC is re-decoded and compared name-for-name against what it
        held before, plus the newcomer. If the reasoning ever meets a case it
        missed, the add reports failure instead of leaving a wrong name.

        Boot-test after using this, every time.
        """
        # Store compressed when it helps, exactly as the game's own packer
        # did: raw DEFLATE, window -15, and size_comp < size_full is the
        # stored-compressed signal. Level 9 beats the 2004 packer, which is
        # the whole reason edits usually shrink. A payload the deflate does
        # not shrink is stored raw (size_comp == size_full), same as stock.
        stored = data
        if compress:
            z = zlib.compressobj(9, zlib.DEFLATED, -15)
            packed = z.compress(data) + z.flush()
            if len(packed) < len(data):
                stored = packed

        check = self.can_add(name, len(stored))
        if not check["ok"]:
            raise ValueError("cannot add %r: %s"
                             % (name, "; ".join(check["reasons"])))
        k = self.insertion_index(name)
        if dry:
            return dict(check, index=k, stored_bytes=len(stored),
                        compressed=stored is not data)

        before = [e.name for e in self.entries]
        encoded = encode_packed_name(name)
        name_at = self.name_used()
        payload_at = check["payload_offset"]

        # Slide the TOC tail down one record and write the newcomer at k.
        tail = self.backing.read_at(SECTOR + k * 16, (self.count - k) * 16)
        self.backing.write_at(SECTOR + (k + 1) * 16, tail)
        self.backing.write_at(SECTOR + k * 16, struct.pack(
            "<IIII", name_at, payload_at, len(data), len(stored)))
        self.backing.write_at(SECTOR + self.info_size + name_at, encoded)
        self.backing.write_at(payload_at, stored)
        self.backing.write_at(4, struct.pack("<I", self.count + 1))
        self.backing.flush()

        self._parse()
        after = [e.name for e in self.entries]
        expect = before[:k] + [name.lower()] + before[k:]
        if after != expect:
            raise RuntimeError(
                "post-insert verification failed: the re-decoded TOC does not "
                "match (first divergence at index %d: %r vs expected %r). The "
                "archive in the WORKING copy is now suspect -- restore it."
                % (next(i for i, (a, b) in enumerate(zip(after, expect))
                        if a != b),
                   after[k] if k < len(after) else "?",
                   expect[k] if k < len(expect) else "?"))
        return dict(check, added=name.lower(), index=k, offset=payload_at,
                    count=self.count)
