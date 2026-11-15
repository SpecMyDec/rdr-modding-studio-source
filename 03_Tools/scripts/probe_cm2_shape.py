"""Does a cm2 payload's SIZE depend on frames, on channels, or on neither?

Three `bow_red_*` animations with 18 channels are all exactly 48 bytes of
payload despite having 2, 33 and 60 frames. If that holds generally it kills
the last version of the "keyframed stream" picture and says the payload is a
fixed-size-per-channel record -- which would make the format tractable.

It also groups by the header field at +0x10, because that field's values are
all congruent to 8 or 9 mod 16 (8, 9, 264, 265, 1032, 1033, 1544, 2568, 3080)
which is the signature of a flags word, not a count. If the layout depends on
those flags then every earlier attempt to find ONE grammar was doomed.

Prints, per +0x10 group:
  * payload size as a function of channels (is it a*ch + b?)
  * whether payload size varies at all when frames vary and channels do not
"""
import struct
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdrstudio.game import Game                      # noqa: E402
from rdrstudio import config                          # noqa: E402

# REQ-035/036. Was a literal path to the WORKING ISO on one machine:
# unrunnable on anyone else's checkout, and pointed at the disc the live
# tests mutate. What this examines is a property of the SHIPPED data, so
# it reads the stock disc. Read-only throughout, which is what makes
# vanilla the right answer here rather than merely the tidier one.
DISC = Path(config.VANILLA_ISO)
HDR = 20
LIMIT = 2500          # distinct animations to sample


def main():
    game = Game(DISC, writable=False)
    print("collecting .anim headers...")
    rows = []
    seen = set()
    for arc in game.archives():
        try:
            toc = game.toc(arc)
        except Exception:
            continue
        for e in toc:
            n = e.name.lower()
            if not n.endswith(".anim") or n in seen:
                continue
            seen.add(n)
            try:
                data = game.read(arc, e.name)
            except Exception:
                continue
            if data[:4] not in (b"cm2\x00", b"cmp\x00"):
                continue
            frames, channels, f0c, f10 = struct.unpack_from("<IIII", data, 4)
            if not channels:
                continue
            rows.append(dict(name=e.name, size=len(data), pay=len(data) - HDR,
                             frames=frames, ch=channels, f0c=f0c, f10=f10))
            if len(rows) >= LIMIT:
                break
        if len(rows) >= LIMIT:
            break
    print("%d distinct animations sampled\n" % len(rows))

    # --- 1. within a fixed channel count, does payload move with frames? ----
    print("=" * 70)
    print("Q1: with channels FIXED, does payload size follow frames?")
    print("=" * 70)
    byc = defaultdict(list)
    for r in rows:
        byc[r["ch"]].append(r)
    flat = 0
    varies = 0
    for ch in sorted(byc)[:14]:
        g = byc[ch]
        if len(g) < 3:
            continue
        pays = {x["pay"] for x in g}
        frs = {x["frames"] for x in g}
        same = len(pays) == 1
        flat += same
        varies += (not same)
        print("  ch=%-4d n=%-5d distinct payload sizes=%-5d distinct frames=%-5d %s"
              % (ch, len(g), len(pays), len(frs),
                 "PAYLOAD CONSTANT" if same else ""))
        if not same and len(g) >= 4:
            g2 = sorted(g, key=lambda x: x["frames"])
            for x in (g2[0], g2[len(g2) // 2], g2[-1]):
                print("        frames=%-5d pay=%-6d f10=%-6d %s"
                      % (x["frames"], x["pay"], x["f10"], x["name"][:40]))
    print("\n  channel groups with a CONSTANT payload size: %d; varying: %d"
          % (flat, varies))

    # --- 2. group by the +0x10 flags ----------------------------------------
    print("\n" + "=" * 70)
    print("Q2: group by header +0x10 -- does each group have its own shape?")
    print("=" * 70)
    byf = defaultdict(list)
    for r in rows:
        byf[r["f10"]].append(r)
    for f10 in sorted(byf, key=lambda k: -len(byf[k]))[:10]:
        g = byf[f10]
        print("\n  +0x10 = %-6d (0x%04X)  n=%d   low nibble %d"
              % (f10, f10, len(g), f10 & 0xF))
        # payload = a*ch + b ?  solve from the two extreme channel counts
        chs = sorted({x["ch"] for x in g})
        if len(chs) >= 2:
            lo = min(g, key=lambda x: x["ch"])
            hi = max(g, key=lambda x: x["ch"])
            if hi["ch"] != lo["ch"]:
                a = (hi["pay"] - lo["pay"]) / (hi["ch"] - lo["ch"])
                b = lo["pay"] - a * lo["ch"]
                ok = sum(1 for x in g
                         if abs(x["pay"] - (a * x["ch"] + b)) < 0.5)
                print("      fit pay = %.4f*ch + %.2f   exact on %d/%d (%.0f%%)"
                      % (a, b, ok, len(g), 100.0 * ok / len(g)))
        # does frames matter inside this group?
        sub = defaultdict(set)
        for x in g:
            sub[x["ch"]].add(x["pay"])
        const = sum(1 for c in sub if len(sub[c]) == 1)
        print("      channel counts here: %d; payload constant within %d of them"
              % (len(sub), const))
        for x in sorted(g, key=lambda y: y["pay"])[:3]:
            print("      ch=%-4d frames=%-5d pay=%-6d  %s"
                  % (x["ch"], x["frames"], x["pay"], x["name"][:44]))

    # --- 3. is +0x0C ever nonzero, and does that change the shape? ----------
    print("\n" + "=" * 70)
    print("Q3: header +0x0C")
    print("=" * 70)
    z = [r for r in rows if r["f0c"] == 0]
    nz = [r for r in rows if r["f0c"] != 0]
    print("  zero: %d   nonzero: %d" % (len(z), len(nz)))
    if nz:
        print("  nonzero examples (u32, and as float):")
        for r in nz[:6]:
            f = struct.unpack("<f", struct.pack("<I", r["f0c"]))[0]
            print("    %-11d -> %12.5f   ch=%-4d frames=%-5d pay=%-6d %s"
                  % (r["f0c"], f, r["ch"], r["frames"], r["pay"], r["name"][:34]))
        # If +0x0C is a float, the "header" is probably 12 bytes, not 20.
        plaus = sum(1 for r in nz
                    if 1e-4 < abs(struct.unpack("<f",
                       struct.pack("<I", r["f0c"]))[0]) < 1e4)
        print("  of the nonzero ones, %d/%d are a plausible small float"
              % (plaus, len(nz)))

    game.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
