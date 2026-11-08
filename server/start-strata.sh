#!/usr/bin/env bash
# Start a locally installed Strata (github.com/Niko1221/Strata) on the saved weights answer:
# abliterated -> run-unsloth-ud-q4_k_xl.sh (gufo's orca-uncensored shards, scripts/install-strata.sh),
# stock -> run-unsloth-ud-iq4_xs.sh; else the newest run-<model>.sh its setup wrote, else its ./setup.sh.
# Folder: $STRATA_DIR, else ~/coding/Strata, ~/Strata, ~/strata, ~/src/Strata. STRATA_WEIGHTS overrides the answer.
# One big engine at a time: stop halogen/gufo first (the wizard does that for you).
#   --print-dir   the checkout (the update check reads the version from it)
#   --print-run   the run script for the saved answer, empty when that model is not installed yet
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
for d in "${STRATA_DIR:-}" "$HOME/coding/Strata" "$HOME/Strata" "$HOME/strata" "$HOME/src/Strata"; do
  [ -n "$d" ] && [ -f "$d/setup.py" ] && [ -f "$d/update.sh" ] && { DIR=$d; break; }
done
[ "${1:-}" = --print-dir ] && { echo "${DIR:-}"; exit 0; }
WEIGHTS="${STRATA_WEIGHTS:-$(cat "${PI_CODING_AGENT_DIR:-${PI_HOME:-$HOME/.pi}/agent}/harness-weights" 2>/dev/null)}"
[ "$WEIGHTS" = abliterated ] && RUN=run-unsloth-ud-q4_k_xl.sh || RUN=run-unsloth-ud-iq4_xs.sh
if [ "${1:-}" = --print-run ]; then
  [ -n "${DIR:-}" ] && [ -f "$DIR/$RUN" ] && echo "$DIR/$RUN"
  exit 0
fi
[ -n "${DIR:-}" ] || { echo "Strata not found (looked in \$STRATA_DIR, ~/coding/Strata, ~/Strata, ~/strata, ~/src/Strata): install it with scripts/install-strata.sh (or ./setup.sh, which offers it), or set STRATA_DIR" >&2; exit 1; }
export BROWSER=true  # its --open would pop a browser tab on every start
cd "$DIR" || exit 1
[ -f "$DIR/$RUN" ] || RUN=$(ls -t "$DIR"/run-*.sh 2>/dev/null | head -1)
# Strix Halo: every expert in the GPU pool when they fit (setup writes a slower RAM-budget mode); idempotent, a no-op elsewhere
CFG=$(grep -o '"--config" "[^"]*"' "$RUN" 2>/dev/null | cut -d'"' -f4)
[ -n "$CFG" ] && [ -x "$DIR/.venv/bin/python" ] && "$DIR/.venv/bin/python" "$HERE/../scripts/strata-uma.py" "$CFG"
[ -n "$RUN" ] && exec sh "$RUN"
exec sh ./setup.sh --yes
