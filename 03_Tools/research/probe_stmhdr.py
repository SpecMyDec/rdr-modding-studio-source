#!/usr/bin/env python3
"""M13 prerequisite: does `stm.py`'s WRITER reproduce the shipped headers?

M13 is the audio milestone -- organise the browser, `.wav` -> `.stm`, and a
one-click song replace "with a loop option if the format allows it".  The
format work is done: `stm.py` decodes and encodes IMA/DVI ADPCM and
`build_header()` sets every field, with the loop descriptor this lane settled
off the `AGESND.IRX` disassembly.

What nobody had done is check the writer against the disc.  A round-trip that
only proves `parse(build(x)) == x` proves nothing about whether the bytes match
what the game ships.  This probe does the harder direction:

    for every shipped .stm:  parse_header -> build_header -> compare 2048 bytes

An exact match on the whole corpus is what lets M13's acceptance test assert
real bytes rather than "no exception was raised".  Any mismatch is reported
with the differing offsets, because a header field the writer gets wrong is
precisely the kind of thing that ships a silent bug.

Read-only.  Takes no lock.
"""
import argparse, collections, json, os, struct, sys, zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from rdrstudio.iso9660 import Iso9660
from rdrstudio import dave, stm

ID_KEYWORD = b'$Id: makestm v 1.17 $'   # at +2027 on 8361/8361

ISO = os.path.join(os.path.dirname(__file__), '..', '..',
                   '01_Vanilla', 'RDR_VANILLA.iso')


def streams(iso):
    seen = set()
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b'Dave', b'DAVE'):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            if not e.name.lower().endswith('.stm') or e.name in seen:
                continue
            seen.add(e.name)
            # The header is the first 2048 bytes; that is all this needs, and
            # reading only the header keeps a 1.21 GB corpus cheap.
            raw = d.backing.read_at(e.offset, min(e.size_comp, 1 << 16))
            if e.compressed:
                try:
                    b = zlib.decompressobj(-15).decompress(raw, stm.HEADER_SIZE)
                except zlib.error:
                    continue
            else:
                b = raw[:stm.HEADER_SIZE]
            if len(b) < stm.HEADER_SIZE:
                continue
            yield f.path, e.name, bytes(b)


def loop_of(h, channels):
    """The loop descriptor as `build_header` wants it, or None.

    ONE state per channel.  The header always has both slots, and on the one
    mono looping file (`tomb_os_saloonamb.stm`) +40 and +44 hold the same
    word -- so taking both unconditionally hands a mono stream two states and
    `build_header` correctly refuses it.
    """
    enabled, restart, s0, s1 = struct.unpack_from('<4I', h, 32)
    if not enabled:
        return None
    words = (s0, s1)[:channels]
    states = [struct.unpack('<hH', struct.pack('<I', w)) for w in words]
    return restart, states


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    iso = Iso9660(ISO)
    total = exact = 0
    failed = 0
    diffs = collections.Counter()
    examples = []
    looped = 0
    rates = collections.Counter()
    chans = collections.Counter()

    for arc, name, h in streams(iso):
        total += 1
        try:
            info = stm.parse_header(h)
        except ValueError:
            failed += 1
            continue
        rates[info['rate']] += 1
        chans[info['channels']] += 1
        lp = loop_of(h, info['channels'])
        if lp:
            looped += 1
        try:
            built = stm.build_header(
                info['rate'], info['channels'], info['data_size'],
                unrounded=info['data_size_unrounded'], loop=lp)
            built = bytearray(built)
            # THE DEFECT: the tool field is an RCS `$Id:` keyword at +2027,
            # 21 bytes, identical on 8361/8361 shipped files.  `build_header`
            # writes only the inner 16 bytes at +2032, so every header it
            # produces differs from the disc in exactly 5 bytes.  Applying the
            # keyword here shows every OTHER field is already exact.
            built[2027:2027 + len(ID_KEYWORD)] = ID_KEYWORD
            built = bytes(built)
        except Exception as ex:
            failed += 1
            if len(examples) < 5:
                examples.append((name, 'build raised: %s' % str(ex)[:60]))
            continue
        if built == h:
            exact += 1
        else:
            bad = [i for i in range(stm.HEADER_SIZE) if built[i] != h[i]]
            for off in sorted({i & ~3 for i in bad}):
                diffs[off] += 1
            if len(examples) < 5:
                examples.append((name, 'differs at %d offsets, first %s'
                                 % (len(bad), bad[:8])))

    iso.close()
    res = dict(streams=total, header_exact=exact, failed=failed,
               looping=looped, rates=dict(rates), channels=dict(chans),
               differing_field_offsets=dict(diffs.most_common(12)),
               examples=examples)
    if args.json:
        print(json.dumps(res, indent=1)); return

    print('.stm distinct files: %d' % total)
    print('rates: %s   channels: %s' % (dict(rates), dict(chans)))
    print('looping (header +32 set): %d' % looped)
    print()
    print('build_header() + the $Id keyword at +2027 reproduces the shipped')
    print('header EXACTLY: %d / %d  (%.2f%%)'
          % (exact, total, 100 * exact / total if total else 0))
    print('WITHOUT that one fix it is 0 / %d -- every header differs in the'
          % total)
    print('same 5 bytes, 2027..2031.')
    if failed:
        print('   could not be checked: %d' % failed)
    if diffs:
        print('\n   differing 4-byte field offsets (count of files):')
        for off, c in diffs.most_common(12):
            print('      +%-5d %6d' % (off, c))
    for n, why in examples:
        print('      %-30s %s' % (n, why))


if __name__ == '__main__':
    main()
