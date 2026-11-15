"""Encode RGB pixels back into a `.ipu` still image.

`ipudec.py` reads the 63 shipped screens; this writes new ones, so menus,
loading screens and logos can be replaced. The decoder's docstring explains
the container and the out-of-band coding parameters -- this module only has to
produce a bitstream that the same hardware path accepts.

WHAT THE SHIPPED ENCODER DID, measured across all 63 files rather than assumed
(scripts/verify_ipuenc.py re-derives every one of these):

  * the 0x10 flags byte is 0x00 in all 63 -- MPEG-2 coding, zigzag, Table B-14,
    intra_dc_precision 0. Nothing here ever needs another value, so `encode`
    writes 0x00 and refuses to pretend otherwise.
  * macroblock 0 is ALWAYS macroblock_type '01' (Intra+Quant) carrying a 5-bit
    quantiser_scale_code; all 895 others are plain '1' (Intra). Every
    macroblock_address_increment is 1. So the picture states its own quantiser
    in macroblock 0 and never changes it -- the q_scale_code=1 that ipudec
    takes from the IDEC command word is overridden by the first symbol of
    every real file, which is worth knowing before trusting that default.
  * the q_scale_codes actually shipped are 1..11, and 4 is used by 35 of the
    63 files. That is the practical quality band: this encoder's default of 1
    is *better* than anything on the disc, and 4 is what the artists lived
    with.
  * after the last macroblock the stream is padded to a byte boundary with
    ZERO bits (63 of 63) and then the 8-byte terminator
    00 00 01 B0 00 00 01 B1 follows. Those 8 bytes are inside the header's
    size field: size + 8 == len(file) holds with them counted.
  * dimensions on the disc are 512x448 (59), 512x256 (3) and 512x512 (1).

THE DC PREDICTOR NEVER RESETS. A normal MPEG-2 picture restarts the DC
predictors at each slice header; a `.ipu` has no slices, so the chain runs
unbroken from macroblock 0 to the end, seeded at 128 (intra_dc_precision 0).
Resetting it anywhere -- per row is the tempting mistake, since that is what a
slice would do -- produces a file that still parses and still has the right
macroblock count, but every macroblock after the reset has the wrong
brightness. The round-trip correlation in the verify script is what catches
that; a structural check cannot.

SIZE IS THE REAL CONSTRAINT. A `.ipu` lives in a fixed Dave archive slot, so a
replacement has to fit the original byte count. `encode_to_fit` is the entry
point the tool should call: it raises q_scale_code until the result fits and
reports which quality it settled on.
"""
import struct

from . import ipu
from . import mpeg2intra as M
from . import mpeg2tables as T

# 00 00 01 B0 is the MPEG "user data" / end marker pair the IPU stops on, and
# 00 00 01 B1 is sequence_end. Copied verbatim from the shipped files; the
# hardware is fed these by the game's own stop path, so do not "improve" them.
TERMINATOR = bytes((0, 0, 1, 0xB0, 0, 0, 1, 0xB1))

FLAGS_BYTE = 0x00        # all 63 shipped files; see the module docstring
MBA_ONE = "1"            # macroblock_address_increment == 1, Table B-1
MB_TYPE_INTRA = "1"      # Table B-2, I-picture
MB_TYPE_INTRA_QUANT = "01"

MAX_Q_SCALE_CODE = 31    # 5-bit field, and 0 is not a legal scale


class IpuTooBig(ValueError):
    """Even the coarsest legal quantiser does not fit the byte budget."""


# ---------------------------------------------------------------------------
# Optional numpy fast path
# ---------------------------------------------------------------------------
# mpeg2intra does the same thing for the IDCT: numpy when present, pure Python
# otherwise, and the verify script asserts the two agree so the fast path
# cannot quietly drift. Encoding a 512x448 screen is 5,376 blocks of forward
# DCT, which is the difference between a fraction of a second and most of a
# minute.
#
# The reason for a failed import is KEPT rather than swallowed. It has already
# bitten once: `scripts/` held a `dis.py`, which shadowed the stdlib `dis`
# module for anything run out of that directory. numpy imports `dis`, so numpy
# raised on import and every fast path in the studio turned itself off with no
# message. The only symptom was that encoding took 6.7s instead of 0.20s.
# (That file is `probe_dis.py` since REQ-031, so this particular collision is
# gone -- but the lesson is why the reason is kept, not the filename.) If
# something here is mysteriously slow, print ipuenc.NUMPY_UNAVAILABLE.
NUMPY_UNAVAILABLE = None
try:
    import numpy as _np
    _FDCT_M = _np.array(M._IDCT_M, dtype=_np.float64)
