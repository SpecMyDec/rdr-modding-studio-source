#!/usr/bin/env python3
"""Action opcode 1560 = HUD / cutscene command; its argument is named in the executable.

    py -3 03_Tools/research/probe_hud_commands.py      (reads 01_Vanilla + the unpacked executable)

Claim: the integer after opcode 1560 indexes the 61 C strings at 0x00527C35
(ShowFocusBar = 0 .. DoneTalking = 60). Checked three independent ways:

  1. corpus   every use of 1560 in every shipped .actb/.stateb has an argument 0..60;
  2. meaning  the names predict the neighbours and the files: StartPuppetshow* is
              followed by "play a cutscene" (1522), hidefocusbar.actb uses HideFocusBar,
              StopSubtitles lives in *talk* files, DoneTalking in *dialog* files;
  3. code     the action executor bounds the value at 61 and jumps through a 61-slot
              table whose Show/Hide pairs set and clear the same HUD bit.

Exit 0 only if all three hold.
"""
import collections
import os
import re
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
from rdrstudio.iso9660 import Iso9660          # noqa: E402
from rdrstudio import dave, aivocab            # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
VANILLA = os.path.join(REPO, "01_Vanilla", "RDR_VANILLA.iso")
ELF = os.path.join(REPO, "07_Research", "SLUS_205.00.unpacked.bin")
BAD = []


def check(ok, what):
    print(("  ok   " if ok else "  FAIL ") + what)
    if not ok:
        BAD.append(what)


def toks(b):
    i, out = 8, []
    while i + 4 <= len(b):
        j = i
        while j < len(b) and 32 <= b[j] < 127:
            j += 1
        if j - i >= 2 and j < len(b) and b[j] == 0:
            out.append(b[i:j].decode())
            i = j + 1
            continue
        out.append(struct.unpack_from("<I", b, i)[0])
        i += 4
    return out


def corpus():
    iso = Iso9660(VANILLA)
    for f in iso.files():
        if f.size < 16 or iso.read_at(f.offset, 4) not in (b"Dave", b"DAVE"):
            continue
        d = dave.Dave(dave.IsoBacking(iso, f.path), label=f.path)
        for e in d.entries:
            if e.ext not in ("actb", "stateb"):
                continue
            raw = d.backing.read_at(e.offset, e.size_comp)
            try:
                b = zlib.decompress(raw, -15) if e.compressed else raw
            except zlib.error:
                continue
            if b.startswith(b"version\x00"):
                yield e.name.lower(), b


def main():
    H = aivocab.HUD_COMMANDS
    ix = {n: i for i, n in enumerate(H)}
    uses = bad = files = 0
    nxt = collections.defaultdict(collections.Counter)
    words = collections.defaultdict(collections.Counter)
    for name, b in corpus():
        t = toks(b)
        hit = False
        for i, x in enumerate(t[:-1]):
            if x != 1560:
                continue
            a = t[i + 1]
            if not isinstance(a, int) or a >= len(H):
                bad += 1
                continue
            uses += 1
            hit = True
            nxt[a][t[i + 2] if i + 2 < len(t) else None] += 1
            for w in re.split(r"[^a-z]+", name.rsplit(".", 1)[0]):
                if len(w) > 2:
                    words[a][w] += 1
        files += hit
    print("1. corpus: %d uses of 1560 in %d files, %d with an argument outside 0..60" % (uses, files, bad))
    check(uses > 1500 and bad == 0, "every argument is 0..60")
    print("2. meaning")
    a = ix["StartPuppetshowFadein"]
    n = sum(nxt[a].values())
    check(nxt[a][1522] * 10 >= n * 8, "StartPuppetshowFadein is followed by 1522 (play a cutscene) %d of %d" % (nxt[a][1522], n))
    for cmd, word in (("HideFocusBar", "hidefocusbar"), ("StopSubtitles", "talk"),
                      ("DoneTalking", "dialog"), ("SuppressCannonIcon", "bridge"),
                      ("FreezeFrame", "dialog"), ("UnfreezeCut", "generic")):
        c = words[ix[cmd]]
        top = [w for w, _ in c.most_common(3)]
        check(word in top, "%s sits in *%s* files (top words %s)" % (cmd, word, top))
    print("3. code")
    if not os.path.exists(ELF):
        print("  [skip] no unpacked executable")
    else:
        img = open(ELF, "rb").read()
        w = lambda va: struct.unpack_from("<I", img, va - 0x100000)[0]   # noqa: E731
        check(aivocab.extract_hud_commands(ELF) == H, "the 61 names re-read from 0x%08X" % aivocab.HUD_POOL_ADDR)
        sw = w(aivocab.HUD_SWITCH_ADDR)
        check((sw >> 26, sw & 0xFFFF) == (11, 61), "executor: sltiu ..., 61 at 0x%08X" % aivocab.HUD_SWITCH_ADDR)

        def bit(case):
            """(set?, mask) for a Show/Hide case of the HUD word, else None."""
            h = w(aivocab.HUD_SWITCH_TABLE + 4 * case)
            for k in range(4):
                x = w(h + 4 * k)
                if x >> 26 == 13 and (x >> 21) & 31 == (x >> 16) & 31:      # ori r, r, mask
                    return True, x & 0xFFFF
                if x >> 26 == 9 and (x >> 21) & 31 == 0 and x & 0x8000:     # addiu r, zero, ~mask
                    return False, (~(x | ~0xFFFF)) & 0xFFFF
            return None
        for show, hide in (("ShowFocusBar", "HideFocusBar"), ("ShowHealthBar", "HideHealthBar"),
                           ("ShowMoney", "HideMoney"), ("ShowReticle", "HideReticle")):
            s, h = bit(ix[show]), bit(ix[hide])
            check(bool(s and h and s[0] and not h[0] and s[1] == h[1]),
                  "%s sets and %s clears the same HUD bit (%s)" % (show, hide, s and hex(s[1])))
    print("RESULT", "FAIL" if BAD else "PASS")
    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
