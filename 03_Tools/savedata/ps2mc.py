"""PS2 memory card filesystem -- enough of it to get a save out and back in.

The developer's Red Dead Revolver save is not a file on disk. It is a directory named
`BASLUS-20500RDR` inside an 8 MB PS2 memory card image,
`<PCSX2 folder>\\memcards\\Mcd001.ps2`, and reaching it means implementing
the card's own filesystem. This module is that filesystem, and nothing above it:
it knows about superblocks, clusters, FAT chains and directory entries. It does
not know what a dollar is. That lives in `rdrsave.py`.

LAYOUT, as measured on his card (`scripts/verify_save.py` re-measures it):

    8,650,752 bytes = 16,384 physical pages of 528
    528 = 512 data + 16 spare            <- the card HAS ECC
    cluster = 2 pages = 1024 data bytes
    block   = 16 pages
    8,192 clusters, alloc_offset 41, alloc_end 8,135, rootdir at cluster 0

A card without ECC (16,384 x 512) exists in the wild and `Card` reads it -- the
spare is sized from the file length, not assumed.

ECC, AND WHY IT IS DERIVED HERE RATHER THAN CITED
-------------------------------------------------
Each 512-byte page carries four 3-byte ECC codes, one per 128-byte chunk, then
four bytes this module does not compute. The column-parity byte is **7 bits**:
the published mask table produces an 8-bit value and the card stores it masked
with 0x7f. That one bit was not guessed -- `Column_parity_masks` below was
checked against every chunk on the real card that stores an ECC at all, and
1,215 of 1,216 agree. See `MASK_IS_7_BIT` and `PAGE_1_IS_STALE`.

THE LAST FOUR SPARE BYTES ARE NOT OURS. On his card 16,080 pages carry
`ff ff ff ff` there and 304 carry `00 00 00 00`. Nothing here knows which a
page should have, so `write_page` copies the old four through untouched. A
writer that normalised them would rewrite 16,080 pages on a no-op save.

WHAT MAKES THE ROUND TRIP BYTE-EXACT. This module never rebuilds a card. It
edits the pages a change actually lands on and copies every other byte from the
image it read. That is not laziness about the format -- it is the only way to
survive the two places the card disagrees with its own rules (page 1, and the
64,463 erased chunks whose spare is `ff` rather than a computed code). A
rebuilding writer would "correct" both and hand back a different file.
"""

import os
import struct

MAGIC = b"Sony PS2 Memory Card Format "

# Directory-entry mode bits.
DF_READ = 0x0001
DF_WRITE = 0x0002
DF_EXECUTE = 0x0004
DF_PROTECTED = 0x0008
DF_FILE = 0x0010
DF_DIRECTORY = 0x0020
DF_EXISTS = 0x8000

ENTRY_LEN = 512

# The chunk is 128 bytes and each input bit contributes a fixed 8-bit mask.
# mask[i] == ((8 + i) << 4) | (7 - i).
_MASKS = [((8 + i) << 4) | (7 - i) for i in range(8)]

# 7, not 8. See the module docstring -- the card stores the column parity
# masked with 0x7f and the difference is invisible on an all-zero page, which
# is most of a fresh card.
MASK_IS_7_BIT = 0x7F

# Page 1 of his card holds an 8-byte ident followed by 0xff fill, and its
# stored ECC is the all-zero code -- it was written without refreshing the
# spare. It is the only live chunk on the card that fails its own ECC. Nothing
# here repairs it; `write_page` is never called on a page an edit did not
# touch, so it survives a round trip unchanged.
PAGE_1_IS_STALE = True

_PARITY = [bin(b).count("1") & 1 for b in range(256)]
_COLUMN = []
for _b in range(256):
    _m = 0
    for _i in range(8):
        if _b >> _i & 1:
            _m ^= _MASKS[_i]
    _COLUMN.append(_m)


class CardError(Exception):
    """The image is not a card we can read, or a path in it does not exist."""


def ecc(chunk):
    """The 3-byte ECC for one 128-byte chunk."""
    col = 0x77
    line0 = 0x7F
    line1 = 0x7F
    for i, b in enumerate(chunk):
        col ^= _COLUMN[b]
        if _PARITY[b]:
            line0 ^= ~i
            line1 ^= i
    return bytes([col & MASK_IS_7_BIT, line0 & 0x7F, line1 & 0x7F])


def page_ecc(page):
    """The 12 ECC bytes for a 512-byte page, in stored order."""
    out = bytearray()
    for c in range(4):
        out += ecc(page[c * 128:(c + 1) * 128])
    return bytes(out)


