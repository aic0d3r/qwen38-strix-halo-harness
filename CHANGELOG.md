# Changelog

## 1.6.1 - 2026-10-16

### Fixed
- `setup.sh`: unknown options (including `--help`) were silently ignored and ran a full install. `-h`/`--help` now prints usage and exits; anything unknown exits 2. `--halogen-upgrade <tag>` no longer leaves the tag behind as a stray argument.
- `scripts/uninstall.sh` missed five installed extensions (`harness-profile/`, `harness-status/`, `harness-update.ts`, `tool-trim/`, `work-guard.ts`); it now removes whatever setup copies from `extensions/`, plus the wizard's `harness-profile.json` / `harness-tuned.json`.
- `docs/LLAMACPP.md`: `cp extensions/*.ts` skipped `harness-profile/`, which the ling-tiny extensions import; the 27B reserve advice (~167144) contradicted the launcher's own half-window cap and now says 60000.
- `/gauntlet` and `/speedtest` prompts still named the removed `codebase_search` tool and the dropped reranker; now semble and the decider.

### Changed
- Proofread all docs: the NPU runs three small models by default (not four), README lists all three pi packages setup installs, and `extensions/README.md` now covers every extension.

## 1.6.0 - 2026-10-16

### Added
- Guided setup for any engine: `./setup.sh` (no flags, in a terminal) or `./harness setup` detects hardware and running engines (halogen, llama.cpp, Strata, vLLM, Ollama, LM Studio), asks only when ambiguous, writes the pi provider with per-model defaults from `config/profiles.json`, and probes prefill/decode to size `maxTokens` and the compaction reserve. Then `./harness use <engine>[/<model>]`, `./harness status`, `./harness tune`. Servers you started are never restarted. Measured on Strata (Qwen3.8-Flash-Next IQ4_XS): 1137 t/s prefill, 47.2 t/s decode, 262k ctx detected.
- `extensions/harness-profile/`: the active engine profile. The ling-tiny extensions, `harness-tune`, `auto-guard` and `npu-retrieval` read provider, model, sidecar and NPU URLs from it instead of hardcoding halogen, so sidecar compaction, branch summary, repo map and the commit guard now work on every local engine. No profile file = previous behaviour.
- `tests/test_harness.py`: wizard self-test against a mock OpenAI server.
- `setup.sh --gufo [url]`: pi-only install plus a `gufo` provider (default `http://127.0.0.1:8080`, what `server/start-gufo-flashnext.sh` serves), same flow as `--strata`. `--doctor` now checks either provider.
- `scripts/halogen-top.sh`: aggregate prefill and decode speed (since server start and since this dashboard started) in the THROUGHPUT panel, plus in the piped one-line mode; prefill trend sparkline.

### Fixed
- `/commit` in a repo with no commits reported "Nothing to commit" (diffed against a missing HEAD).
- `extensions/work-guard.ts`: scratch work was blocked when the session's cwd was the project and the command moved into the temp dir itself (`cd /tmp; python3 - <<'PY' … open('p.py','w')`). The temp-dir exemption now also looks inside the command. The rule is exported as `blocksBashEdit` and covered by `tests/work-guard.test.ts`.

### Removed
- `work-guard`: the serena-first rule. Native `edit`/`write` on source files no longer wait for `mcp__serena__activate_project` (it blocked config/shell/one-line edits and cost a retry). The bash-edit block stays.

### Changed
- `scripts/halogen-top.sh` layout: one label column for every panel, padded numbers with thousands separators, and a fully prompt-cached request shows `cached` instead of a misleading `0.0 t/s`.
- `extensions/ling-tiny-commit.ts`: tighter commit-message prompt (verb must match the change, no vague nouns or filler, `extra` instructions drive the subject) and `tidyMessage` now cuts a too-long subject at a clause boundary instead of mid-phrase.

## 1.5.6 - 2026-10-15

