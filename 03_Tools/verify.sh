#!/usr/bin/env bash
# Milestone done-gate for the tester build-out.
# Exits 0 only when the CURRENT milestone (lowest un-landed ## Mn in ROADMAP-TESTER.md)
# has a registered [Mn] acceptance test AND the whole Windows suite is green.
WINDIR="$(wslpath -w "$(cd "$(dirname "$0")" && pwd)")"
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROADMAP="$ROOT/ROADMAP-TESTER.md"
TESTS="$ROOT/03_Tools/run_tests.py"
CMD=/mnt/c/Windows/System32/cmd.exe

M="$(grep -oE '^## (M[0-9]+)[^\n]*' "$ROADMAP" | grep -v '✅' | head -1 | grep -oE 'M[0-9]+')"

# EXIT 0 WITH NOTHING RUN MUST NOT BE REACHABLE. Until 2026-09-18 this said:
#
#     if [ -z "${M:-}" ]; then echo "verify: every milestone is landed"; exit 0; fi
#
# and that branch fired for the first time at 07:12:49 that day, when cdd9b64
# ticked M21 — the last un-ticked row. From that commit on, this gate exited 0
# in twenty seconds having run ZERO tests, and "no un-landed milestone" was
# indistinguishable in the exit code from "every test passed". It was cited as
# the second half of a release gate in that state.
#
# The gate did not rot. It was COMPLETED into a no-op: the last meaningful
# verify.sh PASS is the one quoted in cdd9b64's own message, authorising the
# tick that disarmed it.
#
# So an empty selector is no longer a reason to skip the suite. It is only a
# reason not to check milestone REGISTRATION, because there is no milestone to
# register. The suite runs either way, and the verdict below is about the
# suite.
if [ -z "${M:-}" ]; then
  echo "verify: no un-landed milestone to gate — running the full suite anyway"
else
  echo "verify: current milestone = $M"
  if ! grep -q "\[$M\]" "$TESTS"; then
    echo "verify: FAIL — no acceptance test registered as [$M] in run_tests.py"
    exit 1
  fi
fi

OUT="$(mktemp)"
flock /tmp/rdr-suite.lock "$CMD" /c "cd /d $WINDIR && set RDR_SUITE_LOCK=held&& py -3 run_tests.py" >"$OUT" 2>&1
RC=$?
tail -40 "$OUT"
# -a, and it is load-bearing. The suite's own output carries bytes from the
# GAME DATA: the [M12] csv test prints the roster name '\x93Smiley\x94 Fawler',
# whose cp1252 smart quotes (0x93/0x94) are not valid UTF-8. grep then treats
# the whole log as binary and `grep -q SUMMARY` exits 1 -- reporting a clean
# 62/62 run as TRUNCATED. Measured on the 2026-09-18 00:05 run: plain -q and
# LC_ALL=C both exit 1, -a exits 0, and -a still fails a genuinely truncated
# log, so the guard keeps its teeth.
if ! grep -aq "SUMMARY" "$OUT"; then
  echo "verify: FAIL — no SUMMARY block; the run was truncated, not clean"
  exit 1
fi
if [ "$RC" -ne 0 ]; then
  echo "verify: FAIL — suite exit $RC"
  exit 1
fi
# Says which question it answered. "PASS" alone let a twenty-second run that
# tested nothing read exactly like a sixteen-minute one that tested everything.
if [ -z "${M:-}" ]; then
  echo "verify: PASS — no milestone outstanding, and the full suite is green"
else
  echo "verify: PASS — $M acceptance test registered and full suite green"
fi
exit 0
