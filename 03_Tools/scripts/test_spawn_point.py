"""[M20 DRAFT - NOT REGISTERED] A spawn point is set, and the winning file agrees.

    python3 scripts/test_spawn_point.py

**This test cannot pass yet, on purpose.** M20's criterion is *"sets a spawn
point and asserts the value that the game would load (the winning file) is
the edited one"*, and which file wins is not yet measured. Until it is, this
reports UNPROVEN and exits non-zero rather than green.

That is deliberate and it is REQ-067's lesson turned on myself: a check that
cannot fail is worse than no check, and "the parts I could test passed" is
exactly how a milestone gets ticked on half its criterion. The file-level
half below is real and runs today; it simply is not the milestone.

## What is settled (measured, rdr-fmt + rdr-3d independently)

    71 story .spwn      53 group-1 only      18 multi-group
    every multiplayer .spwn is multi-group
    declared NumSpawnPoints == Position lines   97/97

    nothing else in a level archive pairs a player key with coordinates:
      br_playerai_start.stateb   no positional verb
      per-level intro .actb      no positional verb
      br_playerai.tbl            binds states only
      .aisp                      NPCs -- sits beside airooms/spawn.cpp

So the tester's inherited "most likely a `.stateb`" has no support in the data.

## What is NOT settled, and why WINNER stays None

* **There is no live reading of anything.** The one boot so far produced a
  TITLE-SCREEN state; the frames that read as in-level were the attract
  demo. No RAM value has been captured.
* **The `actor->0xA8` / 17-arm-jump-table account is second-hand.** It has
  not been reproduced by anyone who can show the work, and rdr-3d has asked
  whether that disassembly happened at all. This file does not lean on it.
* **The band hypothesis is unproven and is the thing to test.** If a story
  level carries four groups and the run reads a band other than the first,
  editing "the spawn" edits a point the run never reads -- which looks
  exactly like the tester's override with nothing overriding. 18 story levels
  are multi-group, so it is not hypothetical.

## What this file does today, and what it is waiting for

    section 1   the .spwn parses and its declared counts are honest   RUNS
    section 2   a spawn point is SET on the working ISO, read back
                through a fresh handle, and restored in a finally     RUNS
    section 3   which file the game actually loaded                   BLOCKED

Section 3 needs **one** in-level savestate and WINNER.

**The two-state `dy=+40` design this file used to describe is DEAD, and the
reason is worth keeping.** Every PERFNEW spawn y sits between -4.5 and 0, so
writing y=+40 puts the player forty units above the floor: on spawn they
FALL, y changes every frame, and no bank-on-entry beats gravity. Worse, with
contiguous bands a state banked slightly late decodes as a confident WRONG
group, and which group depends on when F1 was pressed. **A plausible answer
instead of a null** -- the worst of tonight's failure modes, and rdr-app
killed it for exactly that.

What is actually on the disc (landed, verified, on the reviewer's ruling):

    lvl_perfnew.spwn  in LVLS_SNG/PERFNEW.DAT
    all 24 points: x nudged by (index+1) MILLIMETRES
    y and z untouched -- the player spawns on the floor as always
    points 0-5 group 1, 6-11 group 2, 12-17 group 3, 18-23 group 4

**The marker is self-identifying**: the recovered x names the group AND the
index, from a single state. No differencing is required, and the two
objections that seemed to demand it are handled by construction rather than
by inference --

  * *"a value that happens to be there"* -- `readmark` classifies each hit
    against the parsed-record span off the global spawn list at 0x00760CF8
    (object+36, group stride 360, point stride 36), and requires the actor
    check `A-1464+0xA8` to read 1..16.
  * *"rand % count makes the point unpredictable"* -- true and harmless. The
    draw is WITHIN a group; the group index arrives deterministically from
    the jump table. All six points of every group are marked distinctly, so
    whichever the RNG picks, the marker still names the group -- which is
    the discriminating question and the only one the milestone asks.

Until a state and WINNER arrive this reports UNPROVEN and exits non-zero,
which is REQ-067's lesson turned on myself: a check that cannot fail is
worse than no check, and "the parts I could test passed" is exactly how a
milestone gets ticked on half its criterion.

**`--selftest` proves the section-3 machinery can say YES**, against synthetic
memory where the answer is known, including a decoy copy that does not move.
Without it, the day a real dump arrives a "nothing found" result would be
indistinguishable from a search that never worked. It is kept even though the
single-state design no longer differences two dumps: the same machinery reads
a marker out of a memory image, and a search that cannot demonstrate a YES is
not evidence of a NO.

**Provenance note.** Sections 2 and 3 and `--selftest` appeared in this file
uncommitted and are not this session's work; both committed drafts before
them carry `session_01KF8KtVvF6LRupXK1zEKZD9`. A dirty tree records no
author, so who wrote them cannot be established by anyone -- rdr-3d ruled
out their own lane and that is as far as evidence goes. They are kept because
they are good and because the alternative is discarding work nobody can
claim; this note exists so the history does not silently imply I wrote them.
"""
import os
import re
import struct
import sys
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _HERE]
sys.path.insert(0, os.path.dirname(_HERE))

