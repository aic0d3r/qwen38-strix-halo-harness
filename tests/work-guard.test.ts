// run: bun test tests/   (guards bash from editing project source; scratch dirs and data text must pass)
import { expect, mock, test } from "bun:test";

mock.module("@earendil-works/pi-coding-agent", () => ({ getAgentDir: () => "/tmp" }));
const { blocksBashEdit } = await import("../extensions/work-guard.ts");

const PROJ = "/home/me/proj";

test("blocks source edits routed through bash", () => {
	expect(blocksBashEdit("sed -i s/a/b/ x.go", PROJ)).toBe(true);
	expect(blocksBashEdit("perl -pi -e 's/a/b/' x.go", PROJ)).toBe(true);
	expect(blocksBashEdit("cat > a.go <<'EOF'\npackage x\nEOF", PROJ)).toBe(true);
	expect(blocksBashEdit("echo x >> src/main.ts", PROJ)).toBe(true);
	expect(blocksBashEdit("tee out.py", PROJ)).toBe(true);
	expect(blocksBashEdit("python3 - <<'PY'\nopen('a.go','w').write(s)\nPY", PROJ)).toBe(true);
});

test("lets scratch work, reads and build/test commands through", () => {
	expect(blocksBashEdit("cd /tmp; python3 - <<'PY'\nopen('p.py','w').write(s)\nPY", PROJ)).toBe(false);
	expect(blocksBashEdit("cd /tmp && sed -i s/a/b/ scratch.py", PROJ)).toBe(false);
	expect(blocksBashEdit("sed -i s/a/b/ x.go", "/tmp")).toBe(false);
	expect(blocksBashEdit("cmd 2>&1 | tail -3", PROJ)).toBe(false);
	expect(blocksBashEdit("node -e 'if (s.length > 72) { throw 1 }' 2>&1 | tail -5", PROJ)).toBe(false);
	expect(blocksBashEdit("curl -s -d '{\"a\":1}' -o /dev/null url/v1/x", PROJ)).toBe(false);
	expect(blocksBashEdit("timeout 12 bash ~/x/scripts/halogen-top.sh 2 2>&1 | tail -3", PROJ)).toBe(false);
	expect(blocksBashEdit("go test ./...", PROJ)).toBe(false);
	expect(blocksBashEdit("sed -n 1,5p a.go", PROJ)).toBe(false);
});
