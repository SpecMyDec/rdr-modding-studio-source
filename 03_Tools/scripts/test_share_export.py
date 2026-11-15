#!/usr/bin/env python3
"""Export: a patch that decodes to the working disc, and the routes around it.

The Export dialog promises three things and this checks each one on files it
builds itself, so it never opens either real ISO, takes no lock and binds no
port:

  1. vcdiff.write_patch produces VCDIFF that a decoder turns back into the
     target, byte for byte, across the shapes an image can take: untouched,
     scattered edits, grown past the source, shrunk, from an empty source.
     Two judges, and the output says which ran:
       * a spec decoder written here from RFC 3284 -- the WHOLE default code
         table and the address caches, not the two instructions the encoder
         happens to emit, so it shares no code and no shortcuts with it;
       * stock xdelta3, when this machine has it (on PATH, or through WSL).
         xdelta3 is what Delta Patcher runs, so it is the judge that matters
         to someone applying the patch; the spec decoder is the one that is
         always here.
     And it watches the judge fail once: a patch with one byte changed must
     NOT decode to the target, or the comparison is not reading anything.

  2. The export routes, against a fake Studio pointing at small temp files:
     a patch lands with its readme, an ISO copy is byte-identical, an
     existing name is refused rather than overwritten, the Studio's OWN disc
     paths are used rather than cfg's (the bug the lab run caught), and a
     refused export is not reported as saved.

  3. mod_import files a real .rdrmod.json and refuses anything else.

Exit 0 all passed, 1 a check failed, 2 could not start.
"""

import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

try:
    from rdrstudio import config as cfg, share, vcdiff           # noqa: E402
    from rdrstudio.api import Refusal                              # noqa: E402
    from rdrstudio.journal import FORMAT                           # noqa: E402
except Exception as ex:                                            # noqa: BLE001
    print("could not import rdrstudio: %s" % ex)
    print("Nothing has been run.")
    sys.exit(2)

FAILS = []


def check(ok, what):
    print("  %s %s" % ("ok  " if ok else "FAIL", what))
    if not ok:
        FAILS.append(what)


# ------------------------------------------------------- RFC 3284 decoder

def _code_table():
    """RFC 3284 section 5.6, built the way the RFC builds it."""
    NOOP, ADD, RUN, COPY = 0, 1, 2, 3
    t = [(RUN, 0, 0, NOOP, 0, 0), (ADD, 0, 0, NOOP, 0, 0)]
    t += [(ADD, s, 0, NOOP, 0, 0) for s in range(1, 18)]
    for m in range(9):
        t.append((COPY, 0, m, NOOP, 0, 0))
        t += [(COPY, s, m, NOOP, 0, 0) for s in range(4, 19)]
    for m in range(6):
        for a in range(1, 5):
            for c in range(4, 7):
                t.append((ADD, a, 0, COPY, c, m))
    for m in range(6, 9):
        for a in range(1, 5):
            t.append((ADD, a, 0, COPY, 4, m))
    for m in range(9):
        t.append((COPY, 4, m, ADD, 1, 0))
    assert len(t) == 256, len(t)
    return t


TABLE = _code_table()


class _R:
    def __init__(self, b):
        self.b = b
        self.p = 0

    def byte(self):
        v = self.b[self.p]
        self.p += 1
        return v

    def varint(self):
        n = 0
        while True:
            c = self.byte()
            n = (n << 7) | (c & 0x7F)
            if not c & 0x80:
                return n

    def take(self, k):
        v = self.b[self.p:self.p + k]
        if len(v) != k:
            raise ValueError("truncated")
        self.p += k
        return v


