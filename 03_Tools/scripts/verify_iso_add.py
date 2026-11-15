#!/usr/bin/env python3
"""[M21] A brand-new .DAT is built from nothing, placed on the disc, and the
disc still verifies -- then the disc is put back byte-for-byte.

    python scripts/verify_iso_add.py [--iso PATH] [--offline]

This is M21's create path end to end:

    dave.create()     build a Dave archive that never existed      (REQ-005)
    Iso9660.add()     place it on the volume as a NEW file         (REQ-069)
    Iso9660.remove_last()   take it off again                      (REQ-069)

**What "the disc still verifies" is made to mean here.** Not "it opened".
Nine checks, and the ones that matter are the negative ones: that every file
that was on the disc before is at the SAME LBA with the SAME length
afterwards, and that the sectors the new file landed in were claimed by
neither filesystem. An add that corrupts the volume can still produce a
readable new file, so reading the new file back proves the least of it.

**What the phrase does NOT cover, said here rather than left implied.**
Archives are verified structurally -- every .DAT is opened and its TOC
parsed -- not byte-for-byte, because that is 2.3 GB per run. Content is
compared only for the 37 non-archive files, which is where the negative
control landed: forcing the write onto an occupied LBA trampled SYSTEM.CNF
and the head of SLUS_205.00, and at the time only the sector-overlap check
noticed, because every archive still opened and every directory record still
said what it had always said. Both gaps are closed below; the reason they
existed is that "the disc still verifies" is exactly the kind of check wide
enough for something other than what you meant to satisfy it.

**The disc is written, so this row is NOT --quick-safe.** Sections 3-7 write
`02_Modded/RDR_MODDED.iso` and restore it in a `finally`; `--offline` runs
sections 1-2 only, against the pristine disc read-only, and prints PARTIAL
rather than PASS. Same shape as [M6]/[M9]/[M16]/[M17].

**The restore does not trust `remove_last()`.** Every byte region the add
would touch is snapshotted BEFORE the write, `remove_last()` is then exercised
as the real undo, and the regions are compared against the snapshot
afterwards. If they differ the snapshot is written back verbatim and the run
FAILS while leaving a clean disc. `remove_last()` zeroes the data sectors,
which happens to be right only because those sectors were zero to begin with
-- proven in section 2, not assumed -- and a future run on a disc where they
are not must not silently "restore" them to zeros.
"""

import argparse
import hashlib
import os
import sys

# scripts/dis.py shadows the stdlib `dis`, which argparse reaches through
# inspect on 3.13+. Drop our own directory before anything else imports.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _HERE]
sys.path.insert(0, os.path.dirname(_HERE))

from rdrstudio import Game                                    # noqa: E402
from rdrstudio.config import MODDED_ISO, VANILLA_ISO          # noqa: E402
from rdrstudio.dave import Dave, IsoBacking, SECTOR, create   # noqa: E402
from rdrstudio.iso9660 import Iso9660                         # noqa: E402


DIR = "LVLS_SNG"
NAME = "M21TEST.DAT"

# A minimal but REAL level payload set: the names a level archive is opened
# for, so `Game.archive()` walking it exercises the same path a shipped one
# does. Small on purpose -- M21's Accept line asks for a .dat the app lists,
# not a playable level.
PAYLOAD = {
    "lvl_custom.lvl": b"M21 custom level container, built by dave.create()\n" * 8,
    "lvl_custom.spwn": bytes(range(256)) * 3,
    "readme.txt": b"Created by scripts/verify_iso_add.py -- not a shipped file.\n",
}

checks = []


def ck(label, got, want):
    ok = got == want
    checks.append(ok)
    print("   %-62s %s" % (label, "ok" if ok else "FAIL  got=%r want=%r"
                                  % (got, want)))
    return ok


def h(b):
    return hashlib.sha256(b).hexdigest()[:16]


# ------------------------------------------------------------------ sections

