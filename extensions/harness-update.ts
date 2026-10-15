/**
 * harness-update: once-a-day check whether the harness repo is behind origin, and
 * `/harness-update` to apply it. Never auto-applies: notice only; the command is
 * fast-forward-only and refuses on a dirty tree. Repo path comes from the
 * harness-repo marker that setup.sh writes.
 */
import { execFile } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import { promisify } from "node:util";
import { type ExtensionAPI, getAgentDir } from "@earendil-works/pi-coding-agent";

const run = promisify(execFile);
const AGENT_DIR = getAgentDir();
const STAMP = path.join(AGENT_DIR, "harness-update-check");
const DAY = 24 * 3600 * 1000;

const git = (repo: string, ...a: string[]) => run("git", ["-C", repo, ...a], { timeout: 5000 }).then((r) => r.stdout.trim());
const repoPath = () => {
	try { return fs.readFileSync(path.join(AGENT_DIR, "harness-repo"), "utf8").trim() || null; } catch { return null; }
};
// commits on the upstream branch we don't have yet
const behind = (repo: string) => git(repo, "rev-list", "--count", "HEAD..@{u}").then(Number);

// ff-only pull + setup.sh; throws on a dirty tree. n = commits behind.
const apply = async (repo: string, n: number) => {
	if (await git(repo, "status", "--porcelain")) throw new Error(`${repo} has uncommitted changes: commit or stash, then retry`);
	await run("git", ["-C", repo, "pull", "--ff-only", "--quiet"], { timeout: 60000 });
	await run("bash", [path.join(repo, "setup.sh")], { timeout: 300000 });
	return `pulled ${n} commit${n > 1 ? "s" : ""} and re-ran setup.sh. Restart pi to load changed extensions.`;
};
const fail = (e: any) => `harness-update failed: ${String(e?.stderr || e?.message || e).slice(0, 300)}`;

export default function (pi: ExtensionAPI) {
	pi.on("session_start", async (_e, ctx) => {
		const repo = repoPath();
		if (!repo) return;
		try {
			if (Date.now() - fs.statSync(STAMP).mtimeMs < DAY) return;
		} catch { /* no stamp yet */ }
		try {
			await git(repo, "fetch", "--quiet");
			fs.writeFileSync(STAMP, "");
			const n = await behind(repo);
			if (n <= 0) return;
			// opt-in only: HARNESS_AUTOUPDATE=1 applies instead of just notifying
			if (process.env.HARNESS_AUTOUPDATE !== "1") return ctx.ui.notify(`harness is ${n} commit${n > 1 ? "s" : ""} behind origin: run /harness-update`, "info");
			try { ctx.ui.notify(await apply(repo, n), "info"); } catch (e) { ctx.ui.notify(fail(e), "error"); }
		} catch { /* offline, no upstream, not a repo: stay silent, retry next start */ }
	});

	pi.registerCommand("harness-update", {
		description: "Fast-forward the harness repo from origin and re-run setup.sh",
		handler: async (_args, ctx) => {
			const repo = repoPath();
			if (!repo) return ctx.ui.notify("no harness-repo marker: run ./setup.sh once from the repo", "error");
			try {
				await git(repo, "fetch", "--quiet");
				const n = await behind(repo);
				ctx.ui.notify(n === 0 ? "harness already up to date" : await apply(repo, n), "info");
			} catch (e) {
				ctx.ui.notify(fail(e), "error");
			}
		},
	});
}
