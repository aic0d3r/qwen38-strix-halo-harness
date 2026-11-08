#!/usr/bin/env bash
# gufo (version: GUFO_VERSION below) serving Qwen3.8-Flash-Next, UD-Q4_K_XL unless the hardware pick says otherwise (0.12 dropped UD-IQ4_XS).
# One big engine at a time: STOP halogen first (docker rm -f the container) or
# the two engines fight over the same 109GB heap and device-lost follows.
# Spec decode: native MTP via the -shared- sidecar (the one gufo's docs name).
set -u
ENGINE_DIR=${ENGINE_DIR:-$HOME/LLMBench/models/qwen38-flash-next}
# model family: GUFO_FAMILY, else the wizard's saved model answer (harness-model; qwen38-27b is the only other one gufo
# loads). --print-model ignores the saved answer: its callers (download scripts, Strata install) ask about Flash-Next.
FAMILY="${GUFO_FAMILY:-}"
[ -z "$FAMILY" ] && [ "${1:-}" != --print-model ] && FAMILY=$(cat "${PI_CODING_AGENT_DIR:-${PI_HOME:-$HOME/.pi}/agent}/harness-model" 2>/dev/null)
if [ "$FAMILY" = qwen38-27b ]; then
  # dense 27B + the DFlash2 draft (gufo's docs/models page); files from scripts/download-qwen38-27b.sh
  MDIR="${GUFO_27B_DIR:-$(dirname "$ENGINE_DIR")/qwen38-27b}"
  MODEL_REL=Qwen3.8-27B-UD-Q4_K_XL.gguf; DRAFT=Qwen3.8-27B-DFlash2-Q4_K_M.gguf  # DRAFT: keep in step with G27_DRAFT in setup/harness.py
  [ "${1:-}" = --print-model ] && { echo "$MDIR/$MODEL_REL"; exit 0; }
  for f in "$MODEL_REL" "$DRAFT"; do
    [ -f "$MDIR/$f" ] || { echo "gufo 27B file missing: $MDIR/$f - get it: scripts/download-qwen38-27b.sh" >&2; exit 1; }
  done
  NAME=qwen3.8-27b; SPEC=(--speculative dflash2 --dflash-model "/models/$DRAFT")
else
# default target: the wizard's saved answer (stock|abliterated); GUFO_WEIGHTS overrides it, GUFO_MODEL
# (relative to ENGINE_DIR, first shard) names any other file. docs/ENGINES.md#abliterated-models
WEIGHTS="${GUFO_WEIGHTS:-$(cat "${PI_CODING_AGENT_DIR:-${PI_HOME:-$HOME/.pi}/agent}/harness-weights" 2>/dev/null)}"
# stock quant: GUFO_QUANT, else the wizard's saved hardware pick (harness-quant), else UD-Q4_K_XL. Shard counts are
# Unsloth's (tests/test_harness.py checks them against config/profiles.json). Strata only knows UD-Q4_K_XL.
QUANT="${GUFO_QUANT:-$(cat "${PI_CODING_AGENT_DIR:-${PI_HOME:-$HOME/.pi}/agent}/harness-quant" 2>/dev/null)}"
QUANT=${QUANT:-UD-Q4_K_XL}
case "$QUANT" in
  UD-Q4_K_XL|UD-Q3_K_XL|UD-IQ3_XXS) N=4 ;; UD-Q5_K_XL) N=6 ;;
  *) echo "unknown GUFO_QUANT '$QUANT' (UD-Q4_K_XL, UD-Q3_K_XL, UD-IQ3_XXS, UD-Q5_K_XL)" >&2; exit 1 ;;
