"""`.strtbl` holds every string the game displays, keyed by a name hash.

203 files, 10.9 MB. Decoded structure:

    0x00  u32       n_lang (10)
    0x04  u32[10]   cumulative start offset per language slot; slot i spans
                    [off[i], off[i+1]), slot 9 runs to EOF, and off[i] ==
                    off[i+1] means that language is absent
    0x2C  u32       512
    0x30  u32       n_keys
    0x34            n_keys x { u32 len, len bytes ASCII, u8 0 }
                    -- ends exactly at off[0]
    off[i]          u32 count (== n_keys), then count records:
                      u32 id      hash of the identifier
                      u16 flags   1 everywhere
                      u32 fl, fl bytes ASCII    font name, no terminator
                      u32 sl, sl UTF-16LE units text, sl COUNTS THE NUL
                      f32 scale_x
                      f32 scale_y
                      u16 0

THE PART THAT MATTERS: strings are addressed by a HASH of the identifier, not
by position. That is the trap here -- a positional reading looks like it works
because count == n_keys, so the arrays line up in length while being in a
different order.

The hash is classic PJW/ELF, case-sensitive over the raw ASCII bytes:

    h = 0
    for c in name:
        h = (h << 4) + c
        g = h & 0xF0000000
        if g: h ^= g >> 24; h &= ~g

The result is always < 2^28, which is why the ids look like a structured
bitfield rather than a hash at a glance.

This script re-derives all of it from the disc: it re-encodes every file from
its parsed model and demands byte-identical output, and it scores the hash by
MULTISET EQUALITY against a field of alternatives, so "PJW happens to fit" is
not enough -- the rivals have to fail.
"""
import struct
import sys
import zlib
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rdrstudio import Game  # noqa: E402
from rdrstudio.config import VANILLA_ISO  # noqa: E402


def pjw(name, lower=False, upper=False):
    b = name.lower() if lower else (name.upper() if upper else name)
    h = 0
    for c in b.encode("latin-1", "replace"):
        h = ((h << 4) + c) & 0xFFFFFFFF
        g = h & 0xF0000000
        if g:
            h ^= g >> 24
            h &= (~g) & 0xFFFFFFFF
    return h


def sdbm(name):
    h = 0
    for c in name.encode("latin-1", "replace"):
        h = (c + (h << 6) + (h << 16) - h) & 0xFFFFFFFF
    return h


def fnv1a(name):
    h = 0x811C9DC5
    for c in name.encode("latin-1", "replace"):
        h = ((h ^ c) * 0x01000193) & 0xFFFFFFFF
    return h


def mul(k):
    def f(name):
        h = 0
        for c in name.encode("latin-1", "replace"):
            h = (h * k + c) & 0xFFFFFFFF
        return h
    return f


CANDIDATES = [
    ("pjw", pjw),
    ("pjw-lower", lambda n: pjw(n, lower=True)),
    ("pjw-upper", lambda n: pjw(n, upper=True)),
    ("sdbm", sdbm),
    ("fnv1a", fnv1a),
    ("crc32", lambda n: zlib.crc32(n.encode("latin-1", "replace")) & 0xFFFFFFFF),
    ("mul31", mul(31)),
    ("mul33", mul(33)),
    ("mul65599", mul(65599)),
    ("mul131", mul(131)),
]


