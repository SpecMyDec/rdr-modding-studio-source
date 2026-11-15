"""[M10] Rebinding an enemy's `.stateb` through the tab's own API.

M10 asks for `.tbl`/`.stateb` to get their own tab -- browse, swap, rename and
edit -- and its accept criterion names one thing precisely: *rebind an enemy's
`.stateb` through the tab's own API and assert the on-disc bytes and a re-read
both agree*. So this drives the HTTP routes `web/states.js` calls, not the
`behaviour` module underneath them. `verify_behaviour_edit.py` already covers
the module level, and it boots PCSX2 for 185 s, which is why it is not this.

    python3 scripts/test_behaviour_routes.py      (needs a Studio on 8731)

THE TABLE IS CHOSEN, NOT INVENTED. An enemy's `Table` field names a `.tbl` by
stem, so the test parses a real `.enm` off the disc and rebinds a table an
enemy actually uses. 1,258 of the 3,275 `.tbl` on this disc are named by at
least one enemy and 2,017 by none; rebinding one of the orphans would satisfy
the words of the criterion and miss its point.

WHAT IT ASSERTS, in the order that matters:

  * the binding it is about to change currently RESOLVES to a real `.stateb`,
    and the replacement is a different member of that archive's own palette --
    otherwise the test proves a write, not a rebind;
  * `dry` reports a plan and changes nothing on disc;
  * the write reports applied;
  * **the bytes on the working disc actually changed** (`file.bin`);
  * **a re-read through the route returns the new binding** -- the two halves
    of "both agree";
  * every OTHER binding in that table is untouched;
  * the entry still fits its archive slot. This is asserted against the SLOT
    and not against the file length on purpose: rdr-fmt measured a same-length
    rebind whose STORED size still moved 7 bytes (118 -> 125 of 128), because
    the entry is deflated. C-030. A test that compared lengths would call that
    unchanged.

The write is sent with `everywhere: False`. The tab defaults to writing every
copy on the disc, and the first run of this test proved it: one rebind touched
`LVLS_SNG/BARFIGHT.DAT` AND `SWAT.DAT`. That is right for a user and wrong for
a test, which should put back exactly what it touched.

Then it reverts the file and asserts it is byte-identical to how the test found
it, so the working ISO is left exactly as found.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _HERE]
sys.path.insert(0, os.path.dirname(_HERE))

from rdrstudio import config                                # noqa: E402
from rdrstudio.game import Game                             # noqa: E402

BASE = "http://127.0.0.1:8731/api/"
FAILURES = []


def check(label, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", label,
                           "" if cond else "  -- " + str(detail)[:150]))
    if not cond:
        FAILURES.append(label)
    return cond


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
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return json.load(e)


def raw(archive, name):
    """The entry's exact bytes, straight off the working disc."""
    url = "%sfile.bin?%s" % (BASE, urllib.parse.urlencode(
        dict(archive=archive, name=name)))
    with urllib.request.urlopen(url, timeout=300) as r:
        return r.read()


def pick():
    """An archive, a `.tbl` an ENEMY names, a binding, and a replacement.

    Read off the vanilla disc rather than through the API: choosing the
    target is not the thing under test, and reading stock bytes keeps the
    choice stable whatever the working copy currently holds.
    """
    g = Game(config.VANILLA_ISO, writable=False)
    for archive in ("LVLS_SNG/BARFIGHT.DAT", "LVLS_SNG/MANS.DAT",
                    "LVLS_SNG/GUNSMOKE.DAT", "LVLS_SNG/FORT.DAT"):
        try:
            d = g.archive(archive)
        except Exception:                                   # noqa: BLE001
            continue
        tbl = {e.name[:-4].lower(): e.name[:-4] for e in d if e.ext == "tbl"}
        wanted = []
        for e in [x for x in d if x.ext == "enm"]:
            try:
                txt = d.read(e.name).decode("latin-1")
            except Exception:                               # noqa: BLE001
                continue
            for m in re.findall(r"Table\s+(\S+)", txt):
                key = m.strip().lower()
                if key in tbl and tbl[key] not in wanted:
                    wanted.append(tbl[key])
        if wanted:
            return archive, wanted
    return None, []


