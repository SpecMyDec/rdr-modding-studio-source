"""The writer lock: stale ones clear, live ones refuse with the right reason.

There are four reasons the Studio cannot write the working ISO and only one
of them is the emulator: the disc is missing, the disc carries the read-only
attribute a copied vanilla disc inherits, another process holds the writer
lock (REQ-078), or PCSX2 has it. `Session.locked_problem` tells them apart.

Until 2026-09-19 it told them apart and then nobody heard. `routes2._disc_busy`
threw the answer away and substituted "PCSX2 has the working disc / Close PCSX2
and try again", keeping the real reason only as `problem.detail` -- which
`web/transfer.js:renderProblem` puts behind a collapsed "Technical detail"
disclosure. Five route sites never even asked: they raised a hand-written
`RuntimeError("PCSX2 holds the working ISO")`, a sentence no code had checked.
So three quarters of the time the user read advice they could not act on, and
the sentence they could act on was folded up under a triangle.

What this asserts is therefore not "a refusal happens" -- that never stopped
happening, and it is why the bug survived. It asserts the refusal the BROWSER
receives carries the cause that is true.

Offline: no ISO, no Studio, no port, no lock. It builds a bare Studio with
__new__ and points modded_path at a temp file, because locked_problem reads
nothing else.

    py -3 03_Tools/scripts/test_disc_locked.py
"""

import os
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "03_Tools"))

from rdrstudio import api                                     # noqa: E402
from rdrstudio import iso9660                                 # noqa: E402
from rdrstudio import routes2                                 # noqa: E402
from rdrstudio import studio as studio_mod                    # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    print("  %-4s %s%s" % ("ok" if cond else "FAIL", name,
                           "" if cond else "  -- %s" % detail))
    if not cond:
        FAILED.append(name)


def bare(path):
    """A Studio with nothing open. locked_problem() reads modded_path only."""
    s = studio_mod.Studio.__new__(studio_mod.Studio)
    s.modded_path = path
    s.game = None
    return s


