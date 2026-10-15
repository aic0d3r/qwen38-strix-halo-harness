#!/usr/bin/env bash
# halogen-top.sh — live dashboard for the halogen flash server.
#   ./scripts/halogen-top.sh [interval_s]      (default 1s, Ctrl-C to quit)
#   ./scripts/halogen-top.sh --strata [url] [interval_s]   Strata (llama.cpp server, default
#       http://127.0.0.1:8080): same dashboard minus the halogen-only prompt-cache panel
#       (Strata's vLLM-style /metrics is mapped onto the same counters).
#       HALOGEN_URL overrides the url for either engine.
#
# halogen only bumps its token counters when a request *finishes*, so mid-request
# there is no live t/s to show. Instead we show what is running right now (busy
# time, in flight, queue, KV pool) and, per finished request, its own prefill and
# decode rates taken from our deltas between scrapes. Every scrape is also appended
# to a history CSV that session-audit.sh --perf later diffs (column order is fixed).
# Not a TTY (piped/logged)? One line per finished request instead of the dashboard.
exec python3 - "$@" <<'PY'
import collections, datetime, json, os, shutil, sys, time, urllib.request

_a = sys.argv[1:]
STRATA = "--strata" in _a
_a = [x for x in _a if x != "--strata"]
_url = next((x for x in _a if x.startswith("http")), None)
URL = (_url or os.environ.get("HALOGEN_URL") or
       ("http://127.0.0.1:8080" if STRATA else "http://127.0.0.1:8731")).rstrip("/")
INTERVAL = max(0.2, float(next((x for x in _a if not x.startswith("http")), 1)))
HIST = os.path.join(os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")),
                    "halogen-top", "history.csv")
# order is the CSV column order — halogen-perf.py reads it positionally/by name
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
TTY = sys.stdout.isatty()
COLOR = TTY and not os.environ.get("NO_COLOR")


def c(code, s):
    return f"\033[{code}m{s}\033[0m" if COLOR else str(s)


B, DIM = (lambda s: c("1", s)), (lambda s: c("2", s))
GRN, YEL, RED, CYN, MAG = (lambda s: c("32", s)), (lambda s: c("33", s)), \
    (lambda s: c("31", s)), (lambda s: c("36", s)), (lambda s: c("35", s))


def get(path, js=True):
    raw = urllib.request.urlopen(URL + path, timeout=3).read().decode()
    return json.loads(raw) if js else raw


# Strata (vLLM-style names, tokens reused instead of cache hit/miss counts) -> our KEYS names
STRATA_MAP = {
    "llamacpp:prompt_tokens_total": "vllm:prompt_tokens_total",
    "llamacpp:prompt_seconds_total": "strata:totals_prompt_seconds_total",
    "llamacpp:tokens_predicted_total": "vllm:generation_tokens_total",
    "llamacpp:tokens_predicted_seconds_total": "strata:totals_decode_seconds_total",
    "halogen:requests_total": "vllm:request_success_total",
    "halogen:prompt_tokens_cached_total": "vllm:prefix_cache_hits_total",
    "halogen:draft_tokens_accepted_total": "vllm:spec_decode_num_accepted_tokens_total",
    "halogen:draft_tokens_total": "vllm:spec_decode_num_draft_tokens_total",
    "llamacpp:kv_cache_usage_ratio": "vllm:kv_cache_usage_perc",
    "llamacpp:requests_processing": "vllm:num_requests_running",
    "halogen:kv_pool_waiting_for_room": "vllm:num_requests_waiting",
    "halogen:kv_pool_positions": "strata:engine_max_context",
}


def scrape():
    m = {}
    for line in get("/metrics?format=prometheus" if STRATA else "/metrics", js=False).splitlines():
        if line and not line.startswith("#"):
            name, _, val = line.rpartition(" ")
            if STRATA and name.endswith("}") and "," not in name:
                name = name.split("{")[0]  # keep only label-free families (drops histograms/per-gpu)
            try:
                m[name] = float(val)
            except ValueError:
                pass
    if STRATA:
        m.update({k: m[v] for k, v in STRATA_MAP.items() if v in m})
        # Strata's prompt total includes tokens reused from its cache, but prompt_seconds only
        # covers tokens actually read: subtract them or prefill t/s is wildly inflated.
        if "llamacpp:prompt_tokens_total" in m:
            m["llamacpp:prompt_tokens_total"] -= m.get("vllm:prefix_cache_hits_total", 0.0)
    h, cache = {}, {}
    try:
        h = get("/health")
    except Exception:
        pass
    try:
        cache = get("/cache")
    except Exception:
        pass
    return m, h, cache


