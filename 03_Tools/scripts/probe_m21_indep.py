#!/usr/bin/env python3
"""Independent cross-check of the [M21] claim. A PROBE, not a suite row.

    python3 scripts/probe_m21_indep.py <iso>

rdr-fmt's `verify_iso_add.py` is the acceptance test and it is a good one --
its section 3 exists solely to catch an omitted `dry` flag. But every check in
it reads the disc back through `iso9660.py`, the module under test, so a bug
common to writer and reader would pass. This re-checks the same run with a
judge that shares no code with the subject: an ISO9660 parser written from the
PVD and the directory records, a Dave reader written from the TOC layout, and
a whole-image sha256 instead of a snapshot comparison.

**It WRITES whatever ISO it is handed**, and restores it from its own snapshot
in a `finally`. Hand it a copy, not the working disc -- rdr-3d ran it against a
copy of `01_Vanilla` precisely because the working disc was held. Deliberately
NOT registered in run_tests.py: it takes a path, it is a measurement rather
than a criterion, and a probe that writes a disc must be run on purpose.

Result, 2026-09-18 (rdr-3d): 21/21, and their test reproduced at 43/43.
"""
import hashlib, os, subprocess, sys, tempfile, time

_HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(_HERE)
ROOT = os.path.dirname(TOOLS)

# The path is REQUIRED and there is no default. This writes what it is given,
# and a writing probe whose default is the shared working disc is one absent
# argument away from dirtying it for whichever lane runs next.
SHARED = os.path.join(ROOT, "02_Modded/RDR_MODDED.iso")
VAN = os.path.join(ROOT, "01_Vanilla/RDR_VANILLA.iso")
SECTOR_ = 2048


def build_test_image(src, dst):
    """A structurally complete stand-in for the disc, built in about a second.

    rdr-fmt's shape from `verify_iso_grow.py` (M22), reused rather than
    reinvented. Everything the independent judge reads lives in the first
    ~1,025 sectors -- the volume descriptors, the directory extents this walks
    to build its own file list, and the UDF structures around lba 32..268 --
    plus the backup anchor on the final sector. The data area can be a hole
    because the only file bytes read back are the ones this probe WRITES
    itself, at lba 66, into space that is a hole to begin with and is asserted
    to read as zero before the write.

    Same length, same descriptors, same UDF, same anchor: byte-identical
    everywhere this looks, for a sparse allocation instead of a 2.3 GB copy.
    """
    total = os.path.getsize(src) // SECTOR_
    head = min(1025, total - 1)
    with open(src, "rb") as sf, open(dst, "wb") as df:
        df.truncate(total * SECTOR_)                   # sparse: no data written
        df.seek(0)
        df.write(sf.read(head * SECTOR_))
        sf.seek((total - 1) * SECTOR_)
        df.seek((total - 1) * SECTOR_)
        df.write(sf.read(SECTOR_))                     # the backup anchor
    return total


# The path is REQUIRED and there is no default. This writes what it is given,
# and a writing probe whose default is the shared working disc is one absent
# argument away from dirtying it for whichever lane runs next.
SELF_CONTAINED = len(sys.argv) > 1 and sys.argv[1] == "--self-contained"
if len(sys.argv) < 2:
    sys.exit("usage: probe_m21_indep.py <iso>              (give it a COPY)\n"
             "       probe_m21_indep.py --self-contained   (build its own image)")
if SELF_CONTAINED:
    import atexit, shutil, tempfile
    _tmp = tempfile.mkdtemp(prefix="m21indep_")
    atexit.register(shutil.rmtree, _tmp, True)
    ISO = os.path.join(_tmp, "m21_indep.iso")
    _n = build_test_image(VAN, ISO)
    print("built a sparse stand-in: %d sectors, %.2f GB nominal, descriptors +"
          " directories + UDF + anchor real, data area a hole"
          % (_n, _n * SECTOR_ / 2**30))
else:
    ISO = sys.argv[1]
    for _what, _path in (("the shared working disc", SHARED),
                         ("the pristine disc", VAN)):
        if os.path.abspath(ISO) == os.path.abspath(_path):
            sys.exit("REFUSING: %s is %s. This WRITES what it is handed; give "
                     "it a copy." % (ISO, _what))
