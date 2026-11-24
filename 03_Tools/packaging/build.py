#!/usr/bin/env python3
"""Build the shippable application, then prove the build works.

    python build.py                     the release build -- what people download
    python build.py --build internal    everything, including unfinished work
    python build.py --both              both editions, compared side by side
    python build.py --shape onefile     single .exe, for comparison only
    python build.py --no-numpy          without the numpy fast paths, for comparison
    python build.py --quick             skip self-check, window check and timing

A build that has not been checked is not a build. Freezing fails silently -- a
dropped module looks like a working app until someone opens the one tab that
needed it -- so every build ends by starting the real server inside the real
.exe, driving the real API, and opening the real window to render a real cube
through the bundled three.js.

Four gates, in order, each of which stops the build:

  1. JS SYNTAX GATE. Every .js and every inline <script> that will ship is
     parsed after the feature strip. A syntax error in the front end blanks the
     whole product and that has happened twice; an unbalanced feature sentinel
     is the newest way to cause it.
  2. MANIFEST GATE. Feature ids in features.json and @feature sentinels in the
     source must be the same set. A manifest id with no sentinel strips
     nothing; a sentinel with no manifest entry ships regardless of the switch.
     Either way the switch is a lie, silently.
  3. SELF-CHECK. The frozen exe answers its own API.
  4. WINDOW CHECK. The frozen window renders WebGL. See rdrshell/shell.py.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile

import jsgate
import thirdparty

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
PROJECT = os.path.dirname(TOOLS)
SPEC = os.path.join(HERE, "rdrstudio_app.spec")
VENV = os.path.join(HERE, ".venv")
VENV_PY = os.path.join(VENV, "Scripts", "python.exe")
PY = VENV_PY if os.path.isfile(VENV_PY) else sys.executable
WEB = os.path.join(TOOLS, "web")
MANIFEST = os.path.join(TOOLS, "features.json")

VERSION = "2.2.0"
APPNAME = "RDR Modding Studio"

EDITIONS = {
    "release": dict(tag="release", label="Release",
                    zip="RDR-Modding-Studio-%s.zip" % VERSION,
                    blurb="The public build. This is the one to share."),
    # The edition this fork ships: every tool, the Data Editor and the
    # Flash-screen / audio / animation work included.
    "internal": dict(tag="internal", label="Full",
                     zip="RDR-Modding-Studio-%s-full.zip" % VERSION,
                     blurb="Every tool, including the ones still being "
                           "proven. This is the one to share."),
}

# `\r?\n?` rather than `\n?`: stage_web reads with newline="" now, so a CRLF
# file arrives with its carriage returns intact and a trailing `\n?` alone
# would cut the line but leave the `\r` behind as a blank one. No file in
# web/ is CRLF today; this is what stops that being a silent condition of the
# strip working.
SENTINEL_JS = re.compile(
    r"[ \t]*/\*\s*@feature:([A-Za-z0-9_]+)\s*\*/.*?"
    r"/\*\s*@end:\1\s*\*/[ \t]*\r?\n?", re.S)
SENTINEL_HTML = re.compile(
    r"[ \t]*<!--\s*@feature:([A-Za-z0-9_]+)\s*-->.*?"
    r"<!--\s*@end:\1\s*-->[ \t]*\r?\n?", re.S)
SENTINEL_ANY = re.compile(r"@feature:([A-Za-z0-9_]+)")

# Front-end modules that exist solely to drive one gated panel, so the release
# build should not carry the file at all. Anything not listed here is shared
# code and stays.
FEATURE_MODULES = {
    "behaviour_tables": ["states.js"],
    "menu_edit": ["menus.js"],
    "csv_editor": ["csvedit.js"],
    "char_files": ["rig.js"],
}


# --------------------------------------------------------------------- utils

def sh(cmd, env=None, quiet=True, cwd=HERE):
    e = dict(os.environ)
    e.update(env or {})
    p = subprocess.run(cmd, env=e, cwd=cwd, capture_output=quiet, text=True)
    if p.returncode != 0 and quiet:
        sys.stdout.write(p.stdout or "")
        sys.stderr.write(p.stderr or "")
    return p


def size_of(path):
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            total += os.path.getsize(os.path.join(root, f))
    return total


def mb(n):
    return n / 1048576.0


# ------------------------------------------------------- features + stripping

def read_manifest():
    """features.json is written by the backend side and may not exist yet.

    Absent is a legal state and says so out loud, because "nothing was
    stripped" and "the strip silently did nothing" look identical in a zip.
    """
    if not os.path.isfile(MANIFEST):
        return None
    try:
        with open(MANIFEST, encoding="utf-8") as fh:
            raw = json.load(fh)
    except Exception as ex:                                # noqa: BLE001
        print("  features.json is not readable: %s" % ex)
        return None
    # The list form is a shape this is meant to accept, so it has to be
    # tested BEFORE anything calls .get on it -- and outside the try above,
    # where a raised AttributeError is not caught but a traceback.
    if isinstance(raw, list):
        feats = raw
    elif isinstance(raw, dict):
        feats = raw.get("features", [])
    else:
        print("  features.json is neither an object nor a list of features")
        return None
    out = {}
    for f in feats:
        if isinstance(f, dict) and f.get("id"):
            out[f["id"]] = f
    return out


def sentinel_ids(root):
    found = {}
    for base, _, files in os.walk(root):
        for f in files:
            if not f.endswith((".js", ".html")):
                continue
            p = os.path.join(base, f)
            try:
                with open(p, encoding="utf-8", errors="replace") as fh:
                    src = fh.read()
            except OSError:
                continue
            for m in SENTINEL_ANY.finditer(src):
                found.setdefault(m.group(1), set()).add(f)
    return found



# An array literal with no nested brackets. app.js's import list is one, and
# so is every other array in the file -- `import_lists` is what narrows it.
MODULE_ARRAY = re.compile(r"\[[^\[\]]*\]", re.S)
# A quoted module token, in either shape the front end has used: a bare name
# joined to a path at runtime ('menus'), or a literal path ('./menus.js').
def _quoted(name):
    return re.compile(r"""['"](?:\./)?%s(?:\.js)?['"]""" % re.escape(name))


