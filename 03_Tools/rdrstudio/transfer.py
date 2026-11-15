"""One shape for every import and every export.

The same two acts wore six affordance styles, eight verbs for import and six
for export, and five different shapes of pre-commit summary -- two of which
reported no size and no slot at all. This module is the single door they all
go through now.

Two verbs, and only two. **Export** leaves the tool; **Import** enters the
game. The noun is the thing in the game and the format is a chip.

The important structural promise is that **check never writes**. It encodes,
it plans, it hands back a token, and it stops. `transfer_apply` then commits
the exact bytes `transfer_check` produced -- it never re-encodes and never
re-reads the payload, because a second encode is a second chance to produce
different bytes than the ones the user was shown and agreed to. That closes
the two importers that had no dry run at all by construction rather than by
remembering.

Export carries its own rule: the response is a file **if and only if**
`Content-Type` is not `application/json`. Four export sites used to build an
`<a download>` and click it blind, so a failed export saved the JSON error
body under a `.obj` name and reported success.
"""

import base64
import io
import zlib

from .api import Refusal, write_row

KINDS = ("texture", "still", "sound", "stream", "anim", "geometry",
         "character", "file", "mod")


def _b64(payload):
    if not payload:
        raise Refusal("bad_request", "No file was sent.",
                      "Choose a file and try again.")
    try:
        return base64.b64decode(payload)
    except Exception as ex:
        raise Refusal("bad_request", "That upload could not be read.",
                      "Choose the file again.", str(ex))


def _stored(entry, data):
    if getattr(entry, "compressed", False):
        co = zlib.compressobj(9, 8, -15)
        p = co.compress(data) + co.flush()
        return min(len(p), len(data))
    return len(data)


def _row(disc, archive, filename, data):
    e = disc.archive(archive).find(filename)
    before = e.size_full
    return write_row(archive, filename, before, len(data),
                     _stored(e, data), e.slot)


class Check:
    """What a checked transfer produced: bytes to write, and how to say so."""

    def __init__(self, subject, writes, rows, summary, label, options=None):
        self.subject = subject          # dict for region 1 of the sheet
        self.writes = writes            # [(archive, file, bytes)]
        self.rows = rows                # plan.writes rows
        self.summary = summary
        self.label = label
        self.options = options or []


# --------------------------------------------------------------------- import

def check(studio, kind, target, payload_b64, options):
    if kind not in KINDS:
        raise Refusal("bad_request", "%r is not a kind of transfer." % kind,
                      "Use one of: %s." % ", ".join(KINDS))
    fn = _CHECKERS.get(kind)
    if fn is None:
        raise Refusal(
            "not_in_this_build",
            "Importing a %s is not in this build yet." % kind,
            _WHY.get(kind, ""))
    return fn(studio, target or {}, payload_b64, options or {})


_WHY = {
    "stream": "The 8,361 streamed sounds decode but there is no .stm encoder "
              "yet. Export works today.",
    "character": "Adding a character needs the archive repacked, and no level "
                 "archive has ever been repacked successfully. It is in the "
                 "internal build, behind a checkpoint and a boot test.",
    "mod": "Applying a saved mod is a Version restore -- use My mod > "
           "Versions, which dry-runs every file first.",
}


def _check_file(studio, target, payload_b64, options):
    """The generic primitive: replace any file on the disc with these bytes."""
    archive = target.get("archive")
    name = target.get("name")
    if not archive or not name:
        raise Refusal("bad_request", "This needs an archive and a file name.",
                      "")
    data = _b64(payload_b64)
    disc = studio.disc
    try:
        row = _row(disc, archive, name, data)
    except Exception:
        raise Refusal("not_found", "There is no %s in %s." % (name, archive),
                      "Check the file name.")
    subject = dict(title=name, where="%s > %s" % (_short(archive), name),
                   format="%s bytes" % "{:,}".format(len(data)),
                   rung="measured")
    return Check(subject, [(archive, name, data)], [row],
                 "Replaces %s in %s" % (name, _short(archive)),
                 "%s: %s" % (_short(archive), name))