DIR, NAME = "LVLS_SNG", "M21TEST.DAT"


import struct, hashlib

SECTOR = 2048

def _both16(b): return struct.unpack("<H", b[:2])[0]
def _both32(b): return struct.unpack("<I", b[:4])[0]

def pvd(fh):
    """Primary Volume Descriptor, found by scanning from sector 16."""
    for lba in range(16, 64):
        fh.seek(lba * SECTOR)
        s = fh.read(SECTOR)
        if s[1:6] != b"CD001":
            continue
        if s[0] == 1:                       # 1 = Primary
            return {
                "lba": lba,
                "volume_id": s[40:72].decode("latin-1").strip(),
                "total_sectors": _both32(s[80:88]),
                "logical_block_size": _both16(s[128:132]),
                "root_record": s[156:190],
            }
        if s[0] == 255:                     # Terminator
            break
    raise RuntimeError("no PVD")

def _records(fh, lba, length):
    """Yield raw directory records in the extent at `lba`."""
    data = b""
    n = (length + SECTOR - 1) // SECTOR
    fh.seek(lba * SECTOR)
    data = fh.read(n * SECTOR)
    off = 0
    while off < length:
        rlen = data[off]
        if rlen == 0:
            off = (off // SECTOR + 1) * SECTOR   # next sector
            continue
        yield data[off:off + rlen]
        off += rlen

def walk(fh, root_record, prefix=""):
    """Every file on the volume as (path, lba, size)."""
    lba = _both32(root_record[2:10])
    length = _both32(root_record[10:18])
    out = []
    for rec in _records(fh, lba, length):
        name_len = rec[32]
        name = rec[33:33 + name_len]
        flags = rec[25]
        if name in (b"\x00", b"\x01"):
            continue
        nm = name.decode("latin-1").split(";")[0]
        rlba = _both32(rec[2:10])
        rsize = _both32(rec[10:18])
        path = prefix + "/" + nm
        if flags & 0x02:
            out.extend(walk(fh, rec, path))
        else:
            out.append((path, rlba, rsize))
    return out

def read_raw(fh, lba, nbytes):
    fh.seek(lba * SECTOR)
    return fh.read(nbytes)

def sha(b): return hashlib.sha256(b).hexdigest()[:16]


results = []
def ck(label, got, want):
    ok = got == want
    results.append((ok, label))
    print("   %-58s %s" % (label, "ok" if ok else "FAIL got=%r want=%r" % (got, want)))
    return ok

def full_sha(path):
    t0 = time.time()
    hsh = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(16 << 20)
            if not b: break
            hsh.update(b)
    return hsh.hexdigest(), time.time() - t0

def snapshot(path):
    with open(path, "rb") as fh:
        p = pvd(fh)
        files = walk(fh, p["root_record"])
    return {"pvd": p, "files": set(files), "n": len(files),
            "size": os.path.getsize(path)}

# Same RDR_PORT override as the M6/M16 drivers, so this guard watches whichever
# port a run is actually using rather than always 8731.
PORT = int(os.environ.get("RDR_PORT") or 8731)


def guard():
    """Never write a disc another lane is holding."""
    import urllib.request
    try:
        urllib.request.urlopen("http://127.0.0.1:%d/api/config" % PORT,
                               timeout=4).read()
    except Exception:
        return True
    print("STUDIO-UP: a listener answers on %d. It may be another lane's, so"
          % PORT)
    print("it is NOT being killed. Nothing has been written.")
    return False

print("=" * 68); print("M21 INDEPENDENT CROSS-CHECK"); print("=" * 68)
print("target: %s" % ISO)
if os.path.abspath(ISO) == os.path.abspath(SHARED) and not guard():
    sys.exit(3)

# ---------------------------------------------------------------- pre-state
print("\nA. pre-state, measured with my own parser")
pre = snapshot(ISO)
van = snapshot(VAN)
pre_sha, dt = full_sha(ISO)
print("   %d files, %d bytes, sha %s... (%.0fs)" % (pre["n"], pre["size"], pre_sha[:16], dt))
ck("starts clean: no %s on the disc" % NAME,
   any(p.endswith(NAME) for p, _, _ in pre["files"]), False)
ck("image is not grown (same size as vanilla)", pre["size"], van["size"])
ck("total_sectors matches vanilla", pre["pvd"]["total_sectors"],
   van["pvd"]["total_sectors"])

# ------------------------------------------------- reproduce their test run
print("\nB. their acceptance test, run verbatim")
if SELF_CONTAINED:
    # SKIPPED, and not silently. `verify_iso_add.py`'s section 6 opens all 66
    # shipped archives off the disc, which is exactly the file DATA a sparse
    # stand-in does not have -- it would fail on holes and the failure would be
    # about the stand-in, not the code. Nothing is lost on the gate: the suite
    # already runs that test as the registered [M21] row, against the real
    # disc. What this probe uniquely contributes is the INDEPENDENT judge in
    # A, C, D and E -- a parser and a Dave reader sharing no code with
    # iso9660.py -- and none of that needs archive data.
    print("   SKIPPED under --self-contained: it opens all 66 archives, which")
    print("   a sparse stand-in does not carry. The suite runs it as [M21].")
    post_sha, dt = pre_sha, 0.0
else:
    t0 = time.time()
    p = subprocess.run([sys.executable, "scripts/verify_iso_add.py", "--iso", ISO],
                       cwd=TOOLS,
                       capture_output=True, text=True, timeout=1800)
    out = p.stdout + p.stderr
    print("   exit=%d in %.0fs" % (p.returncode, time.time() - t0))
    tail = [l for l in out.splitlines() if l.strip()][-6:]
    for l in tail: print("   | " + l)
    n_ok = out.count(" ok")
    n_fail = out.count("FAIL")
    print("   counted: %d ok, %d FAIL" % (n_ok, n_fail))
    ck("their test exits 0", p.returncode, 0)
    ck("their test reports no FAIL", n_fail, 0)
    # NOT into 03_Tools. This is a registered suite row now, so writing a
    # scratch file beside the toolkit drops litter into a shared directory on
    # every full gate -- and an untracked file there is exactly what the
    # `_*_tmp` sweep spent this morning clearing out.
    _tr = os.path.join(tempfile.gettempdir(), "probe_m21_their_run.txt")
    open(_tr, "w").write(out)
    print("   their run's full output: %s" % _tr)

    # -------------------------------------- independent restore verification
    print("\nC. did their run put the disc back? (my hash, not their snapshot)")
    post_sha, dt = full_sha(ISO)
    ck("whole-image sha256 identical to before their run", post_sha, pre_sha)
    post = snapshot(ISO)
    ck("file count back to %d" % pre["n"], post["n"], pre["n"])
    ck("no file moved or resized", post["files"], pre["files"])

# ------------------------------------------------- my own add, verified raw
print("\nD. my own add -- is the file PHYSICALLY on the disc?")
sys.path.insert(0, TOOLS)
from rdrstudio.dave import create                             # noqa: E402
from rdrstudio.iso9660 import Iso9660                         # noqa: E402

PAYLOAD = {
    "indep.lvl": b"independent M21 cross-check payload\n" * 11,
    "indep.spwn": bytes(range(256)) * 5,
}
blob = create(PAYLOAD)
blob_sha = hashlib.sha256(blob).hexdigest()
print("   built %d bytes, sha %s..." % (len(blob), blob_sha[:16]))

need = (len(blob) + SECTOR - 1) // SECTOR
regions = {}
iso = Iso9660(ISO, writable=True)
plan = iso.plan_add(DIR, NAME, len(blob))
lba = plan["data_lba"]
print("   plan: lba %d, %d sectors" % (lba, need))
with open(ISO, "rb") as fh:                      # snapshot BEFORE any write
    fh.seek(0); regions[0] = fh.read(400 * SECTOR)
    fh.seek(lba * SECTOR); regions[lba] = fh.read(need * SECTOR)
    rl = pre["pvd"]["root_record"]
    import struct
    rlba = struct.unpack("<I", rl[2:6])[0]
    fh.seek(rlba * SECTOR); regions[rlba] = fh.read(64 * SECTOR)

ok_all = False
try:
    ck("target sectors were zero before the write",
       set(regions[lba]), {0})
    r = iso.add(DIR, NAME, blob, dry=False)
    iso.close()
    ck("add() reports written", r.get("written"), True)
    landed = r["lba"]
    print("   landed at lba %d" % landed)

    with open(ISO, "rb") as fh:
        p2 = pvd(fh)
        files2 = walk(fh, p2["root_record"])
        fh.seek(landed * SECTOR)
        raw = fh.read(len(blob))
    got = [(pp, ll, ss) for pp, ll, ss in files2 if pp.endswith(NAME)]
    ck("my parser sees exactly one new file", len(got), 1)
    ck("it is at the LBA add() reported", got[0][1] if got else None, landed)
    ck("its recorded size is the blob size", got[0][2] if got else None, len(blob))
    ck("raw bytes at that LBA start with the Dave magic", raw[:4], b"Dave")
    ck("raw bytes at that LBA are the blob, byte-exact",
       hashlib.sha256(raw).hexdigest(), blob_sha)
    ck("file count grew by exactly one", len(files2), pre["n"] + 1)
    ck("every pre-existing file unmoved and unresized",
       pre["files"] - set(files2), set())
    ck("image did not grow", os.path.getsize(ISO), pre["size"])
    ck("total_sectors unchanged", p2["total_sectors"], pre["pvd"]["total_sectors"])
    occupied = set()
    for pp, ll, ss in files2:
        if pp.endswith(NAME): continue
        occupied.update(range(ll, ll + (ss + SECTOR - 1) // SECTOR))
    ck("what was WRITTEN overlaps no other file",
       occupied & set(range(landed, landed + need)), set())
    ok_all = True
finally:
    # E used to report a clean 21/21 while testing remove_last NOT AT ALL.
    # `iso2.remove_last()` was called with no arguments against
    # `remove_last(self, dir_path, name, dry=True)`, so it raised TypeError on
    # every run ever made; the except merely PRINTED it, so it never entered
    # `results` and never counted; and the snapshot fallback below then put
    # the disc back regardless, so the sha check passed anyway. The line was
    # unfalsifiable: the restore that proves the disc is clean is the same
    # restore that hides remove_last doing nothing.
    #
    # Two faults, and the second is the one that would have survived fixing
    # the first: `dry` DEFAULTS TO TRUE, so `remove_last(DIR, NAME)` plans and
    # writes nothing, and the fallback masks that identically. It is called
    # with dry=False, its result is asserted through ck(), and the sha is
    # compared BEFORE the fallback runs -- so a no-op remove is now
    # distinguishable from a correct one instead of both reading green.
    #
    # Fixed by rdr-fmt on the reviewer's instruction, not unilaterally; this is
    # rdr-3d's file. See REQ-076's addendum for the class.
    print("\nE. restore")
    removed = None
    try:
        iso2 = Iso9660(ISO, writable=True)
        removed = iso2.remove_last(DIR, NAME, dry=False)
        iso2.close()
        print("   remove_last(%r, %r, dry=False) -> %s" % (DIR, NAME, removed))
    except Exception as ex:
        print("   remove_last() raised: %s: %s" % (type(ex).__name__, ex))
    ck("remove_last() ran without raising", removed is not None, True)
    ck("remove_last() reports written", (removed or {}).get("written"), True)

    # THE LOAD-BEARING ORDER. Measure before the safety net, or the safety net
    # is what is being measured.
    post_sha, _ = full_sha(ISO)
    ck("remove_last() alone restored the image byte-exactly", post_sha, pre_sha)

    # The net stays, because a probe that writes a disc must be able to put it
    # back even when the thing it is testing is broken -- but needing it is
    # now a recorded failure above, not a silent repair.
    final_sha = post_sha
    if final_sha != pre_sha:
        print("   sha differs after remove_last(); writing my snapshot back")
        with open(ISO, "r+b") as fh:
            for off, data in regions.items():
                fh.seek(off * SECTOR); fh.write(data)
            fh.flush(); os.fsync(fh.fileno())
        final_sha, _ = full_sha(ISO)
    ck("disc is byte-identical to where I found it", final_sha, pre_sha)

print("\n" + "=" * 68)
bad = [l for ok, l in results if not ok]
print("%d checks, %d failed" % (len(results), len(bad)))
for l in bad: print("   FAILED: %s" % l)
sys.exit(0 if not bad else 1)
