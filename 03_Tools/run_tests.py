#!/usr/bin/env python3
"""Run every check in one go.

    python run_tests.py            all of them
    python run_tests.py --quick    skip the two full-disc sweeps (~40s each)

Offline checks run standalone. Live checks need the Studio on port 8731; it is
started automatically if nothing is listening, and stopped again afterwards.
Live tests leave the working ISO exactly as they found it.

A Studio that is ALREADY listening is only reused if it is running the source
that is on disc right now -- see `studio_freshness` below. An older one is
refused, because it answers with the code it read when it started.

    --restart-studio   stop a stale Studio and start a fresh one
    --reuse-studio     drive it anyway (prints what it is overriding)
"""

import argparse
import collections
import os
import re
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
def _port():
    """The Studio's port, and it must agree with rdrstudio.config.PORT.

    Duplicated as an EXPRESSION rather than imported, because this file
    deliberately does not import the package it tests at module scope -- a
    syntax error in rdrstudio should fail a ROW, not stop the suite from
    starting. So the two definitions have to be read together: config.py's
    comment names this file and this one names config.py.

    Until 2026-09-18 this was the literal 8731 while config.PORT honoured
    RDR_PORT, and the failure was not "the suite uses the wrong port" -- it
    was worse. The Studio would bind the configured port, the suite would find
    nothing on 8731, start a SECOND Studio there, and report PASS or FAIL
    about an instance nobody meant to test while the configured one sat idle.
    Same wrong-instance family as the wrong-disc Studio closed in 1c87eed,
    arriving through the port instead of the ISO. Found by rdr-3d, who landed
    the config half and left this one alone rather than reach across.

    A malformed RDR_PORT REFUSES rather than falling back to 8731. Falling
    back would be a default that silently means "not what you asked for", and
    the whole point of the variable is isolating a run onto a port of its own
    -- quietly putting it back on the shared one is the outcome it exists to
    prevent.
    """
    raw = os.environ.get("RDR_PORT") or "8731"
    try:
        p = int(raw)
        if not 1 <= p <= 65535:
            raise ValueError("outside 1-65535")
    except ValueError as ex:
        print("  RDR_PORT=%r is not a usable port: %s" % (raw, ex))
        print("Nothing has been run.")
        sys.exit(2)
    return p


PORT = _port()
BASE = "http://127.0.0.1:%d/api/" % PORT

