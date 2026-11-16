#!/usr/bin/env python3
"""Prove the parts of build.py that decide what ships, without freezing anything.

build.py's own gates only ever run at the end of a 10-minute PyInstaller
freeze, on this machine, with a Windows venv and a GPU. So the logic that
decides WHAT GOES IN THE ZIP has never been exercised on its own -- and that
logic is where every bug the file's comments describe actually lived: a strip
that reported removing code it had left in, a private document inside
_internal/, a zip whose entries sprayed into the user's folder, a manifest the
frozen app never read.

This runs that logic against a synthetic project tree in a temp directory:

    1  the strip: what release removes, and what it leaves alone
    2  the source tree is never touched
    3  "removed" means removed -- intent vs. fact
    4  features.json absent, broken, or in list form
    5  the manifest gate: orphan sentinels, unstripped internals
    6  snapshot: stubs, and modules imported but not shipped
    7  the syntax gate catches a strip that breaks a file
    8  a stripped tab that leaves a lookup behind (null.onclick)
    9  the leak gate
   10  the machine-path gate
   11  ship_manifest: the copy the frozen app reads
   12  EDITION.txt/json say what is true
   13  the zip has one top folder

No PyInstaller, no venv, no exe, no network, no GPU. Writes only inside its own
temp directories and deletes them. Runs on Linux and Windows.

    python packaging/test_build.py [--keep]
"""

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import build                                              # noqa: E402
import jsgate                                             # noqa: E402

# build.py resolves everything off six module globals at import time. Every
# fixture repoints them at a sandbox, so nothing here can reach the real tree.
# FEATURE_MODULES is in the list because loader_gate reads it against WEB:
# left pointing at the real four modules while WEB is a sandbox holding two,
# it reports a mismatch that is the fixture's, not the code's. Repointed by
# assignment, never mutated in place -- ORIG holds the real dict itself.
ROOTED = ("HERE", "TOOLS", "PROJECT", "WEB", "MANIFEST", "FEATURE_MODULES")
ORIG = {k: getattr(build, k) for k in ROOTED}

FAILED = []
SANDBOXES = []


def step(n, label):
    print("\n[%s] %s" % (n, label))


def check(ok, label, detail=""):
    print("   %s  %s%s" % ("PASS" if ok else "FAIL", label,
                           ("  -- " + detail) if detail else ""))
    if not ok:
        FAILED.append(label)
    return ok


# ------------------------------------------------------------------ fixtures

# A front end small enough to read and large enough to strip. Every .js here
# is over 200 bytes except stub.js, which is the stub detector's subject.

INDEX = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>t</title></head><body>
<div id="app"></div>
<button id="btnSave">Save</button>
<!-- @feature:menu_edit -->
<div id="panelMenus"><h2>MEDIA ▸ Menus — gated</h2>
<button id="btnPlay">Play</button></div>
<!-- @end:menu_edit -->
<!-- @feature:behaviour_tables -->
<div id="panelStates"></div>
<!-- @end:behaviour_tables -->
<script type="module" src="./app.js"></script>
</body></html>
"""

APP_HEAD = """// the studio front end, in miniature
const $ = (sel) => document.querySelector(sel);

export function init() {
  $('#app').textContent = 'ready';
  $('#btnSave').textContent = 'Save';
}

$('#btnSave').onclick = () => init();
"""

# The gated button's handler, wrapped so the strip takes it with the markup.
APP_GATED_OK = """
/* @feature:menu_edit */
$('#btnPlay').onclick = () => init();
/* @end:menu_edit */
"""

# The same handler with no sentinel: the markup goes, the lookup stays, and it
# is at column 0 so it runs at module scope. This is the bug that shipped twice.
APP_GATED_DANGLING = """
$('#btnPlay').onclick = () => init();
"""

APP_LOADER_OK = """
const MODULES = [
  './shared.js',
  /* @feature:menu_edit */ './menus.js', /* @end:menu_edit */
  /* @feature:behaviour_tables */ './states.js', /* @end:behaviour_tables */
];
for (const m of MODULES) { import(m).catch(() => {}); }
"""

# The module file is dropped by FEATURE_MODULES but its name stays in the
# loader list -- the release build then import()s a file that is not there.
APP_LOADER_LOOSE = """
const MODULES = ['./shared.js', './menus.js', './states.js'];
for (const m of MODULES) { import(m).catch(() => {}); }
"""

# The shape the real app.js uses: BARE names, joined to a path at runtime.
# snapshot()'s literal-reference check cannot see through the template string
# -- it says so -- so this is the shape loader_gate exists for, and the one
# the fixtures above do not cover.
APP_MODLIST_OK = """
const qs = new URLSearchParams(location.search);
const mods = qs.has('nomods') ? []
  : qs.get('mods') ? qs.get('mods').split(',')
  : ['shared',
     /* @feature:menu_edit */ 'menus', /* @end:menu_edit */
     /* @feature:behaviour_tables */ 'states', /* @end:behaviour_tables */
    ];
