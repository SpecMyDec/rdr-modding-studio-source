"""Decoding a frame out of a `.PSS` cutscene -- M19's read half.

`pss.py` reads the PACK layer: which elementary streams a `.PSS` carries, how
big and how long. It stops there and says so. This module is the next layer
down -- it demultiplexes the video elementary stream and decodes one intra
picture from it to pixels.

## Why this exists as its own module

The maths already existed and is NOT reimplemented here: `mpeg2intra` has the
bit reader, the VLC tables, dequantisation, the IDCT and the colour
conversion. What it deliberately does not have is everything *around* a
macroblock -- its own docstring says "no macroblock_address_increment, no
macroblock_type, no slice handling", because a `.ipu` has no sequence or
picture header and guessing that layer would have baked a guess into the
reference.

A `.PSS` is the opposite case: it is an ordinary MPEG-2 Program Stream and it
carries every one of those headers. So this module supplies exactly the layer
`mpeg2intra` withholds -- program stream -> elementary stream -> sequence ->
picture -> slice -> macroblock -- and hands each macroblock's blocks to
`mpeg2intra` for the arithmetic. **`mpeg2intra.py` is rdr-3d's and is not
modified.**

## Two things that cost real time, recorded so they cost nobody else any

**1. `dct_type` is not optional.** A frame picture with `frame_pred_frame_dct`
clear emits one extra bit per intra macroblock, between `macroblock_type` and
`quantiser_scale_code`. RSLOGO.PSS is exactly that case. Omitting it shifts
every following bit by one, and the symptom is not a header error -- it is
`CoefficientOverflow: run 4 put the coefficient index at 68`, a *coefficient*
complaint about a *header* mistake, 125 bits downstream of the actual fault.

**2. Do not re-scan the coefficients.** `decode_intra_macroblock` returns each
block in RASTER order, already inverse-scanned. Calling `inverse_scan()` on
its output applies the scan twice and scatters the DC into a 2-pixel
checkerboard. That renders as a plausibly dark frame, passes any "did we get
pixels" assertion, and is only visible by looking at the image.

Both faults produced output rather than an exception. The check that caught
them was decoding a picture whose content is known -- the Rockstar logo in
GOP 7 of RSLOGO.PSS -- and looking at it.

## Scope

Intra (I) pictures only, which is what the milestone asks for and what the
container makes cheap: every GOP opens with one, so any point in a video is
reachable without inter prediction. P and B pictures need motion
compensation, which is a different job and is not attempted or pretended at.
"""
import struct

from . import mpeg2intra as M

PACK_START = b"\x00\x00\x01\xBA"
PICTURE_START = b"\x00\x00\x01\x00"
SEQUENCE_HEADER = b"\x00\x00\x01\xB3"
EXTENSION_START = b"\x00\x00\x01\xB5"
GOP_START = b"\x00\x00\x01\xB8"

# Slice start codes are 0x01..0xAF -- one per macroblock row.
SLICE_FIRST, SLICE_LAST = 0x01, 0xAF

EXT_SEQUENCE, EXT_PICTURE_CODING = 1, 8
PICTURE_I = 1
FRAME_PICTURE = 3


class PssVideoError(Exception):
    """This is not a video stream we can read, and here is why."""


def demux_video(data, stream_id=None):
    """The video elementary stream out of a Program Stream.

    `stream_id` picks one of 0xE0..0xEF; None takes the first video stream
    encountered, which is the only one any `.PSS` on this disc carries.
    """
    es = bytearray()
    i, n = 0, len(data)
    while i < n - 6:
        if data[i:i + 4] == PACK_START:
            if i + 14 > n:
                break
            i += 14 + (data[i + 13] & 7)          # pack header + stuffing
            continue
        if data[i:i + 3] != b"\x00\x00\x01":
            i += 1
            continue
        sid = data[i + 3]
        if sid == 0xB9:                            # program end
            break
        if i + 6 > n:
            break
        length = struct.unpack_from(">H", data, i + 4)[0]
        body = data[i + 6:i + 6 + length]
        want = (0xE0 <= sid <= 0xEF) if stream_id is None else (sid == stream_id)
        if want and len(body) > 3:
            es += _pes_payload(body)
        i += 6 + length
    return bytes(es)


def _pes_payload(body):
    """Strip the PES header. MPEG-2 style if the flag bits say so, else MPEG-1."""
    if body[0] & 0xC0 == 0x80:                     # '10' -> MPEG-2 PES
        return body[3 + body[2]:]
    k = 0
    while k < len(body) and body[k] == 0xFF:       # MPEG-1 stuffing
        k += 1
    if k < len(body) and body[k] & 0xC0 == 0x40:   # STD buffer scale/size
        k += 2
    if k < len(body):
        if body[k] & 0xF0 == 0x20:
            k += 5
        elif body[k] & 0xF0 == 0x30:
            k += 10
        elif body[k] == 0x0F:
            k += 1
    return body[k:]


