# Setup: the llama.cpp path (full control)

The alternative engine: Nathan's Vulkan llama.cpp builds instead of the halogen container.
Full control over flags, drafter and GGUF quant, **256k ctx** max, and the way to go for the
**27B** runs. You lose every NPU feature — see [Engines](ENGINES.md).

Use `./setup.sh --no-halogen` for the pi wiring on this path, or wire it by hand as below.

## 1. Engine

[Nathanw1014/strix-halo-llamacpp](https://github.com/Nathanw1014/strix-halo-llamacpp) v0.7.4.1+
(fixed stale-KV and a top-k race that were in upstream too); the Vulkan work is migrating to
[halo-box/strix-llama.cpp](https://github.com/halo-box/strix-llama.cpp), either tree works.
Build from source if you can — the portable payload leaves 6-24 % decode on the table.

## 2. Models

- **27B target** — [unsloth/Qwen3.8-27B-GGUF](https://huggingface.co/unsloth/Qwen3.8-27B-GGUF)
  UD-Q4_K_XL (Dynamic v3, the pick; the Q4-Q8 PPL plateau is flat). For exact reproduction of
  published-era numbers: UD-Q5_K_XL @ revision `408fcc1807ab` (v2), set `TARGET_GGUF`.
- **27B vision** — `mmproj-F16.gguf` (same repo), passed as `-mm ... --mmproj-offload` so pi
  sessions always have the projector on GPU. A/B (2026-09-07): projector GPU vs CPU placement
  is byte-identical on decode — vision is free.
- **pi image input** — the model entry in `models.json` must declare
  `"input": ["text", "image"]` or pi refuses to send images.
- **27B drafter** — [incoai/Qwen3.8-27B-DFlash2-GGUF](https://huggingface.co/incoai/Qwen3.8-27B-DFlash2-GGUF)
  Q4_K_M · **Flash-Next drafter** —
  [EasiiX MTP Q8_0](https://huggingface.co/EasiiX/Qwen3.8-Flash-Next-MTP-Strix-Halo-GGUF)
- **Flash-Next (llama.cpp variant)** —
  [unsloth UD-IQ4_XS](https://huggingface.co/unsloth/Qwen3.8-Flash-Next-GGUF) (3 shards) +
  `mmproj-F16.gguf`
- **Aux sidecar** — [inclusionAI/Ling-3.0-tiny](https://huggingface.co/inclusionAI/Ling-3.0-tiny)
  Q4_K_M (runs happily on the portable v0.7.6.1 payload). This is the server every ling-tiny
  extension talks to on `:8090`; it is a GPU model, not an NPU one.
- **Template (27B)** — vendored in `config/template/` (`sharp-v22.3.2.jinja`). Upstream:
  [peculiar-ragdoll/Qwen-Sharp-Chat-Templates](https://huggingface.co/peculiar-ragdoll/Qwen-Sharp-Chat-Templates)

## 3. Servers

One big model at a time on this APU; a second 27B-class server means device-lost.

```sh
ENGINE_DIR=... MODEL_DIR=... server/start-flashnext.sh   # daily driver
ENGINE_DIR=... MODEL_DIR=... server/start-qwen38.sh      # 256k / low-memory
ENGINE_DIR=... MODEL_DIR=... server/start-ling-tiny.sh   # sidecar (second terminal)
```

**GPU memory** — the ~91 GB model lives in the GTT heap, so size it: boot args
`amdgpu.gttsize=126976 ttm.pages_limit=32505856 ttm.page_pool_size=32505856` (on systemd-boot
put them on the single `LINUX_OPTIONS` line — a stray newline there makes the loader silently
drop the whole line). **Never force `--fit off`**: full offload onto an undersized heap
deadlocks the iGPU and hard-locks the host.

## 4. pi wiring

```sh
cp config/models.json.example ~/.pi/agent/models.json   # adjust paths/ports
cat config/settings-snippet.json                        # merge into settings.json
cp extensions/*.ts ~/.pi/agent/extensions/
```

Two numbers in the snippet you must understand, not copy: `reserveTokens` ships **10240**
because that is right for a 65536 window (halogen's launcher rescales it to match its own ctx); on the 27B's 262144 window use
**~167144** instead. `thinkingBudgets` (per-request budgets pi sends) are what actually govern
pi sessions; the server's `--reasoning-budget` flag is only a default for clients that do not
send one. On this path the NPU extensions are inert — there is no halogen to serve them.

## 5. Verify you are actually local

`curl localhost:8080/metrics` while pi runs; if `prompt_tokens_total` is not climbing, pi fell
back to a cloud provider.
