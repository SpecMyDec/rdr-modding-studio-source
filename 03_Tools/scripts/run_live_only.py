"""Re-run only the live (server-driven) checks.

The full suite's live half talks to the Studio on port 8731, and a run is only
worth trusting if nothing else was touching that server at the time. This
re-runs just those checks so a result can be quoted cleanly after a session
that involved poking the running tool by hand.

Assumes the server is already up on 8731 and that nothing else is using it.
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from run_tests import LIVE, PY                       # noqa: E402


def main():
    results = []
    for label, cmd in LIVE:
        print("=" * 70)
        print(">> %s" % label)
        print("=" * 70)
        t0 = time.time()
        r = subprocess.run([PY] + cmd, cwd=HERE)
        dt = time.time() - t0
        ok = r.returncode == 0
        results.append((label, ok))
        print("-- %s (%.1fs)\n" % ("PASS" if ok else "FAIL", dt))

    print("=" * 70)
    print("LIVE-ONLY SUMMARY")
    print("=" * 70)
    for label, ok in results:
        print("  [%s] %s" % ("PASS" if ok else "FAIL", label))
    bad = [l for l, ok in results if not ok]
    print("\n%d/%d passed" % (len(results) - len(bad), len(results)))
    for l in bad:
        print("FAILED: %s" % l)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
