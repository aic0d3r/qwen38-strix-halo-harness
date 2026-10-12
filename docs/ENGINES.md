# Engines, degradation and the cloud hybrid

Halogen is the only engine this harness provisions and verifies — by design: the extensions that
make it worth running ride on halogen's NPU sidecars, and no other engine exposes them. You can
point `models.json` at any OpenAI-compatible server yourself; pi keeps working and the
degradation is graceful but real (all measured).

## Two first-class stacks

| stack | what | when |
|---|---|---|
| **halogen** (recommended) | NPU+GPU engine, Qwen3.8 Flash-Next (125B) from a local checkpoint, zero network | just works, **262k ctx** x 4 slots |
| **llama.cpp (Vulkan)** | Nathan's builds, full control, DFlash2/MTP spec decode, vision, local GGUFs, zero network, **256k ctx** max | tuning and the **27B** runs — see [the llama.cpp path](LLAMACPP.md) |

## What breaks on another engine

| feature | on gufo 0.12.0 / plain llama.cpp |
|---|---|
| chat, tools, streaming, reasoning control, structured output | works |
| semantic search (semble MCP) | works — no NPU involved |
| prompt-injection screening (`auto-guard`) | dead — no `/v1/moderations` |
| `triage` / `dedup_scan` | dead — no decider or embedder |
| image input | dead as launched (no `--mmproj` wired) |
| `/health` verification (cache, vision) | degrades to "unknown" |
| decode (gufo, MTP) | ~35% slower than halogen 0.18.1: 40 vs 62 t/s median battery, 29-32 vs 45-50 t/s from 4k to 131k (flat on both) |
| cold prefill (gufo) | ~15% slower: 131k in 94 s vs 79 s; warm replay is ~0.1 s on both |
| tool calls (gufo) | valid when complete; a call cut off by `max_tokens` comes back empty, not partial |

Measured 2026-10-12, same session, fabric clock held, UD-Q4_K_XL + shared Q8 MTP (gufo 0.12
no longer loads UD-IQ4_XS). Raw `gufo bench`: pp4096 1489, pp32768 1400, tg128 26 tok/s. The
XDNA2 NPU-prefill fork (mikealanni/gufo, claims +7%) is unmerged: upstream rejected its GPU
kernels and holds the NPU path on quality/stability ([gufo#558](https://github.com/gufo-org/gufo/issues/558)).

`./setup.sh --doctor` tells you when a non-halogen engine is answering on `:8731`.

> [!WARNING]
> **Never run two of these engines at once** — they fight over the same unified-memory heap and
> device-lost follows.

## Sidecar isolation (ling-tiny-commit and friends)

The ling-tiny extensions call `:8090` directly instead of the main model — on purpose. Diffs,
session transcripts and repo inventories never enter halogen's context window or prompt cache.
A side call can therefore never evict the active session's cached prefix (an eviction costs
~150-190 s of re-prefill; the sidecar call costs ~0.5-2.5 s).

ling3.0-tiny runs with thinking disabled at the template level (`enable_thinking: false`): the
think pass ate token budget and 8.6x'd latency, and the rule-retention canary stays 10/10
without it.

## Cloud models are a supported hybrid

Point pi at any cloud provider and keep halogen running, and the NPU tools plus the whole
ling-tiny sidecar suite stay on-device: screening, search and compaction never follow your code
to the cloud. `compaction.reserveTokens` carries over as-is — it is a space reserve, not a
trigger point, so a 1M-context cloud model simply compacts later. The one cost is switching
itself: the first cloud turn re-uploads the session, and halogen's prompt cache holds your
prefix warm (within its LRU) for when you switch back.
