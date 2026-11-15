"""probe_pck.py -- `.pck` is a relocatable memory image, not a container.

82 files, 11 KB to 1.8 MB, all stored UNCOMPRESSED, all in SWAT.DAT (78) and
FRONT.DAT (4). Names are UI screens: `shop_gun`, `mprewards`, `weaponselection`,
`memoryicon`, `logo01`, `j01_01_mainmenu`. Nothing in `03_Tools/rdrstudio/`
parsed them and no known magic appears anywhere inside one.

WHAT IS PROVED

    0x00  u32  base   the address the payload is loaded at
    0x04  u32  1      on all 82
    0x08  u32  0      on all 82
    0x0c  u32  size   == file size - 16, on all 82
    0x10  payload, `size` bytes, where payload[i] lives at address base + i

The load-address claim is the one that matters and it is tested, not asserted.
If `base` is really where the block lands, words inside the payload that point
back into it must land in `[base, base + size)` far more often than chance:

    memoryicon.pck   5.967% of its words land in an 11,664-byte window
                     that is 0.00027% of the 32-bit space -- 21,972x
    all 82           enriched, worst case 17x (weaponselection.pck), and
                     73.0% of the 165,947 pointers found this way are
                     4-byte aligned, against 25% for random data

Header floats read as screen rectangles -- 640.0/480.0 in `memoryicon.pck`,
512.0/256.0 in `j01_01_mainmenu.pck` -- with RGBA colours beside them, which
fits the file names. About 1% of every file is `CD`, MSVC's uninitialised-heap
fill, sitting exactly where struct padding would be.

WHAT IS NOT, and three hypotheses this probe killed

  * NOT a VIF/GIF display list. `vifwalk.walk` -- which requires tokens to tile
    the packet with no gap -- rejects the payload at its first code, from every
    plausible start offset.
  * NO EVIDENCE for a plain C++ vtable object graph. If pointer targets were
    objects starting with a vtable, the word at each target would be an
    ELF-range address. It is, 3.3% of the time, against 2.9% for a control of
    random in-block offsets -- the same to within noise. So this test says
    nothing either way and the vtable model is unsupported, not disproved.
    (On the ten smallest files alone the figures came out 3.1% against 4.9%,
    which reads as evidence AGAINST. Neither reading survives the full corpus;
    the honest answer is that the test does not discriminate.)
  * The sector-alignment argument is WORTHLESS and the probe says so rather
    than quietly dropping it. All 82 `.pck` sit on 2,048-byte boundaries
    against a 41.7% disc-wide baseline, which looks like strong support for
    "these are DMA'd to a fixed address". It is not: **every file of 2 KB or
    more on this disc is sector-aligned**, all 30,993 of them. Size explains it
    completely and `.pck` carries no signal at all.

The object layout inside the payload is UNKNOWN. `.pck` is PARTIAL, not
DECODED.

    python3 03_Tools/research/probe_pck.py [--iso PATH] [--head NAME]
"""

import argparse
import collections
import json
import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from rdrstudio.iso9660 import Iso9660          # noqa: E402
from rdrstudio import dave                     # noqa: E402

REPO = os.path.dirname(os.path.dirname(HERE))
VANILLA = os.path.join(REPO, "01_Vanilla", "RDR_VANILLA.iso")
HEADER = 16
ELF_BASE = 0x00100000
ELF_SIZE = 4497384


def parse_header(data):
    base, one, zero, size = struct.unpack_from("<4I", data, 0)
    return {"base": base, "w1": one, "w2": zero, "size": size,
            "size_ok": size == len(data) - HEADER}


def pointers(data, base, size):
    """Words that land inside the block, and how aligned they are."""
    end = base + size
    found = []
    n = 0
    for off in range(HEADER, len(data) - 3, 4):
        w, = struct.unpack_from("<I", data, off)
        n += 1
        if base <= w < end:
            found.append((off, w))
    return n, found


