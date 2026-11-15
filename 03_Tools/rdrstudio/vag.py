"""PS2 SPU-ADPCM, the codec the `.bd` sound banks are stored in.

155 `.bd`/`.hd` pairs ship in BANKS.DAT and they hold the game's sound
effects. Unlike the streamed `.stm` music, this is not a studio codec: it is
Sony's standard SPU format, which is why `AGESND.IRX` imports `libsd` and
calls `sceSdHdGetVAGInfoParam` -- the SPU2 decodes it in hardware and the
driver only has to hand it addresses.

Verified before writing a line of this: over the whole of `snd_00.bd`, every
16-byte block's first byte satisfies `shift <= 12` and `filter <= 4`
(**1.0000** of blocks, against a 0.254 random baseline), and byte 1 takes only
the valid flag values {0, 1, 2, 3, 6, 7}. Data that is not SPU-ADPCM does not
do that -- the `.stm` bodies score 0.441 on the same test.

Layout, per 16-byte block:

    byte 0   low nibble  = shift   (right-shift applied to each sample)
             high nibble = filter  (index into the two-tap predictor below)
    byte 1   flags: bit 0 END, bit 1 LOOP, bit 2 LOOP START
    2..15    28 samples, 4 bits each, LOW nibble first

Each sample is sign-extended from 4 bits, scaled up and shifted down, then run
through a two-tap IIR predictor:

    out = s + (prev1 * f0 + prev2 * f1) / 64
"""
import struct
from typing import Any

# The canonical SPU/XA predictor. Deliberately spelled out rather than
# imported from anywhere: these five pairs ARE the format.
F0 = (0.0, 60.0, 115.0, 98.0, 122.0)
F1 = (0.0, 0.0, -52.0, -55.0, -60.0)

BLOCK = 16
SAMPLES_PER_BLOCK = 28

FLAG_END = 0x01
FLAG_LOOP = 0x02
FLAG_LOOP_START = 0x04


class VagError(Exception):
    pass


def looks_like_adpcm(data, blocks=4000):
    """Fraction of blocks whose header is a legal shift/filter pair.

    Real SPU-ADPCM scores 1.0; unrelated bytes score about 0.25. Used to
    refuse a file rather than emit noise from it.
    """
    n = ok = 0
    end = min(len(data) - BLOCK, BLOCK * blocks)
    for i in range(0, max(0, end), BLOCK):
        b0 = data[i]
        n += 1
        if (b0 & 0x0F) <= 12 and ((b0 >> 4) & 0x0F) <= 4:
            ok += 1
    return (ok / n) if n else 0.0


def decode(data, offset=0, limit=None, stop_at_end=True):
    """Decode SPU-ADPCM to a list of int16.

    `stop_at_end` honours the END flag, which is how a bank marks the end of
    one sound; pass False to run through a whole `.bd` regardless.
    """
    out = []
    prev1 = prev2 = 0.0
    i = offset
    end = len(data) if limit is None else min(len(data), offset + limit)
    while i + BLOCK <= end:
        b0 = data[i]
        flags = data[i + 1]
        shift = b0 & 0x0F
        filt = (b0 >> 4) & 0x0F
        # The hardware clamps rather than faulting, and real banks do contain
        # the occasional out-of-range header; mirroring that keeps a decode
        # from dying on one odd block.
        if shift > 12:
            shift = 9
        if filt > 4:
            filt = 0
        f0, f1 = F0[filt], F1[filt]

        for j in range(SAMPLES_PER_BLOCK):
            byte = data[i + 2 + (j >> 1)]
            nib = (byte & 0x0F) if (j & 1) == 0 else (byte >> 4)
            s = nib << 12
            if s & 0x8000:                 # sign-extend the 4-bit sample
                s -= 0x10000
            sample = (s >> shift) + prev1 * f0 / 64.0 + prev2 * f1 / 64.0
            prev2 = prev1
            prev1 = sample
            v = int(round(sample))
            out.append(-32768 if v < -32768 else (32767 if v > 32767 else v))

        i += BLOCK
        if stop_at_end and (flags & FLAG_END):
            break
    return out


