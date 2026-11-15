"""`.anim` skeletal animation -- the `cm2` codec, read and dequantised.

11,138 `.anim` files ship on the disc, about 4,200 of them distinct. Four
different things wear the extension, told apart by the four-byte magic:

    "cm2\\0"   3,771   the codec implemented here
    "cmp\\0"     234   a different codec -- decoded in animcmp.py
    zeros        181   no codec at all -- the loader NULLs the codec pointer
                       (0x002CF1B8) and takes an uncompressed 8-bytes-per-key
                       path at 0x002CF354 that nobody has read yet
    00 00 28 4E    1   one file, unexplained

Everything below was read out of the unpacked executable
(`07_Research/SLUS_205.00.unpacked.bin`, based at 0x00100000) rather than
guessed from the bytes, and the addresses are quoted so the next person can
check any line of it:

    0x002CF0F0   the .anim resource loader -- parses the 17-byte header
    0x002D2C18   cm2 readData        (vtable +0x0C)  -- parses the payload
    0x002D2EB0   cm2 ENCODER         (vtable +0x10)  -- not needed to read
    0x002D33C8   cm2 sampleAtKey     (vtable +0x14)  -> `at_key`
    0x002D1970   sampleAtPhase       (vtable +0x18)  -> `at_phase`
    0x002D3470   cm2 sampleLerp      (vtable +0x1C)  -> `lerp`
    0x002D2DE8   cm2 memorySize      (vtable +0x20)

THE FORMAT IS NOT COMPRESSED IN ANY INTERESTING SENSE. That is the one thing
worth saying loudly, because the earlier surveys spent a long time hunting for
an entropy coder: the payload is three flat arrays and two floats, and the
codec reads them VERBATIM into malloc'd buffers -- 0x002D2C7C, 0x002D2C98 and
0x002D2D34 are three `jal 0x0019A070` stream reads whose lengths are the
buffer sizes. The measured 0.1..17 bits-per-sample spread that made it look
entropy-coded comes entirely from two facts in the layout: a channel the
encoder judged constant costs 2 bytes for the whole clip instead of 2 per key,
and flags bit 3 halves the key count.

The layout, all little-endian and TIGHTLY PACKED -- there is no alignment
padding anywhere, and the two trailing floats are unaligned in 3,150 of 3,771
files, so every read here is an unaligned read:

    +0x00  char[4]  magic
    +0x04  u32      frames    -- only the low 16 bits are used (0x002CF240)
    +0x08  u32      channels  -- only the low 16 bits are used (0x002CF26C)
    +0x0C  f32      UNKNOWN, see `unknown_0c`
    +0x10  u8       flags -- ONE byte (a2=1 at 0x002CF2B0), bits 0 and 3 only
    +0x11  u32      animCount N: how many channels are animated (0x002D2C48)
    +0x15  u8[ceil(C/8)]   which channels are animated, LSB-first
           s16[C - N]      one constant per non-animated channel
           s16[N * K]      the key matrix, KEY-MAJOR, row stride N
           f32             scaleA, for channels 0,1,2       -- OPTIONAL
           f32             scaleB, for channels 3..C-1      -- OPTIONAL

The sharpest evidence that this is right is that it accounts for every byte:
the size formula is exact on 3,766 of the 3,771 distinct cm2 files, and the
other five are exactly 8 bytes shorter because they omit both scales -- which
the code explicitly tolerates (0x002D2D50 / 0x002D2D94 test the byte count the
stream read returned and keep the constructor defaults). Zero files land
anywhere else. See `scripts/verify_anim.py`.

WHAT IS READ VERSUS WHAT IS INFERRED, because a wrong guess here would poison
everything built on top:

  * READ: channels 0,1,2 dequantise with one scale and 3.. with the other. The
    split is the hard-coded literal 3 at 0x002D3400 and 0x002D34C0. It is not
    data-driven and there is no third group.
  * READ: the encoder derives each scale as 65536/max(2*|v|) = 32768/max|v|
    over its own group (0x002D3088 `lui at,0x4780` = 65536.0f, then
    0x002D3090 `div.s`), so the extremal sample lands on the +/-32767..32768
    rail. Confirmed on data: it does, in 3,511 of 3,771 files for group A and
    3,749 for group B.
  * INFERRED, NOT READ: that channels 0-2 are a root translation in metres and
    3.. are Euler rotations in radians. Nothing in the code names a channel.
    The evidence is circumstantial but it is quantitative: the constructor
    defaults are 1000.0 (millimetres) and 32768/pi (radians), the `cm3` sibling
    hardcodes exactly those two reciprocals at 0x002D2970, 829 files carry
    scaleB == 32768/pi exactly, group-A implied maxima cluster at ~1, and --
    the strongest single check -- channel 1's decoded median lands on the
    matching `.skel`'s own root-bone `offset` Y for 7 of the 9 channel counts
    where a comparison is possible, including a 22-bone bird skeleton whose
    root sits at 0.25 m and whose clips decode channel 1 to 0.23. Two counts
    DISAGREE, one of them the 53-bone shape that covers most of the disc, so
    this is support and not proof. `scripts/verify_anim.py` prints the table.
  * UNKNOWN: which bone each channel triple belongs to, the Euler order, and
    the axis convention. `channels` is always divisible by 3 across cm2 files
    and takes 28 distinct values there, so channels/3 counts triples --
    `bone_count` says "triples" for that reason. There is circumstantial
    support for the triples being one root translation followed by one
    rotation per bone: 121 of the 236 `.skel` files declare exactly that, in
    that order, and their totals land on real channel counts (162 = 3 + 3*53
    covers 2,494 files). It is not a mapping. Do not write an exporter against
    an assumed layout; settle it against a skeleton or in an emulator first.
  * DO NOT assume rotations live in +/-pi. That is true of `cm3`, and it is
    cm2's fallback default, but it is NOT cm2's invariant: scaleB takes 1,970
    distinct values, and 378 files decode at least one channel past pi, the
    largest to 49.5. Use the file's own scale; never clamp or wrap.

Numerics: the console does all of this in single precision on an FPU with no
rounding-mode control and no denormals. This module rounds to float32 after
each arithmetic step (`_f32`) so results match the hardware closely, but they
are NOT guaranteed bit-identical, and nothing here should be treated as such.
"""