EVENTS = os.path.expanduser("~/.pi/agent/harness-events.jsonl")
NPU_STATE = "/sys/class/accel/accel0/device/power_state"


def npu_line():
    """NPU: host power state (D0 = running something, D3hot = asleep), plus this harness's own
    NPU calls (guard/triage/dedup) since the dashboard started, from harness-events.jsonl."""
    try:
        st = open(NPU_STATE).read().strip()
    except OSError:
        return None  # no NPU on this host
    out = GRN("● active") if st == "D0" else DIM(f"◦ asleep ({st})")
    agg = {}
    try:
        with open(EVENTS, "rb") as f:
            f.seek(0, 2); f.seek(max(0, f.tell() - 400_000))
            for ln in f.read().decode("utf-8", "ignore").splitlines()[1:]:
                try:
                    e = json.loads(ln)
                except ValueError:
                    continue
                if e.get("c") not in ("guard", "triage", "dedup_scan", "embed"):
                    continue
                ts = e.get("t", "")
                try:
                    if datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() < t0:
                        continue
                except ValueError:
                    continue
                a = agg.setdefault(e["c"], [0, 0, 0.0, 0])  # n, failed, ms, flagged
                a[0] += 1; a[1] += 0 if e.get("ok", True) else 1
                a[2] += e.get("ms", 0); a[3] += 1 if e.get("flagged") else 0
    except OSError:
        pass
    for k, (n, bad, ms, fl) in agg.items():
        out += f"  ·  {k} {n}" + (f" ({fl} flagged)" if fl else "") + f" {ms/n:.0f}ms avg" + (RED(f" {bad} failed") if bad else "")
    if not agg:
        out += DIM("  ·  no harness NPU calls this session")
    return out


def human(n):
    n = float(n)
    for unit, div in (("G", 1e9), ("M", 1e6), ("k", 1e3)):
        if abs(n) >= div:
            return f"{n/div:.1f}{unit}"
    return f"{n:.0f}"


def dur(s):
    s = int(s)
    return f"{s//3600}h{s%3600//60:02d}m" if s >= 3600 else f"{s//60}m{s%60:02d}s" if s >= 60 else f"{s}s"


def bar(frac, width=24):
    frac = min(max(frac, 0.0), 1.0)
    fill = int(round(frac * width))
    col = RED if frac > 0.95 else YEL if frac > 0.8 else GRN
    return col("█" * fill) + DIM("░" * (width - fill))


SPARK = "▁▂▃▄▅▆▇█"


def spark(vals):
    vals = [v for v in vals if v > 0]
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    return "".join(SPARK[int((v - lo) / (hi - lo) * 7) if hi > lo else 3] for v in vals)


def rate(tok, sec):
    return tok / sec if sec > 0 else 0.0


# panel labels padded to one width so values start in a single column; IND aligns sub-rows under them
LBL = 15
IND = " " * (LBL + 2)


def row(label, text=""):
    # pad before colouring: escape sequences count toward str formatting and would shift columns
    return f" {c('1', f'{label:<{LBL}}')} {text}"


def pad(s, w):
    s = str(s)
    return " " * (w - len(s)) + s


def rrate(v):
    """rate with a thousands separator: prefill runs in the thousands, decode does not"""
    return f"{v/1000:.2f}k" if v >= 10000 else f"{v:,.1f}"


def prefmt(r, w, unit=False):
    """a fully prompt-cached request reads 0 new tokens: "0.0 t/s" would read as a stall"""
    if r["ptok"] <= 0:
        return DIM(pad("cached", w))
    return CYN(pad(rrate(r["pre"]), w) + (" t/s" if unit else ""))


# ── history CSV ──────────────────────────────────────────────────────────────
rows = 0
try:
    os.makedirs(os.path.dirname(HIST), exist_ok=True)
    if os.path.exists(HIST):
        with open(HIST) as f:
            rows = max(0, sum(1 for _ in f) - 1)
    else:
        with open(HIST, "w") as f:  # header: session-audit.sh --perf reads columns by name
            f.write("epoch," + ",".join(KEYS) + "\n")
except OSError:
    pass


def record(m):
    global rows
    try:
        with open(HIST, "a") as f:
            f.write(f"{time.time():.3f}," + ",".join(str(round(m.get(v, 0.0), 4))
                                                     for v in KEYS.values()) + "\n")
        rows += 1
    except OSError:
        pass


