#!/usr/bin/env python3
"""A stand-in engine for the Windows VM: answers GET /v1/models like Strata so setup.ps1 has something to adopt.
  python3 tests/windows-vm/stub-engine.py [host] [port]     default 192.168.122.1 8080 (the libvirt NAT side of this box)
In the guest:  .\\setup.ps1 -Url http://192.168.122.1:8080"""
import http.server, json, sys

host, port = (sys.argv[1] if len(sys.argv) > 1 else "192.168.122.1"), int(sys.argv[2] if len(sys.argv) > 2 else 8080)
BODY = json.dumps({"data": [{"id": "stub-model", "context_length": 65536, "architecture": {"input_modalities": ["text", "image"]}}]}).encode()


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path.startswith("/v1/models") else 404)
        self.send_header("Content-Length", str(len(BODY)) if self.path.startswith("/v1/models") else "0")
        self.end_headers()
        if self.path.startswith("/v1/models"):
            self.wfile.write(BODY)


print(f"stub engine on http://{host}:{port}/v1/models (Ctrl-C to stop)")
http.server.HTTPServer((host, port), H).serve_forever()