def spec_decode(patch, source):
    r = _R(patch)
    if r.take(4) != b"\xd6\xc3\xc4\x00":
        raise ValueError("bad magic")
    hdr = r.byte()
    if hdr & 0x01:
        raise ValueError("secondary compression not supported by this judge")
    if hdr & 0x02:
        raise ValueError("custom code table not supported by this judge")
    if hdr & 0x04:
        r.take(r.varint())
    out = bytearray()
    while r.p < len(patch):
        win = r.byte()
        seg = b""
        if win & 0x03:
            slen = r.varint()
            spos = r.varint()
            seg = (source if win & 0x01 else bytes(out))[spos:spos + slen]
            if len(seg) != slen:
                raise ValueError("source segment past the end")
        r.varint()                                   # length of the encoding
        tlen = r.varint()
        if r.byte() != 0:
            raise ValueError("compressed sections not supported by this judge")
        dlen, ilen, alen = r.varint(), r.varint(), r.varint()
        if win & 0x04:
            r.take(4)                                # xdelta3's adler32
        data, inst, addr = _R(r.take(dlen)), _R(r.take(ilen)), _R(r.take(alen))
        near = [0] * 4
        nslot = 0
        same = [0] * (3 * 256)
        tgt = bytearray()
        while inst.p < ilen:
            code = inst.byte()
            e = TABLE[code]
            for kind, size, mode in ((e[0], e[1], e[2]), (e[3], e[4], e[5])):
                if kind == 0:
                    continue
                if size == 0:
                    size = inst.varint()
                if kind == 1:
                    tgt += data.take(size)
                elif kind == 2:
                    tgt += bytes([data.byte()]) * size
                else:
                    here = len(seg) + len(tgt)
                    if mode == 0:
                        a = addr.varint()
                    elif mode == 1:
                        a = here - addr.varint()
                    elif mode < 6:
                        a = near[mode - 2] + addr.varint()
                    else:
                        a = same[(mode - 6) * 256 + addr.byte()]
                    near[nslot] = a
                    nslot = (nslot + 1) % 4
                    same[a % (3 * 256)] = a
                    for i in range(size):
                        k = a + i
                        tgt.append(seg[k] if k < len(seg) else tgt[k - len(seg)])
        if len(tgt) != tlen:
            raise ValueError("window decoded to %d bytes, header says %d"
                             % (len(tgt), tlen))
        out += tgt
    return bytes(out)


# ------------------------------------------------------------ xdelta3 judge

def _wsl_path(p):
    p = os.path.abspath(p)
    if len(p) > 2 and p[1] == ":":
        return "/mnt/%s%s" % (p[0].lower(), p[2:].replace("\\", "/"))
    return p


def xdelta3_cmd():
    """How to run stock xdelta3 here, or None. Never a guess that passes."""
    exe = shutil.which("xdelta3")
    if exe:
        return [exe], (lambda p: p)
    if sys.platform == "win32" and shutil.which("wsl"):
        try:
            r = subprocess.run(["wsl", "-e", "xdelta3", "-V"],
                               capture_output=True, timeout=30)
            if r.returncode == 0 or b"Xdelta" in (r.stdout + r.stderr):
                return ["wsl", "-e", "xdelta3"], _wsl_path
        except Exception:                                      # noqa: BLE001
            pass
    return None


def xdelta3_decode(cmd, conv, src, patch, out):
    if os.path.exists(out):
        os.remove(out)
    r = subprocess.run(cmd[0] + ["-d", "-f", "-s", conv(src), conv(patch),
                                 conv(out)], capture_output=True, timeout=300)
    if r.returncode != 0:
        return None, (r.stderr or r.stdout).decode("latin-1").strip()[:200]
    with open(out, "rb") as f:
        return f.read(), ""


# ------------------------------------------------------------------ part 1

