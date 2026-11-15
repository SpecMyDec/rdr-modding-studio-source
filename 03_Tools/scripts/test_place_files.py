"""Place Files: overwrite a `.mod` and a `.tex` that are already on the disc.

The tester on v1.2.0: "Place Files cannot overwrite .mod and .tex". It could not
overwrite ANYTHING: the route's success response was built as
`dict(ok=True, used=..., slot=..., **plan)` and `plan` already carried `slot`,
so the reply raised TypeError *after* `s.write` had landed the bytes. The
archive changed and the UI was told the write had failed. Only the add half
had ever been exercised, which is why only the add half was known to work.

So this asserts the real effect, from outside the server:

  1. the dry run prices the write the way dave.patch actually stores it
  2. the write answers 200 with a plan, no error
  3. the bytes of the ARCHIVE ON THE ISO change -- read back through a fresh
     handle, not through the Studio that just wrote them
  4. the entry re-reads as exactly the bytes that were placed
  5. `revert_file` puts it back to vanilla, byte for byte

The donor content is another real `.mod`/`.tex` from the same archive, so this
is the thing a user actually does: drop a different model or texture over an
existing one.
"""

import hashlib
import json
import os
import sys
import urllib.request
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rdrstudio import Game                       # noqa: E402
from rdrstudio import config as cfg              # noqa: E402

BASE = "http://127.0.0.1:8731/api/"
ARCHIVE = "LVLS_SNG/MANS.DAT"


def post(route, body, timeout=300):
    req = urllib.request.Request(
        BASE + route, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as ex:
        # A 500 here is the bug this test exists for -- report the body
        # rather than a bare stack trace.
        return {"_status": ex.code, "error": ex.read().decode("utf-8", "replace")}


def b64(data):
    import base64
    return base64.b64encode(data).decode("ascii")


def stored_size(data, keep_raw=False):
    """What dave.patch will actually occupy.

    Deflate at level 9, falling back to raw when compression grew it -- and
    raw outright for an entry that ships uncompressed, because patch keeps a
    file's stored form rather than changing it.
    """
    if keep_raw:
        return len(data)
    co = zlib.compressobj(9, zlib.DEFLATED, -15)
    packed = co.compress(data) + co.flush()
    return min(len(packed), len(data))


def archive_bytes(iso_path, archive):
    """sha256 of the archive's whole extent as it sits in the ISO."""
    g = Game(iso_path)
    try:
        f = g.iso.get(archive)
        off, size = f.offset, f.size
    finally:
        g.close()
    h = hashlib.sha256()
    with open(iso_path, "rb") as fh:
        fh.seek(off)
        while size:
            chunk = fh.read(min(1 << 20, size))
            h.update(chunk)
            size -= len(chunk)
    return h.hexdigest()


def entry_state(iso_path, archive, name):
    """(stored bytes at the entry's own offset, decompressed contents, slot).

    A FRESH Game every time: the point is to read what is on the disc, not
    what the writing process believes it put there.
    """
    g = Game(iso_path)
    try:
        d = g.archive(archive)
        e = d.find(name)
        return d.raw(e), d.read(e), e.slot
    finally:
        g.close()


def pick_pair(ext):
    """(target, donor, donor bytes): overwrite `target` with `donor`'s content.

    The donor is a different real file of the same kind -- the biggest one
    whose stored form still fits the target's slot -- so this is the thing a
    user actually does, and it lands near the top of the budget rather than
    in a slot with room to spare.
    """
    g = Game(cfg.VANILLA_ISO)
    try:
        d = g.archive(ARCHIVE)
        rows = [e for e in d if e.ext == ext]
        rows.sort(key=lambda e: -e.headroom)
        for target in rows[:4]:
            raw = not target.compressed
            for donor in sorted(rows, key=lambda e: -e.size_full)[:40]:
                if donor.name == target.name:
                    continue
                data = d.read(donor)
                if data and stored_size(data, raw) <= target.slot:
                    return target.name, donor.name, data
    finally:
        g.close()
    return None, None, None


def check(ext, failures):
    target, donor, new = pick_pair(ext)
    if target is None:
        failures.append("no .%s pair with room in %s" % (ext, ARCHIVE))
        return
    raw0, content0, slot = entry_state(cfg.MODDED_ISO, ARCHIVE, target)
    arc0 = archive_bytes(cfg.MODDED_ISO, ARCHIVE)
    print("  .%s: placing %s (%d bytes) over %s (slot %d)"
          % (ext, donor, len(new), target, slot))
    try:
        if new == content0:
            failures.append(".%s donor and target already hold the same bytes"
                            % ext)
            return

        plan = post("file_drop", dict(archive=ARCHIVE, name=target,
                                      data_b64=b64(new), dry=True))
        print("     plan: mode=%s stored=%s slot=%s fits=%s"
              % (plan.get("mode"), plan.get("stored"), plan.get("slot"),
                 plan.get("fits")))
        if plan.get("mode") != "replace":
            failures.append(".%s dry run called it %r, not a replace"
                            % (ext, plan.get("mode")))
        want = stored_size(new, keep_raw=(len(raw0) == len(content0)))
        if plan.get("stored") != want:
            failures.append(".%s dry run priced %s stored bytes, dave.patch "
                            "will write %d"
                            % (ext, plan.get("stored"), want))
        if not plan.get("fits"):
            failures.append(".%s dry run says it does not fit slot %d"
                            % (ext, slot))
            return

        wrote = post("file_drop", dict(archive=ARCHIVE, name=target,
                                       data_b64=b64(new)))
        print("     write: %s" % json.dumps(wrote)[:150])
        if wrote.get("error") or not wrote.get("ok"):
            failures.append(".%s overwrite was refused: %s"
                            % (ext, wrote.get("error") or wrote))
            return

        raw1, content1, _ = entry_state(cfg.MODDED_ISO, ARCHIVE, target)
        arc1 = archive_bytes(cfg.MODDED_ISO, ARCHIVE)
        print("     archive sha %s -> %s" % (arc0[:12], arc1[:12]))
        if arc1 == arc0:
            failures.append(".%s the archive bytes on the ISO did not change"
                            % ext)
        if raw1 == raw0:
            failures.append(".%s the stored bytes at the entry did not change"
                            % ext)
        if content1 != new:
            failures.append(".%s re-read returned %d bytes, not the %d placed"
                            % (ext, len(content1), len(new)))
        else:
            print("     re-reads as the placed bytes (%d)" % len(content1))
    finally:
        # Unconditional, and that is the point: the defect under test wrote
        # the bytes and THEN failed to answer, so a revert that only runs on
        # the happy path leaves the shared ISO dirty for every test after it.
        post("revert_file", dict(archive=ARCHIVE, name=target))
        _, restored, _ = entry_state(cfg.MODDED_ISO, ARCHIVE, target)
        vanilla = Game(cfg.VANILLA_ISO)
        try:
            stock = vanilla.archive(ARCHIVE).read(target)
        finally:
            vanilla.close()
        if restored != stock:
            failures.append(".%s did not revert to vanilla" % ext)
        else:
            print("     reverted to vanilla")


def main():
    failures = []
    for ext in ("mod", "tex"):
        check(ext, failures)
    for line in failures:
        print("  FAIL " + line)
    if failures:
        return 1
    print("  OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
