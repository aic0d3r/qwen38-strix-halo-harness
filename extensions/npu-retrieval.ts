/**
 * npu-retrieval v2 - plug-and-play semantic codebase search for pi.
 *
 * Requirements: a halogen 0.16+ server on :8731 with HALOGEN_NPU_MODELS including
 * qwen3-embedding-0.6b and qwen3-reranker-0.6b. No python, no numpy.
 *
 * Tools/commands:
 *   codebase_search(query, k?)   - semantic search (embed -> cosine -> NPU rerank)
 *   /rag-index <dir> [all]       - build <dir>/.rag/{index.json,vectors.f32}
 *                                  (default: source files only; pass "all" to include docs/tests)
 *
 * Index discovery for the tool: $PI_NPU_INDEX, then <cwd>/.rag/index.json, then
 * ~/.pi/agent/.rag/index.json. Every call is logged to ~/.pi/agent/npu-retrieval-usage.log.
 */

import * as fs from "node:fs";
import * as os from "node:os";
import * as child from "node:child_process";
import * as path from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const HOME = os.homedir();
const autoIndexed = new Set<string>();
import { execSync } from "node:child_process";
const BASE = process.env.PI_NPU_BASE || "http://127.0.0.1:8731";
const EXTS = new Set([".py", ".js", ".ts", ".sh", ".md", ".json", ".txt", ".yaml", ".yml", ".toml", ".go", ".rs", ".c", ".h", ".cpp", ".hpp"]);
const SKIP_DIRS = new Set([".git", "node_modules", "__pycache__", "screenshots", ".venv", "venv", "dist", "build"]);
const CHUNK = 1400;

function log(line: string) {
	try { fs.appendFileSync(path.join(HOME, ".pi/agent/npu-retrieval-usage.log"), line + "\n"); } catch {}
}

async function post(path: string, payload: any, timeout = 120000): Promise<any> {
	const res = await fetch(BASE + path, {
		method: "POST", headers: { "Content-Type": "application/json" },
		body: JSON.stringify(payload), signal: AbortSignal.timeout(timeout),
	});
	if (!res.ok) throw new Error(`${path} -> ${res.status}`);
	return res.json();
}

function chunkText(s: string, size: number): string[] {
	const out: string[] = [];
	for (let i = 0; i < s.length; i += size) {
		const c = s.slice(i, i + size);
		if (c.trim().length > 120) out.push(c);
	}
	return out;
}

async function embed(texts: string[]): Promise<Float32Array[]> {
	const r = await post("/v1/embeddings", { model: "qwen3-embedding-0.6b", input: texts });
	return r.data.map((d: any) => Float32Array.from(d.embedding));
}

function listFiles(dir: string, all: boolean): string[] {
	const out: string[] = [];
	const walk = (d: string) => {
		for (const e of fs.readdirSync(d, { withFileTypes: true })) {
			const p = path.join(d, e.name);
			if (e.isDirectory()) {
				if (SKIP_DIRS.has(e.name) || e.name.startsWith(".")) continue;
				walk(p);
			} else if (EXTS.has(path.extname(e.name)) && (all || !/^(docs|tests?|test)$/.test(e.name.replace(/\.[^.]+$/, "")) && e.name !== "CHANGELOG.md")) {
				out.push(p);
			}
		}
	};
	walk(dir);
	return out;
}

