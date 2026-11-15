"""Is the file the user picked really Red Dead Revolver?

Four tiers, cheapest first, so the first-run window can say something useful
the instant a file is chosen and only get slower if the user asks it to.

    tier 1  size            microseconds   catches "that's a .zip"
    tier 2  ISO descriptor  milliseconds   catches "that's some other ISO"
    tier 3  boot + archives ~a second      catches "that's a different PS2 game"
    tier 4  SHA-256         ~13 s / 2.2 GB catches a bad rip or a bad region

Only tier 4 can prove the exact build, and only tier 4 is slow, so it is
offered rather than forced. Tiers 1-3 are enough to refuse an obviously wrong
file, and tier 3 already pins the disc to SLUS-20500 specifically.

Every result carries `confidence` in the Studio's own vocabulary --
measured / established / inferred -- so the first-run screen speaks the same
language as the rest of the tool.
"""

import hashlib
import os
import struct

# The USA retail disc, VER 1.03. Measured off the developer's own rip and recorded in
# 01_Vanilla/CHECKSUMS.txt on 2026-07-26.
KNOWN = {
    "cca17ea8717b5681feaa0177ddace229dd4662032229afb11b21ea821ed9cfa0": {
        "label": "Red Dead Revolver (USA), SLUS-20500, VER 1.03",
        "size": 2340487168,
        "sectors": 1142816,
    },
}

EXPECT_SIZE = 2340487168
EXPECT_SECTORS = 1142816
EXPECT_BOOT = "SLUS_205.00"
EXPECT_SWAT_ENTRIES = 47115
SECTOR = 2048


class Result(dict):
    """A verdict the UI can render directly."""

    @property
    def ok(self):
        return self.get("verdict") in ("match", "likely")


def _fail(stage, message, hint=None, **extra):
    r = Result(verdict="reject", stage=stage, message=message,
               confidence="measured")
    if hint:
        r["hint"] = hint
    r.update(extra)
    return r


# --------------------------------------------------------------- tiers 1 & 2

def quick(path):
    """Tiers 1 and 2. Costs a few milliseconds; safe to run on every keystroke."""
    if not path or not os.path.isfile(path):
        return _fail("exists", "That file is not there.")

    size = os.path.getsize(path)
    if size < 100 * 1024 * 1024:
        return _fail("size", "That file is only %.1f MB. A PS2 disc image is "
                             "about 2.2 GB." % (size / 1048576.0),
                     hint="If you have a .zip or .7z, extract it first.",
                     size=size)

    try:
        with open(path, "rb") as fh:
            fh.seek(16 * SECTOR)
            pvd = fh.read(SECTOR)
    except OSError as ex:
        return _fail("read", "Could not read that file: %s" % ex)

    if len(pvd) < 200 or pvd[1:6] != b"CD001":
        return _fail("iso", "That is not a disc image -- it has no ISO 9660 "
                            "header.",
                     hint="A .bin/.cue pair or a compressed .chd needs "
                          "converting to .iso first.")

    volume_id = pvd[40:72].decode("ascii", "replace").strip()
    sectors = struct.unpack_from("<I", pvd, 80)[0]

    exact_size = (size == EXPECT_SIZE)
    exact_sectors = (sectors == EXPECT_SECTORS)

    return Result(
        verdict="likely" if (exact_size and exact_sectors) else "unsure",
        stage="header",
        confidence="measured",
        size=size,
        sectors=sectors,
        volume_id=volume_id,
        size_matches=exact_size,
        sectors_matches=exact_sectors,
        message=("Disc image, %s, %.2f GB." % (volume_id or "unnamed",
                                               size / 1073741824.0)),
    )


# -------------------------------------------------------------------- tier 3

