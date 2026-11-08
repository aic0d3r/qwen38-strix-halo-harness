// run: bun test tests/   (checker selection and JSON validation; the subprocess path is exercised by pi itself)
import { expect, mock, test } from "bun:test";

mock.module("@earendil-works/pi-coding-agent", () => ({ getAgentDir: () => "/tmp" }));
const { checkCommand, jsonError } = await import("../extensions/syntax-guard.ts");

test("picks a checker by extension and skips vendored paths", () => {
	expect(checkCommand("/p/a.py")?.[0]).toBe("python3");
	expect(checkCommand("/p/a.sh")).toEqual(["bash", "-n", "/p/a.sh"]);
	expect(checkCommand("/p/a.tsx")?.[0]).toBe("bun");
	expect(checkCommand("/p/a.md")).toBeUndefined();
	expect(checkCommand("/p/node_modules/x/a.js")).toBeUndefined();
	expect(checkCommand("/p/redist/a.py")?.[0]).toBe("python3"); // not a vendored dir, only a name ending in one
	expect(checkCommand("/work/build/proj/a.py", "/work/build/proj")?.[0]).toBe("python3"); // parents of the cwd do not count
	expect(checkCommand("/work/proj/build/a.py", "/work/proj")).toBeUndefined();
});

test("json errors are reported, valid json is clean", () => {
	expect(jsonError('{"a":1}')).toBe("");
	expect(jsonError('{"a":')).not.toBe("");
});

test("checkers really flag broken files", async () => {
	const dir = (await import("node:fs")).mkdtempSync("/tmp/sg-");
	const fs = await import("node:fs");
	const bad: Record<string, string> = { "b.py": "def (:\n", "b.sh": "if then\n", "b.ts": "const x: number = ;\n" };
	for (const [name, body] of Object.entries(bad)) {
		fs.writeFileSync(`${dir}/${name}`, body);
		const cmd = checkCommand(`${dir}/${name}`)!;
		expect(Bun.spawnSync(cmd).exitCode).not.toBe(0);
	}
	fs.writeFileSync(`${dir}/ok.py`, "x = 1\n");
	expect(Bun.spawnSync(checkCommand(`${dir}/ok.py`)!).exitCode).toBe(0);
});
