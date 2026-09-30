#!/usr/bin/env bash
# gufo 0.3.0 v2_12 cells (Q4_K_XL + adaptive MTP, their recommended config).
set -u
H=/home/ezflow/coding/qwen38-strix-halo-harness/bench
L=/home/ezflow/LLMBench/results/qwen38-27b/game-ladder
MARK=$L/gufocells.done
rm -f "$MARK"
cd "$H" || exit 1
export PROVIDER=llamacpp MODEL=qwen3.8-flash-next PORT=8080
P="$(cat "$L/prompt-multi-v2_12.txt")"
for c in b c d; do
  ./run-game-bench-v2.sh "manual-gufo-med-$c-v2_12" "gufo-med-$c-v2_12" medium "$P"
  echo "=== gufo $c rc=$? $(date)"
done
touch "$MARK"