OFFLINE = [
    ("disc integrity  (all 66 archives)", ["scripts/verify_disc.py"], False),
    ("collision geometry (35 levels)", ["scripts/verify_geometry.py"], False),
    ("textures + render geometry", ["scripts/verify_textures.py"], False),
    ("level areas (97, full render coverage)", ["scripts/verify_areas.py"], False),
    ("texture formats (RSC + loose .tex)", ["scripts/verify_tex_formats.py"], False),
    ("model->texture binding (15,730 refs)", ["scripts/verify_binding.py"], False),
    # REQ-028. Registered by rdr-3d on the reviewer's word that run_tests.py is
    # unowned now -- note STATE-CONTRACT.md line 12 still says
    # otherwise and wants updating.
    #
    # Sits here deliberately: it extends the binding law the line above
    # proves for levels to CHARACTERS, which `verify_binding.py` never
    # covered (it sweeps LVLS_* only). `True` because it is genuinely
    # read-only -- opens 01_Vanilla, writes nothing, needs no Studio and
    # takes no lock -- and ~8s.
    ("[M8] a character opens from its .rsc, textures and all",
     ["scripts/verify_charview.py"], True),
    ("scene resourceIndex is 1-based", ["scripts/verify_resindex.py"], False),
    ("part records name a material block", ["scripts/verify_material.py"], False),
    ("string tables (203, hash-keyed)", ["scripts/verify_strtbl.py"], False),
    # Quick-safe: applies and re-parses IN MEMORY off 01_Vanilla -- no Studio,
    # no ISO write, no lock. verify_csv.py checks the reader/writer across all
    # 1,022 files; this follows ONE edit out of the file and into the name the
    # game would show, which is the half M12 asks for.
    ("[M12] a csv cell edit reaches the name the game shows",
     ["scripts/verify_csv_edit.py"], True),
    # Quick-safe: applied and re-parsed in memory off 01_Vanilla. verify_swui
    # proves IDENTITY across every UI file; this is the EDIT half M14 asks
    # for, on pause.swui because its 26 spare bytes make "does it still fit"
    # a real question.
    ("[M14] a HUD element edits and round-trips byte-exactly",
     ["scripts/verify_swui_edit.py"], True),
    # REQ-048, rdr-fmt's. The corpus half of the same milestone: every UI file
    # on the disc, read and rewritten byte-exact, plus which of the three
    # dialects each one actually is. It predates both current [M14] tests and
    # had never been registered. Quick-safe: reads the disc, writes nothing,
    # ~4s. Deliberately NOT labelled [M14] -- one milestone, one labelled row.
    ("menu / HUD files rewrite byte-exact (191 screens)",
     ["scripts/verify_swui.py"], True),
    ("TEX0 names its own pixels+palette", ["scripts/verify_texlink.py"], False),
    ("text formats: 4.7k files byte-exact", ["scripts/verify_props.py"], False),
    ("character body files (939, 426k values)",
     ["scripts/verify_charfile.py"], False),
    # Quick-safe (REQ-008/REQ-011): opens 01_Vanilla read-only, does all nine
    # edits on a temp copy of one 1.6 MB archive, writes no ISO. ~6s.
    ("[M7] character stats: nine formats round-trip byte-exactly",
     ["scripts/verify_charstats.py"], True),
    ("geometry is editable in place", ["scripts/verify_geomedit.py"], False),
    # REQ-015, registered as rdr-3d filed it. `False` on purpose and not by
    # oversight this time: sections 4-5 WRITE the working ISO and restore it,
    # so this is not a read-only check and must not run in the loop lanes fire
    # constantly. `--offline` runs the read-only sections and prints
    # "PARTIAL -- sections 4-5 not run" rather than PASS, so a partial result
    # can never be misread as a full one.
    ("[M6] a selection moves and scales about one pivot",
     ["scripts/verify_massedit.py"], False),
    ("skinned verts re-encode exactly", ["scripts/verify_skinwrite.py"], False),
    ("concurrent disc reads stay correct", ["scripts/test_read_race.py"], False),
    ("level events + module layout", ["scripts/verify_levelmeta.py"], False),
    ("executable unpacks (aPLib + XOR)", ["scripts/verify_elfpack.py"], False),
    ("collision flag order from node names", ["scripts/verify_boundflags.py"], False),
    # REQ-025, registered as rdr-3d filed it. `False` on purpose, like [M6]:
    # section 4 WRITES the working ISO and restores it, so this is not a
    # read-only check. `--offline` runs everything else and prints
    # "PARTIAL -- section 4 not run" rather than PASS.
    ("[M16] a collision shell moves and still collides there",
     ["scripts/verify_boundmove.py"], False),
    # M17, rdr-app's own. `False` on purpose, the same reason as [M6] and
    # [M16]: section 4 WRITES the working ISO and restores it in a `finally`,
    # so this is not a read-only check. `--offline` runs sections 1-3 and
    # prints "PARTIAL -- section 4 not run" rather than PASS.
    #
    # Consequence worth stating where it will be read: this row does NOT run
    # under `--quick`, so a quick green says nothing about M17. Same for [M6],
    # [M9] and [M16]. Only a full run is evidence about any of them.
    #
    # THIS PARAGRAPH IS LOAD-BEARING. Do not tidy it away. REQ-067 asked for
    # the suite to report a milestone as unproven when a `--quick` run skips
    # rows its criterion names; the reviewer closed it 2026-09-18 as an ACCEPTED RISK
    # -- the two-full-runs cost is intended and run_tests.py is not changing.
    # So nothing in the code detects this any more. These lines and the Accept
    # line in ROADMAP-TESTER.md are the entire remaining warning, in the same
    # way `test.sh`'s `grep -aq SUMMARY` is the only thing between a killed
    # run and a lane reporting it green. A comment is a poor guard; it is the
    # one that was left.
    ("[M17] FRONT.DAT opens in the level editor and an edit round-trips",
     ["scripts/test_front_level.py"], False),
    # M18, written by rdr-3d, registered here by rdr-app at their request --
    # the normal split, not the self-register exception.
    #
    # `True` where [M6], [M16] and [M17] are `False`, and the difference is
    # real rather than a guess: those three write the working ISO and restore
    # it, this one opens 01_Vanilla read-only and writes nothing anywhere --
    # no working-ISO patch, no temp file, no restore path to get wrong.
    # Checked before registering rather than taken on trust: 21 checks in
    # 0.30s, and it does the copy in memory.
    #
    # M18's criterion has a second half -- "the existing verify_binding /
    # verify_resindex checks must stay green" -- and those are the two rows
    # at lines 39 and 51. They are `False`, so a --quick run covers this row
    # but not the other half of the milestone.
    ("[M18] a new model lands in a character .rsc and the bindings still verify",
     ["scripts/test_m18_add_model.py"], True),
    # Level custom models: the display-list WRITER (OBJ, any vertex count).
    # Quick-safe like [M18]: reads 01_Vanilla, writes nothing, no Studio, ~15s.
    # Unlabelled -- tooling for a tester ask, not a roadmap milestone. Proves
    # byte-identical regeneration of shipped packets and a verified import;
    # it does NOT prove the console draws the result (see CAPABILITIES C-012).
    ("level custom models: display-list writer + OBJ import (any vertex count)",
     ["scripts/test_levelvif.py"], True),
    ("model + collision are one thing: vanilla move carries collision, solid toggle",
     ["scripts/test_model_collision.py"], True),
    ("custom model gets matching collision: generated box, new bound_ chunk, name link",
     ["scripts/test_custom_collision.py"], True),
    # Code Mods (community-research noclip/freeze/camera-lock hooks). Quick-safe: reads 01_Vanilla, writes nothing.
    ("code mods: addresses verified vs our unpack, installer runs in an R5900 interpreter, ELF = the community research image",
     ["scripts/test_codemods.py"], True),
    ("research code mods: function-hit logger counts calls + records args/caller/return value, memory watch follows pointer chains safely (R5900 interpreter)",
     ["scripts/test_codemods_research.py"], True),
    ("research tools (rdr_hitlog / rdr_watch readers) against a fake PINE server, read-only",
     ["scripts/test_rdr_research_tools.py"], True),
    ("mapping helpers: rdr_snap (snapshot/diff RAM, follows pointers) + rdr_ssdiff (savestate diff) + research rows off by default in the Code Mods tab",
     ["scripts/test_rdr_research_tools2.py"], True),
    ("live player position tool (PINE): position + heading read from the player matrix, read-only, no player -> no crash",
     ["scripts/test_rdr_pos.py"], True),
    ("code mods inside a mod pack: format 2 + version, no game bytes, older Studio / wrong game / already-modded refused",
     ["scripts/test_codemods_pack.py"], True),
    ("code mods: infinite ammo, god mode, one-hit kills (hooks exact, cave run in the R5900 interpreter for every subset)",
     ["scripts/test_codemods_combat.py"], True),
    ("code mods: Fast Red (the three Mover::Update calls go through one cave; the player mover sees frame time x1.5, others unchanged, run in the R5900 interpreter)",
     ["scripts/test_codemods_speed.py"], True),
    ("code mods: Unlock Everything (the one unlock test answers 1 for every Showdown character/stage, journal page and cheat; unimplemented cheats + out-of-range stay original; R5900 interpreter on the real function)",
     ["scripts/test_codemods_unlock.py"], True),
    ("code mods: Double Damage + Hard Mode share the God Mode cave (every subset of the 4 damage keys run in the R5900 interpreter) + every 11/13 mod built with the older ones",
     ["scripts/test_codemods_damage.py"], True),
    ("code mods: Money x5 (player give-money virtual: positive amounts x5, spending untouched; cave + real vanilla prologue in the R5900 interpreter)",
     ["scripts/test_codemods_money.py"], True),
    # Both quick-safe: read 01_Vanilla, write nothing, no Studio, no lock.
    ("character display lists tile 100% (the old failures were a trailer)",
     ["scripts/test_vifwalk_chars.py"], True),
    ("shared weapon models can be imported over, knowingly",
     ["scripts/test_weapon_shared_ack.py"], True),
    ("skinned character batches: header words decoded, encoder byte-identical",
     ["scripts/test_skinvif.py"], True),
    ("custom character mesh: OBJ -> rigged MOD0, re-read by the other reader",
     ["scripts/test_skinwrite.py"], True),
    # REQ-030, registered by rdr-3d under the standing self-register
    # exception the reviewer added to STATE-CONTRACT.md. `False` on purpose,
    # like [M6] and [M16]: sections 2-5 WRITE the working ISO and restore it,
    # so this is not a read-only check. `--offline` runs sections 1 and 6 and
    # prints "PARTIAL -- sections 2-5 not run" rather than PASS.
    ("[M9] a model swap changes one level and not the others",
     ["scripts/verify_castswap.py"], False),
    ("animation curves (cm2, 3,771 files)", ["scripts/verify_anim.py"], False),
    ("animation channels map to bones", ["scripts/probe_anim_bones.py"], False),
    ("BVH export round-trips exactly", ["scripts/verify_bvh.py"], False),
    ("uncompressed .anim codec (181 files)", ["scripts/verify_anim_raw.py"], False),
    ("cmp .anim container (234 files)", ["scripts/verify_anim_cmp.py"], False),
    ("cmp .anim decodes (234 files)", ["scripts/verify_cmp.py"], False),
    ("animation import round-trips byte-exact", ["scripts/verify_anim_import.py"], False),
    ("[M2] animation import survives a DCC round trip",
     ["scripts/verify_anim_dcc.py"], False),
    # Quick-safe: reads the vanilla disc only. It used to open the WORKING
    # ISO through a hardcoded D:\ path, which made it both unportable and
    # dependent on whatever the last live test left behind.
    ("[M5] hat bone: head, hat01, or not worn at all",
     ["scripts/verify_hatbone.py"], True),
    ("geometry shape reads back off the disc", ["scripts/verify_geomshape.py"], False),
    # Registered by rdr-3d under the self-register exception, on the reviewer's
    # instruction, with a PLAIN label on purpose. It is NOT `[M15]`: the
    # roadmap's acceptance for M15 is an IMPORT that replaces a weapon and
    # re-reads it, and this reads only. Labelling it `[M15]` would land the
    # milestone without the thing the tester asked for ever being demonstrated.
    # `True` -- reads 01_Vanilla, writes nothing, no Studio, no lock, ~25s.
    ("text .mod dialect reads (251 files, 3,786 packets)",
     ["scripts/verify_modtext.py"], True),
    # rdr-app's, added by rdr-fmt on the reviewer's instruction 2026-09-18 -- it was
    # on disc and passing but registered in NO list, so it ran in no mode at
    # all, which is the hole [M7] sat in. Deliberately UNLABELLED: it is
    # tooling, not a milestone. Quick-safe -- binds a throwaway port, never
    # touches 8731 or either ISO, ~3s.
    ("run_tests refuses a stale Studio",
     ["scripts/test_studio_freshness.py"], True),
    # Same shape as the row above and UNLABELLED for the same reason: tooling,
    # not a milestone, and I do not hand myself [Mn] numbers. Quick-safe --
    # builds a Studio with __new__ against a temp file, binds no port, opens
    # no ISO, takes no lock, under a second.
    #
    # It asserts the refusal for an unwritable working ISO names the cause
    # that is TRUE. There are four and only one is the emulator; the route
    # layer used to answer all four with "Close PCSX2 and try again" and fold
    # the real sentence under a disclosure triangle. Note what stayed green
    # through all of that: "it refuses". That is why nothing caught it.
    ("the writer lock: stale ones clear, live ones refuse with the right reason",
     ["scripts/test_disc_locked.py"], True),
    # rdr-polish, 2026-09-29. UNLABELLED: tooling for the Export dialog, not
    # a milestone. Quick-safe -- builds its own small files in a temp dir,
    # never opens either ISO, no Studio, no port, no lock, ~3s. Prints which
    # judge ran: the RFC 3284 decoder always, stock xdelta3 when reachable.
    ("export: patches decode to the working disc, routes refuse overwrites",
     ["scripts/test_share_export.py"], True),
    # M19, rdr-fmt's. OFFLINE because neither half needs a Studio -- the
    # decoder is pure and the replacement is a loose-ISO write, so there is
    # no route in between. `False` (not quick-safe) because it WRITES the
    # working ISO and restores it, the same reason [M6]/[M16]/[M17] are
    # false: a quick green must not imply this ran.
    ("[M19] a .pss frame decodes, and a cutscene is replaced and re-reads",
     ["scripts/test_pss_replace.py"], False),
    ("texture import round-trip", ["scripts/test_texture_import.py"], True),
    ("behaviour tables (21k files)", ["scripts/verify_behaviour.py"], False),
    ("enemy edit + placement (61 archives)",
     ["scripts/verify_enemy_edit.py"], False),
    # Quick-safe: splices and re-parses IN MEMORY off 01_Vanilla, so no Studio,
    # no ISO write and no lock. verify_enemy_edit above already proves the N+1
    # half; this is the "and a unique name" half, which nothing asserted.
    ("[M11] an enemy is added by chunk copy, and its name is unique",
     ["scripts/verify_enemy_add.py"], True),
    ("sound banks decode (155 .bd, SPU-ADPCM)", ["scripts/verify_vag.py"], False),
    ("MPEG-2 tables + intra decoder", ["scripts/verify_mpeg2.py"], True),
    # Quick-safe (REQ-011): reads repo text only, writes nothing, ~4s. It was
    # flagged full-suite-only when it was registered, which meant the default
    # `test.sh` -- the run that actually happens -- skipped the milestone's
    # own acceptance test and still reported a clean number.
    ("[M3] capability claims settled; no retired text ships",
     ["scripts/verify_capabilities.py"], True),
    # M21, rdr-fmt's. OFFLINE because the whole create path is library work
    # -- `dave.create()` then `Iso9660.add()` -- with no route in between and
    # no Studio to drive. `False` (not quick-safe) for the same reason as
    # [M6]/[M16]/[M17]/[M19]: sections 3-7 WRITE the working ISO. It snapshots
    # every byte region the add touches first, undoes the add with
    # `remove_last()`, and then compares the regions to the snapshot in a
    # `finally` -- restoring them verbatim and FAILING if `remove_last` left
    # anything behind, so a failure cannot hand the next lane a dirty disc.
    # ~70s, most of it the 66-archive re-open in section 6.
    ("[M21] a brand-new .dat is built, placed on the disc, and the disc verifies",
     ["scripts/verify_iso_add.py"], False),
    # verify.sh itself. Nothing tested the release gate, which is how it came
    # to exit 0 in twenty seconds having run zero tests from 07:12:49 on
    # 2026-09-18 -- and then be cited as half of a gate answering whether v1.3
    # could ship. It runs the REAL verify.sh with one line substituted (the
    # one that launches this suite) and refuses rather than running if that
    # substitution stops matching, because a silent non-match would either
    # test nothing or launch a 16-minute suite from inside a test.
    #
    # `True`: a temp dir and a stub shell script. No Studio, no disc, no lock,
    # no network. Under a second.
    ("the release gate cannot pass without running",
     ["scripts/test_verify_gate.py"], True),
    # features.py's DEFAULTS against features.json. A lockfile check: the
    # manifest is the file a human edits, DEFAULTS is the fallback that has to
    # say the same thing, and the script that fails on drift is the script
    # that regenerates it (--write). Registered because hand-maintaining the
    # list is how it came to disagree in three ways at once -- eleven features
    # missing, scripting_browser `both` against the manifest's `internal`, and
    # place_enemy still `untested` after the manifest measured it.
    #
    # `True`: reads two files, writes nothing without --write, needs no
    # Studio, no disc, no lock. Under a second.
    ("feature defaults match the manifest",
     ["scripts/verify_feature_defaults.py"], True),
    # packaging/test_build.py, added with the three fixes in 8ee2411 and
    # registered here on the reviewer's instruction. Deliberately UNLABELLED: it is
    # tooling, not a milestone.
    #
    # OFFLINE because none of it needs a Studio. It drives build.py's
    # ship/no-ship logic directly -- the strip, the manifest gate, the loader
    # gate, snapshot, the leak and machine-path gates, ship_manifest, the
    # EDITION stamp and the zip layout -- against a synthetic project tree in
    # a temp directory, with build.py's six module globals repointed at it.
    #
    # `True`, checked rather than assumed: it writes only inside
    # tempfile.mkdtemp() and removes it in a `finally`, never opens either
    # ISO, binds no port, takes no lock, and needs neither PyInstaller nor
    # packaging/.venv. 91 checks over 14 sections in 0.6s. It restores
    # build.py's globals on the way out, so it cannot leave the module
    # repointed for anything that imports it later in the same interpreter --
    # though nothing here does, since every row is its own process.
    #
    # WHAT A GREEN HERE DOES NOT SAY: that a build works. Nothing is frozen,
    # so the self-check, the window check, the leak gate's real subject and
    # the as-downloaded check are all untouched -- this row covers only the
    # decisions about what goes INTO the zip, not whether the .exe that comes
    # out of PyInstaller runs. That still has to be shown by running build.py
    # itself, and this row must not be read as standing in for it.
    ("build: what the release zip includes, strips and claims",
     ["packaging/test_build.py"], True),
    # packaging/test_shell.py, rdr-app's file, registered by rdr-re on the reviewer's
    # assignment. It is the whole non-GUI half of the desktop app -- per-user
    # folders, the three ISO verify tiers, the working copy, the config patch,
    # the in-process server, the real API and the real front-end files -- and
    # until now it was registered in NO list and ran in no mode at all. Same
    # hole as the stale-Studio row, and the bigger of the two: nothing else in
    # the suite covers the shell.
    #
    # It does NOT write either shared ISO and does not bind 8731; it reads
    # 01_Vanilla, writes only under packaging/testdata, and takes an
    # OS-assigned port -- all three of which it asserts rather than promises.
    # So the usual reason for `False` does not apply here.
    #
    # `False` for a different reason, stated so it is not "tidied" to True:
    # step 3 copies the 2.3 GB vanilla ISO when packaging/testdata has no
    # usable copy. Warm that is 2.4s; cold it is minutes, and --quick exists
    # to be fast. CONSEQUENCE, in the same spirit as the [M17] note above: a
    # --quick green says NOTHING about the shell. Promote this to True only
    # with a guarantee that testdata is always warm, which nothing currently
    # gives.
    #
    # Verified 21/21 exit 0, with and without %LOCALAPPDATA% set. NOT run
    # under Windows python from here -- no py.exe is reachable from WSL -- so
    # a Windows run is still the confirmation nobody has done.
    #
    # The count was 20 until REQ-072's check was tightened from two assertions
    # to three; an earlier version of this comment said 21/21 and 20/21 of the
    # 20-check file, which was wrong by one in both figures. Corrected here
    # rather than left, because a check count is the kind of number someone
    # later diffs against. When this file said "20/21 unset", the true reading
    # was 19/20, and the one failure was rdrshell/paths.py's
    # `expanduser(r"~\AppData\Local")` fallback -- fixed in f786e1e, so unset
    # now passes too.
    ("the desktop shell: folders, ISO verify, working copy, API",
     ["packaging/test_shell.py"], False),
    # M22, rdr-fmt's. `True` -- genuinely quick-safe, and that is a change of
    # shape from [M21] beside it rather than a looser judgement. It writes NO
    # shared state: `--self-contained` builds a SPARSE stand-in for the disc in
    # its own temp dir and deletes it, so there is no working-ISO write, no
    # Studio, no lock, and nothing for a killed run to leave behind. ~17s.
    #
    # The sparse image is why this row can exist at all. The probe it grew out
    # of truncates a 2.3 GB image; pointing that at the shared disc is unsafe
    # and copying the shared disc costs ~105s of every full run. Everything the
    # test reads is in the first ~1,025 sectors plus the backup anchor, and
    # file DATA is never read -- so the data area is a hole, and the image is
    # byte-identical to the real disc everywhere the test looks.
    ("[M22] the ISO grows, and a real-sized .dat lands past the old end",
     ["scripts/verify_iso_grow.py", "--self-contained"], True),
    # The M21 create path again, judged by a reader that shares no code with
    # it: an ISO9660 parser written from the PVD and directory records, a Dave
    # reader written from the TOC layout, and a whole-image sha256 instead of
    # the snapshot comparison verify_iso_add.py makes of itself. A bug common
    # to writer and reader passes [M21] and fails here, which is the only thing
    # this row adds and the whole reason it exists.
    #
    # Deliberately UNLABELLED: it is a cross-check, not an acceptance
    # criterion, and M21's Accept line is [M21]'s to answer.
    #
    # Quick-safe, and it did not used to be. It WRITES the image it is handed,
    # so it was unregisterable while that meant the working ISO. --self-contained
    # builds a sparse stand-in in its own temp dir instead (rdr-fmt's shape from
    # [M22]): real descriptors, directories, UDF and anchor in the first ~1,025
    # sectors, data area a hole, deleted on exit. No shared disc, no lock, ~16s,
    # and nothing for a killed run to leave behind.
    ("the M21 create path, judged by an independent reader",
     ["scripts/probe_m21_indep.py", "--self-contained"], True),
    # rdr-menu's, assigned by the reviewer as [M23] (REQ-080, 12b1170). Reads two
    # discs -- the vanilla master and the disc that ships -- and compares the
    # mod-list page across every copy of it. No shared-ISO write, no Studio,
    # no port, no lock, ~20s.
    #
    # `True`, and that is a DELIBERATE CHANGE from the `False` rdr-menu
    # proposed. It meets the quick-safe criterion [M22] states above: it writes
    # no shared state and leaves nothing behind for a killed run. `False` would
    # also have been worse than it looks -- test.sh runs --quick BY DEFAULT,
    # REQ-067 was closed ACCEPTED RISK so nothing reports a milestone as
    # unproven when --quick skips it, and this row exists because a half-built
    # disc reached the developer twice. A gate that does not run in the gate people
    # actually run is the same defect it was built to catch.
    #
    # Missing disc exits 2 with "Nothing has been run.", which run() reports as
    # FAIL. That is the honest verdict -- the row verified nothing -- and it is
    # why this does NOT auto-build the 2.3 GB disc when absent: a suite row
    # that quietly starts a multi-GB copy on D: is a D: write with no
    # all-clear, and that wedges whoever is playing.
    #
    # WHAT THE LABEL CLAIMS, AND WHAT IT DOES NOT (REQ-085, renamed 2026-09-19).
    # It read "carries its mod list" while the gate took its expected names off
    # the disc under test, so it compared copies of a page against a list read
    # from one of those copies: consistency wearing correctness's name, green
    # on a page nobody chose (CHECKS-THAT-LIE 21, caught in this file's own
    # review). The gate now reads 02_Modded/modlist-names.txt, written by the
    # builder, so it compares DISC AGAINST RECORD. The label had to move with
    # it. This row catches a hand edit, a second tool writing that page and a
    # copy left half-rewritten; it does NOT catch a build run with the wrong
    # names -- disc and record are then wrong together and this row is green.
    # That limit belongs in the label because the reviewer and the developer read this line
    # and nobody reads verify_modlist_row.py:173.
    #
    # The disc is RESOLVED, not hard-coded -- an earlier version of this
    # comment warned that the filename had churned once (RDR_MENUTEST.iso ->
    # RDR_CROWD_060_MENU.iso) and would redden the default gate until chased.
    # That stopped being true at 5344529: command line, then
    # 02_Modded/modlist-disc.txt, then a scan of 02_Modded that REFUSES if two
    # discs match. Unresolvable still exits 2, never 0.
    ("[M23] the main menu's mod list matches the last intentional build",
     ["frontmenu/verify_modlist_row.py"], True),
    # [SAVE], rdr-save's. Self-registered under the standing exception in
    # STATE-CONTRACT.md (other lanes are paused for this task);
    # recorded in LANE-REQUESTS.md as REQ-088.
    #
    # `True` -- genuinely quick-safe, and for once that is easy to argue: it
    # never opens the ISO, needs no Studio, takes no lock, and runs in ~2s.
    # It READS the developer's memory card to make a copy and writes only inside
    # 08_ForkLab/savelab/. Section 7 asserts his card is unchanged.
    #
    # What a green row here does NOT mean: that any of this is correct
    # in-game. It means the card parses, the save round-trips byte-exactly,
    # and edits land in the file. Money is not decoded -- 05_Docs/SAVE-FORMAT.md
    # says which fields are evidence and which are guesses.
    ("[SAVE] the memory card parses and a save round-trips byte-exactly",
     ["scripts/verify_save.py"], True),
    ("setup / paths", ["scripts/setup_check.py"], True),
]
LIVE = [
    ("disc handover to PCSX2", ["scripts/test_handover.py"]),
    ("edit round-trip", ["scripts/test_roundtrip.py"]),
    ("create + delete objects", ["scripts/test_create.py"]),
    ("undo / redo / mods", ["scripts/test_journal.py"]),
    ("weapon editor (62 copies)", ["scripts/test_guns.py"]),
    ("asset catalogue + placement", ["scripts/test_assets.py"]),
    ("environment field edit + undo", ["scripts/test_props_edit.py"]),
    ("reshape level geometry + undo", ["scripts/test_geom_edit.py"]),
    ("bulk field edit across disc", ["scripts/test_field_bulk.py"]),
    ("rewrite game text + undo", ["scripts/test_strings.py"]),
    ("character OBJ export + import", ["scripts/test_character_edit.py"]),
    ("new editing routes: cast, enemy, attrs, links, versions, transfer",
     ["scripts/test_new_routes.py"]),
    ("[M1] Place Files overwrites a .mod and a .tex",
     ["scripts/test_place_files.py"]),
    ("[M1] drag-and-drop places files end to end",
     ["scripts/test_file_drop.py"]),
    # Live because its third section drives GET/POST /api/settings against a
    # real Studio. The other three sections need no server and no disc, and
    # the script runs standalone with that one section skipped.
    ("[M4] settings persist across launches",
     ["scripts/verify_settings.py"]),
    # Live: it drives the routes web/states.js calls, which is what M10's
    # criterion means by "the tab's own API". The module level is covered by
    # verify_behaviour_edit.py, which is not registered because it boots PCSX2
    # for 185s (REQ-010). Sends `everywhere: False` and reverts what it
    # touched, so it leaves the working ISO as it found it.
    ("[M10] an enemy's .stateb rebinds through the tab's own API",
     ["scripts/test_behaviour_routes.py"]),
    # Live: it drives stream_import against a real Studio, writes one stream
    # and reverts it byte-identically. The encoder itself is covered offline.
    ("[M13] a wav becomes a stm, replaces a song, and decodes",
     ["scripts/test_stream_import.py"]),
    # Live: drives weapon_import against a real Studio, writes bow_lod_01.mod
    # and reverts it byte-identically (step 9, and a `finally` behind it for
    # the run that dies mid-write).
    #
    # Registered by rdr-fmt under the self-register exception, on the reviewer's
    # explicit ruling 2026-09-18 that M15's criterion -- "imports a weapon
    # `.mod` and asserts the replaced weapon re-reads with the new geometry"
    # -- is met. **It is met on ONE weapon.** Sacred_Bow, via bow_lod_01, is
    # the only importable target on the disc: 9 of the 14 weapon models are
    # binary 2.12 and this writer emits text 1.10, and every text model but
    # that one is named by 2 to 11 weapons with no editable ModelName
    # (REQ-045). Overwriting a shared one re-models up to eleven weapons, so
    # the importer refuses it -- checks 2 and 4 here are that refusal.
    # The scope is deliberate and recorded, not an accident of the test.
    ("[M15] an OBJ becomes a weapon model and re-reads off the disc",
     ["scripts/test_weapon_import.py"]),
    ("import panels: bad OBJ -> friendly error, good OBJ -> success (level + weapon)",
     ["scripts/test_import_panels.py"]),
    ("custom collision through the Studio: import, solid/walk-through, linked surface move, one undo",
     ["scripts/test_custom_collision_live.py"]),
    # Deep dive 11/11-12: a multi-piece model moves as ONE set and the collision
    # welded into the merged shells follows (move / geom_edit / mass_edit).
    ("[SET] multi-piece model + welded collision move as one: drag, reshape, selection, drift-free, one undo",
     ["scripts/test_move_set_live.py"]),
    # Mod packs: export only the changed files, apply to another disc.
    # Part 1 builds private ISO copies (never the working one); part 2 edits
    # and restores one file through the live Studio.
    ("[PACK] a mod pack of only the changed files applies to a fresh disc, byte-exact",
     ["scripts/test_modpack.py"]),
    # Export all animations: batch BVH export, every file read back.
    ("[ANIMS] every clip exports to BVH by skeleton, files parse back, cancel works",
     ["scripts/test_anim_export_all.py"]),
]