def _check_texture(studio, target, payload_b64, options):
    from . import texture as T
    archive = target.get("archive")
    area = target.get("area") or None
    index = int(target.get("i", 0))
    if not archive:
        raise Refusal("bad_request", "This needs an archive.", "")
    lvl = studio.level(archive, area=area)
    r = lvl._load_rsc()
    if r is None:
        raise Refusal("not_found", "%s has no resource bundle." % _short(archive),
                      "Pick a level that carries geometry.")
    table = r.texture_table()
    if not 0 <= index < len(table):
        raise Refusal("not_found",
                      "There is no surface %d here (this level has %d)."
                      % (index, len(table)), "")
    _, info, mip, pal = table[index]
    png = _b64(payload_b64)
    try:
        w, h, rgba = T.read_png(png)
        new_mip, new_pal = T.encode_into(r.raw(mip.index), r.raw(pal.index),
                                         rgba, w, h)
    except Exception as ex:
        raise Refusal("bad_request", "That picture could not be used here.",
                      "A surface keeps its own size and palette, so the "
                      "picture has to match what is already there.", str(ex))
    blob = bytearray(r.data)
    blob[mip.offset:mip.offset + mip.size] = new_mip
    blob[pal.offset:pal.offset + pal.size] = new_pal
    data = bytes(blob)
    row = _row(studio.disc, archive, lvl.rsc_name, data)
    subject = dict(title=info.get("name") or ("surface %d" % index),
                   where="%s > %s" % (_short(archive), lvl.rsc_name),
                   format="PNG %dx%d" % (w, h), rung="measured")
    return Check(subject, [(archive, lvl.rsc_name, data)], [row],
                 "Repaints one surface in %s" % _short(archive),
                 "%s: %s" % (_short(archive), info.get("name") or "surface"))


def _check_still(studio, target, payload_b64, options):
    """A full-screen picture: menu art, a loading screen, the boot logo.

    The budget is not the size of the `.ipu`. 60 of the 63 stills are stored
    deflate-compressed and on 36 of them the raw file is LARGER than the slot
    it lives in, so the test has to mirror what the archive actually does:
    compress, fall back to raw if compression grew it, compare to the slot --
    and against the SMALLEST slot among every archive holding the name, since
    a single encode has to fit the tightest or the write lands only partly.
    """
    from . import ipu as _ipu, ipuenc, ipudec
    want = target.get("name") or ""
    disc = studio.disc
    archives = disc.locate(want)
    if not archives:
        raise Refusal("not_found", "There is no screen called %r." % want, "")

    slots = {}
    for a in archives:
        e = disc.archive(a).find(want)
        slots[a] = (e.slot, e.compressed)
    budget = min(v[0] for v in slots.values())
    any_compressed = any(v[1] for v in slots.values())
    original = disc.read(archives[0], want)
    try:
        head = _ipu.parse(original)
    except _ipu.NotIpu as ex:
        raise Refusal("bad_request", "%s is not a picture file." % want, "",
                      str(ex))
    w, h = head["width"], head["height"]

    def stored_len(raw):
        if not any_compressed:
            return len(raw)
        co = zlib.compressobj(9, 8, -15)
        p = co.compress(raw) + co.flush()
        return min(len(p), len(raw))

    try:
        rgb = ipuenc.rgb_from_image(io.BytesIO(_b64(payload_b64)), w, h,
                                    fit=options.get("fit", "cover"))
    except Refusal:
        raise
    except Exception as ex:
        raise Refusal("bad_request", "That picture could not be read.",
                      "PNG, JPEG and BMP all work.", str(ex))

    floor = int(options.get("quality") or 1)
    chosen = None
    ladder = []
    for q in range(max(1, floor), 32):
        enc = ipuenc.encode(rgb, w, h, q_scale_code=q)
        st = stored_len(enc)
        ladder.append(dict(q=q, raw=len(enc), stored=st, fits=st <= budget))
        if st <= budget:
            chosen = (q, enc, st)
            break
    if chosen is None:
        raise Refusal(
            "slot_overflow",
            "Even at the coarsest quality this picture needs %s bytes of a "
            "%s-byte slot." % ("{:,}".format(ladder[-1]["stored"]),
                               "{:,}".format(budget)),
            "A simpler picture -- flatter colour, less fine detail -- "
            "compresses far better. The encode worked; it is the detail that "
            "does not fit.")
    q, enc, st = chosen

    # What we are about to write must decode back. Cheap, and it means a bad
    # encode is caught here rather than as a black screen in the game.
    try:
        dw, dh, _ = ipudec.decode(enc)
    except Exception as ex:
        raise Refusal("bad_request",
                      "The encoded picture does not read back.",
                      "That is an encoder bug, not a problem with your "
                      "picture.", str(ex))
    if (dw, dh) != (w, h):
        raise Refusal("bad_request",
                      "The encoded picture came back %dx%d instead of %dx%d."
                      % (dw, dh, w, h), "")

    writes, rows = [], []
    for a in archives:
        writes.append((a, want, enc))
        rows.append(_row(disc, a, want, enc))
    subject = dict(title=want, where="%d archive%s > %s"
                   % (len(archives), "" if len(archives) == 1 else "s", want),
                   format="picture %dx%d" % (w, h), rung="measured")
    opts = [dict(key="quality", label="Quality floor", control="int",
                 value=q, min=1, max=31, options=None),
            dict(key="fit", label="Fit", control="pick",
                 value=options.get("fit", "cover"), min=None, max=None,
                 options=["cover", "contain", "stretch"])]
    return Check(subject, writes, rows,
                 "Replaces %s in %d archive%s (quality %d)"
                 % (want, len(archives), "" if len(archives) == 1 else "s", q),
                 want, opts)


