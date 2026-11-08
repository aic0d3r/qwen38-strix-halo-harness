"""python3 tests/test_harness.py - wizard core against a mock OpenAI server (no real engine needed)."""
import json, os, re, subprocess, sys, tempfile, threading, http.server

AGENT = tempfile.mkdtemp()
os.environ["PI_CODING_AGENT_DIR"] = AGENT
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "setup"))
import harness as h


class Mock(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _j(self, o):
        b = json.dumps(o).encode(); self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.path == "/v1/models": self._j({"data": [{"id": "Qwen3-8B", **({"owned_by": "gufo", "context_length": 65536} if GUFO else {} if STRATA else {"owned_by": "llamacpp"})}]})
        elif self.path == "/props" and GUFO: self.send_response(400); self.end_headers()
        elif self.path == "/props": self._j({"default_generation_settings": {"n_ctx": 32768}})
        else: self._j({"ok": 1})
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(200); self.end_headers()
        for c in ('{"choices":[{"delta":{"content":"x"}}]}', '{"choices":[],"usage":{"prompt_tokens":900}}', "[DONE]"):
            self.wfile.write(f"data: {c}\n\n".encode())


STRATA = GUFO = False
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Mock)
threading.Thread(target=srv.serve_forever, daemon=True).start()
h.PROFILES["engines"]["llamacpp"]["port"] = srv.server_port

assert h.model_profile("Qwen3-8B")["reasoning"] and h.model_profile("unknown-x")["maxTokens"] == 8192
assert h.server_ctx(srv.server_port) == 32768
d = h.detect()
assert [e["engine"] for e in d["engines"]].count("llamacpp") == 1, d["engines"]
assert h.tune("llamacpp", "Qwen3-8B")["prefill_tps"] > 0
prov, mid, ctx = h.apply("llamacpp", "Qwen3-8B", {"maxTokens": 4096})
m = json.load(open(f"{AGENT}/models.json"))["providers"]["llamacpp"]["models"][0]
assert (prov, mid, ctx) == ("llamacpp", "Qwen3-8B", 32768) and m["maxTokens"] == 4096 and "chatTemplateKwargs" in m["compat"]
assert json.load(open(f"{AGENT}/harness-profile.json"))["npu"] is False
assert "llamacpp-tiny" in json.load(open(f"{AGENT}/models.json"))["providers"]  # sidecar reachable by registry lookups
# Strata shares :8080 and also serves /props (seen on Strata 0.1.44) but no owned_by=llamacpp
STRATA = True
h.PROFILES["engines"]["strata"]["port"] = srv.server_port
up = [e["engine"] for e in h.detect()["engines"] if e["port"] == srv.server_port]  # real engines on this box may also be up
assert up == ["strata"], up
# gufo shares :8080: selectable by name once something answers there
h.PROFILES["engines"]["gufo"]["port"] = srv.server_port
assert h.pick_target(h.detect(), True, "gufo")[0] == "gufo"
# a real gufo (owned_by=gufo) is detected as gufo, not strata, and its context_length is read
GUFO = True
assert [e["engine"] for e in h.detect()["engines"] if e["port"] == srv.server_port] == ["gufo"]
assert h.server_ctx(srv.server_port) == 65536
# started gufo but detect names the port something else: keep the engine we started
d = {"engines": [{"engine": "strata", "port": srv.server_port, "models": ["m"]}]}
assert h.bring_up(d, "gufo", None, True) == ("gufo", "m")
GUFO = False
# halogen's NPU helper models are not offered as chat models
h.PROFILES["engines"]["vllm"]["hide"] = "^Qwen3"
assert h.served_models(srv.server_port, "vllm") == []
# serena: detect project languages and enable matching language servers (idempotent)
proj = tempfile.mkdtemp()
os.makedirs(f"{proj}/.serena")
open(f"{proj}/.serena/project.yml", "w").write('project_name: "x"\nlanguage_servers:\n- python\nencoding: "utf-8"\n')
for i in range(3):
    open(f"{proj}/a{i}.py", "w").write("x=1")
    open(f"{proj}/b{i}.ts", "w").write("let x=1")
