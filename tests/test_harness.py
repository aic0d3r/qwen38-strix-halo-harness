"""python3 tests/test_harness.py - wizard core against a mock OpenAI server (no real engine needed)."""
import json, os, sys, tempfile, threading, http.server

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
assert h.heap_holder({"gufo": 1}) == "gufo" and h.heap_holder(run) is None
assert h.start_engine("strata", run) is False
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
os.remove(f"{AGENT}/harness-sidecar")
print("ok")