def import_lists(src):
    """The array literals in app.js that gate a module name with a sentinel.

    Narrowed to arrays carrying a sentinel rather than "every array", because
    the question being asked is whether the gating agrees with FEATURE_MODULES
    -- and because a bare name like 'states' is also a panel id passed to
    showTab(). Matching one of those would report a file as still imported
    when it is not, and a gate that cries wolf gets passed --force.
    """
    return [m.group(0) for m in MODULE_ARRAY.finditer(src)
            if SENTINEL_ANY.search(m.group(0))]


def loader_gate(report, strict=False):
    """Does FEATURE_MODULES agree with the sentinels in app.js's import list?

    Dropping a front-end module takes two halves and they have to move
    together: the file leaves the bundle because it is named in
    FEATURE_MODULES, and its name leaves app.js's import list because a
    sentinel carves it out. Either half alone is silent.

      * file dropped, name NOT wrapped -> HARD FAIL. The release build
        import()s a file it has just deleted. app.js catches that and logs it
        to a console nobody has open, so the feature is simply missing and
        the build says nothing.
      * name wrapped, file NOT dropped -> warning. The panel is switched off
        but its implementation ships in the public zip, which is the same
        complaint `unstripped_internal` records for shared code.
      * neither, because the name is nowhere in an import list -> warning.
        Nothing breaks; the entry does nothing while reading as though it
        did, and --strict declines to ship a pairing it could not check.

    snapshot() already catches the first case for a literal './name.js', and
    says in its own comment that it cannot see through a name built at
    runtime -- which is the shape app.js actually uses. This is that hole.

    Asked of WEB, never of the stage: after a successful strip the name and
    its sentinel have both left the stage, so the check would answer "fine"
    for exactly the features it exists to watch. Asked in every edition too,
    because the pairing is a property of the source, and the edition where it
    bites is the one being shipped to strangers.
    """
    path = os.path.join(WEB, "app.js")
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            src = fh.read()
    except OSError as ex:
        print("  loader      WARNING: app.js is not readable: %s" % ex)
        report["loader"] = "unreadable"
        return not strict
    region = "\n".join(import_lists(src))
    if not region:
        # Not the same as "nothing to check": the pairing is unverified, and
        # a silent pass here is indistinguishable from a clean one.
        print("  loader      WARNING: no gated import list in app.js, so "
              "FEATURE_MODULES is unchecked against it")
        report["loader"] = "not found"
        return not strict

    # Run the real strip, one feature at a time, and ask whether the name went
    # with the file. Re-implementing the carve here would only test the
    # re-implementation.
    unwrapped, unlisted = [], []
    for fid, mods in sorted(FEATURE_MODULES.items()):
        cut = SENTINEL_JS.sub(
            lambda mm, fid=fid: "" if mm.group(1) == fid else mm.group(0),
            region)
        for mod in mods:
            name = mod[:-3] if mod.endswith(".js") else mod
            where = "%s (@feature:%s)" % (mod, fid)
            if not _quoted(name).search(region):
                unlisted.append(where)
            elif _quoted(name).search(cut):
                unwrapped.append(where)

    # The other direction: a name the import list gates under an id that drops
    # no file. The name goes, the file stays.
    gated = {}
    for mm in re.finditer(
            r"/\*\s*@feature:([A-Za-z0-9_]+)\s*\*/(.*?)/\*\s*@end:\1\s*\*/",
            region, re.S):
        for nm in re.findall(r"""['"](?:\./)?([A-Za-z0-9_-]+)(?:\.js)?['"]""",
                             mm.group(2)):
            gated.setdefault("%s.js" % nm, mm.group(1))
    shipped = ["%s (@feature:%s)" % (mod, fid)
               for mod, fid in sorted(gated.items())
               if mod not in FEATURE_MODULES.get(fid, ())]

    report["loader_unwrapped"] = sorted(unwrapped)
    report["loader_unlisted"] = sorted(unlisted)
    report["loader_ships_gated"] = sorted(shipped)
    ok = True
    if unwrapped:
        print("  LOADER FAIL  dropped by FEATURE_MODULES and still named in "
              "app.js's import list: %s" % ", ".join(sorted(unwrapped)))
        print("               a release build imports a file it deleted, and "
              "says so only in the console")
        ok = False
    if shipped:
        print("  loader      WARNING: app.js gates %s, but FEATURE_MODULES "
              "keeps the file -- switched off, still in the zip"
              % ", ".join(sorted(shipped)))
        if strict:
            ok = False
    if unlisted:
        # "Never named" and "never imported" are different claims: a module
        # reached by a static import at the top of app.js is imported and
        # would not be in this list.
        print("  loader      WARNING: FEATURE_MODULES drops %s, which no "
              "import list names" % ", ".join(sorted(unlisted)))
        if strict:
            ok = False
    print("  loader      %d module(s) in FEATURE_MODULES, %d name(s) gated in "
          "app.js's import list(s)"
          % (sum(len(v) for v in FEATURE_MODULES.values()), len(gated)))
    return ok


# The shipped page source read like a diary: 34 KB of index.html was
# comments narrating decisions and naming the owner (DESIGN-REVIEW-2026-10-01
# A14). A release strips them from the HTML -- markup comments everywhere and
# CSS comments inside <style> -- and keeps the @feature/@end sentinels, which
# manifest_gate still reads off the stage. Scripts are not touched: a regex
# cannot tell a JS comment from the same characters inside a string.
HTML_COMMENT = re.compile(r"<!--(?!\s*@(?:feature|end):)[\s\S]*?-->")
STYLE_BLOCK = re.compile(r"(<style\b[^>]*>)([\s\S]*?)(</style>)")
CSS_COMMENT = re.compile(r"/\*[\s\S]*?\*/")


