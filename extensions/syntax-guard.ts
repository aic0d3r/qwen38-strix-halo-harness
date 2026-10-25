// syntax-guard: after every edit/write, run a fast single-file syntax check and append the error to
// that tool result, so a broken edit is fixed on the next turn instead of discovered by a later run.
// Syntax only (no type or lint pass): cheap, no project config, no false positives from missing deps.
// A missing checker or a checker crash is silent. Off switch: SYNTAX_GUARD_DISABLED=1.
import { resolve } from "node:path";
import * as fs from "node:fs";
import * as path from "node:path";
import { type ExtensionAPI, getAgentDir } from "@earendil-works/pi-coding-agent";
// same event log as the other extensions; scripts/usage-stats.py counts it
const ev = (o: object) => { try { fs.appendFileSync(path.join(getAgentDir(), "harness-events.jsonl"), JSON.stringify({ t: new Date().toISOString(), cwd: process.cwd(), ...o }) + "\n"); } catch {} };

const PY = "import ast,sys; ast.parse(open(sys.argv[1]).read(), sys.argv[1])";
// shortcut: bun's transpiler is the TS/JS parser (node --check fails on ESM .ts); no bun = no TS check.
const BUN = ["bun", "build", "--no-bundle"];
const CHECKS: Record<string, string[]> = {
	py: ["python3", "-c", PY],
	sh: ["bash", "-n"],
	bash: ["bash", "-n"],
	ts: BUN, tsx: BUN, mts: BUN, js: BUN, jsx: BUN, mjs: BUN, cjs: BUN,
};
const SKIP_PATH = /(node_modules|dist|build|vendor)\//;

// full command to syntax-check this file, or undefined when its type has no checker
export function checkCommand(file: string): string[] | undefined {
	if (SKIP_PATH.test(file)) return;
	const ext = file.split(".").pop()?.toLowerCase() ?? "";
	const base = ext === "json" ? undefined : CHECKS[ext];
	return base && [...base, file];
}

export function jsonError(text: string): string {
	try { JSON.parse(text); return ""; } catch (e) { return String((e as Error).message); }
}

export default function syntaxGuard(pi: ExtensionAPI) {
	pi.on("tool_result", async (event, ctx) => {
		if (process.env.SYNTAX_GUARD_DISABLED) return;
		if (event.toolName !== "edit" && event.toolName !== "write") return;
		if (event.isError) return;
		const target = event.input.path;
		if (typeof target !== "string") return;
		const file = resolve(ctx.cwd, target);

		let err = "";
		try {
			if (/\.json$/i.test(file) && !SKIP_PATH.test(file)) {
				err = jsonError(fs.readFileSync(file, "utf8"));
			} else {
				const cmd = checkCommand(file);
				if (!cmd) return;
				const { code, stdout, stderr } = await pi.exec(cmd[0], cmd.slice(1), { cwd: ctx.cwd, timeout: 15_000 });
				// code 127/ENOENT-style failures (checker not installed) must stay silent
				if (code === 0 || code === 127) return;
				// bun prints the transpiled source to stdout on success only, so errors are in stderr
				err = (stderr || stdout).replace(/\x1b\[[0-9;]*m/g, "").trim();
			}
		} catch {
			return; // a broken or missing checker must never eat a good edit
		}
		ev({ c: "syntax-guard", error: !!err });
		if (!err) return;

		return {
			content: [
				...event.content,
				{ type: "text" as const, text: `syntax check failed in ${file}\n${err.slice(0, 800)}\nFix it before moving on.` },
			],
		};
	});
}
