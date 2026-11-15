"""The routes added by the 2026-08 redesign.

Kept in their own module for one reason: `studio.py` holds ~115 routes that
work, and the rule for this pass is that not one of them changes its name,
method, request or response. Two lines of dispatch in `studio.py` reach this
file; everything new lives here, so a mistake in new code cannot reach old
code by being in the same function.

Every route in here obeys the same three promises:

  * `dry` defaults to TRUE. Writing requires `"dry": false` in the body.
  * Every mutating route opens exactly ONE `journal.new_group()`, so
    `plan.undo.steps` is 1 and the user's Ctrl+Z matches the sentence the tool
    showed them.
  * Nothing is spliced until the slot has been checked. A refusal carries the
    byte counts; it is never a corruption and never a bare "failed".

Route registration is gated by `features.py`: a feature switched off is not
added to the table at all, so a call to it returns the same `no such route`
404 as a typo would. Not a disabled button, not a hidden div -- absent.
"""

from . import api
from .api import Refusal
from . import behaviour as beh_mod
from . import cast as cast_mod
from . import charfile as CF
from . import msd as MSD
from . import skeledit as SKE
from . import csvfile
from . import enemyfields as EF
from . import features
from . import links as links_mod
from . import sheets as sheets_mod
from . import transfer as transfer_mod
from . import versions as versions_mod
from . import share as share_mod
from . import modpack as modpack_mod
from . import animbatch as animbatch_mod

TOKENS = api.TokenStore()


def _short(archive):
    return archive.rsplit("/", 1)[-1].rsplit(".", 1)[0]


def _busy(ex):
    """Is this the emulator holding the working ISO rather than a bug?

    `Studio.write` and `Studio.diff` both raise a plain RuntimeError saying
    so. That is something the user can fix in five seconds by closing PCSX2,
    which makes it a refusal with a code, not a 500 with a stack trace.
    """
    text = str(ex)
    return isinstance(ex, RuntimeError) and (
        "PCSX2" in text or "working ISO" in text)


def _disc_busy(ex):
    """The refusal for "the working ISO cannot be written".

    It used to hardcode "PCSX2 has the working disc" and "Close PCSX2 and try
    again", with the real reason passed as `detail` -- which renderProblem puts
    behind a collapsed disclosure. There are THREE causes (REQ-078): the
    emulator, the read-only attribute a copied vanilla disc inherits, and
    another writer holding the lock. Two of them are not PCSX2, and for those
    the visible sentence told the user to close something that was not running
    while the sentence they could act on stayed folded up. That is the failure
    api.py's own docstring was written about: "the message the server took the
    trouble to write is the message the user reads."

    So: if the exception carries the halves (api.DiscLocked, filled in by
    Session.locked_problem), they ARE the message and the fix. Anything else
    keeps the old wording, because a bare RuntimeError from somewhere else has
    no better answer to offer and inventing one is how this started.
    """
    msg = getattr(ex, "message", None)
    if msg:
        return Refusal("disc_busy", msg, getattr(ex, "fix", ""), "")
    return Refusal("disc_busy",
                   "PCSX2 has the working disc, so nothing can be written.",
                   "Close PCSX2 and try again. Nothing was written.", str(ex))


def _locked(s):
    """The live reason the working ISO is unwritable, as a refusal.

    For the call sites that KNOW `s.game is None` and used to answer by
    fabricating `RuntimeError("PCSX2 holds the working ISO")` -- a sentence no
    code had checked, naming the one cause out of three that happens to be
    untrue whenever the disc is merely read-only or held by another writer.
    Ask the Studio instead; it is the only object that can tell them apart.
    """
    try:
        return _disc_busy(api.DiscLocked(*s.locked_problem()))
    except Exception:                                         # noqa: BLE001
        # locked_problem touches the filesystem. If it cannot answer, the old
        # wording is still better than a traceback.
        return _disc_busy(RuntimeError("the working ISO is not open"))


def _level(s, body_or_q, get=None):
    get = get or (lambda k: body_or_q.get(k))
    archive = get("archive")
    if not archive:
        raise Refusal("bad_request", "This needs a level archive.", "")
    try:
        return s.level(archive, manifest=get("manifest") or None,
                       area=get("area") or None)
    except KeyError:
        raise Refusal("not_found", "There is no archive called %s." % archive,
                      "Pick a level from the list at the top.")
    except Exception as ex:
        raise Refusal("not_found", "%s could not be opened." % archive, "",
                      str(ex))


def _cast(s, archive):
    try:
        return cast_mod.cast(s.disc, archive)
    except KeyError:
        raise Refusal("not_found", "There is no archive called %s." % archive,
                      "")


def _node(lvl, uid, want=None):
    try:
        n = lvl.by_uid(int(uid))
    except (KeyError, TypeError, ValueError):
        raise Refusal("not_found", "There is nothing selected with id %s." % uid,
                      "Click the object again -- ids change when a level is "
                      "reloaded.")
    if want and n.kind != want:
        raise Refusal("not_found", "That is a %s, not a%s %s."
                      % (n.kind, "n" if want[0] in "aeiou" else "", want), "")
    return n


def _commit(s, lvl, archive, edits, summary, label, dry, extra=None):
    """The one write path for every level edit added by this pass.

    Preview first, always. Build the plan from that preview -- it is a
    projection of a report that already exists, not a second slot-checker
    that could disagree with the first. Refuse with the plan attached if it
    does not fit, so the sheet can show the overflow rather than a sentence.
    """
    report = lvl.preview(edits)
    plan = api.plan(api.rows_from_report(archive, report), summary, label)
    if not plan["fits"]:
        raise api.overflow(plan)
    if dry:
        out = dict(api.ok(plan=plan))
        out.update(extra or {})
        return out

    group = s.journal.new_group()
    try:
        result = lvl.apply(edits, dry=False,
                           writer=s.writer_for(archive, label, group))
    except RuntimeError as ex:
        if _busy(ex):
            raise _disc_busy(ex)
        raise
    if not result.get("applied"):
        raise Refusal("slot_overflow", result.get("error") or
                      "Nothing was written.", "", "", plan=plan)
    s.note("%s :: %s" % (archive, summary))
    out = dict(api.ok(plan=plan, applied=True, group=group))
    out.update(extra or {})
    return out


# --------------------------------------------------------------- GET /api/cast

def get_cast(handler, s, q):
    archive = (q.get("archive") or [None])[0]
    if not archive:
        raise Refusal("bad_request", "This needs a level archive.", "")
    doc = _cast(s, archive)
    return api.ok(**doc)


# -------------------------------------------------------------- GET /api/enemy

def get_enemy(handler, s, q):
    one = lambda k, d=None: (q.get(k) or [d])[0]          # noqa: E731
    lvl = _level(s, None, get=lambda k: one(k))
    n = _node(lvl, one("uid"), "enemy")
    node, fields = lvl.enemy_fields(n.uid)
    return api.ok(uid=n.uid, archive=one("archive"), file=node.source,
                  name=node.props.get("Name") or node.name,
                  spawn=dict(pos=node.xyz(),
                             angle=node.angle.value if node.angle else 0.0),
                  fields=fields)


# ------------------------------------------------------- POST /api/enemy_set

def post_enemy_set(handler, s, body):
    archive = body.get("archive")
    lvl = _level(s, body)
    n = _node(lvl, body.get("uid"), "enemy")
    changes = body.get("changes")
    if not isinstance(changes, dict) or not changes:
        raise Refusal("bad_request", "Nothing was asked to change.", "")

    doc = _cast(s, archive)
    _, lines = lvl.enemy_lines(n.uid)
    try:
        clean = EF.validate(changes, doc, lines)
    except EF.Invalid as ex:
        raise Refusal(ex.code, ex.message, ex.fix)

    edits = lvl.set_enemy_fields(n.uid, clean)
    who = n.props.get("Name") or n.name
    if len(clean) == 1:
        key = next(iter(clean))
        before = EF.field_value(key, lines.get(key))
        after = clean[key]
        label = "%s: %s — %s %s → %s" % (
            _short(archive), who, EF.BY_KEY[key][1],
            "(none)" if before is None else before,
            "(removed)" if after is None else after)
        summary = label.split(": ", 1)[1]
    else:
        label = "%s: %s — %d fields" % (_short(archive), who, len(clean))
        summary = "%s: %d fields change" % (who, len(clean))
    return _commit(s, lvl, archive, edits, summary, label, api.is_dry(body))


# ----------------------------------------------------- POST /api/enemy_place

def post_enemy_place(handler, s, body):
    archive = body.get("archive")
    lvl = _level(s, body)
    pos = body.get("pos")
    if not pos or len(pos) != 3:
        raise Refusal("bad_request", "This needs a position in the level.",
                      "Click the ground where the enemy should stand.")
    if not body.get("type") or not body.get("table"):
        raise Refusal("bad_request",
                      "A new enemy needs both a character and a behaviour.",
                      "They are separate: the character is the model, the "
                      "behaviour is what it does.")

    doc = _cast(s, archive)
    enms = lvl.enm_files()
    if not enms:
        # 26 of the 61 level archives. Naming SWAT.DAT is the whole point:
        # an empty Enemies layer with no explanation is what this replaces.
        raise Refusal(
            "no_enm_here",
            "%s keeps no enemy roster of its own." % _short(archive),
            "This level's enemies live in SWAT.DAT, and placing into SWAT.DAT "
            "is not in this build. All 26 Showdown levels are like this."
            if doc["roster_source"] == "swat" else
            "The disc's 307 loose rosters all sit in SWAT.DAT and none of "
            "them is named for this level, so there is no file to add to. "
            "All 26 Showdown levels are like this.")

    target = body.get("file") or enms[0]
    if target not in enms:
        raise Refusal("not_found", "There is no roster file called %s here."
                      % target, "This level has: %s." % ", ".join(enms))

    fields = dict(body.get("fields") or {})
    fields["Type"] = body["type"]
    fields["Table"] = body["table"]
    if body.get("name"):
        fields["Name"] = body["name"]
    try:
        clean = EF.validate(fields, doc)
    except EF.Invalid as ex:
        raise Refusal(ex.code, ex.message, ex.fix)
    clean.pop("Name", None)

    if body.get("template_uid") is not None:
        template = _node(lvl, body["template_uid"], "enemy")
    else:
        template = EF.smallest_block(lvl.enemy_nodes())
    if template is None:
        raise Refusal("no_enm_here",
                      "%s has no enemy to copy the shape of." % _short(archive),
                      "A new block is cloned from an existing one so its "
                      "layout matches what the game wrote.")

    edits, new_name, block_bytes = lvl.place_enemy(
        target, template, [float(c) for c in pos],
        float(body.get("angle") or 0.0), clean, body.get("name"))

    label = "%s: placed %s (%s)" % (_short(archive), new_name, clean["Type"])
    summary = "Adds %s to %s (%s bytes)" % (new_name, target,
                                            "{:,}".format(block_bytes))
    out = _commit(s, lvl, archive, edits, summary, label, api.is_dry(body),
                  extra=dict(new_name=new_name, file=target, new_uid=None))
    if out.get("applied"):
        match = [n for n in lvl.enemy_nodes(target)
                 if n.props.get("Name") == new_name]
        out["new_uid"] = match[0].uid if match else None
    return out


# -------------------------------------------------------- GET /api/attrs

_CONTROL_BY_TYPE = {"BOOL": "bool", "INT": "int", "FLOAT": "float"}


def get_attrs(handler, s, q):
    one = lambda k, d=None: (q.get(k) or [d])[0]          # noqa: E731
    lvl = _level(s, None, get=lambda k: one(k))
    n = _node(lvl, one("uid"))

    # What values does this level already use for this attribute name? That
    # set is a real, closed observation, so it makes an honest picker -- but
    # the type set is open, so free text stays available alongside it. A
    # closed list here would be a lie.
    seen = {}
    for other in lvl.nodes:
        for a in getattr(other, "attrs", ()):
            seen.setdefault(a.name, set()).add(a.value)

    rows = []
    for a in n.attrs:
        values = sorted(seen.get(a.name, {a.value}))
        control = _CONTROL_BY_TYPE.get(a.type.upper())
        if control is None:
            control = "pick" if len(values) > 1 else "text"
        rows.append(dict(name=a.name, type=a.type, value=a.value,
                         control=control,
                         options=values if control == "pick" else
                         (["true", "false"] if control == "bool" else None),
                         seen_in_level=values, editable=True,
                         rung="measured"))
    return api.ok(uid=n.uid, file=n.source, kind=n.kind, attrs=rows)


def post_attr_set(handler, s, body):
    archive = body.get("archive")
    lvl = _level(s, body)
    n = _node(lvl, body.get("uid"))
    changes = body.get("changes")
    if not isinstance(changes, dict) or not changes:
        raise Refusal("bad_request", "Nothing was asked to change.", "")

    have = {a.name.lower(): a for a in n.attrs}
    clean = {}
    for name, value in changes.items():
        a = have.get(str(name).lower())
        if a is None:
            raise Refusal(
                "not_editable",
                "%s has no attribute called %s." % (n.name, name),
                "Adding a new attribute would mean changing the block's "
                "`attributes N` count, which is a separate piece of work.")
        text = str(value)
        if any(c in text for c in "\r\n"):
            raise Refusal("bad_request", "An attribute cannot span lines.", "")
        clean[a.name] = text

    edits = lvl.set_attributes(n.uid, clean)
    if len(clean) == 1:
        k, v = next(iter(clean.items()))
        label = "%s: %s — %s %s" % (_short(archive), n.name, k, v)
    else:
        label = "%s: %s — %d attributes" % (_short(archive), n.name, len(clean))
    return _commit(s, lvl, archive, edits, label.split(": ", 1)[1], label,
                   api.is_dry(body))


# ---------------------------------------------------- POST /api/node_rotate

def post_node_rotate(handler, s, body):
    archive = body.get("archive")
    lvl = _level(s, body)
    n = _node(lvl, body.get("uid"))
    m9 = body.get("matrix9")
    if not isinstance(m9, (list, tuple)) or len(m9) != 9:
        raise Refusal("bad_request",
                      "A rotation is nine numbers and %s was sent."
                      % (len(m9) if isinstance(m9, (list, tuple)) else "none"),
                      "Send the first nine numbers of the object's matrix, in "
                      "file order.")
    try:
        m9 = [float(v) for v in m9]
    except (TypeError, ValueError):
        raise Refusal("bad_request", "A rotation must be nine numbers.", "")
    if not n.rot:
        raise Refusal(
            "not_editable",
            "A %s carries a single facing angle, not a rotation matrix."
            % n.kind,
            "Use the move route to set its Angle instead.")
    edits = lvl.set_rotation(n.uid, m9,
                             keep_width=body.get("keep_width", True) is not False)
    label = "%s: rotated %s" % (_short(archive), n.name)
    return _commit(s, lvl, archive, edits, "Rotates %s" % n.name, label,
                   api.is_dry(body))


