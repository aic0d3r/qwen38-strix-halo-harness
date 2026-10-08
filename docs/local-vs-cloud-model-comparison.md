# Local vs cloud model comparison: Qwen3.8 Flash-Next vs GLM 5.3 (flash / flashx) on a real debugging task

Six runs, one real prompt, ground truth verified on the box. Same task given to the local
125B MoE (Qwen3.8 Flash-Next, served by halogen on a Strix Halo Flow Z13 at 70W) and to
z.ai's GLM 5.3 tiers through two different coding clients. Every answer below was checked
against a root-cause forensics pass done on the machine before any rating.

This is the detailed companion to the Reddit post. All numbers here are from logged runs on
2026-10-08.

![Flash-Next vs GLM 5.3 tiers: time to first token and decode rate](charts/local-vs-cloud.png)

## The task

Pasted verbatim into each model's session (a real pacman pre-transaction hook log):

```
:: Running pre-transaction hooks...
(1/1) Creating Timeshift snapshot before upgrade...
Mounted '/dev/nvme0n1p7' at '/run/timeshift/1140196/backup'
RSYNC Snapshot saved successfully (37s)
Tagged snapshot '2026-10-08_00-50-53': ondemand
/tmp/timeshift-taVoJWRL/17914134901266344231: line 10: status: No such file or directory
Mounted '/dev/nvme0n1p7' at '/run/timeshift/1141222/backup'
Removing '2026-10-06_11-56-49'...
Removed '2026-10-06_11-56-49'
```

Question: what happened, is anything broken, what causes the `status: No such file or
directory` line, does it need fixing?

## Ground truth (verified before rating any answer)

- The failing script is `/tmp/timeshift-taVoJWRL/<nanoseconds>` - a temp dir created by
  **timeshift 26.09 itself**, wrapping privileged commands. Its exit-code plumbing is
  `exitCode=$?; echo ${exitCode} > status`.
- The error is that relative redirect failing with ENOENT (the write target vanished), and
  bash prints the redirect target name. Two competing micro-mechanisms were observed across
  answers: the temp dir deleted before an async write (journal shows a pkexec notify-send in
  flight), and/or the script's working directory (the snapshot mount) unmounted before the
  last line. Both are ENOENT races in the same plumbing.
- `status` does not exist in `/usr/bin/timeshift-autosnap` (AUR 0.10.0-1) - a separate
  script. The autosnap leftover `/tmp/timeshift-autosnap.*` is a real but unrelated flaw
  (mktemp -u never cleaned).
- Impact: none. Snapshot created and tagged, old snapshot pruned, hook exited 0, upgrade
  completed (pacman.log).
- Upstream: linuxmint/timeshift#496 (open) - "Fixed error in status file writing via
  notify-send after TEMP_DIR deletion" - confirms the deleted-temp-dir + notify-send race as
  the official diagnosis.

## Results

| run | client | model | wall | verdict | mechanism | proof |
|---|---|---|---|---|---|---|
| 1 | pi harness (local) | Flash-Next 125B | 2m55s | correct | correct (redirect race) | journalctl trace, pacman.log, upstream PR #496 found |
| 2 | pi harness (cloud) | glm-5.3-flashx | 2m41s | correct | correct + most precise (unmounted snapshot mount as CWD, binary symbol named) | status-file-0 empirically checked in leftover dirs, pacman.log packages verified |
| 3 | pi harness (cloud) | glm-5.3-flash | 9m09s | correct | correct + live reproduction (ran timeshift --list, watched the status file appear) | journal confirm, autosnap.conf read, cost $0.022 |
| 4 | opencode | glm-5.3-flash | 1m8s | correct | wrong (blamed a broken-shebang `status` file in PATH) | handed diagnostic commands back to the user |
| 5 | opencode | glm-5.3-flashx | 1m30s | correct | correct | corroborated via web search |
| 6 | opencode | GLM 5.3 (max) | 6m15s | correct | correct (called the regression in the internal script runner) | pacman.log check; missed the existing upstream PR |

## What the six runs show

1. **Verdicts were cheap; proof was rare.** All six runs said "harmless, nothing broken."
   Three of six got the mechanism right, two proved it with on-box evidence, one found the
   upstream PR.
2. **The harness changed the answer more than the model did.** The same glm-5.3-flash took
   1m8s in a lighter client (wrong mechanism) and 9m09s in the pi harness (correct mechanism,
   live reproduction, 93% cache hit, $0.022). Depth came from the tool loop, not the weights.
3. **The premium tier matched the local box in the same harness.** flashx at 2m41s vs
   Flash-Next at 2m55s, both correct, both deep - each with a unique find (mount-unmount
   precision vs the journalctl race trace and the existing PR). A dead heat on quality at
   local speed, with the 125B staying on the desk.
4. **Decode rate predicted almost nothing.** flashx decodes at ~151 tok/s and flash at ~45;
   the 151 tok/s run was 1m30s shallow, the 45 tok/s run (in pi) was 9m09s deep. Thinking
   budget and tool loop dominated wall time.

## Caveats

- One task, six runs. This is a depth comparison, not a capability benchmark.
- Runs 4-6 were in a different client than runs 1-3; client system prompts and tool loops
  differ. Run 3 vs run 4 is the controlled pair (same model, two clients).
- Two early raw-API probes of the GLM tiers returned empty content (reasoning stream
  exhausting a fixed max_tokens). Those are harness artifacts of bare-endpoint testing and
  are excluded from the table; they are why the client runs above were done in real coding
  clients.

## Repro

- Prompt: the log above plus the question line.
- Checker: root-cause forensics on the box (hook chain, binary strings, /tmp artifacts,
  journal timestamps, pacman.log).
- Models: halogen-qwen3.8-flash-next (halogen 0.17.1, NPU sidecars resident) and
  glm-5.3-flash / glm-5.3-flashx / glm-5.3 via the z.ai coding endpoint.

Related: [halogen-flash-server](https://github.com/peonist-ai/halogen-flash-server) and the
[neon-ladder benchmark](https://github.com/aic0d3r/neon-ladder).