def studio_up(port=PORT):
    try:
        urllib.request.urlopen(
            "http://127.0.0.1:%d/api/config" % port, timeout=3).read()
        return True
    except Exception:
        return False


# ------------------------------------------------------------ studio freshness
#
# A Studio that was already listening used to be reused unconditionally, and
# that is the bug this section exists for. Python reads a module once, when the
# process starts, so a Studio launched before an edit keeps serving the code it
# read then. The live tests drive it regardless and report PASS or FAIL against
# source that is no longer on disc: a fix already written looks broken, and --
# the worse half -- a break already written looks fixed. That cost a full test
# cycle (PROGRESS-rdr-app, "Observations"), and `suite-run-hygiene` has said
# "let run_tests.py start/stop its own Studio" ever since.
#
# Only PYTHON source can go stale this way. `web/` is re-read from disc on every
# request (studio.py `_static`) and so is `features.json` (`_feature_allows` ->
# `features.manifest()`), so editing either one does not strand the server and
# neither belongs in the comparison. Widening it to them would refuse runs that
# are perfectly sound.

Freshness = collections.namedtuple("Freshness", "state pid started newest mtime why")
# state: "none"    nothing is listening
#        "fresh"   the listener started AFTER the last source edit
#        "stale"   the listener predates the source it is serving
#        "unknown" could not be established -- treated exactly like "stale"


