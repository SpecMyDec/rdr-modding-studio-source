#!/usr/bin/env bash
# The project's test gate: the Windows suite, serialized under the shared ISO lock.
# Usage: bash 03_Tools/test.sh [--full]    (default is --quick)
WINDIR="$(wslpath -w "$(cd "$(dirname "$0")" && pwd)")"
set -uo pipefail
ARGS="--quick"; [ "${1:-}" = "--full" ] && ARGS=""
OUT="$(mktemp)"
flock /tmp/rdr-suite.lock /mnt/c/Windows/System32/cmd.exe /c \
  "cd /d $WINDIR && set RDR_SUITE_LOCK=held&& py -3 run_tests.py $ARGS" >"$OUT" 2>&1
RC=$?
tail -50 "$OUT"

# -a on BOTH greps below, and it is load-bearing. REQ-062, found by rdr-fmt
# 2026-09-18 against a 62/62 green run this guard called truncated.
#
# Game data reaches the log: [M12] prints a roster name holding cp1252 smart
# quotes (0x93/0x94), which are invalid UTF-8, so grep classifies the whole log
# as binary and -q exits 1 THOUGH SUMMARY appears eight times. Measured on the
# four cases that matter -- the locale is not the fix, and -a keeps the teeth:
#
#   green log + game data   grep -q=1  LC_ALL=C grep -q=1  grep -aq=0
#   green log, clean bytes  grep -q=0                      grep -aq=0
#   TRUNCATED log           grep -q=1                      grep -aq=1  <- still caught
#   STUDIO-STALE + data     grep -q=1                      grep -aq=0
#
# It misfires NONDETERMINISTICALLY, because whether a run's log carries a high
# byte depends on which game strings its tests happened to print. And it fails
# in the safe direction, which is what makes it dangerous: a gate that cries
# wolf gets weakened, and STATE-CONTRACT says this guard is the only
# thing standing between a killed run and a lane reporting it as green. The
# tempting "fix" for the false alarm is exactly how you would get a permanent
# false GREEN. Do not drop the -a, and do not simplify the guard away.
if ! grep -aq SUMMARY "$OUT"; then
  # A REFUSAL is not a truncation. run_tests.py stops before the first test
  # when the Studio on 8731 is older than the source it serves, and prints no
  # SUMMARY because nothing ran. Without this branch the guard below calls that
  # a killed run and sends someone hunting a truncation that never happened --
  # while the actual instruction, which is one line up in the output, says to
  # re-run with --restart-studio.
  if grep -aq '^STUDIO-STALE' "$OUT"; then
    echo "test: FAIL - stale Studio on 8731, nothing was run (see above)"
    exit "$RC"
  fi
  echo "test: FAIL - no SUMMARY block, run was truncated"
  exit 1
fi
exit $RC