from rdrstudio import config                                 # noqa: E402
from rdrstudio.game import Game                              # noqa: E402
from rdrstudio.level import Level                            # noqa: E402

# ---------------------------------------------------------------- the answer
#
# None means "not yet measured". Set it to a dict like:
#
#     WINNER = dict(file="lvl_tomb.spwn", group=1,
#                   evidence="RAM read <date>: group recovered by unique "
#                            "per-point marker; randomiser at 0x003552C0 "
#                            "verified independently by rdr-3d and rdr-app")
#
# **There is no `index` field, and that is the finding, not an omission.**
# rdr-3d disassembled 0x003552FC: the group index arrives as an ARGUMENT and
# is deterministic, but the point WITHIN the group is `rand % count` --
#
#     lw s1, 8(v0)      count for this group
#     slti v1, s1, 2    fewer than 2 -> return, no draw
#     jal 0x0014A528    the RNG
#     div v0, s1        rand % count
#
# So a single boot settles which GROUP was used and CANNOT settle which
# point. Asserting a fixed index would fail five times in six on a
# six-point group -- a flaky test that would read as a real defect. The
# assertion below is therefore MEMBERSHIP in the measured group.
#
# This matters for the milestone too: band-1-always and group-N-by-team
# differ on the GROUP, not on the index. The discriminating question is the
# one a single boot can answer.
#
# `evidence` is required: an answer with no measurement behind it is the
# thing this file exists to refuse.
WINNER = dict(
    file="lvl_sher70.spwn",
    group=1,
    evidence=(
        "RAM measurement 2026-09-18 from rdr-app's savestate, reproduced by "
        "rdr-3d before being believed. obj+8 counts read (2,1,1,1), which is "
        "lvl_sher70.spwn's shape exactly. The group-1 point-0 marker appears "
        "54 times, replicated through the engine; the other four appear once "
        "each, in the table only. Of 19 copies outside the parsed records, "
        "exactly one is actor-plausible: 0x01B84E78 -> A-1464+0xA8 == 1 -> "
        "band 1 -> group 1. Marker scheme independently confirmed by rdr-fmt "
        "against vanilla: every x is vanilla + (index+1) mm."),
    limits=(
        # Stated because rdr-app stated them rather than letting anyone infer.
        "0xA8 == 1 CANNOT separate team-band from player-slot: a campaign "
        "player is team 1 AND slot 1, so one single-player sample fits both "
        "readings. The band STRUCTURE is verified by disassembly; the "
        "SEMANTICS are not. A Showdown boot with several actors would "
        "separate them.",
        "The strength is three signals agreeing, not the actor hit alone. "
        "'reads exactly 1' is the commonest small value in RAM. What makes "
        "the 54 replications real rather than statistical is that "
        "neighbouring values at +/-1, 2 and 3 mm have ZERO hits each.",
        "The level that loaded was SHER70, not PERFNEW. 'New' does not load "
        "mission 1's archive. This was caught by the counts check REFUSING, "
        "not by anyone noticing -- which is the argument for the check.",
        "SHER70 read group 1, so this run can neither confirm nor refute the "
        "band hypothesis. A level whose 0xA8 is not 1 would.",
    ),
)

# The SUBJECT level. The reviewer has ruled PERFNEW for the measurement, superseding
# TOMB (mission 6, unreachable by keystroke automation). Not pinned to PERFNEW
# here yet on purpose: rdr-app reports which level ACTUALLY loaded rather than
# which one was aimed at, and those have differed once already. `--level X`
# overrides; set the default when the readout lands.
LEVEL = "TOMB"
ARC = LEVEL_SPWN = None          # set by resolve_level(), called from main()


