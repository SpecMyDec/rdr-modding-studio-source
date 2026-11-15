"""ISO/IEC 13818-2 (MPEG-2 video) coding tables, as plain data.

Written for `.ipu` -- the PS2 Image Processing Unit still images (see ipu.py).
Those files carry a 16-byte header and then raw macroblock data with no
sequence or picture header, so a decoder has to supply the picture parameters
itself. This module is the parameter-free half of that job: the tables, exactly
as the standard defines them, with nothing game-specific in it.

PROVENANCE, because these tables are the one thing here that cannot be checked
against the disc:

* Every table below was written out from ISO/IEC 13818-2 and then cross-checked,
  entry by entry, against FFmpeg's `libavcodec/mpeg12data.c` (`ff_mpeg1_vlc_table`,
  `ff_mpeg2_vlc_table`, `ff_mpeg12_run`, `ff_mpeg12_level`,
  `ff_mpeg12_vlc_dc_lum_*`, `ff_mpeg12_vlc_dc_chroma_*`, `ff_mpeg12_mbAddrIncrTable`).
* The AC tables (B-14, B-15) were then *machine-generated* from those (code, length)
  pairs rather than retyped, so the binary strings cannot contain a typo that the
  numeric source does not.
* `verify_mpeg2.py` re-derives the structural invariants at test time:
  prefix-freeness, Kraft equality, entry counts, and the B-14/B-15 agreement on
  long codes. Those checks are what actually stand behind the tables; the
  provenance note only says where they came from.

Two transcription errors were caught this way and are worth recording, since the
obvious guess is wrong in both cases:

* B-12 (luma DC) size 11 is ``111111111`` -- NINE bits, the same length as size
  10, not a tenth `1` followed by `0`. The Kraft sum is exactly 1 only this way.
* B-13 (chroma DC) size 11 is ``1111111111`` -- TEN bits, likewise.

WHAT THE CHECKS CANNOT SEE, because the shape of the remaining risk is worth
more than a blanket claim of correctness. Swapping two codes of the SAME LENGTH
within one table is invisible to every check available locally: the code count,
the Kraft sum and prefix-freeness are all unchanged, and a round trip still
passes because the encoder and the decoder consult the same wrong table. The
B-14/B-15 cross-check catches such a swap for any pair whose code is 12 bits or
longer -- 80 of 111 pairs in B-14, 70 of 111 in B-15 -- because the two tables
come from separate source arrays and must agree there. Below 12 bits, 29 codes
in B-14 and 39 in B-15 sit in same-length groups where a swap would go
undetected. A mutation test (10 injected errors, 9 caught) confirms that is the
one surviving class, and it is why the AC tables were generated mechanically
from numeric (code, length) arrays rather than retyped: the checks cannot cover
that gap, so the process has to.

Conventions used throughout:

* A "code" is a string of ``'0'``/``'1'``, most-significant bit first, in the
  order the bits appear in the stream.
* AC codes do NOT include the trailing sign bit. The decoder reads the VLC, and
  then, if it resolved to a run/level pair, reads one more bit: ``0`` positive,
  ``1`` negative. That is why the bare codes are the ones that must be
  prefix-free, and why the Kraft sum counts run/level codes as length + 1.
* Scan tables map SCAN POSITION -> RASTER INDEX (``raster = SCAN[i]``), which is
  the direction a decoder needs.
* Quantiser matrices are in RASTER order, 8x8 row-major.
"""

from typing import Any

# ---------------------------------------------------------------------------
# B.5.1  DC coefficients: Tables B-12 (luminance) and B-13 (chrominance)
# ---------------------------------------------------------------------------
# code -> dct_dc_size. Sizes run 0..11; size 11 is needed only at
# intra_dc_precision 3 (11-bit DC), but all four precisions share one table.

DC_SIZE_LUMA = {
    "100":       0,
    "00":        1,
    "01":        2,
    "101":       3,
    "110":       4,
    "1110":      5,
    "11110":     6,
    "111110":    7,
    "1111110":   8,
    "11111110":  9,
    "111111110": 10,
    "111111111": 11,
}

