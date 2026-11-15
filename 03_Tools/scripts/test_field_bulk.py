"""Finding one setting everywhere and changing it in one step.

The same field exists in dozens of copies across this disc -- one per level,
per area, per Showdown variant. `lgt` alone has 300 files with an `Ambient`.
Editing them one at a time is how you end up with a mod that works in three
levels and not the other thirty.

What matters here is the failure behaviour, not the success case:

  * a batch is dry-run in full first, and refused ENTIRELY if any one file
    would overflow its fixed archive slot. A half-applied bulk edit is worse
    than a refused one -- you would have to work out which of ninety files
    took it.
  * a file already holding the target value is skipped rather than rewritten,
    so `applied` can legitimately be lower than the number targeted.
  * the whole batch is one journal group, so it is one Ctrl+Z.
"""
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8731/api/"
EXT = "fog"
FIELD = "Fog/Enable"
NEW = "0"
BATCH = 40


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=900) as fh:
        return json.load(fh)


def post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as fh:
        return json.load(fh)


def main():
    ok = True
    start = len(get("diff"))
    print("disc dirty before : %d" % start)

    d = get("field_search?ext=%s&key=%s" % (EXT, FIELD))
    exact = [h for h in d["hits"] if h["path"] == FIELD]
    print("%s files scanned  : %d" % (EXT, d["scanned"]))
    print("%-18s: %d occurrence(s), %d distinct value(s)"
          % (FIELD, len(exact), d["distinct"]))
    ok &= len(exact) > 20
    if not exact:
        print("FAIL: found nothing to work with")
        return 1

    targets = [{k: h[k] for k in ("archive", "file", "path")}
               for h in exact[:BATCH]]
    already = sum(1 for h in exact[:BATCH] if h["value"].strip() == NEW)
    print("targeting         : %d (%d already at %r)"
          % (len(targets), already, NEW))

    dry = post("field_bulk_set", dict(value=NEW, targets=targets, dry=True))
    print("dry run           : ok=%s would_apply=%d refused=%d"
          % (dry["ok"], dry.get("would_apply", 0), len(dry["refused"])))
    ok &= bool(dry["ok"]) and not dry["refused"]

    r = post("field_bulk_set", dict(value=NEW, targets=targets))
    print("applied           : %d file(s)" % r["applied"])
    ok &= r["applied"] == len(targets) - already

    after = len(get("diff"))
    print("disc dirty        : %d  (expect %d)" % (after, start + r["applied"]))
    ok &= after == start + r["applied"]

    # Every touched file must now read back the new value.
    check = get("field_search?ext=%s&key=%s" % (EXT, FIELD))
    by = {(h["archive"], h["file"], h["path"]): h["value"] for h in check["hits"]}
    wrong = [t for t in targets
             if by.get((t["archive"], t["file"], t["path"]), "").strip() != NEW]
    print("read back wrong   : %d (expect 0)" % len(wrong))
    ok &= not wrong

    u = post("undo", {})
    print("undo              : %s -> %d file(s)"
          % (u.get("label"), u.get("count", 0)))
    ok &= u.get("count") == r["applied"]

    final = len(get("diff"))
    print("disc dirty after  : %d  (expect %d)" % (final, start))
    ok &= final == start

    print("\n%s" % ("ALL CHECKS PASSED" if ok else "SOMETHING FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