except Exception as _exc:                             # pragma: no cover
    _np = None
    _FDCT_M = None
    NUMPY_UNAVAILABLE = "%s: %s" % (type(_exc).__name__, _exc)


# ---------------------------------------------------------------------------
# Colour
# ---------------------------------------------------------------------------
# Exactly the inverse of mpeg2intra.ycbcr_to_rgb(full_range=True): BT.601
# coefficients with no studio swing, because the IPU's CSC block is programmed
# with SGN=0 and emits 0..255. Using the studio-range matrix here would produce
# a picture that decodes fine and looks washed out.

def rgb_to_ycbcr_planes(rgb, width, height):
    """Packed RGB bytes -> (Y, Cb, Cr) full-size planes of 0..255 ints."""
    n = width * height
    if len(rgb) != n * 3:
        raise ValueError("rgb is %d bytes, want %d for %dx%d"
                         % (len(rgb), n * 3, width, height))
    if _np is not None:
        a = _np.frombuffer(bytes(rgb), dtype=_np.uint8).astype(_np.float64)
        r, g, b = a[0::3], a[1::3], a[2::3]
        y = 0.299 * r + 0.587 * g + 0.114 * b
        cb = -0.168736 * r - 0.331264 * g + 0.5 * b + 128.0
        cr = 0.5 * r - 0.418688 * g - 0.081312 * b + 128.0
        out = []
        for p in (y, cb, cr):
            out.append(_np.clip(_np.rint(p), 0, 255).astype(_np.int32))
        return out

    y = [0] * n
    cb = [0] * n
    cr = [0] * n
    for i in range(n):
        r = rgb[i * 3]
        g = rgb[i * 3 + 1]
        b = rgb[i * 3 + 2]
        y[i] = _clip8(0.299 * r + 0.587 * g + 0.114 * b)
        cb[i] = _clip8(-0.168736 * r - 0.331264 * g + 0.5 * b + 128.0)
        cr[i] = _clip8(0.5 * r - 0.418688 * g - 0.081312 * b + 128.0)
    return y, cb, cr


def _clip8(v):
    i = int(v + 0.5) if v >= 0 else -int(-v + 0.5)
    return 0 if i < 0 else (255 if i > 255 else i)


def downsample_chroma(plane, width, height):
    """Full-size chroma plane -> half size, 2x2 box average (4:2:0).

    ipudec upsamples with nearest neighbour, so a box average here is not the
    exact inverse of what the decoder does. It is still the right choice: the
    averaging error is under a quantiser step, while point-sampling would
    throw away three quarters of the chroma and alias hard edges.
    """
    hw, hh = width // 2, height // 2
    if _np is not None:
        a = _np.asarray(plane, dtype=_np.float64).reshape(height, width)
        a = a[0:hh * 2:2, 0:hw * 2:2] + a[1:hh * 2:2, 0:hw * 2:2] \
            + a[0:hh * 2:2, 1:hw * 2:2] + a[1:hh * 2:2, 1:hw * 2:2]
        return _np.clip(_np.rint(a / 4.0), 0, 255).astype(_np.int32).ravel()
    out = [0] * (hw * hh)
    for y in range(hh):
        r0 = 2 * y * width
        r1 = r0 + width
        for x in range(hw):
            c = 2 * x
            out[y * hw + x] = (plane[r0 + c] + plane[r0 + c + 1]
                               + plane[r1 + c] + plane[r1 + c + 1] + 2) // 4
    return out


# ---------------------------------------------------------------------------
# Analysis: pixels -> DCT coefficients, once
# ---------------------------------------------------------------------------

