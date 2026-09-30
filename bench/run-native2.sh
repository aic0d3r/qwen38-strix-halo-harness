#!/usr/bin/env bash
# Two more 0.6.1 native-budget medium cells (sampling pinned). Marker on exit.
set -u
H=/home/ezflow/coding/qwen38-strix-halo-harness/bench
L=/home/ezflow/LLMBench/results/qwen38-27b/game-ladder
MARK=$L/native2.done
rm -f "$MARK"
cd "$H" || exit 1
export PROVIDER=halogen MODEL=halogen-qwen3.8-flash-next PORT=8731
P="$(cat "$L/prompt-multi-v2_9.txt")"
for c in med-c med-d; do
  ./run-game-bench-v2.sh "manual-halogen061-$c-native" "halogen061-$c-native" medium "$P"
  echo "=== native2 $c rc=$? $(date)"
done
touch "$MARK"
echo "=== native2 done $(date)"
