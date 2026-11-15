#!/usr/bin/env python3
"""run_tests.py must not drive a Studio older than the source it serves.

Python reads a module once, at process start, so a Studio launched before an
edit keeps answering with the code it read then. Reusing one unconditionally
-- which run_tests.py did until this test was written -- reports PASS or FAIL
about source that is not on disc, in either direction. That cost a full test
cycle once already (PROGRESS-rdr-app, "Observations").

Two halves:

  1. The detector, against a throwaway HTTP server on a spare port. It never
     touches port 8731, the real Studio, or either ISO, so it is safe to run
     while another lane holds the suite lock. Both directions are checked:
     a listener NEWER than the source must come back "fresh", or the guard has
     only ever been watched to say no.

  2. The decision table around the kill, with the probes patched out. The kill
     must not fire while another suite is live, and must not fire on an
     undetermined answer either -- `suite-run-hygiene` records the afternoon a
     script printed `suites: 2` and killed anyway.
"""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import run_tests as rt                                          # noqa: E402

# 8799 refused to bind on the Windows box this was written on (WinError 10013)
# though netsh listed no exclusion, so take the first port that works.
CANDIDATE_PORTS = (8812, 8799, 8843, 8877)

SERVER = (
    "import sys\n"
    "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
    "class H(BaseHTTPRequestHandler):\n"
    "    def do_GET(self):\n"
    "        self.send_response(200)\n"
    "        self.send_header('Content-Type', 'application/json')\n"
    "        self.end_headers()\n"
    "        self.wfile.write(b'{}')\n"
    "    def log_message(self, *a): pass\n"
    "HTTPServer(('127.0.0.1', int(sys.argv[1])), H).serve_forever()\n"
)

fails = []


def check(name, got, want):
    ok = got == want
    print("  %-38s -> %-8s %s"
          % (name, got, "ok" if ok else "WRONG, wanted %s" % (want,)))
    if not ok:
        fails.append(name)