def _clock(t):
    return time.strftime("%H:%M:%S", time.localtime(t))


def _ago(seconds):
    return "%dm%02ds" % (int(seconds) // 60, int(seconds) % 60)


def _rel(path):
    """`path` relative to the toolkit, falling back to the full path.

    ntpath.relpath RAISES across drive letters, and this is only ever used to
    make a message read better. Blowing up the whole suite while formatting a
    diagnosis is a poor trade.
    """
    try:
        return os.path.relpath(path, HERE)
    except ValueError:
        return path


def studio_sources():
    """Every file whose contents a running Studio froze at import time."""
    out = [os.path.join(HERE, "RDR_Studio.py")]
    for root, dirs, names in os.walk(os.path.join(HERE, "rdrstudio")):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        out += [os.path.join(root, n) for n in names if n.endswith(".py")]
    return out


def newest_source(sources=None):
    """(path, mtime) of the most recently edited source file, or (None, -1)."""
    best_p, best_m = None, -1.0
    for p in (studio_sources() if sources is None else sources):
        try:
            m = os.path.getmtime(p)
        except OSError:
            continue
        if m > best_m:
            best_p, best_m = p, m
    return best_p, best_m


def listening_pid(port=PORT):
    """PID of the process listening on `port`, or None if it cannot be told.

    The LISTENING row is picked out by its FOREIGN address, not by the word
    "LISTENING", which is translated on a non-English Windows. Matching the
    local port alone is not enough either: the server's own ESTABLISHED rows
    carry the same local port and the same pid, and there were 50-odd of them
    in the netstat this was written against.
    """
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["netstat", "-ano", "-p", "TCP"], stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, timeout=60,
            ).stdout.decode("utf-8", "replace")
            for line in out.splitlines():
                f = line.split()
                if (len(f) >= 5 and f[1].endswith(":%d" % port)
                        and f[2] in ("0.0.0.0:0", "[::]:0", "*:*")):
                    return int(f[4])
            return None
        out = subprocess.run(
            ["ss", "-ltnpH"], stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=60,
        ).stdout.decode("utf-8", "replace")
        for line in out.splitlines():
            f = line.split()
            if len(f) >= 4 and f[3].endswith(":%d" % port):
                m = re.search(r"pid=(\d+)", line)
                if m:
                    return int(m.group(1))
        return None
    except Exception:
        return None


