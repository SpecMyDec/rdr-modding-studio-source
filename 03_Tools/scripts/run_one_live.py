"""Start a Studio on 8731, run the given live test scripts, stop the Studio.
    py -3 scripts\run_one_live.py scripts\test_custom_collision_live.py [...]
(hold /tmp/rdr-suite.lock around it)"""
import os, subprocess, sys, time, urllib.request
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = os.environ.get("RDR_PORT") or "8731"
st = subprocess.Popen([sys.executable, "RDR_Studio.py", "--no-browser"], cwd=HERE,
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
rc = 1
try:
    for _ in range(60):
        time.sleep(1)
        if st.poll() is not None:
            print("Studio exited early", st.returncode); sys.exit(2)
        try:
            urllib.request.urlopen("http://127.0.0.1:%s/api/ping" % PORT, timeout=2); break
        except Exception:
            try:
                urllib.request.urlopen("http://127.0.0.1:%s/" % PORT, timeout=2); break
            except Exception:
                pass
    rc = 0
    for s in sys.argv[1:]:
        r = subprocess.run([sys.executable] + [s], cwd=HERE)
        print("-- %s -> %d" % (s, r.returncode)); rc = rc or r.returncode
finally:
    st.terminate()
    try: st.wait(timeout=20)
    except Exception: st.kill()
sys.exit(rc)