def start_fake_studio():
    """(Popen, port) for a throwaway server, or (None, None)."""
    for port in CANDIDATE_PORTS:
        if rt.studio_up(port):
            continue
        p = subprocess.Popen([sys.executable, "-c", SERVER, str(port)],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        for _ in range(20):
            time.sleep(0.5)
            if rt.studio_up(port):
                return p, port
            if p.poll() is not None:
                break
        if p.poll() is None:
            p.kill()
            p.wait()
    return None, None


def detector():
    print("the detector, against a real listener:")
    fd, tmp = tempfile.mkstemp(suffix=".py")
    os.close(fd)
    src = [tmp]

    p, port = start_fake_studio()
    if p is None:
        fails.append("no spare port would bind")
        print("  could not start a fake studio on any of %s" % (CANDIDATE_PORTS,))
        os.unlink(tmp)
        return
    try:
        check("listening_pid finds the server", rt.listening_pid(port), p.pid)
        started = rt.proc_start(rt.listening_pid(port))
        if started is None:
            fails.append("proc_start")
            print("  proc_start returned None -- the rest cannot run")
            return
        drift = time.time() - started
        check("start time is recent, not epoch 0", 0 <= drift < 120, True)

        # The ALLOW direction. A guard only ever watched to refuse is untested.
        os.utime(tmp, (started - 60, started - 60))
        check("source older than listener", rt.studio_freshness(port, src).state,
              "fresh")

        # The REFUSE direction.
        os.utime(tmp, (started + 60, started + 60))
        check("source newer than listener", rt.studio_freshness(port, src).state,
              "stale")

        # Exact, with no grace margin: a margin would be slack in the unsafe
        # direction, and one second of it is a whole edit.
        os.utime(tmp, (started + 1, started + 1))
        check("source newer by one second", rt.studio_freshness(port, src).state,
              "stale")

        # Undetermined must never read as "fresh" -- that is the whole point.
        os.utime(tmp, (started - 60, started - 60))
        keep = rt.listening_pid
        rt.listening_pid = lambda port=None: None
        check("port owner undeterminable", rt.studio_freshness(port, src).state,
              "unknown")
        rt.listening_pid = keep

        keep = rt.proc_start
        rt.proc_start = lambda pid: None
        check("start time undeterminable", rt.studio_freshness(port, src).state,
              "unknown")
        rt.proc_start = keep

        check("no sources to compare against",
              rt.studio_freshness(port, []).state, "unknown")
    finally:
        p.kill()
        p.wait()
        os.unlink(tmp)

    check("nothing listening", rt.studio_freshness(port, src).state, "none")


def matcher():
    print("\nthe other-suite matcher:")
    others = rt.other_suite_pids()
    print("  other_suite_pids() -> %s" % (others,))
    if others is None:
        print("  undeterminable here; the kill path fails closed on that")
        return
    # Every -f style match can match the process doing the matching: the
    # PowerShell query carries "run_tests.py" in its own command line, and an
    # earlier version of this reported itself.
    check("does not report itself", os.getpid() in others, False)
    check("returns plain ints", all(isinstance(p, int) for p in others), True)


def decisions():
    """The table around the kill, with every probe patched out."""
    print("\nthe decision table:")
    keep = (rt.studio_freshness, rt.other_suite_pids, rt.kill_studio)
    killed = []

    def case(name, state, mode, others, pid=4242):
        del killed[:]
        rt.studio_freshness = lambda *a, **k: rt.Freshness(
            state, pid, 1.0, "x.py", 2.0, "%s, for the test" % state)
        rt.other_suite_pids = lambda: others
        rt.kill_studio = lambda p: killed.append(p)
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rt.resolve_studio(mode)
            code = 0
        except SystemExit as ex:
            code = ex.code
        return name, code, list(killed), buf.getvalue()

    try:
        n, code, k, out = case("fresh + default", "fresh", "refuse", [])
        check(n + ": runs", (code, k), (0, []))

        n, code, k, out = case("none + default", "none", "refuse", [])
        check(n + ": runs", (code, k), (0, []))

        n, code, k, out = case("stale + default", "stale", "refuse", [])
        check(n + ": refuses", (code, k), (2, []))
        check(n + ": says STUDIO-STALE", "STUDIO-STALE" in out, True)
        check(n + ": says nothing ran", "No test has been run" in out, True)

        n, code, k, out = case("unknown + default", "unknown", "refuse", [])
        check(n + ": refuses", (code, k), (2, []))

        n, code, k, out = case("stale + --reuse-studio", "stale", "reuse", [])
        check(n + ": runs, kills nothing", (code, k), (0, []))
        check(n + ": warns", "NOT known to be current" in out, True)

        n, code, k, out = case("stale + --restart, alone", "stale", "restart", [])
        check(n + ": kills the listener", (code, k), (0, [4242]))

        n, code, k, out = case("stale + --restart, suite live",
                               "stale", "restart", [999])
        check(n + ": refuses, kills nothing", (code, k), (2, []))
        check(n + ": names the other suite", "999" in out, True)

        n, code, k, out = case("stale + --restart, can't tell",
                               "stale", "restart", None)
        check(n + ": refuses, kills nothing", (code, k), (2, []))

        n, code, k, out = case("stale + --restart, pid unknown",
                               "stale", "restart", [], pid=None)
        check(n + ": refuses, kills nothing", (code, k), (2, []))

        n, code, k, out = case("unknown + --restart, alone",
                               "unknown", "restart", [])
        check(n + ": kills the listener", (code, k), (0, [4242]))
    finally:
        rt.studio_freshness, rt.other_suite_pids, rt.kill_studio = keep


def main():
    print("platform %s, interpreter %s" % (os.name, sys.executable))
    detector()
    matcher()
    decisions()
    print("\n%s (%d of the checks above failed)"
          % ("FAIL" if fails else "PASS", len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
