#!/usr/bin/env bash
set -u
H=/home/ezflow/coding/qwen38-strix-halo-harness/bench
L=/home/ezflow/LLMBench/results/qwen38-27b/game-ladder
P="$(cat "$L/prompt-multi-v2_12.txt")"
cd "$H" || exit 1
export PROVIDER=llamacpp MODEL=qwen3.8-flash-next PORT=8080
./run-game-bench-v2.sh manual-gufo-med-e-v2_12 gufo-med-e-v2_12 medium "$P"
echo "=== gufo-logtest rc=$? $(date)"
touch "$L/gufologtest.done"
