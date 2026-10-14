#!/usr/bin/env bash
# one command for daily use: heal any dead servers, then drop into the pi TUI.
#   ./start.sh [project-dir]
# first time ever? run ./setup.sh --halogen auto once before this (or let this do it).
set -u
D="${1:-$PWD}"
[ -d "$D" ] || { echo "not a directory: $D" >&2; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
bash "$HERE/setup.sh" --halogen auto || exit 1
MSG=$(bash "$HERE/scripts/check-halogen-update.sh")
if [ -n "$MSG" ]; then
  echo "$MSG"
  if [ -t 0 ]; then
    read -r -p "Upgrade halogen now? (restarts the server, rolls back on failure) [y/N] " A
    case "$A" in [yY]*) bash "$HERE/scripts/upgrade-halogen.sh" || { echo "upgrade failed; continuing on the pinned version" >&2; sleep 3; } ;; esac
  fi
fi
cd "$D"
# ponytail rules stay active; hide the redundant footer indicator (/ponytail status still works)
export PONYTAIL_HIDE_STATUS=1
exec pi --provider halogen --model halogen-qwen3.8-flash-next
