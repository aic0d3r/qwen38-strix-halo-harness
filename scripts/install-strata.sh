#!/usr/bin/env bash
# Install Strata (github.com/Niko1221/Strata) from source the way its docs/SOURCE_INSTALL.md describes (git clone,
# then its own ./setup.sh), with the model for the saved weights answer (./harness config -> weights). Both run
# UD-Q4_K_XL from a GGUF folder used in place (--gguf-dir). Strata 0.2.11+ pins Unsloth's Dynamic 3.1 shards
# (UNSLOTH_Q4_V31_SHARDS, checked by size and SHA-256) and takes them from a folder you already have, while its own
# download of UD-Q4_K_XL is still the 09-30 files. It never downloads weights:
#   abliterated -> gufo's orca-uncensored shards (scripts/download-abliterated.sh gufo)
#   stock       -> Unsloth's UD-Q4_K_XL that gufo shares (scripts/download-stock.sh)
# Strata keys its repacked dense weights (packs/unsloth-ud-q4_k_xl) by model name only, so switching answers
# deletes that small pack (1.4 GB) first and Strata rebuilds it from the other GGUF; <Strata dir>/.harness-weights
# records which answer the pack was built from (server/start-strata.sh refuses a start on the other one).
# Another saved model (./harness config -> model; STRATA_MODEL overrides) is installed by Strata's own setup for that
# catalog entry (setup.sh --family F --model SIZE, config/profiles.json "catalog"), which downloads its files; the
# stock/abliterated answer does not apply to those.
#   scripts/install-strata.sh [--yes]   asks first unless --yes; folder: $STRATA_DIR, else ~/coding/Strata
#   STRATA_WEIGHTS=stock|abliterated overrides the saved answer
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
[ -n "$(bash "$REPO/server/start-strata.sh" --print-run)" ] && { echo "  Strata already installed for these weights"; exit 0; }
HAVE=$(bash "$REPO/server/start-strata.sh" --print-dir)
DIR=${HAVE:-${STRATA_DIR:-$HOME/coding/Strata}}
WEIGHTS="${STRATA_WEIGHTS:-$(cat "${PI_CODING_AGENT_DIR:-${PI_HOME:-$HOME/.pi}/agent}/harness-weights" 2>/dev/null)}"
WEIGHTS=${WEIGHTS:-stock}
AVAIL=$(df -BG --output=avail "$(dirname "$DIR")" 2>/dev/null | tail -1 | tr -dc '0-9')
# the saved model answer (./harness config -> model): a catalog id Strata's own setup downloads and installs (family + size),
# else Flash-Next on the Unsloth GGUF folder below
MODEL_ID="${STRATA_MODEL:-$(cat "${PI_CODING_AGENT_DIR:-${PI_HOME:-$HOME/.pi}/agent}/harness-model" 2>/dev/null)}"; MODEL_ID=${MODEL_ID:-flash-next}
HP="$REPO/setup/harness.py"
if FAMSIZE=$(python3 "$HP" model-info "$MODEL_ID" strata 2>/dev/null); then
  OWN=1; read -r FAM MODEL <<< "$FAMSIZE"; GB=$(python3 "$HP" model-info "$MODEL_ID" download_gb); GGUF=(); WHAT="downloads ~$GB GB itself"
  [ "${AVAIL:-999}" -lt $((GB + 10)) ] && { echo "  only ${AVAIL} GB free - $MODEL_ID needs ~$((GB + 10)) GB" >&2; exit 1; }
else
  OWN=0; FAM=unsloth
  SHARD1=$(GUFO_QUANT=UD-Q4_K_XL GUFO_WEIGHTS=$WEIGHTS bash "$REPO/server/start-gufo-flashnext.sh" --print-model)
  if [ ! -f "$SHARD1" ]; then
    [ "$WEIGHTS" = abliterated ] && echo "  abliterated weights not on disk ($SHARD1) - get them: scripts/download-abliterated.sh gufo" >&2 \
      || echo "  stock weights not on disk ($SHARD1) - get them: scripts/download-stock.sh" >&2
    exit 1
  fi
  MODEL=UD-Q4_K_XL; GGUF=(--gguf-dir "$(dirname "$SHARD1")"); WHAT="uses $(dirname "$SHARD1") (no weights download)"
