"""Decode a `.ipu` still image to RGB pixels.

The format is fully specified in FORMAT_NOTES; the short version is that a
`.ipu` is one MPEG-2 intra picture with its coding parameters supplied out of
band, which is why it cannot simply be renamed and opened.

Everything the file does not state was recovered from the game's own decoder
at ELF 0x003B23A0 and is hard-coded here as a default:

    intra_dc_precision  0        DC predictors reset to 128, dc scaler 8
    q_scale_code        1        ORed into the IDEC command word as 0x00010000
    q_scale_type        0        linear, so quantiser_scale = 2
    intra_vlc_format    0        DCT coefficients use Table B-14
    alternate_scan      0        zigzag
    chroma              4:2:0    6 blocks per macroblock, fixed by IDEC itself
    intra matrix        MPEG-2 default (stored zigzagged at ELF 0x004FB9E0)

Three details are easy to get wrong and each one is fatal:

1. **The bitstream starts at BIT offset 8**, not byte 0x10. The first byte of
   the payload is a picture-coding flags byte that the game peeks with FDEC
   and shifts into IPU_CTRL bits 16..23; it is then consumed. Starting at bit
   0 decodes 0 of 63 files.
2. **There is no macroblock_address_increment before macroblock 0.** IDEC
   begins with macroblock_type. MBA appears only *between* macroblocks.
   Reading an MBA first decodes 24 of 63 files -- a half-success, which is the
   dangerous kind.
3. The flags byte is 0x00 in all 63 shipped files, so every flag sits at its
   default. It is still read here rather than assumed, because a re-encoder
   could legitimately set it and silently reading it as zero would be wrong.

What is NOT settled by the fact that this decodes: of the seven parameters,
only intra_vlc_format and the chroma format change how many bits are consumed.
intra_dc_precision, q_scale_code, q_scale_type, alternate_scan and the
quantiser matrix are invisible to the parse -- they only affect reconstruction.
If an image comes out with wrong brightness or contrast, look there first; the
parse will still report a complete picture. `scripts/verify_ipu.py` is the
check that can see those, because it compares against a screenshot of the
running game.
"""
from . import ipu
from . import mpeg2intra as M
from . import mpeg2tables as T

# Recovered from the IDEC command word, not guessed. See the module docstring.
DEFAULTS = dict(intra_dc_precision=0, q_scale_code=1, q_scale_type=0,
                intra_vlc_format=0, alternate_scan=0)

FLAG_BITS = ("mpeg1", "q_scale_type", "intra_vlc_format", "alternate_scan",
             "reserved", "dct_type_in_stream", "idp1", "idp0")


class IpuUndecodable(ValueError):
    pass


def read_macroblock_type(reader):
    """Table B-2 for an I-picture. Returns the new quantiser_scale_code, or 0.

    Two codes only: '1' is Intra, '01' is Intra+Quant and is followed by a
    5-bit quantiser_scale_code. This is read for EVERY macroblock including
    the first -- `decode_intra_macroblock` deliberately does not do it, and
    omitting it here decoded 28 of 896 macroblocks before dying, which looks
    enough like progress to be misleading.
    """
    at = reader.tell()
    if reader.read(1):
        return 0
    if reader.read(1):
        return reader.read(5)
    # UndefinedVLC is (table, bits, bit_position) -- see mpeg2intra.py:313 for
    # the shape every other raise site uses. This one passed a single sentence
    # and so could not be raised at all: the only signal that says "a parameter
    # guess is wrong" died of TypeError on the way out. Rewind first, so `bits`
    # and `bit_position` describe the offending code rather than the two bits
    # already consumed getting here; B-2 has two codes, so '00' is the only
    # way to arrive.
    reader.seek(at)
    raise M.UndefinedVLC("macroblock_type (I-picture, table B-2)",
                         reader.peek_bits(2), at)


def read_flags(byte):
    """The 0x10 byte -> the parameters it encodes.

    Bit order is stream order as the game shifts it into IPU_CTRL bits 16..23:
    MP1, QST, IVF, AS, reserved, DTD, IDP1, IDP0 from the top.
    """
    b = [(byte >> (7 - i)) & 1 for i in range(8)]
    return dict(mpeg1=b[0], q_scale_type=b[1], intra_vlc_format=b[2],
                alternate_scan=b[3], dct_type_in_stream=b[5],
                intra_dc_precision=(b[6] << 1) | b[7], raw=byte)