DC_SIZE_CHROMA = {
    "00":         0,
    "01":         1,
    "10":         2,
    "110":        3,
    "1110":       4,
    "11110":      5,
    "111110":     6,
    "1111110":    7,
    "11111110":   8,
    "111111110":  9,
    "1111111110": 10,
    "1111111111": 11,
}

# ---------------------------------------------------------------------------
# B.5.2  AC coefficients: Tables B-14 and B-15
# ---------------------------------------------------------------------------
# B-14 ("DCT coefficients Table 0") is used for every non-intra block, and for
# intra blocks when intra_vlc_format == 0.
# B-15 ("DCT coefficients Table 1") is used for intra blocks when
# intra_vlc_format == 1. It exists only in MPEG-2; MPEG-1 has no equivalent.
#
# Both tables code the SAME 111 (run, level) pairs -- run 0 levels 1..40, run 1
# levels 1..18, run 2 levels 1..5, run 3 levels 1..4, runs 4..6 levels 1..3,
# runs 7..16 levels 1..2, runs 17..31 level 1 -- and differ only in which code
# each pair gets. 39 of the 111 codes differ; the other 72, all of them 12 bits
# or longer, are shared verbatim. verify_mpeg2.py asserts exactly that.

# Table B-14 run/level entries: (vlc, run, level). 111 of them.
B14_RL = (
    ("11",                0,  1),
    ("0100",              0,  2),
    ("00101",             0,  3),
    ("0000110",           0,  4),
    ("00100110",          0,  5),
    ("00100001",          0,  6),
    ("0000001010",        0,  7),
    ("000000011101",      0,  8),
    ("000000011000",      0,  9),
    ("000000010011",      0, 10),
    ("000000010000",      0, 11),
    ("0000000011010",     0, 12),
    ("0000000011001",     0, 13),
    ("0000000011000",     0, 14),
    ("0000000010111",     0, 15),
    ("00000000011111",    0, 16),
    ("00000000011110",    0, 17),
    ("00000000011101",    0, 18),
    ("00000000011100",    0, 19),
    ("00000000011011",    0, 20),
    ("00000000011010",    0, 21),
    ("00000000011001",    0, 22),
    ("00000000011000",    0, 23),
    ("00000000010111",    0, 24),
    ("00000000010110",    0, 25),
    ("00000000010101",    0, 26),
    ("00000000010100",    0, 27),
    ("00000000010011",    0, 28),
    ("00000000010010",    0, 29),
    ("00000000010001",    0, 30),
    ("00000000010000",    0, 31),
    ("000000000011000",   0, 32),
    ("000000000010111",   0, 33),
    ("000000000010110",   0, 34),
    ("000000000010101",   0, 35),
    ("000000000010100",   0, 36),
    ("000000000010011",   0, 37),
    ("000000000010010",   0, 38),
    ("000000000010001",   0, 39),
    ("000000000010000",   0, 40),
    ("011",               1,  1),
    ("000110",            1,  2),
    ("00100101",          1,  3),
    ("0000001100",        1,  4),
    ("000000011011",      1,  5),
    ("0000000010110",     1,  6),
    ("0000000010101",     1,  7),
    ("000000000011111",   1,  8),
    ("000000000011110",   1,  9),
    ("000000000011101",   1, 10),
    ("000000000011100",   1, 11),
    ("000000000011011",   1, 12),
    ("000000000011010",   1, 13),
    ("000000000011001",   1, 14),
    ("0000000000010011",  1, 15),
    ("0000000000010010",  1, 16),
    ("0000000000010001",  1, 17),
    ("0000000000010000",  1, 18),
    ("0101",              2,  1),
    ("0000100",           2,  2),
    ("0000001011",        2,  3),
    ("000000010100",      2,  4),
    ("0000000010100",     2,  5),
    ("00111",             3,  1),
    ("00100100",          3,  2),
    ("000000011100",      3,  3),
    ("0000000010011",     3,  4),
    ("00110",             4,  1),
    ("0000001111",        4,  2),
    ("000000010010",      4,  3),
    ("000111",            5,  1),
    ("0000001001",        5,  2),
    ("0000000010010",     5,  3),
    ("000101",            6,  1),
    ("000000011110",      6,  2),
    ("0000000000010100",  6,  3),
    ("000100",            7,  1),
    ("000000010101",      7,  2),
    ("0000111",           8,  1),
    ("000000010001",      8,  2),
    ("0000101",           9,  1),
    ("0000000010001",     9,  2),
    ("00100111",         10,  1),
    ("0000000010000",    10,  2),
    ("00100011",         11,  1),
    ("0000000000011010", 11,  2),
    ("00100010",         12,  1),
    ("0000000000011001", 12,  2),
    ("00100000",         13,  1),
    ("0000000000011000", 13,  2),
    ("0000001110",       14,  1),
    ("0000000000010111", 14,  2),
    ("0000001101",       15,  1),
    ("0000000000010110", 15,  2),
    ("0000001000",       16,  1),
    ("0000000000010101", 16,  2),
    ("000000011111",     17,  1),
    ("000000011010",     18,  1),
    ("000000011001",     19,  1),
    ("000000010111",     20,  1),
    ("000000010110",     21,  1),
    ("0000000011111",    22,  1),
    ("0000000011110",    23,  1),
    ("0000000011101",    24,  1),
    ("0000000011100",    25,  1),
    ("0000000011011",    26,  1),
    ("0000000000011111", 27,  1),
    ("0000000000011110", 28,  1),
    ("0000000000011101", 29,  1),
    ("0000000000011100", 30,  1),
    ("0000000000011011", 31,  1),
)
B14_EOB = "10"
B14_ESCAPE = "000001"