def part1(tmp):
    print("vcdiff.write_patch -> two independent decoders")
    rnd = random.Random(20260929)
    W = vcdiff.WINDOW
    base = bytes(rnd.getrandbits(8) for _ in range(3 * W + 4321))
    edited = bytearray(base)
    for off in (7, 5000, W - 3, 2 * W + 1, 3 * W + 100):
        edited[off:off + 9] = b"MODDED!!!"
    cases = [
        ("untouched", base, base),
        ("scattered edits, one across a window edge", base, bytes(edited)),
        ("grown past the source", base, bytes(edited) + bytes(W + 17)),
        ("shrunk", base, base[:W + 99]),
        ("empty source", b"", base[:3000]),
    ]
    judge = xdelta3_cmd()
    print("  judges: RFC 3284 spec decoder%s" % (
        " + stock xdelta3 (%s)" % " ".join(judge[0]) if judge
        else "; stock xdelta3 NOT available here, so it did not judge"))
    src_p = os.path.join(tmp, "s.bin")
    tgt_p = os.path.join(tmp, "t.bin")
    pat_p = os.path.join(tmp, "p.xdelta")
    out_p = os.path.join(tmp, "o.bin")
    for name, src, tgt in cases:
        with open(src_p, "wb") as f:
            f.write(src)
        with open(tgt_p, "wb") as f:
            f.write(tgt)
        with open(pat_p, "wb") as f:
            info = vcdiff.write_patch(src_p, tgt_p, f)
        with open(pat_p, "rb") as f:
            patch = f.read()
        try:
            got = spec_decode(patch, src)
        except Exception as ex:                                # noqa: BLE001
            got = None
            print("    spec decoder: %s" % ex)
        check(got == tgt, "%s: spec decoder reproduces the target (%d B patch)"
              % (name, len(patch)))
        if judge:
            got, err = xdelta3_decode(judge, judge[1], src_p, pat_p, out_p)
            check(got == tgt, "%s: xdelta3 reproduces the target%s"
                  % (name, " (%s)" % err if err else ""))
        check(info["target_size"] == len(tgt)
              and info["source_size"] == len(src), "%s: sizes reported" % name)
    # The patch for an untouched image carries no literal data at all.
    with open(src_p, "wb") as f:
        f.write(base)
    with open(tgt_p, "wb") as f:
        f.write(base)
    with open(pat_p, "wb") as f:
        info = vcdiff.write_patch(src_p, tgt_p, f)
    check(info["changed_bytes"] == 0
          and info["source_sha256"] == info["target_sha256"],
          "untouched image: no literal bytes, equal hashes")

    # Watch the judge fail once. Flip a byte inside the literal data of a
    # real edit: a decoder that reads the patch must now disagree.
    with open(tgt_p, "wb") as f:
        f.write(bytes(edited))
    with open(pat_p, "wb") as f:
        vcdiff.write_patch(src_p, tgt_p, f)
    with open(pat_p, "rb") as f:
        bad = bytearray(f.read())
    k = bytes(bad).find(b"MODDED!!!")
    check(k > 0, "the edit's bytes are in the patch as literal data")
    bad[k] ^= 0xFF
    try:
        got = spec_decode(bytes(bad), base)
    except Exception:                                          # noqa: BLE001
        got = None
    check(got != bytes(edited), "a one-byte-corrupt patch does NOT decode to "
          "the target (the judge is reading)")


# ------------------------------------------------------------------ part 2

class FakeStudio:
    def __init__(self, stock, work):
        self.vanilla_path = stock
        self.modded_path = work
        self.lock = threading.RLock()
        self.notes = []

    def note(self, msg):
        self.notes.append(msg)


