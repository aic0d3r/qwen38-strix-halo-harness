#!/usr/bin/env bash
# Confirmation cells: is the recent v0741 breakage a roll or systematic?
# Band config replication: v2_9 prompt, native 32768, effort medium, MTP on.
set -u
H=/home/ezflow/coding/qwen38-strix-halo-harness/bench
L=/home/ezflow/LLMBench/results/qwen38-27b/game-ladder
MARK=$L/fnconfirm.done
rm -f "$MARK"
cd "$H" || exit 1
export PROVIDER=llamacpp MODEL=qwen3.8-flash-next PORT=8080
P="$(cat "$L/prompt-multi-v2_9.txt")"
for c in e f g; do
  ./run-game-bench-v2.sh "manual-fn-v0741-med-$c" "fn-v0741-med-$c" medium "$P"
  echo "=== fnconfirm $c rc=$? $(date)"
done
touch "$MARK"