def resolve_level(argv=None):
    """Point the test at a level. `--level X` overrides the default.

    This exists as a FUNCTION rather than three module constants because the
    level is now genuinely in flux: The reviewer ruled PERFNEW over TOMB, the boot
    that was to confirm it failed, and rdr-app's warning stands that whenever
    a state does arrive it may be from whatever level can actually be reached
    rather than the one anyone chose. A hardcoded subject would quietly assert
    about a level nobody measured.
    """
    global LEVEL, ARC, LEVEL_SPWN
    argv = sys.argv if argv is None else argv
    if "--level" in argv:
        i = argv.index("--level")
        if i + 1 >= len(argv):
            sys.exit("--level needs a level name, e.g. --level PERFNEW")
        LEVEL = argv[i + 1].upper()
    ARC = "LVLS_SNG/%s.DAT" % LEVEL
    LEVEL_SPWN = "lvl_%s.spwn" % LEVEL.lower()
    return LEVEL, ARC, LEVEL_SPWN
FAILURES = []
_PRISTINE = []
_MARKED_AT = []   # set by section 2 when the markers land
_RESTORED = []


def suite_is_live():
    """Other Python processes on the Windows side, i.e. probably a suite.

    This test WRITES the working ISO, and it had no guard at all until a lane
    ran it straight into a live `run_tests.py` and contaminated that run. The
    bytes survived -- the restore below is sound -- but per suite-run-hygiene
    the debris from a collision lands on the run AFTER it, so the person who
    pays is not the person who can see the cause.

    **Authorship: this guard is rdr-fmt-7d's** (`session_01DbjFQT...`), not
    the session whose commit it arrived in. It was uncommitted in the tree
    when 0fb9ca7 committed this file by pathspec, so ~50 lines went in under
    a subject about WINNER and stale savestates. Nothing needs undoing -- the
    code belongs here -- but `git log -S suite_is_live` points at the wrong
    session and this note is the correction. Third such sweep in one night,
    in three directions: pathspec protects your COMMIT, not your WORK.

    Counted with `tasklist`, NOT `pgrep`: the suite runs as py.exe/python.exe
    on the Windows side and pgrep from WSL cannot see it at all. A pgrep-based
    check reports a quiet machine in the middle of a run, which is the most
    dangerous possible wrong answer for a guard like this one.

    `flock /tmp/rdr-suite.lock` is still the right wrapper when running this
    from WSL; this guard is the backstop for when someone does not.
    """
    import subprocess
    try:
        out = subprocess.run(["tasklist"], capture_output=True, text=True,
                             timeout=30).stdout
    except Exception:                                      # noqa: BLE001
        return None            # unknown -- caller decides, and must not
                               # read unknown as "quiet"
    me = os.getpid()
    live = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) > 1 and parts[0].lower() in ("py.exe", "python.exe"):
            try:
                if int(parts[1]) != me:
                    live.append((parts[0], int(parts[1])))
            except ValueError:
                pass
    return live


def check(cond, label, detail=""):
    print("  [%s] %-54s %s" % ("PASS" if cond else "FAIL", label, detail))
    if not cond:
        FAILURES.append(label)


def spawn_nodes(game, archive):
    lv = Level(game, archive)
    return [n for n in lv.nodes_json() if n.get("kind") == "spawn"]


def restore():
    """Put the .spwn back, and prove it went back. See M15's lesson:
    Game() opens read-only by default, so a revert needs writable=True and a
    finally, or the run that throws leaves the disc dirty for the next lane."""
    if _RESTORED or not _PRISTINE:
        return
    _RESTORED.append(True)
    try:
        g = Game(config.MODDED_ISO, writable=True)
        d = g.archive(ARC)
        if d.read(d.find(LEVEL_SPWN)) == _PRISTINE[0]:
            check(True, "already as it started -- nothing to undo")
            g.close()
            return
        d.patch(LEVEL_SPWN, _PRISTINE[0])
        g.iso.flush()
        g.close()
    except Exception as ex:
        check(False, "revert", str(ex))
        return
    d2 = Game(config.MODDED_ISO).archive(ARC)
    check(d2.read(d2.find(LEVEL_SPWN)) == _PRISTINE[0],
          "the .spwn is byte-identical to how it started")


