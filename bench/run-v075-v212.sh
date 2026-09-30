#!/usr/bin/env bash
# First v2.2-pipeline cell set: Nathan v0.7.5 x v2_12 prompt (new comparability
# group). Effort-keyed budgets + runtime gate + probe all live in the runner now.
set -u
H=/home/ezflow/coding/qwen38-strix-halo-harness/bench
L=/home/ezflow/LLMBench/results/qwen38-27b/game-ladder
MARK=$L/v075v212.done
rm -f "$MARK"
cd "$H" || exit 1
export PROVIDER=llamacpp MODEL=qwen3.8-flash-next PORT=8080
P="$(cat "$L/prompt-multi-v2_12.txt")"
for c in b c d; do
  ./run-game-bench-v2.sh "manual-fn-v075-med-$c-v2_12" "fn-v075-med-$c-v2_12" medium "$P"
  echo "=== v075v212 $c rc=$? $(date)"
done
./run-game-bench-v2.sh manual-fn-v075-high-b-v2_12 fn-v075-high-b-v2_12 high "$P"
echo "=== v075v212 high-b rc=$? $(date)"
touch "$MARK"
