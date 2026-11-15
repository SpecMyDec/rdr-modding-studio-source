"""`.stm` streamed audio -- 4-bit IMA/DVI ADPCM, the game's biggest data set.

8,361 `.stm` files, 1.21 GB, 52% of the disc by bytes. The decoder is not in
the main executable: `.stm` has no code reference in the EE image at all. It
lives in `AGESND.IRX`, the IOP sound driver (MIPS R3000), in the object file
`adpcm_iop.c` at `.text` 0x0277C..0x031E7.

Everything below was read off that disassembly, not guessed:

  0x03058  one-time table generator. Reads the 8-entry `int8` index-adjust
           table at `.rodata` 0x09300 -- `{-1,-1,-1,-1,2,4,6,8}` -- and the
           89-entry `uint16` step table at 0x09308 -- `7,8,9,...,29794,32767`.
           Both are bit-exact the canonical IMA/DVI tables. It expands them
           into an 89x8 array of `{int16 diff, int16 next_index*8}` in `.bss`
           at 0x09F1C, using
               diff = step>>3 + (n&4 ? step : 0)
                              + (n&2 ? step>>1 : 0)
                              + (n&1 ? step>>2 : 0)
           and `next = clamp(i + indextab[n], 0, 88)` (`bgez`/`slti 89` at
           0x030CC..0x030E8 -- so the index clamp is 88, not 64).
  0x02C98  decode + mix loop, 12k/24k paths.
  0x02F64  decode + mix loop, 48k path.
  0x0277C  "handler A": six paths = {mono, stereo} x {12k, 24k, 48k}. Not part
           of the codec -- it is the rate dispatcher, a zero-order-hold
           upsampler to the SPU's fixed 48 kHz, and a constant-power pan law.
  0x04AD4  header parser.

Per sample, straight off the shifts and masks at 0x02CEC..0x02D5C:

    nib  = next 4 bits, HIGH nibble of each byte first
    mag  = nib & 7
    diff = table lookup as above
    pred = clamp16(nib & 8 ? pred - diff : pred + diff)
    idx  = next_index

Two things here are not the usual IMA conventions and are the reason this
format resisted identification:

  * **High nibble first.** At 0x02CF8..0x02D08 the byte is fetched on an even
    sample count and `sra v0,28` takes the top nibble; the odd count reuses
    `andi 0xF`. Nearly every other IMA variant is low-nibble-first.
  * **No block header of any kind.** The predictor and step index start at
    zero (`.bss` stream context +24/+28, zeroed at 0x04CB8) and run
    continuously for the whole file. There is no per-block predictor+index
    preamble as in IMA-in-WAV, and no resync. This is why the earlier
    "shift <= 12, filter <= 4" screen scored only 0.441 and the format was
    written off as not-ADPCM: that test looks for a Sony SPU/XA block header,
    which IMA simply does not have.

Container, from the parser at 0x04AD4 (the header is 2048 bytes, 2012 of them
zero, and the payload starts at 2048):

    +0     'STMA'                     checked against 0x414D5453
    +4     format id                  26986 / 26987 -> here; 26992 -> raw PCM
    +8     64 / 128 / 256             READ INTO A STACK SLOT AND NEVER USED
    +12    sample rate                only 48000 / 24000 / 12000 accepted
    +16    always 4                   READ INTO A STACK SLOT AND NEVER USED
    +20    1 or 2                     the real channel count
    +24    payload bytes
    +28    payload bytes, unrounded
    +32    optional loop point
    +2027  '$Id: makestm v 1.17 $'    an RCS/CVS keyword, 21 bytes, and the
                                      whole of it -- Angel Studios' own tool,
                                      not middleware. Reading it from +2032
                                      drops the `$Id: ` prefix and is what made
                                      this writer miss every shipped header by
                                      exactly five bytes (REQ-054).

The two fields the driver ignores are exactly the two that misled the earlier
passes. `+16` is 4 on every single file including the mono ones, so it is not
a channel count -- reading it as one makes every duration come out 4x short
(it is why `01_bull_close2_plii.stm` was expected to be ~10 s when it is in
fact 22.76 s). `+8` is not the interleave size either: it measures out as
`64 * channels * (rate / 24000)`, a per-tick byte budget. The interleave is
hard-coded to 64 bytes in all six of handler A's paths.

    mono   -- one continuous nibble stream.
    stereo -- [64 B ch0][64 B ch1][64 B ch0]..., each channel carrying its own
              predictor and index across its own blocks.

Usage:

    from rdrstudio import stm
    rate, nch, chans = stm.decode(data)
    stm.write_wav("out.wav", chans, rate)
"""
import array
import io
import struct
from typing import Any