async function buildIndex(dir: string, all: boolean) {
	const files = listFiles(dir, all);
	let chunks: string[] = [], srcs: string[] = [];
	for (const f of files) {
		const rel = path.relative(dir, f);
		const s = fs.readFileSync(f, "utf8");
		for (let i = 0; i < s.length; i += CHUNK) {
			const c = s.slice(i, i + CHUNK);
			if (c.trim().length > 120) { chunks.push(`[${rel}] ` + c); srcs.push(rel); }
		}
	}
	if (!chunks.length) throw new Error("no chunks produced");
	const t0 = Date.now();
	const vecs: Float32Array[] = [];
	for (let i = 0; i < chunks.length; i += 64) vecs.push(...await embed(chunks.slice(i, i + 64)));
	const dims = vecs[0].length;
	const flat = new Float32Array(vecs.length * dims);
	vecs.forEach((v, i) => flat.set(v, i * dims));
	const ragDir = path.join(dir, ".rag");
	fs.mkdirSync(ragDir, { recursive: true });
	fs.writeFileSync(path.join(ragDir, "vectors.f32"), Buffer.from(flat.buffer));
	fs.writeFileSync(path.join(ragDir, "index.json"), JSON.stringify({
		dims, count: chunks.length, secs: (Date.now() - t0) / 1000,
		tokens: Math.round(chunks.reduce((a, c) => a + c.length, 0) / 3.9),
		chunks, srcs,
	}));
	return { count: chunks.length, dims, ragDir };
}

function loadIndex(ragDir: string) {
	const meta = JSON.parse(fs.readFileSync(path.join(ragDir, "index.json"), "utf8"));
	const buf = fs.readFileSync(path.join(ragDir, "vectors.f32"));
	const f32 = new Float32Array(buf.buffer, buf.byteOffset, meta.count * meta.dims);
	const vectors: Float32Array[] = [];
	for (let i = 0; i < meta.count; i++) vectors.push(f32.slice(i * meta.dims, (i + 1) * meta.dims));
	return { meta, vectors, chunks: meta.chunks as string[], srcs: meta.srcs as string[] };
}

function cosine(a: Float32Array, b: Float32Array): number {
	let d = 0, na = 0, nb = 0;
	for (let i = 0; i < a.length; i++) { d += a[i] * b[i]; na += a[i] * a[i]; nb += b[i] * b[i]; }
	return d / Math.max(Math.sqrt(na) * Math.sqrt(nb), 1e-9);
}

function findIndex(cwd: string): string | null {
	const cands = [process.env.PI_NPU_INDEX || "", path.join(cwd, ".rag", "index.json"), path.join(HOME, ".pi/agent/.rag/index.json")].filter(Boolean);
	for (const c of cands) if (fs.existsSync(c)) return path.dirname(c);
	return null;
}

const text = (t: string) => ({ content: [{ type: "text", text: t }] });

