#!/usr/bin/env python3
"""harness - detect the local engine/model, apply optimized pi defaults, switch later.

  ./harness setup [--yes] [--no-tune]   wizard: detect -> ask only if ambiguous -> apply -> tune -> verify
  ./harness use <engine>[/<model>]      switch the active engine/model (no restart of servers you started)
  ./harness tune                        re-run the 1-2 min probe for the active profile
  ./harness status                      what is active, is it answering, last tuning
  ./harness update-check [engine]       one line if the active engine has a newer release (when the check is on)
  ./harness config                      change one saved answer (sidecar, voice, update check, engine, sudo hints)
  ./harness detect                      print what was found (json)

Profiles live in config/profiles.json. Stdlib only. Never kills or restarts servers it did not launch.
"""
import json, os, re, shutil, subprocess, sys, time, urllib.request, uuid, hashlib, glob

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT = os.environ.get("PI_CODING_AGENT_DIR") or os.path.join(os.environ.get("PI_HOME", os.path.expanduser("~/.pi")), "agent")
PROFILES = json.load(open(os.path.join(REPO, "config", "profiles.json")))
MODEL_DIRS = ["~/models", "~/.cache/huggingface/hub", "/models", "~/llm"]
TUNE_BUDGET_S = 120


def jget(path, default=None):
    try:
        return json.load(open(path))
    except Exception:
        return default


