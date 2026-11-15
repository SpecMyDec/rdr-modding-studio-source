#!/usr/bin/env python3
"""`verify.sh` cannot exit 0 without having run the suite.

This exists because it could, and did. verify.sh picks the current milestone
as the first `## Mn` in ROADMAP-TESTER.md without a tick. When cdd9b64 ticked
M21 -- the last un-ticked row -- the selector came back empty and the gate
exited 0 in twenty seconds having run ZERO tests. "No un-landed milestone" and
"every test passed" became the same exit code, and it was used as the second
half of a release gate in that state.

Nothing caught it because nothing tested the gate. That is what this file is.

    python3 scripts/test_verify_gate.py        (offline, ~1s, writes only to a temp dir)

HOW IT AVOIDS TESTING A COPY OF ITSELF: it runs the REAL 03_Tools/verify.sh,
with exactly one line substituted -- the one that launches the 16-minute
Windows suite -- for a stub whose exit code and output this file controls.
Everything else, including the truncation guard and its REQ-062 `-a`, is the
shipped script.

If that substitution stops matching, this file refuses (exit 2) rather than
running. A silent non-match would either test nothing or launch the real
suite from inside a test, and both of those look like a pass from outside.
"""
import os
import re
import subprocess
import sys
import tempfile

def bash_argv(script):
    """argv that runs `script` under bash, from EITHER platform.

    verify.sh is a WSL-side script -- it invokes cmd.exe, never the reverse --
    but the suite runs Windows `py -3`. So testing it from the suite means
    crossing the boundary the other way, and `bash` is not on the Windows PATH.

    The first registered run of this row came back 127 for exactly that, and
    the part worth remembering is not the 127: FOUR checks still reported PASS,
    because their condition was "the suite did not run" and bash never ran at
    all. A harness that cannot execute its subject produced a mixture of PASS
    and FAIL rather than an obvious refusal.
    """
    if os.name != "nt":
        return ["bash", script]
    # FORWARD SLASHES, and that is the whole trick. wsl.exe hands its
    # arguments through a Windows command line that eats backslashes, so
    # `wslpath -a C:\Users\...\t.sh` arrives as `C:Usersyou...t.sh` and
    # fails. Measured both ways, same file:
    #
    #     backslashes      rc=1  wslpath: C:UsersyouAppDataLocalTemp...
    #     forward slashes  rc=0  /mnt/c/Users/you/AppData/Local/Temp/...
    #
    # Windows accepts forward slashes in paths, so this costs nothing.
    p = subprocess.run(["wsl.exe", "wslpath", "-a", script.replace("\\", "/")],
                       capture_output=True, timeout=60)
    path = p.stdout.decode("utf-8", "replace").strip()
    if p.returncode != 0 or not path:
        return None
    return ["wsl.exe", "bash", path]


HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
REAL = os.path.join(TOOLS, "verify.sh")

# The one line that costs sixteen minutes. Matched loosely on its moving parts
# (flock, the lock path, run_tests.py) rather than verbatim, so a reworded
# command still matches -- but see the refusal below if it ever does not.
SUITE_LINE = re.compile(r"^.*flock\s+/tmp/rdr-suite\.lock.*run_tests\.py.*$",
                        re.M)

FAILED = []


def check(ok, label, detail=""):
    print("   %s  %s%s" % ("PASS" if ok else "FAIL", label,
                           ("  -- " + detail) if detail else ""))
    if not ok:
        FAILED.append(label)


ALL_TICKED = "## M1 ✅ — done\n## M21 ✅ — done\n"
ONE_OPEN = "## M1 ✅ — done\n## M21 — not done\n"