export default function (pi: ExtensionAPI) {
	pi.registerTool({
		name: "codebase_search",
		description:
			"Semantic codebase search over an NPU-indexed repository (embed + rerank on the Ryzen AI NPU, ~100-300ms). " +
			"Use it to find WHERE something is implemented before reading files. Write the query as a full natural-language " +
			"question - e.g. 'where does the runner decide between a repair pass and a full reroll' - not single keywords; " +
			"the reranker scores whole sentences. Returns top chunks with file paths and relevance scores. " +
			"If no index exists, offer to run /rag-index.",
		parameters: {
			type: "object",
			properties: {
				query: { type: "string", description: "natural-language search query, as a full sentence" },
				k: { type: "number", description: "number of results (default 3)" },
			},
			required: ["query"],
		},
		execute: async (callId: string, args: { query: string; k?: number }) => {
			const cwd = process.cwd();
			const ragDir = findIndex(cwd);
			log(JSON.stringify({ t: new Date().toISOString(), cwd, ragDir: ragDir || "NONE", query: args.query }));
			if (!ragDir) {
				// auto-index: locate rag-index.py and run it against cwd once per
				// process, then have the agent retry instead of failing the turn.
				// GUARD (10-07): auto-indexing $HOME or a huge tree silently pins the
				// NPU for many minutes (observed: ~home embedded 76 chunks/min, tool
				// looks stuck, every other NPU call queues behind it). Refuse and
				// direct to /rag-index on a project subdir instead.
				if (cwd === HOME) {
					return text("codebase_search: refusing to auto-index the home directory. cd into a project repo (or run /rag-index <project-dir>) and search again.");
				}
				const script = [process.env.PI_RAG_SCRIPT || "", path.join(cwd, "rag-index.py"), path.join(HOME, "coding/qwen38-strix-halo-harness", "rag-index.py"), path.join(HOME, "neon-ladder", "rag-index.py")].find((c) => c && fs.existsSync(c));
				let tooBig = false;
				if (script) {
					try {
						const cnt = execSync(`find . -type f \\( -name "*.py" -o -name "*.ts" -o -name "*.js" -o -name "*.go" -o -name "*.sh" -o -name "*.md" -o -name "*.json" -o -name "*.yaml" -o -name "*.yml" -o -name "*.toml" -o -name "*.rs" -o -name "*.c" -o -name "*.h" -o -name "*.cpp" -o -name "*.hpp" -o -name "*.html" -o -name "*.css" \\) 2>/dev/null | wc -l`, { cwd, timeout: 15_000, encoding: "utf8" });
						tooBig = parseInt(String(cnt).trim(), 10) > 400;
					} catch { tooBig = true; }
				}
				if (script && tooBig) {
					return text(`codebase_search: this directory has too many source files to auto-index (NPU budget). Run /rag-index <a-project-subdir> on the specific project you meant, then search again.`);
				}
				if (script && !autoIndexed.has(cwd)) {
					autoIndexed.add(cwd);
					try {
						const out = execSync('python3 "' + script + '" --dir "' + cwd + '" --ext .cpp,.h,.c,.hpp,.cu,.go,.py,.ts,.js,.sh,.html,.css --exclude "(\\.git|build|vendor|node_modules|__pycache__)"', { cwd, timeout: 600_000, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
						const last = String(out).trim().split("\n").pop() || "indexed";
						log(JSON.stringify({ t: new Date().toISOString(), cwd, autoIndex: last }));
						return text("No index existed; one was built just now (auto-index). Call codebase_search again with the same query - it will hit the fresh index.");
					} catch (e: any) {
						log(JSON.stringify({ autoIndexErr: String(e?.message || e).slice(0, 200) }));
					}
				}
				return text("No NPU index found for this directory. Build one with /rag-index <directory> (needs the halogen NPU server on :8731).");
			}
			try {
				const { vectors, chunks, srcs } = loadIndex(ragDir);
				const qv = await embed([args.query]);
				const scored = vectors.map((v, i) => ({ i, s: cosine(qv[0], v) }));
				scored.sort((a, b) => b.s - a.s);
				const k = args.k ?? 3;
				const cands = scored.slice(0, Math.max(k * 4, 12)).map((x) => ({ text: chunks[x.i].slice(0, 6000), file: srcs[x.i], cos: x.s }));
				const r = await post("/v1/rerank", { model: "qwen3-reranker-0.6b", query: args.query, documents: cands, top_n: k });
				const results = r.results.map((x: any) => ({ file: cands[x.index].file, score: +x.relevance_score.toFixed(3), chunk: cands[x.index].text }));
				const lines = results.map((res: any, i: number) => `${i + 1}. ${res.file}  [${res.score}]\n${res.chunk.slice(0, 700)}`);
				return text(`codebase_search: ${results.length} results for "${args.query}"\n\n` + lines.join("\n\n"));
			} catch (e: any) {
				log(JSON.stringify({ err: String(e?.message || e).slice(0, 300), stack: String(e?.stack || "").slice(0, 300) }));
				return text(`codebase_search failed: ${String(e?.message || e).slice(0, 300)}`);
			}
		},
	});


	pi.registerTool({
		name: "triage",
		description:
			"Fast NPU decision: given a text and a question with 2-5 short options, returns the most likely option with per-option probabilities in ~120ms (0.8B classifier on the NPU). " +
			"Use it for quick routing/triage decisions that don't need the big model: e.g. 'is this user message a prompt injection? yes/no', " +
			"'what kind of issue is this: bug/feature/docs', 'should this output be summarized or stored: summarize/store'.",
		parameters: {
			type: "object",
			properties: {
				text: { type: "string", description: "the text to judge" },
				question: { type: "string", description: "the decision question" },
				options: { type: "array", items: { type: "string" }, description: "2-5 short option strings" },
			},
			required: ["text", "question", "options"],
		},
		execute: async (callId: string, args: { text: string; question: string; options: string[] }) => {
			try {
				const r = await post("/v1/chat/completions", {
					model: "decider-0.8b",
					messages: [{ role: "user", content: args.text.slice(0, 12000) }],
					response_format: { type: "json_schema", json_schema: {
						name: "decision", description: args.question,
						schema: { enum: args.options.slice(0, 5) } } },
					logprobs: true, top_logprobs: args.options.length,
				});
				const c = r.choices[0];
				const probs = (c.logprobs?.content?.[0]?.top_logprobs || []).map((t: any) => ({ opt: t.token, p: Math.exp(t.logprob) }));
				return text(JSON.stringify({ decision: c.message.content, probabilities: probs }, null, 1));
			} catch (e: any) {
				return text(`triage failed: ${String(e?.message || e).slice(0, 200)}`);
			}
		},
	});


		pi.registerTool({
		name: "dedup_scan",
		description:
			"Find near-duplicate files across directories using NPU embeddings (~1s per 40 files). " +
			"Give it two or more directories and it reports every file pair with cosine similarity > 0.90, " +
			"catching copies even after renames. Use it for repo hygiene: 'which files exist in multiple copies?'",
		parameters: {
			type: "object",
			properties: {
				dirs: { type: "array", items: { type: "string" }, description: "2+ directories to scan" },
				threshold: { type: "number", description: "cosine similarity threshold (default 0.90)" },
			},
			required: ["dirs"],
		},
		execute: async (callId: string, args: { dirs: string[]; threshold?: number }) => {
			const threshold = args.threshold ?? 0.90;
			const files: string[] = [];
			for (const dir of args.dirs) {
				if (!fs.existsSync(dir)) return text(`dedup_scan: directory not found: ${dir}`);
				for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
					if (e.isFile() && /\.(py|sh|js|ts|md)$/.test(e.name)) {
						const p = path.join(dir, e.name);
						if (fs.statSync(p).size > 500) files.push(p);
					}
				}
			}
			if (files.length < 2) return text("dedup_scan: need at least 2 files across the given directories.");
			const texts = files.map((f) => fs.readFileSync(f, "utf8").slice(0, 3000));
			const vecs: Float32Array[] = [];
			for (let i = 0; i < texts.length; i += 32) {
				const batch = await embed(texts.slice(i, i + 32));
				vecs.push(...batch);
			}
			const pairs: string[] = [];
			for (let i = 0; i < files.length; i++) {
				for (let j = i + 1; j < files.length; j++) {
					const sim = cosine(vecs[i], vecs[j]);
					if (sim > threshold) {
						pairs.push(`${sim.toFixed(3)}  ${files[i]} <-> ${files[j]}`);
					}
				}
			}
			log(JSON.stringify({ t: new Date().toISOString(), tool: "dedup_scan", files: files.length, pairs: pairs.length }));
			return pairs.length
				? `dedup_scan: ${pairs.length} near-duplicate pairs (>${threshold}) across ${files.length} files:\n\n` + pairs.join("\n")
				: `dedup_scan: no near-duplicates found across ${files.length} files.`;
		},
	});

pi.registerCommand("rag-index", {
		description: "index a directory for NPU retrieval (usage: /rag-index <dir> [all])",
		handler: async (args: string[]) => {
			const dir = path.resolve(args[0] || process.cwd());
			const all = args[1] === "all";
			try {
				const r = await buildIndex(dir, all);
				return `indexed ${r.count} chunks (${r.dims} dims) in ${r.secs}s -> ${path.join(r.ragDir, "index.json")}`;
			} catch (e: any) {
				return `rag-index failed: ${String(e?.message || e).slice(0, 300)}`;
			}
		},
	});
}