# ── state ────────────────────────────────────────────────────────────────────
t0 = time.time()
first = None          # metrics at start, for "this session" averages
prev = None
recent = collections.deque(maxlen=12)   # finished requests (ours, from deltas)
dec_hist = collections.deque(maxlen=40)  # decode t/s per finished request, for the sparkline
pre_hist = collections.deque(maxlen=40)  # prefill t/s per finished request, for the sparkline
last_ok, last_err, tick = None, "", 0
CACHE_KEYS = ("cache_hits", "cache_misses", "tokens_saved")
pending = dict.fromkeys(CACHE_KEYS, 0.0)  # cache counters move at request start, not finish


def finished(d, now):
    """A window in which prompt/pred counters moved = one or more requests finished."""
    if d["prompt_tokens"] <= 0 and d["pred_tokens"] <= 0:
        return None
    n = int(max(1, d["requests"]))
    hits, miss = d["cache_hits"], d["cache_misses"]
    r = dict(t=now, n=n, ptok=d["prompt_tokens"], psec=d["prompt_seconds"],
             gtok=d["pred_tokens"], gsec=d["pred_seconds"],
             pre=rate(d["prompt_tokens"], d["prompt_seconds"]),
             dec=rate(d["pred_tokens"], d["pred_seconds"]),
             saved=d["tokens_saved"],
             mtp=rate(d["draft_acc"], d["draft_rounds"]),
             cache="hit" if hits and not miss else "miss" if miss and not hits
             else f"{hits:.0f}/{hits+miss:.0f}" if hits + miss else "—")
    return r


def log_line(r, m):
    ts = time.strftime("%H:%M:%S", time.localtime(r["t"]))
    # aggregate since server start: the only rates that mean anything during a quiet stretch,
    # when the between-scrape deltas are flat
    agg = (f"  |  since start  prefill {rrate(rate(m.get(KEYS['prompt_tokens'], 0), m.get(KEYS['prompt_seconds'], 0)))} t/s"
           f"  decode {rrate(rate(m.get(KEYS['pred_tokens'], 0), m.get(KEYS['pred_seconds'], 0)))} t/s")
    return (f"{ts}  x{r['n']}  prefill {r['ptok']:>7,.0f} tok {'cached' if r['ptok'] <= 0 else f'{r["pre"]:7.1f} t/s'}"
            f"  decode {r['gtok']:>6,.0f} tok {r['dec']:6.1f} t/s"
            f"  mtp {r['mtp']:4.0%}  cache {r['cache']}  saved {human(r['saved'])}" + agg)


