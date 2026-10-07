#!/usr/bin/env bash
# halogen launcher (static, self-contained). installed by setup.sh; runs standalone.
#
# context: DEFAULT IS FULL 262144 x 4 slots (halogen native max). pre-flight gate
# recovers fragmented allocators or falls back to 65k - never wedges silently.
# small-context day: HALOGEN_CTX=65536 HALOGEN_KV_SLOTS=2
# offline contract: no HALOGEN_DOWNLOAD, NPU models from /models/npu/ - zero network.
set -u
CTX="${HALOGEN_CTX:-262144}"
SLOTS="${HALOGEN_KV_SLOTS:-4}"
# PINNED image tag: upgrades are deliberate - change the pin, restart, run the
# post-upgrade checks (see README "Upgrading halogen"). Floating :latest runs
# untested behavior against a harness validated on specific versions.
IMAGE_TAG="${HALOGEN_IMAGE_TAG:-0.17.1}"

# locate the main checkpoint at runtime (exclude ngram/vision/mtp/w4b sidecars)
CKPT="${HALOGEN_CHECKPOINT:-}"
if [ -z "$CKPT" ]; then
  CKPT=$(ls "$PWD"/*.hgn /models/*.hgn "$HOME"/models/*.hgn "$HOME"/Downloads/*.hgn \
    "${LLMBENCH:-$HOME/LLMBench}"/models/*/*.hgn 2>/dev/null \
    | grep -v 'ngram\|vision\|mtp\|w4b' | grep 'v2\.hgn' | head -1)
fi
if [ -z "$CKPT" ] || [ ! -f "$CKPT" ]; then
  echo "no main checkpoint found (looked in ., /models, ~/models, ~/Downloads, LLMBench/models)" >&2
  echo "pass one: HALOGEN_CHECKPOINT=/path/qwen38-flash-next-v2.hgn $0" >&2
  exit 1
fi
W=$(dirname "$CKPT")
# vision tower is enabled by default (HALOGEN_VISION_TOWER=1; file must sit beside the checkpoint)
if [ ! -f "$W/qwen38-flash-next-vision.hgn" ]; then
  echo "vision tower missing: $W/qwen38-flash-next-vision.hgn" >&2
  echo "download it from huggingface.co/peonist-ai/halogen-qwen3.8-flash-next (0.9 GB)," >&2
  echo "or launch with VISION=0 to run text-only" >&2
  exit 1
fi
VISION="${VISION:-1}"

# pre-flight: contiguous 2MiB-equivalent units (order-9 x1, order-10 x2, ...).
# counting only order-9 false-positives on kernels where free memory coalesces up.
units() { awk '$4=="Normal"{u=0; for(i=14;i<=NF;i++) u+=$(i)*2**(i-14); print u}' /proc/buddyinfo 2>/dev/null | head -1; }
if [ "$CTX" -gt 65536 ]; then
  U=$(units)
  if [ -n "$U" ] && [ "$U" -lt 1000 ]; then
    echo "contiguous units = $U (<1000): attempting recovery..."
    python3 - "$W" <<'PY' 2>/dev/null || true
import os, sys
for dirpath, _, files in os.walk(sys.argv[1]):
    for f in files:
        try:
            fd = os.open(os.path.join(dirpath, f), os.O_RDONLY)
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
            os.close(fd)
        except OSError: pass
os.sync()
PY
    sleep 2; U=$(units)
    if [ -n "$U" ] && [ "$U" -lt 1000 ]; then
      sudo -n sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches; echo 1 > /proc/sys/vm/compact_memory' 2>/dev/null || true
      sleep 5; U=$(units)
    fi
    if [ -z "$U" ] || [ "$U" -lt 1000 ]; then
      echo "contiguous units = ${U:-?} (<1000) after recovery: 262k would wedge. FALLING BACK to 65k/2-slot." >&2
      echo "for 262k later: reboot fresh, or sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches; echo 1 > /proc/sys/vm/compact_memory'" >&2
      CTX=65536; SLOTS=2
    else
      echo "contiguous units recovered to $U - proceeding at 262k"
    fi
  else
    echo "contiguous units = ${U:-?} - proceeding at ctx=$CTX"
  fi
fi

# keep pi honest: sync contextWindow + compaction reserve to the window we actually serve
python3 - "$CTX" <<'PY' 2>/dev/null || true
import json, os, sys
ctx = int(sys.argv[1])
base = os.environ.get("PI_CODING_AGENT_DIR") or os.path.expanduser("~/.pi/agent")
mp = os.path.join(os.path.dirname(base) if base.endswith("/agent") else base, "models.json")
mp = mp if os.path.isfile(mp) else os.path.expanduser("~/.pi/agent/models.json")
try:
    m = json.load(open(mp))
    for mod in m.get("providers", {}).get("halogen", {}).get("models", []):
        if mod.get("id") == "halogen-qwen3.8-flash-next":
            mod["contextWindow"] = ctx
            json.dump(m, open(mp, "w"), indent=1)
            print(f"models.json: halogen contextWindow -> {ctx}")
    sp = os.path.join(os.path.dirname(mp), "settings.json")
    s = json.load(open(sp))
    s.setdefault("compaction", {})["reserveTokens"] = 60000 if ctx > 65536 else 10240
    json.dump(s, open(sp, "w"), indent=2)
except Exception: pass
PY

RENDER_GID=$(getent group render | cut -d: -f3); VIDEO_GID=$(getent group video | cut -d: -f3)
exec docker run --rm -i \
  --device /dev/accel/accel0 --device /dev/kfd --device /dev/dri \
  -v /sys:/host/sys \
  -v /usr/lib/libxrt_coreutil.so.2:/opt/xilinx/xrt/lib/libxrt_coreutil.so.2:ro \
  -v /usr/lib/libxrt_core.so.2:/opt/xilinx/xrt/lib/libxrt_core.so.2:ro \
  -v /usr/lib/libxrt_driver_xdna.so.2:/opt/xilinx/xrt/lib/libxrt_driver_xdna.so.2:ro \
  -v /usr/lib/libxrt_coreutil.so.2:/usr/lib/libxrt_coreutil.so.2:ro \
  -v /usr/lib/libxrt_core.so.2:/usr/lib/libxrt_core.so.2:ro \
  -v /usr/lib/libxrt_driver_xdna.so.2:/usr/lib/libxrt_driver_xdna.so.2:ro \
  -e HALOGEN_CTX=$CTX -e HALOGEN_KV_SLOTS=$SLOTS \
  -e HALOGEN_NPU_MODELS=decider-0.8b,qwen3-embedding-0.6b,qwen3-reranker-0.6b,qwen3guard-gen-0.6b \
  -e HALOGEN_VISION_TOWER=$VISION \
  -e HALOGEN_CHECKPOINT=/models/$(basename "$CKPT") \
  --group-add "${RENDER_GID:-44}" --group-add "${VIDEO_GID:-99}" \
  --security-opt seccomp=unconfined --ipc=host --ulimit memlock=-1:-1 \
  -v "$W":/models:ro -p 127.0.0.1:8731:8731 \
  ghcr.io/peonist-ai/halogen-flash-server:${IMAGE_TAG}
