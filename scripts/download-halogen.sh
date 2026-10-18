#!/usr/bin/env bash
# Fetch exactly the halogen file set (~124 GB, resumable) into ~/models/halogen-qwen3.8-flash-next.
#   scripts/download-halogen.sh [--yes]   asks first unless --yes; exits 1 on abort/failure
set -u
DL_DIR="$HOME/models/halogen-qwen3.8-flash-next"
AVAIL=$(df -BG --output=avail "$HOME" 2>/dev/null | tail -1 | tr -dc '0-9')
echo "  required download: ~124 GB into $DL_DIR${AVAIL:+ (free: ${AVAIL} GB)}"
if [ "${1:-}" != --yes ]; then
  read -r -p "  download now? [y/N] " A
  case "$A" in [yY]*) ;; *) echo "  aborted - download the files (see README 'Minimum download') or pass a checkpoint: ./setup.sh --halogen /path/to/checkpoint.hgn" >&2; exit 1 ;; esac
fi
if [ "${AVAIL:-999}" -lt 135 ]; then
  echo "  only ${AVAIL} GB free - free up at least 135 GB first" >&2; exit 1
fi
HF_BIN="$(command -v hf || command -v huggingface-cli || true)"
if [ -z "$HF_BIN" ]; then
  echo "  no hf cli found - bootstrapping a venv (~40 MB) ..."
  python3 -m venv "$HOME/.hf-venv" 2>/dev/null && "$HOME/.hf-venv/bin/pip" install -q -U huggingface_hub 2>/dev/null \
    && HF_BIN="$HOME/.hf-venv/bin/hf" || { echo "  could not bootstrap the hf downloader - install huggingface_hub (pipx or a venv) and re-run" >&2; exit 1; }
fi
mkdir -p "$DL_DIR"
echo "  downloading (resumable - re-run if interrupted) ..."
"$HF_BIN" download peonist-ai/halogen-qwen3.8-flash-next \
  qwen38-flash-next-v2.hgn qwen38-flash-next-ngram.hgn qwen38-flash-next-vision.hgn tokenizer/ \
  --local-dir "$DL_DIR" || { echo "  download failed/incomplete - re-run to resume" >&2; exit 1; }
# the NPU models live in four separate repos (they are NOT in the main weights repo)
for n in decider-0.8b qwen3-embedding-0.6b qwen3-reranker-0.6b qwen3guard-gen-0.6b; do
  [ -d "$DL_DIR/npu/$n" ] && continue
  echo "  downloading NPU model: $n ..."
  "$HF_BIN" download "peonist-ai/halogen-npu-$n" --local-dir "$DL_DIR/npu/$n" \
    || { echo "  NPU model download failed: $n - re-run to resume" >&2; exit 1; }
done
[ -f "$DL_DIR/qwen38-flash-next-v2.hgn" ] || { echo "  download finished but v2.hgn is missing from $DL_DIR" >&2; exit 1; }
echo "  download complete"
