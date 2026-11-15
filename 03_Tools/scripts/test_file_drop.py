"""Drag-and-drop: the Place Files pipeline, driven end to end.

The tester on v1.2.0: drag-and-drop "reports success while the archive comes
back unmodified". Dropping files runs three steps in `web/app.js`, and only
the middle one is a judgement call:

    stageDropFiles()  -> POST file_drop {dry:true} per dropped file
    renderDropPlan()  -> decides, per file, whether it is writable
    applyDropPlan()   -> POST file_drop for each writable one

This test performs all three -- with `api()`'s throw-on-`error` and
`applyDropPlan`'s writable rule copied out of `web/app.js`, so the gate under
test is the SAME gate the browser applies -- and then reads the ISO to see
what actually happened. Two properties:

  * a drop the plan calls writable must really change the archive on disc.
    It used to write the bytes and THEN answer 500, so the panel reported a
    failure for a write that had landed.
  * a drop the plan cannot do must come back as a plan that says why, before
    anything is written. It used to come back as `error`, which `api()`
    throws; the catch in `stageDropFiles` then replaces the whole plan with
    `{error}`, so the file was blocked -- correctly -- but the panel had a
    bare exception string and `dave.can_add`'s actual reasons never reached
    the user.

The whole run is over existing entries plus one deliberately impossible add,
so nothing is left on the disc: the replacements are reverted to vanilla and
the impossible add never happens.
"""

import base64
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
ALLOW_GROW = False          # the View setting; off, as it ships


