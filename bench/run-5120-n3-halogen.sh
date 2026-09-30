#!/usr/bin/env bash
# N=3 expansion of the creator's matched-budget cell (5120 + effort medium):
# halogen 0.7.0 GGUF arm, cells 2 and 3 of 3.
set -u
H=/home/ezflow/coding/qwen38-strix-halo-harness/bench
L=/home/ezflow/LLMBench/results/qwen38-27b/game-ladder
MARK=$L/n3halogen.done
rm -f "$MARK"
cd "$H" || exit 1
export PROVIDER=halogen MODEL=halogen-qwen3.8-flash-next PORT=8731 BUDGET=5120
P="$(cat "$L/prompt-multi-v2_9.txt")"
for c in c d; do
  ./run-game-bench-v2.sh "manual-halogen070gguf-med-$c-5120" "halogen070gguf-med-$c-5120" medium "$P"
  echo "=== n3halogen $c rc=$? $(date)"
done
touch "$MARK"
