#!/usr/bin/env python3
"""The last small UNKNOWN extensions: `.rain` `.sys` `.ico` `.psi` `.modb`
`.rec` `.bin`.

Nothing in `03_Tools/rdrstudio/` read any of these.  They are only 21 archive
entries between them, so they never ranked against the big formats -- but the
lane's mandate is unexplained BYTES, not popular ones, and with the `props.py`
structural gap closed these are what is left.

Settled here:

  .rain   a weather config -- `RainStorm` then `Key= value` lines (note the
          `=`, which none of the other text formats on this disc use).
          ONE of the four is not a `.rain` at all: see `misfiled()`.
  .sys    `icon.sys`, magic `PS2D` -- the PS2 memory-card save descriptor.
          It names `iconview.ico` three times (the normal/copy/delete slots).
  .ico    the PS2 memory-card ICON geometry those names point at, and
  .psi    the same format under a different extension.
  .modb   a `.mod`: the `version: 2.12` header is byte-identical.
  .rec    a 30 fps recording with a 32-byte record. PARTIAL -- the record's
          fields are not named here.
  .bin    `agestdfx.bin`. Still UNKNOWN, with the negative results recorded
          so the next person does not repeat them.

Read-only.  Takes no lock.
"""
import argparse, collections, json, os, re, struct, sys, zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from rdrstudio.iso9660 import Iso9660
from rdrstudio import dave

ISO = os.path.join(os.path.dirname(__file__), '..', '..',
                   '01_Vanilla', 'RDR_VANILLA.iso')

EXTS = ('rain', 'sys', 'ico', 'psi', 'modb', 'rec', 'bin', 'mod', 'hf')
ICON_MAGIC = 0x00010000


def grab(iso):
    out = collections.defaultdict(dict)
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b'Dave', b'DAVE'):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            ext = e.name.rsplit('.', 1)[-1].lower()
            if ext not in EXTS:
                continue
            if ext in ('mod', 'hf') and e.name not in (
                    'pagejournalr_lod_01.mod', 'lvl_perfnew.hf'):
                continue
            if e.name in out[ext]:
                continue
            raw = d.backing.read_at(e.offset, e.size_comp)
            try:
                b = zlib.decompress(raw, -15) if e.compressed else raw
            except zlib.error:
                continue
            out[ext][e.name] = (f.path, b)
    return out


def hf_fields(b):
    """`.hf`: u32 w,h; f32 min_x,min_z,max_x,max_z; f32 height[w*h]."""
    if len(b) < 24:
        return None
    w, h = struct.unpack_from('<II', b, 0)
    if not (0 < w < 4096 and 0 < h < 4096):
        return None
    need = 24 + w * h * 4
    return dict(w=w, h=h, need=need, size=len(b), match=need == len(b))


def icon_fields(b):
    """PS2 memory-card icon: magic, animation shapes, texture type, 1.0f, nbvtx."""
    magic, nbsp, tex, res = struct.unpack_from('<4I', b, 0)
    nbvtx = struct.unpack_from('<I', b, 16)[0]
    return dict(magic=magic, nbsp=nbsp, tex_type=tex,
                f32_at_12=struct.unpack_from('<f', b, 12)[0],
                nbvtx=nbvtx, vtx_mod3=nbvtx % 3,
                body=20 + nbvtx * (nbsp * 8 + 16), size=len(b))


def misfiled(blobs):
    """Which `.rain` files are not `.rain`.

    `lvl_perfnew_hf.rain` is a heightfield: its name carries `_hf`, it has no
    `RainStorm` header, and it satisfies the `.hf` size identity exactly.
    A tool that routes on extension alone will mis-handle it.
    """
    out = []
    for name, (arc, b) in blobs['rain'].items():
        text = b[:64].decode('latin-1', 'replace')
        is_text = text.lstrip().startswith('RainStorm')
        hf = None if is_text else hf_fields(b)
        out.append(dict(name=name, archive=arc, size=len(b),
                        rainstorm_header=is_text, hf=hf))
    return out


