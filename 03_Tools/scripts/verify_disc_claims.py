"""Does the disc DO what its Options page says it does?

    py -3 03_Tools/scripts/verify_disc_claims.py <disc.iso> [--quick]

`frontmenu/verify_modlist_row.py` ([M23]) asserts that three strings are
printed on two Options pages and that the soft-lock is not on the disc. That is
all it can see. It cannot tell whether the disc behind those strings carries a
60 roster or an invulnerable Red -- so a disc advertising "God Mode" with the
god-mode edit missing passes [M23] 19/19. The page is the only thing telling
The developer what he is running, which makes that the worst failure available here:
worse than advertising nothing.

This is the other half. It reads the two claims that are mechanical and checks
them against values fixed IN THIS FILE, never against the disc:

    60 Scattered Enemies   lvl_sher70_var_1.enm carries 60 enemy blocks, and
                           its bytes match the disc the developer has already played
    God Mode               srd.cr HealthData.Invulnerable 1 / HitDamage 0.0,
                           in all 57 archives that hold srd.cr

The third name, "Mod List menu", is self-referential -- it is true iff the two
Options pages carry the block, which is exactly what [M23] already asserts.
Nothing here re-checks it.

WHY THE EXPECTATIONS ARE LITERALS. `verify_modlist_row.py` took its expected
names off the disc under test and was green on a page nobody chose
(CHECKS-THAT-LIE entry 21). The numbers below come from the boot evidence, not
from any image: 60 is the measured ceiling (90 hangs, 120 and 240 overrun the
allocator -- REVIEWER-LOG 2026-09-19T02:1x), and Invulnerable/HitDamage is the
lever rdr-fmt root-caused in `srd.cr` after patching `srd.dam` and `srd.msd`
and shipping a god-mode disc that did nothing.

THE NEGATIVE CONTROLS ARE NOT OPTIONAL. Both claims are "a field has a
value", and a reader that cannot find the field, or a parser that returns a
default, fails in the green direction. So every run also reads a disc that must
NOT satisfy each claim -- vanilla for the roster, and a disc known to have no
god mode for the health fields -- and FAILS if the control looks patched. A
check that has never been seen to go red has not been seen to work.

Read-only against every image it opens, so it takes no lock, and it never
writes. Not registered in `run_tests.py`: it wants a disc argument naming a
specific build, which is not a thing a suite row can supply.
"""

import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "03_Tools"))

from rdrstudio import creature, scene                        # noqa: E402
from rdrstudio.game import Game                              # noqa: E402
from rdrstudio.level import Level                            # noqa: E402

VANILLA = os.path.join(ROOT, "01_Vanilla", "RDR_VANILLA.iso")

# The placement reference: the disc the developer has played, whose 60-enemy scatter
# reached the street in 60s and whose god mode was proven against a paired
# control. A rebuild is meant to carry this file across untouched.
PLACEMENT_REF = os.environ.get("RDR_PLACEMENT_REF", "")

LEVEL = "LVLS_SNG/SHER70.DAT"
ENM = "lvl_sher70_var_1.enm"
CREATURE = "srd.cr"

# EXPECTATIONS -- literals, and the whole point of this file. See the docstring.
ROSTER = 60
ROSTER_VANILLA = 19
CREATURE_ARCHIVES = 57
GOD = {"HealthData.Invulnerable": 1.0, "HealthData.HitDamage": 0.0}
NOT_GOD = {"HealthData.Invulnerable": 0.0, "HealthData.HitDamage": 1.0}

# A clone that is not named `<Original>_xN` never spawns -- the name is
# resolved, not a free label, which is what made RDR_CROWD_060_MARKED show 3
# bodies for a roster of 60.
#
# The `#1` is an instance suffix the level carries on EVERY name, vanilla ones
# included (`CameraGuy#1`, `CameraGuy_x1#1`). Written without the strip, this
# regex matched nothing, reported "60 un-cloned names" and failed a disc whose
# enemy file is byte-identical to the one the developer has played. A false red from
# an expectation stated slightly wrong -- which is the direction to fail in,
# but it was the check that was wrong and not the disc.
CLONE = re.compile(r"^(?P<base>.+)_x(?P<n>\d+)$")


def bare(name):
    """`CameraGuy_x1#1` -> `CameraGuy_x1`. Drops the instance suffix."""
    return name.split("#", 1)[0]

FAILURES = []


def check(cond, label, detail=""):
    print("  [%s] %-56s %s" % ("PASS" if cond else "FAIL", label, detail))
    if not cond:
        FAILURES.append(label)
    return cond


def health(game):
    """path -> value for every copy of srd.cr, keyed by archive."""
    out = {}
    for arc in game.locate(CREATURE):
        try:
            data = game.read(arc, CREATURE)
        except Exception as ex:                              # noqa: BLE001
            print("  could not read %s::%s: %s" % (arc, CREATURE, ex))
            continue
        out[arc] = creature.read_fields(data, sorted(GOD))
    return out


def enm_bytes(game):
    """The raw enemy file, or None if this level has no such member."""
    lvl = Level(game, LEVEL)
    try:
        return lvl._read(ENM)
    except Exception as ex:                                  # noqa: BLE001
        print("  could not read %s::%s: %s" % (LEVEL, ENM, ex))
        return None