open(f"{proj}/lone.go", "w").write("package x")  # one file: below the 3-file bar
assert h.serena_languages(proj) == ["typescript"] and h.serena_languages(proj) == []
assert "- typescript\nencoding" in open(f"{proj}/.serena/project.yml").read()
# extras answers: no terminal -> not asked, remembered answer used
assert h.ask_once("voice", "?", False) is False
# sidecar opt-out answer: not asked without a terminal; read back from the file setup.sh and the extensions share
assert h.sidecar_pref() == ""
h.ask_sidecar(False)
assert h.sidecar_pref() == ""
open(f"{AGENT}/harness-sidecar", "w").write("0\n")
assert h.sidecar_pref() == "0"
os.remove(f"{AGENT}/harness-sidecar")
# engine menu: running first, labels map back to engine names; engines without a launcher print how to start
run = {"strata": {"engine": "strata", "port": 1, "models": ["m"]}}
assert h.engine_menu(run) == "llamacpp"  # no tty: first entry, which is the saved current engine
run2 = dict(run)
assert h.heap_holder({"gufo": 1}) == "gufo" and h.heap_holder(run) == "strata" and h.heap_holder({"vllm": 1}) is None
assert h.start_engine("vllm", run) is False  # no launcher: prints how to start it
# quiet install: full setup.sh output goes to setup.log, the exit code still decides success
fake = tempfile.mkdtemp()
open(f"{fake}/setup.sh", "w").write('echo "== 1. a =="; printf "  \\033[32m✓\\033[0m fine\\n"; echo "== 2. b =="; echo "  ! careful"; exit 3\n')
real_repo, h.REPO = h.REPO, fake
assert h.install_extensions("llamacpp", verbose=False) is False
h.REPO = real_repo
assert "! careful" in open(h.SETUP_LOG).read()
# engine update check: versions compare across the formats engines report; different schemes never compare
assert h._ver("Strata 0.1.44.1") < h._ver("v0.1.45") and h._ver("b6312-abc") == ("b", (6312,)) and h._ver("") is None
assert h.engine_update("lmstudio") == ""  # no update source: silent
# status reports an opted-out sidecar as off, not down
open(f"{AGENT}/harness-sidecar", "w").write("0\n")
assert "sidecar off" in h.choices_line()
# weights: stock until answered; the launchers resolve the saved answer to the file they load; tuning is keyed per weights
assert h.weights_pref() == "stock" and h.ask_weights("halogen") is False and "weights stock" in h.choices_line()
k_stock = h.hw_key("gufo", "m")
open(h.WEIGHTS, "w").write("abliterated\n")
assert h.weights_file("gufo").endswith("orca-uncensored/Qwen3.8-Flash-Next-Uncensored-Gufo-00001-of-00005.gguf")
assert h.weights_file("halogen").endswith("/qwen38-flash-next-v2-abliterated.hgn")
assert h.hw_key("gufo", "m") != k_stock and h.hw_key("strata", "m") == h.hw_key("strata", "m")
os.remove(h.WEIGHTS)
assert h.hw_key("gufo", "m") == k_stock
# the refusal canary is what makes the weights answer measurable: its classifier must call
# an apology a refusal and an answer that merely says "can't" a normal reply
rc = subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "..", "bench", "refusal-canary.py"), "--self-test"], capture_output=True, text=True)
assert rc.returncode == 0 and "verdicts correct" in rc.stdout, rc.stdout + rc.stderr
os.remove(f"{AGENT}/harness-sidecar")
# setup order: update offered right after the engine pick (before any start question); then install (no launch),
# free memory, upgrade, start last; an upgrade forces a fresh probe even when a saved result exists
import argparse, types
calls, saved = [], {n: getattr(h, n) for n in ("pick_engine", "update_offer", "upgrader", "install_extensions", "prepare_start", "bring_up", "finish", "harness_update_offer", "ask_sidecar", "ask_once", "served_models")}
h.harness_update_offer = h.ask_sidecar = lambda *a: None
h.ask_once = lambda *a: False
h.pick_engine = lambda d, yes, want=None, on_pick=None: (on_pick("gufo"), calls.append("plan_start"), ("gufo", {"stop": "halogen", "download": False}))[2]
h.update_offer = lambda e: calls.append("update_offer") or True
h.upgrader = lambda e: "/bin/true"
h.install_extensions = lambda e, x, v, launch=True: calls.append(f"install launch={launch}") or True
h.prepare_start = lambda p, r: calls.append("prepare") or True
h.served_models = lambda *a: []
h.bring_up = lambda d, e, p, y: calls.append(f"start plan={p is not None}") or (e, "Qwen3-8B")
h.finish = lambda e, m, no_tune, launch=False: calls.append(f"probe={not no_tune}")
real_cap, h.capacity = h.capacity, lambda: {"gpu_name": "t", "vram_gb": 0, "fast_gb": 8, "disk_free_gb": 500}  # keeps lspci out of the recorded calls
real_run, h.subprocess.run = h.subprocess.run, lambda *a, **k: calls.append("upgrade" if a[0][0] == "bash" else " ".join(a[0][:3])) or types.SimpleNamespace(returncode=0)
h.cmd_setup(argparse.Namespace(yes=False, verbose=False, reconfigure=False, no_tune=False))
h.subprocess.run = real_run
h.capacity = real_cap
for n, f in saved.items(): setattr(h, n, f)
assert calls == ["update_offer", "plan_start", "install launch=False", "prepare", "upgrade", "start plan=True", "probe=True"], calls
# upgrade chosen: pick_engine must not ask "Start <engine>?" (declining it used to exit setup); without one it does
asked, saved = [], {n: getattr(h, n) for n in ("engine_menu", "plan_start", "ask_weights", "ask_yn")}
h.engine_menu = lambda r: "halogen"
h.ask_weights = lambda e: False
h.plan_start = lambda e, r, ask_start=True: asked.append(ask_start) or {"stop": None, "download": False}
real_stdin, h.sys.stdin = h.sys.stdin, types.SimpleNamespace(isatty=lambda: True)
for want in (True, False):
    h.pick_engine({"engines": []}, False, on_pick=lambda e: want)
