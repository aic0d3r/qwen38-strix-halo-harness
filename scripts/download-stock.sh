#!/usr/bin/env bash
# Fetch the stock weights gufo and Strata share: Unsloth's UD-Q4_K_XL GGUF (4 shards, the last one is the n-gram
# table) from unsloth/Qwen3.8-Flash-Next-GGUF, plus gufo's shared Q8 MTP head and the vision projector when missing.
# Default revision is Unsloth Dynamic v3.1 commit d9620119 (the one gufo 0.31.0 and Strata 0.2.11+ name; `main` has
# moved on since); UNSLOTH_REV=main or another commit overrides. The commit actually fetched is written to <engine dir>/UD-Q4_K_XL/.unsloth-revision. Resumable; asks first
# unless --yes; exits 1 on abort/failure. Not gated.
#   scripts/download-stock.sh [--yes]        folder: ENGINE_DIR, else ~/LLMBench/models/qwen38-flash-next
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
WANT=$(GUFO_WEIGHTS=stock bash "$REPO/server/start-gufo-flashnext.sh" --print-model)  # .../UD-Q4_K_XL/<shard 1>
DIR=$(dirname "$WANT"); ROOT=$(dirname "$DIR")
MTP=mtp-Qwen3.8-Flash-Next-shared-Q8_0.gguf
SHARDS=4; GB=112                    # 103.7 GiB of shards; the MTP head (2.6 GB) and projector (0.9 GB) come on top
HAVE=$(ls "$DIR"/Qwen3.8-Flash-Next-UD-Q4_K_XL-0000?-of-0000$SHARDS.gguf 2>/dev/null | wc -l)
if [ "$HAVE" = "$SHARDS" ] && [ -f "$ROOT/$MTP" ] && [ -f "$ROOT/mmproj-BF16.gguf" ]; then echo "  already there: $DIR"; exit 0; fi
mkdir -p "$DIR"
AVAIL=$(df -BG --output=avail "$DIR" 2>/dev/null | tail -1 | tr -dc '0-9')
REV="${UNSLOTH_REV:-d96201194f103c906994f283f504209dc8d2a10c}"
echo "  required download: ~$GB GB into $DIR (unsloth/Qwen3.8-Flash-Next-GGUF @ $REV)${AVAIL:+ (free: ${AVAIL} GB)}"
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
R=unsloth/Qwen3.8-Flash-Next-GGUF
echo "  downloading (resumable - re-run if interrupted) ..."
"$HF_BIN" download "$R" --revision "$REV" --include 'UD-Q4_K_XL/*' --local-dir "$ROOT" \
  || { echo "  download failed/incomplete - re-run to resume" >&2; exit 1; }
if [ ! -f "$ROOT/$MTP" ]; then
  "$HF_BIN" download "$R" --revision "$REV" "MTP/$MTP" --local-dir "$ROOT" && mv "$ROOT/MTP/$MTP" "$ROOT/$MTP" \
    || { echo "  MTP head download failed - re-run to resume" >&2; exit 1; }
fi
[ -f "$ROOT/mmproj-BF16.gguf" ] || "$HF_BIN" download "$R" --revision "$REV" mmproj-BF16.gguf --local-dir "$ROOT" \
  || echo "  vision projector download failed (text still works) - re-run to retry" >&2
# gufo finds the vision projector beside the target: link the shared one in (same disk, no copy)
[ -f "$ROOT/mmproj-BF16.gguf" ] && [ ! -e "$DIR/mmproj-BF16.gguf" ] && ln "$ROOT/mmproj-BF16.gguf" "$DIR/mmproj-BF16.gguf" 2>/dev/null
# which Dynamic revision these shards are (main moves; the "preview" label is Unsloth's)
SHA=$(python3 -c "import json,urllib.request as u;print(json.load(u.urlopen('https://huggingface.co/api/models/$R/revision/$REV',timeout=15))['sha'])" 2>/dev/null)
echo "${SHA:-unknown} ($REV, $(date +%F))" > "$DIR/.unsloth-revision"
HAVE=$(ls "$DIR"/Qwen3.8-Flash-Next-UD-Q4_K_XL-0000?-of-0000$SHARDS.gguf 2>/dev/null | wc -l)
[ "$HAVE" = "$SHARDS" ] && [ -f "$WANT" ] || { echo "  download finished but $HAVE of $SHARDS shards are on disk" >&2; exit 1; }
echo "  download complete (unsloth @ ${SHA:0:8})"