def post(route, body, timeout=300):
    req = urllib.request.Request(
        BASE + route, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as ex:
        return {"_status": ex.code,
                "error": ex.read().decode("utf-8", "replace")}


def stage(name, data):
    """`stageDropFiles`, including what `api()` does on the way back.

    `api()` throws on any reply carrying `error`, and the catch in
    `stageDropFiles` replaces the WHOLE plan with `{error: message}` -- `mode`
    and every other key are lost. Modelling that is the difference between the
    browser reading a plan and the browser holding an exception string, and it
    is why a dry run that answers "no" must not answer with `error`.
    """
    r = post("file_drop", dict(archive=ARCHIVE, name=name,
                               data_b64=base64.b64encode(data).decode(),
                               dry=True))
    return {"error": r["error"]} if r.get("error") else r


def writable(plan):
    """`applyDropPlan`'s own rule, from web/app.js -- kept identical on
    purpose. A plan the browser would arm the button for must be a plan the
    server can actually carry out."""
    d = plan or {}
    if d.get("error") and not d.get("mode"):
        return False                       # the plan itself failed to load
    if d.get("mode") == "replace":
        return bool(d.get("fits")) or bool(d.get("grow_possible") and ALLOW_GROW)
    if d.get("mode") == "add":
        return not (d.get("why") or [])
    return False


def archive_sha(iso_path, archive):
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


def read_entry(iso_path, archive, name):
    g = Game(iso_path)
    try:
        return g.archive(archive).read(name)
    finally:
        g.close()


def fits(data, entry):
    """Would dave.patch land these bytes in that entry's slot? Deflate 9,
    raw when compression grew it, raw outright for an uncompressed entry."""
    if not entry.compressed:
        return len(data) <= entry.slot
    co = zlib.compressobj(9, zlib.DEFLATED, -15)
    packed = co.compress(data) + co.flush()
    return min(len(packed), len(data)) <= entry.slot


def dropped_files():
    """What lands on the drop zone: two real replacements and one add that
    cannot fit. Each is (name, bytes, must_be_writable)."""
    g = Game(cfg.VANILLA_ISO)
    try:
        d = g.archive(ARCHIVE)
        gap = d.largest_gap()[1]
        out = []
        for ext in ("mod", "tex"):
            rows = sorted((e for e in d if e.ext == ext),
                          key=lambda e: -e.headroom)
            target = rows[0]
            stock = d.read(target)
            donor = None
            for e in sorted(rows, key=lambda e: -e.size_full)[:40]:
                if e.name == target.name:
                    continue
                data = d.read(e)
                if data and data != stock and fits(data, target):
                    donor = data
                    break
            if donor is None:
                raise RuntimeError("no .%s donor fits %s" % (ext, target.name))
            out.append((target.name, donor, True))
        # An add of incompressible bytes larger than the biggest free run.
        # There is nowhere on the disc to put this, and the plan has to say
        # so rather than offering to place it.
        out.append(("m1_no_room.mod", os.urandom(gap * 4), False))
        return out
    finally:
        g.close()


def main():
    failures = []
    files = dropped_files()
    before = archive_sha(cfg.MODDED_ISO, ARCHIVE)
    print("  dropped %d file(s) onto %s (archive sha %s)"
          % (len(files), ARCHIVE, before[:12]))
    written = []            # everything a non-dry drop was fired at

    try:
        # 1. stageDropFiles: one dry run per dropped file.
        plans = [stage(name, data) for name, data, _ in files]

        # 2. renderDropPlan: what the button would offer to do.
        for (name, data, expect), plan in zip(files, plans):
            got = writable(plan)
            print("     %-26s %-8s %s%s"
                  % (name, plan.get("mode"), "PLACE" if got else "blocked",
                     "" if got == expect else "   <-- WRONG"))
            if got and not expect:
                failures.append("the plan offers to place %s, and nothing "
                                "can be written for it" % name)
            elif expect and not got:
                failures.append("the plan blocked %s, which does fit" % name)
            if not expect and not (plan.get("mode") == "add"
                                   and plan.get("why")):
                # Blocked is not enough. A refusal that arrives as `error`
                # is thrown by api() and reaches the panel as a bare
                # exception string with no mode and no reasons -- the user
                # is told no, and never told why.
                failures.append("%s came back as a thrown error, not as a "
                                "plan that says why: %s" % (name, plan))

        # 3. applyDropPlan: write only what the plan armed.
        placed = []
        for (name, data, _), plan in zip(files, plans):
            if not writable(plan):
                continue
            written.append(name)
            r = post("file_drop", dict(archive=ARCHIVE, name=name,
                                       data_b64=base64.b64encode(data).decode(),
                                       grow=ALLOW_GROW))
            if r.get("error") or not r.get("ok"):
                failures.append("%s: the drop reported %s"
                                % (name, r.get("error") or r))
                continue
            placed.append((name, data))
            print("     placed %s (%s bytes stored in a %s slot)"
                  % (name, r.get("used"), r.get("slot")))

        # What the drop actually did to the disc.
        after = archive_sha(cfg.MODDED_ISO, ARCHIVE)
        print("  archive sha %s -> %s" % (before[:12], after[:12]))
        if placed and after == before:
            failures.append("the drop reported success and the archive is "
                            "unmodified")
        for name, data in placed:
            got = read_entry(cfg.MODDED_ISO, ARCHIVE, name)
            if got != data:
                failures.append("%s re-reads as %d bytes, not the %d dropped"
                                % (name, len(got), len(data)))
            else:
                print("     %s re-reads as the dropped bytes (%d)"
                      % (name, len(got)))
        # The impossible add must not have appeared.
        for name, _, expect in files:
            if expect:
                continue
            try:
                read_entry(cfg.MODDED_ISO, ARCHIVE, name)
                failures.append("%s was added even though the plan blocked it"
                                % name)
            except Exception:
                print("     %s is not on the disc, as the plan said" % name)
    finally:
        # Every name a real drop was fired at, whatever it answered: a drop
        # that fails in its REPLY has still written, which is the whole
        # reason this test exists.
        vanilla = Game(cfg.VANILLA_ISO)
        try:
            stock = vanilla.archive(ARCHIVE)
            for name in written:
                try:
                    was = stock.read(name)
                except Exception:
                    continue        # never shipped; there is nothing to revert
                post("revert_file", dict(archive=ARCHIVE, name=name))
                if read_entry(cfg.MODDED_ISO, ARCHIVE, name) != was:
                    failures.append("%s did not revert to vanilla" % name)
        finally:
            vanilla.close()
        print("  reverted %d placement(s)" % len(written))

    for line in failures:
        print("  FAIL " + line)
    if failures:
        return 1
    print("  OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