def render(m, h, cache, offline):
    W = shutil.get_terminal_size((100, 30)).columns
    L = []
    up = time.time() - t0
    hal = not STRATA  # halogen-only: hit/miss cache panel
    model = h.get("model", "strata" if STRATA else "halogen")
    ver = (h.get("version") or {}).get("api", "")
    status = RED("● OFFLINE") if offline else GRN("● online")
    L.append(f" {B(model)} {DIM('v' + ver) if ver else ''}  {DIM(URL)}   {status}"
             f"   {DIM('watching ' + dur(up))}")
    L.append(DIM("─" * min(W, 100)))
    if offline:
        L.append(f" {RED('cannot reach server')}: {last_err}")
        if last_ok:
            L.append(DIM(f" last good scrape {dur(time.time() - last_ok)} ago — retrying every {INTERVAL:g}s"))
        L.append("")
    if m:
        inflight = int(h.get("in_flight", m.get("llamacpp:requests_processing", 0)))
        queued = int(h.get("queued", 0)) + int(m.get("halogen:kv_pool_waiting_for_room", 0))
        busy_s = float(h.get("busy_for_s", m.get("halogen:busy_for_seconds", 0)))
        slots = h.get("slots", "?")
        if inflight:
            spin = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[tick % 10]
            now = YEL(f"{spin} working") + f"  {B(inflight)}/{slots} slots busy  ·  busy {B(dur(busy_s))}"
        else:
            now = GRN("◦ idle") + f"  0/{slots} slots"
        if queued:
            lw = float(h.get("longest_wait_s", 0) or 0)  # halogen 0.18.3+
            now += "  ·  " + RED(f"{queued} queued" + (f" (oldest {dur(lw)})" if lw >= 1 else ""))
        L.append(row("NOW", now))
        kv = m.get("llamacpp:kv_cache_usage_ratio", 0.0)
        pool = cache.get("pool", {})
        pos = pool.get("positions") or m.get("halogen:kv_pool_positions", 0)
        used = pool.get("used") or kv * pos
        L.append(row("KV pool", f"{bar(kv)} {kv*100:5.1f}%  {DIM(f'{human(used)} / {human(pos)} positions')}"))
        if inflight:
            L.append(f"{IND}{DIM('t/s only updates when a request finishes; LAST FINISHED is the newest one')}")
        L.append("")

        # last finished request
        if recent:
            r = recent[-1]
            pre, dec, mtp = r["pre"], r["dec"], r["mtp"]
            ago = dur(time.time() - r["t"])
            many = f" ({r['n']} requests)" if r["n"] > 1 else ""
            L.append(row("LAST FINISHED", f"{many}{DIM('  ' + ago + ' ago')}"))
            pre_cell = (DIM(pad("all prompt tokens from cache", 24)) if r["ptok"] <= 0 else
                        f"{pad(f'{r["ptok"]:,.0f}',9)} tok  {pad(f'{r["psec"]:.2f}',6)}s  {prefmt(r, 7, True)}")
            L.append(f"{IND}{CYN('prefill')}  {pre_cell}"
                     f"   {DIM(('cache ' + r['cache'] + ', ' if hal else '') + human(r['saved']) + ' tok reused')}")
            L.append(f"{IND}{MAG('decode')}    {pad(f'{r["gtok"]:,.0f}',9)} tok  {pad(f'{r["gsec"]:.2f}',7)}s  {MAG(pad(rrate(dec), 7))} t/s"
                     f"   {DIM(f'mtp accept {mtp:.0%}')}")
        else:
            L.append(row("LAST FINISHED", DIM("none yet since start — waiting for a request to finish")))
        L.append("")

        # aggregate throughput: lifetime = since the server started, watch = since this dashboard did
        def g(k, src=m):
            return src.get(k, 0.0)
        lp = rate(g(KEYS["prompt_tokens"]), g(KEYS["prompt_seconds"]))
        ld = rate(g(KEYS["pred_tokens"]), g(KEYS["pred_seconds"]))
        sp = rate(g(KEYS["prompt_tokens"]) - g(KEYS["prompt_tokens"], first),
                  g(KEYS["prompt_seconds"]) - g(KEYS["prompt_seconds"], first))
        sd = rate(g(KEYS["pred_tokens"]) - g(KEYS["pred_tokens"], first),
                  g(KEYS["pred_seconds"]) - g(KEYS["pred_seconds"], first))
        L.append(row("THROUGHPUT", f"{DIM(pad('t/s', 8))} {pad('since start', 12)} {pad('this watch', 11)}   {DIM('per request, newest right')}"))
        # a watch with only cache hits reads 0 new prompt tokens: "0.0" would read as a stall
        wt = g(KEYS["prompt_tokens"]) - g(KEYS["prompt_tokens"], first)
        swatch = DIM(pad("cached" if wt <= 0 and g(KEYS["tokens_saved"]) - g(KEYS["tokens_saved"], first) > 0 else "—", 11))
        L.append(f"{IND}{CYN(pad('prefill', 8))} {CYN(pad(rrate(lp), 12))} {(swatch if sp <= 0 else CYN(pad(rrate(sp), 11)))}   {CYN(spark(pre_hist))}")
        L.append(f"{IND}{MAG(pad('decode', 8))} {MAG(pad(rrate(ld), 12))} {MAG(pad(rrate(sd), 11))}   {MAG(spark(dec_hist))}")
        L.append("")

        # cache + mtp + totals
        if hal:
            hr = cache.get("hit_rate")
            thr = cache.get("token_hit_rate")
            if hr is None:
                hits, miss = g("halogen:cache_hits_total"), g("halogen:cache_misses_total")
                hr = hits / (hits + miss) if hits + miss else 0.0
            ent = cache.get("entries", int(g("halogen:cache_entries")))
            maxe = cache.get("max_entries", "?")
            cb = cache.get("bytes", g("halogen:cache_bytes"))
            L.append(row("PROMPT CACHE", f"hit {GRN(f'{hr:.1%}')} of requests"
                     + (f", {GRN(f'{thr:.1%}')} of tokens" if thr is not None else "")
                     + f"  ·  {human(g('halogen:prompt_tokens_cached_total'))} tok saved"))
            L.append(f"{IND}{ent}/{maxe} entries  {cb/1e9:.2f} GB  ·  {int(cache.get('evicted', g('halogen:cache_evicted_total')))} evicted"
                     f"  ·  restore {cache.get('restore_ms_total', 0)/max(1, cache.get('hits', 1)):.0f} ms avg")
        acc = rate(g("halogen:draft_tokens_accepted_total"), g("halogen:draft_tokens_total"))
        L.append(row("MTP DRAFT", f"accept {GRN(f'{acc:.1%}')}  "
                     f"{DIM(human(g('halogen:draft_tokens_accepted_total')) + ' / ' + human(g('halogen:draft_tokens_total')) + ' drafted')}"))
        L.append(row("TOTALS",
                     f"{g('halogen:requests_total'):,.0f} requests  ·  "
                     f"{human(g(KEYS['prompt_tokens']))} prompt tok  ·  {human(g(KEYS['pred_tokens']))} generated"))
        if not STRATA:
            nl = npu_line()
            if nl:
                L.append(row("NPU", nl))
        qw = g("halogen:queue_waited_total")  # halogen 0.18.3+; absent on older/Strata
        if qw:
            L.append(row("QUEUE", f"{qw:.0f} waited >30s  ·  {dur(g('halogen:queue_wait_seconds_total'))} total"
                         f"  ·  {g('halogen:queue_gave_up_total'):.0f} gave up"))
        L.append("")

        # recent requests
        L.append(row("RECENT", DIM("time        n   prompt tok  prefill t/s   gen tok  decode t/s   mtp  cache")))
        if not recent:
            L.append(f"{IND}{DIM('—')}")
        for r in reversed(recent):
            ts = time.strftime("%H:%M:%S", time.localtime(r["t"]))
            pre, dec = r["pre"], r["dec"]
            L.append(f"{IND}{ts}  {pad(r['n'],2)}  {pad(f'{r["ptok"]:,.0f}',11)}  {prefmt(r,11)}"
                     f"  {pad(f'{r["gtok"]:,.0f}',9)}  {MAG(pad(rrate(dec),10))}"
                     f"  {pad(f'{r["mtp"]:.0%}',5)}  {r['cache']}")
    L.append("")
    L.append(DIM(f" every {INTERVAL:g}s  ·  history {HIST} ({rows} rows)  ·  Ctrl-C quits"))
    H = shutil.get_terminal_size((100, 30)).lines
    out = "\033[H" + "\n".join(line + "\033[K" for line in L[:H - 1]) + "\033[J"
    sys.stdout.write(out)
    sys.stdout.flush()


