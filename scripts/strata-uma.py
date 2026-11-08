#!/usr/bin/env python3
"""Strix Halo (Linux): run a Strata GGUF-in-place config with every expert in the GPU's shared pool, when they fit.

Strata's setup writes its RAM-budget mode for UD-Q4_K_XL / UD-IQ4_XS on a unified-memory APU (a fixed cache + a pinned
--resident-budget-gib, the rest read from the GGUF), and keeps Linux gfx1151 on the plain prompt path. Measured on this
box (Orca UD-Q4_K_XL layout, 124 GiB, 128K ctx) the budget-mode config gave 26-31 t/s decode / 350-425 t/s prompt at a
40 W cap; with the changes below, at full power (85 W median): 47-48 t/s decode, 1,080 t/s prompt (8K and 30K), harness
probe 1,034 / 42.2. The rest of Strata's fast config on top (PF_F16, PF_SWITCH_MIN_T, REFILL_PARTS, --lookup-chain 3,
--mtp-q4 all, --prefill 16384) measured 4% slower prompts and the same decode, so it is left out. Each change and why:
  --expert-cache <all>   every expert in the GPU cache: `auto` sized it from the RAM free at start (22,049 of 24,576),
                         and the 1% misses read from NVMe took 1.9 s of a 5 s answer
  no --resident-budget-gib, --mmap-experts
                         no pinned RAM copy beside the cache: on one pool that is every expert twice (the cache got 0 slots)
  --ple-io mmap          the PLE table (28.8 GB for Orca) through the page cache, not locked: frees the room the cache
                         needs; Strata's own Orca guide (docs/ORCA_Q4_K_S.md) runs it so
  --no-prefill-borrow, --prefill auto:16384
                         Strata's Windows gfx1151 defaults (docs/STRIX_HALO_WINDOWS.md): prompts keep their own buffers
                         (a lent slot is re-read from the files per chunk) and read the experts once per 16K chunk
  STRATA_IGPU_PROMPT_SET, STRATA_PF_FUSED, STRATA_PF_FUSED_KQ, STRATA_PREFILL_STREAM_MIN=128
                         the matrix-core prompt set Windows gfx1151 takes by default (KL-gated by Strata: last bits
                         differ, top-1 the same), K-quant packs included, and the fused path for agent-sized prompts
Strata keeps hand edits to "args" across its setup/update runs (args vs args_written); start-strata.sh runs this on every
start anyway (idempotent), so a regenerated config is fixed again. Setup's file is kept as <config>.budget-mode.

  scripts/strata-uma.py <strata-<model>.json> [--check]   (run with Strata's .venv python; exit 0 unless an error)
"""
import glob, json, os, re, sys

CTX_FROM, CTX_TO = 131072, 262144  # setup's recommendation -> the model's trained window
RESIDENT_BESIDE_GIB = 16  # setup.py UNSLOTH_RESIDENT_BESIDE_GB: RAM left beside the experts
ENV = {"STRATA_IGPU_PROMPT_SET": "1", "STRATA_PF_FUSED": "1", "STRATA_PF_FUSED_KQ": "1", "STRATA_PREFILL_STREAM_MIN": "128"}


def strix_halo_pool_gib():
    """Carve-out + GTT of a Strix Halo (PCI 1002:1586) in GiB, else 0."""
    for d in glob.glob("/sys/class/drm/card*/device"):
        try:
            if open(f"{d}/vendor").read().strip() == "0x1002" and open(f"{d}/device").read().strip() == "0x1586":
                return sum(int(open(f"{d}/mem_info_{k}_total").read()) for k in ("vram", "gtt")) / 2**30
        except (OSError, ValueError):
            pass
    return 0.0


def ram_gib():
    for line in open("/proc/meminfo"):
        if line.startswith("MemTotal:"):
            return int(line.split()[1]) / 2**20
    return 0.0