def main():
    try:
        get("config")
    except Exception as ex:                                 # noqa: BLE001
        print("the Studio is not answering on 8731: %s" % ex)
        return 2

    archive, candidates = pick()
    if not archive:
        print("no archive with an enemy-referenced .tbl found")
        return 2
    print("archive: %s" % archive)
    print("%d table(s) named by an enemy in its own .enm" % len(candidates))

    # The first candidate that has a binding we can actually move: it must
    # resolve today, and the palette must offer something else.
    target = row = swap_to = doc = None
    for name in candidates:
        d = get("behaviour_table", archive=archive, name=name)
        if d.get("ok") is False:
            continue
        palette = [p for p in (d.get("palette") or [])]
        for st in d.get("states") or []:
            if not st.get("resolves"):
                continue
            others = [p for p in palette
                      if p.lower() != str(st["value"]).lower()]
            if others:
                target, row, swap_to, doc = name, st, others[0], d
                break
        if target:
            break

    if not target:
        print("no movable binding found")
        return 2

    print("\n%s -- %s: %r -> %r"
          % (target, row["key"], row["value"], swap_to))
    check("the binding resolves to a real .stateb today", row["resolves"])
    check("the replacement is a different palette member",
          swap_to.lower() != str(row["value"]).lower())

    before_bytes = raw(archive, doc["file"])
    before = {s["path"]: s["value"] for s in doc["states"]}

    r = post("behaviour_table_set", {"archive": archive, "name": target,
                                     "changes": {row["path"]: swap_to},
                                     "everywhere": False, "dry": True})
    check("dry run answers ok", r.get("ok") is not False, r.get("problem"))
    check("dry run wrote nothing", raw(archive, doc["file"]) == before_bytes)

    w = post("behaviour_table_set", {"archive": archive, "name": target,
                                     "changes": {row["path"]: swap_to},
                                     "everywhere": False, "dry": False})
    check("the write reports applied", w.get("ok") is not False, w.get("problem"))

    after_bytes = raw(archive, doc["file"])
    check("the bytes on disc actually changed", after_bytes != before_bytes)

    back = get("behaviour_table", archive=archive, name=target)
    now = {s["path"]: s["value"] for s in back.get("states") or []}
    check("a re-read returns the new binding",
          str(now.get(row["path"], "")).lower() == swap_to.lower(),
          now.get(row["path"]))
    moved = [p for p in before if str(now.get(p, "")).lower()
             != str(before[p]).lower()]
    check("exactly one binding moved", moved == [row["path"]], moved)

    # The slot, not the length. A same-length rebind can still change the
    # stored size, because the entry is deflated -- C-030.
    check("the entry still fits its archive slot",
          back.get("stored") is not None and back["stored"] <= back["slot"],
          "%s of %s" % (back.get("stored"), back.get("slot")))
    if back.get("stored") != doc.get("stored"):
        print("     stored size moved %d -> %d of %d (headroom %d) -- C-030"
              % (doc["stored"], back["stored"], back["slot"], back["headroom"]))

    # ---------------------------------------------------------------- restore
    #
    # NOT by setting the binding back through the route, which is what a first
    # version did and why it failed. The route resolves a value through the
    # archive's palette, and archive entry names are stored LOWER CASE -- so a
    # table holding `Barfight_Shop_start` comes back as `barfight_shop_start`.
    # That is deliberate and documented in the route: the file's own spelling
    # wins only when you re-pick the value it ALREADY holds, which stops a
    # no-op rewrite showing up in every diff forever. Once the binding has
    # moved, its original casing is not recoverable through this route.
    #
    # So the restore is `revert_file`, which writes the pristine bytes back --
    # and it is guarded, because revert_file restores VANILLA and would throw
    # away somebody else's edit if this file had been dirty when we started.
    print("\nrestore")
    g = Game(config.VANILLA_ISO, writable=False)
    stock = g.archive(archive).read(doc["file"])
    if before_bytes != stock:
        check("SKIPPED restore: this file was already modified before the "
              "test ran, so reverting would discard that work", False,
              "put %s :: %s back by hand" % (archive, doc["file"]))
    else:
        u = post("revert_file", {"archive": archive, "name": doc["file"]})
        check("revert reports ok", u.get("ok") is True, u.get("error"))
        check("the file is byte-identical to how it started",
              raw(archive, doc["file"]) == before_bytes)

    print("\nSUMMARY")
    print("  [%s] an enemy's .stateb rebinds through the tab's own API"
          % ("FAIL" if FAILURES else "PASS"))
    if FAILURES:
        print("  failed: %s" % ", ".join(FAILURES))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