# ---------------------------------------------------- POST /api/vertex_set

def post_vertex_set(handler, s, body):
    archive = body.get("archive")
    lvl = _level(s, body)
    n = _node(lvl, body.get("uid"))
    pos = body.get("pos")
    if not pos or len(pos) != 3:
        raise Refusal("bad_request", "This needs a position.", "")
    try:
        index = int(body.get("index"))
    except (TypeError, ValueError):
        raise Refusal("bad_request", "This needs which point to move.", "")
    if not n.verts:
        raise Refusal("not_editable", "%s is not a shape with points." % n.name,
                      "Splines and patrol routes have points; boxes do not.")
    try:
        edits = lvl.set_vertex(n.uid, index, [float(c) for c in pos],
                               keep_width=body.get("keep_width", True)
                               is not False)
    except IndexError as ex:
        raise Refusal("bad_request", str(ex), "")
    label = "%s: reshaped %s (point %d of %d)" % (_short(archive), n.name,
                                                  index + 1, len(n.verts))
    return _commit(s, lvl, archive, edits, label.split(": ", 1)[1], label,
                   api.is_dry(body))


# ------------------------------------------------- GET /api/menus, /api/menu
#
# FRONT.DAT is the main-menu archive and the only one whose contents are all
# front end: 317 entries *measured* (the brief said 318), of which 27 are
# `.swui`. The other 188 UI files live in SWAT.DAT and the level archives --
# `pause.swui` alone is in 62 of them -- so the browser lists by FILE and says
# how many copies each has, rather than pretending an archive owns a screen.

def _screens(s):
    from . import swui
    return swui.all_screens(s.disc)


def _menu_read(s, name, archive=None):
    """The bytes of one UI file, plus which archives hold it."""
    from . import swui
    screens, where = _screens(s)
    arcs = where.get(name)
    if not arcs:
        raise Refusal("not_found", "There is no menu file called %s." % name,
                      "Pick one from the list on the left.")
    if archive and archive not in arcs:
        raise Refusal("not_found",
                      "%s is not in %s." % (name, archive),
                      "It is in %s." % ", ".join(arcs[:4]))
    arc = archive or arcs[0]
    try:
        data = s.disc.archive(arc).read(name)
    except Exception as ex:
        raise Refusal("not_found", "%s could not be read." % name, "", str(ex))
    return data, arc, arcs, swui


def get_menus(handler, s, q):
    one = lambda k, d=None: (q.get(k) or [d])[0]      # noqa: E731
    from . import swui
    screens, where = _screens(s)
    want = one("archive")
    rows = []
    for name in sorted(screens):
        arcs = where.get(name, [])
        if want and want != "all" and want not in arcs:
            continue
        data = screens[name]
        d = swui.dialect(data)
        try:
            e = s.disc.archive(arcs[0]).find(name)
            slot, stored = e.slot, e.size_comp
        except Exception:
            slot = stored = 0
        rows.append(dict(name=name, ext=name.rsplit(".", 1)[-1].lower(),
                         dialect=d, bytes=len(data), copies=len(arcs),
                         archives=arcs[:8], slot=slot, stored=stored,
                         headroom=slot - stored,
                         front=("FRONT.DAT" in arcs)))
    counts = {}
    for r in rows:
        counts[r["dialect"]] = counts.get(r["dialect"], 0) + 1
    return api.ok(rows=rows, total=len(rows), dialects=counts,
                  archive=want or "all")


def get_menu(handler, s, q):
    one = lambda k, d=None: (q.get(k) or [d])[0]      # noqa: E731
    name = one("name")
    if not name:
        raise Refusal("bad_request", "This needs a menu file name.", "")
    data, arc, arcs, swui = _menu_read(s, name, one("archive"))
    d = swui.dialect(data)
    doc = dict(name=name, archive=arc, archives=arcs, copies=len(arcs),
               dialect=d, bytes=len(data))
    if d == "tags":
        doc.update(swui.summary(data))
    elif d == "props":
        from . import props as P
        # These really are props files wearing a UI extension -- the .swuibook
        # page setups and the .swuipop popups. Handing them to the tag reader
        # would show an empty screen and call it parsed.
        doc.update(P.summary(data), parsable=True, why="")
    elif d == "list":
        doc.update(parsable=True, why="",
                   lines=[ln.to_json() for ln in swui.parse_list(data)])
    else:
        okay, why = swui.looks_parsable(data)
        doc.update(parsable=False, why=why)
    try:
        e = s.disc.archive(arc).find(name)
        doc.update(slot=e.slot, stored=e.size_comp, headroom=e.headroom,
                   compressed=e.compressed)
    except Exception:
        pass
    _, _, note = swui.lookup_names(s.disc, _screens(s)[0])
    doc["evidence_note"] = note
    return api.ok(**doc)


# ----------------------------------------------------- POST /api/menu_set

def _menu_risks(s, name, data, changes):
    """Grade every proposed change against the two name-lookup evidence sets.

    Values are safe and renames are not, and the difference is not a matter of
    taste: the engine resolves widgets by name, so a rename here is a lookup
    that stops matching there. Renaming a button in `pause.swui` froze
    story-level loading, and the evidence for why is on the disc -- 69 of 210
    screen names and 34 of 144 widget ids appear verbatim in the unpacked
    executable.
    """
    from . import swui
    screens, _ = _screens(s)
    exe, refs, note = swui.lookup_names(s.disc, screens)
    out = []
    dia = swui.dialect(data)

    if dia == "list":
        # The `*cache*.swui` preload manifests are bare screen names, one to a
        # line, and a name here is a lookup key exactly like a name in a tag --
        # *measured*, 85 of their 111 entries are strings in SLUS_205.00 or are
        # named by another screen. Returning [] for them graded all 85 "safe",
        # which is the one grade this panel exists not to give. A list entry is
        # one token, so it wears a one-token Field and goes through the same
        # `risk_of` as everything else rather than growing a second grader.
        lines = {ln.path: ln for ln in swui.parse_list(data)}
        for path, value in changes.items():
            ln = lines.get(path)
            if ln is None:
                continue
            field = swui.Field(0, path, ln.raw, ln.start, ln.end,
                               [swui.Token(ln.raw, ln.start, ln.end)])
            level, why = swui.risk_of("PRELOAD", field, ln.raw, str(value),
                                      exe, refs)
            if level != "safe":
                out.append(dict(path=path, tag="PRELOAD", old=ln.raw,
                                new=str(value), level=level, why=why))
        return out, note

    if dia == "props":
        # The `.swuipop`/`.swuibook` page setups are ordinary props files, and
        # most of what is in them is layout -- but `SwuiFilename` and
        # `ContentsSwuiName` name another SCREEN, which is the same lookup the
        # tag reader guards. *Measured*, 7 of their 318 fields hold a value
        # that is a string in SLUS_205.00 or is named by another screen. Seven
        # is small; it is also every field in this dialect whose entire job is
        # to point at a file by name.
        from . import props as P
        spans = {x.path: x for x in P.parse(data)}
        for path, value in changes.items():
            f = spans.get(path)
            if f is None:
                continue
            field = swui.Field(0, path, f.raw, f.start, f.end,
                               [swui.Token(f.raw, f.start, f.end)])
            level, why = swui.risk_of("PRELOAD", field, f.raw, str(value),
                                      exe, refs)
            if level != "safe":
                out.append(dict(path=path, tag="SETTING", old=f.raw,
                                new=str(value), level=level, why=why))
        return out, note

    if dia != "tags":
        return out, note
    fields = {f.path: (t.name, f)
              for t in swui.parse(data) for f in t.fields}
    for path, value in changes.items():
        # A token path is the field path plus `.t<n>`, so the base is what is
        # left after dropping that last dotted segment. Testing for `".t" in`
        # the last segment tested the segment `t1` for a dot it can never
        # contain, so the base stayed `BUTTON#1.f0.t1`, `fields.get` missed,
        # and EVERY token edit fell out of the loop ungraded. The front end
        # sends a token path for every field with more than one token, and
        # *measured*, 307 tokens on the disc grade `danger` -- `lvl_bridge`,
        # `lvl_ranch`, `lvl_woods` and the rest of the chapter table, which is
        # precisely the story-level loading this warning is about.
        last = path.rsplit(".", 1)[-1]
        is_token = len(last) > 1 and last[0] == "t" and last[1:].isdigit()
        base = path.rsplit(".", 1)[0] if is_token else path
        hit = fields.get(base)
        if not hit:
            continue
        tag, field = hit
        old = field.raw
        if is_token:                          # a single token inside the field
            i = int(last[1:])
            old = field.tokens[i].text if i < len(field.tokens) else ""
        level, why = swui.risk_of(tag, field, old, str(value), exe, refs)
        if level != "safe":
            out.append(dict(path=path, tag=tag, old=old, new=str(value),
                            level=level, why=why))
    return out, note


def post_menu_set(handler, s, body):
    from . import swui
    name, changes = api.require(body, "name", "changes")
    if not isinstance(changes, dict) or not changes:
        raise Refusal("bad_request", "This needs at least one change.", "")
    data, arc, arcs, _ = _menu_read(s, name, body.get("archive"))
    dialect = swui.dialect(data)
    if dialect == "unreadable":
        okay, why = swui.looks_parsable(data)
        raise Refusal("not_editable", "%s cannot be edited: %s." % (name, why),
                      "")

    # Build the new bytes once, here, so a bad path or an illegal character is
    # a refusal with a sentence rather than 62 identical exceptions inside the
    # per-archive loop.
    setter = (swui.set_fields if dialect == "tags" else
              swui.apply_list if dialect == "list" else None)
    if setter is None:
        from . import props as P
        setter = P.apply
    try:
        setter(data, changes)
    except KeyError as ex:
        raise Refusal("not_found", "That field is not in %s." % name,
                      "Reload the screen -- field paths change if the file "
                      "was edited elsewhere.", str(ex))
    except ValueError as ex:
        raise Refusal("not_editable", str(ex), "")

    risks, note = _menu_risks(s, name, data, changes)
    danger = [r for r in risks if r["level"] == "danger"]
    # The warning is loud and it is not a veto -- the user owns their disc.
    # What it does refuse is a rename applied *without having been shown*, so
    # a script or a stale panel cannot rename a widget silently.
    if danger and not body.get("confirm_renames"):
        raise Refusal(
            "not_editable",
            "%s renames %s the engine looks up by name."
            % (name, "a widget" if len(danger) == 1 else
               "%d widgets" % len(danger)),
            "Tick \"Rename anyway\" below to do it regardless "
            "(over the wire: \"confirm_renames\": true). Position, size, "
            "colour and timing changes never need this.",
            "", risks=risks, renames=danger, evidence_note=note)

    scope = (body.get("scope") or "all").lower()
    targets = [arc] if scope == "one" else arcs
    dry = api.is_dry(body)
    summary = "%s: %d field%s" % (name, len(changes),
                                  "" if len(changes) == 1 else "s")
    label = "menu %s" % summary

    # `edit_creature` is already "write this file into every archive that holds
    # it, as ONE undo step", per-copy slot check included. Reused, not forked.
    try:
        rep = s.edit_creature(name, changes, dry=True, setter=setter,
                              kind="menu", archives=targets)
    except RuntimeError as ex:
        if _busy(ex):
            raise _disc_busy(ex)
        raise
    broken = [f for f in rep["failed"] if "stored" not in f]
    if broken:
        raise Refusal("not_editable",
                      "%s could not be rewritten: %s"
                      % (name, broken[0].get("error", "")), "")
    rows = [api.write_row(r["archive"], name, r["bytes_before"],
                          r["bytes_after"], r["stored"], r["slot"])
            for r in rep["applied"] + rep["failed"]]
    plan = api.plan(rows, summary, label)
    if not plan["fits"]:
        raise api.overflow(plan)
    if dry:
        return api.ok(plan=plan, risks=risks, evidence_note=note,
                      copies=len(targets))

    group = s.journal.new_group()
    try:
        done = s.edit_creature(name, changes, dry=False, group=group,
                               setter=setter, kind="menu", archives=targets)
    except RuntimeError as ex:
        if _busy(ex):
            raise _disc_busy(ex)
        raise
    finally:
        # The bytes on the disc have moved, so every cached read of them is
        # now a lie -- including the evidence the rename warning is built on.
        # In `finally` because a write that failed partway still changed some
        # copies.
        swui.invalidate(s.disc)
    s.note("%s :: %s" % (arc, summary))
    return api.ok(plan=plan, applied=True, group=group, risks=risks,
                  evidence_note=note, copies=len(done["applied"]),
                  failed=done["failed"])


# ---------------------------------------------------------- GET /api/links

def _sub_headroom(dave, filename, data):
    e = dave.find(filename)
    return e.slot - api.stored_size(e, data)


def get_links(handler, s, q):
    archive = (q.get("archive") or [None])[0]
    if not archive:
        raise Refusal("bad_request", "This needs a level archive.", "")
    try:
        dave = s.disc.archive(archive)
    except Exception:
        raise Refusal("not_found", "There is no archive called %s." % archive, "")
    subs = links_mod.sub_files(dave)
    dests = links_mod.destinations(s.disc)
    if not subs:
        # Only 6 distinct `.sub` files exist across 61 level archives, so this
        # is the common answer and it has to read as a fact, not a failure.
        return api.ok(archive=archive, file=None, doors=[],
                      destinations=dests,
                      note="%s has no doors between areas. Only ten level "
                           "archives on the disc do." % _short(archive))
    filename = subs[0]
    data = dave.read(filename)
    headroom = _sub_headroom(dave, filename, data)
    doors = [d.to_json(headroom) for d in links_mod.parse(data)]
    return api.ok(archive=archive, file=filename, doors=doors,
                  destinations=dests)