def section1_build():
    """The container is built from nothing and reads back byte-exact."""
    print("\n1. dave.create() -- a Dave archive that never existed")
    blob = create(PAYLOAD)
    print("   built %d bytes for %d entries, sha %s"
          % (len(blob), len(PAYLOAD), h(blob)))

    class MemBacking:
        def __init__(self, b):
            self.b, self.size, self.writable = b, len(b), False

        def read_at(self, off, n):
            return self.b[off:off + n]

    d = Dave(MemBacking(blob), label="<memory>")
    ck("magic is Dave", d.magic, "Dave")
    ck("entry count", len(d), len(PAYLOAD))
    for nm, want in PAYLOAD.items():
        ck("round-trips: %s" % nm, d.read(nm), want)

    # The TOC is binary-searched by the engine (dave.create's own docstring,
    # bsearch at 0x0025EEB8). An unsorted TOC loses arbitrary entries, not
    # just one, so assert the order rather than trusting the builder.
    from rdrstudio.dave import CHARS
    order = [[CHARS.find(c) for c in e.name.lower()] for e in d.entries]
    ck("TOC is sorted under the engine's collation", order, sorted(order))
    return blob


def section2_ground(iso_path, blob):
    """Where it would go, and the proof that the space is nobody's."""
    print("\n2. plan_add() on %s -- read-only" % os.path.basename(iso_path))
    iso = Iso9660(iso_path)
    plan = iso.plan_add(DIR, NAME, len(blob))
    ck("plan is ok", plan.get("ok"), True)
    print("   data -> lba %s (%d sectors), record %d bytes into %s"
          % (plan["data_lba"], plan["sectors_needed"], plan["record_bytes"], DIR))
    print("   %s: declared %d, allocated %d, room %d"
          % (DIR, plan["parent"]["declared"], plan["parent"]["allocated"],
             plan["parent"]["room"]))

    lba, need = plan["data_lba"], plan["sectors_needed"]
    target = set(range(lba, lba + need))

    # THE LOAD-BEARING CHECK. The target sectors must be claimed by neither
    # filesystem. This is the question the first version of free_runs() got
    # wrong by answering a narrower one -- it walked ISO9660 only and
    # reported UDF's metadata as free. Both claims are re-derived here from
    # the disc rather than read back out of the module under test.
    iso_used = set()
    for f in iso.files():
        n = (f.size + SECTOR - 1) // SECTOR
        iso_used.update(range(f.lba, f.lba + n))
    ck("target sectors are claimed by no ISO9660 file", target & iso_used, set())

    udf = iso._udf_sectors()
    ck("target sectors are claimed by no UDF structure", target & udf, set())

    # UDF's partition is where all its file data lives, and its extents are
    # partition-relative -- so a file below the partition start is invisible
    # to UDF by construction rather than by luck.
    import struct
    pd_start = None
    for s in sorted(udf):
        b = iso.read_at(s * SECTOR, 196)
        if struct.unpack_from("<H", b, 0)[0] == 5:
            pd_start = struct.unpack_from("<I", b, 188)[0]
            break
    print("   UDF partition starts at lba %s; target is %d..%d"
          % (pd_start, lba, lba + need - 1))
    ck("target is BELOW the UDF partition", pd_start is not None
       and lba + need <= pd_start, True)

    # remove_last() restores by zeroing. That is only correct if they were
    # zero, so measure it instead of assuming it.
    before = iso.read_at(lba * SECTOR, need * SECTOR)
    ck("target sectors are zero-filled before the write",
       before.count(0), len(before))

    # Hash the files NO other check in this script reads. Section 6 opens
    # every .DAT, so archives are covered structurally; SYSTEM.CNF, the
    # executable and the IOP modules are covered by nothing -- and the
    # negative control put the forced write straight through SYSTEM.CNF and
    # the head of SLUS_205.00, where only the overlap check noticed. 37
    # files, ~2.2 MB, so this costs nothing.
    others = {f.path: h(iso.read_file(f.path))
              for f in iso.files()
              if not f.is_dir and not f.path.upper().endswith(".DAT")}
    print("   hashed %d non-archive files (the boot chain included)"
          % len(others))

    snap = dict(
        files={(f.path, f.lba, f.size) for f in iso.files()},
        n_files=len(iso.files()),
        archives=len(Game(iso_path).archives()),
        free=iso.free_runs(),
        pvd=iso.read_at(16 * SECTOR, SECTOR),
        others=others,
        udf=iso._udf_sectors(),
        plan=plan,
    )
    print("   disc before: %d files, %d archives, %d free sectors"
          % (snap["n_files"], snap["archives"],
             sum(n for _, n in snap["free"])))
    iso.close()
    return snap


def section3_dry(iso_path, blob):
    """An omitted flag plans and does not write."""
    print("\n3. add() with the flag omitted -- must NOT write")
    iso = Iso9660(iso_path, writable=True)
    before = h(iso.read_at(0, 512 * SECTOR))
    r = iso.add(DIR, NAME, blob)
    ck("reports dry", r.get("dry"), True)
    ck("reports not written", r.get("written"), False)
    ck("the first 512 sectors are untouched",
       h(iso.read_at(0, 512 * SECTOR)), before)
    iso.close()


