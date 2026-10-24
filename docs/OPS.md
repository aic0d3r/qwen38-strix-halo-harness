# Operations: 262k Context, Memory Fragmentation and Troubleshooting

## Context size and memory fragmentation

The default launch is halogen's native max: **262,144 ctx x 4 slots**. Memory is not the
constraint (~67 GB model + ~115 MiB/slot in-place cache) — allocator fragmentation is.

The launcher's pre-flight gate handles it: if order-9 pages are low (box up a long time,
big-model churn) it first attempts recovery (page-cache drop, compaction) and only proceeds on a
healthy allocator; otherwise it fails in seconds with the exact fix command instead of wedging
for 30+ minutes. Install the `vm-compact.timer` (`setup.sh`/`--doctor` print the one-line
command) to keep the allocator healthy day to day.

```sh
HALOGEN_CTX=65536 HALOGEN_KV_SLOTS=2 ./start-halogen.sh   # small-context opt-out
```

After any ctx change the launcher syncs `models.json`'s `contextWindow` (so pi token-budgets
against the real window) and scales `compaction.reserveTokens` (60k at 262k, 10k at 65k).

## Watching speed (prefill / decode)

Three official surfaces, no built-in web UI in the image (`/`, `/gui`, `/dashboard` are 404):

- **per request** — every response (and the last streamed chunk) carries a `timings` object:
  `prompt_per_second`, `predicted_per_second`, `cache_n`, `draft_n_accepted`.
- **per session** — `serve_api:` lines in the container log, one per request.
- **aggregate** — `GET /metrics` (Prometheus text, llama.cpp-compatible counter names) and
  `GET /cache` (prompt-cache hit rate, evictions, KV-pool usage). The two `*tokens_seconds`
  gauges mean "since the last scrape", so hand-curling them is misleading.

Two tools ship for it:

```sh
./scripts/halogen-top.sh              # live dashboard: busy state, KV, per-request t/s, cache; piped = log lines
./scripts/halogen-top.sh --strata     # same against Strata (:8080 or --strata <url>); no cache panel
./scripts/session-audit.sh --perf     # prefill/decode for one session's own time window
./scripts/session-audit.sh --harness  # sidecar/NPU calls, latency, tokens offloaded, fallbacks
```

`halogen-top.sh` appends each scrape to `$XDG_STATE_HOME/halogen-top/history.csv`; that file is
what `--perf` uses to diff `/metrics` over a window — the counters are cumulative and carry no session
id, so a window that was never recorded can only be rebuilt from the logs. Leave it running (e.g. in
a `screen` session) for the diff to work. Grafana/Prometheus works too (scrape `127.0.0.1:8731/metrics`) —
the dashboards are yours to build; these two scripts exist because they cost no extra containers.

## Troubleshooting

| symptom | fix |
|---|---|
| any `--doctor` line red | the line prints the exact fix |
| halogen won't launch, order-9 < 200 | reboot (memory fragmentation); the launcher's 65k/2-slot fallback works without one |
| halogen tries to download something | your launch has `HALOGEN_DOWNLOAD` set — remove it; everything loads from local files |
| sessions 2.5x slower | prompt cache is OFF — never set `HALOGEN_PROMPT_CACHE=0` |
| `triage` / `dedup_scan` return an error | halogen down, or the model missing from `HALOGEN_NPU_MODELS` — check the `npu` field of `curl -s localhost:8731/health` |
| hangs or garbage when GPU and NPU work at once | GPU fabric clock not held — `./setup.sh --doctor` prints the one-time unit install |
| semantic search unavailable | `uvx` missing (install uv) or `mcp.json` lacks the semble entry — rerun `./setup.sh` and check `pi mcp list` |
| web search unavailable | `uvx` missing or `mcp.json` lacks the ddg entry — rerun `./setup.sh`; DDG rate-limits searches to 30/min by default |
| stale task on resume | checkpoint from a previous task — `rm PROGRESS.md`, restart |
| want zero-touch sidecar after reboot | `systemctl --user enable ling-tiny` (off by default on purpose) |

## Stopping the servers

`scripts/stop-all.sh` stops halogen, gufo, Strata and the ling-tiny sidecar, then pops a desktop notification (it warns if GPU memory stays held, which needs a reboot). `scripts/stop-all.sh --install` adds "Stop LLM servers" and "Start LLM servers" entries to the app menu and ~/Desktop. The start one (`scripts/start-servers.sh`) runs `./harness up` headless (the first step of `start.sh`: starts the engine you picked in setup, refreshes the install), with no terminal and no pi, and notifies when the engine answers; then run `pi`. Without a terminal it asks nothing, so an engine holding the GPU memory is left running and the notification says so. pi and engines the harness does not start (Ollama, vLLM, ...) are untouched.