# new weights default while halogen runs: restart = a start plan that stops halogen first; declined = keep it running
h.ask_weights, h.ask_yn = (lambda e: True), (lambda q, d=False: True)
hal = {"engines": [{"engine": "halogen", "port": 8731, "models": ["m"]}]}
assert h.pick_engine(hal, False)[1]["stop"] == "halogen" and asked[-1] is False
h.ask_yn = lambda q, d=False: False
assert h.pick_engine(hal, False) == ("halogen", None)
asked.pop()
h.sys.stdin = real_stdin
for n, f in saved.items(): setattr(h, n, f)
assert asked == [False, True], asked

# gufo abliterated: weights present but no shared MTP head -> the sidecar download is still owed
root = tempfile.mkdtemp(); os.makedirs(f"{root}/orca"); open(f"{root}/orca/w.gguf", "w").close()
real_wf, h.weights_file = h.weights_file, lambda e: f"{root}/orca/w.gguf"
assert h.weights_download_gb("gufo") == 5 and h.weights_download_gb("halogen") == 0
open(f"{root}/mtp-Qwen3.8-Flash-Next-shared-Q8_0.gguf", "w").close()
assert h.weights_download_gb("gufo") == 0
h.weights_file = lambda e: f"{root}/none.gguf"
assert h.weights_download_gb("gufo") == 112
h.weights_file = real_wf
# strata missing: plan_start asks to install it, prepare_start runs install-strata.sh before the start
saved = {n: getattr(h, n) for n in ("strata_installed", "ask_yn", "heap_holder")}
h.strata_installed, h.ask_yn, h.heap_holder = (lambda: False), (lambda q, d=False: True), (lambda r: None)
h.stock_ready = lambda *a: True  # the stock GGUF is on disk (this box's real state must not decide the test)
plan = h.plan_start("strata", {})
assert plan["install"] == "strata" and not plan.get("stock"), plan
ran, real_run = [], h.subprocess.run
h.subprocess.run = lambda a, **k: ran.append(os.path.basename(a[1])) or types.SimpleNamespace(returncode=0)
assert h.prepare_start(plan, {}) and ran == ["install-strata.sh"] and "install" not in plan
h.subprocess.run = real_run
# abliterated Strata runs gufo's shards: when they are missing the plan downloads them first (before the install), declining stops it
saved_w = {n: getattr(h, n) for n in ("weights_pref", "weights_file")}
h.weights_pref, h.weights_file = (lambda: "abliterated"), (lambda e: "/nonexistent/orca.gguf")
plan = h.plan_start("strata", {})
assert plan["install"] == "strata" and plan["weights"] == "gufo" and plan["weights_gb"] == 112, plan
ran = []
h.subprocess.run = lambda a, **k: ran.append(os.path.basename(a[1])) or types.SimpleNamespace(returncode=0)
assert h.prepare_start(plan, {}) and ran == ["download-abliterated.sh", "install-strata.sh"], ran
h.subprocess.run = real_run
h.ask_yn = lambda q, d=False: "orca-uncensored" not in q
assert h.plan_start("strata", {}) is None
h.ask_yn = lambda q, d=False: True
for n, f in saved_w.items(): setattr(h, n, f)
# stock weights missing: gufo and Strata both download the one shared UD-Q4_K_XL first (before any Strata install); declining stops it
h.stock_ready = lambda *a: False
plan = h.plan_start("gufo", {})
assert plan["stock"] == "UD-Q4_K_XL" and "install" not in plan, plan
plan = h.plan_start("strata", {})
assert plan["stock"] == "UD-Q4_K_XL" and plan["install"] == "strata", plan
ran = []
h.subprocess.run = lambda a, **k: ran.append(os.path.basename(a[1])) or types.SimpleNamespace(returncode=0)
assert h.prepare_start(plan, {}) and ran == ["download-stock.sh", "install-strata.sh"], ran
h.subprocess.run = real_run
h.ask_yn = lambda q, d=False: "UD-Q4_K_XL" not in q
assert h.plan_start("gufo", {}) is None
h.ask_yn = lambda q, d=False: True
h.stock_ready = lambda *a: True
assert not h.plan_start("gufo", {}).get("stock")
for n, f in saved.items(): setattr(h, n, f)
assert h.heap_holder({"strata": 1}) == "strata" and "tabbyapi" not in h.PROFILES["engines"]
# an NVIDIA box lists strata before the Strix-oriented engines (menu order on a fresh box)
real_hw, real_choose = h.hardware, h.choose
h.hardware = lambda: {"cpus": 8, "ram_gb": 64, "gpu": "nvidia", "npu": False}
seen = []
h.choose = lambda q, opts, yes: seen.extend(opts) or opts[0]
h.engine_menu({})
names = [o.split()[1] if o[2:3] != " " else o.split()[1] for o in seen]
assert names.index("strata") < names.index("gufo"), names
h.hardware, h.choose = real_hw, real_choose
# Strata keys its repacked dense weights by model name, so a start on the other weights answer must be refused, not run on the wrong pack
sd = tempfile.mkdtemp()
for f in ("setup.py", "update.sh"): open(f"{sd}/{f}", "w").close()
open(f"{sd}/run-unsloth-ud-q4_k_xl.sh", "w").write("#!/bin/sh\necho started\n")
strata = os.path.join(os.path.dirname(__file__), "..", "server", "start-strata.sh")
def srun(w, *a):
    return subprocess.run(["bash", strata, *a], capture_output=True, text=True, env=dict(os.environ, STRATA_DIR=sd, STRATA_WEIGHTS=w))
