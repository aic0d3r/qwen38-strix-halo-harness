#!/usr/bin/env python3
"""halogen-perf.py — prefill/decode throughput for one pi session's time window.

  docker logs -t --since <t> <container> | scripts/halogen-perf.py <session.jsonl>
  scripts/halogen-perf.py --selftest

Two independent readings of the same window:
  * the server's per-request serve_api lines  -> this session's own requests
  * the /metrics history recorded by scripts/halogen-top.sh -> server-wide diff
The serve_api lines are the only per-session source: /metrics is cumulative and
carries no session id, so a finished window can only be reconstructed from logs.
"""
import calendar
import json
import os
import re
import sys
import time

DEC = re.compile(r"mtp (\d+) tok in ([\d.]+)s = ([\d.]+) t/s")
ROUNDS = re.compile(r"(\d+) rounds, commit ([\d.]+)/round")
PROMPT = re.compile(r"prompt (\d+)(?: \((\d+) cached, ([\d.]+)%\))?, prefill ([\d.]+)s(?: \((\d+) new\))?")
LOGTS = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)\.[0-9]+Z ")


def epoch(iso):
    """UTC ISO string -> epoch seconds (session JSONL and docker -t prefixes are both UTC)."""
    return calendar.timegm(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S"))


def window(path):
    lo = hi = None
    for line in open(path):
        try:
            t = json.loads(line).get("timestamp")
        except Exception:
            continue
        if isinstance(t, str):
            try:
                s = epoch(t)
            except ValueError:
                continue
            lo = s if lo is None else min(lo, s)
            hi = s if hi is None else max(hi, s)
    return lo, hi


def parse(lines, lo, hi):
    s = dict(reqs=0, gen=0, dec_s=0.0, newp=0, pre_s=0.0, cached=0, prompt=0,
             rounds=0, commit=0.0, think=0, rates=[])
    for raw in lines:
        m = LOGTS.match(raw)
        if not m or not (lo <= epoch(m.group(1)) <= hi) or "serve_api:" not in raw:
            continue
        d = DEC.search(raw)
        if not d:
            continue
        s["reqs"] += 1
        s["gen"] += int(d.group(1))
        s["dec_s"] += float(d.group(2))
        s["rates"].append(float(d.group(3)))
        r = ROUNDS.search(raw)
        if r:
            s["rounds"] += int(r.group(1))
            s["commit"] += int(r.group(1)) * float(r.group(2))
        p = PROMPT.search(raw)
        if p:
            s["prompt"] += int(p.group(1))
            s["cached"] += int(p.group(2) or 0)
            s["pre_s"] += float(p.group(4))
            s["newp"] += int(p.group(5)) if p.group(5) else int(p.group(1)) - int(p.group(2) or 0)
        if "think on" in raw:
            s["think"] += 1
    return s


def pct(vals, q):
    return sorted(vals)[min(len(vals) - 1, int(len(vals) * q))] if vals else 0.0


def report(s, span_s):
    print("PERF  halogen serve_api lines inside this session's window (this session's own requests)")
    if not s["reqs"]:
        print("  no serve_api lines in the window (log rotated, or window predates the current container)")
        return
    print(f"  requests   {s['reqs']}   (thinking on in {s['think']})")
    print(f"  decode     {s['gen']:,} tok in {s['dec_s']:.1f}s = {s['gen']/s['dec_s'] if s['dec_s'] else 0:.1f} t/s"
          f"   p50 {pct(s['rates'], .5):.1f}  p95 {pct(s['rates'], .95):.1f}  min {min(s['rates']):.1f}")
    print(f"  prefill    {s['newp']:,} new tok in {s['pre_s']:.1f}s"
          f" = {s['newp']/s['pre_s'] if s['pre_s'] else 0:.1f} t/s on uncached tokens")
    print(f"  prompt     {s['prompt']:,} tok total, {s['cached']:,} reused"
          f" = {s['cached']/s['prompt']*100 if s['prompt'] else 0:.1f}% cache reuse")
    print(f"  mtp        {s['commit']/s['rounds'] if s['rounds'] else 0:.2f} tok/round committed ({s['rounds']} rounds)")
    print(f"  gpu busy   {s['dec_s'] + s['pre_s']:.1f}s of a {span_s:.0f}s window")


# order must match KEYS in scripts/halogen-top.sh (kv/proc/queued trailing, unused here)
COLS = ["prompt_tokens", "prompt_seconds", "pred_tokens", "pred_seconds", "requests",
        "cache_hits", "cache_misses", "tokens_saved", "draft_acc", "draft_rounds"]


def metrics_diff(lo, hi):
    """Server-wide delta from the halogen-top history CSV, for the same window."""
    hist = os.path.join(os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")),
                        "halogen-top", "history.csv")
    try:
        rows = [ln.split(",") for ln in open(hist).read().splitlines() if ln.strip()]
    except OSError:
        print("  /metrics   no history yet — run scripts/halogen-top.sh to record it")
        return
    if len(rows) < 2 or rows[0][:len(COLS) + 1] != ["epoch"] + COLS:
        print("  /metrics   history unusable — run scripts/halogen-top.sh to record it")
        return
    data = [r for r in rows[1:] if len(r) >= len(COLS) + 1]
    win = [r for r in data if lo <= float(r[0]) <= hi]
    if not win:
        print("  /metrics   no halogen-top samples inside this window")
        return
    a = data[data.index(win[0]) - 1] if data.index(win[0]) else win[0]
    b = win[-1]
    c = {k: float(b[i + 1]) - float(a[i + 1]) for i, k in enumerate(COLS)}
    pre = c["prompt_tokens"] / c["prompt_seconds"] if c["prompt_seconds"] else 0
    dec = c["pred_tokens"] / c["pred_seconds"] if c["pred_seconds"] else 0
    hit = c["cache_hits"] / (c["cache_hits"] + c["cache_misses"]) * 100 \
        if c["cache_hits"] + c["cache_misses"] else 0
    print(f"  /metrics   delta: {c['requests']:.0f} reqs, prefill {pre:.1f} t/s, decode {dec:.1f} t/s,"
          f" cache {hit:.1f}% of reqs, {c['tokens_saved']/1e6:.2f}M prompt tok saved")
    print("             ^ server-wide (every session in the window), from halogen-top history")


def selftest():
    lo, hi = epoch("2026-10-11T18:00:00Z"), epoch("2026-10-11T20:00:00Z")
    lines = [
        "2026-10-11T18:30:00.5Z serve_api: mtp 100 tok in 2.00s = 50.00 t/s | 40 rounds, commit 2.50/round"
        " | prompt 20000 (19000 cached, 95.0%), prefill 1.00s (1000 new) | think on",
        "2026-10-11T19:00:00.0Z serve_api: mtp 50 tok in 1.00s = 50.00 t/s | 20 rounds, commit 2.50/round"
        " | prompt 5000, prefill 2.00s = 2500 t/s | think off",
        "2026-10-11T21:00:00.0Z serve_api: mtp 999 tok in 1.00s = 999.00 t/s | 1 rounds, commit 1.00/round"
        " | prompt 1, prefill 0.00s (1 new)",  # outside the window, must be ignored
    ]
    s = parse(lines, lo, hi)
    assert s["reqs"] == 2 and s["gen"] == 150, s
    assert s["dec_s"] == 3.0 and s["rates"] == [50.0, 50.0], s
    assert s["newp"] == 6000 and s["pre_s"] == 3.0, s  # 1000 new + the uncached 5000
    assert s["prompt"] == 25000 and s["cached"] == 19000, s
    assert s["rounds"] == 60 and abs(s["commit"] - 150.0) < 1e-9, s
    assert s["think"] == 1, s
    report(s, 7200)
    print("selftest ok")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        selftest()
        sys.exit(0)
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    lo, hi = window(sys.argv[1])
    if lo is None:
        sys.exit("no timestamps in session file")
    s = parse(sys.stdin, lo, hi)
    report(s, hi - lo)
    metrics_diff(lo, hi)
