#!/usr/bin/env bash
# Ling-3.0-tiny aux server (compaction/triage/repomap summaries) on :8090.
# Same engine build as the main server. Chat endpoint + template is mandatory
# for this model (raw /completion degenerates); the extensions use chat.
set -euo pipefail
ENGINE_DIR=${ENGINE_DIR:?set ENGINE_DIR to your llama-server build dir}
MODEL_DIR=${MODEL_DIR:?set MODEL_DIR to the dir holding Ling-3.0-tiny-Q4_K_M.gguf}
# ubatch knob shared with /tune (harness-ubatch). Default 4096: the measured
# 2048 deep-fill winner is from 27B decode benches, never measured on tiny;
# 4096 covers typical summarization payloads in one prefill batch.
UBBATCH=${UBBATCH:-$(cat "$HOME/.pi/agent/harness-ubatch" 2>/dev/null || echo 4096)}
exec $ENGINE_DIR/llama-server \
  -a ling3.0-tiny \
  -m $MODEL_DIR/Ling-3.0-tiny-Q4_K_M.gguf \
  -ngl all -fa on -c 131072 -np 1 -b $UBBATCH -ub $UBBATCH \
  -t 16 -tb 32 --jinja \
  --host 127.0.0.1 --port 8090 --metrics