def experts(gguf_first):
    """(GiB, count) of the routed experts over all shards, from the GGUF headers."""
    from gguf_reader import GGUFFile
    m = re.search(r"-(\d{5})-of-(\d{5})\.gguf$", gguf_first)
    shards = [gguf_first[:m.start()] + "-%05d-of-%s.gguf" % (i, m.group(2)) for i in range(1, int(m.group(2)) + 1)] if m else [gguf_first]
    size = count = 0
    for s in shards:
        for t in GGUFFile(s).tensors:
            if "_exps." in t.name:
                size += t.expected_bytes() or 0
                count += t.shape[-1] if "ffn_gate_exps" in t.name else 0
    return size / 2**30, count


def set_arg(args, flag, value=None):
    if flag in args:
        if value is not None:
            args[args.index(flag) + 1] = value
    else:
        args += [flag] + ([value] if value is not None else [])


def grow_context(cfg):
    """Setup recommends 128K on every card (a VRAM rule). On the shared pool the KV cache is small (Strata: 744 MiB of session at 131072,
    about double at 262144, against 35 GiB of pool left beside the experts here), and the model's trained window is 262144, so a
    setup-written 131072 becomes 262144. Another value (a --context choice, a hand edit) stays. Strata's docs/ENGINES.md run 256K here."""
    args, written = cfg["args"], cfg.get("args_written") or cfg["args"]
    if "--max-context" not in args or "--max-context" not in written:
        return
    i = args.index("--max-context") + 1
    if args[i] == written[written.index("--max-context") + 1] == str(CTX_FROM):
        args[i] = str(CTX_TO)


def write(cfg, path):
    if not os.path.exists(path + ".budget-mode"):
        os.replace(path, path + ".budget-mode")  # setup's original, for going back
    tmp = path + ".tmp"
    json.dump(cfg, open(tmp, "w"), indent=1)
    os.replace(tmp, path)


def main():
    path, check = sys.argv[1], "--check" in sys.argv
    cfg = json.load(open(path))
    args = cfg["args"]
    pool = strix_halo_pool_gib()
    if not pool or cfg.get("backend") != "hip" or "--native" not in args:
        return 0  # not a Strix Halo GGUF-in-place config: nothing to do
    before = json.dumps(cfg, sort_keys=True)
    grow_context(cfg)
    if json.dumps(cfg, sort_keys=True) != before:
        print(f"  strata-uma: --max-context {CTX_FROM} -> {CTX_TO} (the model's window; the KV cache is small on the shared pool)")
    root = os.path.dirname(os.path.abspath(path))
    sys.path[:0] = [root, os.path.join(root, "tools")]
    from strata_control import uma_pool  # Strata's measured pool constants (FIXED_GIB, MARGIN_GIB)
    exp, n = experts(args[args.index("--native") + 1])
    need, ram = uma_pool.FIXED_GIB + exp + uma_pool.MARGIN_GIB, ram_gib()
    if not n or need > pool or ram < exp + RESIDENT_BESIDE_GIB:
        print(f"  strata-uma: {exp:.1f} GiB of experts need {need:.1f} of {pool:.1f} GiB pool, RAM {ram:.0f} GiB: keeping setup's budget mode")
        if json.dumps(cfg, sort_keys=True) != before and not check:
            write(cfg, path)
        return 0
    old = before
    if "--resident-budget-gib" in args:
        i = args.index("--resident-budget-gib")
        del args[i:i + 2]
    set_arg(args, "--expert-cache", str(n))
    set_arg(args, "--mmap-experts")
    set_arg(args, "--ple-io", "mmap")
    set_arg(args, "--prefill", "auto:16384")
    set_arg(args, "--no-prefill-borrow")
    cfg.setdefault("env", {}).update({k: v for k, v in ENV.items() if k not in cfg["env"]})  # a value set by hand stays
    if json.dumps(cfg, sort_keys=True) == old:
        return 0
    print(f"  strata-uma: all {n} experts ({exp:.1f} GiB) fit the {pool:.0f} GiB GPU pool: "
          f"{'would write' if check else 'writing'} the all-in-pool config to {path}")
    if not check:
        write(cfg, path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
