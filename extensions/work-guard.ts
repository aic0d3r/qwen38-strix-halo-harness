// work-guard: mechanical enforcement of three AGENTS.md rules that were skipped in session 01a13cd3:
//  1. source edits go through edit/write/serena, not bash (python open().write, sed -i, heredoc redirects)
//  2. ui-ux-work skill must be read before the first UI-file edit
//  3. committing UI changes with no screenshot viewed since the last UI edit shows the user a reminder (no block)
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
const UI_PATH = /\.(css|scss|sass|less|html|vue|svelte|astro|tsx|jsx)$|(^|\/)(gui|ui|views?|components?|pages?|widgets?|screens?)\//i;
const SKILL = /ui-ux-work\/SKILL\.md$/;
const IMAGE = /\.(png|jpe?g|webp|gif)$/i;

const TMPDIR = process.env.TMPDIR || "/tmp";
// heredoc bodies are data, not commands the agent is about to run
const stripHeredocs = (cmd: string) => cmd.replace(/<<-?['"]?(\w+)['"]?[\s\S]*?\n\1\b/g, "<<heredoc");

// exported for tests: does this bash command edit project source (as opposed to build/test/run)?
export const blocksBashEdit = (cmd: string, cwd = ""): boolean => {
	if (cwd.startsWith(TMPDIR) || cmd.includes(TMPDIR)) return false; // scratch work, wherever the session started
	const runsPython = /^\s*python3?\b/.test(cmd) || /(^|\||&&|;)\s*python3?\s/.test(cmd);
	// text quoted in a sed pattern or a heredoc body is data, not an edit: only python actually being run may match that rule
	const probe = runsPython ? cmd : stripHeredocs(cmd);
	return BASH_EDIT.some((r, i) => (i === 2 ? runsPython : true) && r.test(probe));
};

export default function workGuard(pi: ExtensionAPI) {
	let skillRead = false;
	let uiDirty = false;
	let sawImage = false; // any render viewed this session, from any tool

	// pi attaches the image itself to the result, so codemode image(), playwright screenshots and
	// any other image-returning tool count as a render too, not just read on a *.png path.
	// One-shot per commit: warn once per UI-edit burst, otherwise refinement loops nag on every commit.
	pi.on("tool_result", async (event: any) => {
		if (process.env.WORK_GUARD_DISABLED) return;
		if ((event.content ?? []).some((b: any) => b?.type === "image")) { sawImage = true; uiDirty = false; }
	});

	pi.on("tool_call", async (event: any, ctx: any) => {
		if (process.env.WORK_GUARD_DISABLED) return;
		const { toolName, input } = event;
		const p = typeof input?.path === "string" ? input.path : "";

		if (toolName === "read") {
			if (SKILL.test(p)) skillRead = true;
			if (IMAGE.test(p)) { uiDirty = false; sawImage = true; }
			return;
		}
		if (toolName === "bash" && typeof input?.command === "string") {
			if (blocksBashEdit(input.command, ctx.cwd ?? "")) {
				ev({ c: "work-guard", k: "bash-edit" });
				return { block: true, reason: "Don't edit source via bash (python/sed/redirect). Use the edit tool, or Serena (replace_symbol_body, insert_after_symbol, rename_symbol) for symbol work. Bash is for build/test/run. If this wasn't a source edit, rephrase the command." };
			}
			return;
		}
		if ((toolName === "edit" || toolName === "write") && UI_PATH.test(p)) {
			if (!skillRead) {
				ev({ c: "work-guard", k: "skill-first" });
				return { block: true, reason: "UI change: read ~/.pi/agent/skills/ui-ux-work/SKILL.md first (AGENTS.md MUST), then retry this edit." };
			}
			uiDirty = true;
		}
		if (toolName === "commit" && uiDirty && !sawImage) {
			ev({ c: "work-guard", k: "no-render" });
			ctx.ui?.notify("UI files changed this session and no render was viewed: committing unseen. If a render is impossible, say so.", "warning");
		}
	});
}
