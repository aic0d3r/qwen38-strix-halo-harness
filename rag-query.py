#!/usr/bin/env python3
"""rag-query.py - retrieve from an .npu-index.npz store via the halogen NPU.

Embeds the query (qwen3-embedding-0.6b), cosine top-k, then NPU rerank
(qwen3-reranker-0.6b). Prints the top chunks with source + score.

Usage:
  rag-query.py --query "how does the serve rule work" [--index <.npz>]
               [--k 3] [--cands 20] [--base http://127.0.0.1:8731] [--no-rerank]
"""
import argparse, json, math, os, sys, time, urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--query", required=True)
ap.add_argument("--index", default=None)
ap.add_argument("--k", type=int, default=3)
ap.add_argument("--cands", type=int, default=20)
ap.add_argument("--base", default="http://127.0.0.1:8731")
ap.add_argument("--no-rerank", action="store_true")
ap.add_argument("--json", action="store_true", help="machine-readable output")
a = ap.parse_args()
import numpy as np

index = a.index
if not index:
    here = os.path.dirname(os.path.abspath(__file__))
    cands = [os.path.join(here, ".npu-index.npz")]
    for dp, _, fns in os.walk(os.getcwd()):
        for fn in fns:
            if fn == ".npu-index.npz":
                cands.append(os.path.join(dp, fn))
    index = next((c for c in cands if os.path.exists(c)), cands[0])
if not os.path.exists(index):
    sys.exit(f"index not found: {index} (run rag-index.py first)")

store = np.load(index, allow_pickle=False)
vecs, chunks, srcs = store["vectors"].astype(np.float32), store["chunks"], store["srcs"]

def post(path, payload, timeout=120):
    req = urllib.request.Request(a.base + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))

t0 = time.time()
qv = post("/v1/embeddings", {"model": "qwen3-embedding-0.6b", "input": [a.query]})["data"][0]["embedding"]
qv = np.array(qv, dtype=np.float32)
vnorm = vecs / (np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9)
sims = vnorm @ (qv / (np.linalg.norm(qv) + 1e-9))
top = np.argsort(-sims)[:a.cands]

if a.no_rerank:
    ranked = [(float(sims[i]), i) for i in top]
else:
    r = post("/v1/rerank", {"model": "qwen3-reranker-0.6b", "query": a.query,
        "documents": [{"text": chunks[i][:6000]} for i in top], "top_n": a.k})
    ranked = [(float(x["relevance_score"]), top[x["index"]]) for x in r["results"]]
lat = time.time() - t0

results = []
for score, i in ranked[:a.k]:
    results.append({"file": str(srcs[i]), "score": round(score, 3), "chunk": str(chunks[i])[:2400]})
if a.json:
    print(json.dumps({"query": a.query, "latency_s": round(lat, 2), "results": results}, indent=1))
else:
    print(f"query: {a.query}  ({lat:.2f}s)")
    for f, sc, c in [(r["file"], r["score"], r["chunk"]) for r in results]:
        print(f"\n-- {f}  [{sc}]\n{c}")
