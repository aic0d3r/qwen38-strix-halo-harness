#!/usr/bin/env bash
# Install Strata (github.com/Niko1221/Strata) from source the way its docs/SOURCE_INSTALL.md describes (git clone,
# then its own ./setup.sh), with the model for the saved weights answer (./harness config -> weights):
#   abliterated -> gufo's orca-uncensored shards used in place (--gguf-dir --model UD-Q4_K_XL: same tensor layout;
#                  Strata checks own files for completeness only). Never downloads weights: missing -> fails.
#   stock       -> UD-IQ4_XS (~94 GB download, resumable), Strata's own pick for Strix Halo.
# Each model gets its own run-unsloth-<model>.sh; server/start-strata.sh starts the one for the saved answer.
#   scripts/install-strata.sh [--yes]   asks first unless --yes; folder: $STRATA_DIR, else ~/coding/Strata
#   STRATA_WEIGHTS=stock|abliterated overrides the saved answer
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
[ -n "$(bash "$REPO/server/start-strata.sh" --print-run)" ] && { echo "  Strata already installed for these weights"; exit 0; }
HAVE=$(bash "$REPO/server/start-strata.sh" --print-dir)
DIR=${HAVE:-${STRATA_DIR:-$HOME/coding/Strata}}
WEIGHTS="${STRATA_WEIGHTS:-$(cat "${PI_CODING_AGENT_DIR:-${PI_HOME:-$HOME/.pi}/agent}/harness-weights" 2>/dev/null)}"
GGUF=()
if [ "$WEIGHTS" = abliterated ]; then
  ORCA=$(GUFO_WEIGHTS=abliterated bash "$REPO/server/start-gufo-flashnext.sh" --print-model)
  [ -f "$ORCA" ] || { echo "  abliterated weights not on disk ($ORCA) - get them: scripts/download-abliterated.sh gufo" >&2; exit 1; }
  MODEL=UD-Q4_K_XL; GGUF=(--gguf-dir "$(dirname "$ORCA")"); WHAT="uses $(dirname "$ORCA") (no weights download)"
else MODEL=UD-IQ4_XS; WHAT="downloads $MODEL (~94 GB)"; fi
AVAIL=$(df -BG --output=avail "$(dirname "$DIR")" 2>/dev/null | tail -1 | tr -dc '0-9')
echo "  Strata ($WEIGHTS weights): $([ -n "$HAVE" ] && echo "in $HAVE, " || echo "clone into $DIR, ")then its setup fetches the engine and $WHAT${AVAIL:+ (free: ${AVAIL} GB)}"
if [ "${1:-}" != --yes ]; then
  read -r -p "  install now? [y/N] " A
  case "$A" in [yY]*) ;; *) echo "  aborted" >&2; exit 1 ;; esac
fi
[ ${#GGUF[@]} = 0 ] && [ "${AVAIL:-999}" -lt 100 ] && { echo "  only ${AVAIL} GB free - free up at least 100 GB first" >&2; exit 1; }
if [ ! -f "$DIR/setup.sh" ]; then
  mkdir -p "$(dirname "$DIR")"
  git clone https://github.com/Niko1221/Strata.git "$DIR" || { echo "  git clone failed" >&2; exit 1; }
fi
cd "$DIR" || exit 1
# Strata compiles its HIP engine with $ROCM/llvm/bin/clang++; TheRock 10.x system ROCm (rocm-gfx1151-bin) keeps it in
# lib/llvm, so cmake fails. A ROCM_PATH without hipcc makes Strata use its pinned gfx1151 wheels in .venv (~10 GB, no sudo).
# shortcut: drop this once Strata finds lib/llvm/bin/clang++ itself
SYS=${ROCM_PATH:-/opt/rocm}
if [ -x "$SYS/bin/hipcc" ] && [ ! -x "$SYS/llvm/bin/clang++" ]; then
  echo "  system ROCm in $SYS has no llvm/bin/clang++ (the layout Strata builds with): using Strata's own ROCm wheels"
  export ROCM_PATH="$DIR/.no-system-rocm"
  grep -qs "$SYS/llvm/bin/clang++" build-hip/CMakeCache.txt && rm -rf build-hip  # the failed configure's cache
fi
# --model also makes a later run install that model instead of just starting the installed one
BROWSER=true sh ./setup.sh --yes --no-start --family unsloth --model "$MODEL" "${GGUF[@]}" < /dev/null \
  || { echo "  Strata setup failed - re-run to resume, or run ./setup.sh in $DIR to see its questions" >&2; exit 1; }
[ -n "$(STRATA_WEIGHTS=$WEIGHTS bash "$REPO/server/start-strata.sh" --print-run)" ] \
  || { echo "  Strata setup finished but wrote no run-unsloth-${MODEL,,}.sh in $DIR" >&2; exit 1; }
echo "  Strata installed in $DIR ($MODEL)"