class Entry(object):
    """One 512-byte directory entry, plus where it was read from."""

    def __init__(self, raw, cluster, index):
        self.raw = raw
        self.dir_cluster = cluster
        self.index = index
        self.mode = struct.unpack_from("<H", raw, 0x00)[0]
        self.length = struct.unpack_from("<I", raw, 0x04)[0]
        self.cluster = struct.unpack_from("<I", raw, 0x10)[0]
        self.created = _timestamp(raw[0x08:0x10])
        self.modified = _timestamp(raw[0x18:0x20])
        name = raw[0x40:0x60]
        self.name = name.split(b"\x00")[0].decode("ascii", "replace")

    @property
    def exists(self):
        return bool(self.mode & DF_EXISTS)

    @property
    def is_dir(self):
        return bool(self.mode & DF_DIRECTORY)

    @property
    def is_file(self):
        return bool(self.mode & DF_FILE)

    def __repr__(self):
        return "<Entry %r %s %d bytes>" % (
            self.name, "dir" if self.is_dir else "file", self.length)


def _timestamp(raw):
    """The card's 8-byte time. Returns a string, or None if it is empty."""
    if raw == b"\x00" * 8:
        return None
    _, sec, minute, hour, day, month, year = struct.unpack("<BBBBBBH", raw)
    return "%04d-%02d-%02d %02d:%02d:%02d" % (year, month, day, hour, minute, sec)