assert srun("abliterated", "--print-run").stdout.strip().endswith("run-unsloth-ud-q4_k_xl.sh")  # installs older than the marker were abliterated
assert srun("stock", "--print-run").stdout.strip() == ""
r = srun("stock"); assert r.returncode == 1 and "built from the abliterated weights" in r.stderr, r
open(f"{sd}/.harness-weights", "w").write("stock\n")
assert srun("stock", "--print-run").stdout.strip().endswith("run-unsloth-ud-q4_k_xl.sh") and srun("abliterated", "--print-run").stdout.strip() == ""
assert srun("stock").returncode == 0 and srun("abliterated").returncode == 1
# hardware -> engine/quant/context. The measured Strix Halo 124 GB box gets halogen; the rest is the table's arithmetic
R = h.PROFILES["recommend"]
rec = lambda gpu, ram, fast, vram=0, npu=False, name="Strix Halo": h.recommend({"gpu": gpu, "ram_gb": ram, "npu": npu}, {"fast_gb": fast, "vram_gb": vram, "gpu_name": name})
r = rec("amd", 124, 124.5, npu=True)
assert r["engine"] == "halogen" and r["tested"], r
r = rec("amd", 124, 124.5)          # same box without the NPU: gufo at the shipped default quant and context
assert (r["engine"], r["quant"], r["ctx"], r["tested"]) == ("gufo", "UD-Q4_K_XL", 65536, False), r
assert (rec("amd", 102, 102)["quant"], rec("amd", 102, 102)["ctx"]) == ("UD-Q3_K_XL", 65536)
assert (rec("amd", 96, 96)["quant"], rec("amd", 96, 96)["ctx"]) == ("UD-Q3_K_XL", 32768)
assert (rec("amd", 92, 92)["quant"], rec("amd", 92, 92)["ctx"]) == ("UD-IQ3_XXS", 32768)
assert rec("amd", 88, 88)["engine"] is None and rec("amd", 64, 64)["engine"] is None and rec("none", 128, 128, name="")["engine"] is None
# every other AMD/NVIDIA box goes to Strata (gufo is Strix Halo only); the model is the biggest ISTA pack whose RAM need fits (Strata's rule),
# Qwen3.6 below that, nothing under its 32 GB; abliterated needs the Unsloth layout; nothing here is measured
nv = lambda ram, vram=24, gpu="nvidia": rec(gpu, ram, vram, vram=vram, name="RTX 4090")
r = nv(64)
assert (r["engine"], r["model"], r["tested"]) == ("strata", "flash-next-iq3_s", False), r
assert [nv(x)["model"] for x in (56, 48, 44, 40, 32)] == ["flash-next-iq3_xxs", "flash-next-iq2_xs", "flash-next-q2_0", "qwen36-q4", "qwen36-q4"]
assert nv(30)["engine"] is None and nv(64, gpu="amd")["engine"] == "strata" and rec("apple", 64, 64, name="")["engine"] is None
assert all(m in h.catalog() for m in R["strata_order"])
h.weights_pref, real_wp = (lambda: "abliterated"), h.weights_pref
assert nv(64)["model"] == "flash-next" and nv(60, 8)["engine"] is None and nv(64, 8)["engine"] == "strata"
h.weights_pref = real_wp
# the ladder is what the gufo launcher can load: every quant resolves to <quant>/...-00001-of-<shards>, sizes feed the download
for q in R["quants"]:
    out = subprocess.run(["bash", os.path.join(os.path.dirname(__file__), "..", "server", "start-gufo-flashnext.sh"), "--print-model"], capture_output=True, text=True,
                         env=dict(os.environ, GUFO_WEIGHTS="stock", GUFO_QUANT=q["name"])).stdout.strip()
    assert re.search(rf"/{q['name']}/Qwen3\.8-Flash-Next-{q['name']}-00001-of-0000[46]\.gguf$", out), out
    assert h.quant_gb(q["name"]) == int(q["gib"]) + R["extras_gb"] + 1