def iframe_offsets(es):
    """Byte offset of every intra picture's picture_start_code, in order.

    Every GOP opens with one, so this is the set of frames reachable without
    inter prediction.
    """
    out: list[int] = []
    i = 0
    while True:
        i = es.find(PICTURE_START, i + 1 if i else 0)
        if i < 0:
            return out
        if i + 6 <= len(es):
            coding_type = (es[i + 5] >> 3) & 7
            if coding_type == PICTURE_I:
                out.append(i)


def parse_headers(es, picture=None):
    """Sequence + picture parameters needed to decode `picture`.

    `picture` is a picture_start_code offset (default: the first intra one).
    Every field here is one `mpeg2intra` asks for and a `.ipu` cannot supply.
    """
    seq = es.find(SEQUENCE_HEADER)
    if seq < 0:
        raise PssVideoError("no sequence header: not an MPEG-2 video stream")

    r = M.BitReader(es, (seq + 4) * 8)
    width, height = r.read(12), r.read(12)
    r.read(4), r.read(4), r.read(18), r.read(1), r.read(10), r.read(1)
    intra_matrix = [r.read(8) for _ in range(64)] if r.read(1) else None
    if r.read(1):
        [r.read(8) for _ in range(64)]             # non-intra matrix, unused

    hp = dict(width=width, height=height, intra_matrix=intra_matrix,
              chroma="4:2:0", intra_dc_precision=0, picture_structure=FRAME_PICTURE,
              frame_pred_frame_dct=1, q_scale_type=0, intra_vlc_format=0,
              alternate_scan=0)

    ext = _find_extension(es, seq, EXT_SEQUENCE)
    if ext is not None:
        e = M.BitReader(es, (ext + 4) * 8)
        e.read(4), e.read(8), e.read(1)
        hp["chroma"] = {1: "4:2:0", 2: "4:2:2", 3: "4:4:4"}.get(e.read(2), "4:2:0")

    if picture is None:
        got = iframe_offsets(es)
        if not got:
            raise PssVideoError("no intra picture in this stream")
        picture = got[0]
    hp["picture"] = picture

    p = M.BitReader(es, (picture + 4) * 8)
    hp["temporal_reference"] = p.read(10)
    hp["picture_coding_type"] = p.read(3)
    if hp["picture_coding_type"] != PICTURE_I:
        raise PssVideoError("picture at %d is coding type %d, not intra"
                            % (picture, hp["picture_coding_type"]))

    ext = _find_extension(es, picture, EXT_PICTURE_CODING)
    if ext is not None:
        e = M.BitReader(es, (ext + 4) * 8)
        e.read(4)
        for _ in range(4):
            e.read(4)                              # f_codes
        hp["intra_dc_precision"] = e.read(2)
        hp["picture_structure"] = e.read(2)
        e.read(1)                                  # top_field_first
        hp["frame_pred_frame_dct"] = e.read(1)
        e.read(1)                                  # concealment_motion_vectors
        hp["q_scale_type"] = e.read(1)
        hp["intra_vlc_format"] = e.read(1)
        hp["alternate_scan"] = e.read(1)
    return hp


def _find_extension(es, after, want_id):
    """Offset of the next extension_start_code with this identifier."""
    i = after
    while True:
        i = es.find(EXTENSION_START, i + 1)
        if i < 0 or i + 5 > len(es):
            return None
        if (es[i + 4] >> 4) == want_id:
            return i


def _slice_offsets(es, picture):
    """Every slice start code belonging to this picture, in order."""
    end = es.find(PICTURE_START, picture + 4)
    if end < 0:
        end = len(es)
    out: list[int] = []
    i = picture
    while True:
        best = None
        for code in range(SLICE_FIRST, SLICE_LAST + 1):
            k = es.find(bytes([0, 0, 1, code]), i + 4, end)
            if k >= 0 and (best is None or k < best):
                best = k
        if best is None:
            return out
        out.append(best)
        i = best


