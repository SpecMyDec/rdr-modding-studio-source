"""Behaviour tables end to end: bindings, numbers, and a cloned table.

Three claims, checked against a throwaway disc and then against the emulator:

  1. A `.tbl` binding can be repointed at another `.stateb` in the same
     archive, byte-for-byte in place, and undone exactly.
  2. A float parameter inside a `.stateb` can be retuned. Four bytes for four,
     so the file length never moves and no slot can overflow.
  3. A `.tbl` can be CLONED to a new name via `dave.add`, the new file reads
     back, and the archive still parses with every original byte-identical.

Then it boots the disc for 185 seconds and JUDGES BY MOTION. A frozen disc is
not a black screen, it is a good-looking title screen that stops updating: six
late frames hashed, 3+ distinct means it is running, 1 means it is dead. The
opening cinematic alone runs to about 100 s, so a shorter run proves nothing.

    python scripts/verify_behaviour_edit.py
    python scripts/verify_behaviour_edit.py --no-boot      offline checks only
    python scripts/verify_behaviour_edit.py --boot-only    play the disc as it is

Writes ONLY to 02_Modded/RDR_REPACKTEST.iso, restored from the pristine disc
at the start of every run. The routes are driven as functions rather than over
HTTP for exactly that reason: the dev server writes to RDR_MODDED.iso, which
is the developer's working disc.

ONE THROWAWAY DISC, SEVERAL AGENTS. On this machine that ISO is shared, every
one of them starts by copying 2.34 GB of pristine disc over it, and Python's
`open` takes no lock -- so a run can lose the file underneath itself. Hence
three things that would be pointless on a quieter machine: the retry in
`run()`, the re-read of the edits AFTER the emulator has closed, and
`keep_disc_free`, which asks every Studio on the box to drop its writable
handle so PCSX2 can take the file at all.
"""

import argparse
import glob
import hashlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rdrstudio.config import ROOT, VANILLA_ISO   # noqa: E402

# Every route below is gated on a feature that is internal-only, so the build
# has to say so before the dispatch table will carry them.
os.environ["RDR_BUILD"] = "internal"

VANILLA = Path(VANILLA_ISO)
TEST_ISO = Path(ROOT) / "02_Modded" / "RDR_REPACKTEST.iso"
SHOTS = Path(ROOT) / "07_Research" / "play_test"

ARCHIVE = "LVLS_SNG/SHER70.DAT"
TABLE = "sher70_gigolobuddy.tbl"
CLONE = "sher70_behtest"

FAILURES = []


def say(text):
    """Print without assuming the console can encode what a route returned.

    Undo labels carry an em dash and an arrow, as the contract specifies, and a
    Windows console is cp1252 and raises on both. The server only ever puts
    them in JSON, which escapes them, so this is the test's problem alone.
    """
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        text.encode(enc)
    except (UnicodeEncodeError, LookupError):
        text = text.encode(enc, "backslashreplace").decode(enc, "replace")
    print(text)


def check(ok, what, detail=""):
    say(("  ok   " if ok else "  FAIL ") + what + (("  -- " + detail) if detail else ""))
    if not ok:
        FAILURES.append(what)
    return ok


def studio_ports():
    """Every port on this machine that answers as a Studio. Cheap, no writes."""
    import socket
    import urllib.request
    live = []
    for port in range(8730, 8800):
        s = socket.socket()
        s.settimeout(0.15)
        try:
            s.connect(("127.0.0.1", port))
        except OSError:
            continue
        finally:
            s.close()
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/api/config" % port,
                                   timeout=4).read()
            live.append(port)
        except Exception:
            continue
    return live


