// run: bun test tests/   (imports the exported pure helper; needs pi's node_modules resolvable)
import { expect, mock, test } from "bun:test";

// pi is not installed in the repo: stub it so the helper can load and log to /tmp
mock.module("@earendil-works/pi-coding-agent", () => ({ getAgentDir: () => "/tmp" }));
process.env.PI_MAX_HISTORY_IMAGES = "1"; // read once at module load, so set it before the import
const { pruneImages, default: imagePrune } = await import("../extensions/image-prune.ts");

const img = (data: string) => ({ content: [{ type: "image", data }] });

test("keeps the newest images, stubs the older ones", () => {
	const r = pruneImages([img("a"), img("b"), img("c")], 2);
	expect(r.dropped).toBe(1);
	expect(r.messages[0].content[0].type).toBe("text");
	expect(r.messages[1].content[0]).toEqual({ type: "image", data: "b" });
	expect(r.messages[2].content[0]).toEqual({ type: "image", data: "c" });
});

test("drops a re-read of the same image, keeps the newest copy", () => {
	const r = pruneImages([img("same"), img("same")], 20);
	expect(r.dropped).toBe(1);
	expect(r.messages[0].content[0].type).toBe("text");
	expect(r.messages[1].content[0].type).toBe("image");
});

test("under the cap nothing changes", () => {
	const msgs = [img("a"), img("b")];
	const r = pruneImages(msgs, 20);
	expect(r.changed).toBe(false);
	expect(r.messages[0]).toBe(msgs[0]);
});

test("text-only transcripts and mixed content are left alone", () => {
	const msgs = [{ content: "plain string" }, { content: [{ type: "text", text: "hi" }] }, { content: [{ type: "text", text: "t" }, { type: "image", data: "a" }] }];
	const r = pruneImages(msgs, 1);
	expect(r.dropped).toBe(0);
	expect(r.messages[0]).toBe(msgs[0]);
	expect(r.messages[2].content[1].type).toBe("image");
});

test("the context hook logs the prune, and returns nothing when nothing changed", async () => {
	const { readFileSync, rmSync } = await import("node:fs");
	rmSync("/tmp/harness-events.jsonl", { force: true });
	let handler: any;
	imagePrune({ on: (_e: string, h: any) => { handler = h; } } as any);
	expect(await handler({ messages: [img("a")] })).toBeUndefined(); // one image, cap 1: untouched
	expect(await handler({ messages: [img("a"), img("b")] })).toMatchObject({ messages: expect.any(Array) });
	expect(JSON.parse(readFileSync("/tmp/harness-events.jsonl", "utf8").trim())).toMatchObject({ c: "image-prune", kept: 1, dropped: 1 });
});
