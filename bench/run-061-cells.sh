#!/usr/bin/env bash
# 0.6.1 sampling-matched ladder rerun (v2_9 prompt).
# Medium cells: maxTokens 5120 (matched knob). High cell: native 32768.
# Sampling pinned in pi's halogen entry + server-side HALOGEN_* defaults.
set -u
H=/home/ezflow/coding/qwen38-strix-halo-harness/bench
L=/home/ezflow/LLMBench/results/qwen38-27b/game-ladder
M=/home/ezflow/.pi/agent/models.json
cd "$H" || exit 1
export PROVIDER=halogen MODEL=halogen-qwen3.8-flash-next PORT=8731
P="$(cat "$L/prompt-multi-v2_9.txt")"
echo "=== driver start $(date)"
for c in med-b med-c med-d; do
  ./run-game-bench-v2.sh "manual-halogen061-$c" "halogen061-$c" medium "$P"
  echo "=== driver $c rc=$? $(date)"
done
jq '.providers.halogen.models[0].maxTokens = 32768' "$M" > "$M.tmp" && mv "$M.tmp" "$M"
./run-game-bench-v2.sh manual-halogen061-high-b halogen061-high-b high "$P"
echo "=== driver high-b rc=$? $(date)"
jq '.providers.halogen.models[0].maxTokens = 5120' "$M" > "$M.tmp" && mv "$M.tmp" "$M"
echo "=== driver done $(date)"