def strip_comments(stage, report):
    removed = 0
    for base, _, files in os.walk(stage):
        for f in files:
            if not f.endswith(".html"):
                continue
            p = os.path.join(base, f)
            with open(p, encoding="utf-8", newline="") as fh:
                src = fh.read()
            out = STYLE_BLOCK.sub(lambda m: m.group(1) + CSS_COMMENT.sub("", m.group(2))
                                  + m.group(3), HTML_COMMENT.sub("", src))
            if out != src:
                with open(p, "w", encoding="utf-8", newline="") as fh:
                    fh.write(out)
                removed += len(src.encode("utf-8")) - len(out.encode("utf-8"))
    report["comments_removed"] = removed
    report["bytes_removed"] += removed


def _stage_features(tag, edition, manifest):
    """Copy web/ to a staging folder and strip the features this edition omits.

    The source tree is never modified. Stripping happens on the copy, the copy
    is what gets frozen, and the copy is what the syntax gate parses -- so what
    is checked is exactly what ships.
    """
    stage = os.path.join(HERE, "build", tag, "web")
    if os.path.isdir(stage):
        shutil.rmtree(stage, ignore_errors=True)
    shutil.copytree(WEB, stage)

    report = {"stripped": [], "kept": [], "manifest": bool(manifest),
              "bytes_removed": 0}
    if not manifest:
        return stage, report

    omit = {fid for fid, f in manifest.items()
            if edition == "release" and f.get("build") == "internal"}
    report["kept"] = sorted(set(manifest) - omit)
    if not omit:
        return stage, report

    cut_ids = set()

    def cut(m):
        if m.group(1) in omit:
            cut_ids.add(m.group(1))
            return ""
        return m.group(0)

    for base, _, files in os.walk(stage):
        for f in files:
            if not f.endswith((".js", ".html")):
                continue
            p = os.path.join(base, f)
            # newline="" on BOTH, and it is load-bearing on Windows, which is
            # where the release is frozen. Text mode collapses CRLF to \n on
            # the way in and expands \n to CRLF on the way out, so without it
            # this loop REWRITES the line endings of every file it touches and
            # `before - len(src)` is a count of characters reported as bytes.
            # Found 2026-09-18 by test_build.py's first run on Windows: the
            # stage was missing 994 bytes and this claimed 983, the difference
            # being exactly the 11 newlines inside the stripped spans. web/ is
            # LF on disc, so the damage ran the other way too -- a staged
            # app.js came out 12,064 bytes LARGER than the source it was cut
            # from, and EDITION.json published the shortfall to strangers.
            with open(p, encoding="utf-8", newline="") as fh:
                src = fh.read()
            out = SENTINEL_HTML.sub(cut, SENTINEL_JS.sub(cut, src))
            if out != src:
                with open(p, "w", encoding="utf-8", newline="") as fh:
                    fh.write(out)
                # Measured on the ENCODED text, because the field is called
                # bytes_removed and goes into EDITION.json. len() counts
                # characters: index.html's gated panels carry 24 bytes of
                # multi-byte UTF-8 (the arrows and dashes in their labels),
                # every one of which a character count records as 1. The
                # difference was small and would have stayed small, which is
                # the kind of number nobody re-derives once it is in a file
                # handed to strangers.
                report["bytes_removed"] += (len(src.encode("utf-8"))
                                            - len(out.encode("utf-8")))
    # Whole front-end modules that exist only to drive a gated panel. A
    # sentinel can carve a span out of a shared file; it cannot delete a file
    # that is gated end to end, and four of these shipped complete in the
    # public zip -- their panels' full implementation, one showTab() away.
    #
    # The name also has to leave app.js's loader list, or the release build
    # import()s a file that is not there. That is caught and logged, so it
    # would have been silent; the sentinel around each name is what makes it
    # not happen. See the loader at the bottom of app.js.
    dropped = []
    for fid in sorted(omit):
        for mod in FEATURE_MODULES.get(fid, ()):
            p = os.path.join(stage, mod)
            if os.path.isfile(p):
                report["bytes_removed"] += os.path.getsize(p)
                os.remove(p)
                dropped.append(mod)
                cut_ids.add(fid)
    report["dropped_modules"] = sorted(dropped)
    # Only what was actually removed. Listing the intention instead would put
    # "left out of this build" on the tin for code that is still in the tin.
    report["stripped"] = sorted(cut_ids)
    report["wanted_out"] = sorted(omit)
    return stage, report


def stage_web(tag, edition, manifest):
    """Copy web/ to a staging folder and strip what this edition omits.

    Features first (_stage_features), then, for a release, the HTML comments.
    `bytes_removed` stays the whole difference between source and stage.
    """
    stage, report = _stage_features(tag, edition, manifest)
    if edition == "release":
        strip_comments(stage, report)
    return stage, report


def snapshot(stage):
    """What the front end looked like at the moment it was frozen.

    The build reads a tree somebody may be editing. Two builds ten minutes
    apart are not the same product, and a zip with no record of what went into
    it cannot be told apart from one that has. So the file list and the sizes
    go into EDITION.json.

    It also flags near-empty modules: `app.js` loads new UI modules with
    dynamic import() inside try/catch, so a placeholder stub disables its
    feature silently and looks exactly like a working build.
    """
    files, stubs = {}, []
    for base, _, names in os.walk(stage):
        for f in sorted(names):
            if not f.endswith((".js", ".html", ".css")):
                continue
            rel = os.path.relpath(os.path.join(base, f), stage)
            n = os.path.getsize(os.path.join(base, f))
            files[rel.replace("\\", "/")] = n
            if f.endswith(".js") and n < 200:
                stubs.append("%s (%d B)" % (rel, n))

    # A module that is imported but not present disables its feature and logs
    # to a console nobody has open -- the same silence as a stub, from the
    # other direction. Only literal `./name.js` references can be seen from
    # here; a list of bare names built at runtime cannot, so this is a net with
    # holes in it and is reported as a warning rather than a gate.
    missing = set()
    for name in ("index.html", "app.js"):
        p = os.path.join(stage, name)
        if not os.path.isfile(p):
            continue
        with open(p, encoding="utf-8", errors="replace") as fh:
            src = fh.read()
        for m in re.finditer(r"""['"]\./([A-Za-z0-9_.-]+\.js)['"]""", src):
            if m.group(1) not in files:
                missing.add(m.group(1))
    return files, stubs, sorted(missing)


