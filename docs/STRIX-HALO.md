# Strix Halo reference setup

Everything here is specific to the halogen engine on a Strix Halo box, the setup the numbers in this repo come from. For
any other engine see [ENGINES.md](ENGINES.md). Back to the [README](../README.md).

## The stack

One box, two model servers (three with voice), three pieces of silicon:

- **halogen** (`:8731`, iGPU): Qwen3.8 Flash-Next 125B from a local checkpoint, the agent's main model, 262,144 ctx x 4
  slots, prompt cache on.
- **NPU sidecars** (`:8731`, NPU): three 0.6-0.8B models on the same server: guard, decider, embedder (the reranker is no
  longer launched). Detailed in [The NPU](#the-npu).
- **ling3.0-tiny** (`:8090`, iGPU, llama.cpp): the sidecar for commit messages, compaction summaries and repo maps.
- **semble** (child process, CPU): semantic code search over `uvx`; no model server.
- **Lemonade** (`:13305`, CPU, opt-in `--voice`): whisper STT and kokoro TTS.

Flow: every prompt is screened by the NPU guard before the main model sees it. The main model generates on the iGPU with
its prompt cache warm. Side jobs that need a judgement in milliseconds (is this an injection, which of these three is
it, which files are near-duplicates) go to the NPU instead of the 125B model. Sidecars read diffs and transcripts
directly so they never evict the cached prefix. Search runs on CPU against an index built at first query. Halogen is the
only engine `setup.sh` launches and manages (`start.sh` can also start gufo and Strata); every other engine is detected
and wired but you run it yourself ([what breaks on another engine](ENGINES.md) is measured, too).

## Model files

From [huggingface.co/peonist-ai/halogen-qwen3.8-flash-next](https://huggingface.co/peonist-ai/halogen-qwen3.8-flash-next),
plus four separate NPU repos:

| file | size | why |
|---|---|---|
| `qwen38-flash-next-v2.hgn` | 66.7 GB | the main checkpoint (MTP drafter head included) |
| `qwen38-flash-next-ngram.hgn` | 51.2 GB | **required**: the checkpoint's weight lookup table (read through the page cache; not an n-gram drafter despite the name) |
| `qwen38-flash-next-vision.hgn` | 0.9 GB | vision tower; image input is enabled by default |
| `tokenizer/` | small | chat template the engine loads |
| `npu/decider-0.8b/` | 2.3 GB | `triage`: constrained decisions (55 ms warm, ~1.6 s to wake) |
| `npu/qwen3-embedding-0.6b/` | 848 MB | `dedup_scan` embeddings |
| `npu/qwen3guard-gen-0.6b/` | 787 MB | injection screening (`auto-guard`) |
| `npu/qwen3-reranker-0.6b/` | 821 MB | **optional file now**: dropped from the default `HALOGEN_NPU_MODELS` (v1.4); nothing calls `/v1/rerank`, so keep it only if you re-add reranking yourself |

Put everything in one directory (e.g. `~/models/halogen-qwen3.8-flash-next/`), with the NPU models in a lowercase `npu/`
subdirectory. The launcher mounts it read-only at `/models` inside the container, so the engine reads its NPU models from
`/models/npu/`.

```sh
pip install -U "huggingface_hub[cli]"   # provides the hf command
hf download peonist-ai/halogen-qwen3.8-flash-next \
  qwen38-flash-next-v2.hgn qwen38-flash-next-ngram.hgn qwen38-flash-next-vision.hgn tokenizer/ \
  --local-dir ~/models/halogen-qwen3.8-flash-next
for n in decider-0.8b qwen3-embedding-0.6b qwen3guard-gen-0.6b qwen3-reranker-0.6b; do
  hf download peonist-ai/halogen-npu-$n --local-dir ~/models/halogen-qwen3.8-flash-next/npu/$n
done
```

Skip the rest (`w4b.hgn`, `ht43.hgn`, `MTP.hgn`, `w4b.overlay*.hgn`, `qwen3.5-2b`): the checkpoint carries its own draft
head. Upstream also ships an optional smaller/faster `ht43.hgn` checkpoint; the default stays `v2`, and you pass the
`ht43.hgn` path explicitly to use it. The first cold boot pins the 47.7 GiB lookup table from disk, so a few minutes of
startup is normal.

Do not clone the whole model repo: it carries ~190 GB of optional variants and the minimum set is ~124 GB.
`./setup.sh --halogen auto` offers to fetch exactly the right files and resumes where it stopped if interrupted.

## The NPU

The NPU is not where the big model runs. By default it runs three small models whose whole job is to answer, in 0.1-1 s,
questions the 125B model would take 5-90 s on, without touching the main model's prompt cache. Halogen exposes them on
the same `:8731` server.

| model | how it is called | used by | what it does | measured |
|---|---|---|---|---|
| `decider-0.8b` | `/v1/chat/completions` with a `json_schema` enum + logprobs | `triage` | picks one of 2-5 options, returns per-option probabilities | **55 ms warm, ~1.6 s cold** (first call after idle, measured 10-14), 78 % on 18 binary prompts |
| `qwen3-embedding-0.6b` | `/v1/embeddings` | `dedup_scan` | embeds files for near-duplicate detection | 70 ms/query, 38 files in 8.4 s; low traffic (2 runs logged) and loads on first use, so it costs nothing resident |
| `qwen3guard-gen-0.6b` | `/v1/moderations` | `auto-guard`, every prompt | flags prompt injection | 207 ms p50 / 285 p95, **42 % recall, 0 % fp, re-verified 10-14** by `bench/guard-canary.py` |
| ~~`qwen3-reranker-0.6b`~~ | `/v1/rerank` | **nothing since v1.1** | ranked the retired NPU search | out of the default launch as of 10-14: 0 calls in the event log |

> [!NOTE]
> The NPU does **not** do codebase search. It used to, with an embedding + rerank RAG pipeline, and that moved to the
> semble MCP server in v1.1 (17/18 vs 13/18 top-3, 2-3x faster queries). What the NPU still owns is everything the big
> model is too slow or too expensive for: per-prompt screening, one-shot routing decisions, and embedding sweeps over
> thousands of files.

Everything on it is optional and every extension degrades quietly: with no halogen server, `auto-guard` fails open,
`triage` and `dedup_scan` return an error string, and the agent keeps working. Search, commit messages and compaction
never needed the NPU.

```sh
# which NPU models the server has configured vs actually loaded (they load on first use)
curl -s localhost:8731/health | python3 -c "import json,sys;print(json.load(sys.stdin)['npu'])"

# the default set is already trimmed (reranker out); override to go smaller still
HALOGEN_NPU_MODELS=decider-0.8b,qwen3guard-gen-0.6b ./start-halogen.sh
```

> [!WARNING]
> **GPU + NPU at the same time is only safe with the GPU fabric clock held** (halogen NPU docs). Without it, concurrent
> GPU and NPU work can hang the device or corrupt output. `./setup.sh --doctor` probes it and prints the one-time
> host-side install for the `halogen-fabric-clock` unit when it is not held. The container holds the clock while it runs;
> the gap is any NPU call made with no container up.

## Upgrading halogen

The image tag is **pinned** in `server/start-halogen.sh` (`HALOGEN_IMAGE_TAG`, default the version this harness was last
validated on). One command does it safely:

```sh
./setup.sh --halogen-upgrade          # default: latest
./setup.sh --halogen-upgrade 0.18.14  # or a specific tag
```

`setup.sh` asks once whether `start.sh` should print a one-line notice when a newer engine release exists
(`HALOGEN_CHECK_UPDATES=1|0` overrides; the answer is stored in `~/.pi/agent/harness-update-check`). It only upgrades if
you answer y.

The upgrade pulls the image, refreshes the NPU model files (new images can need new NPU device files; 0.18 added the
embedding `u10.elf`), restarts, then runs the acceptance battery: health + version match, prompt cache on, NPU probes
< 1 s, guard smoke, MTP decode > 30 t/s, and a `pi -p` smoke. Only then does the pin move; any failed check restores the
previous NPU files and restarts on the previous pin automatically. Read the changelog first: tool-calling, schema, cache
and log-routing behavior have all changed between releases.