### Fixed
- `work-guard`: the no-render warning fired even when a screenshot had been viewed. It now counts any image returned by any tool (codemode `image()`, MCP screenshot tools, reads under `~/.pi`) as a render, and warns at most once per UI-edit burst, so refinement loops after a viewed render stay quiet.

## 1.5.5 - 2026-10-15

### Changed
- `work-guard`: fewer false positives on bash. Scratch work under `$TMPDIR` is no longer inspected, and text inside heredoc bodies or sed patterns no longer counts as an edit (the python rule only fires when python is actually being run).

## 1.5.4 - 2026-10-15

### Changed
- `harness-status` footer: adds prefill speed (since-start average, e.g. `pf 1.4k/s` — halogen only updates counters at request end, so a poll delta would be meaningless) and drops `0 run` when idle. Example: `halogen kv 59% · 1 run · pf 1.4k/s · 71 t/s`.

## 1.5.3 - 2026-10-15

### Changed
- `harness-status`: the halogen footer and its /metrics polling run only while a halogen model is selected (follows `model_select`); other providers get no footer.

## 1.5.2 - 2026-10-15

### Changed
- `usage-stats.py` counts directory extensions, shows tool-trim token savings, and honours HARNESS_EVENTS. `/usage` runs are logged; the serena guard message says a failed activation still lifts the block.

## 1.5.1 - 2026-10-15

### Changed
- `setup.sh`: serena now starts with `--project-from-cwd` (new installs, and added to an existing mcp.json), so the project is active at startup. It also records the repo path in `~/.pi/agent/harness-repo` so `/usage` works from any install location.
- `work-guard`: source edits inside the project are blocked until `mcp__serena__activate_project` has been called (only when serena is configured and its activate tool is exposed to the session).

## 1.5 - 2026-10-15

### Added
- `extensions/work-guard.ts`: blocks source edits through bash (sed -i, python writes, redirects), requires the ui-ux-work skill to be read before the first UI edit, and warns (no block) when committing UI changes with no screenshot viewed.
- `extensions/tool-trim/`: strips ANSI/progress frames and collapses repeats in bash output; very long output keeps first 60 + last 140 lines with the full original saved to /tmp/pi-out; identical re-reads of unchanged files return a pointer (reset on compaction).
- `extensions/harness-status/`: `/usage` command and a footer halogen load status.
- `scripts/usage-stats.py`: MCP / skill / extension / built-in usage per session or lifetime, never-used lists, 14-day trend, `--json`.
- `ui-ux-guard` and `work-guard` log to harness-events.jsonl.
- `scripts/halogen-top.sh --strata [url]`: the dashboard now reads Strata (default
  `:8080`). Its vLLM-style `/metrics?format=prometheus` is mapped onto the same counters;
  the halogen-only prompt-cache panel is hidden. Prefill t/s counts only tokens Strata
  actually read (its prompt counter includes cache-reused tokens).

### Docs
- Engine-by-hardware table in the README, and Strata measured with the same battery and depth
  prompts as halogen vs gufo (decode 43 t/s battery median, 32-36 t/s at 2k-119k, cold prefill
  ~990 t/s): halogen stays first on Strix Halo, Strata/gufo are the open options.

## 1.4 - 2026-10-14

### Added
- `extensions/ui-ux-guard.ts`: the impeccable UI detector runs mechanically after every
  edit/write on a UI-relevant file, appending new findings to the tool result. In pi the
  detector previously ran only if the agent remembered to (impeccable ships hook manifests
  for Claude Code / Codex / Cursor / Copilot / Gemini — not pi).
- Subagent agents take an opt-in `skills:` frontmatter list (name, path, or comma-separated).
  The SKILL.md body is inlined into the subagent's system prompt and passed via `--skill`,
  so the body is read without the discovery scan — subagents still run `--no-skills`.
  A missing skill is skipped, not fatal.
- The commit tool accepts a `cwd`, for commits in interlinked sibling repos.
- `setup.sh` provisions the pi packages the harness assumes (`ponytail`,
  `@juicesharp/rpiv-ask-user-question`) instead of relying on a hand-installed agent dir.

