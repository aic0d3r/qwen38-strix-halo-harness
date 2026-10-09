# pi extension suite

Extensions that make pi genuinely better for daily agentic coding on Strix Halo.

## Install (fresh machine, halogen setup)

You need: a Strix Halo (or any gfx1151/UMA box) running pi, a halogen 0.16.2+
server with the NPU models, and a Ling-3.0-tiny sidecar. ~70 GB free RAM for
flash-next, ~4 GB for the sidecar.

0. Automated setup (does steps 1-2, starts the sidecar if it can, checks the
   rest and prints exact fixes):

       git clone https://github.com/aic0d3r/neon-ladder
       cd neon-ladder && ./setup.sh            # add --index <repo-dir> to also index

2. Halogen server on 127.0.0.1:8731, prompt cache ON - see the
   halogen-flash-server repo for the container/model download. Do NOT disable
   the prompt cache: agent sessions append strictly and hit 98.6-99.1% prefix
   reuse; with it disabled every turn re-prefills the whole context (~2.5x
   slower sessions). Verify:

       curl -sf localhost:8731/health
       # server log must contain: flash_serve: prompt cache ON

3. Ling-3.0-tiny sidecar on 127.0.0.1:8090 - any llama.cpp build (with --jinja)
   serving Ling-3.0-tiny-Q4_K_M.gguf:

       llama-server -a ling3.0-tiny -m Ling-3.0-tiny-Q4_K_M.gguf -ngl 99 \
         -c 32768 --jinja --host 127.0.0.1 --port 8090
       curl -sf localhost:8090/health   # every sidecar extension no-ops without it

4. pi providers in ~/.pi/agent/models.json:

       "halogen":      { "baseUrl": "http://127.0.0.1:8731/v1", "api": "openai-completions", "apiKey": "dummy", "models": [ { "id": "halogen-qwen3.8-flash-next", "contextWindow": 65536, "maxTokens": 8192, ... } ] },
       "llamacpp-tiny":{ "baseUrl": "http://127.0.0.1:8090/v1", "api": "openai-completions", "apiKey": "dummy", "models": [ { "id": "ling3.0-tiny", "contextWindow": 32768, "maxTokens": 8192, ... } ] }

   maxTokens 8192, not 32768: the server hard-rejects requests once
   context + max_tokens crosses the 65,536 window (~32.7k context trips it).

5. NPU retrieval index for each repo you want semantic search in (one-time,
   ~2.5 min for a 1M-token repo):

       python3 rag-index.py --dir <repo> --ext '.cpp,.h,.py,.ts,.sh' \
         --exclude '(\.git|build|vendor)'

Smoke test (expect the tool list to include commit + codebase_search;
"[progress-tracker] wrote PROGRESS.md" after ~10 responses):

    cd <indexed repo> && pi -p --no-skills --no-context-files \
      --provider halogen --model halogen-qwen3.8-flash-next --thinking medium \
      --tools commit,codebase_search,triage,bash,read,write \
      "List the 5 largest source files and write them to SMOKE.md"

Resume test: kill the session mid-task (Ctrl-C / timeout), then run again with
the same flags and prompt "Continue." - progress-tracker injects the task and
last state automatically; recovery should beat a fresh run.

## What is actually measured (2026-10-04, cache-on era)

Core three - these carry the measured value:

| extension | measured saving |
|---|---|
| `progress-tracker.ts` | resume after crash/kill: recovery 455s vs 572s fresh baseline; note auto-injects, zero agent adoption needed |
| `NPU-retrieval.ts` (codebase_search) | ~70ms/query, 7/7 ground-truth precision on a 943k-token index; replaces ~8.5 grep-chain turns per locate (~3-5 min/task) |
| `ling-tiny-compaction.ts` | summaries ~24s non-blocking vs ~52s blocking on the main model (decode-bound: tiny 122 t/s vs 27B ~45 t/s) |

Optional / situational:

- `ling-tiny-commit.ts` - now also an agent-invocable `commit` TOOL (agents
  never fire slash commands: 3 measured misses). Offloads commit messages to
  the sidecar; keeps main-model turns free.
- `ling-tiny-repomap.ts`, `auto-guard.ts` - cheap, fired, benefit unmeasured.
- `context-prune.ts`, `NPU-triage.ts`, `ling-tiny-triage.ts` - moved to
  `extensions/optional/` (0.0k saved / 0 firings across all in-session A/Bs;
  setup.sh removes stale copies on upgrade). `triage` + `dedup_scan` still
  ship inside NPU-retrieval.ts (same file as codebase_search) - agents never
  call them unprompted, and the tables below are component-level numbers,
  not in-session results.

Routing rule (measured): main-session turns stay on halogen - its prompt cache
makes continuation prefill nearly free. Sidecar jobs go to tiny - they are new
content (no cache inheritance) and decode-bound, where tiny is 2.2x faster and
never blocks the agent.

