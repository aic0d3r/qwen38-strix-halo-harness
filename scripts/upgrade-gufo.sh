#!/usr/bin/env bash
# upgrade-gufo.sh - pull a gufo image, restart on it, run a short acceptance battery, and only move the
# pin (GUFO_VERSION + digest in server/start-gufo-flashnext.sh) if everything passes.
#   scripts/upgrade-gufo.sh [tag]      (default: highest x.y.z tag on ghcr)
# Rollback is automatic: any failed check stops the new server and restarts the old pin (if gufo was up).
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
START="$REPO/server/start-gufo-flashnext.sh"
LOG=/tmp/gufo-upgrade.log
R=gufo-org/toolboxes/gufo-runtime
IMG=ghcr.io/$R
PORT=8080
URL=http://127.0.0.1:$PORT
CUR=$(grep -oE '^GUFO_VERSION=[0-9.]+' "$START" | cut -d= -f2)
TAG="${1:-}"
if [ -z "$TAG" ]; then
  T=$(curl -sf -m 5 "https://ghcr.io/token?scope=repository:$R:pull" | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
  TAG=$(curl -sf -m 5 -H "Authorization: Bearer $T" "https://ghcr.io/v2/$R/tags/list?n=1000" \
    | python3 -c 'import sys,json;print("\n".join(json.load(sys.stdin)["tags"]))' | grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -1)
  [ -n "$TAG" ] || { echo "FAIL: could not list gufo tags (offline?)"; exit 1; }
fi
[ "$TAG" = "$CUR" ] && { echo "already on $CUR - nothing to do"; exit 0; }
# one big engine at a time: gufo and halogen share the GPU heap (device-lost if both run)
[ -n "$(docker ps -q --filter publish=8731)" ] && { echo "FAIL: halogen is running - stop it first (gufo and halogen share the GPU memory)"; exit 1; }

echo "[1/5] pulling $IMG:$TAG (pinned: $CUR)"
docker pull "$IMG:$TAG" >/dev/null 2>&1 || { echo "FAIL: pull $IMG:$TAG"; exit 1; }
DIGEST=$(docker image inspect -f '{{index .RepoDigests 0}}' "$IMG:$TAG" 2>/dev/null | cut -d@ -f2)
case "$DIGEST" in sha256:*) ;; *) echo "FAIL: no registry digest for $IMG:$TAG"; exit 1 ;; esac
echo "      upgrading $CUR -> $TAG"

OLD_ID=$(docker ps -q --filter publish=$PORT)
WAS_UP=$([ -n "$OLD_ID" ] && echo 1 || echo 0)
rollback(){
  echo "FAIL: $1 - rolling back to $CUR"
  local id; id=$(docker ps -q --filter publish=$PORT)
  [ -n "$id" ] && docker stop -t 30 "$id" >/dev/null
  if [ "$WAS_UP" = 1 ]; then
    setsid nohup bash "$START" > "$LOG.rollback" 2>&1 < /dev/null &
    echo "      restarted on $CUR (log: $LOG.rollback); pin unchanged"
  else echo "      gufo was not running before; left stopped. pin unchanged"; fi
  exit 1
}

echo "[2/5] restarting on $TAG (first load of the model can take minutes)"
[ "$WAS_UP" = 1 ] && docker stop -t 30 "$OLD_ID" >/dev/null
rm -f "$LOG"
GUFO_IMAGE="$IMG@$DIGEST" setsid nohup bash "$START" > "$LOG" 2>&1 < /dev/null &
up(){ curl -sf -m 3 "$URL/v1/models" | grep -q '"owned_by": *"gufo"'; }
for i in $(seq 1 60); do sleep 15; up && break; done
up || { tail -5 "$LOG"; rollback "not answering after $((i*15))s (log: $LOG)"; }
echo "      answering on :$PORT"

echo "[3/5] answer check"
python3 - "$URL" <<'PY' || rollback "answer check"
import json, sys, urllib.request
body = json.dumps({"model": "qwen3.8-flash-next", "max_tokens": 400, "temperature": 0,
                   "messages": [{"role": "user", "content": "Reply with exactly: OK"}]}).encode()
d = json.load(urllib.request.urlopen(urllib.request.Request(sys.argv[1] + "/v1/chat/completions", data=body,
                                     headers={"content-type": "application/json"}), timeout=180))
txt = d["choices"][0]["message"].get("content") or ""
print(f"      reply: {txt.strip()[:40]!r}")
assert "OK" in txt, "no OK in reply"
PY

echo "[4/5] decode sanity (must be > 15 t/s)"
python3 - "$URL" <<'PY' || rollback "decode"
import json, sys, time, urllib.request
body = json.dumps({"model": "qwen3.8-flash-next", "max_tokens": 300, "temperature": 0,
                   "messages": [{"role": "user", "content": "Count from 1 to 80, digits only, no spaces."}]}).encode()
t0 = time.time()
d = json.load(urllib.request.urlopen(urllib.request.Request(sys.argv[1] + "/v1/chat/completions", data=body,
                                     headers={"content-type": "application/json"}), timeout=180))
dt = time.time() - t0; n = d["usage"]["completion_tokens"]; ts = n / dt
print(f"      {n} tok in {dt:.1f}s = {ts:.1f} t/s")
assert ts > 15, f"decode {ts:.1f} t/s below 15"
PY

echo "[5/5] updating pin $CUR -> $TAG"
sed -i -e "s/^GUFO_VERSION=$CUR/GUFO_VERSION=$TAG/" \
  -e "s|gufo-runtime@sha256:[0-9a-f]*|gufo-runtime@$DIGEST|" "$START"
grep -E '^GUFO_(VERSION|IMAGE)=' "$START"
echo "OK: validated and pinned to $TAG (running now). commit the start-gufo-flashnext.sh change to keep the repo in sync."
