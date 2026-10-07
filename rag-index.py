#!/usr/bin/env python3
"""rag-index.py - index a directory into an NPU-retrievable store (.rag/).

Chunks text files, embeds them on the halogen NPU (qwen3-embedding-0.6b),
writes <dir>/.rag/{index.json,vectors.f32} for npu-retrieval.ts.

  rag-index.py --dir <dir> [--ext '.cpp,.h,.py'] [--exclude '(\\.git|build)']
               [--incremental]   # re-embed only new/changed files (mtime-based)

index.json carries "files": {relpath: mtime}; --incremental keeps unchanged
files' vectors, re-embeds new/changed files, prunes deleted ones.
"""
import argparse, json, os, re, sys, time, urllib.request
from array import array

ap = argparse.ArgumentParser()
ap.add_argument("--dir", required=True)
ap.add_argument("--out", default=None)
ap.add_argument("--base", default="http://127.0.0.1:8731")
ap.add_argument("--chunk", type=int, default=1400, help="chars per chunk (~350 tok)")
ap.add_argument("--exclude", default=r"(\.git|node_modules|__pycache__|screenshots|\.npz$)")
ap.add_argument("--ext", default=".py,.md,.sh,.js,.ts,.json,.txt,.yaml,.yml,.toml")
ap.add_argument("--src-only", action="store_true")
ap.add_argument("--incremental", action="store_true")
a = ap.parse_args()

out = a.out or os.path.join(a.dir, ".npu-index.npz")
exts = tuple(a.ext.split(","))
excl = re.compile(a.exclude) if a.exclude else None
# per-repo excludes: <dir>/.ragignore lines merge into the exclude pattern
# (lets auto-index, setup --index, and manual runs all honor the same list)
ragignore = os.path.join(a.dir, ".ragignore")
if os.path.isfile(ragignore):
    extra = [l.strip() for l in open(ragignore) if l.strip() and not l.startswith("#")]
    if extra:
        pat = a.exclude + "|" + "|".join(extra) if a.exclude else "(" + "|".join(extra) + ")"
        excl = re.compile(pat)
        print(f".ragignore: +{len(extra)} exclude pattern(s)")
rag_dir = os.path.join(os.path.dirname(out) or ".", ".rag")
os.makedirs(rag_dir, exist_ok=True)
meta_path = os.path.join(rag_dir, "index.json")
vec_path = os.path.join(rag_dir, "vectors.f32")


def wanted(dp, fn):
    if any(x in dp for x in (".git", "node_modules", "__pycache__", "screenshots")):
        return False
    if not fn.endswith(exts) or fn.startswith("."):
        return False
    if a.src_only and (any(p in ("docs", "tests", "test") for p in dp.split(os.sep)) or fn.endswith(".md")):
        return False
    fp = os.path.join(dp, fn)
    if excl and excl.search(fp):
        return False
    return True


def chunk_file(rel, text):
    out_c, out_s = [], []
    for i in range(0, len(text), a.chunk):
        c = text[i:i + a.chunk]
        if len(c.strip()) > 120:
            out_c.append(f"[{rel}] " + c)
            out_s.append(rel)
    return out_c, out_s


def embed_batch(texts):
    req = urllib.request.Request(a.base + "/v1/embeddings",
        data=json.dumps({"model": "qwen3-embedding-0.6b", "input": texts}).encode(),
        headers={"Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=300))
    return [d["embedding"] for d in r["data"]]


# ---- current file mtimes ----
cur = {}
for dp, _, fns in os.walk(a.dir):
    for fn in fns:
        if wanted(dp, fn):
            fp = os.path.join(dp, fn)
            rel = os.path.relpath(fp, a.dir)
            try:
                cur[rel] = int(os.path.getmtime(fp))
            except OSError:
                pass

# ---- prior index (for --incremental) ----
old_files, old_chunks, old_srcs = {}, [], []
old_flat, old_dims = None, None
if a.incremental and os.path.exists(meta_path) and os.path.exists(vec_path):
    try:
        meta = json.load(open(meta_path))
        old_files = meta.get("files", {})
        old_chunks = meta.get("chunks", [])
        old_srcs = meta.get("srcs", [])
        old_dims = meta.get("dims")
        raw = array("f")
        raw.frombytes(open(vec_path, "rb").read())
        old_flat = raw
    except Exception as e:
        print(f"incremental fallback (bad old index): {e}", file=sys.stderr)
        old_files, old_chunks, old_srcs, old_flat = {}, [], [], None

# ---- select work: keep unchanged, embed new/changed ----
keep_idx = [i for i, s in enumerate(old_srcs) if s in cur and old_files.get(s) == cur.get(s)]
kept_chunks = [old_chunks[i] for i in keep_idx]
kept_srcs = [old_srcs[i] for i in keep_idx]
changed = [r for r, mt in sorted(cur.items()) if old_files.get(r) != mt]

if a.incremental and not changed:
    print(f"incremental: nothing changed ({len(kept_chunks)} chunks kept)")
    json.dump({"count": len(kept_chunks), "dims": old_dims, "files": cur,
               "chunks": kept_chunks, "srcs": kept_srcs}, open(meta_path, "w"))
    sys.exit(0)

new_chunks, new_srcs = [], []
for rel in changed:
    try:
        s = open(os.path.join(a.dir, rel), errors="replace").read()
    except OSError:
        continue
    c, s2 = chunk_file(rel, s)
    new_chunks.extend(c)
    new_srcs.extend(s2)

if not kept_chunks and not new_chunks:
    sys.exit("no chunks produced")
if a.incremental:
    print(f"incremental: kept {len(kept_chunks)}, embedding {len(new_chunks)} chunks "
          f"from {len(set(new_srcs))} changed/new files", file=sys.stderr)
else:
    print(f"indexing {len(new_chunks)} chunks from {len(set(new_srcs))} files ...", file=sys.stderr)

# ---- embed ----
t0 = time.time()
flat = array("f")
dims = old_dims
if keep_idx and old_flat is not None and old_dims:
    for i in keep_idx:
        flat.extend(old_flat[i * old_dims:(i + 1) * old_dims])
for i in range(0, len(new_chunks), 64):
    for v in embed_batch(new_chunks[i:i + 64]):
        if dims is None:
            dims = len(v)
        flat.extend(v)
dt = time.time() - t0

chunks = kept_chunks + new_chunks
srcs = kept_srcs + new_srcs
toks = sum(len(c) for c in chunks) / 3.9
with open(vec_path, "wb") as f:
    f.write(flat.tobytes())
json.dump({"count": len(chunks), "dims": dims, "files": cur,
           "chunks": chunks, "srcs": srcs}, open(meta_path, "w"))
rate = int(toks / dt) if dt > 0 else 0
print(f"indexed {len(chunks)} chunks ({int(toks):,} tok) from {len(set(srcs))} files "
      f"in {dt:.1f}s = {rate:,} tok/s -> {rag_dir}")
