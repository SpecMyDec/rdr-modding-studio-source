"""MPEG-2 intra decoding: bit reader, VLC decode, dequantise, IDCT, colour.

This is the maths half of reading a `.ipu` (see ipu.py). It knows nothing about
the game, the disc, or the file header -- it takes a bitstream and a set of
picture parameters and gives back pixels. The parameters are exactly the ones a
`.ipu` never states, because it has no sequence or picture header:

    intra_dc_precision   0..3
    q_scale_code         1..31
    q_scale_type         0 or 1
    intra_vlc_format     0 (Table B-14) or 1 (Table B-15)
    alternate_scan       0 (zigzag) or 1
    quantiser matrix     default intra, or a custom one
    chroma format        4:2:0 or 4:2:2

so a caller has to supply them, and finding them is a search.

TWO THINGS ABOUT THAT SEARCH, both measured by scripts/verify_mpeg2.py rather
than assumed, because the obvious version of each is wrong.

**Only two of those parameters change how the bitstream parses.** Everything
downstream of the VLC layer -- intra_dc_precision, q_scale_code, q_scale_type,
the quantiser matrix, and alternate_scan -- consumes exactly the same bits
whatever it is set to. The DC size table is shared across all four precisions,
the quantiser only scales numbers that have already been read, and the scan is
a permutation applied after the fact. So a decode-length search can settle only:

    intra_vlc_format   0 vs 1   (different AC code table, parses differently)
    chroma format      4:2:0 vs 4:2:2 vs 4:4:4  (different block count per MB)

The other five are invisible to the parser and have to be judged from the
picture that comes out. Expecting the bitstream to reveal them wastes the
search; expecting it to reveal these two works.

**The stream is far less self-checking per symbol than it looks.** The share of
bit patterns that match no code at all is 1/4096 for Table B-14 and 9/4096 for
B-15 -- and the DC tables are complete codes, so they reject nothing, ever.
Decoding pure noise as intra blocks therefore survives a median of 37 blocks
and, in 2000 trials, up to 596. Undefined codes are not even the usual way a
wrong guess dies: on noise the failure is CoefficientOverflow 95% of the time,
a run walking past coefficient 63, and UndefinedVLC only 5%.

What IS sharp is the joint condition -- the exact expected macroblock count AND
finishing within a few bits of the end of the data. Across 2000 noise trials at
the length of a real picture, that was met 0 times, while the correct parameters
met it exactly. So score candidates on landing position and macroblock count
together, not on raw distance travelled.

Everything that can fail therefore fails loudly and specifically -- see the
exception classes below. Each one carries the bit position, so a search can rank
candidates by `exc.bit_position` without catching-and-guessing.

The tables live in mpeg2tables.py. `validate_tables()` here re-derives their
structural invariants; scripts/verify_mpeg2.py runs the full round-trip suite.

References are to ISO/IEC 13818-2. Clause numbers are given at each step.
"""

import math
from fractions import Fraction

from . import mpeg2tables as T

__all__ = [
    "Mpeg2Error", "UndefinedVLC", "EndOfBitstream", "ForbiddenValue",
    "CoefficientOverflow",
    "BitReader", "BitWriter", "VLCDecoder",
    "decode_block", "decode_intra_macroblock",
    "dequantise_intra", "inverse_scan", "forward_scan",
    "idct_8x8", "clamp_samples",
    "ycbcr_to_rgb", "ycbcr_planes_to_rgb", "upsample_chroma",
    "encode_block", "quantise_intra",
    "validate_tables",
]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class Mpeg2Error(Exception):
    """Base for everything this module raises. Always carries bit_position."""

    def __init__(self, message, bit_position=None):
        super().__init__(message)
        self.bit_position = bit_position


class UndefinedVLC(Mpeg2Error):
    """The next bits are not a code in the table.

    This is THE signal that a parameter guess is wrong. `bits` is what was
    peeked (padded with zeros if the stream ran out), `table` names which table
    was being read, so a failure can be read without re-running it.
    """

    def __init__(self, table, bits, bit_position):
        super().__init__(
            "undefined %s code %r at bit %d" % (table, bits, bit_position),
            bit_position)
        self.table = table
        self.bits = bits


class EndOfBitstream(Mpeg2Error):
    """Ran out of data mid-symbol. Distinct from UndefinedVLC on purpose:
    reaching the end is what a CORRECT parameter set does, so a search must be
    able to tell the two apart."""


class ForbiddenValue(Mpeg2Error):
    """A syntactically readable but illegal value: escape level 0 or -2048,
    q_scale_code 0, and so on."""


class CoefficientOverflow(Mpeg2Error):
    """A run pushed the coefficient index past 63. Desynchronisation, not a
    legal stream."""


# ---------------------------------------------------------------------------
# Bit I/O -- MSB first, which is how every MPEG bitstream is written
# ---------------------------------------------------------------------------

