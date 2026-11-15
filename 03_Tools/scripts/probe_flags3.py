"""Is `flags` a VU1 address, a batch size, or a draw layer?

Ruled out so far: it is not a bitfield (contiguous 16*k, k=2..12), not a
byte size (bytes/vertex is flat ~35 for every k), and not the UNPACK count.
Every part uses the same vertex format regardless of k.

What is left is something about how the packet is fed to VU1. 16*k with
k=2..12 is the right shape for a double-buffer stride, and it is the right
numeric range for a VU1 data address. This measures both.
"""
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rdrstudio import Game, rsc, vif  # noqa: E402
from rdrstudio.config import VANILLA_ISO  # noqa: E402

LEVELS = [("LVLS_SNG/TOMB.DAT", "lvl_tomb.rsc"),
          ("LVLS_SNG/PERFNEW.DAT", "lvl_perfnew.rsc"),
          ("LVLS_SNG/WOODS.DAT", "lvl_woods.rsc")]


def main():
    game = Game(VANILLA_ISO)
    addr_hit = Counter()
    stride_hit = Counter()
    batch = defaultdict(list)
    total = 0
    examples = defaultdict(list)

    for arc, area in LEVELS:
        try:
            r = rsc.Rsc(game.archive(arc).read(area), area)
        except Exception:
            continue
        for c in r.chunks:
            if c.tag != "MOD0":
                continue
            parts = r.parts(c.index)
            if not parts:
                continue
            raw = r.raw(c.index)
            for p in parts:
                f, k = p["flags"], p["flags"] // 16
                seg = raw[p["offset"]:p["end"] or len(raw)]
                ups = [u for u in vif.parse(seg) if getattr(u, "kind", None)]
                if not ups:
                    continue
                total += 1
                addrs = [u.addr for u in ups]
                nums = [u.num for u in ups]

                if f in addrs:
                    addr_hit["flags is some UNPACK addr"] += 1
                if addrs and addrs[0] == f:
                    addr_hit["flags == first UNPACK addr"] += 1
                if max(addrs) < f:
                    addr_hit["flags > every addr"] += 1
                # A double-buffered VU1 loop unpacks into alternating halves;
                # the stride between successive base addresses would be k.
                deltas = {b - a for a, b in zip(addrs, addrs[1:]) if b > a}
                if k in deltas:
                    stride_hit["k appears as an addr delta"] += 1
                if f in deltas:
                    stride_hit["flags appears as an addr delta"] += 1

                # Vertices per UNPACK batch -- the classic PS2 chunk size.
                batch[k].append(max(nums))
                if len(examples[k]) < 2:
                    examples[k].append(
                        (max(nums), sorted(set(addrs))[:8], len(ups)))

    print("parts examined: %d\n" % total)
    print("address tests")
    for kk, v in addr_hit.most_common():
        print("  %-34s %6d  (%.0f%%)" % (kk, v, 100.0 * v / total))
    print("\nstride tests")
    for kk, v in stride_hit.most_common():
        print("  %-34s %6d  (%.0f%%)" % (kk, v, 100.0 * v / total))

    print("\nmax vertices per UNPACK batch, by k")
    for k in sorted(batch):
        v = batch[k]
        print("  k=%-3d n=%-5d batch min %3d  max %3d  mode %3d"
              % (k, len(v), min(v), max(v), Counter(v).most_common(1)[0][0]))

    print("\nexamples (max batch, distinct addrs, unpacks)")
    for k in sorted(examples):
        for e in examples[k]:
            print("  k=%-3d batch=%-4d addrs=%s  unpacks=%d"
                  % (k, e[0], e[1], e[2]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
