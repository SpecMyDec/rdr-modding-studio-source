"""probe_pup.py -- the `.pup` cutscene roster, proved over all 1,200 files.

`.pup` ("pupshow" -- puppet show, the engine's name for a scripted cutscene)
was the largest file type on the disc that NOTHING in `03_Tools/rdrstudio/`
parsed: 1,200 files, 24-739 bytes, no module, no mention.

Hypothesis, read off three hexdumps and then tested against every one:

    u32     count              how many actors this cutscene casts
    count x {
        char[]  name           NUL-terminated ASCII
        u32     role           what the actor is (see ROLES below)
    }
    u32     nframes
    nframes x {
        u32     time
        u8      state[count]   one byte per actor, in roster order
    }

The `state[count]` tie is the load-bearing part of the claim: the per-frame
block is sized by the SAME count that sizes the roster, which is what makes
the two halves one format rather than two guesses. A file passes only if the
parse lands exactly on EOF -- no slack, no overrun.

    python3 03_Tools/research/probe_pup.py [--iso PATH] [--dump NAME]
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


class BadPup(Exception):
    pass


def parse(data):
    if len(data) < 4:
        raise BadPup("too short")
    count, = struct.unpack_from("<I", data, 0)
    if not 0 < count < 512:
        raise BadPup("implausible actor count %d" % count)
    pos = 4
    actors = []
    for i in range(count):
        end = data.find(b"\x00", pos)
        if end < 0:
            raise BadPup("unterminated name for actor %d" % i)
        name = data[pos:end]
        if not name or any(not (32 <= c < 127) for c in name):
            raise BadPup("non-ASCII name for actor %d" % i)
        pos = end + 1
        if pos + 4 > len(data):
            raise BadPup("truncated role for actor %d" % i)
        role, = struct.unpack_from("<I", data, pos)
        pos += 4
        actors.append((name.decode("ascii"), role))

    frames = []
    if pos + 4 <= len(data):
        nframes, = struct.unpack_from("<I", data, pos)
        pos += 4
        stride = 4 + count
        for i in range(nframes):
            if pos + stride > len(data):
                raise BadPup("truncated frame %d of %d" % (i, nframes))
            time, = struct.unpack_from("<I", data, pos)
            state = data[pos + 4:pos + stride]
            frames.append((time, state))
            pos += stride
    if pos != len(data):
        raise BadPup("parse ended at %d of %d bytes" % (pos, len(data)))
    return actors, frames


def inflate(backing, e):
    raw = backing.read_at(e.offset, e.size_comp)
    return raw if not e.compressed else zlib.decompress(raw, -15)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso", default=VANILLA)
    ap.add_argument("--dump", default="")
    ap.add_argument("--json", default=os.path.join(REPO, "07_Research", "pup.json"))
    args = ap.parse_args()

    iso = Iso9660(args.iso)
    ok = 0
    fails = []
    roles = collections.Counter()
    role_names = collections.defaultdict(collections.Counter)
    names = collections.Counter()
    nactors = collections.Counter()
    total = 0
    seen = set()
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b"Dave", b"DAVE"):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            if e.ext != "pup":
                continue
            total += 1
            try:
                blob = inflate(d.backing, e)
            except zlib.error:
                fails.append({"archive": f.path, "name": e.name, "why": "inflate"})
                continue
            if args.dump and e.name == args.dump:
                print(blob[:256].hex(" "))
            try:
                actors, frames = parse(blob)
            except BadPup as ex:
                fails.append({"archive": f.path, "name": e.name, "why": str(ex),
                              "size": len(blob)})
                continue
            ok += 1
            nactors[len(actors)] += 1
            key = (f.path, e.name)
            if key not in seen:
                seen.add(key)
                for nm, role in actors:
                    roles[role] += 1
                    names[nm] += 1
                    role_names[role][nm] += 1

    report = {
        "files": total, "parsed": ok, "failed": len(fails),
        "fails": fails[:20],
        "roles": roles.most_common(),
        "role_profile": {
            "0x%02x" % r: {
                "slots": sum(c.values()),
                "distinct_names": len(c),
                "names_ending_Facial": sum(v for k, v in c.items()
                                           if k.endswith("Facial")),
                "names_ending_anim": sum(v for k, v in c.items()
                                         if k.endswith("_anim")),
                "examples": [k for k, _ in c.most_common(5)],
            } for r, c in role_names.items()},
        "top_names": names.most_common(25),
        "actor_counts": sorted(nactors.items()),
    }
    with open(args.json, "w") as fh:
        json.dump(report, fh, indent=1)

    print("%d .pup files; %d parse to EOF exactly, %d fail" % (total, ok, len(fails)))
    for x in fails[:10]:
        print("   FAIL %-30s %-28s %s" % (x["archive"], x["name"], x["why"]))
    print("\nactors per cutscene: %d..%d" % (min(nactors), max(nactors)))
    # What each role code is, by what it is ever attached to. 0x07 is only
    # ever "camera" and appears exactly once per file; 0x0f/0x10 are almost
    # only ever `<char>Facial`. The codes at 0x7b and above carry the bare
    # three-letter character stems that `.cr` uses for FileNameRender, which
    # is why they read as creature ids -- that last part is INFERRED from the
    # naming, not proved here.
    print("\n%-6s %6s %8s %8s %7s  %s" % (
        "ROLE", "SLOTS", "DISTINCT", "Facial", "_anim", "EXAMPLES"))
    for r, c in roles.most_common():
        nm = role_names[r]
        print("0x%-4x %6d %8d %8d %7d  %s" % (
            r, c, len(nm),
            sum(v for k, v in nm.items() if k.endswith("Facial")),
            sum(v for k, v in nm.items() if k.endswith("_anim")),
            ", ".join(k for k, _ in nm.most_common(4))))
    print("\nmost-cast names:")
    for nm, c in names.most_common(15):
        print("   %-18s %d" % (nm, c))
    print("-> %s" % args.json)
    iso.close()


if __name__ == "__main__":
    main()
