# qwen38-strix-halo-harness

Run Qwen3.8-27B (daily driver, 256k ctx) and/or Qwen3.8-Flash-Next 125B-A6B (speed
lane, 30-40 t/s sustained agent decode) as local coding agents on Strix Halo (Ryzen AI
Max+ 395 / Radeon 8060S, 128GB), with a Ling-3.0-tiny aux model handling summaries and
a bench suite that scores agent-built code. Both models are pre-wired in
`config/models.json.example` — pick per session in pi. Companion to the benchmark and
harness posts (links in the footer).

Everything here was measured on a Flow Z13 at 70-80W; numbers and methodology live in the
posts, this repo is the runnable part.

## What's inside

- `server/` — launch scripts for the 27B (Q4_K_XL-v3 default, DFlash2 spec, 256k ctx),
  Flash-Next (MTP sidecar, mandatory reasoning flags), and the tiny aux model. Ubatch
  ceiling documented in the header comments.
- `config/` — `models.json.example` (pi provider wiring incl. thinking flags) and the
  compaction settings snippet.
- `extensions/` — six pi extensions: compaction, branch-summary, commit, triage, repomap
  (auto-fires per session in git repos), and `/tune` (adjust all knobs live).
- `bench/` — the frozen "Neon Overdrive" game-build prompt, the 15-check scorer, and a
  retry wrapper with perturbation semantics (engine nondeterminism is fixed in v0.7.4+,
  but perturbed retries stay in the protocol — cheap, and they defend against everything).

## Setup

1. Build the engine: [Nathanw1014/strix-halo-llamacpp](https://github.com/Nathanw1014/strix-halo-llamacpp)
   v0.6.5 minimum, v0.7.4.1 recommended and bench-validated (greedy repeatability
   fixes — stale-KV between requests and a top-k race above ~2k prompt tokens, both
   also in upstream master; throughput at parity with v0.7.3). Nathan's Vulkan work is migrating to
   [halo-box/strix-llama.cpp](https://github.com/halo-box/strix-llama.cpp);
   either tree works for the above.
2. Download models (see the benchmark post for the full story):
   - 27B target: `unsloth/Qwen3.8-27B-GGUF` UD-Q4_K_XL (Dynamic v3 — the daily driver;
     the Q4-Q8 PPL plateau is flat and build output is identical across the tier). For
     exact reproduction of the published era numbers use UD-Q5_K_XL @ revision
     `408fcc1807ab` (v2) and set `TARGET_GGUF` when launching.
   - 27B drafter: `incoai/Qwen3.8-27B-DFlash2-GGUF` Q4_K_M
   - Flash-Next (optional): UD-IQ4_XS GGUF + the EasiiX Q8_0 MTP sidecar
   - Aux: `inclusionAI/Ling-3.0-tiny` Q4_K_M
   - Template: vendored in `config/template/` — `sharp-v22.3.2.jinja` (current) and
     `sharp-v22.1.jinja` (what the published benchmark numbers were measured on). Upstream:
     [peculiar-ragdoll/Qwen-Sharp-Chat-Templates](https://huggingface.co/peculiar-ragdoll/Qwen-Sharp-Chat-Templates)
3. Start servers:
   ```bash
   ENGINE_DIR=... MODEL_DIR=... server/start-qwen38.sh     # 27B daily driver
   # or:
   ENGINE_DIR=... MODEL_DIR=... server/start-flashnext.sh  # Flash-Next speed lane
   # second terminal (either way):
   ENGINE_DIR=... MODEL_DIR=... server/start-ling-tiny.sh
   ```
   One big model at a time on this APU — a second 27B-class server means device-lost.
4. Install [pi](https://github.com/earendil-works/pi), then:
   ```bash
   cp config/models.json.example ~/.pi/agent/models.json   # adjust paths/ports
   cat config/settings-snippet.json                         # merge into ~/.pi/agent/settings.json
   # reserveTokens: snippet ships 10240 (Flash-Next 65536 window). On the 27B's
   # 262144 window use ~167144 instead. thinkingBudgets = per-request budgets pi
   # sends (these, not the server flag, govern pi sessions); tiers validated on
   # Flash-Next at low/medium/high.
   cp extensions/*.ts ~/.pi/agent/extensions/
   ```
5. Verify you're actually local: `curl localhost:8080/metrics` while pi runs — if
   `prompt_tokens_total` isn't climbing, pi fell back to a cloud provider.

## The bench

```bash
bench/run-game-bench-v2.sh /tmp/game-build test1 medium "$(cat bench/game-prompt-v2.txt)"
python3 bench/game-score.py /tmp/game-build
```

The v2 runner guards the run: server-health precheck, a taxonomy watchdog (kills the
attempt if a file outside the fixed 10-file contract appears), early-exit once the
contract is complete and syntax-clean (ends unbounded polish loops), and an honest
SUCCESS gate (exact file set + `node --check` clean) that ignores the agent's own
report - agents fabricate completion reports.

Reference (medium effort, scorer v1): 11-13/15 checks, ~1,100-1,500 lines, ~15 min per
good roll on this hardware (~1 in 4 rolls hits an instant-EOS basin and retries).
Quality at temp 0 is a distribution, not a constant. Score is static checks only; open
the game and play it - the interesting failure modes (paddle-glue, ball tunneling
through bricks, persistence) are runtime. `bench/game-prompt.txt` and
`bench/run-with-retry.sh` keep the original v1 protocol for comparison.

## License

MIT