def post_link_set(handler, s, body):
    archive = body.get("archive")
    if not archive:
        raise Refusal("bad_request", "This needs a level archive.", "")
    try:
        dave = s.disc.archive(archive)
    except Exception:
        raise Refusal("not_found", "There is no archive called %s." % archive, "")
    subs = links_mod.sub_files(dave)
    filename = body.get("file") or (subs[0] if subs else None)
    if not filename or filename not in subs:
        raise Refusal("not_found", "%s has no door file called %s."
                      % (_short(archive), filename), "")
    data = dave.read(filename)
    doors = links_mod.parse(data)
    try:
        door = doors[int(body.get("id"))]
    except (TypeError, ValueError, IndexError):
        raise Refusal("not_found", "There is no door %s in %s."
                      % (body.get("id"), filename), "")

    changes = links_mod.flatten(body.get("changes") or {})
    if not changes:
        raise Refusal("bad_request", "Nothing was asked to change.", "")

    dests = links_mod.destinations(s.disc)
    fields = {f.path: f for f in links_mod.parse_fields(data)}
    edits = []
    for name, value in changes.items():
        path = door.paths.get(name)
        if path is None or path not in fields:
            raise Refusal("not_editable",
                          "This door has no %s to change." % name,
                          "Doors differ; some carry fields others do not.")
        if name == "level":
            resolved = links_mod.resolve_destination(dests, value)
            if resolved is None:
                raise Refusal(
                    "unknown_value",
                    "There is no level called %r on the disc." % value,
                    "A door can only lead somewhere that exists. There are "
                    "%d places to choose from." % len(dests))
            value = resolved
        f = fields[path]
        edits.append((f.start, f.end,
                      links_mod.render(name, value, f.raw)))

    from .scene import splice
    new = splice(data, edits)
    e = dave.find(filename)
    row = api.write_row(archive, filename, len(data), len(new),
                        api.stored_size(e, new), e.slot)
    which = ", ".join(sorted(changes))
    label = "%s: door %d — %s" % (_short(archive), door.index, which)
    plan = api.plan([row], "Changes %s on the door to %s"
                    % (which, door.values.get("level") or "another area"), label)
    if not plan["fits"]:
        raise api.overflow(plan)
    if api.is_dry(body):
        return api.ok(plan=plan)
    group = s.journal.new_group()
    s.write(archive, filename, new, label, group)
    s.note("%s :: %s" % (archive, label))
    return api.ok(plan=plan, applied=True, group=group)


# ------------------------------------------------------- behaviour tables
#
# A `.tbl` binds behaviour slots to `.stateb` state machines, and a `.stateb`
# is a token stream whose opcodes have no names anywhere on this disc. The
# split that governs every route below:
#
#   .tbl bindings      a closed, knowable set -- a dropdown, never a text box.
#                      *Measured:* 27,263 of 27,544 shipped bindings resolve to
#                      a `.stateb` in their own archive; all 281 that do not
#                      are in SWAT.DAT.
#   .stateb floats     safely editable, four bytes for four, so the length
#                      never moves and a slot cannot overflow.
#   .stateb integers   NOT offered. They are opcodes and indices, and naming
#                      one would be inventing it. See `aivocab`.


def _dave(s, archive, writable=False):
    """The archive, or a refusal. `writable` insists on the working disc.

    `s.disc` silently falls back to the pristine ISO while PCSX2 holds the
    working one (studio.py:541), which is right for browsing and wrong for
    anything that is about to write -- it would diff vanilla against vanilla
    and decide there is nothing to do.
    """
    src = s.game if writable else s.disc
    if src is None:
        raise _locked(s)
    try:
        return src.archive(archive)
    except Exception as ex:
        raise Refusal("not_found", "There is no archive called %s." % archive,
                      "Pick a level from the list at the top.", str(ex))


def _table_file(name):
    n = str(name or "").strip()
    return n if n.lower().endswith(".tbl") else n + ".tbl"


def _state_file(name):
    n = str(name or "").strip()
    return n if n.lower().endswith(".stateb") else n + ".stateb"


def get_behaviour_tables(handler, s, q):
    one = lambda k, d=None: (q.get(k) or [d])[0]          # noqa: E731
    archive = one("archive")
    if not archive:
        raise Refusal("bad_request", "This needs an archive.", "")
    dave = _dave(s, archive)
    rows = beh_mod.tables(dave)
    filt = (one("q") or "").strip().lower()
    if filt:
        rows = [r for r in rows if filt in r["id"].lower()]
    try:
        limit = max(1, int(one("limit", "400")))
    except (TypeError, ValueError):
        raise Refusal("bad_request", "`limit` has to be a number.", "")
    # SWAT.DAT holds 2,705 of the disc's 3,275 `.tbl`. Sending them all is a
    # megabyte of JSON for a list nobody scrolls; the count is still honest.
    return api.ok(archive=archive, total=len(rows), shown=len(rows[:limit]),
                  tables=rows[:limit],
                  states=len(beh_mod.state_palette(dave)))


def get_behaviour_table(handler, s, q):
    one = lambda k, d=None: (q.get(k) or [d])[0]          # noqa: E731
    archive, name = one("archive"), one("name")
    if not archive or not name:
        raise Refusal("bad_request", "This needs an archive and a table.", "")
    dave = _dave(s, archive)
    filename = _table_file(name)
    try:
        doc = beh_mod.table_doc(dave, filename, archive)
    except KeyError:
        raise Refusal("not_found", "There is no %s in %s."
                      % (filename, _short(archive)), "")
    copies = s.disc.locate(filename)
    return api.ok(copies=copies, rung="measured", **doc)


def get_behaviour_state(handler, s, q):
    one = lambda k, d=None: (q.get(k) or [d])[0]          # noqa: E731
    archive, name = one("archive"), one("name")
    if not archive or not name:
        raise Refusal("bad_request", "This needs an archive and a state file.",
                      "")
    dave = _dave(s, archive)
    filename = _state_file(name)
    try:
        e = dave.find(filename)
        data = dave.read(e)
    except KeyError:
        raise Refusal("not_found", "There is no %s in %s."
                      % (filename, _short(archive)),
                      "A table can name a state that is not on the disc -- 281 "
                      "shipped bindings do, all of them in SWAT.DAT.")
    try:
        doc = beh_mod.numbers(data)
    except ValueError as ex:
        raise Refusal("not_editable", "%s is not a behaviour table." % filename,
                      "", str(ex))
    return api.ok(archive=archive, file=filename,
                  stored=e.size_comp, slot=e.slot,
                  headroom=e.slot - e.size_comp,
                  copies=s.disc.locate(filename),
                  # Rung, on the row that matters: the numbers are located and
                  # writable; what they DO is not known, and never claimed.
                  rung="untested", **doc)


def _copy_plan(archive, filename, res, summary, label):
    """`edit_creature`'s report -> the frozen plan. A projection, not a recheck."""
    rows = [api.write_row(r["archive"], filename, r.get("bytes_before", 0),
                          r.get("bytes_after", 0), r.get("stored", 0),
                          r.get("slot", 0))
            for r in list(res["applied"]) + list(res["failed"])]
    return api.plan(rows, summary, label)


def _write_copies(s, archive, filename, changes, setter, kind, everywhere,
                  summary, label, dry):
    """Dry-run every copy, refuse as a whole, then write all of them as one step.

    `Studio.edit_creature` already is "write this file into every archive that
    holds it, as ONE undo step", so this adds the envelope and nothing else.
    """
    if s.game is None:
        raise _locked(s)
    archives = None if everywhere else [archive]
    chk = s.edit_creature(filename, changes, everywhere=everywhere, dry=True,
                          setter=setter, kind=kind, archives=archives)
    # A copy can fail for reasons that have nothing to do with slot size -- a
    # table in one archive that does not carry the line another does, an
    # offset that is a float here and an opcode there. `edit_creature` catches
    # those per archive and reports them with no byte counts, so they are told
    # apart by the ABSENCE of a slot and answered FIRST. Reported as
    # "it does not fit" they would send someone hunting for room that was
    # never the problem.
    hard = [f for f in chk["failed"] if not f.get("slot")]
    if hard:
        raise Refusal("not_editable",
                      "%s could not be changed in %s."
                      % (filename, hard[0]["archive"]),
                      "Every copy has to take the same edit, or the disc ends "
                      "up disagreeing with itself.", hard[0].get("error", ""))
    plan = _copy_plan(archive, filename, chk, summary, label)
    if not plan["fits"]:
        raise api.overflow(plan)
    if dry:
        return api.ok(plan=plan, copies=chk["archives"])

    group = s.journal.new_group()
    try:
        res = s.edit_creature(filename, changes, everywhere=everywhere,
                              dry=False, group=group, setter=setter, kind=kind,
                              archives=archives)
    except RuntimeError as ex:
        if _busy(ex):
            raise _disc_busy(ex)
        raise
    if res["failed"]:
        # The dry run said every copy fits, so reaching here means the disc
        # changed under us. The written copies share `group`, so one Ctrl+Z
        # still takes the whole partial write back -- and the group travels
        # with the refusal so the front end can offer exactly that.
        raise Refusal("slot_overflow", "%d of %d copies were refused."
                      % (len(res["failed"]), res["archives"]),
                      "The rest were written; press Ctrl+Z to take them back.",
                      res["failed"][0].get("error", ""), plan=plan, group=group)
    s.note("%s :: %s" % (archive, summary))
    return api.ok(plan=_copy_plan(archive, filename, res, summary, label),
                  applied=True, group=group, copies=res["archives"])


def post_behaviour_table_set(handler, s, body):
    archive, name = api.require(body, "archive", "name")
    changes = body.get("changes")
    if not isinstance(changes, dict) or not changes:
        raise Refusal("bad_request", "Nothing was asked to change.", "")
    filename = _table_file(name)
    dave = _dave(s, archive, writable=True)
    try:
        doc = beh_mod.table_doc(dave, filename, archive)
    except KeyError:
        raise Refusal("not_found", "There is no %s in %s."
                      % (filename, _short(archive)), "")

    have = {r["path"]: r for r in doc["states"]}
    clean = {}
    for path, value in changes.items():
        row = have.get(path)
        if row is None:
            raise Refusal(
                "not_editable", "This table has no %s line." % path,
                "Tables bind different sets of slots -- this one binds %s."
                % ", ".join(r["key"] for r in doc["states"]))
        # NEVER free text. A misspelt state name is not an enemy that
        # misbehaves, it is a filename the game looks up and does not find.
        #
        # The file's OWN spelling wins a case-insensitive tie. Archive entry
        # names are stored lower case, so the palette offers `sher70_always`
        # where the table writes `Sher70_Always` -- without this, re-picking
        # the value a table already holds would rewrite it in a different case
        # and show up in every diff for the rest of the project's life.
        if str(value).strip().lower() == row["value"].lower():
            continue
        resolved = beh_mod.resolve_state(doc["palette"], value)
        if resolved is None:
            raise Refusal(
                "unknown_value",
                "%s has no state file called %s." % (_short(archive), value),
                "There are %d to choose from in this archive."
                % len(doc["palette"]))
        clean[path] = resolved
    if not clean:
        raise Refusal("bad_request", "Those are the values it already has.",
                      "Change one of the dropdowns first.")

    if len(clean) == 1:
        path, value = next(iter(clean.items()))
        summary = "%s: %s %s → %s" % (doc["file"], path, have[path]["value"],
                                      value)
    else:
        summary = "%s: %d bindings change" % (doc["file"], len(clean))
    label = "%s: %s" % (_short(archive), summary)
    everywhere = body.get("everywhere", True) is not False
    return _write_copies(s, archive, filename, clean, beh_mod.set_fields,
                         "behaviour", everywhere, summary, label,
                         api.is_dry(body))


