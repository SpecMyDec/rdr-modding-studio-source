"""Which capabilities exist in THIS build.

Two builds come out of one codebase. The mechanism is deliberately dull: one
manifest file plus one environment variable, and a route that is not
registered rather than a button that is disabled.

    RDR_BUILD=release    (default) -- only features marked build "both"
    RDR_BUILD=internal              -- everything

A feature switched off here is genuinely absent from the server: the route is
never added to the dispatch table, so calling it returns the same "no such
route" 404 as a typo would. That is honest, and it is cheaper than any
permission system: there is no code path that checks a flag and then does the
work anyway.

The default for anything at rung `untested`, `inferred` or `dead` is
`internal`. That default is *surfaced as a suggestion*, never applied
silently -- `features.json` is the authority and a human edits it.
"""

import json
import os
import sys

MANIFEST = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "features.json")

# The fallback, used when the manifest is missing or unreadable. It must name
# every feature the front end asks about, because a feature absent from this
# dict reads as "off" and silently removes a panel.
#
# The frozen build USED to ship with no features.json at all -- it is still
# not in rdrstudio_app.spec's `datas`, and furnish() copies docs and licences,
# not the manifest -- so every packaged app fell back to this list, and a
# feature missing from it was a panel that worked from source and was
# silently absent in the download. ship_manifest() (packaging/build.py) closed
# that: after the freeze it writes the edition's own manifest into
# `_internal/`, which is where MANIFEST resolves, filtered to what that
# edition ships, and both dist editions carry one. So this is the fallback for
# a manifest that is missing or unreadable, not the normal path for a
# download -- a smaller blast radius, not a reason to let it drift.
#
# The gating fields only: id, label, workspace, rung, build. `why` is left
# out on purpose. ship_manifest() strips it from the release manifest because
# each one is a candid note about what is unproven, written for us -- but this
# file is compiled into BOTH editions, so a `why` here would ship to the
# public whatever that filter does. The switches are what this list is for.
#
# GENERATED from features.json, which is the authority and the file a human
# edits:
#
#     python scripts/verify_feature_defaults.py --write
#
# That script is also the check, and run_tests.py fails on drift. Editing the
# entries below by hand is how this list came to disagree with the manifest in
# three ways at once -- eleven features missing, scripting_browser switched
# the wrong way, place_enemy still untested after it was measured.
DEFAULTS = [
    dict(id='actb_edit', label='Edit mission script parameters',
         workspace='LOGIC', rung='untested', build='internal'),
    dict(id='attr_edit', label='Edit typed object attributes',
         workspace='LEVEL', rung='measured', build='both'),
    dict(id='behaviour_clone', label='Give an enemy its own behaviour table',
         workspace='LOGIC', rung='untested', build='internal'),
    dict(id='behaviour_numbers', label='Retune numbers inside a state file',
         workspace='LOGIC', rung='untested', build='internal'),
    dict(id='behaviour_tables', label='Change which states a behaviour table binds',
         workspace='LOGIC', rung='untested', build='internal'),
    dict(id='char_files', label="Edit a character's body files",
         workspace='BALANCE', rung='untested', build='internal'),
    dict(id='csv_editor', label="Edit the disc's tables",
         workspace='BALANCE', rung='untested', build='internal'),
    dict(id='emulator', label='Play and Boot test buttons',
         workspace='PROJECT', rung='measured', build='both'),
    dict(id='enemy_fields', label='Edit all 21 enemy fields',
         workspace='LEVEL', rung='measured', build='both'),
    dict(id='file_add', label='Add a new file to an archive',
         workspace='DISC', rung='untested', build='internal'),
    dict(id='file_drop', label='Drag files into an archive',
         workspace='DISC', rung='measured', build='both'),
    dict(id='grow_files', label='Let an edit grow its file',
         workspace='LEVEL', rung='measured', build='both'),
    dict(id='lan', label='Serve on the local network',
         workspace='PROJECT', rung='measured', build='internal'),
    dict(id='level_export', label='Export a whole level as a model',
         workspace='LEVEL', rung='measured', build='both'),
    dict(id='linked_move', label='Move a building and its collision as one thing',
         workspace='LEVEL', rung='measured', build='both'),
    dict(id='links_edit', label='Edit area doors',
         workspace='LOGIC', rung='untested', build='internal'),
    dict(id='menu_edit', label='Edit menus and HUD screens',
         workspace='MEDIA', rung='untested', build='internal'),
    dict(id='new_weapon', label='Add a new weapon, cloned from another',
         workspace='BALANCE', rung='measured', build='both'),
    dict(id='place_enemy', label='Place a new enemy anywhere',
         workspace='LEVEL', rung='measured', build='both'),
    dict(id='repack', label='Repack an archive for more room',
         workspace='DISC', rung='untested', build='internal'),
    dict(id='rotate', label='Rotate a box, zone, bound or line',
         workspace='LEVEL', rung='measured', build='both'),
    dict(id='scripting_browser', label='Browse mission scripting',
         workspace='LOGIC', rung='inferred', build='internal'),
    dict(id='strings_editor', label="Rewrite the game's text",
         workspace='BALANCE', rung='measured', build='internal'),
    dict(id='surface_move', label='Move a baked world surface',
         workspace='LEVEL', rung='measured', build='internal'),
    dict(id='swap_behaviour', label='Swap what an enemy does',
         workspace='LEVEL', rung='measured', build='both'),
    dict(id='swap_character', label='Swap which character an enemy is',
         workspace='LEVEL', rung='measured', build='both'),
    dict(id='swap_model', label="Give a character another character's model",
         workspace='BALANCE', rung='untested', build='both'),
    dict(id='transfer', label='One import/export shape',
         workspace='ALL', rung='measured', build='both'),
    dict(id='versions', label='Named versions of the working disc',
         workspace='PROJECT', rung='measured', build='both'),
    dict(id='vertex_edit', label='Reshape a spline',
         workspace='LEVEL', rung='measured', build='both'),
]