assert h.quant_gb("nope") is None
# accepting the gufo pick saves the quant (the launcher reads it) and the context (start_engine passes it); a repeat does not ask again
saved = {n: getattr(h, n) for n in ("hardware", "capacity", "ask_yn", "PICK", "QUANT_FILE", "MODEL_FILE")}
h.hardware, h.capacity = (lambda: {"gpu": "amd", "ram_gb": 96, "npu": False, "cpus": 8}), (lambda: {"gpu_name": "Strix Halo", "vram_gb": 0, "fast_gb": 96, "disk_free_gb": 500})
h.PICK, h.QUANT_FILE, h.MODEL_FILE = f"{AGENT}/pick.json", f"{AGENT}/quant", f"{AGENT}/model"
asked = []
h.ask_yn = lambda q, d=False: asked.append(q) or True
real_stdin, sys.stdin = sys.stdin, types.SimpleNamespace(isatty=lambda: True)
h.offer_recommendation("gufo")
assert open(h.QUANT_FILE).read().strip() == "UD-Q3_K_XL" and json.load(open(h.PICK)) == {"engine": "gufo", "model": "flash-next", "quant": "UD-Q3_K_XL", "ctx": 32768}, asked
assert h.saved_quant("gufo") == "UD-Q3_K_XL" and h.saved_quant("strata") == "UD-Q4_K_XL"
h.offer_recommendation("gufo"); h.offer_recommendation("strata")
assert len(asked) == 1, asked
assert h.hw_key("gufo", "m") != h.hw_key("strata", "m")
# a discrete-GPU box: accepting the Strata pick saves its model; engines that cannot load it keep Flash-Next; the model joins the tune key
h.hardware, h.capacity = (lambda: {"gpu": "nvidia", "ram_gb": 64, "npu": False, "cpus": 8}), (lambda: {"gpu_name": "RTX 4090", "vram_gb": 24, "fast_gb": 24, "disk_free_gb": 500})
k_before = h.hw_key("strata", "m")
assert h.offer_recommendation("strata") is True and h.model_pref() == "flash-next-iq3_s" and h.engine_model("strata") == "flash-next-iq3_s"
assert h.engine_model("gufo") == h.engine_model("halogen") == "flash-next" and h.hw_key("strata", "m") != k_before and h.hw_key("gufo", "m") == h.hw_key("gufo", "m")
n_asked = len(asked); assert h.offer_recommendation("strata") is True and len(asked) == n_asked  # same answer: not asked again
sys.stdin = real_stdin
for n, f in saved.items(): setattr(h, n, f)
# another model than Flash-Next: Strata installs it through its own setup (no stock/abliterated downloads); gufo's 27B fetches its own files
saved = {n: getattr(h, n) for n in ("MODEL_FILE", "strata_installed", "ask_yn", "heap_holder", "stock_ready", "g27_ready")}
h.MODEL_FILE = f"{AGENT}/model2"
h.strata_installed, h.heap_holder, h.stock_ready = (lambda: False), (lambda r: None), (lambda *a: False)
asked = []
h.ask_yn = lambda q, d=False: asked.append(q) or True
open(h.MODEL_FILE, "w").write("qwen36-q4\n")
plan = h.plan_start("strata", {})
assert plan["install"] == "strata" and not plan.get("stock") and not plan.get("weights") and "Qwen3.6-35B-A3B" in asked[0] and "~24 GB" in asked[0], (plan, asked)
ran = []
h.subprocess.run = lambda a, **k: ran.append(os.path.basename(a[1])) or types.SimpleNamespace(returncode=0)
assert h.prepare_start(plan, {}) and ran == ["install-strata.sh"], ran
open(h.MODEL_FILE, "w").write("qwen38-27b\n")
h.g27_ready = lambda: False
plan = h.plan_start("gufo", {})
assert plan["g27"] is True and not plan.get("stock") and not plan.get("weights"), plan
ran = []
assert h.prepare_start(plan, {}) and ran == ["download-qwen38-27b.sh"], ran
h.subprocess.run = real_run
h.g27_ready = lambda: True
assert not h.plan_start("gufo", {}).get("g27")
assert h.plan_start("strata", {})["stock"] == "UD-Q4_K_XL"  # 27B is gufo's: Strata falls back to Flash-Next
for n, f in saved.items(): setattr(h, n, f)
# Strata launcher by model: the catalog's run script is used whatever the weights answer says, a missing one is named, an unknown model means Flash-Next
open(f"{sd}/run-iq3_s.sh", "w").write("#!/bin/sh\necho iq3\n")
open(f"{sd}/.harness-weights", "w").write("abliterated\n")
sm = lambda m, w, *a: subprocess.run(["bash", strata, *a], capture_output=True, text=True, env=dict(os.environ, STRATA_DIR=sd, STRATA_WEIGHTS=w, STRATA_MODEL=m))
assert sm("flash-next-iq3_s", "stock", "--print-run").stdout.strip().endswith("run-iq3_s.sh") and "iq3" in sm("flash-next-iq3_s", "stock").stdout
r = sm("qwen36-q4", "stock", "--print-run"); assert r.stdout.strip() == "", r
r = sm("qwen36-q4", "stock"); assert r.returncode == 1 and "run-qwen36-ud-q4_k_xl.sh" in r.stderr, r
assert sm("qwen38-27b", "abliterated", "--print-run").stdout.strip().endswith("run-unsloth-ud-q4_k_xl.sh")
for mid, e in h.catalog().items():
    assert ("run" in e) == ("strata" in e) and ("strata" not in e or "strata" in e["engines"]), mid