def part2(tmp):
    print("export routes against a fake Studio")
    rnd = random.Random(7)
    stock = os.path.join(tmp, "stock.iso")
    work = os.path.join(tmp, "work.iso")
    data = bytes(rnd.getrandbits(8) for _ in range(2 * vcdiff.WINDOW + 999))
    with open(stock, "wb") as f:
        f.write(data)
    mod = bytearray(data)
    mod[123456:123466] = b"0123456789"
    with open(work, "wb") as f:
        f.write(mod)
    s = FakeStudio(stock, work)
    # cfg deliberately points at discs that do not exist: the routes must use
    # the Studio's own paths, which is what --vanilla/--modded set.
    cfg.VANILLA_ISO = os.path.join(tmp, "nope", "RDR_VANILLA.iso")
    cfg.MODDED_ISO = os.path.join(tmp, "nope", "RDR_MODDED.iso")
    cfg.EXPORTS = os.path.join(tmp, "exports")
    cfg.MODS = os.path.join(tmp, "mods")

    r = share.get_share_info(None, s, {})
    check(r["ok"] and r["stock"]["size"] == len(data)
          and r["working"]["path"] == work,
          "share_info reports the Studio's own discs, not cfg's")

    r = share.post_export_patch(None, s, dict(name="my mod"))
    check(r["ok"] and not r.get("applied")
          and not os.path.exists(cfg.EXPORTS),
          "no `dry` key is a dry run: nothing written")
    r = share.post_export_patch(None, s, dict(name="my mod", dry=False))
    path = r.get("path", "")
    check(r["ok"] and r.get("applied") and path.endswith("my_mod.xdelta")
          and os.path.isfile(path), "patch written as my_mod.xdelta")
    with open(path, "rb") as f:
        check(spec_decode(f.read(), data) == bytes(mod),
              "the written patch decodes to the working disc")
    check(os.path.isfile(r.get("readme", "")), "readme written beside it")
    with open(r["readme"]) as f:
        txt = f.read()
    check(r["source_sha256"] in txt and r["target_sha256"] in txt,
          "readme names the disc it expects and the one it makes")
    try:
        share.post_export_patch(None, s, dict(name="my mod", dry=False))
        check(False, "an existing patch name is refused")
    except Refusal:
        check(True, "an existing patch name is refused, not overwritten")

    r = share.post_export_iso(None, s, dict(name="copy", dry=False))
    ok = r["ok"] and os.path.isfile(r["path"])
    if ok:
        with open(r["path"], "rb") as f:
            ok = f.read() == bytes(mod)
    check(ok, "ISO export is byte-identical to the working disc")
    # Only the Studio's own discs are refused as a destination, so aim the
    # exports folder at them for this one check.
    saved = cfg.EXPORTS
    for bad in (work, stock):
        cfg.EXPORTS = os.path.dirname(bad)
        try:
            share.post_export_iso(None, s, dict(
                name=os.path.basename(bad), dry=False, replace=True))
            check(False, "exporting over %s is refused" % os.path.basename(bad))
        except Refusal:
            check(True, "exporting over the Studio's own %s is refused"
                  % os.path.basename(bad))
    cfg.EXPORTS = saved
    check(not [n for n in os.listdir(cfg.EXPORTS) if n.endswith(".part")],
          "no .part files left behind")

    gone = FakeStudio(os.path.join(tmp, "missing.iso"), work)
    try:
        share.post_export_patch(None, gone, dict(dry=False))
        check(False, "a missing stock disc is refused")
    except Refusal as ex:
        check(ex.code == "not_found", "a missing stock disc is refused "
              "(not_found)")


# ------------------------------------------------------------------ part 3

def part3(tmp):
    print("mod_import")
    s = FakeStudio(None, None)
    doc = dict(format=FORMAT, name="Friend's mod", notes="", files=[
        dict(archive="SWAT.DAT", file="x.txt", data_b64="aGk=")])
    r = share.post_mod_import(None, s, dict(doc=json.dumps(doc),
                                            filename="f.rdrmod.json"))
    check(r["ok"] and os.path.isfile(r["path"]) and r["files"] == 1,
          "a .rdrmod.json is filed under the mods folder")
    with open(r["path"]) as f:
        check(json.load(f) == doc, "filed byte-for-byte as sent (as JSON)")
    r2 = share.post_mod_import(None, s, dict(doc=doc))
    check(r2["path"] != r["path"], "a second import of the same name does "
          "not overwrite the first")
    for bad in ("not json", json.dumps(dict(format="other", files=[])),
                json.dumps(dict(format=FORMAT))):
        try:
            share.post_mod_import(None, s, dict(doc=bad))
            check(False, "refuses %r" % bad[:30])
        except Refusal:
            check(True, "refuses %r" % bad[:30])


def main():
    tmp = tempfile.mkdtemp(prefix="rdr_share_")
    try:
        part1(tmp)
        part2(tmp)
        part3(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if FAILS:
        print("FAILED %d check(s):" % len(FAILS))
        for f in FAILS:
            print("  " + f)
        sys.exit(1)
    print("all export checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