def manifest_gate(stage, manifest, edition, report, strict=False):
    """Do features.json and the @feature sentinels in the source agree?

    A feature is switched off in three independent places (spec 9.2): the
    server does not register its routes, the front end does not construct its
    UI, and the build strips its source. Only the third needs a sentinel, so
    the three ways the two sets can disagree are NOT equally bad:

      * a sentinel with no manifest entry  -> HARD FAIL. Nothing controls it.
        The strip cannot know whether to cut it, so it ships in both editions
        while looking like it is switched.
      * an internal-only feature with no sentinel -> LOUD WARNING in a release
        build. It is still switched off at runtime, but its source is in the
        zip. Recorded in EDITION.json rather than left for someone to find.
      * a "both" feature with no sentinel -> fine, and silent. There is
        nothing to strip; the sentinel would only ever mark a no-op.

    A fourth way they can disagree is one step further in -- a sentinel that
    carves a module's NAME out of app.js's import list while FEATURE_MODULES
    keeps the file, or the reverse. That one is loader_gate's, and it runs
    first, because it is the only disagreement here that breaks a build
    rather than over- or under-shipping one.

    --force downgrades the hard fail. Nothing downgrades the honesty.
    """
    ok = loader_gate(report, strict=strict)
    if manifest is None:
        print("  manifest    features.json absent -- nothing stripped, both "
              "editions are the same build")
        return ok
    # Two different questions, and they need two different trees.
    #
    # Orphans are asked of the STAGE, because the stage is what ships: a
    # sentinel still standing there is one nothing controls.
    #
    # "Has this feature got a sentinel at all?" must be asked of the SOURCE.
    # Asked of the stage it answers no for every feature that was stripped
    # successfully -- their sentinels left with the code -- so all eight of
    # this project's stripped features were being reported as features whose
    # source still ships, and landed in EDITION.json under BOTH
    # `features_omitted` and `switched_off_but_present`. Two contradictory
    # statements about the same feature, in a file handed to strangers, and
    # --strict failed the build over them.
    found = sentinel_ids(stage)
    in_source = sentinel_ids(WEB)
    orphans = sorted(set(found) - set(manifest))
    unstripped = sorted(fid for fid, f in manifest.items()
                        if f.get("build") == "internal" and fid not in in_source)
    if orphans:
        print("  MANIFEST FAIL  @feature sentinel with no features.json entry: %s"
              % ", ".join("%s (%s)" % (k, ", ".join(sorted(found[k])))
                          for k in orphans))
        ok = False
    report["unstripped_internal"] = unstripped if edition == "release" else []
    if unstripped and edition == "release":
        print("  manifest    WARNING: %d internal-only feature(s) have no "
              "@feature sentinel in web/," % len(unstripped))
        print("              so their source ships in the release zip and only "
              "the runtime switch")
        print("              turns them off: %s" % ", ".join(unstripped))
        if strict:
            ok = False
    print("  manifest    %d ids in features.json, %d marked in the source, "
          "%d still marked after the strip"
          % (len(manifest), len(in_source), len(found)))
    return ok


def syntax_gate(stage):
    ok, rows = jsgate.check_dir(stage)
    engines = sorted({r[1] for r in rows})
    print("  syntax      %d/%d front-end files parse  [%s]"
          % (sum(1 for r in rows if r[3]), len(rows), ", ".join(engines)))
    # Valid syntax is not a working front end. A lookup of an element that no
    # longer exists throws at module scope and kills the whole script, which
    # has shipped twice -- see jsgate.dangling_ids. Runs on the STAGED tree, so
    # it sees exactly the markup and JavaScript this edition will ship.
    alive, _fatal, _warn = jsgate.dangling_ids(stage)

    # The setup page ships from rdrshell/, not web/, so the sweep above never
    # saw it -- and it is the ONE page every new user meets before anything
    # else. Its own comment records it blanking once already, when a removed
    # step left orphaned handlers behind. Same gate, same reasons.
    first = os.path.join(HERE, "rdrshell", "firstrun.html")
    if os.path.isfile(first):
        fok, feng, fmsg = jsgate.check_html(first)
        print("  setup page  %s  [%s] %s"
              % ("parses" if fok else "SYNTAX FAIL", feng, fmsg))
        ok = ok and fok
    return ok and alive


# ----------------------------------------------------------------- leak gate

# Files that must never reach a stranger's machine, matched on basename
# anywhere in the tree. The point of matching everywhere rather than at the
# root is that the bug this gate exists to catch put ROADMAP.md in `_internal/`,
# not at the root -- the root was already correct.
PRIVATE_DOCS = {
    "ROADMAP.md": "internal roadmap: unfinished work, unverified claims",
    "README.md": "project working notes: describes the build machine's disc",
    "FORMAT_NOTES.md": "research notes",
    "CHECKSUMS.txt": "identifies the developer's own disc rip",
}

# Substrings that mean a file is development scaffolding wherever it appears.
PRIVATE_HINTS = ("01_Vanilla", "02_Modded", "07_Research", "RDR_VANILLA",
                 "RDR_MODDED", ".rdrmod.json")


def leak_gate(app_dir):
    """Private files found inside a finished RELEASE bundle.

    Returns [(relative path, why)]. Empty is the passing case.

    This exists because the edition filter used to live in exactly one place
    while two places were adding files. Anything that ships is now checked
    against what it IS, at the end, rather than trusted because the step that
    was supposed to exclude it looked correct.
    """
    hits = []
    for base, _, files in os.walk(app_dir):
        for f in files:
            full = os.path.join(base, f)
            rel = os.path.relpath(full, app_dir).replace("\\", "/")
            if f in PRIVATE_DOCS:
                hits.append((rel, PRIVATE_DOCS[f]))
                continue
            for hint in PRIVATE_HINTS:
                if hint in rel:
                    hits.append((rel, "references %s" % hint))
                    break
    return sorted(hits)


