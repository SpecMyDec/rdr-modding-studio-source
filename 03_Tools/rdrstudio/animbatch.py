"""Export EVERY animation clip on the disc to BVH, in one go.

The per-clip path (`/api/anim_bvh`) decodes one clip, asks `Studio.rigs_for_clip`
which skeletons can drive it, picks the clip's own character first and writes
it with `bvh.to_bvh`. This module is that same path in a loop, and it is the
ONE copy of it: the route calls `clip_to_bvh` too, so the single-clip button
and the batch cannot disagree about which rig a clip gets.

What a batch must not do is the failure `to_bvh` was hardened against: a file
that opens in Blender and is missing bones. So nothing is repaired or guessed
here. A clip either exports against a rig `rigs_for_clip` vouches for (exact
length, a longer rig whose names match, or a shorter rig whose NAMES match the
clip over the bones they share), or it is listed as failed with the reason.
The index records the skeleton, frame count and joint count of every file.

Layout under the output folder:

    <archive>/<skeleton>/<clip>.bvh
    index.csv        one row per clip: archive, clip, skeleton, file, frames,
                     joints, channels, status, note
    README.txt       what this is and how to open it
"""

import csv
import os
import re
import time


class ClipRefused(ValueError):
    """A clip that will not export, with the reason in plain words."""

    def __init__(self, msg, candidates=None):
        ValueError.__init__(self, msg)
        self.candidates = candidates or []


class Cancelled(Exception):
    """The caller asked the batch to stop."""


def decode_clip(raw, name):
    """Bytes -> an Anim-like object, for all three codecs."""
    from . import anim as A
    if raw[:4] == b"cmp\x00":
        from . import animcmp as CM
        return CM.parse(raw)
    if raw[:4] == b"\x00\x00\x00\x00":
        from . import animraw as RW
        a = RW.parse(raw)
        if not a.skeletal:
            raise ClipRefused(
                "%s has %d channels, which is not 3 + 3*bones -- it is a "
                "camera or face track, not a skeleton, so there is no rig to "
                "write a BVH against" % (name, a.channels))
        return a
    return A.parse(raw)


def clip_to_bvh(s, arc, name, skeleton=None, euler="YZX", a=None):
    """One clip as BVH text. Returns dict(text, skeleton, note, frames,
    joints, channels). Raises ClipRefused / ValueError with the reason.

    `s` is a Studio (needs .disc and .rigs_for_clip). Pass `a` to skip the
    decode when the caller already holds the parsed clip.
    """
    from . import bvh as BVH
    from .studio import rank_skeletons
    if a is None:
        try:
            a = decode_clip(s.disc.read(arc, name), name)
        except ClipRefused:
            raise
        except Exception as ex:                            # noqa: BLE001
            raise ClipRefused(str(ex))
    want = (a.channels - 3) // 3
    if a.channels != 3 + 3 * want:
        raise ClipRefused(
            "%d channels is not 3 + 3*bones, so this clip does not fit the "
            "translation-plus-rotations layout" % a.channels)
    # ONE rule, shared with the Animation tab's picker (Studio.rigs_for_clip).
    rigs = s.rigs_for_clip(arc, want, name)
    # Only writable rigs are export candidates; a forest rig plays fine but
    # cannot go on paper (BVH has one ROOT).
    cands = [(lbl, bones) for lbl, bones, _n, w in rigs if w]
    notes = {lbl: n for lbl, _b, n, _w in rigs if n}
    if not cands:
        raise ClipRefused(
            "no skeleton in %s has %d bones, which is what a %d-channel clip "
            "needs" % (arc, want, a.channels))
    if skeleton:
        pick = next((c for c in cands if c[0].lower() == skeleton.lower()),
                    None)
        if pick is None:
            plays = dict((lbl, b) for lbl, b, _n, _w in rigs)
            if skeleton in plays:
                why = BVH.writable_rig(plays[skeleton])[1]
            else:
                why = ("%s cannot drive this clip: it is not one of the %d "
                       "rigs whose bone names line up with it."
                       % (skeleton, len(plays)))
            raise ClipRefused(why, [c[0] for c in cands])
    else:
        # The clip's own character first, not whatever sorted first.
        order = rank_skeletons([c[0] for c in cands], name)
        by_name = {c[0]: c for c in cands}
        pick = by_name[order[0]]
    # allow_short: `cands` only holds a shorter rig when its names match.
    text = BVH.to_bvh(a, pick[1], euler=euler,
                      name="%s :: %s" % (arc, name), allow_short=True)
    return dict(text=text, skeleton=pick[0], note=notes.get(pick[0], ""),
                frames=a.keys, joints=len(pick[1]), channels=a.channels)


