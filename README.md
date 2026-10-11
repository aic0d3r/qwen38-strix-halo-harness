# Pi on Strix Halo — a fully local coding agent: 125B on the GPU, four small models on the NPU, zero cloud

<div align="center">

[![Release](https://img.shields.io/github/v/release/aic0d3r/qwen38-strix-halo-harness)](https://github.com/aic0d3r/qwen38-strix-halo-harness/releases)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

[What you get](#what-you-get) •
[What runs where](#what-runs-where) •
[Install](#install) •
[Quick start](#quick-start) •
[The NPU](#the-npu) •
[Engines](#engines) •
[Crash recovery](#crash-recovery) •
[Uninstall](#uninstall) •
[Upgrades](#upgrading-halogen) •
[Troubleshooting](#troubleshooting) •
[Docs](#documentation)

</div>

A coding-agent harness for **AMD Strix Halo** (Ryzen AI Max+ 395 / gfx1151): the
[pi coding agent](https://github.com/earendil-works/pi) driving **Qwen3.8 Flash-Next
(125B)** on the iGPU, with the NPU running the 0.6-0.8B side jobs (fast decisions, embedding
dedup, injection screening) and a llama.cpp sidecar handling commits and compaction. Nothing
leaves the box — no API key, no outbound calls.

**The model was never the hard part.** This repo is everything *around* the model that makes a
local agent usable day to day: one-command setup, crash recovery that survives a killed
server, prompt-cache-safe sidecars, speed tooling, and the measured numbers behind every
claim.

> [!TIP]
> **Just want the agent running?** `./setup.sh --halogen auto`, then `./start.sh ~/my-project`.
> The two things you must supply are the [model files](#model-files) (~124 GB, local) and
> [docker](#requirements). Everything else is optional, and `--doctor` tells you what is live.

Every number here is measured and logged — **[BENCHMARKS.md](docs/BENCHMARKS.md)** holds the
receipts. **[Flash-Next vs GLM 5.3: local vs cloud on a real debugging task](docs/local-vs-cloud-model-comparison.md)**
is the seven-run head-to-head, rated against verified ground truth.

![Flash-Next vs GLM 5.3 tiers: time to first token and decode rate](docs/charts/local-vs-cloud.png)

Measured on an ASUS ROG Flow Z13 (Ryzen AI Max+ 395, Radeon 8060S, 128 GB) at 70 W sustained.

## What you get

- **`setup.sh`** — one idempotent command: extensions, providers, sidecar download,
  keep-alive units, halogen launch, memory check. Touches nothing you configured yourself;
  probes each piece and reports what is actually live — the NPU guard model answers a real
  `/v1/moderations` probe, the vision tower shows `enabled: true` on `/health`, and the smoke
  test verifies its own file artifact instead of trusting the model's report.
- **`start.sh <dir>`** — the daily command: heals any dead server, then drops you into the pi
  TUI.
- **Crash recovery** — kill the agent or lose the server mid-task, relaunch and say
  "continue". `progress-tracker` checkpoints task + partial state; verified across six
  scenarios including `kill -9` at response 1, server death mid-task, and 15-deep compaction
  chains.
- **`npu-retrieval`** — two tools built on the NPU decider and embedder: `triage` (a
  constrained decision with per-option probabilities in ~120 ms) and `dedup_scan`
  (near-duplicate files by embedding similarity). See [The NPU](#the-npu).
- **`auto-guard`** — screens every prompt against the NPU guard model; a fail-open tripwire
  (42 % recall / 0 % false positives on the 30-prompt canary), not a security boundary.
- **The ling-tiny sidecar suite** — `ling-tiny-compaction` (non-blocking, thinking off, 10/10
  rule retention on the canary), `ling-tiny-commit`, `ling-tiny-branch-summary`, and
  `ling-tiny-repomap` (freshness-cached). None of it enters the main model's context — see
  [sidecar isolation](#sidecar-isolation-ling-tiny-commit-and-friends).
- **Semantic code search = semble (MCP)** — `setup.sh` wires the pinned semble server into
  `mcp.json` (`search` + `find_related`, direct exposure). A/B on 18 ground-truth queries
  against the NPU RAG pipeline it replaced: 17/18 vs 13/18 top-3 hits, 2-3x faster queries,
  ~40x faster indexing. It indexes any local path or remote git URL and needs no NPU, so it
  works on `--no-halogen` and llama.cpp-path installs too. Needs `uvx` on PATH (checked at
  setup); indexes on first query, so there is nothing to pre-build.
- **Also in `extensions/`** — `harness-tune` (`/tune` live knobs), `turn-timer`, and
  `subagent/` (worker/scout/reviewer/planner, session-model inheritance). Measured no-ops
  were deleted (last removal in `5bc375f`, `extensions/optional/`); receipts in
  [`extensions/README.md`](extensions/README.md).
- **`server/`** — launch scripts: `start-halogen.sh` (the recommended engine) and the
  llama.cpp path: `start-flashnext.sh` (MTP drafter plus the reasoning flags that stop it
  burning its whole output on thinking), `start-qwen38.sh` (27B, DFlash2, 256k ctx),
  `start-ling-tiny.sh` (aux). Ubatch ceilings documented in the headers.
- **`config/`** — `models.json.example` (llama.cpp path, both models pre-wired) and the
  compaction snippet. Two numbers there must be understood, not copied: `reserveTokens` is
  per-model, and on halogen `maxTokens` must stay <= 8192 or the server 400s past ~32.7k ctx
  (the 32768 in the llama.cpp-path entries applies to that engine only).
- **`bench/`** — `harness-ab.sh` (baseline vs kill+resume A/B, one command),
  `report-accuracy.py` (grades every file:line claim in a report against the tree),
  `check-sidecar.sh`, `check-fixes.sh`, and the `guard-canary.py` / `compaction-retention.py`
  measurement canaries.
- **`templates/`** — pi prompt templates (installed to `~/.pi/agent/prompts/`): `/gauntlet`
  (multi-phase stress session that crosses the compaction trigger) and `/speedtest` (9-probe
  engine benchmark with pass/fail bands).
- **Voice (opt-in)** — `./setup.sh --voice` wires the
  [privateer-speak](https://npm.im/privateer-speak) pi package to a local Lemonade server:
  whisper STT into the composer (`/talk`, alt+t) and kokoro TTS answers (`/speak`), all on the
  CPU while the NPU handles the agent's small jobs. Measured 0.43 s STT, 0.3 s TTS.

## What runs where

One box, four model servers (five with voice), three pieces of silicon. This is the layout the
rest of the README assumes:

| piece | endpoint | silicon | role |
|---|---|---|---|
| **halogen** — Qwen3.8 Flash-Next 125B | `:8731` | iGPU | the agent's main model: chat, tools, vision |
| **NPU sidecars** — four 0.6-0.8B models | `:8731` (same server, own endpoints) | NPU | `auto-guard` screening, `triage`, `dedup_scan` — see [The NPU](#the-npu) |
| **ling3.0-tiny** (llama.cpp) | `:8090` | iGPU | commit messages, compaction summaries, repo maps |
| **semble** (MCP over `uvx`) | child process | CPU | semantic code search — no NPU, no model server |
| **Lemonade** (opt-in `--voice`) | `:13305` | CPU | whisper STT + kokoro TTS |

> [!NOTE]
> The NPU does **not** do codebase search. It used to — an embedding + rerank RAG pipeline —
> and that moved to the semble MCP server in v1.1, which scored 17/18 vs 13/18 top-3 and was
> 2-3x faster per query. What the NPU still owns is everything the 125B model is too slow or
> too expensive for: per-prompt injection screening, one-shot routing decisions, and
> embedding sweeps over thousands of files. See [The NPU](#the-npu).

## Requirements

- An **AMD Strix Halo box** (gfx1151) — the NPU driver needs IOMMU enabled.
- **docker** — a system package; setup will not sudo-install it for you.
- The **[pi coding agent](https://github.com/earendil-works/pi)** — setup installs it via npm
  if missing.
- The **[halogen model files](#model-files)** — ~124 GB, from
  [huggingface.co/peonist-ai/halogen-qwen3.8-flash-next](https://huggingface.co/peonist-ai/halogen-qwen3.8-flash-next).
- `uvx` (from [uv](https://docs.astral.sh/uv)) if you want semantic code search.

## Model files

**Minimum download ~124 GB** — do not grab the whole repo, it carries ~190 GB of optional
variants:

| file | size | why |
|---|---|---|
| `qwen38-flash-next-v2.hgn` | 66.7 GB | the main checkpoint (MTP drafter head included) |
| `qwen38-flash-next-ngram.hgn` | 51.2 GB | **required** — the checkpoint's weight lookup table (read through the page cache; not an n-gram drafter despite the name) |
| `qwen38-flash-next-vision.hgn` | 0.9 GB | vision tower — image input is enabled by default |
| `tokenizer/` | small | chat template the engine loads |
| `npu/decider-0.8b/` | 2.3 GB | `triage` — constrained decisions (~120 ms) |
| `npu/qwen3-embedding-0.6b/` | 848 MB | `dedup_scan` embeddings |
| `npu/qwen3guard-gen-0.6b/` | 787 MB | injection screening (`auto-guard`) |
| `npu/qwen3-reranker-0.6b/` | 821 MB | **unused since v1.1** — only in the default `HALOGEN_NPU_MODELS` list because the image ships it; drop it (see [The NPU](#the-npu)) |

Put everything under one directory (e.g. `~/models/halogen-qwen3.8-flash-next/`) with the
four NPU models in a lowercase `npu/` subdirectory; the launcher mounts it read-only at
`/models` inside the container. The NPU models live in **four separate repos**, not in the
main weights repo:

```sh
hf download peonist-ai/halogen-qwen3.8-flash-next \
  qwen38-flash-next-v2.hgn qwen38-flash-next-ngram.hgn qwen38-flash-next-vision.hgn tokenizer/ \
  --local-dir ~/models/halogen-qwen3.8-flash-next
for n in decider-0.8b qwen3-embedding-0.6b qwen3guard-gen-0.6b qwen3-reranker-0.6b; do
  hf download peonist-ai/halogen-npu-$n --local-dir ~/models/halogen-qwen3.8-flash-next/npu/$n
done
```

(needs `pip install -U "huggingface_hub[cli]"`; skip the reranker if you will drop it from
`HALOGEN_NPU_MODELS`)

Skip the rest: `w4b.hgn` (124 GB), `ht43.hgn` (57.6 GB), `MTP.hgn`, `w4b.overlay*.hgn`,
`qwen3.5-2b` — the engine's own checkpoint carries its draft head. Upstream also ships an
optional `ht43.hgn` checkpoint (smaller/faster tradeoffs); the default remains `v2` — pass
its path explicitly if you want it.

Don't want to fetch by hand? `./setup.sh --halogen auto` offers to download exactly this set
(resumable — re-run the same command to continue) after checking free space.
## Install

```sh
git clone https://github.com/aic0d3r/qwen38-strix-halo-harness
cd qwen38-strix-halo-harness

./setup.sh                      # one-time: extensions, providers, sidecar, pi defaults
./setup.sh --halogen auto       # launch halogen from your local checkpoint (zero downloads)
./setup.sh --doctor             # status check any time (ends with a live model ping)
```

Other flags: `--halogen <path>` (explicit checkpoint), `--voice` (opt-in STT/TTS),
`--halogen-upgrade [tag]` (see [Upgrading](#upgrading-halogen)), `--uninstall`, `--no-halogen`.

`--halogen auto` finds `*.hgn` in `.`, `/models`, `~/models`, `~/Downloads` (or pass the path
explicitly). The launch is pinned to your local files: no `HALOGEN_DOWNLOAD`, NPU models load
from `/models/npu/`, the engine makes no outbound connections. Restart later with
`./start-halogen.sh` (written on first launch). First cold boot after a reboot pins the
47.7 GiB lookup table from disk, so a few minutes of startup is normal.

Plain `pi` also works after setup — startup defaults point at halogen (existing overrides are
never touched).

**Pi on another machine?** Two supported shapes:

- `./setup.sh --no-halogen` — pi-only install: no halogen provider, no NPU extensions, no
  container management. For a laptop pi against your own or cloud providers.
- **Remote Strix Halo appliance** — normal install on the laptop, then point `models.json`'s
  halogen `baseUrl` at the appliance and export `PI_NPU_BASE` so `auto-guard` and the NPU
  tools (`triage`, `dedup_scan`) reach its NPU endpoints over the network. Codebase search
  needs none of this — semble indexes locally wherever pi runs.

## Quick start

```sh
./start.sh ~/my-project         # heals dead servers, then opens the pi TUI
```

In the session: `read`/`edit`/`bash` as usual, `mcp__semble__search` for "where is X
implemented", `commit` for a sidecar-written message, `triage` when a decision is a one-word
call, and `/tune` to change compaction headroom, output budget, temperature or ubatch
mid-session.

### Context size

The default launch is halogen's native max: **262,144 ctx x 4 slots**. Memory is not the
constraint (~67 GB model + ~115 MiB/slot in-place cache) — allocator fragmentation is. The
launcher's pre-flight gate handles it: if order-9 pages are low (box up a long time,
big-model churn) it first attempts recovery (page-cache drop, compaction) and only proceeds on
a healthy allocator; otherwise it fails in seconds with the exact fix command instead of
wedging for 30+ minutes. Install the `vm-compact.timer` (setup/doctor print the one-line
command) to keep the allocator healthy day to day.

```sh
HALOGEN_CTX=65536 HALOGEN_KV_SLOTS=2 ./start-halogen.sh   # small-context opt-out
```

After any ctx change the launcher syncs `models.json`'s `contextWindow` (so pi token-budgets
against the real window) and scales `compaction.reserveTokens` (60k at 262k, 10k at 65k).

### Crash recovery

**pi dies, you kill it, the server dies mid-task** — doesn't matter. Relaunch `./start.sh` in
the same directory and say "continue": `progress-tracker` wrote a checkpoint (task + partial
state) and the fresh session picks it up. **Verified across six scenarios**, including
`kill -9` at response 1, server death mid-task, and 15-deep compaction chains (reliable to
~8-10 compactions; deeper chains deserve a re-orientation pass).

Known limits, measured and documented: one pi task per repo (concurrent sessions race on the
checkpoint — set `PI_PROGRESS_FILE` per task for parallel work), and a stale checkpoint from a
previous task is data, not instructions: `rm PROGRESS.md` when switching tasks.

## The NPU

The NPU is not where the big model runs. It runs four small models whose whole job is to
answer, in 0.1-1 s, questions the 125B model would take 5-90 s on — and without touching the
main model's prompt cache. halogen exposes them on the same `:8731` server.

| model | how it is called | used by | what it does | measured |
|---|---|---|---|---|
| `decider-0.8b` | `/v1/chat/completions` with a `json_schema` enum + logprobs | `triage` | picks one of 2-5 options and returns per-option probabilities | ~120 ms, 78 % on 18 binary prompts |
| `qwen3-embedding-0.6b` | `/v1/embeddings` | `dedup_scan` | embeds files for near-duplicate detection | 70 ms/query, 38 files in 8.4 s |
| `qwen3guard-gen-0.6b` | `/v1/moderations` | `auto-guard`, every prompt | flags prompt injection | 70-130 ms, 42 % recall / 0 % fp |
| `qwen3-reranker-0.6b` | `/v1/rerank` | **nothing since v1.1** | ranked the retired NPU search | 130 ms, idle |

All of it is optional and every extension degrades quietly: with no halogen server,
`auto-guard` fails open, `triage` and `dedup_scan` return an error string, and the agent keeps
working (search, messages and compaction never needed the NPU).

```sh
# which NPU models the server has configured vs actually loaded (they load on first use)
curl -s localhost:8731/health | python3 -c "import json,sys;print(json.load(sys.stdin)['npu'])"

# trim the loaded set — the reranker is dead weight unless you re-add reranking yourself
HALOGEN_NPU_MODELS=decider-0.8b,qwen3-embedding-0.6b,qwen3guard-gen-0.6b ./start-halogen.sh
```

> [!WARNING]
> **GPU + NPU at the same time is only safe with the GPU fabric clock held** (halogen NPU
> docs) — without it, concurrent GPU and NPU work can hang the device or corrupt output.
> `./setup.sh --doctor` probes it and prints the one-time host-side install for the
> `halogen-fabric-clock` unit when it is not held. The container holds the clock while it
> runs; the gap is any NPU call made with no container up.
## Engines

Two stacks, same model family, different engines:

| stack | what | when |
|---|---|---|
| **halogen** (recommended) | NPU+GPU engine, Qwen3.8 Flash-Next (125B) from a local checkpoint, zero network | just works, **262k ctx** x 4 slots |
| **llama.cpp (Vulkan)** | Nathan's builds, full control, DFlash2/MTP spec decode, vision, local GGUFs, zero network, **256k ctx** max | tuning and the **27B** runs |

Halogen is the only engine this harness provisions and verifies — by design: the extensions
that make it worth running ride on halogen's NPU sidecars, and no other engine exposes them.
You can point `models.json` at any OpenAI-compatible server yourself; pi keeps working and the
degradation is graceful but real (all measured):

| feature | on gufo 0.3.0 / plain llama.cpp |
|---|---|
| chat, tools, streaming, reasoning control, structured output | works |
| semantic search (semble MCP) | works — no NPU involved |
| prompt-injection screening (`auto-guard`) | dead — no `/v1/moderations` |
| `triage` / `dedup_scan` | dead — no decider or embedder |
| image input | dead as launched (no `--mmproj` wired) |
| `/health` verification (cache, vision) | degrades to "unknown" |
| warm-cache replay | ~2.5x slower on gufo (133-147k vs 336-382k tok/s) |
| deep context | gufo collapses past ~100k (13 vs 41 t/s at 131k) |

`./setup.sh --doctor` tells you when a non-halogen engine is answering on `:8731`.
**Never run two of these engines at once** — they fight over the same unified-memory heap and
device-lost follows.

**Cloud models are a supported hybrid.** Point pi at any cloud provider and keep halogen
running, and the NPU tools plus the whole ling-tiny sidecar suite stay on-device: screening,
search and compaction never follow your code to the cloud. `compaction.reserveTokens` carries
over as-is — it is a space reserve, not a trigger point, so a 1M-context cloud model simply
compacts later. The one cost is switching itself: the first cloud turn re-uploads the session,
and halogen's prompt cache holds your prefix warm (within its LRU) for when you switch back.

## Sidecar isolation (ling-tiny-commit and friends)

The ling-tiny extensions call `:8090` directly instead of the main model — on purpose. Diffs,
session transcripts and repo inventories never enter halogen's context window or prompt cache.
A side call can therefore never evict the active session's cached prefix (an eviction costs
~150-190 s of re-prefill; the sidecar call costs ~0.5-2.5 s).

ling3.0-tiny runs with thinking disabled at the template level (`enable_thinking: false`): the
think pass ate token budget and 8.6x'd latency, and the rule-retention canary stays 10/10
without it.

## Uninstall

`./setup.sh --uninstall` removes everything the installer provisioned: extensions, templates,
skills, theme, pi defaults and the two providers (only where they still match what setup set),
and the systemd units — after backing all of it to `~/pi-harness-uninstall-backup-*.tar.gz`.

**Deliberately left alone:** your own model entries, `mcp.json`, the sidecar GGUF (the command
to reclaim it is printed), the halogen container and your checkpoint files.

## Upgrading halogen

The image tag is **pinned** in `server/start-halogen.sh` (`HALOGEN_IMAGE_TAG`, default the
version this harness was last validated on). One command does it safely:

```sh
./setup.sh --halogen-upgrade          # default: latest
./setup.sh --halogen-upgrade 0.18.0   # or a specific tag
```

It pulls the image, refreshes the NPU model files (new images can need new NPU device files —
0.18 added the embedding `u10.elf`), restarts, then runs the acceptance battery: health +
version match, prompt cache on, NPU probes < 1 s, guard smoke, MTP decode > 30 t/s, and a
`pi -p` smoke. Only then does the pin move; any failed check restores the previous NPU files
and restarts on the previous pin automatically.

![halogen 0.16.2 to 0.17.1: NPU call latency 4000ms to 130ms, decode 44 to 64 tok/s](docs/charts/halogen-0171-upgrade.png)

Manual path: pull, change the pin, restart, then `scripts/session-audit.sh` + NPU probes +
`pi -p` smoke + `go test` in any active project. Read the changelog first — tool-calling,
schema, cache and log-routing behavior have all changed between releases.

## Watching speed

Three official surfaces, no built-in web UI in the image (`/`, `/gui`, `/dashboard` are 404):

- **per request** — every response (and the last streamed chunk) carries a `timings` object:
  `prompt_per_second`, `predicted_per_second`, `cache_n`, `draft_n_accepted`.
- **per session** — `serve_api:` lines in the container log, one per request.
- **aggregate** — `GET /metrics` (Prometheus text, llama.cpp-compatible counter names) and
  `GET /cache` (prompt-cache hit rate, evictions, KV-pool usage). The two `*tokens_seconds`
  gauges mean "since the last scrape", so hand-curling them is misleading.

Two tools ship for it:

```sh
./scripts/halogen-top.sh              # live dashboard: window + lifetime t/s, Ctrl-C quits
./scripts/session-audit.sh --perf     # prefill/decode for one session's own time window
```

`halogen-top.sh` appends each scrape to `$XDG_STATE_HOME/halogen-top/history.csv`; that file
is what `--perf` diffs `/metrics` over a window — the counters are cumulative and carry no
session id, so a window that was never recorded can only be rebuilt from the logs. Leave one
running in a screen for the diff to work. Grafana/Prometheus works too (scrape
`127.0.0.1:8731/metrics`) — the dashboards are yours to build; these two scripts exist because
they cost no extra containers.

## Setup: llama.cpp path (full control)

1. **Engine** — [Nathanw1014/strix-halo-llamacpp](https://github.com/Nathanw1014/strix-halo-llamacpp)
   v0.7.4.1+ (fixed stale-KV and a top-k race that were in upstream too); the Vulkan work is
   migrating to [halo-box/strix-llama.cpp](https://github.com/halo-box/strix-llama.cpp),
   either tree works. Build from source if you can — the portable payload leaves 6-24 % decode
   on the table.
2. **Models**:
   - 27B target: [unsloth/Qwen3.8-27B-GGUF](https://huggingface.co/unsloth/Qwen3.8-27B-GGUF)
     UD-Q4_K_XL (Dynamic v3, the pick; the Q4-Q8 PPL plateau is flat). For exact reproduction of
     published-era numbers: UD-Q5_K_XL @ revision `408fcc1807ab` (v2), set `TARGET_GGUF`.
   - 27B vision: `mmproj-F16.gguf` (same repo), passed as `-mm ... --mmproj-offload` so pi
     sessions always have the projector on GPU. A/B (2026-09-07): projector GPU vs CPU
     placement is byte-identical on decode — vision is free.
   - pi image input: the model entry in `models.json` must declare
     `"input": ["text", "image"]` or pi refuses to send images.
   - 27B drafter: [incoai/Qwen3.8-27B-DFlash2-GGUF](https://huggingface.co/incoai/Qwen3.8-27B-DFlash2-GGUF)
     Q4_K_M · Flash-Next drafter:
     [EasiiX MTP Q8_0](https://huggingface.co/EasiiX/Qwen3.8-Flash-Next-MTP-Strix-Halo-GGUF)
   - Flash-Next (llama.cpp variant):
     [unsloth UD-IQ4_XS](https://huggingface.co/unsloth/Qwen3.8-Flash-Next-GGUF) (3 shards) +
     `mmproj-F16.gguf`
   - Aux sidecar: [inclusionAI/Ling-3.0-tiny](https://huggingface.co/inclusionAI/Ling-3.0-tiny)
     Q4_K_M (runs happily on the portable v0.7.6.1 payload) — this is the one every ling-tiny
     extension talks to on `:8090`; it is a GPU model, not an NPU one.
   - Template (27B): vendored in `config/template/` (`sharp-v22.3.2.jinja`). Upstream:
     [peculiar-ragdoll/Qwen-Sharp-Chat-Templates](https://huggingface.co/peculiar-ragdoll/Qwen-Sharp-Chat-Templates)
3. **Servers** — one big model at a time on this APU; a second 27B-class server means
   device-lost.

   ```sh
   ENGINE_DIR=... MODEL_DIR=... server/start-flashnext.sh   # daily driver
   ENGINE_DIR=... MODEL_DIR=... server/start-qwen38.sh      # 256k / low-memory
   ENGINE_DIR=... MODEL_DIR=... server/start-ling-tiny.sh   # sidecar (second terminal)
   ```

   **GPU memory** — the ~91 GB model lives in the GTT heap, so size it: boot args
   `amdgpu.gttsize=126976 ttm.pages_limit=32505856 ttm.page_pool_size=32505856` (on
   systemd-boot put them on the single `LINUX_OPTIONS` line — a stray newline there makes the
   loader silently drop the whole line). **Never force `--fit off`**: full offload onto an
   undersized heap deadlocks the iGPU and hard-locks the host.
4. **pi wiring**:

   ```sh
   cp config/models.json.example ~/.pi/agent/models.json   # adjust paths/ports
   cat config/settings-snippet.json                        # merge into settings.json
   cp extensions/*.ts ~/.pi/agent/extensions/
   ```

   Two numbers in the snippet you must understand, not copy: `reserveTokens` ships **10240**
   because that is right for Flash-Next's 65536 window; on the 27B's 262144 window use
   **~167144** instead. `thinkingBudgets` (per-request budgets pi sends) are what actually
   govern pi sessions; the server's `--reasoning-budget` flag is only a default for clients
   that do not send one. On this path the NPU extensions are inert — there is no halogen to
   serve them (`--no-halogen` is the right install shape for a llama.cpp-only box).
5. **Verify you are actually local** — `curl localhost:8080/metrics` while pi runs; if
   `prompt_tokens_total` is not climbing, pi fell back to a cloud provider.

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
| stale task on resume | checkpoint from a previous task — `rm PROGRESS.md`, restart |
| want zero-touch sidecar after reboot | `systemctl --user enable ling-tiny` (off by default on purpose) |

## Documentation

- **[BENCHMARKS.md](docs/BENCHMARKS.md)** — the measured numbers: decode/prefill, NPU
  latencies, canary results, and every claim in this README with its logged run.
- **[Local vs cloud model comparison](docs/local-vs-cloud-model-comparison.md)** — seven runs,
  one real debugging task: Flash-Next vs glm-5.3 / flashx across two clients, answers rated
  against verified ground truth.
- **[extensions/README.md](extensions/README.md)** — per-extension notes and the measured value
  table.
- **[CHANGELOG.md](CHANGELOG.md)** — release history, including what was removed after testing.

## Posts

- [Qwen3.8-27B on Strix Halo at 31 t/s decode, the full stack guide](https://www.reddit.com/r/LocalLLaMA/comments/1vsw6nz/) (r/LocalLLaMA)
- [Running a local coding agent on Strix Halo with pi + llama.cpp](https://www.reddit.com/r/StrixHalo/comments/1w6c5nz/) (r/StrixHalo)
- [Qwen3.8-Flash-Next on Strix Halo: 40 t/s sustained decode](https://www.reddit.com/r/StrixHalo/comments/1w6cf5t/) (r/StrixHalo)
- [Neon Ladder: a playtest-graded benchmark for local LLM stacks](https://www.reddit.com/r/LocalLLaMA/comments/1w6cjm5/) (r/LocalLLaMA)

Companion repo: **[neon-ladder](https://github.com/aic0d3r/neon-ladder)** — the playtest-graded
benchmark this stack was validated on. Benchmark protocol questions live there; harness
questions live here.

## License

[MIT](LICENSE)
