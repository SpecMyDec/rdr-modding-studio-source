#!/usr/bin/env python3
"""Put the app into the 'already set up' state, using the test working copy.

Only for testing the windowed launch without sitting through first run. A real
user never runs this -- the first-run window writes the same file.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from rdrshell import paths                                 # noqa: E402
from rdrstudio.config import VANILLA_ISO                   # noqa: E402

TD = os.path.join(HERE, "testdata")
s = paths.load()
s.update({
    "vanilla_iso": VANILLA_ISO,
    "working_iso": os.path.join(TD, "RDR_WORKING.iso"),
    "mods_dir": os.path.join(TD, "mods"),
    "checkpoints_dir": os.path.join(TD, "checkpoints"),
    "exports_dir": os.path.join(TD, "exports"),
    "accepted_disclaimer": True,
})
paths.ensure_dirs(s)
print(paths.save(s))
print("configured:", paths.configured(s))
