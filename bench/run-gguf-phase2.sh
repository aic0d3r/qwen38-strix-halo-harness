#!/usr/bin/env bash
# v0741 stack (same bytes) arms: shared-5120 medium cell + v2_12 seed cell.
set -u
H=/home/ezflow/coding/qwen38-strix-halo-harness/bench
L=/home/ezflow/LLMBench/results/qwen38-27b/game-ladder
M=/home/ezflow/.pi/agent/models.json
MARK=$L/ggufphase2.done
rm -f "$MARK"
cd "$H" || exit 1
export PROVIDER=llamacpp MODEL=qwen3.8-flash-next PORT=8080
P9="$(cat "$L/prompt-multi-v2_9.txt")"
P12="$(cat "$L/prompt-multi-v2_12.txt")"
jq '.providers.llamacpp.models[] |= (if .id == "qwen3.8-flash-next" then .maxTokens = 5120 else . end)' "$M" > "$M.tmp" && mv "$M.tmp" "$M"
./run-game-bench-v2.sh manual-fn-v0741-med-b-5120 fn-v0741-med-b-5120 medium "$P9"
echo "=== phase2 5120 rc=$? $(date)"
jq '.providers.llamacpp.models[] |= (if .id == "qwen3.8-flash-next" then .maxTokens = 32768 else . end)' "$M" > "$M.tmp" && mv "$M.tmp" "$M"
./run-game-bench-v2.sh manual-fn-v0741-med-b-v2_12 fn-v0741-med-b-v2_12 medium "$P12"
echo "=== phase2 v2_12 rc=$? $(date)"
touch "$MARK"
