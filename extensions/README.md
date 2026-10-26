# pi extension suite

Extensions that make pi genuinely better for daily agentic coding on Strix Halo.

## Install (fresh machine, halogen setup)

You need: a Strix Halo (or any gfx1151/UMA box) running pi, a halogen 0.16.2+
server with the NPU models (`decider-0.8b` for `triage`, `qwen3-embedding-0.6b`
for `dedup_scan`, `qwen3guard-gen-0.6b` for `auto-guard`; the reranker is no
longer launched by default), and a Ling-3.0-tiny sidecar on :8090.
~70 GB free RAM for flash-next, ~4 GB for the sidecar.

1. Automated setup (does steps 2-4, starts the sidecar if it can, checks the
   rest and prints exact fixes):

       git clone https://github.com/aic0d3r/qwen38-strix-halo-harness
       cd qwen38-strix-halo-harness && ./setup.sh

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
         -c 131072 --jinja --host 127.0.0.1 --port 8090
       curl -sf localhost:8090/health   # every sidecar extension no-ops without it

4. pi providers in ~/.pi/agent/models.json:

       "halogen":      { "baseUrl": "http://127.0.0.1:8731/v1", "api": "openai-completions", "apiKey": "dummy", "models": [ { "id": "halogen-qwen3.8-flash-next", "contextWindow": 65536, "maxTokens": 8192, ... } ] },
       "llamacpp-tiny":{ "baseUrl": "http://127.0.0.1:8090/v1", "api": "openai-completions", "apiKey": "dummy", "models": [ { "id": "ling3.0-tiny", "contextWindow": 131072, "maxTokens": 8192, ... } ] }

   maxTokens 8192, not 32768: the server hard-rejects requests once
   context + max_tokens crosses the 65,536 window (~32.7k context trips it).

5. Nothing for search: semantic search is the semble MCP server (wired by
   setup.sh into mcp.json; indexes on first query, ~1s for this repo).

Smoke test (expect the tool list to include commit + mcp__semble__search;
"[progress-tracker] wrote PROGRESS.md" after ~10 responses):

    cd <indexed repo> && pi -p --no-skills --no-context-files \
      --provider halogen --model halogen-qwen3.8-flash-next --thinking medium \
      --tools commit,mcp__semble__search,triage,bash,read,write \
      "List the 5 largest source files and write them to SMOKE.md"

Resume test: kill the session mid-task (Ctrl-C / timeout), then run again with
the same flags and prompt "Continue." - progress-tracker injects the task and
last state automatically; recovery should beat a fresh run.

## What is actually measured (2026-10-04, cache-on era)

Core three - these carry the measured value:

| extension | measured saving |
|---|---|
| `progress-tracker.ts` | resume after crash/kill: recovery 455s vs 572s fresh baseline; auto-injects, so zero agent adoption needed |
| semble MCP (`search`/`find_related`) | replaced NPU codebase_search 2026-10-10: A/B 17/18 vs 13/18 top-3 hit-rate, 0.55s p50 queries, 1.2s index builds; no NPU required |
| `ling-tiny-compaction.ts` | summaries ~24s non-blocking vs ~52s blocking on the main model (decode-bound: tiny 122 t/s vs 27B ~45 t/s) |

Optional / situational:

- `ling-tiny-commit.ts` - now also an agent-invocable `commit` TOOL (agents
  never fire slash commands: 3 measured misses). Offloads commit messages to
  the sidecar; keeps main-model turns free.
- `ling-tiny-repomap.ts`, `auto-guard.ts` - cheap, fired, benefit unmeasured.
- `image-prune.ts` - a guardrail, not a saving: every turn re-sends the whole transcript, so
  each screenshot ever read stays in the request (a provider caps it: DeepInfra 30) and keeps
  costing prefill. Only the newest 4 images go out; older or duplicate ones become a one-line
  re-read hint. Request-local: session files keep every image. No-op for the default text-only
  models, where pi never sends an image. `PI_MAX_HISTORY_IMAGES` (default 4, 0 disables). Resolution is capped separately by `scripts/set-image-limits.py` (run by `setup.sh`).
- `context-prune.ts`, `npu-triage.ts`, `ling-tiny-triage.ts` - deleted
  (last in commit `5bc375f` under `extensions/optional/`; 0.0k saved / 0 firings across all in-session A/Bs;
  setup.sh removes stale copies on upgrade). `triage` + `dedup_scan` still
  ship inside npu-retrieval.ts (search moved to the semble MCP server) - agents never
  call them unprompted, and the tables below are component-level numbers,
  not in-session results.

Routing rule (measured): main-session turns stay on halogen - its prompt cache
makes continuation prefill nearly free. Sidecar jobs go to tiny - they are new
content (no cache inheritance) and decode-bound, where tiny is 2.2x faster and
never blocks the agent.

## The ling-tiny suite (sidecar economics) - COMPONENT-level measurements, not in-session results

