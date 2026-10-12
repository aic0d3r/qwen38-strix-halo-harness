#!/usr/bin/env python3
"""Summarize harness-events.jsonl for one session: harness-events.py <session.jsonl> <events.jsonl>
Exit 1 if any sidecar/NPU call failed (= fell back to the main model or screened nothing)."""
import json, sys
from collections import defaultdict

def window(session):
    lines = [json.loads(l) for l in open(session) if l.strip()]
    ts = [e["timestamp"] for e in lines if isinstance(e.get("timestamp"), str)]
    # +60s: compaction/commit events land just after the last message entry
    from datetime import datetime, timedelta
    end = (datetime.fromisoformat(max(ts).replace("Z", "+00:00")) + timedelta(seconds=60)).isoformat().replace("+00:00", "Z")
    return lines[0].get("cwd"), min(ts), end

def summarize(events, cwd, start, end):
    # shortcut: matches by cwd + time window, so two concurrent sessions in one dir are merged; add a session id if that matters
    by = defaultdict(list)
    for e in events:
        if e.get("cwd") == cwd and start <= e.get("t", "") <= end:
            by[e["c"]].append(e)
    out, fails = [], 0
    for c, es in sorted(by.items()):
        ok = [e for e in es if e.get("ok")]
        bad = len(es) - len(ok)
        fails += bad
        ms = [e["ms"] for e in ok if "ms" in e]
        parts = [f"{c:<15}{len(es):>3}x", f"avg {sum(ms)/len(ms)/1000:.2f}s" if ms else ""]
        tok = sum((e.get("tokIn") or 0) + (e.get("tokOut") or 0) for e in ok)
        if tok: parts.append(f"~{tok:,} tok offloaded from main model")
        if c == "guard": parts.append(f"{sum(1 for e in ok if e.get('flagged'))} flagged")
        if c == "triage":
            ps = [e["p"] for e in ok if e.get("p")]
            if ps: parts.append(f"mean top-p {sum(ps)/len(ps):.2f}, {sum(p < 0.6 for p in ps)} unsure(<0.6)")
        if c == "dedup_scan": parts.append(f"{sum(e.get('pairs', 0) for e in ok)} dup pairs")
        if bad:
            whys = sorted({e.get("why", "?") for e in es if not e.get("ok")})
            parts.append(f"FAILED {bad} ({'; '.join(whys)[:100]})")
        out.append("  ".join(p for p in parts if p))
    return out, fails

if __name__ == "__main__":
    if sys.argv[1] == "--selftest":
        ev = [{"t": "2026-01-01T00:00:05Z", "cwd": "/r", "c": "compaction", "ok": True, "ms": 3000, "tokIn": 40000, "tokOut": 1000},
              {"t": "2026-01-01T00:00:06Z", "cwd": "/r", "c": "compaction", "ok": False, "why": "HTTP 400"},
              {"t": "2026-01-01T00:00:07Z", "cwd": "/r", "c": "triage", "ok": True, "ms": 100, "p": 0.5},
              {"t": "2026-01-01T00:00:08Z", "cwd": "/other", "c": "triage", "ok": True, "ms": 100},
              {"t": "2026-01-02T00:00:00Z", "cwd": "/r", "c": "guard", "ok": True, "ms": 70}]
        out, fails = summarize(ev, "/r", "2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z")
        assert fails == 1 and len(out) == 2, out
        assert "~41,000 tok" in out[0] and "FAILED 1 (HTTP 400)" in out[0], out[0]
        assert "1 unsure" in out[1], out[1]
        print("selftest ok"); sys.exit(0)
    cwd, start, end = window(sys.argv[1])
    try: events = [json.loads(l) for l in open(sys.argv[2]) if l.strip()]
    except FileNotFoundError: events = []
    out, fails = summarize(events, cwd, start, end)
    print("harness  sidecar + NPU work in this session")
    print("\n".join("  " + l for l in out) if out else "  (no events - extensions not installed/updated, or none fired)")
    sys.exit(1 if fails else 0)