# ------------------------------------------------------------------ the batch

CLIP_EXTS = (".anim",)


def list_clips(s, archives=None):
    """[(archive, clip_name)] for every clip on the disc, in disc order.

    A clip is any `.anim` entry, in every archive -- not just SWAT.DAT.
    `archives` limits it to those archive paths (case-insensitive).
    """
    want = None if not archives else set(a.upper() for a in archives)
    out = []
    for arc in s.disc.archives():
        if want is not None and arc.upper() not in want:
            continue
        try:
            entries = s.disc.toc(arc)
        except Exception:                                  # noqa: BLE001
            continue
        for e in entries:
            if e.name.lower().endswith(CLIP_EXTS):
                out.append((arc, e.name))
    return out


def clip_counts(s):
    """{archive: number of clips}, only archives that have any."""
    counts = {}
    for arc, _n in list_clips(s):
        counts[arc] = counts.get(arc, 0) + 1
    return counts


_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_part(text, default="_"):
    t = _BAD.sub("_", str(text)).strip(" .")
    return t or default


def arc_folder(arc):
    return safe_part(arc.rsplit("/", 1)[-1].rsplit(".", 1)[0])


def skel_folder(label):
    # "srd.skel" -> srd ; "srd.skel :: skel_hair" (accessory) -> srd__skel_hair
    t = str(label).replace(" :: ", "__")
    if t.lower().endswith(".skel"):
        t = t[:-5]
    return safe_part(t)


INDEX_COLUMNS = ("archive", "clip", "skeleton", "file", "frames", "joints",
                 "channels", "status", "note")

README = """Animations exported from RDR Modding Studio.

Folders: <archive>/<skeleton>/<clip>.bvh
  The skeleton folder is the rig the clip was exported against (the clip's own
  character where the disc says so). Every joint of that rig is in the file.

index.csv lists every clip: archive, clip, skeleton, file, frames, joints,
channels, status (ok / failed) and a note. A failed clip has no file; its note
says why. Nothing is skipped silently.

Blender: File > Import > Motion Capture (.bvh), scale 1.0. Rotation order is
%(euler)s (the engine's own order is YZX; Blender calls it XZY).
"""


