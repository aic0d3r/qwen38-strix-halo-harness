#!/usr/bin/env bash
# Qwen3.8-27B (UD-Q5_K_XL v2, pinned revision) + DFlash2 drafter on Strix Halo (gfx1151).
# 256k ctx. Ubatch: 4096 for normal work; set UBBATCH=2048 (or 1024) for deliberate
# deep fills past ~128k — ub4096 hits a deterministic device-lost past ~140k (2/2),
# ub2048 probed clean to 138.8k, ub1024 proven on a real 144k session.
#
# Template: config/template/sharp-v22.3.2.jinja (vendored; also ships v22.1, which every
# published benchmark number was measured on). v22.3.x adds: reasoning-effort steering
# via kwargs (aliases: minimal/low->low, high/xhigh/max/ultracode->xhigh), inline tags
# (<|think_off|>, <|think_low|>, <|think_xhigh|>, ...), thinking-off fast-mode fixes,
# preserve_reasoning, optional truncation kwargs (max_tool_arg_chars /
# max_tool_response_chars, default off), and terse:false to disable the terseness block.
#
# --reasoning-preserve keeps the model's thinking trace in history across turns (the
# template supports it; the flag makes the server retain instead of strip). Also available:
# --reasoning-budget N as a server-side hard thinking cap (-1 unrestricted, 0 off, N>0 cap).
#
# Requires: Nathan's strix-halo-llamacpp v0.6.5+ (DFlash2); v0.7.4 recommended
# (greedy repeatability: stale-KV between requests + a top-k race above ~2k prompt
# tokens, both also in upstream, fixed there; throughput at parity). The bundled
# RADV from the v0.7.x payload beats system Mesa on dense prefill — point
# DRIVER_DIR at it if you have it.
#
# Target quant: default is UD-Q4_K_XL-v3 (current daily driver; the Q4-Q8 PPL plateau
# is flat 7.079-7.089 and build output is identical across the tier). Override with
# TARGET_GGUF to run the pinned Q5_K_XL v2 that the published era numbers used.
set -euo pipefail

ENGINE_DIR=${ENGINE_DIR:?set ENGINE_DIR to your llama-server build dir}
MODEL_DIR=${MODEL_DIR:?set MODEL_DIR to the dir holding the target+drafter GGUFs}
TEMPLATE=${TEMPLATE:-$(cd "$(dirname "$0")/../config/template" && pwd)/sharp-v22.3.2.jinja}
TARGET_GGUF=${TARGET_GGUF:-Qwen3.8-27B-UD-Q4_K_XL-v3.gguf}
# UBBATCH default comes from /tune (pi extension) if set; 4096 is fastest at bench
# depths, use 2048 for deliberate deep fills (ceiling notes in header).
# Reasoning-preserve (default OFF): retaining thinking traces across turns ~doubles
# context growth on build-shaped agent work (measured: game build hit 91.7k ctx vs
# ~25-45k for identical builds without it). Opt in with REASONING_PRESERVE=1 if your
# workload benefits from cross-turn reasoning continuity.
REASONING_PRESERVE_FLAGS=${REASONING_PRESERVE:+--reasoning-preserve}
UBBATCH=${UBBATCH:-$(cat "$HOME/.pi/agent/harness-ubatch" 2>/dev/null || echo 4096)}
DRIVER_DIR=${DRIVER_DIR:-}   # optional: dir with radeon_icd.x86_64.json + bundled RADV

if [ -n "$DRIVER_DIR" ]; then
  export VK_ICD_FILENAMES=$DRIVER_DIR/radeon_icd.x86_64.json
  export VK_DRIVER_FILES=$DRIVER_DIR/radeon_icd.x86_64.json
  export LD_LIBRARY_PATH=$DRIVER_DIR:$ENGINE_DIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
fi

exec $ENGINE_DIR/llama-server \
  -a qwen3.8-27b \
  -m $MODEL_DIR/$TARGET_GGUF \
  -md $MODEL_DIR/Qwen3.8-27B-DFlash2-Q4_K_M.gguf \
  -ngl all -ngld all -fa on \
  -ctk f16 -ctv f16 -ctkd q8_0 -ctvd q8_0 \
  -c 262144 -np 1 -b 4096 -ub $UBBATCH \
  -t 16 -tb 32 \
  --spec-type draft-dflash --spec-draft-n-max 4 \
  $REASONING_PRESERVE_FLAGS \
  --chat-template-file $TEMPLATE \
  --jinja --host 127.0.0.1 --port 8080 --metrics
