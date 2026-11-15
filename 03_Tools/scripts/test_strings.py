"""Rewriting a string the game displays, in every archive that carries it.

`.strtbl` holds all 3,069 displayed strings. The same table is duplicated
into each archive that needs it -- "Rifle" lives in 65 of them -- so an edit
that touched only one copy would change the weapon's name in one level and
not the rest.

Two things make this different from the rest of the toolkit's edits:

  * It is NOT byte-count neutral. Text is length-prefixed UTF-16, so a longer
    string grows the file and can overflow its archive slot. Every copy is
    dry-run first and the whole edit is refused if any one would not fit.
  * The language offset table is cumulative, so a string one character longer
    moves the start of every later language block. Re-encoding has to rebuild
    those offsets; leaving them alone yields a file that still parses and
    reads as garbage.
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8731/api/"
NAME = "weapon_select_name8"      # "Rifle"
NEW = "Boomstick"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=900) as fh:
        return json.load(fh)


def post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as fh:
        return json.load(fh)


def lookup(name):
    d = get("strings?q=" + urllib.parse.quote(name))
    for r in d["strings"]:
        if r["name"] == name:
            return r
    return None


def main():
    ok = True
    start = len(get("diff"))
    print("disc dirty before : %d" % start)

    r = lookup(NAME)
    if r is None:
        print("FAIL: no string named %s" % NAME)
        return 1
    original = r["text"]
    copies = r["copies"]
    print("%-18s: %r in %d archive cop%s"
          % (NAME, original, len(copies), "y" if len(copies) == 1 else "ies"))
    ok &= len(copies) > 10

    dry = post("string_set", dict(name=NAME, text=NEW, slot=0,
                                  copies=copies, dry=True))
    print("dry run           : ok=%s would_apply=%d tightest slot has %d "
          "byte(s) spare" % (dry["ok"], dry["would_apply"],
                             dry.get("tightest", 0)))
    ok &= bool(dry["ok"]) and not dry["refused"]

    w = post("string_set", dict(name=NAME, text=NEW, slot=0, copies=copies))
    print("applied           : %d cop(ies)" % w["applied"])
    ok &= w["applied"] == len(copies)

    now = lookup(NAME)
    print("read back         : %r" % now["text"])
    ok &= now["text"] == NEW
    # If only some copies took it, the search groups them and flags a
    # divergence -- which would mean a partial write got through.
    print("copies agree      : %s" % (not now.get("varies")))
    ok &= not now.get("varies")

    after = len(get("diff"))
    print("disc dirty        : %d  (expect %d)" % (after, start + len(copies)))
    ok &= after == start + len(copies)

    # A string too long for its slot must be refused outright, with nothing
    # written. It has to be INCOMPRESSIBLE to test that: the slot check is on
    # the stored size, and 4,000 repeated characters deflate to almost
    # nothing and legitimately fit. That is correct behaviour, not a hole --
    # but it makes "X"*4000 a useless test case.
    huge = "".join(chr(0x21 + (i * 7 + (i * i) % 61) % 90) for i in range(9000))
    ref = post("string_set", dict(name=NAME, text=huge, slot=0, copies=copies))
    print("oversized refused : %s (%d refusal(s), applied %d)"
          % (not ref["ok"], len(ref["refused"]), ref["applied"]))
    ok &= not ref["ok"] and ref["applied"] == 0
    if ref["refused"]:
        print("   e.g. %s" % ref["refused"][0]["why"][:88])

    u = post("undo", {})
    print("undo              : %s -> %d file(s)"
          % (u.get("label"), u.get("count", 0)))
    back = lookup(NAME)
    print("restored          : %r  %s"
          % (back["text"], "OK" if back["text"] == original else "MISMATCH"))
    ok &= back["text"] == original

    final = len(get("diff"))
    print("disc dirty after  : %d  (expect %d)" % (final, start))
    ok &= final == start

    # Never hand back a dirtier disc than we were given, whatever failed.
    for _ in range(8):
        if len(get("diff")) <= start:
            break
        post("undo", {})
    left = len(get("diff"))
    if left != start:
        print("WARNING: left %d changed file(s) behind" % (left - start))

    print("\n%s" % ("ALL CHECKS PASSED" if ok else "SOMETHING FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
