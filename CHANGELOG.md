# Changelog

## Unreleased

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

### Fixed
- re-audit round: branch-summary got the same sidecar payload cap as compaction (long
  abandoned branches silently fell back to slow main-model summarization); the systemd
  unit and first-boot sidecar spawn now carry `-b/-ub` from /tune (default 4096, was
  llama.cpp's 512 - 8x below the measured setting); /tune maxTokens bound raised cap
  to the documented 8192; start-halogen no longer reverts a /tune'd reserveTokens on
  every launch (only overwrites its own defaults) and harness-tune computes compactAt
  from the actual contextWindow (65k-fallback days included); repomap: 90s timeout on
  the tiny call (wedged sidecar can no longer hang turn 1) and its cache moved to
  ~/.pi/repomaps/<cwd> instead of dirtying every repo; harness-ab baseline now also
  parks subagent/; check-sidecar health-probes :8090 instead of trusting systemctl
  is-active; hw-telemetry creates its log dir
- commits always go through tiny: a `tool_call` guard in ling-tiny-commit blocks direct
  `git commit` from tool calls (`--amend --no-edit`/`-C HEAD` allowed - no new message);
  the commit tool's own git call uses pi.exec and is unaffected
- `bench/check-fixes.sh`: offline regression checks for every fix below (tar-member list,
  uninstall routing, guard timeout, compaction cap budget, staleness calc, bytes guard)
- `setup.sh --uninstall` / `--halogen-upgrade` ran the full installer instead (handlers were
  gated behind `--doctor`); they now work standalone as documented
- `uninstall.sh` backup: the second `tar czf` truncated the first archive - one tar call
  with multiple `-C` now backs up extensions AND the systemd unit together
- progress-tracker: checkpoints recorded `(no changes)` for sessions that only create new
  files (`git diff` misses untracked) - `git status --short` now included; removed v5.1
  debug logging to /tmp; history filenames carry the pid (parallel-session collisions)
- compaction: an oversized session could no longer use the sidecar (whole conversation
  serialized against tiny's 131k window -> silent fallback to slow main-model compaction);
  the conversation is now capped head+tail so big sessions keep the fast path
- auto-guard: duplicate `signal` object key meant the documented 15s timeout was silently
  overridden by 5s - single timeout now
- rag: `--index` and auto-index now pass `--incremental` (README always promised it);
  index writes are tmp+rename (no torn index on crash); `codebase_search` appends a
  "N indexed files changed" staleness note; corrupt vectors.f32 now errors actionably;
  `buildIndex` records the file-mtime map, keeping TS-built indexes consistent
- setup: first-run halogen launch works from any cwd (image-pin grep was cwd-relative);
  `rag-index.py` installs to `~/.pi/agent/` and npu-retrieval finds it there - removed
  machine-specific fallback paths; removed the duplicated fabric-clock block
- upgrade battery: guard smoke added (benign prompt must not flag; attack verdict prints
  but cannot gate the upgrade - 42% recall is too weak for that)
- repo: dropped 7.4 MB demo binaries from the vendored token-optimizer skill; setup no
  longer copies `extensions/optional/` (reference archive, never loaded by pi's loader)

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
