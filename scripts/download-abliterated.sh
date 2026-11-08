#!/usr/bin/env bash
# Fetch the abliterated weights the launchers use when the saved weights choice is "abliterated"
# (docs/ENGINES.md#abliterated-models). Resumable; asks first unless --yes; exits 1 on abort/failure.
#   scripts/download-abliterated.sh halogen|gufo [--yes]
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
. "$REPO/scripts/lib-hf.sh"
ENG=${1:-}
case "$ENG" in
  halogen)
    STOCK=$(bash "$REPO/server/start-halogen.sh" --print-stock)
    [ -n "$STOCK" ] || { echo "  the stock halogen files come first (tokenizer, n-gram table, npu/): scripts/download-halogen.sh" >&2; exit 1; }
    DIR=$(dirname "$STOCK"); GB=89; WANT="$DIR/qwen38-flash-next-v2-abliterated.hgn"
    # r2: the release with published quality/speed results; the newer main file is not inference-validated
    ARGS=(Quat3rnion/halogen-qwen3.8-flash-next-v2-abliterated qwen38-flash-next-v2-abliterated.hgn
          --revision e7783c5822dfda2785a0f5b435abf54df89fe8ee) ;;
  gufo)
    WANT=$(GUFO_WEIGHTS=abliterated bash "$REPO/server/start-gufo-flashnext.sh" --print-model)
    DIR=$(dirname "$WANT"); GB=117  # 112 weights + the MTP head and vision projector if missing
    ARGS=(brainnxdomain/Qwen3.8-Flash-Next-Uncensored-Gufo-GGUF --include '*.gguf')
    ROOT=$(dirname "$DIR"); MTP=mtp-Qwen3.8-Flash-Next-shared-Q8_0.gguf ;;
  *) echo "usage: $0 halogen|gufo [--yes]" >&2; exit 1 ;;
esac
# gufo also needs the shared MTP head beside the weights folder (the launcher's --mtp-model)
[ -f "$WANT" ] && { [ "$ENG" != gufo ] || [ -f "$ROOT/$MTP" ]; } && { echo "  already there: $WANT"; exit 0; }
[ -f "$WANT" ] && GB=5  # weights there, only the gufo sidecars left
mkdir -p "$DIR"
AVAIL=$(df -BG --output=avail "$DIR" 2>/dev/null | tail -1 | tr -dc '0-9')
echo "  required download: ~$GB GB into $DIR${AVAIL:+ (free: ${AVAIL} GB)}"
if [ "${2:-}" != --yes ]; then
  read -r -p "  download now? [y/N] " A
  case "$A" in [yY]*) ;; *) echo "  aborted" >&2; exit 1 ;; esac
fi
# a resume only needs the part still missing; $DIR also holds the stock files, so count just hf partials
HAVE_GB=$(du -scBG "$DIR"/.cache/huggingface/download/*.incomplete 2>/dev/null | tail -1 | cut -f1 | tr -dc 0-9)
NEED=$((GB + 5 - ${HAVE_GB:-0}))
[ "${AVAIL:-999}" -lt "$NEED" ] && { echo "  only ${AVAIL} GB free - free up at least $NEED GB first" >&2; exit 1; }
HF_BIN="$(command -v hf || command -v huggingface-cli || ls "$HOME/.hf-venv/bin/hf" 2>/dev/null || true)"
if [ -z "$HF_BIN" ]; then
  echo "  no hf cli found - bootstrapping a venv (~40 MB) ..."
  python3 -m venv "$HOME/.hf-venv" 2>/dev/null && "$HOME/.hf-venv/bin/pip" install -q -U huggingface_hub 2>/dev/null \
    && HF_BIN="$HOME/.hf-venv/bin/hf" || { echo "  could not bootstrap the hf downloader - install huggingface_hub and re-run" >&2; exit 1; }
fi
echo "  downloading (resumable - re-run if interrupted) ..."
if ! hf_dl "${ARGS[@]}" --local-dir "$DIR"; then
  [ "$ENG" = gufo ] && echo "  the gufo repo is gated: accept its terms on huggingface.co/brainnxdomain/Qwen3.8-Flash-Next-Uncensored-Gufo-GGUF, then: $HF_BIN auth login" >&2
  echo "  download failed/incomplete - re-run to resume" >&2; exit 1
fi
# gufo sidecars from the stock repo when this box never had the stock gufo setup: MTP head + vision projector
if [ "$ENG" = gufo ]; then
  if [ ! -f "$ROOT/$MTP" ]; then
    hf_dl unsloth/Qwen3.8-Flash-Next-GGUF "MTP/$MTP" --local-dir "$ROOT" && mv "$ROOT/MTP/$MTP" "$ROOT/$MTP" \
      || { echo "  MTP head download failed - re-run to resume" >&2; exit 1; }
  fi
  [ -f "$ROOT/mmproj-BF16.gguf" ] || hf_dl unsloth/Qwen3.8-Flash-Next-GGUF mmproj-BF16.gguf --local-dir "$ROOT" \
    || echo "  vision projector download failed (text still works) - re-run to retry" >&2
fi
# gufo finds the vision projector beside the target: link the stock one in (same disk, no copy)
if [ "$ENG" = gufo ] && [ -f "$DIR/../mmproj-BF16.gguf" ] && [ ! -e "$DIR/mmproj-BF16.gguf" ]; then
  ln "$DIR/../mmproj-BF16.gguf" "$DIR/mmproj-BF16.gguf" 2>/dev/null || true
fi
[ -f "$WANT" ] || { echo "  download finished but $WANT is missing" >&2; exit 1; }
echo "  download complete"