# The savestate reader is deliberately NOT imported from
# `m20_spawn_probe.py`. That file is rdr-3d's -- they own M20's probe and
# measurement -- and under contract rule 3 taking it as a DEPENDENCY would
# claim it. Twenty lines of zipfile and struct is cheaper than claiming
# another lane's file out from under them. If rdr-3d would rather publish
# these as a shared helper, say so and this duplication goes away.

# Point i gets x nudged by (i+1) millimetres; y and z are left exactly as
# shipped. This is rdr-3d's marker scheme and the reasoning is theirs, worth
# restating because the first version of THIS file got it wrong the same way
# they did: a large offset on Y puts the player above the spawn plane, so on
# spawn they FALL, y changes every frame, and a state banked slightly late
# decodes as a confident WRONG point. Contiguous bands make that a plausible
# answer rather than a null -- the exact failure mode this milestone is about.
# Millimetres on x: a falling body's x is unchanged, uniqueness is what the
# readout needs rather than magnitude, and at |x| ~ 12.7 an ulp is ~1e-6, so a
# 1e-3 step is ~1000 ulps clear. Measured by rdr-app: worst case 524 ulps, and
# +1 mm recovers as 0.9995 mm through a full round trip.
MARK_MM = 0.001
GROUP_RE = re.compile(r"NumSpawnPoints(\d)\s+(\d+)")
# The file is CRLF, so an `$`-anchored pattern must allow the \r -- without
# it every band parsed as EMPTY and the assertions below passed vacuously.
POS_RE = re.compile(r"^Position\t(\S+)\t(\S+)\t(\S+)[ \t]*\r?$", re.M)


def parse_groups(text):
    """[(group, [(x, y, z), ...]), ...] in file order.

    The declared counts are what slices the Position list into bands -- there
    is no per-point group marker in the file. `NumSpawnPoints1 2` means the
    next TWO Position lines are group 1.
    """
    counts = [(int(g), int(n)) for g, n in GROUP_RE.findall(text)]
    pts = [tuple(float(v) for v in m.groups()) for m in POS_RE.finditer(text)]
    out, i = [], 0
    for g, n in counts:
        out.append((g, pts[i:i + n]))
        i += n
    return out


def fmt_width(value, width):
    """`value` as a decimal string of EXACTLY `width` characters.

    A .spwn lives in a fixed archive slot, so a rewrite that grows the file by
    one byte is a different problem than the one being tested. Precision is
    reduced until it fits; a value that cannot fit at all raises rather than
    silently truncating.
    """
    for prec in range(9, -1, -1):
        s = "%.*f" % (prec, value)
        if len(s) == width:
            return s
    # Deliberately no padding fallback. Padding would splice spaces INSIDE the
    # line, between Y and Z, which parses but is not what the file looked like.
    raise ValueError("%r will not fit in %d characters" % (value, width))


def mark_points(text):
    """Nudge every point's x by a unique (i+1) mm. Returns (new_text, marks).

    `marks` is [(group, index_in_file, (x, y, z)), ...] for the MARKED values,
    so a single savestate naming one triple names its group too. That is why
    this marks the whole file rather than one band: the milestone's question
    is which GROUP the run read, and a mark that only covers group 1 cannot
    answer it -- a null would mean "not group 1" and nothing more.
    """
    bands = parse_groups(text)
    assert bands, "no spawn groups in this .spwn"
    ms = list(POS_RE.finditer(text))
    marks, out, i = [], text, 0
    for g, pts in bands:
        for _pt in pts:
            marks.append((g, i, None))
            i += 1
    assert len(marks) == len(ms), "group counts do not cover every Position"
    # Back to front, splicing the x FIELD by its own span: rebuilding the line
    # from a format string silently dropped the CRLF's \r and shrank the file.
    for idx in range(len(ms) - 1, -1, -1):
        m = ms[idx]
        x, y, z = (float(v) for v in m.groups())
        new_x = x + (idx + 1) * MARK_MM
        s, e = m.span(1)
        out = out[:s] + fmt_width(new_x, e - s) + out[e:]
        g = marks[idx][0]
        marks[idx] = (g, idx, (float(fmt_width(new_x, e - s)), y, z))
    return out, marks


def ee_memory(state_path):
    """The 32 MB EE RAM dump inside a PCSX2 .p2s (which is a zip)."""
    with zipfile.ZipFile(state_path) as z:
        return z.read("eeMemory.bin")