def download_check(zip_path, tag):
    """Run the app the way a STRANGER gets it: unzipped, and marked as downloaded.

    Every check before this one runs against the build folder, and a build
    folder has two properties no download has: it was never compressed, and it
    was never marked by Windows as coming from the internet. Both have now
    shipped a broken release.

    The second one is why this exists. Windows stamps a `Zone.Identifier`
    stream on every file extracted from a downloaded zip, .NET refuses to load
    a marked assembly, and pywebview's first act is to load one through
    pythonnet -- so 1.0.0 died on launch for everyone who downloaded it while
    passing 19/19 self-checks, a WebGL window check and a cold-start
    measurement here. Nothing that tested the build folder could have seen it.

    So: extract the zip somewhere else, mark every file exactly as a download
    would be marked, and run the app's own self-check against that. It is the
    only check in this file whose subject is the artefact rather than the
    build.
    """
    tmp = os.path.join(HERE, "build", tag, "downloadtest")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(tmp)

    marked = 0
    for base, _, files in os.walk(tmp):
        for name in files:
            try:
                with open(os.path.join(base, name) + ":Zone.Identifier", "w") as fh:
                    fh.write("[ZoneTransfer]\nZoneId=3\n")
                marked += 1
            except OSError:
                pass

    exe = None
    for base, _, files in os.walk(tmp):
        for name in files:
            if name == APPNAME + ".exe":
                exe = os.path.join(base, name)
                break
    if exe is None:
        return False, "no exe in the zip", 0

    report = os.path.join(HERE, "dist", "%s-download-selfcheck.txt" % tag)
    p = subprocess.run([exe, "--selfcheck", "--out", report],
                       capture_output=True, text=True, timeout=600)
    text = ""
    if os.path.isfile(report):
        with open(report, encoding="utf-8") as fh:
            text = fh.read()
    line = [l.strip() for l in text.splitlines() if "checks passed" in l]
    fails = [l.strip() for l in text.splitlines() if l.strip().startswith("FAIL")]
    ok = p.returncode == 0 and not fails
    detail = line[0] if line else (p.stderr or "no report").strip()[:200]
    if not ok and fails:
        detail = "%s | %s" % (detail, fails[0])
    shutil.rmtree(tmp, ignore_errors=True)
    return ok, detail, marked


# --------------------------------------------------------------------- build

def freeze(shape, numpy, tag, web_dir, edition):
    dist = os.path.join(HERE, "dist", tag)
    work = os.path.join(HERE, "build", tag, "pyi")
    # The spec needs the edition too. Without it, the only edition-aware step
    # was this file, and anything the spec bundled shipped in every build --
    # which is exactly how ROADMAP.md reached the public zip inside _internal/.
    env = {"RDR_ONEFILE": "1" if shape == "onefile" else "0",
           "RDR_WITH_NUMPY": "1" if numpy else "0",
           "RDR_WEB_DIR": web_dir,
           "RDR_EDITION": edition}
    t0 = time.perf_counter()
    p = sh([PY, "-m", "PyInstaller", "--noconfirm", "--clean",
            "--distpath", dist, "--workpath", work, SPEC], env)
    secs = time.perf_counter() - t0
    if p.returncode != 0:
        return None, secs
    if shape == "onefile":
        return os.path.join(dist, APPNAME + " (onefile).exe"), secs
    return os.path.join(dist, APPNAME), secs


