#!/usr/bin/env bash
# 0.6.1 decode battery (serial + mtp), cache off, same as the 0.4.4/0.5.4 arms.
set -u
L=/home/ezflow/LLMBench/results/halogen-cmp
cd "$L" || exit 1
MARK=$L/battery061.done
rm -f "$MARK" arm2-halogen-061-serial.jsonl arm2-halogen-061-mtp.jsonl
S=/home/ezflow/LLMBench/scripts/bench/halogen-cmp.py
P=$L/eval-prompts.json
B=http://127.0.0.1:8731
python3 "$S" --base $B --engine halogen-0.6.1 --drafter serial --prompts "$P" --extra '{"enable_thinking":false}' --out arm2-halogen-061-serial.jsonl
python3 "$S" --base $B --engine halogen-0.6.1 --drafter mtp --prompts "$P" --extra '{"enable_thinking":false}' --out arm2-halogen-061-mtp.jsonl
touch "$MARK"
echo "=== battery 0.6.1 done $(date)"