def parse(data):
    """Returns (keys, blocks) or raises. blocks: slot -> list of records."""
    n_lang = struct.unpack_from("<I", data, 0)[0]
    if n_lang != 10:
        raise ValueError("n_lang is %d, not 10" % n_lang)
    offs = list(struct.unpack_from("<10I", data, 4))
    if offs != sorted(offs):
        raise ValueError("language offsets are not non-decreasing")
    if offs[9] > len(data):
        raise ValueError("last language offset runs past EOF")
    n_keys = struct.unpack_from("<I", data, 0x30)[0]

    p = 0x34
    keys = []
    for _ in range(n_keys):
        ln = struct.unpack_from("<I", data, p)[0]
        p += 4
        keys.append(data[p:p + ln].decode("latin-1"))
        p += ln + 1                       # trailing NUL
    if p != offs[0]:
        raise ValueError("key table ends at 0x%X, not at 0x%X" % (p, offs[0]))

    bounds = offs + [len(data)]
    blocks = {}
    for i in range(10):
        start, end = bounds[i], bounds[i + 1]
        if start >= end:
            continue
        q = start
        count = struct.unpack_from("<I", data, q)[0]
        q += 4
        recs = []
        for _ in range(count):
            rid, flags = struct.unpack_from("<IH", data, q)
            q += 6
            fl = struct.unpack_from("<I", data, q)[0]
            q += 4
            font = data[q:q + fl].decode("latin-1")
            q += fl
            sl = struct.unpack_from("<I", data, q)[0]
            q += 4
            text = data[q:q + sl * 2].decode("utf-16-le", "replace")
            q += sl * 2
            sx, sy = struct.unpack_from("<2f", data, q)
            q += 8
            tail = struct.unpack_from("<H", data, q)[0]
            q += 2
            recs.append(dict(id=rid, flags=flags, font=font,
                             text=text.rstrip("\x00"), sx=sx, sy=sy,
                             tail=tail))
        if q != end:
            raise ValueError("block %d ends at 0x%X, not 0x%X" % (i, q, end))
        blocks[i] = recs
    return keys, blocks, offs


def encode(data, keys, blocks, offs):
    """Rebuild the file from the parsed model; must equal the input."""
    out = bytearray(data[:0x34])
    for k in keys:
        b = k.encode("latin-1")
        out += struct.pack("<I", len(b)) + b + b"\x00"
    for i in range(10):
        if i not in blocks:
            continue
        recs = blocks[i]
        out += struct.pack("<I", len(recs))
        for r in recs:
            out += struct.pack("<IH", r["id"], r["flags"])
            f = r["font"].encode("latin-1")
            out += struct.pack("<I", len(f)) + f
            t = (r["text"] + "\x00").encode("utf-16-le")
            out += struct.pack("<I", len(t) // 2) + t
            out += struct.pack("<2fH", r["sx"], r["sy"], r["tail"])
    return bytes(out)


def main():
    game = Game(VANILLA_ISO)
    files = parsed = exact = 0
    blocks_ok = 0
    scores = Counter()
    tables = 0
    langs = Counter()
    sample = []
    problems = []

    for arc in game.archives():
        try:
            dv = game.archive(arc)
        except Exception:
            continue
        for e in dv:
            if e.ext != "strtbl":
                continue
            files += 1
            try:
                raw = dv.read(e)
                keys, blocks, offs = parse(raw)
            except Exception as ex:
                problems.append((arc, e.name, str(ex)))
                continue
            parsed += 1
            if encode(raw, keys, blocks, offs) == raw:
                exact += 1
            else:
                problems.append((arc, e.name, "re-encode differs"))

            for slot, recs in blocks.items():
                langs[slot] += 1
                if len(recs) == len(keys):
                    blocks_ok += 1
                tables += 1
                ids = sorted(r["id"] for r in recs)
                for name, fn in CANDIDATES:
                    if sorted(fn(k) for k in keys) == ids:
                        scores[name] += 1
                if slot == 0 and len(sample) < 8:
                    hashed = {pjw(k): k for k in keys}
                    for r in recs:
                        if r["text"] and len(sample) < 8 and r["id"] in hashed:
                            sample.append((hashed[r["id"]], r["text"][:52]))
        game._cache.pop(arc.upper(), None)

    print(".strtbl files       : %d" % files)
    print("parsed              : %d / %d" % (parsed, files))
    print("re-encode identical : %d / %d" % (exact, files))
    print("language blocks     : %d  (count == n_keys in %d)"
          % (tables, blocks_ok))
    print("slots populated     : %s" % dict(sorted(langs.items())))

    print("\nhash candidates -- multiset of H(key) vs the record ids")
    for name, _ in CANDIDATES:
        n = scores.get(name, 0)
        print("   %-12s %4d / %4d tables  %s"
              % (name, n, tables, "MATCH" if n == tables else ""))

    print("\nsample strings recovered by hash lookup")
    for k, t in sample:
        print("   %-30s %s" % (k, t))

    for p in problems[:6]:
        print("   PROBLEM %s / %s: %s" % p)

    winners = [n for n, _ in CANDIDATES if scores.get(n, 0) == tables]
    ok = (files > 100 and parsed == files and exact == files
          and blocks_ok == tables and winners == ["pjw"])
    print("\nexactly one hash matches every table: %s" % (winners or "none"))
    print("\n%s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