# Table B-15 run/level entries: (vlc, run, level). 111 of them.
B15_RL = (
    ("10",                0,  1),
    ("110",               0,  2),
    ("0111",              0,  3),
    ("11100",             0,  4),
    ("11101",             0,  5),
    ("000101",            0,  6),
    ("000100",            0,  7),
    ("1111011",           0,  8),
    ("1111100",           0,  9),
    ("00100011",          0, 10),
    ("00100010",          0, 11),
    ("11111010",          0, 12),
    ("11111011",          0, 13),
    ("11111110",          0, 14),
    ("11111111",          0, 15),
    ("00000000011111",    0, 16),
    ("00000000011110",    0, 17),
    ("00000000011101",    0, 18),
    ("00000000011100",    0, 19),
    ("00000000011011",    0, 20),
    ("00000000011010",    0, 21),
    ("00000000011001",    0, 22),
    ("00000000011000",    0, 23),
    ("00000000010111",    0, 24),
    ("00000000010110",    0, 25),
    ("00000000010101",    0, 26),
    ("00000000010100",    0, 27),
    ("00000000010011",    0, 28),
    ("00000000010010",    0, 29),
    ("00000000010001",    0, 30),
    ("00000000010000",    0, 31),
    ("000000000011000",   0, 32),
    ("000000000010111",   0, 33),
    ("000000000010110",   0, 34),
    ("000000000010101",   0, 35),
    ("000000000010100",   0, 36),
    ("000000000010011",   0, 37),
    ("000000000010010",   0, 38),
    ("000000000010001",   0, 39),
    ("000000000010000",   0, 40),
    ("010",               1,  1),
    ("00110",             1,  2),
    ("1111001",           1,  3),
    ("00100111",          1,  4),
    ("00100000",          1,  5),
    ("0000000010110",     1,  6),
    ("0000000010101",     1,  7),
    ("000000000011111",   1,  8),
    ("000000000011110",   1,  9),
    ("000000000011101",   1, 10),
    ("000000000011100",   1, 11),
    ("000000000011011",   1, 12),
    ("000000000011010",   1, 13),
    ("000000000011001",   1, 14),
    ("0000000000010011",  1, 15),
    ("0000000000010010",  1, 16),
    ("0000000000010001",  1, 17),
    ("0000000000010000",  1, 18),
    ("00101",             2,  1),
    ("0000111",           2,  2),
    ("11111100",          2,  3),
    ("0000001100",        2,  4),
    ("0000000010100",     2,  5),
    ("00111",             3,  1),
    ("00100110",          3,  2),
    ("000000011100",      3,  3),
    ("0000000010011",     3,  4),
    ("000110",            4,  1),
    ("11111101",          4,  2),
    ("000000010010",      4,  3),
    ("000111",            5,  1),
    ("000000100",         5,  2),
    ("0000000010010",     5,  3),
    ("0000110",           6,  1),
    ("000000011110",      6,  2),
    ("0000000000010100",  6,  3),
    ("0000100",           7,  1),
    ("000000010101",      7,  2),
    ("0000101",           8,  1),
    ("000000010001",      8,  2),
    ("1111000",           9,  1),
    ("0000000010001",     9,  2),
    ("1111010",          10,  1),
    ("0000000010000",    10,  2),
    ("00100001",         11,  1),
    ("0000000000011010", 11,  2),
    ("00100101",         12,  1),
    ("0000000000011001", 12,  2),
    ("00100100",         13,  1),
    ("0000000000011000", 13,  2),
    ("000000101",        14,  1),
    ("0000000000010111", 14,  2),
    ("000000111",        15,  1),
    ("0000000000010110", 15,  2),
    ("0000001101",       16,  1),
    ("0000000000010101", 16,  2),
    ("000000011111",     17,  1),
    ("000000011010",     18,  1),
    ("000000011001",     19,  1),
    ("000000010111",     20,  1),
    ("000000010110",     21,  1),
    ("0000000011111",    22,  1),
    ("0000000011110",    23,  1),
    ("0000000011101",    24,  1),
    ("0000000011100",    25,  1),
    ("0000000011011",    26,  1),
    ("0000000000011111", 27,  1),
    ("0000000000011110", 28,  1),
    ("0000000000011101", 29,  1),
    ("0000000000011100", 30,  1),
    ("0000000000011011", 31,  1),
)
B15_EOB = "0110"
B15_ESCAPE = "000001"


