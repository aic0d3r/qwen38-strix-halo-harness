// work-guard: mechanical enforcement of three AGENTS.md rules that were skipped in session 01a13cd3:
//  1. source edits go through edit/write/serena, not bash (python open().write, sed -i, heredoc redirects)
//  2. ui-ux-work skill must be read before the first UI-file edit
//  3. committing UI changes with no screenshot viewed since the last UI edit shows the user a reminder (no block)
//  4. with serena configured, source edits in the project wait for mcp__serena__activate_project
// Off switch: WORK_GUARD_DISABLED=1.
import * as fs from "node:fs";
import * as path from "node:path";
import { type ExtensionAPI, getAgentDir } from "@earendil-works/pi-coding-agent";
// same event log as the other extensions; scripts/usage-stats.py counts it
const ev = (o: object) => { try { fs.appendFileSync(path.join(getAgentDir(), "harness-events.jsonl"), JSON.stringify({ t: new Date().toISOString(), cwd: process.cwd(), ...o }) + "\n"); } catch {} };

const SRC = "(go|py|ts|tsx|js|jsx|mjs|rs|c|h|cpp|java|kt|swift|rb|php|css|scss|html|vue|svelte|astro|lua|sh)";
// shortcut: regex heuristics on the command string; a determined agent can dodge them, the goal is stopping habit.
const BASH_EDIT = [
	/\bsed\s+(-[a-zA-Z]*i|--in-place)/,
	/\bperl\s+-[a-zA-Z]*p[a-zA-Z]*i|\bperl\s+-[a-zA-Z]*i/,
	/\bpython3?\b[\s\S]*(\.write\(|write_text\(|open\([^)]*['"][wa]\+?['"])/,
	new RegExp(`(>>?|\\btee\\s+(-a\\s+)?)\\s*["']?(?!/tmp/|/dev/)[^\\s"'|;&]*\\.${SRC}\\b`),
];
const SRC_FILE = new RegExp(`\\.${SRC}$`);
const UI_PATH = /\.(css|scss|sass|less|html|vue|svelte|astro|tsx|jsx)$|(^|\/)(gui|ui|views?|components?|pages?|widgets?|screens?)\//i;
const SKILL = /ui-ux-work\/SKILL\.md$/;
const IMAGE = /\.(png|jpe?g|webp|gif)$/i;

export default function workGuard(pi: ExtensionAPI) {
	let skillRead = false;
	let serenaActive = false;
	const serenaConfigured = (() => { try { return "serena" in JSON.parse(fs.readFileSync(path.join(getAgentDir(), "mcp.json"), "utf8")).mcpServers; } catch { return false; } })();
	let uiDirty = false;

	pi.on("tool_call", async (event: any, ctx: any) => {
		if (process.env.WORK_GUARD_DISABLED) return;
		const { toolName, input } = event;
		const p = typeof input?.path === "string" ? input.path : "";

		if (toolName === "mcp__serena__activate_project" || (toolName === "codemode" && /activate_project/.test(String(input?.code)))) serenaActive = true;
		if (toolName === "read") {
			if (SKILL.test(p)) skillRead = true;
			if (IMAGE.test(p) && !p.includes("/.pi/")) { uiDirty = false; }
			return;
		}
		if (toolName === "bash" && typeof input?.command === "string") {
			if (BASH_EDIT.some((r) => r.test(input.command))) {
				ev({ c: "work-guard", k: "bash-edit" });
				return { block: true, reason: "Don't edit source via bash (python/sed/redirect). Use the edit tool, or Serena (replace_symbol_body, insert_after_symbol, rename_symbol) for symbol work. Bash is for build/test/run. If this wasn't a source edit, rephrase the command." };
			}
			return;
		}
		// source edits inside the project need the Serena project active first (symbol tools, diagnostics)
		if ((toolName === "edit" || toolName === "write") && serenaConfigured && !serenaActive && pi.getActiveTools().includes("mcp__serena__activate_project") && SRC_FILE.test(p) && path.resolve(ctx.cwd, p).startsWith(ctx.cwd + path.sep)) {
			ev({ c: "work-guard", k: "serena-first" });
			return { block: true, reason: "Activate the Serena project before editing source: call mcp__serena__activate_project (cwd), then retry. Prefer Serena symbol tools for symbol edits." };
		}
		if ((toolName === "edit" || toolName === "write") && UI_PATH.test(p)) {
			if (!skillRead) {
				ev({ c: "work-guard", k: "skill-first" });
				return { block: true, reason: "UI change: read ~/.pi/agent/skills/ui-ux-work/SKILL.md first (AGENTS.md MUST), then retry this edit." };
			}
			uiDirty = true;
		}
		if (toolName === "commit" && uiDirty) {
			ev({ c: "work-guard", k: "no-render" });
			ctx.ui?.notify("UI files changed and no screenshot was viewed since: committing without a render.", "warning");
		}
	});
}
