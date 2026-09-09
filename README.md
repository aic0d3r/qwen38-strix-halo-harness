# Qwen3.8 coding agents on Strix Halo, llama.cpp (Vulkan) + pi

One board, no cloud: **Qwen3.8-Flash-Next 125B-A6B** as the daily driver (40 t/s sustained agent decode at the high-acceptance end, 22-45 t/s content-driven envelope, 353 t/s prefill), **Qwen3.8-27B** when you need 256k context or only have ~91GB free, a **Ling-3.0-tiny** aux model for summaries, and the pi extensions that keep 90-minute sessions alive. Every script here is what I actually run. Most of the comments are scars.

- **`server/`**: launch scripts: `start-flashnext.sh` (MTP sidecar, the reasoning flags that stop it burning its whole output on thinking), `start-qwen38.sh` (27B, DFlash2, 256k ctx), `start-ling-tiny.sh` (aux). Ubatch ceiling documented in headers.
- **`config/`**: `models.json.example` (both models pre-wired, thinking flags included) and the compaction snippet (`reserveTokens` per window: the number is per-model, don't copy it blindly).
- **`extensions/`**: six pi extensions: auto-compaction, branch-summary, commit, triage, repomap, and `/tune` (adjust every knob live, mid-session).
- **`bench/`**: the frozen v1/v2 game-build protocol. The current 19-check scorer and full result ledger live in **[neon-ladder](https://github.com/aic0d3r/neon-ladder)**, use that for new numbers.

Measured on a Flow Z13 (Ryzen AI MAX+ 395, Radeon 8060S, 128GB) at 70-80W. This repo is the runnable part; methodology and every receipt live in the [posts](#posts).

## Setup

1. **Engine**: [Nathanw1014/strix-halo-llamacpp](https://github.com/Nathanw1014/strix-halo-llamacpp) v0.6.5 minimum, **v0.7.4.1 recommended** and bench-validated on both models (v0.7.4 fixed stale-KV and a top-k race that were in upstream too; throughput at parity with v0.7.3). The Vulkan work is migrating to [halo-box/strix-llama.cpp](https://github.com/halo-box/strix-llama.cpp), either tree works. Build from source if you can: the portable payload leaves 6-24% decode on the table.
2. **Models**:
   - 27B target: [unsloth/Qwen3.8-27B-GGUF](https://huggingface.co/unsloth/Qwen3.8-27B-GGUF) UD-Q4_K_XL (Dynamic v3, the pick; the Q4-Q8 PPL plateau is flat and build output is identical across the tier). For exact reproduction of the published era numbers: UD-Q5_K_XL @ revision `408fcc1807ab` (v2), set `TARGET_GGUF`.
   - 27B vision: `mmproj-F16.gguf` (same repo), passed as `-mm ... --mmproj-offload` so pi sessions always have vision on GPU. A/B (2026-09-07): projector GPU vs CPU placement is byte-identical on decode (25.8 vs 26.5 t/s, acc 0.475 both). Vision is free.
   - pi image input: the model entry in `models.json` must declare `"input": ["text", "image"]` or pi refuses to send images ("model does not support image input"). 27B entry had it missing; both entries now carry it.
   - 27B drafter: [incoai/Qwen3.8-27B-DFlash2-GGUF](https://huggingface.co/incoai/Qwen3.8-27B-DFlash2-GGUF) Q4_K_M
   - Flash-Next: [unsloth/Qwen3.8-Flash-Next-GGUF](https://huggingface.co/unsloth/Qwen3.8-Flash-Next-GGUF) UD-IQ4_XS (3 shards) + `mmproj-F16.gguf` (same repo, same `-mm` treatment)
   - Flash-Next drafter: [EasiiX/Qwen3.8-Flash-Next-MTP-Strix-Halo-GGUF](https://huggingface.co/EasiiX/Qwen3.8-Flash-Next-MTP-Strix-Halo-GGUF) Q8_0 MTP sidecar
   - Aux: [inclusionAI/Ling-3.0-tiny](https://huggingface.co/inclusionAI/Ling-3.0-tiny) Q4_K_M
   - Template (27B): vendored in `config/template/`: `sharp-v22.3.2.jinja` (current), `sharp-v22.1.jinja` (what the published numbers were measured on). Upstream: [peculiar-ragdoll/Qwen-Sharp-Chat-Templates](https://huggingface.co/peculiar-ragdoll/Qwen-Sharp-Chat-Templates)
3. **Servers**: one big model at a time on this APU; a second 27B-class server means device-lost. Both big-model scripts pass `-mm $MODEL_DIR/$MMPROJ_GGUF --mmproj-offload` (vision on GPU, default offload keeps it in VRAM):
   ```bash
   ENGINE_DIR=... MODEL_DIR=... server/start-flashnext.sh   # daily driver
   # or:
   ENGINE_DIR=... MODEL_DIR=... server/start-qwen38.sh      # 256k / low-memory
   # second terminal (either way):
   ENGINE_DIR=... MODEL_DIR=... server/start-ling-tiny.sh
   ```
   **GPU memory**: the ~91GB model lives in the GTT heap, so size it: boot args `amdgpu.gttsize=126976 ttm.pages_limit=32505856 ttm.page_pool_size=32505856` (on systemd-boot put them on the single `LINUX_OPTIONS` line — a stray newline there makes the loader silently drop the whole line). Check with `cat /sys/class/drm/card*/device/mem_info_gtt_total`. Never force `--fit off`: full offload onto an undersized heap deadlocks the iGPU and hard-locks the host.
4. **pi** ([github.com/earendil-works/pi](https://github.com/earendil-works/pi)):
   ```bash
   cp config/models.json.example ~/.pi/agent/models.json   # adjust paths/ports
   cat config/settings-snippet.json                         # merge into settings.json
   cp extensions/*.ts ~/.pi/agent/extensions/
   ```
   Two numbers in the snippet you must understand, not copy: `reserveTokens` ships **10240** because that's right for Flash-Next's 65536 window; on the 27B's 262144 window use **~167144** instead. And `thinkingBudgets` (per-request budgets pi sends) are what actually govern pi sessions; the server's `--reasoning-budget` flag is only a default for clients that don't send one. Tiers validated on Flash-Next at low/medium/high.
5. **Verify you're actually local**: `curl localhost:8080/metrics` while pi runs; if `prompt_tokens_total` isn't climbing, pi fell back to a cloud provider.

## The bench (v1/v2 protocol, kept for comparability)

```bash
bench/run-game-bench-v2.sh /tmp/game-build test1 medium "$(cat bench/game-prompt-v2.txt)"
python3 bench/game-score.py /tmp/game-build
```

The v2 runner guards the run: server-health precheck, a taxonomy watchdog (kills the attempt if a file outside the fixed 10-file contract appears), early-exit once the contract is complete and syntax-clean, and an honest SUCCESS gate (exact file set + `node --check` clean) that ignores the agent's own report. **Agents fabricate completion reports**; the prime rule of this whole stack is that the scorer and your own eyes are the grade.

Reference (medium effort, scorer v1): 11-13/15 checks, ~1,100-1,500 lines, ~15 min per good roll (~1 in 4 rolls hits an instant-EOS basin and retries). Quality at temp 0 is a distribution, not a constant. Static score is not playability. Open the game and play it; the interesting failure modes (paddle-glue, ball tunneling, persistence, dead keymaps) are all runtime. For the current protocol (19 checks, N≥2-validated tiers, the result ledger), use [neon-ladder](https://github.com/aic0d3r/neon-ladder).

## Posts

- [Qwen3.8-27B on Strix Halo at 31 t/s decode, the full stack guide](https://www.reddit.com/r/LocalLLaMA/comments/1vsw6nz/) (r/LocalLLaMA)
- [Running a local coding agent on Strix Halo with pi + llama.cpp](https://www.reddit.com/r/StrixHalo/comments/1w6c5nz/) (r/StrixHalo)
- [Qwen3.8-Flash-Next on Strix Halo: 40 t/s sustained decode](https://www.reddit.com/r/StrixHalo/comments/1w6cf5t/) (r/StrixHalo)
- [Neon Ladder: a playtest-graded benchmark for local LLM stacks](https://www.reddit.com/r/LocalLLaMA/comments/1w6cjm5/) (r/LocalLLaMA)
- [neon-ladder](https://github.com/aic0d3r/neon-ladder), the benchmark this stack was validated on

## License

MIT
