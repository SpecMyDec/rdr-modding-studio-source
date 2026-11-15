"""A VCDIFF (RFC 3284) patch writer, so a mod can travel without the game.

Sharing the working ISO means handing someone 2.2 GB of a commercial disc.
Sharing a patch means handing them only the bytes that changed; they apply it
to their own copy with xdelta3 or Delta Patcher, which is how PS2 mods are
normally passed around. Those tools read VCDIFF, and nothing on this machine
ships an encoder for it, so this is one.

It uses the smallest honest subset of the format and nothing clever:

  * one target window per WINDOW bytes of the modded image, each paired with
    the SAME range of the stock image as its source segment -- an edit on
    this disc never moves a file, so equal bytes are at equal offsets;
  * two instructions only: COPY (default code table index 19, mode 0, the
    address being an offset into that source segment) for runs that match,
    ADD (index 1) for runs that do not;
  * no secondary compressor, no custom code table, no application header.

A decoder must accept all of that, because it is the baseline every decoder
implements. What this does NOT do is find moved data -- a byte that changed
position is sent as an ADD. For this game that costs nothing: repacks rewrite
archive members in place, and a grown image appends past the old end, where
there is no source to copy from anyway.

The image may be longer or shorter than stock. Windows past the end of the
source carry no source segment and are all ADD.
"""

import hashlib

MAGIC = b"\xd6\xc3\xc4\x00"
WINDOW = 1 << 20          # 1 MiB target windows; xdelta3's hard cap is 16 MiB
BLOCK = 512               # granularity of the match search inside a window

VCD_SOURCE = 0x01
OP_ADD0 = 1               # ADD, size in the instruction stream
OP_COPY0 = 19             # COPY mode 0 (VCD_SELF), size in the instruction stream


def varint(n):
    """RFC 3284 section 2: base-128, most significant group first."""
    if n < 0:
        raise ValueError("varint of a negative number: %d" % n)
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append(0x80 | (n & 0x7F))
        n >>= 7
    return bytes(reversed(out))


def _runs(src, tgt):
    """(equal, start, end) runs over tgt, compared BLOCK by BLOCK against src."""
    n = len(tgt)
    m = len(src)
    runs = []
    pos = 0
    while pos < n:
        end = min(pos + BLOCK, n)
        same = end <= m and src[pos:end] == tgt[pos:end]
        if runs and runs[-1][0] == same:
            runs[-1][2] = end
        else:
            runs.append([same, pos, end])
        pos = end
    return runs


def encode_window(src, tgt):
    """(window body, literal bytes). `src` is the source segment, maybe empty."""
    data = bytearray()
    inst = bytearray()
    addr = bytearray()
    if src and src == tgt:
        # The common case by far: a whole megabyte nobody touched.
        inst.append(OP_COPY0)
        inst += varint(len(tgt))
        addr += varint(0)
    else:
        for same, a, b in _runs(src, tgt):
            if same:
                inst.append(OP_COPY0)
                inst += varint(b - a)
                addr += varint(a)
            else:
                inst.append(OP_ADD0)
                inst += varint(b - a)
                data += tgt[a:b]
    body = (varint(len(tgt)) + b"\x00"
            + varint(len(data)) + varint(len(inst)) + varint(len(addr))
            + bytes(data) + bytes(inst) + bytes(addr))
    return body, len(data)


def write_patch(src_path, tgt_path, out, progress=None):
    """Write a patch turning src_path into tgt_path to the open file `out`.

    Returns a summary: sizes, how many bytes went in as literal data, and the
    sha256 of both images as read -- so the caller can say which disc the
    patch expects and which it produces, from the bytes rather than a name.
    """
    src_sha = hashlib.sha256()
    tgt_sha = hashlib.sha256()
    changed = 0
    windows = 0
    out.write(MAGIC + b"\x00")          # Hdr_Indicator: nothing optional
    with open(src_path, "rb") as fs, open(tgt_path, "rb") as ft:
        fs.seek(0, 2)
        src_len = fs.tell()
        fs.seek(0)
        ft.seek(0, 2)
        tgt_len = ft.tell()
        ft.seek(0)
        pos = 0
        while pos < tgt_len:
            tgt = ft.read(WINDOW)
            if not tgt:
                break
            src = fs.read(WINDOW) if pos < src_len else b""
            src_sha.update(src)
            tgt_sha.update(tgt)
            body, literal = encode_window(src, tgt)
            changed += literal
            if src:
                head = bytes([VCD_SOURCE]) + varint(len(src)) + varint(pos)
            else:
                head = b"\x00"
            out.write(head + varint(len(body)) + body)
            pos += len(tgt)
            windows += 1
            if progress and windows % 64 == 0:
                progress(pos, tgt_len)
        # Stock bytes past the end of a SHORTER image still count toward the
        # stock hash, or the "expects" hash would not match the stock disc.
        while True:
            rest = fs.read(WINDOW)
            if not rest:
                break
            src_sha.update(rest)
    return dict(source_size=src_len, target_size=tgt_len, windows=windows,
                changed_bytes=changed, source_sha256=src_sha.hexdigest(),
                target_sha256=tgt_sha.hexdigest())