def _check_sound(studio, target, payload_b64, options):
    from . import vag
    archive = target.get("archive") or "BANKS.DAT"
    name = target.get("name")
    idx = int(target.get("index", 0))
    if not name:
        raise Refusal("bad_request", "This needs a bank name.", "")
    try:
        data = studio.disc.read(archive, name)
    except Exception:
        raise Refusal("not_found", "There is no %s in %s." % (name, archive), "")
    hd = None
    try:
        hd = studio.disc.read(archive, name[:-3] + ".hd")
    except Exception:
        pass
    snds = vag.sounds_with_rates(data, hd)
    if not 0 <= idx < len(snds):
        raise Refusal("not_found", "There is no sound %d in %s (it holds %d)."
                      % (idx, name, len(snds)), "")
    try:
        samples, in_rate = vag.from_wav(_b64(payload_b64))
    except vag.VagError as ex:
        raise Refusal("bad_request", str(ex), "Use a mono WAV.", "")
    enc = vag.encode(samples)
    slot = snds[idx]["blocks"] * vag.BLOCK
    if len(enc) > slot:
        raise Refusal(
            "slot_overflow",
            "This sound needs %s bytes of a %s-byte space (over by %s)."
            % ("{:,}".format(len(enc)), "{:,}".format(slot),
               "{:,}".format(len(enc) - slot)),
            "Shorten the clip. A bank is patched in place and the .hd holds "
            "absolute offsets, so nothing may grow.")

    out = bytearray(data)
    off = snds[idx]["offset"]
    out[off:off + len(enc)] = enc
    pad = off + len(enc)
    while pad + vag.BLOCK <= off + slot:
        blk = bytearray(vag.BLOCK)
        blk[1] = vag.FLAG_END
        out[pad:pad + vag.BLOCK] = blk
        pad += vag.BLOCK
    blob = bytes(out)
    row = _row(studio.disc, archive, name, blob)
    subject = dict(title="%s sound %d" % (name, idx + 1),
                   where="%s > %s" % (_short(archive), name),
                   format="WAV %d Hz, %s samples"
                          % (in_rate, "{:,}".format(len(samples))),
                   rung="measured")
    return Check(subject, [(archive, name, blob)], [row],
                 "Replaces one sound in %s" % name,
                 "%s: %s sound %d" % (_short(archive), name, idx + 1))


