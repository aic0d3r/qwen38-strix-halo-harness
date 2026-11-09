"""setup/strata_doctor.py: known Strata failure text -> advice, port/model-id checks against a mock server."""
import http.server, json, os, sys, tempfile, threading

AGENT = tempfile.mkdtemp()
os.environ["PI_CODING_AGENT_DIR"] = AGENT
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "setup"))
import strata_doctor as d

# explain: each known failure, plus nothing for unrelated text
assert "#5542" in d.explain("--version exited 3221225785: no output")[0]
assert "Smart App Control" in d.explain("OSError: [WinError 4551] blocked")[0]
v = d.explain("strata-q36: not enough VRAM left (1696 MiB needed, 989 MiB free after the weights, the context)")
assert "short by 707 MiB" in v[0], v
both = d.explain("989 MiB free after the weights; 1696 MiB needed, 989 MiB free after the weights\nignoring --vram-reserve-mib 0 (not an option of this engine)")
assert len(both) == 2 and "ignores 'GPU memory for other apps'" in both[1], both
assert d.explain("all good") == []

# check: mock engine + provider drift
OWNER = {"v": None}


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        b = json.dumps({"data": [{"id": "real-model", **({"owned_by": OWNER["v"]} if OWNER["v"] else {})}]}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)


s = http.server.HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=s.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{s.server_port}"
d.feed_status = lambda *a: None  # no network in tests
json.dump({"providers": {"strata": {"models": [{"id": "local"}]}}}, open(os.path.join(AGENT, "models.json"), "w"))

r = d.check(url)
assert r[0][0] == "ok" and "real-model" in r[0][1], r
assert any("'local'" in t and "404" in t for _, t in r), r  # #5574: provider id not served
OWNER["v"] = "gufo"
r = d.check(url)
assert r[0][0] == "warn" and "gufo answers" in r[0][1] and len(r) == 1, r  # no model-id complaint about another engine's list
s.shutdown()
r = d.check(url)  # down: warns, no crash
assert r[0][0] == "warn" and "nothing answers" in r[0][1], r

# feed: HTTP error is reported, a dead connection is not
import importlib; importlib.reload(d)
assert d.feed_status("http://127.0.0.1:1/x") is None
print("ok")