def section4_write(iso_path, blob, snap):
    print("\n4. add(dry=False) -- three writes, nothing relocated")
    iso = Iso9660(iso_path, writable=True)
    r = iso.add(DIR, NAME, blob, dry=False)
    ck("reports written", r.get("written"), True)
    print("   landed at lba %d, %d bytes" % (r["lba"], r["bytes"]))
    iso.close()
    return r


def section5_verify(iso_path, blob, snap, res):
    print("\n5. the disc still verifies")
    iso = Iso9660(iso_path)

    ck("PVD still parses as ISO9660", iso.volume_id, "REDDEADREVOLVER")
    ck("PVD total_sectors unchanged", iso.total_sectors,
       Iso9660(VANILLA_ISO).total_sectors)

    now = {(f.path, f.lba, f.size) for f in iso.files()}
    ck("file count grew by exactly one",
       len(iso.files()) - snap["n_files"], 1)
    # The whole no-move rule, stated as a set difference: everything that was
    # there before is still there at the same LBA with the same length.
    ck("every pre-existing file is unmoved and unresized",
       snap["files"] - now, set())
    ck("exactly one file is new", len(now - snap["files"]), 1)

    path = "%s/%s" % (DIR, NAME)
    got = iso.read_file(path)[:len(blob)]
    ck("the new file reads back byte-exact off the disc", h(got), h(blob))

    # Free space shrank by exactly what was consumed -- not more (something
    # else was overwritten) and not less (the allocation was not recorded).
    consumed = sum(n for _, n in snap["free"]) - sum(n for _, n in iso.free_runs())
    ck("free sectors fell by exactly the sectors used",
       consumed, snap["plan"]["sectors_needed"])

    # THE SAME OVERLAP CHECK AS SECTION 2, BUT AGAINST WHAT WAS ACTUALLY
    # WRITTEN. Section 2 checks a PLAN; a plan that is sound does not prove
    # the write followed it, and `add(lba=...)` can be handed any sector at
    # all. Without this, forcing the write onto an occupied LBA passes every
    # other check in this section, because the directory records still say
    # what they always said -- only the bytes underneath them changed.
    new_file = (now - snap["files"]).pop()
    n = (new_file[2] + SECTOR - 1) // SECTOR
    landed = set(range(new_file[1], new_file[1] + n))
    occupied = set()
    for f in iso.files():
        if f.path == new_file[0]:
            continue
        k = (f.size + SECTOR - 1) // SECTOR
        occupied.update(range(f.lba, f.lba + k))
    ck("what was WRITTEN overlaps no other file", landed & occupied, set())
    ck("what was WRITTEN overlaps no UDF structure", landed & snap["udf"], set())

    # And the content of everything the checks above only cover structurally.
    changed = [p for p, want in snap["others"].items()
               if h(iso.read_file(p)) != want]
    ck("every non-archive file is byte-identical (%d, boot chain included)"
       % len(snap["others"]), changed, [])
    iso.close()

    print("\n6. the app lists it, and opens it")
    g = Game(iso_path)
    arcs = g.archives()
    ck("archive count grew by one", len(arcs) - snap["archives"], 1)
    ck("the new archive is listed", path in arcs, True)

    # "listed by the app" is the Accept line's own phrase, so assert the list
    # the app actually builds rather than the raw archive roster. Two layers:
    # `list_levels` is the LVLS_* filter that drives the level editor, the
    # mission mapping, the asset sweep and the link index; `modes.catalog`
    # is what the levels list is rendered from. A new campaign archive with
    # no row in the mission table appears in the catalogue with no missions
    # attached -- which is the correct answer for a level that is not in the
    # progression, not an omission.
    from rdrstudio.level import list_levels
    from rdrstudio import modes
    ck("list_levels() returns it", path in list_levels(g), True)
    cat = modes.catalog(g)
    entry = [e for e in cat if e["archive"] == path]
    ck("modes.catalog() lists it", len(entry), 1)
    if entry:
        ck("catalogued under the right group", entry[0]["group"], DIR)
        ck("and with no mission attached", entry[0].get("missions"), [])

    d = g.archive(path)
    ck("it parses as a Dave archive off the disc", d.magic, "Dave")
    ck("with every entry", len(d), len(PAYLOAD))
    for nm, want in PAYLOAD.items():
        ck("reads from the disc: %s" % nm, d.read(nm), want)

    # Every OTHER archive must still open. A corrupt directory extent shows
    # up here and nowhere else -- the new file can be perfect while the
    # record before it has been trampled.
    bad = []
    for a in arcs:
        if a == path:
            continue
        try:
            Game(iso_path).archive(a)
        except Exception as ex:
            bad.append((a, "%s: %s" % (type(ex).__name__, ex)))
    ck("every pre-existing archive still opens (%d)" % (len(arcs) - 1), bad, [])


