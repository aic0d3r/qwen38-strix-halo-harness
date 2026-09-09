# Changelog

## 2026-09-07

- Vision on GPU: both server scripts pass `-mm $MODEL_DIR/$MMPROJ_GGUF --mmproj-offload` (mmproj-F16.gguf ships in each model repo; default on, set `MMPROJ_GGUF=` to disable).
- A/B (projector GPU vs CPU, same binary): byte-identical on decode, 25.8 vs 26.5 t/s at identical acceptance (0.475). Vision costs nothing.
- pi image input: model entries must declare `"input": ["text", "image"]` or pi refuses to send images (`model.input.includes("image")` check). The 27B entry was missing it; both entries now carry it (`config/models.json.example`).
- README: HF download links for every model and drafter (Unsloth 27B + Flash-Next, incoai DFlash2, EasiiX MTP, inclusionAI Ling), and the decode envelope nuance: 22-45 t/s is content-driven (poem 26 t/s / acc 0.48 vs repetitive JSON 54 t/s / acc 0.94 at the same low effort), not config.

## 2026-09-08

- `bench/run-with-retry.sh` fixed a targeting bug and a path bug, found while pointing the runner at a non-default engine:
  - pi's `--provider` accepts the provider name only. The old `--provider llamacpp/qwen3.8-27b` slash form is silently ignored and pi falls back to the default provider from settings, so a run targeted whatever the default happened to be. Provider and model are now separate flags, settable per run (`PROVIDER`, `MODEL` env).
  - The runner `cd`s into the workdir, but wrote and stat'ed the run log as `$DIR/run-$NAME.txt` from inside it, so the log write failed instantly and every try was judged degenerate. Log paths are now relative to the workdir, and the artifact check uses `find .`.

## 2026-09-09

- `server/start-flashnext.sh`: dropped `--fit off`. Forcing full offload of the ~91GB model onto an undersized GTT heap deadlocked the iGPU and hard-locked the host (62.5 GiB GTT -> `amdgpu_vm_validate` x274 -> SIGABRT -> TTM spinlock deadlock). `--fit` now sizes the offload; raise GTT if it doesn't fully fit.
- README: GPU-memory prerequisites for the 91GB model (GTT boot args, sysfs check, the systemd-boot single-line `LINUX_OPTIONS` trap).
- Reasoning flags re-confirmed mandatory by A/B on v0.7.4.1 (temp 0, max_tokens 4096): with `--reasoning-effort medium --reasoning-budget 2048`, 2,002 reasoning / 12,833 content chars; flags omitted, 17,027 reasoning / 0 content, finish=length. Client-sent budgets still override the server default.
- Removed the untracked `bench/run-with-retry-halogen.sh`, a pre-fix duplicate of `run-with-retry.sh` (old workdir path bug, hardcoded provider). For halogen cells use `PROVIDER=halogen MODEL=halogen-qwen3.8-flash-next bench/run-with-retry.sh ...` — see the runner header.