def _check_geometry(studio, target, payload_b64, options):
    from . import geomedit
    from .level import Level
    archive = target.get("archive")
    area = target.get("area") or None
    chunk = int(target.get("chunk", -1))
    if not archive:
        raise Refusal("bad_request", "This needs an archive.", "")
    lvl = studio.level(archive, area=area)
    stock = Level(studio.vanilla, archive, area=area)
    vr = stock._load_rsc()
    if vr is None or not (0 <= chunk < len(vr.chunks)) \
            or vr.chunks[chunk].tag != "MOD0":
        raise Refusal("not_found", "Surface %s is not a model here." % chunk, "")
    m = vr.model(chunk)
    if not m:
        raise Refusal("not_found", "Surface %d will not decode." % chunk, "")
    obj = _b64(payload_b64).decode("latin-1", "replace")
    try:
        verts = geomedit.from_obj(obj)
        out = geomedit.apply_obj(vr.raw(chunk), m, verts)
    except geomedit.NotEditable as ex:
        raise Refusal("not_editable", str(ex),
                      "Positions are matched by index, so the point count "
                      "must not change in the modelling program.")
    except Exception as ex:
        raise Refusal("bad_request", "That OBJ could not be used here.", "",
                      str(ex))
    live = lvl._load_rsc()
    cur = studio.disc.archive(archive).read(lvl.rsc_name)
    c = live.chunks[chunk]
    blob = bytearray(cur)
    blob[c.offset:c.offset + c.size] = out
    data = bytes(blob)
    moved = sum(1 for a, b in zip(verts, m["verts"])
                if any(abs(x - y) > 1e-6 for x, y in zip(a, b)))
    row = _row(studio.disc, archive, lvl.rsc_name, data)
    subject = dict(title=target.get("name") or ("surface %d" % chunk),
                   where="%s > %s" % (_short(archive), lvl.rsc_name),
                   format="OBJ, %s points" % "{:,}".format(len(verts)),
                   rung="established")
    return Check(subject, [(archive, lvl.rsc_name, data)], [row],
                 "Reshapes %d of %s points" % (moved, "{:,}".format(len(verts))),
                 "%s: surface %d" % (_short(archive), chunk))


def _check_anim(studio, target, payload_b64, options):
    from . import bvh as BVH, anim as A
    archive = target.get("archive")
    name = target.get("name")
    if not archive or not name:
        raise Refusal("bad_request", "This needs an archive and a clip name.",
                      "")
    try:
        cur = studio.disc.read(archive, name)
    except Exception:
        raise Refusal("not_found", "There is no %s in %s." % (name, archive), "")
    text = _b64(payload_b64).decode("latin-1", "replace")
    try:
        out, rep = BVH.bvh_to_anim(
            cur, text,
            bones=options.get("bones") or None,
            euler=options.get("euler") or None,
            tolerance=BVH.TEXT_ULP,
            rounding=options.get("rounding") or "nearest",
            allow_reshape=bool(options.get("allow_reshape")))
    except (BVH.ImportRefused, ValueError) as ex:
        raise Refusal("bad_request", str(ex), "")
    except A.AnimError as ex:
        # Not "is not a cm2 clip": import takes all three codecs now
        # (CAPABILITIES.md C-020), so naming cm2 as the requirement told a cmp
        # or uncompressed clip the one thing that is no longer why it failed.
        # rdr-fmt REQ-004(b); same wording as studio.py's anim_import.
        raise Refusal("bad_request",
                      "%s could not be read as an animation: %s" % (name, ex),
                      "")
    row = _row(studio.disc, archive, name, out)
    subject = dict(title=name, where="%s > %s" % (_short(archive), name),
                   format="BVH, %s of %s samples changed"
                          % ("{:,}".format(rep.get("changed_samples", 0)),
                             "{:,}".format(rep.get("total_samples", 0))),
                   rung="established")
    opts = [dict(key="euler", label="Rotation order", control="pick",
                 value=options.get("euler"), min=None, max=None,
                 options=["xyz", "xzy", "yxz", "yzx", "zxy", "zyx"]),
            dict(key="rounding", label="Rounding", control="pick",
                 value=options.get("rounding") or "nearest", min=None,
                 max=None, options=["nearest", "down", "up"])]
    return Check(subject, [(archive, name, out)], [row],
                 "Replaces the motion in %s" % name,
                 "%s: %s" % (_short(archive), name), opts)


