#!/usr/bin/env bash
# halogen v0.15.1 native (v2.hgn) v2_12 cells.
set -u
H=/home/ezflow/coding/qwen38-strix-halo-harness/bench
L=/home/ezflow/LLMBench/results/qwen38-27b/game-ladder
MARK=$L/v015nativecells.done
rm -f "$MARK"
cd "$H" || exit 1
export PROVIDER=halogen MODEL=halogen-qwen3.8-flash-next PORT=8731
P="$(cat "$L/prompt-multi-v2_12.txt")"
for c in b c d; do
  ./run-game-bench-v2.sh "manual-halogen0151nat-med-$c-v2_12" "halogen0151nat-med-$c-v2_12" medium "$P"
  echo "=== v015nat $c rc=$? $(date)"
done
touch "$MARK"