### Changed
- `qwen3-reranker-0.6b` is out of the default `HALOGEN_NPU_MODELS`: nothing has called
  `/v1/rerank` since v1.1 moved search to semble. 821 MB back at the next engine restart;
  override `HALOGEN_NPU_MODELS` to re-add it.
- `auto-guard` records `why` a screen failed in `harness-events.jsonl`. The 33 reason-less
  fast-fails (13-20 ms) were `Unable to connect` — the engine was down, not the guard.

### Docs
- NPU numbers re-measured on halogen 0.18.1: `triage` 55 ms warm / ~1.6 s cold (README said
  ~120 ms), guard 207 ms p50 / 285 p95, canary re-verified 42 % recall / 0 % fp.

## 1.3 - 2026-10-12

### Added
- `scripts/session-audit.sh --harness`: what the sidecar and NPU models did in one session:
  calls, average latency, tokens taken off the main model, guard flags, triage confidence,
  and failures (which fell back to the main model). The extensions now append one line
  per call to `~/.pi/agent/harness-events.jsonl`, which replaces `npu-retrieval-usage.log`.
- Commit tool `message` parameter: commits an exact, dictated message without the sidecar
  (agents used to undo commits and retry "USE VERBATIM" until the sidecar copied it).

### Changed
- Commit messages for large diffs are 2.5x faster (7.2 s -> 2.9 s on a 30-commit diff). Almost
  all the time was the sidecar reading the diff (~3,500 tok/s), so the diff it reads is now
  smaller: the file summary comes first (cutting never hides a file), 1 line of context
  instead of 3, and a 24k-character limit instead of 60k (18.5k -> 7.1k tokens). The git
  commands run in parallel and the message is capped at 400 tokens. Small commits stay
  under 1 s.

### Fixed
- The commit tool starts `ling-tiny.service` when the sidecar is down (it stays down after a
  reboot, since nothing starts at boot); a stopped sidecar to a finished commit took 1.4 s. It
  used to fail with "Unable to connect" until someone started the sidecar.
- The direct `git commit` block ignores text in quotes and heredocs, so commands that only
  mention "git commit" (a grep, a prompt string) are no longer blocked.
- A commit message that comes back as one long line is split into a subject of at most 72
  characters and a body. `bench/check-fixes.sh` check [7] tests both against the real code.

## 1.2.1 - 2026-10-12

### Fixed
- Repo audit, 14 fixes:
  - `setup.sh` no longer overwrites `models.json` / `settings.json` when it cannot parse them
    (it used to replace a file with a typo by an empty config, on every `./start.sh`).
  - The launcher resets a `reserveTokens` that no longer fits the window: a `/tune` value from a
    262k day (e.g. 60144 after `/tune reset`) compacted every turn on a 65k fallback day.
  - `VISION=0 ./start-halogen.sh` works without the vision file, as its own error message says.
  - `bench/harness-ab.sh` refuses a dirty repo, stops on a bad path instead of running
    `git clean` in the wrong directory, restores extensions and the old reserve on Ctrl-C, and
    kills only its own pi run.
  - `./start-halogen.sh` is now a link to `server/start-halogen.sh`, so it follows the pin after
    `upgrade-halogen.sh`; the image is pinned by digest, and the upgrade script writes it.
  - `progress-tracker` checkpoints this session's own last reply (from the session manager), not
    the newest transcript in the folder, and a long "Continue ..." prompt keeps the resume note.
  - `PI_CODING_AGENT_DIR` is honoured by the launcher, `/tune`, the ubatch file and the logs.
  - Sidecar download says "NOT verified" when no checksum could be fetched.
  - Chained compaction keeps the capture list together (the previous summary moved below it);
    the retention canary reads the prompt from the extension and runs thinking-off by default.
  - `GUFO_CTX` sets the served context; the commit guard no longer blocks `git log -- commit.ts`;
    `--doctor`'s model ping has a 120 s timeout. `bench/check-fixes.sh` checks [10] and [11] are new.