def keep_disc_free(ports, seconds):
    """Hold every Studio off the ISO for `seconds`, in a background thread.

    PCSX2 opens the CDVD about two seconds after launch and does not retry:
    *measured* from its own log, `Failed to open CDVD 'RDR_REPACKTEST.iso':
    errno 13: Permission denied.` A Studio's writable handle is what causes
    that, and `/api/release` (`hand_over()`) is the designed answer -- but
    `ensure_open(force=True)` runs on every POST, so any other Studio being
    driven at the same moment takes the disc straight back. On a machine
    running one Studio that race does not exist. On this one it does: several
    are pointed at the same throwaway disc at once.

    So: ask repeatedly for the first few seconds rather than once. As soon as
    PCSX2 has the file, `free_for_emulator()` returns False everywhere and
    every Studio stays out on its own.

    ONE PORT PER THREAD, and that is not a detail. The first version swept the
    ports in a loop, and one Studio that was busy inside a long disc operation
    never answered -- so every pass paid its full timeout and the intended
    0.4-second cadence became three seconds. PCSX2 asked for the disc in one of
    those gaps and the whole run captured nothing, twice.
    """
    import threading
    import urllib.request

    def release(port, timeout):
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:%d/api/release" % port, data=b"{}",
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=timeout).read()
            return True
        except Exception:
            return False

    # GENEROUS TIMEOUTS, AND WAIT FOR THE ANSWER. The version before this used
    # one second, and a Studio busy inside a disc operation took 3.7 -- so
    # every attempt aborted before the handle was dropped, and PCSX2 asked for
    # the file 1.26 seconds after launch and was refused. Three runs captured
    # nothing that way. Measured, not guessed: the same call answers
    # `{"ok": true}` in 3.7 s when it is allowed to finish.
    for port in ports:
        print("    release %d: %s" % (port, "ok" if release(port, 60)
                                      else "no answer"))
    for _ in range(30):
        try:
            open(TEST_ISO, "r+b").close()
            print("    the disc is free")
            break
        except OSError:
            time.sleep(1)
    else:
        print("    WARNING: something still holds the disc; PCSX2 will "
              "refuse it")

    def poke(port):
        stop = time.time() + seconds
        while time.time() < stop:
            release(port, 20)
            time.sleep(2.0)

    threads = [threading.Thread(target=poke, args=(p,), daemon=True)
               for p in ports]
    for t in threads:
        t.start()
    return threads


def play(seconds, expect_start=None):
    """Boot the test disc and judge it BY MOTION, not by screenshot size.

    A frozen disc is not a black screen; it is a perfectly good title screen
    that stops updating. Six late frames hashed: 3+ distinct means the game is
    running, 1 means it is dead. The opening cinematic alone runs to about
    100 seconds, so anything shorter proves nothing at all.
    """
    from rdrstudio import behaviour as B
    from rdrstudio.game import Game

    print("\nPLAYING THE DISC (%.0fs, judged by motion)" % seconds)
    ports = studio_ports()
    print("  %d Studio(s) answering: %s"
          % (len(ports), ", ".join(str(p) for p in ports) or "none"))
    keep_disc_free(ports, 90.0)

    # `play_test.py` finds the emulator by window title and takes the FIRST
    # match. With more than one PCSX2 alive -- and on this machine there have
    # been three at once -- the frames it hashes can belong to somebody else's
    # disc, which would make a green result meaningless. Counted and said out
    # loud rather than assumed away.
    others = subprocess.run(["tasklist", "/FI", "IMAGENAME eq pcsx2-qt.exe"],
                            capture_output=True, text=True)
    running = others.stdout.count("pcsx2-qt.exe")
    if running:
        print("  WARNING: %d PCSX2 already running. Screenshots are matched by "
              "window title and may not be this run's." % running)
    SHOTS.mkdir(parents=True, exist_ok=True)
    seen = set(glob.glob(str(SHOTS / "*.png")))
    subprocess.run([sys.executable, str(HERE / "play_test.py"),
                    "--level", "--seconds", str(seconds),
                    "--iso", str(TEST_ISO)],
                   cwd=str(HERE.parent), capture_output=True, text=True)
    fresh = [f for f in sorted(glob.glob(str(SHOTS / "*.png")),
                               key=os.path.getmtime) if f not in seen][-6:]
    if len(fresh) < 6:
        check(False, "the run produced six late frames",
              "%d captured -- see PCSX2's emulog for why" % len(fresh))
    else:
        hs = []
        for f in fresh:
            h = hashlib.sha256(open(f, "rb").read()).hexdigest()[:12]
            hs.append(h)
            print("   %-26s %s" % (os.path.basename(f), h))
        n = len(set(hs))
        check(n >= 3, "the game is still MOVING after the edits",
              "%d distinct late frames (1 = frozen)" % n)

    # Nine agents share one throwaway disc on this machine and Python's open()
    # does not lock it, so "the disc I booted is the disc I wrote" has to be
    # re-established afterwards or the motion result belongs to someone else's
    # edits. Five runs on 2026-08-05 were lost exactly this way -- three to the
    # disc being replaced mid-run, two to another Studio's writable handle
    # making PCSX2 refuse it with errno 13.
    #
    # A lost disc is VOID, not FAILED. Recording it as a failure would say the
    # edits broke the game, and what actually happened is that the game never
    # read them.
    try:
        g3 = Game(str(TEST_ISO))
        still = B.table_doc(g3.archive(ARCHIVE), CLONE + ".tbl")
        g3.close()
    except (KeyError, OSError) as ex:
        check(False, "the booted disc still carried this run's edits",
              "VOID -- the shared disc was replaced during the run (%s). This "
              "says nothing about the edits." % ex)
        return
    bound = next((x["value"] for x in still["states"]
                  if x["key"] == "StartState"), None)
    if expect_start is None:
        say("  note  booted disc has %s StartState = %s" % (CLONE, bound))
        return
    check(bound == expect_start,
          "the booted disc still carries this run's edits",
          "%s StartState = %s" % (CLONE, bound))