MAGIC = b"STMA"
HEADER_SIZE = 2048

#: The tool keyword, in full. It is an RCS `$Id:` expansion and it starts at
#: +2027, not +2032 -- `$Id: ` is five bytes and the field is 21, not 16.
#: Measured: this exact string is at +2027 on 8,361 of 8,361 shipped streams,
#: one distinct value, no exceptions. (rdr-re, REQ-054.)
TOOL_OFFSET = 2027
TOOL_FIELD = 21
TOOL_ID = "$Id: makestm v 1.17 $"

#: Format ids that reach the ADPCM handler. The dispatch at 0x03F30 is
#: ``v0 = fmt - 26986; sltiu v0, v0, 2`` -- one handler, both ids. They differ
#: only in the channel count at +20, so this is one codec, not two.
FMT_ADPCM_MONO = 26986
FMT_ADPCM_STEREO = 26987
#: Handled by the driver (a `memcpy` path at 0x08B34) but used by no shipped
#: file. Not implemented here.
FMT_PCM = 26992

#: Sample rates the header parser accepts, checked at 0x04B04.
RATES = (48000, 24000, 12000)

#: Hard-coded in handler A, in every one of its six paths. NOT the +8 field.
INTERLEAVE = 64

#: The canonical IMA index-adjust table, `.rodata` 0x09300, 8 x int8.
INDEX_ADJUST = (-1, -1, -1, -1, 2, 4, 6, 8)

#: The canonical 89-entry IMA step table, `.rodata` 0x09308, 89 x uint16.
#: Spelled out rather than generated: these 89 numbers ARE the format.
STEP_TABLE = (
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41,
    45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190,
    209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724,
    796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272,
    2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132,
    7845, 8630, 9493, 10442, 11487, 12635, 13899, 15289, 16818, 18500,
    20350, 22385, 24623, 27086, 29794, 32767,
)

INDEX_MAX = len(STEP_TABLE) - 1  # 88, from the `slti v0, a0, 89` at 0x030DC


def _build_tables():
    """Reproduce the 89x8 `.bss` table that 0x03058 generates at run time.

    Flattened to `idx * 8 + magnitude`, which is exactly how the decode loop
    addresses it (`sll v1,idx,3 / addu v1,v1,mag / sll v1,v1,2`).
    """
    diff = array.array("i", bytes(4 * 89 * 8))
    nxt = array.array("i", bytes(4 * 89 * 8))
    for i, step in enumerate(STEP_TABLE):
        for n in range(8):
            d = step >> 3
            if n & 4:
                d += step
            if n & 2:
                d += step >> 1
            if n & 1:
                d += step >> 2
            j = i + INDEX_ADJUST[n]
            if j < 0:
                j = 0
            elif j > INDEX_MAX:
                j = INDEX_MAX
            diff[i * 8 + n] = d
            nxt[i * 8 + n] = j
    return diff, nxt


_DIFF, _NEXT = _build_tables()


