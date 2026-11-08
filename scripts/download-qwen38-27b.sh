#!/usr/bin/env bash
# Fetch what gufo needs for Qwen3.8 27B: Unsloth's UD-Q4_K_XL GGUF (16.35 GiB), the vision projector (0.87 GiB) and the
# DFlash2 draft (1.06 GiB), at the revisions gufo's docs/models page names. Resumable; asks first unless --yes; exits 1 on
# abort/failure. Not gated.
#   scripts/download-qwen38-27b.sh [--yes]     folder: GUFO_27B_DIR, else <ENGINE_DIR's parent>/qwen38-27b
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
. "$REPO/scripts/lib-hf.sh"
DIR=$(dirname "$(GUFO_FAMILY=qwen38-27b bash "$REPO/server/start-gufo-flashnext.sh" --print-model)")
TARGET=Qwen3.8-27B-UD-Q4_K_XL.gguf; DRAFT=Qwen3.8-27B-DFlash2-Q4_K_M.gguf; GB=20
REV_TARGET=4ca720788d1e01f1bff70c033e0d0028fd02e502; REV_DRAFT=2d9571f8ce46e151f61c6499c99dee6079e1d610
if [ -f "$DIR/$TARGET" ] && [ -f "$DIR/$DRAFT" ] && [ -f "$DIR/mmproj-BF16.gguf" ]; then echo "  already there: $DIR"; exit 0; fi
mkdir -p "$DIR"
AVAIL=$(df -BG --output=avail "$DIR" 2>/dev/null | tail -1 | tr -dc '0-9')
echo "  required download: ~$GB GB into $DIR (unsloth/Qwen3.8-27B-GGUF @ ${REV_TARGET:0:8}, z-lab/Qwen3.8-27B-DFlash2-GGUF @ ${REV_DRAFT:0:8})${AVAIL:+ (free: ${AVAIL} GB)}"
if [ "${1:-}" != --yes ]; then
  read -r -p "  download now? [y/N] " A
  case "$A" in [yY]*) ;; *) echo "  aborted" >&2; exit 1 ;; esac
fi
[ "${AVAIL:-999}" -lt $((GB + 5)) ] && { echo "  only ${AVAIL} GB free - free up at least $((GB + 5)) GB first" >&2; exit 1; }
HF_BIN="$(command -v hf || command -v huggingface-cli || ls "$HOME/.hf-venv/bin/hf" 2>/dev/null || true)"
if [ -z "$HF_BIN" ]; then
  echo "  no hf cli found - bootstrapping a venv (~40 MB) ..."
  python3 -m venv "$HOME/.hf-venv" 2>/dev/null && "$HOME/.hf-venv/bin/pip" install -q -U huggingface_hub 2>/dev/null \
    && HF_BIN="$HOME/.hf-venv/bin/hf" || { echo "  could not bootstrap the hf downloader - install huggingface_hub and re-run" >&2; exit 1; }
fi
echo "  downloading (resumable - re-run if interrupted) ..."
hf_dl unsloth/Qwen3.8-27B-GGUF "$TARGET" mmproj-BF16.gguf --revision "$REV_TARGET" --local-dir "$DIR" \
  && hf_dl z-lab/Qwen3.8-27B-DFlash2-GGUF "$DRAFT" --revision "$REV_DRAFT" --local-dir "$DIR" \
  || { echo "  download failed/incomplete - re-run to resume" >&2; exit 1; }
for f in "$TARGET" "$DRAFT" mmproj-BF16.gguf; do
  [ -f "$DIR/$f" ] || { echo "  download finished but $f is missing from $DIR" >&2; exit 1; }
done
echo "  download complete ($DIR)"