# B-14 has one context-dependent code. In a NON-INTRA block the first
# coefficient cannot be an end-of-block, so the bit that would otherwise start
# EOB ("10") or (0,1) ("11") is instead read as a single "1" meaning (0, 1),
# with the sign bit after it. Intra blocks never use this: their DC comes from
# Table B-12/B-13, so the first AC code is read normally. B-15 has no such case.
B14_FIRST_COEFF_CODE = "1"
B14_FIRST_COEFF_RL = (0, 1)

# Escape format. In MPEG-2 an escape is the 6-bit prefix, then 6 bits of run,
# then 12 bits of level as two's complement. Level 0 and level -2048 are
# forbidden. (MPEG-1 used an 8-bit level with a 16-bit second-level escape;
# it is not used here and is not implemented.)
ESCAPE_RUN_BITS = 6
ESCAPE_LEVEL_BITS = 12

# Sentinels stored as the values of the two non-(run, level) codes.
EOB = "EOB"
ESCAPE = "ESCAPE"


def _ac_table(entries, eob, escape):
    """{code: (run, level)} plus the two sentinels, with a duplicate check."""
    table: dict[str, Any] = {}
    for code, run, level in entries:
        if code in table:
            raise ValueError("duplicate AC code %r" % code)
        table[code] = (run, level)
    for code, marker in ((eob, EOB), (escape, ESCAPE)):
        if code in table:
            raise ValueError("%s code %r collides with a run/level entry"
                             % (marker, code))
        table[code] = marker
    return table


#: Table B-14 as ``{code: (run, level) | EOB | ESCAPE}``. 113 codes.
AC_TABLE_B14 = _ac_table(B14_RL, B14_EOB, B14_ESCAPE)