def main():
    global first, prev, last_ok, last_err, tick
    m = h = cache = {}
    while True:
        offline = False
        try:
            m, h, cache = scrape()
            last_ok = time.time()
        except Exception as e:
            offline, last_err = True, str(getattr(e, "reason", e))
        if not offline:
            if first is None:
                first = m
            if prev is not None:
                # shortcut: a scrape can omit a counter; treat it as no change that window
                d = {k: m.get(v, prev.get(v, 0.0)) - prev.get(v, 0.0) for k, v in KEYS.items()}
                if any(d[k] < 0 for k in ("prompt_tokens", "pred_tokens")):
                    first = m  # server restarted: counters reset
                else:
                    for k in CACHE_KEYS:
                        pending[k] += d[k]
                        d[k] = pending[k]
                    r = finished(d, time.time())
                    if r:
                        pending.update(dict.fromkeys(CACHE_KEYS, 0.0))
                        recent.append(r)
                        if r["dec"]:
                            dec_hist.append(r["dec"])
                        if r["pre"]:
                            pre_hist.append(r["pre"])
                        if not TTY:
                            print(log_line(r, m), flush=True)
            prev = m
            record(m)
        elif not TTY:
            print(f"{time.strftime('%H:%M:%S')}  offline: {last_err}", flush=True)
        if TTY:
            render(m if first is not None else {}, h, cache, offline)
        tick += 1
        time.sleep(INTERVAL)


if TTY:
    sys.stdout.write("\033[?1049h\033[?25l")  # alt screen, hide cursor
try:
    main()
except KeyboardInterrupt:
    pass
finally:
    if TTY:
        sys.stdout.write("\033[?25h\033[?1049l")
    print(f"halogen-top: watched {dur(time.time() - t0)}, {len(recent)} finished requests shown — history in {HIST}")
PY
