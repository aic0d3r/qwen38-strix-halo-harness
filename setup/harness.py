#!/usr/bin/env python3
"""harness - detect the local engine/model, apply optimized pi defaults, switch later.

  ./harness setup [--yes] [--no-tune]   wizard: detect -> ask only if ambiguous -> apply -> tune -> verify
  ./harness use <engine>[/<model>]      switch the active engine/model (no restart of servers you started)
  ./harness tune                        re-run the 1-2 min probe for the active profile
  ./harness status                      what is active, is it answering, last tuning
  ./harness up                          start.sh's first step: saved engine up, install refreshed, update notice
  ./harness update-check [engine]       one line if the active engine has a newer release (when the check is on)
  ./harness config                      change one saved answer (sidecar, voice, update check, engine, sudo hints)
  ./harness detect                      print what was found (json)
  ./harness monitor [engine] [--url U]  live dashboard: Strata's own `strata monitor`, else scripts/halogen-top.sh
  ./harness recommend [--json]          best engine, weights quant and context for this hardware, and why

Profiles live in config/profiles.json. Stdlib only. Never kills or restarts servers it did not launch.
"""
import json, os, re, shutil, subprocess, sys, time, urllib.request, uuid, hashlib, glob

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT = os.environ.get("PI_CODING_AGENT_DIR") or os.path.join(os.environ.get("PI_HOME", os.path.expanduser("~/.pi")), "agent")
PROFILES = json.load(open(os.path.join(REPO, "config", "profiles.json")))
MODEL_DIRS = ["~/models", "~/.cache/huggingface/hub", "/models", "~/llm"]
TUNE_BUDGET_S = 120


def jget(path, default=None):
    """The parsed file, or default when it is missing. A file that exists but is not JSON stops the run: the
    callers read-modify-write, and falling back to the default would overwrite the user's file with only our keys."""
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except ValueError as e:
        sys.exit(f"! {path} is not valid JSON ({e}); fix or remove it, then re-run. Nothing was changed.")
    except OSError:
        return default


def jput(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:  # temp + rename: a crash mid-write must not leave a truncated config
        json.dump(obj, f, indent=2)
        f.write("\n")
    os.replace(path + ".tmp", path)


def http(url, body=None, timeout=3):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None, headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout)


# ---------- detect ----------
def hardware():
    mem = 0
    try:
        mem = int(re.search(r"MemTotal:\s+(\d+)", open("/proc/meminfo").read()).group(1)) // 1048576
    except Exception:
        pass
    gpu = "none"
    if shutil.which("nvidia-smi"):
        gpu = "nvidia"
    elif os.path.exists("/dev/kfd"):
        gpu = "amd"
    elif sys.platform == "darwin":
        gpu = "apple"
    return {"cpus": os.cpu_count(), "ram_gb": mem, "gpu": gpu, "npu": bool(glob.glob("/dev/accel/accel*"))}


# ---------- recommend: hardware -> engine, weights quant, context ----------
PICK = os.path.join(AGENT, "harness-pick.json")    # accepted recommendation: {engine, quant, ctx}; start_engine passes ctx as GUFO_CTX
QUANT_FILE = os.path.join(AGENT, "harness-quant")  # stock quant gufo loads; server/start-gufo-flashnext.sh reads it
DEFAULT_QUANT = "UD-Q4_K_XL"