def parse_header(data):
    """Read the 2048-byte header. Returns a dict; raises ValueError if it is
    not a `.stm`.

    `block_field` and `plii_channels` are reported because they are in the
    file, but the driver never loads either one -- see the module docstring.
    """
    if len(data) < HEADER_SIZE:
        raise ValueError("short file: %d bytes, header alone is %d"
                         % (len(data), HEADER_SIZE))
    if data[:4] != MAGIC:
        raise ValueError("not a .stm: magic is %r, expected %r"
                         % (bytes(data[:4]), MAGIC))
    fmt, block, rate, plii, channels, size, size2, loop = struct.unpack_from(
        "<8I", data, 4)
    tool = (bytes(data[TOOL_OFFSET:TOOL_OFFSET + TOOL_FIELD])
            .split(b"\x00")[0].decode("latin-1"))
    return {
        "format": fmt,
        "block_field": block,       # +8,  unused by the driver
        "rate": rate,
        "plii_channels": plii,      # +16, unused by the driver; 4 everywhere
        "channels": channels,       # +20, the real channel count
        "data_size": size,
        "data_size_unrounded": size2,
        "loop_point": loop,
        "tool": tool,
    }


def decode_ima(data, predictor=0, index=0):
    """Decode a single continuous IMA stream. Two samples per byte, high
    nibble first. Returns `(array('h'), predictor, index)` so a caller can
    resume -- though nothing in the shipped format ever needs to.
    """
    out = array.array("h", bytes(4 * len(data)))
    diff_t = _DIFF
    next_t = _NEXT
    pred = predictor
    idx = index
    k = 0
    for byte in data:
        # high nibble, then low -- 0x02CF8..0x02D08
        for code in (byte >> 4, byte & 0x0F):
            e = idx * 8 + (code & 7)
            d = diff_t[e]
            if code & 8:
                pred -= d
                if pred < -32768:
                    pred = -32768
            else:
                pred += d
                if pred > 32767:
                    pred = 32767
            idx = next_t[e]
            out[k] = pred
            k += 1
    return out, pred, idx


def deinterleave(body, channels, unit=INTERLEAVE):
    """Split a stereo payload into its per-channel byte streams.

    `[unit bytes ch0][unit bytes ch1][unit bytes ch0]...`, ch0 first. A final
    partial group is kept rather than dropped; the shipped files never have
    one (`data_size` is a multiple of `64 * channels` on all 8,361).
    """
    if channels == 1:
        return [bytes(body)]
    parts = [bytearray() for _ in range(channels)]
    group = unit * channels
    for base in range(0, len(body), group):
        for c in range(channels):
            parts[c] += body[base + c * unit: base + (c + 1) * unit]
    return [bytes(p) for p in parts]


def decode(data):
    """Decode a `.stm`.

    Returns `(rate, channels, [per-channel array('h')])` -- `array('h')` is a
    genuine int16 sequence, indexable and iterable like a list, and about 8x
    smaller.

    Each channel starts from `predictor = 0, index = 0` and runs continuously;
    there is no block state to carry in.
    """
    h = parse_header(data)
    if h["format"] == FMT_PCM:
        raise ValueError("format 26992 is the raw-PCM path (driver 0x08B34); "
                         "no shipped file uses it and it is not implemented")
    if h["format"] not in (FMT_ADPCM_MONO, FMT_ADPCM_STEREO):
        raise ValueError("format %d does not reach the ADPCM handler "
                         "(dispatch at 0x03F30 accepts 26986/26987)"
                         % h["format"])
    if h["rate"] not in RATES:
        raise ValueError("rate %d rejected; the parser at 0x04B04 accepts "
                         "only %s" % (h["rate"], ", ".join(map(str, RATES))))
    nch = h["channels"]
    if nch not in (1, 2):
        raise ValueError("channel count %d; the driver holds exactly two "
                         "ADPCM states (context +24 and +28)" % nch)

    size = h["data_size"]
    body = memoryview(data)[HEADER_SIZE:HEADER_SIZE + size]
    chans = [decode_ima(part)[0] for part in deinterleave(body, nch)]
    n = min(len(c) for c in chans)
    chans = [c[:n] for c in chans]
    return h["rate"], nch, chans


