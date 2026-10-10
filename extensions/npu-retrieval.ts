/**
 * npu-retrieval v3 - small NPU utility tools for pi.
 *
 * Semantic codebase search moved to the semble MCP server (mcp.json, `search` /
 * `find_related`): A/B on 18 ground-truth queries scored semble 17/18 vs the NPU
 * pipeline 13/18, 2-3x faster queries, ~40x faster indexing (2026-10-10). This
 * file keeps the NPU tools search cannot replace.
 *
 * Requirements: a halogen 0.16+ server on :8731 with HALOGEN_NPU_MODELS including
 * decider-0.8b (triage) and qwen3-embedding-0.6b (dedup_scan).
 *
 * Tools:
 *   triage(text, question, options) - fast classification with probabilities
 *   dedup_scan(dirs, threshold?)    - near-duplicate files via NPU embeddings
 */

import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const HOME = os.homedir();
const BASE = process.env.PI_NPU_BASE || "http://127.0.0.1:8731";

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

async function embed(texts: string[]): Promise<Float32Array[]> {
	const r = await post("/v1/embeddings", { model: "qwen3-embedding-0.6b", input: texts });
	return r.data.map((d: any) => Float32Array.from(d.embedding));
}

function cosine(a: Float32Array, b: Float32Array): number {
	let d = 0, na = 0, nb = 0;
	for (let i = 0; i < a.length; i++) { d += a[i] * b[i]; na += a[i] * a[i]; nb += b[i] * b[i]; }
	return d / Math.max(Math.sqrt(na) * Math.sqrt(nb), 1e-9);
}

const text = (t: string) => ({ content: [{ type: "text", text: t }] });

export default function (pi: ExtensionAPI) {
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
}