fi
echo "  Strata ($([ "$OWN" = 1 ] && echo "$MODEL_ID" || echo "$WEIGHTS weights")): $([ -n "$HAVE" ] && echo "in $HAVE, " || echo "clone into $DIR, ")then its setup fetches the engine and $WHAT${AVAIL:+ (free: ${AVAIL} GB)}"
if [ "${1:-}" != --yes ]; then
  read -r -p "  install now? [y/N] " A
  case "$A" in [yY]*) ;; *) echo "  aborted" >&2; exit 1 ;; esac
fi
if [ ! -f "$DIR/setup.sh" ]; then
  mkdir -p "$(dirname "$DIR")"
  git clone https://github.com/Niko1221/Strata.git "$DIR" || { echo "  git clone failed" >&2; exit 1; }
fi
cd "$DIR" || exit 1
# Strata only knows the Dynamic 3.1 UD-Q4_K_XL shards from 0.2.11; an older checkout fails deep inside its setup
TAG=$(git describe --tags 2>/dev/null | sed 's/^v//; s/-.*//')
if [ -n "$TAG" ] && [ "$(printf '%s\n0.2.11\n' "$TAG" | sort -V | head -1)" != 0.2.11 ]; then
  echo "  Strata in $DIR is v$TAG, the stock UD-Q4_K_XL files need 0.2.11+: scripts/upgrade-strata.sh (or git pull there)" >&2; exit 1
fi
# Strata compiles its HIP engine with $ROCM/llvm/bin/clang++; TheRock 10.x system ROCm (rocm-gfx1151-bin) keeps it in
# lib/llvm, so cmake fails. A ROCM_PATH without hipcc makes Strata use its pinned gfx1151 wheels in .venv (~10 GB, no sudo).
# shortcut: drop this once Strata finds lib/llvm/bin/clang++ itself
SYS=${ROCM_PATH:-/opt/rocm}
if [ -x "$SYS/bin/hipcc" ] && [ ! -x "$SYS/llvm/bin/clang++" ]; then
  echo "  system ROCm in $SYS has no llvm/bin/clang++ (the layout Strata builds with): using Strata's own ROCm wheels"
  export ROCM_PATH="$DIR/.no-system-rocm"
  grep -qs "$SYS/llvm/bin/clang++" build-hip/CMakeCache.txt && rm -rf build-hip  # the failed configure's cache
fi
# the other answer's pack must go first: Strata reuses a finished pack by name and would pair it with these GGUFs
if [ "$OWN" = 0 ] && [ -f "$DIR/run-unsloth-ud-q4_k_xl.sh" ] && [ "$(cat "$DIR/.harness-weights" 2>/dev/null || echo abliterated)" != "$WEIGHTS" ]; then
  PACK=$(python3 -c 'import json,sys;a=json.load(open(sys.argv[1]))["args"];print(a[a.index("--pack")+1])' "$DIR/strata-unsloth-ud-q4_k_xl.json" 2>/dev/null)
  case "$PACK" in */packs/unsloth-ud-q4_k_xl) echo "  rebuilding Strata's pack for the $WEIGHTS weights (removing $PACK)"; rm -rf "$PACK" ;; *) echo "  cannot find the old pack to reset ($PACK): remove packs/unsloth-ud-q4_k_xl in Strata's data folder and re-run" >&2; exit 1 ;; esac
fi
# --model also makes a later run install that model instead of just starting the installed one
BROWSER=true sh ./setup.sh --yes --no-start --family "$FAM" --model "$MODEL" "${GGUF[@]}" < /dev/null \
  || { echo "  Strata setup failed - re-run to resume, or run ./setup.sh in $DIR to see its questions" >&2; exit 1; }
[ "$OWN" = 0 ] && echo "$WEIGHTS" > "$DIR/.harness-weights"
[ -n "$(STRATA_WEIGHTS=$WEIGHTS bash "$REPO/server/start-strata.sh" --print-run)" ] \
  || { echo "  Strata setup finished but wrote no run script for $MODEL_ID in $DIR" >&2; exit 1; }
echo "  Strata installed in $DIR ($MODEL_ID: $FAM $MODEL)"
