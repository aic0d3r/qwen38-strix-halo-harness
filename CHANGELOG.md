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