def proc_start(pid):
    """Epoch seconds at which `pid` started, or None if it cannot be read."""
    if pid is None:
        return None
    try:
        if os.name == "nt":
            ps = ("$p = Get-Process -Id %d -ErrorAction Stop; "
                  "[int64]($p.StartTime.ToUniversalTime() - "
                  "[datetime]'1970-01-01').TotalSeconds" % pid)
            out = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=90)
            return float(out.stdout.decode("utf-8", "replace").strip())
        # /proc/<pid>/stat field 22 is the start time in clock ticks since
        # boot; everything after the "(comm)" field is offset by three.
        with open("/proc/stat") as f:
            btime = next(float(l.split()[1]) for l in f if l.startswith("btime "))
        with open("/proc/%d/stat" % pid) as f:
            after_comm = f.read().rsplit(") ", 1)[1].split()
        return btime + float(after_comm[19]) / os.sysconf("SC_CLK_TCK")
    except Exception:
        return None


def studio_freshness(port=PORT, sources=None):
    """Is the Studio on `port` running the code that is on disc right now?

    Anything this cannot establish comes back "unknown", never "fresh". An
    undetermined answer is the one that has to be loud: a check that quietly
    falls back to "go ahead" is indistinguishable from no check at all, which
    is the whole failure mode `defaults-that-silently-mean-off` is about.
    """
    if not studio_up(port):
        return Freshness("none", None, None, None, None,
                         "nothing is listening on %d" % port)
    newest, mtime = newest_source(sources)
    if newest is None:
        return Freshness("unknown", None, None, None, None,
                         "found no Studio source files to compare against")
    pid = listening_pid(port)
    if pid is None:
        return Freshness("unknown", None, None, newest, mtime,
                         "something is listening on %d, but the process that "
                         "owns the port could not be identified" % port)
    started = proc_start(pid)
    if started is None:
        return Freshness("unknown", pid, None, newest, mtime,
                         "PID %d owns port %d, but its start time could not "
                         "be read" % (pid, port))
    rel = _rel(newest)
    if started < mtime:
        return Freshness("stale", pid, started, newest, mtime,
                         "PID %d started %s; %s was edited %s, %s later"
                         % (pid, _clock(started), rel, _clock(mtime),
                            _ago(mtime - started)))
    return Freshness("fresh", pid, started, newest, mtime,
                     "PID %d started %s, %s after the last source edit (%s, %s)"
                     % (pid, _clock(started), _ago(started - mtime), rel,
                        _clock(mtime)))


