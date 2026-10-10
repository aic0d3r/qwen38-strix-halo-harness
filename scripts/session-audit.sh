#!/usr/bin/env bash
# session-audit.sh — post-session forensics for the pi harness.
# Everything is scoped to the audited session's own lifetime.
#   ./scripts/session-audit.sh [session.jsonl]   (default: newest session)
set -uo pipefail
F="${1:-}"
if [ -z "$F" ]; then
  F=$(find "$HOME/.pi/agent/sessions" -name "*.jsonl" -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2-)
fi
[ -z "$F" ] || [ ! -f "$F" ] && { echo "no session file found"; exit 1; }

# session start (first event timestamp, ISO/UTC) -> local time for journalctl/docker
START=$(head -1 "$F" | grep -oE '"timestamp":"[0-9T:.Z-]+"' | head -1 | cut -d'"' -f4)
SINCE=$(date -d "$START" "+%Y-%m-%d %H:%M:%S" 2>/dev/null || echo "today")

if [ -t 1 ]; then G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; D=$'\033[2m'; N=$'\033[0m'
else G=""; R=""; Y=""; D=""; N=""; fi
ok()   { echo " ${G}ok${N}   $*"; }
warn() { echo " ${Y}WARN${N} $*"; }
bad()  { echo " ${R}FAIL${N} $*"; }
info() { echo " ${D}-$N $*"; }
hr()   { echo "${D}--------------------------------------------------------------${N}"; }

echo "session-audit  ${F##*/}"
W=0

# --- session stats ---
python3 - "$F" <<'PY'
import json, sys, time
inp = out = cr = 0
turns = 0; maxinp = 0; seq = []; tools = {}; first = last = None
for line in open(sys.argv[1]):
    try: e = json.loads(line)
    except: continue
    if e.get("type") != "message": continue
    m = e.get("message", {}); u = m.get("usage") or {}
    ts = m.get("timestamp")
    if ts: last = ts
    first = first or ts
    inp += u.get("input", 0) or 0
    out += u.get("output", 0) or 0
    cr += u.get("cacheRead", 0) or 0
    if u.get("input", 0): seq.append(u["input"]); maxinp = max(maxinp, u["input"])
    if m.get("role") == "assistant":
        turns += 1
        for c in m.get("content", []):
            if isinstance(c, dict) and c.get("type") == "toolCall":
                tools[c.get("name", "?")] = tools.get(c.get("name", "?"), 0) + 1
hit = cr / (inp + cr) * 100 if (inp + cr) else 0
print(f"turns {turns}   fresh {inp:,}   cached {cr:,}   hit {hit:.0f}%   peak-ctx {maxinp:,}")
def fmt(ts):
    try: return time.strftime("%H:%M:%S", time.localtime(int(ts)/1000))
    except Exception: return "?"
print(f"span   {fmt(first)} .. {fmt(last)}")
top = sorted(tools.items(), key=lambda x: -x[1])
print("tools  " + (", ".join(f"{n} x{c}" for n, c in top[:8]) if top else "(none)"))
drops = [(i, a, b) for i, (a, b) in enumerate(zip(seq, seq[1:]), 1) if a >= 100_000 and b < a * 0.4]
if drops:
    print(f"COMPACTION {len(drops)} event(s): " + ", ".join(f"{a:,}->{b:,}" for _, a, b in drops))
    print("         ^ verify the summarize engine in the sidecar section below")
elif maxinp >= 190_000:
    print("NOTE session approached the 202k trigger without compacting")
PY
hr

# --- halogen version vs pin ---
PIN=$(grep -oE 'HALOGEN_IMAGE_TAG:-[0-9.]+' "$(cd "$(dirname "$0")/.." && pwd)/server/start-halogen.sh" 2>/dev/null | head -1 | cut -d- -f2)
V=$(curl -sf --max-time 5 http://127.0.0.1:8731/health | python3 -c "
import json,sys
try:
    v = json.load(sys.stdin).get('version')
    print(v.get('api') if isinstance(v, dict) else v)
except Exception: print('?')" 2>/dev/null || echo "?")
if [ "$V" = "$PIN" ]; then ok "halogen $V matches pin"
elif [ "$V" = "?" ]; then bad "halogen unreachable"; W=$((W+1))
else warn "halogen $V != pin $PIN — receipts may not transfer"; W=$((W+1)); fi

# --- sidecar rejections since session start ---
J=$(journalctl --user -u ling-tiny --since "$SINCE" --no-pager 2>/dev/null | grep -iE "send_error|exceeds|level=error" | tail -3)
if [ -n "$J" ]; then bad "sidecar errors since session start:"; echo "$J" | sed 's/^/       /'; W=$((W+1))
else ok "sidecar clean since session start"; fi

# --- halogen: cache-broken prefills + errors since session start ---
C=$(docker ps -q --filter publish=8731 2>/dev/null | head -1)
if [ -n "$C" ]; then
  CB=$(docker logs --since "$SINCE" "$C" 2>&1 | grep serve_api | grep -vE "cached, 9[0-9]%|cached,100" | grep -cE "prompt ([5-9][0-9]|[0-9]{3})[0-9]{3}")
  if [ "${CB:-0}" -gt 0 ]; then
    warn "$CB cache-broken prefill(s) >50k tok since session start (compaction signature):"
    docker logs --since "$SINCE" "$C" 2>&1 | grep serve_api | grep -vE "cached, 9[0-9]%|cached,100" | grep -E "prompt ([5-9][0-9]|[0-9]{3})[0-9]{3}" | tail -2 | sed 's/^/       /'
    W=$((W+1))
  else ok "no cache-broken prefills since session start"; fi
  E=$(docker logs --since "$SINCE" "$C" 2>&1 | grep -ciE "send_error| 4[0-9][0-9] | 5[0-9][0-9] ")
  if [ "${E:-0}" -gt 0 ]; then warn "$E error/4xx/5xx log lines since session start"; W=$((W+1))
  else ok "no error/4xx/5xx lines since session start"; fi
else bad "halogen container not running"; W=$((W+1)); fi
hr

hr

if [ "$W" -eq 0 ]; then echo "${G}RESULT: clean — no anomalies in this session's window${N}"
else echo "${Y}RESULT: $W warning(s) above${N}"; fi
