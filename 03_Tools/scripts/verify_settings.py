"""[M4] Theme, View settings and Game lighting come back after a restart.

The tester on v1.2.0: *"Being able to set up and save the Theme, View Settings,
and Game Lighting settings instead of needing to set them each time you
relaunch the app."*

    python3 scripts/verify_settings.py

Four sections. None of them touches an ISO -- there is no disc in this
feature, and the settings routes are excluded from `ensure_open` precisely so
that ticking a checkbox cannot take the disc back off a running PCSX2.

**1. The store's defaults are the front end's defaults.** `settings.py` says
what a setting is when nobody has chosen one, and so does `app.js` --
separately, in a `let`. If those two ever disagree then "default" means two
different things and the file would record a choice the user never made (or
fail to record one they did). Read out of the real source files and compared,
so drift fails here rather than in the product.

**2. A restart brings the values back.** Non-default values are written by one
Python process, that process EXITS, and a second one started from scratch
reads them. Both run against a throwaway `LOCALAPPDATA`, so this section
cannot see or damage the real settings file. This is the assertion the
milestone is actually about: the values survive the death of the process that
set them, which is the one thing an in-memory or a localStorage answer cannot
do.

**3. The app's own routes read and write that same store.** Section 2 proves
the persistence; this proves the shipped app is wired to it, by driving
`GET`/`POST /api/settings` over real HTTP against the running Studio. It
needs a Studio on 8731, and says so and skips if there is not one.

The route reports the path it keeps the answers in, and this section reads
those bytes when it can. It often cannot, and that is not a failure: the
suite runs the Studio under Windows python while this script may run under
another interpreter entirely, so the Studio's path is not necessarily one
this process can open. The read-back through the route covers it either way,
because section 2 asserts `load()` opens the file on EVERY call -- so a value
that comes back through `GET` has been to the disc and returned.

Whatever it changes, it puts back: byte-for-byte from a snapshot when the
file is reachable, and through the route otherwise. Same discipline as the
live tests restoring the ISO.

**4. A bad value does not persist.** A settings file is the one thing that
survives a restart, so a value that would break the app must not be able to
break it again on every launch after that. Unknown keys, wrong types and
out-of-range choices are asserted to be rejected AND to leave the stored
value alone -- not coerced, because storing the string "false" in a boolean
would come back as True.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

# `scripts/` holds a `dis.py`, and Python puts a script's own directory first
# on sys.path -- which shadows the stdlib `dis` that `inspect` imports.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _HERE]
_TOOLS = os.path.dirname(_HERE)
sys.path.insert(0, _TOOLS)

from rdrstudio import settings as S            # noqa: E402

ROOT = os.path.dirname(_TOOLS)
WEB = os.path.join(_TOOLS, "web")
BASE = "http://127.0.0.1:8731/api/"

# Values that are not the default, one per setting, chosen so that every one
# of them differs from `DEFAULTS` -- a "non-default" that happened to equal
# the default would make this whole test vacuous.
NON_DEFAULT = {
    "theme": "midnight",
    "accent": "#6fbf7a",
    "linkCollision": True,
    "allowGrow": True,
    "navMode": "fly",
    "gridOn": True,
    "xray": True,
    "helpersFirst": True,
    "worldMode": "wire",
    "renderMode": "vertexcolor",
    "trackLayout": True,
    "hiddenBoxTypes": ["ai_room", "trigger"],
    "outMode": "type",
    "envTint": False,
    "bakedLight": False,
    "secondPass": False,
}


def fail(msg):
    print("   FAIL  %s" % msg)
    return False


def ok(msg):
    print("   ok    %s" % msg)
    return True


# --------------------------------------------------------------- section 1

def _js(path):
    with open(os.path.join(WEB, path), encoding="utf-8") as fh:
        return fh.read()


def _lit(text, name):
    """The literal a `let <name> = ...;` declares, as a Python value."""
    m = re.search(r"^let %s = (.+?);" % re.escape(name), text, re.M)
    if not m:
        return ("missing", None)
    raw = m.group(1).strip()
    if raw == "true":
        return ("ok", True)
    if raw == "false":
        return ("ok", False)
    if raw == "new Set()":
        return ("ok", [])
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
        return ("ok", raw[1:-1])
    return ("unparsed", raw)


def check_defaults_agree():
    print("\n1. the store's defaults are the front end's defaults:")
    app = _js("app.js")
    nav = _js("nav.js")
    html = _js("index.html")
    good = True

    # The View settings and two of the three Game lighting flags are plain
    # `let` declarations in app.js. `secondPass` is stored in a variable
    # under a different name, which is exactly the kind of thing that makes
    # an unchecked pairing drift.
    for key, var in [("linkCollision", "linkCollision"),
                     ("allowGrow", "allowGrow"),
                     ("navMode", "navMode"),
                     ("gridOn", "gridOn"),
                     ("xray", "xray"),
                     ("helpersFirst", "helpersFirst"),
                     ("worldMode", "worldMode"),
                     ("renderMode", "renderMode"),
                     ("trackLayout", "trackLayout"),
                     ("hiddenBoxTypes", "hiddenBoxTypes"),
                     ("outMode", "outMode"),
                     ("bakedLight", "bakedLight"),
                     ("secondPass", "showSecondPass")]:
        state, value = _lit(app, var)
        if state != "ok":
            good = fail("app.js `let %s` %s" % (var, state)) and good
        elif value != S.DEFAULTS[key]:
            good = fail("%s: app.js says %r, settings.py says %r"
                        % (key, value, S.DEFAULTS[key])) and good
        else:
            ok("%-14s %r" % (key, value))

    # envTint has no variable: `setEnvTint` reads the checkbox itself, so the
    # markup IS the default.
    m = re.search(r'id="envTint"([^>]*)>', html)
    if not m:
        good = fail("index.html has no #envTint") and good
    else:
        checked = "checked" in m.group(1)
        if checked != S.DEFAULTS["envTint"]:
            good = fail("envTint: markup says %r, settings.py says %r"
                        % (checked, S.DEFAULTS["envTint"])) and good
        else:
            ok("%-14s %r  (from the markup)" % ("envTint", checked))

    # Theme and accent are nav.js's, in the fallback `readLook()` returns.
    m = re.search(r"return \{ v: 1, theme: '([^']+)', accent: (\w+)", nav)
    acc = re.search(r"const STOCK_ACCENT = '([^']+)'", nav)
    if not m or not acc:
        good = fail("nav.js: could not read the look fallback") and good
    else:
        theme = m.group(1)
        accent = acc.group(1) if m.group(2) == "STOCK_ACCENT" else None
        for key, value in (("theme", theme), ("accent", accent)):
            if value != S.DEFAULTS[key]:
                good = fail("%s: nav.js says %r, settings.py says %r"
                            % (key, value, S.DEFAULTS[key])) and good
            else:
                ok("%-14s %r" % (key, value))

    # And the test's own table has to actually be non-default, or every
    # assertion below would pass against a store that never changed.
    missing = sorted(set(S.DEFAULTS) - set(NON_DEFAULT))
    same = [k for k in NON_DEFAULT if NON_DEFAULT[k] == S.DEFAULTS.get(k)]
    if missing:
        good = fail("no non-default value covered for %s" % ", ".join(missing))
    elif same:
        good = fail("these 'non-default' values ARE the default: %s"
                    % ", ".join(same))
    else:
        ok("all %d settings have a distinct non-default to test with"
           % len(NON_DEFAULT))
    return good


# --------------------------------------------------------------- section 2

_CHILD_WRITE = """
import json, os, sys
sys.path.insert(0, %(tools)r)
from rdrstudio import settings as S
r = S.save(json.loads(%(payload)r))
print(json.dumps({"written": sorted(r["written"]), "ignored": r["ignored"]}))
"""

_CHILD_READ = """
import json, os, sys
sys.path.insert(0, %(tools)r)
from rdrstudio import settings as S
print(json.dumps(S.load()))
"""


def _child(code, appdata):
    env = dict(os.environ)
    env["LOCALAPPDATA"] = appdata
    p = subprocess.run([sys.executable, "-c", code], env=env,
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("child failed: %s" % (p.stderr.strip() or "?"))
    return json.loads(p.stdout.strip().splitlines()[-1])


def check_restart():
    print("\n2. a restart brings the values back:")
    good = True
    tmp = tempfile.mkdtemp(prefix="rdr-settings-")
    try:
        # Seed the emulator path, so we can prove a preference write does not
        # cost the one setting that was already in this file.
        sfile = os.path.join(tmp, "RDR Modding Studio", "settings.json")
        os.makedirs(os.path.dirname(sfile), exist_ok=True)
        with open(sfile, "w", encoding="utf-8") as fh:
            json.dump({"pcsx2_exe": r"C:\pcsx2\pcsx2.exe"}, fh)

        r = _child(_CHILD_WRITE % {"tools": _TOOLS,
                                   "payload": json.dumps(NON_DEFAULT)}, tmp)
        if sorted(r["written"]) != sorted(NON_DEFAULT):
            good = fail("process 1 wrote %s, expected all %d"
                        % (r["written"], len(NON_DEFAULT)))
        else:
            ok("process 1 wrote all %d settings, then exited"
               % len(r["written"]))

        # The first process is gone. Nothing is cached anywhere.
        back = _child(_CHILD_READ % {"tools": _TOOLS}, tmp)
        wrong = {k: (NON_DEFAULT[k], back.get(k)) for k in NON_DEFAULT
                 if back.get(k) != NON_DEFAULT[k]}
        if wrong:
            for k, (want, got) in sorted(wrong.items()):
                fail("%s came back as %r, set to %r" % (k, got, want))
            good = False
        else:
            ok("process 2 read all %d back, unchanged" % len(NON_DEFAULT))

        with open(sfile, encoding="utf-8") as fh:
            doc = json.load(fh)
        if doc.get("pcsx2_exe") != r"C:\pcsx2\pcsx2.exe":
            good = fail("the emulator path did not survive a settings write")
        else:
            ok("the emulator path in the same file is untouched")

        # A default put back must actually leave the file, not linger as a
        # stored value that shadows a later change to the default.
        _child(_CHILD_WRITE % {"tools": _TOOLS,
                               "payload": json.dumps({"xray": False})}, tmp)
        with open(sfile, encoding="utf-8") as fh:
            doc = json.load(fh)
        if "xray" in doc.get("ui", {}):
            good = fail("setting xray back to its default still stores it")
        else:
            ok("a value set back to the default stops being stored")

        # `load()` must read the file EVERY time rather than cache. This is
        # what lets section 3 treat a read-back through the route as proof
        # that the value reached the disc, and it is the property that makes
        # a long-lived server pick up a settings file edited underneath it.
        here = os.environ.get("LOCALAPPDATA")
        try:
            os.environ["LOCALAPPDATA"] = tmp
            before = S.load()["renderMode"]
            with open(sfile, encoding="utf-8") as fh:
                doc = json.load(fh)
            doc.setdefault("ui", {})["renderMode"] = "plain"
            with open(sfile, "w", encoding="utf-8") as fh:
                json.dump(doc, fh)
            if S.load()["renderMode"] != "plain":
                good = fail("load() cached; an edit to the file was not seen")
            else:
                ok("load() re-reads the file every call (was %r, now 'plain')"
                   % before)
        finally:
            if here is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = here
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return good


# --------------------------------------------------------------- section 3

def _get(route, timeout=15):
    with urllib.request.urlopen(BASE + route, timeout=timeout) as fh:
        return json.load(fh)


def _post(route, body, timeout=15):
    req = urllib.request.Request(
        BASE + route, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as fh:
        return json.load(fh)


def studio_up():
    try:
        _get("config", timeout=3)
        return True
    except Exception:                                          # noqa: BLE001
        return False


def check_routes():
    print("\n3. the app's own routes read and write that store:")
    if not studio_up():
        print("   SKIP  no Studio on 8731; run this inside run_tests.py")
        return True

    try:
        first = _get("settings")
    except (urllib.error.URLError, OSError) as ex:
        return fail("GET settings: %s" % ex)
    for key in ("settings", "defaults"):
        if key not in first:
            return fail("GET settings: no %r in %s" % (key, sorted(first)))
    if first["defaults"] != S.DEFAULTS:
        return fail("GET settings: the route's defaults are not the store's")
    ok("GET returns the live values and the declared defaults")

    # Ask the SERVER where its settings live. Only the server knows: the
    # suite runs the Studio under Windows python and may run this script
    # under another interpreter entirely, so a path computed HERE would be a
    # different machine's answer. A Studio older than this route reports no
    # path at all, which is a thing to say plainly rather than paper over
    # with a local guess.
    live = first.get("path")
    readable = bool(live) and os.path.isfile(live)
    snapshot = None
    if readable:
        with open(live, "rb") as fh:
            snapshot = fh.read()

    good = True
    try:
        # Start from a known state, or this section proves nothing. If the
        # file already held these values -- a previous run of this test, or a
        # developer who happens to like wireframe -- then "wrote 0 settings"
        # would pass while asserting nothing at all. Defaults first, so the
        # write below is always the full sixteen.
        _post("settings", {"settings": S.DEFAULTS})
        base = _get("settings")["settings"]
        off = {k: base.get(k) for k in S.DEFAULTS
               if base.get(k) != S.DEFAULTS[k]}
        if off:
            good = fail("could not reset to defaults first: %r" % off)
        else:
            ok("reset to defaults, so the write below is a real change")

        r = _post("settings", {"settings": NON_DEFAULT})
        if sorted(r.get("written", [])) != sorted(NON_DEFAULT):
            good = fail("POST wrote %s, expected all %d"
                        % (sorted(r.get("written", [])), len(NON_DEFAULT)))
        else:
            ok("POST wrote all %d settings" % len(NON_DEFAULT))

        again = _get("settings")["settings"]
        wrong = {k: again.get(k) for k in NON_DEFAULT
                 if again.get(k) != NON_DEFAULT[k]}
        if wrong:
            good = fail("the route read back %r" % wrong)
        else:
            # Section 2 proved load() opens the file on every call, so this
            # read-back is a disc round trip, not a look at server memory.
            ok("every value reads back through the route (a disc round trip;"
               " see 2)")

        # The bytes themselves, when this process can actually see the file.
        if readable:
            with open(live, encoding="utf-8") as fh:
                stored = json.load(fh).get("ui", {})
            missing = [k for k in NON_DEFAULT if stored.get(k) != NON_DEFAULT[k]]
            if missing:
                good = fail("not written to %s: %s"
                            % (live, ", ".join(missing)))
            else:
                ok("all %d are in %s" % (len(NON_DEFAULT),
                                         os.path.basename(live)))
        elif live:
            ok("the Studio stores them in %s (not open-able from this "
               "interpreter; the round trip above covers it)" % live)
        else:
            ok("this Studio reports no settings path (it predates that field);"
               " the round trip above covers it")

        # An unknown key must be refused over the wire too, not just in-process.
        r = _post("settings", {"settings": {"thereIsNoSuchSetting": 1}})
        if r.get("written") or "thereIsNoSuchSetting" not in r.get("ignored", []):
            good = fail("the route accepted an unknown key: %s" % r)
        else:
            ok("the route ignores an unknown key")
    except (urllib.error.URLError, OSError) as ex:
        good = fail("route error: %s" % ex)
    finally:
        # Put the developer's real settings back the way they were. Through
        # the file when we can (exact), through the route when we cannot.
        try:
            if readable and snapshot is not None:
                with open(live, "wb") as fh:
                    fh.write(snapshot)
                print("   ok    restored %s" % live)
            else:
                _post("settings", {"settings": first["settings"]})
                print("   ok    restored the previous values through the route")
        except (urllib.error.URLError, OSError) as ex:
            good = fail("could NOT restore the settings: %s" % ex)
    return good


# --------------------------------------------------------------- section 4

def check_bad_values():
    print("\n4. a bad value does not persist:")
    good = True
    tmp = tempfile.mkdtemp(prefix="rdr-settings-bad-")
    try:
        os.environ["LOCALAPPDATA"] = tmp
        base = S.save({"xray": True, "navMode": "fly", "worldMode": "wire"})
        if not base["written"]:
            return fail("could not establish a baseline")

        cases = [
            ("an unknown key", {"thereIsNoSuchSetting": 1}),
            ("a string in a boolean", {"xray": "false"}),
            ("a number in a boolean", {"xray": 0}),
            ("a choice that is not one", {"navMode": "teleport"}),
            ("a boolean in a choice", {"worldMode": True}),
            ("a string where a list goes", {"hiddenBoxTypes": "ai_room"}),
            ("a non-string inside the list", {"hiddenBoxTypes": [1, 2]}),
            ("an over-long string", {"theme": "x" * 500}),
        ]
        for label, payload in cases:
            before = S.load()
            r = S.save(payload)
            after = S.load()
            key = list(payload)[0]
            if r["written"]:
                good = fail("%s was WRITTEN (%s)" % (label, r["written"]))
            elif key not in r["ignored"]:
                good = fail("%s was not reported as ignored" % label)
            elif before != after:
                good = fail("%s changed the stored values" % label)
            else:
                ok("%-28s rejected, nothing changed" % label)

        # The rejects must not have damaged what was already there.
        now = S.load()
        if (now["xray"], now["navMode"], now["worldMode"]) != (True, "fly",
                                                               "wire"):
            good = fail("the baseline values did not survive the bad writes")
        else:
            ok("the values set before them are intact")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return good


def main():
    print("=" * 70)
    print("[M4] settings persist across launches")
    print("=" * 70)

    real_appdata = os.environ.get("LOCALAPPDATA")
    results = [
        ("defaults agree with the front end", check_defaults_agree()),
        ("values survive a process restart", check_restart()),
    ]
    # Section 3 must run against the REAL settings path, so it goes before
    # section 4 repoints LOCALAPPDATA at a throwaway directory.
    results.append(("the app's routes use the store", check_routes()))
    results.append(("bad values are rejected", check_bad_values()))
    if real_appdata is None:
        os.environ.pop("LOCALAPPDATA", None)
    else:
        os.environ["LOCALAPPDATA"] = real_appdata

    print("\n" + "-" * 70)
    for label, good in results:
        print("  [%s] %s" % ("PASS" if good else "FAIL", label))
    bad = [l for l, g in results if not g]
    print("\n%s" % ("PASS" if not bad else "FAIL: " + ", ".join(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