def other_suite_pids():
    """PIDs of OTHER run_tests.py processes: [] if none, None if undeterminable.

    Two traps, both already paid for in this repo:

    * A plain command-line match matches the PROCESS DOING THE MATCHING. The
      PowerShell query below carries "run_tests.py" in its own command line and
      the first version of this dutifully reported itself. Anchoring on the
      executable NAME drops it -- the same fix `suite-run-hygiene` records for
      `pgrep -af rdr-suite.lock` matching the watcher's own shell.
    * A run launches a chain of them (cmd.exe -> py.exe -> python.exe), so the
      caller's own launcher matches too. Everything connected to this process
      through the matched set is excluded below.

    A suite here is a WINDOWS python launched from WSL through cmd.exe, so the
    two branches do not see the same things. The Windows one sees the suites
    themselves. The posix one cannot -- a Windows process has no /proc entry --
    and would report a clean [] with a full suite running, so it counts the
    `flock` holders of the shared lock instead, which is what a run from this
    side is wrapped in. Anchoring on argv[0] is again the point: a match on the
    lock NAME alone also matches whatever is doing the matching.
    """
    matched = {}
    try:
        if os.name == "nt":
            ps = ("Get-CimInstance Win32_Process | Where-Object { "
                  "($_.Name -match '^(py|python|pythonw)\\.exe$') -and "
                  "($_.CommandLine -like '*run_tests.py*') } | "
                  "ForEach-Object { '{0}|{1}' -f $_.ProcessId, "
                  "$_.ParentProcessId }")
            out = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=120)
            for line in out.stdout.decode("utf-8", "replace").splitlines():
                f = line.strip().split("|")
                if len(f) == 2 and f[0].isdigit() and f[1].isdigit():
                    matched[int(f[0])] = int(f[1])
        else:
            for d in os.listdir("/proc"):
                if not d.isdigit():
                    continue
                try:
                    with open("/proc/%s/cmdline" % d, "rb") as f:
                        cmd = f.read().decode("utf-8", "replace")
                    argv0 = os.path.basename(cmd.split("\0")[0])
                    hit = (("run_tests.py" in cmd
                            and argv0.startswith(("py", "python")))
                           or ("rdr-suite.lock" in cmd and argv0 == "flock"))
                    if not hit:
                        continue
                    with open("/proc/%s/stat" % d) as f:
                        ppid = int(f.read().rsplit(") ", 1)[1].split()[1])
                    matched[int(d)] = ppid
                except (OSError, ValueError, IndexError):
                    continue
    except Exception:
        return None

    mine, grew = {os.getpid()}, True
    while grew:
        grew = False
        for p, pp in matched.items():
            if p in mine and pp in matched and pp not in mine:
                mine.add(pp)
                grew = True
            elif pp in mine and p not in mine:
                mine.add(p)
                grew = True
    return sorted(p for p in matched if p not in mine)


def _refuse_stale(f):
    """Print the diagnosis and stop. Never returns."""
    print("\n" + "!" * 70)
    print("STUDIO-STALE: refusing to run against the Studio on port %d" % PORT)
    print("!" * 70)
    print("  %s" % f.why)
    print("")
    print("  Python reads its modules once, when the process starts, so a")
    print("  Studio older than the source it serves answers with the code it")
    print("  read back then. A live test driven against it reports PASS or")
    print("  FAIL about a file that is not on disc any more -- in either")
    print("  direction. No test has been run.")
    print("")
    print("  Pick one:")
    print("    run_tests.py --restart-studio   stop that Studio, start a fresh one")
    print("    run_tests.py --reuse-studio     drive it anyway, knowing the above")
    print("    stop the Studio yourself and re-run")
    sys.exit(2)


def kill_studio(pid):
    """Stop exactly the process holding the port. Never returns on failure."""
    print("stopping the stale Studio (PID %d)..." % pid)
    try:
        if os.name == "nt":
            # By PID. NOT `taskkill /f /im python.exe`, which takes out every
            # other lane's suite as well -- done for real on 2026-09-14.
            subprocess.run(["taskkill", "/f", "/pid", str(pid)],
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=60)
        else:
            import signal
            os.kill(pid, signal.SIGTERM)
    except Exception as ex:
        print("  could not signal PID %d (%s)" % (pid, ex))
    for _ in range(20):
        time.sleep(1)
        if not studio_up():
            print("  stopped; port %d is quiet." % PORT)
            return
    print("STUDIO-STALE: PID %d is still listening on %d; nothing has been run."
          % (pid, PORT))
    sys.exit(2)


def _same_disc(a, b):
    """Do two spellings name the same file, across the WSL/Windows boundary?

    The suite runs Windows `py -3` and reports ...; a Studio
    started from WSL reports ... . Comparing those raw
    would fail on every mixed pair and the check would be dropped as noisy
    within a day, which is how a real guard becomes decoration.
    """
    def norm(x):
        x = (x or "").replace("\\", "/").lower().rstrip("/")
        m = re.match(r"^/mnt/([a-z])/(.*)$", x)
        if m:
            x = "%s:/%s" % (m.group(1), m.group(2))
        return x
    return bool(a) and bool(b) and norm(a) == norm(b)


# Three outcomes, and collapsing any two of them is a bug. Returned as
# (reachable, disc) because the first version returned None for BOTH "nothing
# answered" and "answered but would not say", and those want opposite
# treatment: the first is not this check's business, the second is exactly its
# business. Conflating them made the gate fire against a port with nothing on
# it, which is what broke test_studio_freshness's decision table -- it
# simulates a listener without starting one, so every "runs" case refused.
UNREACHABLE = (False, None)


def studio_disc(port=PORT):
    """(reachable, which working ISO it serves).

    /api/config already carries it and run_tests already parses that response,
    so this costs nothing but the comparison.
    """
    import json as _json
    try:
        raw = urllib.request.urlopen(
            "http://127.0.0.1:%d/api/config" % port, timeout=5).read()
    except Exception:                                      # noqa: BLE001
        return UNREACHABLE
    try:
        return True, (_json.loads(raw.decode("utf-8")) or {}).get("modded")
    except Exception:                                      # noqa: BLE001
        return True, None


