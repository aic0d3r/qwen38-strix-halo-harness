/**
 * Ling-3.0-tiny repo-map extension
 *
 * /repomap: builds a compact repo map with tiny (git ls-files + key file heads),
 * inserts it as a user message so the main model starts oriented without
 * spending its first turns on exploratory reads.
 */

import * as os from "node:os";
import * as nodePath from "node:path";
import { uuidv7 } from "@earendil-works/pi-ai";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const TINY_PROVIDER = "llamacpp-tiny";
const TINY_MODEL = "ling3.0-tiny";
const MAX_FILES = 400;
const HEAD_CHARS = 1500;
// cache lives in ~/.pi/repomaps/<cwd-key>, not in the repo: writing .pi/ into
// every mapped project showed up as untracked noise in git status and checkpoints
const mapDir = () => nodePath.join(os.homedir(), ".pi", "repomaps", process.cwd().replace(/[^A-Za-z0-9]+/g, "-").slice(0, 64));

// shared map builder: git inventory -> tiny summary
async function buildRepoMap(pi: ExtensionAPI, ctx: any): Promise<string | null> {
	const model = ctx.modelRegistry.find(TINY_PROVIDER, TINY_MODEL);
	if (!model) return null;

	const { stdout: ls } = await pi.exec("git", ["ls-files"]);
	let files = ls.split("\n").filter(Boolean).slice(0, MAX_FILES);
	if (files.length === 0) return null;

	// pick "key" files: entry points, configs, and a sample of source files
	const keyPatterns = [/^(src|lib|app|server|cmd|pkg|packages)\/.*(index|main|app|server|mod)\.[a-z]+$/i, /^[a-z.]+(config|settings|pyproject|cargo|package)\.[a-z.]+$/i, /(^|\/)(README|AGENTS|CLAUDE|Makefile|Dockerfile)/i];
	const key = files.filter((f) => keyPatterns.some((p) => p.test(f))).slice(0, 15);
	const rest = files.filter((f) => !key.includes(f));
	const sample = rest.filter((_, i) => i % Math.max(1, Math.floor(rest.length / 40)) === 0).slice(0, 25);
	const selected = [...key, ...sample];

	let inventory = `FILE LIST (${files.length} tracked files, showing ${selected.length} key/sample):\n` + selected.join("\n");
	for (const f of selected.slice(0, 8)) {
		const { stdout: head } = await pi.exec("head", ["-c", String(HEAD_CHARS), f]);
		if (head.trim()) inventory += `\n\n=== HEAD of ${f} ===\n${head}`;
	}

	const summaryMessages = [
		{
			role: "user" as const,
			content: [
				{
					type: "text" as const,
					text: `Build a compact repo map for a coding agent starting work on this repository. Sections: (1) what the project is, one line; (2) structure: top-level dirs and their roles; (3) entry points and how the pieces connect; (4) build/test/run commands if visible; (5) conventions (language, framework, config style). Under 300 words, plain facts from the inventory only; do not invent.

<inventory>
${inventory}
</inventory>`,
				},
			],
			timestamp: Date.now(),
		},
	];

				const response = await ctx.modelRegistry.complete(
				model,
				{ messages: summaryMessages },
				{
					maxTokens: 2048,
					// a wedged sidecar must not hang turn 1 of every session in this
					// directory; same ceiling lesson as auto-guard's 10-07 queue wedge
					signal: AbortSignal.timeout(90_000),
					cacheRetention: "none",
					sessionId: uuidv7(),
				},
			);
	const map = response.content
		.filter((c): c is { type: "text"; text: string } => c.type === "text")
		.map((c) => c.text)
		.join("\n")
		.trim();
	return map || null;
}

export default function (pi: ExtensionAPI) {
	// auto-fire once per session on the first agent turn (git repos only):
	// the main model starts with the map instead of exploratory reads
	let autoFired = false;
	pi.on("before_agent_start", async (event, ctx) => {
		if (autoFired) return;
		autoFired = true;
		const fs = await import("node:fs/promises");
		try {
			// freshness cache: reuse the map while the tracked file set is unchanged
			// (same staleness contract as the original once-per-session build: untracked
			// files never trigger a rebuild either)
			const { stdout: hash } = await pi.exec("sh", ["-c", "git ls-files | sha1sum | cut -d' ' -f1"]);
			let map: string | null = null;
			let cached = false;
			try {
				const [sum, prev] = await Promise.all([fs.readFile(nodePath.join(mapDir(), "repomap.sum"), "utf-8"), fs.readFile(nodePath.join(mapDir(), "repomap.md"), "utf-8")]);
				if (sum.trim() === hash.trim() && prev.trim()) {
					map = prev.trim();
					cached = true;
				}
			} catch {}
			if (!map) {
				ctx.ui.notify(`[${TINY_MODEL}] building repo map...`, "info");
				map = await buildRepoMap(pi, ctx);
			}
			if (!map) return;
			await fs.mkdir(mapDir(), { recursive: true });
			await fs.writeFile(nodePath.join(mapDir(), "repomap.md"), map, "utf-8");
			await fs.writeFile(nodePath.join(mapDir(), "repomap.sum"), hash.trim() + "\n", "utf-8");
			ctx.ui.notify(cached ? `[${TINY_MODEL}] repo map reused (tracked files unchanged)` : `[${TINY_MODEL}] repo map injected (${map.length} chars)`, "info");
			// structured section: reaches the model's system prompt WITHOUT rendering in chat
			// (a returned custom_message used to print the whole map into the TUI)
			const opts = (event as { systemPromptOptions?: { sections?: Record<string, string> } }).systemPromptOptions;
			if (opts?.sections) {
				// no path mention: naming .pi/repomap.md here primed the model into
			// creating project files under .pi/ (observed 10-07); the map is inline,
			// so the agent never needs the path
			opts.sections.repo_map = `Repo map (generated by ${TINY_MODEL} from git ls-files). Use it to avoid exploratory reads of files it already covers:\n\n${map}`;
				return;
			}
			// fallback (no structured options): write to disk only - the agent can read .pi/repomap.md
			return;
		} catch (error) {
			const message = error instanceof Error ? error.message : String(error);
			ctx.ui.notify(`[${TINY_MODEL}] auto-repomap skipped: ${message}`, "warning");
		}
	});

	pi.registerCommand("repomap", {
		description: "Build a repo map with ling3.0-tiny and insert it into the session",
		handler: async (args, ctx) => {
			const model = ctx.modelRegistry.find(TINY_PROVIDER, TINY_MODEL);
			if (!model) {
				ctx.ui.notify(`[${TINY_MODEL}] provider not found`, "warning");
				return;
			}
			ctx.ui.notify(`Mapping repo with ${TINY_MODEL}...`, "info");
			try {
				const map = await buildRepoMap(pi, ctx);
				if (!map) {
					ctx.ui.notify("Not a git repo (no tracked files) or empty map.", "warning");
					return;
				}
				const fs = await import("node:fs/promises");
				await fs.mkdir(mapDir(), { recursive: true });
				const mapPath = nodePath.join(mapDir(), "repomap.md");
				await fs.writeFile(mapPath, map, "utf-8");
				ctx.ui.notify(`Repo map written to ${mapPath} (${map.length} chars). Next message: "Read ${mapPath} first, then <task>"`, "info");
			} catch (error) {
				const message = error instanceof Error ? error.message : String(error);
				ctx.ui.notify(`/repomap failed: ${message}`, "error");
			}
		},
	});
}
