"""Every route added by the 2026-08 redesign, over real HTTP.

Needs the Studio running on 8731. This is the live half of
`verify_enemy_edit.py`, which does the same work offline against the pristine
disc: this one proves the wire format.

What it asserts, in order:

  * the envelope -- success is `ok:true`, a refusal is HTTP **200** with
    `ok:false` and a `problem` from the frozen code set, and neither is an
    exception the front end has to catch;
  * `dry` really does default to true, so a request that forgets to say so
    writes nothing;
  * a refusal still carries the `plan`, because "it does not fit" without a
    byte count is not a message;
  * `plan.undo.steps` is 1 and the journal agrees;
  * one write, applied and then undone, leaves the disc byte-identical.

The one write it performs is applied and undone within the run, and the file
is compared before and after. If the run dies mid-way the working ISO can be
left with a single changed field in one level, which `Reset to stock` fixes;
nothing else is touched and the pristine disc is never opened for writing.
"""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = "http://127.0.0.1:8731/api/"
ARCHIVE = "LVLS_SNG/SHER70.DAT"
FAILURES = []


def say(text):
    """Print without assuming the console can encode what the API returned.

    Undo labels carry an em dash and an arrow, exactly as the contract
    specifies. A Windows console is cp1252 and raises on both, which would
    make this script fail on the one thing it is checking. The server itself
    never prints a label -- it only ever puts them in JSON, which escapes
    them -- so this is the test's problem alone.
    """
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        text.encode(enc)
    except (UnicodeEncodeError, LookupError):
        text = text.encode(enc, "backslashreplace").decode(enc, "replace")
    print(text)


def check(ok, what, detail=""):
    say(("  ok   " if ok else "  FAIL ") + what
        + (("  -- " + detail) if detail else ""))
    if not ok:
        FAILURES.append(what)
    return ok


