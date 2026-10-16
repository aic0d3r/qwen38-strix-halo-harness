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
        if self.path == "/v1/models": self._j({"data": [{"id": "Qwen3-8B"}]})
        elif self.path == "/props": self._j({"default_generation_settings": {"n_ctx": 32768}})
        else: self._j({"ok": 1})
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(200); self.end_headers()
        for c in ('{"choices":[{"delta":{"content":"x"}}]}', '{"choices":[],"usage":{"prompt_tokens":900}}', "[DONE]"):
            self.wfile.write(f"data: {c}\n\n".encode())


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
print("ok")