def _check_disc(mode):
    """Refuse a Studio that is serving a disc we did not ask about.

    Freshness answers "is this process newer than the source" and the pid line
    answers "did my own child survive". NEITHER answers the question a green
    result actually claims, which is "was this the disc under test" -- rdr-fmt's
    framing, and it is the stronger one: proving the process is yours is weaker
    than proving the disc under it is the right one.

    Found live 2026-09-18: a peer's negative-control Studio was on 8731 serving
    a scratch neg.iso. It answered, it was freshly started, and it
    passed the freshness gate -- indistinguishable from the right Studio. A
    hand-started suite would have run its whole live block against a disc
    nobody meant to test, and reported PASS or FAIL about it.

    A MISMATCH refuses in every mode, --reuse-studio included: reuse means "I
    accept code that may be stale", not "I accept results about another disc".
    It also refuses rather than restarting, because a Studio pointed somewhere
    else is demonstrably somebody's, and killing it costs them a run -- the
    same reasoning that already stops --restart-studio firing while another
    suite is live.
    """
    # Imported here, not at the top: run_tests.py is a runner and deliberately
    # does not depend on the package it tests at import time -- a syntax error
    # in rdrstudio should fail a ROW, not stop the suite from starting.
    from rdrstudio import config as cfg
    reachable, serving = studio_disc()
    if not reachable:
        # Nothing answered. Whether that is fine or not belongs to the
        # freshness state machine, which has already run -- this check judges
        # a Studio that ANSWERS, and there is nothing here to judge.
        return
    if serving is None:
        if mode == "reuse":
            print("  (could not read which disc that Studio serves)")
            return
        print("\nSTUDIO-STALE: the Studio on port %d answered but will not say "
              "which working ISO it serves," % PORT)
        print("  so there is no way to tell it apart from a Studio pointed at "
              "another disc.")
        print("  Nothing has been run.")
        sys.exit(2)
    if not _same_disc(serving, cfg.MODDED_ISO):
        print("\nSTUDIO-STALE: the Studio on port %d is serving a different "
              "disc." % PORT)
        print("    it serves : %s" % serving)
        print("    we expect : %s" % cfg.MODDED_ISO)
        print("  Its results would be about that disc, not this one. It is not "
              "stopped, because")
        print("  a Studio pointed elsewhere belongs to somebody and killing it "
              "costs them a run.")
        print("  Stop it yourself, or ask its owner to use another port. "
              "Nothing has been run.")
        sys.exit(2)


def resolve_studio(mode):
    """Settle what to do about a Studio that is already listening.

    Returns with the port either quiet or holding a Studio established to be
    current. `mode` is "refuse" (the default), "restart" or "reuse".
    """
    f = studio_freshness()
    if f.state == "none":
        return
    # Before freshness, because a Studio on the wrong disc is wrong however
    # current its code is.
    _check_disc(mode)
    if f.state == "fresh":
        print("\nreusing the Studio already on %d -- %s" % (PORT, f.why))
        print("  do not click around in the browser while the live tests run,")
        print("  or concurrent edits will show up as spurious failures.")
        return

    if mode == "reuse":
        print("\n" + "!" * 70)
        print("--reuse-studio: driving a Studio that is NOT known to be current")
        print("!" * 70)
        print("  %s" % f.why)
        print("  Any live PASS or FAIL below may be about code that is not on")
        print("  disc. Treat this run as evidence about the SERVER, not the")
        print("  source tree.")
        return
    if mode != "restart":
        _refuse_stale(f)                       # exits

    # One decision, one action: an unknown answer and a positive answer are
    # both "someone else may be using that Studio", and both mean do not kill.
    # The 2026-09-14 incident was a script that PRINTED `suites: 2` and killed
    # anyway, because the check and the kill were two separate lines.
    others = other_suite_pids()
    if others is None or others:
        print("\n" + "!" * 70)
        print("STUDIO-STALE: will not restart the Studio on %d" % PORT)
        print("!" * 70)
        print("  %s" % f.why)
        print("  %s" % ("another run_tests.py is live (PID %s) and is probably "
                        "driving it" % ", ".join(str(p) for p in others)
                        if others else
                        "could not establish whether another run_tests.py is "
                        "live, and killing one lane's Studio mid-run costs "
                        "them the whole suite"))
        print("  Wait for it to finish, or stop the Studio yourself.")
        print("  Nothing has been run.")
        sys.exit(2)

    if f.pid is None:
        print("\nSTUDIO-STALE: something holds port %d but its PID is unknown, "
              "so there is nothing safe to stop. Nothing has been run." % PORT)
        sys.exit(2)
    kill_studio(f.pid)


def bank_checkpoint():
    """Bank the user's work before anything writes. True if that is settled.

    False means "there was no Studio to ask", and the caller should try again
    once there is one. The original ran this once, at the top of main(), under
    a plain `if studio_up()`: on a cold machine nothing was listening yet, the
    checkpoint was quietly skipped, and the live tests went on to write to the
    working ISO with no net underneath -- silently, because a skipped safety
    step looks exactly like one that ran and found nothing to do.
    """
    if not studio_up():
        return False
    try:
        import json as _json
        diff = _json.load(urllib.request.urlopen(BASE + "diff", timeout=60))
        if diff:
            req = urllib.request.Request(
                BASE + "checkpoint",
                data=_json.dumps({"label": "auto: before run_tests"}).encode(),
                headers={"Content-Type": "application/json"})
            cp = _json.load(urllib.request.urlopen(req, timeout=900))
            print("banked a checkpoint of your %d changed file(s) first:"
                  % len(diff))
            print("  %s" % cp.get("path", "?"))
            print("  restore it from the Changes tab if a test loses work.")
    except Exception as ex:
        print("WARNING: could not bank a checkpoint (%s)." % ex)
        print("Live tests write to the working ISO. Ctrl+C now if you have")
        print("unsaved work you cannot afford to lose.")
        time.sleep(5)
    return True


