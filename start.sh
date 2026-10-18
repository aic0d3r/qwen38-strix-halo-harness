#!/usr/bin/env bash
# one command for daily use: heal any dead servers, then drop into the pi TUI on the engine ./setup.sh picked.
#   ./start.sh [project-dir]
# first time ever? run ./setup.sh --halogen auto once before this (or let this do it).
set -u
D="${1:-$PWD}"
[ -d "$D" ] || { echo "not a directory: $D" >&2; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
# update notices in bold yellow on a terminal, plain when piped
notice(){ if [ -t 1 ]; then printf '\033[1;33m%s\033[0m\n' "$1"; else echo "$1"; fi; }
# the engine the wizard picked (./setup.sh); no profile yet = halogen, as before
read -r ENG PROV MID < <(python3 -c 'import json,sys; p=json.load(open(sys.argv[1])); print(p["engine"], p["provider"], p["model"])' \
  "${PI_CODING_AGENT_DIR:-${PI_HOME:-$HOME/.pi}/agent}/harness-profile.json" 2>/dev/null)
if [ "${ENG:-halogen}" != halogen ]; then
  bash "$HERE/setup.sh" --no-halogen || exit 1
  python3 "$HERE/setup/harness.py" status | grep -q ' UP ' \
    || echo "! $ENG is not answering - start it (or switch engine: ./setup.sh); pi opens anyway" >&2
  MSG=$(python3 "$HERE/setup/harness.py" update-check "$ENG")
  if [ -n "$MSG" ]; then
    notice "$MSG"
    if [ -t 0 ] && [ -f "$HERE/scripts/upgrade-$ENG.sh" ]; then
      read -r -p "Upgrade $ENG now? (restarts the server, rolls back on failure) [y/N] " A
      case "$A" in [yY]*) bash "$HERE/scripts/upgrade-$ENG.sh" || { echo "upgrade failed; continuing on the pinned version" >&2; sleep 3; } ;; esac
    fi
  fi
  cd "$D"; export PONYTAIL_HIDE_STATUS=1
  exec pi --provider "$PROV" --model "$MID"
fi
bash "$HERE/setup.sh" --halogen auto || exit 1
MSG=$(bash "$HERE/scripts/check-halogen-update.sh")
if [ -n "$MSG" ]; then
  notice "$MSG"
  if [ -t 0 ]; then
    read -r -p "Upgrade halogen now? (restarts the server, rolls back on failure) [y/N] " A
    case "$A" in [yY]*) bash "$HERE/scripts/upgrade-halogen.sh" || { echo "upgrade failed; continuing on the pinned version" >&2; sleep 3; } ;; esac
  fi
fi
cd "$D"
# ponytail rules stay active; hide the redundant footer indicator (/ponytail status still works)
export PONYTAIL_HIDE_STATUS=1
exec pi --provider halogen --model halogen-qwen3.8-flash-next