def inflate(backing, e):
    raw = backing.read_at(e.offset, e.size_comp)
    return raw if not e.compressed else zlib.decompress(raw, -15)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso", default=VANILLA)
    ap.add_argument("--head", default="", help="hexdump this file's header")
    ap.add_argument("--json", default=os.path.join(REPO, "07_Research", "pck.json"))
    args = ap.parse_args()

    iso = Iso9660(args.iso)
    files = []
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b"Dave", b"DAVE"):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            if e.ext != "pck":
                continue
            files.append((f.path, e.name.rsplit("/", 1)[-1],
                          inflate(d.backing, e), e.compressed, e.aligned))
    iso.close()

    rows, bad_size = [], []
    tot_words = tot_ptr = tot_aligned = tot_vtab = tot_ctrl = 0
    bases = collections.Counter()
    import random
    random.seed(1)

    for arch, name, blob, comp, aligned in files:
        h = parse_header(blob)
        bases["0x%08x" % h["base"]] += 1
        if not h["size_ok"]:
            bad_size.append({"name": name, "size": len(blob), "declared": h["size"]})
        n, ptr = pointers(blob, h["base"], h["size"])
        al = sum(1 for _o, p in ptr if (p - h["base"]) % 4 == 0)
        # do pointer targets start with an ELF-range word (a vtable)?
        vt = 0
        for _o, p in ptr:
            fo = (p - h["base"]) + HEADER
            if fo + 4 <= len(blob):
                t, = struct.unpack_from("<I", blob, fo)
                if ELF_BASE <= t < ELF_BASE + ELF_SIZE:
                    vt += 1
        ctrl = 0
        for _ in range(len(ptr)):
            fo = HEADER + 4 * random.randrange(max(h["size"] // 4, 1))
            if fo + 4 <= len(blob):
                t, = struct.unpack_from("<I", blob, fo)
                if ELF_BASE <= t < ELF_BASE + ELF_SIZE:
                    ctrl += 1
        window = 100.0 * h["size"] / 2 ** 32
        rate = 100.0 * len(ptr) / max(n, 1)
        rows.append({
            "archive": arch, "name": name, "size": len(blob),
            "base": h["base"], "declared": h["size"], "size_ok": h["size_ok"],
            "compressed": comp, "sector_aligned": aligned,
            "words": n, "pointers": len(ptr), "aligned_pointers": al,
            "pointer_rate_pct": round(rate, 4),
            "window_pct": round(window, 6),
            "enrichment": round(rate / window, 1) if window else None,
            "cd_bytes": blob.count(b"\xcd"),
        })
        tot_words += n; tot_ptr += len(ptr); tot_aligned += al
        tot_vtab += vt; tot_ctrl += ctrl

        if args.head and name == args.head:
            print("%s  %d bytes, base 0x%08x" % (name, len(blob), h["base"]))
            for off in range(0, min(0x60, len(blob)), 4):
                w, = struct.unpack_from("<I", blob, off)
                fl, = struct.unpack_from("<f", blob, off)
                note = ""
                if h["base"] <= w < h["base"] + h["size"]:
                    note = "-> block +0x%x" % (w - h["base"])
                elif ELF_BASE <= w < ELF_BASE + ELF_SIZE:
                    note = "-> executable"
                elif 1e-3 < abs(fl) < 1e5:
                    note = "float %g" % fl
                print("  +0x%02x  %s  0x%08x  %s"
                      % (off, blob[off:off + 4].hex(" "), w, note))
            print()

    json.dump({"files": len(files), "bad_size": bad_size,
               "bases": bases.most_common(), "rows": rows},
              open(args.json, "w"), indent=1)

    print("%d .pck files; %d satisfy  declared size == file size - 16, %d do not"
          % (len(files), len(files) - len(bad_size), len(bad_size)))
    for x in bad_size:
        print("   FAIL %-28s size %d declared %d" % (x["name"], x["size"], x["declared"]))
    print("all stored uncompressed: %s" % all(not r["compressed"] for r in rows))
    print("distinct base addresses: %s"
          % ", ".join("%s x%d" % (b, c) for b, c in bases.most_common()))

    worst = min(rows, key=lambda r: r["enrichment"] or 1e9)
    best = max(rows, key=lambda r: r["enrichment"] or 0)
    print("\nself-pointer test -- words landing in [base, base+size):")
    print("   %d of %d words (%.3f%%) across all 82"
          % (tot_ptr, tot_words, 100.0 * tot_ptr / tot_words))
    print("   %.1f%% of them are 4-byte aligned (random data would be 25%%)"
          % (100.0 * tot_aligned / max(tot_ptr, 1)))
    print("   enrichment over each file's own window: %.0fx (%s) .. %.0fx (%s)"
          % (worst["enrichment"], worst["name"], best["enrichment"], best["name"]))
    print("\ncontrol -- do pointer targets start with an executable address?")
    print("   %.1f%% do, against %.1f%% for random in-block offsets."
          % (100.0 * tot_vtab / max(tot_ptr, 1), 100.0 * tot_ctrl / max(tot_ptr, 1)))
    print("   The same to within noise, so this test does not discriminate:")
    print("   the vtable-object model is unsupported, not disproved.")
    print("\n%-28s %9s %10s %8s %9s %9s" % (
        "NAME", "SIZE", "BASE", "PTRS", "RATE", "ENRICH"))
    for r in sorted(rows, key=lambda r: r["size"])[:10]:
        print("%-28s %9d 0x%08x %8d %8.3f%% %8.0fx" % (
            r["name"][:28], r["size"], r["base"], r["pointers"],
            r["pointer_rate_pct"], r["enrichment"]))
    print("\n-> %s" % args.json)


if __name__ == "__main__":
    main()