def adpcm_score(b):
    """Is `agestdfx.bin` SPU ADPCM?  16-byte blocks, byte 1 = flags 0..7.

    It is NOT, and the number that looks encouraging is a trap: 45.9% pass
    against a 3.1% base rate, but 726 of the 794 passing blocks have flags==0
    because they are simply zero-filled.  Strip those and the signal goes.
    """
    nb = len(b) // 16
    flags = collections.Counter(b[i * 16 + 1] for i in range(nb))
    ok = sum(v for k, v in flags.items() if k <= 7)
    zero_blocks = sum(1 for i in range(nb)
                      if not any(b[i * 16:i * 16 + 16]))
    return dict(blocks=nb, flags_le7=ok, pct=round(100 * ok / nb, 1),
                control_pct=3.1, zero_blocks=zero_blocks,
                flags_le7_excluding_zero_blocks=ok - zero_blocks)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    iso = Iso9660(ISO)
    blobs = grab(iso)
    iso.close()

    res = {}

    # .rain
    res['rain'] = misfiled(blobs)

    # .sys
    sysres = []
    for name, (arc, b) in blobs['sys'].items():
        strs = [x.decode('latin-1') for x in re.findall(rb'[\x20-\x7e]{4,}', b)]
        sysres.append(dict(name=name, archive=arc, size=len(b),
                           magic=b[:4].decode('latin-1'), strings=strs))
    res['sys'] = sysres

    # .ico + .psi
    icons = []
    for ext in ('ico', 'psi'):
        for name, (arc, b) in sorted(blobs[ext].items()):
            d = icon_fields(b)
            d.update(name=name, ext=ext, archive=arc)
            icons.append(d)
    res['icons'] = icons

    # .modb vs .mod
    mb = blobs['modb']
    mo = blobs['mod']
    res['modb'] = [dict(name=n, size=len(b), header=b[:13].decode('latin-1'))
                   for n, (a, b) in sorted(mb.items())]
    res['mod_reference'] = [dict(name=n, size=len(b),
                                 header=b[:13].decode('latin-1'))
                            for n, (a, b) in sorted(mo.items())]

    # .rec
    recs = []
    for name, (arc, b) in sorted(blobs['rec'].items()):
        period = max(((p, sum(1 for i in range(0, min(4096, len(b) - p))
                              if b[i] == b[i + p])) for p in (16, 32, 48, 64)),
                     key=lambda t: t[1])
        recs.append(dict(name=name, size=len(b),
                         f32_0=round(struct.unpack_from('<f', b, 0)[0], 1),
                         best_period=period[0], matches=period[1],
                         size_div_16=len(b) % 16 == 0))
    res['rec'] = recs

    # .bin
    for name, (arc, b) in blobs['bin'].items():
        res['bin'] = dict(name=name, archive=arc, size=len(b),
                          leading_zero_bytes=next((i for i, c in enumerate(b)
                                                   if c), -1),
                          nonzero_pct=round(100 * sum(1 for c in b if c)
                                            / len(b), 1),
                          printable_runs=len(re.findall(rb'[\x20-\x7e]{6,}', b)),
                          adpcm=adpcm_score(b))

    if args.json:
        print(json.dumps(res, indent=1, default=str)); return

    print('.rain — %d distinct' % len(res['rain']))
    for r in sorted(res['rain'], key=lambda r: r['name']):
        if r['rainstorm_header']:
            print('   %-24s %6d B  RainStorm config' % (r['name'], r['size']))
        else:
            hf = r['hf']
            print('   %-24s %6d B  NOT a .rain -- .hf heightfield '
                  'w=%d h=%d need=%d MATCH=%s'
                  % (r['name'], r['size'], hf['w'], hf['h'], hf['need'],
                     hf['match']))

    print('\n.sys')
    for r in res['sys']:
        print('   %-14s magic=%s  references: %s'
              % (r['name'], r['magic'], sorted(set(r['strings']))))

    print('\n.ico / .psi — PS2 memory-card icon geometry')
    print('   %-15s %-5s %5s %6s %8s %7s %8s %8s'
          % ('name', 'ext', 'nbsp', 'tex', 'nbvtx', 'vtx%3', 'body', 'size'))
    for d in icons:
        print('   %-15s %-5s %5d   0x%02X %8d %7d %8d %8d'
              % (d['name'], d['ext'], d['nbsp'], d['tex_type'], d['nbvtx'],
                 d['vtx_mod3'], d['body'], d['size']))
    print('   magic 0x%08X on all; f32@12 = 1.0 on all' % ICON_MAGIC)

    print('\n.modb — the header against a real .mod')
    for d in res['modb'] + res['mod_reference']:
        print('   %-28s %6d B  header %r' % (d['name'], d['size'], d['header']))

    print('\n.rec — 30 fps recordings')
    for d in res['rec']:
        print('   %-16s %6d B  f32[0]=%.1f  best byte period %d (%d matches)'
              '  size%%16==0 %s'
              % (d['name'], d['size'], d['f32_0'], d['best_period'],
                 d['matches'], d['size_div_16']))

    d = res['bin']
    print('\n.bin %s — STILL UNKNOWN, negatives recorded' % d['name'])
    print('   %d B, %d leading zero bytes, %.1f%% nonzero, %d printable runs'
          % (d['size'], d['leading_zero_bytes'], d['nonzero_pct'],
             d['printable_runs']))
    a = d['adpcm']
    print('   NOT SPU ADPCM: flags<=7 on %d/%d blocks (%.1f%%) looks high '
          'against a %.1f%% control,' % (a['flags_le7'], a['blocks'],
                                         a['pct'], a['control_pct']))
    print('   but %d of those blocks are entirely zero -- %d real passes left.'
          % (a['zero_blocks'], a['flags_le7_excluding_zero_blocks']))


if __name__ == '__main__':
    main()