def report_health(copies, expect, what):
    """True when every copy carries `expect`. Names the ones that do not."""
    if not check(len(copies) == CREATURE_ARCHIVES,
                 "%s: all %d archives holding srd.cr read back"
                 % (what, CREATURE_ARCHIVES), "%d read" % len(copies)):
        return False
    odd = {a: f for a, f in copies.items() if f != expect}
    ok = check(not odd, "%s: every copy reads %s" % (what, fmt(expect)),
               "%d of %d" % (len(copies) - len(odd), len(copies)))
    for arc in sorted(odd)[:5]:
        print("    %-28s %s" % (arc, fmt(odd[arc])))
    if len(odd) > 5:
        print("    ... and %d more" % (len(odd) - 5))
    return ok


def fmt(fields):
    return ", ".join("%s %g" % (k.split(".")[-1], v)
                     for k, v in sorted(fields.items()))


def main():
    if len(sys.argv) < 2:
        print("name the disc to check: verify_disc_claims.py <disc.iso>")
        print("there is no default -- the point of this check is WHICH disc.")
        print("Nothing has been run.")
        return 2
    disc = os.path.abspath(sys.argv[1])
    quick = "--quick" in sys.argv[2:]
    for path in (disc, VANILLA) + (() if quick else (PLACEMENT_REF,)):
        if not os.path.exists(path):
            print("no such image: %s" % path)
            print("Nothing has been run.")
            return 2

    print("disc     : %s" % disc)
    print("vanilla  : %s   (roster control)" % VANILLA)
    if not quick:
        print("reference: %s   (placement + god-mode control)" % PLACEMENT_REF)
    print("read-only on all of them; nothing is written and no lock is taken.")
    print("")
    print("expected, fixed in this file and not read off any disc:")
    print("  60 Scattered Enemies   %d blocks in %s, bytes matching the"
          % (ROSTER, ENM))
    print("                         disc the developer played")
    print("  God Mode               %s in all %d archives"
          % (fmt(GOD), CREATURE_ARCHIVES))
    print("")

    game = Game(disc, writable=False)

    print("1. God Mode -- srd.cr HealthData, every copy")
    report_health(health(game), GOD, "disc")

    print("")
    print("2. 60 Scattered Enemies -- the roster in %s" % ENM)
    data = enm_bytes(game)
    if data is None:
        check(False, "the level's enemy file reads back")
    else:
        nodes = scene.parse_enemies(data, ENM)
        names = [bare(n.name) for n in nodes]
        check(len(nodes) == ROSTER, "the roster is %d enemies" % ROSTER,
              "%d blocks" % len(nodes))
        check(len(set(names)) == len(names),
              "every enemy name is distinct", "%d distinct" % len(set(names)))
        stock = [n for n in names if not CLONE.match(n)]
        clones = [n for n in names if CLONE.match(n)]
        check(len(stock) == ROSTER_VANILLA,
              "the %d vanilla enemies are still there" % ROSTER_VANILLA,
              "%d un-cloned names" % len(stock))
        # Counted before it is inspected. "every clone is well-formed" is
        # vacuously true of no clones at all, so a regex that stops matching
        # passes that check silently -- which is how the first run of this
        # file reported 0 clones and 0 orphans in the same breath.
        check(len(clones) == ROSTER - ROSTER_VANILLA,
              "%d of the %d are clones, not stock"
              % (ROSTER - ROSTER_VANILLA, ROSTER), "%d clones" % len(clones))
        # A clone whose base is not a real enemy name resolves to nothing and
        # never spawns, which reads on screen as a roster that did not apply.
        orphan = [n for n in clones
                  if CLONE.match(n).group("base") not in set(stock)]
        check(not orphan,
              "every clone is <Original>_xN off a name that exists",
              "%d clones, %d orphaned" % (len(clones), len(orphan)))
        for n in orphan[:5]:
            print("    orphan clone: %s" % n)

    print("")
    print("3. the controls -- these must NOT look patched")
    vanilla = Game(VANILLA, writable=False)
    vdata = enm_bytes(vanilla)
    if vdata is None:
        check(False, "vanilla's enemy file reads back")
    else:
        vnodes = scene.parse_enemies(vdata, ENM)
        check(len(vnodes) == ROSTER_VANILLA,
              "vanilla still reads %d enemies, so the counter can tell them"
              % ROSTER_VANILLA, "%d blocks" % len(vnodes))
    report_health(health(vanilla), NOT_GOD, "vanilla")

    if not quick:
        ref = Game(PLACEMENT_REF, writable=False)
        report_health(health(ref), NOT_GOD, "reference")
        rdata = enm_bytes(ref)
        if rdata is None or data is None:
            check(False, "both enemy files read back for the byte compare")
        else:
            rnodes = scene.parse_enemies(rdata, ENM)
            check(len(rnodes) == ROSTER,
                  "the reference disc itself carries %d enemies" % ROSTER,
                  "%d blocks" % len(rnodes))
            print("")
            print("4. placement -- the same scatter the developer played")
            check(data == rdata,
                  "%s is byte-identical to the reference disc" % ENM,
                  "%d bytes vs %d" % (len(data), len(rdata)))

    print("")
    if FAILURES:
        print("FAILED %d check(s):" % len(FAILURES))
        for f in FAILURES:
            print("  %s" % f)
        print("the Options page is advertising something this disc does not "
              "do. Do not ship it under those names.")
        return 1
    print("the disc does what its Options page says: %d enemies, god mode in "
          "all %d archives." % (ROSTER, CREATURE_ARCHIVES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