_CHECKERS = {
    "file": _check_file,
    "texture": _check_texture,
    "still": _check_still,
    "sound": _check_sound,
    "geometry": _check_geometry,
    "anim": _check_anim,
}


# --------------------------------------------------------------------- export

def export(studio, kind, target, options):
    """(bytes, content_type, filename). Raises Refusal, which becomes JSON.

    The caller sends a file if and only if this returns without raising, and
    the front end trusts `Content-Type` rather than the fact that a response
    arrived at all.
    """
    fn = _EXPORTERS.get(kind)
    if fn is None:
        raise Refusal("not_in_this_build",
                      "There is no way to export a %s yet." % kind,
                      _WHY.get(kind, ""))
    return fn(studio, target or {}, options or {})


def _export_file(studio, target, options):
    archive, name = target.get("archive"), target.get("name")
    if not archive or not name:
        raise Refusal("bad_request", "This needs an archive and a file name.",
                      "")
    try:
        data = studio.disc.read(archive, name)
    except Exception:
        raise Refusal("not_found", "There is no %s in %s." % (name, archive), "")
    return data, "application/octet-stream", name


def _export_texture(studio, target, options):
    from . import texture as T
    archive = target.get("archive")
    lvl = studio.level(archive, area=target.get("area") or None)
    r = lvl._load_rsc()
    if r is None:
        raise Refusal("not_found", "%s has no resource bundle." % _short(archive),
                      "")
    table = r.texture_table()
    i = int(target.get("i", 0))
    if not 0 <= i < len(table):
        raise Refusal("not_found", "There is no surface %d here." % i, "")
    _, info, mip, pal = table[i]
    res = T.decode(r.raw(mip.index), T.parse_palette(r.raw(pal.index)))
    if not res:
        raise Refusal("not_editable", "Surface %d will not decode." % i,
                      "Its pixel format is not one the toolkit reads yet.")
    w, h, rgba = res
    stem = (info.get("name") or ("surface_%d" % i)).rsplit(".", 1)[0]
    return T.encode_png(w, h, rgba), "image/png", stem + ".png"


def _export_still(studio, target, options):
    from . import ipu
    want = target.get("name") or ""
    archives = studio.disc.locate(want)
    if not archives:
        raise Refusal("not_found", "There is no screen called %r." % want, "")
    data = studio.disc.read(archives[0], want)
    if not ipu.is_ipu(data):
        raise Refusal("bad_request", "%s is not a picture file." % want, "")
    return data, "application/octet-stream", want


def _export_stream(studio, target, options):
    """The 8,361 voice and music streams, as a WAV that actually saves.

    These have always decoded; the response set `Content-Disposition: inline`,
    so a browser played it and there was no way to keep one.
    """
    from . import streams as _st
    want = target.get("name") or ""
    try:
        wav = _st.wav_for(studio.disc, want)
    except Exception as ex:
        raise Refusal("not_found", "There is no sound called %r." % want, "",
                      str(ex))
    return wav, "audio/wav", want.rsplit(".", 1)[0] + ".wav"


def _export_sound(studio, target, options):
    from . import vag
    archive = target.get("archive") or "BANKS.DAT"
    name = target.get("name")
    idx = int(target.get("index", 0))
    try:
        data = studio.disc.read(archive, name)
    except Exception:
        raise Refusal("not_found", "There is no %s in %s." % (name, archive), "")
    hd = None
    try:
        hd = studio.disc.read(archive, name[:-3] + ".hd")
    except Exception:
        pass
    snds = vag.sounds_with_rates(data, hd)
    if not 0 <= idx < len(snds):
        raise Refusal("not_found", "There is no sound %d in %s." % (idx, name), "")
    s = snds[idx]
    blocks = data[s["offset"]:s["offset"] + s["blocks"] * vag.BLOCK]
    # The rate is not stored per sound in the bank -- the `.hd` carries the
    # pitch a program plays it at -- so it is an option, defaulting to what
    # the decoded lengths sound sensible at.
    rate = int(options.get("rate") or s.get("rate") or 22050)
    wav = vag.to_wav(vag.decode(blocks), rate)
    return wav, "audio/wav", "%s_%03d.wav" % (name.rsplit(".", 1)[0], idx)


