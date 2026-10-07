#!/usr/bin/env bash
# gufo 0.3.0 serving Qwen3.8-Flash-Next UD-IQ4_XS (the headline-comparison arm).
# One big engine at a time: STOP halogen first (docker rm -f the container) or
# the two engines fight over the same 109GB heap and device-lost follows.
# Spec decode: native MTP via the -shared- sidecar (the one gufo's docs name).
set -u
# pre-flight: gufo at 65k has never wedged, but at large ctx the same allocator
# physics as halogen applies. recover mild fragmentation, fail fast otherwise.
CTX="${GUFO_CTX:-65536}"
O9=$(awk '$4=="Normal"{print $(NF-1)}' /proc/buddyinfo 2>/dev/null | head -1)
if [ "$CTX" != "65536" ] && [ -n "$O9" ] && [ "$O9" -lt 1000 ]; then
  sudo -n sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches; echo 1 > /proc/sys/vm/compact_memory' 2>/dev/null || true
  sleep 5; O9=$(awk '$4=="Normal"{print $(NF-1)}' /proc/buddyinfo 2>/dev/null | head -1)
  if [ -z "$O9" ] || [ "$O9" -lt 1000 ]; then
    echo "order-9 = ${O9:-?} <1000: large-ctx gufo would likely wedge. fix: sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches; echo 1 > /proc/sys/vm/compact_memory' (or reboot)." >&2
    exit 1
  fi
fi
ENGINE_DIR=${ENGINE_DIR:-$HOME/LLMBench/models/qwen38-flash-next}
GUFO_IMAGE=${GUFO_IMAGE:-ghcr.io/gufo-org/toolboxes/gufo-runtime:latest}
PORT=${PORT:-8080}
RENDER_GID=$(getent group render | cut -d: -f3); VIDEO_GID=$(getent group video | cut -d: -f3)
exec docker run --rm -i \
  --device /dev/kfd --device /dev/dri \
  --group-add "${RENDER_GID:-44}" --group-add "${VIDEO_GID:-99}" \
  --security-opt seccomp=unconfined --ipc=host --ulimit memlock=-1:-1 \
  -v "$ENGINE_DIR":/models:ro \
  -p 127.0.0.1:$PORT:$PORT \
  "$GUFO_IMAGE" \
  gufo serve llm \
    -m /models/UD-Q4_K_XL/Qwen3.8-Flash-Next-UD-Q4_K_XL-00001-of-00004.gguf \
    --served-model-name qwen3.8-flash-next \
    --speculative mtp \
    --mtp-model /models/mtp-Qwen3.8-Flash-Next-shared-Q8_0.gguf \
    --think auto \
    -c 65536 -j 2 \
    --log-progress \
    -i 0.0.0.0 -p $PORT
