#!/usr/bin/env bash
# Ling-3.0-tiny aux server (compaction/triage/repomap summaries) on :8090.
# Same engine build as the main server. Chat endpoint + template is mandatory
# for this model (raw /completion degenerates); the extensions use chat.
set -euo pipefail
ENGINE_DIR=${ENGINE_DIR:?set ENGINE_DIR to your llama-server build dir}
MODEL_DIR=${MODEL_DIR:?set MODEL_DIR to the dir holding Ling-3.0-tiny-Q4_K_M.gguf}
exec $ENGINE_DIR/llama-server \
  -a ling3.0-tiny \
  -m $MODEL_DIR/Ling-3.0-tiny-Q4_K_M.gguf \
  -ngl all -fa on -c 131072 -np 1 -b 4096 -ub 4096 \
  -t 16 -tb 32 --jinja \
  --host 127.0.0.1 --port 8090 --metrics