- Two leftovers from the retired NPU search. `bench/harness-ab.sh` still asked pi for the
  `codebase_search` tool (deleted in v1.1) and its usage line still said `<repo-with-.rag>`; it
  now runs `mcp__semble__search`. And the upgrade acceptance battery timed a `/v1/rerank` call to
  the idle reranker, so trimming `HALOGEN_NPU_MODELS` the way the README suggests would have
  failed the battery and rolled back every upgrade. `bench/check-fixes.sh` check [9] covers both.

## 1.2 - 2026-10-12

### Docs
- README is now a landing page in the house format (badges, dot-separated nav,
  `> [!TIP]`/`[!NOTE]`/`[!WARNING]` callouts, em-dash bullets, a chart table in place of
  screenshots): intro, What you get, The stack (+ how the pieces hand off), Evidence, Install,
  Model files, Quick Start, The NPU, Crash recovery, Upgrading, Uninstall, Documentation, Posts,
  License. The depth moved to docs pages instead of living in the README: [docs/ENGINES.md](docs/ENGINES.md)
  (engine comparison, sidecar isolation, cloud hybrid), [docs/LLAMACPP.md](docs/LLAMACPP.md)
  (the full-control path) and [docs/OPS.md](docs/OPS.md) (context/fragmentation, speed surfaces,
  troubleshooting).
- Title no longer claims "zero cloud" — switching to a cloud model mid-session is a supported
  shape, so the intro states the local-by-default + cloud-optional split and links the measured
  hybrid. Badge fix: the version badge pointed at `github/v/release` while the repo had tags but
  no GitHub *Release*, so it rendered "no releases or repo not found"; a v1.2 release is now
  published and the badge resolves, alongside `hardware: Strix Halo` + `cloud: optional` in place
  of the misleading `network: zero` badge.
- Stale NPU claims fixed: the decider tool is `triage` (it was never called `decide`), codebase
  search no longer uses the NPU (semble does, on CPU), the reranker is idle since v1.1 and can be
  dropped from `HALOGEN_NPU_MODELS`, NPU dirs are lowercase `npu/`, and the GPU+NPU fabric-clock
  requirement, the `/health` NPU probe and a triage/dedup troubleshooting row are now written down.
- `docs/BENCHMARKS.md`: labels the retrieval eval as superseded by semble, marks the rerank
  number idle, notes the pin moved to 0.18.0, and states that multi-option `triage` accuracy is
  unmeasured for the constrained-enum implementation (the 40 % figure was the dropped variant).
- `extensions/README.md`: install points at this repo (it said `neon-ladder`, the benchmark
  repo), the removed `--index` hint is gone, and the NPU models are listed with the tool each one
  serves.
- `docs/BENCHMARKS.md` gained the eighth local-vs-cloud run (Flash-Next at max: 8m31s,
  source-level fix, PR refound) and the wall-clock finding — a matched-harness tie, with the
  fast-but-shallow runs called out — plus a per-run chart.

### Added
- Speed visibility: `scripts/halogen-top.sh` (live prefill/decode dashboard from `/metrics`
  deltas, with a history CSV) and `scripts/session-audit.sh --perf`, which reports
  prefill/decode throughput for one session's window from the server's `serve_api:` log lines
  plus a `/metrics` diff. Parser self-test: `scripts/halogen-perf.py --selftest`.
- Halogen image pin moved to **0.18.0**, and `--halogen-upgrade latest` resolves the tag up front
  and exits early when already pinned. An upgrade refreshes the NPU model files before the restart
  (0.18 added the embedding `u10.elf`), keeps one rollback copy, and prints how to restore it.

### Fixed
- `scripts/session-audit.sh` passed its session-start time to `docker logs --since` as a naive
  local string, so docker read the window as UTC and the audit grep'd a nearly empty tail (and
  reported "clean" for it). Window now passed as UTC ISO. The cache-reuse filter also never
  matched `cached, 98.9%` - it required `%` directly after two digits - so every large prefill
  was flagged as cache-broken.
