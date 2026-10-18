#!/usr/bin/env python3
"""Acceptance battery the engine upgraders share: the model answers, and decodes fast enough.
  scripts/engine-acceptance.py <base-url> [min-decode-t/s]   exit 0 = pass
"""
import json, sys, time, urllib.request

url, min_tps = sys.argv[1].rstrip("/"), float(sys.argv[2]) if len(sys.argv) > 2 else 15


def chat(content, max_tokens):
    body = json.dumps({"model": model, "max_tokens": max_tokens, "temperature": 0,
                       "messages": [{"role": "user", "content": content}]}).encode()
    req = urllib.request.Request(url + "/v1/chat/completions", data=body, headers={"content-type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=180))


model = json.load(urllib.request.urlopen(url + "/v1/models", timeout=10))["data"][0]["id"]
txt = chat("Reply with exactly: OK", 400)["choices"][0]["message"].get("content") or ""
print(f"      answer: {txt.strip()[:40]!r}")
assert "OK" in txt, "no OK in the reply"
t0 = time.time()
n = chat("Count from 1 to 80, digits only, no spaces.", 300)["usage"]["completion_tokens"]
tps = n / (time.time() - t0)
print(f"      decode: {n} tok = {tps:.1f} t/s (need > {min_tps:g})")
assert tps > min_tps, f"decode {tps:.1f} t/s below {min_tps:g}"