def stamp(root, edition, tag, notes, report):
    """Write EDITION.json / EDITION.txt so a build can identify itself.

    Two zips that differ only in filename survive exactly one rename. This is
    how a bug report says which build it came from.
    """
    e = EDITIONS[edition]
    info = dict(edition=edition, label=e["label"], version=VERSION,
                built=time.strftime("%Y-%m-%d %H:%M:%S"), notes=notes or "",
                features_omitted=report.get("stripped", []),
                features_included=report.get("kept", []),
                manifest_present=report.get("manifest", False),
                # Switched off at runtime but still present in the files. The
                # difference matters to anyone auditing the zip.
                switched_off_but_present=report.get("unstripped_internal", []),
                front_end_files_dropped=report.get("dropped_modules", []),
                front_end=report.get("web", {}),
                front_end_stubs=report.get("stubs", []),
                front_end_missing=report.get("missing_modules", []))
    info["same_as_internal"] = (edition == "release"
                                and not report.get("stripped"))
    # Inside _internal/, not beside the exe: it is for bug reports and the
    # self-check, not something a user needs to see in the app folder.
    idir = os.path.join(root, "_internal")
    os.makedirs(idir, exist_ok=True)
    with open(os.path.join(idir, "EDITION.json"), "w", encoding="utf-8") as fh:
        json.dump(info, fh, indent=2)
    lines = ["%s %s -- %s build" % (APPNAME, VERSION, e["label"]),
             e["blurb"], "Built %s" % info["built"]]
    if notes:
        lines += ["", notes]
    if info["features_omitted"]:
        # "Removed" has to mean removed. It used to be printed for a feature
        # whose nav button was cut while its whole panel and its JS module
        # stayed in the zip, one showTab() away -- a false statement in a file
        # handed to strangers. The strip now takes the panel and the module
        # too, and the file list below says exactly which files left.
        lines += ["", "Removed from this build -- UI and server routes both:"] \
                 + ["  - %s" % f for f in info["features_omitted"]]
    if info["front_end_files_dropped"]:
        lines += ["", "Front-end files not present in this build:"] + \
                 ["  - %s" % f for f in info["front_end_files_dropped"]]
    if info["front_end_stubs"]:
        lines += ["", "Front-end modules that are placeholders in this build:"] + \
                 ["  - %s" % f for f in info["front_end_stubs"]]
    if info["switched_off_but_present"]:
        lines += ["", "Switched off in this build, but the code is still here:"] + \
                 ["  - %s" % f for f in info["switched_off_but_present"]]
    if info["same_as_internal"] and not info["switched_off_but_present"]:
        lines += ["",
                  "This release is currently IDENTICAL to the internal build.",
                  "Nothing has been trimmed out of it yet."]
    with open(os.path.join(idir, "EDITION.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return info


def furnish(root, edition="internal"):
    """Everything that belongs beside the exe but is not code."""
    n = 0
    # The Modding Guide holds the explanations the UI's one-line copy points
    # at ("Details: Modding Guide") -- it ships in BOTH editions.
    # HOWTO-CUSTOM-MODELS.md too: the Weapon model panel and the level model
    # importer both name it, and until 1.4.0 they named a path
    # (05_Docs/...) that exists only on the build machine.
    #
    # They go INSIDE the bundle (_internal/docs/) and are read in the app's
    # Guides viewer (GET /api/guide), so the folder a user opens holds only
    # the exe, one short README.txt and licenses/.
    docs = os.path.join(root, "_internal", "docs")
    os.makedirs(docs, exist_ok=True)
    for name in ("USER_GUIDE.md", "MODDING_GUIDE.md", "HOWTO-CUSTOM-MODELS.md"):
        guide = os.path.join(PROJECT, "05_Docs", name)
        if os.path.isfile(guide):
            shutil.copyfile(guide, os.path.join(docs, name))
            n += 1
    for doc in ("README.txt",):
        src = os.path.join(HERE, doc)
        if os.path.isfile(src):
            shutil.copyfile(src, os.path.join(root, doc))
            n += 1
    # studio.py resolves ROADMAP.md as <resource dir>/../ROADMAP.md, which
    # frozen means one level ABOVE the bundle -- a place PyInstaller's datas
    # cannot express, because datas are always inside it. So it is copied here.
    #
    # INTERNAL ONLY. The Roadmap tab is stripped from the release build, so
    # shipping the file put a 25 KB document in the download that nothing
    # could open -- and it is our own working record besides: what is proven,
    # what is guessed, and every mistake made getting there. That belongs in
    # our build, not in a stranger's.
    if edition != "release":
        src = os.path.join(PROJECT, "ROADMAP.md")
        if os.path.isfile(src):
            shutil.copyfile(src, os.path.join(root, "ROADMAP.md"))
            n += 1
    n += ship_manifest(root, edition)
    rows = thirdparty.collect(VENV, root,
                              extra=[os.path.join(WEB, "three.module.js")])
    return n, len(rows)


def machine_gate(edition):
    """No path from THIS machine may be compiled into a public build.

    `rdrstudio/config.py` opened with ROOT = r"the repo folder" and
    PCSX2_DIR = r"<PCSX2 folder>". The shell overrides both at startup,
    so a real install writes under %LOCALAPPDATA% -- but they stayed as the
    fallback, which put one developer's drive layout, project folder naming
    and emulator location inside every stranger's download, and would have
    aimed a stranger's install at a D: drive that is not theirs if the
    override ever failed.

    Checked on the SOURCE that is about to be frozen rather than on the zip,
    because the modules end up inside a compressed PYZ where grep cannot see
    them -- which is exactly why this survived a leak gate that walks the
    built tree.
    """
    # Both editions: the full one is the public download of this fork, so it
    # must not carry the build machine's paths either.
    # Any drive-letter path into the project or emulator folders, and any
    # path through a real user's home folder (Public/Default are not one).
    bad = re.compile(r"""r?["'][A-Za-z]:[\\/]{1,2}(?:RDR-Modding|Emulation)"""
                     r"""|[Uu]sers[\\/]{1,2}(?!Public|Default|you\b)\w+[\\/]""")
    hits = []
    files = []
    for base in (os.path.join(TOOLS, "rdrstudio"),
                 os.path.join(HERE, "rdrshell")):
        for r, ds, fs in os.walk(base):
            ds[:] = [d for d in ds if d != "__pycache__"]
            files += [(os.path.relpath(os.path.join(r, f), base), os.path.join(r, f))
                      for f in sorted(fs) if f.endswith(".py")]
    for f, p in files:
        if True:
            with open(p, encoding="utf-8", errors="replace") as fh:
                for n, line in enumerate(fh, 1):
                    if line.lstrip().startswith("#"):
                        continue          # a comment ships, but discloses only prose
                    if bad.search(line):
                        hits.append("%s:%d  %s" % (f, n, line.strip()[:70]))
    if hits:
        print("  MACHINE PATH  this build would carry paths from the build "
              "machine:")
        for h in hits:
            print("     %s" % h)
        return False
    print("  machine     no build-machine paths in the packaged modules")
    return True


def ship_manifest(root, edition):
    """Put features.json where the frozen app actually looks for it.

    features.py calls itself "the authority" and resolves the manifest as
    <parent of the package>/features.json. Frozen, that is `_internal/`, and
    NOTHING put a copy there -- not this file, not the spec. So every frozen
    build silently fell back to features.py's hardcoded DEFAULTS list, which
    had drifted from the real manifest:

      * scripting_browser is `internal` in features.json and `both` in
        DEFAULTS, so it was ON in the release build
      * `emulator` is not in DEFAULTS at all, and a missing id reads as off --
        which is the Play and Boot test buttons, promoted to release on
        purpose
      * five more internal ids were missing, and only failed closed by luck

    Exactly the shape the spec's own comment describes for ROADMAP.md: one
    file, two owners, one of them uninformed. This is the third instance.

    Filtered to the edition, so the release build does not carry the
    description of work being held back -- each `why` is a candid note about
    what is unproven, written for us.
    """
    src = os.path.join(TOOLS, "features.json")
    if not os.path.isfile(src):
        return 0
    with open(src, encoding="utf-8") as fh:
        doc = json.load(fh)
    items = doc.get("features") or []
    if edition == "release":
        items = [f for f in items
                 if (f.get("build") or "both").lower() == "both"]
    # PyInstaller onedir puts the package under _internal/; a onefile bundle
    # unpacks elsewhere and reads it from the temp dir, so only write the one
    # that exists rather than guessing.
    dest_dir = os.path.join(root, "_internal")
    if not os.path.isdir(dest_dir):
        return 0
    # `edition` is how the frozen app knows which build it is. Nothing sets
    # RDR_BUILD at freeze time or at launch, so without this both editions
    # answered "release" and the internal build gated itself as a public one.
    with open(os.path.join(dest_dir, "features.json"), "w",
              encoding="utf-8") as fh:
        json.dump(dict(edition=edition, features=items), fh, indent=2)
    return 1


def zip_up(root, name):
    import zipfile
    out = os.path.join(HERE, "dist", name)
    if os.path.exists(out):
        os.remove(out)
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for base, _, files in os.walk(root):
            for f in files:
                full = os.path.join(base, f)
                # Everything inside one top folder. A zip whose entries sit at
                # the archive root sprays 170 files into whatever directory the
                # user extracted from, and the exe stops working because
                # _internal lands somewhere else. That shipped once.
                z.write(full, os.path.join(APPNAME,
                                           os.path.relpath(full, root)))
                n += 1
    return out, n, os.path.getsize(out)


# ---------------------------------------------------------------- the checks

def selfcheck(exe, tag):
    report = os.path.join(HERE, "dist", "%s-selfcheck.txt" % tag)
    p = subprocess.run([exe, "--selfcheck", "--out", report],
                       capture_output=True, text=True)
    text = ""
    if os.path.isfile(report):
        with open(report, encoding="utf-8") as fh:
            text = fh.read()
    line = [l for l in text.splitlines() if "checks passed" in l]
    fails = [l.strip() for l in text.splitlines() if l.strip().startswith("FAIL")]
    print("  self-check  %s%s" % (line[0].strip() if line else "no report",
                                  "" if p.returncode == 0 else
                                  "   <-- NOT SHIPPABLE"))
    for f in fails:
        print("              %s" % f)
    return p.returncode == 0, (line[0].strip() if line else ""), report


def window_check(exe, tag, shot=True):
    """Open the real window and render a real cube. The make-or-break test."""
    out = os.path.join(HERE, "dist", "%s-window.json" % tag)
    if os.path.exists(out):
        os.remove(out)
    png = os.path.join(HERE, "dist", "%s-window.png" % tag)

    proc = subprocess.Popen([exe, "--verify-window", "--out", out,
                             "--hold", "10" if shot else "0"])
    grabbed = ""
    if shot:
        # The window is up for --hold seconds after the probe writes its JSON,
        # so the picture is of the same window the numbers came from.
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < 90 and not os.path.isfile(out):
            time.sleep(0.5)
        time.sleep(1.0)
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy",
                            "Bypass", "-File",
                            os.path.join(HERE, "capture_window.ps1"),
                            "-Title", APPNAME, "-Out", png],
                           capture_output=True, text=True)
        grabbed = (r.stdout or "").strip().splitlines()[-1] if r.returncode == 0 \
            else "screenshot failed"
    try:
        proc.wait(timeout=150)
    except subprocess.TimeoutExpired:
        proc.kill()

    if not os.path.isfile(out):
        print("  window      NO REPORT -- the window check never answered")
        return False, {}, ""
    with open(out, encoding="utf-8") as fh:
        res = json.load(fh)
    w = res.get("webgl", {})

    # UNMEASURED IS NOT BROKEN, and this used to say it was. Any non-ok result
    # printed "3D DOES NOT RENDER", including the one that means no window ever
    # opened -- in which case nothing rendered because nothing could, and every
    # field on the line is None precisely because the probe never got to look.
    #
    # It cost two builds on 2026-09-18. Both reported 3D broken and exited 1;
    # both ran from an automated session with no interactive desktop; the front
    # end had not been touched since 09-14 or the 3D stack since 08-12. It took
    # asking the developer to launch the exe to establish that the viewport renders
    # perfectly. The probe had measured nothing and reported a verdict.
    started = bool(res.get("front_end")) or res.get("stage") not in (None, "webgl")
    if not res.get("ok") and not started and "failed to start" in (res.get("error") or ""):
        print("  window      NOT MEASURED -- the window never opened: %s"
              % res.get("error"))
        print("              This says nothing about 3D. A session with no")
        print("              interactive desktop cannot open one; run the build")
        print("              where a desktop exists to measure it.")
        if grabbed:
            print("              %s" % grabbed)
        return None, res, png

    print("  window      WebGL2 %s | %s | three r%s | %s lit pixels of 16384%s"
          % (w.get("webgl2"), str(w.get("renderer"))[:44],
             w.get("three_revision"), w.get("three_rendered_pixels"),
             "" if res.get("ok") else "   <-- 3D DOES NOT RENDER"))
    if grabbed:
        print("              %s" % grabbed)
    return bool(res.get("ok")), res, png