class BitReader:
    """Most-significant-bit-first reader over a bytes-like object.

    Positions are in BITS from the start of `data`. `peek` past the end pads
    with zeros so a VLC lookup can still be attempted; `read` past the end
    raises EndOfBitstream. That split matters: it lets the last real code in a
    stream decode even when it needs padding to look up, while still refusing
    to invent data.
    """

    __slots__ = ("_data", "_nbits", "_pos")

    def __init__(self, data, start_bit=0):
        self._data = bytes(data)
        self._nbits = len(self._data) * 8
        if not 0 <= start_bit <= self._nbits:
            raise ValueError("start_bit %d outside 0..%d"
                             % (start_bit, self._nbits))
        self._pos = start_bit

    # -- position ----------------------------------------------------------
    def tell(self):
        """Current position, in bits."""
        return self._pos

    def seek(self, bit_position):
        if not 0 <= bit_position <= self._nbits:
            raise ValueError("bit %d outside 0..%d"
                             % (bit_position, self._nbits))
        self._pos = bit_position

    def __len__(self):
        return self._nbits

    @property
    def nbits(self):
        return self._nbits

    def remaining(self):
        """Bits left. Zero or negative never happens -- reads are checked."""
        return self._nbits - self._pos

    def at_end(self):
        return self._pos >= self._nbits

    def byte_align(self):
        """Advance to the next byte boundary. Returns bits skipped."""
        skip = (-self._pos) % 8
        self._pos += skip
        return skip

    # -- reading -----------------------------------------------------------
    def peek(self, n):
        """Next `n` bits as an int, WITHOUT advancing. Pads with zeros past the
        end of the data."""
        if n <= 0:
            return 0
        pos = self._pos
        first = pos >> 3
        nbytes = ((pos & 7) + n + 7) >> 3
        chunk = self._data[first:first + nbytes]
        if len(chunk) < nbytes:
            chunk += b"\x00" * (nbytes - len(chunk))
        value = int.from_bytes(chunk, "big")
        shift = (nbytes << 3) - (pos & 7) - n
        return (value >> shift) & ((1 << n) - 1)

    def peek_bits(self, n):
        """Next `n` bits as a '0'/'1' string. For error messages and tests."""
        return format(self.peek(n), "0%db" % n) if n > 0 else ""

    def skip(self, n):
        """Advance `n` bits, refusing to go past the end."""
        if n < 0:
            raise ValueError("negative skip")
        if self._pos + n > self._nbits:
            raise EndOfBitstream(
                "need %d bits at %d, only %d left"
                % (n, self._pos, self._nbits - self._pos), self._pos)
        self._pos += n

    def read(self, n):
        """Next `n` bits as an unsigned int, advancing."""
        value = self.peek(n)
        self.skip(n)
        return value

    def read_signed(self, n):
        """Next `n` bits as a two's-complement signed int."""
        value = self.read(n)
        if n and (value >> (n - 1)):
            value -= 1 << n
        return value

    def read_bit(self):
        return self.read(1)


