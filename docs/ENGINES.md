# Inference Engines: Halogen vs gufo vs llama.cpp (NVIDIA, Apple, Strix Halo)

Any OpenAI-compatible server (or cloud provider) works with pi, so on other hardware (NVIDIA, Apple Silicon) use
whatever engine fits: llama.cpp CUDA/Metal, vLLM, Ollama, LM Studio, MLX, or gufo where it
supports your GPU. Only Strix Halo was measured here. On Strix Halo, **halogen and gufo are the
best options**. Halogen is the only engine this harness provisions and verifies — by design: the
extensions that make it worth running ride on halogen's NPU sidecars, and no other engine exposes
them. Point `models.json` at your engine yourself; pi keeps working and the degradation is
graceful but real (all measured on gufo / llama.cpp).

## Two first-class stacks

| stack | what | when |
|---|---|---|
| **halogen** (recommended) | NPU+GPU engine, Qwen3.8 Flash-Next (125B) from a local checkpoint, zero network | just works, **262k ctx** x 4 slots |
| **llama.cpp (Vulkan)** | Nathan's builds, full control, DFlash2/MTP spec decode, vision, local GGUFs, zero network, **256k ctx** max | tuning and the **27B** runs — see [the llama.cpp path](LLAMACPP.md) |

## Strata: the pick for NVIDIA and AMD desktop GPUs

