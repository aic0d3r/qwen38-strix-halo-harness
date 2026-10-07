#!/usr/bin/env bash
# one command for daily use: heal any dead servers, then drop into the pi TUI.
#   ./start.sh [project-dir]
# first time ever? run ./setup.sh --halogen auto once before this (or let this do it).
set -u
D="${1:-$PWD}"
[ -d "$D" ] || { echo "not a directory: $D" >&2; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
bash "$HERE/setup.sh" --halogen auto || exit 1
cd "$D"
# ponytail rules stay active; hide the redundant footer indicator (/ponytail status still works)
export PONYTAIL_HIDE_STATUS=1
exec pi --provider halogen --model halogen-qwen3.8-flash-next