def moved_triples(mem_before, mem_after, before_xyz, after_xyz, tol=0.01):
    """Offsets reading `before_xyz` in one dump and `after_xyz` in the other.

    Kept for the stock/patched PAIR form. `marked_hits` is the better
    instrument and the one the boots actually produce; this remains because a
    pair is still the right tool if a level is ever measured without marks.
    """
    hits = []
    probe = struct.pack("<f", before_xyz[0])[2:]
    start = 0
    while True:
        i = mem_before.find(probe, start)
        if i < 0:
            break
        off = i - 2
        start = i + 1
        if off < 0 or off + 12 > len(mem_before) or off + 12 > len(mem_after):
            continue
        a = struct.unpack_from("<fff", mem_before, off)
        if any(abs(u - v) > tol for u, v in zip(a, before_xyz)):
            continue
        b = struct.unpack_from("<fff", mem_after, off)
        if all(abs(u - v) <= tol for u, v in zip(b, after_xyz)):
            hits.append((off, a, b))
    return hits


def marked_hits(mem, marks):
    """[(group, index, offset, triple)] for marked points resident in `mem`.

    EXACT float32 match, deliberately, and this is the property the whole
    measurement rests on. If the engine moves the body at all -- physics
    settling, a collision push, one frame of gravity -- the value stops
    matching and this returns NOTHING rather than the nearest point. A null is
    honest; a neighbour would be a plausible lie, which is the failure this
    milestone exists to rule out. Do not widen this into a tolerance.

    Read a null carefully: "used, then the body moved" and "never used" are
    indistinguishable from a null alone. The discriminator is unmarked
    coordinates resident with no marker -- see `unmarked_resident`.
    """
    hits = []
    for g, idx, xyz in marks:
        want = struct.pack("<fff", *xyz)
        start = 0
        while True:
            i = mem.find(want, start)
            if i < 0:
                break
            hits.append((g, idx, i, xyz))
            start = i + 1
    return hits


def unmarked_resident(mem, original_pts):
    """Original (unmarked) spawn triples resident in `mem`.

    The null discriminator. If no MARKED point is resident but an ORIGINAL one
    is, the game is reading a copy that did not come from the edited file --
    which is a finding about the winner, not a failed measurement.
    """
    out = []
    for xyz in original_pts:
        i = mem.find(struct.pack("<fff", *xyz))
        if i >= 0:
            out.append((i, xyz))
    return out