for (const m of mods) { import(`./${m}.js`).catch(() => {}); }
"""

# The same list with one sentinel taken off. states.js still leaves the
# bundle; its name stays behind and is import()ed into thin air.
APP_MODLIST_LOOSE = APP_MODLIST_OK.replace(
    "/* @feature:behaviour_tables */ 'states', /* @end:behaviour_tables */",
    "'states',")

# An import list with no sentinel anywhere in it, so nothing marks which of
# these names is supposed to leave. Not the same as a list with nothing wrong.
APP_MODLIST_BLIND = (APP_MODLIST_OK
                     .replace("/* @feature:menu_edit */ ", "")
                     .replace(" /* @end:menu_edit */", "")
                     .replace("/* @feature:behaviour_tables */ ", "")
                     .replace(" /* @end:behaviour_tables */", ""))

SHARED = """// shared helpers, in both editions
export function clamp(v, lo, hi) {
  return v < lo ? lo : (v > hi ? hi : v);
}
export function fmt(n) {
  return Number(n).toFixed(3);
}
export function pad(s, n) {
  return String(s).padStart(n, ' ');
}
"""

# A sentinel for an id features.json has never heard of. Nothing controls it,
# so it ships in both editions while looking switched.
SHARED_ORPHAN = """
/* @feature:orphan_panel */
export function orphan() { return 1; }
/* @end:orphan_panel */
"""

# The sentinel opens inside a block and closes outside it. Stripping takes the
# opening brace and leaves the closing one: a syntax error made by the build.
SHARED_UNBALANCED = """
/* @feature:menu_edit */
export function gated() {
/* @end:menu_edit */
  return 1;
}
"""

MENUS = """// menus.js -- exists only to drive the menu_edit panel
export function openMenus(host) {
  host.innerHTML = '<ul class="menus"></ul>';
  return host.firstChild;
}
export const MENU_KIND = 'menu';
export function closeMenus(host) {
  host.innerHTML = '';
}
"""

STATES = """// states.js -- exists only to drive the behaviour_tables panel
export function openStates(host) {
  host.innerHTML = '<table class="states"></table>';
  return host.firstChild;
}
export const STATE_KIND = 'state';
export function closeStates(host) {
  host.innerHTML = '';
}
"""

STUB = "// TODO: implement\nexport function todo() {}\n"

EXTRA = """// in a subfolder, to prove the walk is a walk
export const DEPTH = 2;
export function deep(x) { return x + DEPTH; }
export function shallow(x) { return x - DEPTH; }
export const LABEL = 'a subfolder module, padded past the stub threshold';
"""

CSS = ".app { color: #222; }\n"

BASE_FEATURES = [
    {"id": "swap_character", "label": "Swap character", "build": "both"},
    {"id": "menu_edit", "label": "Edit menus", "build": "internal"},
    {"id": "behaviour_tables", "label": "Behaviour tables", "build": "internal"},
    # internal-only, and nothing in web/ is marked for it: the honest
    # "switched off but the source ships" case.
    {"id": "ghost_feature", "label": "Ghost", "build": "internal"},
    # no build key at all -- read as "both", so release keeps it.
    {"id": "no_build_key", "label": "Unmarked"},
]

CLEAN_CONFIG = '''"""config"""
import os
ROOT = os.environ.get("RDR_ROOT", "")
PCSX2_DIR = os.environ.get("RDR_PCSX2", "")
'''

DIRTY_CONFIG = '''"""config"""
ROOT = r"E:\\RDR-Modding"
PCSX2_DIR = r"E:\\Emulation\\PCSX2"
'''

# The same literal, commented out. A comment ships, but it discloses only
# prose -- the gate is documented to let it through.
COMMENTED_CONFIG = '''"""config"""
import os
# was: ROOT = r"E:\\RDR-Modding"
ROOT = os.environ.get("RDR_ROOT", "")
'''

HOME_CONFIG = '''"""config"""
CACHE = "C:\\\\Users\\\\someone\\\\AppData\\\\Local\\\\rdr"
'''


def world(features=BASE_FEATURES, manifest=True, dangling=False,
          unbalanced=False, orphan=False, loose_loader=False,
          config=CLEAN_CONFIG):
    """A throwaway project tree with build.py's globals pointed at it."""
    root = tempfile.mkdtemp(prefix="test_build-")
    SANDBOXES.append(root)
    tools = os.path.join(root, "03_Tools")
    pack = os.path.join(tools, "packaging")
    web = os.path.join(tools, "web")
    for d in (os.path.join(pack, "dist"), os.path.join(pack, "rdrshell"),
              os.path.join(tools, "rdrstudio"), os.path.join(web, "sub"),
              os.path.join(root, "05_Docs")):
        os.makedirs(d)

    app = APP_HEAD \
        + (APP_GATED_DANGLING if dangling else APP_GATED_OK) \
        + (APP_LOADER_LOOSE if loose_loader else APP_LOADER_OK)
    shared = SHARED + (SHARED_ORPHAN if orphan else "") \
        + (SHARED_UNBALANCED if unbalanced else "")

    # newline="" so a fixture is the same size on disc as the string it came
    # from. Without it, Windows writes CRLF and two checks that compare
    # len(FIXTURE) against a byte count on disc fail there and nowhere else --
    # which is how `stub.js (47 B)` against a 45-character STUB was the first
    # sign of the line-ending bug in stage_web.
    write = lambda p, s: open(p, "w", encoding="utf-8",
                              newline="").write(s)
    write(os.path.join(web, "index.html"), INDEX)
    write(os.path.join(web, "app.js"), app)
    write(os.path.join(web, "shared.js"), shared)
    write(os.path.join(web, "menus.js"), MENUS)
    write(os.path.join(web, "states.js"), STATES)
    write(os.path.join(web, "stub.js"), STUB)
    write(os.path.join(web, "style.css"), CSS)
    write(os.path.join(web, "sub", "extra.js"), EXTRA)
    write(os.path.join(tools, "rdrstudio", "config.py"), config)
    write(os.path.join(pack, "rdrshell", "paths.py"), "APP = 'rdrstudio'\n")
    write(os.path.join(root, "ROADMAP.md"), "# internal roadmap\n")
    write(os.path.join(root, "05_Docs", "MODDING_GUIDE.md"), "# guide\n")
    write(os.path.join(pack, "README.txt"), "readme\n")
    if manifest:
        write(os.path.join(tools, "features.json"),
              json.dumps({"features": features}, indent=2))

    build.HERE = pack
    build.TOOLS = tools
    build.PROJECT = root
    build.WEB = web
    build.MANIFEST = os.path.join(tools, "features.json")
    # What this sandbox's web/ actually holds, and what APP_LOADER_OK gates.
    build.FEATURE_MODULES = {"menu_edit": ["menus.js"],
                             "behaviour_tables": ["states.js"]}
    return root, tools, pack, web