def fingerprint(game, archive):
    dave = game.archive(archive)
    out = {}
    for e in dave:
        try:
            out[e.name] = hashlib.sha1(dave.read(e)).hexdigest()
        except Exception as ex:
            out[e.name] = "ERROR %s" % ex
    return out


def boot_only(seconds):
    """Play the disc EXACTLY as the last full run left it.

    The offline half writes a clone, a rebind and a retune and then verifies
    them; if it has just done that and the boot is the only thing left to
    establish, copying 2.34 GB again to reach the same state is 40 seconds of
    the shared disc that another agent will spend the same 40 seconds wanting.
    The edits are re-read here first, so this refuses rather than booting a
    disc that no longer carries them.
    """
    from rdrstudio import behaviour as B
    from rdrstudio.game import Game

    g = Game(str(TEST_ISO))
    doc = B.table_doc(g.archive(ARCHIVE), CLONE + ".tbl")
    g.close()
    say("  %s is on the disc: %s"
        % (CLONE + ".tbl",
           ", ".join("%s=%s" % (r["key"], r["value"]) for r in doc["states"][:2])))
    return play(seconds)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-boot", action="store_true")
    ap.add_argument("--boot-only", action="store_true",
                    help="skip the offline half and play the disc as it is")
    ap.add_argument("--seconds", type=float, default=185.0)
    args = ap.parse_args()

    from rdrstudio import api, behaviour as B, routes2 as R
    from rdrstudio.game import Game
    from rdrstudio.studio import Studio

    if args.boot_only:
        boot_only(args.seconds)
        print("\n" + "-" * 68)
        return 1 if FAILURES else 0

    print("restoring a pristine test disc ...")
    if TEST_ISO.exists():
        os.chmod(TEST_ISO, 0o666)
    shutil.copyfile(VANILLA, TEST_ISO)
    os.chmod(TEST_ISO, 0o666)

    print("\nbaseline: fingerprinting %s from the pristine disc" % ARCHIVE)
    van = Game(str(VANILLA))
    before = fingerprint(van, ARCHIVE)
    print("  %d entries" % len(before))

    s = Studio(str(VANILLA), str(TEST_ISO))
    # The session document belongs to whoever is using the Studio, not to a
    # test. Journalling still happens in memory, so undo is exercised for real.
    s.journal.autosave_path = None

    def get(fn, **q):
        return fn(None, s, {k: [v] for k, v in q.items()})

    def post(fn, **body):
        return fn(None, s, body)

    def refused(fn, code, **body):
        try:
            post(fn, **body)
            return False, "it was accepted"
        except api.Refusal as ex:
            return ex.code == code, "%s: %s" % (ex.code, ex.message)

    # ------------------------------------------------------------- reading
    print("\nREADING")
    r = get(R.get_behaviour_tables, archive=ARCHIVE)
    check(r["ok"] and r["total"] == 20,
          "SHER70 lists its 20 behaviour tables", "%d tables, %d state files"
          % (r["total"], r["states"]))

    doc = get(R.get_behaviour_table, archive=ARCHIVE, name=TABLE)
    check(doc["ok"] and len(doc["states"]) == 9,
          "%s binds 9 slots" % TABLE, "%d" % len(doc["states"]))
    check(all(x["resolves"] for x in doc["states"]),
          "every binding names a .stateb that is in this archive")
    check(len(doc["palette"]) == 57 and doc["palette"] == sorted(doc["palette"]),
          "the palette is every .stateb stem in the archive, sorted",
          "%d options" % len(doc["palette"]))
    start = next(x for x in doc["states"] if x["key"] == "StartState")
    say("       StartState = %s  (slot %d, %d stored, %d spare)"
          % (start["value"], doc["slot"], doc["stored"], doc["headroom"]))

    st = get(R.get_behaviour_state, archive=ARCHIVE, name=start["value"])
    check(st["ok"] and st["counts"]["floats"] > 0,
          "the state file it points at has float parameters",
          "%d floats, %d ints, %d named conditions"
          % (st["counts"]["floats"], st["counts"]["ints"],
             st["counts"]["names"]))
    check(st["rung"] == "untested",
          "the numbers ship at rung untested, because nothing names them")

    # ------------------------------------------------- 1. rebind a state
    print("\n1  REBIND A STATE")
    target = next(p for p in doc["palette"]
                  if p.lower() != start["value"].lower())
    plan = post(R.post_behaviour_table_set, archive=ARCHIVE, name=TABLE,
                changes={"StartState": target})
    check(plan["ok"] and not plan.get("applied"),
          "a rebind dry-runs by default and writes nothing")
    w = plan["plan"]["writes"][0]
    check(plan["plan"]["undo"]["steps"] == 1 and w["fits"],
          "one undo step, and it fits", "%d of %d bytes stored, %d spare"
          % (w["stored"], w["slot"], w["headroom"]))
    say("       %s" % plan["plan"]["summary"])

    ok, why = refused(R.post_behaviour_table_set, "unknown_value",
                      archive=ARCHIVE, name=TABLE,
                      changes={"StartState": "no_such_state"}, dry=True)
    check(ok, "an invented state name is REFUSED, never written", why)
    ok, why = refused(R.post_behaviour_table_set, "not_editable",
                      archive=ARCHIVE, name=TABLE,
                      changes={"DeadState": target}, dry=True)
    check(ok, "a slot this table does not bind is refused with the list", why)

    n0 = len(s.journal.entries)
    res = post(R.post_behaviour_table_set, archive=ARCHIVE, name=TABLE,
               changes={"StartState": target}, dry=False)
    check(res.get("applied"), "the rebind applies", res.get("group"))
    check(len(s.journal.entries) - n0 == res["copies"],
          "every copy is journalled under ONE group",
          "%d file(s), 1 group" % (len(s.journal.entries) - n0))

    g2 = Game(str(TEST_ISO))
    live = B.table_doc(g2.archive(ARCHIVE), TABLE)
    g2.close()
    got = next(x for x in live["states"] if x["key"] == "StartState")["value"]
    check(got == target, "it reads back off the test disc",
          "%s -> %s" % (start["value"], got))

    s.undo()
    g2 = Game(str(TEST_ISO))
    after = fingerprint(g2, ARCHIVE)
    g2.close()
    check(after.get(TABLE) == before.get(TABLE),
          "one Ctrl+Z puts the whole thing back, byte for byte")

    # ------------------------------------------- 2. retune a float parameter
    print("\n2  RETUNE A NUMBER")
    num = st["numbers"][0]
    say("       byte %d holds %g, %s"
          % (num["off"], num["value"],
             ("nearest name before it is %r" % num["near"]) if num["near"]
             else "no name token near it"))
    plan = post(R.post_behaviour_state_set, archive=ARCHIVE,
                name=start["value"], changes={num["off"]: num["value"] + 1.0})
    w = plan["plan"]["writes"][0]
    check(plan["ok"] and w["bytes_after"] == w["bytes_before"],
          "a number edit is length-neutral, so it cannot overflow a slot",
          "%d -> %d bytes" % (w["bytes_before"], w["bytes_after"]))

    ok, why = refused(R.post_behaviour_state_set, "not_editable",
                      archive=ARCHIVE, name=start["value"],
                      changes={5: 1.0}, dry=True)
    check(ok, "an offset that is not one of this file's floats is refused", why)

    res = post(R.post_behaviour_state_set, archive=ARCHIVE, name=start["value"],
               changes={num["off"]: num["value"] + 1.0}, dry=False)
    check(res.get("applied"), "the number edit applies",
          "%d cop%s" % (res["copies"], "y" if res["copies"] == 1 else "ies"))
    g2 = Game(str(TEST_ISO))
    live = B.numbers(g2.archive(ARCHIVE).read(start["value"] + ".stateb"))
    g2.close()
    check(abs(live["numbers"][0]["value"] - (num["value"] + 1.0)) < 1e-4,
          "it reads back off the test disc",
          "%g -> %g" % (num["value"], live["numbers"][0]["value"]))
    s.undo()

    # --------------------------------------------------- 3. clone a table
    print("\n3  CLONE A TABLE")
    ok, why = refused(R.post_behaviour_table_clone, "bad_request",
                      archive=ARCHIVE, source=TABLE, name="Bad Name!",
                      dry=True)
    check(ok, "a name the archive's 6-bit alphabet cannot spell is refused",
          why)
    ok, why = refused(R.post_behaviour_table_clone, "bad_request",
                      archive=ARCHIVE, source=TABLE, name=TABLE[:-4], dry=True)
    check(ok, "a name that is already in the archive is refused", why)

    plan = post(R.post_behaviour_table_clone, archive=ARCHIVE, source=TABLE,
                name=CLONE)
    check(plan["ok"] and not plan.get("applied") and plan["plan"]["fits"],
          "a clone dry-runs and fits", plan["plan"]["summary"])
    check(plan["plan"]["undo"]["steps"] == 0 and plan["reversible"] is False,
          "and it says out loud that it CANNOT be undone")
    say("       %s" % plan["note"])

    res = post(R.post_behaviour_table_clone, archive=ARCHIVE, source=TABLE,
               name=CLONE, dry=False)
    check(res.get("applied") and res.get("verified"),
          "the clone is written and reads back identical to its source",
          "%s at 0x%X, entry %d of %d"
          % (res["file"], res["offset"], res["index"], res["entries"]))
    check(res["states"] == 9, "the new table parses as 9 bindings",
          "%d" % res["states"])

    # Point a real enemy at the new table, so the disc that gets booted
    # actually names it. A file nothing references would boot whatever it
    # contained.
    lvl = s.level(ARCHIVE)
    enemy = next((n for n in lvl.enemy_nodes()
                  if str(n.props.get("Table", "")).lower()
                  == TABLE[:-4].lower()), None)
    if enemy is not None:
        from rdrstudio import cast as C
        cd = C.cast(s.game, ARCHIVE)
        check(C.resolve_table(cd, CLONE) is not None,
              "the new table shows up in this archive's cast palette")
        edits = lvl.set_enemy_fields(enemy.uid, {"Table": CLONE})
        out = lvl.apply(edits, dry=False,
                        writer=s.writer_for(ARCHIVE, "point at the clone"))
        check(out.get("applied"), "an enemy is repointed at the new table",
              "%s -> %s" % (enemy.props.get("Name"), CLONE))
    else:
        say("  note  no enemy in this level uses %s; the clone is unreferenced"
              % TABLE)

    # ------------------------------------------------- leave all three on disc
    #
    # The undo checks above put the first two edits back, which is the point of
    # them -- but it also means the disc about to be booted would only carry
    # the clone. Re-apply a rebind and a retune, on the files the repointed
    # enemy actually reaches, so one 185-second run covers all three claims.
    print("\nWHAT THE BOOTED DISC CARRIES")
    res = post(R.post_behaviour_table_set, archive=ARCHIVE, name=CLONE,
               changes={"StartState": target}, dry=False)
    check(res.get("applied"), "the cloned table is rebound to %s" % target)
    tgt_nums = B.numbers(s.game.archive(ARCHIVE).read(target + ".stateb"))
    if tgt_nums["numbers"]:
        off = tgt_nums["numbers"][0]["off"]
        was = tgt_nums["numbers"][0]["value"]
        res = post(R.post_behaviour_state_set, archive=ARCHIVE, name=target,
                   changes={off: was * 2.0 if was else 1.0}, dry=False)
        check(res.get("applied"), "and a number inside %s is retuned" % target,
              "byte %d: %g -> %g" % (off, was, was * 2.0 if was else 1.0))
    else:
        say("  note  %s holds no float parameters to retune" % target)

    # ----------------------------------------------- the archive is still sane
    print("\nTHE ARCHIVE AFTERWARDS")
    s.close()
    g2 = Game(str(TEST_ISO))
    dave = g2.archive(ARCHIVE)
    names = [e.name for e in dave]
    check(len(names) == len(before) + 1, "exactly one entry was added",
          "%d -> %d" % (len(before), len(names)))
    check(names == sorted(names), "the table of contents is still name-sorted")
    after = fingerprint(g2, ARCHIVE)
    lost = [n for n in before if n not in after]
    # The files this run deliberately changed are excluded by name; every other
    # original has to come back byte-identical or the add moved data.
    touched = {CLONE + ".tbl", "lvl_sher70_var_1.enm", target + ".stateb"}
    changed = [n for n in before
               if n in after and after[n] != before[n] and n not in touched]
    check(not lost, "no original file vanished", ", ".join(lost[:5]))
    check(not changed, "no untouched original changed", ", ".join(changed[:5]))
    clone_doc = B.table_doc(dave, CLONE + ".tbl")
    check(len(clone_doc["states"]) == 9
          and all(x["resolves"] for x in clone_doc["states"]),
          "the new table re-reads as 9 bindings that all resolve")
    g2.close()

    if args.no_boot:
        print("\n(--no-boot: nothing was played)")
        return 1 if FAILURES else 0

    play(args.seconds, expect_start=target)

    print("\n" + "-" * 68)
    if FAILURES:
        print("%d FAILED:" % len(FAILURES))
        for f in FAILURES:
            print("   - " + f)
        return 1
    print("all behaviour-table checks passed")
    print("What a green motion result DOES show: the disc still boots and the")
    print("game keeps drawing with a cloned .tbl, a rebound state and a")
    print("retuned number on it. What it does NOT show: that the enemy behaves")
    print("any differently. Read the captured frames before claiming more --")
    print("the key script aims at mission 3 and does not always arrive.")
    return 0


def run():
    """Retry a run that the shared disc pulled out from under it.

    Nine agents on this machine share ONE throwaway ISO, and each of them
    starts by copying the 2.34 GB pristine disc over it. A read that lands
    mid-copy fails with `short read at 0x8000: wanted 2048 got 0` -- the ISO's
    primary volume descriptor, gone because the file is momentarily empty.
    That is not a defect in anything under test, and it should cost a retry
    rather than a result. Three attempts, then say so plainly.
    """
    for attempt in range(1, 4):
        del FAILURES[:]
        try:
            return main()
        except OSError as ex:
            if "short read" not in str(ex):
                raise
            print("\nattempt %d lost the disc mid-run (%s)." % (attempt, ex))
            print("Another agent is copying over the shared throwaway ISO. "
                  "Starting again.")
    print("\nThree attempts, three losses of the shared disc. NOT a result: "
          "the boot test did not run.")
    return 1


if __name__ == "__main__":
    sys.exit(run())
