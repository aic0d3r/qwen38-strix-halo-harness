#!/usr/bin/env bash
# Bulletproofing chain, halogen native arm: v3 x2, greedy control, high-c.
set -u
H=/home/ezflow/coding/qwen38-strix-halo-harness/bench
L=/home/ezflow/LLMBench/results/qwen38-27b/game-ladder
MARK=$L/bulhalogen.done
rm -f "$MARK"
cd "$H" || exit 1

# PRE-FLIGHT: never burn cell retries against a dead/absent server.
if ! curl -sf -m 3 "http://127.0.0.1:8731/health" >/dev/null 2>&1; then
  echo "PRE-FLIGHT FAIL: no healthy server on :8731 - start it first (check order-9: awk '$4=="Normal"{print $(NF-1)}' /proc/buddyinfo needs >=200)" >&2
  exit 1
fi
echo "pre-flight OK: server healthy on :8731"

export PROVIDER=halogen MODEL=halogen-qwen3.8-flash-next PORT=8731
P12="$(cat "$L/prompt-multi-v2_12.txt")"
P3="$(cat "$L/prompt-multi-v3.txt")"
./run-game-bench-v2.sh manual-halogen0151nat-med-b-v3 halogen0151nat-med-b-v3 medium "$P3"
echo "=== bul halogen v3-b rc=$? $(date)"
./run-game-bench-v2.sh manual-halogen0151nat-med-c-v3 halogen0151nat-med-c-v3 medium "$P3"
echo "=== bul halogen v3-c rc=$? $(date)"
GREEDY=1 ./run-game-bench-v2.sh manual-halogen0151nat-med-b-greedy halogen0151nat-med-b-greedy medium "$P12"
echo "=== bul halogen greedy rc=$? $(date)"
./run-game-bench-v2.sh manual-halogen0151nat-high-c-v2_12 halogen0151nat-high-c-v2_12 high "$P12"
echo "=== bul halogen high-c rc=$? $(date)"
touch "$MARK"
