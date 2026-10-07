0.17.1 speed-validation milestone — measure halogen against expected ranges. No repo changes: everything disposable in /tmp, results table printed at the end and saved to /tmp/speed-0171.md. Work the phases in order in this single session.

Phase 0 — battery script: write /tmp/bench-0171.py implementing all probes against http://127.0.0.1:8731 (:8090 for the sidecar). Every probe ×3, report median.

Phase 1 — decode A/B: POST /v1/chat/completions, max_tokens 400, temperature 0, "Count from 1 to 100, digits only, no spaces: <seed>" seeds A/B/C, once with "drafter":"mtp" and once with "drafter":"serial". completion_tokens/wall. Expected: MTP 58–68 t/s, serial 34–42 t/s.

Phase 2 — joint speculation: two MTP requests fired concurrently (threads), aggregate completion_tokens/wall over 3 trials. Expected: 65–80 t/s aggregate. Only engages with exactly two generating conversations.

Phase 3 — NPU latency: embed (qwen3-embedding-0.6b, 1 input), rerank (qwen3-reranker-0.6b, 2 docs), guard (qwen3guard-gen-0.6b, 1 string) ×3 each. Expected: every warm call ≤ 0.30s. Anything over 1s = the 0.16.2 disease.

Phase 4 — cache-cold prefill: ~250KB random ASCII (never reuse), "ignore it, reply OK", max_tokens 32; read the serve_api prefill tok/s from docker logs. Expected: 1,150–1,450 tok/s.

Phase 5 — aux engines: (a) one real codebase_search call in this repo (≤ 1.3s warm), (b) sidecar summarize (ling3.0-tiny :8090, ~30k-token filler, chat_template_kwargs {"enable_thinking": false}, max_tokens 2048, ≤ 25s).

Phase 6 — verdict table: probe | median | expected | PASS/FAIL; FAILs get a one-line hypothesis after a double re-run. Save to /tmp/speed-0171.md and show it.

Rules: serena/codebase_search for navigation; everything else in /tmp.
