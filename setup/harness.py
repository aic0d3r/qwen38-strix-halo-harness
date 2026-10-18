#!/usr/bin/env python3
"""harness - detect the local engine/model, apply optimized pi defaults, switch later.

  ./harness setup [--yes] [--no-tune]   wizard: detect -> ask only if ambiguous -> apply -> tune -> verify
  ./harness use <engine>[/<model>]      switch the active engine/model (no restart of servers you started)
  ./harness tune                        re-run the 1-2 min probe for the active profile
  ./harness status                      what is active, is it answering, last tuning
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
    if name == "strata":
        return not identify("llamacpp", port)
    return True


def detect():
    found = []
    for name, e in PROFILES["engines"].items():
        if e.get("autodetect") is False:
            continue
        try:
            http(f"http://127.0.0.1:{e['port']}{e['probe']}")
        except Exception:
            continue
        if identify(name, e["port"]):
            found.append({"engine": name, "port": e["port"], "models": served_models(e["port"], name)})
    if (active() or {}).get("engine") == "gufo":  # gufo shares :8080, so only the saved choice can name it
        for x in found:
            if x["port"] == PROFILES["engines"]["gufo"]["port"]:
                x["engine"] = "gufo"
    ggufs = []
    for d in MODEL_DIRS:
        ggufs += glob.glob(os.path.join(os.path.expanduser(d), "**", "*.gguf"), recursive=True)[:20]
    return {"hardware": hardware(), "engines": found, "gguf": ggufs[:20], "docker": bool(shutil.which("docker")), "pi": bool(shutil.which("pi"))}


# ---------- ask ----------
def choose(question, options, yes):
    if len(options) == 1 or yes or not sys.stdin.isatty():
        return options[0]
    if shutil.which("gum"):
        r = subprocess.run(["gum", "choose", "--header", question, *options], stdout=subprocess.PIPE, text=True)
        if r.stdout.strip():
            return r.stdout.strip()
    print(question)
    for i, o in enumerate(options, 1):
        print(f"  {i}) {o}")
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
    for path, pick in (("/props", lambda d: d["default_generation_settings"]["n_ctx"]), ("/v1/models", lambda d: d["data"][0]["meta"]["n_ctx"])):
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


def install_extensions(engine, extra=()):
    args = (["--halogen", "auto"] if engine == "halogen" else ["--no-halogen"]) + list(extra)
    return subprocess.run(["bash", os.path.join(REPO, "setup.sh"), *args]).returncode == 0


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


def heap_holder(running):
    """halogen and gufo share one GPU memory heap: whichever is up blocks the other."""
    return next((e for e in ("halogen", "gufo") if e in running), None)


def engine_menu(running):
    """One screen: state, model, last speed, what you lose, memory conflicts. -> engine name."""
    cur = (active() or {}).get("engine")
    order = sorted(PROFILES["engines"], key=lambda e: (e != cur, e not in running, e))
    holder = heap_holder(running)
    labels = {}
    for e in order:
        c = PROFILES["engines"][e]
        mods = running[e]["models"] if e in running else []
        state = f"running: {mods[0] if mods else '-'}" if e in running else "not running"
        lose = f"loses: {c.get('loses', '?')}"
        warn = f" | ! stops {holder} first (shared GPU memory)" if e in ("halogen", "gufo") and holder and holder != e and e not in running else ""
        labels[f"{'*' if e == cur else ' '}{'o' if e in running else '-'} {e:9s} {state} | {last_speed(e)} | {lose}{warn}"] = e
    return labels[choose("Which engine? (* = current, o = running)", list(labels), False)]


def stop_engine(engine, running):
    port = PROFILES["engines"][engine]["port"]
    cid = subprocess.run(["docker", "ps", "-q", "--filter", f"publish={port}"], capture_output=True, text=True).stdout.split()
    if not cid:
        print(f"  could not find a container for {engine} on :{port}; stop it yourself")
        return False
    subprocess.run(["docker", "stop", "-t", "30", *cid], stdout=subprocess.DEVNULL)
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


def start_engine(engine, running, wait_s=900):
    """Offer to start an engine we ship a launcher for (halogen, gufo). -> True once it answers."""
    c = PROFILES["engines"][engine]
    script = c.get("script")
    if not script:
        print(f"{engine}: {c.get('start', 'start it yourself')}")
        return False
    holder = heap_holder(running)
    if holder and holder != engine and engine in ("halogen", "gufo"):
        if not ask_yn(f"{holder} holds the GPU memory {engine} needs. Stop {holder} and the ling-tiny sidecar now?"):
            return False
        if not stop_engine(holder, running):
            return False
    if not ask_yn(f"Start {engine} now? (runs {script} in the background, log /tmp/harness-{engine}.log)"):
        return False
    log = open(f"/tmp/harness-{engine}.log", "w")
    subprocess.Popen(["bash", os.path.join(REPO, script)], stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
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


def pick_target(d, yes, want=None):
    running = {x["engine"]: x for x in d["engines"]}

    def models_of(eng):
        e = PROFILES["engines"][eng]
        if eng in running:
            return running[eng]["models"]
        for x in d["engines"]:  # strata / llama.cpp / gufo share :8080: whatever answers there is what you started
            if x["port"] == e["port"]:
                return x["models"]
        sys.exit(f"{eng} is not answering on :{e['port']} - {e.get('start', 'start it first')}")

    if want:
        eng, _, mod = want.partition("/")
        if eng not in PROFILES["engines"]:
            sys.exit(f"unknown engine {eng}; known: {', '.join(PROFILES['engines'])}")
        models = models_of(eng)
        return eng, mod or (models[0] if models else sys.exit("server lists no models"))
    if not running:
        sys.exit("no engine answering on the usual ports. Start one (see docs/ENGINES.md, or `./setup.sh --halogen auto`), then re-run.")
    if yes or not sys.stdin.isatty():  # unattended: only what is already up, halogen first
        eng = next(iter(sorted(running, key=lambda e: e != "halogen")))
    else:
        eng = engine_menu(running)
        if eng not in running:
            if not start_engine(eng, running):
                sys.exit(f"{eng} is not answering on :{PROFILES['engines'][eng]['port']} - {PROFILES['engines'][eng].get('start', 'start it first')}")
            d["engines"] = detect()["engines"]
            running = {x["engine"]: x for x in d["engines"]}
            if eng not in running:  # shares :8080 with strata / llama.cpp: take what answers there
                eng = next(x["engine"] for x in d["engines"] if x["port"] == PROFILES["engines"][eng]["port"])
    models = models_of(eng)
    if not models:
        sys.exit(f"{eng} lists no models")
    return eng, choose("Which model?", models, yes)


# ---------- extras (asked once, answers saved) ----------
EXTRAS = os.path.join(AGENT, "harness-extras.json")
# file extension -> serena language_servers name
LANGS = {".py": "python", ".ts": "typescript", ".tsx": "typescript", ".js": "typescript", ".jsx": "typescript",
         ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin", ".rb": "ruby", ".cs": "csharp",
         ".php": "php", ".swift": "swift", ".c": "cpp", ".cc": "cpp", ".cpp": "cpp", ".h": "cpp"}


def ask_yn(question):
    return input(f"{question} [y/N] ").strip().lower().startswith("y")


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
    a = input("Run the ling-tiny sidecar? Faster commits/compaction (~2x) but holds ~8 GiB of GPU memory.\n"
              "No = the main model writes them instead. [Y/n] ").strip().lower()
    os.makedirs(AGENT, exist_ok=True)
    open(os.path.join(AGENT, "harness-sidecar"), "w").write("0\n" if a.startswith("n") else "1\n")


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


def halogen_upgrade_offer(relevant):
    """Update check + safe-upgrade offer. Runs before engine selection: it needs no running engine.
    relevant: halogen is up or this box has an NPU, so asking about halogen makes sense."""
    path = os.path.join(AGENT, "harness-update-check")  # same answer file setup.sh and start.sh use
    try:
        pref = open(path).read().strip()
    except OSError:
        pref = ""
    if not pref and relevant and sys.stdin.isatty():
        pref = "1" if ask_yn("Check for halogen updates when you run setup/start? (one short network call; asked before anything installs)") else "0"
        os.makedirs(AGENT, exist_ok=True)
        open(path, "w").write(pref + "\n")
    if pref != "1" or not sys.stdin.isatty():
        return
    msg = subprocess.run(["bash", os.path.join(REPO, "scripts", "check-halogen-update.sh")], capture_output=True, text=True).stdout.strip()
    if msg:
        print(msg)
        if ask_yn("Upgrade halogen now? (restarts the server, rolls back on failure)"):
            subprocess.run(["bash", os.path.join(REPO, "scripts", "upgrade-halogen.sh")])


def finish(eng, mod, no_tune):
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
    print(f"  start    {'./start.sh ~/your-project' if eng == 'halogen' else f'pi --provider {prov} --model {mid}'}")
    print("  check    ./setup.sh --doctor   (status: ./harness status)")


def cmd_setup(a):
    d = detect()
    h = d["hardware"]
    print(f"hardware: {h['ram_gb']} GB RAM, gpu={h['gpu']}, npu={h['npu']}; engines up: {[x['engine'] for x in d['engines']] or 'none'}")
    if not d["pi"]:
        print("! pi not found on PATH - install it first")
    if not a.yes:
        harness_update_offer()
        halogen_upgrade_offer(h["npu"] or any(x["engine"] == "halogen" for x in d["engines"]))
    eng, mod = pick_target(d, a.yes)
    if not a.yes:
        ask_sidecar(a.reconfigure)
    voice = not a.yes and ask_once("voice", "Set up voice (local STT/TTS via Lemonade)?", a.reconfigure)
    if not install_extensions(eng, ["--voice"] if voice else []):
        print("! setup.sh reported problems (see above)")
    added = serena_languages()
    if added:
        print(f"serena: enabled language servers {added} in .serena/project.yml (restart serena to load them)")
    finish(eng, mod, a.no_tune)


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
    try:
        http(p["sidecarUrl"] + "/health")
        print("sidecar: UP")
    except Exception:
        print("sidecar: down (starts on demand via ling-tiny.service)")


def main():
    import argparse
    ap = argparse.ArgumentParser(prog="harness")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for n, f in (("detect", cmd_detect), ("setup", cmd_setup), ("use", cmd_use), ("tune", cmd_tune), ("status", cmd_status)):
        p = sub.add_parser(n)
        p.set_defaults(fn=f)
        if n in ("setup", "use"):
            p.add_argument("--yes", action="store_true")
            p.add_argument("--no-tune", action="store_true")
            p.add_argument("--reconfigure", action="store_true", help="ask the saved extras again")
        if n == "use":
            p.add_argument("target", help="engine[/model]")
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