- `thinkingLevelMap` sends the model's real effort tier for pi's `high`: `xhigh`
  instead of the `high` alias. Behavior is unchanged on halogen (serve_api folds
  both spellings to xhigh) but the config no longer contradicts the chat template,
  which supports exactly low/medium/xhigh. The llama.cpp-path example carries the
  same map, and there no wrapper folds the alias, so the real tier name is the
  safe form
- Post-release audit findings (each covered by `bench/check-fixes.sh`): `dedup_scan` returned a
  bare string where pi expects a content block, so the tool result came back empty and pi exited;
  `auto-guard` could no-op instead of failing open; `setup.sh` continued after an aborted model
  download, mis-parsed a trailing `--halogen` argument and never checked the `vm-compact` timer;
  `--uninstall` left the wrong systemd units behind; `/tune reset` did not restore the default; a
  failed `--halogen-upgrade` did not roll its pin back; the sidecar message path reported success
  on a failed run; prompt templates installed to the wrong path; and `--doctor` reported halogen
  as down on a `--no-halogen` install. `extensions/optional/` and `scripts/hw-telemetry.sh` were
  removed as measured no-ops.
- The sidecar message tool staged the crash-recovery checkpoint and `.progress-log/` into every
  commit; pathspecs now exclude those paths only when they are not already gitignored (naming an
  ignored path makes `git add` exit 1).

## 1.1 - 2026-10-10

Post-release audit round: two full review passes plus self-review of the fixes, plus a
measured slimming pass (search moved to the semble MCP server, dead weight dropped).
The audit fixes are in `bench/check-fixes.sh`-covered territory - install/uninstall
correctness, silent sidecar fallbacks, crash-safe index writes, /tune consistency.

### Added
- `./setup.sh --no-halogen`: pi-only install - skips the halogen provider, the NPU
  extensions and all container management (pi on a laptop against your own providers,
  or against a remote Strix Halo appliance via baseUrl + `PI_NPU_BASE`)
- doctor: GPU fabric-clock probe. On Strix Halo, NPU work beside GPU work is only safe
  with the fabric clock held; the probe warns with the one-time host-unit install
  command when it isn't. Unit staged at `config/systemd/halogen-fabric-clock{,.service}`
- `start-halogen.sh`: `HALOGEN_NPU_MODELS` is env-overridable (load custom NPU
  fine-tunes without editing the script)
- `bench/guard-canary.py --model`: run the injection canary against any
  moderation-capable model served by halogen
- `./setup.sh --voice`: voice for pi sessions via the privateer-speak package wired to
  a local Lemonade server - whisper STT into the composer (`/talk`, alt+t) and kokoro
  TTS answers (`/speak`). Measured STT 0.43s / TTS 0.3s, both on CPU; the FLM NPU STT
  path benched slower (1.91s) and stays documented in the receipts. Nothing starts at boot.

### Changed
- semantic codebase search moved from the NPU rag pipeline to the semble MCP server
  (`mcp.json`, `search` + `find_related` direct, pinned 0.6.2 via uvx). A/B on 18
  ground-truth queries across this repo and an unfamiliar one: semble 17/18 (94%)
  top-3 hit-rate vs NPU 13/18 (72%), 2-3x faster queries (0.55-0.61s vs 1.0-1.7s),
  ~40x faster index builds (1.2s vs ~52s). `npu-retrieval.ts` keeps `triage` and
  `dedup_scan` (-178 lines of index machinery); `codebase_search`, `/rag-index`,
  auto-index, `rag-index.py`, `rag-query.py` and `setup.sh --index` are gone. Search
  now works on --no-halogen and llama.cpp-path installs (no NPU involved); setup
  warns if `uvx` is missing; scout/reviewer agents declare `mcp__semble__search`
- subagents spawn with `--no-skills`: every scout/worker/reviewer was paying ~1.4s of
  skill scanning + description prefill per dispatch for skills a subagent never routes
  to (0 loads across all 225 recorded sessions). Skills stay a main-session feature;
  `/skill:name` there forces a load when a task needs one
