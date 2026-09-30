#!/usr/bin/env bash
# 0.6.1 high-effort cell, sampling pinned (card settings), native 32768.
set -u
H=/home/ezflow/coding/qwen38-strix-halo-harness/bench
L=/home/ezflow/LLMBench/results/qwen38-27b/game-ladder
MARK=$L/high061.done
rm -f "$MARK"
cd "$H" || exit 1
export PROVIDER=halogen MODEL=halogen-qwen3.8-flash-next PORT=8731
P="$(cat "$L/prompt-multi-v2_9.txt")"
./run-game-bench-v2.sh manual-halogen061-high-b-native halogen061-high-b-native high "$P"
echo "=== high rc=$? $(date)"
touch "$MARK"
