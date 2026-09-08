#!/usr/bin/env bash
# Run the game-build bench with perturbation-retry semantics.
#
# Why retries: at temperature 0.8 the model occasionally samples an instant-EOS
# first turn (~1 in 3-5 on this prompt class). Retrying the IDENTICAL prompt
# inherits the degenerate turn from KV cache and fails again; each attempt
# appends a unique marker so every retry rolls fresh.
# Note (engine v0.7.4): greedy nondeterminism (stale-KV + top-k race above ~2k prompt
# tokens) is fixed there, but perturbed retries stay in the protocol - cheap, and they
# defend against everything.
#
# Usage: ./run-with-retry.sh <workdir> <session-name> <effort: off|low|medium|high>
#   workdir  - build directory (created if missing; run from a fresh one per bench)
#   PROVIDER - pi provider name (default: llamacpp)
#   MODEL    - pi model id (default: qwen3.8-27b)
# Requires: pi on PATH, the frozen game-prompt.txt next to this script.
#
# GOTCHA (2026-09-08): pi --provider takes the provider NAME only. The old
# "llamacpp/qwen3.8-27b" slash form is not valid input, and pi does not error on
# it: it silently falls back to the default provider from settings, so a run
# targets whatever the current default is. Every earlier cell was correct only
# while the default happened to match the intended engine. Provider and model
# are now separate flags, overridable per run:
#   PROVIDER=halogen MODEL=halogen-qwen3.8-flash-next ./run-with-retry.sh ...
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
PROMPT=$HERE/game-prompt.txt
DIR=$1 NAME=$2 LVL=$3
PROVIDER=${PROVIDER:-llamacpp}
MODEL=${MODEL:-qwen3.8-27b}
mkdir -p "$DIR" && cd "$DIR"
for TRY in 1 2 3 4 5; do
  P="$(cat "$PROMPT") [attempt $TRY $(date +%s)]"
  timeout 5400 pi -p --no-skills --no-context-files \
    --provider "$PROVIDER" --model "$MODEL" --thinking "$LVL" --name "$NAME" "$P" \
    > "run-$NAME.txt" 2>&1
  if [ "$(find . -name '*.js' -o -name '*.html' -o -name '*.css' 2>/dev/null | wc -l)" -gt 0 ] \
     || [ "$(stat -c%s "run-$NAME.txt" 2>/dev/null || echo 0)" -gt 10000 ]; then
    echo "=== $NAME try=$TRY SUCCESS ==="; exit 0
  fi
  echo "=== $NAME try=$TRY degenerate (prompt basin), perturbing ==="; sleep 3
done
echo "=== $NAME FAILED after 5 tries ==="; exit 1
