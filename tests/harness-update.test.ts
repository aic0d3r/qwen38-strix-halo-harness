// run: bun test tests/   (temp origin + clone; no network, no real harness touched)
import { expect, mock, test } from "bun:test";
import { execFileSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "hu-"));
const agent = path.join(tmp, "agent");
const origin = path.join(tmp, "origin.git");
const work = path.join(tmp, "work");
const clone = path.join(tmp, "clone");
fs.mkdirSync(agent);
const sh = (cwd: string, ...a: string[]) => execFileSync("git", a, { cwd, stdio: "pipe" }).toString();
const commit = (msg: string) => {
	fs.writeFileSync(path.join(work, "f"), msg);
	sh(work, "add", "-A");
	sh(work, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", msg);
	sh(work, "push", "-q", "origin", "HEAD");
};
sh(tmp, "init", "-q", "--bare", "-b", "main", origin);
sh(tmp, "clone", "-q", origin, work);
fs.writeFileSync(path.join(work, "setup.sh"), "#!/bin/sh\ntouch \"$(dirname \"$0\")/setup-ran\"\n");
commit("one");
sh(tmp, "clone", "-q", origin, clone);
fs.writeFileSync(path.join(agent, "harness-repo"), clone + "\n");

mock.module("@earendil-works/pi-coding-agent", () => ({ getAgentDir: () => agent }));
const { default: ext } = await import("../extensions/harness-update.ts");

const handlers: Record<string, any> = {};
const cmds: Record<string, any> = {};
ext({ on: (n: string, h: any) => (handlers[n] = h), registerCommand: (n: string, o: any) => (cmds[n] = o.handler) } as any);
const notes: string[] = [];
const ctx = { ui: { notify: (m: string) => notes.push(m) } };
const stamp = path.join(agent, "harness-update-check");

test("quiet when up to date, writes stamp", async () => {
	await handlers.session_start({}, ctx);
	expect(notes).toEqual([]);
	expect(fs.existsSync(stamp)).toBe(true);
});

test("notice when behind (stamp stale), no pull", async () => {
	commit("two");
	fs.utimesSync(stamp, 0, 0);
	await handlers.session_start({}, ctx);
	expect(notes.at(-1)).toContain("1 commit behind");
	expect(fs.existsSync(path.join(clone, "setup-ran"))).toBe(false);
});

test("fresh stamp skips the check", async () => {
	notes.length = 0;
	await handlers.session_start({}, ctx);
	expect(notes).toEqual([]);
});

test("dirty tree refuses", async () => {
	fs.writeFileSync(path.join(clone, "dirty"), "x");
	await cmds["harness-update"]("", ctx);
	expect(notes.at(-1)).toContain("uncommitted");
	fs.rmSync(path.join(clone, "dirty"));
});

test("command pulls ff-only and runs setup.sh", async () => {
	await cmds["harness-update"]("", ctx);
	expect(notes.at(-1)).toContain("pulled 1 commit");
	expect(fs.readFileSync(path.join(clone, "f"), "utf8")).toBe("two");
	expect(fs.existsSync(path.join(clone, "setup-ran"))).toBe(true);
	fs.rmSync(path.join(clone, "setup-ran"));
	await cmds["harness-update"]("", ctx);
	expect(notes.at(-1)).toContain("up to date");
});

test("HARNESS_AUTOUPDATE=1 applies at startup", async () => {
	commit("three");
	fs.utimesSync(stamp, 0, 0);
	process.env.HARNESS_AUTOUPDATE = "1";
	await handlers.session_start({}, ctx);
	delete process.env.HARNESS_AUTOUPDATE;
	expect(notes.at(-1)).toContain("pulled 1 commit");
	expect(fs.readFileSync(path.join(clone, "f"), "utf8")).toBe("three");
});
