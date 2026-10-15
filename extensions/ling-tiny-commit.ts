/**
 * Ling-3.0-tiny commit-message extension
 *
 * Adds /commit: stages nothing, generates the commit message from the
 * working-tree diff with Ling-3.0-tiny (fast, prefill-shaped), then commits.
 * Usage in pi: /commit [optional extra instructions]
 */

import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { type ExtensionAPI, getAgentDir } from "@earendil-works/pi-coding-agent";

const TINY_MODEL = "ling3.0-tiny";
// one JSON line per sidecar/NPU call; summarized by scripts/session-audit.sh --harness
const ev = (o: object) => { try { fs.appendFileSync(path.join(getAgentDir(), "harness-events.jsonl"), JSON.stringify({ t: new Date().toISOString(), cwd: process.cwd(), ...o }) + "\n"); } catch {} };
// crash-recovery checkpoint (progress-tracker) must never ride along in a commit
const PROGRESS = (process.env.PI_PROGRESS_FILE || "PROGRESS.md").split("/").pop()!;

// shortcut: quoted text is ignored, so `bash -c "git commit"` slips through; upgrade to a shell parser if agents start doing that
const QUOTED = /<<-?\s*['"]?(\w+)['"]?[^\n]*\n[\s\S]*?\n\s*\1\b|'[^']*'|"(?:\\.|[^"\\])*"/g;
/** true when a bash command runs `git commit` itself (not when it merely mentions it in a string/heredoc) */
export const isDirectCommit = (cmd: string): boolean => {
	const bare = cmd.replace(QUOTED, "");
	// subcommand position only: `git log -- src/commit.ts` must not be blocked
	if (!/\bgit\s+(?:-[cC]\s+\S+\s+)*commit\b/.test(bare)) return false;
	// --amend --no-edit / -C HEAD reuse an existing message, so there is none to write
	return !/commit[^|;&]*--amend[^|;&]*(--no-edit|-C\s+HEAD)/.test(bare);
};

/** tiny sometimes returns one long line: split it into a <=72-char subject + body */
export const tidyMessage = (msg: string): string => {
	const [first, ...rest] = msg.trim().split("\n");
	let subject = first.trim(), body = rest.join("\n").trim();
	if (subject.length > 72) {
		// first sentence if it fits, else the last clause boundary (tiny writes "X, and Y, not Z"
		// run-ons; cutting at a space mid-clause leaves a subject that reads unfinished), else word break
		const head = subject.slice(0, 73);
		const cut =
			head.search(/[.!?]\s/) + 1 ||
			[Math.max(head.lastIndexOf(", and "), head.lastIndexOf(" and "), head.lastIndexOf(", not "), head.lastIndexOf("; ")) + 1] ||
			subject.lastIndexOf(" ", 72);
		if (cut > 0) {
			body = [subject.slice(cut).trim(), body].filter(Boolean).join("\n\n");
			subject = subject.slice(0, cut).trim();
		}
	}
	subject = subject.replace(/\.$/, "");
	return body ? `${subject}\n\n${body}` : subject;
};

const SIDECAR = "http://127.0.0.1:8090";

export default function (pi: ExtensionAPI) {
	// nothing-at-boot policy leaves the sidecar down after a reboot: start the unit on demand
	const ensureSidecar = async (notify: (m: string, l?: string) => string): Promise<boolean> => {
		const up = () => fetch(`${SIDECAR}/health`, { signal: AbortSignal.timeout(2000) }).then((r) => r.ok, () => false);
		if (await up()) return true;
		notify("Sidecar down, starting ling-tiny.service...", "info");
		if ((await pi.exec("systemctl", ["--user", "start", "ling-tiny.service"])).code !== 0) return false;
		for (let i = 0; i < 60; i++) { if (await up()) return true; await new Promise((r) => setTimeout(r, 500)); }
		return false;
	};

	const generate = async (prompt: string, t0: number): Promise<string> => {
		// direct sidecar call: the tool ctx has no modelRegistry, and the
		// sidecar runs with --jinja so the server applies the chat template
		const tinyRes = await fetch(`${SIDECAR}/v1/chat/completions`, {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({
				model: TINY_MODEL,
				max_tokens: 400,
				temperature: 0.2,
				// ling3.0-tiny thinks by default; the think pass ate budget and
				// 8.6x'd latency (measured 10-06: 3.10s+empty content vs 0.36s
				// with real content; retention canary 10/10 without thinking)
				chat_template_kwargs: { enable_thinking: false },
				messages: [{ role: "user", content: prompt }],
			}),
			signal: AbortSignal.timeout(180_000),
		});
		if (!tinyRes.ok) throw new Error(`sidecar HTTP ${tinyRes.status}`);
		const tinyJson: any = await tinyRes.json();
		ev({ c: "commit", ok: true, ms: Date.now() - t0, tokIn: tinyJson?.usage?.prompt_tokens, tokOut: tinyJson?.usage?.completion_tokens });
		return tidyMessage(String(tinyJson?.choices?.[0]?.message?.content ?? "").replace(/^```[a-z]*\n?|```$/gm, ""));
	};

	// returns the outcome so the tool caller (the model) sees failures, not just the UI
	const runCommit = async (extra: string, ctx: any, message = "", dir = ""): Promise<string> => {
		// interlinked sibling repos: commit somewhere other than the session cwd
		const cwd = dir ? path.resolve(dir.replace(/^~(?=\/|$)/, os.homedir())) : undefined;
		const git = (args: string[]) => pi.exec("git", args, { cwd });
		const notify = (msg: string, level?: string) => {
			try { ctx?.ui?.notify?.(msg, level); } catch { console.log(`[commit] ${msg}`); }
			return msg;
		};

		const { code: repoCode } = await git(["rev-parse", "--is-inside-work-tree"]);
		if (repoCode !== 0) return notify(`Not a git repository${cwd ? `: ${cwd}` : ""}. Run: git init && git add -A && git commit -m init`, "warning");

		notify("Collecting diff...", "info");
		// prefill dominates sidecar latency (~3.5k tok/s): stat first so truncation never hides a file,
		// -U1 trims context lines, 24k cap. Measured 10-12: worst case 7.2s -> 2.9s.
		const [{ stdout: stat }, { stdout: diff }, { stdout: untracked }] = await Promise.all([
			git(["diff", "HEAD", "--stat"]),
			git(["diff", "-U1", "HEAD"]),
			git(["ls-files", "--others", "--exclude-standard"]),
		]);

		let untrackedBlock = "";
		if (untracked.trim()) {
			const files = untracked.split("\n").filter(Boolean).slice(0, 10);
			const heads = (await Promise.all(files.map((f) => pi.exec("head", ["-c", "800", "--", f], { cwd }))))
				.map(({ stdout: h }, i) => (h.trim() ? `--- NEW FILE ${files[i]} (head) ---\n${h}` : ""))
				.filter(Boolean);
			untrackedBlock = `\n--- untracked ---\n${untracked}\n${heads.join("\n")}`;
		}
		const fullDiff = [stat, untrackedBlock, diff].join("\n").slice(0, 24000);
		if (!fullDiff.trim()) return notify("Nothing to commit: no diff, staged changes, or untracked files.", "warning");
		if (!message && !(await ensureSidecar(notify)))
			return notify("commit failed: ling-tiny sidecar is down and could not be started (journalctl --user -u ling-tiny). Retry with the `message` parameter to commit with your own message.", "error");

		if (!message) notify(`Generating commit message with ${TINY_MODEL}...`, "info");
		const prompt = `Write a git commit message for the working-tree changes below.
Subject: one verb then the component then the effect, a complete clause that fits in 72 chars (cut detail into the body, never mid-sentence), no filler words (never "the", "component", "code"). The first word must be one of the verbs below.
Verbs: Add (new behaviour, field, flag, file) | Fix (only if you can name what was broken) | Update (version pin, benchmark numbers, data) | Prevent | Stop (only when the commit makes something stop running; never "Stop" plus a gerund, deleted code is "Remove X") | Drop | Remove | Rename | Use (switched approach) | Document.
If the changes cover several unrelated things, the subject names the biggest one and the body lists the others; never invent a change the diff does not show.
Extra instructions, when present, state the change; the subject must say that.
Body: only if non-trivial, 2-4 plain lines, what and why, no markdown, no signature. ${extra ? `Extra instructions from the user: ${extra}` : ""}

<changes>
${fullDiff}
</changes>`;

		const t0 = Date.now();
		try {
			const commitMessage = message ? message.trim() : await generate(prompt, t0);
			if (!commitMessage) return notify("Empty commit message generated; aborting.", "warning");

			// exclude pathspecs that name a gitignored path make `git add` exit 1, so only exclude un-ignored ones
			const excludes: string[] = [];
			for (const p of [PROGRESS, ".progress-log"]) {
				const { code } = await git(["check-ignore", "-q", p]);
				if (code !== 0) excludes.push(`:(exclude)${p}`);
			}
			const { code: aCode, stderr: aErr } = await git(["add", "-A", "--", ".", ...excludes]);
			if (aCode !== 0) return notify(`git add failed: ${aErr}`, "error");
			const { code: cCode, stdout: cOut, stderr: cErr } = await git(["commit", "-m", commitMessage]);
			if (cCode !== 0) return notify(`git commit failed: ${cErr || cOut}`, "error");
			return notify(`Committed${cwd ? ` in ${cwd}` : ""}: ${commitMessage.split("\n")[0]}`, "info");
		} catch (error) {
			const why = error instanceof Error ? error.message : String(error);
			ev({ c: "commit", ok: false, ms: Date.now() - t0, why: why.slice(0, 120) });
			return notify(`commit failed: ${why}`, "error");
		}
	};

	pi.registerCommand("commit", {
		description: "Generate commit message with tiny from the working-tree diff and commit",
		handler: async (args: any, ctx: any) => { await runCommit((args ?? "").trim(), ctx); },
	});

	// enforce "tiny always writes the message": block direct git commits from tool
	// calls so the message can only come from runCommit. runCommit's own git call
	// goes through pi.exec, which tool_call hooks never see.
	pi.on("tool_call", async (event: any, ctx: any) => {
		// local halogen only: the sidecar offload is pointless for cloud models
		if ((ctx as any).model?.provider !== "halogen") return;
		if (event?.toolName !== "bash" || !isDirectCommit(String(event?.input?.command ?? ""))) return;
		return { block: true, reason: "Commits go through the commit tool (ling-tiny writes the message). Call the commit tool (or /commit) instead of git commit; pass `message` to use an exact message and `cwd` to commit another repo." };
	});

	// agent-invocable tool: agents reach for tools, not slash commands (3 /commit misses measured)
	pi.registerTool({
		name: "commit",
		description:
			"Commit ALL working-tree changes. Generates the commit message with the ling-tiny sidecar from the diff (offloads the main model), then runs git add -A && git commit. Prefer this over running git commit directly.",
		parameters: {
			type: "object",
			properties: {
				extra: { type: "string", description: "Optional extra instructions for the commit message" },
				message: { type: "string", description: "Exact commit message to use verbatim (skips the sidecar). Only when the user dictated the message." },
				cwd: { type: "string", description: "Repo directory to commit in (default: session cwd). Use for sibling repos instead of git -C." },
			},
		},
		execute: async (_callId: string, args: { extra?: string; message?: string; cwd?: string }, ctx: any) => {
			const outcome = await runCommit((args?.extra ?? "").trim(), ctx, args?.message ?? "", (args?.cwd ?? "").trim());
			return { content: [{ type: "text", text: outcome }] };
		},
	});
}
