#!/usr/bin/env bash
# probe the ling-tiny sidecar (:8090) and restart it if dead or wedged.
# systemctl is-active lies for a hung-but-running process - the health probe doesn't.
set -uo pipefail
R="$(cd "$(dirname "$0")/.." && pwd)"
up(){ curl -sf -m 3 localhost:8090/health >/dev/null 2>&1; }
up && echo "sidecar up" || {
  echo "sidecar down/unhealthy - restarting via systemd"
  systemctl --user enable --now ling-tiny >/dev/null 2>&1 || systemctl --user restart ling-tiny >/dev/null 2>&1
  sleep 3
  up && echo "sidecar up" || { echo "still unhealthy"; systemctl --user is-active ling-tiny; exit 1; }
}