import struct

MAGIC = b"cm2\0"

# Codec constructor defaults, 0x002CF1D8-0x002CF204. The scale and its
# reciprocal are stored as two separate fields (codec+0x10/+0x14 and
# +0x18/+0x1C) and the samplers only ever read the RECIPROCALS, which is why
# both are kept here rather than recomputing one from the other.
DEFAULT_SCALE_A = 1000.0                  # 0x447A0000
DEFAULT_DEQUANT_A = 0.0010000000474974513  # 0x3A83126F, not exactly 1/1000
DEFAULT_SCALE_B = 10430.3779296875        # 0x4622F983, = 32768/pi
DEFAULT_DEQUANT_B = 9.58738019107841e-05  # 0x38C90FDB, = pi/32768

HEADER_SIZE = 0x11          # 4+4+4+4+1; see the loader's five length registers


class AnimError(Exception):
    """The file is not a well-formed cm2 clip.

    Raised rather than papered over: a decoder that silently truncates is
    exactly how a bad format spec survives contact with the data.
    """


def _f32(x):
    """Round a Python float to the nearest float32, as the FPU would store it."""
    return struct.unpack("<f", struct.pack("<f", x))[0]


class Anim:
    """One decoded cm2 clip. Construct with `parse`."""

    __slots__ = ("magic", "frames", "channels", "unknown_0c", "flags",
                 "flags_raw", "anim_count", "keys", "mask", "constants",
                 "matrix", "scale_a", "scale_b", "dequant_a", "dequant_b",
                 "scales_present", "size", "consumed")

    # ------------------------------------------------------------------ flags

    @property
    def loop(self):
        """Flags bit 0.

        The NAME is mine; the BEHAVIOUR is read. With this bit set, phase 1.0
        resolves to key 0 instead of the last key (0x002D19B4), the phase
        denominator is `frames` instead of `frames-1` (0x002D1A34), and the
        second key of the final half-rate span wraps to 0 (0x002D1C60). A
        meaning like "first pose equals last pose" would compile identically,
        so treat the label as a label.
        """
        return bool(self.flags & 0x01)

    @property
    def half_rate(self):
        """Flags bit 3: the file stores frames/2+1 keys instead of frames.

        Read at 0x002CF31C, 0x002D2CA4, 0x002D2CF0 and 0x002D19E0 -- the same
        shift-and-test appears in four places, and the key count follows it.
        """
        return bool(self.flags & 0x08)

    @property
    def bone_count(self):
        """channels/3.

        Called a triple count rather than a bone count on purpose: the /3 is
        measured, not stated. All 28 distinct channel counts among cm2 files
        are divisible by 3, and they run 6..219. Nothing in the code binds a
        triple to a joint.

        Worth knowing that this is a cm2 property and not an .anim property:
        across all 4,187 distinct .anim there are 36 distinct values at +0x08
        and three of them (7, 17, 29) are NOT divisible by 3 -- which is a hint
        that the field does not mean "channels" in the non-cm2 layouts.
        """
        return self.channels // 3

    def animated(self, c):
        """Is channel `c` driven by the key matrix?

        LSB-first within each byte, read straight off 0x002D3414-0x002D3428
        (`sra a0,t0,3` / `andi a1,t0,7` / `lbu` / `srav` / `andi 1`). Bit order
        is not a coin flip: under LSB-first the bits above `channels` are clear
        in all 3,771 files, under MSB-first 86 files have stray high bits set.
        """
        return bool((self.mask[c >> 3] >> (c & 7)) & 1)

    def dequant(self, c):
        """The reciprocal scale channel `c` decodes with.

        The literal 3 is the game's, at 0x002D3400 `slti v0,t0,3`.
        """
        return self.dequant_a if c < 3 else self.dequant_b

    # ------------------------------------------------------------- raw access

    def raw_at_key(self, k):
        """Every channel's quantised s16 at key `k`, in channel order.

        Two independent cursors walk the payload exactly as the sampler does
        (0x002D343C advances the key row by 2 bytes, 0x002D3440 advances the
        constants by 2, and only one of them fires per channel). Getting this
        wrong is the classic way to produce plausible-looking garbage, so it is
        written once here and reused by every other accessor.
        """
        if not 0 <= k < self.keys:
            raise AnimError("key %d out of range 0..%d" % (k, self.keys - 1))
        base = k * self.anim_count
        out = []
        arank = crank = 0
        for c in range(self.channels):
            if self.animated(c):
                out.append(self.matrix[base + arank])
                arank += 1
            else:
                out.append(self.constants[crank])
                crank += 1
        return out

    def raw_curve(self, c):
        """Channel `c`'s quantised value at every key, length `keys`.

        A constant channel returns its single value repeated, because that is
        what the sampler hands the animation system on every frame.
        """
        if not 0 <= c < self.channels:
            raise AnimError("channel %d out of range" % c)
        arank = crank = 0
        for i in range(c):
            if self.animated(i):
                arank += 1
            else:
                crank += 1
        if not self.animated(c):
            return [self.constants[crank]] * self.keys
        n = self.anim_count
        return [self.matrix[k * n + arank] for k in range(self.keys)]

    # -------------------------------------------------------------- sampling

    def at_key(self, k):
        """Pose at an exact stored key -- the game's sampleAtKey, 0x002D33C8.

        No wrap fixup on this path; the interpolating path is the only place
        the modular unwrap happens.
        """
        return [_f32(v * self.dequant(c))
                for c, v in enumerate(self.raw_at_key(k))]

    def lerp(self, k0, k1, frac):
        """Blend two keys -- the game's sampleLerp, 0x002D3470.

        The +/-65536 fixup on A is a modular unwrap: it forces A onto the same
        65536-unit revolution as B (A' = B + (s16)(A-B)) so the blend takes the
        short way round. Two things about it are easy to get wrong and are
        therefore spelled out:

          * It is applied to EVERY channel including 0,1,2. Nothing gates it on
            the channel index -- the test at 0x002D3504 sits inside the loop
            body, after the group scale has already been chosen.
          * It only behaves as a true shortest-arc correction when 65536 raw
            units really are one revolution, i.e. when the scale is 32768/pi.
            For the 1,141 files whose scaleB is something else it is just a
            "delta exceeds half the range" correction. That is the game's
            behaviour, so it is reproduced, not fixed.

        Constant channels ignore `frac` entirely (0x002D3554 onward).
        """
        if not 0 <= k0 < self.keys or not 0 <= k1 < self.keys:
            raise AnimError("key pair (%d,%d) out of range 0..%d"
                            % (k0, k1, self.keys - 1))
        n = self.anim_count
        b0, b1 = k0 * n, k1 * n
        out = []
        arank = crank = 0
        for c in range(self.channels):
            inv = self.dequant(c)
            if self.animated(c):
                a = self.matrix[b0 + arank]
                b = self.matrix[b1 + arank]
                arank += 1
                d = a - b
                if d > 32767:
                    a -= 65536
                elif d < -32768:
                    a += 65536
                out.append(_f32(_f32(a + _f32((b - a) * frac)) * inv))
            else:
                out.append(_f32(self.constants[crank] * inv))
                crank += 1
        return out

    def key_at_phase(self, t):
        """Resolve a phase in [0,1] to (k0, k1, frac) without sampling.

        This is sampleAtPhase (0x002D1970) plus its half-rate helper
        (0x002D1BC8), transcribed. It is exposed separately because the key
        arithmetic is the part most likely to be wrong, and a caller checking
        it should not have to infer it back out of float output. k0 == k1 with
        frac 0.0 means the game took one of its exact-key shortcuts.

        Note `span` uses `frames`, not `keys`: phase is always in frame time,
        and the halving happens afterwards.
        """
        frames = self.frames
        if frames == 1 or t <= 0.0:                    # 0x002D1980, 0x002D198C
            return 0, 0, 0.0
        if t >= 1.0:                                   # 0x002D19A4
            k = 0 if self.loop else self.keys - 1      # 0x002D19B4 / 0x002D1A20
            return k, k, 0.0
        span = frames if self.loop else frames - 1     # 0x002D1A34-0x002D1A4C
        pos = _f32(t * float(span))                    # 0x002D1A58 mul.s
        i = int(pos)                                   # 0x002D1A60 cvt.w.s, truncates
        frac = _f32(pos - float(i))                    # 0x002D1A90 / 0x002D1AD0
        if not self.half_rate:                         # 0x002D1AB0
            return i % frames, (i + 1) % frames, frac
        # 0x002D1BC8: the frame index is halved and the fraction is remapped
        # into the half of the span it fell in.
        i %= frames                                    # 0x002D1A9C mfhi
        frac = _f32(frac * 0.5) + 0.5 if (i & 1) else _f32(frac * 0.5)
        k0 = i >> 1                                    # i >= 0, so sra == //2
        if self.loop:
            # last = frames/2 - 1 for even frames, frames/2 for odd
            last = frames // 2 - 1 if frames % 2 == 0 else frames // 2
            k1 = k0 + 1 if k0 != last else 0           # 0x002D1C60 xor/movn
        else:
            k1 = k0 + 1                                # 0x002D1C6C
        return k0, k1, _f32(frac)

    def reachable_keys(self):
        """Which stored keys the phase path can actually land on.

        Worth having because the answer is sometimes "not all of them". For a
        half-rate LOOP clip with an EVEN frame count the game sets
        last = frames/2 - 1 (0x002D1C38-0x002D1C44) and wraps k1 to 0 there,
        while the key count is frames/2 + 1 -- so k0 never exceeds frames/2 - 1,
        k1 never reaches frames/2, and THE LAST STORED KEY IS NEVER SAMPLED.
        931 of the 3,771 files on the disc are in that shape and every one of
        them was confirmed by dense sweep to have an unreachable final key.

        This does not affect reading a clip. It matters to anyone writing an
        encoder or a re-packer, who would otherwise assume every key row is
        live, and it is a place a plausible re-implementation would silently
        diverge from the game.
        """
        seen = set()
        n = max(4 * self.frames, 8)
        for j in range(n + 1):
            k0, k1, _ = self.key_at_phase(j / float(n))
            seen.add(k0)
            seen.add(k1)
        return sorted(seen)

    def at_phase(self, t):
        """Pose at a phase in [0,1] -- the whole runtime path, end to end."""
        k0, k1, frac = self.key_at_phase(t)
        if k0 == k1 and frac == 0.0:
            return self.at_key(k0)
        return self.lerp(k0, k1, frac)

    # ------------------------------------------------------------- reporting

    def to_json(self):
        return dict(
            magic=self.magic.decode("ascii", "replace").rstrip("\0"),
            frames=self.frames, channels=self.channels,
            triples=self.bone_count, keys=self.keys,
            animated_channels=self.anim_count,
            constant_channels=self.channels - self.anim_count,
            flags=self.flags, flags_raw=self.flags_raw,
            loop=self.loop, half_rate=self.half_rate,
            scale_a=self.scale_a, scale_b=self.scale_b,
            scales_present=self.scales_present,
            unknown_0c=self.unknown_0c,
            size=self.size, consumed=self.consumed,
            exact=(self.consumed == self.size),
        )


