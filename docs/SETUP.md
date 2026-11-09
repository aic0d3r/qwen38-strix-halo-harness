# Setup and daily use

The full walkthrough of `./setup.sh`, the `./harness` commands and `./start.sh`. Back to the [README](../README.md).

## Guided setup

`./setup.sh` in a terminal (same as `./harness setup`) runs a wizard that works for any engine:

0. **Checks for harness updates.** If your checkout is behind origin it lists the new commits, offers
   `git pull --ff-only`, then restarts on the new code. It stays quiet offline, with local edits, or when there is
   nothing new.
1. **Detects** your hardware and which engines are already serving: halogen, gufo, llama.cpp, Strata, vLLM, Ollama,
   LM Studio.
2. **Shows one engine menu every run** (current engine first): running or not, the served model, the last measured
   prefill/decode speed, what you lose on that engine (the NPU tools off halogen), and a warning when halogen and gufo
   would fight over the same GPU memory. Picking an engine that is down offers to start it (halogen, gufo), asks before
   stopping the one holding the memory, and waits until it answers. On a fresh box with no engine running the menu still
   shows, halogen first on an NPU box, and picking halogen without a checkpoint offers the ~124 GB download
   ([`scripts/download-halogen.sh`](../scripts/download-halogen.sh)). Other engines print how to start them. To switch
   later, run `./setup.sh` again.

   **All questions come first**, then a summary ("about to: ...") and one `Continue? [Y/n]`; after that the run needs
   nothing from you. Before `Continue?` nothing has been installed or started, and **Esc** (or Ctrl-C) at any prompt
   exits cleanly. Prompts use `gum` when it is installed. The first run prints the full install output; later runs print
   one line per step with only warnings and failures in full (everything goes to `~/.pi/agent/setup.log`; `--verbose`
   shows it all). One-time sudo fixes (fabric clock, vm-compact) are shown once, then only by `--doctor` until fixed.
3. **Writes the pi provider** with per-model defaults (reasoning, sampling, compaction reserve) from
   [`config/profiles.json`](../config/profiles.json), and records the active engine in a profile that the sidecar, guard
   and NPU extensions read, so they follow your engine instead of assuming halogen.
4. **Probes** prefill and decode for ~1 min and sizes `maxTokens` and the compaction reserve to what it measured, then
   prints a short summary and offers to start pi in the current directory. The probe is one short prompt (a few thousand
   tokens), so its speeds are for sizing limits only. They are not comparable to the engine benchmark tables in
   [BENCHMARKS.md](BENCHMARKS.md#engines-on-strix-halo), which use the `halogen-cmp.py` battery and 2k-119k depth prompts.
5. **Extras, asked once and remembered** (`./harness config` changes one; `./harness setup --reconfigure` asks all
   again):
   - whether to run the **ling-tiny sidecar** (default yes; it holds ~8 GiB of GPU memory, and "no" means the main
     model writes commits and summaries, with compaction blocking on it; non-interactive: `./setup.sh --no-sidecar` /
     `--sidecar`);
   - the engine update check (any engine with a known release source: halogen, gufo, Strata, llama.cpp, vLLM, Ollama).
     On halogen, gufo and Strata it offers the safe upgrade right away (`scripts/upgrade-<engine>.sh`: update, restart,
     check, roll back on failure); on the others it prints the upgrade command. `start.sh` repeats the one-line notice,
     and `./harness update-check` runs it on demand;
   - voice.

   Serena language servers for the languages found in your project are enabled automatically in `.serena/project.yml`
   (restart serena to load them).

Servers you started yourself are never restarted. Run non-interactively (scripts, CI) and `./setup.sh` does the plain
install instead.

### Flags

- `--halogen auto` finds `*.hgn` in `.`, `/models`, `~/models` and `~/Downloads` (or pass a path). Restart later with
  `./start-halogen.sh`, which is written on first launch.
- `--no-halogen` installs pi without container management: a laptop pi against your own providers, or against a remote
  Strix Halo appliance via `baseUrl` + `PI_NPU_BASE`.
- `--strata [url]` and `--gufo [url]` do a pi-only install with a provider for that engine.
- Also: `--voice`, `--halogen-upgrade [tag]`, `--uninstall`, `--help`.

Prerequisites for halogen: docker (setup will not sudo-install it) and, for the NPU driver, an IOMMU-enabled gfx1151 box.
The launch is pinned to local files (no `HALOGEN_DOWNLOAD`), so the engine makes no outbound connections. Plain `pi`
also works after setup: startup defaults point at the engine you chose, and existing overrides are never touched.

### Commands

| command | does |
|---|---|
| `./harness setup` | the wizard above |
| `./harness detect` | list hardware and running engines, change nothing |
| `./harness recommend` | pick an engine and model for the detected hardware |
| `./harness use <engine>[/<model>]` | switch pi to another engine or model |
| `./harness status` | engine up/down, model, NPU, saved tuning, sidecar state |
| `./harness config` | menu of saved answers (engine, sidecar, voice, update check, sudo hints); change one, applied right away |
| `./harness tune` | re-run the speed probe and re-size the limits |
| `./harness monitor` | the live dashboard for the active engine (see [Operations](OPS.md#watching-speed-prefill--decode)) |
| `./setup.sh --doctor` | read-only health check of the whole install (`--refusals` also checks the running model for over-refusal on 19 one-line tasks) |
| `./setup.sh --refresh-mcp` | reset the serena/semble MCP exposure and ensure the default DDG server (backs up `mcp.json` first) |

## Daily use

```sh
# daily driver, any engine: heals what it manages, then opens the pi TUI on the engine ./setup.sh picked
./start.sh ~/my-project
```

`start.sh` (= `./harness up`, then pi) does five things:

1. Starts the engine you picked if it is down (halogen, gufo, Strata). It asks first if another engine holds the GPU
   memory, and never launches halogen beside gufo or Strata.
2. Re-checks the install quietly. Only problems print; the full log is `~/.pi/agent/setup.log`.
3. Shows the update notice with the upgrade offer.
4. Opens pi on the provider and model from the wizard.
5. For engines it has no launcher for (vLLM, Ollama, ...), warns and opens pi anyway.

`./harness status` shows what pi is pointed at.

In the session: `read`/`edit`/`bash` as usual, `mcp__semble__search` for "where is X implemented", `commit` for a
sidecar-written message, `triage` when a decision is a one-word call, and `/tune` to change compaction headroom, output
budget, temperature or ubatch mid-session. Codebase search needs no setup: semble indexes on first query.

The default halogen launch is its native max (**262,144 ctx x 4 slots**) behind an allocator pre-flight gate. If order-9
pages are low it recovers or fails in seconds with the exact fix command. Context knobs, the `vm-compact.timer` and the
small-context opt-out are in [Operations](OPS.md).

## Uninstall

```sh
./setup.sh --uninstall
```

Removes everything the installer provisioned (extensions, templates, skills, theme, pi defaults, the providers and the
wizard's profile files where they still match what setup set, and the systemd units) after backing all of it up to
`~/pi-harness-uninstall-backup-*.tar.gz`. **Deliberately left alone:** your own model entries, `mcp.json`, the sidecar
GGUF (the reclaim command is printed), the halogen container and your checkpoint files.
