// run: bun test tests/   (imports the exported pure helpers; needs pi's node_modules resolvable)
import { expect, mock, test } from "bun:test";

// pi is not installed in the repo: stub it so the pure helpers can load
mock.module("@earendil-works/pi-coding-agent", () => ({ getAgentDir: () => "/tmp" }));
const { isDirectCommit, tidyMessage } = await import("../extensions/ling-tiny-commit.ts");

test("isDirectCommit", () => {
	expect(isDirectCommit("git commit -m x")).toBe(true);
	expect(isDirectCommit("git -c a=b commit -am x")).toBe(true);
	expect(isDirectCommit("git log -- src/commit.ts")).toBe(false);
	expect(isDirectCommit("echo 'git commit'")).toBe(false);
	expect(isDirectCommit("cat <<EOF\ngit commit\nEOF")).toBe(false);
	expect(isDirectCommit("git commit --amend --no-edit")).toBe(false);
});

test("tidyMessage splits long subject", () => {
	const m = tidyMessage("Fix thing. " + "word ".repeat(30));
	expect(m.split("\n")[0].length).toBeLessThanOrEqual(72);
});
