#!/usr/bin/env bash
# Install TabbyAPI (github.com/theroyallab/tabbyAPI, ExLlamaV3) and turboderp's EXL3 quant of Qwen3.8-Flash-Next,
# the way its README and the single-GPU guide do it: git clone, a Python 3.11 venv, `pip install ".[cu12]"` (prebuilt
# exllamav3 wheel, no JIT build), `hf download` of one quant branch. NVIDIA only. Stock weights only.
#   scripts/install-tabbyapi.sh [--yes] [--dry-run]   asks first unless --yes; --dry-run prints the plan and does nothing
#   folder: $TABBYAPI_DIR, else ~/coding/tabbyAPI     TABBY_QUANT: EXL3 revision (default 3.05bpw_h5_ng5, ~85 GB)
#   TABBY_CUDA: pip extra (default cu12, CUDA 12.8; the project also publishes a CUDA 13 image)
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
YES=0; DRY=0
for a in "$@"; do case "$a" in --yes) YES=1 ;; --dry-run) DRY=1 ;; esac; done
[ -n "$(bash "$REPO/server/start-tabbyapi.sh" --print-run)" ] && { echo "  TabbyAPI already installed"; exit 0; }
HAVE=$(bash "$REPO/server/start-tabbyapi.sh" --print-dir)
DIR=${HAVE:-${TABBYAPI_DIR:-$HOME/coding/tabbyAPI}}
QUANT="${TABBY_QUANT:-3.05bpw_h5_ng5}"; EXTRA="${TABBY_CUDA:-cu12}"
NAME="qwen3.8-flash-next-exl3-${QUANT%%_*}"   # keep in step with server/start-tabbyapi.sh
NEED_GB=100                                   # 85 GB quant + venv (PyTorch) + headroom
AVAIL=$(df -BG --output=avail "$(dirname "$DIR")" 2>/dev/null | tail -1 | tr -dc '0-9')
RAM_GB=$(awk '/MemTotal/{printf "%d", $2/1048576}' /proc/meminfo)
PY=$(command -v python3.11 || true)
echo "  TabbyAPI: $([ -n "$HAVE" ] && echo "in $HAVE, " || echo "clone into $DIR, ")venv + exllamav3 (.[${EXTRA}]), then EXL3 quant $QUANT (~85 GB) into models/$NAME (free: ${AVAIL:-?} GB, RAM: ${RAM_GB} GB)"
if [ "$DRY" = 1 ]; then
  echo "  dry run: python ${PY:-'3.11 via uv (python3.11 not found)'}, nvidia-smi $(command -v nvidia-smi >/dev/null && echo present || echo MISSING)"
  exit 0
fi
command -v nvidia-smi >/dev/null || { echo "  no NVIDIA GPU (nvidia-smi missing): TabbyAPI/ExLlamaV3 needs CUDA - use Strata or llama.cpp here" >&2; exit 1; }
[ "$RAM_GB" -lt 56 ] && echo "  ! ${RAM_GB} GB RAM: this quant keeps ~43 GB of experts in system RAM; under ~64 GB it swaps and decode collapses (see docs/ENGINES.md#tabbyapi)" >&2
[ "${AVAIL:-999}" -lt "$NEED_GB" ] && { echo "  only ${AVAIL} GB free - need about $NEED_GB GB" >&2; exit 1; }
if [ "$YES" != 1 ]; then
  read -r -p "  install now? [y/N] " A
  case "$A" in [yY]*) ;; *) echo "  aborted" >&2; exit 1 ;; esac
fi
if [ ! -f "$DIR/main.py" ]; then
  mkdir -p "$(dirname "$DIR")"
  git clone https://github.com/theroyallab/tabbyAPI.git "$DIR" || { echo "  git clone failed" >&2; exit 1; }
fi
cd "$DIR" || exit 1
if [ ! -x .venv/bin/python ]; then
  if [ -n "$PY" ]; then "$PY" -m venv .venv
  elif command -v uv >/dev/null; then uv venv --python 3.11 .venv
  else echo "  needs Python 3.11 (python3.11 or uv): the exllamav3 wheels are built for it" >&2; exit 1; fi
fi
# shortcut: unpinned `-U` follows the project's rolling main; pin a tag here once the harness has validated one
.venv/bin/python -m pip install -U ".[${EXTRA}]" || { echo "  pip install failed - re-run to resume" >&2; exit 1; }
.venv/bin/python -c "import torch; assert torch.cuda.is_available()" 2>/dev/null \
  || { echo "  torch cannot see the GPU (driver/CUDA mismatch?): fix that, then re-run" >&2; exit 1; }
HF=.venv/bin/hf; [ -x "$HF" ] || HF=.venv/bin/huggingface-cli
"$HF" download turboderp/Qwen3.8-Flash-Next-exl3 --revision "$QUANT" --local-dir "models/$NAME" \
  || { echo "  quant download failed - re-run to resume" >&2; exit 1; }
[ -n "$(bash "$REPO/server/start-tabbyapi.sh" --print-run)" ] \
  || { echo "  install finished but models/$NAME/config.json is missing" >&2; exit 1; }
echo "  TabbyAPI installed in $DIR ($NAME)"