def identify(path):
    """Tier 3. Opens the disc for real: which game, and are the archives sane.

    Costs about a second. Uses the toolkit's own reader, so if this passes the
    Studio will definitely be able to open the file too -- which is the actual
    question first run needs answered.
    """
    r = quick(path)
    if r.get("verdict") == "reject":
        return r

    try:
        from rdrstudio import Game
    except Exception as ex:                                # noqa: BLE001
        r["identify_error"] = "toolkit unavailable: %s" % ex
        return r

    try:
        g = Game(path, writable=False)
    except Exception as ex:                                # noqa: BLE001
        return _fail("open", "The disc image would not open: %s" % ex)

    try:
        boot = _boot_id(g)
        archives = g.archives()
        swat = None
        try:
            swat = g.archive("SWAT.DAT").count
        except Exception:                                  # noqa: BLE001
            pass
    finally:
        try:
            g.close()
        except Exception:                                  # noqa: BLE001
            pass

    r["boot"] = boot
    r["archives"] = len(archives)
    r["swat_entries"] = swat

    if boot and EXPECT_BOOT not in boot.upper().replace("_", "_"):
        return _fail("game", "That disc boots %s. Red Dead Revolver (USA) "
                             "boots %s." % (boot, EXPECT_BOOT),
                     hint="A PAL or Japanese disc has a different boot ID. "
                          "The toolkit has only ever been tested on the USA "
                          "disc.",
                     boot=boot)

    if swat is not None and swat != EXPECT_SWAT_ENTRIES:
        r["verdict"] = "unsure"
        r["confidence"] = "measured"
        r["message"] = ("Red Dead Revolver, but SWAT.DAT holds %d files and "
                        "the tested disc holds %d. This may be a different "
                        "revision." % (swat, EXPECT_SWAT_ENTRIES))
        return r

    r["verdict"] = "likely"
    r["confidence"] = "established"
    r["message"] = ("Red Dead Revolver (USA). %d archives, SWAT.DAT holds "
                    "%s files." % (len(archives), swat))
    return r


# -------------------------------------------------------------------- tier 4

def checksum(path, progress=None, chunk=8 << 20):
    """Tier 4. The only test that proves the exact build. ~13 s per 2.2 GB.

    `progress(done, total)` is called as it goes so the window can show a bar
    instead of freezing.
    """
    total = os.path.getsize(path)
    h = hashlib.sha256()
    done = 0
    with open(path, "rb") as fh:
        while True:
            buf = fh.read(chunk)
            if not buf:
                break
            h.update(buf)
            done += len(buf)
            if progress:
                progress(done, total)
    digest = h.hexdigest()
    known = KNOWN.get(digest)
    if known:
        return Result(verdict="match", stage="sha256", confidence="measured",
                      sha256=digest, label=known["label"],
                      message="Exact match: %s" % known["label"])
    return Result(verdict="unsure", stage="sha256", confidence="measured",
                  sha256=digest,
                  message="This is a working disc image, but not a build the "
                          "toolkit has been checked against. Everything should "
                          "still work; if something looks wrong, this is why.",
                  hint="Known good: %s" % next(iter(KNOWN)))


def _boot_id(game):
    """Read SYSTEM.CNF for the BOOT2 line -- the disc's own name for itself."""
    try:
        raw = game.iso.read_file("SYSTEM.CNF")
    except Exception:                                      # noqa: BLE001
        return None
    text = raw.decode("ascii", "replace")
    for line in text.splitlines():
        if line.strip().upper().startswith("BOOT2"):
            val = line.split("=", 1)[-1].strip()
            return val.rsplit("\\", 1)[-1].rsplit("/", 1)[-1].split(";")[0]
    return None


if __name__ == "__main__":
    import json
    import sys
    import time

    # No default path here. This block never runs in the frozen app, but the
    # literal is still compiled into the module that ships, and it used to
    # name the build machine's drive and project folder.
    if len(sys.argv) > 1:
        p = sys.argv[1]
    else:
        print("usage: verify.py <path to a Red Dead Revolver .iso>")
        raise SystemExit(2)

    sys.path.insert(0, os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))

    for name, fn in (("quick", quick), ("identify", identify)):
        t = time.perf_counter()
        out = fn(p)
        print("[%s] %.3f s" % (name, time.perf_counter() - t))
        print(json.dumps(out, indent=1))
        print()

    if "--sha" in sys.argv:
        t = time.perf_counter()
        out = checksum(p)
        print("[checksum] %.1f s" % (time.perf_counter() - t))
        print(json.dumps(out, indent=1))
