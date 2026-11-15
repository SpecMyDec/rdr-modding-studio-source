#!/usr/bin/env python3
"""Export all animations: batch-export a subset, read every file back.

The failure this guards against is the one bvh.py was hardened for: a BVH that
Blender opens happily and that is missing bones, or a batch that "succeeds" and
writes nothing. So every check reads the FILES back.

Part 1, offline, on a read-only handle to the stock disc (no Studio, no ISO
writes): a subset of clips across all three codecs (cm2, cmp, raw) plus one
clip that does not exist.
  * every ok row has a file at <archive>/<skeleton>/<clip>.bvh, and the files on
    disk are exactly the ok rows
  * each file parses back (bvh.parse_bvh): frames == the clip's own key count,
    joints == the index row, row width == 3 + 3*joints, and the joint NAMES are
    the first `joints` bone names of the skeleton the row names
  * a rig shorter than the clip is only used when the row says so (note), and
    joints are never fewer than the rig it names
  * the failed clip is a row with a reason, not a silent drop; index.csv has one
    row per clip asked for
  * a cancel after 3 clips leaves a coherent folder: 3 rows, cancelled=True

Part 2, live, against the Studio on 8731:
  * GET anims_export_info lists archives with clip counts
  * POST anims_export on the smallest archive: a dry run writes nothing, the
    real one runs on a thread, status reaches done, files + index.csv exist and
    the count in status matches the files
  * /api/anim_bvh for one of those clips returns the very same bytes as the
    batch wrote (one shared code path)
  * a big export (SWAT.DAT) cancelled through job_cancel ends 'cancelled' with
    fewer clips than asked

Exit 0 all passed, 1 a check failed.
"""

import csv
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rdrstudio import Game                       # noqa: E402
from rdrstudio import config as cfg              # noqa: E402
from rdrstudio import animbatch as AB            # noqa: E402
from rdrstudio import bvh as BVH                 # noqa: E402
from rdrstudio import skeleton as SK             # noqa: E402
from rdrstudio import studio as ST               # noqa: E402

BASE = "http://127.0.0.1:8731/api/"
FAILS = []


def check(ok, what):
    print("  %s %s" % ("ok  " if ok else "FAIL", what))
    if not ok:
        FAILS.append(what)
    return ok


class Probe(ST.Studio):
    """The real Studio's rig logic over a read-only stock disc."""

    def __init__(self, path):
        import threading
        self.lock = threading.RLock()
        self.game = Game(path, writable=False)
        self.vanilla = self.game
        self.log = []
        self.job = None

    @property
    def disc(self):
        return self.game

    def ensure_open(self, force=False):
        pass


def codec(raw):
    return ("cmp" if raw[:4] == b"cmp\x00"
            else "raw" if raw[:4] == b"\x00\x00\x00\x00" else "cm2")