def try_decode(data, **over):
    """How far the bitstream parses, without reconstructing pixels.

    Returns a dict rather than raising, so a parameter search can score a
    candidate by how far it got. This is the cheap half: it answers "is the
    bitstream being read correctly" and says nothing about whether the picture
    looks right.
    """
    try:
        head = ipu.parse(data)
    except ipu.NotIpu as exc:
        return dict(ok=False, macroblocks=0, expected=0, error=str(exc))

    w, h = head["width"], head["height"]
    expected = (w // 16) * (h // 16)
    p = dict(DEFAULTS)
    p.update(read_flags(data[ipu.HEADER]) if not over else {})
    p.update(over)

    reader = M.BitReader(data[ipu.HEADER:], start_bit=8)
    pred = [128, 128, 128]
    n = 0
    err = None
    try:
        while n < expected:
            if n:                      # NO address increment before the first
                M.MB_ADDR_VLC.decode(reader)
            read_macroblock_type(reader)
            _, pred = M.decode_intra_macroblock(
                reader, pred, chroma_format="4:2:0",
                intra_vlc_format=p["intra_vlc_format"],
                alternate_scan=p["alternate_scan"])
            n += 1
    except Exception as exc:
        err = "%s: %s" % (type(exc).__name__, exc)

    return dict(ok=(n == expected and err is None), macroblocks=n,
                expected=expected, width=w, height=h,
                bits_used=reader.tell(), bits_total=(len(data) - ipu.HEADER) * 8,
                error=err, params=p)


def decode(data, **over):
    """`.ipu` bytes -> (width, height, RGB bytes). Raises IpuUndecodable."""
    try:
        head = ipu.parse(data)
    except ipu.NotIpu as exc:
        raise IpuUndecodable(str(exc))

    w, h = head["width"], head["height"]
    if w % 16 or h % 16:
        raise IpuUndecodable("%dx%d is not a whole number of macroblocks"
                             % (w, h))
    mbx, mby = w // 16, h // 16

    p = dict(DEFAULTS)
    p.update(read_flags(data[ipu.HEADER]))
    p.update(over)
    if p.get("mpeg1"):
        raise IpuUndecodable("file asks for MPEG-1 coding, which is not "
                             "implemented (no shipped file sets this)")

    # Full-size luma, half-size chroma.
    y = [0] * (w * h)
    cb = [0] * ((w // 2) * (h // 2))
    cr = [0] * ((w // 2) * (h // 2))

    reader = M.BitReader(data[ipu.HEADER:], start_bit=8)
    pred = [128, 128, 128]
    qm = list(T.DEFAULT_INTRA_QUANTISER_MATRIX)
    # Starts at the value the game ORs into the IDEC command word, but a
    # macroblock may override it via macroblock_type '01'.
    qsc = p["q_scale_code"]

    for mb in range(mbx * mby):
        if mb:
            M.MB_ADDR_VLC.decode(reader)
        try:
            new_qsc = read_macroblock_type(reader)
            if new_qsc:
                qsc = new_qsc
            blocks, pred = M.decode_intra_macroblock(
                reader, pred, chroma_format="4:2:0",
                intra_vlc_format=p["intra_vlc_format"],
                alternate_scan=p["alternate_scan"])
        except Exception as exc:
            raise IpuUndecodable(
                "failed at macroblock %d of %d: %s: %s"
                % (mb, mbx * mby, type(exc).__name__, exc))

        mx, my = (mb % mbx) * 16, (mb // mbx) * 16
        for bi, qf in enumerate(blocks):
            coeffs = M.dequantise_intra(
                qf, quant_matrix=qm, q_scale_code=qsc,
                q_scale_type=p["q_scale_type"],
                intra_dc_precision=p["intra_dc_precision"])
            px = M.clamp_samples(M.idct_8x8(coeffs))
            if bi < 4:                       # Y: 2x2 blocks of 8x8
                ox = mx + (bi & 1) * 8
                oy = my + (bi >> 1) * 8
                for r in range(8):
                    row = (oy + r) * w + ox
                    y[row:row + 8] = px[r * 8:r * 8 + 8]
            else:                            # Cb then Cr, half resolution
                plane = cb if bi == 4 else cr
                cw = w // 2
                ox, oy = mx // 2, my // 2
                for r in range(8):
                    row = (oy + r) * cw + ox
                    plane[row:row + 8] = px[r * 8:r * 8 + 8]

    cbf = M.upsample_chroma(cb, w // 2, h // 2, 2, 2)
    crf = M.upsample_chroma(cr, w // 2, h // 2, 2, 2)
    # SGN is 0 in the IDEC command word -- the IPU emits unsigned 0..255, so
    # the colour conversion is full-range rather than studio-swing.
    rgb = M.ycbcr_planes_to_rgb(y, cbf, crf, w, h, full_range=True)
    return w, h, rgb