def post_behaviour_table_rename(handler, s, body):
    """Rename a `.tbl`, and optionally repoint the enemies that name it.

    Three things make this unlike every other write in this tab, and all three
    belong in the plan rather than in a footnote.

    **The archive is re-indexed, not relabelled.** The engine resolves a file
    by binary search over the 16-byte TOC with a case-insensitive compare --
    all 66 archives are sorted under it -- so a record renamed in place would
    leave the array unsorted, and bsearch on an unsorted array does not merely
    lose the moved entry, it can miss arbitrary others. `dave.rename` moves the
    record to its new sorted position and re-decodes the whole TOC before
    accepting. Nothing else in this product moves a TOC record.

    **It cannot be undone.** The journal's undo rewrites file CONTENT into a
    slot; it does not put a name back. Same property as clone, and the reply
    says so on the dry run as well as the write.

    **Renames are finite.** There is no `remove()`, so the old name's bytes
    stay in the name block as dead space. `spare_name_bytes` is reported for
    that reason: a tight archive runs out, and it should say so before the
    write rather than during.

    Referrer policy, which is rdr-fmt's and measured: `.enm` referrers are
    REPORTED and the rename is refused until the caller confirms, because
    repointing them is safe and checkable. A `.stateb`/`.actb` naming the
    table inline (opcode 1003) is a HARD refusal -- rewriting a token stream
    whose opcodes nothing on this disc names is not something to do on a
    modder's behalf. 2,017 of the 3,275 `.tbl` have no referrers at all and
    rename freely.
    """
    archive, name = api.require(body, "archive", "name")
    to = str(body.get("to") or "").strip()
    if not to:
        raise Refusal("bad_request", "This needs a new name.", "")
    old_stem = _table_file(name)[:-4]
    new_stem = to[:-4] if to.lower().endswith(".tbl") else to

    dave = _dave(s, archive, writable=True)
    try:
        chk = beh_mod.check_rename_table(dave, old_stem, new_stem)
    except KeyError:
        raise Refusal("not_found", "There is no %s.tbl in %s."
                      % (old_stem, _short(archive)), "")

    # Both numbers, always. "20 referrers" and "2 files" are each true of
    # `actor` in BARFIGHT and they mislead in opposite directions, so the
    # reply carries the pair and the UI prints "2 files, 20 enemies".
    report = dict(file=chk["file"], to=chk["to"],
                  enm_referrers=chk["enm_referrers"],
                  rewrites_needed=chk["rewrites_needed"],
                  enemies_affected=chk["enemies_affected"],
                  locked_by=chk["locked_by"],
                  spare_name_bytes=chk["spare_name_bytes"],
                  undoable=False)

    if chk["locked_by"]:
        raise Refusal(
            "not_editable",
            "%s is named inside %d state file(s), which this cannot rewrite."
            % (chk["file"], len(chk["locked_by"])),
            "A .stateb names it in its token stream, and the opcodes around "
            "that name are not documented anywhere on this disc. Rename a "
            "table nothing binds, or clone this one instead.",
            **report)
    if not chk["ok"]:
        raise Refusal("not_editable",
                      "%s cannot be renamed to %s." % (chk["file"], chk["to"]),
                      " ".join(chk["reasons"]) or "", **report)

    if chk["rewrites_needed"] and not body.get("confirm_referrers"):
        # `not_editable`, not a new code: api.CODES is a frozen set the front
        # end knows how to render, and the swui route already signals
        # "confirm first" this way -- refuse, name the flag in `fix`. Widening
        # the set for one route would be a cross-cutting change to every
        # client that switches on it.
        raise Refusal(
            "not_editable",
            "%d enem%s in %d file(s) still name %s."
            % (chk["enemies_affected"],
               "y" if chk["enemies_affected"] == 1 else "ies",
               chk["rewrites_needed"], chk["file"]),
            "Renaming without repointing them leaves those enemies naming a "
            "table that no longer exists. Confirm to repoint them in the same "
            "step (over the wire: \"confirm_referrers\": true).", **report)

    dry = api.is_dry(body)
    moved = dave.rename(chk["file"], chk["to"], dry=True)
    report["index_from"] = moved.get("index_from")
    report["index_to"] = moved.get("index_to")

    # A real plan, costed the way `edit_creature` costs one -- not because the
    # rename itself moves bytes (it does not; no file data moves and no offset
    # changes) but because REPOINTING the referrers does. Repointing `actor`
    # grew one .enm 7,775 -> 7,799, so these can overflow a slot like any
    # other write and the sheet has to say so before Apply is live. The sheet
    # disables Apply on a missing or `fits:false` plan, so a route that
    # returned no plan would render a button nobody could press.
    import zlib
    writes, fits = [], True
    for enm in chk["enm_referrers"]:
        e = dave.find(enm)
        old_bytes = dave.read(enm)
        out = beh_mod.set_table_field(old_bytes, old_stem, new_stem)
        if out == old_bytes:
            continue
        if e.compressed:
            co = zlib.compressobj(9, 8, -15)
            stored = len(co.compress(out) + co.flush())
        else:
            stored = len(out)
        if stored > e.slot:
            fits = False
        writes.append(dict(archive=archive, name=enm,
                           bytes_before=len(old_bytes), bytes_after=len(out),
                           stored=stored, slot=e.slot,
                           headroom=e.slot - stored))
    # The name block is the rename's own budget, and it is finite: there is no
    # remove(), so the old name stays as dead space. Counted as part of `fits`
    # rather than discovered mid-write.
    if chk["spare_name_bytes"] < len(chk["to"]):
        fits = False
    report["plan"] = dict(writes=writes, fits=fits,
                          undo=dict(steps=0),
                          spare_name_bytes=chk["spare_name_bytes"])
    if dry:
        return api.ok(dry=True, rung="measured", **report)

    # Referrers first, and each through the ordinary content-write envelope --
    # repointing `actor` to `zz_actor` grew one .enm from 7,775 to 7,799
    # bytes, so these can overflow a slot like any other write. Doing them
    # inside `dave.rename` would have been a second write path, which is the
    # thing REQ-009 existed to avoid.
    group = s.journal.new_group() if s.journal else None
    label = "%s: rename %s to %s" % (_short(archive), chk["file"], chk["to"])
    rewritten = []
    for enm in chk["enm_referrers"]:
        data = dave.read(enm)
        out = beh_mod.set_table_field(data, old_stem, new_stem)
        if out == data:
            continue
        used, slot = s.write(archive, enm, out, label, group)
        rewritten.append(dict(file=enm, used=used, slot=slot))

    done = dave.rename(chk["file"], chk["to"], dry=False)
    s.note("%s :: renamed %s to %s, repointed %d referrer file(s)"
           % (archive, chk["file"], chk["to"], len(rewritten)))
    return api.ok(applied=True, rung="measured", rewritten=rewritten,
                  index_from=done.get("index_from"),
                  index_to=done.get("index_to"), **{
                      k: v for k, v in report.items()
                      if k not in ("index_from", "index_to")})


def post_behaviour_state_rename(handler, s, body):
    """Rename a `.stateb`, and repoint every `.tbl` binding that names it.

    **The policy is the OPPOSITE of the table rename's, and the inversion is
    measured rather than assumed.** Across the 35 level archives, 1,315 of
    1,315 `.stateb` are named by at least one `.tbl` binding -- there are no
    orphans -- and ZERO are named inline in a token stream. So where a
    referenced `.tbl` is refused (some of its referrers live in `.stateb`
    token streams nobody can rewrite safely), a referenced `.stateb` must be
    REWRITTEN: refusing over referrers would refuse all 1,315 and the verb
    would not exist. Its referrers are `.tbl` bindings -- plain text, known
    span, and `repoint_states` leaves key spelling, indentation and any
    trailing `//comment` alone.

    **What refuses instead is the SLOT, and it refuses often enough to be
    ordinary.** A referrer `.tbl` is deflated into a fixed slot, so a
    same-length rename can still overflow it: `barfight_always` ->
    `zz_fight_always` is fifteen characters either way, leaves
    `barfight_bar1.tbl` at exactly 372 bytes, and moves its stored size to 131
    in a 128-byte slot. 8.3% of same-length state renames across the level
    archives are refused for exactly that, skewed toward heavily-referenced
    states. The plan shows every referrer costed, and a refusal names the ones
    that cannot take it.
    """
    archive, name = api.require(body, "archive", "name")
    to = str(body.get("to") or "").strip()
    if not to:
        raise Refusal("bad_request", "This needs a new name.", "")
    old_stem = _state_file(name)[:-7]
    new_stem = to[:-7] if to.lower().endswith(".stateb") else to

    dave = _dave(s, archive, writable=True)
    try:
        chk = beh_mod.check_rename_state(dave, old_stem, new_stem)
    except KeyError:
        raise Refusal("not_found", "There is no %s.stateb in %s."
                      % (old_stem, _short(archive)), "")

    rewrites = chk.get("rewrites") or []
    blocked = chk.get("blocked_by_slot") or []
    # Costed per referrer, through Dave.would_fit -- which rdr-fmt factored
    # OUT of Dave.patch rather than writing beside it, so the planner and the
    # writer cannot disagree about what fits.
    plan = dict(writes=[dict(archive=archive, name=w["file"],
                             bytes_before=w["bytes_before"],
                             bytes_after=w["bytes_after"],
                             stored=w["stored"], slot=w["slot"],
                             headroom=w.get("headroom",
                                            w["slot"] - w["stored"]))
                        for w in rewrites],
                fits=not blocked and chk.get("ok", False),
                undo=dict(steps=0),
                spare_name_bytes=chk.get("spare_name_bytes"))
    report = dict(file=chk["file"], to=chk["to"],
                  tbl_referrers=chk.get("tbl_referrers") or [],
                  bindings=chk.get("bindings"),
                  blocked_by_slot=blocked,
                  spare_name_bytes=chk.get("spare_name_bytes"),
                  undoable=False, plan=plan)

    if blocked:
        raise Refusal(
            "slot_overflow",
            "%d of the %d table(s) naming %s cannot take the new name."
            % (len(blocked), len(rewrites), chk["file"]),
            "A .tbl is deflated into a fixed slot, so a same-length rename can "
            "still overflow it. Try a shorter name, or rename a state fewer "
            "tables bind. Refused: %s" % ", ".join(blocked), **report)
    if not chk.get("ok"):
        raise Refusal("not_editable",
                      "%s cannot be renamed to %s." % (chk["file"], chk["to"]),
                      " ".join(chk.get("reasons") or []) or "", **report)

    if api.is_dry(body):
        return api.ok(dry=True, rung="measured", **report)

    group = s.journal.new_group() if s.journal else None
    label = "%s: rename %s to %s" % (_short(archive), chk["file"], chk["to"])
    done = []
    for w in rewrites:
        data = dave.read(w["file"])
        out = beh_mod.repoint_states(data, old_stem, new_stem)
        if out == data:
            continue
        used, slot = s.write(archive, w["file"], out, label, group)
        done.append(dict(file=w["file"], used=used, slot=slot))
    moved = dave.rename(chk["file"], chk["to"], dry=False)
    s.note("%s :: renamed %s to %s, repointed %d table(s)"
           % (archive, chk["file"], chk["to"], len(done)))
    return api.ok(applied=True, rung="measured", rewritten=done,
                  index_from=moved.get("index_from"),
                  index_to=moved.get("index_to"), **report)


def post_behaviour_state_set(handler, s, body):
    archive, name = api.require(body, "archive", "name")
    changes = body.get("changes")
    if not isinstance(changes, dict) or not changes:
        raise Refusal("bad_request", "Nothing was asked to change.", "")
    filename = _state_file(name)
    dave = _dave(s, archive, writable=True)
    try:
        data = dave.read(filename)
    except KeyError:
        raise Refusal("not_found", "There is no %s in %s."
                      % (filename, _short(archive)), "")
    try:
        legal = beh_mod.float_offsets(data)
    except ValueError as ex:
        raise Refusal("not_editable", "%s is not a behaviour table." % filename,
                      "", str(ex))

    clean = {}
    for off, value in changes.items():
        try:
            off = int(off)
        except (TypeError, ValueError):
            raise Refusal("bad_request", "%r is not a byte offset." % off, "")
        if off not in legal:
            raise Refusal(
                "not_editable",
                "There is no number at byte %d in %s." % (off, filename),
                "Only the float parameters this file holds can change. The "
                "whole numbers around them are opcodes and indices, and no "
                "name for any of them exists on this disc.")
        try:
            clean[off] = float(value)
        except (TypeError, ValueError):
            raise Refusal("bad_request", "%r is not a number." % value, "")
    if not clean:
        raise Refusal("bad_request", "Nothing was asked to change.", "")

    summary = ("%s: %d parameter%s" % (filename, len(clean),
                                       "" if len(clean) == 1 else "s")
               + (" at byte %d" % next(iter(clean)) if len(clean) == 1 else ""))
    label = "%s: %s" % (_short(archive), summary)
    everywhere = body.get("everywhere", True) is not False
    return _write_copies(s, archive, filename, clean, beh_mod.set_numbers,
                         "behaviour", everywhere, summary, label,
                         api.is_dry(body))


def post_behaviour_table_clone(handler, s, body):
    """Copy a `.tbl` to a NEW name in the same archive, via `dave.add`.

    This is the one route in the file that adds a file rather than patching
    one, and it is the one route whose plan says `undo.steps: 0`. There is no
    "remove entry" in this format, and the journal restores a previous state by
    reading the pristine disc -- a file that was never on it has no previous
    state, so recording the add would leave an undo that raises instead of
    undoing. Saying so is the honest answer; pretending is not.
    """
    archive, source, name = api.require(body, "archive", "source", "name")
    dry = api.is_dry(body)
    dave = _dave(s, archive, writable=True)
    src = _table_file(source)
    try:
        data = dave.read(src)
    except KeyError:
        raise Refusal("not_found", "There is no %s in %s."
                      % (src, _short(archive)),
                      "A new table is copied from one that already exists, so "
                      "its shape is what the game's own exporter wrote.")

    chk = beh_mod.check_clone_name(dave, name)
    if not chk["ok"]:
        raise Refusal("bad_request", chk["reasons"][0].capitalize() + ".",
                      "Pick another name.")
    # `can_add` first, and `add(dry=True)` only once it says yes. `add` raises
    # a bare ValueError on a failed check, and a stack-trace string is not the
    # sentence someone reads when an archive is out of room.
    add = dave.can_add(chk["file"], len(data))
    if not add["ok"]:
        raise Refusal(
            "slot_overflow",
            "%s cannot take a new file: %s."
            % (_short(archive), "; ".join(add["reasons"])),
            "Nothing was written. Adding a file needs three things at once: a "
            "free run between stored files, a spare table-of-contents slot, "
            "and room in the name block.")
    add = dave.add(chk["file"], data, dry=True)

    # Adding a file consumes free space that belonged to some other entry's
    # SLOT, so that entry loses headroom permanently. Say which, before the
    # button is pressed -- it is the only lasting cost of this operation.
    at = add["payload_offset"]
    victim = next((e for e in dave
                   if e.offset <= at < e.offset + e.slot), None)
    row = api.write_row(archive, chk["file"], 0, len(data),
                        add["stored_bytes"], add["payload_room"])
    summary = ("Adds %s to %s as a copy of %s (%s stored bytes in a %s-byte "
               "free run)" % (chk["file"], _short(archive), src,
                              "{:,}".format(add["stored_bytes"]),
                              "{:,}".format(add["payload_room"])))
    plan = api.plan([row], summary,
                    "added %s (this one cannot be undone)" % chk["file"],
                    steps=0)
    note = ("A file can be added to an archive but not removed, so this is "
            "the one edit Ctrl+Z cannot take back. Reset to stock, or restore "
            "a version, is the way out.")
    if victim is not None:
        note += (" It also uses free space inside %s's slot, which drops that "
                 "file's headroom from %s to %s bytes."
                 % (victim.name, "{:,}".format(victim.slot - victim.size_comp),
                    "{:,}".format(max(0, at - victim.offset - victim.size_comp))))
    if not plan["fits"]:
        raise api.overflow(plan)
    if dry:
        return api.ok(plan=plan, file=chk["file"], id=chk["id"], note=note,
                      index=add["index"], reversible=False)

    # Under the Studio's own lock. This is the one write in the file that does
    # not go through `Studio.write`, because `Studio.write` patches an entry
    # that already exists -- but it must not race a level decode or another
    # edit any more than a patch may.
    with s.lock:
        try:
            res = dave.add(chk["file"], data)
        except Exception as ex:
            raise Refusal("not_editable", "The new table was not added.",
                          "Nothing else in the archive was touched -- `add` "
                          "re-decodes the whole table of contents afterwards "
                          "and reports failure rather than leaving a wrong "
                          "name behind.", str(ex))
        # `locate` is built once and cached, and `Level` caches per archive;
        # both would otherwise keep answering as if the file were not there.
        s.game._index = None
        s._drop_level(archive)
        back = dave.read(chk["file"])
    s.note("%s :: added %s (%d bytes, at 0x%X)"
           % (archive, chk["file"], len(data), res["offset"]))
    return api.ok(plan=plan, applied=True, file=chk["file"], id=chk["id"],
                  note=note, reversible=False, index=res["index"],
                  offset=res["offset"], entries=res["count"],
                  # Read back through the freshly re-parsed table of contents,
                  # so "it was added" is a fact and not a hope.
                  verified=back == data,
                  states=len(beh_mod.states(back)))


# ------------------------------------------------------- POST /api/file_add

