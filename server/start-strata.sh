#!/usr/bin/env bash
# Start a locally installed Strata (github.com/Niko1221/Strata) on the saved weights answer. Both answers run
# UD-Q4_K_XL (run-unsloth-ud-q4_k_xl.sh): stock = Unsloth's GGUF that gufo shares (scripts/download-stock.sh),
# abliterated = gufo's orca-uncensored shards (scripts/install-strata.sh). Strata keys its repacked dense weights by
# model name only, so <Strata dir>/.harness-weights records whose weights the install was built from and a start
# on the other answer is refused until install-strata.sh rebuilds it (installs older than the marker were abliterated).
# No q4_k_xl script at all: the newest run-<model>.sh its setup wrote, else its ./setup.sh.
# Folder: $STRATA_DIR, else ~/coding/Strata, ~/Strata, ~/strata, ~/src/Strata. STRATA_WEIGHTS overrides the answer.
# One big engine at a time: stop halogen/gufo first (the wizard does that for you).
#   --print-dir   the checkout (the update check reads the version from it)
#   --print-run   the run script for the saved model and weights answers, empty when that model is not installed yet
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
for d in "${STRATA_DIR:-}" "$HOME/coding/Strata" "$HOME/Strata" "$HOME/strata" "$HOME/src/Strata"; do
  [ -n "$d" ] && [ -f "$d/setup.py" ] && [ -f "$d/update.sh" ] && { DIR=$d; break; }
done
[ "${1:-}" = --print-dir ] && { echo "${DIR:-}"; exit 0; }
WEIGHTS="${STRATA_WEIGHTS:-$(cat "${PI_CODING_AGENT_DIR:-${PI_HOME:-$HOME/.pi}/agent}/harness-weights" 2>/dev/null)}"
WEIGHTS=${WEIGHTS:-stock}
# the saved model answer (./harness config -> model, or STRATA_MODEL): a catalog id with its own Strata run script, else
# Flash-Next on the Unsloth layout (OWN=0), the only one the stock/abliterated answer applies to
MODEL="${STRATA_MODEL:-$(cat "${PI_CODING_AGENT_DIR:-${PI_HOME:-$HOME/.pi}/agent}/harness-model" 2>/dev/null)}"
RUN=$(python3 "$HERE/../setup/harness.py" model-info "${MODEL:-flash-next}" run 2>/dev/null) && OWN=1 || { OWN=0; RUN=run-unsloth-ud-q4_k_xl.sh; }
BUILT=$(cat "${DIR:-/nonexistent}/.harness-weights" 2>/dev/null || echo abliterated)
if [ "${1:-}" = --print-run ]; then
  [ -n "${DIR:-}" ] && [ -f "$DIR/$RUN" ] && { [ "$OWN" = 1 ] || [ "$BUILT" = "$WEIGHTS" ]; } && echo "$DIR/$RUN"
  exit 0
fi
[ -n "${DIR:-}" ] || { echo "Strata not found (looked in \$STRATA_DIR, ~/coding/Strata, ~/Strata, ~/strata, ~/src/Strata): install it with scripts/install-strata.sh (or ./setup.sh, which offers it), or set STRATA_DIR" >&2; exit 1; }
export BROWSER=true  # its --open would pop a browser tab on every start
cd "$DIR" || exit 1
if [ "$OWN" = 0 ] && [ -f "$DIR/$RUN" ] && [ "$BUILT" != "$WEIGHTS" ]; then
  echo "Strata in $DIR is built from the $BUILT weights, your saved answer is $WEIGHTS: scripts/install-strata.sh rebuilds it (a few minutes, no download)" >&2
  exit 1
fi
if [ "$OWN" = 1 ]; then
  [ -f "$DIR/$RUN" ] || { echo "Strata has no $RUN for the saved model ($MODEL): scripts/install-strata.sh sets it up" >&2; exit 1; }
else
  [ -f "$DIR/$RUN" ] || RUN=$(ls -t "$DIR"/run-*.sh 2>/dev/null | head -1)
fi
# Strix Halo: every expert in the GPU pool when they fit (setup writes a slower RAM-budget mode); idempotent, a no-op elsewhere
CFG=$(grep -o '"--config" "[^"]*"' "$RUN" 2>/dev/null | cut -d'"' -f4)
[ -n "$CFG" ] && [ -x "$DIR/.venv/bin/python" ] && "$DIR/.venv/bin/python" "$HERE/../scripts/strata-uma.py" "$CFG"
[ -n "$RUN" ] && exec sh "$RUN"
exec sh ./setup.sh --yes
