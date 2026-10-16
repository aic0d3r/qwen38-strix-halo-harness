#!/usr/bin/env bash
# Prints one line if a halogen image tag newer than the pin exists. Never installs, never blocks:
# 3s budget, silent on any failure. Opt-in: HALOGEN_CHECK_UPDATES=1, or the answer saved by setup.sh.
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PREF="${PI_CODING_AGENT_DIR:-${PI_HOME:-$HOME/.pi}/agent}/harness-update-check"
on="${HALOGEN_CHECK_UPDATES:-$(cat "$PREF" 2>/dev/null)}"
[ "$on" = 1 ] || exit 0
R=peonist-ai/halogen-flash-server
CUR=$(grep -oE 'HALOGEN_IMAGE_TAG:-[0-9.]+' "$REPO/server/start-halogen.sh" | head -1 | cut -d- -f2)
T=$(curl -sf -m 3 "https://ghcr.io/token?scope=repository:$R:pull" | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])' 2>/dev/null) || exit 0
NEW=$(curl -sf -m 3 -H "Authorization: Bearer $T" "https://ghcr.io/v2/$R/tags/list" \
  | python3 -c 'import sys,json;print("\n".join(json.load(sys.stdin)["tags"]))' 2>/dev/null | grep -E '^[0-9]+(\.[0-9]+)*$' | sort -V | tail -1)
[ -n "$CUR" ] && [ -n "$NEW" ] && [ "$NEW" != "$CUR" ] && [ "$(printf '%s\n%s\n' "$CUR" "$NEW" | sort -V | tail -1)" = "$NEW" ] \
  && echo "halogen $NEW available (pinned $CUR): ./setup.sh --halogen-upgrade"
exit 0