class BitWriter:
    """MSB-first writer. Exists so the tests can encode a picture by hand and
    decode it back -- that round trip is the only check on these tables that
    does not depend on trusting the transcription."""

    __slots__ = ("_bits",)

    def __init__(self):
        self._bits = []

    def write(self, value, n):
        """`n` bits of `value`, most significant first."""
        if n < 0:
            raise ValueError("negative width")
        if n and not 0 <= value < (1 << n):
            raise ValueError("%d does not fit in %d bits" % (value, n))
        for shift in range(n - 1, -1, -1):
            self._bits.append((value >> shift) & 1)

    def write_signed(self, value, n):
        """`n` bits of two's complement."""
        lo, hi = -(1 << (n - 1)), (1 << (n - 1)) - 1
        if not lo <= value <= hi:
            raise ValueError("%d outside %d..%d" % (value, lo, hi))
        self.write(value & ((1 << n) - 1), n)

    def write_code(self, code):
        """A '0'/'1' string, as-is."""
        for ch in code:
            if ch == "0":
                self._bits.append(0)
            elif ch == "1":
                self._bits.append(1)
            else:
                raise ValueError("code %r is not binary" % code)

    def tell(self):
        return len(self._bits)

    def bytes(self, pad_bit=0):
        """Flush to bytes, padding the final byte with `pad_bit`."""
        bits = self._bits + [pad_bit] * ((-len(self._bits)) % 8)
        out = bytearray(len(bits) // 8)
        for i, bit in enumerate(bits):
            if bit:
                out[i >> 3] |= 0x80 >> (i & 7)
        return bytes(out)


# ---------------------------------------------------------------------------
# VLC decoding
# ---------------------------------------------------------------------------

class VLCDecoder:
    """A prefix code, expanded into a flat lookup table.

    Building the table IS the prefix-free check: two codes where one prefixes
    the other would try to claim the same slot, and that raises here rather
    than silently mis-decoding later. Nothing in this module can use a table
    that has not survived construction.
    """

    __slots__ = ("name", "table", "max_len", "min_len", "_lut")

    def __init__(self, table, name):
        if not table:
            raise ValueError("empty VLC table")
        self.name = name
        self.table = dict(table)
        self.max_len = max(len(c) for c in table)
        self.min_len = min(len(c) for c in table)
        lut = [None] * (1 << self.max_len)
        for code, payload in table.items():
            n = len(code)
            if n == 0:
                raise ValueError("%s: zero-length code" % name)
            base = int(code, 2) << (self.max_len - n)
            entry = (payload, n)
            for i in range(base, base + (1 << (self.max_len - n))):
                if lut[i] is not None:
                    raise ValueError(
                        "%s: code %r is not prefix-free (collides with %r)"
                        % (name, code, lut[i]))
                lut[i] = entry
        self._lut = lut

    def decode(self, reader):
        """Consume one code, return its payload. Raises UndefinedVLC."""
        entry = self._lut[reader.peek(self.max_len)]
        if entry is None:
            raise UndefinedVLC(self.name, reader.peek_bits(self.max_len),
                               reader.tell())
        payload, n = entry
        reader.skip(n)
        return payload

    def lookup(self, code_bits):
        """Payload for an exact code string, or None. Tests use this."""
        return self.table.get(code_bits)


DC_LUMA_VLC = VLCDecoder(T.DC_SIZE_LUMA, "dct_dc_size_luminance (B-12)")
DC_CHROMA_VLC = VLCDecoder(T.DC_SIZE_CHROMA, "dct_dc_size_chrominance (B-13)")
DC_VLC = (DC_CHROMA_VLC, DC_LUMA_VLC)          # index by is_luma

AC_VLC_B14 = VLCDecoder(T.AC_TABLE_B14, "DCT coefficients Table 0 (B-14)")
AC_VLC_B15 = VLCDecoder(T.AC_TABLE_B15, "DCT coefficients Table 1 (B-15)")
AC_VLC = (AC_VLC_B14, AC_VLC_B15)              # index by intra_vlc_format

MB_ADDR_VLC = VLCDecoder(T.MACROBLOCK_ADDRESS_INCREMENT,
                         "macroblock_address_increment (B-1)")


# ---------------------------------------------------------------------------
# 7.2.1  DC coefficient
# ---------------------------------------------------------------------------

def decode_dct_dc_differential(reader, is_luma):
    """dct_dc_size then dct_dc_differential -> the signed difference.

    7.2.1: a size of 0 means no difference bits follow. Otherwise `size` bits
    are read; a leading 0 marks a negative value, recovered by subtracting
    2**size - 1. That asymmetric coding is why you cannot just sign-extend.
    """
    size = DC_VLC[bool(is_luma)].decode(reader)
    if size == 0:
        return 0
    value = reader.read(size)
    if value < (1 << (size - 1)):
        value -= (1 << size) - 1
    return value


# ---------------------------------------------------------------------------
# 7.2.2  Blocks
# ---------------------------------------------------------------------------

def decode_block(reader, is_luma=True, pred_dc=0, intra=True,
                 intra_vlc_format=0, alternate_scan=0,
                 first_coefficient=False):
    """One block -> its 64 quantised coefficients QF, in RASTER order.

    reader             BitReader positioned at the start of the block
    is_luma            picks Table B-12 (True) or B-13 (False) for the DC
    pred_dc            DC predictor for this component; ignored when intra=False
    intra              intra block (DC coded separately) or not
    intra_vlc_format   0 -> Table B-14, 1 -> Table B-15 (intra only)
    alternate_scan     0 -> zigzag, 1 -> alternate scan
    first_coefficient  non-intra only: apply B-14's one-bit "1s" form, in
                       which a lone "1" means (run 0, level 1). Never used by
                       an intra block, so `.ipu` decoding leaves it False.

    The new DC predictor is simply the returned block's element 0 -- in MPEG-2
    the predictor IS the reconstructed quantised DC, so callers do
    ``pred = decode_block(...)[0]`` and need nothing else.

    Raises UndefinedVLC on an undefined code, CoefficientOverflow if a run
    walks past coefficient 63, ForbiddenValue on an illegal escape level, and
    EndOfBitstream if the data runs out mid-symbol.
    """
    scan = T.SCANS[1 if alternate_scan else 0]
    coeffs = [0] * 64

    if intra:
        table = AC_VLC[1 if intra_vlc_format else 0]
        dc = pred_dc + decode_dct_dc_differential(reader, is_luma)
        coeffs[0] = dc
        index = 1
    else:
        # Non-intra always uses B-14 regardless of intra_vlc_format (7.2.2.3).
        table = AC_VLC_B14
        index = 0
        if first_coefficient:
            # B-14's context-dependent head: a lone "1" is (0, 1) here,
            # because EOB cannot be the first code of a block.
            if reader.peek(1):
                reader.skip(1)
                level = -1 if reader.read_bit() else 1
                coeffs[scan[0]] = level
                index = 1

    while True:
        payload = table.decode(reader)
        # Ordered by frequency: a run/level pair is the overwhelmingly common
        # case, EOB happens once per block, escapes are rare.
        if type(payload) is tuple:
            run, level = payload
            if reader.read_bit():
                level = -level
        elif payload == T.EOB:
            break
        else:                                     # T.ESCAPE
            run = reader.read(T.ESCAPE_RUN_BITS)
            level = reader.read_signed(T.ESCAPE_LEVEL_BITS)
            # 7.2.2.3: both are reserved, and hitting one means the bitstream
            # is not being read the way it was written.
            if level == 0 or level == -2048:
                raise ForbiddenValue(
                    "escape level %d is forbidden (bit %d)"
                    % (level, reader.tell()), reader.tell())

        index += run
        if index > 63:
            # In practice THE way a wrong parameter set dies: on random data
            # this fires roughly 20x more often than an undefined VLC code.
            raise CoefficientOverflow(
                "run %d put the coefficient index at %d (max 63) at bit %d"
                % (run, index, reader.tell()), reader.tell())
        coeffs[scan[index]] = level
        index += 1

    return coeffs


def decode_intra_macroblock(reader, pred_dc, chroma_format="4:2:0",
                            intra_vlc_format=0, alternate_scan=0):
    """One intra macroblock -> (list of blocks, updated pred_dc).

    pred_dc is a 3-list [Y, Cb, Cr] of DC predictors, returned updated (a new
    list; the input is not mutated). Blocks come back in transmission order --
    see T.BLOCK_LAYOUTS -- each 64 quantised coefficients in raster order.

    This is deliberately only the macroblock: no macroblock_address_increment,
    no macroblock_type, no slice handling. What sits around a macroblock in a
    `.ipu` is exactly what is not yet known, so guessing it here would bake a
    guess into the reference.

    For `.pss` that layer IS now known -- `pssvideo.py` (rdr-fmt, REQ-066)
    carries program stream -> ES -> sequence -> picture -> slice -> macroblock
    and hands the blocks here. One thing it found that bites at this boundary:
    **`dct_type` is not optional.** A frame picture with `frame_pred_frame_dct`
    clear emits one bit per intra macroblock, between `macroblock_type` and
    `quantiser_scale_code`. Dropping it does not desync visibly -- it surfaced
    125 bits downstream as `CoefficientOverflow: run 4 put the coefficient
    index at 68`, a coefficient complaint about a header mistake.

    **The blocks come back in RASTER order. Do not call `inverse_scan` on
    them** -- see the warning there.
    """
    layout = T.BLOCK_LAYOUTS[chroma_format]
    component_index = {"Y": 0, "Cb": 1, "Cr": 2}
    pred = list(pred_dc)
    blocks = []
    for component in layout:
        ci = component_index[component]
        block = decode_block(reader, is_luma=(component == "Y"),
                             pred_dc=pred[ci], intra=True,
                             intra_vlc_format=intra_vlc_format,
                             alternate_scan=alternate_scan)
        pred[ci] = block[0]
        blocks.append(block)
    return blocks, pred


def inverse_scan(coeffs_in_scan_order, alternate_scan=0):
    """Scan order -> raster order.

    **Not for the output of `decode_block` or `decode_intra_macroblock`.**
    Those already return raster order -- they apply this internally -- so
    calling it on their blocks scans twice.

    The reason this warning is here rather than left to the reader: the second
    scan does not raise and does not produce obvious noise. It scatters the DC
    into a 2px checkerboard that renders as a plausible dark frame and passes
    any "did we get pixels back" check. rdr-fmt hit it building `pssvideo.py`
    and caught it only by decoding a picture whose content was known -- the
    Rockstar logo in GOP 7 of `RSLOGO.PSS` -- and looking at it. There is no
    way to detect the double scan from the data: 64 ints in, 64 ints out,
    valid either way. The call site is the only place it can be prevented.
    """
    scan = T.SCANS[1 if alternate_scan else 0]
    out = [0] * 64
    for i, value in enumerate(coeffs_in_scan_order):
        out[scan[i]] = value
    return out


def forward_scan(coeffs_in_raster_order, alternate_scan=0):
    """Raster order -> scan order. The encoder side, used by the tests."""
    scan = T.SCANS[1 if alternate_scan else 0]
    return [coeffs_in_raster_order[scan[i]] for i in range(64)]


# ---------------------------------------------------------------------------
# 7.4  Inverse quantisation
# ---------------------------------------------------------------------------

def dequantise_intra(qf, quant_matrix=None, quantiser_scale=None,
                     q_scale_code=None, q_scale_type=0,
                     intra_dc_precision=0, mismatch_control=True):
    """Quantised intra coefficients -> reconstructed DCT coefficients.

    qf                  64 quantised coefficients, raster order
    quant_matrix        64 weights, raster order (default: the standard intra
                        matrix)
    quantiser_scale     the scale itself, if already resolved
    q_scale_code        1..31, resolved through Table 7-6 when quantiser_scale
                        is not given
    q_scale_type        0 linear, 1 non-linear (Table 7-6)
    intra_dc_precision  0..3, sets the DC multiplier (8, 4, 2, 1)
    mismatch_control    apply 7.4.4 oddification (leave on for real streams)

    7.4.1 -- the DC term ignores both the matrix and the scale:
        F[0][0] = intra_dc_mult * QF[0][0]
    7.4.2.3 -- every AC term:
        F[v][u] = (2 * QF[v][u] * W[v][u] * quantiser_scale) / 32
    with "/" truncating toward zero, which is why the sign is pulled out
    rather than relying on Python's floor division.
    """
    if quant_matrix is None:
        quant_matrix = T.DEFAULT_INTRA_QUANTISER_MATRIX
    if len(quant_matrix) != 64:
        raise ValueError("quant matrix has %d entries, want 64"
                         % len(quant_matrix))
    if quantiser_scale is None:
        if q_scale_code is None:
            raise ValueError("give quantiser_scale or q_scale_code")
        if not 1 <= q_scale_code <= 31:
            raise ForbiddenValue("q_scale_code %d outside 1..31" % q_scale_code)
        quantiser_scale = T.QUANTISER_SCALE[1 if q_scale_type else 0][q_scale_code]
    if not 0 <= intra_dc_precision <= 3:
        raise ValueError("intra_dc_precision %d outside 0..3"
                         % intra_dc_precision)

    lo, hi = T.COEFF_MIN, T.COEFF_MAX
    out = [0] * 64
    out[0] = T.INTRA_DC_MULT[intra_dc_precision] * qf[0]
    if out[0] < lo:
        out[0] = lo
    elif out[0] > hi:
        out[0] = hi

    for i in range(1, 64):
        value = qf[i]
        if value == 0:
            continue
        magnitude = (2 * abs(value) * quant_matrix[i] * quantiser_scale) // 32
        result = -magnitude if value < 0 else magnitude
        if result < lo:
            result = lo
        elif result > hi:
            result = hi
        out[i] = result

    if mismatch_control:
        # 7.4.4: force the sum of all 64 coefficients odd by toggling the LSB
        # of the last one. Saturation above guarantees the toggle stays in
        # range (2047 is odd so it can only go down, -2048 even so only up).
        if (sum(out) & 1) == 0:
            if out[63] & 1:
                out[63] -= 1
            else:
                out[63] += 1

    return out


def quantise_intra(coeffs, quant_matrix=None, quantiser_scale=None,
                   q_scale_code=None, q_scale_type=0, intra_dc_precision=0):
    """The rough inverse of dequantise_intra, for building test bitstreams.

    Not a good encoder -- no rate control, no rounding strategy, no mismatch
    handling. It exists so a test can start from real DCT coefficients, get
    plausible quantised values, and round-trip those through the VLC layer.
    """
    if quant_matrix is None:
        quant_matrix = T.DEFAULT_INTRA_QUANTISER_MATRIX
    if quantiser_scale is None:
        quantiser_scale = T.QUANTISER_SCALE[1 if q_scale_type else 0][q_scale_code]
    out = [0] * 64
    out[0] = int(round(coeffs[0] / T.INTRA_DC_MULT[intra_dc_precision]))
    for i in range(1, 64):
        denom = 2 * quant_matrix[i] * quantiser_scale
        out[i] = int(round(coeffs[i] * 32.0 / denom))
    return out


# ---------------------------------------------------------------------------
# Annex A  Inverse DCT
# ---------------------------------------------------------------------------
# f(x,y) = 1/4 * SUM_u SUM_v C(u) C(v) F(u,v) cos((2x+1)u.pi/16) cos((2y+1)v.pi/16)
# with C(0) = 1/sqrt(2) and C(k) = 1 otherwise.
#
# Folding the 1/4 and both C() into one matrix M gives f = M . F . M^T.
#
# Note there is NO level shift here: MPEG-2 transforms 0..255 samples directly,
# unlike JPEG which centres them on zero first. A flat block of 128s has
# F[0][0] = 1024, and 1024/8 = 128 comes straight back out. Adding a +128 would
# be the classic way to get an image that is recognisable but wrong.

_IDCT_M = tuple(
    tuple(
        (math.cos((2 * x + 1) * u * math.pi / 16.0)
         * (math.sqrt(0.5) if u == 0 else 1.0) / 2.0)
        for u in range(8)
    )
    for x in range(8)
)

try:                                              # optional fast path
    import numpy as _np
    _IDCT_M_NP = _np.array(_IDCT_M, dtype=_np.float64)
except Exception:                                 # pragma: no cover
    _np = None
    _IDCT_M_NP = None


def idct_8x8(block, use_numpy=True):
    """64 DCT coefficients (raster) -> 64 spatial samples (raster), as floats.

    The pure-Python path is the definition; numpy is used when present purely
    for speed. verify_mpeg2.py asserts the two agree, so the fast path can
    never quietly drift from the reference.
    """
    if use_numpy and _np is not None:
        f = _np.asarray(block, dtype=_np.float64).reshape(8, 8)
        return (_IDCT_M_NP @ f @ _IDCT_M_NP.T).ravel().tolist()

    m = _IDCT_M
    # rows: tmp[v][x] = SUM_u M[x][u] * F[v][u]
    tmp = []
    for v in range(8):
        row = block[v * 8:v * 8 + 8]
        tmp.append([sum(m[x][u] * row[u] for u in range(8)) for x in range(8)])
    # columns: f[y][x] = SUM_v M[y][v] * tmp[v][x]
    out = [0.0] * 64
    for y in range(8):
        my = m[y]
        base = y * 8
        for x in range(8):
            out[base + x] = sum(my[v] * tmp[v][x] for v in range(8))
    return out


def fdct_8x8(samples):
    """Forward DCT, for tests only -- it is how a test block becomes
    coefficients that a real encoder might have produced."""
    m = _IDCT_M
    tmp = [[sum(m[x][u] * samples[y * 8 + x] for x in range(8))
            for u in range(8)] for y in range(8)]
    out = [0.0] * 64
    for v in range(8):
        for u in range(8):
            out[v * 8 + u] = sum(m[y][v] * tmp[y][u] for y in range(8))
    return out


def clamp_samples(values, lo=0, hi=255):
    """Round and clip IDCT output to sample range. Intra output is the sample
    itself, so this is the last step before it is a pixel."""
    out = []
    for v in values:
        i = int(math.floor(v + 0.5))
        out.append(lo if i < lo else (hi if i > hi else i))
    return out


# ---------------------------------------------------------------------------
# Colour conversion
# ---------------------------------------------------------------------------
# The PS2 IPU's CSC block outputs full-range RGB: Y 0..255 maps to 0..255, not
# the 16..235 studio swing. That is the documented libipu behaviour and matches
# the BT.601 / JFIF coefficients below. The studio-range variant is kept behind
# a flag because if a decoded .ipu ever comes out washed out or crushed, which
# range was assumed is the first thing to re-test.

def ycbcr_to_rgb(y, cb, cr, full_range=True):
    """One pixel. Inputs 0..255, output three ints 0..255."""
    if full_range:
        r = y + 1.402 * (cr - 128.0)
        g = y - 0.344136 * (cb - 128.0) - 0.714136 * (cr - 128.0)
        b = y + 1.772 * (cb - 128.0)
    else:
        yy = 1.164383 * (y - 16.0)
        r = yy + 1.596027 * (cr - 128.0)
        g = yy - 0.391762 * (cb - 128.0) - 0.812968 * (cr - 128.0)
        b = yy + 2.017232 * (cb - 128.0)
    out = []
    for v in (r, g, b):
        i = int(math.floor(v + 0.5))
        out.append(0 if i < 0 else (255 if i > 255 else i))
    return tuple(out)


def upsample_chroma(plane, width, height, factor_x, factor_y):
    """Nearest-neighbour chroma upsample to (width*factor_x, height*factor_y).

    Nearest neighbour, not the sited interpolation of 13818-2 Annex; for
    identifying an image that difference is invisible, and for anything
    stricter this is the place to change.
    """
    out_w = width * factor_x
    out = bytearray(out_w * height * factor_y)
    for y in range(height * factor_y):
        src = (y // factor_y) * width
        dst = y * out_w
        for x in range(out_w):
            out[dst + x] = plane[src + (x // factor_x)]
    return out


def ycbcr_planes_to_rgb(y_plane, cb_plane, cr_plane, width, height,
                        full_range=True):
    """Three full-size 0..255 planes -> packed RGB bytes, length w*h*3."""
    out = bytearray(width * height * 3)
    i = 0
    for p in range(width * height):
        r, g, b = ycbcr_to_rgb(y_plane[p], cb_plane[p], cr_plane[p],
                               full_range=full_range)
        out[i] = r
        out[i + 1] = g
        out[i + 2] = b
        i += 3
    return bytes(out)


# ---------------------------------------------------------------------------
# Encoding -- test support only
# ---------------------------------------------------------------------------

def _inverse_ac_table(table):
    """{(run, level): code} for one AC table."""
    return {payload: code for code, payload in table.items()
            if isinstance(payload, tuple)}


_INV_AC = (_inverse_ac_table(T.AC_TABLE_B14),
           _inverse_ac_table(T.AC_TABLE_B15))
_INV_DC = ({size: code for code, size in T.DC_SIZE_CHROMA.items()},
           {size: code for code, size in T.DC_SIZE_LUMA.items()})


def encode_block(writer, qf, is_luma=True, pred_dc=0,
                 intra_vlc_format=0, alternate_scan=0, force_escape=False):
    """Write one intra block's 64 quantised coefficients (raster order).

    Only exists so the self-test can build a bitstream with the same tables it
    decodes with. `force_escape` codes every AC coefficient as an escape, which
    is how the escape path gets exercised on ordinary values.
    """
    inv_ac = _INV_AC[1 if intra_vlc_format else 0]
    eob = T.AC_EOB_CODES[1 if intra_vlc_format else 0]
    esc = T.AC_ESCAPE_CODES[1 if intra_vlc_format else 0]

    diff = qf[0] - pred_dc
    size = 0 if diff == 0 else abs(diff).bit_length()
    if size > 11:
        raise ValueError("dc differential %d needs %d bits, table stops at 11"
                         % (diff, size))
    writer.write_code(_INV_DC[bool(is_luma)][size])
    if size:
        writer.write(diff if diff > 0 else diff + (1 << size) - 1, size)

    scanned = forward_scan(qf, alternate_scan)
    run = 0
    for index in range(1, 64):
        level = scanned[index]
        if level == 0:
            run += 1
            continue
        key = (run, abs(level))
        if not force_escape and key in inv_ac:
            writer.write_code(inv_ac[key])
            writer.write(1 if level < 0 else 0, 1)
        else:
            if not -2047 <= level <= 2047 or level == 0:
                raise ValueError("level %d cannot be escaped" % level)
            writer.write_code(esc)
            writer.write(run, T.ESCAPE_RUN_BITS)
            writer.write_signed(level, T.ESCAPE_LEVEL_BITS)
        run = 0
    writer.write_code(eob)
    return writer


# ---------------------------------------------------------------------------
# Table validation
# ---------------------------------------------------------------------------

def is_prefix_free(codes):
    """True if no code in `codes` is a prefix of another. O(n * maxlen)."""
    codeset = set(codes)
    for code in codeset:
        for cut in range(1, len(code)):
            if code[:cut] in codeset:
                return False
    return True


def kraft_sum(lengths):
    """SUM 2**-len, exactly, as a Fraction.

    1 means the code is COMPLETE: every infinite bit string starts with some
    code, so nothing can ever be undefined. Less than 1 means part of the code
    space is unassigned. More than 1 is impossible for a prefix code, so a
    result above 1 is proof of a transcription error on its own.
    """
    total = Fraction(0)
    for n in lengths:
        total += Fraction(1, 1 << n)
    return total


#: The (run, level) pairs both AC tables code, as run -> highest level.
#: Table B-14 and B-15 code the same 111 pairs; only the codes differ.
AC_PAIR_SHAPE = dict([(0, 40), (1, 18), (2, 5), (3, 4)]
                     + [(r, 3) for r in (4, 5, 6)]
                     + [(r, 2) for r in range(7, 17)]
                     + [(r, 1) for r in range(17, 32)])


def _ac_signed_lengths(table, eob, escape):
    """Code lengths as a decoder actually consumes them: run/level codes carry
    a sign bit after the VLC, EOB carries nothing. The escape prefix is counted
    at its own length -- the 6+12 bits that follow it are fixed-length and fill
    its subtree exactly, so they neither add to nor subtract from the sum."""
    lengths = [len(c) + 1 for c, p in table.items() if isinstance(p, tuple)]
    return lengths + [len(eob), len(escape)]


def validate_tables():
    """Structural checks over every table, returned as a report.

    These cannot prove the tables say what ISO/IEC 13818-2 says -- nothing
    local can prove that. They catch the failure that actually happens, which
    is a mistyped, dropped or duplicated entry: a wrong length moves the Kraft
    sum, a wrong prefix breaks prefix-freeness, a dropped row breaks the count,
    and a wrong short code breaks the B-14/B-15 cross-check.

    Two expectations that are easy to get backwards, and are asserted in the
    right direction here:

    * B-12 and B-13 ARE complete codes (Kraft exactly 1). That is how the
      length of the size-11 entry gets pinned down -- guess a bit too many and
      the sum falls short.
    * B-14 and B-15 are deliberately INCOMPLETE. Roughly a third of the code
      space is undefined, and that is not a defect: it is the entire reason a
      wrong picture-parameter guess dies within a few symbols instead of
      producing plausible garbage forever. Asserting completeness here would
      be asserting that the format has no error detection.
    """
    report = []

    def add(name, codes, expect_kraft=None, expect_count=None):
        lengths = [len(c) for c in codes]
        row = {
            "table": name,
            "codes": len(codes),
            "min_bits": min(lengths),
            "max_bits": max(lengths),
            "prefix_free": is_prefix_free(codes),
            "kraft": kraft_sum(lengths),
        }
        row["complete"] = row["kraft"] == 1
        row["ok"] = (row["prefix_free"]
                     and (expect_kraft is None or row["kraft"] == expect_kraft)
                     and (expect_count is None or row["codes"] == expect_count))
        report.append(row)
        return row

    add("B-12 dct_dc_size_luminance", list(T.DC_SIZE_LUMA),
        expect_kraft=Fraction(1), expect_count=12)
    add("B-13 dct_dc_size_chrominance", list(T.DC_SIZE_CHROMA),
        expect_kraft=Fraction(1), expect_count=12)

    for name, table, eob, esc in (
            ("B-14 DCT coefficients Table 0", T.AC_TABLE_B14,
             T.B14_EOB, T.B14_ESCAPE),
            ("B-15 DCT coefficients Table 1", T.AC_TABLE_B15,
             T.B15_EOB, T.B15_ESCAPE)):
        row = add(name, list(table), expect_count=113)
        row["kraft_with_sign"] = kraft_sum(_ac_signed_lengths(table, eob, esc))
        # The rate that actually matters: the share of bit patterns that match
        # no code at all, which is what a wrong guess has to trip over. It is
        # 1 - kraft on the BARE codes; the sign bit is always readable and
        # never rejects anything, so counting it here would flatter the number
        # by more than an order of magnitude.
        row["unassigned_code_space"] = 1 - row["kraft"]
        pairs = [p for p in table.values() if isinstance(p, tuple)]
        row["run_level_pairs"] = len(pairs)
        shape = {}
        for run, level in pairs:
            shape[run] = max(shape.get(run, 0), level)
        row["pair_shape_ok"] = (shape == AC_PAIR_SHAPE
                                and len(set(pairs)) == 111)
        row["ok"] = (row["prefix_free"] and row["codes"] == 113
                     and row["run_level_pairs"] == 111
                     and row["pair_shape_ok"]
                     and row["unassigned_code_space"] > 0)

    # The strongest check available without the standard in hand: B-14 and
    # B-15 were transcribed independently, yet they must agree exactly on
    # every code 12 bits or longer and must code an identical set of pairs.
    b14 = {p: c for c, p in T.AC_TABLE_B14.items() if isinstance(p, tuple)}
    b15 = {p: c for c, p in T.AC_TABLE_B15.items() if isinstance(p, tuple)}
    long_pairs = [p for p in b15 if len(b15[p]) >= 12]
    cross = {
        "table": "B-14 vs B-15 cross-check",
        "same_pairs": set(b14) == set(b15),
        "long_codes": len(long_pairs),
        "long_disagreements": sum(1 for p in long_pairs if b14[p] != b15[p]),
        "codes_differing": sum(1 for p in b14 if b14[p] != b15[p]),
    }
    cross["ok"] = (cross["same_pairs"] and cross["long_disagreements"] == 0
                   and cross["long_codes"] == 70
                   and cross["codes_differing"] == 39)
    report.append(cross)

    add("B-1 macroblock_address_increment",
        list(T.MACROBLOCK_ADDRESS_INCREMENT), expect_count=35)

    # Scans must be permutations of 0..63, and must differ from each other.
    scans_ok = (sorted(T.ZIGZAG_SCAN) == list(range(64))
                and sorted(T.ALTERNATE_SCAN) == list(range(64))
                and T.ZIGZAG_SCAN != T.ALTERNATE_SCAN)
    report.append({"table": "scan orders", "ok": scans_ok,
                   "zigzag_is_permutation": sorted(T.ZIGZAG_SCAN) == list(range(64)),
                   "alternate_is_permutation":
                       sorted(T.ALTERNATE_SCAN) == list(range(64))})

    # Quantiser scale tables: 32 entries (index 0 unused), strictly increasing.
    qs_ok = True
    for name, tbl in (("linear", T.QUANTISER_SCALE_LINEAR),
                      ("non-linear", T.QUANTISER_SCALE_NONLINEAR)):
        vals = list(tbl[1:])
        qs_ok = qs_ok and len(tbl) == 32 and vals == sorted(set(vals))
    report.append({"table": "quantiser_scale (Table 7-6)", "ok": qs_ok,
                   "linear_max": T.QUANTISER_SCALE_LINEAR[31],
                   "nonlinear_max": T.QUANTISER_SCALE_NONLINEAR[31]})

    return report


def main():                                       # pragma: no cover
    ok = True
    for row in validate_tables():
        ok = ok and row["ok"]
        if "codes" in row:
            line = ("%-34s %3d codes  %2d-%2d bits  prefix-free=%-5s  kraft=%s"
                    % (row["table"], row["codes"], row["min_bits"],
                       row["max_bits"], row["prefix_free"], row["kraft"]))
            if "unassigned_code_space" in row:
                line += ("  unassigned=%.4f%%"
                         % (100.0 * float(row["unassigned_code_space"])))
        else:
            line = "%-34s %s" % (row["table"], {k: v for k, v in row.items()
                                                if k not in ("table", "ok")})
        print("%s  [%s]" % (line, "ok" if row["ok"] else "FAIL"))
    print("\n%s" % ("all table checks passed" if ok else "TABLE CHECKS FAILED"))
    print("run scripts/verify_mpeg2.py for the encode/decode round trip")
    return 0 if ok else 1


if __name__ == "__main__":                        # pragma: no cover
    import sys
    sys.exit(main())