class Card(object):
    """A memory card image, read whole and edited in place."""

    def __init__(self, raw, path=None):
        self.raw = bytearray(raw)
        self.path = path
        if bytes(self.raw[:len(MAGIC)]) != MAGIC:
            raise CardError("not a PS2 memory card image (bad magic at 0)")
        self.version = bytes(self.raw[:0x28]).split(b"\x00")[0].decode("ascii", "replace")
        (self.page_len, self.pages_per_cluster,
         self.pages_per_block) = struct.unpack_from("<HHH", self.raw, 0x28)
        (self.clusters_per_card, self.alloc_offset, self.alloc_end,
         self.rootdir_cluster) = struct.unpack_from("<4I", self.raw, 0x30)
        self.ifc_list = [c for c in struct.unpack_from("<32I", self.raw, 0x50) if c]
        self.card_type = self.raw[0x150]
        self.card_flags = self.raw[0x151]

        if self.page_len != 512:
            raise CardError("page length %d is not 512" % self.page_len)
        total_pages = self.clusters_per_card * self.pages_per_cluster
        if len(self.raw) == total_pages * (self.page_len + 16):
            self.spare_len = 16
        elif len(self.raw) == total_pages * self.page_len:
            self.spare_len = 0
        else:
            raise CardError(
                "image is %d bytes; %d pages needs %d (with ECC) or %d (without)"
                % (len(self.raw), total_pages,
                   total_pages * (self.page_len + 16), total_pages * self.page_len))
        self.phys_page = self.page_len + self.spare_len
        self.cluster_len = self.page_len * self.pages_per_cluster
        self._fat_cache = {}

    # -- images ---------------------------------------------------------

    @classmethod
    def from_file(cls, path):
        with open(path, "rb") as fh:
            return cls(fh.read(), path)

    def to_bytes(self):
        return bytes(self.raw)

    def save(self, path):
        with open(path, "wb") as fh:
            fh.write(self.raw)

    # -- pages and clusters ---------------------------------------------

    def read_page(self, page):
        off = page * self.phys_page
        return bytes(self.raw[off:off + self.page_len])

    def write_page(self, page, data):
        """Replace one page's data and refresh its ECC, keeping the tail."""
        if len(data) != self.page_len:
            raise CardError("page write is %d bytes, not %d" % (len(data), self.page_len))
        off = page * self.phys_page
        self.raw[off:off + self.page_len] = data
        if self.spare_len:
            # Only the 12 ECC bytes. The last four are the card's, not ours.
            self.raw[off + self.page_len:off + self.page_len + 12] = page_ecc(data)

    def read_cluster(self, cluster):
        base = cluster * self.pages_per_cluster
        out = bytearray()
        for i in range(self.pages_per_cluster):
            out += self.read_page(base + i)
        return bytes(out)

    def write_cluster(self, cluster, data):
        if len(data) != self.cluster_len:
            raise CardError("cluster write is %d bytes, not %d"
                            % (len(data), self.cluster_len))
        base = cluster * self.pages_per_cluster
        for i in range(self.pages_per_cluster):
            self.write_page(base + i, data[i * self.page_len:(i + 1) * self.page_len])

    # -- FAT -------------------------------------------------------------

    def fat(self, index):
        """FAT entry for an allocatable cluster, through the indirect FAT."""
        per = self.cluster_len // 4
        ifc_slot, fat_slot = divmod(index, per)
        ifc_i, ifc_j = divmod(ifc_slot, per)
        if ifc_i >= len(self.ifc_list):
            raise CardError("cluster %d is past the indirect FAT" % index)
        key = self.ifc_list[ifc_i]
        if key not in self._fat_cache:
            self._fat_cache[key] = struct.unpack("<%dI" % per, self.read_cluster(key))
        fat_cluster = self._fat_cache[key][ifc_j]
        if fat_cluster not in self._fat_cache:
            self._fat_cache[fat_cluster] = struct.unpack(
                "<%dI" % per, self.read_cluster(fat_cluster))
        return self._fat_cache[fat_cluster][fat_slot]

    def chain(self, first):
        """Every cluster index in a file's chain, in order."""
        out = []
        cur = first
        seen = set()
        while cur != 0xFFFFFFFF:
            if cur in seen:
                raise CardError("FAT chain loops at cluster %d" % cur)
            seen.add(cur)
            out.append(cur)
            entry = self.fat(cur)
            if not entry & 0x80000000:
                raise CardError("cluster %d is in a chain but not allocated" % cur)
            nxt = entry & 0x7FFFFFFF
            if nxt == 0x7FFFFFFF:
                break
            cur = nxt
        return out

    # -- directories -------------------------------------------------------

    def _entries_at(self, first_cluster, count):
        out = []
        per = self.cluster_len // ENTRY_LEN
        need = (count + per - 1) // per
        for n, idx in enumerate(self.chain(first_cluster)):
            if n >= need:
                break
            data = self.read_cluster(self.alloc_offset + idx)
            for k in range(per):
                if len(out) >= count:
                    break
                out.append(Entry(data[k * ENTRY_LEN:(k + 1) * ENTRY_LEN], idx, k))
        return out

    def root(self):
        """The root directory's entries, `.` and `..` included."""
        head = self._entries_at(self.rootdir_cluster, 1)[0]
        return self._entries_at(self.rootdir_cluster, head.length)

    def listdir(self, path=""):
        """Entries under a path. `""` is the root."""
        parts = [p for p in path.replace("\\", "/").split("/") if p]
        entries = self.root()
        for part in parts:
            match = None
            for e in entries:
                if e.exists and e.name == part:
                    match = e
                    break
            if match is None:
                raise CardError("no such directory %r on the card" % path)
            if not match.is_dir:
                raise CardError("%r is a file, not a directory" % part)
            # The count comes from the PARENT's entry. A subdirectory's own
            # `.` carries length 0 -- only the root's `.` counts its children.
            entries = self._entries_at(match.cluster, match.length)
        return entries

    def _find(self, path):
        parts = [p for p in path.replace("\\", "/").split("/") if p]
        if not parts:
            raise CardError("empty path")
        entries = self.listdir("/".join(parts[:-1]))
        for e in entries:
            if e.exists and e.name == parts[-1]:
                return e
        raise CardError("no such file %r on the card" % path)

    def read_file(self, path):
        """A file's bytes, exactly `length` of them."""
        e = self._find(path)
        if e.is_dir:
            raise CardError("%r is a directory" % path)
        out = bytearray()
        for idx in self.chain(e.cluster):
            out += self.read_cluster(self.alloc_offset + idx)
            if len(out) >= e.length:
                break
        return bytes(out[:e.length])

    def write_file(self, path, data):
        """Replace a file's bytes. Same length only -- this does not allocate.

        Whatever the tail of the last cluster held is left alone, because the
        card keeps it and a round trip has to as well.
        """
        e = self._find(path)
        if e.is_dir:
            raise CardError("%r is a directory" % path)
        if len(data) != e.length:
            raise CardError(
                "%r is %d bytes on the card; refusing to write %d "
                "(this writer does not grow or shrink files)"
                % (path, e.length, len(data)))
        clusters = self.chain(e.cluster)
        for n, idx in enumerate(clusters):
            piece = data[n * self.cluster_len:(n + 1) * self.cluster_len]
            if not piece:
                break
            if len(piece) < self.cluster_len:
                # Short final cluster: keep the card's existing tail bytes.
                piece = piece + self.read_cluster(
                    self.alloc_offset + idx)[len(piece):]
            self.write_cluster(self.alloc_offset + idx, piece)

    # -- checks -------------------------------------------------------------

    def ecc_report(self):
        """(live, bad) -- chunks that store an ECC, and those that disagree.

        A chunk whose 12 spare bytes are all 0xff is erased and stores no code;
        it is not counted. On a healthy card `bad` is [] or the single known
        page-1 entry.
        """
        if not self.spare_len:
            return (0, [])
        live = 0
        bad = []
        pages = self.clusters_per_card * self.pages_per_cluster
        for pg in range(pages):
            off = pg * self.phys_page
            spare = bytes(self.raw[off + self.page_len:off + self.page_len + 12])
            if spare == b"\xff" * 12:
                continue
            page = bytes(self.raw[off:off + self.page_len])
            want = page_ecc(page)
            for c in range(4):
                live += 1
                if spare[c * 3:c * 3 + 3] != want[c * 3:c * 3 + 3]:
                    bad.append((pg, c))
        return (live, bad)
