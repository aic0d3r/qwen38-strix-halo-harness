#!/usr/bin/env bash
set -u
H=/home/ezflow/coding/qwen38-strix-halo-harness/bench
L=/home/ezflow/LLMBench/results/qwen38-27b/game-ladder
P="$(cat "$L/prompt-multi-v2_12.txt")"
cd "$H" || exit 1
export PROVIDER=llamacpp MODEL=qwen3.8-flash-next PORT=8080
./run-game-bench-v2.sh manual-gufo-high-b-v2_12 gufo-high-b-v2_12 high "$P"
echo "=== gufohigh rc=$? $(date)"
touch "$L/gufohigh.done"
