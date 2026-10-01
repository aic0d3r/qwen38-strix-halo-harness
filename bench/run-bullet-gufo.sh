#!/usr/bin/env bash
# Bulletproofing chain, gufo arm: v3 x2, greedy control, high x2.
set -u
H=/home/ezflow/coding/qwen38-strix-halo-harness/bench
L=/home/ezflow/LLMBench/results/qwen38-27b/game-ladder
MARK=$L/bulgufo.done
rm -f "$MARK"
cd "$H" || exit 1
if ! curl -sf -m 3 "http://127.0.0.1:8080/v1/models" >/dev/null 2>&1; then
  echo "PRE-FLIGHT FAIL: no gufo server on :8080 - start it first" >&2
  exit 1
fi
echo "pre-flight OK: gufo healthy on :8080"
export PROVIDER=llamacpp MODEL=qwen3.8-flash-next PORT=8080
P12="$(cat "$L/prompt-multi-v2_12.txt")"
P3="$(cat "$L/prompt-multi-v3.txt")"
./run-game-bench-v2.sh manual-gufo-med-b-v3 gufo-med-b-v3 medium "$P3"
echo "=== bul gufo v3-b rc=$? $(date)"
./run-game-bench-v2.sh manual-gufo-med-c-v3 gufo-med-c-v3 medium "$P3"
echo "=== bul gufo v3-c rc=$? $(date)"
GREEDY=1 ./run-game-bench-v2.sh manual-gufo-med-b-greedy gufo-med-b-greedy medium "$P12"
echo "=== bul gufo greedy rc=$? $(date)"
./run-game-bench-v2.sh manual-gufo-high-c-v2_12 gufo-high-c-v2_12 high "$P12"
echo "=== bul gufo high-c rc=$? $(date)"
./run-game-bench-v2.sh manual-gufo-high-d-v2_12 gufo-high-d-v2_12 high "$P12"
echo "=== bul gufo high-d rc=$? $(date)"
touch "$MARK"
