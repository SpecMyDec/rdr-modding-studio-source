#!/usr/bin/env python3
"""M21 second half -- the image GROWS, and a real-sized level archive lands on it.

    python3 scripts/verify_iso_grow.py <iso>     (give it a COPY)

A PROBE, not a suite row, and deliberately not registered in run_tests.py.
`shrink_to()` TRUNCATES a 2.3 GB image, and a suite row that grows and then
truncates the shared disc on every full run is the hazard M21's own scope
warned about: it restores byte-exactly -- proven below -- but a run killed
mid-flight leaves the disc grown with a 6.5 MB file on it, and this suite has
been killed mid-flight more than once. So the path is required, there is no
default, and the shared and pristine discs are refused outright.

M21's Accept line was met by the FIRST half and M21 is already ticked; this is
beyond that line and earns no tick of its own.

**What it does not prove.** The game has never booted an image grown this way.
Whether the engine reads a file whose LBA is past the ORIGINAL volume size is
the question this whole half turns on, and no structural check answers it --
Sony's IOP filesystem server resolves the LBA, not the game. Every check here
is structure.
"""
import hashlib, os, struct, sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _HERE]
sys.path.insert(0, os.path.dirname(_HERE))

from rdrstudio.iso9660 import Iso9660, SECTOR      # noqa: E402
from rdrstudio.dave import create                  # noqa: E402
from rdrstudio import config                       # noqa: E402

def build_test_image(src, dst):
    """A structurally complete stand-in for the disc, built in about a second.

    Registering this row was the problem: it truncates a 2.3 GB image, so
    pointing it at the shared disc is unsafe and copying the shared disc costs
    ~105 s of every full suite run. Neither is acceptable for a row that runs
    on every gate.

    So build a SPARSE image instead. Everything this test reads lives in the
    first ~1,025 sectors -- the volume descriptors, the directory extents, and
    the UDF structures `_udf_sectors()` scans -- plus the backup anchor on the
    final sector. File DATA is never read: the checks compare (path, lba, size)
    metadata, the blob this test writes itself, and whole-image hashes. So the
    data area can be a hole.

    The result is byte-identical to the real disc everywhere this test looks,
    is the same length, and costs a sparse allocation instead of a copy.
    """
    total = os.path.getsize(src) // SECTOR
    head = min(1025, total - 1)
    with open(src, "rb") as sf, open(dst, "wb") as df:
        df.truncate(total * SECTOR)                      # sparse: no data written
        df.seek(0)
        df.write(sf.read(head * SECTOR))
        sf.seek((total - 1) * SECTOR)
        df.seek((total - 1) * SECTOR)
        df.write(sf.read(SECTOR))                        # the backup anchor
    return total


if len(sys.argv) < 2:
    sys.exit("usage: verify_iso_grow.py <iso>            (give it a COPY)\n"
             "       verify_iso_grow.py --self-contained (build its own image)")

if sys.argv[1] == "--self-contained":
    import tempfile
    _tmp = tempfile.mkdtemp(prefix="m22_")
    ISO = os.path.join(_tmp, "grow_test.iso")
    _n = build_test_image(config.VANILLA_ISO, ISO)
    print("built a sparse stand-in for the disc: %d sectors, %.2f GB nominal, "
          "descriptors + directories + UDF + anchor real, data area a hole\n"
          % (_n, _n * SECTOR / 2**30))
    import atexit, shutil
    atexit.register(shutil.rmtree, _tmp, True)
else:
    ISO = sys.argv[1]
for name, path in (("the shared working disc", config.MODDED_ISO),
                   ("the pristine disc", config.VANILLA_ISO)) if ISO else ():
    if os.path.abspath(ISO) == os.path.abspath(path):
        sys.exit("REFUSING: %s is %s. This grows and TRUNCATES what it is "
                 "handed; give it a copy." % (ISO, name))
DIR, NAME = "LVLS_SNG", "M21BIG.DAT"
checks = []


def ck(label, got, want):
    ok = got == want
    checks.append((ok, label))
    print("   %-64s %s" % (label, "ok" if ok else "FAIL got=%r want=%r" % (got, want)))
    return ok


def h(b):
    return hashlib.sha256(b).hexdigest()[:16]


def full_sha(path):
    d = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            d.update(chunk)
    return d.hexdigest()


print("1. a REAL-SIZED archive, built from nothing")
payload = {
    # Sized against the MEASURED distribution, not a round number: 35
    # LVLS_SNG archives run 2.48 MB min / 5.37 median / 9.39 max. Incompressible
    # bytes on purpose -- create() deflates, and a payload of zeroes would
    # shrink to nothing and prove the mechanism at a size no level uses.
    "lvl_big.lvl":  os.urandom(4 * 1024 * 1024),
    "lvl_big.rsc":  os.urandom(int(2.5 * 1024 * 1024)),
    "lvl_big.spwn": bytes(range(256)) * 512,
    "readme.txt":   b"M21 second half -- grow the image.\n",
}
blob = create(payload)
print("   built %.2f MB for %d entries, sha %s"
      % (len(blob) / 1048576, len(payload), h(blob)))
ck("payload is above the 5.37 MB median level", len(blob) > 5.37 * 1048576, True)

iso = Iso9660(ISO)
pre_sha = full_sha(ISO)
pre_size = os.path.getsize(ISO)
pre_total = iso.total_sectors
pre_files = {(f.path, f.lba, f.size) for f in iso.files()}
pre_anchor = iso.read_at((pre_total - 1) * SECTOR, SECTOR)
print("\n2. before: %d sectors, %d bytes, %d files, anchor at %d"
      % (pre_total, pre_size, len(pre_files), pre_total - 1))