- the four vendored design skills (frontend-design, token-optimizer, ui-ux-pro-max,
  web-design-guidelines) are removed from the repo and from installs - zero loads
  across every recorded session, 9.3 MB of repo and ~1.4s of every session startup
  for nothing. Project-scoped skill directories remain the way to add skills where
  they are actually used. `uninstall.sh` cleans them from pre-1.1 installs
- measured on this box (50W profile): sustained single-stream decode is ~43 tok/s
  (power pins at 52W, no thermal decay); 2 concurrent streams 71 tok/s aggregate,
  4 streams 80; a 4-way subagent burst does NOT evict the resident main session's
  prompt-cache entry (24-entry LRU, restore-on-hit), so `MAX_CONCURRENCY=4` is safe;
  MTP draft acceptance 90-100% across counting/code/reasoning workloads

### Fixed
- `setup.sh --uninstall` / `--halogen-upgrade` ran the full installer instead (handlers
  were gated behind `--doctor`); they now work standalone as documented
- `uninstall.sh` backup: a second `tar czf` truncated the first archive - one tar call
  with re-anchored `-C` switches now backs up extensions, settings, providers AND the
  systemd units together; `rag-index.py` is backed up and removed like the rest
- progress-tracker: checkpoints recorded `(no changes)` for sessions that only create
  new files (`git diff` misses untracked) - `git status --short` is now included;
  removed v5.1 debug logging to /tmp; history filenames carry the pid
- compaction: a session past the sidecar's window silently fell back to slow main-model
  summarization; the conversation is now capped head+tail (420k chars) so big sessions
  keep the fast path
- branch-summary: same oversized-payload fallback as compaction - same head+tail cap
- auto-guard: duplicate `signal` object key let a stale 5s timeout silently override
  the documented 15s ceiling - single timeout now
- commits always go through tiny: a `tool_call` guard in ling-tiny-commit blocks direct
  `git commit` from tool calls (`--amend --no-edit` / `-C HEAD` allowed - no new message);
  the commit tool's own git call uses pi.exec and is unaffected
- rag: `--index` and auto-index now pass `--incremental` (the README always promised it);
  index writes are tmp+rename with an exact byte count in the meta, so a crash mid-write
  fails actionably instead of pairing meta against shifted vectors; `codebase_search`
  appends an "N indexed files changed" staleness note; `buildIndex` records the file-mtime
  map, keeping TS-built indexes consistent with the python tool
- sidecar batch size: the systemd unit and first-boot spawn omitted `-b/-ub`, running
  llama.cpp's 512 default - 8x below the measured 4096; both now read the `/tune`
  value (default 4096) like `start-ling-tiny.sh` does
- `/tune maxTokens` accepted values past the documented halogen hard reject (8192);
  the bound now matches the documentation
- `/tune compactAt` survived no server restart: the launcher rewrote `reserveTokens` on
  every launch and computed from a hardcoded 262k window. The launcher now only replaces
  its own default values, and `harness-tune` reads the live `contextWindow` (65k-fallback
  days included), with bounds that keep the reserve smaller than the context
- ling-tiny-repomap: 90s timeout on the sidecar call (a wedged server can no longer hang
  turn 1 of every session in a directory); the map cache moved to
  `~/.pi/repomaps/<cwd>` instead of writing `.pi/` into every mapped repo
- setup: first-run halogen launch works from any cwd (image-pin grep was cwd-relative);
  `rag-index.py` installs to `~/.pi/agent/` and npu-retrieval finds it there - removed
  machine-specific fallback paths; removed a duplicated fabric-clock check
- `bench/check-sidecar.sh` health-probes `:8090/health` instead of trusting
  `systemctl is-active` (which lies for a wedged-but-running process), restarts on
  failure and exits non-zero if still unhealthy
- `bench/harness-ab.sh`: the baseline arm now also parks `subagent/` (it only moved
  `*.ts` files, so the "baseline" still ran the subagent extension)