def pick_subset(s, per=6):
    """First `per` skeletal clips of each codec in SWAT.DAT, plus 1 elsewhere."""
    got = {"cm2": [], "cmp": [], "raw": []}
    arc = "SWAT.DAT"
    for e in s.disc.toc(arc):
        if not e.name.lower().endswith(".anim"):
            continue
        raw = s.disc.read(arc, e.name)
        k = codec(raw)
        if len(got[k]) >= per:
            continue
        try:
            a = AB.decode_clip(raw, e.name)
        except Exception:
            continue
        if a.channels == 3 + 3 * ((a.channels - 3) // 3) and a.channels > 3:
            got[k].append((arc, e.name))
        if all(len(v) >= per for v in got.values()):
            break
    return got


def part1(tmp):
    print("part 1: offline, read-only stock disc")
    s = Probe(cfg.VANILLA_ISO)
    got = pick_subset(s)
    for k, v in got.items():
        check(len(v) >= 3, "found %d %s clips to export" % (len(v), k))
    clips = [c for v in got.values() for c in v]
    ghost = ("SWAT.DAT", "no_such_clip_xyz.anim")
    asked = clips + [ghost]
    out = os.path.join(tmp, "anims")
    seen = []
    summ = AB.export_all(s, out, clips=asked,
                         progress=lambda d, t, l: seen.append((d, t)))
    check(summ["total"] == len(asked) and summ["exported"] + summ["failed"]
          == len(asked), "every clip asked for is counted (%d)" % len(asked))
    check(seen and seen[-1] == (len(asked), len(asked)),
          "progress reaches %d/%d" % (len(asked), len(asked)))
    check(summ["failed"] >= 1 and any(f["clip"] == ghost[1]
                                      for f in summ["failures"]),
          "the missing clip is reported as a failure, not dropped")
    rows = list(csv.DictReader(open(os.path.join(out, "index.csv"),
                                    encoding="utf-8")))
    check([r["clip"] for r in rows] == [c[1] for c in asked],
          "index.csv has one row per clip, in order")
    check(all(r["note"] for r in rows if r["status"] == "failed"),
          "every failed row carries a reason")
    okrows = [r for r in rows if r["status"] == "ok"]
    check(len(okrows) >= 9, "at least 9 clips exported (got %d)" % len(okrows))

    on_disk = set()
    for dp, _d, fs in os.walk(out):
        for f in fs:
            if f.endswith(".bvh"):
                on_disk.add(os.path.relpath(os.path.join(dp, f), out)
                            .replace(os.sep, "/"))
    check(on_disk == set(r["file"] for r in okrows),
          "the .bvh files on disk are exactly the ok rows (%d)" % len(on_disk))

    bad = []
    for r in okrows:
        arc, name = r["archive"], r["clip"]
        parts = r["file"].split("/")
        if len(parts) != 3 or parts[0] != AB.arc_folder(arc) \
                or parts[1] != AB.skel_folder(r["skeleton"]):
            bad.append("layout %s" % r["file"])
            continue
        raw = s.disc.read(arc, name)
        a = AB.decode_clip(raw, name)
        m = BVH.parse_bvh(open(os.path.join(out, *parts),
                               encoding="utf-8").read())
        want = (a.channels - 3) // 3
        if m.frames != a.keys or str(m.frames) != r["frames"]:
            bad.append("%s frames %s != %s" % (name, m.frames, a.keys))
        if len(m.joints) != int(r["joints"]) or m.width != 3 + 3 * len(m.joints):
            bad.append("%s joints %d / width %d vs row %s"
                       % (name, len(m.joints), m.width, r["joints"]))
        if len(m.rows) != a.keys:
            bad.append("%s rows %d" % (name, len(m.rows)))
        if r["skeleton"].endswith(".skel"):
            names = [b.name for b in SK.parse(s.disc.read(arc, r["skeleton"]))[2]]
            if m.joints != names[:len(m.joints)]:
                bad.append("%s joint names differ from %s" % (name, r["skeleton"]))
        if len(m.joints) < want and not r["note"]:
            bad.append("%s dropped %d bone(s) with no note"
                       % (name, want - len(m.joints)))
    check(not bad, "every file parses back: frames, joints, width, names%s"
          % ("" if not bad else " -- " + "; ".join(bad[:4])))

    # Cancel: stop after 3 clips, folder + index still coherent.
    out2 = os.path.join(tmp, "anims_cancel")
    n = []
    summ2 = AB.export_all(s, out2, clips=asked, cancel=lambda: len(n) >= 3,
                          progress=lambda d, t, l: n.append(d))
    rows2 = list(csv.DictReader(open(os.path.join(out2, "index.csv"),
                                     encoding="utf-8")))
    check(summ2["cancelled"] and len(rows2) == 3,
          "cancel after 3 clips: cancelled, 3 rows in index.csv")
    s.game.close()


# ------------------------------------------------------------------ part 2

def api(route, body=None, raw=False):
    if body is None:
        r = urllib.request.urlopen(BASE + route, timeout=60)
    else:
        req = urllib.request.Request(BASE + route, json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
        r = urllib.request.urlopen(req, timeout=60)
    data = r.read()
    return data if raw else json.loads(data)


def wait(state_in, timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = api("anims_export_status")
        if st.get("state") in state_in:
            return st
        time.sleep(0.4)
    return api("anims_export_status")


def part2():
    print("part 2: live Studio on 8731")
    info = api("anims_export_info")
    check(info.get("ok") and info["total"] > 1000 and info["archives"],
          "anims_export_info lists archives (%s clips)" % info.get("total"))
    small = min(info["archives"], key=lambda a: a["clips"])
    big = max(info["archives"], key=lambda a: a["clips"])
    folder = "_test_anims_%d" % os.getpid()
    root = os.path.join(cfg.EXPORTS, "animations")
    out = os.path.join(root, folder)
    try:
        body = dict(folder=folder, archives=[small["archive"]])
        dry = api("anims_export", dict(body, dry=True))
        check(dry.get("ok") and dry["clips"] == small["clips"]
              and not os.path.exists(out),
              "dry run counts %d clip(s) and writes nothing" % small["clips"])
        r = api("anims_export", dict(body, dry=False))
        check(r.get("ok") and r.get("started"), "export starts on a thread")
        st = wait(("done", "cancelled", "error"))
        check(st["state"] == "done" and st["done"] == small["clips"],
              "status reaches done at %d/%d" % (st.get("done", -1),
                                                 small["clips"]))
        files = [os.path.join(dp, f) for dp, _d, fs in os.walk(out)
                 for f in fs if f.endswith(".bvh")]
        summ = st["summary"]
        check(len(files) == summ["exported"] and summ["exported"] > 0,
              "%d .bvh written == %d reported" % (len(files), summ["exported"]))
        rows = list(csv.DictReader(open(os.path.join(out, "index.csv"),
                                        encoding="utf-8")))
        check(len(rows) == small["clips"], "index.csv has all %d rows"
              % small["clips"])
        ok = next((x for x in rows if x["status"] == "ok"), None)
        if ok:
            q = urllib.parse.urlencode(dict(archive=ok["archive"],
                                            name=ok["clip"],
                                            skeleton=ok["skeleton"],
                                            euler="YZX"))
            one = api("anim_bvh?" + q, raw=True)
            mine = open(os.path.join(out, *ok["file"].split("/")), "rb").read()
            check(one == mine, "single-clip route returns the same bytes as "
                  "the batch (%s)" % ok["clip"])
        again = api("anims_export", dict(body, dry=False))
        check(not again.get("ok"), "re-using a non-empty folder is refused")

        # Cancel mid-run on the biggest archive.
        folder2 = folder + "_c"
        out2 = os.path.join(root, folder2)
        r = api("anims_export", dict(folder=folder2, dry=False,
                                     archives=[big["archive"]]))
        time.sleep(1.5)
        api("job_cancel", {})
        st = wait(("done", "cancelled", "error"), 120)
        check(st["state"] == "cancelled" and st["done"] < big["clips"],
              "cancel stops a %d-clip export early (%s of %s)"
              % (big["clips"], st.get("done"), st.get("total")))
        shutil.rmtree(out2, ignore_errors=True)
    finally:
        shutil.rmtree(out, ignore_errors=True)
        shutil.rmtree(out + "_c", ignore_errors=True)


def main():
    tmp = tempfile.mkdtemp(prefix="_animexp_test_",
                           dir=os.path.join(cfg.ROOT, "02_Modded"))
    try:
        part1(tmp)
        part2()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for f in FAILS:
        print("  FAIL " + f)
    if FAILS:
        return 1
    print("  OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
