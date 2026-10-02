#!/usr/bin/env bash
# 0.16.0+NPU validation cell: v3 prompt, medium, full pipeline, retrieval tool excluded.
set -u
H=/home/ezflow/coding/qwen38-strix-halo-harness/bench
L=/home/ezflow/LLMBench/results/qwen38-27b/game-ladder
if ! curl -sf -m 3 "http://127.0.0.1:8731/health" >/dev/null 2>&1; then
  echo "PRE-FLIGHT FAIL: no halogen on :8731" >&2; exit 1
fi
echo "pre-flight OK"
cd "$H" || exit 1
export PROVIDER=halogen MODEL=halogen-qwen3.8-flash-next PORT=8731
export EXTRA_PI_FLAGS="--exclude-tools codebase_search"
P="$(cat "$L/prompt-multi-v3.txt")"
./run-game-bench-v2.sh manual-halogen016nat-med-d-v3 halogen016nat-med-d-v3 medium "$P"
echo "=== v016 validation rc=$? $(date)"
touch "$L/v016val.done"
