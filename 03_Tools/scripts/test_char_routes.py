"""[M7] The `.msd` and `.skel` routes, over real HTTP.

The offline half of M7 is `verify_charstats.py`, which proves the nine editors
round-trip byte-exactly against an extracted archive copy. This is the live
half: it proves the two NEW routes carry that work over the wire, into the
working ISO, and back out again.

Needs the Studio running on 8731, like every other live test.

What it asserts, per route:

  * the read route answers with a document that actually has editable cells;
  * **`dry` really does default to TRUE** -- a POST that omits the flag comes
    back `ok:true` with a plan and writes NOTHING. This is the trap this file
    exists to pin down: `api.is_dry` returns `body.get("dry", True)`, so a
    caller who forgets looks successful and changes nothing, and the failure
    is indistinguishable from a pass at a glance;
  * a real write (`dry=False`) reports `applied`, and re-reading the file
    through the read route returns the NEW value -- not "no exception", the
    actual changed number coming back off the disc;
  * undo puts the file back BYTE-IDENTICAL, compared against the bytes read
    before the write.

The one write per route is applied and undone within the run. If the run dies
mid-way the working ISO keeps a single changed field, which `Reset to stock`
fixes; the pristine disc is never opened for writing.

    python3 scripts/test_char_routes.py
"""

import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _HERE]
sys.path.insert(0, os.path.dirname(_HERE))

BASE = "http://127.0.0.1:8731/api/"
FAILURES = []


def get(route, **params):
    url = BASE + route
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=300) as r:
        return json.load(r)


def post(route, body):
    req = urllib.request.Request(
        BASE + route, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


def raw(archive, name):
    """The file's exact bytes, straight off the working disc."""
    url = "%sfile.bin?%s" % (BASE, urllib.parse.urlencode(
        dict(archive=archive, name=name)))
    with urllib.request.urlopen(url, timeout=300) as r:
        return r.read()


def check(label, condition, detail=""):
    print("  [%s] %s%s" % ("PASS" if condition else "FAIL", label,
                           "" if condition else "  -- " + str(detail)))
    if not condition:
        FAILURES.append(label)
    return condition


def bump(text):
    t = str(text).strip()
    return t[:-1] + ("8" if t[-1] != "8" else "7")


def exercise(kind, name, read_route, set_route, pick):
    """One format, end to end: read, dry-by-default, write, re-read, undo."""
    print("\n%s -- %s" % (kind, name))
    doc = get(read_route, name=name)
    if not check("read route answers", doc.get("ok") is not False, doc):
        return
    path, before_value = pick(doc)
    if not check("document offers an editable cell", path is not None, doc):
        return
    new_value = bump(before_value)

    before_bytes = raw("SWAT.DAT", name)

    # The trap: omitting `dry` must NOT write.
    silent = post(set_route, dict(name=name, changes={path: new_value}))
    check("omitting `dry` answers ok", silent.get("ok") is True, silent)
    check("omitting `dry` does NOT report applied",
          not silent.get("applied"), silent)
    check("omitting `dry` left the bytes alone",
          raw("SWAT.DAT", name) == before_bytes)

    # The real write.
    done = post(set_route, dict(name=name, changes={path: new_value},
                                dry=False))
    if not check("write reports applied", done.get("applied") is True, done):
        return
    group = done.get("group")

    again = get(read_route, name=name)
    _p, now = pick(again)
    check("re-read returns the NEW value (%r -> %r)" % (before_value, now),
          str(now) == str(new_value), now)
    check("the bytes on disc actually changed",
          raw("SWAT.DAT", name) != before_bytes)

    undone = post("undo", {})
    check("undo reports a change", bool(undone), undone)
    after = raw("SWAT.DAT", name)
    check("undo restored the file byte-identically",
          after == before_bytes,
          "sha1 %s vs %s" % (hashlib.sha1(after).hexdigest()[:12],
                             hashlib.sha1(before_bytes).hexdigest()[:12]))
    return group


def pick_msd(doc):
    for group in doc.get("groups") or []:
        if group.get("key") == "speed":
            for band in group.get("bands") or []:
                for cell in band.get("fields") or []:
                    if str(cell.get("value", "")).strip()[-1:].isdigit():
                        return cell["path"], cell["value"]
    return None, None


def pick_skel(doc):
    for row in doc.get("rows") or []:
        for cell in row.get("cells") or []:
            if str(cell.get("value", "")).strip()[-1:].isdigit():
                return cell["path"], cell["value"]
    return None, None


def first_named(ext, read_route):
    """A file of this kind that SWAT.DAT actually holds.

    Asks the READ ROUTE, rather than trusting the roster. The roster lists
    every character the app knows about, and not all of them own a file of
    every kind -- its first entry here is `_in1`, which has no `.msd` and no
    `.skel`. Taking row 0 on faith made both cases fail with
    "SWAT.DAT has no file called _in1.msd", which reads exactly like a broken
    route and is nothing of the sort.
    """
    for row in get("char_roster").get("characters") or []:
        stem = row.get("stem") or row.get("name")
        if not stem:
            continue
        name = "%s.%s" % (str(stem).split(".")[0], ext)
        try:
            if get(read_route, name=name).get("ok"):
                return name
        except Exception:                                      # noqa: BLE001
            continue
    return None


def main():
    try:
        get("features")
    except Exception as ex:                                    # noqa: BLE001
        print("the Studio is not answering on 8731: %s" % ex)
        return 2

    msd_name = first_named("msd", "msd_file") or "srd.msd"
    skel_name = first_named("skel", "skel_file") or "bow.skel"

    exercise(".msd move set", msd_name, "msd_file", "msd_set", pick_msd)
    exercise(".skel bone offset", skel_name, "skel_file", "skel_set",
             pick_skel)

    print("\nSUMMARY")
    print("  [%s] .msd and .skel round-trip over the wire and undo cleanly"
          % ("FAIL" if FAILURES else "PASS"))
    if FAILURES:
        print("  failed: %s" % ", ".join(FAILURES))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