# --------------------------------------------------------------- encoding
#
# CLAIMED under REQ-042 rule 3 (rdr-app / the reviewer's lane, 2026-09-14): `stm.py`
# is pre-split and on no lane's list, and adding an encoder is more than a
# repair, so this is a claim and not a quiet edit.
#
# Everything below is the exact inverse of the decoder above, derived from the
# same disassembly rather than from a generic IMA encoder. The three places a
# generic one would differ, and why each matters here:
#
#   * the reconstruction table. `_DIFF` is the driver's own 89x8 table, and
#     the encoder reconstructs its predictor THROUGH IT rather than through
#     the textbook `step >> 3` expression, so encoder and driver can never
#     drift apart by a rounding choice.
#   * the index clamp is 88, not 64. `_NEXT` already carries that.
#   * nibble order is HIGH FIRST, and the payload is interleaved per 64-byte
#     block per channel with each channel keeping its own predictor and index
#     across its own blocks -- so a stereo stream is two independent encoders,
#     not one encoder over interleaved samples.


def encode_ima(samples, predictor=0, index=0):
    """Encode one continuous IMA stream. Exact inverse of `decode_ima`.

    Returns `(bytes, predictor, index)`. The search is the canonical IMA
    magnitude test, but the predictor is then advanced by looking the chosen
    code up in `_DIFF`/`_NEXT` -- the same tables the driver builds at run
    time -- so what this function believes the decoder will produce IS what
    the decoder produces.
    """
    out = bytearray()
    diff_t, next_t = _DIFF, _NEXT
    pred, idx = int(predictor), int(index)
    hi = None
    for s in samples:
        if s > 32767:
            s = 32767
        elif s < -32768:
            s = -32768
        step = STEP_TABLE[idx]
        delta = s - pred
        code = 0
        if delta < 0:
            code = 8
            delta = -delta
        if delta >= step:
            code |= 4
            delta -= step
        if delta >= (step >> 1):
            code |= 2
            delta -= step >> 1
        if delta >= (step >> 2):
            code |= 1
        # Advance exactly as decode_ima will, through the driver's table.
        e = idx * 8 + (code & 7)
        d = diff_t[e]
        if code & 8:
            pred -= d
            if pred < -32768:
                pred = -32768
        else:
            pred += d
            if pred > 32767:
                pred = 32767
        idx = next_t[e]
        if hi is None:
            hi = code
        else:
            out.append((hi << 4) | code)
            hi = None
    if hi is not None:
        # An odd sample count would leave a dangling high nibble. The shipped
        # files never have one (every data_size is a multiple of 64*channels,
        # 8,361 of 8,361), and a half-byte would desynchronise the whole
        # stream, so pad with a zero code rather than drop it.
        out.append(hi << 4)
    return bytes(out), pred, idx


def interleave(parts, unit=INTERLEAVE):
    """Inverse of `deinterleave`: per-channel byte streams -> one payload."""
    if len(parts) == 1:
        return bytes(parts[0])
    n = max(len(p) for p in parts)
    if n % unit:
        n += unit - (n % unit)
    out = bytearray()
    for base in range(0, n, unit):
        for p in parts:
            chunk = bytes(p[base:base + unit])
            out += chunk + b"\x00" * (unit - len(chunk))
    return bytes(out)


