#!/usr/bin/env bash
# N=3 expansion, our-stack arm: v0741 cells 2 and 3 of 3 (5120 + medium, v2_9).
set -u
H=/home/ezflow/coding/qwen38-strix-halo-harness/bench
L=/home/ezflow/LLMBench/results/qwen38-27b/game-ladder
MARK=$L/n3fn.done
rm -f "$MARK"
cd "$H" || exit 1
export PROVIDER=llamacpp MODEL=qwen3.8-flash-next PORT=8080 BUDGET=5120
P="$(cat "$L/prompt-multi-v2_9.txt")"
for c in e f; do
  ./run-game-bench-v2.sh "manual-fn-v0741-med-$c-5120" "fn-v0741-med-$c-5120" medium "$P"
  echo "=== n3fn $c rc=$? $(date)"
done
touch "$MARK"
