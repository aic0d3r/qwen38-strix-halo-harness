#!/usr/bin/env bash
# start halogen + the ling-tiny sidecar headless (no terminal, no pi) and notify when ready.
# Same thing start.sh does before it opens pi; log: halogen.log (server) and /tmp/start-servers.log.
REPO="$(cd "$(dirname "$0")/.." && pwd)"
notify-send -i media-playback-start "LLM servers" "Starting halogen + ling-tiny (up to ~5 min)..." 2>/dev/null
if bash "$REPO/setup.sh" --halogen auto > /tmp/start-servers.log 2>&1 \
   && curl -sf -m3 http://127.0.0.1:8731/health >/dev/null && curl -sf -m3 http://127.0.0.1:8090/health >/dev/null; then
  msg="halogen (:8731) and ling-tiny (:8090) are up. Run start.sh to open pi."
else
  msg="start failed: $(grep -E 'FAIL|fail' /tmp/start-servers.log | tail -1 | cut -c1-150) (see /tmp/start-servers.log)"
fi
echo "$msg"; notify-send -i dialog-information "LLM servers" "$msg" 2>/dev/null
