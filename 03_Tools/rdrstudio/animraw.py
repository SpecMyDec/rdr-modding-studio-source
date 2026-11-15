"""The third .anim codec: no compression at all, just floats.

181 clips on the disc open with the magic `00 00 00 00` instead of `cm2\\0` or
`cmp\\0`, and `anim.parse` rejects anything that is not cm2 -- so the Animation
tab refused them before it ever got as far as asking which skeleton they fit.
108 of those 181 are the accessory clips (a character's hair, scarf, coat tail
or jaw), which is why a tester found them all unplayable and reasonably
guessed the cause was the accessory skeleton. The skeleton was only half of
it; this is the other half.

THE FORMAT, and it is as plain as it looks:

    0x00  u32  0            the "magic", which is simply zero
    0x04  u32  frames
    0x08  u32  channels
    0x0C  u32  (varies)     see below
    0x10  u32  (varies)
    0x11  ..   float32 [frames][channels]      KEY-MAJOR

Every byte is accounted for: size == 17 + 4 * channels * frames, exact on all
181 files, 0 mismatches. That is the whole oracle -- a file of raw samples has
no redundancy to check against, so the size identity plus a sane decode is
what there is, and it is stated here rather than dressed up as more.

The 17 is not a typo and it is the one genuinely odd thing: the samples start
at byte 17, not 20, so the fifth header word is only three bytes long before
the data begins. Measured by fitting the size identity, which fails at every
other start offset.

KEY-MAJOR, not channel-major: in cam_anim.anim (7 channels) the floats run
-14.9093 2.2478 30.4224 0.3343 -2.3796 -0.0000 0.5829 then -15.1545, and that
eighth value is the next key's X a few centimetres from the first. Read
channel-major it would have to be seven consecutive X values that never
change, which is not what is there.

Channels mean what they mean everywhere else in this format: 0-2 are the root
bone's translation in metres, then 3+3i..3+3i+2 are bone i's rotation in
radians. So `channels` is 3 + 3*bones, and every one of the 181 divides by 3.

There is no half-rate flag and no key/frame distinction here -- keys == frames
-- because nothing is dropped. A clip is exactly as long as it says it is.
"""

import struct


class RawError(Exception):
    pass


MAGIC = b"\x00\x00\x00\x00"
HEADER = 17                  # measured: samples begin at byte 17, not 20


def looks_raw(data):
    """Cheap test before committing to a parse."""
    return len(data) >= HEADER and data[:4] == MAGIC


class RawAnim:
    """One uncompressed clip, with the same surface the other two codecs
    expose so everything downstream -- the viewer, BVH export -- works
    unchanged."""

    __slots__ = ("frames", "channels", "keys", "words", "size", "raw")

    @property
    def magic(self):
        return "raw"

    @property
    def half_rate(self):
        """Always false. Nothing is dropped, so a stored key IS a game frame."""
        return False

    @property
    def loop(self):
        # No flag word has been identified as the loop bit for this codec, and
        # guessing one would be a claim about playback the disc has not made.
        return False

    @property
    def bone_count(self):
        """Bones, or None when this clip is not shaped 3 + 3*bones.

        Not every uncompressed clip is a skeleton: 20 of the 181 are cameras
        (7 channels) and face rigs (17), which carry their own channel meaning
        and no root translation. They decode perfectly well as curves -- the
        size identity holds for all 181 -- so they are parsed and shown; what
        they cannot do is claim a bone layout they do not have, and anything
        that needs one (BVH export) must check this for None first.
        """
        if self.channels < 3 or (self.channels - 3) % 3:
            return None
        return (self.channels - 3) // 3

    @property
    def skeletal(self):
        return self.bone_count is not None

    @property
    def anim_count(self):
        """Every channel carries its own sample per key, so all of them are
        'animated' in the sense the other codecs mean -- none is stored as a
        single constant."""
        return self.channels

    def animated(self, c):
        return True

    def at_key(self, k):
        if not 0 <= k < self.keys:
            raise RawError("key %d out of range 0..%d" % (k, self.keys - 1))
        i = k * self.channels
        return list(self.words[i:i + self.channels])

    def curve(self, c):
        """Channel `c` across every key."""
        if not 0 <= c < self.channels:
            raise RawError("channel %d out of range" % c)
        return [self.words[k * self.channels + c] for k in range(self.keys)]

    def lerp(self, k0, k1, frac):
        a, b = self.at_key(k0), self.at_key(k1)
        return [x + (y - x) * frac for x, y in zip(a, b)]

    def at_phase(self, phase):
        if self.keys == 1:
            return self.at_key(0)
        t = (phase % 1.0) * (self.keys - 1)
        k0 = int(t)
        k1 = min(k0 + 1, self.keys - 1)
        return self.lerp(k0, k1, t - k0)

    def to_json(self):
        return dict(magic=self.magic, frames=self.frames,
                    channels=self.channels, keys=self.keys,
                    bones=self.bone_count, half_rate=False,
                    size=self.size, codec="uncompressed float32")

    # ---- writing back

    def pack(self, values=None):
        """Re-encode. `values` is [key][channel]; None means unchanged.

        Same length by construction -- one float per channel per key, and
        neither count can change without changing the clip's shape -- so a
        write can never overflow the archive slot.
        """
        if values is None:
            return bytes(self.raw)
        if len(values) != self.keys:
            raise RawError("expected %d keys, got %d" % (self.keys, len(values)))
        out = bytearray(self.raw[:HEADER])
        for k, row in enumerate(values):
            if len(row) != self.channels:
                raise RawError("key %d has %d channels, expected %d"
                               % (k, len(row), self.channels))
            for v in row:
                out += struct.pack("<f", v)
        if len(out) != len(self.raw):
            raise RawError("re-encode changed the length: %d -> %d"
                           % (len(self.raw), len(out)))
        return bytes(out)


def parse(data):
    if not looks_raw(data):
        raise RawError("not an uncompressed clip: magic is %r" % (data[:4],))
    frames, channels = struct.unpack_from("<2I", data, 4)
    if channels == 0 or frames == 0:
        raise RawError("a clip with %d frames and %d channels has no samples"
                       % (frames, channels))
    want = HEADER + 4 * channels * frames
    if len(data) != want:
        raise RawError(
            "size does not match the format: %d frames x %d channels needs "
            "%d bytes, the file has %d" % (frames, channels, want, len(data)))
    a = RawAnim()
    a.frames = frames
    a.channels = channels
    a.keys = frames
    a.size = len(data)
    a.raw = bytes(data)
    a.words = list(struct.unpack_from("<%df" % (channels * frames),
                                      data, HEADER))
    return a