# Each stub touches a flag file first, so "did the suite run" is observed
# rather than inferred from the message.
GREEN = 'touch "$FLAG"; echo SUMMARY; echo "71/71 passed"; exit 0'
RED = 'touch "$FLAG"; echo SUMMARY; echo "70/71 passed"; exit 1'
TRUNCATED = 'touch "$FLAG"; echo "killed mid-run"; exit 1'
# REQ-062: a GREEN log carrying game data. 0x93/0x94 are cp1252 smart quotes
# and not valid UTF-8. The claim REQ-062 rests on is that this makes grep
# treat the log as binary, so `grep -q` exits 1 on a clean run and the gate
# calls it truncated -- which the `-a` in verify.sh prevents.
#
# THIS CASE DOES NOT HOLD THE `-a`, AND SAYING SO IS THE POINT. Removing the
# -a from verify.sh leaves this case green, so it cannot be cited as the
# reason the -a is safe to keep. Measured while mutation-testing this file:
#
#     same /usr/bin/grep, same bytes, `grep -q SUMMARY`
#         inside verify.sh   -> 0        from an interactive shell -> 1
#
# The system `grep` here is ugrep 7.8.4 wearing grep's name, and its binary
# detection is context-dependent in a way GNU grep's is not. REQ-062's
# measurement was taken against the other behaviour.
#
# What this case DOES hold is the user-visible property: a clean run whose log
# carries game data must not be reported as truncated. That is worth a check
# on its own -- it is the symptom REQ-062 was filed about -- and it is the
# most this file can honestly claim.
BINARY_GREEN = ('touch "$FLAG"; echo SUMMARY; '
                r'printf %b "roster: \x93Smiley\x94 Fawler\n"; '
                'echo "71/71 passed"; exit 0')


