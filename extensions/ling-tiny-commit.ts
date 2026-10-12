/**
 * Ling-3.0-tiny commit-message extension
 *
 * Adds /commit: stages nothing, generates the commit message from the
 * working-tree diff with Ling-3.0-tiny (fast, prefill-shaped), then commits.
 * Usage in pi: /commit [optional extra instructions]
 */

import * as fs from "node:fs";
import * as path from "node:path";
import { type ExtensionAPI, getAgentDir } from "@earendil-works/pi-coding-agent";

const TINY_MODEL = "ling3.0-tiny";
// one JSON line per sidecar/NPU call; summarized by scripts/session-audit.sh --harness
const ev = (o: object) => { try { fs.appendFileSync(path.join(getAgentDir(), "harness-events.jsonl"), JSON.stringify({ t: new Date().toISOString(), cwd: process.cwd(), ...o }) + "\n"); } catch {} };
// crash-recovery checkpoint (progress-tracker) must never ride along in a commit
const PROGRESS = (process.env.PI_PROGRESS_FILE || "PROGRESS.md").split("/").pop()!;

export default function (pi: ExtensionAPI) {
	// returns the outcome so the tool caller (the model) sees failures, not just the UI
	const runCommit = async (extra: string, ctx: any): Promise<string> => {
		const notify = (msg: string, level?: string) => {
			try { ctx?.ui?.notify?.(msg, level); } catch { console.log(`[commit] ${msg}`); }
			return msg;
		};

		const { code: repoCode } = await pi.exec("git", ["rev-parse", "--is-inside-work-tree"]);
		if (repoCode !== 0) return notify("Not a git repository. Run: git init && git add -A && git commit -m init", "warning");

		notify("Collecting diff...", "info");
		// prefill dominates sidecar latency (~3.5k tok/s): stat first so truncation never hides a file,
		// -U1 trims context lines, 24k cap. Measured 10-12: worst case 7.2s -> 2.9s.
		const [{ stdout: stat }, { stdout: diff }, { stdout: untracked }] = await Promise.all([
			pi.exec("git", ["diff", "HEAD", "--stat"]),
			pi.exec("git", ["diff", "-U1", "HEAD"]),
			pi.exec("git", ["ls-files", "--others", "--exclude-standard"]),
		]);

		let untrackedBlock = "";
		if (untracked.trim()) {
			const files = untracked.split("\n").filter(Boolean).slice(0, 10);
			const heads = (await Promise.all(files.map((f) => pi.exec("head", ["-c", "800", "--", f]))))
				.map(({ stdout: h }, i) => (h.trim() ? `--- NEW FILE ${files[i]} (head) ---\n${h}` : ""))
				.filter(Boolean);
			untrackedBlock = `\n--- untracked ---\n${untracked}\n${heads.join("\n")}`;
		}
		const fullDiff = [stat, untrackedBlock, diff].join("\n").slice(0, 24000);
		if (!fullDiff.trim()) return notify("Nothing to commit: no diff, staged changes, or untracked files.", "warning");

		notify(`Generating commit message with ${TINY_MODEL}...`, "info");
		const prompt = `Write a git commit message for the working-tree changes below. Rules: a single subject line under 72 chars in the imperative mood ("Add X", "Fix Y"), then a blank line, then a short body (2-5 lines) explaining what and why if the change is non-trivial. Plain text, no markdown, no signature. ${extra ? `Extra instructions from the user: ${extra}` : ""}

<changes>
${fullDiff}
</changes>`;

		const t0 = Date.now();
		try {
			// direct sidecar call: the tool ctx has no modelRegistry, and the
			// sidecar runs with --jinja so the server applies the chat template
			const tinyRes = await fetch("http://127.0.0.1:8090/v1/chat/completions", {
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
			const commitMessage = String(tinyJson?.choices?.[0]?.message?.content ?? "")
				.trim()
				.replace(/^```[a-z]*\n?|```$/gm, "")
				.trim();
			if (!commitMessage) return notify("Empty commit message generated; aborting.", "warning");

			// exclude pathspecs that name a gitignored path make `git add` exit 1, so only exclude un-ignored ones
			const excludes: string[] = [];
			for (const p of [PROGRESS, ".progress-log"]) {
				const { code } = await pi.exec("git", ["check-ignore", "-q", p]);
				if (code !== 0) excludes.push(`:(exclude)${p}`);
			}
			const { code: aCode, stderr: aErr } = await pi.exec("git", ["add", "-A", "--", ".", ...excludes]);
			if (aCode !== 0) return notify(`git add failed: ${aErr}`, "error");
			const { code: cCode, stdout: cOut, stderr: cErr } = await pi.exec("git", ["commit", "-m", commitMessage]);
			if (cCode !== 0) return notify(`git commit failed: ${cErr || cOut}`, "error");
			return notify(`Committed: ${commitMessage.split("\n")[0]}`, "info");
		} catch (error) {
			const message = error instanceof Error ? error.message : String(error);
			ev({ c: "commit", ok: false, ms: Date.now() - t0, why: message.slice(0, 120) });
			return notify(`commit failed: ${message}`, "error");
		}
	};

	pi.registerCommand("commit", {
		description: "Generate commit message with tiny from the working-tree diff and commit",
		handler: async (args: any, ctx: any) => { await runCommit((args ?? "").trim(), ctx); },
	});

	// enforce "tiny always writes the message": block direct git commits from tool
	// calls so the message can only come from runCommit. runCommit's own git call
	// goes through pi.exec, which tool_call hooks never see. --amend --no-edit /
	// -C HEAD are allowed: they reuse an existing message, so there is none to write.
	pi.on("tool_call", async (event: any) => {
		if (event?.toolName !== "bash") return;
		const cmd: string = String(event?.input?.command ?? "");
		// subcommand position only: `git log -- src/commit.ts` must not be blocked
		if (!/\bgit\s+(?:-[cC]\s+\S+\s+)*commit\b/.test(cmd)) return;
		if (/commit[^|;&]*--amend[^|;&]*(--no-edit|-C\s+HEAD)/.test(cmd)) return;
		return { block: true, reason: "Commits go through the commit tool (ling-tiny writes the message). Call the commit tool (or /commit) instead of git commit." };
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
			},
		},
		execute: async (_callId: string, args: { extra?: string }, ctx: any) => {
			const outcome = await runCommit((args?.extra ?? "").trim(), ctx);
			return { content: [{ type: "text", text: outcome }] };
		},
	});
}