def post_file_add(handler, s, body):
    """Add a brand-new file, with any legal name and any content, via
    `dave.add`.

    The same machinery as `behaviour_table_clone`, opened up: the caller
    supplies the bytes instead of copying an existing table, so this is the
    route the Archives tab's "Add a file" button speaks to. Everything the
    clone route says about irreversibility holds here too -- there is no
    "remove entry" in this format, so `undo.steps` is honestly 0.
    """
    archive, name, data_b64 = api.require(body, "archive", "name", "data_b64")
    dry = api.is_dry(body)
    dave = _dave(s, archive, writable=True)

    import base64
    import binascii
    try:
        data = base64.b64decode(data_b64, validate=True)
    except (binascii.Error, ValueError) as ex:
        raise Refusal("bad_request", "The file's bytes did not arrive intact.",
                      "Send the content as standard base64 under \"data_b64\".",
                      str(ex))
    if not data:
        raise Refusal("bad_request", "That file is empty.",
                      "An archive entry with no bytes is not something the "
                      "game has ever shipped, so it is refused rather than "
                      "written as an experiment.")

    name = str(name).strip().lower()
    if not name:
        raise Refusal("bad_request", "The new file needs a name.",
                      "Something like mymod_notes.txt -- lower-case letters, "
                      "digits, and . _ - / are all fine.")

    # The engine streams these extensions straight to hardware and hangs on a
    # compressed one, so they are stored raw exactly as every stock copy is.
    from .dave import ALWAYS_RAW_EXTS
    ext = name.rsplit(".", 1)[-1] if "." in name.rsplit("/", 1)[-1] else ""
    compress = ext not in ALWAYS_RAW_EXTS

    # Size it exactly as `add` will store it, then ask `can_add` about THAT
    # number -- asking about the raw length would refuse a compressible file
    # that actually fits.
    import zlib
    stored_len = len(data)
    if compress:
        z = zlib.compressobj(9, zlib.DEFLATED, -15)
        packed = z.compress(data) + z.flush()
        if len(packed) < len(data):
            stored_len = len(packed)

    chk = dave.can_add(name, stored_len)
    if not chk["ok"]:
        reasons = "; ".join(chk["reasons"])
        # A bad name or a duplicate is the caller's to fix; a full archive is
        # a fact about the disc. Different sentences, same honest numbers.
        if any(("already exists" in r) or ("packed name" in r)
               for r in chk["reasons"]):
            raise Refusal("bad_request",
                          "%s cannot take that name: %s." % (_short(archive),
                                                             reasons),
                          "Pick another name.")
        raise Refusal(
            "slot_overflow",
            "%s cannot take a new file: %s." % (_short(archive), reasons),
            "Nothing was written. Adding a file needs three things at once: "
            "a free run between stored files big enough for the bytes, a "
            "spare table-of-contents slot, and room in the name block.")
    add = dave.add(name, data, dry=True, compress=compress)

    # Adding a file consumes free space that belonged to some other entry's
    # SLOT, so that entry loses headroom permanently. Say which, before the
    # button is pressed -- it is the only lasting cost of this operation.
    at = add["payload_offset"]
    victim = next((e for e in dave
                   if e.offset <= at < e.offset + e.slot), None)
    row = api.write_row(archive, name, 0, len(data),
                        add["stored_bytes"], add["payload_room"])
    summary = ("Adds %s to %s (%s stored bytes in a %s-byte free run), at "
               "sorted position %d of %d"
               % (name, _short(archive), "{:,}".format(add["stored_bytes"]),
                  "{:,}".format(add["payload_room"]), add["index"] + 1,
                  dave.count + 1))
    plan = api.plan([row], summary,
                    "added %s (this one cannot be undone)" % name, steps=0)
    note = ("A file can be added to an archive but not removed, so this is "
            "the one edit Ctrl+Z cannot take back. Reset to stock, or restore "
            "a version, is the way out. Boot-test after using it, every time.")
    if victim is not None:
        note += (" It also uses free space inside %s's slot, which drops that "
                 "file's headroom from %s to %s bytes."
                 % (victim.name, "{:,}".format(victim.slot - victim.size_comp),
                    "{:,}".format(max(0, at - victim.offset - victim.size_comp))))
    if not plan["fits"]:
        raise api.overflow(plan)
    if dry:
        return api.ok(plan=plan, file=name, note=note,
                      index=add["index"], entries_after=dave.count + 1,
                      compressed=add["compressed"], reversible=False)

    # Under the Studio's own lock, like the clone route: `add` is the one
    # write that does not go through `Studio.write`, because that patches an
    # entry that already exists -- but it must not race a level decode or
    # another edit any more than a patch may.
    with s.lock:
        try:
            res = dave.add(name, data, compress=compress)
        except ValueError as ex:
            # Refused before any byte moved: a pre-write check failed between
            # the dry run and now (another edit changed the archive).
            raise Refusal("not_editable", "The new file was not added.",
                          "Nothing was written -- the archive changed between "
                          "the check and the write. Check again.", str(ex))
        except Exception as ex:
            # `add` re-decodes the whole table of contents afterwards and
            # raises if it does not read back as promised -- but by then the
            # bytes are down, so this is not a clean refusal and must not be
            # dressed as one.
            raise Refusal("not_editable",
                          "The add went wrong AFTER writing, and the archive "
                          "on the working disc is now suspect.",
                          "Reset to stock, or restore a version, before "
                          "trusting this archive again.", str(ex))
        # `locate` is built once and cached, and `Level` caches per archive;
        # both would otherwise keep answering as if the file were not there.
        s.game._index = None
        s._drop_level(archive)
        back = dave.read(name)
    s.note("%s :: added %s (%d bytes, at 0x%X, TOC index %d)"
           % (archive, name, len(data), res["offset"], res["index"]))
    return api.ok(plan=plan, applied=True, file=name, note=note,
                  reversible=False, index=res["index"], offset=res["offset"],
                  entries=res["count"],
                  # Read back through the freshly re-parsed table of contents,
                  # so "it was added" is a fact and not a hope.
                  verified=back == data)


# --------------------------------------------------------- GET /api/versions

def get_versions(handler, s, q):
    rows = versions_mod.listing()
    state = versions_mod.on_disc(s, rows)
    for r in rows:
        r["on_disc"] = (r["id"] == state["id"])
    return api.ok(on_disc=state, versions=rows)


def get_version_diff(handler, s, q):
    one = lambda k, d=None: (q.get(k) or [d])[0]          # noqa: E731
    a, b = one("a", "stock"), one("b", "disc")
    # Reading the working disc is the slow half -- a whole-disc diff that
    # holds the lock for minutes cold -- so it registers itself: the boot
    # screen and status bar can name it, and Cancel can stop it.
    import time as _time
    from .studio import JobCancelled
    reads_disc = "disc" in (a, b)
    if reads_disc:
        s.job = dict(label="Comparing the working disc to stock",
                     started=_time.time(), cancel=False)
    try:
        counts, rows = versions_mod.compare(s, a, b)
    except KeyError as ex:
        raise Refusal("not_found", "There is no version called %s." % ex.args[0],
                      "")
    except JobCancelled:
        return api.ok(a=a, b=b, cancelled=True, counts={}, files=[])
    finally:
        if reads_disc:
            s.job = None
    return api.ok(a=a, b=b, counts=counts, files=rows)


def post_version_save(handler, s, body):
    name = (body.get("name") or "").strip()
    vid, doc = versions_mod.save_doc(s, name, body.get("notes") or "")
    # Saving a version writes nothing to the disc, so `writes` is empty --
    # which is legal, and honest, and stops the sheet claiming a disc change
    # that is not happening.
    plan = api.plan([], "Saves %d changed file(s) as \"%s\""
                    % (len(doc["files"]), doc["name"]),
                    "Saved version \"%s\" (nothing on the disc changed)"
                    % doc["name"])
    if api.is_dry(body):
        return api.ok(plan=plan, version=dict(id=vid, name=doc["name"],
                                              files=len(doc["files"])))
    path = versions_mod.write_doc(vid, doc)
    s.note("saved version '%s' (%d file(s)) -> %s"
           % (doc["name"], len(doc["files"]), path))
    return api.ok(plan=plan, applied=True,
                  version=dict(id=vid, name=doc["name"], notes=doc["notes"],
                               created=doc["created"], files=len(doc["files"]),
                               path=path))


def post_version_restore(handler, s, body):
    vid = body.get("id")
    if not vid:
        raise Refusal("bad_request", "This needs a version to go back to.", "")
    if vid == versions_mod.AUTOSAVE_ID:
        raise Refusal(
            "not_editable",
            "The session autosave is a safety net, not a version.",
            "Save a version first, then you can come back to it.")
    try:
        doc, writes = versions_mod.restore_writes(s, vid)
    except KeyError:
        raise Refusal("not_found", "There is no version called %s." % vid, "")

    rows = []
    for archive, filename, data, _why in writes:
        try:
            e = s.disc.archive(archive).find(filename)
            before = e.size_full
            rows.append(api.write_row(archive, filename, before, len(data),
                                      api.stored_size(e, data), e.slot))
        except Exception:
            continue
    reverts = sum(1 for w in writes if w[3] == "revert to stock")
    plan = api.plan(rows, "Puts %d file(s) back%s"
                    % (len(rows), ", including %d returned to stock" % reverts
                       if reverts else ""),
                    "Went back to \"%s\"" % doc.get("name", vid))
    if not plan["fits"]:
        raise api.overflow(plan)
    if api.is_dry(body):
        return api.ok(plan=plan)

    group = s.journal.new_group()
    label = "go back to version '%s'" % doc.get("name", vid)
    with s.journal.batch():
        for archive, filename, data, _why in writes:
            s.write(archive, filename, data, label, group)
    s._levels = {}
    s.note("restored version '%s': %d file(s)" % (doc.get("name"), len(writes)))
    return api.ok(plan=plan, applied=True, group=group)


def post_version_delete(handler, s, body):
    import os
    vid = body.get("id")
    if not vid:
        raise Refusal("bad_request", "This needs a version.", "")
    if vid == versions_mod.AUTOSAVE_ID:
        raise Refusal("not_editable",
                      "The session autosave cannot be deleted here.",
                      "It rewrites itself as you work.")
    path = versions_mod.find(vid)
    if path is None:
        raise Refusal("not_found", "There is no version called %s." % vid, "")
    plan = api.plan([], "Deletes the saved version file (the disc is untouched)",
                    "Deleted version %s" % vid)
    if api.is_dry(body):
        return api.ok(plan=plan)
    os.remove(path)
    s.note("deleted version %s" % vid)
    return api.ok(plan=plan, applied=True)


# -------------------------------------------------------------- transfers

def post_transfer_check(handler, s, body):
    kind = body.get("kind")
    chk = transfer_mod.check(s, kind, body.get("target"),
                             body.get("payload_b64"), body.get("options"))
    plan = api.plan(chk.rows, chk.summary, chk.label)
    if not plan["fits"]:
        raise api.overflow(plan)
    token, ttl = TOKENS.put(dict(writes=chk.writes, plan=plan, label=chk.label,
                                 kind=kind))
    return api.ok(subject=chk.subject, options=chk.options, plan=plan,
                  token=token, expires_in=ttl)


def post_transfer_apply(handler, s, body):
    token = body.get("token")
    if not token:
        raise Refusal("bad_request", "This needs a checked transfer.", "")
    item = TOKENS.take(token)
    group = s.journal.new_group()
    with s.journal.batch():
        for archive, filename, data in item["writes"]:
            s.write(archive, filename, data, item["label"], group)
    s._levels = {}
    s.note("imported: %s" % item["label"])
    return api.ok(plan=item["plan"], applied=True, group=group)


def post_transfer_export(handler, s, body):
    """A file, or a refusal -- and the two are told apart by Content-Type.

    The frozen front-end rule is that the response is a file if and only if
    `Content-Type` is not `application/json`. Four export sites used to build
    an `<a download>` and click it blind, so an export that failed with a JSON
    error body saved as `probe.obj` and toasted "Exported probe.obj".
    """
    data, ctype, filename = transfer_mod.export(
        s, body.get("kind"), body.get("target"), body.get("options"))
    handler.send_response(200)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Content-Disposition",
                        'attachment; filename="%s"' % filename)
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    try:
        handler.wfile.write(data)
    except (BrokenPipeError, ConnectionAbortedError):
        pass
    return None      # already answered; see api.respond


def _working(s):
    """The disc to read CURRENT values from, and whether it really is that one.

    `Studio.disc` deliberately falls back to the pristine image whenever the
    working ISO is not held -- during a PCSX2 session, or in the window after a
    deliberate release. That is exactly right for geometry and textures, and
    exactly wrong for "what does this file say now": the panel would answer
    with the stock model and report a swap the user had already made as not
    having happened. Measured, on a released disc: `model_targets` came back
    `render: ncb` while both copies on the working ISO said `mrd`.
    """
    s.ensure_open()
    return (s.game, True) if s.game is not None else (s.disc, False)


# ------------------------------------------------- GET /api/model_targets
#
# A model swap does not go through `_commit`. That path is for LEVEL edits --
# it previews through `Level`, which owns a level's files. A `.cr` is a
# disc-wide file that lives in up to 57 archives at once, and the machinery
# for "write this file into every archive that holds it as ONE undo step" is
# `Studio.edit_creature`. So the plan is built here from the same primitives
# (`api.write_row` / `api.stored_size`) and the write is that one call.

def get_model_targets(handler, s, q):
    from . import modelswap
    one = lambda k, d=None: (q.get(k) or [d])[0]          # noqa: E731
    name = one("name")
    if not name:
        raise Refusal("bad_request", "This needs a creature file.",
                      'Send "name" -- for example ncb.cr.')
    disc, live = _working(s)
    try:
        # `s.vanilla` decides what the palette CONTAINS, the working disc where
        # this character points today. Reading both from the working disc would
        # let the list shrink as it is used -- repoint the last .cr that renders
        # as `ncb` and `ncb` stops being offered, with no way back.
        doc = modelswap.palette(disc, name, stock=s.vanilla)
    except KeyError:
        raise Refusal("not_found", "There is no creature called %s." % name,
                      "Pick one from the list on the left.")
    return api.ok(live=live, **doc)


# --------------------------------------------------------- POST /api/model_swap