def measure(exe, tag, runs=3):
    ps = os.path.join(HERE, "measure_app.ps1")
    p = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", ps, "-Exe", exe, "-Runs", str(runs),
                        "-Label", tag], capture_output=True, text=True)
    best = None
    for line in (p.stdout or "").splitlines():
        if "best of" in line:
            print("  cold start  %s" % line.split(":")[-1].strip())
            try:
                best = float(line.split(":")[-1].replace("s", "").strip())
            except ValueError:
                pass
    return best


# ----------------------------------------------------------------------- main

def one(edition, shape, numpy, args):
    tag = edition if shape == "onedir" else "%s-%s" % (edition, shape)
    if not numpy:
        tag += "-nonumpy"
    print("\n=== %s (%s%s) ===" % (EDITIONS[edition]["label"], shape,
                                   ", numpy" if numpy else ", no numpy"))

    manifest = read_manifest()
    stage, report = stage_web(tag, edition, manifest)
    if report["stripped"]:
        print("  strip       removed %s (%d bytes)"
              % (", ".join(report["stripped"]), report["bytes_removed"]))
    report["web"], stubs, missing = snapshot(stage)
    report["stubs"] = stubs
    report["missing_modules"] = missing
    print("  front end   %d files captured at %s"
          % (len(report["web"]), time.strftime("%H:%M:%S")))
    if missing:
        print("  front end   WARNING: imported but not in the bundle: %s"
              % ", ".join(missing))
    if stubs:
        print("  front end   WARNING: %d module(s) are near-empty placeholders "
              "and will load as no-ops:" % len(stubs))
        print("              %s" % ", ".join(stubs))

    if not manifest_gate(stage, manifest, edition, report,
                         strict=args.strict) and not args.force:
        return {"tag": tag, "failed": "manifest gate"}
    if not syntax_gate(stage) and not args.force:
        return {"tag": tag, "failed": "JS syntax gate"}

    root, secs = freeze(shape, numpy, tag, stage, edition)
    if root is None:
        print("  BUILD FAILED")
        return {"tag": tag, "failed": "PyInstaller"}
    exe = root if shape == "onefile" else os.path.join(root, APPNAME + ".exe")
    app_dir = os.path.dirname(root) if shape == "onefile" else root
    print("  frozen      %.0f s" % secs)

    info = {"tag": tag, "edition": edition, "shape": shape, "numpy": numpy,
            "build_seconds": round(secs, 1),
            "features_omitted": report["stripped"]}

    if shape == "onedir":
        docs, lics = furnish(root, edition)
        info["edition_stamp"] = stamp(root, edition, tag, args.notes, report)
        print("  furnished   %d docs, %d licence components" % (docs, lics))

    info["files"] = sum(len(f) for _, _, f in os.walk(app_dir))
    info["on_disk_mb"] = round(mb(size_of(app_dir)), 1)

    if not args.quick:
        ok, line, _ = selfcheck(exe, tag)
        info["selfcheck_ok"] = ok
        info["selfcheck"] = line
        if not args.no_window:
            wok, wres, png = window_check(exe, tag, shot=not args.no_shot)
            info["window_ok"] = wok
            info["webgl"] = wres.get("webgl", {})
            info["screenshot"] = png if os.path.isfile(png) else ""
        info["cold_start_s"] = measure(exe, tag)

    if edition == "release":
        if not machine_gate(edition) and not args.force:
            return {"tag": tag, "failed": "machine path gate"}
        leaked = leak_gate(app_dir)
        info["leaked"] = leaked
        if leaked:
            print("  LEAK GATE   %d private file(s) in the public build:"
                  % len(leaked))
            for rel, why in leaked:
                print("              %-44s %s" % (rel, why))
            if not args.force:
                return {"tag": tag, "failed": "leak gate"}
            print("              --force given; shipping anyway")
        else:
            print("  leak gate   clean")

    if shape == "onedir":
        out, n, size = zip_up(root, EDITIONS[edition]["zip"]
                              if numpy else "%s.zip" % tag)
        info["zip"] = out
        info["zipped_mb"] = round(mb(size), 1)
        print("  download    %-38s %.1f MB, %d files"
              % (os.path.basename(out), mb(size), n))

        if not args.quick:
            dok, ddetail, dmarked = download_check(out, tag)
            info["download_check_ok"] = dok
            info["download_check"] = ddetail
            print("  as-download %s   (%d files marked as downloaded)"
                  % (ddetail, dmarked))
            if not dok:
                print("              <-- NOT SHIPPABLE: the zip fails when it is "
                      "actually downloaded")
                if not args.force:
                    return dict(info, tag=tag, failed="download check")
    else:
        info["zipped_mb"] = 0.0
    print("  on disk     %.1f MB, %d files" % (info["on_disk_mb"], info["files"]))
    return info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", choices=("release", "internal"),
                    default="release")
    ap.add_argument("--both", action="store_true")
    ap.add_argument("--shape", choices=("onedir", "onefile"), default="onedir")
    ap.add_argument("--no-numpy", action="store_true",
                    help="build without the numpy fast paths, for comparison")
    ap.add_argument("--quick", action="store_true",
                    help="skip self-check, window check and timing")
    ap.add_argument("--no-window", action="store_true")
    ap.add_argument("--no-shot", action="store_true")
    ap.add_argument("--strict", action="store_true",
                    help="also fail when an internal-only feature has no "
                         "@feature sentinel to strip")
    ap.add_argument("--force", action="store_true",
                    help="package even if a gate fails. It will not be good.")
    ap.add_argument("--notes", default="", help="one line: what changed")
    args = ap.parse_args()

    editions = ("release", "internal") if args.both else (args.build,)
    out = []
    for ed in editions:
        out.append(one(ed, args.shape, not args.no_numpy, args))

    print("\n=== summary ===")
    print("%-18s %9s %9s %8s %9s  %-8s %s"
          % ("build", "on disk", "download", "build", "cold start",
             "3D", "self-check"))
    for i in out:
        if i.get("failed"):
            print("%-18s  FAILED: %s" % (i["tag"], i["failed"]))
            continue
        print("%-18s %7.1f MB %7.1f MB %6.0f s %7s s  %-8s %s"
              % (i["tag"], i["on_disk_mb"], i["zipped_mb"], i["build_seconds"],
                 i.get("cold_start_s", "-"),
                 "OK" if i.get("window_ok") else
                 ("not-meas" if i.get("window_ok") is None else "BROKEN"),
                 i.get("selfcheck", "skipped")))

    with open(os.path.join(HERE, "dist", "build_report.json"), "w") as fh:
        json.dump(out, fh, indent=2)
    # `window_ok is None` means NOT MEASURED -- no window could be opened -- and
    # an unmeasurable environment must not fail a build that is otherwise sound.
    # Only a definite False, meaning a window opened and rendered nothing, does.
    good = all(not i.get("failed")
               and i.get("selfcheck_ok", True)
               and i.get("window_ok", True) is not False for i in out)
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
