#!/usr/bin/env bash
# 0.7.0 GGUF-mode cells (same unsloth UD-IQ4_XS weights as the v0741 stack),
# sampling pinned, native 32768 budget, v2_9 prompt. Marker on exit.
set -u
H=/home/ezflow/coding/qwen38-strix-halo-harness/bench
L=/home/ezflow/LLMBench/results/qwen38-27b/game-ladder
MARK=$L/ggufcells.done
rm -f "$MARK"
cd "$H" || exit 1
export PROVIDER=halogen MODEL=halogen-qwen3.8-flash-next PORT=8731
P="$(cat "$L/prompt-multi-v2_9.txt")"
for c in med-b med-c med-d; do
  ./run-game-bench-v2.sh "manual-halogen070gguf-$c" "halogen070gguf-$c" medium "$P"
  echo "=== ggufcells $c rc=$? $(date)"
done
./run-game-bench-v2.sh manual-halogen070gguf-high-b halogen070gguf-high-b high "$P"
echo "=== ggufcells high-b rc=$? $(date)"
touch "$MARK"
echo "=== ggufcells done $(date)"