def tree(root, exts=(".js", ".html", ".css")):
    """{relative path: bytes} for everything under root with these extensions."""
    out = {}
    for base, _, files in os.walk(root):
        for f in files:
            if exts and not f.endswith(exts):
                continue
            p = os.path.join(base, f)
            out[os.path.relpath(p, root).replace("\\", "/")] = os.path.getsize(p)
    return out


def contents(root):
    """{relative path: text} -- for proving bytes did not move."""
    out = {}
    for base, _, files in os.walk(root):
        for f in files:
            p = os.path.join(base, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root).replace("\\", "/")] = fh.read()
    return out


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# --------------------------------------------------------------------- tests

def t_strip():
    step(1, "the strip: what release removes and internal keeps")
    _, _, _, web = world()
    m = build.read_manifest()

    stage_i, rep_i = build.stage_web("internal", "internal", m)
    check(rep_i["stripped"] == [], "internal strips nothing",
          str(rep_i["stripped"]))
    check(rep_i["bytes_removed"] == 0, "internal removes 0 bytes",
          str(rep_i["bytes_removed"]))
    check(sorted(rep_i["kept"]) == sorted(f["id"] for f in BASE_FEATURES),
          "internal keeps every feature id", "%d" % len(rep_i["kept"]))
    check(os.path.isfile(os.path.join(stage_i, "menus.js")),
          "internal still has menus.js")
    check("@feature:menu_edit" in read(os.path.join(stage_i, "index.html")),
          "internal still has the gated markup")

    stage_r, rep_r = build.stage_web("release", "release", m)
    check(sorted(rep_r["stripped"]) == ["behaviour_tables", "menu_edit"],
          "release strips exactly the two marked internal features",
          ", ".join(sorted(rep_r["stripped"])))
    check(sorted(rep_r["kept"]) == ["no_build_key", "swap_character"],
          "release keeps both/unmarked features only",
          ", ".join(sorted(rep_r["kept"])))
    check(not os.path.isfile(os.path.join(stage_r, "menus.js"))
          and not os.path.isfile(os.path.join(stage_r, "states.js")),
          "release drops the whole feature-only modules")
    check(rep_r["dropped_modules"] == ["menus.js", "states.js"],
          "and says which files left", ", ".join(rep_r["dropped_modules"]))

    idx = read(os.path.join(stage_r, "index.html"))
    app = read(os.path.join(stage_r, "app.js"))
    check("panelMenus" not in idx and "btnPlay" not in idx,
          "the gated panel and its button are gone from the markup")
    check("panelStates" not in idx, "the second gated panel is gone too")
    check('id="app"' in idx and "btnSave" in idx,
          "ungated markup survives")
    check("menus.js" not in app and "states.js" not in app,
          "the dropped modules' names left the loader list")
    check("'./shared.js'" in app, "the shared module is still loaded")
    check("clamp" in read(os.path.join(stage_r, "shared.js")),
          "a file with no sentinels is untouched")
    check(os.path.isfile(os.path.join(stage_r, "sub", "extra.js")),
          "subfolders are staged too")

    # bytes_removed is the whole difference and nothing else: every byte the
    # report claims to have removed is a byte that is missing from the stage.
    #
    # The gated panel in INDEX carries a non-ASCII heading on purpose. tree()
    # reports what the filesystem holds, so this check reads bytes on both
    # sides -- but only if the span being removed has a character that is not
    # one byte does it tell len(text) apart from len(text.encode()). It did
    # not, so stage_web counted characters and called them bytes, and the
    # real index.html's gated panels were over by 24 with nothing red.
    src = sum(tree(web, (".js", ".html")).values())
    out = sum(tree(stage_r, (".js", ".html")).values())
    check(rep_r["bytes_removed"] == src - out,
          "bytes_removed is exactly what the stage is missing",
          "%d claimed, %d actual" % (rep_r["bytes_removed"], src - out))