def build_header(rate, channels, data_size, unrounded=None, loop=None,
                 tool=TOOL_ID):
    """The 2048-byte header, with every field set the way the disc sets it.

    Measured across all 8,361 shipped streams rather than assumed:

        +4   26986 when mono, 26987 when stereo  (8007 / 354)
        +8   64 * channels * (rate // 24000)     (64 / 128 / 256 observed)
        +16  4 on every file including the mono ones
        +24  a multiple of 64 * channels on 8,361 of 8,361
        +2027 '$Id: makestm v 1.17 $' on every file -- 21 bytes from 2027,
              NOT 16 from 2032. Getting that wrong left 2027..2031 zero and
              made every header this function produced differ from every
              header on the disc, in the same five bytes (REQ-054).

    `loop` is either None (no loop, what 8,182 of the 8,361 shipped streams
    do) or `(restart_offset, [(predictor, index), ...])` -- the full loop
    descriptor. Setting the flag alone is not possible through this function,
    and that is deliberate.

    THE DESCRIPTOR, settled from rdr-re's disassembly and then reconstructed
    off the disc rather than inferred:

        +32  loop enabled            the driver branches on it at 0x04bb0
        +36  restart BYTE OFFSET     -> ctx+96, added to ctx+108
        +40  channel 0 loop state    -> ctx+100 -> ctx+24
        +44  channel 1 loop state    -> ctx+104 -> ctx+28  (mono: same as +40)

    +24/+28 are the slots this module's own docstring already names as where
    the predictor and step index start -- normally zeroed at 0x04CB8. So a
    loop does not rewind to silence, it restores the codec state the stream
    was holding at the restart point. Each is packed `<hH`: int16 predictor,
    uint16 step index.

    Checked across all 179 shipped looping files: both step indices land in
    0..88 (179 of 179, where a random word would be ~0.14% likely), +36 never
    exceeds the payload, and +36 is a multiple of 64*channels every time. The
    clincher is the one mono flagged file, `tomb_os_saloonamb.stm`: decoding
    its payload up to +36 yields predictor -447 index 36, which is exactly
    what +40 and +44 both hold.
    """
    if channels not in (1, 2):
        raise ValueError("channels must be 1 or 2, not %r" % (channels,))
    if rate not in (48000, 24000, 12000):
        raise ValueError("the driver accepts 48000/24000/12000 only, not %r"
                         % (rate,))
    states: tuple[Any, ...] = ()
    restart = 0
    if loop is not None:
        restart, states = loop
        if len(states) != channels:
            raise ValueError("a loop needs one (predictor, index) per channel;"
                             " got %d for %d channel(s)"
                             % (len(states), channels))
        if restart % (INTERLEAVE * channels):
            raise ValueError(
                "the restart offset must land on a %d-byte group boundary "
                "(64 * channels) -- it is a payload offset into interleaved "
                "blocks, and every one of the 179 shipped looping files "
                "honours that. %d does not."
                % (INTERLEAVE * channels, restart))
        if restart >= data_size:
            raise ValueError("restart offset %d is past the %d-byte payload"
                             % (restart, data_size))
        for pred, idx in states:
            if not 0 <= idx <= INDEX_MAX:
                raise ValueError("step index %d is outside 0..%d"
                                 % (idx, INDEX_MAX))
    head = bytearray(HEADER_SIZE)
    head[0:4] = MAGIC
    struct.pack_into(
        "<8I", head, 4,
        FMT_ADPCM_STEREO if channels == 2 else FMT_ADPCM_MONO,
        INTERLEAVE * channels * (rate // 24000 or 1),
        rate,
        4,
        channels,
        data_size,
        data_size if unrounded is None else unrounded,
        1 if loop is not None else 0)
    if loop is not None:
        struct.pack_into("<I", head, 36, restart)
        # Mono duplicates its single state into both slots, which is what the
        # one mono looping file on the disc does.
        pairs = list(states) + [states[-1]] if channels == 1 else list(states)
        struct.pack_into("<hH", head, 40, int(pairs[0][0]), int(pairs[0][1]))
        struct.pack_into("<hH", head, 44, int(pairs[1][0]), int(pairs[1][1]))
    # Accept the inner form too, so a caller that passes the old
    # `"makestm v 1.17 $"` still gets bytes that match the disc rather than a
    # keyword written five bytes late.
    text = tool if tool.startswith("$Id: ") else "$Id: " + tool
    head[TOOL_OFFSET:TOOL_OFFSET + len(text)] = \
        text.encode("latin-1")[:TOOL_FIELD]
    return bytes(head)


def encode(chans, rate, loop_at=None):
    """PCM int16 per channel -> a complete `.stm`. Inverse of `decode`.

    `chans` is a list of one or two sequences of int16, one per channel, the
    same shape `decode` returns. Stereo channels are encoded INDEPENDENTLY --
    each carries its own predictor and index across its own 64-byte blocks --
    because that is what `deinterleave` + `decode_ima` do on the way back.
    """
    if not chans or len(chans) > 2:
        raise ValueError("one or two channels, not %d" % len(chans))
    nch = len(chans)
    n = max(len(c) for c in chans)
    # Every shipped data_size is a multiple of 64*channels. One byte holds two
    # samples, so pad each channel to a multiple of 128 samples and the
    # payload lands on that boundary without a partial final group.
    pad = (-n) % (INTERLEAVE * 2)
    total = n + pad

    loop = None
    if loop_at is not None:
        loop_at = int(loop_at)
        if not 0 <= loop_at < total:
            raise ValueError("loop point %d is outside 0..%d samples"
                             % (loop_at, total))
        if loop_at % (INTERLEAVE * 2):
            raise ValueError(
                "the loop point must be a multiple of %d samples, so its byte "
                "offset lands on a 64-byte block boundary in every channel"
                % (INTERLEAVE * 2))

    parts, states = [], []
    for c in chans:
        seq = list(c) + [0] * (total - len(c))
        if loop_at is None:
            body, _, _ = encode_ima(seq)
        else:
            # Encode in two passes so the codec state AT the loop point falls
            # out of the first one. That state is what +40/+44 carry, and it
            # is why the descriptor needs an encoder rather than a byte offset
            # somebody guessed.
            head, pred, idx = encode_ima(seq[:loop_at])
            tail, _, _ = encode_ima(seq[loop_at:], pred, idx)
            body = head + tail
            states.append((pred, idx))
        parts.append(body)

    payload = interleave(parts)
    if loop_at is not None:
        loop = ((loop_at // 2) * nch, states)
    return build_header(rate, nch, len(payload), loop=loop) + payload


def from_wav(data):
    """A RIFF/WAVE blob -> `(rate, [per-channel array('h')])`.

    16-bit PCM only, which is what `write_wav` emits and what anything
    exporting for this tool will produce. Rejects anything else by name
    rather than encoding noise.
    """
    import wave
    with wave.open(io.BytesIO(data), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError("16-bit PCM only; this is %d-bit"
                             % (w.getsampwidth() * 8))
        ch, rate, n = w.getnchannels(), w.getframerate(), w.getnframes()
        if ch not in (1, 2):
            raise ValueError("one or two channels, not %d" % ch)
        raw = w.readframes(n)
    flat = array.array("h")
    flat.frombytes(raw[:len(raw) - (len(raw) % (2 * ch))])
    if ch == 1:
        return rate, [flat]
    return rate, [array.array("h", flat[0::2]), array.array("h", flat[1::2])]


def duration(data):
    """Playing time in seconds, without decoding."""
    h = parse_header(data)
    return h["data_size"] * 2.0 / h["channels"] / h["rate"]


def write_wav(path, chans, rate):
    """Write decoded channels as a 16-bit PCM WAV."""
    nch = len(chans)
    n = min(len(c) for c in chans)
    if nch == 1:
        pcm = chans[0][:n].tobytes()
    else:
        il = array.array("h", bytes(2 * n * nch))
        for c, ch in enumerate(chans):
            il[c::nch] = ch[:n]
        pcm = il.tobytes()
    with open(path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt ")
        f.write(struct.pack("<IHHIIHH", 16, 1, nch, rate,
                            rate * nch * 2, nch * 2, 16))
        f.write(b"data" + struct.pack("<I", len(pcm)) + pcm)
    return path
