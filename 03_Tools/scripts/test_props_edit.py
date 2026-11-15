"""Editing a plain-text settings field, live, and putting it back.

Covers the path the Environment tab uses: read a level's fog, change a
colour, check it fits the archive slot first, write it, read it back, undo,
and confirm the file is byte-identical to stock again.

The slot check is the part that matters. These files live in fixed-size
archive slots -- lvl_tomb.fog gets 160 bytes and uses 143 -- so a write that
grew the file would corrupt the archive. props.apply only moves the value
span, so an edit of equal length always fits, and the dry run says so before
anything is written.
"""
import json
import sys
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8731/api/"
ARCHIVE = "LVLS_SNG/TOMB.DAT"
NAME = "lvl_tomb.fog"
FIELD = "Fog/ColorStart"
TEST_VALUE = "0.900000\t0.100000\t0.100000"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=300) as fh:
        return json.load(fh)


def post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as fh:
        return json.load(fh)


def field(doc, key):
    for f in doc["fields"]:
        if f["path"] == key:
            return f
    return None


def main():
    ok = True
    q = urllib.parse.quote(ARCHIVE)
    props_url = "props?archive=%s&name=%s" % (q, urllib.parse.quote(NAME))

    dirty_before = len(get("diff"))
    print("%s :: %s" % (ARCHIVE, NAME))
    print("  disc dirty before : %d file(s)" % dirty_before)

    doc = get(props_url)
    if not doc.get("textual"):
        print("  FAIL: not recognised as text")
        return 1
    print("  fields parsed     : %d" % doc["total"])
    print("  slot              : %d bytes, %d spare"
          % (doc.get("slot", 0), doc.get("headroom", 0)))

    original = field(doc, FIELD)
    if original is None:
        print("  FAIL: no field %s" % FIELD)
        return 1
    print("  %-18s: %r" % (FIELD, original["value"]))

    dry = post("props_save", dict(archive=ARCHIVE, name=NAME,
                                  edits={FIELD: TEST_VALUE}, dry=True))
    print("  dry run           : %d/%d bytes -> %s"
          % (dry["stored"], dry["slot"], "FITS" if dry["fits"] else "TOO BIG"))
    ok &= bool(dry["fits"]) and bool(dry["changed"])
    if not dry["fits"]:
        print("  refusing to write")
        return 1

    w = post("props_save", dict(archive=ARCHIVE, name=NAME,
                                edits={FIELD: TEST_VALUE}))
    print("  wrote             : %d field(s), %d/%d bytes"
          % (w["fields"], w["used"], w["slot"]))

    now = field(get(props_url), FIELD)
    print("  read back         : %r" % now["value"])
    ok &= now["value"] == TEST_VALUE

    after = len(get("diff"))
    print("  disc dirty        : %d file(s)  (expect %d)"
          % (after, dirty_before + 1))
    ok &= after == dirty_before + 1

    # Every other field must be untouched -- an in-place patch that reflowed
    # the file would still "work" here but would have moved everything else.
    doc2 = get(props_url)
    moved = [f["path"] for f in doc2["fields"]
             if f["path"] != FIELD
             and f["value"] != (field(doc, f["path"]) or {}).get("value")]
    print("  other fields moved: %d (expect 0)" % len(moved))
    ok &= not moved

    u = post("undo", {})
    print("  undo              : %s" % u.get("label"))
    back = field(get(props_url), FIELD)
    print("  restored          : %r  %s"
          % (back["value"],
             "OK" if back["value"] == original["value"] else "MISMATCH"))
    ok &= back["value"] == original["value"]

    final = len(get("diff"))
    print("  disc dirty after  : %d  (expect %d)" % (final, dirty_before))
    ok &= final == dirty_before

    print("\n%s" % ("ALL CHECKS PASSED" if ok else "SOMETHING FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