def jput(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(obj, open(path, "w"), indent=2)
    open(path, "a").write("\n")


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
    s = jget(sj, {})
    s["defaultProvider"], s["defaultModel"] = prov, new["models"][0]["id"]
    reserve = tuned.get("reserveTokens") or min(model_profile(model_id)["reserveTokens"], ctx // 4)
    s.setdefault("compaction", {}).update({"enabled": True, "reserveTokens": reserve})
    jput(sj, s)
    jput(os.path.join(AGENT, "harness-profile.json"), {
        "engine": engine, "provider": prov, "model": new["models"][0]["id"], "npu": bool(e["npu"]),
        "npuUrl": "http://127.0.0.1:8731", "sidecarUrl": "http://127.0.0.1:8090"})
    return prov, new["models"][0]["id"], ctx


def install_extensions(engine, extra=(), verbose=True):
    """Run setup.sh without a terminal on stdin (the wizard already asked everything).
    verbose=False: full output to setup.log, one line per section on screen, warnings/failures in full."""
    args = (["--halogen", "auto"] if engine == "halogen" else ["--no-halogen"]) + list(extra)
    cmd = ["bash", os.path.join(REPO, "setup.sh"), *args]
    if verbose:
        return subprocess.run(cmd, stdin=subprocess.DEVNULL).returncode == 0
    os.makedirs(AGENT, exist_ok=True)
    p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True)  # stderr stays on screen (download progress)
    sec, issues = None, []

    def close():
        if sec:
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
    return hashlib.sha1(f"{engine}|{model_id}|{h['gpu']}|{h['ram_gb']}".encode()).hexdigest()[:12]


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


def cmd_detect(_):
    print(json.dumps(detect(), indent=1))


def last_speed(engine):
    t = [v for v in jget(os.path.join(AGENT, "harness-tuned.json"), {}).values() if v.get("engine") == engine and v.get("decode_tps")]
    t.sort(key=lambda v: v.get("when", ""))
    return f"{t[-1]['prefill_tps']}/{t[-1]['decode_tps']} t/s pf/dec" if t else "not measured"


BIG = ("halogen", "gufo", "strata")  # big engines: one GPU memory heap, and gufo/strata also share :8080


def heap_holder(running):
    """The big engines share one GPU memory heap: whichever is up blocks the others."""
    return next((e for e in BIG if e in running), None)


def engine_menu(running):
    """One screen: state, model, last speed, what you lose, memory conflicts. -> engine name."""
    cur = (active() or {}).get("engine")
    npu = hardware()["npu"]  # fresh box: halogen first where it can run, then engines we can start for you
    order = sorted(PROFILES["engines"], key=lambda e: (e != cur, e not in running, not (npu and e == "halogen"),
                                                       "script" not in PROFILES["engines"][e], e))
    holder = heap_holder(running)
    labels = {}
    for e in order:
        c = PROFILES["engines"][e]
        mods = running[e]["models"] if e in running else []
        state = f"running: {mods[0] if mods else '-'}" if e in running else "not running"
        lose = f"loses: {c.get('loses', '?')}"
        warn = f" | ! stops {holder} first (shared GPU memory)" if e in BIG and holder and holder != e and e not in running else ""
        labels[f"{'*' if e == cur else ' '}{'o' if e in running else '-'} {e:9s} {state} | {last_speed(e)} | {lose}{warn}"] = e
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
    return subprocess.run(["bash", os.path.join(REPO, "server", "start-halogen.sh"), "--print-ckpt"], capture_output=True, text=True).stdout.strip()


def plan_start(engine, running):
    """Ask everything starting engine needs (stop the memory holder, download, start); nothing runs yet.
    -> plan dict, or None when there is no launcher or the user declined."""
    c = PROFILES["engines"][engine]
    if not c.get("script"):
        print(f"{engine}: {c.get('start', 'start it yourself')}")
        return None
    plan = {"stop": None, "download": False}
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
    if not ask_yn(f"Start {engine}? (runs {c['script']} in the background, log /tmp/harness-{engine}.log)", True):
        return None
    return plan


def start_engine(engine, running, plan=None, wait_s=900):
    """Start an engine we ship a launcher for (halogen, gufo); asks first unless given a plan. -> True once it answers."""
    plan = plan or plan_start(engine, running)
    if plan is None:
        return False
    c = PROFILES["engines"][engine]
    if plan["stop"] and not stop_engine(plan["stop"], running):
        return False
    if plan["download"] and subprocess.run(["bash", os.path.join(REPO, "scripts", "download-halogen.sh"), "--yes"]).returncode:
        return False
    log = open(f"/tmp/harness-{engine}.log", "w")
    subprocess.Popen(["bash", os.path.join(REPO, c["script"])], stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
    t0 = time.time()
    try:
        while time.time() - t0 < wait_s:
            if served_models(c["port"], engine):
                print(f"\n  {engine} is up")
                return True
            print(f"\r  waiting for {engine} on :{c['port']} ... {int(time.time() - t0)}s (Ctrl-C to stop waiting)", end="", flush=True)
            time.sleep(3)
    except KeyboardInterrupt:
        pass
    print(f"\n  {engine} did not answer in time; see /tmp/harness-{engine}.log")
    return False


def not_answering(eng):
    e = PROFILES["engines"][eng]
    return f"{eng} is not answering on :{e['port']} - {e.get('start', 'start it first')}"


def pick_engine(d, yes, want=None):
    """Questions only: -> (engine, start plan or None if it is already up). Nothing starts here."""
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
        print("No engine is running yet. Pick one: halogen, gufo and Strata can be started (and halogen downloaded) from here.")
    eng = engine_menu(running)
    if eng in running:
        return eng, None
    return eng, plan_start(eng, running) or sys.exit(not_answering(eng))


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
    """'1' / '0' / '' (never asked); HALOGEN_CHECK_UPDATES overrides, as in check-halogen-update.sh."""
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
    if engine == "halogen":  # its own check (pin in start-halogen.sh); the upgrade itself is scripts/upgrade-halogen.sh
        return subprocess.run(["bash", os.path.join(REPO, "scripts", "check-halogen-update.sh")], capture_output=True,
                              text=True, env={**os.environ, "HALOGEN_CHECK_UPDATES": "1"}).stdout.strip()
    u = PROFILES["engines"][engine].get("update")
    if not u:
        return ""
    try:
        kind, *rest = u["have"].split()
        if kind == "pin":  # version pinned in our launcher: known even while the engine is down
            have = re.search(rf"^{rest[1]}=([0-9.]+)", open(os.path.join(REPO, rest[0])).read(), re.M).group(1)
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
    return (f"sidecar {'off' if sidecar_pref() == '0' else 'on'} · voice {'on' if jget(EXTRAS, {}).get('voice') else 'off'}"
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
    print(f"\n== ready ==\n  engine   {eng} -> {prov}/{mid}\n  context  {ctx}\n  speed    {speed}")
    print("  start    ./start.sh ~/your-project")
    print(f"  choices  {choices_line()}   (change: ./harness config)")
    print("  check    ./setup.sh --doctor   (status: ./harness status)")
    if launch:
        launch_offer(eng, prov, mid)


def cmd_setup(a):
    d = detect()
    h = d["hardware"]
    verbose = a.verbose or active() is None  # first run shows everything; later runs only what needs you
    print(f"hardware: {h['ram_gb']} GB RAM, gpu={h['gpu']}, npu={h['npu']}; engines up: {[x['engine'] for x in d['engines']] or 'none'}")
    if not d["pi"]:
        print("! pi not found on PATH - install it first")
    # 1. every question first ...
    if not a.yes:
        harness_update_offer()
    eng, plan = pick_engine(d, a.yes)
    upgrade = not a.yes and update_offer(eng)
    if not a.yes:
        ask_sidecar(a.reconfigure)
    voice = not a.yes and ask_once("voice", "Set up voice (local STT/TTS via Lemonade)?", a.reconfigure)
    if not a.yes and sys.stdin.isatty():
        steps = ([f"upgrade {eng} (restart, rolls back on failure)"] if upgrade else []) + (
            [f"stop {plan['stop']} and the ling-tiny sidecar"] if plan and plan["stop"] else []) + (
            ["download halogen (~124 GB, resumable)"] if plan and plan["download"] else []) + (
            [f"start {eng} (model asked once it answers, if it serves several)"] if plan else [f"use {eng} (running)"]) + [
            f"install/update pi extensions, providers, skills{'' if verbose else f' (details: {SETUP_LOG})'}",
            choices_line()] + ([] if a.no_tune else ["probe speed (<2 min)"])
        print("\nabout to:\n" + "".join(f"  - {s}\n" for s in steps))
        if not ask_yn("Continue?", True):
            sys.exit("nothing changed")
    # 2. ... then run without stopping
    eng, mod = bring_up(d, eng, plan, a.yes)
    # after bring_up: a gufo upgrade needs halogen already stopped, and the upgraders restart a running server
    if upgrade and subprocess.run(["bash", upgrader(eng)]).returncode:
        print("! upgrade failed; continuing on the pinned version")
    if not install_extensions(eng, ["--voice"] if voice else [], verbose):
        print(f"! setup.sh reported problems (see above{'' if verbose else ' and ' + SETUP_LOG})")
    added = serena_languages()
    if added:
        print(f"serena: enabled language servers {added} in .serena/project.yml (restart serena to load them)")
    finish(eng, mod, a.no_tune, launch=not a.yes)


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
    for n, f in (("detect", cmd_detect), ("setup", cmd_setup), ("use", cmd_use), ("tune", cmd_tune), ("status", cmd_status), ("config", cmd_config), ("update-check", cmd_update_check)):
        p = sub.add_parser(n)
        p.set_defaults(fn=f)
        if n in ("setup", "use"):
            p.add_argument("--yes", action="store_true")
            p.add_argument("--no-tune", action="store_true")
            p.add_argument("--reconfigure", action="store_true", help="ask the saved extras again (one at a time: ./harness config)")
            p.add_argument("--verbose", action="store_true", help="show full setup.sh output (default on the first run only)")
        if n == "use":
            p.add_argument("target", help="engine[/model]")
        if n == "update-check":
            p.add_argument("engine", nargs="?", help="default: the active engine")
    a = ap.parse_args()
    try:
        a.fn(a)
    except (KeyboardInterrupt, EOFError):
        cancel()


if __name__ == "__main__":
    main()