def _run():
    van = Game(config.VANILLA_ISO)

    print("1. the spawn file parses and its declared counts are honest:")
    d = van.archive(ARC)
    raw = d.read(d.find(LEVEL_SPWN))
    text = raw.decode("latin-1", "replace")
    counts = [int(m) for m in re.findall(r"NumSpawnPoints\d\s+(\d+)", text)]
    positions = len(re.findall(r"^Position", text, re.M))
    check(bool(counts), "NumSpawnPoints groups are present", str(counts))
    bands = parse_groups(text)          # `counts` is a list of ints, not pairs
    check(len(bands) == len(counts) and len(bands) > 0
          and all(len(pts) == n for (_g, pts), n in zip(bands, counts)),
          "and parse_groups slices the Position lines to match",
          "%s vs declared %s" % ([len(pts) for _g, pts in bands], counts))
    check(sum(counts) == positions,
          "declared counts equal the Position lines",
          "%d == %d" % (sum(counts), positions))

    nodes = spawn_nodes(van, ARC)
    check(len(nodes) == positions,
          "level.py sees every spawn the file declares",
          "%d nodes" % len(nodes))
    check(all(n.get("source") == LEVEL_SPWN for n in nodes),
          "and attributes them all to the .spwn")

    print()
    print("2. setting a spawn point, and proving the disc took it:")
    live = suite_is_live()
    if live is None:
        print("  [REFUSED] could not determine whether a suite is running.")
        print("            An undetermined answer is not a quiet machine.")
        FAILURES.append("could not establish that the disc is free")
        return 1
    if live and "--force" not in sys.argv:
        print("  [REFUSED] %d other Python process(es) are alive: %s"
              % (len(live), ", ".join("%s/%d" % p for p in live[:4])))
        print("            This section WRITES the working ISO. A write")
        print("            inside a suite's window contaminates that run,")
        print("            and the debris surfaces on the run AFTER it --")
        print("            so the lane that pays cannot see the cause.")
        print("            Wait for the machine to go quiet, or run under")
        print("            `flock /tmp/rdr-suite.lock`. `--force` overrides")
        print("            and you own the consequences.")
        FAILURES.append("a suite may be running; refused to write the disc")
        return 1
    mod = Game(config.MODDED_ISO)
    dm = mod.archive(ARC)
    _PRISTINE.append(dm.read(dm.find(LEVEL_SPWN)))
    start_text = _PRISTINE[0].decode("latin-1", "replace")
    check(start_text == text,
          "the working ISO starts from the vanilla .spwn",
          "%d bytes" % len(_PRISTINE[0]))

    edited, marks = mark_points(start_text)
    payload = edited.encode("latin-1")
    check(len(payload) == len(_PRISTINE[0]),
          "the edit keeps the file byte-length",
          "%d == %d" % (len(payload), len(_PRISTINE[0])))
    check(len(marks) > 0 and len({m[2] for m in marks}) == len(marks),
          "every point got a DISTINCT marker",
          "%d point(s), %d distinct" % (len(marks), len({m[2] for m in marks})))

    g = Game(config.MODDED_ISO, writable=True)
    try:
        g.archive(ARC).patch(LEVEL_SPWN, payload)
        g.iso.flush()
    finally:
        g.close()

    # Read back through a FRESH handle: a patch that returns without raising
    # is not evidence the bytes landed. M15's revert "worked" for weeks on a
    # read-only handle because nobody looked.
    back = Game(config.MODDED_ISO)
    db = back.archive(ARC)
    got = back.archive(ARC).read(db.find(LEVEL_SPWN))
    check(got == payload, "and the bytes are on the disc, read back fresh")
    on_disc = parse_marks(got.decode("latin-1", "replace"))
    check(len(on_disc) == len(marks) and len(marks) > 0
          and all(abs(d[2][0] - m[2][0]) < 1e-6
                  for d, m in zip(on_disc, marks)),
          "the markers on the disc are the ones asked for",
          "%d point(s)" % len(on_disc))
    # The markers are now demonstrably on disc. Anything a later section
    # reads must postdate this instant or it cannot contain them.
    import time as _time
    _MARKED_AT.append(_time.time())
    check(got != _PRISTINE[0],
          "and the disc actually differs from where it started",
          "%d byte(s) changed"
          % sum(1 for a, b in zip(got, _PRISTINE[0]) if a != b))

    print()
    print("3. the milestone's own question -- which file did the game load?")
    if WINNER is None:
        print("  [UNPROVEN] which file the game actually loads has not been")
        print("             measured. Set WINNER (with evidence) to enable")
        print("             this half.")
        FAILURES.append("winning file not yet measured")
        return 1

    check(bool(WINNER.get("evidence")),
          "the answer cites a measurement", WINNER.get("evidence", ""))
    check("index" not in WINNER,
          "the answer does NOT claim a fixed index",
          "selection within a group is rand % count")
    check(isinstance(WINNER.get("group"), int),
          "it names a measured group", str(WINNER.get("group")))

    single, pair = state_paths()
    if not (single or pair):
        print("  [UNPROVEN] no savestate to read. Either:")
        print("               --state FILE.p2s          one MARKED state")
        print("               --vanilla-state A --patched-state B")
        print("             The marked form is preferred and is what the")
        print("             boots produce: 24 unique millimetre offsets mean")
        print("             one state names which point was drawn.")
        FAILURES.append("no savestate to read")
        return 1

    if single:
        mem = ee_memory(single)
        check(len(mem) > 16 * 1024 * 1024,
              "the savestate carries a full EE RAM dump", "%d bytes" % len(mem))
        hits = marked_hits(mem, marks)
        originals = [pt for _g, pts in parse_groups(start_text) for pt in pts]
        stale = unmarked_resident(mem, originals)
        check(bool(hits),
              "a MARKED spawn point is resident in RAM",
              "%d hit(s)" % len(hits))
        if not hits:
            # The null is not automatically a negative result about the file.
            print("             A null here does NOT mean the .spwn was")
            print("             unused: 'used, then the body moved' and")
            print("             'never used' look identical from a null.")
            if stale:
                print("             BUT %d UNMARKED original(s) are resident"
                      % len(stale))
                print("             -- the game is reading a copy that did")
                print("             not come from the edited file. That is a")
                print("             finding about the winner, not a failure.")
            FAILURES.append("no marked point resident")
            return 1
        groups_seen = sorted({h[0] for h in hits})
        check(groups_seen == [WINNER["group"]],
              "the resident point belongs to the measured group",
              "saw group(s) %s, WINNER says %d" % (groups_seen, WINNER["group"]))
        return 1 if FAILURES else 0

    a_path, b_path = pair
    mem_a, mem_b = ee_memory(a_path), ee_memory(b_path)
    check(len(mem_a) == len(mem_b) and len(mem_a) > 16 * 1024 * 1024,
          "both savestates carry a full EE RAM dump",
          "%d bytes each" % len(mem_a))
    originals = [pt for _g, pts in parse_groups(start_text) for pt in pts]
    hits = []
    for (g_, _i, after), before in zip(marks, originals):
        hits += [(g_,) + h for h in moved_triples(mem_a, mem_b, before, after)]
    check(bool(hits), "a position in RAM moved with the edit",
          "%d location(s)" % len(hits))
    if not hits:
        return 1
    groups_seen = sorted({h[0] for h in hits})
    check(groups_seen == [WINNER["group"]],
          "the moved point belongs to the measured group",
          "saw group(s) %s, WINNER says %d" % (groups_seen, WINNER["group"]))
    return 1 if FAILURES else 0