def capacity():
    """What hardware() leaves out. fast_gb: memory the GPU reads at full speed (NVIDIA VRAM; AMD VRAM+GTT, the whole
    shared pool on an APU; Apple unified RAM). Best effort: a failed probe gives 0/"" and the recommendation says so."""
    h = hardware()
    name, vram, fast = "", 0, float(h["ram_gb"])
    try:
        if h["gpu"] == "nvidia":
            row = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"], capture_output=True, text=True).stdout.splitlines()[0]
            name, _, mib = row.rpartition(",")
            name, vram = name.strip(), (int(mib) + 512) // 1024  # a "24 GB" card reports 24564 MiB
            fast = float(vram)
        elif h["gpu"] == "amd":
            pools = [sum(int(open(f"{c}/mem_info_{k}_total").read()) for k in ("vram", "gtt")) / 2**30 for c in glob.glob("/sys/class/drm/card[0-9]/device") if os.path.exists(f"{c}/mem_info_gtt_total")]
            fast = max(pools) if pools else 0.0
            vram = max([int(open(f).read()) / 2**30 for f in glob.glob("/sys/class/drm/card[0-9]/device/mem_info_vram_total")] or [0])
            lines = [l for l in subprocess.run(["lspci"], capture_output=True, text=True).stdout.splitlines() if re.search(r"VGA|Display|3D", l) and "AMD" in l]
            name = re.sub(r"\s*(\[[^\]]*\]|\(rev [^)]*\))", "", lines[0].split("[AMD/ATI]", 1)[-1].split(": ", 1)[-1]).strip() if lines else ""
            name = name or "AMD GPU"
    except Exception:
        pass
    d = os.environ.get("ENGINE_DIR") or os.path.expanduser("~/LLMBench/models")
    while d != os.path.dirname(d) and not os.path.isdir(d):
        d = os.path.dirname(d)
    return {"gpu_name": name, "vram_gb": round(vram, 1), "fast_gb": round(fast, 1), "disk_free_gb": shutil.disk_usage(d).free // 2**30}


def recommend(hw, cap):
    """-> {engine|None, quant, ctx, kv, tested, download_gb, why}. Table in config/profiles.json ("recommend"). Only
    halogen on a Strix Halo with ~124 GB follows a measurement; the rest is arithmetic on published shard sizes or
    Strata's own docs and says tested=False. Context is a heuristic on spare memory (KV size per token is unmeasured)."""
    R = PROFILES["recommend"]
    fast, ram, vram = cap["fast_gb"], hw["ram_gb"], cap["vram_gb"]
    none = lambda why: {"engine": None, "quant": None, "ctx": None, "kv": None, "tested": False, "download_gb": 0, "why": why}
    if hw["gpu"] == "amd":
        if hw["npu"] and ram >= R["halogen_min_ram_gb"]:
            return {"engine": "halogen", "quant": None, "ctx": None, "kv": None, "tested": True, "download_gb": R["halogen_download_gb"],
                    "why": f"AMD NPU + GPU with {ram} GB shared memory: halogen runs the whole model and the NPU guard/triage/dedup tools (its launcher sets context and KV slots)"}
        for q in R["quants"]:
            spare = fast - q["gib"] - R["extras_gb"]
            if spare >= R["ctx_tiers"][-1]["spare_gb"]:
                ctx = next(t["ctx"] for t in R["ctx_tiers"] if spare >= t["spare_gb"])
                return {"engine": "gufo", "quant": q["name"], "ctx": ctx, "kv": None, "tested": False, "download_gb": int(q["gib"]) + R["extras_gb"] + 1,
                        "why": f"AMD GPU, {fast:.0f} GB addressable: {q['name']} ({q['gib']} GiB) leaves {spare:.0f} GB for context -> {ctx} tokens (gufo has no KV-cache flag)"}
        return none(f"AMD GPU with {fast:.0f} GB addressable: the smallest quant ({R['quants'][-1]['gib']} GiB) plus context does not fit")
    if hw["gpu"] == "nvidia":
        # Strata keeps the experts that do not fit the card in RAM; the installer here sets up its UD-Q4_K_XL (RAM + VRAM for the weights)
        if ram + vram < R["strata_min_gb"]:
            return none(f"NVIDIA {vram} GB VRAM + {ram} GB RAM: Strata's UD-Q4_K_XL needs {R['strata_min_gb']}+ GB of RAM and VRAM together; smaller packs: run Strata's own setup, then ./setup.sh --strata")
        return {"engine": "strata", "quant": None, "ctx": None, "kv": None, "tested": False, "download_gb": quant_gb(DEFAULT_QUANT),
                "why": f"NVIDIA {vram} GB VRAM + {ram} GB RAM: Strata keeps what does not fit the card in RAM (and runs on Windows and Mac too)"}
    return none(f"no supported GPU path on this box (gpu={hw['gpu']}); a remote engine works: ./setup.sh --gufo <url>")


def describe(rec, cap):
    gpu = f"{cap['gpu_name'] or 'no GPU'}, {cap['fast_gb']:g} GB GPU-addressable, {cap['disk_free_gb']} GB disk free"
    if not rec["engine"]:
        return [f"hardware: {gpu}", f"recommended: nothing - {rec['why']}"]
    out = [f"hardware: {gpu}",
           f"recommended: {rec['engine']}" + (f", weights {rec['quant']}" if rec["quant"] else "") + (f", context {rec['ctx']}" if rec["ctx"] else "") + (f", {rec['kv']} KV cache" if rec["kv"] else "")
           + ("" if rec["tested"] else "  (untested on this class of hardware)"), f"  why: {rec['why']}"]
    if rec["download_gb"] and cap["disk_free_gb"] < rec["download_gb"] + 5:
        out.append(f"  ! a fresh download is ~{rec['download_gb']} GB, only {cap['disk_free_gb']} GB free (fine if the weights are already on disk)")
    return out


def quant_gb(name):
    """Download size in GB for a stock quant: shards + MTP head + projector. None for an unknown quant."""
    q = next((q for q in PROFILES["recommend"]["quants"] if q["name"] == name), None)
    return q and int(q["gib"]) + PROFILES["recommend"]["extras_gb"] + 1


def saved_quant(engine="gufo"):
    """The stock quant the engine loads: Strata only knows the default; gufo follows the saved hardware pick."""
    try:
        q = open(QUANT_FILE).read().strip()
    except OSError:
        q = ""
    return q if engine == "gufo" and quant_gb(q) else DEFAULT_QUANT


def offer_recommendation(eng):
    """Right after the engine pick: when it is the recommended one, offer its quant and context and save the answer.
    Never changes the engine and never asks twice for the same settings."""
    if not sys.stdin.isatty():
        return
    rec = recommend(hardware(), capacity())
    if rec["engine"] != eng or eng != "gufo":  # only gufo has a pick to save: halogen and Strata size themselves
        return
    want = {"engine": eng, "quant": rec["quant"], "ctx": rec["ctx"]}
    if jget(PICK, None) == want and (eng != "gufo" or saved_quant() == rec["quant"]):
        return
    if not ask_yn(f"Use the recommended settings for this hardware? ({rec['quant'] or 'fixed weights'}, context {rec['ctx'] or 'engine default'}; {rec['why']})", True):
        return
    os.makedirs(AGENT, exist_ok=True)
    if eng == "gufo" and rec["quant"]:
        open(QUANT_FILE, "w").write(rec["quant"] + "\n")
    jput(PICK, want)


def served_models(port, engine=None):
    """Model ids the server lists, minus helper models the engine hides (halogen's NPU sidecars)."""
    hide = PROFILES["engines"].get(engine or "", {}).get("hide")
    try:
        ids = [m["id"] for m in json.load(http(f"http://127.0.0.1:{port}/v1/models"))["data"]]
    except Exception:
        return []
    return [i for i in ids if not (hide and re.search(hide, i))]


def identify(name, port):
    """Several engines share :8080 (strata / llamacpp): tell them apart by what answers."""
    if name == "llamacpp":  # llama-server tags its models owned_by=llamacpp; Strata also serves /props, so that is no tell
        try:
            return any(m.get("owned_by") == "llamacpp" for m in json.load(http(f"http://127.0.0.1:{port}/v1/models"))["data"])
        except Exception:
            return False
    if name == "gufo":  # gufo tags its models owned_by=gufo
        try:
            return any(m.get("owned_by") == "gufo" for m in json.load(http(f"http://127.0.0.1:{port}/v1/models"))["data"])
        except Exception:
            return False
    if name == "strata":
        return not identify("llamacpp", port) and not identify("gufo", port)
    return True


def detect():
    found = []
    for name, e in PROFILES["engines"].items():
        try:
            http(f"http://127.0.0.1:{e['port']}{e['probe']}")
        except Exception:
            continue
        if identify(name, e["port"]):
            found.append({"engine": name, "port": e["port"], "models": served_models(e["port"], name)})
    ggufs = []
    for d in MODEL_DIRS:
        ggufs += glob.glob(os.path.join(os.path.expanduser(d), "**", "*.gguf"), recursive=True)[:20]
    return {"hardware": hardware(), "engines": found, "gguf": ggufs[:20], "docker": bool(shutil.which("docker")), "pi": bool(shutil.which("pi"))}


# ---------- ask ----------
def cancel():
    """Esc or Ctrl-C at any prompt: leave cleanly, no traceback."""
    print("\ncancelled")
    sys.exit(130)


def key():
    """One keypress without Enter (the no-gum prompts), so Esc works there too."""
    import termios, tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)  # keeps ISIG: Ctrl-C still raises KeyboardInterrupt
        return os.read(fd, 8).decode(errors="ignore")  # arrow keys arrive as one read ("\x1b[A"), bare Esc as "\x1b"
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def choose(question, options, yes):
    if len(options) == 1 or yes or not sys.stdin.isatty():
        return options[0]
    if shutil.which("gum"):
        r = subprocess.run(["gum", "choose", "--header", question, *options], stdout=subprocess.PIPE, text=True)
        out = r.stdout.strip()
        if out:  # gum trims the line it prints: map it back to the exact option (labels can start with a space)
            return next((o for o in options if o.strip() == out), out)
        if r.returncode in (1, 130):  # Esc / Ctrl-C
            cancel()
    print(question)
    for i, o in enumerate(options, 1):
        print(f"  {i}) {o}")
    if len(options) <= 9:  # one keypress picks; Esc cancels
        print("number [1] (Esc cancels): ", end="", flush=True)
        while True:
            k = key()
            if k == "\x1b":
                cancel()
            if k in ("\r", "\n") or (k.isdigit() and 0 < int(k) <= len(options)):
                i = int(k) if k.isdigit() else 1
                print(i)
                return options[i - 1]
    a = input("number [1]: ").strip() or "1"
    return options[int(a) - 1] if a.isdigit() and 0 < int(a) <= len(options) else options[0]


# ---------- profile ----------
def model_profile(model_id):
    m = PROFILES["models"]
    out = json.loads(json.dumps(m["default"]))
    for k, v in m.items():
        if k != "default" and re.search(v["match"], model_id, re.I):
            out.update({a: b for a, b in v.items() if a != "match"})
            break
    return out


def server_ctx(port):
    """Real context window from the server, or 0 if it will not say."""
    for path, pick in (("/props", lambda d: d["default_generation_settings"]["n_ctx"]), ("/v1/models", lambda d: d["data"][0]["meta"]["n_ctx"]),
                       ("/v1/models", lambda d: d["data"][0]["context_length"])):  # gufo
        try:
            return int(pick(json.load(http(f"http://127.0.0.1:{port}{path}"))))
        except Exception:
            pass
    return 0


