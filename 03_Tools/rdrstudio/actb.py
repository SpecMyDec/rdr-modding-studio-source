"""Structural reading of .actb / .stateb -- the game's behaviour tables.

21,000+ files on the disc: action tables (.actb), state machines (.stateb)
and .acttbl. Together they are where AI behaviour and scripted sequences
live, which makes them the most valuable closed format left.

    0x00  "version\\0"
    0x08  f32 format version (1.0 everywhere on this disc)
    0x0C  u32 count
    ...   a token stream of 32-bit words and inline NUL-terminated names

WHAT THIS DOES AND DOES NOT DO
------------------------------
It recovers the *structure*: every inline name, every float parameter, every
integer opcode, and where each one sits in the file. That is enough to read
what a table refers to and to retune its numbers surgically.

It does NOT know what most opcodes MEAN. Values like 1019 or 1193 are indices
into behaviour compiled inside SLUS_205.00.

That used to say naming them needed a disassembly of the ELF, and that is
false: `03_Tools/research/probe_actb.py` named five from the corpus alone by
scoring each opcode's inline-string argument against namespaces built from
INDEPENDENT files -- 1005 (run an action table), 1004 (go to a state), 1003
(use a `.tbl`), 1566 and 1522 (play a `.pup`). Most opcodes are still unnamed
and a disassembly is still the way to finish the job; what is retired is the
"nothing short of" part. See C-036.

Numeric parameters are safely editable. Whether control flow can be AUTHORED
is a separate question and an open one -- C-008, **UNTESTED**: the structure
parses and 1004/1005 are the control-flow operands, but knowing what an
operand names is not knowing the record layout well enough to write one. Do
not quote it as a settled limit.
"""

import re
import struct

MAGIC = b"version\x00"

# A float token is only believable in a sane range; everything else reads
# better as an integer opcode or index.
def _is_float(word_bytes):
    (f,) = struct.unpack("<f", word_bytes)
    if f != f or f in (float("inf"), float("-inf")):
        return False
    a = abs(f)
    return f == 0.0 or (1e-6 < a < 1e7)


class Token:
    __slots__ = ("offset", "kind", "value", "raw")

    def __init__(self, offset, kind, value, raw=None):
        self.offset = offset
        self.kind = kind        # name | float | int
        self.value = value
        self.raw = raw

    def to_json(self):
        return dict(off=self.offset, kind=self.kind, value=self.value)


def is_actb(data):
    return data[:8] == MAGIC


def parse(data, limit=20000):
    """Walk the token stream. Returns (version, count, [Token])."""
    if not is_actb(data):
        raise ValueError("not an actb/stateb (missing 'version' header)")
    version = struct.unpack_from("<f", data, 8)[0]
    count = struct.unpack_from("<I", data, 12)[0]

    # Inline names are NUL-terminated ASCII. Find them first, and record the
    # byte range each one covers -- the walker must not step INTO a name and
    # read four of its characters as an integer. ("Hast" decoding as opcode
    # 1953396040 is the tell that this is happening.)
    names = {}
    covered = {}
    for m in re.finditer(rb"[A-Za-z_][A-Za-z0-9_./\- ]{2,}\x00", data[16:]):
        start = 16 + m.start()
        end = start + len(m.group())
        names[start] = m.group()[:-1].decode("latin-1")
        for off in range(start, end):
            covered[off] = (start, end)

    tokens: list[Token] = []
    i = 16
    n = len(data)
    while i + 4 <= n and len(tokens) < limit:
        if i in names:
            name = names[i]
            tokens.append(Token(i, "name", name))
            i = covered[i][1]
            continue
        if i in covered:
            # Landed mid-name: resync to just past it rather than decoding
            # letters as a number.
            i = covered[i][1]
            continue
        word = data[i:i + 4]
        u = struct.unpack("<I", word)[0]
        if all(32 <= c < 127 for c in word):
            # Four printable bytes. Not every inline name is NUL-terminated,
            # so this is very likely text the name scan missed. Say so rather
            # than presenting "unty" as opcode 2037673589.
            tokens.append(Token(i, "chars", word.decode("latin-1"), u))
        elif _is_float(word) and u > 0x10000:
            tokens.append(Token(i, "float", round(struct.unpack("<f", word)[0], 6)))
        else:
            tokens.append(Token(i, "int", u))
        i += 4
    return version, count, tokens


def summary(data):
    version, count, tokens = parse(data)
    names = [t.value for t in tokens if t.kind == "name"]
    floats = [t for t in tokens if t.kind == "float"]
    ints = [t for t in tokens if t.kind == "int"]
    chars = [t for t in tokens if t.kind == "chars"]
    # Integers that recur across the file look like opcodes rather than data.
    freq: dict[int, int] = {}
    for t in ints:
        freq[t.value] = freq.get(t.value, 0) + 1
    opcodes = sorted(((v, c) for v, c in freq.items() if c > 1 and v > 255),
                     key=lambda x: -x[1])[:20]
    return dict(version=version, count=count, tokens=len(tokens),
                names=names, floats=len(floats), ints=len(ints),
                unresolved_text=len(chars),
                opcodes=[dict(op=v, uses=c) for v, c in opcodes])


def listing(data, limit=4000):
    """A readable dump: one line per token, with file offsets."""
    version, count, tokens = parse(data, limit=limit)
    out = ["version %.2f, header count %d, %d tokens" % (version, count, len(tokens)),
           "",
           "  offset  kind    value"]
    for t in tokens:
        if t.kind == "name":
            out.append("  %06X  name    %s" % (t.offset, t.value))
        elif t.kind == "float":
            out.append("  %06X  float   %g" % (t.offset, t.value))
        elif t.kind == "chars":
            out.append("  %06X  text?   %r  (or int %d)" % (t.offset, t.value, t.raw))
        else:
            out.append("  %06X  int     %d" % (t.offset, t.value))
    if len(tokens) >= limit:
        out.append("  ... truncated at %d tokens" % limit)
    return "\n".join(out)


def set_float(data, offset, value):
    """Replace one float parameter in place. Length never changes."""
    if offset + 4 > len(data):
        raise IndexError("offset past end of file")
    return data[:offset] + struct.pack("<f", float(value)) + data[offset + 4:]


def set_int(data, offset, value):
    if offset + 4 > len(data):
        raise IndexError("offset past end of file")
    return data[:offset] + struct.pack("<I", int(value) & 0xFFFFFFFF) + data[offset + 4:]