def parse_marks(text):
    """[(group, index, (x, y, z))] as the file currently reads."""
    out, i = [], 0
    for g, pts in parse_groups(text):
        for pt in pts:
            out.append((g, i, pt))
            i += 1
    return out


def state_paths():
    """(single_marked_state, (vanilla, patched)) -- whichever was supplied."""
    args = sys.argv
    def opt(name):
        return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else None
    single = opt("--state")
    a, b = opt("--vanilla-state"), opt("--patched-state")
    for f in (single, a, b):
        _refuse_if_stale(f)
    if single:
        return single, None
    if a and b:
        return None, (a, b)
    d = os.path.join(os.path.dirname(_HERE), "..", "07_Research", "play_test")
    marked = os.path.join(d, "m20_marked.p2s")
    if os.path.exists(marked):
        return marked, None
    van, pat = (os.path.join(d, "m20_vanilla.p2s"),
                os.path.join(d, "m20_patched.p2s"))
    if os.path.exists(van) and os.path.exists(pat):
        return None, (van, pat)
    return None, None


def _refuse_if_stale(path):
    """A savestate older than the markers it is searched for proves nothing.

    Found the hard way: this file marks the .spwn itself in section 2, and I
    then pointed section 3 at a savestate captured during an EARLIER boot. It
    reported 0 hits. That null is not evidence the .spwn went unused -- the
    state simply predates the markers being searched for, and there was never
    anything to find.

    The failure is dangerous because the output is identical to a real null,
    and a real null on this test would be a genuine finding about the engine.
    So the ordering is checked rather than trusted: **the state must be newer
    than the .spwn write it is supposed to reflect.**
    """
    if not path or not os.path.exists(path):
        return
    state_mtime = os.path.getmtime(path)
    iso = getattr(config, "MODDED_ISO", None)
    if not iso or not os.path.exists(iso):
        return
    # Compare against THIS RUN's marking write, not a guessed threshold.
    # The first version allowed an hour of slack, picked to dodge false
    # positives -- and a state 3.5 minutes stale sailed straight under it.
    # A generous bound on a check like this is the same defect as no check:
    # it passes the case it exists to catch. _MARKED_AT is set by section 2
    # the moment the markers land; before that there is nothing to be stale
    # relative to, so the guard correctly stays quiet.
    if not _MARKED_AT:
        return
    if state_mtime < _MARKED_AT[0]:
        import time
        raise SystemExit(
            "REFUSING a stale savestate.\n"
            "  state  %s\n         captured %s\n"
            "  but this run laid its markers down at %s, LATER.\n\n"
            "  A state older than the markers it is searched for yields 0\n"
            "  hits for the same reason an empty search does, and the two\n"
            "  are indistinguishable in the output. Capture a state from a\n"
            "  boot taken AFTER the marking, or pass it explicitly knowing\n"
            "  this." % (path, time.ctime(state_mtime),
                         time.ctime(_MARKED_AT[0])))