# gufo launcher: the 27B family needs its own files, and --print-model keeps meaning Flash-Next whatever the saved answer is
gl = os.path.join(os.path.dirname(__file__), "..", "server", "start-gufo-flashnext.sh")
gd = tempfile.mkdtemp(); open(f"{gd}/harness-model", "w").write("qwen38-27b\n")
ge = dict(os.environ, PI_CODING_AGENT_DIR=gd, ENGINE_DIR=f"{gd}/flash", GUFO_27B_DIR=f"{gd}/27b")
assert "/UD-Q4_K_XL/Qwen3.8-Flash-Next" in subprocess.run(["bash", gl, "--print-model"], capture_output=True, text=True, env=ge).stdout
assert subprocess.run(["bash", gl, "--print-model"], capture_output=True, text=True, env=dict(ge, GUFO_FAMILY="qwen38-27b")).stdout.strip() == f"{gd}/27b/Qwen3.8-27B-UD-Q4_K_XL.gguf"
r = subprocess.run(["bash", gl], capture_output=True, text=True, env=ge)
assert r.returncode == 1 and "download-qwen38-27b.sh" in r.stderr, r
assert h.G27_DRAFT in open(gl).read() and h.G27_DRAFT in open(os.path.join(os.path.dirname(gl), "..", "scripts", "download-qwen38-27b.sh")).read()
# live monitor: halogen and Strata have one; Strata uses its own when its venv can run it, else our dashboard; others none
assert h.monitor_cmd("halogen")[1].endswith("halogen-top.sh") and h.monitor_cmd("gufo") is None and h.monitor_cmd("vllm") is None
os.environ["STRATA_DIR"] = md = tempfile.mkdtemp()
for f in ("setup.py", "update.sh", "strata.py"): open(f"{md}/{f}", "w").close()
assert h.monitor_cmd("strata")[2] == "--strata"  # no venv: our dashboard
os.makedirs(f"{md}/.venv/bin"); os.symlink(sys.executable, f"{md}/.venv/bin/python")
sp = h.PROFILES["engines"]["strata"]["port"]  # the mock above moved it
assert h.monitor_cmd("strata", once=True)[1:] == [f"{md}/strata.py", "monitor", "--url", f"http://127.0.0.1:{sp}", "--once"]
assert h.monitor_cmd("strata", "http://nas:8080", True)[4] == "http://nas:8080"
print("ok")