def post_model_swap(handler, s, body):
    from . import modelswap
    name, target = api.require(body, "name", "target")
    target = str(target).strip().lower()

    disc, _ = _working(s)
    try:
        doc = modelswap.palette(disc, name, stock=s.vanilla)
    except KeyError:
        raise Refusal("not_found", "There is no creature called %s." % name, "")
    row = next((t for t in doc["targets"] if t["id"] == target), None)
    if row is None:
        # Re-checked here rather than trusted from the picker. The bone count
        # is the reason the list is short, and a route that took whatever it
        # was handed would let a crafted request feed a 52-bone animation to a
        # 59-bone rig.
        f = modelswap.facts(s.vanilla)
        known = f.targets.get(target)
        if known is None:
            raise Refusal("unknown_value",
                          "There is no character model called %s." % target,
                          "Pick one from the list.")
        raise Refusal(
            "not_editable",
            "%s is rigged to %d bones and %s has %d."
            % (target, known["bones"], doc["stem"], doc["current"]["bones"]),
            "Only models with the same bone count are offered: the animations "
            "stay pointed at this character and they carry one curve triple "
            "per bone.")

    rows, skipped, writable = modelswap.plan_swap(
        disc, name, target, stock=s.vanilla)
    if not rows:
        raise Refusal(
            "not_found",
            "No archive that holds %s also holds %s_lods.rsc." % (name, target),
            "The disc keeps every character's model in the same archive as "
            "its .cr, so a swap can only be written where the model already "
            "is.")

    summary = "%s uses %s's model, skeleton, face and morphs" % (doc["stem"],
                                                                 target)
    label = "%s: model → %s" % (doc["stem"], target)
    plan = api.plan(rows, summary, label)
    if not plan["fits"]:
        raise api.overflow(plan)

    warnings = _swap_warnings(doc, row, skipped)
    if api.is_dry(body):
        return api.ok(plan=plan, warnings=warnings, skipped=skipped,
                      target=target, where=writable)

    if s.game is None:
        # `edit_creature` reaches straight into `self.game` and would raise an
        # AttributeError here -- a 500 with "'NoneType' object has no attribute
        # 'archive'" for a situation the user can fix by closing an emulator.
        raise _locked(s)

    group = s.journal.new_group()
    try:
        result = s.edit_creature(
            name, {k: target for k in modelswap.FIELDS},
            dry=False, group=group, setter=modelswap.set_fields, kind="model",
            archives=writable)
    except RuntimeError as ex:
        if _busy(ex):
            raise _disc_busy(ex)
        raise
    if result["failed"]:
        raise Refusal("slot_overflow",
                      "%d of %d copies would not fit."
                      % (len(result["failed"]), len(writable)),
                      "The copies that did fit have been written; press "
                      "Ctrl+Z to take them back.",
                      "; ".join(f["error"] for f in result["failed"]),
                      plan=plan, applied=result["applied"])
    s.note("%s :: %s" % (name, summary))
    return api.ok(plan=plan, applied=True, group=group, target=target,
                  warnings=warnings, skipped=skipped, where=writable)


def _swap_warnings(doc, row, skipped):
    """What the user must be told BEFORE pressing Apply, in plain sentences.

    Returned as data rather than baked into a string so the sheet can render
    each one with its own severity, and so the test can assert that the
    "nothing on this disc proves this" sentence is actually present.
    """
    out = [dict(kind="unproven", text=(
        "No arbitrary cross-character swap exists on the disc. All 59 .cr "
        "files that already borrow a model borrow a variant of the same "
        "character, so the shipped game proves the indirection is read -- it "
        "does not prove this particular pairing works. Load a level in PCSX2 "
        "and look.")) ]

    hb = doc["hitboxes"]
    if row["hitbox_missing"]:
        gone = ", ".join(b for m in row["hitbox_missing"] for b in m["bones"][:6])
        out.append(dict(kind="hitboxes", text=(
            "Hit boxes stay behind. %s name %s, which %s's skeleton does not "
            "have, so those hit boxes have nothing to hang on."
            % (" and ".join(m["file"] for m in row["hitbox_missing"]),
               gone, row["id"]))))
    elif hb["checked"]:
        out.append(dict(kind="hitboxes", ok=True, text=(
            "Hit boxes checked: all %d parent bones named by %s exist in %s's "
            "skeleton. They are NOT swapped -- they keep pointing at bone "
            "names, and every name still resolves."
            % (hb["parents"], " and ".join(f["file"] for f in hb["files"]),
               row["id"]))))
    else:
        out.append(dict(kind="hitboxes", text=(
            "This character has no .hot or .sphs file, so there were no hit "
            "boxes to check.")))

    if not row["face"]:
        out.append(dict(kind="face", text=(
            "%s has no separate face mesh and no morph targets, so this "
            "character loses its expressions." % row["id"])))
    if skipped:
        out.append(dict(kind="partial", text=(
            "%d of %d copies of this creature stay exactly as they are, "
            "because their archive has no %s_lods.rsc. Those levels keep the "
            "old model." % (len(skipped), len(skipped) + row["archives"],
                            row["id"]))))
    return out


# --------------------------------------------------------- GET /api/features

def get_features(handler, s, q):
    d = features.describe()
    return api.ok(build=d["build"], features=d["features"],
                  manifest=d["manifest"], suggestions=d["suggestions"])


# ------------------------------------------------------- GET /api/new_weapon

SHOP_FILE = "invmasterlist.csv"


PLACEHOLDER_OBJ = """# placeholder gun: a plain long block, barrel toward -Z (like the stock guns)
v -0.035 -0.035 -0.60
v 0.035 -0.035 -0.60
v 0.035 0.035 -0.60
v -0.035 0.035 -0.60
v -0.035 -0.035 0.10
v 0.035 -0.035 0.10
v 0.035 0.035 0.10
v -0.035 0.035 0.10
f 1 3 2
f 1 4 3
f 5 6 7
f 5 7 8
f 1 2 6
f 1 6 5
f 2 3 7
f 2 7 6
f 3 4 8
f 3 8 7
f 4 1 5
f 4 5 8
"""


def own_model_stem(newname):
    """The file stem for a clone's own model: its name, lower-cased."""
    return newname.lower()


def _own_model_adds(s, source_model_file, stem):
    """(rows, adds, problems) for giving a clone its own model file.

    The model is a small plain block (the source's real model is 5-8 KB packed
    and the free run in every archive is ~2 KB, so a copy cannot fit). It is
    added to every archive that carries the source's model, so the level the
    weapon is used in can find it. Import your own OBJ over it afterwards.
    """
    from . import modimport
    data, _stats = modimport.build(modimport.from_obj(PLACEHOLDER_OBJ))
    fname = stem + ".mod"
    rows, adds, problems = [], [], []
    for arc in s.disc.locate(source_model_file):
        d = s.disc.archive(arc)
        try:
            dry = d.add(fname, data, dry=True)
        except ValueError as ex:
            problems.append("%s: %s" % (arc, ex))
            continue
        rows.append(api.write_row(arc, fname, 0, len(data),
                                  dry["stored_bytes"], dry["payload_room"]))
        adds.append((arc, fname, data))
    return rows, adds, problems


def _guns_plan(s, newname, source, arsenals, shop, model_stem=None, first=()):
    """Every write a clone would make, as plan rows. Nothing is written here.

    All 62 copies of `swat.guns` are byte-identical on the retail disc
    (measured), so the clone and the deflate are done once and reused -- but
    keyed on the source bytes, not assumed, because a disc that has already
    been edited need not be uniform.
    """
    from . import guns as G

    rows, edits = [], []
    done = {}
    for arc in s.disc.locate("swat.guns"):
        d = s.disc.archive(arc)
        e = d.find("swat.guns")
        data = d.read(e)
        key = hash(data)
        if key not in done:
            done[key] = G.clone(data, source, newname, model=model_stem)
        new = done[key]
        rows.append(api.write_row(arc, "swat.guns", len(data), len(new),
                                  api.stored_size(e, new), e.slot))
        edits.append((arc, "swat.guns", new))

    for name in arsenals:
        for arc in s.disc.locate(name):
            d = s.disc.archive(arc)
            e = d.find(name)
            data = d.read(e)
            new = G.arsenal_add(data, newname, first=(name in first))
            rows.append(api.write_row(arc, name, len(data), len(new),
                                      api.stored_size(e, new), e.slot))
            edits.append((arc, name, new))

    if shop:
        for arc in s.disc.locate(SHOP_FILE):
            d = s.disc.archive(arc)
            e = d.find(SHOP_FILE)
            data = d.read(e)
            new = _shop_add(data, newname, source)
            rows.append(api.write_row(arc, SHOP_FILE, len(data), len(new),
                                      api.stored_size(e, new), e.slot))
            edits.append((arc, SHOP_FILE, new))

    return rows, edits


def _shop_add(data, newname, source):
    """One more row in invmasterlist.csv, copied from the source weapon's.

    ONLY column 0 -- the item key -- takes the new name. The row's other
    references (icon texture, "Unlocks this Item", and the reward/string-table
    key in the last column) keep pointing at the source's, because those keys
    have to resolve to something that exists and a clone brings no string
    table entry of its own. The consequence is visible and intended: the new
    weapon is sold at the source's price and displays under the source's name.
    """
    src = source.encode("latin-1")
    for ln in data.replace(b"\r\n", b"\n").split(b"\n"):
        if ln.startswith(src + b","):
            row = newname.encode("latin-1") + ln[len(src):]
            head = data if data.endswith(b"\n") else data + b"\r\n"
            return head + row + b"\r\n"
    raise Refusal("not_found",
                  "%s has no shop row for %s to copy." % (SHOP_FILE, source),
                  "Clear the shop checkbox, or clone a weapon that is sold.")


def get_new_weapon(handler, s, q):
    """What a clone can be made from, and where the extra line would fit.

    The arsenals carry the interesting numbers. Most of the 100 `.ars` files
    are 27-30 bytes in a 32-byte slot, so whether a character can be handed
    one more weapon depends on the NAME: measured on the vanilla disc, a
    5-character name fits 73 of them and a 31-character name fits 39. It is
    not only the length -- these files are small enough that the deflate cost
    moves with the letters, and at 16 of the 29 usable lengths two random
    spellings disagreed about which arsenals fit. So this is recomputed for
    the name actually typed rather than estimated client-side.

    `?name=` is optional; without it the list is costed for an 11-character
    placeholder so the panel has something honest to show before you type.
    """
    from . import guns as G

    one = lambda k, d=None: (q.get(k) or [d])[0]      # noqa: E731
    name = (one("name") or "").strip()
    probe = name if G.check_name(name)[0] else "Peacekeeper"

    data = s.disc.read("SWAT.DAT", "swat.guns")
    weapons = [w.name for w in G.parse(data)]

    d = s.disc.archive("SWAT.DAT")
    arsenals = []
    for e in sorted((e for e in d if e.ext == "ars"), key=lambda e: e.name):
        try:
            src = d.read(e)
            new = G.arsenal_add(src, probe)
        except Exception:
            continue
        stored = api.stored_size(e, new)
        arsenals.append(dict(file=e.name, holds=[r["name"] for r in G.arsenal(src)],
                             stored=stored, slot=e.slot,
                             headroom=e.slot - stored, fits=stored <= e.slot))

    ok, why = G.check_name(name) if name else (False, "")
    return api.ok(weapons=weapons, source_default="Old_Pistol",
                  copies=len(s.disc.locate("swat.guns")),
                  max_name=G.MAX_TYPENAME, name=name, name_ok=ok, name_why=why,
                  taken=name in weapons, probe=probe,
                  arsenals=arsenals, shop_file=SHOP_FILE)


# ---------------------------------------------------- POST /api/weapon_clone

def post_weapon_clone(handler, s, body):
    """Add a 56th weapon to the rack, cloned from an existing one.

    Appending is safe because the engine looks weapons up by NAME -- see the
    header comment in `guns.py` for the three addresses in the executable that
    say so. Every write this makes is one undo step.
    """
    from . import guns as G

    source, name = api.require(body, "source", "name")
    name = str(name).strip()
    ok, why = G.check_name(name)
    if not ok:
        raise Refusal("bad_request", why, "Pick a shorter or simpler name.")

    data = s.disc.read("SWAT.DAT", "swat.guns")
    have = [w.name for w in G.parse(data)]
    if name in have:
        raise Refusal("bad_request",
                      "There is already a weapon called %s." % name,
                      "Pick another name, or edit that weapon in the list.")
    if source not in have:
        raise Refusal("not_found", "There is no weapon called %s." % source,
                      "Pick a source from the weapon list.")

    arsenals = [str(a) for a in (body.get("arsenals") or [])]
    unknown = [a for a in arsenals
               if not a.endswith(".ars") or not s.disc.locate(a)]
    if unknown:
        raise Refusal("unknown_value",
                      "There is no arsenal called %s." % ", ".join(unknown),
                      "Tick arsenals from the list; the names come from the "
                      "disc.")
    shop = bool(body.get("shop"))
    first = set(str(a) for a in (body.get("first_in") or []))
    if body.get("first"):
        first |= set(arsenals)
    own = body.get("own_model", True)
    own = True if own is None else bool(own)

    stem = own_model_stem(name) if own else None
    adds = []
    try:
        rows, edits = _guns_plan(s, name, source, arsenals, shop,
                                 model_stem=stem, first=first)
        if own:
            src_w = next(w for w in G.parse(data) if w.name == source)
            src_model = (src_w.fields.get("ModelName", {}).get("text") or "")
            src_model = src_model.strip().strip('"') + ".mod"
            mrows, adds, bad = _own_model_adds(s, src_model, stem)
            if bad:
                raise Refusal("slot_overflow",
                              "The new weapon's own model file does not fit: "
                              + bad[0],
                              "Untick \"own model\" to share the source's "
                              "model, or pick a shorter name.")
            rows = rows + mrows
    except Refusal:
        raise
    except (KeyError, ValueError) as ex:
        raise Refusal("bad_request", str(ex), "")

    summary = ("Add %s to the rack, cloned from %s: %d copies of swat.guns"
               % (name, source, len(s.disc.locate("swat.guns"))))
    if arsenals:
        summary += ", %d arsenal(s)" % len(arsenals)
    if shop:
        summary += ", " + SHOP_FILE
    if own:
        summary += ", plus its own model %s.mod in %d archives" % (stem, len(adds))
    plan = api.plan(rows, summary, "new weapon %s" % name)
    if not plan["fits"]:
        raise api.overflow(plan)
    if api.is_dry(body):
        return api.ok(plan=plan, weapon=name, source=source)

    group = s.journal.new_group()
    try:
        # 62 copies of swat.guns and up to 100 arsenals is far past the ~50
        # where the session autosave -- which rewrites the whole history
        # document per entry -- starts costing more than the writes do.
        with s.journal.batch():
            for arc, filename, new in edits:
                s.write(arc, filename, new, "new weapon %s" % name, group)
    except RuntimeError as ex:
        if _busy(ex):
            raise _disc_busy(ex)
        raise
    if adds:
        # Adding a file is the one write Ctrl+Z cannot take back (see
        # post_add_file). Done AFTER every other write and dry-checked for
        # every archive above, so a refusal never leaves it half-added.
        with s.lock:
            for arc, fname, mdata in adds:
                s.disc.archive(arc).add(fname, mdata)
            s.game._index = None
            for arc, _f, _d in adds:
                s._drop_level(arc)
    s.note("new weapon %s cloned from %s (%d file writes, %d model adds)"
           % (name, source, len(edits), len(adds)))
    return api.ok(plan=plan, applied=True, group=group,
                  weapon=name, source=source, model=(stem + ".mod") if own else None,
                  model_copies=len(adds), reversible=not adds)


