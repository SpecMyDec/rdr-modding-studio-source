"""`.strtbl` -- every string the game puts on screen.

203 files, 10.9 MB, 96,393 English records over 3,069 distinct identifiers:
weapon names, shop prompts, tutorial lines, mission text, menu labels.

    0x00  u32       n_lang (10 on every file)
    0x04  u32[10]   CUMULATIVE start offset per language slot. Slot i spans
                    [off[i], off[i+1]); slot 9 runs to EOF; off[i]==off[i+1]
                    means that language is absent.
    0x2C  u32       512
    0x30  u32       n_keys
    0x34            n_keys x { u32 len, len bytes ASCII, u8 0 }
                    -- ends exactly at off[0] on all 203 files
    off[i]          u32 count (== n_keys), then count records:
                      u32 id      PJW/ELF hash of the identifier
                      u16 flags   1 on all 101,778 records
                      u32 fl, fl bytes ASCII   font name, NOT terminated
                      u32 sl, sl UTF-16LE units  text, sl COUNTS the NUL
                      f32 scale_x
                      f32 scale_y
                      u16 0

**Strings are addressed by a hash of the identifier, not by position.** That
is the trap: `count == n_keys` on every block, so the key array and the record
array are the same length and a positional reading lines up perfectly while
being in the wrong order. Verified by scoring ten candidate hashes on multiset
equality across all 209 blocks -- PJW matches 209/209 and every rival matches
0/209.

Language slots actually used: 0 English (203 files), 1 a pig-latin
pseudo-locale left in by the localisation pipeline (2 dev files), 6 Japanese
(4 Xbox/backup files). Slots 2,3,4,5,7,8,9 are empty everywhere.

Writing changes the file's length -- text is length-prefixed UTF-16 -- so
unlike the rest of this toolkit an edit here is NOT byte-count neutral and
has to be slot-checked. `encode` rebuilds the language offset table, which is
the part a naive rewrite gets wrong: those offsets are cumulative, so a string
one character longer moves every later language block.
"""

import struct

HEADER = 0x34
N_LANG = 10
LANG_NAMES = {0: "English", 1: "Pig Latin (dev)", 6: "Japanese"}


def name_hash(name):
    """PJW/ELF, case-sensitive over the raw bytes. Always < 2^28."""
    h = 0
    for c in name.encode("latin-1", "replace"):
        h = ((h << 4) + c) & 0xFFFFFFFF
        g = h & 0xF0000000
        if g:
            h ^= g >> 24
            h &= (~g) & 0xFFFFFFFF
    return h


class Record:
    __slots__ = ("id", "flags", "font", "text", "sx", "sy", "tail")

    def __init__(self, rid, flags, font, text, sx, sy, tail):
        self.id = rid
        self.flags = flags
        self.font = font
        self.text = text
        self.sx = sx
        self.sy = sy
        self.tail = tail


class StringTable:
    """A parsed .strtbl. Edit `blocks[slot][i].text`, then `encode()`."""

    def __init__(self, data):
        self.data = data
        n_lang = struct.unpack_from("<I", data, 0)[0]
        if n_lang != N_LANG:
            raise ValueError("n_lang is %d, expected %d" % (n_lang, N_LANG))
        offs = list(struct.unpack_from("<10I", data, 4))
        if offs != sorted(offs):
            raise ValueError("language offsets are not non-decreasing")
        if offs[9] > len(data):
            raise ValueError("language offsets run past the end of the file")
        self.magic = struct.unpack_from("<I", data, 0x2C)[0]
        n_keys = struct.unpack_from("<I", data, 0x30)[0]

        p = HEADER
        self.keys = []
        for _ in range(n_keys):
            ln = struct.unpack_from("<I", data, p)[0]
            p += 4
            self.keys.append(data[p:p + ln].decode("latin-1"))
            p += ln + 1
        if p != offs[0]:
            raise ValueError("key table ends at 0x%X, not at the first "
                             "language offset 0x%X" % (p, offs[0]))

        self.blocks = {}
        bounds = offs + [len(data)]
        for i in range(N_LANG):
            start, end = bounds[i], bounds[i + 1]
            if start >= end:
                continue
            self.blocks[i] = self._read_block(data, start, end, i)

        # identifier <- hash, so a record can name itself
        self.by_hash = {name_hash(k): k for k in self.keys}

    def _read_block(self, data, start, end, slot):
        q = start
        count = struct.unpack_from("<I", data, q)[0]
        q += 4
        recs = []
        for _ in range(count):
            rid, flags = struct.unpack_from("<IH", data, q)
            q += 6
            fl = struct.unpack_from("<I", data, q)[0]
            q += 4
            font = data[q:q + fl].decode("latin-1")
            q += fl
            sl = struct.unpack_from("<I", data, q)[0]
            q += 4
            text = data[q:q + sl * 2].decode("utf-16-le", "replace")
            q += sl * 2
            sx, sy = struct.unpack_from("<2f", data, q)
            q += 8
            tail = struct.unpack_from("<H", data, q)[0]
            q += 2
            recs.append(Record(rid, flags, font, text.rstrip("\x00"),
                               sx, sy, tail))
        if q != end:
            raise ValueError("language block %d ends at 0x%X, not 0x%X"
                             % (slot, q, end))
        return recs

    # ------------------------------------------------------------- access

    def name_of(self, record):
        return self.by_hash.get(record.id)

    def entries(self, slot=0):
        """(identifier, Record) for one language, identifier may be None."""
        return [(self.by_hash.get(r.id), r)
                for r in self.blocks.get(slot, [])]

    def find(self, identifier, slot=0):
        want = name_hash(identifier)
        for i, r in enumerate(self.blocks.get(slot, [])):
            if r.id == want:
                return i, r
        return None, None

    def set_text(self, identifier, text, slot=0):
        i, r = self.find(identifier, slot)
        if r is None:
            raise KeyError("no string named %r in language slot %d"
                           % (identifier, slot))
        r.text = text
        return i

    # ------------------------------------------------------------- encode

    def encode(self):
        """Rebuild the file, recomputing the cumulative language offsets.

        Those offsets are the part a naive rewrite gets wrong. They are
        cumulative, so a string one character longer moves the start of every
        later language block; leaving them alone produces a file that parses
        and reads pure garbage.
        """
        keyblob = bytearray()
        for k in self.keys:
            b = k.encode("latin-1")
            keyblob += struct.pack("<I", len(b)) + b + b"\x00"

        blobs = {}
        for slot, recs in self.blocks.items():
            out = bytearray(struct.pack("<I", len(recs)))
            for r in recs:
                out += struct.pack("<IH", r.id, r.flags)
                f = r.font.encode("latin-1")
                out += struct.pack("<I", len(f)) + f
                t = (r.text + "\x00").encode("utf-16-le")
                out += struct.pack("<I", len(t) // 2) + t
                out += struct.pack("<2fH", r.sx, r.sy, r.tail)
            blobs[slot] = bytes(out)

        # A slot with no data starts where the previous one ended, which is
        # how an absent language is expressed (off[i] == off[i+1]).
        offs = []
        cursor = HEADER + len(keyblob)
        for i in range(N_LANG):
            offs.append(cursor)
            cursor += len(blobs.get(i, b""))

        out = bytearray()
        out += struct.pack("<I", N_LANG)
        out += struct.pack("<10I", *offs)
        out += struct.pack("<I", self.magic)
        out += struct.pack("<I", len(self.keys))
        out += keyblob
        for i in range(N_LANG):
            out += blobs.get(i, b"")
        return bytes(out)


def parse(data):
    return StringTable(data)