RISKY_RUNGS = ("untested", "inferred", "dead")


def build():
    """Which edition is running.

    RDR_BUILD wins, because a developer running from source says so
    explicitly. A FROZEN build has no such environment: nothing set the
    variable at freeze time and nothing sets it at launch, so both editions
    answered "release" -- the internal build was gating itself as a release
    build, and every routes2 feature marked internal was 404 in the build
    that exists to have them.

    So the shipped manifest names its own edition. It is written per edition
    by build.py, sits beside the package, and is the same file that says which
    features exist -- one artifact, one answer, nothing to keep in step.
    """
    v = (os.environ.get("RDR_BUILD") or "").strip().lower()
    if v:
        return "internal" if v == "internal" else "release"
    try:
        with open(MANIFEST) as f:
            e = (json.load(f).get("edition") or "").strip().lower()
        if e:
            return "internal" if e == "internal" else "release"
    except Exception:
        pass
    # Running from SOURCE, with no shipped manifest to ask. That is a
    # developer working on the whole codebase, so everything is on -- the same
    # reasoning the PyInstaller spec already applies to a hand-run build.
    # Defaulting to "release" here silently switched off the internal features
    # in the source tree, which took out the test suite's strings and links
    # coverage the moment route gating arrived.
    if not getattr(sys, "frozen", False):
        return "internal"
    return "release"


def manifest(path=None):
    """The feature list. Falls back to DEFAULTS rather than failing to start.

    A missing manifest must not brick the app; it means "use what the code
    knows about", which is exactly the fallback list above.
    """
    path = path or MANIFEST
    try:
        with open(path) as f:
            doc = json.load(f)
        items = doc.get("features") or []
        if items:
            return items
    except Exception:
        pass
    return DEFAULTS


def table(path=None, which=None):
    """{feature id: enabled} for this build."""
    which = which or build()
    out = {}
    for f in manifest(path):
        want = (f.get("build") or "both").lower()
        out[f["id"]] = True if which == "internal" else (want == "both")
    return out


def suggestions(path=None):
    """Features whose rung argues for internal-only but are marked `both`.

    Reported, never applied. The manifest is the authority; this is the line
    that says "you probably meant" and stops there.
    """
    out = []
    for f in manifest(path):
        if (f.get("rung") in RISKY_RUNGS
                and (f.get("build") or "both").lower() == "both"):
            out.append(dict(id=f["id"], rung=f["rung"],
                            why=f.get("why", ""),
                            suggest="internal"))
    return out


def enabled(fid, path=None):
    return bool(table(path).get(fid, False))


def describe(path=None):
    which = build()
    t = table(path, which)
    return dict(build=which, features=t, manifest=manifest(path),
                suggestions=suggestions(path))