## The ling-tiny suite (sidecar economics, corrected) - tables below are COMPONENT-level measurements, not in-session results

All of these route decode-shaped busywork to a small local model (Ling-3.0-tiny,
~4 GB GGUF) instead of the main model. The old "8x prefill" framing was wrong
twice over: halogen prefills at ~1,360 t/s uncached and its prompt cache makes
continuations nearly free - but sidecar jobs never inherit that cache, and the
real win is decode offload + never blocking the agent.

Prerequisites: a llama.cpp server serving `ling3.0-tiny` on `127.0.0.1:8090`
(runs with `--jinja`; the commit tool talks to it over plain HTTP), and a pi
`models.json` entry with provider `llamacpp-tiny`, model id `ling3.0-tiny`.


All of these route prefill-shaped busywork to a small local model (Ling-3.0-tiny, ~4 GB GGUF)
instead of your main model. On this hardware the tiny runs ~2,100 tok/s where a 27B main model
prefills at ~250 tok/s - an 8x difference on exactly the jobs that are pure prefill.

Prerequisites: a llama.cpp server serving `ling3.0-tiny` on `127.0.0.1:8090`, and a pi
`models.json` entry with provider `llamacpp-tiny`, model id `ling3.0-tiny`.

| extension | what it does | saving |
|---|---|---|
| `ling-tiny-compaction.ts` | routes /compact (manual + auto) summarization to tiny | 8x faster compaction |
| `ling-tiny-triage.ts` | compresses oversized bash tool results BEFORE they enter history | smaller context every turn |
| `ling-tiny-commit.ts` | `commit` tool + /commit: message from the diff via sidecar | seconds instead of main-model turns |
| `ling-tiny-branch-summary.ts` | /tree branch navigation summarizes abandoned entries with tiny | free branch history |
| `ling-tiny-repomap.ts` | /repomap: compact repo map inserted before the first turn | main model starts oriented |

## NPU-retrieval.ts (halogen 0.16 NPU)

Three tools + one command, each measured and earned:

| tool | what it does | measured |
|---|---|---|
| `codebase_search(query, k?)` | semantic search (NPU embed + rerank, ~100-300ms) | 15/20 correct-file vs 9/20 for grep on 20 intent queries across 2 repos |
| `dedup_scan(dirs[], threshold?)` | find near-duplicate files across directories | 45 pairs found across 4 repos in 8.4s, catches renames |
| `triage(text, question, options[])` | fast routing decision (0.8B classifier, ~120ms) | 78% on binary decisions; use for guardrail hints and issue classification, NOT as a security boundary |
| `/rag-index <dir>` | build `<dir>/.rag/{index.json,vectors.f32}` | ~5,700 tok/s on NPU |


## harness-tune.ts

`/tune [key value]` - live performance knobs from inside pi: compaction threshold,
per-turn token budget, sampling temperature (0 for deterministic benches),
llama.cpp-side ubatch. Reads and writes the real config files with validation.
(Tool-result triage threshold was removed 10-06: it pointed at
`extensions/optional/ling-tiny-triage.ts`, which is not installed or loaded, so
the knob was a silent no-op.)

## Why this suite

The ling-tiny extensions handle the *prefill-shaped* half of session overhead (compaction,
commits, repo maps, tool-result bloat) at 8x the main model's throughput. The NPU extensions
handle the *semantic* half (finding code, detecting duplicates, routing decisions) at ~100ms
per call without touching the iGPU. Together they cover both axes of agent-session cost.

## Context management extensions (new 10-03, from learn-harness-engineering)

| extension | what it does | saving |
|---|---|---|
| `context-prune.ts` | deduplicates identical tool outputs + strips status noise before compaction | 10-30% less context to compact; every dedup saves a re-prefill on all subsequent turns |
| `stop-verify.ts` | runs bash -n / node --check / py_compile on modified files when the agent tries to stop | prevents broken builds from shipping (deterministic, no model) |
| `progress-tracker.ts` | auto-writes PROGRESS.md every 5 turns; injects it on session resume | eliminates 5-10 min of re-exploration on session restart |

These are pure TypeScript with zero model calls - they cost nothing to run and
save context/time unconditionally. Install: copy to `~/.pi/agent/extensions/`.

## Passive NPU extensions (new 10-03, zero adoption needed)

| extension | what it does | measured |
|---|---|---|
| `auto-guard.ts` | screens every user message via qwen3guard on NPU (~85ms), injects caution if flagged | 80% accuracy on agent-relevant messages |
| `NPU-triage.ts` | summarizes oversized tool results via qwen3.5-2b on NPU before they enter context | 1.6x faster than main model (9.6s vs 15.5s), 98% context reduction |

These fire automatically on pi lifecycle events - the agent doesn't know they exist.
The context saving compounds: every summarized output saves ~2k tokens × remaining
turns in re-prefill cost (break-even at ~3 remaining turns).
