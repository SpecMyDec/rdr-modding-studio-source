"""`.anim` skeletal animation -- the `cmp` codec.

234 files on the disc wear this magic instead of `cm2`. It is a different
codec with its own runtime class, and everything below was read out of the
unpacked executable rather than inferred from the bytes:

    0x002D35F0   cmp readData  (vtable 0x005078CC slot 0) -- parses the payload
    0x002D3F08   cmp sampler   -- unpacks a key and dequantises it

**There is no separate compressed stream.** The task that tracked this format
was called "decode the adaptive sample bit-packing", and that name assumed a
variable-width bitstream. What is actually there is fixed 32-bit words, each
packing three channels:

    bits  0..10   11 bits   channel i where i % 3 == 0
    bits 11..20   10 bits   channel i where i % 3 == 1
    bits 21..31   11 bits   channel i where i % 3 == 2

The middle field is one bit narrower, which is exactly why the loader scales
it by 1/1024 while the other two get 1/2048 (0x002D376C): all three cover the
same range. "Adaptive" was the right word in the wrong place -- the *scale* is
per track, not the width. Measured over 5,130 rotation tracks the span runs
from a few thousandths of a radian to several radians, median 0.23 rad, so a
bone that barely moves still gets the full 11 bits of resolution across its
own small range.

Layout, in stream order:

    17 bytes            the shared .anim header
    tracks x 12         f32 scale, f32 bias, u16 index, u16 (the reader
                        loads this and discards it -- 0x002D36DC)
    2 bytes             nchan, the number of 32-bit columns
    nchan*keys x 4      the packed words, KEY-MAJOR (0x002D3F1C computes
                        frame*nchan before indexing)

`index` is 0xFFFF for a channel that never moves; its value is the bias alone
and it occupies no column. Otherwise it selects a column, and because each
column carries three channels an index is shared by at most three tracks --
true on 234/234 files, max reuse exactly 3.

Verified: `17 + tracks*12 + 2 + nchan*keys*4` equals the file size exactly on
all 234 files, and decoding them yields no non-finite samples.
"""
import struct

MAGIC = b"cmp\x00"
HEADER_SIZE = 17

# 0x3A001002 and 0x3A802008, loaded as immediates at 0x002D376C/0x002D3788.
SCALE_WIDE = struct.unpack("<f", struct.pack("<I", 0x3A001002))[0]   # ~1/2048
SCALE_NARROW = struct.unpack("<f", struct.pack("<I", 0x3A802008))[0]  # ~1/1024

CONSTANT = 0xFFFF


class CmpError(Exception):
    pass


def _f32(x):
    return struct.unpack("<f", struct.pack("<f", x))[0]


class CmpAnim:
    """One decoded cmp clip. Construct with `parse`."""

    __slots__ = ("magic", "frames", "channels", "keys", "nchan", "flags_raw",
                 "flags", "scale", "bias", "index", "words", "size")

    @property
    def half_rate(self):
        """Flags bit 3, tested at 0x002D3628 -- same bit cm2 uses."""
        return bool(self.flags & 0x08)

    @property
    def loop(self):
        return bool(self.flags & 0x01)

    @property
    def bone_count(self):
        """3 root translation channels, then 3 rotation channels per bone."""
        return (self.channels - 3) // 3

    def animated(self, c):
        """Does channel `c` move, or is it a single constant?"""
        return self.index[c] != CONSTANT

    def at_key(self, k):
        """Every channel's value at stored key `k`, in channel order."""
        if not 0 <= k < self.keys:
            raise CmpError("key %d out of range 0..%d" % (k, self.keys - 1))
        base = k * self.nchan
        out = []
        for c in range(self.channels):
            ci = self.index[c]
            if ci == CONSTANT:
                out.append(_f32(self.bias[c]))
                continue
            w = self.words[base + ci]
            slot = c % 3
            if slot == 0:
                f = w & 0x7FF
            elif slot == 1:
                f = (w >> 11) & 0x3FF
            else:
                f = (w >> 21) & 0x7FF
            out.append(_f32(self.scale[c] * f + self.bias[c]))
        return out

    def lerp(self, k0, k1, frac):
        a, b = self.at_key(k0), self.at_key(k1)
        return [_f32(x + (y - x) * frac) for x, y in zip(a, b)]

    def at_phase(self, phase):
        """Pose at 0..1 across the clip, interpolating between stored keys."""
        if self.keys < 2:
            return self.at_key(0)
        t = max(0.0, min(1.0, float(phase))) * (self.keys - 1)
        k0 = int(t)
        if k0 >= self.keys - 1:
            return self.at_key(self.keys - 1)
        return self.lerp(k0, k0 + 1, t - k0)

    def curve(self, c):
        """One channel's value at every stored key."""
        return [self.at_key(k)[c] for k in range(self.keys)]

    def to_json(self):
        return dict(
            codec="cmp", frames=self.frames, channels=self.channels,
            keys=self.keys, columns=self.nchan, half_rate=self.half_rate,
            loop=self.loop, bones=self.bone_count,
            constant_channels=sum(1 for c in range(self.channels)
                                  if not self.animated(c)),
            size=self.size)


def parse(data):
    """Decode a cmp `.anim`. Raises CmpError on anything that does not fit."""
    data = bytes(data)
    size = len(data)
    if size < HEADER_SIZE + 2:
        raise CmpError("only %d bytes; too short for a cmp header" % size)

    a = CmpAnim()
    a.size = size
    a.magic = data[0:4]
    if a.magic != MAGIC:
        raise CmpError("magic %r is not cmp" % (a.magic,))

    # Both counts are u32 on disc; the loader keeps the low half, as for cm2.
    a.frames = struct.unpack_from("<I", data, 0x04)[0] & 0xFFFF
    a.channels = struct.unpack_from("<I", data, 0x08)[0] & 0xFFFF
    a.flags_raw = data[0x10]
    a.flags = a.flags_raw & 0x09
    a.keys = a.frames // 2 + 1 if a.flags & 0x08 else a.frames
    if a.channels <= 0 or a.keys <= 0:
        raise CmpError("%d channels and %d keys is not a clip"
                       % (a.channels, a.keys))

    need = HEADER_SIZE + a.channels * 12 + 2
    if size < need:
        raise CmpError("needs %d bytes for %d track records, file is %d"
                       % (need, a.channels, size))

    scale, bias, index = [], [], []
    for i in range(a.channels):
        o = HEADER_SIZE + i * 12
        s, b = struct.unpack_from("<ff", data, o)
        c = struct.unpack_from("<H", data, o + 8)[0]
        # The narrow field is one bit shorter, so its step is twice as big.
        scale.append(_f32(s * (SCALE_NARROW if i % 3 == 1 else SCALE_WIDE)))
        bias.append(b)
        index.append(c)
    a.scale, a.bias, a.index = scale, bias, index

    off = HEADER_SIZE + a.channels * 12
    a.nchan = struct.unpack_from("<H", data, off)[0]
    body = off + 2
    want = a.nchan * a.keys
    if body + want * 4 != size:
        raise CmpError(
            "payload is %d bytes but %d columns x %d keys needs %d"
            % (size - body, a.nchan, a.keys, want * 4))
    a.words = struct.unpack_from("<%dI" % want, data, body) if want else ()

    bad = [c for c in index if c != CONSTANT and c >= a.nchan]
    if bad:
        raise CmpError("track index %d is past the %d columns"
                       % (bad[0], a.nchan))
    return a
