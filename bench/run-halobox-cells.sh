#!/usr/bin/env bash
# halo-box v2_12 cells (their best config: combined draft-mtp,ngram-mod spec server).
set -u
H=/home/ezflow/coding/qwen38-strix-halo-harness/bench
L=/home/ezflow/LLMBench/results/qwen38-27b/game-ladder
MARK=$L/haloboxcells.done
rm -f "$MARK"
cd "$H" || exit 1
export PROVIDER=llamacpp MODEL=qwen3.8-flash-next PORT=8080
P="$(cat "$L/prompt-multi-v2_12.txt")"
for c in b c d; do
  ./run-game-bench-v2.sh "manual-halobox-med-$c-v2_12" "halobox-med-$c-v2_12" medium "$P"
  echo "=== halobox $c rc=$? $(date)"
done
./run-game-bench-v2.sh manual-halobox-high-b-v2_12 halobox-high-b-v2_12 high "$P"
echo "=== halobox high-b rc=$? $(date)"
touch "$MARK"
