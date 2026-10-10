# Changelog

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
  - `NPU-retrieval` - `codebase_search` (semantic, 15/20 vs 9/20 for grep on intent queries),
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
