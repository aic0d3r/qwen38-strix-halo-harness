#!/usr/bin/env python3
"""A stand-in engine for the Windows VM: answers GET /v1/models like Strata so setup.ps1 has something to adopt.
  python3 tests/windows-vm/stub-engine.py [host] [port]     default 192.168.122.1 8080 (the libvirt NAT side of this box)
In the guest:  .\\setup.ps1 -Url http://192.168.122.1:8080"""
import http.server, json, sys

host, port = (sys.argv[1] if len(sys.argv) > 1 else "192.168.122.1"), int(sys.argv[2] if len(sys.argv) > 2 else 8080)
BODY = json.dumps({"data": [{"id": "stub-model", "context_length": 65536, "architecture": {"input_modalities": ["text", "image"]}}]}).encode()


class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        """A scripted chat completion, enough for `pi -p` to run a whole turn through the extensions:
        prompt `bash:<command>` -> the model calls pi's bash tool with <command> (`bash:a;;;b` runs a, then b); `tool:<name>:k=v;;k=v` -> any other tool
        (e.g. `tool:write:path=C:\\p\\a.css;;content=a{}`; `<NL>` in a value is a newline). No quotes, `|` or `\\n`: Windows PowerShell mangles
        quotes and the pi .cmd shim treats an unquoted `|` as a pipe. After any tool result it answers `RESULT: <what the tool
        returned>` (so the caller can read what the guards did); anything else -> `ok`."""
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        text = lambda m: m.get("content") if isinstance(m.get("content"), str) else " ".join(p.get("text", "") for p in (m.get("content") or []))
        msgs = body.get("messages") or [{}]
        u = max(i for i, m in enumerate(msgs) if m.get("role") == "user")
        prompt, results = text(msgs[u]), [text(m) for m in msgs[u + 1:] if m.get("role") == "tool"]
        steps = prompt.split(";;;") if prompt.startswith("bash:") else [prompt]  # `bash:a;;;b` = run a, then b, in one turn
        if len(results) >= (len(steps) if prompt.startswith(("bash:", "tool:")) else 0):
            delta = {"role": "assistant", "content": "ok" if not results else "RESULT: " + " || ".join(r[:1500] for r in results)}
        else:
            step = steps[len(results)]
            if prompt.startswith("bash:"):
                name, args = "bash", json.dumps({"command": step[5:] if len(results) == 0 else step})
            else:
                name, _, kv = prompt[5:].partition(":")
                args = json.dumps({k: v.replace("<NL>", "\n") for k, _, v in (q.partition("=") for q in kv.split(";;"))})
            delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": "call_%d" % len(results), "type": "function", "function": {"name": name, "arguments": args}}]}
        done = "tool_calls" if "tool_calls" in delta else "stop"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for c in (json.dumps({"choices": [{"delta": delta}]}), json.dumps({"choices": [{"delta": {}, "finish_reason": done}], "usage": {"prompt_tokens": 10, "completion_tokens": 1}}), "[DONE]"):
            self.wfile.write(f"data: {c}\n\n".encode())

    def do_GET(self):
        self.send_response(200 if self.path.startswith("/v1/models") else 404)
        self.send_header("Content-Length", str(len(BODY)) if self.path.startswith("/v1/models") else "0")
        self.end_headers()
        if self.path.startswith("/v1/models"):
            self.wfile.write(BODY)


print(f"stub engine on http://{host}:{port}/v1/models (Ctrl-C to stop)")
http.server.HTTPServer((host, port), H).serve_forever()
