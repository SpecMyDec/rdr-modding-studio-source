"""probe_handback.py -- does test_journal actually give the disc back?

`scripts/test_journal.py` wipes the working ISO on purpose: it needs a clean
baseline to reason about its own diffs. It banks a checkpoint first and hands
the files back at the end, and that hand-back is the only thing standing
between a routine suite run and the user's work.

On 2026-09-19 it did not stand there. The 75 files in
`06_Backups/20260919-130436_auto_before_test_journal.checkpoint.json` were
banked at 13:04, reverted to vanilla (the Studio's own session journal records
all 75), and never came back -- and the row read PASS. Two things in the old
shape allow that, and this probe holds both against the repaired code:

    skippable    the restore was the last statement of the checks, so
                 anything that raised above it jumped straight over it
    unasserted   `ok` was decided BEFORE the restore, and the restore's
                 answer was printed and never read -- so a restore that
                 applied nothing reported success

No Studio, no ISO, no network: the API is a stub, so the scenarios that are
expensive and destructive to stage for real cost nothing here.

The last case is the control. It replays the SAME silent-failure scenario
against the old shape, which passes it -- which is what makes the four before
it evidence rather than four greens in a row.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "scripts"))

import test_journal                                           # noqa: E402

BANKED = [
    dict(archive="FRONT.DAT", name="swat.strtbl", size=118032),
    dict(archive="SWAT.DAT", name="xb_swat.strtbl", size=118032),
    dict(archive="LVLS_SNG/MANS.DAT", name="lvl_mans.fog", size=2048),
]
CP = "/06_Backups/20260919-130436_auto_before_test_journal.checkpoint.json"


class Stub(object):
    """Stands in for the Studio. Records what was asked of it."""

    def __init__(self, on_disc, restore=None, restore_raises=None):
        self.on_disc = list(on_disc)      # what `diff` reports, now
        self.restore = restore            # what `checkpoint_restore` answers
        self.restore_raises = restore_raises
        self.calls = []

    def get(self, route):
        self.calls.append(route)
        if route == "diff":
            return list(self.on_disc)
        raise AssertionError("unexpected GET %s" % route)

    def post(self, route, body):
        self.calls.append(route)
        if route == "checkpoint":
            return dict(ok=True, path=CP, files=len(self.on_disc))
        if route == "revert_file":
            self.on_disc = [x for x in self.on_disc
                            if (x["archive"], x["name"])
                            != (body["archive"], body["name"])]
            return dict(ok=True)
        if route == "checkpoint_restore":
            if self.restore_raises:
                raise self.restore_raises
            if self.restore is not None:
                self.on_disc = list(self.restore)
            return dict(ok=True, applied=len(self.on_disc), failed=[])
        raise AssertionError("unexpected POST %s" % route)

    def install(self):
        test_journal.get = self.get
        test_journal.post = self.post
        return self


def banked_map(rows):
    return {(x["archive"], x["name"]): x["size"] for x in rows}


def case(name, got, want):
    ok = got == want
    print("%-58s %s" % (name, "ok" if ok else "FAILED (%r, wanted %r)"
                        % (got, want)))
    return ok


def main():
    ok = True

    # 1. the 2026-09-19 shape: the restore answers ok and the disc stays empty.
    print("\n1. restore answers ok, disc comes back EMPTY")
    Stub(on_disc=[], restore=[]).install()
    ok &= case("  hand_back reports failure",
               test_journal.hand_back(dict(path=CP), banked_map(BANKED)), False)

    # 2. a restore that puts back some of it is not a restore.
    print("\n2. restore puts back 2 of 3")
    Stub(on_disc=[], restore=BANKED[:2]).install()
    ok &= case("  hand_back reports failure",
               test_journal.hand_back(dict(path=CP), banked_map(BANKED)), False)

    # 3. right content, wrong bytes -- a size that does not match is not the
    #    file the user had.
    print("\n3. restore puts back all 3, one at the wrong size")
    wrong = [dict(BANKED[0], size=118024)] + BANKED[1:]
    Stub(on_disc=[], restore=wrong).install()
    ok &= case("  hand_back reports failure",
               test_journal.hand_back(dict(path=CP), banked_map(BANKED)), False)

    # 4. and it says yes when the disc really is back.
    print("\n4. restore puts every file back at its own size")
    Stub(on_disc=[], restore=BANKED).install()
    ok &= case("  hand_back reports success",
               test_journal.hand_back(dict(path=CP), banked_map(BANKED)), True)

    # 5. the skippable half: the checks raise, and the disc still goes back.
    print("\n5. the checks RAISE partway through")
    s = Stub(on_disc=BANKED, restore=BANKED).install()

    def boom():
        raise RuntimeError("the Studio went away mid-test")

    test_journal.checks = boom
    try:
        test_journal.main()
        raised = None
    except RuntimeError as ex:
        raised = "%s" % ex
    ok &= case("  the exception still reaches the caller", raised,
               "the Studio went away mid-test")
    ok &= case("  the restore was attempted anyway",
               "checkpoint_restore" in s.calls, True)
    ok &= case("  the disc is back", banked_map(s.on_disc),
               banked_map(BANKED))

    # 6. CONTROL. The old shape, on case 1's scenario. It must PASS -- if this
    #    line reports a failure, the control is broken and cases 1-4 prove
    #    nothing about what changed.
    print("\n6. control: the OLD shape on case 1's scenario")
    s = Stub(on_disc=[], restore=[]).install()

    def old_shape(saved):
        """What the file did before: call it, print it, never read it."""
        if saved and saved.get("path"):
            r = test_journal.post("checkpoint_restore", {"path": saved["path"]})
            print("  restored your checkpoint: %d file(s) re-applied"
                  % r.get("applied", 0))
        return True                       # `ok`, decided before the restore

    ok &= case("  the old shape reports success on the same disc",
               old_shape(dict(path=CP)), True)
    ok &= case("  ...while the disc is still empty", s.on_disc, [])

    print("\n%s" % ("ALL CHECKS PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
