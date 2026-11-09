"""python3 tests/test_adopt.py - setup/adopt.py against a temp HOME: copy sections, provider merge, rtk install (fake archives, no network)."""
import hashlib, http.server, io, json, os, sys, tarfile, tempfile, threading, zipfile

HOME = tempfile.mkdtemp()
os.environ.update(HOME=HOME, USERPROFILE=HOME, PI_HOME=os.path.join(HOME, ".pi"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "setup"))
import adopt

AGENT, EXT = adopt.AGENT, adopt.EXT_DIR
cap = io.StringIO()


def run(fn, *a):
    old, sys.stdout = sys.stdout, cap
    cap.seek(0); cap.truncate()
    try:
        fn(*a)
    finally:
        sys.stdout = old
    return cap.getvalue()


# extensions: engine-free install skips the NPU ones, drops stale files, re-run changes nothing
os.makedirs(EXT, exist_ok=True)
open(os.path.join(EXT, "npu-triage.ts"), "w").write("old")
os.makedirs(os.path.join(EXT, "plan-mode"))
out = run(adopt.extensions, True)
assert not os.path.exists(os.path.join(EXT, "npu-retrieval.ts")) and not os.path.exists(os.path.join(EXT, "auto-guard.ts")), out
assert os.path.isfile(os.path.join(EXT, "taint-gate.ts")) and os.path.isfile(os.path.join(EXT, "subagent", "index.ts"))
assert "removed npu-triage.ts" in out and "removed plan-mode/" in out, out
assert open(os.path.join(AGENT, "harness-repo")).read().strip() == adopt.REPO
assert "already current" in run(adopt.extensions, True)
open(os.path.join(EXT, "subagent", "index.ts"), "a").write("// drift")  # a changed dir is replaced
assert "installed subagent/" in run(adopt.extensions, True)
run(adopt.extensions, False)
assert os.path.isfile(os.path.join(EXT, "npu-retrieval.ts"))  # with halogen they come too

# provider: from a mock /v1/models; an existing provider and saved defaults are left alone
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        b = json.dumps({"data": [{"id": "m1", "context_length": 65536, "architecture": {"input_modalities": ["text", "image"]}}]}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)


srv = http.server.HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_port}"
run(adopt.provider, url, "strata")
m = json.load(open(os.path.join(AGENT, "models.json")))["providers"]["strata"]
assert m["baseUrl"] == url + "/v1" and m["models"][0]["id"] == "m1" and m["models"][0]["contextWindow"] == 65536 and m["models"][0]["input"] == ["text", "image"], m
assert json.load(open(os.path.join(AGENT, "settings.json")))["defaultModel"] == "m1"
m["apiKey"] = "secret"
json.dump({"providers": {"strata": m}}, open(os.path.join(AGENT, "models.json"), "w"))
assert "left untouched" in run(adopt.provider, url, "strata")
assert json.load(open(os.path.join(AGENT, "models.json")))["providers"]["strata"]["apiKey"] == "secret"
srv.shutdown()
open(os.path.join(AGENT, "models.json"), "w").write("{broken")  # a parse error must not be overwritten
try:
    adopt.provider(url, "gufo"); assert False
except ValueError:
    assert open(os.path.join(AGENT, "models.json")).read() == "{broken"

# agents: user-owned file never overwritten
open(os.path.join(AGENT, "AGENTS.md"), "w").write("mine")
assert "left untouched" in run(adopt.agents) and open(os.path.join(AGENT, "AGENTS.md")).read() == "mine"

# rtk: pinned sha256, tar (Linux) and zip (Windows), mismatch refused
exe_data = b"MZ-fake-rtk"
def tar_bytes():
    b = io.BytesIO()
    with tarfile.open(fileobj=b, mode="w:gz") as t:
        ti = tarfile.TarInfo("rtk"); ti.size = len(exe_data); t.addfile(ti, io.BytesIO(exe_data))
    return b.getvalue()
def zip_bytes():
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("LICENSE", "x"); z.writestr("rtk.exe", exe_data)
    return b.getvalue()

class Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False

def try_rtk(system, arch, payload, sha):
    adopt.platform.system = lambda: system
    adopt.platform.machine = lambda: arch
    adopt.RTK_ASSETS[(system.lower(), "x86_64")] = ("x.zip" if system == "Windows" else "x.tar.gz", sha)
    adopt.urllib.request.urlopen = lambda *a, **k: Resp(payload)
    adopt.rtk_version = lambda: (None, "")
    adopt.shutil.which = lambda n: None
    return run(adopt.rtk_binary)

bindir = os.path.join(HOME, ".local", "bin")
p = tar_bytes()
out = try_rtk("Linux", "x86_64", p, hashlib.sha256(p).hexdigest())
assert "installed to" in out and open(os.path.join(bindir, "rtk"), "rb").read() == exe_data, out
p = zip_bytes()
out = try_rtk("Windows", "AMD64", p, hashlib.sha256(p).hexdigest())  # AMD64 is what Windows reports
assert "installed to" in out and open(os.path.join(bindir, "rtk.exe"), "rb").read() == exe_data, out
os.remove(os.path.join(bindir, "rtk.exe"))
out = try_rtk("Windows", "AMD64", p, "0" * 64)
assert "download failed" in out and not os.path.exists(os.path.join(bindir, "rtk.exe")), out  # swapped asset: nothing installed

# rtk config: placed where `rtk config` says (on Windows the path carries its own colon), never overwritten
toml = os.path.join(HOME, "cfg", "rtk", "config.toml")
adopt.shutil.which = lambda n: "rtk"
adopt.subprocess.run = lambda *a, **k: type("R", (), {"stdout": f"Config: {toml}\n\n(default config, file not created)\n"})()  # a shell-script fake cannot run on Windows
run(adopt.rtk_config)
assert open(toml).read() == open(os.path.join(adopt.REPO, "config", "rtk", "config.toml")).read()
open(toml, "w").write("mine")
run(adopt.rtk_config)
assert open(toml).read() == "mine"
adopt.shutil.which = lambda n: None
adopt.RTK_BIN = os.path.join(HOME, "nowhere")
run(adopt.rtk_config)  # no rtk at all: silent no-op

print("ok")
