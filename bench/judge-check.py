#!/usr/bin/env python3
"""scratch: drive bench/refusal-canary.py against a mock engine + mock NPU decider."""
import http.server, json, os, subprocess, sys, threading

class Mock(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if body.get("model") == "decider-0.8b":
            assert body["response_format"]["json_schema"]["schema"]["enum"] == ["answered", "refused"]
            content = "refused"
        else:  # a refusal no regex catches
            content = "That topic is outside what I care to discuss here."
        out = json.dumps({"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(out))); self.end_headers()
        self.wfile.write(out)

srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Mock)
threading.Thread(target=srv.serve_forever, daemon=True).start()
port = srv.server_port
env = dict(os.environ, PI_NPU_BASE=f"http://127.0.0.1:{port}")
r = subprocess.run([sys.executable, "bench/refusal-canary.py", "--base", f"http://127.0.0.1:{port}",
                    "--model", "mock", "--weights", "stock", "--judge"],
                   capture_output=True, text=True, env=env, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
print("\n".join(r.stdout.splitlines()[-10:]), r.stderr)
assert r.returncode == 1, r.returncode
assert "refused(judge)" in r.stdout, "judge override missing"
assert "19/19" in r.stdout, "all prompts should be judge-overridden"
print("JUDGE-CHECK OK (exit 1, 19 judge overrides)")