print("\n3. it does NOT fit without growing -- the whole reason for this half")
p_nogrow = iso.plan_add(DIR, NAME, len(blob))
ck("plan without grow refuses", p_nogrow["ok"], False)
print("   largest free run is %s sectors (%.0f KB); the archive needs %d"
      % (p_nogrow["largest_free_run"][1],
         p_nogrow["largest_free_run"][1] * SECTOR / 1024,
         (len(blob) + SECTOR - 1) // SECTOR))

p = iso.plan_add(DIR, NAME, len(blob), grow=True)
ck("plan with grow=True succeeds", p["ok"], True)
ck("it plans to grow", p["grows"], True)
need = (len(blob) + SECTOR - 1) // SECTOR
ck("grows by the archive plus one anchor sector", p["grow_by"], need + 1)
ck("data lands at the old end of the image", p["data_lba"], pre_total)
iso.close()

print("\n4. dry by default -- an omitted flag must not write")
iso = Iso9660(ISO, writable=True)
r = iso.add(DIR, NAME, blob, grow=True)
ck("reports dry", r["dry"], True)
ck("reports not written", r["written"], False)
iso.close()
ck("the image is untouched by the dry run", full_sha(ISO), pre_sha)

print("\n5. grow + add")
iso = Iso9660(ISO, writable=True)
r = iso.add(DIR, NAME, blob, dry=False, grow=True)
ck("reports written", r.get("written"), True)
print("   landed at lba %s, image now %d sectors" % (r.get("lba"), iso.total_sectors))
iso.close()

print("\n6. the grown disc still verifies")
iso = Iso9660(ISO)
ck("total_sectors grew by exactly the plan", iso.total_sectors, pre_total + need + 1)
ck("file size matches total_sectors", os.path.getsize(ISO), iso.total_sectors * SECTOR)
# Compared against the INDEPENDENTLY computed expectation, not against
# iso.total_sectors -- which is parsed FROM the PVD, so checking one against
# the other is a tautology that passes even when grow() never wrote the field.
# The negative control proved that: with the PVD update disabled, both of
# these still said "ok" and only the file-size check caught it.
expect_total = pre_total + need + 1
pvd = iso.read_at(16 * SECTOR, SECTOR)
ck("PVD volume_space_size LE is the new total",
   struct.unpack_from("<I", pvd, 80)[0], expect_total)
ck("PVD volume_space_size BE is the new total",
   struct.unpack_from(">I", pvd, 84)[0], expect_total)

now = {(f.path, f.lba, f.size) for f in iso.files()}
ck("every pre-existing file unmoved and unresized", pre_files - now, set())
ck("exactly one file is new", len(now - pre_files), 1)

a = iso.read_at((iso.total_sectors - 1) * SECTOR, SECTOR)
ck("backup anchor is at the NEW final sector", struct.unpack_from("<H", a, 0)[0], 2)
ck("its TagLocation names that sector",
   struct.unpack_from("<I", a, 12)[0], iso.total_sectors - 1)
ck("its TagChecksum is valid",
   a[4], sum(a[i] for i in range(16) if i != 4) & 0xFF)
ck("its body is byte-identical to the old anchor's", a[16:], pre_anchor[16:])
old_a = iso.read_at((pre_total - 1) * SECTOR, SECTOR)
ck("the OLD anchor sector is zeroed", old_a.count(0), SECTOR)

got = iso.read_file("%s/%s" % (DIR, NAME))[:len(blob)]
ck("the new file reads back byte-exact from BEYOND the old end", h(got), h(blob))
ck("...and it really is beyond the old end", r["lba"] >= pre_total, True)
iso.close()

print("\n7. the app opens it off the grown disc")
from rdrstudio.dave import Dave, IsoBacking            # noqa: E402
iso = Iso9660(ISO)
d = Dave(IsoBacking(iso, "%s/%s" % (DIR, NAME)), label=NAME)
ck("parses as a Dave archive", d.magic, "Dave")
ck("with every entry", len(d), len(payload))
for nm, want in payload.items():
    ck("reads from the grown disc: %s" % nm, d.read(nm), want)
iso.close()

print("\n8. shrink_to refuses while the file is still there")
iso = Iso9660(ISO, writable=True)
bad = iso.shrink_to(pre_total, dry=True)
ck("refuses to cut under a live file", bad["ok"], False)
print("   %s" % bad.get("reason"))

print("\n9. remove_last, then shrink back")
rl = iso.remove_last(DIR, NAME, dry=False)
ck("remove_last reports written", rl.get("written"), True)
sh = iso.shrink_to(pre_total, dry=False)
ck("shrink_to reports written", sh.get("written"), True)
iso.close()

print("\n10. the disc is byte-identical to how it was found")
ck("total_sectors is back", Iso9660(ISO).total_sectors, pre_total)
ck("file size is back", os.path.getsize(ISO), pre_size)
ck("whole-image sha256 is identical", full_sha(ISO), pre_sha)

bad = [l for ok, l in checks if not ok]
print("\n%s -- %d/%d checks" % ("PASS" if not bad else "FAIL",
                                sum(1 for ok, _ in checks if ok), len(checks)))
for l in bad:
    print("   FAILED: %s" % l)
sys.exit(0 if not bad else 1)
