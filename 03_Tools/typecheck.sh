#!/usr/bin/env bash
# The static type gate for rdrstudio.  Config and the opt-out list: mypy.ini.
#
#     bash 03_Tools/typecheck.sh            the gate -- exit 0 means clean
#     bash 03_Tools/typecheck.sh --debt     ignore the opt-out list, show
#                                           every error the package really has
#
# No suite lock: mypy reads .py text and never opens the ISO, so this is safe
# to run while another lane's suite is mid-flight.  It does start a Windows
# python.exe for a few seconds -- if a lane is about to run the
# `taskkill /f /im python.exe` hygiene step, let this finish first.
WINDIR="$(wslpath -w "$(cd "$(dirname "$0")" && pwd)")"
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CMD=/mnt/c/Windows/System32/cmd.exe
FLAGS=""
[ "${1:-}" = "--debt" ] && FLAGS="--no-incremental --config-file=nul --check-untyped-defs --ignore-missing-imports --python-version=3.12"

OUT="$(mktemp)"
"$CMD" /c "cd /d $WINDIR && py -3 -m mypy -p rdrstudio $FLAGS --no-color-output" \
  >"$OUT" 2>&1
RC=$?
tr -d '\r' <"$OUT"

if ! grep -qE "^(Success|Found )" "$OUT"; then
  echo "typecheck: FAIL - no mypy summary line; the run died rather than finished"
  exit 1
fi
if [ "$RC" -ne 0 ] && [ "${1:-}" != "--debt" ]; then
  echo "typecheck: FAIL - see mypy.ini; do not silence a new error by adding its module to the opt-out list"
  exit 1
fi
[ "${1:-}" = "--debt" ] && exit 0
echo "typecheck: PASS"
exit 0
