#!/usr/bin/env bash
set -u
H=/home/ezflow/coding/qwen38-strix-halo-harness/bench
L=/home/ezflow/LLMBench/results/qwen38-27b/game-ladder
P="$(cat "$L/prompt-multi-v2_12.txt")"
cd "$H" || exit 1
export PROVIDER=halogen MODEL=halogen-qwen3.8-flash-next PORT=8731
./run-game-bench-v2.sh manual-halogen0151nat-med-e-v2_12 halogen0151nat-med-e-v2_12 medium "$P"
echo "=== med-e rc=$? $(date)"
touch "$L/mede.done"
