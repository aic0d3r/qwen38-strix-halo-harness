#!/usr/bin/env bash
set -u
H=/home/ezflow/coding/qwen38-strix-halo-harness/bench
L=/home/ezflow/LLMBench/results/qwen38-27b/game-ladder
cd "$H" || exit 1
export PROVIDER=llamacpp MODEL=qwen3.8-flash-next PORT=8080
P="$(cat "$L/prompt-multi-v2_12.txt")"
./run-game-bench-v2.sh manual-fn-v0741-med-h-v2_12 fn-v0741-med-h-v2_12 medium "$P"
echo "=== v212b rc=$? $(date)"
touch "$L/v212b.done"
