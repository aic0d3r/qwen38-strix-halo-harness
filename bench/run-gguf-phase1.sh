#!/usr/bin/env bash
# Creator-requested cell: shared 5120 budget + explicit effort medium (his
# "most like to see"), on identical bytes (UD-IQ4_XS via halogen GGUF mode).
# Then seed the v2_12 prompt group (native 32768, medium).
set -u
H=/home/ezflow/coding/qwen38-strix-halo-harness/bench
L=/home/ezflow/LLMBench/results/qwen38-27b/game-ladder
M=/home/ezflow/.pi/agent/models.json
MARK=$L/ggufphase1.done
rm -f "$MARK"
cd "$H" || exit 1
export PROVIDER=halogen MODEL=halogen-qwen3.8-flash-next PORT=8731
P9="$(cat "$L/prompt-multi-v2_9.txt")"
P12="$(cat "$L/prompt-multi-v2_12.txt")"
jq '.providers.halogen.models[0].maxTokens = 5120' "$M" > "$M.tmp" && mv "$M.tmp" "$M"
./run-game-bench-v2.sh manual-halogen070gguf-med-b-5120 halogen070gguf-med-b-5120 medium "$P9"
echo "=== phase1 5120 rc=$? $(date)"
jq '.providers.halogen.models[0].maxTokens = 32768' "$M" > "$M.tmp" && mv "$M.tmp" "$M"
./run-game-bench-v2.sh manual-halogen070gguf-med-b-v2_12 halogen070gguf-med-b-v2_12 medium "$P12"
echo "=== phase1 v2_12 rc=$? $(date)"
touch "$MARK"