def get(route, **q):
    url = BASE + route
    if q:
        url += "?" + urllib.parse.urlencode(q)
    with urllib.request.urlopen(url, timeout=180) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def post(route, body):
    req = urllib.request.Request(
        BASE + route, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as ex:
        return ex.code, json.loads(ex.read().decode("utf-8"))


def raw_post(route, body):
    req = urllib.request.Request(
        BASE + route, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, r.headers.get("Content-Type"), \
                r.headers.get("Content-Disposition"), r.read()
    except urllib.error.HTTPError as ex:
        return ex.code, ex.headers.get("Content-Type"), None, ex.read()


CODES = {"bad_request", "not_found", "unknown_type", "unknown_table",
         "unknown_value", "no_enm_here", "slot_overflow", "not_editable",
         "disc_busy", "token_expired", "not_in_this_build", "server_fault"}


def is_refusal(status, body, code=None):
    if status != 200 or body.get("ok") is not False:
        return False
    p = body.get("problem") or {}
    if p.get("code") not in CODES:
        return False
    return code is None or p["code"] == code


def plan_shape(p):
    if not isinstance(p, dict):
        return "not an object"
    for k in ("writes", "fits", "summary", "undo"):
        if k not in p:
            return "missing %s" % k
    for w in p["writes"]:
        for k in ("archive", "file", "bytes_before", "bytes_after", "stored",
                  "slot", "headroom", "fits"):
            if k not in w:
                return "write missing %s" % k
        for banned in ("encoded_bytes", "slot_bytes", "spare", "used",
                       "same_size"):
            if banned in w:
                return "write carries retired name %s" % banned
    files = [w["file"] for w in p["writes"]]
    if files != sorted(files):
        return "writes not sorted by file"
    if p["fits"] != all(w["fits"] for w in p["writes"]):
        return "fits disagrees with the rows"
    if p["undo"].get("steps") != 1:
        return "undo.steps is %r, not 1" % p["undo"].get("steps")
    return None


# Wider than any session this suite produces. `history` windows the last
# `limit` ENTRIES (Journal.history -> self.entries[-limit:]) and its default is
# 200, so a deep enough journal makes an added step invisible rather than
# absent: each new entry evicts the oldest and the row count pins at 200.
# Measured against the real Journal, the boundary is exact --
#
#     199 entries -> 199 rows, +1 -> 200    observable
#     200 entries -> 200 rows, +1 -> 200    NOT observable
#
# -- and this row failed on it at 139 -> 139 once 3341be0 changed how deep the
# journal is by the time these checks run. Asking for the whole thing is the
# fix; 5e249e6 added the parameter that makes asking possible.
HISTORY_LIMIT = 1000000


def live_history():
    """Undo steps that are still live.

    `history` also lists what has been UNDONE, so a plain row count does not
    change when a new edit replaces the redo stack. Counting the live rows is
    what "how many times must I press Ctrl+Z" actually means.

    Asks for the FULL journal, not the default window -- see HISTORY_LIMIT. A
    capped window cannot distinguish "the edit cost no undo step" from "the
    step fell off the far end", and this function's callers assert on exactly
    that difference.
    """
    rows = get("history", limit=HISTORY_LIMIT)[1]["entries"]
    return [r for r in rows if not r.get("undone")]


def find_enemy(name="Sheriff"):
    _, lvl = get("level", archive=ARCHIVE)
    for n in lvl["nodes"]:
        if n.get("kind") == "enemy" and n.get("props", {}).get("Name") == name:
            return n
    raise SystemExit("no enemy called %s in %s" % (name, ARCHIVE))


def main():
    print("Studio at %s" % BASE)

    print("\nFEATURES")
    st, f = get("features")
    check(st == 200 and f.get("ok"), "GET /api/features answers",
          "build=%s" % f.get("build"))
    # The property under test is that ROUTE REGISTRATION TRACKS THE FEATURE
    # SWITCH: a feature that is off must be ABSENT from the dispatch table,
    # not present and refusing. That holds in both editions, so check it from
    # whichever side this server is on rather than assuming one.
    #
    # This used to assume a source run was a release build. That stopped being
    # true when source runs began defaulting to internal -- a developer's
    # checkout is not a public build -- and the assumption was never the
    # point. Asserting the mechanism in both directions proves more than
    # asserting one edition's values.
    internal = f.get("build") == "internal"
    check(f["features"].get("repack") is bool(internal),
          "the running edition's own manifest decides what is on",
          "build=%s repack=%s" % (f.get("build"), f["features"].get("repack")))
    st2, _ = post("link_set", {})
    if internal:
        check(st2 != 404, "a route whose feature is ON is registered",
              "link_set -> HTTP %d" % st2)
    else:
        check(st2 == 404,
              "a route whose feature is off is NOT REGISTERED, not disabled",
              "link_set -> HTTP %d" % st2)

    print("\nE1/E2  THE PALETTE")
    st, c = get("cast", archive=ARCHIVE)
    check(st == 200 and c["ok"], "GET /api/cast answers")
    check(len(c["characters"]) == 12 and len(c["behaviours"]) == 20,
          "SHER70 returns 12 characters and 20 behaviours",
          "%d / %d" % (len(c["characters"]), len(c["behaviours"])))
    check(all(len(b["states"]) == 9 for b in c["behaviours"]),
          "every behaviour names all nine bound states")
    check(c["roster_source"] == "local", "roster_source is reported",
          c["roster_source"])
    st, c2 = get("cast", archive="LVLS_M2/BRIDGE2.DAT")
    check(c2["roster_source"] != "local" and c2["roster_note"],
          "a level with no roster of its own says so instead of showing "
          "nothing", c2["roster_source"])
    st, nf = get("cast", archive="NOPE.DAT")
    check(is_refusal(st, nf, "not_found"),
          "an unknown archive is a REFUSAL at HTTP 200, not an exception",
          "HTTP %d %s" % (st, (nf.get("problem") or {}).get("code")))

    print("\nE3  ALL 21 FIELDS")
    enemy = find_enemy()
    st, e = get("enemy", archive=ARCHIVE, uid=enemy["uid"])
    check(st == 200 and e["ok"], "GET /api/enemy answers")
    keys = [x["key"] for x in e["fields"]]
    want = ["Type", "Table", "Name", "Comment", "Alignment", "Group",
            "PopFreq", "BossType", "MyBoss", "Cullable", "Pushable",
            "TextureVariation", "PowerUp", "WeaponList", "StartWeapon",
            "ThrownWeaponAmmo", "Inventory", "SpawnHorse", "IsBeast",
            "WhichHead", "NeedsSound"]
    check(keys == want, "all 21 fields come back in the frozen order",
          "%d fields" % len(keys))
    absent = [x["key"] for x in e["fields"] if not x["present"]]
    check(absent, "fields absent from the block are reported, not omitted",
          "absent here: %s" % ", ".join(absent))
    wl = next(x for x in e["fields"] if x["key"] == "WeaponList")
    check(wl["arity"] == len(wl["value"]),
          "WeaponList arity is read from the file", "%d values" % wl["arity"])
    controls = {x["control"] for x in e["fields"]}
    check(controls <= {"pick", "text", "int", "float", "bool", "vec3", "list"},
          "every control is in the frozen set", ", ".join(sorted(controls)))

    print("\nDRY IS THE DEFAULT")
    st, r = post("enemy_set", {"archive": ARCHIVE, "uid": enemy["uid"],
                               "changes": {"Type": "ugl"}})
    check(st == 200 and r["ok"] and not r.get("applied"),
          "a request that says nothing about dry does NOT write",
          "applied=%r" % r.get("applied"))
    check(plan_shape(r["plan"]) is None, "the plan matches the frozen shape",
          plan_shape(r["plan"]) or "")
    check(r["plan"]["undo"]["steps"] == 1, "undo.steps is 1",
          r["plan"]["undo"]["label"])
    say("       label: %s" % r["plan"]["undo"]["label"])
    say("       write: %s" % json.dumps(r["plan"]["writes"][0]))

    print("\nREFUSALS")
    for changes, code in (({"Type": "nope"}, "unknown_type"),
                          ({"Table": "nope"}, "unknown_table"),
                          ({"TextureVariation": "nope"}, "unknown_value"),
                          ({"PowerUp": "Death_Ray"}, "unknown_value"),
                          ({"Name": None}, "not_editable"),
                          ({"WeaponList": [1]}, "bad_request")):
        st, r = post("enemy_set", {"archive": ARCHIVE, "uid": enemy["uid"],
                                   "changes": changes, "dry": True})
        check(is_refusal(st, r, code), "%s -> %s" % (list(changes)[0], code),
              "HTTP %d %s: %s" % (st, (r.get("problem") or {}).get("code"),
                                  (r.get("problem") or {}).get("message")))
    st, r = post("enemy_set", {"archive": ARCHIVE, "uid": 999999,
                               "changes": {"Type": "ugl"}})
    check(is_refusal(st, r, "not_found"), "an unknown uid -> not_found")

    print("\nE4  PLACE A NEW ENEMY")
    st, r = post("enemy_place", {
        "archive": ARCHIVE, "pos": [24.4, 1.5, -15.8], "angle": 12.5,
        "type": "ugl", "table": "Sher70_wildbunch_ugl"})
    check(st == 200 and r["ok"] and not r.get("applied"),
          "a placement dry-runs by default")
    check(plan_shape(r["plan"]) is None, "the placement plan is the same shape")
    check(r.get("new_name") and r["plan"]["writes"][0]["file"].endswith(".enm"),
          "it names the roster file and the name it would use",
          "%s -> %s" % (r["plan"]["writes"][0]["file"], r.get("new_name")))
    say("       %s" % r["plan"]["summary"])
    st, r = post("enemy_place", {"archive": "LVLS_M2/BRIDGE2.DAT",
                                 "pos": [0, 0, 0], "type": "ncb",
                                 "table": "actor"})
    check(is_refusal(st, r, "no_enm_here"),
          "a level with no roster refuses with no_enm_here and names SWAT.DAT",
          (r.get("problem") or {}).get("fix", "")[:70])
    st, r = post("enemy_place", {"archive": ARCHIVE, "pos": [0, 0, 0],
                                 "type": "zzz", "table": "Sher70_sheriff"})
    check(is_refusal(st, r, "unknown_type"), "an invented character is refused")
    st, r = post("enemy_place", {"archive": ARCHIVE, "pos": [0, 0, 0],
                                 "type": "ugl", "table": "zzz"})
    check(is_refusal(st, r, "unknown_table"),
          "an invented behaviour is refused, not written",
          (r.get("problem") or {}).get("message"))

    print("\nE5  ATTRIBUTES")
    _, lvl = get("level", archive=ARCHIVE)
    target = next((n for n in lvl["nodes"]
                   if any(k.startswith("attr.") for k in n.get("props", {}))),
                  None)
    check(target is not None, "an object with typed attributes is found")
    st, a = get("attrs", archive=ARCHIVE, uid=target["uid"])
    check(st == 200 and a["ok"] and a["attrs"],
          "GET /api/attrs answers", "%d attributes on %s"
          % (len(a["attrs"]), target["name"]))
    row = a["attrs"][0]
    check(set(row) >= {"name", "type", "value", "control", "options",
                       "seen_in_level", "editable", "rung"},
          "an attribute row carries its type, its control and its rung",
          "%s %s = %s (%s)" % (row["type"], row["name"], row["value"],
                               row["control"]))
    st, r = post("attr_set", {"archive": ARCHIVE, "uid": target["uid"],
                              "changes": {row["name"]: row["value"]}})
    check(st == 200 and r["ok"] and plan_shape(r["plan"]) is None,
          "an attribute edit dry-runs into the same plan shape")
    st, r = post("attr_set", {"archive": ARCHIVE, "uid": target["uid"],
                              "changes": {"NotAnAttribute": "1"}})
    check(is_refusal(st, r, "not_editable"),
          "adding an attribute that is not there is refused with a reason",
          (r.get("problem") or {}).get("fix", "")[:60])

    print("\nE6/E7  ROTATION AND SPLINES")
    box = next(n for n in lvl["nodes"] if n.get("rot"))
    st, r = post("node_rotate", {"archive": ARCHIVE, "uid": box["uid"],
                                 "matrix9": [1, 0, 0, 0, 1, 0, 0, 0, 1]})
    check(st == 200 and r["ok"] and plan_shape(r["plan"]) is None,
          "a rotation dry-runs into the same plan shape", box["name"])
    w = r["plan"]["writes"][0]
    check(w["bytes_after"] == w["bytes_before"],
          "a rotation write is length-neutral",
          "%d -> %d bytes" % (w["bytes_before"], w["bytes_after"]))
    spawn = next(n for n in lvl["nodes"] if n["kind"] == "enemy")
    st, r = post("node_rotate", {"archive": ARCHIVE, "uid": spawn["uid"],
                                 "matrix9": [1, 0, 0, 0, 1, 0, 0, 0, 1]})
    check(is_refusal(st, r, "not_editable"),
          "an enemy has no matrix and is told where to go instead",
          (r.get("problem") or {}).get("fix"))
    line = next(n for n in lvl["nodes"] if len(n.get("verts") or []) > 1)
    st, r = post("vertex_set", {"archive": ARCHIVE, "uid": line["uid"],
                                "index": 1, "pos": [1.0, 2.0, 3.0]})
    check(st == 200 and r["ok"], "a spline point dry-runs", line["name"])
    st, r = post("vertex_set", {"archive": ARCHIVE, "uid": line["uid"],
                                "index": 99, "pos": [1.0, 2.0, 3.0]})
    check(is_refusal(st, r, "bad_request"),
          "a point that does not exist is refused",
          (r.get("problem") or {}).get("message"))

    print("\nE8  DOORS")
    st, l = get("links", archive="LVLS_SNG/TOMB.DAT")
    check(st == 200 and l["ok"] and len(l["doors"]) == 6,
          "TOMB has six doors", "%d" % len(l.get("doors", [])))
    d0 = l["doors"][0]
    check(d0["headroom"] is not None and d0["rung"] == "measured",
          "each door shows its headroom BEFORE anything is typed",
          "%d bytes spare, %d destinations to choose from"
          % (d0["headroom"], len(l["destinations"])))
    st, l2 = get("links", archive=ARCHIVE)
    check(st == 200 and l2["ok"] and l2["doors"] == [] and l2.get("note"),
          "a level with no doors says so rather than looking broken",
          (l2.get("note") or "")[:60])

    print("\nVERSIONS")
    st, v = get("versions")
    check(st == 200 and v["ok"], "GET /api/versions answers",
          "%d version(s)" % len(v["versions"]))
    od = v["on_disc"]
    check(set(od) == {"id", "name", "matches", "files_changed", "drifted"},
          "the banner reports exactly what is on the disc right now",
          "id=%r matches=%r changed=%d" % (od["id"], od["matches"],
                                           od["files_changed"]))
    st, d = get("version_diff", a="stock", b="disc")
    check(st == 200 and d["ok"] and set(d["counts"]) ==
          {"added", "removed", "changed", "same"},
          "stock vs disc compares", json.dumps(d["counts"]))
    st, r = post("version_save", {"name": "probe (not saved)"})
    check(st == 200 and r["ok"] and not r.get("applied")
          and r["plan"]["writes"] == [],
          "saving a version dry-runs and writes nothing to the DISC",
          r["plan"]["summary"])
    st, r = post("version_restore", {"id": "_session"})
    check(is_refusal(st, r, "not_editable"),
          "the session autosave is visible but cannot be restored from",
          (r.get("problem") or {}).get("fix"))
    st, r = post("version_restore", {"id": "nope"})
    check(is_refusal(st, r, "not_found"), "an unknown version -> not_found")

    print("\nTRANSFERS")
    st, r = post("transfer_check", {"kind": "sandwich", "target": {}})
    check(is_refusal(st, r, "bad_request"), "an unknown kind is refused")
    st, r = post("transfer_check", {"kind": "stream", "target": {}})
    check(is_refusal(st, r, "not_in_this_build"),
          "a kind with no encoder yet says so instead of failing later",
          (r.get("problem") or {}).get("fix", "")[:60])
    st, r = post("transfer_apply", {"token": "t_nonsense"})
    check(is_refusal(st, r, "token_expired"),
          "an invented token is refused, so apply cannot write unplanned bytes")

    st, ctype, disp, blob = raw_post(
        "transfer_export", {"kind": "texture",
                            "target": {"archive": ARCHIVE, "i": 0}})
    check(st == 200 and ctype == "image/png" and blob[:4] == b"\x89PNG",
          "EXPORT: a texture comes back as a real PNG",
          "%s, %s, %d bytes" % (ctype, disp, len(blob)))
    st, ctype, disp, blob = raw_post(
        "transfer_export", {"kind": "stream",
                            "target": {"name": "d_bar_bart_01.stm"}})
    if st == 200 and ctype == "audio/wav":
        check(blob[:4] == b"RIFF" and "attachment" in (disp or ""),
              "EXPORT: a voice line comes back as a WAV that SAVES",
              "%s, %d bytes" % (disp, len(blob)))
    else:
        check(ctype == "application/json",
              "EXPORT: a missing stream refuses in JSON, so the front end "
              "can tell", "%s" % ctype)
    st, ctype, disp, blob = raw_post(
        "transfer_export", {"kind": "geometry",
                            "target": {"archive": ARCHIVE, "chunk": 99999}})
    check(ctype == "application/json" and st == 200,
          "EXPORT: a bad request answers JSON, never a file named .obj",
          "%s" % ctype)
    body = json.loads(blob.decode("utf-8"))
    check(body.get("ok") is False and body["problem"]["code"] in CODES,
          "and that JSON is a refusal envelope",
          body["problem"]["code"])

    print("\nTHE CHECKED PLAN IS THE APPLIED PLAN")
    # Export a surface as PNG and import the same pixels straight back. The
    # bytes are then knowably correct, so anything that differs afterwards is
    # the transfer path's doing and not the encoder's.
    import base64
    st, ctype, _d, png = raw_post(
        "transfer_export", {"kind": "texture",
                            "target": {"archive": ARCHIVE, "i": 0}})
    st, chk = post("transfer_check", {
        "kind": "texture", "target": {"archive": ARCHIVE, "i": 0},
        "payload_b64": base64.b64encode(png).decode("ascii")})
    ok = check(st == 200 and chk.get("ok") and chk.get("token"),
               "transfer_check encodes, plans and hands back a token",
               chk.get("subject", {}).get("title"))
    if ok:
        check(plan_shape(chk["plan"]) is None,
              "the transfer plan is the SAME plan shape as an enemy edit")
        check(set(chk["subject"]) == {"title", "where", "format", "rung"},
              "the subject carries its confidence rung BEFORE you commit",
              "%s -- %s" % (chk["subject"]["where"], chk["subject"]["rung"]))
        n0 = len(live_history())
        st, ap = post("transfer_apply", {"token": chk["token"]})
        check(st == 200 and ap.get("applied")
              and ap["plan"] == chk["plan"],
              "transfer_apply commits the EXACT plan that was checked",
              ap.get("group"))
        rows = live_history()
        check(len(rows) == n0 + 1, "one import is one undo step",
              "%d -> %d" % (n0, len(rows)))
        st, r2 = post("transfer_apply", {"token": chk["token"]})
        check(is_refusal(st, r2, "token_expired"),
              "a token is single-use, so an apply cannot happen twice")
        post("undo", {})
        check(True, "undone")

    print("\nMODEL SWAP  (dry only -- the write path is proved offline by "
          "verify_modelswap.py, on a copy)")
    st, m = get("model_targets", name="ncb.cr")
    if check(st == 200 and m.get("ok"), "GET /api/model_targets answers",
             "%d targets" % len(m.get("targets", []))):
        check(m["current"]["render"] == m["current"]["skel"] == "ncb",
              "it reports the model AND the skeleton the .cr names",
              "%s / %s" % (m["current"]["render"], m["current"]["skel"]))
        check(all(t["bones"] == m["current"]["bones"] for t in m["targets"]),
              "every offered target has the same bone count -- the animations "
              "do not move with the swap", "%d bones" % m["current"]["bones"])
        check(all("archives" in t and "hitbox_missing" in t
                  for t in m["targets"]),
              "each target says how many copies it can reach and whether the "
              "hit boxes survive")

        # `dry` defaults to TRUE, so a body that forgets to say so plans and
        # writes nothing. Asserted here as well as for enemy_set because this
        # route reaches Studio.edit_creature rather than Level.apply.
        st, d = post("model_swap", {"name": "ncb.cr", "target": "mrd"})
        if check(st == 200 and d.get("ok") and not d.get("applied"),
                 "POST /api/model_swap with no `dry` key writes NOTHING"):
            check(plan_shape(d["plan"]) is None,
                  "and answers with the same plan shape as every other edit",
                  d["plan"]["summary"])
            check(d["plan"]["undo"]["steps"] == 1,
                  "one swap is one undo step, however many copies it touches",
                  "%d writes" % len(d["plan"]["writes"]))
            check(all(w["file"] == "ncb.cr" for w in d["plan"]["writes"])
                  and len(d["plan"]["writes"]) < m["archives"],
                  "it plans only the archives that already hold mrd_lods.rsc",
                  "%d of %d copies" % (len(d["plan"]["writes"]), m["archives"]))
            kinds = {w.get("kind") for w in d["warnings"]}
            check("unproven" in kinds and "hitboxes" in kinds,
                  "the warnings name both what is unproven and what happens "
                  "to the hit boxes", ", ".join(sorted(k for k in kinds if k)))
            check(any("59" in w["text"] for w in d["warnings"]),
                  "and say out loud that all 59 borrowed models on the disc "
                  "are same-character variants")

        # The bone-count rule is enforced by the ROUTE, not only by the picker.
        st, bad = post("model_swap", {"name": "ncb.cr", "target": "hrs"})
        check(is_refusal(st, bad, "not_editable"),
              "a target with the wrong bone count is refused with the two "
              "numbers in the sentence",
              (bad.get("problem") or {}).get("message"))
        st, bad = post("model_swap", {"name": "ncb.cr", "target": "nosuch"})
        check(is_refusal(st, bad, "unknown_value"),
              "and a model that does not exist is a refusal, not a 500")
        st, bad = post("model_swap", {"name": "ncb.cr"})
        check(is_refusal(st, bad, "bad_request"),
              "a body with no target is a refusal that names the key")

    print("\nONE REAL WRITE, THEN UNDO")
    before = get("file", archive=ARCHIVE, name="lvl_sher70_var_1.enm")[1]
    n0 = len(live_history())
    st, r = post("enemy_set", {"archive": ARCHIVE, "uid": enemy["uid"],
                               "changes": {"Type": "ugl"}, "dry": False})
    ok = check(st == 200 and r.get("ok") and r.get("applied"),
               "a write with dry:false applies", r.get("group"))
    if ok:
        st, e2 = get("enemy", archive=ARCHIVE, uid=enemy["uid"])
        got = next(x["value"] for x in e2["fields"] if x["key"] == "Type")
        check(got == "ugl", "the character really changed on the working disc",
              "cls -> %s" % got)
        rows = live_history()
        check(len(rows) == n0 + 1,
              "it cost exactly ONE undo step",
              "%d -> %d history rows" % (n0, len(rows)))
        st, u = post("undo", {})
        check(u.get("ok") is not False, "undo runs", u.get("label"))
        st, e3 = get("enemy", archive=ARCHIVE, uid=enemy["uid"])
        got = next(x["value"] for x in e3["fields"] if x["key"] == "Type")
        check(got == "cls", "undo put the character back", "%s" % got)
        after = get("file", archive=ARCHIVE, name="lvl_sher70_var_1.enm")[1]
        check(after.get("text") == before.get("text"),
              "the roster file is byte-identical to how the run found it")

    print("\n" + "-" * 68)
    if FAILURES:
        print("%d FAILED:" % len(FAILURES))
        for f in FAILURES:
            print("   - " + f)
        return 1
    print("all live checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