[Strata](https://github.com/Niko1221/Strata) (free, open source) runs the same Qwen3.8-Flash-Next
family on gaming PCs: NVIDIA RTX 20-50 or AMD RX 7000/9000 with 12 GB+ VRAM, 32 GB+ RAM, Windows or
Linux (Apple Silicon and Strix Halo are listed as experimental). It serves an OpenAI-compatible API
on `http://127.0.0.1:8080`, so it plugs into pi like any other engine.

```bash
# 1. install + start Strata itself (START-HERE.bat or ./setup.sh in its folder)
# 2. then, in this repo:
./setup.sh --strata                      # or: ./setup.sh --strata http://host:port
./setup.sh --doctor                      # checks Strata is answering
pi --provider strata --model <id>        # id: `curl -s localhost:8080/v1/models`; pi >= 1.0 rejects --provider alone
```

`--strata` is a pi-only install (it implies `--no-halogen`): it installs the extensions and adds a
`strata` provider to `~/.pi/agent/models.json`, with model id, context and input types read from
`/v1/models` (text-only unless Strata runs with images). It does not change your default provider.
An existing `strata` provider is left untouched. If Strata runs with `API_KEY`, put it in that
provider's `apiKey`. Same limits as any non-halogen engine (table below): no NPU
guard/triage/dedup.

**Measured on this box (Strix Halo, Strata 0.1.44.1, UD-IQ4_XS, 256k ctx, nothing else running).**
Default setup config vs the tuned one below (own A/B script, greedy, thinking off, fresh prompts):

| | default | tuned |
|---|---|---|
| cold prefill, 8k / 32k tokens | 683 / 669 t/s | 1,013 / 1,015 t/s |
| cold prefill, 94k tokens | n/a | 1,012 t/s |
| decode, short code replies | 53 t/s | 56-60 t/s |
| decode with 32k / 94k of context | 40 t/s / n/a | 42-46 t/s / 43-44 t/s |
| Strata `bench.sh`: 4k / 16k prompt | 671 / 722 t/s | 891 / 1,091 t/s |
| Strata `bench.sh`: agent follow-up turn, first token (median) | 5.65 s | 3.97 s |
| two conversations alternating, 10k tokens each | 11 s re-read per turn | 0.4-0.9 s |

**Same A/B as halogen vs gufo (2026-10-14, `halogen-cmp.py`, greedy, thinking off, nonce-tagged).**
10-prompt battery: decode median 43 t/s (range 38-59; 55-59 on short code, 38-41 on prose), ttft about
0.5 s. Depth sweep, prompt tokens as Strata counts them: 2.3k 717 t/s prefill / 34 t/s decode; 9.7k
942 / 36; 19.5k 915 / 33; 39.3k 1,015 / 32; 118.9k 990 / 32 (cold prefill 120 s). Raw rows:
`battery|depth|depth131k-strata-iq4xs.jsonl` in `LLMBench/results/halogen-cmp/`. Decode here is
below the live-session range in the next paragraph because the battery is long prose at greedy.

Live agent session (2026-10-14, `scripts/halogen-top.sh --strata`, 100k+ contexts): cold prefill
900-1,000 t/s on 7k-25k tokens read, decode 53-60 t/s early and 37-45 t/s past 100k, MTP accept
52-75%. Prompts that were mostly cached read only a few hundred tokens, so their prefill t/s is
noisy and low (110-800), not a slowdown. Strata's `/metrics` prompt counter includes cache-reused
tokens; the script subtracts them.

Halogen is still ahead on this chip (65 t/s decode battery median on 0.18.5, 131k cold in 86 s, 119k in 78 s,
about 1,520 t/s), so here Strata is the open-source/GGUF alternative, not the upgrade. Its pitch is
NVIDIA/AMD desktop GPUs. Needle test on the tuned config: 5/5 at 28-30k tokens.

Tuned config (section 5 of Strata's `docs/STRIX_HALO.md`, plus three flags), in
`strata-<model>.json`:

```
"env": { "STRATA_PF_FUSED": "1", "STRATA_PF_GEMM": "1", "STRATA_HC_UPMIX": "1", "STRATA_PA_FAST": "1",
         "STRATA_HIP_WMMA": "1", "STRATA_SELECT_WMMA": "1", "STRATA_HC_Q8": "1",
         "STRATA_PF_SWITCH_MIN_T": "4096", "STRATA_PREFILL_STREAM_MIN": "128" }
args: --spec 5 --lookup-chain 3 --mtp-q4 all --conversation-cache-mib 8192 --conversation-cache-slots 4
```

The env switches change rounding (Strata gates them by KL; replies can differ slightly from the
default). Setup keeps added `env` keys when re-run but drops hand-added `args`. Tried and not
kept: `--prefill auto:32768` and `16384` (no gain, and the engine warns it lends cache slots to
the prompt path) and `--mtp-window 8192` (no gain at 94k). Not tried: holding the GPU fabric clock
(needs root; see the doctor warning).

Strix Halo install notes (never run it beside halogen, see the warning below):

```bash
cd Strata && ROCM_PATH=/nonexistent ./setup.sh --yes --family unsloth --model UD-IQ4_XS \
    --context 262144 --no-start
```

- `ROCM_PATH=/nonexistent` makes setup use its own ROCm wheels. A TheRock-layout system ROCm in
  `/opt/rocm` (CachyOS `rocm-gfx1151-bin`) is picked up and then fails at configure
  (`/opt/rocm/llvm/bin/clang++` missing; the compiler is in `lib/llvm`).
- `--yes` picked a 32k context here; pass `--context` for more.
- The 94 GB download can come from an existing copy: put the three `UD-IQ4_XS-0000N-of-00003.gguf`
  shards in `Strata-data/models/unsloth-UD-IQ4_XS/` (a hardlink works) before step 5, and setup
  verifies them by size and SHA-256.
- `--no-start` leaves the server down. Start it without opening a browser:
  `.venv/bin/python serve/server.py --engine strata --config strata-<model>.json --port 8080`.

## What breaks on another engine

| feature | on gufo 0.12.0 / plain llama.cpp |
|---|---|
| chat, tools, streaming, reasoning control, structured output | works |
| semantic search (semble MCP) | works — no NPU involved |
| prompt-injection screening (`auto-guard`) | dead — no `/v1/moderations` |
| `triage` / `dedup_scan` | dead — no decider or embedder |
| image input | dead as launched (no `--mmproj` wired) |
| `/health` verification (cache, vision) | degrades to "unknown" |
| decode (gufo, MTP) | ~38% slower than halogen 0.18.5: 40 vs 65 t/s median battery, 29-32 vs 47-50 t/s from 4k to 131k (flat on both) |
| cold prefill (gufo) | ~17% slower: 119k in 94 s vs 78 s (halogen 131k: 86 s; the "131k" label on earlier runs was this 119k-token prompt); warm replay is ~0.1 s on both |
| tool calls (gufo) | valid when complete; a call cut off by `max_tokens` comes back empty, not partial |

Measured 2026-10-12, same session, fabric clock held, UD-Q4_K_XL + shared Q8 MTP (gufo 0.12
no longer loads UD-IQ4_XS). Raw `gufo bench`: pp4096 1489, pp32768 1400, tg128 26 tok/s. The
XDNA2 NPU-prefill fork (mikealanni/gufo, claims +7%) is unmerged: upstream rejected its GPU
kernels and holds the NPU path on quality/stability ([gufo#558](https://github.com/gufo-org/gufo/issues/558)).

`./setup.sh --doctor` tells you when a non-halogen engine is answering on `:8731`.

> [!WARNING]
> **On Strix Halo, never run two of these engines at once** — they fight over the same
> unified-memory heap and device-lost follows.

## Sidecar isolation (ling-tiny-commit and friends)

The ling-tiny extensions call `:8090` directly instead of the main model — on purpose. Diffs,
session transcripts and repo inventories never enter halogen's context window or prompt cache.
A side call can therefore never evict the active session's cached prefix (an eviction costs
~150-190 s of re-prefill; the sidecar call costs ~0.5-2.5 s).

ling3.0-tiny runs with thinking disabled at the template level (`enable_thinking: false`): the
think pass ate token budget and raised latency 8.6x, and the rule-retention canary stays 10/10
without it.

## Cloud models are a supported hybrid

Point pi at any cloud provider and keep halogen running (the NPU tools need Strix Halo), and the NPU tools plus the whole
ling-tiny sidecar suite stay on-device: screening, search and compaction never follow your code
to the cloud. `compaction.reserveTokens` carries over as-is — it is a space reserve, not a
trigger point, so a 1M-context cloud model simply compacts later. The one cost is switching
itself: the first cloud turn re-uploads the session, and halogen's prompt cache holds your
prefix warm (within its LRU) for when you switch back.