def parse(data):
    """Decode a cm2 `.anim`. Raises AnimError on anything that does not fit.

    The parse deliberately mirrors the game's own validation (frames and
    channels are range-checked at 0x002CF244 and 0x002CF278, and a failure
    there aborts the load and returns 0) so that "the game would refuse this
    file" and "this decoder refuses this file" stay the same set.
    """
    data = bytes(data)
    size = len(data)
    if size < HEADER_SIZE + 4:
        raise AnimError("only %d bytes; a cm2 header plus animCount is 21" % size)

    a = Anim()
    a.size = size
    a.magic = data[0:4]
    if a.magic != MAGIC:
        raise AnimError("magic %r is not cm2 -- a different codec owns this file"
                        % (a.magic,))

    # Both counts are stored as u32 but the loader keeps only the low half.
    a.frames = struct.unpack_from("<I", data, 0x04)[0] & 0xFFFF
    a.channels = struct.unpack_from("<I", data, 0x08)[0] & 0xFFFF
    a.unknown_0c = struct.unpack_from("<f", data, 0x0C)[0]
    # The loader ORs bit 0 and bit 3 into the runtime word one at a time
    # (0x002CF2B8 and 0x002CF2D8) and never looks at the other six, so they are
    # dropped here too. Both bytes are kept: on the disc the raw byte only ever
    # takes 0x00/0x01/0x08/0x09, and `flags_raw` is what lets a test say so.
    a.flags_raw = data[0x10]
    a.flags = a.flags_raw & 0x09

    if ((a.frames - 1) & 0xFFFF) >= 10000:
        raise AnimError("frames=%d fails the game's own check at 0x002CF24C"
                        % a.frames)
    if ((a.channels - 1) & 0xFFFF) >= 1000:
        raise AnimError("channels=%d fails the game's own check at 0x002CF278"
                        % a.channels)

    a.anim_count = struct.unpack_from("<I", data, 0x11)[0]   # unaligned
    a.keys = a.frames // 2 + 1 if (a.flags & 0x08) else a.frames

    # Nothing in the game checks N <= C, but a file that violates it would
    # make the constants array negative-length, so refuse it here rather than
    # produce nonsense.
    if a.anim_count > a.channels:
        raise AnimError("animCount %d exceeds channels %d"
                        % (a.anim_count, a.channels))

    off = 0x15
    nmask = (a.channels + 7) // 8
    nconst = a.channels - a.anim_count
    nkey = a.anim_count * a.keys
    need = nmask + 2 * nconst + 2 * nkey
    if off + need > size:
        raise AnimError(
            "payload wants %d bytes (mask %d + const %d + keys %dx%d) but only "
            "%d are left" % (need, nmask, 2 * nconst, a.anim_count, a.keys,
                             size - off))

    a.mask = data[off:off + nmask]
    off += nmask
    a.constants = list(struct.unpack_from("<%dh" % nconst, data, off)) if nconst else []
    off += 2 * nconst
    a.matrix = list(struct.unpack_from("<%dh" % nkey, data, off)) if nkey else []
    off += 2 * nkey

    # Both scales are genuinely optional. The game reads 4 bytes, divides the
    # returned count by 4 (0x002D2D4C `sra v0,v0,2`) and skips the reciprocal
    # if that is zero -- so a short read leaves the CONSTRUCTOR DEFAULT in
    # place. Note it leaves the default in the RECIPROCAL field while the
    # scale field itself may have been partly overwritten; since every sampler
    # reads only the reciprocal (codec+0x14, codec+0x1C), the default
    # reciprocal is what actually decodes the clip. Hence the two fields are
    # set independently below instead of one being derived from the other.
    a.scales_present = 0
    a.scale_a, a.dequant_a = DEFAULT_SCALE_A, DEFAULT_DEQUANT_A
    a.scale_b, a.dequant_b = DEFAULT_SCALE_B, DEFAULT_DEQUANT_B
    if off + 4 <= size:
        a.scale_a = struct.unpack_from("<f", data, off)[0]
        # Explicitly NOT a division by zero: the game tests for zero
        # (0x002D2D60 c.eq.s / 0x002D2D68 bc1t, and 0x002D2DA4 / 0x002D2DAC for
        # the other group) and stores 0.0, so that whole group decodes to
        # exactly 0.0. 172 files carry scaleA == 0.0 and 4 carry scaleB == 0.0.
        a.dequant_a = 0.0 if a.scale_a == 0.0 else _f32(1.0 / a.scale_a)
        off += 4
        a.scales_present += 1
    if off + 4 <= size:
        a.scale_b = struct.unpack_from("<f", data, off)[0]
        a.dequant_b = 0.0 if a.scale_b == 0.0 else _f32(1.0 / a.scale_b)
        off += 4
        a.scales_present += 1

    a.consumed = off
    return a


def read(game, archive, name):
    """Parse one clip straight out of a Dave archive."""
    return parse(game.read(archive, name))


def magic_of(data):
    """The four-byte tag, for triaging a pile of .anim files cheaply."""
    return bytes(data[0:4])