def _extract_blocks(plane, width, height, order):
    """Pull 8x8 blocks out of one plane in the order `order` lists them.

    `order` is a list of (x, y) top-left corners, all multiples of 8.
    """
    if _np is not None:
        # (h/8, 8, w/8, 8) -> (h/8, w/8, 8, 8): every block contiguous, no
        # Python-level per-pixel work.
        grid = (_np.asarray(plane, dtype=_np.float64)
                .reshape(height // 8, 8, width // 8, 8)
                .transpose(0, 2, 1, 3))
        rows = _np.fromiter((oy >> 3 for (ox, oy) in order), dtype=_np.intp,
                            count=len(order))
        cols = _np.fromiter((ox >> 3 for (ox, oy) in order), dtype=_np.intp,
                            count=len(order))
        return grid[rows, cols]
    out = []
    for (ox, oy) in order:
        blk = []
        for r in range(8):
            row = (oy + r) * width + ox
            blk.extend(plane[row:row + 8])
        out.append(blk)
    return out


def analyse(rgb, width, height):
    """RGB bytes -> forward-DCT coefficients for every block, in the order the
    bitstream needs them (macroblock raster order, Y0 Y1 Y2 Y3 Cb Cr).

    Split out from `encode` because `encode_to_fit` retries at several
    quantisers and the DCT does not depend on the quantiser. Re-running it per
    trial would make the search five times slower for no reason.
    """
    if width % 16 or height % 16:
        raise ValueError("%dx%d is not a whole number of macroblocks; .ipu is "
                         "4:2:0 so both axes must be multiples of 16"
                         % (width, height))
    y, cb, cr = rgb_to_ycbcr_planes(rgb, width, height)
    cw, ch = width // 2, height // 2
    cb = downsample_chroma(cb, width, height)
    cr = downsample_chroma(cr, width, height)

    mbx, mby = width // 16, height // 16
    luma_order = []
    chroma_order = []
    for mb in range(mbx * mby):
        mx, my = (mb % mbx) * 16, (mb // mbx) * 16
        for bi in range(4):
            luma_order.append((mx + (bi & 1) * 8, my + (bi >> 1) * 8))
        chroma_order.append((mx // 2, my // 2))

    yb = _fdct_many(_extract_blocks(y, width, height, luma_order))
    cbb = _fdct_many(_extract_blocks(cb, cw, ch, chroma_order))
    crb = _fdct_many(_extract_blocks(cr, cw, ch, chroma_order))

    nmb = mbx * mby
    if _np is not None:
        # Transmission order is Y0 Y1 Y2 Y3 Cb Cr per macroblock; build it as
        # one (nmb, 6, 64) array rather than 5,376 Python appends.
        out = _np.empty((nmb, 6, 64), dtype=_np.float64)
        out[:, 0:4, :] = yb.reshape(nmb, 4, 64)
        out[:, 4, :] = cbb.reshape(nmb, 64)
        out[:, 5, :] = crb.reshape(nmb, 64)
        return out.reshape(nmb * 6, 64), mbx, mby

    blocks = []
    for mb in range(nmb):
        blocks.extend(yb[mb * 4:mb * 4 + 4])
        blocks.append(cbb[mb])
        blocks.append(crb[mb])
    return blocks, mbx, mby


def _fdct_many(blocks):
    """Forward DCT over a list of 64-sample blocks."""
    if _np is not None:
        a = _np.asarray(blocks, dtype=_np.float64).reshape(-1, 8, 8)
        # fdct_8x8 computes M^T . s . M; this is the same thing batched.
        return _FDCT_M.T @ a @ _FDCT_M
    return [M.fdct_8x8(b) for b in blocks]


# ---------------------------------------------------------------------------
# Quantisation
# ---------------------------------------------------------------------------

def _quantise_many(coeff_blocks, q_scale_code):
    """DCT coefficients -> quantised QF, raster order, one list per block.

    The rounding is the plain nearest-integer one that mpeg2intra.quantise_intra
    does; this is not a rate-distortion optimising encoder and does not pretend
    to be. Values are clamped to the escape range because a level of +-2048 is
    forbidden by 7.2.2.3 and would make encode_block raise on an image that is
    merely very high contrast.
    """
    scale = T.QUANTISER_SCALE[0][q_scale_code]
    qm = T.DEFAULT_INTRA_QUANTISER_MATRIX
    dc_mult = T.INTRA_DC_MULT[0]

    if _np is not None:
        a = _np.asarray(coeff_blocks, dtype=_np.float64).reshape(-1, 64)
        denom = _np.asarray(qm, dtype=_np.float64) * (2.0 * scale)
        denom[0] = 1.0                      # DC is replaced below, not divided
        q = _np.rint(a * 32.0 / denom)
        q[:, 0] = _np.rint(a[:, 0] / dc_mult)
        _np.clip(q, -2047, 2047, out=q)
        return q.astype(_np.int32).tolist()

    out = []
    for c in coeff_blocks:
        qf = [0] * 64
        v = int(round(c[0] / dc_mult))
        qf[0] = -2047 if v < -2047 else (2047 if v > 2047 else v)
        for i in range(1, 64):
            v = int(round(c[i] * 32.0 / (2 * qm[i] * scale)))
            qf[i] = -2047 if v < -2047 else (2047 if v > 2047 else v)
        out.append(qf)
    return out


# ---------------------------------------------------------------------------
# Bitstream
# ---------------------------------------------------------------------------

class _CountingWriter:
    """BitWriter's interface, but it only counts bits.

    The size search runs `_write_picture` several times and only the winner is
    ever materialised. Counting through the real `encode_block` rather than a
    separate cost model is deliberate: a hand-written cost function is free to
    drift from what actually gets written, and a size search that is wrong by
    one VLC per block is exactly the bug that ships a file too big for its
    slot.
    """

    __slots__ = ("n",)

    def __init__(self):
        self.n = 0

    def write(self, value, n):
        if n and not 0 <= value < (1 << n):
            raise ValueError("%d does not fit in %d bits" % (value, n))
        self.n += n

    def write_signed(self, value, n):
        lo, hi = -(1 << (n - 1)), (1 << (n - 1)) - 1
        if not lo <= value <= hi:
            raise ValueError("%d outside %d..%d" % (value, lo, hi))
        self.n += n

    def write_code(self, code):
        self.n += len(code)

    def tell(self):
        return self.n


def _write_picture(writer, qblocks, mbx, mby, q_scale_code):
    """Every macroblock, into `writer`. Returns nothing; the writer holds it."""
    pred = [T.INTRA_DC_PRED_RESET[0]] * 3      # 128, and never reset again
    for mb in range(mbx * mby):
        if mb:
            # Never before macroblock 0 -- IDEC starts on macroblock_type.
            # Writing one here yields a file that decodes 24/63 in the mirror
            # image of the bug ipudec's docstring records.
            writer.write_code(MBA_ONE)
        if mb == 0:
            writer.write_code(MB_TYPE_INTRA_QUANT)
            writer.write(q_scale_code, 5)
        else:
            writer.write_code(MB_TYPE_INTRA)
        base = mb * 6
        for bi in range(6):
            qf = qblocks[base + bi]
            ci = 0 if bi < 4 else (bi - 3)     # Y, then Cb, then Cr
            M.encode_block(writer, qf, is_luma=(bi < 4), pred_dc=pred[ci],
                           intra_vlc_format=0, alternate_scan=0)
            pred[ci] = qf[0]


def _wrap(payload_bits, width, height):
    """Bitstream bytes -> a complete `.ipu` file."""
    body = bytes((FLAGS_BYTE,)) + payload_bits + TERMINATOR
    # 0x04 counts everything from 0x08 on: width, height, frames, then body.
    size = 8 + len(body)
    return struct.pack("<4sIHHI", ipu.MAGIC, size, width, height, 1) + body


def encode(rgb, width, height, q_scale_code=1):
    """RGB bytes -> `.ipu` file bytes at a fixed quality.

    q_scale_code 1 is the finest legal quantiser (scale 2) and therefore the
    largest file. The disc's own screens sit at 1..11 with 4 the mode, so 4 is
    a reasonable starting guess if size matters and `encode_to_fit` is not
    being used.
    """
    if not 1 <= q_scale_code <= MAX_Q_SCALE_CODE:
        raise ValueError("q_scale_code %d outside 1..31" % q_scale_code)
    blocks, mbx, mby = analyse(rgb, width, height)
    qblocks = _quantise_many(blocks, q_scale_code)
    w = M.BitWriter()
    _write_picture(w, qblocks, mbx, mby, q_scale_code)
    # Pad bit 0: all 63 shipped files pad with zeros, and a 1 here would look
    # like the start of a macroblock_address_increment to a decoder that kept
    # reading.
    return _wrap(w.bytes(pad_bit=0), width, height)


def measure(source, width=None, height=None, q_scale_code=4):
    """Exact file size in bytes at a given quality, without building the file.

    `source` is either raw RGB bytes (with width and height) or the tuple that
    `analyse` returns, so a caller sweeping quality pays for the DCT once.
    """
    if isinstance(source, tuple) and len(source) == 3:
        blocks, mbx, mby = source
    else:
        blocks, mbx, mby = analyse(source, width, height)
    qblocks = _quantise_many(blocks, q_scale_code)
    c = _CountingWriter()
    _write_picture(c, qblocks, mbx, mby, q_scale_code)
    return _file_bytes_for_bits(c.n)


def _file_bytes_for_bits(bits):
    """16-byte header + flags byte + padded bitstream + 8-byte terminator."""
    return ipu.HEADER + 1 + (bits + 7) // 8 + len(TERMINATOR)


def encode_to_fit(rgb, width, height, max_bytes, min_q=1,
                  max_q=MAX_Q_SCALE_CODE):
    """The function the tool should call: best quality that fits `max_bytes`.

    Returns a dict:
        data          the `.ipu` bytes
        bytes         len(data)
        max_bytes     the budget it was given
        q_scale_code  the quantiser it settled on (1 best .. 31 worst)
        headroom      max_bytes - bytes
        tried         [(q, size), ...] every trial, cheapest first, so a caller
                      can show the size/quality curve instead of a bare number
    Raises IpuTooBig if nothing in min_q..max_q fits, and the message carries
    the smallest size achievable, which is the number the user needs in order
    to decide whether to give up or shrink the picture.

    Size is not guaranteed monotonic in q_scale_code -- coarser quantisation
    can occasionally cost a bit more when a value crosses out of a short VLC
    into an escape -- so the binary search's answer is confirmed by stepping
    back down while the smaller q still fits, rather than trusted.
    """
    prepared = analyse(rgb, width, height)
    tried = []
    cache = {}

    def size_at(q):
        if q not in cache:
            cache[q] = measure(prepared, q_scale_code=q)
            tried.append((q, cache[q]))
        return cache[q]

    lo, hi = min_q, max_q
    best = None
    while lo <= hi:
        mid = (lo + hi) // 2
        if size_at(mid) <= max_bytes:
            best = mid
            hi = mid - 1
        else:
            lo = mid + 1

    if best is None:
        smallest = size_at(max_q)
        raise IpuTooBig(
            "smallest encode is %d bytes at q_scale_code %d, budget is %d "
            "(%d over) -- the picture has to lose resolution or detail, no "
            "quantiser will do it" % (smallest, max_q, max_bytes,
                                      smallest - max_bytes))

    while best > min_q and size_at(best - 1) <= max_bytes:
        best -= 1

    data = encode(rgb, width, height, q_scale_code=best)
    # Belt and braces: the counting writer and the real writer must agree, and
    # if they ever do not it is better to fail here than to hand the caller a
    # file that overflows its archive slot.
    if len(data) != cache[best]:
        raise AssertionError(
            "size prediction %d != actual %d at q=%d -- _CountingWriter has "
            "drifted from BitWriter" % (cache[best], len(data), best))
    if len(data) > max_bytes:
        raise IpuTooBig("encode is %d bytes, budget %d" % (len(data), max_bytes))

    return dict(data=data, bytes=len(data), max_bytes=max_bytes,
                q_scale_code=best, headroom=max_bytes - len(data),
                tried=sorted(tried))


# ---------------------------------------------------------------------------
# Convenience for the tool
# ---------------------------------------------------------------------------

def rgb_from_image(source, width, height, fit="stretch"):
    """Any Pillow-openable image -> RGB bytes at exactly width x height.

    Needs Pillow, which the studio already depends on. `fit` is "stretch" or
    "cover" (centre crop to the target aspect first, which is what you want for
    a full-screen background so nothing is squashed).

    The PS2 screens are 512x448 storage for a 4:3 display, so a 512x448 `.ipu`
    is horizontally squeezed on purpose. "cover" fits to the STORED 512x448,
    not to 4:3 -- matching what the shipped art does.
    """
    from PIL import Image
    img = source if hasattr(source, "convert") else Image.open(source)
    img = img.convert("RGB")
    if fit == "cover" and img.size != (width, height):
        sw, sh = img.size
        want = width / float(height)
        have = sw / float(sh)
        if have > want:
            new = int(round(sh * want))
            left = (sw - new) // 2
            img = img.crop((left, 0, left + new, sh))
        elif have < want:
            new = int(round(sw / want))
            top = (sh - new) // 2
            img = img.crop((0, top, sw, top + new))
    if img.size != (width, height):
        # Pillow 10 moved the constants; the studio pins nothing, so ask.
        lanczos = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
        img = img.resize((width, height), lanczos)
    return img.tobytes()


def replace(original_ipu_bytes, rgb, max_bytes=None):
    """Encode `rgb` at the dimensions and byte budget of an existing `.ipu`.

    The common case in the tool: the user picks a screen, supplies a picture,
    and the replacement has to drop into the same archive slot. Dimensions come
    from the original because the game programs the IPU from them.
    """
    head = ipu.parse(original_ipu_bytes)
    if max_bytes is None:
        max_bytes = len(original_ipu_bytes)
    return encode_to_fit(rgb, head["width"], head["height"], max_bytes)
