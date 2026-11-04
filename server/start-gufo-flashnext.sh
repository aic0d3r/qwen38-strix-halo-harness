#!/usr/bin/env bash
# gufo (version: GUFO_VERSION below) serving Qwen3.8-Flash-Next UD-Q4_K_XL (0.12 dropped UD-IQ4_XS).
# One big engine at a time: STOP halogen first (docker rm -f the container) or
# the two engines fight over the same 109GB heap and device-lost follows.
# Spec decode: native MTP via the -shared- sidecar (the one gufo's docs name).
set -u
ENGINE_DIR=${ENGINE_DIR:-$HOME/LLMBench/models/qwen38-flash-next}
# default target: the wizard's saved answer (stock|abliterated); GUFO_WEIGHTS overrides it, GUFO_MODEL
# (relative to ENGINE_DIR, first shard) names any other file. docs/ENGINES.md#abliterated-models
WEIGHTS="${GUFO_WEIGHTS:-$(cat "${PI_CODING_AGENT_DIR:-${PI_HOME:-$HOME/.pi}/agent}/harness-weights" 2>/dev/null)}"
DEF=UD-Q4_K_XL/Qwen3.8-Flash-Next-UD-Q4_K_XL-00001-of-00004.gguf
[ "$WEIGHTS" = abliterated ] && DEF=orca-uncensored/Qwen3.8-Flash-Next-Uncensored-Gufo-00001-of-00005.gguf
GUFO_MODEL=${GUFO_MODEL:-$DEF}
[ "${1:-}" = --print-model ] && { echo "$ENGINE_DIR/$GUFO_MODEL"; exit 0; }
if [ ! -f "$ENGINE_DIR/$GUFO_MODEL" ]; then
  echo "gufo target missing: $ENGINE_DIR/$GUFO_MODEL" >&2
  [ "$GUFO_MODEL" = "$DEF" ] && [ "$WEIGHTS" = abliterated ] \
    && echo "get it: scripts/download-abliterated.sh gufo   (or run stock: GUFO_WEIGHTS=stock $0)" >&2
  exit 1
fi
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
# pinned like halogen (0.12.0 measured in docs/ENGINES.md); scripts/upgrade-gufo.sh moves both lines
GUFO_VERSION=0.19.0  # the tag the digest below was pulled as; ./harness update-check compares against it
GUFO_IMAGE=${GUFO_IMAGE:-ghcr.io/gufo-org/toolboxes/gufo-runtime@sha256:51be93845170ce3ce441c7399b5b2eba942baf7f3e6c9a4a3333fccecc27d190}
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
    -m "/models/$GUFO_MODEL" \
    --served-model-name qwen3.8-flash-next \
    --speculative mtp \
    --mtp-model /models/mtp-Qwen3.8-Flash-Next-shared-Q8_0.gguf \
    --think auto \
    -c "$CTX" -j 2 \
    --log-progress \
    -i 0.0.0.0 -p $PORT
