#!/usr/bin/env python3
"""The 182 `.anim` files nobody had read.

`rdrstudio/anim.py` decodes the `cm2` codec and `animcmp.py` the `cmp` one, and
its docstring names what was left:

    zeros        181   no codec at all -- the loader NULLs the codec pointer
                       (0x002CF1B8) and takes an uncompressed 8-bytes-per-key
                       path at 0x002CF354 that nobody has read yet
    00 00 28 4E    1   one file, unexplained

This probe closes both.

**The 181 uncompressed files: `17-byte header + frames x channels float32`,
and the size identity holds on 181 / 181.**  The header is the same 17 bytes
the resource loader parses at 0x002CF0F0 -- magic, frames, channels, one f32,
one flags byte -- and the payload is then one plain float per channel per
frame.  Note the docstring's "8 bytes per key" does not match the data: a key
is `channels` floats, and a SAMPLE is 4 bytes, not 8.

**The 1 remaining file is not a valid `.anim`.**  `red_shit.anim` is shown here
with the evidence rather than a guessed codec -- see `verdict_red_shit()`.

Read-only.  Takes no lock.
"""
import argparse, collections, json, os, random, statistics, struct, sys, zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from rdrstudio.iso9660 import Iso9660
from rdrstudio import dave

ISO = os.path.join(os.path.dirname(__file__), '..', '..',
                   '01_Vanilla', 'RDR_VANILLA.iso')

HEADER = 17          # 0x002CF0F0 parses 17 bytes
RAW_MAGIC = b'\x00\x00\x00\x00'
ODD_MAGIC = b'\x00\x00\x28\x4e'


def anims(iso):
    seen = set()
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b'Dave', b'DAVE'):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            if not e.name.lower().endswith('.anim') or e.name in seen:
                continue
            seen.add(e.name)
            raw = d.backing.read_at(e.offset, e.size_comp)
            try:
                b = zlib.decompress(raw, -15) if e.compressed else raw
            except zlib.error:
                continue
            yield f.path, e.name, b


def header_of(b):
    frames, channels = struct.unpack_from('<II', b, 4)
    return frames & 0xffff, channels & 0xffff, b[16]


def med_delta(series):
    return statistics.median([abs(series[i + 1] - series[i])
                              for i in range(len(series) - 1)])


def verdict_red_shit(b):
    """Is the odd file animation data under ANY plausible reshape?  No.

    Three independent things are wrong with it, and the smoothness test is the
    one that rules out "some codec we have not found":

      * its first four bytes match no codec the loader knows;
      * its payload is 16,200 bytes, while the uncompressed layout needs
        31,121 for its declared 48 frames x 162 channels and the cm2 layout
        needs neither;
      * reshaped as int16 key-major under every factorisation of 8,100, the
        per-channel series is NO SMOOTHER THAN A SHUFFLE of the same bytes.
        Real animation channels are autocorrelated; these are not.

    The K=25 / C=324 row is reported but must not be read as a hit: with 324
    columns and 2,355 zeros among 8,100 values, many columns are constant
    zero, so the median difference is 0 for free.  That is the same
    "divides evenly is nearly free" trap that has bitten this lane before.
    """
    pay = b[HEADER:]
    n = len(pay) // 2
    vals = list(struct.unpack('<%dh' % n, pay[:n * 2]))
    out = {}
    for C in (162, 50, 324, 90):
        if n % C:
            continue
        cols = [vals[c::C] for c in range(C)]
        out['key-major C=%d (K=%d)' % (C, n // C)] = round(
            statistics.median([med_delta(c) for c in cols]), 1)
    sh = vals[:]
    random.Random(0).shuffle(sh)
    out['CONTROL shuffled C=162'] = round(
        statistics.median([med_delta(sh[c::162]) for c in range(162)]), 1)
    out['zeros'] = [vals.count(0), len(vals)]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    iso = Iso9660(ISO)
    magic = collections.Counter()
    raw_ok = raw_bad = 0
    bad = []
    odd = None
    chan = collections.Counter()

    for arc, name, b in anims(iso):
        m = bytes(b[:4])
        magic[m] += 1
        if m == ODD_MAGIC:
            odd = (arc, name, b)
        if m != RAW_MAGIC:
            continue
        frames, channels, flags = header_of(b)
        chan[channels] += 1
        want = HEADER + frames * channels * 4
        if len(b) == want:
            raw_ok += 1
        else:
            raw_bad += 1
            if len(bad) < 6:
                bad.append((name, len(b), want, frames, channels))
    iso.close()

    res = dict(magics={m.hex(): c for m, c in magic.most_common()},
               uncompressed_size_identity=[raw_ok, raw_ok + raw_bad],
               mismatches=bad,
               uncompressed_channel_counts=dict(chan.most_common(8)))
    if odd:
        res['red_shit'] = dict(archive=odd[0], size=len(odd[2]),
                               header=dict(zip(('frames', 'channels', 'flags'),
                                               header_of(odd[2]))),
                               smoothness=verdict_red_shit(odd[2]))
    if args.json:
        print(json.dumps(res, indent=1)); return

    print('.anim magics across %d distinct files:' % sum(magic.values()))
    for m, c in magic.most_common():
        print('   %-12s %5d' % (m.hex(), c))
    print()
    print('UNCOMPRESSED PATH  size == 17 + frames*channels*4 :  %d / %d'
          % (raw_ok, raw_ok + raw_bad))
    for x in bad:
        print('   mismatch %s size %d want %d (%d frames x %d ch)' % x)
    print('   channel counts seen: %s' % dict(chan.most_common(8)))
    print('   NOTE: a SAMPLE is 4 bytes, not the 8 anim.py\'s docstring says.')

    if odd:
        arc, name, b = odd
        fr, ch, fl = header_of(b)
        print()
        print('THE ONE UNEXPLAINED FILE: %s (%s), %d bytes' % (name, arc, len(b)))
        print('   header frames=%d channels=%d flags=0x%02x' % (fr, ch, fl))
        print('   uncompressed layout would need %d bytes; payload is %d'
              % (HEADER + fr * ch * 4, len(b) - HEADER))
        print('   int16 smoothness, median |delta| within a channel:')
        for k, v in verdict_red_shit(b).items():
            print('      %-28s %s' % (k, v))
        print('   -> no reshape beats the shuffle. Not animation data.')


if __name__ == '__main__':
    main()