def export_all(s, outdir, archives=None, euler="YZX", progress=None,
               cancel=None, lock=None, clips=None):
    """Write every clip under `outdir`. Returns a summary dict.

    progress(done, total, label) is called after each clip; cancel() -> bool is
    polled between clips and raises Cancelled (after the index of what DID get
    written is saved, so a cancelled run is still a coherent folder).
    `lock` (a context manager, e.g. studio.lock) is held per clip so the rest
    of the app stays responsive during a long batch. `clips` ([(archive, name)])
    replaces the whole-disc listing -- for tests and "just these".
    """
    import contextlib
    lock = lock or contextlib.nullcontext()
    t0 = time.time()
    if clips is None:
        with lock:
            clips = list_clips(s, archives)
    total = len(clips)
    os.makedirs(outdir, exist_ok=True)
    rows, ok, failed = [], 0, 0
    cancelled = False
    by_arc = {}
    used = set()
    for i, (arc, name) in enumerate(clips):
        if cancel is not None and cancel():
            cancelled = True
            break
        row = dict(archive=arc, clip=name, skeleton="", file="", frames="",
                   joints="", channels="", status="failed", note="")
        try:
            with lock:
                r = clip_to_bvh(s, arc, name, euler=euler)
            rel = "%s/%s/%s.bvh" % (arc_folder(arc), skel_folder(r["skeleton"]),
                                    safe_part(name.rsplit(".", 1)[0]))
            if rel.lower() in used:
                raise ClipRefused("two clips map to the same file name (%s)"
                                  % rel)
            used.add(rel.lower())
            path = os.path.join(outdir, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(r["text"])
            row.update(skeleton=r["skeleton"], file=rel, frames=r["frames"],
                       joints=r["joints"], channels=r["channels"],
                       status="ok", note=r["note"])
            ok += 1
            by_arc.setdefault(arc, [0, 0])[0] += 1
        except Exception as ex:                            # noqa: BLE001
            row["note"] = str(ex) or type(ex).__name__
            failed += 1
            by_arc.setdefault(arc, [0, 0])[1] += 1
        rows.append(row)
        if progress is not None:
            progress(i + 1, total, "%s  %s" % (arc, name))
    index = os.path.join(outdir, "index.csv")
    with open(index, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=INDEX_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(outdir, "README.txt"), "w", encoding="utf-8") as f:
        f.write(README % dict(euler=euler))
    return dict(outdir=outdir, index=index, total=total, exported=ok,
                failed=failed, cancelled=cancelled,
                seconds=round(time.time() - t0, 1),
                archives=dict((a, dict(ok=v[0], failed=v[1]))
                              for a, v in sorted(by_arc.items())),
                failures=[dict(archive=r["archive"], clip=r["clip"],
                               why=r["note"]) for r in rows
                          if r["status"] == "failed"][:200])


# ------------------------------------------------------------------ the routes
#
# The batch runs on a thread so the page can poll for progress and cancel it.
# It takes the Studio lock once PER CLIP, not for the whole run, so the rest of
# the app keeps working; cancel rides the existing /api/job_cancel flag, and the
# batch shows up in /api/job like any other long job.

import threading                                           # noqa: E402

from .api import Refusal, ok as _ok, is_dry, plan as _plan  # noqa: E402


def _out_root():
    from .share import exports_dir
    return os.path.join(exports_dir(), "animations")


def get_anims_export_info(handler, s, q):
    with s.lock:
        s.ensure_open()
        counts = clip_counts(s)
    return _ok(archives=[dict(archive=a, clips=n)
                         for a, n in sorted(counts.items())],
               total=sum(counts.values()), folder=_out_root())


def get_anims_export_status(handler, s, q):
    st = getattr(s, "anim_export", None)
    if st is None:
        return _ok(state="idle")
    return _ok(**dict(st, seconds=round(
        (st.get("finished") or time.time()) - st["started"], 1)))


def post_anims_export(handler, s, body):
    st = getattr(s, "anim_export", None)
    if st is not None and st.get("state") == "running":
        raise Refusal("bad_request", "An animation export is already running.",
                      "Wait for it, or cancel it first.")
    archives = body.get("archives") or None
    if archives is not None and not isinstance(archives, list):
        raise Refusal("bad_request", "archives must be a list.", "")
    euler = str(body.get("euler") or "YZX").upper()
    from . import bvh as BVH
    if euler not in BVH.EULER_ORDERS:
        raise Refusal("unknown_value", "%s is not a rotation order." % euler,
                      "Choose one of: %s." % ", ".join(BVH.EULER_ORDERS))
    name = safe_part(body.get("folder") or "",
                     "animations_" + time.strftime("%Y%m%d-%H%M%S"))
    outdir = os.path.join(_out_root(), name)
    if os.path.exists(outdir) and os.listdir(outdir) and not body.get("replace"):
        raise Refusal("bad_request", "%s already exists and is not empty."
                      % name, "Pick another folder name.")
    with s.lock:
        s.ensure_open(force=True)
        clips = list_clips(s, archives)
    if not clips:
        raise Refusal("not_found", "No animation clips found%s."
                      % (" in those archives" if archives else ""), "")
    plan = _plan([], "Writes %d clip(s) as BVH into %s, a folder per archive "
                 "and skeleton, plus index.csv." % (len(clips), outdir),
                 "Exported %d animation clips (nothing on the disc changed)"
                 % len(clips), steps=0)
    if is_dry(body):
        return _ok(plan=plan, path=outdir, clips=len(clips))

    st = s.anim_export = dict(state="running", done=0, total=len(clips),
                              current="", started=time.time(), finished=None,
                              outdir=outdir, summary=None, error=None)
    s.job = dict(label="Exporting all animations", started=st["started"],
                 cancel=False)

    def progress(done, total, label):
        st.update(done=done, total=total, current=label)

    def cancel():
        j = s.job
        return bool(j is not None and j.get("cancel"))

    def run():
        try:
            summ = export_all(s, outdir, archives=archives, euler=euler,
                              progress=progress, cancel=cancel, lock=s.lock)
            st.update(state="cancelled" if summ["cancelled"] else "done",
                      summary=summ)
            s.note("exported %d animation clips to %s (%d failed%s)"
                   % (summ["exported"], outdir, summ["failed"],
                      ", cancelled" if summ["cancelled"] else ""))
        except Exception as ex:                            # noqa: BLE001
            import traceback
            traceback.print_exc()
            st.update(state="error", error="%s: %s" % (type(ex).__name__, ex))
        finally:
            st["finished"] = time.time()
            s.job = None

    threading.Thread(target=run, name="anims-export", daemon=True).start()
    return _ok(plan=plan, started=True, path=outdir, clips=len(clips))
