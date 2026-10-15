#!/usr/bin/env python3
"""cache-gaps.py — does the 1h prompt cache (PI_CACHE_RETENTION=long) pay off for your Anthropic sessions?
  ./scripts/cache-gaps.py [session.jsonl ...]   default: every Anthropic session in ~/.pi/agent/sessions
Gap = time between consecutive assistant messages of one session (includes generation time, so it
slightly overstates the user's pause). Token counts and costs come from pi's own usage records.
1h writes cost 2x base instead of 1.25x (+60% on write cost). A call after a 5-60 min gap would have
rewritten its whole context under 5m (1.25x) where 1h reads it (~0.1x). Compare "premium" vs "saved".
shortcut: read price assumed 0.1x base (0.05x on Sonnet/Opus 5.5 makes "saved" slightly low).
"""
import collections, datetime, glob, json, os, sys

def ts(s): return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()

files = sys.argv[1:] or glob.glob(os.path.expanduser("~/.pi/agent/sessions/*/*.jsonl"))
T = collections.Counter(); gaps = collections.Counter(); sessions = 0
for f in files:
    prev = base = None; used = False
    for line in open(f, errors="ignore"):
        try: d = json.loads(line)
        except ValueError: continue
        m = d.get("message") or {}
        if m.get("role") != "assistant" or m.get("provider") != "anthropic" or "usage" not in m: continue
        u = m["usage"]; c = u.get("cost", {}); t = ts(d["timestamp"]); used = True
        w = u.get("cacheWrite") or 0; w1h = u.get("cacheWrite1h") or 0; r = u.get("cacheRead") or 0
        if w: base = c.get("cacheWrite", 0) / (1.25 * (w - w1h) + 2 * w1h)  # $/token base input price
        gap = None if prev is None else t - prev; prev = t
        T["calls"] += 1; T["write"] += w; T["write1h"] += w1h; T["read"] += r; T["cost"] += c.get("total", 0)
        b = None if gap is None else "<=5m" if gap <= 300 else "5-60m" if gap <= 3600 else ">60m"
        if b: gaps[b] += 1
        if base and b == "5-60m": T["saved"] += (r + w) * base * (1.25 - 0.1)  # cold rewrite avoided
        elif base: T["premium"] += 0.75 * w * base  # (2x - 1.25x) per written token; 1h would not have rewritten after a 5-60m gap
    sessions += used

if not T["calls"]: sys.exit("no Anthropic assistant messages found")
print(f"{sessions} sessions, {T['calls']} calls, total cost ${T['cost']:.2f}")
print(f"cache read {T['read']:,} tok | written {T['write']:,} tok ({T['write1h']:,} at 1h)")
print("gaps between calls:", {k: gaps[k] for k in ("<=5m", "5-60m", ">60m")}, "(>60m: no TTL helps)")
print(f"\n1h premium over 5m on all writes     : ~${T['premium']:.2f}")
print(f"saved by 1h on calls after 5-60m gaps: ~${T['saved']:.2f}")
print("verdict:", "keep 1h" if T["saved"] > T["premium"] else "5m would be cheaper for this usage")