These route decode-shaped busywork to a small local model (Ling-3.0-tiny, ~4 GB GGUF) instead of
the main model. Halogen's prompt cache makes continuations nearly free, but sidecar jobs never
inherit that cache, so the win is decode offload and never blocking the agent.

Prerequisites: a llama.cpp server serving `ling3.0-tiny` on `127.0.0.1:8090` (with `--jinja`),
and a pi `models.json` entry with provider `llamacpp-tiny`, model id `ling3.0-tiny`.

| extension | what it does |
|---|---|
| `ling-tiny-compaction.ts` | routes /compact (manual + auto) summarization to tiny |
| `ling-tiny-commit.ts` | `commit` tool + /commit: message from the diff via sidecar |
| `ling-tiny-branch-summary.ts` | /tree branch navigation summarizes abandoned entries with tiny |
| `ling-tiny-repomap.ts` | /repomap: compact repo map inserted before the first turn |

## npu-retrieval.ts (halogen NPU)

Two tools, each measured:

| tool | what it does | measured |
|---|---|---|
| `dedup_scan(dirs[], threshold?)` | find near-duplicate files across directories | 45 pairs found across 4 repos in 8.4s, catches renames |
| `triage(text, question, options[])` | fast routing decision (0.8B classifier, ~120ms) | 78% on binary decisions; use for guardrail hints and issue classification, NOT as a security boundary |

## harness-tune.ts

`/tune [key value]` - live performance knobs from inside pi: compaction threshold,
per-turn token budget, sampling temperature (0 for deterministic benches),
llama.cpp-side ubatch. Reads and writes the real config files with validation.
(Tool-result triage threshold was removed 10-06: it pointed at
`ling-tiny-triage.ts`, which was never installed or loaded, so
the knob was a silent no-op.)

## ui-ux-guard.ts (deterministic UI gate, no model)

Runs the impeccable design detector after every `edit`/`write` on a UI file
(`.css .scss .sass .less .html .vue .svelte .astro .tsx .jsx`) and appends any
new finding to that tool result, so the agent has to fix it or dismiss it with a
stated reason. Impeccable ships hook manifests for Claude Code, Codex, Cursor,
Copilot and Gemini - not for pi - so this is the pi wiring for the same check.

- Cost: one subprocess, ~25 ms per UI edit, no model call. Non-UI tools and
  errored edits return immediately.
- Detector exit codes are the lint convention: 0 clean, 1 scanner error,
  **2 = findings reported** (only >2 stays silent).
- Deduped per file+rule+line for the life of the process, so the same finding
  does not re-nag on every later edit.
- **Inert by design if the detector is missing.** This harness ships no skills
  (see the 1.0.1 drop of vendored design skills), so the extension no-ops unless
  `~/.pi/agent/skills/impeccable/scripts/impeccable` exists; set
  `IMPECCABLE_BIN` to point it somewhere else. One-shot disable:
  `IMPECCABLE_HOOK_DISABLED=1`.

## syntax-guard.ts (deterministic syntax gate, no model)

After every `edit`/`write`, runs a single-file syntax check and appends the error to
that tool result, so a broken edit is fixed on the next turn. Checkers: `python3` ast
(`.py`), `bash -n` (`.sh .bash`), `bun build --no-bundle` (`.ts .tsx .js .jsx .mjs .cjs`),
`JSON.parse` (`.json`). Syntax only, by design: no type/lint pass, so no project config
and no false positives from missing deps. A missing checker is silent. Cost: one
subprocess per edit. Off switch: `SYNTAX_GUARD_DISABLED=1`.

## Other extensions

- `harness-profile/` - shared helper, not a feature: loads the active engine profile from `<agent dir>/harness-profile.json` (legacy halogen defaults if absent) and registers nothing; imported by the ling-tiny-*, harness-tune, auto-guard and npu-retrieval extensions.
- `harness-status/` - `/usage` runs `scripts/usage-stats.py` inside pi; a footer status shows halogen load from `:8731/metrics` (hidden when unreachable).
- `harness-update.ts` - once-a-day notice when the harness repo is behind origin; `/harness-update` applies it (fast-forward only, refuses on a dirty tree, re-runs setup.sh).
- `tool-trim/` - keeps big tool output out of context: cleans and caps long bash output (full text saved to a file) and turns identical re-reads of unchanged files into a pointer. Off switch: `TOOL_TRIM_DISABLED=1`.
- `turn-timer.ts` - per-turn elapsed wall time in the footer.
- `work-guard.ts` - enforces AGENTS.md rules: blocks source edits via bash, blocks UI edits until the ui-ux-work skill is read, warns on committing UI changes with no render viewed. Off switch: `WORK_GUARD_DISABLED=1`.
- `subagent/` - `subagent` tool: delegates tasks to isolated `pi` subprocesses (single, parallel, chain); see [subagent/README.md](subagent/README.md).