def run(label, args, quick_ok):
    print("\n" + "=" * 70)
    print(">> %s" % label)
    print("=" * 70)
    t0 = time.time()
    p = subprocess.run([PY] + args, cwd=HERE)
    dt = time.time() - t0
    status = "PASS" if p.returncode == 0 else "FAIL"
    print("-- %s (%.1fs)" % (status, dt))
    return p.returncode == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true",
                    help="skip the full-disc sweeps")
    # Off by default on purpose: it launches PCSX2 and drives it by sending
    # keystrokes, so it takes over the screen and keyboard for ~90 seconds.
    # That is not something a routine test run should do without being asked.
    ap.add_argument("--ingame", action="store_true",
                    help="also check that an edit reaches the RUNNING game "
                         "(launches PCSX2, takes over input for ~90s)")
    ap.add_argument("--restart-studio", action="store_true",
                    help="if the Studio already listening is older than the "
                         "source it serves, stop it and start a fresh one")
    ap.add_argument("--no-lock", action="store_true",
                    help="run without the suite lock, loudly (see the refusal)")
    ap.add_argument("--reuse-studio", action="store_true",
                    help="drive an already-listening Studio even when it "
                         "cannot be shown to be running the current source")
    args = ap.parse_args()

    # THE SUITE LOCK, AND WHY THIS REFUSES RATHER THAN TAKING IT.
    #
    # This file cannot take /tmp/rdr-suite.lock. It runs as WINDOWS python --
    # measured: platform win32, fcntl unavailable, and /tmp is not a path this
    # process can see. The file is reachable as \\wsl$\Ubuntu\tmp\..., but a
    # lock taken across that boundary does not join WSL's flock namespace, so
    # it would look like protection and provide none. That is worse than no
    # lock at all.
    #
    # So the lock stays with the WSL-side wrapper, and this refuses to run
    # unless a wrapper says it holds it. Until 2026-09-18 there was no check:
    # verify.sh took the lock, test.sh took the lock, and `py -3 run_tests.py`
    # -- the obvious thing to type, and what the docs showed -- silently
    # bypassed the queue. A suite running outside the queue is invisible to
    # every other lane's gate check, which is how two runs collided today.
    if os.environ.get("RDR_SUITE_LOCK") != "held" and not args.no_lock:
        print("  This suite must run under /tmp/rdr-suite.lock, and it cannot")
        print("  take the lock itself -- it runs as Windows python, which has")
        print("  no fcntl and cannot see /tmp.")
        print()
        print("  Use one of:")
        print("      bash 03_Tools/test.sh --full")
        print("      bash 03_Tools/verify.sh")
        print("      flock /tmp/rdr-suite.lock cmd.exe /c \"set RDR_SUITE_LOCK=held"
              "&& py -3 run_tests.py\"")
        print()
        print("  --no-lock runs anyway and says so, for a deliberately")
        print("  unqueued run.")
        print("Nothing has been run.")
        return 2
    if args.no_lock:
        print("WARNING: running WITHOUT the suite lock (--no-lock).")
        print("  Other lanes' gate checks cannot see this run. Concurrent")
        print("  access to the working ISO is not serialised.")

    if args.restart_studio and args.reuse_studio:
        ap.error("--restart-studio and --reuse-studio contradict each other")
    mode = ("restart" if args.restart_studio else
            "reuse" if args.reuse_studio else "refuse")

    # Bank the user's work before ANY test runs.
    #
    # `test_journal.py` already does this for itself, and that is the only
    # reason a night's work was recoverable on 2026-07-31. But it is the only
    # test that does, so every other live test touches the working ISO with no
    # net underneath. One checkpoint here covers all of them, costs a few
    # seconds, and is skipped when the disc is already clean.
    #
    # FIRST, ahead of `resolve_studio` -- which under --restart-studio stops
    # the Studio, and an unreachable Studio banks nothing. A checkpoint taken
    # through a slightly stale server is still the user's bytes; skipping it is
    # the outcome that loses a night's work.
    banked = bank_checkpoint()

    resolve_studio(mode)

    results = []
    for label, cmd, is_quick in OFFLINE:
        if args.quick and not is_quick:
            print("skipping %s (--quick)" % label)
            continue
        results.append((label, run(label, cmd, is_quick)))

    started = None
    if not studio_up():
        print("\nstarting Studio for the live tests...")
        started = subprocess.Popen(
            [PY, "RDR_Studio.py", "--no-browser"], cwd=HERE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(30):
            time.sleep(1)
            # OUR CHILD FIRST, because studio_up() asks the PORT and the port
            # is not ours. `studio_up()` above and this bind are two steps, and
            # four lanes share this machine: a peer can take 8731 in between,
            # our child dies with "Address already in use", and the very next
            # studio_up() answers True about THEIR Studio. The loop would then
            # break, the live tests would drive a Studio this process does not
            # own against the shared working ISO, and started.terminate() at
            # the end would terminate a corpse.
            #
            # That is not hypothetical. rdr-fmt's harness (REQ-077) has the
            # same readiness loop and did exactly this: it printed "studio up
            # after 31s, pid 39846" for a pid that was in an Errno 98
            # traceback, then filed a stale-read bug against studio.py which
            # dissolved once the real Studio was identified. A readiness check
            # that polls a port does not tell you your own process is alive.
            if started.poll() is not None:
                print("STUDIO-STALE: the Studio we started exited with code %d "
                      "before it answered." % started.returncode)
                print("  Something else may now be listening on %d -- a peer's "
                      "Studio takes the port the same way ours does, and the" % PORT)
                print("  readiness check cannot tell the difference. Driving it "
                      "would run the live tests against a Studio this run does")
                print("  not own. Nothing has been run.")
                sys.exit(2)
            if studio_up():
                break
        else:
            print("Studio did not come up")
            started.terminate()
            started = None
        if started is not None:
            # Evidence, not a gate: on Windows `py -3` is a launcher and the
            # server is its CHILD, so started.pid and the listening pid are
            # legitimately different and an equality check would be a false
            # red. Printing it is what lets a later question -- "whose Studio
            # answered this run?" -- be settled from the log instead of
            # assumed, which is the gap REQ-077 was filed into.
            print("  Studio pid %s, listening pid %s"
                  % (started.pid, listening_pid(PORT)))

    # The Studio is only now reachable on a cold start, or after a restart.
    if not banked:
        bank_checkpoint()

    # The check that actually guards the live tests. `resolve_studio` above
    # fails fast, before the offline sweeps spend forty minutes; this one sits
    # next to the use, and catches a source edit made WHILE they were running
    # -- by another lane, or by a hand. Four lanes share this tree.
    #
    # It is also what exercises the detector in the ALLOW direction on every
    # single run, including against the Studio this process just started
    # itself. A guard that has only ever been watched to say "no" has not been
    # tested.
    fresh = studio_freshness()
    if fresh.state == "fresh" or (mode == "reuse" and fresh.state != "none"):
        print("\nlive tests: %s" % fresh.why)
        for label, cmd in LIVE:
            results.append((label, run(label, cmd, False)))

        # Last, because it boots the disc in an emulator and wants the
        # server's file handles released while it does.
        if args.ingame:
            results.append(("an edit reaches the RUNNING game",
                            run("an edit reaches the RUNNING game",
                                ["scripts/verify_ingame.py"], False)))
    else:
        # Recorded as a FAILED row, not skipped in silence. A run that drops
        # every live test and still prints "12/12 passed" is the same lie in a
        # different costume -- and "12" is not a number anyone counts.
        print("\nSTUDIO-STALE: live tests NOT RUN -- %s" % fresh.why)
        results.append(("the %d live tests -- NOT RUN (%s)"
                        % (len(LIVE), fresh.why), False))

    if started:
        started.terminate()
        try:
            started.wait(timeout=10)
        except Exception:
            started.kill()
        # terminate() is TerminateProcess on Windows and cannot be caught, so
        # the Studio's atexit never runs and its writer lock on the working ISO
        # outlives this run -- four times in one day. The next writer ON
        # WINDOWS clears it on the way past, but `_pid_alive` answers None
        # across the WSL/Windows boundary on purpose (unrelated pid spaces), so
        # from WSL every writer afterwards refuses over a holder that is gone.
        # Clear it here, where the platform matches the pid we just killed.
        #
        # It removes a lock only when the recorded pid is PROVABLY dead, so a
        # peer's live Studio is never disturbed -- and note it cannot key off
        # `started.pid`: on Windows `py -3` is a launcher and the server is its
        # child, which is the pid in the file.
        try:
            from rdrstudio import config as cfg2
            from rdrstudio.iso9660 import Iso9660
            held = Iso9660.clear_stale_lock(cfg2.MODDED_ISO)
            if held:
                print("  cleared the writer lock this run left behind (%s)"
                      % held)
        except Exception as ex:                               # noqa: BLE001
            print("  could not check for a stale writer lock: %s" % ex)

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for label, ok in results:
        print("  [%s] %s" % ("PASS" if ok else "FAIL", label))
    failed = [l for l, ok in results if not ok]
    print("\n%d/%d passed" % (len(results) - len(failed), len(results)))
    if failed:
        print("FAILED: %s" % ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