def t_source_untouched():
    step(2, "the source tree is never modified")
    _, _, _, web = world()
    before = contents(web)
    m = build.read_manifest()
    build.stage_web("release", "release", m)
    build.stage_web("internal", "internal", m)
    after = contents(web)
    check(before == after, "web/ is byte-identical after both editions staged",
          "%d files" % len(before))


def t_intent_vs_fact():
    step(3, '"removed" has to mean removed')
    world()
    m = build.read_manifest()
    _, rep = build.stage_web("release", "release", m)
    # ghost_feature is internal-only, so the build WANTS it out -- but nothing
    # in web/ is marked for it, so nothing was removed. Saying "removed" here
    # would put a false statement in a file handed to strangers.
    check("ghost_feature" in rep["wanted_out"],
          "an internal-only feature with no sentinel is in wanted_out")
    check("ghost_feature" not in rep["stripped"],
          "...and is NOT reported as stripped")
    check(sorted(rep["wanted_out"]) ==
          ["behaviour_tables", "ghost_feature", "menu_edit"],
          "wanted_out is the full intent", ", ".join(sorted(rep["wanted_out"])))


def t_manifest_reading():
    step(4, "features.json absent, broken, or in list form")
    world(manifest=False)
    check(build.read_manifest() is None, "absent manifest reads as None")
    _, rep = build.stage_web("nom", "release", None)
    check(rep["manifest"] is False and rep["stripped"] == [],
          "with no manifest, release strips nothing")
    stage = os.path.join(build.HERE, "build", "nom", "web")
    check(tree(stage) == tree(build.WEB),
          "...so both editions really are the same build")

    _, tools, _, _ = world()
    with open(build.MANIFEST, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    check(build.read_manifest() is None, "unreadable manifest reads as None")

    # read_manifest() carries `raw if isinstance(raw, list) else []` for the
    # list form, so a bare list is a shape it means to accept. It must not
    # take the build down with a traceback.
    world()
    with open(build.MANIFEST, "w", encoding="utf-8") as fh:
        json.dump([{"id": "a", "build": "both"}, {"label": "no id"}], fh)
    try:
        m, err = build.read_manifest(), ""
    except Exception as ex:                                # noqa: BLE001
        m, err = None, "raised %s: %s" % (type(ex).__name__, ex)
    check(m == {"a": {"id": "a", "build": "both"}},
          "a bare list is accepted and an entry with no id is dropped",
          err or str(m))


def t_manifest_gate():
    step(5, "the manifest gate")
    world(orphan=True)
    m = build.read_manifest()
    stage, rep = build.stage_web("orph", "release", m)
    check(build.manifest_gate(stage, m, "release", rep) is False,
          "a sentinel with no features.json entry is a HARD FAIL")

    world()
    m = build.read_manifest()
    stage, rep = build.stage_web("warn", "release", m)
    ok = build.manifest_gate(stage, m, "release", rep)
    check(ok is True, "an internal feature with no sentinel only warns")
    check("ghost_feature" in rep["unstripped_internal"],
          "...and is recorded, not left for someone to find")
    check(build.manifest_gate(stage, m, "release", dict(rep), strict=True)
          is False, "--strict turns that warning into a failure")

    # THE POINT: the gate runs on the post-strip stage, where a successfully
    # stripped feature no longer has a sentinel -- so it must not be counted
    # as one whose source still ships. These two lists describe opposite
    # states and cannot name the same feature.
    both = sorted(set(rep["stripped"]) & set(rep["unstripped_internal"]))
    check(not both,
          "no feature is reported as BOTH removed and still present",
          "in both lists: " + ", ".join(both) if both else "")

    _, _, _, _ = world()
    m = build.read_manifest()
    stage, rep = build.stage_web("int", "internal", m)
    build.manifest_gate(stage, m, "internal", rep)
    check(rep["unstripped_internal"] == [],
          "the internal build has nothing to warn about")

    check(build.manifest_gate(stage, None, "release", rep) is True,
          "no manifest passes the gate")


def t_snapshot():
    step(6, "snapshot: stubs and modules that will not be there")
    _, _, _, web = world()
    m = build.read_manifest()
    stage, _ = build.stage_web("snap", "internal", m)
    files, stubs, missing = build.snapshot(stage)
    check(files == tree(stage), "every .js/.html/.css is captured with its size",
          "%d files" % len(files))
    check("sub/extra.js" in files, "paths are relative and forward-slashed")
    check(stubs == ["stub.js (%d B)" % len(STUB)],
          "the near-empty module is flagged", ", ".join(stubs))
    check(missing == [], "nothing is imported that is not there")

    # The strip drops menus.js. If its name did not leave the loader list, the
    # release build import()s a file it does not have -- caught and logged, so
    # silent. It has to be visible before the freeze.
    world(loose_loader=True)
    m = build.read_manifest()
    stage, _ = build.stage_web("loose", "release", m)
    _, _, missing = build.snapshot(stage)
    check(missing == ["menus.js", "states.js"],
          "a dropped module still named in the loader list is reported",
          ", ".join(missing))


def t_syntax_gate():
    step(7, "the syntax gate catches a strip that breaks a file")
    world(unbalanced=True)
    m = build.read_manifest()
    stage_i, _ = build.stage_web("bal-int", "internal", m)
    ok, rows = jsgate.check_dir(stage_i, out=open(os.devnull, "w"))
    check(ok is True, "the unstripped source parses",
          "%d files" % len(rows))

    stage_r, _ = build.stage_web("bal-rel", "release", m)
    broken = os.path.join(stage_r, "shared.js")
    check("return 1;" in read(broken) and read(broken).count("}") >
          read(broken).count("{"),
          "the strip really did leave an unmatched brace behind")

    # jsgate.check_file uses `node --check` when node is on PATH and its own
    # balance scanner otherwise, and the build takes whichever answers. Both
    # have to catch this, so the verdict names the engine that gave it.
    ok_file, engine, msg = jsgate.check_file(broken)
    check(ok_file is False,
          "check_file rejects the file the strip broke",
          "%s said %s" % (engine, "ok" if ok_file else msg))

    try:
        jsgate.check_text(read(broken))
        scanner = "accepted it"
    except jsgate.Problem as ex:
        scanner = str(ex)
    check(scanner != "accepted it",
          "the balance scanner rejects it", scanner)

    ok, rows = jsgate.check_dir(stage_r, out=open(os.devnull, "w"))
    bad = [r[0] for r in rows if not r[3]]
    check(ok is False and bad == ["shared.js"],
          "and the gate over the whole staged tree fails on exactly that file",
          ", ".join(bad) or "nothing failed")


def t_dangling():
    step(8, "a stripped tab that leaves a lookup behind")
    world(dangling=True)
    m = build.read_manifest()
    stage_i, _ = build.stage_web("dang-int", "internal", m)
    ok, fatal, _ = jsgate.dangling_ids(stage_i, out=open(os.devnull, "w"))
    check(ok is True, "internal keeps the markup, so the lookup resolves",
          "%d fatal" % len(fatal))

    stage_r, _ = build.stage_web("dang-rel", "release", m)
    ok, fatal, _ = jsgate.dangling_ids(stage_r, out=open(os.devnull, "w"))
    check(ok is False and any("btnPlay" in str(f) for f in fatal),
          "release strips #btnPlay and the module-scope lookup is caught",
          "; ".join(str(f) for f in fatal)[:90] or "nothing caught")


def t_leak_gate():
    step(9, "the leak gate")
    root, _, pack, _ = world()
    app = os.path.join(pack, "leakapp")
    os.makedirs(os.path.join(app, "_internal", "deep"))
    open(os.path.join(app, "app.exe"), "w").write("x")
    open(os.path.join(app, "README.txt"), "w").write("public readme")
    check(build.leak_gate(app) == [], "a clean bundle is clean")

    # The bug this gate exists for: ROADMAP.md was not at the root, it was
    # inside _internal/, and the root was already correct.
    open(os.path.join(app, "_internal", "ROADMAP.md"), "w").write("x")
    hits = build.leak_gate(app)
    check([h[0] for h in hits] == ["_internal/ROADMAP.md"],
          "a private doc is caught wherever it is nested", str(hits[:1]))
    check("roadmap" in hits[0][1].lower(), "and says why", hits[0][1])

    open(os.path.join(app, "_internal", "deep", "01_Vanilla_notes.txt"),
         "w").write("x")
    hits = dict(build.leak_gate(app))
    check("_internal/deep/01_Vanilla_notes.txt" in hits,
          "a path that references the dev tree is caught",
          hits.get("_internal/deep/01_Vanilla_notes.txt", ""))
    check(not any(h.endswith("README.txt") for h in hits),
          "README.txt is not README.md -- the shipped one stays")


def t_machine_gate():
    step(10, "the machine-path gate")
    world(config=DIRTY_CONFIG)
    # 2.0.0: the Full (internal) edition is this fork's public download, so it
    # is held to the same rule.
    check(build.machine_gate("internal") is False,
          "the Full (internal) build refuses them too")
    check(build.machine_gate("release") is False,
          "a drive-letter RDR-Modding literal fails the release build")

    world(config=HOME_CONFIG)
    check(build.machine_gate("release") is False,
          "so does a path through the developer's home folder")

    world(config=COMMENTED_CONFIG)
    check(build.machine_gate("release") is True,
          "the same literal in a comment discloses only prose")

    world(config=CLEAN_CONFIG)
    check(build.machine_gate("release") is True, "a clean module passes")


def t_ship_manifest():
    step(11, "the features.json copy the frozen app actually reads")
    _, _, pack, _ = world()
    app = os.path.join(pack, "shipapp")
    os.makedirs(app)
    check(build.ship_manifest(app, "release") == 0,
          "with no _internal/ there is nowhere to put it, and it says so")

    os.makedirs(os.path.join(app, "_internal"))
    check(build.ship_manifest(app, "release") == 1, "onedir gets a copy")
    doc = json.loads(read(os.path.join(app, "_internal", "features.json")))
    ids = sorted(f["id"] for f in doc["features"])
    check(doc.get("edition") == "release",
          "the copy records which edition this is", str(doc.get("edition")))
    check(ids == ["no_build_key", "swap_character"],
          "release carries only the features it ships", ", ".join(ids))
    check(not any("internal" in json.dumps(f) for f in doc["features"]),
          "and none of the notes about work being held back")

    build.ship_manifest(app, "internal")
    doc = json.loads(read(os.path.join(app, "_internal", "features.json")))
    check(doc.get("edition") == "internal"
          and len(doc["features"]) == len(BASE_FEATURES),
          "the internal build carries all of them and knows it is internal",
          "%d features" % len(doc["features"]))


def t_stamp():
    step(12, "EDITION.txt and EDITION.json say what is true")
    _, _, pack, _ = world()
    app = os.path.join(pack, "stampapp")
    os.makedirs(app)

    m = build.read_manifest()
    _, rep = build.stage_web("stamp", "release", m)
    rep["unstripped_internal"] = ["ghost_feature"]
    info = build.stamp(app, "release", "release", "a note", rep)
    txt = read(os.path.join(app, "_internal", "EDITION.txt"))
    disk = json.loads(read(os.path.join(app, "_internal", "EDITION.json")))
    check(not os.path.exists(os.path.join(app, "EDITION.txt"))
          and not os.path.exists(os.path.join(app, "EDITION.json")),
          "the stamp is inside _internal, not loose beside the exe")
    check(disk == info, "EDITION.json is the dict stamp() returned")
    check(disk["version"] == build.VERSION and disk["edition"] == "release",
          "version and edition are stamped", disk["version"])
    check(info["same_as_internal"] is False,
          "a release that stripped something is not the internal build")
    check("Removed from this build" in txt
          and "menu_edit" in txt and "behaviour_tables" in txt,
          "the removal list names exactly what left")
    check("ghost_feature" not in txt.split("Removed from this build")[1]
          .split("Switched off")[0],
          "a feature that was NOT removed is not in the removal list")
    check("Switched off in this build, but the code is still here" in txt
          and "ghost_feature" in txt,
          "and the still-present ones are disclosed separately")
    check("menus.js" in txt and "states.js" in txt,
          "the dropped front-end files are named")

    # Nothing stripped: the release IS the internal build, and has to say so.
    world(manifest=False)
    app2 = os.path.join(build.HERE, "stampapp2")
    os.makedirs(app2)
    _, rep2 = build.stage_web("same", "release", None)
    info2 = build.stamp(app2, "release", "release", "", rep2)
    txt2 = read(os.path.join(app2, "_internal", "EDITION.txt"))
    check(info2["same_as_internal"] is True,
          "nothing stripped means same_as_internal")
    check("IDENTICAL to the internal build" in txt2,
          "and the file says so out loud")
    check("Removed from this build" not in txt2,
          "with no removal list to contradict it")


def t_zip():
    step(13, "the zip has exactly one top folder")
    _, _, pack, _ = world()
    app = os.path.join(pack, "zipapp")
    os.makedirs(os.path.join(app, "_internal", "deep"))
    for rel in ("app.exe", "EDITION.txt", "_internal/base.pyz",
                "_internal/deep/three.js"):
        p = os.path.join(app, *rel.split("/"))
        open(p, "w").write("payload " * 20)

    out, n, size = build.zip_up(app, "test.zip")
    import zipfile
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
    tops = {nm.split("/")[0] for nm in names}
    check(n == 4 and len(names) == 4, "every file is in the archive", str(n))
    check(tops == {build.APPNAME},
          "every entry sits under one top folder, not at the archive root",
          ", ".join(sorted(tops)))
    check(build.APPNAME + "/_internal/deep/three.js" in names,
          "nesting is preserved")
    check(size == os.path.getsize(out) and size > 0,
          "the reported size is the file's size", "%d B" % size)

    check(round(build.mb(1048576), 3) == 1.0, "mb() converts bytes to MiB")
    each = len("payload " * 20)
    check(build.size_of(os.path.join(app, "app.exe")) == each,
          "size_of on a file is its size", "%d B" % each)
    check(build.size_of(app) == 4 * each,
          "size_of on a folder is the whole tree", str(build.size_of(app)))


def t_loader_gate():
    step(14, "FEATURE_MODULES against app.js's import list")
    _, _, _, web = world()
    app = os.path.join(web, "app.js")
    real = ORIG["FEATURE_MODULES"]          # the project's own, not the sandbox's
    TWO = dict(build.FEATURE_MODULES)

    def run(src, fm=None, strict=False):
        with open(app, "w", encoding="utf-8", newline="") as fh:
            fh.write(APP_HEAD + src)
        build.FEATURE_MODULES = dict(fm or TWO)
        rep = {}
        return build.loader_gate(rep, strict=strict), rep

    ok, rep = run(APP_MODLIST_OK)
    check(ok and not rep["loader_unwrapped"] and not rep["loader_unlisted"]
          and not rep["loader_ships_gated"],
          "a list where every dropped file's name is gated passes clean")

    ok, rep = run(APP_MODLIST_LOOSE)
    check(not ok, "a dropped file whose name is NOT gated fails the build")
    check(rep["loader_unwrapped"] ==
          ["states.js (@feature:behaviour_tables)"],
          "...and the report names the file and the id that should cut it",
          ", ".join(rep["loader_unwrapped"]))

    # The other direction. Nothing breaks at runtime, so it is a warning:
    # the panel is off and its implementation is in the public zip.
    ok, rep = run(APP_MODLIST_OK, fm={"menu_edit": ["menus.js"]})
    check(ok and rep["loader_ships_gated"] ==
          ["states.js (@feature:behaviour_tables)"],
          "a gated name whose file still ships is a warning, not a failure",
          ", ".join(rep["loader_ships_gated"]))
    ok, _ = run(APP_MODLIST_OK, fm={"menu_edit": ["menus.js"]}, strict=True)
    check(not ok, "...and --strict will not ship it")

    STRAY = {"menu_edit": ["menus.js", "stub.js"],
             "behaviour_tables": ["states.js"]}
    ok, rep = run(APP_MODLIST_OK, fm=STRAY)
    check(ok and rep["loader_unlisted"] == ["stub.js (@feature:menu_edit)"],
          "a dropped file no import list names is a warning",
          ", ".join(rep["loader_unlisted"]))
    ok, _ = run(APP_MODLIST_OK, fm=STRAY, strict=True)
    check(not ok, "...and --strict will not ship an unchecked pairing")

    # Both shapes the front end has used, so a rewrite of the import list
    # does not quietly turn the gate off.
    ok, rep = run(APP_LOADER_OK)
    check(ok and not rep["loader_unwrapped"] and not rep["loader_unlisted"],
          "literal './name.js' entries are understood too")
    half = APP_LOADER_OK.replace(
        "/* @feature:behaviour_tables */ './states.js', "
        "/* @end:behaviour_tables */", "'./states.js',")
    ok, rep = run(half)
    check(not ok and rep["loader_unwrapped"] ==
          ["states.js (@feature:behaviour_tables)"],
          "...and one ungated entry among them fails there as well",
          ", ".join(rep["loader_unwrapped"]))

    # APP_LOADER_LOOSE has no sentinel left anywhere, so nothing marks
    # that list as the gated one and this gate cannot speak to it. That
    # case belongs to snapshot(), which sees literal './name.js' paths --
    # step 6 holds it. Saying "unchecked" is the whole point of the
    # branch below; a clean pass here would be a lie.
    ok, rep = run(APP_LOADER_LOOSE)
    check(rep.get("loader") == "not found",
          "...and a list with every sentinel gone is declared unchecked",
          str(rep.get("loader")))

    # A list with no sentinels at all is the dangerous case: silence here
    # reads exactly like a clean pass, which is the failure this whole
    # file is about. It has to be said out loud, and --strict refuse it.
    ok, rep = run(APP_MODLIST_BLIND)
    check(ok and rep.get("loader") == "not found",
          "an import list with nothing gated in it is reported, not "
          "passed over", str(rep.get("loader")))
    ok, _ = run(APP_MODLIST_BLIND, strict=True)
    check(not ok, "...and --strict refuses to ship it unverified")

    # The only check in this file that reads the real web/ -- read-only.
    # The pairing this gate exists for lives in the project's own app.js,
    # not in a fixture, and it is worth proving on every run.
    build.WEB = ORIG["WEB"]
    build.FEATURE_MODULES = real
    rep = {}
    check(build.loader_gate(rep),
          "the project's own app.js and FEATURE_MODULES agree",
          "%d module(s) gated" % sum(len(v) for v in real.values()))


def main():
    keep = "--keep" in sys.argv
    try:
        for fn in (t_strip, t_source_untouched, t_intent_vs_fact,
                   t_manifest_reading, t_manifest_gate, t_snapshot,
                   t_syntax_gate, t_dangling, t_leak_gate, t_machine_gate,
                   t_ship_manifest, t_stamp, t_zip, t_loader_gate):
            # One broken gate must not hide the other twelve.
            try:
                fn()
            except Exception as ex:                        # noqa: BLE001
                import traceback
                traceback.print_exc()
                FAILED.append("%s raised %s" % (fn.__name__,
                                                type(ex).__name__))
    finally:
        for k, v in ORIG.items():
            setattr(build, k, v)
        if keep:
            print("\nsandboxes kept:\n  %s" % "\n  ".join(SANDBOXES))
        else:
            for d in SANDBOXES:
                shutil.rmtree(d, ignore_errors=True)

    if FAILED:
        print("\n%d FAILED: %s" % (len(FAILED), "; ".join(FAILED)))
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