def main():
    tmp = tempfile.mkdtemp(prefix="rdr-locked-")
    disc = os.path.join(tmp, "RDR_WORKING.iso")
    lock = disc + iso9660.Iso9660.LOCK_SUFFIX

    print("1. the four causes are four different answers")
    seen = {}

    s = bare(disc)                                   # nothing there at all
    seen["missing"] = s.locked_problem()
    check("a missing disc is not reported as read-only",
          "read-only" not in seen["missing"][0].lower(), seen["missing"][0])
    check("a missing disc says where it looked",
          disc in seen["missing"][0], seen["missing"][0])

    with open(disc, "wb") as fh:                     # present and writable
        fh.write(b"\0" * 16)
    seen["pcsx2"] = s.locked_problem()
    check("nothing else explains it -> the emulator",
          "PCSX2" in seen["pcsx2"][0], seen["pcsx2"][0])

    with open(lock, "w", encoding="utf-8") as fh:    # a second writer
        fh.write("pid 4242 on windows, opened 2026-09-19 14:17:24\n")
    seen["writer"] = s.locked_problem()
    check("another writer is named, not the emulator",
          "PCSX2" not in seen["writer"][0], seen["writer"][0])
    check("the holder is quoted from the lock file",
          "pid 4242" in seen["writer"][0], seen["writer"][0])
    check("the fix names the lock file to delete",
          lock in seen["writer"][1], seen["writer"][1])
    os.remove(lock)

    os.chmod(disc, 0o444)                            # the read-only attribute
    # chmod does not always take: root under WSL writes 0444 files regardless,
    # and then locked_problem correctly falls through to the PCSX2 answer. That
    # is the environment declining to pose the question, so the cause is left
    # OUT of `seen` entirely rather than recorded as an answer it did not give
    # -- a duplicate parked in there passes section 2 by agreeing with the
    # emulator's sentence and fails the distinctness check below for a reason
    # that has nothing to do with the code. The Windows suite does exercise it.
    if os.access(disc, os.W_OK):
        print("  SKIP the read-only cause: this process can write 0444 files")
    else:
        seen["readonly"] = s.locked_problem()
        check("a read-only disc is not blamed on the emulator",
              "PCSX2" not in seen["readonly"][0], seen["readonly"][0])
        check("the fix is the one the user can act on",
              "Properties" in seen["readonly"][1], seen["readonly"][1])
        check("read-only is named as such",
              "read-only" in seen["readonly"][0].lower(), seen["readonly"][0])
    os.chmod(disc, 0o666)

    msgs = [v[0] for v in seen.values()]
    check("no two causes produce the same sentence",
          len(set(msgs)) == len(msgs), msgs)
    check("every cause still says 'working ISO', so routes2._busy files it "
          "as a refusal and not a 500",
          all("working ISO" in m for m in msgs), msgs)

    print("2. the cause reaches the browser as the MESSAGE, not as detail")
    for cause, (msg, fix) in seen.items():
        ex = api.DiscLocked(msg, fix)
        check("_busy(%s) is still a refusal" % cause, routes2._busy(ex))
        r = routes2._disc_busy(ex)
        check("%s: problem.message IS the cause" % cause, r.message == msg,
              r.message)
        check("%s: problem.fix IS the fix" % cause, r.fix == fix, r.fix)
        check("%s: code stays in the frozen set" % cause,
              r.code == "disc_busy", r.code)
        body = r.body()["problem"]
        check("%s: the browser body carries it" % cause,
              body["message"] == msg and body["fix"] == fix, body)

    print("3. the sites that KNOW the disc is shut ask, they do not guess")
    s2 = bare(disc)
    with open(lock, "w", encoding="utf-8") as fh:
        fh.write("pid 4242 on windows, opened 2026-09-19 14:17:24\n")
    r = routes2._locked(s2)
    check("_locked() reports the writer lock", "pid 4242" in r.message,
          r.message)
    check("_locked() does not invent PCSX2", "PCSX2" not in r.message,
          r.message)
    os.remove(lock)

    print("4. an unrecognised RuntimeError keeps the old wording")
    # Not every caller has an answer to offer. The point of the change is to
    # stop INVENTING one, not to degrade the fallback.
    r = routes2._disc_busy(RuntimeError("PCSX2 holds the working ISO"))
    check("bare RuntimeError still refuses", r.code == "disc_busy")
    check("bare RuntimeError keeps the PCSX2 sentence",
          "PCSX2" in r.message, r.message)
    check("bare RuntimeError keeps its text as detail",
          "PCSX2 holds the working ISO" in r.detail, r.detail)

    print("5. a stale writer lock clears; a live one does not")
    # The other half of the same story. run_tests ends its Studio with
    # terminate() -- TerminateProcess on Windows, uncatchable -- so the lock
    # outlives the run, and from WSL nothing reclaims it because _pid_alive
    # answers None across the pid-space boundary. Iso9660.clear_stale_lock is
    # what the suite now calls on its way out. The contract it must keep is
    # NOT "clears locks": it is "clears only locks whose owner is provably
    # gone", and the second half is the one that matters, because breaking it
    # hands the disc to a second writer by way of the lock.
    plat = "windows" if os.name == "nt" else "posix"
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    if iso9660.Iso9660._pid_alive(dead.pid, plat) is not False:
        # pid reuse, or a platform that cannot answer. Say so rather than
        # asserting against a pid we have not established is gone.
        print("  SKIP the stale-lock checks: pid %d is not provably dead"
              % dead.pid)
    else:
        with open(lock, "w", encoding="utf-8") as fh:
            fh.write("pid %d on %s, opened 2026-09-19 14:17:24\n"
                     % (dead.pid, plat))
        got = iso9660.Iso9660.clear_stale_lock(disc)
        check("a dead holder's lock is cleared", got is not None, got)
        check("the file is actually gone", not os.path.exists(lock))
        check("it reports whose lock it removed",
              got is not None and ("pid %d" % dead.pid) in got, got)

        import time as _t
        with open(lock, "w", encoding="utf-8") as fh:
            fh.write("pid %d on %s (%s), opened %s\n"
                     % (os.getpid(), plat, iso9660.Iso9660._holder_image(),
                        _t.strftime("%Y-%m-%d %H:%M:%S")))
        got = iso9660.Iso9660.clear_stale_lock(disc)
        check("a LIVE holder's lock is left alone", got is None, got)
        check("and the file is still there", os.path.exists(lock))
        os.remove(lock)

        # 2026-11-11, measured on the dev machine: a killed Studio's pid was
        # handed to a dllhost.exe, "the pid exists" answered True, and every
        # write on the disc was refused for a holder that died the day
        # before. A pid is the holder only if it COULD be: same program,
        # started no later than the lock.
        if plat == "windows":
            with open(lock, "w", encoding="utf-8") as fh:
                fh.write("pid %d on windows, opened 2026-09-19 14:17:24\n"
                         % os.getpid())
            got = iso9660.Iso9660.clear_stale_lock(disc)
            check("a RECYCLED pid (process younger than the lock) is not the "
                  "holder: cleared", got is not None, got)
            if os.path.exists(lock):
                os.remove(lock)
            with open(lock, "w", encoding="utf-8") as fh:
                fh.write("pid %d on windows (dllhost.exe), opened %s\n"
                         % (os.getpid(), _t.strftime("%Y-%m-%d %H:%M:%S")))
            got = iso9660.Iso9660.clear_stale_lock(disc)
            check("a pid now owned by a DIFFERENT program is not the holder: "
                  "cleared", got is not None, got)
            if os.path.exists(lock):
                os.remove(lock)

        other = "posix" if plat == "windows" else "windows"
        with open(lock, "w", encoding="utf-8") as fh:
            fh.write("pid %d on %s, opened 2026-09-19 14:17:24\n"
                     % (dead.pid, other))
        got = iso9660.Iso9660.clear_stale_lock(disc)
        check("a lock from the OTHER pid space is not touched -- None is not "
              "rounded down to dead", got is None, got)
        os.remove(lock)

        with open(lock, "w", encoding="utf-8") as fh:
            fh.write("held by something that did not write a pid\n")
        got = iso9660.Iso9660.clear_stale_lock(disc)
        check("an unparseable lock is left alone", got is None, got)
        os.remove(lock)

    check("no lock at all is not an error",
          iso9660.Iso9660.clear_stale_lock(disc) is None)

    try:
        os.remove(disc)
        os.rmdir(tmp)
    except OSError:
        pass

    print("")
    if FAILED:
        print("FAILED %d check(s):" % len(FAILED))
        for f in FAILED:
            print("  %s" % f)
        return 1
    print("the refusal names the cause that is true, and a lock is cleared\n"
          "only when its owner is provably gone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