def split_sounds(data):
    """Cut a `.bd` into its individual sounds on the END flag.

    Returns [(start_offset, block_count)]. The bank is a run of ADPCM blocks
    with no index of its own -- the `.hd` holds the program/region table --
    but the END flag alone recovers the boundaries, which is enough to audit
    a bank without parsing the header file.
    """
    out = []
    start = 0
    i = 0
    while i + BLOCK <= len(data):
        if data[i + 1] & FLAG_END:
            out.append((start, (i - start) // BLOCK + 1))
            start = i + BLOCK
        i += BLOCK
    if start < len(data) - BLOCK:
        out.append((start, (len(data) - start) // BLOCK))
    return out


def encode(samples, loop=False):
    """int16 -> SPU-ADPCM. The inverse of `decode`, and the basis of import.

    For each 16-byte block the encoder tries all five predictors and every
    legal shift, and keeps whichever pair reproduces the block most closely.
    Brute force is the right call here: there are only 5 x 13 = 65 candidates
    per block, and a cheaper heuristic would trade audible quality for time
    nobody is waiting on.

    Crucially it runs the DECODER's own recursion while searching, so the
    predictor state it plans against is the state the hardware will actually
    be in. Encoding against the clean input instead is the classic way to
    produce a file that measures fine and drifts when played.
    """
    out = bytearray()
    prev1 = prev2 = 0.0
    n = len(samples)
    for start in range(0, n, SAMPLES_PER_BLOCK):
        chunk = list(samples[start:start + SAMPLES_PER_BLOCK])
        while len(chunk) < SAMPLES_PER_BLOCK:
            chunk.append(0)

        best = None
        for filt in range(5):
            f0, f1 = F0[filt], F1[filt]
            # The residual this predictor leaves, given the real state.
            p1, p2 = prev1, prev2
            resid = []
            for s in chunk:
                resid.append(s - (p1 * f0 / 64.0 + p2 * f1 / 64.0))
                p2, p1 = p1, float(s)
            peak = max((abs(r) for r in resid), default=0.0)
            # Pick the FINEST step that still spans the residual.
            #
            # Decoding is `(nibble << 12) >> shift`, so the step is
            # 2**(12 - shift): a LARGER shift means a SMALLER step and finer
            # resolution. The search therefore starts at the finest setting
            # and backs off until a signed nibble (-8..7) can reach the peak.
            # Walking the other way -- starting coarse and incrementing while
            # the fit is bad -- moves away from the answer, because each step
            # shrinks the range it is failing to cover. That bug cost ~13 dB
            # and produced sample errors larger than full scale.
            shift = 12
            while shift > 0 and peak / float(1 << (12 - shift)) > 7.0:
                shift -= 1

            p1, p2 = prev1, prev2
            nibbles = []
            err = 0.0
            for s in chunk:
                pred = p1 * f0 / 64.0 + p2 * f1 / 64.0
                d = s - pred
                q = int(round(d / (1 << (12 - shift))))
                q = -8 if q < -8 else (7 if q > 7 else q)
                rec = pred + (q << (12 - shift))
                rec = -32768.0 if rec < -32768 else (32767.0 if rec > 32767 else rec)
                err += (s - rec) ** 2
                nibbles.append(q & 0x0F)
                p2, p1 = p1, rec
            if best is None or err < best[0]:
                best = (err, filt, shift, nibbles, p1, p2)

        assert best is not None      # the filt/shift sweep is never empty
        _, filt, shift, nibbles, p1, p2 = best
        blk = bytearray(BLOCK)
        blk[0] = (filt << 4) | shift
        blk[1] = FLAG_LOOP if loop else 0
        for j in range(0, SAMPLES_PER_BLOCK, 2):
            blk[2 + (j >> 1)] = nibbles[j] | (nibbles[j + 1] << 4)
        out += blk
        prev1, prev2 = p1, p2

    if out:
        # Mark the end the way the hardware expects, or a voice runs on into
        # whatever sample happens to sit after it in SPU RAM.
        out[len(out) - BLOCK + 1] |= FLAG_END
    return bytes(out)


def from_wav(data):
    """Pull mono int16 out of a RIFF/WAVE file. Returns (samples, rate)."""
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise VagError("not a RIFF/WAVE file")
    pos, fmt, rate, chans, bits = 12, None, None, 1, 16
    pcm = None
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        sz = struct.unpack_from("<I", data, pos + 4)[0]
        body = data[pos + 8:pos + 8 + sz]
        if cid == b"fmt ":
            fmt, chans, rate = struct.unpack_from("<HHI", body, 0)[:3]
            bits = struct.unpack_from("<H", body, 14)[0] if len(body) >= 16 else 16
        elif cid == b"data":
            pcm = body
        pos += 8 + sz + (sz & 1)
    if pcm is None or rate is None:
        raise VagError("no fmt/data chunk")
    if fmt != 1 or bits != 16:
        raise VagError("need uncompressed 16-bit PCM (got format %s, %s-bit)"
                       % (fmt, bits))
    vals = list(struct.unpack("<%dh" % (len(pcm) // 2), pcm[:len(pcm) // 2 * 2]))
    if chans > 1:
        vals = vals[::chans]          # take the first channel; SPU voices are mono
    return vals, rate


def parse_hd(data):
    """The `.hd` companion: where each sound starts and what rate it plays at.

    Sony's standard sound-bank header. Every chunk begins `IECS` -- "SCEI"
    byte-reversed -- followed by a reversed four-character id:

        sreV  Vers    daeH  Head    igaV  Vagi
        lpmS  Smpl    tesS  Sset

    `Vagi` is the one that matters here. After a 16-byte chunk header comes a
    u32 count, then `count + 1` u32 pointers (relative to the chunk start),
    each addressing an 8-byte record:

        u32  offset of this sound inside the .bd
        u16  sample rate, in Hz
        u8   (0 on every record measured)
        u8   (255 on every record measured)

    The rate is the point. `split_sounds()` can find the boundaries from the
    ADPCM END flag alone, but nothing in the body says how fast to play them,
    and they are NOT all the same: snd_00 alone carries 22050, 20500, 18000
    and 14000. Playing everything at one rate is a guess; this is the answer.

    Returns [] rather than raising if the file is not a recognisable `.hd`,
    so a caller can fall back to the END-flag split.
    """
    if len(data) < 0x60 or data[0:4] != b"IECS":
        return []
    # Find the Vagi chunk rather than assuming it sits at 0x50.
    pos = None
    i = 0
    while i + 8 <= len(data):
        if data[i:i + 4] == b"IECS" and data[i + 4:i + 8] == b"igaV":
            pos = i
            break
        i += 4
    if pos is None:
        return []
    try:
        count = struct.unpack_from("<I", data, pos + 12)[0]
        if not 0 < count < 4096:
            return []
        out = []
        for k in range(count + 1):
            ptr = struct.unpack_from("<I", data, pos + 16 + k * 4)[0]
            rec = pos + ptr
            if rec + 8 > len(data):
                break
            off = struct.unpack_from("<I", data, rec)[0]
            rate = struct.unpack_from("<H", data, rec + 4)[0]
            out.append(dict(index=k, offset=off, rate=rate))
        return out
    except struct.error:
        return []


def sounds_with_rates(bd, hd):
    """Merge the body and its header into one list of playable sounds.

    Prefers the `.hd` (authoritative offsets and rates). Falls back to the
    END-flag split with no rate when the header is missing or unreadable, and
    says which happened so a caller need not guess.
    """
    info = parse_hd(hd) if hd else []
    if info:
        out: list[dict[str, Any]] = []
        for k, rec in enumerate(info):
            start = rec["offset"]
            end = info[k + 1]["offset"] if k + 1 < len(info) else len(bd)
            if start >= len(bd) or end <= start:
                continue
            out.append(dict(index=len(out), offset=start,
                            blocks=(end - start) // BLOCK, rate=rec["rate"],
                            source="hd"))
        if out:
            return out
    return [dict(index=i, offset=o, blocks=b, rate=None, source="end-flag")
            for i, (o, b) in enumerate(split_sounds(bd))]


def to_wav(samples, rate=22050, channels=1):
    """A minimal RIFF/WAVE wrapper -- no dependency on the `wave` module."""
    n = len(samples)
    pcm = struct.pack("<%dh" % n, *samples)
    hdr = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt "
    hdr += struct.pack("<IHHIIHH", 16, 1, channels, rate,
                       rate * channels * 2, channels * 2, 16)
    hdr += b"data" + struct.pack("<I", len(pcm))
    return hdr + pcm