# ------------------------------------------------------- GET /api/csv_files

def get_csv_files(handler, s, q):
    return api.ok(**sheets_mod.catalogue(s.disc))


# ------------------------------------------------------------- GET /api/csv

def _csv_read(s, name, disc=None):
    """The bytes of one .csv, from SWAT.DAT -- which holds all 1,022."""
    disc = disc or s.disc
    if not name or not str(name).lower().endswith(".csv"):
        raise Refusal("bad_request", "This needs the name of a .csv file.",
                      "Pick one from the list on the left.")
    name = str(name).lower()
    try:
        return name, disc.archive("SWAT.DAT").read(name)
    except Exception as ex:
        raise Refusal("not_found", "There is no file called %s on the disc."
                      % name, "", str(ex))


def get_csv(handler, s, q):
    one = lambda k, d=None: (q.get(k) or [d])[0]          # noqa: E731
    name, data = _csv_read(s, one("name"))
    sheet = csvfile.parse(data)
    fam = sheets_mod.classify(name)
    label, why = sheets_mod.FAMILY_LABEL.get(fam, (fam, ""))
    return api.ok(name=name, family=fam, family_label=label, family_why=why,
                  copies=sheets_mod.copies(s.disc, name),
                  annotation=sheets_mod.annotate(s.disc, name, sheet),
                  **sheet.to_json())


# ------------------------------------------------------- GET /api/csv_sound

def get_csv_sound(handler, s, q):
    return api.ok(**sheets_mod.sound_map(s.disc))


# ------------------------------------------------------ GET /api/csv_roster

def get_csv_roster(handler, s, q):
    return api.ok(**sheets_mod.roster(s.disc))


# ------------------------------------------------------- POST /api/csv_set

def _csv_changes(body, sheet, name):
    """Validate the requested cells before a single byte is encoded.

    Everything that can be wrong with a cell edit is wrong here, once, with a
    sentence -- rather than 62 identical exceptions inside the per-archive
    loop, one per copy of the file.
    """
    changes = body.get("changes")
    if not isinstance(changes, dict) or not changes:
        raise Refusal("bad_request", "Nothing was asked to change.",
                      "Type in a cell first.")
    clean = {}
    for path, value in changes.items():
        try:
            r, c = csvfile._key(path)
        except KeyError:
            raise Refusal("bad_request", "%s is not a cell address." % path,
                          "Cells are addressed row.column, both counted "
                          "from 0.")
        try:
            sheet.cell(r, c)
        except KeyError as ex:
            raise Refusal("not_found",
                          "%s has no cell at row %d, column %d."
                          % (name, r + 1, c + 1),
                          "Reload the file -- it may have been edited "
                          "elsewhere.", str(ex))
        text = "" if value is None else str(value)
        if "\r" in text or "\n" in text:
            raise Refusal("not_editable", "A cell cannot span lines.",
                          "Remove the line break from row %d, column %d."
                          % (r + 1, c + 1))
        try:
            text.encode("latin-1")
        except UnicodeEncodeError:
            # The disc is latin-1: 22 .csv files carry bytes above 0x7E and
            # none carries a character outside it. Pasting a curly quote or an
            # em dash from a web page is the way this happens, and silently
            # substituting `?` would be a value the user never typed.
            raise Refusal("not_editable",
                          "Row %d, column %d has a character the disc cannot "
                          "store." % (r + 1, c + 1),
                          "These files hold plain Latin-1 text. Smart quotes "
                          "and dashes pasted from a browser are the usual "
                          "cause.")
        clean["%d.%d" % (r, c)] = text
    return clean


def post_csv_set(handler, s, body):
    name = body.get("name")
    if s.game is None:
        raise _locked(s)
    # Read through `s.game`, never `s.disc`. During a PCSX2 handover `s.disc`
    # answers with the PRISTINE image, and a read-modify-write through it
    # would splice stock bytes over the user's work without a word.
    name, data = _csv_read(s, name, disc=s.game)
    sheet = csvfile.parse(data)
    changes = _csv_changes(body, sheet, name)

    # Encode once, here, so a quoting rule that could not store the value is a
    # refusal with a sentence instead of an identical failure in every copy.
    try:
        csvfile.apply(data, changes)
    except csvfile.Unwritable as ex:
        raise Refusal("not_editable",
                      "%s cannot hold that value." % name,
                      "It would not read back as what you typed.", str(ex))

    n = len(changes)
    where = ", ".join(sorted(changes)[:3]) + ("…" if n > 3 else "")
    summary = "%s: %d cell%s (%s)" % (name, n, "" if n == 1 else "s", where)
    label = "csv %s" % summary
    dry = api.is_dry(body)
    everywhere = body.get("everywhere", True)
    targets = None if everywhere else ["SWAT.DAT"]

    # `edit_creature` is already "write this file into every archive that
    # holds it, as ONE undo step", per-copy slot check included. Reused, not
    # forked -- `setter=` is the whole reason that parameter exists.
    try:
        rep = s.edit_creature(name, changes, dry=True,
                              setter=csvfile.set_fields, kind="csv",
                              archives=targets)
    except RuntimeError as ex:
        if _busy(ex):
            raise _disc_busy(ex)
        raise
    broken = [f for f in rep["failed"] if "stored" not in f]
    if broken:
        raise Refusal("not_editable", "%s could not be rewritten: %s"
                      % (name, broken[0].get("error", "")), "")
    rows = [api.write_row(r["archive"], name, r["bytes_before"],
                          r["bytes_after"], r["stored"], r["slot"])
            for r in rep["applied"] + rep["failed"]]
    plan = api.plan(rows, summary, label)
    if not plan["fits"]:
        ref = api.overflow(plan)
        # `api.overflow`'s advice -- shorten a name, drop an optional field --
        # is written for a config file. A .csv row has neither, and the
        # surprising case here is a change of exactly the SAME LENGTH that
        # still does not fit: 1,021 of the 1,022 .csv on this disc are stored
        # DEFLATEd, so the slot is checked after compression. Measured across
        # the 28 zero-headroom files, 71 of 123 same-length single-cell edits
        # overflow -- and on snd_cfg_lvl_woods.csv, `65` -> `665` (one byte
        # LONGER) fits where `65` -> `66` does not.
        ref.fix = ("This file is stored compressed, so what counts is how "
                   "well it zips, not how long it is: a value the same length "
                   "as the old one can still be too big, and a longer one can "
                   "fit. Try a different value, or edit a copy with spare "
                   "bytes.")
        raise ref
    if dry:
        return api.ok(plan=plan, copies=len(rows), name=name)

    group = s.journal.new_group()
    try:
        # snd_00.csv lives in 63 archives -- the most any .csv here does --
        # which is past the ~50 where the session autosave, rewriting the
        # whole history document per entry, costs more than the writes do.
        with s.journal.batch():
            done = s.edit_creature(name, changes, dry=False, group=group,
                                   setter=csvfile.set_fields, kind="csv",
                                   archives=targets)
    except RuntimeError as ex:
        if _busy(ex):
            raise _disc_busy(ex)
        raise
    s.note("%s :: %s" % (name, summary))
    return api.ok(plan=plan, applied=True, group=group, name=name,
                  copies=len(done["applied"]), failed=done["failed"])


# ------------------------------------------------- the per-character family
#
# `.dam .sphs .ars .hot .tail .acc .armr .rep` -- 939 files, one editor. See
# `charfile.py` for what each format is and why props cannot address six of
# them.
#
# Everything is read out of SWAT.DAT. Measured: all 939 are in SWAT.DAT, and
# of the 487 that also live in a level archive, NOT ONE copy differs from the
# SWAT.DAT one. So one read is representative and the write still goes to
# every copy.

_CHAR_EXTS = tuple(CF.ORDER)

# `.msd` and `.skel` are character stat formats like the other eight, but they
# are NOT in `charfile.KINDS`: neither is a flat list of named fields, so each
# has its own reader (`msd.py`, `skeledit.py`) and its own pair of routes.
#
# The roster still has to advertise them. It is what the Rig tab builds its
# per-character buttons from, so a kind the roster does not name is a kind the
# app cannot open -- which is why `msd_set`/`skel_set` existed and worked for
# a day while a tester still could not edit a move set (REQ-009).
#
# Declared here rather than added to `charfile.KINDS` because `charfile` does
# not read either format and should not claim to.
_EXTRA_KINDS = {
    "msd": dict(title="Movement",
                blurb="How this character moves, per weapon stance."),
    # The count is of THIS roster's own population, not of the 181 characters
    # that own a `.cr`. Two different denominators, and quoting the wrong one
    # in a string attached to this list is the kind of claim M3 existed to
    # retire: 148 of the 301 characters here own a `.skel`.
    "skel": dict(title="Skeleton",
                 blurb="Bone offsets. 148 of the 301 characters have one; "
                       "the rest simply have no skeleton file."),
}


def _swat(s, disc=None):
    try:
        return (disc or s.disc).archive("SWAT.DAT")
    except Exception as ex:
        raise Refusal("not_found", "SWAT.DAT could not be opened.", "", str(ex))


def _char_split(name):
    stem, _, ext = (name or "").rpartition(".")
    ext = ext.lower()
    if not stem or ext not in CF.KINDS:
        raise Refusal(
            "bad_request",
            "%r is not one of the character files this panel edits." % name,
            "It handles %s." % ", ".join("." + e for e in _CHAR_EXTS))
    return stem, ext


def _char_ctx(dave, stem, ext):
    """Whatever the format's reader declared it `wants`.

    The bone list is the honest part. 635 of 635 `.sphs` parents resolve
    against the same-stem `.skel` -- but only 127 of the 199 characters have
    one on the disc at all. When there is no skeleton the reader gets an empty
    set and renders free text, because a picker built from a guess is worse
    than a text box.
    """
    ctx = {}
    if ext in ("sphs", "hot"):
        try:
            from . import skeleton
            ctx["bones"] = {b.name for b in
                            skeleton.parse(dave.read(stem + ".skel"))[2]}
        except Exception:
            ctx["bones"] = set()
    if ext == "ars":
        cat = []
        try:
            for line in dave.read("swat.guns").decode("latin-1").splitlines():
                bits = line.replace(":", " ").replace("=", " ").split()
                if len(bits) >= 2 and bits[0] == "TypeName":
                    cat.append(bits[1])
        except Exception:
            pass
        ctx["catalogue"] = cat
    if ext == "rep":
        # All 4,187 `.anim` stems on the disc are in SWAT.DAT (measured: the
        # SWAT-only set and the 66-archive set are the same size and one
        # contains the other), so the full sweep costs 0.80 s to learn nothing
        # the 0.02 s one did not.
        ctx["anims"] = {e.name[:-5].lower() for e in dave if e.ext == "anim"}
    return ctx


def get_char_roster(handler, s, q):
    dave = _swat(s)
    stems = {}
    extra = {}
    for e in dave:
        base = e.name.rsplit(".", 1)[0]
        if e.ext in CF.KINDS:
            stems.setdefault(base, {})[e.ext] = e.name
        elif e.ext in _EXTRA_KINDS:
            extra.setdefault(base, {})[e.ext] = e.name
    # `.msd`/`.skel` ADD kinds to characters the roster already lists; they do
    # not create entries of their own. 88 stems in SWAT.DAT own a `.skel` and
    # nothing else, and they are not characters -- `akabag`, `arw`,
    # `boarda_anim`, `animpropcha`: rigged props that happen to carry a
    # skeleton. Letting a skeleton confer membership grew the character list
    # from 301 to 390 and put `boarda_anim` in a picker labelled Characters.
    for base, files in extra.items():
        if base in stems:
            stems[base].update(files)
    rows = [dict(stem=k, files=v, count=len(v)) for k, v in sorted(stems.items())]
    kinds = [dict(ext=k, title=CF.KINDS[k]["title"],
                  blurb=CF.KINDS[k]["blurb"]) for k in _CHAR_EXTS]
    # Ownership is patchy and that is ORDINARY, not an error: .msd is on all
    # 181 characters but .skel on only 124, .tail on 16, .acc on 11. A
    # character simply has no entry for a kind it does not own, and the Rig
    # tab draws no button for it -- the same way it already handles .tail.
    kinds += [dict(ext=k, title=v["title"], blurb=v["blurb"])
              for k, v in _EXTRA_KINDS.items()]
    return api.ok(characters=rows, kinds=kinds,
                  total=sum(r["count"] for r in rows))


def get_char_file(handler, s, q):
    one = lambda k, d=None: (q.get(k) or [d])[0]          # noqa: E731
    name = one("name")
    stem, ext = _char_split(name)
    dave = _swat(s)
    try:
        data = dave.read(name)
    except Exception:
        raise Refusal("not_found", "SWAT.DAT has no file called %s." % name,
                      "Pick a character from the list on the left.")
    try:
        doc = CF.read(ext, data, **_char_ctx(dave, stem, ext))
    except Exception as ex:
        raise Refusal("not_found", "%s could not be read." % name, "", repr(ex))
    # Read through `s.disc`, like every other read route: during a PCSX2
    # handover it answers with the pristine image so browsing keeps working.
    # That can show stock values while the working disc holds an edit -- and
    # it is safe here only because a write addresses ONE span. A field the
    # panel never touched is never rewritten, so a stale read cannot splice
    # stock bytes over somebody's work.
    e = dave.find(name)
    try:
        copies = len(s.disc.locate(name))
    except Exception:
        copies = 1
    doc.update(name=name, stem=stem, copies=copies, bytes=len(data),
               stored=e.size_comp, slot=e.slot,
               headroom=e.slot - e.size_comp, rung="measured")
    return api.ok(**doc)


def _char_changes(body):
    changes = body.get("changes")
    if not isinstance(changes, dict) or not changes:
        raise Refusal("bad_request", "Nothing was asked to change.", "")
    clean = {}
    for k, v in changes.items():
        text = "" if v is None else str(v)
        if any(c in text for c in "\r\n"):
            raise Refusal("bad_request", "A value cannot span lines.", "")
        try:
            text.encode("latin-1")
        except UnicodeEncodeError:
            raise Refusal(
                "not_editable",
                "%s has a character the disc cannot store." % k,
                "These files are plain Latin-1. A curly quote or an em dash "
                "pasted from a browser is the usual cause.")
        clean[str(k)] = text
    return clean