def selftest():
    """Prove the difference search can say YES, against synthetic memory.

    suite-run-hygiene point 7, turned on this file: *a guard that can only
    ever say "no" has not been tested*. Until savestates exist, section 3 can
    only ever report UNPROVEN, so the day the real dumps arrive a "nothing
    moved" result would be indistinguishable from a search that never worked.
    This runs the search against memory where the answer is known.

        python3 scripts/test_spawn_point.py --selftest
    """
    before = (-24.519470, 0.799992, 11.484390)
    after = (-24.519470, 40.799992, 11.484390)
    noise = struct.pack("<f", 1.25) * 4096
    at = 1024
    mem_a = bytearray(noise)
    mem_b = bytearray(noise)
    mem_a[at:at + 12] = struct.pack("<fff", *before)
    mem_b[at:at + 12] = struct.pack("<fff", *after)
    # A decoy that holds the BEFORE value in both dumps: a copy that did not
    # move is exactly what an absolute search would wrongly accept.
    mem_a[2048:2060] = struct.pack("<fff", *before)
    mem_b[2048:2060] = struct.pack("<fff", *before)

    hits = moved_triples(bytes(mem_a), bytes(mem_b), before, after)
    check(len(hits) == 1, "the search finds the location that moved",
          "%d hit(s)" % len(hits))
    check(bool(hits) and hits[0][0] == at,
          "at the right offset", hits[0][0] if hits else "-")
    check(all(h[0] != 2048 for h in hits),
          "and rejects the copy that did NOT move",
          "a difference test, not an absolute one")
    check(not moved_triples(bytes(mem_a), bytes(mem_a), before, after),
          "two identical dumps yield nothing")

    # --- the MARKED form, which is the instrument the boots actually produce
    print()
    print("  marked-state form (the primary instrument):")
    marks = [(1, 0, (-24.518470, 0.799992, 11.484390)),      # group 1, +1 mm
             (1, 1, (10.545156, 1.586441, 5.034636)),        # group 1, +2 mm
             (2, 2, (3.709349, 0.000000, 33.056938))]        # group 2, +3 mm
    originals = [(-24.519470, 0.799992, 11.484390),
                 (10.544156, 1.586441, 5.034636),
                 (3.706349, 0.000000, 33.056938)]

    mem = bytearray(noise)
    mem[512:524] = struct.pack("<fff", *marks[1][2])          # group 1 drawn
    hits = marked_hits(bytes(mem), marks)
    check(len(hits) == 1 and hits[0][0] == 1 and hits[0][1] == 1,
          "one marked point resident -> names its point AND its group",
          "group %s, index %s" % (hits[0][0], hits[0][1]) if hits else "-")
    check(not unmarked_resident(bytes(mem), originals),
          "and no unmarked original is resident")

    # The null that is NOT a negative about the file: the game holds a copy
    # that never came from the edited .spwn.
    mem2 = bytearray(noise)
    mem2[512:524] = struct.pack("<fff", *originals[1])
    check(not marked_hits(bytes(mem2), marks),
          "an UNMARKED copy produces a null, not a nearest-neighbour",
          "exact match, deliberately")
    check(len(unmarked_resident(bytes(mem2), originals)) == 1,
          "and the null discriminator sees the unmarked original",
          "'used then moved' vs 'never used' is decided here")

    # A body nudged off the mark must vanish rather than decode as a neighbour.
    mem3 = bytearray(noise)
    drifted = (marks[1][2][0] + 0.02, marks[1][2][1], marks[1][2][2])
    mem3[512:524] = struct.pack("<fff", *drifted)
    check(not marked_hits(bytes(mem3), marks),
          "a point the engine MOVED reads as silence, not as its neighbour",
          "the failure mode this milestone exists to rule out")
    return 1 if FAILURES else 0


def main():
    resolve_level()
    print("level: %s  (%s :: %s)" % (LEVEL, ARC, LEVEL_SPWN))
    try:
        return _run()
    finally:
        if _PRISTINE and not _RESTORED:
            print()
            print("restore (after an aborted run):")
            restore()


if __name__ == "__main__":
    rc = selftest() if "--selftest" in sys.argv else main()
    print()
    print("SUMMARY")
    if FAILURES:
        print("  [FAIL] %s: %s"
              % ("selftest" if "--selftest" in sys.argv else "M20 is NOT proven",
                 "; ".join(FAILURES[:3])))
    elif "--selftest" in sys.argv:
        print("  [PASS] the difference search works; section 3 can say yes")
    sys.exit(rc)