esac
DEF=$QUANT/Qwen3.8-Flash-Next-$QUANT-00001-of-0000$N.gguf
[ "$WEIGHTS" = abliterated ] && DEF=orca-uncensored/Qwen3.8-Flash-Next-Uncensored-Gufo-00001-of-00005.gguf
GUFO_MODEL=${GUFO_MODEL:-$DEF}
[ "${1:-}" = --print-model ] && { echo "$ENGINE_DIR/$GUFO_MODEL"; exit 0; }
if [ ! -f "$ENGINE_DIR/$GUFO_MODEL" ]; then
  echo "gufo target missing: $ENGINE_DIR/$GUFO_MODEL" >&2
  [ "$GUFO_MODEL" = "$DEF" ] && [ "$WEIGHTS" = abliterated ] \
    && echo "get it: scripts/download-abliterated.sh gufo   (or run stock: GUFO_WEIGHTS=stock $0)" >&2
  exit 1
fi
MTP=mtp-Qwen3.8-Flash-Next-shared-Q8_0.gguf  # without it gufo fails the load but never exits (port stays dark)
if [ ! -f "$ENGINE_DIR/$MTP" ]; then
  echo "gufo MTP head missing: $ENGINE_DIR/$MTP" >&2
  if [ "$WEIGHTS" = abliterated ]; then echo "get it: scripts/download-abliterated.sh gufo" >&2
  else echo "get it: hf download unsloth/Qwen3.8-Flash-Next-GGUF MTP/$MTP --local-dir $ENGINE_DIR && mv $ENGINE_DIR/MTP/$MTP $ENGINE_DIR/" >&2; fi
  exit 1
fi
MDIR=$ENGINE_DIR; MODEL_REL=$GUFO_MODEL; NAME=qwen3.8-flash-next; SPEC=(--speculative mtp --mtp-model "/models/$MTP")
fi
# pre-flight: gufo at 65k has never wedged, but at large ctx the same allocator
# physics as halogen applies. recover mild fragmentation, fail fast otherwise.
CTX="${GUFO_CTX:-65536}"
O9=$(awk '$4=="Normal"{print $(NF-1)}' /proc/buddyinfo 2>/dev/null | head -1)
if [ "$CTX" -gt 65536 ] && [ -n "$O9" ] && [ "$O9" -lt 1000 ]; then
  sudo -n sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches; echo 1 > /proc/sys/vm/compact_memory' 2>/dev/null || true
  sleep 5; O9=$(awk '$4=="Normal"{print $(NF-1)}' /proc/buddyinfo 2>/dev/null | head -1)
  if [ -z "$O9" ] || [ "$O9" -lt 1000 ]; then
    echo "order-9 = ${O9:-?} <1000: large-ctx gufo would likely wedge. fix: sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches; echo 1 > /proc/sys/vm/compact_memory' (or reboot)." >&2
    exit 1
  fi
fi
# pinned like halogen (0.12.0 measured in docs/ENGINES.md); scripts/upgrade-gufo.sh moves both lines
GUFO_VERSION=0.31.0  # the tag the digest below was pulled as; ./harness update-check compares against it
GUFO_IMAGE=${GUFO_IMAGE:-ghcr.io/gufo-org/toolboxes/gufo-runtime@sha256:9a6e2d43ca00d8d1c89453ebf456e975fd197ef54d648ba0d1b4a8bcc0917f24}
PORT=${PORT:-8080}
RENDER_GID=$(getent group render | cut -d: -f3); VIDEO_GID=$(getent group video | cut -d: -f3)
exec docker run --rm -i \
  --device /dev/kfd --device /dev/dri \
  --group-add "${RENDER_GID:-44}" --group-add "${VIDEO_GID:-99}" \
  --security-opt seccomp=unconfined --ipc=host --ulimit memlock=-1:-1 \
  -v "$MDIR":/models:ro \
  -p 127.0.0.1:$PORT:$PORT \
  "$GUFO_IMAGE" \
  gufo serve llm \
    -m "/models/$MODEL_REL" \
    --served-model-name "$NAME" \
    "${SPEC[@]}" \
    --think auto \
    -c "$CTX" -j 2 \
    --log-progress \
    -i 0.0.0.0 -p $PORT