#: Table B-15 as ``{code: (run, level) | EOB | ESCAPE}``. 113 codes.
AC_TABLE_B15 = _ac_table(B15_RL, B15_EOB, B15_ESCAPE)

#: Indexed by intra_vlc_format.
AC_TABLES = (AC_TABLE_B14, AC_TABLE_B15)
AC_EOB_CODES = (B14_EOB, B15_EOB)
AC_ESCAPE_CODES = (B14_ESCAPE, B15_ESCAPE)

# ---------------------------------------------------------------------------
# B.1  macroblock_address_increment (Table B-1)
# ---------------------------------------------------------------------------
# Not needed to decode a block, but it is what sits between slices and
# macroblocks, so a parameter search that has to decide whether the bitstream
# starts with a macroblock address needs it.
#
# Values are the increment 1..33, or one of the two markers. macroblock_escape
# means "add 33 and read another macroblock_address_increment". Stuffing is
# MPEG-1 only -- a compliant MPEG-2 stream must not contain it -- and is listed
# so a decoder can recognise it and say so rather than desynchronising.

MB_ESCAPE = "MB_ESCAPE"
MB_STUFFING = "MB_STUFFING"

MACROBLOCK_ADDRESS_INCREMENT = {
    "1":           1,
    "011":         2,
    "010":         3,
    "0011":        4,
    "0010":        5,
    "00011":       6,
    "00010":       7,
    "0000111":     8,
    "0000110":     9,
    "00001011":   10,
    "00001010":   11,
    "00001001":   12,
    "00001000":   13,
    "00000111":   14,
    "00000110":   15,
    "0000010111": 16,
    "0000010110": 17,
    "0000010101": 18,
    "0000010100": 19,
    "0000010011": 20,
    "0000010010": 21,
    "00000100011": 22,
    "00000100010": 23,
    "00000100001": 24,
    "00000100000": 25,
    "00000011111": 26,
    "00000011110": 27,
    "00000011101": 28,
    "00000011100": 29,
    "00000011011": 30,
    "00000011010": 31,
    "00000011001": 32,
    "00000011000": 33,
    "00000001000": MB_ESCAPE,
    "00000001111": MB_STUFFING,
}

# ---------------------------------------------------------------------------
# 7.3  Quantiser matrices (default values, clause 6.3.11 / Table 7-4, 7-5)
# ---------------------------------------------------------------------------
# Raster order, 8x8 row-major. A stream may replace either with a custom matrix
# via load_intra_quantiser_matrix / load_non_intra_quantiser_matrix, which are
# transmitted in ZIGZAG order -- use ZIGZAG_SCAN to place them.

DEFAULT_INTRA_QUANTISER_MATRIX = (
     8, 16, 19, 22, 26, 27, 29, 34,
    16, 16, 22, 24, 27, 29, 34, 37,
    19, 22, 26, 27, 29, 34, 34, 38,
    22, 22, 26, 27, 29, 34, 37, 40,
    22, 26, 27, 29, 32, 35, 40, 48,
    26, 27, 29, 32, 35, 40, 48, 58,
    26, 27, 29, 34, 38, 46, 56, 69,
    27, 29, 35, 38, 46, 56, 69, 83,
)

DEFAULT_NON_INTRA_QUANTISER_MATRIX = tuple([16] * 64)

# ---------------------------------------------------------------------------
# 7.3  Scan patterns (Figure 7-2 / Figure 7-3)
# ---------------------------------------------------------------------------
# SCAN[i] is the RASTER index of the i-th coefficient in coding order, so
# inverse scan is ``raster[SCAN[i]] = coeff[i]``.
#
# alternate_scan is a picture-coding-extension bit. MPEG-1 has only the zigzag.

