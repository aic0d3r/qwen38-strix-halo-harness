// run: bun test tests/   (tool-trim core: short output untouched, head and tail always kept, noise collapsed)
import { expect, test } from "bun:test";
import { BOUNDED, clean, LIMITS, trim } from "../extensions/tool-trim/core.ts";

const lines = (n: number) => Array.from({ length: n }, (_, i) => `line ${i}`).join("\n");

test("short output is returned unchanged", () => {
	const r = trim(lines(50), "/tmp/x");
	expect(r.cut).toBe(false);
	expect(r.text).toBe(lines(50));
});

test("long output keeps the first and last lines and points at the saved original", () => {
	const r = trim(lines(1000), "/tmp/saved.txt");
	expect(r.cut).toBe(true);
	const out = r.text.split("\n");
	expect(out[0]).toBe("line 0");
	expect(out[LIMITS.head - 1]).toBe(`line ${LIMITS.head - 1}`);
	expect(out.at(-1)).toBe("line 999");
	expect(r.text).toContain("/tmp/saved.txt");
	expect(r.text).toContain(`${1000 - LIMITS.head - LIMITS.tail} middle lines omitted`);
});

test("ansi colours, progress frames and repeated lines are collapsed", () => {
	expect(clean("\x1b[31mred\x1b[0m")).toBe("red");
	expect(clean("10%\r50%\r100%")).toBe("100%");
	expect(clean("a\na\na\na\nb")).toBe("a\n[previous line repeated 3 more times]\nb");
	expect(clean("a\na\nb")).toBe("a\na\nb");
});

test("one huge line is capped, not dropped", () => {
	const r = trim("x".repeat(5000), "/tmp/x");
	expect(r.text.length).toBeLessThan(2200);
	expect(r.text).toContain("line cut, 5000 chars");
});

test("commands that bound their own output are skipped", () => {
	expect(BOUNDED.test("cat big.log | tail -50")).toBe(true);
	expect(BOUNDED.test("go test ./...")).toBe(false);
});