def sandbox(tmp, script, roadmap, registered, stub):
    root = os.path.join(tmp, "root")
    os.makedirs(os.path.join(root, "03_Tools"), exist_ok=True)
    with open(os.path.join(root, "ROADMAP-TESTER.md"), "w",
              encoding="utf-8") as fh:
        fh.write(roadmap)
    with open(os.path.join(root, "03_Tools", "run_tests.py"), "w",
              encoding="utf-8") as fh:
        fh.write("[M21] a brand-new .dat\n" if registered else "nothing\n")
    flag = os.path.join(root, "suite_ran")
    if os.path.exists(flag):
        os.remove(flag)
    with open(os.path.join(root, "stub.sh"), "w", encoding="utf-8",
              newline="\n") as fh:
        # $0, not a path from Python: on Windows the sandbox paths are
        # C:\... and bash would eat the backslashes. Deriving it inside bash
        # removes the translation instead of getting it right.
        fh.write('FLAG="$(dirname "$0")/suite_ran"\n%s\n' % stub)
    path = os.path.join(root, "03_Tools", "verify.sh")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(script)
    # BYTES, and decoded here with errors="replace" rather than by
    # subprocess. text=True decodes as UTF-8 and dies on the very cp1252 byte
    # case [4] exists to exercise -- this harness hit that on its first run,
    # which is REQ-062 reappearing inside the test written to hold REQ-062.
    # A test that cannot read a log containing game data cannot check a guard
    # whose whole subject is logs containing game data.
    argv = bash_argv(path)
    if argv is None:
        return None, "", False
    try:
        p = subprocess.run(argv, capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None, "", False
    out = (p.stdout + p.stderr).decode("utf-8", "replace")
    return p.returncode, out, os.path.exists(flag)


def main():
    if not os.path.isfile(REAL):
        print("  %s is not there" % REAL)
        print("Nothing has been checked.")
        return 2
    with open(REAL, encoding="utf-8") as fh:
        real = fh.read()
    stubbed, n = SUITE_LINE.subn('bash "$ROOT/stub.sh" >"$OUT" 2>&1', real)
    if n != 1:
        # Refusal, not a failure: nothing ran. A non-match would mean either
        # testing a script whose suite line is intact -- launching the real
        # 16-minute suite from inside a test -- or silently testing nothing.
        print("  the suite-launch line in verify.sh matched %d times, not 1" % n)
        print("  This file substitutes exactly that line for a stub, and "
              "cannot run safely without it.")
        print("Nothing has been checked.")
        return 2

    # PROVE THE PLUMBING WORKS BEFORE BELIEVING ANY ANSWER -- AND PROVE IT
    # WITHOUT TOUCHING THE SUBJECT.
    #
    # The row's first suite run had no bash: every sandbox returned 127 and the
    # output was not an obvious refusal but six FAILs and four PASSes, because
    # four checks assert that something did NOT happen and nothing happening is
    # what a dead harness produces.
    #
    # The first fix for that was worse than the bug. It preflighted by running
    # a known-good case THROUGH verify.sh and requiring that the suite ran --
    # which is the very property these checks exist to measure. Restoring the
    # old no-op then made the harness look broken instead of making the gate
    # look broken: mutation-tested, 7 red checks became 0 and a refusal. A
    # health check that shares a signal with its subject cannot tell "my tools
    # are broken" from "the thing I am testing is broken", and it fails toward
    # the one that stops you looking.
    #
    # So the preflight runs the STUB DIRECTLY, never verify.sh. It establishes
    # only: bash is reachable, a stub executes, and its side effect is visible
    # to this process. Nothing about milestones, selectors or exit codes.
    with tempfile.TemporaryDirectory() as tmp:
        probe = os.path.join(tmp, "probe")
        os.makedirs(probe)
        sh = os.path.join(probe, "stub.sh")
        with open(sh, "w", encoding="utf-8", newline="\n") as fh:
            fh.write('FLAG="$(dirname "$0")/suite_ran"\n' + GREEN + "\n")
        argv = bash_argv(sh)
        ok = False
        if argv is not None:
            try:
                r = subprocess.run(argv, capture_output=True, timeout=60)
                ok = (r.returncode == 0
                      and os.path.exists(os.path.join(probe, "suite_ran"))
                      and b"SUMMARY" in r.stdout)
            except (OSError, subprocess.SubprocessError):
                ok = False
        if not ok:
            print("  bash is not reachable, or a stub does not execute here.")
            print("    argv: %s" % (argv or "no bash"))
            print("  Every check below would report about a script that never "
                  "ran, and four of them")
            print("  would report PASS for it. Refusing instead.")
            print("Nothing has been checked.")
            return 2

    print("\n[1] an empty milestone selector still runs the suite")
    with tempfile.TemporaryDirectory() as tmp:
        rc, out, ran = sandbox(tmp, stubbed, ALL_TICKED, True, GREEN)
        check(ran, "every milestone ticked: the suite RAN")
        check(rc == 0, "...and a green suite passes the gate", "exit %d" % rc)
        check("no milestone outstanding" in out,
              "...and the verdict says which question it answered")

        rc, out, ran = sandbox(tmp, stubbed, ALL_TICKED, True, RED)
        check(ran and rc == 1, "every milestone ticked + RED suite: FAILS",
              "exit %d, suite ran=%s" % (rc, ran))

        rc, out, ran = sandbox(tmp, stubbed, ALL_TICKED, True, TRUNCATED)
        check(ran and rc == 1, "every milestone ticked + truncated: FAILS",
              "exit %d" % rc)
        check("truncated" in out, "...and says it was truncated, not clean")

    print("\n[2] the old no-op is unreachable")
    with tempfile.TemporaryDirectory() as tmp:
        rc, out, ran = sandbox(tmp, stubbed, ALL_TICKED, True, RED)
        check(not (rc == 0 and not ran),
              "there is no path that exits 0 without running the suite")
        check("every milestone is landed" not in out,
              "the twenty-second 'every milestone is landed' exit is gone")

    print("\n[3] a real milestone still gates on registration")
    with tempfile.TemporaryDirectory() as tmp:
        rc, out, ran = sandbox(tmp, stubbed, ONE_OPEN, True, GREEN)
        check(rc == 0 and ran, "M21 open, registered, green: passes",
              "exit %d" % rc)
        check("M21 acceptance test registered" in out,
              "...and names the milestone it gated on")

        rc, out, ran = sandbox(tmp, stubbed, ONE_OPEN, False, GREEN)
        check(rc == 1, "M21 open but NOT registered: fails", "exit %d" % rc)
        check(not ran, "...before spending sixteen minutes on the suite")

    print("\n[4] REQ-062: game data in a GREEN log is not a truncation")
    with tempfile.TemporaryDirectory() as tmp:
        rc, out, ran = sandbox(tmp, stubbed, ALL_TICKED, True, BINARY_GREEN)
        check(rc == 0, "a clean log carrying cp1252 bytes still passes",
              "exit %d" % rc)
        check("truncated" not in out,
              "...and is not reported as a truncation")
        print("      (this case does NOT hold verify.sh's -a: removing it "
              "leaves this green here.")
        print("       See the note above BINARY_GREEN -- system grep is "
              "ugrep and its binary")
        print("       detection is context-dependent. The -a stays on "
              "REQ-062's measurement,")
        print("       not on this check.)")

    print()
    if FAILED:
        print("FAILED %d check(s): %s" % (len(FAILED), "; ".join(FAILED)))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
