"""Moved. Use build.py (or build_app.py one level up).

This script used to freeze, stamp and zip on its own. build.py now does all
three, plus the syntax gate, the manifest gate, the self-check and the window
check -- and two scripts that both produce "the download" is exactly how a
release gets cut from the one that skipped a gate.

    python build_app.py                 from 03_Tools -- the usual way
    python packaging/build.py --both    both editions, compared

Kept as a forwarder rather than deleted, because the old command is in a
README somewhere and failing with a sentence beats failing with a traceback.
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

if __name__ == "__main__":
    print(__doc__.strip())
    print("\nforwarding to build.py ...\n")
    args = [a for a in sys.argv[1:] if a not in ("--edition", "both")]
    sys.exit(subprocess.run([sys.executable,
                             os.path.join(HERE, "build.py")] + args,
                            cwd=HERE).returncode)
