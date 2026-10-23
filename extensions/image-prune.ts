// image-prune: every turn re-sends the whole transcript, so each screenshot the agent ever read
// stays in the request for the rest of the session - it keeps costing prefill on the local model
// and eventually a provider refuses the request (DeepInfra: 30 images per request). Keep the
// newest images in the request and swap older or duplicate ones for a text stub. The session file
// keeps every image: `context` is request-local and restored after, and re-reading a file adds one
// fresh image, not another permanent one. No-op for the default text-only models, where pi never
// puts an image in the request at all. Tune with PI_MAX_HISTORY_IMAGES (0 disables); 4 is
// enough for phone/browser automation, which only needs the last few screens. Savings are logged to harness-events.jsonl (c: "image-prune").
// shortcut: the cap is a total, so one message with more than 4 images keeps only its newest;
// raise the cap if a provider with a bigger image budget needs them.

import * as fs from "node:fs";
import * as path from "node:path";
import { type ExtensionAPI, getAgentDir } from "@earendil-works/pi-coding-agent";

const MAX_IMAGES = Number(process.env.PI_MAX_HISTORY_IMAGES ?? 4);
const STUB = "[image omitted from history: re-read the file if you need to see it again]";

type Block = { type?: string; data?: string; [key: string]: unknown };
type Message = { content?: unknown };

export function pruneImages<T extends Message>(messages: T[], max = MAX_IMAGES): { messages: T[]; changed: boolean; kept: number; dropped: number } {
	let kept = 0;
	let dropped = 0;
	const seen = new Set<string>();
	const out = [...messages];

	for (let i = messages.length - 1; i >= 0; i--) {
		const message = messages[i];
		if (!Array.isArray(message.content)) continue;
		let messageChanged = false;

		const pruned = (message.content as Block[]).map((block) => {
			if (block?.type !== "image") return block;
			const data = typeof block.data === "string" ? block.data : "";
			// Newest copy of an image wins; older copies and everything past the cap become text.
			if (kept >= max || (data !== "" && seen.has(data))) {
				messageChanged = true;
				dropped++;
				return { type: "text", text: STUB };
			}
			kept++;
			if (data !== "") seen.add(data);
			return block;
		});

		if (messageChanged) out[i] = { ...message, content: pruned };
	}

	return { messages: out, changed: dropped > 0, kept, dropped };
}

export default function imagePrune(pi: ExtensionAPI) {
	if (!(MAX_IMAGES > 0)) return;

	pi.on("context", async (event) => {
		const result = pruneImages(event.messages as Message[]);
		if (!result.changed) return;
		try {
			fs.appendFileSync(
				path.join(getAgentDir(), "harness-events.jsonl"),
				JSON.stringify({ t: new Date().toISOString(), cwd: process.cwd(), c: "image-prune", k: "request", kept: result.kept, dropped: result.dropped }) + "\n",
			);
		} catch {}
		return { messages: result.messages as typeof event.messages };
	});
}
