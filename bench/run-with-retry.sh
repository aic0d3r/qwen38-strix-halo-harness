#!/usr/bin/env bash
# Run the game-build bench with perturbation-retry semantics.
#
# Why retries: at temperature 0.8 the model occasionally samples an instant-EOS
# first turn (~1 in 3-5 on this prompt class). Retrying the IDENTICAL prompt
# inherits the degenerate turn from KV cache and fails again; each attempt
# appends a unique marker so every retry rolls fresh.
# Note (engine v0.7.4): greedy nondeterminism (stale-KV + top-k race above ~2k prompt
# tokens) is fixed there, but perturbed retries stay in the protocol — cheap, and they
# defend against everything.
#
# Usage: ./run-with-retry.sh <workdir> <session-name> <effort: off|low|medium|high>
#   workdir  - build directory (created if missing; run from a fresh one per bench)
# Requires: pi on PATH, the frozen game-prompt.txt next to this script.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
PROMPT=$HERE/game-prompt.txt
DIR=$1 NAME=$2 LVL=$3
mkdir -p "$DIR" && cd "$DIR"
for TRY in 1 2 3 4 5; do
  P="$(cat "$PROMPT") [attempt $TRY $(date +%s)]"
  timeout 5400 pi -p --no-skills --no-context-files \
    --provider llamacpp/qwen3.8-27b --thinking "$LVL" --name "$NAME" "$P" \
    > "$DIR/run-$NAME.txt" 2>&1
  if [ "$(find "$DIR" -name '*.js' -o -name '*.html' -o -name '*.css' 2>/dev/null | wc -l)" -gt 0 ] \
     || [ "$(stat -c%s "$DIR/run-$NAME.txt" 2>/dev/null || echo 0)" -gt 10000 ]; then
    echo "=== $NAME try=$TRY SUCCESS ==="; exit 0
  fi
  echo "=== $NAME try=$TRY degenerate (prompt basin), perturbing ==="; sleep 3
done
echo "=== $NAME FAILED after 5 tries ==="; exit 1
