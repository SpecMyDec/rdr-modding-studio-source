"""The UNCOMPRESSED `.anim` codec -- 182 files that are not cm2 or cmp.

The loader at 0x002CF0F0 dispatches on the four-byte magic. `cm2` and `cmp` get
a codec object; ANYTHING ELSE gets the codec pointer NULLed (0x002CF1B8) and
takes a different path at 0x002CF320, which turns out to be no codec at all.

"Anything else" is the exact rule, not "magic is four zeros": 181 of these files
begin with four zero bytes, and one -- `red_shit.anim`, a dev joke ("red" is the
player) -- begins with `00 00 28 4E`, a value that is simply neither cm2 nor cmp.
Its header is otherwise ordinary (48 frames, 162 channels, flags 0x09) and it
fits the size formula below to the byte, so it decodes through this same path.
Filtering on zero magic instead of on the engine's real dispatch would drop it.

Read straight off the instruction stream:

    0x002CF320  andi  v0, v0, 0x1          ; the same half-rate test
    0x002CF32C-4C                          ; keys = frames/2 + 1
    0x002CF350  daddu s2, v0, zero         ; s2 = key count
    0x002CF354  sll   a0, s2, 3            ; keys * 8
    0x002CF35C  jal   0x00219F20           ; malloc(keys*8 + 16)
    0x002CF38C  addiu s0, s0, 8            ; stride 8 -- one record per KEY
    0x002CF3B0  jal   0x002D4BA0           ; fill one key (dest, stream, channels)

and the per-key reader at 0x002D4BA0 ends with

    0x002D4BC0  lh    a2, 0(s0)            ; count, a s16 at key+0
    0x002D4BC8  lw    a1, 4(s0)            ; the key's float buffer, at key+4
    0x002D4BCC  jal   0x0019A070           ; stream read
    0x002D4BD0  sll   a2, a2, 2            ; length = count * 4

`count * 4` is the whole story: **raw float32, one per channel per key, with no
quantiser and no scales**. So the layout is the same 17-byte header followed by

    f32[channels * keys]

and nothing else. Which gives an exact size, and that is what this checks.

The 8-byte key record is `{ s16 count; f32 *data; }` -- the count is written by
0x002D4B20 before the read, and the pointer at +4 is the buffer it allocated.
"""
import math
import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdrstudio.game import Game                      # noqa: E402
from rdrstudio import config                          # noqa: E402

# REQ-035. Was a literal path to the WORKING ISO on one machine:
# unrunnable on anyone else's checkout, and pointed at the disc the
# live tests mutate, so a read-only structural check could go red for
# a reason with nothing to do with the code under test. What this
# measures is a property of the shipped clips, so it reads the stock
# disc. Opened writable=False, which is what makes vanilla correct here.
DISC = Path(config.VANILLA_ISO)
HEADER = 17


def keys_of(frames, flags):
    return (frames // 2 + 1) if (flags & 0x08) else frames


def main():
    game = Game(DISC, writable=False)
    ok = True

    def check(what, got, want, cmp="=="):
        nonlocal ok
        good = (got == want if cmp == "==" else
                got < want if cmp == "<" else got > want)
        ok &= good
        print("  %-52s %-12s %s"
              % (what, str(got)[:12],
                 "ok" if good else "FAIL (want %s %s)" % (cmp, want)))

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
                d = game.read(arc, e.name)
            except Exception:
                continue
            # The engine's real dispatch: not cm2 and not cmp -> this path.
            if len(d) < 20 or d[:4] in (b"cm2\x00", b"cmp\x00"):
                continue
            fr, ch = struct.unpack_from("<II", d, 4)
            rows.append((e.name, arc, d, fr & 0xFFFF, ch & 0xFFFF, d[0x10]))

    print("%d .anim files that are neither cm2 nor cmp\n" % len(rows))
    check("found the uncompressed set", len(rows), 182)

    # ---- the size formula accounts for every byte
    exact = 0
    for name, arc, d, fr, ch, fl in rows:
        if len(d) == HEADER + 4 * ch * keys_of(fr, fl):
            exact += 1
    print("\nsize == 17 + 4 * channels * keys:")
    check("files matching exactly", exact, len(rows))

    # ---- every value is a usable float
    bad = 0
    total = 0
    for name, arc, d, fr, ch, fl in rows:
        k = keys_of(fr, fl)
        vals = struct.unpack_from("<%df" % (ch * k), d, HEADER)
        total += len(vals)
        for v in vals:
            if math.isnan(v) or math.isinf(v) or abs(v) > 1e7:
                bad += 1
    print("\nvalues, read as float32:")
    print("     %s samples across all %d files" % ("{:,}".format(total), len(rows)))
    check("nan / inf / absurd values", bad, 0)

    # ---- curves are SMOOTH, which random bytes would not be
    #
    # This is the check that says the floats are real data rather than a
    # coincidence of alignment: consecutive keys of a channel should differ by
    # far less than the channel's own range.
    ratios = []
    for name, arc, d, fr, ch, fl in rows:
        k = keys_of(fr, fl)
        if k < 4:
            continue
        vals = struct.unpack_from("<%df" % (ch * k), d, HEADER)
        for c in range(ch):
            col = [vals[i * ch + c] for i in range(k)]
            rng = max(col) - min(col)
            if rng <= 1e-6:
                continue
            step = sum(abs(col[i + 1] - col[i]) for i in range(k - 1)) / (k - 1)
            ratios.append(step / rng)
    ratios.sort()
    med = ratios[len(ratios) // 2] if ratios else 1.0
    print("\nsmoothness -- mean |step| as a fraction of each channel's range:")
    print("     %d moving channels; median %.4f, p90 %.4f"
          % (len(ratios), med, ratios[int(len(ratios) * 0.9)] if ratios else 0))
    check("median step is a small fraction of the range", med < 0.15, True)

    # ---- what ARE these files?
    print("\nchannel counts:")
    cc = Counter(r[4] for r in rows)
    for c, n in cc.most_common(10):
        kind = ("skeletal, %d bones" % ((c - 3) // 3)) if (c - 3) % 3 == 0 \
            else ("camera-shaped (eye3 + target3 + lens)" if c == 7 else "other")
        print("     %-5d channels  %3d files   %s" % (c, n, kind))
    cams = [r for r in rows if r[4] == 7]
    print("\n     the 7-channel ones are front-end camera moves:")
    for r in cams[:5]:
        print("        %s" % r[0])
    # A camera clip's 7th channel should be near-constant -- it is the lens.
    if cams:
        name, arc, d, fr, ch, fl = cams[0]
        k = keys_of(fr, fl)
        vals = struct.unpack_from("<%df" % (ch * k), d, HEADER)
        lens = [vals[i * ch + 6] for i in range(k)]
        print("     %s channel 6 spans %.4f..%.4f  (a lens, held constant)"
              % (name, min(lens), max(lens)))
        check("the 7th channel is constant, as a lens would be",
              max(lens) - min(lens) < 1e-4, True)

    print("\n%s" % ("ALL CHECKS PASSED" if ok else "SOMETHING FAILED"))
    game.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
