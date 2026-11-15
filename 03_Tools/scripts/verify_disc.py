"""Full-disc integrity sweep.

Decompresses every file in every archive and reports:
  - any entry that fails to parse or inflate  (library correctness)
  - which extensions are ALWAYS sector-aligned (the DMA rule)
  - which extensions are ALWAYS stored raw    (the no-compress rule)
  - slot headroom distribution                (how much room mods have)

Run:  python scripts/verify_disc.py <iso>
"""

import collections
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rdrstudio import Game  # noqa: E402
from rdrstudio.dave import SECTOR  # noqa: E402
from rdrstudio import config  # noqa: E402


def main(iso_path, out_json=None):
    t0 = time.time()
    g = Game(iso_path)
    archives = g.archives()
    print("%d archives in %s\n" % (len(archives), os.path.basename(iso_path)))

    ext_align = collections.defaultdict(lambda: [0, 0])   # ext -> [aligned, total]
    ext_raw = collections.defaultdict(lambda: [0, 0])     # ext -> [raw, total]
    failures = []
    totals = dict(entries=0, bytes_stored=0, bytes_full=0,
                  raw=0, aligned=0, zero_headroom=0)
    per_archive = {}

    for arc in archives:
        try:
            d = g.archive(arc)
        except Exception as ex:
            failures.append((arc, "<archive>", "%s: %s" % (type(ex).__name__, ex)))
            continue

        bad = 0
        for e in d:
            totals["entries"] += 1
            totals["bytes_stored"] += e.size_comp
            totals["bytes_full"] += e.size_full
            ext = e.ext or "<none>"

            ext_align[ext][1] += 1
            ext_raw[ext][1] += 1
            if e.aligned:
                ext_align[ext][0] += 1
                totals["aligned"] += 1
            if not e.compressed:
                ext_raw[ext][0] += 1
                totals["raw"] += 1
            if e.headroom <= 0:
                totals["zero_headroom"] += 1

            try:
                data = d.read(e)
                if len(data) != e.size_full:
                    raise ValueError("length %d != TOC %d" % (len(data), e.size_full))
            except Exception as ex:
                bad += 1
                if len(failures) < 200:
                    failures.append((arc, e.name, "%s: %s" % (type(ex).__name__, ex)))

        per_archive[arc] = dict(magic=d.magic, count=d.count, size=d.size, bad=bad)
        print("  %-24s %-5s %6d files  %9.1f MB  %s"
              % (arc, d.magic, d.count, d.size / 1048576.0,
                 "OK" if bad == 0 else "%d BAD" % bad))
        # Archives are cached; drop them so 2 GB of TOCs don't pile up.
        g._cache.pop(arc.upper(), None)

    print("\n" + "=" * 66)
    print("entries          : %d" % totals["entries"])
    print("stored / full    : %.1f MB / %.1f MB"
          % (totals["bytes_stored"] / 1048576.0, totals["bytes_full"] / 1048576.0))
    print("stored raw       : %d (%.1f%%)"
          % (totals["raw"], 100.0 * totals["raw"] / totals["entries"]))
    print("sector-aligned   : %d (%.1f%%)"
          % (totals["aligned"], 100.0 * totals["aligned"] / totals["entries"]))
    print("zero headroom    : %d  (cannot grow at all)" % totals["zero_headroom"])
    print("decode failures  : %d" % len(failures))

    always_aligned = sorted(k for k, (a, t) in ext_align.items() if a == t and t >= 3)
    always_raw = sorted(k for k, (r, t) in ext_raw.items() if r == t and t >= 3)
    print("\nALWAYS sector-aligned (>=3 samples):\n  " + " ".join(always_aligned))
    print("\nALWAYS stored raw (>=3 samples):\n  " + " ".join(always_raw))

    if failures:
        print("\nfirst failures:")
        for a, n, m in failures[:20]:
            print("  %s :: %s -> %s" % (a, n, m))

    print("\nelapsed %.1fs" % (time.time() - t0))

    if out_json:
        with open(out_json, "w") as f:
            json.dump(dict(
                iso=iso_path, totals=totals, per_archive=per_archive,
                always_aligned=always_aligned, always_raw=always_raw,
                ext_align={k: v for k, v in ext_align.items()},
                ext_raw={k: v for k, v in ext_raw.items()},
                failures=failures[:200],
            ), f, indent=1)
        print("wrote %s" % out_json)
    return 1 if failures else 0


if __name__ == "__main__":
    # config, not a `D:\` literal: `config.ROOT` derives from the module's
    # own location, so this registered row is no longer Windows-only.
    # REQ-035, in a file that was never on its list.
    iso = sys.argv[1] if len(sys.argv) > 1 else config.VANILLA_ISO
    out = sys.argv[2] if len(sys.argv) > 2 else None
    sys.exit(main(iso, out))