def build_entry(engine, model_id, ctx, tuned):
    e = PROFILES["engines"][engine]
    mp = model_profile(model_id)
    ctx = ctx or 32768
    max_tokens = tuned.get("maxTokens") or min(mp["maxTokens"], max(1024, ctx // 8))
    entry = {"id": model_id, "name": f"{model_id} ({engine})", "reasoning": mp["reasoning"], "input": ["text"],
             "contextWindow": ctx, "maxTokens": max_tokens, "samplingParams": mp["samplingParams"]}
    for k in ("compat", "thinkingLevelMap"):
        if k in mp:
            entry[k] = mp[k]
    return {"baseUrl": f"http://127.0.0.1:{e['port']}/v1", "api": "openai-completions", "apiKey": "dummy", "models": [entry]}


def apply(engine, model_id, tuned=None):
    tuned = tuned or {}
    e = PROFILES["engines"][engine]
    ctx = server_ctx(e["port"])
    mj = os.path.join(AGENT, "models.json")
    m = jget(mj, {})
    if os.path.exists(mj) and not os.path.exists(mj + ".pre-harness"):
        shutil.copy(mj, mj + ".pre-harness")  # one-time backup before we first touch it
    prov = e["provider"]
    old = m.get("providers", {}).get(prov)
    new = build_entry(engine, model_id, ctx, tuned)
    if old and engine == "halogen":  # setup.sh provisions the halogen provider; never overwrite it
        new = old
    m.setdefault("providers", {})[prov] = new
    # compaction/branch-summary look the sidecar up in the model registry; setup.sh only adds it on
    # the halogen path, so without this every other engine silently fell back to main-model summaries
    m["providers"].setdefault("llamacpp-tiny", {
        "baseUrl": "http://127.0.0.1:8090/v1", "api": "openai-completions", "apiKey": "dummy",
        "models": [{"id": "ling3.0-tiny", "name": "Ling-3.0-tiny sidecar", "reasoning": True, "input": ["text"],
                    "contextWindow": 131072, "maxTokens": 8192,
                    "compat": {"thinkingFormat": "chat-template", "chatTemplateKwargs": {"enable_thinking": False}}}]})
    jput(mj, m)
    ctx = new["models"][0]["contextWindow"]
    sj = os.path.join(AGENT, "settings.json")
    if os.path.exists(sj) and not os.path.exists(sj + ".pre-harness"):
        shutil.copy(sj, sj + ".pre-harness")  # same one-time backup as models.json
    s = jget(sj, {})
    s["defaultProvider"], s["defaultModel"] = prov, new["models"][0]["id"]
    reserve = tuned.get("reserveTokens") or min(model_profile(model_id)["reserveTokens"], ctx // 4)
    s.setdefault("compaction", {}).update({"enabled": True, "reserveTokens": reserve})
    jput(sj, s)
    jput(os.path.join(AGENT, "harness-profile.json"), {
        "engine": engine, "provider": prov, "model": new["models"][0]["id"], "npu": bool(e["npu"]),
        "npuUrl": "http://127.0.0.1:8731", "sidecarUrl": "http://127.0.0.1:8090"})
    return prov, new["models"][0]["id"], ctx


def install_extensions(engine, extra=(), verbose=True, show_ok=True, launch=True):
    """Run setup.sh without a terminal on stdin (the wizard already asked everything).
    verbose=False: full output to setup.log, one line per section on screen, warnings/failures in full.
    show_ok=False (start.sh): sections without problems leave no line at all.
    launch=False (wizard): setup.sh must not start halogen; the wizard starts the engine last itself."""
    args = (["--halogen", "auto"] if launch else []) if engine == "halogen" else ["--no-halogen"]
    cmd = ["bash", os.path.join(REPO, "setup.sh"), *args, *extra]
    env = dict(os.environ, HARNESS_STARTS_ENGINE="" if launch else "1")
    if verbose:
        return subprocess.run(cmd, stdin=subprocess.DEVNULL, env=env).returncode == 0
    os.makedirs(AGENT, exist_ok=True)
    p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True, env=env)  # stderr stays on screen (download progress)
    sec, issues = None, []

    def close():
        if sec and not issues and not show_ok:
            print("\r\033[K", end="", flush=True)
        elif sec:
            mark = "\033[31m✗\033[0m" if any("✗" in i for i in issues) else "\033[33m!\033[0m" if issues else "\033[32m✓\033[0m"
            print(f"\r\033[K  {mark} {sec}" + "".join(f"\n    {i.strip()}" for i in issues))

    with open(SETUP_LOG, "w") as log:
        for line in p.stdout:
            log.write(line)
            plain = re.sub(r"\033\[[0-9;]*m", "", line).rstrip()
            m = re.match(r"== (?:[0-9a-z-]+\. )?(.*) ==$", plain)
            if m:
                close()
                sec, issues = m.group(1), []
                print(f"  … {sec}", end="", flush=True)
            elif re.match(r"\s+[!✗] ", plain):
                issues.append(plain)
    close()
    return p.wait() == 0


# ---------- tune ----------
def hw_key(engine, model_id):
    h = hardware()
    w = "|abliterated" if engine in WEIGHTS_ENGINES and weights_pref() == "abliterated" else ""  # stock keeps its old key
    if engine == "gufo" and not w and saved_quant() != DEFAULT_QUANT:
        w = "|" + saved_quant()  # another quant is another speed; the default quant keeps its old key
    return hashlib.sha1(f"{engine}|{model_id}|{h['gpu']}|{h['ram_gb']}{w}".encode()).hexdigest()[:12]


def stream_once(port, model_id, prompt, max_tokens):
    """-> (prompt_tokens, ttft_s, decode_tps) for one streamed chat completion."""
    t0, first, n, ptok = time.time(), None, 0, 0
    body = {"model": model_id, "stream": True, "max_tokens": max_tokens, "temperature": 0,
            "stream_options": {"include_usage": True}, "messages": [{"role": "user", "content": prompt}]}
    for line in http(f"http://127.0.0.1:{port}/v1/chat/completions", body, timeout=TUNE_BUDGET_S):
        line = line.decode().strip()
        if not line.startswith("data:") or line.endswith("[DONE]"):
            continue
        d = json.loads(line[5:])
        if d.get("usage"):
            ptok = d["usage"].get("prompt_tokens", 0)
        for c in d.get("choices", []):
            delta = c.get("delta", {})
            if delta.get("content") or delta.get("reasoning_content"):
                first = first or time.time()
                n += 1
    end = time.time()
    return ptok, (first or end) - t0, (n / (end - first)) if first and end > first else 0.0


def tune(engine, model_id):
    port = PROFILES["engines"][engine]["port"]
    ctx = server_ctx(port)
    words = min(3000, max(200, (ctx or 8192) // 8))  # ~words*1.3 tokens; never more than 1/6 of the window
    filler = " ".join(f"item{i}" for i in range(words))
    res = {"engine": engine, "model": model_id, "ctx": ctx, "when": time.strftime("%Y-%m-%d")}
    try:
        ptok, ttft, _ = stream_once(port, model_id, f"[{uuid.uuid4()}] Count the items:\n{filler}", 1)
        res["prefill_tps"] = round(ptok / ttft) if ptok and ttft else 0
        _, _, tps = stream_once(port, model_id, "Write a Python function that reverses a linked list.", 128)
        res["decode_tps"] = round(tps, 1)
    except Exception as ex:
        res["error"] = str(ex)[:120]
        return res  # keep the shipped profile; a failed probe must never degrade defaults
    # derive settings: answer room scales with decode speed, compaction reserve with the real window
    if ctx:
        res["maxTokens"] = int(min(model_profile(model_id)["maxTokens"], max(1024, ctx // 8)))
        res["reserveTokens"] = int(min(60000, ctx // 4))
    cache = os.path.join(AGENT, "harness-tuned.json")
    c = jget(cache, {})
    c[hw_key(engine, model_id)] = res
    jput(cache, c)
    return res


# ---------- commands ----------
def active():
    return jget(os.path.join(AGENT, "harness-profile.json"))


def cmd_recommend(a):
    h, cap = hardware(), capacity()
    rec = recommend(h, cap)
    if a.json:
        print(json.dumps({"hardware": h, "capacity": cap, "recommendation": rec}, indent=1))
    else:
        print("\n".join(describe(rec, cap)))


def cmd_quant_info(a):
    gb = quant_gb(a.name)
    if gb is None:
        sys.exit(f"unknown quant {a.name}; known: {', '.join(q['name'] for q in PROFILES['recommend']['quants'])}")
    print(gb)


def cmd_detect(_):
    print(json.dumps(detect(), indent=1))


def last_speed(engine):
    t = [v for v in jget(os.path.join(AGENT, "harness-tuned.json"), {}).values() if v.get("engine") == engine and v.get("decode_tps")]
    t.sort(key=lambda v: v.get("when", ""))
    return f"{t[-1]['prefill_tps']}/{t[-1]['decode_tps']} t/s pf/dec" if t else "not measured"


BIG = ("halogen", "gufo", "strata")  # big engines: one GPU memory heap (VRAM on NVIDIA), and gufo/strata also share :8080


def heap_holder(running):
    """The big engines share one GPU memory heap: whichever is up blocks the others."""
    return next((e for e in BIG if e in running), None)


def engine_menu(running):
    """One screen: state, model, last speed, what you lose, memory conflicts. -> engine name."""
    cur = (active() or {}).get("engine")
    hw = hardware()  # fresh box: the recommended engine, halogen where it can run, then the NVIDIA engines on an NVIDIA box, then engines we can start for you
    rec = recommend(hw, capacity())["engine"]
    order = sorted(PROFILES["engines"], key=lambda e: (e != cur, e not in running, e != rec, not (hw["npu"] and e == "halogen"),
                                                       not (hw["gpu"] == "nvidia" and e == "strata"),
                                                       "script" not in PROFILES["engines"][e], e))
    holder = heap_holder(running)
    labels = {}
    for e in order:
        c = PROFILES["engines"][e]
        mods = running[e]["models"] if e in running else []
        state = f"running: {mods[0] if mods else '-'}" if e in running else "not running"
        lose = f"loses: {c.get('loses', '?')}"
        warn = f" | ! stops {holder} first (shared GPU memory)" if e in BIG and holder and holder != e and e not in running else ""
        labels[f"{'*' if e == cur else ' '}{'o' if e in running else '-'} {e:9s}{' (recommended)' if e == rec else ''} {state} | {last_speed(e)} | {lose}{warn}"] = e
    return labels[choose("Which engine? (* = current, o = running)", list(labels), False)]


def stop_process(port):
    """SIGTERM the process listening on port (ours only: ss shows pids of the current user), wait until it lets go."""
    pids = set(re.findall(r"pid=(\d+)", subprocess.run(["ss", "-ltnpH", f"sport = :{port}"], capture_output=True, text=True).stdout))
    if not pids:
        return False
    for p in pids:
        try:
            os.kill(int(p), 15)
        except OSError:
            return False
    for _ in range(30):
        if not subprocess.run(["ss", "-ltnH", f"sport = :{port}"], capture_output=True, text=True).stdout.strip():
            return True
        time.sleep(2)
    return False


def stop_engine(engine, running):
    port = PROFILES["engines"][engine]["port"]
    cid = subprocess.run(["docker", "ps", "-q", "--filter", f"publish={port}"], capture_output=True, text=True).stdout.split()
    if cid:
        subprocess.run(["docker", "stop", "-t", "30", *cid], stdout=subprocess.DEVNULL)
    elif not stop_process(port):  # Strata runs as a plain process, not a container
        print(f"  could not find what serves {engine} on :{port}; stop it yourself")
        return False
    # the ling-tiny sidecar is a GPU llama-server holding ~8 GiB too (measured: GTT 9.7 -> 1.5 GiB when stopped);
    # it restarts on demand, so stopping it here frees the memory for the next engine
    subprocess.run(["systemctl", "--user", "stop", "ling-tiny.service"], stderr=subprocess.DEVNULL)
    gtt = lambda: sum(int(open(f).read()) for f in glob.glob("/sys/class/drm/card*/device/mem_info_gtt_used")) / 2**30
    for _ in range(10):
        if gtt() < 3:
            break
        time.sleep(2)
    print(f"  GPU memory in use now: {gtt():.1f} GiB")
    return True


def halogen_ckpt():
    """The stock checkpoint: its folder also holds what every halogen checkpoint needs (tokenizer, n-gram, npu/)."""
    return subprocess.run(["bash", os.path.join(REPO, "server", "start-halogen.sh"), "--print-stock"], capture_output=True, text=True).stdout.strip()


# ---------- weights (stock / abliterated), one saved answer the halogen and gufo launchers read ----------
WEIGHTS = os.path.join(AGENT, "harness-weights")
WEIGHTS_ENGINES = {"halogen": ("--print-ckpt", 89), "gufo": ("--print-model", 112)}  # launcher flag, download GB
WEIGHTS_ASK = (*WEIGHTS_ENGINES, "strata")  # strata: the answer picks which model scripts/install-strata.sh installs and starts
WEIGHTS_LABELS = {"stock": "stock Qwen3.8-Flash-Next (default)",
                  "abliterated": "abliterated (refusals removed; orcarouter edit, see docs/ENGINES.md#abliterated-models)"}


def weights_pref():
    try:
        return open(WEIGHTS).read().strip() or "stock"
    except OSError:
        return "stock"


def weights_file(engine):
    """The main weights file the engine's launcher will load with the saved answer (it may not exist yet)."""
    c = PROFILES["engines"][engine]
    return subprocess.run(["bash", os.path.join(REPO, c["script"]), WEIGHTS_ENGINES[engine][0]], capture_output=True, text=True).stdout.strip()


def weights_download_gb(engine):
    """GB scripts/download-abliterated.sh still has to fetch: 0 when complete; gufo also needs the stock MTP head."""
    f = weights_file(engine)
    if not os.path.isfile(f):
        return WEIGHTS_ENGINES[engine][1]
    mtp = os.path.join(os.path.dirname(os.path.dirname(f)), "mtp-Qwen3.8-Flash-Next-shared-Q8_0.gguf")
    return 5 if engine == "gufo" and not os.path.isfile(mtp) else 0


def ask_weights(engine):
    """Which weights halogen/gufo load by default; current answer first. -> True if the answer changed."""
    if engine not in WEIGHTS_ASK or not sys.stdin.isatty():
        return False
    cur = weights_pref()
    opts = sorted(WEIGHTS_LABELS, key=lambda k: k != cur)
    pick = {WEIGHTS_LABELS[k]: k for k in opts}[choose("Default model weights? (halogen, gufo and Strata load these; abliterated on Strata = gufo's orca-uncensored shards)", [WEIGHTS_LABELS[k] for k in opts], False)]
    os.makedirs(AGENT, exist_ok=True)
    open(WEIGHTS, "w").write(pick + "\n")
    return pick != cur


def stock_ready(quant=DEFAULT_QUANT):
    """Every shard of the stock quant is on disk (scripts/download-stock.sh puts them there; Strata and an unpicked
    gufo use UD-Q4_K_XL). Shard 1 is tiny and lands first, so it alone proves nothing."""
    first = subprocess.run(["bash", os.path.join(REPO, "server", "start-gufo-flashnext.sh"), "--print-model"], capture_output=True, text=True,
                           env=dict(os.environ, GUFO_WEIGHTS="stock", GUFO_QUANT=quant)).stdout.strip()
    m = re.search(r"-00001-of-(\d{5})\.gguf$", first)
    return bool(m) and all(os.path.isfile(first.replace("-00001-of-", f"-{i:05d}-of-")) for i in range(1, int(m.group(1)) + 1))


def strata_installed():
    """Strata's setup already installed the model for the saved weights answer (its run-unsloth-<model>.sh exists)."""
    return bool(subprocess.run(["bash", os.path.join(REPO, "server", "start-strata.sh"), "--print-run"], capture_output=True, text=True).stdout.strip())


def plan_start(engine, running, ask_start=True):
    """Ask everything starting engine needs (stop the memory holder, download, start); nothing runs yet.
    ask_start=False (start.sh): starting the saved engine needs no question, only stopping another one does.
    -> plan dict, or None when there is no launcher or the user declined."""
    c = PROFILES["engines"][engine]
    if not c.get("script"):
        print(f"{engine}: {c.get('start', 'start it yourself')}")
        return None
    plan = {"stop": None, "download": False, "weights": None}
    holder = heap_holder(running)
    if holder and holder != engine and engine in BIG:
        if not ask_yn(f"{holder} holds the GPU memory {engine} needs. Stop {holder} and the ling-tiny sidecar?"):
            return None
        plan["stop"] = holder
    if engine == "halogen" and not os.path.isfile(halogen_ckpt()):
        free = shutil.disk_usage(os.path.expanduser("~")).free // 2**30
        if not ask_yn(f"No halogen checkpoint on this box. Download it (~124 GB into ~/models, {free} GB free, resumable)?"):
            return None
        plan["download"] = True
    if engine == "strata" and not strata_installed():
        free = shutil.disk_usage(os.path.expanduser("~")).free // 2**30
        what = "uses gufo's orca-uncensored shards in place" if weights_pref() == "abliterated" else "uses the stock UD-Q4_K_XL that gufo shares in place"
        if not ask_yn(f"Strata is not installed for the {weights_pref()} weights. Install them (git clone into ~/coding/Strata if needed; its setup fetches the engine and {what}, switching answers rebuilds its small pack, no weights download)?"):
            return None
        plan["install"] = "strata"
        # abliterated Strata runs gufo's orca-uncensored shards in place: say so now, not when the install fails
        if weights_pref() == "abliterated" and not os.path.isfile(weights_file("gufo")):
            if not ask_yn(f"The abliterated Strata model is gufo's orca-uncensored GGUF, which is not on this box. Download it (~{WEIGHTS_ENGINES['gufo'][1]} GB, gated: accept the terms on its Hugging Face page and `hf auth login` first; {free} GB free, resumable)?"):
                return None
            plan["weights"], plan["weights_gb"] = "gufo", WEIGHTS_ENGINES["gufo"][1]
    # gufo and Strata share one stock GGUF (Unsloth's UD-Q4_K_XL, latest Dynamic): fetch it once, whichever comes first.
    # gufo may follow the hardware pick to a smaller quant; Strata only runs UD-Q4_K_XL.
    q = saved_quant(engine)
    if engine in ("gufo", "strata") and weights_pref() == "stock" and not stock_ready(q):
        free = shutil.disk_usage(os.path.expanduser("~")).free // 2**30
        if not ask_yn(f"The stock weights (Unsloth {q}, {'shared by gufo and Strata' if q == DEFAULT_QUANT else 'picked for this hardware'}) are not on this box. Download them (~{quant_gb(q)} GB, {free} GB free, resumable)?"):
            return None
        plan["stock"] = q
    gb = weights_download_gb(engine) if engine in WEIGHTS_ENGINES and weights_pref() != "stock" else 0
    if gb:
        free = shutil.disk_usage(os.path.expanduser("~")).free // 2**30
        if not ask_yn(f"The {weights_pref()} {engine} weights (or their MTP head) are not on this box. Download them (~{gb} GB, {free} GB free, resumable)?"):
            return None
        plan["weights"], plan["weights_gb"] = engine, gb
    if ask_start and not ask_yn(f"Start {engine}? (runs {c['script']} in the background, log /tmp/harness-{engine}.log)", True):
        return None
    return plan


def prepare_start(plan, running):
    """The parts of a start plan an upgrade also needs first: stop the memory holder, download. Runs once (clears them)."""
    stop, dl, wd = plan["stop"], plan["download"], plan.get("weights")
    plan["stop"], plan["download"], plan["weights"] = None, False, None
    if stop and not stop_engine(stop, running):
        return False
    if dl and subprocess.run(["bash", os.path.join(REPO, "scripts", "download-halogen.sh"), "--yes"]).returncode:
        return False
    if wd and subprocess.run(["bash", os.path.join(REPO, "scripts", "download-abliterated.sh"), wd, "--yes"]).returncode:
        return False  # before the install: abliterated Strata installs on top of gufo's shards
    sq = plan.pop("stock", False)
    if sq and subprocess.run(["bash", os.path.join(REPO, "scripts", "download-stock.sh"), "--yes"], env=dict(os.environ, GUFO_QUANT=sq)).returncode:
        return False  # same for stock: Strata installs on top of the shared UD-Q4_K_XL
    inst = plan.pop("install", None)
    return not (inst and subprocess.run(["bash", os.path.join(REPO, "scripts", f"install-{inst}.sh"), "--yes"]).returncode)


def start_engine(engine, running, plan=None, wait_s=900):
    """Start an engine we ship a launcher for (halogen, gufo); asks first unless given a plan. -> True once it answers."""
    plan = plan or plan_start(engine, running)
    if plan is None or not prepare_start(plan, running):
        return False
    c = PROFILES["engines"][engine]
    log = open(f"/tmp/harness-{engine}.log", "w")
    env = dict(os.environ)
    pick = jget(PICK, {}) or {}
    if engine == "gufo" and pick.get("engine") == "gufo" and pick.get("ctx"):
        env.setdefault("GUFO_CTX", str(pick["ctx"]))  # the accepted hardware pick; an explicit GUFO_CTX still wins
    proc = subprocess.Popen(["bash", os.path.join(REPO, c["script"])], stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True, env=env)
    t0 = time.time()
    try:
        while time.time() - t0 < wait_s:
            if served_models(c["port"], engine):
                print(f"\n  {engine} is up")
                return True
            if proc.poll() is not None:  # launchers exec the server: an exited launcher means the start failed
                tail = open(f"/tmp/harness-{engine}.log").read().strip().splitlines()[-3:]
                print(f"\n  {engine} failed to start:" + "".join(f"\n    {l}" for l in tail))
                return False
            print(f"\r  waiting for {engine} on :{c['port']} ... {int(time.time() - t0)}s (Ctrl-C to stop waiting)", end="", flush=True)
            time.sleep(3)
    except KeyboardInterrupt:
        pass
    print(f"\n  {engine} did not answer in time; see /tmp/harness-{engine}.log")
    return False


def not_answering(eng):
    e = PROFILES["engines"][eng]
    return f"{eng} is not answering on :{e['port']} - {e.get('start', 'start it first')}"


def pick_engine(d, yes, want=None, on_pick=None):
    """Questions only: -> (engine, start plan or None if it is already up). Nothing starts here.
    on_pick(engine) runs right after the choice, before any start/stop/download question (the update check)."""
    running = {x["engine"]: x for x in d["engines"]}
    if want:
        eng = want.partition("/")[0]
        if eng not in PROFILES["engines"]:
            sys.exit(f"unknown engine {eng}; known: {', '.join(PROFILES['engines'])}")
        return eng, None
    if yes or not sys.stdin.isatty():  # unattended: only what is already up, halogen first
        if not running:
            sys.exit("no engine answering on the usual ports. Start one (see docs/ENGINES.md, or `./setup.sh --halogen auto`), then re-run.")
        return next(iter(sorted(running, key=lambda e: e != "halogen"))), None
    if not running:
        print("No engine is running yet. Pick one: halogen, gufo and Strata can be started (and halogen or Strata installed) from here.")
    eng = engine_menu(running)
    offer_recommendation(eng)
    upgrading = on_pick(eng) if on_pick else False
    # the server keeps what it loaded at start: a new default needs a restart (an upgrade restarts it anyway)
    restart = ask_weights(eng) and eng in running and not upgrading and ask_yn(
        f"{eng} is running the {'stock' if weights_pref() != 'stock' else 'abliterated'} weights. Restart it on the new default?", True)
    if eng in running and not restart:
        return eng, None
    if upgrading:  # every upgrader starts the engine itself: "Start?" would be a question with only one answer
        print(f"  the {eng} upgrade starts it")
    plan = plan_start(eng, {k: v for k, v in running.items() if k != eng}, ask_start=not (upgrading or restart)) or sys.exit(not_answering(eng))
    if restart:
        plan["stop"] = eng  # prepare_start stops it, start_engine brings it back on the new weights
    return eng, plan


def bring_up(d, eng, plan, yes, mod=""):
    """Run the start plan (if any), then pick the model. -> (engine, model)."""
    if plan:
        if not start_engine(eng, {x["engine"]: x for x in d["engines"]}, plan):
            sys.exit(not_answering(eng))
        d["engines"] = detect()["engines"]
    running = {x["engine"]: x for x in d["engines"]}
    port = PROFILES["engines"][eng]["port"]
    if eng not in running:  # strata / llama.cpp / gufo share :8080: whatever answers there is what you started
        same = [x for x in d["engines"] if x["port"] == port]
        if not same:
            sys.exit(not_answering(eng))
        running[eng] = same[0]  # we started (or you named) eng: keep it, whatever detect calls that port
    models = running[eng]["models"]
    if not models:
        sys.exit(f"{eng} lists no models")
    return eng, mod or choose("Which model?", models, yes)


def pick_target(d, yes, want=None):
    eng, plan = pick_engine(d, yes, want)
    return bring_up(d, eng, plan, yes, (want or "").partition("/")[2])


# ---------- extras (asked once, answers saved) ----------
EXTRAS = os.path.join(AGENT, "harness-extras.json")
# file extension -> serena language_servers name
LANGS = {".py": "python", ".ts": "typescript", ".tsx": "typescript", ".js": "typescript", ".jsx": "typescript",
         ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin", ".rb": "ruby", ".cs": "csharp",
         ".php": "php", ".swift": "swift", ".c": "cpp", ".cc": "cpp", ".cpp": "cpp", ".h": "cpp"}


def ask_yn(question, default=False):
    """Every yes/no in the wizard: gum confirm when installed (same look as choose()), else a plain prompt."""
    if not sys.stdin.isatty():
        return default
    if shutil.which("gum"):
        # gum choose, not gum confirm: confirm exits 1 for both "No" and Esc, so Esc could not cancel
        r = subprocess.run(["gum", "choose", "--header", question, "--selected", "Yes" if default else "No", "Yes", "No"],
                           stdout=subprocess.PIPE, text=True)
        out = r.stdout.strip()
        if out in ("Yes", "No"):
            print(f"{question} {out.lower()}")
            return out == "Yes"
        if r.returncode in (1, 130):
            cancel()
    print(f"{question} {'[Y/n]' if default else '[y/N]'} ", end="", flush=True)
    while True:
        k = key().lower()
        if k == "\x1b":
            cancel()
        if k in ("\r", "\n", "y", "n"):
            a = default if k in ("\r", "\n") else k == "y"
            print("yes" if a else "no")
            return a


def sidecar_pref():
    """'1' / '0' / '' (never asked) from the file setup.sh reads."""
    try:
        return open(os.path.join(AGENT, "harness-sidecar")).read().strip()
    except OSError:
        return ""


def ask_sidecar(reconfigure):
    """Ask once whether to run the ling-tiny sidecar (default yes); setup.sh reads the saved answer."""
    if (sidecar_pref() and not reconfigure) or not sys.stdin.isatty():
        return
    dl = "" if glob.glob(os.path.expanduser("~/.models/ling3.0-tiny/*.gguf")) + glob.glob(os.path.join(REPO, "models/ling3.0-tiny/*.gguf")) else ", first run downloads 4.5 GB"
    on = ask_yn(f"Run the ling-tiny sidecar? ~2x faster commits/compaction, holds ~8 GiB GPU memory{dl}. "
                "(No = the main model writes them)", True)
    os.makedirs(AGENT, exist_ok=True)
    open(os.path.join(AGENT, "harness-sidecar"), "w").write("1\n" if on else "0\n")


def ask_once(key, question, reconfigure):
    """y/N asked on a terminal, then remembered; --reconfigure asks again."""
    st = jget(EXTRAS, {})
    if (key in st and not reconfigure) or not sys.stdin.isatty():
        return bool(st.get(key))
    st[key] = ask_yn(question)
    jput(EXTRAS, st)
    return st[key]


def serena_languages(root="."):
    """Enable serena language servers for the languages found in the project; -> languages added."""
    yml = os.path.join(root, ".serena", "project.yml")
    if not os.path.exists(yml):
        return []
    n = {}
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in (".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".serena")]
        for f in fn:
            lang = LANGS.get(os.path.splitext(f)[1])
            if lang:
                n[lang] = n.get(lang, 0) + 1
        if sum(n.values()) > 5000:
            break
    want = [l for l, c in n.items() if c >= 3]
    text = open(yml).read()
    m = re.search(r"^language_servers:\n((?:- .*\n)+)", text, re.M)
    if not m:
        return []
    have = [x[2:].strip() for x in m.group(1).splitlines()]
    add = [l for l in want if l not in have]
    if add:
        open(yml, "w").write(text[:m.end(1)] + "".join(f"- {l}\n" for l in add) + text[m.end(1):])
    return add


def git(*a, timeout=15):
    r = subprocess.run(["git", "-C", REPO, *a], capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout.strip()


def harness_update_offer():
    """If this checkout is behind its upstream, offer a fast-forward pull, then restart the wizard on the new code."""
    if not sys.stdin.isatty():
        return
    try:
        if git("fetch", "--quiet", timeout=10)[0] or git("status", "--porcelain")[1]:
            return  # offline / no remote, or local edits: stay quiet (the /harness-update command explains)
        rc, n = git("rev-list", "--count", "HEAD..@{u}")
        if rc or not n.isdigit() or int(n) == 0:
            return
        print(f"harness is {n} commit(s) behind origin:")
        print(git("log", "--oneline", "-8", "HEAD..@{u}")[1])
        if not ask_yn("Update now? (git pull --ff-only, then setup restarts)"):
            return
        if git("pull", "--ff-only", "--quiet", timeout=60)[0]:
            return print("! pull failed (diverged from origin?): run git pull yourself")
        print("updated; restarting setup on the new code\n")
        os.execv(sys.executable, [sys.executable, "-u", *sys.argv])
    except (subprocess.TimeoutExpired, OSError):
        return


UPDATE_PREF = os.path.join(AGENT, "harness-update-check")  # same answer file setup.sh and start.sh use


def update_pref():
    """'1' / '0' / '' (never asked); HALOGEN_CHECK_UPDATES=1|0 overrides (name kept for old setups)."""
    try:
        return os.environ.get("HALOGEN_CHECK_UPDATES") or open(UPDATE_PREF).read().strip()
    except OSError:
        return ""


def _ver(s):
    """'Strata 0.1.44.1' / 'v0.40.6' / 'b6312-abc' -> (kind, numbers) to compare, or None."""
    m = re.search(r"\bb(\d{3,})\b|(\d+(?:\.\d+)+)", str(s or ""))
    return None if not m else ("b", (int(m.group(1)),)) if m.group(1) else ("v", tuple(map(int, m.group(2).split("."))))


def engine_update(engine):
    """-> one line if a newer release of engine exists; '' when current, unknown or offline. Never installs."""
    u = PROFILES["engines"][engine].get("update")
    if not u:
        return ""
    try:
        kind, *rest = u["have"].split()
        if kind == "pin":  # version pinned in our launcher: known even while the engine is down
            # GUFO_VERSION=0.12.0 / IMAGE_TAG="${HALOGEN_IMAGE_TAG:-0.18.14}"
            have = re.search(rf"{rest[1]}(?:=|:-)([0-9.]+)", open(os.path.join(REPO, rest[0])).read()).group(1)
        elif kind == "dir":  # a git checkout our launcher starts (Strata): its tag, known while the engine is down
            src = subprocess.run(["bash", os.path.join(REPO, rest[0]), "--print-dir"], capture_output=True, text=True).stdout.strip()
            have = subprocess.run(["git", "-C", src, "describe", "--tags"], capture_output=True, text=True).stdout.strip()
        else:  # what the running server reports
            have = json.load(http(f"http://127.0.0.1:{PROFILES['engines'][engine]['port']}{kind}"))[rest[0]]
        src, repo = u["latest"].split(":", 1)
        if src == "gh":
            new = json.load(http(f"https://api.github.com/repos/{repo}/releases/latest"))["tag_name"]
        else:  # ghcr: highest x.y.z tag
            tok = json.load(http(f"https://ghcr.io/token?scope=repository:{repo}:pull"))["token"]
            req = urllib.request.Request(f"https://ghcr.io/v2/{repo}/tags/list?n=1000", headers={"Authorization": f"Bearer {tok}"})
            tags = [t for t in json.load(urllib.request.urlopen(req, timeout=3))["tags"] if re.fullmatch(r"\d+\.\d+\.\d+", t)]
            new = max(tags, key=_ver)
    except Exception:
        return ""
    a, b = _ver(have), _ver(new)
    if not a or not b or a[0] != b[0] or b[1] <= a[1]:  # different schemes (llama.cpp b1234 vs v0.7.0): can't tell, stay quiet
        return ""
    return f"{engine} {new} available (running {str(have).split()[-1]}): {u['how'].format(new=new.lstrip('v'))}"


def upgrader(eng):
    """scripts/upgrade-<engine>.sh (halogen, gufo): pull, restart, check, pin, roll back on failure. -> path or None."""
    p = os.path.join(REPO, "scripts", f"upgrade-{eng}.sh")
    return p if os.path.exists(p) else None


def update_offer(eng):
    """Ask once whether to check for engine updates, then check eng. -> True if the user wants the safe
    upgrade (engines with an upgrader; run later, after all questions); others get a notice with the command."""
    pref = update_pref()
    if not pref and sys.stdin.isatty():
        pref = "1" if ask_yn("Check for engine updates when you run setup/start? (one short network call; never installs without asking)") else "0"
        os.makedirs(AGENT, exist_ok=True)
        open(UPDATE_PREF, "w").write(pref + "\n")
    if pref != "1" or not sys.stdin.isatty():
        return False
    msg = engine_update(eng)
    if msg:
        notice(msg)
        return bool(upgrader(eng)) and ask_yn(f"Upgrade {eng}? (restarts the server, rolls back on failure)")
    return False


def monitor_cmd(eng, url=None, once=False):
    """argv of the live monitor for an engine, or None. Strata: its own `strata monitor` (Textual, optional: Strata's
    venv has it when its setup installed the monitor requirements; --once needs none), else our scripts/halogen-top.sh
    --strata. halogen: halogen-top.sh. Other engines have none."""
    top = os.path.join(REPO, "scripts", "halogen-top.sh")
    if eng == "halogen":
        return ["bash", top, *([url] if url else [])]
    if eng != "strata":
        return None
    d = subprocess.run(["bash", os.path.join(REPO, "server", "start-strata.sh"), "--print-dir"], capture_output=True, text=True).stdout.strip()
    py = os.path.join(d, ".venv", "bin", "python")
    if d and os.path.isfile(os.path.join(d, "strata.py")) and os.access(py, os.X_OK) and (
            once or subprocess.run([py, "-c", "import textual"], capture_output=True).returncode == 0):
        return [py, os.path.join(d, "strata.py"), "monitor", "--url", url or f"http://127.0.0.1:{PROFILES['engines']['strata']['port']}", *(["--once"] if once else [])]
    return ["bash", top, "--strata", *([url] if url else [])]


def cmd_monitor(a):
    eng = a.engine or (active() or {}).get("engine") or next((x["engine"] for x in detect()["engines"]), "halogen")
    argv = monitor_cmd(eng, a.url, a.once)
    if not argv:
        sys.exit(f"no live monitor for {eng} (halogen and Strata have one)")
    os.execvp(argv[0], argv)


def cmd_up(_):
    """start.sh: bring the saved engine up (start it if we ship a launcher; ask before stopping another engine
    that holds the GPU memory), refresh the install quietly, then the update notice + upgrade offer."""
    eng = (active() or {}).get("engine", "halogen")
    running = {x["engine"]: x for x in detect()["engines"]}
    if eng not in running:
        plan = plan_start(eng, running, ask_start=False)
        if plan is None or not start_engine(eng, running, plan):
            print(f"! {not_answering(eng)}; pi opens anyway", file=sys.stderr)
    if not install_extensions(eng, verbose=False, show_ok=False):
        print(f"! setup.sh reported problems (details: {SETUP_LOG})", file=sys.stderr)
    if update_pref() == "1":
        msg = engine_update(eng)
        if msg:
            notice(msg)
            if upgrader(eng) and ask_yn(f"Upgrade {eng} now? (restarts the server, rolls back on failure)"):
                if subprocess.run(["bash", upgrader(eng)]).returncode:
                    print("! upgrade failed; continuing on the pinned version", file=sys.stderr)


def cmd_update_check(a):
    """start.sh: one line if the active (or named) engine has a newer release; silent when off/offline."""
    if update_pref() == "1":
        msg = engine_update(a.engine or (active() or {}).get("engine", "halogen"))
        if msg:
            notice(msg)


def notice(msg):
    """Update notices in bold yellow on a terminal, plain when piped (start.sh colours its own copy)."""
    print(f"\033[1;33m{msg}\033[0m" if sys.stdout.isatty() else msg)


def choices_line():
    """One line with every saved answer, for summaries and the config menu."""
    return (f"weights {weights_pref()} · sidecar {'off' if sidecar_pref() == '0' else 'on'} · voice {'on' if jget(EXTRAS, {}).get('voice') else 'off'}"
            f" · update check {'on' if update_pref() == '1' else 'off'}")


SETUP_LOG = os.path.join(AGENT, "setup.log")


def launch_offer(eng, prov, mid):
    """End of the wizard: start pi right here instead of printing a command to copy."""
    if not (sys.stdin.isatty() and shutil.which("pi")):
        return
    cwd = os.getcwd()
    if cwd == REPO:
        print("  (you are in the harness checkout; start it on a project with: ./start.sh ~/your-project)")
        return
    if ask_yn(f"Start pi in {cwd} now?", True):
        os.environ["PONYTAIL_HIDE_STATUS"] = "1"  # same as start.sh
        os.execvp("pi", ["pi", "--provider", prov, "--model", mid])


def finish(eng, mod, no_tune, launch=False):
    tuned = jget(os.path.join(AGENT, "harness-tuned.json"), {}).get(hw_key(eng, mod), {}) if no_tune else {}
    if not no_tune:
        print(f"== probing {eng}/{mod} (prefill + decode, <2 min) ==")
        tuned = tune(eng, mod)
        print("  ", {k: v for k, v in tuned.items() if k in ("ctx", "prefill_tps", "decode_tps", "error")})
        if "error" in tuned:
            print("  probe failed - keeping shipped defaults")
            tuned = {}
    prov, mid, ctx = apply(eng, mod, tuned)
    speed = f"{tuned['prefill_tps']} t/s prefill, {tuned['decode_tps']} t/s decode" if tuned.get("decode_tps") else "not probed (./harness tune)"
    if no_tune and tuned.get("decode_tps"):
        speed += f" (saved {tuned.get('when', '')}; re-measure: ./harness tune)"
    print(f"\n== ready ==\n  engine   {eng} -> {prov}/{mid}\n  context  {ctx}\n  speed    {speed}")
    print("  start    ./start.sh ~/your-project")
    if eng in ("halogen", "strata"):
        print("  monitor  ./harness monitor   (live speed, memory and requests; Ctrl-C to quit)")
    print(f"  choices  {choices_line()}   (change: ./harness config)")
    print("  check    ./setup.sh --doctor   (status: ./harness status)")
    if launch:
        launch_offer(eng, prov, mid)


def cmd_setup(a):
    d = detect()
    h = d["hardware"]
    verbose = a.verbose or active() is None  # first run shows everything; later runs only what needs you
    print(f"hardware: {h['ram_gb']} GB RAM, gpu={h['gpu']}, npu={h['npu']}; engines up: {[x['engine'] for x in d['engines']] or 'none'}")
    cap = capacity()
    print("\n".join(describe(recommend(h, cap), cap)[1:]))
    if not d["pi"]:
        print("! pi not found on PATH - install it first")
    # 1. every question first ...
    if not a.yes:
        harness_update_offer()
    up = []  # the update check runs as soon as the engine is chosen, before "Start <engine>?"
    eng, plan = pick_engine(d, a.yes, on_pick=None if a.yes else lambda e: up.append(update_offer(e)) or up[0])
    upgrade = bool(up and up[0])
    if not a.yes:
        ask_sidecar(a.reconfigure)
    voice = not a.yes and ask_once("voice", "Set up voice (local STT/TTS via Lemonade)?", a.reconfigure)
    if not a.yes and sys.stdin.isatty():
        steps = [f"install/update pi extensions, providers, skills{'' if verbose else f' (details: {SETUP_LOG})'}",
                 choices_line()] + (
            [f"stop {plan['stop']} and the ling-tiny sidecar"] if plan and plan["stop"] else []) + (
            ["download halogen (~124 GB, resumable)"] if plan and plan["download"] else []) + (
            [f"install Strata for the {weights_pref()} weights (" + ("orca-uncensored in place" if weights_pref() == "abliterated" else "shared stock UD-Q4_K_XL in place") + ")"] if plan and plan.get("install") == "strata" else []) + (
            [f"download the stock {plan['stock']} (~{quant_gb(plan['stock'])} GB, resumable)"] if plan and plan.get("stock") else []) + (
            [f"download the abliterated {eng} weights (~{plan.get('weights_gb')} GB, resumable)"] if plan and plan.get("weights") else []) + (
            [f"upgrade {eng} (restart, rolls back on failure)"] if upgrade else []) + (
            [f"start {eng} (model asked once it answers, if it serves several)"] if plan else [f"use {eng} (running)"]) + (
            [] if a.no_tune else ["probe speed (<2 min; skipped if already measured for this model, unless upgraded)"])
        print("\nabout to:\n" + "".join(f"  - {s}\n" for s in steps))
        if not ask_yn("Continue?", True):
            sys.exit("nothing changed")
    # 2. ... then run without stopping: install, free memory/download, upgrade, and start the engine last
    if not install_extensions(eng, ["--voice"] if voice else [], verbose, launch=False):
        print(f"! setup.sh reported problems (see above{'' if verbose else ' and ' + SETUP_LOG})")
    added = serena_languages()
    if added:
        print(f"serena: enabled language servers {added} in .serena/project.yml (restart serena to load them)")
    if plan and not prepare_start(plan, {x["engine"]: x for x in d["engines"]}):
        sys.exit(not_answering(eng))
    if upgrade:  # a gufo upgrade needs halogen already stopped; the upgraders start/restart the server themselves
        print(f"\n== upgrading {eng} ==")
        if subprocess.run(["bash", upgrader(eng)]).returncode:
            print("! upgrade failed; continuing on the pinned version")
        d["engines"] = detect()["engines"]
        if plan and served_models(PROFILES["engines"][eng]["port"], eng):
            plan = None  # the upgrade left it running: nothing to start
    eng, mod = bring_up(d, eng, plan, a.yes)
    saved = jget(os.path.join(AGENT, "harness-tuned.json"), {}).get(hw_key(eng, mod), {}).get("decode_tps")
    finish(eng, mod, a.no_tune or (bool(saved) and not upgrade), launch=not a.yes)


def cmd_use(a):
    eng, mod = pick_target(detect(), True, a.target)
    install_extensions(eng)
    finish(eng, mod, a.no_tune or bool(jget(os.path.join(AGENT, "harness-tuned.json"), {}).get(hw_key(eng, mod))))


def cmd_tune(_):
    p = active() or sys.exit("no active profile: run ./harness setup")
    finish(p["engine"], p["model"], False)


def cmd_status(_):
    p = active()
    if not p:
        return print("not configured: run ./harness setup")
    port = PROFILES["engines"][p["engine"]]["port"]
    up = bool(served_models(port, p["engine"]))
    print(f"engine {p['engine']} :{port} {'UP' if up else 'DOWN'}  model {p['model']}  npu {p['npu']}")
    t = jget(os.path.join(AGENT, "harness-tuned.json"), {}).get(hw_key(p["engine"], p["model"]))
    print("tuning:", t or "none yet (./harness tune)")
    if sidecar_pref() == "0":
        return print("sidecar: off (opted out; ./harness config turns it on)")
    try:
        http(p["sidecarUrl"] + "/health")
        print("sidecar: UP")
    except Exception:
        print("sidecar: down (starts on demand via ling-tiny.service)")


def cmd_config(a):
    """Menu of every saved answer; change one, applied right away."""
    if not sys.stdin.isatty():
        return print(choices_line())
    hints = os.path.join(AGENT, "harness-hints-shown")
    while True:
        p = active() or {}
        upd = update_pref()
        sc = sidecar_pref() != "0"
        voice = bool(jget(EXTRAS, {}).get("voice"))
        items = {
            f"engine        {p.get('engine', '-')}/{p.get('model', '-')}": "engine",
            f"weights       {weights_pref()}  (default for halogen and gufo)": "weights",
            f"sidecar       {'on' if sc else 'off'}  (ling-tiny, ~8 GiB GPU memory)": "sidecar",
            f"voice         {'on' if voice else 'off'}  (Lemonade STT/TTS)": "voice",
            f"update check  {'on' if upd == '1' else 'off'}  (newer engine release, never installs without asking)": "update",
            f"sudo hints    {'hidden after first show' if os.path.exists(hints) else 'shown'}  (fabric clock, vm-compact)": "hints",
            "done": "done"}
        k = items[choose("Change which setting?", list(items), False)]
        if k == "done":
            return
        if k == "engine":
            eng, mod = pick_target(detect(), False)
            install_extensions(eng, verbose=False)
            finish(eng, mod, bool(jget(os.path.join(AGENT, "harness-tuned.json"), {}).get(hw_key(eng, mod))))
        elif k == "weights":
            eng = p.get("engine", "halogen")
            if not ask_weights(eng if eng in WEIGHTS_ASK else "halogen") or eng not in WEIGHTS_ASK:
                continue
            running = {x["engine"]: x for x in detect()["engines"]}
            if ask_yn(f"Restart {eng} on the {weights_pref()} weights now?", True):
                if eng in running and not stop_engine(eng, running):
                    continue
                running.pop(eng, None)
                start_engine(eng, running)
        elif k == "sidecar":
            open(os.path.join(AGENT, "harness-sidecar"), "w").write("0\n" if sc else "1\n")
            r = subprocess.run(["systemctl", "--user", "stop" if sc else "start", "ling-tiny.service"], capture_output=True)
            print(f"  sidecar {'off, stopped' if sc else 'on, started'}" if not r.returncode else
                  f"  saved; {'stop' if sc else 'start'} failed - run ./setup.sh to {'stop' if sc else 'install'} it")
        elif k == "voice":
            st = jget(EXTRAS, {})
            st["voice"] = not voice
            jput(EXTRAS, st)
            if voice:
                print("  voice off (nothing uninstalled; /speak and /talk stay available)")
            else:
                install_extensions((active() or {}).get("engine", "halogen"), ["--voice"], verbose=False)
        elif k == "update":
            os.makedirs(AGENT, exist_ok=True)
            open(UPDATE_PREF, "w").write("0\n" if upd == "1" else "1\n")
        elif k == "hints" and os.path.exists(hints):
            os.remove(hints)
            print("  sudo hints will show on the next ./setup.sh run")


def main():
    import argparse
    ap = argparse.ArgumentParser(prog="harness")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for n, f in (("detect", cmd_detect), ("setup", cmd_setup), ("use", cmd_use), ("tune", cmd_tune), ("status", cmd_status), ("config", cmd_config), ("update-check", cmd_update_check), ("up", cmd_up), ("recommend", cmd_recommend), ("quant-info", cmd_quant_info), ("monitor", cmd_monitor)):
        p = sub.add_parser(n)
        p.set_defaults(fn=f)
        if n in ("setup", "use"):
            p.add_argument("--yes", action="store_true")
            p.add_argument("--no-tune", action="store_true")
            p.add_argument("--reconfigure", action="store_true", help="ask the saved extras again (one at a time: ./harness config)")
            p.add_argument("--verbose", action="store_true", help="show full setup.sh output (default on the first run only)")
        if n == "use":
            p.add_argument("target", help="engine[/model]")
        if n == "recommend":
            p.add_argument("--json", action="store_true")
        if n == "monitor":
            p.add_argument("engine", nargs="?", help="default: the active engine (halogen or strata)")
            p.add_argument("--url", help="server root, e.g. a remote Strata (default: this box)")
            p.add_argument("--once", action="store_true", help="Strata only: print one snapshot and exit")
        if n == "quant-info":
            p.add_argument("name")
        if n == "update-check":
            p.add_argument("engine", nargs="?", help="default: the active engine")
    a = ap.parse_args()
    try:
        a.fn(a)
    except (KeyboardInterrupt, EOFError):
        cancel()


if __name__ == "__main__":
    main()
