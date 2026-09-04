#!/usr/bin/env bash
# Qwen3.8-Flash-Next 125B-A6B (UD-IQ4_XS) + native MTP Q8_0 sidecar — the speed lane.
# ~353 t/s prefill, 30-40 t/s sustained decode on agent traffic (peak 48.7) at 70-80W.
#
# THE REASONING FLAGS ARE MANDATORY. Without --reasoning-effort/--reasoning-budget,
# Flash-Next burns its entire output budget on thinking and emits nothing visible
# (verified: zero-content completions with thinking maxed). Keep them.
#
# Mechanism (verified from source + A/B, 2026-09-05): the server budget is the
# DEFAULT for requests that don't carry one (curl, llama-bench, raw API). Clients
# that send their own per-request budget override it — pi does, via thinkingBudgets
# (low 1024 / medium 4096 / high 8192, all validated on Flash-Next). Raising the
# budget above the tier bought nothing in A/B (8x at low effort scored WORSE,
# 14/19 vs 16/19) — this flag is burn-out protection, not a quality knob.
#
# No template file: Flash-Next uses its embedded chat template (--jinja handles the
# kwargs). Engine: Nathan's strix-halo-vulkan releases, validated v0.6.11 through
# v0.7.4.1 (0.7.4+ = throughput parity + greedy repeatability fixes; the release
# payload's bundled RADV beats system Mesa on prefill).
set -euo pipefail

ENGINE_DIR=${ENGINE_DIR:?set ENGINE_DIR to your llama-server build dir}
MODEL_DIR=${MODEL_DIR:?set to the dir holding the Flash-Next + MTP sidecar GGUFs}
TARGET_GGUF=${TARGET_GGUF:-Qwen3.8-Flash-Next-UD-IQ4_XS.gguf}   # multi-shard: point at shard 1
DRAFT_GGUF=${DRAFT_GGUF:-Qwen3.8-Flash-Next-mtp-Q8_0.gguf}
N_MAX=${N_MAX:-4}          # 4 is the sweet spot at >=8k ctx
CTX=${CTX:-65536}
RB=${RB:-2048}             # default budget for budget-less clients; pi's per-request tiers override
DRIVER_DIR=${DRIVER_DIR:-}

if [ -n "$DRIVER_DIR" ]; then
  export VK_ICD_FILENAMES=$DRIVER_DIR/radeon_icd.x86_64.json
  export VK_DRIVER_FILES=$DRIVER_DIR/radeon_icd.x86_64.json
  export LD_LIBRARY_PATH=$DRIVER_DIR:$ENGINE_DIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
fi

exec $ENGINE_DIR/llama-server \
  -a qwen3.8-flash-next \
  -m $MODEL_DIR/$TARGET_GGUF \
  -md $MODEL_DIR/$DRAFT_GGUF --spec-type draft-mtp \
  --spec-draft-n-max $N_MAX \
  -ngl all -fa on -ctk q8_0 -ctv q8_0 \
  -c $CTX -np 1 -b 2048 -ub 2048 \
  -t 16 -tb 32 \
  --reasoning-effort medium --reasoning-budget $RB \
  --jinja --fit off \
  --host 127.0.0.1 --port 8080 --metrics