def _char_overflow_advice(ext, failed, grow):
    """What to do about a character-stat value that outgrew its slot.

    The advice this replaced stated a property of the ROUTE as a property of
    the FILES, and was false either way: `Dave.patch(grow=True)` relocates a
    payload into the archive's free run, and on stock 747 of the 939 files in
    this family fit SWAT.DAT's. rdr-fmt measured it; CAPABILITIES C-018.
    (The retired sentence is quoted in that register entry and deliberately
    not repeated here -- the M3 check greps the whole toolkit for it.)

    So there are three different situations here and they need three different
    sentences: the arsenal case, the one where moving the file would work and
    the user has simply not asked for it, and the one where it genuinely will
    not fit anywhere.
    """
    if ext == "ars":
        return ("Every weapon you add is a whole new line plus a wider "
                "NumTypes. Drop one you do not need, or pick a character "
                "whose arsenal has more room.")
    if not grow and any(f.get("grow_possible") for f in failed):
        return ("This edit writes into the slot the file already has, and the "
                "value no longer fits it -- but the archive has a free run "
                "big enough to hold the file somewhere else. Tick \"allow "
                "files to grow\" in View settings to move it there, or write "
                "the number with the digits it already had.")
    return ("This edit writes into the slot the file already has, and there "
            "is no free run in the archive big enough to move the file to "
            "instead. Write the number with the digits it already had, or "
            "shorten another value in the same file to make room.")


def _write_char_family(s, body, name, setter, kind, title, ext=None,
                       unknown=None):
    """The write half of every character-stat route, once.

    `.dam .sphs .ars .hot .tail .acc .armr .rep` go through `charfile`,
    `.msd` through `msd` and `.skel` through `skeledit`, and all three want
    the identical envelope: read through `s.game`, dry-run every copy, refuse
    as a whole with a plan, then write them all under one undo group. Three
    copies of that is three chances for them to disagree about whether a write
    fits, which is the failure this family can least afford -- the median
    `.sphs` copy has ONE spare byte.

    The only things that differ are the four parameters: which setter, what to
    call it in the journal, which exception the setter raises for a path it
    does not address, and -- for `charfile` only -- the extension, which
    `_char_overflow_advice` uses to tell the `.ars` case from the rest.
    """
    changes = _char_changes(body)
    n = len(changes)

    if s.game is None:
        raise _locked(s)
    # Through `s.game`, never `s.disc`: during a handover `s.disc` answers with
    # the PRISTINE image, so a read-modify-write through it would splice stock
    # bytes over the user's own work and report success.
    try:
        setter(_swat(s, disc=s.game).read(name), changes)
    except (unknown or CF.Unknown) as ex:
        raise Refusal("not_editable", str(ex),
                      "Reload the character -- this file may have changed "
                      "underneath the panel.")
    except Exception as ex:
        raise Refusal("not_editable", "%s could not be rewritten." % name,
                      "", repr(ex))

    what = ", ".join(sorted(changes)[:3])
    summary = "%s: %d value%s (%s%s)" % (name, n, "" if n == 1 else "s", what,
                                         "…" if n > 3 else "")
    label = "%s %s" % (title.lower(), summary)
    targets = None if body.get("everywhere", True) else ["SWAT.DAT"]
    # The same "allow files to grow" tick file_drop reads. Off by default:
    # growing RELOCATES the payload into the archive's free run, and that is
    # the user's call to make, not ours to make for them.
    grow = bool(body.get("grow"))

    # `edit_creature` already is "write this file into every archive that holds
    # it, checking each against its OWN slot, as one undo step". `setter=` is
    # why that parameter exists; forking the loop would be a second place that
    # could disagree about whether a write fits.
    try:
        rep = s.edit_creature(name, changes, dry=True, setter=setter,
                              kind=kind, archives=targets, grow=grow)
    except RuntimeError as ex:
        if _busy(ex):
            raise _disc_busy(ex)
        raise
    broken = [f for f in rep["failed"] if "stored" not in f]
    if broken:
        raise Refusal("not_editable", "%s could not be rewritten in %s: %s"
                      % (name, broken[0]["archive"], broken[0].get("error", "")),
                      "")
    rows = []
    for r in rep["applied"] + rep["failed"]:
        row = api.write_row(r["archive"], name, r["bytes_before"],
                            r["bytes_after"], r["stored"], r["slot"])
        # `api.write_row` derives `fits` from stored-against-slot. That is the
        # right question for a write that stays put and the WRONG one for a
        # copy about to relocate. Without this the plan took a row
        # `edit_creature` had already accepted as a grow, called it an
        # overflow and refused -- so threading `grow` into the write path
        # changed nothing a user could actually do. Boot-testing the grow path
        # is the only reason this was caught rather than shipped
        # believed-working. `headroom` stays negative on purpose: that is the
        # truth about the slot, and the plan still shows it.
        if r.get("grown"):
            row = dict(row, fits=True, grown=True, gap=r.get("gap"))
        rows.append(row)
    plan = api.plan(rows, summary, label)
    if not plan["fits"]:
        # This is the common refusal for this family, not an edge case: the
        # median `.sphs` copy has ONE spare byte and 18 of the 199 have none.
        # `api.overflow`'s standard advice -- shorten a name, drop an optional
        # field -- is right for a level and wrong here: a `.dam` is eight rows
        # of bare numbers with no names and no comments to shorten. Same code,
        # same plan, same byte counts; only the sentence that tells the user
        # what to do about it changes.
        worst = min((w for w in rows if not w["fits"]),
                    key=lambda w: w["headroom"])
        raise Refusal(
            "slot_overflow",
            "%s needs %s bytes of a %s-byte slot in %s (over by %s)."
            % (name, "{:,}".format(worst["stored"]),
               "{:,}".format(worst["slot"]), worst["archive"],
               "{:,}".format(worst["stored"] - worst["slot"])),
            _char_overflow_advice(ext, rep["failed"], grow),
            "", plan=plan)
    if api.is_dry(body):
        return api.ok(plan=plan, name=name, copies=len(rows))

    group = s.journal.new_group()
    try:
        with s.journal.batch():
            done = s.edit_creature(name, changes, dry=False, group=group,
                                   setter=setter, kind=kind,
                                   archives=targets, grow=grow)
    except RuntimeError as ex:
        if _busy(ex):
            raise _disc_busy(ex)
        raise
    s.note("%s :: %s" % (name, summary))
    return api.ok(plan=plan, applied=True, group=group, name=name,
                  copies=len(done["applied"]), failed=done["failed"])


def post_char_file_set(handler, s, body):
    name = body.get("name")
    _stem, ext = _char_split(name)
    return _write_char_family(s, body, name, CF.setter_for(ext), "body",
                              CF.KINDS[ext]["title"], ext=ext,
                              unknown=CF.Unknown)


def post_msd_set(handler, s, body):
    """`.msd` -- how a character MOVES, per weapon stance.

    Paths are full (`MoveSetData/MobileData/rflSneakStandGaitset/SpeedBand/
    RunSpeed`), not bare field names, because `RunSpeed` exists once per
    gaitset -- eight times in most files.
    """
    name = body.get("name")
    if not str(name or "").lower().endswith(".msd"):
        raise Refusal("bad_request",
                      "%r is not a move-set file." % name,
                      "This route edits .msd only.")
    return _write_char_family(s, body, name, MSD.set_fields, "moveset",
                              "move set", unknown=MSD.Unknown)


def post_skel_set(handler, s, body):
    """`.skel` -- bone offsets, one component per path.

    `<bone>/offset/<x|y|z>`, so an edit rewrites one number's byte span and
    leaves the file otherwise identical.
    """
    name = body.get("name")
    if not str(name or "").lower().endswith(".skel"):
        raise Refusal("bad_request",
                      "%r is not a skeleton." % name,
                      "This route edits .skel only.")
    return _write_char_family(s, body, name, SKE.set_fields, "skeleton",
                              "skeleton", unknown=SKE.Unknown)


def get_msd_file(handler, s, q):
    one = lambda k, d=None: (q.get(k) or [d])[0]          # noqa: E731
    name = one("name")
    dave = _swat(s)
    try:
        data = dave.read(name)
    except Exception:
        raise Refusal("not_found", "SWAT.DAT has no file called %s." % name,
                      "Pick a character from the list on the left.")
    e = dave.find(name)
    return api.ok(name=name, groups=MSD.read(data),
                  gaitsets=[dict(name=g, stance=MSD.stance_of(g))
                            for g in MSD.gaitsets(data)],
                  bytes=len(data), stored=e.size_comp, slot=e.slot,
                  headroom=e.slot - e.size_comp, rung="measured")


def get_skel_file(handler, s, q):
    one = lambda k, d=None: (q.get(k) or [d])[0]          # noqa: E731
    name = one("name")
    dave = _swat(s)
    try:
        data = dave.read(name)
    except Exception:
        raise Refusal("not_found", "SWAT.DAT has no file called %s." % name,
                      "Pick a character from the list on the left.")
    e = dave.find(name)
    return api.ok(name=name, bytes=len(data), stored=e.size_comp, slot=e.slot,
                  headroom=e.slot - e.size_comp, rung="measured",
                  **SKE.doc(data))


# ----------------------------------------------------------------- dispatch
#
# A feature that is off is not in this table, so its route is not registered
# and calling it returns the ordinary `no such route` 404. The code stays in
# the .py and is unreachable, which is honest and costs nothing.

_GET = {
    "anims_export_info": (animbatch_mod.get_anims_export_info, None),
    "anims_export_status": (animbatch_mod.get_anims_export_status, None),
    "cast": (get_cast, "swap_character"),
    "enemy": (get_enemy, "enemy_fields"),
    "attrs": (get_attrs, "attr_edit"),
    # Read side of the Links panel. It carried None -- no feature -- while its
    # write route link_set carries links_edit, so on a release build the panel
    # was stripped but its data still answered. Every other internal read route
    # in this table names its feature; this was an oversight, not a decision.
    "links": (get_links, "links_edit"),
    "versions": (get_versions, "versions"),
    "version_diff": (get_version_diff, "versions"),
    "share_info": (share_mod.get_share_info, "versions"),
    "model_targets": (get_model_targets, "swap_model"),
    "new_weapon": (get_new_weapon, "new_weapon"),
    "behaviour_tables": (get_behaviour_tables, "behaviour_tables"),
    "behaviour_table": (get_behaviour_table, "behaviour_tables"),
    "behaviour_state": (get_behaviour_state, "behaviour_numbers"),
    "menus": (get_menus, "menu_edit"),
    "menu": (get_menu, "menu_edit"),
    "csv_files": (get_csv_files, "csv_editor"),
    "csv": (get_csv, "csv_editor"),
    "csv_sound": (get_csv_sound, "csv_editor"),
    "csv_roster": (get_csv_roster, "csv_editor"),
    "char_roster": (get_char_roster, "char_files"),
    "char_file": (get_char_file, "char_files"),
    "msd_file": (get_msd_file, "char_files"),
    "skel_file": (get_skel_file, "char_files"),
    "features": (get_features, None),
}

_POST = {
    "anims_export": (animbatch_mod.post_anims_export, None),
    "enemy_set": (post_enemy_set, "enemy_fields"),
    "enemy_place": (post_enemy_place, "place_enemy"),
    "attr_set": (post_attr_set, "attr_edit"),
    "model_swap": (post_model_swap, "swap_model"),
    "weapon_clone": (post_weapon_clone, "new_weapon"),
    "behaviour_table_set": (post_behaviour_table_set, "behaviour_tables"),
    "behaviour_state_set": (post_behaviour_state_set, "behaviour_numbers"),
    "behaviour_table_clone": (post_behaviour_table_clone, "behaviour_clone"),
    "behaviour_table_rename": (post_behaviour_table_rename, "behaviour_clone"),
    "behaviour_state_rename": (post_behaviour_state_rename, "behaviour_clone"),
    "file_add": (post_file_add, "file_add"),
    "menu_set": (post_menu_set, "menu_edit"),
    "node_rotate": (post_node_rotate, "rotate"),
    "vertex_set": (post_vertex_set, "vertex_edit"),
    "link_set": (post_link_set, "links_edit"),
    "version_save": (post_version_save, "versions"),
    "version_restore": (post_version_restore, "versions"),
    "version_delete": (post_version_delete, "versions"),
    "export_patch": (share_mod.post_export_patch, "versions"),
    "export_iso": (share_mod.post_export_iso, "versions"),
    "export_open": (share_mod.post_export_open, "versions"),
    "mod_import": (share_mod.post_mod_import, "versions"),
    "pack_export": (modpack_mod.post_pack_export, "versions"),
    "pack_apply": (modpack_mod.post_pack_apply, "versions"),
    "transfer_check": (post_transfer_check, "transfer"),
    "transfer_apply": (post_transfer_apply, "transfer"),
    "transfer_export": (post_transfer_export, "transfer"),
    "csv_set": (post_csv_set, "csv_editor"),
    "char_file_set": (post_char_file_set, "char_files"),
    "msd_set": (post_msd_set, "char_files"),
    "skel_set": (post_skel_set, "char_files"),
}


def _live(table, route):
    entry = table.get(route)
    if entry is None:
        return None
    fn, feature = entry
    if feature and not features.enabled(feature):
        return None
    return fn


def has_get(route):
    return _live(_GET, route) is not None


def has_post(route):
    return _live(_POST, route) is not None


def _guarded(fn):
    """Turn "the emulator has the disc" into a refusal wherever it surfaces.

    Several routes reach `Studio.diff` or `Studio.write` indirectly -- the
    Versions banner diffs the whole ISO, a transfer writes through the same
    choke point -- and all of them raise the same RuntimeError. Catching it in
    one place means no route has to remember, and none of them answers a
    fixable situation with a 500.
    """
    def run():
        try:
            return fn()
        except Refusal:
            raise
        except RuntimeError as ex:
            if _busy(ex):
                raise _disc_busy(ex)
            raise
    return run


def handle_get(handler, studio, route, q):
    fn = _live(_GET, route)
    return api.respond(handler, _guarded(lambda: fn(handler, studio, q)),
                       "reading %s" % route)


def handle_post(handler, studio, route, body):
    fn = _live(_POST, route)
    return api.respond(handler, _guarded(lambda: fn(handler, studio, body)),
                       "writing %s" % route)
