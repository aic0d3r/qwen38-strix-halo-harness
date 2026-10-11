#!/usr/bin/env bash
# halogen-top.sh — live prefill/decode throughput for the halogen flash server.
#   ./scripts/halogen-top.sh [interval_s]      (default 1s, Ctrl-C to quit)
#
# /metrics is cumulative since server start, and its two *tokens_seconds gauges
# only mean "since the last scrape" — so we take our own deltas between scrapes
# and append them to a history CSV that session-audit.sh --perf later diffs.
exec python3 - "${1:-1}" <<'PY'
import json, os, sys, time, urllib.request

URL = os.environ.get("HALOGEN_URL", "http://127.0.0.1:8731")
INTERVAL = float(sys.argv[1])
HIST = os.path.join(os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")),
                    "halogen-top", "history.csv")
KEYS = {
    "prompt_tokens": "llamacpp:prompt_tokens_total",
    "prompt_seconds": "llamacpp:prompt_seconds_total",
    "pred_tokens": "llamacpp:tokens_predicted_total",
    "pred_seconds": "llamacpp:tokens_predicted_seconds_total",
    "requests": "halogen:requests_total",
    "cache_hits": "halogen:cache_hits_total",
    "cache_misses": "halogen:cache_misses_total",
    "tokens_saved": "halogen:prompt_tokens_cached_total",
    "draft_acc": "halogen:draft_tokens_accepted_total",
    "draft_rounds": "halogen:draft_tokens_total",
    "kv": "llamacpp:kv_cache_usage_ratio",
    "proc": "llamacpp:requests_processing",
    "queued": "halogen:kv_pool_waiting_for_room",
}


def scrape(url):
    txt = urllib.request.urlopen(url + "/metrics", timeout=5).read().decode()
    want = set(KEYS.values())
    out = {}
    for line in txt.splitlines():
        if line.startswith("#") or not line:
            continue
        name, _, val = line.partition(" ")
        if name in want:
            out[name] = float(val)
    try:  # busy/queued live in /health, not /metrics
        h = json.load(urllib.request.urlopen(url + "/health", timeout=5))
        out["in_flight"] = float(h.get("in_flight", 0))
        out["queued"] = h.get("queued", out.get("queued", 0))
        out["busy_for_s"] = h.get("busy_for_s", 0)
    except Exception:
        pass
    return out


def g(s, k):
    return s.get(k, 0.0)


def ratio(s, num, den):
    return g(s, num) / g(s, den) if g(s, den) else 0.0


try:
    os.makedirs(os.path.dirname(HIST), exist_ok=True)
    prev = scrape(URL)
except Exception as e:
    sys.exit(f"cannot scrape {URL}/metrics: {e}")

rows = 0
try:
    rows = max(0, len(open(HIST).read().splitlines()) - 1)
except OSError:
    try:
        with open(HIST, "w") as f:  # header: session-audit.sh --perf reads columns by name
            f.write("epoch," + ",".join(KEYS) + "\n")
    except OSError:
        pass

live = {"prefill": 0.0, "decode": 0.0}
t0 = time.time()


def loop(prev):
    while True:
        time.sleep(INTERVAL)
        try:
            cur = scrape(URL)
        except Exception as e:
            print(f"scrape failed: {e}", flush=True)
            continue
        global rows
        # shortcut: a scrape under load can omit a counter; treat it as no change that window
        d = {k: cur.get(v, prev.get(v, 0.0)) - prev.get(v, 0.0) for k, v in KEYS.items()}
        # window rates: tokens over the seconds actually spent in each phase
        pre = max(0.0, d["prompt_tokens"] / d["prompt_seconds"]) if d["prompt_seconds"] > 0 else 0.0
        dec = max(0.0, d["pred_tokens"] / d["pred_seconds"]) if d["pred_seconds"] > 0 else 0.0
        live.update(prefill=pre or live["prefill"], decode=dec or live["decode"])
        hit = d["cache_hits"] / (d["cache_hits"] + d["cache_misses"]) * 100 \
            if d["cache_hits"] + d["cache_misses"] else 0.0
        # lifetime = cumulative counters, window = this scrape
        lp = ratio(cur, "llamacpp:prompt_tokens_total", "llamacpp:prompt_seconds_total")
        ld = ratio(cur, "llamacpp:tokens_predicted_total", "llamacpp:tokens_predicted_seconds_total")
        accept = ratio(cur, "halogen:draft_tokens_accepted_total", "halogen:draft_tokens_total")
        up = int(time.time() - t0)
        print(f"\033[H\033[Jhalogen {URL}   watching {up//60}m{up%60:02d}s   Ctrl-C to quit")
        print(f"  prefill   {d['prompt_tokens']:>9,.0f} tok in {d['prompt_seconds']:6.2f}s"
              f" = {pre:7.1f} t/s   (lifetime {lp:7.1f})")
        print(f"  decode    {d['pred_tokens']:>9,.0f} tok in {d['pred_seconds']:6.2f}s"
              f" = {dec:7.1f} t/s   (lifetime {ld:7.1f})")
        print(f"  cache     {hit:5.1f}% of reqs this window"
              f"   {g(cur,'halogen:prompt_tokens_cached_total')/1e6:6.2f}M prompt tok saved")
        print(f"  mtp       {accept:4.1%} of draft tokens accepted"
              f"   kv {g(cur,'llamacpp:kv_cache_usage_ratio')*100:5.1f}% used")
        print(f"  state     processing {g(cur,'llamacpp:requests_processing'):.0f}"
              f"  in_flight {g(cur,'in_flight'):.0f}  queued {g(cur,'queued'):.0f}"
              f"  busy {g(cur,'busy_for_s'):.0f}s  reqs {g(cur,'halogen:requests_total'):.0f}")
        print(f"  last busy prefill {live['prefill']:7.1f} t/s   decode {live['decode']:6.1f} t/s")
        print(f"  history   {HIST}  ({rows+1} rows)", flush=True)
        try:
            with open(HIST, "a") as f:
                f.write(f"{time.time():.3f}," + ",".join(str(round(cur.get(v, 0.0), 4))
                                                         for v in KEYS.values()) + "\n")
            rows += 1
        except OSError:
            pass
        prev = cur


try:
    loop(prev)
except KeyboardInterrupt:
    print(f"\nstopped after {int(time.time() - t0)}s — history in {HIST}")
PY