def section7_remove(iso_path, snap):
    print("\n7. remove_last() -- the door opens both ways")
    iso = Iso9660(iso_path, writable=True)
    r = iso.remove_last(DIR, NAME, dry=False)
    ck("reports written", r.get("written"), True)
    iso.close()

    iso = Iso9660(iso_path)
    ck("file count is back", len(iso.files()), snap["n_files"])
    ck("the file set is identical to before",
       {(f.path, f.lba, f.size) for f in iso.files()}, snap["files"])
    ck("free runs are identical to before", iso.free_runs(), snap["free"])
    changed = [p for p, want in snap["others"].items()
               if h(iso.read_file(p)) != want]
    ck("every non-archive file is still byte-identical", changed, [])
    iso.close()


# ------------------------------------------------------------------- restore

def regions(plan):
    """Every byte range the add touches, as (offset, length)."""
    lba, need = plan["data_lba"], plan["sectors_needed"]
    p = plan["parent"]
    return [
        ("file data", lba * SECTOR, need * SECTOR),
        # The whole allocated directory extent, not just the appended record:
        # cheap, and it catches a write that lands at the wrong offset inside
        # it, which a record-sized window would step straight over.
        ("%s extent" % DIR, p["lba"] * SECTOR, p["allocated"]),
        ("root record length field", plan["writes"][2]["at"], 8),
    ]


def snapshot(iso_path, plan):
    with open(iso_path, "rb") as f:
        out = []
        for label, off, n in regions(plan):
            f.seek(off)
            out.append((label, off, f.read(n)))
    return out


def verify_or_restore(iso_path, snap_bytes):
    """Compare every touched region to its snapshot; rewrite what differs."""
    print("\n8. the disc is byte-identical to how it was found")
    dirty = []
    with open(iso_path, "rb") as f:
        for label, off, want in snap_bytes:
            f.seek(off)
            if f.read(len(want)) != want:
                dirty.append((label, off, want))
    for label, off, want in dirty:
        with open(iso_path, "r+b") as f:
            f.seek(off)
            f.write(want)
            f.flush()
            os.fsync(f.fileno())
        print("   RESTORED %s at 0x%X (%d bytes) -- remove_last did not"
              % (label, off, len(want)))
    ck("every touched region matches the snapshot",
       [d[0] for d in dirty], [])
    return not dirty


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso", default=MODDED_ISO)
    ap.add_argument("--offline", action="store_true",
                    help="sections 1-2 only, read-only, against the pristine disc")
    a = ap.parse_args()

    blob = section1_build()

    if a.offline:
        section2_ground(VANILLA_ISO, blob)
        ok = all(checks)
        print("\n%s -- %d/%d checks, sections 3-8 not run"
              % ("PARTIAL" if ok else "FAIL", sum(checks), len(checks)))
        return 0 if ok else 1

    snap = section2_ground(a.iso, blob)

    # The disc must be nobody else's right now. An undetermined answer is not
    # a quiet machine -- refuse rather than race a suite or the emulator.
    try:
        open(a.iso, "r+b").close()
    except Exception as ex:
        print("\nREFUSING: could not establish that the disc is free -- %s" % ex)
        return 1

    snap_bytes = snapshot(a.iso, snap["plan"])
    print("   snapshotted %d regions, %d bytes"
          % (len(snap_bytes), sum(len(b) for _, _, b in snap_bytes)))

    try:
        section3_dry(a.iso, blob)
        res = section4_write(a.iso, blob, snap)
        section5_verify(a.iso, blob, snap, res)
        section7_remove(a.iso, snap)
    finally:
        verify_or_restore(a.iso, snap_bytes)

    ok = all(checks)
    print("\n%s -- %d/%d checks" % ("PASS" if ok else "FAIL",
                                    sum(checks), len(checks)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
