"""Concurrent disc reads must not corrupt each other.

The Studio runs on `ThreadingHTTPServer`, so two browser requests genuinely
execute at once — and the whole tool reads the disc through ONE file object.
`seek(offset)` then `read(length)` is two calls; another thread can seek
between them, so thread A reads from thread B's offset. The failure is not a
crash. It is worse:

  * sometimes a short read, surfaced as "2 parse warning(s)" in the HUD;
  * sometimes a *successful* read of the wrong bytes, which the parsers skip
    as unrecognised, so the level quietly comes back with 730 nodes instead of
    896 -- and because `_levels` caches it, that half-level is what you edit
    for the rest of the session.

This test drives the real `Iso` and the real `Dave` from many threads and
checks two things: every read returns the bytes that belong to its own offset,
and a level parsed under load has the same node count as one parsed alone.
"""
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdrstudio.game import Game                    # noqa: E402
from rdrstudio.level import Level                  # noqa: E402
from rdrstudio import config                         # noqa: E402

# The PRISTINE disc. This is a read-only structural check, so it must not
# baseline on the working copy -- that makes its result depend on whatever
# the last write left behind. Was a hardcoded `D:\...\RDR_MODDED.iso`,
# which also meant this only ran under Windows python. REQ-035.
DISC = Path(config.VANILLA_ISO)
ARCHIVE = "LVLS_SNG/TOMB.DAT"
THREADS = 8
ROUNDS = 40


def main():
    game = Game(DISC, writable=False)
    iso = game.iso

    # A spread of offsets across the disc, each with the bytes it must return.
    entries = [e for e in game.iso.files() if e.size > 8192][:24]
    truth = {}
    for e in entries:
        truth[e.offset] = iso.read_at(e.offset, 4096)
    print("baseline: %d probe offsets read single-threaded" % len(truth))

    bad = []
    lock = threading.Lock()

    def hammer():
        for _ in range(ROUNDS):
            for off, want in truth.items():
                try:
                    got = iso.read_at(off, 4096)
                except Exception as ex:
                    with lock:
                        bad.append("0x%X raised %s" % (off, str(ex)[:60]))
                    continue
                if got != want:
                    with lock:
                        bad.append("0x%X returned the wrong bytes" % off)

    ts = [threading.Thread(target=hammer) for _ in range(THREADS)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    reads = THREADS * ROUNDS * len(truth)
    print("%d concurrent reads, %d wrong" % (reads, len(bad)))
    for line in bad[:5]:
        print("   %s" % line)

    # The consequence that actually bit: a level parsed under load must not
    # come back smaller than the same level parsed alone.
    alone = Level(game, ARCHIVE)
    want_nodes = len(alone.nodes)
    want_err = len(alone.errors)
    print("\n%s alone: %d nodes, %d parse errors" % (ARCHIVE, want_nodes, want_err))

    counts, errs = [], []

    def parse_shared():
        lv = Level(game, ARCHIVE)          # same Game, same shared file handle
        with lock:
            counts.append(len(lv.nodes))
            errs.extend(lv.errors)

    ts = [threading.Thread(target=parse_shared) for _ in range(THREADS)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    print("under %d-way load: node counts %s" % (THREADS, sorted(set(counts))))
    print("parse errors raised: %d" % len(errs))
    for line in errs[:4]:
        print("   %s" % line[:90])

    ok = (not bad
          and all(c == want_nodes for c in counts)
          and len(errs) == want_err)
    print("\n%s" % ("ALL CHECKS PASSED" if ok else "SOMETHING FAILED"))
    game.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
