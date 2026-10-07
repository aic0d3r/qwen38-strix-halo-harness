#!/usr/bin/env bash
# probe the ling-tiny sidecar (:8090) and restart it if dead.
# SIDECAAR_HOME override: where the systemd unit / launcher lives (default: this repo).
set -uo pipefail
R="$(cd "$(dirname "$0")/.." && pwd)"
systemctl --user is-active ling-tiny >/dev/null 2>&1 \
  && echo "sidecar up" \
  || { echo "sidecar down - starting via systemd"; systemctl --user enable --now ling-tiny 2>/dev/null; sleep 3; systemctl --user is-active ling-tiny; }
