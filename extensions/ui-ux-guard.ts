// ui-ux-guard: the UI detector gate for pi. impeccable ships hook manifests for Claude
// Code / Codex / Cursor / Copilot / Gemini, but not for pi, so in pi the detector would
// only run if the agent remembered to. This wires it mechanically: after every edit/write
// on a UI-relevant file, run the impeccable detector and append any new findings to that
// tool result, so a UI change cannot be silently called done.
// One-shot off switch: IMPECCABLE_HOOK_DISABLED=1 (same env var impeccable honors).

import { resolve } from "node:path";
import * as fs from "node:fs";
import * as path from "node:path";
import { type ExtensionAPI, getAgentDir } from "@earendil-works/pi-coding-agent";
// same event log as the other extensions; scripts/usage-stats.py counts it
const ev = (o: object) => { try { fs.appendFileSync(path.join(getAgentDir(), "harness-events.jsonl"), JSON.stringify({ t: new Date().toISOString(), cwd: process.cwd(), ...o }) + "\n"); } catch {} };

// Impeccable is not part of this harness (it ships no skills; see the 1.0.1 drop of vendored
// design skills), so the detector binary is wherever the user put it: override with
// IMPECCABLE_BIN, else the default user-skill location. Missing binary = silent no-op.
const DETECTOR =
	process.env.IMPECCABLE_BIN || `${process.env.HOME}/.pi/agent/skills/impeccable/scripts/impeccable`;
const UI_FILE = /\.(css|scss|sass|less|html|vue|svelte|astro|tsx|jsx)$/i;
const SKIP_PATH = /(node_modules|dist|build|vendor)\//;

type Finding = {
	antipattern: string;
	name: string;
	description: string;
	file: string;
	line: number;
};

export default function uiUxGuard(pi: ExtensionAPI) {
	// shortcut: dedupe lives in this process only, so a finding suppressed at line 12 stays
	// quiet if the same rule/line comes back later in the same session. Restart pi to re-report.
	const reported = new Set<string>();

	pi.on("tool_result", async (event, ctx) => {
		if (process.env.IMPECCABLE_HOOK_DISABLED) return;
		if (event.toolName !== "edit" && event.toolName !== "write") return;
		if (event.isError) return;

		const target = event.input.path;
		if (typeof target !== "string" || !UI_FILE.test(target) || SKIP_PATH.test(target)) return;
		const file = resolve(ctx.cwd, target);

		let findings: Finding[];
		try {
			const { code, stdout } = await pi.exec(DETECTOR, ["detect", "--json", file], {
				cwd: ctx.cwd,
				timeout: 30_000,
			});
			// impeccable exit codes: 0 = clean, 1 = scanner error, 2 = findings reported.
			// Only a scanner failure is a reason to stay quiet; findings are the point.
			if (code > 2) return;
			findings = JSON.parse(stdout || "[]") as Finding[];
		} catch {
			return; // a broken or missing scanner must never eat a good edit
		}

		const fresh = findings.filter((f) => {
			const key = `${f.file}:${f.antipattern}:${f.line}`;
			if (reported.has(key)) return false;
			reported.add(key);
			return true;
		});
		ev({ c: "ui-ux-guard", findings: fresh.length });
		if (fresh.length === 0) return;

		const shown = fresh.slice(0, 5);
		const lines = shown.map((f) => `- ${f.antipattern} (line ${f.line}) ${f.name}: ${f.description}`);
		if (fresh.length > shown.length) lines.push(`- +${fresh.length - shown.length} more: run impeccable detect for the full list`);

		return {
			content: [
				...event.content,
				{
					type: "text" as const,
					text: `impeccable detector — ${fresh.length} finding(s) in ${file}\n${lines.join("\n")}\nFix each one, or dismiss it with a stated reason (see the ui-ux-work skill).`,
				},
			],
		};
	});
}