ZIGZAG_SCAN = (
     0,  1,  8, 16,  9,  2,  3, 10,
    17, 24, 32, 25, 18, 11,  4,  5,
    12, 19, 26, 33, 40, 48, 41, 34,
    27, 20, 13,  6,  7, 14, 21, 28,
    35, 42, 49, 56, 57, 50, 43, 36,
    29, 22, 15, 23, 30, 37, 44, 51,
    58, 59, 52, 45, 38, 31, 39, 46,
    53, 60, 61, 54, 47, 55, 62, 63,
)

ALTERNATE_SCAN = (
     0,  8, 16, 24,  1,  9,  2, 10,
    17, 25, 32, 40, 48, 56, 57, 49,
    41, 33, 26, 18,  3, 11,  4, 12,
    19, 27, 34, 42, 50, 58, 35, 43,
    51, 59, 20, 28,  5, 13,  6, 14,
    21, 29, 36, 44, 52, 60, 37, 45,
    53, 61, 22, 30,  7, 15, 23, 31,
    38, 46, 54, 62, 39, 47, 55, 63,
)

#: Indexed by alternate_scan.
SCANS = (ZIGZAG_SCAN, ALTERNATE_SCAN)

# ---------------------------------------------------------------------------
# 7.4.2.2  quantiser_scale (Table 7-6)
# ---------------------------------------------------------------------------
# Index by q_scale_code 1..31; index 0 is a placeholder, q_scale_code 0 is
# forbidden. q_scale_type 0 is the linear table (MPEG-1 compatible), 1 is the
# non-linear one, chosen by a bit in the picture coding extension.

QUANTISER_SCALE_LINEAR = (
    0,
     2,  4,  6,  8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 32,
    34, 36, 38, 40, 42, 44, 46, 48, 50, 52, 54, 56, 58, 60, 62,
)

QUANTISER_SCALE_NONLINEAR = (
    0,
     1,  2,  3,  4,  5,  6,  7,  8, 10, 12, 14, 16, 18, 20, 22, 24,
    28, 32, 36, 40, 44, 48, 56, 64, 72, 80, 88, 96, 104, 112, 120,
)

#: Indexed by q_scale_type.
QUANTISER_SCALE = (QUANTISER_SCALE_LINEAR, QUANTISER_SCALE_NONLINEAR)

# ---------------------------------------------------------------------------
# 7.4.1  Intra DC
# ---------------------------------------------------------------------------
# intra_dc_precision -> DC bit depth, dequantiser multiplier, and the value the
# DC predictors are reset to at the start of every slice.

INTRA_DC_PRECISION_BITS = (8, 9, 10, 11)
INTRA_DC_MULT = (8, 4, 2, 1)
INTRA_DC_PRED_RESET = (128, 256, 512, 1024)

# ---------------------------------------------------------------------------
# 6.3.17 / Table 6-3  Block layout within a macroblock
# ---------------------------------------------------------------------------
# Block index -> "Y", "Cb" or "Cr", in transmission order. 4:2:0 sends 6 blocks,
# 4:2:2 sends 8, 4:4:4 sends 12. Which one an .ipu uses is not stated in the
# file and is one of the parameters a search has to settle.

BLOCK_LAYOUT_420 = ("Y", "Y", "Y", "Y", "Cb", "Cr")
BLOCK_LAYOUT_422 = ("Y", "Y", "Y", "Y", "Cb", "Cr", "Cb", "Cr")
BLOCK_LAYOUT_444 = ("Y", "Y", "Y", "Y", "Cb", "Cr", "Cb", "Cr", "Cb", "Cr")

BLOCK_LAYOUTS = {
    "4:2:0": BLOCK_LAYOUT_420,
    "4:2:2": BLOCK_LAYOUT_422,
    "4:4:4": BLOCK_LAYOUT_444,
}

# Chroma subsampling factors (horizontal, vertical) per chroma format.
CHROMA_SUBSAMPLING = {
    "4:2:0": (2, 2),
    "4:2:2": (2, 1),
    "4:4:4": (1, 1),
}

# Coefficient saturation after dequantisation (7.4.3).
COEFF_MIN = -2048
COEFF_MAX = 2047