def _export_geometry(studio, target, options):
    from . import geomedit
    from .level import Level
    archive = target.get("archive")
    chunk = int(target.get("chunk", -1))
    stock = Level(studio.vanilla, archive, area=target.get("area") or None)
    vr = stock._load_rsc()
    if vr is None or not (0 <= chunk < len(vr.chunks)):
        raise Refusal("not_found", "There is no surface %d here." % chunk, "")
    if vr.chunks[chunk].tag != "MOD0":
        raise Refusal("not_editable",
                      "Surface %d is a %s, not a model."
                      % (chunk, vr.chunks[chunk].tag), "")
    m = vr.model(chunk)
    if not m or not geomedit.editable(m):
        raise Refusal("not_editable",
                      "This surface cannot be exported for editing.",
                      "Its display list is not one the toolkit can rebuild.")
    name = target.get("name") or ("chunk_%d" % chunk)
    obj = geomedit.to_obj(m, name, dict(archive=stock.archive,
                                        area=stock.rsc_name or "", chunk=chunk))
    return obj.encode("latin-1"), "text/plain; charset=latin-1", name + ".obj"


def _export_anim(studio, target, options):
    """A clip as BVH, which Blender imports natively.

    Nothing in a `.anim` names its skeleton, so the bone COUNT is the only
    link and several skeletons usually share one. The candidates are offered
    rather than silently picked -- and the skeleton and Euler-order choices
    belong here, in the transfer sheet, not in the video transport bar at the
    bottom of the viewport where the Euler picker used to live.
    """
    from . import anim as A, bvh as BVH, skeleton as SK
    archive, name = target.get("archive"), target.get("name")
    if not archive or not name:
        raise Refusal("bad_request", "This needs an archive and a clip name.",
                      "")
    try:
        a = A.parse(studio.disc.read(archive, name))
    except Exception as ex:
        raise Refusal("not_found", "%s will not read as a clip." % name, "",
                      str(ex))
    want = (a.channels - 3) // 3
    cands = []
    for e in studio.disc.toc(archive):
        if not e.name.lower().endswith(".skel"):
            continue
        try:
            _v, _d, bones, _r = SK.parse(studio.disc.read(archive, e.name))
        except Exception:
            continue
        if len(bones) == want:
            cands.append((e.name, bones))
    if not cands:
        raise Refusal("not_editable",
                      "No skeleton in %s has %d bones, which is what this "
                      "%d-channel clip needs." % (_short(archive), want,
                                                  a.channels), "")
    skel = target.get("skeleton") or options.get("skeleton")
    pick = cands[0]
    if skel:
        pick = next((c for c in cands if c[0].lower() == str(skel).lower()),
                    None)
        if pick is None:
            raise Refusal("unknown_value",
                          "%s is not a %d-bone skeleton in %s."
                          % (skel, want, _short(archive)),
                          "Choose one of: %s."
                          % ", ".join(c[0] for c in cands))
    try:
        # allow_short: `cands` comes from skels_for_clip, which only offers a
        # shorter rig when its bone names match the clip's.
        text = BVH.to_bvh(a, pick[1],
                          euler=(options.get("euler") or "XYZ").upper(),
                          name="%s :: %s" % (archive, name),
                          allow_short=True)
    except ValueError as ex:
        raise Refusal("not_editable", str(ex), "")
    return (text.encode("utf-8"), "text/plain; charset=utf-8",
            name.rsplit(".", 1)[0] + ".bvh")


_EXPORTERS = {
    "file": _export_file,
    "texture": _export_texture,
    "still": _export_still,
    "stream": _export_stream,
    "sound": _export_sound,
    "geometry": _export_geometry,
    "anim": _export_anim,
}


def _short(archive):
    return archive.rsplit("/", 1)[-1].rsplit(".", 1)[0]