def decode_frame(es, picture=None):
    """One intra picture -> (width, height, Y, Cb, Cr) as 0..255 plane lists.

    Y is full size; Cb and Cr are at the chroma resolution for the stream's
    format. `to_rgb` puts them together.
    """
    hp = parse_headers(es, picture)
    w, h = hp["width"], hp["height"]
    mb_w = (w + 15) // 16
    cw, ch = _chroma_size(hp["chroma"], w, h)

    Y = bytearray(w * h)
    Cb = bytearray(cw * ch)
    Cr = bytearray(cw * ch)
    n_mb = 0

    for s in _slice_offsets(es, hp["picture"]):
        row = es[s + 3] - 1
        r = M.BitReader(es, (s + 4) * 8)
        qsc = r.read(5)
        while r.read(1):                           # intra_slice / extra_bit_slice
            r.read(8)
        pred = [1 << (7 + hp["intra_dc_precision"])] * 3
        col = -1
        while True:
            try:
                col += M.MB_ADDR_VLC.decode(r)
                quant = False
                if r.read(1) == 0:                 # '01' = Intra, Quant
                    if r.read(1) != 1:
                        break
                    quant = True
                # See the module docstring: this bit is why a missing header
                # field surfaces as a coefficient overflow.
                if (hp["picture_structure"] == FRAME_PICTURE
                        and hp["frame_pred_frame_dct"] == 0):
                    r.read(1)                      # dct_type
                if quant:
                    qsc = r.read(5)
                blocks, pred = M.decode_intra_macroblock(
                    r, pred, chroma_format=hp["chroma"],
                    intra_vlc_format=hp["intra_vlc_format"],
                    alternate_scan=hp["alternate_scan"])
            except Exception:
                break                              # slice ends at a bad read
            n_mb += 1
            _place(blocks, hp, qsc, row, col, Y, Cb, Cr, w, h, cw, ch)
            if r.peek(23) == 0:                    # next start code
                break
    return dict(width=w, height=h, chroma=hp["chroma"], y=Y, cb=Cb, cr=Cr,
                macroblocks=n_mb, expected=mb_w * ((h + 15) // 16),
                params=hp)


def _chroma_size(chroma, w, h):
    if chroma == "4:2:0":
        return (w + 1) // 2, (h + 1) // 2
    if chroma == "4:2:2":
        return (w + 1) // 2, h
    return w, h


def _block_pixels(block, hp, qsc):
    """One block of quantised coefficients -> 64 clamped samples.

    No `inverse_scan` here on purpose -- see the module docstring.
    """
    coeffs = M.dequantise_intra(block, quantiser_scale=None, q_scale_code=qsc,
                                q_scale_type=hp["q_scale_type"],
                                intra_dc_precision=hp["intra_dc_precision"],
                                quant_matrix=hp["intra_matrix"])
    return M.clamp_samples(M.idct_8x8(coeffs))


def _place(blocks, hp, qsc, row, col, Y, Cb, Cr, w, h, cw, ch):
    """Scatter a macroblock's decoded blocks into the planes."""
    for i in range(4):                             # luma, 2x2 of 8x8
        px = _block_pixels(blocks[i], hp, qsc)
        ox, oy = col * 16 + (i % 2) * 8, row * 16 + (i // 2) * 8
        for yy in range(8):
            if oy + yy >= h:
                break
            base = (oy + yy) * w + ox
            for xx in range(min(8, w - ox)):
                Y[base + xx] = px[yy * 8 + xx]
    sub_y = 2 if hp["chroma"] == "4:2:0" else 1
    for plane, i in ((Cb, 4), (Cr, 5)):
        if i >= len(blocks):
            continue
        px = _block_pixels(blocks[i], hp, qsc)
        ox, oy = col * 8, row * (16 // sub_y)
        for yy in range(8):
            if oy + yy >= ch:
                break
            base = (oy + yy) * cw + ox
            for xx in range(min(8, cw - ox)):
                plane[base + xx] = px[yy * 8 + xx]


def to_rgb(frame, full_range=True):
    """A decoded frame -> packed RGB bytes, length width*height*3."""
    w, h = frame["width"], frame["height"]
    cw, ch = _chroma_size(frame["chroma"], w, h)
    fx, fy = w // cw, h // ch
    cb = M.upsample_chroma(frame["cb"], cw, ch, fx, fy)
    cr = M.upsample_chroma(frame["cr"], cw, ch, fx, fy)
    return M.ycbcr_planes_to_rgb(frame["y"], cb, cr, w, h, full_range=full_range)


# ---------------------------------------------------------------- replacing

PADDING_STREAM = 0xBE
END_CODE = b"\x00\x00\x01\xB9"
_PAD_HEADER = 6                       # 00 00 01 BE + u16 length
_PAD_MAX_PAYLOAD = 0xFFFF


class ReplaceRefused(Exception):
    """Something the caller can fix, carrying the fix."""

    def __init__(self, message, fix="", code="bad_request", detail=""):
        Exception.__init__(self, message)
        self.message, self.fix, self.code, self.detail = message, fix, code, detail

    def as_problem(self):
        return dict(code=self.code, message=self.message, fix=self.fix,
                    detail=self.detail)


def pad_to(data, size):
    """A Program Stream padded to EXACTLY `size` bytes, still a valid stream.

    A `.PSS` lives loose on the ISO, and `Iso9660` never changes a file's LBA
    or its length -- by design, because a wrong repoint must fail loudly
    rather than silently move the volume around. So a replacement cannot be
    shorter *in the directory record*: the game would read the new video and
    then keep reading whatever stale bytes followed it.

    MPEG-2 answers this itself. Stream id 0xBE is a padding packet, and it is
    not a trick -- `RSLOGO.PSS` and `COACH02.PSS` ship with three each. So a
    shorter video is padded up to the original extent with real padding
    packets and remains a file any player will open.

    The `00 00 01 B9` program end code stays LAST: all 22 files on this disc
    end with it, so padding goes before it, not after.
    """
    body, end = (data[:-4], END_CODE) if data.endswith(END_CODE) else (data, b"")
    need = size - len(body) - len(end)
    if need < 0:
        raise ReplaceRefused(
            "this video is %d bytes too long for the slot it would replace."
            % -need,
            fix="Re-encode it smaller, or replace a longer cutscene.",
            code="slot_overflow")
    if 0 < need < _PAD_HEADER:
        # A padding packet cannot be smaller than its own header.
        raise ReplaceRefused(
            "this video is %d bytes short of the slot, and a padding packet "
            "needs at least %d." % (need, _PAD_HEADER),
            fix="Re-encode it a few bytes shorter so the gap is 0 or at "
                "least %d bytes." % _PAD_HEADER,
            code="unpaddable",
            detail="gap %d, minimum padding packet %d" % (need, _PAD_HEADER))
    out = bytearray(body)
    while need > 0:
        take = min(need, _PAD_HEADER + _PAD_MAX_PAYLOAD)
        # Never leave a remainder too small to be its own packet.
        if 0 < need - take < _PAD_HEADER:
            take -= _PAD_HEADER
        payload = take - _PAD_HEADER
        out += (b"\x00\x00\x01" + bytes([PADDING_STREAM])
                + struct.pack(">H", payload) + b"\xFF" * payload)
        need -= take
    out += end
    assert len(out) == size, "padding arithmetic: %d != %d" % (len(out), size)
    return bytes(out)


def plan_replace(game, iso_path, new_data):
    """Cost and check replacing a loose `.PSS`. Writes nothing.

    Returns `(plan, problem, padded_bytes)`. `padded_bytes` is what was
    COSTED, so a caller writes the bytes that were measured rather than
    rebuilding them and trusting the two to agree -- the same discipline the
    weapon importer uses.
    """
    entry = game.iso.get(iso_path)
    if entry is None:
        raise ReplaceRefused("there is no %s on this disc." % iso_path,
                             fix="Pick one of the files in PSS/.",
                             code="not_found")
    plan = dict(path=iso_path, slot=entry.size, bytes_in=len(new_data),
                lba=entry.lba)

    if not is_program_stream(new_data):
        return plan, dict(
            code="bad_request",
            message="that file does not start with an MPEG pack header, so "
                    "it is not a Program Stream.",
            fix="Export the video as an MPEG-2 Program Stream (.mpg/.pss).",
            detail="first bytes %s" % new_data[:4].hex()), None

    try:
        padded = pad_to(new_data, entry.size)
    except ReplaceRefused as ex:
        plan["fits"] = False
        return plan, ex.as_problem(), None

    plan["fits"] = True
    plan["padding"] = entry.size - len(new_data)
    plan["writes"] = [dict(path=iso_path, bytes_before=entry.size,
                           bytes_after=len(padded), slot=entry.size,
                           headroom=0)]
    return plan, None, padded


def is_program_stream(data):
    """True if this begins with an MPEG pack header."""
    return len(data) >= 4 and data[:4] == PACK_START


def replace(game, iso_path, new_data, dry=True):
    """Replace a loose `.PSS` in place. `dry` defaults to TRUE.

    The default is deliberate and matches `api.is_dry`: an omitted flag means
    PLAN, never WRITE. The opposite default is the one that has cost this
    repo a boot test more than once, and the reply to a dry run is otherwise
    indistinguishable from the reply to a write.
    """
    plan, problem, padded = plan_replace(game, iso_path, new_data)
    if problem is not None:
        return dict(ok=False, dry=bool(dry), written=False, plan=plan,
                    problem=problem)
    if dry:
        return dict(ok=True, dry=True, written=False, plan=plan)
    if not getattr(game, "writable", False):
        raise ReplaceRefused(
            "this disc is open read-only, so nothing was written.",
            fix="Open the working ISO for writing, not the vanilla master.",
            code="read_only")
    entry = game.iso.get(iso_path)
    game.iso.write_at(entry.offset, padded)
    game.iso.flush()
    return dict(ok=True, dry=False, written=True, applied=True, plan=plan,
                used=len(padded), slot=entry.size)