- upgrade battery: guard smoke added - a benign prompt must not flag; the attack verdict
  prints but cannot gate the upgrade (42% recall is too weak for that)
- `scripts/hw-telemetry.sh` creates its log directory before appending
- `extensions/optional/` is no longer copied into the extensions directory (it is a
  reference archive; pi's loader never reads it)
- repo: dropped 7.4 MB of demo binaries from the vendored token-optimizer skill

### Tests
- `bench/check-fixes.sh`: nine offline regression checks covering every fix above
  (tar member list, uninstall routing, guard timeout, both sidecar caps, staleness
  calc, byte guard, commit guard, ubatch/reserve/maxTokens guards); run after any
  change to the touched files

## 1.0.0 - 2026-10-07

Initial public release. A coding-agent harness for Qwen3.8-Flash-Next on AMD Strix Halo
(Ryzen AI Max+ 395 / gfx1151), built and validated against halogen-flash-server 0.17.1
(NPU + iGPU inference) with a ling3.0-tiny llama.cpp sidecar.

### Added
- **`setup.sh`** - one-command install: provisions pi extensions, prompt templates, skills,
  providers and MCP config (never overwrites your own entries), sha256-verifies the sidecar
  GGUF, installs a keep-alive systemd unit, pre-flights allocator fragmentation before
  committing to 262k context. `--doctor` verifies the whole stack; `--halogen-upgrade [tag]`
  pulls, restarts, runs the acceptance battery and only moves the version pin if everything
  passes (auto-rollback otherwise). `--index <dir>` builds an NPU search index.
- **`start.sh`** - daily driver: heals dead servers, opens the TUI.
- **10 pi extensions:**
  - `progress-tracker` - crash recovery: kill the agent mid-task, the next session picks up
    the exact task and state (310+ checkpoints, validated at the model level)
  - `npu-retrieval` - `codebase_search` (semantic, 15/20 vs 9/20 for grep on intent queries),
    `dedup_scan` (45 duplicate pairs in 8.4s), `decide` (78% binary decisions, ~120ms)
  - `ling-tiny-compaction` - sidecar-summarized compaction, thinking off, 10/10 rule
    retention on the canary set (~80-95s faster than main-model fallback)
  - `ling-tiny-commit` - sidecar commit messages, diffs never enter the main context
  - `ling-tiny-repomap` - repo map on session start, freshness-cached
  - `ling-tiny-branch-summary` - sidecar branch summaries
  - `auto-guard` - NPU injection screening, fail-open tripwire (42% recall / 0% false
    positives on a 30-prompt canary; not a security boundary)
  - `harness-tune` - `/tune` live config knobs (compaction threshold, output budget,
    temperature, ubatch)
  - `turn-timer` - per-turn timer in the status bar
  - `subagent/` - worker/scout/reviewer/planner fleet inheriting the session model
- **`templates/`** - `/gauntlet` (multi-phase stress session) and `/speedtest` (engine
  benchmark battery with pass/fail bands)
- **`bench/`** - `harness-ab.sh` A/B runner, `check-sidecar.sh`, `guard-canary.py`
  (precision/recall set), `compaction-retention.py` (rule-survival canary)
- **ops**: `scripts/session-audit.sh` (post-session forensics), `scripts/hw-telemetry.sh`
  + systemd units (allocator fragmentation tracking), pinned container image with
  acceptance-battery upgrades
- **vision**: image input enabled end-to-end (0.17.1 vision tower), verified by rendered-
  page inspection

### Validation highlights (halogen 0.17.1, this box)
- decode: 64 tok/s MTP / 38 serial; prefill ~1,300 tok/s (cache-cold, 174k prompt)
- 2 concurrent streams: 72.7 tok/s aggregate (joint speculative decoding)
- NPU calls: 70-130ms warm
- 2-hour live session: 119 turns, 97% prompt-cache hit, 5 commits, 3 real product bugs found
  by the harness's own vision verification
