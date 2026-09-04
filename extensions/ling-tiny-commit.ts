/**
 * Ling-3.0-tiny commit-message extension
 *
 * Adds /commit: stages nothing, generates the commit message from the
 * working-tree diff with Ling-3.0-tiny (fast, prefill-shaped), then commits.
 * Usage in pi: /commit [optional extra instructions]
 */

import { uuidv7 } from "@earendil-works/pi-ai";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const TINY_PROVIDER = "llamacpp-tiny";
const TINY_MODEL = "ling3.0-tiny";

export default function (pi: ExtensionAPI) {
	pi.registerCommand("commit", {
		description: "Generate commit message with tiny from the working-tree diff and commit",
		handler: async (args, ctx) => {
			const extra = (args ?? "").trim();

			const model = ctx.modelRegistry.find(TINY_PROVIDER, TINY_MODEL);
			if (!model) {
				ctx.ui.notify(`[${TINY_MODEL}] provider not found; aborting /commit`, "warning");
				return;
			}

			// must be a git repo first
			const { code: repoCode } = await pi.exec("git", ["rev-parse", "--is-inside-work-tree"]);
			if (repoCode !== 0) {
				ctx.ui.notify("Not a git repository. Run: git init && git add -A && git commit -m init", "warning");
				return;
			}

			ctx.ui.notify("Collecting diff...", "info");
			const { code: dCode, stdout: diff, stderr: dErr } = await pi.exec("git", ["diff", "HEAD"]);
			const { stdout: staged } = await pi.exec("git", ["diff", "--cached", "--stat"]);
			const { stdout: untracked } = await pi.exec("git", ["ls-files", "--others", "--exclude-standard"]);

			let untrackedBlock = "";
			if (untracked.trim()) {
				const heads: string[] = [];
				for (const f of untracked.split("\n").filter(Boolean).slice(0, 10)) {
					const { stdout: h } = await pi.exec("head", ["-c", "800", f]);
					if (h.trim()) heads.push(`--- NEW FILE ${f} (head) ---\n${h}`);
				}
				untrackedBlock = `\n--- untracked ---\n${untracked}\n${heads.join("\n")}`;
			}
			const fullDiff = [diff, staged ? `\n--- staged stat ---\n${staged}` : "", untrackedBlock].join("\n").slice(0, 60000);

			if (!fullDiff.trim()) {
				ctx.ui.notify("Nothing to commit: no diff, staged changes, or untracked files.", "warning");
				return;
			}

			ctx.ui.notify(`Generating commit message with ${TINY_MODEL}...`, "info");
			const summaryMessages = [
				{
					role: "user" as const,
					content: [
						{
							type: "text" as const,
							text: `Write a git commit message for the working-tree changes below. Rules: a single subject line under 72 chars in the imperative mood ("Add X", "Fix Y"), then a blank line, then a short body (2-5 lines) explaining what and why if the change is non-trivial. Plain text, no markdown, no signature. ${extra ? `Extra instructions from the user: ${extra}` : ""}

<changes>
${fullDiff}
</changes>`,
						},
					],
					timestamp: Date.now(),
				},
			];

			try {
				const response = await ctx.modelRegistry.complete(
					model,
					{ messages: summaryMessages },
					{
						maxTokens: 512,
						cacheRetention: "none",
						sessionId: uuidv7(),
					},
				);

				const commitMessage = response.content
					.filter((c): c is { type: "text"; text: string } => c.type === "text")
					.map((c) => c.text)
					.join("\n")
					.trim()
					.replace(/^```[a-z]*\n?|```$/gm, "")
					.trim();

				if (!commitMessage) {
					ctx.ui.notify("Empty commit message generated; aborting.", "warning");
					return;
				}

				const { code: aCode, stdout: aOut, stderr: aErr } = await pi.exec("git", ["add", "-A"]);
				if (aCode !== 0) {
					ctx.ui.notify(`git add failed: ${aErr}`, "error");
					return;
				}
				const { code: cCode, stdout: cOut, stderr: cErr } = await pi.exec("git", ["commit", "-m", commitMessage]);
				if (cCode !== 0) {
					ctx.ui.notify(`git commit failed: ${cErr}`, "error");
					return;
				}
				ctx.ui.notify(`Committed: ${commitMessage.split("\n")[0]}`, "info");
			} catch (error) {
				const message = error instanceof Error ? error.message : String(error);
				ctx.ui.notify(`/commit failed: ${message}`, "error");
			}
		},
	});
}
