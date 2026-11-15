#!/usr/bin/env python3
"""Measure the one slow step of first run: copying the user's ISO.

The app must never write to the file the user picked -- that is their disc
image and the only pristine copy they have. So first run makes a working copy,
and that copy is the longest thing the app will ever make them wait for. This
measures it honestly, with the same chunked read/write loop the real flow uses
so progress can be reported.

    python packaging/measure_copy.py <dest.iso>

Reads the vanilla ISO. Writes only to the destination you name, and deletes it
again unless you pass --keep.
"""

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from rdrstudio.config import VANILLA_ISO                   # noqa: E402

SRC = VANILLA_ISO
CHUNK = 8 << 20        # 8 MiB: big enough to saturate a spinning disk


def copy_with_progress(src, dst, chunk=CHUNK, report=None):
    total = os.path.getsize(src)
    done = 0
    t0 = time.perf_counter()
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        while True:
            buf = fi.read(chunk)
            if not buf:
                break
            fo.write(buf)
            done += len(buf)
            if report:
                report(done, total, time.perf_counter() - t0)
        fo.flush()
        os.fsync(fo.fileno())          # the bytes are really on the platter
    return total, time.perf_counter() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dest")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--chunk-mb", type=int, default=8)
    a = ap.parse_args()

    if not os.path.isfile(SRC):
        sys.exit("no source ISO at %s" % SRC)

    last = [0.0]

    def report(done, total, elapsed):
        if elapsed - last[0] >= 2.0:
            last[0] = elapsed
            pct = 100.0 * done / total
            rate = done / elapsed / 1048576.0
            eta = (total - done) / (done / elapsed) if done else 0
            sys.stdout.write("\r  %5.1f%%  %6.1f MB/s  eta %3.0fs" % (pct, rate, eta))
            sys.stdout.flush()

    print("copy %s" % SRC)
    print("  -> %s" % a.dest)
    total, secs = copy_with_progress(SRC, a.dest, a.chunk_mb << 20, report)
    print("\r  done: %.2f GB in %.1f s  =  %.0f MB/s%s"
          % (total / 1073741824.0, secs, total / secs / 1048576.0, " " * 12))

    if not a.keep:
        os.remove(a.dest)
        print("  (removed)")


if __name__ == "__main__":
    main()
