#!/usr/bin/env bash
# one command for daily use: bring the engine ./setup.sh picked back up (starts halogen, gufo or Strata
# if it is down; asks before stopping another engine that holds the GPU memory), refresh the install
# quietly, show an update notice, then drop into the pi TUI on that engine.
#   ./start.sh [project-dir]
# no ./setup.sh run yet: halogen, as before.
set -u
D="${1:-$PWD}"
[ -d "$D" ] || { echo "not a directory: $D" >&2; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
python3 -u "$HERE/setup/harness.py" up || exit 1
read -r PROV MID < <(python3 -c 'import json,sys; p=json.load(open(sys.argv[1])); print(p["provider"], p["model"])' \
  "${PI_CODING_AGENT_DIR:-${PI_HOME:-$HOME/.pi}/agent}/harness-profile.json" 2>/dev/null)
cd "$D"
# ponytail rules stay active; hide the redundant footer indicator (/ponytail status still works)
export PONYTAIL_HIDE_STATUS=1
exec pi --provider "${PROV:-halogen}" --model "${MID:-halogen-qwen3.8-flash-next}"
