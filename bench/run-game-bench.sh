#!/usr/bin/env bash
# CANONICAL game-build bench: FULL harness (extensions on, repomap auto-fire, triage,
# compaction-at-95k) - per user directive this is the only valid way to bench builds.
# Server: tilefix clean binary, ub from /tune persisted value (default 4096), v22.3.2.
L=~/LLMBench/results/qwen38-27b/game-ladder
DIR=$1 NAME=$2 LVL=${3:-medium}
mkdir -p "$L/$DIR" && cd "$L/$DIR" && git init -q 2>/dev/null
for TRY in 1 2 3 4 5; do
  P="$(cat "$L/prompt-multi.txt") [attempt $TRY $(date +%s)]"
  T0=$(date +%s)
  timeout 5400 pi -p --provider llamacpp/qwen3.8-27b --thinking "$LVL" --name "$NAME" "$P" > "$L/run-$NAME.txt" 2>&1
  W=$(( ($(date +%s)-T0)/60 ))
  if [ "$(find "$L/$DIR" -name '*.js' -o -name '*.html' -o -name '*.css' 2>/dev/null | wc -l)" -gt 0 ]; then
    echo "=== $NAME try=$TRY SUCCESS wall=${W}min (full harness) ===" >> "$L/ladder.log"; exit 0
  fi
  echo "=== $NAME try=$TRY degenerate, retry ===" >> "$L/ladder.log"; sleep 3
done
echo "=== $NAME FAILED ===" >> "$L/ladder.log"
